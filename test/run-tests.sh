#!/usr/bin/env bash
set -euo pipefail
#
# run-tests.sh — Integration tests for the sync and drift-report scripts.
#
# Creates mock git repos and a fake `gh` CLI to validate the full pipeline
# without needing GitHub access.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEST_DIR=$(mktemp -d)
PASS=0
FAIL=0

trap 'rm -rf "$TEST_DIR"' EXIT

# These tests must never reach the network. sync.sh rewrites each clone's
# `origin` to https://github.com/<repo> whenever GH_TOKEN is set, so an
# ambient token in a developer's shell silently redirects every push away from
# the mock bare repos and out to real GitHub — the suite then fails with
# "access denied"/403 noise that looks like a code regression and is not. CI
# never exports a plain GH_TOKEN (only GH_TOKEN_<OWNER>), so this only ever
# bit local runs; unsetting both here makes the run deterministic everywhere.
unset GH_TOKEN GITHUB_TOKEN

# Ensure git identity is configured (CI runners may not have this set globally).
if ! git config --global user.name &>/dev/null; then
    git config --global user.name "test-runner"
fi
if ! git config --global user.email &>/dev/null; then
    git config --global user.email "test@localhost"
fi

# ── Helpers ────────────────────────────────────────────────────────────────

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }
# `--` before the pattern, in both. Without it grep parses a needle that starts
# with a dash as an OPTION: it then exits 2, and 2>/dev/null turns that into
# "no match" — which fails an assert_contains loudly but passes every
# assert_not_contains SILENTLY, making the assertion vacuous. Caught by a
# negative assertion on "--match-head-commit"; the older " --squash" one reads
# as deliberate but only avoids the bug by starting with a space.
assert_contains() {
    if grep -qF -- "$2" "$1" 2>/dev/null; then pass "$3"; else fail "$3 — expected '$2' in $1"; fi
}
assert_not_contains() {
    if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$3 — did not expect '$2' in $1"; else pass "$3"; fi
}
assert_row_contains() {
    if grep -F -- "$2" "$1" | grep -qF -- "$3"; then pass "$4"; else fail "$4 — expected '$3' in row '$2' of $1"; fi
}
# Ordering between two things a run printed. Both greps are captured with an
# explicit failure branch: a needle that is absent exits 1, and under the
# suite's `set -euo pipefail` an unguarded command substitution would end the
# whole run instead of failing one assertion.
assert_line_before() {   # <file> <needle A> <needle B> <label>
    local a b
    a=$(grep -nF "$2" "$1" | head -1 | cut -d: -f1) || a=""
    b=$(grep -nF "$3" "$1" | head -1 | cut -d: -f1) || b=""
    if [[ -n "$a" && -n "$b" && "$a" -lt "$b" ]]; then
        pass "$4"
    else
        fail "$4 — '$2' at line ${a:-none}, '$3' at line ${b:-none}"
    fi
}

# The sync now pushes directly to main, mutating shared bare-repo state that
# the old branch-only model left untouched. Snapshot the pristine bares after
# setup so tests that must observe the pre-sync state (the drift report; the
# re-run "N synced" counts) can restore it.
snapshot_bare_repos() {
    rm -rf "$TEST_DIR/bare-pristine"
    cp -a "$TEST_DIR/bare" "$TEST_DIR/bare-pristine"
}
reset_bare_repos() {
    rm -rf "$TEST_DIR/bare"
    cp -a "$TEST_DIR/bare-pristine" "$TEST_DIR/bare"
}

# Install a pre-receive hook on a bare repo that rejects any update to
# refs/heads/main (GH013-like) while allowing every other ref — simulating a
# branch-protected default branch the sync's direct push cannot reach, so it
# must fall back to a PR.
install_reject_main_hook() {
    local bare="$1"
    cat > "$bare/hooks/pre-receive" <<'HOOK'
#!/bin/sh
while read -r _old _new ref; do
    if [ "$ref" = "refs/heads/main" ]; then
        echo "remote: error: GH013: Repository rule violations found for refs/heads/main." 1>&2
        exit 1
    fi
done
exit 0
HOOK
    chmod +x "$bare/hooks/pre-receive"
}

# The other ref a GitHub ruleset can restrict: creating the bot's bump branch.
# GitHub prints `! [remote rejected]` for this exactly as it does for a
# non-fast-forward, which is why the bumper must not classify by that word.
install_reject_bump_branch_hook() {
    local bare="$1"
    cat > "$bare/hooks/pre-receive" <<'HOOK'
#!/bin/sh
while read -r _old _new ref; do
    case "$ref" in
        refs/heads/skills-lock-bump/*)
            echo "remote: error: GH013: Repository rule violations found for $ref." 1>&2
            echo "remote: Cannot create ref due to creations being restricted." 1>&2
            exit 1
            ;;
    esac
done
exit 0
HOOK
    chmod +x "$bare/hooks/pre-receive"
}

# ── Set up mock repos as bare git repos ────────────────────────────────────

setup_mock_repos() {
    echo "Setting up mock repos..."

    # Disable commit signing for test repos (CI environment may enforce signing)
    GIT_NOSIGN=(-c commit.gpgsign=false -c tag.gpgsign=false)

    # Mock repo 1: has .agents-sync.yml requesting python + docker
    local repo1_bare="$TEST_DIR/bare/testorg_repo-with-sync"
    local repo1_work="$TEST_DIR/work/repo-with-sync"
    mkdir -p "$repo1_bare" "$repo1_work"
    git init --bare --initial-branch=main "$repo1_bare" >/dev/null 2>&1
    git init --initial-branch=main "$repo1_work" >/dev/null 2>&1
    cd "$repo1_work"
    git config commit.gpgsign false
    git remote add origin "$repo1_bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - python
  - docker
YAML
    git add .agents-sync.yml
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1

    # Mock repo 2: no .agents-sync.yml, no AGENTS.md
    local repo2_bare="$TEST_DIR/bare/testorg_repo-no-sync"
    local repo2_work="$TEST_DIR/work/repo-no-sync"
    mkdir -p "$repo2_bare" "$repo2_work"
    git init --bare --initial-branch=main "$repo2_bare" >/dev/null 2>&1
    git init --initial-branch=main "$repo2_work" >/dev/null 2>&1
    cd "$repo2_work"
    git config commit.gpgsign false
    git remote add origin "$repo2_bare"
    echo "# hello" > README.md
    git add README.md
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1

    # Mock repo 3: has existing AGENTS.md with repo-specific content
    local repo3_bare="$TEST_DIR/bare/testorg_repo-with-existing"
    local repo3_work="$TEST_DIR/work/repo-with-existing"
    mkdir -p "$repo3_bare" "$repo3_work"
    git init --bare --initial-branch=main "$repo3_bare" >/dev/null 2>&1
    git init --initial-branch=main "$repo3_work" >/dev/null 2>&1
    cd "$repo3_work"
    git config commit.gpgsign false
    git remote add origin "$repo3_bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - go
YAML
    cat > AGENTS.md <<'MD'
# old managed stuff
This will be overwritten.

## Repo-specific additions

Keep this custom content!
Do not delete me.
MD
    # Standard two-line bridge (byte-identical to what sync.sh writes) —
    # makes this repo the bridge-ok drift case; sync must not warn for it.
    cat > CLAUDE.md <<'MD'
<!-- Managed by _agent-guidance: bridges Claude Code (which reads CLAUDE.md) to AGENTS.md. -->
@AGENTS.md
MD
    git add .agents-sync.yml AGENTS.md CLAUDE.md
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1

    # Mock repo 4: has existing AGENTS.md WITHOUT the repo-specific marker
    local repo4_bare="$TEST_DIR/bare/testorg_repo-existing-no-marker"
    local repo4_work="$TEST_DIR/work/repo-existing-no-marker"
    mkdir -p "$repo4_bare" "$repo4_work"
    git init --bare --initial-branch=main "$repo4_bare" >/dev/null 2>&1
    git init --initial-branch=main "$repo4_work" >/dev/null 2>&1
    cd "$repo4_work"
    git config commit.gpgsign false
    git remote add origin "$repo4_bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - python
YAML
    cat > AGENTS.md <<'MD'
# Our Custom Agent Guide

Follow these repo-specific rules when working in this codebase.

- Always run linting before commits
- Use conventional commit messages
MD
    git add .agents-sync.yml AGENTS.md
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1

    # Central repos.yml fixture is written by setup_bootstrap_repos (it needs
    # the stub hook's digest), which runs right after this function.

    # Mock repo 5: has .agents-sync.yml (typescript) and a pre-existing
    # CLAUDE.md that does NOT import @AGENTS.md, no AGENTS.md.
    local repo5_bare="$TEST_DIR/bare/testorg_repo-with-claude-md"
    local repo5_work="$TEST_DIR/work/repo-with-claude-md"
    mkdir -p "$repo5_bare" "$repo5_work"
    git init --bare --initial-branch=main "$repo5_bare" >/dev/null 2>&1
    git init --initial-branch=main "$repo5_work" >/dev/null 2>&1
    cd "$repo5_work"
    git config commit.gpgsign false
    git remote add origin "$repo5_bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - typescript
YAML
    # Content mirrors the real adamdaniel.ai#2545 failure shape (a
    # markdown LINK to AGENTS.md, not an import) plus a fenced example of
    # the real bridge syntax — proving the classifier is fence-aware: the
    # old unanchored `grep -qF '@AGENTS.md'` check would have been fooled
    # by the fenced line into silently treating this as bridged.
    cat > CLAUDE.md <<'MD'
# My hand-written Claude notes

Some pre-existing instructions that do not reference AGENTS.md.

See [AGENTS.md](./AGENTS.md) for the agent guidance.

Example bridge syntax (illustration only — fenced, so it is not a real
import):

```
@AGENTS.md
```
MD
    cp CLAUDE.md "$TEST_DIR/repo-with-claude-md.CLAUDE.md.orig"
    git add .agents-sync.yml CLAUDE.md
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1

    # Mock repo 6: repo-excluded — deliberately NOT set up as a bare repo.
    # It must only appear in the fake `gh repo list` JSON output, so that if
    # the exclusion filter is ever broken, `gh repo clone` fails loudly
    # ("mock repo ... not found") instead of the test silently passing.

    # Mock repo 7: AGENTS.md already up to date (built via the real build
    # script so it can't drift from the actual implementation), no CLAUDE.md.
    local repo7_bare="$TEST_DIR/bare/testorg_repo-up-to-date-no-claude"
    local repo7_work="$TEST_DIR/work/repo-up-to-date-no-claude"
    mkdir -p "$repo7_bare" "$repo7_work"
    git init --bare --initial-branch=main "$repo7_bare" >/dev/null 2>&1
    git init --initial-branch=main "$repo7_work" >/dev/null 2>&1
    cd "$repo7_work"
    git config commit.gpgsign false
    git remote add origin "$repo7_bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - python
YAML
    local managed_for_repo7 marker_block
    managed_for_repo7=$("$REPO_ROOT/scripts/build-agents-md.sh" python)
    marker_block="$(printf '%s\n\n%s\n' \
        "## Repo-specific additions" \
        "<!-- Add your repo-specific agent guidance below this line -->")"
    printf '%s\n%s\n' "$managed_for_repo7" "$marker_block" > AGENTS.md
    git add .agents-sync.yml AGENTS.md
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1

    # Mock repo 9: testorg/repo-fix-claude — opts into fix_claude_md: true.
    # AGENTS.md already up to date (built the same way as repo 7); CLAUDE.md
    # is present but pointer-only (no-import). Exercises both the extended
    # skip condition (agents_up_to_date && claude_md_present is no longer
    # enough to skip when a fix is pending) and the opted-in rewrite path.
    local repo9_bare="$TEST_DIR/bare/testorg_repo-fix-claude"
    local repo9_work="$TEST_DIR/work/repo-fix-claude"
    mkdir -p "$repo9_bare" "$repo9_work"
    git init --bare --initial-branch=main "$repo9_bare" >/dev/null 2>&1
    git init --initial-branch=main "$repo9_work" >/dev/null 2>&1
    cd "$repo9_work"
    git config commit.gpgsign false
    git remote add origin "$repo9_bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - python
fix_claude_md: true
YAML
    local managed_for_repo9 marker_block_repo9
    managed_for_repo9=$("$REPO_ROOT/scripts/build-agents-md.sh" python)
    marker_block_repo9="$(printf '%s\n\n%s\n' \
        "## Repo-specific additions" \
        "<!-- Add your repo-specific agent guidance below this line -->")"
    printf '%s\n%s\n' "$managed_for_repo9" "$marker_block_repo9" > AGENTS.md
    cat > CLAUDE.md <<'MD'
See [AGENTS.md](./AGENTS.md) for the agent guidance.
MD
    git add .agents-sync.yml AGENTS.md CLAUDE.md
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1

    # Mock repo 8: testorg2/repo-owner2-only — no .agents-sync.yml, no
    # AGENTS.md; verifies SYNC_OWNERS scans a second owner and falls back to
    # default_sections (rust) like repo-no-sync does.
    local repo8_bare="$TEST_DIR/bare/testorg2_repo-owner2-only"
    local repo8_work="$TEST_DIR/work/repo-owner2-only"
    mkdir -p "$repo8_bare" "$repo8_work"
    git init --bare --initial-branch=main "$repo8_bare" >/dev/null 2>&1
    git init --initial-branch=main "$repo8_work" >/dev/null 2>&1
    cd "$repo8_work"
    git config commit.gpgsign false
    git remote add origin "$repo8_bare"
    echo "# hello" > README.md
    git add README.md
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1

    # Mock repo: testorg2/_agent-guidance — deliberately NOT set up as a bare
    # repo. It must only appear in the fake `gh repo list` JSON output for
    # testorg2, so that if the self-repo exclusion filter is ever broken for
    # a second owner, `gh repo clone` fails loudly instead of the test
    # silently passing.

    # Mock repo 10: protorg/repo-protected — its bare repo rejects any update
    # to refs/heads/main (pre-receive hook), simulating a branch-protected
    # default branch. The direct push must fail and the sync must fall back to
    # a PR + auto-merge. Pre-existing no-import CLAUDE.md so the fallback PR
    # body carries the "does not import" warning.
    local repo10_bare="$TEST_DIR/bare/protorg_repo-protected"
    local repo10_work="$TEST_DIR/work/repo-protected"
    mkdir -p "$repo10_bare" "$repo10_work"
    git init --bare --initial-branch=main "$repo10_bare" >/dev/null 2>&1
    git init --initial-branch=main "$repo10_work" >/dev/null 2>&1
    cd "$repo10_work"
    git config commit.gpgsign false
    git remote add origin "$repo10_bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - python
YAML
    cat > CLAUDE.md <<'MD'
See [AGENTS.md](./AGENTS.md) for the agent guidance.
MD
    git add .agents-sync.yml CLAUDE.md
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    # A pre-existing, DIVERGED stale agents-md-sync/update branch from the old
    # PR-era: built on a sibling commit (not an ancestor of the sync's fresh
    # HEAD), so the fallback's branch push is non-fast-forward and must force.
    git checkout -b agents-md-sync/update >/dev/null 2>&1
    printf 'stale old sync content\n' > AGENTS.md
    git add AGENTS.md
    git commit -m "stale sync" >/dev/null 2>&1
    git push origin HEAD:agents-md-sync/update >/dev/null 2>&1
    git checkout main >/dev/null 2>&1
    # Hook installed AFTER the setup pushes so seeding succeeds; only the
    # sync's later direct push to main is rejected.
    install_reject_main_hook "$repo10_bare"

    # Mock repo 11: protorg/repo-protected-fix — protected (same hook) and
    # opted into fix_claude_md: true with an already-up-to-date AGENTS.md and a
    # pointer-only CLAUDE.md, so the fallback PR body carries the fix_claude_md
    # opt-in note.
    local repo11_bare="$TEST_DIR/bare/protorg_repo-protected-fix"
    local repo11_work="$TEST_DIR/work/repo-protected-fix"
    mkdir -p "$repo11_bare" "$repo11_work"
    git init --bare --initial-branch=main "$repo11_bare" >/dev/null 2>&1
    git init --initial-branch=main "$repo11_work" >/dev/null 2>&1
    cd "$repo11_work"
    git config commit.gpgsign false
    git remote add origin "$repo11_bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - python
fix_claude_md: true
YAML
    local managed_for_repo11 marker_block_repo11
    managed_for_repo11=$("$REPO_ROOT/scripts/build-agents-md.sh" python)
    marker_block_repo11="$(printf '%s\n\n%s\n' \
        "## Repo-specific additions" \
        "<!-- Add your repo-specific agent guidance below this line -->")"
    printf '%s\n%s\n' "$managed_for_repo11" "$marker_block_repo11" > AGENTS.md
    cat > CLAUDE.md <<'MD'
See [AGENTS.md](./AGENTS.md) for the agent guidance.
MD
    git add .agents-sync.yml AGENTS.md CLAUDE.md
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    install_reject_main_hook "$repo11_bare"

    # Mock repo 12: stalorg/repo-stale — unprotected, but carries a
    # pre-existing agents-md-sync/update branch (and a still-"open" PR #42 via
    # MOCK_OPEN_PR_REPOS in the stale-cleanup test). After a successful direct
    # push the sync must close PR #42 and delete the stale branch.
    local repo12_bare="$TEST_DIR/bare/stalorg_repo-stale"
    local repo12_work="$TEST_DIR/work/repo-stale"
    mkdir -p "$repo12_bare" "$repo12_work"
    git init --bare --initial-branch=main "$repo12_bare" >/dev/null 2>&1
    git init --initial-branch=main "$repo12_work" >/dev/null 2>&1
    cd "$repo12_work"
    git config commit.gpgsign false
    git remote add origin "$repo12_bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - python
YAML
    git add .agents-sync.yml
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    # Pre-existing stale sync branch left behind by the old branch + PR model.
    git push origin HEAD:agents-md-sync/update >/dev/null 2>&1

    cd "$REPO_ROOT"
}

# ── Set up the skills-bootstrap fixtures ───────────────────────────────────
#
# Delivery is opt-in and DOUBLE-KEYED (repos.yml allowlist AND the target repo
# already carrying its own skills.lock), so the fixtures below exist to pin
# each key, plus the two ways delivery must degrade instead of damaging
# something:
#
#   bootorg/agentskills        the registry the pinned hook is fetched FROM
#                              (absent from `gh repo list` by default so it is
#                              never itself a sync target here; MOCK_INCLUDE_
#                              REGISTRY=1 puts it back for the one drift-report
#                              sub-test that needs the registry as a TARGET)
#   bootorg/repo-adopted       allowlisted + federated skills.lock + an
#                              EXISTING SessionStart entry → the happy path,
#                              and the append-don't-overwrite regression. Also
#                              carries an UNRELATED `.gitignore`, so `blocked`
#                              has to key on a rule that matches the artifact
#                              rather than on the file merely existing
#   bootorg/repo-hook-no-lock  allowlisted + hook already committed, but NO
#                              lock → the state no sync ever revisits: the
#                              hook prints `skills: DEGRADED` into every
#                              session forever
#   bootorg/repo-ignored       allowlisted + lock, but `.claude/` is
#                              gitignored → `git add` on an ignored path exits
#                              1, which under `set -euo pipefail` would abort
#                              the WHOLE FLEET RUN. It sorts third, so if the
#                              guard regresses, every repo after it silently
#                              stops syncing
#   bootorg/repo-no-lock       allowlisted, no lock → withhold (the hook with
#                              no lock is a permanent DEGRADED verdict, not a
#                              no-op)
#   bootorg/repo-not-allowed   has a lock but is NOT allowlisted → nothing
#   bootorg/repo-unparseable   allowlisted + lock, but settings.json is not
#                              valid JSON → refuse to touch it, deliver nothing
setup_bootstrap_repos() {
    echo "Setting up skills-bootstrap fixtures..."

    # The stub stands in for the real 46 KB hook: the sync only ever copies
    # bytes and compares digests, so its CONTENT is irrelevant to these tests
    # and a 4-line file keeps the fixtures readable.
    local hook_src="$TEST_DIR/pinned-hook.sh"
    cat > "$hook_src" <<'HOOK'
#!/usr/bin/env bash
# stub skills-bootstrap hook (test fixture)
echo '{"hookSpecificOutput":{"hookEventName":"SessionStart"}}'
HOOK
    BOOTSTRAP_HOOK_SHA=$(sha256sum "$hook_src" | cut -d' ' -f1)

    # A federated lock, mirroring adamdaniel.ai's live shape: two sources. If
    # the sync ever writes a lock, this is what it would flatten.
    cat > "$TEST_DIR/federated.lock" <<'LOCK'
{
  "registry": "bootorg/agentskills",
  "ref": "1111111111111111111111111111111111111111",
  "bundles": ["adam"],
  "sources": [
    {
      "registry": "bootorg/cms-platform",
      "ref": "2222222222222222222222222222222222222222",
      "bundles": ["cms-platform"],
      "layout": "skills"
    }
  ],
  "skills": {"adam/finding-unknowns": "deadbeef"}
}
LOCK

    # An existing SessionStart entry, byte-identical in shape to the one both
    # live consumers carry. It must survive registration untouched.
    cat > "$TEST_DIR/existing-settings.json" <<'JSON'
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$CLAUDE_PROJECT_DIR/scripts/setup-hooks.sh\"",
            "timeout": 30
          }
        ]
      }
    ]
  },
  "worktree": {
    "symlinkDirectories": ["vendor", "node_modules", ".bundle"]
  }
}
JSON

    local name bare work
    for name in agentskills repo-adopted repo-hook-no-lock repo-ignored \
                repo-no-lock repo-not-allowed repo-unparseable; do
        bare="$TEST_DIR/bare/bootorg_$name"
        work="$TEST_DIR/work/bootorg-$name"
        mkdir -p "$bare" "$work"
        git init --bare --initial-branch=main "$bare" >/dev/null 2>&1
        git init --initial-branch=main "$work" >/dev/null 2>&1
        cd "$work"
        git config commit.gpgsign false
        git remote add origin "$bare"

        case "$name" in
            agentskills)
                mkdir -p .claude/hooks
                cp "$hook_src" .claude/hooks/skills-bootstrap.sh
                ;;
            repo-adopted)
                cp "$TEST_DIR/federated.lock" skills.lock
                mkdir -p .claude
                cp "$TEST_DIR/existing-settings.json" .claude/settings.json
                # An ignore file that has NOTHING to say about `.claude/`.
                # The blocked probe must replay git's matcher and come back
                # "not ignored" — a check that keyed on "the repo has a
                # .gitignore" would flip this happy-path repo to blocked.
                printf 'node_modules/\n' > .gitignore
                ;;
            repo-hook-no-lock)
                # The hook is already here; the lock never arrived. Nothing in
                # the sync revisits this — it is not a to-do, it is a standing
                # DEGRADED verdict in every session the repo opens.
                mkdir -p .claude/hooks
                cp "$hook_src" .claude/hooks/skills-bootstrap.sh
                ;;
            repo-ignored)
                cp "$TEST_DIR/federated.lock" skills.lock
                printf '.claude/\n' > .gitignore
                ;;
            repo-no-lock)
                echo "# no lock here" > README.md
                ;;
            repo-not-allowed)
                cp "$TEST_DIR/federated.lock" skills.lock
                ;;
            repo-unparseable)
                cp "$TEST_DIR/federated.lock" skills.lock
                mkdir -p .claude
                printf '{ this is not json\n' > .claude/settings.json
                ;;
        esac

        cat > .agents-sync.yml <<'YAML'
sections:
  - python
YAML
        git add -A
        git commit -m "init" >/dev/null 2>&1
        git push origin HEAD:main >/dev/null 2>&1
    done

    # Byte-exact pre-sync copies of every file the sync must NOT change.
    cp "$TEST_DIR/work/bootorg-repo-adopted/skills.lock" "$TEST_DIR/repo-adopted.skills.lock.orig"
    cp "$TEST_DIR/work/bootorg-repo-unparseable/.claude/settings.json" "$TEST_DIR/repo-unparseable.settings.orig"

    # Central repos.yml fixture for this test run (NOT the real repo-root
    # repos.yml — tests must not depend on the real exclusion list or the real
    # allowlist).
    cat > "$TEST_DIR/repos.yml" <<YAML
exclude:
  - repo-excluded
default_sections:
  - rust
skills_bootstrap:
  registry: bootorg/agentskills
  path: .claude/hooks/skills-bootstrap.sh
  ref: 3333333333333333333333333333333333333333
  sha256: $BOOTSTRAP_HOOK_SHA
  repos:
    - repo-adopted
    - repo-hook-no-lock
    - repo-ignored
    - repo-no-lock
    - repo-unparseable
# Every repo the mock \`gh repo list\` can return, across all five mock orgs, so
# the drift report's cron-coverage classification check finds nothing to flag.
# Silence is this block's pass condition, and test_drift_report asserts it; the
# flagging path gets its own fixture in test_drift_report_cron_classification.
#
# FLOW SEQUENCES, not block ones, on purpose: two tests derive variants of this
# file with \`sed '/- <name>/d'\`, and a block list here would have those edits
# silently reach into this key as well.
cron_coverage:
  fleet: [repo-with-sync, repo-no-sync, repo-with-existing, repo-existing-no-marker,
          repo-with-claude-md, repo-up-to-date-no-claude, repo-fix-claude,
          _agent-guidance, repo-owner2-only, repo-protected, repo-protected-fix,
          repo-stale, agentskills, repo-adopted, repo-hook-no-lock, repo-ignored,
          repo-no-lock, repo-not-allowed, repo-unparseable]
  out_of_scope: [repo-excluded]
YAML

    # Same registry and allowlist, but a WRONG digest — used by the
    # digest-mismatch test.
    sed 's/^  sha256: .*/  sha256: 00000000000000000000000000000000000000000000000000000000deadbeef/' \
        "$TEST_DIR/repos.yml" > "$TEST_DIR/repos-baddigest.yml"

    cd "$REPO_ROOT"
}

# write_stub_generator <path> — the stand-in for agentskills'
# generate_skills_lock.py.
#
# WHY A STAND-IN AND NOT THE REAL FILE. The real generator lives in the
# registry, is tested there, and is not on this runner: ci.yml checks out this
# repo and nothing else. Vendoring a copy would be a second implementation to
# keep in step with the first, which is the thing that repo's own docstring
# warns against. What this file reproduces instead is the CONTRACT
# bump-consumer-locks.sh depends on — the flags it passes, the exit codes it
# branches on, and the FAILED:/ERROR: distinction it refuses to conflate —
# faithfully enough that the fixture CONTENT decides each outcome rather than a
# switch. It computes real digests over real directories, so "the digests
# changed" is an observation and not a stub returning a canned answer.
#
# What it therefore does NOT prove: that the real generator agrees with it
# about a digest. Nothing in this repo consumes those bytes; the registry's
# own suite owns that, and this one owns the orchestration around it.
write_stub_generator() {
    cat > "$1" <<'STUBEOF'
#!/usr/bin/env python3
"""Stand-in for agentskills' generate_skills_lock.py (test fixture only).

  --check-current  exit 0 when the bundle content at the ref the lock pins
                   still matches the checkout's working tree; exit 1 with a
                   FAILED: verdict when it has moved; exit 1 with an ERROR:
                   line when it cannot tell (an unresolvable pinned ref).
                   Every source is read, primary first.
  --check-format   exit 0 when every digest STORED in the lock is
                   `sha256:<64 lowercase hex>`; exit 1 with a FAILED: verdict
                   naming the offenders when any is not. Reads the file alone
                   — no checkout, no git — because that is the real flag's
                   calling convention and the bumper leans on it. An empty
                   `skills` map does not pass vacuously either: this flag
                   gates a REPAIR, where "nothing to fix" and "nothing there"
                   are different answers. It exits 1 with an ERROR: line
                   rather than a FAILED: one, as does a missing or non-map
                   `skills` — the same split a missing FILE takes. The split
                   is reproduced faithfully because the bumper BRANCHES on it:
                   only FAILED: means "these digests are malformed", and only
                   that licenses rewriting a consumer's lock.
  --repin          inherit registry / bundles / sources from the lock at -o,
                   re-resolve only `ref` (to HEAD of --repo, or --ref), and
                   rewrite the file. --registry / --bundles / --source are
                   refused alongside it, exactly as the real one refuses them,
                   so a caller that starts passing them is caught here instead
                   of de-federating a real lock in production.

Digests follow the real generator's shape — a per-file manifest of
"<relpath>\\0<sha256>\\n", then sha256 of the concatenation — read from
`git archive` for the pinned side and from the working tree for the current
side.
"""
import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

DEFAULT_LAYOUT = "plugins/{bundle}/skills"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True)


# The real generator labels at the DOCUMENT boundary (`_label_digests`), not
# inside collection, so every comparison between builder outputs keeps working
# on bare hex — which is exactly why --check-current is blind to a lock's
# stored shape and why --check-format had to exist. Reproduced here at the same
# boundary: a stub that wrote bare hex would make every fixture lock malformed,
# and the anti-churn tests would then be asserting the behaviour of a defect.
LOCK_DIGEST_PREFIX = "sha256:"


def label(skills):
    return {name: LOCK_DIGEST_PREFIX + digest for name, digest in skills.items()}


def digest_dir(directory):
    entries = []
    for path in sorted(p for p in Path(directory).rglob("*") if p.is_file()):
        rel = path.relative_to(directory).as_posix()
        entries.append("%s\0%s\n" % (rel, hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256("".join(sorted(entries)).encode("utf-8")).hexdigest()


def collect(root, bundles, layout):
    skills = {}
    for bundle in bundles:
        base = Path(root) / layout.format(bundle=bundle)
        if not base.is_dir():
            continue
        for skill in sorted(p for p in base.iterdir() if p.is_dir()):
            skills["%s/%s" % (bundle, skill.name)] = digest_dir(skill)
    return skills


def at_ref(repo, ref, bundles, layout):
    if git(repo, "cat-file", "-e", "%s^{commit}" % ref).returncode != 0:
        sys.exit("ERROR: cannot resolve ref '%s' in %s" % (ref, repo))
    proc = git(repo, "archive", "--format=tar", ref)
    if proc.returncode != 0:
        sys.exit("ERROR: git archive %s failed in %s" % (ref, repo))
    with tempfile.TemporaryDirectory() as scratch:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
            try:
                archive.extractall(scratch, filter="data")
            except TypeError:  # Python < 3.11.4 has no extraction filters
                archive.extractall(scratch)
        return collect(scratch, bundles, layout)


def plan(lock, repo, overrides):
    sources = [{
        "registry": lock["registry"],
        "ref": lock["ref"],
        "bundles": lock["bundles"],
        "layout": DEFAULT_LAYOUT,
        "path": Path(repo),
    }]
    for source in lock.get("sources") or []:
        key = source["registry"]
        if key not in overrides:
            sys.exit("ERROR: %s: no checkout — pass --source-repo '%s=<path>'" % (key, key))
        sources.append({
            "registry": key,
            "ref": source["ref"],
            "bundles": source["bundles"],
            "layout": source.get("layout", DEFAULT_LAYOUT),
            "path": Path(overrides[key]),
        })
    return sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-current", action="store_true")
    parser.add_argument("--check-format", action="store_true")
    parser.add_argument("--repin", action="store_true")
    parser.add_argument("--repo")
    parser.add_argument("--ref")
    parser.add_argument("--registry")
    parser.add_argument("--bundles")
    parser.add_argument("--source", action="append")
    parser.add_argument("--source-repo", action="append", default=[])
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()

    if args.repin and (args.registry or args.bundles or args.source):
        parser.error("--repin inherits the lock's identity; --registry / --bundles / "
                     "--source would override it")

    overrides = dict(spec.split("=", 1) for spec in args.source_repo)
    output = Path(args.output)
    if not output.is_file():
        sys.exit("ERROR: %s does not exist" % output)
    lock = json.loads(output.read_text(encoding="utf-8"))

    # Answered HERE, above plan(), because reading nothing but the file is this
    # flag's calling convention and not an incidental economy: the bumper runs
    # it per consumer lock and plan() would exit ERROR: for any federated lock
    # whose source checkout this call was not given.
    if args.check_format:
        skills = lock.get("skills")
        # ERROR:, not FAILED:, and that is the whole point of reproducing
        # these two branches at all. `FAILED:` is the bumper's licence to
        # REWRITE a consumer's lock; neither of these is an answer about
        # digest shape, so neither may claim it. The real generator says
        # ERROR: here for exactly that reason — a lock with no skills is
        # "nothing there", which a re-pin does not repair, and reading it as
        # "malformed" is what put an empty map into a nightly re-pin loop with
        # no exit from it.
        if not isinstance(skills, dict):
            print("ERROR: %s has no usable 'skills' map (got %s), so it holds "
                  "no digests whose shape could be wrong"
                  % (output, type(skills).__name__))
            return 1
        if not skills:
            print("ERROR: %s lists no skills at all, so nothing in it has a "
                  "digest to be in the right shape" % output)
            return 1
        # Names only, never the offending VALUE: a bare 64-hex digest beside a
        # keyword-bearing name is precisely what gitleaks' generic-api-key rule
        # fires on, and this report is written for CI logs.
        offenders = [name for name in sorted(skills)
                     if not (isinstance(skills[name], str)
                             and re.fullmatch(r"sha256:[0-9a-f]{64}", skills[name]))]
        if not offenders:
            print("OK: every digest in %s is sha256:<64 hex> (%d skills)."
                  % (output, len(skills)))
            return 0
        print("FAILED: %d of %d digests in %s are not sha256:<64 lowercase hex>."
              % (len(offenders), len(skills), output))
        # The REMEDIATION line, and it carries `--ref` naming this lock's own
        # pin. Reproduced because the bumper quotes this whole verdict verbatim
        # into a PR body and then tells the reviewer it is the command that ran
        # — a claim only checkable if the line is here to check. The real
        # generator charset-guards the value it prints (agentskills #108); what
        # is load-bearing for the bumper is that the ref named is the lock's
        # OWN, so a re-pin that moved the pin would leave the body quoting a
        # command that cannot reproduce the diff beneath it.
        print("  python3 scripts/generate_skills_lock.py --repin --ref %s "
              "--repo <a clone of the registry this lock names> -o <this lock>"
              % lock.get("ref"))
        for name in offenders:
            print("  - %s" % name)
        return 1

    sources = plan(lock, args.repo, overrides)

    if args.check_current:
        differences = []
        for source in sources:
            pinned = at_ref(source["path"], source["ref"], source["bundles"], source["layout"])
            here = collect(source["path"], source["bundles"], source["layout"])
            for name in sorted(set(here) - set(pinned)):
                differences.append("added: '%s' is in the working tree but not at %s"
                                   % (name, source["ref"]))
            for name in sorted(set(pinned) - set(here)):
                differences.append("removed: '%s' is at %s but not in the working tree"
                                   % (name, source["ref"]))
            for name in sorted(set(pinned) & set(here)):
                if pinned[name] != here[name]:
                    differences.append("changed: '%s' differs from its content at %s"
                                       % (name, source["ref"]))
        if not differences:
            print("OK: the working tree still matches %s." % lock["ref"])
            return 0
        print("FAILED: the bundle has moved on since %s, which %s still pins."
              % (lock["ref"], output))
        for line in differences:
            print("  - %s" % line)
        return 1

    if args.repin:
        # The real generator's probe: a clone that IS this registry contains
        # the commit the lock already pins.
        if git(args.repo, "cat-file", "-e", "%s^{commit}" % lock["ref"]).returncode != 0:
            sys.exit("ERROR: %s does not contain %s, the commit %s pins for '%s'"
                     % (args.repo, lock["ref"], output, lock["registry"]))
        if args.ref:
            new_ref = args.ref
        else:
            proc = git(args.repo, "rev-parse", "--verify", "HEAD^{commit}")
            if proc.returncode != 0:
                sys.exit("ERROR: cannot resolve HEAD in %s" % args.repo)
            new_ref = proc.stdout.decode().strip()
        sources[0]["ref"] = new_ref
        skills = {}
        for source in sources:
            skills.update(at_ref(source["path"], source["ref"],
                                 source["bundles"], source["layout"]))
        document = {
            "registry": lock["registry"],
            "ref": new_ref,
            "bundles": lock["bundles"],
        }
        # Inherited verbatim — never rebuilt from flags. This is the whole
        # federation property the fleet test asserts.
        if lock.get("sources"):
            document["sources"] = lock["sources"]
        document["skills"] = label(dict(sorted(skills.items())))
        document["generated_from"] = new_ref
        output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        print("Wrote %s: %d skills at %s." % (output, len(skills), new_ref))
        return 0

    parser.error("nothing to do: pass --check-current, --check-format or --repin")


if __name__ == "__main__":
    sys.exit(main())
STUBEOF
    chmod +x "$1"
}

# ── Set up the skills.lock bump fixtures ───────────────────────────────────
#
# bump-consumer-locks.sh does not compute a lock; it decides WHICH repos need
# one recomputed and calls the registry's generator to do it. So these
# fixtures are built around the two decisions that can go wrong silently:
#
#   * re-pinning a lock whose BUNDLE has not moved — churn, one PR per repo
#     per night, and the fastest way to make the fleet ignore these PRs;
#   * re-pinning a FEDERATED lock in a way that drops its other registry —
#     the trap ADR 0001 named, reachable here at fleet scale.
#
# The registry fixture therefore has three commits and only the middle one
# touches bundle content, so "the ref moved" and "the bundle moved" are
# different facts that different consumers pin. The federated source registry
# has TWO commits for the same reason on its own axis: with one, its pinned
# ref and its HEAD are the same sha, and "its pin did not move" and "its pin
# was re-resolved to HEAD" produce byte-identical fixtures — the single most
# likely de-federation bug, invisible.
#
#   bumporg/agentskills      the registry itself, carrying a stale lock of its
#                            own so the "never bump the registry" assertion is
#                            not vacuous — a regressed carve-out would produce
#                            a real PR here
#   bumporg/repo-current     pinned one commit BEHIND the registry's HEAD, but
#                            at content identical to it → no PR, no push
#   bumporg/repo-diverged    stale primary, but its bump branch already exists
#                            carrying a DIFFERENT lock (a reviewer's own commit
#                            on an open bump PR) → refused, never force-pushed
#   bumporg/repo-error       pins a commit no registry contains → a per-repo
#                            failure, never a re-pin. Sorts THIRD of ten, so
#                            the repos after it prove the run survived it
#   bumporg/repo-fed-current primary content-current, federated source BEHIND
#                            its registry's HEAD → no PR. The gate reads the
#                            primary alone; a combined verdict would re-pin
#                            this repo on every registry commit forever, with
#                            not one digest changing
#   bumporg/repo-federated   stale primary + a cms-platform source → PR, with
#                            the sources array carried through untouched
#   bumporg/repo-inverted    the federation inverted — cms-platform primary,
#                            the bumped registry only a source → skipped, so
#                            no other registry's pin is advanced under this
#                            registry's name
#   bumporg/repo-no-lock     declares no bundles at all → skipped
#   bumporg/repo-other-registry  a lock for some other registry → skipped
#   bumporg/repo-stale       stale primary → PR, ref advanced, digests changed
setup_bump_repos() {
    echo "Setting up skills.lock bump fixtures..."

    # The stand-in generator, written where the real one lives inside its own
    # registry (scripts/generate_skills_lock.py), because that is where
    # bump-consumer-locks.sh looks for it by default.
    mkdir -p "$TEST_DIR/registry/scripts" \
             "$TEST_DIR/registry/plugins/adam/skills/finding-unknowns" \
             "$TEST_DIR/registry/plugins/adam/skills/writing-adrs"
    write_stub_generator "$TEST_DIR/registry/scripts/generate_skills_lock.py"

    echo "v1" > "$TEST_DIR/registry/plugins/adam/skills/finding-unknowns/SKILL.md"
    echo "v1" > "$TEST_DIR/registry/plugins/adam/skills/writing-adrs/SKILL.md"
    git init --initial-branch=main "$TEST_DIR/registry" >/dev/null 2>&1
    cd "$TEST_DIR/registry"
    git config commit.gpgsign false
    git add -A && git commit -m "skills v1" >/dev/null 2>&1
    BUMP_REF_OLD=$(git rev-parse HEAD)
    echo "v2" > plugins/adam/skills/finding-unknowns/SKILL.md
    git add -A && git commit -m "edit a skill" >/dev/null 2>&1
    BUMP_REF_CONTENT=$(git rev-parse HEAD)
    # A commit that moves HEAD without touching a bundle. This is what makes
    # repo-current's "no PR" a real assertion rather than a tautology: its ref
    # IS behind HEAD, and it must still not be re-pinned.
    echo "# registry" > README.md
    git add -A && git commit -m "docs only" >/dev/null 2>&1
    BUMP_REF_HEAD=$(git rev-parse HEAD)

    # The federated source, on its own cadence — never advanced by a bump.
    # It carries its bundle twice: under its own `skills` layout, which is how
    # every federated consumer names it, and under the default
    # plugins/{bundle}/skills, which is the only layout a PRIMARY can have
    # (the generator materialises the primary with DEFAULT_LAYOUT and a lock
    # records no layout for it). repo-inverted pins it as a primary, so
    # without the second copy that fixture could never drift and "no other
    # registry's pin was advanced" would hold for the wrong reason.
    mkdir -p "$TEST_DIR/cms-platform/skills/deploy-site" \
             "$TEST_DIR/cms-platform/plugins/cms-platform/skills/publish-site"
    echo "deploy v1" > "$TEST_DIR/cms-platform/skills/deploy-site/SKILL.md"
    echo "publish v1" > "$TEST_DIR/cms-platform/plugins/cms-platform/skills/publish-site/SKILL.md"
    git init --initial-branch=main "$TEST_DIR/cms-platform" >/dev/null 2>&1
    cd "$TEST_DIR/cms-platform"
    git config commit.gpgsign false
    git add -A && git commit -m "deploy-site" >/dev/null 2>&1
    BUMP_SRC_REF=$(git rev-parse HEAD)
    # Captured BEFORE this second commit, so the pin every federated fixture
    # carries is genuinely behind this registry's HEAD. Without it a re-pin
    # that re-resolved each sources[].ref to HEAD would leave the lock byte
    # for byte where it was, and the assertions that its pin "did not move"
    # could not fail.
    echo "deploy v2" > "$TEST_DIR/cms-platform/skills/deploy-site/SKILL.md"
    echo "publish v2" > "$TEST_DIR/cms-platform/plugins/cms-platform/skills/publish-site/SKILL.md"
    git add -A && git commit -m "deploy v2" >/dev/null 2>&1
    BUMP_SRC_HEAD=$(git rev-parse HEAD)

    BUMP_CHECKOUTS_ARG="bumporg/agentskills=$TEST_DIR/registry bumporg/cms-platform=$TEST_DIR/cms-platform"

    local name bare work
    for name in agentskills repo-current repo-diverged repo-error \
                repo-fed-current repo-federated repo-inverted \
                repo-no-lock repo-other-registry repo-stale; do
        bare="$TEST_DIR/bare/bumporg_$name"
        work="$TEST_DIR/work/bumporg-$name"
        mkdir -p "$bare" "$work"
        git init --bare --initial-branch=main "$bare" >/dev/null 2>&1
        git init --initial-branch=main "$work" >/dev/null 2>&1
        cd "$work"
        git config commit.gpgsign false
        git remote add origin "$bare"
        echo "# $name" > README.md

        case "$name" in
            agentskills|repo-stale|repo-diverged)
                seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_OLD"
                ;;
            repo-current)
                seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_CONTENT"
                ;;
            repo-federated)
                # The sources baseline is written by the FIXTURE, before the
                # generator ever sees this lock. Comparing generator output
                # against generator output is a tautology: a generator that
                # mangles or drops `sources` mangles the baseline identically
                # and the byte-for-byte assertion still passes.
                seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_OLD" \
                    "$BUMP_SRC_REF" fill "$TEST_DIR/repo-federated.sources.expected"
                ;;
            repo-fed-current)
                seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_CONTENT" \
                    "$BUMP_SRC_REF"
                ;;
            repo-inverted)
                seed_inverted_lock skills.lock
                ;;
            repo-error)
                # A 40-hex ref no registry contains: the generator cannot say
                # whether this lock is current, and "cannot say" must never be
                # read as "re-pin it".
                seed_bump_lock skills.lock "bumporg/agentskills" \
                    "9999999999999999999999999999999999999999" "" nofill
                ;;
            repo-other-registry)
                seed_bump_lock skills.lock "bumporg/elsewhere" \
                    "8888888888888888888888888888888888888888" "" nofill
                ;;
            repo-no-lock)
                : # declares no bundles at all
                ;;
        esac

        git add -A
        git commit -m "init" >/dev/null 2>&1
        git push origin HEAD:main >/dev/null 2>&1
    done

    # repo-diverged additionally carries an already-open bump branch whose
    # lock is NOT the one a run would push — the stand-in for a reviewer's own
    # commit on an open bump PR, which is the one thing this repo's AGENTS.md
    # records force-pushing as destroying.
    cd "$TEST_DIR/work/bumporg-repo-diverged"
    git checkout -q -b skills-lock-bump/update
    python3 "$TEST_DIR/registry/scripts/generate_skills_lock.py" --repin \
        --repo "$TEST_DIR/registry" --ref "$BUMP_REF_CONTENT" -o skills.lock >/dev/null
    git add -A
    git commit -m "a reviewer's own edit on the bump branch" >/dev/null 2>&1
    git push origin HEAD:refs/heads/skills-lock-bump/update >/dev/null 2>&1
    git checkout -q main

    # Byte-exact pre-run copies of the two locks no bump may touch.
    cp "$TEST_DIR/work/bumporg-repo-current/skills.lock" "$TEST_DIR/repo-current.lock.orig"
    cp "$TEST_DIR/work/bumporg-repo-stale/skills.lock" "$TEST_DIR/repo-stale.lock.orig"
    cp "$TEST_DIR/work/bumporg-repo-federated/skills.lock" "$TEST_DIR/repo-federated.lock.orig"

    cd "$REPO_ROOT"
}

# seed_bump_lock <path> <registry> <ref> [<federated source ref>] [nofill]
#                [<sources baseline file>]
#
# Writes a lock and then fills in its digests by running the stand-in
# generator's own --repin at that same ref, so a fixture lock is generator
# OUTPUT rather than hand-written JSON — the same reason the bootstrap
# fixtures register through register-bootstrap-hook.sh instead of pasting a
# settings.json. `nofill` skips that step for the two locks the generator
# cannot read (a ref no registry has, a registry with no checkout).
#
# The optional sources baseline is the ONE thing deliberately captured before
# the generator runs: an assertion that a bumped lock's `sources` survived
# byte for byte is worthless if both sides came out of the generator, because
# a generator that mangles the array mangles the baseline the same way.
seed_bump_lock() {
    local path="$1" registry="$2" ref="$3" source_ref="${4:-}" mode="${5:-fill}"
    local sources_baseline="${6:-}"
    python3 -c '
import json, sys
path, registry, ref, source_ref, baseline = sys.argv[1:6]
doc = {"registry": registry, "ref": ref, "bundles": ["adam"]}
if source_ref:
    doc["sources"] = [{
        "registry": "bumporg/cms-platform",
        "ref": source_ref,
        "bundles": ["cms-platform"],
        "layout": "skills",
    }]
doc["skills"] = {}
doc["generated_from"] = ref
with open(path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
if baseline:
    with open(baseline, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(doc.get("sources"), indent=2, ensure_ascii=False) + "\n")
' "$path" "$registry" "$ref" "$source_ref" "$sources_baseline"

    [[ "$mode" == "nofill" ]] && return 0

    local source_args=()
    [[ -n "$source_ref" ]] && source_args=(--source-repo "bumporg/cms-platform=$TEST_DIR/cms-platform")
    python3 "$TEST_DIR/registry/scripts/generate_skills_lock.py" --repin \
        --repo "$TEST_DIR/registry" --ref "$ref" \
        ${source_args[@]+"${source_args[@]}"} -o "$path" >/dev/null
}

# strip_digest_labels <path> — rewrite a generated lock's digests back to bare
# hex, reproducing the eight real consumer locks that were written before the
# generator labelled anything. Deliberately a MUTATION of real generator output
# rather than a hand-typed lock: the digests stay the true ones for the ref the
# lock pins, so --check-current still answers OK at exit 0 on it and the only
# thing wrong with the fixture is the one thing under test. A hand-written lock
# would risk failing the currency question too and prove nothing about which
# gate fired.
#
# Fails loudly on a lock it did not change. A silent no-op here — an empty
# `skills` map, a lock already bare, a schema that moved — would seed a fixture
# identical to its own control, and every assertion downstream would pass while
# testing nothing.
strip_digest_labels() {
    python3 -c '
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    doc = json.load(handle)
skills = doc.get("skills") or {}
if not skills:
    sys.exit("strip_digest_labels: %s lists no skills to strip" % path)
stripped = 0
for name, digest in list(skills.items()):
    if isinstance(digest, str) and digest.startswith("sha256:"):
        skills[name] = digest[len("sha256:"):]
        stripped += 1
if stripped != len(skills):
    sys.exit("strip_digest_labels: %s had %d of %d digests labelled; expected all"
             % (path, stripped, len(skills)))
with open(path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
' "$1"
}

# seed_inverted_lock <path> — the same federation with the roles swapped:
# bumporg/cms-platform is the PRIMARY and bumporg/agentskills appears only
# under `sources`. Generator output like every other fixture lock. Nothing in
# the real fleet has this shape yet, and nothing rejected it either — a bump
# of agentskills would have advanced cms-platform's pin instead, under a title
# naming agentskills and a sha agentskills does not contain.
seed_inverted_lock() {
    python3 -c '
import json, sys
path, primary_ref, source_ref = sys.argv[1:4]
doc = {
    "registry": "bumporg/cms-platform",
    "ref": primary_ref,
    "bundles": ["cms-platform"],
    "sources": [{
        "registry": "bumporg/agentskills",
        "ref": source_ref,
        "bundles": ["adam"],
        "layout": "plugins/{bundle}/skills",
    }],
    "skills": {},
    "generated_from": primary_ref,
}
with open(path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
' "$1" "$BUMP_SRC_REF" "$BUMP_REF_OLD"
    python3 "$TEST_DIR/registry/scripts/generate_skills_lock.py" --repin \
        --repo "$TEST_DIR/cms-platform" --ref "$BUMP_SRC_REF" \
        --source-repo "bumporg/agentskills=$TEST_DIR/registry" -o "$1" >/dev/null
}

# ── Create mock gh CLI ─────────────────────────────────────────────────────

create_mock_gh() {
    local gh_mock="$TEST_DIR/bin/gh"
    mkdir -p "$TEST_DIR/bin"

    cat > "$gh_mock" <<'GHSCRIPT'
#!/usr/bin/env bash
# Mock gh CLI for testing.
# Simulates gh repo list, gh repo clone, gh api, and gh pr.

# Parse all arguments to extract common flags
parse_jq_filter() {
    local args=("$@")
    for ((i=0; i<${#args[@]}; i++)); do
        if [[ "${args[$i]}" == "--jq" ]]; then
            echo "${args[$((i+1))]}"
            return
        fi
    done
}

# Generic flag-value extractor (e.g. parse_flag_value --body "$@"), used to
# capture gh pr create's --body for test verification.
parse_flag_value() {
    local flag="$1"; shift
    local args=("$@")
    for ((i=0; i<${#args[@]}; i++)); do
        if [[ "${args[$i]}" == "$flag" ]]; then
            echo "${args[$((i+1))]}"
            return
        fi
    done
}

case "$1" in
    repo)
        case "$2" in
            list)
                shift 2  # remove "repo list"
                org="$1"
                # Raw JSON data, keyed by requested org (gh repo list <org> ...
                # puts the org as the first positional arg right after "repo list")
                case "$org" in
                    testorg)
                        json='[
                          {"nameWithOwner":"testorg/repo-with-sync"},
                          {"nameWithOwner":"testorg/repo-no-sync"},
                          {"nameWithOwner":"testorg/repo-with-existing"},
                          {"nameWithOwner":"testorg/repo-existing-no-marker"},
                          {"nameWithOwner":"testorg/repo-with-claude-md"},
                          {"nameWithOwner":"testorg/repo-excluded"},
                          {"nameWithOwner":"testorg/repo-up-to-date-no-claude"},
                          {"nameWithOwner":"testorg/repo-fix-claude"},
                          {"nameWithOwner":"testorg/_agent-guidance"}
                        ]'
                        ;;
                    testorg2)
                        json='[
                          {"nameWithOwner":"testorg2/repo-owner2-only"},
                          {"nameWithOwner":"testorg2/_agent-guidance"}
                        ]'
                        ;;
                    protorg)
                        json='[
                          {"nameWithOwner":"protorg/repo-protected"},
                          {"nameWithOwner":"protorg/repo-protected-fix"}
                        ]'
                        ;;
                    stalorg)
                        json='[
                          {"nameWithOwner":"stalorg/repo-stale"}
                        ]'
                        ;;
                    bootorg)
                        # bootorg/agentskills is ABSENT by default: it is the
                        # registry the hook is fetched from, not a sync target.
                        # MOCK_INCLUDE_REGISTRY=1 puts it back, for the one
                        # sub-test that asks what the report says when the
                        # registry IS scanned as a target.
                        registry_row=""
                        [[ -n "${MOCK_INCLUDE_REGISTRY:-}" ]] && \
                            registry_row='{"nameWithOwner":"bootorg/agentskills"},'
                        json="[
                          $registry_row
                          {\"nameWithOwner\":\"bootorg/repo-adopted\"},
                          {\"nameWithOwner\":\"bootorg/repo-hook-no-lock\"},
                          {\"nameWithOwner\":\"bootorg/repo-ignored\"},
                          {\"nameWithOwner\":\"bootorg/repo-no-lock\"},
                          {\"nameWithOwner\":\"bootorg/repo-not-allowed\"},
                          {\"nameWithOwner\":\"bootorg/repo-unparseable\"}
                        ]"
                        ;;
                    bumporg)
                        # The lock-bump fixtures, enumerated off disk rather
                        # than listed here: several of them are stood up and
                        # torn down by a single test (a rejected push, a
                        # vanished bundle) in their own MOCK_BARE_DIR, and a
                        # hard-coded roster would have to be edited in two
                        # places to match. bumporg/agentskills IS the registry
                        # and is deliberately among them: the carve-out that
                        # skips it has to be observable, and a registry absent
                        # from discovery would make that assertion vacuous.
                        rows=""
                        for bare_dir in "${MOCK_BARE_DIR}"/bumporg_*; do
                            [[ -d "$bare_dir" ]] || continue
                            name=$(basename "$bare_dir")
                            rows="${rows}${rows:+,}{\"nameWithOwner\":\"bumporg/${name#bumporg_}\"}"
                        done
                        json="[$rows]"
                        ;;
                    failorg)
                        # An owner the CLI cannot authenticate against — the
                        # shape a missing App installation takes when the
                        # script has unset GH_TOKEN for it. Real gh writes to
                        # stderr and exits non-zero.
                        echo "error connecting to api.github.com: authentication required" >&2
                        exit 4
                        ;;
                    *)
                        json='[]'
                        ;;
                esac
                # Find --jq filter in remaining args
                jq_filter=$(parse_jq_filter "$@")
                if [[ -n "$jq_filter" ]]; then
                    echo "$json" | jq -r "$jq_filter"
                else
                    echo "$json"
                fi
                ;;
            clone)
                # Clone from our bare repos
                repo_slug=$(echo "$3" | tr '/' '_')
                dest="${4}"
                shift 4
                # Strip -- separator if present
                [[ "${1:-}" == "--" ]] && shift
                bare_path="${MOCK_BARE_DIR}/${repo_slug}"
                if [[ -d "$bare_path" ]]; then
                    git clone "$bare_path" "$dest" "$@" 2>/dev/null
                    git -C "$dest" config commit.gpgsign false 2>/dev/null || true
                else
                    echo "ERROR: mock repo $bare_path not found" >&2
                    exit 1
                fi
                ;;
            view)
                # sync.sh resolves the default branch via
                # `gh repo view <repo> --json defaultBranchRef --jq ...`;
                # the mock repos all use main.
                shift 2  # remove "repo view"
                json='{"defaultBranchRef":{"name":"main"}}'
                jq_filter=$(parse_jq_filter "$@")
                if [[ -n "$jq_filter" ]]; then
                    echo "$json" | jq -r "$jq_filter"
                else
                    echo "$json"
                fi
                ;;
        esac
        ;;
    api)
        shift  # remove 'api'
        api_path="$1"
        shift
        jq_filter=$(parse_jq_filter "$@")

        # repos/{owner}/{repo}/contents/{path}
        if [[ "$api_path" =~ repos/([^/]+)/([^/]+)/contents/(.+) ]]; then
            owner="${BASH_REMATCH[1]}"
            repo="${BASH_REMATCH[2]}"
            file_path="${BASH_REMATCH[3]}"
            # Strip a ?ref=<sha> query. The mock always serves `main`: pinning
            # is exercised by the digest check in sync.sh, not by the mock
            # resolving refs, and a mock that pretended to would be testing
            # itself.
            file_path="${file_path%%\?*}"
            repo_slug="${owner}_${repo}"
            bare_path="${MOCK_BARE_DIR}/${repo_slug}"

            if [[ -d "$bare_path" ]]; then
                content=$(git -C "$bare_path" show "main:$file_path" 2>/dev/null || true)
                if [[ -n "$content" ]]; then
                    encoded=$(echo "$content" | base64 -w 0)
                    json="{\"content\": \"$encoded\"}"
                    if [[ -n "$jq_filter" ]]; then
                        echo "$json" | jq -r "$jq_filter"
                    else
                        echo "$json"
                    fi
                else
                    # Real gh api prints the raw error JSON body to stdout on
                    # HTTP errors (the --jq filter is NOT applied) — mimic that
                    # so callers that mishandle failure output get caught.
                    echo '{"message":"Not Found","documentation_url":"https://docs.github.com/rest/repos/contents#get-repository-content","status":"404"}'
                    exit 1
                fi
            else
                echo '{"message":"Not Found","documentation_url":"https://docs.github.com/rest/repos/contents#get-repository-content","status":"404"}'
                exit 1
            fi
        fi
        ;;
    pr)
        case "$2" in
            list)
                shift 2
                jq_filter=$(parse_jq_filter "$@")
                repo_arg=$(parse_flag_value --repo "$@")
                if [[ -n "$repo_arg" ]]; then
                    # A --repo query is the bumper's SWEEP asking what is open
                    # on a repo it is not standing in, and it is answered only
                    # from MOCK_PR_DIR: a directory of <owner>_<repo>.json
                    # files, each an array of PR objects. Unset, or no file for
                    # this repo, means no open PRs — which is what every test
                    # that does not exercise the sweep wants.
                    #
                    # DELIBERATELY NOT FILTERED BY --head, though the caller
                    # passes it and real gh honours it. The script re-checks
                    # the head branch itself, and a mock that pre-filtered on
                    # the very field that guard reads would make the guard
                    # untestable — it exists precisely because a listing filter
                    # is a query, not a guarantee.
                    json='[]'
                    pr_file="${MOCK_PR_DIR:-/nonexistent}/${repo_arg//\//_}.json"
                    [[ -f "$pr_file" ]] && json=$(cat "$pr_file")
                else
                    # No --repo: the propose pass asking about the clone it is
                    # standing in. Unchanged — an "open" PR #42 for repos whose
                    # clone-dir basename (owner_repo) is listed in
                    # MOCK_OPEN_PR_REPOS; every other repo has none.
                    json='[]'
                    current_repo=$(basename "$PWD")
                    for r in ${MOCK_OPEN_PR_REPOS:-}; do
                        if [[ "$r" == "$current_repo" ]]; then
                            json='[{"number":42}]'
                            break
                        fi
                    done
                fi
                if [[ -n "$jq_filter" ]]; then
                    echo "$json" | jq -r "$jq_filter"
                else
                    echo "$json"
                fi
                ;;
            view)
                # gh pr view <number> --repo <owner/repo> --json <fields>
                shift 2
                pr_number="$1"; shift
                repo_arg=$(parse_flag_value --repo "$@")
                jq_filter=$(parse_jq_filter "$@")
                repo_slug="${repo_arg//\//_}"
                pr_file="${MOCK_PR_DIR:-/nonexistent}/${repo_slug}.json"
                pr_obj=""
                [[ -f "$pr_file" ]] && pr_obj=$(jq -c --argjson n "$pr_number" \
                    '.[] | select(.number == $n)' "$pr_file")
                if [[ -z "$pr_obj" ]]; then
                    echo "could not resolve to a PullRequest with the number of ${pr_number}" >&2
                    exit 1
                fi
                # `files` is COMPUTED from the branch rather than declared in
                # the fixture, so a test that says "someone pushed a second
                # file onto the bump branch" has to actually put it there.
                head_ref=$(jq -r '.headRefName' <<< "$pr_obj")
                files_json=$(git -C "${MOCK_BARE_DIR:-/nonexistent}/${repo_slug}" \
                    diff --name-only "main...refs/heads/${head_ref}" 2>/dev/null \
                    | jq -R . | jq -s 'map({path: .})')
                # Same principle as `files`: COMPUTED from the branch, not
                # declared, so the oid the script pins its merge to is the one
                # the branch actually has. MOCK_PR_HEAD_MOVES names repos where
                # it reports a well-formed oid that is NOT the tip — the exact
                # shape of "someone pushed between the check and the merge".
                head_oid=$(git -C "${MOCK_BARE_DIR:-/nonexistent}/${repo_slug}" \
                    rev-parse --verify -q "refs/heads/${head_ref}" 2>/dev/null || true)
                if [[ " ${MOCK_PR_HEAD_MOVES:-} " == *" $repo_slug "* ]]; then
                    head_oid="0123456789abcdef0123456789abcdef01234567"
                fi
                # ...and one where it is not a sha at all, which is the only
                # other thing the script can be handed and the case where it
                # must refuse rather than merge unpinned.
                if [[ " ${MOCK_PR_HEAD_GARBLED:-} " == *" $repo_slug "* ]]; then
                    head_oid="not-a-sha"
                fi
                json=$(jq --argjson files "${files_json:-[]}" --arg oid "$head_oid" \
                    '.files = $files | .headRefOid = $oid' <<< "$pr_obj")
                if [[ -n "$jq_filter" ]]; then
                    echo "$json" | jq -r "$jq_filter"
                else
                    echo "$json"
                fi
                ;;
            create)
                # Log PR creation to a file for test verification
                echo "pr-created" >> "${MOCK_PR_LOG:-/dev/null}"
                if [[ -n "${MOCK_PR_BODY_DIR:-}" ]]; then
                    mkdir -p "$MOCK_PR_BODY_DIR"
                    pr_body=$(parse_flag_value --body "$@")
                    printf '%s\n' "$pr_body" > "$MOCK_PR_BODY_DIR/$(basename "$PWD").body"
                    # The title too: a PR list shows it and nothing else, so a
                    # title that misdescribes the diff is read by more people
                    # than the body is. It went uncaptured while the body was
                    # asserted line by line.
                    pr_title=$(parse_flag_value --title "$@")
                    printf '%s\n' "$pr_title" > "$MOCK_PR_BODY_DIR/$(basename "$PWD").title"
                fi
                echo "https://github.com/mock/pr/1"
                ;;
            close)
                # gh pr close <number> --comment ... — log the closed number.
                echo "pr-closed $3" >> "${MOCK_PR_LOG:-/dev/null}"
                ;;
            merge)
                # gh pr merge <number|url> [--auto] --merge|--squash — every
                # call is logged with its arguments, so a test can assert WHICH
                # method was asked for.
                shift 2
                # The capability probe, answered BEFORE the log line: it is not
                # a merge, and letting it reach the log would make "gh pr merge
                # was never called" untrue in every run. MOCK_GH_NO_MATCH_FLAG
                # plays an older gh that lacks the flag entirely.
                for a in "$@"; do
                    if [[ "$a" == "--help" ]]; then
                        echo "Merge a pull request on GitHub."
                        echo "  --merge      Merge the commits with the base branch"
                        [[ -z "${MOCK_GH_NO_MATCH_FLAG:-}" ]] && \
                            echo "  --match-head-commit string   Commit SHA that the pull request head must match to allow merge"
                        exit 0
                    fi
                done
                echo "pr-merged $*" >> "${MOCK_PR_LOG:-/dev/null}"
                auto=""
                for a in "$@"; do [[ "$a" == "--auto" ]] && auto=1; done
                if [[ -n "$auto" ]]; then
                    # --auto ARMS native auto-merge; it never merges anything
                    # itself, which is why nothing below runs for it.
                    # MOCK_AUTO_MERGE_FAILS reproduces what this fleet actually
                    # does: with no required checks there is nothing to hold the
                    # merge for, and GitHub refuses to arm at all.
                    if [[ -n "${MOCK_AUTO_MERGE_FAILS:-}" ]]; then
                        echo "failed to enable auto-merge: Pull request is in clean status" >&2
                        exit 1
                    fi
                    exit 0
                fi
                pr_number="$1"
                repo_arg=$(parse_flag_value --repo "$@")
                repo_slug="${repo_arg//\//_}"
                if [[ " ${MOCK_PR_MERGE_FAIL:-} " == *" $repo_slug "* ]]; then
                    echo "failed to merge pull request: Base branch was modified" >&2
                    exit 1
                fi
                # What --match-head-commit buys, enforced the way GitHub
                # enforces it: the merge is refused outright when the head is
                # not the commit the caller pinned.
                match_sha=$(parse_flag_value --match-head-commit "$@")
                if [[ -n "$match_sha" ]]; then
                    want_ref=$(jq -r --argjson n "$1" \
                        '.[] | select(.number == $n) | .headRefName' \
                        "${MOCK_PR_DIR:-/nonexistent}/${repo_slug}.json" 2>/dev/null || true)
                    have=$(git -C "${MOCK_BARE_DIR:-/nonexistent}/${repo_slug}" \
                        rev-parse --verify -q "refs/heads/${want_ref}" 2>/dev/null || true)
                    if [[ "$match_sha" != "$have" ]]; then
                        echo "failed to merge pull request: Head branch was modified. Review and try the merge again." >&2
                        exit 1
                    fi
                fi
                # The state change a real merge makes, in the two places a test
                # can see it: the PR stops being open, and the default branch
                # carries the branch tip. `update-ref` rather than a real merge
                # commit — a bare repo has no worktree to merge in, and what is
                # under test is which method the script ASKED for, not GitHub's
                # merge topology.
                pr_file="${MOCK_PR_DIR:-/nonexistent}/${repo_slug}.json"
                if [[ -f "$pr_file" ]]; then
                    head_ref=$(jq -r --argjson n "$pr_number" \
                        '.[] | select(.number == $n) | .headRefName' "$pr_file")
                    tip=$(git -C "${MOCK_BARE_DIR:-/nonexistent}/${repo_slug}" \
                        rev-parse --verify -q "refs/heads/${head_ref}" 2>/dev/null || true)
                    if [[ -n "$tip" ]]; then
                        git -C "${MOCK_BARE_DIR}/${repo_slug}" update-ref refs/heads/main "$tip"
                    fi
                    jq --argjson n "$pr_number" 'map(select(.number != $n))' "$pr_file" \
                        > "${pr_file}.tmp" && mv "${pr_file}.tmp" "$pr_file"
                fi
                exit 0
                ;;
        esac
        ;;
esac
GHSCRIPT

    chmod +x "$gh_mock"
}

# ── Test 1: build-agents-md.sh ────────────────────────────────────────────

test_build_script() {
    echo ""
    echo "=== Test: build-agents-md.sh ==="

    local output
    output=$("$REPO_ROOT/scripts/build-agents-md.sh" python docker)

    echo "$output" > "$TEST_DIR/build-output.md"

    assert_contains "$TEST_DIR/build-output.md" "BEGIN MANAGED SECTION" "has managed section start marker"
    assert_contains "$TEST_DIR/build-output.md" "END MANAGED SECTION" "has managed section end marker"
    assert_contains "$TEST_DIR/build-output.md" "Sections: python docker" "lists sections in header"
    # Derive the sentinel from base.md's first heading rather than hardcoding
    # one. This assertion only means "the base content got included"; pinning
    # it to a specific heading made it fail whenever base.md was reorganised,
    # which is a rename, not a regression.
    base_heading=$(grep -m1 '^## ' "$REPO_ROOT/agents-md/base.md")
    # An empty needle would make both assertions below vacuous (grep -F ""
    # matches anything), so a headingless base.md must fail loudly instead.
    if [ -z "$base_heading" ]; then
        fail "agents-md/base.md has no '## ' heading to use as a base-content sentinel"
    fi
    assert_contains "$TEST_DIR/build-output.md" "$base_heading" "includes base content"
    assert_contains "$TEST_DIR/build-output.md" "## Python" "includes python section"
    assert_contains "$TEST_DIR/build-output.md" "## Docker" "includes docker section"
    assert_not_contains "$TEST_DIR/build-output.md" "## Go" "does not include unrequested section"

    # Test with no sections
    output=$("$REPO_ROOT/scripts/build-agents-md.sh")
    echo "$output" > "$TEST_DIR/build-no-sections.md"
    assert_contains "$TEST_DIR/build-no-sections.md" "Sections: none" "reports none when no sections"
    assert_contains "$TEST_DIR/build-no-sections.md" "$base_heading" "still includes base"

    # Test with unknown section
    output=$("$REPO_ROOT/scripts/build-agents-md.sh" python bogus)
    echo "$output" > "$TEST_DIR/build-unknown.md"
    assert_contains "$TEST_DIR/build-unknown.md" "WARNING: unknown section 'bogus'" "warns on unknown section"
    assert_contains "$TEST_DIR/build-unknown.md" "## Python" "still includes valid section"
}

# ── Test 1a: bridge-status.sh ─────────────────────────────────────────────

test_bridge_status() {
    echo ""
    echo "=== Test: bridge-status.sh ==="

    local bridge_script="$REPO_ROOT/scripts/bridge-status.sh"
    local bs_dir="$TEST_DIR/bridge-status"
    mkdir -p "$bs_dir"
    local result

    # Standard two-line bridge
    cat > "$bs_dir/standard.md" <<'MD'
<!-- Managed by _agent-guidance: bridges Claude Code (which reads CLAUDE.md) to AGENTS.md. -->
@AGENTS.md
MD
    result=$("$bridge_script" "$bs_dir/standard.md")
    [[ "$result" == "bridge-ok" ]] && pass "standard two-line bridge -> bridge-ok" || fail "standard two-line bridge -> bridge-ok (got '$result')"

    # Markdown-link pointer (the adamdaniel.ai#2545 failure shape)
    cat > "$bs_dir/pointer.md" <<'MD'
See [AGENTS.md](./AGENTS.md) for the agent guidance.
MD
    result=$("$bridge_script" "$bs_dir/pointer.md")
    [[ "$result" == "no-import" ]] && pass "markdown-link pointer -> no-import" || fail "markdown-link pointer -> no-import (got '$result')"

    # Fenced example only
    cat > "$bs_dir/fenced-only.md" <<'MD'
Example:

```
@AGENTS.md
```
MD
    result=$("$bridge_script" "$bs_dir/fenced-only.md")
    [[ "$result" == "no-import" ]] && pass "fenced example only -> no-import" || fail "fenced example only -> no-import (got '$result')"

    # Fenced example AND a real line-start import after it
    cat > "$bs_dir/fenced-plus-real.md" <<'MD'
Example:

```
@AGENTS.md
```

@AGENTS.md
MD
    result=$("$bridge_script" "$bs_dir/fenced-plus-real.md")
    [[ "$result" == "bridge-ok" ]] && pass "fenced example plus real import after it -> bridge-ok" || fail "fenced example plus real import after it -> bridge-ok (got '$result')"

    # @AGENTS.md with trailing whitespace
    printf '@AGENTS.md   \n' > "$bs_dir/trailing-ws.md"
    result=$("$bridge_script" "$bs_dir/trailing-ws.md")
    [[ "$result" == "bridge-ok" ]] && pass "@AGENTS.md with trailing whitespace -> bridge-ok" || fail "@AGENTS.md with trailing whitespace -> bridge-ok (got '$result')"

    # Nonexistent path
    result=$("$bridge_script" "$bs_dir/does-not-exist.md")
    [[ "$result" == "missing" ]] && pass "nonexistent path -> missing" || fail "nonexistent path -> missing (got '$result')"

    # Empty file
    : > "$bs_dir/empty.md"
    result=$("$bridge_script" "$bs_dir/empty.md")
    [[ "$result" == "missing" ]] && pass "empty file -> missing" || fail "empty file -> missing (got '$result')"

    # Stdin mode
    result=$(printf '@AGENTS.md\n' | "$bridge_script" -)
    [[ "$result" == "bridge-ok" ]] && pass "stdin mode: bridge-ok" || fail "stdin mode: bridge-ok (got '$result')"

    result=$(printf 'no import here\n' | "$bridge_script" -)
    [[ "$result" == "no-import" ]] && pass "stdin mode: no-import" || fail "stdin mode: no-import (got '$result')"

    result=$(printf '' | "$bridge_script" -)
    [[ "$result" == "missing" ]] && pass "stdin mode: empty stdin -> missing" || fail "stdin mode: empty stdin -> missing (got '$result')"
}

# ── Test 2: sync.sh --dry-run ─────────────────────────────────────────────

test_sync_dry_run() {
    echo ""
    echo "=== Test: sync.sh --dry-run ==="

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=testorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" --dry-run 2>&1
    ) || true

    echo "$output" > "$TEST_DIR/sync-output.txt"

    assert_contains "$TEST_DIR/sync-output.txt" "Scanning repos for: testorg" "scans correct org"
    assert_contains "$TEST_DIR/sync-output.txt" "repo-with-sync" "finds repo-with-sync"
    assert_contains "$TEST_DIR/sync-output.txt" "repo-no-sync" "finds repo-no-sync"
    assert_contains "$TEST_DIR/sync-output.txt" "repo-with-existing" "finds repo-with-existing"
    assert_not_contains "$TEST_DIR/sync-output.txt" "=== testorg/_agent-guidance ===" "excludes self repo"
    assert_contains "$TEST_DIR/sync-output.txt" "[DRY RUN]" "respects dry-run flag"
    assert_not_contains "$TEST_DIR/sync-output.txt" "=== testorg/repo-excluded ===" "excludes repo listed in repos.yml"
    assert_contains "$TEST_DIR/sync-output.txt" "excluded by repos.yml" "logs exclusion reason"
}

# ── Test 3: sync.sh full run ──────────────────────────────────────────────

test_sync_full() {
    echo ""
    echo "=== Test: sync.sh (full run) ==="

    local pr_log="$TEST_DIR/pr-creations.log"
    rm -f "$pr_log"

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=testorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        MOCK_PR_LOG="$pr_log" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" 2>&1
    ) || true

    echo "$output" > "$TEST_DIR/sync-full-output.txt"

    # Check repo-with-sync got python + docker sections
    assert_contains "$TEST_DIR/sync-full-output.txt" "Sections: python docker" "repo-with-sync gets python docker"

    # Check repo-with-existing got go sections
    assert_contains "$TEST_DIR/sync-full-output.txt" "Sections: go" "repo-with-existing gets go"

    # Check repo-no-sync falls back to default_sections (rust) from repos.yml
    assert_contains "$TEST_DIR/sync-full-output.txt" "Sections: rust" "repo-no-sync gets default_sections (rust)"

    # Verify repo-with-existing preserved repo-specific content
    local existing_bare="$TEST_DIR/bare/testorg_repo-with-existing"
    local verify_dir="$TEST_DIR/verify-existing"
    git clone "$existing_bare" "$verify_dir" 2>/dev/null || {
        fail "repo-with-existing: sync branch not created"
        return
    }

    assert_contains "$verify_dir/AGENTS.md" "## Repo-specific additions" "repo-with-existing: marker header present"
    assert_contains "$verify_dir/AGENTS.md" "Keep this custom content!" "repo-with-existing: repo-specific content preserved"
    assert_contains "$verify_dir/AGENTS.md" "Do not delete me." "repo-with-existing: multi-line repo content preserved"
    assert_contains "$verify_dir/AGENTS.md" "## Go" "repo-with-existing: go section injected"
    assert_not_contains "$verify_dir/AGENTS.md" "old managed stuff" "repo-with-existing: old managed content replaced"

    # Verify repo-with-sync has correct AGENTS.md
    local sync_bare="$TEST_DIR/bare/testorg_repo-with-sync"
    local verify_sync="$TEST_DIR/verify-sync"
    git clone "$sync_bare" "$verify_sync" 2>/dev/null || {
        fail "repo-with-sync: sync branch not created"
        return
    }

    assert_contains "$verify_sync/AGENTS.md" "## Python" "repo-with-sync: python section present"
    assert_contains "$verify_sync/AGENTS.md" "## Docker" "repo-with-sync: docker section present"
    assert_contains "$verify_sync/AGENTS.md" "## Repo-specific additions" "repo-with-sync: marker header added"
    assert_contains "$verify_sync/CLAUDE.md" "@AGENTS.md" "repo-with-sync: CLAUDE.md bridge created"
    assert_contains "$verify_sync/CLAUDE.md" "Managed by _agent-guidance" "repo-with-sync: CLAUDE.md bridge comment present"

    # Verify repo-no-sync got the default_sections (rust) content
    local nosync_bare="$TEST_DIR/bare/testorg_repo-no-sync"
    local verify_nosync="$TEST_DIR/verify-no-sync"
    git clone "$nosync_bare" "$verify_nosync" 2>/dev/null || {
        fail "repo-no-sync: sync branch not created"
        return
    }
    assert_contains "$verify_nosync/AGENTS.md" "## Rust" "repo-no-sync: default_sections rust section present"

    # Verify repo-existing-no-marker preserved existing content under the marker
    local nomarker_bare="$TEST_DIR/bare/testorg_repo-existing-no-marker"
    local verify_nomarker="$TEST_DIR/verify-nomarker"
    git clone "$nomarker_bare" "$verify_nomarker" 2>/dev/null || {
        fail "repo-existing-no-marker: sync branch not created"
        return
    }

    assert_contains "$verify_nomarker/AGENTS.md" "## Repo-specific additions" "repo-existing-no-marker: marker header added"
    assert_contains "$verify_nomarker/AGENTS.md" "# Our Custom Agent Guide" "repo-existing-no-marker: original heading preserved"
    assert_contains "$verify_nomarker/AGENTS.md" "Always run linting before commits" "repo-existing-no-marker: original content preserved"
    assert_contains "$verify_nomarker/AGENTS.md" "Use conventional commit messages" "repo-existing-no-marker: all original lines preserved"
    assert_contains "$verify_nomarker/AGENTS.md" "## Python" "repo-existing-no-marker: managed python section present"
    assert_contains "$verify_nomarker/AGENTS.md" "BEGIN MANAGED SECTION" "repo-existing-no-marker: managed section marker present"

    # Verify content ordering for no-marker repo: managed content BEFORE marker,
    # existing (preserved) content AFTER — this is the parse invariant: content
    # above "$MARKER" is managed/overwritten, content at-and-below it survives.
    # The marker grep must be anchored: line 1 of the built output (the
    # BEGIN MANAGED SECTION comment) contains the marker TEXT, so an
    # unanchored grep locates line 1 instead of the real marker line —
    # the production parse in sync.sh is anchored (`sed -n "/^MARKER/..."`).
    local marker_line managed_line existing_line
    marker_line=$(grep -n "^## Repo-specific additions" "$verify_nomarker/AGENTS.md" | head -1 | cut -d: -f1)
    existing_line=$(grep -n "# Our Custom Agent Guide" "$verify_nomarker/AGENTS.md" | head -1 | cut -d: -f1)
    managed_line=$(grep -n "BEGIN MANAGED SECTION" "$verify_nomarker/AGENTS.md" | head -1 | cut -d: -f1)
    if [[ -n "$managed_line" && -n "$marker_line" && "$managed_line" -lt "$marker_line" ]]; then
        pass "repo-existing-no-marker: managed content appears before marker"
    else
        fail "repo-existing-no-marker: managed content appears before marker — managed at line $managed_line, marker at line $marker_line"
    fi
    if [[ -n "$existing_line" && -n "$marker_line" && "$existing_line" -gt "$marker_line" ]]; then
        pass "repo-existing-no-marker: existing content appears after marker"
    else
        fail "repo-existing-no-marker: existing content appears after marker — existing at line $existing_line, marker at line $marker_line"
    fi

    # Verify repo-with-claude-md: existing CLAUDE.md left untouched, WARN emitted
    local claudemd_bare="$TEST_DIR/bare/testorg_repo-with-claude-md"
    local verify_claudemd="$TEST_DIR/verify-claude-md"
    git clone "$claudemd_bare" "$verify_claudemd" 2>/dev/null || {
        fail "repo-with-claude-md: sync branch not created"
        return
    }
    assert_contains "$verify_claudemd/CLAUDE.md" "Some pre-existing instructions" "repo-with-claude-md: existing CLAUDE.md content unchanged"
    # Byte-identical check instead of assert_not_contains "@AGENTS.md": the
    # fenced example in the fixture now legitimately contains that substring,
    # so only an exact comparison against the pristine pre-sync copy proves
    # the file was never touched.
    if cmp -s "$verify_claudemd/CLAUDE.md" "$TEST_DIR/repo-with-claude-md.CLAUDE.md.orig"; then
        pass "repo-with-claude-md: existing CLAUDE.md byte-identical — never modified without opt-in"
    else
        fail "repo-with-claude-md: existing CLAUDE.md byte-identical — never modified without opt-in"
    fi
    assert_contains "$TEST_DIR/sync-full-output.txt" "WARN: CLAUDE.md exists but does not import @AGENTS.md" "repo-with-claude-md: WARN emitted for non-bridging CLAUDE.md"

    # Verify repo-up-to-date-no-claude: AGENTS.md untouched, only CLAUDE.md added
    local uptodate_bare="$TEST_DIR/bare/testorg_repo-up-to-date-no-claude"
    local verify_uptodate="$TEST_DIR/verify-up-to-date-no-claude"
    git clone "$uptodate_bare" "$verify_uptodate" 2>/dev/null || {
        fail "repo-up-to-date-no-claude: sync branch not created"
        return
    }
    local expected_managed_repo7 expected_marker_block_repo7 expected_agents_repo7
    expected_managed_repo7=$("$REPO_ROOT/scripts/build-agents-md.sh" python)
    expected_marker_block_repo7="$(printf '%s\n\n%s\n' \
        "## Repo-specific additions" \
        "<!-- Add your repo-specific agent guidance below this line -->")"
    expected_agents_repo7="$(printf '%s\n%s\n' "$expected_managed_repo7" "$expected_marker_block_repo7")"
    if diff -q <(echo "$expected_agents_repo7") "$verify_uptodate/AGENTS.md" &>/dev/null; then
        pass "repo-up-to-date-no-claude: AGENTS.md byte-identical to already up-to-date fixture"
    else
        fail "repo-up-to-date-no-claude: AGENTS.md byte-identical to already up-to-date fixture"
    fi
    assert_contains "$verify_uptodate/CLAUDE.md" "@AGENTS.md" "repo-up-to-date-no-claude: CLAUDE.md bridge added"

    # Verify repo-fix-claude: fix_claude_md: true opt-in rewrites the
    # pointer-only CLAUDE.md to the standard bridge; AGENTS.md (already
    # up to date) is untouched.
    local fixclaude_bare="$TEST_DIR/bare/testorg_repo-fix-claude"
    local verify_fixclaude="$TEST_DIR/verify-fix-claude"
    git clone "$fixclaude_bare" "$verify_fixclaude" 2>/dev/null || {
        fail "repo-fix-claude: sync branch not created"
        return
    }

    if grep -q '^@AGENTS.md' "$verify_fixclaude/CLAUDE.md"; then
        pass "repo-fix-claude: CLAUDE.md rewritten with line-start @AGENTS.md import"
    else
        fail "repo-fix-claude: CLAUDE.md rewritten with line-start @AGENTS.md import"
    fi
    assert_contains "$verify_fixclaude/CLAUDE.md" "Managed by _agent-guidance" "repo-fix-claude: CLAUDE.md rewritten with standard bridge comment"
    assert_not_contains "$verify_fixclaude/CLAUDE.md" "See [AGENTS.md]" "repo-fix-claude: old pointer-only content replaced"

    local expected_managed_repo9 expected_marker_block_repo9 expected_agents_repo9
    expected_managed_repo9=$("$REPO_ROOT/scripts/build-agents-md.sh" python)
    expected_marker_block_repo9="$(printf '%s\n\n%s\n' \
        "## Repo-specific additions" \
        "<!-- Add your repo-specific agent guidance below this line -->")"
    expected_agents_repo9="$(printf '%s\n%s\n' "$expected_managed_repo9" "$expected_marker_block_repo9")"
    if diff -q <(echo "$expected_agents_repo9") "$verify_fixclaude/AGENTS.md" &>/dev/null; then
        pass "repo-fix-claude: AGENTS.md byte-identical to already up-to-date fixture"
    else
        fail "repo-fix-claude: AGENTS.md byte-identical to already up-to-date fixture"
    fi

    assert_contains "$TEST_DIR/sync-full-output.txt" "Rewriting CLAUDE.md" "repo-fix-claude: sync log reports the rewrite"

    # Verify repo-excluded never got processed
    assert_not_contains "$TEST_DIR/sync-full-output.txt" "=== testorg/repo-excluded ===" "repo-excluded: never processed by sync"

    # Unprotected repos now take the DIRECT-push path — no PRs are created.
    assert_not_contains "$pr_log" "pr-created" "sync used direct push (no PRs) for unprotected repos"
    assert_contains "$TEST_DIR/sync-full-output.txt" "Pushed directly to main." "sync output shows a direct push to main"

    # Verify summary line
    assert_contains "$TEST_DIR/sync-full-output.txt" "Sync complete:" "sync shows summary line"
    assert_contains "$TEST_DIR/sync-full-output.txt" "7 synced" "sync reports 7 synced"
    assert_contains "$TEST_DIR/sync-full-output.txt" "0 failed" "sync reports 0 failed"
}

# ── Test 3a: sync.sh with SYNC_OWNERS (multiple owners) ───────────────────

test_sync_multi_owner() {
    echo ""
    echo "=== Test: sync.sh (SYNC_OWNERS multi-owner) ==="

    # test_sync_full (run earlier) direct-pushed to the testorg bare repos'
    # main, mutating shared state. Restore the pristine bares so this run
    # re-syncs every repo from the pre-sync baseline and "8 synced" holds.
    reset_bare_repos

    local pr_log="$TEST_DIR/pr-creations-multi.log"
    rm -f "$pr_log"

    local output
    output=$(
        SYNC_OWNERS="testorg testorg2" \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        MOCK_PR_LOG="$pr_log" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" 2>&1
    ) || true

    echo "$output" > "$TEST_DIR/sync-multi-output.txt"

    assert_contains "$TEST_DIR/sync-multi-output.txt" "Scanning repos for: testorg" "multi-owner: scans testorg"
    assert_contains "$TEST_DIR/sync-multi-output.txt" "Scanning repos for: testorg2" "multi-owner: scans testorg2"
    assert_contains "$TEST_DIR/sync-multi-output.txt" "=== testorg2/repo-owner2-only ===" "multi-owner: processes testorg2's repo"
    assert_not_contains "$TEST_DIR/sync-multi-output.txt" "=== testorg2/_agent-guidance ===" "multi-owner: excludes self repo for testorg2"
    assert_contains "$TEST_DIR/sync-multi-output.txt" "repo-with-sync" "multi-owner: still processes testorg's repos"

    # Every repo is unprotected, so the sync direct-pushes to main — no PRs.
    assert_not_contains "$pr_log" "pr-created" "multi-owner: all repos direct-pushed (no PRs created)"

    assert_contains "$TEST_DIR/sync-multi-output.txt" "8 synced" "multi-owner: sync reports 8 synced"
    assert_contains "$TEST_DIR/sync-multi-output.txt" "0 failed" "multi-owner: sync reports 0 failed"
}

# ── Test 3a2: sync.sh per-owner token resolution & restoration ────────────

test_sync_per_owner_token() {
    echo ""
    echo "=== Test: sync.sh (per-owner token resolution & restoration) ==="

    # Reset branches from prior tests so each sync.sh invocation below can
    # push a clean branch again (see test_sync_multi_owner for why this is
    # needed).
    for bare in "$TEST_DIR"/bare/testorg_* "$TEST_DIR"/bare/testorg2_*; do
        git -C "$bare" branch -D agents-md-sync/update >/dev/null 2>&1 || true
    done

    # Case 1: a per-owner token set for the SECOND owner (testorg2) only,
    # plus a base GH_TOKEN. testorg (no per-owner token of its own) must
    # fall back to the base token silently — it must not log per-owner
    # usage. Exact-line match (grep -x), not the shared assert_contains
    # substring helper: "testorg" is a literal prefix of "testorg2", so a
    # substring search for testorg's log line would spuriously match
    # testorg2's "Using per-owner token for testorg2" line too.
    local out1="$TEST_DIR/sync-token-case1.txt"
    local output1
    output1=$(
        SYNC_OWNERS="testorg testorg2" \
        GH_TOKEN="base-token" \
        GH_TOKEN_TESTORG2="testorg2-token" \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" 2>&1
    ) || true
    echo "$output1" > "$out1"

    if grep -qxF "  Using per-owner token for testorg2" "$out1"; then
        pass "per-owner token: testorg2 uses its own token"
    else
        fail "per-owner token: testorg2 uses its own token"
    fi
    if grep -qxF "  Using per-owner token for testorg" "$out1"; then
        fail "per-owner token: testorg (no per-owner token) does not claim one"
    else
        pass "per-owner token: testorg (no per-owner token) does not claim one"
    fi

    # Reset branches again for the second invocation below.
    for bare in "$TEST_DIR"/bare/testorg_* "$TEST_DIR"/bare/testorg2_*; do
        git -C "$bare" branch -D agents-md-sync/update >/dev/null 2>&1 || true
    done

    # Case 2 (restoration): a per-owner token set for the FIRST owner
    # (testorg) only. testorg2's iteration must not reuse testorg's
    # token — testorg2 has none of its own, so it must fall back to the
    # base token instead of leaking testorg's token across iterations.
    local out2="$TEST_DIR/sync-token-case2.txt"
    local output2
    output2=$(
        SYNC_OWNERS="testorg testorg2" \
        GH_TOKEN="base-token" \
        GH_TOKEN_TESTORG="testorg-token" \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" 2>&1
    ) || true
    echo "$output2" > "$out2"

    if grep -qxF "  Using per-owner token for testorg" "$out2"; then
        pass "per-owner token restoration: first owner (testorg) uses its own token"
    else
        fail "per-owner token restoration: first owner (testorg) uses its own token"
    fi
    if grep -qxF "  Using per-owner token for testorg2" "$out2"; then
        fail "per-owner token restoration: second owner (testorg2) does not reuse testorg's token"
    else
        pass "per-owner token restoration: second owner (testorg2) does not reuse testorg's token"
    fi
}

# ── Test 3b: sync.sh exits non-zero on per-repo failure ───────────────

test_sync_failure_exit_code() {
    echo ""
    echo "=== Test: sync.sh (failure exit code) ==="

    # Create a mock gh that lists repos but clone always fails
    local gh_fail_mock="$TEST_DIR/bin-fail/gh"
    mkdir -p "$TEST_DIR/bin-fail"
    cat > "$gh_fail_mock" <<'GHSCRIPT'
#!/usr/bin/env bash
case "$1" in
    repo)
        case "$2" in
            list)
                jq_filter=""
                for arg in "$@"; do
                    if [[ "$prev" == "--jq" ]]; then jq_filter="$arg"; fi
                    prev="$arg"
                done
                json='[{"nameWithOwner":"testorg/some-repo"}]'
                if [[ -n "$jq_filter" ]]; then
                    echo "$json" | jq -r "$jq_filter"
                else
                    echo "$json"
                fi
                ;;
            clone)
                echo "ERROR: permission denied" >&2
                exit 1
                ;;
        esac
        ;;
esac
GHSCRIPT
    chmod +x "$gh_fail_mock"

    local exit_code=0
    GITHUB_REPOSITORY_OWNER=testorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    PATH="$TEST_DIR/bin-fail:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" > "$TEST_DIR/sync-fail-output.txt" 2>&1 || exit_code=$?

    if [[ $exit_code -ne 0 ]]; then
        pass "sync exits non-zero when repos fail"
    else
        fail "sync should exit non-zero when repos fail (got exit code 0)"
    fi

    assert_contains "$TEST_DIR/sync-fail-output.txt" "1 failed" "sync reports failure count"
}

# ── Test 3c: no-marker assemble + marker-case parse round-trip ────────────
#
# Regression test for two data-loss bugs in the "# ── Assemble" section:
#
# 1. Inverted no-marker ordering: the no-marker branch used to put
#    pre-existing hand-written content ABOVE the marker and managed content
#    below it. On the *next* sync, the marker-case parse (content from
#    "$MARKER" down is "repo-specific" and survives; everything above it is
#    managed and gets overwritten) would treat that stale managed copy as
#    repo-specific and discard the hand-written content sitting above it —
#    silent, permanent data loss on the second sync.
#
# 2. Glued marker in the marker case: managed_content carries no trailing
#    newline (command substitution strips it), so the old
#    `printf '%s%s\n'` glued the marker line onto
#    "<!-- END MANAGED SECTION -->". A glued marker still passes the
#    unanchored `grep -qF` presence check but fails the anchored
#    `sed -n "/^MARKER/..."` parse, leaving repo_specific empty — dropping
#    all preserved content one sync later (third sync from a no-marker seed).
#
# Hence the three consecutive cycles below, asserting after each that the
# hand-written content survives and the marker starts its own line, plus
# byte-identical output between cycles (idempotency — anything less would
# also churn PRs forever instead of hitting the "Up to date" diff check).
#
# This exercises the actual "# ── Preserve repo-specific content" and
# "# ── Assemble" blocks from sync.sh — extracted by their section-comment
# anchors, the same style the script itself uses to delimit managed content —
# so the test tracks the real implementation instead of a hand-duplicated
# copy that could silently drift out of sync with it. It needs neither `gh`
# nor `jq`, so it runs even in environments where the full mocked sync.sh
# pipeline (used by the tests above) cannot.

extract_sync_block() {
    # Prints the lines between two "# ── <label>" section-comment anchors in
    # sync.sh, exclusive of the closing anchor line.
    sed -n "/# ── $1/,/# ── $2/p" "$REPO_ROOT/scripts/sync.sh" | sed '$d'
}

test_sync_round_trip_no_marker() {
    echo ""
    echo "=== Test: no-marker assemble + marker-case parse round-trip ==="

    local rt_dir="$TEST_DIR/round-trip"
    mkdir -p "$rt_dir"

    local MARKER="## Repo-specific additions"
    local preserve_block assemble_block
    preserve_block=$(extract_sync_block "Preserve repo-specific content" "Assemble")
    assemble_block=$(extract_sync_block "Assemble" "Diff check")

    # Seed a hand-written AGENTS.md with NO marker — the scenario that used
    # to get inverted.
    cat > "$rt_dir/AGENTS.md" <<'MD'
# Our Custom Agent Guide

Follow these repo-specific rules when working in this codebase.

- Always run linting before commits
- Use conventional commit messages
MD

    # --- Three consecutive syncs. Cycle 1 exercises the no-marker branch
    #     (adopts the existing file as repo-specific content below a
    #     newly-added marker); cycles 2 and 3 exercise the marker-case parse
    #     against the previous cycle's output. Cycle 3 is what catches the
    #     glued-marker bug: gluing happens on cycle 2, data loss on cycle 3. ---
    local managed_content new_agents_md repo_specific existing_prefix
    local cycle marker_count
    for cycle in 1 2 3; do
        managed_content=$("$REPO_ROOT/scripts/build-agents-md.sh" python)
        (
            cd "$rt_dir"
            eval "$preserve_block"
            eval "$assemble_block"
            echo "$new_agents_md" > AGENTS.md
        )
        cp "$rt_dir/AGENTS.md" "$rt_dir/AGENTS.md.cycle$cycle"

        assert_contains "$rt_dir/AGENTS.md" "# Our Custom Agent Guide" "round-trip cycle $cycle: original heading survives"
        assert_contains "$rt_dir/AGENTS.md" "Follow these repo-specific rules when working in this codebase." "round-trip cycle $cycle: original body survives"
        assert_contains "$rt_dir/AGENTS.md" "Always run linting before commits" "round-trip cycle $cycle: original bullet 1 survives"
        assert_contains "$rt_dir/AGENTS.md" "Use conventional commit messages" "round-trip cycle $cycle: original bullet 2 survives"
        assert_contains "$rt_dir/AGENTS.md" "## Python" "round-trip cycle $cycle: managed python section present"

        # The marker must start its own line — a marker glued onto the end of
        # the managed content still passes grep -qF but breaks the anchored
        # sed parse on the following sync.
        marker_count=$(grep -c "^## Repo-specific additions" "$rt_dir/AGENTS.md" || true)
        if [[ "$marker_count" -eq 1 ]]; then
            pass "round-trip cycle $cycle: marker at start of its own line (exactly once)"
        else
            fail "round-trip cycle $cycle: marker at start of its own line (exactly once) — anchored count $marker_count"
        fi
    done

    # Idempotency: re-syncing an already-correct file must be byte-identical,
    # otherwise sync.sh's diff check never reports "Up to date" and every
    # repo gets a churn PR on every run.
    if cmp -s "$rt_dir/AGENTS.md.cycle1" "$rt_dir/AGENTS.md.cycle2"; then
        pass "round-trip idempotency: cycle 1 and cycle 2 outputs byte-identical"
    else
        fail "round-trip idempotency: cycle 1 and cycle 2 outputs byte-identical"
    fi
    if cmp -s "$rt_dir/AGENTS.md.cycle2" "$rt_dir/AGENTS.md.cycle3"; then
        pass "round-trip idempotency: cycle 2 and cycle 3 outputs byte-identical"
    else
        fail "round-trip idempotency: cycle 2 and cycle 3 outputs byte-identical"
    fi
}

# ── Test 4: drift-report.sh ───────────────────────────────────────────────

test_drift_report() {
    echo ""
    echo "=== Test: drift-report.sh ==="

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=testorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true

    echo "$output" > "$TEST_DIR/drift-output.txt"

    assert_contains "$REPO_ROOT/drift-report.md" "# AGENTS.md Drift Report" "drift report has title"
    assert_contains "$REPO_ROOT/drift-report.md" "repo-with-sync" "drift report includes repo-with-sync"
    assert_contains "$REPO_ROOT/drift-report.md" "repo-no-sync" "drift report includes repo-no-sync"
    assert_contains "$REPO_ROOT/drift-report.md" "repo-with-existing" "drift report includes repo-with-existing"
    assert_contains "$REPO_ROOT/drift-report.md" "Status legend" "drift report has legend"
    assert_contains "$REPO_ROOT/drift-report.md" "Organization:" "drift report shows org"
    assert_contains "$REPO_ROOT/drift-report.md" "7 repo(s) scanned" "drift report shows repo count"
    assert_not_contains "$REPO_ROOT/drift-report.md" "_agent-guidance" "drift report excludes self"
    assert_not_contains "$REPO_ROOT/drift-report.md" "repo-excluded" "drift report excludes repos.yml-excluded repo"

    # CLAUDE.md bridge column
    assert_contains "$REPO_ROOT/drift-report.md" "CLAUDE.md bridge" "drift report has CLAUDE.md bridge column"
    assert_row_contains "$REPO_ROOT/drift-report.md" "repo-with-existing" "bridge-ok" "drift report: repo-with-existing is bridge-ok"
    assert_row_contains "$REPO_ROOT/drift-report.md" "repo-with-claude-md" "**no-import**" "drift report: repo-with-claude-md is no-import"
    assert_row_contains "$REPO_ROOT/drift-report.md" "repo-fix-claude" "**no-import**" "drift report: repo-fix-claude is no-import"
    assert_row_contains "$REPO_ROOT/drift-report.md" "repo-no-sync" "missing" "drift report: repo-no-sync bridge is missing"
    assert_contains "$REPO_ROOT/drift-report.md" "CLAUDE.md bridge legend" "drift report has CLAUDE.md bridge legend"

    # Cron-coverage classification: the fixture repos.yml classifies every mock
    # repo, so the report must say NOTHING. Asserted here rather than only in
    # the flagging test because a check that fires on a fully-classified fleet
    # is noise nobody would read twice.
    assert_not_contains "$REPO_ROOT/drift-report.md" "Unclassified for cron coverage" \
        "drift report: a fully classified fleet raises nothing"
}

# ── Test 4b: drift-report.sh skills-bootstrap column ──────────────────────

test_drift_report_bootstrap() {
    echo ""
    echo "=== Test: drift-report.sh (skills-bootstrap column) ==="

    # Observe the fully-delivered state produced by test_sync_bootstrap.
    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/drift-bootstrap-output.txt"

    local rpt="$REPO_ROOT/drift-report.md"
    assert_contains "$rpt" "skills-bootstrap" "drift report has a skills-bootstrap column"
    assert_contains "$rpt" "skills-bootstrap legend" "drift report has a skills-bootstrap legend"

    assert_row_contains "$rpt" "repo-adopted" "ok" "drift report: repo-adopted is ok"
    # The cheapest answer to invisible lock staleness: print what each lock
    # pins, per federated source.
    assert_row_contains "$rpt" "repo-adopted" "lock: agentskills@1111111 + cms-platform@2222222" "drift report: repo-adopted's federated lock pins are shown"
    assert_row_contains "$rpt" "repo-no-lock" "no-lock" "drift report: repo-no-lock shows the withheld state"

    # ── The three states that never self-heal must not hide inside
    #    `**missing**`, whose whole meaning is "the next sync delivers it".
    assert_row_contains "$rpt" "repo-ignored" "**blocked**" "drift report: a gitignored .claude/ is blocked, not missing"
    assert_row_contains "$rpt" "repo-ignored" '`.claude/` gitignored' "drift report: repo-ignored's Notes name the reason"
    assert_row_contains "$rpt" "repo-unparseable" "**refused**" "drift report: an unparseable settings.json is refused, not missing"
    assert_row_contains "$rpt" "repo-unparseable" '`settings.json` unparseable' "drift report: repo-unparseable's Notes name the reason"
    assert_row_contains "$rpt" "repo-hook-no-lock" "**degraded**" "drift report: a hook with no lock is degraded, not no-lock and not ok"

    # A `.gitignore` that says nothing about `.claude/` must not blocked-flag
    # a healthy repo: the probe keys on a rule that MATCHES the artifact.
    assert_row_contains "$rpt" "repo-adopted" "ok" "drift report: an unrelated .gitignore leaves the happy path ok"

    # The legend no longer delegates the distinction to a log the reader has
    # to go find.
    assert_not_contains "$rpt" "check the sync log for a gitignored" "drift report: the missing legend no longer punts to the sync log"
    assert_contains "$rpt" "| **blocked** |" "drift report: legend documents blocked"
    assert_contains "$rpt" "| **refused** |" "drift report: legend documents refused"

    # repo-not-allowed carries a lock but no hook and is not allowlisted, so
    # the bootstrap column must stay blank rather than inventing a to-do.
    if grep -F "repo-not-allowed" "$rpt" | grep -qF "no-lock"; then
        fail "drift report: non-allowlisted repo has no bootstrap state"
    else
        pass "drift report: non-allowlisted repo has no bootstrap state"
    fi
}

# ── Test 4b2: a repo classified by neither cron_coverage key is flagged ───
#
# `check-cron-coverage.js` audits a DISK and so cannot notice a repo that is
# not checked out — issue #37, where 14 of 25 repos were audited and the other
# 11 produced no output at all. repos.yml's `cron_coverage.fleet` makes an
# absent one an ERROR there; this report is the only thing that sees what the
# ACCOUNT holds, so it is the only thing that can notice a repo missing from
# the list itself. Without this test the list could rot exactly as silently as
# the crons it exists to cover.

test_drift_report_cron_classification() {
    echo ""
    echo "=== Test: drift-report.sh (cron-coverage classification) ==="

    # Drop one repo from both keys — the state a newly created repo is in.
    # `repo-not-allowed` is chosen because nothing else asserts on its row, and
    # the deletion is anchored to the flow-sequence spelling so it cannot reach
    # into skills_bootstrap.repos.
    sed 's/, repo-not-allowed,/,/' "$TEST_DIR/repos.yml" > "$TEST_DIR/repos-uncron.yml"

    # Prove the fixture actually changed: a no-op sed would make every
    # assertion below pass against an unmodified file.
    if diff -q "$TEST_DIR/repos.yml" "$TEST_DIR/repos-uncron.yml" >/dev/null; then
        fail "drift report: the unclassified-repo fixture did not change repos.yml"
        return
    fi

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos-uncron.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/drift-uncron-output.txt"

    local rpt="$REPO_ROOT/drift-report.md"
    assert_contains "$rpt" "Unclassified for cron coverage (1)" \
        "drift report: an unclassified repo is counted"
    assert_contains "$rpt" "bootorg/repo-not-allowed" \
        "drift report: the unclassified repo is named"
    assert_contains "$rpt" "cron_coverage.out_of_scope" \
        "drift report: the finding says which keys resolve it"
    assert_contains "$TEST_DIR/drift-uncron-output.txt" "1 repo(s) unclassified for cron coverage" \
        "drift report: the run log surfaces the count too"

    # Classified repos must NOT be swept in with it.
    if grep -q '^> - `bootorg/repo-adopted`' "$rpt"; then
        fail "drift report: a classified repo was reported unclassified"
    else
        pass "drift report: classified repos are not flagged"
    fi
}

# ── Test 4c: a hook in a repo that is no longer allowlisted ───────────────

test_drift_report_bootstrap_unmanaged() {
    echo ""
    echo "=== Test: drift-report.sh (unmanaged hook detection) ==="

    # The sync has no delete semantics: dropping a repo from the allowlist
    # leaves its hook in place, still running. `unmanaged` is the only thing
    # in the fleet that would ever say so.
    sed '/- repo-adopted/d' "$TEST_DIR/repos.yml" > "$TEST_DIR/repos-dropped.yml"

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos-dropped.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/drift-unmanaged-output.txt"

    assert_row_contains "$REPO_ROOT/drift-report.md" "repo-adopted" "**unmanaged**" "drift report: a hook left behind by a de-allowlisted repo is flagged unmanaged"
}

# ── Test 4d: the registry itself is never `unmanaged` ─────────────────────

test_drift_report_bootstrap_registry() {
    echo ""
    echo "=== Test: drift-report.sh (the registry is not an unmanaged hook) ==="

    # The registry AUTHORS the hook and is deliberately off the allowlist, so
    # a naive "not allowlisted + has a hook" rule points `unmanaged`'s "remove
    # it by hand" advice straight at the source of truth. Scan it as a target
    # once and prove it does not.
    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_INCLUDE_REGISTRY=1 \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/drift-registry-output.txt"

    if grep -F "bootorg/agentskills" "$REPO_ROOT/drift-report.md" | grep -qF "**unmanaged**"; then
        fail "drift report: the registry that authors the hook is not flagged unmanaged"
    else
        pass "drift report: the registry that authors the hook is not flagged unmanaged"
    fi
}

# ── Test 4e: the ignore probe leaves no temp directory behind ─────────────

test_drift_report_probe_cleanup() {
    echo ""
    echo "=== Test: drift-report.sh (ignore probe is cleaned up) ==="

    # `bootstrap_blocked` creates one `mktemp -d` probe per RUN — it is reused
    # across repos, which is what `IGNORE_PROBE_DIR` memoizes — and nothing ever
    # removed it, so every run left one behind; this suite alone leaked three.
    # Point TMPDIR at an empty directory of our own so whatever the run leaves
    # behind is unambiguously the probe and nothing else.
    local probe_tmp="$TEST_DIR/probe-tmpdir"
    rm -rf "$probe_tmp"
    mkdir -p "$probe_tmp"

    # Every drift test writes the report to the same fixed path, and the
    # preceding one leaves the very same `repo-ignored | **blocked**` row there
    # from its own bootorg fixtures. Delete it immediately before the run so the
    # row the guard below reads can only have come from THIS invocation —
    # otherwise the guard passes on a stale artifact even when the script under
    # test never executes at all.
    rm -f "$REPO_ROOT/drift-report.md"

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        TMPDIR="$probe_tmp" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/drift-cleanup-output.txt"

    # Guard against a vacuous pass. If no repo reaches the probe, no directory
    # is ever created and the leak check below would pass without testing
    # anything. `repo-ignored` reaches `**blocked**` only THROUGH the probe, so
    # this assertion is what proves the run exercised the path.
    assert_row_contains "$REPO_ROOT/drift-report.md" "repo-ignored" "**blocked**" \
        "drift report cleanup: the run actually exercised the ignore probe"

    local leftover
    leftover=$(find "$probe_tmp" -mindepth 1 -maxdepth 1)
    if [[ -z "$leftover" ]]; then
        pass "drift report: the ignore probe directory is removed on exit"
    else
        fail "drift report: the ignore probe directory is removed on exit — left behind: $(echo "$leftover" | tr '\n' ' ')"
    fi
}

# ── Test 4a: drift-report.sh with SYNC_OWNERS (multiple owners) ───────────

test_drift_report_multi_owner() {
    echo ""
    echo "=== Test: drift-report.sh (SYNC_OWNERS multi-owner) ==="

    local output
    output=$(
        SYNC_OWNERS="testorg testorg2" \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true

    echo "$output" > "$TEST_DIR/drift-multi-output.txt"

    assert_contains "$REPO_ROOT/drift-report.md" "## testorg" "multi-owner drift report has testorg heading"
    assert_contains "$REPO_ROOT/drift-report.md" "## testorg2" "multi-owner drift report has testorg2 heading"
    assert_contains "$REPO_ROOT/drift-report.md" "repo-owner2-only" "multi-owner drift report includes testorg2's repo"
    assert_contains "$REPO_ROOT/drift-report.md" "repo-with-sync" "multi-owner drift report still includes testorg's repos"

    local count
    count=$(grep -c "Status legend" "$REPO_ROOT/drift-report.md" || true)
    if [[ "$count" -eq 1 ]]; then
        pass "multi-owner drift report has Status legend exactly once"
    else
        fail "multi-owner drift report has Status legend exactly once — got count $count"
    fi
}

# ── Test 3d: protected default branch → PR fallback + auto-merge ──────────

test_sync_protected_fallback() {
    echo ""
    echo "=== Test: sync.sh (protected default branch → PR fallback) ==="

    local pr_log="$TEST_DIR/pr-protected.log"
    local pr_body_dir="$TEST_DIR/pr-bodies-protected"
    rm -f "$pr_log"
    rm -rf "$pr_body_dir"

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=protorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        MOCK_PR_LOG="$pr_log" \
        MOCK_PR_BODY_DIR="$pr_body_dir" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" 2>&1
    ) || true

    echo "$output" > "$TEST_DIR/sync-protected-output.txt"

    assert_contains "$TEST_DIR/sync-protected-output.txt" "direct push to main rejected — falling back to PR" "protected: logs the rejected direct push + fallback"

    # main must be UNCHANGED — the pre-receive hook rejected the direct push,
    # so no managed AGENTS.md landed there (repo-protected had none to start).
    local prot_bare="$TEST_DIR/bare/protorg_repo-protected"
    local verify_main="$TEST_DIR/verify-protected-main"
    git clone "$prot_bare" "$verify_main" 2>/dev/null || {
        fail "repo-protected: could not clone main"
        return
    }
    assert_not_contains "$verify_main/AGENTS.md" "BEGIN MANAGED SECTION" "repo-protected: main left unchanged (no managed AGENTS.md)"

    # The managed content must have landed on the fallback branch instead.
    local verify_branch="$TEST_DIR/verify-protected-branch"
    git clone "$prot_bare" "$verify_branch" -b agents-md-sync/update 2>/dev/null || {
        fail "repo-protected: fallback branch not pushed"
        return
    }
    assert_contains "$verify_branch/AGENTS.md" "## Python" "repo-protected: fallback branch has managed python section"
    assert_contains "$verify_branch/AGENTS.md" "BEGIN MANAGED SECTION" "repo-protected: fallback branch has managed section"
    # The fixture seeds a diverged stale agents-md-sync/update branch; the
    # fallback must force-push over it (non-fast-forward) rather than fail.
    assert_not_contains "$verify_branch/AGENTS.md" "stale old sync content" "repo-protected: force-push replaced the diverged stale branch"

    # A PR was created and auto-merge was enabled on it (the "--auto" flag).
    assert_contains "$pr_log" "pr-created" "protected: PR created on fallback"
    assert_contains "$pr_log" "pr-merged 1 --auto" "protected: auto-merge enabled on the fallback PR"
    # ...as a MERGE COMMIT, and on the first attempt. The fleet default is
    # merge-only, so leading with --squash spent a guaranteed failed call per
    # repo; the mock succeeds on whatever it is given, so the first logged line
    # is the method the sync actually prefers.
    assert_contains "$pr_log" "pr-merged 1 --auto --merge" "protected: auto-merge uses a merge commit"
    if head -n 1 "$pr_log" >/dev/null 2>&1 && grep -q -- "--squash" "$pr_log"; then
        fail "protected: sync fell back to --squash when --merge was available"
    else
        pass "protected: no wasted --squash attempt"
    fi

    # PR bodies are captured only on the fallback path — the no-import warning
    # and the fix_claude_md opt-in note now surface here.
    assert_contains "$pr_body_dir/protorg_repo-protected.body" "does not import" "protected: PR body warns about the non-bridging CLAUDE.md"
    assert_contains "$pr_body_dir/protorg_repo-protected-fix.body" "fix_claude_md" "protected: PR body notes the fix_claude_md opt-in"

    assert_contains "$TEST_DIR/sync-protected-output.txt" "2 synced" "protected: both repos synced via fallback"
    assert_contains "$TEST_DIR/sync-protected-output.txt" "0 failed" "protected: no repo failures on the fallback path"
}

# ── Test 3e: stale PR/branch cleanup after a direct push ──────────────────

test_sync_stale_cleanup() {
    echo ""
    echo "=== Test: sync.sh (stale PR/branch cleanup after direct push) ==="

    local pr_log="$TEST_DIR/pr-stale.log"
    rm -f "$pr_log"

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=stalorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        MOCK_PR_LOG="$pr_log" \
        MOCK_OPEN_PR_REPOS="stalorg_repo-stale" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" 2>&1
    ) || true

    echo "$output" > "$TEST_DIR/sync-stale-output.txt"

    assert_contains "$TEST_DIR/sync-stale-output.txt" "Pushed directly to main." "stale: direct push to main succeeded"

    # Managed content updated on main.
    local stale_bare="$TEST_DIR/bare/stalorg_repo-stale"
    local verify_main="$TEST_DIR/verify-stale-main"
    git clone "$stale_bare" "$verify_main" 2>/dev/null || {
        fail "repo-stale: could not clone main"
        return
    }
    assert_contains "$verify_main/AGENTS.md" "## Python" "stale: managed content pushed to main"

    # The pre-existing open PR #42 was closed.
    assert_contains "$pr_log" "pr-closed 42" "stale: superseded PR #42 closed"

    # The stale sync branch was deleted from the remote.
    if git ls-remote --heads "$stale_bare" agents-md-sync/update | grep -q agents-md-sync/update; then
        fail "stale: agents-md-sync/update branch still present on remote"
    else
        pass "stale: agents-md-sync/update branch deleted from remote"
    fi
}

# ── Test 5: bootstrap-status.sh ───────────────────────────────────────────

test_bootstrap_status() {
    echo ""
    echo "=== Test: bootstrap-status.sh ==="

    local s="$REPO_ROOT/scripts/bootstrap-status.sh"
    local d="$TEST_DIR/bootstrap-status"
    mkdir -p "$d"
    local result rc

    cp "$TEST_DIR/existing-settings.json" "$d/other-hook.json"
    result=$("$s" "$d/other-hook.json")
    [[ "$result" == "no-entry" ]] && pass "unrelated SessionStart entry -> no-entry" || fail "unrelated SessionStart entry -> no-entry (got '$result')"

    cat > "$d/registered.json" <<'JSON'
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/scripts/setup-hooks.sh\""}]},
      {"matcher": "startup|resume", "hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/skills-bootstrap.sh\"", "timeout": 90}]}
    ]
  }
}
JSON
    result=$("$s" "$d/registered.json")
    [[ "$result" == "registered" ]] && pass "hook named in a SessionStart command -> registered" || fail "hook named in a SessionStart command -> registered (got '$result')"

    # agentskills' own entry: no matcher, no timeout. Matching the whole
    # command verbatim would call this unregistered and re-add it every run.
    cat > "$d/bare-entry.json" <<'JSON'
{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/skills-bootstrap.sh\""}]}]}}
JSON
    result=$("$s" "$d/bare-entry.json")
    [[ "$result" == "registered" ]] && pass "matcher-less hand-written entry -> registered" || fail "matcher-less hand-written entry -> registered (got '$result')"

    # The hook named under a DIFFERENT event never runs at session start. A
    # grep over the file would call this registered; a parse must not.
    cat > "$d/wrong-event.json" <<'JSON'
{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "bash .claude/hooks/skills-bootstrap.sh"}]}]}}
JSON
    result=$("$s" "$d/wrong-event.json")
    if [[ "$result" == "no-entry" ]]; then
        pass "hook under a non-SessionStart event -> no-entry (not fooled by a substring)"
    else
        fail "hook under a non-SessionStart event -> no-entry (got '$result')"
    fi

    printf '{ not json\n' > "$d/broken.json"
    result=$("$s" "$d/broken.json")
    [[ "$result" == "unparseable" ]] && pass "malformed JSON -> unparseable" || fail "malformed JSON -> unparseable (got '$result')"

    printf '[]\n' > "$d/array.json"
    result=$("$s" "$d/array.json")
    [[ "$result" == "unparseable" ]] && pass "non-object top level -> unparseable" || fail "non-object top level -> unparseable (got '$result')"

    result=$("$s" "$d/absent.json")
    [[ "$result" == "missing" ]] && pass "absent file -> missing" || fail "absent file -> missing (got '$result')"

    # A repo root passed instead of its settings.json is the obvious hand-run
    # slip. Directories have nonzero size, so the `-s` test alone lets one
    # through into `classify < "$1"` and python3 dies on a directory stdin.
    # It is a caller error (exit 2), and must NOT come back as one of the four
    # classifications: `missing` would read as "no hook registered here".
    rc=0
    result=$("$s" "$d" 2>/dev/null) || rc=$?
    if [[ "$rc" -eq 2 && ! "$result" =~ (registered|no-entry|unparseable|missing) ]]; then
        pass "directory argument -> exit 2 caller error (never a classification)"
    else
        fail "directory argument -> exit 2 caller error (never a classification) (got rc=$rc, stdout '$result')"
    fi

    result=$(printf '' | "$s" -)
    [[ "$result" == "missing" ]] && pass "stdin mode: empty -> missing" || fail "stdin mode: empty -> missing (got '$result')"

    result=$(cat "$d/registered.json" | "$s" -)
    [[ "$result" == "registered" ]] && pass "stdin mode: registered" || fail "stdin mode: registered (got '$result')"
}

# ── Test 5a: register-bootstrap-hook.sh (append + idempotence) ────────────

test_register_bootstrap_hook() {
    echo ""
    echo "=== Test: register-bootstrap-hook.sh (append, never overwrite) ==="

    local r="$REPO_ROOT/scripts/register-bootstrap-hook.sh"
    local d="$TEST_DIR/register-hook"
    mkdir -p "$d"
    local out rc

    # ── The regression both live consumers depend on: an existing
    #    setup-hooks.sh SessionStart entry must survive verbatim.
    cp "$TEST_DIR/existing-settings.json" "$d/consumer.json"
    out=$("$r" "$d/consumer.json")
    [[ "$out" == "registered" ]] && pass "existing settings.json -> registered" || fail "existing settings.json -> registered (got '$out')"

    assert_contains "$d/consumer.json" "setup-hooks.sh" "append: pre-existing hook command survives"
    assert_contains "$d/consumer.json" "skills-bootstrap.sh" "append: bootstrap hook command added"
    assert_contains "$d/consumer.json" "symlinkDirectories" "append: unrelated top-level keys survive"

    # Structure, not substrings: two SEPARATE groups, ours last, and the
    # pre-existing group's matcher/timeout/command untouched.
    python3 - "$d/consumer.json" <<'PY' > "$d/shape.txt"
import json, sys
doc = json.load(open(sys.argv[1]))
g = doc["hooks"]["SessionStart"]
print("groups=%d" % len(g))
print("first_cmd=%s" % g[0]["hooks"][0]["command"])
print("first_timeout=%s" % g[0]["hooks"][0].get("timeout"))
print("first_matcher=%s" % g[0].get("matcher"))
print("last_cmd=%s" % g[-1]["hooks"][0]["command"])
print("last_timeout=%s" % g[-1]["hooks"][0].get("timeout"))
print("last_matcher=%s" % g[-1].get("matcher"))
PY
    assert_contains "$d/shape.txt" "groups=2" "append: a SEPARATE group was added (not merged into the existing one)"
    assert_contains "$d/shape.txt" "first_cmd=bash \"\$CLAUDE_PROJECT_DIR/scripts/setup-hooks.sh\"" "append: existing group's command unchanged"
    assert_contains "$d/shape.txt" "first_timeout=30" "append: existing group's timeout unchanged"
    assert_contains "$d/shape.txt" "first_matcher=startup|resume" "append: existing group's matcher unchanged"
    assert_contains "$d/shape.txt" "last_cmd=bash \"\$CLAUDE_PROJECT_DIR/.claude/hooks/skills-bootstrap.sh\"" "append: new group's command matches the live reference"
    assert_contains "$d/shape.txt" "last_timeout=90" "append: new group's timeout is 90"

    # ── Idempotence: a second application must be a byte-for-byte no-op.
    cp "$d/consumer.json" "$d/consumer.after1.json"
    out=$("$r" "$d/consumer.json")
    [[ "$out" == "already-registered" ]] && pass "second application -> already-registered" || fail "second application -> already-registered (got '$out')"
    if cmp -s "$d/consumer.after1.json" "$d/consumer.json"; then
        pass "idempotent: file byte-identical after a second application"
    else
        fail "idempotent: file byte-identical after a second application"
    fi

    # ── Absent file: created with just our group.
    out=$("$r" "$d/fresh.json")
    [[ "$out" == "registered" ]] && pass "absent settings.json -> created" || fail "absent settings.json -> created (got '$out')"
    assert_contains "$d/fresh.json" "skills-bootstrap.sh" "absent settings.json: hook registered in the new file"
    out=$("$r" "$d/fresh.json")
    [[ "$out" == "already-registered" ]] && pass "created file is idempotent too" || fail "created file is idempotent too (got '$out')"

    # ── Unparseable: refuse, exit 3, write NOTHING.
    printf '{ not json\n' > "$d/broken.json"
    cp "$d/broken.json" "$d/broken.orig"
    rc=0
    out=$("$r" "$d/broken.json") || rc=$?
    [[ "$rc" -eq 3 ]] && pass "unparseable settings.json -> exit 3" || fail "unparseable settings.json -> exit 3 (got $rc)"
    if cmp -s "$d/broken.json" "$d/broken.orig"; then
        pass "unparseable settings.json left byte-identical (never rewritten)"
    else
        fail "unparseable settings.json left byte-identical (never rewritten)"
    fi

    # ── A `hooks` key of the wrong TYPE is configuration we do not
    #    understand; coercing it would destroy it.
    printf '{"hooks": "nope"}\n' > "$d/weird.json"
    cp "$d/weird.json" "$d/weird.orig"
    rc=0
    out=$("$r" "$d/weird.json") || rc=$?
    [[ "$rc" -eq 3 ]] && pass "wrong-typed hooks key -> exit 3" || fail "wrong-typed hooks key -> exit 3 (got $rc)"
    if cmp -s "$d/weird.json" "$d/weird.orig"; then
        pass "wrong-typed hooks key left byte-identical"
    else
        fail "wrong-typed hooks key left byte-identical"
    fi
}

# ── Test 5b: sync.sh delivers the bootstrap hook (opt-in, double-keyed) ────

test_sync_bootstrap() {
    echo ""
    echo "=== Test: sync.sh (skills-bootstrap delivery) ==="

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/sync-bootstrap.txt"

    assert_contains "$TEST_DIR/sync-bootstrap.txt" "pinned hook fetched" "bootstrap: pinned hook fetched and digest verified"

    # ── The gitignore fleet-killer. `git add` on an ignored path exits 1 and
    #    would abort the whole run under set -euo pipefail. repo-ignored sorts
    #    THIRD, so the three repos after it prove the run survived.
    assert_contains "$TEST_DIR/sync-bootstrap.txt" ".claude/ is gitignored in bootorg/repo-ignored" "bootstrap: gitignored .claude/ is detected and warned, not force-added"
    assert_contains "$TEST_DIR/sync-bootstrap.txt" "=== bootorg/repo-no-lock ===" "bootstrap: the run SURVIVES a gitignored .claude/ (later repos still processed)"
    assert_contains "$TEST_DIR/sync-bootstrap.txt" "=== bootorg/repo-unparseable ===" "bootstrap: the run reaches the last repo after the gitignored one"
    assert_contains "$TEST_DIR/sync-bootstrap.txt" "6 synced" "bootstrap: all six bootorg repos synced"
    assert_contains "$TEST_DIR/sync-bootstrap.txt" "0 failed" "bootstrap: no repo failures"

    # ── repo-adopted: hook delivered, registration APPENDED, lock untouched.
    local adopted="$TEST_DIR/verify-bootstrap-adopted"
    git clone "$TEST_DIR/bare/bootorg_repo-adopted" "$adopted" 2>/dev/null || {
        fail "repo-adopted: could not clone"
        return
    }
    if [[ -f "$adopted/.claude/hooks/skills-bootstrap.sh" ]]; then
        pass "repo-adopted: hook file delivered"
    else
        fail "repo-adopted: hook file delivered"
    fi
    if cmp -s "$adopted/.claude/hooks/skills-bootstrap.sh" "$TEST_DIR/pinned-hook.sh"; then
        pass "repo-adopted: delivered hook is byte-identical to the pinned copy"
    else
        fail "repo-adopted: delivered hook is byte-identical to the pinned copy"
    fi
    assert_contains "$adopted/.claude/settings.json" "setup-hooks.sh" "repo-adopted: pre-existing SessionStart entry preserved"
    assert_contains "$adopted/.claude/settings.json" "skills-bootstrap.sh" "repo-adopted: bootstrap entry appended"

    # THE non-negotiable: the federated lock must survive byte-for-byte.
    if cmp -s "$adopted/skills.lock" "$TEST_DIR/repo-adopted.skills.lock.orig"; then
        pass "repo-adopted: federated skills.lock byte-identical — the sync never writes it"
    else
        fail "repo-adopted: federated skills.lock byte-identical — the sync never writes it"
    fi
    assert_contains "$adopted/skills.lock" "bootorg/cms-platform" "repo-adopted: the lock's second federated source survives"

    # ── repo-no-lock: allowlisted but undeclared → withhold, and say why.
    assert_contains "$TEST_DIR/sync-bootstrap.txt" "no skills.lock in the repo yet" "repo-no-lock: withheld with a stated reason"
    local nolock="$TEST_DIR/verify-bootstrap-nolock"
    git clone "$TEST_DIR/bare/bootorg_repo-no-lock" "$nolock" 2>/dev/null || {
        fail "repo-no-lock: could not clone"
        return
    }
    if [[ -e "$nolock/.claude" ]]; then
        fail "repo-no-lock: nothing under .claude/ was created"
    else
        pass "repo-no-lock: nothing under .claude/ was created"
    fi
    if [[ -e "$nolock/skills.lock" ]]; then
        fail "repo-no-lock: the sync did NOT create a skills.lock"
    else
        pass "repo-no-lock: the sync did NOT create a skills.lock"
    fi

    # ── repo-not-allowed: has a lock, but the fleet never allowlisted it.
    local notallowed="$TEST_DIR/verify-bootstrap-notallowed"
    git clone "$TEST_DIR/bare/bootorg_repo-not-allowed" "$notallowed" 2>/dev/null || {
        fail "repo-not-allowed: could not clone"
        return
    }
    if [[ -e "$notallowed/.claude" ]]; then
        fail "repo-not-allowed: a lock alone does NOT trigger delivery"
    else
        pass "repo-not-allowed: a lock alone does NOT trigger delivery"
    fi

    # ── repo-ignored: warned above; nothing may have landed.
    local ignored="$TEST_DIR/verify-bootstrap-ignored"
    git clone "$TEST_DIR/bare/bootorg_repo-ignored" "$ignored" 2>/dev/null || {
        fail "repo-ignored: could not clone"
        return
    }
    if [[ -f "$ignored/.claude/hooks/skills-bootstrap.sh" ]]; then
        fail "repo-ignored: no hook committed into a repo that gitignores .claude/"
    else
        pass "repo-ignored: no hook committed into a repo that gitignores .claude/"
    fi

    # ── repo-unparseable: refuse to edit, deliver nothing, leave it alone.
    assert_contains "$TEST_DIR/sync-bootstrap.txt" "is not parseable JSON — refusing to edit it" "repo-unparseable: refusal is logged"
    local unparse="$TEST_DIR/verify-bootstrap-unparseable"
    git clone "$TEST_DIR/bare/bootorg_repo-unparseable" "$unparse" 2>/dev/null || {
        fail "repo-unparseable: could not clone"
        return
    }
    if cmp -s "$unparse/.claude/settings.json" "$TEST_DIR/repo-unparseable.settings.orig"; then
        pass "repo-unparseable: settings.json byte-identical (never rewritten)"
    else
        fail "repo-unparseable: settings.json byte-identical (never rewritten)"
    fi
    if [[ -f "$unparse/.claude/hooks/skills-bootstrap.sh" ]]; then
        fail "repo-unparseable: hook withheld too (a hook nothing runs is silently dead)"
    else
        pass "repo-unparseable: hook withheld too (a hook nothing runs is silently dead)"
    fi
}

# ── Test 5c: a second sync run is a no-op (no duplicate registration) ─────

test_sync_bootstrap_idempotent() {
    echo ""
    echo "=== Test: sync.sh (skills-bootstrap idempotence on re-run) ==="

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/sync-bootstrap-2.txt"

    assert_contains "$TEST_DIR/sync-bootstrap-2.txt" "6 skipped" "re-run: every bootorg repo is now up to date"
    assert_contains "$TEST_DIR/sync-bootstrap-2.txt" "0 synced" "re-run: nothing re-committed"

    local adopted="$TEST_DIR/verify-bootstrap-adopted-2"
    git clone "$TEST_DIR/bare/bootorg_repo-adopted" "$adopted" 2>/dev/null || {
        fail "repo-adopted (re-run): could not clone"
        return
    }
    local groups
    groups=$(python3 -c "import json;print(len(json.load(open('$adopted/.claude/settings.json'))['hooks']['SessionStart']))")
    if [[ "$groups" -eq 2 ]]; then
        pass "re-run: still exactly 2 SessionStart groups (registration not duplicated)"
    else
        fail "re-run: still exactly 2 SessionStart groups — got $groups"
    fi
    if cmp -s "$adopted/skills.lock" "$TEST_DIR/repo-adopted.skills.lock.orig"; then
        pass "re-run: skills.lock STILL byte-identical"
    else
        fail "re-run: skills.lock STILL byte-identical"
    fi
}

# ── Test 5d: a drifted hook is overwritten with the pinned copy ───────────

test_sync_bootstrap_drift() {
    echo ""
    echo "=== Test: sync.sh (drifted hook is re-pinned) ==="

    # Hand-edit the delivered hook on main, exactly as a well-meaning local
    # patch (or a stale older pin) would leave it.
    local w="$TEST_DIR/work/drift-adopted"
    rm -rf "$w"
    git clone "$TEST_DIR/bare/bootorg_repo-adopted" "$w" >/dev/null 2>&1
    git -C "$w" config commit.gpgsign false
    printf '# hand-edited, drifted\n' >> "$w/.claude/hooks/skills-bootstrap.sh"
    git -C "$w" add -A >/dev/null 2>&1
    git -C "$w" commit -m "drift the hook" >/dev/null 2>&1
    git -C "$w" push origin HEAD:main >/dev/null 2>&1

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/sync-bootstrap-drift.txt"

    assert_contains "$TEST_DIR/sync-bootstrap-drift.txt" "hook differed from the pin — overwritten" "drift: the divergent hook is overwritten, not preserved"

    local v="$TEST_DIR/verify-bootstrap-drift"
    git clone "$TEST_DIR/bare/bootorg_repo-adopted" "$v" 2>/dev/null || {
        fail "drift: could not clone"
        return
    }
    if cmp -s "$v/.claude/hooks/skills-bootstrap.sh" "$TEST_DIR/pinned-hook.sh"; then
        pass "drift: hook restored byte-identical to the pinned copy"
    else
        fail "drift: hook restored byte-identical to the pinned copy"
    fi
    assert_not_contains "$v/.claude/hooks/skills-bootstrap.sh" "hand-edited, drifted" "drift: the local edit is gone"
    if cmp -s "$v/skills.lock" "$TEST_DIR/repo-adopted.skills.lock.orig"; then
        pass "drift: skills.lock STILL byte-identical through the overwrite"
    else
        fail "drift: skills.lock STILL byte-identical through the overwrite"
    fi
}

# ── Test 5e: a digest mismatch disables delivery and fails the run ────────

test_sync_bootstrap_bad_digest() {
    echo ""
    echo "=== Test: sync.sh (pinned-hook digest mismatch) ==="

    reset_bare_repos

    local exit_code=0
    GITHUB_REPOSITORY_OWNER=bootorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos-baddigest.yml" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" > "$TEST_DIR/sync-bootstrap-baddigest.txt" 2>&1 || exit_code=$?

    assert_contains "$TEST_DIR/sync-bootstrap-baddigest.txt" "digest mismatch" "bad digest: mismatch reported"
    assert_contains "$TEST_DIR/sync-bootstrap-baddigest.txt" "Delivery disabled for this run" "bad digest: delivery disabled for the run"
    if [[ $exit_code -ne 0 ]]; then
        pass "bad digest: run exits non-zero"
    else
        fail "bad digest: run exits non-zero (got 0)"
    fi

    # AGENTS.md still syncs — a bad pin must not take the guidance layer down.
    local v="$TEST_DIR/verify-baddigest"
    rm -rf "$v"
    git clone "$TEST_DIR/bare/bootorg_repo-adopted" "$v" 2>/dev/null || {
        fail "bad digest: could not clone"
        return
    }
    assert_contains "$v/AGENTS.md" "BEGIN MANAGED SECTION" "bad digest: AGENTS.md still synced (fail-soft, not fail-stop)"
    if [[ -f "$v/.claude/hooks/skills-bootstrap.sh" ]]; then
        fail "bad digest: no hook delivered"
    else
        pass "bad digest: no hook delivered"
    fi
}

# ── Test 5f: --dry-run reports the bootstrap work honestly ────────────────

test_sync_bootstrap_dry_run() {
    echo ""
    echo "=== Test: sync.sh --dry-run (skills-bootstrap visibility) ==="

    reset_bare_repos

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" --dry-run 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/sync-bootstrap-dry.txt"

    assert_contains "$TEST_DIR/sync-bootstrap-dry.txt" "[DRY RUN] Would add .claude/hooks/skills-bootstrap.sh" "dry-run: names the hook it would add"
    assert_contains "$TEST_DIR/sync-bootstrap-dry.txt" "[DRY RUN] Would append a SessionStart entry" "dry-run: names the settings.json append"
    assert_contains "$TEST_DIR/sync-bootstrap-dry.txt" "existing entries preserved" "dry-run: states that existing entries are preserved"
    assert_contains "$TEST_DIR/sync-bootstrap-dry.txt" "[DRY RUN] Would NOT touch skills.lock" "dry-run: states that skills.lock is never touched"
    assert_contains "$TEST_DIR/sync-bootstrap-dry.txt" "no skills.lock in the repo yet" "dry-run: explains the withheld repo"

    # A dry run must change nothing at all.
    local v="$TEST_DIR/verify-dry"
    rm -rf "$v"
    git clone "$TEST_DIR/bare/bootorg_repo-adopted" "$v" 2>/dev/null || {
        fail "dry-run: could not clone"
        return
    }
    if [[ -f "$v/.claude/hooks/skills-bootstrap.sh" ]]; then
        fail "dry-run: nothing was actually written"
    else
        pass "dry-run: nothing was actually written"
    fi
}

# ── Test 5g: protected repo → the PR body discloses the hook ──────────────

test_sync_bootstrap_pr_body() {
    echo ""
    echo "=== Test: sync.sh (bootstrap disclosure in the fallback PR body) ==="

    reset_bare_repos
    install_reject_main_hook "$TEST_DIR/bare/bootorg_repo-adopted"

    local pr_body_dir="$TEST_DIR/pr-bodies-bootstrap"
    rm -rf "$pr_body_dir"

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        MOCK_PR_LOG="$TEST_DIR/pr-bootstrap.log" \
        MOCK_PR_BODY_DIR="$pr_body_dir" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/sync-bootstrap-prbody.txt"

    local body="$pr_body_dir/bootorg_repo-adopted.body"
    assert_contains "$body" "This PR also delivers" "PR body: the hook is called out explicitly"
    assert_contains "$body" "SessionStart" "PR body: names the event it runs on"
    assert_contains "$body" "ephemeral" "PR body: states that it only acts on ephemeral surfaces"
    assert_contains "$body" "always-on context" "PR body: discloses the standing context cost"
    assert_contains "$body" "existing entries are preserved" "PR body: states that existing SessionStart entries survive"
    assert_contains "$body" "sha256 verified before writing" "PR body: states the delivered bytes were digest-verified"

    rm -f "$TEST_DIR/bare/bootorg_repo-adopted/hooks/pre-receive"
}

# ── Test 6: check-cron-coverage.js ────────────────────────────────────────
#
# The gate that says a repo running crons actually watches them. It is pinned
# in BOTH directions against fixtures because presence is not coverage, and
# the first draft of this gate only checked presence: a caller missing
# `issues: write` and a caller with no `schedule:` trigger BOTH scored "OK".
# That certifies as covered a repo whose nightly audit 403s, and one whose
# audit can never fire at all — a new silently-failing cron dressed up as the
# fix for silently-failing crons. Those are the `noperm` and `nosched` cases;
# delete either assertion and the defect walks straight back in.
#
# The `covered` case is not decoration either: without a reachable pass path a
# gate that always fails would satisfy every negative assertion here.

test_check_cron_coverage() {
    echo ""
    echo "=== Test: check-cron-coverage.js ==="

    local script="$REPO_ROOT/scripts/check-cron-coverage.js"
    local caller="$REPO_ROOT/.github/workflows/scheduled-run-health.yml"
    local root="$TEST_DIR/cron"

    # No silent skip when the parser is missing. A gate that quietly does not
    # run is precisely the failure this suite exists to catch, so say what is
    # wrong and fail rather than pass vacuously. CI installs it with `npm ci`.
    if [[ ! -d "$REPO_ROOT/node_modules/yaml" ]]; then
        fail "cron coverage: node_modules/yaml is missing — run \`npm ci\` first"
        return
    fi

    # Every fixture is a REPOSITORY BUILT BY GIT — `git init` plus an empty
    # commit, which is the step that materializes `.git/index`. Two earlier
    # revisions of this helper hand-built the git dir instead, and each encoded
    # a contract WEAKER than what git writes, so the suite taught the wrong
    # invariant and would have waved through the wrong fix: revision one wrote
    # an empty FILE named `.git` (itself the evasion cases 7e/7f now pin), and
    # revision two wrote objects/ + refs/ + HEAD but no INDEX — and the index is
    # precisely what separates "this repo has no workflows" from "this checkout
    # omitted them" (cases 7j-7m). Fixtures no longer DESCRIBE git's output,
    # they ARE it. The empty commit tracks nothing, so the workflow files each
    # fixture writes afterwards are untracked — which the audit allows on
    # purpose: reading more than git tracks can only make a verdict louder.
    cron_repo() {
        git init -q --initial-branch=main "$root/$1"
        git -C "$root/$1" -c user.name=t -c user.email=t@example.com \
            commit -q --allow-empty -m seed
    }

    # Commits what the fixture has written so far, so its INDEX really lists the
    # workflow paths the checkout cases below then hide from the working tree.
    cron_commit() {
        git -C "$root/$1" add -A
        git -C "$root/$1" -c user.name=t -c user.email=t@example.com commit -q -m wf
    }

    # A scheduled workload, so every fixture below has something to watch.
    cron_workload() {
        cron_repo "$1"
        mkdir -p "$root/$1/.github/workflows"
        cat > "$root/$1/.github/workflows/nightly.yml" <<'EOF'
name: Nightly
on:
  schedule:
    - cron: '0 6 * * *'
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
EOF
    }

    # <fixture> <expected-exit> <expected-substring> <label>
    assert_cron() {
        local out rc=0
        out=$(node "$script" --repos-root "$root" --require "$1" 2>&1) || rc=$?
        if [[ "$rc" == "$2" ]] && grep -qF -- "$3" <<<"$out"; then
            pass "$4"
        else
            fail "$4 — expected exit $2 containing '$3'; got exit $rc: $(echo "$out" | head -2 | tr '\n' ' ')"
        fi
    }

    # Same contract driven through the ARGUMENT-LESS cwd form instead. Every
    # assertion above passes --repos-root/--require, so on its own the suite
    # leaves the cwd default — the only form a CI runner can use, and the one
    # ci.yml actually wires — completely unexercised. The `cd` is confined to
    # the subshell, so the caller's cwd is untouched.
    assert_cron_cwd() {
        local out rc=0
        out=$(cd "$root/$1" && node "$script" 2>&1) || rc=$?
        if [[ "$rc" == "$2" ]] && grep -qF -- "$3" <<<"$out"; then
            pass "$4"
        else
            fail "$4 — expected exit $2 containing '$3'; got exit $rc: $(echo "$out" | head -2 | tr '\n' ' ')"
        fi
    }

    rm -rf "$root"; mkdir -p "$root"

    # 1. The real caller, unmodified: the pass path must be reachable.
    cron_workload covered
    cp "$caller" "$root/covered/.github/workflows/scheduled-run-health.yml"
    assert_cron covered 0 "OK    covered:" "cron coverage: a correct caller passes"

    # 2. Crons, no caller at all — the state every adopting repo starts in.
    cron_workload uncovered
    assert_cron uncovered 1 "no scheduled-run-health caller" \
        "cron coverage: crons with no caller fail"

    # 3. Caller file present, job-level `uses:` deleted. The detector must key
    #    on the `uses:` VALUE, never on a filename it could be fooled by.
    cron_workload nouses
    sed '/uses: Adam-S-Daniel/d' "$caller" \
        > "$root/nouses/.github/workflows/scheduled-run-health.yml"
    assert_cron nouses 1 "no scheduled-run-health caller" \
        "cron coverage: a caller file with no uses: fails"

    # 4. Caller present but `issues: write` removed. Reusable permissions are
    #    CAPPED by the caller's grant, so this audit 403s every night.
    cron_workload noperm
    sed '/^  issues: write$/d' "$caller" \
        > "$root/noperm/.github/workflows/scheduled-run-health.yml"
    assert_cron noperm 1 "lacks permissions issues: write" \
        "cron coverage: a caller without issues: write fails"

    # 4b. Caller present and `issues:` GRANTED — but only at `read`. Pins the
    #     LEVEL of the grant, not merely its presence: without this, the
    #     scope check could be loosened to "the key exists" and case 4 above
    #     would still pass, while a read-only grant still 403s on every issue
    #     write the audit makes.
    cron_workload issread
    sed 's/^  issues: write$/  issues: read/' "$caller" \
        > "$root/issread/.github/workflows/scheduled-run-health.yml"
    assert_cron issread 1 "lacks permissions issues: write" \
        "cron coverage: a caller granting issues: read (not write) fails"

    # 5. Caller present but its own `schedule:` trigger removed — it can never
    #    fire, so it watches nothing while looking installed.
    cron_workload nosched
    sed '/^  schedule:$/,/^  workflow_dispatch:$/{/^  workflow_dispatch:$/!d;}' "$caller" \
        > "$root/nosched/.github/workflows/scheduled-run-health.yml"
    assert_cron nosched 1 "has no on.schedule:" \
        "cron coverage: a caller with no schedule: trigger fails"

    # 6. Workflows, but none scheduled: nothing to watch, so not a failure.
    cron_repo nocron
    mkdir -p "$root/nocron/.github/workflows"
    printf 'name: CI\non:\n  pull_request:\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n' \
        > "$root/nocron/.github/workflows/ci.yml"
    assert_cron nocron 0 "SKIP  nocron:" "cron coverage: a repo with no crons skips"

    # 7. No .github/workflows at all. The first draft FAILed here and so
    #    reddened rss-inator, a repo with nothing to audit. Measured
    #    2026-08-17: 3 of the 14 repos on this disk are in that state, which is
    #    why cases 7c/7d below identify a repo by .git and NOT by the presence
    #    of a workflows dir — that marker would redden all three.
    cron_repo noworkflows
    assert_cron noworkflows 0 "no .github/workflows" \
        "cron coverage: a repo with no workflows skips, not fails"

    # 7b. The repo DIRECTORY itself is absent — a typo'd or mislocated path.
    #     Distinct from case 7: there the repo is real and has nothing to
    #     audit; here nothing was audited at all. Collapsing the two makes the
    #     gate certify paths it never looked at (`--repos-root D:\repos` from
    #     a POSIX shell printed "All audited repos covered", exit 0). No
    #     mkdir here — the absence IS the fixture.
    assert_cron doesnotexist 1 "no such directory" \
        "cron coverage: a repo directory that does not exist is an error, not a skip"

    # 7c. The target EXISTS but is a regular FILE. `fs.existsSync` is true for a
    #     file, so four empty files named after the four adopting repos audited
    #     as clean: four "SKIP … no .github/workflows", "All audited repos
    #     covered", exit 0. Case 7b does not catch this — the path does exist.
    : > "$root/isafile"
    assert_cron isafile 1 "is not a directory" \
        "cron coverage: a target that is a file, not a directory, is an error"

    # 7d. The target exists, IS a directory, but is not a repo — `--repos-root`
    #     off by one level. Same operator-error class as 7b, and invited by this
    #     fleet's own documented layout: AGENTS.md specifies a TWO-segment
    #     `D:\repos\<owner>\<repo>`, so aiming at the owner level is the natural
    #     mistake, and an owner level is a perfectly real directory.
    #     Measured on the shipped code: `--repos-root /home --require user` →
    #     "SKIP user: no .github/workflows", exit 0. No .git here — that
    #     absence IS the fixture, so do not route this through cron_repo.
    mkdir -p "$root/notarepo"
    assert_cron notarepo 1 "not a repository" \
        "cron coverage: an existing non-repo directory is an error, not a skip"

    # 7e/7f. The NAME `.git` is not a repository either. Testing the marker by
    #     existence was the third revision of this predicate, and it certified a
    #     directory holding one zero-byte file called `.git`: measured on that
    #     code, four such directories named after the four adopting repos scored
    #     "SKIP … no .github/workflows" ×4, "All audited repos covered", exit 0.
    #     An empty DIRECTORY called `.git` did the same. Both are the same
    #     "the path resolved" error as 7b-7d, one level further down, which is
    #     why the fix stopped blacklisting shapes and started reading content.
    mkdir -p "$root/gitnamefile"; : > "$root/gitnamefile/.git"
    assert_cron gitnamefile 1 "not a repository" \
        "cron coverage: an empty file named .git is not a repository"
    mkdir -p "$root/gitnamedir/.git"
    assert_cron gitnamedir 1 "not a repository" \
        "cron coverage: an empty directory named .git is not a repository"

    # 7g. And one level below THAT: a git dir with the right entry NAMES but a
    #     zero-byte HEAD. Pins that HEAD is read and validated as a ref rather
    #     than merely existing — otherwise the predicate stops one directory
    #     short and the same defect class survives. Measured 2026-08-17: `git
    #     rev-parse --git-dir` in this fixture says "not a git repository", so
    #     accepting it would put the gate at odds with git itself.
    mkdir -p "$root/emptyhead/.git/objects" "$root/emptyhead/.git/refs"
    : > "$root/emptyhead/.git/HEAD"
    assert_cron emptyhead 1 "not a repository" \
        "cron coverage: a git dir whose HEAD is empty is not a repository"

    # 7h. The positive half, and the case existence-testing was chosen to
    #     protect: in a LINKED WORKTREE git writes `.git` as a FILE holding
    #     `gitdir: <path>`, and that git dir has no objects/ or refs/ of its own
    #     — it borrows both from the parent through `commondir`. Built by `git
    #     worktree add` rather than by hand: the hand-built version omitted the
    #     two things git actually writes there, the `gitdir` BACK-POINTER that
    #     7i turns on and the worktree's own `index` that 7j-7m turn on, so it
    #     encoded a shape strictly weaker than reality and would have passed a
    #     fix that reads neither. Without this assertion, closing 7e/7f by
    #     demanding a `.git` DIRECTORY would look correct and would break the
    #     argument-less cwd form ci.yml runs in every worktree.
    cron_repo wtparent
    git -C "$root/wtparent" worktree add -q --detach "$root/wtshape"
    assert_cron wtshape 0 "no .github/workflows" \
        "cron coverage: a real linked worktree is a repository"

    # 7i. A gitlink is a POINTER, and following one proves a repository exists
    #     SOMEWHERE — never that THIS directory is it. Measured 2026-08-17 on
    #     the shipped code: four directories, each holding the single line
    #     `gitdir: /home/user/_agent-guidance/.git` and audited under a
    #     DIFFERENT --require name, all scored "SKIP … nothing to watch" and
    #     "All audited repos covered", exit 0 — one real repo's git dir
    #     vouching for four fakes, which is case 7c's four-empty-files evasion
    #     reconstituted at content level, and a direct YES to "can this audit
    #     ever report a different repo than --require named". Every content
    #     test 7e-7g makes still passes here, because they interrogate the repo
    #     at the far end of the pointer rather than this directory.
    cron_repo lender
    mkdir -p "$root/borrowed"
    printf 'gitdir: %s/lender/.git\n' "$root" > "$root/borrowed/.git"
    assert_cron borrowed 1 "is not this repository" \
        "cron coverage: a gitlink borrowed from another repo is not this repo"

    # 7j. The same class of gap with NOTHING hand-written: an ordinary clone of
    #     a repo that really does have crons, sparse-checked-out so the tree
    #     lacks them. Measured on the shipped code against two clones of THIS
    #     repo at the SAME commit — sparse said "SKIP … nothing to watch",
    #     exit 0; full said FAIL, exit 1. `git rev-parse --git-dir` blesses
    #     both, so no amount of git-repo-ness closes it: the verdict flipped
    #     red -> green purely on which files were materialized. The index does
    #     close it, because it still lists the paths the checkout skipped.
    cron_workload realsrc
    mkdir -p "$root/realsrc/scripts"; echo keep > "$root/realsrc/scripts/keep.sh"
    cron_commit realsrc
    git clone -q --no-checkout "$root/realsrc" "$root/sparsetree"
    git -C "$root/sparsetree" sparse-checkout init --cone >/dev/null
    git -C "$root/sparsetree" sparse-checkout set scripts >/dev/null
    git -C "$root/sparsetree" checkout -q
    assert_cron sparsetree 1 "did not materialize" \
        "cron coverage: a checkout that omitted .github/workflows is not audited"

    # 7k. With no files materialized AT ALL: `git worktree add --no-checkout`
    #     yields a directory whose only entry is a 61-byte `.git`. Measured on
    #     the shipped code through the ARGUMENT-LESS cwd form ci.yml actually
    #     runs: "no .github/workflows — nothing to watch", exit 0. That git dir
    #     has no `index`, so nothing on disk says the checkout ever happened —
    #     which has to be a loud refusal, never a silent skip.
    cron_repo nocosrc
    git -C "$root/nocosrc" worktree add -q --no-checkout --detach "$root/nocheckout"
    assert_cron nocheckout 1 "no readable git index" \
        "cron coverage: a worktree with no checkout at all is not audited"

    # 7l. One level below 7j: the DIRECTORY is materialized but a file inside it
    #     is not. Measured on the shipped code — a --no-cone sparse checkout
    #     that took .github/workflows/ci.yml and left nightly.yml behind scored
    #     "SKIP … no scheduled workflows", exit 0, because the repo's only cron
    #     simply was not on disk. Pins that the index is compared FILE BY FILE,
    #     not merely asked whether the directory ought to exist.
    cron_workload partialsrc
    cat > "$root/partialsrc/.github/workflows/ci.yml" <<'EOF'
name: CI
on:
  pull_request:
jobs:
  t:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
EOF
    cron_commit partialsrc
    git clone -q --no-checkout "$root/partialsrc" "$root/partialtree"
    git -C "$root/partialtree" sparse-checkout init --no-cone >/dev/null
    printf '/.github/workflows/ci.yml\n' > "$root/partialtree/.git/info/sparse-checkout"
    git -C "$root/partialtree" checkout -q
    assert_cron partialtree 1 "did not materialize" \
        "cron coverage: a checkout missing one tracked workflow file is not audited"

    # 7m. The same omission carried by an index of VERSION 4, which prefix-
    #     compresses every path against the previous one. The obvious way to
    #     implement 7j — scanning the raw index file for the substring
    #     ".github/workflows/" — reads FALSE here even though the path is
    #     tracked (measured 2026-08-17 with `git update-index --index-version
    #     4`), so that shortcut fails OPEN and waves this straight through.
    #     Pins that the index is PARSED, which is also why v2/v3/v4 are handled
    #     and any other version is refused rather than guessed at.
    #     `.gitattributes` is LOAD-BEARING, not scenery: v4 compresses a path
    #     only against the previous SORTED entry, so with the workflow first in
    #     the index it is stored whole and the substring is present after all.
    #     Measured 2026-08-17 — without this sibling the fixture reads
    #     SUBSTRING_PRESENT=true and a substring implementation passes it,
    #     which is exactly what the mutation proof caught on the first draft.
    cron_workload v4src
    mkdir -p "$root/v4src/scripts"; echo keep > "$root/v4src/scripts/keep.sh"
    echo "* text=auto" > "$root/v4src/.gitattributes"
    cron_commit v4src
    git clone -q --no-checkout "$root/v4src" "$root/v4tree"
    git -C "$root/v4tree" sparse-checkout init --cone >/dev/null
    git -C "$root/v4tree" sparse-checkout set scripts >/dev/null
    git -C "$root/v4tree" checkout -q
    git -C "$root/v4tree" update-index --index-version 4
    assert_cron v4tree 1 "did not materialize" \
        "cron coverage: a version-4 (prefix-compressed) index is parsed, not scanned"

    # 7n. The OTHER return leg git writes, and the second positive case. A
    #     SUBMODULE's `.git` is a gitlink too — the very shape 7i rejects — but
    #     its git dir claims this directory back through `core.worktree` in its
    #     config rather than through a `gitdir` file (measured on a real one:
    #     `worktree = ../../../sub`, relative to the git dir). Without this
    #     assertion the identity check would look correct while rejecting every
    #     submodule, the same way demanding a `.git` DIRECTORY would look
    #     correct while breaking every worktree.
    cron_repo subparent
    cron_repo subchild
    git -C "$root/subparent" -c protocol.file.allow=always \
        -c user.name=t -c user.email=t@example.com submodule add -q "$root/subchild" sub
    assert_cron subparent/sub 0 "no .github/workflows" \
        "cron coverage: a submodule identifies itself through core.worktree"

    # 8. Malformed YAML must be its OWN labelled outcome. Left uncaught it
    #    exits 1 with a parser stack trace that reads exactly like an
    #    uncovered repo, making a broken workflow and a missing audit
    #    indistinguishable.
    cron_workload badyaml
    printf 'name: Broken\non:\n  push:\njobs:\n  a:\n   - [unclosed\n' \
        > "$root/badyaml/.github/workflows/broken.yml"
    assert_cron badyaml 1 "unparseable YAML" \
        "cron coverage: malformed YAML reports as a parse error, not a coverage verdict"

    # 9. The no-args cwd form, both directions. This is the path ci.yml runs
    #    and the only one available to a runner (a runner has exactly ONE repo
    #    checked out), yet cases 1-8 all drive --repos-root/--require — so a
    #    broken cwd default would ship green behind a fully green suite.
    #    Reuses the fixtures built above rather than making new ones.
    assert_cron_cwd covered 0 "OK    covered:" \
        "cron coverage: no-args cwd mode passes on a covered repo"
    assert_cron_cwd uncovered 1 "no scheduled-run-health caller" \
        "cron coverage: no-args cwd mode fails on an uncovered repo"

    # 10. TWO callers in one repo: the verdict must be a property of the SET,
    #     not of which filename sorts last. A single overwritten `caller`
    #     variable made the last file win — measured 2026-08-17, one good caller
    #     plus one dispatch-only manual caller scored FAIL as `zzz-manual.yml`
    #     and OK as `aaa-manual.yml`: same repo, same coverage, opposite exit.
    #     Both halves are pinned, because order-independence must not be bought
    #     by dropping a detection:
    #       - a dispatch-only MANUAL caller is legitimate (it fires only when a
    #         human runs it, so it is not a silent failure) → OK either way;
    #       - a SCHEDULED caller missing `issues: write` 403s nightly, which is
    #         the exact failure this gate exists to prevent → FAIL either way,
    #         including when a working caller sorts after it (where the old
    #         last-wins rule scored the repo OK and missed it).
    cron_two_callers() {   # <fixture> <good-file> <second-file> manual|broken
        cron_workload "$1"
        cp "$caller" "$root/$1/.github/workflows/$2"
        if [[ "$4" == manual ]]; then
            sed '/^  schedule:$/,/^  workflow_dispatch:$/{/^  workflow_dispatch:$/!d;}' \
                "$caller" > "$root/$1/.github/workflows/$3"
        else
            sed '/^  issues: write$/d' "$caller" > "$root/$1/.github/workflows/$3"
        fi
    }

    cron_two_callers manuallast aaa-health.yml zzz-manual.yml manual
    cron_two_callers manualfirst zzz-health.yml aaa-manual.yml manual
    assert_cron manuallast 0 "watched by aaa-health.yml" \
        "cron coverage: a manual-only caller sorting LAST does not unseat a good one"
    assert_cron manualfirst 0 "watched by zzz-health.yml" \
        "cron coverage: a manual-only caller sorting FIRST does not unseat a good one"

    cron_two_callers brokenlast aaa-health.yml zzz-broken.yml broken
    cron_two_callers brokenfirst zzz-health.yml aaa-broken.yml broken
    assert_cron brokenlast 1 "zzz-broken.yml job 'audit' lacks permissions" \
        "cron coverage: a scheduled 403ing caller fails the repo, sorting LAST"
    assert_cron brokenfirst 1 "aaa-broken.yml job 'audit' lacks permissions" \
        "cron coverage: a scheduled 403ing caller fails the repo, sorting FIRST"

    # ── 9. WHICH repos the audit is answering about ───────────────────────
    #
    # Everything above asks whether a named directory is covered. These ask
    # which directories had to be named, which is the half issue #37 found
    # missing: with --require supplying the only list, a disk holding 14 of 25
    # repos audited 14 and printed nothing at all about the other 11 — its
    # clean line and a complete one are the same bytes. The fleet now comes
    # from repos.yml, and a name that does not resolve is an ERROR by the same
    # rule the rest of this file enforces: absence is never a pass.

    # <repos-yml> <root> <expected-exit> <expected-substring> <label>
    assert_fleet() {
        local out rc=0
        out=$(node "$script" --repos-root "$2" --repos-yml "$1" 2>&1) || rc=$?
        if [[ "$rc" == "$3" ]] && grep -qF -- "$4" <<<"$out"; then
            pass "$5"
        else
            fail "$5 — expected exit $3 containing '$4'; got exit $rc: $(echo "$out" | head -2 | tr '\n' ' ')"
        fi
    }

    # Writes a repos.yml whose cron_coverage body is the remaining args, ONE
    # PER LINE; $1 names the file. `printf '%s\n' "$@"` rather than "$*": the
    # latter joins with a space, which folded a two-key fixture onto one line
    # and turned an overlap assertion into a YAML parse error that still
    # exited 2 — the right code for the wrong reason.
    fleet_yml() {
        local file="$root/$1"; shift
        { echo 'exclude: []'; echo 'cron_coverage:'; printf '%s\n' "$@"; } > "$file"
    }

    # 9a. The declared fleet is what gets audited — both fixtures, no --require.
    fleet_yml fleet-ok.yml "  fleet: [covered, uncovered]"
    assert_fleet "$root/fleet-ok.yml" "$root" 1 "OK    covered:" \
        "cron coverage: the declared fleet is audited without --require"
    assert_fleet "$root/fleet-ok.yml" "$root" 1 "FAIL  uncovered:" \
        "cron coverage: every declared repo is audited, not just the first"

    # 9b. THE #37 FIX. A repo the list names and the disk lacks is a FINDING.
    # Before this, the same run simply had one fewer line and still said
    # "All audited repos covered".
    fleet_yml fleet-absent.yml "  fleet: [covered, never-cloned]"
    assert_fleet "$root/fleet-absent.yml" "$root" 1 "no such directory" \
        "cron coverage: a fleet repo missing from the disk is an error, not a silence"

    # 9c-9f. A registry that cannot decide the question is a loud STOP (exit 2),
    # never a green run over whatever it managed to read. An empty fleet is the
    # vacuous-pass shape specifically: zero audited, zero failures, exit 0.
    assert_fleet "$root/no-such-repos.yml" "$root" 2 "cannot read the fleet list" \
        "cron coverage: an unreadable repos.yml stops the run"
    printf 'exclude: []\n' > "$root/fleet-none.yml"
    assert_fleet "$root/fleet-none.yml" "$root" 2 "no cron_coverage: block" \
        "cron coverage: a repos.yml with no cron_coverage block stops the run"
    fleet_yml fleet-empty.yml "  fleet: []"
    assert_fleet "$root/fleet-empty.yml" "$root" 2 "lists no cron_coverage.fleet repos" \
        "cron coverage: an empty fleet stops the run rather than passing vacuously"
    fleet_yml fleet-both.yml "  fleet: [covered]" "  out_of_scope: [covered]"
    assert_fleet "$root/fleet-both.yml" "$root" 2 "as both cron_coverage.fleet and out_of_scope" \
        "cron coverage: a repo in both keys is a contradiction, not a default"

    # 9g. Names, not paths: the entries are joined onto --repos-root, so a
    # traversal component would silently audit somewhere else entirely.
    fleet_yml fleet-path.yml "  fleet: ['../elsewhere']"
    assert_fleet "$root/fleet-path.yml" "$root" 2 "is not a repo name" \
        "cron coverage: a fleet entry containing a path separator is rejected"

    # 9h. --require alone has no root to resolve against; it used to be half of
    # a pair, and the pairing is what this preserves now that the other half
    # has a default.
    local out rc=0
    out=$(node "$script" --require covered 2>&1) || rc=$?
    if [[ "$rc" == 2 ]] && grep -qF "needs --repos-root" <<<"$out"; then
        pass "cron coverage: --require without --repos-root is rejected"
    else
        fail "cron coverage: --require without --repos-root is rejected — got exit $rc"
    fi

    # 9i. The REAL repos.yml, not a fixture. Nothing else exercises it: ci.yml
    # runs the cwd form, which never reads the file at all, so a fleet key
    # deleted, emptied or double-listed would ship green. Pointed at an empty
    # directory, a well-formed registry must get as far as looking for its
    # first repo (exit 1, "no such directory"); any registry-level defect stops
    # at exit 2 before that.
    mkdir -p "$root/empty-disk"
    assert_fleet "$REPO_ROOT/repos.yml" "$root/empty-disk" 1 "no such directory" \
        "cron coverage: this repo's own repos.yml declares a usable fleet"
}

# ── Test 7: this repo's own committed configuration ───────────────────────
#
# ── Test 8: bump-consumer-locks.sh ────────────────────────────────────────
#
# The fleet half of the lock re-pinner: which repos need one, and what happens
# to the ones that do not. Three properties carry the lane, and each has its
# own fixture rather than a flag:
#
#   * ANTI-CHURN. `repo-current` pins a commit that is BEHIND the registry's
#     HEAD at content identical to it. A re-pinner keyed on "is the ref the
#     newest commit" opens a PR here every night forever, and a fleet that
#     learns to ignore these PRs is worse than no re-pinner. It must produce
#     nothing at all.
#   * NON-DE-FEDERATION. `repo-federated`'s `sources` array must come through
#     a bump unchanged. That is ADR 0001's named trap, at fleet scale.
#   * ISOLATION. `repo-error` cannot be assessed at all, and sorts third of
#     ten: `set -euo pipefail` plus a loop is how sync.sh nearly died at the
#     first gitignored repo, silently leaving every repo after it unsynced.
#     The repos after it are the assertion.
#   * FAILING CLOSED ON THE WRITE SIDE. Three things this script can do are
#     irreversible or fleet-wide, so each has a fixture that reaches it: a
#     mistyped `--dry-run` must not turn into a live run, an already-open bump
#     branch carrying someone else's commit must not be force-pushed over, and
#     a push a ruleset refuses must be a counted failure rather than a skip
#     with a remedy that cannot be acted on.
#
# Deterministic and offline like the rest of the suite: mock `gh`, local bare
# repos, a stand-in generator, no network, no sleeps, no wall-clock.

BUMP_EXIT=0
BUMP_PR_LOG="$TEST_DIR/bump-pr.log"
BUMP_PR_BODY_DIR="$TEST_DIR/bump-pr-bodies"
# Created empty here so every later `wc -l`/`cat` on it is total. The mock
# only creates it once some run opens a PR, so any regression that stops this
# lane opening one used to make a bare `wc -l < "$BUMP_PR_LOG"` abort the
# WHOLE suite under `set -euo pipefail`: no Results line, and the six tests
# after it — including the only checks on this repo's own workflows — never
# run. That is the isolation failure this lane exists to assert, in the
# harness that asserts it.
: > "$BUMP_PR_LOG"

run_bump() {   # <output file> [script args...]
    local out="$1"; shift
    BUMP_EXIT=0
    GITHUB_REPOSITORY_OWNER=bumporg \
    SYNC_OWNERS="${BUMP_OWNERS_FOR_RUN:-}" \
    MOCK_BARE_DIR="${BUMP_BARE_DIR_FOR_RUN:-$TEST_DIR/bare}" \
    MOCK_PR_LOG="$BUMP_PR_LOG" \
    MOCK_PR_BODY_DIR="$BUMP_PR_BODY_DIR" \
    MOCK_OPEN_PR_REPOS="${MOCK_OPEN_PR_REPOS:-}" \
    MOCK_PR_DIR="${BUMP_PR_DIR_FOR_RUN:-}" \
    MOCK_PR_MERGE_FAIL="${BUMP_MERGE_FAIL_FOR_RUN:-}" \
    MOCK_AUTO_MERGE_FAILS="${BUMP_AUTO_MERGE_FAILS_FOR_RUN:-}" \
    MOCK_PR_HEAD_MOVES="${BUMP_HEAD_MOVES_FOR_RUN:-}" \
    MOCK_PR_HEAD_GARBLED="${BUMP_HEAD_GARBLED_FOR_RUN:-}" \
    MOCK_GH_NO_MATCH_FLAG="${BUMP_NO_MATCH_FLAG_FOR_RUN:-}" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    BUMP_REGISTRY="bumporg/agentskills" \
    BUMP_CHECKOUTS="${BUMP_CHECKOUTS_FOR_RUN:-$BUMP_CHECKOUTS_ARG}" \
    BUMP_GENERATOR="${BUMP_GENERATOR_FOR_RUN:-}" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/bump-consumer-locks.sh" "$@" > "$out" 2>&1 || BUMP_EXIT=$?
}

bump_branch_sha() {   # <short repo name> — the bump branch's sha, or "" if none
    git -C "$TEST_DIR/bare/bumporg_$1" rev-parse --verify -q \
        "refs/heads/skills-lock-bump/update" 2>/dev/null || true
}

bump_lock_at() {   # <short repo name> <ref> — that ref's skills.lock
    git -C "$TEST_DIR/bare/bumporg_$1" show "$2:skills.lock" 2>/dev/null || true
}

lock_field_of() {   # <file> <top-level key>
    python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle).get(sys.argv[2], ""))
' "$1" "$2"
}

lock_skill_digest() {   # <file> <skill key>
    python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print((json.load(handle).get("skills") or {}).get(sys.argv[2], ""))
' "$1" "$2"
}

# The `sources` array as the generator would serialize it. json.loads preserves
# key order, so re-serializing both sides with identical settings compares the
# array's BYTES — a reordered key, a changed ref or a dropped source all show
# up — without depending on where the array happens to sit in the file.
lock_sources_json() {   # <file>
    python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.dumps(json.load(handle).get("sources"), indent=2, ensure_ascii=False))
' "$1"
}

assert_no_bump_branch() {   # <short repo name> <label>
    if [[ -z "$(bump_branch_sha "$1")" ]]; then pass "$2"; else fail "$2 — a bump branch was pushed to bumporg/$1"; fi
}

# ── Test 8a: --dry-run decides everything and writes nothing ──────────────

test_bump_dry_run() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh --dry-run ==="

    run_bump "$TEST_DIR/bump-dry.txt" --dry-run

    assert_contains "$TEST_DIR/bump-dry.txt" "[DRY RUN] Would re-pin skills.lock" "dry-run: names the re-pin it would make"
    assert_contains "$TEST_DIR/bump-dry.txt" "bundle content unchanged since ${BUMP_REF_CONTENT:0:7}" "dry-run: still reports the repo that needs nothing"
    assert_contains "$TEST_DIR/bump-dry.txt" "0 proposed" "dry-run: proposes nothing"

    # A repo that cannot be ASSESSED is a failure in a dry run too: the
    # decision is what a dry run exercises, and this one could not be made.
    if [[ $BUMP_EXIT -ne 0 ]]; then
        pass "dry-run: exits non-zero for the repo it could not assess"
    else
        fail "dry-run: exits non-zero for the repo it could not assess (got 0)"
    fi

    local r
    for r in repo-stale repo-federated repo-current repo-error agentskills \
             repo-fed-current repo-inverted; do
        assert_no_bump_branch "$r" "dry-run: nothing pushed to bumporg/$r"
    done
    if [[ ! -s "$BUMP_PR_LOG" ]]; then
        pass "dry-run: no pull request opened"
    else
        fail "dry-run: no pull request opened — $(cat "$BUMP_PR_LOG")"
    fi
}

# ── Test 8a2: a federated source with no checkout is skipped, not halved ──
#
# --repin advances the primary ref only, but --check-current and every digest
# are read from git, so a source whose clone is absent leaves the question
# unanswerable for that consumer. The dangerous answer is the plausible one:
# re-pin the half we CAN read and write the other half from whatever is in the
# file. That is ADR 0001's de-federation damage arriving by omission rather
# than by a flag. Run as a dry run so the assertion holds wherever this sits
# in the lane's fixed order.
test_bump_missing_source_checkout() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a federated source with no checkout) ==="

    BUMP_CHECKOUTS_FOR_RUN="bumporg/agentskills=$TEST_DIR/registry" \
        run_bump "$TEST_DIR/bump-nosource.txt" --dry-run
    unset BUMP_CHECKOUTS_FOR_RUN

    assert_contains "$TEST_DIR/bump-nosource.txt" "no checkout configured for bumporg/cms-platform" "missing source: names the registry it cannot read"
    assert_contains "$TEST_DIR/bump-nosource.txt" "skipping bumporg/repo-federated rather than re-pinning part of its lock" "missing source: the federated consumer is skipped whole"
    # The skip is that consumer's, not the run's: a single-source consumer in
    # the same run is unaffected.
    assert_contains "$TEST_DIR/bump-nosource.txt" "[DRY RUN] Would re-pin" "missing source: the single-source consumer is still assessed"
    assert_no_bump_branch repo-federated "missing source: nothing pushed to the federated consumer"
}

# ── Test 8b: the bump itself ──────────────────────────────────────────────

test_bump_consumer_locks() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (re-pin the stale, leave the rest) ==="

    local diverged_before
    diverged_before=$(bump_branch_sha repo-diverged)

    run_bump "$TEST_DIR/bump.txt"
    local log="$TEST_DIR/bump.txt"

    # ── The registry itself owns its own re-pin.
    assert_contains "$log" "the registry itself" "registry: excluded with a stated reason"
    assert_no_bump_branch agentskills "registry: never bumped, even carrying a stale lock of its own"

    # ── The anti-churn case: an old ref, an unmoved bundle, no PR.
    assert_contains "$log" "bundle content unchanged since ${BUMP_REF_CONTENT:0:7}" "unchanged: reported as needing no re-pin"
    assert_no_bump_branch repo-current "unchanged: no branch pushed"
    local current_now="$TEST_DIR/bump-current-now.lock"
    bump_lock_at repo-current main > "$current_now"
    if cmp -s "$current_now" "$TEST_DIR/repo-current.lock.orig"; then
        pass "unchanged: skills.lock byte-identical on main"
    else
        fail "unchanged: skills.lock byte-identical on main"
    fi

    # ── The repo that could not be assessed: a failure, never a re-pin.
    assert_contains "$log" "could not decide whether skills.lock is current" "unassessable: reported as a failure, not as drift"
    assert_no_bump_branch repo-error "unassessable: no branch pushed on the strength of an error"

    # ── Isolation: repo-error sorts THIRD of seven.
    assert_contains "$log" "=== bumporg/repo-no-lock ===" "isolation: the run survives a per-repo failure"
    assert_contains "$log" "=== bumporg/repo-stale ===" "isolation: the last repo is still reached"
    assert_contains "$log" "1 failed" "isolation: the failure is counted, not swallowed"
    if [[ $BUMP_EXIT -ne 0 ]]; then
        pass "isolation: the run exits non-zero"
    else
        fail "isolation: the run exits non-zero (got 0)"
    fi

    # ── The two skips that are not faults.
    assert_contains "$log" "no skills.lock — nothing to re-pin" "no lock: skipped"
    assert_no_bump_branch repo-no-lock "no lock: nothing created"
    assert_contains "$log" "never names bumporg/agentskills — skipping" "other registry: skipped"
    assert_no_bump_branch repo-other-registry "other registry: nothing pushed"

    # ── ANTI-CHURN, federated edition. --check-current reports ONE verdict for
    # every source a lock names, while --repin advances only the primary. Gate
    # on the combined answer and this repo — primary current, federated source
    # behind its registry's HEAD — gets a pull request for EVERY commit to the
    # primary registry, forever, with not one digest in the diff.
    assert_contains "$log" "a FEDERATED source has moved on since the ref this lock pins for it" "federated-current: the moved federated half is reported, not acted on"
    assert_contains "$log" "bundle content unchanged since ${BUMP_REF_CONTENT:0:7}" "federated-current: the primary is judged on its own content"
    assert_no_bump_branch repo-fed-current "federated-current: no branch — a federated advance is not a primary re-pin"

    # ── The federation inverted: everything downstream targets the PRIMARY,
    # so a lock that only federates this registry must be left alone rather
    # than have some other registry's pin advanced under this one's name.
    assert_contains "$log" "federates bumporg/agentskills but pins bumporg/cms-platform as its primary" "inverted: skipped, with the reason"
    assert_no_bump_branch repo-inverted "inverted: no other registry's pin was advanced"

    # ── A bump branch that already carries someone else's commit.
    assert_contains "$log" "refusing to force-push" "diverged: an open bump branch with other content is refused"
    if [[ -n "$diverged_before" && "$(bump_branch_sha repo-diverged)" == "$diverged_before" ]]; then
        pass "diverged: the reviewer's commit is still the branch tip"
    else
        fail "diverged: the reviewer's commit is still the branch tip — was '$diverged_before', now '$(bump_branch_sha repo-diverged)'"
    fi

    # ── The stale consumer: ref advanced, digests re-derived.
    assert_contains "$log" "2 proposed" "bump: exactly the two stale consumers were proposed"
    local stale_new="$TEST_DIR/bump-stale-new.lock"
    bump_lock_at repo-stale "refs/heads/skills-lock-bump/update" > "$stale_new"
    if [[ -s "$stale_new" ]]; then
        pass "bump: repo-stale has a bump branch carrying a lock"
    else
        fail "bump: repo-stale has a bump branch carrying a lock"
        return
    fi
    if [[ "$(lock_field_of "$stale_new" ref)" == "$BUMP_REF_HEAD" ]]; then
        pass "bump: ref advanced to the registry's current commit"
    else
        fail "bump: ref advanced to the registry's current commit — got $(lock_field_of "$stale_new" ref)"
    fi
    local stale_old="$TEST_DIR/bump-stale-old.lock"
    bump_lock_at repo-stale main > "$stale_old"
    if [[ "$(lock_skill_digest "$stale_new" adam/finding-unknowns)" \
          != "$(lock_skill_digest "$stale_old" adam/finding-unknowns)" ]]; then
        pass "bump: the changed skill's digest was re-derived"
    else
        fail "bump: the changed skill's digest was re-derived (it is unchanged)"
    fi
    if [[ "$(lock_skill_digest "$stale_new" adam/writing-adrs)" \
          == "$(lock_skill_digest "$stale_old" adam/writing-adrs)" ]]; then
        pass "bump: the untouched skill's digest is unchanged"
    else
        fail "bump: the untouched skill's digest is unchanged"
    fi
    if cmp -s "$stale_old" "$TEST_DIR/repo-stale.lock.orig"; then
        pass "bump: the default branch is untouched — the change arrives as a PR"
    else
        fail "bump: the default branch is untouched — the change arrives as a PR"
    fi

    # ── THE non-negotiable: a federated lock keeps its other registry.
    # Compared against the array the FIXTURE composed, not against the seeded
    # lock: `seed_bump_lock` fills a fixture lock by running the generator's
    # own --repin, so comparing one generator output with another is a
    # tautology — a generator that drops `sources` drops it from both sides
    # and this assertion still reports PASS.
    local fed_new="$TEST_DIR/bump-fed-new.lock"
    bump_lock_at repo-federated "refs/heads/skills-lock-bump/update" > "$fed_new"
    if [[ "$(lock_sources_json "$fed_new")" == "$(cat "$TEST_DIR/repo-federated.sources.expected")" ]]; then
        pass "federated: the sources array survives a bump byte-for-byte"
    else
        fail "federated: the sources array survives a bump byte-for-byte — got $(lock_sources_json "$fed_new")"
    fi
    assert_contains "$fed_new" "bumporg/cms-platform" "federated: the second registry is still named"
    assert_contains "$fed_new" "$BUMP_SRC_REF" "federated: the second registry's own pin did not move"
    # The negative half. Its registry HAS moved on (two commits), so a re-pin
    # that re-resolved each sources[].ref to that source's HEAD would be the
    # de-federation-by-currency bug — and would be indistinguishable from
    # "unchanged" if the fixture had a single commit.
    assert_not_contains "$fed_new" "$BUMP_SRC_HEAD" "federated: the federated pin was not re-resolved to its registry's HEAD"
    if [[ "$(lock_field_of "$fed_new" ref)" == "$BUMP_REF_HEAD" ]]; then
        pass "federated: the primary ref still advanced"
    else
        fail "federated: the primary ref still advanced"
    fi

    # ── What the PR discloses.
    local body="$BUMP_PR_BODY_DIR/bumporg_repo-stale.body"
    # The header of a CONTENT re-pin, which is the paired control for the
    # format gate's assertion that a FORMAT re-pin carries no such line: drop
    # the header entirely and that one still passes, so only this one notices.
    assert_contains "$body" "**What moved:**" \
        "PR body: a content re-pin is headed by what moved"
    # The PAIRED CONTROLS for the format gate's two "this PR does not announce
    # a move" assertions. Each of those is satisfiable by deleting the
    # announcement from BOTH branches, so the content side has to insist the
    # announcement is still there — in the title a PR list shows, and in the
    # commit message that outlives the PR.
    local stale_title="$BUMP_PR_BODY_DIR/bumporg_repo-stale.title"
    local moved_onto="re-pin skills.lock onto bumporg/agentskills@${BUMP_REF_HEAD:0:7}"
    assert_contains "$stale_title" "$moved_onto" \
        "PR title: a content re-pin still announces the commit it moved onto"
    local stale_msg="$TEST_DIR/stale-commit-msg.txt"
    git -C "$TEST_DIR/bare/bumporg_repo-stale" log -1 --format=%B \
        refs/heads/skills-lock-bump/update > "$stale_msg" 2>/dev/null || : > "$stale_msg"
    assert_contains "$stale_msg" "has moved since ${BUMP_REF_OLD:0:7}" \
        "commit message: a content re-pin still says the bundle moved, and since when"
    assert_contains "$body" "${BUMP_REF_OLD:0:7}" "PR body: names the ref it moved from"
    assert_contains "$body" "${BUMP_REF_HEAD:0:7}" "PR body: names the ref it moved to"
    assert_contains "$body" "re-derived from the newly pinned commit" "PR body: says where the digests come from"
    assert_contains "$body" "re-resolves only \`ref\`" \
        "PR body: a content re-pin still says the ref was re-resolved"
    assert_contains "$body" "Generated, never hand-edited" "PR body: says the change is generator output"
    assert_contains "$body" "This lock has no federated sources." "PR body: says so when there is no federated half"
    assert_contains "$body" "This pull request merges itself" "PR body: discloses that no reviewer has to click merge"
    assert_contains "$log" "native auto-merge armed" "auto-merge: requested on every PR this run opened"
    assert_contains "$body" "which skills.lock still pins" "PR body: quotes the consumer's own lock path, not this run's temp copy"
    assert_not_contains "$body" "$TEST_DIR" "PR body: no path from the machine that ran the bump"
    local fed_body="$BUMP_PR_BODY_DIR/bumporg_repo-federated.body"
    assert_contains "$fed_body" "Federated sources keep their pins" "PR body: discloses that the federated half is untouched"
    assert_contains "$fed_body" "bumporg/cms-platform@${BUMP_SRC_REF:0:7}" "PR body: names the federated pin that did not move"
    # The quoted verdict is the primary-scoped one, so every difference line in
    # it belongs to the ref this PR advances. A combined verdict would blame a
    # cms-platform skill for an agentskills re-pin.
    assert_contains "$fed_body" "adam/finding-unknowns" "PR body: quotes the primary difference that caused this PR"
    assert_not_contains "$fed_body" "cms-platform/deploy-site" "PR body: does not blame a federated skill for a primary re-pin"
}

# ── Test 8c: a re-run proposes nothing, and repairs an interrupted one ────

test_bump_idempotent() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (re-run) ==="

    local stale_before fed_before prs_before
    stale_before=$(bump_branch_sha repo-stale)
    fed_before=$(bump_branch_sha repo-federated)
    prs_before=$(wc -l < "$BUMP_PR_LOG")

    # The steady state after a bump: branch pushed, PR open, and main still
    # carrying the stale lock until someone merges it. The mock has no memory,
    # so the open PRs are supplied here.
    MOCK_OPEN_PR_REPOS="bumporg_repo-stale bumporg_repo-federated" \
        run_bump "$TEST_DIR/bump-rerun.txt"

    assert_contains "$TEST_DIR/bump-rerun.txt" "0 proposed" "re-run: nothing proposed"
    assert_contains "$TEST_DIR/bump-rerun.txt" "is already open for this branch" "re-run: the open PR is left alone"
    if [[ "$(bump_branch_sha repo-stale)" == "$stale_before" \
          && "$(bump_branch_sha repo-federated)" == "$fed_before" ]]; then
        pass "re-run: no second commit — both bump branches are untouched"
    else
        fail "re-run: no second commit — both bump branches are untouched"
    fi
    if [[ "$(wc -l < "$BUMP_PR_LOG")" == "$prs_before" ]]; then
        pass "re-run: no second pull request"
    else
        fail "re-run: no second pull request — $(cat "$BUMP_PR_LOG")"
    fi

    # An interrupted run leaves a correct branch and no PR (the workflow can
    # be cancelled between the push and `gh pr create`). Returning early on a
    # matching branch would strand it forever, because every later run finds
    # the same match. The repair opens the PR and touches nothing else.
    run_bump "$TEST_DIR/bump-strand.txt"
    assert_contains "$TEST_DIR/bump-strand.txt" "not pushing again" "stranded: the matching branch is not re-pushed"
    assert_contains "$TEST_DIR/bump-strand.txt" "PR created" "stranded: the missing PR is opened"
    if [[ "$(bump_branch_sha repo-stale)" == "$stale_before" \
          && "$(bump_branch_sha repo-federated)" == "$fed_before" ]]; then
        pass "stranded: still no second commit"
    else
        fail "stranded: still no second commit"
    fi
}

# ── Test 8d: a shallow registry checkout is named, not mistaken for drift ─

test_bump_shallow_registry() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (shallow registry checkout) ==="

    rm -rf "$TEST_DIR/registry-shallow"
    git clone --depth 1 "file://$TEST_DIR/registry" "$TEST_DIR/registry-shallow" >/dev/null 2>&1

    BUMP_CHECKOUTS_FOR_RUN="bumporg/agentskills=$TEST_DIR/registry-shallow bumporg/cms-platform=$TEST_DIR/cms-platform" \
        run_bump "$TEST_DIR/bump-shallow.txt"
    unset BUMP_CHECKOUTS_FOR_RUN

    # Every consumer would otherwise fail with "this checkout is not that
    # registry" — the --repin probe looks for the commit the lock already
    # pins, which a shallow clone does not have — and send a reader hunting a
    # wrong registry rather than a missing `fetch-depth: 0`.
    assert_contains "$TEST_DIR/bump-shallow.txt" "SHALLOW clone" "shallow: says the checkout is shallow"
    assert_contains "$TEST_DIR/bump-shallow.txt" "fetch-depth: 0" "shallow: names the remedy"
    if [[ $BUMP_EXIT -eq 2 ]]; then
        pass "shallow: refuses the run outright rather than skipping every repo"
    else
        fail "shallow: refuses the run outright rather than skipping every repo (exit $BUMP_EXIT)"
    fi
}

# ── Test 8e: an unrecognised argument stops the run ───────────────────────
#
# `--dry-run` is the flag that means "write nothing", and it used to be
# sniffed rather than parsed: anything that was not exactly `--dry-run` in
# exactly first position left DRY_RUN=false and the run cloned, committed,
# pushed and opened pull requests. In CI this script holds installation tokens
# with write scope across two owners, so a one-character typo is the wrong
# place for a default. Runs first in the lane, while no bump branch exists
# anywhere, so "nothing was pushed" is a statement about this run.
test_bump_unknown_argument() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (an unrecognised argument) ==="

    local prs_before
    prs_before=$(wc -l < "$BUMP_PR_LOG")
    run_bump "$TEST_DIR/bump-badarg.txt" --dry-runn

    assert_contains "$TEST_DIR/bump-badarg.txt" "unknown argument '--dry-runn'" "bad argument: named rather than ignored"
    if [[ $BUMP_EXIT -eq 2 ]]; then
        pass "bad argument: refuses the run (exit 2)"
    else
        fail "bad argument: refuses the run (exit 2) — got $BUMP_EXIT"
    fi
    local r
    for r in repo-stale repo-federated; do
        assert_no_bump_branch "$r" "bad argument: nothing pushed to bumporg/$r"
    done
    if [[ "$(wc -l < "$BUMP_PR_LOG")" == "$prs_before" ]]; then
        pass "bad argument: no pull request opened"
    else
        fail "bad argument: no pull request opened — $(cat "$BUMP_PR_LOG")"
    fi
}

# ── Test 8f: a generator without --repin is named once, up front ──────────
#
# BUMP_GENERATOR defaults to the copy inside the registry checkout, and that
# checkout is of the registry's DEFAULT BRANCH — so a run can meet a generator
# that predates the flag this script cannot reimplement. Unprobed, the
# shortfall surfaces only once some consumer is genuinely stale, as one
# argparse exit 2 per stale repo and a red scheduled run every night: a
# version skew that reads as a fleet-wide breakage.
test_bump_generator_without_repin() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a generator with no re-pin flag) ==="

    local gen="$TEST_DIR/generator-no-repin.py"
    cat > "$gen" <<'NOREPIN'
#!/usr/bin/env python3
"""A generator from before the advance flag existed: verify modes only."""
import argparse
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--check-current", action="store_true")
parser.add_argument("--repo")
parser.add_argument("--source-repo", action="append", default=[])
parser.add_argument("-o", "--output", required=True)
parser.parse_args()
sys.exit(0)
NOREPIN

    BUMP_GENERATOR_FOR_RUN="$gen" run_bump "$TEST_DIR/bump-norepin.txt"
    unset BUMP_GENERATOR_FOR_RUN

    assert_contains "$TEST_DIR/bump-norepin.txt" "does not support --repin" "no re-pin flag: said once, before any repo is touched"
    if [[ $BUMP_EXIT -eq 2 ]]; then
        pass "no re-pin flag: refuses the run outright"
    else
        fail "no re-pin flag: refuses the run outright (exit $BUMP_EXIT)"
    fi
    assert_not_contains "$TEST_DIR/bump-norepin.txt" "=== bumporg/repo-stale ===" "no re-pin flag: no consumer is processed at all"
}

# ── Test 8f2: one owner's listing failure is that owner's failure ─────────
#
# `gh repo list` runs under `set -euo pipefail`, and a non-zero gh used to end
# the script mid-loop. The workflow passes only GH_TOKEN_<OWNER>, so an owner
# with no installation has GH_TOKEN unset for it and gh fails "authentication
# required" — and with SYNC_OWNERS ordered "Adam-S-Daniel jodidaniel", losing
# the FIRST installation stopped the entire fleet, printing a raw gh error and
# no summary. Both the workflow's comment and its own warning say the opposite
# ("its repos will be skipped this run"). The failing owner goes first here
# because that is the ordering the workflow ships.
test_bump_owner_list_failure() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (one owner cannot be listed) ==="

    local prs_before
    prs_before=$(wc -l < "$BUMP_PR_LOG")
    mkdir -p "$TEST_DIR/bare-owner"

    BUMP_OWNERS_FOR_RUN="failorg bumporg" \
    BUMP_BARE_DIR_FOR_RUN="$TEST_DIR/bare-owner" \
        run_bump "$TEST_DIR/bump-owner.txt"
    unset BUMP_OWNERS_FOR_RUN BUMP_BARE_DIR_FOR_RUN

    assert_contains "$TEST_DIR/bump-owner.txt" "failorg: could not list repos" "owner listing: the owner that failed is named"
    assert_contains "$TEST_DIR/bump-owner.txt" "Scanning repos for: bumporg" "owner listing: the SECOND owner is still scanned"
    assert_contains "$TEST_DIR/bump-owner.txt" "Lock bump complete" "owner listing: the run still prints its summary"
    assert_contains "$TEST_DIR/bump-owner.txt" "1 failed" "owner listing: counted as a per-owner failure"
    if [[ $BUMP_EXIT -ne 0 ]]; then
        pass "owner listing: the run exits non-zero"
    else
        fail "owner listing: the run exits non-zero (got 0)"
    fi
    if [[ "$(wc -l < "$BUMP_PR_LOG")" == "$prs_before" ]]; then
        pass "owner listing: no pull request opened"
    else
        fail "owner listing: no pull request opened — $(cat "$BUMP_PR_LOG")"
    fi

    rm -rf "$TEST_DIR/bare-owner"
}

# ── Test 8g: a push a ruleset refuses is a failure, not a stale branch ─────
#
# Git prints `! [remote rejected]` for ANY server-side refusal, so classifying
# on the word "rejected" reports a restricted ref creation (GH013 — the error
# this repo's own AGENTS.md documents) as a stale bump branch. Three things go
# wrong at once: the remedy printed is "merge or close its PR", about a branch
# that does not exist; a genuinely stale consumer is never bumped; and the run
# stays green, so the scheduled-run-health issue never fires. Stands its
# fixture up in a MOCK_BARE_DIR of its own so the shared fleet is untouched.
test_bump_push_rejected() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (the push is refused by a ruleset) ==="

    local bare="$TEST_DIR/bare-blocked/bumporg_repo-blocked"
    local work="$TEST_DIR/work/bumporg-repo-blocked"
    rm -rf "$TEST_DIR/bare-blocked" "$work"
    mkdir -p "$bare" "$work"
    git init --bare --initial-branch=main "$bare" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$bare"
    echo "# repo-blocked" > README.md
    seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_OLD"
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"
    install_reject_bump_branch_hook "$bare"

    BUMP_BARE_DIR_FOR_RUN="$TEST_DIR/bare-blocked" \
        run_bump "$TEST_DIR/bump-blocked.txt"
    unset BUMP_BARE_DIR_FOR_RUN

    assert_not_contains "$TEST_DIR/bump-blocked.txt" "non-fast-forward" "push refused: not diagnosed as a stale bump branch"
    assert_contains "$TEST_DIR/bump-blocked.txt" "push failed for bumporg/repo-blocked" "push refused: reported as a push failure"
    assert_contains "$TEST_DIR/bump-blocked.txt" "1 failed" "push refused: counted as a failure, not a skip"
    if [[ $BUMP_EXIT -ne 0 ]]; then
        pass "push refused: the run exits non-zero, so a scheduled run goes red"
    else
        fail "push refused: the run exits non-zero, so a scheduled run goes red (got 0)"
    fi
    if [[ -z "$(git -C "$bare" rev-parse --verify -q refs/heads/skills-lock-bump/update 2>/dev/null || true)" ]]; then
        pass "push refused: no branch exists, so 'merge or close its PR' would name nothing"
    else
        fail "push refused: no branch exists, so 'merge or close its PR' would name nothing"
    fi

    rm -rf "$TEST_DIR/bare-blocked" "$work"
}

# ── Test 8h: a bundle that vanished is refused, not proposed to everyone ───
#
# Rename or delete a bundle directory in the registry and --repin finds no
# skills at the new HEAD, so it writes `"skills": {}` — for every consumer, in
# one run, under a PR body that still says every digest was re-derived from
# the newly pinned commit. Merging one is not a no-op: skills-bootstrap writes
# its claims stream from the routing map precisely so a bundle a lock still
# declares but has emptied REAPS its old skills. Its own registry clone and
# its own MOCK_BARE_DIR, so the shared fixtures never see the rename.
test_bump_bundle_vanished() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a bundle vanished from the registry) ==="

    rm -rf "$TEST_DIR/registry-vanished"
    # A full clone: --repin proves the checkout is the registry by finding the
    # commit the lock already pins, which a shallow one would not contain.
    git clone "file://$TEST_DIR/registry" "$TEST_DIR/registry-vanished" >/dev/null 2>&1
    cd "$TEST_DIR/registry-vanished"
    git config commit.gpgsign false
    git mv plugins/adam plugins/adam2 >/dev/null 2>&1
    git commit -m "rename the bundle directory" >/dev/null 2>&1
    cd "$REPO_ROOT"

    local bare="$TEST_DIR/bare-vanish/bumporg_repo-vanish"
    local work="$TEST_DIR/work/bumporg-repo-vanish"
    rm -rf "$TEST_DIR/bare-vanish" "$work"
    mkdir -p "$bare" "$work"
    git init --bare --initial-branch=main "$bare" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$bare"
    echo "# repo-vanish" > README.md
    seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_OLD"
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"

    BUMP_BARE_DIR_FOR_RUN="$TEST_DIR/bare-vanish" \
    BUMP_CHECKOUTS_FOR_RUN="bumporg/agentskills=$TEST_DIR/registry-vanished bumporg/cms-platform=$TEST_DIR/cms-platform" \
        run_bump "$TEST_DIR/bump-vanish.txt"
    unset BUMP_BARE_DIR_FOR_RUN BUMP_CHECKOUTS_FOR_RUN

    assert_contains "$TEST_DIR/bump-vanish.txt" "refusing to propose this re-pin" "vanished bundle: refused rather than fanned out"
    assert_contains "$TEST_DIR/bump-vanish.txt" "declares no skills at all" "vanished bundle: says what is wrong with the lock it just built"
    assert_contains "$TEST_DIR/bump-vanish.txt" "1 failed" "vanished bundle: counted, so the scheduled run goes red"
    if [[ $BUMP_EXIT -ne 0 ]]; then
        pass "vanished bundle: the run exits non-zero"
    else
        fail "vanished bundle: the run exits non-zero (got 0)"
    fi
    if [[ -z "$(git -C "$bare" rev-parse --verify -q refs/heads/skills-lock-bump/update 2>/dev/null || true)" ]]; then
        pass "vanished bundle: nothing pushed"
    else
        fail "vanished bundle: nothing pushed"
    fi

    rm -rf "$TEST_DIR/bare-vanish" "$work" "$TEST_DIR/registry-vanished"
}

# ── Test 8h1: a lock with NO digests is not a lock with BAD digests ───────
#
# The distinction this asserts is the difference between reporting a repo and
# REWRITING it, and it is carried entirely by which prefix --check-format
# prints. `FAILED:` is this script's licence to re-pin a consumer's lock;
# everything else routes to report-and-count. An empty `skills` map is not an
# answer about digest shape at all — there are no digests — so the generator
# says ERROR:, and this run must leave the lock alone.
#
# Why it needs its own test rather than riding on 8h2: the two conditions are
# one `if` apart in the generator and both exit 1, so a generator that
# collapsed them would look identical from the exit code. Reading it as
# "malformed" is not merely imprecise — it re-pins, --repin over a registry
# whose bundles vanished writes the same empty map straight back, the shrink
# guard refuses to propose it, and tomorrow night does it again. A loop with
# no exit, every step of which reports as progress.
#
# It is also the one place the stub generator's FIDELITY to the real one is
# checked. Nothing else in this repo reads --check-format's ERROR: branch, so
# without this the stub could drift back to FAILED: — as it did once — and the
# whole suite would stay green while the fleet script it exists to test took
# the opposite branch in production.
test_bump_format_gate_empty_skills() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a lock that lists no skills at all) ==="

    local root="$TEST_DIR/bare-empty-skills"
    local work="$TEST_DIR/work/bumporg-repo-empty-skills"
    local bare="$root/bumporg_repo-empty-skills"
    rm -rf "$root" "$work"
    mkdir -p "$bare" "$work"
    git init --bare --initial-branch=main "$bare" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$bare"
    echo "# repo-empty-skills" > README.md
    # BUMP_REF_CONTENT so --check-current answers OK at exit 0 and the format
    # gate is what decides this run, exactly as in 8h2.
    seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_CONTENT"
    python3 -c '
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    doc = json.load(handle)
if not doc.get("skills"):
    sys.exit("fixture: %s already lists no skills, so emptying it proves nothing" % path)
doc["skills"] = {}
with open(path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
' skills.lock
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"

    BUMP_BARE_DIR_FOR_RUN="$root" run_bump "$TEST_DIR/bump-empty-skills.txt"
    unset BUMP_BARE_DIR_FOR_RUN

    assert_contains "$TEST_DIR/bump-empty-skills.txt" \
        "could not decide whether skills.lock's digests are well-formed" \
        "empty skills: routed to report-and-count, not to the rewrite path"
    assert_contains "$TEST_DIR/bump-empty-skills.txt" "lists no skills at all" \
        "empty skills: the log quotes what the generator actually said"
    assert_contains "$TEST_DIR/bump-empty-skills.txt" "1 failed" \
        "empty skills: counted, so the scheduled run goes red"
    assert_contains "$TEST_DIR/bump-empty-skills.txt" "0 proposed" \
        "empty skills: nothing is proposed on an answer nobody got"
    if [[ $BUMP_EXIT -ne 0 ]]; then
        pass "empty skills: the run exits non-zero"
    else
        fail "empty skills: the run exits non-zero (got 0)"
    fi
    # The load-bearing one. A re-pin here would write the same empty map back
    # and be refused downstream, so the only evidence that it never STARTED is
    # that no branch exists to have pushed it to.
    if [[ -z "$(git -C "$bare" rev-parse --verify -q refs/heads/skills-lock-bump/update 2>/dev/null || true)" ]]; then
        pass "empty skills: the consumer's lock is left untouched"
    else
        fail "empty skills: the consumer's lock is left untouched"
    fi

    rm -rf "$root" "$work"
}

# ── Test 8h2: a lock of the right CONTENT but the wrong digest SHAPE ──────
#
# The gate this asserts exists because the old one could not fail. A lock that
# stores bare 64-hex where the canonical shape is `sha256:<hex>` is wrong, and
# `--check-current` — the only question this script used to ask — cannot see
# it: it digests the pinned tree and the working tree afresh and never reads
# the stored values at all (the generator labels at the DOCUMENT boundary
# expressly so that comparison keeps working on bare hex). Eight real consumer
# locks sat malformed on 94cdcc81 for exactly as long as the `adam` bundle
# stood still, with this script logging "no re-pin needed" at them nightly.
#
# So the fixtures are a MATCHED PAIR pinned at the same content-current ref,
# differing in nothing but digest shape. That pairing is the point: a test that
# only proved the malformed lock gets re-pinned would pass just as well if the
# gate had degenerated into "re-pin everything", which is the anti-churn
# property this repo's whole design is built to protect (ADR 0005). One must be
# proposed and the other must be left alone, in the same run.
#
# Its own bare dir and its own consumers, so the shared fixture set — and the
# exact "2 proposed" count another test asserts on it — never sees these.
test_bump_digest_format_gate() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a lock whose digests are the wrong shape) ==="

    local root="$TEST_DIR/bare-format"
    rm -rf "$root" "$TEST_DIR/work/bumporg-repo-bare-digests" \
           "$TEST_DIR/work/bumporg-repo-labelled"

    local name bare work
    for name in repo-bare-digests repo-labelled; do
        bare="$root/bumporg_$name"
        work="$TEST_DIR/work/bumporg-$name"
        mkdir -p "$bare" "$work"
        git init --bare --initial-branch=main "$bare" >/dev/null 2>&1
        git init --initial-branch=main "$work" >/dev/null 2>&1
        cd "$work"
        git config commit.gpgsign false
        git remote add origin "$bare"
        echo "# $name" > README.md
        # BUMP_REF_CONTENT is the ref whose bundle content equals the
        # registry's working tree, so --check-current answers OK at exit 0 for
        # both of these. Whatever happens next is the shape gate's doing and
        # nothing else's.
        seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_CONTENT"
        [[ "$name" == "repo-bare-digests" ]] && strip_digest_labels skills.lock
        git add -A
        git commit -m "init" >/dev/null 2>&1
        git push origin HEAD:main >/dev/null 2>&1
        cd "$REPO_ROOT"
    done

    # The fixture is only meaningful if seeding really produced the two shapes
    # asked for. Asserted rather than assumed: seed_bump_lock fills the lock by
    # calling the stub's --repin, so a stub that stopped labelling would make
    # BOTH fixtures bare, and every assertion below would still "pass" while
    # testing one case twice.
    local bare_lock labelled_lock
    bare_lock=$(git -C "$root/bumporg_repo-bare-digests" show main:skills.lock)
    labelled_lock=$(git -C "$root/bumporg_repo-labelled" show main:skills.lock)
    if grep -qF '"adam/finding-unknowns": "sha256:' <<< "$labelled_lock" \
       && ! grep -qF '"adam/finding-unknowns": "sha256:' <<< "$bare_lock"; then
        pass "format gate: the fixtures really are one labelled lock and one bare one"
    else
        fail "format gate: the fixtures really are one labelled lock and one bare one"
    fi

    BUMP_BARE_DIR_FOR_RUN="$root" run_bump "$TEST_DIR/bump-format.txt"
    unset BUMP_BARE_DIR_FOR_RUN

    # ── The malformed lock is repaired ────────────────────────────────────
    assert_contains "$TEST_DIR/bump-format.txt" \
        "stored digests are not sha256:<64 hex> — re-pin needed to relabel them" \
        "format gate: the malformed lock is re-pinned, and the log says the shape is why"
    assert_contains "$TEST_DIR/bump-format.txt" "1 proposed" \
        "format gate: exactly one of the two consumers is proposed"

    # ── ...and the reason is legible, not the bundle-moved boilerplate ────
    local body="$BUMP_PR_BODY_DIR/bumporg_repo-bare-digests.body"
    assert_contains "$body" "The bundle content at the pinned ref is UNCHANGED" \
        "format gate: the PR body says plainly that nothing diverged"
    assert_contains "$body" '`--check-format`' \
        "format gate: the PR body names the flag whose verdict it quotes"
    # The old body claimed divergence unconditionally. On this PR that sentence
    # would contradict the diff it introduces, in which not one digest's hex
    # differs — so its absence is the assertion, not a nicety.
    assert_not_contains "$body" "no longer matches the registry's tree" \
        "format gate: the PR body does not claim the bundle diverged"
    # The HEADER, not just the paragraph. The first cut of this gate branched
    # the paragraph and left `**What moved:** <registry> — <old> → <new>`
    # unconditional six lines above it, so the PR announced a bundle move
    # directly over a paragraph denying one — the self-contradicting-in-its-own
    # -diff shape this whole change set exists to stop. Both halves are
    # asserted because either alone is satisfiable by deleting the header
    # outright, which is why test_bump_consumer_locks asserts the content-side
    # header is still there.
    assert_not_contains "$body" "**What moved:**" \
        "format gate: a format re-pin is not headed by a bundle move"
    assert_contains "$body" "pin stays at \`${BUMP_REF_CONTENT:0:7}\`" \
        "format gate: the header says the pin does not move, and names where it stays"

    # ── The repair actually lands in the pushed lock ──────────────────────
    # The gate is worth nothing if it proposes a re-pin that does not fix the
    # thing it fired on, so the pushed bytes are read back rather than trusted.
    local pushed
    pushed=$(git -C "$root/bumporg_repo-bare-digests" \
        show "refs/heads/skills-lock-bump/update:skills.lock" 2>/dev/null || true)
    if [[ -n "$pushed" ]] && ! grep -qE '"adam/[a-z-]+": "[0-9a-f]{64}"' <<< "$pushed" \
       && grep -qF '"adam/finding-unknowns": "sha256:' <<< "$pushed"; then
        pass "format gate: the pushed lock has every digest relabelled, none left bare"
    else
        fail "format gate: the pushed lock has every digest relabelled, none left bare"
    fi

    # ── A SHAPE repair is not a CONTENT advance ──────────────────────────
    #
    # `--repin` does not inherit `ref` — advancing the pin IS the operation —
    # so an invocation without `--ref` re-pins onto whatever commit the
    # registry checkout is sitting on. Correct for a content re-pin, which is
    # what test_bump_consumer_locks asserts still happens; wrong here, where
    # `--check-current` has already answered OK and the only complaint is
    # about the digests STORED in the file.
    #
    # This is not hypothetical and it is not small. Eight real consumer locks
    # sat bare on 94cdcc81 at once; they were healed BY HAND, every pin
    # preserved. Had the nightly bumper reached them first, one sweep would
    # have moved all eight pins — a fleet-wide content advance wearing a shape
    # repair's PR body, whose every digest line proves nothing diverged.
    #
    # These artifacts are written to files rather than held in `$( )` for one
    # reason: `assert_not_contains` PASSES on a file that does not exist
    # (`grep -qF` simply finds nothing in nothing), so the emptiness check
    # below is what stops the whole block from going green on a run that
    # pushed no branch at all.
    local before_lock="$TEST_DIR/format-before.lock"
    local after_lock="$TEST_DIR/format-after.lock"
    printf '%s\n' "$bare_lock" > "$before_lock"
    printf '%s\n' "$pushed" > "$after_lock"
    local title="$BUMP_PR_BODY_DIR/bumporg_repo-bare-digests.title"
    if [[ -s "$after_lock" && -s "$body" && -s "$title" && -n "$pushed" ]]; then
        pass "format gate: there is a pushed lock, a body and a title to assert on at all"
    else
        fail "format gate: there is a pushed lock, a body and a title to assert on at all"
    fi

    # The pin, read as a field rather than grepped: `ref` and `generated_from`
    # must BOTH still name the commit the lock arrived pinned to. Read with
    # `|| true` so an absent lock reaches the assertion as an empty string and
    # FAILS there, instead of aborting the suite from a command substitution.
    local pushed_ref pushed_generated_from
    pushed_ref=$(lock_field_of "$after_lock" ref 2>/dev/null) || pushed_ref=""
    pushed_generated_from=$(lock_field_of "$after_lock" generated_from 2>/dev/null) \
        || pushed_generated_from=""
    if [[ "$pushed_ref" == "$BUMP_REF_CONTENT" \
       && "$pushed_generated_from" == "$BUMP_REF_CONTENT" ]]; then
        pass "format gate: the re-pin holds the pin at the commit the lock already named"
    else
        fail "format gate: the re-pin holds the pin — got '${pushed_ref:-<no lock>}'"
    fi
    # The negative half, and the one that would notice a pin moved to a commit
    # nobody named: the registry's HEAD is two commits ahead of this lock's
    # ref, so it can appear here only by having been resolved.
    assert_not_contains "$after_lock" "$BUMP_REF_HEAD" \
        "format gate: the registry checkout's HEAD is nowhere in the repaired lock"

    # A pure RELABEL: same names, same hex, the label added. Distinct from the
    # pin assertions above because it is what makes "every digest here is
    # re-derived from the newly pinned commit" true of a diff a reviewer can
    # check by eye — and it fails loudly rather than vacuously if either side
    # lists nothing, which is the shape a comparison of two empty maps takes.
    if python3 -c '
import json, sys
locks = []
for path in sys.argv[1:3]:
    try:
        locks.append(json.load(open(path, encoding="utf-8")))
    except (OSError, ValueError) as exc:
        sys.exit("relabel: %s is not a readable lock (%s) — there is nothing to "
                 "compare, which is not the same as nothing being wrong" % (path, exc))
before, after = locks
was, now = before.get("skills") or {}, after.get("skills") or {}
if not was or not now:
    sys.exit("relabel: %d digests before and %d after — an empty comparison is not a "
             "clean one" % (len(was), len(now)))
if set(was) != set(now):
    sys.exit("relabel: the skill names changed: %s" % sorted(set(was) ^ set(now)))
wrong = [name for name in sorted(was) if now[name] != "sha256:" + was[name]]
if wrong:
    sys.exit("relabel: %d of %d digests are not the same hex relabelled: %s"
             % (len(wrong), len(was), wrong[:3]))
' "$before_lock" "$after_lock" 2>"$TEST_DIR/format-relabel.err"; then
        pass "format gate: every digest is the same hex it was, wearing its label"
    else
        fail "format gate: every digest is the same hex it was — \
$(cat "$TEST_DIR/format-relabel.err")"
    fi

    # ── The PR cannot contradict its own diff ────────────────────────────
    # The body QUOTES `--check-format`'s verdict verbatim, remediation line
    # included, and then tells the reviewer that line is the command that ran.
    # With the pin held that is true; without `--ref` the quoted command names
    # the old pin above a diff that moved it — a body that cannot reproduce
    # itself, which is how this defect was found.
    # A RELATION, not a constant: the ref the body's quoted command names has to
    # be the ref the pushed lock actually carries. Asserting the constant
    # `$BUMP_REF_CONTENT` here would pass unchanged while the diff moved the
    # pin out from under it, which is precisely the contradiction being
    # guarded — the quoted verdict is `--check-format`'s and always names the
    # lock's OWN pin, whatever the re-pin then did.
    local quoted_ref
    quoted_ref=$(sed -n 's/.*--repin --ref \([0-9a-f]\{40\}\).*/\1/p' "$body" 2>/dev/null \
        | head -1) || quoted_ref=""
    if [[ -n "$quoted_ref" && "$quoted_ref" == "$pushed_ref" ]]; then
        pass "format gate: the quoted remediation names the ref this PR actually pinned"
    else
        fail "format gate: the quoted remediation names the ref this PR actually pinned \
— body says '${quoted_ref:-<none>}', lock says '$pushed_ref'"
    fi
    assert_contains "$body" "the command this PR ran" \
        "format gate: the body claims that command, so the assertion above has a subject"
    # The two sentences in the body's SHARED tail that a held pin makes false.
    # Both are asserted from both sides — the content-side controls sit in
    # test_bump_consumer_locks — because either alone is satisfiable by
    # deleting the sentence outright from both branches.
    assert_not_contains "$body" "re-derived from the newly pinned commit" \
        "format gate: nothing claims a NEWLY pinned commit — the pin is the one it had"
    assert_contains "$body" "re-derived from the commit this lock already pinned" \
        "format gate: the body says which commit the digests came from instead"
    assert_not_contains "$body" "re-resolves only \`ref\`" \
        "format gate: the body does not claim ref was re-resolved when it was pinned"
    assert_contains "$body" "so even \`ref\` is unchanged" \
        "format gate: the body says ref was held, and why"

    # The commit message is the same artifact one layer in, and it carried the
    # same unconditional bundle-moved sentence the header used to.
    local msg="$TEST_DIR/format-commit-msg.txt"
    git -C "$root/bumporg_repo-bare-digests" log -1 --format=%B \
        refs/heads/skills-lock-bump/update > "$msg" 2>/dev/null || : > "$msg"
    assert_not_contains "$msg" "has moved since" \
        "format gate: the commit message does not claim the bundle moved"
    assert_contains "$msg" "has NOT moved" \
        "format gate: the commit message says plainly what did not happen"

    # The TITLE. A PR list shows nothing else, so "re-pin onto <registry>@<sha>"
    # over a diff whose ref line is unchanged sends a reviewer looking for a
    # move that is not there.
    assert_contains "$title" "pin unchanged" \
        "format gate: the title says the pin did not move"
    assert_not_contains "$title" "re-pin skills.lock onto" \
        "format gate: the title does not announce a re-pin onto a commit"

    # ── ANTI-CHURN. The well-formed twin is left completely alone ─────────
    assert_contains "$TEST_DIR/bump-format.txt" \
        "bundle content unchanged since ${BUMP_REF_CONTENT:0:7} — no re-pin needed." \
        "format gate: a well-formed lock with unchanged content is still skipped"
    if [[ -z "$(git -C "$root/bumporg_repo-labelled" rev-parse --verify -q \
                refs/heads/skills-lock-bump/update 2>/dev/null || true)" ]]; then
        pass "format gate: nothing is pushed to the well-formed consumer"
    else
        fail "format gate: nothing is pushed to the well-formed consumer"
    fi

    rm -rf "$root" "$TEST_DIR/work/bumporg-repo-bare-digests" \
           "$TEST_DIR/work/bumporg-repo-labelled"
}

# ── Test 8h3: a generator too old to answer the shape question ────────────
#
# The flag is newer than the rest of this script's requirements and the
# generator comes from whatever checkout BUMP_GENERATOR points at, so a run can
# meet one that lacks it. Unlike --repin it is not load-bearing: without it the
# gate asks one question instead of two, which is exactly what the script did
# before. So the required behaviour is DEGRADE — never a hard exit (that would
# ground the fleet over a flag that only ever adds a case) and never silence
# (the caller is a nightly scheduled run, where an unannounced downgrade is
# indistinguishable from the gate working).
test_bump_generator_without_check_format() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a generator with no shape flag) ==="

    local root="$TEST_DIR/bare-noformat"
    local work="$TEST_DIR/work/bumporg-repo-bare-old-gen"
    rm -rf "$root" "$work"
    mkdir -p "$root/bumporg_repo-bare-old-gen" "$work"
    git init --bare --initial-branch=main "$root/bumporg_repo-bare-old-gen" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$root/bumporg_repo-bare-old-gen"
    echo "# repo-bare-old-gen" > README.md
    seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_CONTENT"
    strip_digest_labels skills.lock
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"

    # Carries --repin (so the load-bearing probe passes and the run proceeds)
    # and --check-current (exit 0 — this fixture's content genuinely has not
    # moved), and nothing else. The absence of --check-format from --help is
    # the whole fixture.
    local gen="$TEST_DIR/generator-no-check-format.py"
    cat > "$gen" <<'NOFORMAT'
#!/usr/bin/env python3
"""A generator from before the shape flag existed: current/re-pin only."""
import argparse
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--check-current", action="store_true")
parser.add_argument("--repin", action="store_true")
parser.add_argument("--repo")
parser.add_argument("--ref")
parser.add_argument("--source-repo", action="append", default=[])
parser.add_argument("-o", "--output", required=True)
parser.parse_args()
sys.exit(0)
NOFORMAT

    BUMP_BARE_DIR_FOR_RUN="$root" BUMP_GENERATOR_FOR_RUN="$gen" \
        run_bump "$TEST_DIR/bump-noformat.txt"
    unset BUMP_BARE_DIR_FOR_RUN BUMP_GENERATOR_FOR_RUN

    assert_contains "$TEST_DIR/bump-noformat.txt" "has no --check-format" \
        "old generator: the missing flag is announced, by name"
    if [[ $BUMP_EXIT -eq 0 ]]; then
        pass "old generator: degrades instead of failing the run"
    else
        fail "old generator: degrades instead of failing the run (exit $BUMP_EXIT)"
    fi
    # Degrading means behaving as this script did before the gate existed —
    # which for a content-current lock is to skip it. A run that instead
    # re-pinned on a question it could not ask would be the worse failure.
    assert_contains "$TEST_DIR/bump-noformat.txt" \
        "bundle content unchanged since ${BUMP_REF_CONTENT:0:7} — no re-pin needed." \
        "old generator: falls back to the bundle-moved question alone"
    assert_contains "$TEST_DIR/bump-noformat.txt" "0 proposed" \
        "old generator: proposes nothing it could not justify"

    rm -rf "$root" "$work"
}

# ── Test 8h4: "cannot tell" is not "the digests are bad" ──────────────────
#
# --check-format exits 1 for a malformed lock AND for a lock it could not read
# at all — a missing file, unparseable JSON — exactly as --check-current exits
# 1 both for real drift and for an unresolvable ref. Measured against the real
# generator: `--check-format -o <missing>` prints `ERROR: ... does not exist`
# and exits 1, byte-identical in exit code to the FAILED: case. So the gate
# must branch on the flag's own verdict, never on the exit code, or a run that
# merely lost sight of a lock would rewrite it — the same mistake the
# --check-current branch already refuses to make, and the reason this fixture
# exists rather than being assumed from that one.
test_bump_format_check_unreadable() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (the shape question cannot be answered) ==="

    local root="$TEST_DIR/bare-formaterr"
    local work="$TEST_DIR/work/bumporg-repo-format-error"
    rm -rf "$root" "$work"
    mkdir -p "$root/bumporg_repo-format-error" "$work"
    git init --bare --initial-branch=main "$root/bumporg_repo-format-error" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$root/bumporg_repo-format-error"
    echo "# repo-format-error" > README.md
    # Content-current and bare, i.e. the exact shape that SHOULD be re-pinned.
    # That is what makes this a real test of the verdict check: the only thing
    # standing between this lock and a rewrite is the ERROR:/FAILED: split.
    seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_CONTENT"
    strip_digest_labels skills.lock
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"

    # Advertises the flag, so the probe arms the gate, then cannot answer it.
    local gen="$TEST_DIR/generator-format-error.py"
    cat > "$gen" <<'FORMATERR'
#!/usr/bin/env python3
"""--check-format is present but can only report that it could not tell."""
import argparse
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--check-current", action="store_true")
parser.add_argument("--check-format", action="store_true")
parser.add_argument("--repin", action="store_true")
parser.add_argument("--repo")
parser.add_argument("--ref")
parser.add_argument("--source-repo", action="append", default=[])
parser.add_argument("-o", "--output", required=True)
args = parser.parse_args()
if args.check_format:
    print("ERROR: %s is not valid JSON" % args.output)
    sys.exit(1)
if args.repin:
    sys.exit("this generator must never be asked to re-pin")
sys.exit(0)
FORMATERR

    BUMP_BARE_DIR_FOR_RUN="$root" BUMP_GENERATOR_FOR_RUN="$gen" \
        run_bump "$TEST_DIR/bump-formaterr.txt"
    unset BUMP_BARE_DIR_FOR_RUN BUMP_GENERATOR_FOR_RUN

    assert_contains "$TEST_DIR/bump-formaterr.txt" \
        "could not decide whether skills.lock's digests are well-formed" \
        "unanswerable shape: reported as a failure to decide, not as a verdict"
    assert_contains "$TEST_DIR/bump-formaterr.txt" "1 failed" \
        "unanswerable shape: counted, so the scheduled run goes red"
    assert_contains "$TEST_DIR/bump-formaterr.txt" "0 proposed" \
        "unanswerable shape: nothing is proposed on an answer nobody got"
    if [[ $BUMP_EXIT -ne 0 ]]; then
        pass "unanswerable shape: the run exits non-zero"
    else
        fail "unanswerable shape: the run exits non-zero (got 0)"
    fi
    if [[ -z "$(git -C "$root/bumporg_repo-format-error" rev-parse --verify -q \
                refs/heads/skills-lock-bump/update 2>/dev/null || true)" ]]; then
        pass "unanswerable shape: the consumer's lock is left untouched"
    else
        fail "unanswerable shape: the consumer's lock is left untouched"
    fi

    rm -rf "$root" "$work"
}

# ── Test 8i: the sweep merges the bump PRs a previous run left open ───────
#
# The half that makes these pull requests land without anyone clicking merge,
# and the half where a mistake is unreviewed by construction. So every fixture
# here is a way for the sweep to merge something it must not:
#
#   * a PR whose checks are RED, or have not finished — and the two shapes a
#     rollup entry can take, because a check RUN carries `.conclusion` and a
#     legacy commit status carries `.state`, and reading one leaves the
#     other's failures looking clean (AGENTS.md, "The watch finished is not
#     CI passed");
#   * a PR that is not OURS — the right branch under someone else's name, or
#     someone else's branch;
#   * a PR whose diff has grown a second file, which is a human's work sitting
#     on the bot's branch;
#   * a PR that cannot be merged at all.
#
# An ABSENCE of checks is deliberately NOT one of them: most consumers in this
# fleet have no CI, and a sweep that waited for a green check on those would
# never merge anything. `repo-nochecks` is that case, and it must merge.
#
# The fleet is stood up in a MOCK_BARE_DIR and a MOCK_PR_DIR of its own, like
# the two tests above it, so none of this becomes standing state for the
# shared bumporg fixtures.

SWEEP_BARE="$TEST_DIR/bare-sweep"
SWEEP_PR_DIR="$TEST_DIR/sweep-prs"

# make_sweep_repo <name> <lock ref on main> <head branch|none> <extra file on
#                 that branch|none> <PR object|none>
#
# The bump branch is built the way a previous night's run would have built it:
# the same lock, re-pinned onto the registry's current commit by the same
# generator, and nothing else in the commit. The `files` array the sweep reads
# is computed by the mock from THIS branch, so "someone pushed a second file"
# has to actually push one.
make_sweep_repo() {
    local name="$1" main_ref="$2" head="$3" extra="$4" pr="$5"
    local bare="$SWEEP_BARE/bumporg_$name"
    local work="$TEST_DIR/work/sweep-$name"

    rm -rf "$bare" "$work"
    mkdir -p "$bare" "$work"
    git init --bare --initial-branch=main "$bare" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$bare"
    echo "# $name" > README.md
    seed_bump_lock skills.lock "bumporg/agentskills" "$main_ref"
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1

    if [[ "$head" != "none" ]]; then
        git checkout -q -b "$head"
        python3 "$TEST_DIR/registry/scripts/generate_skills_lock.py" --repin \
            --repo "$TEST_DIR/registry" -o skills.lock >/dev/null
        if [[ "$extra" != "none" ]]; then
            echo "a reviewer's own work, pushed onto the bot's branch" > "$extra"
        fi
        git add -A
        git commit -m "chore: re-pin skills.lock" >/dev/null 2>&1
        git push origin "HEAD:refs/heads/$head" >/dev/null 2>&1
        git checkout -q main
    fi

    if [[ "$pr" != "none" ]]; then
        mkdir -p "$SWEEP_PR_DIR"
        printf '[%s]\n' "$pr" > "$SWEEP_PR_DIR/bumporg_$name.json"
    fi

    cd "$REPO_ROOT"
}

sweep_main_sha() {   # <short repo name>
    git -C "$SWEEP_BARE/bumporg_$1" rev-parse --verify -q refs/heads/main 2>/dev/null || true
}

setup_sweep_repos() {
    local branch="skills-lock-bump/update"
    local bot='"author":{"login":"agents-md-sync[bot]"}'
    local clean='"isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","reviewDecision":""'

    rm -rf "$SWEEP_BARE" "$SWEEP_PR_DIR"
    mkdir -p "$SWEEP_BARE" "$SWEEP_PR_DIR"

    # The registry itself, carrying a bump pull request that is ready in every
    # other respect. It must NOT be merged: nothing in this bumper opens a PR
    # on the registry, so anything sitting on that branch name there belongs to
    # somebody else. Without this fixture the carve-out assertion would be
    # vacuous, exactly as the shared bumporg fleet's stale registry lock keeps
    # the propose-side carve-out from being vacuous.
    make_sweep_repo agentskills "$BUMP_REF_CONTENT" "$branch" none \
        "{\"number\":100,\"headRefName\":\"$branch\",$bot,$clean,\"statusCheckRollup\":[]}"

    # Sorts FIRST, has no bump PR, and its lock is genuinely stale — so the
    # run proposes here. That is the anchor for the ordering assertion: the
    # sweep merges repo-zz-ready, which sorts LAST, and if the sweep were a
    # per-repo step rather than a pass of its own, this repo's proposal would
    # come first.
    make_sweep_repo repo-aa-stale "$BUMP_REF_OLD" none none none

    make_sweep_repo repo-conflict "$BUMP_REF_CONTENT" "$branch" none \
        "{\"number\":102,\"headRefName\":\"$branch\",$bot,\"isDraft\":false,\"mergeable\":\"CONFLICTING\",\"mergeStateStatus\":\"DIRTY\",\"reviewDecision\":\"\",\"statusCheckRollup\":[]}"

    # The merge itself is refused by the API (a base that moved under it).
    # Sorts before both repos that DO merge, so those two are the assertion
    # that one repo's failure did not end the sweep.
    make_sweep_repo repo-fail-merge "$BUMP_REF_CONTENT" "$branch" none \
        "{\"number\":103,\"headRefName\":\"$branch\",$bot,$clean,\"statusCheckRollup\":[]}"

    make_sweep_repo repo-failing "$BUMP_REF_CONTENT" "$branch" none \
        "{\"number\":104,\"headRefName\":\"$branch\",$bot,\"isDraft\":false,\"mergeable\":\"MERGEABLE\",\"mergeStateStatus\":\"UNSTABLE\",\"reviewDecision\":\"\",\"statusCheckRollup\":[{\"name\":\"ci\",\"status\":\"COMPLETED\",\"conclusion\":\"FAILURE\"}]}"

    # The OTHER rollup shape: a legacy commit status, which carries `.state`
    # and no `.conclusion` at all. A gate that reads only `.conclusion` sees
    # null here and has to decide what null means — and "not concluded yet" is
    # the reading that merges this PR.
    make_sweep_repo repo-legacy-red "$BUMP_REF_CONTENT" "$branch" none \
        "{\"number\":105,\"headRefName\":\"$branch\",$bot,\"isDraft\":false,\"mergeable\":\"MERGEABLE\",\"mergeStateStatus\":\"UNSTABLE\",\"reviewDecision\":\"\",\"statusCheckRollup\":[{\"context\":\"legacy/build\",\"state\":\"FAILURE\"}]}"

    make_sweep_repo repo-nochecks "$BUMP_REF_CONTENT" "$branch" none \
        "{\"number\":106,\"headRefName\":\"$branch\",$bot,$clean,\"statusCheckRollup\":[]}"

    make_sweep_repo repo-otherfile "$BUMP_REF_CONTENT" "$branch" NOTES.md \
        "{\"number\":107,\"headRefName\":\"$branch\",$bot,$clean,\"statusCheckRollup\":[]}"

    make_sweep_repo repo-pending "$BUMP_REF_CONTENT" "$branch" none \
        "{\"number\":108,\"headRefName\":\"$branch\",$bot,\"isDraft\":false,\"mergeable\":\"MERGEABLE\",\"mergeStateStatus\":\"UNSTABLE\",\"reviewDecision\":\"\",\"statusCheckRollup\":[{\"name\":\"ci\",\"status\":\"IN_PROGRESS\",\"conclusion\":null}]}"

    make_sweep_repo repo-wrongauthor "$BUMP_REF_CONTENT" "$branch" none \
        "{\"number\":109,\"headRefName\":\"$branch\",\"author\":{\"login\":\"a-human\"},$clean,\"statusCheckRollup\":[]}"

    # Someone else's branch, listed as if the head filter had not been applied
    # — which is exactly the assumption the script must not make (see the mock
    # `pr list`).
    make_sweep_repo repo-wrongbranch "$BUMP_REF_CONTENT" "human/experiment" none \
        "{\"number\":110,\"headRefName\":\"human/experiment\",$bot,$clean,\"statusCheckRollup\":[]}"

    # Sorts LAST, and is the one PR that is ready in every respect.
    make_sweep_repo repo-zz-ready "$BUMP_REF_CONTENT" "$branch" none \
        "{\"number\":111,\"headRefName\":\"$branch\",$bot,$clean,\"statusCheckRollup\":[{\"name\":\"ci\",\"status\":\"COMPLETED\",\"conclusion\":\"SUCCESS\"},{\"name\":\"lint\",\"status\":\"COMPLETED\",\"conclusion\":\"SKIPPED\"}]}"
}

# A prefix assignment in front of a FUNCTION call stays set after the call
# returns (unlike one in front of an external command), which is why every
# caller in this lane unsets afterwards. Done once here.
run_sweep() {   # <output file> [script args...]
    BUMP_BARE_DIR_FOR_RUN="$SWEEP_BARE" \
    BUMP_PR_DIR_FOR_RUN="$SWEEP_PR_DIR" \
    BUMP_MERGE_FAIL_FOR_RUN="bumporg_repo-fail-merge" \
    BUMP_AUTO_MERGE_FAILS_FOR_RUN=1 \
        run_bump "$@"
    unset BUMP_BARE_DIR_FOR_RUN BUMP_PR_DIR_FOR_RUN \
          BUMP_MERGE_FAIL_FOR_RUN BUMP_AUTO_MERGE_FAILS_FOR_RUN
}

# ── Test 8i1: --dry-run reports every merge it would make and makes none ──

test_bump_sweep_dry_run() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh --dry-run (the sweep) ==="

    setup_sweep_repos

    local ready_before nochecks_before prs_before
    ready_before=$(sweep_main_sha repo-zz-ready)
    nochecks_before=$(sweep_main_sha repo-nochecks)
    prs_before=$(wc -l < "$BUMP_PR_LOG")

    run_sweep "$TEST_DIR/sweep-dry.txt" --dry-run
    local log="$TEST_DIR/sweep-dry.txt"

    assert_contains "$log" "[DRY RUN] Would merge bumporg/repo-zz-ready#111" "sweep dry-run: names the merge it would make"
    assert_contains "$log" "[DRY RUN] Would merge bumporg/repo-nochecks#106" "sweep dry-run: names every merge, not just the first"
    assert_contains "$log" "0 merged" "sweep dry-run: merges nothing"
    # The decisions are still made and still reported — a dry run is what a
    # reviewer reads to find out what tonight would do.
    assert_contains "$log" "bumporg/repo-failing#104: not merged" "sweep dry-run: still reports the ones it would refuse"

    if [[ "$(sweep_main_sha repo-zz-ready)" == "$ready_before" \
          && "$(sweep_main_sha repo-nochecks)" == "$nochecks_before" ]]; then
        pass "sweep dry-run: no default branch moved"
    else
        fail "sweep dry-run: no default branch moved"
    fi
    if [[ "$(wc -l < "$BUMP_PR_LOG")" == "$prs_before" ]]; then
        pass "sweep dry-run: gh pr merge was never called"
    else
        fail "sweep dry-run: gh pr merge was never called — $(tail -1 "$BUMP_PR_LOG")"
    fi
    if [[ -f "$SWEEP_PR_DIR/bumporg_repo-zz-ready.json" ]] \
       && grep -q '"number": *111' "$SWEEP_PR_DIR/bumporg_repo-zz-ready.json"; then
        pass "sweep dry-run: the pull request is still open"
    else
        fail "sweep dry-run: the pull request is still open"
    fi
}

# ── Test 8i2: the sweep itself ────────────────────────────────────────────

test_bump_sweep() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (sweep, then propose) ==="

    local ready_branch_tip untouched
    ready_branch_tip=$(git -C "$SWEEP_BARE/bumporg_repo-zz-ready" rev-parse \
        --verify -q refs/heads/skills-lock-bump/update)
    : > "$TEST_DIR/sweep-mains-before.txt"
    for untouched in agentskills repo-conflict repo-failing repo-legacy-red \
                     repo-otherfile repo-pending repo-wrongauthor repo-wrongbranch; do
        echo "$untouched $(sweep_main_sha "$untouched")" >> "$TEST_DIR/sweep-mains-before.txt"
    done

    run_sweep "$TEST_DIR/sweep.txt"
    local log="$TEST_DIR/sweep.txt"

    # ── The ready PR lands, with a merge commit and nothing else.
    assert_contains "$log" "bumporg/repo-zz-ready#111: MERGED with a merge commit" "sweep: a ready bump PR from a previous run is merged"
    assert_contains "$log" "all 2 check(s) concluded green" "sweep: says what the checks said"
    assert_contains "$BUMP_PR_LOG" "pr-merged 111 --repo bumporg/repo-zz-ready --merge" "sweep: merged with --merge, and with the PR and repo named"
    assert_not_contains "$BUMP_PR_LOG" " --squash" "sweep: never asks for a squash — it is disabled fleet-wide and strands a pinned commit"
    # Pinned to the very commit the gate read. Without this the verdict and
    # the merge are two decisions with a gap between them, and what lands is
    # whatever the branch happens to hold by the time the merge goes out.
    assert_contains "$BUMP_PR_LOG" "pr-merged 111 --repo bumporg/repo-zz-ready --merge --match-head-commit $ready_branch_tip" "sweep: the merge is pinned to the head commit the safety check read"
    if [[ "$(sweep_main_sha repo-zz-ready)" == "$ready_branch_tip" ]]; then
        pass "sweep: the default branch now carries the re-pinned lock"
    else
        fail "sweep: the default branch now carries the re-pinned lock"
    fi

    # ── No CI at all is not a red CI. Most consumers are this one.
    assert_contains "$log" "bumporg/repo-nochecks#106: MERGED with a merge commit" "sweep: a PR with no checks at all is merged"
    assert_contains "$log" "no checks ran on it" "sweep: 'no checks ran' is its own sentence, not an indistinguishable OK"

    # ── Every reason not to merge.
    assert_contains "$log" "bumporg/repo-failing#104: not merged — 1 check(s) are not green (ci: FAILURE)" "sweep: a failing check-run conclusion blocks the merge"
    assert_contains "$log" "bumporg/repo-legacy-red#105: not merged — 1 check(s) are not green (legacy/build: FAILURE)" "sweep: a legacy commit status .state failure blocks it too"
    assert_contains "$log" "bumporg/repo-pending#108: not merged — 1 check(s) have not concluded" "sweep: a pending check blocks the merge"
    assert_contains "$log" "bumporg/repo-wrongauthor#109: not merged — it was opened by" "sweep: a PR opened by someone else is not merged"
    assert_contains "$log" "bumporg/repo-wrongbranch#110: not merged — its head branch is" "sweep: a PR on another branch is not merged"
    assert_contains "$log" "bumporg/repo-otherfile#107: not merged — its diff is not skills.lock alone" "sweep: a diff carrying a second file is not merged"
    assert_contains "$log" "NOTES.md" "sweep: names the file that is not the lock"
    assert_contains "$log" "bumporg/repo-conflict#102: not merged — GitHub reports mergeable=CONFLICTING" "sweep: a conflicted PR is not merged"
    # The registry owns its own re-pin (ADR 0005), so it is not swept either —
    # and its PR is not merely refused by the gate, it is never judged.
    assert_contains "$log" "bumporg/agentskills — the registry itself; nothing here opens a pull request on it" "sweep: the registry is carved out, with the reason"
    assert_not_contains "$log" "bumporg/agentskills#100" "sweep: the registry's pull request is not even considered"

    # Not merged is a claim about the repo, not about the log: every one of
    # these still has to be sitting on the commit it started the run on.
    local r sha
    while read -r r sha; do
        if [[ "$(sweep_main_sha "$r")" == "$sha" ]]; then
            pass "sweep: bumporg/$r default branch is untouched"
        else
            fail "sweep: bumporg/$r default branch is untouched — something merged into it"
        fi
    done < "$TEST_DIR/sweep-mains-before.txt"

    # ── One repo's merge failure is counted, and the sweep goes on. Both
    # repos that DO merge sort after it.
    assert_contains "$log" "bumporg/repo-fail-merge#103: merge was refused" "sweep: a refused merge is reported"
    # Anchored on the summary line's closing marker: a bare "1 failed" also
    # matches "11 failed".
    assert_contains "$log" "1 failed ===" "sweep: the refused merge is counted, and is the only failure"
    assert_line_before "$log" "bumporg/repo-fail-merge#103: merge was refused" \
        "bumporg/repo-zz-ready#111: MERGED" "sweep: a later repo still merges after one repo's failure"
    if [[ $BUMP_EXIT -ne 0 ]]; then
        pass "sweep: the run exits non-zero, so a scheduled run goes red"
    else
        fail "sweep: the run exits non-zero, so a scheduled run goes red (got 0)"
    fi

    # ── THE ordering. repo-zz-ready sorts LAST and repo-aa-stale FIRST, so a
    # sweep folded into the per-repo loop would put the proposal first. Merging
    # a PR seconds after opening it merges it before any check has started.
    assert_line_before "$log" "bumporg/repo-zz-ready#111: MERGED" \
        "=== bumporg/repo-aa-stale ===" "sweep: the whole sweep runs BEFORE anything is proposed"
    assert_line_before "$log" "Sweeping bump pull requests left open by a previous run" \
        "Proposing re-pins for consumers whose bundle has moved" "sweep: the log says which pass is which, in that order"
    assert_contains "$log" "PR created" "sweep: the propose pass still runs after it"
    assert_not_contains "$log" "bumporg/repo-aa-stale#" "sweep: the PR this run opened is not also merged by this run"

    # ── The summary carries the merges alongside the existing counts.
    assert_contains "$log" "2 merged, 1 proposed" "sweep: merges are counted in the summary line"

    # ── Native auto-merge is attempted on the new PR and its refusal is not a
    # failure. MOCK_AUTO_MERGE_FAILS reproduces this fleet's measured
    # behaviour: with no required checks there is nothing to hold the merge
    # for, and GitHub refuses to arm at all.
    # Needles that would START with a dash are prefixed with the log's own
    # first word: `grep -F -- "$needle"` is not what assert_contains runs, so a
    # leading `--` is parsed by grep as an option, the grep errors, and
    # assert_not_contains in particular would then PASS on anything.
    assert_contains "$BUMP_PR_LOG" "pr-merged --auto --merge --repo bumporg/repo-aa-stale" "auto-merge: attempted on the PR this run opened"
    assert_contains "$log" "native auto-merge did not arm" "auto-merge: its refusal is reported, not hidden"
    assert_not_contains "$log" "2 failed ===" "auto-merge: a refusal to arm is not counted as a failure"

    rm -rf "$SWEEP_BARE" "$SWEEP_PR_DIR"
}

# ── Test 8i3: the merge is pinned to the commit that was checked ──────────
#
# The gate in pr_merge_verdict() judges a SNAPSHOT, and the merge goes out
# afterwards. Everything the gate refuses — a second file in the diff, a red
# check, a stranger's authorship — arrives on the branch by a push, and a push
# can land in that gap. --match-head-commit is what makes the two one
# decision. Both halves are tested, because a flag that is silently absent
# leaves a green suite that proves nothing.
test_bump_sweep_head_match() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (the merge is pinned to the checked commit) ==="

    local before_sha log prlog before_lines

    # ── The head moved between the check and the merge: nothing lands.
    setup_sweep_repos
    before_sha=$(sweep_main_sha repo-zz-ready)
    BUMP_HEAD_MOVES_FOR_RUN="bumporg_repo-zz-ready" \
        run_sweep "$TEST_DIR/sweep-moved.txt"
    unset BUMP_HEAD_MOVES_FOR_RUN
    log="$TEST_DIR/sweep-moved.txt"

    assert_contains "$log" "bumporg/repo-zz-ready#111: merge was refused" "head match: a head that moved after the check is refused, not merged"
    assert_contains "$log" "Head branch was modified" "head match: the refusal says the head moved"
    if [[ "$(sweep_main_sha repo-zz-ready)" == "$before_sha" ]]; then
        pass "head match: the default branch did not move"
    else
        fail "head match: the default branch did not move — an unchecked diff landed"
    fi
    # The rest of the sweep is unaffected: one repo's refusal is one repo's.
    assert_contains "$log" "bumporg/repo-nochecks#106: MERGED with a merge commit" "head match: other repos still merge"

    # ── An oid that is not a sha: refuse. "Cannot pin it" is not permission
    # to merge it unpinned — the same rule the verdict itself follows.
    setup_sweep_repos
    before_sha=$(sweep_main_sha repo-zz-ready)
    BUMP_HEAD_GARBLED_FOR_RUN="bumporg_repo-zz-ready" \
        run_sweep "$TEST_DIR/sweep-garbled.txt"
    unset BUMP_HEAD_GARBLED_FOR_RUN
    log="$TEST_DIR/sweep-garbled.txt"
    assert_contains "$log" "bumporg/repo-zz-ready#111: its head commit did not read back as a sha" "head match: an unreadable head commit is refused, with the reason"
    assert_not_contains "$log" "bumporg/repo-zz-ready#111: MERGED" "head match: an unreadable head commit is not merged unpinned"
    if [[ "$(sweep_main_sha repo-zz-ready)" == "$before_sha" ]]; then
        pass "head match: the default branch did not move on an unreadable head"
    else
        fail "head match: the default branch did not move on an unreadable head"
    fi

    # ── A gh too old to have the flag: degrade, say so, still merge.
    setup_sweep_repos
    before_lines=$(wc -l < "$BUMP_PR_LOG")
    BUMP_NO_MATCH_FLAG_FOR_RUN=1 run_sweep "$TEST_DIR/sweep-noflag.txt"
    unset BUMP_NO_MATCH_FLAG_FOR_RUN
    log="$TEST_DIR/sweep-noflag.txt"
    prlog="$TEST_DIR/sweep-noflag-prlog.txt"
    tail -n +$((before_lines + 1)) "$BUMP_PR_LOG" > "$prlog"

    assert_contains "$log" "this gh has no 'gh pr merge --match-head-commit'" "head match: an older gh is reported, not assumed"
    assert_contains "$log" "bumporg/repo-zz-ready#111: MERGED with a merge commit" "head match: an older gh still merges — the guard is hardening, not a dependency"
    assert_not_contains "$prlog" "--match-head-commit" "head match: the flag is not passed to a gh that would reject it"
}

# Everything above drives mock repos through the scripts. These five read the
# REAL files in this checkout, because nothing else does: this repo is dropped
# from both `sync.sh` and `drift-report.sh` (`grep -v "/${SELF_REPO}$"`), so
# neither the delivery that repairs a consumer nor the report that flags one
# can see anything here. Where the fleet has two mechanisms watching it, this
# repo has the suite.

# Reads a YAML document with the same real parser scripts/check-cron-coverage.js
# uses, printing one line per array element. Never a grep: a line scan matches
# its needle wherever the bytes happen to sit — inside a comment, under a
# different key, in a block the file no longer uses — and so reads clean on
# exactly the structure it cannot see. A missing parser FAILS rather than
# skips; these files have no other check. CI installs it with `npm ci`.
yaml_field() {   # <file> <dotted-path>
    if [[ ! -d "$REPO_ROOT/node_modules/yaml" ]]; then
        echo "node_modules/yaml is missing — run \`npm ci\` first" >&2
        return 1
    fi
    node -e '
const fs = require("node:fs");
const YAML = require(process.argv[1] + "/node_modules/yaml");
const file = process.argv[2];
const path = process.argv[3];
let cursor = YAML.parse(fs.readFileSync(file, "utf8"));
for (const key of path.split(".")) {
  const isObj = cursor && typeof cursor === "object";
  // `on:` parses to the string key "on" under YAML 1.2 core (what `yaml`
  // uses) and folds to boolean true under 1.1. Accept both, exactly as
  // check-cron-coverage.js does, so a trigger block can never be missed
  // because of spec version.
  const actual = isObj && !(key in cursor) && key === "on" && "true" in cursor
    ? "true" : key;
  if (!isObj || !(actual in cursor)) {
    console.error(`${file} has no ${path}`);
    process.exit(1);
  }
  cursor = cursor[actual];
}
for (const v of (Array.isArray(cursor) ? cursor : [cursor])) console.log(String(v));
' "$REPO_ROOT" "$1" "$2"
}

# ── Test 7a: sync.yml fires on every file that decides what a run does ────
#
# THE BUG THIS PINS. sync.sh reads its policy out of this checkout while it
# runs, and most of that policy is in repos.yml — the exclusion list, the
# skills-bootstrap allowlist, the hook's pinned ref and its sha256. For as
# long as `paths:` did not name repos.yml, a pure repos.yml commit produced
# ZERO sync runs: the fleet's declared state changed, nothing applied it, and
# the diff read as shipped. That made ADR 0001's "fanning a hook security fix
# across the fleet is a reviewable one-line pin bump" false as written.
#
# The expected set is DERIVED from sync.sh, never typed out here. A hand-kept
# list reproduces the same bug one level up: it passes while naming a SUBSET,
# so the entries a future editor is likeliest to prune as noise — the per-repo
# helpers — would be exactly the ones nothing asserts. Deriving means adding a
# helper to sync.sh makes this test demand its `paths:` entry, with no second
# edit to remember.
test_sync_workflow_trigger() {
    echo ""
    echo "=== Test: sync.yml triggers on the files that decide a run ==="

    local paths_file="$TEST_DIR/sync-yml-paths.txt"
    local err_file="$TEST_DIR/sync-yml-paths.err"
    if ! yaml_field "$REPO_ROOT/.github/workflows/sync.yml" "on.push.paths" \
            > "$paths_file" 2> "$err_file"; then
        fail "sync trigger: could not read on.push.paths — $(head -1 "$err_file")"
        return
    fi

    # Every file sync.sh resolves out of this checkout at runtime, read off
    # sync.sh itself: `$SCRIPT_DIR/<helper>` and `$REPO_ROOT/<file>`. The
    # leading-alphanumeric character class is what keeps `$SCRIPT_DIR/..` — the
    # REPO_ROOT computation — from being derived as a watched path. `|| true`
    # on each: no match is grep's exit 1, and under `set -euo pipefail` an
    # empty derivation would abort the suite instead of failing the floor
    # check below, which is the thing that can actually explain it.
    local helpers_file="$TEST_DIR/sync-yml-helpers.txt"
    local roots_file="$TEST_DIR/sync-yml-roots.txt"
    grep -oE '\$SCRIPT_DIR/[A-Za-z0-9][A-Za-z0-9._-]*' "$REPO_ROOT/scripts/sync.sh" \
        | sed 's#\$SCRIPT_DIR/#scripts/#' > "$helpers_file" || true
    grep -oE '\$REPO_ROOT/[A-Za-z0-9][A-Za-z0-9._-]*' "$REPO_ROOT/scripts/sync.sh" \
        | sed 's#\$REPO_ROOT/##' > "$roots_file" || true

    # A derivation that quietly matched nothing would satisfy every assertion
    # below and prove nothing at all, so each half has to have found SOMETHING
    # before its result is trusted. Checked as a shape rather than a count: how
    # many helpers sync.sh has is allowed to change, and a count here would
    # then fail in the one place whose message cannot explain it.
    local half
    for half in "$helpers_file" "$roots_file"; do
        if [[ ! -s "$half" ]]; then
            fail "sync trigger: derived no paths from scripts/sync.sh ($(basename "$half")) — it no longer resolves files through \$SCRIPT_DIR/\$REPO_ROOT, so the derivation broke, not the filter"
            return
        fi
    done

    # The three no derivation can see, each for its own reason: the entrypoint
    # never references itself; `agents-md/**` is read one level down, by
    # build-agents-md.sh; and the workflow carries `SYNC_OWNERS`, so it decides
    # WHICH ~20 repos a run touches while — without its own entry — firing no
    # run to apply that decision.
    local want_file="$TEST_DIR/sync-yml-want.txt"
    cat "$helpers_file" "$roots_file" > "$want_file"
    printf '%s\n' "scripts/sync.sh" "agents-md/**" ".github/workflows/sync.yml" \
        >> "$want_file"
    sort -u -o "$want_file" "$want_file"

    # Exact-line matching, not substring: "repos.yml" is a substring of a
    # dozen plausible paths, and a filter naming one of those would satisfy a
    # looser check while watching the wrong file.
    local want
    while IFS= read -r want; do
        [[ -n "$want" ]] || continue
        if grep -qxF -- "$want" "$paths_file"; then
            pass "sync trigger: on.push.paths names $want"
        else
            fail "sync trigger: on.push.paths does not name $want — editing it would change what the sync does to the fleet with no run to apply the change"
        fi
    done < "$want_file"
}

# ── Test 7b: the self-hosted hook matches the pin in repos.yml ────────────
#
# This repo cannot RECEIVE the hook (see the group header), so the two
# mechanisms that keep a consumer's copy honest — sync.sh overwriting a
# drifted hook, drift-report.sh printing `drifted` — are both blind here. This
# assertion and test 7d's are the whole of the guard, on a file that fetches
# and executes instruction text at session start with no approval prompt: this
# one covers the BYTES, 7d covers whether they ever run and whether they have
# a lock to act on. It compares bytes on disk against the digest in repos.yml
# and fetches nothing, so it is as offline and deterministic as the rest of
# the suite.
test_self_hosted_hook_pin() {
    echo ""
    echo "=== Test: the self-hosted skills-bootstrap hook matches the pin ==="

    local hook="$REPO_ROOT/.claude/hooks/skills-bootstrap.sh"
    local pinned registry ref hook_path actual
    if ! pinned=$(yaml_field "$REPO_ROOT/repos.yml" "skills_bootstrap.sha256" 2>&1); then
        fail "self-hosted hook: $pinned"
        return
    fi
    registry=$(yaml_field "$REPO_ROOT/repos.yml" "skills_bootstrap.registry" 2>/dev/null || echo "the registry")
    ref=$(yaml_field "$REPO_ROOT/repos.yml" "skills_bootstrap.ref" 2>/dev/null || echo "<ref>")
    hook_path=$(yaml_field "$REPO_ROOT/repos.yml" "skills_bootstrap.path" 2>/dev/null || echo "<path>")

    # Both remedies named, because which one is right depends on intent and the
    # suite cannot know it: a stale copy is re-fetched, deliberately newer bytes
    # mean the pin moves (and moves for the whole fleet with it).
    local remedy="re-copy it (\`git -C <clone of $registry> show $ref:$hook_path\`), or bump skills_bootstrap.ref + sha256 in repos.yml if the newer bytes are the intended ones"

    if [[ ! -f "$hook" ]]; then
        fail "self-hosted hook: $hook is missing — $remedy"
        return
    fi

    actual=$(sha256sum "$hook" | cut -d' ' -f1)
    if [[ "$actual" == "$pinned" ]]; then
        pass "self-hosted hook: sha256 equals repos.yml's skills_bootstrap.sha256"
    else
        fail "self-hosted hook: $hook hashes to ${actual:0:12}… but repos.yml pins ${pinned:0:12}… — $remedy"
    fi
}

# ── Test 7c: nothing unreachable is bootstrap-allowlisted ────────────────
#
# Both sync.sh and drift-report.sh drop repos BEFORE the per-repo loop that
# consults the allowlist, and neither ever says an allowlist entry was
# ignored. So a name dropped by one of those filters is not an overridden
# decision, it is an invisible one: the entry looks like delivery was chosen
# and nothing will ever act on it. Two filters drop names, and the allowlist
# has to stay clear of BOTH:
#
#   * `exclude:` — checked repo by repo; a run prints "excluded by repos.yml"
#     for the repo and nothing about the allowlist.
#   * `$SELF_REPO` — dropped with the same pre-loop `grep -v "/${SELF_REPO}$"`
#     in both scripts (ADR 0004 fact 5). THIS repo can therefore never be
#     delivered to and never appears in the drift report's bootstrap table; an
#     entry for it would only tell the next reader that the sync maintains
#     this repo's hook, which is the belief that lets a stale copy sit. It
#     self-hosts instead, guarded by 7b and 7d.
#
# Kept honest here because the scripts cannot — check-cron-coverage.js refuses
# a fleet/out_of_scope overlap outright, and these are the same contradiction
# under keys that have no such refusal.
test_bootstrap_allowlist_disjoint() {
    echo ""
    echo "=== Test: exclude: and skills_bootstrap.repos do not overlap ==="

    local excluded_file="$TEST_DIR/repos-excluded.txt"
    local allowed_file="$TEST_DIR/repos-allowlisted.txt"
    local err_file="$TEST_DIR/repos-keys.err"
    if ! yaml_field "$REPO_ROOT/repos.yml" "exclude" > "$excluded_file" 2> "$err_file" \
       || ! yaml_field "$REPO_ROOT/repos.yml" "skills_bootstrap.repos" > "$allowed_file" 2>> "$err_file"; then
        fail "allowlist overlap: could not read repos.yml — $(head -1 "$err_file")"
        return
    fi

    # Intersected with grep inside an `if`, never as a bare command: no match
    # is grep's exit 1, and under `set -euo pipefail` the empty — i.e. PASSING
    # — case would abort the whole suite before it could report anything.
    local overlap="" repo
    while IFS= read -r repo; do
        [[ -n "$repo" ]] || continue
        if grep -qxF -- "$repo" "$excluded_file"; then
            overlap="${overlap:+$overlap, }$repo"
        fi
    done < "$allowed_file"

    if [[ -z "$overlap" ]]; then
        pass "allowlist overlap: no repo is both excluded and bootstrap-allowlisted"
    else
        fail "allowlist overlap: $overlap is in both exclude: and skills_bootstrap.repos — exclusion is applied first and silently wins, so the allowlist entry can only ever be a no-op that reads as a decision"
    fi

    # Read out of sync.sh rather than written here, so renaming this repo does
    # not leave the assertion guarding a name nothing uses any more.
    local self_repo
    self_repo=$(sed -n 's/^SELF_REPO="\${SYNC_SELF_REPO:-\(.*\)}"$/\1/p' \
        "$REPO_ROOT/scripts/sync.sh")
    if [[ -z "$self_repo" ]]; then
        fail "allowlist overlap: could not read SELF_REPO's default out of scripts/sync.sh — the derivation broke, not the allowlist"
    elif grep -qxF -- "$self_repo" "$allowed_file"; then
        fail "allowlist overlap: $self_repo is this repo, dropped by both scripts before the allowlist is consulted (grep -v \"/\${SELF_REPO}\$\") — the entry cannot deliver anything or be reported on, and it tells the next reader the sync maintains this repo's hook, which it cannot"
    else
        pass "allowlist overlap: this repo ($self_repo) is not bootstrap-allowlisted"
    fi
}

# ── Test 7d: the self-hosted hook is registered, and has a lock to read ───
#
# 7b proves the hook's BYTES are the reviewed ones. It says nothing about
# whether they ever run, or whether they can do anything when they do — and
# the other two thirds of this repo's adoption fail silently, in opposite
# directions:
#
#   * Claude Code runs the hook ONLY if `.claude/settings.json` names it in a
#     SessionStart entry. Drop the registration and the hook sits there,
#     byte-perfect and dead, and no session ever says so — the exact case
#     bootstrap-status.sh exists to classify for consumers.
#   * With no `skills.lock` the hook runs and installs nothing: it prints
#     `skills: DEGRADED — no skills.lock found` into EVERY ephemeral session,
#     forever. repos.yml calls that out as worse than inert, because nothing
#     revisits it.
#
# Neither file is reachable by sync.sh or drift-report.sh here (see the group
# header), so without this both could be deleted with CI fully green. Offline
# and deterministic: the repo's own classifier plus a stdlib JSON read.
test_self_hosted_registration() {
    echo ""
    echo "=== Test: the self-hosted hook is registered and has a lock ==="

    local settings="$REPO_ROOT/.claude/settings.json"
    local lock="$REPO_ROOT/skills.lock"
    local state

    # The same classifier sync.sh and drift-report.sh use on consumers, so
    # "registered" means here exactly what it means in the drift report — a
    # second opinion hand-rolled in this file could disagree with the fleet's.
    if [[ ! -f "$settings" ]]; then
        fail "self-hosted registration: $settings is missing — the hook is delivered but nothing runs it; regenerate with scripts/register-bootstrap-hook.sh"
    else
        state=$("$REPO_ROOT/scripts/bootstrap-status.sh" "$settings")
        if [[ "$state" == "registered" ]]; then
            pass "self-hosted registration: .claude/settings.json registers the hook"
        else
            fail "self-hosted registration: bootstrap-status.sh reads '$state', not 'registered' — the hook would never run; re-run scripts/register-bootstrap-hook.sh"
        fi
    fi

    # Only the keys the hook itself requires before it can install anything:
    # a `registry`/`ref` to fetch from, at least one bundle to fetch, and a
    # `skills` object of digests to verify against. Not a schema check and not
    # a currency check — `generate_skills_lock.py --check`, in the registry,
    # owns whether the digests still match the pinned ref, and it needs the
    # network this suite must not touch.
    if [[ ! -f "$lock" ]]; then
        fail "self-hosted registration: $lock is missing — the hook would print 'skills: DEGRADED — no skills.lock found' into every ephemeral session"
        return
    fi
    local lock_err
    if lock_err=$(python3 -c '
import json, sys

with open(sys.argv[1], encoding="utf-8") as handle:
    try:
        lock = json.load(handle)
    except Exception as exc:
        sys.exit("not valid JSON (%s)" % exc.__class__.__name__)

if not isinstance(lock, dict):
    sys.exit("top level is not an object")
for key in ("registry", "ref"):
    if not isinstance(lock.get(key), str) or not lock[key].strip():
        sys.exit("%s is missing or empty" % key)
if not isinstance(lock.get("bundles"), list) or not lock["bundles"]:
    sys.exit("bundles is missing or empty")
if not isinstance(lock.get("skills"), dict) or not lock["skills"]:
    sys.exit("skills is missing or empty")
' "$lock" 2>&1); then
        pass "self-hosted registration: skills.lock carries the keys the hook reads"
    else
        fail "self-hosted registration: skills.lock is unusable — $lock_err"
    fi
}

# workflow_steps <file> — one line per job step: "<uses> <repository> <fetch-depth>",
# with "-" for a field the step does not set. The same real parser yaml_field
# uses, for the same reason: `uses:` inside a comment, or under a key the file
# no longer reads, is bytes a grep matches and a parser does not.
workflow_steps() {
    if [[ ! -d "$REPO_ROOT/node_modules/yaml" ]]; then
        echo "node_modules/yaml is missing — run \`npm ci\` first" >&2
        return 1
    fi
    node -e '
const fs = require("node:fs");
const YAML = require(process.argv[1] + "/node_modules/yaml");
const doc = YAML.parse(fs.readFileSync(process.argv[2], "utf8"));
for (const spec of Object.values((doc && doc.jobs) || {})) {
  // A job-level `uses:` is a reusable-workflow call, pinned by the same rule.
  if (spec && typeof spec.uses === "string") console.log([spec.uses, "-", "-"].join(" "));
  for (const step of (spec && spec.steps) || []) {
    const w = step.with || {};
    console.log([step.uses || "-", w.repository || "-",
                 w["fetch-depth"] === undefined ? "-" : String(w["fetch-depth"])].join(" "));
  }
}
' "$REPO_ROOT" "$1"
}

# workflow_shape <file> — a fact per line about a workflow's TRIGGERS, its
# JOBS, and every place a `concurrency:` key appears:
#
#   trigger <name>            one per top-level key under `on:`
#   job <name>                one per key under `jobs:`
#   concurrency workflow      the workflow-level key, if it is there at all
#   concurrency job:<name>    a job-level one, if it is there at all
#
# The same real parser workflow_steps uses, and here the reason is sharper than
# usual: the thing being asserted is an ABSENCE. A line scanner that "finds no
# concurrency" cannot tell a file that has none from a file it mis-read — a
# `concurrency:` under a key the parser folds differently, or one it never
# reached because `jobs:` moved. So the trigger and job lines are emitted
# whether or not anything is wrong, which is what lets a caller distinguish
# "examined this file and found no concurrency" from "examined nothing".
workflow_shape() {
    if [[ ! -d "$REPO_ROOT/node_modules/yaml" ]]; then
        echo "node_modules/yaml is missing — run \`npm ci\` first" >&2
        return 1
    fi
    node -e '
const fs = require("node:fs");
const YAML = require(process.argv[1] + "/node_modules/yaml");
const doc = YAML.parse(fs.readFileSync(process.argv[2], "utf8"));
if (!doc || typeof doc !== "object") {
  console.error(process.argv[2] + " does not parse to a mapping");
  process.exit(1);
}
// `on:` is the string key "on" under YAML 1.2 core (what `yaml` uses) and
// folds to boolean true under 1.1. Accept both, exactly as yaml_field and
// check-cron-coverage.js do, so a trigger block is never missed over a spec
// version. `on: push` and `on: [push, pull_request]` are legal too, and both
// enumerate here rather than reporting nothing.
const on = "on" in doc ? doc.on : doc[true];
if (typeof on === "string") console.log("trigger " + on);
else for (const name of (Array.isArray(on) ? on : Object.keys(on || {}))) {
  console.log("trigger " + String(name));
}
if ("concurrency" in doc) console.log("concurrency workflow");
for (const [name, spec] of Object.entries((doc && doc.jobs) || {})) {
  console.log("job " + name);
  if (spec && typeof spec === "object" && "concurrency" in spec) {
    console.log("concurrency job:" + name);
  }
}
' "$REPO_ROOT" "$1"
}

# ── Test 7e: the lock-bump workflow says what the script needs ────────────
#
# Read off the REAL file in this checkout, like the four tests above it,
# because nothing syncs or reports on this repo. Three of these are not style:
#
#   * every `uses:` is a 40-hex commit pin (fleet rule — a tag is a movable
#     pointer, and this job holds a token that can push to ~20 repos);
#   * the registry checkout is `fetch-depth: 0`, because --repin proves the
#     checkout IS the registry by finding the commit the lock already pins,
#     and a shallow clone does not contain it;
#   * the job can actually fire, and publishes no status context — which is
#     what puts its `concurrency:` group on the safe side of the rule in
#     AGENTS.md about required checks.
test_bump_workflow() {
    echo ""
    echo "=== Test: skills-lock-bump.yml is pinned, scheduled and scoped ==="

    local wf="$REPO_ROOT/.github/workflows/skills-lock-bump.yml"
    local steps_file="$TEST_DIR/bump-workflow-steps.txt"
    local err_file="$TEST_DIR/bump-workflow.err"

    if ! workflow_steps "$wf" > "$steps_file" 2> "$err_file"; then
        fail "bump workflow: could not parse $wf — $(head -1 "$err_file")"
        return
    fi
    if [[ -s "$steps_file" ]]; then
        pass "bump workflow: parses, and declares at least one step"
    else
        fail "bump workflow: parses, and declares at least one step — it declares none, so every assertion below would be vacuous"
        return
    fi

    # Exact form, not "contains 40 hex": `@v4  # 34e1148…` would satisfy a
    # looser check while pinning a movable tag.
    local uses unpinned=""
    while read -r uses _ _; do
        [[ "$uses" == "-" ]] && continue
        if [[ ! "$uses" =~ ^[^@[:space:]]+@[0-9a-f]{40}$ ]]; then
            unpinned="${unpinned:+$unpinned, }$uses"
        fi
    done < "$steps_file"
    if [[ -z "$unpinned" ]]; then
        pass "bump workflow: every uses: is pinned to a full 40-character commit SHA"
    else
        fail "bump workflow: not pinned to a 40-character SHA — $unpinned"
    fi

    # The other half of the same house rule, and the one half a parser cannot
    # see: AGENTS.md makes the trailing `# vX.Y.Z (date)` part of the pin —
    # forty hex characters say nothing on their own, the version says what it
    # is and the date says how stale it is. Comments are not in the YAML data
    # model, so this reads raw lines; the day is optional because the account
    # holds two different day-level records for one of these SHAs and this
    # file is not the place to pick between them. What is asserted is that a
    # pin carries a version AND a date at all, which is what a bare `# v3.2.0`
    # was missing.
    local pin_comment='@[0-9a-f]{40}[[:space:]]+#[[:space:]]*v[0-9][^[:space:]]*[[:space:]]+\([0-9]{4}(-[0-9]{2}){1,2}\)[[:space:]]*$'
    local uses_line ref undated=""
    while IFS= read -r uses_line; do
        ref="${uses_line#*uses:}"
        ref="${ref#"${ref%%[![:space:]]*}"}"
        # Nothing to pin, and nothing to date.
        [[ "$ref" == ./* || "$ref" == docker://* ]] && continue
        if [[ ! "$uses_line" =~ $pin_comment ]]; then
            undated="${undated:+$undated; }${uses_line#"${uses_line%%[![:space:]]*}"}"
        fi
    done < <(grep -E '(^|[[:space:]])uses:' "$wf")
    if [[ -z "$undated" ]]; then
        pass "bump workflow: every pin carries its version and date comment"
    else
        fail "bump workflow: a pin has no '# vX.Y.Z (date)' comment — $undated"
    fi

    local registry_depth
    registry_depth=$(awk '$2 == "Adam-S-Daniel/agentskills" { print $3 }' "$steps_file")
    if [[ "$registry_depth" == "0" ]]; then
        pass "bump workflow: the registry is checked out at full depth"
    else
        fail "bump workflow: the registry needs fetch-depth: 0 (got '${registry_depth:-none}') — --repin looks for the commit the lock already pins, and a shallow clone does not have it"
    fi

    local field
    for field in "on.schedule.0.cron:41 7 * * *" \
                 "on.workflow_dispatch.inputs.dry_run.type:boolean" \
                 "permissions.contents:read" \
                 "concurrency.group:skills-lock-bump" \
                 "concurrency.cancel-in-progress:false"; do
        local path="${field%%:*}" want="${field#*:}" got
        if got=$(yaml_field "$wf" "$path" 2>/dev/null) && [[ "$got" == "$want" ]]; then
            pass "bump workflow: $path is $want"
        else
            fail "bump workflow: $path should be '$want', got '${got:-nothing}'"
        fi
    done

    # No pull_request trigger, so this job can never publish a required status
    # context — the whole reason a concurrency group is safe here.
    if yaml_field "$wf" "on.pull_request" >/dev/null 2>&1; then
        fail "bump workflow: it now has a pull_request trigger, so its concurrency group can cancel a run that publishes a status context — read the AGENTS.md rule about required checks before keeping both"
    else
        pass "bump workflow: no pull_request trigger, so no status context a cancelled run could poison"
    fi
}

# ── Test 7f: check-agents-md.sh catches a doubled managed block ───────────
#
# THE BUG THIS PINS. AGENTS.md carried two managed blocks — two, sometimes
# contradictory, copies of the skills-ecosystem rule — from c86465f through
# 7b87581, because something split it on the first OCCURRENCE of the marker
# substring instead of the marker LINE, and the managed block's own BEGIN
# header quotes the marker verbatim (`DO NOT EDIT ABOVE "## Repo-specific
# additions"`). CI's staleness check (diff against build-agents-md.sh output)
# never caught it: the doubled file is a FIXED POINT of the regen recipe, so
# it kept regenerating to itself. check-agents-md.sh is the structural check
# that can tell the difference — these fixtures are built by hand rather than
# through sync.sh/build-agents-md.sh, so each one isolates a single way the
# structure can go wrong without needing a mock repo to produce it.
test_check_agents_md() {
    echo ""
    echo "=== Test: check-agents-md.sh (structural AGENTS.md validator) ==="

    local script="$REPO_ROOT/scripts/check-agents-md.sh"
    local fixture out exit_code

    # -- a well-formed file passes --
    fixture="$TEST_DIR/check-agents-md-wellformed.md"
    cat > "$fixture" <<'EOF'
<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
> **Managed by [`_agent-guidance`].**
some managed content
<!-- END MANAGED SECTION -->
## Repo-specific additions
some repo-specific text
EOF
    out="$TEST_DIR/check-agents-md-wellformed.out"
    exit_code=0
    "$script" "$fixture" > "$out" 2>&1 || exit_code=$?
    if [[ $exit_code -eq 0 ]]; then
        pass "check-agents-md: a well-formed file passes"
    else
        fail "check-agents-md: a well-formed file passes — exit $exit_code: $(cat "$out")"
    fi

    # -- two full managed blocks fails --
    fixture="$TEST_DIR/check-agents-md-doubled.md"
    cat > "$fixture" <<'EOF'
<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
> **Managed by [`_agent-guidance`].**
fresh managed content
<!-- END MANAGED SECTION -->
<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
> **Managed by [`_agent-guidance`].**
stale managed content
<!-- END MANAGED SECTION -->
## Repo-specific additions
some repo-specific text
EOF
    out="$TEST_DIR/check-agents-md-doubled.out"
    exit_code=0
    "$script" "$fixture" > "$out" 2>&1 || exit_code=$?
    if [[ $exit_code -eq 1 ]]; then
        pass "check-agents-md: a file with two managed blocks fails"
    else
        fail "check-agents-md: a file with two managed blocks fails — exit $exit_code: $(cat "$out")"
    fi

    # -- the truncated BEGIN-header fragment fails, and is named by name --
    # This is the actual shape the c86465f corruption left behind: one BEGIN
    # (the first-occurrence split anchored ON the header, so it was consumed
    # into the "preserved" tail rather than duplicated itself), two ENDs and
    # two "Managed by"s (one pair per block), and the header's own closing
    # `" -->` stranded on a line that otherwise starts exactly like the real
    # marker.
    fixture="$TEST_DIR/check-agents-md-fingerprint.md"
    cat > "$fixture" <<'EOF'
<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
> **Managed by [`_agent-guidance`].**
new managed content
<!-- END MANAGED SECTION -->
## Repo-specific additions" -->
> **Managed by [`_agent-guidance`].**
old repo-specific text
<!-- END MANAGED SECTION -->
## Repo-specific additions
new repo-specific text
EOF
    out="$TEST_DIR/check-agents-md-fingerprint.out"
    exit_code=0
    "$script" "$fixture" > "$out" 2>&1 || exit_code=$?
    if [[ $exit_code -eq 1 ]]; then
        pass "check-agents-md: the truncated fragment line fails"
    else
        fail "check-agents-md: the truncated fragment line fails — exit $exit_code: $(cat "$out")"
    fi
    assert_contains "$out" "truncated-header fingerprint" \
        "check-agents-md: the truncated-fragment failure names the fingerprint by name"

    # -- zero markers fails --
    fixture="$TEST_DIR/check-agents-md-empty.md"
    cat > "$fixture" <<'EOF'
Just some prose. No managed section, no marker, nothing to preserve.
EOF
    out="$TEST_DIR/check-agents-md-empty.out"
    exit_code=0
    "$script" "$fixture" > "$out" 2>&1 || exit_code=$?
    if [[ $exit_code -eq 1 ]]; then
        pass "check-agents-md: a file with zero markers fails"
    else
        fail "check-agents-md: a file with zero markers fails — exit $exit_code: $(cat "$out")"
    fi

    # -- out-of-order markers fails --
    # One of each marker, so invariants 1-4 all read "exactly one" — only
    # invariant 6 (ordering) can catch this, which is the point: it proves
    # the ordering check does independent work rather than only ever firing
    # alongside a count failure.
    fixture="$TEST_DIR/check-agents-md-outoforder.md"
    cat > "$fixture" <<'EOF'
<!-- BEGIN MANAGED SECTION -->
> **Managed by [`_agent-guidance`].**
## Repo-specific additions
some repo-specific text
<!-- END MANAGED SECTION -->
EOF
    out="$TEST_DIR/check-agents-md-outoforder.out"
    exit_code=0
    "$script" "$fixture" > "$out" 2>&1 || exit_code=$?
    if [[ $exit_code -eq 1 ]]; then
        pass "check-agents-md: out-of-order markers fail"
    else
        fail "check-agents-md: out-of-order markers fail — exit $exit_code: $(cat "$out")"
    fi
    assert_contains "$out" "out of order" \
        "check-agents-md: the out-of-order failure names it as an ordering problem"
}

# ── Test 7g: ci.yml's trigger set, and the absence that makes it safe ─────
#
# THE CLAIM THIS PINS. ci.yml carries a `workflow_dispatch` trigger and a long
# comment arguing the addition is safe. That argument rests on two properties,
# and only one of them lives in another repo: that this workflow publishes no
# REQUIRED status context is a repo-settings fact (`required_status_checks:
# []`), asserted there; that this file has NO `concurrency:` block, at workflow
# or job level, is local and is the half a future tidy-up adds without reading
# why it was missing. With both a dispatch and a concurrency group, two events
# on one head sha can leave a cancelled run behind — the trap in AGENTS.md.
#
# Locked here because this repo's own convention says so and the precedent sits
# a few hundred lines up: test_bump_workflow asserts skills-lock-bump.yml's
# concurrency.group, its cancel-in-progress AND fails if a pull_request trigger
# ever appears — the mirror image of this file's requirement, since that
# workflow is safe to cancel precisely because no PR event can give it a
# context. Until this test, nothing in the suite read ci.yml at all: the file
# that decides whether every other assertion here even runs was the one file
# with no assertion on it.
#
# The trigger set is asserted EXACTLY rather than "contains workflow_dispatch".
# The comment declares three; a fourth arriving unread — `schedule`,
# `pull_request_target`, a `push` narrowed to one branch — changes which events
# can collide on a sha, and that collision is the entire subject of the
# argument the comment makes.
test_ci_workflow_shape() {
    echo ""
    echo "=== Test: ci.yml's triggers, and the concurrency block it must not have ==="

    local wf="$REPO_ROOT/.github/workflows/ci.yml"
    local shape="$TEST_DIR/ci-workflow-shape.txt"
    local err="$TEST_DIR/ci-workflow-shape.err"

    if ! workflow_shape "$wf" > "$shape" 2> "$err"; then
        fail "ci workflow: could not parse $wf — $(head -1 "$err")"
        return
    fi

    # VACUITY GUARD, and not a formality here: both assertions below are about
    # what a set does or does not contain, and the empty set satisfies "no
    # concurrency anywhere" perfectly. A parse that silently yielded nothing —
    # a `jobs:` key that moved, an `on:` this helper could not read — would
    # hand back a clean bill of health for a file it never looked at, which is
    # the exact failure the rest of this change set exists to close.
    local triggers jobs
    triggers=$(awk '$1 == "trigger" { print $2 }' "$shape" | sort | tr '\n' ' ' | sed 's/ $//')
    jobs=$(awk '$1 == "job" { print $2 }' "$shape" | sort | tr '\n' ' ' | sed 's/ $//')
    if [[ -n "$triggers" && -n "$jobs" ]]; then
        pass "ci workflow: parses, and declares at least one trigger and at least one job"
    else
        fail "ci workflow: parses, and declares at least one trigger and at least one job — got triggers '${triggers:-none}' and jobs '${jobs:-none}', so every assertion below would be vacuous"
        return
    fi

    if [[ "$triggers" == "pull_request push workflow_dispatch" ]]; then
        pass "ci workflow: its triggers are exactly push, pull_request and workflow_dispatch"
    else
        fail "ci workflow: its triggers should be exactly 'pull_request push workflow_dispatch', got '$triggers' — the comment above workflow_dispatch argues from WHICH events can produce a run for one head sha, so re-read it before adding or removing one"
    fi

    local found
    found=$(awk '$1 == "concurrency" { print $2 }' "$shape" | sort | tr '\n' ' ' | sed 's/ $//')
    if [[ -z "$found" ]]; then
        pass "ci workflow: no concurrency group, at workflow or job level"
    else
        fail "ci workflow: it now has a concurrency group ($found) — with workflow_dispatch on the same file, two events on one head sha can leave a cancelled run behind. Read the comment above the trigger, and AGENTS.md on required checks, before keeping both"
    fi
}

# ── Run all tests ──────────────────────────────────────────────────────────

echo "========================================="
echo "  Agent Guidance Integration Tests"
echo "========================================="

setup_mock_repos
setup_bootstrap_repos
setup_bump_repos
create_mock_gh
snapshot_bare_repos
test_build_script
test_bridge_status
test_bootstrap_status
test_register_bootstrap_hook
test_sync_dry_run
test_sync_full
test_sync_protected_fallback
test_sync_stale_cleanup
test_sync_failure_exit_code
test_sync_round_trip_no_marker
# The skills-bootstrap lane mutates the bootorg bares in a fixed order:
# dry-run (writes nothing) → deliver → re-run (no-op) → drift → report →
# unmanaged report → bad digest → PR-body fallback. Each of the three that
# needs a clean slate resets the bares itself.
test_sync_bootstrap_dry_run
test_sync_bootstrap
test_sync_bootstrap_idempotent
test_sync_bootstrap_drift
test_drift_report_bootstrap
test_drift_report_cron_classification
test_drift_report_bootstrap_unmanaged
test_drift_report_bootstrap_registry
test_drift_report_probe_cleanup
test_sync_bootstrap_bad_digest
test_sync_bootstrap_pr_body
# The sync now direct-pushes to main; restore the pristine bares so the drift
# report observes the pre-sync baseline (test_sync_multi_owner resets itself).
reset_bare_repos
test_drift_report
test_sync_multi_owner
test_sync_per_owner_token
test_drift_report_multi_owner
test_check_cron_coverage
# The lock-bump lane, in its own mock org (bumporg) so nothing here disturbs
# the bares the sync and drift-report lanes share. Fixed order: the two runs
# that must write nothing at all (a mistyped flag, a generator too old) while
# no bump branch exists anywhere -> dry run -> bump -> re-run -> a deliberately
# misconfigured registry checkout. The last two stand their own fixture up in
# a MOCK_BARE_DIR of their own and tear it down again, so they can be a
# rejected push and a vanished bundle without those becoming standing state.
test_bump_unknown_argument
test_bump_generator_without_repin
test_bump_owner_list_failure
test_bump_dry_run
test_bump_missing_source_checkout
test_bump_consumer_locks
test_bump_idempotent
test_bump_shallow_registry
test_bump_push_rejected
test_bump_bundle_vanished
test_bump_format_gate_empty_skills
test_bump_digest_format_gate
test_bump_generator_without_check_format
test_bump_format_check_unreadable
# The sweep lane, in a bare dir and a PR fixture dir of its own: it MERGES,
# which is the one thing in this repo nothing else undoes. Dry run first, so
# "it merged nothing" is a statement about a run that had every chance to.
test_bump_sweep_dry_run
test_bump_sweep
test_bump_sweep_head_match
# This repo's own committed files, not the mock fleet — nothing syncs or
# reports on _agent-guidance, so these are the only checks they get.
test_sync_workflow_trigger
test_self_hosted_hook_pin
test_bootstrap_allowlist_disjoint
test_self_hosted_registration
test_bump_workflow
test_ci_workflow_shape
test_check_agents_md

echo ""
echo "========================================="
echo "  Results: $PASS passed, $FAIL failed"
echo "========================================="

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
