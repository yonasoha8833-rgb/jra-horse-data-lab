"""Import JRA graded-race replay PDFs into a separate runner-level data file."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from urllib.request import Request, urlopen

from jra_replay_parser import extract_replay_pdf

CATALOG = Path("data/replay_catalog.json")
OUTPUT = Path("data/replay_results.json")
MAX_PDF_BYTES = 20 * 1024 * 1024


def read_limited_response(response, max_bytes: int = MAX_PDF_BYTES) -> bytes:
    """Read a response with a hard cap, including streams without Content-Length."""
    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            declared_length = int(length)
        except (TypeError, ValueError):
            raise ValueError("Invalid Content-Length header")
        if declared_length < 0:
            raise ValueError("Invalid negative Content-Length header")
        if declared_length > max_bytes:
            raise ValueError(f"PDF exceeds size limit ({max_bytes} bytes)")
    data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"PDF exceeds size limit ({max_bytes} bytes)")
    return data


def validate_replay_records(rows: list[dict]) -> list[dict]:
    """Reject malformed rows before a source is marked as successfully imported."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("No runner rows recognized in replay PDF")
    seen_horse_numbers = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Runner row {index} is not an object")
        raw_number = row.get("horse_number")
        raw_finish = row.get("finish")
        try:
            horse_number = int(raw_number)
            finish = int(raw_finish)
        except (TypeError, ValueError):
            raise ValueError(f"Runner row {index} has invalid horse number or placing")
        if str(raw_number).strip() != str(horse_number) or str(raw_finish).strip() != str(finish):
            raise ValueError(f"Runner row {index} has non-integer horse number or placing")
        if not 1 <= horse_number <= 18:
            raise ValueError(f"Runner row {index} has horse number outside 1-18")
        if horse_number in seen_horse_numbers:
            raise ValueError(f"Duplicate horse number {horse_number} in replay PDF")
        seen_horse_numbers.add(horse_number)
        if not 1 <= finish <= 18:
            raise ValueError(f"Runner row {index} has placing outside 1-18")
        if not isinstance(row.get("horse_name"), str) or not row["horse_name"].strip():
            raise ValueError(f"Runner row {index} has no horse name")
        last3f = row.get("individual_last3f")
        if last3f is not None:
            try:
                value = float(last3f)
            except (TypeError, ValueError):
                raise ValueError(f"Runner row {index} has invalid individual last 3F")
            if not math.isfinite(value):
                raise ValueError(f"Runner row {index} has non-finite individual last 3F")
            if not 20.0 <= value <= 60.0:
                raise ValueError(f"Runner row {index} has individual last 3F outside 20-60 seconds")
        corners = row.get("corner_positions")
        if corners is not None:
            if not isinstance(corners, str):
                raise ValueError(f"Runner row {index} has invalid corner positions")
            positions = corners.replace("－", "-").split("-")
            if not 2 <= len(positions) <= 4:
                raise ValueError(f"Runner row {index} has invalid corner position count")
            try:
                parsed_positions = [int(value.strip()) for value in positions]
            except ValueError:
                raise ValueError(f"Runner row {index} has non-integer corner positions")
            if any(not 1 <= value <= 18 for value in parsed_positions):
                raise ValueError(f"Runner row {index} has corner position outside 1-18")
    return rows


def deduplicate_records(rows: list[dict]) -> list[dict]:
    """Keep the most complete row per source PDF and horse number."""
    if not isinstance(rows, list):
        raise ValueError("Stored replay records must be a list")
    unique = {}
    unkeyed = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Stored replay record {index + 1} is not an object")
        source = str(row.get("source_url") or "").strip()
        number = row.get("horse_number")
        if not source or number is None or isinstance(number, bool):
            unkeyed.append(row)
            continue
        try:
            numeric_number = float(number)
        except (TypeError, ValueError):
            numeric_number = None
        if numeric_number is not None and math.isfinite(numeric_number) and numeric_number.is_integer():
            normalized_number = int(numeric_number)
        else:
            normalized_number = str(number).strip()
            if not normalized_number:
                unkeyed.append(row)
                continue
        key = (source, normalized_number)
        current = unique.get(key)
        score = sum(value is not None and value != "" for value in row.values())
        current_score = (sum(value is not None and value != "" for value in current.values())
                         if current is not None else -1)
        if score > current_score:
            unique[key] = row
    return list(unique.values()) + unkeyed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if not CATALOG.exists():
        raise SystemExit("Replay catalog is missing; run jra_replay_catalog.py first")

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    old = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    if not isinstance(old, dict):
        raise SystemExit("Existing replay results file must contain a JSON object")
    records = old.get("records", [])
    if not isinstance(records, list):
        raise SystemExit("Existing replay results records must be a list")
    processed_sources = old.get("processed_sources", [])
    if not isinstance(processed_sources, list) or any(
        not isinstance(url, str) or not url for url in processed_sources
    ):
        raise SystemExit("Existing processed_sources must be a list of non-empty URLs")
    processed = set(processed_sources)
    failures = {
        item["url"]: item for item in old.get("import_failures", [])
        if isinstance(item, dict) and item.get("url")
    }
    candidates = [item for item in catalog.get("items", [])
                  if item.get("url") and item["url"] not in processed]
    candidates.sort(key=lambda item: (
        item["url"] in failures,
        failures.get(item["url"], {}).get("last_attempt_utc", ""),
    ))

    attempted = 0
    for item in candidates:
        if attempted >= args.limit:
            break
        attempted += 1
        url = item["url"]
        try:
            request = Request(url, headers={"User-Agent": "JRA-Horse-Data-Lab/1.0"})
            with urlopen(request, timeout=45) as response:
                pdf_bytes = read_limited_response(response)
            extracted = validate_replay_records(extract_replay_pdf(
                pdf_bytes,
                source_url=url,
                race_metadata=item.get("race_metadata") or {},
            ))
            records.extend(extracted)
            processed.add(url)
            failures.pop(url, None)
            print(f"Imported {len(extracted)} runner rows: {url}")
        except Exception as exc:
            previous = failures.get(url, {})
            failures[url] = {
                "url": url,
                "attempts": previous.get("attempts", 0) + 1,
                "last_attempt_utc": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"ERROR: {url}: {exc}")

    unique = deduplicate_records(records)
    result = {
        "source": "JRA official graded-race result PDFs",
        "processed_sources": sorted(processed),
        "import_failures": sorted(failures.values(), key=lambda item: item["url"]),
        "record_count": len(unique),
        "records": unique,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Individual last-3F values are taken only from each horse's official 上り（600メートル） field.",
            "This dataset is kept separate from annual full-results imports until event-level matching is verified.",
            "PDF downloads are limited to 20 MiB and runner rows are validated before a source is marked processed.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(unique)} runner records; processed PDFs: {len(processed)}")


if __name__ == "__main__":
    main()
