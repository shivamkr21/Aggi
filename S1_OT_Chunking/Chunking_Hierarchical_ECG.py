import re
import json
import fitz
from collections import Counter

PDF_PATH = "./PDF/ECG_PT1.pdf"
OUTPUT_PATH = "ECG_PT1_Hierarchical_Chunks.json"
BOOK_ID = "ECG_PT1"

MIN_PARAGRAPH_CHARS = 80

# ── Font signatures ────────────────────────────────────────────────────────────
# Each chapter opener page carries:
#   - a bare digit in Arial-BoldMT @ 19.5pt  (chapter number)
#   - the chapter title in PalatinoLinotype-Bold @ 30.0pt
CHAPTER_NUMBER_FONT = ("Arial-BoldMT", 19.5)
CHAPTER_TITLE_FONT  = ("PalatinoLinotype-Bold", 30.0)

# Within a chapter, section structure uses two Arial-BoldMT sizes:
#   24.8pt → Topic     ("What is an ECG?", "The different parts of the ECG")
#   19.5pt → Subtopic  ("The wiring diagram of the heart", "Times and speeds")
#             (same font as the chapter number, distinguished by not being a digit)
TOPIC_FONT    = ("Arial-BoldMT", 24.8)
SUBTOPIC_FONT = ("Arial-BoldMT", 19.5)

# "PART N" divider pages use the same 24.8pt Arial-BoldMT as topic headings.
# They are navigation pages and carry no content of their own.
PART_RE = re.compile(r"^PART\s+\d+$", re.IGNORECASE)

# Fonts whose text should be skipped entirely:
#   Arial-BoldMT  @ 11.2pt / 9.0pt  — figure captions ("FIG. 1.1 ...")
#   ArialMT       @ 12.8pt           — figure caption continuations
#   Arial-BoldMT  @ 15.0pt           — table titles ("TABLE 1.1") and
#                                      TOC entries on chapter-opener pages
#   ArialMT       @ 15.0pt           — TOC chapter-list entries
#   PalatinoLinotype-Roman @ 9.0pt   — subscript table labels (V1, V2 ...)
SKIP_FONT_SIZES = {
    ("Arial-BoldMT", 11.2),
    ("Arial-BoldMT", 9.0),
    ("ArialMT", 12.8),
    ("Arial-BoldMT", 15.0),
    ("ArialMT", 15.0),
    ("PalatinoLinotype-Roman", 9.0),
}

SENTENCE_END_RE = re.compile(r'[.?!""]\s*$')


def clean_text(text: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    text = text.replace("- ", "")
    return text.strip()


def is_horizontal(line: dict) -> bool:
    _, dy = line.get("dir", (1.0, 0.0))
    return abs(dy) < 0.1


def starts_new_paragraph(buffer_text: str, next_text: str) -> bool:
    if not buffer_text or not next_text:
        return True
    return bool(SENTENCE_END_RE.search(buffer_text)) and next_text[0].isupper()


def detect_chapter_pages(doc):
    """Return a dict mapping page_number -> (chapter_num_str, chapter_title).

    Each chapter opens with a page that contains:
      - one span of CHAPTER_NUMBER_FONT whose text is a bare integer
      - one or more spans of CHAPTER_TITLE_FONT carrying the title
    No other pages in the book use both of those font/size combinations.
    """
    chapters = {}
    for page_number, page in enumerate(doc, start=1):
        number = None
        title_parts = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                if not is_horizontal(l):
                    continue
                for s in l["spans"]:
                    text = s["text"].strip()
                    if not text:
                        continue
                    font = s["font"]
                    size = round(s["size"], 1)
                    if (font, size) == CHAPTER_NUMBER_FONT and re.fullmatch(r"\d+", text):
                        number = text
                    elif (font, size) == CHAPTER_TITLE_FONT:
                        title_parts.append(text)
        if number is not None and title_parts:
            chapters[page_number] = (number, clean_text(" ".join(title_parts)))
    return chapters


def detect_part_pages(doc):
    """Return the set of page numbers that are PART divider pages.
    These carry 'PART N' in TOPIC_FONT -- pure navigation, no prose content.
    """
    part_pages = set()
    for page_number, page in enumerate(doc, start=1):
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    text = s["text"].strip()
                    font = s["font"]
                    size = round(s["size"], 1)
                    if (font, size) == TOPIC_FONT and PART_RE.match(text):
                        part_pages.add(page_number)
    return part_pages


def extract_ordered_blocks(doc, chapter_pages, part_pages):
    """Yield (page_number, text, font, size) for every content block, in
    reading order, with noise pre-filtered:
      - PART divider pages are skipped entirely
      - Chapter-opener decoration (number + title glyphs) is skipped
      - Figure captions, table titles, TOC entries are skipped via SKIP_FONT_SIZES
    """
    for page_number, page in enumerate(doc, start=1):
        if page_number in part_pages:
            continue

        items = []
        for b in page.get_text("dict")["blocks"]:
            lines = b.get("lines")
            if not lines:
                continue
            if not all(is_horizontal(l) for l in lines):
                continue

            # Collect (y0, text, font, size) for this block
            block_text_parts = []
            dominant_font = None
            dominant_size = None
            max_chars = 0

            for l in lines:
                line_parts = []
                for s in l["spans"]:
                    t = s["text"]
                    if t.strip():
                        font = s["font"]
                        size = round(s["size"], 1)
                        line_parts.append(t)
                        char_count = len(t.strip())
                        if char_count > max_chars:
                            max_chars = char_count
                            dominant_font = font
                            dominant_size = size
                if line_parts:
                    if block_text_parts:
                        block_text_parts.append(" ")  # space between wrapped lines
                    block_text_parts.extend(line_parts)

            if not block_text_parts:
                continue

            text = clean_text("".join(block_text_parts))
            if not text:
                continue

            # Skip figure/table captions and TOC entries
            if (dominant_font, dominant_size) in SKIP_FONT_SIZES:
                continue

            # Skip chapter opener decoration (the big title/number glyphs)
            if page_number in chapter_pages:
                if (dominant_font, dominant_size) == CHAPTER_TITLE_FONT:
                    continue
                if (dominant_font, dominant_size) == CHAPTER_NUMBER_FONT and re.fullmatch(r"\d+", text):
                    continue

            y0 = b["bbox"][1]
            items.append((y0, text, dominant_font, dominant_size))

        for _, text, font, size in sorted(items, key=lambda v: v[0]):
            yield page_number, text, font, size


class HierarchyBuilder:
    """Accumulates Chapter → Topic → Subtopic → Paragraph hierarchy.
    IDs are hierarchical (e.g. ECG_PT1_CH02_TP03_ST01_PA004) for easy tracing.
    """

    def __init__(self, book_id):
        self.book_id = book_id
        self.chapters, self.topics, self.subtopics, self.paragraphs = [], [], [], []
        self.chapter_id = self.topic_id = self.subtopic_id = None
        self.chapter_idx = self.topic_idx = self.subtopic_idx = self.para_idx = 0
        self.last_chapter_num = None
        self.para_buffer = []
        self.para_page = None

    def start_chapter(self, title, page, chapter_num):
        if chapter_num is not None and chapter_num == self.last_chapter_num:
            return
        self.last_chapter_num = chapter_num
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
            self.start_chapter("Untitled Chapter", page, None)

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


def build_hierarchy(doc):
    chapter_pages = detect_chapter_pages(doc)
    part_pages = detect_part_pages(doc)
    builder = HierarchyBuilder(BOOK_ID)

    chapter_seen = False

    for page_number, text, font, size in extract_ordered_blocks(doc, chapter_pages, part_pages):

        # New chapter
        if page_number in chapter_pages and not chapter_seen:
            num, title = chapter_pages[page_number]
            builder.start_chapter(title, page_number, num)
            chapter_seen = True
            continue

        if page_number in chapter_pages:
            num, title = chapter_pages[page_number]
            builder.start_chapter(title, page_number, num)
            continue

        if not chapter_seen:
            continue  # skip any front matter before chapter 1

        # Topic heading
        if (font, size) == TOPIC_FONT and not PART_RE.match(text):
            builder.start_topic(text, page_number)
            continue

        # Subtopic heading — Arial-BoldMT @ 19.5pt that is NOT a bare digit
        # (bare digits were already filtered out in extract_ordered_blocks as
        # chapter-opener decoration, but guard here too for safety)
        if (font, size) == SUBTOPIC_FONT and not re.fullmatch(r"\d+", text) and text != "OUTLINE":
            builder.start_subtopic(text, page_number)
            continue

        # Everything else is body text (PalatinoLinotype-Roman/Bold at any
        # retained size, plus the 11.2pt PalatinoLinotype content from Q&A
        # sections and the bullet-list 19.5pt PalatinoLinotype-Roman blocks)
        builder.add_body(text, page_number)

    return builder.finish()


def main():
    doc = fitz.open(PDF_PATH)
    chapters, topics, subtopics, paragraphs = build_hierarchy(doc)

    print(f"Chapters  : {len(chapters)}")
    print(f"Topics    : {len(topics)}")
    print(f"Subtopics : {len(subtopics)}")
    print(f"Paragraphs: {len(paragraphs)}")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "chapters": chapters,
            "topics": topics,
            "subtopics": subtopics,
            "paragraphs": paragraphs,
        }, f, ensure_ascii=False, indent=2)

    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
