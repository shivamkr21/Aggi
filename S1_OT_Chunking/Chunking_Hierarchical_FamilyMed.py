import re
import json
import fitz

PDF_PATH = "./PDF/4 Family Medicine/FamilyMed_PT1.pdf"
OUTPUT_PATH = "FamilyMed_PT1_Hierarchical_Chunks.json"
BOOK_ID = "FamilyMed_PT1"

MIN_PARAGRAPH_CHARS = 80

# ── Font signatures ────────────────────────────────────────────────────────────
# Chapter opener: FuturaStd-Medium @ 11.0pt
#   Two consecutive spans — first is the chapter number ("1", "2", ...),
#   second is the chapter title ("Abdominal Pain", "Anemia", ...).
# IMPORTANT: On page 1, the chapter header appears at the BOTTOM of the page
# (after the chapter content), so a simple inline scan would miss all chapter-
# 1 content. A pre-scan pass maps each page to the chapter that starts on it;
# the main pass uses that map to open a chapter as soon as its page begins.
CHAPTER_FONT = ("FuturaStd-Medium", 11.0)

# Topic heading within a chapter: FuturaStd-Bold @ 9.0pt.
# "KEY POINTS"        → body text (valuable content), not a new topic.
# "SELECTED REFERENCES" → marks end of chapter prose; skip until next chapter.
TOPIC_FONT = ("FuturaStd-Bold", 9.0)

# Outline headings embedded in body text (same font as body text):
#   Roman numeral  "I.\s+"  → Topic    (I, II, III, IV ...)
#   Capital letter "A.\s+"  → Subtopic (A, B, C ...)
# Capital letter check requires the next character also to be uppercase so
# mid-sentence fragments like "A. aureus..." are not misclassified.
ROMAN_RE  = re.compile(r'^(I{1,4}|IV|IX|V?I{1,3}|XI{0,3}|XIV|XV?I{0,3}|X{2,3})\.\s+([A-Z].+)', re.DOTALL)
LETTER_RE = re.compile(r'^([A-Z])\.\s+([A-Z].+)', re.DOTALL)

# Body text fonts (FuturaStd variants at 7.0–7.5 pt)
BODY_FONTS = {
    ("FuturaStd-Book",        7.5), ("FuturaStd-Bold",        7.5),
    ("FuturaStd-BookOblique", 7.5), ("FuturaStd-BoldOblique", 7.5),
    ("FuturaStd-Book",        7.0), ("FuturaStd-Bold",        7.0),
    ("FuturaStd-BookOblique", 7.0), ("FuturaStd-BoldOblique", 7.0),
    ("FuturaStd-Book",        4.9),
}

# Fonts / sizes to skip entirely:
#   FuturaStd-Medium 6.5 — running headers ("FAMILY MEDICINE", "1: Abdominal Pain",
#                           bare page numbers)
#   FuturaStd-Medium 8.0 — large page number in top margin
#   FuturaStd-Medium 8.5 — author byline on chapter openers
#   FuturaStd-Medium 13.0 — "SECTION I." part-divider text
#   FuturaStd-Bold 6.5   — table column headers, figure captions ("FIGURE N–N")
#   FuturaStd-Bold 13.0  — section divider headline (older scan artefact)
#   FuturaStd-CondensedBold 6.5 — table group headers
#   FuturaStd-MediumOblique 6.5 — table footnotes / reference citations
#   FuturaStd-BookOblique 6.5   — short reference snippets
#   UniMath-Regular      — URL strings in reference lists
#   MinionPro-*          — flowchart / algorithm text boxes
#   Helvetica / -Bold    — anatomical diagram labels
#   Anything ≤ 4.5 pt    — superscript footnote markers
SKIP_FONTS = {
    ("FuturaStd-Medium",         6.5),
    ("FuturaStd-Medium",         8.0),
    ("FuturaStd-Medium",         8.5),
    ("FuturaStd-Medium",        13.0),
    ("FuturaStd-Bold",           6.5),
    ("FuturaStd-Bold",          13.0),
    ("FuturaStd-CondensedBold",  6.5),
    ("FuturaStd-MediumOblique",  6.5),
    ("FuturaStd-BookOblique",    6.5),
    ("UniMath-Regular",          7.0),
}
SKIP_FONT_PREFIXES = ("MinionPro", "Helvetica")
MAX_SKIP_SIZE = 4.5

SENTENCE_END_RE = re.compile(r'[.?!""]\s*$')


def clean_text(text: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
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


# ── Pass 1: locate chapter starting pages ─────────────────────────────────────
def detect_chapter_pages(doc):
    """Return {page_number: (chapter_num_str, chapter_title)}.

    Each chapter opener contains two FuturaStd-Medium @ 11.0pt lines:
      line 1 = bare digit   ("1", "2", ...)
      line 2 = chapter title ("Abdominal Pain", ...)
    They appear anywhere on the page (sometimes at the bottom, after content).
    We record the page as the START of that chapter so body content above the
    header on the same page still gets attributed to the right chapter.
    """
    chapters = {}
    for page_number, page in enumerate(doc, start=1):
        lines_11 = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                if not is_horizontal(l):
                    continue
                for s in l["spans"]:
                    t = s["text"].strip()
                    if not t:
                        continue
                    if s["font"] == CHAPTER_FONT[0] and round(s["size"], 1) == CHAPTER_FONT[1]:
                        lines_11.append(t)

        # Look for a digit immediately followed by a title
        for i in range(len(lines_11) - 1):
            if re.fullmatch(r"\d+", lines_11[i]):
                chapters[page_number] = (lines_11[i], lines_11[i + 1])
                break

    return chapters


# ── Pass 2: extract ordered content blocks ────────────────────────────────────
def extract_ordered_blocks(doc):
    """Yield (page_number, text, font, size) sorted top-to-bottom per page,
    with noise pre-filtered."""
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

            # Skip CHAPTER_FONT blocks — already handled via chapter_pages map
            if (dom_font, dom_size) == CHAPTER_FONT:
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

    def start_chapter(self, title, page, chapter_num):
        if chapter_num is not None and chapter_num == self.last_chapter_num:
            return
        self.last_chapter_num = chapter_num
        self.flush_paragraph()
        self.chapter_idx += 1
        self.chapter_id = f"{self.book_id}_CH{self.chapter_idx:03d}"
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


# ── Main build ────────────────────────────────────────────────────────────────
def build_hierarchy(doc):
    chapter_pages = detect_chapter_pages(doc)
    builder = HierarchyBuilder(BOOK_ID)

    in_references = False
    current_page = None

    for page_number, text, font, size in extract_ordered_blocks(doc):

        # Open new chapter as soon as we enter its page (before any body
        # content on that page, since headers may appear at the bottom).
        if page_number != current_page:
            current_page = page_number
            if page_number in chapter_pages:
                num, title = chapter_pages[page_number]
                builder.start_chapter(title, page_number, num)
                in_references = False

        # ── Topic-level headings (9 pt Bold) ──────────────────────────────
        if (font, size) == TOPIC_FONT:
            if text == "SELECTED REFERENCES":
                in_references = True
                continue
            if in_references:
                in_references = False
            if text == "KEY POINTS":
                # Body content, not a structural heading — let it flow
                continue
            builder.start_topic(text, page_number)
            continue

        if in_references:
            continue

        # ── Outline headings embedded in body text ─────────────────────────
        if (font, size) in BODY_FONTS:

            # Roman numeral → new Topic
            m = ROMAN_RE.match(text)
            if m:
                numeral, rest = m.group(1), m.group(2).strip()
                # Extract short title (text up to first period, within 60 chars).
                # If no period within that range, the line has no standalone
                # title — use the first 5 words as a label and keep the full
                # text as the body.
                title_m = re.match(r'^([^.]{1,60})\.\s*(.*)', rest, re.DOTALL)
                if title_m:
                    title     = title_m.group(1).strip()
                    body_rest = title_m.group(2).strip()
                else:
                    title     = " ".join(rest.split()[:5])
                    body_rest = rest
                builder.start_topic(f"{numeral}. {title}", page_number)
                if body_rest:
                    builder.add_body(body_rest, page_number)
                continue

            # Capital letter → new Subtopic
            m = LETTER_RE.match(text)
            if m:
                letter, rest = m.group(1), m.group(2).strip()
                # Extract short title (text up to first period, within 60 chars).
                # If no period within that range, the line has no standalone
                # title — use the first 5 words as a label and keep the full
                # text as the body.
                title_m = re.match(r'^([^.]{1,60})\.\s*(.*)', rest, re.DOTALL)
                if title_m:
                    title     = title_m.group(1).strip()
                    body_rest = title_m.group(2).strip()
                else:
                    title     = " ".join(rest.split()[:5])
                    body_rest = rest
                builder.start_subtopic(f"{letter}. {title}", page_number)
                if body_rest:
                    builder.add_body(body_rest, page_number)
                continue

            # Plain body text
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
