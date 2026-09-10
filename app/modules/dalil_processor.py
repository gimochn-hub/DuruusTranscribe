import os
import json
import re
from typing import Optional, Callable, Dict, Any, List
from pydantic import BaseModel, Field
import json_repair
from app.config import GEMINI_API_KEY

class SectionModel(BaseModel):
    sub_judul: str
    konten: str

class TranskripKajianModel(BaseModel):
    judul_kajian: str
    daftar_isi: List[str]
    sections: List[SectionModel]

class DalilPendukungModel(BaseModel):
    arab: str
    terjemahan: str
    sumber: str
    relevansi: str
    lathaif: Optional[str] = ""

class TadabburLathaifModel(BaseModel):
    """Detail tadabbur ayat/hadits & lathaif kebahasaan/tafsir tahlily ala Dorar Saniyyah."""
    fokus_dalil: str = ""       # Potongan ayat / hadits yang ditadabburi
    tinjauan_bahasa: str = ""   # Analisis kebahasaan (mufradat, balaghah, uslub)
    lathaif_hikmah: str = ""    # Lathaif tafsiriyyah & rahasia makna mendalam

class IntegratedSectionModel(BaseModel):
    """Satu topik ringkasan kajian lengkap — modul pembelajaran mandiri terintegrasi."""
    sub_judul: str
    ringkasan: str                                              # Ringkasan isi topik (sertakan dalil pemateri dengan [DALIL] & [TERJEMAHAN])
    refleksi: str                                               # Refleksi kehidupan nyata zaman now khusus topik ini
    dalil_pendukung: List[DalilPendukungModel] = Field(default_factory=list)   # MINIMAL 3 dalil pendukung
    tadabbur_lathaif: List[TadabburLathaifModel] = Field(default_factory=list) # Detail tadabbur & lathaif
    aksi_nyata: List[str] = Field(default_factory=list)         # Aksi konkret pengamalan harian

class RingkasanKajianModel(BaseModel):
    judul_kajian: str
    quick_takeaways: List[str] = Field(default_factory=list)
    bagan_mermaid: str = ""
    integrated_sections: List[IntegratedSectionModel] = Field(default_factory=list)

    # Backward-compatibility fields untuk format legacy / skema lama
    ringkasan_sections: Optional[List[SectionModel]] = None
    refleksi_zaman_now: Optional[str] = None
    korelasi_dalil_terkait: Optional[List[DalilPendukungModel]] = None
    aksi_nyata: Optional[List[str]] = None

class DalilProcessorError(Exception):
    """Custom exception for Dalil processing errors."""
    pass

EXACT_SYSTEM_PROMPT = """Kamu adalah asisten penyusun transkrip kajian Islam. Tugasmu:
1. IDENTIFIKASI setiap bagian yang berisi bacaan ayat Al-Qur'an atau hadits (teks Arab). Salin bagian ini PERSIS apa adanya dari transkrip asli, huruf demi huruf. JANGAN mengubah, meringkas, memperbaiki, atau menghilangkan SATU KARAKTER PUN dari teks Arab ini, bahkan jika terlihat ada kesalahan ejaan hasil transkripsi otomatis. Bungkus setiap bagian ini dengan marker: [DALIL]...teks asli...[/DALIL]
2. Untuk setiap [DALIL], jika dari konteks kajian TIDAK ada penjelasan arti yang disampaikan ustadz, tambahkan baris terjemahan singkat setelahnya dengan marker terpisah: [TERJEMAHAN]...[/TERJEMAHAN]. Jika ustadz sudah menjelaskan artinya dalam ceramah, JANGAN tambahkan terjemahan duplikat.
3. RAPIKAN bagian narasi Bahasa Indonesia di SEKITAR dalil: hapus kata pengisi (eee, anu, jadi gini), hapus pengulangan, susun jadi paragraf yang enak dibaca. JANGAN mengubah makna atau membuang isi substantif ceramah, hanya membersihkan cara penyampaiannya.
4. Susun seluruh transkrip menjadi struktur dengan sub-judul per topik (H2) berdasarkan pergantian pembahasan yang natural.
5. Buat DAFTAR ISI di awal berupa daftar sub-judul yang kamu buat di langkah 4.
6. Output HARUS dalam format JSON dengan struktur:
{
"judul_kajian": "...",
"daftar_isi": ["sub-judul 1", "sub-judul 2", ...],
"sections": [
{"sub_judul": "...", "konten": "teks dengan marker [DALIL] dan [TERJEMAHAN] di dalamnya"}
]
}
ATURAN MUTLAK: jika ragu apakah suatu bagian adalah dalil atau bukan, PERLAKUKAN SEBAGAI DALIL (salin verbatim) daripada berisiko mengubah teks agama. Lebih baik terlalu hati-hati daripada menghilangkan sesuatu yang seharusnya dijaga utuh."""

SUMMARY_SYSTEM_PROMPT = """Kamu adalah asisten ahli penyusun ringkasan kajian Islam komprehensif yang cerdas, mendalam secara ilmiah (tafsir tahlily/Dorar Saniyyah), dan sangat kontekstual serta aplikatif untuk kehidupan modern.

ATURAN MUTLAK PRESERVASI DALIL:
- Setiap ayat Al-Qur'an, hadits, atau doa berbahasa Arab yang disebutkan ustadz HARUS dipertahankan 100% verbatim (huruf demi huruf) di dalam tag [DALIL]...[/DALIL]. DILARANG mengubah, meringkas, atau memotong satu karakter pun!
- Jika ustadz tidak menyertakan artinya, tambahkan tag [TERJEMAHAN]...[/TERJEMAHAN] tepat setelahnya.

STRUKTUR UTAMA RINGKASAN:
Kajian disusun secara TERINTEGRASI per poin/topik pembelajaran. Jangan memisah-misahkan ringkasan di awal lalu refleksi atau dalil di tempat terpisah! Setiap topik adalah satu "paket utuh" (modul belajar mandiri).

TUGASMU:
1. QUICK_TAKEAWAYS: Buat 3-5 poin intisari super singkat (1 kalimat padat per poin) — saripati keseluruhan kajian.

2. BAGAN_MERMAID: Diagram mindmap keseluruhan materi kajian (Mermaid.js valid). Format:
mindmap
  root((Judul Singkat))
    Topik 1
      Sub 1
      Sub 2
    Topik 2
Maksimal 20 node.

3. INTEGRATED_SECTIONS (INTI DARI TUGAS):
Bagi kajian menjadi 3-6 topik/poin utama. Untuk SETIAP topik, isi 5 pilar wajib berikut:
   a. sub_judul: Judul topik/poin ringkasan yang jelas dan menarik.
   b. ringkasan: Penjelasan inti materi secara padat dan berbobot (2-4 paragraf). Masukkan dalil yang dibacakan ustadz dengan tag [DALIL]...[/DALIL] dan [TERJEMAHAN]...[/TERJEMAHAN].
   c. refleksi: 1-2 paragraf refleksi kontekstual zaman now yang relate dengan kehidupan nyata sehari-hari. Hubungkan secara spesifik dengan fenomena modern (tekanan kerja, godaan media sosial, FOMO, overthinking, kesehatan mental, dinamika keluarga, integritas di era digital, dll).
   d. dalil_pendukung: MINIMAL 3 dalil (atau lebih) penguat berupa ayat Al-Qur'an dan/atau hadits shahih yang relevan dengan topik ini.
      Setiap item berisi:
      - arab: Teks Arab lengkap berharakat
      - terjemahan: Terjemahan bahasa Indonesia yang jelas
      - sumber: Nama Surat & Nomor Ayat (misal: "QS. Al-Baqarah: 153") atau Perawi & Nomor Hadits (misal: "HR. Bukhari no. 6412")
      - relevansi: Penjelasan 1-2 kalimat mengapa dalil ini memperkuat topik
   e. tadabbur_lathaif: 1-2 telaah tadabbur mendalam ayat/hadits ala tafsir tahlily dan Lathaif Dorar Saniyyah:
      - fokus_dalil: Potongan lafadz ayat atau hadits yang ditadabburi
      - tinjauan_bahasa: Analisis kebahasaan (pilihan mufradat/kosakata unik, balaghah, keindahan uslub bahasa Arab)
      - lathaif_hikmah: Rahasia makna mendalam (lathaif Qur'aniyyah/haditsiyyah), kelembutan hikmah, atau sudut pandang tafsir tahlily yang memperkaya pemahaman
   f. aksi_nyata: 2-4 langkah aksi konkret dan praktis yang bisa langsung diamalkan sehari-hari terkait poin ini (bukan saran klise abstrak, melainkan kebiasaan/tindakan nyata yang bisa dimulai hari ini).

Output WAJIB berupa format JSON valid berikut:
{
  "judul_kajian": "...",
  "quick_takeaways": ["Poin 1", "Poin 2", ...],
  "bagan_mermaid": "mindmap\\n  root((...))\\n    ...",
  "integrated_sections": [
    {
      "sub_judul": "Poin 1: ...",
      "ringkasan": "Uraian materi... [DALIL]...[/DALIL] [TERJEMAHAN]...[/TERJEMAHAN]",
      "refleksi": "Refleksi mendalam yang relate dengan kehidupan sekarang...",
      "dalil_pendukung": [
        {"arab": "...", "terjemahan": "...", "sumber": "QS...", "relevansi": "..."},
        {"arab": "...", "terjemahan": "...", "sumber": "HR...", "relevansi": "..."},
        {"arab": "...", "terjemahan": "...", "sumber": "HR...", "relevansi": "..."}
      ],
      "tadabbur_lathaif": [
        {
          "fokus_dalil": "...",
          "tinjauan_bahasa": "Tinjauan pemilihan lafadz...",
          "lathaif_hikmah": "Lathaif dan rahasia makna tafsir tahlily..."
        }
      ],
      "aksi_nyata": [
        "Langkah konkret 1",
        "Langkah konkret 2"
      ]
    }
  ]
}"""

def get_gemini_client(api_key: Optional[str] = None):
    """Initialize Google GenAI client."""
    key = (api_key or GEMINI_API_KEY or "").strip()
    if not key:
        raise DalilProcessorError(
            "Gemini API Key belum dikonfigurasi. Mohon isi GEMINI_API_KEY di file .env atau sertakan API key."
        )
    return key

def clean_json_text(raw_text: str) -> str:
    """Strip markdown code block fences if present in model output."""
    raw_text = raw_text.strip()
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]
    elif raw_text.startswith("```"):
        raw_text = raw_text[3:]
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]
    return raw_text.strip()


def robust_json_parse(raw_text: str) -> Dict[str, Any]:
    """
    Parse JSON output from Gemini with multi-layered fault tolerance:
    1. Strips markdown fences (```json ... ```) even with preamble or postamble.
    2. json.loads with strict=False (handles unescaped control chars like literal newlines, tabs).
    3. json_repair (handles unescaped quotes, trailing commas, thinking mode leakage).
    4. Balanced { ... } object extraction with strict=False and json_repair fallbacks.
    5. Fallback regex sanitization for any remaining illegal ASCII control characters.
    """
    if not raw_text or not raw_text.strip():
        raise DalilProcessorError("Respons dari Gemini kosong.")

    text = raw_text.strip()

    # Step 1: Strip markdown code block fences if present
    if "```json" in text:
        parts = text.split("```json", 1)[1]
        if "```" in parts:
            text = parts.split("```", 1)[0].strip()
        else:
            text = parts.strip()
    elif "```" in text:
        parts = text.split("```", 1)[1]
        if "```" in parts:
            text = parts.split("```", 1)[0].strip()
        else:
            text = parts.strip()

    # Step 2: Try standard json.loads with strict=False (allows unescaped control chars like \n, \t)
    try:
        data = json.loads(text, strict=False)
        if isinstance(data, dict):
            return data
        elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            return data[0]
    except Exception:
        pass

    # Step 3: Try json_repair on text
    try:
        data = json_repair.loads(text)
        if isinstance(data, dict):
            return data
        elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            return data[0]
    except Exception:
        pass

    # Step 4: Extract first balanced { ... } block
    start = text.find('{')
    if start != -1:
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(text[start:], start=start):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        data = json.loads(candidate, strict=False)
                        if isinstance(data, dict):
                            return data
                    except Exception:
                        pass
                    try:
                        data = json_repair.loads(candidate)
                        if isinstance(data, dict):
                            return data
                        elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                            return data[0]
                    except Exception:
                        pass

    # Step 5: Try json_repair directly on raw_text
    try:
        data = json_repair.loads(raw_text)
        if isinstance(data, dict):
            return data
        elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            return data[0]
    except Exception:
        pass

    # Step 6: Regex sanitization of illegal control characters (ASCII 0-31 except \t, \n, \r)
    try:
        sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        data = json.loads(sanitized, strict=False)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    raise DalilProcessorError(
        f"Gemini mengembalikan format JSON tidak valid.\nRaw Response:\n{raw_text[:500]}"
    )


def extract_first_json_object(raw_text: str) -> str:
    """Backward compatibility helper: parse robustly and serialize back to JSON string."""
    data = robust_json_parse(raw_text)
    return json.dumps(data, ensure_ascii=False)


def process_transcript_with_gemini(
    raw_transcript: str,
    metadata_hint: Optional[Dict[str, str]] = None,
    api_key: Optional[str] = None,
    model_name: str = "gemini-2.5-flash",
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> Dict[str, Any]:
    """
    Process raw transcript with Gemini API using the exact required prompt.
    Returns structured JSON with verbatim dalil preservation.
    """
    if not raw_transcript or not raw_transcript.strip():
        raise DalilProcessorError("Transkrip mentah kosong, tidak ada teks untuk diproses.")

    key = get_gemini_client(api_key)

    if progress_callback:
        progress_callback(20.0, "Menghubungkan ke Gemini API untuk preservasi dalil & restrukturisasi...")

    meta_info = ""
    if metadata_hint:
        if metadata_hint.get("judul"):
            meta_info += f"\nJudul Referensi: {metadata_hint['judul']}"
        if metadata_hint.get("ustadz"):
            meta_info += f"\nNama Ustadz: {metadata_hint['ustadz']}"

    user_prompt = f"""Berikut adalah transkrip mentah kajian untuk diproses:{meta_info}

--- TRANSKRIP MENTAH ---
{raw_transcript}
--- AKHIR TRANSKRIP MENTAH ---

Ingat: Wajib output JSON valid sesuai format yang ditentukan. Pertahankan teks Arab [DALIL] PERSIS huruf demi huruf tanpa perubahan apa pun!"""

    try:
        # Try new google-genai SDK first
        try:
            from google import genai
            from google.genai import types
            
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=EXACT_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.2,
                )
            )
            response_text = response.text
        except ImportError:
            # Fallback to google.generativeai
            import google.generativeai as genai
            genai.configure(api_key=key)
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=EXACT_SYSTEM_PROMPT,
                generation_config={"response_mime_type": "application/json", "temperature": 0.2}
            )
            response = model.generate_content(user_prompt)
            response_text = response.text

        if progress_callback:
            progress_callback(70.0, "Memvalidasi struktur JSON & marker dalil...")

        # Parse & validate JSON with multi-layer fault tolerance
        data = robust_json_parse(response_text)
        
        # Validate schema via Pydantic
        structured_data = TranskripKajianModel.model_validate(data).model_dump()

        # If user provided explicit title in metadata, use it as priority if model gave generic title
        if metadata_hint and metadata_hint.get("judul"):
            structured_data["judul_kajian"] = metadata_hint["judul"]

        if progress_callback:
            progress_callback(100.0, "Restrukturisasi kajian & preservasi dalil selesai.")

        return structured_data

    except DalilProcessorError:
        raise
    except Exception as e:
        raise DalilProcessorError(f"Terjadi kesalahan saat memproses dalil dengan Gemini: {str(e)}")

def _call_gemini(
    key: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str
) -> str:
    """Internal helper: call Gemini API with new or legacy SDK."""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                temperature=0.3,
            )
        )
        return response.text
    except ImportError:
        import google.generativeai as genai
        genai.configure(api_key=key)
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_prompt,
            generation_config={"response_mime_type": "application/json", "temperature": 0.3}
        )
        response = model.generate_content(user_prompt)
        return response.text

def process_summary_with_gemini(
    raw_transcript: str,
    metadata_hint: Optional[Dict[str, str]] = None,
    api_key: Optional[str] = None,
    model_name: str = "gemini-2.5-flash",
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> Dict[str, Any]:
    """
    Process raw transcript into a rich summary with Gemini AI.
    Returns structured JSON: quick_takeaways, bagan_mermaid, ringkasan_sections,
    refleksi_zaman_now, korelasi_dalil_terkait, aksi_nyata.
    All Arabic dalil text is preserved verbatim via [DALIL] markers.
    """
    if not raw_transcript or not raw_transcript.strip():
        raise DalilProcessorError("Transkrip mentah kosong, tidak ada teks untuk diproses.")

    key = get_gemini_client(api_key)

    if progress_callback:
        progress_callback(20.0, "Menghubungkan ke Gemini API untuk menyusun ringkasan & refleksi cerdas...")

    meta_info = ""
    if metadata_hint:
        if metadata_hint.get("judul"):
            meta_info += f"\nJudul Referensi: {metadata_hint['judul']}"
        if metadata_hint.get("ustadz"):
            meta_info += f"\nNama Ustadz: {metadata_hint['ustadz']}"

    user_prompt = f"""Berikut adalah transkrip mentah kajian Islam untuk diproses menjadi ringkasan cerdas:{meta_info}

--- TRANSKRIP MENTAH ---
{raw_transcript}
--- AKHIR TRANSKRIP MENTAH ---

PENTING: Output wajib JSON valid. Semua teks Arab (ayat/hadits) HARUS ada di dalam tag [DALIL]...[/DALIL] tanpa diubah satu karakter pun. Buat bagan_mermaid yang valid dan informatif!"""

    response_text = ""
    try:
        if progress_callback:
            progress_callback(40.0, "Menyusun ringkasan, refleksi zaman now & korelasi dalil...")

        response_text = _call_gemini(key, model_name, SUMMARY_SYSTEM_PROMPT, user_prompt)

        if progress_callback:
            progress_callback(80.0, "Memvalidasi struktur ringkasan & marker dalil...")

        # Parse & validate JSON with multi-layer fault tolerance
        data = robust_json_parse(response_text)

        # Validate schema via Pydantic
        structured_data = RingkasanKajianModel.model_validate(data).model_dump()

        # Override judul jika user menyediakan
        if metadata_hint and metadata_hint.get("judul"):
            structured_data["judul_kajian"] = metadata_hint["judul"]

        if progress_callback:
            progress_callback(100.0, "Ringkasan kajian & refleksi selesai disusun.")

        return structured_data

    except DalilProcessorError:
        raise
    except Exception as e:
        raise DalilProcessorError(f"Terjadi kesalahan saat menyusun ringkasan dengan Gemini: {str(e)}")

