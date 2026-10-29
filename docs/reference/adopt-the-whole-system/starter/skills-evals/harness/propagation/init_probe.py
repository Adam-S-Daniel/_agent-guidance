#!/usr/bin/env python3
"""The propagation primitive: read a session's loaded skill set, credential-free.

`claude -p … --output-format stream-json --verbose` emits a
`{"type":"system","subtype":"init"}` event carrying `skills`, `plugins`,
`slash_commands` and the CLI version. That event is produced entirely locally,
BEFORE the first API call — measured on CLI 2.1.231 with
`ANTHROPIC_BASE_URL=http://127.0.0.1:9`, `init.apiKeySource == "none"` and the
`api_retry` events only appear after it. So the whole assertion layer costs
$0.00, needs no credential and no network, and can therefore run on every pull
request instead of behind `eval.yml`'s OIDC.

Three measured traps this module exists to close:

1. **The init event's position is NOT contractual.** Measured on the same CLI
   build: index 0 under a scrubbed `env -i` environment, index 4 under the
   ambient one (behind `active_goal`, `autocompact_state` and two
   `commands_changed` events). Select on `(type, subtype)`, never an index,
   never a regex over the stream. Selecting on `type == "system"` alone picks
   `commands_changed`, which has no `skills` key at all.

2. **A scratch `HOME` is necessary but NOT sufficient.** Same scratch HOME, two
   runs: with `env -i PATH HOME TMPDIR` the session loaded 16 skills and wrote
   nothing account-shaped; inheriting the ambient environment it loaded 35 — 17
   account-store skills re-synced in, and `$HOME/.claude/skills/synced/` created
   under the scratch dir. Unsetting `CLAUDE_CODE_SYNC_SKILLS` alone does not
   stop it. Hence PASSTHROUGH: an env ALLOWLIST, built up from nothing. Never
   `{**os.environ, ...}`.

3. **A probe launched from inside a Claude Code session inherits
   `CLAUDE_CODE_REMOTE_SESSION_ID` / `CLAUDE_CODE_ENTRYPOINT=remote`**, which
   are exactly what agentskills' `skills-bootstrap.sh` surface guard keys on. A
   bootstrap-hook control leg that inherits them installs the skills it was
   supposed to decline to install, and the "expect invisible" assertion then
   passes for entirely the wrong reason. The allowlist scrubs them.

`--disallowedTools …,Skill` does NOT suppress the init event's skill list
(measured: same names with and without; only `tools` loses `"Skill"`), so an
init-event probe can disallow every tool and stay fully sighted. That is a real
difference from a BEHAVIOURAL probe like `run_canary.py`, where tool controls
are load-bearing. Scope flags are still refused by default here (see `probe`)
because `--setting-sources project` DOES change the answer — it drops
user-level skills — and an arm that acquires one by accident measures a
different surface than the one it names.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


# S1-a-2. The one timeout predicate lives in harness/guidance.py, beside the
# ceiling and the error type it raises, and every function in this file that
# hands a timeout to a subprocess API calls it on entry. `guidance` is
# imported inside those functions rather than at module scope — guidance.py
# imports run_canary, and this package is imported from run_eval/
# run_propagation while guidance is still initialising — so the directory it
# lives in has to be reachable from here whichever entry point got us in.
_HARNESS_DIR = str(Path(__file__).resolve().parent.parent)
if _HARNESS_DIR not in sys.path:
    sys.path.insert(0, _HARNESS_DIR)

# How long `probe` waits for a killed child to actually die. Named rather than
# inlined so the sink check and the `timeout=` argument are provably the same
# value (the pin compares the two expressions, not two beliefs about them).
KILL_WAIT_TIMEOUT_S = 10

# Everything the child is allowed to see. An allowlist, not the ambient
# environment — see trap 2 and trap 3 in the module docstring. Adding a name
# here widens what every arm inherits, so add deliberately.
PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SHELL", "USER", "NODE_PATH")

# Test seam. The hermetic suite drives `test/fake-claude-init` and
# `test/fixtures/fake-skills-bootstrap.sh`, whose mutation modes are selected by
# environment variable — and the allowlist is precisely what would otherwise
# stop those variables reaching the child, leaving every mutation silently
# unmutated. Tests patch this; production leaves it empty, and
# `test_extra_passthrough_is_empty_in_production` asserts the committed value
# so a debugging widening cannot be left behind.
EXTRA_PASSTHROUGH = ()

# Black-holes the Anthropic API. Port 9 (discard) refuses immediately rather
# than hanging, so a probe that somehow reaches the API fails fast instead of
# burning its timeout. Nothing this module asserts on is served from there.
BLACKHOLE_BASE_URL = "http://127.0.0.1:9"

# Never inherited even if a caller passes them in `env`: a leaked credential
# would make a "$0.00, credential-free" claim false.
CREDENTIAL_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                    "ANTHROPIC_FEDERATION_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")

# Flags that change WHICH surface the init event describes. Refused unless the
# caller says it means it, so an arm cannot silently measure a narrower surface
# than the one it claims to.
SCOPE_FLAGS = ("--setting-sources", "--disallowedTools", "--allowedTools")

# `commands_changed` tags account-store skills with this suffix. Measured: 17
# of 63 commands carried it, exactly the account set. It is a FREE third
# channel discriminator — but only when that event is emitted at all, which it
# is not on a surface with no account (see `ProbeFacts.commands_seen`).
ACCOUNT_SUFFIX = " (claude.ai sync)"

# Claude Code >= 2.1.269 names a skill synced from claude.ai
# `anthropic-skills:<name>` (read in the 2.1.269 release notes, not measured
# here; #196), so a `:` in the name no longer means a marketplace plugin. This
# prefix is a fallback when `commands_changed` is absent and the init event
# does not list an installed plugin with that namespace.
ACCOUNT_NAMESPACE = "anthropic-skills:"

# Where the account store lands. Its mere existence under a leg's scratch HOME
# means the account channel reached that leg — the discriminator that stays
# available when `commands_changed` is not emitted.
ACCOUNT_SENTINEL = Path(".claude") / "skills" / "synced"


class ProbeError(RuntimeError):
    """The probe could not observe the surface at all.

    Deliberately distinct from an assertion failure: "the CLI did not start"
    and "the skills did not arrive" must never collapse into one red. Callers
    map this to exit 2, never exit 1.
    """


@dataclass
class ProbeFacts:
    """Everything one probe run observed. Pure data — no assertions here."""

    init: dict
    commands: dict = field(default_factory=dict)  # name -> description
    commands_seen: bool = False
    init_index: int = -1
    events: list = field(default_factory=list)  # (type, subtype) in arrival order
    unparseable_lines: int = 0
    home: Path | None = None
    cwd: Path | None = None
    argv: list = field(default_factory=list)

    @property
    def skills(self) -> list:
        """`init.skills` verbatim — a LIST, and it can carry duplicates.

        Measured: seeding the same skill name into both `~/.claude/skills` and
        the project's `.claude/skills` yields TWO entries of that name. So
        `len(init.skills)` is not a count of distinct skills and set equality
        silently discards the multiplicity. Assertions that care use
        `skill_counts()`.
        """
        return list(self.init.get("skills") or [])

    def skill_counts(self) -> Counter:
        return Counter(self.skills)

    @property
    def version(self) -> str:
        return str(self.init.get("claude_code_version") or "unknown")

    @property
    def plugins(self) -> list:
        return list(self.init.get("plugins") or [])

    def plugin_path(self, bundle: str) -> Path | None:
        """Where the CLI actually resolved `bundle` to, per the init event.

        Preferred over guessing `~/.claude/plugins/cache/...`: this is the tree
        the session really loaded, which is the thing worth digesting.
        """
        for plugin in self.plugins:
            if isinstance(plugin, dict) and plugin.get("name") == bundle:
                path = plugin.get("path")
                if path:
                    return Path(path)
        return None

    def account_named_skills(self) -> set:
        """Skill names this run can attribute to the claude.ai account store.

        Empty when `commands_changed` was not emitted — which is NOT the same
        claim as "no account skills". `commands_seen` is what distinguishes
        them, and every guard block prints it.
        """
        return {name for name, desc in self.commands.items()
                if str(desc).endswith(ACCOUNT_SUFFIX)}

    def account_sentinel_present(self) -> bool:
        """Did the account store reach this leg's HOME on disk?

        Available on every run, credential or not, so this — not the
        `commands_changed` suffix — is the load-bearing account discriminator.
        """
        return self.home is not None and (self.home / ACCOUNT_SENTINEL).exists()


def attribute(facts: ProbeFacts) -> dict:
    """name -> channel, for every skill the init event carries.

    - `"account"` — carries the ` (claude.ai sync)` suffix in `commands_changed`,
      or uses the `anthropic-skills:` namespace when no plugin by that name
      appears in `init.plugins`. The suffix is authoritative even if plugin
      evidence also exists. Without a suffix or observed plugin, the namespace
      alone is ambiguous; this fallback follows the account naming convention
      reported for CLI >= 2.1.269 (#196).
    - `"plugin"`  — namespaced (`my:workflow-path-audit`), i.e. marketplace.
      An observed plugin named `anthropic-skills` wins over the namespace
      fallback when no account suffix was emitted.
    - `"local"`   — everything else: `~/.claude/skills`, the project's
      `.claude/skills`, or a CLI built-in. The init event genuinely cannot
      separate those three; the ARM separates them, by controlling what exists
      where before the spawn.
    """
    account = facts.account_named_skills()
    plugins = {plugin["name"] for plugin in facts.plugins
               if isinstance(plugin, dict)
               and isinstance(plugin.get("name"), str)}
    out = {}
    for name in facts.skills:
        if name in account:
            out[name] = "account"
        elif (name.startswith(ACCOUNT_NAMESPACE)
              and "anthropic-skills" not in plugins):
            out[name] = "account"
        elif ":" in name:
            out[name] = "plugin"
        else:
            out[name] = "local"
    return out


def build_env(*, home: Path, tmpdir: Path, extra: dict | None = None) -> dict:
    """The child's COMPLETE environment. Built up from nothing, never inherited."""
    env = {name: os.environ[name]
           for name in (*PASSTHROUGH, *EXTRA_PASSTHROUGH) if name in os.environ}
    env["HOME"] = str(home)
    env["TMPDIR"] = str(tmpdir)
    env["ANTHROPIC_BASE_URL"] = BLACKHOLE_BASE_URL
    env.update(extra or {})
    for name in CREDENTIAL_NAMES:
        env.pop(name, None)
    return env


def claude_bin() -> str:
    """Same convention as run_eval/run_canary: $CLAUDE_BIN, else `claude`."""
    return os.environ.get("CLAUDE_BIN", "claude")


def probe(*, cwd: Path, home: Path, tmpdir: Path, env_extra: dict | None = None,
          extra_argv=(), model: str = "claude-haiku-5-5", timeout: int = 120,
          allow_scope_flags: bool = False) -> ProbeFacts:
    """Spawn the CLI, read the init event, kill the child. No API call is made.

    Raises ProbeError (never returns a half-answer) when the CLI cannot be
    started, emits no init event, or emits one whose `skills` is missing or not
    a list. An absent `skills` key must NEVER read as "zero skills delivered" —
    that is the vacuous green this whole harness exists to prevent.
    """
    import guidance  # noqa: PLC0415 — cycle-avoidance, see the preamble
    guidance.check_timeout(KILL_WAIT_TIMEOUT_S,
                           "init_probe.probe(<popen>.wait timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    for flag in extra_argv:
        if not allow_scope_flags and str(flag).split("=", 1)[0] in SCOPE_FLAGS:
            raise ProbeError(
                f"{flag} changes which surface the init event describes; pass "
                "allow_scope_flags=True if the arm really means to narrow it")

    argv = [claude_bin(), "-p", "propagation-probe",
            "--output-format", "stream-json", "--verbose",
            "--model", model, *[str(a) for a in extra_argv]]
    env = build_env(home=home, tmpdir=tmpdir, extra=env_extra)

    try:
        proc = subprocess.Popen(  # noqa: S603 — argv list, no shell
            argv, cwd=str(cwd), env=env, text=True, bufsize=1,
            stdin=subprocess.DEVNULL,  # else the CLI waits on a tty for ~3s
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise ProbeError(f"could not start {argv[0]!r}: {exc}") from exc

    facts = ProbeFacts(init={}, home=home, cwd=cwd, argv=argv)
    found = None
    try:
        for index, line in enumerate(proc.stdout):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Never a regex fallback. A line that is not JSON matches
                # nothing, is counted, and is reported in the guard block.
                facts.unparseable_lines += 1
                continue
            if not isinstance(event, dict):
                facts.unparseable_lines += 1
                continue
            facts.events.append((event.get("type"), event.get("subtype")))
            if event.get("type") != "system":
                continue
            if event.get("subtype") == "commands_changed":
                facts.commands_seen = True
                for command in event.get("commands") or []:
                    if isinstance(command, dict) and command.get("name"):
                        facts.commands[command["name"]] = command.get("description", "")
            elif event.get("subtype") == "init":
                found = event
                facts.init_index = index
                break
    finally:
        proc.kill()
        try:
            proc.wait(timeout=KILL_WAIT_TIMEOUT_S)
        except subprocess.TimeoutExpired:  # pragma: no cover — kill(2) not honoured
            pass
        stderr = (proc.stderr.read() or "")[-2000:] if proc.stderr else ""
        for stream in (proc.stdout, proc.stderr):
            if stream:
                stream.close()

    if found is None:
        raise ProbeError(
            "no system/init event in the stream — the CLI failed to start, or "
            "stream-json's event shape changed. This is a PROBE fault, not a "
            f"propagation failure.\n  argv: {' '.join(argv)}\n"
            f"  events seen: {facts.events or '(none)'}\n"
            f"  unparseable lines: {facts.unparseable_lines}\n"
            f"  stderr tail: {stderr.strip() or '(empty)'}")

    skills = found.get("skills")
    if not isinstance(skills, list):
        raise ProbeError(
            "the init event carries no `skills` LIST — refusing to read that as "
            "'zero skills delivered', which would make every absence assertion "
            f"pass vacuously.\n  got: {type(skills).__name__} {skills!r}\n"
            f"  init keys: {sorted(found)}")

    facts.init = found
    return facts
