import unittest

from import_jra_html_results import compare_sources, validate_complete_races


def runner(**overrides):
    row = {
        "date": "2026-11-15", "track": "京都", "race_number": 7,
        "horse_number": 6, "horse_name": "テストホース",
        "field_size": 2, "race_validation_warnings": [],
    }
    row.update(overrides)
    return row


class ImportJraHtmlResultsTests(unittest.TestCase):
    def test_accepts_only_complete_warning_free_race_table(self):
        rows = [
            runner(horse_number=1, field_size=2),
            runner(horse_number=2, field_size=2),
        ]
        accepted, rejected = validate_complete_races(rows)
        self.assertEqual(accepted, rows)
        self.assertEqual(rejected, [])

    def test_rejects_entire_race_when_field_is_incomplete(self):
        rows = [
            runner(horse_number=1, field_size=None),
            runner(horse_number=2, field_size=None),
        ]
        accepted, rejected = validate_complete_races(rows)
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 1)

    def test_rejects_duplicate_horse_numbers_without_guessing(self):
        rows = [
            runner(horse_number=1, field_size=2),
            runner(horse_number=1, field_size=2),
        ]
        accepted, rejected = validate_complete_races(rows)
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 1)

    def test_rejects_entire_race_with_impossible_finish_time(self):
        rows = [
            runner(horse_number=1, field_size=2, finish=1,
                   finish_time="23:39.0", finish_time_seconds=1419.0),
            runner(horse_number=2, field_size=2, finish=2,
                   finish_time="1:40.0", finish_time_seconds=100.0),
        ]
        accepted, rejected = validate_complete_races(rows)
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 1)

    def test_allows_non_finish_status_without_a_finish_time(self):
        rows = [
            runner(horse_number=1, field_size=2, finish=1,
                   finish_time="1:40.0", finish_time_seconds=100.0),
            runner(horse_number=2, field_size=2, finish=None,
                   result_status="競走中止", finish_time=None,
                   finish_time_seconds=None),
        ]
        accepted, rejected = validate_complete_races(rows)
        self.assertEqual(accepted, rows)
        self.assertEqual(rejected, [])

    def test_matches_only_exact_race_and_horse_identity(self):
        html = [runner()]
        pdf = [runner()]
        result = compare_sources(html, pdf)
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["conflict_count"], 0)
        self.assertFalse(result["automatic_merge_performed"])

    def test_horse_name_disagreement_is_reported_not_merged(self):
        html = [runner()]
        pdf = [runner(horse_name="別の馬名")]
        result = compare_sources(html, pdf)
        self.assertEqual(result["matched_count"], 0)
        self.assertEqual(result["conflict_count"], 1)
        self.assertEqual(result["conflicts"][0]["reason"], "horse_name_mismatch")
        self.assertFalse(result["automatic_merge_performed"])

    def test_reports_field_level_conflicts_for_exactly_matched_horses(self):
        html = [runner(finish=1, finish_time_seconds=94.4, individual_last3f=34.2)]
        pdf = [runner(finish=2, finish_time_seconds=94.5, individual_last3f=34.4)]
        result = compare_sources(html, pdf)
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["field_conflict_count"], 3)
        self.assertEqual(
            {item["field"] for item in result["field_conflicts"]},
            {"finish", "finish_time_seconds", "individual_last3f"},
        )
        self.assertEqual(result["compared_field_count"], 3)
        self.assertFalse(result["automatic_merge_performed"])

    def test_missing_source_values_are_not_treated_as_agreement(self):
        html = [runner(finish=1, finish_time_seconds=None, individual_last3f=34.2)]
        pdf = [runner(finish=1, finish_time_seconds=94.4, individual_last3f=None)]
        result = compare_sources(html, pdf)
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["field_conflict_count"], 0)
        self.assertEqual(result["missing_field_pair_count"], 2)

    def test_missing_pdf_race_metadata_does_not_fuzzy_match(self):
        html = [runner()]
        pdf = [runner(date=None, track=None, race_number=None)]
        result = compare_sources(html, pdf)
        self.assertEqual(result["matched_count"], 0)
        self.assertEqual(result["html_only_count"], 1)


if __name__ == "__main__":
    unittest.main()
