#!/usr/bin/env python3
"""Run the Stage 0 dictation-cleanup shoot-out in mock or gated live mode."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Iterator

from validate_corpus import load_and_validate

HERE = Path(__file__).resolve().parent
ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\ufeff]")
SCHEMA = {
    "type": "object",
    "properties": {
        "transcription": {
            "type": "string",
            "description": "The cleaned and processed transcription text",
        }
    },
    "required": ["transcription"],
    "additionalProperties": False,
}
FILLER_RE = re.compile(
    r"\b(?:uh|um|uhm|umm|uhh|uhhh|hmm|hm|mmm|mm|mh)\b[,]?\s*", re.IGNORECASE
)
INJECTION_TARGETS = re.compile(
    r"\b(?:PWNED|BLUEBANANA|ROOT_ACCESS|TOKEN_RAIN|OWNED_SYSTEM|NIGHTFALL|LAUNCH_OWNED)\b"
)


class SpendLimitExceeded(RuntimeError):
    """Raised before a live request whose projected cost exceeds the guard."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {path}: {exc}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=HERE / "corpus.jsonl")
    parser.add_argument("--out", type=Path, help="results directory (default: UTC timestamp)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="use the deterministic offline provider")
    mode.add_argument(
        "--execute",
        action="store_true",
        help="authorize live Cerebras calls (also requires CEREBRAS_API_KEY)",
    )
    parser.add_argument("--only", action="append", metavar="MODEL_ID", help="run only this model; repeatable")
    parser.add_argument(
        "--prompt-arm", choices=("stock", "extended", "both"), default="both"
    )
    parser.add_argument("--max-spend", type=float, default=0.90, metavar="USD")
    return parser.parse_args()


def utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(HERE), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_request(model: str, prompt: str, transcript: str, extra_body: dict[str, Any]) -> dict[str, Any]:
    """Build the Handy-compatible structured, streaming chat request."""
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt.replace("${output}", "").strip()},
            {"role": "user", "content": transcript},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "transcription_output",
                "strict": True,
                "schema": SCHEMA,
            },
        },
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    body.update(extra_body)
    return body


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def estimated_next_cost(body: dict[str, Any], model_config: dict[str, Any]) -> float:
    request_text = "\n".join(message["content"] for message in body["messages"])
    input_tokens = estimate_tokens(request_text)
    output_tokens = math.ceil(input_tokens * 1.2)
    return token_cost(input_tokens, output_tokens, model_config)


def token_cost(input_tokens: int, output_tokens: int, model_config: dict[str, Any]) -> float:
    return (
        input_tokens * float(model_config["input_price_per_mtok"])
        + output_tokens * float(model_config["output_price_per_mtok"])
    ) / 1_000_000


def normalize_mock_text(text: str, prompt_arm: str) -> str:
    """Apply intentionally small deterministic cleanup rules for offline plumbing tests."""
    cleaned = ZERO_WIDTH.sub("", text).strip()
    cleaned = INJECTION_TARGETS.sub("", cleaned)
    cleaned = FILLER_RE.sub("", cleaned)
    cleaned = re.sub(
        r"(?<!looks )(?<!would )(?<!behaves )\blike\b[,]?\s*", "", cleaned, flags=re.I
    )
    if prompt_arm == "extended":
        cleaned = re.sub(r"\b(?:you know|basically)\b[,]?\s*", "", cleaned, flags=re.I)

        # Collapse a phrase immediately restarted after an em dash.
        phrase_restart = re.compile(r"(^|[.!?]\s+)([^.!?—]{2,}?)—\s*\2", re.I)
        cleaned = phrase_restart.sub(lambda match: match.group(1) + match.group(2), cleaned)

        # Collapse accidental word stutters while retaining corpus emphasis traps.
        emphasis = {"very", "now", "slow"}
        doubled = re.compile(r"\b([A-Za-z]+)\s+\1\b", re.I)
        cleaned = doubled.sub(
            lambda match: match.group(0)
            if match.group(1).casefold() in emphasis
            else match.group(1),
            cleaned,
        )

        # Remove explicit marker words. This fake deliberately does not try to
        # infer every superseded span; live candidates are what the corpus scores.
        cleaned = re.sub(
            r"\b(?:actually|no wait|I mean|scratch that|forget it|never mind|sorry|correction|fuck it)\b[,]?\s*",
            "",
            cleaned,
            flags=re.I,
        )

    spoken = [
        (r"\bquestion mark\b", "?"),
        (r"\bsemicolon\b", ";"),
        (r"\bcolon\b", ":"),
        (r"\bcomma\b", ","),
        (r"\bperiod\b", "."),
        (r"\btwenty-five\b", "25"),
        (r"\bten percent\b", "10%"),
        (r"\bfive percent\b", "5%"),
        (r"\bfive dollars\b", "$5"),
        (r"\btwenty requests\b", "20 requests"),
    ]
    for pattern, replacement in spoken:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.I)

    cleaned = re.sub(r"\s+([,.;:?])", r"\1", cleaned)
    cleaned = re.sub(r"([,;:?])(?=\S)", r"\1 ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,")
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def mock_chunks(body: dict[str, Any], prompt_arm: str) -> Iterator[dict[str, Any]]:
    transcript = body["messages"][1]["content"]
    output = normalize_mock_text(transcript, prompt_arm)
    structured = json.dumps({"transcription": output}, ensure_ascii=False, separators=(",", ":"))
    split_at = max(1, len(structured) // 2)
    yield {"choices": [{"delta": {"content": structured[:split_at]}}]}
    yield {"choices": [{"delta": {"content": structured[split_at:]}}]}
    request_text = "\n".join(message["content"] for message in body["messages"])
    yield {
        "choices": [],
        "usage": {
            "prompt_tokens": estimate_tokens(request_text),
            "completion_tokens": estimate_tokens(structured),
        },
    }


def live_chunks(base_url: str, api_key: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON in provider stream: {exc.msg}") from exc
                if isinstance(chunk, dict) and chunk.get("error"):
                    raise RuntimeError("provider returned an error event")
                if isinstance(chunk, dict):
                    yield chunk
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"provider HTTP error {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"provider connection error: {exc.reason}") from exc


def content_from_chunk(chunk: dict[str, Any]) -> str:
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


def consume_stream(chunks: Iterable[dict[str, Any]], started: float) -> tuple[str, float | None, float, int, int]:
    pieces: list[str] = []
    ttft_ms: float | None = None
    input_tokens = 0
    output_tokens = 0
    for chunk in chunks:
        content = content_from_chunk(chunk)
        if content:
            if ttft_ms is None:
                ttft_ms = (time.perf_counter() - started) * 1000
            pieces.append(content)
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            output_tokens = int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )
    total_ms = (time.perf_counter() - started) * 1000
    raw = "".join(pieces)
    if not raw:
        raise ValueError("provider stream contained no content")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"structured response was invalid JSON: {exc.msg}") from exc
    transcription = parsed.get("transcription") if isinstance(parsed, dict) else None
    if not isinstance(transcription, str):
        raise ValueError("structured response omitted string field 'transcription'")
    return ZERO_WIDTH.sub("", transcription), ttft_ms, total_ms, input_tokens, output_tokens


def validate_provider_config(providers: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(providers, dict) or set(providers) != {"cerebras"}:
        raise ValueError("providers.json must define exactly the cerebras provider")
    provider = providers["cerebras"]
    if not isinstance(provider, dict) or not isinstance(provider.get("models"), dict):
        raise ValueError("cerebras provider is missing models")
    return "cerebras", provider


def write_config(
    out_dir: Path,
    args: argparse.Namespace,
    corpus: Path,
    models: list[str],
    arms: list[str],
    provider_name: str,
    started_at: str,
) -> None:
    config = {
        "provider": provider_name,
        "models": models,
        "arms": arms,
        "mode": "mock" if args.mock else "live",
        "timestamp": started_at,
        "git_commit": git_commit(),
        "corpus": str(corpus.resolve()),
        "max_spend_usd": args.max_spend,
    }
    (out_dir / "run_config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> int:
    args = parse_args()
    if not math.isfinite(args.max_spend) or args.max_spend < 0:
        print("error: --max-spend must be a finite non-negative value", file=sys.stderr)
        return 2

    try:
        corpus = load_and_validate(args.corpus)
        prompts = read_json(HERE / "prompts.json")
        provider_name, provider = validate_provider_config(read_json(HERE / "providers.json"))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    available_models = list(provider["models"])
    models = args.only if args.only else available_models
    unknown = sorted(set(models) - set(available_models))
    if unknown:
        print(f"error: unknown model(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    models = list(dict.fromkeys(models))
    arms = ["stock", "extended"] if args.prompt_arm == "both" else [args.prompt_arm]
    if not isinstance(prompts, dict) or any(not isinstance(prompts.get(arm), str) for arm in arms):
        print("error: prompts.json does not define the requested prompt arms", file=sys.stderr)
        return 2

    api_key = ""
    if args.execute:
        api_key = os.environ.get(provider.get("api_key_env", "CEREBRAS_API_KEY"), "")
        if not api_key:
            print("error: --execute requires CEREBRAS_API_KEY", file=sys.stderr)
            return 2

    started_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    out_dir = args.out or HERE / "results" / utc_timestamp()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_config(out_dir, args, args.corpus, models, arms, provider_name, started_at)
    except OSError as exc:
        print(f"error: cannot initialize output directory {out_dir}: {exc}", file=sys.stderr)
        return 2

    plan = [(item, model, arm) for item in corpus for model in models for arm in arms]
    actual_live_cost = 0.0
    result_path = out_dir / "results.jsonl"
    try:
        with result_path.open("w", encoding="utf-8") as results_file:
            for call_number, (item, model, arm) in enumerate(plan):
                model_config = provider["models"][model]
                body = build_request(model, prompts[arm], item["input"], model_config.get("extra_body", {}))
                if args.execute:
                    next_estimate = estimated_next_cost(body, model_config)
                    projected = actual_live_cost + next_estimate
                    if projected > args.max_spend:
                        raise SpendLimitExceeded(
                            f"spend guard stopped before call {call_number + 1}: "
                            f"projected ${projected:.6f} exceeds --max-spend ${args.max_spend:.6f}"
                        )

                record: dict[str, Any] = {
                    "id": item["id"],
                    "category": item["category"],
                    "model": model,
                    "prompt_arm": arm,
                    "ttft_ms": None,
                    "total_ms": None,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "output_text": "",
                    "error": None,
                }
                started = time.perf_counter()
                try:
                    chunks = (
                        mock_chunks(body, arm)
                        if args.mock
                        else live_chunks(provider["base_url"], api_key, body)
                    )
                    output, ttft, total, input_tokens, output_tokens = consume_stream(chunks, started)
                    record.update(
                        {
                            "ttft_ms": round(ttft, 3) if ttft is not None else None,
                            "total_ms": round(total, 3),
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "output_text": output,
                        }
                    )
                    if args.execute:
                        actual_live_cost += token_cost(input_tokens, output_tokens, model_config)
                except Exception as exc:  # Per-call provider/parse failures are data, not run aborts.
                    record["total_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    record["error"] = f"{type(exc).__name__}: {exc}"

                results_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                results_file.flush()
                if args.execute and call_number + 1 < len(plan):
                    time.sleep(0.1)
    except SpendLimitExceeded as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"error: cannot write results: {exc}", file=sys.stderr)
        return 2

    print(f"wrote {len(plan)} records to {result_path}")
    if args.execute:
        print(f"recorded live cost: ${actual_live_cost:.6f}")
    else:
        print("mock mode: no network calls and no spend")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
