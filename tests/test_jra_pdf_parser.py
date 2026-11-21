import unittest

from jra_pdf_parser import _ocr_declared_field_size, _propagate_pdf_identity, _strip_race_heading, _complete_ocr_records, _match_ocr_identifier_rows, _ocr_identifier_pairs, _ocr_tsv_lines, _ocr_identifier_column, _ocr_identifier_column_slots, _pair_identifier_columns, _infer_identifier_pairs_from_complete_horse_column, _infer_identifier_pairs_from_complete_frame_column, _build_ocr_runner_lines, parse_race_text, _ocr_single_horse_number, _ocr_single_frame_number, _identifier_column_inner_bounds, _unmatched_trailing_identifier_slots, _trailing_identifier_slot_center, _internal_identifier_slot_centers


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

    def test_reads_only_one_unambiguous_ocr_headcount_label(self):
        self.assertEqual(_ocr_declared_field_size("payout text (16頭) other text"), 16)
        self.assertEqual(_ocr_declared_field_size("（１８頭）"), 18)
        self.assertIsNone(_ocr_declared_field_size("(1694)"))
        self.assertIsNone(_ocr_declared_field_size("(16頭) (17頭)"))
        self.assertIsNone(_ocr_declared_field_size("(19頭)"))

    def test_propagates_only_one_verified_pdf_wide_date_and_track(self):
        rows = [
            {"date": "2026-01-10", "track": "京都"},
            {"date": None, "track": ""},
            {"date": "2026-01-10", "track": "京都"},
        ]
        self.assertIs(_propagate_pdf_identity(rows), rows)
        self.assertEqual(
            [(row["date"], row["track"]) for row in rows],
            [("2026-01-10", "京都")] * 3,
        )

    def test_does_not_propagate_conflicting_or_unobserved_pdf_identity(self):
        rows = [
            {"date": "2026-01-10", "track": "京都"},
            {"date": "2026-01-11", "track": "京都"},
            {"date": None, "track": None},
        ]
        _propagate_pdf_identity(rows)
        self.assertIsNone(rows[2]["date"])
        self.assertEqual(rows[2]["track"], "京都")
        unknown = [{"date": None, "track": None}]
        _propagate_pdf_identity(unknown)
        self.assertIsNone(unknown[0]["date"])
        self.assertIsNone(unknown[0]["track"])

    def test_ocr_heading_removal_preserves_metadata_on_same_line(self):
        line = "9月6日雨 (ダート)重 (2026年中山) 第1競走 3歳未勝利 1,200m"
        self.assertEqual(
            _strip_race_heading(line),
            "9月6日雨 (ダート)重 (2026年中山)  3歳未勝利 1,200m",
        )

    def test_ocr_heading_only_line_becomes_empty(self):
        self.assertEqual(_strip_race_heading("第1競走"), "")

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

    def test_rejects_implausibly_long_ocr_finish_time(self):
        text = SAMPLE.replace("1：39．0", "23：39．0")
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["finish_time"])
        self.assertIsNone(rows[0]["finish_time_seconds"])
        self.assertIsNone(rows[0]["finish"])
        self.assertEqual(
            rows[0]["runner_validation_warnings"],
            ["invalid_finish_time_minutes"],
        )
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

    def test_ocr_metadata_beyond_first_eight_lines_is_parsed(self):
        text = SAMPLE.replace(
            "11月 8日 晴良 （2026年5東京） 第1競走 2歳未勝利 1，600ｍ",
            "第1競走 2歳未勝利\\n見出し補足1\\n見出し補足2\\n見出し補足3\\n"
            "見出し補足4\\n見出し補足5\\n見出し補足6\\n"
            "11月 8日 晴良 （2026年5東京） 1，600ｍ",
        )
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["date"], "2026-11-08")
        self.assertEqual(rows[0]["track"], "東京")
        self.assertEqual(rows[0]["distance"], 1600)
        self.assertEqual(rows[0]["going"], "良")

    def test_surface_metadata_beyond_first_eight_lines_is_parsed(self):
        text = SAMPLE.replace(
            "11月 8日 晴良 （2026年5東京） 第1競走 2歳未勝利 1，600ｍ",
            "第1競走 2歳未勝利\\n見出し補足1\\n見出し補足2\\n見出し補足3\\n"
            "見出し補足4\\n見出し補足5\\n見出し補足6\\n"
            "11月 8日 晴良 （2026年5東京） 1，600ｍ",
        ).replace("（ダート・左）", "（芝・左）")
        rows = parse_race_text(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["surface"], "芝")

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
                "track": "東京",
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

    def test_ocr_fallback_rejects_missing_finish_time_without_explicit_status(self):
        rows = parse_race_text(SAMPLE)
        rows[0]["horse_number"], rows[0]["frame"] = 1, 1
        rows[1]["horse_number"], rows[1]["frame"] = 2, 2
        rows[0]["finish_time"] = None
        rows[0]["finish_time_seconds"] = None
        self.assertEqual(_complete_ocr_records(rows), [])

        # An explicit JRA terminal status is a valid alternative to a time.
        rows[0]["result_status"] = "競走中止"
        accepted = _complete_ocr_records(rows)
        self.assertEqual(len(accepted), 2)

    def test_ocr_fallback_rejects_entire_race_with_legacy_impossible_finish_time(self):
        rows = parse_race_text(SAMPLE.replace("1：39．0", "23：39．0"))
        rows[0]["horse_number"], rows[0]["frame"] = 1, 1
        rows[1]["horse_number"], rows[1]["frame"] = 2, 2
        self.assertEqual(rows[0]["finish_time_seconds"], None)
        self.assertIn("invalid_finish_time_minutes", rows[0]["runner_validation_warnings"])
        self.assertEqual(_complete_ocr_records(rows), [])

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
                "track": "東京",
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
        pairs = _ocr_identifier_pairs("4\\6\n6 }10\n3} 3\n711\n8 |14")
        self.assertEqual(pairs, [(4, 6), (6, 10), (3, 3), (7, 11), (8, 14)])

    def test_ocr_identifier_reader_marks_unreadable_rows_instead_of_guessing(self):
        pairs = _ocr_identifier_pairs("a}7\n4|19")
        self.assertEqual(pairs, [None, None])



    def test_pairs_separately_ocrd_frame_and_horse_columns(self):
        paired = _pair_identifier_columns(
            [(100.0, 1), (140.0, 5), (180.0, 3)],
            [(101.0, 1), (141.0, 5), (181.0, 3)],
        )
        self.assertEqual(paired, [
            (100.5, (1, 1)), (140.5, (5, 5)), (180.5, (3, 3))
        ])

    def test_infers_missing_horse_numbers_from_complete_aligned_frame_column(self):
        entries = [
            (4, 8), (3, 5), (4, 7), (6, 11), (6, 12), (1, 1),
            (8, 16), (7, 13), (5, 9), (7, 14), (1, 2), (5, 10),
            (2, 4), (2, 3), (3, 6), (8, 15),
        ]
        frame_rows = [(100.0 + index * 40, frame) for index, (frame, _) in enumerate(entries)]
        horse_rows = [
            (100.0 + index * 40, horse)
            for index, (_, horse) in enumerate(entries)
            if horse not in {5, 12}
        ]

        inferred = _infer_identifier_pairs_from_complete_frame_column(frame_rows, horse_rows)

        self.assertEqual([pair for _, pair in inferred], entries)

    def test_repairs_one_misread_horse_number_only_when_complete_frame_proves_it(self):
        entries = [
            (3, 6), (2, 3), (8, 16), (6, 11), (7, 13), (5, 9),
            (1, 2), (7, 14), (1, 1), (2, 4), (4, 7), (8, 15),
            (6, 12), (4, 8), (3, 5), (5, 10),
        ]
        frame_rows = [(100.0 + index * 40, frame) for index, (frame, _) in enumerate(entries)]
        horse_rows = [
            (100.0 + index * 40, 3 if index == 14 else horse)
            for index, (_, horse) in enumerate(entries)
        ]
        inferred = _infer_identifier_pairs_from_complete_frame_column(frame_rows, horse_rows)
        self.assertEqual([pair for _, pair in inferred], entries)

    def test_repairs_one_missing_horse_and_its_frame_only_when_all_other_pairs_prove_it(self):
        entries = [
            (8, 9), (6, 6), (7, 8), (1, 1), (5, 5),
            (2, 2), (7, 7), (8, 10), (3, 3), (4, 4),
        ]
        frame_rows = [(100.0 + index * 30, frame) for index, (frame, _) in enumerate(entries)]
        # The fifth row's frame is misread as 3 and its horse number is unreadable.
        frame_rows[4] = (frame_rows[4][0], 3)
        horse_rows = [
            (100.0 + index * 30, None if index == 4 else horse)
            for index, (_, horse) in enumerate(entries)
        ]
        inferred = _infer_identifier_pairs_from_complete_frame_column(frame_rows, horse_rows)
        self.assertEqual([pair for _, pair in inferred], entries)

    def test_does_not_repair_missing_horse_when_another_row_is_also_unresolved(self):
        entries = [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6)]
        frame_rows = [(100.0 + index * 30, frame) for index, (frame, _) in enumerate(entries)]
        frame_rows[4] = (frame_rows[4][0], 3)
        horse_rows = [
            (100.0 + index * 30, None if index in {3, 4} else horse)
            for index, (_, horse) in enumerate(entries)
        ]
        self.assertEqual(_infer_identifier_pairs_from_complete_frame_column(frame_rows, horse_rows), [])

    def test_does_not_repair_multiple_misread_horse_numbers(self):
        entries = [
            (3, 6), (2, 3), (8, 16), (6, 11), (7, 13), (5, 9),
            (1, 2), (7, 14), (1, 1), (2, 4), (4, 7), (8, 15),
            (6, 12), (4, 8), (3, 5), (5, 10),
        ]
        frame_rows = [(100.0 + index * 40, frame) for index, (frame, _) in enumerate(entries)]
        horse_rows = [
            (100.0 + index * 40, (3 if index == 14 else 8 if index == 5 else horse))
            for index, (_, horse) in enumerate(entries)
        ]
        self.assertEqual(
            _infer_identifier_pairs_from_complete_frame_column(frame_rows, horse_rows), []
        )

    def test_infers_missing_horses_when_ocr_rows_are_present_but_unreadable(self):
        entries = [
            (4, 8), (3, 5), (4, 7), (6, 11), (6, 12), (1, 1),
            (8, 16), (7, 13), (5, 9), (7, 14), (1, 2), (5, 10),
            (2, 4), (2, 3), (3, 6), (8, 15),
        ]
        frame_rows = [(100.0 + index * 40, frame) for index, (frame, _) in enumerate(entries)]
        horse_slots = [
            (100.0 + index * 40, None if horse in {5, 12} else horse)
            for index, (_, horse) in enumerate(entries)
        ]
        inferred = _infer_identifier_pairs_from_complete_frame_column(frame_rows, horse_slots)
        self.assertEqual([pair for _, pair in inferred], entries)

    def test_does_not_infer_two_missing_horses_in_the_same_frame(self):
        entries = [
            (4, 8), (3, 5), (3, 6), (6, 11), (6, 12), (1, 1),
            (8, 16), (7, 13), (5, 9), (7, 14), (1, 2), (5, 10),
            (2, 4), (2, 3), (4, 7), (8, 15),
        ]
        frame_rows = [(100.0 + index * 40, frame) for index, (frame, _) in enumerate(entries)]
        horse_rows = [
            (100.0 + index * 40, horse)
            for index, (_, horse) in enumerate(entries)
            if horse not in {5, 6}
        ]

        self.assertEqual(
            _infer_identifier_pairs_from_complete_frame_column(frame_rows, horse_rows), []
        )

    def test_does_not_infer_when_a_recognized_horse_has_the_wrong_frame(self):
        frame_rows = [(100.0, 1), (140.0, 2), (180.0, 3), (220.0, 4)]
        horse_rows = [(100.0, 1), (140.0, 2), (180.0, 3), (220.0, 4)]
        frame_rows[2] = (180.0, 4)

        self.assertEqual(
            _infer_identifier_pairs_from_complete_frame_column(frame_rows, horse_rows), []
        )

    def test_infers_frame_column_from_complete_horse_number_sequence(self):
        rows = _infer_identifier_pairs_from_complete_horse_column(
            [(100.0, 1), (140.0, 2), (180.0, 3), (220.0, 4),
             (260.0, 5), (300.0, 6), (340.0, 7), (380.0, 8)]
        )
        self.assertEqual(
            rows,
            [(100.0, (1, 1)), (140.0, (2, 2)), (180.0, (3, 3)),
             (220.0, (4, 4)), (260.0, (5, 5)), (300.0, (6, 6)),
             (340.0, (7, 7)), (380.0, (8, 8))],
        )

    def test_infers_official_frame_assignments_for_complete_twelve_horse_field(self):
        rows = _infer_identifier_pairs_from_complete_horse_column(
            [(float(number * 10), number) for number in range(1, 13)]
        )
        self.assertEqual([pair for _, pair in rows], [
            (1, 1), (2, 2), (3, 3), (4, 4), (5, 5),
            (5, 6), (6, 7), (6, 8), (7, 9), (7, 10), (8, 11), (8, 12),
        ])

    def test_does_not_infer_frames_from_incomplete_or_duplicate_horse_numbers(self):
        self.assertEqual(
            _infer_identifier_pairs_from_complete_horse_column(
                [(100.0, 1), (140.0, 2), (180.0, 4)]
            ),
            [],
        )
        self.assertEqual(
            _infer_identifier_pairs_from_complete_horse_column(
                [(100.0, 1), (140.0, 2), (180.0, 2)]
            ),
            [],
        )

    def test_rejects_misaligned_separate_identifier_columns(self):
        self.assertEqual(_pair_identifier_columns(
            [(100.0, 1), (140.0, 5)],
            [(100.0, 1), (170.0, 5)],
        ), [])

    def test_single_frame_number_retry_accepts_only_one_frame_digit(self):
        self.assertEqual(_ocr_single_frame_number("7"), 7)
        self.assertIsNone(_ocr_single_frame_number("rf"))
        self.assertIsNone(_ocr_single_frame_number("17"))
        self.assertIsNone(_ocr_single_frame_number(""))

    def test_single_horse_number_retry_accepts_only_one_valid_digit_token(self):
        self.assertEqual(_ocr_single_horse_number("9"), 9)
        self.assertEqual(_ocr_single_horse_number(" 18 "), 18)
        self.assertIsNone(_ocr_single_horse_number("Q"))
        self.assertIsNone(_ocr_single_horse_number("19"))
        self.assertIsNone(_ocr_single_horse_number("1 2"))
        self.assertIsNone(_ocr_single_horse_number(""))

    def test_identifier_row_crops_exclude_table_rules(self):
        self.assertEqual(_identifier_column_inner_bounds(129, column="frame"), (30, 105))
        self.assertEqual(_identifier_column_inner_bounds(111, column="horse"), (15, 91))

    def test_detects_one_internal_ocr_row_gap_from_stable_spacing(self):
        rows = [
            (100.0, 1), (125.0, 2), (175.0, 4), (200.0, 5),
            (225.0, 6), (250.0, 7),
        ]
        self.assertEqual(
            _internal_identifier_slot_centers(rows),
            [(150.0, 25.0)],
        )

    def test_does_not_project_large_or_irregular_internal_ocr_gaps(self):
        self.assertEqual(
            _internal_identifier_slot_centers([
                (100.0, 1), (125.0, 2), (200.0, 5), (225.0, 6),
            ]),
            [],
        )
        self.assertEqual(
            _internal_identifier_slot_centers([
                (100.0, 1), (125.0, 2), (185.0, 4), (210.0, 5),
            ]),
            [],
        )

    def test_projects_one_trailing_identifier_slot_only_with_stable_spacing(self):
        rows = [(100.0 + index * 21.0, index + 1) for index in range(15)]
        self.assertAlmostEqual(
            _trailing_identifier_slot_center(rows, typical_gap=21.0, max_center=800.0),
            415.0,
        )
        self.assertAlmostEqual(
            _trailing_identifier_slot_center(rows, typical_gap=21.0, max_center=415.0),
            415.0,
        )
        self.assertIsNone(
            _trailing_identifier_slot_center(rows, typical_gap=21.0, max_center=414.0)
        )
        irregular = rows[:-1] + [(rows[-1][0] + 50.0, 15)]
        self.assertIsNone(
            _trailing_identifier_slot_center(irregular, typical_gap=21.0, max_center=800.0)
        )

    def test_alternate_ocr_adds_only_unmatched_trailing_identifier_slots(self):
        existing = [(100.0, 9), (120.0, 8), (140.0, 7)]
        alternate = [(100.5, 9), (120.5, 8), (140.5, 7), (160.0, 13), (200.0, 4)]
        self.assertEqual(
            _unmatched_trailing_identifier_slots(existing, alternate, typical_gap=20.0),
            [(160.0, 13)],
        )
        self.assertEqual(
            _unmatched_trailing_identifier_slots(existing, alternate, typical_gap=0),
            [],
        )

    def test_identifier_column_slots_preserve_unreadable_rows(self):
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t90\t15\n"
            "5\t1\t1\t1\t2\t1\t0\t50\t10\t10\t40\td\n"
            "5\t1\t1\t1\t3\t1\t0\t100\t10\t10\t90\t13\n"
        )
        self.assertEqual(
            _ocr_identifier_column_slots(
                tsv, scale=1, y_offset=0, allowed=r"(?:[1-9]|1[0-8])"
            ),
            [(5.0, 15), (55.0, None), (105.0, 13)],
        )

    def test_identifier_column_reader_does_not_strip_mixed_ocr_tokens(self):
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t0\t20\t10\t10\t90\t1]\n"
            "5\t1\t1\t1\t2\t1\t0\t60\t10\t10\t90\t11\n"
        )
        self.assertEqual(
            _ocr_identifier_column_slots(
                tsv, scale=1, y_offset=0, allowed=r"(?:[1-9]|1[0-8])"
            ),
            [(25.0, None), (65.0, 11)],
        )
        self.assertEqual(
            _ocr_identifier_column(
                tsv, scale=1, y_offset=0, allowed=r"(?:[1-9]|1[0-8])"
            ),
            [(65.0, 11)],
        )

    def test_identifier_column_reader_keeps_only_valid_single_column_values(self):
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t0\t20\t10\t10\t90\t5\n"
            "5\t1\t1\t1\t2\t1\t0\t60\t10\t10\t90\t9\n"
            "5\t1\t1\t1\t3\t1\t0\t100\t10\t10\t90\tO\n"
        )
        self.assertEqual(
            _ocr_identifier_column(tsv, scale=5, y_offset=30, allowed=r"[1-8]"),
            [(35.0, 5)]
        )

    def test_ocr_identifier_rows_require_vertical_alignment(self):
        matched = _match_ocr_identifier_rows(
            [100.0, 140.0, 180.0],
            [(101.0, (1, 1)), (141.0, (2, 2)), (181.0, (3, 3))],
        )
        self.assertEqual(matched, [(1, 1), (2, 2), (3, 3)])
        self.assertIsNone(_match_ocr_identifier_rows(
            [100.0, 140.0, 180.0],
            [(101.0, (1, 1)), (170.0, (2, 2)), (181.0, (3, 3))],
        ))

    def test_ocr_identifier_rows_reject_missing_or_extra_identifiers(self):
        self.assertIsNone(_match_ocr_identifier_rows(
            [100.0, 140.0], [(101.0, (1, 1))]
        ))
        self.assertIsNone(_match_ocr_identifier_rows(
            [100.0, 140.0], [(101.0, (1, 1)), (141.0, None)]
        ))

    def test_ocr_identifier_rows_ignore_extra_rows_outside_runner_positions(self):
        matched = _match_ocr_identifier_rows(
            [100.0, 140.0, 180.0],
            [(20.0, (8, 8)), (101.0, (1, 1)), (141.0, (2, 2)),
             (181.0, (3, 3)), (260.0, (4, 4))],
        )
        self.assertEqual(matched, [(1, 1), (2, 2), (3, 3)])

    def test_ocr_identifier_rows_reject_ambiguous_nearby_rows(self):
        self.assertIsNone(_match_ocr_identifier_rows(
            [100.0, 140.0],
            [(99.0, (1, 1)), (101.0, (1, 2)), (141.0, (2, 2))],
        ))

    def test_ocr_tsv_lines_converts_crop_coordinates_to_panel_coordinates(self):
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t0\t20\t10\t10\t90\t1\n"
        )
        lines = _ocr_tsv_lines(tsv, scale=5, y_offset=30)
        self.assertEqual(lines, [(35.0, "1")])


    def test_builds_runner_lines_from_row_ocr_and_trusted_identifiers(self):
        rows = [(100.0, (7, 14)), (140.0, (1, 2))]
        lines = _build_ocr_runner_lines(
            rows,
            ["メイショウイブキ 牝3鹿 55 1:23.4", "サンライズロイ 牡3鹿 57 1:24.0"],
        )
        self.assertEqual(lines, [
            "7|14 メイショウイブキ 牝3鹿 55 1:23.4",
            "1|2 サンライズロイ 牡3鹿 57 1:24.0",
        ])

    def test_rejects_incomplete_row_ocr_results(self):
        self.assertEqual(
            _build_ocr_runner_lines([(100.0, (7, 14)), (140.0, (1, 2))], ["馬名"]),
            [],
        )

if __name__ == "__main__":
    unittest.main()
