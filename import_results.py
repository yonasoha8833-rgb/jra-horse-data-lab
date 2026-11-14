"""Import a bounded number of PDFs from data/catalog.json into race_results.json."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from jra_pdf_parser import _complete_ocr_records, download_and_extract

CATALOG = Path("data/catalog.json")
OUTPUT = Path("data/race_results.json")


def sanitize_existing_records(records: list[dict]) -> list[dict]:
    """Revalidate legacy OCR rows before retaining them in the cumulative dataset."""
    ordinary = [
        row for row in records
        if row.get("extraction_status") != "ocr_fallback_basic_fields"
    ]
    legacy_ocr = [
        row for row in records
        if row.get("extraction_status") == "ocr_fallback_basic_fields"
    ]
    return ordinary + _complete_ocr_records(legacy_ocr)


def build_quality_report(records: list[dict]) -> dict:
    """Summarize extraction completeness and race-level consistency warnings."""
    races: dict[tuple, list[dict]] = {}
    for row in records:
        key = (row.get("source_url"), row.get("race_number"), row.get("date"))
        races.setdefault(key, []).append(row)

    warning_races = 0
    duplicate_number_races = 0
    field_size_mismatch_races = 0
    for rows in races.values():
        warnings = {
            warning
            for row in rows
            for warning in (
                row.get("race_validation_warnings")
                if isinstance(row.get("race_validation_warnings"), list)
                else []
            )
            if isinstance(warning, str)
        }
        if warnings:
            warning_races += 1
        if any(warning.startswith("duplicate_horse_number:") for warning in warnings):
            duplicate_number_races += 1
        if any(warning.startswith("declared_field_size_mismatch:") for warning in warnings):
            field_size_mismatch_races += 1

    return {
        "race_count": len(races),
        "warning_race_count": warning_races,
        "duplicate_number_race_count": duplicate_number_races,
        "field_size_mismatch_race_count": field_size_mismatch_races,
        "records_missing_date": sum(1 for row in records if not row.get("date")),
        "records_missing_track": sum(1 for row in records if not row.get("track")),
        "records_missing_distance": sum(1 for row in records if row.get("distance") is None),
        "records_missing_going": sum(1 for row in records if not row.get("going")),
        "records_missing_finish_time": sum(1 for row in records if row.get("finish_time_seconds") is None),
        "records_with_invalid_finish_time": sum(
            1 for row in records
            if isinstance(row.get("runner_validation_warnings"), list)
            and "invalid_finish_time_seconds" in row["runner_validation_warnings"]
        ),
        "records_with_individual_last3f": sum(
            1 for row in records if row.get("individual_last3f") is not None
        ),
        "records_missing_individual_last3f": sum(
            1 for row in records if row.get("individual_last3f") is None
        ),
        "records_with_horse_registration_number": sum(
            1 for row in records if str(row.get("horse_registration_number") or "").strip()
        ),
        "records_missing_horse_registration_number": sum(
            1 for row in records if not str(row.get("horse_registration_number") or "").strip()
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1,
                        help="maximum PDFs to process in this run (default: 1)")
    args = parser.parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    old = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {"records": []}
    records = old.get("records", [])
    processed = set(old.get("processed_sources", []))
    failures_by_url = {
        item["url"]: item for item in old.get("import_failures", [])
        if isinstance(item, dict) and item.get("url")
    }
    attempted = 0

    # Do not let one persistently broken early catalog entry block every later PDF.
    # Process never-attempted sources first, then retry failures oldest-first.
    catalog_items = [item for item in catalog.get("items", []) if item.get("url") and item.get("url") not in processed]
    catalog_items.sort(key=lambda item: (
        item["url"] in failures_by_url,
        failures_by_url.get(item["url"], {}).get("last_attempt_utc", ""),
    ))

    for item in catalog_items:
        url = item.get("url")
        if attempted >= args.limit:
            break
        attempted += 1
        try:
            extracted = download_and_extract(url)
            if not extracted:
                message = "No runner rows recognized; source remains eligible for retry"
                previous = failures_by_url.get(url, {})
                failures_by_url[url] = {
                    "url": url,
                    "attempts": previous.get("attempts", 0) + 1,
                    "last_attempt_utc": datetime.now(timezone.utc).isoformat(),
                    "error": message,
                }
                print(f"WARNING: {message}: {url}")
                continue
            records.extend(extracted)
            processed.add(url)
            failures_by_url.pop(url, None)
            print(f"Imported {len(extracted)} runner rows from {url}")
        except Exception as exc:
            previous = failures_by_url.get(url, {})
            failures_by_url[url] = {
                "url": url,
                "attempts": previous.get("attempts", 0) + 1,
                "last_attempt_utc": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"ERROR: {url}: {exc}")

    # Stable deduplication protects against repeated imports.
    unique = {}
    for row in records:
        key = (row.get("source_url"), row.get("race_number"), row.get("horse_number"))
        unique[key] = row

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    quality_report = build_quality_report(list(unique.values()))
    OUTPUT.write_text(json.dumps({
        "source": "JRA official result PDFs listed in data/catalog.json",
        "processed_sources": sorted(processed),
        "import_failures": sorted(failures_by_url.values(), key=lambda item: item["url"]),
        "last_import_attempted": attempted,
        "record_count": len(unique),
        "quality_report": quality_report,
        "records": list(unique.values()),
        "notes": [
            "finish is based on the order of recognized official result rows",
            "race_last3f is the race-level closing 3F, not an individual horse figure",
            "individual_last3f is intentionally null unless a source provides it"
        ]
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
