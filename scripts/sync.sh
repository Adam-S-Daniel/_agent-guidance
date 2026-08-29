#!/usr/bin/env bash
set -euo pipefail
#
# sync.sh — Sync the managed AGENTS.md to every repo in the organization.
#
# Discovers repos dynamically via `gh repo list`. For each repo the script:
#   1. Reads the repo's .agents-sync.yml (sections to include)
#   2. Builds the managed portion via build-agents-md.sh
#   3. Preserves any content below "## Repo-specific additions"
#   4. Ensures a CLAUDE.md bridge exists (creates it if absent; warns — or
#      rewrites when opted in via fix_claude_md — if present but broken)
#   5. Delivers the skills-bootstrap SessionStart hook to ALLOWLISTED repos
#      that already carry their own skills.lock (see repos.yml and
#      docs/decisions/0001) — never to every repo, and never writing the lock
#   6. Pushes the update directly to the default branch (the sync App has a
#      ruleset bypass, declared in repo-settings); falls back to a PR with
#      auto-merge for repos whose protection rejects the push
#
# Requirements: gh (GitHub CLI, authenticated), yq, git, python3
# Usage:        ./scripts/sync.sh [--dry-run]
#
# Environment:
#   SYNC_OWNERS              — space-separated list of owners to scan; when
#                               set, takes precedence over
#                               GITHUB_REPOSITORY_OWNER and the git-remote
#                               fallback (e.g. "Adam-S-Daniel jodidaniel")
#   GITHUB_REPOSITORY_OWNER — org/user to scan (auto-set in GitHub Actions)
#   SYNC_SELF_REPO          — this repo's name, excluded from sync (default: _agent-guidance)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_SCRIPT="$SCRIPT_DIR/build-agents-md.sh"
BRIDGE_SCRIPT="$SCRIPT_DIR/bridge-status.sh"
BOOTSTRAP_STATUS_SCRIPT="$SCRIPT_DIR/bootstrap-status.sh"
REGISTER_SCRIPT="$SCRIPT_DIR/register-bootstrap-hook.sh"
HOOK_REL_PATH=".claude/hooks/skills-bootstrap.sh"
SETTINGS_REL_PATH=".claude/settings.json"
LOCK_REL_PATH="skills.lock"
MARKER="## Repo-specific additions"
BRANCH_NAME="agents-md-sync/update"
# The committer identity every commit this sync makes is written under (set on
# each clone below) AND the identity the PR-fallback force-push guard
# recognises as its own work. One constant because those two must never drift:
# were the commits written under one address and the guard looking for another,
# the guard would read this sync's own stale branch as a stranger's and refuse
# every repo it had ever proposed to.
SYNC_BOT_EMAIL="agents-md-sync[bot]@users.noreply.github.com"
DRY_RUN=false
WORK_DIR=$(mktemp -d)
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

trap 'rm -rf "$WORK_DIR"' EXIT

# Parsed as a closed set rather than sniffed, exactly as bump-consumer-locks.sh
# and bump-hook-pin.sh do and for the same reason. `[[ "${1:-}" == "--dry-run" ]]`
# alone fails OPEN: `-n`, `--dry-runn`, and the flag given anywhere but first
# all left DRY_RUN=false, and the run then went on to clone every repo in the
# fleet, commit, push STRAIGHT to their default branches (this sync holds a
# ruleset bypass, so protection does not catch it) and force-push a branch —
# while the operator who typed the flag believed nothing was written. A flag
# whose whole meaning is "write nothing" has to fail CLOSED, so an argument
# this script does not recognise stops it. After the trap, so a usage exit
# still removes the work directory.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true ;;
        -h|--help)
            echo "Usage: $(basename "${BASH_SOURCE[0]}") [--dry-run]"
            echo "Environment: see the header of this script."
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument '$1'." >&2
            echo "Usage: $(basename "${BASH_SOURCE[0]}") [--dry-run]" >&2
            exit 2
            ;;
    esac
    shift
done

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

log()  { echo "  $*"; }
fail() { echo "  ERROR: $*"; }

# Writes the standard two-line CLAUDE.md bridge (imports @AGENTS.md) to the
# current directory. Shared by both the "CLAUDE.md absent" and the opted-in
# "rewrite a broken bridge" paths so the byte-for-byte content can't drift
# between them.
write_bridge_claude_md() {
    cat > CLAUDE.md <<'CLAUDEEOF'
<!-- Managed by _agent-guidance: bridges Claude Code (which reads CLAUDE.md) to AGENTS.md. -->
@AGENTS.md
CLAUDEEOF
}

FAIL_COUNT=0
OK_COUNT=0
SKIP_COUNT=0

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
# Duplicated in sync.sh, drift-report.sh, bump-consumer-locks.sh and
# bump-hook-pin.sh for the reason given above the yq preflight, and it must
# move with them.
read_repos_yml() {
    local expr="$1" out err err_file rc=0
    # Streams captured SEPARATELY. `2>&1` answered the failure path's question
    # by corrupting the success path's answer: yq writes to stderr while
    # exiting 0 (deprecation and expression warnings), and everything this
    # returns is PARSED — the exclusion list, default_sections, the
    # skills_bootstrap allowlist and its registry pin. Not a hypothetical
    # stderr: the mikefarah yq this repo pins answers `-j` with the right value
    # on stdout, "Flag --tojson has been deprecated" on stderr, and exit 0
    # (measured, v4.44.3). Measured with a yq wrapper that prints one "[WARN]"
    # line to stderr and then succeeds: sync.sh logged "Sections: yq: [WARN]
    # this expression syntax is deprecated rust" for a repo whose sections are
    # "rust", and "WARN: could not fetch yq: [WARN] ... — skills-bootstrap not
    # delivered this run."
    if ! err_file=$(mktemp); then
        echo "::error::repos.yml: could not create a temp file for yq's diagnostics" >&2
        exit 2
    fi
    out=$(yq -r "$expr" "$REPOS_YML" 2>"$err_file") || rc=$?
    # Read and removed UNCONDITIONALLY, before the branch, so the success path
    # cannot leak the file either.
    err=$(head -1 "$err_file")
    rm -f "$err_file"
    if [[ $rc -ne 0 ]]; then
        echo "::error::repos.yml: yq failed reading '$expr' — ${err:-no output}" >&2
        exit 2
    fi
    printf '%s\n' "$out"
}

# ── Load central repos.yml (exclusions + default sections) ─────────────────
#
# A repos.yml this script cannot FIND is a refusal, not an empty config — the
# same guard bump-hook-pin.sh carries, and for the reason the yq preflight
# above spells out at length. `read_repos_yml` was given teeth so that a
# repos.yml yq could not parse would stop the run; absence walked straight past
# it, because `if [[ -f "$REPOS_YML" ]]` simply skipped both reads and left
# EXCLUDED_REPOS empty with nothing in the log. That is the identical outcome,
# and the identical blast radius: the exclusion filter then matches nothing and
# the run clones a repo repos.yml excludes and pushes the managed AGENTS.md
# straight to its default branch. Measured against the suite's own fixtures —
# pointed at the real file, a dry run prints "testorg/repo-excluded — excluded
# by repos.yml" and "Found 1 repo(s)"; pointed one character off, the same run
# prints "Found 2 repo(s)", opens "=== testorg/repo-excluded ===", clones it,
# and exits 0 with nothing to tell it apart from a healthy run. A renamed file,
# a mis-set REPOS_YML and a checkout that never landed all arrive here.
if [[ ! -f "$REPOS_YML" ]]; then
    echo "::error::no repos.yml at $REPOS_YML — refusing to run, because the exclusions and default sections that decide which repos this sync writes to are read from it." >&2
    exit 2
fi

EXCLUDED_REPOS=()
DEFAULT_SECTIONS=()

excluded_raw=$(read_repos_yml '.exclude // [] | .[]')
while IFS= read -r r; do
    [[ -n "$r" ]] && EXCLUDED_REPOS+=("$r")
done <<< "$excluded_raw"

default_sections_raw=$(read_repos_yml '.default_sections // [] | .[]')
while IFS= read -r s; do
    [[ -n "$s" ]] && DEFAULT_SECTIONS+=("$s")
done <<< "$default_sections_raw"

# ── skills-bootstrap hook delivery config ──────────────────────────────────
# Opt-in, allowlisted, and double-keyed: a repo gets the hook only if it is
# named in repos.yml's skills_bootstrap.repos AND already carries its own
# skills.lock. See docs/decisions/0001. Absent config = the feature is simply
# off, which is what keeps this an additive change for every existing repo.

BOOTSTRAP_REPOS=()
BOOTSTRAP_REGISTRY=""
BOOTSTRAP_PATH=""
BOOTSTRAP_REF=""
BOOTSTRAP_SHA256=""

# No existence check here: the guard above already refused a missing repos.yml,
# so by this point the file is known to be present and readable. An absent
# skills_bootstrap BLOCK is still perfectly normal, and stays the "feature off"
# case below — that is a key legitimately missing from a file we did read, not
# a file we never found.
BOOTSTRAP_REGISTRY=$(read_repos_yml '.skills_bootstrap.registry // ""')
BOOTSTRAP_PATH=$(read_repos_yml '.skills_bootstrap.path // ""')
BOOTSTRAP_REF=$(read_repos_yml '.skills_bootstrap.ref // ""')
BOOTSTRAP_SHA256=$(read_repos_yml '.skills_bootstrap.sha256 // ""')

bootstrap_repos_raw=$(read_repos_yml '.skills_bootstrap.repos // [] | .[]')
while IFS= read -r r; do
    [[ -n "$r" ]] && BOOTSTRAP_REPOS+=("$r")
done <<< "$bootstrap_repos_raw"

# Enabled only when the pin is fully specified. A half-filled block (a ref
# with no digest, say) must not silently deliver unverified bytes.
BOOTSTRAP_ENABLED=false
if [[ -n "$BOOTSTRAP_REGISTRY" && -n "$BOOTSTRAP_PATH" \
      && -n "$BOOTSTRAP_REF" && -n "$BOOTSTRAP_SHA256" \
      && ${#BOOTSTRAP_REPOS[@]} -gt 0 ]]; then
    BOOTSTRAP_ENABLED=true
fi

BOOTSTRAP_BLOB=""          # path to the fetched, verified hook; "" until fetched
BOOTSTRAP_FETCH_WARNED=false

bootstrap_allowlisted() {
    local short="${1##*/}" entry
    for entry in ${BOOTSTRAP_REPOS[@]+"${BOOTSTRAP_REPOS[@]}"}; do
        [[ "$short" == "$entry" ]] && return 0
    done
    return 1
}

# ensure_bootstrap_blob — fetch the pinned hook once per run, verify its digest.
#
# Called from inside the owner loop rather than before it, because the token
# that can read the registry is the PER-OWNER one resolved there (sync.yml
# exports no shared GH_TOKEN). It is retried on each owner until it succeeds,
# so owner ordering cannot decide whether the hook is available.
#
# Two failure modes, deliberately different:
#   • fetch failed  — transient/permissions. Warn once, leave the feature on,
#     retry next owner; repos simply don't get the hook this run.
#   • digest mismatch — the bytes at the pinned commit are not the bytes that
#     were reviewed. Hard-disable delivery for the run and fail it.
ensure_bootstrap_blob() {
    $BOOTSTRAP_ENABLED || return 1
    [[ -n "$BOOTSTRAP_BLOB" ]] && return 0

    local encoded dest actual
    dest="$WORK_DIR/skills-bootstrap.sh"

    if ! encoded=$(gh api \
        "repos/$BOOTSTRAP_REGISTRY/contents/$BOOTSTRAP_PATH?ref=$BOOTSTRAP_REF" \
        --jq '.content' 2>/dev/null) || [[ -z "$encoded" ]]; then
        if ! $BOOTSTRAP_FETCH_WARNED; then
            log "WARN: could not fetch $BOOTSTRAP_REGISTRY/$BOOTSTRAP_PATH@${BOOTSTRAP_REF:0:7} — skills-bootstrap not delivered this run."
            BOOTSTRAP_FETCH_WARNED=true
        fi
        return 1
    fi

    if ! echo "$encoded" | base64 -d > "$dest" 2>/dev/null; then
        log "WARN: could not decode $BOOTSTRAP_PATH@${BOOTSTRAP_REF:0:7} — skills-bootstrap not delivered this run."
        rm -f "$dest"
        return 1
    fi

    actual=$(sha256sum "$dest" | cut -d' ' -f1)
    if [[ "$actual" != "$BOOTSTRAP_SHA256" ]]; then
        fail "skills-bootstrap digest mismatch at ${BOOTSTRAP_REF:0:7}: repos.yml says ${BOOTSTRAP_SHA256:0:12}…, fetched ${actual:0:12}…. Delivery disabled for this run."
        BOOTSTRAP_ENABLED=false
        ((FAIL_COUNT++)) || true
        rm -f "$dest"
        return 1
    fi

    BOOTSTRAP_BLOB="$dest"
    log "skills-bootstrap: pinned hook fetched from $BOOTSTRAP_REGISTRY@${BOOTSTRAP_REF:0:7} (digest OK)."
    return 0
}

# Base GH_TOKEN captured before the per-owner loop, so each iteration can
# restore it when the owner has no per-owner token of its own (owner A's
# per-owner token must not leak into owner B's iteration).
BASE_GH_TOKEN="${GH_TOKEN:-}"

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
    log "Using per-owner token for $ORG"
elif [[ -n "$BASE_GH_TOKEN" ]]; then
    export GH_TOKEN="$BASE_GH_TOKEN"
else
    unset GH_TOKEN || true
fi

# Best-effort, at most once per run (see the function's comment for why it is
# attempted here and not before the owner loop).
ensure_bootstrap_blob || true

# ── Discover repos ─────────────────────────────────────────────────────────

echo "Scanning repos for: $ORG (excluding $SELF_REPO)"
echo ""

# Captured through a command substitution, never process substitution: <(...)
# silently swallows the error and the script would report success while doing
# nothing. The failure branch is explicit because a bare `set -e` death here is
# the wrong shape too — sync.yml mints one App token per owner with
# continue-on-error, and its verify step promises that a failed mint only means
# "its repos will be skipped this run", but it exports no base GH_TOKEN. So a
# failed mint leaves GH_TOKEN unset, this `gh repo list` exits non-zero and,
# with SYNC_OWNERS ordered "Adam-S-Daniel jodidaniel", the run ends on the
# FIRST owner: the second owner is never scanned and no "=== Sync complete ==="
# summary is printed. Counted per owner instead, so the run still goes red at
# the end and names the owner it could not read, after serving every owner it
# could.
#
# Streams captured SEPARATELY, for the reason read_repos_yml gives above: what
# this returns is the FLEET, split into $REPOS and looped over, so a line gh
# writes to stderr while exiting 0 — a deprecation notice, an auth-expiry
# warning — does not merely decorate the log, it becomes a repository NAME.
# `sed '/^$/d'` is no defence: the injected line is not blank. Measured against
# this script with a gh stubbed to print one "gh: warning: your token will
# expire soon" line to stderr and one real repo to stdout, exit 0 — merged, the
# run printed "Found 2 repo(s):", opened "=== gh: warning: your token will
# expire soon ===", tried to read .agents-sync.yml from a path built out of the
# warning and ended "0 synced, 0 skipped, 2 failed"; separated, it prints
# "Found 1 repo(s)" and syncs it. mktemp is checked because this owner loop
# counts per-owner failures rather than aborting the fleet — under `set -e` an
# unchecked assignment here would end the run before the next owner is scanned,
# which is the exact failure the paragraph above exists to prevent.
if ! repo_list_err_file=$(mktemp); then
    fail "$ORG: could not create a temp file to capture gh's diagnostics"
    ((FAIL_COUNT++)) || true
    continue
fi
repo_list_rc=0
repo_list_raw=$(
    gh repo list "$ORG" \
        --no-archived \
        --source \
        --json nameWithOwner \
        --limit 1000 \
        --jq '.[].nameWithOwner' 2>"$repo_list_err_file"
) || repo_list_rc=$?
# Read and removed unconditionally, before the branch, so the `continue` below
# cannot leak the file and the success path cannot either.
repo_list_err=$(head -1 "$repo_list_err_file")
rm -f "$repo_list_err_file"
if [[ $repo_list_rc -ne 0 ]]; then
    fail "$ORG: could not list repos — ${repo_list_err:-no diagnostic output from gh}"
    ((FAIL_COUNT++)) || true
    continue
fi

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

if [[ ${#REPOS[@]} -eq 0 ]]; then
    echo "No repos found in $ORG — nothing to sync."
    continue
fi

echo "Found ${#REPOS[@]} repo(s):"
printf '  %s\n' "${REPOS[@]}"
echo ""

# ── Main loop ──────────────────────────────────────────────────────────────

for repo_name in "${REPOS[@]}"; do
    echo "=== $repo_name ==="

    # ── Resolve sections from repo's .agents-sync.yml ──────────────────

    sections=()

    # This one call carries three hazards. Two were in the shape it used to
    # have, and it got the second of them wrong in a way that WRITES; the third
    # arrived with the fix for the other two, which is why it is described last.
    #
    # The stdout half, which the old comment had right: on an HTTP error gh api
    # prints the raw error JSON body to stdout — the --jq filter is not applied
    # — so `|| true` alone would leave that body in remote_yaml and break the
    # base64 decode.
    #
    # The status half, which `2>/dev/null` + `remote_yaml=""` quietly threw
    # away: the exit status is the ONLY thing separating "this repo ships no
    # .agents-sync.yml" from "the credential could not read it". Discard it and
    # every 401, 403, 429, 5xx, DNS or TLS failure is reported as a fact the run
    # never established, and the run then REBUILDS that repo's AGENTS.md from
    # DEFAULT_SECTIONS, sees the content change, and commits and pushes it —
    # sync.sh is the one script of the four that writes. Measured with a stubbed
    # gh: with .agents-sync.yml readable (sections: python, docker) the run logs
    # "Sections: python docker"; with the same call answering "gh: API rate
    # limit exceeded (HTTP 403)" it logs "Sections: none", counts no failure and
    # exits 0 — indistinguishable from a repo that genuinely has none. The
    # managed build for [python docker] is 43,955 bytes against 42,796 for [],
    # so 1,159 bytes of guidance this repo opted INTO would be stripped and
    # pushed. The same disambiguation bump-consumer-locks.sh makes over
    # skills.lock, for the same reason.
    #
    # The STREAM half, which the fix for those two introduced by reaching for
    # `2>&1` so the diagnostic could be quoted: `2>&1` applies on SUCCESS too,
    # and it answers the failure path's question by corrupting the success
    # path's answer. gh writes to stderr while exiting 0 in ordinary conditions
    # — deprecation notices, auth warnings — and a merged capture folds those
    # lines into the base64 payload that is decoded and parsed below. Measured
    # with a gh stubbed to print one "gh: this API endpoint is deprecated" line
    # to stderr and the correct content to stdout, exit 0: merged, the run
    # reported "could not read the sections from .agents-sync.yml — base64:
    # invalid input" and counted the repo FAILED; separated, it reports
    # "Sections: python docker". A healthy repo turned into a failure by a
    # notice that changed nothing.
    #
    # So: stdout stays the file, stderr goes to $api_err_file, and only the
    # failure path reads it. mktemp is checked because this loop counts per-repo
    # failures rather than aborting the fleet — under `set -e` an unchecked
    # assignment here would end the run for every repo after this one.
    if ! api_err_file=$(mktemp); then
        fail "$repo_name: could not create a temp file to capture gh's diagnostics"
        ((FAIL_COUNT++)) || true
        continue
    fi
    api_rc=0
    remote_yaml=$(gh api "repos/$repo_name/contents/.agents-sync.yml" \
        --jq '.content' 2>"$api_err_file") || api_rc=$?
    # Read then removed unconditionally, so no `continue` below can leak it;
    # the value is only ever USED in the failure branch. Same shape as
    # drift-report.sh's fetch_file_content, which reads the same endpoint.
    api_stderr=$(cat "$api_err_file")
    rm -f "$api_err_file"

    if [[ $api_rc -ne 0 ]]; then
        # gh's own diagnostic, and only gh's: on an HTTP error gh prints the
        # API's raw error body to stdout with the --jq filter unapplied, and
        # the house rule forbids echoing a response body into a public log
        # (AGENTS.md, "Sanitize error output"). With the streams separated
        # that body is no longer in reach of this message, which is what the
        # merged capture could not promise.
        # The line carrying the STATUS is preferred over the merely first one.
        # gh's deprecation and auth-expiry notices are themselves `gh: `-prefixed
        # and arrive BEFORE the error, so `grep -m1 '^gh: '` named the notice on
        # exactly the runs the paragraph above describes. Measured with a gh
        # stubbed to print "gh: this API endpoint is deprecated" then "gh: API
        # rate limit exceeded (HTTP 403)" and exit 1: it reported "could not read
        # .agents-sync.yml — gh: this API endpoint is deprecated", which sends
        # the operator after the wrong fault. This picks the line only; the 404
        # disambiguation below still matches "(HTTP 404)" anywhere in
        # $api_stderr, so what the run DOES is unchanged — only what it says it
        # saw. Three-step fallback because none of the three is guaranteed: the
        # last is the old behaviour, kept for a diagnostic gh writes in neither
        # shape.
        api_err=$(grep -m1 '^gh: .*(HTTP ' <<< "$api_stderr" \
            || grep -m1 '^gh: ' <<< "$api_stderr" \
            || head -1 <<< "$api_stderr")

        # A 404 on the PATH is the only outcome that MAY mean "absent", and even
        # it means that only once the REPO itself answers: GitHub replies 404
        # rather than 403 when a credential is not authorized to know a thing
        # exists, so an unprobed 404 is as much a scope gap as a missing file
        # (AGENTS.md, "A GitHub 404 means 'not authorized', not 'not there'").
        # Both surfaces of the status are matched because only one of them may
        # reach us, and separating the streams is what lets each be read where
        # it actually lands: gh's own line carries "(HTTP 404)" on stderr, while
        # the API's error body carries "status":"404" on stdout — which, on this
        # path, is what $remote_yaml holds.
        if [[ "$api_stderr" != *"(HTTP 404)"* \
              && ! "$remote_yaml" =~ \"status\":[[:space:]]*\"404\" ]]; then
            fail "$repo_name: could not read .agents-sync.yml — ${api_err:-no diagnostic output from gh}"
            ((FAIL_COUNT++)) || true
            continue
        fi

        # The probe that tells the two 404s apart. It asks about the REPO, not
        # the file: a repo that answers has told us the credential can see it
        # and the file really is not there, whereas a repo that 404s too has
        # told us nothing about the file at all.
        if ! repo_probe=$(gh api "repos/$repo_name" --silent 2>&1); then
            probe_err=$(grep -m1 '^gh: ' <<< "$repo_probe" || head -1 <<< "$repo_probe")
            fail "$repo_name: .agents-sync.yml answered 404 and so did the repo itself — a scope gap, not a missing file — ${probe_err:-no diagnostic output from gh}"
            ((FAIL_COUNT++)) || true
            continue
        fi

        # Genuinely absent, established rather than assumed: the repo answered
        # and the file did not. This is the commonest outcome in the fleet, and
        # the one the default_sections fallback below exists for.
        remote_yaml=""
    fi

    if [[ -n "$remote_yaml" ]]; then
        # Captured, never `< <(...)`: process substitution discards the exit
        # status of the command inside it, which is what let a file that ARRIVED
        # COMPLETE but did not PARSE reach the fallback as an empty section list
        # — the same wrong answer the 403 above produced, reached a different
        # way, and pushed just the same. A malformed .agents-sync.yml is a
        # repo-authored file and a normal thing to encounter, not a defensive
        # edge case. Measured with a stubbed gh serving unparseable YAML: the
        # run logged "Sections: none" and exited 0. `set -o pipefail` is what
        # makes one capture answer for both stages, so a base64 body that is not
        # a file is refused here too rather than decoding to nothing.
        #
        # Streams SEPARATED, and this is the capture in this script where that
        # matters most: what it returns is DATA — split into $sections and handed
        # to "$BUILD_SCRIPT" — so a line either stage writes to stderr while the
        # pipeline still exits 0 becomes a SECTION NAME. Not hypothetical: the
        # mikefarah yq this repo pins answers `-j` with the right value on
        # stdout, "Flag --tojson has been deprecated" on stderr, and exit 0
        # (measured, v4.44.3). Measured against this script with a yq wrapper
        # that prints one deprecation line to stderr and then succeeds, over an
        # .agents-sync.yml declaring [python, docker] — merged, the run logged
        # "Sections: Flag --r has been deprecated, please use --unwrapScalar
        # python docker"; separated, "Sections: python docker". And the
        # corruption does not stop at the log: `build-agents-md.sh Flag python
        # docker` exits 0 and emits 45,677 bytes against 45,598 for `python
        # docker`, with the header rewritten to "Sections: Flag python docker"
        # plus an appended "WARNING: unknown section 'Flag'" — content this
        # script would then see as changed, commit, and push. The brace group is
        # what puts BOTH stages' stderr in the file; mktemp is checked because
        # this loop counts per-repo failures rather than aborting the fleet.
        if ! sections_err_file=$(mktemp); then
            fail "$repo_name: could not create a temp file to capture the section read's diagnostics"
            ((FAIL_COUNT++)) || true
            continue
        fi
        sections_read_rc=0
        repo_sections_raw=$( { base64 -d <<< "$remote_yaml" \
                | yq -r '.sections // [] | .[]'; } 2>"$sections_err_file" ) || sections_read_rc=$?
        # Read and removed unconditionally, before the branch: the value is only
        # ever USED in the failure branch, but the file must go either way.
        sections_err=$(head -1 "$sections_err_file")
        rm -f "$sections_err_file"
        if [[ $sections_read_rc -ne 0 ]]; then
            fail "$repo_name: could not read the sections from .agents-sync.yml — ${sections_err:-no diagnostic output}"
            ((FAIL_COUNT++)) || true
            continue
        fi

        while IFS= read -r s; do
            [[ -n "$s" ]] && sections+=("$s")
        done <<< "$repo_sections_raw"

        # Optional per-repo opt-in: let the sync REWRITE an existing
        # CLAUDE.md that doesn't import @AGENTS.md (see the "CLAUDE.md
        # bridge" block below). Anything other than exactly "true" — unset,
        # malformed YAML, "false", "yes", etc. — normalizes to false.
        fix_claude_md=$(echo "$remote_yaml" | base64 -d | yq -r '.fix_claude_md // false' 2>/dev/null || echo false)
        [[ "$fix_claude_md" == "true" ]] || fix_claude_md=false
    else
        sections=("${DEFAULT_SECTIONS[@]}")
        fix_claude_md=false
    fi

    log "Sections: ${sections[*]:-none}"

    # ── Build managed content ──────────────────────────────────────────

    managed_content=$("$BUILD_SCRIPT" "${sections[@]}")

    # ── Clone & prepare ────────────────────────────────────────────────

    repo_dir="$WORK_DIR/$(echo "$repo_name" | tr '/' '_')"
    if ! gh repo clone "$repo_name" "$repo_dir" -- --depth 1; then
        fail "clone failed for $repo_name"
        ((FAIL_COUNT++)) || true
        continue
    fi
    cd "$repo_dir"

    # Configure git identity for commits (not inherited in fresh clones)
    git config user.name "agents-md-sync[bot]"
    git config user.email "$SYNC_BOT_EMAIL"

    # Embed token in remote URL so git push can authenticate in CI (no TTY).
    # gh-repo-clone sets an HTTPS remote but does not persist credentials for
    # subsequent git operations, causing:
    #   fatal: could not read Username for 'https://github.com': No such device or address
    if [[ -n "${GH_TOKEN:-}" ]]; then
        git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/${repo_name}.git"
    fi

    # ── Preserve repo-specific content ─────────────────────────────────

    repo_specific=""
    existing_prefix=""
    if [[ -f AGENTS.md ]]; then
        # Anchored AND exact-whole-line, on both the presence check and the
        # extraction — not the unanchored/non-end-anchored pair this used to
        # be. This repo's own AGENTS.md carried a live example of why: its
        # generated BEGIN-MANAGED-SECTION header quotes the marker text
        # verbatim (`DO NOT EDIT ABOVE "## Repo-specific additions"`), so an
        # unanchored `grep -qF "$MARKER"` is satisfied by line 1 of a file
        # with no real marker at all, and a start-only-anchored
        # `/^${MARKER}/` treats a truncated `## Repo-specific additions" -->`
        # fragment (the tail of that same header, however it got split onto
        # its own line) as a valid anchor too. Either way the sed extracts
        # the wrong slice — in the truncated-fragment case, everything from
        # THAT line down, which includes a stale managed block and re-glues
        # it below a freshly generated one on every future sync, doubling it
        # forever. `-x` on the grep and `$` on the sed regex require the
        # WHOLE line to equal the marker, so only a genuine
        # `## Repo-specific additions` line — nothing quoting it, nothing
        # truncating it — can anchor either check. This closes the
        # unanchored-grep/anchored-sed mismatch the comment further down this
        # block (the "glued marker" note) already flagged: that comment was
        # about the trailing-newline case; this is the other way the same
        # mismatch bites.
        if grep -qxF -- "$MARKER" AGENTS.md; then
            repo_specific=$(sed -n "/^${MARKER}\$/,\$p" AGENTS.md)
        else
            # Existing AGENTS.md without marker — preserve entire content above
            # the marker heading, with managed content added below it
            existing_prefix="$(cat AGENTS.md)"
        fi
    fi

    if [[ -z "$repo_specific" && -z "$existing_prefix" ]]; then
        repo_specific="$(printf '%s\n\n%s\n' \
            "$MARKER" \
            "<!-- Add your repo-specific agent guidance below this line -->")"
    fi

    # ── Assemble ───────────────────────────────────────────────────────

    if [[ -n "$existing_prefix" ]]; then
        # No-marker case: managed content on top, then the marker, then the
        # existing hand-written content preserved below it — mirroring the
        # marker-case ordering below (managed content above the marker,
        # repo-specific content at-and-below it). This ordering is required
        # by the parse invariant: on every sync, content above "$MARKER" is
        # managed (overwritten) and content from "$MARKER" down is preserved.
        # Putting the existing content ABOVE the marker here (as before) would
        # make the *next* sync's marker-case parse treat the stale managed
        # copy below it as "repo-specific", silently destroying everything
        # written above on the second sync.
        new_agents_md="$(printf '%s\n%s\n\n%s\n' "$managed_content" "$MARKER" "$existing_prefix")"
    else
        # The "\n" between managed content and repo_specific is load-bearing:
        # managed_content carries no trailing newline (command substitution
        # strips it), so without it the marker line at the top of
        # repo_specific glues onto "<!-- END MANAGED SECTION -->". A glued
        # marker still passes the unanchored `grep -qF` presence check above
        # but fails the anchored `sed -n "/^${MARKER}/..."` parse on the next
        # sync, leaving repo_specific empty and dropping all preserved content.
        new_agents_md="$(printf '%s\n%s\n' "$managed_content" "$repo_specific")"
    fi

    # ── Diff check ─────────────────────────────────────────────────────
    # Skip only when AGENTS.md is already correct AND the CLAUDE.md bridge
    # is already in place AND there's no opted-in bridge fix pending — a
    # repo can be AGENTS.md-current but still missing CLAUDE.md (e.g. it
    # predates the bridge), and that case must still get a commit that adds
    # just the bridge file. Likewise, a repo can be current-and-bridged yet
    # opted into fixing a broken bridge (fix_claude_md: true with an
    # existing no-import CLAUDE.md), and that case must still get a commit
    # that rewrites it.

    agents_up_to_date=false
    if [[ -f AGENTS.md ]] && diff -q <(echo "$new_agents_md") AGENTS.md &>/dev/null; then
        agents_up_to_date=true
    fi

    claude_md_present=false
    [[ -f CLAUDE.md ]] && claude_md_present=true

    # bridge_status classifies an existing CLAUDE.md via bridge-status.sh;
    # needs_claude_fix gates the opt-in rewrite path below on a broken
    # bridge AND the repo's fix_claude_md: true.
    bridge_status="missing"
    if $claude_md_present; then
        bridge_status=$("$BRIDGE_SCRIPT" CLAUDE.md)
    fi

    needs_claude_fix=false
    if $claude_md_present && [[ "$bridge_status" == "no-import" ]] && [[ "$fix_claude_md" == "true" ]]; then
        needs_claude_fix=true
    fi

    # ── skills-bootstrap: classify ─────────────────────────────────────
    # Both keys are read from the CLONE, so neither can be spoofed by config
    # in this repo: the allowlist is ours, the lock is theirs. Everything
    # below stays false for a repo that is not allowlisted, which is why this
    # block cannot change behaviour for the 16 repos that aren't.

    bootstrap_deliver=false     # this repo should end up with hook + registration
    bootstrap_reason=""         # why not, when it shouldn't
    hook_state="n/a"            # missing | current | drifted
    reg_state="n/a"             # registered | no-entry | unparseable | missing
    lock_present=false
    [[ -f "$LOCK_REL_PATH" ]] && lock_present=true

    if bootstrap_allowlisted "$repo_name"; then
        if ! $lock_present; then
            # NOT a half-install: the hook without a lock prints a permanent
            # "skills: DEGRADED — no skills.lock found" verdict into every
            # ephemeral session, naming a generator script the repo does not
            # have. Withholding is the correct, documented outcome.
            bootstrap_reason="no skills.lock in the repo yet — delivery withheld (the repo declares its own bundles; the sync never writes one)"
        elif [[ -z "$BOOTSTRAP_BLOB" ]]; then
            bootstrap_reason="pinned hook unavailable this run"
        else
            bootstrap_deliver=true

            if [[ -f "$HOOK_REL_PATH" ]]; then
                if cmp -s "$HOOK_REL_PATH" "$BOOTSTRAP_BLOB"; then
                    hook_state="current"
                else
                    hook_state="drifted"
                fi
            else
                hook_state="missing"
            fi

            reg_state=$("$BOOTSTRAP_STATUS_SCRIPT" "$SETTINGS_REL_PATH")

            # An unreadable settings.json is never rewritten (same posture as
            # an existing CLAUDE.md). Delivering the hook file alone would
            # leave it silently dead, so withhold the whole artifact and say so.
            if [[ "$reg_state" == "unparseable" ]]; then
                bootstrap_deliver=false
                bootstrap_reason="$SETTINGS_REL_PATH is not parseable JSON — refusing to edit it"
            fi
        fi
    fi

    bootstrap_up_to_date=true
    if $bootstrap_deliver && { [[ "$hook_state" != "current" ]] || [[ "$reg_state" != "registered" ]]; }; then
        bootstrap_up_to_date=false
    fi

    if [[ -n "$bootstrap_reason" ]]; then
        log "skills-bootstrap: $bootstrap_reason."
    fi

    if $agents_up_to_date && $claude_md_present && ! $needs_claude_fix && $bootstrap_up_to_date; then
        log "Up to date — skipping."
        ((SKIP_COUNT++)) || true
        cd "$REPO_ROOT"
        continue
    fi

    # ── Resolve default branch ─────────────────────────────────────────
    # Resolved before delivery (the direct push targets it, the PR fallback
    # bases onto it) and before the dry-run report so it can name the branch.
    # Guarded assignment — a bare command substitution would abort the entire
    # run under set -e on a transient API error, breaking the per-repo fail
    # isolation used by the other steps. The -z check also catches an
    # empty-but-exit-0 response.
    if ! default_branch=$(gh repo view "$repo_name" --json defaultBranchRef \
        --jq .defaultBranchRef.name) || [[ -z "$default_branch" ]]; then
        fail "could not resolve default branch for $repo_name"
        ((FAIL_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi

    if $DRY_RUN; then
        if ! $agents_up_to_date; then
            log "[DRY RUN] Would update AGENTS.md (direct push to $default_branch; PR fallback if rejected)"
        elif $needs_claude_fix; then
            log "[DRY RUN] AGENTS.md up to date; would rewrite CLAUDE.md to the standard @AGENTS.md bridge (fix_claude_md: true)"
        elif ! $claude_md_present; then
            log "[DRY RUN] AGENTS.md up to date; would add missing CLAUDE.md bridge"
        fi

        # The bootstrap artifact is reported line-by-line and separately from
        # AGENTS.md, including the case where the ONLY change is the hook —
        # a dry run that hid that would be lying about the run it previews.
        if ! $bootstrap_up_to_date; then
            case "$hook_state" in
                missing) log "[DRY RUN] Would add $HOOK_REL_PATH (from $BOOTSTRAP_REGISTRY@${BOOTSTRAP_REF:0:7})" ;;
                drifted) log "[DRY RUN] Would overwrite drifted $HOOK_REL_PATH with the pinned copy (${BOOTSTRAP_REF:0:7})" ;;
            esac
            [[ "$reg_state" != "registered" ]] && \
                log "[DRY RUN] Would append a SessionStart entry for the hook to $SETTINGS_REL_PATH (existing entries preserved)"
        fi
        if $bootstrap_deliver; then
            log "[DRY RUN] Would NOT touch $LOCK_REL_PATH (present; the sync never writes it)"
        fi

        ((SKIP_COUNT++)) || true
        cd "$REPO_ROOT"
        continue
    fi

    # ── Commit on the default branch ───────────────────────────────────
    # No side branch: the clone is already checked out on the default
    # branch, and the sync App's ruleset bypass lets us push straight to it.

    echo "$new_agents_md" > AGENTS.md

    # ── CLAUDE.md bridge ────────────────────────────────────────────────
    # Claude Code reads CLAUDE.md, not AGENTS.md — it never sees the managed
    # guidance unless something imports it. Anthropic's documented pattern is
    # a CLAUDE.md containing `@AGENTS.md`. Default remains never-rewrite: an
    # existing CLAUDE.md is left untouched even if it doesn't import
    # AGENTS.md, since we must not clobber someone's hand-written file.
    # fix_claude_md is the per-repo opt-in that lifts that default — it's
    # safe because it only fires on the repo's explicit fix_claude_md: true
    # opt-in (the same delivery path as any other change: direct push, PR
    # fallback). Bridge presence is judged with bridge-status.sh instead of
    # the old `grep -qF '@AGENTS.md'`, which a fenced example could fool into
    # a false positive; the classifier is fence-aware and isn't.
    claude_md_added=false
    claude_md_fixed=false
    warn_no_import=false
    if ! $claude_md_present; then
        write_bridge_claude_md
        claude_md_added=true
    elif [[ "$bridge_status" == "no-import" ]]; then
        if $needs_claude_fix; then
            write_bridge_claude_md
            claude_md_fixed=true
            log "Rewriting CLAUDE.md to the standard @AGENTS.md bridge (fix_claude_md: true)."
        else
            warn_no_import=true
            log "WARN: CLAUDE.md exists but does not import @AGENTS.md — Claude Code will not see the managed guidance."
        fi
    fi

    # ── skills-bootstrap: deliver ──────────────────────────────────────
    # Three files are in play and they are NOT symmetrical:
    #   .claude/hooks/skills-bootstrap.sh — machinery. Written when absent,
    #     OVERWRITTEN when drifted. There is no repo-specific seam in it and
    #     no marker to preserve; a divergent copy is either stale-from-an-
    #     older-pin (which must self-heal) or hand-edited — and a hand-edited
    #     copy of a file that fetches and installs instruction text with no
    #     approval prompt is precisely the thing not to preserve. Hence no
    #     fix_* opt-in, unlike CLAUDE.md: the escape hatch is the allowlist.
    #   .claude/settings.json — configuration. APPENDED to, never overwritten,
    #     and refused outright if unparseable (classified above).
    #   skills.lock — the repo's own DECLARATION. Never written, not even
    #     created. There is deliberately no code path here that writes it.
    bootstrap_hook_written=false
    bootstrap_registered_now=false
    bootstrap_gitignored=false

    if $bootstrap_deliver; then
        # git add on a gitignored path exits 1, and under `set -euo pipefail`
        # that aborts the ENTIRE fleet run at whichever repo hits it first —
        # not just this repo. Two repos in the fleet gitignore `.claude/`
        # today, deliberately and with a comment, so this probe is load-
        # bearing. `git add -f` is not the answer: overriding a repo's
        # explicit policy from a central sync is a conversation, not a flag.
        if git check-ignore -q "$HOOK_REL_PATH" 2>/dev/null \
           || git check-ignore -q "$SETTINGS_REL_PATH" 2>/dev/null; then
            bootstrap_gitignored=true
            log "WARN: .claude/ is gitignored in $repo_name — skills-bootstrap not delivered (change that repo's .gitignore, or drop it from repos.yml)."
        else
            if [[ "$hook_state" != "current" ]]; then
                mkdir -p "$(dirname "$HOOK_REL_PATH")"
                cp "$BOOTSTRAP_BLOB" "$HOOK_REL_PATH"
                chmod 0755 "$HOOK_REL_PATH"
                bootstrap_hook_written=true
                if [[ "$hook_state" == "drifted" ]]; then
                    log "skills-bootstrap: hook differed from the pin — overwritten with ${BOOTSTRAP_REF:0:7}."
                else
                    log "skills-bootstrap: hook added at ${BOOTSTRAP_REF:0:7}."
                fi
            fi

            if [[ "$reg_state" != "registered" ]]; then
                mkdir -p "$(dirname "$SETTINGS_REL_PATH")"
                if register_result=$("$REGISTER_SCRIPT" "$SETTINGS_REL_PATH"); then
                    [[ "$register_result" == "registered" ]] && bootstrap_registered_now=true
                    log "skills-bootstrap: settings.json — $register_result."
                else
                    log "WARN: could not register the hook in $SETTINGS_REL_PATH ($register_result) — leaving it untouched."
                fi
            fi
        fi
    fi

    add_paths=(AGENTS.md)
    { $claude_md_added || $claude_md_fixed; } && add_paths+=(CLAUDE.md)
    $bootstrap_hook_written && add_paths+=("$HOOK_REL_PATH")
    $bootstrap_registered_now && add_paths+=("$SETTINGS_REL_PATH")
    git add "${add_paths[@]}"

    # Whatever else changed, skills.lock is never among it. Cheap, absolute,
    # and checked HERE rather than trusted: a staged lock means some future
    # edit introduced a writer, and that must stop the repo, not ship. Uses
    # --name-only (empty output == not staged) rather than --quiet, whose
    # non-zero exit is indistinguishable from a git error.
    if [[ -n "$(git diff --cached --name-only -- "$LOCK_REL_PATH")" ]]; then
        fail "$repo_name: refusing to commit — $LOCK_REL_PATH is staged, and the sync must never write it."
        ((FAIL_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi

    bootstrap_note=""
    if $bootstrap_hook_written || $bootstrap_registered_now; then
        bootstrap_note="

Also delivers the skills-bootstrap SessionStart hook, fetched from
${BOOTSTRAP_REGISTRY}@${BOOTSTRAP_REF:0:7} (pinned in _agent-guidance's
repos.yml) and registered as an additional SessionStart entry. This repo's
own skills.lock declares which bundles it installs and is not touched."
    fi

    if $agents_up_to_date && $claude_md_present && ! $claude_md_fixed; then
        commit_message="chore: deliver the skills-bootstrap SessionStart hook

AGENTS.md and the CLAUDE.md bridge were already up to date.${bootstrap_note}"
    elif $agents_up_to_date && $claude_md_fixed; then
        commit_message="chore: rewrite CLAUDE.md to the @AGENTS.md bridge

AGENTS.md was already up to date, but CLAUDE.md did not import it, so
Claude Code never saw the managed guidance. Rewritten to the standard
@AGENTS.md bridge per this repo's fix_claude_md: true opt-in.${bootstrap_note}"
    elif $agents_up_to_date; then
        commit_message="chore: add CLAUDE.md bridge for AGENTS.md sync

AGENTS.md was already up to date. Adds a CLAUDE.md that imports
@AGENTS.md so Claude Code (which reads CLAUDE.md, not AGENTS.md) sees
the managed guidance.${bootstrap_note}"
    else
        commit_message="chore: sync AGENTS.md from _agent-guidance

Sections: ${sections[*]:-none}
Managed content updated by the central _agent-guidance repository.${bootstrap_note}"
    fi

    # "git commit failed" and "there was nothing to commit" are separate
    # answers and are asked separately. Control only reaches here when the diff
    # check above found something to change, so in practice every trip through
    # the second branch is a real error — and the fleet's own tooling makes one
    # error the common one: cms-platform's dev-hooks-sync installs a global
    # core.hooksPath whose secrets-scan pre-commit guard FAILS CLOSED when
    # gitleaks is absent from PATH. That is correct for a security gate and
    # fatal here, because it refuses EVERY per-repo commit. Folded into
    # "Nothing to commit." and counted as a benign skip, that printed
    # "=== Sync complete: 0 synced, 20 skipped, 0 failed ===" and exited 0
    # having pushed nothing anywhere — a whole fleet silently unsynced by a
    # green run. A refused commit is a per-repo FAILURE that names the reason.
    if git diff --cached --quiet; then
        log "Nothing to commit."
        ((SKIP_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi

    if ! commit_out=$(git commit -m "$commit_message" 2>&1); then
        fail "$repo_name: commit refused — $(head -1 <<< "$commit_out")"
        ((FAIL_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi

    # ── Deliver: push directly, fall back to a PR ──────────────────────
    # The sync App has a ruleset bypass on fleet-managed repos (declared in
    # repo-settings; see its ADR 0001), so push straight to the default
    # branch. Repos whose branch protection still rejects the push (the
    # cms-platform-managed repos) fall back to a PR with auto-merge.

    if git push origin HEAD:"$default_branch"; then
        log "Pushed directly to $default_branch."

        # Stale-PR/branch cleanup from the pre-direct-push era: an earlier
        # run of this sync (branch + PR model) may have left an open PR and
        # its head branch behind. Neither cleanup step is a repo failure.
        existing_pr=$(gh pr list --head "$BRANCH_NAME" --json number \
            --jq '.[0].number // empty' 2>/dev/null || true)
        if [[ -n "$existing_pr" ]]; then
            if ! gh pr close "$existing_pr" --comment "Superseded: the sync now pushes the managed AGENTS.md directly to the default branch (ruleset bypass declared in repo-settings fleet.yml; see its ADR 0001)."; then
                log "WARN: could not close superseded PR #$existing_pr."
            fi
        fi
        if git ls-remote --exit-code --heads origin "$BRANCH_NAME" >/dev/null 2>&1; then
            if ! git push origin --delete "$BRANCH_NAME"; then
                log "WARN: could not delete stale branch $BRANCH_NAME."
            fi
        fi

        ((OK_COUNT++)) || true
    else
        log "WARN: direct push to $default_branch rejected — falling back to PR."

        git checkout -b "$BRANCH_NAME" 2>/dev/null || git checkout "$BRANCH_NAME" 2>/dev/null || {
            fail "could not create branch in $repo_name"
            ((FAIL_COUNT++)) || true
            cd "$REPO_ROOT"; continue
        }

        # ── Force only over commits this sync itself wrote ─────────────
        # The force-push keeps the one case it exists for: a stale
        # agents-md-sync/update from the old PR-era, built on a
        # since-superseded default branch, has diverged from this run's HEAD,
        # so a plain push is rejected (fetch first) and without force the repo
        # would never receive another update. What is NOT true is the
        # justification this comment used to carry — "the branch is bot-owned,
        # this sync is its only writer". On the cms-platform-managed repos this
        # fallback opens a PR and arms auto-merge, so a maintainer can push a
        # conflict resolution or a reviewer-requested fix onto that branch; the
        # next run then overwrote it and logged only "PR #N already exists —
        # branch updated". agentskills' AGENTS.md still asserts the invariant
        # this restores: the sync must not discard reviewer commits on an open
        # PR.
        #
        # So the invariant is now the narrower, true one. Every commit the
        # remote branch carries that the default branch does not already
        # contain must have been written under $SYNC_BOT_EMAIL; one foreign
        # committer and this repo is refused and NAMED, so the run goes red
        # rather than silently overwriting a human's work. "Could not ask" is
        # never read as "nothing is there": an existing branch whose commits
        # cannot be listed refuses too.
        branch_foreign_commits=""
        ls_remote_status=0
        git ls-remote --exit-code --heads origin "$BRANCH_NAME" >/dev/null 2>&1 \
            || ls_remote_status=$?
        if [[ $ls_remote_status -eq 0 ]]; then
            # The range below needs a real merge base, which a --depth 1 clone
            # cannot supply, so the clone is DEEPENED before it is asked.
            #
            # The graft is the trap, and the reading that looks reassuring gets
            # it backwards: in the shallow clone `origin/$default_branch` has NO
            # PARENTS, and that truncates the side of the range that is
            # EXCLUDED, not the side that is included. The exclusion therefore
            # collapses to one commit and `origin/<default>..FETCH_HEAD`
            # degenerates into "the branch's whole history". Any stale
            # agents-md-sync/update whose base is an ANCESTOR of — rather than
            # equal to — the current default tip then yields the repo's OWN
            # mainline commits, whose committers are humans; the guard below
            # reads them as foreign, refuses, and counts the repo FAILED, so it
            # never receives another update. That is exactly the case the
            # force-push exists for, which the paragraph above says in as many
            # words. Measured on git 2.43, one repo and two branches: forked
            # from the current tip the range is 1 commit and is allowed; forked
            # from `main~2` it is the bot's commit plus `human commit 1` and is
            # refused on human@example.com. Nothing else differs between the two
            # runs.
            #
            # The suite reproduces it, and only recently: for as long as the
            # git mock cloned by LOCAL PATH, git took its hardlink optimisation
            # and ignored `--depth` silently, so every fixture handed these
            # scripts full history and no test in the suite had ever exercised a
            # shallow clone — this guard shipped green while refusing the one
            # case it exists for. The mock now clones over `file://`, which
            # forces upload-pack and honours `--depth`, and
            # ancorg/repo-stale-ancestor is the fixture built to be read through
            # the graft: its branch forks from an ANCESTOR of main, so
            # `origin/main..FETCH_HEAD` is [bot "stale sync", human "H0 init"]
            # shallow and [bot "stale sync"] deepened. Delete the deepening and
            # that fixture goes red on the human committer.
            #
            # `--unshallow` is fatal on a repository that is already complete
            # ("--unshallow on a complete repository does not make sense", exit
            # 128), so it is asked for only when the clone really is shallow —
            # and whether it is, is read rather than assumed. A clone that
            # cannot answer either question has not established the range, and
            # an unestablished range REFUSES: never a force-push over commits
            # nobody looked at, and never a refusal blamed on a committer this
            # run never actually read.
            deepen_ok=true
            if ! clone_is_shallow=$(git rev-parse --is-shallow-repository 2>/dev/null); then
                deepen_ok=false
            elif [[ "$clone_is_shallow" == "true" ]] \
                 && ! git fetch --unshallow origin >/dev/null 2>&1; then
                deepen_ok=false
            fi
            if ! $deepen_ok; then
                fail "$repo_name: could not deepen the shallow clone, so the range that says whose commits $BRANCH_NAME carries could not be established — refusing to force-push."
                ((FAIL_COUNT++)) || true
                cd "$REPO_ROOT"; continue
            fi

            # No refspec is given, so this fetch writes FETCH_HEAD and cannot
            # collide with the local branch of the same name that was just
            # checked out.
            if ! git fetch origin "$BRANCH_NAME" >/dev/null 2>&1 \
               || ! branch_commit_emails=$(git log --format='%ce' \
                    "origin/$default_branch..FETCH_HEAD" 2>/dev/null); then
                fail "$repo_name: could not read $BRANCH_NAME to see whose commits it carries — refusing to force-push."
                ((FAIL_COUNT++)) || true
                cd "$REPO_ROOT"; continue
            fi
            branch_foreign_commits=$(grep -v -x -F "$SYNC_BOT_EMAIL" <<< "$branch_commit_emails" || true)
        elif [[ $ls_remote_status -ne 2 ]]; then
            # 2 is ls-remote's own "no matching ref" — the branch does not
            # exist, so there is nothing on the remote to protect and the push
            # below simply creates it. Any other status is a transport or auth
            # failure, which is not an answer to the question that was asked.
            fail "$repo_name: could not tell whether $BRANCH_NAME exists — refusing to force-push."
            ((FAIL_COUNT++)) || true
            cd "$REPO_ROOT"; continue
        fi

        if [[ -n "$branch_foreign_commits" ]]; then
            fail "$repo_name: $BRANCH_NAME carries a commit this sync did not write (committer $(head -1 <<< "$branch_foreign_commits")) — refusing to force-push over it. Merge or close its PR, or delete the branch, then re-run."
            ((FAIL_COUNT++)) || true
            cd "$REPO_ROOT"; continue
        fi

        # The default branch itself stays gated by the repo's protection.
        if ! git push -u --force origin "$BRANCH_NAME"; then
            fail "push failed for $repo_name"
            ((FAIL_COUNT++)) || true
            cd "$REPO_ROOT"; continue
        fi

        # ── Open or update PR ──────────────────────────────────────────

        # Surface CLAUDE.md bridge status in the PR body — the same
        # observability gap that motivated the WARN log line above, but
        # written where a reviewer approving the sync PR will actually see it.
        pr_extra=""
        if $warn_no_import; then
            pr_extra="⚠️ **CLAUDE.md does not import \`@AGENTS.md\`** — Claude Code will not see this guidance. This sync never rewrites an existing CLAUDE.md by default. To fix, add a line containing exactly \`@AGENTS.md\` (outside code fences) to CLAUDE.md, or set \`fix_claude_md: true\` in \`.agents-sync.yml\` to let the sync propose the rewrite."
        elif $claude_md_fixed; then
            pr_extra="This PR also rewrites CLAUDE.md to the standard \`@AGENTS.md\` bridge (opted in via \`fix_claude_md: true\`) because the previous file never imported AGENTS.md."
        fi

        # The bootstrap hook gets its OWN paragraph, always, when it is part of
        # the change. A reviewer approving this PR is approving a script that
        # runs at session start and installs skills with no further prompt, and
        # the always-on context those skills cost — that must not arrive as an
        # unremarked file in a diff titled "sync AGENTS.md".
        if $bootstrap_hook_written || $bootstrap_registered_now; then
            pr_extra="${pr_extra}

**This PR also delivers \`$HOOK_REL_PATH\`** — a \`SessionStart\` hook, fetched
from [\`${BOOTSTRAP_REGISTRY}\`](https://github.com/${BOOTSTRAP_REGISTRY}/blob/${BOOTSTRAP_REF}/${BOOTSTRAP_PATH}) at
the immutable commit \`${BOOTSTRAP_REF:0:7}\` pinned in \`repos.yml\`, with its
sha256 verified before writing. On **ephemeral** surfaces only (cloud sessions,
CI runners — it no-ops on a developer's machine) it installs the bundles this
repo's own \`skills.lock\` names, verifying every skill's digest. Those skills
then cost always-on context in each such session.

It is registered as an **additional** \`hooks.SessionStart\` entry in
\`$SETTINGS_REL_PATH\`; existing entries are preserved. \`$LOCK_REL_PATH\` is
**not** modified by this sync — it is this repo's own declaration."
        fi

        existing_pr=$(gh pr list --head "$BRANCH_NAME" --json number \
            --jq '.[0].number // empty' 2>/dev/null || true)

        if [[ -n "$existing_pr" ]]; then
            log "PR #$existing_pr already exists — branch updated."
            pr_number="$existing_pr"
        else
            # --head: gh cannot infer the head branch in a fresh temp clone.
            # --base: the default branch varies across repos (main vs master),
            #         and omitting --base while passing --head can mistarget.
            # Capture the created PR's URL to derive its number for auto-merge.
            if pr_url=$(gh pr create \
                --head "$BRANCH_NAME" \
                --base "$default_branch" \
                --title "chore: sync AGENTS.md from _agent-guidance" \
                --body "$(cat <<EOF
Automated sync of the managed portion of \`AGENTS.md\` from the central
[\`_agent-guidance\`](https://github.com/${ORG}/${SELF_REPO}) repository.

**Sections included:** ${sections[*]:-none}

Content below \`## Repo-specific additions\` has been preserved.

This sync also ensures a \`CLAUDE.md\` exists that imports \`AGENTS.md\`
via \`@AGENTS.md\` — Claude Code reads CLAUDE.md, not AGENTS.md, directly,
so without this bridge it would never see the managed guidance. An
existing CLAUDE.md is left untouched unless this repo opts in via
\`fix_claude_md: true\`.

${pr_extra}
EOF
)"); then
                log "PR created."
                pr_number="${pr_url##*/}"
            else
                fail "PR creation failed for $repo_name"
                ((FAIL_COUNT++)) || true
                cd "$REPO_ROOT"; continue
            fi
        fi

        # Enable auto-merge so the PR lands on its own once checks pass. Merge
        # commit FIRST, squash only as a fallback: the fleet default is
        # merge-only (repo-settings' fleet.yml disables squash and rebase), and
        # the three cms-platform-managed repos that do keep squash allow plain
        # merges too — so `--merge` is the one method that works on every repo
        # this sync touches, and leading with squash spent a guaranteed failed
        # call per repo. The fallback stays because a repo CAN be configured
        # squash-only, and a PR left open for manual merge is an acceptable
        # degraded outcome — none of these count as a repo failure.
        if [[ -n "$pr_number" ]]; then
            if ! gh pr merge "$pr_number" --auto --merge 2>/dev/null \
                && ! gh pr merge "$pr_number" --auto --squash 2>/dev/null; then
                log "WARN: could not enable auto-merge on PR #$pr_number — left open for manual merge."
            fi
        else
            log "WARN: could not enable auto-merge — left open for manual merge."
        fi

        ((OK_COUNT++)) || true
    fi

    cd "$REPO_ROOT"
done

done

echo ""
echo "=== Sync complete: $OK_COUNT synced, $SKIP_COUNT skipped, $FAIL_COUNT failed ==="

if [[ $FAIL_COUNT -gt 0 ]]; then
    exit 1
fi
