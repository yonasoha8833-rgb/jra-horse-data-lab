import io
import unittest
from unittest.mock import patch

from jra_html_parser import (
    fetch_and_parse_result,
    fetch_result_html,
    parse_result_html,
    read_limited_html,
)


SAMPLE_HTML = """
<html><body>
<h1>レース結果2026年11月15日（日曜）5回京都4日 7レース</h1>
<p>2026年11月15日（日曜） 5回京都4日 発走時刻：13時20分</p>
<p>天候小雨 芝重</p>
<h2>3歳以上1勝クラス</h2>
<p>コース：1,600メートル（芝・右 外）</p>
<table>
<tr><th>着順</th><th>枠</th><th>馬番</th><th>馬名</th><th>性齢</th>
<th>負担重量</th><th>騎手名</th><th>タイム</th><th>着差</th>
<th>コーナー通過順位</th><th>推定上り</th><th>馬体重（増減）</th>
<th>調教師名</th><th>単勝人気</th></tr>
<tr><td>1</td><td>3</td><td>6</td><td><a>ミラキュラスアース</a></td>
<td>牝3</td><td>55.0</td><td>西村 淳也</td><td>1:34.4</td><td></td>
<td><ul><li>6</li><li>6</li></ul></td><td>34.2</td><td>474(+2)</td><td>奥村 豊</td><td>10</td></tr>
<tr><td>2</td><td>8</td><td>17</td><td><a>ルーチェブリラーレ</a></td>
<td>牝3</td><td>55.0</td><td>C.ルメール</td><td>1:34.5</td><td>１／２</td>
<td><ul><li>10</li><li>6</li></ul></td><td>34.3</td><td>448(-8)</td><td>福永 祐一</td><td>2</td></tr>
<tr><td>9</td><td>7</td><td>15</td><td><a>コスモグローリー</a><img alt="ブリンカー着用"></td>
<td>牡4</td><td>58.0</td><td>岩田 望来</td><td>1:35.1</td><td>１</td>
<td><ul><li>3</li><li>3</li></ul></td><td>35.1</td><td>472(+4)</td><td>北出 成人</td><td>8</td></tr>
</table>
<p>タイム</p>
<p>ハロンタイム | 12.5 - 11.2 - 11.7 - 12.3 - 12.2 - 11.8 - 11.1 - 11.6</p>
<p>上り | 4F 46.7 - 3F 34.5</p>
<p>コーナー通過順位</p>
<p>3コーナー | 7,12(5,15)10(1,2,6,14)17</p>
<p>4コーナー | (*7,12)15(5,14)(10,6,17)</p>
</body></html>
"""


class FakeResponse(io.BytesIO):
    def __init__(self, body, headers=None, url="https://www.jra.go.jp/JRADB/accessS.html?CNAME=test"):
        super().__init__(body)
        self.headers = headers or {}
        self._url = url

    def geturl(self):
        return self._url


class JraHtmlParserTests(unittest.TestCase):
    def test_extracts_runner_level_last3f_and_race_level_times_separately(self):
        rows = parse_result_html(SAMPLE_HTML, source_url="https://www.jra.go.jp/JRADB/accessS.html?CNAME=test")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["date"], "2026-11-15")
        self.assertEqual(rows[0]["track"], "京都")
        self.assertEqual(rows[0]["race_number"], 7)
        self.assertEqual(rows[0]["race_class"], "3歳以上1勝クラス")
        self.assertEqual(rows[0]["surface"], "芝")
        self.assertEqual(rows[0]["distance"], 1600)
        self.assertEqual(rows[0]["going"], "重")
        self.assertEqual(rows[0]["horse_name"], "ミラキュラスアース")
        self.assertEqual(rows[0]["individual_last3f"], 34.2)
        self.assertEqual(rows[0]["race_last3f"], 34.5)
        self.assertEqual(rows[0]["race_last4f"], 46.7)
        self.assertEqual(rows[0]["race_lap_times"], [12.5, 11.2, 11.7, 12.3, 12.2, 11.8, 11.1, 11.6])
        self.assertEqual(rows[0]["corner_positions"], "6-6")
        self.assertEqual(rows[0]["corner_3"], 6)
        self.assertEqual(rows[0]["corner_4"], 6)
        self.assertEqual(rows[0]["finish_time_seconds"], 94.4)
        self.assertEqual(rows[0]["field_size"], 3)

    def test_reads_blinker_marker_without_adding_it_to_horse_name(self):
        rows = parse_result_html(SAMPLE_HTML)
        blinker = next(row for row in rows if row["horse_number"] == 15)
        self.assertEqual(blinker["horse_name"], "コスモグローリー")
        self.assertTrue(blinker["blinkers"])

    def test_missing_individual_last3f_is_not_filled_from_race_last3f(self):
        html = SAMPLE_HTML.replace("<td>34.2</td>", "<td></td>")
        rows = parse_result_html(html)
        self.assertIsNone(rows[0]["individual_last3f"])
        self.assertEqual(rows[0]["race_last3f"], 34.5)

    def test_invalid_date_and_distance_remain_missing(self):
        html = SAMPLE_HTML.replace("2026年11月15日", "2026年2月30日").replace("1,600メートル", "99,999メートル")
        rows = parse_result_html(html)
        self.assertIsNone(rows[0]["date"])
        self.assertIsNone(rows[0].get("distance"))

    def test_status_runner_has_no_finish_place(self):
        html = SAMPLE_HTML.replace("<td>9</td><td>7</td><td>15</td>", "<td>中止</td><td>7</td><td>15</td>")
        rows = parse_result_html(html)
        row = next(item for item in rows if item["horse_number"] == 15)
        self.assertIsNone(row["finish"])
        self.assertEqual(row["result_status"], "中止")


    def test_partial_result_table_does_not_claim_a_complete_field(self):
        html = SAMPLE_HTML.replace(
            "<tr><td>2</td><td>8</td><td>17</td><td><a>ルーチェブリラーレ</a></td>",
            "<tr><td></td><td>8</td><td>17</td><td><a>ルーチェブリラーレ</a></td>",
        )
        rows = parse_result_html(html)
        self.assertEqual(len(rows), 3)
        self.assertIsNone(rows[1]["field_size"])
        self.assertIn("incomplete_runner_rows:1", rows[1]["race_validation_warnings"])

    def test_returns_empty_for_unrecognized_table(self):
        self.assertEqual(parse_result_html("<html><table><tr><td>説明のみ</td></tr></table></html>"), [])

    def test_rejects_non_jra_or_non_result_urls(self):
        for url in (
            "http://www.jra.go.jp/JRADB/accessS.html?CNAME=test",
            "https://example.com/JRADB/accessS.html?CNAME=test",
            "https://www.jra.go.jp/JRADB/accessD.html?CNAME=test",
            "https://www.jra.go.jp/JRADB/accessS.html",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    fetch_result_html(url)

    def test_limits_html_download_size(self):
        with self.assertRaisesRegex(ValueError, "size limit"):
            read_limited_html(FakeResponse(b"x" * 11), max_bytes=10)

    def test_fetches_and_parses_only_verified_jra_html(self):
        url = "https://www.jra.go.jp/JRADB/accessS.html?CNAME=test"
        response = FakeResponse(
            SAMPLE_HTML.encode("utf-8"),
            {"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(SAMPLE_HTML.encode("utf-8")))},
            url=url,
        )
        with patch("jra_html_parser.urlopen", return_value=response):
            rows = fetch_and_parse_result(url)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["source_url"], url)
        self.assertEqual(rows[0]["source_type"], "jra_official_result_html")

    def test_detects_shift_jis_from_html_meta_when_header_omits_charset(self):
        url = "https://www.jra.go.jp/JRADB/accessS.html?CNAME=test"
        html = SAMPLE_HTML.replace(
            "<html><body>",
            '<html><head><meta charset="Shift_JIS"></head><body>',
        )
        body = html.encode("shift_jis")
        response = FakeResponse(
            body,
            {"Content-Type": "text/html", "Content-Length": str(len(body))},
            url=url,
        )
        with patch("jra_html_parser.urlopen", return_value=response):
            rows = fetch_and_parse_result(url)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["horse_name"], "ミラキュラスアース")
        self.assertEqual(rows[0]["individual_last3f"], 34.2)
        self.assertEqual(rows[0]["race_last3f"], 34.5)

    def test_rejects_redirect_outside_jra(self):
        url = "https://www.jra.go.jp/JRADB/accessS.html?CNAME=test"
        response = FakeResponse(b"<html/>", {"Content-Type": "text/html"},
                                url="https://example.com/accessS.html?CNAME=test")
        with patch("jra_html_parser.urlopen", return_value=response):
            with self.assertRaises(ValueError):
                fetch_result_html(url)


if __name__ == "__main__":
    unittest.main()
