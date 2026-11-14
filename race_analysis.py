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
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        name = str(row.get("horse_name") or "").strip()
        registration_number = str(row.get("horse_registration_number") or "").strip()
        if name:
            # Prefer an official registration identifier when a source provides it.
            # Name-only grouping remains provisional and is explicitly labeled.
            identity = ("registration", registration_number) if registration_number else ("name", name)
            grouped[identity].append(row)

    summaries = []
    for identity, rows in sorted(grouped.items()):
        name = str(rows[0].get("horse_name") or "").strip()
        registration_number = identity[1] if identity[0] == "registration" else None
        finishes = [_number(r.get("finish")) for r in rows]
        finishes = [v for v in finishes if v is not None and v > 0]
        times = [_number(r.get("finish_time_seconds", r.get("time_seconds"))) for r in rows]
        times = [v for v in times if v is not None and v > 0]
        last3fs = [_number(r.get("individual_last3f", r.get("last3f"))) for r in rows]
        last3fs = [v for v in last3fs if v is not None and v > 0]
        summaries.append({
            "horse_name": name,
            "horse_registration_number": registration_number,
            "identity_basis": "official_registration_number" if registration_number else "horse_name_only",
            "identity_verified": bool(registration_number),
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





def recent_form_summary(
    records: Iterable[dict[str, Any]],
    *,
    recent_count: int = 5,
) -> dict[str, Any]:
    """Summarize the latest results and compare the latest three with prior three.

    Only dated, valid placing records are used for the trend comparison.
    A lower average finishing position indicates improvement.
    """
    if recent_count < 1:
        raise ValueError("recent_count must be at least 1")
    dated = []
    for row in records:
        finish = _number(row.get("finish"))
        date_value = str(row.get("date") or "")
        if finish is not None and finish > 0 and date_value:
            dated.append((date_value, row))
    dated.sort(key=lambda item: item[0], reverse=True)
    latest = [row for _, row in dated[:recent_count]]
    placings = [float(_number(row.get("finish"))) for row in latest]
    latest_three = placings[:3]
    previous_three = [float(_number(row.get("finish"))) for _, row in dated[3:6]]
    latest_average = round(mean(latest_three), 2) if latest_three else None
    previous_average = round(mean(previous_three), 2) if len(previous_three) == 3 else None
    trend = None
    if latest_average is not None and previous_average is not None:
        difference = previous_average - latest_average
        trend = "improving" if difference > 0 else "declining" if difference < 0 else "stable"
    return {
        "recent_results": latest,
        "latest_three_average_finish": latest_average,
        "previous_three_average_finish": previous_average,
        "trend": trend,
    }

def summarize_conditions(
    records: Iterable[dict[str, Any]],
    field: str,
    *,
    min_starts: int = 3,
) -> list[dict[str, Any]]:
    """Summarize results by a condition field, excluding unknown values.

    Groups below min_starts are omitted to avoid presenting tiny samples as
    meaningful performance evidence. Rates are descriptive, not predictive.
    """
    if min_starts < 1:
        raise ValueError("min_starts must be at least 1")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        value = str(row.get(field) or "").strip()
        if value:
            grouped[value].append(row)
    summaries = []
    for condition, rows in grouped.items():
        if len(rows) < min_starts:
            continue
        wins = sum(1 for row in rows if _number(row.get("finish")) == 1)
        top3 = sum(1 for row in rows if _number(row.get("finish")) in (1, 2, 3))
        summaries.append({
            "condition": condition,
            "starts": len(rows),
            "wins": wins,
            "top3": top3,
            "win_rate": round(wins / len(rows), 4),
            "top3_rate": round(top3 / len(rows), 4),
        })
    return sorted(summaries, key=lambda item: (-item["starts"], item["condition"]))


def distance_band(distance: Any) -> str | None:
    """Map a valid race distance to a broad descriptive distance band."""
    value = _number(distance)
    if value is None or value < 1000 or value > 5000:
        return None
    if value <= 1400:
        return "短距離（1000〜1400m）"
    if value <= 1800:
        return "マイル（1401〜1800m）"
    if value <= 2200:
        return "中距離（1801〜2200m）"
    return "長距離（2201〜5000m）"


def summarize_distance_bands(
    records: Iterable[dict[str, Any]],
    *,
    min_starts: int = 3,
) -> list[dict[str, Any]]:
    """Summarize win/top-three rates by broad distance band."""
    prepared = []
    for row in records:
        band = distance_band(row.get("distance"))
        if band:
            prepared.append({**row, "distance_band": band})
    return summarize_conditions(prepared, "distance_band", min_starts=min_starts)


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
