import os
import time
from typing import Optional, Callable, Dict, Any, List
from groq import Groq, APIError, RateLimitError, APIConnectionError
from app.config import GROQ_API_KEY
from app.modules.converter import split_audio_if_needed

class TranscriberError(Exception):
    """Custom exception for transcription errors."""
    pass

def get_groq_client(api_key: Optional[str] = None) -> Groq:
    """Get initialized Groq client."""
    key = (api_key or GROQ_API_KEY or "").strip()
    if not key:
        raise TranscriberError(
            "Groq API Key belum dikonfigurasi. Mohon isi GROQ_API_KEY di file .env atau sertakan API key."
        )
    return Groq(api_key=key)

def transcribe_audio_file(
    file_path: str,
    api_key: Optional[str] = None,
    prompt_hint: Optional[str] = "Kajian Islam, ceramah agama, ayat Al-Qur'an, hadits Nabi, bahasa Indonesia dan bahasa Arab.",
    max_retries: int = 4,
    retry_delay_seconds: float = 3.0,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> Dict[str, Any]:
    """
    Transcribe an audio file using Groq whisper-large-v3 with exponential backoff retry.
    Supports audio split if file is large (>24MB).
    """
    if not os.path.exists(file_path):
        raise TranscriberError(f"File audio tidak ditemukan: {file_path}")

    client = get_groq_client(api_key)

    # Check if file needs splitting (<24MB per chunk)
    chunks = split_audio_if_needed(file_path, max_size_mb=24.0)
    total_chunks = len(chunks)

    all_transcripts: List[str] = []

    for idx, chunk_path in enumerate(chunks):
        chunk_num = idx + 1
        if progress_callback:
            percent = 10.0 + (idx / total_chunks) * 80.0
            chunk_info = f" (bagian {chunk_num}/{total_chunks})" if total_chunks > 1 else ""
            progress_callback(percent, f"Mentranskripsi rekaman dengan Groq Whisper Large v3{chunk_info}...")

        transcript_text = _transcribe_single_chunk_with_retry(
            client=client,
            chunk_path=chunk_path,
            prompt_hint=prompt_hint,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            progress_callback=progress_callback
        )
        all_transcripts.append(transcript_text.strip())

    combined_text = "\n\n".join(all_transcripts).strip()

    if progress_callback:
        progress_callback(100.0, "Transkripsi selesai.")

    return {
        "text": combined_text,
        "chunks_count": total_chunks,
        "raw_length": len(combined_text)
    }

def _transcribe_single_chunk_with_retry(
    client: Groq,
    chunk_path: str,
    prompt_hint: Optional[str],
    max_retries: int,
    retry_delay_seconds: float,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> str:
    """Internal helper to transcribe a single audio chunk with retry backoff."""
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            with open(chunk_path, "rb") as audio_file:
                # Whisper on Groq
                transcription = client.audio.transcriptions.create(
                    file=(os.path.basename(chunk_path), audio_file.read()),
                    model="whisper-large-v3",
                    prompt=prompt_hint,
                    response_format="text",
                    temperature=0.0
                )
                return str(transcription)
        except RateLimitError as e:
            last_error = e
            wait_time = retry_delay_seconds * (2 ** (attempt - 1))
            if progress_callback:
                progress_callback(0, f"Rate limit Groq terdeteksi, mencoba ulang dalam {wait_time:.1f} detik (Percobaan {attempt}/{max_retries})...")
            time.sleep(wait_time)
        except APIConnectionError as e:
            last_error = e
            wait_time = retry_delay_seconds * (2 ** (attempt - 1))
            if progress_callback:
                progress_callback(0, f"Koneksi ke Groq terputus, mencoba ulang dalam {wait_time:.1f} detik (Percobaan {attempt}/{max_retries})...")
            time.sleep(wait_time)
        except APIError as e:
            last_error = e
            err_msg = str(e)
            if "invalid_api_key" in err_msg.lower() or "unauthorized" in err_msg.lower():
                raise TranscriberError("Groq API Key tidak valid. Mohon periksa kembali GROQ_API_KEY di file .env.")
            # For other API errors, try retry
            wait_time = retry_delay_seconds * (2 ** (attempt - 1))
            time.sleep(wait_time)
        except Exception as e:
            last_error = e
            raise TranscriberError(f"Terjadi kesalahan saat memproses audio dengan Groq: {str(e)}")

    raise TranscriberError(
        f"Gagal mentranskripsi audio setelah {max_retries} percobaan. Error terakhir: {str(last_error)}"
    )
