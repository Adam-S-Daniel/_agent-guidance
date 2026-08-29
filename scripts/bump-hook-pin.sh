#!/usr/bin/env bash
set -euo pipefail
#
# bump-hook-pin.sh — re-pin repos.yml's `skills_bootstrap.ref`/`sha256` onto
# the registry's current skills-bootstrap hook, as one pull request on THIS
# repo.
#
# WHY THIS EXISTS. Two pins decide what an ephemeral session actually gets, and
# until this script they moved on completely different cadences:
#
#   * each consumer's `skills.lock` — WHICH bundles at WHICH commit. Re-pinned
#     nightly by bump-consumer-locks.sh, so a skill added to the registry
#     reaches the fleet within a day, with no human in the loop.
#   * repos.yml's `skills_bootstrap.ref`/`sha256` — the HOOK that reads that
#     lock. Moved only when somebody hand-edited this file, which had happened
#     five times in the six days after delivery shipped and not once since.
#
# The gap that produced this script, measured 2026-08-25: agentskills #131
# ("install the union of every discovered lock in a multi-repo session") merged
# as da48d29 and the nightly bumper re-pinned adamdaniel.ai's lock onto that
# very commit the same night — while its hook stayed at f92569e, the pin from
# 2026-08-19. So the fleet was running #131's LOCK against a pre-#131 HOOK, and
# nothing anywhere was going to notice: the pin was internally consistent (the
# delivered bytes matched the recorded digest exactly), the sync was not
# lagging, and there was no queued work. There was simply nothing that ever
# proposed a new value. That is the shape of failure this closes.
#
# WHAT IT DELIBERATELY DOES NOT DO — auto-merge. bump-consumer-locks.sh sweeps
# its own previous night's PRs because a lock bump's blast radius is one repo's
# skills, reverted by one revert. Merging THIS pull request is what makes
# sync.yml fan a new hook into all ten allowlisted repos on its next run
# (`repos.yml` is in that workflow's `paths:` filter precisely so it does), and
# a hook is code that runs at the start of every ephemeral session in every one
# of them. That is a human's call, so this script opens the PR and stops. See
# docs/decisions/0010.
#
# ANTI-CHURN, and why it is keyed on the DIGEST rather than the commit. A
# re-pinner keyed on "is `ref` the registry's newest commit" would open a pull
# request here every single night, because the registry moves for reasons that
# have nothing to do with the hook — a skill edit, a doc, an ADR. A fleet that
# learns to ignore these PRs is worse than no re-pinner (Test 8's own
# `repo-current` fixture exists for the same reason one lane over). So the
# question asked here is "did the hook's BYTES change", and a run where they
# did not writes nothing at all. A consequence worth stating because it looks
# like a bug: `ref` therefore lags the registry's HEAD by design, and names the
# commit at which the hook last CHANGED. That is the more useful pin anyway —
# it is the commit a reviewer wants when asking "what is this hook".
#
# Requirements: gh (GitHub CLI, authenticated), yq, git, python3
# Usage:        ./scripts/bump-hook-pin.sh [--dry-run]
#
# Environment:
#   REPOS_YML          — the repos.yml to read and rewrite
#                        (default: this checkout's)
#   BUMP_REGISTRY      — OWNER/REPO of the registry that owns the hook
#                        (default: repos.yml skills_bootstrap.registry)
#   BUMP_CHECKOUTS     — where each registry is checked out on THIS machine, as
#                        space- or newline-separated OWNER/REPO=PATH entries.
#                        The same variable bump-consumer-locks.sh consumes, and
#                        required for the same reason: a guessed path is how
#                        sync-skills once enumerated nothing (see AGENTS.md), so
#                        nothing here encodes a repo location as a constant.
#   HOOK_PIN_REPO      — OWNER/REPO carrying repos.yml, i.e. the repo this
#                        script opens its pull request on
#                        (default: GITHUB_REPOSITORY, else the origin remote)
#   HOOK_PIN_BRANCH    — branch the PR is opened from
#                        (default: hook-pin-bump/update). Deliberately NOT
#                        `skills-lock-bump/update`: this repo's own skills.lock
#                        is in the consumer bumper's scope, so that branch is
#                        already in use here and the two would clobber one
#                        another on alternate nights.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REPOS_YML="${REPOS_YML:-$REPO_ROOT/repos.yml}"
BRANCH_NAME="${HOOK_PIN_BRANCH:-hook-pin-bump/update}"
DRY_RUN=false
WORK_DIR=$(mktemp -d)

log()  { echo "  $*"; }
fail() { echo "  ERROR: $*"; }

trap 'rm -rf "$WORK_DIR"' EXIT

# Parsed as a closed set rather than sniffed, exactly as bump-consumer-locks.sh
# does and for the same reason: this run holds a token that can push to and
# open pull requests on this repo, so the flag meaning "write nothing" has to
# fail CLOSED. `--dry-runn`, `-n`, or the flag given second must stop the run,
# not silently leave DRY_RUN=false and go on to push. After the trap, so a
# usage exit still removes the work directory.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        *) fail "unrecognised argument: $1"; echo "Usage: bump-hook-pin.sh [--dry-run]" >&2; exit 2 ;;
    esac
done

echo "=== Hook pin bump ==="
$DRY_RUN && log "DRY RUN — deciding and reporting, writing and pushing nothing."

# ── yq preflight ───────────────────────────────────────────────────────────
#
# THE INCIDENT, and it is one missing guard wearing two costumes. The
# repos.yml reads that decide which repos a run touches — `exclude`,
# `default_sections`, `cron_coverage` and the `skills_bootstrap` block — were
# spelled `yq ... 2>/dev/null || true` (`|| echo ""` for the scalars) in
# sync.sh, drift-report.sh and bump-consumer-locks.sh, which collapses three
# different answers into one: the key is legitimately absent, yq could not
# parse the file, and yq is not installed at all. Only the first is normal;
# `|| true` turned the other two into an EMPTY LIST, exit 0, and not one line
# in the log. An empty exclusion list is not an inert one —
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

# read_repos_yml <yq expression> — the one way this script reads repos.yml.
#
# It exists to keep apart three answers the old `yq ... 2>/dev/null || true`
# spelling collapsed into one: a key that is legitimately absent (yq prints
# nothing and exits 0 — a normal empty result, which each caller's `[[ -n ]]`
# guard already handles), a repos.yml yq cannot parse, and a yq that fell
# over. Only a NON-ZERO EXIT is a failure, and a failure now stops the run,
# because what is read through here decides what a run does to other people's
# repositories — which repos the fleet walkers write to, and which hook every
# session in them then runs: a silently empty answer is not a smaller run, it
# is a run against the wrong set.
#
# Command substitution, never `< <(...)`. Process substitution discards the
# exit status of the command inside it, so simply dropping `|| true` from one
# of those reads would have changed the visible behaviour not at all — the
# same trap named beside the `gh repo list` capture in the scripts that walk
# the fleet. The `exit 2` below leaves only the substitution's subshell on its
# own; what stops the run is `set -e` seeing the assignment that captured it
# come back non-zero, so every caller must assign the result rather than pipe
# it — including through a `${VAR:-$(read_repos_yml ...)}` default, which was
# measured to abort the run rather than silently substitute an empty string.
#
# Duplicated in sync.sh, drift-report.sh, bump-consumer-locks.sh and
# bump-hook-pin.sh for the reason given above the yq preflight, and it must
# move with them. bump-hook-pin.sh was the last to get it, and was the one
# script the sentence above the preflight never described: its four
# skills_bootstrap reads were spelled BARE — no redirection, no `|| true` — so
# a yq that errors killed the run with yq's own one-line message and nothing
# naming repos.yml or the key it was reading. A different failure from the
# silent empty list, and still one the four disagreed about until this copy
# landed.
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

# ── Read the current pin ───────────────────────────────────────────────────
#
# Read with a real parser, never a line scan — the house rule, and repos.yml is
# a heavily commented file where a regex would be reading prose as often as
# data. yq is already this repo's YAML reader (sync.sh, bump-consumer-locks.sh)
# and the workflow installs it digest-verified, so there is no new dependency
# here.

if [[ ! -f "$REPOS_YML" ]]; then
    fail "no repos.yml at $REPOS_YML"
    exit 1
fi

PIN_REGISTRY="${BUMP_REGISTRY:-$(read_repos_yml '.skills_bootstrap.registry // ""')}"
PIN_PATH=$(read_repos_yml '.skills_bootstrap.path // ""')
PIN_REF=$(read_repos_yml '.skills_bootstrap.ref // ""')
PIN_SHA256=$(read_repos_yml '.skills_bootstrap.sha256 // ""')

# Every one of the four is load-bearing, and a missing one means repos.yml is
# not in the shape this script knows how to edit. Refuse rather than guess a
# default — a wrong guess here rewrites the file that decides what code runs at
# the start of every session in ten repos.
missing=""
[[ -n "$PIN_REGISTRY" ]] || missing="${missing:+$missing, }registry"
[[ -n "$PIN_PATH"     ]] || missing="${missing:+$missing, }path"
[[ -n "$PIN_REF"      ]] || missing="${missing:+$missing, }ref"
[[ -n "$PIN_SHA256"   ]] || missing="${missing:+$missing, }sha256"
if [[ -n "$missing" ]]; then
    fail "repos.yml's skills_bootstrap block is missing: $missing — nothing to re-pin."
    exit 1
fi

log "current pin: $PIN_REGISTRY@${PIN_REF:0:7} ($PIN_PATH), digest ${PIN_SHA256:0:12}…"

# ── Locate the registry checkout ───────────────────────────────────────────

REGISTRY_DIR=""
for entry in ${BUMP_CHECKOUTS:-}; do
    [[ "$entry" == *=* ]] || continue
    if [[ "${entry%%=*}" == "$PIN_REGISTRY" ]]; then
        REGISTRY_DIR="${entry#*=}"
    fi
done

if [[ -z "$REGISTRY_DIR" ]]; then
    fail "BUMP_CHECKOUTS names no path for $PIN_REGISTRY — cannot read the hook. Set BUMP_CHECKOUTS='$PIN_REGISTRY=/path/to/checkout'."
    exit 1
fi
if ! git -C "$REGISTRY_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    fail "$REGISTRY_DIR is not a git checkout — cannot read $PIN_PATH from it."
    exit 1
fi

# A shallow clone is refused up front rather than allowed to fail later as
# something else. It CAN serve the hook at HEAD, so the refusal is not about
# this run's happy path: it is that the consistency check below reads the
# CURRENT pin's blob, and in a shallow clone that read fails with "bad object"
# — which reads exactly like a pin naming a commit that does not exist, i.e.
# like the one alarming thing this script can discover. Stating the shallowness
# is cheaper than making someone chase that.
if [[ "$(git -C "$REGISTRY_DIR" rev-parse --is-shallow-repository 2>/dev/null)" == "true" ]]; then
    fail "$REGISTRY_DIR is a shallow checkout — re-check it out with fetch-depth: 0 so the currently pinned commit is readable."
    exit 1
fi

TARGET_REF=$(git -C "$REGISTRY_DIR" rev-parse HEAD)
log "registry HEAD: ${TARGET_REF:0:7}"

# ── Read and vet the hook at that commit ───────────────────────────────────

HOOK_FILE="$WORK_DIR/hook-at-target"
if ! git -C "$REGISTRY_DIR" show "$TARGET_REF:$PIN_PATH" > "$HOOK_FILE" 2>/dev/null; then
    fail "$PIN_PATH does not exist at $PIN_REGISTRY@${TARGET_REF:0:7} — refusing to pin a path the registry no longer has."
    exit 1
fi

# Non-empty, and syntactically a bash script. Both are cheap, and both are
# things a pin cannot express: repos.yml records WHERE the hook is and WHAT its
# bytes hash to, and is equally happy recording zero bytes or a file that dies
# on `bash -c` at line 1. Delivery would then hand every allowlisted repo a
# SessionStart hook that fails in every session — with a correct digest, so
# sync.sh's own integrity check passes it through. `bash -n` parses without
# executing, so nothing in the registry's hook runs here.
if [[ ! -s "$HOOK_FILE" ]]; then
    fail "$PIN_PATH is empty at ${TARGET_REF:0:7} — refusing to pin it."
    exit 1
fi
if ! bash -n "$HOOK_FILE" 2>"$WORK_DIR/syntax.err"; then
    fail "$PIN_PATH does not parse as bash at ${TARGET_REF:0:7} — refusing to fan a broken hook across the fleet: $(head -1 "$WORK_DIR/syntax.err")"
    exit 1
fi

TARGET_SHA256=$(sha256sum "$HOOK_FILE" | cut -d' ' -f1)
log "hook at HEAD: digest ${TARGET_SHA256:0:12}…"

# ── Is the CURRENT pin internally consistent? ──────────────────────────────
#
# Reported, never acted on. If repos.yml's recorded digest does not match the
# bytes at the ref it names, sync.sh is already refusing to deliver anything
# fleet-wide ("digest mismatch … Delivery disabled for this run") and has been
# since whenever that was introduced. This script's job is to propose the new
# pin either way — a bump is the FIX for that state, not something to be
# blocked by it — but a silent fix would hide that the fleet had delivery
# disabled, so it is said out loud.
CURRENT_HOOK="$WORK_DIR/hook-at-pin"
if git -C "$REGISTRY_DIR" show "$PIN_REF:$PIN_PATH" > "$CURRENT_HOOK" 2>/dev/null; then
    # Digested from the file git writes, never from a `$(...)` round-trip:
    # command substitution strips trailing newlines, so a shell capture of a
    # text file hashes to something the recorded digest can never equal.
    current_digest=$(sha256sum "$CURRENT_HOOK" | cut -d' ' -f1)
    if [[ "$current_digest" != "$PIN_SHA256" ]]; then
        log "WARN: repos.yml records ${PIN_SHA256:0:12}… for ${PIN_REF:0:7}, but that commit's $PIN_PATH hashes to ${current_digest:0:12}… — sync.sh is refusing to deliver the hook to ANY repo until this is corrected."
    fi
else
    log "WARN: the currently pinned commit ${PIN_REF:0:7} is not readable in this checkout — cannot confirm the recorded digest describes it."
fi

# ── Anti-churn ─────────────────────────────────────────────────────────────

if [[ "$TARGET_SHA256" == "$PIN_SHA256" ]]; then
    log "The hook is byte-identical to the pinned one — no re-pin needed."
    echo ""
    echo "=== Hook pin bump complete: unchanged ==="
    exit 0
fi

log "The hook has CHANGED since ${PIN_REF:0:7} — proposing ${TARGET_REF:0:7}."

# ── Resolve the repo to open the PR on ─────────────────────────────────────

if [[ -n "${HOOK_PIN_REPO:-}" ]]; then
    TARGET_REPO="$HOOK_PIN_REPO"
elif [[ -n "${GITHUB_REPOSITORY:-}" ]]; then
    TARGET_REPO="$GITHUB_REPOSITORY"
else
    TARGET_REPO=$(git -C "$REPO_ROOT" remote get-url origin \
        | sed -E 's#^.*github\.com[:/]##; s#\.git$##')
fi
if [[ -z "$TARGET_REPO" || "$TARGET_REPO" != */* ]]; then
    fail "could not resolve which repo carries repos.yml — set HOOK_PIN_REPO=OWNER/REPO."
    exit 1
fi
log "target repo: $TARGET_REPO"

# ── Is last night's proposal still open? ───────────────────────────────────
#
# Asked BEFORE anything is written, and it is not an optimisation — without it
# this script goes red every night that a pin PR waits for review, which on a
# change nobody merges promptly is every night until they do.
#
# The mechanism, because it is not obvious: the anti-churn test above compares
# the registry against the pin on the DEFAULT branch, and an open PR has not
# changed that, so a second run gets past it and rebuilds the same commit. Same
# parent, same tree, same message — but a later committer timestamp, so a
# different sha, so the push to the branch that already holds last night's
# commit is a non-fast-forward. It would then either fail the run or tempt
# someone into `--force`, which is a force-push over a branch a reviewer may
# have committed to. Neither is acceptable for a nightly job, and both are
# avoided by simply not proposing twice.
# Captured with its exit status, and read only on success. `gh` prints an HTTP
# error body to STDOUT and never runs the --jq filter, so the `2>/dev/null ||
# true` this replaces made "there is no open pull request" and "I could not
# ask" the same empty string — and the refusal further down then stated the
# first as a fact, beside a remedy (delete the branch) that CLOSES an open pull
# request on GitHub and discards whatever review was pending on it. An
# unanswerable question stops the run instead, exactly as
# bump-consumer-locks.sh's sweep already handles it.
#
# Streams captured SEPARATELY, and on this call that is not tidiness — a merged
# capture turns the whole script into a silent no-op. What comes back is DATA,
# not a diagnostic: `.[0].number // empty` prints NOTHING and exits 0 when no
# pull request is open, and the EMPTINESS of that answer is the entire
# decision below. gh writes to stderr while exiting 0 in ordinary conditions —
# deprecation notices, auth-expiry warnings — so `2>&1` makes a night with no
# open PR indistinguishable from a night with one. Measured against this
# script with a gh stubbed to exit 0 having printed no PR: with stderr silent
# it logs "DRY RUN — would re-pin example/registry 0000000 → 78848fd" and
# proposes the bump; with one "gh: warning: authentication token is nearing
# expiry" line on stderr and the same exit 0 it logs "PR #gh: warning:
# authentication token is nearing expiry already proposes a hook pin bump on
# hook-pin-bump/update — leaving it alone", prints "=== Hook pin bump complete:
# already proposed ===" and exits 0 having written nothing. Nothing anywhere
# goes red — scheduled-run-health sees a success — and the pin never advances,
# which is the precise failure this whole script exists to close.
#
# mktemp is checked because under `set -e` an unchecked assignment failure ends
# the run without saying why, and every other refusal on this path names its
# reason first.
if ! pr_err_file=$(mktemp); then
    fail "could not create a temp file to capture gh's diagnostics"
    exit 1
fi
pr_list_rc=0
pr_list_out=$(gh pr list --repo "$TARGET_REPO" --head "$BRANCH_NAME" --state open \
    --json number --jq '.[0].number // empty' 2>"$pr_err_file") || pr_list_rc=$?
# Read then removed UNCONDITIONALLY, before the branch, so the success path
# cannot leak the file either. The failure path deliberately quotes only this,
# never $pr_list_out: that variable holds the API's raw error body on an HTTP
# error, and the house rule forbids echoing a response body into a public log
# (AGENTS.md, "Sanitize error output").
pr_list_err=$(head -1 "$pr_err_file")
rm -f "$pr_err_file"
if [[ $pr_list_rc -ne 0 ]]; then
    fail "could not list open pull requests on $TARGET_REPO — ${pr_list_err:-no diagnostic output from gh}"
    exit 1
fi
existing_pr="$pr_list_out"
if [[ -n "$existing_pr" ]]; then
    log "PR #$existing_pr already proposes a hook pin bump on $BRANCH_NAME — leaving it alone."
    log "It will be re-evaluated against the registry once that PR is merged or closed."
    echo ""
    echo "=== Hook pin bump complete: already proposed ==="
    exit 0
fi

if $DRY_RUN; then
    log "DRY RUN — would re-pin $PIN_REGISTRY ${PIN_REF:0:7} → ${TARGET_REF:0:7} and open a PR on $TARGET_REPO."
    echo ""
    echo "=== Hook pin bump complete: 1 would be proposed ==="
    exit 0
fi

# ── Clone, rewrite, commit ─────────────────────────────────────────────────

CLONE_DIR="$WORK_DIR/target"
if ! gh repo clone "$TARGET_REPO" "$CLONE_DIR" -- --depth 1 >/dev/null 2>&1; then
    fail "could not clone $TARGET_REPO."
    exit 1
fi
if [[ -n "${GH_TOKEN:-}" ]]; then
    git -C "$CLONE_DIR" remote set-url origin \
        "https://x-access-token:${GH_TOKEN}@github.com/${TARGET_REPO}.git"
fi

CLONE_REPOS_YML="$CLONE_DIR/repos.yml"
if [[ ! -f "$CLONE_REPOS_YML" ]]; then
    fail "$TARGET_REPO has no repos.yml at its default branch."
    exit 1
fi

# THE WRITE, and why it is a two-line text edit rather than `yq -i`.
#
# repos.yml is ~90% comment by line count, and every one of those comments is
# load-bearing prose that an ADR points at. mikefarah yq re-serialises the
# document it parsed; comment handling is best-effort and formatting is not
# preserved, so an in-place yq write is a diff across the whole file in which
# the two bytes that matter are invisible. So: rewrite exactly the two scalar
# lines inside the `skills_bootstrap:` block, leave every other byte alone.
#
# A text edit then has to prove it did not do something else, so it does —
# this is register-bootstrap-hook.sh's semantic guard, in the same shape and
# for the same reason. After patching, BOTH documents are parsed and compared
# as data: the result must equal the original with exactly `skills_bootstrap.ref`
# and `skills_bootstrap.sha256` changed and nothing else. A successful write
# provably means "the old file plus those two values" — no key dropped, no list
# reordered, no anchor coerced, no neighbouring block touched. If the
# comparison fails, nothing is written and the run stops.
python3 - "$CLONE_REPOS_YML" "$TARGET_REF" "$TARGET_SHA256" <<'PY'
import json
import re
import subprocess
import sys

path, new_ref, new_sha = sys.argv[1], sys.argv[2], sys.argv[3]

with open(path, encoding="utf-8") as handle:
    original = handle.read()
lines = original.split("\n")

# Locate the block by its top-level key LINE, and end it at the next top-level
# key line. A comment column-0 line does not end the block (repos.yml separates
# blocks with comment banners), and neither does a blank one.
start = None
for i, line in enumerate(lines):
    if line == "skills_bootstrap:":
        start = i
        break
if start is None:
    sys.exit("repos.yml has no top-level `skills_bootstrap:` key")

end = len(lines)
for i in range(start + 1, len(lines)):
    line = lines[i]
    if line and not line[0].isspace() and not line.startswith("#"):
        end = i
        break

# Only a DIRECT child of the block: `ref:`/`sha256:` nested deeper (inside a
# future sub-mapping) must not be captured by accident. The block's child
# indent is taken from the first indented line rather than assumed to be two
# spaces.
child_indent = None
for i in range(start + 1, end):
    stripped = lines[i].lstrip()
    if stripped and not stripped.startswith("#"):
        child_indent = len(lines[i]) - len(stripped)
        break
if child_indent is None:
    sys.exit("repos.yml's skills_bootstrap block has no keys")

replaced = {}
for key, value in (("ref", new_ref), ("sha256", new_sha)):
    pattern = re.compile(
        r"^(?P<indent>[ ]{%d})(?P<key>%s):(?P<gap>[ \t]+)(?P<value>[^ \t#][^#]*?)"
        r"(?P<trail>[ \t]*(?:#.*)?)$" % (child_indent, key)
    )
    for i in range(start + 1, end):
        match = pattern.match(lines[i])
        if match:
            lines[i] = "{indent}{key}:{gap}{value}{trail}".format(
                indent=match.group("indent"), key=match.group("key"),
                gap=match.group("gap"), value=value, trail=match.group("trail"),
            )
            replaced[key] = True
            break

missing = [k for k in ("ref", "sha256") if k not in replaced]
if missing:
    sys.exit("repos.yml's skills_bootstrap block has no %s line to rewrite"
             % ", ".join(missing))

patched = "\n".join(lines)


def parse(text):
    """Parse YAML to JSON via the same yq the rest of this repo reads with.

    Deliberately not PyYAML: nothing in this repo depends on it, CI installs no
    Python packages, and a script that silently needs one is a script that
    works until the runner image changes.
    """
    result = subprocess.run(
        ["yq", "-o=json", "-I0", "."],
        input=text, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        sys.exit("could not parse repos.yml: %s" % result.stderr.strip())
    return result.stdout


before = json.loads(parse(original))
after = json.loads(parse(patched))

expected = json.loads(json.dumps(before))
expected["skills_bootstrap"]["ref"] = new_ref
expected["skills_bootstrap"]["sha256"] = new_sha

if after != expected:
    sys.exit(
        "refusing to write: the patched repos.yml is not the original with only "
        "skills_bootstrap.ref and skills_bootstrap.sha256 changed"
    )

with open(path, "w", encoding="utf-8") as handle:
    handle.write(patched)
PY

cd "$CLONE_DIR"
git config user.name "agents-md-sync[bot]"
git config user.email "agents-md-sync[bot]@users.noreply.github.com"
# Silenced but not ignored: `set -e` already aborts on a non-zero checkout, and
# without this it aborts with no line explaining which step died.
git checkout -b "$BRANCH_NAME" >/dev/null 2>&1 || {
    fail "could not create the branch $BRANCH_NAME in the clone."
    exit 1
}

# THE SELF-HOSTED COPY MOVES IN THE SAME COMMIT, and leaving it out is the way
# this bump lands red.
#
# This repo is dropped from both sync.sh's and drift-report.sh's per-repo loops
# ($SELF_REPO, ADR 0004 fact 5), so it can never RECEIVE the hook it publishes
# the pin for — it carries its own copy instead, and `test_self_hosted_hook_pin`
# asserts that copy hashes to exactly `skills_bootstrap.sha256`. Move the pin
# without the file and that test fails on the bump PR itself: CI here runs on
# every push and every pull request, so the bumper would open a red PR every
# time it had anything to say.
#
# Guarded on the file already existing rather than created unconditionally: a
# repo that does not self-host the hook should not acquire a copy because a pin
# bump passed through it.
SELF_HOSTED="$CLONE_DIR/$PIN_PATH"
declare -a CHANGED_PATHS=(repos.yml)
if [[ -f "$SELF_HOSTED" ]]; then
    # cat into the existing file rather than `cp` over it: the destination is
    # tracked at mode 100755 and a copy that clobbers the inode can carry the
    # source's mode instead, turning the bump into a spurious mode change — or,
    # worse, a hook the harness will not execute.
    cat "$HOOK_FILE" > "$SELF_HOSTED"
    CHANGED_PATHS+=("$PIN_PATH")
    log "self-hosted copy of $PIN_PATH refreshed to match the new pin."
fi

git add -- "${CHANGED_PATHS[@]}"

# Nothing but those paths, ever. A bump that carried anything else would be a
# fleet config change riding in on a pin bump, which is exactly the diff nobody
# reads closely.
if ! git diff --cached --quiet -- . ":!repos.yml" ":!$PIN_PATH"; then
    fail "something other than repos.yml and $PIN_PATH is staged — refusing to commit."
    exit 1
fi
if git diff --cached --quiet; then
    log "the default branch already carries this pin — nothing to commit."
    echo ""
    echo "=== Hook pin bump complete: unchanged ==="
    exit 0
fi

PR_TITLE="Re-pin the skills-bootstrap hook onto ${TARGET_REF:0:7}"
PR_BODY="The \`skills-bootstrap\` hook changed in \`$PIN_REGISTRY\`, so \
\`repos.yml\`'s \`skills_bootstrap\` pin no longer names the current one.

| | before | after |
|---|---|---|
| \`ref\` | \`$PIN_REF\` | \`$TARGET_REF\` |
| \`sha256\` | \`$PIN_SHA256\` | \`$TARGET_SHA256\` |

**What merging this does.** \`repos.yml\` is in \`sync.yml\`'s \`paths:\` filter, so \
merging fans the hook at \`${TARGET_REF:0:7}\` into every repo named in \
\`skills_bootstrap.repos\` on the next sync run, overwriting whatever copy each \
one carries. The hook runs at the start of every ephemeral session in those \
repos, which is why this bumper opens a pull request and does not merge it \
itself — see \`docs/decisions/0010-the-hook-pin-is-re-pinned-from-here.md\`.

This repo self-hosts the hook — it is dropped from the sync's own per-repo loop, \
so it can never receive the copy it publishes the pin for — so \
\`$PIN_PATH\` moves in this same commit. Its digest is what \
\`test_self_hosted_hook_pin\` checks against the \`sha256\` above.

**What was checked before proposing it.** The path exists at \
\`${TARGET_REF:0:7}\`, the file is non-empty, and it parses under \`bash -n\`. \
The digest above is computed from the bytes at that commit, not carried over. \
The rewrite touched only \`skills_bootstrap.ref\` and \`skills_bootstrap.sha256\`: \
the patched file was re-parsed and required to equal the original with exactly \
those two values changed, or nothing would have been written.

Opened by \`scripts/bump-hook-pin.sh\` from \`.github/workflows/skills-lock-bump.yml\`."

git commit -m "$PR_TITLE

The nightly lock bumper re-pins every consumer's skills.lock, but the hook that
READS that lock moved only when someone hand-edited repos.yml. This is that
bump, proposed automatically: the hook's bytes changed at $PIN_REGISTRY, so the
recorded ref and digest no longer describe the current one." >/dev/null

# ── Does the branch already carry exactly THIS pin? ────────────────────────
#
# Asked before pushing, and it is the whole difference between a branch that
# outlived its pull request being adopted and being stranded forever. The
# sequence that produced it: one night this script pushes $BRANCH_NAME and then
# `gh pr create` dies — a transient 5xx, a rate limit, an App installation
# holding Contents:write but not Pull requests:write — so the branch exists and
# the pull request does not. The default branch still carries the old pin, so
# every later run gets past anti-churn, finds no OPEN pull request, rebuilds
# the same commit on the same parent with only a later committer timestamp, and
# pushes a non-fast-forward onto its own already-correct branch. The identical
# dead end is reached with no failure at all if somebody simply CLOSES the bump
# PR without deleting its branch. Nothing else reaps this branch — sync.sh's
# stale-branch cleanup is scoped to `agents-md-sync/update` — so the hook pin
# would never be proposed again, while all ten allowlisted repos kept
# delivering the pre-change hook.
#
# So: read the pin the remote branch actually carries and compare it with the
# one this run computed. Equal means that branch already IS the proposal and
# the only thing missing is the pull request, which the step below opens. This
# is bump-consumer-locks.sh's `branch_matches` fall-through, in the same shape
# and for the reason written out beside it there.
#
# Every read sits inside the `if` condition so an unreadable answer — a fetch
# that fails, a branch with no repos.yml, a repos.yml yq cannot parse — leaves
# `branch_matches` false instead of aborting the run under `set -e`. Collapsing
# "cannot tell" into "does not match" is safe in this one direction only: it
# lands on the refusal below, which writes nothing.
branch_matches=false
if git ls-remote --exit-code --heads origin "$BRANCH_NAME" >/dev/null 2>&1; then
    REMOTE_REPOS_YML="$WORK_DIR/repos-on-branch.yml"
    if git fetch --depth 1 origin "$BRANCH_NAME" >/dev/null 2>&1 \
       && git show "FETCH_HEAD:repos.yml" > "$REMOTE_REPOS_YML" 2>/dev/null \
       && remote_ref=$(yq -r '.skills_bootstrap.ref // ""' "$REMOTE_REPOS_YML" 2>/dev/null) \
       && remote_sha=$(yq -r '.skills_bootstrap.sha256 // ""' "$REMOTE_REPOS_YML" 2>/dev/null) \
       && [[ "$remote_ref" == "$TARGET_REF" && "$remote_sha" == "$TARGET_SHA256" ]]; then
        branch_matches=true
    fi
fi

if $branch_matches; then
    # The push is skipped rather than attempted. Our commit and the remote one
    # differ only in committer timestamp, so pushing would be refused as a
    # non-fast-forward and land in the arm below — which is precisely the
    # stranding this check exists to undo.
    log "$BRANCH_NAME already carries exactly this pin but has no open pull request — adopting that branch rather than pushing again."
else
    push_ok=true
    push_out=$(git push origin "HEAD:refs/heads/$BRANCH_NAME" 2>&1) || push_ok=false
    if ! $push_ok; then
        # Matched on git's own non-fast-forward wording rather than the bare word
        # "rejected", which the server also prints for a ruleset refusal (GH013) or
        # a pre-receive hook — calling one of those a stale branch prints a remedy
        # for a branch that does not exist. Never force-pushed: a bump branch
        # someone has committed to is a branch with a reviewer's work on it.
        if grep -qiE 'non-fast-forward|fetch first|updates were rejected because' <<< "$push_out"; then
            # Reached only when the branch exists, carries a pin that is NOT the
            # one this run computed, and has no open pull request: the check
            # above adopted the matching case, and the open-PR question was
            # asked — and answered, not merely attempted — long before this.
            # So what is parked on the name is a closed-but-undeleted proposal
            # for some other pin, or somebody's hand-pushed work.
            #
            # IT EXITS NON-ZERO, and that reverses what this arm used to do.
            # Exiting 0 was justified as not crying wolf for a nightly job, but
            # a wolf that never arrives is not this state: the default branch
            # still holds the old pin, so tomorrow's run repeats every step and
            # lands right back here, and the night after that, for as long as
            # the branch exists — green every time, so scheduled-run-health
            # never fires either, while the fleet keeps delivering the
            # pre-change hook to every ephemeral session. Permanent AND
            # invisible is the one combination this script may not produce; that
            # silence is the failure ADR 0010 exists to end.
            fail "$BRANCH_NAME already exists and does not fast-forward — refusing to force-push over it. Free the name (delete that branch, or merge what is on it) and the next run re-proposes."
            echo "::error::$TARGET_REPO: $BRANCH_NAME is occupied by a different pin and has no open pull request — the skills-bootstrap hook re-pin will not be proposed again until that branch is freed." >&2
            exit 1
        fi
        fail "push failed — $(head -1 <<< "$push_out")"
        exit 1
    fi
fi

if gh pr create --repo "$TARGET_REPO" --head "$BRANCH_NAME" \
       --title "$PR_TITLE" --body "$PR_BODY" >/dev/null; then
    log "PR created."
else
    fail "PR creation failed for $TARGET_REPO."
    exit 1
fi

echo ""
echo "=== Hook pin bump complete: 1 proposed ==="
