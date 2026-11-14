"""Conservative parser for JRA official race-result PDFs.

Requires Poppler's pdftotext executable. Race-level lap/closing-section values
are stored separately from runner-level data; this module never invents an
individual horse's last-3F value from the race-level figure.
"""
from __future__ import annotations

import csv
import io
import os
import re
from datetime import date

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

TRACKS = ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉")
RUNNER_RE = re.compile(
    # OCR may separate frame/horse numbers ("2 2", "2 1 2") or join them ("811"),
    # and may substitute braces or punctuation for the printed separator.
    r"^\s*([1-8])\s*[|/\\\\{}()\[\].,:;<>_-]*\s*(\d{1,2})\s*[|/\\\\{}()\[\].,:;<>_-]?\s*(.+?)\s*[|/\\\\{}()\[\].,:;<>_-]?\s*(牡|牝|セ)\s*([0-9０-９]+)",
)
RACE_RE = re.compile(r"第\s*(\d+)\s*競走")
DATE_RE = re.compile(r"(\d{1,2})月\s*(\d{1,2})日")
FIELD_SIZE_RE = re.compile(r"[（(]\s*([0-9０-９]{1,2})\s*頭\s*[）)]")
DISTANCE_RE = re.compile(r"([0-9０-９]{1,2}[,，][0-9０-９]{3}|[0-9０-９]{3,4})\s*(?:ｍ|m|メートル|\x02)")
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
    # OCR sometimes inserts a space inside a two-digit horse number (e.g. "2 1 2").
    # Japanese horse names do not begin with digits, so safely join that prefix.
    line = re.sub(
        r"^(\s*[1-8]\s*[|/\\\\{}()\[\].,:;<>_-]*\s*)(\d)\s+(\d)(?=\s)",
        r"\1\2\3",
        line,
    )
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
    dead_heat = False
    runner_warnings: list[str] = []
    if time_match:
        # A status after the official time belongs to the result field. Do not
        # scan jockey/owner/trainer text, where the same word could be a name.
        status_match = re.search(r"(競走中止|失格|中止|取消|除外)\s*$", tail[time_match.end():])
    else:
        # JRA prints 〃 instead of repeating the time for a dead heat.
        dead_heat = bool(re.search(r"(?:^|\s)〃(?:\s|$)", tail))
        # Non-finishers have no time; accept a status only when terminal.
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
        "dead_heat": dead_heat,
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

        # OCR often places meeting/date/distance metadata on adjacent lines.
        header_window = "\n".join(block[:8])
        date_match = DATE_RE.search(header_window)
        race_date = None
        if date_match:
            month, day = map(int, date_match.groups())
            year_match = re.search(r"(20\d{2})年", header_window)
            if not year_match and source_url:
                year_match = re.search(r"/(20\d{2})/", source_url)
            if year_match:
                year = int(year_match.group(1))
                try:
                    race_date = date(year, month, day).isoformat()
                except ValueError:
                    race_date = None

        distance_match = DISTANCE_RE.search(header_window)
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

        track = _track(header_window)
        going_match = GOING_RE.search(header_window)
        going = going_match.group(1) if going_match else None
        race_name_match = re.search(r"第\s*\d+\s*競走\s*(.*?)\s*[0-9０-９]{1,2}(?:[,，][0-9０-９]{3}|[0-9０-９]{2,3})", header_window)
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
        last_finish_position = None
        last_finish_time = None
        last_finish_seconds = None
        for runner in runners:
            # The 〃 marker shares the previous finisher placing and time.
            is_tied = runner.get("dead_heat") and last_finish_position is not None
            is_placed = (runner["finish_time_seconds"] is not None or is_tied) and runner["result_status"] is None
            current_finish_position = None
            if is_tied:
                runner["finish_time"] = last_finish_time
                runner["finish_time_seconds"] = last_finish_seconds
                finish_position += 1  # Count tied runners when assigning later ordinal places.
                current_finish_position = last_finish_position
            elif is_placed:
                finish_position += 1
                current_finish_position = finish_position
                last_finish_position = current_finish_position
            if is_placed:
                last_finish_time = runner["finish_time"]
                last_finish_seconds = runner["finish_time_seconds"]
            records.append({
                **runner,
                "date": race_date,
                "track": track,
                "race_number": race_number,
                "race_name": race_name,
                "surface": surface,
                "distance": distance,
                "going": going,
                "finish": current_finish_position if is_placed else None,
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



def _expected_frame(field_size: int, horse_number: int) -> int:
    """Return the official JRA frame for a horse number and field size."""
    if field_size <= 8:
        return horse_number
    base, remainder = divmod(field_size, 8)
    frame = 1
    first_larger_frame = 8 - remainder + 1 if remainder else 9
    for current_frame in range(1, 9):
        count = base + (1 if current_frame >= first_larger_frame else 0)
        if horse_number <= count:
            return current_frame
        horse_number -= count
    return 0


def _inferred_complete_field_size(rows: list[dict[str, Any]]) -> int | None:
    """Infer field size only when contiguous horse numbers and JRA frame assignments identify it uniquely."""
    numbers = sorted(row.get("horse_number") for row in rows)
    if not numbers or numbers != list(range(1, len(numbers) + 1)):
        return None
    candidates = []
    for field_size in range(max(2, len(numbers)), 19):
        if all(
            row.get("frame") == _expected_frame(field_size, row.get("horse_number"))
            for row in rows
        ):
            candidates.append(field_size)
    return candidates[0] if len(candidates) == 1 else None


def _repair_ocr_horse_numbers(rows: list[dict[str, Any]]) -> bool:
    """Repair a spurious leading '1' only when a complete JRA field proves it."""
    field_size = len(rows)
    if not 2 <= field_size <= 18:
        return False

    repaired_numbers = []
    for row in rows:
        number = row.get("horse_number")
        frame = row.get("frame")
        candidates = [number]
        # OCR often turns a single-digit number into 11..18. The official
        # frame assignment can disambiguate it, but only in a full field.
        if isinstance(number, int) and 11 <= number <= 18:
            candidates.append(number - 10)
        valid = [
            candidate for candidate in candidates
            if isinstance(candidate, int)
            and 1 <= candidate <= field_size
            and frame == _expected_frame(field_size, candidate)
        ]
        if len(valid) != 1:
            return False
        repaired_numbers.append(valid[0])

    if sorted(repaired_numbers) != list(range(1, field_size + 1)):
        return False

    for row, number in zip(rows, repaired_numbers):
        row["horse_number"] = number
        row["declared_field_size"] = field_size
    return True


def _complete_ocr_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only OCR races with a verified declared or uniquely inferred full field."""
    races: dict[tuple, list[dict[str, Any]]] = {}
    for row in records:
        key = (row.get("source_url"), row.get("date"), row.get("race_number"))
        races.setdefault(key, []).append(row)
    accepted = []
    for rows in races.values():
        declared = rows[0].get("declared_field_size")
        if declared is not None:
            complete = len(rows) == declared
        else:
            inferred = _inferred_complete_field_size(rows)
            if inferred is None and _repair_ocr_horse_numbers(rows):
                inferred = len(rows)
            complete = inferred is not None and len(rows) == inferred
            if complete:
                declared = inferred
                for row in rows:
                    row["declared_field_size"] = inferred

        # A matching row count alone is not enough: OCR may duplicate one
        # horse number while omitting another. Require the exact official
        # 1..N numbering and matching frame assignments before accepting.
        if complete and declared is not None:
            numbers = [row.get("horse_number") for row in rows]
            complete = sorted(numbers) == list(range(1, declared + 1))
            if complete:
                complete = all(
                    row.get("frame") == _expected_frame(declared, row["horse_number"])
                    for row in rows
                )
        if complete:
            for row in rows:
                row["extraction_status"] = "ocr_fallback_basic_fields"
                accepted.append(row)
    return accepted


def _ocr_identifier_pairs(text: str) -> list[tuple[int, int] | None]:
    """Read frame/horse identifiers from the narrow, high-contrast number columns."""
    pairs: list[tuple[int, int] | None] = []
    for line in text.splitlines():
        # Keep compact forms such as 22 and 711, but never salvage digits
        # from a malformed separated line (e.g. 4|19 -> 4|1).
        compact = re.fullmatch(r"\s*([1-8])\s*([1-9]|1[0-8])\s*", line)
        if compact:
            frame, number = map(int, compact.groups())
        else:
            separated = re.search(r"(?<!\d)([1-8])\s*[|/\\\\{}()\[\].,:;<>_ -]+\s*(1[0-8]|[1-9])(?!\d)", line)
            if not separated:
                pairs.append(None)
                continue
            frame, number = map(int, separated.groups())
        if 1 <= frame <= 8 and 1 <= number <= 18:
            pairs.append((frame, number))
        else:
            pairs.append(None)
    return pairs


def _ocr_tsv_lines(tsv: str, *, scale: float = 1.0, y_offset: float = 0.0) -> list[tuple[float, str]]:
    """Return OCR text lines with vertical centers in original panel coordinates."""
    grouped: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    for item in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        if item.get("level") != "5" or not item.get("text", "").strip():
            continue
        key = (item.get("page_num", "1"), item.get("block_num", "0"),
               item.get("par_num", "0"), item.get("line_num", "0"))
        grouped.setdefault(key, []).append(item)
    lines = []
    for words in grouped.values():
        try:
            top = min(int(word["top"]) for word in words)
            bottom = max(int(word["top"]) + int(word["height"]) for word in words)
        except (KeyError, ValueError):
            continue
        text = " ".join(word["text"].strip() for word in words if word.get("text", "").strip())
        lines.append((y_offset + ((top + bottom) / 2) / scale, text))
    return sorted(lines, key=lambda item: item[0])


def _match_ocr_identifier_rows(
    runner_centers: list[float],
    identifier_rows: list[tuple[float, tuple[int, int] | None]],
) -> list[tuple[int, int]] | None:
    """Match independent OCR rows by vertical position; reject ambiguity."""
    if not runner_centers or len(runner_centers) != len(identifier_rows):
        return None
    centers = sorted(runner_centers)
    rows = sorted(identifier_rows, key=lambda item: item[0])
    gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
    gaps = [gap for gap in gaps if gap > 0]
    if len(gaps) != len(centers) - 1:
        return None
    typical_gap = sorted(gaps)[len(gaps) // 2] if gaps else 0
    tolerance = typical_gap * 0.40
    if not tolerance or any(abs(y - row[0]) > tolerance for y, row in zip(centers, rows)):
        return None
    if any(row[1] is None for row in rows):
        return None
    return [row[1] for row in rows]


def _ocr_pdf_text(pdf_path: Path, temp_dir: Path, *, max_pages: int = 10) -> str | None:
    """OCR JRA panels, using a separate high-resolution pass for frame/horse columns."""
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        return None
    prefix = temp_dir / "ocr-page"
    subprocess.run(
        ["pdftoppm", "-f", "1", "-l", str(max_pages), "-r", "180", "-png",
         str(pdf_path), str(prefix)],
        check=True,
        timeout=90,
        capture_output=True,
        text=True,
    )
    pages = sorted(
        temp_dir.glob("ocr-page-*.png"),
        key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
    )
    if not pages:
        return None
    try:
        from PIL import Image
    except ImportError:
        print("Pillow unavailable; cannot safely split JRA result columns")
        return None

    chunks = []
    for page_index, page in enumerate(pages):
        with Image.open(page) as image:
            width, height = image.size
            midpoint = width // 2
            overlap = max(2, width // 100)
            regions = (
                ("left", (0, 0, min(width, midpoint + overlap), height)),
                ("right", (max(0, midpoint - overlap), 0, width, height)),
            )
            for side_index, (side, bounds) in enumerate(regions):
                crop_path = temp_dir / f"{page.stem}-{side}.png"
                crop = image.crop(bounds)
                crop.resize((crop.width * 5 // 3, crop.height * 5 // 3),
                            Image.Resampling.LANCZOS).save(crop_path)
                result = subprocess.run(
                    ["tesseract", str(crop_path), "stdout", "-l", "jpn+eng", "--psm", "6"],
                    check=True, timeout=30, capture_output=True, text=True,
                )
                clean_lines = [
                    line for line in result.stdout.splitlines()
                    if not RACE_RE.search(line)
                ]

                # The runner table has fixed columns. OCR the narrow frame/horse
                # number strip separately at 5x; this avoids names and odds
                # confusing the two small identifier fields.
                panel_width, panel_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
                number_box = (
                    int(panel_width * 0.043), int(panel_height * 0.292),
                    int(panel_width * 0.112), int(panel_height * 0.542),
                )
                number_crop = image.crop((
                    bounds[0] + number_box[0], bounds[1] + number_box[1],
                    bounds[0] + number_box[2], bounds[1] + number_box[3],
                ))
                number_crop = number_crop.resize(
                    (number_crop.width * 5, number_crop.height * 5),
                    Image.Resampling.LANCZOS,
                )
                number_path = temp_dir / f"{page.stem}-{side}-numbers.png"
                number_crop.save(number_path)
                # Equal row counts are unsafe: independently OCR both columns
                # with TSV boxes and require close vertical alignment.
                panel_tsv = subprocess.run(
                    ["tesseract", str(crop_path), "stdout", "-l", "jpn+eng", "--psm", "6", "tsv"],
                    check=True, timeout=30, capture_output=True, text=True,
                ).stdout
                number_tsv = subprocess.run(
                    ["tesseract", str(number_path), "stdout", "-l", "eng", "--psm", "6", "tsv"],
                    check=True, timeout=30, capture_output=True, text=True,
                ).stdout
                panel_lines = _ocr_tsv_lines(panel_tsv, scale=5 / 3)
                number_lines = _ocr_tsv_lines(
                    number_tsv, scale=5, y_offset=float(number_box[1])
                )
                runner_indexes = [
                    i for i, line in enumerate(clean_lines) if _runner(line.strip())
                ]
                runner_centers = [
                    y for y, line in panel_lines if _runner(line.strip())
                ]
                # Parse each TSV line's own text so OCR text and bounding-box
                # rows can never drift apart through a positional zip.
                identifier_rows = []
                for y, line_text in number_lines:
                    pairs = _ocr_identifier_pairs(line_text)
                    pair = pairs[0] if len(pairs) == 1 else None
                    identifier_rows.append((y, pair))
                matched = _match_ocr_identifier_rows(runner_centers, identifier_rows)
                if len(runner_indexes) == len(runner_centers) and matched is not None:
                    for line_index, (frame, number) in zip(runner_indexes, matched):
                        line = clean_lines[line_index]
                        prefix_match = re.match(
                            r"^\\s*[1-8]\\s*[|/\\\\{}()\\[\\].,:;<>_-]*\\s*\\d{1,2}",
                            line,
                        )
                        if prefix_match:
                            clean_lines[line_index] = (
                                f"{frame}|{number} " + line[prefix_match.end():].lstrip()
                            )

                debug_dir = os.environ.get("JRA_OCR_DEBUG_DIR")
                if debug_dir and page == pages[0]:
                    destination = Path(debug_dir)
                    destination.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(crop_path, destination / f"{page.stem}-{side}.png")
                    shutil.copyfile(number_path, destination / f"{page.stem}-{side}-numbers.png")
                    (destination / f"{page.stem}-{side}.txt").write_text(
                        result.stdout, encoding="utf-8"
                    )
                    (destination / f"{page.stem}-{side}-numbers.txt").write_text(
                        number_result.stdout, encoding="utf-8"
                    )

                race_number = page_index * 2 + side_index + 1
                chunks.append(f"第{race_number}競走\n" + "\n".join(clean_lines))
    return "\n".join(chunks)

def extract_pdf(pdf_bytes: bytes, *, source_url: str = "") -> list[dict[str, Any]]:
    """Parse PDF text first, then use bounded Japanese OCR if its text layer is unusable."""
    with tempfile.TemporaryDirectory(prefix="jra-pdf-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        pdf_path = temp_dir / "source.pdf"
        txt_path = temp_dir / "source.txt"
        pdf_path.write_bytes(pdf_bytes)
        subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), str(txt_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        text = txt_path.read_text(encoding="utf-8", errors="replace")
        records = parse_race_text(text, source_url=source_url)
        if records:
            print(f"PDF text layer: {len(records)} rows from {source_url}")
            return records

        text_lines = text.splitlines()
        header_count = sum(1 for line in text_lines if RACE_RE.search(line))
        runner_line_count = sum(1 for line in text_lines if _runner(line.strip()))
        print(
            f"PDF text layer yielded no runners: {source_url}; "
            f"lines={len(text_lines)}, race_headers={header_count}, "
            f"runner_candidates={runner_line_count}; trying OCR"
        )
        text_sample = [line.strip()[:180] for line in text_lines if line.strip()][:12]
        print(f"PDF text sample: {text_sample!r}")
        ocr_text = _ocr_pdf_text(pdf_path, temp_dir)
        if not ocr_text:
            print(f"OCR unavailable or empty: {source_url}")
            return []
        ocr_lines = [line.strip()[:180] for line in ocr_text.splitlines() if line.strip()]
        ocr_records = parse_race_text(ocr_text, source_url=source_url)
        accepted = _complete_ocr_records(ocr_records)
        race_groups: dict[tuple, list[dict[str, Any]]] = {}
        for row in ocr_records:
            key = (row.get("date"), row.get("race_number"))
            race_groups.setdefault(key, []).append(row)
        print(
            "OCR race validation: "
            + repr([
                {
                    "date": key[0],
                    "race_number": key[1],
                    "parsed": len(rows),
                    "declared_field_size": rows[0].get("declared_field_size"),
                    "horse_numbers": sorted(row.get("horse_number") for row in rows),
                }
                for key, rows in race_groups.items()
            ])
        )
        print(
            f"OCR result: {source_url}; chars={len(ocr_text)}, "
            f"race_headers={sum(1 for line in ocr_lines if RACE_RE.search(line))}, "
            f"runner_candidates={sum(1 for line in ocr_lines if _runner(line))}, "
            f"parsed_rows={len(ocr_records)}, accepted_rows={len(accepted)}"
        )
        print(f"OCR text sample: {ocr_lines[:20]!r}")
        return accepted


def download_and_extract(url: str, *, timeout: int = 45) -> list[dict[str, Any]]:
    request = Request(url, headers={"User-Agent": "JRA-HORSE-DATA-LAB/0.3"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
    if not payload.startswith(b"%PDF"):
        raise ValueError(f"URL did not return a PDF: {url}")
    return extract_pdf(payload, source_url=url)
