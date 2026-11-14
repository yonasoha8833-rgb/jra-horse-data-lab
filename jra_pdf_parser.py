"""Conservative parser for JRA official race-result PDFs.

Requires Poppler's pdftotext executable. Race-level lap/closing-section values
are stored separately from runner-level data; this module never invents an
individual horse's last-3F value from the race-level figure.
"""
from __future__ import annotations

import re
from datetime import date
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

TRACKS = ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉")
RUNNER_RE = re.compile(
    # JRA extraction may separate frame/horse numbers ("2 2") or join them ("811").
    r"^\s*(\d)\s*(\d{1,2})\s+(.+?)\s+(牡|牝|セ)\s*([0-9０-９]+)",
)
RACE_RE = re.compile(r"第\s*(\d+)\s*競走")
DATE_RE = re.compile(r"(\d{1,2})月\s*(\d{1,2})日")
FIELD_SIZE_RE = re.compile(r"[（(]\s*([0-9０-９]{1,2})\s*頭\s*[）)]")
DISTANCE_RE = re.compile(r"([0-9０-９]{1,2}[,，][0-9０-９]{3}|[0-9０-９]{3,4})\s*(?:ｍ|m|メートル)")
RESULT_STATUS_RE = re.compile(r"(競走中止|失格|中止|取消|除外)")
GOING_RE = re.compile(r"(?:小雨|小雪|晴|曇|雨|雪)\s*(良|稍重|重|不良)")
LAST3F_RE = re.compile(r"上り\s*4F\s*([0-9.．]+)\s*[―ー-]\s*3F\s*([0-9.．]+)")
LAP_RE = re.compile(r"ハロンタイム\s*([0-9.．―ー-]+)")


def _ascii_digits(value: str) -> str:
    return value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def _seconds(value: str | None) -> float | None:
    if not value:
        return None
    value = _ascii_digits(value).replace("．", ".")
    try:
        return float(value)
    except ValueError:
        return None


def _distance(value: str | None) -> int | None:
    if not value:
        return None
    digits = _ascii_digits(value).replace(",", "").replace("，", "")
    try:
        distance = int(digits)
    except ValueError:
        return None
    # JRA race distances fall within this broad physical range; reject
    # malformed extraction values rather than storing implausible distances.
    return distance if 1000 <= distance <= 5000 else None


def _track(text: str) -> str | None:
    for track in TRACKS:
        if track in text:
            return track
    return None


def _runner(line: str) -> dict[str, Any] | None:
    match = RUNNER_RE.match(line)
    if not match:
        return None
    frame, number, name, sex, age = match.groups()
    frame_number = int(_ascii_digits(frame))
    horse_number = int(_ascii_digits(number))
    horse_age = int(_ascii_digits(age))
    # Reject impossible identifiers/ages from malformed text extraction.
    # The broad age ceiling avoids excluding unusual historical records.
    if not (1 <= frame_number <= 8 and 1 <= horse_number <= 18 and 2 <= horse_age <= 20):
        return None
    tail = line[match.end():]
    time_match = re.search(r"(?<![0-9])([0-9０-９]{1,2})[：:]([0-9０-９]{2})[.．]([0-9０-９])", tail)
    finish_time = None
    finish_time_seconds = None
    runner_warnings: list[str] = []
    if time_match:
        # A status after the official time belongs to the result field. Do not
        # scan jockey/owner/trainer text, where the same word could be a name.
        status_match = re.search(r"(競走中止|失格|中止|取消|除外)\s*$", tail[time_match.end():])
    else:
        # Non-finishers have no time; accept a status only when it is the
        # terminal token in the runner row, not an incidental name fragment.
        status_match = re.search(r"(競走中止|失格|中止|取消|除外)\s*$", tail)
    result_status = status_match.group(1) if status_match else None
    if time_match:
        minutes, seconds, tenth = map(int, (_ascii_digits(v) for v in time_match.groups()))
        if seconds < 60:
            finish_time = f"{minutes}:{seconds:02d}.{tenth}"
            finish_time_seconds = minutes * 60 + seconds + tenth / 10
        else:
            runner_warnings.append("invalid_finish_time_seconds")
    name = re.sub(r"\s+", "", name).strip()
    if not name or name in {"枠", "番", "馬番"}:
        return None
    return {
        "frame": frame_number,
        "horse_number": horse_number,
        "horse_name": name,
        "sex": sex,
        "age": horse_age,
        "finish_time": finish_time,
        "finish_time_seconds": finish_time_seconds,
        "result_status": result_status,
        "runner_validation_warnings": runner_warnings,
    }


def parse_race_text(text: str, *, source_url: str = "") -> list[dict[str, Any]]:
    """Parse race sections from pdftotext -layout output."""
    lines = [line.rstrip() for line in text.replace("\x00", "").splitlines()]
    starts = [i for i, line in enumerate(lines) if RACE_RE.search(line)]
    records: list[dict[str, Any]] = []

    for race_index, start in enumerate(starts):
        end = starts[race_index + 1] if race_index + 1 < len(starts) else len(lines)
        block = lines[start:end]
        header = block[0]
        race_match = RACE_RE.search(header)
        if not race_match:
            continue
        race_number = int(race_match.group(1))
        if not 1 <= race_number <= 12:
            continue

        date_match = DATE_RE.search(header)
        race_date = None
        if date_match:
            month, day = map(int, date_match.groups())
            year_match = re.search(r"(20\d{2})年", header)
            if year_match:
                year = int(year_match.group(1))
                try:
                    race_date = date(year, month, day).isoformat()
                except ValueError:
                    # Invalid calendar dates are left missing, never normalized.
                    race_date = None

        distance_match = DISTANCE_RE.search(header)
        distance = _distance(distance_match.group(1)) if distance_match else None
        surface = None
        for line in block[:8]:
            if "芝" in line:
                surface = "芝"
                break
            if "ダート" in line:
                surface = "ダート"
                break
            if "障害" in line:
                surface = "障害"
                break

        track = _track(header)
        going_match = GOING_RE.search(header)
        going = going_match.group(1) if going_match else None
        race_name_match = re.search(r"第\s*\d+\s*競走\s*(.*?)\s*[0-9０-９]{1,2}(?:[,，][0-9０-９]{3}|[0-9０-９]{2,3})", header)
        race_name = race_name_match.group(1).strip() if race_name_match else None

        runners = []
        for line in block:
            runner = _runner(line.strip())
            if runner:
                runners.append(runner)

        # Ignore blocks with no reliably recognized runner rows.
        if not runners:
            continue

        # Keep extracted rows, but attach warnings when race-level consistency
        # checks fail so questionable data can be reviewed rather than hidden.
        validation_warnings: list[str] = []
        horse_numbers = [runner["horse_number"] for runner in runners]
        duplicates = sorted({number for number in horse_numbers if horse_numbers.count(number) > 1})
        if duplicates:
            validation_warnings.append(
                "duplicate_horse_number:" + ",".join(str(number) for number in duplicates)
            )
        declared_match = FIELD_SIZE_RE.search("\\n".join(block))
        declared_field_size = (
            int(_ascii_digits(declared_match.group(1))) if declared_match else None
        )
        if declared_field_size is not None and declared_field_size != len(runners):
            validation_warnings.append(
                f"declared_field_size_mismatch:declared={declared_field_size},parsed={len(runners)}"
            )

        last3f = None
        last4f = None
        laps = None
        for line in block:
            match = LAST3F_RE.search(line)
            if match:
                last4f, last3f = _seconds(match.group(1)), _seconds(match.group(2))
            lap_match = LAP_RE.search(line)
            if lap_match:
                laps = [
                    _seconds(v) for v in re.findall(r"[0-9０-９]+[.．][0-9０-９]+", lap_match.group(1))
                ]

        # Finish order follows the official result table's runner row order.
        # Ties are not guessed from margins; downstream validation can refine it.
        finish_position = 0
        for runner in runners:
            # A runner without a recorded finish time may be a non-finisher,
            # so never assign it a placing or shift later finishers upward.
            is_placed = runner["finish_time_seconds"] is not None and runner["result_status"] is None
            if is_placed:
                finish_position += 1
            records.append({
                **runner,
                "date": race_date,
                "track": track,
                "race_number": race_number,
                "race_name": race_name,
                "surface": surface,
                "distance": distance,
                "going": going,
                "finish": finish_position if is_placed else None,
                "field_size": len(runners),
                "declared_field_size": declared_field_size,
                "race_validation_warnings": validation_warnings,
                "race_last3f": last3f,
                "race_last4f": last4f,
                "race_lap_times": laps,
                "individual_last3f": None,
                "source_url": source_url,
                "extraction_status": "parsed_basic_fields",
            })

    return records


def extract_pdf(pdf_bytes: bytes, *, source_url: str = "") -> list[dict[str, Any]]:
    """Convert PDF bytes to layout-preserving text with pdftotext, then parse."""
    with tempfile.TemporaryDirectory(prefix="jra-pdf-") as temp_dir:
        pdf_path = Path(temp_dir) / "source.pdf"
        txt_path = Path(temp_dir) / "source.txt"
        pdf_path.write_bytes(pdf_bytes)
        subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), str(txt_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return parse_race_text(txt_path.read_text(encoding="utf-8", errors="replace"),
                               source_url=source_url)


def download_and_extract(url: str, *, timeout: int = 45) -> list[dict[str, Any]]:
    request = Request(url, headers={"User-Agent": "JRA-HORSE-DATA-LAB/0.3"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
    if not payload.startswith(b"%PDF"):
        raise ValueError(f"URL did not return a PDF: {url}")
    return extract_pdf(payload, source_url=url)
