"""One-shot live smoke test against a stable JRA official result page."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jra_html_parser import fetch_and_parse_result  # noqa: E402

SOURCE_URL = (
    "https://www.jra.go.jp/JRADB/accessS.html?"
    "CNAME=pw01sde1008202605040720261115%2FD3"
)


def main() -> None:
    records = fetch_and_parse_result(SOURCE_URL)
    if len(records) != 18:
        raise SystemExit(f"Expected 18 runner rows, got {len(records)}")

    by_number = {row["horse_number"]: row for row in records}
    if set(by_number) != set(range(1, 19)):
        raise SystemExit(
            "Horse-number coverage mismatch: "
            + json.dumps(sorted(by_number), ensure_ascii=False)
        )

    winner = by_number[6]
    if winner["finish"] != 1 or winner["individual_last3f"] != 34.2:
        raise SystemExit(
            "Winner row mismatch: "
            + json.dumps(winner, ensure_ascii=False, sort_keys=True)
        )
    if winner["finish_time_seconds"] != 94.4:
        raise SystemExit(f"Winner finish-time mismatch: {winner['finish_time_seconds']}")
    if winner["corner_positions"] != "6-6":
        raise SystemExit(f"Winner corner positions mismatch: {winner['corner_positions']}")

    if by_number[17]["finish"] != 2 or by_number[17]["individual_last3f"] != 34.3:
        raise SystemExit("Second-place runner row mismatch")
    if not by_number[15]["blinkers"]:
        raise SystemExit("Official blinker marker was not extracted for horse 15")

    race = records[0]
    if (
        race["date"] != "2026-11-15"
        or race["track"] != "京都"
        or race["race_number"] != 7
        or race["surface"] != "芝"
        or race["distance"] != 1600
        or race["going"] != "重"
    ):
        raise SystemExit(
            "Race metadata mismatch: "
            + json.dumps({key: race.get(key) for key in (
                "date", "track", "race_number", "surface", "distance", "going"
            )}, ensure_ascii=False, sort_keys=True)
        )

    if race["race_last3f"] != 34.5 or race["race_last4f"] != 46.7:
        raise SystemExit("Race-level closing time mismatch")
    if race["race_lap_times"] != [12.5, 11.2, 11.7, 12.3, 12.2, 11.8, 11.1, 11.6]:
        raise SystemExit("Race lap-time sequence mismatch")
    if any(row["field_size"] != 18 for row in records):
        raise SystemExit("Complete-field validation did not accept all 18 runners")
    if any(row["race_validation_warnings"] for row in records):
        raise SystemExit("Unexpected race validation warnings")

    print(json.dumps({
        "status": "success",
        "source_url": SOURCE_URL,
        "date": race["date"],
        "track": race["track"],
        "race_number": race["race_number"],
        "runner_records": len(records),
        "horse_numbers": sorted(by_number),
        "winner": {
            "horse_number": winner["horse_number"],
            "horse_name": winner["horse_name"],
            "finish": winner["finish"],
            "individual_last3f": winner["individual_last3f"],
            "corner_positions": winner["corner_positions"],
        },
        "race_last3f": race["race_last3f"],
        "race_last4f": race["race_last4f"],
        "lap_count": len(race["race_lap_times"]),
        "blinker_marker_horse_15": by_number[15]["blinkers"],
        "field_size": race["field_size"],
        "data_files_modified": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
