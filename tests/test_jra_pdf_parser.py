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
    def test_accepts_compact_frame_and_horse_number(self):
        compact_sample = SAMPLE.replace("2 2 サノノファイター", "22 サノノファイター")
        rows = parse_race_text(compact_sample)
        self.assertEqual(rows[0]["frame"], 2)
        self.assertEqual(rows[0]["horse_number"], 2)

    def test_extracts_runner_and_race_level_fields(self):
        rows = parse_race_text(SAMPLE, source_url="https://example.test/race.pdf")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["horse_name"], "サノノファイター")
        self.assertEqual(rows[0]["finish"], 1)
        self.assertEqual(rows[0]["field_size"], 2)
        self.assertEqual(rows[0]["finish_time"], "1:39.0")
        self.assertAlmostEqual(rows[0]["finish_time_seconds"], 99.0)
        self.assertEqual(rows[1]["finish_time"], "1:39.2")
        self.assertEqual(rows[1]["horse_number"], 7)
        self.assertEqual(rows[0]["track"], "東京")
        self.assertEqual(rows[0]["surface"], "ダート")
        self.assertEqual(rows[0]["distance"], 1600)
        self.assertEqual(rows[0]["race_last3f"], 39.4)
        self.assertIsNone(rows[0]["individual_last3f"])
        self.assertEqual(rows[0]["race_lap_times"], [12.4, 10.8, 11.5, 12.3])


    def test_accepts_distance_without_thousands_separator(self):
        text = SAMPLE.replace("1，600ｍ", "1600ｍ")
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["distance"], 1600)


    def test_invalid_calendar_date_is_not_stored(self):
        text = SAMPLE.replace("11月 8日", "2月 30日")
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["date"])

    def test_leap_day_is_validated_with_race_year(self):
        text = SAMPLE.replace("11月 8日", "2月 29日").replace("2026年", "2024年")
        rows = parse_race_text(text)
        self.assertEqual(rows[0]["date"], "2024-02-29")

    def test_implausible_distance_is_left_missing(self):
        text = SAMPLE.replace("1，600ｍ", "99，999ｍ")
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["distance"])

    def test_ignores_non_runner_lines(self):
        text = SAMPLE.replace("（2頭）", "（2頭）\n発走 9時55分")
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)


    def test_non_finisher_does_not_shift_later_finish_positions(self):
        text = SAMPLE.replace(
            "6 7 ブルーサンタ 牡2青鹿56 戸崎 圭太 SNSグループ 452＋2 1：39．2",
            "6 7 ブルーサンタ 牡2青鹿56 戸崎 圭太 SNSグループ 452＋2 中止\\n"
            "3 3 テストホース 牡3鹿56 横山 武史 テスト 470＋0 1：40．0",
        )
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["finish"], 1)
        self.assertIsNone(rows[1]["finish"])
        self.assertEqual(rows[2]["finish"], 2)
        self.assertIsNone(rows[1]["finish_time_seconds"])


    def test_does_not_mistake_race_year_for_distance(self):
        text = SAMPLE.replace("1，600ｍ", "距離不明")
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["distance"])

    def test_accepts_ascii_meter_suffix(self):
        text = SAMPLE.replace("1，600ｍ", "1600m")
        rows = parse_race_text(text)
        self.assertEqual(rows[0]["distance"], 1600)


    def test_disqualified_runner_keeps_status_and_has_no_finish_place(self):
        text = SAMPLE.replace(
            "2 2 サノノファイター 牡2鹿 56 田辺 裕信 佐野 信幸氏 栗田 徹 484±0 1：39．0",
            "2 2 サノノファイター 牡2鹿 56 田辺 裕信 佐野 信幸氏 栗田 徹 484±0 1：39．0 失格",
        )
        rows = parse_race_text(text)
        self.assertEqual(rows[0]["result_status"], "失格")
        self.assertIsNone(rows[0]["finish"])
        self.assertEqual(rows[1]["finish"], 1)


    def test_status_word_in_owner_name_is_not_misclassified(self):
        text = SAMPLE.replace(
            "佐野 信幸氏 栗田 徹",
            "中止商事 栗田 徹",
        )
        rows = parse_race_text(text)
        self.assertIsNone(rows[0]["result_status"])
        self.assertEqual(rows[0]["finish"], 1)


    def test_status_word_after_time_must_be_terminal(self):
        text = SAMPLE.replace("1：39．0", "1：39．0 備考：中止予定")
        rows = parse_race_text(text)
        self.assertIsNone(rows[0]["result_status"])
        self.assertEqual(rows[0]["finish"], 1)

    def test_status_after_finish_time_is_detected(self):
        text = SAMPLE.replace("1：39．0", "1：39．0 失格")
        rows = parse_race_text(text)
        self.assertEqual(rows[0]["result_status"], "失格")
        self.assertIsNone(rows[0]["finish"])

    def test_non_finisher_status_is_recorded(self):
        text = SAMPLE.replace("1：39．2", "中止")
        rows = parse_race_text(text)
        self.assertEqual(rows[1]["result_status"], "中止")
        self.assertIsNone(rows[1]["finish"])


if __name__ == "__main__":
    unittest.main()
