import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

# Fonts directory
FONTS_DIR = BASE_DIR / "app" / "static" / "fonts"
FONTS_DIR.mkdir(parents=True, exist_ok=True)
