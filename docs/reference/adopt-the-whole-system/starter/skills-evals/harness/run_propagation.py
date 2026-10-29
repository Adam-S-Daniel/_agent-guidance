#!/usr/bin/env python3
"""Tier-2 propagation probes: does each delivery channel still deliver?

One arm per channel, each spawning the real Claude Code CLI twice (control leg,
then arm leg) and asserting on the difference against `skills.lock`. Unlike
`run_eval.py` and `run_canary.py` this runner reaches NO API and needs NO
credential: it reads the `system/init` event, which the CLI computes locally
before its first request, then kills the child. A full five-arm run costs
$0.00, so this belongs on `pull_request` rather than behind `eval.yml`'s OIDC —
where a branch dispatch would die at token exchange and tell you nothing.

This used to also carry a FRESHNESS GATE that read a claude.ai Routine's
Tier-3 account-store audit and failed when that audit's published result was
missing, stale or unreadable. The owner deleted that Routine on 2026-09-28
along with the claude.ai ZIP-upload channel it audited (adam-agentskills#23),
so the gate was retired in the same change — there was nothing left for it to
watch. See `evals/propagation/ROUTINE.md`, now marked HISTORY, for what that
layer did and why.

Usage:
    python3 harness/run_propagation.py evals/propagation
    python3 harness/run_propagation.py evals/propagation --arm bootstrap-hook

Exit codes: 0 everything asserted holds; 1 an assertion failed; 2 a probe
fault — the CLI would not start, the stream changed shape, or a guard did not
hold, so neither a pass nor a fail would have meant anything.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guidance  # noqa: E402 — the harness-wide timeout ceiling and predicate
from propagation import arms  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_FAULT = 0, 1, 2


def load_fixture(eval_dir: Path) -> dict:
    """The fixture, or a named configuration error.

    A-N1-2, applied to every entry point that loads one: the container that
    holds a fixture's keys was never typed, so a LIST root was
    `AttributeError: 'list' object has no attribute 'get'` and an EMPTY file
    (YAML `None`) a `TypeError`, both rc 1 and both outside the rc-2
    configuration contract.
    """
    path = eval_dir / "fixture.yaml"
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict):
        raise guidance.GuidanceError(
            f"{path} must be a YAML mapping of fixture keys, got "
            f"{type(doc).__name__}"
            + (" (the file is empty)" if doc is None else f": {doc!r}"))
    return doc


def parse_iso8601(value: str) -> datetime:
    """RFC 3339 in, tz-aware UTC out. Used only for `--now` (see its help)."""
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def resolve_registry(cli_value: Path | None) -> Path:
    """This script's OWN convention: --registry, $AGENTSKILLS_DIR, ~/repos.

    Unlike run_eval.py (issue #63), this probe only ever targets the
    acsi-agentskills registry, so it keeps its own single-path resolution
    rather than harness/registries.yml's multi-registry NAME=PATH scheme —
    the two are deliberately NOT the same shape any more; don't assume parity.

    ABSOLUTE on every branch, and that is load-bearing rather than tidiness:
    the arms hand registry-derived paths to children they spawn with `cwd` set
    to a scratch workspace — `arms._run_hook` runs `bash <hook>` there — so a
    relative registry is read against a directory that does not contain it.
    Measured in CI, where propagation.yml at the time passed
    `--registry ../agentskills`: both hook-running arms died with rc=127,
    `bash: ../agentskills/.claude/hooks/skills-bootstrap.sh: No such file or
    directory`. It never reproduced locally because every local invocation had
    passed an absolute path — which is exactly how it reached CI. (Now
    `../acsi-agentskills`; the failure mode is unchanged.)
    """
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    env = os.environ.get("AGENTSKILLS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / "repos" / "acsi-agentskills").resolve()


def self_test(ctx: arms.ArmContext) -> tuple:
    """Prove the assertion layer can still fail — against the REAL binary.

    The hermetic mutation suite proves the probe's logic; it cannot prove the
    CLI still emits `init` where this harness looks for it, or that a
    plugin install still produces namespaced names. So one live run of the
    plugin arm goes against a lock carrying a skill the registry does not ship,
    and is REQUIRED to come back FAIL. A green self-test on a real CLI whose
    event shape has moved would otherwise be the whole suite's blind spot: an
    arm that can no longer detect anything reports INCONCLUSIVE at worst and
    PASS at best, and this is what tells them apart.
    """
    mutated = dict(ctx.lock, skills=dict(ctx.lock["skills"],
                                         **{f"{ctx.bundle}/phantom-skill": "0" * 64}))
    # Its OWN scratch root. Sharing the arms' root would let the real run's
    # marketplace install sit in the HOME this leg treats as clean, firing the
    # negative control and returning FAIL for a reason that has nothing to do
    # with the phantom skill — a green self-test that proves nothing.
    root = Path(tempfile.mkdtemp(prefix="propagation-selftest-"))
    try:
        result = arms.run_arm("plugin-marketplace",
                              arms.ArmContext(**dict(vars(ctx), root=root,
                                                     lock=mutated)))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if result.status == arms.FAIL:
        return True, ("PASS self-test: the live plugin arm still fails on a lock "
                      "naming a skill the registry does not ship")
    return False, (f"FAIL self-test: injecting a phantom skill into the lock "
                   f"produced {result.status}, not FAIL — the assertion layer "
                   f"has stopped detecting anything against this CLI\n"
                   + result.render())


def build_context(fixture: dict, registry: Path, root: Path,
                  timeout: int) -> arms.ArmContext:
    lock_path = registry / fixture["lock_path"]
    hook = registry / fixture["hook_path"]
    for path, what in ((registry, "registry checkout"), (lock_path, "skills.lock"),
                       (hook, "bootstrap hook")):
        if not path.exists():
            raise arms.ArmError(
                f"{what} not found at {path} — pass --registry PATH (or set "
                "$AGENTSKILLS_DIR) pointing at an acsi-agentskills checkout")
    return arms.ArmContext(
        root=root, registry=registry, lock_path=lock_path,
        lock=arms.load_lock(lock_path), hook=hook,
        bundle=fixture["bundle"], collision_skill=fixture["collision_skill"],
        timeout=timeout)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("eval_dir", type=Path)
    parser.add_argument("--arm", action="append", default=None,
                        help="run only this arm (repeatable); default: all")
    parser.add_argument("--registry", type=Path, default=None,
                        help="acsi-agentskills checkout: this, else $AGENTSKILLS_DIR, "
                             "else ~/repos/acsi-agentskills")
    parser.add_argument("--self-test", action="store_true",
                        help="also prove, against the real binary, that the "
                             "plugin arm still FAILS on a deliberately wrong lock")
    parser.add_argument("--now", default=None,
                        help="ISO-8601 instant to stamp the run record's "
                             "generated_at with; tests pass it so the JSON "
                             "output never depends on the clock")
    parser.add_argument("--timeout", type=int, default=120,
                        help="per-CLI-invocation timeout in seconds; "
                             "1..2700, the harness-wide ceiling "
                             "harness/guidance.py holds every timeout to")
    parser.add_argument("--json", type=Path, default=None,
                        help="also write the machine-readable run record here")
    args = parser.parse_args(argv)

    # The SAME predicate and the SAME ceiling every other timeout in this
    # harness is held to — `args.timeout` becomes `ctx.timeout` and reaches
    # `arms._probe`'s and `arm_plugin_marketplace`'s
    # `subprocess.run(timeout=...)` with nothing between, where argparse's
    # `type=int` bounds neither end and a very large value raises a bare
    # `OverflowError` instead of naming a rule.
    try:
        guidance.check_timeout(args.timeout, "--timeout",
                               guidance.CLI_TIMEOUT_REMEDY)
    except guidance.GuidanceError as exc:
        print(f"configuration error: {exc}")
        return EXIT_FAULT

    try:
        fixture = load_fixture(args.eval_dir)
    except guidance.GuidanceError as exc:
        print(f"configuration error: {exc}")
        return EXIT_FAULT
    now = (parse_iso8601(args.now) if args.now
           else datetime.now(timezone.utc))

    names = args.arm or list(fixture["arms"])
    unknown = [name for name in names if name not in arms.ARMS]
    if unknown:
        print(f"unknown arm(s): {unknown}; known: {sorted(arms.ARMS)}")
        return EXIT_FAULT

    results = []
    self_test_line = None
    self_test_ok = True
    fault = False
    root = Path(tempfile.mkdtemp(prefix="propagation-"))
    try:
        ctx = build_context(fixture, resolve_registry(args.registry), root,
                            args.timeout)
        for name in names:
            results.append(arms.run_arm(name, ctx))
        if args.self_test:
            self_test_ok, self_test_line = self_test(ctx)
    except guidance.GuidanceError as exc:
        # A subprocess sink under harness/propagation/ refused its timeout
        # on entry (S1-a-2). That is a configuration error the operator
        # must fix, not an inconclusive arm: named, rc 2, no traceback.
        print(f"configuration error: {exc}")
        return EXIT_FAULT
    except arms.ArmError as exc:
        print(f"{arms.INCONCLUSIVE} setup: {exc}")
        fault = True
    finally:
        shutil.rmtree(root, ignore_errors=True)

    for result in results:
        print(result.render())
    if self_test_line:
        print(self_test_line)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "schema": 1,
            "probe": "propagation/tier2",
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "arms": [r.to_dict() for r in results],
            "self_test": {"ok": self_test_ok, "detail": self_test_line},
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if fault or any(r.status == arms.INCONCLUSIVE for r in results):
        return EXIT_FAULT
    if not self_test_ok or any(r.status == arms.FAIL for r in results):
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
