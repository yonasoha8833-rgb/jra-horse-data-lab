import unittest

from jra_registry_parser import build_unique_name_index, parse_registry_text


class JraRegistryParserTests(unittest.TestCase):
    def test_parses_name_followed_by_registration_number(self):
        rows = parse_registry_text("アイファーバトル 2023-01631")
        self.assertEqual(rows, [{
            "horse_name": "アイファーバトル",
            "horse_registration_number": "2023-01631",
        }])

    def test_parses_registration_number_followed_by_name(self):
        rows = parse_registry_text("2023-01631 アイファーバトル")
        self.assertEqual(rows[0]["horse_name"], "アイファーバトル")

    def test_rejects_lines_with_multiple_numbers(self):
        self.assertEqual(parse_registry_text("馬名 2023-01631 2024-00001"), [])

    def test_rejects_ambiguous_multi_column_line(self):
        self.assertEqual(parse_registry_text("馬名 2023-01631 父名 母名"), [])

    def test_rejects_invalid_registration_number(self):
        self.assertEqual(parse_registry_text("馬名 202301631"), [])

    def test_duplicate_registration_number_is_rejected_for_all_names(self):
        rows = parse_registry_text("馬A 2023-01631\\n馬B 2023-01631")
        self.assertEqual(rows, [])

    def test_unique_name_index_excludes_same_name_conflicts(self):
        rows = [
            {"horse_name": "同名馬", "horse_registration_number": "2023-00001"},
            {"horse_name": "同名馬", "horse_registration_number": "2024-00002"},
            {"horse_name": "一意馬", "horse_registration_number": "2022-00003"},
        ]
        self.assertEqual(build_unique_name_index(rows), {"一意馬": "2022-00003"})


if __name__ == "__main__":
    unittest.main()
