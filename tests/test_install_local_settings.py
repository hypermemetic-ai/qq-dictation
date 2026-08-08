"""Hermetic regression tests for the local installer settings policy."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "configure-local-settings.py"


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

    def test_existing_true_stays_true(self):
        result = self.apply_policy(
            {"post_process_enabled": True, "provider": "cerebras"}
        )
        self.assertIs(result["settings"]["post_process_enabled"], True)
        self.assertEqual(result["settings"]["provider"], "cerebras")

    def test_existing_false_stays_false(self):
        result = self.apply_policy({"post_process_enabled": False})
        self.assertIs(result["settings"]["post_process_enabled"], False)

    def test_missing_setting_defaults_false(self):
        result = self.apply_policy({"provider": "cerebras"})
        self.assertIs(result["settings"]["post_process_enabled"], False)
        self.assertEqual(result["unrelated"], "retained")


if __name__ == "__main__":
    unittest.main()
