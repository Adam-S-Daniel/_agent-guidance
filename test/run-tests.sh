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

    # Central repos.yml fixture for this test run (NOT the real repo-root
    # repos.yml — tests must not depend on the real exclusion list).
    cat > "$TEST_DIR/repos.yml" <<'YAML'
exclude:
  - repo-excluded
default_sections:
  - rust
YAML

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
    # Hook installed AFTER the setup push so seeding main succeeds; only the
    # sync's later direct push is rejected.
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
    assert_contains "$TEST_DIR/build-output.md" "## General guidelines" "includes base content"
    assert_contains "$TEST_DIR/build-output.md" "## Python" "includes python section"
    assert_contains "$TEST_DIR/build-output.md" "## Docker" "includes docker section"
    assert_not_contains "$TEST_DIR/build-output.md" "## Go" "does not include unrequested section"

    # Test with no sections
    output=$("$REPO_ROOT/scripts/build-agents-md.sh")
    echo "$output" > "$TEST_DIR/build-no-sections.md"
    assert_contains "$TEST_DIR/build-no-sections.md" "Sections: none" "reports none when no sections"
    assert_contains "$TEST_DIR/build-no-sections.md" "## General guidelines" "still includes base"

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

    # A PR was created and auto-merge was enabled on it (the "--auto" flag).
    assert_contains "$pr_log" "pr-created" "protected: PR created on fallback"
    assert_contains "$pr_log" "pr-merged 1 --auto" "protected: auto-merge enabled on the fallback PR"

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

# ── Run all tests ──────────────────────────────────────────────────────────

echo "========================================="
echo "  Agent Guidance Integration Tests"
echo "========================================="

setup_mock_repos
create_mock_gh
snapshot_bare_repos
test_build_script
test_bridge_status
test_sync_dry_run
test_sync_full
test_sync_protected_fallback
test_sync_stale_cleanup
test_sync_failure_exit_code
test_sync_round_trip_no_marker
# The sync now direct-pushes to main; restore the pristine bares so the drift
# report observes the pre-sync baseline (test_sync_multi_owner resets itself).
reset_bare_repos
test_drift_report
test_sync_multi_owner
test_sync_per_owner_token
test_drift_report_multi_owner

echo ""
echo "========================================="
echo "  Results: $PASS passed, $FAIL failed"
echo "========================================="

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
