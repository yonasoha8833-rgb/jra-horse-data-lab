"""Horse-level statistics for normalized JRA race result records.

This module deliberately does not guess missing PDF fields. The PDF extraction
layer should provide one dictionary per runner using the documented keys.
"""
from __future__ import annotations

from collections import defaultdict
import re
from statistics import mean
from typing import Any, Iterable


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


REGISTRATION_NUMBER_RE = re.compile(r"^[0-9]{4}-[0-9]{5}$")


def _registration_number(value: Any) -> str | None:
    """Return only a well-formed JRA-style YYYY-NNNNN registration number."""
    candidate = str(value or "").strip()
    return candidate if REGISTRATION_NUMBER_RE.fullmatch(candidate) else None


def summarize_horses(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate normalized race rows by horse name.

    Expected optional keys: horse_name, date, track, surface, distance,
    going, class, finish, finish_time_seconds, field_size, last3f,
    corner_positions, jockey.
    Missing or unparseable values are excluded from numeric averages.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen_runners: set[tuple[str, str, str, str, str]] = set()
    for row in records:
        # When a source and full race/runner identity are available, count that
        # runner only once. Rows lacking any key component remain untouched.
        source = str(row.get("source_url") or "").strip()
        race_number = str(row.get("race_number") or "").strip()
        horse_number = str(row.get("horse_number") or "").strip()
        race_date = str(row.get("date") or "").strip()
        track = str(row.get("track") or "").strip()
        if all((source, race_number, horse_number, race_date, track)):
            runner_key = (source, race_date, track, race_number, horse_number)
            if runner_key in seen_runners:
                continue
            seen_runners.add(runner_key)
        name = str(row.get("horse_name") or "").strip()
        registration_number = _registration_number(row.get("horse_registration_number"))
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

def summarize_races(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate runner rows into race-level summaries without guessing missing data."""
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        race_number = str(row.get("race_number") or "").strip()
        if not race_number:
            continue
        key = (
            str(row.get("source_url") or "").strip(),
            str(row.get("date") or "").strip(),
            str(row.get("track") or "").strip(),
            race_number,
        )
        grouped[key].append(row)

    summaries = []
    for key, rows in grouped.items():
        placed = [row for row in rows if (_number(row.get("finish")) or 0) > 0]
        winners = [row for row in placed if _number(row.get("finish")) == 1]
        top_three = [row for row in placed if _number(row.get("finish")) in (1, 2, 3)]
        warnings = sorted({
            warning
            for row in rows
            for warning in (row.get("race_validation_warnings") if isinstance(row.get("race_validation_warnings"), list) else [])
            if isinstance(warning, str)
        })
        first = rows[0]
        summaries.append({
            "source_url": key[0] or None,
            "date": key[1] or None,
            "track": key[2] or None,
            "race_number": int(key[3]) if key[3].isdigit() else key[3],
            "race_name": first.get("race_name"),
            "surface": first.get("surface"),
            "distance": first.get("distance"),
            "going": first.get("going"),
            "starters": len(rows),
            "placed_starters": len(placed),
            "unplaced_starters": len(rows) - len(placed),
            "winner_count": len(winners),
            "winner_names": [str(row.get("horse_name") or "") for row in winners if row.get("horse_name")],
            "winning_time_seconds": _number(winners[0].get("finish_time_seconds")) if len(winners) == 1 else None,
            "top_three": [
                {"finish": _number(row.get("finish")), "horse_number": row.get("horse_number"),
                 "horse_name": row.get("horse_name"), "finish_time": row.get("finish_time")}
                for row in sorted(top_three, key=lambda row: (_number(row.get("finish")) or 99, _number(row.get("horse_number")) or 99))
            ],
            "race_last3f": _number(first.get("race_last3f")),
            "race_lap_times": first.get("race_lap_times") if isinstance(first.get("race_lap_times"), list) else None,
            "validation_warnings": warnings,
        })
    return sorted(summaries, key=lambda race: (
        str(race.get("date") or ""), str(race.get("track") or ""),
        int(race["race_number"]) if str(race.get("race_number", "")).isdigit() else 0
    ), reverse=True)

def summarize_race_conditions(
    records: Iterable[dict[str, Any]],
    *,
    dimension: str = "track",
    min_races: int = 3,
) -> list[dict[str, Any]]:
    """Summarize race-level averages by track/course/distance band/going.

    Each race is counted once, not once per runner. Missing condition values
    and missing measurements are excluded rather than inferred.
    """
    allowed = {"track", "surface", "distance_band", "going"}
    if dimension not in allowed:
        raise ValueError(f"unsupported race condition dimension: {dimension}")
    if min_races < 1:
        raise ValueError("min_races must be at least 1")
    races = summarize_races(records)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for race in races:
        value = distance_band(race.get("distance")) if dimension == "distance_band" else race.get(dimension)
        value = str(value or "").strip()
        if value:
            grouped[value].append(race)

    result = []
    for condition, items in grouped.items():
        if len(items) < min_races:
            continue
        starters = [_number(item.get("starters")) for item in items]
        winning_times = [_number(item.get("winning_time_seconds")) for item in items]
        last3fs = [_number(item.get("race_last3f")) for item in items]
        result.append({
            "condition": condition,
            "races": len(items),
            "average_starters": round(mean(v for v in starters if v is not None), 1) if any(v is not None for v in starters) else None,
            "average_winning_time_seconds": round(mean(v for v in winning_times if v is not None), 2) if any(v is not None for v in winning_times) else None,
            "average_race_last3f": round(mean(v for v in last3fs if v is not None), 2) if any(v is not None for v in last3fs) else None,
            "warning_races": sum(1 for item in items if item.get("validation_warnings")),
        })
    return sorted(result, key=lambda item: (-item["races"], item["condition"]))



def summarize_horse_condition_profiles(
    records: Iterable[dict[str, Any]],
    *,
    min_starts: int = 3,
) -> list[dict[str, Any]]:
    """Return per-horse condition performance using consistent identity keys.

    Profiles cover track, surface, distance band, going, surface+going, and
    track+surface+distance. Conditions below min_starts are omitted. Unknown
    condition values are never inferred.
    """
    if min_starts < 1:
        raise ValueError("min_starts must be at least 1")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen_runners: set[tuple[str, str, str, str, str]] = set()
    for row in records:
        # Match summarize_horses: suppress duplicate imports only when the
        # complete source/race/runner identity is available.
        source = str(row.get("source_url") or "").strip()
        race_number = str(row.get("race_number") or "").strip()
        horse_number = str(row.get("horse_number") or "").strip()
        race_date = str(row.get("date") or "").strip()
        track = str(row.get("track") or "").strip()
        if all((source, race_number, horse_number, race_date, track)):
            runner_key = (source, race_date, track, race_number, horse_number)
            if runner_key in seen_runners:
                continue
            seen_runners.add(runner_key)
        name = str(row.get("horse_name") or "").strip()
        if not name:
            continue
        registration = _registration_number(row.get("horse_registration_number"))
        identity = ("registration", registration) if registration else ("name", name)
        grouped[identity].append(row)

    profiles = []
    for identity, rows in sorted(grouped.items()):
        prepared = []
        for row in rows:
            distance = distance_band(row.get("distance"))
            item = dict(row)
            if distance:
                item["distance_band"] = distance
            if row.get("surface") and row.get("going"):
                item["surface_going"] = f'{row["surface"]}・{row["going"]}'
            if row.get("track") and row.get("surface") and _number(row.get("distance")):
                item["track_surface_distance"] = (
                    f'{row["track"]} {row["surface"]} {int(_number(row["distance"]))}m'
                )
            prepared.append(item)
        dimensions = {
            "track": "track",
            "surface": "surface",
            "distance_band": "distance_band",
            "going": "going",
            "surface_going": "surface_going",
            "track_surface_distance": "track_surface_distance",
        }
        profiles.append({
            "horse_name": str(rows[0].get("horse_name") or "").strip(),
            "horse_registration_number": identity[1] if identity[0] == "registration" else None,
            "identity_basis": "official_registration_number" if identity[0] == "registration" else "horse_name_only",
            "identity_verified": identity[0] == "registration",
            "starts": len(rows),
            "conditions": {
                label: summarize_conditions(prepared, field, min_starts=min_starts)
                for label, field in dimensions.items()
            },
        })
    return profiles
