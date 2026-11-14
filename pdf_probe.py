#!/usr/bin/env python3
"""Diagnose the spatial layout of JRA official result PDFs.

The PDF is commonly arranged in multiple columns. This tool records text blocks
with page coordinates and classifies them by column; it does not infer horse
records or race results.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import fitz


def download_pdf(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "JRA-HORSE-DATA-LAB/0.4"})
    with urlopen(request, timeout=60) as response:
        data = response.read()
    if not data.startswith(b"%PDF"):
        raise ValueError("Downloaded content is not a PDF")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def extract_layout(pdf_path: Path) -> dict:
    document = fitz.open(pdf_path)
    pages = []

    for page_number, page in enumerate(document, start=1):
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        midpoint = page_width / 2
        blocks = []

        for block_number, block in enumerate(page.get_text("dict")["blocks"]):
            if "lines" not in block:
                continue
            text_lines = []
            for line in block["lines"]:
                spans = [span.get("text", "") for span in line.get("spans", [])]
                line_text = "".join(spans).strip()
                if line_text:
                    text_lines.append(line_text)
            text = "\n".join(text_lines).strip()
            if not text:
                continue

            x0, y0, x1, y1 = [round(float(v), 2) for v in block["bbox"]]
            if x0 < midpoint < x1 and (x1 - x0) > page_width * 0.42:
                column = "full_width"
            elif (x0 + x1) / 2 < midpoint:
                column = "left"
            else:
                column = "right"

            blocks.append({
                "block_number": block_number,
                "column": column,
                "bbox": [x0, y0, x1, y1],
                "text": text,
            })

        blocks.sort(key=lambda item: (
            {"full_width": 0, "left": 1, "right": 2}[item["column"]],
            item["bbox"][1],
            item["bbox"][0],
        ))
        pages.append({
            "page_number": page_number,
            "width": page_width,
            "height": page_height,
            "column_midpoint": round(midpoint, 2),
            "block_count": len(blocks),
            "blocks": blocks,
        })

    return {
        "source_pdf": str(pdf_path),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "page_count": len(document),
        "method": "PyMuPDF dict blocks with bounding boxes; left/right column classification",
        "warning": "Layout diagnostic only. Horse-level fields and results are not yet parsed.",
        "pages": pages,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="JRA official PDF URL")
    parser.add_argument("--output", default="data/pdf_layout.json")
    parser.add_argument("--pdf", default="data/sample.pdf")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    download_pdf(args.url, pdf_path)
    result = extract_layout(pdf_path)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    total_blocks = sum(page["block_count"] for page in result["pages"])
    print(f"Pages: {result['page_count']}; positioned text blocks: {total_blocks}")
    print(f"Saved spatial layout diagnostic to {output}")


if __name__ == "__main__":
    main()
