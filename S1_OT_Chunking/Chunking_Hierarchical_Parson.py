import re
import json
import os
import fitz

PDF_DIR    = "./PDF/7 Parson"
OUTPUT_DIR = "./Hierarchy Chunks/7 Parson"
TOTAL_PARTS = 10

MIN_PARAGRAPH_CHARS = 80

# ── Font signatures ────────────────────────────────────────────────────────────
# Chapter title: TimesNewRomanPS-BoldMT at 27.7pt or 30.0pt
# e.g. "Anatomy of the eye" (27.7), "Physiology of the eye" (30.0)
CHAPTER_TITLE_FONTS = {
    ("TimesNewRomanPS-BoldMT", 27.7),
    ("TimesNewRomanPS-BoldMT", 30.0),
}

# Topic (h1): Arial-BoldMT @ 19.6pt
# e.g. "Development of the eye", "Physiology of vision"
# NOTE: same font also used for "CHAPTER 1" label on opener pages — those are
# filtered out via CHAPTER_LABEL_RE rather than relying on on_chapter_opener,
# since opener detection only catches the *title* page, not the preceding page.
TOPIC_FONT = ("Arial-BoldMT", 19.6)
CHAPTER_LABEL_RE = re.compile(r"^CHAPTER\s+\d+", re.IGNORECASE)

# Subtopic (h2): Arial-BoldMT @ 15.6pt
# e.g. "Cornea", "Transparency of cornea", "Blood supply and innervation"
SUBTOPIC_FONT = ("Arial-BoldMT", 15.6)

# Sub-subtopic (h3 → stored as subtopic): Arial-BoldMT @ 15.0pt or 16.9pt
# e.g. "Young–helmholtz or the trichromatic theory"
SUBSUBTOPIC_FONTS = {
    ("Arial-BoldMT", 16.9),
    ("Arial-BoldMT", 15.0),
}

# Body text: TimesNewRomanPSMT and variants at 13.8pt and 15.0pt
BODY_FONTS = {
    ("TimesNewRomanPSMT",              13.8),
    ("TimesNewRomanPSMT",              15.0),
    ("TimesNewRomanPS-ItalicMT",       13.8),
    ("TimesNewRomanPS-ItalicMT",       15.0),
    ("TimesNewRomanPS-BoldItalicMT",   13.8),
    ("TimesNewRomanPS-BoldItalicMT",   15.0),
    ("TimesNewRomanPS-BoldMT",         13.8),
    ("TimesNewRomanPS-BoldMT",         15.0),
}

# "Suggested reading" heading sizes — flag in_bibliography when seen
SUGGESTED_READING_FONTS = {
    ("Arial-BoldMT", 23.1),
    ("Arial-BoldMT", 25.0),
}
SUGGESTED_READING_RE = re.compile(r"suggested\s+reading", re.IGNORECASE)

FIGURE_TABLE_RE = re.compile(r"^(Figure|Table|FIG\.?)\s", re.IGNORECASE)
SENTENCE_END_RE = re.compile(r"[.?!\"']\s*$")

# Everything not explicitly listed above is skipped (ArialMT captions,
# table cells, page numbers, section openers, footnotes, etc.)

MAX_SKIP_SIZE = 5.0


def clean_text(text: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    text = text.replace("­", "")   # soft hyphen
    return text.strip()


def is_horizontal(line: dict) -> bool:
    _, dy = line.get("dir", (1.0, 0.0))
    return abs(dy) < 0.1


def starts_new_paragraph(buf: str, nxt: str) -> bool:
    if not buf or not nxt:
        return True
    return bool(SENTENCE_END_RE.search(buf)) and nxt[0].isupper()


# ── Pass 1: locate chapter pages ──────────────────────────────────────────────
def detect_chapter_pages(doc):
    """Return {page_number: chapter_title} for pages containing the chapter
    title font. These pages also carry CHAPTER OUTLINE TOC entries (ArialMT)
    that must be skipped during body processing."""
    chapters = {}
    for page_number, page in enumerate(doc, start=1):
        title_parts = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                if not is_horizontal(l):
                    continue
                for s in l["spans"]:
                    t = s["text"].strip()
                    font = s["font"]
                    size = round(s["size"], 1)
                    if t and (font, size) in CHAPTER_TITLE_FONTS:
                        title_parts.append(t)
        if title_parts:
            chapters[page_number] = clean_text(" ".join(title_parts))
    return chapters


# Heading fonts that can be embedded inside a mixed block alongside body text.
# When a line's dominant font is one of these, it is split out as its own entry
# even if the PDF encodes it in the same block as the following paragraph.
ALL_HEADING_FONTS = {
    TOPIC_FONT, SUBTOPIC_FONT,
} | SUBSUBTOPIC_FONTS | CHAPTER_TITLE_FONTS


# ── Pass 2: ordered content blocks ────────────────────────────────────────────
def extract_ordered_blocks(doc):
    """Yield (page_number, text, font, size) top-to-bottom per page.

    Processes line-by-line so that heading lines embedded in the same PDF block
    as their following body paragraph are correctly separated.  This is needed
    because Parson encodes headings like 'Cornea' and 'Sclera' as the first
    span of the paragraph block rather than as standalone blocks.
    """
    for page_number, page in enumerate(doc, start=1):
        items = []
        for b in page.get_text("dict")["blocks"]:
            lines = b.get("lines")
            if not lines:
                continue

            y0_block = b["bbox"][1]

            for line_idx, l in enumerate(lines):
                if not is_horizontal(l):
                    continue

                line_parts = []
                dom_font, dom_size, max_chars = None, None, 0

                for s in l["spans"]:
                    t = s["text"]
                    font = s["font"]
                    size = round(s["size"], 1)
                    if t.strip() and size > MAX_SKIP_SIZE:
                        line_parts.append(t)
                        nc = len(t.strip())
                        if nc > max_chars:
                            max_chars = nc
                            dom_font = font
                            dom_size = size

                if not line_parts or dom_font is None:
                    continue

                text = clean_text("".join(line_parts))
                if not text:
                    continue

                if (dom_font, dom_size) in CHAPTER_TITLE_FONTS:
                    continue
                if FIGURE_TABLE_RE.match(text):
                    continue

                # Use line's y0 for ordering; fall back to block y0 for first line
                y0 = l["bbox"][1] if "bbox" in l else y0_block
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
    on_chapter_opener = False
    in_bibliography = False

    for page_number, text, font, size in extract_ordered_blocks(doc):

        # ── Chapter boundary ──────────────────────────────────────────────────
        if page_number != current_page:
            current_page = page_number
            on_chapter_opener = page_number in chapter_pages
            if on_chapter_opener:
                builder.start_chapter(chapter_pages[page_number], page_number)
                in_bibliography = False

        # ── Suggested reading → bibliography flag ─────────────────────────────
        if (font, size) in SUGGESTED_READING_FONTS or SUGGESTED_READING_RE.match(text):
            in_bibliography = True
            continue

        if in_bibliography:
            continue

        # ── Topic (h1) ────────────────────────────────────────────────────────
        if (font, size) == TOPIC_FONT:
            # Skip "CHAPTER 1", "CHAPTER 2" labels (same font, different role)
            if CHAPTER_LABEL_RE.match(text):
                continue
            if on_chapter_opener:
                continue
            builder.start_topic(text, page_number)
            continue

        # ── Subtopic (h2) ─────────────────────────────────────────────────────
        if (font, size) == SUBTOPIC_FONT:
            if on_chapter_opener:
                continue
            builder.start_subtopic(text, page_number)
            continue

        # ── Sub-subtopic (h3 → stored as subtopic) ────────────────────────────
        if (font, size) in SUBSUBTOPIC_FONTS:
            if on_chapter_opener:
                continue
            builder.start_subtopic(text, page_number)
            continue

        # ── Body text ─────────────────────────────────────────────────────────
        if (font, size) in BODY_FONTS:
            if on_chapter_opener:
                continue
            builder.add_body(text, page_number)

    return builder.finish()


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    grand_total = 0

    for part in range(1, TOTAL_PARTS + 1):
        book_id  = f"Parson_PT{part}"
        pdf_path = f"{PDF_DIR}/Parson_PT{part}.pdf"
        output   = f"{OUTPUT_DIR}/P{part}_Hierarchical_Chunks.json"

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

    print(f"\nAll done. Total paragraphs: {grand_total}")


if __name__ == "__main__":
    main()
