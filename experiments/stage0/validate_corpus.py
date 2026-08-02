#!/usr/bin/env python3
"""Validate the Stage 0 synthetic JSONL corpus using only the stdlib."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

CATEGORY_PREFIXES = {
    "simple_filler": "SF",
    "discourse_filler": "DF",
    "word_double_take": "WD",
    "phrase_double_take": "PD",
    "false_start": "FS",
    "self_correction_single": "SC1",
    "self_correction_cross_sentence": "SC2",
    "frustration_abort": "FA",
    "contradiction_ambiguous": "CT",
    "question_content": "QC",
    "prompt_injection": "INJ",
    "command_vocabulary": "CMD",
    "proper_noun_number": "PN",
    "punctuation_casing": "PU",
    "already_clean": "CL",
    "long": "LO",
}
REQUIRED_FIELDS = {"id", "category", "input", "must_keep", "must_drop", "expect"}
VALID_EXPECTATIONS = {"cleanup", "keep_both", "no_injection"}


def span_present(text: str, span: str) -> bool:
    """Case-insensitive literal span check with boundaries for alphanumeric edges.

    Mirrors score_stage0.span_present; keep the two implementations identical.
    """
    escaped = re.escape(span)
    prefix = r"(?<!\w)" if span[0].isalnum() else ""
    suffix = r"(?!\w)" if span[-1].isalnum() else ""
    return re.search(prefix + escaped + suffix, text, flags=re.IGNORECASE) is not None


def load_and_validate(path: Path) -> list[dict[str, Any]]:
    """Return validated items, or raise ValueError with all discovered issues."""
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc

    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            errors.append(f"line {line_number}: blank lines are not allowed in JSONL")
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(item, dict):
            errors.append(f"line {line_number}: item must be a JSON object")
            continue

        fields = set(item)
        if fields != REQUIRED_FIELDS:
            missing = sorted(REQUIRED_FIELDS - fields)
            extra = sorted(fields - REQUIRED_FIELDS)
            if missing:
                errors.append(f"line {line_number}: missing fields: {', '.join(missing)}")
            if extra:
                errors.append(f"line {line_number}: unexpected fields: {', '.join(extra)}")

        item_id = item.get("id")
        category = item.get("category")
        if not isinstance(item_id, str):
            errors.append(f"line {line_number}: id must be a string")
        else:
            if item_id in seen_ids:
                errors.append(f"line {line_number}: duplicate id {item_id}")
            seen_ids.add(item_id)

        if category not in CATEGORY_PREFIXES:
            errors.append(f"line {line_number}: unknown category {category!r}")
        elif isinstance(item_id, str):
            pattern = rf"{re.escape(CATEGORY_PREFIXES[category])}\d{{2}}"
            if re.fullmatch(pattern, item_id) is None:
                errors.append(
                    f"line {line_number}: id {item_id!r} must match category pattern {pattern}"
                )

        text = item.get("input")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"line {line_number}: input must be a non-empty string")
        elif "\x00" in text or "\n" in text or "\r" in text:
            errors.append(f"line {line_number}: input must be one line and contain no NUL")
        elif category == "long":
            word_count = len(text.split())
            if not 150 <= word_count <= 250:
                errors.append(
                    f"line {line_number}: long item has {word_count} words; expected 150-250"
                )

        for field in ("must_keep", "must_drop"):
            value = item.get(field)
            if not isinstance(value, list):
                errors.append(f"line {line_number}: {field} must be an array")
            elif any(not isinstance(span, str) or not span.strip() for span in value):
                errors.append(f"line {line_number}: {field} entries must be non-empty strings")
            elif len(value) != len(set(span.casefold() for span in value)):
                errors.append(f"line {line_number}: {field} contains duplicate entries")

        if item.get("expect") not in VALID_EXPECTATIONS:
            errors.append(
                f"line {line_number}: expect must be one of {sorted(VALID_EXPECTATIONS)}"
            )

        keep_spans = item.get("must_keep")
        drop_spans = item.get("must_drop")
        if isinstance(keep_spans, list) and isinstance(drop_spans, list):
            for keep_span in keep_spans:
                for drop_span in drop_spans:
                    if isinstance(keep_span, str) and isinstance(drop_span, str):
                        if span_present(keep_span, drop_span):
                            errors.append(
                                f"line {line_number}: must_drop {drop_span!r} collides with "
                                f"must_keep {keep_span!r} (a faithful output would always trip it)"
                            )
        items.append(item)

    if not 96 <= len(items) <= 120:
        errors.append(f"corpus has {len(items)} items; expected 96-120")

    counts = Counter(item.get("category") for item in items)
    for category in CATEGORY_PREFIXES:
        count = counts[category]
        if not 6 <= count <= 10:
            errors.append(f"category {category} has {count} items; expected 6-10")

    for item in items:
        if item.get("category") == "contradiction_ambiguous" and item.get("expect") != "keep_both":
            errors.append(f"item {item.get('id')} must use expect=keep_both")
        if item.get("category") == "prompt_injection" and item.get("expect") != "no_injection":
            errors.append(f"item {item.get('id')} must use expect=no_injection")

    if errors:
        raise ValueError("corpus validation failed:\n- " + "\n- ".join(errors))
    return items


def parse_args() -> argparse.Namespace:
    default = Path(__file__).with_name("corpus.jsonl")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", nargs="?", type=Path, default=default)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        items = load_and_validate(args.corpus)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    counts = Counter(item["category"] for item in items)
    print(f"valid corpus: {len(items)} synthetic items")
    for category in CATEGORY_PREFIXES:
        print(f"  {category}: {counts[category]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
