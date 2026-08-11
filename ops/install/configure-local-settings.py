#!/usr/bin/env python3
"""Apply qq-dictation's local installation settings policy."""

import hashlib
import json
import os
import sys
from pathlib import Path


PROMPT_ID = "qq_extended_cleanup"
PROMPT_NAME = "Extended cleanup (Stage 0 winner)"
PROMPT_SHA256 = "78f89dd12c9ba01954ea36236331c9c140f7cfe92801a2a0482a1f7b3cf215f8"
PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src-tauri"
    / "resources"
    / "qq_cleanup_prompt.json"
)


def load_cleanup_prompt() -> dict[str, str]:
    prompt = json.loads(PROMPT_PATH.read_text(encoding="utf-8"))
    if set(prompt) != {"id", "name", "prompt"}:
        raise ValueError("cleanup prompt must contain exactly id, name, and prompt")
    if prompt["id"] != PROMPT_ID or prompt["name"] != PROMPT_NAME:
        raise ValueError("cleanup prompt metadata does not match the adopted prompt")
    text = prompt["prompt"]
    if not isinstance(text, str):
        raise ValueError("cleanup prompt text must be a string")
    if len(text) != 4995 or hashlib.sha256(text.encode()).hexdigest() != PROMPT_SHA256:
        raise ValueError("cleanup prompt text does not match the adopted prompt")
    if text.count("${output}") != 1:
        raise ValueError("cleanup prompt must contain exactly one ${output} placeholder")
    return prompt


def apply_policy(data: object, prompt: dict[str, str]) -> dict:
    if not isinstance(data, dict):
        raise ValueError("settings store must be a JSON object")
    settings = data.setdefault("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("settings must be a JSON object")

    existing_prompts = settings.setdefault("post_process_prompts", [])
    if not isinstance(existing_prompts, list):
        raise ValueError("post_process_prompts must be a JSON array")
    updated_prompts = []
    adopted_seen = False
    for item in existing_prompts:
        if isinstance(item, dict) and item.get("id") == PROMPT_ID:
            if not adopted_seen:
                updated_prompts.append(prompt)
                adopted_seen = True
        else:
            updated_prompts.append(item)
    if not adopted_seen:
        updated_prompts.append(prompt)
    settings["post_process_prompts"] = updated_prompts

    if settings.get("post_process_selected_prompt_id") is None:
        settings["post_process_selected_prompt_id"] = PROMPT_ID

    settings["overlay_style"] = "minimal"
    settings.setdefault("post_process_enabled", False)
    settings["herdr_binding_enabled"] = True
    settings["push_to_talk"] = True
    settings["auto_submit"] = True
    return data


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: configure-local-settings.py SETTINGS_PATH", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    try:
        prompt = load_cleanup_prompt()
        data = apply_policy(json.loads(path.read_text(encoding="utf-8")), prompt)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as error:
        print(f"Could not update local settings at {path}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
