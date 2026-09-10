import os
import urllib.request
from pathlib import Path
from app.config import FONTS_DIR

AMIRI_REGULAR_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/amiri/Amiri-Regular.ttf"
AMIRI_BOLD_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/amiri/Amiri-Bold.ttf"

def ensure_arabic_fonts() -> dict:
    """Ensure Amiri fonts exist locally in FONTS_DIR. Downloads them if missing."""
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    regular_path = FONTS_DIR / "Amiri-Regular.ttf"
    bold_path = FONTS_DIR / "Amiri-Bold.ttf"

    headers = {'User-Agent': 'Mozilla/5.0'}

    if not regular_path.exists() or regular_path.stat().st_size < 1000:
        try:
            req = urllib.request.Request(AMIRI_REGULAR_URL, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp, open(regular_path, 'wb') as f:
                f.write(resp.read())
        except Exception as e:
            print(f"Warning: Failed to download Amiri-Regular: {e}")

    if not bold_path.exists() or bold_path.stat().st_size < 1000:
        try:
            req = urllib.request.Request(AMIRI_BOLD_URL, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp, open(bold_path, 'wb') as f:
                f.write(resp.read())
        except Exception as e:
            print(f"Warning: Failed to download Amiri-Bold: {e}")

    return {
        "regular": str(regular_path) if regular_path.exists() else None,
        "bold": str(bold_path) if bold_path.exists() else None,
    }
