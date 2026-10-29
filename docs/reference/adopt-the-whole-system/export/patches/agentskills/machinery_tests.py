"""DECISIONS X9, second half: the registry's tests follow the empty plugins.

Runs on the substituted tree, after blank_registry.py. Every change below is
listed in export/DECISIONS.md ("Export record: agentskills") with its reason.
No test of the machinery is removed: the ones that read the shipped layout
now accept an empty skill list, and the README-table tests that used an
empty bundle as their example of "renders nothing" use an unrenderable
entry instead, because an empty bundle now renders a row.
"""

from __future__ import annotations

from pathlib import Path


def _sub(root: Path, rel: str, pairs) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        n = text.count(old)
        if n != 1:
            raise SystemExit(f"machinery_tests: expected 1 occurrence in {rel}, found {n}: {old[:70]!r}")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def apply(root: Path, ctx) -> None:
    # ── scripts/test_check_consistency.py ──────────────────────────────────
    _sub(root, "scripts/test_check_consistency.py", [
        # The layout file may hold an empty skill list (a plugin with no
        # skills yet); it must still be a sorted, duplicate-free list.
        ('''        assert entry["skills"], name
        assert entry["skills"] == sorted(set(entry["skills"])), name
''', '''        assert isinstance(entry["skills"], list), name
        assert entry["skills"] == sorted(set(entry["skills"])), name
'''),
        # A plugin with no skills has no skills/ folder in git (git keeps no
        # empty directory), which is the same as an empty one.
        ('''        on_disk = {p.name for p in (cc.PLUGINS_DIR / name / "skills").iterdir() if p.is_dir()}
''', '''        skills_dir = cc.PLUGINS_DIR / name / "skills"
        on_disk = {p.name for p in skills_dir.iterdir() if p.is_dir()} if skills_dir.is_dir() else set()
'''),
    ])

    # ── scripts/test_generate_readme_table.py ──────────────────────────────
    _sub(root, "scripts/test_generate_readme_table.py", [
        ('''def test_a_bundle_that_renders_no_rows_is_a_problem(plugins_dir):
    # The exact old behaviour of a federated entry: zero rows, a byte-identical
    # table, and a green --check over a README that never mentions the bundle.
    by_plugin, problems = collect(marketplace(local_entry("empty")), plugins_dir)
    assert len(problems) == 1
    assert "empty" in problems[0]
    assert [name for name, _ in by_plugin] == ["empty"]
''', '''def test_a_bundle_with_no_skills_still_renders_one_row(plugins_dir):
    # The exact old behaviour of a federated entry was zero rows, a
    # byte-identical table, and a green --check over a README that never
    # mentions the bundle. A local bundle with no skills yet (a new registry
    # starts that way) must not repeat it: one row, naming where skills go.
    by_plugin, problems = collect(marketplace(local_entry("empty")), plugins_dir)
    assert problems == []
    assert [name for name, _ in by_plugin] == ["empty"]
    (cells,) = table_cells(grt.build_table(by_plugin[0][1]))
    assert cells[0] == "`empty`"
    assert "plugins/empty/skills/" in cells[1]
'''),
        ('''def test_problems_from_several_bundles_accumulate(plugins_dir):
    market = marketplace(local_entry("empty-one"), local_entry("empty-two"))
''', '''def test_problems_from_several_bundles_accumulate(plugins_dir):
    market = marketplace({"name": "mystery-one", "source": {"source": "gitlab", "repo": "o/r"}},
                         {"name": "mystery-two", "source": {"source": "gitlab", "repo": "o/s"}})
'''),
        ('''def test_build_rows_exits_nonzero_when_a_bundle_renders_nothing(monkeypatch, plugins_dir):
    monkeypatch.setattr(grt, "PLUGINS_DIR", plugins_dir)
    monkeypatch.setattr(grt, "load_marketplace", lambda: marketplace(local_entry("empty")))
    with pytest.raises(SystemExit) as excinfo:
        grt.build_rows()
    assert "empty" in str(excinfo.value)
''', '''def test_build_rows_exits_nonzero_when_a_bundle_renders_nothing(monkeypatch, plugins_dir):
    monkeypatch.setattr(grt, "PLUGINS_DIR", plugins_dir)
    unrenderable = {"name": "mystery", "source": {"source": "gitlab", "repo": "o/r"}}
    monkeypatch.setattr(grt, "load_marketplace", lambda: marketplace(unrenderable))
    with pytest.raises(SystemExit) as excinfo:
        grt.build_rows()
    assert "mystery" in str(excinfo.value)
'''),
    ])

    # ── scripts/test_generate_skills_lock.py ───────────────────────────────
    _sub(root, "scripts/test_generate_skills_lock.py", [
        # The bare owner-name bundle label became `my` (C1) and the platform repo became
        # `example-site-platform` (C3), which sort the other way round.
        ('''    assert sorted(lock["skills"]) == ["my/alpha", "example-site-platform/deploy"]
''', '''    assert sorted(lock["skills"]) == ["example-site-platform/deploy", "my/alpha"]
'''),
        # The vendored bootstrap hook cites a test of the skills-doctor
        # skill, which the kit does not ship (X9). The hook is byte for byte
        # _agent-guidance's copy, so the citation stays and is declared here.
        ('''    "test_bump_format_gate_empty_skills",
}
''', '''    "test_bump_format_gate_empty_skills",
    # The source registry's skills-doctor skill (scripts/test_check_provenance.py),
    # which the adoption kit does not ship (its DECISIONS X9); the bootstrap
    # hook, vendored byte for byte, still cites it.
    "test_the_source_limits_are_the_hooks_own",
}
'''),
    ])

    # ── scripts/test_setup_owner_machine.py ────────────────────────────────
    # The legacy whole-directory home link pointed at plugins/coding-local/
    # skills/, which a registry with no skills does not have, so "the target
    # survives" could not be checked. It points at the same plugin's hooks/,
    # a real directory under plugins/ that ships: still "ours" to setup.sh,
    # still a target that must survive the sweep.
    _sub(root, "scripts/test_setup_owner_machine.py", [
        ('''    (home / ".agents" / "skills").symlink_to(REPO / "plugins" / "coding-local" / "skills")
''', '''    (home / ".agents" / "skills").symlink_to(REPO / "plugins" / "coding-local" / "hooks")
'''),
        ('''    assert (REPO / "plugins" / "coding-local" / "skills").is_dir()
''', '''    assert (REPO / "plugins" / "coding-local" / "hooks").is_dir()
'''),
    ])

    # ── scripts/test_generate_skills_lock.py: the dogfood format check ─────
    # --check-format refuses a lock with no skills by design ("no work is not
    # no errors"), and the registry ships none, so this repo's own lock has
    # nothing to judge until its first skill. It skips, saying so, and runs
    # again from the first skill on.
    _sub(root, "scripts/test_generate_skills_lock.py", [
        ('''    of, and it is the reference an adopter compares theirs against."""
    proc = run_generator("--check-format", "-o", str(REPO_ROOT / "skills.lock"))
''', '''    of, and it is the reference an adopter compares theirs against."""
    if not json.loads((REPO_ROOT / "skills.lock").read_text(encoding="utf-8"))["skills"]:
        pytest.skip("this registry's lock lists no skills yet, and --check-format refuses an "
                    "empty lock by design; it runs once the first skill is locked")
    proc = run_generator("--check-format", "-o", str(REPO_ROOT / "skills.lock"))
'''),
    ])

    # ── scripts/test_agent_checks.py ───────────────────────────────────────
    # The runner the sync delivers ships no checks (X9). Its machinery tests
    # (events, ranges, the installer, real git) run against a TEST-ONLY copy
    # with a lesson in its slot that refuses the paths the source's fleet
    # checks refused, so each case keeps its shape. Removed, because each
    # locked only a fleet check the kit does not ship: the two pre-push
    # default-branch tests (push-to-default) and the staged-changes test
    # (staged-index).
    _sub(root, "scripts/test_agent_checks.py", [
        ('''ZERO = "0" * 40
''', '''ZERO = "0" * 40

# The runner ships no checks (the adoption kit's DECISIONS X9), so each scratch
# repo gets a TEST-ONLY copy of it with this lesson in its lesson slot. It
# refuses the paths the source registry's fleet checks refused, so the cases
# below exercise the runner's events, ranges and hook wiring unchanged.
FIXTURE_LESSON = """check_lessons() {
  case "$1" in
    .claude/rules/*)
      problem lesson-test-fixture "$1: the test fixture refuses path-scoped rules." ;;  # lesson: test-fixture
  esac
  case "${1##*/}" in
    .env.example|.env.sample|.env.template) ;;
    .env|.env.*|*.pem|*.key|*.p12|*.pfx|id_rsa|id_ecdsa|id_ed25519)
      problem lesson-test-fixture "$1: the test fixture refuses secret-looking files." ;;  # lesson: test-fixture
  esac
}
"""


def fixture_runner() -> bytes:
    text = RUNNER.read_text(encoding="utf-8")
    for old, new in (('lesson_checks=""\\n', 'lesson_checks="lesson-test-fixture"\\n'),
                     ("check_lessons() {\\n  :\\n}\\n", FIXTURE_LESSON)):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text.encode("utf-8")
'''),
        ('''    shutil.copyfile(RUNNER, repo / ".githooks" / "agent-checks")
''', '''    (repo / ".githooks" / "agent-checks").write_bytes(fixture_runner())
'''),
        ('''@pytest.mark.parametrize("branch", ["main", "master"])
def test_pre_push_refuses_the_default_branch(tmp_path, branch):
    repo = make_repo(tmp_path)
    proc = run_checks(repo, tmp_path, "pre-push", "origin", "url",
                      stdin=push_line(head(repo, tmp_path), f"refs/heads/{branch}"))
    assert proc.returncode == 1
    assert "pull request" in proc.stderr


def test_pre_push_uses_the_remotes_recorded_default_branch(tmp_path):
    repo = make_repo(tmp_path)
    env = env_for(tmp_path)
    sha = head(repo, tmp_path)
    git(repo, env, "update-ref", "refs/remotes/origin/trunk", sha)
    git(repo, env, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    assert run_checks(repo, tmp_path, "pre-push", "origin", "url",
                      stdin=push_line(sha, "refs/heads/trunk")).returncode == 1
    assert run_checks(repo, tmp_path, "pre-push", "origin", "url",
                      stdin=push_line(sha, "refs/heads/main")).returncode == 0


''', ''),
        ('''def test_pre_push_refuses_while_changes_are_staged_but_uncommitted(tmp_path):
    # The trap: a hook refused `git commit`, and the push that follows would
    # publish the commit before it and print an ordinary success.
    repo = make_repo(tmp_path)
    stage(repo, tmp_path, "src/work.py")
    proc = run_checks(repo, tmp_path, "pre-push", "origin", "url",
                      stdin=push_line(head(repo, tmp_path), "refs/heads/feature"))
    assert proc.returncode == 1
    assert "staged changes" in proc.stderr


''', ''),
    ])
