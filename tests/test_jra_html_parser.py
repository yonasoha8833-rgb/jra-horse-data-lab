import unittest

from jra_pdf_parser import (
    _official_result_url,
    _parse_official_result_html,
    _race_identity_from_pdf_url,
)


SOURCE = "https://www.jra.go.jp/datafile/seiseki/report/2026/2026-4kyoto9.pdf"
OCR_HEADER = "第4回 京都競馬 第9日 10月25日"


class JraOfficialHtmlParserTests(unittest.TestCase):
    def test_builds_official_jra_result_url(self):
        identity = _race_identity_from_pdf_url(SOURCE, OCR_HEADER)
        self.assertEqual(identity["track"], "京都")
        self.assertEqual(identity["date"], "2026-10-25")
        self.assertEqual(
            _official_result_url(identity, 1),
            "https://own.jra.jp/JRADB/accessS.html?CNAME=pw01sde0108202604090120261025/A3",
        )

    def test_parses_official_result_table(self):
        identity = _race_identity_from_pdf_url(SOURCE, OCR_HEADER)
        html = """
        <html><body>
        <p>2026年10月25日（日曜） 4回京都9日</p>
        <p>天候小雨 ダート不良</p>
        <h2>2歳未勝利</h2>
        <p>コース：1,800メートル（ダート・右）</p>
        <table>
          <tr><th>着順</th><th>枠</th><th>馬番</th><th>馬名</th><th>性齢</th>
          <th>負担重量</th><th>騎手名</th><th>タイム</th><th>着差</th>
          <th>コーナー</th><th>推定上り</th><th>馬体重</th><th>調教師</th><th>人気</th></tr>
          <tr><td>1</td><td>1</td><td>1</td><td>ナオミガサイキョウ</td><td>牝2</td>
          <td>55.0</td><td>松若風馬</td><td>1:52.8</td><td></td><td>4-4-3-2</td>
          <td>38.4</td><td>502(-4)</td><td>佐藤悠太</td><td>1</td></tr>
          <tr><td>2</td><td>5</td><td>5</td><td>アップルペアー</td><td>牝2</td>
          <td>55.0</td><td>幸英明</td><td>1:54.2</td><td>8</td><td>8-8-8-8</td>
          <td>38.2</td><td>422(0)</td><td>大根田裕之</td><td>7</td></tr>
        </table>
        <p>上り 4F 51.4 - 3F 38.6</p>
        </body></html>
        """
        rows = _parse_official_result_html(
            html, identity=identity, race_number=1, source_url=SOURCE
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["horse_name"], "ナオミガサイキョウ")
        self.assertEqual(rows[0]["horse_number"], 1)
        self.assertEqual(rows[0]["finish"], 1)
        self.assertEqual(rows[0]["finish_time_seconds"], 112.8)
        self.assertEqual(rows[0]["individual_last3f"], 38.4)
        self.assertEqual(rows[0]["race_last3f"], 38.6)
        self.assertEqual(rows[0]["track"], "京都")
        self.assertEqual(rows[0]["surface"], "ダート")
        self.assertEqual(rows[0]["distance"], 1800)
        self.assertEqual(rows[0]["going"], "不良")
        self.assertEqual(rows[0]["extraction_status"], "official_html")


if __name__ == "__main__":
    unittest.main()
