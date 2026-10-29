#!/usr/bin/env python3
"""improve_gate.py: validate and prepare a routine's improve branch.

    improve_gate.py validate --evals-repo DIR --sha SHA --head-branch NAME
                             --registry-dir DIR [--base origin/main]
                             [--policy evals/improve-policy.yml]
    improve_gate.py prepare  (same flags) --out DIR

validate reads the branch only through `git show` and `git diff` on the
commit SHA. It never checks the branch out and never runs, imports or
executes anything from it. It prints JSON {"ok": bool, "reasons": [...]}
and exits 0 when ok, 1 when refused, 2 on bad usage.

prepare runs validate again, then (no credential anywhere) applies the patch
in the registry checkout on a new branch eval-improve/<run id>, raises the
plugin's patch version, appends a docs/skill-impact.md entry, commits, and
writes DIR/pr-body.md and DIR/result.json. A plugin that runs code (hooks/ or
bin/), or improve_merge_mode: draft, sets "draft": true in result.json: that
PR is opened as a draft and auto-merge is never turned on.
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import registry_edit as R  # noqa: E402

RUN_ID = r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}"
BRANCH_RE = re.compile(rf"\Aclaude/eval-improve-({RUN_ID})\Z")
SKILL_PATH_RE = re.compile(r"\Aplugins/([a-z0-9][a-z0-9-]*)/skills/([a-z0-9][a-z0-9-]*)/SKILL\.md\Z")
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.S)
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
MAX_EVIDENCE_BYTES = 200_000
EPS = 1e-9
DEFAULT_POLICY = {"improve_merge_mode": "auto", "min_fixtures": 4, "min_train_gain": 0.10,
                  "judge_tie_gain": 1.0, "max_patch_bytes": 40000, "max_token_ratio": 1.5}


class Refusal(Exception):
    pass


# evals/improve-policy.yml uses the guide's key names (9.2) and also holds the
# planner's keys, so one file serves every reader. Guide name -> name used here;
# either spelling is accepted, never both.
POLICY_ALIASES = {"training_gain_min": "min_train_gain", "judge_gain_when_tied": "judge_tie_gain"}
# Rules check_evidence always applies: the file may state them, never turn them off.
ALWAYS_ON = ("validation_may_not_worsen", "holdout_may_not_regress", "no_errored_fixture")
# Read by the planner (plan_week.py), not by this gate; accepted and type-checked.
PLANNER_KEYS = ("trials", "pause_after_rejection_days", "pause_after_consecutive_rejections",
                "no_branch_counts_as_rejection_after_hours")


def load_policy(path):
    import yaml
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    if not isinstance(doc, dict):
        raise ValueError("policy must be a mapping")
    doc = dict(doc)
    for key in ALWAYS_ON:
        if key in doc and doc.pop(key) is not True:
            raise ValueError(key + " must be true: the gate always applies it")
    for key in PLANNER_KEYS:
        if key in doc:
            v = doc.pop(key)
            if type(v) is not int or v < 1:
                raise ValueError(key + " must be a positive integer")
    for guide_name, name in POLICY_ALIASES.items():
        if guide_name in doc:
            if name in doc:
                raise ValueError("policy sets both %s and %s" % (guide_name, name))
            doc[name] = doc.pop(guide_name)
    unknown = set(doc) - set(DEFAULT_POLICY)
    if unknown:
        raise ValueError("unknown policy keys")
    pol = {**DEFAULT_POLICY, **doc}
    if pol["improve_merge_mode"] not in ("auto", "draft"):
        raise ValueError("improve_merge_mode must be auto or draft")
    return pol


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True).stdout


def show(repo, sha, path):
    """File content at a commit, as bytes. Raises Refusal if absent."""
    try:
        return _git(repo, "show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        raise Refusal(f"missing-file {path!r}") from None


def changed_entries(repo, base, sha):
    """[(status, new_mode, path)] for base...sha, renames off, NUL-separated."""
    raw = _git(repo, "diff", "--raw", "--no-renames", "-z", f"{base}...{sha}").split(b"\0")
    out, i = [], 0
    while i + 1 < len(raw) and raw[i]:
        meta = raw[i].decode("ascii", "replace").lstrip(":").split()
        out.append((meta[4][0], meta[1], raw[i + 1].decode("utf-8", "replace")))
        i += 2
    return out


def check_paths(entries, run_id):
    want = {f"eval-improve/{run_id}/patch.diff", f"eval-improve/{run_id}/evidence.json"}
    got = {p for _, _, p in entries}
    for status, mode, path in entries:
        if path not in want:
            raise Refusal(f"path-not-allowed {path[:80]!r}")
        if status != "A" or mode != "100644":
            raise Refusal(f"not-a-plain-added-file {path!r}")
    if got != want:
        raise Refusal("branch must add exactly patch.diff and evidence.json")


def parse_patch(text):
    """Strict single-file unified diff. Returns (path, hunks, added) where
    hunks is [(old_start, [(op, line)])] and added is [(newlineno, text)]."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) < 4 or not lines[0].startswith("diff --git "):
        raise Refusal("patch-format: must start with diff --git")
    m = re.match(r"^diff --git a/(\S+) b/(\S+)$", lines[0])
    if not m or m.group(1) != m.group(2):
        raise Refusal("patch-format: header")
    path = m.group(1)
    i = 1
    while i < len(lines) and not lines[i].startswith("--- "):
        if not re.match(r"^index [0-9a-f]+\.\.[0-9a-f]+( 100644)?$", lines[i]):
            raise Refusal("patch-format: only an index line may precede ---")
        i += 1
    if i + 1 >= len(lines) or lines[i] != f"--- a/{path}" or lines[i + 1] != f"+++ b/{path}":
        raise Refusal("patch-format: ---/+++ lines")
    i += 2
    hunks, added = [], []
    while i < len(lines):
        h = HUNK_RE.match(lines[i])
        if not h:
            raise Refusal("patch-format: unexpected line (second file or header?)")
        old_n = int(h.group(2) if h.group(2) is not None else 1)
        new_n = int(h.group(4) if h.group(4) is not None else 1)
        body, o, n = [], 0, 0
        new_no = int(h.group(3))
        i += 1
        while i < len(lines) and (o < old_n or n < new_n or lines[i].startswith("\\")):
            ln = lines[i]
            if ln.startswith("\\"):
                i += 1
                continue
            op, rest = ln[:1], ln[1:]
            if op == " ":
                o += 1; n += 1; new_no += 1
            elif op == "-":
                o += 1
            elif op == "+":
                n += 1; added.append((new_no, rest)); new_no += 1
            else:
                raise Refusal("patch-format: bad hunk line")
            body.append((op, rest))
            i += 1
        if o != old_n or n != new_n:
            raise Refusal("patch-format: hunk counts")
        hunks.append((int(h.group(1)), body))
    if not hunks:
        raise Refusal("patch-format: no hunks")
    return path, hunks, added


def apply_hunks(old_text, hunks):
    lines = old_text.split("\n")
    trailing = lines[-1] == ""
    if trailing:
        lines.pop()
    out, pos = [], 0
    for start, body in hunks:
        at = max(start - 1, 0) if any(op != "+" for op, _ in body) else start
        if at < pos or at > len(lines):
            raise Refusal("patch-apply: hunks overlap or out of range")
        out.extend(lines[pos:at])
        pos = at
        for op, text in body:
            if op in " -":
                if pos >= len(lines) or lines[pos] != text:
                    raise Refusal("patch-apply: context does not match")
                if op == " ":
                    out.append(text)
                pos += 1
            else:
                out.append(text)
    out.extend(lines[pos:])
    return "\n".join(out) + ("\n" if trailing else "")


def check_frontmatter(old, new):
    a, b = FRONTMATTER_RE.match(old), FRONTMATTER_RE.match(new)
    if not a or not b or a.group(0) != b.group(0):
        raise Refusal("frontmatter-changed")
    if not new[b.end():].strip():
        raise Refusal("empty-body")


def _num(v, lo=0.0, hi=1e12):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise Refusal("evidence-format: bad number")
    return float(v)


def _side(d):
    if not isinstance(d, dict):
        raise Refusal("evidence-format: side")
    judge = d.get("judge")
    errors = d.get("errors")
    if isinstance(errors, bool) or not isinstance(errors, int) or errors < 0:
        raise Refusal("evidence-format: errors")
    return {"pass": _num(d.get("pass_rate"), 0, 1),
            "judge": None if judge is None else _num(judge, 0, 10),
            "tokens": _num(d.get("tokens", 0)), "errors": errors}


def not_worse(base, cand):
    if cand["pass"] < base["pass"] - EPS:
        return False
    if abs(cand["pass"] - base["pass"]) <= EPS and None not in (base["judge"], cand["judge"]):
        return cand["judge"] >= base["judge"] - EPS
    return True


def check_evidence(ev, pol, plugin, skill, run_id):
    """The acceptance rule of 10.2. Returns a dict of the numbers; raises Refusal."""
    if not isinstance(ev, dict) or ev.get("schema") != 1:
        raise Refusal("evidence-format: schema")
    if ev.get("run_id") != run_id or ev.get("skill") != skill or ev.get("plugin") != plugin:
        raise Refusal("evidence-mismatch: run, skill or plugin differs from the patch")
    fx = ev.get("fixtures")
    if not isinstance(fx, list) or len(fx) < pol["min_fixtures"]:
        raise Refusal("evidence: fewer than the minimum fixtures")
    roles = [f.get("role") if isinstance(f, dict) else None for f in fx]
    if roles[0] != "holdout" or roles.count("holdout") != 1:
        raise Refusal("evidence: the first fixture must be the one fixed holdout")
    if roles.count("validation") != 1 or any(r not in ("holdout", "validation", "train") for r in roles):
        raise Refusal("evidence: exactly one validation fixture, the rest train")
    parsed = [(f["role"], _side(f.get("baseline")), _side(f.get("candidate"))) for f in fx]
    if any(b["errors"] or c["errors"] for _, b, c in parsed):
        raise Refusal("evidence: a fixture errored")
    train = [(b, c) for r, b, c in parsed if r == "train"]
    pass_gain = sum(c["pass"] - b["pass"] for b, c in train) / len(train)
    pairs = [(b["judge"], c["judge"]) for b, c in train if None not in (b["judge"], c["judge"])]
    judge_gain = sum(c - b for b, c in pairs) / len(pairs) if pairs else 0.0
    if abs(pass_gain) <= EPS:
        if judge_gain < pol["judge_tie_gain"] - EPS:
            raise Refusal("evidence: training gain below the bar")
    elif pass_gain < pol["min_train_gain"] - EPS:
        raise Refusal("evidence: training gain below the bar")
    for role in ("validation", "holdout"):
        for r, b, c in parsed:
            if r == role and not not_worse(b, c):
                raise Refusal(f"evidence: {role} fixture got worse")
    base_tok = sum(b["tokens"] for _, b, _ in parsed)
    cand_tok = sum(c["tokens"] for _, _, c in parsed)
    ratio = cand_tok / base_tok if base_tok else 1.0
    if ratio > pol["max_token_ratio"] + EPS:
        raise Refusal("evidence: token use rose past the cap")
    return {"pass_gain": pass_gain, "judge_gain": judge_gain, "token_ratio": ratio}


def default_privacy_hook(added):
    """added: [(where, text)]. Returns hit strings (location and pattern number
    only). Fails closed without PRIVACY_DENYLIST_FILE, as check-privacy.py does."""
    path = os.environ.get("PRIVACY_DENYLIST_FILE") or ""
    if not os.path.isfile(path):
        return ["privacy: no denylist file; failing closed"]
    with open(path, encoding="utf-8") as f:
        pats = [re.compile(x.strip(), re.I) for x in f
                if x.strip() and not x.lstrip().startswith("#")]
    if not pats:
        return ["privacy: the denylist is empty; failing closed"]
    return [f"{where}: pattern {n}" for where, text in added
            for n, p in enumerate(pats, 1) if p.search(text)]


def validate(evals_repo, sha, head_branch, registry_dir, pol, base="origin/main",
             privacy_hook=default_privacy_hook, registry_ref="HEAD"):
    """Returns (info, reasons). info is filled when the branch is acceptable."""
    try:
        m = BRANCH_RE.match(head_branch)
        if not m:
            raise Refusal("branch-name")
        run_id = m.group(1)
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise Refusal("bad-sha")
        check_paths(changed_entries(evals_repo, base, sha), run_id)
        patch_b = show(evals_repo, sha, f"eval-improve/{run_id}/patch.diff")
        ev_b = show(evals_repo, sha, f"eval-improve/{run_id}/evidence.json")
        if len(patch_b) > pol["max_patch_bytes"] or len(ev_b) > MAX_EVIDENCE_BYTES:
            raise Refusal("too-large")
        try:
            patch, ev_text = patch_b.decode("utf-8"), ev_b.decode("utf-8")
            ev = json.loads(ev_text)
        except ValueError:
            raise Refusal("not-utf8-or-json") from None
        path, hunks, added = parse_patch(patch)
        pm = SKILL_PATH_RE.match(path)
        if not pm:
            raise Refusal("patch-target: not one plugins/<p>/skills/<s>/SKILL.md")
        plugin, skill = pm.groups()
        try:
            old = _git(registry_dir, "show", f"{registry_ref}:{path}").decode("utf-8")
        except (subprocess.CalledProcessError, UnicodeDecodeError):
            raise Refusal("patch-target: not an existing SKILL.md in the registry") from None
        new = apply_hunks(old, hunks)
        check_frontmatter(old, new)
        numbers = check_evidence(ev, pol, plugin, skill, run_id)
        scan = [(f"{path}:{n}", t) for n, t in added]
        scan += [(f"evidence.json:{n}", t) for n, t in enumerate(ev_text.split("\n"), 1)]
        hits = privacy_hook(scan)
        if hits:
            raise Refusal("privacy: " + "; ".join(hits[:5]))
    except Refusal as e:
        return None, [str(e)]
    return {"run_id": run_id, "plugin": plugin, "skill": skill, "path": path,
            "new_text": new, "evidence": ev, **numbers}, []


CLOSING = re.compile(r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b(?=\s*:?\s*(?:\[?\s*(?:[\w.-]+/[\w.-]+)?#\d+|https?://))", re.I)


def sanitize(text):
    """Remove closing keywords before issue refs and links, @ mentions, and
    the autolink of #N, from text copied out of a branch."""
    text = CLOSING.sub("see", text)
    text = re.sub(r"[@＠](?=\w)", "", text)
    return re.sub(r"#(?=\d)", "# ", text)


def pr_body(info, mode_note):
    rows = ["| fixture | role | pass before | pass after | judge before | judge after |",
            "|---|---|---|---|---|---|"]
    for f in info["evidence"]["fixtures"]:
        b, c = f["baseline"], f["candidate"]
        rows.append(f"| {sanitize(str(f.get('id', '?')))[:40]} | {f['role']} | {b['pass_rate']} | "
                    f"{c['pass_rate']} | {b.get('judge')} | {c.get('judge')} |")
    summary = sanitize(str(info["evidence"].get("summary", ""))[:1000])
    return (f"Improve `{info['skill']}` in `{info['plugin']}` (eval run {info['run_id']}).\n\n"
            f"Training gain: {info['pass_gain']:.4g} pass rate, {info['judge_gain']:.4g} judge points. "
            f"Token ratio: {info['token_ratio']:.4g}.\n\n" + "\n".join(rows) +
            f"\n\nRoutine summary (data from the branch, sanitized):\n\n> {summary}\n\n"
            f"{mode_note}\n")


def prepare(info, registry_dir, out_dir, pol):
    plugin_dir = os.path.join(registry_dir, "plugins", info["plugin"])
    why = R.runs_code(plugin_dir)
    draft = why is not None or pol["improve_merge_mode"] == "draft"
    reason = why and f"plugin runs code ({why})" or (
        "improve_merge_mode is draft" if draft else None)
    branch = f"eval-improve/{info['run_id']}"
    R.git(registry_dir, "checkout", "-b", branch)
    with open(os.path.join(registry_dir, info["path"]), "w", encoding="utf-8") as f:
        f.write(info["new_text"])
    old, new = R.bump_plugin(registry_dir, info["plugin"], "patch")
    R.append_impact(registry_dir, (
        f"## {info['run_id'][:8]}: improved `{info['skill']}`\n\n"
        f"- Change: SKILL.md body only; `{info['plugin']}` {old} -> {new}.\n"
        f"- Why: loop improve run {info['run_id']}.\n"
        f"- Eval: training gain {info['pass_gain']:.4g} pass rate, "
        f"{info['judge_gain']:.4g} judge points; validation and holdout not worse; "
        f"token ratio {info['token_ratio']:.4g}.\n"
        f"- Tried and turned down: none.\n"))
    R.git(registry_dir, "add", "-A")
    R.git(registry_dir, "commit", "-m",
          f"Improve {info['skill']} (eval run {info['run_id']})\n\nAgent: claude-code")
    os.makedirs(out_dir, exist_ok=True)
    note = ("Opened as a draft: " + reason + ". It needs a human.") if draft else \
        "Auto-merge is on; required checks decide."
    with open(os.path.join(out_dir, "pr-body.md"), "w", encoding="utf-8") as f:
        f.write(pr_body(info, note))
    result = {"branch": branch, "draft": draft, "draft_reason": reason,
              "title": f"Improve {info['skill']}: {info['run_id']}",
              "plugin": info["plugin"], "skill": info["skill"], "run_id": info["run_id"]}
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return result


def main(argv=None, privacy_hook=default_privacy_hook):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["validate", "prepare"])
    ap.add_argument("--evals-repo", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--head-branch", required=True)
    ap.add_argument("--registry-dir", required=True)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--policy", default="evals/improve-policy.yml")
    ap.add_argument("--out")
    try:
        a = ap.parse_args(argv)
        pol = load_policy(a.policy)
        if a.command == "prepare" and not a.out:
            raise ValueError("prepare needs --out")
    except (SystemExit, ValueError, OSError):
        print("improve_gate: bad usage or policy", file=sys.stderr)
        return 2
    info, reasons = validate(a.evals_repo, a.sha, a.head_branch, a.registry_dir, pol,
                             a.base, privacy_hook)
    if reasons:
        print(json.dumps({"ok": False, "reasons": reasons}))
        return 1
    if a.command == "validate":
        print(json.dumps({"ok": True, "reasons": [], "skill": info["skill"],
                          "plugin": info["plugin"], "run_id": info["run_id"]}))
        return 0
    result = prepare(info, a.registry_dir, a.out, pol)
    print(json.dumps({"ok": True, "reasons": [], **result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
