import unittest

from race_analysis import analyze_race_scenario, classify_running_style, parse_corner_positions, distance_band, filter_records, parse_lap_times, performance_score, condition_adjusted_performance_score, integrated_condition_last3f_score, integrated_horse_performance_score, recent_form_summary, summarize_race_pace, summarize_conditions, summarize_distance_bands, summarize_horses, summarize_races, summarize_race_conditions, summarize_horse_condition_profiles, summarize_relative_last3f, summarize_running_style_pace_performance


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



    def test_style_pace_performance_uses_validated_race_laps(self):
        rows = []
        for race_number, finishes in [(1, [1, 2, 4]), (2, [2, 1, 3]), (3, [3, 1, 2])]:
            for horse_number, finish in enumerate(finishes, 1):
                rows.append({
                    "source_url": "pace.pdf", "date": "2026-11-01", "track": "東京",
                    "race_number": race_number, "horse_number": horse_number,
                    "horse_name": f"馬{horse_number}", "finish": finish,
                    "field_size": 16, "corner_positions": [1, 1, 1] if horse_number == 1 else [5, 5, 5],
                    "distance": 1600, "race_lap_times": [12.5] * 4 + [11.5] * 4,
                    "individual_last3f": 34.0 + horse_number / 10,
                })
        # A missing field size on the first runner must not override a valid
        # field size supplied by another runner in the same race.
        rows[0].pop("field_size")
        result = summarize_running_style_pace_performance(rows, min_starts=3)
        front = next(item for item in result if item["running_style"] == "逃げ")
        self.assertEqual(front["pace_shape"], "後半速い")
        self.assertEqual(front["starts"], 3)
        self.assertEqual(front["wins"], 1)
        self.assertEqual(front["top3"], 3)
        self.assertEqual(front["win_rate"], 0.3333)
        self.assertEqual(front["top3_rate"], 1.0)
        self.assertEqual(front["individual_last3f_sample_count"], 3)
        self.assertEqual(front["average_individual_last3f"], 34.1)

    def test_style_pace_performance_uses_source_pdf_when_race_number_missing(self):
        rows = []
        for horse_number, finish in enumerate([1, 2, 3], 1):
            rows.append({
                "source_url": "https://jra.example/race.pdf",
                "date": "2026-11-01", "track": "東京",
                "horse_number": horse_number, "horse_name": f"馬{horse_number}",
                "finish": finish, "field_size": 16,
                "corner_positions": [1, 1, 1] if horse_number == 1 else [5, 5, 5],
                "distance": 1600, "race_lap_times": [12.5] * 4 + [11.5] * 4,
            })
        result = summarize_running_style_pace_performance(rows, min_starts=1)
        self.assertEqual(sum(item["starts"] for item in result), 3)

    def test_style_pace_performance_excludes_missing_laps_and_small_samples(self):
        rows = [
            {"race_number": 1, "horse_name": "不明", "finish": 1,
             "corner_positions": [1, 1], "field_size": 8, "distance": 1600},
        ]
        self.assertEqual(summarize_running_style_pace_performance(rows), [])
        with self.assertRaises(ValueError):
            summarize_running_style_pace_performance([], min_starts=0)

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
                 "last3f": 35.1, "individual_last3f": None}]
        horse = summarize_horses(rows)[0]
        self.assertEqual(horse["average_time_seconds"], 91.25)
        # Race-level closing 3F must never be mislabeled as an individual figure.
        self.assertIsNone(horse["average_last3f"])

    def test_condition_summary_does_not_relabel_race_last3f_as_individual(self):
        rows = [
            {"going": "良", "finish": 1, "individual_last3f": None,
             "last3f": 35.1, "race_last3f": 35.1},
            {"going": "良", "finish": 2, "individual_last3f": None,
             "last3f": 35.1, "race_last3f": 35.1},
            {"going": "良", "finish": 3, "individual_last3f": None,
             "last3f": 35.1, "race_last3f": 35.1},
        ]
        result = summarize_conditions(rows, "going", min_starts=3)
        self.assertEqual(result[0]["individual_last3f_sample_count"], 0)
        self.assertIsNone(result[0]["average_individual_last3f"])

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
        
    def test_condition_summary_aggregates_individual_last3f_without_filling_missing(self):
        rows = [
            {"going": "良", "finish": 1, "individual_last3f": 33.4},
            {"going": "良", "finish": 2, "individual_last3f": 34.2},
            {"going": "良", "finish": 4, "individual_last3f": None},
            {"going": "稍重", "finish": 1, "individual_last3f": 32.8},
        ]
        result = summarize_conditions(rows, "going", min_starts=3)
        self.assertEqual(result[0]["individual_last3f_sample_count"], 2)
        self.assertEqual(result[0]["average_individual_last3f"], 33.8)
        self.assertEqual(result[0]["starts"], 3)

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
        self.assertEqual(parse_lap_times("12.0 11.8 11.7"), [12.0, 11.8, 11.7])
        self.assertEqual(parse_lap_times("12.0, 11.8, 11.7"), [12.0, 11.8, 11.7])
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
        profile = summarize_horse_condition_profiles([row, dict(row)], min_starts=1)[0]
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


    def test_condition_adjusted_score_shrinks_small_condition_sample_to_overall(self):
        rows = [
            {"date": "2026-11-01", "finish": 1, "field_size": 10, "surface": "芝", "distance": 1600},
            {"date": "2026-10-01", "finish": 10, "field_size": 10, "surface": "芝", "distance": 1600},
            {"date": "2026-09-01", "finish": 10, "field_size": 10, "surface": "ダート", "distance": 1200},
            {"date": "2026-11-04", "finish": 10, "field_size": 10, "surface": "ダート", "distance": 1200},
            {"date": "2026-11-03", "finish": 10, "field_size": 10, "surface": "ダート", "distance": 1200},
            {"date": "2026-11-02", "finish": 10, "field_size": 10, "surface": "ダート", "distance": 1200},
        ]
        result = condition_adjusted_performance_score(
            rows, {"surface": "芝", "distance": 1600}, min_condition_starts=2, prior_strength=5
        )
        self.assertEqual(result["condition_sample_count"], 2)
        self.assertEqual(result["matched_condition"], "芝ダート・距離帯")
        self.assertGreater(result["score"], result["overall_score"])
        self.assertLess(result["score"], result["condition_score"])

    def test_condition_adjusted_score_uses_most_specific_supported_group(self):
        rows = [
            {"finish": 1, "field_size": 10, "track": "東京", "surface": "芝", "distance": 1600}
            for _ in range(3)
        ] + [
            {"finish": 8, "field_size": 10, "track": "京都", "surface": "芝", "distance": 1600}
            for _ in range(3)
        ]
        result = condition_adjusted_performance_score(
            rows, {"track": "東京", "surface": "芝", "distance": 1600},
            min_condition_starts=3,
        )
        self.assertEqual(result["matched_condition"], "競馬場・芝ダート・距離")
        self.assertEqual(result["condition_score"], 100.0)
        self.assertEqual(result["condition_sample_count"], 3)

    def test_condition_adjusted_score_falls_back_without_enough_matching_data(self):
        result = condition_adjusted_performance_score(
            [{"finish": 2, "field_size": 10, "surface": "芝"}],
            {"surface": "芝"}, min_condition_starts=3,
        )
        self.assertEqual(result["score"], result["overall_score"])
        self.assertIsNone(result["condition_score"])
        self.assertEqual(result["confidence"], "総合成績のみ")

    def test_condition_adjusted_score_handles_missing_baseline_and_invalid_options(self):
        self.assertIsNone(condition_adjusted_performance_score([], {"surface": "芝"})["score"])
        with self.assertRaises(ValueError):
            condition_adjusted_performance_score([], {}, min_condition_starts=0)
        with self.assertRaises(ValueError):
            condition_adjusted_performance_score([], {}, prior_strength=-1)


    def test_relative_last3f_compares_runners_within_same_race(self):
        rows = [
            {"horse_name": "最速馬", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "surface": "芝", "distance": 1600, "going": "良", "horse_number": 1,
             "finish": 1, "field_size": 3, "individual_last3f": None, "last3f": 33.0},
            {"horse_name": "中間馬", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "surface": "芝", "distance": 1600, "going": "良", "horse_number": 2,
             "finish": 2, "field_size": 3, "last3f": 34.0},
            {"horse_name": "最遅馬", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "surface": "芝", "distance": 1600, "going": "良", "horse_number": 3,
             "finish": 3, "field_size": 3, "last3f": 35.0},
        ]
        result = summarize_relative_last3f(rows)
        scores = {item["horse_name"]: item["average_relative_score"] for item in result}
        self.assertEqual(scores, {"最速馬": 100.0, "中間馬": 50.0, "最遅馬": 0.0})
        self.assertEqual(result[0]["conditions"]["surface"], [])

    def test_relative_last3f_does_not_rank_using_race_level_legacy_last3f(self):
        rows = [
            {
                "horse_name": f"レース上がり誤認防止馬{number}",
                "date": "2026-11-01", "track": "東京", "race_number": 1,
                "horse_number": number, "finish": number, "field_size": 3,
                "individual_last3f": None, "last3f": 33.0 + number,
                "race_last3f": 35.1,
            }
            for number in range(1, 4)
        ]
        self.assertEqual(summarize_relative_last3f(rows, min_condition_starts=1), [])

    def test_relative_last3f_excludes_incomplete_races_and_tolerates_ties(self):
        rows = [
            {"horse_name": "同着A", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "horse_number": 1, "finish": 1, "field_size": 4, "last3f": 33.0},
            {"horse_name": "同着B", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "horse_number": 2, "finish": 2, "field_size": 4, "last3f": 33.0},
            {"horse_name": "3番手", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "horse_number": 3, "finish": 3, "field_size": 4, "last3f": 34.0},
            {"horse_name": "データ不足", "date": "2026-11-02", "track": "東京", "race_number": 2,
             "horse_number": 1, "finish": 1, "field_size": 3, "last3f": 34.0},
            {"horse_name": "欠損", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "horse_number": 4, "finish": None, "field_size": 4, "last3f": None,
             "result_status": "競走中止"},
        ]
        result = summarize_relative_last3f(rows)
        scores = {item["horse_name"]: item["average_relative_score"] for item in result}
        self.assertAlmostEqual(scores["同着A"], 75.0)
        self.assertAlmostEqual(scores["同着B"], 75.0)
        self.assertEqual(scores["3番手"], 0.0)
        self.assertNotIn("データ不足", scores)
        self.assertNotIn("欠損", scores)

    def test_relative_last3f_rejects_missing_finisher_times_and_missing_field_rows(self):
        rows = [
            {"horse_name": "計測馬A", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "horse_number": 1, "finish": 1, "field_size": 3, "individual_last3f": 33.0},
            {"horse_name": "計測欠損馬", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "horse_number": 2, "finish": 2, "field_size": 3, "individual_last3f": None},
            {"horse_name": "計測馬C", "date": "2026-11-01", "track": "東京", "race_number": 1,
             "horse_number": 3, "finish": 3, "field_size": 3, "individual_last3f": 35.0},
            {"horse_name": "欠落レースA", "date": "2026-11-02", "track": "東京", "race_number": 2,
             "horse_number": 1, "finish": 1, "field_size": 3, "individual_last3f": 33.0},
            {"horse_name": "欠落レースB", "date": "2026-11-02", "track": "東京", "race_number": 2,
             "horse_number": 2, "finish": 2, "field_size": 3, "individual_last3f": 34.0},
        ]
        self.assertEqual(
            summarize_relative_last3f(rows, min_condition_starts=1),
            [],
        )

    def test_relative_last3f_requires_minimum_comparable_starts_for_conditions(self):
        rows = []
        for race_number, times in [(1, [33.0, 34.0, 35.0]), (2, [33.5, 34.5, 35.5])]:
            for index, value in enumerate(times):
                rows.append({
                    "horse_name": "対象馬" if index == 0 else f"相手{race_number}-{index}",
                    "date": f"2026-11-0{race_number}", "track": "東京",
                    "race_number": race_number, "surface": "芝", "distance": 1600,
                    "horse_number": index + 1, "finish": index + 1, "field_size": 3,
                    "last3f": value,
                })
        target = next(item for item in summarize_relative_last3f(rows) if item["horse_name"] == "対象馬")
        self.assertEqual(target["comparable_starts"], 2)
        self.assertEqual(target["conditions"]["surface"], [])
        with self.assertRaises(ValueError):
            summarize_relative_last3f(rows, min_race_runners=1)
        with self.assertRaises(ValueError):
            summarize_relative_last3f(rows, min_condition_starts=0)



    def test_relative_last3f_deduplicates_cross_source_rows_and_merges_missing_times(self):
        rows = []
        for race_number in range(1, 4):
            runners = [
                {"horse_name": "対象馬", "horse_registration_number": "2020-12345",
                 "date": "2026-11-01", "track": "東京", "race_number": race_number,
                 "horse_number": 1, "finish": 1, "field_size": 3, "surface": "芝",
                 "distance": 1600, "going": "良", "individual_last3f": None},
                {"horse_name": "相手馬A", "horse_registration_number": "2021-12345",
                 "date": "2026-11-01", "track": "東京", "race_number": race_number,
                 "horse_number": 2, "finish": 2, "field_size": 3, "surface": "芝",
                 "distance": 1600, "going": "良", "individual_last3f": None},
                {"horse_name": "相手馬B", "horse_registration_number": "2022-12345",
                 "date": "2026-11-01", "track": "東京", "race_number": race_number,
                 "horse_number": 3, "finish": 3, "field_size": 3, "surface": "芝",
                 "distance": 1600, "going": "良", "individual_last3f": None},
            ]
            rows.extend({
                **row, "source_url": "annual-results.pdf",
                "last3f": 35.1, "race_last3f": 35.1,
            } for row in runners)
            rows.extend({
                **row,
                "source_url": "graded-replay.pdf",
                "individual_last3f": 33.0 + row["horse_number"],
            } for row in runners)

        summaries = summarize_relative_last3f(rows, min_condition_starts=1)
        target = next(item for item in summaries if item["horse_registration_number"] == "2020-12345")
        self.assertEqual(target["comparable_starts"], 3)
        self.assertEqual(target["conditions"]["track"][0]["sample_count"], 3)
        self.assertEqual(target["conditions"]["track"][0]["average_relative_score"], 100.0)

    def test_integrated_score_combines_condition_form_and_relative_last3f(self):
        horse_rows = []
        race_rows = []
        for race_number in range(1, 4):
            date = f"2026-11-0{race_number}"
            target = {
                "horse_name": "統合対象馬", "horse_registration_number": "2020-12345",
                "date": date, "track": "東京", "race_number": race_number,
                "surface": "芝", "distance": 1600, "going": "良",
                "finish": 2, "field_size": 10, "horse_number": 1, "last3f": 33.0,
            }
            horse_rows.append(target)
            race_rows.append({**target, "field_size": 3})
            race_rows.extend([
                {**target, "horse_name": "相手馬A", "horse_registration_number": f"2021-{race_number:05d}",
                 "horse_number": 2, "finish": 1, "field_size": 3, "last3f": 34.0},
                {**target, "horse_name": "相手馬B", "horse_registration_number": f"2022-{race_number:05d}",
                 "horse_number": 3, "finish": 3, "field_size": 3, "last3f": 35.0},
            ])
        result = integrated_condition_last3f_score(
            horse_rows, race_rows, {"track": "東京", "surface": "芝", "distance": 1600, "going": "良"},
            horse_registration_number="2020-12345",
        )
        self.assertTrue(result["integrated"])
        self.assertEqual(result["relative_last3f_score"], 100.0)
        self.assertEqual(result["relative_last3f_sample_count"], 3)
        self.assertEqual(result["relative_last3f_condition"], "競馬場・芝ダート・距離")
        self.assertGreater(result["score"], result["placing_condition_score"])

    def test_integrated_score_falls_back_when_relative_sample_is_insufficient(self):
        horse = {"horse_name": "単独馬", "horse_registration_number": "2020-12345",
                 "date": "2026-11-01", "track": "東京", "race_number": 1,
                 "surface": "芝", "distance": 1600, "finish": 2, "field_size": 10,
                 "last3f": 33.0}
        race_rows = [
            horse,
            {**horse, "horse_name": "相手", "horse_registration_number": "2021-12345",
             "horse_number": 2, "last3f": 34.0},
        ]
        result = integrated_condition_last3f_score(
            [horse], race_rows, {"surface": "芝", "distance": 1600},
            horse_registration_number="2020-12345",
        )
        self.assertFalse(result["integrated"])
        self.assertIsNone(result["relative_last3f_score"])
        self.assertEqual(result["score"], result["overall_score"])

    def test_integrated_score_validates_weight_and_requires_identity(self):
        horse = {"horse_name": "対象", "date": "2026-11-01", "finish": 2, "field_size": 10}
        with self.assertRaises(ValueError):
            integrated_condition_last3f_score([horse], [], {}, horse_name="対象", relative_last3f_weight=1.1)
        result = integrated_condition_last3f_score(
            [horse], [], {}, horse_registration_number=None, horse_name=None
        )
        self.assertFalse(result["integrated"])


    def test_relative_last3f_uses_exact_jra_horse_profile_id(self):
        race_rows = []
        horse_rows = []
        for race_number in range(1, 4):
            base = {
                "date": f"2026-11-0{race_number}",
                "track": "京都",
                "race_number": race_number,
                "surface": "芝",
                "distance": 1600,
                "going": "良",
                "field_size": 3,
            }
            target = {
                **base, "horse_name": "同名馬", "horse_profile_id": "pw-target/AA",
                "horse_number": 1, "finish": 1, "individual_last3f": 33.0,
            }
            horse_rows.append(target)
            race_rows.extend([
                target,
                {**base, "horse_name": "同名馬", "horse_profile_id": "pw-other/BB",
                 "horse_number": 2, "finish": 2, "individual_last3f": 34.0},
                {**base, "horse_name": "第三馬", "horse_profile_id": "pw-third/CC",
                 "horse_number": 3, "finish": 3, "individual_last3f": 35.0},
            ])

        summaries = summarize_relative_last3f(race_rows)
        self.assertEqual(len(summaries), 3)
        target_summary = next(
            item for item in summaries if item["horse_profile_id"] == "pw-target/AA"
        )
        same_name_other = next(
            item for item in summaries if item["horse_profile_id"] == "pw-other/BB"
        )
        self.assertEqual(target_summary["identity_basis"], "official_horse_profile_id")
        self.assertTrue(target_summary["identity_verified"])
        self.assertEqual(target_summary["comparable_starts"], 3)
        self.assertEqual(target_summary["average_relative_score"], 100.0)
        self.assertNotEqual(
            target_summary["average_relative_score"],
            same_name_other["average_relative_score"],
        )

        identified = integrated_horse_performance_score(
            horse_rows, race_rows,
            {"track": "京都", "surface": "芝", "distance": 1600, "going": "良"},
            horse_profile_id="pw-target/AA",
        )
        self.assertEqual(identified["relative_last3f_sample_count"], 3)
        self.assertEqual(identified["relative_last3f_raw_score"], 100.0)
        self.assertEqual(identified["identity_basis"], "official_horse_profile_id")

        unidentified = integrated_horse_performance_score(
            horse_rows, race_rows,
            {"track": "京都", "surface": "芝", "distance": 1600, "going": "良"},
        )
        self.assertIsNone(unidentified["relative_last3f_score"])
        self.assertEqual(unidentified["identity_basis"], "unverified")

        legacy = integrated_condition_last3f_score(
            horse_rows, race_rows,
            {"track": "京都", "surface": "芝", "distance": 1600, "going": "良"},
            horse_profile_id="pw-target/AA",
        )
        self.assertTrue(legacy["integrated"])
        self.assertEqual(legacy["relative_last3f_score"], 100.0)

    def test_integrated_horse_performance_score_combines_independent_components(self):
        horse_rows = []
        specifications = [
            (1600, "良", 1),
            (1600, "良", 2),
            (1600, "重", 10),
            (2000, "良", 4),
            (2200, "良", 5),
            (1200, "良", 6),
        ]
        for index, (distance, going, finish) in enumerate(specifications, start=1):
            horse_rows.append({
                "horse_name": "統合評価馬",
                "horse_registration_number": "2020-12345",
                "source_url": f"race-{index}.pdf",
                "date": f"2026-11-{index:02d}",
                "track": "東京",
                "race_number": 1,
                "horse_number": 1,
                "surface": "芝",
                "distance": distance,
                "going": going,
                "finish": finish,
                "field_size": 10,
                "individual_last3f": 33.0,
            })

        race_rows = []
        for target in horse_rows[:3]:
            race_rows.append({**target, "finish": 1, "field_size": 3})
            race_rows.extend([
                {
                    **target,
                    "horse_name": "相手馬A",
                    "horse_registration_number": "2021-12345",
                    "horse_number": 2,
                    "finish": 2,
                    "field_size": 3,
                    "individual_last3f": 34.0,
                },
                {
                    **target,
                    "horse_name": "相手馬B",
                    "horse_registration_number": "2022-12345",
                    "horse_number": 3,
                    "finish": 3,
                    "field_size": 3,
                    "individual_last3f": 35.0,
                },
            ])

        result = integrated_horse_performance_score(
            horse_rows,
            race_rows,
            {"track": "東京", "surface": "芝", "distance": 1600, "going": "良"},
            horse_registration_number="2020-12345",
        )

        self.assertTrue(result["integrated"])
        self.assertEqual(result["distance_sample_count"], 3)
        self.assertEqual(result["distance_condition"], "芝ダート・同距離")
        self.assertEqual(result["going_sample_count"], 5)
        self.assertEqual(result["going_condition"], "芝ダート・馬場状態")
        self.assertEqual(result["relative_last3f_sample_count"], 3)
        self.assertEqual(result["relative_last3f_condition"], "競馬場・芝ダート・距離")
        self.assertEqual(result["relative_last3f_raw_score"], 100.0)
        self.assertLess(result["relative_last3f_score"], 100.0)
        self.assertEqual(
            result["component_weights"],
            {"placing": 0.4, "distance": 0.2, "going": 0.2, "relative_last3f": 0.2},
        )
        expected = (
            result["baseline_score"] * 0.4
            + result["distance_score"] * 0.2
            + result["going_score"] * 0.2
            + result["relative_last3f_score"] * 0.2
        )
        self.assertAlmostEqual(result["score"], round(expected, 1))
        self.assertEqual(result["identity_basis"], "official_registration_number")

    def test_integrated_score_confidence_uses_effective_five_start_baseline(self):
        rows = [
            {
                "horse_name": "履歴多数馬",
                "source_url": f"history-{index}.pdf",
                "date": f"2026-10-{index:02d}",
                "track": "東京",
                "race_number": 1,
                "horse_number": 1,
                "surface": "芝",
                "distance": 1600,
                "going": "良",
                "finish": (index % 4) + 1,
                "field_size": 10,
            }
            for index in range(1, 13)
        ]
        result = integrated_horse_performance_score(
            rows, [], {"track": "東京", "surface": "芝", "distance": 1600, "going": "良"},
            horse_name="履歴多数馬",
        )

        self.assertEqual(result["baseline_sample_count"], 12)
        self.assertEqual(result["baseline_used_sample_count"], 5)
        self.assertEqual(result["distance_sample_count"], 12)
        self.assertEqual(result["going_sample_count"], 12)
        self.assertEqual(result["confidence"], "中程度")

    def test_integrated_horse_performance_score_uses_distance_band_and_shrinks_small_samples(self):
        rows = [
            {"horse_name": "距離評価馬", "date": "2026-11-01", "finish": 1,
             "field_size": 10, "surface": "芝", "distance": 1600, "going": "良"},
            {"horse_name": "距離評価馬", "date": "2026-11-02", "finish": 1,
             "field_size": 10, "surface": "芝", "distance": 1600, "going": "良"},
            {"horse_name": "距離評価馬", "date": "2026-11-03", "finish": 1,
             "field_size": 10, "surface": "芝", "distance": 1800, "going": "重"},
            {"horse_name": "距離評価馬", "date": "2026-11-04", "finish": 10,
             "field_size": 10, "surface": "芝", "distance": 2000, "going": "重"},
        ]
        result = integrated_horse_performance_score(
            rows, [], {"surface": "芝", "distance": 1600, "going": "良"},
            horse_name="距離評価馬",
        )
        self.assertEqual(result["distance_sample_count"], 3)
        self.assertEqual(result["distance_condition"], "芝ダート・距離帯")
        self.assertIsNone(result["going_score"])
        self.assertIsNone(result["relative_last3f_score"])
        self.assertGreater(result["distance_score"], result["baseline_score"])
        self.assertLess(result["distance_score"], result["distance_raw_score"])
        self.assertEqual(result["confidence"], "低め")

    def test_integrated_score_deduplicates_same_runner_across_sources(self):
        rows = [
            {"horse_name": "重複確認馬", "date": "2026-11-01", "track": "東京",
             "race_number": index, "horse_number": 1, "surface": "芝",
             "distance": 1600, "going": "良", "finish": finish, "field_size": 10,
             "source_url": "annual-" + str(index) + ".pdf"}
            for index, finish in enumerate((1, 2, 3), start=1)
        ]
        duplicate_rows = [
            {**row, "source_url": "graded-" + str(index) + ".pdf"}
            for index, row in enumerate(rows, start=1)
        ]
        result = integrated_horse_performance_score(
            rows + duplicate_rows, [],
            {"track": "東京", "surface": "芝", "distance": 1600, "going": "良"},
            horse_name="重複確認馬",
        )

        self.assertEqual(result["baseline_sample_count"], 3)
        self.assertEqual(result["distance_sample_count"], 3)
        self.assertEqual(result["going_sample_count"], 3)

    def test_integrated_horse_performance_score_falls_back_without_condition_data(self):
        rows = [
            {"horse_name": "基準馬", "date": "2026-11-01", "finish": 1, "field_size": 10},
            {"horse_name": "基準馬", "date": "2026-11-02", "finish": 3, "field_size": 10},
            {"horse_name": "基準馬", "date": "2026-11-03", "finish": 5, "field_size": 10},
        ]
        result = integrated_horse_performance_score(
            rows, [], {"surface": "芝", "distance": 1600, "going": "良"},
            horse_name="基準馬",
        )
        self.assertFalse(result["integrated"])
        self.assertEqual(result["score"], result["baseline_score"])
        self.assertEqual(result["component_weights"], {"placing": 1.0})
        self.assertIsNone(result["distance_score"])
        self.assertIsNone(result["going_score"])
        self.assertIsNone(result["relative_last3f_score"])

    def test_integrated_score_marks_one_or_two_overall_starts_as_low_confidence(self):
        rows = [
            {"horse_name": "少数標本馬", "date": "2026-11-01", "finish": 1, "field_size": 10},
            {"horse_name": "少数標本馬", "date": "2026-11-02", "finish": 2, "field_size": 10},
        ]
        result = integrated_horse_performance_score(
            rows, [], {}, horse_name="少数標本馬",
        )

        # Keep the existing numeric/API result, but make the weak sample explicit.
        self.assertEqual(result["baseline_used_sample_count"], 2)
        self.assertEqual(result["score"], result["baseline_score"])
        self.assertEqual(result["confidence"], "低め")

    def test_integrated_horse_performance_score_validates_weights(self):
        with self.assertRaises(ValueError):
            integrated_horse_performance_score(
                [{"finish": 1, "field_size": 10}], [], {},
                placing_weight=0,
            )
        with self.assertRaises(ValueError):
            integrated_horse_performance_score(
                [{"finish": 1, "field_size": 10}], [], {},
                distance_weight=float("nan"),
            )


if __name__ == "__main__":
    unittest.main()
