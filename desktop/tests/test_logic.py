import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from learning import propose_correction, replace_hotwords
from model_store import is_ready, normalize_model_path
from preferences import load_settings, save_settings, parse_mappings


class LogicTests(unittest.TestCase):
    def test_hotword_replacements(self):
        self.assertEqual(replace_hotwords("chatgpt与毫安时", {"chatgpt": "ChatGPT", "毫安时": "mAh"}),
                         "ChatGPT与mAh")

    def test_correction_inside_insertion(self):
        self.assertEqual(propose_correction("前文我使用安格。", "前文我使用angr。", 2, 8),
                         ("安格", "angr"))

    def test_reject_changes_outside_insertion(self):
        self.assertIsNone(propose_correction("ABC hello", "ZBC hello", 4, 9))

    def test_reject_deletion(self):
        self.assertIsNone(propose_correction("ABC hello", "ABC he", 4, 9))

    def test_parse_and_save(self):
        import preferences
        with tempfile.TemporaryDirectory() as d:
            with patch.object(preferences, "APP_HOME", Path(d)), patch.object(
                preferences, "SETTINGS_PATH", Path(d) / "prefs.json"
            ):
                loaded = load_settings()
                loaded["hotwords"] = parse_mappings("# comment\n安格 = angr\n")
                save_settings(loaded)
                self.assertEqual(load_settings()["hotwords"]["安格"], "angr")

    def test_missing_model(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(is_ready(d))

    def test_models_in_nested_folder(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "models" / "paraformer-offline-zh").mkdir(parents=True)
            self.assertEqual(normalize_model_path(root), root.resolve() / "models")


if __name__ == "__main__":
    unittest.main()
