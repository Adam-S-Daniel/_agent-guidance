#!/usr/bin/env python3
"""Payload assembly and delivery for the `guidance` eval subject (#97).

A SKILL eval copies one skill into `<workspace>/.claude/skills/` and runs the
agent with `--setting-sources project`. The fleet guidance is delivered
differently — `_agent-guidance`'s `fleet-memory.sh` SessionStart hook writes a
MARKED BLOCK into user memory (`$CLAUDE_CONFIG_DIR/CLAUDE.md`, default
`~/.claude/CLAUDE.md`), which the CLI reads once per session — so an eval of
the guidance has to deliver it the same way, and has to PROVE per arm that it
did.

THE TRAP THIS MODULE EXISTS FOR. On any machine or hosted session carrying the
fleet hook, the real `~/.claude/CLAUDE.md` already IS the guidance. A harness
that does not isolate the config dir per arm therefore delivers the guidance to
BOTH arms and reports a null delta that reads as "the guidance does nothing" —
a quiet, plausible, wrong number. Hence: a fresh scratch config dir per arm, an
environment ALLOWLIST rather than the ambient environment (`agent_env` below,
the same shape harness/propagation/arms.py uses), and a magic-token probe per
arm whose disagreement with the arm's expectation makes the run INCONCLUSIVE —
never PASS, never FAIL.

WHERE THE CONTENT COMES FROM, and why that is a boundary. Guidance content is
EXECUTED by the arm — that is the subject, and eval.yml's header states it as
the trust boundary a guidance dispatch accepts — but the harness reads that
content only from inside the `_agent-guidance` checkout it was pointed at:
every manifest `file:` is resolved with its symlinks followed and refused if
it lands outside (`inside_checkout` below). Before that, a row
`file: ../OUTSIDE_SECRET.md` was read, delivered, and written into
`results/.../transcripts/raw.json`, which main pushes to the public
`persistent/eval-results` branch — so a row could publish any file the runner can read.

THE FIVE MODES (`mode:` on an arm):

  none                — no GUIDANCE delivered; the control. It is not
                        delivered nothing: it gets a DECOY, a fresh token of
                        its own in an otherwise empty marked block, through
                        the same hook. See the guard paragraph below.
  stub                — `agents-md/stub.md`, what a repo carries inline.
  section             — the section's own file's intro (everything before its
                        first `##`) plus the section's extent.
  full                — the whole delivered corpus: `base.md`, plus the
                        section's own file when it lives under `sections/`.
  full-minus-section  — that corpus with the section's extent removed.

WHAT THE PER-ARM GUARD PROVES, exactly. Every arm's guard is TWO-SIDED: it
reports the token it was delivered, and it reports no token it was not. For a
TREATMENT arm: this arm was delivered its payload and reads it — its probe
reports the run's magic token, which no earlier run could have left behind —
and it did NOT read the control's scratch user memory (its probe does not
report the decoy). For the CONTROL arm: it reads its OWN scratch user memory
(its probe reports the DECOY token delivered to it, and only to it) and it was
NOT delivered the treatment payload (its probe does not report the treatment
token). Without the decoy the control's guard was
vacuous: `mode: none` delivered nothing, so the probe could only ever answer
"no magic word" — which is exactly what it answers when the arm IS
contaminated, measured with an ambient file carrying a stale token and with
the real base.md, both of which scored clean.

THE RESIDUAL the guard does NOT settle: a control arm that reads its own
scratch memory AND an ambient one in addition — a real `~/.claude/CLAUDE.md`
alongside the delivered decoy — is prevented by the per-arm HOME and
CLAUDE_CONFIG_DIR isolation rather than by the guard. TWO reasons, and the
one this paragraph used to give is the narrower: a probe asked for the magic
words when its context carries two may report either, and a contaminating
source that carries no token of its own is invisible to a token guard at all.
Measured, both of these score clean: an ambient file carrying a STALE token
(no current run's token is in the reply, so there is nothing to catch) and
the real base.md, which carries no token whatsoever. Only a real dispatch
settles it.

The two-word case is the DECOY'S OWN DOING, and is why GUARD_PROMPT asks for
every magic word rather than "the magic word": before the decoy a
contaminated control carried exactly one magic word — the treatment token —
and had to report it, and the decoy gave that token somewhere to hide. A
one-word answer from a contaminated control is the case the plural prompt
exists for.

`section` vs `none` asks "does this teach the behavior"; `full` vs
`full-minus-section` is the ablation — the marginal value of the section IN
SITU, inside a 56 KB always-on file, which is the question that decides whether
a section keeps paying for its bytes.

EXTENT is defined exactly as `_agent-guidance`'s own
`scripts/check-guidance-coverage.js` defines it, because that script's `bytes`
column and this module's payloads must never disagree: from a `##` heading
through the line before the next `##` in the same file (or end of file), `###`
children included. It is computed with a REAL markdown parse (markdown-it-py,
pinned) and never a regex — `base.md` contains fenced blocks, and a `## ` line
inside a fence is not a heading.

Everything down to the `delivery` banner is pure: same inputs, same string,
no filesystem writes, no clock, no network.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import string
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_canary  # noqa: E402  — run_leg is the probe the guard reuses

MODES = ("none", "stub", "section", "full", "full-minus-section")

# Where the guidance is delivered, and therefore which `--setting-sources` the
# arm's CLI invocation gets. `user` is the production path (the hook writes
# $CLAUDE_CONFIG_DIR/CLAUDE.md, the CLI reads it as user memory); `project`
# is the documented fallback for a CLI that does not honour
# CLAUDE_CONFIG_DIR for memory — same hook, pointed at the workspace, read as
# project memory. Whichever is used is recorded in every summary, and the
# per-arm guard decides whether the arm counts either way.
# The three names that ARE the per-arm isolation. A fixture's `env:` block is
# applied after they are set, so a fixture allowed to name one could point an
# arm at the operator's real config dir.
ISOLATION_NAMES = ("HOME", "TMPDIR", "CLAUDE_CONFIG_DIR")

DELIVERIES = ("user", "project")
SETTING_SOURCES = {"user": "user,project", "project": "project"}

# The `--permission-mode` every agent arm (skill and guidance alike, both
# through run_eval.run_agent) and the judge are launched with. `auto` is the
# default: it is how the owner runs nearly everything, so it is the context
# the evals should measure, and `bypassPermissions` is refused outright when
# the CLI runs as root (a Claude routine's sandbox, ADR 0010; probe
# 2026-10-06). `bypassPermissions` stays selectable so a run can be made
# comparable with results recorded before this setting existed — every one
# of which ran under it, which is why a summary WITHOUT a recorded mode is
# read as `LEGACY_PERMISSION_MODE` (scripts/make_badge.py). The mode is
# recorded in every summary.json's `harness` block, and runs under different
# modes are never averaged together. The guard probe (run_canary.run_leg)
# is tool-free and passes no mode at all; it is not governed by this.
PERMISSION_MODES = ("auto", "bypassPermissions")
DEFAULT_PERMISSION_MODE = "auto"
LEGACY_PERMISSION_MODE = "bypassPermissions"


def check_permission_mode(mode) -> str:
    """`mode` if it is one of PERMISSION_MODES; else GuidanceError. Checked
    at the sink (the argv builder), whatever a caller passed."""
    if mode not in PERMISSION_MODES:
        raise GuidanceError(
            f"permission mode {mode!r} is not one of "
            f"{', '.join(PERMISSION_MODES)}")
    return mode


# The agent arms' CLI effort level: the values `claude --help` lists for
# `--effort <level>` (Claude Code 2.1.292). No arm set one before this, so
# every arm ran at the CLI's own default, which a release can change. None
# (no `--effort` flag, no fixture `effort:` key) keeps exactly that: no flag
# is passed and the summary records `effort: null`, read as "CLI default".
# Agent arms only: the judge and the guard probe are launched without it.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def check_effort(effort) -> str | None:
    """`effort` if it is None or one of EFFORT_LEVELS; else GuidanceError.
    Checked at fixture load and again at the sink (the argv builder)."""
    if effort is not None and effort not in EFFORT_LEVELS:
        raise GuidanceError(
            f"effort {effort!r} is not one of {', '.join(EFFORT_LEVELS)} "
            "(or absent, for the CLI's default)")
    return effort

MANIFEST_REL = Path("agents-md") / "eval-coverage.yml"
BASE_REL = Path("agents-md") / "base.md"
STUB_REL = Path("agents-md") / "stub.md"
HOOK_REL = Path(".claude") / "hooks" / "fleet-memory.sh"

# The probe. Deliberately the guidance-bridge canary's own prompt and
# disallowed-tools list (evals/guidance-bridge-canary/fixture.yaml) — without
# the tool controls the model forages with Read/Glob and "finds" the token in
# a file, which would make a contaminated arm look delivered. A test asserts
# these two still match that fixture.
# PLURAL, deliberately. "reply with that magic word" asks for ONE, and since
# the control arm is delivered a DECOY its context can carry TWO — its own
# decoy and, when it is contaminated, the treatment token as well. A probe
# obeying the singular question could then report the decoy alone and hide the
# treatment token behind it: measured with a contaminated control and a probe
# that answers one word, rc 0 and a clean score, where the same fixture before
# the decoy existed (one magic word to report) gave rc 2. Asking for every
# magic word is what makes the two-sided check answerable.
GUARD_PROMPT = ("If your context contains any magic words, reply with every "
                "one of them and nothing else. If it contains none, reply "
                "with exactly NO-MAGIC-WORD. Do not use any tools.")
GUARD_DISALLOWED_TOOLS = "Read,Glob,Grep,Bash,WebFetch,WebSearch,Task"

# Everything the agent/probe child is allowed to inherit by name, plus HOME,
# TMPDIR, CLAUDE_CONFIG_DIR and every ANTHROPIC_* variable, which `agent_env`
# sets explicitly. An allowlist, never `{**os.environ, ...}`: arms.py measured
# 16 vs 35 loaded skills between a scrubbed and an ambient environment, and an
# arm that inherits the operator's own CLAUDE_* settings is not measuring the
# guidance.
PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "SHELL", "USER", "NODE_PATH")

# Test seam, exactly as harness/propagation/init_probe.py has one: the
# hermetic suite drives test/fake-claude, whose mode is selected by an
# environment variable, and the allowlist is precisely what would otherwise
# stop that variable reaching the child. Tests patch this; production leaves
# it empty and a test asserts the committed value.
EXTRA_PASSTHROUGH = ()

# Forwarded only when the harness's own environment sets it. A hosted Claude
# Code session (the eval routine, ADR 0010) authenticates through a host proxy
# at ANTHROPIC_BASE_URL and holds no credential of its own; from CLI 2.1.305
# the CLI refuses to start ("Not logged in") unless this flag says the host
# provides auth, where 2.1.304 started without it (measured 2026-10-20 with
# `env -i`: this one name added, and nothing else, turns the refusal into an
# answer). It is a mode flag, not a credential, and outside a hosted session
# it is absent and nothing changes.
HOSTED_PASSTHROUGH = ("CLAUDE_CODE_REMOTE",)


class GuidanceError(ValueError):
    """A configuration problem the operator must fix: a missing checkout, an
    unknown section id, an unknown mode. Always exit 2, never a score."""


# ---------------------------------------------------------------------------
# timeouts — ONE ceiling and ONE predicate, for EVERY source
#
# `subprocess.run(timeout=None)` waits forever, and the only backstop in CI is
# the 45-minute job kill, with no summary and no artifact written. A string
# yields a TypeError traceback and rc 1, outside the "configuration problem"
# contract (rc 2, a named message, no traceback). Above ~2.147e6 the value
# reaches `selector.poll` as milliseconds and raises a bare `OverflowError:
# timeout is too large` — rc 1, empty stdout, no named message.
#
# The ceiling lives HERE, beside the error type it raises, rather than in any
# one entry point, because the value has more than one source and a bound that
# only guards the source you were looking at is not a bound. Round 2 added the
# ceiling to `run_eval.validate_timeouts`, which sees the FIXTURE dict alone —
# and `run_eval.py --timeout 2200000` walked straight past it into the same
# `OverflowError` the ceiling was added to close (measured, round 3). Every
# entry point under harness/ that accepts a timeout from an operator now calls
# `check_timeout` with the same predicate and the same message:
# run_eval.py's `--timeout` and its four fixture knobs, run_canary.py's
# `--timeout`, run_propagation.py's `--timeout`.
# `test_every_harness_subprocess_timeout_names_its_validated_source` is the
# inventory that keeps a NEW sink from arriving with no source named.
#
# 45 minutes is `.github/workflows/eval.yml`'s `eval` job budget: a knob larger
# than the job's own budget cannot do anything except outlive it.
# `test_the_timeout_ceiling_is_the_workflow_job_budget` parses that workflow
# and asserts this constant equals its `timeout-minutes` x 60, so the two
# cannot drift.
MAX_TIMEOUT_S = 45 * 60


def timeout_is_sane(value) -> bool:
    """The single predicate. A timeout is a real, finite, positive number of
    seconds no greater than the ceiling.

    `bool` is an `int` in Python; `timeout_s: true` is not a duration.
    Bounded on BOTH sides in the one predicate: an upper bound that lived in a
    second check somewhere else is a bound a later edit can drop without the
    lower one noticing.
    """
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and value == value and value not in (float("inf"), float("-inf"))
            and 0 < value <= MAX_TIMEOUT_S)


def check_timeout(value, where: str, remedy: str, prefix: str = "") -> None:
    """`value` is a sane timeout, or a named GuidanceError saying why not.

    `where` names the knob or flag in the spelling the operator typed it;
    `remedy` is the one sentence that tells them what to write instead, which
    differs between a fixture knob (omit the key) and a flag (omit the flag).
    """
    if timeout_is_sane(value):
        return
    raise GuidanceError(
        f"{prefix}`{where}` must be a positive number of seconds no greater "
        f"than {MAX_TIMEOUT_S} (eval.yml gives the eval job that many), got "
        f"{value!r}. An explicit null here means \"no timeout\" — a run that "
        "hangs until the job is killed, with no summary and no artifact; a "
        "value above the ceiling is the same failure with extra steps, and a "
        f"very large one crashes the run outright instead of naming a rule. "
        f"{remedy}")


# The two remedies, so the wording cannot drift between the sources.
FIXTURE_TIMEOUT_REMEDY = "Omit the key to take the default instead."
CLI_TIMEOUT_REMEDY = ("Omit the flag to take the fixture's own `timeout_s:` "
                      "(or the default) instead.")
# The SINK's own remedy. A rejection here names a function that was ABOUT to
# hand the value to the OS, so the fix is always at the caller rather than in
# a fixture the operator may not even have written.
SINK_TIMEOUT_REMEDY = (
    "This is the subprocess sink itself: the value was checked on entry to "
    "the function that spawns, whatever source it came from. Fix the caller "
    "that passed it.")


# ---------------------------------------------------------------------------
# a fixture's `env:` — ONE predicate, at every place a child environment is
# built
#
# A-N1-2. `env: {"A=B": "x"}` reached `subprocess.run(env=...)` and came back
# as `ValueError: illegal environment variable name` — rc 1 and a traceback,
# outside the rc-2 configuration contract every other malformed fixture key
# gets. The OS's rule is short and absolute: a name is a non-empty string with
# no `=` and no NUL, and a value is a string with no NUL. Checked HERE, at
# every function that builds a child environment out of a fixture's block,
# rather than at the loader alone, because a loader check is a claim about the
# callers a loader knows.


def check_env_entry(name, value, where: str) -> None:
    """One `env:` entry, exactly as the OS will take it, or a named
    GuidanceError.

    The rule is `execve`'s and nothing more. Both harness `agent_env`s
    stringify a name and a value before handing them over — an int from YAML
    becomes "7", which is documented and pinned — so the check is on the
    STRINGIFIED forms, not on their Python types: refusing an int here would
    reject fixtures that work today, and accepting an `=` or a NUL rejects
    nothing while letting the OS raise a ValueError traceback part-way into a
    run.
    """
    spelled = str(name)
    if not spelled or "=" in spelled or "\0" in spelled:
        raise GuidanceError(
            f"{where}: `env:` name {name!r} is not a legal environment "
            "variable name — it must be non-empty and contain neither `=` "
            "nor a NUL byte. `execve` refuses anything else, so this used to "
            "reach subprocess as `ValueError: illegal environment variable "
            "name`: rc 1 and a traceback, after the arm had started.")
    if "\0" in str(value):
        raise GuidanceError(
            f"{where}: `env:` value for {spelled!r} contains a NUL byte, "
            "which no environment value may carry.")


def check_env_block(env_spec, where: str) -> None:
    """Every entry of a fixture's `env:` block. An absent or null block is
    fine and means "no entries"; a non-mapping is the caller's own check
    (run_eval.validate_mapping_keys)."""
    if not isinstance(env_spec, dict):
        return
    for name, value in env_spec.items():
        check_env_entry(name, value, where)


# ---------------------------------------------------------------------------
# the CLI child's forced variables — ONE definition, for EVERY session spawn
#
# A skill arm and the judge run under the operator's real HOME on a
# workstation (the interactive login lives there; ADR 0002, decision 4), and
# the CLI's auto-memory writes to `$HOME/.claude/projects/<cwd>/memory/`.
# Observed 2026-10-05: a local rename-pdfs trial wrote `MEMORY.md` and a
# fixture-derived note into the operator's own profile, keyed by the trial's
# temp workspace. `CLAUDE_CODE_DISABLE_AUTO_MEMORY` is the CLI's own switch
# (2.1.289 treats "1"/"true"/"yes"/"on" as off, and a falsy value FORCES it
# on, overriding `autoMemoryEnabled` in settings), so it is SET here, last,
# rather than allow-listed: an ambient `=0` or a fixture `env:` block cannot
# turn it back on. Every sink that starts a CLI session — run_eval.run_agent,
# judge._run_judge_cli, run_canary.run_leg, and local_eval's
# child_environment — applies this mapping; `--version` calls load no
# session and are left alone. The propagation probe builds its own scratch
# HOME and allowlist (init_probe.build_env) and makes no model turn.
CLI_FORCED_ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}


def cli_child_env(env=None) -> dict:
    """A copy of `env` (default: this process's environment) with
    `CLI_FORCED_ENV` applied last. The input mapping is never mutated."""
    out = dict(os.environ if env is None else env)
    out.update(CLI_FORCED_ENV)
    return out


# ---------------------------------------------------------------------------
# checkout + manifest


def resolve_guidance_dir(cli_path: str | None, env_value: str | None,
                         base_dir: Path) -> Path:
    """`--guidance PATH`, else `$AGENT_GUIDANCE_DIR`, else the sibling
    `../_agent-guidance` next to this checkout — the same sibling convention
    #63 gave the skill registries. Existence is NOT checked here (callers
    that never touch the guidance must not pay for a missing checkout);
    `require_guidance_dir` is the check, and it names the flag.
    """
    if cli_path:
        return Path(cli_path).expanduser().resolve()
    if env_value:
        return Path(env_value).expanduser().resolve()
    return (base_dir / ".." / "_agent-guidance").resolve()


def require_guidance_dir(guidance_dir: Path) -> Path:
    """The guidance checkout, or a GuidanceError naming `--guidance`.

    A missing checkout is exit 2 and not a silently empty payload: an arm that
    delivered nothing because the checkout was absent would score as a
    perfectly ordinary `none` arm.
    """
    if not guidance_dir.is_dir():
        raise GuidanceError(
            f"no _agent-guidance checkout at {guidance_dir} — pass "
            "--guidance PATH, set $AGENT_GUIDANCE_DIR, or check it out "
            "side by side as ../_agent-guidance")
    for rel in (MANIFEST_REL, BASE_REL):
        if not inside_checkout(guidance_dir, rel).is_file():
            raise GuidanceError(
                f"{guidance_dir} does not look like an _agent-guidance "
                f"checkout: {rel} is missing — pass --guidance PATH, set "
                "$AGENT_GUIDANCE_DIR, or check it out side by side as "
                "../_agent-guidance")
    return guidance_dir


def load_manifest(guidance_dir: Path) -> list[dict]:
    """`agents-md/eval-coverage.yml` — one row per `##` heading, keyed by a
    stable `id` that never moves when the heading's wording does."""
    path = inside_checkout(guidance_dir, MANIFEST_REL)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GuidanceError(f"could not read the section manifest {path}: {exc}") from exc
    if not isinstance(doc, list) or not doc:
        raise GuidanceError(f"{path} must be a non-empty YAML list of section rows")
    for i, row in enumerate(doc):
        if not isinstance(row, dict):
            raise GuidanceError(f"{path} row {i} is not a mapping")
        missing = [f for f in ("id", "heading", "file") if not row.get(f)]
        if missing:
            raise GuidanceError(
                f"{path} row {i} is missing required field(s): {', '.join(missing)}")
        # TYPE, not just presence: `not row.get("file")` is a truthiness test,
        # so `file: 5` and `file: [x]` sailed past it and reached
        # `root / 5` inside inside_checkout — `TypeError: unsupported operand
        # type(s) for /` and rc 1, outside the rc-2 configuration contract
        # every other malformed row gets.
        if not isinstance(row["file"], str):
            raise GuidanceError(
                f"{path} row {i}'s `file:` must be a string path relative to "
                f"the checkout, got {row['file']!r} "
                f"({type(row['file']).__name__}). It is joined to the checkout "
                "root and read, so anything else is a configuration mistake "
                "rather than a path.")
        # Checked HERE, at load, and not only at the read: the manifest is
        # loaded once in `_run_guidance` before any arm exists, so a hostile
        # row is refused before ANY arm is delivered anything — including the
        # control's decoy, which an arm-ordering change would otherwise let
        # through ahead of the treatment arm's first read. `_read` runs the
        # same check as the funnel every guidance file read passes through.
        # `file:` is the ONLY path this module builds out of manifest data:
        # base.md, stub.md, the manifest itself and the hook are module
        # constants, and the fixture's `section:` is an id that `_run_guidance`
        # already refuses to let carry a `/`, `.` or `..` before it becomes a
        # results/ path segment.
        inside_checkout(guidance_dir, row["file"])
    return doc


def find_row(manifest: list[dict], section_id: str, guidance_dir: Path) -> dict:
    """The manifest row for `section:`, or a GuidanceError NAMING THE MANIFEST.

    An unknown id is the single most likely fixture typo, and the fix is
    always "look at the manifest" — so the message says where it lives and
    what is in it, rather than failing deep inside a markdown parse.
    """
    for row in manifest:
        if row["id"] == section_id:
            return row
    known = ", ".join(sorted(str(row["id"]) for row in manifest))
    raise GuidanceError(
        f"unknown section id {section_id!r} — no row in "
        f"{inside_checkout(guidance_dir, MANIFEST_REL)} carries it "
        f"(known ids: {known})")


# ---------------------------------------------------------------------------
# extent + payload assembly (pure)


def _markdown_it():
    """markdown-it-py, imported HERE and not at module scope.

    run_eval.py imports this module unconditionally, for every subject — so a
    module-scope `from markdown_it import MarkdownIt` made a SKILL fixture's
    `--arm objective-only` run die with a bare ImportError traceback on a
    machine without the parser, for a code path that never parses markdown at
    all. Imported at the one call site that needs it, and the failure names
    the pip line instead of a traceback.
    """
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:
        raise GuidanceError(
            "the guidance subject locates a section's extent with a real "
            "markdown parse and needs markdown-it-py: "
            "`pip install markdown-it-py==4.2.0` (pinned exact — a floating "
            f"parser version could silently change what a `section` arm "
            f"delivers). Import failed: {exc}") from exc
    return MarkdownIt()


def h2_extents(text: str) -> list[dict]:
    """Every level-2 heading in `text` as {heading, start, end} CHARACTER
    offsets, the extent running from the heading line through the line before
    the next `##` (or end of file).

    A real markdown parse, never a regex: `base.md` has fenced blocks, and a
    `## ` line inside one is not a heading. markdown-it-py also hands back the
    heading's parsed inline text, so `## Closed Form ##` and a CommonMark
    leading-indent heading both read correctly, where a `^##\\s+` regex gets
    both wrong.

    Deliberately the same arithmetic as _agent-guidance's
    scripts/check-guidance-coverage.js, down to the off-by-one it documents:
    `splitlines`-style line starts have one MORE entry than the file has
    newlines, and the trailing phantom line must not be charged a newline of
    its own or the last section in every file overcounts by exactly one.

    The ARITHMETIC matches; the UNIT does not. These are Python CHARACTER
    offsets, where the JS counts `Buffer.byteLength` — the same number only
    for ASCII, and base.md is not ASCII (it is full of em dashes and arrows).
    `test_extents_agree_with_the_real_manifests_generated_bytes` encodes
    before comparing, which is why it agrees; do not "fix" either side to
    match the other.
    """
    # CommonMark normalizes CRLF and lone CR before parsing. Keep offsets
    # into the original text, including both characters of a CRLF delimiter.
    line_start = [0]
    index = 0
    while index < len(text):
        if text[index] == "\r":
            index += 2 if text[index:index + 2] == "\r\n" else 1
            line_start.append(index)
        elif text[index] == "\n":
            index += 1
            line_start.append(index)
        else:
            index += 1

    def offset_at(index: int) -> int:
        return line_start[index] if index < len(line_start) else len(text)

    tokens = _markdown_it().parse(text)
    raw = []
    for i, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h2":
            raw.append((tokens[i + 1].content, token.map[0]))

    out = []
    for i, (heading, start_line) in enumerate(raw):
        end_line = raw[i + 1][1] if i + 1 < len(raw) else len(line_start)
        out.append({"heading": heading, "start": offset_at(start_line),
                    "end": offset_at(end_line)})
    return out


def _extent_of(text: str, heading: str, where: str) -> dict:
    spans = [s for s in h2_extents(text) if s["heading"] == heading]
    if not spans:
        raise GuidanceError(
            f"heading {heading!r} not found in {where} — the manifest row's "
            "`heading` is the volatile half of the join and has probably "
            "drifted from the real file; run "
            "`node scripts/check-guidance-coverage.js` in _agent-guidance")
    if len(spans) > 1:
        raise GuidanceError(
            f"heading {heading!r} appears {len(spans)} times in {where} — "
            "headings must be unique across the guidance source")
    return spans[0]


def inside_checkout(guidance_dir: Path, rel) -> Path:
    """`guidance_dir / rel`, resolved, and PROVEN to be inside the checkout.

    F-1. The manifest's `file:` is the one path in this module built from
    manifest DATA rather than from a module constant, and nothing bounded it:
    a row `file: ../OUTSIDE_SECRET.md` was resolved, read, delivered to the
    arm and written verbatim into
    `results/guidance/<key>/<ts>/<arm>/transcripts/raw.json`, which on `main`
    is pushed to the PUBLIC `persistent/eval-results` branch. So a manifest row could
    publish any file the runner can read. An absolute `file:` is the same
    hole spelled shorter — `Path("/x") / "/etc/passwd"` is `/etc/passwd`.

    SYMLINKS ARE FOLLOWED before the comparison (`.resolve()` on both sides),
    because a link inside `agents-md/` pointing out of the tree is the same
    read with one more step in it. Both sides are resolved, so a checkout
    that itself lives under a symlinked path still compares equal.

    This is a READ boundary, and it is not the same as the trust boundary
    eval.yml states: guidance content is EXECUTED by the arm on purpose, and
    that is the documented risk of the subject. What is not on purpose is the
    harness reading and publishing a file from outside the checkout it was
    pointed at.
    """
    root = Path(guidance_dir).resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise GuidanceError(
            f"the guidance path {str(rel)!r} resolves to {path}, which is "
            f"OUTSIDE the checkout at {root}. The harness reads guidance "
            "content only from inside the checkout it was given: a manifest "
            "row's `file:` (or a symlink it follows) that escapes it would "
            "publish whatever it names into results/, which is pushed to a "
            "public branch. Fix the row in agents-md/eval-coverage.yml, or "
            "point --guidance at the checkout that really holds the file.")
    return path


def _read(guidance_dir: Path, rel) -> str:
    path = inside_checkout(guidance_dir, rel)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GuidanceError(f"could not read {path}: {exc}") from exc


def corpus(guidance_dir: Path, row: dict) -> str:
    """The FULL guidance as delivered for this section.

    `base.md` for a section that lives in it. For an opt-in language section
    under `agents-md/sections/`, base.md with that file appended — which is
    what "full" means for a section that is only ever delivered alongside the
    base, and the only reading under which `full-minus-section` differs from
    `full` at all. Either way the identity holds exactly:
    `len(full) - len(extent) == len(full-minus-section)`.
    """
    base = _read(guidance_dir, BASE_REL)
    if Path(row["file"]) == BASE_REL:
        return base
    if not base.endswith("\n"):
        base += "\n"
    return base + _read(guidance_dir, row["file"])


def token_paragraph(token: str) -> str:
    """The trailing paragraph every non-`none` payload carries, so the per-arm
    delivery guard has something to look for that no earlier run could have
    left behind."""
    return f"\nThe magic word is {token}.\n"


def _random_token() -> str:
    body = "".join(secrets.choice(string.ascii_uppercase) for _ in range(8))
    digits = "".join(secrets.choice(string.digits) for _ in range(4))
    return f"{body}-{digits}"


def new_token() -> str:
    """A fresh TREATMENT token per run. Random, not derived from the clock or a
    counter: a token an earlier run could reproduce would let a stale
    ~/.claude/CLAUDE.md satisfy this run's guard, which is the exact
    contamination the guard exists to catch.
    """
    return _random_token()


def new_decoy_token() -> str:
    """A fresh CONTROL token, one per `none` arm.

    The control is delivered this, and only this, through the same hook — so
    its probe reporting the decoy proves the arm reads ITS OWN scratch user
    memory, and its probe reporting the TREATMENT token proves it was
    contaminated. EVERY arm's guard is two-sided for that reason, not only the
    control's: the decoy is also the forbidden token a TREATMENT arm must not
    report, which is how a treatment arm reading the control's scratch memory
    is caught rather than scored.

    Deliberately NOT `new_token()` under another name: a test that pins one to
    a fixed value must not collapse the other into it, or the control arm
    would be handed the treatment token and report itself contaminated.
    """
    return _random_token()


def assemble(guidance_dir: Path, row: dict, mode: str,
             token: str | None = None) -> str:
    """The payload text for one arm. Pure: no writes, no clock, no network.

    `token=None` assembles the payload WITHOUT the magic-word paragraph — the
    shape the extent identities are stated over. A real run always passes a
    token, because an arm that cannot prove its delivery does not count.
    """
    if mode not in MODES:
        raise GuidanceError(
            f"unknown mode {mode!r} — expected one of {', '.join(MODES)}")
    if mode == "none":
        # The control delivers no GUIDANCE. With a token it delivers the DECOY
        # — that token alone, in an otherwise empty marked block, through the
        # same hook — which is what makes the control's guard two-sided
        # instead of vacuous. The caller passes the arm's OWN token here
        # (`new_decoy_token()`), never the treatment token: a `none` arm
        # carrying the treatment token would defeat its own guard.
        return "" if token is None else token_paragraph(token)

    if mode == "stub":
        payload = _read(guidance_dir, STUB_REL)
    elif mode == "section":
        own = _read(guidance_dir, row["file"])
        spans = h2_extents(own)
        if not spans:
            raise GuidanceError(
                f"{inside_checkout(guidance_dir, row['file'])} has no `##` "
                "heading")
        extent = _extent_of(own, row["heading"], str(row["file"]))
        # The file's intro — everything before its FIRST `##` — prepended, so
        # a section arrives with the framing the real file gives it. A
        # sections/*.md file is its own single `##` and has no intro.
        payload = own[:spans[0]["start"]] + own[extent["start"]:extent["end"]]
    else:
        text = corpus(guidance_dir, row)
        extent = _extent_of(text, row["heading"], f"the corpus for {row['id']}")
        payload = text if mode == "full" else text[:extent["start"]] + text[extent["end"]:]

    if not payload.strip():
        # An EMPTY payload plus the magic-word paragraph would give the arm
        # nothing but the token — and it would still pass its delivery guard,
        # because the guard looks for the token. A clean-looking A/B measuring
        # nothing at all. Refuse instead; the line arithmetic in `h2_extents`
        # is NOT changed for this, because it deliberately matches
        # check-guidance-coverage.js:95.
        #
        # A truncated `agents-md/stub.md` is the reachable cause today. The
        # lone-CR file the round-1 review named does NOT reach here: `_read`
        # uses `Path.read_text()`, whose universal-newline translation turns
        # `\r` into `\n` before either the parse or `text.split("\n")` sees it
        # (measured: b"# T\r\r## A\r\rbody\r" reads back as "# T\n\n## A\n\nbody\n"
        # and yields one correct extent). This refusal is the floor over the
        # whole class, not a fix for that one cause.
        raise GuidanceError(
            f"the `{mode}` payload for section {row.get('id')!r} is empty — "
            f"{inside_checkout(guidance_dir, row['file'])} yielded no "
            "section text (a file "
            "with lone-CR line endings does this). Delivering it would give "
            "the arm the magic word and no guidance, which passes the "
            "delivery guard and measures nothing.")
    if token is None:
        return payload
    if not payload.endswith("\n"):
        payload += "\n"
    return payload + token_paragraph(token)


def content_digests(guidance_dir: Path, row: dict) -> dict:
    """{"section", "rest"}: sha256 of the section's extent in its corpus, and
    of that corpus with the extent removed (the `full-minus-section` payload
    without a token). Every guidance arm records both, whatever its mode, so
    the retirement gate (harness/gates.py, `guidance_verdict`) can pool runs
    of the section on main against the rest of the guidance they ran beside
    (skills-evals#124). Pure: no token, no clock."""
    text = corpus(guidance_dir, row)
    extent = _extent_of(text, row["heading"], f"the corpus for {row['id']}")
    section = text[extent["start"]:extent["end"]]
    rest = text[:extent["start"]] + text[extent["end"]:]
    return {"section": hashlib.sha256(section.encode("utf-8")).hexdigest(),
            "rest": hashlib.sha256(rest.encode("utf-8")).hexdigest()}


# ---------------------------------------------------------------------------
# delivery — the production path, and the only impure code in this module


BEGIN_MARK = "<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance) — DO NOT EDIT -->"


def _refuse_real_config_dir(dest_dir: Path, home: Path) -> None:
    """Never, under any code path, let an arm write the developer's own
    ~/.claude/CLAUDE.md. The hook itself defaults to `$HOME/.claude` when
    CLAUDE_CONFIG_DIR is unset, so a dropped variable would silently target
    the real file; this is the belt to that braces. test/run_tests.py's
    main() is the other one: it fingerprints the real file around the WHOLE
    suite (build_suite() and the runner both) and exits 1 naming it if a run
    changed it, so a reviewer's revert-this-guard mutation fails loudly
    instead of destroying user memory.
    """
    real_home = Path(os.path.expanduser("~")).resolve()
    for path, what in ((dest_dir, "config dir"), (home, "HOME")):
        resolved = Path(path).resolve()
        if resolved == real_home or resolved == (real_home / ".claude"):
            raise GuidanceError(
                f"refusing to deliver guidance into the real {what} "
                f"({resolved}) — every arm gets a fresh scratch dir")


def deliver(guidance_dir: Path, *, scratch: Path, dest_dir: Path, home: Path,
            payload: str | bytes, timeout: int = 120,
            payload_mtime: int | None = None) -> dict:
    """Deliver `payload` the way the fleet does: the REAL fleet-memory.sh from
    the checkout, `FLEET_GUIDANCE_PAYLOAD` pointing at the assembled file,
    `CLAUDE_CONFIG_DIR` pointing at this arm's scratch dir — so the marked
    block lands in `<dest_dir>/CLAUDE.md` byte-for-byte as a real session gets
    it, hook header, version line and all. Running the real hook rather than
    imitating it is the point: an eval of the delivery path that reimplements
    the delivery path measures the imitation.

    An empty payload (`mode: none`) runs nothing at all.

    `payload` may be raw bytes (an in-place arm's frozen context, ADR 0012),
    written as they are. `payload_mtime` fixes the payload file's mtime, which
    the hook reads for its delivery stamp when the file is not committed, so
    two arms delivered the same bytes get the same block.

    S1-a-2. The timeout is checked HERE, on entry, before anything is
    spawned — not because of what the one caller passes today, but because
    this is the function that hands the value to the OS. A table of beliefs
    about where the value comes from is green the moment a caller rebinds it
    to an unvalidated key (measured: `timeout=fixture.get("deliver_timeout_s",
    2200000)` left every source-side pin green and reproduced
    `OverflowError: timeout is too large`).
    """
    check_timeout(timeout, "guidance.deliver(timeout=)", SINK_TIMEOUT_REMEDY)
    _refuse_real_config_dir(dest_dir, home)
    dest = dest_dir / "CLAUDE.md"
    if not payload:
        # The hook is not run at all, so there is no returncode to report and
        # nothing was asked of it — `installed` False here is not a failure.
        return {"bytes": 0, "verdict": None, "installed": False,
                "returncode": None, "dest": str(dest)}

    # F-1-N. The hook is EXECUTED, and it was the one path in this module
    # built from `guidance_dir` that never passed the funnel: measured on
    # f9115ce, `.claude/hooks/fleet-memory.sh` replaced by a symlink pointing
    # out of the checkout ran the OUTSIDE script and finished rc 0.
    hook = inside_checkout(guidance_dir, HOOK_REL)
    if not hook.is_file():
        raise GuidanceError(
            f"no fleet-memory hook at {hook} — the guidance subject delivers "
            "through the real hook, not a copy of it")
    payload_path = scratch / "payload.md"
    if isinstance(payload, bytes):
        payload_path.write_bytes(payload)
    else:
        payload_path.write_text(payload, encoding="utf-8")
    if payload_mtime is not None:
        os.utime(payload_path, (payload_mtime, payload_mtime))
    dest_dir.mkdir(parents=True, exist_ok=True)

    env = {name: os.environ[name] for name in ("PATH",) if name in os.environ}
    env["HOME"] = str(home)
    env["TMPDIR"] = str(scratch / "tmp")
    env["CLAUDE_CONFIG_DIR"] = str(dest_dir)
    env["FLEET_GUIDANCE_PAYLOAD"] = str(payload_path)
    Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(  # noqa: S603 — argv list, no shell
            ["bash", str(hook)], cwd=str(scratch), env=env, text=True,
            stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GuidanceError(f"could not run {hook}: {exc}") from exc

    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    verdict = lines[-1] if lines else ""
    installed = dest.is_file() and BEGIN_MARK in dest.read_text(encoding="utf-8")
    # `installed` is an OFFLINE proof that the marked block reached the config
    # dir, and `returncode` is the hook's own verdict. Both are returned so a
    # caller can refuse to spend a guard call on an arm whose delivery already
    # provably failed — a sabotaged hook that prints `fleet-guidance: current`
    # and writes nothing exits 0 and says the right words, and only these two
    # facts catch it.
    size = len(payload) if isinstance(payload, bytes) else len(payload.encode("utf-8"))
    return {"bytes": size, "verdict": verdict,
            "installed": installed, "returncode": proc.returncode,
            "dest": str(dest)}


def agent_env(*, workspace: Path, home: Path, tmpdir: Path, config_dir: Path,
              env_spec: dict | None = None) -> dict:
    """The COMPLETE environment an arm's CLI child gets. Built from nothing.

    PATH (and a few locale/shell names) by allowlist, HOME/TMPDIR/
    CLAUDE_CONFIG_DIR set explicitly, every ANTHROPIC_* variable passed
    through (eval.yml exports ANTHROPIC_AUTH_TOKEN step-locally and the CLI
    must still see it), then the fixture's own `env:` block.

    `$VAR` in a fixture's `env:` expands against THIS environment, not the
    ambient one — expanding against `os.environ` would let a fixture reach
    round the allowlist and pull an arbitrary ambient value into the child.
    `$WORKSPACE` is the temp workspace the arm actually got, same as
    run_eval.agent_env.
    """
    env = {name: os.environ[name]
           for name in (*PASSTHROUGH, *EXTRA_PASSTHROUGH, *HOSTED_PASSTHROUGH)
           if name in os.environ}
    env.update({name: value for name, value in os.environ.items()
                if name.startswith("ANTHROPIC_")})
    env["HOME"] = str(home)
    env["TMPDIR"] = str(tmpdir)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    env["WORKSPACE"] = str(workspace)
    check_env_block(env_spec, "the fixture's `env:`")
    for key, value in (env_spec or {}).items():
        if str(key) in ISOLATION_NAMES:
            raise GuidanceError(
                f"a fixture's `env:` may not set {key} — HOME, TMPDIR and "
                "CLAUDE_CONFIG_DIR ARE the per-arm isolation, and this block "
                "is applied after they are set, so a fixture naming one could "
                "point an arm at the real config dir")
        env[str(key)] = string.Template(str(value)).safe_substitute(env)
    return env


def guard_expectation(mode: str) -> bool:
    """Does this arm's probe have to SEE the token it was delivered?

    Every mode: yes. A treatment arm is delivered its payload plus the run's
    magic token; the control is delivered a DECOY token of its own. An arm
    that cannot report the token IT was handed did not read its own scratch
    user memory, and nothing measured on it means anything — which is as true
    of the control as of any treatment arm.

    (This returned `mode != "none"` while the control was delivered nothing.
    Its probe could then only ever answer "no magic word", which is also what
    it answers when the arm IS contaminated: the control's guard passed
    unconditionally.)

    Derived from the mode, not from the arm's name: an arm renamed in a
    fixture must not be able to change what its guard expects. `_validate_arms`
    is what keeps a `with_*` name from carrying `mode: none` in the first
    place, so the two can never disagree.
    """
    if mode not in MODES:
        raise GuidanceError(
            f"unknown mode {mode!r} — expected one of {', '.join(MODES)}")
    return True


def run_guard(*, workspace: Path, token: str, expected: bool, env: dict,
              setting_sources: str, model: str | None, timeout: int,
              forbidden_tokens: tuple[str, ...] = (),
              prompt: str = GUARD_PROMPT,
              disallowed_tools: str = GUARD_DISALLOWED_TOOLS) -> dict:
    """One tool-free probe against this arm's config dir and workspace.

    Reuses run_canary.run_leg — the same probe the guidance-bridge canary
    runs, parametrized with this arm's `--setting-sources` and environment.
    Two cheap calls per run (one per arm of a pair) on the preflight model.

    `token` is the token THIS arm was delivered — the run's magic token for a
    treatment arm, its own decoy for the control. `forbidden_tokens` is every
    OTHER token the run minted: the treatment token for a control arm, and
    the control's decoy for a treatment arm.

    BOTH directions, because contamination has two. The loud one is a control
    arm reached by the guidance. The quiet one is a treatment arm that reads
    the CONTROL's scratch user memory — the same per-arm isolation failure
    seen from the other side, and just as fatal to the pair, because the two
    arms are then not measuring two different contexts. Only the control was
    given a forbidden token, so the quiet one scored clean: measured, a
    treatment arm whose probe reported both the treatment token and the
    control's decoy exited 0 with every check passing.

    "Fatal to the PAIR" is exact, and narrower than it reads: the comparison
    is dead and the run exits 2 as INCONCLUSIVE, but the CLEAN partner's own
    `summary.json` is still written and still carries its `objective_checks`.
    That is a record of what that one arm did, not a score for the pair —
    nothing reads it as a result, and in CI the failed step skips the badge
    and commit steps that would publish one.

    Returns a guard block: `ok` False means the arm is INCONCLUSIVE — no
    score is written for it and the run exits 2. A probe that could not run
    at all (no credential, CLI missing) is also `ok` False: a guard that
    cannot run is never a skipped guard.
    """
    result = run_canary.run_leg(workspace, prompt, disallowed_tools,
                                model=model, timeout=timeout,
                                setting_sources=setting_sources, env=env)
    if "error" in result:
        return {"expected": expected, "observed": None, "contaminated": None,
                "ok": False, "model": model, "setting_sources": setting_sources,
                "error": {"type": result["error"], "detail": result.get("detail", "")},
                "reply": ""}
    reply = result["reply"]
    observed = token in reply
    contaminated = any(other in reply for other in forbidden_tokens if other)
    return {"expected": expected, "observed": observed,
            "contaminated": contaminated, "ok": observed == expected
            and not contaminated,
            "model": model, "setting_sources": setting_sources, "error": None,
            "reply": reply[:500]}
