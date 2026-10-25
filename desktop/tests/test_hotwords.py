import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import legacy_hotwords


class HotwordTests(unittest.TestCase):
    def test_seed_and_import_preserve_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "dictionary"
            with patch.object(legacy_hotwords, "HOT_HOME", folder), patch.object(
                legacy_hotwords, "initial_defaults", return_value=None
            ):
                legacy_hotwords.initialize_files()
                legacy_hotwords.write_files({
                    "hot-zh.txt": "周深\n",
                    "hot-rule.txt": "毫安时 = mAh\n",
                })
                old = root / "old"
                old.mkdir()
                (old / "hot-zh.txt").write_text("周深\n申珊靛\n", encoding="utf-8")
                (old / "hot-rule.txt").write_text("毫安时 = mAH\n赫兹 = Hz\n", encoding="utf-8")
                counts = legacy_hotwords.import_legacy(old)
                self.assertEqual(counts["hot-zh.txt"], 1)
                self.assertEqual(counts["hot-rule.txt"], 1)
                self.assertIn("申珊靛", legacy_hotwords.read_file("hot-zh.txt"))
                self.assertIn("毫安时 = mAh", legacy_hotwords.read_file("hot-rule.txt"))
                self.assertNotIn("毫安时 = mAH", legacy_hotwords.read_file("hot-rule.txt"))

    def test_invalid_regex_preserves_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(legacy_hotwords, "HOT_HOME", Path(tmp)), patch.object(
                legacy_hotwords, "initial_defaults", return_value=None
            ):
                legacy_hotwords.initialize_files()
                old = legacy_hotwords.read_file("hot-rule.txt")
                with self.assertRaises(Exception):
                    legacy_hotwords.write_files({"hot-rule.txt": "( = bad"})
                self.assertEqual(legacy_hotwords.read_file("hot-rule.txt"), old)

    def test_english_and_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(legacy_hotwords, "HOT_HOME", Path(tmp)), patch.object(
                legacy_hotwords, "initial_defaults", return_value=None
            ):
                legacy_hotwords.initialize_files()
                legacy_hotwords.write_files({
                    "hot-en.txt": "ChatGPT\n",
                    "hot-rule.txt": "毫安时 = mAh\n",
                })
                engine = legacy_hotwords.HotwordEngine()
                settings = {"heteronym": False, "tone": False, "hot_zh": False, "hot_en": True,
                            "hot_rule": True, "hot_kwd": False, "hotwords": {}}
                result = engine.apply("chat gpt 有10毫安时", settings, lambda t, m: t)
                self.assertIn("ChatGPT", result)
                self.assertIn("mAh", result)


if __name__ == "__main__":
    unittest.main()
