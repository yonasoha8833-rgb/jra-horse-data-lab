"""Import JRA graded-race replay PDFs into a separate runner-level data file."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.request import Request, urlopen

from jra_replay_parser import extract_replay_pdf

CATALOG = Path("data/replay_catalog.json")
OUTPUT = Path("data/replay_results.json")


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
    records = old.get("records", [])
    processed = set(old.get("processed_sources", []))
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
                pdf_bytes = response.read()
            extracted = extract_replay_pdf(
                pdf_bytes,
                source_url=url,
                race_metadata=item.get("race_metadata") or {},
            )
            if not extracted:
                raise ValueError("No runner rows recognized in replay PDF")
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

    unique = {}
    for row in records:
        key = (row.get("source_url"), row.get("horse_number"))
        unique[key] = row
    result = {
        "source": "JRA official graded-race result PDFs",
        "processed_sources": sorted(processed),
        "import_failures": sorted(failures.values(), key=lambda item: item["url"]),
        "record_count": len(unique),
        "records": list(unique.values()),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Individual last-3F values are taken only from each horse's official 上り（600メートル） field.",
            "This dataset is kept separate from annual full-results imports until event-level matching is verified.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(unique)} runner records; processed PDFs: {len(processed)}")


if __name__ == "__main__":
    main()
