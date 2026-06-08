import fitz
import re
import json
from collections import Counter

PDF_PATH = "./PDF/2 BRS Pathology/BRS_PT3.pdf"
OUTPUT_PATH = "P3_Hierarchical_Chunks.json"
BOOK_ID = "BRS_PT3"

MIN_PARAGRAPH_CHARS = 80

# BRS is an outline-style review book -- headings are plain numbered/lettered
# list markers (no glyphs, no special chapter-only fonts) set in the same
# Univers-CondensedBold family used for *all* inline emphasis. Size is what
# separates the two heading levels we care about from run-of-the-mill bold text:
#   - Topic    : roman numerals  'I.' 'II.' ...   @ 14.0pt
#   - Subtopic : capital letters 'A.' 'B.' ...    @  9.5pt (same size as inline
#                bold/emphasis and the deeper a./1./(1) outline levels, so the
#                leading-letter pattern is what actually distinguishes them)
TOPIC_FONT = ("Univers-CondensedBold", 14.0)
SUBTOPIC_FONT = ("Univers-CondensedBold", 9.5)
ROMAN_TOPIC_RE = re.compile(r"^[IVXLCDM]+\.\s*")
SUBTOPIC_RE = re.compile(r"^[A-Z]\.\s+")

# Each chapter opens with a block grouping its (possibly multi-line) title in
# Utopia-Regular @ 22pt, a large number in Univers-CondensedBold @ 44pt, and a
# small-caps 'c h a p t e r' label -- all three together, nowhere else in the book.
CHAPTER_TITLE_FONT = ("Utopia-Regular", 22.0)
CHAPTER_NUMBER_FONT = ("Univers-CondensedBold", 44.0)
CHAPTER_LABEL_RE = re.compile(r"^c\s*h\s*a\s*p\s*t\s*e\s*r$", re.IGNORECASE)

# End-of-chapter quiz sections ('Review Test' / 'Answers and Explanations',
# both set in Utopia-Regular @ 21pt) are Q&A material, not narrative prose --
# skip them the same way Harrison's 'FURTHER READING' bibliographies are skipped.
REVIEW_SECTION_RE = re.compile(r"^(Review Test|Answers and Explanations)", re.IGNORECASE)

# Figure captions ('FIGURE 1-1 Marked atrophy...') and table titles ('2-2
# t a b l e Vasoactive Mediators', the same letter-spaced labelling style as
# the chapter opener's 'c h a p t e r') both introduce visual-reference
# material that doesn't belong in narrative prose.
FIGURE_CAPTION_RE = re.compile(r"^FIGURE\s+\d", re.IGNORECASE)
TABLE_TITLE_RE = re.compile(r"^\d+-\d+\s+t\s*a\s*b\s*l\s*e\b", re.IGNORECASE)

# Running headers/footers ('Chapter N <chapter title>   NN' on most pages,
# 'NN   BRS Pathology' on others) consistently sit in the y=34..46 band at
# the very top margin -- well above where any real body content starts
# (the earliest seen is y=58). Position is a far more reliable signal here
# than text-pattern matching, since the two header styles share no wording.
HEADER_FOOTER_Y_MAX = 50

SENTENCE_END_RE = re.compile(r'[.?!”"]\s*$')


def starts_new_paragraph(buffer_text, next_text):
    """A PDF paragraph commonly arrives as several blocks (it wraps across
    lines/pages, and outline sub-points such as '1.' / 'a.' continue the same
    thought). Treat a new block as the *same* paragraph continuing unless the
    buffered text ends a sentence AND the new block opens with a capital
    letter -- numbered/lettered outline markers start with digits or
    lowercase letters, so they correctly fall through as continuations."""
    if not buffer_text or not next_text:
        return True
    return bool(SENTENCE_END_RE.search(buffer_text)) and next_text[0].isupper()


def clean_text(text: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)  # stray control chars (e.g. '\x07' glyphs)
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    text = text.replace("- ", "")
    return text.strip()


def is_horizontal(line: dict) -> bool:
    _, dy = line.get("dir", (1.0, 0.0))
    return abs(dy) < 0.1


def is_page_number(text: str, avg_size: float) -> bool:
    return bool(re.fullmatch(r"\d{1,4}", text)) and avg_size < 8.5


def merge_spans(spans):
    """Collapse consecutive spans sharing the same font+size into runs of
    (text, font, size). This lets us see *where* the font changes within a
    block, which is how run-in headings ('A.\\tRestoration of normal
    structure.<heading font> This occurs when...<body font>') get told apart
    from the paragraph text that follows them.

    Whitespace-only spans are folded into the *previous* run rather than
    dropped or treated as their own run: BRS frequently switches fonts right
    at a bare-space boundary (e.g. 'bone marrow transplantation'<bold> ' '
    <regular>'because...'<regular>), and discarding that space would glue the
    two words together (-> 'transplantationbecause'). Its own font/size is
    irrelevant -- a space looks the same either way -- so it can't fracture
    what is really one continuous run of text."""
    runs = []
    for s in spans:
        text = s["text"]
        if not text:
            continue
        if not text.strip():
            if runs:
                runs[-1] = (runs[-1][0] + text, runs[-1][1], runs[-1][2])
            else:
                runs.append((text, s["font"], round(s["size"], 1)))
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


def is_table_or_caption(runs):
    """Comparison-table cell text and figure/table captions are set in a
    grab-bag of small-size fonts -- 'Univers-Condensed/-Bold/-Light @ 7-7.5',
    'Symbol @ 7.5' for Greek letters like the alpha in 'α-L-Iduronidase',
    and similar -- all comfortably below the body prose size (Utopia-Regular
    @ 9.0) and the heading-marker sizes (9.5pt/14.0pt). A block is
    non-prose supplementary material precisely when *every* run in it sits
    below that floor; mixed blocks (body text with the occasional small
    subscript, e.g. 'GM<2>') still have their 9.0pt runs and pass through."""
    return all(size < 9.0 for _, _, size in runs)


def extract_ordered_blocks(doc):
    """Yield (page_number, text, runs) in reading order with noise dropped:
    page headers/footers ('BRS Pathology' + page number), figure/table
    captions, and comparison-table contents (Univers-Condensed family).

    Unlike Harrison's two-column layout, BRS is single-column with figures
    floating beside the text -- since captions/tables are dropped outright,
    a simple top-to-bottom sort by block position gives correct reading order.
    """
    for page_number, page in enumerate(doc, start=1):
        items = []

        for b in page.get_text("dict")["blocks"]:
            lines = b.get("lines")
            if not lines or not all(is_horizontal(l) for l in lines):
                continue

            runs = []
            for l in lines:
                line_runs = merge_spans(l["spans"])
                if not line_runs:
                    continue
                if runs:
                    # PDF line wraps don't always carry an explicit space char
                    # (e.g. a line ending '...Causes' followed by 'a. Ischemic
                    # heart disease...') -- insert one between lines so words
                    # never glue together; clean_text() collapses any doubles.
                    runs.append((" ", line_runs[0][1], line_runs[0][2]))
                runs.extend(line_runs)
            if not runs:
                continue

            text = clean_text("".join(r[0] for r in runs))
            if not text:
                continue

            avg_size = sum(r[2] for r in runs) / len(runs)
            if is_page_number(text, avg_size):
                continue
            y0, y1 = b["bbox"][1], b["bbox"][3]
            if y0 > 30 and y1 < HEADER_FOOTER_Y_MAX:
                continue  # running header/footer band: 'Chapter N <title>  NN' / 'NN  BRS Pathology'
            if FIGURE_CAPTION_RE.match(text) or TABLE_TITLE_RE.match(text):
                continue  # figure captions / table titles -- visual references, not prose
            if is_table_or_caption(runs):
                continue  # comparison-table cell contents

            items.append((y0, text, runs))

        for _, text, runs in sorted(items, key=lambda v: v[0]):
            yield page_number, text, runs


def detect_chapter_starts(doc):
    """Map page_number -> (chapter_num, chapter_title) for each page that
    opens a chapter. Each opener combines a number in CHAPTER_NUMBER_FONT, a
    small-caps 'c h a p t e r' / 'cha p t e r' label (the letter-spacing
    varies between parts), and a (possibly multi-line) title in
    CHAPTER_TITLE_FONT -- e.g. '1' + 'c h a p t e r' + 'Cellular Reaction' /
    'to Injury'. On most chapters all three live in one block, but on some
    (long titles needing their own text box) the title sits in a *separate*
    block alongside the number+label block -- so detection scans the whole
    page rather than block-by-block. Nothing else on a page uses these
    fonts/sizes, so collecting them page-wide carries no risk of false hits.
    """
    starts = {}
    for page_number, page in enumerate(doc, start=1):
        has_label = False
        number = None
        title_parts = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                if not is_horizontal(l):
                    continue
                spans = l["spans"]
                if not spans:
                    continue
                text = clean_text("".join(s["text"] for s in spans))
                if not text:
                    continue
                font, size = spans[0]["font"], round(spans[0]["size"], 1)
                if CHAPTER_LABEL_RE.match(text):
                    has_label = True
                elif (font, size) == CHAPTER_NUMBER_FONT and text.isdigit():
                    number = text
                elif (font, size) == CHAPTER_TITLE_FONT:
                    title_parts.append(text)
        if has_label and number is not None:
            starts[page_number] = (number, clean_text(" ".join(title_parts)))
    return starts


def split_inline_heading(runs, body_font):
    """A heading and the paragraph that follows it are often a single
    PyMuPDF block (e.g. 'A.\\tRestoration of normal structure.<heading font>
    This occurs when the connective tissue...<body font>'). Find the run
    where the font reverts to body text and split there. If it never
    reverts, the whole thing is heading."""
    body_name, body_size = body_font
    for i in range(1, len(runs)):
        if runs[i][1] == body_name and abs(runs[i][2] - body_size) < 0.5:
            heading = clean_text("".join(r[0] for r in runs[:i]))
            rest = clean_text("".join(r[0] for r in runs[i:]))
            return heading, rest
    return clean_text("".join(r[0] for r in runs)), ""


def classify_block(text, runs, body_font):
    """Returns (kind, payload):
      'topic'              -> payload (title, inline_lead_in_text)
      'topic_continuation' -> payload title_fragment
      'subtopic'           -> payload (title, inline_lead_in_text)
      'body'               -> payload None

    BRS nests its outline far deeper than chapter/topic/subtopic/paragraph
    (roman numerals -> capital letters -> numbers -> lowercase letters ->
    parenthesised numbers, all set in the *same* Univers-CondensedBold font
    family). Rather than rely on font alone -- which can't tell 'A.' from
    'a.' or '(1)' -- each level is matched by its leading-marker pattern at
    its specific size; deeper outline levels simply flow into paragraph text.
    """
    lead_font, lead_size = runs[0][1], round(runs[0][2], 1)

    if (lead_font, lead_size) == TOPIC_FONT:
        if ROMAN_TOPIC_RE.match(text):
            heading, rest = split_inline_heading(runs, body_font)
            title = ROMAN_TOPIC_RE.sub("", heading, count=1).strip()
            return "topic", (title, rest)
        # Pure 14pt-bold text with no roman-numeral marker only occurs when a
        # topic title wraps onto a second block (e.g. 'VIII. Disorders...'
        # then 'of Protein Folding') -- fold it back into the topic title.
        return "topic_continuation", clean_text(text)

    if (lead_font, lead_size) == SUBTOPIC_FONT and SUBTOPIC_RE.match(text):
        heading, rest = split_inline_heading(runs, body_font)
        title = SUBTOPIC_RE.sub("", heading, count=1).strip()
        return "subtopic", (title, rest)

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

    def extend_topic_title(self, fragment):
        """Fold a wrapped continuation line back into the most recent topic's
        title (e.g. 'VIII. Disorders Characterized by Abnormalities' + 'of
        Protein Folding' -> one title). No-op if no topic is open yet."""
        if self.topics:
            self.topics[-1]["title"] = clean_text(f"{self.topics[-1]['title']} {fragment}")

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
    in_review = False
    current_page = None

    for page_number, text, runs in extract_ordered_blocks(doc):
        if page_number != current_page:
            current_page = page_number
            if page_number in chapter_starts:
                num, title = chapter_starts[page_number]
                builder.start_chapter(title or f"Chapter {num}", page_number, num)
                chapter_seen = True
                in_review = False

        if any((r[1], round(r[2], 1)) == CHAPTER_TITLE_FONT for r in runs):
            continue  # chapter-opener decoration (number + title), not body content

        if not chapter_seen:
            continue  # drop front matter that precedes the first chapter

        if REVIEW_SECTION_RE.match(text):
            in_review = True  # 'Review Test' / 'Answers and Explanations': skip to next chapter
            continue

        if in_review:
            continue

        kind, payload = classify_block(text, runs, body_font)

        if kind == "topic":
            title, lead_in = payload
            builder.start_topic(title, page_number)
            if lead_in:
                builder.add_body(lead_in, page_number)
            continue

        if kind == "topic_continuation":
            builder.extend_topic_title(payload)
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
