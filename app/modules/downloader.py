import os
import re
from pathlib import Path
from typing import Callable, Optional, Dict, Any
import yt_dlp

class DownloadError(Exception):
    """Custom exception for download errors with clear messages."""
    pass

def is_youtube_url(url: str) -> bool:
    """Check if the provided string is a plausible YouTube URL."""
    if not url or not isinstance(url, str):
        return False
    youtube_regex = (
        r'(https?://)?(www\.)?'
        r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|shorts/|.+\?v=)?([^&=%\?]{11})'
    )
    return bool(re.match(youtube_regex, url.strip()))

def validate_youtube_url(url: str) -> Dict[str, Any]:
    """
    Validate YouTube URL using yt-dlp in simulate mode.
    Returns metadata dict if valid, or raises DownloadError.
    """
    url = url.strip()
    if not is_youtube_url(url):
        raise DownloadError("Format link YouTube tidak valid. Mohon periksa kembali URL yang dimasukkan.")

    cookies_file = Path(__file__).resolve().parent.parent.parent / "cookies.txt"
    ydl_opts = {
        'simulate': True,
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'skip_download': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        },
    }
    if cookies_file.exists():
        ydl_opts['cookiefile'] = str(cookies_file)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise DownloadError("Tidak dapat mengekstrak informasi dari link YouTube tersebut.")
            
            if info.get('is_live'):
                raise DownloadError("Video yang sedang live streaming tidak dapat diproses langsung.")

            title = info.get('title', 'Kajian Tanpa Judul')
            uploader = info.get('uploader') or info.get('channel') or 'Ustadz'
            duration = info.get('duration', 0) # in seconds

            return {
                'valid': True,
                'id': info.get('id'),
                'title': title,
                'uploader': uploader,
                'duration': duration,
                'webpage_url': info.get('webpage_url', url)
            }
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e).lower()
        if "private video" in error_msg:
            raise DownloadError("Video bersifat privat dan tidak dapat diakses.")
        elif "sign in" in error_msg or "age" in error_msg:
            raise DownloadError("Video memiliki batasan usia atau memerlukan login YouTube.")
        elif "region" in error_msg or "country" in error_msg or "geo" in error_msg:
            raise DownloadError("Video dibatasi wilayah geografis (region-locked).")
        elif "not available" in error_msg or "deleted" in error_msg:
            raise DownloadError("Video tidak tersedia atau telah dihapus.")
        elif "403" in error_msg or "forbidden" in error_msg:
            raise DownloadError("YouTube memblokir akses unduhan audio (HTTP 403 Forbidden). Coba ulangi kembali.")
        else:
            raise DownloadError(f"Gagal memvalidasi video YouTube: {str(e)}")
    except Exception as e:
        if isinstance(e, DownloadError):
            raise
        raise DownloadError(f"Terjadi kesalahan saat memvalidasi URL: {str(e)}")

def download_youtube_audio(
    url: str,
    output_dir: str,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> Dict[str, Any]:
    """
    Download audio from YouTube URL using yt-dlp into output_dir.
    Returns metadata including path to downloaded audio.
    """
    output_path_template = os.path.join(output_dir, "%(id)s.%(ext)s")

    def ydl_progress_hook(d):
        if progress_callback and d.get('status') == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                percent = min(99.0, (downloaded / total) * 100.0)
                speed = d.get('speed')
                speed_str = f" ({speed/1024/1024:.1f} MB/s)" if speed else ""
                progress_callback(percent, f"Mengunduh audio dari YouTube... {percent:.0f}%{speed_str}")

    cookies_file = Path(__file__).resolve().parent.parent.parent / "cookies.txt"
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path_template,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [ydl_progress_hook],
        'postprocessors': [], # Keep raw audio download, converter.py will handle ffmpeg to .opus
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        },
        'socket_timeout': 30,
        'retries': 5,
        'fragment_retries': 10,
    }
    if cookies_file.exists():
        ydl_opts['cookiefile'] = str(cookies_file)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if progress_callback:
                progress_callback(5.0, "Memulai unduhan audio dari YouTube...")
            
            info = ydl.extract_info(url, download=True)
            if not info:
                raise DownloadError("Gagal mengunduh audio dari YouTube.")

            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                # Search for any file with id in output_dir
                vid_id = info.get('id', '')
                matching = [f for f in os.listdir(output_dir) if vid_id in f]
                if matching:
                    filename = os.path.join(output_dir, matching[0])
                else:
                    raise DownloadError("File audio hasil unduhan tidak ditemukan di server.")

            if progress_callback:
                progress_callback(100.0, "Unduhan audio YouTube selesai.")

            return {
                'file_path': filename,
                'title': info.get('title', 'Kajian Tanpa Judul'),
                'uploader': info.get('uploader') or info.get('channel') or '',
                'duration': info.get('duration', 0),
                'webpage_url': info.get('webpage_url', url)
            }
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e).lower()
        if "private video" in error_msg:
            raise DownloadError("Video bersifat privat dan tidak dapat diakses.")
        elif "sign in" in error_msg:
            raise DownloadError("Video memerlukan autentikasi login YouTube.")
        elif "region" in error_msg:
            raise DownloadError("Video dibatasi wilayah geografis.")
        else:
            raise DownloadError(f"Gagal mengunduh audio YouTube: {str(e)}")
    except Exception as e:
        if isinstance(e, DownloadError):
            raise
        raise DownloadError(f"Terjadi kesalahan saat mengunduh audio: {str(e)}")
