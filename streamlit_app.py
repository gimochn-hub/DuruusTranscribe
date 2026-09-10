import os
import sys
import tempfile
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import streamlit as st

from app.config import GROQ_API_KEY, GEMINI_API_KEY
from app.modules.downloader import validate_youtube_url, download_youtube_audio
from app.modules.converter import convert_to_opus, check_ffmpeg
from app.modules.transcriber import transcribe_audio_file
from app.modules.dalil_verifier import verify_dalil_in_transcript
from app.modules.dalil_processor import process_summary_with_gemini, process_transcript_with_gemini
from app.modules.pdf_renderer import render_ringkasan_pdf, render_kajian_pdf, merge_pdfs
from app.modules.font_loader import ensure_arabic_fonts

# Ensure fonts are available
ensure_arabic_fonts()

st.set_page_config(
    page_title="Kajian Transcriber & Tadabbur AI",
    page_icon="🕌",
    layout="wide"
)

st.title("🕌 Kajian Transcriber & Tadabbur AI")
st.caption("Transkrip audio kajian, ekstraksi dalil Al-Qur'an/Hadits, ringkasan per-poin, dan tadabbur lathaif otomatis ke PDF.")

# Sidebar for API Keys & Settings
with st.sidebar:
    st.header("🔑 Pengaturan API Key")
    groq_input = st.text_input(
        "Groq API Key (Whisper)",
        value=os.getenv("GROQ_API_KEY", ""),
        type="password",
        help="Dapatkan gratis di https://console.groq.com"
    )
    gemini_input = st.text_input(
        "Gemini API Key (AI Analisis)",
        value=os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", ""),
        type="password",
        help="Dapatkan gratis di https://aistudio.google.com"
    )

    st.markdown("---")
    st.markdown("### ⚙️ Info Sistem")
    ffmpeg_ok = check_ffmpeg()
    if ffmpeg_ok:
        st.success("✅ FFmpeg Terdeteksi")
    else:
        st.warning("⚠️ FFmpeg memuat modul otomatis")

# Main Interface
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("1. Pilih Sumber Audio")
    source_type = st.radio("Tipe Input", ["YouTube Link", "Upload File Audio/Video"], horizontal=True)
    
    youtube_url = ""
    uploaded_file = None
    
    if source_type == "YouTube Link":
        youtube_url = st.text_input("Tautan Video/Audio YouTube", placeholder="https://www.youtube.com/watch?v=...")
    else:
        uploaded_file = st.file_uploader("Pilih File Audio/Video", type=["mp3", "m4a", "wav", "mp4", "aac", "ogg", "opus"])

with col2:
    st.subheader("2. Opsi Kajian")
    judul_input = st.text_input("Judul Kajian (Opsional)", placeholder="Otomatis diambil jika kosong")
    ustadz_input = st.text_input("Nama Ustadz / Pemateri (Opsional)", placeholder="Otomatis diambil jika kosong")
    
    process_mode = st.selectbox(
        "Mode Dokumen yang Dibuat",
        [
            ("summary", "📋 Ringkasan Poin + Tadabbur Lathaif + 3 Dalil (Rekomendasi)"),
            ("both", "📚 Paket Lengkap (Ringkasan + Transkrip Kata Per Kata)"),
            ("transcript", "📄 Transkrip Lengkap Kata Per Kata Saja")
        ],
        format_func=lambda x: x[1]
    )[0]

st.markdown("---")

if st.button("🚀 Mulai Proses Transkripsi & Analisis", type="primary", use_container_width=True):
    # Validation
    groq_key = (groq_input or GROQ_API_KEY or "").strip()
    gemini_key = (gemini_input or GEMINI_API_KEY or "").strip()
    
    if not groq_key:
        st.error("❌ Groq API Key wajib diisi! Masukkan di sidebar sebelah kiri.")
        st.stop()
    if not gemini_key:
        st.error("❌ Gemini API Key wajib diisi! Masukkan di sidebar sebelah kiri.")
        st.stop()
        
    if source_type == "YouTube Link" and not youtube_url.strip():
        st.error("❌ Silakan masukkan link YouTube terlebih dahulu.")
        st.stop()
        
    if source_type != "YouTube Link" and not uploaded_file:
        st.error("❌ Silakan unggah file rekaman terlebih dahulu.")
        st.stop()

    progress_bar = st.progress(5)
    status_text = st.empty()
    
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # 1. Extraction
            final_title = judul_input.strip() if judul_input else ""
            final_ustadz = ustadz_input.strip() if ustadz_input else "Ustadz"
            source_display = youtube_url or "File Upload"
            
            if source_type == "YouTube Link":
                status_text.info("📥 Mengunduh audio dari YouTube...")
                meta = validate_youtube_url(youtube_url)
                if not final_title:
                    final_title = meta.get("title", "Kajian Islam")
                if not final_ustadz or final_ustadz == "Ustadz":
                    final_ustadz = meta.get("uploader", "Ustadz")
                
                dl_res = download_youtube_audio(youtube_url, output_dir=temp_dir)
                raw_audio = dl_res["file_path"]
            else:
                status_text.info("📥 Menyimpan file audio...")
                raw_audio = os.path.join(temp_dir, uploaded_file.name)
                with open(raw_audio, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                if not final_title:
                    final_title = Path(uploaded_file.name).stem

            progress_bar.progress(25)
            
            # 2. Conversion
            status_text.info("🔄 Mengonversi audio ke format optimal Whisper (.opus)...")
            opus_path = os.path.join(temp_dir, "audio.opus")
            convert_to_opus(raw_audio, opus_path)
            progress_bar.progress(40)
            
            # 3. Transcribe
            status_text.info("🎙️ Mentranskripsi rekaman dengan Groq Whisper Large v3...")
            trans_res = transcribe_audio_file(opus_path, api_key=groq_key)
            raw_transcript = trans_res.get("text", "").strip()
            
            if not raw_transcript:
                st.error("❌ Gagal: Tidak ada ucapan kata yang terdeteksi.")
                st.stop()
            progress_bar.progress(60)
            
            # 4. Verify Dalil
            status_text.info("🔍 Memverifikasi ayat Al-Qur'an dan Hadits ke database resmi...")
            verified_transcript, verified_list = verify_dalil_in_transcript(
                transcript=raw_transcript,
                gemini_api_key=gemini_key
            )
            n_verified = len(verified_list)
            progress_bar.progress(75)
            
            meta_hint = {"judul": final_title, "ustadz": final_ustadz}
            pdf_meta = {
                "ustadz": final_ustadz,
                "source_url": source_display,
                "verified_count": n_verified
            }
            
            # 5. Pipeline execution
            if process_mode == "summary":
                status_text.info("🧠 AI Gemini sedang menyusun poin ringkasan, refleksi, 3+ dalil & tadabbur lathaif...")
                summary_data = process_summary_with_gemini(verified_transcript, meta_hint, api_key=gemini_key)
                summary_data["verified_dalil"] = verified_list
                progress_bar.progress(90)
                
                status_text.info("📄 Merender dokumen PDF Ringkasan Kajian...")
                pdf_path = os.path.join(temp_dir, "Ringkasan_Kajian.pdf")
                render_ringkasan_pdf(summary_data, pdf_path, metadata=pdf_meta)
                progress_bar.progress(100)
                
                status_text.success("🎉 Berhasil! Dokumen Ringkasan Kajian siap diunduh.")
                
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()
                    
                clean_title = "".join(c for c in final_title if c.isalnum() or c in (' ', '_', '-')).strip()
                clean_title = clean_title.replace(' ', '_')[:50] or "Kajian"
                
                st.download_button(
                    label=f"📥 Download PDF Ringkasan ({clean_title}.pdf)",
                    data=pdf_bytes,
                    file_name=f"Ringkasan_{clean_title}.pdf",
                    mime="application/pdf",
                    type="primary"
                )
                
                # Display preview
                with st.expander("👁️ Lihat Ringkasan & Tadabbur di Layar"):
                    st.write(summary_data)
                    
            elif process_mode == "both":
                status_text.info("🧠 Menyusun transkrip lengkap & ringkasan tadabbur...")
                struct_data = process_transcript_with_gemini(verified_transcript, meta_hint, api_key=gemini_key)
                struct_data["verified_dalil"] = verified_list
                
                summary_data = process_summary_with_gemini(verified_transcript, meta_hint, api_key=gemini_key)
                summary_data["verified_dalil"] = verified_list
                progress_bar.progress(85)
                
                status_text.info("📄 Merender dokumen PDF lengkap...")
                pdf_t = os.path.join(temp_dir, "Transkrip.pdf")
                pdf_s = os.path.join(temp_dir, "Ringkasan.pdf")
                pdf_b = os.path.join(temp_dir, "Kajian_Lengkap.pdf")
                
                render_kajian_pdf(struct_data, pdf_t, metadata=pdf_meta)
                render_ringkasan_pdf(summary_data, pdf_s, metadata=pdf_meta)
                merge_pdfs([pdf_s, pdf_t], pdf_b)
                progress_bar.progress(100)
                
                status_text.success("🎉 Berhasil! Paket PDF Lengkap siap diunduh.")
                with open(pdf_b, "rb") as f:
                    st.download_button(
                        label="📥 Download PDF Paket Lengkap (Ringkasan + Transkrip)",
                        data=f.read(),
                        file_name=f"Lengkap_{final_title[:40]}.pdf",
                        mime="application/pdf",
                        type="primary"
                    )
            else:
                status_text.info("🧠 AI Gemini sedang menyusun struktur transkrip...")
                struct_data = process_transcript_with_gemini(verified_transcript, meta_hint, api_key=gemini_key)
                struct_data["verified_dalil"] = verified_list
                progress_bar.progress(90)
                
                status_text.info("📄 Merender PDF Transkrip...")
                pdf_t = os.path.join(temp_dir, "Transkrip_Kajian.pdf")
                render_kajian_pdf(struct_data, pdf_t, metadata=pdf_meta)
                progress_bar.progress(100)
                
                status_text.success("🎉 Berhasil! Transkrip siap diunduh.")
                with open(pdf_t, "rb") as f:
                    st.download_button(
                        label="📥 Download PDF Transkrip Lengkap",
                        data=f.read(),
                        file_name=f"Transkrip_{final_title[:40]}.pdf",
                        mime="application/pdf",
                        type="primary"
                    )

    except Exception as e:
        st.error(f"❌ Terjadi kesalahan: {str(e)}")
