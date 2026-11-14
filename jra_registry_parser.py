"""Conservative parser for OCR text from JRA horse registry PDFs.

Only accepts a horse name and a JRA-style registration number on the same
OCR line. Multi-line or ambiguous associations are deliberately not guessed.
"""
from __future__ import annotations

import re
from typing import Any

REGISTRATION_RE = re.compile(r"(?<![0-9])([0-9]{4}-[0-9]{5})(?![0-9])")


def parse_registry_text(text: str) -> list[dict[str, Any]]:
    """Extract unambiguous name/registration pairs from OCR text lines."""
    results: list[dict[str, Any]] = []
    candidates: list[dict[str, str]] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        matches = list(REGISTRATION_RE.finditer(line))
        if len(matches) != 1:
            continue
        match = matches[0]
        before = line[:match.start()].strip(" \t・,，:：()（）")
        after = line[match.end():].strip(" \t・,，:：()（）")
        # Reject lines with text on both sides: these may contain pedigree,
        # owner, or other columns that cannot safely be assigned to the name.
        if bool(before) == bool(after):
            continue
        name = before if before else after
        name = re.sub(r"\s+", "", name)
        if not name or len(name) > 30 or not re.search(r"[ぁ-んァ-ヶ一-龠]", name):
            continue
        number = match.group(1)
        candidates.append({"horse_name": name, "horse_registration_number": number})
    number_counts: dict[str, int] = {}
    for row in candidates:
        number = row["horse_registration_number"]
        number_counts[number] = number_counts.get(number, 0) + 1
    return [row for row in candidates if number_counts[row["horse_registration_number"]] == 1]


def build_unique_name_index(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Index only names that map to exactly one registration number."""
    candidates: dict[str, set[str]] = {}
    for row in rows:
        name = str(row.get("horse_name") or "").strip()
        number = str(row.get("horse_registration_number") or "").strip()
        if name and REGISTRATION_RE.fullmatch(number):
            candidates.setdefault(name, set()).add(number)
    return {
        name: next(iter(numbers))
        for name, numbers in candidates.items()
        if len(numbers) == 1
    }
