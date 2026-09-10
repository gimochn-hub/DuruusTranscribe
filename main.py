import os
import time
import uuid
import shutil
import asyncio
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

from app.config import (
    BASE_DIR, HOST, PORT, DEBUG, GROQ_API_KEY, GEMINI_API_KEY, FONTS_DIR
)
from app.modules.downloader import validate_youtube_url, download_youtube_audio, DownloadError
from app.modules.converter import convert_to_opus, check_ffmpeg, ConversionError
from app.modules.transcriber import transcribe_audio_file, TranscriberError
from app.modules.dalil_processor import (
    process_transcript_with_gemini,
    process_summary_with_gemini,
    DalilProcessorError
)
from app.modules.dalil_verifier import verify_dalil_in_transcript
from app.modules.pdf_renderer import render_kajian_pdf, render_ringkasan_pdf, merge_pdfs
from app.modules.font_loader import ensure_arabic_fonts

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-download fonts if needed
    ensure_arabic_fonts()
    yield

app = FastAPI(title="Kajian Transcriber API", version="1.0.0", lifespan=lifespan)

# Setup CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static, Templates, and Persistent Outputs
STATIC_DIR = BASE_DIR / "app" / "static"
TEMPLATES_DIR = BASE_DIR / "app" / "templates"
OUTPUTS_DIR = BASE_DIR / "outputs"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# In-memory job registry for stateless streaming
# Each job holds its temp directory for audio and persistent output directory for PDFs
class JobState:
    def __init__(self, job_id: str, process_mode: str = "transcript"):
        self.job_id = job_id
        self.process_mode = process_mode  # "transcript" | "summary" | "both"
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.temp_dir = self.temp_dir_obj.name
        self.output_dir = OUTPUTS_DIR / job_id
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.status = "queued"  # queued, processing, completed, error
        self.progress = 0.0
        self.stage = "init"
        self.message = "Menyiapkan antrian..."
        self.error: Optional[str] = None
        self.pdf_path: Optional[str] = None
        self.summary_pdf_path: Optional[str] = None
        self.bundle_pdf_path: Optional[str] = None
        self.judul: str = "Transkrip Kajian"
        self.summary_data: Optional[Dict[str, Any]] = None  # For copas without PDF
        self.event_queue = asyncio.Queue()
        self.created_at = time.time()

    async def emit_progress(self, percent: float, stage: str, message: str):
        self.progress = percent
        self.stage = stage
        self.message = message
        await self.event_queue.put({
            "status": "processing",
            "percent": percent,
            "stage": stage,
            "message": message
        })

    async def emit_completed(self, download_url: Optional[str], judul: str,
                             summary_download_url: Optional[str] = None,
                             summary_data: Optional[Dict] = None,
                             bundle_download_url: Optional[str] = None):
        self.status = "completed"
        self.progress = 100.0
        self.stage = "done"
        self.message = "Dokumen PDF selesai dibuat!"
        self.judul = judul
        payload: Dict[str, Any] = {
            "status": "completed",
            "percent": 100.0,
            "stage": "done",
            "message": "Dokumen selesai!",
            "judul": judul,
            "process_mode": self.process_mode,
        }
        if download_url:
            payload["download_url"] = download_url
        if summary_download_url:
            payload["summary_download_url"] = summary_download_url
        if bundle_download_url:
            payload["bundle_download_url"] = bundle_download_url
        if summary_data:
            payload["summary_data"] = summary_data
        await self.event_queue.put(payload)

    async def emit_error(self, err_msg: str):
        self.status = "error"
        self.error = err_msg
        self.message = f"Gagal: {err_msg}"
        await self.event_queue.put({
            "status": "error",
            "percent": self.progress,
            "stage": "error",
            "message": err_msg
        })

    def cleanup_temp_audio(self):
        """Cleans up heavy downloaded audio files, preserving generated PDFs."""
        try:
            self.temp_dir_obj.cleanup()
        except Exception:
            pass

    def cleanup(self):
        self.cleanup_temp_audio()

jobs: Dict[str, JobState] = {}

# ─────────────────────────────────────────────
# HELPERS: Audio Extraction (common to all modes)
# ─────────────────────────────────────────────

async def _extract_audio(job: "JobState", source_type: str, youtube_url: Optional[str],
                          uploaded_file_path: Optional[str],
                          judul_manual: Optional[str], ustadz_manual: Optional[str]) -> tuple:
    """Extract & convert audio. Returns (opus_path, final_title, final_ustadz, source_url_display)."""
    final_title = judul_manual or ""
    final_ustadz = ustadz_manual or ""
    source_url_display = youtube_url or "File Upload"
    loop = asyncio.get_event_loop()

    # ---- TAHAP 1: INPUT & EKSTRAKSI AUDIO ----
    if source_type == "youtube":
        await job.emit_progress(5.0, "download", "Memvalidasi tautan YouTube...")

        def do_validate():
            return validate_youtube_url(youtube_url)

        meta = await loop.run_in_executor(None, do_validate)
        if not final_title:
            final_title = meta.get("title", "Kajian Islam")
        if not final_ustadz:
            final_ustadz = meta.get("uploader", "Ustadz")

        await job.emit_progress(15.0, "download", f"Mengunduh audio: {final_title[:45]}...")

        def do_download():
            return download_youtube_audio(url=youtube_url, output_dir=job.temp_dir)

        dl_res = await loop.run_in_executor(None, do_download)
        raw_audio_path = dl_res["file_path"]

    elif source_type == "file":
        await job.emit_progress(10.0, "upload", "Menerima file audio/video unggahan...")
        if not uploaded_file_path or not os.path.exists(uploaded_file_path):
            raise ValueError("File unggahan tidak ditemukan.")
        raw_audio_path = uploaded_file_path
        if not final_title:
            final_title = Path(uploaded_file_path).stem
        if not final_ustadz:
            final_ustadz = "Ustadz"
    else:
        raise ValueError("Tipe sumber tidak dikenal (harus youtube atau file).")

    # ---- TAHAP 2: KONVERSI AUDIO KE .OPUS ----
    await job.emit_progress(30.0, "convert", "Mengonversi audio ke format .opus (ASR Optimized)...")
    opus_path = os.path.join(job.temp_dir, "audio_processed.opus")

    def do_convert():
        return convert_to_opus(raw_audio_path, opus_path)

    await loop.run_in_executor(None, do_convert)

    return opus_path, final_title, final_ustadz, source_url_display


async def _transcribe(job: "JobState", opus_path: str, groq_key: str) -> str:
    """Transcribe audio, returns raw transcript string."""
    loop = asyncio.get_event_loop()
    await job.emit_progress(50.0, "transcribe", "Mentranskripsi rekaman dengan Groq Whisper Large v3...")

    def do_transcribe():
        return transcribe_audio_file(file_path=opus_path, api_key=groq_key)

    result = await loop.run_in_executor(None, do_transcribe)
    raw_transcript = result.get("text", "")

    if not raw_transcript.strip():
        raise TranscriberError("Transkripsi tidak menghasilkan teks ucapan apa pun.")

    return raw_transcript

# ─────────────────────────────────────────────
# PIPELINE: Transcript Mode
# ─────────────────────────────────────────────

async def run_transcript_pipeline(
    job: "JobState", opus_path: str, final_title: str, final_ustadz: str,
    source_url_display: str, raw_transcript: str, gemini_key: str
):
    loop = asyncio.get_event_loop()

    # ── TAHAP VERIFIKASI DALIL (Quran.com + Dorar) ──
    await job.emit_progress(55.0, "verify_dalil", "Memverifikasi ayat & hadits ke database resmi (Quran.com + Dorar)...")

    verified_dalil_list = []
    def do_verify():
        nonlocal verified_dalil_list
        patched, vlist = verify_dalil_in_transcript(
            transcript=raw_transcript,
            gemini_api_key=gemini_key
        )
        verified_dalil_list = vlist
        return patched

    verified_transcript = await loop.run_in_executor(None, do_verify)
    n_verified = len(verified_dalil_list)
    await job.emit_progress(68.0, "verify_dalil",
        f"Verifikasi selesai: {n_verified} dalil terverifikasi database ✅")

    # ── TAHAP GEMINI: Strukturisasi Transkrip ──
    await job.emit_progress(75.0, "dalil", "Menyusun struktur & mempreservasi dalil Al-Qur'an/Hadits...")

    meta_hint = {"judul": final_title, "ustadz": final_ustadz}

    def do_dalil_process():
        return process_transcript_with_gemini(
            raw_transcript=verified_transcript,
            metadata_hint=meta_hint,
            api_key=gemini_key
        )

    structured_json = await loop.run_in_executor(None, do_dalil_process)
    # Simpan info dalil terverifikasi ke dalam output JSON untuk PDF renderer
    structured_json["verified_dalil"] = verified_dalil_list

    await job.emit_progress(92.0, "pdf", "Merender dokumen PDF dengan font Arab & layout dalil...")

    output_pdf = os.path.join(str(job.output_dir), "Transkrip_Kajian.pdf")
    pdf_metadata = {"ustadz": final_ustadz, "source_url": source_url_display,
                    "verified_count": n_verified}

    def do_render_pdf():
        return render_kajian_pdf(data=structured_json, output_pdf_path=output_pdf, metadata=pdf_metadata)

    await loop.run_in_executor(None, do_render_pdf)
    job.pdf_path = output_pdf
    job.cleanup_temp_audio()

    final_judul = structured_json.get("judul_kajian", final_title)
    await job.emit_completed(
        download_url=f"/api/download/{job.job_id}/Transkrip_Kajian.pdf",
        judul=final_judul
    )

# ─────────────────────────────────────────────
# PIPELINE: Summary Mode
# ─────────────────────────────────────────────

async def run_summary_pipeline(
    job: "JobState", opus_path: str, final_title: str, final_ustadz: str,
    source_url_display: str, raw_transcript: str, gemini_key: str
):
    loop = asyncio.get_event_loop()

    # ── TAHAP VERIFIKASI DALIL ──
    await job.emit_progress(55.0, "verify_dalil", "Memverifikasi ayat & hadits ke database resmi (Quran.com + Dorar)...")

    verified_dalil_list = []
    def do_verify():
        nonlocal verified_dalil_list
        patched, vlist = verify_dalil_in_transcript(
            transcript=raw_transcript,
            gemini_api_key=gemini_key
        )
        verified_dalil_list = vlist
        return patched

    verified_transcript = await loop.run_in_executor(None, do_verify)
    n_verified = len(verified_dalil_list)
    await job.emit_progress(65.0, "verify_dalil",
        f"Verifikasi selesai: {n_verified} dalil terverifikasi database ✅")

    # ── TAHAP GEMINI: Ringkasan ──
    await job.emit_progress(70.0, "summary", "Menyusun ringkasan cerdas, refleksi & korelasi dalil...")

    meta_hint = {"judul": final_title, "ustadz": final_ustadz}

    def do_summary():
        return process_summary_with_gemini(
            raw_transcript=verified_transcript,
            metadata_hint=meta_hint,
            api_key=gemini_key
        )

    summary_json = await loop.run_in_executor(None, do_summary)
    summary_json["verified_dalil"] = verified_dalil_list
    job.summary_data = summary_json

    await job.emit_progress(90.0, "pdf", "Merender dokumen PDF ringkasan...")

    output_pdf = os.path.join(str(job.output_dir), "Ringkasan_Kajian.pdf")
    pdf_metadata = {"ustadz": final_ustadz, "source_url": source_url_display,
                    "verified_count": n_verified}

    def do_render_summary_pdf():
        return render_ringkasan_pdf(data=summary_json, output_pdf_path=output_pdf, metadata=pdf_metadata)

    await loop.run_in_executor(None, do_render_summary_pdf)
    job.summary_pdf_path = output_pdf
    job.cleanup_temp_audio()

    final_judul = summary_json.get("judul_kajian", final_title)
    await job.emit_completed(
        download_url=None,
        judul=final_judul,
        summary_download_url=f"/api/download-summary/{job.job_id}/Ringkasan_Kajian.pdf",
        summary_data=summary_json
    )

# ─────────────────────────────────────────────
# PIPELINE: Both Mode (Transcript + Summary)
# ─────────────────────────────────────────────

async def run_both_pipeline(
    job: "JobState", opus_path: str, final_title: str, final_ustadz: str,
    source_url_display: str, raw_transcript: str, gemini_key: str
):
    loop = asyncio.get_event_loop()
    meta_hint = {"judul": final_title, "ustadz": final_ustadz}

    # ── TAHAP VERIFIKASI DALIL (dilakukan sekali, dipakai oleh kedua pipeline) ──
    await job.emit_progress(52.0, "verify_dalil", "Memverifikasi ayat & hadits ke database resmi (Quran.com + Dorar)...")

    verified_dalil_list = []
    def do_verify():
        nonlocal verified_dalil_list
        patched, vlist = verify_dalil_in_transcript(
            transcript=raw_transcript,
            gemini_api_key=gemini_key
        )
        verified_dalil_list = vlist
        return patched

    verified_transcript = await loop.run_in_executor(None, do_verify)
    n_verified = len(verified_dalil_list)
    pdf_metadata = {"ustadz": final_ustadz, "source_url": source_url_display,
                    "verified_count": n_verified}
    await job.emit_progress(60.0, "verify_dalil",
        f"Verifikasi selesai: {n_verified} dalil terverifikasi database ✅")

    # Step A: Full Transcript
    await job.emit_progress(63.0, "dalil", "Menyusun transkrip lengkap & preservasi dalil...")

    def do_dalil():
        return process_transcript_with_gemini(
            raw_transcript=verified_transcript, metadata_hint=meta_hint, api_key=gemini_key
        )

    structured_json = await loop.run_in_executor(None, do_dalil)
    structured_json["verified_dalil"] = verified_dalil_list

    await job.emit_progress(74.0, "pdf", "Merender PDF transkrip lengkap...")
    output_pdf = os.path.join(str(job.output_dir), "Transkrip_Kajian.pdf")

    def do_pdf():
        return render_kajian_pdf(data=structured_json, output_pdf_path=output_pdf, metadata=pdf_metadata)

    await loop.run_in_executor(None, do_pdf)
    job.pdf_path = output_pdf

    # Step B: Summary
    await job.emit_progress(82.0, "summary", "Menyusun ringkasan cerdas, refleksi & bagan...")

    def do_summary():
        return process_summary_with_gemini(
            raw_transcript=verified_transcript, metadata_hint=meta_hint, api_key=gemini_key
        )

    summary_json = await loop.run_in_executor(None, do_summary)
    summary_json["verified_dalil"] = verified_dalil_list
    job.summary_data = summary_json

    await job.emit_progress(92.0, "pdf", "Merender PDF ringkasan & refleksi...")
    summary_pdf = os.path.join(str(job.output_dir), "Ringkasan_Kajian.pdf")

    def do_summary_pdf():
        return render_ringkasan_pdf(data=summary_json, output_pdf_path=summary_pdf, metadata=pdf_metadata)

    await loop.run_in_executor(None, do_summary_pdf)
    job.summary_pdf_path = summary_pdf

    # Step C: Bundle both into one combined PDF (Summary first, then Transcript)
    await job.emit_progress(97.0, "pdf", "Menggabungkan PDF bundel lengkap (Ringkasan + Transkrip)...")
    bundle_pdf = os.path.join(str(job.output_dir), "Kajian_Lengkap.pdf")

    def do_bundle():
        return merge_pdfs([summary_pdf, output_pdf], bundle_pdf)

    await loop.run_in_executor(None, do_bundle)
    job.bundle_pdf_path = bundle_pdf
    job.cleanup_temp_audio()

    final_judul = structured_json.get("judul_kajian", final_title)
    await job.emit_completed(
        download_url=f"/api/download/{job.job_id}/Transkrip_Kajian.pdf",
        judul=final_judul,
        summary_download_url=f"/api/download-summary/{job.job_id}/Ringkasan_Kajian.pdf",
        summary_data=summary_json,
        bundle_download_url=f"/api/download-bundle/{job.job_id}/Kajian_Lengkap.pdf"
    )

# ─────────────────────────────────────────────
# MAIN BACKGROUND TASK
# ─────────────────────────────────────────────

async def run_pipeline_task(
    job_id: str,
    source_type: str,
    youtube_url: Optional[str],
    uploaded_file_path: Optional[str],
    judul_manual: Optional[str],
    ustadz_manual: Optional[str],
    groq_key: Optional[str],
    gemini_key: Optional[str]
):
    job = jobs.get(job_id)
    if not job:
        return

    try:
        job.status = "processing"

        # Audio extraction is common for all modes
        opus_path, final_title, final_ustadz, source_url_display = await _extract_audio(
            job=job,
            source_type=source_type,
            youtube_url=youtube_url,
            uploaded_file_path=uploaded_file_path,
            judul_manual=judul_manual,
            ustadz_manual=ustadz_manual
        )

        # Transcription is common for all modes
        raw_transcript = await _transcribe(job, opus_path, groq_key)

        # Save raw transcript to job output_dir as backup
        try:
            raw_transcript_path = job.output_dir / "raw_transcript.txt"
            with open(raw_transcript_path, "w", encoding="utf-8") as f:
                f.write(raw_transcript)
        except Exception:
            pass

        # Branch based on process mode
        if job.process_mode == "summary":
            await run_summary_pipeline(
                job=job, opus_path=opus_path, final_title=final_title,
                final_ustadz=final_ustadz, source_url_display=source_url_display,
                raw_transcript=raw_transcript, gemini_key=gemini_key
            )
        elif job.process_mode == "both":
            await run_both_pipeline(
                job=job, opus_path=opus_path, final_title=final_title,
                final_ustadz=final_ustadz, source_url_display=source_url_display,
                raw_transcript=raw_transcript, gemini_key=gemini_key
            )
        else:  # default: "transcript"
            await run_transcript_pipeline(
                job=job, opus_path=opus_path, final_title=final_title,
                final_ustadz=final_ustadz, source_url_display=source_url_display,
                raw_transcript=raw_transcript, gemini_key=gemini_key
            )

    except Exception as e:
        err_msg = str(e)
        await job.emit_error(err_msg)

# ─────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/health")
async def health_check():
    return {
        "status": "online",
        "ffmpeg": check_ffmpeg(),
        "has_groq_key": bool(GROQ_API_KEY),
        "has_gemini_key": bool(GEMINI_API_KEY),
    }

@app.post("/api/start-job")
async def start_job(
    background_tasks: BackgroundTasks,
    source_type: str = Form(...),
    process_mode: str = Form("transcript"),  # "transcript" | "summary" | "both"
    youtube_url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    judul: Optional[str] = Form(None),
    ustadz: Optional[str] = Form(None),
    groq_key: Optional[str] = Form(None),
    gemini_key: Optional[str] = Form(None),
):
    # Validate process_mode
    valid_modes = ("transcript", "summary", "both")
    if process_mode not in valid_modes:
        process_mode = "transcript"

    # Check effective keys
    eff_groq = (groq_key or GROQ_API_KEY or "").strip()
    eff_gemini = (gemini_key or GEMINI_API_KEY or "").strip()

    if not eff_groq:
        raise HTTPException(
            status_code=400,
            detail="GROQ_API_KEY belum dikonfigurasi. Mohon isi di .env atau masukkan API key."
        )
    if not eff_gemini:
        raise HTTPException(
            status_code=400,
            detail="GEMINI_API_KEY belum dikonfigurasi. Mohon isi di .env atau masukkan API key."
        )

    job_id = str(uuid.uuid4())
    job = JobState(job_id, process_mode=process_mode)
    jobs[job_id] = job

    uploaded_file_path = None
    if source_type == "file":
        if not file or not file.filename:
            raise HTTPException(status_code=400, detail="File rekaman wajib diunggah untuk mode file.")

        # Save file to temp dir
        safe_filename = Path(file.filename).name
        uploaded_file_path = os.path.join(job.temp_dir, safe_filename)
        with open(uploaded_file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

    elif source_type == "youtube":
        if not youtube_url or not youtube_url.strip():
            raise HTTPException(status_code=400, detail="Link YouTube wajib diisi untuk mode YouTube.")

    # Launch pipeline in background
    background_tasks.add_task(
        run_pipeline_task,
        job_id=job_id,
        source_type=source_type,
        youtube_url=youtube_url.strip() if youtube_url else None,
        uploaded_file_path=uploaded_file_path,
        judul_manual=judul.strip() if judul else None,
        ustadz_manual=ustadz.strip() if ustadz else None,
        groq_key=eff_groq,
        gemini_key=eff_gemini
    )

    return {"job_id": job_id, "status": "queued", "process_mode": process_mode}

@app.get("/api/events/{job_id}")
async def get_job_events(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    async def event_generator():
        # First send current state immediately
        initial_payload = {
            "status": job.status,
            "percent": job.progress,
            "stage": job.stage,
            "message": job.message,
            "process_mode": job.process_mode
        }
        if job.status == "completed":
            if job.pdf_path:
                initial_payload["download_url"] = f"/api/download/{job_id}/Transkrip_Kajian.pdf"
            if job.summary_pdf_path:
                initial_payload["summary_download_url"] = f"/api/download-summary/{job_id}/Ringkasan_Kajian.pdf"
            if getattr(job, "bundle_pdf_path", None):
                initial_payload["bundle_download_url"] = f"/api/download-bundle/{job_id}/Kajian_Lengkap.pdf"
            initial_payload["judul"] = job.judul
            if job.summary_data:
                initial_payload["summary_data"] = job.summary_data
        elif job.status == "error":
            initial_payload["message"] = job.error or job.message

        yield f"data: {JSONResponse(initial_payload).body.decode()}\n\n"

        if job.status in ("completed", "error"):
            return

        while True:
            try:
                # Wait for next event with a timeout for keepalive
                event_data = await asyncio.wait_for(job.event_queue.get(), timeout=15.0)
                yield f"data: {JSONResponse(event_data).body.decode()}\n\n"
                if event_data.get("status") in ("completed", "error"):
                    break
            except asyncio.TimeoutError:
                # Keepalive comment
                yield ": keepalive\n\n"
            except asyncio.CancelledError:
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")

def _pdf_not_found_response(message: str = "File PDF tidak ditemukan atau sedang diproses.") -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PDF Tidak Ditemukan - Kajian Transcriber</title>
    <style>
        body {{ font-family: 'Plus Jakarta Sans', system-ui, sans-serif; background: #090e17; color: #f8fafc; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }}
        .card {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 16px; padding: 36px 28px; max-width: 480px; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }}
        .icon {{ font-size: 40px; margin-bottom: 12px; }}
        h2 {{ color: #f87171; margin: 0 0 10px 0; font-size: 1.25rem; }}
        p {{ color: #94a3b8; font-size: 0.92rem; line-height: 1.6; margin-bottom: 24px; }}
        .btn {{ display: inline-block; padding: 12px 24px; background: #059669; color: #ffffff; text-decoration: none; border-radius: 8px; font-weight: 700; font-size: 0.9rem; }}
        .btn:hover {{ background: #10b981; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">📄</div>
        <h2>File PDF Belum Tersedia</h2>
        <p>{message}</p>
        <a href="/" class="btn">Kembali ke Halaman Utama</a>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=404)

@app.get("/api/download/{job_id}")
@app.get("/api/download/{job_id}/{filename:path}")
async def download_pdf(job_id: str, filename: Optional[str] = None):
    job = jobs.get(job_id)
    pdf_path = None
    judul = "Transkrip_Kajian"

    if job and job.pdf_path and os.path.exists(job.pdf_path):
        pdf_path = job.pdf_path
        judul = job.judul
    else:
        # Persistent fallback in OUTPUTS_DIR
        candidate = OUTPUTS_DIR / job_id / "Transkrip_Kajian.pdf"
        if candidate.exists():
            pdf_path = str(candidate)

    if not pdf_path or not os.path.exists(pdf_path):
        return _pdf_not_found_response("Dokumen PDF transkrip tidak ditemukan atau belum selesai diproses.")

    clean_title = "".join(c for c in judul if c.isalnum() or c in (' ', '_', '-')).strip()
    clean_title = clean_title.replace(' ', '_')[:60] or "Transkrip_Kajian"
    download_filename = f"{clean_title}.pdf"

    return FileResponse(
        path=pdf_path,
        filename=download_filename,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{download_filename}"'}
    )

@app.get("/api/download-summary/{job_id}")
@app.get("/api/download-summary/{job_id}/{filename:path}")
async def download_summary_pdf(job_id: str, filename: Optional[str] = None):
    job = jobs.get(job_id)
    pdf_path = None
    judul = "Ringkasan_Kajian"

    if job and job.summary_pdf_path and os.path.exists(job.summary_pdf_path):
        pdf_path = job.summary_pdf_path
        judul = job.judul
    else:
        # Persistent fallback in OUTPUTS_DIR
        candidate = OUTPUTS_DIR / job_id / "Ringkasan_Kajian.pdf"
        if candidate.exists():
            pdf_path = str(candidate)

    if not pdf_path or not os.path.exists(pdf_path):
        return _pdf_not_found_response("Dokumen PDF ringkasan tidak ditemukan atau belum selesai diproses.")

    clean_title = "".join(c for c in judul if c.isalnum() or c in (' ', '_', '-')).strip()
    clean_title = clean_title.replace(' ', '_')[:60] or "Ringkasan_Kajian"
    download_filename = f"Ringkasan_{clean_title}.pdf"

    return FileResponse(
        path=pdf_path,
        filename=download_filename,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{download_filename}"'}
    )

@app.get("/api/download-bundle/{job_id}")
@app.get("/api/download-bundle/{job_id}/{filename:path}")
async def download_bundle_pdf(job_id: str, filename: Optional[str] = None):
    job = jobs.get(job_id)
    pdf_path = None
    judul = "Kajian_Lengkap"

    if job and getattr(job, "bundle_pdf_path", None) and os.path.exists(job.bundle_pdf_path):
        pdf_path = job.bundle_pdf_path
        judul = job.judul
    else:
        # Persistent fallback in OUTPUTS_DIR
        candidate = OUTPUTS_DIR / job_id / "Kajian_Lengkap.pdf"
        if candidate.exists():
            pdf_path = str(candidate)
        else:
            # If bundle doesn't exist yet but transcript and summary exist, merge on the fly!
            t_cand = OUTPUTS_DIR / job_id / "Transkrip_Kajian.pdf"
            s_cand = OUTPUTS_DIR / job_id / "Ringkasan_Kajian.pdf"
            if t_cand.exists() and s_cand.exists():
                try:
                    b_target = str(OUTPUTS_DIR / job_id / "Kajian_Lengkap.pdf")
                    merge_pdfs([str(s_cand), str(t_cand)], b_target)
                    pdf_path = b_target
                except Exception:
                    pass

    if not pdf_path or not os.path.exists(pdf_path):
        return _pdf_not_found_response("Dokumen PDF bundel lengkap tidak ditemukan atau belum selesai diproses.")

    clean_title = "".join(c for c in judul if c.isalnum() or c in (' ', '_', '-')).strip()
    clean_title = clean_title.replace(' ', '_')[:60] or "Kajian_Lengkap"
    download_filename = f"Lengkap_{clean_title}.pdf"

    return FileResponse(
        path=pdf_path,
        filename=download_filename,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{download_filename}"'}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=DEBUG)
