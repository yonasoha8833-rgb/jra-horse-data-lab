#!/usr/bin/env python3
"""Inspect JRA official result PDFs and split their text into race sections.

This is a diagnostic first step: it preserves source text rather than guessing
horse-level fields from a complex PDF layout.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import fitz


def download_pdf(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "JRA-HORSE-DATA-LAB/0.3"})
    with urlopen(request, timeout=60) as response:
        data = response.read()
    if not data.startswith(b"%PDF"):
        raise ValueError("Downloaded content is not a PDF")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def extract_sections(pdf_path: Path) -> dict:
    document = fitz.open(pdf_path)
    pages = [page.get_text("text", sort=True) for page in document]
    full_text = "\n".join(pages)
    matches = list(re.finditer(r"第\s*(\d+)\s*競走", full_text))
    races = []

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(full_text)
        section = full_text[start:end].strip()
        lines = [line.strip() for line in section.splitlines() if line.strip()]
        lap_lines = []
        for line_index, line in enumerate(lines):
            if "ハロンタイム" in line:
                lap_lines.append(" ".join(lines[line_index:line_index + 2]))
        races.append({
            "race_number": int(match.group(1)),
            "text_line_count": len(lines),
            "lap_time_source_lines": lap_lines,
            "source_text": section,
        })

    return {
        "source_pdf": str(pdf_path),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "page_count": len(document),
        "race_count": len(races),
        "races": races,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="JRA official PDF URL")
    parser.add_argument("--output", default="data/pdf_probe.json")
    parser.add_argument("--pdf", default="data/sample.pdf")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    download_pdf(args.url, pdf_path)
    result = extract_sections(pdf_path)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Pages: {result['page_count']}; race sections: {result['race_count']}")
    print(f"Saved diagnostic extraction to {output}")


if __name__ == "__main__":
    main()
