"""Import a bounded number of PDFs from data/catalog.json into race_results.json."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jra_pdf_parser import download_and_extract

CATALOG = Path("data/catalog.json")
OUTPUT = Path("data/race_results.json")


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
    new_sources = []

    for item in catalog.get("items", []):
        url = item.get("url")
        if not url or url in processed:
            continue
        try:
            extracted = download_and_extract(url)
            if not extracted:
                print(f"WARNING: no runner rows recognized: {url}")
                processed.add(url)
                new_sources.append(url)
                continue
            records.extend(extracted)
            processed.add(url)
            new_sources.append(url)
            print(f"Imported {len(extracted)} runner rows from {url}")
        except Exception as exc:
            print(f"ERROR: {url}: {exc}")
        if len(new_sources) >= args.limit:
            break

    # Stable deduplication protects against repeated imports.
    unique = {}
    for row in records:
        key = (row.get("source_url"), row.get("race_number"), row.get("horse_number"))
        unique[key] = row

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "source": "JRA official result PDFs listed in data/catalog.json",
        "processed_sources": sorted(processed),
        "record_count": len(unique),
        "records": list(unique.values()),
        "notes": [
            "finish is based on the order of recognized official result rows",
            "race_last3f is the race-level closing 3F, not an individual horse figure",
            "individual_last3f is intentionally null unless a source provides it"
        ]
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
