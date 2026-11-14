import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import import_results


class ImportResultsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
