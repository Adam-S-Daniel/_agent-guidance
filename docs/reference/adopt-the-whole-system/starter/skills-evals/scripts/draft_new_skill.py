#!/usr/bin/env python3
"""Turn a `new-skill` lesson into a drafted, measured skill, or a hook proposal.

The consumer side of the session miner. The miner (acsi-agentskills-private,
redacted at the source) writes one JSON proposal per lesson under
`lessons/proposals/<YYYY-MM-DD>/<subject>__<kind>.json`. A daily caller,
`.github/workflows/routine-draft-skill.yml`, picks one `new-skill` proposal
(`select`) and fires the eval routine in `draft` mode
(`routine-eval-fire.yml`, which builds the payload with `payload` and
re-checks it with `fire-check`). The routine drafts, measures and pushes one
branch, `claude/draft-skill-<run id>`; `routine-draft-skill-gate.yml`
validates that branch from the default branch (`validate`) and opens what it
asks for. Nothing here merges anything, and the model never writes the
registry's bookkeeping: `apply` makes those edits deterministically.

HOOKS FIRST (the owner, 2026-10-26): every lesson is triaged in this order.

  1. A mechanical check: one in the fleet's `.githooks/agent-checks` runner
     (_agent-guidance ADR 0021, enforced by the required `agent-checks` CI
     job) or a PreToolUse guard. The routine reads
     _agent-guidance's `templates/agent-checks/` and decides. If the lesson
     can be checked mechanically it drafts NO skill and NO fixtures, runs
     `hook-first`, and pushes a branch whose record is `decision:
     "hook-first"` with a `check_sketch`. The gate opens an issue (body from
     `issue-body`, machine-readable) for the agent-checks lane.
  2. Only if it cannot, a skill: drafted with Anthropic's skill-creator at
     SKILL_CREATOR_COMMIT, with four fixtures, measured by `measure`. The
     record carries `triage: {mechanical: false, reason}` so a reviewer sees
     why it is not a hook. When the agent-checks lane declines a hook
     proposal (it closes the issue as not planned), `select --hook-declined`
     makes the subject eligible again, the payload says `hook_declined`,
     the routine skips triage, and the reason is DECLINED_REASON.
  3. Guidance text last, and never from here.

Subcommands (`key=value` lines are for $GITHUB_OUTPUT and hold validated
values only):

  select    --lessons DIR --registry NAME=DIR [...] --evals DIR
            --drafted FILE [--hook-declined FILE]
            -> proposal=<path under lessons/>, count=0|1, hook_declined=0|1
  drafted-subjects --repo DIR      -> `<subject> <decision>` per line
  payload   --lessons DIR --proposal RELPATH --run-id ID [--hook-declined]
            -> the fire payload JSON on stdout
  fire-check --payload FILE        (no output; exit 0 or 1)
  hook-first --payload FILE --proposal-json FILE --results DIR
  measure   --staging DIR --registry acsi-agentskills=DIR --ref SHA
            --payload FILE --trials N --results DIR
            (exit 0 accepted, 1 rejected, 2 refused)
  resolve   --event-name NAME --event-path PATH
  validate  --repo DIR --base REF --source REF --branch NAME
            [--expect-sha SHA] --registry acsi-agentskills=DIR --out DIR
            -> decision=, subject=, plugin=, run_id=, sha=
  apply     --staged DIR --tree DIR         (in an acsi-agentskills checkout)
  fixtures-apply --staged DIR --tree DIR    (in a skills-evals checkout)
  pr-body   --staged DIR --run-id ID --sha SHA --branch NAME [--mode draft|auto]
            --repo acsi-agentskills|skills-evals         (body on stdout)
  issue-body --staged DIR --run-id ID --sha SHA --branch NAME (body on stdout)

`measure` lays `--results` out as the tree the branch adds:
`draft-skill/<run id>/summary.json` always; on accept also `report.md`,
`skill.patch` and `evals/<subject>/<fixture>/...`. The routine copies that
tree onto its branch as it is.

The staging directory `measure` reads holds exactly `skill/` (SKILL.md,
PURPOSE.md, optional `references/`, `scripts/`, `assets/`), `fixtures/<name>/`
(4..8, each `fixture.yaml` and an optional `seed/`), `plugin` (one line) and
`triage.json` (`{"mechanical": false, "reason": "..."}`; not needed when the
payload says `hook_declined`).

The branch, the staging directory, the payload and every proposal are
UNTRUSTED (a model or a mined session wrote them). They are read as data
only; every refusal is a fixed message that never quotes them.
"""

from __future__ import annotations

import argparse
from datetime import date
from fractions import Fraction
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DIR = REPO_ROOT / "harness"
RUN_EVAL = REPO_ROOT / "harness" / "run_eval.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(HARNESS_DIR))

import context  # noqa: E402
import improve_gate  # noqa: E402
import ingest_routine_results as ingest  # noqa: E402
from ingest_routine_results import Rejected  # noqa: E402

# ---------------------------------------------------------------------------
# Names and limits.

#: A subject becomes a skill directory basename and `evals/<subject>/`.
SUBJECT = r"[a-z0-9][a-z0-9-]{0,63}"
SUBJECT_RE = re.compile(SUBJECT)
PROPOSAL_ID = r"[A-Za-z0-9._:-]{1,128}"
REPO_NAME = r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+"
DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
KINDS = ("skill-edit", "new-skill", "guidance-edit", "memory-note",
         "fixture-candidate")
#: The one shape a proposal path takes under `lessons/` (the fire workflow
#: checks its input with the same expression).
PROPOSAL_PATH_RE = re.compile(
    rf"proposals/(?P<date>{DATE})/(?P<subject>{SUBJECT})__(?P<kind>new-skill)\.json")

#: The proposal schema, one table: when the miner's final schema lands, this
#: is the one edit. `type` is const | str | text | int | date | list.
PROPOSAL_SCHEMA = {
    "schema_version": {"type": "const", "value": 1},
    "id": {"type": "str", "pattern": PROPOSAL_ID},
    "subject": {"type": "str", "pattern": SUBJECT},
    "kind": {"type": "str", "choices": KINDS},
    "evidence_count": {"type": "int", "min": 1, "max": 10 ** 6},
    "first_seen": {"type": "date"},
    "last_seen": {"type": "date"},
    "examples": {"type": "list", "min": 1, "max": 20,
                 "item": {"type": "text", "max": 2000}},
    "repos": {"type": "list", "min": 0, "max": 20,
              "item": {"type": "str", "pattern": REPO_NAME}},
}
#: What the payload carries of a proposal (dates dropped).
PAYLOAD_PROPOSAL_KEYS = ("id", "subject", "evidence_count", "examples", "repos")
MAX_PROPOSAL_FILE = 256 * 1024
#: The fire payload's UTF-8 cap, under the fire step's 65536-byte text cap.
MAX_PAYLOAD_BYTES = 60000

RUN_ID_RE = re.compile(ingest.RUN_ID)
BRANCH_RE = re.compile(rf"claude/draft-skill-(?P<run_id>{ingest.RUN_ID})")
BRANCH_SHAPE = "claude/draft-skill-<run id>"
SOURCE_ROOT = "draft-skill"
REMOTE_PREFIX = "refs/remotes/origin/"
DECISIONS = ("accepted", "rejected", "hook-first")

REGISTRY_NAME = improve_gate.REGISTRY_NAME
REGISTRY_URL = improve_gate.REGISTRY_URL
PLUGINS = ("acsi-anything-anywhere", "acsi-coding-anywhere",
           "acsi-coding-local", "acsi-non-coding-local")
#: anthropics/claude-plugins-official, the skill-creator the routine drafts with.
SKILL_CREATOR_COMMIT = "2a40fd2e7c52207aa903bd33fc4c65716126966e"
TOKENS_BASIS = "model_usage_total"
MAX_TRIALS = 20

#: The hook-first record's sketch of the check, for the agent-checks lane.
HOOK_EVENTS = ("pre-commit", "pre-push", "ci")
CHECK_SKETCH_LIMITS = {"event": None, "inspects": 2000, "refusal_message": 500,
                       "rationale": 2000, "false_positive_risk": 1000}
TRIAGE_REASON_MAX = 2000
DECLINED_REASON = "declined by the agent-checks lane"

SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
FIXTURE_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
SKILL_TOP = ("SKILL.md", "PURPOSE.md", "references", "scripts", "assets")
SKILL_FILE_CAP = 64 * 1024
SKILL_TOTAL_CAP = 256 * 1024
SKILL_MAX_FILES = 64
MIN_FIXTURES, MAX_FIXTURES = 4, 8
FIXTURE_FILE_CAP = 256 * 1024
FIXTURE_TOTAL_CAP = 2 * 1024 * 1024
FIXTURE_MAX_FILES = 400
#: The branch's own files and their caps.
SIZE_CAPS = {"summary.json": 256 * 1024, "report.md": 64 * 1024,
             "skill.patch": 384 * 1024}
#: A drafted fixture is a Class A isolation pair: no context (drafts run in
#: isolation pairing), no setup hook, env, follow-up or model pin.
FIXTURE_KEYS = ("skill", "registry", "prompt", "arms", "objective_checks",
                "judge", "judge_rubric", "timeout_s")
FIXTURE_REQUIRED = ("skill", "registry", "prompt", "objective_checks",
                    "judge_rubric")
ARMS = {"with_skill": {"install": "copy"}, "without_skill": {"install": "none"}}
MAX_FIXTURE_TIMEOUT_S = 600
#: An example this long appearing verbatim in a staged file is a quote.
VERBATIM_MIN_CHARS = 40

ARM_KEYS = ("n", "errors", "passed", "total", "pass_rate", "tokens")
SKILL_RECORD_KEYS = ("schema", "run_id", "decision", "reasons", "subject",
                     "plugin", "registry", "registry_sha", "evidence_count",
                     "trials", "triage", "fixtures", "skill_creator_commit",
                     "tokens_basis")
HOOK_RECORD_KEYS = ("schema", "run_id", "decision", "subject",
                    "evidence_count", "proposal", "check_sketch")

SKILL_TITLE = "# New skill: {subject}"
HOOK_TITLE = "# Hook first: {subject}"
MARKER = "<sub>🤖 Written by Claude Code on behalf of @example-adopter</sub>"
EVALS_TREE = "https://github.com/example-adopter/skills-evals/tree/{sha}"
TARGET_BRANCH = "draft-skill/{subject}"
TEMPLATE_CHECKLIST = (
    "- [ ] Examples, fixtures and docs use invented data only (example.com, "
    "fictional names); no real personal details, usernames, hostnames or "
    "account IDs.\n"
    "- [ ] A skill-content change has an entry in `docs/skill-impact.md`, and "
    "the plugin's version is bumped in both manifests.\n"
    "- [ ] Tests run: `python3 -m pytest scripts/ plugins/*/skills/*/tests/ "
    "plugins/*/skills/*/scripts/ -q` (say the count).")


class Refused(Exception):
    """The operator's input cannot be used: exit 2, nothing written."""


# ---------------------------------------------------------------------------
# Values.

def text_ok(value, limit: int) -> bool:
    """A non-blank string within `limit` characters, no control character
    but newline and tab."""
    return (isinstance(value, str) and value.strip() != "" and len(value) <= limit
            and not any((ord(c) < 32 and c not in "\n\t") or ord(c) == 127
                        for c in value))


def value_ok(spec: dict, value) -> bool:
    kind = spec["type"]
    if kind == "const":
        return type(value) is type(spec["value"]) and value == spec["value"]
    if kind == "int":
        return (type(value) is int and spec["min"] <= value <= spec["max"])
    if kind == "text":
        return text_ok(value, spec["max"])
    if kind == "str":
        if not isinstance(value, str):
            return False
        if "choices" in spec:
            return value in spec["choices"]
        return re.fullmatch(spec["pattern"], value) is not None
    if kind == "date":
        return isinstance(value, str) and parse_date(value) is not None
    if kind == "list":
        return (isinstance(value, list) and spec["min"] <= len(value) <= spec["max"]
                and all(value_ok(spec["item"], item) for item in value))
    raise AssertionError(kind)


def parse_date(value: str) -> date | None:
    if not re.fullmatch(DATE, value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def check_proposal(doc, path_date: str | None = None,
                   path_subject: str | None = None) -> dict:
    """A full proposal file's content, or Rejected. The path's date must not
    predate the evidence it holds, and its subject and kind must be the
    content's."""
    if not isinstance(doc, dict) or set(doc) != set(PROPOSAL_SCHEMA):
        raise Rejected("proposal: not exactly the schema's keys")
    for key, spec in PROPOSAL_SCHEMA.items():
        if not value_ok(spec, doc[key]):
            raise Rejected("proposal: a field fails the schema")
    if doc["kind"] != "new-skill":
        raise Rejected("proposal: not a new-skill proposal")
    first, last = parse_date(doc["first_seen"]), parse_date(doc["last_seen"])
    if first > last:
        raise Rejected("proposal: first_seen is after last_seen")
    if path_date is not None and parse_date(path_date) is None:
        raise Rejected("proposal: the path's date is not a date")
    if path_date is not None and parse_date(path_date) < last:
        raise Rejected("proposal: the path's date predates last_seen")
    if path_subject is not None and doc["subject"] != path_subject:
        raise Rejected("proposal: the path's subject is not the content's")
    return doc


def check_payload_proposal(proposal) -> dict:
    if not isinstance(proposal, dict) or set(proposal) != set(PAYLOAD_PROPOSAL_KEYS):
        raise Rejected("payload: proposal is not exactly "
                       f"{list(PAYLOAD_PROPOSAL_KEYS)}")
    for key in PAYLOAD_PROPOSAL_KEYS:
        if not value_ok(PROPOSAL_SCHEMA[key], proposal[key]):
            raise Rejected("payload: a proposal field fails the schema")
    return proposal


def encode_payload(doc: dict) -> bytes:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def check_payload(doc) -> dict:
    """The fire payload: exactly run_id, mode, proposal, and an optional
    `hook_declined: true`; within MAX_PAYLOAD_BYTES."""
    if not isinstance(doc, dict) or not (
            {"run_id", "mode", "proposal"} <= set(doc)
            <= {"run_id", "mode", "proposal", "hook_declined"}):
        raise Rejected("payload: not exactly run_id, mode, proposal "
                       "(and an optional hook_declined)")
    if not isinstance(doc["run_id"], str) or not RUN_ID_RE.fullmatch(doc["run_id"]):
        raise Rejected("payload: run_id is not a routine run id")
    if doc["mode"] != "draft":
        raise Rejected("payload: mode is not draft")
    if "hook_declined" in doc and doc["hook_declined"] is not True:
        raise Rejected("payload: hook_declined is present and not true")
    check_payload_proposal(doc["proposal"])
    if len(encode_payload(doc)) > MAX_PAYLOAD_BYTES:
        raise Rejected(f"payload: over {MAX_PAYLOAD_BYTES} UTF-8 bytes")
    return doc


def read_json_file(path: Path, cap: int, what: str):
    """A small JSON file that is not a symlink, strictly parsed."""
    try:
        if path.is_symlink() or not path.is_file():
            raise Rejected(f"{what}: not a regular file")
        data = path.read_bytes()
    except OSError:
        raise Rejected(f"{what}: unreadable") from None
    if len(data) > cap:
        raise Rejected(f"{what}: over its size cap")
    return ingest.load_json(ingest.decode_text(data, what), what)


def load_payload(path: Path) -> dict:
    return check_payload(read_json_file(path, MAX_PAYLOAD_BYTES, "payload"))


def check_sketch(sketch) -> dict:
    if not isinstance(sketch, dict) or set(sketch) != set(CHECK_SKETCH_LIMITS):
        raise Rejected(f"check_sketch: not exactly {list(CHECK_SKETCH_LIMITS)}")
    if sketch["event"] not in HOOK_EVENTS:
        raise Rejected(f"check_sketch: event is not one of {list(HOOK_EVENTS)}")
    for key, limit in CHECK_SKETCH_LIMITS.items():
        if limit is not None and not text_ok(sketch[key], limit):
            raise Rejected(f"check_sketch: {key} is not text of at most "
                           f"{limit} characters")
    return sketch


def check_triage(triage) -> dict:
    if not isinstance(triage, dict) or set(triage) != {"mechanical", "reason"}:
        raise Rejected("triage: not exactly mechanical and reason")
    if triage["mechanical"] is not False:
        raise Rejected("triage: a skill record's mechanical must be false")
    if not text_ok(triage["reason"], TRIAGE_REASON_MAX):
        raise Rejected(f"triage: reason is not text of at most "
                       f"{TRIAGE_REASON_MAX} characters")
    return triage


def text_problem(text: str) -> str | None:
    """Why `text` may not go into a pull request or issue body, or None. A
    code fence is refused too: it could swallow what the body adds below."""
    stripped = improve_gate.closing_text(text)
    if improve_gate.CLOSING_RE.search(text) or improve_gate.CLOSING_RE.search(stripped) \
            or improve_gate.CLOSING_URL_RE.search(stripped):
        return "has a closing keyword before an issue or pull request reference"
    if improve_gate.MENTION_RE.search(text):
        return "has an @mention"
    if "```" in text or "~~~" in text:
        return "has a code fence"
    return None


# ---------------------------------------------------------------------------
# Skill and fixture content (shared by measure and validate).

def _safe_rel(rel: str) -> bool:
    parts = rel.split("/")
    return all(SEGMENT_RE.fullmatch(p) and p not in (".", "..", ".git")
               for p in parts)


def frontmatter(text: str) -> dict | None:
    if not text.startswith("---\n") or "\n---\n" not in text[3:]:
        return None
    try:
        data = yaml.safe_load(text[4:text.index("\n---\n", 3)])
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def skill_problems(files: dict[str, bytes], subject: str) -> list[str]:
    """Problems with a skill's files (paths relative to its directory)."""
    problems = []
    if not 1 <= len(files) <= SKILL_MAX_FILES:
        problems.append(f"skill: 1..{SKILL_MAX_FILES} files expected")
    if sum(len(d) for d in files.values()) > SKILL_TOTAL_CAP:
        problems.append("skill: over the total size cap")
    for rel, data in sorted(files.items()):
        top = rel.split("/", 1)[0]
        if not _safe_rel(rel) or top not in SKILL_TOP \
                or (top in ("SKILL.md", "PURPOSE.md") and rel != top) \
                or (top not in ("SKILL.md", "PURPOSE.md") and rel == top):
            problems.append("skill: a file is outside SKILL.md, PURPOSE.md, "
                            "references/, scripts/ and assets/")
            continue
        if len(data) > SKILL_FILE_CAP:
            problems.append("skill: a file is over the per-file cap")
        try:
            text = ingest.decode_text(data, "skill file")
        except Rejected:
            problems.append("skill: a file is not UTF-8 text")
            continue
        if not text or not text.endswith("\n"):
            problems.append("skill: a file is empty or does not end with a newline")
    for required in ("SKILL.md", "PURPOSE.md"):
        if required not in files:
            problems.append(f"skill: {required} is missing")
    if "SKILL.md" in files:
        try:
            meta = frontmatter(files["SKILL.md"].decode("utf-8"))
        except UnicodeDecodeError:
            meta = None
        if meta is None or meta.get("name") != subject \
                or not text_ok(meta.get("description"), 1024):
            problems.append("skill: SKILL.md's frontmatter does not name the "
                            "subject with a description")
    return sorted(set(problems))


def fixture_problem(data: bytes, subject: str) -> str | None:
    """Why one fixture.yaml is not a drafted fixture of `subject`, or None.
    Loaded with the harness's own loader."""
    try:
        doc = context.load_fixture_yaml(data, Path("fixture.yaml"))
    except Exception:  # noqa: BLE001 — any loader failure is a refusal
        return "fixture.yaml does not load with the harness's fixture loader"
    if set(doc) - set(FIXTURE_KEYS) or set(FIXTURE_REQUIRED) - set(doc):
        return (f"fixture.yaml keys are not within {list(FIXTURE_KEYS)} with "
                f"{list(FIXTURE_REQUIRED)} present (no context:, drafts run in "
                "isolation pairing)")
    if doc["skill"] != subject or doc["registry"] != REGISTRY_URL:
        return "fixture.yaml does not name the subject and acsi-agentskills"
    if not isinstance(doc["prompt"], str) or not doc["prompt"].strip():
        return "fixture.yaml has no prompt"
    if "arms" in doc and doc["arms"] != ARMS:
        return "fixture.yaml arms are not the isolation pair"
    checks = doc["objective_checks"]
    if not isinstance(checks, list) or not checks or not all(
            isinstance(c, dict) and isinstance(c.get("id"), str)
            and isinstance(c.get("type"), str) for c in checks):
        return "fixture.yaml has no objective checks with an id and a type"
    if "judge" in doc and (not isinstance(doc["judge"], dict) or "model" in doc["judge"]):
        return "fixture.yaml judge is not a mapping without a model pin"
    if "timeout_s" in doc and (type(doc["timeout_s"]) is not int
                               or not 1 <= doc["timeout_s"] <= MAX_FIXTURE_TIMEOUT_S):
        return f"fixture.yaml timeout_s is not 1..{MAX_FIXTURE_TIMEOUT_S}"
    return None


def fixtures_problems(fixtures: dict[str, dict[str, bytes]], subject: str) -> list[str]:
    """Problems with fixture directories: name -> {relative path: bytes}."""
    import run_eval  # noqa: PLC0415 — heavy; only the fixture name rule
    problems = []
    if not MIN_FIXTURES <= len(fixtures) <= MAX_FIXTURES:
        problems.append(f"fixtures: {MIN_FIXTURES}..{MAX_FIXTURES} expected")
    count = sum(len(f) for f in fixtures.values())
    total = sum(len(d) for f in fixtures.values() for d in f.values())
    if count > FIXTURE_MAX_FILES or total > FIXTURE_TOTAL_CAP:
        problems.append("fixtures: over the file count or total size cap")
    for name, files in sorted(fixtures.items()):
        try:
            ok = FIXTURE_NAME_RE.fullmatch(name) is not None
            run_eval._validate_fixture_name(name)
        except run_eval.FixtureLayoutError:
            ok = False
        if not ok:
            problems.append("fixtures: a fixture name is not a plain lowercase "
                            "name or is reserved")
            continue
        for rel, data in files.items():
            if not _safe_rel(rel) or not (rel == "fixture.yaml"
                                          or rel.startswith("seed/")):
                problems.append("fixtures: a file is outside fixture.yaml and seed/")
            elif len(data) > FIXTURE_FILE_CAP:
                problems.append("fixtures: a file is over the per-file cap")
            else:
                try:
                    ingest.decode_text(data, "fixture file")
                except Rejected:
                    problems.append("fixtures: a file is not UTF-8 text")
        if "fixture.yaml" not in files:
            problems.append("fixtures: a fixture has no fixture.yaml")
        else:
            problem = fixture_problem(files["fixture.yaml"], subject)
            if problem:
                problems.append(f"fixtures: {problem}")
    return sorted(set(problems))


def read_tree(root: Path) -> dict[str, bytes]:
    """Every file below `root` by relative path; Rejected on a symlink, a
    special file, or too many files."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise Rejected("staging holds a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise Rejected("staging holds a special file")
        out[rel] = path.read_bytes()
        if len(out) > FIXTURE_MAX_FILES + SKILL_MAX_FILES:
            raise Rejected("staging holds too many files")
    return out


# ---------------------------------------------------------------------------
# The patch: a `git diff --no-index`-style addition of new files only.

def new_files_patch(prefix: str, files: dict[str, bytes]) -> str:
    """One `new file` block per file under `prefix`, in path order. Every
    file is non-empty UTF-8 text ending in a newline (skill_problems)."""
    out = []
    for rel in sorted(files):
        path = f"{prefix}/{rel}"
        lines = files[rel].decode("utf-8").split("\n")[:-1]
        out += [f"diff --git a/{path} b/{path}", "new file mode 100644",
                "--- /dev/null", f"+++ b/{path}", f"@@ -0,0 +1,{len(lines)} @@"]
        out += ["+" + line for line in lines]
    return "\n".join(out) + "\n"


NEW_HUNK_RE = re.compile(r"@@ -0,0 \+1(?:,(?P<n>[1-9][0-9]*))? @@")


def parse_new_files_patch(text: str, prefix: str) -> dict[str, bytes]:
    """The files a new-files patch adds below `prefix`, or Rejected."""
    if not text.endswith("\n"):
        raise Rejected("skill.patch: does not end with a newline")
    lines = text[:-1].split("\n")
    files, i = {}, 0
    while i < len(lines):
        head = lines[i:i + 5]
        match = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", head[0]) if head else None
        if not match or match.group(1) != match.group(2) or len(head) < 5 \
                or head[1:3] != ["new file mode 100644", "--- /dev/null"] \
                or head[3] != f"+++ b/{match.group(1)}":
            raise Rejected("skill.patch: a block is not a new regular file")
        path = match.group(1)
        if not path.startswith(prefix + "/"):
            raise Rejected("skill.patch: a file is outside the new skill's directory")
        rel = path[len(prefix) + 1:]
        if not _safe_rel(rel) or rel in files:
            raise Rejected("skill.patch: a path is unsafe or repeated")
        hunk = NEW_HUNK_RE.fullmatch(head[4])
        if not hunk:
            raise Rejected("skill.patch: a hunk header is not a whole new file")
        count = int(hunk.group("n") or 1)
        body = lines[i + 5:i + 5 + count]
        if len(body) != count or not all(line.startswith("+") for line in body):
            raise Rejected("skill.patch: a hunk does not match its header")
        files[rel] = ("\n".join(line[1:] for line in body) + "\n").encode("utf-8")
        i += 5 + count
    if not files:
        raise Rejected("skill.patch: adds nothing")
    return files


def skill_prefix(plugin: str, subject: str) -> str:
    return f"plugins/{plugin}/skills/{subject}"


# ---------------------------------------------------------------------------
# select / drafted-subjects / payload / fire-check.

def parse_registries(values: list[str]) -> dict[str, Path]:
    out = {}
    for value in values or []:
        name, sep, path = value.partition("=")
        if not sep or not SEGMENT_RE.fullmatch(name) or not path or name in out:
            raise Refused("--registry takes NAME=DIR, once per name")
        out[name] = Path(path)
    return out


def skill_exists(registry: Path, subject: str) -> bool:
    return any(p.is_file() for p in registry.glob(f"plugins/*/skills/{subject}/SKILL.md"))


def parse_drafted(path: Path) -> dict[str, set[str]]:
    """`<subject> <decision>` lines (drafted-subjects' output) by subject;
    anything else is skipped."""
    out: dict[str, set[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and SUBJECT_RE.fullmatch(parts[0]) and parts[1] in DECISIONS:
            out.setdefault(parts[0], set()).add(parts[1])
    return out


def parse_subjects(path: Path | None) -> set[str]:
    if path is None:
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if SUBJECT_RE.fullmatch(line.strip())}


def eligibility(subject: str, drafted: dict[str, set[str]],
                declined: set[str]) -> tuple[bool, bool]:
    """(eligible, via a declined hook proposal). A skill record (accepted or
    rejected) is final; a hook-first record holds the subject until the
    agent-checks lane declines it."""
    decisions = drafted.get(subject, set())
    if decisions & {"accepted", "rejected"}:
        return False, False
    if "hook-first" in decisions:
        return (True, True) if subject in declined else (False, False)
    return True, False


def select(lessons: Path, registries: dict[str, Path], evals: Path,
           drafted_file: Path, declined_file: Path | None) -> dict:
    drafted = parse_drafted(drafted_file)
    declined = parse_subjects(declined_file)
    proposals_dir = lessons / "proposals"
    candidates, skipped = [], 0
    days = sorted(proposals_dir.iterdir()) if proposals_dir.is_dir() \
        and not proposals_dir.is_symlink() else []
    for day in days:
        if day.is_symlink() or not day.is_dir():
            continue
        for path in sorted(day.glob("*__new-skill.json")):
            rel = path.relative_to(lessons).as_posix()
            match = PROPOSAL_PATH_RE.fullmatch(rel)
            try:
                if not match:
                    raise Rejected("bad path")
                doc = check_proposal(read_json_file(path, MAX_PROPOSAL_FILE, "proposal"),
                                     match.group("date"), match.group("subject"))
                # One the fire could not send is never picked (it would be
                # picked again every day).
                check_payload(build_payload(doc, "00000000T000000Z-000000", True))
            except Rejected:
                skipped += 1
                continue
            subject = doc["subject"]
            eligible, via_decline = eligibility(subject, drafted, declined)
            if not eligible or any(skill_exists(r, subject) for r in registries.values()) \
                    or (evals / subject).exists():
                continue
            candidates.append(((-doc["evidence_count"],
                                -parse_date(doc["last_seen"]).toordinal(),
                                subject, rel), rel, via_decline))
    print(f"proposals skipped as invalid: {skipped}", file=sys.stderr)
    if not candidates:
        return {"proposal": "", "count": "0", "hook_declined": "0"}
    _, rel, via_decline = min(candidates)
    return {"proposal": rel, "count": "1", "hook_declined": "1" if via_decline else "0"}


def drafted_subjects(repo: str) -> list[str]:
    """`<subject> <decision>` for every remote draft branch whose record can
    be read, through git plumbing only."""
    refs = ingest.git(repo, "for-each-ref", "--format=%(refname)",
                      REMOTE_PREFIX + "claude/draft-skill-*").decode("utf-8", "replace")
    lines = set()
    for ref in refs.splitlines():
        match = BRANCH_RE.fullmatch(ref[len(REMOTE_PREFIX):]) \
            if ref.startswith(REMOTE_PREFIX) else None
        if not match:
            continue
        spec = f"{ref}:{SOURCE_ROOT}/{match.group('run_id')}/summary.json"
        try:
            size = int(ingest.git(repo, "cat-file", "-s", spec).decode().strip())
            if size > SIZE_CAPS["summary.json"]:
                continue
            doc = ingest.load_json(ingest.decode_text(
                ingest.git(repo, "cat-file", "blob", spec), "summary"), "summary")
        except (Rejected, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("subject"), str) \
                and SUBJECT_RE.fullmatch(doc["subject"]) \
                and doc.get("decision") in DECISIONS:
            lines.add(f"{doc['subject']} {doc['decision']}")
    return sorted(lines)


def build_payload(doc: dict, run_id: str, hook_declined: bool) -> dict:
    payload = {"run_id": run_id, "mode": "draft",
               "proposal": {key: doc[key] for key in PAYLOAD_PROPOSAL_KEYS}}
    if hook_declined:
        payload["hook_declined"] = True
    return payload


def payload(lessons: Path, rel: str, run_id: str, hook_declined: bool) -> bytes:
    match = PROPOSAL_PATH_RE.fullmatch(rel)
    if not match:
        raise Rejected("proposal path is not proposals/<date>/<subject>__new-skill.json")
    if not RUN_ID_RE.fullmatch(run_id):
        raise Rejected("run id is not a routine run id")
    root = (lessons / "proposals").resolve()
    cursor = lessons
    for part in Path(rel).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise Rejected("proposal path has a symlink")
    if not cursor.resolve().is_relative_to(root):
        raise Rejected("proposal path leaves lessons/proposals")
    doc = check_proposal(read_json_file(cursor, MAX_PROPOSAL_FILE, "proposal"),
                         match.group("date"), match.group("subject"))
    out = build_payload(doc, run_id, hook_declined)
    check_payload(out)
    return encode_payload(out)


# ---------------------------------------------------------------------------
# hook-first.

def one_line(text: str) -> str:
    return " ".join(text.split())


def hook_report(subject: str, evidence_count: int, sketch: dict) -> str:
    return "\n".join([
        HOOK_TITLE.format(subject=subject), "",
        "The draft loop's triage found this lesson can be enforced "
        "mechanically, so it drafted no skill and no fixtures. Hooks come "
        "first: a mechanical check, then a skill, then guidance text.", "",
        f"Drafted from a redacted lesson with {evidence_count} pieces of evidence.", "",
        "## Proposed check", "",
        f"- Event: `{sketch['event']}`",
        f"- What it inspects: {one_line(sketch['inspects'])}",
        f"- Refusal message: {one_line(sketch['refusal_message'])}",
        f"- Why a check beats a skill: {one_line(sketch['rationale'])}",
        f"- False-positive risk: {one_line(sketch['false_positive_risk'])}", ""])


def hook_first(payload_path: Path, sketch_path: Path, results: Path) -> dict:
    try:
        doc = load_payload(payload_path)
        sketch = check_sketch(read_json_file(sketch_path, 64 * 1024, "check_sketch"))
    except Rejected as exc:
        raise Refused(str(exc)) from None
    if doc.get("hook_declined"):
        raise Refused("the agent-checks lane declined this lesson's hook; "
                      "draft a skill instead")
    proposal = doc["proposal"]
    report = hook_report(proposal["subject"], proposal["evidence_count"], sketch)
    if text_problem(report):
        raise Refused("check_sketch: the report would carry a closing keyword, "
                      "an @mention or a code fence; reword it")
    record = {"schema": 1, "run_id": doc["run_id"], "decision": "hook-first",
              "subject": proposal["subject"],
              "evidence_count": proposal["evidence_count"],
              "proposal": proposal, "check_sketch": sketch}
    out = run_dir(results, doc["run_id"])
    out.mkdir(parents=True)
    write_json(out / "summary.json", record)
    (out / "report.md").write_text(report, encoding="utf-8")
    return {"decision": "hook-first", "subject": proposal["subject"],
            "run_id": doc["run_id"]}


def run_dir(results: Path, run_id: str) -> Path:
    out = results / SOURCE_ROOT / run_id
    if out.exists():
        raise Refused("the results directory already holds this run")
    return out


def write_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# measure.

class Runner:
    """The real thing: harness/run_eval.py, as the routine's eval mode runs
    it. Tests substitute a fake with the same method."""

    def run_eval(self, argv: list[str]) -> int:
        return subprocess.run([sys.executable, str(RUN_EVAL), *argv],
                              check=False).returncode


def run_eval_argv(eval_dir: Path, fixture: str, trials: int, registry: Path,
                  runs: Path, timestamp: str, run_id: str,
                  read_deny: list[Path]) -> list[str]:
    argv = [str(eval_dir), "--fixture", fixture, "--arm", "both",
            "--trials", str(trials), "--registry", f"{REGISTRY_NAME}={registry}",
            "--results-dir", str(runs), "--timestamp", timestamp,
            "--run-billing", "subscription", "--run-runner", "routine",
            "--run-location", "cloud", "--run-id", run_id]
    for path in read_deny:
        argv += ["--read-deny", str(path)]
    return argv


def arm_metrics(arm_dir: Path, trials: int) -> dict | None:
    """One arm's figures from its trial summaries, or None when a summary is
    missing or unreadable."""
    import propose_skill_edit as loop  # noqa: PLC0415 — measure only
    paths = [arm_dir / "summary.json"] if trials == 1 else [
        arm_dir / f"trial-{k}" / "summary.json" for k in range(1, trials + 1)]
    summaries = []
    for path in paths:
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(summary, dict):
            return None
        summaries.append(summary)
    stats = loop.run_eval.aggregate_trials(summaries)
    objective = stats["aggregate"]["objective"] or {}
    passed, total = objective.get("passed", 0), objective.get("total", 0)
    return {"n": stats["n"], "errors": stats["errors"], "passed": passed,
            "total": total, "pass_rate": passed / total if total else None,
            "tokens": loop.mean_total_tokens(summaries)}


def arm_complete(arm) -> bool:
    return (isinstance(arm, dict) and arm.get("errors") == 0
            and type(arm.get("total")) is int and arm["total"] > 0
            and type(arm.get("passed")) is int and 0 <= arm["passed"] <= arm["total"])


def decide(fixtures: dict) -> tuple[str, list[str]]:
    """ACCEPT iff every fixture has complete results for both arms, the
    with_skill arm passes every objective check on at least one fixture (the
    registry's graduation gate: one green with_skill arm), and the summed
    with_skill objective pass rate is strictly above without_skill's (the
    skill must add something). Pass rates are compared exactly."""
    if not fixtures:
        return "rejected", ["no fixture was measured"]
    reasons = []
    for name, row in sorted(fixtures.items()):
        if row.get("run_eval_exit") != 0:
            reasons.append(f"fixture {name}: run_eval exited non-zero")
        for arm in ("with_skill", "without_skill"):
            if not arm_complete(row.get(arm)):
                reasons.append(f"fixture {name}: {arm} has no complete results")
    if reasons:
        return "rejected", reasons
    rate = {arm: sum((Fraction(r[arm]["passed"], r[arm]["total"])
                      for r in fixtures.values()), Fraction(0))
            for arm in ("with_skill", "without_skill")}
    green = sorted(n for n, r in fixtures.items()
                   if r["with_skill"]["passed"] == r["with_skill"]["total"])
    summed = (f"summed objective pass rate: with_skill "
              f"{float(rate['with_skill']):.3f}, without_skill "
              f"{float(rate['without_skill']):.3f}")
    if not green:
        reasons.append("with_skill passed every objective check on no fixture "
                       "(the graduation gate needs one green with_skill arm)")
    if not rate["with_skill"] > rate["without_skill"]:
        reasons.append(f"the skill adds nothing: {summed}")
    if reasons:
        return "rejected", reasons
    return "accepted", [
        f"every one of {len(fixtures)} fixtures has results for both arms",
        f"with_skill passed every objective check on {len(green)} of "
        f"{len(fixtures)} fixtures", summed]


def _cell(arm: dict) -> str:
    return f"{arm['passed']}/{arm['total']} ({arm['pass_rate']:.2f})"


def _tokens(arm: dict) -> str:
    return "n/a" if arm.get("tokens") is None else f"{arm['tokens']:.0f}"


def skill_report(record: dict) -> str:
    rows = ["| Fixture | with_skill | without_skill | with_skill tokens | "
            "without_skill tokens |", "|---|---|---|---|---|"]
    for name, row in sorted(record["fixtures"].items()):
        rows.append(f"| `{name}` | {_cell(row['with_skill'])} | "
                    f"{_cell(row['without_skill'])} | {_tokens(row['with_skill'])} | "
                    f"{_tokens(row['without_skill'])} |")
    return "\n".join([
        SKILL_TITLE.format(subject=record["subject"]), "",
        f"Drafted by the skills-evals draft loop (run `{record['run_id']}`) with "
        f"Anthropic's skill-creator at `{SKILL_CREATOR_COMMIT}`, for the "
        f"`{record['plugin']}` plugin. Drafted from a redacted lesson with "
        f"{record['evidence_count']} pieces of evidence.", "",
        "## Why a skill and not a hook", "",
        one_line(record["triage"]["reason"]), "",
        f"## Measured ({record['trials']} trials per arm, routine run, "
        f"registry at `{record['registry_sha']}`)", "",
        "Objective checks passed over all trials; tokens are the mean per "
        "trial, every model counted.", "",
        *rows, "",
        "## Decision", "",
        *[f"- {reason}" for reason in record["reasons"]], ""])


def staging_parts(staging: Path) -> tuple[dict, dict, str]:
    """(skill files, fixture files by name, plugin) from the staging dir, or
    Rejected with a fixed reason."""
    if staging.is_symlink() or not staging.is_dir():
        raise Rejected("staging: not a directory")
    entries = {p.name for p in staging.iterdir()}
    if not {"skill", "fixtures", "plugin"} <= entries \
            <= {"skill", "fixtures", "plugin", "triage.json"}:
        raise Rejected("staging: not exactly skill/, fixtures/, plugin and triage.json")
    plugin_file = staging / "plugin"
    if plugin_file.is_symlink() or not plugin_file.is_file() \
            or plugin_file.stat().st_size > 256:
        raise Rejected("staging: plugin is not a small regular file")
    plugin = plugin_file.read_text(encoding="utf-8", errors="replace").strip()
    if plugin not in PLUGINS:
        raise Rejected(f"staging: plugin is not one of {list(PLUGINS)}")
    for name in ("skill", "fixtures"):
        if (staging / name).is_symlink() or not (staging / name).is_dir():
            raise Rejected(f"staging: {name}/ is not a directory")
    skill = read_tree(staging / "skill")
    fixtures: dict[str, dict[str, bytes]] = {}
    for rel, data in read_tree(staging / "fixtures").items():
        name, _, rest = rel.partition("/")
        if not rest:
            raise Rejected("staging: a file sits directly in fixtures/")
        fixtures.setdefault(name, {})[rest] = data
    return skill, fixtures, plugin


def quotes_an_example(files: list[bytes], examples: list[str]) -> bool:
    texts = [d.decode("utf-8", "replace") for d in files]
    return any(len(e.strip()) >= VERBATIM_MIN_CHARS and e.strip() in t
               for e in examples for t in texts)


def measure(staging: Path, registry_flag: str, ref: str, payload_path: Path,
            trials: int, results: Path, runner: Runner) -> int:
    import propose_skill_edit as loop  # noqa: PLC0415 — measure only
    try:
        doc = load_payload(payload_path)
    except Rejected as exc:
        raise Refused(str(exc)) from None
    registries = parse_registries([registry_flag])
    if set(registries) != {REGISTRY_NAME}:
        raise Refused(f"--registry must name {REGISTRY_NAME}")
    checkout = registries[REGISTRY_NAME]
    if not ingest.SHA_RE.fullmatch(ref):
        raise Refused("--ref is not a 40-hex commit id")
    if not 1 <= trials <= MAX_TRIALS:
        raise Refused(f"--trials must be 1..{MAX_TRIALS}")
    run_id, proposal = doc["run_id"], doc["proposal"]
    subject = proposal["subject"]
    if doc.get("hook_declined"):
        triage = {"mechanical": False, "reason": DECLINED_REASON}
    else:
        try:
            triage = check_triage(read_json_file(staging / "triage.json", 16 * 1024,
                                                 "triage"))
        except Rejected:
            raise Refused("staging triage.json is missing or malformed; a skill "
                          "record needs the triage that ruled out a hook") from None
    out = run_dir(results, run_id)
    record = {"schema": 1, "run_id": run_id, "decision": "rejected",
              "reasons": [], "subject": subject, "plugin": None,
              "registry": REGISTRY_URL, "registry_sha": ref,
              "evidence_count": proposal["evidence_count"], "trials": trials,
              "triage": triage, "fixtures": {},
              "skill_creator_commit": SKILL_CREATOR_COMMIT,
              "tokens_basis": TOKENS_BASIS}

    def finish(reasons: list[str]) -> int:
        record["reasons"] = reasons
        out.mkdir(parents=True)
        write_json(out / "summary.json", record)
        return 1

    try:
        skill, fixtures, plugin = staging_parts(staging)
    except Rejected as exc:
        return finish([str(exc)])
    record["plugin"] = plugin
    problems = skill_problems(skill, subject) + fixtures_problems(fixtures, subject)
    if problems:
        return finish(problems)
    staged = list(skill.values()) + [d for f in fixtures.values() for d in f.values()]
    if quotes_an_example(staged, proposal["examples"]):
        return finish(["a staged file quotes a lesson example verbatim"])

    with tempfile.TemporaryDirectory(prefix="propose-skill-edit-draft-") as tmp:
        scratch = Path(tmp)
        try:
            sha = loop.resolve_ref(checkout, ref)
            if sha != ref:
                raise Refused("--ref does not resolve to itself")
            archived = loop.archive_registry(checkout, ref, scratch / "registry")
        except loop.Refusal:
            raise Refused("the registry checkout cannot archive that commit") from None
        if skill_exists(archived, subject):
            return finish(["the subject already exists as a skill in the "
                           "registry at that commit"])
        if not (archived / "plugins" / plugin / "skills").is_dir():
            return finish(["the plugin has no skills/ directory in the registry "
                           "at that commit"])
        target = archived / skill_prefix(plugin, subject)
        for rel, data in skill.items():
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            (target / rel).write_bytes(data)
        evals = scratch / "evals" / subject
        for name, files in fixtures.items():
            for rel, data in files.items():
                (evals / name / rel).parent.mkdir(parents=True, exist_ok=True)
                (evals / name / rel).write_bytes(data)
        runs = scratch / "runs"
        results.mkdir(parents=True, exist_ok=True)
        timestamp = run_id[:16]
        for name in sorted(fixtures):
            code = runner.run_eval(run_eval_argv(
                evals, name, trials, archived, runs, timestamp, run_id,
                [staging.resolve(), results.resolve()]))
            unit = runs / subject / timestamp / name
            record["fixtures"][name] = {
                "run_eval_exit": code,
                "with_skill": arm_metrics(unit / "with_skill", trials),
                "without_skill": arm_metrics(unit / "without_skill", trials)}

    decision, reasons = decide(record["fixtures"])
    if decision != "accepted":
        return finish(reasons)
    record["decision"], record["reasons"] = "accepted", reasons
    report = skill_report(record)
    if text_problem(report):
        record["decision"] = "rejected"
        return finish(["the report would carry a closing keyword, an @mention "
                       "or a code fence (check the triage reason)"])
    out.mkdir(parents=True)
    write_json(out / "summary.json", record)
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "skill.patch").write_text(new_files_patch(skill_prefix(plugin, subject),
                                                     skill), encoding="utf-8")
    for name, files in fixtures.items():
        for rel, data in files.items():
            path = results / "evals" / subject / name / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return 0


# ---------------------------------------------------------------------------
# validate (the gate).

def _arm_record(arm, where: str) -> None:
    if arm is None:
        return
    if not isinstance(arm, dict) or set(arm) != set(ARM_KEYS):
        raise Rejected(f"{where}: an arm is not exactly {list(ARM_KEYS)}")
    for key in ("n", "errors", "passed", "total"):
        ingest._integer(arm[key], f"{where}: {key}", 0, 10 ** 6)
    for key in ("pass_rate", "tokens"):
        ingest._number(arm[key], f"{where}: {key}", 0, 10 ** 12, null=True)


def check_record(doc, run_id: str) -> dict:
    """A branch's summary.json, any of the three decisions."""
    where = "summary.json"
    if not isinstance(doc, dict):
        raise Rejected(f"{where}: not an object")
    improve_gate.bounded(doc, where)
    decision = doc.get("decision")
    if decision not in DECISIONS:
        raise Rejected(f"{where}: decision is not one of {list(DECISIONS)}")
    keys = HOOK_RECORD_KEYS if decision == "hook-first" else SKILL_RECORD_KEYS
    if set(doc) != set(keys):
        raise Rejected(f"{where}: not exactly the {decision} record's keys")
    if doc["schema"] != 1 or type(doc["schema"]) is not int:
        raise Rejected(f"{where}: unknown schema")
    if doc["run_id"] != run_id:
        raise Rejected(f"{where}: run_id is not the branch's")
    if not isinstance(doc["subject"], str) or not SUBJECT_RE.fullmatch(doc["subject"]):
        raise Rejected(f"{where}: bad subject")
    if not value_ok(PROPOSAL_SCHEMA["evidence_count"], doc["evidence_count"]):
        raise Rejected(f"{where}: bad evidence_count")
    if decision == "hook-first":
        try:
            proposal = check_payload_proposal(doc["proposal"])
        except Rejected:
            raise Rejected(f"{where}: proposal fails the payload schema") from None
        if proposal["subject"] != doc["subject"] \
                or proposal["evidence_count"] != doc["evidence_count"]:
            raise Rejected(f"{where}: proposal does not match the record")
        check_sketch(doc["check_sketch"])
        return doc
    check_triage(doc["triage"])
    if decision == "accepted" and doc["plugin"] not in PLUGINS:
        raise Rejected(f"{where}: plugin is not one of {list(PLUGINS)}")
    if doc["plugin"] is not None and doc["plugin"] not in PLUGINS:
        raise Rejected(f"{where}: plugin is not one of {list(PLUGINS)}")
    if doc["registry"] != REGISTRY_URL:
        raise Rejected(f"{where}: registry is not {REGISTRY_NAME}")
    if not isinstance(doc["registry_sha"], str) \
            or not ingest.SHA_RE.fullmatch(doc["registry_sha"]):
        raise Rejected(f"{where}: registry_sha is not a 40-hex commit id")
    if doc["skill_creator_commit"] != SKILL_CREATOR_COMMIT \
            or doc["tokens_basis"] != TOKENS_BASIS:
        raise Rejected(f"{where}: not the pinned skill-creator or token basis")
    ingest._integer(doc["trials"], f"{where}: trials", 1, MAX_TRIALS)
    reasons = doc["reasons"]
    if not isinstance(reasons, list) or not reasons \
            or not all(text_ok(r, 2000) for r in reasons):
        raise Rejected(f"{where}: reasons must be a list of strings")
    fixtures = doc["fixtures"]
    if not isinstance(fixtures, dict):
        raise Rejected(f"{where}: fixtures is not an object")
    for name, row in fixtures.items():
        if not FIXTURE_NAME_RE.fullmatch(name) or not isinstance(row, dict) \
                or set(row) != {"run_eval_exit", "with_skill", "without_skill"}:
            raise Rejected(f"{where}: a fixture row is malformed")
        ingest._integer(row["run_eval_exit"], f"{where}: run_eval_exit", -255, 255)
        _arm_record(row["with_skill"], where)
        _arm_record(row["without_skill"], where)
    if decision == "accepted" and decide(fixtures)[0] != "accepted":
        raise Rejected(f"{where}: accepted, but its own figures do not pass "
                       "the decision rule")
    return doc


def _read_blob(repo: str, oid: str, cap: int) -> bytes:
    size = int(ingest.git(repo, "cat-file", "-s", oid).decode().strip())
    if size > cap:
        raise Rejected("a file is over its size cap")
    data = ingest.git(repo, "cat-file", "blob", oid)
    if len(data) != size:
        raise Rejected("a file changed size while reading")
    return data


def validate(repo: str, base: str, source: str, branch: str,
             expect_sha: str | None, registry: Path | None, out: Path) -> dict:
    match = BRANCH_RE.fullmatch(branch)
    if not match:
        raise Rejected(f"branch name is not {BRANCH_SHAPE}")
    run_id = match.group("run_id")
    tip = ingest.resolve_commit(repo, source)
    if expect_sha is not None:
        if not ingest.SHA_RE.fullmatch(expect_sha):
            raise Rejected("expected sha is not a 40-hex commit id")
        if tip != expect_sha:
            raise Rejected("the branch moved since the push that triggered "
                           "this run; that newer push triggers its own gate")
    base_sha = ingest.resolve_commit(repo, base)
    merge_base = ingest.git(repo, "merge-base", base_sha, tip).decode().strip()
    if not ingest.SHA_RE.fullmatch(merge_base):
        raise Rejected("the branch shares no history with the default branch")
    try:
        added = ingest.added_files(repo, merge_base, tip)
    except Rejected:
        raise Rejected("the branch does something other than add regular, "
                       "non-executable files") from None
    if len(added) > len(SIZE_CAPS) + FIXTURE_MAX_FILES:
        raise Rejected("the branch adds too many files")

    prefix = f"{SOURCE_ROOT}/{run_id}/"
    own, others = {}, []
    for name, _, oid in added:
        if name.startswith(prefix) and name[len(prefix):] in SIZE_CAPS:
            kind = name[len(prefix):]
            own[kind] = _read_blob(repo, oid, SIZE_CAPS[kind])
        else:
            others.append((name, oid))
    if "summary.json" not in own:
        raise Rejected(f"the branch adds no {prefix}summary.json")
    doc = check_record(ingest.load_json(
        ingest.decode_text(own["summary.json"], "summary.json"), "summary.json"), run_id)
    decision, subject = doc["decision"], doc["subject"]
    expected = {"accepted": {"summary.json", "report.md", "skill.patch"},
                "rejected": {"summary.json"},
                "hook-first": {"summary.json", "report.md"}}[decision]
    if set(own) != expected or (decision != "accepted" and others):
        raise Rejected(f"a {decision} record's branch adds files other than "
                       f"{sorted(expected)}")

    fixtures: dict[str, dict[str, bytes]] = {}
    total = 0
    for name, oid in others:
        head = f"evals/{subject}/"
        rest = name[len(head):] if name.startswith(head) else ""
        fixture, _, rel = rest.partition("/")
        if not rel:
            raise Rejected(f"a file is outside {prefix} and the subject's fixtures")
        data = _read_blob(repo, oid, FIXTURE_FILE_CAP)
        total += len(data)
        if total > FIXTURE_TOTAL_CAP:
            raise Rejected("the fixtures are over the total size cap")
        fixtures.setdefault(fixture, {})[rel] = data

    if "report.md" in own:
        report = ingest.decode_text(own["report.md"], "report.md")
        title = (HOOK_TITLE if decision == "hook-first" else SKILL_TITLE)
        if report.split("\n", 1)[0] != title.format(subject=subject):
            raise Rejected("report.md does not open with its title for this subject")
        problem = text_problem(report)
        if problem:
            raise Rejected(f"report.md {problem}")

    if decision == "accepted":
        problems = fixtures_problems(fixtures, subject)
        if problems:
            raise Rejected("; ".join(problems[:5]))
        if set(fixtures) != set(doc["fixtures"]):
            raise Rejected("the branch's fixtures are not the ones the record measured")
        plugin = doc["plugin"]
        skill = parse_new_files_patch(
            ingest.decode_text(own["skill.patch"], "skill.patch"),
            skill_prefix(plugin, subject))
        problems = skill_problems(skill, subject)
        if problems:
            raise Rejected("; ".join(problems[:5]))
        if registry is None or not registry.is_dir():
            raise Rejected(f"an accepted record needs the {REGISTRY_NAME} checkout")
        if skill_exists(registry, subject) or any(
                registry.glob(f"plugins/*/skills/{subject}")):
            raise Rejected(f"the subject already exists in {REGISTRY_NAME}")
        if ingest.git(repo, "ls-tree", "-z", base_sha, "--", f"evals/{subject}"):
            raise Rejected("the subject already has evals/<subject>/ on the "
                           "default branch")

    if out.exists() and any(out.iterdir()):
        raise Rejected("the staging directory is not empty")
    out.mkdir(parents=True, exist_ok=True)
    for kind, data in own.items():
        (out / kind).write_bytes(data)
    for fixture, files in fixtures.items():
        for rel, data in files.items():
            path = out / "evals" / subject / fixture / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return {"decision": decision, "subject": subject,
            "plugin": doc.get("plugin") or "", "run_id": run_id, "sha": tip}


# ---------------------------------------------------------------------------
# apply / fixtures-apply (the registry's own required additions).

def staged_record(staged: Path, decision: str | None = None) -> dict:
    doc = read_json_file(staged / "summary.json", SIZE_CAPS["summary.json"],
                         "summary.json")
    run_id = doc.get("run_id") if isinstance(doc, dict) else None
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise Rejected("summary.json: bad run_id")
    doc = check_record(doc, run_id)
    if decision is not None and doc["decision"] != decision:
        raise Rejected(f"summary.json: not a {decision} record")
    return doc


def _no_symlink_on(tree: Path, rel: str) -> None:
    cursor = tree
    for part in Path(rel).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise Rejected("a destination path has a symlink")


def bump_patch_version(path: Path) -> tuple[str, str]:
    """Raise a manifest's `x.y.z` version to `x.y.(z+1)`, changing that one
    token; the JSON is parsed before and after to prove nothing else moved."""
    text = path.read_text(encoding="utf-8")
    before = json.loads(text)
    old = before.get("version")
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)", old or "")
    token = f'"version": "{old}"'
    if not match or text.count(token) != 1:
        raise Rejected("a plugin manifest has no single x.y.z version")
    new = f"{match.group(1)}.{match.group(2)}.{int(match.group(3)) + 1}"
    text = text.replace(token, f'"version": "{new}"')
    after = json.loads(text)
    if {**before, "version": new} != after:
        raise Rejected("bumping the version changed more than the version")
    path.write_text(text, encoding="utf-8")
    return old, new


def impact_entry(record: dict, versions: tuple[str, str]) -> str:
    rows = record["fixtures"].values()
    with_p = sum(r["with_skill"]["passed"] for r in rows)
    with_t = sum(r["with_skill"]["total"] for r in rows)
    without_p = sum(r["without_skill"]["passed"] for r in rows)
    without_t = sum(r["without_skill"]["total"] for r in rows)
    run_id = record["run_id"]
    day = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"
    return "\n".join([
        f"## {day} — {record['plugin']}/{record['subject']} — create", "",
        "- Motivation: a `new-skill` lesson mined from agent sessions "
        f"(redacted, {record['evidence_count']} pieces of evidence) that the "
        "draft loop's triage could not check mechanically.",
        f"- Change: new skill `{skill_prefix(record['plugin'], record['subject'])}/`, "
        "drafted by the skills-evals draft loop with skill-creator at "
        f"`{SKILL_CREATOR_COMMIT[:12]}`; bundle `{record['plugin']}` "
        f"{versions[0]} -> {versions[1]} (PR pending).",
        f"- Eval: draft-loop measurement, skills-evals routine run `{run_id}`, "
        f"{record['trials']} trials per arm over {len(record['fixtures'])} new "
        f"fixtures: with_skill objective {with_p}/{with_t}, without_skill "
        f"{without_p}/{without_t}. Not badge input; the fixtures land in a "
        "companion skills-evals PR.",
        "- Outcome: pending review.", ""])


def insert_impact(path: Path, entry: str) -> None:
    text = path.read_text(encoding="utf-8")
    try:
        idx = text.index("\n---\n", text.index("## Entry format"))
    except ValueError:
        raise Rejected("docs/skill-impact.md has no entry separator") from None
    head, tail = text[:idx + 5], text[idx + 5:].lstrip("\n")
    path.write_text(f"{head}\n{entry}\n{tail}", encoding="utf-8")


def regenerate_readme(tree: Path) -> None:
    """The registry's own README table generator, loaded from the checkout
    (its default branch: trusted code, the same file its CI runs)."""
    script = tree / "scripts" / "generate_readme_table.py"
    if script.is_symlink() or not script.is_file():
        raise Rejected("the registry has no scripts/generate_readme_table.py")
    spec = importlib.util.spec_from_file_location("registry_readme_table", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.render(False) not in (0, None):
        raise Rejected("the registry's README table generator failed")


def apply(staged: Path, tree: Path) -> dict:
    """In an acsi-agentskills checkout: the patch, as data, then what the
    registry requires of a new skill (its pull-request template and CI):
    the bundle version bumped in both manifests (check_plugin_versions.py),
    the README skill table regenerated (generate_readme_table.py --check),
    and a `docs/skill-impact.md` entry. `relock=true` means the bundle is in
    the registry's own skills.lock: commit, then re-pin the lock on that
    commit (`generate_skills_lock.py --repin`) and commit it, or
    `--check-current` fails."""
    record = staged_record(staged, "accepted")
    plugin, subject = record["plugin"], record["subject"]
    prefix = skill_prefix(plugin, subject)
    patch_path = staged / "skill.patch"
    added = parse_new_files_patch(
        read_text(patch_path, SIZE_CAPS["skill.patch"]), prefix)
    _no_symlink_on(tree, prefix)
    if (tree / prefix).exists() or not (tree / "plugins" / plugin / "skills").is_dir():
        raise Rejected("the skill already exists, or its plugin is missing")
    for args in (("apply", "--check", str(patch_path.resolve())),
                 ("apply", str(patch_path.resolve()))):
        # Raw git is fine here (#343): the gate's own registry checkout, never
        # an agent-touched workspace, so harness/workspace_git.py does not apply.
        result = subprocess.run(["git", "-C", str(tree), *args],
                                capture_output=True, check=False)
        if result.returncode != 0:
            raise Rejected("the patch does not apply to the registry checkout")
    status = ingest.git(str(tree), "status", "--porcelain=v1", "-z",
                        "--untracked-files=all").decode("utf-8").split("\0")[:-1]
    if sorted(status) != sorted(f"?? {prefix}/{rel}" for rel in added):
        raise Rejected("the patch changed more than the new skill's files")
    versions = None
    for manifest in ("plugin.json", ".claude-plugin/plugin.json"):
        _no_symlink_on(tree, f"plugins/{plugin}/{manifest}")
        moved = bump_patch_version(tree / "plugins" / plugin / manifest)
        if versions is not None and moved != versions:
            raise Rejected("the two plugin manifests disagree on the version")
        versions = moved
    _no_symlink_on(tree, "docs/skill-impact.md")
    insert_impact(tree / "docs" / "skill-impact.md", impact_entry(record, versions))
    _no_symlink_on(tree, "README.md")
    regenerate_readme(tree)
    try:
        lock = json.loads((tree / "skills.lock").read_text(encoding="utf-8"))
        relock = plugin in lock.get("bundles", [])
    except (OSError, ValueError, AttributeError):
        relock = False
    return {"subject": subject, "plugin": plugin, "version": versions[1],
            "relock": "true" if relock else "false"}


def read_text(path: Path, cap: int) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > cap:
        raise Rejected("a staged file is missing or over its cap")
    return ingest.decode_text(path.read_bytes(), "staged file")


def layout_line(subject: str, comment: str) -> str:
    return f"  {subject}/".ljust(27) + f"# {comment}"


def fixtures_apply(staged: Path, tree: Path) -> dict:
    """In a skills-evals checkout: the staged fixtures at evals/<subject>/,
    and the README's layout entry the suite requires of every eval directory
    (test_every_eval_directory_is_named_in_the_readmes_layout)."""
    record = staged_record(staged, "accepted")
    subject = record["subject"]
    source = staged / "evals" / subject
    files = read_tree(source)
    by_fixture: dict[str, dict[str, bytes]] = {}
    for rel, data in files.items():
        name, _, rest = rel.partition("/")
        by_fixture.setdefault(name, {})[rest] = data
    problems = fixtures_problems(by_fixture, subject)
    if problems or set(by_fixture) != set(record["fixtures"]):
        raise Rejected("the staged fixtures are not the record's valid fixtures")
    _no_symlink_on(tree, f"evals/{subject}")
    if (tree / "evals" / subject).exists():
        raise Rejected("evals/<subject>/ already exists")
    _no_symlink_on(tree, "README.md")
    readme = tree / "README.md"
    text = readme.read_text(encoding="utf-8")
    anchor = "\n  real-work/"
    blocks = text.split("```")
    index = next((i for i, b in enumerate(blocks)
                  if "evals/" in b and "harness/" in b), None)
    if index is None or anchor not in blocks[index]:
        raise Rejected("README.md has no layout block with a real-work/ entry")
    entry = [layout_line(subject, f"A/B eval drafted from a lesson: "
                         f"{len(by_fixture)} fixtures")]
    entry += [f"    {name}/" for name in sorted(by_fixture)]
    blocks[index] = blocks[index].replace(anchor, "\n" + "\n".join(entry) + anchor, 1)
    for rel, data in files.items():
        path = tree / "evals" / subject / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    readme.write_text("```".join(blocks), encoding="utf-8")
    return {"subject": subject, "files": str(len(files) + 1)}


# ---------------------------------------------------------------------------
# Bodies.

def _provenance(run_id: str, sha: str, branch: str) -> str:
    return (f"run `{run_id}`: the eval routine pushed "
            f"[`{branch}`]({EVALS_TREE.format(sha=sha)}) at `{sha}`, and "
            "`routine-draft-skill-gate.yml` validated it without running it")


def pr_body(staged: Path, run_id: str, sha: str, branch: str, repo: str,
            mode: str = "draft") -> str:
    """`mode` is the gate's merge mode (improve_gate.effective_mode): in
    `auto` both pull requests are ready with auto-merge enabled, the skill's
    first; in `draft` a person merges them."""
    record = staged_record(staged, "accepted")
    report = read_text(staged / "report.md", SIZE_CAPS["report.md"]).rstrip("\n")
    subject = record["subject"]
    if repo == REGISTRY_NAME:
        head = [
            f"A new skill drafted by the skills-evals draft loop, "
            f"{_provenance(run_id, sha, branch)}; `draft_new_skill.py apply` "
            "applied its `skill.patch` here and made the registry's own "
            "additions (bundle version, README table, `docs/skill-impact.md`).",
            "",
            ("This pull request merges itself (merge commit) once the "
             "required checks pass, with no human review (acsi-agentskills "
             f"[ADR 0021]({improve_gate.REGISTRY_ADR}); the owner, 2026-10-26). "
             if mode == "auto" else
             "A person reviews and merges this, or closes it; nothing merges "
             "itself. ")
            + "Its fixtures are in a companion skills-evals pull "
            "request; merge this one first. The numbers below are a routine "
            "measurement on those fixtures, not badge input.", "",
            "## Checklist", "", TEMPLATE_CHECKLIST]
    else:
        head = [
            f"Eval fixtures for the drafted skill `{subject}`, from the draft "
            f"loop's {_provenance(run_id, sha, branch)}.", "",
            "Merge this after the companion acsi-agentskills pull request "
            f"(branch `{TARGET_BRANCH.format(subject=subject)}`) adds the "
            "skill. Until then these fixtures name a skill the registry does "
            "not have: an eval dispatch fails `skill_not_found` and the "
            "coverage census reports an `orphan_fixture`."]
        if mode == "auto":
            head += [
                "", "Auto-merge (merge commit) is enabled, with no human "
                "review. This pull request was opened with the job token, "
                "which starts no workflow, so its required checks start only "
                "when someone pushes to it or runs them, after the skill's "
                "pull request has merged."]
    return "\n".join([*head, "", "---", "", report, "", MARKER, ""])


def _escape_json(text: str) -> str:
    """JSON that decodes to the same value but cannot @mention a person or
    cross-reference an issue from a GitHub body."""
    return text.replace("@", "\\u0040").replace("#", "\\u0023")


def issue_body(staged: Path, run_id: str, sha: str, branch: str) -> str:
    record = staged_record(staged, "hook-first")
    report = read_text(staged / "report.md", SIZE_CAPS["report.md"]).rstrip("\n")
    proposal = record["proposal"]
    block = {"schema_version": 1, "id": proposal["id"],
             "subject": record["subject"], "kind": "agent-check",
             "evidence_count": record["evidence_count"],
             "examples": proposal["examples"],
             "check_sketch": record["check_sketch"]}
    return "\n".join([
        f"A hook proposal from the skills-evals draft loop, "
        f"{_provenance(run_id, sha, branch)}. For the agent-checks lane: "
        "close it as not planned if the lesson cannot be checked at commit "
        "time, and the draft loop will draft it as a skill instead.", "",
        report, "", "```json",
        _escape_json(json.dumps(block, indent=2, ensure_ascii=False)),
        "```", "", MARKER, ""])


def check_body_args(run_id: str, sha: str, branch: str) -> None:
    match = BRANCH_RE.fullmatch(branch)
    if not match or not ingest.SHA_RE.fullmatch(sha) or match.group("run_id") != run_id:
        raise Rejected("bodies take only validated values")


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("select")
    p.add_argument("--lessons", required=True, type=Path)
    p.add_argument("--registry", action="append", required=True)
    p.add_argument("--evals", required=True, type=Path)
    p.add_argument("--drafted", required=True, type=Path)
    p.add_argument("--hook-declined", type=Path, default=None)
    p = sub.add_parser("drafted-subjects")
    p.add_argument("--repo", required=True)
    p = sub.add_parser("payload")
    p.add_argument("--lessons", required=True, type=Path)
    p.add_argument("--proposal", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--hook-declined", action="store_true")
    p = sub.add_parser("fire-check")
    p.add_argument("--payload", required=True, type=Path)
    p = sub.add_parser("hook-first")
    p.add_argument("--payload", required=True, type=Path)
    p.add_argument("--proposal-json", required=True, type=Path)
    p.add_argument("--results", required=True, type=Path)
    p = sub.add_parser("measure")
    p.add_argument("--staging", required=True, type=Path)
    p.add_argument("--registry", required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--payload", required=True, type=Path)
    p.add_argument("--trials", required=True, type=int)
    p.add_argument("--results", required=True, type=Path)
    p = sub.add_parser("resolve")
    p.add_argument("--event-name", required=True)
    p.add_argument("--event-path", required=True)
    p = sub.add_parser("validate")
    p.add_argument("--repo", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--expect-sha", default="")
    p.add_argument("--registry", default=None)
    p.add_argument("--out", required=True, type=Path)
    for name in ("apply", "fixtures-apply"):
        p = sub.add_parser(name)
        p.add_argument("--staged", required=True, type=Path)
        p.add_argument("--tree", required=True, type=Path)
    for name in ("pr-body", "issue-body"):
        p = sub.add_parser(name)
        p.add_argument("--staged", required=True, type=Path)
        p.add_argument("--run-id", required=True)
        p.add_argument("--sha", required=True)
        p.add_argument("--branch", required=True)
        if name == "pr-body":
            p.add_argument("--repo", required=True,
                           choices=(REGISTRY_NAME, "skills-evals"))
            p.add_argument("--mode", choices=improve_gate.MERGE_MODES,
                           default="draft")
    args = parser.parse_args(argv)
    try:
        if args.command == "measure":
            return measure(args.staging, args.registry, args.ref, args.payload,
                           args.trials, args.results, runner or Runner())
        if args.command == "fire-check":
            load_payload(args.payload)
            return 0
        if args.command == "drafted-subjects":
            for line in drafted_subjects(args.repo):
                print(line)
            return 0
        if args.command == "payload":
            sys.stdout.write(payload(args.lessons, args.proposal, args.run_id,
                                     args.hook_declined).decode("utf-8") + "\n")
            return 0
        if args.command in ("pr-body", "issue-body"):
            check_body_args(args.run_id, args.sha, args.branch)
            sys.stdout.write(
                pr_body(args.staged, args.run_id, args.sha, args.branch, args.repo,
                        args.mode)
                if args.command == "pr-body"
                else issue_body(args.staged, args.run_id, args.sha, args.branch))
            return 0
        if args.command == "select":
            result = select(args.lessons, parse_registries(args.registry),
                            args.evals, args.drafted, args.hook_declined)
        elif args.command == "hook-first":
            result = hook_first(args.payload, args.proposal_json, args.results)
        elif args.command == "resolve":
            result = ingest.resolve(args.event_name, args.event_path,
                                    BRANCH_RE, BRANCH_SHAPE)
        elif args.command == "validate":
            registries = parse_registries([args.registry] if args.registry else [])
            if set(registries) - {REGISTRY_NAME}:
                raise Refused(f"--registry must name {REGISTRY_NAME}")
            result = validate(args.repo, args.base, args.source, args.branch,
                              args.expect_sha or None,
                              registries.get(REGISTRY_NAME), args.out)
        elif args.command == "apply":
            result = apply(args.staged, args.tree)
        else:
            result = fixtures_apply(args.staged, args.tree)
    except Refused as exc:
        print(f"refused: {_printable(exc)}", file=sys.stderr)
        return 2
    except Rejected as exc:
        print(f"rejected: {_printable(exc)}", file=sys.stderr)
        return 1
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


def _printable(exc: Exception) -> str:
    """The backstop: no message can start a new log line."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in str(exc))


if __name__ == "__main__":
    sys.exit(main())
