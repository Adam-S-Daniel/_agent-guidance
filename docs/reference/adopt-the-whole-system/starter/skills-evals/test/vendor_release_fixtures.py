"""Shared support for the nested vendor-release-impact-issues fixture tests.

Each test module under test/issues/ that covers one of
evals/vendor-release-impact-issues/<name>/ builds workspaces from that
fixture's seed, scores them with the harness's own scorer, and asserts the
same structural properties of its fixture.yaml. What they share lives here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TEST_DIR = Path(__file__).resolve().parent
REPO_ROOT = TEST_DIR.parent
HARNESS_DIR = REPO_ROOT / "harness"
SKILL_EVALS = REPO_ROOT / "evals" / "vendor-release-impact-issues"

sys.path.insert(0, str(HARNESS_DIR))
import run_eval  # noqa: E402
from scorers import objective  # noqa: E402


def regex_tokens(pattern: str):
    """Yield (text, kind) left to right; kind is "escape", "class" or "char".

    An escape pair and a whole `[...]` class are each one opaque token, so a
    `|`, `(`, `)`, `.` or `^` inside either is never read as structure.
    """
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n:
            yield pattern[i:i + 2], "escape"
            i += 2
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] == "^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" and j + 1 < n else 1
            j = min(j + 1, n)
            yield pattern[i:j], "class"
            i = j
        else:
            yield c, "char"
            i += 1


def anchoring_problems(pattern: str) -> list[str]:
    """Why `pattern` could match across lines or away from a line start.

    Empty when: the only inline flag, if any, is a leading `(?i)`; every
    top-level alternative starts with `^`; there is no unescaped `.`; every
    negated class excludes a newline; and no newline-matching shorthand
    (`\\s`, `\\W`, `\\D`) stands outside a class.
    """
    problems = []
    body = pattern[4:] if pattern.startswith("(?i)") else pattern
    if re.compile(body).flags & (re.DOTALL | re.IGNORECASE | re.VERBOSE | re.MULTILINE):
        problems.append("carries an inline flag other than a leading (?i)")
    alternatives, current, depth = [], [], 0
    for token in regex_tokens(body):
        text, kind = token
        if kind == "char" and text == "(":
            depth += 1
        elif kind == "char" and text == ")":
            depth -= 1
        elif kind == "char" and text == "|" and depth == 0:
            alternatives.append(current)
            current = []
            continue
        current.append(token)
    alternatives.append(current)
    for alternative in alternatives:
        if not alternative or alternative[0] != ("^", "char"):
            text = "".join(t for t, _ in alternative)
            problems.append(f"alternative {text!r} does not start with ^")
    for text, kind in regex_tokens(body):
        if kind == "char" and text == ".":
            problems.append("has an unescaped .")
        if kind == "class" and text.startswith("[^") and not (
                "\\n" in text or "\\s" in text):
            problems.append(f"negated class {text} admits a newline")
        if kind == "escape" and text in ("\\s", "\\W", "\\D"):
            problems.append(f"{text} outside a class can match a newline")
    return problems


def fixture_patterns(fixture: dict) -> list[tuple[str, str]]:
    """(check id, pattern) for every must_match / must_not_match pattern."""
    return [(check["id"], pattern)
            for check in fixture["objective_checks"]
            for key in ("must_match", "must_not_match")
            for pattern in check.get(key) or []]


class VendorReleaseFixtureCase(unittest.TestCase):
    """Base for one nested fixture's tests. Subclasses set FIXTURE_NAME."""

    FIXTURE_NAME = ""

    @classmethod
    def eval_dir(cls) -> Path:
        return SKILL_EVALS / cls.FIXTURE_NAME

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix=f"vrii-{self.FIXTURE_NAME}-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.fixture = run_eval.load_fixture(self.eval_dir())
        self.seed = self.eval_dir() / "seed"
        self._serial = 0

    # ---- workspaces --------------------------------------------------------

    def ws(self) -> Path:
        """The workspace the arm gets: the production materializer's copy."""
        self._serial += 1
        ws = self.tmp / f"ws-{self._serial}"
        with mock.patch.dict(os.environ, self.env, clear=True):
            actual = run_eval.materialize_workspace(self.seed, self.fixture, workspace=ws)
        self.assertEqual(actual, ws)
        return ws

    def write(self, ws: Path, rel: str, text: str) -> None:
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def replace_once(self, path: Path, old: str, new: str) -> None:
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, f"{path.name}: anchor {old!r}")
        path.write_text(text.replace(old, new), encoding="utf-8")

    # ---- scoring -----------------------------------------------------------

    def score(self, ws: Path) -> dict[str, bool]:
        results = objective.run_checks(self.fixture, str(ws), str(self.seed))
        self.assertEqual([r["id"] for r in results],
                         [c["id"] for c in self.fixture["objective_checks"]])
        return {r["id"]: r["passed"] for r in results}

    def failed(self, ws: Path) -> set[str]:
        return {check for check, passed in self.score(ws).items() if not passed}

    def score_cli(self) -> tuple[int, dict[str, bool]]:
        """The pristine seed, scored through run_eval.py's command line."""
        env = dict(self.env, TMPDIR=str(self.tmp),
                   PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
        proc = subprocess.run(
            [sys.executable, str(HARNESS_DIR / "run_eval.py"), str(self.eval_dir()),
             "--arm", "objective-only", "--results-dir", str(self.tmp / "results")],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=300)
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"run_eval.py printed no JSON (exit {proc.returncode}):\n"
                      f"{proc.stdout}\n{proc.stderr}")
        return proc.returncode, {c["id"]: c["passed"] for c in payload["checks"]}

    # ---- structure shared by every nested fixture --------------------------

    def assert_shared_structure(self, rubric_caps: set[str]) -> None:
        self.assertFalse((SKILL_EVALS / "fixture.yaml").exists(),
                         "a skill dir holds nested fixtures or one flat one, never both")
        self.assertEqual(self.fixture["skill"], "vendor-release-impact-issues")
        self.assertEqual(self.fixture["registry"],
                         "https://github.com/example-adopter/acsi-agentskills")
        self.assertNotIn("model", self.fixture, "arms follow the roster (ADR 0013)")
        self.assertNotIn("model", self.fixture["judge"], "the judge follows the arm")
        weights = self.fixture["judge"]["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertEqual(max(weights, key=weights.get), "correctness")
        self.assertGreater(weights["correctness"], weights["restraint"])
        self.assertEqual(self.fixture["arms"], {"with_skill": {"install": "copy"},
                                                "without_skill": {"install": "none"}})
        for check_id, pattern in fixture_patterns(self.fixture):
            with self.subTest(check=check_id, pattern=pattern):
                self.assertEqual(anchoring_problems(pattern), [])
        ids = [c["id"] for c in self.fixture["objective_checks"]]
        self.assertEqual(len(ids), len(set(ids)))
        rubric = " ".join(self.fixture["judge_rubric"].split())
        for check_id in rubric_caps:
            self.assertIn(check_id, ids, "the rubric caps on a check that does not exist")
            self.assertIn(check_id, rubric)
