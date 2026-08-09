"""Apply qq-dictation's local installation settings policy."""

import json
import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: configure-local-settings.py SETTINGS_PATH", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        settings = data.setdefault("settings", {})
        settings["overlay_style"] = "minimal"
        settings.setdefault("post_process_enabled", False)
        settings["herdr_binding_enabled"] = True
        settings["push_to_talk"] = True
        settings["auto_submit"] = True
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except (json.JSONDecodeError, OSError) as error:
        print(f"Could not update local settings at {path}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
