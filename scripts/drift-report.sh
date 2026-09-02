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
# Output: drift-report.md in the repository root, or $DRIFT_REPORT_OUTPUT.
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
#   DRIFT_REPORT_OUTPUT     — where to write the report (default:
#                               <repo root>/drift-report.md). Exists so a test
#                               can name its own path instead of racing every
#                               other run for one global file; CI leaves it unset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_SCRIPT="$SCRIPT_DIR/build-agents-md.sh"
BRIDGE_SCRIPT="$SCRIPT_DIR/bridge-status.sh"
BOOTSTRAP_STATUS_SCRIPT="$SCRIPT_DIR/bootstrap-status.sh"
HOOK_REL_PATH=".claude/hooks/skills-bootstrap.sh"
SETTINGS_REL_PATH=".claude/settings.json"
LOCK_REL_PATH="skills.lock"
# Every other input this script takes is parameterized — REPOS_YML just below,
# SYNC_OWNERS, GITHUB_REPOSITORY_OWNER, SYNC_SELF_REPO, and the PATH the mocks
# ride in on — and the one OUTPUT was not, so two runs pointed at different
# fixtures still wrote the same file and a reader of it got whichever finished
# last. That is a determinism hole in the suite rather than a production
# concern: nothing in CI sets this, so the default keeps the published path
# exactly where drift-report.yml expects to find it.
OUTPUT_FILE="${DRIFT_REPORT_OUTPUT:-$REPO_ROOT/drift-report.md}"
# Machine-readable companion to the report, for a reader that is a workflow
# rather than a person. Derived from OUTPUT_FILE so the two always travel
# together, including when a caller redirects the report to its own path.
SKILLS_SIDECAR="${OUTPUT_FILE%.md}-skills-unclassified.txt"
# The OTHER direction, in its OWN file rather than a second section of that
# one: the two findings resolve differently — one is answered by classifying a
# repo, the other by looking a name up — and each has to be able to close while
# the other stands.
SKILLS_ORPHAN_SIDECAR="${OUTPUT_FILE%.md}-skills-orphans.txt"
MARKER="## Repo-specific additions"
TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M UTC")
BRANCH_NAME="agents-md-sync/update"
SELF_REPO="${SYNC_SELF_REPO:-_agent-guidance}"

# A run-scoped scratch dir for every short-lived temp file below, paired with an
# EXIT trap — the shape sync.sh, bump-consumer-locks.sh and bump-hook-pin.sh
# already have and this script did not.
#
# The files themselves are each removed by an unconditional `rm -f` a line or
# two after they are read, so on every path the script actually takes nothing is
# left behind. The trap is for the paths it does NOT take: a signal between the
# mktemp and the rm — a cancelled Actions job, a `timeout-minutes` wall, an
# operator ^C during a long `gh` call — leaves the file in $TMPDIR forever.
#
# It is installed HERE, at the top, rather than beside the ignore probe it also
# cleans up, because that one is installed ~470 lines down and read_repos_yml
# runs before it: a trap that exists only from the ignore probe onward cannot
# cover the earliest capture in the script. That ordering is the whole reason
# this is one trap at the top rather than an extension of the old one.
WORK_DIR=$(mktemp -d)

# The ONE EXIT trap this script installs. Both directories are removed here
# rather than from two handlers, because a second `trap ... EXIT` REPLACES the
# first rather than adding to it — installing one per resource would silently
# disarm whichever was registered earlier.
#
# The ignore probe is removed from here rather than from its own handler for a
# second reason too: it outlives any single call (it is memoized and reused
# across every repo in the run), so the end of the run is the earliest safe
# moment to remove it either way.
cleanup() {
    rm -rf "$WORK_DIR"
    # `:-` because this runs on paths that exit long before IGNORE_PROBE_DIR is
    # declared, and `set -u` would otherwise make the trap itself the error. The
    # emptiness test is not here to stop `rm -rf ""`, which is a silent no-op;
    # it is here to keep the trap's exit status 0, so cleanup can never rewrite
    # the script's own exit code. It also leaves the `:?` unreachable — that is a
    # backstop for the day the test is dropped, not a path this takes today.
    if [[ -n "${IGNORE_PROBE_DIR:-}" ]]; then
        rm -rf "${IGNORE_PROBE_DIR:?}"
    fi
}
trap cleanup EXIT

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

# pick_diagnostic <producer> <file> — the ONE line of a captured stderr that a
# one-line failure report should quote.
#
# Every caller has the same shape: a command's stderr was redirected to a file
# so the failure could be named without quoting the command's STDOUT, which on
# a gh HTTP error is the API's raw response body — and AGENTS.md ("Sanitize
# error output") forbids putting one of those in a public log. Separating the
# streams settled WHICH STREAM to quote. It left open WHICH LINE, and `head -1`
# is wrong for both producers this repo captures, in OPPOSITE directions. That
# is why the producer is a parameter and not one rule:
#
#   gh    — gh's ordinary notices are `gh: `-prefixed too and are written
#           BEFORE the error it is being asked about, so the first line is the
#           notice and the operator is sent after the wrong fault. gh writes
#           its own status as `gh: <message> (HTTP <code>)`, so that line is
#           preferred, then any `gh: ` line, then the first non-blank line.
#   tool  — python3 and yq write their warnings and traces FIRST and the fatal
#           line LAST: a traceback ends in `SomeError: message`, and argparse
#           prints its `usage:` banner first and `<prog>: error: ...` last. So
#           the LAST non-blank line is the one that says what went wrong.
#
# Measured with the twelve stderr shapes these callers actually see (a
# deprecation notice then `gh: Not Found (HTTP 404)`; an auth-expiry warning
# then `(HTTP 403)`; a bare `(HTTP 401)`; a line gh did not write; empty; a
# yq deprecation then `Error:`; an argparse usage banner then `: error: `; a
# python traceback): `gh` picks the status line in each gh case and `tool` the
# fatal line in each tool case, where plain `head -1` picks the notice and the
# usage banner.
#
# Blank lines are skipped in EVERY branch, which is not tidiness: measured on
# a stderr of "real error\n\n\n", plain `tail -1` quotes the empty string, and
# on "\n\nmock gh: boom\n" plain `head -1` does the same. A producer that pads
# its stderr would otherwise be reported as having said nothing.
#
# An unknown producer returns 2 with a message rather than an empty string,
# because it can only be a construction error here: every call site passes a
# literal.
#
# This is NOT generator_error_line's job, which stays where it is in
# bump-consumer-locks.sh: that one reads a captured STRING of the lock
# generator's MERGED output and additionally knows that generator's own
# `ERROR:` marker. This one reads a stderr FILE.
#
# Duplicated in sync.sh, drift-report.sh, bump-consumer-locks.sh and
# bump-hook-pin.sh for the reason given above the yq preflight, and it must
# move with them.
pick_diagnostic() {
    local producer="$1" file="$2" line
    # A file the caller never created (its mktemp failed) is not a fault here;
    # the caller reports that itself, and it has no diagnostic to quote.
    [[ -r "$file" ]] || return 0
    case "$producer" in
        gh)
            line=$(grep -m1 '^gh: .*(HTTP ' "$file" \
                || grep -m1 '^gh: ' "$file" \
                || grep -m1 -v '^[[:space:]]*$' "$file" \
                || true)
            ;;
        tool)
            # `|| true` inside the substitution because `set -o pipefail` makes
            # a grep that selected nothing (an empty or all-blank stderr) fail
            # the whole pipeline.
            line=$(grep -v '^[[:space:]]*$' "$file" | tail -1 || true)
            ;;
        *)
            echo "::error::pick_diagnostic: unknown producer '$producer'" >&2
            return 2
            ;;
    esac
    printf '%s' "$line"
}

# Fetch a file's contents from a repo's default branch.
#
#   stdout : the file's bytes, or empty when the file is genuinely absent
#   exit 0 : the bytes are COMPLETE — verified against the API's own byte count —
#            or the file is ESTABLISHED absent: a 404 on the path from a repo
#            this credential can otherwise read
#   exit 2 : the request could not be turned into either of those answers. The
#            caller must not draw a conclusion from stdout; there is nothing
#            usable.
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
# Note what is NOT claimed here: no short read was ever reproduced through this
# function. Fetching the same file over the same endpoint with curl returns a
# complete body, and the `jq -r .content | base64 -d` half handles a 95 KB
# payload correctly in isolation. #81's reported symptom has since been traced
# to something else entirely — the `echo "$current_agents" | grep -q` further
# down, whose writer took SIGPIPE on any AGENTS.md past the pipe buffer; see the
# marker check for the measurements. This byte-count guard stays anyway, as a
# guard rather than as that bug's fix: it does not need to know a cause to
# refuse to publish a consequence.
#
# A FAILED REQUEST IS NOT AN ABSENT FILE, and for a long time this function said
# it was. `gh api ... || return 0` returns "the file is not there" for a 401, a
# 403, a 429, a 5xx, a DNS failure, a TLS failure — every transport and auth
# fault there is — so every caller's `rc != 0` branch was blind to the whole
# class, and the cascade the ledger below exists to stop fired anyway: a
# credential that lost Contents permission mid-run publishes `missing`,
# `no-lock`, **no-entry** and **blocked** as confident verdicts about files it
# never saw. Two surfaces are read to tell the cases apart, because neither is
# universal: real gh writes `gh: <message> (HTTP <code>)` to stderr, and on an
# HTTP error it also prints the API's raw error body to stdout with the --jq
# filter unapplied, which carries `.status`. A 404 is then still not enough on
# its own — GitHub answers 404 rather than 403 when a credential is not
# authorized to know a repo exists — so the repo itself is probed, and only a
# 404 on the path from a repo this credential CAN read is reported as an
# absence. Anything else, including a request whose status could not be
# determined at all, is exit 2: "could not ask" must never render as "the answer
# is none". Discard stdout on failure either way; never decode it.
fetch_file_content() {
    local repo="$1" path="$2"
    local json size tmp actual err err_text why rc=0 http_status http_re

    err=$(mktemp -p "$WORK_DIR") || return 2
    json=$(gh api "repos/$repo/contents/$path" 2>"$err") || rc=$?
    # From the FILE rather than through a pipe, and ONE line of it: gh's own
    # diagnostic is what is wanted here, not whatever body followed it, and the
    # house rule forbids echoing an API response into a public log.
    #
    # The line carrying the STATUS, though, not merely the first one — and here
    # that is more than tidiness, because $err_text is read TWICE: the `(HTTP
    # nnn)` regex below extracts the status from it, and the operator-facing
    # message quotes it. gh's deprecation and auth-expiry notices are themselves
    # `gh: `-prefixed and arrive BEFORE the error, so `head -1` handed both uses
    # the notice: the status match then fell through to the stdout body's
    # `.status`, and where that body carries none, a genuine 404 stopped looking
    # like one and the row went fetch-failed instead of absent. Measured with a
    # gh stubbed to print "gh: this API endpoint is deprecated" then "gh: Not
    # Found (HTTP 404)" and exit 1.
    #
    # That preference is now pick_diagnostic's, spelled once for all four
    # scripts; this call site is the reason the `gh` arm prefers the status line
    # rather than merely a `gh: ` one.
    err_text=$(pick_diagnostic gh "$err")
    rm -f "$err"

    if [[ "$rc" -ne 0 ]]; then
        http_re='\(HTTP ([0-9]{3})\)'
        http_status=""
        if [[ "$err_text" =~ $http_re ]]; then
            http_status="${BASH_REMATCH[1]}"
        else
            http_status=$(jq -r '.status // empty' <<<"$json" 2>/dev/null) || http_status=""
        fi

        # `>/dev/null 2>&1` on the probe on purpose: its BODY is of no interest,
        # only whether the repo answers this credential at all. A probe that
        # itself fails leaves the `&&` false, which is the fail-closed side.
        if [[ "$http_status" == "404" ]] && gh api "repos/$repo" >/dev/null 2>&1; then
            return 0
        fi

        # Named rather than interpolated inline, so the log distinguishes "gh
        # told us why" from "gh exited $rc and said nothing we could parse" —
        # which is itself a different fault to chase.
        why="${err_text:-${http_status:+HTTP $http_status}}"
        echo "::error::$repo/$path: the contents read failed (${why:-gh exit $rc, no diagnostic}) and this run could not establish that the file is merely absent — refusing to report it as one (issue #81)" >&2
        return 2
    fi

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

    tmp=$(mktemp -p "$WORK_DIR") || return 2
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
    if ! err_file=$(mktemp -p "$WORK_DIR"); then
        echo "::error::repos.yml: could not create a temp file for yq's diagnostics" >&2
        exit 2
    fi
    out=$(yq -r "$expr" "$REPOS_YML" 2>"$err_file") || rc=$?
    # Read and removed UNCONDITIONALLY, before the branch, so the success path
    # cannot leak the file either.
    err=$(pick_diagnostic tool "$err_file")
    rm -f "$err_file"
    if [[ $rc -ne 0 ]]; then
        echo "::error::repos.yml: yq failed reading '$expr' — ${err:-no output}" >&2
        exit 2
    fi
    printf '%s\n' "$out"
}

# ── repos.yml must EXIST ───────────────────────────────────────────────────
#
# Absence is not an empty config, and `if [[ -f "$REPOS_YML" ]]` said it was.
# This is the yq-preflight incident again in its last remaining costume: that
# guard taught this script to keep three answers apart once yq is running, and
# then the file simply not being there walked straight past all of it, leaving
# every list empty with no error and no log line. Measured against a nonexistent
# REPOS_YML: this script printed "Found 8 repo(s)", announced
# "Checking testorg/repo-excluded ...", and published two rows naming a repo
# repos.yml excludes — the same contamination the exclusion list exists to
# prevent, reached by deleting the list rather than by breaking the reader.
#
# `exit 2` rather than a warning, and rather than bump-hook-pin.sh's `exit 1`:
# the same refusal code the yq preflight and read_repos_yml already use here, so
# every "this script cannot trust repos.yml" exit from this file is one number.
if [[ ! -f "$REPOS_YML" ]]; then
    echo "::error::no repos.yml at $REPOS_YML — this script's exclusion list, default sections, cron classification and skills-bootstrap allowlist all come from it, and an absent file is not an empty one." >&2
    exit 2
fi

# ── Load central repos.yml (exclusions + default sections) ─────────────────

EXCLUDED_REPOS=()
DEFAULT_SECTIONS=()

excluded_raw=$(read_repos_yml '.exclude // [] | .[]')
while IFS= read -r r; do
    [[ -n "$r" ]] && EXCLUDED_REPOS+=("$r")
done <<< "$excluded_raw"

sections_raw=$(read_repos_yml '.default_sections // [] | .[]')
while IFS= read -r s; do
    [[ -n "$s" ]] && DEFAULT_SECTIONS+=("$s")
done <<< "$sections_raw"

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

cron_classified_raw=$(read_repos_yml \
    '((.cron_coverage.fleet // []) + (.cron_coverage.out_of_scope // [])) | .[]')
while IFS= read -r r; do
    [[ -n "$r" ]] && CRON_CLASSIFIED+=("$r")
done <<< "$cron_classified_raw"

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

BOOTSTRAP_REGISTRY=$(read_repos_yml '.skills_bootstrap.registry // ""')
BOOTSTRAP_PATH=$(read_repos_yml '.skills_bootstrap.path // ""')
BOOTSTRAP_REF=$(read_repos_yml '.skills_bootstrap.ref // ""')
bootstrap_repos_raw=$(read_repos_yml '.skills_bootstrap.repos // [] | .[]')
while IFS= read -r r; do
    [[ -n "$r" ]] && BOOTSTRAP_REPOS+=("$r")
done <<< "$bootstrap_repos_raw"

bootstrap_allowlisted() {
    local short="${1##*/}" entry
    for entry in ${BOOTSTRAP_REPOS[@]+"${BOOTSTRAP_REPOS[@]}"}; do
        [[ "$short" == "$entry" ]] && return 0
    done
    return 1
}

# ── skills-bootstrap CLASSIFICATION (the other half of the allowlist) ──────
#
# `repos:` is a decision and `out_of_scope:` is the other half of the same
# decision; a repo in neither has not been decided about, and until this block
# existed that state was indistinguishable from a settled one. Exactly the
# shape `cron_classified` above audits, for the reason ADR 0003 gives, and now
# ADR 0011: the offline gate (scripts/check-registry.js) can only assert the
# two keys do not CONTRADICT each other, because whether they together still
# COVER the account is a discovery question and discovery happens here.
#
# Free, like the cron one: the names are already in hand and this is a set
# lookup, not another API call.
SKILLS_CLASSIFIED=()
skills_classified_raw=$(read_repos_yml \
    '((.skills_bootstrap.repos // []) + ((.skills_bootstrap.out_of_scope // []) | map(.repo))) | .[]')
while IFS= read -r r; do
    [[ -n "$r" ]] && SKILLS_CLASSIFIED+=("$r")
done <<< "$skills_classified_raw"

skills_classified() {
    local short="${1##*/}" entry
    for entry in ${SKILLS_CLASSIFIED[@]+"${SKILLS_CLASSIFIED[@]}"}; do
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
# a fresh temp dir that the EXIT trap above never sees.
IGNORE_UNREADABLE_PATH=""

# Removed by `cleanup` at the top of this script, not by a handler of its own:
# a second `trap ... EXIT` would replace that one rather than join it. On a run
# that probed nothing — no allowlisted repo missing its hook — the variable is
# still "", which `cleanup` handles.

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

# Written unconditionally and emptied here, so an ABSENT sidecar means the
# script did not run rather than that it found nothing -- the same distinction
# this file draws everywhere else. Truncated at the same point as the report
# and appended to per owner: doing it inside the loop would leave only the last
# owner's names, which is a wrong answer that looks like a right one.
: > "$SKILLS_SIDECAR"
# Same contract, one exception written at the block that fills it: on a run
# where an owner could not be listed this file is REMOVED rather than left
# empty, because an empty one is this script saying it looked and found
# nothing, and the nudge step reads that as "close the issue".
: > "$SKILLS_ORPHAN_SIDECAR"

# Base GH_TOKEN captured before the per-owner loop, so each iteration can
# restore it when the owner has no per-owner token of its own (owner A's
# per-owner token must not leak into owner B's iteration).
BASE_GH_TOKEN="${GH_TOKEN:-}"

# Owners whose repo listing could not be read at all this run. Reported in the
# published table (below) and counted here, because "this owner has no drift" and
# "this owner was never looked at" must not render as the same silence.
OWNER_FAILURES=()

# Every short repo name discovery returned, ACROSS the owners — accumulated
# here rather than inside the loop because the question it answers ("does the
# registry claim a name nothing returns?") is asked once, of the whole account.
# Asked per owner it answers itself wrong and confidently: with SYNC_OWNERS
# ordered "Adam-S-Daniel jodidaniel", every repo of the owner not currently
# being scanned reads as missing — issue #95 measured a single-org pass
# reporting 5 spurious names against the test fixtures.
# `test_drift_report_registry_orphan_multi_owner` holds it, in both directions:
# scanning testorg alone, testorg2's only repo IS a finding; scanning both, it
# is not, while a name neither owner holds still is.
DISCOVERED_SHORT_NAMES=()

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
#
# Streams captured SEPARATELY, for the reason read_repos_yml gives above: what
# this returns is the FLEET, split into $REPOS and looped over, so a line gh
# writes to stderr while exiting 0 — a deprecation notice, an auth-expiry
# warning — does not merely decorate the log, it becomes a repository NAME and
# this script PUBLISHES it as a table row. `sed '/^$/d'` is no defence: the
# injected line is not blank. Measured against this script with a gh stubbed to
# print one "gh: warning: your token will expire soon" line to stderr and one
# real repo to stdout, exit 0 — merged, drift-report.md gained a
# **fetch-failed** row titled `gh: warning: your token will expire soon`,
# linked to github.com/gh: warning:...; separated, only the real repo is
# reported. mktemp is checked because this loop counts per-owner failures
# rather than aborting: under `set -e` an unchecked assignment here would kill
# the run with no report to publish at all, which is the fault the paragraph
# above exists to prevent.
if ! repo_list_err_file=$(mktemp -p "$WORK_DIR"); then
    echo "::error::$ORG: could not create a temp file to capture gh's diagnostics" >&2
    repo_list_rc=1
    repo_list_err="mktemp failed"
    # The published row's note, set on BOTH arms rather than written once below.
    # Until this existed the shared block asserted "`gh repo list <org>` failed"
    # for every non-zero $repo_list_rc — and on THIS arm gh was never invoked at
    # all, so the report published a claim about a call the run did not make.
    # Before the mktemp branch existed the only way in was a genuine gh failure
    # and the sentence was true; adding a second entrance is what made it false,
    # which is the class of defect lib/bump-pr-claims.sh exists to make
    # unwritable. Each arm now names the cause it actually established.
    repo_list_note="this run could not create a temp file to capture \`gh repo list\`'s diagnostics, so the listing was never attempted — no repo under this owner was checked this run; see the run log"
else
    repo_list_rc=0
    repo_list_raw=$(
        gh repo list "$ORG" \
            --no-archived \
            --source \
            --json nameWithOwner \
            --limit 1000 \
            --jq '.[].nameWithOwner' 2>"$repo_list_err_file"
    ) || repo_list_rc=$?
    # Read and removed unconditionally, before the branch, so neither the
    # `continue` below nor the success path can leak the file.
    repo_list_err=$(pick_diagnostic gh "$repo_list_err_file")
    rm -f "$repo_list_err_file"
    repo_list_note="\`gh repo list $ORG\` failed — no repo under this owner was checked this run; see the run log"
fi
if [[ $repo_list_rc -ne 0 ]]; then
    echo "::error::$ORG: could not list repos — ${repo_list_err:-no diagnostic output from gh}" >&2
    {
        echo ""
        echo "## $ORG"
        echo ""
        echo "> Organization: \`$ORG\` — **not scanned this run**"
        echo ""
        echo "| Repository | Status | Has marker | CLAUDE.md bridge | skills-bootstrap | Open PR | Sections | Notes |"
        echo "|------------|--------|------------|-------------------|------------------|---------|----------|-------|"
        echo "| *(owner not readable)* | **fetch-failed** | ? | ? | ? | ? | ? | $repo_list_note |"
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
# The SKILLS side is NOT computed here: its denominator is the delivery set,
# which the exclude filter below has not produced yet. See the block after it.
SKILLS_UNCLASSIFIED=()
while IFS= read -r r; do
    [[ -n "$r" ]] || continue
    # RAW, deliberately, and the orphan pass after the loop is why it matters a
    # second time: $SELF_REPO and every `exclude:`d repo are dropped just below,
    # and both are names the registry legitimately carries. Take the union from
    # the filtered list and each one is reported as a name that no longer
    # exists, in an account where it is sitting in plain sight.
    DISCOVERED_SHORT_NAMES+=("${r##*/}")
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

# ── Unclassified for skills-bootstrap, over the DELIVERY set ───────────────
# A DIFFERENT denominator from the cron pass above, and the difference is the
# point rather than an inconsistency. Cron classifies every discovered name,
# self and `exclude:`d repos included, because a cron can exist in any of them.
# Skills delivery cannot reach either: sync.sh drops $SELF_REPO before the loop
# and never visits an excluded repo, so demanding a skills decision about one
# would demand a decision that could not be acted on. `REPOS` here is exactly
# what the sync visits, which is the set the question is about.
for r in ${REPOS[@]+"${REPOS[@]}"}; do
    skills_classified "$r" || SKILLS_UNCLASSIFIED+=("$r")
done

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

    # Every path this row could not READ, or could not UNDERSTAND. A name lands
    # here on a verified short read, on a request that failed for any reason this
    # run could not resolve to "the file is simply not there" (a 401, a 403, a
    # rate limit, a 5xx, a DNS or TLS fault, a 404 on a repo the credential
    # cannot see at all), and on bytes that arrived whole but would not parse.
    # What it never means is "the file is not there" — `fetch_file_content`
    # returns 0 for that, and only after establishing it against the repo — which
    # is the distinction #81 turned on. One entry is enough to withhold the whole
    # row; see the block just before the row is written.
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
        # The bytes arrived; that is not the same as the bytes being usable, and
        # the guard above only covered the first half. `yq ... 2>/dev/null ||
        # true` inside a process substitution discarded BOTH halves of the
        # answer — the status the substitution swallows by design (the trap named
        # above read_repos_yml), and the one `|| true` throws away on top — so a
        # `.agents-sync.yml` that arrived whole and does NOT parse collapsed to an
        # empty section list. `expected` was then built from zero sections,
        # diffed against an AGENTS.md that is in fact correct, and the row
        # published **drift-detected**: word for word the wrong answer the fetch
        # guard above it exists to prevent, reached through the other door.
        #
        # Command substitution, so the status is the yq run's own. A parse
        # failure joins the same ledger as a failed read, because to this row
        # they are the same fact: no section list, so nothing honest to compare
        # against. `sections_rc` carries it forward to the comparison below for
        # the same reason.
        #
        # Streams SEPARATED rather than folded together so the log could quote
        # what yq objected to. Folding them answered the failure path's question
        # by corrupting the success path's answer: what this returns is DATA —
        # split into $sections, which `expected` is built from — and yq writes
        # to stderr while exiting 0 (the mikefarah build this repo pins answers
        # `-j` with the right value on stdout, "Flag --tojson has been
        # deprecated" on stderr, and exit 0; measured, v4.44.3). A notice that
        # changed nothing therefore produced this report's worst output:
        # **drift-detected** against an AGENTS.md that is in fact correct — the
        # same wrong answer the fetch guard above exists to prevent, reached
        # through a third door. Measured against this script with a yq wrapper
        # printing one deprecation line to stderr and then succeeding, over a
        # repo whose .agents-sync.yml says [python] and whose AGENTS.md was
        # built from [python] — merged: "| **drift-detected** | ... | Flag --r
        # has been deprecated, please use --unwrapScalar python |"; separated:
        # "| **up-to-date** | ... | python |". mktemp failure takes the parse
        # failure's own path, because the row cannot establish a section list
        # either way and must not print one it did not verify.
        parse_rc=0
        parse_err=""
        if ! parse_err_file=$(mktemp -p "$WORK_DIR"); then
            parse_rc=2
            parse_err="could not create a temp file for yq's diagnostics"
        else
            remote_sections_raw=$(yq -r '.sections // [] | .[]' <<<"$remote_yaml" 2>"$parse_err_file") || parse_rc=$?
            # Read and removed unconditionally, before the branch, so the
            # success path cannot leak the file either.
            parse_err=$(pick_diagnostic tool "$parse_err_file")
            rm -f "$parse_err_file"
        fi
        if [[ "$parse_rc" -ne 0 ]]; then
            echo "::error::$repo_name/.agents-sync.yml: yq could not parse it — ${parse_err:-no diagnostic output}" >&2
            fetch_failed_paths+=(".agents-sync.yml")
            sections_rc=$parse_rc
            sections_display="?"
        else
            while IFS= read -r s; do
                [[ -n "$s" ]] && sections+=("$s")
            done <<< "$remote_sections_raw"
            sections_display="${sections[*]:-none}"
        fi
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
        #
        # A HERE-STRING, never `echo "$current_agents" | grep -q`, and that is
        # issue #81's actual root cause rather than a style preference. `grep -q`
        # exits at the FIRST match; when the payload is bigger than the kernel's
        # 64 KiB pipe buffer the `echo` on the writing end still has bytes to
        # push, takes SIGPIPE, and dies 141 — and `set -o pipefail` promotes that
        # 141 to the PIPELINE's status even though grep itself exited 0. Sitting
        # in an `if`, the 141 does not end the run; it merely routes to the else,
        # so a marker that IS present publishes as `Has marker: no`, the file is
        # then diffed whole-file against the expected managed block, and the repo
        # goes out as **drift-detected**.
        #
        # It is a RACE, not a threshold, which is why #81's own single-shot probe
        # looked like it disproved this: the writer only loses when grep gets
        # there first, so one green test clears nothing. Measured against a file
        # shaped like `cms-platform/AGENTS.md` (95 kB, marker 42% of the way in),
        # wrong answers per 20 trials were 48 kB: 0, 56 kB: 0, 64 kB: 0, 72 kB: 4,
        # 95 kB: 20 — and 0 out of 20 at every one of those sizes, plus 0 out of
        # 20 at 1 MB, once it is a here-string. The live confirmation is the
        # 2026-08-28 19:20 UTC dashboard run, which reported 17 of 18 repos
        # up-to-date and `cms-platform` — the fleet's largest AGENTS.md — as the
        # single drift-detected row, `Has marker: no`, while
        # `git show origin/main:AGENTS.md | grep -c '^## Repo-specific additions$'`
        # on that same repo answers 1.
        #
        # A here-string has no writer to signal: bash materialises the whole
        # thing — a temp file, or a pre-filled pipe when it is small enough to
        # fit — before grep is started, so there is no process left holding the
        # write end to be killed. `scripts/sync.sh` greps the FILE directly and
        # was never exposed to any of this, which is why the fleet kept syncing
        # correctly the whole time this dashboard said it had drifted.
        if grep -qxF -- "$MARKER" <<<"$current_agents"; then
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
    # One unusable read is enough to make the whole row untrustworthy, so Status
    # says so rather than publishing a verdict assembled from bytes that never
    # all arrived, never arrived at all, or arrived and made no sense. Until now
    # that promise was kept at exactly ONE of this loop's call sites: a short read
    # of anything but AGENTS.md still published a confident cell — `missing`,
    # `no-lock`, **no-entry**, **blocked** — which is #81's entire failure mode
    # wearing a different file's name, and it made the legend's "every other
    # column is withheld rather than guessed" false. The Notes line stays
    # deliberately vague about WHICH fault it was, because naming one in the
    # published dashboard would mean choosing between a short read, a 403 and a
    # parse error on evidence the row does not carry; the `::error::` lines in the
    # run log carry that, per file, without guessing.
    #
    # Placed after the open-PR check on purpose: **pr-open** is a verdict too, and
    # a row nobody could read must not be overwritten by one.
    if [[ ${#fetch_failed_paths[@]} -gt 0 ]]; then
        status="**fetch-failed**"
        failed_list=$(printf '`%s`, ' "${fetch_failed_paths[@]}")
        notes="${notes:+$notes; }could not read or parse ${failed_list%, } — the columns those files feed are withheld (\`?\`) rather than guessed; see the run log for which fault it was"
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

# ── Unclassified for skills-bootstrap ──────────────────────────────────────
# Same contract as the cron block above and the same silence-is-the-pass rule.
# A name here is a repo nobody has decided about: it belongs in
# `skills_bootstrap.repos` (delivered) or `skills_bootstrap.out_of_scope`
# (with the reason it is not), and defaulting either way silently is the
# failure ADR 0011 exists to end.
#
# A SIDECAR as well as a report line, because a report is read by a human who
# opens it and this needs to reach one who does not. Written unconditionally,
# empty when there is nothing to say, so its absence means the script did not
# run rather than that it found nothing — the same distinction this file makes
# everywhere else.
if [[ ${#SKILLS_UNCLASSIFIED[@]} -gt 0 ]]; then
    {
        echo ""
        echo "> **Unclassified for skills-bootstrap (${#SKILLS_UNCLASSIFIED[@]}):**"
        for r in "${SKILLS_UNCLASSIFIED[@]}"; do
            echo "> - \`$r\` — add it to \`repos.yml\` under \`skills_bootstrap.repos\` (deliver the hook) or \`skills_bootstrap.out_of_scope\` (with the reason not to)"
        done
    } >> "$OUTPUT_FILE"
    printf '%s\n' "${SKILLS_UNCLASSIFIED[@]}" >> "$SKILLS_SIDECAR"
    echo "  ${#SKILLS_UNCLASSIFIED[@]} repo(s) unclassified for skills-bootstrap"
fi

done

# ── Registry names discovery did not return (the orphan direction) ─────────
#
# The mirror of the SKILLS_UNCLASSIFIED block inside the loop, and the half
# that was missing: that one flags a repo the registry does not classify, this
# one flags a name the registry claims and discovery no longer returns. Three
# `civic-*` repos were deleted from GitHub and sat in this fleet's
# configuration for weeks — nothing enumerated in this direction, so nothing
# said so, and a propagation verifier seeded from that configuration counted
# them as live consumers and reported them as missing text they could not
# possibly have had. `scripts/check-registry.js` cannot close it: it is offline
# by design, and this is a discovery question.
#
# OUTSIDE the owner loop for the reason DISCOVERED_SHORT_NAMES gives, and
# SILENT when any owner went unread. An owner whose listing failed contributes
# no names at all, so every repo it holds would read as gone — the
# 404-means-not-authorised ambiguity in its most expensive form, an alarm
# naming an entire owner's fleet on the one night its App install was missing.
# The sidecar is WITHHELD rather than written empty on that path: empty means
# "looked, found nothing", which the nudge step reads as permission to close.
if [[ ${#OWNER_FAILURES[@]} -gt 0 ]]; then
    rm -f "$SKILLS_ORPHAN_SIDECAR"
    echo "  ${#OWNER_FAILURES[@]} owner(s) unread — registry-orphan check skipped and its sidecar withheld; nothing is concluded from a partial enumeration"
else
    SKILLS_ORPHANS=()
    for entry in ${SKILLS_CLASSIFIED[@]+"${SKILLS_CLASSIFIED[@]}"}; do
        found=no
        for short in ${DISCOVERED_SHORT_NAMES[@]+"${DISCOVERED_SHORT_NAMES[@]}"}; do
            if [[ "$short" == "$entry" ]]; then
                found=yes
                break
            fi
        done
        [[ "$found" == no ]] && SKILLS_ORPHANS+=("$entry")
    done

    # Only `skills_bootstrap`'s two keys. `cron_coverage` is NOT read here: it
    # counts over a wider denominator that deliberately names repos discovery
    # can never reach — the account's two forks and the third-owner
    # `superoutrigger` — so sweeping it in would fire every night forever.
    if [[ ${#SKILLS_ORPHANS[@]} -gt 0 ]]; then
        {
            echo ""
            echo "> **Registry names discovery did not return (${#SKILLS_ORPHANS[@]}):**"
            for entry in "${SKILLS_ORPHANS[@]}"; do
                echo "> - \`$entry\` — classified under \`skills_bootstrap\` in \`repos.yml\`, but no owner scanned this run returned it. A name to look at, not a verdict: it may be private, renamed, transferred, or unreadable by this run's credential. Confirm before removing the entry"
            done
        } >> "$OUTPUT_FILE"
        printf '%s\n' "${SKILLS_ORPHANS[@]}" >> "$SKILLS_ORPHAN_SIDECAR"
        echo "  ${#SKILLS_ORPHANS[@]} registry name(s) discovery did not return"
    fi
fi

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
    echo "| **fetch-failed** | A file this row is built from could not be read, or could not be understood — the request failed for a reason this run could not resolve to a plain absence (a 401, a 403, a rate limit, a 5xx, a network fault, or a 404 on a repo the credential cannot see at all), or the decoded byte count disagreed with the API's own \`size\`, or the bytes arrived whole and would not parse. **Notes** names the file; every column it feeds is withheld as \`?\` rather than guessed; see issue #81 |"
    echo ""
    echo "**CLAUDE.md bridge legend**"
    echo ""
    echo "| Bridge status | Meaning |"
    echo "|---------------|---------|"
    echo "| bridge-ok | CLAUDE.md imports \`@AGENTS.md\` (line-start, outside code fences) |"
    echo "| **no-import** | CLAUDE.md exists but never imports \`@AGENTS.md\` — Claude Code will not see the managed guidance |"
    echo "| missing | No CLAUDE.md yet — sync adds the bridge in its next PR |"
    echo "| ? | \`CLAUDE.md\` could not be read — withheld, not guessed (the row reads **fetch-failed**) |"
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
    echo "| ? | A file this column is decided from (the hook, \`skills.lock\`, \`.claude/settings.json\`, or the repo's ignore rules) could not be read — withheld, not guessed (the row reads **fetch-failed**) |"
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
