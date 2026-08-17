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
assert_contains() {
    if grep -qF "$2" "$1" 2>/dev/null; then pass "$3"; else fail "$3 — expected '$2' in $1"; fi
}
assert_not_contains() {
    if grep -qF "$2" "$1" 2>/dev/null; then fail "$3 — did not expect '$2' in $1"; else pass "$3"; fi
}
assert_row_contains() {
    if grep -F "$2" "$1" | grep -qF "$3"; then pass "$4"; else fail "$4 — expected '$3' in row '$2' of $1"; fi
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
YAML

    # Same registry and allowlist, but a WRONG digest — used by the
    # digest-mismatch test.
    sed 's/^  sha256: .*/  sha256: 00000000000000000000000000000000000000000000000000000000deadbeef/' \
        "$TEST_DIR/repos.yml" > "$TEST_DIR/repos-baddigest.yml"

    cd "$REPO_ROOT"
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
                # Parse --jq from remaining args. Return an "open" PR #42 for
                # repos whose clone-dir basename (owner_repo) is listed in
                # MOCK_OPEN_PR_REPOS — used by the stale-cleanup test; every
                # other repo has no open PRs, as before.
                shift 2
                jq_filter=$(parse_jq_filter "$@")
                json='[]'
                current_repo=$(basename "$PWD")
                for r in ${MOCK_OPEN_PR_REPOS:-}; do
                    if [[ "$r" == "$current_repo" ]]; then
                        json='[{"number":42}]'
                        break
                    fi
                done
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
                fi
                echo "https://github.com/mock/pr/1"
                ;;
            close)
                # gh pr close <number> --comment ... — log the closed number.
                echo "pr-closed $3" >> "${MOCK_PR_LOG:-/dev/null}"
                ;;
            merge)
                # gh pr merge <number> --auto --squash|--merge — always succeed,
                # logging the args so tests can assert --auto was requested.
                shift 2
                echo "pr-merged $*" >> "${MOCK_PR_LOG:-/dev/null}"
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
        if [[ "$rc" == "$2" ]] && grep -qF "$3" <<<"$out"; then
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
        if [[ "$rc" == "$2" ]] && grep -qF "$3" <<<"$out"; then
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
}

# ── Run all tests ──────────────────────────────────────────────────────────

echo "========================================="
echo "  Agent Guidance Integration Tests"
echo "========================================="

setup_mock_repos
setup_bootstrap_repos
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

echo ""
echo "========================================="
echo "  Results: $PASS passed, $FAIL failed"
echo "========================================="

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
