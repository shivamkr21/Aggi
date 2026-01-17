import fitz  # PyMuPDF
import json
import re

# =========================
# CONFIGURATION
# =========================

PDF_PATH = "./PDF/Harrison_CH_1_12.pdf"
MIN_PARAGRAPH_LENGTH = 40  # characters

# =========================
# CLEANING UTIL
# =========================

def clean_text(text: str) -> str:
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    text = text.replace("- ", "")  # fix hyphen line breaks
    return text.strip()

# =========================
# PARAGRAPH EXTRACTION
# =========================

def extract_paragraphs_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    paragraphs = []
    para_id = 1
    num = 0
    val = 0

    for page_number, page in enumerate(doc, start=1):
        num +=1
        blocks = page.get_text("blocks")
        
        for block in blocks:
            raw_text = block[4].strip()

            if len(raw_text) < MIN_PARAGRAPH_LENGTH:
                continue

            cleaned = clean_text(raw_text)

            paragraphs.append({
                "paragraph_id": para_id,
                "page": page_number,
                "text": cleaned
            })

            para_id += 1

        #print(blocks)

        break

        print(str(num) + " - " + str(len(blocks)) + " - " + str(val))

    return paragraphs

def extract_paragraphs_from_pdf_blocks(pdf_path):
    doc = fitz.open(pdf_path)
    paragraphs = []
    para_id = 1
    num = 0
    val = 0

    for page_number, page in enumerate(doc, start=1):
        num +=1
        blocks = page.get_text("dict")

        para_id += 1

        for i in blocks["blocks"]:
            print(i)
            break
        #keys = list(blocks.keys())
        #print(keys)


        break

        print(str(num) + " - " + str(len(blocks)) + " - " + str(val))

    return paragraphs

# =========================
# MAIN
# =========================

def main():
    print("Reading PDF...")
    paragraphs = extract_paragraphs_from_pdf(PDF_PATH)

    print(f"Extracted {len(paragraphs)} paragraphs\n")

    #print(json.dumps(paragraphs, indent=2, ensure_ascii=False))

    chp_output_file = "Paragraph.txt"
    #chp_output_file = "Blocks.txt"

    with open(chp_output_file, "w", encoding="utf-8") as f:
        for i in paragraphs:
            f.write("{")
            f.write("\n")
            f.write("   'paragraph_id': '" + str(i["paragraph_id"]) + "', ")
            f.write("'page': " + str(i["page"]) + ", ")
            f.write("'text': " + str(i["text"]) + ", ")
            f.write("\n")
            f.write("},")

    paragraphs = extract_paragraphs_from_pdf_blocks(PDF_PATH)


if __name__ == "__main__":
    main()
