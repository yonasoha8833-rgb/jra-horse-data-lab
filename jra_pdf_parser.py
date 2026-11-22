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
from urllib.parse import urlparse
from urllib.request import Request, urlopen

TRACKS = ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉")
RUNNER_RE = re.compile(
    # OCR may separate frame/horse numbers ("2 2", "2 1 2") or join them ("811"),
    # and may substitute braces or punctuation for the printed separator.
    r"^\s*([1-8])\s*[|/\\\\{}()\[\].,:;<>_-]*\s*(\d{1,2})\s*[|/\\\\{}()\[\].,:;<>_-]?\s*(.+?)\s*[|/\\\\{}()\[\].,:;<>_-]?\s*(牡|牝|騸|セン|せん|セ)\s*([0-9０-９]+)",
)
RACE_RE = re.compile(r"第\s*(\d+)\s*競走")

def _strip_race_heading(line: str) -> str:
    """Remove only the synthetic race heading, preserving metadata on the same OCR line."""
    return RACE_RE.sub("", line).strip()
DATE_RE = re.compile(r"(\d{1,2})月\s*(\d{1,2})日")
FIELD_SIZE_RE = re.compile(r"[（(]\s*([0-9０-９]{1,2})\s*頭\s*[）)]")
HEADCOUNT_VERIFIED_RE = re.compile(r"JRA_OCR_HEADCOUNT_VERIFIED=(\d{1,2})")
DISTANCE_RE = re.compile(r"([0-9０-９]{1,2}[,，][0-9０-９]{3}|[0-9０-９]{3,4})\s*(?:ｍ|m|メートル|\x02)")
RESULT_STATUS_RE = re.compile(r"(競走中止|失格|中止|取消|除外)")
GOING_RE = re.compile(r"(?:小雨|小雪|晴|曇|雨|雪)\s*(良|稍重|重|不良)")
LAST3F_RE = re.compile(r"上り\s*4F\s*([0-9.．]+)\s*[―ー-]\s*3F\s*([0-9.．]+)")
LAP_RE = re.compile(r"ハロンタイム\s*([0-9.．―ー-]+)")


def _ascii_digits(value: str) -> str:
    return value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def _ocr_runner_row_bounds(
    center: float, previous: float, following: float, scale: float, image_height: int
) -> tuple[int, int]:
    """Crop the central 80% of a runner's vertical slot, away from adjacent rows."""
    before_gap = max(1.0, center - previous)
    after_gap = max(1.0, following - center)
    half_height = min(before_gap, after_gap) * 0.40
    top = max(0, int((center - half_height) * scale))
    bottom = min(image_height, int((center + half_height) * scale))
    return top, bottom

def _ocr_runner_identity_bounds(panel_width: int) -> tuple[int, int]:
    """Crop only the horse-name and sex/age columns, excluding unrelated table fields."""
    return max(0, int(panel_width * 0.085)), min(panel_width, int(panel_width * 0.335))


def _last_stable_identifier_slot_center(centers) -> float | None:
    """Find the final table-row center from stable row spacing, ignoring a detached footer OCR slot."""
    ordered = sorted(float(center) for center in centers)
    if len(ordered) < 4:
        return None
    gaps = [later - earlier for earlier, later in zip(ordered, ordered[1:])]
    positive = [gap for gap in gaps if gap > 0]
    if len(positive) < 3:
        return None
    typical_gap = sorted(positive)[len(positive) // 2]
    if typical_gap <= 0:
        return None
    # If the final detected slot is detached from the regular runner rows,
    # anchor the footer scan to the preceding stable row, not the stray slot.
    if len(gaps) >= 2 and gaps[-1] > typical_gap * 1.60:
        stable_gaps = gaps[:-1]
        if not stable_gaps or any(
            gap < typical_gap * 0.60 or gap > typical_gap * 1.40
            for gap in stable_gaps
        ):
            return None
        return ordered[-2]
    if any(gap < typical_gap * 0.60 or gap > typical_gap * 1.40 for gap in gaps):
        return None
    return ordered[-1]


def _last_valid_ocr_runner_center(centers, identifier_rows, row_texts) -> float | None:
    """Locate the last row that independently parses as a runner, ignoring stray OCR slots."""
    for center, identifier_row, row_text in reversed(list(zip(centers, identifier_rows, row_texts))):
        identifiers = identifier_row[1]
        if identifiers is None:
            continue
        frame_number, horse_number = identifiers
        if _runner(f"{frame_number}|{horse_number} {row_text}") is not None:
            return center
    return None


def _ocr_declared_field_size(text: str) -> int | None:
    """Return a headcount only when OCR reads one unambiguous official '(N頭)' label."""
    matches = {
        int(_ascii_digits(match.group(1)))
        for match in FIELD_SIZE_RE.finditer(text)
    }
    if len(matches) != 1:
        return None
    value = next(iter(matches))
    return value if 1 <= value <= 18 else None


def _consensus_declared_field_size(readings: list[str]) -> int | None:
    """Accept a headcount only when two independent OCR layouts read the same exact label."""
    parsed = [_ocr_declared_field_size(reading) for reading in readings]
    valid = [value for value in parsed if value is not None]
    if len(valid) < 2 or len(set(valid)) != 1:
        return None
    return valid[0]


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



def _strict_ocr_finish_time(text: str) -> str | None:
    """Return one unambiguous, plausible finish-time token from an isolated OCR cell."""
    matches = re.findall(
        r"(?<![0-9A-Za-z])([0-9０-９]{1,2})\s*[:：]\s*([0-9０-９]{2})\s*[.．]\s*([0-9０-９])(?![0-9A-Za-z])",
        text,
    )
    values = set()
    for minute_text, second_text, tenth_text in matches:
        minutes, seconds, tenth = (
            int(_ascii_digits(value))
            for value in (minute_text, second_text, tenth_text)
        )
        if 0 <= minutes <= 15 and 0 <= seconds < 60:
            values.add(f"{minutes}:{seconds:02d}.{tenth}")
    return next(iter(values)) if len(values) == 1 else None


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
    time_match = re.search(r"(?<![0-9])([0-9０-９]{1,2})[：:]([0-9０-９]{2})[.．]\s*([0-9０-９])", tail)
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
        minute_text, seconds_text, tenth_text = (
            _ascii_digits(value) for value in time_match.groups()
        )
        # In compact JRA PDF text, the body-weight change and finish time can
        # touch (for example, "484±01:39.0" or "452＋21:39.2").
        # When the time token begins immediately after a weight sign, its first
        # digit belongs to the weight change; the final digit is the minute.
        if (
            len(minute_text) == 2
            and time_match.start() > 0
            and tail[time_match.start() - 1] in "±＋+-"
        ):
            minute_text = minute_text[-1]
        minutes, seconds, tenth = int(minute_text), int(seconds_text), int(tenth_text)
        if seconds >= 60:
            runner_warnings.append("invalid_finish_time_seconds")
        elif minutes > 15:
            # JRA race finish times are far below this conservative ceiling.
            # Larger OCR matches usually come from nearby lap/odds text and
            # must not be accepted as a runner's official finish time.
            runner_warnings.append("invalid_finish_time_minutes")
        else:
            finish_time = f"{minutes}:{seconds:02d}.{tenth}"
            finish_time_seconds = minutes * 60 + seconds + tenth / 10
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

        # OCR may place date, track, surface and distance several lines after
        # the race heading. Read the complete pre-runner header, with a hard
        # bound, rather than assuming all metadata is within the first 8 lines.
        header_lines = []
        for line in block[:30]:
            if _runner(line.strip()):
                break
            header_lines.append(line)
        header_window = "\n".join(header_lines)
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
        for line in header_lines:
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
        verified_headcount_match = HEADCOUNT_VERIFIED_RE.search("\\n".join(block))
        headcount_verified = bool(
            verified_headcount_match
            and declared_field_size is not None
            and int(verified_headcount_match.group(1)) == declared_field_size
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
                "headcount_verified": headcount_verified,
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
    # This helper exists only to correct a demonstrated OCR prefix error.
    # Do not use an unchanged short fragment to declare its own row count complete.
    if all(number == row.get("horse_number") for row, number in zip(rows, repaired_numbers)):
        return False

    for row, number in zip(rows, repaired_numbers):
        row["horse_number"] = number
        row["declared_field_size"] = field_size
    return True


def _complete_ocr_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only complete OCR fields with verified date and track identity."""
    races: dict[tuple, list[dict[str, Any]]] = {}
    for row in records:
        key = (row.get("source_url"), row.get("date"), row.get("race_number"))
        races.setdefault(key, []).append(row)
    accepted = []
    for rows in races.values():
        # Horse rows without a verified race date and track cannot be safely
        # associated with a historical race or used for condition analysis.
        if not rows[0].get("date") or not rows[0].get("track"):
            continue
        # Legacy OCR rows may predate finish-time validation. Reject the whole
        # race when any recognized runner carries an impossible time: keeping
        # the other placings could silently shift the official finish order.
        invalid_time = any(
            (
                isinstance(row.get("finish_time_seconds"), (int, float))
                and row["finish_time_seconds"] > 15 * 60
            )
            or (
                isinstance(row.get("runner_validation_warnings"), list)
                and any(
                    warning in {"invalid_finish_time_seconds", "invalid_finish_time_minutes"}
                    for warning in row["runner_validation_warnings"]
                )
            )
            for row in rows
        )
        if invalid_time:
            continue
        # A recognized runner must have either a valid official finish time
        # (including a resolved dead heat) or an explicit terminal result
        # status. Do not accept a complete-looking field when OCR simply lost
        # a runner's result cell; that can conceal a shifted/incomplete table.
        missing_result = any(
            "finish_time_seconds" in row
            and row.get("finish_time_seconds") is None
            and not row.get("result_status")
            for row in rows
        )
        if missing_result:
            continue
        declared = (
            rows[0].get("declared_field_size")
            if rows[0].get("headcount_verified") is True
            else None
        )
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
        # horse number while omitting another. If all numbers form 1..N, the
        # official frame allocation can be deterministically checked/repaired.
        # When a verified JRA headcount is present, however, scratches can
        # leave gaps in horse numbers. In that case accept only a complete set
        # of unique, in-range horse numbers and independently read valid frame
        # numbers; never recompute frames from the reduced starter count.
        if complete and declared is not None:
            numbers = [row.get("horse_number") for row in rows]
            exact_sequence = sorted(numbers) == list(range(1, declared + 1))
            if exact_sequence:
                for row in rows:
                    row["frame"] = _expected_frame(declared, row["horse_number"])
            else:
                valid_numbers = all(
                    isinstance(number, int) and not isinstance(number, bool)
                    and 1 <= number <= 18
                    for number in numbers
                )
                unique_numbers = len(set(numbers)) == len(numbers)
                valid_frames = all(
                    isinstance(row.get("frame"), int)
                    and not isinstance(row.get("frame"), bool)
                    and 1 <= row["frame"] <= 8
                    for row in rows
                )
                complete = (
                    rows[0].get("headcount_verified") is True
                    and valid_numbers and unique_numbers and valid_frames
                )
        if complete:
            for row in rows:
                row["extraction_status"] = "ocr_fallback_basic_fields"
                accepted.append(row)
    return accepted


def _deduplicate_identifier_slots(rows: list[tuple[float, int | None]], *, tolerance: float = 2.0) -> list[tuple[float, int | None]]:
    """Collapse duplicate detections of the same printed digit at nearly identical y positions.

    Conflicting values are deliberately preserved so downstream alignment rejects
    ambiguous OCR rather than choosing one value.
    """
    ordered = sorted(rows, key=lambda item: item[0])
    result: list[tuple[float, int | None]] = []
    for center, value in ordered:
        if result:
            previous_center, previous_value = result[-1]
            if (
                abs(center - previous_center) <= tolerance
                and value == previous_value
            ):
                # Keep the midpoint to avoid biasing later row alignment.
                result[-1] = ((previous_center + center) / 2, value)
                continue
        result.append((center, value))
    return result


def _trim_trailing_identifier_outliers(
    rows: list[tuple[float, int | None]], *, gap_multiplier: float = 1.8,
) -> list[tuple[float, int | None]]:
    """Drop only isolated trailing OCR slots; later race validation must still prove a full field.

    Footer/payout text can be detected as a final identifier row far below the
    runner table. A large final vertical gap marks that trailing slot as an
    outlier. This never creates or changes an identifier, and any trimmed real
    runner causes the official field-size/count validation to reject the race.
    """
    ordered = sorted(rows, key=lambda item: item[0])
    while len(ordered) >= 3:
        gaps = [
            later[0] - earlier[0]
            for earlier, later in zip(ordered, ordered[1:])
            if later[0] > earlier[0]
        ]
        if len(gaps) != len(ordered) - 1:
            break
        typical_gap = sorted(gaps)[len(gaps) // 2]
        if typical_gap <= 0 or gaps[-1] <= typical_gap * gap_multiplier:
            break
        ordered.pop()
    return ordered


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


def _ocr_single_frame_number(text: str) -> int | None:
    """Accept exactly one frame digit from a row-local OCR pass."""
    token = text.strip()
    return int(token) if re.fullmatch(r"[1-8]", token) else None


def _ocr_single_horse_number(text: str) -> int | None:
    """Accept exactly one digit-only horse number from a row-local OCR pass."""
    token = text.strip()
    if not re.fullmatch(r"(?:[1-9]|1[0-8])", token):
        return None
    return int(token)

def _verified_horse_retry(text: str, field_size: int, frame: int, reserved: set[int]) -> int | None:
    """Accept a fresh horse-number read only when it agrees with the printed frame."""
    value = _ocr_single_horse_number(text)
    if value is None or not 1 <= value <= field_size:
        return None
    if _expected_frame(field_size, value) != frame or value in reserved:
        return None
    return value


def _consensus_horse_number(readings: list[str]) -> int | None:
    """Accept a horse number only when two independent OCR segmentation modes agree."""
    parsed = [_ocr_single_horse_number(value) for value in readings]
    valid = [value for value in parsed if value is not None]
    if len(valid) < 2 or len(set(valid)) != 1:
        return None
    return valid[0]


def _select_consensus_horse_number(
    aligned_values: list[int], row_readings: list[str]
) -> int | None:
    """Require row-local OCR consensus and reject conflicts with the column pass."""
    consensus = _consensus_horse_number(row_readings)
    if consensus is None or len(aligned_values) > 1:
        return None
    if aligned_values and aligned_values[0] != consensus:
        return None
    return consensus


def _verified_horse_retry_consensus(
    readings: list[str], field_size: int, frame: int, reserved: set[int]
) -> int | None:
    """Accept only a two-read OCR consensus that also matches the printed frame."""
    value = _consensus_horse_number(readings)
    if value is None:
        return None
    return _verified_horse_retry(str(value), field_size, frame, reserved)


def _ocr_identifier_column(tsv: str, *, scale: float, y_offset: float, allowed: str) -> list[tuple[float, int]]:
    """Read one isolated identifier column without merging adjacent digits."""
    values = []
    for y, text in _ocr_tsv_lines(tsv, scale=scale, y_offset=y_offset):
        # Do not strip OCR punctuation: e.g. "1]" must not silently become
        # horse number 1 when the printed value may be 11 or 12.
        token = _ascii_digits(text.strip())
        if re.fullmatch(allowed, token):
            values.append((y, int(token)))
    return values


def _pair_identifier_columns(
    frame_rows: list[tuple[float, int]],
    horse_rows: list[tuple[float, int]],
) -> list[tuple[float, tuple[int, int]]]:
    """Pair independently recognized frame and horse columns by row position."""
    if not frame_rows or not horse_rows:
        return []
    frame_gaps = [b[0] - a[0] for a, b in zip(frame_rows, frame_rows[1:]) if b[0] > a[0]]
    horse_gaps = [b[0] - a[0] for a, b in zip(horse_rows, horse_rows[1:]) if b[0] > a[0]]
    if not frame_gaps or not horse_gaps:
        return []
    frame_pitch = sorted(frame_gaps)[len(frame_gaps) // 2]
    horse_pitch = sorted(horse_gaps)[len(horse_gaps) // 2]
    # Use the tighter observed row pitch as the alignment tolerance. When one
    # OCR column drops a row, preserve the vertical sequence and skip only the
    # unmatched detection; never shift all subsequent frame/horse identities.
    tolerance = min(frame_pitch, horse_pitch) * 0.40
    paired = []
    used = set()
    for frame_y, frame in frame_rows:
        candidates = [
            i for i, (horse_y, _) in enumerate(horse_rows)
            if i not in used and abs(frame_y - horse_y) <= tolerance
        ]
        if len(candidates) > 1:
            return []
        if not candidates:
            continue
        index = candidates[0]
        used.add(index)
        horse_y, horse = horse_rows[index]
        paired.append(((frame_y + horse_y) / 2, (frame, horse)))
    # A sparse accidental match is not evidence of a reliable column alignment.
    if len(paired) < 3 or len(paired) < 0.75 * min(len(frame_rows), len(horse_rows)):
        return []
    return paired


def _remove_horizontal_identifier_rules(image):
    """Remove full-width table rules without drawing through identifier glyphs."""
    from PIL import ImageDraw, ImageOps

    gray = ImageOps.grayscale(image)
    rule_rows = []
    for y in range(gray.height):
        dark_pixels = sum(
            1 for value in gray.crop((0, y, gray.width, y + 1)).getdata()
            if value < 120
        )
        if dark_pixels >= int(gray.width * 0.70):
            rule_rows.append(y)

    # Clear a narrow horizontal band on each detected rule row. Drawing one
    # diagonal line from y-1 to y+1 leaves most of the printed rule intact and
    # can cut through a nearby digit; erase each row across the full strip.
    if rule_rows:
        draw = ImageDraw.Draw(image)
        rows_to_clear = {
            row
            for y in rule_rows
            for row in range(max(0, y - 1), min(image.height, y + 2))
        }
        for y in rows_to_clear:
            draw.line((0, y, image.width - 1, y), fill="white", width=1)
    return image


def _identifier_column_inner_bounds(width: int, *, column: str) -> tuple[int, int]:
    """Exclude vertical table rules while keeping the printed identifier glyph."""
    left_ratio = 0.235 if column == "frame" else 0.15
    left = max(0, min(width - 1, int(width * left_ratio)))
    right = max(left + 1, min(width, int(width * (0.82 if column == "frame" else 0.92))))
    return left, right

def _identifier_column_retry_bounds(width: int, *, column: str) -> tuple[int, int]:
    """Keep the full isolated horse-number strip for row retries; narrow crops clip glyphs."""
    if column == "horse":
        return 0, width
    return _identifier_column_inner_bounds(width, column=column)


def _ocr_identifier_column_slots(
    tsv: str, *, scale: float, y_offset: float, allowed: str,
) -> list[tuple[float, int | None]]:
    """Preserve unreadable OCR rows as positional slots instead of dropping them."""
    values: list[tuple[float, int | None]] = []
    for y, text in _ocr_tsv_lines(tsv, scale=scale, y_offset=y_offset):
        # Preserve the whole OCR token so mixed text such as "1]" remains
        # unreadable and can be retried with the row-local digit whitelist.
        token = _ascii_digits(text.strip())
        value = int(token) if re.fullmatch(allowed, token) else None
        values.append((y, value))
    return values


def _unmatched_trailing_identifier_slots(
    existing_rows: list[tuple[float, int | None]],
    alternate_rows: list[tuple[float, int | None]],
    *,
    typical_gap: float,
) -> list[tuple[float, int]]:
    """Return only clearly separated trailing identifiers found by alternate OCR."""
    if not existing_rows or typical_gap <= 0:
        return []
    last_center = max(center for center, _ in existing_rows)
    tolerance = typical_gap * 0.40
    candidates = []
    for center, value in alternate_rows:
        if value is None or center <= last_center + tolerance:
            continue
        if center > last_center + typical_gap * 1.60:
            continue
        if any(abs(center - known_center) <= tolerance for known_center, _ in existing_rows):
            continue
        candidates.append((center, value))
    return sorted(candidates)


def _trailing_identifier_slot_center(
    existing_rows: list[tuple[float, int | None]],
    *,
    typical_gap: float,
    max_center: float,
) -> float | None:
    """Project one final table row only when the observed row spacing is stable."""
    centers = sorted(center for center, _ in existing_rows)
    if len(centers) < 3 or typical_gap <= 0:
        return None
    gaps = [later - earlier for earlier, later in zip(centers, centers[1:])]
    if any(gap <= 0 or not 0.60 * typical_gap <= gap <= 1.40 * typical_gap for gap in gaps):
        return None
    expected = centers[-1] + typical_gap
    # The projected row center must remain inside the OCR crop. Requiring an
    # additional 40% row-gap margin rejects genuine final rows near the crop
    # edge; the later frame, horse-number, and runner-text OCR checks remain
    # mandatory before the row can be accepted.
    if expected > max_center:
        return None
    return expected


def _internal_identifier_slot_centers(
    rows: list[tuple[float, int | None]],
) -> list[tuple[float, float]]:
    """Locate one omitted internal row only when neighboring row spacing proves its position."""
    centers = sorted(center for center, _ in rows)
    if len(centers) < 4:
        return []
    gaps = [later - earlier for earlier, later in zip(centers, centers[1:])]
    ordinary_gaps = [gap for gap in gaps if gap > 0]
    if len(ordinary_gaps) < 3:
        return []
    typical_gap = sorted(ordinary_gaps)[len(ordinary_gaps) // 2]
    if typical_gap <= 0:
        return []
    projected = []
    for earlier, later in zip(centers, centers[1:]):
        gap = later - earlier
        # A gap near exactly two normal row intervals indicates one omitted row.
        # Larger gaps remain ambiguous and are deliberately not reconstructed.
        if 1.70 * typical_gap <= gap <= 2.30 * typical_gap:
            projected.append(((earlier + later) / 2, typical_gap))
    return projected


def _infer_identifier_pairs_from_complete_frame_column(
    frame_rows: list[tuple[float, int]],
    horse_rows: list[tuple[float, int | None]],
) -> list[tuple[float, tuple[int, int]]]:
    """Recover missing or one misread horse number only from a complete aligned field."""
    field_size = len(frame_rows)
    if not 2 <= field_size <= 18 or not horse_rows:
        return []

    centers = [y for y, _ in frame_rows]
    gaps = [centers[index + 1] - centers[index] for index in range(field_size - 1)]
    if any(gap <= 0 for gap in gaps):
        return []
    typical_gap = sorted(gaps)[len(gaps) // 2]
    tolerance = typical_gap * 0.40
    if tolerance <= 0:
        return []

    observed: set[int] = set()
    used_rows: set[int] = set()
    missing_slots: list[tuple[float, int]] = []
    mismatched_slots: list[tuple[float, int]] = []
    row_numbers: dict[float, int] = {}
    for center, frame in frame_rows:
        if not isinstance(frame, int) or not 1 <= frame <= 8:
            return []
        candidates = [
            index for index, (horse_y, _) in enumerate(horse_rows)
            if abs(center - horse_y) <= tolerance
        ]
        if len(candidates) > 1:
            return []
        if not candidates:
            missing_slots.append((center, frame))
            continue
        index = candidates[0]
        if index in used_rows:
            return []
        used_rows.add(index)
        horse = horse_rows[index][1]
        if horse is None:
            missing_slots.append((center, frame))
            continue
        # A recognized digit that conflicts with the official frame allocation,
        # or duplicates an already observed number, is treated as one OCR error.
        # It is never trusted; the replacement must be uniquely proven below.
        if (
            not isinstance(horse, int)
            or not 1 <= horse <= field_size
            or horse in observed
            or _expected_frame(field_size, horse) != frame
        ):
            mismatched_slots.append((center, frame))
            continue
        observed.add(horse)
        row_numbers[center] = horse

    # OCR can pick up a digit-like token from the payout/footer area just
    # below the final runner. Ignore an unmatched detection only when it is
    # strictly outside the complete frame column's vertical span; an unmatched
    # row inside the runner span remains ambiguous and rejects the whole field.
    frame_min = min(centers)
    frame_max = max(centers)
    unmatched_rows = [
        horse_rows[index][0]
        for index in range(len(horse_rows))
        if index not in used_rows
    ]
    if any(frame_min - tolerance <= y <= frame_max + tolerance for y in unmatched_rows):
        return []
    if len(mismatched_slots) > 1:
        return []
    missing_numbers = set(range(1, field_size + 1)) - observed
    unresolved_slots = missing_slots + mismatched_slots
    if len(missing_numbers) != len(unresolved_slots):
        return []

    assigned: set[int] = set()
    corrected_frames: dict[float, int] = {}
    for center, frame in unresolved_slots:
        candidates = [
            horse for horse in missing_numbers
            if horse not in assigned and _expected_frame(field_size, horse) == frame
        ]
        if len(candidates) == 1:
            horse = candidates[0]
            corrected_frames[center] = frame
        elif (
            # A single unreadable horse number can also coincide with one
            # misread frame digit. Repair both only when every other row is
            # independently valid and the sole missing horse has one official
            # frame allocation. Never apply this across multiple unresolved rows.
            len(unresolved_slots) == 1
            and len(missing_numbers) == 1
            and not mismatched_slots
        ):
            horse = next(iter(missing_numbers))
            corrected_frames[center] = _expected_frame(field_size, horse)
        else:
            return []
        row_numbers[center] = horse
        assigned.add(horse)

    if assigned != missing_numbers:
        return []
    return [
        (center, (corrected_frames.get(center, frame), row_numbers[center]))
        for center, frame in frame_rows
    ]


def _infer_identifier_pairs_from_complete_horse_column(
    horse_rows: list[tuple[float, int]],
) -> list[tuple[float, tuple[int, int]]]:
    """Infer frame numbers only when OCR proves a complete 1..N horse-number column."""
    field_size = len(horse_rows)
    if not 2 <= field_size <= 18:
        return []
    numbers = [horse for _, horse in horse_rows]
    if sorted(numbers) != list(range(1, field_size + 1)):
        return []
    if any(
        horse_rows[index + 1][0] <= horse_rows[index][0]
        for index in range(field_size - 1)
    ):
        return []
    return [
        (y, (_expected_frame(field_size, horse), horse))
        for y, horse in horse_rows
    ]


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
    if not runner_centers or not identifier_rows:
        return None
    centers = sorted(runner_centers)
    rows = sorted(identifier_rows, key=lambda item: item[0])
    gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
    gaps = [gap for gap in gaps if gap > 0]
    if len(gaps) != len(centers) - 1:
        return None
    typical_gap = sorted(gaps)[len(gaps) // 2] if gaps else 0
    tolerance = typical_gap * 0.40
    if not tolerance:
        return None

    # Extra OCR rows can come from headers, borders or cancellation/payout
    # areas. Ignore only rows that cannot match any runner. Every runner must
    # still have exactly one nearby identifier, and matches must be one-to-one.
    matched = []
    used_rows = set()
    for center in centers:
        candidates = [
            index for index, (row_y, pair) in enumerate(rows)
            if pair is not None and abs(center - row_y) <= tolerance
        ]
        if len(candidates) != 1:
            return None
        index = candidates[0]
        if index in used_rows:
            return None
        used_rows.add(index)
        matched.append(rows[index][1])

    # Preserve the table's vertical order; crossing assignments are unsafe.
    selected_indexes = []
    for center in centers:
        selected_indexes.append(next(
            index for index, (row_y, pair) in enumerate(rows)
            if index not in selected_indexes and pair is not None
            and abs(center - row_y) <= tolerance
        ))
    if selected_indexes != sorted(selected_indexes):
        return None
    return matched



def _build_ocr_runner_lines(
    identifier_rows: list[tuple[float, tuple[int, int]]],
    row_texts: list[str],
) -> list[str]:
    """Attach trusted identifiers to row-local OCR text, preserving row order."""
    if not identifier_rows or len(identifier_rows) != len(row_texts):
        return []
    lines = []
    for (_, (frame, horse)), text in zip(identifier_rows, row_texts):
        text = re.sub(r"^\s*[1-8]\s*[|/\\\\{}()\[\].,:;<>_-]*\s*\d{1,2}\s*", "", text)
        if not text.strip():
            return []
        lines.append(f"{frame}|{horse} {text.strip()}")
    return lines



def _ocr_runner_lines_are_complete(lines: list[str], expected_count: int) -> bool:
    """Use row-local OCR only when every row independently has a valid JRA result."""
    if expected_count < 1 or len(lines) != expected_count:
        return False
    for line in lines:
        runner = _runner(line)
        if runner is None:
            return False
        if (
            runner.get("finish_time_seconds") is None
            and not runner.get("result_status")
            and not runner.get("dead_heat")
        ):
            return False
    return True



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
        from PIL import Image, ImageOps
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
                # A single OCR line can contain both the race heading and
                # date/track/distance metadata. Remove only the heading token;
                # dropping the entire line silently loses those race fields.
                clean_lines = [
                    metadata_line
                    for line in result.stdout.splitlines()
                    if (metadata_line := FIELD_SIZE_RE.sub("", _strip_race_heading(line)).strip())
                ]

                # The runner table has fixed columns. OCR the narrow frame/horse
                # number strip separately at 5x; this avoids names and odds
                # confusing the two small identifier fields.
                panel_width, panel_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
                # JRA two-panel result PDFs place the right-hand race table
                # higher than the left-hand table. Use side-specific vertical
                # bounds so the first/last runner rows are not clipped.
                number_top, number_bottom = (
                    (0.292, 0.57) if side == "left" else (0.19, 0.51)
                )
                number_box = (
                    int(panel_width * 0.043), int(panel_height * number_top),
                    int(panel_width * 0.112), int(panel_height * number_bottom),
                )
                number_crop = image.crop((
                    bounds[0] + number_box[0], bounds[1] + number_box[1],
                    bounds[0] + number_box[2], bounds[1] + number_box[3],
                ))
                number_crop = number_crop.resize(
                    (number_crop.width * 5, number_crop.height * 5),
                    Image.Resampling.LANCZOS,
                )
                # Horizontal table rules cross both identifier columns and can
                # make Tesseract silently skip otherwise clear digits. Remove
                # only rows that contain a near-full-width dark rule; do not
                # alter digit pixels or infer any identifier values.
                _remove_horizontal_identifier_rules(number_crop)
                number_path = temp_dir / f"{page.stem}-{side}-numbers.png"
                number_crop.save(number_path)
                # Equal row counts are unsafe: independently OCR both columns
                # with TSV boxes and require close vertical alignment.
                panel_tsv = subprocess.run(
                    ["tesseract", str(crop_path), "stdout", "-l", "jpn+eng", "--psm", "6", "tsv"],
                    check=True, timeout=30, capture_output=True, text=True,
                ).stdout
                # Split the two narrow identifier columns before OCR. A
                # combined pass frequently merges neighboring digits (e.g. 5 and 9).
                split_x = max(1, int(number_crop.width * 0.35))
                # The horse-number column is immediately beside the frame column.
                # Do not include the wide blank/payout area to its right: Tesseract
                # otherwise groups footer text into the same OCR lines.
                horse_end = min(number_crop.width, split_x + int(number_crop.width * 0.30))
                frame_path = temp_dir / f"{page.stem}-{side}-frame.png"
                horse_path = temp_dir / f"{page.stem}-{side}-horse.png"
                number_crop.crop((0, 0, split_x, number_crop.height)).save(frame_path)
                number_crop.crop((split_x, 0, horse_end, number_crop.height)).save(horse_path)
                # Remove the left/right table rules and surrounding blank margin
                # before OCR. In the full frame strip those borders dominate the
                # narrow crop and Tesseract can return only footer noise even when
                # every printed frame digit is legible. Keep the original frame_path
                # for row-local retries; this trimmed copy is only the TSV pass.
                # The frame numerals are printed on a light strip with strong
                # vertical rules. A tighter center crop plus inverted polarity
                # gives Tesseract a clean glyph silhouette; retain the original
                # frame_path for independent row-local retries.
                frame_inner_left = int(split_x * 0.29)
                frame_inner_right = int(split_x * 0.76)
                frame_ocr_path = temp_dir / f"{page.stem}-{side}-frame-inner.png"
                frame_inner = ImageOps.invert(
                    ImageOps.grayscale(
                        Image.open(frame_path).crop(
                            (frame_inner_left, 0, frame_inner_right, number_crop.height)
                        )
                    )
                )
                ImageOps.expand(frame_inner, border=25, fill="black").save(frame_ocr_path)
                frame_tsv = subprocess.run(
                    ["tesseract", str(frame_ocr_path), "stdout", "-l", "eng", "--psm", "6", "tsv"],
                    check=True, timeout=30, capture_output=True, text=True,
                ).stdout
                horse_tsv = subprocess.run(
                    ["tesseract", str(horse_path), "stdout", "-l", "eng", "--psm", "6", "tsv"],
                    check=True, timeout=30, capture_output=True, text=True,
                ).stdout
                number_tsv = "FRAME COLUMN\n" + frame_tsv + "\nHORSE COLUMN\n" + horse_tsv
                # frame_tsv is generated from frame_inner with a 25px top border.
                # Remove that border from its reported coordinates so frame and horse
                # row centers share the same number_crop origin (horse_tsv has no border).
                frame_tsv_y_offset = float(number_box[1]) - 25 / 5
                frame_rows = _ocr_identifier_column(
                    frame_tsv, scale=5, y_offset=frame_tsv_y_offset, allowed=r"[1-8]"
                )
                horse_rows = _ocr_identifier_column(
                    horse_tsv, scale=5, y_offset=float(number_box[1]), allowed=r"(?:[1-9]|1[0-8])"
                )
                # Anchor row-local retries to frame-column positions. The normal
                # horse-column TSV pass can omit whole rows or merge digits.
                frame_slot_rows = _ocr_identifier_column_slots(
                    frame_tsv, scale=5, y_offset=frame_tsv_y_offset, allowed=r"[1-8]"
                )
                horse_slot_rows = _ocr_identifier_column_slots(
                    horse_tsv, scale=5, y_offset=float(number_box[1]), allowed=r"(?:[1-9]|1[0-8])"
                )
                identifier_row_diagnostics = []
                identifier_rows = []
                row_diagnostics = []
                # Sparse-text OCR can find a trailing horse number that the
                # normal single-column pass misses. Accept it only beyond the
                # last known slot, then independently read its frame digit.
                if len(frame_slot_rows) >= 2 and horse_slot_rows:
                    slot_centers = [center for center, _ in frame_slot_rows]
                    slot_gaps = [
                        later - earlier
                        for earlier, later in zip(slot_centers, slot_centers[1:])
                        if later > earlier
                    ]
                    typical_slot_gap = sorted(slot_gaps)[len(slot_gaps) // 2] if slot_gaps else 0
                    if typical_slot_gap > 0:
                        alternate_horse_tsv = subprocess.run(
                            ["tesseract", str(horse_path), "stdout", "-l", "eng", "--psm", "11", "tsv"],
                            check=True, timeout=30, capture_output=True, text=True,
                        ).stdout
                        alternate_horse_rows = _ocr_identifier_column_slots(
                            alternate_horse_tsv, scale=5,
                            y_offset=float(number_box[1]),
                            allowed=r"(?:[1-9]|1[0-8])",
                        )
                        trailing_rows = _unmatched_trailing_identifier_slots(
                            horse_slot_rows, alternate_horse_rows,
                            typical_gap=typical_slot_gap,
                        )
                        with Image.open(frame_path) as frame_column_image:
                            frame_left, frame_right = _identifier_column_inner_bounds(
                                frame_column_image.width, column="frame"
                            )
                            for center, horse_value in trailing_rows:
                                local_center = int((center - number_box[1]) * 5)
                                half_height = max(2, int(typical_slot_gap * 5 * 0.38))
                                top = max(0, local_center - half_height)
                                bottom = min(frame_column_image.height, local_center + half_height + 1)
                                retry_path = temp_dir / f"{page.stem}-{side}-frame-trailing.png"
                                frame_crop = frame_column_image.crop((
                                    frame_left, top, frame_right, bottom
                                ))
                                ImageOps.expand(frame_crop, border=15, fill="white").save(retry_path)
                                frame_retry_text = subprocess.run(
                                    ["tesseract", str(retry_path), "stdout", "-l", "eng", "--psm", "10",
                                     "-c", "tessedit_char_whitelist=12345678"],
                                    check=True, timeout=15, capture_output=True, text=True,
                                ).stdout.strip()
                                frame_value = _ocr_single_frame_number(frame_retry_text)
                                if frame_value is not None:
                                    frame_slot_rows.append((center, frame_value))
                                    horse_slot_rows.append((center, horse_value))
                                    identifier_row_diagnostics.append(
                                        f"alternate_trailing_identifier y={center:.1f} "
                                        f"frame={frame_value} frame_retry={frame_retry_text!r} horse={horse_value}"
                                    )
                        frame_slot_rows.sort(key=lambda item: item[0])
                        horse_slot_rows.sort(key=lambda item: item[0])
                # A final table row can be missed by both TSV passes when it
                # touches a horizontal rule. Project only one trailing slot from
                # stable frame-column spacing, then require independent frame,
                # horse, and runner-text OCR before adding it.
                if len(frame_slot_rows) >= 3:
                    slot_centers = [center for center, _ in frame_slot_rows]
                    slot_gaps = [
                        later - earlier
                        for earlier, later in zip(slot_centers, slot_centers[1:])
                    ]
                    typical_slot_gap = sorted(slot_gaps)[len(slot_gaps) // 2] if slot_gaps else 0
                    with Image.open(frame_path) as frame_column_image, Image.open(horse_path) as horse_column_image:
                        expected_center = _trailing_identifier_slot_center(
                            frame_slot_rows,
                            typical_gap=typical_slot_gap,
                            max_center=number_box[1] + min(
                                frame_column_image.height, horse_column_image.height
                            ) / 5,
                        )
                        if expected_center is not None and not any(
                            abs(expected_center - center) <= typical_slot_gap * 0.40
                            for center, _ in horse_slot_rows
                        ):
                            local_center = int((expected_center - number_box[1]) * 5)
                            half_height = max(2, int(typical_slot_gap * 5 * 0.38))
                            top = max(0, local_center - half_height)
                            bottom = min(horse_column_image.height, local_center + half_height + 1)
                            frame_left, frame_right = _identifier_column_inner_bounds(
                                frame_column_image.width, column="frame"
                            )
                            horse_left, horse_right = _identifier_column_inner_bounds(
                                horse_column_image.width, column="horse"
                            )
                            retry_specs = (
                                ("frame", frame_column_image, frame_left, frame_right, "12345678"),
                                ("horse", horse_column_image, horse_left, horse_right, "1234567890"),
                            )
                            retry_values = {}
                            for column, column_image, x_left, x_right, whitelist in retry_specs:
                                retry_path = temp_dir / f"{page.stem}-{side}-{column}-projected-trailing.png"
                                row_crop = column_image.crop((x_left, top, x_right, bottom))
                                ImageOps.expand(row_crop, border=15, fill="white").save(retry_path)
                                retry_text = subprocess.run(
                                    ["tesseract", str(retry_path), "stdout", "-l", "eng", "--psm", "10",
                                     "-c", f"tessedit_char_whitelist={whitelist}"],
                                    check=True, timeout=15, capture_output=True, text=True,
                                ).stdout.strip()
                                retry_values[column] = (
                                    _ocr_single_frame_number(retry_text)
                                    if column == "frame" else _ocr_single_horse_number(retry_text)
                                )
                            frame_value = retry_values["frame"]
                            horse_value = retry_values["horse"]
                            if frame_value is not None and horse_value is not None:
                                scale = 5 / 3
                                x_left = int(panel_width * 0.085 * scale)
                                x_right = int(panel_width * 0.99 * scale)
                                previous_center = slot_centers[-1]
                                y_top = max(0, int(((previous_center + expected_center) / 2) * scale))
                                y_bottom = int(((expected_center + expected_center + typical_slot_gap) / 2) * scale)
                                with Image.open(crop_path) as panel_image:
                                    y_bottom = min(panel_image.height, y_bottom)
                                    row_path = temp_dir / f"{page.stem}-{side}-projected-trailing-row.png"
                                    panel_image.crop((x_left, y_top, x_right, y_bottom)).save(row_path)
                                    row_text = subprocess.run(
                                        ["tesseract", str(row_path), "stdout", "-l", "jpn+eng", "--psm", "7"],
                                        check=True, timeout=20, capture_output=True, text=True,
                                    ).stdout.strip()
                                if _runner(f"{frame_value}|{horse_value} {row_text}") is not None:
                                    frame_slot_rows.append((expected_center, frame_value))
                                    horse_slot_rows.append((expected_center, horse_value))
                                    frame_rows.append((expected_center, frame_value))
                                    horse_rows.append((expected_center, horse_value))
                                    identifier_row_diagnostics.append(
                                        f"projected_trailing_identifier y={expected_center:.1f} "
                                        f"frame={frame_value} horse={horse_value} text={row_text!r}"
                                    )
                if len(frame_slot_rows) >= 2:
                    slot_centers = [center for center, _ in frame_slot_rows]
                    slot_gaps = [
                        later - earlier
                        for earlier, later in zip(slot_centers, slot_centers[1:])
                    ]
                    if all(gap > 0 for gap in slot_gaps):
                        typical_slot_gap = sorted(slot_gaps)[len(slot_gaps) // 2]
                        if typical_slot_gap > 0:
                            refined_frames = []
                            refined_horses = []
                            with Image.open(frame_path) as frame_column_image, Image.open(horse_path) as horse_column_image:
                                for row_index, (center, frame_value) in enumerate(frame_slot_rows):
                                    local_center = int((center - number_box[1]) * 5)
                                    half_height = max(2, int(typical_slot_gap * 5 * 0.38))
                                    top = max(0, local_center - half_height)
                                    bottom = min(horse_column_image.height, local_center + half_height + 1)
                                    frame_retry_text = ""
                                    if frame_value is None:
                                        retry_path = temp_dir / f"{page.stem}-{side}-frame-row-{row_index}.png"
                                        frame_left, frame_right = _identifier_column_inner_bounds(
                                            frame_column_image.width, column="frame"
                                        )
                                        frame_crop = frame_column_image.crop((
                                            frame_left, top, frame_right,
                                            min(frame_column_image.height, bottom),
                                        ))
                                        ImageOps.expand(frame_crop, border=15, fill="white").save(retry_path)
                                        frame_retry_text = subprocess.run(
                                            ["tesseract", str(retry_path), "stdout", "-l", "eng", "--psm", "10", "-c", "tessedit_char_whitelist=12345678"],
                                            check=True, timeout=15, capture_output=True, text=True,
                                        ).stdout.strip()
                                        frame_value = _ocr_single_frame_number(frame_retry_text)
                                    horse_retry_path = temp_dir / f"{page.stem}-{side}-horse-row-{row_index}.png"
                                    # Keep the complete isolated horse-number column on retry.
                                    # Tight inner crops clipped narrow digits such as 1 and 5
                                    # against nearby table rules in the JRA scan.
                                    horse_left, horse_right = _identifier_column_retry_bounds(
                                        horse_column_image.width, column="horse"
                                    )
                                    horse_crop = horse_column_image.crop((
                                        horse_left, top, horse_right,
                                        min(horse_column_image.height, bottom),
                                    ))
                                    ImageOps.expand(horse_crop, border=15, fill="white").save(horse_retry_path)
                                    horse_retry_text = subprocess.run(
                                        ["tesseract", str(horse_retry_path), "stdout", "-l", "eng", "--psm", "10", "-c", "tessedit_char_whitelist=0123456789"],
                                        check=True, timeout=15, capture_output=True, text=True,
                                    ).stdout.strip()
                                    # Always run independent single-row segmentation modes.
                                    # A lone OCR mode can turn 8 into 5 or drop the leading
                                    # digit of a two-digit number; require at least two modes
                                    # to agree, and reject any conflict with the column TSV.
                                    alternate_readings = []
                                    for psm in ("8", "13"):
                                        alternate_readings.append(subprocess.run(
                                            ["tesseract", str(horse_retry_path), "stdout", "-l", "eng",
                                             "--psm", psm, "-c", "tessedit_char_whitelist=0123456789"],
                                            check=True, timeout=15, capture_output=True, text=True,
                                        ).stdout.strip())
                                    row_readings = [horse_retry_text, *alternate_readings]
                                    aligned = [
                                        value for y, value in horse_slot_rows
                                        if abs(y - center) <= typical_slot_gap * 0.40 and value is not None
                                    ]
                                    horse_value = _select_consensus_horse_number(aligned, row_readings)
                                    identifier_row_diagnostics.append(
                                        f"identifier_row={row_index + 1} y={center:.1f} "
                                        f"frame={frame_value} frame_retry={frame_retry_text!r} "
                                        f"horse={horse_value} horse_retry={horse_retry_text!r}"
                                    )
                                    refined_frames.append((center, frame_value))
                                    refined_horses.append((center, horse_value))
                            frame_rows = [(y, value) for y, value in refined_frames if value is not None]
                            horse_slot_rows = refined_horses
                            horse_rows = [(y, value) for y, value in refined_horses if value is not None]
                # Recover an internal row omitted by the number-column OCR only when
                # the surrounding rows establish a single missing slot. Both identifiers
                # and the Japanese runner row are then re-read independently; no number
                # is inferred from neighboring values or field order.
                internal_slots = _internal_identifier_slot_centers(frame_slot_rows)
                if internal_slots:
                    with Image.open(frame_path) as frame_column_image, Image.open(horse_path) as horse_column_image, Image.open(crop_path) as panel_image:
                        frame_left, frame_right = _identifier_column_inner_bounds(
                            frame_column_image.width, column="frame"
                        )
                        horse_left, horse_right = _identifier_column_inner_bounds(
                            horse_column_image.width, column="horse"
                        )
                        scale = 5 / 3
                        for slot_index, (center, typical_gap) in enumerate(internal_slots):
                            local_center = int((center - number_box[1]) * 5)
                            half_height = max(2, int(typical_gap * 5 * 0.38))
                            top = max(0, local_center - half_height)
                            bottom = min(frame_column_image.height, local_center + half_height + 1)
                            retry_values = {}
                            for column, column_image, x_left, x_right, whitelist in (
                                ("frame", frame_column_image, frame_left, frame_right, "12345678"),
                                ("horse", horse_column_image, horse_left, horse_right, "1234567890"),
                            ):
                                retry_path = temp_dir / f"{page.stem}-{side}-{column}-internal-{slot_index}.png"
                                row_crop = column_image.crop((x_left, top, x_right, bottom))
                                ImageOps.expand(row_crop, border=15, fill="white").save(retry_path)
                                retry_text = subprocess.run(
                                    ["tesseract", str(retry_path), "stdout", "-l", "eng", "--psm", "10",
                                     "-c", f"tessedit_char_whitelist={whitelist}"],
                                    check=True, timeout=15, capture_output=True, text=True,
                                ).stdout.strip()
                                retry_values[column] = (
                                    _ocr_single_frame_number(retry_text)
                                    if column == "frame" else _ocr_single_horse_number(retry_text)
                                )
                            frame_value, horse_value = retry_values["frame"], retry_values["horse"]
                            if frame_value is None or horse_value is None:
                                identifier_row_diagnostics.append(
                                    f"internal_identifier_slot y={center:.1f} "
                                    f"frame={frame_value} horse={horse_value} rejected=identifier_ocr"
                                )
                                continue
                            row_top = max(0, int((center - typical_gap * 0.50) * scale))
                            row_bottom = min(
                                panel_image.height,
                                int((center + typical_gap * 0.50) * scale),
                            )
                            x_left = int(panel_width * 0.085 * scale)
                            # Include the official finish-time/result columns at the
                            # far right of the runner table. The previous 82% crop
                            # ended before those columns on some JRA layouts.
                            x_right = int(panel_width * 0.99 * scale)
                            row_path = temp_dir / f"{page.stem}-{side}-internal-row-{slot_index}.png"
                            panel_image.crop((x_left, row_top, x_right, row_bottom)).save(row_path)
                            row_text = subprocess.run(
                                ["tesseract", str(row_path), "stdout", "-l", "jpn+eng", "--psm", "7"],
                                check=True, timeout=20, capture_output=True, text=True,
                            ).stdout.strip()
                            if _runner(f"{frame_value}|{horse_value} {row_text}") is None:
                                identifier_row_diagnostics.append(
                                    f"internal_identifier_slot y={center:.1f} "
                                    f"frame={frame_value} horse={horse_value} "
                                    f"text={row_text!r} rejected=runner_row_validation"
                                )
                                continue
                            frame_slot_rows.append((center, frame_value))
                            horse_slot_rows.append((center, horse_value))
                            frame_rows.append((center, frame_value))
                            horse_rows.append((center, horse_value))
                            identifier_row_diagnostics.append(
                                f"internal_identifier_slot y={center:.1f} "
                                f"frame={frame_value} horse={horse_value} text={row_text!r} accepted"
                            )
                    frame_slot_rows.sort(key=lambda item: item[0])
                    horse_slot_rows.sort(key=lambda item: item[0])
                    frame_rows.sort(key=lambda item: item[0])
                    horse_rows.sort(key=lambda item: item[0])
                # Retry unreadable horse-number cells at their own OCR row positions.
                # This reads the printed digit again; it never derives a horse number
                # from neighboring numbers, frame allocation, or runner order.
                valid_horse_centers = sorted(
                    center for center, value in horse_slot_rows if value is not None
                )
                horse_gaps = [
                    later - earlier
                    for earlier, later in zip(valid_horse_centers, valid_horse_centers[1:])
                    if later > earlier
                ]
                typical_horse_gap = sorted(horse_gaps)[len(horse_gaps) // 2] if horse_gaps else 0
                if typical_horse_gap > 0 and any(value is None for _, value in horse_slot_rows):
                    with Image.open(horse_path) as horse_column_image:
                        horse_left, horse_right = _identifier_column_inner_bounds(
                            horse_column_image.width, column="horse"
                        )
                        retried_slots = []
                        for slot_index, (center, value) in enumerate(horse_slot_rows):
                            if value is not None:
                                retried_slots.append((center, value))
                                continue
                            local_center = int((center - number_box[1]) * 5)
                            half_height = max(2, int(typical_horse_gap * 5 * 0.38))
                            top = max(0, local_center - half_height)
                            bottom = min(horse_column_image.height, local_center + half_height + 1)
                            retry_path = temp_dir / f"{page.stem}-{side}-horse-slot-{slot_index}.png"
                            row_crop = horse_column_image.crop(
                                (horse_left, top, horse_right, bottom)
                            )
                            ImageOps.expand(row_crop, border=15, fill="white").save(retry_path)
                            retry_text = subprocess.run(
                                ["tesseract", str(retry_path), "stdout", "-l", "eng", "--psm", "10",
                                 "-c", "tessedit_char_whitelist=0123456789"],
                                check=True, timeout=15, capture_output=True, text=True,
                            ).stdout.strip()
                            retry_value = _ocr_single_horse_number(retry_text)
                            retried_slots.append((center, retry_value))
                            identifier_row_diagnostics.append(
                                f"horse_slot_retry y={center:.1f} original={value} "
                                f"retry={retry_text!r} accepted={retry_value is not None}"
                            )
                        horse_slot_rows = retried_slots
                        horse_rows = [
                            (center, value) for center, value in horse_slot_rows
                            if value is not None
                        ]
                # A digit can be confidently misread as another valid digit (for
                # example, 5 as 3). When the complete frame column supplies every
                # row, retry only horse cells that conflict with the official
                # frame allocation or duplicate another horse number. The value
                # is accepted only when the fresh image OCR independently reads
                # a digit compatible with that row's printed frame.
                if (
                    2 <= len(frame_slot_rows) <= 18
                    and all(value is not None for _, value in frame_slot_rows)
                ):
                    frame_centers = sorted(frame_slot_rows, key=lambda item: item[0])
                    frame_gaps = [
                        later[0] - earlier[0]
                        for earlier, later in zip(frame_centers, frame_centers[1:])
                        if later[0] > earlier[0]
                    ]
                    frame_gap = sorted(frame_gaps)[len(frame_gaps) // 2] if frame_gaps else 0
                    tolerance = frame_gap * 0.40
                    aligned = []
                    used_horse_slots = set()
                    if tolerance > 0:
                        for frame_center, frame_value in frame_centers:
                            candidates = [
                                index for index, (horse_center, _) in enumerate(horse_slot_rows)
                                if index not in used_horse_slots
                                and abs(horse_center - frame_center) <= tolerance
                            ]
                            if len(candidates) != 1:
                                aligned = []
                                break
                            index = candidates[0]
                            used_horse_slots.add(index)
                            aligned.append((frame_center, frame_value, index, horse_slot_rows[index][1]))
                    if len(aligned) == len(frame_centers) and len(used_horse_slots) == len(horse_slot_rows):
                        counts = {}
                        for _, _, _, value in aligned:
                            if value is not None:
                                counts[value] = counts.get(value, 0) + 1
                        retry_indices = [
                            index for _, frame_value, index, horse_value in aligned
                            if (
                                horse_value is None
                                or counts.get(horse_value, 0) > 1
                                or _expected_frame(len(frame_centers), horse_value) != frame_value
                            )
                        ]
                        reserved = {
                            horse_value for _, frame_value, index, horse_value in aligned
                            if (
                                horse_value is not None
                                and counts.get(horse_value, 0) == 1
                                and _expected_frame(len(frame_centers), horse_value) == frame_value
                                and index not in retry_indices
                            )
                        }
                        if retry_indices:
                            with Image.open(horse_path) as horse_column_image:
                                horse_left, horse_right = _identifier_column_inner_bounds(
                                    horse_column_image.width, column="horse"
                                )
                                retried_slots = list(horse_slot_rows)
                                for center, frame_value, slot_index, original in aligned:
                                    if slot_index not in retry_indices:
                                        continue
                                    local_center = int((center - number_box[1]) * 5)
                                    half_height = max(2, int(frame_gap * 5 * 0.38))
                                    top = max(0, local_center - half_height)
                                    bottom = min(horse_column_image.height, local_center + half_height + 1)
                                    retry_path = temp_dir / f"{page.stem}-{side}-horse-mismatch-{slot_index}.png"
                                    row_crop = horse_column_image.crop(
                                        (horse_left, top, horse_right, bottom)
                                    )
                                    ImageOps.expand(row_crop, border=15, fill="white").save(retry_path)
                                    retry_text = subprocess.run(
                                        ["tesseract", str(retry_path), "stdout", "-l", "eng", "--psm", "10",
                                         "-c", "tessedit_char_whitelist=0123456789"],
                                        check=True, timeout=15, capture_output=True, text=True,
                                    ).stdout.strip()
                                    retry_value = _verified_horse_retry(
                                        retry_text, len(frame_centers), frame_value, reserved
                                    )
                                    contrast_readings = []
                                    if retry_value is None:
                                        # A valid-looking but frame-inconsistent digit (for example,
                                        # a printed 5 read as 9) must not be trusted. Re-read the
                                        # same isolated printed cell with independent contrast
                                        # thresholds; accept only a two-reading consensus that
                                        # also agrees with the independently read frame.
                                        gray = ImageOps.autocontrast(ImageOps.grayscale(row_crop))
                                        enlarged = gray.resize(
                                            (max(1, gray.width * 3), max(1, gray.height * 3)),
                                            Image.Resampling.LANCZOS,
                                        )
                                        for threshold in (100, 145, 190):
                                            variant = enlarged.point(
                                                lambda pixel, cutoff=threshold: 255 if pixel >= cutoff else 0
                                            )
                                            variant_path = temp_dir / (
                                                f"{page.stem}-{side}-horse-mismatch-{slot_index}-"
                                                f"threshold-{threshold}.png"
                                            )
                                            ImageOps.expand(variant, border=30, fill="white").save(variant_path)
                                            contrast_readings.append(subprocess.run(
                                                ["tesseract", str(variant_path), "stdout", "-l", "eng",
                                                 "--psm", "10", "-c",
                                                 "tessedit_char_whitelist=0123456789"],
                                                check=True, timeout=15, capture_output=True, text=True,
                                            ).stdout.strip())
                                        retry_value = _verified_horse_retry_consensus(
                                            contrast_readings, len(frame_centers), frame_value, reserved
                                        )
                                    accepted_retry = retry_value is not None
                                    # A primary OCR digit that contradicts the printed
                                    # frame is not retained when the independent retry
                                    # fails. Keep the slot unreadable; complete-field
                                    # validation may resolve it only from the unique
                                    # official frame allocation.
                                    retried_slots[slot_index] = (center, retry_value)
                                    if accepted_retry:
                                        reserved.add(retry_value)
                                    identifier_row_diagnostics.append(
                                        f"horse_mismatch_retry y={center:.1f} original={original} "
                                        f"retry={retry_text!r} contrast={contrast_readings!r} "
                                        f"accepted={accepted_retry}"
                                    )
                                horse_slot_rows = retried_slots
                                horse_rows = [
                                    (center, value) for center, value in horse_slot_rows
                                    if value is not None
                                ]
                # Use independently recognized horse-number row centers to retry the frame digit.
                # The frame and horse digits are printed side by side on the same official row;
                # this is a fresh read of the frame cell, not a frame/horse-number inference.
                if typical_horse_gap > 0 and horse_slot_rows:
                    with Image.open(frame_path) as frame_column_image:
                        frame_left, frame_right = _identifier_column_inner_bounds(
                            frame_column_image.width, column="frame"
                        )
                        frame_tolerance = typical_horse_gap * 0.40
                        for slot_index, (center, horse_value) in enumerate(horse_slot_rows):
                            if horse_value is None:
                                continue
                            if any(
                                frame_value is not None and abs(frame_center - center) <= frame_tolerance
                                for frame_center, frame_value in frame_slot_rows
                            ):
                                continue
                            local_center = int((center - number_box[1]) * 5)
                            half_height = max(2, int(typical_horse_gap * 5 * 0.38))
                            top = max(0, local_center - half_height)
                            bottom = min(frame_column_image.height, local_center + half_height + 1)
                            retry_path = temp_dir / f"{page.stem}-{side}-frame-at-horse-row-{slot_index}.png"
                            row_crop = frame_column_image.crop(
                                (frame_left, top, frame_right, bottom)
                            )
                            ImageOps.expand(row_crop, border=15, fill="white").save(retry_path)
                            retry_text = subprocess.run(
                                ["tesseract", str(retry_path), "stdout", "-l", "eng", "--psm", "10",
                                 "-c", "tessedit_char_whitelist=12345678"],
                                check=True, timeout=15, capture_output=True, text=True,
                            ).stdout.strip()
                            retry_value = _ocr_single_frame_number(retry_text)
                            identifier_row_diagnostics.append(
                                f"frame_slot_retry y={center:.1f} retry={retry_text!r} "
                                f"accepted={retry_value is not None}"
                            )
                            if retry_value is not None:
                                frame_slot_rows.append((center, retry_value))
                                frame_rows.append((center, retry_value))
                        frame_slot_rows.sort(key=lambda item: item[0])
                        frame_rows.sort(key=lambda item: item[0])
                # Remove only identical same-column detections that land on the
                # same printed row. Conflicting nearby values remain ambiguous.
                frame_slot_rows = _deduplicate_identifier_slots(frame_slot_rows)
                horse_slot_rows = _deduplicate_identifier_slots(horse_slot_rows)
                # Footer/payout OCR can create a final isolated slot far below the
                # runner table. Remove only that trailing outlier. The complete-field
                # validator still requires the independently read official headcount
                # and exact 1..N horse numbers, so a missed real runner is rejected.
                frame_slot_rows = _trim_trailing_identifier_outliers(frame_slot_rows)
                horse_slot_rows = _trim_trailing_identifier_outliers(horse_slot_rows)
                frame_rows = _deduplicate_identifier_slots([
                    (center, value) for center, value in frame_slot_rows if value is not None
                ])
                horse_rows = _deduplicate_identifier_slots([
                    (center, value) for center, value in horse_slot_rows if value is not None
                ])
                # Preserve the ordinary aligned-column result when no inference is needed.
                identifier_rows = _pair_identifier_columns(frame_rows, horse_rows)
                # If a complete frame column is available, fill unreadable horse
                # digits only when the remaining numbers and JRA frame allocation
                # identify every missing row uniquely.
                inferred_from_frames = _infer_identifier_pairs_from_complete_frame_column(
                    frame_rows, horse_slot_rows
                )
                if inferred_from_frames:
                    identifier_rows = inferred_from_frames
                # When the horse-number column proves a complete 1..N field,
                # derive frame numbers from the official JRA allocation. The
                # neighboring narrow column can be shifted or read as another
                # table field (for example, finish position), so a non-empty
                # but inconsistent pair must not override the complete column.
                inferred_identifier_rows = _infer_identifier_pairs_from_complete_horse_column(
                    horse_rows
                )
                if inferred_identifier_rows:
                    identifier_rows = inferred_identifier_rows
                # OCR complete runner rows only after identifier pairs are valid.
                row_diagnostics = []
                if len(identifier_rows) >= 2:
                    centers = [row[0] for row in identifier_rows]
                    gaps = [b - a for a, b in zip(centers, centers[1:]) if b > a]
                    if len(gaps) == len(centers) - 1:
                        typical_gap = sorted(gaps)[len(gaps) // 2]
                        panel_image = Image.open(crop_path)
                        try:
                            scale = 5 / 3
                            identity_left, identity_right = _ocr_runner_identity_bounds(panel_image.width)
                            x_left = identity_left
                            x_right = identity_right
                            row_texts = []
                            time_diagnostics = []
                            for row_index, center in enumerate(centers):
                                previous = centers[row_index - 1] if row_index else center - typical_gap
                                following = centers[row_index + 1] if row_index + 1 < len(centers) else center + typical_gap
                                y_top, y_bottom = _ocr_runner_row_bounds(
                                    center, previous, following, scale, panel_image.height
                                )
                                row_path = temp_dir / f"{page.stem}-{side}-row-{row_index}.png"
                                row_image = panel_image.crop((x_left, y_top, x_right, y_bottom))
                                # The runner row is short and densely printed. Normalize
                                # contrast and enlarge it before OCR so small Japanese names
                                # and the finish-time column survive the line segmentation.
                                row_image = ImageOps.autocontrast(ImageOps.grayscale(row_image))
                                # These identity columns contain compact Japanese glyphs.
                                # Enlarge the bounded single-row crop more aggressively so
                                # Tesseract receives enough pixels for the horse name and
                                # sex/age without expanding into adjacent runner rows.
                                row_image = row_image.resize(
                                    (row_image.width * 4, row_image.height * 4),
                                    Image.Resampling.LANCZOS,
                                )
                                row_image.save(row_path)
                                row_result = subprocess.run(
                                    ["tesseract", str(row_path), "stdout", "-l", "jpn+eng", "--psm", "7",
                                     "-c", "preserve_interword_spaces=1"],
                                    check=True, timeout=20, capture_output=True, text=True,
                                ).stdout.strip()
                                frame_value, horse_value = identifier_rows[row_index][1]
                                parsed_row = _runner(f"{frame_value}|{horse_value} {row_result}")
                                # Retry block mode not only when the runner identity
                                # fails, but also when the finish/result cell is absent.
                                # A valid name/sex/age alone is not enough for acceptance.
                                needs_result_retry = (
                                    parsed_row is None
                                    or (
                                        parsed_row.get("finish_time_seconds") is None
                                        and not parsed_row.get("result_status")
                                        and not parsed_row.get("dead_heat")
                                    )
                                )
                                if needs_result_retry:
                                    retry_result = subprocess.run(
                                        ["tesseract", str(row_path), "stdout", "-l", "jpn+eng", "--psm", "6"],
                                        check=True, timeout=20, capture_output=True, text=True,
                                    ).stdout.strip()
                                    retry_row = _runner(f"{frame_value}|{horse_value} {retry_result}")
                                    if retry_row is not None and (
                                        parsed_row is None
                                        or retry_row.get("finish_time_seconds") is not None
                                        or retry_row.get("result_status")
                                        or retry_row.get("dead_heat")
                                    ):
                                        row_result = retry_result
                                # Sparse table rows can split the horse name and
                                # sex/age across separate OCR lines. A second
                                # sparse-text pass reads the same row crop; join
                                # its line fragments only when the strict runner
                                # parser can then recognize the printed fields.
                                if _runner(f"{frame_value}|{horse_value} {row_result}") is None:
                                    sparse_result = subprocess.run(
                                        ["tesseract", str(row_path), "stdout", "-l", "jpn+eng", "--psm", "11"],
                                        check=True, timeout=20, capture_output=True, text=True,
                                    ).stdout.strip()
                                    sparse_result = " ".join(sparse_result.split())
                                    sparse_row = _runner(f"{frame_value}|{horse_value} {sparse_result}")
                                    if sparse_row is not None:
                                        row_result = sparse_result
                                # Small row crops can leave Japanese name/sex/age glyphs
                                # undersampled. Retry an enlarged, contrast-normalized copy
                                # with independent line/layout modes. A retry is usable only
                                # when the strict parser reads an official result time,
                                # terminal status, or dead-heat marker from that same row.
                                parsed_after_retry = _runner(f"{frame_value}|{horse_value} {row_result}")
                                if (
                                    parsed_after_retry is None
                                    or (
                                        parsed_after_retry.get("finish_time_seconds") is None
                                        and not parsed_after_retry.get("result_status")
                                        and not parsed_after_retry.get("dead_heat")
                                    )
                                ):
                                    with Image.open(row_path) as source_row_image:
                                        enhanced_row = ImageOps.autocontrast(
                                            ImageOps.grayscale(source_row_image)
                                        )
                                        enhanced_row = enhanced_row.resize(
                                            (enhanced_row.width * 2, enhanced_row.height * 2),
                                            Image.Resampling.LANCZOS,
                                        )
                                        enhanced_row_path = temp_dir / f"{page.stem}-{side}-row-{row_index}-enhanced.png"
                                        enhanced_row.save(enhanced_row_path)
                                    for retry_psm in ("7", "6", "11"):
                                        enhanced_text = subprocess.run(
                                            ["tesseract", str(enhanced_row_path), "stdout", "-l", "jpn+eng",
                                             "--psm", retry_psm],
                                            check=True, timeout=20, capture_output=True, text=True,
                                        ).stdout.strip()
                                        enhanced_text = " ".join(enhanced_text.split())
                                        enhanced_runner = _runner(
                                            f"{frame_value}|{horse_value} {enhanced_text}"
                                        )
                                        if enhanced_runner is not None and (
                                            enhanced_runner.get("finish_time_seconds") is not None
                                            or enhanced_runner.get("result_status")
                                            or enhanced_runner.get("dead_heat")
                                        ):
                                            row_result = enhanced_text
                                            break
                                # Independently OCR the official finish-time cell.
                                # A clear isolated reading can recover a missed time, but a
                                # disagreement with the full-row reading makes the row
                                # incomplete instead of silently choosing either value.
                                parsed_row = _runner(f"{frame_value}|{horse_value} {row_result}")
                                time_left = int(panel_image.width * 0.765)
                                time_right = int(panel_image.width * 0.845)
                                time_crop_path = temp_dir / f"{page.stem}-{side}-time-{row_index}.png"
                                time_crop = panel_image.crop((time_left, y_top, time_right, y_bottom))
                                time_crop = ImageOps.autocontrast(ImageOps.grayscale(time_crop))
                                time_crop = time_crop.resize(
                                    (time_crop.width * 3, time_crop.height * 3),
                                    Image.Resampling.LANCZOS,
                                )
                                time_crop.save(time_crop_path)
                                time_reads = []
                                for time_psm in ("7", "8"):
                                    time_text = subprocess.run(
                                        ["tesseract", str(time_crop_path), "stdout", "-l", "eng",
                                         "--psm", time_psm, "-c",
                                         "tessedit_char_whitelist=0123456789:."],
                                        check=True, timeout=15, capture_output=True, text=True,
                                    ).stdout.strip()
                                    time_reads.append(time_text)
                                isolated_time = _strict_ocr_finish_time(" ".join(time_reads))
                                time_diagnostics.append(
                                    f"time_row={row_index + 1} horse={horse_value} "
                                    f"y=({y_top},{y_bottom}) x=({time_left},{time_right}) "
                                    f"reads={time_reads!r} parsed_time={isolated_time!r} "
                                    f"row_parse={'ok' if parsed_row is not None else 'failed'}"
                                )
                                if isolated_time and parsed_row is not None:
                                    # The isolated crop is aligned to the printed time column.
                                    # Use that independent cell reading to replace any
                                    # conflicting full-row OCR token, never to infer a value.
                                    row_result = re.sub(
                                        r"(?<![0-9A-Za-z])[0-9０-９]{1,2}[:：][0-9０-９]{2}[.．]\s*[0-9０-９](?![0-9A-Za-z])",
                                        "",
                                        row_result,
                                    )
                                    row_result = f"{row_result} | {isolated_time}"
                                row_texts.append(row_result)
                            row_lines = _build_ocr_runner_lines(identifier_rows, row_texts)
                            row_diagnostics = [
                                f"row={index + 1} frame={pair[1][0]} horse={pair[1][1]} text={row_text!r}"
                                for index, (pair, row_text) in enumerate(zip(identifier_rows, row_texts))
                            ]
                            row_diagnostics = time_diagnostics + row_diagnostics
                            row_diagnostics.append(f"assembled_runner_lines={row_lines!r}")
                            # The official runner-table footer prints the declared field size
                            # (for example, '(16頭)') immediately below the final runner.
                            # Whole-panel OCR often drops this small label, so read that
                            # bounded footer crop independently and accept only an exact,
                            # unambiguous FIELD_SIZE_RE match.
                            last_runner_center = _last_valid_ocr_runner_center(
                                centers, identifier_rows, row_texts
                            )
                            if last_runner_center is None:
                                # Full-row OCR can fail on a noisy scan even when the
                                # identifier-column geometry still shows the final runner row.
                                # Use stable row spacing only to position the footer OCR crop;
                                # this does not infer a horse number or accept a race result.
                                last_runner_center = _last_stable_identifier_slot_center(centers)
                            if last_runner_center is not None:
                                headcount_top = max(0, int(last_runner_center * scale) + 20)
                                headcount_bottom = min(panel_image.height, headcount_top + 180)
                                if headcount_bottom > headcount_top:
                                    headcount_path = temp_dir / f"{page.stem}-{side}-headcount.png"
                                    panel_image.crop((0, headcount_top, panel_image.width, headcount_bottom)).save(headcount_path)
                                    headcount_text = subprocess.run(
                                        ["tesseract", str(headcount_path), "stdout", "-l", "jpn+eng", "--psm", "11"],
                                        check=True, timeout=15, capture_output=True, text=True,
                                    ).stdout.strip()
                                    declared_headcount = _ocr_declared_field_size(headcount_text)
                                    row_diagnostics.append(
                                        f"headcount_retry last_runner_y={last_runner_center:.1f} "
                                        f"text={headcount_text!r} accepted={declared_headcount}"
                                    )
                                    # Retry the complete footer band with a uniform text-line
                                    # layout. The official '(N頭)' label sits at the left edge of
                                    # the footer; narrow crops can clip its leading digit, while
                                    # thresholding can turn '(16頭)' into '(1698)'.
                                    if declared_headcount is None:
                                        footer_top = max(0, int(last_runner_center * scale) + 20)
                                        footer_bottom = min(panel_image.height, footer_top + 180)
                                        if footer_bottom > footer_top:
                                            footer_retry_path = temp_dir / f"{page.stem}-{side}-headcount-line.png"
                                            panel_image.crop(
                                                (0, footer_top, panel_image.width, footer_bottom)
                                            ).save(footer_retry_path)
                                            footer_retry_text = subprocess.run(
                                                ["tesseract", str(footer_retry_path), "stdout", "-l", "jpn+eng", "--psm", "6"],
                                                check=True, timeout=15, capture_output=True, text=True,
                                            ).stdout.strip()
                                            declared_headcount = _ocr_declared_field_size(footer_retry_text)
                                            row_diagnostics.append(
                                                f"headcount_line_retry last_runner_y={last_runner_center:.1f} "
                                                f"text={footer_retry_text!r} accepted={declared_headcount}"
                                            )
                                    # A single OCR layout can turn '(12頭)' into '(2頭)'.
                                    # Therefore the first-pass and footer-retry readings are
                                    # diagnostic only: the declared field size is trusted only
                                    # when at least two independent overlapping footer windows
                                    # read the same exact official '(N頭)' label.
                                    single_read_headcount = declared_headcount
                                    label_left = max(0, int(panel_image.width * 0.10))
                                    label_right = min(panel_image.width, int(panel_image.width * 0.365))
                                    label_reads = []
                                    offset_diagnostics = []
                                    for y_offset in (-80, -60, -40, -20):
                                        label_top = max(0, headcount_top + y_offset)
                                        label_bottom = min(panel_image.height, label_top + 180)
                                        if label_bottom <= label_top:
                                            continue
                                        label_crop = panel_image.crop(
                                            (label_left, label_top, label_right, label_bottom)
                                        )
                                        label_crop = ImageOps.autocontrast(ImageOps.grayscale(label_crop))
                                        label_crop = label_crop.resize(
                                            (label_crop.width * 4, label_crop.height * 4),
                                            Image.Resampling.LANCZOS,
                                        )
                                        label_path = temp_dir / (
                                            f"{page.stem}-{side}-headcount-label-{y_offset}.png"
                                        )
                                        label_crop.save(label_path)
                                        label_text = subprocess.run(
                                            ["tesseract", str(label_path), "stdout", "-l", "jpn+eng", "--psm", "6"],
                                            check=True, timeout=15, capture_output=True, text=True,
                                        ).stdout.strip()
                                        label_reads.append(label_text)
                                        offset_diagnostics.append(
                                            {"offset": y_offset, "text": label_text,
                                             "accepted": _ocr_declared_field_size(label_text)}
                                        )
                                    declared_headcount = _consensus_declared_field_size(label_reads)
                                    row_diagnostics.append(
                                        f"headcount_label_scan initial_single_read={single_read_headcount!r} "
                                        f"readings={offset_diagnostics!r} "
                                        f"consensus_accepted={declared_headcount}"
                                    )
                                    if declared_headcount is not None:
                                        clean_lines.append(f"({declared_headcount}頭)")
                                        clean_lines.append(
                                            f"JRA_OCR_HEADCOUNT_VERIFIED={declared_headcount}"
                                        )
                            if _ocr_runner_lines_are_complete(row_lines, len(identifier_rows)):
                                # Row-local OCR is often noisier than the complete-panel pass.
                                # Replace its runner lines only when every identified row has
                                # a strict result-time or terminal-status parse; otherwise
                                # preserve the original panel OCR for downstream validation.
                                non_runner_lines = [
                                    line for line in clean_lines if not _runner(line.strip())
                                ]
                                clean_lines = non_runner_lines + row_lines
                            else:
                                row_diagnostics.append(
                                    "row_local_ocr_not_used: incomplete_or_invalid_runner_rows"
                                )
                        finally:
                            panel_image.close()

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
                        number_tsv, encoding="utf-8"
                    )
                    (destination / f"{page.stem}-{side}-rows.txt").write_text(
                        "\n".join(identifier_row_diagnostics + row_diagnostics)
                        + "\n--- metadata and parser input ---\n"
                        + "\n".join(clean_lines),
                        encoding="utf-8",
                    )

                race_number = page_index * 2 + side_index + 1
                chunks.append(f"第{race_number}競走\n" + "\n".join(clean_lines))
    return "\n".join(chunks)

def _track_from_official_pdf_url(source_url: str) -> str | None:
    """Read the racecourse slug only from a canonical JRA official result-PDF URL."""
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.hostname != "www.jra.go.jp":
        return None
    match = re.fullmatch(
        r"/datafile/seiseki/report/\d{4}/\d+-\d+"
        r"(sapporo|hakodate|fukushima|niigata|tokyo|nakayama|chukyo|kyoto|hanshin|kokura)\d+\.pdf",
        parsed.path,
    )
    if not match:
        return None
    return {
        "sapporo": "札幌", "hakodate": "函館", "fukushima": "福島",
        "niigata": "新潟", "tokyo": "東京", "nakayama": "中山",
        "chukyo": "中京", "kyoto": "京都", "hanshin": "阪神",
        "kokura": "小倉",
    }[match.group(1)]


def _propagate_pdf_identity(
    records: list[dict[str, Any]], *, source_url: str = ""
) -> list[dict[str, Any]]:
    """Propagate one PDF-wide identity; derive track only from its canonical JRA URL."""
    for field in ("date", "track"):
        observed = {
            row.get(field)
            for row in records
            if isinstance(row.get(field), str) and row.get(field).strip()
        }
        if len(observed) == 1:
            verified_value = next(iter(observed))
        elif field == "track" and not observed:
            verified_value = _track_from_official_pdf_url(source_url)
        else:
            continue
        if verified_value:
            for row in records:
                if not row.get(field):
                    row[field] = verified_value
    return records


def _ocr_rejection_reasons(rows: list[dict[str, Any]]) -> list[str]:
    """Explain why an OCR race fails strict acceptance without changing any row."""
    if not rows:
        return ["no_runner_rows"]
    reasons = []
    if not rows[0].get("date"):
        reasons.append("missing_verified_date")
    if not rows[0].get("track"):
        reasons.append("missing_verified_track")
    if any(
        (isinstance(row.get("finish_time_seconds"), (int, float))
         and row["finish_time_seconds"] > 15 * 60)
        or (isinstance(row.get("runner_validation_warnings"), list)
            and any(w in {"invalid_finish_time_seconds", "invalid_finish_time_minutes"}
                    for w in row["runner_validation_warnings"]))
        for row in rows
    ):
        reasons.append("invalid_finish_time")
    if any("finish_time_seconds" in row and row.get("finish_time_seconds") is None
           and not row.get("result_status") for row in rows):
        reasons.append("missing_runner_result")
    declared = rows[0].get("declared_field_size")
    inferred = _inferred_complete_field_size(rows) if declared is None else None
    expected = declared if declared is not None else inferred
    if expected is None:
        reasons.append("field_size_unverified")
    elif len(rows) != expected:
        reasons.append(f"runner_count_mismatch:{len(rows)}/{expected}")
    if expected is not None:
        numbers = [row.get("horse_number") for row in rows]
        if sorted(numbers) != list(range(1, expected + 1)):
            reasons.append("horse_numbers_not_exact_1_to_N")
    return reasons or ["accepted"]


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
        # Each official JRA result PDF represents one course and one meeting day.
        # Reuse a date/track only when OCR independently found exactly one value
        # somewhere in that same PDF; conflicting or wholly missing values stay unknown.
        ocr_records = _propagate_pdf_identity(ocr_records, source_url=source_url)
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
                    "rejection_reasons": _ocr_rejection_reasons(rows),
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
