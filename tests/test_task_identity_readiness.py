"""Focused tests for configuration-derived identity readiness."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
CHECKER = ROOT / "scripts" / "check-task-identity-readiness.py"


def run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_checker(repository: Path, config: Path) -> subprocess.CompletedProcess[str]:
    return run(
        [
            sys.executable,
            str(CHECKER),
            "--repository",
            str(repository),
            "--config",
            str(config),
        ],
        cwd=repository,
    )


def digest(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def marker(path: str, line: str) -> str:
    payload = {
        "category": "explicit historical evidence",
        "digest": digest(line),
        "line": 1,
        "path": path,
    }
    return (
        "<!-- identity-readiness: "
        + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        + " -->"
    )


def initialize_mixed_candidate_repository(repository: Path) -> Path:
    repository.mkdir()
    initialized = run(["git", "init", "--quiet"], cwd=repository)
    if initialized.returncode:
        raise AssertionError(initialized.stderr)
    for key, value in (("user.name", "Readiness Test"), ("user.email", "test@example.invalid")):
        configured = run(["git", "config", key, value], cwd=repository)
        if configured.returncode:
            raise AssertionError(configured.stderr)

    identity_word = "TA" + "SK"
    authority_word = "Chan" + "ge"
    sources = {
        "committed.md": f"Completed {identity_word}-7 record.\n",
        "staged.md": f"The retired {authority_word} worktree is historical.\n",
        "untracked.md": f"Completed {identity_word}-9 record.\n",
    }
    for path, content in sources.items():
        (repository / path).write_text(content, encoding="utf-8")

    docs = repository / "docs"
    docs.mkdir()
    receipt = docs / "task-identity-cutover-readiness.md"
    receipt.write_text(
        "# Fixture inventory\n\n"
        + "\n".join(marker(path, content.rstrip("\n")) for path, content in sources.items())
        + "\n",
        encoding="utf-8",
    )
    added = run(
        ["git", "add", "docs/task-identity-cutover-readiness.md", "committed.md"],
        cwd=repository,
    )
    if added.returncode:
        raise AssertionError(added.stderr)
    committed = run(["git", "commit", "--quiet", "-m", "fixture"], cwd=repository)
    if committed.returncode:
        raise AssertionError(committed.stderr)
    staged = run(["git", "add", "staged.md"], cwd=repository)
    if staged.returncode:
        raise AssertionError(staged.stderr)

    config = repository / "fixture.yml"
    config.write_text("task_prefix: a\n", encoding="utf-8")
    return config


class ConfiguredPrefixTests(unittest.TestCase):
    def test_current_and_a_prefixes_derive_from_the_same_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            legacy = "task"
            configs = {
                legacy: f'task_prefix: "{legacy}"\n',
                "a": "task_prefix: a\n",
            }
            for prefix, content in configs.items():
                with self.subTest(prefix=prefix):
                    config = directory / f"{prefix}.yml"
                    config.write_text(content, encoding="utf-8")
                    result = run_checker(ROOT, config)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["display_prefix"], prefix.upper())
                    self.assertEqual(payload["unclassified_occurrences"], [])

    def assert_config_refused(self, content: str):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.yml"
            config.write_text(content, encoding="utf-8")
            result = run_checker(ROOT, config)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_malformed_prefix_is_refused(self):
        self.assert_config_refused("task_prefix: a-1\n")

    def test_duplicate_prefix_is_refused(self):
        self.assert_config_refused("task_prefix: task\ntask_prefix: a\n")

    def test_dual_prefix_scalar_is_refused(self):
        self.assert_config_refused("task_prefix: task,a\n")


class InventoryTests(unittest.TestCase):
    def test_complete_inventory_covers_committed_staged_and_untracked_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "candidate"
            config = initialize_mixed_candidate_repository(repository)
            result = run_checker(repository, config)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["classified_occurrences"], 3)
        self.assertEqual(payload["unclassified_occurrences"], [])

    def test_unclassified_untracked_occurrence_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "candidate"
            config = initialize_mixed_candidate_repository(repository)
            identity_word = "TA" + "SK"
            (repository / "unexpected.md").write_text(
                f"Unclassified {identity_word}-99 reference.\n",
                encoding="utf-8",
            )
            result = run_checker(repository, config)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unclassified_occurrences", result.stderr)
        self.assertIn("unexpected.md", result.stderr)


if __name__ == "__main__":
    unittest.main()
