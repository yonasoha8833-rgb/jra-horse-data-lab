"""Conservative parser for OCR text from JRA horse registry PDFs.

Only accepts a horse name and a JRA-style registration number on the same
OCR line. Multi-line or ambiguous associations are deliberately not guessed.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

REGISTRATION_RE = re.compile(r"(?<![0-9])([0-9]{4}-[0-9]{5})(?![0-9])")
AMBIGUOUS_REGISTRATION_RE = re.compile(r"(?<![A-Za-z0-9])[0-9OIl|]{4}[-－ー][0-9OIl|]{5}(?![A-Za-z0-9])")
AMBIGUOUS_OCR_CHAR_RE = re.compile(r"[OIl|]")
PREFIX_ANNOTATIONS = (
    "市場取引馬",
    "外",
    "（地）",
    "(地)",
)
NAME_ANNOTATIONS = (
    "（初出走はJRA）",
    "(初出走はJRA)",
    "（初出走JRA）",
    "(初出走JRA)",
    "（初出走 JRA）",
    "(初出走 JRA)",
)


def _clean_name(value: str) -> str:
    name = "".join(value.split())
    changed = True
    while changed:
        changed = False
        for annotation in PREFIX_ANNOTATIONS:
            if name.startswith(annotation):
                name = name[len(annotation):]
                changed = True
        for annotation in NAME_ANNOTATIONS:
            if annotation in name:
                name = name.replace(annotation, "")
                changed = True
    return "".join(name.split())


def parse_registry_text(text: str) -> list[dict[str, Any]]:
    """Extract unambiguous name/registration pairs from OCR text lines."""
    candidates: list[dict[str, str]] = []
    for raw_line in text.splitlines():
        line = " ".join(unicodedata.normalize("NFKC", raw_line).split())
        matches = list(REGISTRATION_RE.finditer(line))
        if len(matches) != 1:
            continue
        match = matches[0]
        before = line[:match.start()].strip(" ・,，:：")
        after = line[match.end():].strip(" ・,，:：")
        if bool(before) == bool(after):
            continue
        name = _clean_name(before if before else after)
        if not name or len(name) > 30 or not re.search(r"[ぁ-んァ-ヶ一-龠]", name):
            continue
        candidates.append({"horse_name": name, "horse_registration_number": match.group(1)})
    number_counts: dict[str, int] = {}
    for row in candidates:
        number = row["horse_registration_number"]
        number_counts[number] = number_counts.get(number, 0) + 1
    return [row for row in candidates if number_counts[row["horse_registration_number"]] == 1]



def find_ambiguous_registration_lines(text: str) -> list[dict[str, Any]]:
    """Return OCR lines with likely digit/letter confusions for manual review.

    This function never corrects OCR characters or emits registration IDs.
    """
    findings = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        normalized = unicodedata.normalize("NFKC", raw_line)
        match = AMBIGUOUS_REGISTRATION_RE.search(normalized)
        if match and AMBIGUOUS_OCR_CHAR_RE.search(match.group(0)):
            findings.append({
                "line_number": line_number,
                "raw_text": raw_line.strip(),
                "review_reason": "registration number may contain O/I/l/| OCR confusion",
            })
    return findings


def build_unique_name_index(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Index only names that map to exactly one registration number."""
    candidates: dict[str, set[str]] = {}
    for row in rows:
        name = _clean_name(str(row.get("horse_name") or "").strip())
        number = str(row.get("horse_registration_number") or "").strip()
        if name and REGISTRATION_RE.fullmatch(number):
            candidates.setdefault(name, set()).add(number)
    return {
        name: next(iter(numbers))
        for name, numbers in candidates.items()
        if len(numbers) == 1
    }
