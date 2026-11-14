import io
import unittest

from import_replay_results import (
    deduplicate_records,
    read_limited_response,
    validate_replay_records,
)


class FakeResponse(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}


class ImportReplayResultsTests(unittest.TestCase):
    def test_reads_response_within_limit(self):
        response = FakeResponse(b"%PDF-1.4 sample", {"Content-Length": "15"})
        self.assertEqual(read_limited_response(response, max_bytes=20), b"%PDF-1.4 sample")

    def test_rejects_content_length_above_limit_before_reading(self):
        response = FakeResponse(b"ignored", {"Content-Length": "101"})
        with self.assertRaisesRegex(ValueError, "size limit"):
            read_limited_response(response, max_bytes=100)

    def test_rejects_malformed_content_length(self):
        for value in ("unknown", "-1", "1.5"):
            with self.subTest(value=value):
                response = FakeResponse(b"small", {"Content-Length": value})
                with self.assertRaisesRegex(ValueError, "Content-Length"):
                    read_limited_response(response, max_bytes=100)

    def test_rejects_stream_that_exceeds_limit_without_content_length(self):
        response = FakeResponse(b"x" * 101)
        with self.assertRaisesRegex(ValueError, "size limit"):
            read_limited_response(response, max_bytes=100)

    def test_validates_runner_rows_before_import(self):
        rows = [{
            "horse_number": 9,
            "finish": 1,
            "horse_name": "テストホース",
            "individual_last3f": 33.4,
        }]
        self.assertIs(validate_replay_records(rows), rows)

    def test_rejects_invalid_runner_number_and_missing_name(self):
        with self.assertRaisesRegex(ValueError, "horse number"):
            validate_replay_records([{
                "horse_number": 19, "finish": 1, "horse_name": "テスト"
            }])
        with self.assertRaisesRegex(ValueError, "no horse name"):
            validate_replay_records([{
                "horse_number": 1, "finish": 1, "horse_name": " "
            }])

    def test_rejects_duplicate_horse_numbers_in_one_pdf(self):
        rows = [
            {"horse_number": 4, "finish": 1, "horse_name": "先頭馬"},
            {"horse_number": 4, "finish": 2, "horse_name": "重複馬"},
        ]
        with self.assertRaisesRegex(ValueError, "Duplicate horse number"):
            validate_replay_records(rows)

    def test_rejects_fractional_horse_number_or_placing(self):
        with self.assertRaisesRegex(ValueError, "non-integer"):
            validate_replay_records([{
                "horse_number": 1.5, "finish": 1, "horse_name": "テスト"
            }])
        with self.assertRaisesRegex(ValueError, "non-integer"):
            validate_replay_records([{
                "horse_number": 1, "finish": 2.5, "horse_name": "テスト"
            }])

    def test_rejects_non_finite_individual_last3f(self):
        with self.assertRaisesRegex(ValueError, "non-finite"):
            validate_replay_records([{
                "horse_number": 1,
                "finish": 1,
                "horse_name": "テスト",
                "individual_last3f": float("nan"),
            }])

    def test_rejects_implausible_individual_last3f(self):
        for value in (19.9, 60.1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "outside 20-60"):
                    validate_replay_records([{
                        "horse_number": 1, "finish": 1, "horse_name": "テスト",
                        "individual_last3f": value,
                    }])

    def test_validates_corner_position_count_and_range(self):
        base = {"horse_number": 1, "finish": 1, "horse_name": "テスト"}
        valid = dict(base, corner_positions="3-2-2-1")
        self.assertEqual(validate_replay_records([valid]), [valid])
        for value in ("3", "3-2-1-2-4", "3-19", "3-x"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_replay_records([dict(base, corner_positions=value)])

    def test_deduplicates_by_source_and_horse_but_keeps_unkeyed_rows(self):
        rows = [
            {"source_url": "a.pdf", "horse_number": 1, "horse_name": "旧"},
            {"source_url": "a.pdf", "horse_number": 1, "horse_name": "新"},
            {"source_url": "a.pdf", "horse_number": 2, "horse_name": "別馬"},
            {"source_url": "b.pdf", "horse_number": 1, "horse_name": "別レース"},
            {"source_url": "a.pdf", "horse_number": None, "horse_name": "未確定A"},
            {"source_url": "a.pdf", "horse_number": None, "horse_name": "未確定B"},
        ]
        result = deduplicate_records(rows)
        self.assertEqual(len(result), 5)
        self.assertIn("新", [row["horse_name"] for row in result])
        self.assertIn("未確定A", [row["horse_name"] for row in result])
        self.assertIn("未確定B", [row["horse_name"] for row in result])


if __name__ == "__main__":
    unittest.main()
