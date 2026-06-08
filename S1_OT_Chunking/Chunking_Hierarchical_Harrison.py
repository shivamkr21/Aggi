import fitz
import re
import json
from collections import Counter

PDF_PATH = "./PDF/Harrison_PT20.pdf"
OUTPUT_PATH = "P20_Hierarchical_Chunks.json"
BOOK_ID = "HARRISON_PT20"

MIN_PARAGRAPH_CHARS = 80
TOPIC_GLYPH = "■"
FURTHER_READING_RE = re.compile(r"^FURTHER READING", re.IGNORECASE)
TABLE_CAPTION_RE = re.compile(r"^TABLE\s+\d", re.IGNORECASE)

# 'CHAPTER N' only ever appears as rotated sidebar text in this document, so it
# can't anchor chapter boundaries. Each chapter instead *opens* with its number
# set large (~29pt) and its title set at ~16pt, both in this decorative font
# (used nowhere else) -- that combination reliably marks a chapter's first page.
CHAPTER_TITLE_FONT = "GaramondPremrPro-Smbd"

SENTENCE_END_RE = re.compile(r'[.?!”"]\s*$')


def starts_new_paragraph(buffer_text, next_text):
    """A PDF paragraph commonly arrives as several blocks (it wraps across
    columns/pages). Treat a new block as the *same* paragraph continuing
    unless the buffered text ends a sentence AND the new block opens with a
    capital letter -- that combination is what actually marks a fresh
    paragraph rather than a wrapped continuation."""
    if not buffer_text or not next_text:
        return True
    return bool(SENTENCE_END_RE.search(buffer_text)) and next_text[0].isupper()


def clean_text(text: str) -> str:
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    text = text.replace("- ", "")
    return text.strip()


def is_horizontal(line: dict) -> bool:
    """PyMuPDF gives each line a writing-direction vector; rotated/vertical
    sidebar text (running headers printed in the page margin) has a non-zero
    y-component. Dropping these removes the 'PART 1 ...' margin noise."""
    _, dy = line.get("dir", (1.0, 0.0))
    return abs(dy) < 0.1


def is_page_number(text: str, avg_size: float) -> bool:
    return bool(re.fullmatch(r"\d{1,4}", text)) and avg_size < 8.5


def merge_spans(spans):
    """Collapse consecutive spans sharing the same font+size into runs of
    (text, font, size). This lets us see *where* the font changes within a
    block, which is how run-in headings ('History-Taking <body text>...')
    get told apart from the paragraph text that follows them."""
    runs = []
    for s in spans:
        text = s["text"]
        if not text.strip():
            continue
        font, size = s["font"], round(s["size"], 1)
        if runs and runs[-1][1] == font and runs[-1][2] == size:
            runs[-1] = (runs[-1][0] + text, font, size)
        else:
            runs.append((text, font, size))
    return runs


def estimate_body_font(doc):
    counter = Counter()
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                if not is_horizontal(l):
                    continue
                for s in l["spans"]:
                    if s["text"].strip():
                        counter[(s["font"], round(s["size"], 1))] += 1
        if counter.total() > 2000:
            break
    return counter.most_common(1)[0][0]  # (font_name, size)


def extract_ordered_blocks(doc):
    """Yield (page_number, text, runs) in reading order with noise dropped:
    rotated sidebar headers, page numbers, and table/figure content
    (set in the UniversLTStd family, visually distinct from body prose).

    Reading order is resolved per page by bucketing each block into a left
    or right column based on its horizontal center, then sorting each
    column top-to-bottom -- this avoids the column-interleaving that plain
    block iteration produces on this two-column layout.
    """
    page_width = doc[0].rect.width
    mid_x = page_width / 2

    for page_number, page in enumerate(doc, start=1):
        left, right = [], []

        for b in page.get_text("dict")["blocks"]:
            lines = b.get("lines")
            if not lines or not all(is_horizontal(l) for l in lines):
                continue

            runs = []
            for l in lines:
                runs.extend(merge_spans(l["spans"]))
            if not runs:
                continue

            text = clean_text("".join(r[0] for r in runs))
            if not text:
                continue

            avg_size = sum(r[2] for r in runs) / len(runs)
            if is_page_number(text, avg_size):
                continue
            if runs[0][1].startswith(("UniversLTStd", "HelveticaLTStd")):
                continue  # tables (UniversLTStd) / chart & diagram labels (HelveticaLTStd)
            if TABLE_CAPTION_RE.match(text):
                continue  # caption left orphaned once its table body is dropped above

            x0, y0, x1, _ = b["bbox"]
            bucket = left if (x0 + x1) / 2 < mid_x else right
            bucket.append((y0, text, runs))

        for _, text, runs in sorted(left, key=lambda v: v[0]):
            yield page_number, text, runs
        for _, text, runs in sorted(right, key=lambda v: v[0]):
            yield page_number, text, runs


def detect_chapter_starts(doc):
    """Map page_number -> (chapter_num, chapter_title) for each page that
    opens a chapter, identified by the large-number + title pair rendered in
    CHAPTER_TITLE_FONT (e.g. '1' at 29pt and 'The Practice of Medicine' at
    16pt on the same page)."""
    starts = {}
    for page_number, page in enumerate(doc, start=1):
        num_text = None
        title_parts = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                if not is_horizontal(l):
                    continue
                for s in l["spans"]:
                    if s["font"] != CHAPTER_TITLE_FONT:
                        continue
                    txt = s["text"].strip()
                    if not txt:
                        continue
                    size = round(s["size"], 1)
                    if size > 25 and txt.isdigit():
                        num_text = txt
                    elif 15 <= size <= 17:
                        title_parts.append(txt)
        if num_text is not None:
            starts[page_number] = (num_text, clean_text(" ".join(title_parts)))
    return starts


def split_inline_heading(runs, body_font):
    """A heading and the paragraph that follows it are often a single
    PyMuPDF block (e.g. 'History-Taking<heading font> The recorded
    history...<body font>'). Find the run where the font reverts to body
    text and split there. If it never reverts, the whole thing is heading."""
    body_name, body_size = body_font
    for i in range(1, len(runs)):
        if runs[i][1] == body_name and abs(runs[i][2] - body_size) < 0.5:
            heading = clean_text("".join(r[0] for r in runs[:i]))
            rest = clean_text("".join(r[0] for r in runs[i:]))
            return heading, rest
    return clean_text("".join(r[0] for r in runs)), ""


def classify_block(text, runs, body_font):
    """Returns (kind, payload):
      'topic'    -> payload (title, inline_lead_in_text)
      'subtopic' -> payload (title, inline_lead_in_text)
      'body'     -> payload None
    Chapter boundaries are handled separately via detect_chapter_starts(),
    since 'CHAPTER N' never appears as regular horizontal heading text.
    """
    body_name, body_size = body_font
    lead_font, lead_size = runs[0][1], runs[0][2]
    is_heading_font = (lead_font != body_name) or (lead_size > body_size + 0.5)

    if text.lstrip().startswith(TOPIC_GLYPH):
        heading, rest = split_inline_heading(runs, body_font)
        heading = clean_text(heading.lstrip(f"{TOPIC_GLYPH} "))
        return "topic", (heading, rest)

    if is_heading_font:
        heading, rest = split_inline_heading(runs, body_font)
        if heading and len(heading) < 80:
            return "subtopic", (heading, rest)

    return "body", None


class HierarchyBuilder:
    """Accumulates the Chapter -> Topic -> Subtopic -> Paragraph hierarchy.
    Each level stores its own id plus its immediate parent's id, exactly as
    requested. IDs are derived from the parent id, so they double as a
    readable trail back to the book (e.g. '..._CH02_TP03_ST01_PA004')."""

    def __init__(self, book_id):
        self.book_id = book_id
        self.chapters, self.topics, self.subtopics, self.paragraphs = [], [], [], []

        self.chapter_id = self.topic_id = self.subtopic_id = None
        self.chapter_idx = self.topic_idx = self.subtopic_idx = self.para_idx = 0
        self.last_chapter_num = None

        self.para_buffer = []
        self.para_page = None

    # --- structure transitions -------------------------------------------------
    def start_chapter(self, title, page, chapter_num):
        if chapter_num is not None and chapter_num == self.last_chapter_num:
            return  # recurring running-header repeat of the same chapter, ignore
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

    # --- fallbacks for body text encountered without explicit headings ---------
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

    # --- paragraph accumulation -------------------------------------------------
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


def build_hierarchy(doc, book_id=BOOK_ID):
    body_font = estimate_body_font(doc)
    chapter_starts = detect_chapter_starts(doc)
    builder = HierarchyBuilder(book_id)

    chapter_seen = False
    in_references = False
    current_page = None

    for page_number, text, runs in extract_ordered_blocks(doc):
        if page_number != current_page:
            current_page = page_number
            if page_number in chapter_starts:
                num, title = chapter_starts[page_number]
                builder.start_chapter(title or f"Chapter {num}", page_number, num)
                chapter_seen = True
                in_references = False

        if any(r[1] == CHAPTER_TITLE_FONT for r in runs):
            continue  # chapter-opener decoration (number + title), not body content

        if not chapter_seen:
            continue  # drop front matter that precedes the first chapter

        kind, payload = classify_block(text, runs, body_font)

        if kind == "topic":
            title, lead_in = payload
            if FURTHER_READING_RE.match(title):
                in_references = True  # bibliography block: skip to next chapter
                continue
            in_references = False
            builder.start_topic(title, page_number)
            if lead_in:
                builder.add_body(lead_in, page_number)
            continue

        if in_references:
            continue

        if kind == "subtopic":
            title, lead_in = payload
            builder.start_subtopic(title, page_number)
            if lead_in:
                builder.add_body(lead_in, page_number)
            continue

        builder.add_body(text, page_number)

    return builder.finish()


def main():
    doc = fitz.open(PDF_PATH)
    chapters, topics, subtopics, paragraphs = build_hierarchy(doc)

    print(f"Chapters: {len(chapters)}")
    print(f"Topics: {len(topics)}")
    print(f"Subtopics: {len(subtopics)}")
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
