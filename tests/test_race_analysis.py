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

    def test_horse_summary(self):
        result = summarize_horses(self.rows)
        horse = next(x for x in result if x["horse_name"] == "サンプルホース")
        self.assertEqual(horse["starts"], 2)
        self.assertEqual(horse["wins"], 1)
        self.assertEqual(horse["top3"], 2)
        self.assertEqual(horse["average_finish"], 2.0)
        self.assertEqual(horse["average_last3f"], 33.95)

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
