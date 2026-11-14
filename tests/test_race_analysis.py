import unittest

from race_analysis import analyze_race_scenario, classify_running_style, parse_corner_positions, distance_band, filter_records, parse_lap_times, performance_score, recent_form_summary, summarize_race_pace, summarize_conditions, summarize_distance_bands, summarize_horses, summarize_races, summarize_race_conditions, summarize_horse_condition_profiles


class RaceAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"horse_name": "サンプルホース", "date": "2026-11-01", "finish": 1,
             "time_seconds": 92.4, "last3f": 34.1, "track": "東京",
             "surface": "芝", "distance": 1600},
            {"horse_name": "サンプルホース", "date": "2026-10-01", "finish": 3,
             "time_seconds": 93.0, "last3f": 33.8, "track": "京都",
             "surface": "芝", "distance": 1600},
            {"horse_name": "別の馬", "date": "2026-10-10", "finish": 8,
             "time_seconds": None, "last3f": None},
        ]


    def test_race_scenario_uses_front_runner_pressure_and_scores(self):
        result = analyze_race_scenario([
            {"horse_name": "逃げA", "running_style": "逃げ"},
            {"horse_name": "逃げB", "running_style": "逃げ"},
            {"horse_name": "先行A", "running_style": "先行"},
            {"horse_name": "差しA", "running_style": "差し"},
            {"horse_name": "追込A", "running_style": "追込"},
        ])
        self.assertEqual(result["scenario"], "ハイペース寄り")
        self.assertEqual(result["estimated_pace_shape"], "前半速い")
        self.assertEqual(result["leader_count"], 2)
        self.assertEqual(result["horse_scores"][3]["scenario_fit_score"], 75)

    def test_race_scenario_keeps_missing_style_unknown(self):
        result = analyze_race_scenario([
            {"horse_name": "脚質不明馬"},
            {"horse_name": "前走先頭馬", "corner_positions": [1, 1, 2], "field_size": 16},
        ])
        self.assertEqual(result["known_style_count"], 1)
        self.assertEqual(result["style_coverage"], 0.5)
        self.assertIsNone(result["horse_scores"][0]["scenario_fit_score"])

    def test_race_scenario_without_styles_does_not_guess(self):
        result = analyze_race_scenario([{"horse_name": "不明馬"}])
        self.assertIsNone(result["estimated_pace_shape"])
        self.assertEqual(result["scenario"], "判定材料不足")

    def test_running_style_parser_and_classification(self):
        self.assertEqual(parse_corner_positions("1-1-2-2"), [1, 1, 2, 2])
        self.assertIsNone(parse_corner_positions("1-二-3"))
        self.assertEqual(classify_running_style([1, 1, 2, 2], 16), "逃げ")
        self.assertEqual(classify_running_style([3, 4, 5, 4], 16), "先行")
        self.assertEqual(classify_running_style([7, 8, 9, 8], 16), "差し")
        self.assertEqual(classify_running_style([13, 14, 15, 14], 16), "追込")
        self.assertIsNone(classify_running_style([1, 2], None))
        self.assertIsNone(classify_running_style([1, 17], 16))

    def test_horse_summary_includes_running_style_distribution(self):
        rows = [
            {"horse_name": "脚質集計馬", "finish": 1, "field_size": 16, "corner_positions": [1, 1, 2, 2]},
            {"horse_name": "脚質集計馬", "finish": 2, "field_size": 16, "corner_positions": [3, 4, 5, 4]},
            {"horse_name": "脚質集計馬", "finish": 4, "field_size": 16, "corner_positions": None},
        ]
        style = summarize_horses(rows)[0]["running_style"]
        self.assertEqual(style["sample_starts"], 2)
        self.assertEqual(style["dominant_style"], "逃げ")
        self.assertEqual(style["counts"], {"逃げ": 1, "先行": 1})

    def test_official_registration_number_separates_same_named_horses(self):
        rows = [
            {"horse_name": "同名馬", "horse_registration_number": "2020-10001", "finish": 1},
            {"horse_name": "同名馬", "horse_registration_number": "2021-10002", "finish": 3},
        ]
        result = summarize_horses(rows)
        self.assertEqual(len(result), 2)
        self.assertEqual(
            {horse["horse_registration_number"] for horse in result},
            {"2020-10001", "2021-10002"},
        )
        self.assertTrue(all(horse["identity_verified"] for horse in result))

    def test_malformed_registration_number_does_not_verify_identity(self):
        horse = summarize_horses([{
            "horse_name": "形式不正番号の馬",
            "horse_registration_number": "2020100001",
            "finish": 1,
        }])[0]
        self.assertIsNone(horse["horse_registration_number"])
        self.assertEqual(horse["identity_basis"], "horse_name_only")
        self.assertFalse(horse["identity_verified"])

    def test_name_only_identity_is_marked_unverified(self):
        horse = summarize_horses([{"horse_name": "登録番号なし馬", "finish": 1}])[0]
        self.assertIsNone(horse["horse_registration_number"])
        self.assertEqual(horse["identity_basis"], "horse_name_only")
        self.assertFalse(horse["identity_verified"])

    def test_horse_summary_does_not_count_duplicate_runner_rows_twice(self):
        row = {
            "horse_name": "重複防止馬", "date": "2026-11-01", "track": "東京",
            "race_number": 1, "horse_number": 3, "source_url": "race.pdf",
            "finish": 1,
        }
        result = summarize_horses([row, dict(row)])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["starts"], 1)
        self.assertEqual(result[0]["wins"], 1)

    def test_horse_summary_keeps_rows_without_complete_runner_identity(self):
        row = {"horse_name": "識別情報不足馬", "date": "2026-11-01", "finish": 2}
        result = summarize_horses([row, dict(row)])
        self.assertEqual(result[0]["starts"], 2)

    def test_horse_summary(self):
        result = summarize_horses(self.rows)
        horse = next(x for x in result if x["horse_name"] == "サンプルホース")
        self.assertEqual(horse["starts"], 2)
        self.assertEqual(horse["wins"], 1)
        self.assertEqual(horse["top3"], 2)
        self.assertEqual(horse["average_finish"], 2.0)
        self.assertEqual(horse["average_last3f"], 33.95)

    def test_official_import_time_field_is_aggregated(self):
        rows = [{"horse_name": "公式取込馬", "finish": 2,
                 "finish_time_seconds": 91.25, "race_last3f": 35.1,
                 "individual_last3f": None}]
        horse = summarize_horses(rows)[0]
        self.assertEqual(horse["average_time_seconds"], 91.25)
        # Race-level closing 3F must never be mislabeled as an individual figure.
        self.assertIsNone(horse["average_last3f"])

    def test_official_individual_last3f_is_aggregated(self):
        rows = [
            {"horse_name": "個別上がり馬", "finish": 1,
             "race_last3f": 34.8, "individual_last3f": 33.7},
            {"horse_name": "個別上がり馬", "finish": 2,
             "race_last3f": 35.2, "individual_last3f": 34.1},
        ]
        horse = summarize_horses(rows)[0]
        self.assertEqual(horse["average_last3f"], 33.9)


    def test_result_statuses_are_counted_without_inventing_finish_places(self):
        rows = [
            {"horse_name": "状態集計馬", "finish": 1},
            {"horse_name": "状態集計馬", "finish": None, "result_status": "競走中止"},
            {"horse_name": "状態集計馬", "finish": None, "result_status": "失格"},
            {"horse_name": "状態集計馬", "finish": None},
        ]
        horse = summarize_horses(rows)[0]
        self.assertEqual(horse["starts"], 4)
        self.assertEqual(horse["placed_starts"], 1)
        self.assertEqual(horse["unplaced_starts"], 3)
        self.assertEqual(horse["result_status_counts"], {"失格": 1, "競走中止": 1})

    def test_missing_metrics_stay_empty(self):
        horse = next(x for x in summarize_horses(self.rows) if x["horse_name"] == "別の馬")
        self.assertIsNone(horse["average_time_seconds"])
        self.assertIsNone(horse["average_last3f"])

    def test_performance_score_normalizes_field_size_and_recent_starts(self):
        rows = [
            {"date": "2026-11-01", "finish": 1, "field_size": 10},
            {"date": "2026-10-01", "finish": 10, "field_size": 10},
        ]
        self.assertEqual(performance_score(rows), 66.7)
        self.assertIsNone(performance_score([{"finish": 1}]))


    def test_condition_summary_requires_minimum_sample_and_ignores_unknowns(self):
        rows = [
            {"going": "良", "finish": 1},
            {"going": "良", "finish": 2},
            {"going": "良", "finish": 4},
            {"going": "重", "finish": 1},
            {"going": None, "finish": 1},
        ]
        result = summarize_conditions(rows, "going", min_starts=3)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["condition"], "良")
        self.assertEqual(result[0]["starts"], 3)
        self.assertEqual(result[0]["wins"], 1)
        self.assertEqual(result[0]["top3"], 2)
        self.assertEqual(result[0]["win_rate"], 0.3333)
        self.assertEqual(result[0]["top3_rate"], 0.6667)

    def test_condition_summary_rejects_zero_minimum(self):
        with self.assertRaises(ValueError):
            summarize_conditions([], "going", min_starts=0)



    def test_recent_form_compares_latest_three_with_previous_three(self):
        rows = [
            {"date": "2026-11-06", "finish": 1},
            {"date": "2026-11-05", "finish": 2},
            {"date": "2026-11-04", "finish": 3},
            {"date": "2026-11-03", "finish": 8},
            {"date": "2026-11-02", "finish": 7},
            {"date": "2026-11-01", "finish": 6},
        ]
        result = recent_form_summary(rows)
        self.assertEqual(result["latest_three_average_finish"], 2.0)
        self.assertEqual(result["previous_three_average_finish"], 7.0)
        self.assertEqual(result["trend"], "improving")
        self.assertEqual(len(result["recent_results"]), 5)

    def test_recent_form_does_not_guess_trend_without_six_results(self):
        result = recent_form_summary([
            {"date": "2026-11-06", "finish": 1},
            {"date": "2026-11-05", "finish": 2},
            {"date": "2026-11-04", "finish": 3},
        ])
        self.assertIsNone(result["trend"])
        self.assertIsNone(result["previous_three_average_finish"])



    def test_distance_band_boundaries_and_invalid_values(self):
        self.assertEqual(distance_band(1400), "短距離（1000〜1400m）")
        self.assertEqual(distance_band(1401), "マイル（1401〜1800m）")
        self.assertEqual(distance_band(1800), "マイル（1401〜1800m）")
        self.assertEqual(distance_band(1801), "中距離（1801〜2200m）")
        self.assertEqual(distance_band(2201), "長距離（2201〜5000m）")
        self.assertIsNone(distance_band(999))
        self.assertIsNone(distance_band(None))
        self.assertIsNone(distance_band(5001))

    def test_distance_band_summary_requires_minimum_sample(self):
        rows = [
            {"distance": 1200, "finish": 1},
            {"distance": 1400, "finish": 2},
            {"distance": 1300, "finish": 3},
            {"distance": 1600, "finish": 1},
        ]
        result = summarize_distance_bands(rows, min_starts=3)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["condition"], "短距離（1000〜1400m）")
        self.assertEqual(result[0]["starts"], 3)
        self.assertEqual(result[0]["wins"], 1)


    def test_filters(self):
        result = filter_records(self.rows, track="東京", surface="芝", distance=1600)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["horse_name"], "サンプルホース")


    def test_lap_parser_and_race_pace_summary(self):
        splits = [12.0, 11.8, 11.7, 11.9, 12.0, 12.2, 12.5, 12.7]
        self.assertEqual(parse_lap_times("12.0-11.8-11.7"), [12.0, 11.8, 11.7])
        self.assertIsNone(parse_lap_times("12.0-2O.8-11.7"))
        result = summarize_race_pace(splits, 1600)
        self.assertEqual(result["first_3f_seconds"], 35.5)
        self.assertEqual(result["last_3f_seconds"], 37.4)
        self.assertEqual(result["pace_shape"], "前半速い")
        self.assertEqual(result["second_minus_first_seconds_per_200m"], 0.5)
        self.assertIsNone(summarize_race_pace(splits, 1800))
        self.assertIsNone(summarize_race_pace(splits, 1600.5))
        self.assertIsNone(summarize_race_pace(None, 1600))

    def test_race_pace_classifies_even_and_latter_faster_patterns(self):
        self.assertEqual(
            summarize_race_pace([12] * 8, 1600)["pace_shape"],
            "ほぼ均等",
        )
        self.assertEqual(
            summarize_race_pace([12.5] * 4 + [11.5] * 4, 1600)["pace_shape"],
            "後半速い",
        )

    def test_race_summary_groups_runners_and_reports_official_top_three(self):
        rows = [
            {"source_url": "pdf-a", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "race_name": "サンプル特別", "surface": "芝", "distance": 1600, "going": "良",
             "horse_number": 2, "horse_name": "勝ち馬", "finish": 1, "finish_time": "1:32.4",
             "finish_time_seconds": 92.4, "race_last3f": 34.1,
             "race_lap_times": [12.0, 11.8, 11.7, 11.9, 12.0, 12.2, 12.5, 12.7]},
            {"source_url": "pdf-a", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "horse_number": 4, "horse_name": "2着馬", "finish": 2, "finish_time": "1:32.8"},
            {"source_url": "pdf-a", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "horse_number": 7, "horse_name": "3着馬", "finish": 3, "finish_time": "1:33.0"},
            {"source_url": "pdf-a", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "horse_number": 8, "horse_name": "競走中止馬", "finish": None},
            {"source_url": "pdf-a", "date": "2026-11-01", "track": "東京", "race_number": 2,
             "horse_number": 1, "horse_name": "別レース馬", "finish": 1},
            {"source_url": "pdf-a", "date": "2026-11-01", "track": "東京", "horse_name": "番号なし"},
        ]
        races = summarize_races(rows)
        self.assertEqual(len(races), 2)
        race = next(item for item in races if item["race_number"] == 1)
        self.assertEqual(race["starters"], 4)
        self.assertEqual(race["placed_starters"], 3)
        self.assertEqual(race["unplaced_starters"], 1)
        self.assertEqual(race["winner_names"], ["勝ち馬"])
        self.assertEqual(race["winning_time_seconds"], 92.4)
        self.assertEqual([item["horse_name"] for item in race["top_three"]], ["勝ち馬", "2着馬", "3着馬"])
        self.assertEqual(race["race_last3f"], 34.1)
        self.assertEqual(race["pace_analysis"]["pace_shape"], "前半速い")
        self.assertEqual(race["pace_analysis"]["first_3f_seconds"], 35.5)

    def test_race_summary_does_not_invent_winner_when_ambiguous(self):
        rows = [
            {"race_number": 1, "horse_name": "A", "finish": 1, "finish_time_seconds": 90},
            {"race_number": 1, "horse_name": "B", "finish": 1, "finish_time_seconds": 91},
        ]
        race = summarize_races(rows)[0]
        self.assertEqual(race["winner_count"], 2)
        self.assertIsNone(race["winning_time_seconds"])



    def test_race_condition_summary_counts_each_race_once(self):
        rows = []
        for race_number, winning_time, last3f in [(1, 92.0, 34.0), (2, 93.0, 35.0), (3, 91.0, 33.0)]:
            for horse_number, finish in [(1, 1), (2, 2), (3, 3), (4, None)]:
                rows.append({
                    "source_url": "same-pdf", "date": "2026-11-01", "track": "東京",
                    "race_number": race_number, "surface": "芝", "distance": 1600,
                    "going": "良", "horse_number": horse_number, "finish": finish,
                    "finish_time_seconds": winning_time if finish == 1 else None,
                    "race_last3f": last3f,
                })
        result = summarize_race_conditions(rows, dimension="track", min_races=3)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["races"], 3)
        self.assertEqual(result[0]["average_starters"], 4.0)
        self.assertEqual(result[0]["average_winning_time_seconds"], 92.0)
        self.assertEqual(result[0]["average_race_last3f"], 34.0)

    def test_race_condition_summary_ignores_missing_values_and_small_samples(self):
        rows = [
            {"source_url": "pdf", "date": "2026-11-01", "track": "東京", "race_number": 1, "going": "良", "finish": 1, "finish_time_seconds": 90},
            {"source_url": "pdf", "date": "2026-11-01", "track": "東京", "race_number": 2, "going": "良", "finish": 1, "finish_time_seconds": None},
            {"source_url": "pdf", "date": "2026-11-01", "track": "東京", "race_number": 3, "going": "良", "finish": 1, "finish_time_seconds": 92},
            {"source_url": "pdf", "date": "2026-11-01", "track": "東京", "race_number": 4, "going": None, "finish": 1},
        ]
        result = summarize_race_conditions(rows, dimension="going", min_races=3)
        self.assertEqual(result[0]["races"], 3)
        self.assertEqual(result[0]["average_winning_time_seconds"], 91.0)
        self.assertEqual(result[0]["average_race_last3f"], None)

    def test_race_condition_summary_validates_dimension_and_minimum(self):
        with self.assertRaises(ValueError):
            summarize_race_conditions([], dimension="horse_name")
        with self.assertRaises(ValueError):
            summarize_race_conditions([], min_races=0)



    def test_horse_condition_profiles_group_by_identity_and_conditions(self):
        rows = [
            {"horse_name": "条件分析馬", "horse_registration_number": "2020-10001",
             "track": "東京", "surface": "芝", "distance": 1600, "going": "良", "finish": 1},
            {"horse_name": "条件分析馬", "horse_registration_number": "2020-10001",
             "track": "東京", "surface": "芝", "distance": 1800, "going": "良", "finish": 2},
            {"horse_name": "条件分析馬", "horse_registration_number": "2020-10001",
             "track": "東京", "surface": "芝", "distance": 1600, "going": "重", "finish": 3},
            {"horse_name": "条件分析馬", "horse_registration_number": "2020-10001",
             "track": "京都", "surface": "ダート", "distance": 1200, "going": "良", "finish": 8},
            {"horse_name": "別個体", "horse_registration_number": "2021-10002",
             "track": "東京", "surface": "芝", "distance": 1600, "going": "良", "finish": 1},
        ]
        profiles = summarize_horse_condition_profiles(rows, min_starts=3)
        self.assertEqual(len(profiles), 2)
        horse = next(item for item in profiles if item["horse_registration_number"] == "2020-10001")
        self.assertTrue(horse["identity_verified"])
        self.assertEqual(horse["conditions"]["track"][0]["condition"], "東京")
        self.assertEqual(horse["conditions"]["track"][0]["starts"], 3)
        self.assertEqual(horse["conditions"]["surface"][0]["condition"], "芝")
        self.assertEqual(horse["conditions"]["going"][0]["condition"], "良")
        self.assertEqual(horse["conditions"]["surface_going"], [])
        self.assertEqual(horse["conditions"]["distance_band"][0]["condition"], "マイル（1401〜1800m）")
        self.assertEqual(horse["conditions"]["track_surface_distance"], [])

    def test_horse_condition_profiles_ignore_duplicate_runner_rows(self):
        row = {
            "horse_name": "重複条件馬", "horse_registration_number": "2020-10001",
            "source_url": "race.pdf", "date": "2026-11-01", "track": "東京",
            "race_number": 1, "horse_number": 3, "surface": "芝",
            "distance": 1600, "going": "良", "finish": 1,
        }
        profile = summarize_horse_condition_profiles([row, dict(row)])[0]
        self.assertEqual(profile["starts"], 1)
        self.assertEqual(profile["conditions"]["track"][0]["starts"], 1)

    def test_horse_condition_profiles_exclude_unknowns_and_validate_minimum(self):
        rows = [
            {"horse_name": "仮集計馬", "track": "東京", "finish": 1},
            {"horse_name": "仮集計馬", "track": "東京", "finish": 2},
            {"horse_name": "仮集計馬", "track": None, "finish": 3},
        ]
        profile = summarize_horse_condition_profiles(rows)[0]
        self.assertFalse(profile["identity_verified"])
        self.assertEqual(profile["conditions"]["track"], [])
        with self.assertRaises(ValueError):
            summarize_horse_condition_profiles(rows, min_starts=0)


if __name__ == "__main__":
    unittest.main()
