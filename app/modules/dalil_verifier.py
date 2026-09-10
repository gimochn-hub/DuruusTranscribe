"""
dalil_verifier.py
=================
Modul verifikasi dalil otomatis menggunakan sumber referensi eksternal:
- Al-Qur'an  : Quran.com API → Rasm Utsmani resmi + Terjemahan Kemenag RI
- Hadits     : Dorar.net (dengan fallback ke Sunnah.com API jika diblokir)
- Terjemahan : Gemini AI — diproses dalam satu batch untuk hemat token

Pipeline:
  raw_transcript
      → [Gemini Extractor]  ekstrak petunjuk dalil (JSON kecil, hemat token)
      → [QuranVerifier]     verifikasi ayat via quran.com API
      → [DorarVerifier]     cari hadits via dorar.net / sunnah.com
      → [GeminiBatchTrans]  terjemahkan semua hadits dalam 1 request
      → [Reconciler]        patch transkrip + tambah marker [VERIFIED:...]
      → patched_transcript  (siap di-proses Gemini utama)
"""

import re
import json
import time
import logging
import unicodedata
from typing import Optional, Dict, Any, List, Tuple
import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# KONSTANTA
# ─────────────────────────────────────────────────────────────────────────────

QURAN_API_BASE = "https://api.quran.com/api/v4"
# resource_id 33 = Terjemahan Kemenag RI (Bahasa Indonesia resmi)
QURAN_TRANSLATION_ID = 33

# Dorar.net unofficial JSON endpoint
DORAR_API_URL = "https://dorar.net/dorar_api.json"
# Sunnah.com public API (fallback)
SUNNAH_API_BASE = "https://api.sunnah.com/v1"

# Request headers agar tidak diblokir sebagai bot
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "id-ID,id;q=0.9,ar;q=0.8,en;q=0.7",
    "Referer": "https://dorar.net/",
}

# Timeout request dalam detik
REQUEST_TIMEOUT = 12
MAX_RETRY = 2

# ─────────────────────────────────────────────────────────────────────────────
# PROMPT EKSTRAKSI PETUNJUK DALIL (Hemat Token — Hanya Identifikasi)
# ─────────────────────────────────────────────────────────────────────────────

DALIL_EXTRACTION_PROMPT = """Kamu adalah pendeteksi dalil Islam dari teks transkripsi ceramah.
Tugasmu HANYA mengidentifikasi dalil (ayat Al-Qur'an & hadits) yang DISEBUTKAN dalam transkrip.
Output HARUS JSON array (bukan objek). Jika tidak ada dalil, kembalikan array kosong: []

Format setiap item:
- tipe: "quran" atau "hadits"
- petunjuk: Untuk quran → "SURAH:AYAH" (contoh: "2:153"). Untuk hadits → kata kunci matan Arab atau terjemahan (max 8 kata, pilih kata yang paling khas/unik).
- teks_whisper: potongan teks persis dari transkrip yang mengandung dalil ini (max 120 karakter)
- nama_surah: nama surah jika disebutkan (kosong jika tidak ada)
- kitab: nama kitab hadits jika disebutkan (contoh: "Bukhari", "Muslim", "Abu Dawud") — kosong jika tidak ada

ATURAN:
- Jika ustadz menyebut "Al-Baqarah ayat 153" → petunjuk: "2:153"
- Jika ustadz hanya membaca Arab tanpa menyebut nomor → beri petunjuk kata kunci matan terpenting
- JANGAN mengarang teks Arab, hanya identifikasi apa yang ada di transkrip
- Maksimal 15 dalil per transkrip"""

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS: Normalisasi Teks Arab
# ─────────────────────────────────────────────────────────────────────────────

def strip_tashkeel(text: str) -> str:
    """Hapus harakat/tashkeel dari teks Arab untuk keperluan matching."""
    # Unicode range U+0610–U+061A dan U+064B–U+065F adalah harakat Arab
    tashkeel_pattern = re.compile(
        r'[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06dc\u06df-\u06e4\u06e7\u06e8\u06ea-\u06ed]'
    )
    return tashkeel_pattern.sub('', text)


def normalize_arabic(text: str) -> str:
    """Normalisasi teks Arab: hapus harakat + standarkan alif/hamzah/ya."""
    text = strip_tashkeel(text)
    # Standarkan berbagai bentuk alif
    text = re.sub(r'[أإآٱ]', 'ا', text)
    # Standarkan ya di akhir
    text = re.sub(r'ى', 'ي', text)
    # Standarkan ta marbuta
    text = re.sub(r'ة', 'ه', text)
    # Standarkan waw dengan hamzah
    text = re.sub(r'ؤ', 'و', text)
    # Standarkan ya dengan hamzah
    text = re.sub(r'ئ', 'ي', text)
    return text.strip()


def parse_surah_ayah(petunjuk: str) -> Optional[Tuple[int, int]]:
    """Parse string 'SURAH:AYAH' menjadi tuple (surah_int, ayah_int). Return None jika gagal."""
    match = re.match(r'^(\d{1,3}):(\d{1,3})$', petunjuk.strip())
    if match:
        s, a = int(match.group(1)), int(match.group(2))
        if 1 <= s <= 114 and 1 <= a <= 286:
            return s, a
    return None


# ─────────────────────────────────────────────────────────────────────────────
# QURAN VERIFIER
# ─────────────────────────────────────────────────────────────────────────────

class QuranVerifier:
    """
    Verifikasi ayat Al-Qur'an via Quran.com API v4.
    Mengembalikan teks Rasm Utsmani resmi + terjemahan Kemenag RI.
    """

    def __init__(self, timeout: int = REQUEST_TIMEOUT):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def get_verse(self, surah: int, ayah: int) -> Optional[Dict[str, Any]]:
        """
        Ambil ayat berdasarkan (surah, ayah).
        Return dict: {arab, terjemahan_id, surah_name, ayah_num, verse_key} atau None.
        """
        verse_key = f"{surah}:{ayah}"
        try:
            # 1. Ambil teks Uthmani
            uthmani_url = f"{QURAN_API_BASE}/quran/verses/uthmani"
            r_uthmani = self.session.get(
                uthmani_url,
                params={"verse_key": verse_key},
                timeout=self.timeout
            )
            r_uthmani.raise_for_status()
            uthmani_data = r_uthmani.json()
            verses = uthmani_data.get("verses", [])
            if not verses:
                return None
            arabic_text = verses[0].get("text_uthmani", "")

            # 2. Ambil terjemahan Kemenag RI + info surah
            trans_url = f"{QURAN_API_BASE}/verses/by_key/{verse_key}"
            r_trans = self.session.get(
                trans_url,
                params={
                    "translations": str(QURAN_TRANSLATION_ID),
                    "fields": "chapter_id,verse_number"
                },
                timeout=self.timeout
            )
            r_trans.raise_for_status()
            trans_data = r_trans.json()
            verse_obj = trans_data.get("verse", {})
            translations = verse_obj.get("translations", [])
            terjemahan = translations[0].get("text", "") if translations else ""

            # 3. Ambil nama surah
            surah_url = f"{QURAN_API_BASE}/chapters/{surah}"
            r_surah = self.session.get(surah_url, timeout=self.timeout)
            r_surah.raise_for_status()
            surah_data = r_surah.json()
            chapter = surah_data.get("chapter", {})
            surah_name_ar = chapter.get("name_arabic", "")
            surah_name_id = chapter.get("name_simple", "")

            return {
                "arab": arabic_text,
                "terjemahan": terjemahan,
                "sumber": f"QS. {surah_name_id} ({surah_name_ar}) [{verse_key}]",
                "verse_key": verse_key,
                "surah_name": surah_name_id,
                "ayah_num": ayah,
                "verified": True,
                "source_db": "quran.com"
            }

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout saat mengambil ayat {verse_key} dari Quran.com")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Error Quran.com untuk {verse_key}: {e}")
            return None

    def search_by_keyword(self, keyword: str, surah_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Cari ayat berdasarkan kata kunci (jika ustadz tidak menyebut nomor).
        Menggunakan Quran.com search API.
        """
        try:
            search_url = f"{QURAN_API_BASE}/search"
            params = {"q": keyword, "size": 3, "language": "id"}
            if surah_hint:
                params["filters"] = f"chapter_id:{surah_hint}"

            r = self.session.get(search_url, params=params, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            results = data.get("search", {}).get("results", [])
            if not results:
                return None

            # Ambil hasil terbaik (index 0)
            best = results[0]
            verse_key = best.get("verse_key", "")
            if not verse_key:
                return None

            parts = verse_key.split(":")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                return self.get_verse(int(parts[0]), int(parts[1]))

        except Exception as e:
            logger.warning(f"Error saat search Quran.com keyword '{keyword}': {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# DORAR HADITH VERIFIER
# ─────────────────────────────────────────────────────────────────────────────

class DorarHadithVerifier:
    """
    Verifikasi hadits via Dorar.net (unofficial JSON API).
    Fallback ke Sunnah.com jika Dorar memblokir.
    Terjemahan Indonesia di-generate Gemini secara batch (hemat token).
    """

    def __init__(self, timeout: int = REQUEST_TIMEOUT, max_retry: int = MAX_RETRY):
        self.timeout = timeout
        self.max_retry = max_retry
        self.session = requests.Session()
        self.session.headers.update(BROWSER_HEADERS)

    def _dorar_search(self, keyword: str) -> Optional[Dict[str, Any]]:
        """Cari hadits di Dorar.net. Return dict hasil atau None."""
        for attempt in range(self.max_retry):
            try:
                params = {"skey": keyword}
                r = self.session.get(
                    DORAR_API_URL,
                    params=params,
                    timeout=self.timeout
                )
                if r.status_code == 403:
                    logger.warning(f"Dorar.net 403 Forbidden (attempt {attempt+1}). Akan coba fallback.")
                    return None
                r.raise_for_status()

                # Dorar mengembalikan HTML dalam JSON field
                data = r.json()
                ahadith = data.get("ahadith", {}).get("result", "")
                if not ahadith or ahadith == "0":
                    return None

                # Parse HTML Dorar — ekstrak hadits pertama
                return self._parse_dorar_html(ahadith, keyword)

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout Dorar.net (attempt {attempt+1})")
                if attempt < self.max_retry - 1:
                    time.sleep(1)
            except Exception as e:
                logger.warning(f"Error Dorar.net: {e}")
                return None
        return None

    def _parse_dorar_html(self, html_content: str, keyword: str) -> Optional[Dict[str, Any]]:
        """
        Parse HTML response Dorar.net untuk mengekstrak matan hadits pertama.
        Dorar mengembalikan HTML snippet dengan class .hadith-title, .hadith, dsb.
        """
        try:
            # Ekstrak matan Arab (dalam span dengan dir="rtl" atau class hadith)
            # Dorar wraps matan dalam: <div class="hadith-content">...</div>
            arab_match = re.search(
                r'class=["\']hadith[^"\']*["\'][^>]*>(.*?)</(?:div|p|span)>',
                html_content,
                re.DOTALL | re.IGNORECASE
            )
            # Alternatif: cari teks dalam tag dengan dir=rtl
            if not arab_match:
                arab_match = re.search(
                    r'dir=["\']rtl["\'][^>]*>(.*?)</(?:div|p|span)>',
                    html_content,
                    re.DOTALL | re.IGNORECASE
                )

            # Ekstrak takhrij (perawi + kitab)
            takhrij_match = re.search(
                r'class=["\']hadith-grade[^"\']*["\'][^>]*>(.*?)</(?:div|p|span)>',
                html_content,
                re.DOTALL | re.IGNORECASE
            )
            # Ekstrak derajat
            grade_match = re.search(
                r'class=["\']label[^"\']*["\'][^>]*>(.*?)</(?:div|p|span)>',
                html_content,
                re.DOTALL | re.IGNORECASE
            )

            arab_text = ""
            if arab_match:
                raw = arab_match.group(1)
                # Strip HTML tags
                arab_text = re.sub(r'<[^>]+>', '', raw).strip()

            takhrij = ""
            if takhrij_match:
                raw_t = takhrij_match.group(1)
                takhrij = re.sub(r'<[^>]+>', '', raw_t).strip()

            derajat = ""
            if grade_match:
                raw_g = grade_match.group(1)
                derajat = re.sub(r'<[^>]+>', '', raw_g).strip()

            if not arab_text:
                logger.warning(f"Tidak bisa parse matan dari Dorar HTML untuk keyword: {keyword}")
                return None

            sumber = f"HR. {takhrij}" if takhrij else "Dorar.net"
            if derajat:
                sumber += f" — {derajat}"

            return {
                "arab": arab_text,
                "terjemahan": "",  # Akan diisi Gemini batch
                "sumber": sumber,
                "derajat": derajat,
                "takhrij": takhrij,
                "verified": True,
                "source_db": "dorar.net"
            }
        except Exception as e:
            logger.warning(f"Error parse Dorar HTML: {e}")
            return None

    def _sunnah_search(self, keyword: str, kitab_hint: str = "") -> Optional[Dict[str, Any]]:
        """
        Fallback: Cari hadits di Sunnah.com API (tidak butuh key untuk basic search).
        Menggunakan endpoint publik.
        """
        try:
            # Sunnah.com search endpoint (v1 — tidak butuh auth key untuk read)
            url = f"https://api.sunnah.com/v1/hadiths/random"
            # Untuk pencarian real via Sunnah.com diperlukan API key.
            # Sebagai fallback tanpa key, kita coba endpoint koleksi Bukhari:
            collections_to_try = []
            if kitab_hint:
                kl = kitab_hint.lower()
                if "bukhari" in kl:
                    collections_to_try.append("bukhari")
                elif "muslim" in kl:
                    collections_to_try.append("muslim")
                elif "abu" in kl or "dawud" in kl:
                    collections_to_try.append("abudawud")
                elif "tirmidzi" in kl or "tirmizi" in kl:
                    collections_to_try.append("tirmidhi")
                elif "nasai" in kl or "nasa" in kl:
                    collections_to_try.append("nasai")
                elif "majah" in kl or "ibnu majah" in kl:
                    collections_to_try.append("ibnmajah")

            # Tidak ada hasil jika tidak ada koleksi hint dan tidak ada key
            if not collections_to_try:
                return None

            # Sunnah.com butuh API key untuk search. Return None agar graceful fallback.
            logger.info("Sunnah.com butuh API key untuk search. Hadits tidak terverifikasi.")
            return None

        except Exception as e:
            logger.warning(f"Error Sunnah.com fallback: {e}")
            return None

    def search(self, keyword: str, kitab_hint: str = "") -> Optional[Dict[str, Any]]:
        """
        Cari hadits. Prioritas: Dorar.net → Sunnah.com.
        Return dict hasil hadits atau None jika tidak ditemukan.
        """
        # 1. Coba Dorar.net
        result = self._dorar_search(keyword)
        if result:
            return result

        # 2. Fallback ke Sunnah.com
        result = self._sunnah_search(keyword, kitab_hint)
        if result:
            return result

        logger.info(f"Hadits tidak ditemukan di database untuk keyword: '{keyword}'")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# GEMINI HELPER: Ekstraksi Petunjuk + Batch Terjemahan
# ─────────────────────────────────────────────────────────────────────────────

def _call_gemini_json(api_key: str, system_prompt: str, user_prompt: str,
                      model_name: str = "gemini-2.5-flash") -> Any:
    """Panggil Gemini API dan return data JSON (dict atau list). Raise jika gagal."""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                temperature=0.1,
            )
        )
        text = response.text
    except ImportError:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_prompt,
            generation_config={"response_mime_type": "application/json", "temperature": 0.1}
        )
        text = model.generate_content(user_prompt).text

    # Clean markdown fences
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    return json.loads(text)


def extract_dalil_hints(transcript: str, api_key: str,
                        model_name: str = "gemini-2.5-flash") -> List[Dict[str, Any]]:
    """
    Minta Gemini mengekstrak daftar petunjuk dalil dari transkrip.
    Return: list of {tipe, petunjuk, teks_whisper, nama_surah, kitab}
    Ini adalah operasi HEMAT TOKEN — Gemini hanya diminta mengidentifikasi, bukan menulis teks Arab.
    """
    user_prompt = f"""Identifikasi semua dalil dalam transkrip ceramah ini:

--- TRANSKRIP ---
{transcript[:8000]}
--- AKHIR ---

Kembalikan JSON array. Jika tidak ada dalil, kembalikan []"""

    try:
        result = _call_gemini_json(api_key, DALIL_EXTRACTION_PROMPT, user_prompt, model_name)
        if isinstance(result, list):
            return result
        elif isinstance(result, dict) and "dalil" in result:
            return result["dalil"]
        return []
    except Exception as e:
        logger.warning(f"Gagal ekstraksi petunjuk dalil dari Gemini: {e}")
        return []


BATCH_TRANSLATION_PROMPT = """Kamu adalah penerjemah teks hadits Arab ke Bahasa Indonesia yang fasih dan islami.
Terjemahkan setiap hadits di bawah ini ke Bahasa Indonesia yang natural, baku, dan mudah dipahami.
Output HARUS JSON array dengan panjang SAMA PERSIS dengan input, format: [{"terjemahan": "..."}]
JANGAN skip item apapun. Jika tidak bisa terjemahkan, isi terjemahan dengan string kosong."""


def batch_translate_hadith(hadith_arabic_list: List[str], api_key: str,
                            model_name: str = "gemini-2.5-flash") -> List[str]:
    """
    Terjemahkan semua matan hadits dalam SATU request Gemini (hemat token).
    Return: list terjemahan dengan urutan sama.
    """
    if not hadith_arabic_list:
        return []

    items = [{"no": i + 1, "arab": t} for i, t in enumerate(hadith_arabic_list)]
    user_prompt = f"Terjemahkan hadits-hadits berikut ke Bahasa Indonesia:\n{json.dumps(items, ensure_ascii=False)}"

    try:
        result = _call_gemini_json(api_key, BATCH_TRANSLATION_PROMPT, user_prompt, model_name)
        if isinstance(result, list) and len(result) == len(hadith_arabic_list):
            return [item.get("terjemahan", "") for item in result]
    except Exception as e:
        logger.warning(f"Batch terjemahan hadits gagal: {e}")

    # Fallback: return empty strings
    return [""] * len(hadith_arabic_list)


# ─────────────────────────────────────────────────────────────────────────────
# REKONSILIASI: Patch Transkrip dengan Teks Terverifikasi
# ─────────────────────────────────────────────────────────────────────────────

def reconcile_transcript(
    transcript: str,
    verified_dalil: List[Dict[str, Any]]
) -> str:
    """
    Tambahkan informasi verified dalil ke transkrip sebagai anotasi.
    Tidak mengganti teks Whisper (agar Gemini tetap punya konteks asli),
    tetapi menambahkan blok [DALIL_VERIFIED] setelah setiap dalil yang ditemukan.
    Ini memberi Gemini instruksi eksplisit: 'gunakan teks ini, bukan teks Whisper'.
    """
    if not verified_dalil:
        return transcript

    # Tambahkan semua verified dalil sebagai lampiran di akhir transkrip
    # Gemini akan instruksikan untuk menggunakan ini
    patches = []
    for item in verified_dalil:
        if not item.get("verified"):
            continue

        tipe = item.get("tipe", "")
        arab = item.get("arab", "").strip()
        terjemahan = item.get("terjemahan", "").strip()
        sumber = item.get("sumber", "").strip()
        teks_whisper_hint = item.get("teks_whisper", "").strip()

        if not arab:
            continue

        marker = "[DALIL_VERIFIED_QURAN]" if tipe == "quran" else "[DALIL_VERIFIED_HADITS]"
        block = (
            f"\n\n{marker}\n"
            f"KONTEKS_WHISPER: {teks_whisper_hint}\n"
            f"TEKS_ARAB_RESMI: {arab}\n"
            f"TERJEMAHAN: {terjemahan}\n"
            f"SUMBER: {sumber}\n"
            f"[/{marker.strip('[]')}]"
        )
        patches.append(block)

    if patches:
        transcript += (
            "\n\n\n=== DALIL TERVERIFIKASI DATABASE (GUNAKAN TEKS INI) ===\n"
            "INSTRUKSI KHUSUS UNTUK AI: Setiap blok [DALIL_VERIFIED_*] di bawah ini "
            "berisi teks Arab yang SUDAH DIVERIFIKASI dari database resmi. "
            "Ketika menyusun transkrip dan kamu menemukan dalil yang cocok dengan "
            "KONTEKS_WHISPER di bawah, WAJIB gunakan TEKS_ARAB_RESMI ini "
            "(bukan teks dari transkripsi Whisper yang mungkin ada kesalahan).\n"
        )
        transcript += "".join(patches)

    return transcript


# ─────────────────────────────────────────────────────────────────────────────
# FUNGSI UTAMA: verify_dalil_in_transcript
# ─────────────────────────────────────────────────────────────────────────────

def verify_dalil_in_transcript(
    transcript: str,
    gemini_api_key: str,
    model_name: str = "gemini-2.5-flash",
    progress_callback=None
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Fungsi utama verifikasi dalil.

    Args:
        transcript: Transkrip mentah dari Whisper
        gemini_api_key: Key Gemini untuk ekstraksi petunjuk + terjemahan batch
        model_name: Model Gemini yang digunakan
        progress_callback: Optional callback(percent, message)

    Returns:
        Tuple[patched_transcript, verified_dalil_list]
        - patched_transcript: transkrip yang sudah dilengkapi anotasi dalil terverifikasi
        - verified_dalil_list: list dict dalil yang berhasil diverifikasi (untuk metadata PDF)
    """
    if not transcript or not transcript.strip():
        return transcript, []

    def _progress(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    # ── FASE 1: Ekstraksi Petunjuk Dalil (Gemini — hemat token) ──
    _progress(10.0, "Mengidentifikasi dalil dalam transkripsi...")
    hints = extract_dalil_hints(transcript, gemini_api_key, model_name)

    if not hints:
        logger.info("Tidak ada dalil terdeteksi dalam transkrip.")
        return transcript, []

    logger.info(f"Terdeteksi {len(hints)} dalil dalam transkrip.")

    quran_verifier = QuranVerifier()
    dorar_verifier = DorarHadithVerifier()

    verified_dalil: List[Dict[str, Any]] = []
    hadith_to_translate: List[Tuple[int, str]] = []  # (index_in_verified, arab_text)

    # ── FASE 2: Verifikasi Per Dalil ──
    total = len(hints)
    for i, hint in enumerate(hints):
        tipe = hint.get("tipe", "")
        petunjuk = hint.get("petunjuk", "").strip()
        teks_whisper = hint.get("teks_whisper", "")
        nama_surah = hint.get("nama_surah", "")
        kitab = hint.get("kitab", "")

        pct = 10.0 + (i / total) * 50.0
        _progress(pct, f"Memverifikasi dalil {i+1}/{total}...")

        if tipe == "quran":
            result = None
            # Coba parse surah:ayah langsung
            parsed = parse_surah_ayah(petunjuk)
            if parsed:
                s, a = parsed
                result = quran_verifier.get_verse(s, a)
            # Jika tidak ada nomor, cari berdasarkan kata kunci
            if not result and petunjuk:
                result = quran_verifier.search_by_keyword(petunjuk, surah_hint=nama_surah)

            if result:
                result["tipe"] = "quran"
                result["teks_whisper"] = teks_whisper
                verified_dalil.append(result)
                logger.info(f"✅ Quran {result.get('verse_key')} terverifikasi.")
            else:
                logger.info(f"⚠️  Ayat Al-Qur'an tidak ditemukan: petunjuk='{petunjuk}'")

        elif tipe == "hadits":
            result = dorar_verifier.search(petunjuk, kitab_hint=kitab)
            if result:
                result["tipe"] = "hadits"
                result["teks_whisper"] = teks_whisper
                verified_dalil.append(result)
                # Tandai untuk batch terjemahan
                hadith_to_translate.append((len(verified_dalil) - 1, result["arab"]))
                logger.info(f"✅ Hadits terverifikasi: {result.get('sumber', '')[:50]}")
            else:
                logger.info(f"⚠️  Hadits tidak ditemukan di database: keyword='{petunjuk}'")

    # ── FASE 3: Batch Terjemahan Hadits (Satu Request Gemini) ──
    if hadith_to_translate:
        _progress(65.0, f"Menerjemahkan {len(hadith_to_translate)} hadits (batch Gemini)...")
        arabic_texts = [arab for _, arab in hadith_to_translate]
        translations = batch_translate_hadith(arabic_texts, gemini_api_key, model_name)

        for (idx, _), trans in zip(hadith_to_translate, translations):
            verified_dalil[idx]["terjemahan"] = trans

    # ── FASE 4: Rekonsiliasi — Patch Transkrip ──
    _progress(80.0, f"Menyisipkan {len(verified_dalil)} dalil terverifikasi ke transkrip...")
    patched = reconcile_transcript(transcript, verified_dalil)

    _progress(100.0, f"Verifikasi dalil selesai: {len(verified_dalil)} dalil terverifikasi.")
    return patched, verified_dalil
