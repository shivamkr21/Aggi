from PyPDF2 import PdfReader, PdfWriter
import os

def split_pdf_by_ranges(
    input_pdf_path: str,
    page_ranges: list,
    output_dir: str
):

    os.makedirs(output_dir, exist_ok=True)

    reader = PdfReader(input_pdf_path)
    total_pages = len(reader.pages)

    for idx, (start, end) in enumerate(page_ranges, start=1):
        if start < 1 or end > total_pages or start > end:
            raise ValueError(f"Invalid range: {start}-{end}")

        writer = PdfWriter()

        # Convert to 0-based index
        for page_num in range(start - 1, end):
            writer.add_page(reader.pages[page_num])

        output_path = os.path.join(
            output_dir, f"split_{idx}_{start}_to_{end}.pdf"
        )

        with open(output_path, "wb") as f:
            writer.write(f)

        print(f"Created: {output_path}")


# ---------------- USAGE ---------------- #

input_pdf = "./PDF/Harrison 2022 mobile edition.pdf"

ranges = [
    (42, 130)
#    (42, 3896)
]

output_folder = "./PDF/Split"

split_pdf_by_ranges(input_pdf, ranges, output_folder)
