#!/usr/bin/env python3
"""First-pass structured extraction from the spatial JRA PDF diagnostic.

This is deliberately conservative: every extracted horse row retains its raw
source text and a confidence flag. It is a development parser, not an
authoritative results feed.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

TIME_RE = re.compile(r"(?P<time>\d{1,2}[:：]\d{2}[.．]\d)")
RACE_RE = re.compile(r"第\s*(\d+)\s*競走")
HORSE_START_RE = re.compile(r"(?m)(?:^|\n)\s*(\d{1,2})\s+(\d{1,2})\s*\n")


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value)
    return re.sub(r"[ \t]+", " ", value).strip()


def parse_horse_chunk(chunk: str, frame: str, horse_number: str) -> dict | None:
    raw = chunk.strip()
    compact = clean_text(raw).replace(" ", "").replace("\n", "")
    # Name is the text between the saddlecloth/horse number and sex/age.
    identity = re.match(r"^(.+?)(牡|牝|騸|せ)(\d{1,2})", compact)
    time_match = TIME_RE.search(compact)
    if not identity or not time_match:
        return None
    name = identity.group(1)
    # Avoid treating malformed or very long merged blocks as one horse.
    if not name or len(name) > 24:
        return None
    time_value = time_match.group("time").replace("：", ":").replace("．", ".")
    return {
        "frame": int(frame),
        "horse_number": int(horse_number),
        "horse_name": name,
        "sex": identity.group(2),
        "age": int(identity.group(3)),
        "finish_time": time_value,
        "raw_text": raw,
        "confidence": "review",
    }


def extract(layout: dict) -> dict:
    races: list[dict] = []
    for page in layout.get("pages", []):
        current_race = None
        for block in page.get("blocks", []):
            text = block.get("text", "")
            race_match = RACE_RE.search(text)
            if race_match:
                current_race = {
                    "race_number": int(race_match.group(1)),
                    "page": page.get("page_number"),
                    "column": block.get("column"),
                    "horses": [],
                }
                races.append(current_race)
            if current_race is None:
                continue
            # Each horse entry generally begins with frame and horse number on
            # separate PDF lines. Keep the source block intact for auditability.
            starts = list(HORSE_START_RE.finditer(text))
            for index, match in enumerate(starts):
                end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
                chunk = text[match.end():end]
                horse = parse_horse_chunk(chunk, match.group(1), match.group(2))
                if horse:
                    current_race["horses"].append(horse)

    # Deduplicate rows repeated across overlapping PDF text blocks.
    for race in races:
        unique = {}
        for horse in race["horses"]:
            key = (horse["horse_number"], horse["horse_name"], horse["finish_time"])
            unique[key] = horse
        race["horses"] = sorted(unique.values(), key=lambda x: x["horse_number"])
        race["horse_count_extracted"] = len(race["horses"])
    return {
        "source": layout.get("source_pdf"),
        "page_count": layout.get("page_count"),
        "parser_version": "0.1.0",
        "warning": "First-pass extraction. Verify against the official PDF before analytical or betting use.",
        "races": races,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/pdf_layout.json")
    parser.add_argument("--output", default="data/race_results.json")
    args = parser.parse_args()
    source = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = extract(source)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    race_count = len(result["races"])
    horse_count = sum(r["horse_count_extracted"] for r in result["races"])
    print(f"Races detected: {race_count}; horse rows extracted: {horse_count}")
    print(f"Saved first-pass results to {output}")


if __name__ == "__main__":
    main()
