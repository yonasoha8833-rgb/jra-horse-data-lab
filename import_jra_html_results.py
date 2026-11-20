"""Import JRA official HTML results into a separate, non-merging sidecar."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any
from jra_html_parser import fetch_and_parse_result

CATALOG = Path("data/jra_html_catalog.json")
OUTPUT = Path("data/jra_html_results.json")
PDF_INPUT = Path("data/race_results.json")
COMPARISON_OUTPUT = Path("data/jra_html_pdf_comparison.json")


def _race_key(row: dict[str, Any]) -> tuple:
    return row.get("date"), row.get("track"), row.get("race_number")


def _runner_key(row: dict[str, Any]) -> tuple:
    return (*_race_key(row), row.get("horse_number"))


def validate_complete_races(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Accept only complete, warning-free race tables."""
    groups: dict[tuple, list[dict]] = {}
    for row in records:
        groups.setdefault(_race_key(row), []).append(row)
    accepted, rejected = [], []
    for key, rows in groups.items():
        numbers = [row.get("horse_number") for row in rows]
        warnings = [
            warning for row in rows
            for warning in (row.get("race_validation_warnings")
                            if isinstance(row.get("race_validation_warnings"), list) else [])
            if isinstance(warning, str)
        ]
        # A placed runner must have a plausible, independently parsed time.
        # Non-finish statuses may legitimately have no finish time.
        invalid_times = any(
            row.get("finish") is not None
            and (
                row.get("finish_time") is None
                or row.get("finish_time_seconds") is None
                or row.get("finish_time_seconds") > 15 * 60
            )
            for row in rows
        )
        valid = (
            all(value not in (None, "") for value in key)
            and all(bool(str(row.get("horse_name") or "").strip()) for row in rows)
            and all(isinstance(n, int) and 1 <= n <= 18 for n in numbers)
            and len(numbers) == len(set(numbers))
            and all(row.get("field_size") == len(rows) for row in rows)
            and not warnings
            and not invalid_times
        )
        if valid:
            accepted.extend(rows)
        else:
            rejected.append({
                "date": key[0], "track": key[1], "race_number": key[2],
                "runner_rows": len(rows), "reason": "incomplete_or_ambiguous_official_table",
                "warnings": sorted(set(warnings)),
            })
    return accepted, rejected


def compare_sources(html_records: list[dict], pdf_records: list[dict]) -> dict:
    """Exact identity comparison only; never infer, fuzzy-match, or merge."""
    pdf_index: dict[tuple, list[dict]] = {}
    for row in pdf_records:
        key = _runner_key(row)
        if all(value not in (None, "") for value in key):
            pdf_index.setdefault(key, []).append(row)
    matched, conflicts, html_only = [], [], []
    html_keys = set()
    for row in html_records:
        key = _runner_key(row)
        html_keys.add(key)
        base = {"date": row.get("date"), "track": row.get("track"),
                "race_number": row.get("race_number"), "horse_number": row.get("horse_number")}
        if not all(value not in (None, "") for value in key):
            html_only.append({**base, "reason": "incomplete_html_identity"})
            continue
        candidates = pdf_index.get(key, [])
        if len(candidates) != 1:
            html_only.append({**base, "reason": "no_exact_pdf_match" if not candidates else "ambiguous_pdf_match"})
            continue
        pdf_row = candidates[0]
        item = {**base, "html_horse_name": row.get("horse_name"),
                "pdf_horse_name": pdf_row.get("horse_name")}
        if row.get("horse_name") == pdf_row.get("horse_name"):
            matched.append(item)
        else:
            conflicts.append({**item, "reason": "horse_name_mismatch"})
    pdf_only = [
        {"date": row.get("date"), "track": row.get("track"),
         "race_number": row.get("race_number"), "horse_number": row.get("horse_number")}
        for key, rows in pdf_index.items() if key not in html_keys for row in rows
    ]
    return {
        "matching_policy": "exact date + track + race_number + horse_number + horse_name",
        "matched_count": len(matched), "conflict_count": len(conflicts),
        "html_only_count": len(html_only), "pdf_only_count": len(pdf_only),
        "matched": matched, "conflicts": conflicts, "html_only": html_only,
        "pdf_only": pdf_only, "automatic_merge_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    previous = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    old_records = [r for r in previous.get("records", []) if isinstance(r, dict)]
    old_by_source: dict[str, list[dict]] = {}
    for row in old_records:
        source = row.get("source_url")
        if source:
            old_by_source.setdefault(source, []).append(row)
    # Re-fetch previously imported pages once when the parser gains a verified
    # official horse-profile identity field; retain only fully refreshed sources.
    profile_complete_sources = {
        source for source, rows in old_by_source.items()
        if rows and all(re.fullmatch(r"[A-Za-z0-9]+/[A-Za-z0-9]+",
                                     str(row.get("horse_profile_id") or "")) for row in rows)
    }
    processed = set(previous.get("processed_sources", [])) & profile_complete_sources
    failures = {x["url"]: x for x in previous.get("import_failures", [])
                if isinstance(x, dict) and x.get("url")}
    records = [r for r in old_records if r.get("source_url") in processed]
    attempted = 0
    for item in catalog.get("items", []):
        url = item.get("url")
        if not url or url in processed or attempted >= args.limit:
            continue
        attempted += 1
        try:
            fetched = fetch_and_parse_result(url)
            if not fetched:
                raise ValueError("No official runner table recognized")
            accepted, rejected = validate_complete_races(fetched)
            if rejected or len(accepted) != len(fetched):
                raise ValueError("Official table failed complete-field validation")
            records.extend(accepted)
            processed.add(url)
            failures.pop(url, None)
        except Exception as exc:
            old = failures.get(url, {})
            failures[url] = {
                "url": url, "attempts": old.get("attempts", 0) + 1,
                "last_attempt_utc": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
            }
    unique = {(r.get("source_url"), r.get("race_number"), r.get("horse_number")): r for r in records}
    records = list(unique.values())
    pdf = json.loads(PDF_INPUT.read_text(encoding="utf-8")) if PDF_INPUT.exists() else {}
    comparison = compare_sources(records, pdf.get("records", []))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "source": "JRA official result HTML pages",
        "processed_sources": sorted(processed),
        "import_failures": sorted(failures.values(), key=lambda x: x["url"]),
        "last_import_attempted": attempted, "record_count": len(records),
        "records": records,
        "notes": ["Stored separately from data/race_results.json",
                  "Only complete, warning-free tables are accepted",
                  "Missing values are never inferred"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    COMPARISON_OUTPUT.write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
    print(json.dumps({
        "html_records": len(records), "matched": comparison["matched_count"],
        "conflicts": comparison["conflict_count"], "html_only": comparison["html_only_count"],
        "pdf_only": comparison["pdf_only_count"], "automatic_merge_performed": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
