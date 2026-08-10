"""Hermetic regression tests for the local installer settings policy."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "ops" / "install" / "configure-local-settings.py"
PROMPT_PATH = ROOT / "src-tauri" / "resources" / "qq_cleanup_prompt.json"
PROMPT_ID = "qq_extended_cleanup"
PROMPT_NAME = "Extended cleanup (Stage 0 winner)"
PROMPT_SHA256 = "ce2006c55f88f2e63de35c578e5e42608ec4b1dc7f47f97b7a1f9e2fda44a054"


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

    def adopted_prompt(self):
        return json.loads(PROMPT_PATH.read_text(encoding="utf-8"))

    def test_canonical_prompt_has_exact_adopted_contract(self):
        prompt = self.adopted_prompt()
        self.assertEqual(set(prompt), {"id", "name", "prompt"})
        self.assertEqual(prompt["id"], PROMPT_ID)
        self.assertEqual(prompt["name"], PROMPT_NAME)
        self.assertEqual(len(prompt["prompt"]), 4621)
        self.assertEqual(
            hashlib.sha256(prompt["prompt"].encode()).hexdigest(), PROMPT_SHA256
        )
        self.assertEqual(prompt["prompt"].count("${output}"), 1)

    def test_existing_adopted_selection_is_updated_from_product_source(self):
        other = {"id": "operator_prompt", "name": "Operator", "prompt": "keep me"}
        result = self.apply_policy(
            {
                "post_process_prompts": [
                    other,
                    {"id": PROMPT_ID, "name": "stale", "prompt": "stale"},
                ],
                "post_process_selected_prompt_id": PROMPT_ID,
                "post_process_enabled": True,
                "post_process_provider_id": "cerebras",
                "post_process_models": {"cerebras": "gpt-oss-120b"},
                "post_process_api_keys": {"cerebras": "synthetic-secret"},
            }
        )
        settings = result["settings"]
        self.assertEqual(settings["post_process_prompts"], [other, self.adopted_prompt()])
        self.assertEqual(settings["post_process_selected_prompt_id"], PROMPT_ID)
        self.assertIs(settings["post_process_enabled"], True)
        self.assertEqual(settings["post_process_provider_id"], "cerebras")
        self.assertEqual(settings["post_process_models"], {"cerebras": "gpt-oss-120b"})
        self.assertEqual(
            settings["post_process_api_keys"], {"cerebras": "synthetic-secret"}
        )
        self.assertEqual(result["unrelated"], "retained")

    def test_different_existing_selection_and_unrelated_prompts_are_preserved(self):
        other = {"id": "operator_prompt", "name": "Operator", "prompt": "keep me"}
        result = self.apply_policy(
            {
                "post_process_prompts": [other],
                "post_process_selected_prompt_id": "operator_prompt",
                "post_process_enabled": False,
            }
        )["settings"]
        self.assertEqual(result["post_process_prompts"], [other, self.adopted_prompt()])
        self.assertEqual(result["post_process_selected_prompt_id"], "operator_prompt")
        self.assertIs(result["post_process_enabled"], False)

    def test_missing_selection_uses_adopted_prompt_and_defaults_disabled(self):
        result = self.apply_policy({"provider": "cerebras"})["settings"]
        self.assertEqual(result["post_process_prompts"], [self.adopted_prompt()])
        self.assertEqual(result["post_process_selected_prompt_id"], PROMPT_ID)
        self.assertIs(result["post_process_enabled"], False)
        self.assertEqual(result["provider"], "cerebras")

    def test_duplicate_adopted_entries_collapse_to_one_canonical_entry(self):
        result = self.apply_policy(
            {
                "post_process_prompts": [
                    {"id": PROMPT_ID, "name": "first", "prompt": "first"},
                    {"id": PROMPT_ID, "name": "second", "prompt": "second"},
                ],
                "post_process_selected_prompt_id": PROMPT_ID,
            }
        )["settings"]
        self.assertEqual(result["post_process_prompts"], [self.adopted_prompt()])


if __name__ == "__main__":
    unittest.main()
