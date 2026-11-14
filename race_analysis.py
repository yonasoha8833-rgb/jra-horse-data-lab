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


def parse_corner_positions(value: Any) -> list[int] | None:
    """Parse positive corner ranks from a list or a delimited official value."""
    if isinstance(value, str):
        tokens = [token for token in re.split(r"[\s,、/|‐‑‒–—-]+", value.strip()) if token]
    elif isinstance(value, (list, tuple)):
        tokens = list(value)
    else:
        return None
    if not tokens:
        return None
    positions = []
    for token in tokens:
        number = _number(token)
        if number is None or number < 1 or not number.is_integer():
            return None
        positions.append(int(number))
    return positions


def classify_running_style(corner_positions: Any, field_size: Any) -> str | None:
    """Estimate running style from mean corner rank relative to field size."""
    positions = parse_corner_positions(corner_positions)
    runners = _number(field_size)
    if positions is None or runners is None or runners < 2 or not runners.is_integer():
        return None
    if any(position > runners for position in positions):
        return None
    average_ratio = mean(positions) / runners
    first_ratio = positions[0] / runners
    if first_ratio <= 0.15 and average_ratio <= 0.25:
        return "逃げ"
    if average_ratio <= 0.40:
        return "先行"
    if average_ratio <= 0.70:
        return "差し"
    return "追込"


def _summarize_running_styles(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        style = classify_running_style(row.get("corner_positions"), row.get("field_size"))
        if style:
            counts[style] += 1
    total = sum(counts.values())
    if not total:
        return {"sample_starts": 0, "dominant_style": None, "counts": {}, "distribution": {}}
    order = ["逃げ", "先行", "差し", "追込"]
    dominant = sorted(counts, key=lambda style: (-counts[style], order.index(style)))[0]
    return {
        "sample_starts": total,
        "dominant_style": dominant,
        "counts": dict(sorted(counts.items(), key=lambda item: order.index(item[0]))),
        "distribution": {
            style: round(count / total, 4)
            for style, count in sorted(counts.items(), key=lambda item: order.index(item[0]))
        },
    }


def analyze_race_scenario(runners: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Estimate pace pressure from declared runner styles and score heuristic fit.

    This is a coarse scenario aid, not a race prediction. Missing styles are
    excluded from pressure counts and receive no individual fit score.
    """
    entries = list(runners)
    prepared = []
    for row in entries:
        style = row.get("running_style")
        if style not in {"逃げ", "先行", "差し", "追込"}:
            style = classify_running_style(row.get("corner_positions"), row.get("field_size"))
        prepared.append((row, style))

    known = [style for _, style in prepared if style]
    leaders = known.count("逃げ")
    forward = leaders + known.count("先行")
    known_count = len(known)
    if known_count == 0:
        pace = None
        label = "判定材料不足"
    elif leaders >= 3 or forward / known_count >= 0.55:
        pace = "前半速い"
        label = "ハイペース寄り"
    elif leaders <= 1 and forward / known_count <= 0.30:
        pace = "後半速い"
        label = "スローペース寄り"
    else:
        pace = "ほぼ均等"
        label = "平均ペース寄り"

    scores = {"逃げ": 50, "先行": 50, "差し": 50, "追込": 50}
    if pace == "前半速い":
        scores.update({"逃げ": 35, "先行": 45, "差し": 75, "追込": 70})
    elif pace == "後半速い":
        scores.update({"逃げ": 75, "先行": 70, "差し": 45, "追込": 35})
    elif pace == "ほぼ均等":
        scores.update({"逃げ": 55, "先行": 60, "差し": 55, "追込": 45})

    horse_scores = []
    for row, style in prepared:
        horse_scores.append({
            "horse_name": row.get("horse_name"),
            "horse_number": row.get("horse_number"),
            "running_style": style,
            "scenario_fit_score": scores[style] if style and pace else None,
        })
    return {
        "scenario": label,
        "estimated_pace_shape": pace,
        "known_style_count": known_count,
        "leader_count": leaders,
        "front_runner_count": forward,
        "style_coverage": round(known_count / len(entries), 4) if entries else 0.0,
        "horse_scores": horse_scores,
        "warning": "脚質の頭数から作る簡易シナリオ評価です。確率・着順予測ではなく、馬場、枠順、騎手、展開変化は反映していません。",
    }


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
            "running_style": _summarize_running_styles(rows),
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

def parse_lap_times(value: Any) -> list[float] | None:
    """Parse 200m split times without correcting or guessing OCR values."""
    if isinstance(value, str):
        tokens = re.split(r"[\\s,、/|‐‑‒–—-]+", value.strip())
        if not tokens or any(not token for token in tokens):
            return None
    elif isinstance(value, (list, tuple)):
        tokens = list(value)
    else:
        return None
    parsed = []
    for token in tokens:
        number = _number(token)
        # A 200m split outside this broad plausibility range is treated as
        # untrusted input; it is never silently repaired.
        if number is None or number < 8 or number > 40:
            return None
        parsed.append(number)
    return parsed or None


def summarize_race_pace(lap_times: Any, distance: Any) -> dict[str, Any] | None:
    """Describe race-level pace from official 200m splits, not horse-level speed.

    The distance must match the number of 200m splits exactly. Positive
    balance means the first half was faster per 200m than the second half.
    """
    splits = parse_lap_times(lap_times)
    metres = _number(distance)
    if splits is None or metres is None or metres < 1000 or metres > 5000:
        return None
    if metres % 200 != 0 or len(splits) != int(metres // 200):
        return None
    midpoint = len(splits) // 2
    first_half = splits[:midpoint]
    second_half = splits[midpoint:]
    if not first_half or not second_half:
        return None
    first_avg = mean(first_half)
    second_avg = mean(second_half)
    balance = round(second_avg - first_avg, 2)
    shape = "前半速い" if balance >= 0.3 else "後半速い" if balance <= -0.3 else "ほぼ均等"
    return {
        "split_count": len(splits),
        "first_3f_seconds": round(sum(splits[:3]), 2) if len(splits) >= 3 else None,
        "last_3f_seconds": round(sum(splits[-3:]), 2) if len(splits) >= 3 else None,
        "first_5f_seconds": round(sum(splits[:5]), 2) if len(splits) >= 5 else None,
        "last_5f_seconds": round(sum(splits[-5:]), 2) if len(splits) >= 5 else None,
        "first_half_average_200m": round(first_avg, 2),
        "second_half_average_200m": round(second_avg, 2),
        "second_minus_first_seconds_per_200m": balance,
        "pace_shape": shape,
        "interpretation": "レース全体のラップ傾向。個々の馬の走破速度や展開利を直接示すものではありません。",
    }


def summarize_running_style_pace_performance(
    records: Iterable[dict[str, Any]],
    *,
    min_starts: int = 3,
) -> list[dict[str, Any]]:
    """Summarize actual outcomes by estimated running style and official race pace.

    A race contributes only when its 200m lap data passes validation. Unknown
    styles and invalid finishing positions are excluded rather than inferred.
    Rates are descriptive historical statistics, not predictions.
    """
    if min_starts < 1:
        raise ValueError("min_starts must be at least 1")

    races: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        source_url = str(row.get("source_url") or "").strip()
        race_number = str(row.get("race_number") or "").strip()
        # Graded-race replay PDFs contain one race per source file. When the
        # official PDF URL is known, it safely identifies that race even if
        # the listing does not expose a race number.
        if not race_number and not source_url:
            continue
        key = (
            source_url,
            str(row.get("date") or "").strip(),
            str(row.get("track") or "").strip(),
            race_number or "__source_pdf__",
        )
        races[key].append(row)

    grouped: dict[tuple[str, str], list[tuple[dict[str, Any], int, int]]] = defaultdict(list)
    seen: set[tuple[tuple[str, str, str, str], str]] = set()
    for race_key, rows in races.items():
        first = rows[0]
        lap_times = next((row.get("race_lap_times") for row in rows
                          if row.get("race_lap_times") is not None), None)
        distance = next((row.get("distance") for row in rows
                         if row.get("distance") is not None), first.get("distance"))
        pace = summarize_race_pace(lap_times, distance)
        if pace is None:
            continue
        field_size = _number(first.get("field_size"))
        if field_size is None or field_size < 2 or not field_size.is_integer():
            field_size = len(rows)
        for row in rows:
            runner_id = str(row.get("horse_number") or row.get("horse_name") or "").strip()
            dedupe_key = (race_key, runner_id)
            if runner_id and dedupe_key in seen:
                continue
            if runner_id:
                seen.add(dedupe_key)
            style = row.get("running_style")
            if style not in {"逃げ", "先行", "差し", "追込"}:
                style = classify_running_style(row.get("corner_positions"), field_size)
            finish = _number(row.get("finish"))
            if not style or finish is None or finish < 1 or finish > field_size or not finish.is_integer():
                continue
            grouped[(style, pace["pace_shape"])].append((row, int(finish), int(field_size)))

    results = []
    style_order = {"逃げ": 0, "先行": 1, "差し": 2, "追込": 3}
    pace_order = {"前半速い": 0, "ほぼ均等": 1, "後半速い": 2}
    for (style, pace_shape), outcomes in sorted(
        grouped.items(), key=lambda item: (style_order[item[0][0]], pace_order[item[0][1]])
    ):
        starts = len(outcomes)
        if starts < min_starts:
            continue
        wins = sum(1 for _, finish, _ in outcomes if finish == 1)
        top3 = sum(1 for _, finish, _ in outcomes if finish <= 3)
        normalized = [100.0 * (field_size - finish) / (field_size - 1)
                      for _, finish, field_size in outcomes]
        results.append({
            "running_style": style,
            "pace_shape": pace_shape,
            "starts": starts,
            "wins": wins,
            "top3": top3,
            "win_rate": round(wins / starts, 4),
            "top3_rate": round(top3 / starts, 4),
            "average_field_normalized_finish_score": round(mean(normalized), 1),
            "sample_sufficient": starts >= max(min_starts, 10),
            "interpretation": "過去レースの記述統計です。少数標本や条件差があるため、将来の勝率を示すものではありません。",
        })
    return results


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
            "pace_analysis": summarize_race_pace(first.get("race_lap_times"), first.get("distance")),
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
