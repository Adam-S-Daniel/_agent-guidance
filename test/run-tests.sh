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

# Mirrors of sync.sh's delivery paths, so an assertion names the artifact it
# means rather than the directory several artifacts share.
HOOK_REL_PATH_T=".claude/hooks/skills-bootstrap.sh"
FLEET_HOOK_REL_PATH_T=".claude/hooks/fleet-memory.sh"
FLEET_PAYLOAD_REL_PATH_T=".claude/hooks/fleet-guidance.md"

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
# THE NEEDLE THAT CANNOT MATCH THE TEXT IT GUARDS, closed structurally. The
# two above are `grep -F` over a file, so a needle only ever matches inside ONE
# line — and everything they guard here is hard-wrapped: a PR body, a commit
# message, a block comment. A sentence that spans a wrap therefore matches
# nothing in either version of the file, which fails an `assert_contains`
# loudly and passes every `assert_not_contains` SILENTLY.
#
# Measured, on the assertion named after the sentence it forbids:
# `assert_not_contains "$msg" "so the pin does not move and"` guards a commit
# body reading "...so the primary's pin does not\nmove and every digest...".
# The realistic one-word regression — `sed "s/so the primary.s pin does not/so
# the pin does not/"` on the claims library — reproduces EXACTLY the forbidden
# sentence, and test_bump_format_and_federated came back 19 passed / 0 failed,
# EXIT 0. Only a two-part edit that also unwrapped the line reddened it.
#
# So these two flatten BOTH sides first: every run of whitespace becomes one
# space, and a comment marker opening a line is dropped, because neither is
# part of the sentence a reader would quote. A wrap can then no longer hide a
# claim, and a needle no longer has to be typed to the width of the file it
# greps. Use them for prose — a body, a title, a commit message, a comment —
# and keep `assert_contains` for anything where the line structure IS the
# thing under test (a bullet, a log line, a fenced command).
_flatten_prose() {   # <file> — writes that file back as one flat line
    sed -e 's/^[[:space:]]*#[[:space:]]\{0,1\}//' < "$1" | tr -s '[:space:]' ' '
}
assert_prose_contains() {
    local hay needle
    hay=$(_flatten_prose "$1")
    needle=$(printf '%s' "$2" | tr -s '[:space:]' ' ')
    if [[ "$hay" == *"$needle"* ]]; then pass "$3"; else fail "$3 — expected '$2' in $1, unwrapped"; fi
}
assert_prose_omits() {
    local hay needle
    hay=$(_flatten_prose "$1")
    needle=$(printf '%s' "$2" | tr -s '[:space:]' ' ')
    if [[ "$hay" == *"$needle"* ]]; then fail "$3 — did not expect '$2' in $1, unwrapped"; else pass "$3"; fi
}
# scoped_flag_pair — the library's own SCOPED_FLAG_PAIR, read out of the file
# rather than re-typed here. Every needle that guards a sentence about the two
# scoped flags is built from this, so a reworded pair moves the claim and its
# guard together instead of leaving a guard matching a string nothing prints —
# which is the failure this branch has been closing one instance at a time.
scoped_flag_pair() {
    bash -c 'source "$1"; printf "%s" "$SCOPED_FLAG_PAIR"' _ "$REPO_ROOT/scripts/lib/bump-pr-claims.sh"
}
# degraded_fed_remedy_text <listed-source-count> <primary registry> — the same,
# for the two arms of the degraded per-repo annotation's remedy. Derived for
# the direction that matters: the self-federating lane FORBIDS the actionable
# arm, and a re-typed negative needle goes green the moment the sentence is
# reworded — which, with a gate slipping at the same time, is the two-part
# edit this branch keeps meeting.
degraded_fed_remedy_text() {
    bash -c 'source "$1"; degraded_fed_remedy "$2" "$3"' _ \
        "$REPO_ROOT/scripts/lib/bump-pr-claims.sh" "$1" "$2"
}
# self_named_log_line_text <scoped true|false> <lock path> <registry> — and the
# same for the self-named log line, whose two branches a lane has to tell
# apart. log() output is the one artifact the cross product's CLOSURE check
# cannot account for claim by claim, so a hand-copied needle here has nothing
# behind it: reword the scoped branch and the assertion forbidding it in the
# DEGRADED lane passes over a run printing it.
self_named_log_line_text() {
    bash -c 'source "$1"; self_named_log_line "$2" "$3" "$4"' _ \
        "$REPO_ROOT/scripts/lib/bump-pr-claims.sh" "$1" "$2" "$3"
}
# assert_scoped_line <file> <anchor> <needle> <label> — <needle> must appear on
# a LINE THAT ALSO CARRIES <anchor>, never merely somewhere in the file.
#
# What it is guarding against is a "quotes the reason" assertion drifting into a
# "the reason appears in the log somewhere" assertion. These runs capture the
# script under test with `2>&1`, and the diagnostic being asserted about
# ORIGINATES in a subprocess — a stub `gh`, a pre-commit hook — so the moment
# anything lets that subprocess's stderr reach the log on its own, the needle is
# satisfied by the thing that printed it rather than by the script that was
# supposed to quote it.
#
# MEASURED, and worth writing down because it is the reassuring half: at each of
# the three call sites today the script captures the subprocess with `2>&1`
# INSIDE a command substitution (`commit_out=$(git commit ... 2>&1)`,
# `pr_list=$(gh pr list ... 2>&1)`, `encoded=$(gh api ... 2>&1)`), so nothing
# leaks and the unscoped needles were in fact still discriminating — each one
# was verified to fail against a script mutated to stop quoting its reason. The
# scoping is therefore a strengthening rather than a repair: it makes the
# assertion say what its label says, and it does not depend on that capture
# staying exactly where it is.
#
# An absent anchor FAILS rather than passing quietly: "no line said that" and
# "the line said it without the needle" are both this assertion being unable to
# establish its claim, and neither is the claim holding.
assert_scoped_line() {
    local file="$1" anchor="$2" needle="$3" label="$4" hits
    hits=$(grep -F -- "$anchor" "$file" 2>/dev/null) || hits=""
    if [[ -n "$hits" ]] && grep -qF -- "$needle" <<< "$hits"; then
        pass "$label"
    else
        fail "$label — no line of $file carries both '$anchor' and '$needle'"
    fi
}
# The exact negation, and it needs to be a helper for the same reason
# assert_row_lacks_cell does: the needle these legs forbid is a line the run
# legitimately PRINTS somewhere — a gh notice reaches the log on its own
# whenever the script under test is captured with `2>&1` — so an unscoped
# `assert_not_contains` could only ever fail. What is being asserted is not
# "the notice is absent" but "the notice is not the line the operator was
# handed", which is a claim about ONE line and has to be checked on that line.
#
# An absent anchor FAILS, exactly as above: "no line said that" is this
# assertion being unable to establish its claim, not the claim holding. That is
# the vacuity this whole family of helpers keeps being fixed for.
assert_scoped_line_lacks() {
    local file="$1" anchor="$2" needle="$3" label="$4" hits
    hits=$(grep -F -- "$anchor" "$file" 2>/dev/null) || hits=""
    if [[ -z "$hits" ]]; then
        fail "$label — no line of $file carries '$anchor', so this asserts nothing"
    elif grep -qF -- "$needle" <<< "$hits"; then
        fail "$label — a line of $file carrying '$anchor' also carries '$needle'"
    else
        pass "$label"
    fi
}
# repo_section <file> <header line> — the block a fleet walker prints for ONE
# repo: everything between its `=== <owner>/<repo> ===` header and the next
# `=== ` line (the following repo, or the run summary).
#
# It exists because `log() { echo "  $*"; }` puts NO repo name on a per-repo
# line — the name appears once, on the header — so any assertion that greps the
# whole log for a log line and then greps those hits for a repo name is
# matching nothing, whatever the script did. Narrow to the section first and
# the per-repo claim becomes observable.
repo_section() {
    awk -v hdr="$2" '
        $0 == hdr { inside = 1; next }
        inside && /^=== / { exit }
        inside { print }
    ' "$1"
}
# assert_scoped_probe_warnings <log> <count> <label> — how many of the two SOFT
# federated probes annotated this run. There are exactly two of them in
# bump-consumer-locks.sh, a `--only` probe and a `--repin-source` one, and each
# emits one `::warning::` when the generator does not refuse its flag the way
# the real one does. A stand-in carrying NEITHER flag therefore gets two, not
# one — which is what makes this a count rather than a presence check. The
# stub inventory states that number in prose; this is what stops it drifting.
assert_scoped_probe_warnings() {
    local n
    n=$(grep -cE "::warning::.*has no '--(check-current --only|repin --repin-source)" "$1" 2>/dev/null || true)
    if [[ "$n" == "$2" ]]; then pass "$3"; else fail "$3 — expected $2, got ${n:-0} in $1"; fi
}
# THE NEEDLE THAT MATCHES THE WRONG CELL, and it is the same defect as the two
# above one column over. `grep -F "$repo" | grep -qF "$needle"` narrows to the
# ROW and then stops narrowing, so the needle is free to match any cell in it —
# including the repo NAME, which every row carries twice (label and URL).
#
# Measured, on the three assertions that had nothing behind them:
#   * `repo-adopted` / `ok` — every row also carries `bridge-ok` in the
#     CLAUDE.md column, and `ok` is a substring of it. Substituting the
#     skills-bootstrap cell `| ok |` -> `| **drifted** |` and re-running the old
#     pipeline still matched. Those were the ONLY positive assertions in the
#     whole suite that this column ever produces `ok`, so nothing anywhere
#     asserted the happy path.
#   * `repo-no-lock` / `no-lock` — the row names `bootorg/repo-no-lock` twice,
#     so the needle matched the repo before it ever reached the cell.
#   * `repo-up-to-date-no-claude` / `up-to-date` — same shape a third time, and
#     worse: the fixture is NAMED after the status it is asserting. The test
#     one screen down already knew about this trap and spelled its own grep
#     `\*\*up-to-date\*\*` for exactly this reason; this helper did not.
#
# So it splits the matched row on `|`, trims each cell, and requires a cell to
# EQUAL the needle. A verdict cell is a whole cell — `ok`, `no-lock`,
# `**blocked**`, `?` — so exactness costs the callers nothing and is what makes
# `ok` stop matching `bridge-ok`. Note the needle must now carry the bolding
# the report writes (`**up-to-date**`, not `up-to-date`): the asterisks are in
# the cell, and a needle without them is asserting a cell that does not exist.
#
# Prose that ACCUMULATES belongs in assert_row_note_contains below, not here.
assert_row_contains() {
    local file="$1" key="$2" needle="$3" label="$4"
    local rows status=0 row
    rows=$(grep -F -- "$key" "$file") || status=$?
    # Three answers, not two: grep exits 2 when it could not read the file at
    # all, and folding that into "no such row" would report a missing report as
    # a wrong verdict — the same conflation the scripts under test are being
    # fixed for.
    if [[ $status -gt 1 ]]; then
        fail "$label — grep could not read $file (exit $status)"
        return
    fi
    if [[ $status -eq 1 ]]; then
        fail "$label — no row containing '$key' in $file"
        return
    fi
    while IFS= read -r row; do
        if _row_cell_equals "$row" "$needle"; then pass "$label"; return; fi
    done <<< "$rows"
    fail "$label — no cell of the row(s) matching '$key' in $file is exactly '$needle'"
}
_row_cell_equals() {   # <row> <needle> — some `|`-delimited cell equals <needle>
    local row="$1" needle="$2" cell
    local -a cells
    IFS='|' read -r -a cells <<< "$row"
    for cell in "${cells[@]}"; do
        cell="${cell#"${cell%%[![:space:]]*}"}"   # ltrim
        cell="${cell%"${cell##*[![:space:]]}"}"   # rtrim
        [[ "$cell" == "$needle" ]] && return 0
    done
    return 1
}
# assert_row_note_contains <file> <row-key> <needle> <label> — a substring of
# the row's LAST cell, which is Notes.
#
# Notes is the one column exactness would be wrong for: drift-report.sh builds
# it by appending (`notes="${notes:+$notes; }..."`), so a row can legitimately
# carry a lock summary AND a gitignore reason AND a fetch-failed list at once,
# and a whole-cell needle would have to be re-typed every time an unrelated
# clause joined it. Scoping to the Notes cell is what keeps the assertion real
# anyway: a needle here can no longer match the repo name, a verdict cell, or
# the legend rows further down the document.
assert_row_note_contains() {
    local file="$1" key="$2" needle="$3" label="$4"
    local rows status=0 row notes trimmed
    rows=$(grep -F -- "$key" "$file") || status=$?
    if [[ $status -gt 1 ]]; then
        fail "$label — grep could not read $file (exit $status)"
        return
    fi
    if [[ $status -eq 1 ]]; then
        fail "$label — no row containing '$key' in $file"
        return
    fi
    while IFS= read -r row; do
        trimmed="${row%"${row##*[![:space:]]}"}"   # rtrim, so the closing `|` is last
        trimmed="${trimmed%|}"
        notes="${trimmed##*|}"
        if [[ "$notes" == *"$needle"* ]]; then pass "$label"; return; fi
    done <<< "$rows"
    fail "$label — the Notes cell of the row(s) matching '$key' in $file does not contain '$needle'"
}
# The exact negation of assert_row_contains, and it needs to be a helper rather
# than an `assert_not_contains` over the whole document: every verdict this
# suite forbids in a cell — `missing`, `no-lock`, `**no-entry**` — is also a
# LEGEND row further down the same file, so a document-wide negative needle
# matches unconditionally and the assertion can only ever fail. That is the same
# vacuity assert_row_contains was just fixed for, inverted.
assert_row_lacks_cell() {
    local file="$1" key="$2" needle="$3" label="$4"
    local rows status=0 row
    rows=$(grep -F -- "$key" "$file") || status=$?
    if [[ $status -gt 1 ]]; then
        fail "$label — grep could not read $file (exit $status)"
        return
    fi
    if [[ $status -eq 1 ]]; then
        fail "$label — no row containing '$key' in $file, so this asserts nothing"
        return
    fi
    while IFS= read -r row; do
        if _row_cell_equals "$row" "$needle"; then
            fail "$label — a cell of the row matching '$key' in $file is exactly '$needle'"
            return
        fi
    done <<< "$rows"
    pass "$label"
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
    #
    # COMMITTED UNDER THE SYNC BOT'S OWN IDENTITY, and that is the whole point
    # of the fixture rather than a detail of it. sync.sh now force-pushes only
    # over commits it can see are its own — every commit the remote branch adds
    # to the default branch has to carry $SYNC_BOT_EMAIL — so the identity on
    # this commit is what decides which of two opposite behaviours this repo
    # exercises. The prose above already says what this branch IS ("from the
    # old PR-era", i.e. written by an earlier run of this same sync), and until
    # the guard shipped nothing made the fixture say it: the commit inherited
    # the suite's global `test-runner <test@localhost>`, which is a stranger.
    # Left that way it drove the REFUSAL path, and the four assertions below
    # about the force-push landing failed against a guard that was working
    # exactly as designed. The stranger's branch is a real case and gets its
    # own repo, in its own org, in setup_foreign_branch_repo.
    git checkout -b agents-md-sync/update >/dev/null 2>&1
    printf 'stale old sync content\n' > AGENTS.md
    git add AGENTS.md
    git -c user.name="agents-md-sync[bot]" \
        -c user.email="agents-md-sync[bot]@users.noreply.github.com" \
        commit -m "stale sync" >/dev/null 2>&1
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

    # Mock repo 13: fgnorg/repo-foreign-branch — the mirror image of repo 10,
    # and the reason repo 10's stale commit had to grow an explicit identity.
    #
    # Same shape: protected default branch, so the sync falls back to the PR
    # path, and a diverged agents-md-sync/update already sitting on the remote
    # so the fallback's push is non-fast-forward. The ONE difference is who
    # wrote the commit on that branch — here a human, at a `@users.noreply`
    # address that is not $SYNC_BOT_EMAIL.
    #
    # That difference is the whole invariant. The force-push used to be
    # justified as "the branch is bot-owned, this sync is its only writer",
    # which is not true on the cms-platform-managed repos: the fallback opens a
    # PR and arms auto-merge, so a maintainer can push a conflict resolution or
    # a reviewer-requested fix onto that branch, and the next nightly run
    # overwrote it and logged only "PR #N already exists — branch updated". Its
    # own org's guidance forbids exactly that ("it could discard reviewer
    # commits on an open PR"). A separate org rather than a third protorg repo
    # because this repo must FAIL, and protorg's test asserts "2 synced, 0
    # failed" — adding a failing repo there would move a count that is
    # asserting something else.
    local repo13_bare="$TEST_DIR/bare/fgnorg_repo-foreign-branch"
    local repo13_work="$TEST_DIR/work/repo-foreign-branch"
    mkdir -p "$repo13_bare" "$repo13_work"
    git init --bare --initial-branch=main "$repo13_bare" >/dev/null 2>&1
    git init --initial-branch=main "$repo13_work" >/dev/null 2>&1
    cd "$repo13_work"
    git config commit.gpgsign false
    git remote add origin "$repo13_bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - python
YAML
    git add .agents-sync.yml
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    git checkout -b agents-md-sync/update >/dev/null 2>&1
    printf 'a reviewer fixed the conflict by hand\n' > AGENTS.md
    git add AGENTS.md
    git -c user.name="A Reviewer" \
        -c user.email="reviewer@users.noreply.github.com" \
        commit -m "address review feedback on the sync PR" >/dev/null 2>&1
    git push origin HEAD:agents-md-sync/update >/dev/null 2>&1
    git checkout main >/dev/null 2>&1
    install_reject_main_hook "$repo13_bare"

    setup_ancestor_branch_repo
    setup_sync_yaml_failure_repo
    setup_big_agents_md_repo

    cd "$REPO_ROOT"
}

# ── Mock repo 14: ancorg/repo-stale-ancestor ──────────────────────────────
#
# THE CONTROL THE OTHER TWO LACK, and the fixture the `file://` clone in the
# mock exists to serve.
#
# protorg/repo-protected and fgnorg/repo-foreign-branch are a matched pair on
# WHO wrote the stale branch's commit — the bot (force-push lands) against a
# reviewer (force-push refused) — but they are identical on the axis that
# actually decides whether the guard can read the answer: both build
# agents-md-sync/update with `git checkout -b` off the CURRENT tip of main, so
# `origin/main..FETCH_HEAD` is one commit no matter how much history the clone
# has. Under those two, a guard that cannot see past a graft looks perfectly
# healthy.
#
# Here the branch forks from an ANCESTOR — main moves on afterwards, under
# human identities — which is the ordinary shape of a stale branch: it is
# stale precisely because main went somewhere. Measured on git 2.43 against
# this exact layout, cloned `--depth 1` over `file://`:
#
#   shallow    origin/main..FETCH_HEAD = [bot "stale sync", human "H0 init"]
#   deepened   origin/main..FETCH_HEAD = [bot "stale sync"]
#
# In the shallow clone `origin/main` is grafted and has no parents, so the
# EXCLUDED side of the range collapses to one commit and the fork point — a
# commit that is in fact already on main, written by a human — falls into the
# range. The guard then reads its committer as foreign, refuses, and counts the
# repo failed, which is the one case the force-push exists for. That is why
# sync.sh deepens before it asks, and this is the fixture that makes the
# deepening observable.
#
# Its own org, for the reason fgnorg has one: protorg's test asserts
# "2 synced, 0 failed" and a third repo there would move a count that is
# asserting something else.
setup_ancestor_branch_repo() {
    local bare="$TEST_DIR/bare/ancorg_repo-stale-ancestor"
    local work="$TEST_DIR/work/repo-stale-ancestor"
    mkdir -p "$bare" "$work"
    git init --bare --initial-branch=main "$bare" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - python
YAML
    git add .agents-sync.yml
    # The fork point, committed under an explicit HUMAN identity rather than
    # left to the suite's global `test-runner <test@localhost>`. This commit is
    # the one a shallow range wrongly pulls in, so whose it is decides what the
    # refusal would name — `example.com` per the fixture-address rule.
    git -c user.name="A Human" -c user.email="human@example.com" \
        commit -m "H0 init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1

    # The bot's stale branch, forked HERE — at what will become main~2.
    git checkout -b agents-md-sync/update >/dev/null 2>&1
    printf 'stale old sync content\n' > AGENTS.md
    git add AGENTS.md
    git -c user.name="agents-md-sync[bot]" \
        -c user.email="agents-md-sync[bot]@users.noreply.github.com" \
        commit -m "stale sync" >/dev/null 2>&1
    git push origin HEAD:agents-md-sync/update >/dev/null 2>&1

    # ... and main moves on without it. Two commits, so the fork point is an
    # ancestor rather than the parent, and human-authored so the wrong answer
    # is legible when it happens.
    git checkout main >/dev/null 2>&1
    printf 'human work 1\n' > docs.md
    git add docs.md
    git -c user.name="A Human" -c user.email="human@example.com" \
        commit -m "human commit 1" >/dev/null 2>&1
    printf 'human work 2\n' >> docs.md
    git add docs.md
    git -c user.name="A Human" -c user.email="human@example.com" \
        commit -m "human commit 2" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1

    # Protected LAST, so the seeding pushes above land and only the sync's own
    # push to main is refused — which is what routes it to the PR fallback,
    # where the force-push lives.
    install_reject_main_hook "$bare"
    cd "$REPO_ROOT"
}

# ── Mock repo 15: sfailorg/repo-sync-yml ──────────────────────────────────
#
# One ordinary repo, used by four regression legs across two scripts: a
# `.agents-sync.yml` read that FAILS (403) and one that arrives whole and does
# not PARSE, in sync.sh and in drift-report.sh.
#
# It is a repo of its own, in an org of its own, because both legs turn on the
# section list being wrong in a way that is silent, and every other org's tests
# assert counts that a repo failing on purpose would move. Its `.agents-sync.yml`
# starts VALID: the parse leg breaks it on the remote inside the test, so the
# same fixture supplies the control ("this repo reads fine") and the injury,
# and no mock has to pretend a file is unparseable.
#
# IT CARRIES AN AGENTS.md, and that is not decoration. The wrong answer the
# drift leg forbids is **drift-detected**, and drift-report.sh only ever reaches
# the comparison that can produce it through the `else` of `[[ -z
# "$current_agents" ]]`. With no AGENTS.md the row is **no-agents-md** before
# any section list is consulted, so the guard held whatever the script did —
# measured against the pre-fix parse spelling, which published exactly the row
# that guard forbids everywhere else and still left it passing here. The file is
# built from the real build script for this repo's own `sections: [python]`, so
# it is the shape the bug needed: an AGENTS.md that is in fact CORRECT, which a
# section list collapsed to zero then reports as drifted.
setup_sync_yaml_failure_repo() {
    local bare="$TEST_DIR/bare/sfailorg_repo-sync-yml"
    local work="$TEST_DIR/work/repo-sync-yml"
    mkdir -p "$bare" "$work"
    git init --bare --initial-branch=main "$bare" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - python
YAML
    # Built the way testorg/repo-up-to-date-no-claude's is, through the real
    # build script, so it cannot drift from what drift-report.sh will compare
    # it against.
    local managed marker_block
    managed=$("$REPO_ROOT/scripts/build-agents-md.sh" python)
    marker_block="$(printf '%s\n\n%s\n' \
        "## Repo-specific additions" \
        "<!-- Add your repo-specific agent guidance below this line -->")"
    printf '%s\n%s\n' "$managed" "$marker_block" > AGENTS.md
    git add .agents-sync.yml AGENTS.md
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"
}

# sync_yaml_fixture_write <yaml body> — rewrite that repo's `.agents-sync.yml`
# on its default branch, so the file really is what the test says it is rather
# than something a mock claims. Used to break it and to put it back.
sync_yaml_fixture_write() {
    local body="$1" bare="$TEST_DIR/bare/sfailorg_repo-sync-yml"
    local edit="$TEST_DIR/work/repo-sync-yml-edit"
    rm -rf "$edit"
    git clone "$bare" "$edit" >/dev/null 2>&1
    printf '%s' "$body" > "$edit/.agents-sync.yml"
    git -C "$edit" config commit.gpgsign false
    git -C "$edit" add .agents-sync.yml
    git -C "$edit" -c user.name="A Human" -c user.email="human@example.com" \
        commit -m "edit .agents-sync.yml" >/dev/null 2>&1
    git -C "$edit" push origin HEAD:main >/dev/null 2>&1
}

# ── Mock repo 16: bigorg/repo-big-agents-md ───────────────────────────────
#
# The fixture for issue #81's actual root cause, and its SIZE is the whole
# fixture: `echo "$current_agents" | grep -q` loses the race only when the
# payload outgrows the kernel's 64 KiB pipe buffer, so a small AGENTS.md
# cannot catch it however many times it is run. grep exits at the first match,
# the `echo` still has bytes to push, takes SIGPIPE and dies 141, and
# `set -o pipefail` promotes that to the pipeline's status — so a marker that
# IS present reports as absent.
#
# Shaped like `cms-platform/AGENTS.md`, the file that actually did this on the
# live dashboard: comfortably past 72 kB with the marker well inside the first
# 64 KiB, so grep really does finish early. Wrong answers per 20 trials on the
# old spelling were measured at 48/56/64 kB: 0, at 72 kB: 4, at 95 kB: 20.
#
# Its own org, so the repeated runs the race needs cost one repo each rather
# than a whole fleet.
setup_big_agents_md_repo() {
    local bare="$TEST_DIR/bare/bigorg_repo-big-agents-md"
    local work="$TEST_DIR/work/repo-big-agents-md"
    mkdir -p "$bare" "$work"
    git init --bare --initial-branch=main "$bare" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$bare"
    cat > .agents-sync.yml <<'YAML'
sections:
  - python
YAML
    # Managed block first (so the marker sits where a real file's does), then
    # enough repo-specific prose after it to put the total well past 72 kB.
    {
        "$REPO_ROOT/scripts/build-agents-md.sh" python
        printf '%s\n\n' "## Repo-specific additions"
        local i
        for i in $(seq 1 700); do
            printf 'Repo-specific paragraph %s. %s\n\n' "$i" \
                "Filler prose that exists only to push this file past the pipe buffer."
        done
    } > AGENTS.md
    git add .agents-sync.yml AGENTS.md
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
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
  # The complement, so that SILENCE is this key's pass condition here exactly
  # as it is for cron_coverage below: every other repo the mock \`gh repo list\`
  # can return is classified, and test_drift_report asserts nothing is flagged.
  # The flagging path gets its own fixture in
  # test_drift_report_skills_classification, which deletes one name from here.
  # FLOW-sequence \`repo:\` values on their own lines, so the sed that derives
  # that variant can anchor on one name without reaching into any other key.
  out_of_scope:
    - {repo: repo-with-sync, reason: test fixture}
    - {repo: repo-no-sync, reason: test fixture}
    - {repo: repo-with-existing, reason: test fixture}
    - {repo: repo-existing-no-marker, reason: test fixture}
    - {repo: repo-with-claude-md, reason: test fixture}
    - {repo: repo-up-to-date-no-claude, reason: test fixture}
    - {repo: repo-fix-claude, reason: test fixture}
    - {repo: repo-owner2-only, reason: test fixture}
    - {repo: repo-protected, reason: test fixture}
    - {repo: repo-protected-fix, reason: test fixture}
    - {repo: repo-stale, reason: test fixture}
    - {repo: repo-foreign-branch, reason: test fixture}
    - {repo: repo-not-allowed, reason: test fixture}
    - {repo: agentskills, reason: test fixture}
    - {repo: _agent-guidance, reason: test fixture}
    - {repo: repo-excluded, reason: test fixture}
# Every repo the mock \`gh repo list\` can return, across all six mock orgs that
# hold any, so
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
          repo-stale, repo-foreign-branch, agentskills, repo-adopted,
          repo-hook-no-lock, repo-ignored, repo-no-lock, repo-not-allowed,
          repo-unparseable]
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
                   Every source is read, primary first, and each drifted
                   source gets its OWN FAILED: headline and its own
                   remediation line — the bumper slices `/^FAILED:/,$p` and
                   `head -20` into a PR body, so a headline separated from its
                   fix is the one truncation that must not be representable.
  --only REGISTRY  scope --check-current to ONE registry the lock plans, so a
                   caller learns which half moved from the exit code of the
                   question it asked. Refuses a registry the lock does not
                   plan, one that is both primary and source, and any use
                   outside --check-current. Presence is tested with
                   `is not None`, never truthiness: `--only ''` is a value the
                   caller passed and is refused, not silently ignored.
  --repin-source '<OWNER/REPO>@[<REF>]'
                   with --repin, advance the pin of ONE federated source the
                   lock already names; an empty REF means that source's HEAD.
                   Merges by registry KEY into the inherited array — never
                   adds, drops or reorders. Every reason it declines lives in
                   `repin_source_blocker`, which --check-current consults
                   BEFORE it recommends the flag, so this stand-in cannot
                   print a command it would then reject. Without --repin it is
                   an argparse error.
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


_SHA_RE = re.compile(r"[0-9a-f]{40}")


def repin_source_blocker(extras, registry, primary_registry):
    """Why `--repin-source <registry>@` cannot advance that source — or None.

    ONE predicate, read by the flag that REFUSES (apply_repin_sources) and by
    the report that RECOMMENDS (--check-current), which is the real
    generator's shape and the reason it has that shape: two sites answering
    separately is how a tool prints a command it then rejects at exit 1,
    leaving a drifted lock with no route the tool itself will accept. The
    fleet bumper builds `--repin-source` from its own list of drifted
    registries, so it walks into that exit 1 as surely as a human would.
    """
    matched = [s for s in extras if s["registry"] == registry]
    if registry == primary_registry:
        if matched:
            return ("this lock names that registry as BOTH its primary registry and a "
                    "federated source, so under one name there is no spec that reaches "
                    "the federated entry alone")
        return ("that is this lock's PRIMARY registry, not a federated source; the "
                "primary's pin is what --ref (or bare --repin) advances")
    if not matched:
        known = ", ".join(dict.fromkeys(s["registry"] for s in extras)) or "none"
        return ("that is not a source this lock federates (%s); ADDING a source changes "
                "what the lock means and is a plain generate, not a re-pin" % known)
    if len(matched) > 1:
        # Representable and --check green: uniqueness is keyed on BUNDLE, so
        # two entries may share a registry with different bundles and
        # independent pins. Merging by registry key would advance BOTH from one
        # spec — a pin nobody named, with digests nobody reviewed, at exit 0.
        claimed = "; ".join(", ".join(s["bundles"]) or "no bundles" for s in matched)
        return ("this lock federates that registry twice, under [%s], each with its own "
                "pin — so one spec names two sources and advancing 'it' has two answers"
                % claimed)
    if not _SHA_RE.fullmatch(matched[0]["ref"]):
        # A branch name resolves in ANY clone, so it proves nothing about which
        # repository the checkout is; the pin is the whole proof.
        return ("this lock pins that source at %r, which is not a commit sha — and the "
                "commit the lock pins is the ONLY thing that proves the checkout this "
                "would re-pin from is that registry at all" % matched[0]["ref"])
    return None


def repin_primary_blocker(lock, output):
    """Why a --repin cannot advance this lock's PRIMARY pin — or None.

    Consulted by the report as well as by --repin, because EVERY command
    --check-current prints is a `--repin`, the federated one included, so a
    primary-side refusal rejects a source's remediation too.
    """
    pinned = lock.get("ref")
    if not isinstance(pinned, str) or not pinned:
        return ("%s: 'ref' is missing or unusable (%r); --repin advances an existing "
                "pin and this lock has none to advance" % (output, pinned))
    if not _SHA_RE.fullmatch(pinned):
        return ("%s pins '%s' at %r, which is not a commit sha — and the commit the lock "
                "pins is the ONLY thing that proves the clone --repin reads is that "
                "registry" % (output, lock.get("registry"), pinned))
    return None


def select_sources(lock, only):
    """The real generator's --only, filtering EXTRAS before anything is located.

    Filtering up front rather than filtering plan()'s output is what keeps an
    unrelated source's missing checkout from deciding this source's answer:
    plan() exits on the first source it has no override for.

    Returns (extras subset, include_primary).
    """
    extras = list(lock.get("sources") or [])
    if only is None:
        return extras, True
    matched = [source for source in extras if source["registry"] == only]
    if only == lock["registry"]:
        if matched:
            sys.exit("ERROR: --only %s: this lock names it as BOTH its primary registry "
                     "and a federated source, so scoping to it has two answers" % only)
        return [], True
    if not matched:
        planned = ", ".join(dict.fromkeys(
            [lock["registry"]] + [source["registry"] for source in extras]))
        sys.exit("ERROR: --only %s: not a registry this lock plans. It plans %s — name "
                 "one of those exactly as the lock spells it." % (only, planned))
    return matched, False


def apply_repin_sources(lock, specs, overrides):
    """Merge --repin-source pins into the INHERITED array. Never adds, never drops.

    An untouched source comes back BY REFERENCE, so it re-serializes byte for
    byte; a named one comes back as a copy with only `ref` replaced, so key
    order survives too. That is the merge-by-key property `--source` does not
    have, and it is what the fleet's non-de-federation assertion rests on.
    """
    extras = list(lock.get("sources") or [])
    wanted = {}
    for spec in specs:
        registry, sep, ref = spec.rpartition("@")
        if not sep:
            sys.exit("ERROR: --repin-source %r: expected '<OWNER/REPO>@[<ref>]'" % spec)
        if registry in wanted:
            sys.exit("ERROR: --repin-source names %s twice; one pin per source" % registry)
        wanted[registry] = ref
    for registry in wanted:
        # Read, never re-derived: a second copy of any of these conditions is
        # how the refusal and the report drift into disagreeing about the same
        # command.
        blocker = repin_source_blocker(extras, registry, lock["registry"])
        if blocker:
            sys.exit("ERROR: --repin-source %s: %s" % (registry, blocker))
    merged = []
    for source in extras:
        ref = wanted.get(source["registry"])
        if ref is None:
            merged.append(source)            # untouched, byte-identical
            continue
        if source["registry"] not in overrides:
            sys.exit("ERROR: %s: no checkout — pass --source-repo '%s=<path>'"
                     % (source["registry"], source["registry"]))
        path = overrides[source["registry"]]
        # The same identity probe --repin does for the primary, and for the
        # same reason: the lock names a registry, --source-repo names a
        # directory, and nothing else ties the two together. A fork or a
        # same-named repo under another owner sits at that path just as
        # happily, and HEAD resolves in any git repo at all — so without this
        # the wrong clone's HEAD is written under the right registry's name at
        # exit 0. The pin the lock ALREADY carries is the proof, which is only
        # true because repin_source_blocker has refused a non-sha pin above.
        if git(path, "cat-file", "-e", "%s^{commit}" % source["ref"]).returncode != 0:
            sys.exit("ERROR: %s does not contain %s, the commit this lock pins for '%s' "
                     "— so this checkout is not that registry, and re-pinning from it "
                     "would write a commit the registry does not have"
                     % (path, source["ref"], source["registry"]))
        if not ref:
            # Resolved HERE, so a literal `HEAD` can never be written into a lock.
            proc = git(path, "rev-parse", "--verify", "HEAD^{commit}")
            if proc.returncode != 0:
                sys.exit("ERROR: cannot resolve HEAD in %s" % path)
            ref = proc.stdout.decode().strip()
        merged.append({**source, "ref": ref})
    return merged


def plan(lock, repo, overrides, extras):
    sources = [{
        "registry": lock["registry"],
        "ref": lock["ref"],
        "bundles": lock["bundles"],
        "layout": DEFAULT_LAYOUT,
        "path": Path(repo),
    }]
    for source in extras:
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
    parser.add_argument("--only", metavar="REGISTRY", default=None)
    parser.add_argument("--check-format", action="store_true")
    parser.add_argument("--repin", action="store_true")
    parser.add_argument("--repin-source", metavar="'OWNER/REPO@[REF]'",
                        action="append", default=None)
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
    # --repin-source is deliberately NOT in that list. It merges by registry
    # key and never replaces the array, so folding it in would make the one
    # flag that can fix a federated pin an error alongside the only flag it
    # means anything with.
    # `is not None`, never truthiness, in all three. argparse leaves an
    # unpassed flag as None, so presence is what these guards are about and an
    # EMPTY value is still a value the caller passed — `--only "$REG"` with an
    # unset REG is the ordinary failure mode of a shell caller, which the fleet
    # bumper is. Testing truth let `--only ''` slip both guards and degrade the
    # run into a different command silently.
    if args.repin_source is not None and not args.repin:
        parser.error("--repin-source advances a pin the lock already carries, so it "
                     "only means anything alongside --repin")
    if args.only is not None and not args.check_current:
        parser.error("--only scopes --check-current; pass it alongside that flag.")
    if args.only is not None and args.check_format:
        parser.error("--only scopes --check-current alone; --check-format reads the "
                     "file alone and cannot be narrowed to one registry.")

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

    if args.check_current:
        selected, include_primary = select_sources(lock, args.only)
        sources = plan(lock, args.repo, overrides, selected)
        if not include_primary:
            # Dropped AFTER planning: a scoped run should still refuse a lock
            # that cannot be planned at all. It is the READING of the primary
            # that a scoped question has no business doing.
            sources = sources[1:]
        # The ref of the FIRST source this run planned. Unscoped that is the
        # primary, so these bytes are what they always were.
        scoped_ref = lock["ref"] if include_primary else selected[0]["ref"]
        drifted = []
        for index, source in enumerate(sources):
            pinned = at_ref(source["path"], source["ref"], source["bundles"], source["layout"])
            here = collect(source["path"], source["bundles"], source["layout"])
            differences = []
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
            if differences:
                drifted.append((source, include_primary and index == 0, differences))
        if not drifted:
            print("OK: the working tree still matches %s." % scoped_ref)
            return 0
        # ONE BLOCK PER DRIFTED SOURCE, primary first (plan order gives that),
        # every headline at column 0, and every headline that HAS a remediation
        # line immediately followed by it. bump-consumer-locks.sh branches on
        # `grep -q '^FAILED:'` and slices `sed -n '/^FAILED:/,$p' | head -20`
        # into a PR body, so a truncation may drop a trailing block but must
        # never separate a headline from the command that fixes it.
        #
        # A block whose repair this generator would REFUSE carries no command
        # at all and states the reason inside its own headline. Printing one
        # anyway sends its reader — or the fleet bumper, which builds the same
        # flag from its own list of drifted registries — to an exit 1 with
        # nothing else offered. Asked before the command is printed, never
        # after it is rejected.
        all_extras = list(lock.get("sources") or [])
        primary_blocked = repin_primary_blocker(lock, output)
        primary_drifted = any(is_primary for _, is_primary, _ in drifted)
        for source, is_primary, differences in drifted:
            if is_primary:
                if primary_blocked:
                    print("FAILED: the bundle has moved on since %s, which %s still "
                          "pins. No re-pin command is printed for it because this "
                          "generator would refuse one: %s"
                          % (lock["ref"], output, primary_blocked))
                else:
                    print("FAILED: the bundle has moved on since %s, which %s still pins."
                          % (lock["ref"], output))
                    print("  python3 scripts/generate_skills_lock.py --repin")
            else:
                blocker = primary_blocked or repin_source_blocker(
                    all_extras, source["registry"], lock["registry"])
                if blocker:
                    print("FAILED: %s's bundles have moved on since %s, which %s still "
                          "pins for it. No --repin-source command is printed for it "
                          "because this generator would refuse one: %s"
                          % (source["registry"], source["ref"], output, blocker))
                else:
                    print("FAILED: %s's bundles have moved on since %s, which %s still "
                          "pins for it." % (source["registry"], source["ref"], output))
                    # `--ref` is part of the command, not decoration: --repin
                    # does not inherit `ref`, so a source-only repair printed
                    # without one advances the PRIMARY pin as a side effect.
                    # Dropped when the primary drifted too, because its own
                    # block is already telling the reader to advance it.
                    anchor = "" if primary_drifted else "--ref %s " % lock["ref"]
                    print("  python3 scripts/generate_skills_lock.py --repin "
                          "%s--repin-source '%s@'" % (anchor, source["registry"]))
            for line in differences:
                print("  - %s" % line)
        return 1

    if args.repin:
        # Merged BEFORE planning, so the digests of an advanced source are read
        # at its new ref and the array written below is the merged one.
        extras = (apply_repin_sources(lock, args.repin_source, overrides)
                  if args.repin_source is not None else list(lock.get("sources") or []))
        sources = plan(lock, args.repo, overrides, extras)
        # The real generator's probe: a clone that IS this registry contains
        # the commit the lock already pins — which needs that pin to BE a
        # commit, since `main^{commit}` resolves in every clone with a main
        # branch and would turn the probe into a formality any impostor passes.
        primary_blocker = repin_primary_blocker(lock, output)
        if primary_blocker:
            sys.exit("ERROR: %s" % primary_blocker)
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
        # Inherited — never rebuilt from flags. A source --repin-source did
        # not name comes through by reference and re-serializes byte for byte;
        # a named one differs in `ref` alone. This is the whole federation
        # property the fleet test asserts.
        if extras:
            document["sources"] = extras
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

# write_strip_scoped_flags <path> — a one-purpose editor that removes the two
# federated flags from a copy of the stand-in generator, so a run can meet the
# generator that predates them. Written to a file rather than inlined because
# it has to state, in code, exactly which lines it expects to find: a
# text-surgery strip that silently matches nothing produces a stub with the
# flags still in it, and the degraded-path test would then be exercising the
# ordinary path while reporting PASS.
write_strip_scoped_flags() {
    cat > "$1" <<'STRIPEOF'
#!/usr/bin/env python3
"""Remove --only and/or --repin-source from a copy of the stand-in generator.

    strip-scoped-flags.py <path> [both|only|repin-source]

PARAMETERISED, because the script's own comment makes "BOTH, OR NEITHER" a
load-bearing invariant and stripping both together is the one case that cannot
exercise it: a run where FED_ADVANCE_AVAILABLE were set per flag would degrade
identically. The two one-missing shapes are the ones that would notice.
"""
import io
import sys

path = sys.argv[1]
which = sys.argv[2] if len(sys.argv) > 2 else "both"
if which not in ("both", "only", "repin-source"):
    sys.exit("strip: unknown selection %r" % which)
text = io.open(path, encoding="utf-8").read()

ONLY_CUTS = [
    '    parser.add_argument("--only", metavar="REGISTRY", default=None)\n',
    '    if args.only is not None and not args.check_current:\n'
    '        parser.error("--only scopes --check-current; pass it alongside that flag.")\n'
    '    if args.only is not None and args.check_format:\n'
    '        parser.error("--only scopes --check-current alone; --check-format reads the "\n'
    '                     "file alone and cannot be narrowed to one registry.")\n',
]
ONLY_SWAPS = [
    ("select_sources(lock, args.only)", "select_sources(lock, None)"),
]
SOURCE_CUTS = [
    '    parser.add_argument("--repin-source", metavar="\'OWNER/REPO@[REF]\'",\n'
    '                        action="append", default=None)\n',
    '    if args.repin_source is not None and not args.repin:\n'
    '        parser.error("--repin-source advances a pin the lock already carries, so it "\n'
    '                     "only means anything alongside --repin")\n',
]
SOURCE_SWAPS = [
    ("if args.repin_source is not None else", "if False else"),
    ("apply_repin_sources(lock, args.repin_source, overrides)",
     "apply_repin_sources(lock, [], overrides)"),
]

cuts, swaps, survivors = [], [], []
if which in ("both", "only"):
    cuts += ONLY_CUTS
    swaps += ONLY_SWAPS
    survivors.append("args.only")
if which in ("both", "repin-source"):
    cuts += SOURCE_CUTS
    swaps += SOURCE_SWAPS
    survivors.append("args.repin_source")

for cut in cuts:
    if text.count(cut) != 1:
        sys.exit("strip: expected exactly one of:\n%s" % cut)
    text = text.replace(cut, "")
for old, new in swaps:
    if text.count(old) != 1:
        sys.exit("strip: expected exactly one %r" % old)
    text = text.replace(old, new)
for name in survivors:
    if name in text:
        sys.exit("strip: a reference to the removed %s survived" % name)
# And the converse, which is what makes a PARTIAL strip a real fixture rather
# than a full one under another name: the flag that was NOT selected has to
# still be there.
kept = {"only": "args.repin_source", "repin-source": "args.only"}.get(which)
if kept and kept not in text:
    sys.exit("strip: %s was removed but only %s was asked for" % (kept, which))

io.open(path, "w", encoding="utf-8").write(text)
STRIPEOF
}

# write_only_bundles_stub <path> — a one-purpose editor that gives a copy of
# the stand-in generator NO `--only` and an unrelated flag whose name merely
# BEGINS `--only`. The false-positive fixture for the capability probe, and it
# has to be built by surgery for the same reason the strip above is: an editor
# that quietly matched nothing would leave the real `--only` in place and the
# probe test would pass against the flag it is meant to be missing.
#
# `--only-bundles` is inert here — the point is entirely what ARGPARSE does
# with it. `allow_abbrev` is on by default, so `--only <registry>` is an
# unambiguous prefix of `--only-bundles` and is silently accepted as one.
write_only_bundles_stub() {
    cat > "$1" <<'ONLYBUNDLESEOF'
#!/usr/bin/env python3
"""Replace --only with an unrelated --only-bundles in the stand-in generator."""
import io
import re
import sys

path = sys.argv[1]
text = io.open(path, encoding="utf-8").read()

CUTS = [
    '    if args.only is not None and not args.check_current:\n'
    '        parser.error("--only scopes --check-current; pass it alongside that flag.")\n'
    '    if args.only is not None and args.check_format:\n'
    '        parser.error("--only scopes --check-current alone; --check-format reads the "\n'
    '                     "file alone and cannot be narrowed to one registry.")\n',
]
SWAPS = [
    ('    parser.add_argument("--only", metavar="REGISTRY", default=None)\n',
     '    parser.add_argument("--only-bundles", metavar="LIST", default=None)\n'),
    ("select_sources(lock, args.only)", "select_sources(lock, None)"),
]

for cut in CUTS:
    if text.count(cut) != 1:
        sys.exit("only-bundles: expected exactly one of:\n%s" % cut)
    text = text.replace(cut, "")
for old, new in SWAPS:
    if text.count(old) != 1:
        sys.exit("only-bundles: expected exactly one %r" % old)
    text = text.replace(old, new)
if re.search(r"args\.only(?!_bundles)", text):
    sys.exit("only-bundles: a reference to the removed --only survived")

io.open(path, "w", encoding="utf-8").write(text)
ONLYBUNDLESEOF
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
#   bumporg/repo-fed-current primary content-current, federated source MOVED →
#                            a PR that advances the SOURCE pin and holds the
#                            primary's. Each half is asked its own scoped
#                            question, so which one moved decides which one
#                            moves
#   bumporg/repo-fed-stale   BOTH halves moved → one PR advancing both pins
#   bumporg/repo-federated   THE NEGATIVE CONTROL, and the reason this whole
#                            gate is per-source: stale primary + a federated
#                            source that is content-CURRENT but pinned behind
#                            its own HEAD → the primary ref advances and
#                            sources[0].ref must NOT. A combined verdict says
#                            FAILED here (the primary moved) and would advance
#                            the federated pin too, on every routine bump
#                            night, across the fleet
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
    write_strip_scoped_flags "$TEST_DIR/strip-scoped-flags.py"
    write_only_bundles_stub "$TEST_DIR/only-bundles-stub.py"

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
    # Captured BEFORE this second commit, so a fixture pinning it is genuinely
    # BEHIND this source's content. Without it a re-pin that re-resolved each
    # sources[].ref to HEAD would leave the lock byte for byte where it was,
    # and the assertions that its pin "did not move" could not fail.
    echo "deploy v2" > "$TEST_DIR/cms-platform/skills/deploy-site/SKILL.md"
    echo "publish v2" > "$TEST_DIR/cms-platform/plugins/cms-platform/skills/publish-site/SKILL.md"
    git add -A && git commit -m "deploy v2" >/dev/null 2>&1
    BUMP_SRC_CONTENT=$(git rev-parse HEAD)
    # The federated twin of the registry's "docs only" commit above, and the
    # reason THE NEGATIVE CONTROL can fail at all. A source pinned at
    # BUMP_SRC_CONTENT is content-CURRENT while its ref sits behind HEAD, so a
    # run that advanced it anyway writes BUMP_SRC_HEAD and is caught. Pin the
    # negative control at HEAD instead and "did not advance" and "advanced to
    # HEAD" are the same bytes — a green light wired to nothing.
    echo "# cms-platform" > README.md
    git add -A && git commit -m "docs only" >/dev/null 2>&1
    BUMP_SRC_HEAD=$(git rev-parse HEAD)

    BUMP_CHECKOUTS_ARG="bumporg/agentskills=$TEST_DIR/registry bumporg/cms-platform=$TEST_DIR/cms-platform"

    local name bare work
    for name in agentskills repo-current repo-diverged repo-error \
                repo-fed-current repo-fed-stale repo-federated repo-inverted \
                repo-leftover repo-no-lock repo-other-registry repo-stale; do
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
            agentskills|repo-stale|repo-diverged|repo-leftover)
                seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_OLD"
                ;;
            repo-current)
                seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_CONTENT"
                ;;
            repo-federated)
                # THE NEGATIVE CONTROL. Its source is pinned at
                # $BUMP_SRC_CONTENT — content-current, ref behind that source's
                # HEAD — while its PRIMARY is stale. So the primary's question
                # answers FAILED and the source's answers OK, and the only
                # thing that keeps sources[0].ref where it is is that the two
                # were asked separately.
                #
                # The sources baseline is written by the FIXTURE, before the
                # generator ever sees this lock. Comparing generator output
                # against generator output is a tautology: a generator that
                # mangles or drops `sources` mangles the baseline identically
                # and the byte-for-byte assertion still passes.
                seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_OLD" \
                    "$BUMP_SRC_CONTENT" fill "$TEST_DIR/repo-federated.sources.expected"
                ;;
            repo-fed-current)
                # The mirror of the negative control: the PRIMARY is
                # content-current and the SOURCE has moved. The re-pin this
                # produces must hold the primary's pin and advance the
                # source's — the opposite assignment, from the opposite pair
                # of answers.
                seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_CONTENT" \
                    "$BUMP_SRC_REF"
                ;;
            repo-fed-stale)
                # Both halves moved. One PR, both pins advanced: a stale
                # primary and a moved source are independent facts, and the
                # re-pin that fixes one is the re-pin that fixes the other.
                seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_OLD" \
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

    # repo-leftover is repo-diverged's opposite, and the distinction is the
    # whole point of the self-heal: its bump branch also carries content the
    # run would not push, but every commit on it is ALREADY on main — the
    # residue of a bump PR that merged and whose branch nobody deleted.
    #
    # Pointed straight at main, so the compare API answers "identical" and the
    # branch is provably carrying nothing. Measured 2026-08-25, this was the
    # real fleet's state in five repos at once, and it silently disabled the
    # re-pinner in every one of them.
    cd "$TEST_DIR/work/bumporg-repo-leftover"
    git push origin main:refs/heads/skills-lock-bump/update >/dev/null 2>&1

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
                    fgnorg)
                        json='[
                          {"nameWithOwner":"fgnorg/repo-foreign-branch"}
                        ]'
                        ;;
                    ancorg)
                        json='[
                          {"nameWithOwner":"ancorg/repo-stale-ancestor"}
                        ]'
                        ;;
                    sfailorg)
                        json='[
                          {"nameWithOwner":"sfailorg/repo-sync-yml"}
                        ]'
                        ;;
                    bigorg)
                        json='[
                          {"nameWithOwner":"bigorg/repo-big-agents-md"}
                        ]'
                        ;;
                    emptyorg)
                        # An owner that really does hold nothing. Distinct from
                        # `failorg` below, and the distinction is the point:
                        # this one ANSWERS, with an empty list. `--jq
                        # '.[].nameWithOwner'` over `[]` prints nothing at all,
                        # so `repo_list_raw` is the empty string — and `echo ""`
                        # is one blank LINE, which mapfile turns into a
                        # one-element array holding "". Both scripts then
                        # believed they had found one repo whose name is the
                        # empty string and went on to clone it. The `sed
                        # '/^$/d'` in each is what this fixture exists to hold
                        # in place; a hand-written `[]` here rather than falling
                        # through to the `*)` default so the intent survives
                        # someone editing that default.
                        json='[]'
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
                # A diagnostic written alongside a listing that SUCCEEDS —
                # the shape gh takes in ordinary conditions (deprecation
                # notices, auth-expiry warnings). It matters here more than
                # anywhere else in this mock: every caller of `gh repo list`
                # splits the captured value into REPO NAMES, so a merged
                # `2>&1` does not decorate a log, it invents a repository. The
                # `failorg` arm above is stderr with a FAILURE; this is stderr
                # with exit 0, which nothing in the suite could produce.
                # MOCK_REPO_LIST_STDERR_NOTICE names the orgs that get it.
                if [[ " ${MOCK_REPO_LIST_STDERR_NOTICE:-} " == *" $org "* ]]; then
                    echo "gh: warning: authentication token is nearing expiry" >&2
                fi
                # Find --jq filter in remaining args
                jq_filter=$(parse_jq_filter "$@")
                if [[ -n "$jq_filter" ]]; then
                    echo "$json" | jq -r "$jq_filter"
                else
                    echo "$json"
                fi
                ;;
            clone)
                # Clone from our bare repos — over `file://`, and that scheme is
                # load-bearing rather than tidiness.
                #
                # `git clone /abs/path` takes git's LOCAL optimisation: it
                # hardlinks the object store and ignores `--depth` entirely,
                # silently, with no warning and exit 0. Every script under test
                # clones `-- --depth 1` (sync.sh, bump-consumer-locks.sh,
                # bump-hook-pin.sh), so for as long as this mock cloned by path
                # NO test in this suite ever exercised a shallow clone: the
                # scripts got full history in the tests and a graft in
                # production, and every assertion about behaviour that turns on
                # the graft was vacuous. That is not hypothetical — it is how
                # the force-push guard shipped green while refusing the one case
                # it exists for (see setup_ancestor_branch_repo, and the range
                # comment in sync.sh beside the `--unshallow`).
                #
                # `file://` forces the transport-honest path: upload-pack and
                # receive-pack, so `--depth` is honoured, `--unshallow` works,
                # and a bare repo's pre-receive hook still fires and still
                # relays its `remote:` lines. Measured on git 2.43 against a
                # three-commit bare: cloned by path `--depth 1` gives
                # `is-shallow-repository=false` and 3 commits, cloned by
                # `file://` it gives `true` and 1.
                repo_slug=$(echo "$3" | tr '/' '_')
                dest="${4}"
                shift 4
                # Strip -- separator if present
                [[ "${1:-}" == "--" ]] && shift
                bare_path="${MOCK_BARE_DIR}/${repo_slug}"
                if [[ -d "$bare_path" ]]; then
                    git clone "file://$bare_path" "$dest" "$@" 2>/dev/null
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
        # `-X DELETE` puts a flag where the path used to be. Parsed rather
        # than assumed, so a caller that omits it still lands on GET.
        api_method="GET"
        if [[ "$1" == "-X" || "$1" == "--method" ]]; then
            api_method="$2"; shift 2
        fi
        # Valueless flags may sit between the subcommand and the path --
        # `--paginate` is the one in use. Skipped rather than assumed absent:
        # real `gh` accepts them there, so a mock that treats the first token
        # as the path silently routes the call to no handler at all and every
        # caller reads the fallthrough as an answer. (Measured 2026-08-29:
        # adding `--paginate` to one call site turned five assertions red with
        # nothing naming the flag.)
        while [[ "$1" == --* && "$1" != "--jq" ]]; do
            shift
        done
        api_path="$1"
        shift
        jq_filter=$(parse_jq_filter "$@")

        # DELETE repos/{owner}/{repo}/git/refs/heads/{branch}
        # What delete_bump_branch calls to clean up after a merged bump PR.
        if [[ "$api_method" == "DELETE" \
              && "$api_path" =~ ^repos/([^/]+)/([^/]+)/git/refs/heads/(.+)$ ]]; then
            repo_slug="${BASH_REMATCH[1]}_${BASH_REMATCH[2]}"
            del_branch="${BASH_REMATCH[3]}"
            bare_path="${MOCK_BARE_DIR}/${repo_slug}"
            # The failure that is NOT an absent ref, and the one no fixture
            # could previously produce: an HTTP 404 raised while the branch is
            # STILL THERE. GitHub answers 404 rather than 403 when a credential
            # is not authorized to know a repo exists, so a scope gap, a
            # revoked installation and an expired token all land here wearing
            # the same "Not Found" body a deleted ref would — which is why
            # delete_bump_branch may not classify this by reading the text.
            # The ref is deliberately left in place: the whole assertion is
            # that the branch survives while the caller is told it is gone.
            # MOCK_DELETE_REF_HTTP_FAIL names repos (owner_repo) that get it.
            if [[ " ${MOCK_DELETE_REF_HTTP_FAIL:-} " == *" $repo_slug "* ]]; then
                echo '{"message":"Not Found","status":"404"}'
                exit 1
            fi
            if [[ -d "$bare_path" ]] \
               && git -C "$bare_path" rev-parse --verify -q "refs/heads/$del_branch" >/dev/null; then
                git -C "$bare_path" update-ref -d "refs/heads/$del_branch"
                exit 0
            fi
            # Real gh prints the error body to stdout and exits non-zero. The
            # wording no longer decides anything — delete_bump_branch settles
            # already-gone by asking matching-refs below — but it stays the
            # API's real 422 so the mock keeps describing GitHub rather than
            # the script.
            echo '{"message":"Reference does not exist","status":"422"}'
            exit 1
        fi

        # GET repos/{owner}/{repo}/git/matching-refs/heads/{prefix} — what
        # delete_bump_branch asks after a failed DELETE to settle whether the
        # ref is actually gone. Computed from the bare repo, like the compare
        # handler below, so a fixture's real refs decide the answer.
        #
        # The SHAPE is the reason the script asks this endpoint and not
        # git/ref/heads/{branch}: a prefix matching nothing is HTTP 200 with an
        # empty array, so absence is a success response rather than a 404
        # indistinguishable from the scope 404 above. A missing bare repo is
        # still the other handlers' 404 — that is the "cannot see this repo"
        # case, and it must NOT read as "the branch is gone".
        #
        # Matching is a STRING prefix over the whole ref name, as GitHub's is:
        # heads/foo also returns heads/foo-2. `git for-each-ref refs/heads/foo`
        # would not — it globs by path component — so the filter is applied
        # here rather than handed to git, and a mock that handed it to git
        # would quietly make the script's exact-ref test look unnecessary.
        if [[ "$api_method" == "GET" \
              && "$api_path" =~ ^repos/([^/]+)/([^/]+)/git/matching-refs/heads/(.+)$ ]]; then
            repo_slug="${BASH_REMATCH[1]}_${BASH_REMATCH[2]}"
            ref_prefix="refs/heads/${BASH_REMATCH[3]}"
            bare_path="${MOCK_BARE_DIR}/${repo_slug}"
            # The credential is gone by the time the follow-up question is
            # asked -- an installation revoked mid-run, an expired token, a
            # repo that left the App's scope between the two calls. The ref is
            # left in place, exactly as with the DELETE above: the assertion is
            # that a branch which still exists is not reported as deleted just
            # because the question about it could not be asked.
            # MOCK_MATCHING_REFS_HTTP_FAIL names repos (owner_repo) that get it.
            if [[ " ${MOCK_MATCHING_REFS_HTTP_FAIL:-} " == *" $repo_slug "* ]]; then
                echo '{"message":"Not Found","status":"404"}'
                exit 1
            fi
            if [[ ! -d "$bare_path" ]]; then
                echo '{"message":"Not Found","status":"404"}'
                exit 1
            fi
            json="[]"
            while IFS= read -r one_ref; do
                [[ -n "$one_ref" && "$one_ref" == "$ref_prefix"* ]] || continue
                json=$(echo "$json" | jq --arg r "$one_ref" '. + [{"ref": $r}]')
            done < <(git -C "$bare_path" for-each-ref --format='%(refname)' refs/heads/ 2>/dev/null)
            if [[ -n "$jq_filter" ]]; then echo "$json" | jq -r "$jq_filter"; else echo "$json"; fi
            exit 0
        fi

        # repos/{owner}/{repo}/compare/{base}...{head} — answers
        # branch_adds_nothing_to_base. Computed from the bare repo rather than
        # hardcoded, so a fixture's real topology decides the verdict.
        if [[ "$api_path" =~ ^repos/([^/]+)/([^/]+)/compare/(.+)\.\.\.(.+)$ ]]; then
            repo_slug="${BASH_REMATCH[1]}_${BASH_REMATCH[2]}"
            cmp_base="${BASH_REMATCH[3]}"
            cmp_head="${BASH_REMATCH[4]}"
            bare_path="${MOCK_BARE_DIR}/${repo_slug}"
            if [[ ! -d "$bare_path" ]]; then
                echo '{"message":"Not Found","status":"404"}'
                exit 1
            fi
            base_sha=$(git -C "$bare_path" rev-parse --verify -q "$cmp_base" 2>/dev/null || true)
            head_sha=$(git -C "$bare_path" rev-parse --verify -q "$cmp_head" 2>/dev/null || true)
            if [[ -z "$base_sha" || -z "$head_sha" ]]; then
                echo '{"message":"Not Found","status":"404"}'
                exit 1
            fi
            if [[ "$base_sha" == "$head_sha" ]]; then
                cmp_status="identical"
            elif git -C "$bare_path" merge-base --is-ancestor "$head_sha" "$base_sha" 2>/dev/null; then
                cmp_status="behind"
            elif git -C "$bare_path" merge-base --is-ancestor "$base_sha" "$head_sha" 2>/dev/null; then
                cmp_status="ahead"
            else
                cmp_status="diverged"
            fi
            json="{\"status\": \"$cmp_status\"}"
            if [[ -n "$jq_filter" ]]; then echo "$json" | jq -r "$jq_filter"; else echo "$json"; fi
            exit 0
        fi

        # repos/{owner}/{repo} — repo metadata; only default_branch is read.
        if [[ "$api_path" =~ ^repos/([^/]+)/([^/]+)$ ]]; then
            repo_slug="${BASH_REMATCH[1]}_${BASH_REMATCH[2]}"
            if [[ ! -d "${MOCK_BARE_DIR}/${repo_slug}" ]]; then
                echo '{"message":"Not Found","status":"404"}'
                exit 1
            fi
            json='{"default_branch": "main"}'
            if [[ -n "$jq_filter" ]]; then echo "$json" | jq -r "$jq_filter"; else echo "$json"; fi
            exit 0
        fi

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

            # The OTHER way a contents read fails, and the one no fixture could
            # previously produce: an HTTP error that is NOT 404. A credential
            # that lost Contents permission, a rate limit, a 5xx. Real gh writes
            # its own `gh: ... (HTTP 403)` line to stderr AND the API's error
            # body to stdout with the --jq filter unapplied, so both surfaces
            # are emitted here — bump-consumer-locks.sh matches on either, and a
            # mock that produced only one of them would let half that guard rot.
            # MOCK_CONTENTS_HTTP_FAIL names repos (owner_repo) where every
            # contents call answers this way.
            # A notice that arrives BEFORE the error, on the same stream.
            #
            # This is the shape every reader of this endpoint claims to handle
            # and none of them could be asked about: the suite's two stderr
            # paths are mutually exclusive by construction —
            # MOCK_CONTENTS_STDERR_NOTICE writes only where the fetch SUCCEEDS
            # and exits 0, MOCK_CONTENTS_HTTP_FAIL and the 404 knob only where
            # it fails — so no mock anywhere emitted a gh stderr with more than
            # ONE line, and "which line does the operator get shown" had exactly
            # one possible answer whatever the selection rule was.
            #
            # The notice is deliberately `gh: `-prefixed and status-free, which
            # is what makes it discriminating rather than merely present: it
            # defeats a `head -1` AND a plain `grep -m1 '^gh: '`, so only a rule
            # that prefers the line CARRYING THE STATUS reaches the right one.
            # Real gh notices look like this (the auth-expiry and deprecation
            # warnings it writes alongside a perfectly ordinary response), and
            # sync.sh's own comment records the measurement that motivated the
            # rule: two lines in this order reported "could not read
            # .agents-sync.yml — gh: this API endpoint is deprecated" and sent
            # the operator after the wrong fault.
            #
            # It carries no response BODY and never will: the house rule
            # (AGENTS.md, "Sanitize error output") is that a diagnostic quotes a
            # status and a machine error type, so a fixture that tempted a
            # reader to quote a body would be testing for the wrong behaviour.
            if [[ " ${MOCK_CONTENTS_STDERR_PRENOTICE:-} " == *" $repo_slug "* ]]; then
                echo "gh: your authentication token expires in 3 days" >&2
            fi

            if [[ " ${MOCK_CONTENTS_HTTP_FAIL:-} " == *" $repo_slug "* ]]; then
                echo "gh: Resource not accessible by integration (HTTP 403)" >&2
                echo '{"message":"Resource not accessible by integration","status":"403"}'
                exit 1
            fi

            # A 404 that reaches the caller on STDERR ONLY, which every reader
            # of this endpoint claims to handle and none of them was ever asked
            # to. sync.sh and bump-consumer-locks.sh both disambiguate "the file
            # is absent" from "the credential cannot see it" by matching TWO
            # surfaces — `(HTTP 404)` in gh's own stderr line and
            # `"status":"404"` in the body gh leaves on stdout — and both say in
            # a comment that "only one of them may reach us". The default 404
            # below emits the body and no status line, so until this knob the
            # stderr half of every one of those conditions was dead text:
            # measured, replacing sync.sh's needle with "(HTTP 404 NEVERMATCH)"
            # left the suite at 907 passed / 0 failed while breaking the stdout
            # regex moved 7 assertions.
            #
            # The asymmetry is real, not invented for the test: GitHub's REST
            # errors do not all carry a `status` field (the documented 404 body
            # is `message` + `documentation_url`), while gh's own
            # `gh: Not Found (HTTP 404)` line is written for every one of them.
            # MOCK_CONTENTS_404_STDERR_ONLY names repos (owner_repo) whose
            # missing files answer that way. Left OFF, the default below is
            # unchanged, which is what keeps the stdout half independently
            # detectable.
            if [[ " ${MOCK_CONTENTS_404_STDERR_ONLY:-} " == *" $repo_slug "* ]] \
               && ! git -C "$bare_path" cat-file -e "main:$file_path" 2>/dev/null; then
                echo "gh: Not Found (HTTP 404)" >&2
                echo '{"message":"Not Found","documentation_url":"https://docs.github.com/rest/repos/contents#get-repository-content"}'
                exit 1
            fi

            if [[ -d "$bare_path" ]]; then
                content=$(git -C "$bare_path" show "main:$file_path" 2>/dev/null || true)
                if [[ -n "$content" ]]; then
                    # `size` is not decoration: the real contents API always
                    # sends it, and fetch_file_content verifies its decode
                    # against it. A mock that omits it cannot exercise the
                    # check, which is exactly how #81 shipped green.
                    size=$(echo "$content" | wc -c)
                    encoded=$(echo "$content" | base64 -w 0)
                    # MOCK_TRUNCATE_CONTENTS=<n> serves the first n base64 chars
                    # while still declaring the honest size -- i.e. the exact
                    # shape of #81: a short body that looks like a whole file.
                    #
                    # MOCK_TRUNCATE_PATHS restricts that to a space-separated
                    # list of paths, and it exists because the global switch
                    # alone made the #81 guard UNTESTABLE at every call site but
                    # one. Truncate everything and AGENTS.md truncates too; the
                    # AGENTS.md branch is the first fetch in the row, it records
                    # the failure, and the row is already **fetch-failed** before
                    # CLAUDE.md, `.agents-sync.yml`, `skills.lock` or
                    # `.claude/settings.json` is ever asked for. Every other
                    # site's rc=2 handling was therefore shadowed by the first
                    # one, and a suite that only ever truncated globally would
                    # have gone green with all of them still spelled
                    # `|| VAR=""`.
                    #
                    # Real truncation is SIZE-driven, so in production it is
                    # per-file by nature: one big file in a repo comes back
                    # short while the small ones beside it arrive whole. That is
                    # the shape this reproduces, and it is the shape the #81
                    # fixtures could not — they are all tiny, so nothing in them
                    # ever crossed a size cliff on its own.
                    truncate_this=false
                    if [[ -n "${MOCK_TRUNCATE_CONTENTS:-}" ]]; then
                        if [[ -z "${MOCK_TRUNCATE_PATHS:-}" ]]; then
                            truncate_this=true
                        else
                            for tp in ${MOCK_TRUNCATE_PATHS}; do
                                [[ "$tp" == "$file_path" ]] && truncate_this=true && break
                            done
                        fi
                    fi
                    if $truncate_this; then
                        encoded="${encoded:0:$MOCK_TRUNCATE_CONTENTS}"
                    fi
                    # A diagnostic written alongside a call that SUCCEEDS.
                    # Real gh does this in ordinary conditions — deprecation
                    # notices, auth-expiry warnings — and every reader of this
                    # endpoint captures the payload in a command substitution,
                    # so a merged `2>&1` folds the notice into base64 that is
                    # then decoded and parsed. Nothing in this mock could
                    # produce that shape before: MOCK_CONTENTS_HTTP_FAIL above
                    # exits 1, so the only stderr the suite ever saw came with a
                    # failure, and the separation on the SUCCESS path was
                    # untested — measured, reverting sync.sh's split capture to
                    # the merged spelling left the suite at 907 passed / 0
                    # failed. MOCK_CONTENTS_STDERR_NOTICE names repos
                    # (owner_repo) that get the notice; the content and the
                    # exit-0 status are unchanged, which is the whole point.
                    if [[ " ${MOCK_CONTENTS_STDERR_NOTICE:-} " == *" $repo_slug "* ]]; then
                        echo "gh: this API endpoint is deprecated" >&2
                    fi
                    json="{\"size\": $size, \"content\": \"$encoded\"}"
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
                    # A listing that cannot be answered at all — a 401, a rate
                    # limit, a network blip. Real gh writes the diagnostic to
                    # stderr and exits non-zero having written nothing usable to
                    # stdout, which is what makes "no open pull request" and "I
                    # could not ask" indistinguishable to a caller that reads
                    # only the string. MOCK_PR_LIST_FAILS names repos
                    # (owner_repo) whose listing does that.
                    if [[ " ${MOCK_PR_LIST_FAILS:-} " == *" ${repo_arg//\//_} "* ]]; then
                        echo "gh: Bad credentials (HTTP 401)" >&2
                        exit 1
                    fi
                    # The same diagnostic written by a listing that SUCCEEDS,
                    # which is the harder case and had no fixture at all. Both
                    # readers of this listing decide on the SHAPE of the value,
                    # not on the exit status: bump-hook-pin.sh asks
                    # `.[0].number // empty` and treats an empty answer as "no
                    # PR is open", so one stderr line folded in makes a quiet
                    # night look like an already-proposed one and the whole
                    # script a silent no-op; the bumper's sweep mapfiles the
                    # answer into PR NUMBERS it then acts on. Neither failure
                    # goes red anywhere. MOCK_PR_LIST_STDERR_NOTICE names repos
                    # (owner_repo) whose listing writes it.
                    if [[ " ${MOCK_PR_LIST_STDERR_NOTICE:-} " == *" ${repo_arg//\//_} "* ]]; then
                        echo "gh: warning: authentication token is nearing expiry" >&2
                    fi
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
                # The OTHER way a read fails, and the one the live sweep hit:
                # the PR is listable and perfectly real, but `gh pr view`
                # refuses this particular --json field set. Real gh writes that
                # to stderr and exits 1 having written nothing to stdout, so a
                # caller that discards stderr is left with a failure and no
                # reason at all. MOCK_PR_VIEW_FAILS names repos where every
                # view does that.
                if [[ " ${MOCK_PR_VIEW_FAILS:-} " == *" $repo_slug "* ]]; then
                    echo "unknown JSON field: \"statusCheckRollup\"" >&2
                    exit 1
                fi
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
                    # A repo with "automatically delete head branches" enabled,
                    # which is a setting the bot does not control and cannot
                    # see. The head ref is gone before delete_bump_branch ever
                    # asks, so its DELETE fails on a ref that genuinely no
                    # longer exists — the ONE case that may be reported as
                    # already-gone, and the control that keeps the regression
                    # test above from passing merely because nothing is ever
                    # called gone. MOCK_MERGE_DELETES_BRANCH names repos
                    # (owner_repo) whose merge reaps the branch this way.
                    if [[ " ${MOCK_MERGE_DELETES_BRANCH:-} " == *" $repo_slug "* \
                          && -n "$head_ref" ]]; then
                        git -C "${MOCK_BARE_DIR}/${repo_slug}" \
                            update-ref -d "refs/heads/${head_ref}" 2>/dev/null || true
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
    # The default mode ships the STUB, not base.md. base.md now reaches a
    # session once, through user memory, instead of once per attached repo —
    # so its headings being ABSENT here is the change working, and their
    # presence would mean a repo had silently gone back to carrying its own
    # ~52 kB copy.
    stub_heading=$(grep -m1 '^## ' "$REPO_ROOT/agents-md/stub.md")
    if [ -z "$stub_heading" ]; then
        fail "agents-md/stub.md has no '## ' heading to use as a stub sentinel"
    fi
    assert_contains "$TEST_DIR/build-output.md" "$stub_heading" "includes stub content"
    assert_not_contains "$TEST_DIR/build-output.md" "$base_heading" "stub mode does not inline base content"
    assert_contains "$TEST_DIR/build-output.md" "Mode: stub" "reports stub mode"
    assert_contains "$TEST_DIR/build-output.md" "## Python" "includes python section"
    assert_contains "$TEST_DIR/build-output.md" "## Docker" "includes docker section"
    assert_not_contains "$TEST_DIR/build-output.md" "## Go" "does not include unrequested section"

    # Test with no sections
    output=$("$REPO_ROOT/scripts/build-agents-md.sh")
    echo "$output" > "$TEST_DIR/build-no-sections.md"
    assert_contains "$TEST_DIR/build-no-sections.md" "Sections: none" "reports none when no sections"
    assert_contains "$TEST_DIR/build-no-sections.md" "$stub_heading" "still includes the stub"

    # full mode — the fail-safe for a repo the fleet-memory hook cannot reach
    # (.claude/ gitignored, or an unparseable settings.json). Such a repo must
    # keep the WHOLE guidance inline; a stub there would point at a delivery
    # that is never going to happen.
    output=$(AGENTS_MD_MODE=full "$REPO_ROOT/scripts/build-agents-md.sh")
    echo "$output" > "$TEST_DIR/build-full.md"
    assert_contains "$TEST_DIR/build-full.md" "$base_heading" "full mode inlines base content"
    assert_contains "$TEST_DIR/build-full.md" "Mode: full" "full mode reports itself"

    # An unrecognised mode must stop, not silently pick one. Captured without a
    # pipe so the exit code is the script's own.
    if AGENTS_MD_MODE=bogus "$REPO_ROOT/scripts/build-agents-md.sh" >/dev/null 2>&1; then
        fail "build: an unknown AGENTS_MD_MODE is rejected"
    else
        pass "build: an unknown AGENTS_MD_MODE is rejected"
    fi

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

    # ── the 404 that arrives on STDERR ONLY ──────────────────────────────
    #
    # sync.sh tells "this repo ships no .agents-sync.yml" from "the credential
    # could not read it" by matching TWO surfaces of the status — `(HTTP 404)`
    # in gh's own stderr line and `"status":"404"` in the body gh leaves on
    # stdout — and its comment says outright that "only one of them may reach
    # us". Until MOCK_CONTENTS_404_STDERR_ONLY the mock produced only the stdout
    # one, so the stderr needle was never executed against anything: measured,
    # replacing it with "(HTTP 404 NEVERMATCH)" left the suite at 907 passed /
    # 0 failed, while breaking the stdout regex moved 7 assertions.
    #
    # `repo-no-sync` is the fixture because its `.agents-sync.yml` really is
    # absent, so the correct verdict is the one this run already asserts
    # elsewhere: fall back to `default_sections` (rust) and count no failure.
    # `--dry-run` keeps the leg free of writes, so it can sit here in the
    # ordered lane before the first run that touches the fleet.
    local before404 after404 out404="$TEST_DIR/sync-404-stderr.txt" exit404=0
    before404=$(bare_fleet_fingerprint)
    GITHUB_REPOSITORY_OWNER=testorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    MOCK_CONTENTS_404_STDERR_ONLY="testorg_repo-no-sync" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" --dry-run > "$out404" 2>&1 || exit404=$?

    assert_contains "$out404" "Sections: rust" \
        "404 on stderr (sync): a status gh reports only on stderr is still read as absent"
    assert_not_contains "$out404" "could not read .agents-sync.yml" \
        "404 on stderr (sync): the absent file was not promoted to a failed read"
    assert_contains "$out404" "0 failed" \
        "404 on stderr (sync): counted as no failure"
    if [[ $exit404 -eq 0 ]]; then
        pass "404 on stderr (sync): the run exits 0"
    else
        fail "404 on stderr (sync): the run exits 0 — got $exit404"
    fi
    after404=$(bare_fleet_fingerprint)
    if [[ "$before404" == "$after404" ]]; then
        pass "404 on stderr (sync): --dry-run still wrote nothing"
    else
        fail "404 on stderr (sync): --dry-run wrote to the fleet"
    fi
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


# ── Test: the marker is matched as a WHOLE LINE, not a substring ──────────
#
# The managed block's own BEGIN header quotes the marker verbatim:
#   <!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
# so a substring match calls the marker present in a file that only mentions it.
# Both of drift-report.sh's marker operations were substring matches; the `sed`
# one deleted the entire file and made every repo report drift-detected.
#
# This asserts the two expressions directly, because the failure they produce
# downstream (an empty managed block) looks identical to a legitimately empty
# one, and a row-level test cannot tell those apart.
test_drift_report_marker_is_whole_line() {
    echo ""
    echo "=== Test: drift-report.sh (marker matched as a whole line) ==="

    local marker='## Repo-specific additions'
    local header='<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->'

    # A file that only MENTIONS the marker does not have it.
    if ! printf '%s\nbody\n' "$header" | grep -qxF "$marker"; then
        pass "marker: a file that only quotes the marker is not credited with it"
    else
        fail "marker: the header comment was mistaken for the marker"
    fi

    # A file that really has it, does.
    if printf '%s\n%s\ntail\n' "$header" "$marker" | grep -qxF "$marker"; then
        pass "marker: a real marker line is found"
    else
        fail "marker: a real marker line was missed"
    fi

    # The slice must keep the managed half, not annihilate the file. Unanchored
    # this returns 0 lines; that is the bug, and this is its regression test.
    local doc kept
    doc=$(printf '%s\nmanaged-1\nmanaged-2\n%s\nrepo-specific\n' "$header" "$marker")
    kept=$(printf '%s\n' "$doc" | sed "/^${marker}\$/,\$d" | wc -l)
    if [[ "$kept" -eq 3 ]]; then
        pass "marker: the anchored slice keeps the managed block (3 lines)"
    else
        fail "marker: anchored slice kept $kept lines, expected 3"
    fi

    kept=$(printf '%s\n' "$doc" | sed "/${marker}/,\$d" | wc -l)
    if [[ "$kept" -eq 0 ]]; then
        pass "marker: (control) the UNanchored slice really does delete everything"
    else
        fail "marker: control failed — unanchored slice kept $kept lines, so this test proves nothing"
    fi
}

# ── Test: drift-report.sh refuses to report on a partial read (#81) ────────
#
# The bug: six repos were reported as missing their AGENTS.md marker, and then
# as drift-detected, because fetch_file_content returned fewer bytes than the
# file actually has. A short read and a genuine absence are indistinguishable
# downstream -- both are just a string without the marker in it -- so every
# column in those rows was wrong and the report published them with no hint.
#
# The fix verifies the decode against the API's own `size`. This drives that
# path with MOCK_TRUNCATE_CONTENTS, which serves a short body while still
# declaring the honest size.
#
# NOTE the negative control. A test that only asserts the truncated case fails
# would ALSO pass if fetch_file_content were broken outright and returned
# nothing for anyone, so the same fixture runs untruncated first and must
# produce a normal report. Without that, this cannot tell "the guard works"
# from "the fetch is dead".
test_drift_report_partial_read() {
    echo ""
    echo "=== Test: drift-report.sh (refuses to report on a partial read, #81) ==="

    local rpt="$TEST_DIR/drift-partial-read.md"

    local control
    control=$(
        GITHUB_REPOSITORY_OWNER=testorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true

    # Scope to TABLE ROWS. The legend also names every status, so an
    # unscoped `grep -q fetch-failed` matches this document unconditionally --
    # an assertion that cannot fail, which is the exact defect this test exists
    # to catch. Rows start '| [`owner/repo`](...'; legend entries do not.
    local ctl_ok
    ctl_ok=$(grep -cE '^\| \[`[^`]+`\].*\*\*up-to-date\*\*' "$rpt" || true)
    if [[ "$ctl_ok" -gt 0 ]] &&
       ! grep -qE '^\| \[`[^`]+`\].*fetch-failed' "$rpt"; then
        pass "partial read (control): untruncated run produces up-to-date rows and flags nothing"
    else
        fail "partial read (control): expected up-to-date rows and no fetch-failed row (got $ctl_ok up-to-date)"
    fi

    if ! echo "$control" | grep -q '::error::.*partial read'; then
        pass "partial read (control): no spurious ::error:: on a complete fetch"
    else
        fail "partial read (control): complete fetch wrongly flagged as partial"
    fi

    # Same fixtures, truncated. 200 base64 chars decodes to ~150 bytes: far
    # short of any AGENTS.md, and short of the marker.
    local out
    out=$(
        MOCK_TRUNCATE_CONTENTS=200 \
        GITHUB_REPOSITORY_OWNER=testorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true

    if grep -qE '^\| \[`[^`]+`\].*fetch-failed' "$rpt"; then
        pass "partial read: the row reports fetch-failed instead of a guessed status"
    else
        fail "partial read: expected a fetch-failed row, got none"
    fi

    # The invariant, rather than a named fixture: a partial read must never
    # produce a CONFIDENT verdict. Naming a repo is brittle here -- which
    # fixtures carry an AGENTS.md depends on which sync tests have run, and a
    # fixture small enough that a 200-char truncation is a no-op fetches whole
    # and may legitimately drift. What must hold regardless is that no row
    # claims to have compared a file it did not receive.
    #
    # This has teeth because the control run above produces up-to-date rows for
    # every fixture: if the guard stopped working, they would reappear here.
    local up_to_date
    # `\*\*up-to-date\*\*`, not `up-to-date`: the status cell is bolded and the
    # repo NAME is not -- and one fixture is called `repo-up-to-date-no-claude`,
    # so a bare substring match reads a status off the repo's name.
    up_to_date=$(grep -cE '^\| \[`[^`]+`\].*\*\*up-to-date\*\*' "$rpt" || true)
    if [[ "$up_to_date" -eq 0 ]]; then
        pass "partial read: no row claims up-to-date off a short read"
    else
        fail "partial read: $up_to_date row(s) claimed up-to-date from a partial fetch -- the #81 cascade is back; rows: $(grep -E '^\| \[`[^`]+`\].*\*\*up-to-date\*\*' "$rpt" | sed 's/](http[^)]*)//' | tr '\n' ' ')"
    fi

    if echo "$out" | grep -q '::error::.*refusing to report on a partial read'; then
        pass "partial read: emits a loud ::error:: naming both byte counts"
    else
        fail "partial read: the short read was silent -- no ::error:: annotation"
    fi

    # Restore a clean report for any test that runs after this one.
    (
        GITHUB_REPOSITORY_OWNER=testorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" >/dev/null 2>&1
    ) || true
}

# ── Test 4a2: a partial read of ONE file, at each call site (#81) ─────────
#
# The test above drives the guard through exactly ONE of the six places this
# loop reads a file, and could not have driven it through any other. Truncating
# globally truncates AGENTS.md too; AGENTS.md is fetched early, its branch
# records the failure, and the row is **fetch-failed** before CLAUDE.md,
# `.agents-sync.yml`, `skills.lock` or `.claude/settings.json` is asked for at
# all. So the other five sites kept the `|| VAR=""` spelling — a short read
# arriving as an EMPTY STRING — and the suite stayed green over it. That is
# issue #81 itself, still live, one file's name over.
#
# The fixtures made it unreachable rather than merely untested: they are all a
# few hundred bytes, so nothing in them crosses a size cliff on its own, while
# in production truncation is size-driven and therefore per-file by nature.
# MOCK_TRUNCATE_PATHS is what supplies that asymmetry.
#
# Each case below names the CONFIDENT WRONG ANSWER the old spelling produced,
# and asserts it is gone as well as asserting the withheld one is there. Both
# halves are needed: `?` appearing somewhere in the row does not establish that
# `missing` left it.
#
# `bootorg/repo-adopted` is the fixture for all four because it is the only one
# carrying every file at once — a lock, a registered settings.json, a delivered
# hook, a bridging CLAUDE.md and its own `.agents-sync.yml` — so one repo can
# exercise four call sites and each case differs only in which path is short.
drift_report_bootorg() {   # <output file> [space-separated paths to truncate]
    local out="$1" paths="${2:-}"
    # 4 base64 characters decode to 3 bytes, so this is a real truncation of any
    # file over 3 bytes long — every fixture file here is hundreds. `${paths:+4}`
    # leaves MOCK_TRUNCATE_CONTENTS empty when no path was named, which is what
    # makes the same helper serve the control run.
    MOCK_TRUNCATE_CONTENTS="${paths:+4}" \
    MOCK_TRUNCATE_PATHS="$paths" \
    GITHUB_REPOSITORY_OWNER=bootorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    DRIFT_REPORT_OUTPUT="$TEST_DIR/drift-perfile.md" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/drift-report.sh" > "$out" 2>&1 || true
}

test_drift_report_partial_read_per_file() {
    echo ""
    echo "=== Test: drift-report.sh (a partial read of ONE file, per call site, #81) ==="

    local rpt="$TEST_DIR/drift-perfile.md"

    # ── CONTROL, and it carries more weight here than the usual one. Every
    # assertion below is "this cell stopped saying X", which a fetch that had
    # died outright would also satisfy. The control pins the four cells to the
    # confident values they legitimately hold when every byte arrives.
    drift_report_bootorg "$TEST_DIR/drift-perfile-control.txt"
    assert_row_contains "$rpt" "repo-adopted" "ok" \
        "per-file (control): with every file whole, skills-bootstrap reads ok"
    assert_row_contains "$rpt" "repo-adopted" "bridge-ok" \
        "per-file (control): with every file whole, the CLAUDE.md bridge reads bridge-ok"
    assert_row_contains "$rpt" "repo-adopted" "python" \
        "per-file (control): with every file whole, Sections comes from .agents-sync.yml"
    if ! grep -qE '^\| \[`[^`]+`\].*fetch-failed' "$rpt"; then
        pass "per-file (control): a complete run flags no row fetch-failed"
    else
        fail "per-file (control): a complete run already has a fetch-failed row, so the cases below prove nothing"
    fi

    # ── CLAUDE.md. Wrong answer: `missing`, whose legend reads "sync adds the
    # bridge in its next PR" — a to-do manufactured out of a CLAUDE.md that is
    # already bridging correctly.
    drift_report_bootorg "$TEST_DIR/drift-perfile-claude.txt" "CLAUDE.md"
    assert_row_contains "$rpt" "repo-adopted" "**fetch-failed**" \
        "per-file CLAUDE.md: the row's Status is fetch-failed"
    assert_row_contains "$rpt" "repo-adopted" "?" \
        "per-file CLAUDE.md: the bridge column is withheld"
    assert_row_lacks_cell "$rpt" "repo-adopted" "missing" \
        "per-file CLAUDE.md: the row does NOT claim the bridge is missing"
    assert_row_note_contains "$rpt" "repo-adopted" '`CLAUDE.md`' \
        "per-file CLAUDE.md: Notes name the file that could not be read"

    # ── .agents-sync.yml. Wrong answer is the nastiest of the four because it
    # is plausible: the section list falls back to DEFAULT_SECTIONS (`rust` in
    # this fixture), `expected` is built from sections the repo never asked for,
    # and the row publishes **drift-detected** against an AGENTS.md that is in
    # fact correct.
    drift_report_bootorg "$TEST_DIR/drift-perfile-sections.txt" ".agents-sync.yml"
    assert_row_contains "$rpt" "repo-adopted" "**fetch-failed**" \
        "per-file .agents-sync.yml: the row's Status is fetch-failed"
    assert_row_lacks_cell "$rpt" "repo-adopted" "rust" \
        "per-file .agents-sync.yml: Sections does NOT fall back to default_sections"
    assert_row_lacks_cell "$rpt" "repo-adopted" "**drift-detected**" \
        "per-file .agents-sync.yml: no drift is declared against a section list that never arrived"
    assert_row_note_contains "$rpt" "repo-adopted" '`.agents-sync.yml`' \
        "per-file .agents-sync.yml: Notes name the file that could not be read"

    # ── skills.lock. Wrong answer: the lock reads absent, and with a hook
    # sitting beside it that is **degraded** — a standing "remove the hook by
    # hand" addressed to a human about a repo that is fine.
    drift_report_bootorg "$TEST_DIR/drift-perfile-lock.txt" "skills.lock"
    assert_row_contains "$rpt" "repo-adopted" "**fetch-failed**" \
        "per-file skills.lock: the row's Status is fetch-failed"
    assert_row_contains "$rpt" "repo-adopted" "?" \
        "per-file skills.lock: the skills-bootstrap column is withheld"
    # No `no-lock` assertion here. Both `no-lock` and **degraded** hang off the
    # lock reading ABSENT, and drift-report.sh picks between them on whether a
    # hook is sitting beside it — bootorg/repo-adopted has one, so the pre-fix
    # wrong answer for THIS fixture is **degraded** and `no-lock` is
    # unreachable. A guard over an unreachable branch is a guard that cannot
    # fail; the assertion below covers the wrong answer this fixture can
    # actually produce.
    assert_row_lacks_cell "$rpt" "repo-adopted" "**degraded**" \
        "per-file skills.lock: the row does NOT declare a degraded hook off a short read"
    assert_row_note_contains "$rpt" "repo-adopted" '`skills.lock`' \
        "per-file skills.lock: Notes name the file that could not be read"

    # ── .claude/settings.json. Wrong answer: **no-entry**, the loudest verdict
    # this column has ("nothing runs it — Silently dead"), invented out of a
    # settings.json whose SessionStart entry is present and correct.
    drift_report_bootorg "$TEST_DIR/drift-perfile-settings.txt" ".claude/settings.json"
    assert_row_contains "$rpt" "repo-adopted" "**fetch-failed**" \
        "per-file settings.json: the row's Status is fetch-failed"
    assert_row_contains "$rpt" "repo-adopted" "?" \
        "per-file settings.json: the skills-bootstrap column is withheld"
    assert_row_lacks_cell "$rpt" "repo-adopted" "**no-entry**" \
        "per-file settings.json: the row does NOT claim the hook is registered nowhere"
    assert_row_note_contains "$rpt" "repo-adopted" '`.claude/settings.json`' \
        "per-file settings.json: Notes name the file that could not be read"

    # Every case must have gone through the guard rather than through some other
    # path that happens to print `?`; the ::error:: is what says the byte counts
    # disagreed.
    if grep -q '::error::.*refusing to report on a partial read' "$TEST_DIR/drift-perfile-settings.txt"; then
        pass "per-file: the short read is announced loudly, not just rendered as ?"
    else
        fail "per-file: no ::error:: naming the partial read"
    fi

    # Restore the untruncated bootorg report for anything that runs after this.
    drift_report_bootorg "$TEST_DIR/drift-perfile-restore.txt"
}

# ── Test 4: drift-report.sh ───────────────────────────────────────────────

test_drift_report() {
    echo ""
    echo "=== Test: drift-report.sh ==="

    local rpt="$TEST_DIR/drift-report-full.md"

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=testorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true

    echo "$output" > "$TEST_DIR/drift-output.txt"

    assert_contains "$rpt" "# AGENTS.md Drift Report" "drift report has title"
    assert_contains "$rpt" "repo-with-sync" "drift report includes repo-with-sync"
    assert_contains "$rpt" "repo-no-sync" "drift report includes repo-no-sync"
    assert_contains "$rpt" "repo-with-existing" "drift report includes repo-with-existing"
    assert_contains "$rpt" "Status legend" "drift report has legend"
    assert_contains "$rpt" "Organization:" "drift report shows org"
    assert_contains "$rpt" "7 repo(s) scanned" "drift report shows repo count"
    assert_not_contains "$rpt" "_agent-guidance" "drift report excludes self"
    assert_not_contains "$rpt" "repo-excluded" "drift report excludes repos.yml-excluded repo"

    # ── The Status column, which until 2026-08-28 nothing asserted ────────
    #
    # That absence is how an unanchored `sed` address survived: it matched the
    # managed block's BEGIN header (which quotes the marker verbatim) on line 1
    # and deleted to EOF, so `current_managed` was always EMPTY, so EVERY repo
    # compared unequal and reported drift-detected. The report was structurally
    # incapable of printing up-to-date, and all 18 rows of the 2026-08-27 run
    # said drift-detected. The suite ran green throughout, because it only ever
    # asserted that repo NAMES and headings appeared.
    #
    # A fixture that IS in sync must therefore say so. This is the assertion
    # that can fail; the name checks above cannot.
    assert_row_contains "$rpt" "repo-up-to-date-no-claude" "**up-to-date**" \
        "drift report: an in-sync repo reports up-to-date, not drift"

    # And an empty open-PR list must render as "none". `.[0].number` over `[]`
    # produces the literal string "null", which is non-empty — so every repo
    # showed `#null` in the Open PR column AND had its real status overwritten
    # by **pr-open**, hiding genuine drift behind a phantom pull request.
    if ! grep -qF -- '#null' "$rpt"; then
        pass "drift report: an empty PR list renders as none, not #null"
    else
        fail "drift report: an empty PR list rendered as #null; rows: $(grep -F -- '#null' "$rpt" | sed 's/](http[^)]*)//' | tr '\n' ' ')"
    fi

    # CLAUDE.md bridge column
    assert_contains "$rpt" "CLAUDE.md bridge" "drift report has CLAUDE.md bridge column"
    assert_row_contains "$rpt" "repo-with-existing" "bridge-ok" "drift report: repo-with-existing is bridge-ok"
    assert_row_contains "$rpt" "repo-with-claude-md" "**no-import**" "drift report: repo-with-claude-md is no-import"
    assert_row_contains "$rpt" "repo-fix-claude" "**no-import**" "drift report: repo-fix-claude is no-import"
    assert_row_contains "$rpt" "repo-no-sync" "missing" "drift report: repo-no-sync bridge is missing"
    assert_contains "$rpt" "CLAUDE.md bridge legend" "drift report has CLAUDE.md bridge legend"

    # Cron-coverage classification: the fixture repos.yml classifies every mock
    # repo, so the report must say NOTHING. Asserted here rather than only in
    # the flagging test because a check that fires on a fully-classified fleet
    # is noise nobody would read twice.
    assert_not_contains "$rpt" "Unclassified for cron coverage" \
        "drift report: a fully classified fleet raises nothing"
}

# ── Test 4b: drift-report.sh skills-bootstrap column ──────────────────────

test_drift_report_bootstrap() {
    echo ""
    echo "=== Test: drift-report.sh (skills-bootstrap column) ==="

    # Observe the fully-delivered state produced by test_sync_bootstrap.
    local rpt="$TEST_DIR/drift-bootstrap.md"
    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/drift-bootstrap-output.txt"

    assert_contains "$rpt" "skills-bootstrap" "drift report has a skills-bootstrap column"
    assert_contains "$rpt" "skills-bootstrap legend" "drift report has a skills-bootstrap legend"

    assert_row_contains "$rpt" "repo-adopted" "ok" "drift report: repo-adopted is ok"
    # The cheapest answer to invisible lock staleness: print what each lock
    # pins, per federated source.
    assert_row_note_contains "$rpt" "repo-adopted" "lock: agentskills@1111111 + cms-platform@2222222" "drift report: repo-adopted's federated lock pins are shown"
    assert_row_contains "$rpt" "repo-no-lock" "no-lock" "drift report: repo-no-lock shows the withheld state"

    # ── The three states that never self-heal must not hide inside
    #    `**missing**`, whose whole meaning is "the next sync delivers it".
    assert_row_contains "$rpt" "repo-ignored" "**blocked**" "drift report: a gitignored .claude/ is blocked, not missing"
    assert_row_note_contains "$rpt" "repo-ignored" '`.claude/` gitignored' "drift report: repo-ignored's Notes name the reason"
    assert_row_contains "$rpt" "repo-unparseable" "**refused**" "drift report: an unparseable settings.json is refused, not missing"
    assert_row_note_contains "$rpt" "repo-unparseable" '`settings.json` unparseable' "drift report: repo-unparseable's Notes name the reason"
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

# The skills half of the same contract, and the reason the offline gate
# (scripts/check-registry.js) is not enough on its own: that one can only
# assert the two keys do not CONTRADICT each other. Whether they together still
# COVER the account is a discovery question, and discovery happens here.
test_drift_report_skills_classification() {
    echo ""
    echo "=== Test: drift-report.sh (skills-bootstrap classification) ==="

    # Drop one repo from the complement — the state a newly created repo is in,
    # and the state this whole mechanism exists to make visible. Anchored on
    # the flow-mapping spelling so the edit cannot reach cron_coverage, which
    # names the same repo.
    sed '/{repo: repo-with-sync, reason: test fixture}/d' "$TEST_DIR/repos.yml" \
        > "$TEST_DIR/repos-unskilled.yml"

    # A no-op sed would make every assertion below pass against an unmodified
    # file, which is the shape of a test that proves nothing.
    if diff -q "$TEST_DIR/repos.yml" "$TEST_DIR/repos-unskilled.yml" >/dev/null; then
        fail "drift report: the unclassified-skills fixture did not change repos.yml"
        return
    fi

    local rpt="$TEST_DIR/drift-unskilled.md"
    local side="$TEST_DIR/drift-unskilled-skills-unclassified.txt"
    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=testorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos-unskilled.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/drift-unskilled-output.txt"

    assert_contains "$rpt" "Unclassified for skills-bootstrap (1)" \
        "drift skills: an unclassified repo is counted"
    assert_contains "$rpt" "testorg/repo-with-sync" \
        "drift skills: the unclassified repo is named"
    assert_contains "$rpt" "skills_bootstrap.out_of_scope" \
        "drift skills: the finding says which keys resolve it"
    assert_contains "$TEST_DIR/drift-unskilled-output.txt" "1 repo(s) unclassified for skills-bootstrap" \
        "drift skills: the run log surfaces the count too"

    # A repo classified under EITHER key must not be swept in with it — one
    # control per key, because a predicate that had stopped reading one of them
    # would still satisfy the other.
    if grep -q '^> - `testorg/repo-no-sync`' "$rpt"; then
        fail "drift skills: an out_of_scope repo was reported unclassified"
    else
        pass "drift skills (control): an out_of_scope repo is not flagged"
    fi
    if grep -q '^> - `testorg/repo-adopted`' "$rpt"; then
        fail "drift skills: an allowlisted repo was reported unclassified"
    else
        pass "drift skills (control): an allowlisted repo is not flagged"
    fi

    # THE SIDECAR, which is what a workflow reads. A report a human has to open
    # is not a notification, and a scheduled run notifies nobody.
    if [[ -f "$side" ]] && grep -qxF -- "testorg/repo-with-sync" "$side"; then
        pass "drift skills: the sidecar names the repo for a reader that is not a person"
    else
        fail "drift skills: the sidecar names the repo for a reader that is not a person — $(cat "$side" 2>/dev/null | tr '\n' ' ')"
    fi

    # ── CONTROL: the same run against the UNMODIFIED registry writes the
    # sidecar and leaves it EMPTY. Without this, a script that never wrote the
    # file at all would pass every assertion above, and "absent" would come to
    # mean "nothing found" — the exact conflation the file is written
    # unconditionally to prevent.
    local crpt="$TEST_DIR/drift-skilled.md"
    local cside="$TEST_DIR/drift-skilled-skills-unclassified.txt"
    GITHUB_REPOSITORY_OWNER=testorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    DRIFT_REPORT_OUTPUT="$crpt" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/drift-report.sh" >/dev/null 2>&1 || true

    if [[ -f "$cside" ]]; then
        pass "drift skills (control): the sidecar is written even with nothing to report"
    else
        fail "drift skills (control): the sidecar is written even with nothing to report — it is absent, so absence cannot mean 'clean'"
    fi
    if [[ -f "$cside" && ! -s "$cside" ]]; then
        pass "drift skills (control): and it is empty when every repo is classified"
    else
        fail "drift skills (control): and it is empty when every repo is classified — got: $(cat "$cside" 2>/dev/null | tr '\n' ' ')"
    fi
    assert_not_contains "$crpt" "Unclassified for skills-bootstrap" \
        "drift skills (control): a fully classified fleet says nothing at all"
}

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

    local rpt="$TEST_DIR/drift-cron.md"
    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos-uncron.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/drift-uncron-output.txt"

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

    local rpt="$TEST_DIR/drift-unmanaged.md"
    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos-dropped.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/drift-unmanaged-output.txt"

    assert_row_contains "$rpt" "repo-adopted" "**unmanaged**" "drift report: a hook left behind by a de-allowlisted repo is flagged unmanaged"
}

# ── Test 4d: the registry itself is never `unmanaged` ─────────────────────

test_drift_report_bootstrap_registry() {
    echo ""
    echo "=== Test: drift-report.sh (the registry is not an unmanaged hook) ==="

    # The registry AUTHORS the hook and is deliberately off the allowlist, so
    # a naive "not allowlisted + has a hook" rule points `unmanaged`'s "remove
    # it by hand" advice straight at the source of truth. Scan it as a target
    # once and prove it does not.
    local rpt="$TEST_DIR/drift-registry.md"
    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_INCLUDE_REGISTRY=1 \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/drift-registry-output.txt"

    if grep -F "bootorg/agentskills" "$rpt" | grep -qF "**unmanaged**"; then
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

    # Every drift test used to write the report to the same fixed path, so the
    # preceding one could leave the very same `repo-ignored | **blocked**` row
    # there from its own bootorg fixtures, and the guard below could read that
    # stale row and pass even when the script under test never ran at all --
    # it used to require an `rm -f "$REPO_ROOT/drift-report.md"` right here,
    # immediately before the run, so the row could only have come from THIS
    # invocation. Named under $TEST_DIR via DRIFT_REPORT_OUTPUT below, this
    # run's report cannot collide with any other test's, so there is nothing
    # left to delete.
    local rpt="$TEST_DIR/drift-cleanup.md"

    local output
    output=$(
        GITHUB_REPOSITORY_OWNER=bootorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        TMPDIR="$probe_tmp" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true
    echo "$output" > "$TEST_DIR/drift-cleanup-output.txt"

    # Guard against a vacuous pass. If no repo reaches the probe, no directory
    # is ever created and the leak check below would pass without testing
    # anything. `repo-ignored` reaches `**blocked**` only THROUGH the probe, so
    # this assertion is what proves the run exercised the path.
    assert_row_contains "$rpt" "repo-ignored" "**blocked**" \
        "drift report cleanup: the run actually exercised the ignore probe"

    local leftover
    leftover=$(find "$probe_tmp" -mindepth 1 -maxdepth 1)
    if [[ -z "$leftover" ]]; then
        pass "drift report: the ignore probe directory is removed on exit"
    else
        fail "drift report: the ignore probe directory is removed on exit — left behind: $(echo "$leftover" | tr '\n' ' ')"
    fi
}

# ── Test 4h: a read that FAILED is not a file that is ABSENT ──────────────
#
# `gh api ... || return 0` said it was, and that is one return statement
# standing between a credential fault and the whole #81 cascade: for a 401, a
# 403, a rate limit, a 5xx, a DNS or TLS failure, `fetch_file_content` handed
# back an empty string and exit 0, so every caller's `rc != 0` branch was blind
# to the entire class and the row published `missing`, `no-lock`, **no-entry**
# and **blocked** as confident verdicts about files nobody had seen.
#
# MOCK_CONTENTS_HTTP_FAIL answers EVERY contents path for this repo with a 403,
# which is exactly the shape of an App that lost Contents permission mid-run —
# so this leg is also the general check on the function's contract: a non-404
# must return 2, at every call site, not 0 with an empty stdout.
test_drift_report_contents_unreadable() {
    echo ""
    echo "=== Test: drift-report.sh (every contents read fails, not 404) ==="

    local out="$TEST_DIR/drift-contents-403.txt"
    local rpt="$TEST_DIR/drift-contents-403.md"

    # See the sync leg's note on MOCK_CONTENTS_STDERR_PRENOTICE: it is what
    # makes gh's stderr more than one line long, and so what gives the scoped
    # assertion below something to be wrong about.
    GITHUB_REPOSITORY_OWNER=sfailorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    MOCK_CONTENTS_HTTP_FAIL="sfailorg_repo-sync-yml" \
    MOCK_CONTENTS_STDERR_PRENOTICE="sfailorg_repo-sync-yml" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    DRIFT_REPORT_OUTPUT="$rpt" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/drift-report.sh" > "$out" 2>&1 || true

    assert_contains "$out" "refusing to report it as one" \
        "drift 403: the run refuses to call an unreadable file an absent one"
    assert_scoped_line "$out" "the contents read failed" "HTTP 403" \
        "drift 403: the ::error:: quotes gh's own status line"
    assert_scoped_line_lacks "$out" "the contents read failed" "token expires" \
        "drift 403: the ::error:: names the status, not the notice ahead of it"
    assert_row_contains "$rpt" "repo-sync-yml" "**fetch-failed**" \
        "drift 403: the row's Status is fetch-failed"
    # The confident verdicts a 0-with-empty-stdout return used to publish, for
    # the call sites THIS fixture actually reaches.
    assert_row_lacks_cell "$rpt" "repo-sync-yml" "**no-agents-md**" \
        "drift 403: it does NOT report the repo as having no AGENTS.md"
    assert_row_lacks_cell "$rpt" "repo-sync-yml" "missing" \
        "drift 403: it does NOT report the CLAUDE.md bridge as missing"
    # No `no-lock` assertion, for the same reason the skills.lock leg of
    # test_drift_report_partial_read_per_file carries none, one column over:
    # that verdict is assigned only inside `elif bootstrap_allowlisted`, and
    # this repo is not on the fixture repos.yml's `skills_bootstrap.repos`, so
    # the branch is unreachable for it whatever fetch_file_content returns.
    # Measured by restoring the pre-#81 `return 0` in fetch_file_content: every
    # other assertion in this test fails and a `no-lock` guard sails through.
    # The lock call site's #81 contract is asserted where it IS reachable — the
    # **degraded** case in the per-file test.
    assert_row_contains "$rpt" "repo-sync-yml" "?" \
        "drift 403: the columns those files feed are withheld"

    # The CONTROL: the same repo with nothing injected must produce a confident
    # row, or the assertions above would hold for a report that fetch-fails
    # everything.
    local ctl="$TEST_DIR/drift-contents-control.md"
    GITHUB_REPOSITORY_OWNER=sfailorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    DRIFT_REPORT_OUTPUT="$ctl" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/drift-report.sh" > "$TEST_DIR/drift-contents-control.txt" 2>&1 || true
    assert_row_lacks_cell "$ctl" "repo-sync-yml" "**fetch-failed**" \
        "drift 403 (control): the uninjured run publishes a confident row"
}

# ── Test 4i: a `.agents-sync.yml` that arrives whole and will not parse ────
#
# The other door onto the same wrong answer, and the nastier one, because the
# bytes really did all arrive — the byte-count guard above has nothing to say.
# `yq ... 2>/dev/null || true` inside a process substitution threw away BOTH
# halves of the status (the one process substitution swallows by design, and
# the one `|| true` discards on top), so an unparseable file collapsed to an
# empty section list. `expected` was then built from zero sections, diffed
# against an AGENTS.md that is in fact correct, and the row published
# **drift-detected**.
test_drift_report_sync_yml_unparseable() {
    echo ""
    echo "=== Test: drift-report.sh (.agents-sync.yml will not parse) ==="

    local out="$TEST_DIR/drift-syncyml-parse.txt"
    local rpt="$TEST_DIR/drift-syncyml-parse.md"

    sync_yaml_fixture_write 'sections: [python
'
    GITHUB_REPOSITORY_OWNER=sfailorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    DRIFT_REPORT_OUTPUT="$rpt" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/drift-report.sh" > "$out" 2>&1 || true

    assert_contains "$out" "yq could not parse it" \
        "drift yml parse: the run says the file would not parse"
    assert_row_contains "$rpt" "repo-sync-yml" "**fetch-failed**" \
        "drift yml parse: the row's Status is fetch-failed"
    assert_row_lacks_cell "$rpt" "repo-sync-yml" "**drift-detected**" \
        "drift yml parse: no drift is declared against a section list that never parsed"
    # This one guards the OTHER mis-fix of the same door, not the historical
    # one, and it is worth saying so because the historical one cannot reach it:
    # the empty-section collapse leaves Sections reading `none`, and `rust`
    # arrives only if somebody decides an unparseable file should fall back to
    # `default_sections`. Measured — replacing the parse-failure arm with
    # `sections=("${DEFAULT_SECTIONS[@]}")` publishes `rust` in the Sections
    # cell and fails exactly this line. The needle is not `none`, which would be
    # the collapse's own wrong answer: `none` is also what the Open PR column
    # holds on every row with no open PR, and assert_row_lacks_cell matches any
    # cell, so it would fail against a correct run.
    assert_row_lacks_cell "$rpt" "repo-sync-yml" "rust" \
        "drift yml parse: Sections does NOT fall back to default_sections"
    assert_row_note_contains "$rpt" "repo-sync-yml" '`.agents-sync.yml`' \
        "drift yml parse: Notes name the file"

    # Put the fixture back for anything that reads it after this.
    sync_yaml_fixture_write 'sections:
  - python
'

    # ── the same file, a yq that PARSES it, and a line on stderr ─────────
    #
    # The third door onto **drift-detected**, and the only one that opens on a
    # run where nothing failed. The two legs above are about a section list the
    # script could not establish; this is about one it established correctly
    # and then had a diagnostic appended to. yq writes to stderr while exiting
    # 0 — the build this repo pins does it for `-j` (see setup_noisy_yq_dir) —
    # so `2>&1` on this capture puts that line in $sections, `expected` is
    # built from a phantom section, and the row this report PUBLISHES is
    # **drift-detected** against an AGENTS.md that is in fact correct.
    #
    # The Sections assertion is the load-bearing one and it is spelled as a
    # WHOLE-CELL match on purpose: the corrupted cell still ends in `python`, so
    # a substring needle would pass against exactly the output this leg exists
    # to forbid.
    setup_noisy_yq_dir
    local nrpt="$TEST_DIR/drift-syncyml-noisy-yq.md"
    GITHUB_REPOSITORY_OWNER=sfailorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    DRIFT_REPORT_OUTPUT="$nrpt" \
    PATH="$TEST_DIR/bin-yq-noisy:$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/drift-report.sh" > "$TEST_DIR/drift-syncyml-noisy-yq.txt" 2>&1 || true

    assert_row_contains "$nrpt" "repo-sync-yml" "python" \
        "drift noisy yq: the Sections cell is the section list and nothing else"
    assert_row_lacks_cell "$nrpt" "repo-sync-yml" "**drift-detected**" \
        "drift noisy yq: a correct AGENTS.md is not published as drifted"
    assert_row_lacks_cell "$nrpt" "repo-sync-yml" "**fetch-failed**" \
        "drift noisy yq: a noisy but successful parse is not a failed read"
}

# ── Test 4j: the marker is read from a here-string, not through a pipe ─────
#
# Issue #81's actual root cause, and the reason this test runs the same report
# many times instead of once. `echo "$current_agents" | grep -q "$MARKER"` is a
# RACE: grep exits at the first match, and when the payload is larger than the
# kernel's 64 KiB pipe buffer the `echo` on the writing end still has bytes to
# push, takes SIGPIPE and dies 141 — which `set -o pipefail` promotes to the
# pipeline's status even though grep itself exited 0. Inside an `if` that 141
# does not end the run; it routes to the else, so a marker that IS present
# publishes as `Has marker: no`, the file is diffed whole against the expected
# managed block, and the repo goes out as **drift-detected**.
#
# Being a race is what made #81's own single-shot probe look like a refutation:
# the writer only loses when grep gets there first, so ONE green run clears
# nothing. Measured on a 95 kB file with the marker 42% in, wrong answers per
# 20 trials were 0 at 48/56/64 kB, 4 at 72 kB, and 20 at 95 kB — and 0 out of
# 20 at every size once it is a here-string. TRIALS below is set to 20 so a
# defect as rare as the 72 kB rate (2 in 10) would show with ~88% probability,
# and the fixture is sized past 72 kB where the measured rate was total.
test_drift_report_marker_not_through_a_pipe() {
    echo ""
    echo "=== Test: drift-report.sh (the marker read does not race a pipe) ==="

    local rpt="$TEST_DIR/drift-bigmarker.md"
    local agents_md="$TEST_DIR/work/repo-big-agents-md/AGENTS.md"
    local marker_line="## Repo-specific additions"
    local trials=20 i size wrong=0 row marker_at after

    # Asserted FIRST, because the byte arithmetic below is derived from it.
    if grep -qxF -- "$marker_line" "$agents_md"; then
        pass "big marker: the fixture really does carry the marker as a whole line"
    else
        fail "big marker: the fixture has no marker line, so 'yes' below would mean nothing"
        return
    fi

    # THE QUANTITY IS THE BYTES AFTER THE MARKER, not the size of the file, and
    # the arithmetic is written out because the whole-file guard this replaces
    # could stay green over a fixture that had stopped discriminating.
    #
    # `echo "$current_agents" | grep -q` loses only while the WRITER still has
    # bytes outstanding at the moment grep exits. grep exits at the first match,
    # having consumed the file up to the end of the marker line; by then the
    # writer has been able to push at most that much, plus the 64 KiB the
    # kernel's pipe buffer holds on its behalf. So the writer is still blocked —
    # and can still take SIGPIPE — exactly when
    #
    #     total bytes − (byte offset of the END of the marker line) > 64 KiB
    #
    # The file's SIZE says nothing about that on its own, and the collapse is
    # measured rather than argued. Moving this fixture's marker to sit AFTER its
    # filler leaves 10,001 bytes behind it in a file that is BIGGER than before
    # (124,906 bytes) — and the trials below then caught the piped spelling in
    # 1 of 20 runs, three times over, against 20 of 20 on the fixture as built.
    # The `size > 72 kB` guard this replaces read PASS on that degraded fixture.
    # So the failure this shape forecloses is not a fixture that shrinks — it is
    # a marker that migrates late, which leaves the size assertion green, the
    # TRIALS=20 detection budget below sized for a rate it no longer has, and
    # the whole test one scheduling accident away from a tautology.
    size=$(wc -c < "$agents_md")
    marker_at=$(grep -m1 -bxF -- "$marker_line" "$agents_md" | cut -d: -f1)
    after=$(( size - marker_at - ${#marker_line} - 1 ))   # -1 for the newline
    if [[ "$after" -gt 65536 ]]; then
        pass "big marker: $after bytes follow the marker line, clear of the 64 KiB pipe buffer (file $size bytes, marker at byte $marker_at)"
    else
        fail "big marker: only $after bytes follow the marker line (file $size bytes, marker at byte $marker_at) — that fits inside the 64 KiB pipe buffer, so the writer never blocks and the old spelling would answer correctly however large the file is"
    fi

    for (( i = 1; i <= trials; i++ )); do
        GITHUB_REPOSITORY_OWNER=bigorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" >/dev/null 2>&1 || true
        row=$(grep -F 'bigorg/repo-big-agents-md' "$rpt" || true)
        if [[ -z "$row" ]]; then
            fail "big marker: run $i published no row for the fixture"
            return
        fi
        _row_cell_equals "$row" "yes" || wrong=$((wrong + 1))
    done

    if [[ "$wrong" -eq 0 ]]; then
        pass "big marker: 'Has marker' read yes in all $trials runs of a $size-byte file"
    else
        fail "big marker: 'Has marker' read no in $wrong of $trials runs — the marker read is racing a pipe again (issue #81)"
    fi
}

# ── Test 4a: drift-report.sh with SYNC_OWNERS (multiple owners) ───────────

test_drift_report_multi_owner() {
    echo ""
    echo "=== Test: drift-report.sh (SYNC_OWNERS multi-owner) ==="

    local rpt="$TEST_DIR/drift-multi-owner.md"
    local output
    output=$(
        SYNC_OWNERS="testorg testorg2" \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" 2>&1
    ) || true

    echo "$output" > "$TEST_DIR/drift-multi-output.txt"

    assert_contains "$rpt" "## testorg" "multi-owner drift report has testorg heading"
    assert_contains "$rpt" "## testorg2" "multi-owner drift report has testorg2 heading"
    assert_contains "$rpt" "repo-owner2-only" "multi-owner drift report includes testorg2's repo"
    assert_contains "$rpt" "repo-with-sync" "multi-owner drift report still includes testorg's repos"

    local count
    count=$(grep -c "Status legend" "$rpt" || true)
    if [[ "$count" -eq 1 ]]; then
        pass "multi-owner drift report has Status legend exactly once"
    else
        fail "multi-owner drift report has Status legend exactly once — got count $count"
    fi
}

# ── Test 4f: one owner's listing failure does not end the scan ────────────
#
# The same per-owner conflation sync.sh and bump-consumer-locks.sh both have a
# test for, and it costs MORE here, because this script's entire output is a
# published dashboard. Under `set -e` a non-zero `gh repo list` ended the run
# mid-loop: with SYNC_OWNERS ordered "Adam-S-Daniel jodidaniel", one owner
# missing its App installation meant the OTHER owner was never scanned, the run
# died with no report to publish, and nothing anywhere named the owner that had
# gone unread.
#
# The report itself has to say so, not just the log: a section that is absent
# and a section with no drift in it render as the same silence to whoever opens
# drift-report.md, and only one of them means "nobody looked".
test_drift_report_owner_list_failure() {
    echo ""
    echo "=== Test: drift-report.sh (one owner cannot be listed) ==="

    local out="$TEST_DIR/drift-owner-fail.txt" exit_code=0
    # ITS OWN FILE, via DRIFT_REPORT_OUTPUT, and that is an assertion about the
    # assertions below rather than housekeeping. Every drift-report.sh run in
    # this suite used to write one global `$REPO_ROOT/drift-report.md`, so a
    # test read whichever run finished last — not necessarily the run it just
    # made. Nothing caught it because the lane happens to be ordered, which is
    # exactly the kind of guarantee that holds until someone reorders one line;
    # and this test in particular then had to end by RE-RUNNING the script to
    # put the shared file back for whoever came next, which is a coupling, not
    # a cleanup. Named under $TEST_DIR, an assertion here can only be reading
    # the output of the run above it.
    local rpt="$TEST_DIR/drift-owner-fail.md"

    SYNC_OWNERS="failorg testorg" \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    DRIFT_REPORT_OUTPUT="$rpt" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/drift-report.sh" > "$out" 2>&1 || exit_code=$?

    assert_contains "$out" "::error::failorg: could not list repos" \
        "drift owner listing: the failing owner is announced loudly"
    assert_contains "$out" "Scanning repos for: testorg" \
        "drift owner listing: the SECOND owner is still scanned"
    assert_contains "$out" "1 owner(s) could not be listed" \
        "drift owner listing: the run summarises what went unread"

    # In the PUBLISHED report, not only the log.
    assert_contains "$rpt" "## failorg" \
        "drift owner listing: the report carries a section for the unread owner"
    assert_contains "$rpt" "not scanned this run" \
        "drift owner listing: that section says nobody looked"
    assert_row_contains "$rpt" "(owner not readable)" "**fetch-failed**" \
        "drift owner listing: its placeholder row reads fetch-failed"
    # And the other owner really did produce rows, so this is not a report that
    # merely failed politely.
    assert_row_contains "$rpt" "testorg/repo-with-existing" "bridge-ok" \
        "drift owner listing: the readable owner's rows are still published"

    # Deliberately NOT an exit code: drift-report.yml's publish step carries no
    # `if: always()`, so failing here would suppress the very report that now
    # holds the unreadable owner's section.
    if [[ $exit_code -eq 0 ]]; then
        pass "drift owner listing: exits 0 so the report still publishes"
    else
        fail "drift owner listing: exits 0 so the report still publishes — got $exit_code"
    fi

    # No restoring run at the end any more. It existed only to put the shared
    # `$REPO_ROOT/drift-report.md` back for whatever ran next; with the report
    # written under $TEST_DIR this run never touched that file, so there is
    # nothing to undo.
}

# ── Test 4g: an owner that holds nothing is not one nameless repo ─────────
#
# sync.sh's twin of this is test_sync_empty_owner; the same blank line reached
# the same `mapfile` here and put a row in the PUBLISHED report for a repo
# whose name is the empty string, complete with a broken github.com link.
test_drift_report_empty_owner() {
    echo ""
    echo "=== Test: drift-report.sh (an owner with no repos) ==="

    local out="$TEST_DIR/drift-empty-owner.txt"
    # Its own file, for the reason written out in test_drift_report_owner_list_failure.
    local rpt="$TEST_DIR/drift-empty-owner.md"

    (
        GITHUB_REPOSITORY_OWNER=emptyorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" > "$out" 2>&1
    ) || true

    assert_contains "$rpt" "0 repo(s) scanned" \
        "drift empty owner: the report says zero, not one"
    assert_contains "$rpt" "(no repos found)" \
        "drift empty owner: it renders the explicit empty-fleet row"
    assert_not_contains "$rpt" "1 repo(s) scanned" \
        "drift empty owner: no phantom repo was counted"
    # The phantom's signature in the published table: a row whose repo cell is
    # an empty-named link. Scoped to a ROW so the legend cannot satisfy it.
    if grep -qE '^\| \[`\`\]|^\| \[``\]' "$rpt"; then
        fail "drift empty owner: the report carries an empty-named repo row"
    else
        pass "drift empty owner: no empty-named repo row was written"
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

# ── Test 3f: an argument sync.sh does not recognise stops it ──────────────
#
# `[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true` reads exactly one position
# for exactly one spelling and has no else. `-n`, `--dry-runn`, and `--dry-run`
# anywhere but first therefore all left DRY_RUN=false — and this script's
# not-dry-run path clones every repo in the fleet, commits, and pushes STRAIGHT
# to their default branches, which the sync App's ruleset bypass does not stop.
# An operator who typed the flag and got a real run had no signal at all: the
# log looks like an ordinary sync, because it is one.
#
# So the fingerprint below covers every ref of every bare repo, not just the
# ones this org would touch. "Wrote nothing" is the claim; anything less than
# the whole fleet is a smaller claim wearing the same words.
bare_fleet_fingerprint() {   # [bare dir — defaults to the shared fleet]
    local bare dir="${1:-$TEST_DIR/bare}"
    for bare in "$dir"/*; do
        [[ -d "$bare" ]] || continue
        printf '%s %s\n' "$(basename "$bare")" \
            "$(git -C "$bare" show-ref 2>/dev/null | sort | sha256sum | cut -d' ' -f1)"
    done
}

# published_bump_branches [bare dir] — every `skills-lock-bump/*` ref in the
# fleet, one line per branch, sorted.
#
# The narrower claim, for bump-consumer-locks.sh, and it is narrowed along a
# different axis than it used to be. bare_fleet_fingerprint above covers every
# ref because sync.sh's "wrote nothing" really does mean every ref; the
# bumper's does not, because its FIRST pass is the sweep, which reaps a bump
# branch whose PR already merged (bumporg/repo-leftover is the fixture for
# exactly that, and the real fleet had five of them at once) — a deliberate
# write that happens long before the propose pass reaches a `git commit`. So
# this is compared as a SUBSET, not for equality: a reap removes a line and is
# fine, a publish adds one and is the thing under test.
#
# It replaces a fingerprint over `refs/heads/main`, which measured a surface
# this lane cannot touch: the bumper's only push is `HEAD:refs/heads/$BRANCH_NAME`,
# so no refusal it mishandles can move a default branch, and that assertion was
# true before and after any fix. Measured — with the commit refusal's
# `cd "$REPO_ROOT"; continue` removed, so the push publishes the BASE commit
# exactly as AGENTS.md's "a successful `git push` does not mean your commit
# exists" describes, four consumers gained a bump branch and the old assertion
# still read "no repo's default branch moved: PASS".
published_bump_branches() {   # [bare dir — defaults to the shared fleet]
    local bare dir="${1:-$TEST_DIR/bare}"
    for bare in "$dir"/*; do
        [[ -d "$bare" ]] || continue
        # A directory that is not a repository publishes no bump branches, and
        # is skipped BEFORE the pipeline below rather than after it. Not
        # tidiness: `git for-each-ref` exits 128 there, `pipefail` carries that
        # through the inner `| sed` and then through the outer `| sort`, and
        # every caller here assigns this function's output BARE — so under this
        # file's `set -euo pipefail` a stray directory ends the whole run
        # instead of one test. Only the LAST iteration's status survives, which
        # is why it stayed latent; test_harness_published_bump_branches pins it.
        #
        # The probe is `|| continue` rather than a blanket `|| true` on the
        # pipeline on purpose: a for-each-ref that fails inside a REAL
        # repository still propagates, so this cannot quietly start reporting an
        # empty fleet — which, compared before and against after, would pass
        # every caller vacuously.
        git -C "$bare" rev-parse --git-dir >/dev/null 2>&1 || continue
        # `--format` carries no repo name of its own: a bare's basename is
        # prefixed afterwards so a `%` in one could never be read as a format
        # directive.
        git -C "$bare" for-each-ref --format='%(refname) %(objectname)' \
            'refs/heads/skills-lock-bump/*' 2>/dev/null \
            | sed "s|^|$(basename "$bare") |"
    done | sort
}

# ── the harness's own regression test, for the helper directly above ──────
#
# `published_bump_branches` is called in a BARE assignment
# (`before=$(published_bump_branches ...)`), so under this file's own
# `set -euo pipefail` a non-zero return from it does not fail a test — it ends
# the RUN, mid-suite, with no Results line and every test after it unexecuted.
# That is the same class of harness-isolation failure the empty `$BUMP_PR_LOG`
# above is pre-created to prevent.
#
# The path that returns non-zero is a directory under the bare dir that is not
# a git repository: `git for-each-ref` exits 128, `pipefail` carries that
# through the inner `| sed`, the `for` compound's status becomes 128, and
# `pipefail` carries it through the outer `| sort`. Its sibling
# `bare_fleet_fingerprint` is immune by accident of shape — its git call sits
# inside `$( ... )` as a printf argument, so the status is absorbed.
#
# ORDER-DEPENDENT, which is why it was latent rather than loud: only the LAST
# iteration's status survives to become the compound's. Measured in a
# standalone `set -euo pipefail` probe over two directories — the non-repo
# sorting FIRST returned `SURVIVED, before=[]` and exit 0, the same non-repo
# sorting LAST killed the probe at exit 128 before its next line ran. Today
# every entry under $TEST_DIR/bare is a bare repo and the last alphabetically
# is `testorg_repo-with-sync`, so nothing triggers it; one fixture directory
# named past that and the suite stops reporting.
#
# Asserted on the helper's RETURN STATUS rather than by planting the directory
# in the shared fleet: the status is the root cause, planting it live would
# make every unrelated fingerprint comparison depend on this fixture, and — if
# the guard were ever removed — a live plant would take the whole suite down
# instead of failing one line.
test_harness_published_bump_branches() {
    echo ""
    echo "=== Test: the harness's own bump-branch listing survives a non-repo ==="

    local scratch="$TEST_DIR/pbb-probe" rc=0 out
    rm -rf "$scratch"
    mkdir -p "$scratch"
    git init --bare --initial-branch=main "$scratch/aaa_repo" >/dev/null 2>&1
    # Sorts after `aaa_repo`, so its status is the one the loop ends on.
    mkdir -p "$scratch/zzz_not_a_repo"

    out=$(published_bump_branches "$scratch") || rc=$?
    if [[ $rc -eq 0 ]]; then
        pass "harness: a non-repo directory sorting last does not abort the run"
    else
        fail "harness: published_bump_branches returned $rc — under set -e that ends the suite, not this test"
    fi
    if [[ -z "$out" ]]; then
        pass "harness: and it reports no bump branches for a fleet that has none"
    else
        fail "harness: it reported bump branches where there are none — [$out]"
    fi

    # The other half of the claim: the guard must not have turned the helper
    # into one that reports nothing at all. A real bump branch still shows up.
    local work="$TEST_DIR/pbb-probe-work"
    rm -rf "$work"
    git init --initial-branch=main "$work" >/dev/null 2>&1
    git -C "$work" config commit.gpgsign false
    echo probe > "$work/f"
    git -C "$work" add f >/dev/null 2>&1
    git -C "$work" -c user.name="A Human" -c user.email="human@example.com" \
        commit -q -m "probe" >/dev/null 2>&1
    git -C "$work" push -q "$scratch/aaa_repo" \
        HEAD:refs/heads/skills-lock-bump/update >/dev/null 2>&1

    rc=0
    out=$(published_bump_branches "$scratch") || rc=$?
    if [[ $rc -eq 0 && "$out" == *"aaa_repo refs/heads/skills-lock-bump/update"* ]]; then
        pass "harness: a real bump branch is still listed, with its repo name"
    else
        fail "harness: the bump branch was not listed (rc=$rc) — [$out]"
    fi

    rm -rf "$scratch" "$work"
}

test_sync_unknown_argument() {
    echo ""
    echo "=== Test: sync.sh (an unrecognised argument) ==="

    local before after out exit_code arg
    before=$(bare_fleet_fingerprint)

    # Three spellings, and the third is the one with teeth: `--dry-run` in
    # second position is not a typo at all, it is the flag the operator meant,
    # in a position the old check could not see.
    for arg in "-n" "--dry-runn" "--verbose --dry-run"; do
        out="$TEST_DIR/sync-badarg.txt"
        exit_code=0
        # shellcheck disable=SC2086  # deliberate word-splitting: $arg is an
        # argument LIST, and the two-argument case is the whole point of it.
        GITHUB_REPOSITORY_OWNER=testorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/sync.sh" $arg > "$out" 2>&1 || exit_code=$?

        if [[ $exit_code -eq 2 ]]; then
            pass "sync bad argument '$arg': refuses the run (exit 2)"
        else
            fail "sync bad argument '$arg': refuses the run (exit 2) — got $exit_code: $(head -3 "$out")"
        fi
        assert_contains "$out" "unknown argument" "sync bad argument '$arg': named rather than ignored"
        # The proof that it stopped BEFORE doing anything, not merely that it
        # complained: discovery is the first thing a real run prints.
        assert_not_contains "$out" "Scanning repos for" \
            "sync bad argument '$arg': never reached discovery"
    done

    after=$(bare_fleet_fingerprint)
    if [[ "$before" == "$after" ]]; then
        pass "sync bad argument: not one ref of one bare repo moved"
    else
        fail "sync bad argument: the fleet was written to — $(diff <(echo "$before") <(echo "$after") | tr '\n' ' ')"
    fi
}

# ── Test 3f2: an absent repos.yml stops both fleet walkers ────────────────
#
# The yq preflight and read_repos_yml between them taught these scripts to keep
# three answers apart once yq is RUNNING — key absent, file unparseable, yq
# missing — and then the file simply not being there walked past all of it:
# `if [[ -f "$REPOS_YML" ]]` skipped every read and left EXCLUDED_REPOS empty
# with nothing in the log. An empty exclusion list is not a smaller run. It is a
# run against the wrong set: the filter matches nothing, and sync.sh — the one
# of the four that writes — clones a repo repos.yml excludes and pushes the
# managed AGENTS.md to its default branch. A renamed file, a mis-set REPOS_YML
# and a checkout that never landed all arrive here.
#
# Bracketed by bare_fleet_fingerprint for the reason written out above it: the
# claim is "wrote nothing", and the only honest way to check that is every ref
# of every bare repo, not just the ones this org would have touched.
#
# drift-report.sh carries the identical guard from the same round and is
# checked in the same test — it publishes rather than writes, so its blast
# radius is a dashboard naming repos repos.yml excludes, which is the same
# contamination one surface over.
test_missing_repos_yml() {
    echo ""
    echo "=== Test: sync.sh + drift-report.sh (repos.yml is not there) ==="

    local before after out exit_code missing="$TEST_DIR/repos-that-is-not-there.yml"
    before=$(bare_fleet_fingerprint)

    # Named rather than deleted: the real fault is a path that points at
    # nothing, and removing the shared fixture would break every test after
    # this one.
    rm -f "$missing"

    out="$TEST_DIR/sync-missing-repos-yml.txt"; exit_code=0
    GITHUB_REPOSITORY_OWNER=testorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$missing" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" > "$out" 2>&1 || exit_code=$?

    if [[ $exit_code -eq 2 ]]; then
        pass "missing repos.yml (sync): refuses the run (exit 2)"
    else
        fail "missing repos.yml (sync): refuses the run (exit 2) — got $exit_code: $(head -3 "$out")"
    fi
    assert_contains "$out" "no repos.yml at $missing" \
        "missing repos.yml (sync): the path it could not find is named"
    # It stopped BEFORE discovery, not merely complained on the way through.
    assert_not_contains "$out" "Scanning repos for" \
        "missing repos.yml (sync): never reached discovery"
    # The specific contamination: the excluded repo must never be opened.
    assert_not_contains "$out" "=== testorg/repo-excluded ===" \
        "missing repos.yml (sync): the repo repos.yml excludes was never touched"

    out="$TEST_DIR/drift-missing-repos-yml.txt"; exit_code=0
    GITHUB_REPOSITORY_OWNER=testorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$missing" \
    DRIFT_REPORT_OUTPUT="$TEST_DIR/drift-missing-repos-yml.md" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/drift-report.sh" > "$out" 2>&1 || exit_code=$?

    if [[ $exit_code -eq 2 ]]; then
        pass "missing repos.yml (drift): refuses the run (exit 2)"
    else
        fail "missing repos.yml (drift): refuses the run (exit 2) — got $exit_code: $(head -3 "$out")"
    fi
    assert_contains "$out" "no repos.yml at $missing" \
        "missing repos.yml (drift): the path it could not find is named"
    assert_not_contains "$out" "Checking testorg/repo-excluded" \
        "missing repos.yml (drift): the excluded repo was never checked"
    if [[ ! -e "$TEST_DIR/drift-missing-repos-yml.md" ]]; then
        pass "missing repos.yml (drift): no report was published"
    else
        fail "missing repos.yml (drift): a report was published anyway"
    fi

    after=$(bare_fleet_fingerprint)
    if [[ "$before" == "$after" ]]; then
        pass "missing repos.yml: not one ref of one bare repo moved"
    else
        fail "missing repos.yml: the fleet was written to — $(diff <(echo "$before") <(echo "$after") | tr '\n' ' ')"
    fi
}

# ── Test 3g: an owner that cannot be listed is that owner's failure ───────
#
# The same shape bump-consumer-locks.sh already has a test for, in the script
# that writes to every repo in the fleet. sync.yml mints one App token per
# owner with continue-on-error and exports no base GH_TOKEN, so a failed mint
# leaves GH_TOKEN unset and `gh repo list` exits non-zero — under `set -e`,
# mid-loop. With SYNC_OWNERS ordered "Adam-S-Daniel jodidaniel", losing the
# FIRST installation ended the whole run: the second owner was never scanned,
# and no "=== Sync complete ===" line was printed, so the log's last word was a
# raw gh error. The workflow's own warning says the opposite — "its repos will
# be skipped this run".
#
# Driven with --dry-run so the assertions are about control flow and this test
# leaves no state behind; the failing branch runs before any dry-run gate.
test_sync_owner_list_failure() {
    echo ""
    echo "=== Test: sync.sh (one owner cannot be listed) ==="

    local out="$TEST_DIR/sync-owner-fail.txt" exit_code=0 before after
    before=$(bare_fleet_fingerprint)

    SYNC_OWNERS="failorg testorg" \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" --dry-run > "$out" 2>&1 || exit_code=$?

    assert_contains "$out" "failorg: could not list repos" \
        "sync owner listing: the owner that failed is named"
    assert_contains "$out" "Scanning repos for: testorg" \
        "sync owner listing: the SECOND owner is still scanned"
    assert_contains "$out" "=== Sync complete" \
        "sync owner listing: the run still prints its summary"
    assert_contains "$out" "1 failed" \
        "sync owner listing: counted as a per-owner failure"
    if [[ $exit_code -ne 0 ]]; then
        pass "sync owner listing: the run exits non-zero"
    else
        fail "sync owner listing: the run exits non-zero (got 0)"
    fi

    after=$(bare_fleet_fingerprint)
    if [[ "$before" == "$after" ]]; then
        pass "sync owner listing: --dry-run still wrote nothing"
    else
        fail "sync owner listing: --dry-run wrote to the fleet"
    fi
}

# ── Test 3h: an owner that holds nothing is not one nameless repo ─────────
#
# `gh repo list --jq '.[].nameWithOwner'` over `[]` prints NOTHING, so
# `repo_list_raw` is the empty string — and `echo "$repo_list_raw"` is one
# blank line, which `grep -v` passes through and `mapfile` turns into a
# one-element array holding "". Both scripts then reported "Found 1 repo(s)"
# and went on to `gh repo clone <owner>/` — a phantom with no name, produced by
# an owner that answered honestly. It is the mirror of the failing owner above
# and must not render as it: "holds nothing" and "could not be asked" are
# different facts, and only one of them is a failure.
test_sync_empty_owner() {
    echo ""
    echo "=== Test: sync.sh (an owner with no repos) ==="

    local out="$TEST_DIR/sync-empty-owner.txt" exit_code=0

    GITHUB_REPOSITORY_OWNER=emptyorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" > "$out" 2>&1 || exit_code=$?

    assert_contains "$out" "No repos found in emptyorg" \
        "sync empty owner: says the owner holds nothing"
    assert_not_contains "$out" "Found 1 repo(s)" \
        "sync empty owner: does not invent a repo out of a blank line"
    # The consequence, not just the count. The per-repo header is
    # `echo "=== $repo_name ==="`, so a phantom prints `===  ===` — an empty
    # name between two spaces, a string nothing else in this log produces.
    #
    # It is spelled this way because the obvious alternative does not work:
    # `MOCK_BARE_DIR/` with an empty repo slug is a real DIRECTORY, so the mock
    # clones it happily and never says "mock repo not found". Measured against
    # the reverted script — it printed `===  ===`, cloned the bare root, and
    # died at exit 128, while an assertion on the mock's error message passed.
    assert_not_contains "$out" "===  ===" \
        "sync empty owner: no per-repo header for a nameless repo"
    if [[ $exit_code -eq 0 ]]; then
        pass "sync empty owner: an empty owner is not a failure"
    else
        fail "sync empty owner: an empty owner is not a failure — got exit $exit_code"
    fi
}

# ── Test 3h2: a repo listing that SUCCEEDS noisily is still an empty one ──
#
# The twin of test_sync_empty_owner one layer down. There the phantom repo came
# from a blank line the script produced itself; here it comes from gh, which
# writes to stderr while exiting 0 in ordinary conditions — deprecation
# notices, auth-expiry warnings — so a `gh repo list ... 2>&1` capture folds
# those lines into the fleet. `sed '/^$/d'` is no defence: the injected line is
# not blank, and every one of the three scripts below then treats it as a
# REPOSITORY NAME.
#
# `emptyorg` is the fixture because it makes the injected line the ONLY entry,
# so a merged capture cannot hide behind real repos: "0 repos" and "1 repo
# called `gh: warning: ...`" are as far apart as two answers get. It also
# writes nothing whichever way the scripts behave, so this test is safe to run
# anywhere in the order.
#
# One knob, three scripts, because all three share the capture verbatim — and
# they are asserted separately because their wrong answers differ in kind:
# sync.sh CLONES the phantom, drift-report.sh PUBLISHES it as a table row with
# a broken github.com link, and bump-consumer-locks.sh asks GitHub about it.
test_repo_list_stderr_notice() {
    echo ""
    echo '=== Test: a `gh repo list` that succeeds noisily is still empty ==='

    local out rpt exit_code before after
    before=$(bare_fleet_fingerprint)

    # ── sync.sh ──────────────────────────────────────────────────────────
    out="$TEST_DIR/repolist-notice-sync.txt"; exit_code=0
    GITHUB_REPOSITORY_OWNER=emptyorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    MOCK_REPO_LIST_STDERR_NOTICE="emptyorg" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" > "$out" 2>&1 || exit_code=$?

    assert_contains "$out" "No repos found in emptyorg" \
        "repo list notice (sync): the owner still holds nothing"
    assert_not_contains "$out" "Found 1 repo(s)" \
        "repo list notice (sync): gh's warning did not become a repo"
    assert_not_contains "$out" "=== gh: warning" \
        "repo list notice (sync): no per-repo header for the warning"
    if [[ $exit_code -eq 0 ]]; then
        pass "repo list notice (sync): a noisy empty owner is not a failure"
    else
        fail "repo list notice (sync): a noisy empty owner is not a failure — got $exit_code"
    fi

    # ── drift-report.sh ──────────────────────────────────────────────────
    out="$TEST_DIR/repolist-notice-drift.txt"
    rpt="$TEST_DIR/repolist-notice-drift.md"
    (
        GITHUB_REPOSITORY_OWNER=emptyorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        MOCK_REPO_LIST_STDERR_NOTICE="emptyorg" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        DRIFT_REPORT_OUTPUT="$rpt" \
        PATH="$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/drift-report.sh" > "$out" 2>&1
    ) || true

    assert_contains "$rpt" "0 repo(s) scanned" \
        "repo list notice (drift): the report still says zero"
    assert_not_contains "$rpt" "1 repo(s) scanned" \
        "repo list notice (drift): the warning was not counted as a repo"
    # The consequence this report can produce and the other two cannot: the
    # phantom is PUBLISHED, under its own name and its own broken link.
    assert_not_contains "$rpt" "gh: warning" \
        "repo list notice (drift): the warning is not published as a table row"

    # ── bump-consumer-locks.sh ───────────────────────────────────────────
    out="$TEST_DIR/repolist-notice-bump.txt"
    BUMP_OWNERS_FOR_RUN="emptyorg" \
    BUMP_REPO_LIST_NOTICE_FOR_RUN="emptyorg" \
        run_bump "$out" --dry-run
    unset BUMP_OWNERS_FOR_RUN BUMP_REPO_LIST_NOTICE_FOR_RUN

    assert_contains "$out" "No repos found in emptyorg" \
        "repo list notice (bumper): the owner still holds nothing"
    assert_not_contains "$out" "Found 1 repo(s)" \
        "repo list notice (bumper): gh's warning did not become a repo"
    assert_not_contains "$out" "=== gh: warning" \
        "repo list notice (bumper): no per-repo header for the warning"

    # A DELIBERATE BROAD SAFETY NET, and a passenger with respect to the defect
    # this test is named for — said out loud rather than left for the next
    # reader to discover, the way the noisy-python leg's is.
    #
    # It cannot discriminate here, and the reason is structural rather than
    # accidental: the scenario is an owner that holds NOTHING, so no per-repo
    # loop body runs whatever the listing said, and the phantom the merged
    # capture invents is the string `gh: warning ...`, which names no bare repo
    # the mock can clone — so every write path against it fails before it
    # writes. Measured 2026-08-29: with sync.sh's and drift-report.sh's repo-list
    # captures reverted to `2>&1`, seven assertions above went red (the sync run
    # reported `Found 1 repo(s)` and opened a `=== gh: warning` section, and the
    # drift report published a row for it) while this fingerprint stayed green.
    #
    # Moving the legs to an owner that DOES hold repos would not repair it — it
    # would break it the other way. sync.sh writes to healthy repos by design,
    # so the fingerprint would then differ on every run, correct or not, and the
    # assertion would have to be deleted rather than strengthened. What it is
    # kept for is the case it CAN see: these three runs each reach real git
    # machinery, and if a later edit gives this test a non-empty owner, or lets
    # a failed listing fall through to a write, this is already standing there.
    after=$(bare_fleet_fingerprint)
    if [[ "$before" == "$after" ]]; then
        pass "repo list notice: none of the three wrote to the fleet"
    else
        fail "repo list notice: the fleet was written to"
    fi
}

# ── Test 3i: a refused `git commit` is a failure, not a benign skip ───────
#
# `git commit ... || { log "Nothing to commit."; ((SKIP_COUNT++)); continue; }`
# reads every non-zero commit as "there was nothing to commit", and the fleet's
# own tooling makes one non-zero commit the common one: cms-platform's
# dev-hooks-sync installs a global core.hooksPath whose secrets-scan pre-commit
# guard FAILS CLOSED when gitleaks is missing from PATH. That is correct for a
# security gate and fatal here, because it refuses EVERY per-repo commit —
# which printed "0 synced, 20 skipped, 0 failed", exited 0, and left the whole
# fleet unsynced behind a green run.
#
# BOTH WRITING SCRIPTS, in one loop, and the loop is the point rather than a
# tidying. This shape was sync.sh's, and bump-consumer-locks.sh carried a copy
# of it — same idiom, same hook, same silence, and one script's regression test
# said nothing whatever about the other. Measured against the bumper with such
# a hook installed: five consumers that needed a re-pin each printed "Nothing to
# commit.", the run printed "0 merged, 0 proposed, 11 skipped, 0 failed" — the
# shape of a healthy night — and exited 0 having written nothing. A per-script
# body here means the next script that grows a commit is one row away from
# being covered, instead of one test away from being forgotten.
#
# The hook is installed via GIT_CONFIG_GLOBAL rather than by touching the
# machine's real gitconfig, and each run gets a throwaway copy of the pristine
# bares so there is genuinely something to commit no matter where in the file
# this test is ordered.
test_commit_refused() {
    echo ""
    echo "=== Test: a pre-commit hook refuses the commit (both writers) ==="

    local hookdir="$TEST_DIR/refusing-hooks"
    mkdir -p "$hookdir"
    cat > "$hookdir/pre-commit" <<'HOOK'
#!/bin/sh
echo "secrets-scan: gitleaks not found on PATH - refusing to commit" >&2
exit 1
HOOK
    chmod +x "$hookdir/pre-commit"

    local gcfg="$TEST_DIR/refusing-gitconfig"
    cat > "$gcfg" <<CFG
[user]
	name = test-runner
	email = test@localhost
[core]
	hooksPath = $hookdir
CFG

    # Per script: a repo it MUST name in a refusal, and whether this lane's
    # exit code is attributable to that refusal at all. sync.sh's whole fleet is
    # refused, so its "0 failed" and its exit code say something about this
    # injection; the bumper's lane permanently carries `repo-error`, a fixture
    # that cannot be assessed by design, so both are non-zero either way and
    # asserting them there would be asserting somebody else's failure — the
    # trap that left test_bump_contents_unreadable with an assertion that could
    # not fail. The NAMED repo is the attributable form, and it is asserted for
    # both.
    local script out exit_code before after gained label refused_repo exit_attributable
    for script in sync.sh bump-consumer-locks.sh; do
        label="commit refused ($script)"
        if [[ "$script" == "sync.sh" ]]; then
            refused_repo="testorg/repo-with-sync"
            exit_attributable=true
        else
            refused_repo="bumporg/repo-stale"
            exit_attributable=false
        fi

        # A fresh throwaway fleet per script, so each one is measured against
        # pristine bares rather than against what the previous script left.
        rm -rf "$TEST_DIR/bare-commit-refused"
        cp -a "$TEST_DIR/bare-pristine" "$TEST_DIR/bare-commit-refused"
        # The widest claim each script can honestly make — see
        # published_bump_branches for why the bumper's is the narrower one.
        if [[ "$script" == "sync.sh" ]]; then
            before=$(bare_fleet_fingerprint "$TEST_DIR/bare-commit-refused")
        else
            before=$(published_bump_branches "$TEST_DIR/bare-commit-refused")
        fi

        out="$TEST_DIR/commit-refused-$script.txt"
        exit_code=0
        if [[ "$script" == "sync.sh" ]]; then
            GITHUB_REPOSITORY_OWNER=testorg \
            MOCK_BARE_DIR="$TEST_DIR/bare-commit-refused" \
            REPOS_YML="$TEST_DIR/repos.yml" \
            GIT_CONFIG_GLOBAL="$gcfg" \
            GIT_CONFIG_NOSYSTEM=1 \
            PATH="$TEST_DIR/bin:$PATH" \
            "$REPO_ROOT/scripts/sync.sh" > "$out" 2>&1 || exit_code=$?
        else
            GITHUB_REPOSITORY_OWNER=bumporg \
            MOCK_BARE_DIR="$TEST_DIR/bare-commit-refused" \
            MOCK_PR_LOG="$TEST_DIR/commit-refused-bump-pr.log" \
            MOCK_PR_BODY_DIR="$BUMP_PR_BODY_DIR" \
            REPOS_YML="$TEST_DIR/repos.yml" \
            BUMP_REGISTRY="bumporg/agentskills" \
            BUMP_CHECKOUTS="$BUMP_CHECKOUTS_ARG" \
            GIT_CONFIG_GLOBAL="$gcfg" \
            GIT_CONFIG_NOSYSTEM=1 \
            PATH="$TEST_DIR/bin:$PATH" \
            "$REPO_ROOT/scripts/bump-consumer-locks.sh" > "$out" 2>&1 || exit_code=$?
        fi

        assert_contains "$out" "commit refused" \
            "$label: the run says the commit was refused"
        # Scoped to the script's own refusal line, so this asserts that the
        # reason was QUOTED there rather than that the words appear in a `2>&1`
        # log at all.
        assert_scoped_line "$out" "commit refused" "secrets-scan" \
            "$label: it quotes the hook's own reason"
        # Attributable: a repo that genuinely needed the write is named on the
        # refusal, so this counts THIS injection rather than the lane's weather.
        assert_scoped_line "$out" "commit refused" "$refused_repo" \
            "$label: the refusal names a repo that needed the write"
        assert_not_contains "$out" "Nothing to commit." \
            "$label: a refusal is NOT reported as an empty diff"
        if $exit_attributable; then
            assert_not_contains "$out" "0 failed" \
                "$label: not counted as a clean run"
            if [[ $exit_code -ne 0 ]]; then
                pass "$label: the run exits non-zero"
            else
                fail "$label: the run exits non-zero (got 0)"
            fi
        fi

        # Nothing may have reached the throwaway fleet either: a refused commit
        # leaves HEAD where it was, and the push that follows would otherwise
        # publish the base commit and look like a successful run.
        if [[ "$script" == "sync.sh" ]]; then
            # Over every ref of every bare, because "wrote nothing" is the claim.
            after=$(bare_fleet_fingerprint "$TEST_DIR/bare-commit-refused")
            label="$label: not one ref of one bare repo moved"
            if [[ "$before" == "$after" ]]; then
                pass "$label"
            else
                fail "$label — $(diff <(echo "$before") <(echo "$after") | tr '\n' ' ')"
            fi
        else
            # GAINED, not changed: the sweep legitimately reaps
            # bumporg/repo-leftover's branch in this same run, so equality here
            # would fail for a correct reason. What the refusal must never do is
            # ADD a branch — that is the base commit reaching a remote.
            after=$(published_bump_branches "$TEST_DIR/bare-commit-refused")
            label="$label: no bump branch was published"
            gained=$(comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after"))
            if [[ -z "$gained" ]]; then
                pass "$label"
            else
                fail "$label — $(echo "$gained" | tr '\n' ' ')"
            fi
        fi
    done

    rm -rf "$TEST_DIR/bare-commit-refused"
}

# ── Test 3j: a foreign commit on the sync branch blocks the force-push ────
#
# The counterpart to test_sync_protected_fallback, which asserts the force-push
# still LANDS over this sync's own stale branch. Here the branch carries a
# commit somebody else wrote, and the whole justification the force-push used
# to carry — "the branch is bot-owned, this sync is its only writer" — is false
# on exactly the repos the fallback runs on: it opens a PR and arms auto-merge,
# so a maintainer can push a conflict resolution onto that branch, and the next
# nightly run overwrote it while logging "branch updated".
test_sync_foreign_branch_commit() {
    echo ""
    echo "=== Test: sync.sh (a foreign commit on agents-md-sync/update) ==="

    local bare="$TEST_DIR/bare/fgnorg_repo-foreign-branch"
    local before after out exit_code=0
    before=$(git -C "$bare" rev-parse "refs/heads/agents-md-sync/update")

    out="$TEST_DIR/sync-foreign.txt"
    GITHUB_REPOSITORY_OWNER=fgnorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    MOCK_PR_LOG="$TEST_DIR/pr-foreign.log" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" > "$out" 2>&1 || exit_code=$?

    assert_contains "$out" "carries a commit this sync did not write" \
        "foreign branch: the refusal says whose branch it is"
    assert_contains "$out" "reviewer@users.noreply.github.com" \
        "foreign branch: it names the committer it found"
    assert_contains "$out" "1 failed" \
        "foreign branch: counted as a repo failure, not a skip"
    if [[ $exit_code -ne 0 ]]; then
        pass "foreign branch: the run exits non-zero"
    else
        fail "foreign branch: the run exits non-zero (got 0)"
    fi

    after=$(git -C "$bare" rev-parse "refs/heads/agents-md-sync/update")
    if [[ "$before" == "$after" ]]; then
        pass "foreign branch: the reviewer's commit is still the branch tip"
    else
        fail "foreign branch: the branch was force-pushed over — $before -> $after"
    fi
    # And the bytes, not only the sha: the assertion above would also hold if
    # the branch had been deleted and recreated at the same object.
    local tip_file
    tip_file=$(git -C "$bare" show "refs/heads/agents-md-sync/update:AGENTS.md" 2>/dev/null || true)
    if [[ "$tip_file" == "a reviewer fixed the conflict by hand" ]]; then
        pass "foreign branch: the reviewer's content survived intact"
    else
        fail "foreign branch: the reviewer's AGENTS.md is gone — branch tip now reads '$tip_file'"
    fi
}

# ── Test 3k: a stale bot branch forked from an ANCESTOR still force-pushes ─
#
# The third leg of the force-push guard, and the only one of the three that can
# tell a working guard from a broken one.
#
# test_sync_protected_fallback asserts the force-push LANDS over the bot's own
# branch; test_sync_foreign_branch_commit asserts it is REFUSED over a
# stranger's. Both fixtures fork agents-md-sync/update off the current tip of
# main, so the range the guard walks is one commit either way and a guard that
# cannot see past a shallow graft answers both of them correctly. This one
# forks off an ancestor, with human commits on main after it — the ordinary
# shape of a branch that has gone stale — where a graft-blind range yields
# main's own mainline commits and the guard refuses the bot's own work.
#
# The failure it guards is not "a wrong log line". A refusal here is counted as
# a repo FAILURE, so the repo never receives another update and the run goes
# red every night — for a branch this sync wrote itself. See setup_ancestor_
# branch_repo for the measured ranges, and the `--unshallow` block in sync.sh
# for why the clone is deepened before the question is asked.
test_sync_ancestor_branch_force_push() {
    echo ""
    echo "=== Test: sync.sh (a stale bot branch forked from an ancestor of main) ==="

    local bare="$TEST_DIR/bare/ancorg_repo-stale-ancestor"
    local out="$TEST_DIR/sync-ancestor.txt" exit_code=0
    local before after

    before=$(git -C "$bare" rev-parse "refs/heads/agents-md-sync/update")

    GITHUB_REPOSITORY_OWNER=ancorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    MOCK_PR_LOG="$TEST_DIR/pr-ancestor.log" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" > "$out" 2>&1 || exit_code=$?

    # It really did take the PR fallback, so the force-push was on the path at
    # all. Without this the assertions below would also hold for a run that
    # pushed straight to main and never reached the guard.
    assert_contains "$out" "falling back to PR" \
        "ancestor branch: the protected default branch routed it to the PR path"

    assert_not_contains "$out" "carries a commit this sync did not write" \
        "ancestor branch: the bot's own stale branch is not read as a stranger's"
    # The specific wrong answer, named: the fork point is a HUMAN commit that is
    # already on main, and a graft-blind range pulls it in and quotes it.
    assert_not_contains "$out" "human@example.com" \
        "ancestor branch: no commit already on main is blamed on the branch"
    assert_contains "$out" "0 failed" \
        "ancestor branch: the repo is not counted as a failure"
    if [[ $exit_code -eq 0 ]]; then
        pass "ancestor branch: the run exits 0"
    else
        fail "ancestor branch: the run exits 0 — got $exit_code: $(cat "$out")"
    fi

    after=$(git -C "$bare" rev-parse "refs/heads/agents-md-sync/update")
    if [[ "$before" != "$after" ]]; then
        pass "ancestor branch: the force-push landed"
    else
        fail "ancestor branch: the branch tip never moved from $before"
    fi
    # And the bytes, not only the sha: what is on the branch now is this run's
    # managed AGENTS.md, not the stale content it replaced.
    local tip_file
    tip_file=$(git -C "$bare" show "refs/heads/agents-md-sync/update:AGENTS.md" 2>/dev/null || true)
    if grep -qF "## Repo-specific additions" <<< "$tip_file" \
       && ! grep -qF "stale old sync content" <<< "$tip_file"; then
        pass "ancestor branch: the branch now carries the freshly built AGENTS.md"
    else
        fail "ancestor branch: the branch tip is not the managed file — $(head -1 <<< "$tip_file")"
    fi
}

# ── Test 3l: a `.agents-sync.yml` sync.sh could not READ, or could not PARSE ─
#
# Two doors onto one wrong answer, and sync.sh is the script where that answer
# is written to somebody else's default branch.
#
# The section list decides what the managed AGENTS.md contains. `gh api ...
# --jq .content 2>/dev/null` on failure, and `< <(... | yq ... 2>/dev/null ||
# true)` on a file that will not parse, both produced ZERO SECTIONS and exit 0
# — indistinguishable from a repo that genuinely ships no `.agents-sync.yml`.
# The run then rebuilds that repo's AGENTS.md from DEFAULT_SECTIONS, sees the
# content change, commits and PUSHES it: the guidance the repo opted into is
# stripped, on a green run that counts no failure.
#
# The control leg is what makes the other two mean anything — the same fixture,
# uninjured, must still read `Sections: python` and sync — and each injured leg
# also asserts that NOTHING reached the remote, because "it said the right
# thing" and "it did the right thing" are different claims.
test_sync_agents_sync_yml_unreadable() {
    echo ""
    echo "=== Test: sync.sh (.agents-sync.yml unreadable, then unparseable) ==="

    local bare="$TEST_DIR/bare/sfailorg_repo-sync-yml"
    local out exit_code before after

    # ── the read that FAILS, for a reason that is not a 404 ──────────────
    before=$(git -C "$bare" show-ref | sort | sha256sum)
    # MOCK_CONTENTS_STDERR_PRENOTICE puts an ordinary gh notice on stderr AHEAD
    # of the status line, which is the only way this leg can say anything about
    # WHICH line the operator is handed. Without it gh's stderr is one line
    # long, every selection rule picks it, and the `HTTP 403` assertion below
    # holds against a script that simply takes the first thing it finds.
    out="$TEST_DIR/sync-syncyml-403.txt"; exit_code=0
    GITHUB_REPOSITORY_OWNER=sfailorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    MOCK_CONTENTS_HTTP_FAIL="sfailorg_repo-sync-yml" \
    MOCK_CONTENTS_STDERR_PRENOTICE="sfailorg_repo-sync-yml" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" > "$out" 2>&1 || exit_code=$?

    assert_contains "$out" "could not read .agents-sync.yml" \
        "sync yml 403: the repo and the file are both named"
    assert_scoped_line "$out" "could not read .agents-sync.yml" "HTTP 403" \
        "sync yml 403: the failure quotes gh's own status line"
    # The behaviour, not the implementation: the operator's line names the
    # status, and the notice that arrived first is not what it names. Spelled
    # against the message rather than against whatever selects it, because that
    # selection is being factored into a shared helper and applied at ten call
    # sites — an assertion shaped like the mechanism would break on the move.
    assert_scoped_line_lacks "$out" "could not read .agents-sync.yml" "token expires" \
        "sync yml 403: the operator gets the status line, not the notice ahead of it"
    assert_contains "$out" "1 failed" \
        "sync yml 403: counted as a repo failure"
    # The wrong answer, named: an unreadable file must not fall through to the
    # default section list.
    assert_not_contains "$out" "Sections: rust" \
        "sync yml 403: it did NOT fall back to default_sections"
    if [[ $exit_code -ne 0 ]]; then
        pass "sync yml 403: the run exits non-zero"
    else
        fail "sync yml 403: the run exits non-zero (got 0)"
    fi
    after=$(git -C "$bare" show-ref | sort | sha256sum)
    if [[ "$before" == "$after" ]]; then
        pass "sync yml 403: nothing was pushed to the repo"
    else
        fail "sync yml 403: the repo was written to"
    fi

    # ── the file that ARRIVES WHOLE and does not parse ───────────────────
    # An unterminated flow sequence: a real editor slip, and one that still
    # LOOKS like it declares sections, which is what makes silently reading it
    # as zero sections so plausible.
    sync_yaml_fixture_write 'sections: [python
'
    before=$(git -C "$bare" show-ref | sort | sha256sum)
    out="$TEST_DIR/sync-syncyml-parse.txt"; exit_code=0
    GITHUB_REPOSITORY_OWNER=sfailorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" > "$out" 2>&1 || exit_code=$?

    assert_contains "$out" "could not read the sections from .agents-sync.yml" \
        "sync yml parse: a file that will not parse is its own named failure"
    assert_contains "$out" "1 failed" \
        "sync yml parse: counted as a repo failure"
    assert_not_contains "$out" "Sections: none" \
        "sync yml parse: an unparseable file is NOT read as zero sections"
    if [[ $exit_code -ne 0 ]]; then
        pass "sync yml parse: the run exits non-zero"
    else
        fail "sync yml parse: the run exits non-zero (got 0)"
    fi
    after=$(git -C "$bare" show-ref | sort | sha256sum)
    if [[ "$before" == "$after" ]]; then
        pass "sync yml parse: nothing was pushed to the repo"
    else
        fail "sync yml parse: the repo was written to"
    fi

    # ── the CONTROL, and it is what stops the two legs above passing over a
    # sync that simply refuses this repo whatever it reads.
    sync_yaml_fixture_write 'sections:
  - python
'
    out="$TEST_DIR/sync-syncyml-control.txt"; exit_code=0
    GITHUB_REPOSITORY_OWNER=sfailorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" > "$out" 2>&1 || exit_code=$?

    assert_contains "$out" "Sections: python" \
        "sync yml control: the same repo, unharmed, reads its own section list"
    assert_contains "$out" "0 failed" \
        "sync yml control: and syncs without a failure"
    if [[ $exit_code -eq 0 ]]; then
        pass "sync yml control: the run exits 0"
    else
        fail "sync yml control: the run exits 0 — got $exit_code"
    fi

    # ── the read that SUCCEEDS and still writes to stderr ────────────────
    #
    # The control above and the 403 leg above that are both about a call whose
    # STATUS answers the question. This one is about a call that answers it
    # correctly and writes a line to stderr on the way — gh's ordinary
    # deprecation and auth-expiry notices — which is the condition under which
    # capturing `2>&1` corrupts the SUCCESS path in order to make the failure
    # path's message convenient. The captured value here is not a diagnostic
    # anybody quotes: it is a base64 payload, decoded and parsed a few lines
    # later, so a notice folded into it makes base64 refuse a repo that is
    # perfectly healthy — a counted failure, a red scheduled run, and a
    # consumer left unsynced by a message that changed nothing.
    #
    # Nothing in this suite could produce that shape until
    # MOCK_CONTENTS_STDERR_NOTICE existed: the only stderr the contents mock
    # wrote came with `exit 1`. Measured — with the knob off and sync.sh's
    # split capture reverted to `2>&1`, the suite stayed at 907 passed / 0
    # failed; with the knob on it moves.
    out="$TEST_DIR/sync-syncyml-notice.txt"; exit_code=0
    GITHUB_REPOSITORY_OWNER=sfailorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    MOCK_CONTENTS_STDERR_NOTICE="sfailorg_repo-sync-yml" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    PATH="$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" > "$out" 2>&1 || exit_code=$?

    assert_contains "$out" "Sections: python" \
        "sync yml notice: a notice on gh's stderr does not reach the payload"
    assert_not_contains "$out" "could not read the sections from .agents-sync.yml" \
        "sync yml notice: the healthy repo is not reported as unreadable"
    assert_contains "$out" "0 failed" \
        "sync yml notice: a notice that changed nothing counts no failure"
    if [[ $exit_code -eq 0 ]]; then
        pass "sync yml notice: the run exits 0"
    else
        fail "sync yml notice: the run exits 0 — got $exit_code"
    fi

    # ── the same question, asked of yq instead of gh ─────────────────────
    #
    # The section list is read by a PIPELINE — `base64 -d | yq` — and sync.sh
    # captures both stages' stderr through one brace group. yq is the noisier
    # of the two: the build this repo pins answers `-j` correctly and still
    # prints a deprecation line (see setup_noisy_yq_dir). Folded in, that line
    # becomes a SECTION NAME: the log says so, and `build-agents-md.sh` accepts
    # it, emits a header naming it and an "unknown section" warning, and sync.sh
    # is the one script of the four that COMMITS what it built.
    #
    # The noisy yq is on PATH for the whole run, so sync.sh's SEVEN repos.yml
    # reads pass through it too, before the per-repo loop starts. That is a
    # second defect in the same shape and it gets its own assertions below,
    # because exercising a path is not asserting anything about it: with this
    # leg's assertions as they stood, reverting read_repos_yml here to the old
    # `2>&1` left the whole suite green while this very run logged "WARN: could
    # not fetch Flag --tojson has been deprecated, please use -o=json instead /
    # bootorg/agentskills/Flag ... — skills-bootstrap not delivered this run."
    # (measured 2026-08-29). sync.sh's copy is the ONLY one a sync run loads;
    # test_repos_yml_scalars_under_noisy_yq covers the other three.
    setup_noisy_yq_dir
    out="$TEST_DIR/sync-syncyml-noisy-yq.txt"; exit_code=0
    GITHUB_REPOSITORY_OWNER=sfailorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    PATH="$TEST_DIR/bin-yq-noisy:$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/sync.sh" > "$out" 2>&1 || exit_code=$?

    assert_contains "$out" "Sections: python" \
        "sync yml noisy yq: a deprecation line does not become a section name"
    assert_not_contains "$out" "Sections: Flag" \
        "sync yml noisy yq: the section list starts at the first real section"
    assert_contains "$out" "0 failed" \
        "sync yml noisy yq: a noisy but correct yq counts no failure"
    # read_repos_yml's own separation, in sync.sh's copy. The registry and the
    # ref are read as SCALARS, so a folded-in warning does not decorate them —
    # it becomes their first line, and the fetch is then made against a
    # repository whose name starts with a deprecation notice.
    assert_contains "$out" "pinned hook fetched from bootorg/agentskills@3333333" \
        "sync yml noisy yq: the skills_bootstrap registry and ref are the bare pins"
    assert_not_contains "$out" "skills-bootstrap not delivered" \
        "sync yml noisy yq: a noisy repos.yml read does not withhold the hook"
    assert_not_contains "$out" "Flag --tojson" \
        "sync yml noisy yq: nothing yq wrote to stderr reached a parsed value"
    if [[ $exit_code -eq 0 ]]; then
        pass "sync yml noisy yq: the run exits 0"
    else
        fail "sync yml noisy yq: the run exits 0 — got $exit_code"
    fi
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
    # `.claude/` DOES exist here now — fleet-memory is delivered to every
    # synced repo, unconditionally, because every synced repo's AGENTS.md is
    # simultaneously reduced to the stub that depends on it. What must still be
    # withheld is the BOOTSTRAP hook specifically, so the assertion names that
    # file instead of the directory it happens to share.
    if [[ -e "$nolock/$HOOK_REL_PATH_T" ]]; then
        fail "repo-no-lock: skills-bootstrap withheld"
    else
        pass "repo-no-lock: skills-bootstrap withheld"
    fi
    if [[ -f "$nolock/$FLEET_HOOK_REL_PATH_T" && -s "$nolock/$FLEET_PAYLOAD_REL_PATH_T" ]]; then
        pass "repo-no-lock: fleet-memory still delivered (it is not allowlisted)"
    else
        fail "repo-no-lock: fleet-memory still delivered (it is not allowlisted)"
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
    if [[ -e "$notallowed/$HOOK_REL_PATH_T" ]]; then
        fail "repo-not-allowed: a lock alone does NOT trigger bootstrap delivery"
    else
        pass "repo-not-allowed: a lock alone does NOT trigger bootstrap delivery"
    fi
    if [[ -f "$notallowed/$FLEET_HOOK_REL_PATH_T" ]]; then
        pass "repo-not-allowed: fleet-memory delivered regardless of the allowlist"
    else
        fail "repo-not-allowed: fleet-memory delivered regardless of the allowlist"
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
    # Three groups: the repo's own, skills-bootstrap, and fleet-memory. The
    # point of the assertion is unchanged — a second run must not append a
    # duplicate of either hook — only the expected count moved.
    if [[ "$groups" -eq 3 ]]; then
        pass "re-run: still exactly 3 SessionStart groups (registration not duplicated)"
    else
        fail "re-run: still exactly 3 SessionStart groups — got $groups"
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

# ── Test 6b: check-registry.js ────────────────────────────────────────────
#
# The sibling of test 6, over the OTHER two-key scope block. Same shape and
# same reason: every negative case below is paired with a positive one, because
# a gate that always refused would satisfy each negative assertion on its own
# and prove nothing.
#
# The case that carries the most weight is the vacuous-pass guard. With a
# populated allowlist and no complement key, every finding this script can make
# is a RELATION between the two keys or a defect in an out-of-scope RECORD --
# so with no second key there is nothing to contradict and nothing to be
# malformed, and the check prints a clean line forever. That is not a passing
# gate, it is an unwired one, and from the outside they are identical.
test_check_registry() {
    echo ""
    echo "=== Test: check-registry.js (the allowlist must classify, not just list) ==="

    local script="$REPO_ROOT/scripts/check-registry.js"
    local dir="$TEST_DIR/registry"
    mkdir -p "$dir"

    # Same non-silent-skip rule as the cron gate: a check that quietly does not
    # run is the failure this suite exists to catch.
    if [[ ! -d "$REPO_ROOT/node_modules/yaml" ]]; then
        fail "registry: node_modules/yaml is missing — run \`npm ci\` first"
        return
    fi

    # <name> <expected-exit> <needle> <label> -- fixture body on stdin.
    assert_reg() {
        local name="$1" want="$2" needle="$3" label="$4" out rc=0
        cat > "$dir/$name.yml"
        out=$(node "$script" --repos-yml "$dir/$name.yml" 2>&1) || rc=$?
        if [[ "$rc" == "$want" ]] && grep -qF -- "$needle" <<<"$out"; then
            pass "$label"
        else
            fail "$label — expected exit $want containing '$needle'; got exit $rc: $(head -2 <<<"$out" | tr '\n' ' ')"
        fi
    }

    # ── The positive. Everything below is only meaningful against this.
    assert_reg valid 0 "all well-formed, all reasoned" \
        "registry: a partition of two well-formed keys passes" <<'YML'
skills_bootstrap:
  repos:
    - alpha
    - beta
  out_of_scope:
    - repo: gamma
      reason: dormant since 2026-03
YML

    # ── REFUSALS (exit 2): the question could not be asked.
    assert_reg noblock 2 "has no skills_bootstrap: block" \
        "registry: a file with no skills_bootstrap block is a refusal, not a pass" <<'YML'
exclude: []
YML

    assert_reg nocomplement 2 "refusing to certify it" \
        "registry: an allowlist with no complement key REFUSES rather than reporting clean" <<'YML'
skills_bootstrap:
  repos:
    - alpha
YML

    assert_reg emptycomplement 2 "refusing to certify it" \
        "registry: an empty complement key is the same refusal as a missing one" <<'YML'
skills_bootstrap:
  repos:
    - alpha
  out_of_scope: []
YML

    assert_reg bothempty 2 "names no repos under either key" \
        "registry: two empty keys is zero things examined, not a clean bill" <<'YML'
skills_bootstrap:
  repos: []
  out_of_scope: []
YML

    assert_reg notalist 2 "is not a list" \
        "registry: a key written against a different schema is a refusal, not a finding" <<'YML'
skills_bootstrap:
  repos: alpha
  out_of_scope:
    - repo: gamma
      reason: dormant
YML

    printf 'skills_bootstrap:\n  repos:\n   - [unclosed\n' > "$dir/unparseable.yml"
    local out rc=0
    out=$(node "$script" --repos-yml "$dir/unparseable.yml" 2>&1) || rc=$?
    if [[ "$rc" == 2 ]] && grep -qF -- "cannot read the registry" <<<"$out"; then
        pass "registry: unparseable YAML is a refusal with a sentence, not a stack trace"
    else
        fail "registry: unparseable YAML is a refusal with a sentence, not a stack trace — got exit $rc: $(head -2 <<<"$out" | tr '\n' ' ')"
    fi
    if ! grep -qF -- "at Object.<anonymous>" <<<"$out"; then
        pass "registry: and the refusal is not a stack trace dressed as a finding"
    else
        fail "registry: and the refusal is not a stack trace dressed as a finding"
    fi

    # ── FINDINGS (exit 1): read fine, breaks a rule.
    assert_reg bothkeys 1 "listed under BOTH" \
        "registry: a name under both keys is a contradiction, not a default" <<'YML'
skills_bootstrap:
  repos:
    - alpha
  out_of_scope:
    - repo: alpha
      reason: dormant
YML

    assert_reg dupdeliver 1 "listed more than once under skills_bootstrap.repos" \
        "registry: a duplicate in the allowlist is a finding" <<'YML'
skills_bootstrap:
  repos:
    - alpha
    - alpha
  out_of_scope:
    - repo: gamma
      reason: dormant
YML

    assert_reg dupexclude 1 "listed more than once under skills_bootstrap.out_of_scope" \
        "registry: two reasons for one absence is a finding — nothing says which holds" <<'YML'
skills_bootstrap:
  repos:
    - alpha
  out_of_scope:
    - repo: gamma
      reason: dormant
    - repo: gamma
      reason: contaminates the instrument
YML

    assert_reg noreason 1 "has no reason:" \
        "registry: an exclusion with no reason records a decision nobody can review" <<'YML'
skills_bootstrap:
  repos:
    - alpha
  out_of_scope:
    - repo: gamma
      reason: "   "
YML

    assert_reg barestring 1 "not a mapping with repo: and reason:" \
        "registry: a bare string cannot carry a reason, which is the whole point of the key" <<'YML'
skills_bootstrap:
  repos:
    - alpha
  out_of_scope:
    - gamma
YML

    # BOTH separators, not just the POSIX one. These names are joined onto a
    # checkout root by the tools that consume them, so a name carrying a
    # separator addresses a different directory instead of failing.
    assert_reg slash 1 "contains a path separator" \
        "registry: an owner/repo in the allowlist is rejected — these are SHORT names" <<'YML'
skills_bootstrap:
  repos:
    - Adam-S-Daniel/alpha
  out_of_scope:
    - repo: gamma
      reason: dormant
YML

    assert_reg backslash 1 "contains a path separator" \
        "registry: and a backslash is rejected too, not only the POSIX one" <<'YML'
skills_bootstrap:
  repos:
    - alpha
  out_of_scope:
    - repo: "sub\\gamma"
      reason: dormant
YML

    # ── The one that is about THIS repo rather than about the script, and the
    # reason the gate is in CI at all.
    rc=0
    out=$(node "$script" 2>&1) || rc=$?
    if [[ "$rc" == 0 ]]; then
        pass "registry: this repo's own repos.yml declares a usable partition"
    else
        fail "registry: this repo's own repos.yml declares a usable partition — exit $rc: $(head -3 <<<"$out" | tr '\n' ' ')"
    fi

    # ── And that CI actually runs it. A gate nothing invokes is a file.
    # PARSED, never grepped: a line scan matches its needle in a comment just
    # as happily, and this whole file is comments.
    local wired
    wired=$(node -e '
      const YAML = require("yaml"), fs = require("fs");
      const d = YAML.parse(fs.readFileSync(process.argv[1], "utf8"));
      const runs = [];
      for (const job of Object.values(d.jobs || {}))
        for (const step of job.steps || []) if (step.run) runs.push(step.run);
      process.stdout.write(runs.some(r => /check-registry\.js/.test(r)) ? "yes" : "no");
    ' "$REPO_ROOT/.github/workflows/ci.yml" 2>/dev/null)
    if [[ "$wired" == "yes" ]]; then
        pass "registry: ci.yml actually runs the gate"
    else
        fail "registry: ci.yml actually runs the gate — no run: step invokes check-registry.js"
    fi

    unset -f assert_reg
}

# ── Test 6c: capture-routine.py ───────────────────────────────
#
# The snapshot in docs/routines/ is the only prior copy of a Routine that is
# edited on claude.ai, so the ways this script can be wrong are the ways that
# copy silently stops meaning anything: a field the policy has never seen and
# quietly drops, an identifier reaching a public repo, runtime state making the
# file permanently stale, or a render that is not reproducible so --check can
# never be trusted. Each has a test below, and each refusal is checked against
# the positive above it.

test_capture_routine() {
    echo ""
    echo "=== Test: capture-routine.py (the Routine snapshot must refuse, not guess) ==="

    local script="$REPO_ROOT/scripts/capture-routine.py"
    local dir="$TEST_DIR/routine"
    mkdir -p "$dir"

    if ! command -v python3 >/dev/null 2>&1; then
        fail "routine: python3 is missing — the capture script cannot be exercised"
        return
    fi

    # One synthetic routine record, matching the shape `list_triggers` returns.
    # Deliberately fake throughout: the values below are what the redaction
    # assertions grep for, so a real id here would be both a leak and a test
    # that proves nothing.
    write_fixture() {   # <name> [python mutation over `r`]
        local name="$1" mutation="${2:-pass}"
        python3 - "$dir/$name.json" "$mutation" <<'PY'
import json, sys
out, mutation = sys.argv[1], sys.argv[2]
prompt = "Read the spec.\n\n```bash\necho hi\n```\n"
r = {
    "id": "trig_FIXTUREfixtureFIXTUREfixture",
    "name": "Fixture routine",
    "enabled": True,
    "cron_expression": "0 7 * * 0",
    "created_at": "2026-01-01T00:00:00Z",
    "created_kind": "ROUTINE_CREATED_KIND_ROUTINE",
    "created_via": "meta_mcp",
    "creator": {"account_uuid": "ACCOUNTUUIDfixture0000"},
    "derived_state": {"folders_state": "FOLDERS_STATE_NONE", "model": "", "prompt": prompt},
    "next_run_at": "NEXTRUNfixture0000",
    "last_fired_at": "2026-01-02T00:00:00Z",
    "last_run": {"status": "ROUTINE_RUN_STATUS_SUCCEEDED", "fired_at": "2026-01-02T00:00:00Z",
                 "finished_at": "2026-01-02T00:01:00Z", "session_id": "SESSIONfixture0000"},
    "updated_at": "2026-01-03T00:00:00Z",
    "notifications": {"channel": {"push": True, "email": False, "slack": False}},
    "mcp_connections": [{"name": "fixture-mcp", "url": "https://mcp.example.com/",
                         "connector_uuid": "CONNECTORUUIDfixture0000"}],
    "job_config": {"ccr": {
        "environment_id": "ENVIRONMENTfixture0000",
        "events": [{"data": {"type": "user", "uuid": "MESSAGEUUIDfixture0000",
                             "session_id": "", "parent_tool_use_id": None,
                             "message": {"role": "user", "content": prompt}}}],
        "session_context": {
            "allowed_tools": ["preset:default", "Bash"],
            "autofix_on_pr_create": True,
            "sources": [{"git_repository": {"url": "https://github.com/testorg/repo-one"}},
                        {"git_repository": {"url": "https://github.com/testorg/repo-two"}}],
            "outcomes": [{"git_repository": {"git_info": {"repo": "testorg/repo-one",
                                                          "branches": ["claude/fixture"]}}}],
        }}},
}
exec(mutation)
json.dump({"data": [r]}, open(out, "w"))
PY
    }

    # <fixture> <expected-exit> <needle> <label>
    assert_cap() {
        local fixture="$1" want="$2" needle="$3" label="$4" out rc=0
        out=$(python3 "$script" --id trig_FIXTUREfixtureFIXTUREfixture \
                  --input "$dir/$fixture.json" --out "$dir/$fixture.md" 2>&1) || rc=$?
        if [[ "$rc" == "$want" ]] && grep -qF -- "$needle" <<<"$out"; then
            pass "$label"
        else
            fail "$label — expected exit $want containing '$needle'; got exit $rc: $(head -2 <<<"$out" | tr '\n' ' ')"
        fi
    }

    # ── The positive. Every refusal below is only meaningful against this.
    write_fixture good
    assert_cap good 0 "wrote" "routine: a well-formed record renders a snapshot"

    # ── REFUSALS (exit 2): the question could not be answered honestly.
    write_fixture unknown 'r["brand_new_field"] = "surprise"'
    assert_cap unknown 2 "brand_new_field" \
        "routine: a field the policy has never classified REFUSES and names it"

    write_fixture skew 'r["derived_state"]["prompt"] += "drifted"'
    assert_cap skew 2 "disagree" \
        "routine: derived_state.prompt disagreeing with the seed event is a refusal"

    write_fixture twoev 'r["job_config"]["ccr"]["events"].append(r["job_config"]["ccr"]["events"][0])'
    assert_cap twoev 2 "exactly one seed event" \
        "routine: two seed events make the prompt ambiguous, so it refuses"

    write_fixture noprompt 'r["job_config"]["ccr"]["events"][0]["data"]["message"]["content"] = ""'
    assert_cap noprompt 2 "no prompt text" \
        "routine: an empty prompt is a refusal, not an empty snapshot"

    printf '{"data":[]}' > "$dir/empty.json"
    assert_cap empty 2 "zero routines" \
        "routine: zero routines refuses — an empty list and an unauthorised one look alike"

    printf 'not json' > "$dir/notjson.json"
    assert_cap notjson 2 "not JSON" \
        "routine: a non-JSON payload is a refusal"

    local out rc=0
    out=$(python3 "$script" --id trig_ABSENT --input "$dir/good.json" \
              --out "$dir/absent.md" 2>&1) || rc=$?
    if [[ $rc -eq 2 ]] && grep -qF "will not guess" <<<"$out"; then
        pass "routine: an id that is not in the response refuses rather than picking one"
    else
        fail "routine: absent id should exit 2; got $rc"
    fi

    # ── Redaction. This repo is public, so identifiers must not reach the file.
    local snap="$dir/good.md" leaked=0 lit
    for lit in ACCOUNTUUIDfixture0000 ENVIRONMENTfixture0000 CONNECTORUUIDfixture0000 \
               SESSIONfixture0000 MESSAGEUUIDfixture0000; do
        if grep -qF -- "$lit" "$snap"; then
            fail "routine: $lit leaked into the snapshot"
            leaked=1
        fi
    done
    [[ $leaked -eq 0 ]] && pass "routine: every redacted identifier is absent from the snapshot"

    # The control that keeps the five greps above from being wired to nothing:
    # a literal that IS expected must be found by the same method.
    if grep -qF -- "trig_FIXTUREfixtureFIXTUREfixture" "$snap"; then
        pass "routine: control — the leak check finds a literal that is present"
    else
        fail "routine: control failed — the leak greps cannot see the file at all"
    fi

    # ── Volatility. Committing runtime state guarantees a permanently stale file.
    if grep -qF -- "NEXTRUNfixture0000" "$snap"; then
        fail "routine: next_run_at was captured — the snapshot goes stale on every fire"
    else
        pass "routine: runtime state is named in the exclusions but its value is not captured"
    fi
    assert_contains "$snap" 'next_run_at' "routine: the excluded field is still listed by name"

    # ── Determinism. --check is worthless if two renders of one input differ.
    python3 "$script" --id trig_FIXTUREfixtureFIXTUREfixture --input "$dir/good.json" \
        --out "$dir/again.md" >/dev/null 2>&1
    if cmp -s "$snap" "$dir/again.md"; then
        pass "routine: two renders of one payload are byte-identical"
    else
        fail "routine: rendering is not deterministic"
    fi

    rc=0
    python3 "$script" --id trig_FIXTUREfixtureFIXTUREfixture --input "$dir/good.json" \
        --out "$snap" --check >/dev/null 2>&1 || rc=$?
    [[ $rc -eq 0 ]] && pass "routine: --check passes against a current snapshot" \
        || fail "routine: --check should pass against its own output; got $rc"

    printf 'drift\n' >> "$snap"
    rc=0
    python3 "$script" --id trig_FIXTUREfixtureFIXTUREfixture --input "$dir/good.json" \
        --out "$snap" --check >/dev/null 2>&1 || rc=$?
    [[ $rc -eq 1 ]] && pass "routine: --check reports exit 1 on a stale snapshot" \
        || fail "routine: --check should exit 1 on drift; got $rc"

    # ── The wrapping fence must outgrow anything the prompt puts inside it.
    write_fixture fence 'p = r["derived_state"]["prompt"] + "\n~~~~\ninner\n~~~~\n"; r["derived_state"]["prompt"] = p; r["job_config"]["ccr"]["events"][0]["data"]["message"]["content"] = p'
    assert_cap fence 0 "wrote" "routine: a prompt containing ~~~~ still renders"
    if grep -q '^~~~~~text$' "$dir/fence.md"; then
        pass "routine: the fence lengthens past the longest run inside the prompt"
    else
        fail "routine: the fence did not grow — the prompt would close it early"
    fi
    if grep -q '^~~~text$' "$dir/good.md"; then
        pass "routine: control — a prompt with no tildes still uses the short fence"
    else
        fail "routine: control failed — the short fence is not what a plain prompt gets"
    fi

    # ── --runtime: the half the snapshot excludes, answered rather than hidden.
    # Every assertion below is clock-independent by construction: 2000 is stale
    # under any clock this runs on, 2099 is not, and neither depends on today.
    rc=0
    out=$(python3 "$script" --id trig_FIXTUREfixtureFIXTUREfixture \
              --input "$dir/good.json" --runtime 2>&1) || rc=$?
    if [[ $rc -eq 0 ]] && grep -q 'next run:' <<<"$out" && grep -q 'last fired:' <<<"$out"; then
        pass "routine: --runtime prints the volatile fields the snapshot omits"
    else
        fail "routine: --runtime should print next run and last fired; got exit $rc"
    fi
    if grep -q 'NEXTRUNfixture0000' <<<"$out"; then
        pass "routine: control — --runtime really does surface the excluded value"
    else
        fail "routine: control failed — --runtime printed no next_run_at at all"
    fi
    # --out is passed HERE deliberately. Without it this assertion is vacuous:
    # a path the script was never given cannot appear whatever the code does.
    # Naming it makes the check real — if --runtime ever fell through to the
    # renderer, this file would exist.
    python3 "$script" --id trig_FIXTUREfixtureFIXTUREfixture \
        --input "$dir/good.json" --out "$dir/runtime-must-not-write.md" \
        --runtime >/dev/null 2>&1
    if [[ -e "$dir/runtime-must-not-write.md" ]]; then
        fail "routine: --runtime wrote a snapshot even though it was told not to"
    else
        pass "routine: --runtime writes nothing, even when handed an --out path"
    fi

    write_fixture stale 'r["last_fired_at"] = "2000-01-01T00:00:00Z"'
    out=$(python3 "$script" --id trig_FIXTUREfixtureFIXTUREfixture \
              --input "$dir/stale.json" --runtime 2>&1)
    grep -q 'VERDICT: STOPPED' <<<"$out" \
        && pass "routine: a last fire past the two-week threshold reads STOPPED" \
        || fail "routine: an ancient last_fired_at should read STOPPED; got: $(tail -1 <<<"$out")"

    write_fixture future 'r["last_fired_at"] = "2099-01-01T00:00:00Z"'
    out=$(python3 "$script" --id trig_FIXTUREfixtureFIXTUREfixture \
              --input "$dir/future.json" --runtime 2>&1)
    grep -q 'VERDICT: last fired in the future' <<<"$out" \
        && pass "routine: a last fire in the future is called out, not read as healthy" \
        || fail "routine: a future last_fired_at should be flagged; got: $(tail -1 <<<"$out")"

    write_fixture neverfired 'del r["last_fired_at"]; del r["last_run"]'
    out=$(python3 "$script" --id trig_FIXTUREfixtureFIXTUREfixture \
              --input "$dir/neverfired.json" --runtime 2>&1)
    grep -q 'VERDICT: never fired' <<<"$out" \
        && pass "routine: a Routine that has never fired says so rather than looking healthy" \
        || fail "routine: a never-fired routine should say so; got: $(tail -1 <<<"$out")"

    rc=0
    python3 "$script" --id trig_FIXTUREfixtureFIXTUREfixture \
        --input "$dir/good.json" >/dev/null 2>&1 || rc=$?
    [[ $rc -eq 2 ]] && pass "routine: neither --out nor --runtime is an error, not a silent no-op" \
        || fail "routine: omitting both --out and --runtime should fail; got $rc"
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
    MOCK_MERGE_DELETES_BRANCH="${BUMP_MERGE_REAPS_BRANCH_FOR_RUN:-}" \
    MOCK_DELETE_REF_HTTP_FAIL="${BUMP_DELETE_REF_FAIL_FOR_RUN:-}" \
    MOCK_MATCHING_REFS_HTTP_FAIL="${BUMP_MATCHING_REFS_FAIL_FOR_RUN:-}" \
    MOCK_AUTO_MERGE_FAILS="${BUMP_AUTO_MERGE_FAILS_FOR_RUN:-}" \
    MOCK_PR_HEAD_MOVES="${BUMP_HEAD_MOVES_FOR_RUN:-}" \
    MOCK_PR_HEAD_GARBLED="${BUMP_HEAD_GARBLED_FOR_RUN:-}" \
    MOCK_PR_VIEW_FAILS="${BUMP_VIEW_FAILS_FOR_RUN:-}" \
    MOCK_CONTENTS_HTTP_FAIL="${BUMP_CONTENTS_FAIL_FOR_RUN:-}" \
    MOCK_CONTENTS_STDERR_NOTICE="${BUMP_CONTENTS_NOTICE_FOR_RUN:-}" \
    MOCK_CONTENTS_STDERR_PRENOTICE="${BUMP_CONTENTS_PRENOTICE_FOR_RUN:-}" \
    MOCK_CONTENTS_404_STDERR_ONLY="${BUMP_CONTENTS_404_STDERR_FOR_RUN:-}" \
    MOCK_REPO_LIST_STDERR_NOTICE="${BUMP_REPO_LIST_NOTICE_FOR_RUN:-}" \
    MOCK_PR_LIST_STDERR_NOTICE="${BUMP_PR_LIST_NOTICE_FOR_RUN:-}" \
    MOCK_GH_NO_MATCH_FLAG="${BUMP_NO_MATCH_FLAG_FOR_RUN:-}" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    BUMP_REGISTRY="bumporg/agentskills" \
    BUMP_CHECKOUTS="${BUMP_CHECKOUTS_FOR_RUN:-$BUMP_CHECKOUTS_ARG}" \
    BUMP_GENERATOR="${BUMP_GENERATOR_FOR_RUN:-}" \
    PATH="${BUMP_PATH_PREFIX_FOR_RUN:+$BUMP_PATH_PREFIX_FOR_RUN:}$TEST_DIR/bin:$PATH" \
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

lock_source_ref_of() {   # <file> <source registry> — that source's pinned ref
    python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    doc = json.load(handle)
for source in doc.get("sources") or []:
    if source.get("registry") == sys.argv[2]:
        print(source.get("ref", ""))
        break
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
             repo-fed-current repo-fed-stale repo-inverted; do
        assert_no_bump_branch "$r" "dry-run: nothing pushed to bumporg/$r"
    done
    if [[ ! -s "$BUMP_PR_LOG" ]]; then
        pass "dry-run: no pull request opened"
    else
        fail "dry-run: no pull request opened — $(cat "$BUMP_PR_LOG")"
    fi

    # ── the same run, under a python3 that writes to stderr ──────────────
    #
    # gh is not this script's only noisy producer: the lock-plan reader is a
    # local `python3 -c`, and python writes to stderr while exiting 0 whenever
    # the inherited environment asks it to (see setup_noisy_python_dir).
    #
    # WHAT THIS DOES AND DOES NOT PROVE, because the difference matters. It
    # pins the ANCHORING — the `^registry `/`^ref `/`^source ` prefixes the
    # three `sed -n` extractions match — and nothing more. It does NOT
    # discriminate `lock_plan`'s split capture from the merged one: measured,
    # reverting that capture to `2>&1` left every line below passing, because
    # with the anchors in place one unanchored extra line changes no parsed
    # value. The separation there is defence in depth, and this leg is a
    # passenger with respect to it, which is said out loud rather than left for
    # the next reader to discover. The capture where the same noise DOES change
    # the answer is the shrink check, and it has its own test.
    setup_noisy_python_dir
    BUMP_PATH_PREFIX_FOR_RUN="$TEST_DIR/bin-py-noisy" \
        run_bump "$TEST_DIR/bump-dry-noisy-py.txt" --dry-run
    unset BUMP_PATH_PREFIX_FOR_RUN
    local npy="$TEST_DIR/bump-dry-noisy-py.txt"

    assert_contains "$npy" "[DRY RUN] Would re-pin skills.lock" \
        "noisy python: the re-pin the quiet run would make is still made"
    assert_not_contains "$npy" "skills.lock is unusable" \
        "noisy python: a diagnostic on stderr is not read as a broken lock"
    assert_not_contains "$npy" "DeprecationWarning" \
        "noisy python: the diagnostic reaches no operator-facing line"
}

# ── Test 8a1b: the shrink check under a python3 that writes to stderr ─────
#
# The one capture in this script whose value is TESTED FOR EMPTINESS, which is
# what makes a noisy success change the answer here and nowhere else in the
# same family. `skills_shrink_reason` prints a reason when the re-pinned lock
# would delete skills and prints NOTHING when it would not, so empty is the
# permission to proceed. Merge its streams and any line the producer writes on
# a run that exited 0 becomes a reason: the script refuses a healthy re-pin,
# counts the repo FAILED, and prints that line as the cause. Measured in a
# standalone `set -euo pipefail` probe of the exact shape — separated,
# "re-pin proceeds"; merged, "REFUSED re-pin: sys:1: DeprecationWarning: ...".
#
# A REAL propose run, not a dry one, because the shrink check sits after the
# dry-run gate and a dry run never reaches it — which is also why
# test_bump_dry_run's noisy-python leg above must not be read as covering it.
# Its own MOCK_BARE_DIR, on the model of test_bump_push_rejected, so a run that
# does push leaves the shared fleet untouched.
test_bump_shrink_check_noisy_python() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (the shrink check under a noisy python3) ==="

    local bare="$TEST_DIR/bare-noisypy/bumporg_repo-noisypy"
    local work="$TEST_DIR/work/bumporg-repo-noisypy"
    rm -rf "$TEST_DIR/bare-noisypy" "$work"
    mkdir -p "$bare" "$work"
    git init --bare --initial-branch=main "$bare" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$bare"
    echo "# repo-noisypy" > README.md
    # A lock pinned at the OLD ref: genuinely stale, so a re-pin is due, and
    # its skill set is unchanged by that re-pin, so the honest shrink answer is
    # the empty one.
    seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_OLD"
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"

    setup_noisy_python_dir
    BUMP_BARE_DIR_FOR_RUN="$TEST_DIR/bare-noisypy" \
    BUMP_PATH_PREFIX_FOR_RUN="$TEST_DIR/bin-py-noisy" \
        run_bump "$TEST_DIR/bump-noisypy.txt"
    unset BUMP_BARE_DIR_FOR_RUN BUMP_PATH_PREFIX_FOR_RUN
    local out="$TEST_DIR/bump-noisypy.txt"

    assert_not_contains "$out" "refusing to propose this re-pin" \
        "noisy python shrink: a diagnostic on stderr is not read as a vanished bundle"
    assert_contains "$out" "1 proposed" \
        "noisy python shrink: the re-pin this consumer is due is actually proposed"
    assert_contains "$out" "0 failed" \
        "noisy python shrink: a noisy but correct producer counts no failure"
    if [[ -n "$(git -C "$bare" rev-parse --verify -q refs/heads/skills-lock-bump/update 2>/dev/null || true)" ]]; then
        pass "noisy python shrink: the bump branch was pushed"
    else
        fail "noisy python shrink: no bump branch was pushed — $(cat "$out")"
    fi

    rm -rf "$TEST_DIR/bare-noisypy" "$work"
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

    # ── ONE SCOPED QUESTION PER HALF, and the answers drive different pins.
    # repo-fed-current is the mirror of the negative control below: its PRIMARY
    # is content-current and its SOURCE has moved, so the re-pin must hold the
    # primary's pin and advance the source's. Gate on a COMBINED verdict and
    # this pair is indistinguishable from the negative control's, which is the
    # whole reason the question is asked per source.
    assert_contains "$log" "bundle content unchanged since ${BUMP_REF_CONTENT:0:7}" "federated-current: the primary is judged on its own content"
    assert_contains "$log" "a federated source has moved — re-pin needed" "federated-current: the moved federated half is what forces the re-pin"
    assert_contains "$log" "federated sources whose pins this re-pin advances: bumporg/cms-platform" "federated-current: the source whose pin advances is named"
    local fedcur="$TEST_DIR/bump-fedcur-new.lock"
    bump_lock_at repo-fed-current "refs/heads/skills-lock-bump/update" > "$fedcur"
    if [[ "$(lock_field_of "$fedcur" ref)" == "$BUMP_REF_CONTENT" ]]; then
        pass "federated-current: the PRIMARY pin is held — advancing a source is not a content advance"
    else
        fail "federated-current: the PRIMARY pin is held — expected ${BUMP_REF_CONTENT:0:7}, got $(lock_field_of "$fedcur" ref)"
    fi
    if [[ "$(lock_source_ref_of "$fedcur" bumporg/cms-platform)" == "$BUMP_SRC_HEAD" ]]; then
        pass "federated-current: the federated pin advanced to its own registry's HEAD"
    else
        fail "federated-current: the federated pin advanced — expected ${BUMP_SRC_HEAD:0:7}, got $(lock_source_ref_of "$fedcur" bumporg/cms-platform)"
    fi
    local fedcur_body="$BUMP_PR_BODY_DIR/bumporg_repo-fed-current.body"
    assert_prose_contains "$fedcur_body" "Federated sources advance one at a time, and only when asked." "federated-current: the PR body says a source advanced, not that the pins are kept"
    assert_prose_contains "$fedcur_body" "bumporg/cms-platform@${BUMP_SRC_REF:0:7}\` → \`bumporg/cms-platform@${BUMP_SRC_HEAD:0:7}" "federated-current: the PR body shows the source pin old → new"
    assert_not_contains "$fedcur_body" "$TEST_DIR" "federated-current: no path from the machine that ran the bump"
    assert_contains "$fedcur_body" "cms-platform/deploy-site" "federated-current: the body quotes the SOURCE difference that caused this PR"
    assert_prose_omits "$fedcur_body" "**What moved:** \`bumporg/agentskills\`" "federated-current: the body does not announce a primary move"

    # ── BOTH halves moved: one PR, both pins advanced.
    local fedstale="$TEST_DIR/bump-fedstale-new.lock"
    bump_lock_at repo-fed-stale "refs/heads/skills-lock-bump/update" > "$fedstale"
    if [[ "$(lock_field_of "$fedstale" ref)" == "$BUMP_REF_HEAD" \
          && "$(lock_source_ref_of "$fedstale" bumporg/cms-platform)" == "$BUMP_SRC_HEAD" ]]; then
        pass "both moved: one re-pin advances the primary AND the federated pin"
    else
        fail "both moved: one re-pin advances the primary AND the federated pin — ref $(lock_field_of "$fedstale" ref), source $(lock_source_ref_of "$fedstale" bumporg/cms-platform)"
    fi
    # ── ...and every artifact says BOTH pins moved. The lock assertions above
    # shipped green while this body announced the primary alone, claimed every
    # digest in it was published at the primary's new ref — untrue of the
    # cms-platform digests, which come from a different repository at a
    # different ref — and marked the source **advanced** without quoting the
    # scoped verdict that decided it.
    local fedstale_body="$BUMP_PR_BODY_DIR/bumporg_repo-fed-stale.body"
    local fedstale_title="$BUMP_PR_BODY_DIR/bumporg_repo-fed-stale.title"
    assert_prose_contains "$fedstale_title" "and advance its federated pin for bumporg/cms-platform" \
        "both moved: the title names both pins"
    assert_prose_contains "$fedstale_body" "and the federated pins listed below." \
        "both moved: the header names both halves"
    assert_prose_omits "$fedstale_body" "**Every digest here is re-derived from the newly pinned commit**" \
        "both moved: nothing claims one commit for digests from two repositories"
    assert_prose_contains "$fedstale_body" "and each from the pin of the half it belongs to" \
        "both moved: the body says each half's digests came from its own pin"
    assert_contains "$fedstale_body" "**advanced**" \
        "both moved: the source is listed as advanced"
    assert_contains "$fedstale_body" "cms-platform/deploy-site" \
        "both moved: and the scoped verdict behind that label is quoted"
    assert_not_contains "$fedstale_body" "$TEST_DIR" \
        "both moved: no path from the machine that ran the bump"
    local fedstale_msg="$TEST_DIR/fedstale-commit-msg.txt"
    git -C "$TEST_DIR/bare/bumporg_repo-fed-stale" log -1 --format=%B \
        refs/heads/skills-lock-bump/update > "$fedstale_msg" 2>/dev/null || : > "$fedstale_msg"
    assert_prose_contains "$fedstale_msg" "A FEDERATED source moved too: bumporg/cms-platform." \
        "both moved: the commit message says the source pin moved as well"
    assert_prose_omits "$fedstale_msg" "and re-resolves only the primary ref." \
        "both moved: and does not claim the primary ref was the only one re-resolved"

    # ── The federation inverted: everything downstream targets the PRIMARY,
    # so a lock that only federates this registry must be left alone rather
    # than have some other registry's pin advanced under this one's name.
    assert_contains "$log" "federates bumporg/agentskills but pins bumporg/cms-platform as its primary" "inverted: skipped, with the reason"
    assert_no_bump_branch repo-inverted "inverted: no other registry's pin was advanced"

    # ── A bump branch that already carries someone else's commit.
    assert_contains "$log" "refusing to force-push" "diverged: an open bump branch with other content is refused"

    # ── The SELF-HEAL, and why it is not the same case as the one above.
    # Both branches carry content this run would not push. The difference is
    # whether anything would be lost: repo-diverged holds a commit that exists
    # nowhere else, repo-leftover holds only what main already has. The first
    # must be refused forever; the second must be cleaned up, or the repo
    # never receives another lock update and NOTHING goes red to say so — the
    # bumper exits 0 and the consumer's own verdict reads OK while it serves a
    # stale bundle. Five repos sat in exactly that state for four days.
    assert_contains "$log" "was a merged leftover and carried nothing the default branch lacks" \
        "leftover: a fully-merged bump branch is deleted rather than refused"
    if [[ -n "$(bump_branch_sha repo-leftover)" ]]; then
        # Re-pushed by this same run after the delete, which is the point: the
        # branch is not merely gone, the re-pin it was blocking got proposed.
        pass "leftover: the re-pin was proposed after the branch was freed"
    else
        fail "leftover: expected a fresh bump branch after the leftover was deleted, found none"
    fi
    local leftover_lock="$TEST_DIR/leftover-branch.lock"
    git -C "$TEST_DIR/bare/bumporg_repo-leftover" show \
        "refs/heads/skills-lock-bump/update:skills.lock" > "$leftover_lock" 2>/dev/null || : > "$leftover_lock"
    if [[ "$(lock_field_of "$leftover_lock" ref)" == "$BUMP_REF_HEAD" ]]; then
        pass "leftover: the freshly pushed branch carries the new ref, not the leftover content"
    else
        fail "leftover: expected ref $BUMP_REF_HEAD on the new bump branch, got $(lock_field_of "$leftover_lock" ref)"
    fi
    if [[ -n "$diverged_before" && "$(bump_branch_sha repo-diverged)" == "$diverged_before" ]]; then
        pass "diverged: the reviewer's commit is still the branch tip"
    else
        fail "diverged: the reviewer's commit is still the branch tip — was '$diverged_before', now '$(bump_branch_sha repo-diverged)'"
    fi

    # ── The stale consumer: ref advanced, digests re-derived.
    # Five, not four, since repo-leftover joined the fixture set: its bump
    # branch is a merged leftover, so the self-heal frees the name and the
    # re-pin it was blocking is proposed in the same run.
    assert_contains "$log" "5 proposed" "bump: exactly the five consumers needing a re-pin were proposed"
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
    # ── THE NEGATIVE CONTROL, and the single assertion this whole change
    # turns on. This repo's PRIMARY is stale, so the full-lock --check-current
    # answers FAILED for it — and a gate that read that combined verdict as
    # "the federated half moved" would pass --repin-source for this source and
    # write $BUMP_SRC_HEAD here. Its source is content-CURRENT but pinned
    # BEHIND its own HEAD, so "did not advance" and "advanced to HEAD" are
    # different bytes and the wrong answer cannot hide.
    assert_contains "$fed_new" "$BUMP_SRC_CONTENT" "negative control: a primary-only drift leaves the federated pin exactly where it was"
    assert_not_contains "$fed_new" "$BUMP_SRC_HEAD" "negative control: a primary-only drift does NOT advance the federated pin to its source's HEAD"
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
    assert_contains "$fed_body" "bumporg/cms-platform@${BUMP_SRC_CONTENT:0:7}" "PR body: names the federated pin that did not move"
    assert_not_contains "$fed_body" "**advanced**" "PR body: nothing claims a federated advance on a primary-only drift"
    # The quoted verdict is the primary-scoped one, so every difference line in
    # it belongs to the ref this PR advances. A combined verdict would blame a
    # cms-platform skill for an agentskills re-pin.
    assert_contains "$fed_body" "adam/finding-unknowns" "PR body: quotes the primary difference that caused this PR"
    assert_not_contains "$fed_body" "cms-platform/deploy-site" "PR body: does not blame a federated skill for a primary re-pin"
    # A lock with a federated source holds digests published in TWO
    # repositories, so "every digest here is re-derived from the newly pinned
    # commit ... published at <the primary's ref>" was false of this PR too —
    # not only of the ones where a source advanced. repo-stale, which has no
    # sources, is the paired control that keeps the unqualified sentence alive
    # where it IS true.
    assert_prose_omits "$fed_body" "**Every digest here is re-derived from the newly pinned commit**" \
        "PR body: a federated lock does not claim one commit for every digest"
    assert_prose_contains "$fed_body" "and each from the pin of the half it belongs to" \
        "PR body: it names the pin each half's digests came from instead"
}

# ── Test 8c: a re-run proposes nothing, and repairs an interrupted one ────

test_bump_idempotent() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (re-run) ==="

    local stale_before fed_before fedcur_before fedstale_before prs_before
    stale_before=$(bump_branch_sha repo-stale)
    fed_before=$(bump_branch_sha repo-federated)
    fedcur_before=$(bump_branch_sha repo-fed-current)
    fedstale_before=$(bump_branch_sha repo-fed-stale)
    prs_before=$(wc -l < "$BUMP_PR_LOG")

    # The steady state after a bump: branch pushed, PR open, and main still
    # carrying the stale lock until someone merges it. The mock has no memory,
    # so the open PRs are supplied here — EVERY repo the previous run proposed
    # for, or the re-run opens a second PR on the one that was left out.
    MOCK_OPEN_PR_REPOS="bumporg_repo-stale bumporg_repo-federated bumporg_repo-fed-current bumporg_repo-fed-stale bumporg_repo-leftover" \
        run_bump "$TEST_DIR/bump-rerun.txt"

    assert_contains "$TEST_DIR/bump-rerun.txt" "0 proposed" "re-run: nothing proposed"
    assert_contains "$TEST_DIR/bump-rerun.txt" "is already open for this branch" "re-run: the open PR is left alone"
    if [[ "$(bump_branch_sha repo-stale)" == "$stale_before" \
          && "$(bump_branch_sha repo-federated)" == "$fed_before" \
          && "$(bump_branch_sha repo-fed-current)" == "$fedcur_before" \
          && "$(bump_branch_sha repo-fed-stale)" == "$fedstale_before" ]]; then
        pass "re-run: no second commit — every bump branch is untouched"
    else
        fail "re-run: no second commit — every bump branch is untouched"
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
          && "$(bump_branch_sha repo-federated)" == "$fed_before" \
          && "$(bump_branch_sha repo-fed-current)" == "$fedcur_before" \
          && "$(bump_branch_sha repo-fed-stale)" == "$fedstale_before" ]]; then
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
    # AND NOT ONE `::warning::`, which is what makes the stub inventory's
    # sentence about this stand-in checkable. It carries neither scoped flag,
    # so a reader would expect the two SOFT probes to degrade and annotate —
    # but the HARD --repin probe exits 2 above them and neither ever runs. The
    # inventory said "The four hand-rolled ones ... emit a `::warning::`
    # apiece" over a set including this one; measured here, this lane emits
    # zero.
    if [[ "$(grep -c '::warning::' "$TEST_DIR/bump-norepin.txt" || true)" == "0" ]]; then
        pass "no re-pin flag: the soft probes below it never run, so nothing is annotated"
    else
        fail "no re-pin flag: the soft probes below it never run, so nothing is annotated — got $(grep -c '::warning::' "$TEST_DIR/bump-norepin.txt" || true)"
    fi
    assert_scoped_probe_warnings "$TEST_DIR/bump-norepin.txt" 0 \
        "no re-pin flag: neither soft federated probe annotated anything"
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

# ── Test 8f3: a lock that cannot be read is not a lock that is absent ─────
#
# `gh api .../contents/skills.lock --jq .content 2>/dev/null || encoded=""`
# threw away the ONE thing separating "this repo declares no bundles" from "the
# credential can no longer read file contents". Contents permission is granted
# App-wide, so a credential that can still enumerate both owners but no longer
# read a file makes EVERY consumer answer "no skills.lock": the whole re-pin
# pass becomes a no-op and — since eight of the ten allowlist entries in the
# real repos.yml genuinely are `lock: pending` — the summary prints "0 merged,
# 0 proposed, N skipped, 0 failed", which is exactly what a healthy night
# prints. Nothing goes red, scheduled-run-health sees a success, and every
# consumer serves a frozen bundle until somebody notices by hand.
#
# BOTH halves are asserted in one run, and they have to be: the fix is only
# correct if a genuine 404 still reads as the benign skip it has always been.
# `--dry-run` because what is under test is the reading, and a run that writes
# nothing can sit anywhere in this ordered lane.
test_bump_contents_unreadable() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (the lock could not be read) ==="

    local out="$TEST_DIR/bump-contents-fail.txt"
    local ctl="$TEST_DIR/bump-contents-control.txt"
    local prs_before
    prs_before=$(wc -l < "$BUMP_PR_LOG")

    # The CONTROL run, and here it does double duty. It is the usual "prove the
    # fetch is not simply dead" check, and it is also what makes the count below
    # attributable: this lane already carries `repo-error`, a deliberate
    # cannot-be-assessed fixture, so the summary is never at zero failures and a
    # hardcoded "1 failed" would be asserting somebody else's failure.
    run_bump "$ctl" --dry-run

    # The injected run also carries a gh notice AHEAD of the status line on the
    # same stream (MOCK_CONTENTS_STDERR_PRENOTICE). Without it gh's stderr is
    # one line long here, and the scoped assertion below cannot distinguish a
    # script that prefers the status line from one that takes whatever came
    # first — which is the whole of the rule it is meant to hold.
    BUMP_CONTENTS_FAIL_FOR_RUN="bumporg_repo-stale" \
    BUMP_CONTENTS_PRENOTICE_FOR_RUN="bumporg_repo-stale" \
        run_bump "$out" --dry-run
    unset BUMP_CONTENTS_FAIL_FOR_RUN BUMP_CONTENTS_PRENOTICE_FOR_RUN

    # The 403 half: named, counted, and red.
    assert_contains "$out" "repo-stale: could not read skills.lock" \
        "unreadable lock: the repo and the file are both named"
    # Scoped to the script's own failure line, for the reason above
    # assert_scoped_line.
    assert_scoped_line "$out" "could not read skills.lock" "HTTP 403" \
        "unreadable lock: the diagnostic quotes gh's own status line"
    # A claim about WHICH line, which the pair above cannot make on its own.
    # Behaviour-shaped on purpose: it says the operator's line names the status
    # and not the notice, and says nothing about how that line gets chosen, so
    # it survives the selection moving into a shared helper.
    assert_scoped_line_lacks "$out" "could not read skills.lock" "token expires" \
        "unreadable lock: the diagnostic is the status line, not the notice ahead of it"

    local ctl_failed inj_failed
    ctl_failed=$(sed -n 's/.*, \([0-9][0-9]*\) failed ===.*/\1/p' "$ctl" | tail -1)
    inj_failed=$(sed -n 's/.*, \([0-9][0-9]*\) failed ===.*/\1/p' "$out" | tail -1)
    if [[ -n "$ctl_failed" && -n "$inj_failed" && "$inj_failed" -eq $((ctl_failed + 1)) ]]; then
        pass "unreadable lock: counted as exactly one more failure than the same run without it"
    else
        fail "unreadable lock: failure count went from '${ctl_failed:-unparsed}' to '${inj_failed:-unparsed}', expected +1"
    fi
    assert_not_contains "$ctl" "could not read skills.lock" \
        "unreadable lock (control): the same run without the injection reads every lock fine"
    # There is deliberately NO "the run exits non-zero" assertion here. This
    # lane already carries `repo-error`, a fixture that cannot be assessed by
    # design, so BUMP_EXIT is non-zero on the control run too — the assertion
    # held identically with the injection and without it, which makes it a
    # statement about somebody else's fixture rather than about this one. The
    # control-vs-injected failure COUNT above is the attributable form of the
    # same claim, and it is the one that discriminates.
    # The sentence the old code printed for this repo, which asserted something
    # the run had not established.
    #
    # Read out of repo-stale's OWN section. The spelling this replaces greped
    # the log for `no skills.lock` lines and then greped those hits for the repo
    # name — but that log line carries no repo name (`log() { echo "  $*"; }`);
    # the name is on the `=== bumporg/repo-stale ===` header the first grep has
    # already thrown away. So the inner grep matched nothing no matter what the
    # script did, and this assertion passed unconditionally — including in the
    # exact scenario it exists to forbid.
    local stale_section
    stale_section=$(repo_section "$out" "=== bumporg/repo-stale ===")
    if [[ -z "$stale_section" ]]; then
        fail "unreadable lock: the run printed no section for bumporg/repo-stale, so this assertion has nothing to read"
    elif grep -qF 'no skills.lock' <<< "$stale_section"; then
        fail "unreadable lock: repo-stale was still reported as having no lock"
    else
        pass "unreadable lock: repo-stale is NOT reported as declaring no bundles"
    fi

    # The 404 half, in the SAME run: bumporg/repo-no-lock really has no
    # skills.lock, the repo itself answers, and that must still be the quiet
    # skip it has always been rather than collateral from the stricter branch.
    assert_contains "$out" "no skills.lock — nothing to re-pin" \
        "unreadable lock: a genuine 404 on a repo that answers is still a benign skip"
    assert_not_contains "$out" "repo-no-lock: could not read" \
        "unreadable lock: the benign skip was not promoted to a failure"

    if [[ "$(wc -l < "$BUMP_PR_LOG")" == "$prs_before" ]]; then
        pass "unreadable lock: no pull request opened"
    else
        fail "unreadable lock: no pull request opened — $(cat "$BUMP_PR_LOG")"
    fi

    # ── the THIRD way this read goes wrong: it succeeds, noisily ─────────
    #
    # Both halves above turn on the exit STATUS. This one is about a call that
    # exits 0 with the right payload on stdout and a notice on stderr — gh's
    # ordinary deprecation and auth-expiry lines — which a merged `2>&1`
    # capture folds into the base64 this loop decodes some sixty lines later.
    # base64 then refuses it, and a consumer that is perfectly healthy is
    # counted FAILED by a message that changed nothing.
    #
    # Attributed the same way the 403 half is, against the same control run:
    # the claim is that the failure count does NOT move, which is a claim the
    # bare number could not make on a lane that already carries `repo-error`.
    local nout="$TEST_DIR/bump-contents-notice.txt" notice_failed
    BUMP_CONTENTS_NOTICE_FOR_RUN="bumporg_repo-stale" \
        run_bump "$nout" --dry-run
    unset BUMP_CONTENTS_NOTICE_FOR_RUN

    assert_not_contains "$nout" "could not decode skills.lock" \
        "noisy lock read: a notice on gh's stderr does not reach the payload"
    assert_not_contains "$nout" "repo-stale: could not read skills.lock" \
        "noisy lock read: the healthy repo is not reported as unreadable"
    notice_failed=$(sed -n 's/.*, \([0-9][0-9]*\) failed ===.*/\1/p' "$nout" | tail -1)
    if [[ -n "$ctl_failed" && -n "$notice_failed" && "$notice_failed" -eq "$ctl_failed" ]]; then
        pass "noisy lock read: the failure count is unchanged from the same run without the notice"
    else
        fail "noisy lock read: failure count went from '${ctl_failed:-unparsed}' to '${notice_failed:-unparsed}', expected no change"
    fi

    # ── and the 404 that arrives on STDERR ONLY ──────────────────────────
    #
    # The 404 half above is served the way the mock has always served it: an
    # error BODY on stdout carrying `"status":"404"`, and nothing on stderr. The
    # script matches TWO surfaces for that status and says in a comment that
    # "only one of them may reach us" — but until MOCK_CONTENTS_404_STDERR_ONLY
    # existed only the stdout one was ever produced, so the stderr needle was
    # dead text. GitHub's REST errors do not all carry a `status` field, while
    # gh writes its own `gh: Not Found (HTTP 404)` line for every one of them,
    # so the stderr-only shape is the realistic half, not the contrived one.
    #
    # The verdict must be identical to the stdout-only 404's: a benign skip,
    # not a counted failure.
    local sout="$TEST_DIR/bump-contents-404-stderr.txt" stderr404_failed
    BUMP_CONTENTS_404_STDERR_FOR_RUN="bumporg_repo-no-lock" \
        run_bump "$sout" --dry-run
    unset BUMP_CONTENTS_404_STDERR_FOR_RUN

    assert_contains "$sout" "no skills.lock — nothing to re-pin" \
        "404 on stderr: a status gh reports only on stderr is still read as absent"
    assert_not_contains "$sout" "repo-no-lock: could not read" \
        "404 on stderr: the benign skip was not promoted to a failure"
    stderr404_failed=$(sed -n 's/.*, \([0-9][0-9]*\) failed ===.*/\1/p' "$sout" | tail -1)
    if [[ -n "$ctl_failed" && -n "$stderr404_failed" && "$stderr404_failed" -eq "$ctl_failed" ]]; then
        pass "404 on stderr: the failure count is unchanged from the same run without it"
    else
        fail "404 on stderr: failure count went from '${ctl_failed:-unparsed}' to '${stderr404_failed:-unparsed}', expected no change"
    fi
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
    assert_scoped_probe_warnings "$TEST_DIR/bump-noformat.txt" 2 \
        "old generator: both soft federated probes degraded, one warning each"
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

# ── Test 8h3b: a generator too old to scope, or to advance a source ───────
#
# The two federated flags arrive in the registry's own pull request, and this
# script's generator comes from a checkout of that registry's DEFAULT BRANCH.
# So there is a window — however short — in which the nightly meets a
# generator that has neither. A hard probe would ground every run in that
# window over the ordering of two green PRs, which is why both probes are soft
# and why this test exists: the required behaviour is DEGRADE to exactly what
# this script did before (report the federated half, act on nothing), say so
# where a scheduled run's reader will actually see it, and leave the lock
# alone.
#
# Run against a copy of the real fixture stub with the two flags STRIPPED,
# rather than a hand-written toy, so the degraded run still answers the
# primary and shape questions truthfully and the only difference is the two
# missing flags. The strip is verified line by line and aborts if the stub
# moved out from under it — a strip that silently did nothing would leave the
# flags present and this whole test asserting the ordinary path.
test_bump_generator_without_scoped_flags() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a generator with no federated flags) ==="

    local gen="$TEST_DIR/generator-no-scoped-flags.py"
    write_stub_generator "$gen"
    python3 "$TEST_DIR/strip-scoped-flags.py" "$gen" || {
        fail "no scoped flags: could not strip the stub's federated flags"
        return
    }
    # Proof the strip did what its name says, before anything is asserted
    # about the run: the flags must be gone from --help AND rejected if passed.
    if python3 "$gen" --help 2>&1 | grep -q -- '--only'; then
        fail "no scoped flags: --only is still advertised by the stripped stub"
    else
        pass "no scoped flags: the stripped stub no longer advertises --only"
    fi
    if python3 "$gen" --help 2>&1 | grep -q -- '--repin-source'; then
        fail "no scoped flags: --repin-source is still advertised by the stripped stub"
    else
        pass "no scoped flags: the stripped stub no longer advertises --repin-source"
    fi

    local before_lock="$TEST_DIR/fedcur-before-degraded.lock"
    bump_lock_at repo-fed-current main > "$before_lock"

    BUMP_GENERATOR_FOR_RUN="$gen" run_bump "$TEST_DIR/bump-noscoped.txt" --dry-run
    unset BUMP_GENERATOR_FOR_RUN
    local log="$TEST_DIR/bump-noscoped.txt"

    assert_contains "$log" "has no '--check-current --only <REGISTRY>'" \
        "no scoped flags: the missing gate primitive is announced, by name"
    assert_contains "$log" "has no '--repin --repin-source <REGISTRY>@'" \
        "no scoped flags: the missing remedy is announced too"
    assert_contains "$log" "::warning::" \
        "no scoped flags: announced as an annotation, which survives to a scheduled run's summary"
    # Exit 2 is what this script uses for "the run is refused before any repo
    # is touched", which is what a HARD probe would have produced. Anything
    # else means the run went ahead — this fleet fixture always ends 1 because
    # repo-error cannot be assessed, and that failure is not this one.
    if [[ $BUMP_EXIT -ne 2 ]]; then
        pass "no scoped flags: degrades instead of grounding the run"
    else
        fail "no scoped flags: degrades instead of grounding the run (exit 2 — the run was refused)"
    fi
    assert_contains "$log" "=== bumporg/repo-stale ===" \
        "no scoped flags: every consumer is still assessed"
    # Degrading means behaving as this script did before: the moved federated
    # half is reported and nothing is re-pinned for it. repo-fed-current's
    # primary is content-current, so with no scoped question there is no
    # reason left to propose anything for it at all.
    # The `::warning::` prefix is asserted as part of the SAME needle, not on
    # its own line: `log()` writes to stdout, and a green nightly's stdout is
    # read by nobody. Split the two and the escalation from log() to an
    # annotation passes on the probe's warnings alone, while this line quietly
    # goes back to being invisible.
    assert_prose_contains "$log" "::warning::bumporg/repo-fed-current: a FEDERATED source has moved on since the ref this lock pins for it, and this run did not ask which one or advance it — this generator has no $(scoped_flag_pair) pair" \
        "no scoped flags: the moved federated half is reported as an annotation, not acted on"
    assert_not_contains "$log" "federated sources whose pins this re-pin advances" \
        "no scoped flags: no federated pin is advanced on a question this generator could not answer"
    local after_lock="$TEST_DIR/fedcur-after-degraded.lock"
    bump_lock_at repo-fed-current main > "$after_lock"
    if cmp -s "$before_lock" "$after_lock"; then
        pass "no scoped flags: the consumer's lock is byte-identical"
    else
        fail "no scoped flags: the consumer's lock is byte-identical"
    fi
}

# ── Test 8h2b: a re-pin that is BOTH a shape repair and a source advance ──
#
# TWO REASONS AT ONCE, and the combination had no fixture. `repin_reason` is
# `format` here — a malformed digest is malformed whatever else is true — while
# `fed_drifted_regs` is non-empty, and every artifact used to branch on the
# reason ALONE. The result was a PR titled "(pin unchanged)" and bodied "the
# digest SHAPE ... and nothing else / every digest below is the same hex it
# was" over a diff that advanced a source ref and changed one of that source's
# digests, above a body line marking that source **advanced**. Worse, it told
# the reviewer that the quoted `--check-format` remediation was "the command
# this PR ran ... so you can reproduce this diff from it", while that line
# carries no `--repin-source` and reproduces no such thing.
#
# Reachable now, not hypothetically: the fleet has eight bare-hex consumer
# locks and two federated ones, and these PRs merge themselves on the sweep.
test_bump_format_and_federated() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a shape repair that also advances a source) ==="

    local root="$TEST_DIR/bare-fmtfed"
    local work="$TEST_DIR/work/bumporg-repo-fmtfed"
    rm -rf "$root" "$work"
    mkdir -p "$root/bumporg_repo-fmtfed" "$work"
    git init --bare --initial-branch=main "$root/bumporg_repo-fmtfed" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$root/bumporg_repo-fmtfed"
    echo "# repo-fmtfed" > README.md
    # Primary content-CURRENT and bare (so the shape gate is what fires for it)
    # with a federated source that HAS moved (so the other axis fires too).
    seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_CONTENT" "$BUMP_SRC_REF"
    strip_digest_labels skills.lock
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"

    BUMP_BARE_DIR_FOR_RUN="$root" run_bump "$TEST_DIR/bump-fmtfed.txt"
    unset BUMP_BARE_DIR_FOR_RUN
    local body="$BUMP_PR_BODY_DIR/bumporg_repo-fmtfed.body"
    local title="$BUMP_PR_BODY_DIR/bumporg_repo-fmtfed.title"
    local after="$TEST_DIR/fmtfed-after.lock"
    git -C "$root/bumporg_repo-fmtfed" show \
        "refs/heads/skills-lock-bump/update:skills.lock" > "$after" 2>/dev/null || : > "$after"
    if [[ -s "$body" && -s "$title" && -s "$after" ]]; then
        pass "format+federated: there is a PR, a title and a pushed lock to assert on"
    else
        fail "format+federated: there is a PR, a title and a pushed lock to assert on"
        rm -rf "$root" "$work"
        return
    fi

    # ── The diff. Both halves really do move, so the claims below have a
    #    subject: the primary's pin held, the source's pin advanced.
    if [[ "$(lock_field_of "$after" ref)" == "$BUMP_REF_CONTENT" \
       && "$(lock_source_ref_of "$after" bumporg/cms-platform)" == "$BUMP_SRC_HEAD" ]]; then
        pass "format+federated: the primary pin is held and the source pin advanced"
    else
        fail "format+federated: the primary pin is held and the source pin advanced — ref $(lock_field_of "$after" ref), source $(lock_source_ref_of "$after" bumporg/cms-platform)"
    fi

    # ── The TITLE, which is all a PR list shows.
    assert_prose_contains "$title" "advance its federated pin for bumporg/cms-platform" \
        "format+federated: the title names the pin that moved"
    assert_prose_omits "$title" "hex> (pin unchanged)" \
        "format+federated: the title does not read as shape-only"
    assert_prose_contains "$title" "(primary pin unchanged)" \
        "format+federated: and still says which pin did NOT move"

    # ── The HEADER and the two sentences that were false.
    assert_prose_contains "$body" "and the federated pins listed below." \
        "format+federated: the header names both halves"
    assert_prose_omits "$body" "the digest SHAPE stored in \`skills.lock\`, and nothing else." \
        "format+federated: the header does not claim nothing else changed"
    assert_prose_omits "$body" "and every digest below is the same hex it was, wearing its label." \
        "format+federated: nothing claims every digest is a relabel"

    # ── The reproducibility claim, which is the one with a checkable subject.
    assert_prose_omits "$body" "**That remediation line is the command this PR ran**, \`--ref\` included, so you can reproduce this diff from it." \
        "format+federated: no claim that the quoted line reproduces this diff"
    assert_prose_contains "$body" "**That remediation line is the SHAPE half of the command this PR ran**, \`--ref\` included." \
        "format+federated: the body says which half that line is"
    assert_prose_contains "$body" "--repin-source 'bumporg/cms-platform@'" \
        "format+federated: and names the --repin-source this run appended"
    # BOTH additions. The generator looks for a source's clone beside --repo
    # at the sibling ../<repo-name>, and --source-repo is what overrides that
    # lookup — so a reconstructed command carrying only the flags above can
    # stop at "no checkout at <path>" before it writes. The needle carries the
    # PLACEHOLDER, not this machine's path — the `$TEST_DIR` assertion further
    # down forbids a real one from reaching a PR body at all.
    assert_prose_contains "$body" "--source-repo 'bumporg/cms-platform=<a clone of it>'" \
        "format+federated: and the --source-repo that points it at each source's clone"

    # ── The evidence for the **advanced** label, which used to be absent on
    #    this path: the scoped verdict is quoted, so the label is checkable.
    assert_contains "$body" "cms-platform/deploy-site" \
        "format+federated: the scoped verdict for the advanced source is quoted"
    assert_contains "$body" "**advanced**" \
        "format+federated: and the source is listed as advanced"
    assert_not_contains "$body" "$TEST_DIR" \
        "format+federated: no path from the machine that ran the bump"

    # ── Where the digests came from. Two repositories, so no single commit
    #    is the answer.
    assert_prose_omits "$body" "**Every digest here is re-derived from the commit this lock already pinned**" \
        "format+federated: nothing claims one commit for digests from two repositories"
    assert_prose_contains "$body" "and each from the pin of the half it belongs to" \
        "format+federated: the body says each half's digests came from its own pin"

    # ── The commit message, the same artifact one layer in.
    local msg="$TEST_DIR/fmtfed-commit-msg.txt"
    git -C "$root/bumporg_repo-fmtfed" log -1 --format=%B \
        refs/heads/skills-lock-bump/update > "$msg" 2>/dev/null || : > "$msg"
    assert_prose_contains "$msg" "and advance its federated pin for bumporg/cms-platform" \
        "format+federated: the commit subject names the pin that moved"
    assert_prose_contains "$msg" "A FEDERATED source moved too: bumporg/cms-platform." \
        "format+federated: the commit body says so too"
    assert_prose_omits "$msg" "so the pin does not move and" \
        "format+federated: the commit body does not claim no pin moved"

    rm -rf "$root" "$work"
}

# ── Test 8h3c: a lock that federates one registry twice ───────────────────
#
# Two things at once, and the second is a policy this run PINS rather than
# discovers. `source_registries` is keyed on the registry NAME everywhere
# downstream — one scoped question, one `--repin-source <name>@`, one line in
# the PR body — so leaving duplicates in it makes the gate ask the same
# question twice and build the same flag twice. The generator then refuses the
# bumper's own duplicated flag ("names <reg> twice; one pin per source") and
# its accurate diagnosis of the LOCK never reaches the log.
#
# THE POLICY: with the flag built once, the generator refuses it because one
# spec names two entries and advancing "it" has two answers. This script does
# not then drop that source and re-pin the rest. Half-re-pinning a federated
# lock is the damage ADR 0001 named, and which of two entries a spec meant is
# an adjudication a retry cannot make — so the failure is counted, the lock is
# left alone, and the night goes red with the reason a human can act on. That
# is the same policy already written for a cross-registry basename collision
# and for the shrink guard.
test_bump_twice_federated_lock() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a lock federating one registry twice) ==="

    local root="$TEST_DIR/bare-twicefed"
    local work="$TEST_DIR/work/bumporg-repo-twicefed"
    rm -rf "$root" "$work"
    mkdir -p "$root/bumporg_repo-twicefed" "$work"
    git init --bare --initial-branch=main "$root/bumporg_repo-twicefed" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$root/bumporg_repo-twicefed"
    echo "# repo-twicefed" > README.md
    # Primary content-current, so the ONLY thing that can force a re-pin here
    # is the federated half — and the federated half is the ambiguous one.
    #
    # Seeded with ONE source and generated like every other fixture lock, then
    # the duplicate entry is appended afterwards: the stand-in now refuses to
    # --repin a twice-federated lock at all, which is the behaviour under test,
    # so the fixture cannot be filled in that shape. The second entry names a
    # bundle no registry carries, so it contributes no digests and the
    # generated `skills` map stays true of the file.
    seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_CONTENT" "$BUMP_SRC_REF"
    python3 -c '
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    doc = json.load(handle)
first = doc["sources"][0]
doc["sources"].append({**first, "bundles": ["other"]})
with open(path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
' skills.lock
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    local before="$TEST_DIR/twicefed-before.lock"
    cp skills.lock "$before"
    cd "$REPO_ROOT"

    BUMP_BARE_DIR_FOR_RUN="$root" run_bump "$TEST_DIR/bump-twicefed.txt"
    unset BUMP_BARE_DIR_FOR_RUN
    local log="$TEST_DIR/bump-twicefed.txt"

    assert_contains "$log" "federates that registry twice" \
        "twice-federated: the generator's diagnosis of the LOCK is what the run reports"
    assert_not_contains "$log" "one pin per source" \
        "twice-federated: not the bumper's own duplicated flag"
    assert_contains "$log" "1 failed" \
        "twice-federated: counted, so a human adjudicates rather than a retry"
    if [[ -z "$(git -C "$root/bumporg_repo-twicefed" rev-parse --verify -q \
                refs/heads/skills-lock-bump/update 2>/dev/null || true)" ]]; then
        pass "twice-federated: nothing is proposed — a lock is never half re-pinned"
    else
        fail "twice-federated: nothing is proposed — a lock is never half re-pinned"
    fi

    rm -rf "$root" "$work"
}

# ── Test 8h3a: the stand-in refuses everything the real generator refuses ─
#
# THE HOLLOW-VERIFIER TRAP, in the one place this suite is exposed to it. Every
# federated assertion in this file is green against `write_stub_generator`, so
# a refusal the real generator has and the stand-in lacks is a lane where the
# fleet script is measured against something that cannot fail. The bumper's
# gate reads only whether a `FAILED:` line exists, never whether that block
# carried a repair — so a stand-in that always prints a `--repin-source`
# command hides that the real generator sometimes prints none and then refuses
# the flag.
#
# Measured against Adam-S-Daniel/agentskills@ag58-generator. These are its
# refusals as they stand there NOW, including the two its round-3 and round-4
# work added (the report-side suppression, and the source-checkout identity
# probe). The list is asserted here rather than described in a comment,
# because a comment cannot go red.
#
# THE STUB INVENTORY, recorded so the next flag does not rediscover it. TWELVE
# generator stand-ins live in this file, in two kinds, and only the first is
# under test here.
#
# HAND-ROLLED, each a whole small generator written for one shortfall:
#   * write_stub_generator          — the fleet stand-in, all lanes
#   * generator-no-repin.py         — lacks --repin; the run exits 2 at the one
#                                     HARD probe before any flag here matters
#   * generator-no-check-format.py  — lacks --check-format
#   * generator-format-error.py     — --check-format present, unanswerable
#   * generator-errline.py          — rejects --check-current, argparse-style
#
# DERIVED BY SURGERY on write_stub_generator's output, so each keeps every
# other refusal the stand-in has:
#   * generator-no-scoped-flags.py     — both scoped flags stripped
#   * generator-missing-only.py        — only --only stripped
#   * generator-missing-repin-source.py — only --repin-source stripped
#   * generator-degraded-fed.py        — both stripped, for the body lane
#   * generator-degraded-halves.py     — both stripped, for the which-half lane
#   * generator-degraded-selffed.py    — both stripped, for the self-federating
#                                        lane where the log and the body must agree
#   * generator-only-bundles.py        — --only replaced by --only-bundles
#
# COUNTED BY A TEST, not by this comment. An inventory that states a number
# nothing checks is the defect this repo files against itself, and the first
# version of this block proved it: it said FOUR above a list of five, three
# lines before a sentence that only parses for five, while four more stand-ins
# it never mentioned already existed. The assertion in this test counts them.
#
# The four besides `write_stub_generator` carry neither `--only` nor
# `--repin-source`. There are TWO soft federated probes, so three of these four
# lanes are annotated TWICE — one `::warning::` per probe, not one per lane.
# generator-no-repin.py is annotated not at all, because the run exits 2 at the
# one HARD probe before either soft probe is reached; its own bullet above says
# so. Every one of those four numbers is COUNTED, by assert_scoped_probe_warnings
# in each lane, because the previous wording of this sentence said "a
# `::warning::` apiece" over a set that included the silent one and nothing
# could tell it was wrong twice over. Those three lanes pass today only because
# both probes are SOFT; harden either into an exit 2 and they go red without
# having changed.
test_bump_stub_generator_parity() {
    echo ""
    echo "=== Test: the stand-in generator's refusals match the real one's ==="

    # THE INVENTORY ABOVE, CHECKED. Both directions and the two numbers it
    # states, because a list is only as good as its completeness and the first
    # version of this block was wrong in every way one can be: it said FOUR
    # over a list of five, followed by a sentence that only parses for five,
    # while four stand-ins it never mentioned already existed in the file. A
    # comment asserting a count nothing counts is what this repo files issues
    # about; the count is here instead.
    if python3 -c '
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
block = re.search(r"THE STUB INVENTORY.*?without having changed\\.", src, re.S)
if not block:
    sys.exit("the inventory block is gone")
block = block.group(0)
listed = re.findall(r"^#   \* (\S+)", block, re.M)
hand = re.findall(r"^#   \* (\S+)", block.split("DERIVED BY SURGERY")[0], re.M)
words = {"four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
         "twelve": 12}
stated = re.search(r"([A-Z]+) *\n?#? *generator stand-ins", block)
if not stated:
    sys.exit("the inventory no longer states how many stand-ins there are")
if words.get(stated.group(1).lower()) != len(listed):
    sys.exit("the inventory says %s but lists %d" % (stated.group(1), len(listed)))
# The off-by-one is STATED, not encoded here: the sentence says "besides
# `write_stub_generator`", so the number it gives is the list minus that one
# entry and a reader counting the bullets against it lands on the same answer.
# The previous wording said "The four hand-rolled ones" over a five-bullet
# list and left this check subtracting 1 to make it true.
hand_stated = re.search(r"The (\w+) besides .write_stub_generator.", block)
if not hand_stated:
    sys.exit("the inventory no longer says how many stand-ins are hand-rolled besides write_stub_generator")
if words.get(hand_stated.group(1).lower()) != len(hand) - 1:
    sys.exit("the inventory says %s besides write_stub_generator but lists %d"
             % (hand_stated.group(1), len(hand) - 1))
# Both directions, against the CODE of this file — every comment line is
# dropped first, the inventory bullets among them, so no prose can vouch for a
# stand-in that nothing builds. And every reference has to be a literal path:
# a name assembled from a shell variable resolves to nothing a checker can
# match, and the listed-but-never-built direction used to skip any listed name
# sharing such a prefix, which let the two partial-strip stand-ins vouch for
# each other.
body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
used = set(re.findall(r"generator-[A-Za-z0-9$_.-]+\.py", body))
literal = {u for u in used if "$" not in u}
problems = ["a stand-in is named by interpolation, so nothing can resolve it: " + u
            for u in sorted(used - literal)]
if "write_stub_generator" not in listed or "write_stub_generator() {" not in body:
    problems.append("write_stub_generator is not both listed and defined")
for name in listed:
    if name == "write_stub_generator" or name in literal:
        continue
    problems.append("listed but never built: " + name)
for name in literal:
    if name not in listed:
        problems.append("built but not listed: " + name)
if problems:
    sys.exit("; ".join(sorted(problems)))
' "$REPO_ROOT/test/run-tests.sh" 2>"$TEST_DIR/stub-inventory.err"; then
        pass "stub inventory: it names every stand-in in this file, and only those, in the numbers it states"
    else
        fail "stub inventory: it names every stand-in in this file, and only those, in the numbers it states — $(cat "$TEST_DIR/stub-inventory.err")"
    fi

    local dir="$TEST_DIR/stubparity"
    rm -rf "$dir"; mkdir -p "$dir"
    local gen="$TEST_DIR/registry/scripts/generate_skills_lock.py"

    # One ordinary federated lock, and one that federates the SAME registry
    # twice — representable, and green to a --check keyed on bundle uniqueness.
    python3 -c '
import json, sys
out, ref, src = sys.argv[1:4]
def lock(sources, primary_ref=None):
    return {"registry": "bumporg/agentskills", "ref": primary_ref or ref,
            "bundles": ["adam"], "sources": sources, "skills": {},
            "generated_from": primary_ref or ref}
def source(bundles, r=None):
    return {"registry": "bumporg/cms-platform", "ref": r or src,
            "bundles": bundles, "layout": "skills"}
docs = {
    "one.lock": lock([source(["cms-platform"])]),
    "twice.lock": lock([source(["cms-platform"]), source(["other"])]),
    "branchpin.lock": lock([source(["cms-platform"])], primary_ref="main"),
}
for name, doc in docs.items():
    with open("%s/%s" % (out, name), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
' "$dir" "$BUMP_REF_CONTENT" "$BUMP_SRC_REF"

    local out before after
    local src_arg="--source-repo=bumporg/cms-platform=$TEST_DIR/cms-platform"

    # 1. `--only ''` is a value the caller passed, not an absent flag. Guarding
    #    on truthiness silently degrades the run into a DIFFERENT command.
    before=$(cat "$dir/one.lock")
    out=$(python3 "$gen" --only '' --repin --repo "$TEST_DIR/registry" \
          "$src_arg" -o "$dir/one.lock" 2>&1) && out="EXIT0: $out"
    if grep -q 'EXIT0' <<< "$out"; then
        fail "stub parity: --only '' is refused, not silently ignored"
    else
        pass "stub parity: --only '' is refused, not silently ignored"
    fi
    if [[ "$before" == "$(cat "$dir/one.lock")" ]]; then
        pass "stub parity: and nothing is written while it is refused"
    else
        fail "stub parity: and nothing is written while it is refused"
    fi

    # 2. The source-checkout IDENTITY probe. Point --source-repo at the wrong
    #    repository and the pin the lock already carries is what catches it —
    #    without this the impostor's HEAD lands under the right registry's name
    #    at exit 0.
    out=$(python3 "$gen" --repin --repo "$TEST_DIR/registry" \
          --repin-source 'bumporg/cms-platform@' \
          --source-repo "bumporg/cms-platform=$TEST_DIR/registry" \
          -o "$dir/one.lock" 2>&1) && out="EXIT0: $out"
    assert_stub_refusal "$out" "does not contain" \
        "stub parity: a --source-repo pointing at the wrong repository is refused"
    if [[ "$before" == "$(cat "$dir/one.lock")" ]]; then
        pass "stub parity: and the lock is byte-identical afterwards"
    else
        fail "stub parity: and the lock is byte-identical afterwards"
    fi

    # 3. A registry federated TWICE. One spec names two sources, so advancing
    #    "it" has two answers — and the REPORT must not print a command the
    #    flag then rejects.
    out=$(python3 "$gen" --check-current --only bumporg/cms-platform \
          --repo "$TEST_DIR/registry" "$src_arg" -o "$dir/twice.lock" 2>&1) && out="EXIT0: $out"
    assert_stub_refusal "$out" "FAILED:" \
        "stub parity: a twice-federated source still reports its drift"
    # The COMMAND line, not the flag name: the headline names the flag while
    # explaining why no command follows, so a bare substring match on
    # `--repin-source` reports a command that is not there.
    if grep -qE '^ +python3 .*--repin-source' <<< "$out"; then
        fail "stub parity: and prints no --repin-source command the flag would refuse"
    else
        pass "stub parity: and prints no --repin-source command the flag would refuse"
    fi
    assert_stub_refusal "$out" "federates that registry twice" \
        "stub parity: the headline carries the reason instead of a command"
    out=$(python3 "$gen" --repin --repo "$TEST_DIR/registry" \
          --repin-source 'bumporg/cms-platform@' "$src_arg" \
          -o "$dir/twice.lock" 2>&1) && out="EXIT0: $out"
    assert_stub_refusal "$out" "federates that registry twice" \
        "stub parity: and the flag refuses it in the same words"

    # 4. A PRIMARY pinned at a branch name proves nothing about which clone
    #    --repin is reading: `main^{commit}` resolves anywhere.
    out=$(python3 "$gen" --repin --repo "$TEST_DIR/registry" "$src_arg" \
          -o "$dir/branchpin.lock" 2>&1) && out="EXIT0: $out"
    assert_stub_refusal "$out" "which is not a commit sha" \
        "stub parity: a --repin whose pin is a branch name is refused"

    # 5. The two refusals the stand-in already had, kept under the predicate so
    #    the consolidation cannot have dropped them.
    out=$(python3 "$gen" --repin --repo "$TEST_DIR/registry" \
          --repin-source 'bumporg/agentskills@' "$src_arg" \
          -o "$dir/one.lock" 2>&1) && out="EXIT0: $out"
    assert_stub_refusal "$out" "PRIMARY registry" \
        "stub parity: --repin-source naming the primary is refused"
    out=$(python3 "$gen" --repin --repo "$TEST_DIR/registry" \
          --repin-source 'bumporg/nowhere@' "$src_arg" \
          -o "$dir/one.lock" 2>&1) && out="EXIT0: $out"
    assert_stub_refusal "$out" "not a source this lock federates" \
        "stub parity: --repin-source naming an unfederated registry is refused"

    rm -rf "$dir"
    cd "$REPO_ROOT"
}

# assert_stub_refusal <captured output> <needle> <label> — the command must
# have FAILED (the caller prefixes EXIT0: when it did not) and said why.
assert_stub_refusal() {
    if grep -q '^EXIT0' <<< "$1"; then
        fail "$3 — the generator exited 0"
    elif grep -qF -- "$2" <<< "$1"; then
        pass "$3"
    else
        fail "$3 — expected '$2' in: $(head -3 <<< "$1")"
    fi
}

# ── Test 8h3b: a lock that federates its own primary registry ─────────────
#
# Representable, and nothing rejects it: `plan_sources`' uniqueness check is
# keyed on BUNDLE, so one registry may appear as both the primary and a source
# with different bundles and its own pin. The generator refuses to SCOPE to
# such a name, correctly — one name, two entries, two answers — so a scoped
# loop that asks anyway lands on the else-fail path, which is safe and has no
# way back: red every night until a human edits the consumer's lock. On the
# combined-verdict behaviour this replaced, the same lock was re-pinned
# normally, so asking made a passing case a permanent failure.
#
# The primary here is STALE, so a re-pin is genuinely due: this measures that
# the re-pin still happens, not merely that nothing exploded.
test_bump_self_federating_lock() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a lock naming its primary as a source) ==="

    local root="$TEST_DIR/bare-selffed"
    local work="$TEST_DIR/work/bumporg-repo-selffed"
    rm -rf "$root" "$work"
    mkdir -p "$root/bumporg_repo-selffed" "$work"
    git init --bare --initial-branch=main "$root/bumporg_repo-selffed" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$root/bumporg_repo-selffed"
    echo "# repo-selffed" > README.md
    # Written here rather than through seed_bump_lock, which hardcodes the
    # sibling registry: the whole shape under test is a `sources` entry whose
    # registry EQUALS the lock's own. The bundle is one no registry carries, so
    # that entry contributes no skills and cannot collide with the primary's.
    python3 -c '
import json, sys
path, ref = sys.argv[1:3]
doc = {
    "registry": "bumporg/agentskills",
    "ref": ref,
    "bundles": ["adam"],
    "sources": [{
        "registry": "bumporg/agentskills",
        "ref": ref,
        "bundles": ["extra"],
        "layout": "skills",
    }],
    "skills": {},
    "generated_from": ref,
}
with open(path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
' skills.lock "$BUMP_REF_OLD"
    python3 "$TEST_DIR/registry/scripts/generate_skills_lock.py" --repin \
        --repo "$TEST_DIR/registry" --ref "$BUMP_REF_OLD" \
        --source-repo "bumporg/agentskills=$TEST_DIR/registry" -o skills.lock >/dev/null
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"

    BUMP_BARE_DIR_FOR_RUN="$root" run_bump "$TEST_DIR/bump-selffed.txt"
    unset BUMP_BARE_DIR_FOR_RUN
    local log="$TEST_DIR/bump-selffed.txt"

    assert_contains "$log" "names bumporg/agentskills as both its primary registry and a federated source" \
        "self-federating: the question this script cannot ask is named, not asked"
    assert_not_contains "$log" "could not decide whether skills.lock's bumporg/agentskills source is current" \
        "self-federating: no unanswerable scoped question is put"
    assert_contains "$log" "0 failed" \
        "self-federating: not a permanent nightly failure"
    assert_contains "$log" "1 proposed" \
        "self-federating: the stale primary is still re-pinned"

    local after="$TEST_DIR/selffed-after.lock"
    git -C "$root/bumporg_repo-selffed" show \
        "refs/heads/skills-lock-bump/update:skills.lock" > "$after" 2>/dev/null || : > "$after"
    if [[ "$(lock_field_of "$after" ref)" == "$BUMP_REF_HEAD" ]]; then
        pass "self-federating: the primary pin advanced"
    else
        fail "self-federating: the primary pin advanced — got $(lock_field_of "$after" ref)"
    fi
    if [[ "$(lock_source_ref_of "$after" bumporg/agentskills)" == "$BUMP_REF_OLD" ]]; then
        pass "self-federating: the self-named source entry is carried through untouched"
    else
        fail "self-federating: the self-named source entry is carried through — got $(lock_source_ref_of "$after" bumporg/agentskills)"
    fi
    local body="$BUMP_PR_BODY_DIR/bumporg_repo-selffed.body"
    assert_prose_contains "$body" "**One \`sources\` entry names \`bumporg/agentskills\`, this lock's own primary registry.**" \
        "self-federating: the PR body discloses the entry nothing asked about"
    assert_not_contains "$body" "**unchanged**" \
        "self-federating: and gives it no verdict, because no question was put"

    rm -rf "$root" "$work"
}

# ── Test 8h3b2: a self-federating lock met by a generator with no flags ──
#
# THE TWO ARTIFACTS OF ONE RUN, CHECKED AGAINST EACH OTHER. The lock names its
# own primary as a source AND the generator has neither scoped flag, so the
# entry's pin is carried through for two independent reasons — and only one of
# them is a reason this run can stand behind. Round 2 filed the paragraph that
# explained a `--check-current --only` refusal on a run with no `--only`;
# round 3 split it into two claims and fixed the PR body, and left the `log()`
# line saying the scoped reason to whoever reads a nightly.
#
# So both artifacts are read here, and the assertion is that they AGREE.
test_bump_degraded_self_federating_lock() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a self-federating lock, no scoped flags) ==="

    local root="$TEST_DIR/bare-selffed-degraded"
    local work="$TEST_DIR/work/bumporg-repo-selffed-degraded"
    rm -rf "$root" "$work"
    mkdir -p "$root/bumporg_repo-selffed-degraded" "$work"
    git init --bare --initial-branch=main "$root/bumporg_repo-selffed-degraded" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$root/bumporg_repo-selffed-degraded"
    echo "# repo-selffed-degraded" > README.md
    python3 -c '
import json, sys
path, ref = sys.argv[1:3]
doc = {
    "registry": "bumporg/agentskills",
    "ref": ref,
    "bundles": ["adam"],
    "sources": [{
        "registry": "bumporg/agentskills",
        "ref": ref,
        "bundles": ["extra"],
        "layout": "skills",
    }],
    "skills": {},
    "generated_from": ref,
}
with open(path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
' skills.lock "$BUMP_REF_OLD"
    python3 "$TEST_DIR/registry/scripts/generate_skills_lock.py" --repin \
        --repo "$TEST_DIR/registry" --ref "$BUMP_REF_OLD" \
        --source-repo "bumporg/agentskills=$TEST_DIR/registry" -o skills.lock >/dev/null
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"

    # The generator, stripped of both scoped flags. Verified before anything
    # is asserted about the run, or this would be the ORDINARY self-federating
    # lane wearing a different name.
    local gen="$TEST_DIR/generator-degraded-selffed.py"
    write_stub_generator "$gen"
    if ! python3 "$TEST_DIR/strip-scoped-flags.py" "$gen"; then
        fail "degraded self-federating: could not strip the stub's federated flags"
        rm -rf "$root" "$work"
        return
    fi
    if python3 "$gen" --help 2>&1 | grep -q -- '--only'; then
        fail "degraded self-federating: --only is still advertised by the stripped stub"
        rm -rf "$root" "$work"
        return
    fi
    pass "degraded self-federating: the stripped stub no longer advertises --only"

    BUMP_BARE_DIR_FOR_RUN="$root" BUMP_GENERATOR_FOR_RUN="$gen" \
        run_bump "$TEST_DIR/bump-selffed-degraded.txt"
    unset BUMP_BARE_DIR_FOR_RUN BUMP_GENERATOR_FOR_RUN
    local log="$TEST_DIR/bump-selffed-degraded.txt"
    local body="$BUMP_PR_BODY_DIR/bumporg_repo-selffed-degraded.body"

    if [[ -s "$body" ]]; then
        pass "degraded self-federating: the stale primary still gets its PR"
    else
        fail "degraded self-federating: the stale primary still gets its PR"
        rm -rf "$root" "$work"
        return
    fi

    # THE LOG. The fact survives the degrade; the reason does not. A run with
    # no `--only` cannot have declined to put a `--check-current --only`
    # question BECAUSE the name has two answers — it could not have put one
    # about any source, whatever the name meant.
    assert_prose_contains "$log" "names bumporg/agentskills as both its primary registry and a federated source" \
        "degraded self-federating: the log still names the entry nothing asked about"
    assert_prose_omits "$log" "$(self_named_log_line_text true skills.lock bumporg/agentskills)" \
        "degraded self-federating: and does not give a reason this generator could not have had"
    assert_prose_contains "$log" "$(self_named_log_line_text false skills.lock bumporg/agentskills)" \
        "degraded self-federating: it gives the reason this run actually has"

    # THE PR BODY OF THE SAME RUN, which must say the same thing. This is the
    # pair that contradicted each other.
    assert_prose_contains "$body" "No per-source question was put about any source in this run" \
        "degraded self-federating: the body gives the degraded reason too"
    assert_prose_omits "$body" "A \`--check-current --only bumporg/agentskills\` has two answers" \
        "degraded self-federating: and neither artifact describes a refusal nothing here could make"

    # ── AND THE PER-REPO ANNOTATION OF THE SAME RUN, which is the third
    #    artifact and was the one nothing here read. It told the reader to
    #    "Update the registry checkout to ask one scoped question per source"
    #    over a lock whose only `sources` entry names its own primary
    #    registry — a remedy whose result is ZERO scoped questions, because
    #    this script never scopes a question to that name and
    #    `--repin-source` refuses it. The same run's body says so outright:
    #    "No federated source in this lock could be asked about."
    #
    #    Both halves asserted, because the fix is a branch and only one arm
    #    of it is exercised here: the unactionable remedy is gone, and what
    #    replaced it says why no checkout upgrade would add a question. The
    #    OTHER arm — a lock that does have an askable source, where the
    #    remedy is the right advice — is asserted in test_bump_degraded_-
    #    federated_body, so neither arm can be deleted unnoticed.
    assert_prose_omits "$log" "$(degraded_fed_remedy_text 1 bumporg/agentskills)" \
        "degraded self-federating: no remedy is offered that would yield no question"
    assert_prose_contains "$log" "$(degraded_fed_remedy_text 0 bumporg/agentskills)" \
        "degraded self-federating: the annotation says why, instead of sending the reader to a no-op"

    # And the lock: the primary advances, the self-named entry does not.
    local after="$TEST_DIR/selffed-degraded-after.lock"
    git -C "$root/bumporg_repo-selffed-degraded" show \
        "refs/heads/skills-lock-bump/update:skills.lock" > "$after" 2>/dev/null || : > "$after"
    if [[ "$(lock_field_of "$after" ref)" == "$BUMP_REF_HEAD" ]]; then
        pass "degraded self-federating: the primary pin still advanced"
    else
        fail "degraded self-federating: the primary pin still advanced — got $(lock_field_of "$after" ref)"
    fi
    if [[ "$(lock_source_ref_of "$after" bumporg/agentskills)" == "$BUMP_REF_OLD" ]]; then
        pass "degraded self-federating: the self-named entry is carried through untouched"
    else
        fail "degraded self-federating: the self-named entry is carried through untouched — got $(lock_source_ref_of "$after" bumporg/agentskills)"
    fi

    rm -rf "$root" "$work"
}

# ── Test 8h3f: what the PR body's 20-line cap does and does not keep ──────
#
# The bumper builds `$fed_check_out` by CONCATENATING one
# `--check-current --only <registry>` report per drifted source and slices it
# with `sed -n '/^FAILED:/,$p' | head -20` into a PR body. That slice is a NEW
# consumer of the cap and inherits no guarantee from the unscoped one beside
# it: a scoped stream carries no primary block at all, so "the primary's
# headline and command survive" is not merely unhelpful there, it is about a
# block that is not present.
#
# So the comment beside that slice states a bounded claim rather than an
# absolute, and this test is what makes the claim checkable — it runs the exact
# pipeline over a synthetic stream and measures the SLICED output, which is the
# only thing a reader of the PR body ever sees.
test_bump_pr_body_slice_arithmetic() {
    echo ""
    echo "=== Test: the PR body's 20-line cap over a concatenated scoped stream ==="

    # THE PIPELINE THIS RUNS IS THE LIBRARY'S OWN, called, not copied. It used
    # to be half derived and half re-typed: `head -20` was grepped for out of
    # the library, but the `sed -n '/^FAILED:/,$p'` range beside it was a hand
    # copy — so changing the library's range to, say, `/^FAILED:/,+5p` left
    # this test happily measuring the old pipeline under a comment claiming it
    # ran the current one. `failed_slice` is now the one definition of both
    # halves, and this sources the library and calls it.
    local claims="$REPO_ROOT/scripts/lib/bump-pr-claims.sh"
    if grep -qF -- '$(failed_slice <<< "$fed_check_out"' "$claims"; then
        pass "slice: the PR body's federated evidence is sliced by failed_slice"
    else
        fail "slice: the PR body's federated evidence is sliced by failed_slice"
        return
    fi

    # THE CAP IS MEASURED off that function rather than read off its source,
    # so nothing here can keep asserting a number the code stopped using — and
    # the fixture below is built FROM the measurement, so the arithmetic under
    # test is the arithmetic the library actually performs.
    local cap="" i
    cap=$( { echo "FAILED: probe"; seq 1 200 | sed 's/^/  - line /'; } \
           | ( source "$claims" && failed_slice ) | wc -l ) || cap=""
    if [[ "$cap" =~ ^[0-9]+$ ]] && [[ "$cap" -ge 4 ]]; then
        pass "slice: failed_slice caps a report at $cap lines"
    else
        fail "slice: failed_slice caps a report at a countable number of lines — got '$cap'"
        return
    fi
    # AND THE COMMENT BESIDE IT STATES THAT SAME ARITHMETIC. It names three
    # numbers a reader would otherwise have to take on trust; all three are
    # derived from the one measurement above.
    if python3 -c '
import re, sys
cap = int(sys.argv[2])
text = re.sub(r"(?m)^[ \t]*#[ \t]?", "", open(sys.argv[1], encoding="utf-8").read())
text = re.sub(r"\s+", " ", text)
m = re.search(r"a first source with (\d+) difference lines puts the second "
              r"source.s headline on line (\d+) and its command on line (\d+)", text)
if not m:
    sys.exit("the comment beside the slice no longer states the arithmetic")
diffs, headline, command = (int(g) for g in m.groups())
if (diffs, headline, command) != (cap - 3, cap, cap + 1):
    sys.exit("the comment says %d/%d/%d; a cap of %d makes it %d/%d/%d"
             % (diffs, headline, command, cap, cap - 3, cap, cap + 1))
' "$claims" "$cap" 2>"$TEST_DIR/slice-arith.err"; then
        pass "slice: and the comment beside it states that cap's own arithmetic"
    else
        fail "slice: and the comment beside it states that cap's own arithmetic — $(cat "$TEST_DIR/slice-arith.err")"
    fi

    local stream="$TEST_DIR/fedslice.in" sliced="$TEST_DIR/fedslice.out"
    # Two scoped blocks, each 1 headline + 1 remediation + its differences.
    # `cap - 3` differences in the first is what puts the second block's
    # headline on the LAST line kept and its command one past the cap:
    # 1 + 1 + (cap - 3) = cap - 1.
    {
        echo "FAILED: org/src-a's bundles have moved on since aaaaaaa, which skills.lock still pins for it."
        echo "  python3 scripts/generate_skills_lock.py --repin --ref bbbbbbb --repin-source 'org/src-a@'"
        for i in $(seq 1 $((cap - 3))); do
            echo "  - changed: 'src-a/skill-$i' differs from its content at aaaaaaa"
        done
        echo "FAILED: org/src-b's bundles have moved on since ccccccc, which skills.lock still pins for it."
        echo "  python3 scripts/generate_skills_lock.py --repin --ref bbbbbbb --repin-source 'org/src-b@'"
        echo "  - changed: 'src-b/skill-1' differs from its content at ccccccc"
    } > "$stream"
    ( source "$claims" && failed_slice ) < "$stream" > "$sliced" || :
    if [[ "$(wc -l < "$sliced")" -eq "$cap" ]]; then
        pass "slice: the fixture is long enough that the cap is what truncates it"
    else
        fail "slice: the fixture is long enough that the cap is what truncates it — kept $(wc -l < "$sliced") of $cap"
    fi

    # WHAT HOLDS: the first block's headline is line 1 and its command line 2,
    # whichever block is first, so the pair a reader needs most cannot be split.
    if [[ "$(sed -n '1p' "$sliced")" == FAILED:\ org/src-a* ]]; then
        pass "slice: the first block's headline is the first line kept"
    else
        fail "slice: the first block's headline is the first line kept — got '$(sed -n '1p' "$sliced")'"
    fi
    if grep -qF -- "--repin-source 'org/src-a@'" <<< "$(sed -n '2p' "$sliced")"; then
        pass "slice: and its command is the line under it"
    else
        fail "slice: and its command is the line under it — got '$(sed -n '2p' "$sliced")'"
    fi

    # WHAT DOES NOT HOLD, and is stated rather than promised away: a LATER
    # block can keep its headline and lose the command beneath it.
    if [[ "$(sed -n "${cap}p" "$sliced")" == FAILED:\ org/src-b* ]]; then
        pass "slice: a later block's headline can be the last line kept"
    else
        fail "slice: a later block's headline can be the last line kept — line $cap is '$(sed -n "${cap}p" "$sliced")'"
    fi
    assert_not_contains "$sliced" "--repin-source 'org/src-b@'" \
        "slice: while its command falls outside the cap"

    # And the comment beside the slice must say the same. TWO HALVES, because
    # only the pair is checkable: the bound has to be STATED, and the absolute
    # has to be ABSENT. A lone `assert_not_contains` passes just as well on a
    # file with no comment at all.
    #
    # THE NEEDLE IS THE WORDING THAT EXISTS, not a paraphrase of it. The
    # previous version of this guard forbade "can never separate a headline
    # from the command" — a phrase that had never appeared in the file it
    # grepped, in either version, so it was a green light wired to nothing:
    # 0 matches before the fix and 0 after. The sentence a reader would
    # actually carry over is agentskills' own, beside its own report, which
    # reads "must never separate a headline from the command that fixes it" —
    # so that is what this forbids, in the shortest form of it. Through
    # `assert_prose_*`, so the wrap the sentence lands on when it is pasted
    # into a comment block cannot hide it: measured, pasting it back into the
    # library across two comment lines reds this, and `assert_not_contains`
    # did not.
    assert_prose_contains "$claims" "a later block can lose its command to the cap" \
        "slice: the comment beside the slice states the bound this test measured"
    assert_prose_omits "$claims" "never separate a headline from the command" \
        "slice: and does not restate the absolute that belongs to the other stream"
}

# ── Test 8h3g: every sentence a bump PR can carry, over every run state ───
#
# THE DEFECT CLASS THIS LANE EXISTS FOR, and it is one defect found five
# separate times by five separate readers: a sentence written for one shape of
# run is left unbranched when a second shape becomes reachable, and then
# asserts something that run never established. A degraded body said each
# source "answered that its bundles have not moved" from a generator with no
# way to ask; the paragraph beside it explained a `--repin-source` refusal by a
# generator that had no `--repin-source`; a federated-only header said a source
# moved "and nothing else" further up the same body than a block saying one
# moved "too"; a body pointed at "the ref listed for it below" with nothing listed
# below; and a body called a partial command "the whole of it".
#
# scripts/lib/bump-pr-claims.sh makes the omission a construction error at
# runtime. This is what makes the claim CHECKABLE, and it is deliberately not
# a second copy of that file's condition table — a test that restates the
# conditions passes whenever the conditions are wrong in both places. What it
# asserts instead are PROHIBITIONS derived from the run state itself: a run
# with no scoped flags cannot name one, a run that held the pin cannot
# announce a move, a body that points below must have something below.
#
# THE SENTENCE LIST IS NOT WRITTEN HERE. It is read off the library — every
# `claim_text_*` it defines — and every one of them has to be reachable from
# some cell of the cross product, so a sentence nothing can emit is a failure
# too. And every cell's artifacts are checked to be EXACTLY their emitted
# claims plus whitespace, which is what stops a sentence being typed straight
# into the composer where no condition governs it.
#
# The cross product is 4 x 7 x 2:
#   PRIMARY   drifted (content) | current, either reason it can still open a
#             PR (format, federated) | unanswerable
#   FEDERATED advanced | unchanged | not asked | self-named | self-named
#             ALONGSIDE a source that MOVED | self-named alongside one that
#             did NOT | no sources at all
#   SCOPED    the generator has --check-current --only and --repin-source, or
#             it does not
# The brief's three primary states and four federated ones are all in there;
# the three extra federated columns are the ones the four cannot reach — a
# lock with no `sources` at all, and the two shapes that name the primary as a
# source BESIDE a real one, which are the only ones where the digest paragraph
# both points at a list and has an entry that is not in it.
#
# THE QUIET ONE IS THERE BECAUSE ITS ABSENCE HID A DEFECT. Until this column
# existed the grid never rendered `federated_unchanged` beside a self-named
# entry — self_named_plus always drifts its real source — so that claim's
# unrestricted "Each was put its own question" was denied two paragraphs
# further down a body no cell ever built.
#
# Thirty of the fifty-six are impossible, and each one is asserted to be
# impossible rather than skipped: an unanswerable primary must produce no
# artifact at all, an advance is unreachable without the flag that makes one,
# and `federated` as a reason means a source moved.
test_bump_pr_claims_cross_product() {
    echo ""
    echo "=== Test: every sentence a bump PR can carry, over every run state ==="

    local claims="$REPO_ROOT/scripts/lib/bump-pr-claims.sh"
    local dir="$TEST_DIR/claimgrid"
    rm -rf "$dir"; mkdir -p "$dir/fx"

    # Two locks, because the "advanced" line prints an arrow and the two ends
    # of it come from two different files: the lock as the default branch has
    # it, and the re-pinned working copy. One file cannot show a move.
    cat > "$dir/fx/before.lock" <<'LOCK'
{"registry": "org/reg", "ref": "aaaaaaaaaaaaaaaa", "bundles": ["b"],
 "sources": [{"registry": "org/src", "ref": "1111111111111111", "bundles": ["c"]}],
 "skills": {}, "generated_from": "aaaaaaaaaaaaaaaa"}
LOCK
    cat > "$dir/fx/skills.lock" <<'LOCK'
{"registry": "org/reg", "ref": "bbbbbbbbbbbbbbbb", "bundles": ["b"],
 "sources": [{"registry": "org/src", "ref": "2222222222222222", "bundles": ["c"]}],
 "skills": {}, "generated_from": "bbbbbbbbbbbbbbbb"}
LOCK

    # The renderer. A subshell per cell, so no cell can inherit another's
    # state — which is the failure mode a single long-lived shell would hide.
    cat > "$dir/render.sh" <<'RENDER'
#!/usr/bin/env bash
set -uo pipefail
source "$1"
fx="$2"; out="$3"; reason="$4"; fed="$5"; scoped="$6"
mkdir -p "$out"
cd "$fx" || exit 9
LOCK_REL_PATH="skills.lock"
LOCK_DIGEST_SHAPE='sha256:<64 hex>'
BUMPER_SOURCE='`_agent-guidance`'
primary_registry="org/reg"
old_ref="aaaaaaaaaaaaaaaa"
if [[ "$reason" == "content" ]]; then new_ref="bbbbbbbbbbbbbbbb"; else new_ref="$old_ref"; fi
repin_reason="$reason"
FED_ADVANCE_AVAILABLE="$scoped"
case "$fed" in
    advanced)        source_registries=("org/src"); fed_drifted_regs=("org/src") ;;
    self_named)      source_registries=("org/reg"); fed_drifted_regs=() ;;
    self_named_plus) source_registries=("org/reg" "org/src"); fed_drifted_regs=("org/src") ;;
    self_named_quiet) source_registries=("org/reg" "org/src"); fed_drifted_regs=() ;;
    none)            source_registries=(); fed_drifted_regs=() ;;
    *)               source_registries=("org/src"); fed_drifted_regs=() ;;
esac
check_out="FAILED: org/reg's bundles have moved on since aaaaaaa, which skills.lock still pins for it."
format_out="FAILED: skills.lock stores 1 digest that is not sha256:<64 lowercase hex>."
fed_check_out="FAILED: org/src's bundles have moved on since 1111111, which skills.lock still pins for it."
primary_lock="$fx/copy.primary.lock"
lock_file="$fx/before.lock"
repin_source_flags_shown="--repin-source 'org/src@'"
repin_source_repo_shown="--source-repo 'org/src=<a clone of it>'"
status=0
compose_bump_artifacts || status=1
printf '%s' "$status" > "$out/status"
printf '%s' "$CLAIM_ERRORS" > "$out/errors"
: > "$out/claims"
for id in ${EMITTED_CLAIMS[@]+"${EMITTED_CLAIMS[@]}"}; do printf '%s\n' "$id" >> "$out/claims"; done
[[ "$status" == "0" ]] || exit 0
printf '%s' "$PR_TITLE" > "$out/title"
printf '%s' "$PR_BODY" > "$out/body"
printf '%s\n%s\n%s\n%s' "$PR_TITLE" "$COMMIT_SUBJECT" "$COMMIT_BODY" "$PR_BODY" > "$out/all"
# The log() line this run would print, which is an artifact of the run like
# the four above and is governed with them below. It is not part of $out/all,
# because that file is what the CLOSURE check accounts for claim by claim and
# this sentence is not emitted through `emit` — see the library's note on why.
: > "$out/log"
case "$fed" in
    self_named|self_named_plus)
        self_named_log_line "$scoped" "$LOCK_REL_PATH" "$primary_registry" > "$out/log" ;;
esac
: > "$out/parts.bin"
for text in ${EMITTED_TEXT[@]+"${EMITTED_TEXT[@]}"}; do printf '%s\0' "$text" >> "$out/parts.bin"; done
exit 0
RENDER

    local reason fed scoped cell out expect
    local r_reach="" r_scoped="" r_held="" r_advance="" r_below="" r_only="" r_whole="" r_label="" r_closure="" r_each=""
    # Read once, out of the library, for the degraded-sentence check below.
    local pair_needle
    pair_needle="$(scoped_flag_pair)"
    local rendered=0 refused=0
    : > "$dir/all-claims.txt"

    for reason in content format federated unanswerable; do
        for fed in advanced unchanged not_asked self_named self_named_plus self_named_quiet none; do
            for scoped in true false; do
                cell="$reason-$fed-$scoped"
                out="$dir/out/$cell"
                bash "$dir/render.sh" "$claims" "$dir/fx" "$out" "$reason" "$fed" "$scoped"

                # WHAT THIS CELL IS ALLOWED TO PRODUCE, declared before it is
                # read. `expect=refuse` is a positive claim about the script —
                # that this run state cannot yield an artifact — and it is
                # checked, not assumed.
                local advancing=false
                [[ "$fed" == "advanced" || "$fed" == "self_named_plus" ]] && advancing=true

                expect=render
                if [[ "$reason" == "unanswerable" ]]; then
                    expect=refuse           # nothing establishes anything
                elif [[ "$reason" == "federated" ]] && ! $advancing; then
                    expect=refuse           # `federated` IS a source advance
                elif $advancing && [[ "$scoped" != "true" ]]; then
                    expect=refuse           # no flag to advance a pin with
                fi

                if [[ "$expect" == "refuse" ]]; then
                    [[ "$(cat "$out/status")" == "1" ]] || r_reach="$r_reach $cell(rendered)"
                    refused=$((refused + 1))
                    continue
                fi
                if [[ "$(cat "$out/status")" != "0" ]]; then
                    r_reach="$r_reach $cell(refused: $(cat "$out/errors"))"
                    continue
                fi
                rendered=$((rendered + 1))
                cat "$out/claims" >> "$dir/all-claims.txt"

                # FLATTENED, because every needle below guards a sentence
                # in a hard-wrapped artifact and `grep -F` matches inside one
                # line. Measured on this branch's own artifact needle: a
                # forbidden sentence that lands across a wrap matches nothing
                # and the guard passes silently. `$out/body` stays unflattened
                # for the one check that is about line structure — whether the
                # body has a `- \`` bullet.
                tr -s '[:space:]' ' ' < "$out/all" > "$out/flat"
                # Prose only: a fenced block is the generator's own verdict,
                # quoted verbatim, and quoting evidence is not asserting it.
                awk '/^```/{f=!f; next} !f' "$out/all" > "$out/prose"
                # THE LOG LINE IS PROSE TOO, appended as its own paragraph.
                # Nothing governed log() output until this: the same run's
                # body was split into a scoped claim and a degraded one while
                # the log kept printing the scoped reason, so two artifacts of
                # one run said opposite things about whether a question could
                # have been put at all.
                if [[ -s "$out/log" ]]; then
                    printf '\n\n' >> "$out/prose"
                    cat "$out/log" >> "$out/prose"
                fi

                # A degraded run may name a scoped flag ONLY to report that
                # the generator did not carry it — never to describe what one
                # did or refused, because the flags were not there to do or
                # refuse anything. That much this check always said. Two
                # things it did not, both measured:
                #
                #   * The exemption was the two-word substring "has no"
                #     ANYWHERE IN THE PARAGRAPH, and the one paragraph that
                #     legitimately carries those words is the disclosure — so
                #     the single place the shipped defect lived was the place
                #     the prohibition could not reach. Rewriting
                #     self_named_degraded to say "`--repin-source` refuses it
                #     outright. This generator has no scoped flag." put the
                #     production defect back and the full gate stayed at
                #     681 passed / 0 failed.
                #   * "has no <one flag>" is not a thing a degraded run
                #     established. FED_ADVANCE_AVAILABLE goes false when
                #     EITHER probe fails, so the only absence in evidence is
                #     the absence of the PAIR; a body naming one conjunct is
                #     false on the generator that carries the other.
                #
                #   * A sentence can be false about the flags without naming
                #     one. The degraded log line said "this run's generator
                #     cannot put a per-source question about any source" —
                #     no flag spelled, and false of a generator that carries
                #     `--only`. Reverting that line to its old wording left
                #     this lane at 13 passed / 0 failed while the flag-naming
                #     rule below was already live, so the trigger reads
                #     capability ascription too, not just flag spellings.
                #
                # So: sentence by sentence, and the exemption is the library's
                # own ${SCOPED_FLAG_PAIR} — READ OUT OF THE LIBRARY, not
                # re-typed here, and matched whitespace-normalised so the wrap
                # a claim happens to take cannot decide whether it is checked.
                if [[ "$scoped" != "true" ]] && ! SCOPED_PAIR="$pair_needle" python3 -c '
import os, re, sys
prose = open(sys.argv[1], encoding="utf-8").read()
pair = " ".join(os.environ["SCOPED_PAIR"].split())
if not pair:
    sys.exit("the library exports no SCOPED_FLAG_PAIR to check against")
# TWO WAYS A SENTENCE REACHES THIS. Naming a flag is the obvious one. The
# other is ascribing a CAPABILITY to the generator without naming any flag at
# all, which is the shape the log line shipped in — "this run\u0027s generator
# cannot put a per-source question about any source" names nothing, and is
# false of a generator carrying --only and not --repin-source. A needle list
# of flag spellings could not see it.
MODAL = r"\b(can|cannot|could|able|unable|has|have|had|carries|carried|lacks|supports)\b"
for sentence in re.split(r"(?<=[.!?:])\s+", prose):
    flat = " ".join(sentence.split())
    names_flag = re.search(r"--only|--repin-source|scoped question", flat)
    rates_generator = "generator" in flat and re.search(MODAL, flat)
    if not (names_flag or rates_generator):
        continue
    # The pair, in this sentence, reported as ABSENT in this sentence. Both
    # halves matter: the pair alone would let "<pair> refused that name"
    # through, and an absence word alone is what the paragraph-wide "has no"
    # already was.
    if pair not in flat:
        sys.exit(1)
    if not re.search(r"\b(has|have|had|carries|carried) (no|neither)\b", flat):
        sys.exit(1)
' "$out/prose"; then
                    r_scoped="$r_scoped $cell"
                fi

                # A held pin is never announced as a move.
                if [[ "$reason" != "content" ]] \
                   && grep -qF -- 'newly pinned commit' "$out/flat"; then
                    r_held="$r_held $cell"
                fi
                if [[ "$reason" != "content" ]] \
                   && grep -qF -- 'bbbbbbb' "$out/flat"; then
                    r_held="$r_held $cell(new ref)"
                fi

                # Nothing moved that this run did not move.
                if ! $advancing \
                   && grep -qE -- 'moved too|\*\*advanced\*\*|advance its federated pin' "$out/flat"; then
                    r_advance="$r_advance $cell"
                fi

                # A pointer at the body's own list has something in that
                # list. THE POINTER IS RECOGNISED BY ITS SHAPE, not by a list
                # of the phrasings that existed when this was written: the
                # three alternatives here were a hand copy of the body's
                # wordings, and when two more pointers were added ("Each
                # source listed below…", "Each one listed below…") neither
                # matched any of them, so two of the four pointers in the
                # artifact sat outside the guard that exists for them.
                #
                # The one form that must NOT count is the NEGATIVE pointer —
                # "that entry is not listed below", which a body prints
                # precisely when there is nothing below — so it is removed
                # before the positive one is looked for. That needle is
                # hand-typed and it is the safe direction to be wrong in: if
                # the negation is reworded this guard goes RED over a body
                # that is fine, rather than green over one that is not.
                if python3 -c '
import re, sys
flat = open(sys.argv[1], encoding="utf-8").read()
flat = re.sub(r"\b(is|are) not listed below", "", flat)
sys.exit(0 if re.search(r"listed below|below was checked", flat) else 1)
' "$out/flat" && ! grep -qE '^- `' "$out/body"; then
                    r_below="$r_below $cell"
                fi

                # "and nothing else" is exclusive or it is not a claim.
                if grep -qF -- 'and nothing else' "$out/flat" \
                   && grep -qF -- 'moved too' "$out/flat"; then
                    r_only="$r_only $cell"
                fi
                if grep -qF -- 'SHAPE stored in `skills.lock`, and nothing' "$out/flat" \
                   && grep -qF -- '**advanced**' "$out/flat"; then
                    r_only="$r_only $cell(shape)"
                fi

                # No artifact presents a partial command as a complete one.
                # The body quotes the generator's remediation line for the
                # whole, and names its own additions as additions.
                if grep -qF -- 'whole of it' "$out/flat"; then
                    r_whole="$r_whole $cell"
                fi
                if [[ "$reason" == "format" ]] && $advancing \
                   && ! grep -qF -- "--repin-source 'org/src@'" "$out/flat"; then
                    r_whole="$r_whole $cell(addition unnamed)"
                fi
                # AND NAMED COMPLETELY. A command the body says this run RAN
                # has to be one that would run: the generator looks for a
                # source's clone at the sibling ../<repo-name> beside --repo
                # and stops at "no checkout at ..." when it is not there, so a
                # reconstruction carrying the --repin-source additions alone
                # can fail before it writes. Incomplete rather than false,
                # which is the shape the "whole of it" fix left behind.
                #
                # Keyed off the CELL, not off a sentence. The first cut
                # selected cells by grepping the artifact for "half of the
                # command this PR ran" — a hand copy of the claim's own
                # opening, so rewording the claim silently deselected every
                # cell and the check passed over a body that named no
                # --source-repo at all. Measured: rewording that clause to
                # "SHAPE half of what this PR ran" and dropping the flag left
                # test_bump_pr_claims_cross_product at 13 passed / 0 failed.
                # `format` + advancing is the condition claim_holds uses to
                # emit repro_shape_half, and it is what the sibling check
                # above already keys on.
                if [[ "$reason" == "format" ]] && $advancing \
                   && ! grep -qF -- "--source-repo 'org/src=" "$out/flat"; then
                    r_whole="$r_whole $cell(addition incomplete)"
                fi

                # A QUANTIFIED CLAIM ABOUT SCOPED QUESTIONS NAMES THE LIST IT
                # RANGES OVER. Same shape as "and nothing else" beside "moved
                # too", one axis over: the federated header used to say EVERY
                # source was put its own scoped question, and two paragraphs
                # later the same body said one was not — the self-named entry,
                # which gets no line in that list and no question.
                #
                # THE RESTRICTION IS THE INVARIANT, which is why this no longer
                # pairs a quantifier against a denial. Matching the denial too
                # made the guard an approximation of one sentence: it read
                # `(Each|Every)( [a-z]+)? was put its`, whose single optional
                # word cannot span "Each federated source was put its OWN" —
                # measured, that restores the unrestricted claim over a body
                # that still says one source's question "was not put", at
                # 13 passed / 0 failed here and 681 passed / 0 failed on the
                # full gate. It also saw none of the three OTHER unrestricted
                # quantifiers in the same artifact ("was asked once per source",
                # "One scoped question per source"), which are denied by that
                # same paragraph in exactly the same way.
                #
                # So the rule is the property itself: a sentence that puts a
                # universal quantifier beside the scoped question has to say
                # WHICH sources — and the only list a reader can check is the
                # one printed below it, which r_below above proves is there.
                if ! python3 -c '
import re, sys
prose = open(sys.argv[1], encoding="utf-8").read()
for sentence in re.split(r"(?<=[.!?:])\s+", prose):
    flat = " ".join(sentence.split())
    if not re.search(r"--check-current --only|scoped question", flat):
        continue
    if not re.search(r"\b(each|every)\b|\b(once|one)\b[^.]{0,40}\bper[- ]source\b",
                     flat, re.I):
        continue
    if "listed below" not in flat:
        sys.exit(1)
' "$out/prose"; then
                    r_each="$r_each $cell"
                fi

                # `unchanged` is a VERDICT and `not asked` is the absence of
                # one. The lock and the drift set are identical in those two
                # columns; what separates them is whether anything could ask.
                if [[ "$fed" == "unchanged" || "$fed" == "not_asked" || "$fed" == "self_named_quiet" ]]; then
                    if [[ "$scoped" == "true" ]]; then
                        grep -qF -- '**unchanged**' "$out/body" || r_label="$r_label $cell"
                        grep -qF -- '**not asked**' "$out/body" && r_label="$r_label $cell(both)"
                    else
                        grep -qF -- '**not asked**' "$out/body" || r_label="$r_label $cell"
                        grep -qF -- '**unchanged**' "$out/body" && r_label="$r_label $cell(both)"
                    fi
                fi

                # CLOSURE. Every artifact is exactly the claims it emitted,
                # in order, with whitespace between them — so a sentence typed
                # into the composer, where no condition governs it, is a
                # residue this cannot explain.
                if ! python3 -c '
import sys
whole = open(sys.argv[1], encoding="utf-8").read()
parts = open(sys.argv[2], "rb").read().decode("utf-8").split("\0")[:-1]
cursor = 0
for part in parts:
    found = whole.find(part, cursor)
    if found < 0:
        sys.exit("claim not found in artifact: %r" % part[:60])
    whole = whole[:found] + whole[found + len(part):]
    cursor = found
if whole.strip():
    sys.exit("text outside every claim: %r" % whole.strip()[:120])
' "$out/all" "$out/parts.bin" 2>"$out/closure.err"; then
                    r_closure="$r_closure $cell($(head -1 "$out/closure.err"))"
                fi
            done
        done
    done

    if [[ $rendered -eq 26 && $refused -eq 30 ]]; then
        pass "claims: the cross product is 56 cells — 26 that produce an artifact, 30 that cannot"
    else
        fail "claims: the cross product is 56 cells — 26 that produce an artifact, 30 that cannot (got $rendered / $refused)"
    fi
    [[ -z "$r_reach" ]] && pass "claims: every cell produces exactly what its run state allows" \
        || fail "claims: every cell produces exactly what its run state allows —$r_reach"
    [[ -z "$r_scoped" ]] && pass "claims: a run with no scoped flags never names one" \
        || fail "claims: a run with no scoped flags never names one —$r_scoped"
    [[ -z "$r_held" ]] && pass "claims: a held pin is never announced as a move" \
        || fail "claims: a held pin is never announced as a move —$r_held"
    [[ -z "$r_advance" ]] && pass "claims: nothing says a source advanced on a run where none did" \
        || fail "claims: nothing says a source advanced on a run where none did —$r_advance"
    [[ -z "$r_below" ]] && pass "claims: a body that points below has something listed below" \
        || fail "claims: a body that points below has something listed below —$r_below"
    [[ -z "$r_only" ]] && pass "claims: 'and nothing else' never sits beside a second thing moving" \
        || fail "claims: 'and nothing else' never sits beside a second thing moving —$r_only"
    [[ -z "$r_whole" ]] && pass "claims: no body presents a partial command as the whole one" \
        || fail "claims: no body presents a partial command as the whole one —$r_whole"
    [[ -z "$r_each" ]] && pass "claims: a quantified claim about scoped questions names the list it ranges over" \
        || fail "claims: a quantified claim about scoped questions names the list it ranges over —$r_each"
    [[ -z "$r_label" ]] && pass "claims: a source is labelled with the verdict this run actually got" \
        || fail "claims: a source is labelled with the verdict this run actually got —$r_label"
    [[ -z "$r_closure" ]] && pass "claims: every artifact is exactly its claims plus whitespace" \
        || fail "claims: every artifact is exactly its claims plus whitespace —$r_closure"

    # NO DEAD SENTENCES. The registry is read off the library, so this cannot
    # drift into a hand-maintained list: a claim no cell can reach is either
    # unreachable prose or a condition nobody can satisfy, and both are the
    # thing this lane is about.
    local registered emitted missing
    # `|| true` because this suite runs under `set -e`: a grep that matched
    # nothing would take the WHOLE run down with it, Results line and all,
    # instead of failing the vacuity guard below — which is the isolation
    # failure this file's own header is about.
    registered=$(grep -oE '^claim_text_[a-z0-9_]+' "$claims" | sed 's/^claim_text_//' | sort -u || true)
    emitted=$(sort -u "$dir/all-claims.txt" | grep -v '^$' || true)
    missing=$(comm -23 <(printf '%s\n' "$registered") <(printf '%s\n' "$emitted") | tr '\n' ' ')
    if [[ -z "${missing// /}" ]]; then
        pass "claims: every sentence the library registers is reachable from some run"
    else
        fail "claims: every sentence the library registers is reachable from some run — never emitted: $missing"
    fi
    if [[ $(printf '%s\n' "$registered" | wc -l) -ge 40 ]]; then
        pass "claims: the sentence list came off the library, and it is not empty"
    else
        fail "claims: the sentence list came off the library, and it is not empty"
    fi
}

# ── Test 8h3e: a scoped question that cannot be answered at all ───────────
#
# The branch between "this source drifted" and "acting on a question nobody
# answered", and it had no fixture: every other federated test drives either
# the FAILED path or the degrade. It is the landing place for a bad --only
# value, an unreadable source checkout, and any refusal the generator makes
# about the lock — so a run that read a non-zero exit as drift would re-pin a
# consumer on the strength of an error.
#
# Reached here through an unresolvable SOURCE pin, which is the shape that
# needs no cooperation from the generator's argument handling: the scoped
# question is well formed and the answer is ERROR: rather than FAILED:.
test_bump_scoped_question_unanswerable() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a scoped question with no answer) ==="

    local root="$TEST_DIR/bare-scopederr"
    local work="$TEST_DIR/work/bumporg-repo-scopederr"
    rm -rf "$root" "$work"
    mkdir -p "$root/bumporg_repo-scopederr" "$work"
    git init --bare --initial-branch=main "$root/bumporg_repo-scopederr" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$root/bumporg_repo-scopederr"
    echo "# repo-scopederr" > README.md
    # Seeded and FILLED at a real source ref, then the source pin is rewritten
    # to a commit no repository has. The digests stay the true ones, so the
    # only thing wrong with this fixture is the one thing under test — the same
    # discipline strip_digest_labels uses.
    seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_OLD" "$BUMP_SRC_REF"
    python3 -c '
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    doc = json.load(handle)
doc["sources"][0]["ref"] = "7777777777777777777777777777777777777777"
with open(path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
' skills.lock
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"

    BUMP_BARE_DIR_FOR_RUN="$root" run_bump "$TEST_DIR/bump-scopederr.txt"
    unset BUMP_BARE_DIR_FOR_RUN
    local log="$TEST_DIR/bump-scopederr.txt"

    assert_contains "$log" "could not decide whether skills.lock's bumporg/cms-platform source is current" \
        "unanswerable scope: reported as a failure to decide, naming the source it was about"
    assert_contains "$log" "cannot resolve ref" \
        "unanswerable scope: the generator's own reason survives into the report"
    assert_contains "$log" "1 failed" \
        "unanswerable scope: counted, so the scheduled run goes red"
    assert_not_contains "$log" "federated sources whose pins this re-pin advances" \
        "unanswerable scope: a non-zero exit is not read as drift"
    # The PRIMARY is stale here, so a run that fell through would have plenty
    # to re-pin. Nothing does.
    if [[ -z "$(git -C "$root/bumporg_repo-scopederr" rev-parse --verify -q \
                refs/heads/skills-lock-bump/update 2>/dev/null || true)" ]]; then
        pass "unanswerable scope: nothing is written, though the primary is stale"
    else
        fail "unanswerable scope: nothing is written, though the primary is stale"
    fi

    rm -rf "$root" "$work"
}

# ── Test 8h3d: ONE of the two scoped flags missing, either one ────────────
#
# The script's own comment makes "BOTH, OR NEITHER" load-bearing — a scoped
# question with no way to act on it is the report-only behaviour this script
# already had, and acting without the scoped question is the fleet-wide
# over-advance ADR 0009 exists to prevent. Stripping both flags together, which
# is the only shape the degrade test had, cannot exercise that: a run that set
# FED_ADVANCE_AVAILABLE per flag would degrade identically. These two are the
# shapes that would notice, and they are reachable — the flags land in
# agentskills in one PR but a rollback, a partial revert or a pinned checkout
# can leave either alone.
test_bump_generator_with_one_scoped_flag() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (one scoped flag present, one missing) ==="

    # EACH STAND-IN IS NAMED IN FULL, never assembled from $which. An
    # interpolated path is a name no reader and no checker can resolve back to
    # a file: the stub inventory's "listed but never built" direction had to
    # skip any listed name sharing an interpolated prefix, which made
    # generator-missing-only.py and generator-missing-repin-source.py vouch
    # for each other. Measured before this: dropping repin-source from this
    # loop left its stand-in listed in the inventory, built by nothing, and
    # test_bump_stub_generator_parity at 12 passed / 0 failed.
    local spec which gen log
    for spec in "only=$TEST_DIR/generator-missing-only.py" \
                "repin-source=$TEST_DIR/generator-missing-repin-source.py"; do
        which="${spec%%=*}"
        gen="${spec#*=}"
        write_stub_generator "$gen"
        if ! python3 "$TEST_DIR/strip-scoped-flags.py" "$gen" "$which"; then
            fail "one flag ($which): could not strip just that flag"
            continue
        fi
        # The fixture is a PARTIAL strip or it is nothing: the named flag gone
        # from --help, the other one still there. Without both halves this
        # would be the both-missing test wearing a different name.
        if python3 "$gen" --help 2>&1 | grep -q -- "--$which"; then
            fail "one flag ($which): --$which is still advertised by the stripped stub"
        else
            pass "one flag ($which): --$which is gone from the stripped stub"
        fi
        local kept; [[ "$which" == "only" ]] && kept="--repin-source" || kept="--only"
        if python3 "$gen" --help 2>&1 | grep -q -- "$kept"; then
            pass "one flag ($which): $kept is still there, so this is a partial strip"
        else
            fail "one flag ($which): $kept is still there, so this is a partial strip"
        fi

        log="$TEST_DIR/bump-onemissing-$which.txt"
        BUMP_GENERATOR_FOR_RUN="$gen" run_bump "$log" --dry-run
        unset BUMP_GENERATOR_FOR_RUN

        # Exactly ONE probe warning: the pair degrades together, but each probe
        # reports only its own flag. Both warnings would mean the probes are
        # not independent; none would mean the gate armed on half a capability.
        local warnings
        warnings=$(grep -c "this run cannot" "$log" || true)
        if [[ "$warnings" == "1" ]]; then
            pass "one flag ($which): exactly one probe reports a shortfall"
        else
            fail "one flag ($which): exactly one probe reports a shortfall — got $warnings"
        fi
        # DEGRADE, never ground the run. Exit 2 is "refused before any repo was
        # touched", which is what a hard probe would produce; this fleet
        # fixture always ends 1 because repo-error cannot be assessed.
        if [[ $BUMP_EXIT -ne 2 ]]; then
            pass "one flag ($which): the run is not grounded"
        else
            fail "one flag ($which): the run is not grounded (exit 2)"
        fi
        assert_not_contains "$log" "federated sources whose pins this re-pin advances" \
            "one flag ($which): no federated pin is advanced on half a capability"
        # And no argparse rejection reaches the log, which is what would happen
        # if the gate armed and then passed the flag that is missing.
        assert_not_contains "$log" "unrecognized arguments" \
            "one flag ($which): the missing flag is never passed"

        # ── THE HALF-CAPABILITY RUN IS THE ONE THAT NOTICES a sentence
        #    written as if the gate were one flag. `FED_ADVANCE_AVAILABLE`
        #    goes false when EITHER probe fails, so on this generator every
        #    degraded sentence naming one conjunct as the reason is FALSE
        #    about the flag it names — and it contradicts this same run's
        #    annotation, which named the other one.
        #
        #    Measured before this lane asserted anything: with `--only`
        #    present and `--repin-source` stripped, the per-repo annotation
        #    read "this generator cannot say whether a FEDERATED source has
        #    moved with it" and the PR body of the same run read "The
        #    generator this run used has no `--check-current --only
        #    <registry>`". It had both.
        #
        #    The needle is the library's own SCOPED_FLAG_PAIR, read out of
        #    the file rather than re-typed, so a reworded pair cannot leave a
        #    guard matching a string nothing prints.
        local pair
        pair="$(scoped_flag_pair)"
        assert_prose_contains "$log" "has no $pair pair" \
            "one flag ($which): the degraded annotation names the PAIR, which is what this run established"
        assert_prose_omits "$log" "this generator cannot say whether a FEDERATED source has moved with it" \
            "one flag ($which): and not one conjunct it does not establish"
    done
}

# ── Test 8h4a: a flag whose name merely BEGINS --only is not --only ───────
#
# THE FALSE POSITIVE the capability probe exists to close, and it lands on this
# suite's own negative control. `grep -q -- '--only'` over --help matches any
# longer flag beginning with those characters, and argparse's default
# `allow_abbrev=True` then accepts `--only <registry>` as an unambiguous prefix
# abbreviation of that other flag — so the gate arms, the "scoped" question is
# silently an UNSCOPED one, and its FAILED: for a primary-only drift is read as
# source drift. The lock that must not move is the measurement: this fixture's
# source is content-CURRENT but pinned behind its own HEAD, so "left alone" and
# "advanced to HEAD" are different bytes.
test_bump_only_prefix_flag() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a flag that only looks like --only) ==="

    local root="$TEST_DIR/bare-onlypfx"
    local work="$TEST_DIR/work/bumporg-repo-onlypfx"
    rm -rf "$root" "$work"
    mkdir -p "$root/bumporg_repo-onlypfx" "$work"
    git init --bare --initial-branch=main "$root/bumporg_repo-onlypfx" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$root/bumporg_repo-onlypfx"
    echo "# repo-onlypfx" > README.md
    # repo-federated's shape: stale primary, source content-current and pinned
    # behind its own HEAD.
    seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_OLD" "$BUMP_SRC_CONTENT"
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"

    local gen="$TEST_DIR/generator-only-bundles.py"
    write_stub_generator "$gen"
    if ! python3 "$TEST_DIR/only-bundles-stub.py" "$gen"; then
        fail "only-prefix: could not build the --only-bundles stub"
        rm -rf "$root" "$work"
        return
    fi
    # The fixture's two load-bearing properties, verified before the run so a
    # PASS below cannot come from a stub that failed to be built.
    if python3 "$gen" --help 2>&1 | grep -q -- '--only'; then
        pass "only-prefix: --help still contains the characters a substring probe matched"
    else
        fail "only-prefix: --help still contains the characters a substring probe matched"
    fi
    if python3 "$gen" --only x --check-current -o "$work/skills.lock" 2>&1 \
       | grep -q -- 'unrecognized arguments'; then
        fail "only-prefix: argparse swallows --only as a prefix abbreviation"
    else
        pass "only-prefix: argparse swallows --only as a prefix abbreviation"
    fi

    BUMP_BARE_DIR_FOR_RUN="$root" BUMP_GENERATOR_FOR_RUN="$gen" \
        run_bump "$TEST_DIR/bump-onlypfx.txt"
    unset BUMP_BARE_DIR_FOR_RUN BUMP_GENERATOR_FOR_RUN
    local log="$TEST_DIR/bump-onlypfx.txt"

    assert_contains "$log" "has no '--check-current --only <REGISTRY>'" \
        "only-prefix: the missing gate primitive is announced, not inferred from a substring"
    assert_not_contains "$log" "federated sources whose pins this re-pin advances" \
        "only-prefix: no federated pin is advanced on a question that was never scoped"
    local after="$TEST_DIR/onlypfx-after.lock"
    git -C "$root/bumporg_repo-onlypfx" show \
        "refs/heads/skills-lock-bump/update:skills.lock" > "$after" 2>/dev/null || : > "$after"
    if [[ "$(lock_source_ref_of "$after" bumporg/cms-platform)" == "$BUMP_SRC_CONTENT" ]]; then
        pass "only-prefix: the federated pin is exactly where it was"
    else
        fail "only-prefix: the federated pin is exactly where it was — got $(lock_source_ref_of "$after" bumporg/cms-platform)"
    fi
    if [[ "$(lock_field_of "$after" ref)" == "$BUMP_REF_HEAD" ]]; then
        pass "only-prefix: the stale primary is still re-pinned, so the degrade cost nothing else"
    else
        fail "only-prefix: the stale primary is still re-pinned — got $(lock_field_of "$after" ref)"
    fi

    rm -rf "$root" "$work"
}

# ── Test 8h4b: a failure report quotes the line that says what went wrong ─
#
# argparse writes its `usage:` banner on the FIRST line and
# `<prog>: error: ...` on the LAST, so `head -1` on a rejected argument reports
# the one line of that output which says nothing — and a scheduled run then
# goes red every night with a message nobody can act on. Commit 6744fcf fixed
# exactly this in the sweep; the federated work reintroduced it on newly
# reachable paths, so the extraction is a helper now and every generator-output
# failure report goes through it.
#
# Driven through the PRIMARY currency question, which is the one path a
# generator can reject an argument on while still advertising `--repin` and so
# clearing this script's only hard probe.
test_bump_generator_error_line() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (which line of a rejection is reported) ==="

    local root="$TEST_DIR/bare-errline"
    local work="$TEST_DIR/work/bumporg-repo-errline"
    rm -rf "$root" "$work"
    mkdir -p "$root/bumporg_repo-errline" "$work"
    git init --bare --initial-branch=main "$root/bumporg_repo-errline" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$root/bumporg_repo-errline"
    echo "# repo-errline" > README.md
    seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_OLD"
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"

    # Advertises --repin, so the hard probe passes, and then rejects an
    # argument the way argparse does: banner first, reason last, on stderr.
    local gen="$TEST_DIR/generator-errline.py"
    cat > "$gen" <<'ERRLINE'
#!/usr/bin/env python3
"""--repin is advertised; --check-current rejects an argument, argparse-style."""
import sys

if "--help" in sys.argv:
    print("usage: gen.py [-h] [--check-current] [--check-format] [--repin]")
    sys.exit(0)
if "--check-current" in sys.argv:
    sys.stderr.write("usage: gen.py [-h] [--check-current] [--check-format] [--repin]\n")
    sys.stderr.write("gen.py: error: unrecognized arguments: --check-current\n")
    sys.exit(2)
sys.exit("this generator must never be asked for anything else")
ERRLINE

    BUMP_BARE_DIR_FOR_RUN="$root" BUMP_GENERATOR_FOR_RUN="$gen" \
        run_bump "$TEST_DIR/bump-errline.txt"
    unset BUMP_BARE_DIR_FOR_RUN BUMP_GENERATOR_FOR_RUN
    local log="$TEST_DIR/bump-errline.txt"

    assert_contains "$log" "could not decide whether skills.lock is current — gen.py: error: unrecognized arguments" \
        "error line: the reported line is the one that says what went wrong"
    assert_scoped_probe_warnings "$log" 2 \
        "error line: both soft federated probes degraded, one warning each"
    assert_not_contains "$log" "current — usage:" \
        "error line: the usage banner is not what the failure quotes"
    assert_contains "$log" "1 failed" \
        "error line: still a counted failure, so the scheduled run goes red"
    if [[ -z "$(git -C "$root/bumporg_repo-errline" rev-parse --verify -q \
                refs/heads/skills-lock-bump/update 2>/dev/null || true)" ]]; then
        pass "error line: nothing is re-pinned on an answer nobody got"
    else
        fail "error line: nothing is re-pinned on an answer nobody got"
    fi

    rm -rf "$root" "$work"
}

# ── Test 8h6b: which HALF a failure names, when either could be blamed ────
#
# A failure report that names the wrong half sends its reader to the wrong
# repository, and on a degraded run the two halves are answered by the SAME
# generator call — so the report is a choice, not an observation.
#
# THE DEFECT THIS HOLDS SHUT. The degraded federated check was moved out of the
# `current_exit -eq 0` branch so that a stale-primary consumer would still get
# its "nothing here was verified" annotation. That hoist put a combined
# `--check-current` in front of a lock whose PRIMARY question had already come
# back unanswerable, and the combined call returns the SAME primary-side error
# — which that path reports as "could not read this lock's federated half".
# Measured on the shape below: the unresolvable ref is the primary's, in the
# primary's checkout, and the lock's federated half is intact and readable.
#
# Two fixtures in one run, because only the pair pins the attribution down. A
# script that always blamed the primary would pass the first assertion and fail
# the second; one that always blamed the federated half does the reverse.
#   * repo-badprim  — a federated lock whose PRIMARY pin no registry contains
#   * repo-badsrc   — a federated lock whose SOURCE pin its registry does not
#                     contain, with the primary perfectly readable
test_bump_which_half_could_not_be_read() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (which half a failure names) ==="

    local root="$TEST_DIR/bare-halves"
    local work
    rm -rf "$root"
    mkdir -p "$root/bumporg_repo-badprim" "$root/bumporg_repo-badsrc"

    local name
    for name in repo-badprim repo-badsrc; do
        work="$TEST_DIR/work/bumporg-$name"
        rm -rf "$work"; mkdir -p "$work"
        git init --bare --initial-branch=main "$root/bumporg_$name" >/dev/null 2>&1
        git init --initial-branch=main "$work" >/dev/null 2>&1
        cd "$work"
        git config commit.gpgsign false
        git remote add origin "$root/bumporg_$name"
        echo "# $name" > README.md
        if [[ "$name" == "repo-badprim" ]]; then
            # Primary unreadable, source at a ref its own registry really has.
            seed_bump_lock skills.lock "bumporg/agentskills" \
                "9999999999999999999999999999999999999999" "$BUMP_SRC_REF" nofill
        else
            # Primary readable, source pinned at a ref cms-platform never had.
            seed_bump_lock skills.lock "bumporg/agentskills" \
                "$BUMP_REF_OLD" "8888888888888888888888888888888888888888" nofill
        fi
        git add -A
        git commit -m "init" >/dev/null 2>&1
        git push origin HEAD:main >/dev/null 2>&1
        cd "$REPO_ROOT"
    done

    # DEGRADED, because that is the only mode in which one generator call has
    # to answer for both halves. With the scoped flags present the two
    # questions are separate and the attribution is free.
    local gen="$TEST_DIR/generator-degraded-halves.py"
    write_stub_generator "$gen"
    if ! python3 "$TEST_DIR/strip-scoped-flags.py" "$gen"; then
        fail "halves: could not strip the stub's federated flags"
        rm -rf "$root"; return
    fi
    if python3 "$gen" --help 2>&1 | grep -q -- '--only'; then
        fail "halves: --only is still advertised by the stripped stub"
        rm -rf "$root"; return
    else
        pass "halves: the stripped stub no longer advertises --only"
    fi

    BUMP_BARE_DIR_FOR_RUN="$root" BUMP_GENERATOR_FOR_RUN="$gen" \
        run_bump "$TEST_DIR/bump-halves.txt"
    unset BUMP_BARE_DIR_FOR_RUN BUMP_GENERATOR_FOR_RUN
    local log="$TEST_DIR/bump-halves.txt"

    # Each repo's own line, isolated, so one repo's message cannot satisfy an
    # assertion about the other.
    local prim src
    prim=$(grep -F 'bumporg/repo-badprim:' "$log" || true)
    src=$(grep -F 'bumporg/repo-badsrc:' "$log" || true)

    if grep -qF 'could not decide whether skills.lock is current' <<< "$prim"; then
        pass "halves: an unreadable PRIMARY is reported as the primary question failing"
    else
        fail "halves: an unreadable PRIMARY is reported as the primary question failing — got '${prim:-no line at all}'"
    fi
    if grep -qF "could not read this lock's federated half" <<< "$prim"; then
        fail "halves: and never as the federated half — got '$prim'"
    else
        pass "halves: and never as the federated half"
    fi
    if grep -qF "could not read this lock's federated half" <<< "$src"; then
        pass "halves: an unreadable SOURCE is still reported as the federated half"
    else
        fail "halves: an unreadable SOURCE is still reported as the federated half — got '${src:-no line at all}'"
    fi

    # Both are counted failures and neither is re-pinned: "cannot say" is never
    # a licence to write a consumer's lock.
    assert_contains "$log" "2 failed" "halves: both are counted failures, so the run goes red"
    for name in repo-badprim repo-badsrc; do
        if [[ -z "$(git -C "$root/bumporg_$name" rev-parse --verify -q \
                    refs/heads/skills-lock-bump/update 2>/dev/null || true)" ]]; then
            pass "halves: nothing is pushed to $name"
        else
            fail "halves: nothing is pushed to $name"
        fi
    done

    rm -rf "$root"
}

# ── Test 8h5: a degraded run's PR body says only what it asked ────────────
#
# THE WORST OUTPUT THIS SCRIPT HAS, and it was reachable on exactly the window
# the two soft probes exist to survive. With a generator predating `--only`,
# `fed_drifted_regs` is empty for every consumer — and the first cut branched
# the federated disclosure on that emptiness alone, so a degraded run's PR body
# claimed each source "was put its own `--check-current --only <registry>`
# question and answered that its bundles have not moved" and labelled it
# **unchanged**. Both halves false: no such question exists on that generator,
# and this fixture's source HAS moved.
#
# The fixture is repo-fed-stale's shape (both halves moved) in a bare dir of its
# own. Both halves matter: the stale PRIMARY is the only reason a degraded run
# opens a PR here at all, and the moved SOURCE is what makes "unchanged" a
# checkable falsehood rather than an accidental truth.
test_bump_degraded_federated_body() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (what a degraded run discloses) ==="

    local root="$TEST_DIR/bare-degradedfed"
    local work="$TEST_DIR/work/bumporg-repo-degraded-fed"
    rm -rf "$root" "$work"
    mkdir -p "$root/bumporg_repo-degraded-fed" "$work"
    git init --bare --initial-branch=main "$root/bumporg_repo-degraded-fed" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    cd "$work"
    git config commit.gpgsign false
    git remote add origin "$root/bumporg_repo-degraded-fed"
    echo "# repo-degraded-fed" > README.md
    seed_bump_lock skills.lock "bumporg/agentskills" "$BUMP_REF_OLD" "$BUMP_SRC_REF"
    git add -A
    git commit -m "init" >/dev/null 2>&1
    git push origin HEAD:main >/dev/null 2>&1
    cd "$REPO_ROOT"

    local gen="$TEST_DIR/generator-degraded-fed.py"
    write_stub_generator "$gen"
    if ! python3 "$TEST_DIR/strip-scoped-flags.py" "$gen"; then
        fail "degraded body: could not strip the stub's federated flags"
        rm -rf "$root" "$work"
        return
    fi
    # The strip is the fixture, so it is verified before anything is asserted
    # about the run — a strip that matched nothing would leave the ORDINARY
    # path under test while this function reported on the degraded one.
    if python3 "$gen" --help 2>&1 | grep -q -- '--only'; then
        fail "degraded body: --only is still advertised by the stripped stub"
    else
        pass "degraded body: the stripped stub no longer advertises --only"
    fi

    BUMP_BARE_DIR_FOR_RUN="$root" BUMP_GENERATOR_FOR_RUN="$gen" \
        run_bump "$TEST_DIR/bump-degradedfed.txt"
    unset BUMP_BARE_DIR_FOR_RUN BUMP_GENERATOR_FOR_RUN
    local log="$TEST_DIR/bump-degradedfed.txt"
    local body="$BUMP_PR_BODY_DIR/bumporg_repo-degraded-fed.body"

    if [[ -s "$body" ]]; then
        pass "degraded body: the stale primary still gets its PR"
    else
        fail "degraded body: the stale primary still gets its PR"
        rm -rf "$root" "$work"
        return
    fi
    # The two sentences that were false. Asserted as absences AND as a
    # presence, because deleting the disclosure entirely would satisfy the
    # absences alone.
    assert_prose_omits "$body" "answered that its bundles have not moved" \
        "degraded body: no claim that a scoped question was asked and answered"
    assert_not_contains "$body" "**unchanged**" \
        "degraded body: a source this run never asked about is not labelled unchanged"
    assert_prose_contains "$body" "**Federated sources keep their pins, and this run could not ask whether they should.**" \
        "degraded body: says the question could not be put"
    assert_contains "$body" "**not asked**" \
        "degraded body: each pin carries the only verdict this run has for it"
    assert_contains "$body" "bumporg/cms-platform@${BUMP_SRC_REF:0:7}" \
        "degraded body: names the pin the lock keeps"
    # The annotation for the OTHER half of the degraded population. It used to
    # live inside the primary-is-current branch, so the repos that actually
    # open a PR in degraded mode — these — got no annotation at all, which is
    # where the body above is read. Its wording is asserted whole, `::warning::`
    # included: log() writes to stdout and a green nightly's stdout is read by
    # nobody, so splitting the prefix off would let this pass on the two probe
    # warnings alone.
    assert_prose_contains "$log" "::warning::bumporg/repo-degraded-fed: the primary has moved, and this run did not ask whether a FEDERATED source has moved with it — this generator has no $(scoped_flag_pair) pair" \
        "degraded body: a drifted primary does not silence the federated annotation"
    assert_not_contains "$log" "::warning::bumporg/repo-degraded-fed: a FEDERATED source has moved on since the ref" \
        "degraded body: and it does not claim to know which half moved"
    # THE ARM WHERE THE REMEDY IS ACTIONABLE. This lock names a source that is
    # not its own primary registry, so an updated checkout really would put
    # one scoped question about it — which is what makes the other arm, in
    # test_bump_degraded_self_federating_lock, a branch rather than a deletion.
    assert_prose_contains "$log" "Every federated pin here is carried through unverified. $(degraded_fed_remedy_text 1 bumporg/cms-platform)" \
        "degraded body: and the remedy is offered where it would actually yield a question"
    # And the pin really is kept — the body would be true of a run that
    # advanced it and said nothing, so the lock is read as well.
    local after="$TEST_DIR/degradedfed-after.lock"
    git -C "$root/bumporg_repo-degraded-fed" show \
        "refs/heads/skills-lock-bump/update:skills.lock" > "$after" 2>/dev/null || : > "$after"
    if [[ "$(lock_source_ref_of "$after" bumporg/cms-platform)" == "$BUMP_SRC_REF" ]]; then
        pass "degraded body: the federated pin is carried through, not advanced"
    else
        fail "degraded body: the federated pin is carried through — got $(lock_source_ref_of "$after" bumporg/cms-platform)"
    fi

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
    assert_scoped_probe_warnings "$TEST_DIR/bump-formaterr.txt" 2 \
        "format unreadable: both soft federated probes degraded, one warning each"
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

sweep_bump_branch_sha() {   # <short repo name> — its bump branch, or "" if none
    git -C "$SWEEP_BARE/bumporg_$1" rev-parse --verify -q \
        refs/heads/skills-lock-bump/update 2>/dev/null || true
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

    # ── a listing that SUCCEEDS and still writes to stderr ───────────────
    #
    # Every refusal above turns on what the listing SAID. This one is about
    # what a listing that said the right thing also wrote to stderr on the way
    # — gh's ordinary deprecation and auth-expiry lines, emitted while exiting
    # 0. Merged into the capture they are mapfile'd into $numbers alongside the
    # real ones and become PR NUMBERS THIS SWEEP THEN ACTS ON, so the mechanism
    # runs in the opposite direction to bump-hook-pin.sh's: there a phantom line
    # suppresses the night's work, here it manufactures work that does not
    # exist. Against a real repo it is a merge attempt on a number nobody
    # opened.
    #
    # The signature is `$repo_name#$number` with the warning in the number
    # position, which nothing a healthy run prints can produce; the surviving
    # real merge below is the control that keeps the absence assertion from
    # passing over a sweep that simply did nothing.
    BUMP_PR_LIST_NOTICE_FOR_RUN="bumporg_repo-zz-ready" \
        run_sweep "$TEST_DIR/sweep-dry-notice.txt" --dry-run
    unset BUMP_PR_LIST_NOTICE_FOR_RUN
    local nlog="$TEST_DIR/sweep-dry-notice.txt"

    assert_not_contains "$nlog" "#gh: warning" \
        "sweep notice: a notice on gh's stderr does not become a PR number"
    assert_contains "$nlog" "[DRY RUN] Would merge bumporg/repo-zz-ready#111" \
        "sweep notice (control): the real pull request on that repo is still swept"

    # ── the merge GATE's own answer, under a noisy python3 ───────────────
    #
    # The listing above is one of two captures in this sweep whose value is
    # data; the other is the verdict `pr_merge_verdict` prints, which is split
    # into a verdict word and a detail on the very next line. Its producer is a
    # local `python3 -c`, and python writes to stderr while exiting 0 whenever
    # the inherited environment asks it to (see setup_noisy_python_dir).
    #
    # It fails SAFE — noise is not the word "READY", so no merge happens — and
    # that is exactly what makes it worth a test rather than not: the sweep
    # then stalls on every repo it should have merged, logging "not merged —"
    # followed by a diagnostic that names no cause a reader can act on, and
    # nothing about the run looks broken. The assertion is therefore that the
    # merge the quiet run would make is still identified.
    setup_noisy_python_dir
    BUMP_PATH_PREFIX_FOR_RUN="$TEST_DIR/bin-py-noisy" \
        run_sweep "$TEST_DIR/sweep-dry-noisy-py.txt" --dry-run
    unset BUMP_PATH_PREFIX_FOR_RUN
    local pylog="$TEST_DIR/sweep-dry-noisy-py.txt"

    assert_contains "$pylog" "[DRY RUN] Would merge bumporg/repo-zz-ready#111" \
        "sweep noisy python: the merge gate's verdict is the verdict, not a diagnostic"
    # The stall's exact signature, measured against the merged spelling:
    # `bumporg/repo-zz-ready#111: not merged — DeprecationWarning: an
    # inherited-environment diagnostic`. Anchored on the two halves together so
    # the needle cannot match a line where the diagnostic merely appears.
    assert_not_contains "$pylog" "not merged — DeprecationWarning" \
        "sweep noisy python: no repo is stalled by a reason naming no cause"
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

# ── Test 8i4: an unreadable pull request reports WHY ─────────────────────
#
# `gh pr view` fails in two shapes, and only one of them is self-evident from
# the sweep's own log. "No such PR" can be inferred from the number; a refused
# --json field set cannot be inferred from anything, and the sweep's message
# used to be a bare "could not read the pull request." with the reason sent to
# /dev/null. A scheduled run that goes red with no diagnosable cause is the
# failure this asserts against: the reason gh printed has to reach the log,
# and the run still has to fail.
test_bump_sweep_view_failure_reports_why() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (an unreadable pull request says why) ==="

    setup_sweep_repos
    local before_sha log
    before_sha=$(sweep_main_sha repo-zz-ready)

    BUMP_VIEW_FAILS_FOR_RUN="bumporg_repo-zz-ready" \
        run_sweep "$TEST_DIR/sweep-view-fails.txt"
    unset BUMP_VIEW_FAILS_FOR_RUN
    log="$TEST_DIR/sweep-view-fails.txt"

    assert_contains "$log" "bumporg/repo-zz-ready#111: could not read the pull request" "view failure: the unreadable pull request is named"
    # The point of the whole change: the reason gh printed, not just that it
    # failed. Asserting the gh text rather than the script's own prefix is what
    # makes this test fail if the stderr capture is dropped again.
    assert_contains "$log" 'unknown JSON field: "statusCheckRollup"' "view failure: gh's own reason reaches the log"
    assert_not_contains "$log" "bumporg/repo-zz-ready#111: MERGED" "view failure: an unreadable pull request is not merged"
    if [[ "$(sweep_main_sha repo-zz-ready)" == "$before_sha" ]]; then
        pass "view failure: the default branch did not move"
    else
        fail "view failure: the default branch did not move"
    fi
    if [[ $BUMP_EXIT -ne 0 ]]; then
        pass "view failure: the run exits non-zero, so a scheduled run goes red"
    else
        fail "view failure: the run exits non-zero, so a scheduled run goes red (got 0)"
    fi
    # One repo's unreadable PR is one repo's: the sweep goes on.
    assert_contains "$log" "bumporg/repo-nochecks#106: MERGED with a merge commit" "view failure: other repos still merge"
}

# ── Test 8i5: a failed branch delete is not a deleted branch ──────────────
#
# delete_bump_branch exists to stop the NEXT run refusing to propose here, so
# the case with teeth is the one where the delete did NOT happen and the
# function reports that it did. It used to decide that by grepping the failed
# DELETE's own output for `not found|does not exist`, unanchored — and GitHub
# answers 404 rather than 403 when a credential is not authorized to know a
# repo exists, so a scope gap, a revoked installation and an expired token all
# arrive carrying exactly the "Not Found" an absent ref would. The branch
# survives, the run calls it gone, and that repo is stuck for good with nothing
# anywhere going red: the bumper exits 0 and the consumer's own verdict reads
# OK while it serves a stale bundle. That is the four-day, five-repo stall this
# cleanup was written to end, reintroduced by the cleanup itself.
#
# Three fixtures, because the failure assertion alone would also be green on a
# function that never calls anything gone and never deletes anything:
#
#   * THE REGRESSION — a 404 on the DELETE with the ref still on the bare repo;
#   * a ref that is GENUINELY absent, because the repo has "automatically
#     delete head branches" on and reaped it during the merge — the one shape
#     that may be reported as success;
#   * an ordinary delete that works, which must still say "deleted" and must
#     still leave nothing behind.
test_bump_sweep_branch_cleanup() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh (a failed branch delete is not a deleted branch) ==="

    setup_sweep_repos
    local nochecks_tip log
    nochecks_tip=$(sweep_bump_branch_sha repo-nochecks)
    if [[ -n "$nochecks_tip" ]]; then
        pass "branch cleanup: the fixture starts with a bump branch there is something to delete"
    else
        fail "branch cleanup: the fixture starts with a bump branch there is something to delete — found none"
        return
    fi

    BUMP_DELETE_REF_FAIL_FOR_RUN="bumporg_repo-nochecks" \
        run_sweep "$TEST_DIR/sweep-delete-404.txt"
    unset BUMP_DELETE_REF_FAIL_FOR_RUN
    log="$TEST_DIR/sweep-delete-404.txt"

    assert_contains "$log" "bumporg/repo-nochecks: WARN could not delete skills-lock-bump/update" \
        "branch cleanup: a DELETE that failed with a 404 is reported as a failure"
    # Deliberately unqualified. repo-zz-ready's delete SUCCEEDS in this same
    # run, so nothing legitimate prints this phrase anywhere in this log — and
    # naming the repo would let the misread through under a reworded prefix.
    assert_not_contains "$log" "was already gone" \
        "branch cleanup: a 404 that could be a scope gap is not read as an already-deleted branch"
    if [[ "$(sweep_bump_branch_sha repo-nochecks)" == "$nochecks_tip" ]]; then
        pass "branch cleanup: the branch the delete failed on is still on the repo"
    else
        fail "branch cleanup: the branch the delete failed on is still on the repo — it is gone, so the assertion above proves nothing"
    fi
    # A failed cleanup does not unwind the merge that preceded it. That is why
    # the call site spells this `|| true` instead of folding it into `gh pr
    # merge --delete-branch`, where the deletion's exit code becomes the
    # merge's.
    assert_contains "$log" "bumporg/repo-nochecks#106: MERGED with a merge commit" \
        "branch cleanup: the merge still stands when the cleanup after it fails"
    assert_contains "$log" "2 merged" "branch cleanup: and is still counted in the summary"

    # ── CONTROL, from the same run: a delete that works.
    assert_contains "$log" "bumporg/repo-zz-ready: deleted skills-lock-bump/update" \
        "branch cleanup (control): a delete that worked says deleted"
    if [[ -z "$(sweep_bump_branch_sha repo-zz-ready)" ]]; then
        pass "branch cleanup (control): and the branch really is gone afterwards"
    else
        fail "branch cleanup (control): and the branch really is gone afterwards — it is still on the bare repo"
    fi

    # ── CONTROL: genuinely absent. The repo reaped the head branch on merge,
    # so the DELETE fails on a ref that truly no longer exists. This is the ONE
    # failure that is success, and without it the run above would also pass
    # against a function that had simply stopped believing anything.
    setup_sweep_repos
    BUMP_MERGE_REAPS_BRANCH_FOR_RUN="bumporg_repo-nochecks" \
        run_sweep "$TEST_DIR/sweep-delete-gone.txt"
    unset BUMP_MERGE_REAPS_BRANCH_FOR_RUN
    log="$TEST_DIR/sweep-delete-gone.txt"

    assert_contains "$log" "bumporg/repo-nochecks: skills-lock-bump/update was already gone." \
        "branch cleanup (control): a ref that is genuinely absent is success, not a WARN"
    assert_not_contains "$log" "WARN could not delete" \
        "branch cleanup (control): and nothing warns about a branch there was nothing left to delete"
    if [[ -z "$(sweep_bump_branch_sha repo-nochecks)" ]]; then
        pass "branch cleanup (control): the reaped branch really is absent"
    else
        fail "branch cleanup (control): the reaped branch really is absent — the mock left it in place"
    fi

    # ── THE SECOND DOOR. The DELETE fails AND the follow-up question fails
    # too: the credential is gone by the time we ask. `gh api --jq` prints the
    # error body to stdout with the filter unapplied, so the output is blanked
    # with the exit code to keep that body from being searched — and blanking
    # it makes the exact-ref test below find nothing, which is
    # indistinguishable from a ref that is genuinely absent. Reading THAT as
    # already-gone is the original defect wearing the follow-up query's
    # clothes, so the exit code has to be read as well as captured.
    #
    # Nothing else in the suite reaches this: both fixtures above answer the
    # follow-up successfully, so a `refs_exit` that is captured and never read
    # is green in every one of them.
    setup_sweep_repos
    nochecks_tip=$(sweep_bump_branch_sha repo-nochecks)
    BUMP_DELETE_REF_FAIL_FOR_RUN="bumporg_repo-nochecks" \
    BUMP_MATCHING_REFS_FAIL_FOR_RUN="bumporg_repo-nochecks" \
        run_sweep "$TEST_DIR/sweep-delete-blind.txt"
    unset BUMP_DELETE_REF_FAIL_FOR_RUN BUMP_MATCHING_REFS_FAIL_FOR_RUN
    log="$TEST_DIR/sweep-delete-blind.txt"

    # Unqualified for the same reason as above: repo-zz-ready's delete succeeds
    # in this run, so no legitimate line carries this phrase.
    assert_not_contains "$log" "was already gone" \
        "branch cleanup: a follow-up query that FAILED is not an answer that the branch is gone"
    assert_contains "$log" "bumporg/repo-nochecks: WARN could not delete skills-lock-bump/update" \
        "branch cleanup: a delete whose outcome could not be established warns"
    # The two failures may not read the same. Quoting the DELETE's body here
    # would print `Reference does not exist` from the branch that just declined
    # to conclude exactly that.
    assert_contains "$log" "could not establish whether it survived" \
        "branch cleanup: the warning says which question went unanswered, not just that a delete failed"
    # NOT `assert_not_contains "Reference does not exist"`, which is what this
    # started as and what the real 422 body says. It is INERT here: the blind
    # fixture's DELETE answers 404 `Not Found`, so that phrase is absent for a
    # reason having nothing to do with the code, and the assertion passed with
    # the branch under test deleted (measured). This one is wired — collapsing
    # the two warnings makes the blind case claim the ref is still on the repo,
    # which is exactly the conclusion it declined to draw.
    assert_not_contains "$log" "it is still on the repo" \
        "branch cleanup: and does not assert the ref survived, having just refused to conclude that"
    if [[ "$(sweep_bump_branch_sha repo-nochecks)" == "$nochecks_tip" ]]; then
        pass "branch cleanup: and the branch is still there to be warned about"
    else
        fail "branch cleanup: and the branch is still there to be warned about — it is gone, so the assertions above prove nothing"
    fi
    # The control that keeps the run above from passing on a function that has
    # simply stopped answering: the OTHER repo in the same run still gets a
    # clean delete, so the failure is scoped to the repo the mock blinded.
    assert_contains "$log" "bumporg/repo-zz-ready: deleted skills-lock-bump/update" \
        "branch cleanup (control): a repo the credential can still see is deleted in that same run"

    # ── THE PREFIX. matching-refs matches a STRING PREFIX over the whole ref
    # name, as GitHub's does, so asking for `heads/skills-lock-bump/update`
    # also returns `heads/skills-lock-bump/update-2`. A test for an EMPTY
    # result therefore reads a live SIBLING as proof that OUR branch survived,
    # and warns about a ref that is genuinely gone.
    #
    # This fixture exists because the exact-ref test was UNCOVERED. Measured
    # 2026-08-29: substituting `[[ -z "$refs_out" ]]` for the exact-ref grep
    # left the suite at 996 passed, 0 failed. That is not hypothetical — the
    # substitution went in unnoticed once already, and every gate stayed green
    # over it. The sibling planted below is the only thing that tells the two
    # readings apart, so it is the reason the grep may not be "simplified".
    setup_sweep_repos
    git -C "$SWEEP_BARE/bumporg_repo-nochecks" update-ref \
        refs/heads/skills-lock-bump/update-2 \
        refs/heads/skills-lock-bump/update
    if [[ -n "$(git -C "$SWEEP_BARE/bumporg_repo-nochecks" rev-parse --verify -q \
                refs/heads/skills-lock-bump/update-2 2>/dev/null)" ]]; then
        pass "branch cleanup: the same-prefix sibling really is on the fixture"
    else
        fail "branch cleanup: the same-prefix sibling really is on the fixture — it is absent, so the assertions below prove nothing"
    fi

    # The merge reaps OUR branch, exactly as in the genuinely-absent control
    # above; the only difference is the sibling left standing beside it.
    BUMP_MERGE_REAPS_BRANCH_FOR_RUN="bumporg_repo-nochecks" \
        run_sweep "$TEST_DIR/sweep-delete-sibling.txt"
    unset BUMP_MERGE_REAPS_BRANCH_FOR_RUN
    log="$TEST_DIR/sweep-delete-sibling.txt"

    assert_contains "$log" "bumporg/repo-nochecks: skills-lock-bump/update was already gone." \
        "branch cleanup: a same-prefix sibling is not our branch — the reaped ref still reads as gone"
    assert_not_contains "$log" "WARN could not delete skills-lock-bump/update" \
        "branch cleanup: and nothing warns about a branch that a sibling merely resembles"

    rm -rf "$SWEEP_BARE" "$SWEEP_PR_DIR"
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
    grep -oE '\$SCRIPT_DIR/[A-Za-z0-9][A-Za-z0-9._/-]*' "$REPO_ROOT/scripts/sync.sh" \
        | sed 's#\$SCRIPT_DIR/#scripts/#' > "$helpers_file" || true
    # `[A-Za-z0-9.]` to open, and `/` inside the class: sync.sh resolves
    # `$REPO_ROOT/.claude/hooks/fleet-memory.sh` and
    # `$REPO_ROOT/agents-md/base.md`, and the older pattern could see neither —
    # it stopped at the first slash and refused a leading dot outright, so a
    # reference into a subdirectory derived as a truncated stem or as nothing
    # at all. A path this cannot see is a file whose edit fires no sync, which
    # is the exact failure the test exists to catch.
    grep -oE '\$REPO_ROOT/[A-Za-z0-9.][A-Za-z0-9._/-]*' "$REPO_ROOT/scripts/sync.sh" \
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
        # An exact entry, or a `<dir>/**` entry that covers it. Exactness
        # still matters within a segment — "repos.yml" is a substring of a
        # dozen plausible paths — so the glob arm walks whole path segments
        # rather than doing a prefix match on the raw string.
        covered=false
        if grep -qxF -- "$want" "$paths_file"; then
            covered=true
        else
            prefix="$want"
            while [[ "$prefix" == */* ]]; do
                prefix="${prefix%/*}"
                if grep -qxF -- "$prefix/**" "$paths_file"; then covered=true; break; fi
            done
        fi
        if $covered; then
            pass "sync trigger: on.push.paths covers $want"
        else
            fail "sync trigger: on.push.paths does not cover $want — editing it would change what the sync does to the fleet with no run to apply the change"
        fi
    done < "$want_file"
}

# ── The self-hosted fleet-memory payload matches agents-md/base.md ────────
#
# This repo is excluded from the sync (SYNC_SELF_REPO), so nothing overwrites
# a drifted copy here and no drift report mentions it — the same blind spot
# test 7b covers for the bootstrap hook. It matters more here than it looks:
# this repo's own AGENTS.md is now the STUB, so this payload is the only copy
# of the guidance its own sessions will ever load. If it drifts from base.md,
# every session in _agent-guidance is quietly reading something the fleet is
# not.
test_self_hosted_fleet_payload() {
    echo ""
    echo "=== Test: the self-hosted fleet-memory payload matches base.md ==="

    local payload="$REPO_ROOT/.claude/hooks/fleet-guidance.md"
    local source="$REPO_ROOT/agents-md/base.md"

    if [[ ! -s "$payload" ]]; then
        fail "self-hosted payload: $payload is missing or empty — this repo's own sessions would open DEGRADED"
        return
    fi
    if cmp -s "$payload" "$source"; then
        pass "self-hosted payload: byte-identical to agents-md/base.md"
    else
        fail "self-hosted payload: differs from agents-md/base.md — re-copy it (nothing syncs this repo)"
    fi

    # The stub must not have quietly become the payload, or the repo would
    # deliver a pointer to itself and nothing else.
    if grep -qF "Fleet guidance is delivered once per session" "$payload"; then
        fail "self-hosted payload: looks like the stub, not the full guidance"
    else
        pass "self-hosted payload: is the full guidance, not the stub"
    fi

    # And this repo must actually RUN it — a payload nothing invokes is inert.
    local reg
    reg=$(BOOTSTRAP_HOOK_BASENAME="fleet-memory.sh" \
          "$REPO_ROOT/scripts/bootstrap-status.sh" "$REPO_ROOT/.claude/settings.json")
    if [[ "$reg" == "registered" ]]; then
        pass "self-hosted payload: fleet-memory.sh is registered in this repo's settings.json"
    else
        fail "self-hosted payload: fleet-memory.sh is $reg in this repo's settings.json — the hook would never run here"
    fi
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

# workflow_step_by_run <file> <needle> — facts about the FIRST job step whose
# `run:` body contains <needle>, one per line:
#
#   found
#   if <expression>        the step's `if:`, or "-" when it sets none
#   env <NAME>             one per key in the step's `env:`
#   interpolates           emitted only if the run body contains a `${{ }}`
#
# Parsed with the same real parser as its siblings, and here the distinction is
# not theoretical: the first version of test_hook_pin_workflow_wiring grepped
# the workflow for `if: success() || failure()` and PASSED with that key
# deleted, because the comment three lines above it quotes the string verbatim.
# A grep cannot tell a key from prose about the key. This can.
workflow_step_by_run() {
    if [[ ! -d "$REPO_ROOT/node_modules/yaml" ]]; then
        echo "node_modules/yaml is missing — run \`npm ci\` first" >&2
        return 1
    fi
    node -e '
const fs = require("node:fs");
const YAML = require(process.argv[1] + "/node_modules/yaml");
const doc = YAML.parse(fs.readFileSync(process.argv[2], "utf8"));
const needle = process.argv[3];
for (const spec of Object.values((doc && doc.jobs) || {})) {
  for (const step of (spec && spec.steps) || []) {
    if (typeof step.run !== "string" || !step.run.includes(needle)) continue;
    console.log("found");
    console.log("if " + (step.if === undefined ? "-" : String(step.if)));
    for (const key of Object.keys(step.env || {})) console.log("env " + key);
    if (/\$\{\{/.test(step.run)) console.log("interpolates");
    process.exit(0);
  }
}
' "$REPO_ROOT" "$1" "$2"
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
# ── The bump script's own comments, checked against the bump script ───────
#
# This repo's rule is that a comment must not assert anything a reader cannot
# check — and the ones with teeth are the ABSOLUTES, because an absolute stays
# readable long after the code beneath it stops being described by it. Each
# assertion below is a RELATION between two things in the same file, never a
# constant: the presence of the behaviour is asserted first, so a claim that
# nothing does X can only be flagged in a file that visibly does X.
test_bump_script_self_consistency() {
    echo ""
    echo "=== Test: bump-consumer-locks.sh's comments against its own code ==="

    local script="$REPO_ROOT/scripts/bump-consumer-locks.sh"
    if grep -qF -- '--repin-source "$reg@"' "$script"; then
        pass "self-consistency: this script does advance a federated pin, so the claims below have a subject"
    else
        fail "self-consistency: this script does advance a federated pin, so the claims below have a subject"
        return
    fi
    # Both of these stood forty lines above the loop that contradicts them, so
    # a reader going top to bottom met the false absolute first.
    #
    # EACH NEEDLE IS THE WHOLE SENTENCE, through `assert_prose_*`, which
    # flattens the comment's line breaks and its `#` markers before matching.
    # It could not be, once: `grep -F` matches within one line, so the
    # full-sentence form of either of these matched nothing in EITHER version
    # of the file and PASSED while the sentence was still there — measured
    # against the pre-fix script. The truncated fragments that replaced them
    # were live but arbitrary, and each was one rewrap away from the same
    # green light wired to nothing.
    assert_prose_omits "$script" "nothing in this system ever advances a federated pin" \
        "self-consistency: no comment claims a federated pin is never advanced"
    assert_prose_omits "$script" "the moment a federated checkout sits ahead of its pin that FAILED: is permanent" \
        "self-consistency: no comment claims a federated FAILED: can never be cleared"

    # _agent-guidance#65. The cross-repo block asked a future reader to rewrite
    # an agentskills paragraph beginning "One consequence to expect rather than
    # re-discover", which that repo had already replaced — so the instruction
    # pointed at text that is not there, and a stale cross-repo pointer is read
    # and believed exactly like a stale SHA-pin version comment. Two halves,
    # because deleting the whole block would leave the agentskills side (which
    # names this one by its heading) pointing at nothing: the heading stays,
    # the discharged request does not.
    if grep -qF -- 'A SIBLING SITE MOVES WITH THIS' "$script"; then
        pass "self-consistency: the cross-repo block agentskills names by heading still exists"
    else
        fail "self-consistency: the cross-repo block agentskills names by heading still exists"
    fi
    assert_prose_omits "$script" "carries a paragraph beginning \"One consequence to expect rather than re-discover\"" \
        "self-consistency: it no longer asks for an edit to a paragraph that is gone"
    # The request read "It is a one-paragraph edit over there — make it when
    # that / repo is next open" — wrapped after "that", which is why this is a
    # prose assertion and not a `grep -F` for a fragment that happens to fit
    # one line.
    assert_prose_omits "$script" "It is a one-paragraph edit over there — make it when that repo is next open" \
        "self-consistency: and carries no outstanding cross-repo request at all"

    # ── EVERY PER-REPO SENTENCE ABOUT THE SCOPED SHORTFALL NAMES THE PAIR,
    #    which is what makes the library's claim to hold the whole set of them
    #    checkable rather than a comment a reader has to trust.
    #
    #    THE SPLIT IS THE POINT. Above the per-repo loop there are exactly two
    #    sentences that may name ONE scoped flag: the `--only` probe's warning
    #    and the `--repin-source` probe's. Each is emitted by the probe that
    #    tested that one flag, so each names what it measured. Below the loop
    #    header nothing has measured either flag on its own —
    #    FED_ADVANCE_AVAILABLE is the conjunction and it is all a per-repo
    #    sentence has — so a per-repo sentence naming one conjunct is false on
    #    the generator carrying the other. That was the shipped defect: two
    #    annotations in this half said "this generator cannot say which one" /
    #    "cannot say whether a FEDERATED source has moved with it".
    #
    #    Nothing else covers this half. The cross product governs the PR body
    #    and the self-named log line; these `::warning::`s are neither, and a
    #    sixth sentence added here tomorrow would be guarded by nothing.
    #    SENTENCES ONLY, which is why this reads the lines that PRINT rather
    #    than every line naming a flag. Below the loop header the flags are
    #    also passed to the generator and interpolated into
    #    `repin_source_flags_shown`; neither is a sentence, and the second is
    #    a value the claims library governs.
    local below scoped_offenders
    below=$(awk '/^for repo_name in /{f=1} f' "$script")
    scoped_offenders=$(printf '%s\n' "$below" \
        | grep -nE -- '::warning::|^[[:space:]]*(log|fail) "' \
        | grep -E -- '--check-current --only|--repin-source|scoped question' \
        | grep -vF -- '${SCOPED_FLAG_PAIR}' \
        | grep -vE '^[[:space:]]*[0-9]+:[[:space:]]*#' || true)
    if [[ -z "$scoped_offenders" ]]; then
        pass "self-consistency: every per-repo sentence about the scoped shortfall names the PAIR, not one flag"
    else
        fail "self-consistency: every per-repo sentence about the scoped shortfall names the PAIR, not one flag — $(printf '%s' "$scoped_offenders" | head -3 | tr '\n' ' ')"
    fi
}

# ── ADR 0009's cited measurement, against the report it describes ─────────
#
# The ADR quotes a three-way measurement of `--check-current` as the evidence
# for its decision. Two of the three were taken against the generator as it
# stood BEFORE the paired PR and became checkably false when that PR grouped
# the report by source — a decision that is right, with stated evidence that is
# quotable and wrong, which is worse than vague evidence. The relation asserted
# here is in-repo and real: this suite's own stand-in reproduces the per-source
# report, so an ADR beside it cannot claim a single headline for every case.
test_adr_0009_self_consistency() {
    echo ""
    echo "=== Test: ADR 0009's cited measurement against the report it describes ==="

    local adr="$REPO_ROOT/docs/decisions/0009-a-federated-pin-advances-on-a-scoped-question.md"
    local suite="$REPO_ROOT/test/run-tests.sh"
    if [[ -f "$adr" ]]; then
        pass "ADR 0009: the record exists to check"
    else
        fail "ADR 0009: the record exists to check"
        return
    fi
    if grep -qF 'ONE BLOCK PER DRIFTED SOURCE' "$suite"; then
        pass "ADR 0009: the stand-in really does print one block per drifted source"
    else
        fail "ADR 0009: the stand-in really does print one block per drifted source"
        return
    fi
    assert_prose_omits "$adr" "**all three drifted** — still exactly one headline." \
        "ADR 0009: it does not claim one headline for several drifted sources"
    # The old bullet wrapped after "primary's" ("the same headline, still
    # naming the primary's / clean sha"), and a `grep -F` for the sentence
    # therefore matched nothing in either version and could not go red —
    # measured against the pre-fix ADR, where the full-sentence form passed
    # while the bullet was still there. These are prose assertions for that
    # reason: the whole sentence is the needle, and the ADR may rewrap.
    assert_prose_omits "$adr" "the same headline, still naming the primary's clean sha" \
        "ADR 0009: it does not claim a source-only drift is attributed to the primary"
    assert_prose_omits "$adr" "\`check_current\` returns one flat list of differences across every source" \
        "ADR 0009: it does not describe, in the present tense, a shape the generator no longer has"
    # And the half that DOES still reproduce has to still be there, or the
    # absences above are satisfiable by deleting the evidence outright.
    assert_prose_contains "$adr" "**only the primary edited, the source exactly at its pin**" \
        "ADR 0009: the measurement that actually justifies the decision is still cited"
}

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

# yq_install_facts — one fact per line about how this repo's workflows fetch a
# third-party binary, read with the same real parser the helpers above use.
#
# A line scan cannot do this job, and this very change set is the proof: ci.yml
# now carries the literal string `releases/latest` inside a COMMENT that
# explains the ref it stopped using. A grep flags that comment, and it would
# equally miss a mutable ref reached through a YAML anchor or a folded scalar —
# matching bytes it can see, blind to structure it cannot. Every fact below is
# therefore read off PARSED `run:` bodies and step `env:` mappings, by which
# point comments do not exist.
#
#   workflow <file>              one per workflow parsed          (vacuity guard)
#   fetch    <file> <step>       a run: body fetching the yq release asset
#   mutable  <file> <step>       a run: body naming a MUTABLE release ref
#   unverified <file> <step>     a fetch with no sha256 check in the same body
#   step     <file> <digest>     sha256 over the whole install step, for skew
#   version  <file> <value>      its YQ_VERSION
#   digest   <file> <value>      its YQ_SHA256
yq_install_facts() {
    if [[ ! -d "$REPO_ROOT/node_modules/yaml" ]]; then
        echo "node_modules/yaml is missing — run \`npm ci\` first" >&2
        return 1
    fi
    node -e '
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const YAML = require(process.argv[1] + "/node_modules/yaml");
const dir = path.join(process.argv[1], ".github", "workflows");
for (const file of fs.readdirSync(dir).filter((f) => /\.ya?ml$/.test(f)).sort()) {
  const doc = YAML.parse(fs.readFileSync(path.join(dir, file), "utf8"));
  if (!doc || typeof doc !== "object") {
    console.error(file + " does not parse to a mapping");
    process.exit(1);
  }
  console.log("workflow " + file);
  for (const spec of Object.values(doc.jobs || {})) {
    for (const step of (spec && spec.steps) || []) {
      if (!step || typeof step.run !== "string") continue;
      const name = String(step.name || "(unnamed)").replace(/\s+/g, "_");
      const body = step.run;
      // Mutable by construction: whatever the tag points at TODAY.
      if (body.includes("/releases/latest")) console.log("mutable " + file + " " + name);
      // Match the ASSET, not the path shape: a step that regressed to a
      // mutable ref must still COUNT as an install step, or restoring one
      // trips the vacuity guard ("3 steps, expected 4") instead of the
      // mutable assertion, and the failure names a deleted step rather than
      // the ref that came back. Caught by the negative control, not by
      // review.
      if (!body.includes("yq_linux_amd64")) continue;
      console.log("fetch " + file + " " + name);
      if (!body.includes("sha256sum -c")) console.log("unverified " + file + " " + name);
      const env = step.env || {};
      // Hash the whole step, env included: two copies that differ in ANY of
      // name, env or body are a skew this test exists to catch.
      const canonical = JSON.stringify([step.name || null, env, body]);
      console.log("step " + file + " " + crypto.createHash("sha256").update(canonical).digest("hex"));
      console.log("version " + file + " " + String(env.YQ_VERSION));
      console.log("digest " + file + " " + String(env.YQ_SHA256));
    }
  }
}
' "$REPO_ROOT"
}

# ── Test 7h: the yq install is pinned, verified, retried — and unskewed ────
#
# THE INCIDENT. On 2026-08-20 run 32383649430 (PR #56) lost this step to `wget`
# exit 4 — a network failure — four seconds in, before a test body ran, reding
# a `test` check run while a sibling run of the same context on the same head
# passed. One transient blip, one red CI, nothing to do with the diff.
#
# THE LARGER PROBLEM the incident exposed. The ref was
# `releases/latest/download`: a MUTABLE pointer to a third-party binary that is
# then chmod +x and run as root. That is precisely the trust the fleet's
# SHA-pinning rule exists to constrain — and the rule could not reach it,
# because the rule governs `uses:` and this is a `run:` block. It sat, unnoticed,
# in the repo that HOSTS the rule.
#
# WHAT THIS PINS, and why each assertion is separate from the others:
#   * no run: body anywhere reaches a mutable release ref — the regression;
#   * every yq fetch verifies a sha256 in the same body — a pin without a
#     digest check still trusts whatever the host serves under that tag;
#   * the version is an exact vX.Y.Z and the digest is 64 hex — so a future
#     `YQ_VERSION: latest` cannot satisfy the assertion above by shape alone;
#   * all four copies are byte-identical. The step is duplicated across four
#     workflows rather than factored into a composite action, because a local
#     `uses: ./…` would fail test_bump_workflow's 40-hex pin assertion and
#     widening a security lint to accommodate a refactor is the wrong trade
#     (docs/decisions/0008). Duplication is only safe while it cannot skew, and
#     four copies of a version+digest is exactly the pin-skew hazard this
#     account has been bitten by — so the identity check is what BUYS the
#     duplication, not a tidiness nicety.
test_yq_install_pinned() {
    echo ""
    echo "=== Test: the yq install is version-pinned, digest-verified and retried ==="

    local facts="$TEST_DIR/yq-install-facts.txt"
    local err="$TEST_DIR/yq-install-facts.err"

    if ! yq_install_facts > "$facts" 2> "$err"; then
        fail "yq install: could not parse this repo's workflows — $(head -1 "$err")"
        return
    fi

    # VACUITY GUARD. Three of the four assertions below are about an ABSENCE,
    # and the empty set satisfies every one of them perfectly. A parse that
    # silently yielded nothing would hand back a clean bill of health for files
    # it never opened — so establish first that workflows were read AND that
    # the step being judged was actually found.
    local workflows fetches
    workflows=$(awk '$1 == "workflow"' "$facts" | wc -l)
    fetches=$(awk '$1 == "fetch"' "$facts" | wc -l)
    if [[ "$workflows" -ge 4 && "$fetches" -eq 4 ]]; then
        pass "yq install: parsed $workflows workflows and found all 4 install steps"
    else
        fail "yq install: expected >=4 workflows parsed and exactly 4 yq install steps, got $workflows and $fetches — every assertion below would be vacuous"
        return
    fi

    local mutable
    mutable=$(awk '$1 == "mutable" { print $2 "/" $3 }' "$facts" | tr '\n' ' ' | sed 's/ $//')
    if [[ -z "$mutable" ]]; then
        pass "yq install: no run: body reaches a mutable release ref"
    else
        fail "yq install: a mutable release ref is back in $mutable — 'latest' is whatever the tag points at on the day CI runs, on a binary this step makes executable and runs as root. Pin the version and the digest; see docs/decisions/0008"
    fi

    local unverified
    unverified=$(awk '$1 == "unverified" { print $2 "/" $3 }' "$facts" | tr '\n' ' ' | sed 's/ $//')
    if [[ -z "$unverified" ]]; then
        pass "yq install: every yq fetch checks a sha256 in the same run: body"
    else
        fail "yq install: $unverified downloads yq without checking a sha256 — a version pin alone still trusts whatever the host serves under that tag"
    fi

    # Exact form, not "looks versionish": `latest` and `v4` are both refs that
    # can move under a pin that a laxer check would accept.
    local bad_version bad_digest
    bad_version=$(awk '$1 == "version" && $3 !~ /^v[0-9]+\.[0-9]+\.[0-9]+$/ { print $2 "=" $3 }' "$facts" | tr '\n' ' ' | sed 's/ $//')
    bad_digest=$(awk '$1 == "digest" && $3 !~ /^[0-9a-f]{64}$/ { print $2 "=" $3 }' "$facts" | tr '\n' ' ' | sed 's/ $//')
    if [[ -z "$bad_version" && -z "$bad_digest" ]]; then
        pass "yq install: every copy states an exact vX.Y.Z and a 64-hex sha256"
    else
        fail "yq install: not an exact version and digest — version ${bad_version:-ok}, digest ${bad_digest:-ok}"
    fi

    local distinct
    distinct=$(awk '$1 == "step" { print $3 }' "$facts" | sort -u | wc -l)
    if [[ "$distinct" -eq 1 ]]; then
        pass "yq install: all 4 copies of the step are identical"
    else
        fail "yq install: the 4 copies have drifted into $distinct variants — they carry the pinned version and digest, so a skew means CI, the sync, the drift report and the lock bumper are no longer running the same yq. Edit one and copy it to the other three"
    fi
}

# ── Test 9: bump-hook-pin.sh ──────────────────────────────────────────────
#
# The other pin. bump-consumer-locks.sh moves every consumer's `skills.lock`;
# this one moves the hook that READS that lock, which until ADR 0010 moved only
# when somebody hand-edited repos.yml. Four properties carry the lane, and each
# has a fixture rather than a flag:
#
#   * ANTI-CHURN, keyed on the hook's DIGEST and not on the registry's HEAD.
#     `hook_pin_advance_registry` moves the registry forward WITHOUT touching
#     the hook — the ordinary case, since the registry moves for skills, docs
#     and ADRs. A re-pinner keyed on "is `ref` the newest commit" opens a pull
#     request here every night forever, which is the failure the lock lane's
#     `repo-current` fixture exists to prevent one lane over.
#   * THE PIN AND THE SELF-HOSTED COPY MOVE TOGETHER. This repo cannot receive
#     the hook it publishes the pin for ($SELF_REPO, ADR 0004 fact 5), so it
#     carries its own copy and test 7b requires that copy to hash to
#     `skills_bootstrap.sha256`. A bump that moved the pin alone would open a
#     pull request that fails its own repo's CI, every time it had anything to
#     say. The assertion is on both files in one commit.
#   * THE WRITE IS SURGICAL. repos.yml is ~90% comment by line count and every
#     comment is prose an ADR points at, so the bump must change two scalar
#     lines and nothing else. Asserted by counting comment lines across the
#     write, not by eyeballing a diff.
#   * IT REFUSES A HOOK IT CANNOT VOUCH FOR. A pin records where the hook is
#     and what it hashes to, and is equally happy recording a file that dies at
#     line 1 — delivery would then hand every allowlisted repo a hook that
#     fails in every session, with a correct digest, so sync.sh's own integrity
#     check passes it straight through.
#
# Deterministic and offline like the rest of the suite: a local registry repo,
# a local bare repo, the shared mock `gh`, no network, no sleeps, no
# wall-clock.

HOOK_PIN_DIR="$TEST_DIR/hookpin"
HOOK_PIN_PR_LOG="$TEST_DIR/hook-pin-pr.log"
HOOK_PIN_BODY_DIR="$TEST_DIR/hook-pin-bodies"
HOOK_PIN_EXIT=0
# Created empty so every later `wc -l`/`grep` on it is total rather than an
# abort under `set -euo pipefail` — the same harness lesson the lock lane's
# BUMP_PR_LOG carries.
: > "$HOOK_PIN_PR_LOG"

# A hook whose bytes vary by marker and which really does parse as bash, so
# `bash -n` passing is a fact about the fixture rather than an accident.
hook_pin_hook_text() {   # <marker>
    printf '#!/usr/bin/env bash\nset -euo pipefail\n# skills-bootstrap fixture: %s\necho "{}"\n' "$1"
}

hook_pin_commit_hook() {   # <marker> <commit subject>
    local reg="$HOOK_PIN_DIR/registry"
    hook_pin_hook_text "$1" > "$reg/.claude/hooks/skills-bootstrap.sh"
    chmod +x "$reg/.claude/hooks/skills-bootstrap.sh"
    git -C "$reg" add -A
    git -C "$reg" commit -q -m "$2"
}

# Move the registry forward WITHOUT touching the hook. This is what the
# registry does most days, and the fixture that makes anti-churn testable.
hook_pin_advance_registry() {   # <subject>
    local reg="$HOOK_PIN_DIR/registry"
    echo "$1" >> "$reg/CHANGELOG.md"
    git -C "$reg" add -A
    git -C "$reg" commit -q -m "$1"
}

hook_pin_registry_head() { git -C "$HOOK_PIN_DIR/registry" rev-parse HEAD; }

# The guidance repo as it stands on its default branch: a heavily commented
# repos.yml pinning the OLD hook, plus the self-hosted copy of that same hook.
hook_pin_seed_target() {   # <pinned ref> <pinned sha256> <hook marker>
    local work="$HOOK_PIN_DIR/work" bare="$HOOK_PIN_DIR/bare/pinorg_guidance"
    rm -rf "$work" "$bare"
    git init --bare --initial-branch=main "$bare" >/dev/null 2>&1
    git init --initial-branch=main "$work" >/dev/null 2>&1
    git -C "$work" config commit.gpgsign false
    git -C "$work" remote add origin "$bare"
    mkdir -p "$work/.claude/hooks"
    hook_pin_hook_text "$3" > "$work/.claude/hooks/skills-bootstrap.sh"
    chmod +x "$work/.claude/hooks/skills-bootstrap.sh"
    # Comment lines on BOTH sides of the two scalars that move, and a block
    # after this one, so a write that re-serialises the document instead of
    # patching two lines cannot pass the comment-count assertion by luck.
    cat > "$work/repos.yml" <<YAML
# ── fixture repos.yml ──────────────────────────────────────────────────────
# A comment above the block.
exclude: []
default_sections: []

# ── skills-bootstrap hook delivery ─────────────────────────────────────────
#
# A long explanatory comment that must survive the write untouched.
skills_bootstrap:
  # a comment between the key and the registry
  registry: pinorg/agentskills
  path: .claude/hooks/skills-bootstrap.sh
  ref: $1
  # a comment between ref and sha256
  sha256: $2
  repos:
    - repo-adopted

# A trailing block, after the one that moves.
cron_coverage:
  fleet: [repo-adopted]
  out_of_scope: []
YAML
    git -C "$work" add -A
    git -C "$work" commit -q -m "seed"
    git -C "$work" push -q origin main
}

run_hook_pin() {   # <output file> [script args...]
    local out="$1"; shift
    HOOK_PIN_EXIT=0
    MOCK_BARE_DIR="$HOOK_PIN_DIR/bare" \
    MOCK_PR_LOG="$HOOK_PIN_PR_LOG" \
    MOCK_PR_BODY_DIR="$HOOK_PIN_BODY_DIR" \
    MOCK_PR_DIR="${HOOK_PIN_PR_DIR_FOR_RUN:-}" \
    MOCK_PR_LIST_FAILS="${HOOK_PIN_PR_LIST_FAILS_FOR_RUN:-}" \
    MOCK_PR_LIST_STDERR_NOTICE="${HOOK_PIN_PR_LIST_NOTICE_FOR_RUN:-}" \
    REPOS_YML="$HOOK_PIN_DIR/work/repos.yml" \
    BUMP_CHECKOUTS="pinorg/agentskills=$HOOK_PIN_DIR/registry" \
    HOOK_PIN_REPO="pinorg/guidance" \
    PATH="${HOOK_PIN_PATH_PREFIX_FOR_RUN:+$HOOK_PIN_PATH_PREFIX_FOR_RUN:}$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/bump-hook-pin.sh" "$@" > "$out" 2>&1 || HOOK_PIN_EXIT=$?
}

hook_pin_branch_sha() {
    git -C "$HOOK_PIN_DIR/bare/pinorg_guidance" rev-parse --verify -q \
        "refs/heads/hook-pin-bump/update" 2>/dev/null || true
}

# hook_pin_occupied_branch_remedy — the remedy bump-hook-pin.sh prints when it
# refuses a branch occupied by some other pin, READ OUT OF THE SCRIPT rather
# than re-typed here, the way scoped_flag_pair() and self_named_log_line_text()
# already do for the bump library's sentences.
#
# The needle it replaces was the literal "Delete that branch". That wording was
# reworded to "Free the name (delete that branch, or merge what is on it)", and
# because `grep -qF` is case-sensitive the capital D could no longer match
# anything the script prints — so the assertion below passed unconditionally,
# in the scenario it forbids as much as in the one it permits. A re-typed
# needle guarding a sentence is a guard that survives its own subject; anchored
# on the refusal it belongs to, a rewording moves the claim and the guard
# together, and a reworded ANCHOR yields the empty string, which the caller
# turns into a loud failure rather than a silent pass.
hook_pin_occupied_branch_remedy() {
    local anchor="refusing to force-push over it." line remedy
    line=$(grep -m1 -F -- "$anchor" "$REPO_ROOT/scripts/bump-hook-pin.sh") || return 0
    remedy=${line#*"$anchor"}   # everything the refusal says after the refusal
    remedy=${remedy%\"}         # ... minus the closing quote of the shell word
    remedy=${remedy# }          # ... and the space that joined the two sentences
    printf '%s' "$remedy"
}

hook_pin_file_at() {   # <ref> <path>
    git -C "$HOOK_PIN_DIR/bare/pinorg_guidance" show "$1:$2" 2>/dev/null || true
}

assert_no_hook_pin_branch() {   # <label>
    if [[ -z "$(hook_pin_branch_sha)" ]]; then pass "$1"; else fail "$1 — a hook-pin branch was pushed"; fi
}

# ── Test 9a: the registry moved but the hook did not ─────────────────────

test_hook_pin_unchanged() {
    echo ""
    echo "=== Test: bump-hook-pin.sh (registry advanced, hook unchanged) ==="

    rm -rf "$HOOK_PIN_DIR"
    mkdir -p "$HOOK_PIN_DIR/registry/.claude/hooks" "$HOOK_PIN_DIR/bare"
    git init --initial-branch=main "$HOOK_PIN_DIR/registry" >/dev/null 2>&1
    git -C "$HOOK_PIN_DIR/registry" config commit.gpgsign false
    hook_pin_commit_hook "v1" "add the hook"

    local pinned_ref pinned_sha
    pinned_ref=$(hook_pin_registry_head)
    pinned_sha=$(hook_pin_hook_text "v1" | sha256sum | cut -d' ' -f1)

    # The registry moves on, for something that is not the hook.
    hook_pin_advance_registry "an unrelated skill edit"
    hook_pin_advance_registry "an ADR"

    hook_pin_seed_target "$pinned_ref" "$pinned_sha" "v1"

    local out="$TEST_DIR/hook-pin-unchanged.txt"
    run_hook_pin "$out"

    if [[ $HOOK_PIN_EXIT -eq 0 ]]; then
        pass "hook pin: a run with nothing to do exits 0"
    else
        fail "hook pin: a run with nothing to do exits 0 — got $HOOK_PIN_EXIT: $(cat "$out")"
    fi
    assert_contains "$out" "byte-identical to the pinned one" \
        "hook pin: it says the hook is unchanged"
    assert_no_hook_pin_branch "hook pin: two commits past the pin, it still proposes nothing"
    if [[ ! -s "$HOOK_PIN_PR_LOG" ]]; then
        pass "hook pin: no pull request is opened for a registry that only moved"
    else
        fail "hook pin: it opened a pull request for a hook that did not change"
    fi
}

# ── Test 9a2: read_repos_yml's stream separation, in the three copies the
#    sync leg cannot reach ──────────────────────────────────────────────────
#
# `read_repos_yml` is duplicated verbatim in sync.sh, drift-report.sh,
# bump-consumer-locks.sh and bump-hook-pin.sh, and one process loads exactly
# one of those copies. test_sync_agents_sync_yml_unreadable's noisy-yq leg
# covers sync.sh's; this covers the other three, because nothing did — measured
# 2026-08-29, reverting any single copy to the old `out=$(yq ... 2>&1)`
# spelling left the suite at 959 passed / 0 failed.
#
# What `2>&1` corrupts is the SINGLE-VALUE reads specifically, and that is why
# each leg below is keyed on one. A list expression (`.exclude // [] | .[]`)
# merely gains a bogus FIRST ELEMENT that matches no repo name, so the
# exclusion list is not an observable at all; a scalar comes back as TWO LINES
# with the real answer second, and every consumer of it then reasons about the
# warning. Each needle here was checked both ways against these same fixtures:
#
#   drift-report.sh         `default_sections` decides the Sections cell of a
#                           repo with no `.agents-sync.yml`. Healthy: `rust`.
#                           Reverted: a cell reading `Flag --tojson has been
#                           deprecated, please use -o=json instead rust`,
#                           PUBLISHED into the report.
#   bump-consumer-locks.sh  `.skills_bootstrap.registry` decides which checkout
#                           is the registry. Healthy: `Bumping consumer locks
#                           onto bumporg/agentskills`, exit 0. Reverted:
#                           `ERROR: no checkout configured for Flag --tojson …`
#                           and exit 2, before any repo is looked at.
#   bump-hook-pin.sh        all four pins at once. Healthy: `current pin:
#                           pinorg/agentskills@… digest <sha12>` and an
#                           unchanged verdict. Reverted: `BUMP_CHECKOUTS names
#                           no path for Flag --tojson …` and exit 1.
#
# Sited here because this is the first point in the run where all three
# fixtures exist together: bumporg's registry checkout from setup_bump_repos,
# and $HOOK_PIN_DIR from test_hook_pin_unchanged immediately above — whose
# "nothing to do" state the dry run below re-reads and does not disturb.
test_repos_yml_scalars_under_noisy_yq() {
    echo ""
    echo "=== Test: read_repos_yml keeps yq's streams apart (drift, bumper, hook pin) ==="

    setup_noisy_yq_dir
    local noisy_bin="$TEST_DIR/bin-yq-noisy"
    local out rpt exit_code

    # ── drift-report.sh ──────────────────────────────────────────────────
    out="$TEST_DIR/noisy-yq-drift.txt"
    rpt="$TEST_DIR/noisy-yq-drift.md"
    exit_code=0
    GITHUB_REPOSITORY_OWNER=testorg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    REPOS_YML="$TEST_DIR/repos.yml" \
    DRIFT_REPORT_OUTPUT="$rpt" \
    PATH="$noisy_bin:$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/drift-report.sh" > "$out" 2>&1 || exit_code=$?

    # A WHOLE-CELL match, for the reason the sync-yml leg gives: the corrupted
    # cell still ENDS in `rust`, so a substring needle would pass against
    # exactly the output this leg exists to forbid.
    assert_row_contains "$rpt" "repo-no-sync" "rust" \
        "noisy yq (drift): default_sections is the section list and nothing else"
    assert_not_contains "$rpt" "Flag --tojson" \
        "noisy yq (drift): nothing yq wrote to stderr is published in the report"
    if [[ $exit_code -eq 0 ]]; then
        pass "noisy yq (drift): the run exits 0"
    else
        fail "noisy yq (drift): the run exits 0 — got $exit_code"
    fi

    # ── bump-consumer-locks.sh ───────────────────────────────────────────
    #
    # Invoked directly rather than through run_bump, and BUMP_REGISTRY is
    # deliberately left unset: the script reads the pin only when that variable
    # is empty (`BUMP_REGISTRY="${BUMP_REGISTRY:-}"`), and run_bump hardcodes
    # it — so riding that helper would short-circuit the one read this leg is
    # about. The repos.yml is derived from the shared fixture with that single
    # key repointed at the registry bumporg actually has a checkout for.
    local bump_yml="$TEST_DIR/repos-noisy-yq-bump.yml"
    sed 's|^  registry: bootorg/agentskills$|  registry: bumporg/agentskills|' \
        "$TEST_DIR/repos.yml" > "$bump_yml"
    # Asserted rather than assumed: a sed that matched nothing leaves the pin
    # naming a registry with no checkout, which fails the leg below for a
    # reason that has nothing to do with yq's stderr.
    if grep -qF "registry: bumporg/agentskills" "$bump_yml"; then
        pass "noisy yq (bumper): the derived repos.yml pins the registry bumporg holds"
    else
        fail "noisy yq (bumper): the derived repos.yml does not pin bumporg/agentskills, so the run below would fail for the wrong reason"
    fi

    out="$TEST_DIR/noisy-yq-bump.txt"
    GITHUB_REPOSITORY_OWNER=bumporg \
    MOCK_BARE_DIR="$TEST_DIR/bare" \
    MOCK_PR_LOG="$BUMP_PR_LOG" \
    MOCK_PR_BODY_DIR="$BUMP_PR_BODY_DIR" \
    REPOS_YML="$bump_yml" \
    BUMP_CHECKOUTS="$BUMP_CHECKOUTS_ARG" \
    PATH="$noisy_bin:$TEST_DIR/bin:$PATH" \
    "$REPO_ROOT/scripts/bump-consumer-locks.sh" --dry-run > "$out" 2>&1 || true

    # No exit-code assertion here, and that is deliberate rather than an
    # omission: bumporg carries `repo-error`, whose lock pins a ref that cannot
    # resolve, so a walk of that whole owner counts one failure and exits 1
    # whatever yq does. The discriminating fact is upstream of the walk — the
    # corrupted read never reaches a repo at all, because the two-line registry
    # matches no BUMP_CHECKOUTS entry and the script refuses at that point.
    assert_contains "$out" "Bumping consumer locks onto bumporg/agentskills" \
        "noisy yq (bumper): the registry pin is the bare OWNER/REPO"
    assert_not_contains "$out" "no checkout configured for" \
        "noisy yq (bumper): the registry it resolved is one the run has a checkout for"
    assert_not_contains "$out" "Flag --tojson" \
        "noisy yq (bumper): nothing yq wrote to stderr reached a value it acts on"

    # ── bump-hook-pin.sh ─────────────────────────────────────────────────
    #
    # The one of the four that reads all four pins, so the digest below is the
    # sharpest needle available: `${PIN_SHA256:0:12}` is a warning's first
    # twelve characters the moment the streams are folded together.
    local pinned_sha
    pinned_sha=$(hook_pin_hook_text "v1" | sha256sum | cut -d' ' -f1)
    out="$TEST_DIR/noisy-yq-hook-pin.txt"
    HOOK_PIN_PATH_PREFIX_FOR_RUN="$noisy_bin" run_hook_pin "$out" --dry-run
    unset HOOK_PIN_PATH_PREFIX_FOR_RUN

    assert_contains "$out" "current pin: pinorg/agentskills@" \
        "noisy yq (hook pin): the registry pin is the bare OWNER/REPO"
    assert_contains "$out" "digest ${pinned_sha:0:12}" \
        "noisy yq (hook pin): the sha256 pin is the bare digest"
    assert_contains "$out" "byte-identical to the pinned one" \
        "noisy yq (hook pin): the pins it read still describe the hook on disk"
    assert_not_contains "$out" "Flag --tojson" \
        "noisy yq (hook pin): nothing yq wrote to stderr reached a pin"
    if [[ $HOOK_PIN_EXIT -eq 0 ]]; then
        pass "noisy yq (hook pin): the run exits 0"
    else
        fail "noisy yq (hook pin): the run exits 0 — got $HOOK_PIN_EXIT"
    fi
}

# ── Test 9b: --dry-run decides and writes nothing ────────────────────────

test_hook_pin_dry_run() {
    echo ""
    echo "=== Test: bump-hook-pin.sh --dry-run ==="

    hook_pin_commit_hook "v2" "change the hook"

    local out="$TEST_DIR/hook-pin-dry.txt"
    run_hook_pin "$out" --dry-run

    assert_contains "$out" "The hook has CHANGED" "hook pin: the dry run notices the change"
    assert_contains "$out" "DRY RUN — would re-pin" "hook pin: the dry run says what it would do"
    assert_no_hook_pin_branch "hook pin: --dry-run pushes nothing"
    if [[ ! -s "$HOOK_PIN_PR_LOG" ]]; then
        pass "hook pin: --dry-run opens no pull request"
    else
        fail "hook pin: --dry-run opened a pull request"
    fi

    # FAILING CLOSED on the write side. `--dry-runn` must stop the run, not
    # leave DRY_RUN=false and go on to clone, commit and push under a token
    # that can do all three.
    out="$TEST_DIR/hook-pin-typo.txt"
    run_hook_pin "$out" --dry-runn
    if [[ $HOOK_PIN_EXIT -eq 2 ]]; then
        pass "hook pin: a mistyped --dry-run is refused, not treated as a live run"
    else
        fail "hook pin: a mistyped --dry-run exited $HOOK_PIN_EXIT, not 2 — $(cat "$out")"
    fi
    assert_no_hook_pin_branch "hook pin: the mistyped flag pushed nothing"

    # ── the listing that SUCCEEDS and still writes to stderr ─────────────
    #
    # This script's open-PR query is the sharpest instance of the whole class,
    # because it decides on the SHAPE of the answer rather than on the status:
    # `--jq '.[0].number // empty'` prints nothing and exits 0 when no pull
    # request is open, so EMPTY is the entire signal for "there is work to do".
    # gh writes to stderr while exiting 0 in ordinary conditions, and a merged
    # capture turns that emptiness into a PR number — after which the script
    # reports the bump as already proposed, prints its own success banner and
    # exits 0 having written nothing. Nothing goes red, scheduled-run-health
    # sees a success, and the pin never advances: the exact failure this script
    # exists to close, wearing the log of a healthy night.
    #
    # `--dry-run` because the decision under test happens before any write, and
    # this leg should not be the one that publishes a branch the next test
    # measures.
    out="$TEST_DIR/hook-pin-prlist-notice.txt"
    HOOK_PIN_PR_LIST_NOTICE_FOR_RUN="pinorg_guidance" \
        run_hook_pin "$out" --dry-run
    unset HOOK_PIN_PR_LIST_NOTICE_FOR_RUN

    assert_contains "$out" "DRY RUN — would re-pin" \
        "hook pin notice: a notice on gh's stderr does not become a PR number"
    assert_not_contains "$out" "already proposes a hook pin bump" \
        "hook pin notice: no phantom pull request is reported"
    assert_not_contains "$out" "already proposed" \
        "hook pin notice: the run does not exit through the no-op banner"
    if [[ $HOOK_PIN_EXIT -eq 0 ]]; then
        pass "hook pin notice: the run exits 0"
    else
        fail "hook pin notice: the run exits 0 — got $HOOK_PIN_EXIT: $(cat "$out")"
    fi
    assert_no_hook_pin_branch "hook pin notice: --dry-run still pushes nothing"
}

# ── Test 9c: the real bump — both files, and only those two lines ────────

test_hook_pin_proposes() {
    echo ""
    echo "=== Test: bump-hook-pin.sh (the hook changed) ==="

    local target_ref target_sha comments_before
    target_ref=$(hook_pin_registry_head)
    target_sha=$(hook_pin_hook_text "v2" | sha256sum | cut -d' ' -f1)
    comments_before=$(grep -c '^[[:space:]]*#' "$HOOK_PIN_DIR/work/repos.yml")

    local out="$TEST_DIR/hook-pin-propose.txt"
    run_hook_pin "$out"

    if [[ $HOOK_PIN_EXIT -eq 0 ]]; then
        pass "hook pin: the bump run exits 0"
    else
        fail "hook pin: the bump run exits 0 — got $HOOK_PIN_EXIT: $(cat "$out")"
    fi

    local branch_sha; branch_sha=$(hook_pin_branch_sha)
    if [[ -n "$branch_sha" ]]; then
        pass "hook pin: the bump branch was pushed"
    else
        fail "hook pin: the bump branch was pushed — $(cat "$out")"
        return
    fi

    local pushed_yml="$TEST_DIR/hook-pin-pushed.yml"
    hook_pin_file_at "$branch_sha" "repos.yml" > "$pushed_yml"

    assert_contains "$pushed_yml" "ref: $target_ref" "hook pin: ref moved to the registry's head"
    assert_contains "$pushed_yml" "sha256: $target_sha" "hook pin: sha256 is the digest of the bytes at that commit"

    # THE PIN AND THE SELF-HOSTED COPY IN ONE COMMIT. Compared as a digest
    # rather than by marker: the digest is the thing test 7b checks, so this
    # asserts the property that test actually enforces.
    local pushed_hook_sha
    pushed_hook_sha=$(hook_pin_file_at "$branch_sha" ".claude/hooks/skills-bootstrap.sh" | sha256sum | cut -d' ' -f1)
    if [[ "$pushed_hook_sha" == "$target_sha" ]]; then
        pass "hook pin: the self-hosted hook moved in the same commit, to the digest the pin now names"
    else
        fail "hook pin: the self-hosted hook hashes to ${pushed_hook_sha:0:12}… but the new pin says ${target_sha:0:12}… — test 7b would fail on this bump's own PR"
    fi

    # THE WRITE IS SURGICAL. Every comment line survives, and the old values
    # are gone rather than merely joined by the new ones.
    local comments_after; comments_after=$(grep -c '^[[:space:]]*#' "$pushed_yml")
    if [[ "$comments_after" == "$comments_before" ]]; then
        pass "hook pin: all $comments_before comment lines survived the write"
    else
        fail "hook pin: repos.yml went from $comments_before comment lines to $comments_after — the write re-serialised the document instead of patching two lines"
    fi
    assert_contains "$pushed_yml" "A long explanatory comment that must survive the write untouched." \
        "hook pin: the block's own explanatory comment is intact"
    assert_contains "$pushed_yml" "# A trailing block, after the one that moves." \
        "hook pin: the block after the edited one is intact"

    # Exactly two lines differ from the seed, and they are the two scalars.
    local changed
    changed=$(diff <(git -C "$HOOK_PIN_DIR/bare/pinorg_guidance" show main:repos.yml) "$pushed_yml" \
        | grep -c '^[<>]' || true)
    if [[ "$changed" == "4" ]]; then
        pass "hook pin: exactly two lines of repos.yml changed"
    else
        fail "hook pin: $((changed / 2)) lines of repos.yml changed, expected 2"
    fi

    if grep -q "pr-created" "$HOOK_PIN_PR_LOG"; then
        pass "hook pin: a pull request was opened"
    else
        fail "hook pin: no pull request was opened"
    fi

    # The body has to disclose the thing a reviewer cannot see in the diff:
    # that merging fans a hook into the whole allowlist.
    local body="$HOOK_PIN_BODY_DIR/target.body"
    if [[ -f "$body" ]]; then
        assert_contains "$body" "What merging this does" "hook pin: the body states what merging does"
        assert_contains "$body" "$target_ref" "hook pin: the body names the commit being pinned"
    else
        fail "hook pin: no PR body was captured at $body"
    fi
}

# ── Test 9c2: a failing `gh pr list` stops the run ───────────────────────
#
# `gh pr list ... 2>/dev/null || true` made "there is no open pull request" and
# "I could not ask" the same empty string — gh prints an HTTP error body to
# stdout and never runs the --jq filter. The refusal further down then stated
# the first as a FACT and printed a remedy for it: "Delete that branch (its PR
# is not open)". Deleting the head branch of an open pull request closes it on
# GitHub and discards whatever review was pending, so the wrong answer here is
# not merely wrong, it is an instruction to destroy the thing it misread.
test_hook_pin_pr_list_failure() {
    echo ""
    echo "=== Test: bump-hook-pin.sh (the open-PR query cannot be answered) ==="

    local out="$TEST_DIR/hook-pin-listfail.txt"
    local prs_before
    prs_before=$(wc -l < "$HOOK_PIN_PR_LOG")

    # ── The occupied branch the forbidden remedy is ABOUT, seeded for the
    # length of this test and removed again at the end.
    #
    # Without it the scenario cannot reach the refusal at all: nothing in this
    # lane has pushed a bump branch yet at this point, so the old spelling's
    # empty answer pushed cleanly, opened a pull request, and printed no remedy
    # — measured, and the needle below then matched nothing whatever the script
    # did, which is the shape of a guard that cannot fail. `occupied` means BOTH
    # halves of that arm's condition: a commit `main` does not contain, so the
    # push is a non-fast-forward, carrying a repos.yml that is NOT this run's
    # pin, so the adoption check above the refusal does not claim the branch
    # first.
    #
    # Torn down again because the next test in the lane pushes this same branch
    # name for real, and a stranger's commit parked on it would refuse that push
    # for a reason that test is not about.
    local bare="$HOOK_PIN_DIR/bare/pinorg_guidance"
    local occupied="$TEST_DIR/hook-pin-occupied" occupied_sha
    rm -rf "$occupied"
    git clone -q "$bare" "$occupied"
    git -C "$occupied" config commit.gpgsign false
    echo "somebody else's work, on the name this bumper wants" > "$occupied/NOTES.md"
    git -C "$occupied" add -A
    git -C "$occupied" -c user.name="A Human" -c user.email="human@example.com" \
        commit -q -m "a commit this bumper did not write"
    git -C "$occupied" push -q origin "HEAD:refs/heads/hook-pin-bump/update"
    occupied_sha=$(hook_pin_branch_sha)
    if [[ -z "$occupied_sha" ]]; then
        fail "pr-list failure: the occupied branch was not seeded, so the remedy below is unreachable"
        rm -rf "$occupied"
        return
    fi

    HOOK_PIN_PR_LIST_FAILS_FOR_RUN="pinorg_guidance" run_hook_pin "$out"

    assert_contains "$out" "could not list open pull requests" \
        "pr-list failure: the run says the question went unanswered"
    if [[ $HOOK_PIN_EXIT -ne 0 ]]; then
        pass "pr-list failure: the run exits non-zero"
    else
        fail "pr-list failure: the run exits non-zero (got 0): $(cat "$out")"
    fi
    # The two things the old spelling let it do on an unanswered question.
    # The remedy is read out of the script (see hook_pin_occupied_branch_remedy)
    # rather than re-typed, because the re-typed version of this needle had
    # already stopped matching anything the script prints.
    local occupied_remedy
    occupied_remedy=$(hook_pin_occupied_branch_remedy)
    if [[ -n "$occupied_remedy" ]]; then
        assert_not_contains "$out" "$occupied_remedy" \
            "pr-list failure: it does NOT advise deleting a branch whose PR it could not check"
    else
        fail "pr-list failure: could not read the occupied-branch remedy out of bump-hook-pin.sh, so this assertion has no needle"
    fi
    # Not assert_no_hook_pin_branch: a branch legitimately exists now, and the
    # claim is that this run left it exactly as it found it.
    if [[ "$(hook_pin_branch_sha)" == "$occupied_sha" ]]; then
        pass "pr-list failure: the occupied branch was left exactly as it was"
    else
        fail "pr-list failure: the occupied branch was written to — $occupied_sha -> $(hook_pin_branch_sha)"
    fi
    if [[ "$(wc -l < "$HOOK_PIN_PR_LOG")" == "$prs_before" ]]; then
        pass "pr-list failure: no pull request opened"
    else
        fail "pr-list failure: no pull request opened — $(cat "$HOOK_PIN_PR_LOG")"
    fi

    git -C "$bare" update-ref -d "refs/heads/hook-pin-bump/update"
    rm -rf "$occupied"
}

# ── Test 9c3: a branch that outlived its pull request is ADOPTED ──────────
#
# The permanent-and-invisible state, and the one combination this script may
# not produce. One night the branch pushes and `gh pr create` dies — a
# transient 5xx, a rate limit, an App holding Contents:write but not Pull
# requests:write — so the branch exists and the pull request does not. The same
# dead end is reached with no failure at all if somebody CLOSES the bump PR
# without deleting its branch.
#
# From there the default branch still carries the OLD pin, so every later run
# gets past anti-churn, finds no open pull request, rebuilds the same commit on
# the same parent with only a later committer timestamp, and pushes a
# non-fast-forward onto its own already-correct branch. The old arm logged a
# WARN and exited 0 — green every night, so scheduled-run-health never fires,
# while all ten allowlisted repos keep delivering the pre-change hook. Nothing
# else reaps this branch: sync.sh's stale-branch cleanup is scoped to
# `agents-md-sync/update`.
#
# This test needs no fixture of its own — it is exactly the state
# test_hook_pin_proposes leaves behind, read with no MOCK_PR_DIR, which is what
# makes it the state a real run lands in.
test_hook_pin_orphaned_branch() {
    echo ""
    echo "=== Test: bump-hook-pin.sh (a branch whose PR never opened) ==="

    local out="$TEST_DIR/hook-pin-orphan.txt"
    local before prs_before
    before=$(hook_pin_branch_sha)
    prs_before=$(grep -c "pr-created" "$HOOK_PIN_PR_LOG" || true)

    if [[ -z "$before" ]]; then
        fail "orphaned branch: no bump branch exists, so this test asserts nothing"
        return
    fi

    # THE REBUILT COMMIT GETS A FIXED DATE, and that is what makes four of the
    # five assertions below able to fail at all.
    #
    # bump-hook-pin.sh rebuilds the same tree on the same parent with the same
    # message under a fixed bot identity, so the ONLY thing separating this
    # run's commit from the one test_hook_pin_proposes pushed is the timestamp
    # — and two runs landing in the same wall-clock second produce the SAME
    # SHA. When they do, the push reports "Everything up-to-date" and succeeds,
    # `gh pr create` runs, and the branch sha is unchanged: the run exits 0, a
    # pull request appears, the branch was not rewritten, and nothing was
    # mistaken for a stranger's branch — every assertion here except the
    # "adopting that branch" one passes over a script with the adoption arm
    # REVERTED. Pinned to a date in the past, the rebuilt commit can never
    # collide with one written at today's clock, so a reverted script really
    # does attempt the non-fast-forward push this test exists to watch it not
    # need.
    local pinned_date="2020-01-01T00:00:00+0000"
    GIT_AUTHOR_DATE="$pinned_date" GIT_COMMITTER_DATE="$pinned_date" \
        run_hook_pin "$out"

    # And the precondition stated rather than assumed: the tip this run had to
    # recognise was written at a different date, so "the branch was reused, not
    # re-pushed" below cannot be satisfied by two commits that hash the same.
    local tip_date
    tip_date=$(git -C "$HOOK_PIN_DIR/bare/pinorg_guidance" \
        log -1 --format=%cI "refs/heads/hook-pin-bump/update" 2>/dev/null || true)
    if [[ -n "$tip_date" && "$tip_date" != "2020-01-01T00:00:00+00:00" ]]; then
        pass "orphaned branch: the rebuilt commit cannot collide with the branch tip"
    else
        fail "orphaned branch: branch tip is dated '$tip_date' — a sha collision would satisfy the assertions below"
    fi

    if [[ $HOOK_PIN_EXIT -eq 0 ]]; then
        pass "orphaned branch: the run exits 0"
    else
        fail "orphaned branch: the run exits 0 — got $HOOK_PIN_EXIT: $(cat "$out")"
    fi
    assert_contains "$out" "adopting that branch rather than pushing again" \
        "orphaned branch: it recognises the branch as its own proposal"

    # THE POINT: the run must REACH `gh pr create`. Without that it is merely a
    # politer version of the same stall.
    local prs_after
    prs_after=$(grep -c "pr-created" "$HOOK_PIN_PR_LOG" || true)
    if [[ "$prs_after" -gt "$prs_before" ]]; then
        pass "orphaned branch: the missing pull request was finally opened"
    else
        fail "orphaned branch: still no pull request — $prs_before before, $prs_after after"
    fi

    # And it adopted rather than rewrote: the branch this run reused is the one
    # the previous run pushed, byte for byte.
    if [[ "$(hook_pin_branch_sha)" == "$before" ]]; then
        pass "orphaned branch: the existing branch was reused, not re-pushed"
    else
        fail "orphaned branch: the branch was rewritten — $before -> $(hook_pin_branch_sha)"
    fi
    assert_not_contains "$out" "refusing to force-push over it" \
        "orphaned branch: it is NOT mistaken for a stranger's branch"
}

# ── Test 9d: it does not propose the same bump twice ─────────────────────
#
# Without this the script goes red every night a pin PR waits for review: the
# anti-churn test compares against the DEFAULT branch, which an open PR has not
# changed, so a second run rebuilds the same commit with a later timestamp and
# pushes a non-fast-forward onto its own branch.

test_hook_pin_already_proposed() {
    echo ""
    echo "=== Test: bump-hook-pin.sh (a bump PR is already open) ==="

    local before; before=$(hook_pin_branch_sha)
    mkdir -p "$TEST_DIR/hook-pin-prs"
    cat > "$TEST_DIR/hook-pin-prs/pinorg_guidance.json" <<'JSON'
[{"number": 77, "headRefName": "hook-pin-bump/update"}]
JSON

    local out="$TEST_DIR/hook-pin-open.txt"
    HOOK_PIN_PR_DIR_FOR_RUN="$TEST_DIR/hook-pin-prs" run_hook_pin "$out"

    if [[ $HOOK_PIN_EXIT -eq 0 ]]; then
        pass "hook pin: a run with a PR already open exits 0"
    else
        fail "hook pin: a run with a PR already open exits 0 — got $HOOK_PIN_EXIT: $(cat "$out")"
    fi
    assert_contains "$out" "PR #77 already proposes a hook pin bump" \
        "hook pin: it names the open PR rather than pushing again"
    if [[ "$(hook_pin_branch_sha)" == "$before" ]]; then
        pass "hook pin: the existing bump branch was left exactly as it was"
    else
        fail "hook pin: the bump branch was rewritten while a PR was open on it"
    fi
}

# ── Test 9e: it refuses a hook it cannot vouch for ───────────────────────

test_hook_pin_refuses_broken_hook() {
    echo ""
    echo "=== Test: bump-hook-pin.sh (the hook at HEAD does not parse) ==="

    local before; before=$(hook_pin_branch_sha)
    # Valid UTF-8, obviously a shell script, and unparseable: an unterminated
    # `if`. `bash -n` is the only thing between this and every allowlisted
    # repo running it at session start.
    printf '#!/usr/bin/env bash\nif [ -z "$x" ]; then\n  echo broken\n' \
        > "$HOOK_PIN_DIR/registry/.claude/hooks/skills-bootstrap.sh"
    git -C "$HOOK_PIN_DIR/registry" add -A
    git -C "$HOOK_PIN_DIR/registry" commit -q -m "a hook that does not parse"

    local out="$TEST_DIR/hook-pin-broken.txt"
    run_hook_pin "$out"

    if [[ $HOOK_PIN_EXIT -ne 0 ]]; then
        pass "hook pin: a hook that does not parse fails the run"
    else
        fail "hook pin: a hook that does not parse was accepted — $(cat "$out")"
    fi
    assert_contains "$out" "does not parse as bash" "hook pin: it says why it refused"
    if [[ "$(hook_pin_branch_sha)" == "$before" ]]; then
        pass "hook pin: nothing was pushed for the unparseable hook"
    else
        fail "hook pin: it pushed a branch for a hook that does not parse"
    fi

    # An empty hook is the other shape a digest is equally happy to record.
    : > "$HOOK_PIN_DIR/registry/.claude/hooks/skills-bootstrap.sh"
    git -C "$HOOK_PIN_DIR/registry" add -A
    git -C "$HOOK_PIN_DIR/registry" commit -q -m "an empty hook"
    out="$TEST_DIR/hook-pin-empty.txt"
    run_hook_pin "$out"
    if [[ $HOOK_PIN_EXIT -ne 0 ]]; then
        pass "hook pin: an empty hook fails the run"
    else
        fail "hook pin: an empty hook was accepted — $(cat "$out")"
    fi
    assert_contains "$out" "is empty at" "hook pin: it says the hook was empty"
}

# ── Test 9f: the workflow really runs it ─────────────────────────────────
#
# The script above can be perfect and deliver nothing if no workflow calls it —
# which is precisely the failure ADR 0010 exists to close, one level up. This
# asserts the wiring rather than trusting it.

test_hook_pin_workflow_wiring() {
    echo ""
    echo "=== Test: skills-lock-bump.yml runs the hook-pin bumper ==="

    local wf="$REPO_ROOT/.github/workflows/skills-lock-bump.yml"
    if [[ ! -f "$wf" ]]; then
        fail "hook pin wiring: $wf is missing"
        return
    fi
    # PARSED, NOT GREPPED, and the reason is recorded on workflow_step_by_run:
    # the first version of this test grepped for `if: success() || failure()`
    # and passed with that key deleted, matching the comment that quotes it.
    local facts="$TEST_DIR/hook-pin-wiring.txt" err="$TEST_DIR/hook-pin-wiring.err"
    if ! workflow_step_by_run "$wf" "scripts/bump-hook-pin.sh" > "$facts" 2> "$err"; then
        fail "hook pin wiring: could not parse $wf — $(head -1 "$err")"
        return
    fi
    if grep -qxF "found" "$facts"; then
        pass "hook pin wiring: the nightly workflow has a step that runs the script"
    else
        fail "hook pin wiring: no step in $wf runs scripts/bump-hook-pin.sh — the script exists and nothing calls it, which is the failure ADR 0010 exists to close"
        return
    fi

    # The step must survive a failure in the lock pass: the two lanes share a
    # checkout and a token and nothing else, so an owner whose consumer bump
    # failed is no reason to leave the hook pin stale another day. `always()`
    # is NOT acceptable — it would also run after a cancellation, which is the
    # one case where someone has deliberately stopped the run mid-flight.
    local step_if; step_if=$(awk '$1 == "if" { $1 = ""; sub(/^ /, ""); print }' "$facts")
    if [[ "$step_if" == "success() || failure()" ]]; then
        pass "hook pin wiring: the step's if: is success() || failure()"
    else
        fail "hook pin wiring: the step's if: is '${step_if:-nothing}', not 'success() || failure()' — a failed lock pass would freeze the hook pin indefinitely, and always() would push after a cancellation"
    fi

    # `${{ inputs.* }}` reaches the script through the environment, never
    # interpolated into the `run:` body — Actions echoes the rendered command
    # to a log this account treats as public (AGENTS.md, "Data exposure in CI
    # and public repos").
    if grep -qxF "env DRY_RUN" "$facts"; then
        pass "hook pin wiring: the dry-run input is passed through the environment"
    else
        fail "hook pin wiring: the step declares no DRY_RUN env key — the input is reaching the script some other way"
    fi
    if grep -qxF "interpolates" "$facts"; then
        fail "hook pin wiring: the run: body interpolates a \${{ }} expression — it is rendered into the shell line Actions echoes to the log"
    else
        pass "hook pin wiring: the run: body interpolates nothing"
    fi
}

# ── Test 10: a yq that cannot answer stops every script that reads repos.yml
#
# `yq ... 2>/dev/null || true` collapsed three answers into one: the key is
# legitimately absent, repos.yml will not parse, and yq is not there at all.
# Only the first is normal, and the other two arrived as an EMPTY LIST, exit 0,
# and not one line in the log. An empty exclusion list is not an inert one —
# sync.sh's filter matches nothing, and the run clones a repo repos.yml
# EXCLUDES and pushes the managed AGENTS.md straight to its default branch,
# which is the contamination that exclusion exists to prevent.
#
# Two flavours, because the second is what actually happened. mikefarah's Go yq
# is what this repo's workflows install version-pinned; kislyuk's Python
# jq-wrapper is what Debian ships under the same name, and it forwards
# `-o=json` to jq and dies with "jq: Unknown option -o=json" — a message that
# blames repos.yml for a tool problem, and the message the nightly hook-pin
# bump died with. So the stub below is not a generic "broken command": it is
# that flavour, answering `--version` perfectly happily, which is exactly why a
# version probe would not have caught it.
#
# The ABSENT case gets a symlink farm rather than a shell trick because
# `command -v` searches PATH and there is no way to shadow a name into
# non-existence. The farm holds only the handful of tools each script uses
# BEFORE its preflight, which is all it needs: the run is supposed to stop
# there, and a farm that let it get further would be testing something else.
setup_yq_stub_dirs() {
    # The wrong flavour.
    mkdir -p "$TEST_DIR/bin-yq-python"
    cat > "$TEST_DIR/bin-yq-python/yq" <<'STUB'
#!/bin/sh
# Stands in for kislyuk's python-yq: a real program, correctly installed,
# answering --version without complaint, that does not understand the one flag
# this repo's repos.yml reads depend on.
case "${1:-}" in
    --version) echo "yq 3.4.3"; exit 0 ;;
esac
for arg in "$@"; do
    case "$arg" in
        -o=json)
            echo "jq: Unknown option -o=json" >&2
            echo "Use jq --help for help with command-line options," >&2
            exit 1
            ;;
    esac
done
exit 0
STUB
    chmod +x "$TEST_DIR/bin-yq-python/yq"

    # No yq at all. Only what runs before the preflight, resolved from the live
    # PATH rather than hardcoded to /usr/bin, so this works on any runner.
    mkdir -p "$TEST_DIR/bin-noyq"
    local tool resolved
    for tool in sh bash env basename dirname git mktemp python3 sed date cat \
                grep head cut tr sort wc rm mkdir cp ls sha256sum base64 jq diff; do
        resolved=$(command -v "$tool" 2>/dev/null) || continue
        ln -sf "$resolved" "$TEST_DIR/bin-noyq/$tool"
    done
}

# ── A yq that WORKS and still writes to stderr ────────────────────────────
#
# The third flavour, and the one the two above cannot stand in for: both of
# them exist to be REFUSED, and every assertion about them is about a run that
# stopped. This one answers correctly, exits 0, and writes one line to stderr
# while doing it — which is the condition under which folding the two streams
# together corrupts a value nobody ever looks at twice.
#
# It is not a hypothetical. The mikefarah yq this repo pins does exactly this:
# measured on this box with v4.44.3,
# `printf 'sections:\n  - python\n' | yq -j -r '.sections // [] | .[]'`
# writes `python` to stdout, `Flag --tojson has been deprecated, please use
# -o=json instead` to stderr, and exits 0. That line is what the stub prints,
# verbatim — on EVERY call rather than only on the ones that pass `-j`, because
# what is being tested is each capture site's handling of a noisy success and
# not yq's own flag parsing.
#
# It delegates to the real yq rather than faking an answer, and the real yq is
# resolved ONCE here and baked in as an absolute path: resolved from inside the
# stub it would find itself, because this directory goes on the front of PATH.
setup_noisy_yq_dir() {
    local real
    real=$(command -v yq) || return 1
    mkdir -p "$TEST_DIR/bin-yq-noisy"
    cat > "$TEST_DIR/bin-yq-noisy/yq" <<STUB
#!/bin/sh
# A correct yq that is not a silent one. See setup_noisy_yq_dir.
echo "Flag --tojson has been deprecated, please use -o=json instead" >&2
exec "$real" "\$@"
STUB
    chmod +x "$TEST_DIR/bin-yq-noisy/yq"
}

# ── A python3 that WORKS and still writes to stderr ───────────────────────
#
# The same third flavour as setup_noisy_yq_dir, for the other producer the
# bumper captures from. Three of its captures are fed by a LOCAL `python3 -c`
# rather than by gh — the lock-plan reader, the merge-gate verdict and the
# shrink check — and python writes to stderr while exiting 0 whenever the
# INHERITED environment asks it to. Measured on this box, `PYTHONVERBOSE=1
# python3 -c pass` exits 0 having written several hundred lines to stderr;
# `PYTHONWARNINGS=always` is the same shape with fewer lines.
#
# A wrapper is used rather than setting PYTHONVERBOSE for the run because the
# real variable would also flood the stub generator's own captures with
# hundreds of lines and turn a targeted test into a test of everything at once.
# One line, on every call, is the same hazard at a size the log can show.
setup_noisy_python_dir() {
    local real
    real=$(command -v python3) || return 1
    mkdir -p "$TEST_DIR/bin-py-noisy"
    cat > "$TEST_DIR/bin-py-noisy/python3" <<STUB
#!/bin/sh
# A correct python3 that is not a silent one. See setup_noisy_python_dir.
echo "sys:1: DeprecationWarning: an inherited-environment diagnostic" >&2
exec "$real" "\$@"
STUB
    chmod +x "$TEST_DIR/bin-py-noisy/python3"
}

test_yq_preflight() {
    echo ""
    echo "=== Test: every repos.yml reader refuses a yq it cannot use ==="

    setup_yq_stub_dirs

    local script exit_code out fingerprint_before fingerprint_after
    fingerprint_before=$(bare_fleet_fingerprint)

    for script in sync.sh drift-report.sh bump-consumer-locks.sh bump-hook-pin.sh; do
        # ── WRONG FLAVOUR
        out="$TEST_DIR/yq-flavour-${script%.sh}.txt"
        exit_code=0
        GITHUB_REPOSITORY_OWNER=testorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        BUMP_REGISTRY="bumporg/agentskills" \
        HOOK_PIN_REPO="pinorg/guidance" \
        PATH="$TEST_DIR/bin-yq-python:$TEST_DIR/bin:$PATH" \
        "$REPO_ROOT/scripts/$script" > "$out" 2>&1 || exit_code=$?

        if [[ $exit_code -eq 2 ]]; then
            pass "yq flavour ($script): refuses the run (exit 2)"
        else
            fail "yq flavour ($script): refuses the run (exit 2) — got $exit_code: $(head -3 "$out")"
        fi
        assert_contains "$out" "does not accept '-o=json -I0'" \
            "yq flavour ($script): names the flag it probed"
        assert_contains "$out" "yq 3.4.3" \
            "yq flavour ($script): quotes what the yq on PATH actually says"
        assert_contains "$out" "mikefarah" \
            "yq flavour ($script): names the yq this repo requires"

        # ── ABSENT
        out="$TEST_DIR/yq-absent-${script%.sh}.txt"
        exit_code=0
        GITHUB_REPOSITORY_OWNER=testorg \
        MOCK_BARE_DIR="$TEST_DIR/bare" \
        REPOS_YML="$TEST_DIR/repos.yml" \
        BUMP_REGISTRY="bumporg/agentskills" \
        HOOK_PIN_REPO="pinorg/guidance" \
        PATH="$TEST_DIR/bin-noyq" \
        "$REPO_ROOT/scripts/$script" > "$out" 2>&1 || exit_code=$?

        if [[ $exit_code -eq 2 ]]; then
            pass "yq absent ($script): refuses the run (exit 2)"
        else
            fail "yq absent ($script): refuses the run (exit 2) — got $exit_code: $(head -3 "$out")"
        fi
        assert_contains "$out" "yq is not on PATH" \
            "yq absent ($script): says which tool is missing"
    done

    # THE CONSEQUENCE, which is what the exit code is protecting against. With
    # an empty exclusion list sync.sh clones and pushes to `repo-excluded`; both
    # runs above must have stopped before discovery ever happened.
    assert_not_contains "$TEST_DIR/yq-flavour-sync.txt" "Scanning repos for" \
        "yq flavour (sync.sh): never reached repo discovery"
    assert_not_contains "$TEST_DIR/yq-absent-sync.txt" "Scanning repos for" \
        "yq absent (sync.sh): never reached repo discovery"
    assert_not_contains "$TEST_DIR/yq-flavour-sync.txt" "repo-excluded" \
        "yq flavour (sync.sh): the excluded repo was never touched"

    fingerprint_after=$(bare_fleet_fingerprint)
    if [[ "$fingerprint_before" == "$fingerprint_after" ]]; then
        pass "yq preflight: eight refused runs wrote nothing to the fleet"
    else
        fail "yq preflight: the fleet was written to by a run with a broken yq"
    fi
}

# ── Test 7k: the helpers the four repos.yml readers each carry a copy of ───
#
# `read_repos_yml` and `pick_diagnostic` are duplicated VERBATIM in sync.sh,
# drift-report.sh, bump-consumer-locks.sh and bump-hook-pin.sh, for the reason
# the yq-preflight comment gives at length: all four are standalone entry
# points, only one of them sources anything today, and a library existing to
# hold two functions would be an abstraction invented for them. The cost of
# that decision is skew, and skew here is silent — an edit that lands in three
# copies and not the fourth changes nothing any other test observes, so it
# ships green and the fourth script keeps the old behaviour until somebody
# reads it.
#
# Nothing asserted this before. `test_yq_install_pinned` above is the only
# other "all four copies are identical" check in the suite and it is about a
# different thing entirely — the yq INSTALL step in four workflow files — so
# the sentence "the four copies must move together", written three times in
# the scripts themselves, was enforced by nobody.
#
# This test compares each file against the others and knows nothing about what
# the helpers should CONTAIN: a deliberate change to `read_repos_yml` is meant
# to fail here once, in the copies not yet moved, and pass as soon as all four
# carry it. The behaviour those helpers must have is asserted elsewhere —
# test_sync_agents_sync_yml_unreadable's noisy-yq leg and
# test_repos_yml_scalars_under_noisy_yq for the stream separation,
# test_yq_preflight for the refusal.
test_shared_repos_yml_helpers_are_identical() {
    echo ""
    echo "=== Test: the helpers duplicated across the four repos.yml readers agree ==="

    local -a readers=(sync.sh drift-report.sh bump-consumer-locks.sh bump-hook-pin.sh)
    local -a digests names
    local helper script body found min_lines n i mismatched

    for helper in read_repos_yml pick_diagnostic; do
        digests=(); names=(); found=0; min_lines=""
        for script in "${readers[@]}"; do
            body=$(sed -n "/^${helper}() {/,/^}/p" "$REPO_ROOT/scripts/$script")
            [[ -n "$body" ]] || continue
            found=$((found + 1))
            n=$(printf '%s\n' "$body" | wc -l)
            [[ -z "$min_lines" || $n -lt $min_lines ]] && min_lines=$n
            names+=("$script")
            digests+=("$(printf '%s\n' "$body" | sha256sum | cut -d' ' -f1)")
        done

        # VACUITY GUARD, and this test needs one more than most: the whole
        # assertion below is "these strings are equal", and the EMPTY STRING
        # equals itself four times over. `sed -n '/^f() {/,/^}/p'` yields
        # nothing at all for a file that has no such function — rename the
        # helper, or break the pattern, and the comparison reports perfect
        # agreement about four things it never read. So establish first that
        # four bodies came back and that none is a stub. The real bodies are
        # tens of lines — 29 and 25 when this was written, and they move as the
        # helpers grow — so the floor is deliberately set far below any
        # legitimate body and far above a degenerate one-line match, rather
        # than at a number that would need maintaining.
        if [[ $found -eq 4 && ${min_lines:-0} -ge 5 ]]; then
            pass "$helper: extracted all 4 copies, shortest body $min_lines lines"
        else
            fail "$helper: extracted $found copies (want 4), shortest ${min_lines:-0} lines (want >= 5) — the comparison below would be vacuous, so it was skipped"
            continue
        fi

        mismatched=""
        for ((i = 1; i < ${#digests[@]}; i++)); do
            [[ "${digests[$i]}" == "${digests[0]}" ]] \
                || mismatched="${mismatched:+$mismatched, }${names[$i]}"
        done
        if [[ -z "$mismatched" ]]; then
            pass "$helper: all 4 copies are byte-identical"
        else
            fail "$helper: ${names[0]}'s copy differs from $mismatched — these four are duplicated verbatim on purpose and must move together; port the change to every copy in the same commit"
        fi
    done
}

# ── Test 11: the Dependabot sweep's listing failure is not an empty sweep ──
#
# Extracted from the workflow and RUN, rather than asserted about as text. The
# step is 300 lines of ordinary bash with no `${{ }}` in it (checked below, so
# this stays true if someone adds one), which makes executing it the honest
# test: what is under test is control flow — that a failed `gh pr list` reaches
# `exit 1` instead of falling into the branch that prints "No open Dependabot
# PRs — nothing to sweep" and exits 0.
#
# That branch is why this matters more here than the shape usually does. This
# sweep is the only path that lands a Dependabot PR unattended on this repo,
# and the scheduled-run-health audit scans only for failure / startup_failure /
# timed_out — so an exit-0 sweep is invisible to the one alarm watching these
# crons. A listing failing for weeks would stall the whole dependency pipeline
# with no signal at all.
workflow_run_body() {   # <file> <needle> — the first job step's `run:` body
    if [[ ! -d "$REPO_ROOT/node_modules/yaml" ]]; then
        echo "node_modules/yaml is missing — run \`npm ci\` first" >&2
        return 1
    fi
    node -e '
const fs = require("node:fs");
const YAML = require(process.argv[1] + "/node_modules/yaml");
const doc = YAML.parse(fs.readFileSync(process.argv[2], "utf8"));
const needle = process.argv[3];
for (const spec of Object.values((doc && doc.jobs) || {})) {
  for (const step of (spec && spec.steps) || []) {
    if (typeof step.run !== "string" || !step.run.includes(needle)) continue;
    process.stdout.write(step.run);
    process.exit(0);
  }
}
process.exit(1);
' "$REPO_ROOT" "$1" "$2"
}

test_dependabot_sweep_list_failure() {
    echo ""
    echo "=== Test: dependabot-auto-merge.yml (the sweep's PR listing fails) ==="

    local wf="$REPO_ROOT/.github/workflows/dependabot-auto-merge.yml"
    local body="$TEST_DIR/sweep-step.sh"

    if ! workflow_run_body "$wf" 'app/dependabot' > "$body" 2>"$TEST_DIR/sweep-extract.err"; then
        fail "sweep: could not extract the sweep step — $(cat "$TEST_DIR/sweep-extract.err")"
        return
    fi
    # Executing it is only valid while it is plain bash. A `${{ }}` would be a
    # syntax error here rather than a wrong answer, but say so plainly instead
    # of letting the shell report it.
    if grep -qF '${{' "$body"; then
        fail "sweep: the step now interpolates \${{ }} — extract-and-run no longer models it"
        return
    fi
    if ! bash -n "$body" 2>"$TEST_DIR/sweep-syntax.err"; then
        fail "sweep: the extracted step is not valid bash — $(head -1 "$TEST_DIR/sweep-syntax.err")"
        return
    fi

    # A `gh` whose `pr list` cannot answer: the diagnostic goes to stderr and
    # nothing usable to stdout, which is what real gh does and what made "no
    # open PRs" and "I could not ask" the same empty string.
    mkdir -p "$TEST_DIR/bin-sweepfail"
    cat > "$TEST_DIR/bin-sweepfail/gh" <<'GHSTUB'
#!/usr/bin/env bash
if [[ "${1:-}" == "pr" && "${2:-}" == "list" ]]; then
    echo "gh: Bad credentials (HTTP 401)" >&2
    exit 1
fi
echo "mock gh: unexpected call: $*" >&2
exit 1
GHSTUB
    chmod +x "$TEST_DIR/bin-sweepfail/gh"

    local out="$TEST_DIR/sweep-out.txt" exit_code=0
    PATH="$TEST_DIR/bin-sweepfail:$PATH" bash "$body" > "$out" 2>&1 || exit_code=$?

    if [[ $exit_code -ne 0 ]]; then
        pass "sweep: a failed PR listing exits non-zero"
    else
        fail "sweep: a failed PR listing exits non-zero (got 0): $(cat "$out")"
    fi
    assert_contains "$out" "could not list open Dependabot PRs" \
        "sweep: it says the listing failed"
    # Scoped to the step's own `::error::` line, for the reason above
    # assert_scoped_line: the status is the stub `gh`'s output, and what is
    # under test is the step repeating it, not its mere presence in the log.
    assert_scoped_line "$out" "could not list open Dependabot PRs" "HTTP 401" \
        "sweep: it quotes gh's own reason"
    assert_not_contains "$out" "No open Dependabot PRs" \
        "sweep: it does NOT report an empty sweep it never established"

    # The negative control, and it is what makes the assertions above mean
    # anything: with a `gh pr list` that ANSWERS with an empty list, the very
    # same script must take the quiet branch and exit 0. Without this, a script
    # that failed unconditionally would pass every assertion above.
    mkdir -p "$TEST_DIR/bin-sweepempty"
    cat > "$TEST_DIR/bin-sweepempty/gh" <<'GHSTUB'
#!/usr/bin/env bash
if [[ "${1:-}" == "pr" && "${2:-}" == "list" ]]; then
    exit 0
fi
echo "mock gh: unexpected call: $*" >&2
exit 1
GHSTUB
    chmod +x "$TEST_DIR/bin-sweepempty/gh"

    local ctl="$TEST_DIR/sweep-control.txt" ctl_code=0
    PATH="$TEST_DIR/bin-sweepempty:$PATH" bash "$body" > "$ctl" 2>&1 || ctl_code=$?

    if [[ $ctl_code -eq 0 ]]; then
        pass "sweep (control): a genuinely empty listing still exits 0"
    else
        fail "sweep (control): a genuinely empty listing still exits 0 — got $ctl_code: $(cat "$ctl")"
    fi
    assert_contains "$ctl" "No open Dependabot PRs" \
        "sweep (control): an empty listing takes the quiet branch"
    # And the emptiness guard itself: `mapfile <<<""` yields ONE empty element,
    # which would make a genuinely empty list into a bogus PR "number".
    assert_not_contains "$ctl" "Open Dependabot PR(s):" \
        "sweep (control): an empty listing did not become one nameless PR"
}


# ── fleet-memory.sh ────────────────────────────────────────────────────────
#
# The hook that replaced the per-repo managed block with a single user-memory
# copy. What these guard is not "does it write a file" but the three ways a
# delivery like this goes wrong quietly:
#
#   * it CLOBBERS a developer's own ~/.claude/CLAUDE.md (their file, not ours);
#   * it GROWS the file a little on every session until someone notices;
#   * it FAILS SILENTLY, leaving a session with no fleet guidance and nothing
#     on screen to say so — the one outcome the stub-plus-verdict design exists
#     to prevent.
#
# Every failure path is asserted to exit 0 AND to print a DEGRADED line: a hook
# that breaks the session is worse than one that degrades, and a degradation
# nobody can see is worse than either.
test_fleet_memory_hook() {
    echo ""
    echo "TEST: fleet-memory.sh (user-memory delivery)"

    local hook="$REPO_ROOT/.claude/hooks/fleet-memory.sh"
    local d="$TEST_DIR/fleetmem"
    mkdir -p "$d/cfg"
    local payload="$d/payload.md"
    printf '# Fleet guidance\n\nThe canary is TEAL-HERON-31.\n' > "$payload"

    # CLAUDE_CONFIG_DIR is the hook's own seam for this; overriding HOME would
    # also move git's config and make the failure modes ambiguous.
    run_hook() { CLAUDE_CONFIG_DIR="$d/cfg" FLEET_GUIDANCE_PAYLOAD="$payload" bash "$hook" 2>&1; }

    local dest="$d/cfg/CLAUDE.md"
    local out

    # 1. Fresh install.
    rm -f "$dest"
    out="$(run_hook)"; local rc=$?
    printf '%s' "$out" > "$d/out1"
    [[ $rc -eq 0 ]] && pass "fleet-memory: fresh run exits 0" || fail "fleet-memory: fresh run exit $rc"
    assert_contains "$d/out1" "fleet-guidance: installed" "fleet-memory: fresh run reports installed"
    assert_contains "$dest" "TEAL-HERON-31" "fleet-memory: payload reaches user memory"

    # The file must not OPEN with a blank line: the first draft did exactly
    # that whenever the destination existed but stripped to nothing, and a
    # cosmetic wart at the top of the user's global memory is the kind of thing
    # that gets "fixed" by deleting the whole file.
    if [[ -s "$dest" ]] && [[ -n "$(head -1 "$dest")" ]]; then
        pass "fleet-memory: no leading blank line"
    else
        fail "fleet-memory: file starts with a blank line"
    fi

    # 2. Idempotence — byte-identical, and SAYS so rather than rewriting.
    local before_sum; before_sum="$(sha256sum "$dest" | cut -d' ' -f1)"
    out="$(run_hook)"; printf '%s' "$out" > "$d/out2"
    assert_contains "$d/out2" "fleet-guidance: current" "fleet-memory: second run reports current"
    local after_sum; after_sum="$(sha256sum "$dest" | cut -d' ' -f1)"
    [[ "$before_sum" == "$after_sum" ]] && pass "fleet-memory: idempotent (bytes unchanged)" \
        || fail "fleet-memory: second run changed the file"

    # 3. A developer's own content survives, and survives REPEATEDLY.
    printf 'MY OWN NOTE — must survive.\n' > "$dest"
    run_hook >/dev/null
    assert_contains "$dest" "MY OWN NOTE" "fleet-memory: foreign content preserved"
    assert_contains "$dest" "TEAL-HERON-31" "fleet-memory: block added alongside foreign content"

    # 4. No unbounded growth, and no duplicate blocks. Five runs, because the
    #    growth bug this guards added ONE line per run — a single re-run would
    #    have looked clean.
    local size_a; size_a="$(wc -c < "$dest")"
    local i; for i in 1 2 3 4 5; do run_hook >/dev/null; done
    local size_b; size_b="$(wc -c < "$dest")"
    [[ "$size_a" -eq "$size_b" ]] && pass "fleet-memory: file does not grow across runs" \
        || fail "fleet-memory: file grew ${size_a} -> ${size_b} over five runs"
    local nbegin nend
    nbegin="$(grep -c 'BEGIN FLEET GUIDANCE' "$dest" || true)"
    nend="$(grep -c 'END FLEET GUIDANCE' "$dest" || true)"
    [[ "$nbegin" -eq 1 && "$nend" -eq 1 ]] && pass "fleet-memory: exactly one managed block" \
        || fail "fleet-memory: found $nbegin BEGIN / $nend END markers"

    # 5. A changed payload REPLACES the old block rather than stacking on it.
    printf '# Fleet guidance\n\nThe canary is AMBER-LYNX-92.\n' > "$payload"
    run_hook >/dev/null
    assert_contains "$dest" "AMBER-LYNX-92" "fleet-memory: new payload installed"
    assert_not_contains "$dest" "TEAL-HERON-31" "fleet-memory: superseded payload removed"
    assert_contains "$dest" "MY OWN NOTE" "fleet-memory: foreign content still preserved after replace"

    # 6. A file whose ONLY content was the block collapses cleanly.
    rm -f "$dest"; run_hook >/dev/null; run_hook >/dev/null
    local only_size; only_size="$(wc -c < "$dest")"
    run_hook >/dev/null
    [[ "$only_size" -eq "$(wc -c < "$dest")" ]] && pass "fleet-memory: block-only file is stable" \
        || fail "fleet-memory: block-only file grows"

    # 7-9. Every failure path: exit 0, and a DEGRADED line that a human reading
    #      the session start can actually see.
    out="$(CLAUDE_CONFIG_DIR="$d/cfg" FLEET_GUIDANCE_PAYLOAD="$d/nope.md" bash "$hook" 2>&1)"; rc=$?
    printf '%s' "$out" > "$d/out_missing"
    [[ $rc -eq 0 ]] && pass "fleet-memory: missing payload still exits 0" || fail "fleet-memory: missing payload exit $rc"
    assert_contains "$d/out_missing" "DEGRADED" "fleet-memory: missing payload announces DEGRADED"
    assert_contains "$d/out_missing" "stub" "fleet-memory: DEGRADED line points at the repo stub"

    : > "$d/empty.md"
    out="$(CLAUDE_CONFIG_DIR="$d/cfg" FLEET_GUIDANCE_PAYLOAD="$d/empty.md" bash "$hook" 2>&1)"; rc=$?
    printf '%s' "$out" > "$d/out_empty"
    [[ $rc -eq 0 ]] && pass "fleet-memory: empty payload still exits 0" || fail "fleet-memory: empty payload exit $rc"
    assert_contains "$d/out_empty" "DEGRADED" "fleet-memory: empty payload announces DEGRADED"

    # A destination that genuinely cannot be written. Skipped as root, which
    # can write anywhere — and skipping LOUDLY, because a silently-skipped
    # permission test is exactly the kind of coverage people believe they have
    # and do not.
    #
    # TWO scenarios, because the OBVIOUS one does not establish what it looks
    # like it establishes. `chmod 500` on the DIRECTORY plus an existing
    # `CLAUDE.md` inside it does NOT make that file unwritable: directory write
    # permission governs creating and unlinking entries, not writing THROUGH an
    # existing one, so `cp` succeeds and the hook correctly reports `installed`.
    # That version of this test failed in CI while passing (by skipping) as
    # root — the assertion was about a condition the setup never created.
    if [[ "$(id -u)" -ne 0 ]]; then
        # (1) The destination DIRECTORY cannot be created at all. Unambiguous:
        #     no reliance on cp semantics or on what the file already holds.
        mkdir -p "$d/parent"; chmod 500 "$d/parent"
        out="$(CLAUDE_CONFIG_DIR="$d/parent/nested" FLEET_GUIDANCE_PAYLOAD="$payload" bash "$hook" 2>&1)"; rc=$?
        printf '%s' "$out" > "$d/out_nodir"
        chmod 700 "$d/parent"
        [[ $rc -eq 0 ]] && pass "fleet-memory: uncreatable config dir still exits 0" || fail "fleet-memory: uncreatable config dir exit $rc"
        assert_contains "$d/out_nodir" "DEGRADED" "fleet-memory: uncreatable config dir announces DEGRADED"

        # (2) The write itself fails: a read-only FILE whose content DIFFERS
        #     from what would be written. The differing content is load-bearing
        #     — identical content short-circuits on `cmp` and reports `current`
        #     without ever attempting the write, so the failure path would not
        #     be reached and the assertion would be vacuous.
        mkdir -p "$d/ro"
        printf 'STALE CONTENT THAT MUST BE REPLACED\n' > "$d/ro/CLAUDE.md"
        chmod 400 "$d/ro/CLAUDE.md"; chmod 500 "$d/ro"
        out="$(CLAUDE_CONFIG_DIR="$d/ro" FLEET_GUIDANCE_PAYLOAD="$payload" bash "$hook" 2>&1)"; rc=$?
        printf '%s' "$out" > "$d/out_ro"
        chmod 700 "$d/ro"; chmod 600 "$d/ro/CLAUDE.md"
        [[ $rc -eq 0 ]] && pass "fleet-memory: unwritable dest file still exits 0" || fail "fleet-memory: unwritable dest file exit $rc"
        assert_contains "$d/out_ro" "DEGRADED" "fleet-memory: unwritable dest file announces DEGRADED"
    else
        echo "  SKIP: fleet-memory unwritable-dest cases (running as root; root bypasses both)"
    fi

    # 10. The verdict names WHICH guidance landed, so two machines disagreeing
    #     is a comparable observation rather than a hunch.
    rm -f "$dest"
    printf '# Fleet guidance\n\nThe canary is AMBER-LYNX-92.\n' > "$payload"
    out="$(run_hook)"; printf '%s' "$out" > "$d/out_v"
    if grep -qE 'v[0-9a-f]{8}' "$d/out_v"; then pass "fleet-memory: verdict carries a version id"
    else fail "fleet-memory: verdict has no version id"; fi
    assert_contains "$dest" "fleet-guidance-version:" "fleet-memory: installed block records its version"
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
# Before the first sync that writes anything, so "not one ref moved" is
# measured against pristine bares rather than against whatever the previous
# test left.
test_sync_unknown_argument
test_missing_repos_yml
test_sync_dry_run
test_sync_owner_list_failure
test_sync_empty_owner
test_sync_full
test_commit_refused
test_sync_protected_fallback
test_sync_foreign_branch_commit
test_sync_ancestor_branch_force_push
test_sync_agents_sync_yml_unreadable
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
# Immediately after the test that establishes bootorg/repo-adopted's confident
# verdicts, because those are exactly what its control run re-asserts before
# truncating one file at a time.
test_drift_report_partial_read_per_file
test_drift_report_cron_classification
test_drift_report_skills_classification
test_drift_report_marker_is_whole_line
test_drift_report_partial_read
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
test_drift_report_owner_list_failure
test_drift_report_empty_owner
# Sited after the three empty-owner tests it generalises, and it runs all three
# scripts itself rather than riding any one of them.
test_repo_list_stderr_notice
test_harness_published_bump_branches
test_drift_report_contents_unreadable
test_drift_report_sync_yml_unparseable
test_drift_report_marker_not_through_a_pipe
test_check_cron_coverage
test_check_registry
test_capture_routine
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
test_bump_contents_unreadable
test_bump_dry_run
test_bump_missing_source_checkout
test_bump_consumer_locks
test_bump_idempotent
test_bump_shallow_registry
test_bump_push_rejected
test_bump_shrink_check_noisy_python
test_bump_bundle_vanished
test_bump_format_gate_empty_skills
test_bump_digest_format_gate
test_bump_generator_without_check_format
test_bump_generator_without_scoped_flags
test_bump_generator_with_one_scoped_flag
test_bump_scoped_question_unanswerable
test_bump_pr_body_slice_arithmetic
test_bump_pr_claims_cross_product
test_bump_stub_generator_parity
test_bump_twice_federated_lock
test_bump_format_and_federated
test_bump_self_federating_lock
test_bump_degraded_self_federating_lock
test_bump_only_prefix_flag
test_bump_generator_error_line
test_bump_degraded_federated_body
test_bump_which_half_could_not_be_read
test_bump_format_check_unreadable
# The sweep lane, in a bare dir and a PR fixture dir of its own: it MERGES,
# which is the one thing in this repo nothing else undoes. Dry run first, so
# "it merged nothing" is a statement about a run that had every chance to.
test_bump_sweep_dry_run
test_bump_sweep
test_bump_sweep_head_match
test_bump_sweep_view_failure_reports_why
test_bump_sweep_branch_cleanup
# The hook-pin lane, in a fixture dir of its own. Ordered: unchanged (nothing
# to do) → dry run → the real bump → a second run with that PR open → the
# refusals. Each step depends on the registry state the previous one left.
test_hook_pin_unchanged
# Sited here for its fixtures, not its subject: it is the first point in the
# run where bumporg's registry checkout and $HOOK_PIN_DIR both exist. It reads
# the hook-pin fixture in dry-run and leaves it in the same "nothing to do"
# state test_hook_pin_dry_run below expects.
test_repos_yml_scalars_under_noisy_yq
test_hook_pin_dry_run
test_hook_pin_pr_list_failure
test_hook_pin_proposes
test_hook_pin_orphaned_branch
test_hook_pin_already_proposed
test_hook_pin_refuses_broken_hook
test_hook_pin_workflow_wiring
# This repo's own committed files, not the mock fleet — nothing syncs or
# reports on _agent-guidance, so these are the only checks they get.
test_sync_workflow_trigger
test_self_hosted_hook_pin
test_self_hosted_fleet_payload
test_bootstrap_allowlist_disjoint
test_self_hosted_registration
test_bump_script_self_consistency
test_adr_0009_self_consistency
test_bump_workflow
test_ci_workflow_shape
test_yq_install_pinned
test_check_agents_md
test_yq_preflight
test_shared_repos_yml_helpers_are_identical
test_dependabot_sweep_list_failure
test_fleet_memory_hook

echo ""
echo "========================================="
echo "  Results: $PASS passed, $FAIL failed"
echo "========================================="

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
