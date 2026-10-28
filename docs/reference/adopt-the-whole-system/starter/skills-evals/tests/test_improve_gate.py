import json
import os
import subprocess

import pytest

import improve_gate as G

RID = "20261028T120000Z-1a2b3c"
BRANCH = f"claude/eval-improve-{RID}"
OLD = "---\nname: demo\ndescription: Demo skill. Use when testing.\n---\n\n# Demo\n\nStep one.\nStep two.\n"
NEW = OLD.replace("Step two.\n", "Step two, with a check.\nStep three.\n")
PATH = "plugins/p1/skills/demo/SKILL.md"
POL = {**G.DEFAULT_POLICY}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com",
                           *args], capture_output=True, check=True, text=True).stdout


def init(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    return path


@pytest.fixture
def registry(tmp_path):
    r = init(tmp_path / "registry")
    for rel, text in {
        PATH: OLD,
        "plugins/p1/.claude-plugin/plugin.json": json.dumps({"name": "p1", "version": "1.2.3"}, indent=2) + "\n",
        "plugins/p1/skills/other/SKILL.md": OLD.replace("demo", "other"),
        "docs/skill-impact.md": "# Skill impact\n",
    }.items():
        f = r / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


def make_patch(registry, rel=PATH, new=NEW):
    f = registry / rel
    old = f.read_text()
    f.write_text(new)
    patch = git(registry, "diff", "--no-color")
    f.write_text(old)
    return patch


def fx(role, id_, b=0.5, c=0.5, bj=None, cj=None, bt=100, ct=100, be=0, ce=0):
    return {"id": id_, "role": role,
            "baseline": {"pass_rate": b, "judge": bj, "tokens": bt, "errors": be},
            "candidate": {"pass_rate": c, "judge": cj, "tokens": ct, "errors": ce}}


def good_evidence(**over):
    ev = {"schema": 1, "run_id": RID, "skill": "demo", "plugin": "p1", "summary": "Tightened step two.",
          "fixtures": [fx("holdout", "h", 0.5, 0.5), fx("validation", "v", 0.5, 0.6),
                       fx("train", "t1", 0.4, 0.6), fx("train", "t2", 0.5, 0.7)]}
    ev.update(over)
    return ev


@pytest.fixture
def evals(tmp_path):
    e = init(tmp_path / "evals")
    (e / "README.md").write_text("evals\n")
    git(e, "add", "-A")
    git(e, "commit", "-qm", "init")
    return e


def push_branch(evals, files, symlinks=()):
    git(evals, "checkout", "-q", "-b", "work")
    for rel, data in files.items():
        f = evals / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(data if isinstance(data, str) else json.dumps(data))
    for rel, target in symlinks:
        f = evals / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, f)
    git(evals, "add", "-A")
    git(evals, "commit", "-qm", "branch")
    sha = git(evals, "rev-parse", "HEAD").strip()
    git(evals, "checkout", "-q", "main")
    return sha


def branch_files(registry, patch=None, ev=None, rid=RID):
    return {f"eval-improve/{rid}/patch.diff": patch if patch is not None else make_patch(registry),
            f"eval-improve/{rid}/evidence.json": ev if ev is not None else good_evidence()}


def check(evals, registry, sha, branch=BRANCH, pol=POL, hook=lambda added: []):
    return G.validate(str(evals), sha, branch, str(registry), pol, base="main", privacy_hook=hook)


def refused(evals, registry, sha, why, **kw):
    info, reasons = check(evals, registry, sha, **kw)
    assert info is None and reasons and why in reasons[0], reasons
    return reasons


def test_happy_path(evals, registry):
    sha = push_branch(evals, branch_files(registry))
    info, reasons = check(evals, registry, sha)
    assert reasons == [] and info["skill"] == "demo" and info["plugin"] == "p1"
    assert info["new_text"] == NEW
    assert info["pass_gain"] == pytest.approx(0.2)


def test_validate_never_checks_the_branch_out(evals, registry):
    sha = push_branch(evals, branch_files(registry))
    check(evals, registry, sha)
    assert git(evals, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert not (evals / "eval-improve").exists()


@pytest.mark.parametrize("branch", [
    "main", "claude/eval-improve-", "claude/eval-improve-abc", f"{BRANCH};rm -rf",
    f"{BRANCH}\n", f"x/{BRANCH}", f"claude/eval-results-{RID}", f"claude/eval-improve-{RID}-2",
    "claude/eval-improve-20261028T120000Z-1A2B3C"])
def test_bad_branch_name(evals, registry, branch):
    sha = push_branch(evals, branch_files(registry))
    refused(evals, registry, sha, "branch-name", branch=branch)


def test_extra_path(evals, registry):
    files = branch_files(registry)
    files["scripts/run_me.py"] = "print(1)\n"
    refused(evals, registry, push_branch(evals, files), "path-not-allowed")


def test_path_under_another_run_id(evals, registry):
    files = branch_files(registry, rid="20261028T120000Z-ffffff")
    refused(evals, registry, push_branch(evals, files), "path-not-allowed")


def test_missing_evidence_file(evals, registry):
    files = branch_files(registry)
    del files[f"eval-improve/{RID}/evidence.json"]
    refused(evals, registry, push_branch(evals, files), "exactly patch.diff and evidence.json")


def test_symlink_is_refused(evals, registry):
    files = branch_files(registry)
    del files[f"eval-improve/{RID}/evidence.json"]
    sha = push_branch(evals, files, symlinks=[(f"eval-improve/{RID}/evidence.json", "/etc/passwd")])
    refused(evals, registry, sha, "not-a-plain-added-file")


def test_frontmatter_edit(evals, registry):
    bad = NEW.replace("Use when testing.", "Use always.")
    sha = push_branch(evals, branch_files(registry, patch=make_patch(registry, new=bad)))
    refused(evals, registry, sha, "frontmatter-changed")


def test_two_file_patch(evals, registry):
    two = make_patch(registry) + make_patch(registry, "plugins/p1/skills/other/SKILL.md",
                                            OLD.replace("demo", "other") + "More.\n")
    refused(evals, registry, push_branch(evals, branch_files(registry, patch=two)), "patch-format")


def test_patch_to_a_non_skill_file(evals, registry):
    other = make_patch(registry, "docs/skill-impact.md", "# Skill impact\nchanged\n")
    refused(evals, registry, push_branch(evals, branch_files(registry, patch=other)), "patch-target")


def test_new_file_and_mode_change_headers_are_refused(evals, registry):
    p = make_patch(registry).replace("index ", "new file mode 100644\nindex ", 1)
    refused(evals, registry, push_branch(evals, branch_files(registry, patch=p)), "patch-format")


def test_patch_that_does_not_apply(evals, registry):
    p = make_patch(registry).replace(" Step one.", " Step uno.")
    refused(evals, registry, push_branch(evals, branch_files(registry, patch=p)), "patch-apply")


def test_patch_over_the_size_cap(evals, registry):
    sha = push_branch(evals, branch_files(registry))
    refused(evals, registry, sha, "too-large", pol={**POL, "max_patch_bytes": 50})


@pytest.mark.parametrize("name,ev,why", [
    ("three fixtures", good_evidence(fixtures=good_evidence()["fixtures"][:3]), "fewer than the minimum"),
    ("holdout not first", good_evidence(fixtures=list(reversed(good_evidence()["fixtures"]))), "holdout"),
    ("gain too small", good_evidence(fixtures=[fx("holdout", "h"), fx("validation", "v"),
                                               fx("train", "a", 0.4, 0.45), fx("train", "b", 0.4, 0.45)]),
     "training gain"),
    ("validation worse", good_evidence(fixtures=[fx("holdout", "h"), fx("validation", "v", 0.5, 0.4),
                                                 fx("train", "a", 0.4, 0.7), fx("train", "b", 0.4, 0.7)]),
     "validation"),
    ("holdout regressed", good_evidence(fixtures=[fx("holdout", "h", 0.5, 0.4), fx("validation", "v"),
                                                  fx("train", "a", 0.4, 0.7), fx("train", "b", 0.4, 0.7)]),
     "holdout fixture got worse"),
    ("errored", good_evidence(fixtures=[fx("holdout", "h"), fx("validation", "v"),
                                        fx("train", "a", 0.4, 0.7, ce=1), fx("train", "b", 0.4, 0.7)]),
     "errored"),
    ("tokens up too far", good_evidence(fixtures=[fx("holdout", "h", ct=400), fx("validation", "v"),
                                                  fx("train", "a", 0.4, 0.7), fx("train", "b", 0.4, 0.7)]),
     "token"),
    ("wrong skill", good_evidence(skill="other"), "evidence-mismatch"),
    ("two holdouts", good_evidence(fixtures=[fx("holdout", "h"), fx("holdout", "h2"),
                                             fx("train", "a", 0.4, 0.7), fx("train", "b", 0.4, 0.7)]), "holdout"),
])
def test_failing_evidence(evals, registry, name, ev, why):
    sha = push_branch(evals, branch_files(registry, ev=ev))
    refused(evals, registry, sha, why)


def test_exactly_the_bar_is_enough_and_tokens_may_rise_with_the_gain(evals, registry):
    ev = good_evidence(fixtures=[fx("holdout", "h", ct=130), fx("validation", "v"),
                                 fx("train", "a", 0.4, 0.5), fx("train", "b", 0.3, 0.4)])
    info, reasons = check(evals, registry, push_branch(evals, branch_files(registry, ev=ev)))
    assert reasons == [] and info["token_ratio"] > 1


def test_judge_point_counts_only_when_pass_rates_tie(evals, registry):
    ev = good_evidence(fixtures=[fx("holdout", "h"), fx("validation", "v"),
                                 fx("train", "a", 0.5, 0.5, 6.0, 7.0), fx("train", "b", 0.5, 0.5, 6.0, 7.0)])
    assert check(evals, registry, push_branch(evals, branch_files(registry, ev=ev)))[1] == []
    ev["fixtures"][2]["candidate"]["judge"] = 6.5
    ev["fixtures"][3]["candidate"]["judge"] = 6.5
    sha = push_branch_again(evals, registry, ev)
    refused(evals, registry, sha, "training gain")


def push_branch_again(evals, registry, ev):
    git(evals, "branch", "-D", "work")
    return push_branch(evals, branch_files(registry, ev=ev))


def test_privacy_hook_sees_added_lines_and_can_refuse(evals, registry):
    sha = push_branch(evals, branch_files(registry))
    seen = []

    def hook(added):
        seen.extend(added)
        return ["SKILL.md:11: pattern 2"]
    reasons = refused(evals, registry, sha, "privacy", hook=hook)
    assert "pattern 2" in reasons[0]
    assert any(t == "Step three." and w.startswith(PATH) for w, t in seen)


def test_default_privacy_hook_fails_closed_and_prints_no_text(tmp_path, monkeypatch):
    monkeypatch.delenv("PRIVACY_DENYLIST_FILE", raising=False)
    assert "failing closed" in G.default_privacy_hook([("a:1", "x")])[0]
    deny = tmp_path / "deny"
    deny.write_text("# c\nsecretname\n")
    monkeypatch.setenv("PRIVACY_DENYLIST_FILE", str(deny))
    assert G.default_privacy_hook([("a:1", "ok"), ("b:2", "has SecretName")]) == ["b:2: pattern 1"]


def test_sanitize_strips_closing_keywords_and_mentions():
    text = "Closes #12, fixes owner/repo#3, Resolved: https://github.com/o/r/issues/4 and thanks @someone"
    out = G.sanitize(text)
    assert "@" not in out
    for word in ("Closes", "fixes", "Resolved"):
        assert word not in out
    assert "#12" not in out and "# 12" in out
    assert G.sanitize("Fixes the bug in step 2") == "Fixes the bug in step 2"


def test_prepare_applies_bumps_and_sanitizes(evals, registry, tmp_path):
    ev = good_evidence(summary="Closes #9. Ask @someone about it.")
    sha = push_branch(evals, branch_files(registry, ev=ev))
    info, reasons = check(evals, registry, sha)
    assert reasons == []
    out = tmp_path / "out"
    result = G.prepare(info, str(registry), str(out), POL)
    assert result["draft"] is False and result["branch"] == f"eval-improve/{RID}"
    assert git(registry, "rev-parse", "--abbrev-ref", "HEAD").strip() == f"eval-improve/{RID}"
    assert (registry / PATH).read_text() == NEW
    assert json.loads((registry / "plugins/p1/.claude-plugin/plugin.json").read_text())["version"] == "1.2.4"
    assert "improved `demo`" in (registry / "docs/skill-impact.md").read_text()
    body = (out / "pr-body.md").read_text()
    assert "@" not in body and "Closes" not in body and "#9" not in body
    assert git(registry, "log", "--format=%s", "-1").strip().startswith("Improve demo")
    assert git(registry, "status", "--porcelain") == ""


def test_plugin_that_runs_code_becomes_a_draft(evals, registry, tmp_path):
    (registry / "plugins/p1/hooks").mkdir()
    (registry / "plugins/p1/hooks/h.sh").write_text("echo hi\n")
    git(registry, "add", "-A")
    git(registry, "commit", "-qm", "hooks")
    info, _ = check(evals, registry, push_branch(evals, branch_files(registry)))
    result = G.prepare(info, str(registry), str(tmp_path / "o"), POL)
    assert result["draft"] is True and "hooks/" in result["draft_reason"]
    assert "draft" in (tmp_path / "o" / "pr-body.md").read_text().lower()


def test_bin_dir_and_plugin_json_hooks_also_count(tmp_path):
    p = tmp_path / "plug"
    (p / "bin").mkdir(parents=True)
    import registry_edit as R
    assert R.runs_code(str(p)) == "has bin/"
    (p / "bin").rmdir()
    (p / ".claude-plugin").mkdir()
    (p / ".claude-plugin/plugin.json").write_text('{"hooks": {}}')
    assert R.runs_code(str(p)) == "plugin.json names hooks"


def test_draft_merge_mode_makes_a_draft(evals, registry, tmp_path):
    info, _ = check(evals, registry, push_branch(evals, branch_files(registry)))
    result = G.prepare(info, str(registry), str(tmp_path / "o"), {**POL, "improve_merge_mode": "draft"})
    assert result["draft"] is True and result["draft_reason"] == "improve_merge_mode is draft"


def test_cli_exit_codes(evals, registry, tmp_path, capsys):
    sha = push_branch(evals, branch_files(registry))
    pol = tmp_path / "pol.yml"
    pol.write_text("improve_merge_mode: auto\n")
    args = ["validate", "--evals-repo", str(evals), "--sha", sha, "--head-branch", BRANCH,
            "--registry-dir", str(registry), "--base", "main", "--policy", str(pol)]
    assert G.main(args, privacy_hook=lambda a: []) == 0
    assert G.main(args[:6] + ["main"] + args[7:], privacy_hook=lambda a: []) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["ok"] is False
    assert G.main(["prepare"] + args[1:], privacy_hook=lambda a: []) == 2   # no --out
    out = tmp_path / "out"
    assert G.main(["prepare"] + args[1:] + ["--out", str(out)], privacy_hook=lambda a: []) == 0
    assert (out / "result.json").exists()


def test_workflows_follow_the_pattern():
    import yaml
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".github", "workflows")
    pushed = yaml.safe_load(open(os.path.join(root, "routine-improve-pushed.yml")))
    assert pushed["permissions"] == {}
    trig = pushed[True]   # YAML reads the key "on" as True
    assert trig["push"]["branches"] == ["claude/eval-improve-*"] and trig["push"]["paths"] == ["eval-improve/**"]
    gate = yaml.safe_load(open(os.path.join(root, "routine-improve-gate.yml")))
    assert gate[True]["workflow_run"]["workflows"] == ["routine-improve-pushed"]
    assert gate["jobs"]["open-pr"]["environment"] == "loop-writer"
    assert "environment" not in gate["jobs"]["prepare"] or gate["jobs"]["prepare"]["environment"] == "privacy"
    steps = gate["jobs"]["open-pr"]["steps"]
    names = [s.get("id") or s.get("name") for s in steps]
    assert names.index("app") + 1 == names.index("Open the pull request")
    text = open(os.path.join(root, "routine-improve-gate.yml")).read()
    assert "gh pr merge --auto --merge" in text and "--draft" in text
