"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {
  integratedHorsePerformanceScore,
  summarizeRelativeLast3f
} = require("../horse_scoring.js");

function buildFourFactorFixture() {
  const specifications = [
    [1600, "良", 1],
    [1600, "良", 2],
    [1600, "重", 10],
    [2000, "良", 4],
    [2200, "良", 5],
    [1200, "良", 6]
  ];
  const horseRows = specifications.map(([distance, going, finish], index) => ({
    horse_name: "統合評価馬",
    horse_registration_number: "2020-12345",
    source_url: "race-" + (index + 1) + ".pdf",
    date: "2026-11-" + String(index + 1).padStart(2, "0"),
    track: "東京",
    race_number: 1,
    horse_number: 1,
    surface: "芝",
    distance,
    going,
    finish,
    field_size: 10,
    individual_last3f: 33
  }));
  const raceRows = [];
  for (const target of horseRows.slice(0, 3)) {
    raceRows.push({ ...target, finish: 1, field_size: 3 });
    raceRows.push({
      ...target,
      horse_name: "相手馬A",
      horse_registration_number: "2021-12345",
      horse_number: 2,
      finish: 2,
      individual_last3f: 34,
      field_size: 3
    });
    raceRows.push({
      ...target,
      horse_name: "相手馬B",
      horse_registration_number: "2022-12345",
      horse_number: 3,
      finish: 3,
      individual_last3f: 35,
      field_size: 3
    });
  }
  return { horseRows, raceRows };
}

test("integrates placing, distance, going and relative individual last-3F", () => {
  const { horseRows, raceRows } = buildFourFactorFixture();
  const result = integratedHorsePerformanceScore(
    horseRows,
    raceRows,
    { track: "東京", surface: "芝", distance: 1600, going: "良" },
    { horseRegistrationNumber: "2020-12345" }
  );

  assert.equal(result.integrated, true);
  assert.equal(result.distance_sample_count, 3);
  assert.equal(result.distance_condition, "芝ダート・同距離");
  assert.equal(result.going_sample_count, 5);
  assert.equal(result.going_condition, "芝ダート・馬場状態");
  assert.equal(result.relative_last3f_sample_count, 3);
  assert.equal(result.relative_last3f_condition, "競馬場・芝ダート・距離");
  assert.equal(result.relative_last3f_raw_score, 100);
  assert.ok(result.relative_last3f_score < 100);
  assert.deepEqual(result.component_weights, {
    placing: 0.4,
    distance: 0.2,
    going: 0.2,
    relative_last3f: 0.2
  });
  const expected = result.baseline_score * 0.4
    + result.distance_score * 0.2
    + result.going_score * 0.2
    + result.relative_last3f_score * 0.2;
  assert.equal(result.score, Number(expected.toFixed(1)));
  assert.equal(result.identity_basis, "official_registration_number");
});

test("shrinks strong condition results toward overall form", () => {
  const horseRows = [];
  for (let index = 1; index <= 6; index += 1) {
    const targetCondition = index <= 3;
    horseRows.push({
      horse_name: "縮約確認馬",
      horse_registration_number: "2020-23456",
      source_url: "shrink-" + index + ".pdf",
      date: "2026-11-" + String(index).padStart(2, "0"),
      track: "東京",
      race_number: 1,
      horse_number: 1,
      surface: "芝",
      distance: targetCondition ? 1600 : 2000,
      going: targetCondition ? "良" : "重",
      finish: targetCondition ? 1 : 10,
      field_size: 10
    });
  }
  const result = integratedHorsePerformanceScore(
    horseRows,
    [],
    { surface: "芝", distance: 1600, going: "良" },
    { horseName: "縮約確認馬" }
  );
  assert.equal(result.distance_sample_count, 3);
  assert.equal(result.going_sample_count, 3);
  assert.ok(result.distance_score > result.baseline_score);
  assert.ok(result.distance_score < result.distance_raw_score);
  assert.ok(result.going_score > result.baseline_score);
  assert.ok(result.going_score < result.going_raw_score);
});

test("confidence uses the effective five-start baseline window", () => {
  const horseRows = Array.from({ length: 12 }, (_, index) => ({
    horse_name: "履歴多数馬",
    source_url: "history-" + index + ".pdf",
    date: "2026-10-" + String(index + 1).padStart(2, "0"),
    track: "東京",
    race_number: 1,
    horse_number: 1,
    surface: "芝",
    distance: 1600,
    going: "良",
    finish: (index % 4) + 1,
    field_size: 10
  }));
  const result = integratedHorsePerformanceScore(
    horseRows,
    [],
    { surface: "芝", distance: 1600, going: "良" },
    { horseName: "履歴多数馬" }
  );

  assert.equal(result.baseline_sample_count, 12);
  assert.equal(result.baseline_used_sample_count, 5);
  assert.equal(result.distance_sample_count, 12);
  assert.equal(result.going_sample_count, 12);
  assert.equal(result.confidence, "中程度");
});

test("omits condition components when sample sizes are insufficient", () => {
  const horseRows = [
    { horse_name: "少数馬", date: "2026-11-01", surface: "芝", distance: 1600, going: "良", finish: 1, field_size: 10 },
    { horse_name: "少数馬", date: "2026-11-02", surface: "芝", distance: 1600, going: "良", finish: 2, field_size: 10 }
  ];
  const raceRows = [
    { ...horseRows[0], track: "東京", race_number: 1, horse_number: 1, individual_last3f: 33 },
    { ...horseRows[0], horse_name: "相手馬", horse_number: 2, individual_last3f: 34 }
  ];
  const result = integratedHorsePerformanceScore(
    horseRows,
    raceRows,
    { track: "東京", surface: "芝", distance: 1600, going: "良" },
    { horseName: "少数馬" }
  );
  assert.equal(result.integrated, false);
  assert.equal(result.score, result.baseline_score);
  assert.equal(result.component_weights.placing, 1);
  assert.equal(result.distance_score, null);
  assert.equal(result.going_score, null);
  assert.equal(result.relative_last3f_score, null);
});

test("does not count the same runner twice across different source PDFs", () => {
  const { horseRows, raceRows } = buildFourFactorFixture();
  const duplicated = horseRows.concat({
    ...horseRows[0],
    source_url: "graded-replay-copy.pdf"
  });
  const result = integratedHorsePerformanceScore(
    duplicated,
    raceRows,
    { track: "東京", surface: "芝", distance: 1600, going: "良" },
    { horseRegistrationNumber: "2020-12345" }
  );
  assert.equal(result.baseline_sample_count, 6);
  assert.equal(result.distance_sample_count, 3);
});

test("averages tied last-3F ranks and rejects invalid weights", () => {
  const rows = [
    { horse_name: "同着A", horse_registration_number: "2020-11111", date: "2026-11-01", track: "東京", race_number: 1, surface: "芝", distance: 1600, going: "良", horse_number: 1, finish: 1, field_size: 3, individual_last3f: 33 },
    { horse_name: "同着B", horse_registration_number: "2021-11111", date: "2026-11-01", track: "東京", race_number: 1, surface: "芝", distance: 1600, going: "良", horse_number: 2, finish: 2, field_size: 3, individual_last3f: 33 },
    { horse_name: "相手C", horse_registration_number: "2022-11111", date: "2026-11-01", track: "東京", race_number: 1, surface: "芝", distance: 1600, going: "良", horse_number: 3, finish: 3, field_size: 3, individual_last3f: 34 }
  ];
  const summaries = summarizeRelativeLast3f(rows, { minConditionStarts: 1 });
  const tied = summaries.find(item => item.horse_registration_number === "2020-11111");
  const score = tied.conditions.track.find(item => item.condition === "東京").average_relative_score;
  assert.equal(score, 75);
  assert.throws(() => integratedHorsePerformanceScore([], [], {}, { distanceWeight: -0.1 }), RangeError);
});



test("deduplicates the same race across sources and merges missing runner times", () => {
  const rows = [];
  for (let raceNumber = 1; raceNumber <= 3; raceNumber += 1) {
    const runners = [
      { horse_name: "対象馬", horse_registration_number: "2020-12345",
        date: "2026-11-01", track: "東京", race_number: raceNumber,
        horse_number: 1, finish: 1, field_size: 3, surface: "芝",
        distance: 1600, going: "良", individual_last3f: 33 },
      { horse_name: "相手馬A", horse_registration_number: "2021-12345",
        date: "2026-11-01", track: "東京", race_number: raceNumber,
        horse_number: 2, finish: 2, field_size: 3, surface: "芝",
        distance: 1600, going: "良", individual_last3f: 34 },
      { horse_name: "相手馬B", horse_registration_number: "2022-12345",
        date: "2026-11-01", track: "東京", race_number: raceNumber,
        horse_number: 3, finish: 3, field_size: 3, surface: "芝",
        distance: 1600, going: "良", individual_last3f: 35 }
    ];
    rows.push(...runners.map(row => ({
      ...row, source_url: "annual-results.pdf", individual_last3f: null
    })));
    rows.push(...runners.map(row => ({
      ...row, source_url: "graded-replay.pdf"
    })));
  }

  const summaries = summarizeRelativeLast3f(rows, { minConditionStarts: 1 });
  const target = summaries.find(item => item.horse_registration_number === "2020-12345");
  assert.equal(target.comparable_starts, 3);
  assert.equal(target.conditions.track[0].sample_count, 3);
  assert.equal(target.conditions.track[0].average_relative_score, 100);
});

test("does not rank a race when a finisher's individual last-3F is missing", () => {
  const rows = [
    { horse_name: "計測馬A", date: "2026-11-01", track: "東京", race_number: 1, horse_number: 1, finish: 1, field_size: 3, individual_last3f: 33 },
    { horse_name: "計測欠損馬", date: "2026-11-01", track: "東京", race_number: 1, horse_number: 2, finish: 2, field_size: 3, individual_last3f: null },
    { horse_name: "計測馬C", date: "2026-11-01", track: "東京", race_number: 1, horse_number: 3, finish: 3, field_size: 3, individual_last3f: 35 }
  ];
  assert.deepEqual(summarizeRelativeLast3f(rows, { minConditionStarts: 1 }), []);
});

test("ranks all finishers while excluding an explicitly recorded non-finisher", () => {
  const rows = [
    { horse_name: "完走馬A", date: "2026-11-01", track: "東京", race_number: 1, horse_number: 1, finish: 1, field_size: 4, individual_last3f: 33 },
    { horse_name: "完走馬B", date: "2026-11-01", track: "東京", race_number: 1, horse_number: 2, finish: 2, field_size: 4, individual_last3f: 34 },
    { horse_name: "完走馬C", date: "2026-11-01", track: "東京", race_number: 1, horse_number: 3, finish: 3, field_size: 4, individual_last3f: 35 },
    { horse_name: "中止馬", date: "2026-11-01", track: "東京", race_number: 1, horse_number: 4, finish: null, field_size: 4, individual_last3f: null, result_status: "競走中止" }
  ];
  const summaries = summarizeRelativeLast3f(rows, { minConditionStarts: 1 });
  assert.equal(summaries.length, 3);
  assert.equal(summaries.find(item => item.horse_name === "完走馬A").conditions.track[0].average_relative_score, 100);
  assert.equal(summaries.some(item => item.horse_name === "中止馬"), false);
});

test("does not rank a race when a runner row is missing from the official field", () => {
  const rows = [
    { horse_name: "完走馬A", date: "2026-11-01", track: "東京", race_number: 1, horse_number: 1, finish: 1, field_size: 4, individual_last3f: 33 },
    { horse_name: "完走馬B", date: "2026-11-01", track: "東京", race_number: 1, horse_number: 2, finish: 2, field_size: 4, individual_last3f: 34 },
    { horse_name: "完走馬C", date: "2026-11-01", track: "東京", race_number: 1, horse_number: 3, finish: 3, field_size: 4, individual_last3f: 35 }
  ];
  assert.deepEqual(summarizeRelativeLast3f(rows, { minConditionStarts: 1 }), []);
});

test("browser page inline JavaScript has valid syntax", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const start = html.indexOf("<script>");
  const end = html.indexOf("</script>", start);
  assert.ok(start >= 0 && end > start);
  const inlineScript = html.slice(start + "<script>".length, end);
  assert.doesNotThrow(() => new Function(inlineScript));
});
