"""Download a small sample of a JRA registry PDF and OCR selected pages.

This is a diagnostic tool: it writes raw OCR text for human review and does not
create horse/registration mappings or modify race-result data.
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

MAX_PDF_BYTES = 30 * 1024 * 1024
DEFAULT_URL = (
    "https://www.jra.go.jp/datafile/resist/pdf/registration_miho3-3.pdf"
)


def download_pdf(url: str, destination: Path, max_bytes: int = MAX_PDF_BYTES) -> None:
    parsed_url = urlsplit(url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "www.jra.go.jp":
        raise ValueError("Only HTTPS URLs on www.jra.go.jp are permitted")
    request = urllib.request.Request(
        url, headers={"User-Agent": "JRA-Horse-Data-Lab/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content_type = response.headers.get("Content-Type", "").lower()
        length = response.headers.get("Content-Length")
        if length:
            try:
                if int(length) < 1 or int(length) > max_bytes:
                    raise ValueError("PDF size is outside the permitted limit")
            except ValueError as exc:
                raise ValueError("Invalid or excessive Content-Length") from exc
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("PDF exceeds the permitted download limit")
    if not data.startswith(b"%PDF-"):
        raise ValueError("Downloaded content is not a PDF")
    if content_type and "pdf" not in content_type and "octet-stream" not in content_type:
        raise ValueError("Unexpected response content type")
    destination.write_bytes(data)


def extract_pdf_text(pdf_path: Path, first_page: int, last_page: int) -> str:
    """Extract positioned text from selected PDF pages for comparison with OCR."""
    if first_page < 1 or last_page < first_page:
        raise ValueError("Page numbers must be positive and ordered")
    if last_page - first_page > 4:
        raise ValueError("At most five pages may be extracted in one run")
    result = subprocess.run(
        [
            "pdftotext", "-f", str(first_page), "-l", str(last_page),
            "-layout", str(pdf_path), "-",
        ],
        check=True, capture_output=True, text=True, timeout=60,
    )
    if len(result.stdout) > 2 * 1024 * 1024:
        raise ValueError("Extracted PDF text exceeds the permitted 2 MiB limit")
    return result.stdout

def ocr_pdf_pages(pdf_path: Path, first_page: int, last_page: int, dpi: int = 300) -> str:
    if first_page < 1 or last_page < first_page:
        raise ValueError("Page numbers must be positive and ordered")
    if last_page - first_page > 4:
        raise ValueError("At most five pages may be OCRed in one run")
    with tempfile.TemporaryDirectory(prefix="jra-registry-ocr-") as temp:
        prefix = str(Path(temp) / "page")
        subprocess.run(
            [
                "pdftoppm", "-f", str(first_page), "-l", str(last_page),
                "-r", str(dpi), "-png", str(pdf_path), prefix,
            ],
            check=True, capture_output=True, text=True,
        )
        images = sorted(Path(temp).glob("page-*.png"))
        if not images:
            raise RuntimeError("No page images were produced")
        output = []
        for image in images:
            result = subprocess.run(
                ["tesseract", str(image), "stdout", "-l", "jpn", "--psm", "6"],
                check=True, capture_output=True, text=True,
            )
            output.append(result.stdout)
        return "\n".join(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--first-page", type=int, default=1)
    parser.add_argument("--last-page", type=int, default=1)
    parser.add_argument("--output", default="registry_ocr_sample.txt")
    parser.add_argument("--text-output", default="registry_text_sample.txt")
    args = parser.parse_args()
    output_path = Path(args.output)
    with tempfile.TemporaryDirectory(prefix="jra-registry-pdf-") as temp:
        pdf_path = Path(temp) / "registry.pdf"
        download_pdf(args.url, pdf_path)
        extracted_text = extract_pdf_text(pdf_path, args.first_page, args.last_page)
        text = ocr_pdf_pages(pdf_path, args.first_page, args.last_page)
    Path(args.text_output).write_text(extracted_text, encoding="utf-8")
    output_path.write_text(text, encoding="utf-8")
    print(f"PDF text written to {args.text_output}")
    print(f"OCR text written to {output_path}")
    print("Review the OCR output manually; it is not imported into race data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
