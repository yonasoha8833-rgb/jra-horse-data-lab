"use strict";

(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.JraHorseScoring = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const BASELINE_RECENT_LIMIT = 5;

  function numeric(value) {
    if (value === null || value === undefined || (typeof value === "string" && value.trim() === "")) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function registrationNumber(value) {
    const candidate = String(value || "").trim();
    return /^[0-9]{4}-[0-9]{5}$/.test(candidate) ? candidate : null;
  }

  function distanceBand(value) {
    const distance = numeric(value);
    if (distance === null || distance < 1000 || distance > 5000) return null;
    if (distance <= 1400) return "短距離（1000〜1400m）";
    if (distance <= 1800) return "マイル（1401〜1800m）";
    if (distance <= 2200) return "中距離（1801〜2200m）";
    return "長距離（2201〜5000m）";
  }

  function normalizedFinishScore(row) {
    const finish = numeric(row && row.finish);
    const fieldSize = numeric(row && row.field_size);
    if (!Number.isInteger(finish) || !Number.isInteger(fieldSize)
        || fieldSize <= 1 || finish < 1 || finish > fieldSize) return null;
    return 100 * (fieldSize - finish) / (fieldSize - 1);
  }

  function raceRunnerIdentity(row) {
    const date = String(row.date || "").trim();
    const track = String(row.track || "").trim();
    const raceNumber = String(row.race_number || "").trim();
    if (!date || !track || !raceNumber) return null;

    const horseNumber = numeric(row.horse_number);
    let runner;
    if (horseNumber !== null && Number.isInteger(horseNumber) && horseNumber > 0) {
      runner = "number:" + String(horseNumber);
    } else {
      const registration = registrationNumber(row.horse_registration_number);
      const name = String(row.horse_name || "").trim();
      runner = registration ? "registration:" + registration
        : name ? "name:" + name : "";
    }
    return runner ? [date, track, raceNumber, runner].join("|") : null;
  }

  function mergeRunnerRecords(existing, incoming) {
    const merged = { ...existing };
    for (const [field, value] of Object.entries(incoming)) {
      if ((merged[field] === null || merged[field] === undefined || merged[field] === "")
          && value !== null && value !== undefined && value !== "") {
        merged[field] = value;
      }
    }

    if (normalizedFinishScore(merged) === null
        && normalizedFinishScore(incoming) !== null) {
      merged.finish = incoming.finish;
      merged.field_size = incoming.field_size;
    }

    let currentTime = numeric(merged.individual_last3f);
    if (currentTime === null || currentTime <= 0) {
      const fallbackTime = numeric(merged.last3f);
      if (fallbackTime !== null && fallbackTime > 0) {
        currentTime = fallbackTime;
        merged.individual_last3f = merged.last3f;
      }
    }
    let incomingTime = numeric(incoming.individual_last3f);
    let incomingValue = incoming.individual_last3f;
    if (incomingTime === null || incomingTime <= 0) {
      incomingTime = numeric(incoming.last3f);
      incomingValue = incoming.last3f;
    }
    if ((currentTime === null || currentTime <= 0)
        && incomingTime !== null && incomingTime > 0) {
      merged.individual_last3f = incomingValue;
    }
    return merged;
  }

  function performanceScore(records) {
    const valid = [];
    for (const row of records || []) {
      const score = normalizedFinishScore(row);
      if (score !== null) valid.push({ date: String(row.date || ""), score });
    }
    valid.sort((a, b) => b.date.localeCompare(a.date));
    const recent = valid.slice(0, BASELINE_RECENT_LIMIT);
    if (!recent.length) return null;
    let weighted = 0;
    let totalWeight = 0;
    recent.forEach((item, index) => {
      const weight = recent.length - index;
      weighted += item.score * weight;
      totalWeight += weight;
    });
    return Number((weighted / totalWeight).toFixed(1));
  }

  function deduplicateHorseRows(records) {
    const seen = new Map();
    const rows = [];
    for (const row of records || []) {
      const key = raceRunnerIdentity(row);
      if (key !== null && seen.has(key)) {
        const index = seen.get(key);
        rows[index] = mergeRunnerRecords(rows[index], row);
        continue;
      }
      if (key !== null) seen.set(key, rows.length);
      rows.push(row);
    }
    return rows;
  }

  function summarizeRelativeLast3f(records, options) {
    const settings = options || {};
    const minRaceRunners = settings.minRaceRunners === undefined ? 3 : settings.minRaceRunners;
    const minConditionStarts = settings.minConditionStarts === undefined ? 3 : settings.minConditionStarts;
    if (!Number.isInteger(minRaceRunners) || minRaceRunners < 2) {
      throw new RangeError("minRaceRunners must be an integer of at least 2");
    }
    if (!Number.isInteger(minConditionStarts) || minConditionStarts < 1) {
      throw new RangeError("minConditionStarts must be a positive integer");
    }

    const races = new Map();
    for (const row of records || []) {
      const date = String(row.date || "").trim();
      const track = String(row.track || "").trim();
      const raceNumber = String(row.race_number || "").trim();
      if (!date || !track || !raceNumber) continue;
      // Multiple official PDFs may contain the same race; count it once.
      const key = [date, track, raceNumber].join("|");
      if (!races.has(key)) races.set(key, []);
      races.get(key).push(row);
    }

    const scored = [];
    for (const raceRows of races.values()) {
      const unique = new Map();
      raceRows.forEach((row, index) => {
        const identity = raceRunnerIdentity(row) || ("row:" + index);
        if (unique.has(identity)) {
          unique.set(identity, mergeRunnerRecords(unique.get(identity), row));
        } else {
          unique.set(identity, row);
        }
      });
      const runners = Array.from(unique.values());
      const fieldSizes = runners.map(row => numeric(row.field_size));
      if (fieldSizes.length < minRaceRunners
          || fieldSizes.some(size => !Number.isInteger(size))
          || new Set(fieldSizes).size !== 1
          || fieldSizes[0] !== runners.length) continue;

      const finishers = runners.filter(row => normalizedFinishScore(row) !== null);
      const explicitNonFinishers = runners.filter(row =>
        String(row.result_status || "").trim() !== ""
      );
      // Rank only when every official runner is accounted for and every finisher
      // has an individual time. Unclassified rows and missing times invalidate
      // the race rather than shrinking the comparison field silently.
      if (finishers.length < minRaceRunners
          || finishers.length + explicitNonFinishers.length !== runners.length) continue;

      const ranked = finishers.map((row, index) => ({
        index,
        time: numeric(row.individual_last3f ?? row.last3f)
      })).sort((a, b) => a.time - b.time);
      if (ranked.some(item => item.time === null || item.time <= 0)) continue;

      const ranks = new Map();
      let position = 0;
      while (position < ranked.length) {
        let end = position + 1;
        while (end < ranked.length && ranked[end].time === ranked[position].time) end += 1;
        const averageRank = ((position + 1) + end) / 2;
        for (let index = position; index < end; index += 1) {
          ranks.set(ranked[index].index, averageRank);
        }
        position = end;
      }
      for (const [index, row] of finishers.entries()) {
        if (!ranks.has(index)) continue;
        scored.push({
          ...row,
          relative_last3f_score: 100 * (ranked.length - ranks.get(index)) / (ranked.length - 1)
        });
      }
    }

    const horses = new Map();
    const seen = new Set();
    for (const row of scored) {
      const runnerKey = raceRunnerIdentity(row);
      if (runnerKey !== null) {
        if (seen.has(runnerKey)) continue;
        seen.add(runnerKey);
      }
      const name = String(row.horse_name || "").trim();
      if (!name) continue;
      const registration = registrationNumber(row.horse_registration_number);
      const identity = registration ? "registration:" + registration : "name:" + name;
      if (!horses.has(identity)) horses.set(identity, []);
      horses.get(identity).push(row);
    }

    const dimensions = {
      track: "track",
      surface: "surface",
      distance_band: "distance_band",
      going: "going",
      surface_going: "surface_going",
      track_surface_distance: "track_surface_distance"
    };
    const results = [];
    for (const [identity, rows] of horses.entries()) {
      const prepared = rows.map(row => {
        const item = { ...row };
        const band = distanceBand(row.distance);
        if (band) item.distance_band = band;
        if (row.surface && row.going) item.surface_going = row.surface + "・" + row.going;
        const distance = numeric(row.distance);
        if (row.track && row.surface && distance !== null && Number.isInteger(distance)) {
          item.track_surface_distance = row.track + " " + row.surface + " " + distance + "m";
        }
        return item;
      });
      const conditions = {};
      for (const [label, field] of Object.entries(dimensions)) {
        const groups = new Map();
        for (const row of prepared) {
          const condition = String(row[field] || "").trim();
          if (!condition) continue;
          if (!groups.has(condition)) groups.set(condition, []);
          groups.get(condition).push(row);
        }
        conditions[label] = Array.from(groups.entries())
          .filter(([, items]) => items.length >= minConditionStarts)
          .map(([condition, items]) => ({
            condition,
            sample_count: items.length,
            average_relative_score: Number((
              items.reduce((sum, item) => sum + item.relative_last3f_score, 0) / items.length
            ).toFixed(1))
          }))
          .sort((a, b) => b.sample_count - a.sample_count
            || a.condition.localeCompare(b.condition, "ja"));
      }
      const isRegistration = identity.startsWith("registration:");
      results.push({
        horse_name: String(rows[0].horse_name || "").trim(),
        horse_registration_number: isRegistration ? identity.slice("registration:".length) : null,
        identity_basis: isRegistration ? "official_registration_number" : "horse_name_only",
        conditions
      });
    }
    return results;
  }

  function scoreConditionComponent(rows, candidates, baseline, minStarts, priorStrength) {
    for (const [label, matches] of candidates) {
      const scores = rows.filter(matches).map(normalizedFinishScore).filter(value => value !== null);
      if (scores.length < minStarts) continue;
      const rawScore = scores.reduce((sum, value) => sum + value, 0) / scores.length;
      const adjusted = (rawScore * scores.length + baseline * priorStrength)
        / (scores.length + priorStrength);
      return {
        score: Number(adjusted.toFixed(1)),
        raw_score: Number(rawScore.toFixed(1)),
        sample_count: scores.length,
        matched_condition: label
      };
    }
    return { score: null, raw_score: null, sample_count: 0, matched_condition: null };
  }

  function integratedHorsePerformanceScore(horseRecords, raceRecords, targetConditions, options) {
    const settings = options || {};
    const minConditionStarts = settings.minConditionStarts === undefined ? 3 : settings.minConditionStarts;
    const priorStrength = settings.priorStrength === undefined ? 5 : settings.priorStrength;
    const weights = {
      placing: settings.placingWeight === undefined ? 0.4 : settings.placingWeight,
      distance: settings.distanceWeight === undefined ? 0.2 : settings.distanceWeight,
      going: settings.goingWeight === undefined ? 0.2 : settings.goingWeight,
      relative_last3f: settings.relativeLast3fWeight === undefined ? 0.2 : settings.relativeLast3fWeight
    };
    if (!Number.isInteger(minConditionStarts) || minConditionStarts < 1) {
      throw new RangeError("minConditionStarts must be a positive integer");
    }
    if (typeof priorStrength !== "number" || !Number.isFinite(priorStrength) || priorStrength < 0) {
      throw new RangeError("priorStrength must be a finite non-negative number");
    }
    for (const weight of Object.values(weights)) {
      if (typeof weight !== "number" || !Number.isFinite(weight) || weight < 0) {
        throw new RangeError("component weights must be finite non-negative numbers");
      }
    }
    if (weights.placing <= 0) throw new RangeError("placingWeight must be greater than zero");

    const rows = deduplicateHorseRows(horseRecords);
    const validRows = rows.filter(row => normalizedFinishScore(row) !== null);
    const baseline = performanceScore(validRows);
    const baselineCount = validRows.length;
    const baselineUsedCount = Math.min(BASELINE_RECENT_LIMIT, baselineCount);
    if (baseline === null) {
      return {
        score: null, baseline_score: null, baseline_sample_count: 0, baseline_used_sample_count: 0,
        distance_score: null, distance_raw_score: null, distance_sample_count: 0, distance_condition: null,
        going_score: null, going_raw_score: null, going_sample_count: 0, going_condition: null,
        relative_last3f_score: null, relative_last3f_raw_score: null,
        relative_last3f_sample_count: 0, relative_last3f_condition: null,
        component_weights: {}, components_used: [], integrated: false, confidence: "判定材料不足"
      };
    }

    const target = targetConditions && typeof targetConditions === "object" ? targetConditions : {};
    const surface = String(target.surface || "").trim();
    const going = String(target.going || "").trim();
    const distance = numeric(target.distance);
    const exactDistance = distance !== null && Number.isInteger(distance) ? distance : null;
    const band = distanceBand(distance);

    const distanceCandidates = [];
    if (surface && exactDistance !== null) {
      distanceCandidates.push(["芝ダート・同距離", row =>
        row.surface === surface && numeric(row.distance) === exactDistance]);
    }
    if (surface && band) {
      distanceCandidates.push(["芝ダート・距離帯", row =>
        row.surface === surface && distanceBand(row.distance) === band]);
    }
    if (exactDistance !== null) {
      distanceCandidates.push(["同距離", row => numeric(row.distance) === exactDistance]);
    }
    if (band) {
      distanceCandidates.push(["距離帯", row => distanceBand(row.distance) === band]);
    }
    const goingCandidates = [];
    if (surface && going) {
      goingCandidates.push(["芝ダート・馬場状態", row =>
        row.surface === surface && row.going === going]);
    }
    if (going) goingCandidates.push(["馬場状態", row => row.going === going]);

    const distanceComponent = scoreConditionComponent(
      validRows, distanceCandidates, baseline, minConditionStarts, priorStrength
    );
    const goingComponent = scoreConditionComponent(
      validRows, goingCandidates, baseline, minConditionStarts, priorStrength
    );

    const registration = registrationNumber(settings.horseRegistrationNumber);
    let name = String(settings.horseName || "").trim();
    if (!name && rows.length) name = String(rows[0].horse_name || "").trim();
    let relativeComponent = {
      score: null, raw_score: null, sample_count: 0, matched_condition: null
    };
    if (registration || name) {
      const relativeSummaries = Array.isArray(settings.relativeSummaries)
        ? settings.relativeSummaries
        : summarizeRelativeLast3f(raceRecords, { minConditionStarts });
      const horseSummary = relativeSummaries.find(item =>
        (registration && item.horse_registration_number === registration)
        || (!registration && !item.horse_registration_number && item.horse_name === name)
      );
      if (horseSummary) {
        const track = String(target.track || "").trim();
        const candidates = [];
        if (track && surface && exactDistance !== null) {
          candidates.push(["競馬場・芝ダート・距離", "track_surface_distance",
            track + " " + surface + " " + exactDistance + "m"]);
        }
        if (surface && going) {
          candidates.push(["芝ダート・馬場状態", "surface_going", surface + "・" + going]);
        }
        if (band) candidates.push(["距離帯", "distance_band", band]);
        if (going) candidates.push(["馬場状態", "going", going]);
        if (track) candidates.push(["競馬場", "track", track]);
        if (surface) candidates.push(["芝ダート", "surface", surface]);

        let selected = null;
        for (const [label, dimension, condition] of candidates) {
          const match = (horseSummary.conditions[dimension] || []).find(item =>
            item.condition === condition && item.sample_count >= minConditionStarts
          );
          if (match) {
            selected = { label, match };
            break;
          }
        }
        if (selected) {
          const rawScore = selected.match.average_relative_score;
          const sampleCount = selected.match.sample_count;
          const adjusted = (rawScore * sampleCount + 50 * priorStrength)
            / (sampleCount + priorStrength);
          relativeComponent = {
            score: Number(adjusted.toFixed(1)),
            raw_score: Number(rawScore.toFixed(1)),
            sample_count: sampleCount,
            matched_condition: selected.label
          };
        }
      }
    }

    const components = {
      placing: { score: baseline, weight: weights.placing, count: baselineCount },
      distance: { score: distanceComponent.score, weight: weights.distance, count: distanceComponent.sample_count },
      going: { score: goingComponent.score, weight: weights.going, count: goingComponent.sample_count },
      relative_last3f: { score: relativeComponent.score, weight: weights.relative_last3f, count: relativeComponent.sample_count }
    };
    const available = Object.entries(components)
      .filter(([, item]) => item.score !== null && item.weight > 0);
    const totalWeight = available.reduce((sum, [, item]) => sum + item.weight, 0);
    const componentWeights = {};
    for (const [key, item] of available) {
      componentWeights[key] = Number((item.weight / totalWeight).toFixed(4));
    }
    const combined = available.reduce((sum, [, item]) =>
      sum + item.score * item.weight, 0) / totalWeight;
    const extraCounts = available
      .filter(([key]) => key !== "placing")
      .map(([, item]) => item.count);
    let confidence = "総合成績のみ";
    if (extraCounts.length) {
      const minimumSample = Math.min(baselineUsedCount, ...extraCounts);
      confidence = minimumSample >= 10 ? "高め" : minimumSample >= 5 ? "中程度" : "低め";
    }
    const componentsUsed = available.map(([key]) => key);
    return {
      score: Number(combined.toFixed(1)),
      baseline_score: baseline,
      baseline_sample_count: baselineCount,
      baseline_used_sample_count: baselineUsedCount,
      distance_score: distanceComponent.score,
      distance_raw_score: distanceComponent.raw_score,
      distance_sample_count: distanceComponent.sample_count,
      distance_condition: distanceComponent.matched_condition,
      going_score: goingComponent.score,
      going_raw_score: goingComponent.raw_score,
      going_sample_count: goingComponent.sample_count,
      going_condition: goingComponent.matched_condition,
      relative_last3f_score: relativeComponent.score,
      relative_last3f_raw_score: relativeComponent.raw_score,
      relative_last3f_sample_count: relativeComponent.sample_count,
      relative_last3f_condition: relativeComponent.matched_condition,
      component_weights: componentWeights,
      components_used: componentsUsed,
      integrated: componentsUsed.length > 1,
      confidence,
      identity_basis: registration ? "official_registration_number"
        : name ? "horse_name_only" : "unverified",
      warning: "着順・距離・馬場状態・同一レース内の個別上がり3F順位を統合した説明用指数です。不足した要素は除外し、残りの重みを再配分します。勝率や将来の着順予測ではありません。"
    };
  }


  function summarizeForwardPosition(records, options) {
    const settings = options || {};
    const minRaceRunners = settings.minRaceRunners === undefined ? 3 : settings.minRaceRunners;
    const minStarts = settings.minStarts === undefined ? 1 : settings.minStarts;
    if (!Number.isInteger(minRaceRunners) || minRaceRunners < 2) {
      throw new RangeError("minRaceRunners must be an integer of at least 2");
    }
    if (!Number.isInteger(minStarts) || minStarts < 1) {
      throw new RangeError("minStarts must be a positive integer");
    }

    const races = new Map();
    for (const row of records || []) {
      const date = String(row.date || "").trim();
      const track = String(row.track || "").trim();
      const raceNumber = String(row.race_number || "").trim();
      if (!date || !track || !raceNumber) continue;
      const key = [date, track, raceNumber].join("|");
      if (!races.has(key)) races.set(key, []);
      races.get(key).push(row);
    }

    const samples = new Map();
    for (const rows of races.values()) {
      const fieldSize = numeric(rows[0] && rows[0].field_size);
      if (!Number.isInteger(fieldSize) || fieldSize < minRaceRunners
          || rows.some(row => numeric(row.field_size) !== fieldSize)) continue;
      const byNumber = new Map();
      let invalid = false;
      for (const row of rows) {
        const number = numeric(row.horse_number);
        if (!Number.isInteger(number) || number < 1 || number > fieldSize
            || byNumber.has(number)) {
          invalid = true;
          break;
        }
        byNumber.set(number, row);
      }
      // A partial or duplicated field cannot establish a reliable position percentile.
      if (invalid || byNumber.size !== fieldSize) continue;

      for (const row of byNumber.values()) {
        let position = numeric(row.corner_3);
        if (position === null) {
          const text = String(row.corner_positions || "").trim();
          const first = text.match(/^\s*(\d{1,2})/);
          position = first ? Number(first[1]) : null;
        }
        if (!Number.isInteger(position) || position < 1 || position > fieldSize) continue;
        const registration = registrationNumber(row.horse_registration_number);
        const name = String(row.horse_name || "").trim();
        if (!registration && !name) continue;
        const identity = registration ? "registration:" + registration : "name:" + name;
        if (!samples.has(identity)) {
          samples.set(identity, { horse_name: name, horse_registration_number: registration,
            identity_basis: registration ? "official_registration_number" : "horse_name_only",
            positions: [] });
        }
        samples.get(identity).positions.push({
          date: String(row.date || ""),
          position,
          field_size: fieldSize,
          score: 100 * (fieldSize - position) / (fieldSize - 1)
        });
      }
    }

    return Array.from(samples.values())
      .filter(item => item.positions.length >= minStarts)
      .map(item => {
        const ordered = item.positions.slice().sort((a, b) => b.date.localeCompare(a.date));
        const averagePosition = ordered.reduce((sum, sample) => sum + sample.position, 0) / ordered.length;
        const averageScore = ordered.reduce((sum, sample) => sum + sample.score, 0) / ordered.length;
        return {
          horse_name: item.horse_name,
          horse_registration_number: item.horse_registration_number,
          identity_basis: item.identity_basis,
          sample_count: ordered.length,
          average_early_position: Number(averagePosition.toFixed(2)),
          average_forward_position_score: Number(averageScore.toFixed(1)),
          recent_samples: ordered
        };
      })
      .sort((a, b) => b.average_forward_position_score - a.average_forward_position_score
        || a.horse_name.localeCompare(b.horse_name, "ja"));
  }

  function summarizeOfficialRaceLapSplits(records) {
    const races = new Map();
    for (const row of records || []) {
      const date = String(row.date || "").trim();
      const track = String(row.track || "").trim();
      const raceNumber = String(row.race_number || "").trim();
      if (!date || !track || !raceNumber) continue;
      const key = [date, track, raceNumber].join("|");
      if (!races.has(key)) races.set(key, []);
      races.get(key).push(row);
    }

    const summaries = [];
    for (const [key, rows] of races.entries()) {
      const candidates = rows.map(row => Array.isArray(row.race_lap_times)
        ? row.race_lap_times.map(numeric) : null);
      if (!candidates.length || candidates.some(laps =>
        !laps || laps.length < 5 || laps.some(value => value === null || value <= 0)
      )) continue;
      const signature = candidates[0].join(",");
      if (candidates.some(laps => laps.join(",") !== signature)) continue;
      const laps = candidates[0];
      const sum = values => Number(values.reduce((total, value) => total + value, 0).toFixed(1));
      summaries.push({
        race_key: key,
        date: String(rows[0].date),
        track: String(rows[0].track),
        race_number: rows[0].race_number,
        surface: rows[0].surface || null,
        distance: numeric(rows[0].distance),
        going: rows[0].going || null,
        runner_record_count: rows.length,
        lap_count: laps.length,
        first3f: sum(laps.slice(0, 3)),
        first5f: sum(laps.slice(0, 5)),
        final3f: sum(laps.slice(-3)),
        lap_times: laps
      });
    }
    return summaries.sort((a, b) => b.date.localeCompare(a.date)
      || String(a.track).localeCompare(String(b.track), "ja")
      || Number(a.race_number) - Number(b.race_number));
  }

  function summarizeObservedBlinkerChanges(records) {
    const horses = new Map();
    for (const row of records || []) {
      const registration = registrationNumber(row.horse_registration_number);
      const date = String(row.date || "").trim();
      if (!registration || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(date)
          || typeof row.blinkers !== "boolean") continue;
      if (!horses.has(registration)) horses.set(registration, []);
      horses.get(registration).push(row);
    }
    const events = [];
    for (const [registration, rows] of horses.entries()) {
      const byDate = new Map();
      rows.forEach(row => {
        const date = String(row.date);
        if (!byDate.has(date)) byDate.set(date, []);
        byDate.get(date).push(row);
      });
      const ordered = Array.from(byDate.entries())
        .filter(([, sameDay]) => sameDay.length === 1)
        .map(([date, sameDay]) => ({ date, row: sameDay[0] }))
        .sort((a, b) => a.date.localeCompare(b.date));
      for (let index = 1; index < ordered.length; index += 1) {
        const previous = ordered[index - 1];
        const current = ordered[index];
        if (previous.row.blinkers !== false || current.row.blinkers !== true) continue;
        events.push({
          horse_registration_number: registration,
          horse_name: String(current.row.horse_name || previous.row.horse_name || ""),
          previous_date: previous.date,
          observed_change_date: current.date,
          previous_race: previous.row.race_number ?? null,
          observed_race: current.row.race_number ?? null,
          status: "初着用候補（連続して確認できた記録上の変化）",
          warning: "全出走履歴を確認できた保証はないため、初着用の確定判定ではありません。"
        });
      }
    }
    return events.sort((a, b) => b.observed_change_date.localeCompare(a.observed_change_date)
      || a.horse_name.localeCompare(b.horse_name, "ja"));
  }

  return {
    distanceBand,
    performanceScore,
    summarizeRelativeLast3f,
    summarizeForwardPosition,
    summarizeOfficialRaceLapSplits,
    summarizeObservedBlinkerChanges,
    integratedHorsePerformanceScore
  };
});
