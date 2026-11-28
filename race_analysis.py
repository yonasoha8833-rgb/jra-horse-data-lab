"""Horse-level statistics for normalized JRA race result records.

This module deliberately does not guess missing PDF fields. The PDF extraction
layer should provide one dictionary per runner using the documented keys.
"""
from __future__ import annotations

from collections import defaultdict
from math import isfinite
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


def _individual_last3f(row: dict[str, Any]) -> float | None:
    """Read an individual time; never reinterpret an explicit race-level 3F as a runner time."""
    explicit = _number(row.get("individual_last3f"))
    if explicit is not None and explicit > 0:
        return explicit
    race_level = row.get("race_last3f")
    if race_level is not None and race_level != "":
        return None
    legacy = _number(row.get("last3f"))
    return legacy if legacy is not None and legacy > 0 else None


BASELINE_RECENT_LIMIT = 5
REGISTRATION_NUMBER_RE = re.compile(r"^[0-9]{4}-[0-9]{5}$")


def _registration_number(value: Any) -> str | None:
    """Return only a well-formed JRA-style YYYY-NNNNN registration number."""
    candidate = str(value or "").strip()
    return candidate if REGISTRATION_NUMBER_RE.fullmatch(candidate) else None


def _horse_profile_id(value: Any) -> str | None:
    """Return an exact, non-empty official JRA horse profile identifier."""
    candidate = str(value or "").strip()
    return candidate or None


def _matches_horse_summary(
    item: dict[str, Any],
    registration: str | None,
    profile_id: str | None,
    name: str,
) -> bool:
    """Match the strongest supplied horse identifiers without accepting conflicts."""
    item_registration = _registration_number(item.get("horse_registration_number"))
    item_profile_id = _horse_profile_id(item.get("horse_profile_id"))
    if registration or profile_id:
        if registration and item_registration and item_registration != registration:
            return False
        if profile_id and item_profile_id and item_profile_id != profile_id:
            return False
        return bool(
            (registration and item_registration == registration)
            or (profile_id and item_profile_id == profile_id)
        )
    return bool(
        name
        and not item_registration
        and not item_profile_id
        and item.get("horse_name") == name
    )


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
        last3fs = [_individual_last3f(r) for r in rows]
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
        individual_last3fs = [
            value
            for row in rows
            if (value := _individual_last3f(row)) is not None
            and value > 0
        ]
        summaries.append({
            "condition": condition,
            "starts": len(rows),
            "wins": wins,
            "top3": top3,
            "win_rate": round(wins / len(rows), 4),
            "top3_rate": round(top3 / len(rows), 4),
            "individual_last3f_sample_count": len(individual_last3fs),
            "average_individual_last3f": (
                round(mean(individual_last3fs), 2) if individual_last3fs else None
            ),
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
    recent = valid[:BASELINE_RECENT_LIMIT]
    if not recent:
        return None
    weights = list(range(len(recent), 0, -1))
    return round(sum(score * weight for (_, score), weight in zip(recent, weights)) / sum(weights), 1)


def condition_adjusted_performance_score(
    records: Iterable[dict[str, Any]],
    target_conditions: dict[str, Any],
    *,
    min_condition_starts: int = 3,
    prior_strength: float = 5.0,
) -> dict[str, Any]:
    """Blend overall form with the best-supported matching condition sample.

    Condition scores use field-size-normalized finishing positions. The most
    specific condition group meeting min_condition_starts is selected, then
    shrunk toward the horse's overall score using prior_strength pseudo-starts.
    This is a descriptive index, not a win probability.
    """
    if min_condition_starts < 1:
        raise ValueError("min_condition_starts must be at least 1")
    if not isinstance(prior_strength, (int, float)) or prior_strength < 0:
        raise ValueError("prior_strength must be a non-negative number")

    rows = list(records)
    baseline = performance_score(rows)
    if baseline is None:
        return {
            "score": None,
            "overall_score": None,
            "condition_score": None,
            "condition_sample_count": 0,
            "matched_condition": None,
            "confidence": "判定材料不足",
        }

    target = target_conditions or {}
    surface = str(target.get("surface") or "").strip()
    track = str(target.get("track") or "").strip()
    going = str(target.get("going") or "").strip()
    distance = _number(target.get("distance"))
    band = distance_band(distance)
    exact_distance = int(distance) if distance is not None and distance.is_integer() else None

    candidates: list[tuple[str, Any]] = []
    if track and surface and exact_distance is not None:
        candidates.append(("競馬場・芝ダート・距離", lambda r: r.get("track") == track and r.get("surface") == surface and _number(r.get("distance")) == exact_distance))
    if surface and band:
        candidates.append(("芝ダート・距離帯", lambda r: r.get("surface") == surface and distance_band(r.get("distance")) == band))
    if surface and going:
        candidates.append(("芝ダート・馬場状態", lambda r: r.get("surface") == surface and r.get("going") == going))
    if track and surface:
        candidates.append(("競馬場・芝ダート", lambda r: r.get("track") == track and r.get("surface") == surface))
    if surface:
        candidates.append(("芝ダート", lambda r: r.get("surface") == surface))
    if band:
        candidates.append(("距離帯", lambda r: distance_band(r.get("distance")) == band))
    if going:
        candidates.append(("馬場状態", lambda r: r.get("going") == going))
    if track:
        candidates.append(("競馬場", lambda r: r.get("track") == track))

    selected = None
    for label, matches in candidates:
        matching = []
        for row in rows:
            if not matches(row):
                continue
            finish = _number(row.get("finish"))
            field_size = _number(row.get("field_size"))
            if (finish is None or field_size is None or field_size <= 1
                    or not finish.is_integer() or not field_size.is_integer()
                    or finish < 1 or finish > field_size):
                continue
            matching.append(100.0 * (field_size - finish) / (field_size - 1))
        if len(matching) >= min_condition_starts:
            selected = (label, matching)
            break

    if selected is None:
        return {
            "score": baseline,
            "overall_score": baseline,
            "condition_score": None,
            "condition_sample_count": 0,
            "matched_condition": None,
            "confidence": "総合成績のみ",
        }

    label, scores = selected
    condition_average = mean(scores)
    adjusted = (
        (condition_average * len(scores) + baseline * prior_strength)
        / (len(scores) + prior_strength)
    )
    confidence = "高め" if len(scores) >= 10 else "中程度"
    return {
        "score": round(adjusted, 1),
        "overall_score": baseline,
        "condition_score": round(condition_average, 1),
        "condition_sample_count": len(scores),
        "matched_condition": label,
        "confidence": confidence,
    }

def parse_lap_times(value: Any) -> list[float] | None:
    """Parse 200m split times without correcting or guessing OCR values."""
    if isinstance(value, str):
        tokens = re.split(r"[\s,、/|‐‑‒–—-]+", value.strip())
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
        field_size = next(
            (
                value for value in (_number(row.get("field_size")) for row in rows)
                if value is not None and value >= 2 and value.is_integer()
            ),
            None,
        )
        if field_size is None:
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
        individual_last3fs = [
            value
            for row, _, _ in outcomes
            if (value := _individual_last3f(row)) is not None
            and value > 0
        ]
        results.append({
            "running_style": style,
            "pace_shape": pace_shape,
            "starts": starts,
            "wins": wins,
            "top3": top3,
            "win_rate": round(wins / starts, 4),
            "top3_rate": round(top3 / starts, 4),
            "average_field_normalized_finish_score": round(mean(normalized), 1),
            "individual_last3f_sample_count": len(individual_last3fs),
            "average_individual_last3f": round(mean(individual_last3fs), 2) if individual_last3fs else None,
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




def summarize_relative_last3f(
    records: Iterable[dict[str, Any]],
    *,
    min_race_runners: int = 3,
    min_condition_starts: int = 3,
) -> list[dict[str, Any]]:
    """Summarize each horse's last-3F rank relative to runners in the same race.

    A race is ranked only when every declared runner is present, field size is
    consistent, and every finisher has a valid individual last-3F. Explicitly
    recorded non-finishers are included in the field check but are not ranked.
    Ties receive average ranks.
    """
    if min_race_runners < 2:
        raise ValueError("min_race_runners must be at least 2")
    if min_condition_starts < 1:
        raise ValueError("min_condition_starts must be at least 1")

    races: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        date = str(row.get("date") or "").strip()
        track = str(row.get("track") or "").strip()
        race_number = str(row.get("race_number") or "").strip()
        if not (date and track and race_number):
            continue
        # Different official PDFs can describe the same race. Group by the
        # official race identity, not by source URL, to avoid duplicate starts.
        races[(date, track, race_number)].append(row)

    scored: list[dict[str, Any]] = []
    for race_rows in races.values():
        runners = _deduplicate_runner_records(race_rows)
        if len(runners) < min_race_runners:
            continue

        field_sizes = [_number(row.get("field_size")) for row in runners]
        if any(size is None or not size.is_integer() for size in field_sizes):
            continue
        if len(set(field_sizes)) != 1 or int(field_sizes[0]) != len(runners):
            continue

        finishers = [row for row in runners if _normalized_finish_score(row) is not None]
        explicit_nonfinishers = [
            row for row in runners if str(row.get("result_status") or "").strip()
        ]
        if (
            len(finishers) < min_race_runners
            or len(finishers) + len(explicit_nonfinishers) != len(runners)
        ):
            continue

        ranked = []
        invalid_finisher_time = False
        for index, row in enumerate(finishers):
            value = _individual_last3f(row)
            if value is None or value <= 0:
                invalid_finisher_time = True
                break
            ranked.append((float(value), index))
        if invalid_finisher_time:
            continue
        ranked.sort()

        ranks: dict[int, float] = {}
        pos = 0
        while pos < len(ranked):
            end = pos + 1
            while end < len(ranked) and ranked[end][0] == ranked[pos][0]:
                end += 1
            average_rank = ((pos + 1) + end) / 2
            for _, index in ranked[pos:end]:
                ranks[index] = average_rank
            pos = end
        for index, row in enumerate(finishers):
            score = 100.0 * (len(finishers) - ranks[index]) / (len(finishers) - 1)
            scored.append({**row, "relative_last3f_score": score})

    horses: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str, str, str]] = set()
    for row in scored:
        runner_key = _race_runner_identity(row)
        if runner_key is not None:
            if runner_key in seen:
                continue
            seen.add(runner_key)
        name = str(row.get("horse_name") or "").strip()
        registration = _registration_number(row.get("horse_registration_number"))
        profile_id = _horse_profile_id(row.get("horse_profile_id"))
        if not name and not profile_id:
            continue
        identity = (
            ("registration", registration) if registration else
            ("profile", profile_id) if profile_id else ("name", name)
        )
        horses[identity].append(row)

    results = []
    dimensions = {
        "track": "track",
        "surface": "surface",
        "distance_band": "distance_band",
        "going": "going",
        "surface_going": "surface_going",
        "track_surface_distance": "track_surface_distance",
    }
    for identity, rows in sorted(horses.items()):
        prepared = []
        for row in rows:
            item = dict(row)
            band = distance_band(row.get("distance"))
            if band:
                item["distance_band"] = band
            if row.get("surface") and row.get("going"):
                item["surface_going"] = f'{row["surface"]}・{row["going"]}'
            distance = _number(row.get("distance"))
            if row.get("track") and row.get("surface") and distance is not None and distance.is_integer():
                item["track_surface_distance"] = f'{row["track"]} {row["surface"]} {int(distance)}m'
            prepared.append(item)

        conditions = {}
        for label, field in dimensions.items():
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in prepared:
                condition = str(row.get(field) or "").strip()
                if condition:
                    groups[condition].append(row)
            summaries = []
            for condition, items in groups.items():
                if len(items) < min_condition_starts:
                    continue
                times = []
                for item in items:
                    numeric_value = _individual_last3f(item)
                    if numeric_value is not None and numeric_value > 0:
                        times.append(numeric_value)
                summaries.append({
                    "condition": condition,
                    "sample_count": len(items),
                    "average_relative_score": round(mean(item["relative_last3f_score"] for item in items), 1),
                    "average_individual_last3f": round(mean(times), 2) if times else None,
                })
            conditions[label] = sorted(
                summaries, key=lambda item: (-item["sample_count"], item["condition"])
            )
        profile_id = next(
            (value for row in rows if (value := _horse_profile_id(row.get("horse_profile_id")))),
            None,
        )
        results.append({
            "horse_name": str(rows[0].get("horse_name") or "").strip(),
            "horse_registration_number": identity[1] if identity[0] == "registration" else None,
            "horse_profile_id": profile_id,
            "identity_basis": (
                "official_registration_number" if identity[0] == "registration" else
                "official_horse_profile_id" if identity[0] == "profile" else "horse_name_only"
            ),
            "identity_verified": identity[0] in {"registration", "profile"},
            "comparable_starts": len(rows),
            "average_relative_score": round(mean(row["relative_last3f_score"] for row in rows), 1),
            "conditions": conditions,
            "warning": "同一レース内の個別上がり3F順位を0〜100点に換算した相対指数です。異なるレース間の走破速度や勝率を示すものではありません。",
        })
    return results

def integrated_condition_last3f_score(
    horse_records: Iterable[dict[str, Any]],
    race_records: Iterable[dict[str, Any]],
    target_conditions: dict[str, Any],
    *,
    horse_registration_number: str | None = None,
    horse_profile_id: str | None = None,
    horse_name: str | None = None,
    min_condition_starts: int = 3,
    prior_strength: float = 5.0,
    relative_last3f_weight: float = 0.2,
) -> dict[str, Any]:
    """Combine condition-adjusted placing with same-race relative last-3F.

    race_records must contain the target horse and its competitors for each
    race. Relative last-3F is included only when a matching condition has
    enough comparable starts; otherwise the placing-based score is unchanged.
    This descriptive index is not a win probability.
    """
    if not 0 <= relative_last3f_weight <= 1:
        raise ValueError("relative_last3f_weight must be between 0 and 1")
    if min_condition_starts < 1:
        raise ValueError("min_condition_starts must be at least 1")

    base = condition_adjusted_performance_score(
        horse_records, target_conditions,
        min_condition_starts=min_condition_starts,
        prior_strength=prior_strength,
    )
    if base["score"] is None:
        return {
            **base,
            "relative_last3f_score": None,
            "relative_last3f_sample_count": 0,
            "relative_last3f_condition": None,
            "integrated": False,
        }

    registration = _registration_number(horse_registration_number)
    profile_id = _horse_profile_id(horse_profile_id)
    name = str(horse_name or "").strip()
    if not registration and not profile_id and not name:
        return {
            **base,
            "relative_last3f_score": None,
            "relative_last3f_sample_count": 0,
            "relative_last3f_condition": None,
            "integrated": False,
        }

    summaries = summarize_relative_last3f(
        race_records, min_condition_starts=min_condition_starts
    )
    target = next((
        item for item in summaries
        if (registration and item["horse_registration_number"] == registration)
        or (profile_id and item["horse_profile_id"] == profile_id)
        or (
            not registration and not profile_id
            and not item["horse_registration_number"]
            and not item["horse_profile_id"]
            and item["horse_name"] == name
        )
    ), None)
    if target is None:
        return {
            **base,
            "relative_last3f_score": None,
            "relative_last3f_sample_count": 0,
            "relative_last3f_condition": None,
            "integrated": False,
        }

    conditions = target_conditions or {}
    surface = str(conditions.get("surface") or "").strip()
    track = str(conditions.get("track") or "").strip()
    going = str(conditions.get("going") or "").strip()
    distance = _number(conditions.get("distance"))
    band = distance_band(distance)
    exact_distance = int(distance) if distance is not None and distance.is_integer() else None
    candidates = []
    if track and surface and exact_distance is not None:
        candidates.append(("track_surface_distance", f"{track} {surface} {exact_distance}m", "競馬場・芝ダート・距離"))
    if surface and band:
        candidates.append(("surface", surface, "芝ダート"))
        candidates.append(("distance_band", band, "距離帯"))
    if surface and going:
        candidates.append(("surface_going", f"{surface}・{going}", "芝ダート・馬場状態"))
    if track:
        candidates.append(("track", track, "競馬場"))
    if surface:
        candidates.append(("surface", surface, "芝ダート"))
    if band:
        candidates.append(("distance_band", band, "距離帯"))
    if going:
        candidates.append(("going", going, "馬場状態"))

    selected = None
    for dimension, condition, label in candidates:
        match = next((
            item for item in target["conditions"].get(dimension, [])
            if item["condition"] == condition
            and item["sample_count"] >= min_condition_starts
        ), None)
        if match is not None:
            selected = (label, match)
            break

    if selected is None or relative_last3f_weight == 0:
        return {
            **base,
            "relative_last3f_score": None,
            "relative_last3f_sample_count": 0,
            "relative_last3f_condition": None,
            "integrated": False,
        }

    label, relative = selected
    relative_score = relative["average_relative_score"]
    combined = (
        base["score"] * (1 - relative_last3f_weight)
        + relative_score * relative_last3f_weight
    )
    return {
        **base,
        "score": round(combined, 1),
        "placing_condition_score": base["score"],
        "relative_last3f_score": relative_score,
        "relative_last3f_sample_count": relative["sample_count"],
        "relative_last3f_condition": label,
        "relative_last3f_weight": relative_last3f_weight,
        "integrated": True,
        "confidence": "高め" if relative["sample_count"] >= 10 and base["confidence"] == "高め" else "中程度",
    }



def _normalized_finish_score(row: dict[str, Any]) -> float | None:
    """Return a field-size-normalized placing score for a valid result row."""
    finish = _number(row.get("finish"))
    field_size = _number(row.get("field_size"))
    if (finish is None or field_size is None or not finish.is_integer()
            or not field_size.is_integer() or field_size <= 1
            or finish < 1 or finish > field_size):
        return None
    return 100.0 * (field_size - finish) / (field_size - 1)


def _normalized_integrated_score(row: dict[str, Any]) -> float | None:
    """Score official finishers normally and explicit started non-finishers as zero.

    Only JRA result statuses that prove the horse started (競走中止, 失格)
    receive zero. Scratches, exclusions, unknown statuses and missing rows
    remain unscored rather than being guessed.
    """
    placing = _normalized_finish_score(row)
    if placing is not None:
        return placing
    status = str(row.get("result_status") or "").strip()
    field_size = _number(row.get("field_size"))
    if (
        status in {"競走中止", "失格"}
        and field_size is not None
        and field_size.is_integer()
        and field_size > 1
    ):
        return 0.0
    return None


def _integrated_performance_score(records: Iterable[dict[str, Any]]) -> float | None:
    """Recent field-normalized form, including only explicit started non-finishers."""
    valid = []
    for row in records:
        score = _normalized_integrated_score(row)
        if score is not None:
            valid.append((str(row.get("date") or ""), score))
    valid.sort(key=lambda item: item[0], reverse=True)
    recent = valid[:BASELINE_RECENT_LIMIT]
    if not recent:
        return None
    weights = list(range(len(recent), 0, -1))
    return round(
        sum(score * weight for (_, score), weight in zip(recent, weights)) / sum(weights),
        1,
    )


def _race_runner_identity(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Identify a runner within an official race, independent of source PDF."""
    date = str(row.get("date") or "").strip()
    track = str(row.get("track") or "").strip()
    race_number = str(row.get("race_number") or "").strip()
    if not (date and track and race_number):
        return None

    horse_number = _number(row.get("horse_number"))
    if horse_number is not None and horse_number.is_integer() and horse_number > 0:
        runner = "number:" + str(int(horse_number))
    else:
        registration = _registration_number(row.get("horse_registration_number"))
        name = str(row.get("horse_name") or "").strip()
        runner = (
            "registration:" + registration if registration else
            "name:" + name if name else ""
        )
    return (date, track, race_number, runner) if runner else None


def _merge_runner_records(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Fill missing runner fields from a duplicate official-source row."""
    # Capture legacy values before merging race-level fields from another source.
    current_time = _individual_last3f(existing)
    incoming_time = _individual_last3f(incoming)
    merged = dict(existing)
    for field, value in incoming.items():
        current = merged.get(field)
        if (current is None or current == "") and value is not None and value != "":
            merged[field] = value

    # Prefer a valid placing pair when the first source has incomplete values.
    if (_normalized_finish_score(merged) is None
            and _normalized_finish_score(incoming) is not None):
        merged["finish"] = incoming.get("finish")
        merged["field_size"] = incoming.get("field_size")

    if current_time is not None:
        merged["individual_last3f"] = current_time
    elif incoming_time is not None:
        merged["individual_last3f"] = incoming_time

    return merged


def _deduplicate_runner_records(
    records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge cross-source duplicates but discard same-source or untraceable duplicates.

    Repeated runner identities within one PDF are ambiguous: they may hide a
    missing runner or a bad horse-number read. Do not let them contribute to
    placing or relative last-3F statistics.
    """
    rows: list[dict[str, Any] | None] = []
    seen: dict[tuple[str, str, str, str], int] = {}
    sources: dict[tuple[str, str, str, str], set[str]] = {}
    ambiguous: set[tuple[str, str, str, str]] = set()

    for row in records:
        key = _race_runner_identity(row)
        if key is None:
            rows.append(row)
            continue
        if key in ambiguous:
            continue

        source = str(row.get("source_url") or "").strip()
        if key not in seen:
            seen[key] = len(rows)
            sources[key] = {source} if source else set()
            rows.append(row)
            continue

        known_sources = sources[key]
        if not source or not known_sources or source in known_sources:
            rows[seen[key]] = None
            ambiguous.add(key)
            continue

        known_sources.add(source)
        index = seen[key]
        existing = rows[index]
        if existing is not None:
            rows[index] = _merge_runner_records(existing, row)

    return [row for row in rows if row is not None]


def _score_condition_component(
    rows: list[dict[str, Any]],
    candidates: list[tuple[str, Any]],
    baseline: float,
    *,
    min_starts: int,
    prior_strength: float,
) -> dict[str, Any]:
    """Choose the most specific supported condition and shrink it to baseline."""
    for label, matches in candidates:
        scores = [
            score for row in rows
            if matches(row)
            and (score := _normalized_integrated_score(row)) is not None
        ]
        if len(scores) < min_starts:
            continue
        raw_score = mean(scores)
        adjusted = (
            (raw_score * len(scores) + baseline * prior_strength)
            / (len(scores) + prior_strength)
        )
        return {
            "score": round(adjusted, 1),
            "raw_score": round(raw_score, 1),
            "sample_count": len(scores),
            "matched_condition": label,
        }
    return {
        "score": None,
        "raw_score": None,
        "sample_count": 0,
        "matched_condition": None,
    }


def integrated_horse_performance_score(
    horse_records: Iterable[dict[str, Any]],
    race_records: Iterable[dict[str, Any]],
    target_conditions: dict[str, Any],
    *,
    horse_registration_number: str | None = None,
    horse_profile_id: str | None = None,
    horse_name: str | None = None,
    min_condition_starts: int = 3,
    prior_strength: float = 5.0,
    placing_weight: float = 0.4,
    distance_weight: float = 0.2,
    going_weight: float = 0.2,
    relative_last3f_weight: float = 0.2,
) -> dict[str, Any]:
    """Integrate placing, distance suitability, going suitability and relative last-3F.

    Distance and going are evaluated independently so one supported condition
    cannot hide the other. Each condition score is shrunk toward overall form;
    relative last-3F is shrunk toward a neutral 50. Missing components are
    omitted and the remaining weights are renormalized. This is a descriptive
    index, not a win probability.
    """
    if min_condition_starts < 1:
        raise ValueError("min_condition_starts must be at least 1")
    if not isinstance(prior_strength, (int, float)) or not isfinite(prior_strength) or prior_strength < 0:
        raise ValueError("prior_strength must be a finite non-negative number")

    weights = {
        "placing": placing_weight,
        "distance": distance_weight,
        "going": going_weight,
        "relative_last3f": relative_last3f_weight,
    }
    if any(
        not isinstance(weight, (int, float)) or not isfinite(weight) or weight < 0
        for weight in weights.values()
    ):
        raise ValueError("component weights must be finite non-negative numbers")
    if placing_weight <= 0:
        raise ValueError("placing_weight must be greater than zero")

    # Merge only traceable cross-source duplicates; same-source duplicates are ambiguous.
    rows = _deduplicate_runner_records(horse_records)

    valid_rows = [row for row in rows if _normalized_integrated_score(row) is not None]
    baseline = _integrated_performance_score(valid_rows)
    baseline_count = len(valid_rows)
    baseline_used_count = min(BASELINE_RECENT_LIMIT, baseline_count)
    if baseline is None:
        return {
            "score": None,
            "baseline_score": None,
            "baseline_sample_count": 0,
            "baseline_used_sample_count": 0,
            "distance_score": None,
            "distance_raw_score": None,
            "distance_sample_count": 0,
            "distance_condition": None,
            "going_score": None,
            "going_raw_score": None,
            "going_sample_count": 0,
            "going_condition": None,
            "relative_last3f_score": None,
            "relative_last3f_raw_score": None,
            "relative_last3f_sample_count": 0,
            "relative_last3f_condition": None,
            "component_weights": {},
            "components_used": [],
            "component_coverage_count": 0,
            "component_coverage_total": 4,
            "component_coverage_ratio": 0.0,
            "integrated": False,
            "confidence": "判定材料不足",
        }

    target = target_conditions if isinstance(target_conditions, dict) else {}
    surface = str(target.get("surface") or "").strip()
    going = str(target.get("going") or "").strip()
    distance = _number(target.get("distance"))
    exact_distance = (
        int(distance) if distance is not None and distance.is_integer() else None
    )
    band = distance_band(distance)

    distance_candidates: list[tuple[str, Any]] = []
    if surface and exact_distance is not None:
        distance_candidates.append((
            "芝ダート・同距離",
            lambda row: row.get("surface") == surface
            and _number(row.get("distance")) == exact_distance,
        ))
    if surface and band:
        distance_candidates.append((
            "芝ダート・距離帯",
            lambda row: row.get("surface") == surface
            and distance_band(row.get("distance")) == band,
        ))
    if exact_distance is not None:
        distance_candidates.append((
            "同距離",
            lambda row: _number(row.get("distance")) == exact_distance,
        ))
    if band:
        distance_candidates.append((
            "距離帯",
            lambda row: distance_band(row.get("distance")) == band,
        ))

    going_candidates: list[tuple[str, Any]] = []
    if surface and going:
        going_candidates.append((
            "芝ダート・馬場状態",
            lambda row: row.get("surface") == surface and row.get("going") == going,
        ))
    if going:
        going_candidates.append((
            "馬場状態",
            lambda row: row.get("going") == going,
        ))

    distance_component = _score_condition_component(
        valid_rows, distance_candidates, baseline,
        min_starts=min_condition_starts, prior_strength=prior_strength,
    )
    going_component = _score_condition_component(
        valid_rows, going_candidates, baseline,
        min_starts=min_condition_starts, prior_strength=prior_strength,
    )

    registration = _registration_number(horse_registration_number)
    profile_id = _horse_profile_id(horse_profile_id)
    name = str(horse_name or "").strip()
    relative_component = {
        "score": None,
        "raw_score": None,
        "sample_count": 0,
        "matched_condition": None,
    }
    if registration or profile_id or name:
        relative_summaries = summarize_relative_last3f(
            race_records, min_condition_starts=min_condition_starts
        )
        matching_summaries = [
            item for item in relative_summaries
            if _matches_horse_summary(item, registration, profile_id, name)
        ]
        # Two separate partial-ID summaries cannot prove that both IDs belong
        # to the same horse; omit the component instead of choosing the first.
        horse_summary = matching_summaries[0] if len(matching_summaries) == 1 else None
        if horse_summary is not None:
            track = str(target.get("track") or "").strip()
            relative_candidates = []
            if track and surface and exact_distance is not None:
                relative_candidates.append((
                    "競馬場・芝ダート・距離",
                    "track_surface_distance",
                    f"{track} {surface} {exact_distance}m",
                ))
            if surface and going:
                relative_candidates.append((
                    "芝ダート・馬場状態",
                    "surface_going",
                    f"{surface}・{going}",
                ))
            if band:
                relative_candidates.append(("距離帯", "distance_band", band))
            if going:
                relative_candidates.append(("馬場状態", "going", going))
            if track:
                relative_candidates.append(("競馬場", "track", track))
            if surface:
                relative_candidates.append(("芝ダート", "surface", surface))

            selected = None
            for label, dimension, condition in relative_candidates:
                match = next((
                    item for item in horse_summary["conditions"].get(dimension, [])
                    if item["condition"] == condition
                    and item["sample_count"] >= min_condition_starts
                ), None)
                if match is not None:
                    selected = (label, match)
                    break
            if selected is not None:
                label, match = selected
                raw_score = float(match["average_relative_score"])
                sample_count = int(match["sample_count"])
                adjusted = (
                    (raw_score * sample_count + 50.0 * prior_strength)
                    / (sample_count + prior_strength)
                )
                relative_component = {
                    "score": round(adjusted, 1),
                    "raw_score": round(raw_score, 1),
                    "sample_count": sample_count,
                    "matched_condition": label,
                }

    components = {
        "placing": (baseline, placing_weight, baseline_count),
        "distance": (
            distance_component["score"], distance_weight,
            distance_component["sample_count"],
        ),
        "going": (
            going_component["score"], going_weight,
            going_component["sample_count"],
        ),
        "relative_last3f": (
            relative_component["score"], relative_last3f_weight,
            relative_component["sample_count"],
        ),
    }
    available = {
        key: (score, weight, count)
        for key, (score, weight, count) in components.items()
        if score is not None and weight > 0
    }
    total_weight = sum(weight for _, weight, _ in available.values())
    component_weights = {
        key: round(weight / total_weight, 4)
        for key, (_, weight, _) in available.items()
    }
    combined = sum(
        float(score) * weight for score, weight, _ in available.values()
    ) / total_weight
    extra_counts = [
        count for key, (_, _, count) in available.items() if key != "placing"
    ]
    components_used = list(available)
    if not extra_counts:
        confidence = "低め" if baseline_used_count < 3 else "総合成績のみ"
    else:
        minimum_sample = min([baseline_used_count, *extra_counts])
        confidence = "高め" if minimum_sample >= 10 else (
            "中程度" if minimum_sample >= 5 else "低め"
        )
        if len(components_used) < 4 and confidence == "高め":
            confidence = "中程度"
        if len(components_used) == 2 and confidence == "中程度":
            confidence = "低め"

    return {
        "score": round(combined, 1),
        "baseline_score": baseline,
        "baseline_sample_count": baseline_count,
        "baseline_used_sample_count": baseline_used_count,
        "distance_score": distance_component["score"],
        "distance_raw_score": distance_component["raw_score"],
        "distance_sample_count": distance_component["sample_count"],
        "distance_condition": distance_component["matched_condition"],
        "going_score": going_component["score"],
        "going_raw_score": going_component["raw_score"],
        "going_sample_count": going_component["sample_count"],
        "going_condition": going_component["matched_condition"],
        "relative_last3f_score": relative_component["score"],
        "relative_last3f_raw_score": relative_component["raw_score"],
        "relative_last3f_sample_count": relative_component["sample_count"],
        "relative_last3f_condition": relative_component["matched_condition"],
        "component_weights": component_weights,
        "components_used": components_used,
        "component_coverage_count": len(components_used),
        "component_coverage_total": 4,
        "component_coverage_ratio": round(len(components_used) / 4, 2),
        "integrated": len(components_used) > 1,
        "confidence": confidence,
        "identity_basis": (
            "official_registration_number" if registration else
            "official_horse_profile_id" if profile_id else
            "horse_name_only" if name else "unverified"
        ),
        "warning": (
            "着順・距離・馬場状態・同一レース内の個別上がり3F順位を統合した説明用指数です。"
            "不足した要素は除外し、残りの重みを再配分します。勝率や将来の着順予測ではありません。"
        ),
    }


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
