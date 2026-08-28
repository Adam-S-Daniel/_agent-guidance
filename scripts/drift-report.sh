#!/usr/bin/env bash
set -euo pipefail
#
# drift-report.sh — Generate a markdown drift-report dashboard.
#
# Discovers all repos in the organization dynamically and checks:
#   • Whether AGENTS.md exists
#   • Whether the managed section matches what we would generate
#   • Whether the repo-specific marker header is present
#   • Whether CLAUDE.md imports @AGENTS.md (the Claude Code bridge)
#   • Whether the skills-bootstrap hook is delivered, current and REGISTERED
#   • Whether a sync PR is currently open
#   • Which sections the repo requests
#
# Output: drift-report.md in the repository root.
#
# Requirements: gh (GitHub CLI, authenticated), yq, python3
#
# Environment:
#   SYNC_OWNERS              — space-separated list of owners to scan; when
#                               set, takes precedence over
#                               GITHUB_REPOSITORY_OWNER and the git-remote
#                               fallback (e.g. "Adam-S-Daniel jodidaniel")
#   GITHUB_REPOSITORY_OWNER — org/user to scan (auto-set in GitHub Actions)
#   SYNC_SELF_REPO          — this repo's name, excluded from report (default: _agent-guidance)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_SCRIPT="$SCRIPT_DIR/build-agents-md.sh"
BRIDGE_SCRIPT="$SCRIPT_DIR/bridge-status.sh"
BOOTSTRAP_STATUS_SCRIPT="$SCRIPT_DIR/bootstrap-status.sh"
HOOK_REL_PATH=".claude/hooks/skills-bootstrap.sh"
SETTINGS_REL_PATH=".claude/settings.json"
LOCK_REL_PATH="skills.lock"
OUTPUT_FILE="$REPO_ROOT/drift-report.md"
MARKER="## Repo-specific additions"
TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M UTC")
BRANCH_NAME="agents-md-sync/update"
SELF_REPO="${SYNC_SELF_REPO:-_agent-guidance}"

# Resolve the owner(s) to scan: SYNC_OWNERS (space-separated) takes
# precedence, then GITHUB_REPOSITORY_OWNER, then fall back to git remote.
if [[ -n "${SYNC_OWNERS:-}" ]]; then
    read -ra OWNERS <<< "$SYNC_OWNERS"
elif [[ -n "${GITHUB_REPOSITORY_OWNER:-}" ]]; then
    OWNERS=("$GITHUB_REPOSITORY_OWNER")
else
    OWNERS=("$(git remote get-url origin | sed -E 's#.*/([^/]+)/[^/]+\.git$#\1#; s#.*/([^/]+)/[^/]+$#\1#')")
fi

REPOS_YML="${REPOS_YML:-$REPO_ROOT/repos.yml}"

# ── yq preflight ───────────────────────────────────────────────────────────
#
# THE INCIDENT, and it is one missing guard wearing two costumes. Every
# repos.yml read in this repo was spelled `yq ... 2>/dev/null || true`, which
# collapses three different answers into one: the key is legitimately absent,
# yq could not parse the file, and yq is not installed at all. Only the first
# is normal; `|| true` turned the other two into an EMPTY LIST, exit 0, and
# not one line in the log. An empty exclusion list is not an inert one —
# sync.sh's filter then matches nothing, and the run clones a repo repos.yml
# excludes and pushes the managed AGENTS.md straight to its default branch,
# which is the contamination that exclusion exists to prevent. Measured with
# a `yq` stubbed to exit 1: drift-report.sh printed "Found 1 repo(s)" and
# reported on the excluded repo, where the same run with a working yq printed
# "excluded by repos.yml" and "Found 0 repo(s)".
#
# The second costume is FLAVOUR, and it is why the probe below runs a command
# instead of reading a version string. Two unrelated programs install as
# `yq`: mikefarah's Go one, which this repo's workflows fetch version-pinned
# and digest-verified (docs/decisions/0008; Test 7h holds all four copies of
# that step identical), and kislyuk's Python wrapper around jq, which Debian
# ships as `yq` and which forwards `-o=json` to jq and dies with "jq: Unknown
# option -o=json" — a message that blames repos.yml for a tool problem, and
# the message the nightly hook-pin bump actually died with. `-o=json -I0` is
# the exact invocation bump-hook-pin.sh's round-trip check depends on, so
# probing it asks the question this repo really has, and asks it once, up
# front, rather than letting the answer arrive as an empty array or a
# misattributed parse error halfway down.
#
# THIS BLOCK IS DUPLICATED VERBATIM in sync.sh, drift-report.sh,
# bump-consumer-locks.sh and bump-hook-pin.sh. All four are standalone entry
# points and only one of them sources anything today (a file about pull
# request prose), so a library existing solely to hold this guard would be an
# abstraction invented for one `if`. The four copies must move together.
if ! command -v yq >/dev/null 2>&1; then
    echo "::error::yq is not on PATH, and this script reads repos.yml with it." >&2
    echo "::error::Install mikefarah yq (https://github.com/mikefarah/yq) — CI installs it version-pinned and digest-verified in the 'Install yq' step of .github/workflows/{ci,sync,drift-report,skills-lock-bump}.yml." >&2
    exit 2
fi
if ! printf 'a: 1\n' | yq -o=json -I0 . >/dev/null 2>&1; then
    # Captured rather than echoed straight through, and branched, so that
    # "yq --version itself fell over" and "yq --version answered something
    # unexpected" stay separate sentences instead of both reaching the log as
    # an empty string. The flavour is then named as the LIKELY cause, not
    # asserted: all this run established is that the probe failed, and a
    # sentence claiming more than the run established is the defect
    # lib/bump-pr-claims.sh exists to make unwritable.
    if ! yq_found=$(yq --version 2>&1); then
        yq_found="'yq --version' itself failed: ${yq_found:-no output}"
    else
        yq_found="'yq --version' says: ${yq_found:-no output}"
    fi
    echo "::error::the yq on PATH does not accept '-o=json -I0', which this script's repos.yml reads need — $yq_found" >&2
    echo "::error::This repo requires mikefarah's Go yq; a 'yq' that rejects that flag is usually kislyuk's Python jq-wrapper, which Debian ships under the same name. CI installs the required one version-pinned and digest-verified in the 'Install yq' step of .github/workflows/{ci,sync,drift-report,skills-lock-bump}.yml." >&2
    exit 2
fi

# ── Helpers ────────────────────────────────────────────────────────────────

strip_volatile() {
    grep -v '^<!-- Last synced:' || true
}

# Fetch a file's contents from a repo's default branch.
#
#   stdout : the file's bytes, or empty when the file is genuinely absent
#   exit 0 : the bytes are COMPLETE — verified against the API's own byte count
#   exit 2 : the response could not be turned into the whole file. The caller
#            must not draw a conclusion from stdout; there is nothing usable.
#
# The verification is the point, and it is why this asks for the JSON instead of
# piping `--jq .content` straight into base64. The JSON carries `size`, and
# comparing the decode against it is what separates "this file does not contain
# X" from "I did not receive all of this file".
#
# Issue #81: six repos were reported as missing their AGENTS.md marker and then
# as drift-detected, because the bytes this function returned were not the bytes
# in the repo — `git show origin/main:AGENTS.md` found the marker in every one.
# Nothing downstream could tell, because a short read and a real absence looked
# identical: both are just a string without the marker in it. Every column the
# row then produced was wrong, and the report published them with no hint.
#
# Note what is NOT claimed here: the mechanism was never reproduced. Fetching the
# same file over the same endpoint with curl returns a complete body, and the
# `jq -r .content | base64 -d` half handles a 95 KB payload correctly in
# isolation, so the fault lies somewhere this repo cannot exercise without a
# `gh` binary and an installation token. That is precisely why the fix is a
# check rather than a repair: it does not need to know the cause to refuse to
# publish the consequence.
#
# On HTTP errors gh api prints the raw error JSON body to stdout (the --jq
# filter is not applied) — discard output on failure, don't decode it.
fetch_file_content() {
    local repo="$1" path="$2"
    local json size tmp actual

    json=$(gh api "repos/$repo/contents/$path" 2>/dev/null) || return 0

    # A directory listing is a JSON array and legitimately has no .size — that
    # is an absence, not a failure. But an OBJECT without .size is a failure:
    # the contents API always sends it for a file, so its absence means the
    # response is not what it claims to be. Failing open there would re-create
    # the very bug this function exists to stop, one level up -- an unusable
    # response quietly reported as "the file is not there".
    if printf '%s' "$json" | jq -e 'type == "array"' >/dev/null 2>&1; then
        return 0
    fi
    size=$(printf '%s' "$json" | jq -r '.size // empty' 2>/dev/null) || size=""
    if [[ -z "$size" ]]; then
        echo "::error::$repo/$path: response carries no .size — cannot verify completeness, refusing to report on it (issue #81)" >&2
        return 2
    fi

    tmp=$(mktemp) || return 2
    printf '%s' "$json" | jq -r '.content // empty' 2>/dev/null | base64 -d >"$tmp" 2>/dev/null || true
    actual=$(wc -c <"$tmp")

    if [[ "$actual" -ne "$size" ]]; then
        echo "::error::$repo/$path: decoded $actual bytes but the API reports $size — refusing to report on a partial read (issue #81)" >&2
        rm -f "$tmp"
        return 2
    fi

    cat "$tmp"
    rm -f "$tmp"
    return 0
}

# read_repos_yml <yq expression> — the one way this script reads repos.yml.
#
# It exists to keep apart three answers the old `yq ... 2>/dev/null || true`
# spelling collapsed into one: a key that is legitimately absent (yq prints
# nothing and exits 0 — a normal empty result, which each caller's `[[ -n ]]`
# guard already handles), a repos.yml yq cannot parse, and a yq that fell
# over. Only a NON-ZERO EXIT is a failure, and a failure now stops the run,
# because the lists read through here decide which repos this script writes
# to: a silently empty one is not a smaller run, it is a run against the
# wrong set.
#
# Command substitution, never `< <(...)`. Process substitution discards the
# exit status of the command inside it, so simply dropping `|| true` from one
# of those reads would have changed the visible behaviour not at all — the
# same trap named beside the `gh repo list` capture further down. The `exit 2`
# below leaves only the substitution's subshell on its own; what stops the run
# is `set -e` seeing the assignment that captured it come back non-zero, so
# every caller must assign the result rather than pipe it.
#
# Duplicated in sync.sh, drift-report.sh and bump-consumer-locks.sh for the
# reason given above the yq preflight, and it must move with them.
read_repos_yml() {
    local expr="$1" out
    if ! out=$(yq -r "$expr" "$REPOS_YML" 2>&1); then
        echo "::error::repos.yml: yq failed reading '$expr' — ${out:-no output}" >&2
        exit 2
    fi
    printf '%s\n' "$out"
}

# ── Load central repos.yml (exclusions + default sections) ─────────────────

EXCLUDED_REPOS=()
DEFAULT_SECTIONS=()

if [[ -f "$REPOS_YML" ]]; then
    excluded_raw=$(read_repos_yml '.exclude // [] | .[]')
    while IFS= read -r r; do
        [[ -n "$r" ]] && EXCLUDED_REPOS+=("$r")
    done <<< "$excluded_raw"

    sections_raw=$(read_repos_yml '.default_sections // [] | .[]')
    while IFS= read -r s; do
        [[ -n "$s" ]] && DEFAULT_SECTIONS+=("$s")
    done <<< "$sections_raw"
fi

# ── cron-coverage classification (the fleet list's only drift alarm) ───────
#
# `scripts/check-cron-coverage.js` audits a DISK, so it can only reason about
# repos that are checked out; repos.yml's `cron_coverage.fleet` is what makes an
# absent one a finding there. But that list is hand-maintained and this script
# is the only thing in the repo that knows what the ACCOUNT actually holds — so
# a repo created and never classified is invisible everywhere else. Checking it
# here costs nothing: discovery has already produced the names, and this is a
# set lookup, not an extra API call. See
# docs/decisions/0003-cron-coverage-is-fleet-listed.md.
CRON_CLASSIFIED=()

if [[ -f "$REPOS_YML" ]]; then
    cron_classified_raw=$(read_repos_yml \
        '((.cron_coverage.fleet // []) + (.cron_coverage.out_of_scope // [])) | .[]')
    while IFS= read -r r; do
        [[ -n "$r" ]] && CRON_CLASSIFIED+=("$r")
    done <<< "$cron_classified_raw"
fi

cron_classified() {
    local short="${1##*/}" entry
    for entry in ${CRON_CLASSIFIED[@]+"${CRON_CLASSIFIED[@]}"}; do
        [[ "$short" == "$entry" ]] && return 0
    done
    return 1
}

# ── skills-bootstrap delivery config (read-only mirror of sync.sh's) ───────

BOOTSTRAP_REPOS=()
BOOTSTRAP_REGISTRY=""
BOOTSTRAP_PATH=""
BOOTSTRAP_REF=""

if [[ -f "$REPOS_YML" ]]; then
    BOOTSTRAP_REGISTRY=$(read_repos_yml '.skills_bootstrap.registry // ""')
    BOOTSTRAP_PATH=$(read_repos_yml '.skills_bootstrap.path // ""')
    BOOTSTRAP_REF=$(read_repos_yml '.skills_bootstrap.ref // ""')
    bootstrap_repos_raw=$(read_repos_yml '.skills_bootstrap.repos // [] | .[]')
    while IFS= read -r r; do
        [[ -n "$r" ]] && BOOTSTRAP_REPOS+=("$r")
    done <<< "$bootstrap_repos_raw"
fi

bootstrap_allowlisted() {
    local short="${1##*/}" entry
    for entry in ${BOOTSTRAP_REPOS[@]+"${BOOTSTRAP_REPOS[@]}"}; do
        [[ "$short" == "$entry" ]] && return 0
    done
    return 1
}

# The pinned hook, fetched lazily so a report run that touches no allowlisted
# repo costs nothing. Fetch failure is not fatal: the column degrades to
# "unverified" rather than the whole report failing.
#
# This is the ONE fetch_file_content call site that deliberately keeps the bare
# `|| VAR=""` shape every other one in this file had to give up. It can, because
# both outcomes it collapses are already the withheld one: an absent pinned hook
# and a short read of it both leave PINNED_HOOK empty, pinned_hook returns 1, and
# the cell reads "unverified" — a cell that says the comparison did not happen. A
# separate rc branch here would be a branch that changes nothing.
PINNED_HOOK=""
PINNED_HOOK_TRIED=false
pinned_hook() {
    if ! $PINNED_HOOK_TRIED; then
        PINNED_HOOK_TRIED=true
        [[ -n "$BOOTSTRAP_REGISTRY" && -n "$BOOTSTRAP_PATH" && -n "$BOOTSTRAP_REF" ]] || return 1
        PINNED_HOOK=$(fetch_file_content "$BOOTSTRAP_REGISTRY" "$BOOTSTRAP_PATH?ref=$BOOTSTRAP_REF") || PINNED_HOOK=""
    fi
    [[ -n "$PINNED_HOOK" ]]
}

# lock_summary <json> — "registry@shortref" per source, ", "-joined. This is the
# cheapest available answer to "is a consumer's lock stale?": the report cannot
# re-pin one (the generator lives in the registry, not here), but printing what
# each lock pins is what stops staleness being INVISIBLE — a lock forty commits
# behind installs cleanly and reports OK, so nothing else surfaces it.
lock_summary() {
    python3 -c '
import json, sys
try:
    doc = json.loads(sys.stdin.read())
except Exception:
    print("unreadable")
    sys.exit(0)
parts = []
def add(entry):
    reg = str(entry.get("registry", "?")).split("/")[-1]
    parts.append("%s@%s" % (reg, str(entry.get("ref", "?"))[:7]))
add(doc)
for src in doc.get("sources", []) or []:
    if isinstance(src, dict):
        add(src)
print(" + ".join(parts))
' 2>/dev/null || echo "unreadable"
}

# The drift report has no clone of any target repo — `fetch_file_content` is a
# `gh api …/contents/` read and nothing else — so `git check-ignore`, which is
# what sync.sh's probe uses, is unavailable. Infer instead: fetch the repo's
# committed ignore rules and replay them through git's OWN matcher in a
# throwaway repo. Never hand-roll the matching. The rules this has to get right
# are exactly the ones a regex gets wrong: `.claude/` followed by a
# `!.claude/hooks/` that git will NOT honour inside an excluded directory.
#
# `--no-index` is deliberate. sync.sh's probe is index-aware and answers "not
# ignored" for a TRACKED path; here the artifact is absent by construction, so
# the two answers coincide.
#
# Not inferable through this API: nested `.gitignore`s below `.claude/`,
# `.git/info/exclude`, `core.excludesFile`. All three are empty or
# runner-default in the CI clone sync.sh probes, so root + `.claude/.gitignore`
# reproduces its effective view there.
IGNORE_PROBE_DIR=""

# Set by bootstrap_blocked when it returns 2, naming the ignore file it could not
# read in full, so the row's note can say WHICH path went unread. It is a global
# rather than something printed on stdout because bootstrap_blocked has to run in
# the caller's shell: run it in a command substitution and the memoized
# IGNORE_PROBE_DIR it assigns is lost with the subshell, so every call would mint
# a fresh temp dir that the EXIT trap below never sees.
IGNORE_UNREADABLE_PATH=""

# The probe outlives any single call — it is reused across every repo in the
# run, which is what memoizing it above is for — so the earliest safe moment to
# remove it is the end of the run. sync.sh pairs its own `mktemp -d` with an
# EXIT trap the same way; this one needs a function rather than that one-liner
# because the directory does not exist yet when the trap is installed. Nothing
# else in this script traps, so there is no handler to chain onto.
cleanup_ignore_probe() {
    # On a run that probed nothing — no allowlisted repo missing its hook — the
    # variable is still "". The test is not here to stop `rm -rf ""`, which is a
    # silent no-op; it is here to keep the trap's exit status 0, so cleanup can
    # never rewrite the script's own exit code. It also leaves the `:?` below
    # unreachable — that is a backstop for the day the test is dropped, not a
    # path this takes today.
    if [[ -n "$IGNORE_PROBE_DIR" ]]; then
        rm -rf "${IGNORE_PROBE_DIR:?}"
    fi
}
trap cleanup_ignore_probe EXIT

# Returns 0 blocked, 1 not blocked, 2 could not read the ignore rules in full.
# That third answer has to exist because the two verdicts this decides hand a
# human OPPOSITE instructions: **blocked** says "does not self-heal, change that
# repo's .gitignore", **missing** says "the next sync delivers it". A short read
# of either ignore file used to be swallowed into "no rules at all", which is the
# branch that prints the reassuring one for a repo the sync is in fact skipping
# with a warning every night.
bootstrap_blocked() {
    local repo_name="$1" root_ignore claude_ignore probe rc=0
    IGNORE_UNREADABLE_PATH=""
    root_ignore=$(fetch_file_content "$repo_name" ".gitignore") || rc=$?
    if [[ "$rc" -ne 0 ]]; then
        IGNORE_UNREADABLE_PATH=".gitignore"
        return 2
    fi
    claude_ignore=$(fetch_file_content "$repo_name" ".claude/.gitignore") || rc=$?
    if [[ "$rc" -ne 0 ]]; then
        IGNORE_UNREADABLE_PATH=".claude/.gitignore"
        return 2
    fi
    [[ -z "$root_ignore$claude_ignore" ]] && return 1

    if [[ -z "$IGNORE_PROBE_DIR" ]]; then
        probe=$(mktemp -d)
        # Assign ONLY after init succeeds: assigning first latches the guard
        # below, and every later probe returns 1 forever with no signal.
        git init -q "$probe" 2>/dev/null || { rm -rf "$probe"; return 1; }
        IGNORE_PROBE_DIR="$probe"
    fi

    printf '%s\n' "$root_ignore" > "$IGNORE_PROBE_DIR/.gitignore"
    mkdir -p "$IGNORE_PROBE_DIR/.claude"
    printf '%s\n' "$claude_ignore" > "$IGNORE_PROBE_DIR/.claude/.gitignore"

    git -C "$IGNORE_PROBE_DIR" check-ignore -q --no-index "$HOOK_REL_PATH" 2>/dev/null \
      || git -C "$IGNORE_PROBE_DIR" check-ignore -q --no-index "$SETTINGS_REL_PATH" 2>/dev/null
}

# ── Write report header (once, before any owner) ────────────────────────────

{
    echo "# AGENTS.md Drift Report"
    echo ""
    echo "> Last generated: $TIMESTAMP"
} > "$OUTPUT_FILE"

# Base GH_TOKEN captured before the per-owner loop, so each iteration can
# restore it when the owner has no per-owner token of its own (owner A's
# per-owner token must not leak into owner B's iteration).
BASE_GH_TOKEN="${GH_TOKEN:-}"

# Owners whose repo listing could not be read at all this run. Reported in the
# published table (below) and counted here, because "this owner has no drift" and
# "this owner was never looked at" must not render as the same silence.
OWNER_FAILURES=()

# ── Scan each owner ──────────────────────────────────────────────────────

for ORG in "${OWNERS[@]}"; do

# ── Resolve per-owner token ──────────────────────────────────────────────
# GH_TOKEN_<OWNER>, where <OWNER> is $ORG uppercased with - and . mapped to
# _ (e.g. Adam-S-Daniel -> GH_TOKEN_ADAM_S_DANIEL). Falls back to the base
# GH_TOKEN captured above; if neither is set, GH_TOKEN is left unset so gh's
# ambient auth (locally) or failure (in CI) behaves as it did before.
per_owner_var="GH_TOKEN_$(echo "$ORG" | tr '[:lower:]-.' '[:upper:]__')"
per_owner_token="${!per_owner_var:-}"
if [[ -n "$per_owner_token" ]]; then
    export GH_TOKEN="$per_owner_token"
    echo "  Using per-owner token for $ORG"
elif [[ -n "$BASE_GH_TOKEN" ]]; then
    export GH_TOKEN="$BASE_GH_TOKEN"
else
    unset GH_TOKEN || true
fi

# ── Discover repos ─────────────────────────────────────────────────────────

echo "Scanning repos for: $ORG (excluding $SELF_REPO)"

# Capture repo list via command substitution so failures propagate under set -e.
# Process substitution <(...) silently swallows errors, which would cause the
# script to report success while doing nothing.
#
# The failure branch is explicit because a bare `set -e` death here is the wrong
# shape too, for the same reason bump-consumer-locks.sh spells one out: with
# SYNC_OWNERS ordered "Adam-S-Daniel jodidaniel", one owner missing its App
# installation ends the run before the OTHER owner is scanned at all. That costs
# more here than it does there, because this script's entire output is a
# published dashboard — the run dies with no report to publish and nothing
# anywhere naming the owner that could not be read. Counted per owner instead,
# and written INTO the report as its own section so a reader of drift-report.md
# can tell an owner with no drift from an owner nobody looked at.
if ! repo_list_raw=$(
    gh repo list "$ORG" \
        --no-archived \
        --source \
        --json nameWithOwner \
        --limit 1000 \
        --jq '.[].nameWithOwner' 2>&1
); then
    echo "::error::$ORG: could not list repos — $(head -1 <<< "$repo_list_raw")" >&2
    {
        echo ""
        echo "## $ORG"
        echo ""
        echo "> Organization: \`$ORG\` — **not scanned this run**"
        echo ""
        echo "| Repository | Status | Has marker | CLAUDE.md bridge | skills-bootstrap | Open PR | Sections | Notes |"
        echo "|------------|--------|------------|-------------------|------------------|---------|----------|-------|"
        echo "| *(owner not readable)* | **fetch-failed** | ? | ? | ? | ? | ? | \`gh repo list $ORG\` failed — no repo under this owner was checked this run; see the run log |"
    } >> "$OUTPUT_FILE"
    OWNER_FAILURES+=("$ORG")
    continue
fi

# Measured against the RAW discovery output, not the filtered list: this repo
# itself and every repos.yml-excluded repo still have to be classified for cron
# coverage, and both are dropped a line below. Forks and archived repos never
# appear here at all (`--source`, `--no-archived`), which is why repos.yml
# records them as structurally out of scope rather than expecting them.
CRON_UNCLASSIFIED=()
while IFS= read -r r; do
    [[ -n "$r" ]] || continue
    cron_classified "$r" || CRON_UNCLASSIFIED+=("$r")
done < <(echo "$repo_list_raw" | sort)

mapfile -t REPOS < <(echo "$repo_list_raw" | grep -v "/${SELF_REPO}$" | sed '/^$/d' | sort)   # drop the blank line an empty owner produces

# ── Filter repos excluded via repos.yml ─────────────────────────────────────
if [[ ${#EXCLUDED_REPOS[@]} -gt 0 ]]; then
    FILTERED_REPOS=()
    for r in "${REPOS[@]}"; do
        short_name="${r##*/}"
        excluded=false
        for ex in "${EXCLUDED_REPOS[@]}"; do
            [[ "$short_name" == "$ex" ]] && excluded=true && break
        done
        if $excluded; then
            echo "  $r — excluded by repos.yml"
        else
            FILTERED_REPOS+=("$r")
        fi
    done
    REPOS=("${FILTERED_REPOS[@]}")
fi

echo "Found ${#REPOS[@]} repo(s)"
echo ""

# ── Build report ───────────────────────────────────────────────────────────

{
    echo ""
    echo "## $ORG"
    echo ""
    echo "> Organization: \`$ORG\` — ${#REPOS[@]} repo(s) scanned"
    echo ""
    echo "| Repository | Status | Has marker | CLAUDE.md bridge | skills-bootstrap | Open PR | Sections | Notes |"
    echo "|------------|--------|------------|-------------------|------------------|---------|----------|-------|"
} >> "$OUTPUT_FILE"

if [[ ${#REPOS[@]} -eq 0 ]]; then
    echo "| *(no repos found)* | — | — | — | — | — | — | Check org name and gh auth |" >> "$OUTPUT_FILE"
fi

for repo_name in "${REPOS[@]}"; do
    echo "  Checking $repo_name ..."

    status="unknown"
    has_marker="—"
    bridge_cell="—"
    open_pr="none"
    sections_display="—"
    notes=""

    # Every path this row could not read IN FULL. `fetch_file_content` returns 2
    # only for a verified short read and 0 for a legitimate 404, so a name landing
    # in here always means "the bytes never arrived", never "the file is not
    # there" — the distinction #81 turned on. One entry is enough to withhold the
    # whole row; see the block just before the row is written.
    fetch_failed_paths=()

    # ── Resolve sections from repo's .agents-sync.yml ──────────────────

    sections=()

    sections_rc=0
    remote_yaml=$(fetch_file_content "$repo_name" ".agents-sync.yml") || sections_rc=$?
    if [[ "$sections_rc" -ne 0 ]]; then
        # This is #81's cascade arriving through a DIFFERENT file, and it is the
        # nastiest of the set because the wrong answer it produces is a plausible
        # one. Falling back to DEFAULT_SECTIONS on a short read builds `expected`
        # from a section list the repo never asked for, diffs it against an
        # AGENTS.md that arrived complete and is in fact correct, and publishes
        # **drift-detected** — indistinguishable from real drift, with the sync
        # then asked to "fix" a file that was already right.
        fetch_failed_paths+=(".agents-sync.yml")
        sections_display="?"
    elif [[ -n "$remote_yaml" ]]; then
        while IFS= read -r s; do
            [[ -n "$s" ]] && sections+=("$s")
        done < <(echo "$remote_yaml" | yq -r '.sections // [] | .[]' 2>/dev/null || true)
        sections_display="${sections[*]:-none}"
    else
        sections=("${DEFAULT_SECTIONS[@]}")
        sections_display="${sections[*]:-none}"
    fi

    # ── Fetch current AGENTS.md ────────────────────────────────────────

    # Distinguished on purpose: an unreadable AGENTS.md is not an absent one,
    # and reporting the first as the second is what made #81 silent.
    agents_rc=0
    current_agents=$(fetch_file_content "$repo_name" "AGENTS.md") || agents_rc=$?

    if [[ "$agents_rc" -ne 0 ]]; then
        # The status and the note are no longer set here: every other call site in
        # this loop now records the same way, and one shared block below turns the
        # ledger into the row's verdict.
        fetch_failed_paths+=("AGENTS.md")
        has_marker="?"
    elif [[ -z "$current_agents" ]]; then
        status="**no-agents-md**"
        notes="AGENTS.md not found in repo"
    else
        # Check marker header
        # -x (whole line), not a bare substring match. The managed block's own
        # BEGIN header QUOTES the marker verbatim —
        #   <!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
        # — so `grep -qF` calls the marker present in a file that merely
        # mentions it, and this file mentions it three times before the real
        # heading ever arrives.
        if echo "$current_agents" | grep -qxF "$MARKER"; then
            has_marker="yes"
        else
            has_marker="no"
        fi

        # Build expected managed content and compare. Skipped outright when the
        # section list did not arrive in full: `expected` is built FROM that list,
        # so diffing against it would measure the DEFAULT_SECTIONS fallback rather
        # than the repo, and publish that as drift.
        if [[ "$sections_rc" -ne 0 ]]; then
            # Nothing honest to compare — the status column stays "unknown" and
            # the shared block below marks the row fetch-failed.
            :
        else
            expected=$("$BUILD_SCRIPT" "${sections[@]}" 2>/dev/null || true)

            if [[ -z "$expected" ]]; then
                status="**update-failed**"
                notes="Could not build expected content"
            else
                # Extract managed section from current file
                if [[ "$has_marker" == "yes" ]]; then
                    # ANCHORED (^…$). Unanchored, this address matches the BEGIN
                    # header on LINE 1 — which quotes the marker — and deletes from
                    # there to EOF, leaving current_managed EMPTY. Compared against
                    # a 728-line expected block that can never match, so EVERY repo
                    # reported drift-detected and the dashboard was structurally
                    # incapable of ever printing up-to-date. All 18 rows of the
                    # 2026-08-27 report read drift-detected for this reason.
                    #
                    # This is the same first-occurrence trap check-agents-md.sh was
                    # written to catch, in the script that reports on it. Measured:
                    # unanchored keeps 0 bytes, anchored keeps 42,794 — byte-equal
                    # to build-agents-md.sh's output for this repo's own AGENTS.md.
                    current_managed=$(echo "$current_agents" | sed "/^${MARKER}\$/,\$d")
                else
                    current_managed="$current_agents"
                fi

                expected_clean=$(echo "$expected" | strip_volatile)
                current_clean=$(echo "$current_managed" | strip_volatile)

                if diff -q <(echo "$expected_clean") <(echo "$current_clean") &>/dev/null; then
                    status="**up-to-date**"
                else
                    status="**drift-detected**"
                fi
            fi
        fi
    fi

    # ── Check CLAUDE.md bridge status ───────────────────────────────────

    claude_rc=0
    current_claude=$(fetch_file_content "$repo_name" "CLAUDE.md") || claude_rc=$?

    if [[ "$claude_rc" -ne 0 ]]; then
        # `missing` is not a neutral word in this column — its legend reads "sync
        # adds the bridge in its next PR" — so a short read of a CLAUDE.md that
        # already imports @AGENTS.md manufactures a to-do out of a correct file.
        fetch_failed_paths+=("CLAUDE.md")
        bridge_cell="?"
    elif [[ -z "$current_claude" ]]; then
        bridge_cell="missing"
    else
        bridge_status=$(echo "$current_claude" | "$BRIDGE_SCRIPT" -)
        if [[ "$bridge_status" == "no-import" ]]; then
            bridge_cell="**no-import**"
        else
            bridge_cell="$bridge_status"
        fi
    fi

    # ── Check skills-bootstrap delivery ────────────────────────────────
    # Reported for EVERY repo, not just allowlisted ones: a hook sitting in a
    # repo that is no longer allowlisted still runs, and `unmanaged` is the
    # only thing that would ever say so (the sync has no delete semantics).

    bootstrap_cell="—"
    hook_rc=0
    current_hook=$(fetch_file_content "$repo_name" "$HOOK_REL_PATH") || hook_rc=$?

    if [[ "$hook_rc" -ne 0 ]]; then
        # Every verdict below reads the hook — whether it is there at all, and
        # whether it still matches the pin — so a short read of it decides
        # nothing. Left unanswered rather than answered "absent", which on an
        # allowlisted repo publishes **missing** ("the next sync delivers it") and
        # off the allowlist silently drops a live **unmanaged** hook that is
        # running in every session and that no sync will ever remove.
        fetch_failed_paths+=("$HOOK_REL_PATH")
        bootstrap_cell="?"
    elif bootstrap_allowlisted "$repo_name"; then
        lock_rc=0
        current_lock=$(fetch_file_content "$repo_name" "$LOCK_REL_PATH") || lock_rc=$?

        if [[ "$lock_rc" -ne 0 ]]; then
            # `no-lock` and **degraded** both turn on the lock being ABSENT, and
            # both are addressed to a human ("commit a lock", "remove the hook").
            # A short read cannot tell absence from truncation, so it answers
            # neither — and the lock summary in Notes is withheld with them, since
            # it would otherwise print pins parsed out of a fragment.
            fetch_failed_paths+=("$LOCK_REL_PATH")
            bootstrap_cell="?"
        else
            if [[ -n "$current_lock" ]]; then
                notes="${notes:+$notes; }lock: $(echo "$current_lock" | lock_summary)"
            fi

            if [[ -z "$current_lock" ]]; then
                # Delivery correctly withheld — not a fault, and not a to-do for
                # the sync. The repo has not declared its bundles yet. Tested
                # BEFORE the hook: sync.sh skips a lock-less repo whether or not
                # a hook is sitting there, so a hook present here is not `ok` —
                # it prints `skills: DEGRADED` into every session, forever, and
                # no sync will ever revisit it.
                if [[ -n "$current_hook" ]]; then
                    bootstrap_cell="**degraded**"
                else
                    bootstrap_cell="no-lock"
                fi
            elif [[ -z "$current_hook" ]]; then
                # Three reasons the hook can be absent. Only one self-heals.
                settings_rc=0
                settings_probe=$(fetch_file_content "$repo_name" "$SETTINGS_REL_PATH") || settings_rc=$?

                if [[ "$settings_rc" -ne 0 ]]; then
                    # **refused** and **missing** are both read off this one file
                    # and they are opposite instructions — "fix that file by hand,
                    # it will not self-heal" against "the next sync handles it".
                    fetch_failed_paths+=("$SETTINGS_REL_PATH")
                    bootstrap_cell="?"
                else
                    settings_state="missing"
                    [[ -n "$settings_probe" ]] && \
                        settings_state=$(echo "$settings_probe" | "$BOOTSTRAP_STATUS_SCRIPT" -)

                    # bootstrap_blocked now answers three ways, so its status is
                    # captured rather than read as a bare true/false: 2 means the
                    # ignore rules themselves came back short, and that is not the
                    # same fact as "nothing is ignored".
                    blocked=no
                    blocked_rc=0
                    if [[ "$settings_state" != "unparseable" ]]; then
                        bootstrap_blocked "$repo_name" || blocked_rc=$?
                        [[ "$blocked_rc" -eq 0 ]] && blocked=yes
                    fi

                    if [[ "$blocked_rc" -eq 2 ]]; then
                        fetch_failed_paths+=("$IGNORE_UNREADABLE_PATH")
                        bootstrap_cell="?"
                    elif [[ "$settings_state" == "unparseable" ]]; then
                        bootstrap_cell="**refused**"
                        notes="$notes; \`settings.json\` unparseable"
                    elif [[ "$blocked" == yes ]]; then
                        bootstrap_cell="**blocked**"
                        notes="$notes; \`.claude/\` gitignored"
                    else
                        bootstrap_cell="**missing**"
                    fi
                fi
            else
                settings_rc=0
                current_settings=$(fetch_file_content "$repo_name" "$SETTINGS_REL_PATH") || settings_rc=$?

                if [[ "$settings_rc" -ne 0 ]]; then
                    # **no-entry** is the loudest verdict this column has — its
                    # legend reads "nothing runs it — Silently dead" — and a short
                    # read is exactly what manufactures one out of a repo whose
                    # SessionStart entry is present and correct.
                    fetch_failed_paths+=("$SETTINGS_REL_PATH")
                    bootstrap_cell="?"
                else
                    if [[ -z "$current_settings" ]]; then
                        bootstrap_status="missing"
                    else
                        bootstrap_status=$(echo "$current_settings" | "$BOOTSTRAP_STATUS_SCRIPT" -)
                    fi

                    if [[ "$bootstrap_status" != "registered" ]]; then
                        # The silent-death case: the file is there, nothing runs it.
                        bootstrap_cell="**no-entry**"
                    elif ! pinned_hook; then
                        bootstrap_cell="unverified"
                    elif [[ "$current_hook" == "$PINNED_HOOK" ]]; then
                        # Both sides come through the same command-substitution path,
                        # so trailing-newline differences cancel; content is compared,
                        # not bytes-on-disk (sync.sh's cmp is the authoritative check).
                        bootstrap_cell="ok"
                    else
                        bootstrap_cell="**drifted**"
                    fi
                fi
            fi
        fi
    elif [[ -n "$current_hook" ]] && [[ "$repo_name" != "$BOOTSTRAP_REGISTRY" ]]; then
        # The registry AUTHORS the hook. `unmanaged`'s legend says "remove it
        # by hand" — pointed at the source of truth, that is a wrong answer.
        bootstrap_cell="**unmanaged**"
    fi

    # ── Check for open sync PR ─────────────────────────────────────────

    # `// empty` is load-bearing: `.[0].number` over an empty array renders the
    # literal string "null", which is non-empty, so every repo with no open sync
    # PR reported `#null` AND had its real status overwritten by **pr-open**.
    pr_number=$(gh pr list --repo "$repo_name" --head "$BRANCH_NAME" \
        --json number --jq '.[0].number // empty' 2>/dev/null || true)

    # Validate the SHAPE, don't trust the renderer. `-n` accepted anything the
    # far side happened to print, and what it printed was "null": jq renders
    # `.[0].number` over an empty array as the literal string, and whether the
    # `// empty` above suppresses it depends on which jq is answering -- this
    # sandbox's jq 1.7 prints nothing, the CI runner's prints `null`, on the
    # same commit. Every repo then published `#null` in the Open PR column AND
    # had its real status overwritten by **pr-open**, hiding genuine drift
    # behind a phantom pull request.
    #
    # A PR number is a number. Anything else -- "null", an error string, a
    # truncated body -- is not a PR, and refusing it here closes the whole
    # class rather than the one renderer that exposed it.
    if [[ "$pr_number" =~ ^[0-9]+$ ]]; then
        open_pr="#$pr_number"
        [[ "$status" == "**drift-detected**" ]] && status="**pr-open**"
    fi

    # ── Withhold the row when any part of it could not be read ─────────
    #
    # One partial read is enough to make the whole row untrustworthy, so Status
    # says so rather than publishing a verdict assembled from bytes that never
    # all arrived. Until now that promise was kept at exactly ONE of this loop's
    # call sites: a short read of anything but AGENTS.md still published a
    # confident cell — `missing`, `no-lock`, **no-entry**, **blocked** — which is
    # #81's entire failure mode wearing a different file's name, and it made the
    # legend's "every other column is withheld rather than guessed" false.
    #
    # Placed after the open-PR check on purpose: **pr-open** is a verdict too, and
    # a row nobody could read must not be overwritten by one.
    if [[ ${#fetch_failed_paths[@]} -gt 0 ]]; then
        status="**fetch-failed**"
        failed_list=$(printf '`%s`, ' "${fetch_failed_paths[@]}")
        notes="${notes:+$notes; }could not read ${failed_list%, } in full — the columns those files feed are withheld (\`?\`) rather than guessed"
    fi

    # ── Write row ──────────────────────────────────────────────────────

    echo "| [\`$repo_name\`](https://github.com/$repo_name) | $status | $has_marker | $bridge_cell | $bootstrap_cell | $open_pr | $sections_display | $notes |" >> "$OUTPUT_FILE"
done

# ── Unclassified for cron coverage ─────────────────────────────────────────
# Silence here is the pass. A name showing up means someone has to decide
# whether it belongs in `cron_coverage.fleet` (audited, and its absence from a
# disk becomes an error) or `out_of_scope` (nobody is promising to watch it).
if [[ ${#CRON_UNCLASSIFIED[@]} -gt 0 ]]; then
    {
        echo ""
        echo "> **Unclassified for cron coverage (${#CRON_UNCLASSIFIED[@]}):**"
        for r in "${CRON_UNCLASSIFIED[@]}"; do
            echo "> - \`$r\` — add it to \`repos.yml\` under \`cron_coverage.fleet\` or \`cron_coverage.out_of_scope\`"
        done
    } >> "$OUTPUT_FILE"
    echo "  ${#CRON_UNCLASSIFIED[@]} repo(s) unclassified for cron coverage"
fi

done

# ── Footer ─────────────────────────────────────────────────────────────────

{
    echo ""
    echo "---"
    echo ""
    echo "**Status legend**"
    echo ""
    echo "| Status | Meaning |"
    echo "|--------|---------|"
    echo "| **up-to-date** | Managed section matches the expected output |"
    echo "| **drift-detected** | Managed section has diverged — needs sync |"
    echo "| **pr-open** | A sync PR is already open for this repo |"
    echo "| **no-agents-md** | Repo does not have an AGENTS.md yet |"
    echo "| **update-failed** | An error occurred while checking this repo |"
    echo "| **fetch-failed** | A file this row is built from could not be read in full — the decoded byte count disagreed with the API's own \`size\`. **Notes** names the file; every column it feeds is withheld as \`?\` rather than guessed; see issue #81 |"
    echo ""
    echo "**CLAUDE.md bridge legend**"
    echo ""
    echo "| Bridge status | Meaning |"
    echo "|---------------|---------|"
    echo "| bridge-ok | CLAUDE.md imports \`@AGENTS.md\` (line-start, outside code fences) |"
    echo "| **no-import** | CLAUDE.md exists but never imports \`@AGENTS.md\` — Claude Code will not see the managed guidance |"
    echo "| missing | No CLAUDE.md yet — sync adds the bridge in its next PR |"
    echo "| ? | \`CLAUDE.md\` could not be read in full — withheld, not guessed (the row reads **fetch-failed**) |"
    echo ""
    echo "**skills-bootstrap legend**"
    echo ""
    echo "Delivery is opt-in and double-keyed: the repo must be listed in"
    echo "\`repos.yml\`'s \`skills_bootstrap.repos\` **and** already carry its own"
    echo "\`skills.lock\`. See \`docs/decisions/0001-skills-bootstrap-delivery-is-opt-in.md\`."
    echo ""
    echo "| Status | Meaning |"
    echo "|--------|---------|"
    echo "| ok | Hook present, byte-equal to the pinned copy, and registered in \`.claude/settings.json\` |"
    echo "| **no-entry** | Hook is present but **nothing runs it** — no SessionStart entry names it. Silently dead |"
    echo "| **drifted** | Hook present but differs from the pinned copy — the next sync overwrites it |"
    echo "| **missing** | Allowlisted and has a lock, but no hook — the next sync delivers it (unless the pinned hook was unavailable fleet-wide that run; the sync log says \`pinned hook unavailable this run\`) |"
    echo "| **blocked** | Allowlisted and has a lock, but the repo gitignores \`.claude/\` — \`git add\` cannot stage the hook, so every sync skips it with a warning. Does **not** self-heal: change that repo's \`.gitignore\`, or drop it from the allowlist |"
    echo "| **refused** | Allowlisted and has a lock, but \`.claude/settings.json\` is not parseable JSON — the sync will not edit it, and withholds the hook rather than leave one nothing runs. Does **not** self-heal: fix that file |"
    echo "| **degraded** | Hook present in a repo with no \`skills.lock\` — it prints \`skills: DEGRADED\` into every session and no sync will revisit it. Commit a lock, or remove the hook |"
    echo "| no-lock | Allowlisted, no \`skills.lock\` yet — delivery deliberately withheld until the repo declares its bundles |"
    echo "| **unmanaged** | Hook present in a repo that is **not** allowlisted — it still runs; the sync has no delete path, so remove it by hand |"
    echo "| unverified | Could not fetch the pinned hook this run — the drift comparison was skipped |"
    echo "| — | Not allowlisted and no hook present |"
    echo "| ? | A file this column is decided from (the hook, \`skills.lock\`, \`.claude/settings.json\`, or the repo's ignore rules) could not be read in full — withheld, not guessed (the row reads **fetch-failed**) |"
    echo ""
    echo "**Cron-coverage classification**"
    echo ""
    echo "\`scripts/check-cron-coverage.js\` audits a disk, so it cannot notice a"
    echo "repo that is not checked out; \`repos.yml\`'s \`cron_coverage.fleet\` is what"
    echo "makes an absent one an error there. This report is the only thing that"
    echo "sees the whole account, so it is where a repo classified by neither key"
    echo "surfaces — as the block above, or not at all when every repo is"
    echo "classified. See \`docs/decisions/0003-cron-coverage-is-fleet-listed.md\`."
    echo ""
    echo "The **Notes** column carries each allowlisted repo's lock pins"
    echo "(\`registry@shortref\`, one per federated source). Nothing else in the"
    echo "fleet surfaces a stale lock: a lock pinned far behind still installs"
    echo "cleanly and still reports \`OK\` in-session, by design."
} >> "$OUTPUT_FILE"

echo ""
echo "Drift report written to $OUTPUT_FILE"

# Deliberately not an exit code. drift-report.yml's publish step carries no
# `if: always()`, so failing here would suppress the very report that now holds
# the unreadable owner's section; the per-owner `::error::` above is what makes
# the run itself say so.
if [[ ${#OWNER_FAILURES[@]} -gt 0 ]]; then
    echo "  ${#OWNER_FAILURES[@]} owner(s) could not be listed and were not scanned: ${OWNER_FAILURES[*]}"
fi
