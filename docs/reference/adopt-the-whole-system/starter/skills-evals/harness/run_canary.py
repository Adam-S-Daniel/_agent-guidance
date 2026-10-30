#!/usr/bin/env python3
"""Run the guidance-bridge canary against the real Claude Code CLI.

Verifies that the fleet's guidance-bridge pattern — a CLAUDE.md containing an
`@AGENTS.md` import line — still gets expanded by Claude Code's memory loader,
by probing each layout in evals/guidance-bridge-canary/layouts/ for a
per-layout magic token and checking whether it shows up in a tool-free reply.

This needs real API access (it spawns the real `claude` CLI, not a stub), so
it is run on demand or on a schedule like real eval runs — it is NOT part of
the hermetic test suite (test/run_tests.py exercises this runner against
test/fake-claude only) and is NOT run in CI.

Usage:
    python3 harness/run_canary.py evals/guidance-bridge-canary
    python3 harness/run_canary.py evals/guidance-bridge-canary --subagent \
      [--model M] [--timeout N] [--results-dir results]

Exit codes: 0 all layouts behaved as expected; 1 an expectation mismatch (see
per-layout diagnostics printed to stdout); 2 a runner-level error (CLI
invocation failed for at least one leg).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml


def load_fixture(eval_dir: Path) -> dict:
    """The fixture, or a named configuration error.

    A-N1-2, applied to every entry point that loads one: the container that
    holds a fixture's keys was never typed, so a LIST root was
    `AttributeError: 'list' object has no attribute 'get'` and an EMPTY file
    (YAML `None`) a `TypeError`, both rc 1 and both outside the rc-2
    configuration contract.
    """
    import guidance  # noqa: PLC0415 — cycle-avoidance, see main()
    path = eval_dir / "fixture.yaml"
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict):
        raise guidance.GuidanceError(
            f"{path} must be a YAML mapping of fixture keys, got "
            f"{type(doc).__name__}"
            + (" (the file is empty)" if doc is None else f": {doc!r}"))
    return doc


# The bound on the `--version` probe. Named rather than inlined so the sink
# check and the `timeout=` argument are provably the same value.
VERSION_TIMEOUT_S = 30


def claude_version() -> str:
    """Record the CLI version under test; "unknown" if it can't be determined."""
    import guidance  # noqa: PLC0415 — cycle-avoidance, see main()
    guidance.check_timeout(VERSION_TIMEOUT_S,
                           "run_canary.claude_version(timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    try:
        result = subprocess.run(
            [os.environ.get("CLAUDE_BIN", "claude"), "--version"],
            capture_output=True, text=True, timeout=VERSION_TIMEOUT_S,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:  # noqa: BLE001 — record "unknown", never crash the run
        pass
    return "unknown"


#: The open `collecting_results()` blocks, as `judge._MODEL_SINKS` is for the
#: judge: each is a list every leg's parsed result is appended to, with the
#: meter events the leg printed under `events` (#370).
_RESULT_SINKS: list[list] = []


@contextlib.contextmanager
def collecting_results():
    """Collect the usage figures of every CLI result `run_leg` parses in this
    block: `modelUsage` (always) and `num_turns`, `usage` and
    `total_cost_usd` where the result has them, raw and unvalidated (#371),
    and `events`, the meter events the leg printed (#370). An `is_error`
    result is collected too: it was still spent."""
    sink: list = []
    _RESULT_SINKS.append(sink)
    try:
        yield sink
    finally:
        _RESULT_SINKS.remove(sink)


def run_leg(workspace: Path, prompt: str, disallowed_tools: str, *,
           model: str | None, timeout: int,
           setting_sources: str = "project", env: dict | None = None) -> dict:
    """Invoke the CLI once against a seeded workspace.

    Mirrors run_eval.run_agent's error-dict pattern: callers must check
    `"error" in result` rather than relying on exceptions. Success dicts carry
    "reply" (the agent's final text, possibly empty).

    `setting_sources` and `env` exist for the guidance subject's per-arm
    delivery guard (#97), which reuses this probe against an arm's own config
    dir: guidance is delivered into USER memory, so its guard has to be
    invoked with `user,project` and with that arm's scrubbed environment. The
    defaults are exactly the canary's historical behaviour — `project`, and
    the harness's own environment inherited — so the guidance-bridge canary is
    byte-identical across this change.
    """
    # S1-a-2. Checked at the SINK, before the spawn, whatever the caller
    # passed: this leg is reached from run_canary's own `--timeout`, from
    # guidance.run_guard with the `guard.timeout_s` knob, and from anywhere a
    # later caller decides to reach it from.
    import guidance  # noqa: PLC0415 — cycle-avoidance, see main()
    from cli_json import (failed_run_detail, normalize_cli_result,  # noqa: PLC0415 — main() sets path
                          rate_limit_events)
    guidance.check_timeout(timeout, "run_canary.run_leg(timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    # Unlike run_eval, no --permission-mode bypassPermissions: the probe must
    # not use tools at all (read tools are explicitly disallowed), so the
    # default deny-without-a-prompter headless behavior is the safer choice —
    # and bypassPermissions is refused outright when running as root.
    cmd = [os.environ.get("CLAUDE_BIN", "claude"), "-p", prompt,
          "--output-format", "json",
          # The meter event an agent call prints (#370): the guidance guard
          # is read as it starts too, and the shape no longer depends on the
          # machine's verbose setting.
          "--verbose",
          "--setting-sources", setting_sources,
          "--disallowedTools", disallowed_tools,
          "--strict-mcp-config"]
    # No transcript under the real HOME. A leg with its own scratch config
    # dir (the guidance guard) writes inside that scratch and keeps the
    # CLI's default there.
    if not (env or {}).get("CLAUDE_CONFIG_DIR"):
        cmd.append("--no-session-persistence")
    if model:
        cmd += ["--model", model]

    try:
        # `env=None` still means "inherit", now with auto-memory off
        # (guidance.CLI_FORCED_ENV) applied last.
        result = subprocess.run(cmd, cwd=workspace, capture_output=True,
                                text=True, timeout=timeout,
                                env=guidance.cli_child_env(env))
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "detail": f"agent timed out after {timeout}s"}

    if result.returncode != 0:
        return {"error": "nonzero_exit",
                "detail": failed_run_detail(result.stdout, result.stderr),
                "returncode": result.returncode}

    try:
        decoded = json.loads(result.stdout)
        data = normalize_cli_result(decoded)
    except json.JSONDecodeError as e:
        return {"error": "invalid_json",
                "detail": f"stdout is not valid JSON ({e.msg} at character "
                          f"{e.pos} of {len(result.stdout)})"}
    except ValueError as e:
        return {"error": "invalid_json", "detail": str(e)}

    for sink in _RESULT_SINKS:
        sink.append({"modelUsage": data.get("modelUsage"),
                     **{key: data[key] for key in
                        ("num_turns", "usage", "total_cost_usd") if key in data},
                     "events": rate_limit_events(decoded)})

    if data.get("is_error"):
        return {"error": "agent_error", "detail": data.get("result", ""), "raw": data}

    return {"reply": data.get("result") or ""}


def _build_legs(fixture: dict, eval_dir: Path, use_subagent: bool) -> list[dict]:
    """One leg per layout, plus an optional bridge-subagent leg."""
    layouts_dir = eval_dir / "layouts"
    legs = [
        {
            "name": layout["name"],
            "expect": layout["expect"],
            "token": layout["token"],
            "prompt": fixture["prompt"],
            "disallowed_tools": fixture["disallowed_tools"],
            "layout_dir": layouts_dir / layout["name"],
        }
        for layout in fixture["layouts"]
    ]
    if use_subagent:
        bridge = next(layout for layout in fixture["layouts"] if layout["name"] == "bridge")
        # Catches regressions in subagent memory passing: same bridge layout
        # and token, but the probe itself launches a Task subagent.
        legs.append({
            "name": "bridge-subagent",
            "expect": bridge["expect"],
            "token": bridge["token"],
            "prompt": fixture["subagent_prompt"],
            "disallowed_tools": fixture["subagent_disallowed_tools"],
            "layout_dir": layouts_dir / "bridge",
        })
    return legs


def _run_leg(leg: dict, model: str | None, timeout: int) -> dict:
    """Materialize a fresh workspace from the leg's layout, probe it, clean up."""
    workspace = Path(tempfile.mkdtemp(prefix=f"guidance-bridge-canary-{leg['name']}-"))
    try:
        shutil.copytree(leg["layout_dir"], workspace, dirs_exist_ok=True)
        result = run_leg(workspace, leg["prompt"], leg["disallowed_tools"],
                         model=model, timeout=timeout)

        if "error" in result:
            return {"name": leg["name"], "expect": leg["expect"], "token": leg["token"],
                    "visible": None, "passed": False, "reply": "",
                    "error": {"type": result["error"], "detail": result.get("detail", "")}}

        reply = result["reply"]
        visible = leg["token"] in reply
        passed = visible == (leg["expect"] == "visible")
        return {"name": leg["name"], "expect": leg["expect"], "token": leg["token"],
                "visible": visible, "passed": passed, "reply": reply, "error": None}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _failure_hint(leg: dict) -> str:
    name = leg["name"]
    if name == "bridge":
        return ("import bridge may have regressed in this CLI version — check the "
                "upstream import-regression history and anthropics/claude-code#6235; "
                "see README \"Guidance-bridge canary\".")
    if name == "bridge-subagent":
        return "subagent memory passing may have regressed (as reported upstream Feb-May 2026)."
    if name == "agents-only":
        # Expected `visible`, got invisible: the one leg with no CLAUDE.md.
        return ("this layout has AGENTS.md and no CLAUDE.md, which Claude Code "
                "2.1.277 reads natively (its release note, unmeasured here until "
                "this leg first passes); check the recorded "
                "claude --version first, since an older CLI is expected to fail "
                "here, and only then suspect a native-loading regression; see "
                "README \"Guidance-bridge canary\".")
    # no-bridge / fence: expected invisible, got visible. They keep a CLAUDE.md,
    # which native AGENTS.md support (no CLAUDE.md only) does not explain.
    hint = ("most likely the probe's tool controls broke (foraging leaked the "
            "token); native AGENTS.md support does not explain it, because this "
            "layout has a CLAUDE.md")
    if name == "fence":
        hint += "; for fence specifically: fenced-import expansion behavior changed."
    else:
        hint += "."
    return hint


def _print_leg(leg: dict) -> None:
    if leg["error"]:
        print(f"ERROR {leg['name']}: {leg['error']['type']}: {leg['error']['detail']}")
        return
    status = "visible" if leg["visible"] else "invisible"
    if leg["passed"]:
        print(f"PASS {leg['name']} (expected {leg['expect']}, token {status})")
    else:
        print(f"FAIL {leg['name']} (token {leg['token']}, expected {leg['expect']}, "
             f"got {status}): {_failure_hint(leg)}")


def _render_report(name: str, version: str, model: str | None, timestamp: str,
                   legs: list[dict]) -> str:
    lines = [
        f"# Guidance-bridge canary report: {name}",
        "",
        f"- Claude CLI version: {version}",
        f"- Model: {model or '(CLI default)'}",
        f"- Timestamp: {timestamp}",
        "",
        "| Leg | Expectation | Token visible | Result |",
        "| --- | --- | --- | --- |",
    ]
    for leg in legs:
        if leg["error"]:
            visible_str = "-"
            result_str = f"ERROR: {leg['error']['type']}"
        else:
            visible_str = "yes" if leg["visible"] else "no"
            result_str = "PASS" if leg["passed"] else "FAIL"
        lines.append(f"| {leg['name']} | {leg['expect']} | {visible_str} | {result_str} |")

    lines += ["", "## Replies", ""]
    for leg in legs:
        reply = (leg.get("reply") or "")[:500]
        lines.append(f"### {leg['name']}")
        lines.append("")
        lines.append(f"> {reply}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("eval_dir", type=Path)
    parser.add_argument("--subagent", action="store_true",
                        help="also run the bridge-subagent leg (Task-launched subagent)")
    parser.add_argument("--model", default=None, help="override the model for all legs")
    parser.add_argument("--timeout", type=int, default=600,
                        help="per-leg CLI timeout in seconds (default 600); "
                             "1..2700, the harness-wide ceiling "
                             "harness/guidance.py holds every timeout to")
    parser.add_argument("--results-dir", type=Path, default=Path("results"),
                        help="root directory for run outputs (summary + report)")
    args = parser.parse_args()

    # The SAME predicate and the SAME ceiling every other timeout in this
    # harness is held to. `args.timeout` reaches `run_leg`'s
    # `subprocess.run(timeout=...)` at :86 with nothing between: argparse's
    # `type=int` accepts every integer there is, and 2 200 000 of them raise a
    # bare `OverflowError: timeout is too large` instead of naming a rule.
    # This is the flag beside the one round 3 caught in run_eval.py; a bound
    # that only guards the entry point you were looking at is not a bound.
    #
    # Imported HERE and not at module scope: harness/guidance.py imports THIS
    # module (`run_canary.run_leg` is the probe its guard reuses), so a
    # module-scope import would close the cycle. By the time main() runs both
    # modules are fully loaded, and `sys.path` already carries harness/ —
    # either because guidance put it there, or because this file is
    # `__main__` and its own directory is `sys.path[0]`.
    import guidance  # noqa: PLC0415 — cycle-avoidance, see above
    try:
        guidance.check_timeout(args.timeout, "--timeout",
                               guidance.CLI_TIMEOUT_REMEDY)
    except guidance.GuidanceError as exc:
        print(f"configuration error: {exc}")
        return 2

    try:
        fixture = load_fixture(args.eval_dir)
        version = claude_version()
        legs = _build_legs(fixture, args.eval_dir, args.subagent)
        results = [_run_leg(leg, args.model, args.timeout) for leg in legs]
    except guidance.GuidanceError as exc:
        # The sink checks in claude_version() and run_leg() raise from inside
        # the function that was about to spawn, whatever fed them. Named rc 2,
        # never a traceback.
        print(f"configuration error: {exc}")
        return 2

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_dir = args.results_dir / fixture["name"] / timestamp
    report_dir.mkdir(parents=True, exist_ok=True)

    report = _render_report(fixture["name"], version, args.model, timestamp, results)
    with open(report_dir / "report.md", "w", encoding="utf-8") as f:
        f.write(report)

    summary = {
        "name": fixture["name"],
        "timestamp": timestamp,
        "claude_version": version,
        "legs": [{k: leg[k] for k in
                 ("name", "expect", "token", "visible", "passed", "error", "reply")}
                for leg in results],
    }
    with open(report_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    for leg in results:
        _print_leg(leg)

    if any(leg["error"] for leg in results):
        return 2
    if any(not leg["passed"] for leg in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
