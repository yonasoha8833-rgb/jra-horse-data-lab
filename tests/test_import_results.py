import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import import_results


class ImportResultsTests(unittest.TestCase):

    def test_quality_report_counts_race_warnings_and_missing_fields(self):
        records = [
            {
                "source_url": "https://example.test/a.pdf",
                "race_number": 1,
                "date": "2026-11-08",
                "horse_number": 2,
                "race_validation_warnings": ["duplicate_horse_number:2"],
                "track": "東京",
                "distance": 1600,
                "finish_time_seconds": 99.0,
            },
            {
                "source_url": "https://example.test/a.pdf",
                "race_number": 1,
                "date": "2026-11-08",
                "horse_number": 2,
                "race_validation_warnings": ["duplicate_horse_number:2"],
                "track": "東京",
                "distance": 1600,
                "finish_time_seconds": None,
            },
            {
                "source_url": "https://example.test/b.pdf",
                "race_number": 2,
                "date": None,
                "horse_number": 1,
                "race_validation_warnings": [],
                "track": None,
                "distance": None,
                "finish_time_seconds": None,
            },
        ]
        report = import_results.build_quality_report(records)
        self.assertEqual(report["race_count"], 2)
        self.assertEqual(report["warning_race_count"], 1)
        self.assertEqual(report["duplicate_number_race_count"], 1)
        self.assertEqual(report["records_missing_date"], 1)
        self.assertEqual(report["records_missing_track"], 1)
        self.assertEqual(report["records_missing_distance"], 1)
        self.assertEqual(report["records_missing_finish_time"], 2)

    def test_failed_source_is_recorded_and_remains_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.json"
            output = root / "race_results.json"
            catalog.write_text(json.dumps({"items": [
                {"url": "https://example.test/fail.pdf"},
                {"url": "https://example.test/good.pdf"},
            ]}), encoding="utf-8")

            extracted = [{
                "source_url": "https://example.test/good.pdf",
                "race_number": 1,
                "horse_number": 3,
                "horse_name": "テストホース",
                "finish": 1,
            }]
            with patch.object(import_results, "CATALOG", catalog), \
                 patch.object(import_results, "OUTPUT", output), \
                 patch.object(import_results, "download_and_extract",
                              side_effect=[RuntimeError("temporary network error"), extracted]), \
                 patch("sys.argv", ["import_results.py", "--limit", "2"]):
                import_results.main()

            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["processed_sources"], ["https://example.test/good.pdf"])
            self.assertEqual(len(saved["import_failures"]), 1)
            self.assertEqual(saved["import_failures"][0]["url"], "https://example.test/fail.pdf")
            self.assertEqual(saved["import_failures"][0]["attempts"], 1)
            self.assertEqual(saved["record_count"], 1)

    def test_empty_extraction_is_recorded_but_not_marked_processed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.json"
            output = root / "race_results.json"
            url = "https://example.test/empty.pdf"
            catalog.write_text(json.dumps({"items": [{"url": url}]}), encoding="utf-8")

            with patch.object(import_results, "CATALOG", catalog), \
                 patch.object(import_results, "OUTPUT", output), \
                 patch.object(import_results, "download_and_extract", return_value=[]), \
                 patch("sys.argv", ["import_results.py", "--limit", "1"]):
                import_results.main()

            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["processed_sources"], [])
            self.assertEqual(saved["import_failures"][0]["attempts"], 1)
            self.assertIn("No runner rows recognized", saved["import_failures"][0]["error"])


    def test_new_sources_are_imported_before_retrying_old_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.json"
            output = root / "race_results.json"
            failed_url = "https://example.test/previously-failed.pdf"
            new_url = "https://example.test/new.pdf"
            catalog.write_text(json.dumps({"items": [
                {"url": failed_url}, {"url": new_url},
            ]}), encoding="utf-8")
            output.write_text(json.dumps({
                "records": [],
                "processed_sources": [],
                "import_failures": [{
                    "url": failed_url,
                    "attempts": 3,
                    "last_attempt_utc": "2026-11-13T00:00:00+00:00",
                    "error": "temporary error",
                }],
            }), encoding="utf-8")
            extracted = [{
                "source_url": new_url,
                "race_number": 1,
                "horse_number": 1,
                "horse_name": "新規取込馬",
                "finish": 1,
            }]
            with patch.object(import_results, "CATALOG", catalog), \
                 patch.object(import_results, "OUTPUT", output), \
                 patch.object(import_results, "download_and_extract", return_value=extracted) as download, \
                 patch("sys.argv", ["import_results.py", "--limit", "1"]):
                import_results.main()

            download.assert_called_once_with(new_url)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["processed_sources"], [new_url])
            self.assertEqual(saved["import_failures"][0]["url"], failed_url)


if __name__ == "__main__":
    unittest.main()
