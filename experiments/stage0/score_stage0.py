#!/usr/bin/env python3
"""Score a Stage 0 results directory and create review artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from validate_corpus import CATEGORY_PREFIXES, load_and_validate

HERE = Path(__file__).resolve().parent
FILLER_RE = re.compile(
    r"\b(?:uh|um|uhm|umm|uhh|uhhh|hmm|hm|mmm|mm|mh)\b", re.IGNORECASE
)
ANSWER_START_RE = re.compile(r"^\s*(?:sure\b|here is\b|the answer\b)", re.IGNORECASE)
ANSWER_CONTAINS_RE = re.compile(r"\bi cannot\b", re.IGNORECASE)
SCORE_FIELDS = [
    "id",
    "category",
    "model",
    "prompt_arm",
    "must_keep_pass_rate",
    "must_drop_violations",
    "length_ratio",
    "leftover_filler_count",
    "answer_shaped_flag",
    "ttft_ms",
    "total_ms",
    "error",
]


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {path}: {exc}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number}: result must be an object")
        records.append(record)
    if not records:
        raise ValueError(f"{path} contains no result records")
    return records


def span_present(text: str, span: str) -> bool:
    """Case-insensitive literal span check with boundaries for alphanumeric edges."""
    escaped = re.escape(span)
    prefix = r"(?<!\w)" if span[0].isalnum() else ""
    suffix = r"(?!\w)" if span[-1].isalnum() else ""
    return re.search(prefix + escaped + suffix, text, flags=re.IGNORECASE) is not None


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def answer_shaped(text: str) -> bool:
    return bool(ANSWER_START_RE.search(text) or ANSWER_CONTAINS_RE.search(text))


def percentile(values: Iterable[float], percent: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def finite_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def score_record(record: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    output = record.get("output_text")
    if not isinstance(output, str):
        output = ""
    keeps = item["must_keep"]
    keep_hits = sum(span_present(output, span) for span in keeps)
    keep_rate = keep_hits / len(keeps) if keeps else 1.0
    drop_violations = sum(span_present(output, span) for span in item["must_drop"])
    input_words = max(1, word_count(item["input"]))
    ratio = word_count(output) / input_words
    ttft = finite_number(record.get("ttft_ms"))
    total = finite_number(record.get("total_ms"))
    error = record.get("error")
    return {
        "id": item["id"],
        "category": item["category"],
        "model": str(record.get("model", "")),
        "prompt_arm": str(record.get("prompt_arm", "")),
        "must_keep_pass_rate": keep_rate,
        "must_drop_violations": drop_violations,
        "length_ratio": ratio,
        "leftover_filler_count": len(FILLER_RE.findall(output)),
        "answer_shaped_flag": int(answer_shaped(output)),
        "ttft_ms": ttft,
        "total_ms": total,
        "error": "" if error is None else str(error),
        "_keep_hits": keep_hits,
        "_keep_total": len(keeps),
        "_input_tokens": int(record.get("input_tokens") or 0),
        "_output_tokens": int(record.get("output_tokens") or 0),
        "_output_text": output,
        "_input_text": item["input"],
    }


def write_scores(path: Path, scores: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=SCORE_FIELDS)
        writer.writeheader()
        for score in scores:
            row = {field: score[field] for field in SCORE_FIELDS}
            row["must_keep_pass_rate"] = f"{score['must_keep_pass_rate']:.4f}"
            row["length_ratio"] = f"{score['length_ratio']:.4f}"
            row["ttft_ms"] = "" if score["ttft_ms"] is None else f"{score['ttft_ms']:.3f}"
            row["total_ms"] = "" if score["total_ms"] is None else f"{score['total_ms']:.3f}"
            writer.writerow(row)


def price_for(model: str, providers: dict[str, Any]) -> tuple[float, float]:
    for provider in providers.values():
        if isinstance(provider, dict) and model in provider.get("models", {}):
            config = provider["models"][model]
            return (
                float(config["input_price_per_mtok"]),
                float(config["output_price_per_mtok"]),
            )
    raise ValueError(f"no pricing found for model {model!r}")


def aggregate(scores: list[dict[str, Any]], providers: dict[str, Any]) -> dict[str, Any]:
    keep_hits = sum(score["_keep_hits"] for score in scores)
    keep_total = sum(score["_keep_total"] for score in scores)
    input_price, output_price = price_for(scores[0]["model"], providers)
    total_cost = sum(
        (score["_input_tokens"] * input_price + score["_output_tokens"] * output_price)
        / 1_000_000
        for score in scores
    )
    per_dictation = total_cost / len(scores)
    ttft_values = [score["ttft_ms"] for score in scores if score["ttft_ms"] is not None]
    total_values = [score["total_ms"] for score in scores if score["total_ms"] is not None]
    return {
        "critical_pct": 100 * keep_hits / keep_total if keep_total else 100.0,
        "drop_violations": sum(score["must_drop_violations"] for score in scores),
        "mean_length_ratio": statistics.fmean(score["length_ratio"] for score in scores),
        "fillers": sum(score["leftover_filler_count"] for score in scores),
        "answers": sum(score["answer_shaped_flag"] for score in scores),
        "ttft_p50": percentile(ttft_values, 0.50),
        "ttft_p95": percentile(ttft_values, 0.95),
        "total_p50": percentile(total_values, 0.50),
        "total_p95": percentile(total_values, 0.95),
        "per_dictation": per_dictation,
        "per_month": per_dictation * 200 * 30,
        "errors": sum(bool(score["error"]) for score in scores),
    }


def fmt_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def group_scores(scores: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for score in scores:
        groups[tuple(str(score[key]) for key in keys)].append(score)
    return dict(groups)


def write_summary(path: Path, scores: list[dict[str, Any]], providers: dict[str, Any], mode: str) -> None:
    pairs = group_scores(scores, ("model", "prompt_arm"))
    aggregates = {pair: aggregate(group, providers) for pair, group in pairs.items()}
    ranked = sorted(
        pairs,
        key=lambda pair: (
            aggregates[pair]["drop_violations"],
            aggregates[pair]["answers"],
            -aggregates[pair]["critical_pct"],
            aggregates[pair]["fillers"],
            aggregates[pair]["errors"],
            aggregates[pair]["total_p50"] if aggregates[pair]["total_p50"] is not None else math.inf,
            aggregates[pair]["per_dictation"],
        ),
    )

    lines = [
        "# Stage 0 results summary",
        "",
        f"Run mode: **{mode}**. Costs use recorded token counts and committed Cerebras list prices.",
        "Monthly projection assumes 200 dictations/day for 30 days.",
        "",
        "## Ranked results",
        "",
        "Ranking is quality-first: must-drop violations, answer-shaped outputs, critical-span preservation, leftover fillers, errors, total p50, then cost.",
        "",
        "| Rank | Model | Prompt arm | Critical spans | Must-drop violations | Mean length ratio | Leftover fillers | Answer-shaped | Errors | TTFT p50 / p95 ms | Total p50 / p95 ms | Cost / dictation | Cost / month |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, pair in enumerate(ranked, 1):
        model, arm = pair
        data = aggregates[pair]
        lines.append(
            f"| {rank} | {model} | {arm} | {data['critical_pct']:.2f}% | "
            f"{data['drop_violations']} | {data['mean_length_ratio']:.3f} | {data['fillers']} | "
            f"{data['answers']} | {data['errors']} | {fmt_ms(data['ttft_p50'])} / {fmt_ms(data['ttft_p95'])} | "
            f"{fmt_ms(data['total_p50'])} / {fmt_ms(data['total_p95'])} | "
            f"${data['per_dictation']:.6f} | ${data['per_month']:.2f} |"
        )

    lines.extend(
        [
            "",
            "## Per-category breakdown",
            "",
            "| Model | Prompt arm | Category | Critical spans | Must-drop violations | Mean length ratio | Leftover fillers | Answer-shaped |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    category_groups = group_scores(scores, ("model", "prompt_arm", "category"))
    for (model, arm, category), group in sorted(category_groups.items()):
        keep_hits = sum(score["_keep_hits"] for score in group)
        keep_total = sum(score["_keep_total"] for score in group)
        critical_pct = 100 * keep_hits / keep_total if keep_total else 100.0
        lines.append(
            f"| {model} | {arm} | {category} | {critical_pct:.2f}% | "
            f"{sum(score['must_drop_violations'] for score in group)} | "
            f"{statistics.fmean(score['length_ratio'] for score in group):.3f} | "
            f"{sum(score['leftover_filler_count'] for score in group)} | "
            f"{sum(score['answer_shaped_flag'] for score in group)} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation notes",
            "",
            "- Critical-span matching and must-drop checks are case-insensitive literal checks with word boundaries at alphanumeric edges.",
            "- `keep_both` contradiction items pass only when all of their `must_keep` spans survive; `no_injection` items pass only when none of their `must_drop` targets appear.",
            "- Length ratio is whitespace-delimited output words divided by input words.",
            "- Percentiles use linear interpolation over successful numeric measurements.",
            "- Mock timings and mock quality are plumbing checks, not candidate evidence.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_human_sheet(path: Path, scores: list[dict[str, Any]]) -> None:
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for score in scores:
        by_key[(score["model"], score["prompt_arm"], score["category"], score["id"])] = score

    models = sorted({score["model"] for score in scores})
    arms = sorted({score["prompt_arm"] for score in scores})
    categories = list(CATEGORY_PREFIXES)
    fields = ["model", "prompt_arm", "category", "item_id", "input", "output", "rating"]
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fields)
        writer.writeheader()
        for model in models:
            for arm in arms:
                for category in categories:
                    candidates = sorted(
                        (
                            score
                            for key, score in by_key.items()
                            if key[0] == model and key[1] == arm and key[2] == category
                        ),
                        key=lambda score: score["id"],
                    )[:5]
                    for score in candidates:
                        writer.writerow(
                            {
                                "model": model,
                                "prompt_arm": arm,
                                "category": category,
                                "item_id": score["id"],
                                "input": score["_input_text"],
                                "output": score["_output_text"],
                                "rating": "",
                            }
                        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = read_json(args.results_dir / "run_config.json")
        records = read_jsonl(args.results_dir / "results.jsonl")
        providers = read_json(HERE / "providers.json")
        corpus_path = Path(config.get("corpus", HERE / "corpus.jsonl"))
        if not corpus_path.exists():
            corpus_path = HERE / "corpus.jsonl"
        corpus = load_and_validate(corpus_path)
    except (ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    items = {item["id"]: item for item in corpus}
    scores: list[dict[str, Any]] = []
    for index, record in enumerate(records, 1):
        item_id = record.get("id")
        if item_id not in items:
            print(f"error: result record {index} has unknown id {item_id!r}", file=sys.stderr)
            return 2
        scores.append(score_record(record, items[item_id]))

    try:
        write_scores(args.results_dir / "scores.csv", scores)
        write_summary(
            args.results_dir / "summary.md",
            scores,
            providers,
            str(config.get("mode", "unknown")),
        )
        write_human_sheet(args.results_dir / "human_rating_sheet.csv", scores)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: cannot write score artifacts: {exc}", file=sys.stderr)
        return 2

    print(f"scored {len(scores)} records in {args.results_dir}")
    print("wrote scores.csv, summary.md, and human_rating_sheet.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
