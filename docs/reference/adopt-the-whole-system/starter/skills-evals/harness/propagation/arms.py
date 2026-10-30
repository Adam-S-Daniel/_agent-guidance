#!/usr/bin/env python3
"""Tier-2 propagation arms: one per delivery channel, each with a control leg.

Every arm runs the SAME probe twice against identically-materialised scratch
state — once before the channel delivers anything (the control leg), once
after — and asserts on the DIFFERENCE. Nothing here ever asserts equality over
`init.skills` as a whole: the CLI's built-in skill set is not stable across
versions or even across runs (measured: a `schedule` entry present in one run
and absent in the next), so a whole-set assertion would go red for reasons that
have nothing to do with propagation, and would then get muted.

Anti-vacuity is structural, not a convention:

* **The differential.** `delivered = counts(arm) - counts(control)`, compared
  for EXACT SET EQUALITY against what the lock says the channel owes. An arm
  that merely counted "more skills than before" would pass on any plugin.
* **Guards.** Every leg records a guard block — HOME isolation, env allowlist,
  the bootstrap surface variables this leg intends to carry, account-store
  absence, a non-empty built-in floor, the init event's index, unparseable line
  count. Any guard not OK makes the
  arm INCONCLUSIVE (exit 2), never PASS and never FAIL. The arm that expects
  to find NOTHING is the one most able to pass on a dead CLI, so it is the one
  that most needs a floor.
* **Negative controls with a structural expectation.** The bootstrap hook's
  control leg does not merely assert "the skills are not visible" — that also
  holds if the hook crashed, if the lock were missing, or if the clone 404'd.
  It asserts the `skipped — durable session…, marketplace install is
  authoritative` shape (an interpolated diagnostic clause in the middle is
  allowed — see `HOOK_SKIPPED_RE`) AND an empty `$HOME/.claude/skills`.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import init_probe
from .init_probe import ProbeError, ProbeFacts


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

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"

# The three environment variables acsi-agentskills' skills-bootstrap.sh surface
# guard keys on. Absent from a leg unless that leg declares one: a control leg
# that inherits CLAUDE_CODE_REMOTE_SESSION_ID from the session running the
# probe arms the hook it is trying to observe declining to fire.
SURFACE_VARS = ("CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT",
                "SKILLS_BOOTSTRAP_FORCE")

# `skills: 9/9 from file:///…/acsi-agentskills@9d024c3 — OK`. The two counts
# must be the SAME number (backreference), so "8/9 — OK" can never match.
HOOK_OK_RE = re.compile(r"^skills: (\d+)/\1 from \S+@[0-9a-f]{7,40} — OK$")

# `skills: skipped — durable session, marketplace install is authoritative` —
# structural, not exact-equality, on purpose. agentskills 24977ed
# ("Make the skip verdict name the values it decided from") inserted an
# interpolated diagnostic between the reason and the marketplace clause:
# `skills: skipped — durable session (entrypoint=unset, no remote session
# id), marketplace install is authoritative`. That diagnostic is
# informational and must be allowed to vary or grow further; what has to
# hold is the two clauses either side of it — WHY the hook declined (a
# durable session, not some other reason) and WHAT is authoritative instead
# (the marketplace install, still named, still present). A parenthesised
# `(...)` block with no nested parens is accepted between the two literal
# clauses; nothing else is.
HOOK_SKIPPED_RE = re.compile(
    r"^skills: skipped — durable session(?: \([^()]*\))?, "
    r"marketplace install is authoritative$")


def hook_declined_for_durable_session(verdict: str) -> bool:
    """True iff `verdict` is a durable-session skip naming the marketplace
    install as authoritative, with any single interpolated diagnostic
    clause in between tolerated. See `HOOK_SKIPPED_RE`.
    """
    return HOOK_SKIPPED_RE.match(verdict) is not None


class ArmError(RuntimeError):
    """Arm setup failed — a probe fault (exit 2), not an assertion failure."""


# ---------------------------------------------------------------------------
# lock + digest


def load_lock(path: Path) -> dict:
    """Parse a `skills.lock`. Never a regex — it is JSON, so json.loads it."""
    try:
        lock = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArmError(f"could not read the lock at {path}: {exc}") from exc
    skills = lock.get("skills")
    if not isinstance(skills, dict) or not skills:
        raise ArmError(
            f"{path} declares no skills — a lock that expects nothing can "
            "never fail, so every arm built on it would pass vacuously")
    for key in skills:
        if "/" not in key:
            raise ArmError(f"{path}: skill key {key!r} is not '<bundle>/<skill>'")
    return lock


def lock_pairs(lock: dict) -> list:
    """[(bundle, skill), …] in lock order."""
    return [tuple(key.split("/", 1)) for key in lock["skills"]]


def digest_skill_dir(path: Path) -> str:
    """sha256 of a skill directory, byte-for-byte compatible with the lock.

    THIRD copy of this algorithm, deliberately not an independent one: the
    other two are `digest_skill_dir` in acsi-agentskills'
    `scripts/generate_skills_lock.py` and `digest_dir` in its
    `.claude/hooks/skills-bootstrap.sh`. The manifest is
    `<relpath>\\0<sha256 of bytes>\\n` per file, sorted by relpath, hashed.
    Directories and broken symlinks carry no bytes and are skipped.

    Drift between the copies is caught immediately and loudly: the
    `plugin-marketplace` arm digests the real registry tree and compares to the
    real lock, so a disagreement of even one byte reds that arm. There is no
    quiet failure mode here to guard against separately.
    """
    root = Path(path).resolve()
    if not root.is_dir():
        raise ArmError(f"not a directory: {path}")
    entries = sorted(
        (candidate.relative_to(root).as_posix(), candidate)
        for candidate in root.rglob("*") if candidate.is_file())
    manifest = "".join(
        f"{relpath}\0{hashlib.sha256(file_path.read_bytes()).hexdigest()}\n"
        for relpath, file_path in entries)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


# agentskills#87 relabelled every lock digest `sha256:<hex>` where it used to
# write bare `<hex>`. This arm compares a digest it computes itself against the
# one the lock records, so that serialization change broke the comparison for
# all 8 skills at once — `got a6f71e6d… want sha256:a6f71e6d…` — while the
# CONTENT was identical. The lock and this file are two of the three
# independent copies of the same digest (see digest_skill_dir above); the third
# moved and this one did not.
#
# Accept BOTH shapes rather than requiring the label, because both are
# currently correct: a lock re-pinned since that change carries the prefix, and
# one that has not been re-pinned yet still carries bare hex (measured
# 2026-08-19: example-site-a and example-site-b labelled, example-site-platform,
# GHA-bench and _agent-guidance not). Requiring the prefix would just move the
# false failure onto the repos that have not been re-pinned.
#
# Deliberately NOT a general "strip anything before a colon": only the exact
# algorithm label this generator writes is stripped, so a genuinely malformed
# digest still fails the comparison instead of being normalised into agreement.
def unlabelled_digest(recorded: str) -> str:
    """The bare hex of a lock digest, with or without its `sha256:` label."""
    prefix = "sha256:"
    return recorded[len(prefix):] if recorded.startswith(prefix) else recorded


# ---------------------------------------------------------------------------
# guards + findings


@dataclass
class Guard:
    name: str
    ok: bool
    detail: str


@dataclass
class Finding:
    assertion: str
    ok: bool
    detail: str


@dataclass
class ArmResult:
    name: str
    status: str
    guards: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    error: str | None = None
    #: What the arm's own leg ran on (#202): the init event's
    #: `claude_code_version` and `model`, or None when the arm never probed
    #: or the event carried no usable value. See `probe_versions`.
    harness_version: str | None = None
    model: str | None = None

    def render(self) -> str:
        lines = [f"{self.status} {self.name}"]
        for note in self.notes:
            lines.append(f"  note: {note}")
        for finding in self.findings:
            mark = "ok  " if finding.ok else "FAIL"
            lines.append(f"  [{mark}] {finding.assertion}: {finding.detail}")
        if self.error:
            lines.append(f"  probe fault: {self.error}")
        if self.status != PASS:
            # The guard block prints on EVERY non-pass, so a red is never
            # reported without the evidence that says whether to believe it.
            lines.append("  guards:")
            for guard in self.guards:
                lines.append(f"    [{'ok  ' if guard.ok else 'BAD '}] "
                             f"{guard.name}: {guard.detail}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "error": self.error,
            "notes": list(self.notes),
            "guards": [vars(g) for g in self.guards],
            "findings": [vars(f) for f in self.findings],
            "harness_version": self.harness_version,
            "model": self.model,
        }


def leg_guards(label: str, facts: ProbeFacts, *, scratch: Path,
               declared_env: dict, lock: dict, expect_surface=()) -> list:
    """The guard block for one leg. See the module docstring.

    `expect_surface` is asserted as an EXACT set, in both directions. The
    bootstrap hook's positive leg must carry `SKILLS_BOOTSTRAP_FORCE` and its
    control leg must carry none — and an arm that lost its force variable would
    otherwise quietly become a second copy of the control leg and pass. The
    ambient environment cannot supply one of these (they are not in
    PASSTHROUGH, which `test_surface_variables_are_not_in_the_allowlist`
    locks); what this catches is an edit to the ARM.
    """
    declared = dict(declared_env or {})
    allowed = set(init_probe.PASSTHROUGH) | set(init_probe.EXTRA_PASSTHROUGH)
    allowed |= {"HOME", "TMPDIR", "ANTHROPIC_BASE_URL"} | set(declared)
    actual = init_probe.build_env(home=facts.home, tmpdir=scratch / "tmp",
                                  extra=declared)
    unexpected = sorted(set(actual) - allowed)
    surface_present = {v for v in SURFACE_VARS if v in actual}
    surface_ok = surface_present == set(expect_surface)
    lock_names = {skill for _, skill in lock_pairs(lock)}
    floor = [name for name in facts.skills
             if ":" not in name and name not in lock_names]

    return [
        Guard(f"{label}/home-isolated",
              facts.home is not None and scratch in facts.home.parents,
              f"HOME={facts.home}"),
        Guard(f"{label}/env-allowlist", not unexpected,
              "child env is the allowlist plus this arm's declared extras "
              f"{sorted(declared)}" if not unexpected
              else f"undeclared variables reached the child: {unexpected}"),
        Guard(f"{label}/surface-vars", surface_ok,
              f"carries exactly {sorted(surface_present) or 'no'} bootstrap "
              "surface variable(s), as this leg intends" if surface_ok
              else f"carries {sorted(surface_present)}, expected "
                   f"{sorted(expect_surface)} — this leg is not measuring the "
                   "surface it claims to"),
        Guard(f"{label}/account-store-absent", not facts.account_sentinel_present(),
              f"{init_probe.ACCOUNT_SENTINEL} absent under this leg's HOME"
              if not facts.account_sentinel_present()
              else "the claude.ai account store reached this leg — the env "
                   "allowlist leaked and every 'no account skills' assertion "
                   "in this arm is now meaningless"),
        Guard(f"{label}/account-names-absent", not facts.account_named_skills(),
              f"commands_changed emitted={facts.commands_seen}; "
              f"account-suffixed names={sorted(facts.account_named_skills())}"),
        Guard(f"{label}/builtin-floor", bool(floor),
              f"{len(floor)} skill(s) present that the lock does not own "
              f"(the CLI started with a real skill set)"),
        Guard(f"{label}/stream-shape", facts.unparseable_lines == 0,
              f"init at stream index {facts.init_index}, "
              f"{facts.unparseable_lines} unparseable line(s), "
              f"CLI {facts.version}"),
    ]


# ---------------------------------------------------------------------------
# arm scaffolding


@dataclass
class Scratch:
    root: Path
    home: Path
    ws: Path
    tmp: Path
    proj: Path


def make_scratch(root: Path, name: str) -> Scratch:
    """A leg's private HOME/cwd/TMPDIR, and it must be FRESH.

    Strict about the directory already existing, because a reused one is a
    silently wrong control leg: an arm running a second time under the same
    root would find the previous run's marketplace install already in place,
    its "before" probe would see the namespaced skills, and its negative
    control would fire — reporting a failure that is really this harness's own
    state leaking between runs. That bug shipped once here and the self-test
    passed on it, for entirely the wrong reason.
    """
    base = root / name
    if base.exists():
        raise ArmError(
            f"scratch directory {base} already exists — a leg must start from a "
            "clean HOME, or its control leg measures the previous run")
    scratch = Scratch(root=base, home=base / "home", ws=base / "ws",
                      tmp=base / "tmp", proj=base / "proj")
    for path in (scratch.home, scratch.ws, scratch.tmp, scratch.proj):
        path.mkdir(parents=True)
    return scratch


def _probe(scratch: Scratch, *, env_extra: dict | None = None,
           timeout: int) -> ProbeFacts:
    return init_probe.probe(cwd=scratch.ws, home=scratch.home,
                            tmpdir=scratch.tmp, env_extra=env_extra,
                            timeout=timeout)


def _delivered(control: ProbeFacts, arm: ProbeFacts) -> Counter:
    """What this arm's channel added, as a multiset. Negative counts dropped."""
    return arm.skill_counts() - control.skill_counts()


def _delivery_finding(expected: set, delivered: Counter, channel: str) -> Finding:
    got = set(delivered)
    if got == expected:
        return Finding("delivered-set", True,
                       f"{len(expected)} skill(s) delivered on channel "
                       f"{channel}, exactly the set the lock names")
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    detail = []
    if missing:
        detail.append(f"missing: {missing}")
    if extra:
        # A rogue delivery is a propagation failure too: something put a skill
        # into this session that the lock never declared.
        detail.append(f"undeclared extras: {extra}")
    return Finding("delivered-set", False,
                   f"channel {channel} delivered {sorted(got)}; " + "; ".join(detail))


def _run_hook(hook: Path, *, scratch: Scratch, env_extra: dict,
              timeout: int) -> str:
    """Run skills-bootstrap.sh and return its `additionalContext` verdict."""
    import guidance  # noqa: PLC0415 — cycle-avoidance, see the preamble
    guidance.check_timeout(timeout, "arms._run_hook(timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    env = init_probe.build_env(home=scratch.home, tmpdir=scratch.tmp,
                               extra=env_extra)
    try:
        proc = subprocess.run(  # noqa: S603 — argv list, no shell
            ["bash", str(hook)], cwd=str(scratch.ws), env=env, text=True,
            stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ArmError(f"could not run the bootstrap hook: {exc}") from exc
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ArmError(
            "the bootstrap hook emitted no parseable JSON verdict "
            f"(rc={proc.returncode}): {proc.stdout[:400]!r} "
            f"stderr={proc.stderr[-400:]!r}") from exc
    verdict = (payload.get("hookSpecificOutput") or {}).get("additionalContext")
    if not isinstance(verdict, str):
        raise ArmError(f"hook verdict was not a string: {payload!r}")
    return verdict


def _seed_skill_dirs(dest: Path, registry: Path, lock: dict) -> None:
    """Copy every locked skill from a registry checkout into `dest`."""
    for bundle, skill in lock_pairs(lock):
        src = registry / "plugins" / bundle / "skills" / skill
        if not src.is_dir():
            raise ArmError(f"registry has no {bundle}/{skill} at {src}")
        shutil.copytree(src, dest / skill, dirs_exist_ok=True)


# ---------------------------------------------------------------------------
# the arms


def arm_clean_room(ctx) -> ArmResult:
    """Nothing is installed anywhere: the lock's skills must be absent.

    The arm most able to pass on a dead CLI, so it carries the built-in floor
    guard and asserts on NAMES rather than on a count.
    """
    scratch = make_scratch(ctx.root, "clean-room")
    facts = _probe(scratch, timeout=ctx.timeout)
    guards = leg_guards("clean-room", facts, scratch=scratch.root,
                        declared_env={}, lock=ctx.lock)

    names = set(facts.skills)
    lock_bare = {skill for _, skill in lock_pairs(ctx.lock)}
    lock_ns = {f"{bundle}:{skill}" for bundle, skill in lock_pairs(ctx.lock)}
    bundles = {bundle for bundle, _ in lock_pairs(ctx.lock)}
    namespaced = {name for name in names
                  if name.split(":", 1)[0] in bundles and ":" in name}

    findings = [
        Finding("clean-room/no-locked-skills", not (names & (lock_bare | lock_ns)),
                f"leaked: {sorted(names & (lock_bare | lock_ns))}"
                if names & (lock_bare | lock_ns)
                else "none of the lock's skills are visible"),
        Finding("clean-room/no-lock-namespace", not namespaced,
                f"namespaced skills present: {sorted(namespaced)}" if namespaced
                else f"no {sorted(bundles)} namespace in the session"),
    ]
    return _finish("clean-room", guards, findings, facts)


def arm_project_mirror(ctx) -> ArmResult:
    """A repo-committed `.claude/skills/` mirror in the working directory."""
    scratch = make_scratch(ctx.root, "project-mirror")
    control = _probe(scratch, timeout=ctx.timeout)
    _seed_skill_dirs(scratch.ws / ".claude" / "skills", ctx.registry, ctx.lock)
    facts = _probe(scratch, timeout=ctx.timeout)

    guards = (leg_guards("control", control, scratch=scratch.root,
                         declared_env={}, lock=ctx.lock)
              + leg_guards("arm", facts, scratch=scratch.root,
                           declared_env={}, lock=ctx.lock))
    expected = {skill for _, skill in lock_pairs(ctx.lock)}
    findings = [_delivery_finding(expected, _delivered(control, facts), "local")]
    return _finish("project-mirror", guards, findings, facts)


def arm_plugin_marketplace(ctx) -> ArmResult:
    """The developer-laptop channel: `claude plugin install <bundle>@<market>`.

    Asserts the namespaced set AND the per-skill content digests of the tree
    the CLI actually resolved. It deliberately does NOT compare the plugin
    cache's `gitCommitSha` to the lock's `ref`: measured on this box they
    differ (`20095c6…` vs `9d024c34…`) while every content digest matches,
    because the lock pins the commit whose CONTENT it hashed and the plugin
    cache records the checkout's HEAD. That assertion would ship red on day
    one and be muted by week one.
    """
    import guidance  # noqa: PLC0415 — cycle-avoidance, see the preamble
    guidance.check_timeout(ctx.timeout, "arms.arm_plugin_marketplace(timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    scratch = make_scratch(ctx.root, "plugin-marketplace")
    control = _probe(scratch, timeout=ctx.timeout)
    if any(":" in name for name in control.skills):
        # The negative control: a namespaced skill BEFORE any install means the
        # arm would be reading a pre-existing plugin, not its own install.
        return _finish("plugin-marketplace",
                       leg_guards("control", control, scratch=scratch.root,
                                  declared_env={}, lock=ctx.lock),
                       [Finding("plugin/negative-control", False,
                                "namespaced skills were present before the "
                                f"install: {sorted(n for n in control.skills if ':' in n)}")],
                       control)

    marketplace = ctx.registry.resolve()
    # EVERY bundle the lock names, in lock order. The lock is the declared
    # expectation this arm compares against, and acsi-agentskills' lock spans
    # two plugins; installing only `ctx.bundle` would report the other
    # plugin's skills as missing although the channel delivered them fine.
    bundles = list(dict.fromkeys(bundle for bundle, _ in lock_pairs(ctx.lock)))
    installs = [["plugin", "install", f"{bundle}@{marketplace.name}"]
                for bundle in bundles]
    for argv in (["plugin", "marketplace", "add", str(marketplace)], *installs):
        env = init_probe.build_env(home=scratch.home, tmpdir=scratch.tmp)
        proc = subprocess.run(  # noqa: S603 — argv list, no shell
            [init_probe.claude_bin(), *argv], cwd=str(scratch.ws), env=env,
            text=True, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=ctx.timeout)
        if proc.returncode != 0:
            raise ArmError(f"`claude {' '.join(argv)}` failed "
                           f"({proc.returncode}): {proc.stderr[-400:]}")

    facts = _probe(scratch, timeout=ctx.timeout)
    guards = (leg_guards("control", control, scratch=scratch.root,
                         declared_env={}, lock=ctx.lock)
              + leg_guards("arm", facts, scratch=scratch.root,
                           declared_env={}, lock=ctx.lock))

    expected = {f"{bundle}:{skill}" for bundle, skill in lock_pairs(ctx.lock)}
    findings = [_delivery_finding(expected, _delivered(control, facts), "plugin")]
    # Deliberately no `gitCommitSha` check anywhere in this arm: see the docstring (#192).

    roots = {bundle: facts.plugin_path(bundle) for bundle in bundles}
    unresolved = [bundle for bundle, root in roots.items() if root is None]
    if unresolved:
        findings.append(Finding("plugin/digest", False,
                                f"the init event names no plugin "
                                f"{', '.join(repr(b) for b in unresolved)}; "
                                f"plugins={facts.plugins}"))
    else:
        drift = []
        for bundle, skill in lock_pairs(ctx.lock):
            want = unlabelled_digest(ctx.lock["skills"][f"{bundle}/{skill}"])
            path = roots[bundle] / "skills" / skill
            got = digest_skill_dir(path) if path.is_dir() else "ABSENT"
            if got != want:
                drift.append(f"{bundle}/{skill}: got {got[:12]} want {want[:12]}")
        where = ", ".join(str(root) for root in roots.values())
        findings.append(Finding(
            "plugin/digest", not drift,
            f"{len(ctx.lock['skills'])} skill digest(s) match skills.lock "
            f"(resolved at {where})" if not drift else "; ".join(drift)))
    return _finish("plugin-marketplace", guards, findings, facts)


def arm_bootstrap_hook(ctx) -> ArmResult:
    """The ephemeral/cloud channel: acsi-agentskills' skills-bootstrap SessionStart hook.

    Two legs, and the NEGATIVE one is the point. The hook no-ops unless the
    surface is ephemeral, and a GitHub runner is not — so a control leg that
    only asserted "the skills are not visible" would pass for entirely the
    wrong reason, and would keep passing if the hook crashed, if the lock went
    missing, or if the clone 404'd. It asserts the literal verdict sentence and
    an empty `$HOME/.claude/skills`.
    """
    (ctx.root / "hook-lock").mkdir(parents=True, exist_ok=True)
    shutil.copy(ctx.lock_path, ctx.root / "hook-lock" / "skills.lock")

    # --- negative control: no surface variable, so the hook must decline ---
    control_scratch = make_scratch(ctx.root, "bootstrap-control")
    control_env = {"AGENTSKILLS_REPO": ctx.registry_url,
                   "CLAUDE_PROJECT_DIR": str(ctx.root / "hook-lock")}
    control_verdict = _run_hook(ctx.hook, scratch=control_scratch,
                                env_extra=control_env, timeout=ctx.timeout)
    control_dir = control_scratch.home / ".claude" / "skills"
    control_installed = sorted(p.name for p in control_dir.iterdir()) \
        if control_dir.is_dir() else []
    control = _probe(control_scratch, timeout=ctx.timeout)

    # --- positive leg: forced ephemeral surface ---
    scratch = make_scratch(ctx.root, "bootstrap-hook")
    before = _probe(scratch, timeout=ctx.timeout)
    env = dict(control_env, SKILLS_BOOTSTRAP_FORCE="1")
    verdict = _run_hook(ctx.hook, scratch=scratch, env_extra=env,
                        timeout=ctx.timeout)
    facts = _probe(scratch, timeout=ctx.timeout)

    # `declared_env` here describes the HOOK invocation, not the probe's: the
    # surface guard is what governs the hook, so that is the environment worth
    # asserting on. The control leg must carry no surface variable; the
    # positive leg must carry exactly the one that forces the hook to act.
    guards = (leg_guards("control", control, scratch=control_scratch.root,
                         declared_env=control_env, lock=ctx.lock,
                         expect_surface=())
              + leg_guards("arm", facts, scratch=scratch.root,
                           declared_env=env, lock=ctx.lock,
                           expect_surface=("SKILLS_BOOTSTRAP_FORCE",)))

    match = HOOK_OK_RE.match(verdict)
    total = int(match.group(1)) if match else None
    expected = {skill for _, skill in lock_pairs(ctx.lock)}
    findings = [
        Finding("hook/verdict", bool(match) and total == len(ctx.lock["skills"]),
                f"verdict: {verdict!r}" + ("" if match and total == len(ctx.lock["skills"])
                                           else f" (expected N/N — OK with "
                                                f"N={len(ctx.lock['skills'])})")),
        _delivery_finding(expected, _delivered(before, facts), "local"),
        Finding("hook/control-verdict",
                hook_declined_for_durable_session(control_verdict),
                f"control verdict: {control_verdict!r}"
                + ("" if hook_declined_for_durable_session(control_verdict)
                   else " (expected a durable-session skip naming the "
                        "marketplace install as authoritative, matching "
                        f"{HOOK_SKIPPED_RE.pattern!r})")),
        Finding("hook/control-installed-nothing", not control_installed,
                "the declining hook wrote no skills into $HOME/.claude/skills"
                if not control_installed
                else f"the control leg installed {control_installed} — the "
                     "surface guard did not fire"),
    ]
    return _finish("bootstrap-hook", guards, findings, facts)


def arm_collision_guard(ctx) -> ArmResult:
    """A repo-owned copy must win: the hook skips it and says so.

    Asserts BOTH the message and the filesystem. The message alone would pass
    if the guard printed the right thing and copied anyway.
    """
    collided = ctx.collision_skill
    project = ctx.root / "collision-project"
    (project / ".claude" / "skills" / collided).mkdir(parents=True, exist_ok=True)
    shutil.copy(ctx.lock_path, project / "skills.lock")
    (project / ".claude" / "skills" / collided / "SKILL.md").write_text(
        "---\nname: %s\ndescription: repo-owned copy seeded by the collision "
        "arm.\n---\n" % collided, encoding="utf-8")

    scratch = make_scratch(ctx.root, "collision-guard")
    before = _probe(scratch, timeout=ctx.timeout)
    env = {"AGENTSKILLS_REPO": ctx.registry_url,
           "CLAUDE_PROJECT_DIR": str(project),
           "SKILLS_BOOTSTRAP_FORCE": "1"}
    verdict = _run_hook(ctx.hook, scratch=scratch, env_extra=env,
                        timeout=ctx.timeout)
    facts = _probe(scratch, timeout=ctx.timeout)
    guards = leg_guards("arm", facts, scratch=scratch.root, declared_env=env,
                        lock=ctx.lock, expect_surface=("SKILLS_BOOTSTRAP_FORCE",))

    phrase = f"1 collision skipped, repo-owned wins ({collided})"
    written = (scratch.home / ".claude" / "skills" / collided).exists()
    expected = {skill for _, skill in lock_pairs(ctx.lock)} - {collided}
    findings = [
        Finding("collision/verdict", phrase in verdict,
                f"verdict: {verdict!r}"
                + ("" if phrase in verdict else f" (expected to contain {phrase!r})")),
        Finding("collision/not-written", not written,
                f"$HOME/.claude/skills/{collided} was not written"
                if not written
                else f"the hook reported skipping {collided} and copied it anyway"),
        _delivery_finding(expected, _delivered(before, facts), "local"),
    ]
    return _finish("collision-guard", guards, findings, facts)


ARMS = {
    "clean-room": arm_clean_room,
    "project-mirror": arm_project_mirror,
    "plugin-marketplace": arm_plugin_marketplace,
    "bootstrap-hook": arm_bootstrap_hook,
    "collision-guard": arm_collision_guard,
}


#: Caps on what the run record copies out of the init event (#202). Both are
#: the CLI's own output and land in a public artifact.
VERSION_MAX_CHARS = 64
MODEL_ID_MAX_CHARS = 128


def _bounded_str(value, limit: int) -> str | None:
    if isinstance(value, str) and 0 < len(value.strip()) <= limit:
        return value.strip()
    return None


def probe_versions(facts: ProbeFacts | None) -> dict:
    """`{"harness_version", "model"}` from a leg's init event, each a
    bounded string or None — never the "unknown" `ProbeFacts.version`
    renders for a human."""
    init = facts.init if facts is not None else {}
    return {"harness_version": _bounded_str(init.get("claude_code_version"),
                                            VERSION_MAX_CHARS),
            "model": _bounded_str(init.get("model"), MODEL_ID_MAX_CHARS)}


def _finish(name: str, guards: list, findings: list,
            facts: ProbeFacts | None = None) -> ArmResult:
    """A bad guard outranks every finding: INCONCLUSIVE, never PASS or FAIL.

    `facts` is the arm's own (last) leg, whose init event names the CLI
    version and model the run record carries for this arm (#202).
    """
    versions = probe_versions(facts)
    if not all(guard.ok for guard in guards):
        return ArmResult(name, INCONCLUSIVE, guards, findings,
                         notes=["a guard did not hold, so neither a pass nor a "
                                "fail from this arm would mean anything"],
                         **versions)
    status = PASS if all(finding.ok for finding in findings) else FAIL
    return ArmResult(name, status, guards, findings, **versions)


@dataclass
class ArmContext:
    root: Path
    registry: Path
    lock_path: Path
    lock: dict
    hook: Path
    bundle: str
    collision_skill: str
    timeout: int = 120

    @property
    def registry_url(self) -> str:
        """file:// URL — the hook's remote_url() allows it so tests need no network."""
        return f"file://{self.registry.resolve()}"


def run_arm(name: str, ctx: ArmContext) -> ArmResult:
    try:
        return ARMS[name](ctx)
    except (ArmError, ProbeError) as exc:
        return ArmResult(name, INCONCLUSIVE, [], [], error=str(exc))
