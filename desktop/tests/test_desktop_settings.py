import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from preferences import DEFAULTS, validate_settings
from key_bindings import parse_shortcut, KeyBinding
from record_store import save_record


class DesktopSettingsTest(unittest.TestCase):
    def test_shortcuts(self):
        self.assertEqual(parse_shortcut("caps lock"), ((), "caps lock"))
        self.assertEqual(parse_shortcut("ctrl+alt+v"), (("ctrl", "alt"), "v"))
        for key in ("", "ctrl", "a", "ctrl+ctrl+a"):
            with self.assertRaises(ValueError, msg=key):
                parse_shortcut(key)

    def test_settings_validation(self):
        settings = {**DEFAULTS, "hotwords": {}, "quicker_actions": {}}
        validate_settings(settings)
        settings["mic_seg_overlap"] = settings["mic_seg_duration"]
        with self.assertRaises(ValueError):
            validate_settings(settings)

    def test_saves_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = {**DEFAULTS, "record_dir": directory,
                        "save_audio": False, "save_text": False}
            audio = np.zeros(16000, dtype=np.float32)
            self.assertEqual(save_record(settings, audio, "测试", 1700000000), ())
            self.assertEqual(list(Path(directory).rglob("*")), [])
            settings["save_audio"] = True
            settings["save_text"] = True
            settings["hot_kwd"] = True
            saved = save_record(settings, audio, "重要测试", 1700000000, ("重要",))
            self.assertEqual(len(saved), 3)
            self.assertTrue(saved[0].exists())
            self.assertEqual(saved[0].suffix, ".wav")
            self.assertIn("测试", saved[2].read_text(encoding="utf-8"))
            import wave
            with wave.open(str(saved[0]), "rb") as recorder:
                self.assertEqual(recorder.getframerate(), 16000)

    def test_rebind_failure_keeps_active_shortcut(self):
        binding = KeyBinding(lambda e: None)
        with patch("key_bindings.keyboard.hook_key", return_value="old"), patch(
                "key_bindings.keyboard.unhook") as unhook:
            binding.bind("caps lock", True)
            with self.assertRaises(ValueError):
                binding.bind("ctrl+alt+v", True)
            self.assertEqual(binding.main, "caps lock")
            unhook.assert_not_called()


if __name__ == "__main__":
    unittest.main()
