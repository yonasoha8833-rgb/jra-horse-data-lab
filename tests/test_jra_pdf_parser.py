import unittest

from jra_pdf_parser import parse_race_text


SAMPLE = """
11月 8日 晴良 （2026年5東京） 第1競走 2歳未勝利 1，600ｍ
発走 9時55分 （ダート・左）
本賞 5，900，000円
2 2 サノノファイター 牡2鹿 56 田辺 裕信 佐野 信幸氏 栗田 徹 484±0 1：39．0
6 7 ブルーサンタ 牡2青鹿56 戸崎 圭太 SNSグループ 452＋2 1：39．2
（2頭）
ハロンタイム 12．4―10．8―11．5―12．3
「通過コーナー順 3→4」 上り 4F 52．0―3F 39．4
"""


class JraPdfParserTests(unittest.TestCase):
    def test_extracts_runner_and_race_level_fields(self):
        rows = parse_race_text(SAMPLE, source_url="https://example.test/race.pdf")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["horse_name"], "サノノファイター")
        self.assertEqual(rows[0]["finish"], 1)
        self.assertEqual(rows[1]["horse_number"], 7)
        self.assertEqual(rows[0]["track"], "東京")
        self.assertEqual(rows[0]["surface"], "ダート")
        self.assertEqual(rows[0]["distance"], 1600)
        self.assertEqual(rows[0]["race_last3f"], 39.4)
        self.assertIsNone(rows[0]["individual_last3f"])
        self.assertEqual(rows[0]["race_lap_times"], [12.4, 10.8, 11.5, 12.3])


if __name__ == "__main__":
    unittest.main()
