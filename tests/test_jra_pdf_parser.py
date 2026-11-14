import unittest

from jra_pdf_parser import _complete_ocr_records, _ocr_identifier_pairs, parse_race_text


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

    def test_rejects_race_number_outside_official_range(self):
        text = SAMPLE.replace("第1競走", "第13競走")
        self.assertEqual(parse_race_text(text), [])

    def test_invalid_finish_time_is_not_used_for_placing(self):
        text = SAMPLE.replace("1：39．0", "1：99．0")
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["finish_time"])
        self.assertIsNone(rows[0]["finish_time_seconds"])
        self.assertEqual(rows[0]["finish"], None)
        self.assertEqual(rows[0]["runner_validation_warnings"], ["invalid_finish_time_seconds"])
        self.assertEqual(rows[1]["finish"], 1)

    def test_accepts_ocr_pipe_delimited_jra_runner_row(self):
        ocr_row = "6|7| ウォータージェード|牡2和|56 | 松若風馬 1:08.5"
        text = SAMPLE.replace(
            "2 2 サノノファイター 牡2鹿 56 田辺 裕信 佐野 信幸氏 栗田 徹 484±0 1：39．0",
            ocr_row,
        )
        rows = parse_race_text(text)
        self.assertEqual(rows[0]["frame"], 6)
        self.assertEqual(rows[0]["horse_number"], 7)
        self.assertEqual(rows[0]["horse_name"], "ウォータージェード")
        self.assertEqual(rows[0]["sex"], "牡")
        self.assertEqual(rows[0]["age"], 2)

    def test_accepts_ocr_pipe_separator_between_frame_and_horse_number(self):
        rows = parse_race_text(
            SAMPLE.replace("2 2 サノノファイター", "2|2 サノノファイター")
        )
        self.assertEqual(rows[0]["frame"], 2)
        self.assertEqual(rows[0]["horse_number"], 2)

    def test_accepts_ocr_backslash_separator_between_frame_and_horse_number(self):
        rows = parse_race_text(
            SAMPLE.replace("2 2 サノノファイター", r"8\12 サノノファイター")
        )
        self.assertEqual(rows[0]["frame"], 8)
        self.assertEqual(rows[0]["horse_number"], 12)

    def test_accepts_ocr_slash_separator_between_frame_and_horse_number(self):
        rows = parse_race_text(
            SAMPLE.replace("2 2 サノノファイター", "6/8 サノノファイター")
        )
        self.assertEqual(rows[0]["frame"], 6)
        self.assertEqual(rows[0]["horse_number"], 8)

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



    def test_extracts_track_going_condition_from_race_header(self):
        for header, expected in (
            ("晴良", "良"),
            ("曇稍重", "稍重"),
            ("雨重", "重"),
            ("小雨不良", "不良"),
        ):
            with self.subTest(header=header):
                rows = parse_race_text(SAMPLE.replace("晴良", header))
                self.assertEqual(rows[0]["going"], expected)


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

    def test_ocr_header_metadata_on_adjacent_lines_is_parsed(self):
        text = SAMPLE.replace(
            "11月 8日 晴良 （2026年5東京） 第1競走 2歳未勝利 1，600ｍ",
            "第1競走 2歳未勝利\n11月 8日 晴良 （2026年5東京）\n1，600ｍ",
        )
        rows = parse_race_text(text)
        self.assertEqual(rows[0]["date"], "2026-11-08")
        self.assertEqual(rows[0]["track"], "東京")
        self.assertEqual(rows[0]["distance"], 1600)

    def test_leap_day_is_validated_with_race_year(self):
        text = SAMPLE.replace("11月 8日", "2月 29日").replace("2026年", "2024年")
        rows = parse_race_text(text)
        self.assertEqual(rows[0]["date"], "2024-02-29")

    def test_implausible_distance_is_left_missing(self):
        text = SAMPLE.replace("1，600ｍ", "99，999ｍ")
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["distance"])


    def test_rejects_invalid_frame_and_horse_number(self):
        invalid_frame = SAMPLE.replace("2 2 サノノファイター", "9 2 サノノファイター")
        rows = parse_race_text(invalid_frame)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["horse_number"], 7)

        invalid_number = SAMPLE.replace("2 2 サノノファイター", "2 19 サノノファイター")
        rows = parse_race_text(invalid_number)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["horse_number"], 7)

    def test_rejects_impossible_horse_age(self):
        text = SAMPLE.replace("牡2鹿", "牡1鹿")
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["horse_name"], "ブルーサンタ")

    def test_accepts_upper_boundary_values(self):
        text = SAMPLE.replace("2 2 サノノファイター 牡2鹿", "8 18 サノノファイター 牡20鹿")
        rows = parse_race_text(text)
        self.assertEqual(rows[0]["frame"], 8)
        self.assertEqual(rows[0]["horse_number"], 18)
        self.assertEqual(rows[0]["age"], 20)


    def test_duplicate_horse_numbers_are_flagged(self):
        text = SAMPLE.replace(
            "6 7 ブルーサンタ",
            "6 2 ブルーサンタ",
        )
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["race_validation_warnings"], ["duplicate_horse_number:2"])
        self.assertEqual(rows[1]["race_validation_warnings"], ["duplicate_horse_number:2"])

    def test_declared_field_size_mismatch_is_flagged(self):
        text = SAMPLE.replace("（2頭）", "（3頭）")
        rows = parse_race_text(text)
        self.assertEqual(rows[0]["declared_field_size"], 3)
        self.assertEqual(rows[0]["field_size"], 2)
        self.assertIn(
            "declared_field_size_mismatch:declared=3,parsed=2",
            rows[0]["race_validation_warnings"],
        )

    def test_matching_declared_field_size_has_no_warnings(self):
        rows = parse_race_text(SAMPLE)
        self.assertEqual(rows[0]["declared_field_size"], 2)
        self.assertEqual(rows[0]["race_validation_warnings"], [])

    def test_ignores_non_runner_lines(self):
        text = SAMPLE.replace("（2頭）", "（2頭）\n発走 9時55分")
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)


    def test_non_finisher_does_not_shift_later_finish_positions(self):
        text = SAMPLE.replace(
            "6 7 ブルーサンタ 牡2青鹿56 戸崎 圭太 SNSグループ 452＋2 1：39．2",
            "6 7 ブルーサンタ 牡2青鹿56 戸崎 圭太 SNSグループ 452＋2 中止\n"
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

    def test_parses_official_distance_control_glyph(self):
        text = SAMPLE.replace("1，600ｍ", "1，600\x02")
        rows = parse_race_text(text)
        self.assertEqual(rows[0]["distance"], 1600)

    def test_dead_heat_marker_shares_previous_place_and_time(self):
        text = SAMPLE.replace(
            "6 7 ブルーサンタ 牡2青鹿56 戸崎 圭太 SNSグループ 452＋2 1：39．2",
            "6 7 ブルーサンタ 牡2青鹿56 戸崎 圭太 SNSグループ 452＋2 〃 アタマ",
        ).replace(
            "（2頭）",
            "6 6 タイセイアドーレ 牡2青鹿56 A．プーシャン 田中 成奉氏 伊藤 圭三 498＋0 1：39．8\\n（3頭）",
        )
        rows = parse_race_text(text)
        self.assertEqual(rows[0]["finish"], 1)
        self.assertEqual(rows[1]["finish"], 1)
        self.assertEqual(rows[1]["finish_time_seconds"], rows[0]["finish_time_seconds"])
        self.assertEqual(rows[2]["finish"], 3)

    def test_parses_actual_jra_pdf_layout_with_dead_heat(self):
        text = """11月 8日 晴良 （2026年5東京） 第2日 第1競走 2歳未勝利  1，600
発走 9時55分 （ダート・左）
2 2 サノノファイター 牡2鹿 56 田辺 裕信 佐野 信幸氏 栗田 徹 むかわ 上水牧場 484±01：39．0 1．8
6 7 ブルーサンタ 牡2青鹿56 戸崎 圭太 SNSグループ 斎藤 誠 浦河 宮内牧場 452＋21：39．2 5．6
4 4 ルボワ 牡2鹿 56 西村 淳也 亀岡 和彦氏 杉山 佳明 安平 ノーザンファーム 520±0 〃 アタマ 5．5
811 ココビスケット 牝2鹿 55 小林 脩斗 吉田 千津氏 小島 茂之 千歳 社台ファーム 436＋21：39．3 88．0
（11頭）
"""
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["finish"] for row in rows], [1, 2, 2, 4])
        self.assertEqual(rows[0]["distance"], 1600)
        self.assertEqual(rows[0]["track"], "東京")
        self.assertEqual(rows[0]["surface"], "ダート")
        self.assertEqual(rows[2]["finish_time_seconds"], rows[1]["finish_time_seconds"])

    def test_ocr_fallback_infers_full_field_from_unique_jra_frame_layout(self):
        frame_by_number = {
            **{number: number for number in range(1, 7)},
            7: 7, 8: 7, 9: 8, 10: 8,
        }
        rows = [
            {
                "source_url": "official.pdf",
                "date": "2026-11-14",
                "race_number": 1,
                "horse_number": number,
                "frame": frame_by_number[number],
                "declared_field_size": None,
            }
            for number in range(1, 11)
        ]
        accepted = _complete_ocr_records(rows)
        self.assertEqual(len(accepted), 10)
        self.assertTrue(all(row["declared_field_size"] == 10 for row in accepted))

    def test_ocr_fallback_rejects_ambiguous_or_incomplete_inferred_field(self):
        frame_by_number = {
            **{number: number for number in range(1, 7)},
            7: 7, 8: 7, 9: 8, 10: 8,
        }
        rows = [
            {
                "source_url": "official.pdf",
                "date": "2026-11-14",
                "race_number": 1,
                "horse_number": number,
                "frame": frame_by_number[number],
                "declared_field_size": None,
            }
            for number in range(1, 10)
        ]
        self.assertEqual(_complete_ocr_records(rows), [])

    def test_ocr_fallback_keeps_only_races_with_full_declared_field(self):
        rows = parse_race_text(SAMPLE)
        rows[0]["horse_number"], rows[0]["frame"] = 1, 1
        rows[1]["horse_number"], rows[1]["frame"] = 2, 2
        accepted = _complete_ocr_records(rows)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(_complete_ocr_records(rows[:1]), [])
        self.assertTrue(all(
            row["extraction_status"] == "ocr_fallback_basic_fields"
            for row in accepted
        ))

    def test_ocr_fallback_rejects_duplicate_number_even_when_row_count_matches(self):
        rows = parse_race_text(SAMPLE)
        rows[0]["horse_number"], rows[0]["frame"] = 1, 1
        rows[1]["horse_number"], rows[1]["frame"] = 1, 1
        self.assertEqual(_complete_ocr_records(rows), [])

    def test_ocr_fallback_repairs_leading_one_only_when_full_frame_layout_proves_it(self):
        frame_by_number = {
            1: 1, 2: 2, 3: 3, 4: 4,
            5: 5, 6: 5, 7: 6, 8: 6,
            9: 7, 10: 7, 11: 8, 12: 8,
        }
        rows = [
            {
                "source_url": "official.pdf",
                "date": "2026-11-14",
                "race_number": 1,
                "horse_number": number,
                "frame": frame_by_number[number],
                "declared_field_size": None,
            }
            for number in range(1, 13)
        ]
        # Typical OCR errors: 2 -> 12 and 5 -> 15.
        rows[1]["horse_number"] = 12
        rows[4]["horse_number"] = 15
        accepted = _complete_ocr_records(rows)
        self.assertEqual(len(accepted), 12)
        self.assertEqual(sorted(row["horse_number"] for row in accepted), list(range(1, 13)))
        self.assertTrue(all(row["declared_field_size"] == 12 for row in accepted))

    def test_ocr_fallback_rejects_wrong_frame_assignment(self):
        rows = parse_race_text(SAMPLE)
        rows[0]["horse_number"], rows[0]["frame"] = 1, 2
        rows[1]["horse_number"], rows[1]["frame"] = 2, 2
        self.assertEqual(_complete_ocr_records(rows), [])



    def test_accepts_ocr_brace_separator_between_frame_and_horse_number(self):
        rows = parse_race_text(
            SAMPLE.replace("2 2 サノノファイター", "2 {2 サノノファイター")
        )
        self.assertEqual(rows[0]["frame"], 2)
        self.assertEqual(rows[0]["horse_number"], 2)

    def test_joins_ocr_split_two_digit_horse_number(self):
        rows = parse_race_text(
            SAMPLE.replace("2 2 サノノファイター", "2 1 2 サノノファイター")
        )
        self.assertEqual(rows[0]["frame"], 2)
        self.assertEqual(rows[0]["horse_number"], 12)

    def test_synthetic_race_header_restores_number_when_ocr_header_is_missing(self):
        text = "第3競走\\n" + SAMPLE.replace("第1競走", "")
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["race_number"] == 3 for row in rows))


    def test_ocr_identifier_reader_handles_separators_and_compact_numbers(self):
        pairs = _ocr_identifier_pairs("4\\\\6\\n6 }10\\n3} 3\\n711\\n8 |14")
        self.assertEqual(pairs, [(4, 6), (6, 10), (3, 3), (7, 11), (8, 14)])

    def test_ocr_identifier_reader_marks_unreadable_rows_instead_of_guessing(self):
        pairs = _ocr_identifier_pairs("a}7\\n4|19")
        self.assertEqual(pairs, [None, (4, 19)])


if __name__ == "__main__":
    unittest.main()
