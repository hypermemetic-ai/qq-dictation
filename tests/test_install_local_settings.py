"""Hermetic regression tests for the local installer settings policy."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "ops" / "install" / "configure-local-settings.py"


class LocalSettingsPolicyTests(unittest.TestCase):
    def apply_policy(self, settings):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings_store.json"
            path.write_text(
                json.dumps({"settings": settings, "unrelated": "retained"}) + "\n",
                encoding="utf-8",
            )
            subprocess.run(["/usr/bin/python3", str(SCRIPT), str(path)], check=True)
            return json.loads(path.read_text(encoding="utf-8"))

    def test_existing_unrelated_state_is_preserved(self):
        result = self.apply_policy(
            {
                "selected_model": "whisper-large-v3-turbo",
                "overlay_style": "live",
                "push_to_talk": False,
            }
        )
        settings = result["settings"]
        self.assertEqual(settings["selected_model"], "whisper-large-v3-turbo")
        self.assertEqual(settings["overlay_style"], "minimal")
        self.assertIs(settings["herdr_binding_enabled"], True)
        self.assertIs(settings["push_to_talk"], True)
        self.assertIs(settings["auto_submit"], True)
        self.assertEqual(result["unrelated"], "retained")

    def test_empty_settings_receive_local_defaults(self):
        result = self.apply_policy({})["settings"]
        self.assertEqual(result["overlay_style"], "minimal")
        self.assertIs(result["herdr_binding_enabled"], True)
        self.assertIs(result["push_to_talk"], True)
        self.assertIs(result["auto_submit"], True)


if __name__ == "__main__":
    unittest.main()
