"""Parser for JRA graded-race replay PDFs containing runner-level closing 3F.

These PDFs are a separate source from the annual full-results PDFs. The
individual closing time is read only from each runner's own result block.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

TRACKS = ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉")
CIRCLED_NUMBERS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
ROW_START_RE = re.compile(rf"^\s*(\d{{1,2}})\s*([{CIRCLED_NUMBERS}])(?:\s+|$)")
RUNNER_HEADER_RE = re.compile(
    rf"^\s*(\d{{1,2}})\s*([{CIRCLED_NUMBERS}])\s+(.+?)\s+(牡|牝|セ|せん)\s*([0-9０-９]+)",
    re.S,
)
INDIVIDUAL_LAST3F_RE = re.compile(r"(?<![0-9.])([0-9０-９]{2,3}[.．][0-9０-９])\s+[0-9０-９]{3}\s*[（(]")
DATE_RE = re.compile(r"(20\d{2})[.／/年](\d{1,2})[.／/月](\d{1,2})")
DISTANCE_RE = re.compile(r"(芝|ダート|ダ|障)\s*([0-9０-９,，]{3,5})\s*(?:㍍|ｍ|m|メートル)")
GOING_RE = re.compile(r"[・\s](良|稍重|重|不良)")
CORNER_RE = re.compile(r"(?<![0-9])\d{1,2}(?:\s*[-－]\s*\d{1,2}){2,3}(?![0-9])")


def _ascii_digits(value: str) -> str:
    return value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def _number(value: str) -> int:
    return int(_ascii_digits(value).replace(",", "").replace("，", ""))


def _metadata(text: str, supplied: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(supplied or {})
    if not result.get("date"):
        match = DATE_RE.search(text)
        if match:
            year, month, day = map(int, match.groups())
            try:
                from datetime import date
                result["date"] = date(year, month, day).isoformat()
            except ValueError:
                pass
    if not result.get("track"):
        for track in TRACKS:
            if track in text:
                result["track"] = track
                break
    if not result.get("surface") or not result.get("distance"):
        match = DISTANCE_RE.search(text)
        if match:
            surface, distance = match.groups()
            if not result.get("surface"):
                result["surface"] = {"ダ": "ダート", "障": "障害"}.get(surface, surface)
            if not result.get("distance"):
                result["distance"] = _number(distance)
    if not result.get("going"):
        match = GOING_RE.search(text)
        if match:
            result["going"] = match.group(1)
    return result


def parse_replay_text(
    text: str,
    *,
    source_url: str = "",
    race_metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Parse runner rows from pdftotext -layout output of a JRA replay PDF.

    The replay PDF is generally for a graded race; callers should supply
    race_number and race_name from the official listing when available.
    """
    lines = [line.rstrip() for line in text.replace("\x00", "").splitlines()]
    starts = [i for i, line in enumerate(lines) if ROW_START_RE.match(line)]
    if not starts:
        return []
    meta = _metadata(text, race_metadata)
    records: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        header = RUNNER_HEADER_RE.search(block)
        if not header:
            continue
        placing, circled, horse_name, sex, age = header.groups()
        last3f_match = INDIVIDUAL_LAST3F_RE.search(block)
        corner_match = CORNER_RE.search(block)
        row = {
            **meta,
            "finish": int(placing),
            "horse_number": CIRCLED_NUMBERS.index(circled) + 1,
            "horse_name": re.sub(r"\s+", "", horse_name).strip(),
            "sex": sex,
            "age": _number(age),
            "individual_last3f": (
                float(_ascii_digits(last3f_match.group(1)).replace("．", "."))
                if last3f_match else None
            ),
            "corner_positions": (
                re.sub(r"\s+", "", corner_match.group(0)).replace("－", "-")
                if corner_match else None
            ),
            "source_url": source_url,
            "source_type": "jra_graded_race_replay_pdf",
        }
        if row["horse_name"]:
            records.append(row)
    return records


def extract_replay_pdf(
    pdf_bytes: bytes,
    *,
    source_url: str = "",
    race_metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Convert a JRA replay PDF to layout-preserving text and parse runner rows."""
    if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("Downloaded content is not a PDF (missing %PDF- signature)")
    if len(pdf_bytes) < 100:
        raise ValueError("Downloaded PDF is unexpectedly small")
    with tempfile.TemporaryDirectory(prefix="jra-replay-") as temp_dir:
        pdf_path = Path(temp_dir) / "replay.pdf"
        txt_path = Path(temp_dir) / "replay.txt"
        pdf_path.write_bytes(pdf_bytes)
        subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), str(txt_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return parse_replay_text(
            txt_path.read_text(encoding="utf-8", errors="replace"),
            source_url=source_url,
            race_metadata=race_metadata,
        )
