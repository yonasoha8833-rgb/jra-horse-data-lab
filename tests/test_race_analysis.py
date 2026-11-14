import unittest

from race_analysis import filter_records, performance_score, summarize_horses


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


    def test_official_registration_number_separates_same_named_horses(self):
        rows = [
            {"horse_name": "同名馬", "horse_registration_number": "2020100001", "finish": 1},
            {"horse_name": "同名馬", "horse_registration_number": "2021100002", "finish": 3},
        ]
        result = summarize_horses(rows)
        self.assertEqual(len(result), 2)
        self.assertEqual(
            {horse["horse_registration_number"] for horse in result},
            {"2020100001", "2021100002"},
        )
        self.assertTrue(all(horse["identity_verified"] for horse in result))

    def test_name_only_identity_is_marked_unverified(self):
        horse = summarize_horses([{"horse_name": "登録番号なし馬", "finish": 1}])[0]
        self.assertIsNone(horse["horse_registration_number"])
        self.assertEqual(horse["identity_basis"], "horse_name_only")
        self.assertFalse(horse["identity_verified"])

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

    def test_filters(self):
        result = filter_records(self.rows, track="東京", surface="芝", distance=1600)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["horse_name"], "サンプルホース")


if __name__ == "__main__":
    unittest.main()
