"""Horse-level statistics for normalized JRA race result records.

This module deliberately does not guess missing PDF fields. The PDF extraction
layer should provide one dictionary per runner using the documented keys.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Iterable


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize_horses(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate normalized race rows by horse name.

    Expected optional keys: horse_name, date, track, surface, distance,
    going, class, finish, finish_time_seconds, field_size, last3f,
    corner_positions, jockey.
    Missing or unparseable values are excluded from numeric averages.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        name = str(row.get("horse_name") or "").strip()
        if name:
            grouped[name].append(row)

    summaries = []
    for name, rows in sorted(grouped.items()):
        finishes = [_number(r.get("finish")) for r in rows]
        finishes = [v for v in finishes if v is not None and v > 0]
        times = [_number(r.get("finish_time_seconds", r.get("time_seconds"))) for r in rows]
        times = [v for v in times if v is not None and v > 0]
        last3fs = [_number(r.get("individual_last3f", r.get("last3f"))) for r in rows]
        last3fs = [v for v in last3fs if v is not None and v > 0]
        summaries.append({
            "horse_name": name,
            "starts": len(rows),
            "placed_starts": sum(1 for r in rows if _number(r.get("finish")) is not None and _number(r.get("finish")) > 0),
            "unplaced_starts": sum(1 for r in rows if _number(r.get("finish")) is None or _number(r.get("finish")) <= 0),
            "result_status_counts": {
                status: sum(1 for r in rows if r.get("result_status") == status)
                for status in sorted({str(r.get("result_status")) for r in rows if r.get("result_status")})
            },
            "wins": sum(1 for r in rows if _number(r.get("finish")) == 1),
            "top3": sum(1 for r in rows if (_number(r.get("finish")) or 0) in (1, 2, 3)),
            "win_rate": round(sum(1 for r in rows if _number(r.get("finish")) == 1) / len(rows), 4),
            "top3_rate": round(sum(1 for r in rows if (_number(r.get("finish")) or 0) in (1, 2, 3)) / len(rows), 4),
            "average_finish": round(mean(finishes), 2) if finishes else None,
            "performance_score": performance_score(rows),
            "average_time_seconds": round(mean(times), 3) if times else None,
            "average_last3f": round(mean(last3fs), 2) if last3fs else None,
            "records": sorted(rows, key=lambda r: str(r.get("date") or ""), reverse=True),
        })
    return summaries


def filter_records(
    records: Iterable[dict[str, Any]],
    *,
    horse_name: str | None = None,
    track: str | None = None,
    surface: str | None = None,
    distance: int | None = None,
) -> list[dict[str, Any]]:
    """Filter race rows without treating absent values as matches."""
    result = []
    for row in records:
        if horse_name and horse_name.casefold() not in str(row.get("horse_name") or "").casefold():
            continue
        if track and track != row.get("track"):
            continue
        if surface and surface != row.get("surface"):
            continue
        if distance is not None and _number(row.get("distance")) != distance:
            continue
        result.append(row)
    return result



def performance_score(records: Iterable[dict[str, Any]]) -> float | None:
    """Return a transparent 0-100 average placing score.

    Each valid finish is normalized against that race's field size:
    winner=100, last=0. The latest five valid starts receive greater weight.
    This is a descriptive result index, not a predictive probability or
    speed figure; rows without a valid field_size/finish are excluded.
    """
    valid = []
    for row in records:
        finish = _number(row.get("finish"))
        field_size = _number(row.get("field_size"))
        if finish is None or field_size is None or field_size <= 1:
            continue
        if finish < 1 or finish > field_size:
            continue
        score = 100.0 * (field_size - finish) / (field_size - 1)
        valid.append((str(row.get("date") or ""), score))
    valid.sort(key=lambda item: item[0], reverse=True)
    recent = valid[:5]
    if not recent:
        return None
    weights = list(range(len(recent), 0, -1))
    return round(sum(score * weight for (_, score), weight in zip(recent, weights)) / sum(weights), 1)
