import unittest

from jra_replay_parser import parse_replay_text


SAMPLE = """
第76回 毎日王冠
2025.10.5 東京 曇・良 芝1800㍍
着順 馬番 馬名 性齢 斤量 騎手 タイム 上り（600㍍） コーナー
1 ⑨ レーベンスティール 牡５
57 津村明秀 1:44.0
3 - 3 - 3
33.3 484（± ０）
7.7⑤ 田中博康（美浦）
2 ⑧ ホウオウビスケッツ 牡５
57 岩田康誠
1/2
1 - 1 - 1
33.8 508（－６）
4.3② 奥村 武（美浦）
3 ⑩ サトノシャイニング 牡３
55 武 豊
3/4
2 - 2 - 2
33.7 498（＋２）
"""


class JraReplayParserTests(unittest.TestCase):
    def test_extracts_individual_closing_time_per_runner(self):
        rows = parse_replay_text(SAMPLE, source_url="https://example.test/replay.pdf")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["horse_name"], "レーベンスティール")
        self.assertEqual(rows[0]["horse_number"], 9)
        self.assertEqual(rows[0]["finish"], 1)
        self.assertEqual(rows[0]["individual_last3f"], 33.3)
        self.assertEqual(rows[1]["individual_last3f"], 33.8)
        self.assertEqual(rows[2]["individual_last3f"], 33.7)
        self.assertEqual(rows[0]["corner_positions"], "3-3-3")
        self.assertEqual(rows[0]["date"], "2025-10-05")
        self.assertEqual(rows[0]["track"], "東京")
        self.assertEqual(rows[0]["surface"], "芝")
        self.assertEqual(rows[0]["distance"], 1800)
        self.assertEqual(rows[0]["going"], "良")
        self.assertEqual(rows[0]["source_type"], "jra_graded_race_replay_pdf")

    def test_does_not_guess_missing_runner_closing_time(self):
        text = SAMPLE.replace("33.8 508（－６）", "508（－６）")
        rows = parse_replay_text(text)
        self.assertIsNone(rows[1]["individual_last3f"])

    def test_uses_supplied_metadata_when_header_is_incomplete(self):
        rows = parse_replay_text(
            SAMPLE.replace("2025.10.5 東京 曇・良 芝1800㍍", ""),
            race_metadata={"date": "2025-10-05", "track": "東京", "race_number": 11,
                           "race_name": "毎日王冠", "surface": "芝", "distance": 1800},
        )
        self.assertEqual(rows[0]["race_number"], 11)
        self.assertEqual(rows[0]["race_name"], "毎日王冠")

    def test_returns_empty_when_no_runner_table_exists(self):
        self.assertEqual(parse_replay_text("これは説明文だけです"), [])


if __name__ == "__main__":
    unittest.main()
