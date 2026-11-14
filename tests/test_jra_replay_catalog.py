import unittest

from jra_replay_catalog import parse_listing_html


class JraReplayCatalogTests(unittest.TestCase):
    def test_extracts_pdf_links_and_race_metadata(self):
        html = """
        <table><tr>
          <td>10月5日日曜</td><td>GⅡ毎日王冠</td><td>東京</td>
          <td>3歳以上</td><td>芝1,800メートル</td>
          <td>レーベンスティール</td><td>津村明秀</td>
          <td><a href="/datafile/seiseki/replay/2025/pdf/090.pdf">レース結果</a></td>
        </tr></table>
        """
        rows = parse_listing_html(
            html, year=2025,
            listing_url="https://www.jra.go.jp/datafile/seiseki/replay/2025/jyusyo.html",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["url"],
            "https://www.jra.go.jp/datafile/seiseki/replay/2025/pdf/090.pdf",
        )
        self.assertEqual(rows[0]["race_metadata"]["date"], "2025-10-05")
        self.assertEqual(rows[0]["race_metadata"]["race_name"], "GⅡ毎日王冠")
        self.assertEqual(rows[0]["race_metadata"]["track"], "東京")
        self.assertEqual(rows[0]["race_metadata"]["surface"], "芝")
        self.assertEqual(rows[0]["race_metadata"]["distance"], 1800)

    def test_resolves_pdf_link_from_result_html(self):
        from jra_replay_catalog import find_pdf_link

        html = '<html><body><a href="../pdf/090.pdf">PDF成績表</a></body></html>'
        self.assertEqual(
            find_pdf_link(
                html,
                "https://www.jra.go.jp/datafile/seiseki/replay/2025/result/090.html",
            ),
            "https://www.jra.go.jp/datafile/seiseki/replay/2025/pdf/090.pdf",
        )

    def test_keeps_result_page_url_for_later_pdf_resolution(self):
        html = """
        <table><tr><td>10月5日日曜</td><td>GⅡ毎日王冠</td><td>東京</td>
        <td>3歳以上</td><td>芝1,800メートル</td><td></td><td></td>
        <td><a href="/JRADB/accessS.html?CNAME=example">レース結果</a>
        <a href="/history.html">成績一覧</a></td></tr></table>
        """
        rows = parse_listing_html(
            html, year=2025,
            listing_url="https://www.jra.go.jp/datafile/seiseki/replay/2025/jyusyo.html",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["result_url"],
            "https://www.jra.go.jp/JRADB/accessS.html?CNAME=example",
        )
        self.assertNotIn("url", rows[0])

    def test_ignores_rows_without_result_pdf_and_invalid_dates(self):
        html = """
        <table>
          <tr><td>2月30日日曜</td><td>GⅢ架空S</td><td>東京</td><td>3歳</td>
          <td>芝1,600メートル</td><td></td><td></td><td>未掲載</td></tr>
          <tr><td>3月1日日曜</td><td>GⅢ未掲載S</td><td>東京</td><td>3歳</td>
          <td>芝1,600メートル</td><td></td><td></td><td>未掲載</td></tr>
        </table>
        """
        self.assertEqual(parse_listing_html(
            html, year=2025, listing_url="https://www.jra.go.jp/datafile/seiseki/replay/2025/jyusyo.html"
        ), [])


if __name__ == "__main__":
    unittest.main()
