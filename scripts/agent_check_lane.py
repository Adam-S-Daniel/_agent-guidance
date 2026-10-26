#!/usr/bin/env python3
"""The lesson-check lane's gates for the agent-checks runner (ADR 0027).

The fleet worker turns a checkable lesson into a `case` arm in the runner's
check_lessons function (templates/agent-checks/agent-checks). Before the PR
may auto-merge, these subcommands must pass:

  cases      every case in the case table holds against a runner
  red-green  the lesson's refuse cases fail under the base runner and every
             case holds under the candidate
  fleet-scan the candidate refuses nothing on any fleet repo's main that the
             base did not already refuse
  scope      the PR changes only the lane's four files, and the two runner
             copies are byte-identical

The case table (test/fixtures/agent-checks-cases.jsonl) holds one JSON object
per line: `id`, optional `lesson`, `event` (pre-commit, ci or tree), `files`
({path: content}), `expect` (refuse or pass), and `message` (a substring
stderr must hold for a refusal). Each case runs in a fresh repo whose base is
an empty commit. pre-commit stages the files and runs `pre-commit`. tree
commits them and runs `tree HEAD`. ci commits each file in its own commit, in
order, then removes the paths in an optional `delete` list in a last commit,
and runs `ci <base> HEAD`, so a case can show a file caught although the head
no longer has it.

Every runner run gets a clean environment: a temp HOME, no global or system
git config and no inherited GIT_* variable, so no machine hook fires, and
AGENT_CHECKS_LOG=off, so no case lands in the owner's run log.

Usage:
  python3 scripts/agent_check_lane.py cases --runner R [--cases F]
  python3 scripts/agent_check_lane.py red-green --base R0 --candidate R1 --lesson ID [--cases F]
  python3 scripts/agent_check_lane.py fleet-scan --base R0 --candidate R1 --root DIR [--repos-yml PATH]
  python3 scripts/agent_check_lane.py scope --base REF --head REF
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = REPO_ROOT / "test" / "fixtures" / "agent-checks-cases.jsonl"
DEFAULT_REPOS_YML = REPO_ROOT / "repos.yml"
EVENTS = ("pre-commit", "ci", "tree")
EXPECTS = ("refuse", "pass")
REFUSAL_PREFIX = "agent-checks: "
# The closing line the runner prints after any refusal. It is not a refusal of
# its own, so fleet-scan does not count it.
TRAILER_PREFIX = "agent-checks: fix the cause rather than skipping the hook"
SCOPE_PATHS = (
    "templates/agent-checks/agent-checks",
    ".githooks/agent-checks",
    "test/fixtures/agent-checks-cases.jsonl",
    "docs/agent-checks-lessons.md",
)
RUNNER_COPIES = SCOPE_PATHS[:2]


def clean_env(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(home), GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
               AGENT_CHECKS_LOG="off")
    return env


def git(cwd, env, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, check=True).stdout


def run_runner(runner, cwd, env, *args):
    return subprocess.run(["bash", str(Path(runner).resolve()), *args], cwd=str(cwd),
                          env=env, capture_output=True, text=True)


def load_cases(path):
    cases = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            case = json.loads(line)
            problems = []
            if not isinstance(case.get("id"), str) or not case["id"]:
                problems.append("no id")
            if case.get("event") not in EVENTS:
                problems.append(f"event must be one of {', '.join(EVENTS)}")
            if case.get("expect") not in EXPECTS:
                problems.append("expect must be refuse or pass")
            files = case.get("files")
            if not isinstance(files, dict) or not files or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
                problems.append("files must be a non-empty {path: content} object")
            delete = case.get("delete", [])
            if delete and (case.get("event") != "ci" or not isinstance(delete, list)
                           or not all(isinstance(files, dict) and d in files for d in delete)):
                problems.append("delete is for ci cases only and lists paths from files")
            if case.get("expect") == "refuse" and not case.get("message"):
                problems.append("a refuse case needs a message")
            if problems:
                raise ValueError(f"{path}:{lineno}: {'; '.join(problems)}")
            cases.append(case)
    ids = [c["id"] for c in cases]
    dups = sorted({i for i in ids if ids.count(i) > 1})
    if dups:
        raise ValueError(f"{path}: duplicate case id(s): {', '.join(dups)}")
    return cases


def write_file(repo, rel, content):
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)


def run_case(case, runner, scratch):
    """Run one case; return (held, why)."""
    home = scratch / "home"
    repo = scratch / "repo"
    home.mkdir()
    repo.mkdir()
    env = clean_env(home)
    git(repo, env, "init", "-q", "-b", "main")
    git(repo, env, "config", "user.name", "Example")
    git(repo, env, "config", "user.email", "agent@example.com")
    git(repo, env, "config", "commit.gpgsign", "false")
    git(repo, env, "commit", "-q", "--allow-empty", "-m", "base")
    base = git(repo, env, "rev-parse", "HEAD").strip()

    event = case["event"]
    if event == "pre-commit":
        for rel, content in case["files"].items():
            write_file(repo, rel, content)
        git(repo, env, "add", "--", *case["files"])
        result = run_runner(runner, repo, env, "pre-commit")
    elif event == "ci":
        for rel, content in case["files"].items():
            write_file(repo, rel, content)
            git(repo, env, "add", "--", rel)
            git(repo, env, "commit", "-q", "-m", f"add {rel}")
        if case.get("delete"):
            git(repo, env, "rm", "-q", "--", *case["delete"])
            git(repo, env, "commit", "-q", "-m", "remove")
        result = run_runner(runner, repo, env, "ci", base, "HEAD")
    else:
        for rel, content in case["files"].items():
            write_file(repo, rel, content)
        git(repo, env, "add", "--", *case["files"])
        git(repo, env, "commit", "-q", "-m", "files")
        result = run_runner(runner, repo, env, "tree", "HEAD")

    if case["expect"] == "pass":
        if result.returncode == 0:
            return True, ""
        return False, f"expected pass, got exit {result.returncode}"
    if result.returncode != 1:
        return False, f"expected refuse (exit 1), got exit {result.returncode}"
    if case["message"] not in result.stderr:
        return False, "refused, but stderr lacks the expected message"
    return True, ""


def evaluate(case, runner):
    with tempfile.TemporaryDirectory(prefix="agent-check-case-") as tmp:
        return run_case(case, runner, Path(tmp))


def cmd_cases(args):
    cases = load_cases(args.cases)
    failed = 0
    for case in cases:
        held, why = evaluate(case, args.runner)
        if held:
            print(f"PASS {case['id']}")
        else:
            failed += 1
            print(f"FAIL {case['id']} ({why})")
    print(f"{len(cases) - failed} passed, {failed} failed")
    return 0 if failed == 0 and cases else 1


def cmd_red_green(args):
    cases = load_cases(args.cases)
    tagged = [c for c in cases if c.get("lesson") == args.lesson]
    refuse = [c for c in tagged if c["expect"] == "refuse"]
    if not tagged:
        print(f"FAIL no case is tagged with lesson {args.lesson}")
        return 1
    if not refuse:
        print(f"FAIL no refuse case is tagged with lesson {args.lesson}")
        return 1
    failed = 0
    for case in refuse:
        held, _ = evaluate(case, args.base)
        if held:
            failed += 1
            print(f"FAIL red {case['id']} (already refuses under the base runner, so it tests nothing new)")
        else:
            print(f"PASS red {case['id']}")
    for case in cases:
        held, why = evaluate(case, args.candidate)
        if held:
            print(f"PASS green {case['id']}")
        else:
            failed += 1
            print(f"FAIL green {case['id']} ({why})")
    print(f"red: {len(refuse)} lesson case(s); green: {len(cases)} case(s); {failed} failed")
    return 0 if failed == 0 else 1


def fleet_names(repos_yml):
    """The names under cron_coverage.fleet, read with yq as scripts/sync.sh does."""
    if shutil.which("yq") is None:
        raise RuntimeError("yq is not on PATH; install mikefarah yq as ci.yml's 'Install yq' step does")
    probe = subprocess.run(["yq", "-o=json", "-I0", "."], input="a: 1\n",
                           capture_output=True, text=True)
    if probe.returncode != 0:
        raise RuntimeError("the yq on PATH does not accept '-o=json -I0'; this needs mikefarah's Go yq")
    out = subprocess.run(["yq", "-r", ".cron_coverage.fleet // [] | .[]", str(repos_yml)],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"yq could not read {repos_yml} (exit {out.returncode})")
    names = [n for n in out.stdout.splitlines() if n.strip()]
    if not names:
        raise RuntimeError(f"{repos_yml} lists no cron_coverage.fleet repos")
    return names


def refusal_lines(stderr):
    return [line for line in stderr.splitlines()
            if line.startswith(REFUSAL_PREFIX) and not line.startswith(TRAILER_PREFIX)]


def reach(root, name, env):
    """The checkout for name under root, or None and why it is not reachable."""
    if not name or "/" in name or name in (".", ".."):
        return None, "not a plain repo name"
    path = (root / name).resolve()
    if not path.is_dir():
        return None, "no checkout"
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(path), env=env,
                         capture_output=True, text=True)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != path:
        return None, "not a git checkout"
    head = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
                          cwd=str(path), env=env, capture_output=True, text=True)
    if head.returncode != 0:
        return None, "no HEAD commit"
    return path, ""


def cmd_fleet_scan(args):
    try:
        names = fleet_names(args.repos_yml)
    except RuntimeError as err:
        print(f"fleet-scan: {err}")
        return 2
    root = Path(args.root)
    not_reached = []
    total_new = 0
    with tempfile.TemporaryDirectory(prefix="agent-check-scan-") as tmp:
        env = clean_env(tmp)
        for name in names:
            path, why = reach(root, name, env)
            if path is None:
                not_reached.append(name)
                print(f"{name}: NOT REACHED ({why})")
                continue
            before = run_runner(args.base, path, env, "tree", "HEAD")
            after = run_runner(args.candidate, path, env, "tree", "HEAD")
            seen = set(refusal_lines(before.stderr))
            new = [line for line in refusal_lines(after.stderr) if line not in seen]
            if after.returncode not in (0, 1):
                new.append(f"(candidate runner exited {after.returncode})")
            total_new += len(new)
            print(f"{name}: {len(new)} new refusal(s)")
            for line in new:
                print(f"  {line}")
    if not_reached:
        print(f"NOT REACHED: {', '.join(not_reached)}; a repo that was not scanned is not clean")
        return 2
    print(f"{len(names)} repo(s) scanned, {total_new} new refusal(s)")
    return 1 if total_new else 0


def cmd_scope(args):
    out = subprocess.run(["git", "diff", "--name-status", "-z", "--find-renames",
                          f"{args.base}...{args.head}"], capture_output=True, text=True)
    if out.returncode != 0:
        print(f"scope: git diff {args.base}...{args.head} failed (exit {out.returncode})")
        return 1
    fields = out.stdout.split("\0")
    problems = []
    changes = 0
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status[0] in "RC":
            paths = fields[i + 1:i + 3]
            i += 3
        else:
            paths = fields[i + 1:i + 2]
            i += 2
        changes += 1
        if status not in ("A", "M"):
            problems.append(f"{status} {' -> '.join(paths)}: only adds and modifies are allowed")
        elif paths[0] not in SCOPE_PATHS:
            problems.append(f"{status} {paths[0]}: outside the lesson-check lane's files")
    if changes == 0:
        problems.append("no changes between the base and the head")
    blobs = []
    for rel in RUNNER_COPIES:
        blob = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{args.head}:{rel}"],
                              capture_output=True, text=True)
        blobs.append(blob.stdout.strip() if blob.returncode == 0 else None)
    if None in blobs or blobs[0] != blobs[1]:
        problems.append(f"{RUNNER_COPIES[1]} is not byte-identical to {RUNNER_COPIES[0]} at the head")
    for problem in problems:
        print(f"scope: {problem}")
    if problems:
        return 1
    print(f"scope: {changes} change(s), all within the lane's files")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("cases", help="run the case table against a runner")
    p.add_argument("--runner", required=True)
    p.add_argument("--cases", default=str(DEFAULT_CASES))
    p.set_defaults(func=cmd_cases)
    p = sub.add_parser("red-green", help="prove a lesson's cases fail before and pass after")
    p.add_argument("--base", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--lesson", required=True)
    p.add_argument("--cases", default=str(DEFAULT_CASES))
    p.set_defaults(func=cmd_red_green)
    p = sub.add_parser("fleet-scan", help="count new refusals on every fleet checkout")
    p.add_argument("--base", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--repos-yml", default=str(DEFAULT_REPOS_YML))
    p.set_defaults(func=cmd_fleet_scan)
    p = sub.add_parser("scope", help="refuse a change outside the lane's files")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.set_defaults(func=cmd_scope)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as err:
        print(f"{args.command}: {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
