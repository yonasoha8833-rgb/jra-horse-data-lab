import unittest

from collector import Parser, merge_catalog_items, parse_metadata


class CollectorMetadataTests(unittest.TestCase):
    def test_parses_meeting_track_day_and_date(self):
        metadata = parse_metadata("5回 東京3日（PDF：701KB）", "11月14日（土曜）", 2026)
        self.assertEqual(metadata, {
            "date": "2026-11-14",
            "track": "東京",
            "meeting_number": 5,
            "meeting_day": 3,
        })

    def test_parses_track_session_day_without_date(self):
        metadata = parse_metadata("第2日（PDF：655KB）", "東京 第4回", 2026)
        self.assertEqual(metadata.get("track"), "東京")
        self.assertEqual(metadata.get("meeting_number"), 4)
        self.assertEqual(metadata.get("meeting_day"), 2)
        self.assertNotIn("date", metadata)

    def test_invalid_calendar_date_is_not_emitted(self):
        self.assertNotIn("date", parse_metadata("成績PDF", "2月30日", 2026))


    def test_parser_scopes_metadata_to_current_heading(self):
        html = """
        <h3>11月14日（土曜）</h3>
        <p><a href="a.pdf">5回 東京3日（PDF）</a></p>
        <h3>各競馬場ごとの全成績</h3>
        <h4>東京</h4><h5>第4回</h5>
        <p><a href="b.pdf">第2日（PDF）</a></p>
        <h4>京都</h4><h5>第3回</h5>
        <p><a href="c.pdf">第1日（PDF）</a></p>
        """
        parser = Parser()
        parser.feed(html)
        self.assertEqual(len(parser.links), 3)
        first = parse_metadata(parser.links[0][1], parser.links[0][2], 2026)
        second = parse_metadata(parser.links[1][1], parser.links[1][2], 2026)
        third = parse_metadata(parser.links[2][1], parser.links[2][2], 2026)
        self.assertEqual(first["date"], "2026-11-14")
        self.assertEqual(first["track"], "東京")
        self.assertEqual(second["track"], "東京")
        self.assertEqual(second["meeting_number"], 4)
        self.assertNotIn("date", second)
        self.assertEqual(third["track"], "京都")
        self.assertEqual(third["meeting_number"], 3)
        self.assertNotIn("date", third)

    def test_duplicate_pdf_urls_merge_metadata(self):
        rows = merge_catalog_items([
            {"url": "https://example.test/a.pdf", "label": "5回 東京3日", "track": "東京"},
            {"url": "https://example.test/a.pdf", "label": "第3日", "date": "2026-11-14"},
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["track"], "東京")
        self.assertEqual(rows[0]["date"], "2026-11-14")
        self.assertEqual(set(rows[0]["labels"]), {"5回 東京3日", "第3日"})


if __name__ == "__main__":
    unittest.main()
