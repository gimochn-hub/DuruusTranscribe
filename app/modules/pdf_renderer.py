import os
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
import arabic_reshaper
from bidi.algorithm import get_display

from app.modules.font_loader import ensure_arabic_fonts
from app.config import FONTS_DIR

class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and display total page count."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count: int):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))

        # Footer
        footer_text = f"Halaman {self._pageNumber} dari {page_count}  •  Kajian Transcriber"
        self.drawRightString(A4[0] - 40, 25, footer_text)
        self.drawString(40, 25, "Dokumen Kajian Islam — Dalil Terverifikasi Database Resmi")

        # Top subtle line on pages after page 1
        if self._pageNumber > 1:
            self.setStrokeColor(colors.HexColor("#e2e8f0"))
            self.setLineWidth(0.5)
            self.line(40, A4[1] - 30, A4[0] - 40, A4[1] - 30)

        self.restoreState()

_fonts_registered = False

def register_fonts():
    """Register custom TTF fonts for English, Indonesian, and Arabic."""
    global _fonts_registered
    if _fonts_registered:
        return

    font_paths = ensure_arabic_fonts()
    regular_ttf = font_paths.get("regular")
    bold_ttf = font_paths.get("bold")

    if regular_ttf and os.path.exists(regular_ttf):
        try:
            pdfmetrics.registerFont(TTFont("Amiri", regular_ttf))
            if bold_ttf and os.path.exists(bold_ttf):
                pdfmetrics.registerFont(TTFont("Amiri-Bold", bold_ttf))
            else:
                pdfmetrics.registerFont(TTFont("Amiri-Bold", regular_ttf))
            from reportlab.pdfbase.pdfmetrics import registerFontFamily
            registerFontFamily("Amiri", normal="Amiri", bold="Amiri-Bold", italic="Amiri", boldItalic="Amiri-Bold")
        except Exception as e:
            print(f"Warning: Failed to register Amiri font: {e}")

    _fonts_registered = True

# Emoji regex covering all Unicode emoji ranges to prevent black boxes (tofu) in ReportLab
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"  # enclosed characters
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA70-\U0001FAFF"  # symbols and pictographs extended-a
    "\U00002600-\U000026FF"  # misc symbols (lightning, etc.)
    "\U00002B00-\U00002BFF"  # misc symbols and arrows
    "]+",
    flags=re.UNICODE
)

ARABIC_CHAR_PATTERN = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]+')

def clean_pdf_text(text: str) -> str:
    """Remove emojis and normalize characters that ReportLab cannot render."""
    if not text:
        return ""
    cleaned = EMOJI_PATTERN.sub("", str(text))
    cleaned = cleaned.replace('\u200b', '').replace('\ufeff', '')
    return cleaned.strip()

def is_mostly_arabic(text: str) -> bool:
    """Check if string is primarily Arabic script."""
    if not text:
        return False
    ar_chunks = ARABIC_CHAR_PATTERN.findall(text)
    if not ar_chunks:
        return False
    ar_len = sum(len(c) for c in ar_chunks)
    total_len = len(text.replace(" ", ""))
    return (ar_len / max(1, total_len)) >= 0.4

def format_mixed_pdf_text(text: str) -> str:
    """
    Cleans emojis and reshapes any embedded Arabic words in Latin/Indonesian sentences
    so Arabic letters properly connect and render without tofu/black boxes.
    """
    if not text:
        return ""
    cleaned = clean_pdf_text(text)
    config = {
        'delete_harakat': False,
        'support_ligatures': True,
        'shift_harakat_position': False
    }
    reshaper = arabic_reshaper.ArabicReshaper(configuration=config)
    def replace_ar(match):
        chunk = match.group(0)
        try:
            r = reshaper.reshape(chunk)
            return get_display(r)
        except Exception:
            return chunk
    return ARABIC_CHAR_PATTERN.sub(replace_ar, cleaned)

def shape_arabic_text(
    text: str,
    max_line_width: float = 460.0,
    font_name: str = "Amiri",
    font_size: float = 14.0
) -> str:
    """
    Reshape Arabic text for proper ligatures, diacritics (harakat), BiDi RTL ordering,
    AND top-to-bottom line ordering.

    ReportLab is an LTR layout engine. If a long BiDi string wraps across lines inside a Paragraph,
    ReportLab places the first visual characters (which BiDi reversed to be the END of the verse)
    on Line 1, and the last characters (the START of the verse) on the bottom line. This causes
    the entire verse to render upside-down (bottom-to-top).

    Fix:
    1. Words are grouped into logical lines that fit within max_line_width (measured via stringWidth).
    2. Each line is reshaped and BiDi-ordered independently (so Line 1 is top, Line 2 is middle, etc.).
    3. Lines are joined with '<br/>' so ReportLab renders them sequentially from top to bottom.
    """
    if not text:
        return ""
    try:
        config = {
            'delete_harakat': False,
            'support_ligatures': True,
            'shift_harakat_position': False
        }
        reshaper = arabic_reshaper.ArabicReshaper(configuration=config)

        # Normalize line breaks and clean any stray emojis
        clean_text = clean_pdf_text(text).replace("<br/>", "\n").replace("<br>", "\n")
        raw_paragraphs = clean_text.split("\n")
        output_lines = []

        has_font = font_name in pdfmetrics.getRegisteredFontNames()

        for para in raw_paragraphs:
            words = para.split()
            if not words:
                continue

            current_words = []
            for word in words:
                test_words = current_words + [word]
                test_str = " ".join(test_words)
                reshaped_test = reshaper.reshape(test_str)
                if has_font:
                    try:
                        width = pdfmetrics.stringWidth(reshaped_test, font_name, font_size)
                    except Exception:
                        width = len(test_str) * font_size * 0.45
                else:
                    width = len(test_str) * font_size * 0.45

                if width <= max_line_width or not current_words:
                    current_words.append(word)
                else:
                    line_str = " ".join(current_words)
                    reshaped_line = reshaper.reshape(line_str)
                    displayed_line = get_display(reshaped_line)
                    output_lines.append(displayed_line)
                    current_words = [word]

            if current_words:
                line_str = " ".join(current_words)
                reshaped_line = reshaper.reshape(line_str)
                displayed_line = get_display(reshaped_line)
                output_lines.append(displayed_line)

        return "<br/>".join(output_lines)
    except Exception:
        return clean_pdf_text(text)

def parse_konten_markers(konten: str) -> List[Dict[str, str]]:
    """
    Parse a section's text content into sequence of chunks:
    - {'type': 'narasi', 'text': '...'}
    - {'type': 'dalil', 'text': '...'}
    - {'type': 'terjemahan', 'text': '...'}
    """
    pattern = re.compile(
        r'(\[DALIL\][\s\S]*?\[/DALIL\]|\[TERJEMAHAN\][\s\S]*?\[/TERJEMAHAN\])',
        re.IGNORECASE
    )
    parts = pattern.split(konten)
    results = []

    for part in parts:
        if not part or not part.strip():
            continue
        trimmed = part.strip()
        if trimmed.startswith("[DALIL]") and trimmed.endswith("[/DALIL]"):
            raw_dalil = trimmed[7:-8].strip()
            results.append({"type": "dalil", "text": raw_dalil})
        elif trimmed.startswith("[TERJEMAHAN]") and trimmed.endswith("[/TERJEMAHAN]"):
            raw_trans = trimmed[12:-13].strip()
            results.append({"type": "terjemahan", "text": raw_trans})
        else:
            results.append({"type": "narasi", "text": trimmed})

    return results

def render_kajian_pdf(
    data: Dict[str, Any],
    output_pdf_path: str,
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    """
    Render structured Kajian JSON to an elegant, publication-ready PDF document.
    """
    register_fonts()
    os.makedirs(os.path.dirname(os.path.abspath(output_pdf_path)), exist_ok=True)

    doc = SimpleDocTemplate(
        output_pdf_path,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=45,
        bottomMargin=45
    )

    styles = getSampleStyleSheet()
    
    # Custom Palette
    c_primary = colors.HexColor("#064e3b")     # Deep Emerald
    c_accent = colors.HexColor("#059669")      # Emerald 600
    c_dark = colors.HexColor("#1e293b")        # Slate 800
    c_muted = colors.HexColor("#64748b")       # Slate 500
    c_box_bg = colors.HexColor("#f0fdf4")      # Emerald 50
    c_box_border = colors.HexColor("#059669")  # Emerald 600

    # Custom Typography Styles
    style_main_title = ParagraphStyle(
        'MainTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=c_primary,
        alignment=TA_CENTER,
        spaceAfter=12
    )

    style_meta_label = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=c_accent
    )

    style_meta_value = ParagraphStyle(
        'MetaValue',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=c_dark
    )

    style_toc_heading = ParagraphStyle(
        'TOCHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=15,
        textColor=c_primary,
        spaceBefore=14,
        spaceAfter=8
    )

    style_toc_item = ParagraphStyle(
        'TOCItem',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=c_dark,
        leftIndent=12
    )

    style_section_h2 = ParagraphStyle(
        'SectionH2',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=17,
        textColor=c_primary,
        spaceBefore=16,
        spaceAfter=8,
        keepWithNext=True
    )

    style_narasi = ParagraphStyle(
        'Narasi',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=15,
        textColor=c_dark,
        alignment=TA_JUSTIFY,
        spaceAfter=8
    )

    # Arabic style using registered Amiri font
    has_amiri = "Amiri" in pdfmetrics.getRegisteredFontNames()
    arabic_font_name = "Amiri" if has_amiri else "Helvetica"

    style_dalil_ar = ParagraphStyle(
        'DalilArabic',
        parent=styles['Normal'],
        fontName=arabic_font_name,
        fontSize=14,
        leading=24,
        textColor=colors.HexColor("#064e3b"),
        alignment=TA_RIGHT,
        spaceBefore=4,
        spaceAfter=4
    )

    style_dalil_badge = ParagraphStyle(
        'DalilBadge',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=c_accent,
        alignment=TA_LEFT
    )

    style_terjemahan = ParagraphStyle(
        'Terjemahan',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#334155"),
        alignment=TA_JUSTIFY,
        leftIndent=8
    )

    elements = []

    # 1. Main Title
    judul = data.get("judul_kajian") or "Transkrip Kajian Islam"
    # Ambil info dalil terverifikasi
    verified_dalil_list = data.get("verified_dalil", [])
    verified_count = (metadata or {}).get("verified_count", len(verified_dalil_list))
    # Buat set sumber terverifikasi untuk lookup cepat
    verified_sources = set()
    for vd in verified_dalil_list:
        hint = vd.get("teks_whisper", "")[:60].strip()
        if hint:
            verified_sources.add(hint)
    elements.append(Paragraph(judul, style_main_title))

    # 2. Metadata Box
    ustadz = (metadata or {}).get("ustadz") or "Pemateri Kajian"
    source_url = (metadata or {}).get("source_url") or "Rekaman Audio/Video"
    tanggal = datetime.now().strftime("%d %B %Y")

    meta_table_data = [
        [
            Paragraph("<b>Penceramah / Ustadz:</b>", style_meta_label),
            Paragraph(ustadz, style_meta_value),
            Paragraph("<b>Tanggal Proses:</b>", style_meta_label),
            Paragraph(tanggal, style_meta_value),
        ],
        [
            Paragraph("<b>Sumber:</b>", style_meta_label),
            Paragraph(source_url[:50] + ("..." if len(source_url) > 50 else ""), style_meta_value),
            Paragraph("<b>Status Dalil:</b>", style_meta_label),
            Paragraph(
                f"[TERVERIFIKASI] {verified_count} Dalil Terverifikasi Database"
                if verified_count > 0
                else "Preservasi Verbatim 100%",
                style_meta_value
            ),
        ]
    ]

    meta_table = Table(meta_table_data, colWidths=[100, 160, 90, 165])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 14))

    # 3. Daftar Isi (Table of Contents)
    daftar_isi = data.get("daftar_isi", [])
    if daftar_isi:
        elements.append(Paragraph("Daftar Isi Pokok Bahasan", style_toc_heading))
        toc_items = []
        for i, item in enumerate(daftar_isi, start=1):
            toc_items.append([
                Paragraph(f"<b>{i}.</b>", style_toc_item),
                Paragraph(clean_pdf_text(item), style_toc_item)
            ])
        toc_table = Table(toc_items, colWidths=[24, 490])
        toc_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(toc_table)
        elements.append(Spacer(1, 10))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceAfter=14))

    # 4. Sections Body
    sections = data.get("sections", [])
    for idx, sec in enumerate(sections, start=1):
        sub_judul = sec.get("sub_judul", f"Bagian {idx}")
        elements.append(Paragraph(f"{idx}. {clean_pdf_text(sub_judul)}", style_section_h2))

        konten = sec.get("konten", "")
        parsed_chunks = parse_konten_markers(konten)

        for chunk in parsed_chunks:
            c_type = chunk["type"]
            c_text = chunk["text"]

            if c_type == "narasi":
                # Split multiple paragraphs if newline present
                paragraphs = [p.strip() for p in c_text.split("\n") if p.strip()]
                for p in paragraphs:
                    elements.append(Paragraph(format_mixed_pdf_text(p), style_narasi))

            elif c_type == "dalil":
                # Reshape Arabic text
                shaped_ar = shape_arabic_text(c_text, max_line_width=485, font_name=arabic_font)

                # Cek apakah dalil ini ada dalam daftar yang terverifikasi
                # dengan membandingkan potongan teks (normalisasi sederhana)
                is_verified = any(
                    vs and vs[:40].replace(' ', '') in c_text.replace(' ', '')
                    for vs in verified_sources
                ) if verified_sources else False

                if is_verified:
                    badge_label = "<b>[TERVERIFIKASI] DALIL TERVERIFIKASI</b> — Teks Arab dari Database Resmi (Quran.com / Dorar.net)"
                    badge_color = colors.HexColor("#065f46")  # Deeper green for verified
                    box_bg = colors.HexColor("#d1fae5")        # Emerald 100 — lebih cerah
                    box_border = colors.HexColor("#10b981")    # Emerald 500
                else:
                    badge_label = "<b>DALIL (AYAT AL-QUR'AN / HADITS)</b>"
                    badge_color = c_accent
                    box_bg = c_box_bg
                    box_border = c_box_border

                style_dalil_badge_dynamic = ParagraphStyle(
                    'DalilBadgeDynamic',
                    parent=style_dalil_badge,
                    textColor=badge_color
                )

                # Create Styled Callout Box with left colored border
                dalil_content = [
                    [Paragraph(badge_label, style_dalil_badge_dynamic)],
                    [Paragraph(shaped_ar, style_dalil_ar)]
                ]
                dalil_table = Table(dalil_content, colWidths=[515])
                dalil_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), box_bg),
                    ('LINEBEFORE', (0, 0), (0, -1), 4, box_border),
                    ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#a7f3d0")),
                    ('TOPPADDING', (0, 0), (-1, -1), 6),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                    ('LEFTPADDING', (0, 0), (-1, -1), 12),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                ]))
                elements.append(Spacer(1, 4))
                elements.append(KeepTogether(dalil_table))
                elements.append(Spacer(1, 4))

            elif c_type == "terjemahan":
                # Styled Translation Block
                trans_content = [
                    [Paragraph(f"<b>Terjemahan:</b> <i>\"{c_text}\"</i>", style_terjemahan)]
                ]
                trans_table = Table(trans_content, colWidths=[515])
                trans_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                    ('LINEBEFORE', (0, 0), (0, -1), 2.5, colors.HexColor("#94a3b8")),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                    ('LEFTPADDING', (0, 0), (-1, -1), 10),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                ]))
                elements.append(KeepTogether(trans_table))
                elements.append(Spacer(1, 8))

        elements.append(Spacer(1, 10))

    # Build PDF using NumberedCanvas
    doc.build(elements, canvasmaker=NumberedCanvas)
    return output_pdf_path


def render_ringkasan_pdf(
    data: Dict[str, Any],
    output_pdf_path: str,
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    """
    Render structured Summary JSON to a modern, publication-ready PDF document.
    Each integrated_section is a self-contained learning module with:
      ringkasan + refleksi zaman now + dalil pendukung + aksi nyata.
    """
    register_fonts()
    os.makedirs(os.path.dirname(os.path.abspath(output_pdf_path)), exist_ok=True)

    doc = SimpleDocTemplate(
        output_pdf_path,
        pagesize=A4,
        leftMargin=40, rightMargin=40,
        topMargin=45, bottomMargin=45
    )

    styles = getSampleStyleSheet()

    # Color Palette
    c_primary   = colors.HexColor("#064e3b")   # Deep Emerald
    c_accent    = colors.HexColor("#059669")   # Emerald 600
    c_dark      = colors.HexColor("#1e293b")   # Slate 800
    c_muted     = colors.HexColor("#64748b")   # Slate 500
    c_gold      = colors.HexColor("#b45309")   # Amber 700
    c_gold_bg   = colors.HexColor("#fffbeb")   # Amber 50
    c_gold_brd  = colors.HexColor("#fbbf24")   # Amber 400
    c_cyan_bg   = colors.HexColor("#f0f9ff")   # Sky 50
    c_cyan_brd  = colors.HexColor("#0284c7")   # Sky 600
    c_cyan_txt  = colors.HexColor("#0c4a6e")   # Sky 900
    c_green_bg  = colors.HexColor("#f0fdf4")   # Emerald 50
    c_teal_bg   = colors.HexColor("#ecfdf5")   # Teal 50

    has_amiri = "Amiri" in pdfmetrics.getRegisteredFontNames()
    arabic_font = "Amiri" if has_amiri else "Helvetica"

    # ─── Typography Styles ───
    style_badge_title = ParagraphStyle(
        'BadgeTitle', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=8, leading=10,
        textColor=c_accent, spaceAfter=4
    )
    style_main_title = ParagraphStyle(
        'SumMainTitle', parent=styles['Heading1'],
        fontName='Helvetica-Bold', fontSize=20, leading=24,
        textColor=c_primary, alignment=TA_CENTER, spaceAfter=6
    )
    style_sub_label = ParagraphStyle(
        'SumSubLabel', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9, leading=12,
        textColor=c_muted, alignment=TA_CENTER
    )
    style_meta = ParagraphStyle(
        'SumMeta', parent=styles['Normal'],
        fontName='Helvetica', fontSize=9, leading=12, textColor=c_dark
    )
    style_meta_label = ParagraphStyle(
        'SumMetaLbl', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9, leading=12, textColor=c_accent
    )
    style_section_h2 = ParagraphStyle(
        'SumH2', parent=styles['Heading2'],
        fontName='Helvetica-Bold', fontSize=13, leading=17,
        textColor=c_primary, spaceBefore=14, spaceAfter=6, keepWithNext=True
    )
    style_narasi = ParagraphStyle(
        'SumNarasi', parent=styles['Normal'],
        fontName='Helvetica', fontSize=10, leading=15,
        textColor=c_dark, alignment=TA_JUSTIFY, spaceAfter=6
    )
    style_dalil_ar = ParagraphStyle(
        'SumDalilAr', parent=styles['Normal'],
        fontName=arabic_font, fontSize=14, leading=24,
        textColor=colors.HexColor("#064e3b"), alignment=TA_RIGHT,
        spaceBefore=4, spaceAfter=4
    )
    style_dalil_badge = ParagraphStyle(
        'SumDalilBadge', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=8, leading=10,
        textColor=c_accent, alignment=TA_LEFT
    )
    style_terjemahan = ParagraphStyle(
        'SumTerjemahan', parent=styles['Normal'],
        fontName='Helvetica-Oblique', fontSize=9.5, leading=14,
        textColor=colors.HexColor("#334155"), alignment=TA_JUSTIFY, leftIndent=8
    )
    style_takeaway = ParagraphStyle(
        'Takeaway', parent=styles['Normal'],
        fontName='Helvetica', fontSize=10.5, leading=15,
        textColor=c_dark, leftIndent=8, spaceAfter=4
    )
    style_refleksi = ParagraphStyle(
        'Refleksi', parent=styles['Normal'],
        fontName='Helvetica', fontSize=10, leading=15,
        textColor=c_cyan_txt, alignment=TA_JUSTIFY, spaceAfter=6
    )
    style_refleksi_hdr = ParagraphStyle(
        'RefleksiHdr', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9, leading=12,
        textColor=c_cyan_brd
    )
    style_dalil_ko_arab = ParagraphStyle(
        'DalilKoArab', parent=styles['Normal'],
        fontName=arabic_font, fontSize=13, leading=22,
        textColor=c_gold, alignment=TA_RIGHT, spaceAfter=2
    )
    style_dalil_ko_meta = ParagraphStyle(
        'DalilKoMeta', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=8.5, leading=12,
        textColor=c_gold
    )
    style_dalil_ko_trans = ParagraphStyle(
        'DalilKoTrans', parent=styles['Normal'],
        fontName='Helvetica-Oblique', fontSize=9, leading=13,
        textColor=colors.HexColor("#78350f"), spaceAfter=2
    )
    style_dalil_ko_relv = ParagraphStyle(
        'DalilKoRelv', parent=styles['Normal'],
        fontName=arabic_font, fontSize=9, leading=13,
        textColor=c_muted, spaceAfter=4
    )
    # Additional Color Tokens
    c_purple_bg  = colors.HexColor("#faf5ff")   # Purple 50
    c_purple_brd = colors.HexColor("#8b5cf6")   # Violet 500
    c_purple_txt = colors.HexColor("#4c1d95")   # Violet 900
    c_indigo_hdr = colors.HexColor("#312e81")   # Indigo 900

    style_sec_header = ParagraphStyle(
        'SecHeader', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=10.5, leading=14,
        textColor=colors.HexColor("#064e3b")
    )
    style_sub_section_label = ParagraphStyle(
        'SubSecLabel', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9, leading=12,
        textColor=colors.HexColor("#047857"), spaceBefore=5, spaceAfter=3
    )
    style_lathaif_hdr = ParagraphStyle(
        'LathaifHdr', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=8.5, leading=11,
        textColor=c_purple_txt
    )
    style_lathaif_label = ParagraphStyle(
        'LathaifLabel', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=8.5, leading=12,
        textColor=c_purple_txt
    )
    style_lathaif_body = ParagraphStyle(
        'LathaifBody', parent=styles['Normal'],
        fontName=arabic_font, fontSize=9.5, leading=14,
        textColor=colors.HexColor("#1e1b4b"), spaceAfter=3
    )
    style_lathaif_arab = ParagraphStyle(
        'LathaifArab', parent=styles['Normal'],
        fontName=arabic_font, fontSize=13, leading=22,
        textColor=colors.HexColor("#064e3b"), alignment=TA_RIGHT,
        spaceBefore=2, spaceAfter=4
    )
    style_aksi = ParagraphStyle(
        'Aksi', parent=styles['Normal'],
        fontName=arabic_font, fontSize=9.5, leading=14,
        textColor=c_dark, leftIndent=6, spaceAfter=3
    )

    elements = []

    judul = clean_pdf_text(data.get("judul_kajian") or "Ringkasan Kajian Islam")
    ustadz = clean_pdf_text((metadata or {}).get("ustadz") or "Pemateri Kajian")
    source_url = clean_pdf_text((metadata or {}).get("source_url") or "Rekaman Audio/Video")
    tanggal = datetime.now().strftime("%d %B %Y")

    # ─── 1. HEADER ───
    elements.append(Paragraph("RINGKASAN & REFLEKSI CERDAS", style_badge_title))
    elements.append(Paragraph(judul, style_main_title))
    elements.append(Paragraph("Modul Terpadu • Refleksi Zaman Now • Dalil & Lathaif Tafsir", style_sub_label))
    elements.append(Spacer(1, 10))

    meta_table_data = [[
        Paragraph("<b>Penceramah:</b>", style_meta_label), Paragraph(ustadz, style_meta),
        Paragraph("<b>Tanggal Proses:</b>", style_meta_label), Paragraph(tanggal, style_meta),
    ], [
        Paragraph("<b>Sumber:</b>", style_meta_label),
        Paragraph(source_url[:50] + ("..." if len(source_url) > 50 else ""), style_meta),
        Paragraph("<b>Mode:</b>", style_meta_label),
        Paragraph("Modul Terpadu + Lathaif", style_meta),
    ]]
    meta_table = Table(meta_table_data, colWidths=[100, 160, 90, 165])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 16))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceAfter=14))

    # ─── 2. QUICK TAKEAWAYS ───
    quick_takeaways = data.get("quick_takeaways", [])
    if not isinstance(quick_takeaways, list):
        quick_takeaways = [str(quick_takeaways)] if quick_takeaways else []

    if quick_takeaways:
        takeaway_rows = [[
            Paragraph("<b>INTISARI CEPAT (SARIPATI KAJIAN)</b>", style_refleksi_hdr)
        ]]
        for i, point in enumerate(quick_takeaways, 1):
            takeaway_rows.append([Paragraph(f"<b>{i}.</b>  {clean_pdf_text(point)}", style_takeaway)])
        tbl = Table(takeaway_rows, colWidths=[515])
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), c_teal_bg),
            ('LINEBEFORE', (0, 0), (0, -1), 4, c_accent),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#a7f3d0")),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ]))
        elements.append(KeepTogether(tbl))
        elements.append(Spacer(1, 16))

    # ─── 3. STRUKTUR TERINTEGRASI ATAU LEGACY ───
    integrated_sections = data.get("integrated_sections", [])
    if not isinstance(integrated_sections, list):
        integrated_sections = []

    if integrated_sections:
        # ═══ NEW: INTEGRATED PER-SECTION LEARNING MODULES ═══
        elements.append(Paragraph("Pembahasan Materi Terpadu", style_section_h2))
        elements.append(Spacer(1, 4))

        for idx, sec in enumerate(integrated_sections, 1):
            if not isinstance(sec, dict):
                continue
            sub_judul = sec.get("sub_judul", f"Topik {idx}")
            ringkasan_text = sec.get("ringkasan") or sec.get("konten") or ""
            refleksi_text = sec.get("refleksi") or ""
            dalil_list = sec.get("dalil_pendukung") or []
            tadabbur_list = sec.get("tadabbur_lathaif") or []
            aksi_list = sec.get("aksi_nyata") or []

            # 3.1 Banner Modul
            sec_header_table = Table([[
                Paragraph(f"<b>MODUL {idx}: {clean_pdf_text(sub_judul).upper()}</b>", style_sec_header)
            ]], colWidths=[515])
            sec_header_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#ecfdf5")),
                ('BOX', (0, 0), (-1, -1), 1, colors.HexColor("#10b981")),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LEFTPADDING', (0, 0), (-1, -1), 10),
                ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ]))
            elements.append(Spacer(1, 8))
            elements.append(KeepTogether(sec_header_table))
            elements.append(Spacer(1, 6))

            # 3.2 Uraian Ringkasan
            if ringkasan_text:
                elements.append(Paragraph("<b>Ringkasan Materi:</b>", style_sub_section_label))
                parsed = parse_konten_markers(ringkasan_text)
                for chunk in parsed:
                    c_type, c_text = chunk["type"], chunk["text"]
                    if c_type == "narasi":
                        for p in [p.strip() for p in c_text.split("\n") if p.strip()]:
                            elements.append(Paragraph(format_mixed_pdf_text(p), style_narasi))
                    elif c_type == "dalil":
                        shaped_ar = shape_arabic_text(c_text, max_line_width=485, font_name=arabic_font)
                        dalil_content = [
                            [Paragraph("<b>DALIL DALAM KAJIAN</b>", style_dalil_badge)],
                            [Paragraph(shaped_ar, style_dalil_ar)]
                        ]
                        dt = Table(dalil_content, colWidths=[515])
                        dt.setStyle(TableStyle([
                            ('BACKGROUND', (0, 0), (-1, -1), c_green_bg),
                            ('LINEBEFORE', (0, 0), (0, -1), 4, c_accent),
                            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#a7f3d0")),
                            ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                            ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                        ]))
                        elements.append(Spacer(1, 3))
                        elements.append(KeepTogether(dt))
                        elements.append(Spacer(1, 3))
                    elif c_type == "terjemahan":
                        cleaned_tr = clean_pdf_text(c_text)
                        tr = Table([[Paragraph(f"<b>Terjemahan:</b> <i>\"{cleaned_tr}\"</i>", style_terjemahan)]], colWidths=[515])
                        tr.setStyle(TableStyle([
                            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                            ('LINEBEFORE', (0, 0), (0, -1), 2.5, colors.HexColor("#94a3b8")),
                            ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                            ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                        ]))
                        elements.append(KeepTogether(tr))
                        elements.append(Spacer(1, 5))

            # 3.3 Refleksi Zaman Now
            if refleksi_text:
                elements.append(Spacer(1, 3))
                ref_rows = [[Paragraph("<b>REFLEKSI & KONTEKSTUALISASI ZAMAN NOW</b>", style_refleksi_hdr)]]
                for p in [p.strip() for p in refleksi_text.split("\n") if p.strip()]:
                    ref_rows.append([Paragraph(format_mixed_pdf_text(p), style_refleksi)])
                tbl_r = Table(ref_rows, colWidths=[515])
                tbl_r.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), c_cyan_bg),
                    ('LINEBEFORE', (0, 0), (0, -1), 4, c_cyan_brd),
                    ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#bae6fd")),
                    ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                    ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                ]))
                elements.append(KeepTogether(tbl_r))
                elements.append(Spacer(1, 6))

            # 3.4 Dalil Pendukung (Minimal 3 Dalil)
            if dalil_list and isinstance(dalil_list, list):
                elements.append(Spacer(1, 3))
                elements.append(Paragraph("<b>Dalil-Dalil Pendukung Terkait:</b>", style_sub_section_label))
                for d_item in dalil_list:
                    if not isinstance(d_item, dict):
                        continue
                    ko_rows = []
                    if d_item.get("sumber"):
                        ko_rows.append([Paragraph(f"<b>[DALIL] {clean_pdf_text(d_item['sumber'])}</b>", style_dalil_ko_meta)])
                    if d_item.get("arab"):
                        shaped = shape_arabic_text(d_item["arab"], max_line_width=485, font_name=arabic_font)
                        ko_rows.append([Paragraph(shaped, style_dalil_ko_arab)])
                    if d_item.get("terjemahan"):
                        t_cleaned = clean_pdf_text(d_item['terjemahan'])
                        ko_rows.append([Paragraph(f"<i>\"{t_cleaned}\"</i>", style_dalil_ko_trans)])
                    if d_item.get("relevansi"):
                        rel_cleaned = format_mixed_pdf_text(d_item['relevansi'])
                        ko_rows.append([Paragraph(f"<b>Korelasi:</b> {rel_cleaned}", style_dalil_ko_relv)])
                    if d_item.get("lathaif"):
                        lat_cleaned = format_mixed_pdf_text(d_item['lathaif'])
                        ko_rows.append([Paragraph(f"<b>Lathaif:</b> {lat_cleaned}", style_dalil_ko_relv)])
                    if ko_rows:
                        tbl_k = Table(ko_rows, colWidths=[515])
                        tbl_k.setStyle(TableStyle([
                            ('BACKGROUND', (0, 0), (-1, -1), c_gold_bg),
                            ('LINEBEFORE', (0, 0), (0, -1), 4, c_gold_brd),
                            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#fde68a")),
                            ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                            ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                        ]))
                        elements.append(KeepTogether(tbl_k))
                        elements.append(Spacer(1, 4))

            # 3.5 Detail Tadabbur & Lathaif (Ala Tafsir Tahlily Dorar Saniyyah)
            if tadabbur_list and isinstance(tadabbur_list, list):
                elements.append(Spacer(1, 3))
                elements.append(Paragraph("<b>Tadabbur & Lathaif (Tafsir Tahlily Dorar Saniyyah):</b>", style_sub_section_label))
                for t_item in tadabbur_list:
                    if not isinstance(t_item, dict):
                        continue
                    tad_rows = [[
                        Paragraph("<b>DETAIL TADABBUR & LATHAIF (TAFSIR TAHLILY)</b>", style_lathaif_hdr)
                    ]]
                    if t_item.get("fokus_dalil"):
                        raw_fokus = clean_pdf_text(t_item["fokus_dalil"])
                        if is_mostly_arabic(raw_fokus):
                            shaped_fokus = shape_arabic_text(raw_fokus, max_line_width=485, font_name=arabic_font, font_size=13)
                            tad_rows.append([
                                Paragraph("<b>Lafadz / Ayat Fokus:</b>", style_lathaif_label)
                            ])
                            tad_rows.append([
                                Paragraph(shaped_fokus, style_lathaif_arab)
                            ])
                        else:
                            shaped_fokus = format_mixed_pdf_text(raw_fokus)
                            tad_rows.append([
                                Paragraph(f"<b>Lafadz / Ayat Fokus:</b> <i>{shaped_fokus}</i>", style_lathaif_body)
                            ])
                    if t_item.get("tinjauan_bahasa"):
                        tb_text = format_mixed_pdf_text(t_item["tinjauan_bahasa"])
                        tad_rows.append([
                            Paragraph(f"<b>Tinjauan Bahasa & Balaghah:</b> {tb_text}", style_lathaif_body)
                        ])
                    if t_item.get("lathaif_hikmah"):
                        lh_text = format_mixed_pdf_text(t_item["lathaif_hikmah"])
                        tad_rows.append([
                            Paragraph(f"<b>Lathaif & Rahasia Makna:</b> {lh_text}", style_lathaif_body)
                        ])
                    if len(tad_rows) > 1:
                        tbl_tad = Table(tad_rows, colWidths=[515])
                        tbl_tad.setStyle(TableStyle([
                            ('BACKGROUND', (0, 0), (-1, -1), c_purple_bg),
                            ('LINEBEFORE', (0, 0), (0, -1), 4, c_purple_brd),
                            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#ddd6fe")),
                            ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                            ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                        ]))
                        elements.append(KeepTogether(tbl_tad))
                        elements.append(Spacer(1, 4))

            # 3.6 Aksi Nyata
            if aksi_list and isinstance(aksi_list, list):
                elements.append(Spacer(1, 3))
                elements.append(Paragraph("<b>Aksi Nyata Yang Bisa Langsung Diamalkan:</b>", style_sub_section_label))
                aksi_rows = []
                for a_idx, aksi in enumerate(aksi_list, 1):
                    aksi_rows.append([Paragraph(f"<b>{a_idx}.</b>  {format_mixed_pdf_text(aksi)}", style_aksi)])
                tbl_a = Table(aksi_rows, colWidths=[515])
                tbl_a.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), c_teal_bg),
                    ('LINEBEFORE', (0, 0), (0, -1), 4, c_accent),
                    ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#a7f3d0")),
                    ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                    ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                ]))
                elements.append(KeepTogether(tbl_a))
                elements.append(Spacer(1, 6))

            # Pembatas antar modul
            elements.append(Spacer(1, 10))
            elements.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#cbd5e1"), spaceAfter=10))

    else:
        # ═══ FALLBACK: LEGACY LAYOUT ═══
        sections = data.get("ringkasan_sections", [])
        if not isinstance(sections, list):
            sections = []

        if sections:
            elements.append(Paragraph("Ringkasan Materi", style_section_h2))
            for idx, sec in enumerate(sections, 1):
                if not isinstance(sec, dict):
                    continue
                sub_judul = sec.get("sub_judul", f"Bagian {idx}")
                elements.append(Paragraph(f"{idx}. {clean_pdf_text(sub_judul)}", ParagraphStyle(
                    f'SumSecH{idx}', parent=style_section_h2, fontSize=11, spaceBefore=10
                )))
                konten = sec.get("konten", "")
                parsed = parse_konten_markers(konten)
                for chunk in parsed:
                    c_type, c_text = chunk["type"], chunk["text"]
                    if c_type == "narasi":
                        for p in [p.strip() for p in c_text.split("\n") if p.strip()]:
                            elements.append(Paragraph(format_mixed_pdf_text(p), style_narasi))
                    elif c_type == "dalil":
                        shaped_ar = shape_arabic_text(c_text, max_line_width=485, font_name=arabic_font)
                        dalil_content = [
                            [Paragraph("<b>DALIL (AYAT AL-QUR'AN / HADITS)</b>", style_dalil_badge)],
                            [Paragraph(shaped_ar, style_dalil_ar)]
                        ]
                        dt = Table(dalil_content, colWidths=[515])
                        dt.setStyle(TableStyle([
                            ('BACKGROUND', (0, 0), (-1, -1), c_green_bg),
                            ('LINEBEFORE', (0, 0), (0, -1), 4, c_accent),
                            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#a7f3d0")),
                            ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                            ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                        ]))
                        elements.append(Spacer(1, 4))
                        elements.append(KeepTogether(dt))
                        elements.append(Spacer(1, 4))
                    elif c_type == "terjemahan":
                        cleaned_tr = clean_pdf_text(c_text)
                        tr = Table([[Paragraph(f"<b>Terjemahan:</b> <i>\"{cleaned_tr}\"</i>", style_terjemahan)]], colWidths=[515])
                        tr.setStyle(TableStyle([
                            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                            ('LINEBEFORE', (0, 0), (0, -1), 2.5, colors.HexColor("#94a3b8")),
                            ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                            ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                        ]))
                        elements.append(KeepTogether(tr))
                        elements.append(Spacer(1, 6))
                elements.append(Spacer(1, 8))

        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceBefore=8, spaceAfter=14))

        # Refleksi Zaman Now (Legacy)
        refleksi = str(data.get("refleksi_zaman_now") or "")
        if refleksi:
            refleksi_rows = [[Paragraph("<b>REFLEKSI & KONTEKSTUALISASI ZAMAN NOW</b>", style_refleksi_hdr)]]
            for para in [p.strip() for p in refleksi.split("\n") if p.strip()]:
                refleksi_rows.append([Paragraph(format_mixed_pdf_text(para), style_refleksi)])
            tbl_r = Table(refleksi_rows, colWidths=[515])
            tbl_r.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), c_cyan_bg),
                ('LINEBEFORE', (0, 0), (0, -1), 4, c_cyan_brd),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#bae6fd")),
                ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ]))
            elements.append(KeepTogether(tbl_r))
            elements.append(Spacer(1, 14))

        # Korelasi Dalil Terkait (Legacy)
        korelasi_list = data.get("korelasi_dalil_terkait", [])
        if not isinstance(korelasi_list, list):
            korelasi_list = []

        if korelasi_list:
            elements.append(Paragraph("Dalil Pendukung & Korelasi", ParagraphStyle(
                'KorelasiHdr', parent=style_section_h2, fontSize=12
            )))
            for item in korelasi_list:
                if not isinstance(item, dict):
                    continue
                ko_rows = []
                if item.get("sumber"):
                    ko_rows.append([Paragraph(f"<b>[DALIL] {clean_pdf_text(item['sumber'])}</b>", style_dalil_ko_meta)])
                if item.get("arab"):
                    shaped = shape_arabic_text(item["arab"], max_line_width=485, font_name=arabic_font)
                    ko_rows.append([Paragraph(shaped, style_dalil_ko_arab)])
                if item.get("terjemahan"):
                    ko_rows.append([Paragraph(f"<i>\"{clean_pdf_text(item['terjemahan'])}\"</i>", style_dalil_ko_trans)])
                if item.get("relevansi"):
                    ko_rows.append([Paragraph(f"<b>Korelasi:</b> {format_mixed_pdf_text(item['relevansi'])}", style_dalil_ko_relv)])
                if ko_rows:
                    tbl_k = Table(ko_rows, colWidths=[515])
                    tbl_k.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, -1), c_gold_bg),
                        ('LINEBEFORE', (0, 0), (0, -1), 4, c_gold_brd),
                        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#fde68a")),
                        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                        ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                    ]))
                    elements.append(KeepTogether(tbl_k))
                    elements.append(Spacer(1, 8))
            elements.append(Spacer(1, 8))

        # Aksi Nyata (Legacy)
        aksi_list = data.get("aksi_nyata", [])
        if not isinstance(aksi_list, list):
            aksi_list = [str(aksi_list)] if aksi_list else []

        if aksi_list:
            aksi_rows = [[Paragraph("<b>AKSI NYATA YANG BISA LANGSUNG DIAMALKAN</b>", style_refleksi_hdr)]]
            for i, aksi in enumerate(aksi_list, 1):
                aksi_rows.append([Paragraph(f"<b>{i}.</b>  {format_mixed_pdf_text(aksi)}", style_aksi)])
            tbl_a = Table(aksi_rows, colWidths=[515])
            tbl_a.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), c_teal_bg),
                ('LINEBEFORE', (0, 0), (0, -1), 4, c_accent),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#a7f3d0")),
                ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ]))
            elements.append(KeepTogether(tbl_a))
            elements.append(Spacer(1, 14))

    # Build PDF
    doc.build(elements, canvasmaker=NumberedCanvas)
    return output_pdf_path


def merge_pdfs(pdf_paths: List[str], output_pdf_path: str) -> str:
    """
    Merge multiple PDF files into one combined PDF booklet using pypdf.
    E.g. [summary_pdf, transcript_pdf] -> Kajian_Lengkap.pdf.
    """
    from pypdf import PdfWriter
    os.makedirs(os.path.dirname(os.path.abspath(output_pdf_path)), exist_ok=True)
    writer = PdfWriter()
    valid_count = 0
    for p in pdf_paths:
        if p and os.path.exists(p) and os.path.getsize(p) > 0:
            writer.append(p)
            valid_count += 1

    if valid_count == 0:
        raise ValueError("Tidak ada file PDF valid yang dapat digabungkan.")

    with open(output_pdf_path, "wb") as f_out:
        writer.write(f_out)
    writer.close()
    return output_pdf_path

