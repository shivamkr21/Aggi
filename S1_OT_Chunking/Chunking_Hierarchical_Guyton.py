import re
import json
import os
import fitz

PDF_DIR    = "./PDF/6 Guyton"
OUTPUT_DIR = "./Hierarchy Chunks/6 Guyton"
TOTAL_PARTS = 15

MIN_PARAGRAPH_CHARS = 80

# ── Font signatures ────────────────────────────────────────────────────────────
# Chapter opener pages carry the chapter title in WarnockPro-BoldDisp @ 24pt.
# The opener also shows a TOC of the chapter's headings using the same Bliss
# fonts as the real headings — so we skip ALL heading-level text on opener
# pages to avoid creating spurious topic/subtopic nodes.
CHAPTER_TITLE_FONT = ("WarnockPro-BoldDisp", 24.0)

# Topic   (h1) — e.g. "Control Systems of the Body", "Functional Systems of
#                      the Cell"  — Bliss-Bold @ 12pt
TOPIC_FONT = ("Bliss-Bold", 12.0)

# Subtopic (h2) — Bliss-Bold or Bliss-Regular @ 11pt
#   Bold:    "Membranous Structures of the Cell", "Nucleus"
#   Regular: "Cell Membrane", "Golgi Apparatus", "Bibliography" (skip below)
SUBTOPIC_FONTS = {
    ("Bliss-Bold",    11.0),
    ("Bliss-Regular", 11.0),
}

# Sub-subtopic (also stored as subtopic) — Bliss-Bold @ 10pt
#   e.g. "Lipid Barrier of the Cell Membrane Impedes Water"
#        "Integral and Peripheral Cell Membrane Proteins."
SUBSUBTOPIC_FONT = ("Bliss-Bold", 10.0)

# Body text — WarnockPro-Light / LightIt / Regular / It at 10pt and 9pt.
# Some parts use 9pt as the main body size, so both are included.
BODY_FONTS = {
    ("WarnockPro-Light",   10.0), ("WarnockPro-LightIt", 10.0),
    ("WarnockPro-Regular", 10.0), ("WarnockPro-It",      10.0),
    ("WarnockPro-Light",    9.0), ("WarnockPro-LightIt",  9.0),
    ("WarnockPro-Regular",  9.0), ("WarnockPro-It",       9.0),
}

# Fonts to skip entirely:
#   Bliss-ExtraBold 18pt  — "U n i t  I" unit markers
#   Bliss-Italic 9pt      — "Unit I" running header (even pages)
#   Bliss-LightItalic 9pt — unit title running header
#   Bliss-Light 9pt       — figure/table captions, bibliography, chapter
#                           running header ("Chapter 1Functional Org...")
#   Bliss-Medium 10pt     — page numbers ("3", "4", ...)
#   Bliss-Regular 12pt    — TOC entries on unit-intro splash pages
#   Bliss-Bold 9pt        — table header cells
#   Bliss-Light 8pt       — bibliography reference entries
#   Bliss-LightItalic 8pt — bibliography italic entries
#   Bliss-Bold 8pt        — occasional small heading in sidebar boxes
#   Helvetica / variants  — diagram labels inside figures
#   WarnockPro-BoldDisp 32pt — unit title
#   WarnockPro-ItDisp 144pt  — large decorative unit numeral
#   Arial 10pt            — "This page intentionally left blank"
SKIP_FONTS = {
    ("Bliss-ExtraBold",   18.0),
    ("Bliss-Italic",       9.0),
    ("Bliss-LightItalic",  9.0),
    ("Bliss-Light",        9.0),
    ("Bliss-Medium",      10.0),
    ("Bliss-Regular",     12.0),
    ("Bliss-Bold",         9.0),
    ("Bliss-Light",        8.0),
    ("Bliss-LightItalic",  8.0),
    ("Bliss-Bold",         8.0),
    ("WarnockPro-BoldDisp", 32.0),
    ("WarnockPro-ItDisp",  144.0),
    ("Arial",              10.0),
}
SKIP_FONT_PREFIXES = ("Helvetica",)   # all Helvetica variants → diagram labels
MAX_SKIP_SIZE = 5.0

FIGURE_TABLE_RE = re.compile(r'^(Figure|Table)\s+\d', re.IGNORECASE)
SENTENCE_END_RE = re.compile(r'[.?!""]\s*$')


def clean_text(text: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    text = text.replace("­", "")   # soft hyphen / joining artefact
    return text.strip()


def is_horizontal(line: dict) -> bool:
    _, dy = line.get("dir", (1.0, 0.0))
    return abs(dy) < 0.1


def starts_new_paragraph(buf: str, nxt: str) -> bool:
    if not buf or not nxt:
        return True
    return bool(SENTENCE_END_RE.search(buf)) and nxt[0].isupper()


def should_skip(font: str, size: float) -> bool:
    if size <= MAX_SKIP_SIZE:
        return True
    if (font, size) in SKIP_FONTS:
        return True
    for prefix in SKIP_FONT_PREFIXES:
        if font.startswith(prefix):
            return True
    return False


# ── Pass 1: locate chapter pages ──────────────────────────────────────────────
def detect_chapter_pages(doc):
    """Return {page_number: chapter_title} for pages with WarnockPro-BoldDisp
    @ 24pt (the chapter title font). These pages also contain TOC-style heading
    lists that must be skipped during body processing."""
    chapters = {}
    for page_number, page in enumerate(doc, start=1):
        title_parts = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                if not is_horizontal(l):
                    continue
                for s in l["spans"]:
                    t = s["text"].strip()
                    if t and s["font"] == CHAPTER_TITLE_FONT[0] and round(s["size"], 1) == CHAPTER_TITLE_FONT[1]:
                        title_parts.append(t)
        if title_parts:
            chapters[page_number] = clean_text(" ".join(title_parts))
    return chapters


# ── Pass 2: ordered content blocks ────────────────────────────────────────────
def extract_ordered_blocks(doc):
    """Yield (page_number, text, font, size) sorted top-to-bottom per page."""
    for page_number, page in enumerate(doc, start=1):
        items = []
        for b in page.get_text("dict")["blocks"]:
            lines = b.get("lines")
            if not lines or not all(is_horizontal(l) for l in lines):
                continue

            block_parts = []
            dom_font, dom_size, max_chars = None, None, 0

            for l in lines:
                line_parts = []
                for s in l["spans"]:
                    t = s["text"]
                    font = s["font"]
                    size = round(s["size"], 1)
                    if t.strip() and not should_skip(font, size):
                        line_parts.append(t)
                        nc = len(t.strip())
                        if nc > max_chars:
                            max_chars = nc
                            dom_font = font
                            dom_size = size
                if line_parts:
                    if block_parts:
                        block_parts.append(" ")
                    block_parts.extend(line_parts)

            if not block_parts or dom_font is None:
                continue

            text = clean_text("".join(block_parts))
            if not text:
                continue

            # Never pass the chapter title into body processing
            if (dom_font, dom_size) == CHAPTER_TITLE_FONT:
                continue

            # Skip figure/table captions regardless of font
            if FIGURE_TABLE_RE.match(text):
                continue

            y0 = b["bbox"][1]
            items.append((y0, text, dom_font, dom_size))

        for _, text, font, size in sorted(items, key=lambda v: v[0]):
            yield page_number, text, font, size


# ── Hierarchy builder ─────────────────────────────────────────────────────────
class HierarchyBuilder:
    def __init__(self, book_id):
        self.book_id = book_id
        self.chapters, self.topics, self.subtopics, self.paragraphs = [], [], [], []
        self.chapter_id = self.topic_id = self.subtopic_id = None
        self.chapter_idx = self.topic_idx = self.subtopic_idx = self.para_idx = 0
        self.para_buffer = []
        self.para_page = None

    def start_chapter(self, title, page):
        self.flush_paragraph()
        self.chapter_idx += 1
        self.chapter_id = f"{self.book_id}_CH{self.chapter_idx:02d}"
        self.topic_idx = self.subtopic_idx = self.para_idx = 0
        self.topic_id = self.subtopic_id = None
        self.chapters.append({"chapter_id": self.chapter_id, "book_id": self.book_id,
                               "title": title, "page": page})

    def start_topic(self, title, page):
        self.flush_paragraph()
        self.ensure_chapter(page)
        self.topic_idx += 1
        self.topic_id = f"{self.chapter_id}_TP{self.topic_idx:02d}"
        self.subtopic_idx = self.para_idx = 0
        self.subtopic_id = None
        self.topics.append({"topic_id": self.topic_id, "chapter_id": self.chapter_id,
                             "title": title, "page": page})

    def start_subtopic(self, title, page):
        self.flush_paragraph()
        self.ensure_topic(page)
        self.subtopic_idx += 1
        self.subtopic_id = f"{self.topic_id}_ST{self.subtopic_idx:02d}"
        self.para_idx = 0
        self.subtopics.append({"subtopic_id": self.subtopic_id, "topic_id": self.topic_id,
                                "title": title, "page": page})

    def ensure_chapter(self, page):
        if self.chapter_id is None:
            self.start_chapter("Untitled Chapter", page)

    def ensure_topic(self, page):
        self.ensure_chapter(page)
        if self.topic_id is None:
            self.start_topic("General", page)

    def ensure_subtopic(self, page):
        self.ensure_topic(page)
        if self.subtopic_id is None:
            self.start_subtopic("General", page)

    def add_body(self, text, page):
        self.ensure_subtopic(page)
        buffered = " ".join(self.para_buffer)
        if self.para_buffer and starts_new_paragraph(buffered, text):
            self.flush_paragraph()
        if not self.para_buffer:
            self.para_page = page
        self.para_buffer.append(text)

    def flush_paragraph(self):
        if not self.para_buffer:
            return
        text = clean_text(" ".join(self.para_buffer))
        self.para_buffer = []
        if len(text) < MIN_PARAGRAPH_CHARS or self.subtopic_id is None:
            return
        self.para_idx += 1
        self.paragraphs.append({
            "paragraph_id": f"{self.subtopic_id}_PA{self.para_idx:03d}",
            "subtopic_id": self.subtopic_id,
            "page": self.para_page,
            "text": text,
        })

    def finish(self):
        self.flush_paragraph()
        return self.chapters, self.topics, self.subtopics, self.paragraphs


# ── Main build ────────────────────────────────────────────────────────────────
def build_hierarchy(doc, book_id):
    chapter_pages = detect_chapter_pages(doc)
    builder = HierarchyBuilder(book_id)

    current_page = None
    on_chapter_opener = False  # suppress heading nodes on TOC-only opener pages
    in_bibliography = False    # skip reference lists

    for page_number, text, font, size in extract_ordered_blocks(doc):

        # ── Chapter boundary ──────────────────────────────────────────────────
        if page_number != current_page:
            current_page = page_number
            on_chapter_opener = page_number in chapter_pages
            if on_chapter_opener:
                builder.start_chapter(chapter_pages[page_number], page_number)
                in_bibliography = False

        # ── Topic (h1) ────────────────────────────────────────────────────────
        if (font, size) == TOPIC_FONT:
            if on_chapter_opener:
                continue   # TOC entry, not a real heading
            in_bibliography = False
            builder.start_topic(text, page_number)
            continue

        # ── Subtopic (h2) ─────────────────────────────────────────────────────
        if (font, size) in SUBTOPIC_FONTS:
            if on_chapter_opener:
                continue   # TOC entry
            if text.strip() == "Bibliography":
                in_bibliography = True
                continue
            if in_bibliography:
                continue
            builder.start_subtopic(text, page_number)
            continue

        # ── Sub-subtopic → also stored as subtopic (h3) ───────────────────────
        if (font, size) == SUBSUBTOPIC_FONT:
            if on_chapter_opener or in_bibliography:
                continue
            builder.start_subtopic(text, page_number)
            continue

        # ── Body text ─────────────────────────────────────────────────────────
        if (font, size) in BODY_FONTS:
            if in_bibliography:
                continue
            builder.add_body(text, page_number)

    return builder.finish()


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    grand_total = 0

    for part in range(1, TOTAL_PARTS + 1):
        book_id    = f"Guyton_PT{part}"
        pdf_path   = f"{PDF_DIR}/Guyton_PT{part}.pdf"
        output     = f"{OUTPUT_DIR}/P{part}_Hierarchical_Chunks.json"

        print(f"\n=== {book_id} ===")
        doc = fitz.open(pdf_path)
        chapters, topics, subtopics, paragraphs = build_hierarchy(doc, book_id)

        print(f"Chapters  : {len(chapters)}")
        print(f"Topics    : {len(topics)}")
        print(f"Subtopics : {len(subtopics)}")
        print(f"Paragraphs: {len(paragraphs)}")

        with open(output, "w", encoding="utf-8") as f:
            json.dump({"chapters": chapters, "topics": topics,
                       "subtopics": subtopics, "paragraphs": paragraphs},
                      f, ensure_ascii=False, indent=2)

        print(f"Written to {output}")
        grand_total += len(paragraphs)

    print(f"\nAll done. Total paragraphs across all parts: {grand_total}")


if __name__ == "__main__":
    main()
