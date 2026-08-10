#!/usr/bin/env python3
"""Small shared helpers for the private TASK-30 tooling."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class Task30Error(Exception):
    """An input or evidence error safe to report without private transcript text."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def require_absolute(path_value: str, label: str) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise Task30Error(f"{label} must be a non-empty absolute path string")
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise Task30Error(f"{label} must be an absolute path")
    return path


def require_plain_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise Task30Error(f"{label} does not exist: {path}") from error
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise Task30Error(f"{label} must be a real directory, not a symlink: {path}")


def require_plain_file(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise Task30Error(f"{label} does not exist: {path}") from error
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise Task30Error(f"{label} must be a regular file, not a symlink: {path}")
    return metadata


def ensure_private_directory(path: Path) -> None:
    """Create one directory tree and make the requested directory mode 0700."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    require_plain_directory(path, "private directory")
    path.chmod(0o700)


def private_write_bytes(path: Path, payload: bytes, *, exclusive: bool = True) -> None:
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def private_write_json(path: Path, value: Any, *, exclusive: bool = True) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    private_write_bytes(path, payload, exclusive=exclusive)


def private_append_json_line(descriptor: int, value: Any) -> None:
    payload = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]
    os.fsync(descriptor)


def parse_sample_id(value: Any, label: str = "sample id") -> str:
    if isinstance(value, bool):
        raise Task30Error(f"{label} must be a positive history id")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        try:
            number = int(value)
        except ValueError as error:
            raise Task30Error(f"{label} must be a positive history id") from error
    else:
        raise Task30Error(f"{label} must be a positive history id")
    if number <= 0 or str(number) != str(value):
        raise Task30Error(f"{label} must use its canonical positive integer form")
    return str(number)


def load_seed_ids(corpus: Path, *, required: bool = False) -> set[str]:
    """Read, but never rewrite, the owner-maintained existing-saved cohort."""
    manifest = corpus / "seed-existing.json"
    if not manifest.exists() and not manifest.is_symlink():
        if required:
            raise Task30Error(f"existing-saved cohort manifest is missing: {manifest}")
        return set()
    require_plain_file(manifest, "seed-existing.json")
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise Task30Error(f"cannot parse seed-existing.json: {error}") from error

    entries: Any
    if isinstance(value, list):
        entries = value
    elif isinstance(value, dict):
        present = [key for key in ("history_ids", "sample_ids", "ids") if key in value]
        if len(present) != 1:
            raise Task30Error(
                "seed-existing.json must contain exactly one of history_ids, sample_ids, or ids"
            )
        entries = value[present[0]]
    else:
        raise Task30Error("seed-existing.json must be an object or array")
    if not isinstance(entries, list):
        raise Task30Error("seed-existing.json id collection must be an array")

    ids = {parse_sample_id(item, "seed-existing id") for item in entries}
    if len(ids) != len(entries):
        raise Task30Error("seed-existing.json contains duplicate ids")
    return ids


def list_corpus_sample_ids(corpus: Path) -> list[str]:
    samples = corpus / "samples"
    require_plain_directory(samples, "corpus samples directory")
    ids: list[str] = []
    for entry in samples.iterdir():
        if entry.name.startswith("."):
            continue
        sample_id = parse_sample_id(entry.name, "sample directory name")
        require_plain_directory(entry, f"sample {sample_id}")
        ids.append(sample_id)
    return sorted(ids, key=int)


def selected_sample_ids(corpus: Path, cohort: str) -> list[str]:
    all_ids = list_corpus_sample_ids(corpus)
    if cohort == "full":
        return all_ids
    if cohort == "existing-saved":
        seed_ids = load_seed_ids(corpus, required=True)
        missing = sorted(seed_ids.difference(all_ids), key=int)
        if missing:
            raise Task30Error(
                "existing-saved cohort refers to missing sample ids: " + ", ".join(missing)
            )
        return [sample_id for sample_id in all_ids if sample_id in seed_ids]
    raise Task30Error("cohort must be 'existing-saved' or 'full'")


def validate_sample_files(corpus: Path, sample_ids: Iterable[str]) -> None:
    for sample_id in sample_ids:
        sample = corpus / "samples" / sample_id
        require_plain_directory(sample, f"sample {sample_id}")
        require_plain_file(sample / "audio.wav", f"sample {sample_id} audio")
        require_plain_file(sample / "metadata.json", f"sample {sample_id} metadata")
