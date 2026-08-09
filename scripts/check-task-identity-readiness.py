#!/usr/bin/env python3
"""Check one configured identity prefix and the classified source inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


INVENTORY_PATH = "docs/task-identity-cutover-readiness.md"
MARKER_PREFIX = "<!-- identity-readiness: "
MARKER_SUFFIX = " -->"
CATEGORIES = {
    "current authority",
    "current reference",
    "ordinary product prose",
    "explicit historical evidence",
}
PREFIX_LINE = re.compile(r"^task_prefix[ \t]*:(.*)$", re.ASCII)
PREFIX_SCALAR = re.compile(
    r"(?P<scalar>[A-Za-z]+|\"[A-Za-z]+\"|'[A-Za-z]+')"
    r"(?:[ \t]+#.*)?",
    re.ASCII,
)
IDENTITY_WORD = "TA" + "SK"
AUTHORITY_WORD = "Chan" + "ge"
OCCURRENCE = re.compile(
    rf"\b(?:{re.escape(IDENTITY_WORD)}|{re.escape(AUTHORITY_WORD)})\b"
)


class ReadinessError(RuntimeError):
    """A readiness input or invariant was invalid."""


def parse_prefix(config: Path) -> str:
    try:
        text = config.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReadinessError(f"cannot read configuration {config}: {error}") from error

    values: list[str] = []
    for line in text.splitlines():
        match = PREFIX_LINE.fullmatch(line)
        if match is None:
            continue
        value_text = match.group(1).strip()
        scalar_match = PREFIX_SCALAR.fullmatch(value_text)
        if scalar_match is None:
            raise ReadinessError(
                "top-level task_prefix must be one plain or quoted "
                "ASCII-letters-only scalar"
            )
        scalar = scalar_match.group("scalar")
        if scalar[0] in "\"'":
            scalar = scalar[1:-1]
        values.append(scalar)

    if len(values) != 1:
        raise ReadinessError(
            "configuration must contain exactly one unambiguous top-level "
            f"task_prefix; found {len(values)}"
        )
    return values[0]


def validate_inventory_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ReadinessError("inventory occurrence path must be a nonempty string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise ReadinessError(f"inventory occurrence has unsafe path: {value!r}")
    if path.parts[0] == "backlog":
        raise ReadinessError("inventory must not classify the mounted backlog store")
    return value


def parse_inventory(repository: Path) -> dict[tuple[str, int, str], str]:
    receipt = repository / INVENTORY_PATH
    if receipt.is_symlink() or not receipt.is_file():
        raise ReadinessError(f"checked-in inventory is missing: {INVENTORY_PATH}")
    try:
        lines = receipt.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReadinessError(f"cannot read {INVENTORY_PATH}: {error}") from error

    inventory: dict[tuple[str, int, str], str] = {}
    for receipt_line, line in enumerate(lines, 1):
        stripped = line.strip()
        if MARKER_PREFIX not in stripped:
            continue
        if not stripped.startswith(MARKER_PREFIX) or not stripped.endswith(MARKER_SUFFIX):
            raise ReadinessError(
                f"malformed inventory marker at {INVENTORY_PATH}:{receipt_line}"
            )
        encoded = stripped[len(MARKER_PREFIX) : -len(MARKER_SUFFIX)]
        try:
            entry = json.loads(encoded)
        except json.JSONDecodeError as error:
            raise ReadinessError(
                f"invalid inventory JSON at {INVENTORY_PATH}:{receipt_line}: {error}"
            ) from error
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "line",
            "digest",
            "category",
        }:
            raise ReadinessError(
                f"inventory marker at {INVENTORY_PATH}:{receipt_line} has wrong fields"
            )
        path = validate_inventory_path(entry["path"])
        source_line = entry["line"]
        digest = entry["digest"]
        category = entry["category"]
        if not isinstance(source_line, int) or isinstance(source_line, bool) or source_line < 1:
            raise ReadinessError(
                f"inventory marker at {INVENTORY_PATH}:{receipt_line} has invalid line"
            )
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ReadinessError(
                f"inventory marker at {INVENTORY_PATH}:{receipt_line} has invalid digest"
            )
        if category not in CATEGORIES:
            raise ReadinessError(
                f"inventory marker at {INVENTORY_PATH}:{receipt_line} has invalid category"
            )
        key = (path, source_line, digest)
        if key in inventory:
            raise ReadinessError(
                f"duplicate inventory occurrence for {path}:{source_line}"
            )
        inventory[key] = category
    return inventory


def candidate_paths(repository: Path) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ReadinessError(f"cannot enumerate repository candidate source: {detail}")
    try:
        paths = [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    except UnicodeDecodeError as error:
        raise ReadinessError("repository contains a non-UTF-8 candidate path") from error
    return sorted(set(paths))


def line_digest(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def scan_occurrences(repository: Path) -> tuple[set[tuple[str, int, str]], int]:
    occurrences: set[tuple[str, int, str]] = set()
    scanned_files = 0
    for relative in candidate_paths(repository):
        path = PurePosixPath(relative)
        if path.parts and path.parts[0] == "backlog":
            continue
        candidate = repository / relative
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            data = candidate.read_bytes()
        except OSError as error:
            raise ReadinessError(f"cannot read candidate source {relative}: {error}") from error
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        scanned_files += 1
        for number, line in enumerate(text.splitlines(), 1):
            if OCCURRENCE.search(line):
                occurrences.add((relative, number, line_digest(line)))
    return occurrences, scanned_files


def describe_occurrence(key: tuple[str, int, str]) -> dict[str, object]:
    path, line, digest = key
    return {"path": path, "line": line, "digest": digest}


def check(repository: Path, config: Path) -> dict[str, object]:
    repository = repository.resolve()
    if not repository.is_dir():
        raise ReadinessError(f"repository is not a directory: {repository}")
    display_prefix = parse_prefix(config.resolve()).upper()
    inventory = parse_inventory(repository)
    occurrences, scanned_files = scan_occurrences(repository)
    classified = set(inventory)
    unclassified = sorted(occurrences - classified)
    stale = sorted(classified - occurrences)
    if unclassified or stale:
        details = {
            "unclassified_occurrences": [
                describe_occurrence(item) for item in unclassified
            ],
            "stale_classifications": [describe_occurrence(item) for item in stale],
        }
        raise ReadinessError(
            "checked-in classification does not match candidate source: "
            + json.dumps(details, sort_keys=True, separators=(",", ":"))
        )
    return {
        "classified_occurrences": len(occurrences),
        "display_prefix": display_prefix,
        "scanned_files": scanned_files,
        "unclassified_occurrences": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        payload = check(arguments.repository, arguments.config)
    except ReadinessError as error:
        print(f"task-identity-readiness: {error}", file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
