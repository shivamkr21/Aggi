import re
import json
import fitz

TOTAL_PARTS = 13
PDF_DIR = "./PDF/5 KDT"

MIN_PARAGRAPH_CHARS = 80

# ── Font signatures ────────────────────────────────────────────────────────────
# Chapter opener pages contain multi-line Calibri @ 22.0pt for the chapter
# title (e.g. "Introduction, Routes of / Drug Administration").
# A two-pass approach is used: pre-scan finds the chapter pages so body content
# above the title in the stream still belongs to the right chapter.
CHAPTER_TITLE_FONT = ("Calibri", 22.0)

# Topic heading: Helvetica or Helvetica,Bold at 10–9.5pt, in ALL CAPS.
# e.g. "INTRODUCTION", "ROUTES OF DRUG ADMINISTRATION", "BIOAVAILABILITY"
TOPIC_FONTS = {
    ("Helvetica,Bold", 10.0),
    ("Helvetica",      10.0),
    ("Helvetica,Bold",  9.5),
    ("Helvetica",       9.5),
}

# Subtopic heading: Helvetica,Bold or Helvetica,Italic at 10pt, non-all-caps.
# e.g. "Passive diffusion", "1.Oral", "(i) Subcutaneous", "Pharmacopoeias"
# Also Helvetica,Bold @ 8pt for sidebar box titles.
SUBTOPIC_FONTS = {
    ("Helvetica,Bold",   10.0),
    ("Helvetica,Italic", 10.0),
    ("Helvetica",        10.0),
    ("Helvetica,Bold",    8.0),
}

# Body text fonts
BODY_FONTS = {
    ("TimesNewRoman",        10.0),
    ("TimesNewRoman,Italic", 10.0),
    ("TimesNewRoman",         8.0),
    ("TimesNewRoman,Italic",  8.0),
    ("TimesNewRoman,Bold",    8.0),
    ("Helvetica",             8.0),   # sidebar / box body text
    ("Helvetica,Italic",      8.0),   # sidebar italic
    ("Calibri",              10.0),   # Problem Directed Study text
    ("Helvetica-Light",      10.0),   # occasional body
}

# Fonts to skip entirely
SKIP_FONTS = {
    ("Helvetica",      12.0),   # page numbers
    ("Helvetica,Bold", 11.0),   # running headers "CHAPTER1", "SECTION1"
    ("Calibri",        11.0),   # running chapter title
    ("Gautami,Bold",   20.0),   # decorative "Chapter" / "Chapter1" in opener
    ("Calibri,Bold",   16.0),   # "SECTION1" in chapter opener
    ("Calibri,Bold",   18.2),   # section name in chapter opener
    ("Symbol",         10.0),   # math arrows
}
MAX_SKIP_SIZE = 5.0   # anything ≤ 5pt is a footnote/superscript

# Patterns to skip at content level
FIGURE_RE    = re.compile(r'^Fig\.?\s*\d', re.IGNORECASE)
PROB_STUDY_RE = re.compile(r'PROBLEM\s+DIRECTED\s+STUDY', re.IGNORECASE)

SENTENCE_END_RE = re.compile(r'[.?!""]\s*$')


def clean_text(text: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    text = text.replace("- ", "")   # rejoin soft-hyphenated line breaks
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
    return (font, size) in SKIP_FONTS


def is_all_caps(text: str) -> bool:
    """True when all alphabetic characters in text are uppercase."""
    alpha = [c for c in text if c.isalpha()]
    return bool(alpha) and all(c.isupper() for c in alpha)


# ── Pass 1: locate chapter starting pages ─────────────────────────────────────
def detect_chapter_pages(doc):
    """Return {page_number: chapter_title} for every page that opens a chapter.
    Chapter openers contain one or more Calibri @ 22.0pt lines forming the title.
    """
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

            # Skip chapter title font in body (used only for detection)
            if (dom_font, dom_size) == CHAPTER_TITLE_FONT:
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
        self.last_chapter_num = None
        self.para_buffer = []
        self.para_page = None

    def start_chapter(self, title, page):
        self.flush_paragraph()
        self.chapter_idx += 1
        self.chapter_id = f"{self.book_id}_CH{self.chapter_idx:02d}"
        self.topic_idx = self.subtopic_idx = self.para_idx = 0
        self.topic_id = self.subtopic_id = None
        self.chapters.append({
            "chapter_id": self.chapter_id,
            "book_id": self.book_id,
            "title": title,
            "page": page,
        })

    def start_topic(self, title, page):
        self.flush_paragraph()
        self.ensure_chapter(page)
        self.topic_idx += 1
        self.topic_id = f"{self.chapter_id}_TP{self.topic_idx:02d}"
        self.subtopic_idx = self.para_idx = 0
        self.subtopic_id = None
        self.topics.append({
            "topic_id": self.topic_id,
            "chapter_id": self.chapter_id,
            "title": title,
            "page": page,
        })

    def start_subtopic(self, title, page):
        self.flush_paragraph()
        self.ensure_topic(page)
        self.subtopic_idx += 1
        self.subtopic_id = f"{self.topic_id}_ST{self.subtopic_idx:02d}"
        self.para_idx = 0
        self.subtopics.append({
            "subtopic_id": self.subtopic_id,
            "topic_id": self.topic_id,
            "title": title,
            "page": page,
        })

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

    for page_number, text, font, size in extract_ordered_blocks(doc):

        # Open new chapter when we enter its page
        if page_number != current_page:
            current_page = page_number
            if page_number in chapter_pages:
                builder.start_chapter(chapter_pages[page_number], page_number)

        # ── Topic heading ─────────────────────────────────────────────────────
        if (font, size) in TOPIC_FONTS and is_all_caps(text):
            if PROB_STUDY_RE.search(text):
                continue  # skip "PROBLEM DIRECTED STUDY" markers
            builder.start_topic(text, page_number)
            continue

        # ── Subtopic heading ──────────────────────────────────────────────────
        if (font, size) in SUBTOPIC_FONTS and not is_all_caps(text):
            # Figure captions in Helvetica,Bold 8pt — skip
            if FIGURE_RE.match(text):
                continue
            builder.start_subtopic(text, page_number)
            continue

        # ── Body text ─────────────────────────────────────────────────────────
        if (font, size) in BODY_FONTS:
            # Skip figure captions ("Fig. X.Y: ...")
            if FIGURE_RE.match(text):
                continue
            builder.add_body(text, page_number)

    return builder.finish()


def main():
    for part in range(1, TOTAL_PARTS + 1):
        book_id    = f"KDT_PT{part}"
        pdf_path   = f"{PDF_DIR}/KDT_PT{part}.pdf"
        output     = f"KDT_PT{part}_Hierarchical_Chunks.json"

        print(f"\n=== {book_id} ===")
        doc = fitz.open(pdf_path)
        chapters, topics, subtopics, paragraphs = build_hierarchy(doc, book_id)

        print(f"Chapters  : {len(chapters)}")
        print(f"Topics    : {len(topics)}")
        print(f"Subtopics : {len(subtopics)}")
        print(f"Paragraphs: {len(paragraphs)}")

        with open(output, "w", encoding="utf-8") as f:
            json.dump({
                "chapters": chapters,
                "topics": topics,
                "subtopics": subtopics,
                "paragraphs": paragraphs,
            }, f, ensure_ascii=False, indent=2)

        print(f"Written to {output}")


if __name__ == "__main__":
    main()
