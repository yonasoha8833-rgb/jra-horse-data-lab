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


def assess_source_completeness(records: list[dict], expected_race_numbers: list[int] | None = None) -> dict:
    """Conservatively validate race coverage and runner numbering for one PDF."""
    if not records:
        return {"status": "incomplete", "reasons": ["no_runner_records"],
                "race_numbers": [], "race_count": 0}
    races: dict[int, list[dict]] = {}
    for row in records:
        number = row.get("race_number")
        if not isinstance(number, int) or not 1 <= number <= 12:
            return {"status": "incomplete", "reasons": ["invalid_race_number"],
                    "race_numbers": [], "race_count": 0}
        races.setdefault(number, []).append(row)

    reasons: list[str] = []
    for race_number, rows in races.items():
        numbers = [row.get("horse_number") for row in rows]
        if any(not isinstance(n, int) or n < 1 for n in numbers):
            reasons.append(f"race_{race_number}_missing_horse_number")
            continue
        if len(set(numbers)) != len(numbers):
            reasons.append(f"race_{race_number}_duplicate_horse_number")
            continue
        sizes = {row.get("declared_field_size") for row in rows
                 if isinstance(row.get("declared_field_size"), int)
                 and row.get("declared_field_size") > 0}
        if len(sizes) > 1:
            reasons.append(f"race_{race_number}_conflicting_declared_field_size")
        elif sizes:
            size = next(iter(sizes))
            if len(rows) != size or sorted(numbers) != list(range(1, size + 1)):
                reasons.append(f"race_{race_number}_field_size_or_number_mismatch")

    observed = sorted(races)
    if expected_race_numbers is None:
        if not reasons:
            reasons.append("official_race_inventory_not_available")
            status = "unverified"
        else:
            status = "incomplete"
    else:
        expected = sorted(set(expected_race_numbers))
        if observed != expected:
            reasons.append("race_inventory_mismatch")
        status = "incomplete" if reasons else "complete"
    return {"status": status, "reasons": reasons,
            "race_numbers": observed, "race_count": len(observed)}


def deduplicate_records(records: list[dict]) -> list[dict]:
    """Keep the more complete row when duplicate source/race/horse keys occur."""
    unique: dict[tuple, dict] = {}
    for index, row in enumerate(records):
        horse_key = row.get("horse_number")
        if horse_key is None:
            horse_key = ("unknown", row.get("horse_name"), row.get("frame"), index)
        key = (row.get("source_url"), row.get("date"), row.get("race_number"), horse_key)
        warnings = row.get("runner_validation_warnings")
        race_warnings = row.get("race_validation_warnings")
        quality = (
            -(len(warnings) if isinstance(warnings, list) else 0)
            -(len(race_warnings) if isinstance(race_warnings, list) else 0),
            sum(value not in (None, "", []) for value in row.values()),
        )
        old = unique.get(key)
        if old is None:
            unique[key] = row
            continue
        old_w = old.get("runner_validation_warnings")
        old_rw = old.get("race_validation_warnings")
        old_quality = (
            -(len(old_w) if isinstance(old_w, list) else 0)
            -(len(old_rw) if isinstance(old_rw, list) else 0),
            sum(value not in (None, "", []) for value in old.values()),
        )
        if quality > old_quality:
            unique[key] = row
    return list(unique.values())


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
    records = sanitize_existing_records(old.get("records", []))
    processed = set(old.get("processed_sources", []))
    failures_by_url = {
        item["url"]: item for item in old.get("import_failures", [])
        if isinstance(item, dict) and item.get("url")
    }
    attempted = 0

    # Do not let one persistently broken early catalog entry block every later PDF.
    # Process never-attempted sources first, then retry failures oldest-first.
    # Merge duplicate catalog entries before applying the per-run limit.
    catalog_by_url = {}
    for item in catalog.get("items", []):
        url = item.get("url")
        if not url:
            continue
        if url not in catalog_by_url:
            catalog_by_url[url] = dict(item)
        else:
            expected = set(catalog_by_url[url].get("expected_race_numbers", []))
            expected.update(item.get("expected_race_numbers", []))
            if expected:
                catalog_by_url[url]["expected_race_numbers"] = sorted(expected)
    catalog_items = [item for url, item in catalog_by_url.items() if url not in processed]
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
            assessment = assess_source_completeness(
                extracted, item.get("expected_race_numbers")
            )
            if extracted:
                records.extend(extracted)
            if assessment["status"] == "complete":
                processed.add(url)
                failures_by_url.pop(url, None)
                print(f"Imported complete source ({len(extracted)} rows): {url}")
            else:
                previous = failures_by_url.get(url, {})
                message = f"Source {assessment['status']}: " + ", ".join(assessment["reasons"])
                failures_by_url[url] = {
                    "url": url,
                    "attempts": previous.get("attempts", 0) + 1,
                    "last_attempt_utc": datetime.now(timezone.utc).isoformat(),
                    "error": message,
                    "assessment": assessment,
                }
                print(f"WARNING: {message}: {url}")
        except Exception as exc:
            previous = failures_by_url.get(url, {})
            failures_by_url[url] = {
                "url": url,
                "attempts": previous.get("attempts", 0) + 1,
                "last_attempt_utc": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"ERROR: {url}: {exc}")

    # Keep the best available row; never overwrite it with a poorer duplicate.
    unique = deduplicate_records(records)

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
            "individual_last3f is intentionally null unless a source provides it",
            "sources without an official expected_race_numbers inventory remain unverified and retryable"
        ]
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
