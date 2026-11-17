"""One-shot live smoke test against a stable JRA official result page."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jra_html_parser import (  # noqa: E402
    _TableParser,
    fetch_result_html,
    parse_result_html,
)

SOURCE_URL = (
    "https://www.jra.go.jp/JRADB/accessS.html?"
    "CNAME=pw01sde1008202605040720261115%2FD3"
)


def _diagnostics(html: str, parser: _TableParser) -> dict:
    return {
        "html_bytes": len(html.encode("utf-8")),
        "table_count": len(parser.tables),
        "tables": [
            {
                "row_count": len(rows),
                "sample_rows": [
                    [
                        {
                            "text": " ".join(cell.text).strip(),
                            "alt": cell.alt,
                        }
                        for cell in row
                    ]
                    for row in rows[:4]
                ],
            }
            for rows in parser.tables[:8]
        ],
        "text_sample": " ".join(parser.text_parts)[:1800],
        "html_prefix": html[:1200],
    }


def main() -> None:
    html = fetch_result_html(SOURCE_URL)
    dom = _TableParser()
    dom.feed(html)
    records = parse_result_html(html, source_url=SOURCE_URL)

    if len(records) != 18:
        raise SystemExit(
            "Expected 18 runner rows, got "
            + str(len(records))
            + "\nDOM diagnostics: "
            + json.dumps(_diagnostics(html, dom), ensure_ascii=False)
        )

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


TOKYO_SOURCE_URL = (
    "https://www.jra.go.jp/JRADB/accessS.html?"
    "CNAME=pw01sde0105202605040720261115%2F16"
)


def validate_tokyo_page() -> None:
    """Validate a second live page: Tokyo dirt 1400m, 14 runners."""
    html = fetch_result_html(TOKYO_SOURCE_URL)
    dom = _TableParser()
    dom.feed(html)
    records = parse_result_html(html, source_url=TOKYO_SOURCE_URL)

    if len(records) != 14:
        raise SystemExit(
            "Tokyo: expected 14 runner rows, got "
            + str(len(records))
            + "\\nDOM diagnostics: "
            + json.dumps(_diagnostics(html, dom), ensure_ascii=False)
        )

    by_number = {row["horse_number"]: row for row in records}
    if set(by_number) != set(range(1, 15)):
        raise SystemExit(
            "Tokyo: horse-number coverage mismatch: "
            + json.dumps(sorted(by_number), ensure_ascii=False)
        )

    winner = by_number[7]
    if (
        winner["finish"] != 1
        or winner["horse_name"] != "バルダンツァ"
        or winner["finish_time_seconds"] != 83.8
        or winner["individual_last3f"] != 35.8
        or winner["corner_positions"] != "10-10"
    ):
        raise SystemExit(
            "Tokyo: winner mismatch: "
            + json.dumps(winner, ensure_ascii=False, sort_keys=True)
        )

    runner_up = by_number[8]
    if (
        runner_up["finish"] != 2
        or runner_up["individual_last3f"] != 36.7
        or runner_up["corner_positions"] != "4-4"
        or not runner_up["blinkers"]
    ):
        raise SystemExit(
            "Tokyo: second-place runner mismatch: "
            + json.dumps(runner_up, ensure_ascii=False, sort_keys=True)
        )

    race = records[0]
    if (
        race["date"] != "2026-11-15"
        or race["track"] != "東京"
        or race["race_number"] != 7
        or race["surface"] != "ダート"
        or race["distance"] != 1400
        or race["going"] != "良"
    ):
        raise SystemExit(
            "Tokyo: race metadata mismatch: "
            + json.dumps({key: race.get(key) for key in (
                "date", "track", "race_number", "surface", "distance", "going"
            )}, ensure_ascii=False, sort_keys=True)
        )

    if race["race_last4f"] != 49.0 or race["race_last3f"] != 36.9:
        raise SystemExit("Tokyo: race-level closing time mismatch")
    if race["race_lap_times"] != [12.4, 10.8, 11.6, 12.1, 12.3, 12.4, 12.2]:
        raise SystemExit("Tokyo: race lap-time sequence mismatch")
    if any(row["field_size"] != 14 for row in records):
        raise SystemExit("Tokyo: complete-field validation did not accept all 14 runners")
    if any(row["race_validation_warnings"] for row in records):
        raise SystemExit("Tokyo: unexpected race validation warnings")

    print(json.dumps({
        "status": "success",
        "source_url": TOKYO_SOURCE_URL,
        "date": race["date"],
        "track": race["track"],
        "race_number": race["race_number"],
        "surface": race["surface"],
        "distance": race["distance"],
        "going": race["going"],
        "runner_records": len(records),
        "horse_numbers": sorted(by_number),
        "winner": {
            "horse_number": winner["horse_number"],
            "horse_name": winner["horse_name"],
            "finish_time_seconds": winner["finish_time_seconds"],
            "individual_last3f": winner["individual_last3f"],
            "corner_positions": winner["corner_positions"],
        },
        "runner_up_blinkers": runner_up["blinkers"],
        "race_last3f": race["race_last3f"],
        "race_last4f": race["race_last4f"],
        "lap_count": len(race["race_lap_times"]),
        "field_size": race["field_size"],
        "data_files_modified": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
    validate_tokyo_page()
