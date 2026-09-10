import os
import subprocess
import shutil
import json
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List

class ConversionError(Exception):
    """Custom exception for audio conversion errors."""
    pass

def check_ffmpeg() -> bool:
    """Check if ffmpeg executable is available in PATH."""
    if shutil.which("ffmpeg") is not None:
        return True
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        return shutil.which("ffmpeg") is not None
    except Exception:
        return False

def check_ffprobe() -> bool:
    """Check if ffprobe executable is available in PATH."""
    if shutil.which("ffprobe") is not None:
        return True
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        return shutil.which("ffprobe") is not None
    except Exception:
        return False


def get_audio_info(file_path: str) -> Dict[str, Any]:
    """Get audio duration and format details using ffprobe."""
    if not os.path.exists(file_path):
        raise ConversionError(f"File tidak ditemukan: {file_path}")

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(res.stdout)
        duration = float(data.get("format", {}).get("duration", 0.0))
        size_bytes = int(data.get("format", {}).get("size", os.path.getsize(file_path)))
        return {
            "duration": duration,
            "size_bytes": size_bytes,
            "format_name": data.get("format", {}).get("format_name", "")
        }
    except Exception as e:
        # Fallback if ffprobe fails or duration is missing
        return {
            "duration": 0.0,
            "size_bytes": os.path.getsize(file_path),
            "format_name": ""
        }

def convert_to_opus(
    input_path: str,
    output_path: str,
    bitrate: str = "32k",
    sample_rate: int = 16000,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> str:
    """
    Convert any input audio/video file to an ASR-optimized .opus audio file.
    
    Optimizations:
    - Codec: libopus
    - Audio channels: 1 (mono)
    - Sample rate: 16kHz (optimal for Whisper)
    - Bitrate: 32kbps (crisp speech, ~14MB per hour)
    """
    if not os.path.exists(input_path):
        raise ConversionError(f"Input file tidak ditemukan: {input_path}")

    if not check_ffmpeg():
        raise ConversionError("FFmpeg tidak ditemukan di sistem server.")

    # Ensure output directory exists
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if progress_callback:
        progress_callback(10.0, "Memulai konversi audio ke format .opus...")

    # Build ffmpeg command
    # -y: overwrite output
    # -vn: disable video recording/processing
    # -acodec libopus: use opus codec
    # -ac 1: mono
    # -ar 16000: 16kHz sample rate
    # -b:a 32k: 32kbps bitrate
    # -application voip: tuned for voice
    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-vn",
        "-c:a", "libopus",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-b:a", bitrate,
        "-application", "voip",
        output_path
    ]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate()

        if process.returncode != 0:
            raise ConversionError(f"FFmpeg gagal mengonversi audio (Exit code {process.returncode}): {stderr[-500:]}")

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise ConversionError("File output .opus kosong atau tidak berhasil dibuat.")

        if progress_callback:
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            progress_callback(100.0, f"Konversi .opus selesai ({file_size_mb:.2f} MB).")

        return output_path
    except Exception as e:
        if isinstance(e, ConversionError):
            raise
        raise ConversionError(f"Terjadi kesalahan saat konversi audio: {str(e)}")

def split_audio_if_needed(
    audio_path: str,
    max_size_mb: float = 24.0,
    segment_time_seconds: int = 3600
) -> List[str]:
    """
    If audio exceeds max_size_mb, split it into chunks under max_size_mb.
    Returns list of paths to chunk files (or single original path if no split needed).
    """
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    if file_size_mb <= max_size_mb:
        return [audio_path]

    dir_name = os.path.dirname(audio_path)
    base_name = Path(audio_path).stem
    out_pattern = os.path.join(dir_name, f"{base_name}_part%03d.opus")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", audio_path,
        "-c", "copy",
        "-f", "segment",
        "-segment_time", str(segment_time_seconds),
        "-reset_timestamps", "1",
        out_pattern
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        chunks = sorted([
            os.path.join(dir_name, f)
            for f in os.listdir(dir_name)
            if f.startswith(f"{base_name}_part") and f.endswith(".opus")
        ])
        return chunks if chunks else [audio_path]
    except Exception:
        return [audio_path]
