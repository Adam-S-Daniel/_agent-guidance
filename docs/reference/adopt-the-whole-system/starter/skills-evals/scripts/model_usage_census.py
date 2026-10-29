#!/usr/bin/env python3
"""Count which Claude models this account actually used, per ISO week.

*** THE OUTPUT OF THIS SCRIPT IS PUBLISHED PUBLICLY. ***

It is committed to the `persistent/eval-results` branch of a PUBLIC repository as
`usage/latest.json` by `scripts/publish_usage_census.sh` (see
`evals/usage/CENSUS.md`). Treat every byte it emits as world-readable,
forever.

That is why the output is `{model_id: {iso_week: count}}` and NOTHING else. It
carries no project names, no directory names, no filesystem paths, no prompt
or reply text, no session ids, no timestamps finer than an ISO week. The
transcripts it reads are full of all of those — `~/.claude/projects/` encodes
the project path in the directory name alone — so the narrowness here is the
whole safety property, not tidiness.

AND THE KEYS ARE NOT TRUSTED EITHER. `message.model` is whatever the routing
layer wrote into the transcript, and it is not always a model id: a Bedrock
inference-profile ARN carries an AWS account number, a Vertex path carries a
GCP project id, a local proxy can put a filesystem path or free prose there.
Copying that verbatim into a top-level key published every one of them. Only
values matching `MODEL_ID_RE` become keys; everything else is counted under
the single key `other`, so the raw turn count this script publishes stays
honest — nothing is silently dropped — without any of those strings crossing
the boundary. The roster's own usage_share() then EXCLUDES `other`, and any
key it cannot rank, from what it divides by: this file's contract is a
truthful count of turns seen, not a truthful denominator for a model's
share, which is roster.py's decision to make. Nothing is ever passed
through `str()` — `str()` of a dict is `repr()`, which publishes its values.
`test/run_tests.py::TestIssue67::test_census_emits_only_model_week_counts_and
_leaks_nothing` and its review-round sibling in `TestIssue67Review` are the
guards on that, and they stay.

Runs on a durable machine (the transcripts are local, and a CI runner has
none). Weekly buckets, not daily: a daily series over one account is a
timeline of when a person was at their desk, and the roster policy only ever
asks about 4- and 8-week windows anyway.

Subagent transcripts are counted, on purpose: a subagent turn is a real API
turn against a real model, it is billed, and the roster's question is "what
does this account run", not "what did a human type at".

`generated_at` is when this script RAN, not when the usage happened — the
usage is in the week buckets. The roster's freshness check reads it as
"how long ago did someone last publish a census", which is the question it
should be asking of a publication timestamp.

NOT stdlib-only: classifying a `message.model` value against the tier ladder
needs `harness/roster.py`'s `tier_words()`/`load_policy()`, and roster.py
itself needs PyYAML. That import is LAZY (see `_require_model_id_re()`), so
importing this module and running `--help` work with no PyYAML installed —
only building an actual census does, and a machine that lacks it gets one
named line on stderr and exit 2, never an ImportError traceback. See
`evals/usage/CENSUS.md` for installing it on the durable machine that runs
this script.

SKILL USAGE rides the same walk when `--weekly-out` is given: how often each
skill was invoked, per ISO week, published beside the model census as
`usage/weekly-local.json` (schema: `harness/skill_usage.py`, the one place it
is defined). The same contract holds, and the keys are just as
untrusted: a `Skill` tool call's `input.skill` and a typed
`<command-name>` are free strings, so a name becomes a key ONLY when it is
in `skill_allowlist()` — skills that already appear by name in this public
repo, as a fixture or a non-coverage row for a non-private registry — and
matches `SKILL_KEY_RE`. Every other Skill tool call counts under `other`; a
typed slash command that is not an allowlisted skill (`/clear`, `/model`)
is not a skill invocation and is not counted at all.

HOOK RUNS from the fleet's git hook runner (agent-checks writes one JSON line
per local run, `--hook-logs`) become `hook` rows in the same weekly file:
per check id, per allowlisted public repository, runs and failures. A check
id is published only when shaped like one, a repository only when it is
public (`public_repos()`); everything else is `other`, and an `all` row per
week counts every run, so the totals do not depend on either allowlist.

PRIVATE EVENTS are the exception, and the only one: `--events-out` writes
one JSON line per skill invocation, with its timestamp, session id and title,
repository and branch. That file is NEVER published publicly:
`scripts/publish_usage_census.sh` commits it only to the private
`persistent/usage-events` branch of agentskills-private (see
`evals/usage/CENSUS.md`, "Private usage events"). Nothing from it reaches
the two public files.

Usage:
    python3 scripts/model_usage_census.py --out usage/latest.json \
        [--weekly-out usage/weekly-local.json] [--events-out events.jsonl] \
        [--hook-logs PATH[:PATH...]]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "harness"))
# Appended, not inserted: eval_coverage (for the skill allowlist) is the only
# module wanted from scripts/, and nothing there may shadow anything else.
sys.path.append(str(REPO_ROOT / "scripts"))
from timeweeks import iso_week, parse_ts, window_start, window_weeks  # noqa: E402
import skill_usage  # noqa: E402

DEFAULT_PROJECTS = Path.home() / ".claude" / "projects"
DEFAULT_WEEKS = 8
POLICY_PATH = REPO_ROOT / "evals" / "roster-policy.yml"

#: The one message this script prints for a missing PyYAML, everywhere it can
#: be hit — main()'s guard and the test that shims the import away both key
#: off this exact string, so it can never drift out of step with itself.
PYYAML_MISSING_MESSAGE = ("model_usage_census needs PyYAML: "
                          "python3 -m pip install --user pyyaml")


def _model_id_re() -> re.Pattern:
    """The shape of a model id, and the ONLY shape that becomes a published
    key. Being lowercase-and-dashes is necessary but not sufficient — a
    proxy alias or a path-shaped value can be shaped exactly like an id
    (measured: `claude-home-user-secret-client-northrop-merger`). It must
    also carry, as one of its dash-separated tokens, a family word from the
    tier ladder in evals/roster-policy.yml (read via `roster.tier_words`,
    never hardcoded here), and it is capped at the length a real id needs —
    about 40 — so a long adjacent string cannot ride in just because it
    happens to contain a family word somewhere in it.

    `import roster` is LOCAL to this function, not at module level: it is
    the one place this script needs PyYAML (roster.py's own `import yaml`,
    to read the tier ladder) — the alternative, hand-rolling family words
    here, is exactly what NO MODEL IDS/FAMILY WORDS LIVE OUTSIDE
    roster-policy.yml exists to prevent. Deferring the import means a
    machine with no PyYAML installed can still import this module and reach
    `--help`/argument errors; only actually building a census needs it, and
    `main()` turns that ImportError into one named line, never a traceback.
    """
    import roster
    words = "|".join(re.escape(w) for w in
                     roster.tier_words(roster.load_policy(POLICY_PATH)))
    # `\Z`, not `$`: `$` matches just before a trailing newline too, so an
    # otherwise honest id with one appended would satisfy this pattern and
    # be published as its own (distinct, newline-carrying) key rather than
    # falling to `other` (see test/run_tests.py's HOSTILE_MODELS).
    return re.compile(
        rf"^(?=.{{1,40}}\Z)claude(?:-[a-z0-9.]{{1,20}})*-(?:{words})"
        rf"(?:-[a-z0-9.]{{1,20}})*\Z")


#: Populated by `_require_model_id_re()` on first use — never at import time.
MODEL_ID_RE: re.Pattern | None = None


def _require_model_id_re() -> re.Pattern:
    """The module-level MODEL_ID_RE, computed on first call. Raises
    ImportError, uncaught, if PyYAML (via roster.py) is not installed —
    callers that can reach a user (main()) must catch it themselves and
    print PYYAML_MISSING_MESSAGE; callers in a test that already has PyYAML
    installed never see it raise at all.
    """
    global MODEL_ID_RE
    if MODEL_ID_RE is None:
        MODEL_ID_RE = _model_id_re()
    return MODEL_ID_RE


#: Where everything else is counted. One key, no detail — the count is the
#: only part of a non-conforming value that is safe to publish.
OTHER_KEY = "other"


def published_key(model) -> str | None:
    """The key this `message.model` value may appear under, or None to skip.

    None means "this entry names no model" (absent or empty). Anything else
    that is not a model-id-shaped string — a dict, a list, an int, an ARN,
    a path, prose — is counted, but under `OTHER_KEY`.
    """
    if model is None or model == "":
        return None
    if isinstance(model, str) and _require_model_id_re().match(model):
        return model
    return OTHER_KEY


def _turn_key(entry: dict, message: dict):
    """What identifies one API turn across the several entries it writes.

    A single assistant turn is written as several JSONL entries — a thinking
    block, a text block, one per tool call — all sharing `message.id`. Counting
    entries measured tool density, not turns (2.5x inflation on an agentic
    transcript), and the roster's 10% and 2% bars were being decided on it.
    `requestId` is the fallback for older transcripts; an entry with neither is
    counted on its own, because there is nothing to fold it into.
    """
    for candidate in (message.get("id"), entry.get("requestId")):
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


#: The shape of a published skill key: a skill directory basename, lowercase.
#: Necessary even for an allowlisted name — the allowlist is read from files a
#: pull request can change, and this is the last check before a public key.
SKILL_KEY_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

#: A typed slash command, as Claude Code records it in the user entry.
_COMMAND_NAME_RE = re.compile(r"<command-name>([^<]{0,200})</command-name>")


def skill_allowlist(root: Path = REPO_ROOT) -> frozenset[str]:
    """The skill names that may become published keys.

    A name qualifies when this public repo already carries it: a skill
    fixture `scripts/eval_coverage.py` counts under `root/evals`, or a row
    of `root/evals/non-coverage.yml` for a registry that is not private.
    Both are read by eval_coverage's own `count_fixtures`/`load_skips`, so
    "is a fixture" means the same here as in the coverage census. Raises
    `eval_coverage.CensusRefusal` or ValueError when either cannot be read;
    the caller fails closed. Needs PyYAML, so the import is lazy.
    """
    import eval_coverage
    import run_eval
    registries = {entry["name"]: entry
                  for entry in run_eval._load_registries_config()}
    fixtures = eval_coverage.count_fixtures(Path(root) / "evals", registries)
    skips: dict = {}
    eval_coverage.load_skips(Path(root) / "evals" / "non-coverage.yml",
                             registries, private=False, into=skips)
    names = {skill for _registry, skill in fixtures}
    names |= {skill for registry, skill in skips
              if registry not in eval_coverage.PRIVATE_REGISTRIES}
    return frozenset(name for name in names if SKILL_KEY_RE.fullmatch(name))


def _skill_name(raw) -> str | None:
    """`/coding-anywhere:workflow-path-audit` -> `workflow-path-audit`."""
    if not isinstance(raw, str):
        return None
    name = raw[1:] if raw.startswith("/") else raw
    return name.rsplit(":", 1)[-1]


def skill_key(raw, allowlist: frozenset[str]) -> str:
    """The key a Skill tool call's `input.skill` is counted under."""
    name = _skill_name(raw)
    if name in allowlist and SKILL_KEY_RE.fullmatch(name):
        return name
    return OTHER_KEY


def _typed_command(message: dict) -> str | None:
    """The slash command a user entry typed, as written, or None.

    Only the message's own text counts (a string, or `text` items): a
    `tool_result` can quote a transcript, and its tags were not typed here.
    The first text that carries a command decides.
    """
    content = message.get("content")
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        texts = [item["text"] for item in content
                 if isinstance(item, dict) and item.get("type") == "text"
                 and isinstance(item.get("text"), str)]
    else:
        return None
    for text in texts:
        match = _COMMAND_NAME_RE.search(text)
        if match:
            return match.group(1)
    return None


def _typed_skill(message: dict, allowlist: frozenset[str]) -> str | None:
    """The allowlisted skill a user entry's slash command names, or None."""
    name = _skill_name(_typed_command(message))
    if name in allowlist and SKILL_KEY_RE.fullmatch(name):
        return name
    return None


# --- Private per-invocation records (`--events-out`) -----------------------
# These go ONLY to the private repository. They still get bounds: every free
# string is capped, and a repository is published as `owner/name` only when it
# has that shape, so a remote URL carrying a token never reaches the record.

#: Longest kept value of a free string in a private record.
EVENT_TEXT_CAP = 200
#: A host name that may name a file in the private store.
MACHINE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_GITHUB_REMOTE_RE = re.compile(
    r"(?:https://(?:[^@/\s]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)"
    r"([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?")


def machine_name() -> str:
    """This host's name, or `unknown` when it is not shaped like one."""
    name = socket.gethostname()
    return name if isinstance(name, str) and MACHINE_RE.fullmatch(name) else "unknown"


def _text(value) -> str | None:
    """A non-empty string, capped; anything else is None."""
    return value[:EVENT_TEXT_CAP] if isinstance(value, str) and value else None


def github_repo(url) -> str | None:
    """`owner/name` of a github.com remote URL, or None for any other URL."""
    match = _GITHUB_REMOTE_RE.fullmatch(url) if isinstance(url, str) else None
    if not match or match.group(2) in (".", ".."):
        return None
    return f"{match.group(1)}/{match.group(2)}"


def cwd_repo(cwd, cache: dict) -> str | None:
    """`owner/name` of `cwd`'s `origin` when `cwd` still exists and that
    remote is on github.com; None otherwise. One `git` call per directory."""
    if not isinstance(cwd, str) or not cwd:
        return None
    if cwd not in cache:
        repo = None
        if os.path.isabs(cwd) and os.path.isdir(cwd):
            try:
                proc = subprocess.run(
                    ["git", "-C", cwd, "-c", "core.fsmonitor=false",
                     "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=10,
                    stdin=subprocess.DEVNULL)
                if proc.returncode == 0:
                    repo = github_repo(proc.stdout.strip())
            except (OSError, subprocess.SubprocessError):
                repo = None
        cache[cwd] = repo
    return cache[cwd]


def _event_ts(when: datetime) -> str:
    """UTC, milliseconds, `Z`: one shape, so records sort as strings."""
    return when.astimezone(timezone.utc).isoformat(timespec="milliseconds") \
        .replace("+00:00", "Z")


def _skill_event(entry: dict, when: datetime, event_id, name: str,
                 cache: dict, machine: str, root: str) -> dict:
    """One private record of a skill invocation. `session_title` is filled
    in when the whole transcript has been read."""
    ts = _event_ts(when)
    session = _text(entry.get("sessionId"))
    if not _text(event_id):
        # No tool-use id or entry uuid: an id that is still stable across runs.
        event_id = hashlib.sha256(f"{session}\n{ts}\n{name}".encode("utf-8")).hexdigest()
    return {"v": 1, "id": _text(event_id), "ts": ts, "kind": "skill",
            "name": name[:EVENT_TEXT_CAP], "surface": "local", "root": root,
            "machine": machine,
            "session_id": session, "session_title": None,
            "cwd_repo": cwd_repo(entry.get("cwd"), cache),
            "branch": _text(entry.get("gitBranch"))}


def _bump(counts: dict, key: str, week: str) -> None:
    counts.setdefault(key, {})
    counts[key][week] = counts[key].get(week, 0) + 1


def census_counts(projects_dir: Path, now: datetime,
                  weeks: int = DEFAULT_WEEKS) -> dict:
    """{model_id: {iso_week: count}} over the trailing `weeks` ISO weeks.

    One count per assistant TURN that names a model, deduplicated per
    transcript on `message.id`. Entries outside the window, entries of any
    other type, entries without a model, and lines that are not JSON at all
    are all skipped — a transcript being written while this runs will have a
    torn last line, and that is not a reason to fail.
    """
    return census_all(projects_dir, now, weeks)[0]


# --- Local git-hook runs (`--hook-logs`) -----------------------------------
# agent-checks (managed in _agent-guidance) appends one line per local hook
# run: {"v":1,"ts":"...Z","event":"pre-commit|pre-push","repo":"owner/name"|null,
# "checks":[ids],"failed":[ids]}. The lines are as untrusted as transcripts.

#: The shape of a check id that may be a published key.
HOOK_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
HOOK_EVENTS = ("pre-commit", "pre-push")
#: The per-week row that counts every run, whatever its checks.
HOOK_ALL = "all"
#: Public repositories that are not skill registries but run the hooks.
ALWAYS_PUBLIC_REPOS = frozenset({"example-adopter/skills-evals",
                                 "example-adopter/_agent-guidance"})
_HOOK_LIST_CAP = 200


def default_hook_log() -> str:
    """Where agent-checks writes, by the same rule it uses."""
    explicit = os.environ.get("AGENT_CHECKS_LOG")
    if explicit:
        return explicit
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "agent-checks", "runs.jsonl")


def public_repos() -> frozenset[str]:
    """`owner/name` of every registry in harness/registries.yml that is not
    private, plus ALWAYS_PUBLIC_REPOS. Needs PyYAML, so the import is lazy."""
    import eval_coverage
    import run_eval
    repos = set(ALWAYS_PUBLIC_REPOS)
    for entry in run_eval._load_registries_config():
        if entry["name"] not in eval_coverage.PRIVATE_REGISTRIES:
            repo = github_repo(entry["url"])
            if repo:
                repos.add(repo)
    return frozenset(repos)


def _id_list(value) -> list | None:
    if not isinstance(value, list) or len(value) > _HOOK_LIST_CAP:
        return None
    if not all(isinstance(item, str) and 0 < len(item) <= 100 for item in value):
        return None
    return value


def _hook_run(line: str) -> dict | None:
    """One well-formed agent-checks line, or None."""
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    if not isinstance(entry, dict) or type(entry.get("v")) is not int or entry["v"] != 1:
        return None
    when = parse_ts(entry["ts"]) if isinstance(entry.get("ts"), str) else None
    repo = entry.get("repo")
    checks, failed = _id_list(entry.get("checks")), _id_list(entry.get("failed"))
    if (when is None or entry.get("event") not in HOOK_EVENTS or checks is None
            or failed is None
            or not (repo is None or (isinstance(repo, str) and 0 < len(repo) <= 200))):
        return None
    return {"when": when, "event": entry["event"], "repo": repo,
            "checks": checks, "failed": failed}


def _hook_key(check: str) -> str:
    return check if HOOK_ID_RE.fullmatch(check) and check != HOOK_ALL else OTHER_KEY


def hook_tally(paths, now: datetime, weeks: int, repos: frozenset[str],
               events: list | None = None, machine: str = "") -> dict | None:
    """{"rows": {(iso_week, name, repo): [runs, blocked]}, "runs_scanned": n}
    over the census window, or None when no file in `paths` could be read.

    A malformed line is skipped. A line seen twice (the same bytes in two
    logs) counts once: its id is the hash of the machine and the line. When
    `events` is a list, each counted run is appended to it as a private
    record.
    """
    wanted = set(window_weeks(now, weeks))
    rows: dict = {}
    seen: set[str] = set()
    read = False
    for path in paths:
        try:
            handle = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        read = True
        with handle:
            for raw in handle:
                raw = raw.strip()
                run = _hook_run(raw) if raw else None
                if run is None or iso_week(run["when"]) not in wanted:
                    continue
                digest = hashlib.sha256(f"{machine}\n{raw}".encode("utf-8")).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                week = iso_week(run["when"])
                repo = run["repo"]
                if repo is not None and repo not in repos:
                    repo = OTHER_KEY
                failed = {_hook_key(check) for check in run["failed"]}
                ran = {_hook_key(check) for check in run["checks"]} | failed
                for name, hit in [((HOOK_ALL, None), bool(failed))] + \
                        [((key, repo), key in failed) for key in ran]:
                    counts = rows.setdefault((week, *name), [0, 0])
                    counts[0] += 1
                    counts[1] += int(hit)
                if events is not None:
                    events.append({"v": 1, "id": digest, "ts": _event_ts(run["when"]),
                                   "kind": "hook", "event": run["event"],
                                   "repo": run["repo"], "checks": run["checks"],
                                   "failed": run["failed"], "surface": "local",
                                   "root": root_kind(path), "machine": machine})
    return {"rows": rows, "runs_scanned": len(seen)} if read else None


def root_kind(path) -> str:
    """Which transcript root `path` is, for the private records: `cowork`
    (the desktop app's Cowork sandbox), `windows` (a Windows drive seen from
    WSL, `/mnt/<letter>/...`) or `wsl` (this side's own home)."""
    parts = Path(path).parts
    if "local-agent-mode-sessions" in parts:
        return "cowork"
    if len(parts) > 2 and parts[:2] == ("/", "mnt") and len(parts[2]) == 1:
        return "windows"
    return "wsl"


def census_all(projects_dir, now: datetime, weeks: int = DEFAULT_WEEKS,
               allowlist: frozenset[str] | None = None,
               events: list | None = None) -> tuple[dict, dict]:
    """(model counts, skill tally) from ONE walk of the transcripts.

    `projects_dir` is one transcript root or a list of them (WSL, Windows,
    Cowork). A turn, Skill call or typed command whose id an EARLIER root
    already counted is skipped, so a session stored under two roots counts
    once. Inside one Cowork root the ids are deduplicated across files too:
    the desktop app stores each Cowork session twice.

    Model counts are `census_counts`'s. The skill tally is
    {"invocations": {skill: {iso_week: n}}, "sessions": {skill: {iso_week: n}},
    "sessions_scanned": n}. Invocations stay empty when `allowlist` is None;
    otherwise one per `Skill` tool call (deduplicated per transcript on the
    tool_use `id`) and one per user entry whose typed slash command names an
    allowlisted skill. `sessions` counts the distinct transcript files that
    invoked a skill in a week; `sessions_scanned` the transcript files read
    (those the mtime prefilter kept). Same window as the models.

    When `events` is a list (and `allowlist` is given), one private record
    per counted invocation is appended to it, the name as invoked; its
    `session_title` is the transcript's last `summary` or `custom-title`.
    """
    wanted = set(window_weeks(now, weeks))
    oldest = window_start(now, weeks)
    counts: dict[str, dict[str, int]] = {}
    skills: dict[str, dict[str, int]] = {}
    sessions: dict[str, dict[str, int]] = {}
    tally = {"invocations": skills, "sessions": sessions, "sessions_scanned": 0}
    #: (st_dev, st_ino) of every transcript already processed. A symlink or a
    #: hard-linked copy is the SAME underlying file reachable via a second
    #: path in the walk — reading it twice double-counts every turn in it.
    #: This is deliberately NOT the same thing as deduping turns globally:
    #: inside one non-Cowork root `seen` below still resets per (unique)
    #: file, because two genuinely different transcripts can legitimately
    #: share a message id (a resumed session) and both must still count.
    seen_files: set[tuple[int, int]] = set()
    roots = ([Path(projects_dir)] if isinstance(projects_dir, (str, Path))
             else [Path(root) for root in projects_dir])
    record = events is not None and allowlist is not None
    machine = machine_name() if record else ""
    repos: dict = {}
    #: ("t"|"c"|"u", id) of every turn, Skill call and typed entry counted
    #: under an earlier root.
    claimed: set[tuple[str, str]] = set()
    for root in roots:
        if root.is_dir():
            claimed |= _census_root(root, wanted, oldest, counts, tally, seen_files,
                                    claimed, allowlist, events if record else None,
                                    repos, machine)
    return counts, tally


def _census_root(root: Path, wanted: set, oldest: datetime, counts: dict,
                 tally: dict, seen_files: set, claimed: set, allowlist,
                 events: list | None, repos: dict, machine: str) -> set:
    """Walk one transcript root into `counts` and `tally`; return the ids it
    counted. See census_all."""
    skills, sessions = tally["invocations"], tally["sessions"]
    record = events is not None
    kind_of_root = root_kind(root)
    this_root: set[tuple[str, str]] = set()

    def fresh(key) -> bool:
        if key in claimed or key in seen:
            return False
        seen.add(key)
        return True

    for transcript in sorted(root.rglob("*.jsonl")):
        try:
            stat = transcript.stat()
        except OSError:
            continue
        file_id = (stat.st_dev, stat.st_ino)
        if file_id in seen_files:
            continue
        seen_files.add(file_id)
        # A transcript last written before the window cannot hold an entry
        # inside it; skipping it unread is the difference between reading
        # eight weeks of transcripts and reading every one ever made. The
        # bound carries a whole extra week of slack (window_start), so a
        # file being appended to across a week boundary is never dropped.
        if datetime.fromtimestamp(stat.st_mtime, timezone.utc) < oldest:
            continue
        seen = this_root if kind_of_root == "cowork" else set()
        invoked: set[tuple[str, str]] = set()   # (skill, week) in this file
        file_events: list[dict] = []
        title = None
        tally["sessions_scanned"] += 1
        try:
            with open(transcript, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    kind = entry.get("type")
                    if record and kind in ("summary", "custom-title"):
                        text = entry.get("summary" if kind == "summary" else "customTitle")
                        title = _text(text) or title
                        continue
                    if kind not in ("assistant", "user"):
                        continue
                    message = entry.get("message")
                    if not isinstance(message, dict):
                        continue
                    when = parse_ts(entry.get("timestamp"))
                    if when is None:
                        continue
                    week = iso_week(when)
                    if week not in wanted:
                        continue
                    if kind == "user":
                        if allowlist is not None:
                            typed = _typed_skill(message, allowlist)
                            uuid = entry.get("uuid")
                            if typed is not None and isinstance(uuid, str) and uuid \
                                    and not fresh(("u", uuid)):
                                typed = None
                            if typed is not None:
                                _bump(skills, typed, week)
                                invoked.add((typed, week))
                                if record:
                                    raw = _typed_command(message)
                                    file_events.append(_skill_event(
                                        entry, when, entry.get("uuid"),
                                        raw[1:] if raw.startswith("/") else raw,
                                        repos, machine, kind_of_root))
                        continue
                    if allowlist is not None and isinstance(message.get("content"), list):
                        for item in message["content"]:
                            if not (isinstance(item, dict)
                                    and item.get("type") == "tool_use"
                                    and item.get("name") == "Skill"):
                                continue
                            call = item.get("id")
                            if isinstance(call, str) and call \
                                    and not fresh(("c", call)):
                                continue
                            given = item.get("input")
                            raw = given.get("skill") if isinstance(given, dict) else None
                            name = skill_key(raw, allowlist)
                            _bump(skills, name, week)
                            invoked.add((name, week))
                            if record and _text(raw):
                                file_events.append(_skill_event(
                                    entry, when, call, raw, repos, machine,
                                    kind_of_root))
                    key = published_key(message.get("model"))
                    if key is None:
                        continue
                    turn = _turn_key(entry, message)
                    if turn is not None and not fresh(("t", turn)):
                        continue
                    _bump(counts, key, week)
        except OSError:
            continue
        for name, week in invoked:
            _bump(sessions, name, week)
        for event in file_events:
            event["session_title"] = title
        if record:
            events.extend(file_events)
        if seen is not this_root:
            this_root |= seen
    return this_root


def build_document(projects_dir: Path, now: datetime,
                   weeks: int = DEFAULT_WEEKS) -> dict:
    """The published artifact. Exactly three keys — see the module docstring."""
    return _document(census_counts(projects_dir, now, weeks), now, weeks)


def _document(counts: dict, now: datetime, weeks: int) -> dict:
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "weeks": window_weeks(now, weeks),
        "counts": counts,
    }


def build_weekly_document(tally: dict, now: datetime,
                          weeks: int = DEFAULT_WEEKS, hooks: dict | None = None) -> dict:
    """`usage/weekly-local.json`, built by `harness/skill_usage.py`, which
    owns its schema. The window starts at the Monday of its oldest week.
    `hooks` is `hook_tally()`'s result; None adds no hook rows or source."""
    rows = [skill_usage.row(week, "skill", name, n,
                            tally["sessions"].get(name, {}).get(week, 0))
            for name, by_week in tally["invocations"].items()
            for week, n in by_week.items()]
    if hooks is not None:
        rows += [skill_usage.hook_row(week, name, repo, runs, blocked)
                 for (week, name, repo), (runs, blocked) in hooks["rows"].items()]
    oldest = skill_usage.week_monday(window_weeks(now, weeks)[-1])
    window_from = datetime.fromisoformat(oldest).replace(tzinfo=timezone.utc)
    return skill_usage.document(rows, now, window_from, tally["sessions_scanned"],
                                None if hooks is None else hooks["runs_scanned"])


#: Where the private store keeps records: events/<YYYY-MM>/<machine>.jsonl.
EVENTS_DIR = "events"
_MONTH_RE = re.compile(r"[0-9]{4}-[0-9]{2}")


def merge_events(events_path, store) -> int:
    """Add each record of `events_path` (this script's `--events-out`) to
    `store`/events/<YYYY-MM>/<machine>.jsonl unless its id is already there.

    Existing lines are kept byte for byte, whatever they hold; a file that
    gains a record is rewritten sorted by `ts` (stable, so equal stamps keep
    their order). Returns how many records were added. ValueError for a
    malformed record or a target path that goes through a symbolic link
    (the store is a clone of a branch, and a committed link could point the
    write at `lessons/` or outside the clone). PRIVATE:
    `scripts/publish_usage_census.sh` calls this only on its clone of the
    private `persistent/usage-events` branch, and prints only the count.
    """
    store = Path(store).resolve()
    groups: dict = {}
    with open(events_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            event = json.loads(line)
            if not (isinstance(event, dict) and isinstance(event.get("id"), str)
                    and isinstance(event.get("ts"), str)
                    and isinstance(event.get("machine"), str)
                    and _MONTH_RE.fullmatch(event["ts"][:7])
                    and MACHINE_RE.fullmatch(event["machine"])):
                raise ValueError("an event record is malformed")
            groups.setdefault((event["ts"][:7], event["machine"]), []).append(event)
    added = 0
    for (month, machine), batch in sorted(groups.items()):
        path = store / EVENTS_DIR / month / f"{machine}.jsonl"
        if path.resolve() != path:
            raise ValueError("an event file's path goes through a link")
        kept, ids = [], set()
        if path.exists():
            for raw in path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                try:
                    old = json.loads(raw)
                except ValueError:
                    old = None
                old = old if isinstance(old, dict) else {}
                if isinstance(old.get("id"), str):
                    ids.add(old["id"])
                kept.append((old["ts"] if isinstance(old.get("ts"), str) else "", raw))
        new = []
        for event in batch:
            if event["id"] not in ids:
                ids.add(event["id"])
                new.append((event["ts"], json.dumps(event, sort_keys=True)))
        if not new:
            continue
        added += len(new)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = sorted(kept + new, key=lambda pair: pair[0])
        path.write_text("".join(raw + "\n" for _, raw in lines), encoding="utf-8")
    return added


def write_events(path: Path, events: list) -> None:
    """JSON lines, sorted by timestamp (then id). PRIVATE: see the docstring."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for event in sorted(events, key=lambda e: (e["ts"], e["id"])):
            f.write(json.dumps(event, sort_keys=True) + "\n")


def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, sort_keys=True)
        f.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects", action="append", default=None,
                        help="Claude Code transcript root; repeat it, or separate "
                             "roots with the OS path separator, for several "
                             "(default ~/.claude/projects)")
    parser.add_argument("--weeks", type=int, default=DEFAULT_WEEKS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--weekly-out", type=Path, default=None,
                        help="also write weekly skill invocations here "
                             "(published as usage/weekly-local.json)")
    parser.add_argument("--events-out", type=Path, default=None,
                        help="also write one private JSON line per skill "
                             "invocation here (never published publicly)")
    parser.add_argument("--hook-logs", default=None,
                        help="agent-checks run logs, separated by the OS path "
                             "separator; missing files are skipped (default: "
                             "agent-checks' own default path)")
    parser.add_argument("--skill-allowlist-root", type=Path, default=REPO_ROOT,
                        help="repo whose evals/ fixtures and non-coverage.yml "
                             "name the publishable skills (default: this "
                             "script's repo)")
    parser.add_argument("--now", default=None,
                        help="ISO-8601 instant to treat as now (tests and "
                             "scripts/publish_usage_census.sh's test seam); "
                             "default: the wall clock")
    args = parser.parse_args()

    try:
        _require_model_id_re()
    except ImportError:
        # PyYAML (via roster.py) is not installed on this machine. Printed
        # here, not raised: the durable machine that runs this script
        # (evals/usage/CENSUS.md) installs nothing by default,
        # and a bare ImportError traceback used to kill the import of this
        # module before argparse ever ran — even `--help` failed.
        print(PYYAML_MISSING_MESSAGE, file=sys.stderr)
        return 2

    roots = ([DEFAULT_PROJECTS] if args.projects is None else
             [Path(part) for value in args.projects
              for part in value.split(os.pathsep) if part])
    if not any(root.is_dir() for root in roots):
        # Say so and stop, rather than publishing a clean census of nothing —
        # never a clean census of nothing on a surface that cannot do the
        # job. The message names no path: this line reaches a scheduled
        # task's status report.
        print("no Claude Code transcript directory on this surface; "
              "nothing published", file=sys.stderr)
        return 2

    now = parse_ts(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print("--now is not an ISO-8601 instant", file=sys.stderr)
        return 2
    allowlist = None
    repos = frozenset()
    if args.weekly_out is not None or args.events_out is not None:
        import eval_coverage
        try:
            allowlist = skill_allowlist(args.skill_allowlist_root)
            repos = public_repos()
        except (eval_coverage.CensusRefusal, ValueError, OSError):
            # No detail: a refusal message can carry a path, and this line
            # reaches a scheduled task's status report. Nothing is written.
            print("skill allowlist could not be read from the evals "
                  "directory; nothing published", file=sys.stderr)
            return 2
    events = [] if args.events_out is not None else None
    counts, tally = census_all(roots, now, args.weeks, allowlist, events)
    hooks = None
    if allowlist is not None:
        paths = ([default_hook_log()] if args.hook_logs is None
                 else [p for p in args.hook_logs.split(os.pathsep) if p])
        hooks = hook_tally(paths, now, args.weeks, repos, events, machine_name())
    document = _document(counts, now, args.weeks)
    _write_json(args.out, document)

    # Non-identifying status line only: counts, and the OUTPUT path the caller
    # already knows — never a model id, never anything from under --projects.
    total = sum(sum(by_week.values()) for by_week in document["counts"].values())
    status = (f"census: {len(document['counts'])} models, {total} assistant turns, "
              f"{args.weeks} weeks -> {args.out}")
    if args.weekly_out is not None:
        _write_json(args.weekly_out,
                    build_weekly_document(tally, now, args.weeks, hooks))
        calls = sum(sum(by_week.values()) for by_week in tally["invocations"].values())
        status += f"; {calls} skill invocations -> {args.weekly_out}"
    if args.events_out is not None:
        write_events(args.events_out, events)
        status += f"; {len(events)} private events -> {args.events_out}"
    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
