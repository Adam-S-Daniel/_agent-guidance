#!/usr/bin/env bash
set -euo pipefail
#
# bump-consumer-locks.sh — re-pin every consumer's skills.lock onto the
# registry's current commit, one pull request per repo.
#
# Discovers repos dynamically via `gh repo list`, exactly as sync.sh does. For
# each repo the script:
#   1. Fetches skills.lock from the default branch (absent → nothing to do)
#   2. Reads which registries that lock names — the primary and every
#      federated source — and skips a lock that never names the registry
#      this run is bumping
#   3. Asks the GENERATOR whether a re-pin is needed at all
#      (`--check-current`: does the bundle content at the PRIMARY's pinned
#      ref still match that registry's tree?). Unchanged → no PR, no push
#   4. Otherwise re-pins with `--repin` inside a clone of the consumer, and
#      opens (or leaves alone) one PR carrying that single file
#
# WHY THIS LIVES HERE AND NOT IN THE REGISTRY. ADR 0001 said re-pinning
# "belongs in agentskills (it owns the generator and the digests)". Half of it
# does, and landed there as `generate_skills_lock.py --repin` (agentskills
# #103), which this script calls, does not reimplement, and probes for up
# front rather than assuming. The FLEET half cannot: agentskills' workflows
# hold only `secrets.GITHUB_TOKEN`, scoped to agentskills, so a bumper there
# could not push a branch to a consumer or open a PR on one. This repo already
# holds the agents-md-sync App credentials that mint a per-owner token across
# both owners. See docs/decisions/0005.
#
# WHY IT IS A SEPARATE SCRIPT FROM sync.sh. ADR 0001 made "_agent-guidance has
# no code path that writes skills.lock" a structural property, and sync.sh
# enforces it by refusing to commit at all if the lock is ever staged. That
# property is not weakened by this file: the lock a consumer gets here is
# `--repin`'s output, which INHERITS the consumer's own registry, bundles and
# whole `sources` array and re-resolves only `ref`. What ADR 0001 recorded was
# that "any canonical lock pushed fleet-wide would flatten the federated one";
# a writer that cannot express the declaration cannot flatten it. sync.sh's
# guard stays exactly as it is — do not add a lock writer to it.
#
# Requirements: gh (GitHub CLI, authenticated), yq, git, python3
# Usage:        ./scripts/bump-consumer-locks.sh [--dry-run]
#
# Environment:
#   SYNC_OWNERS             — space-separated owners to scan; takes precedence
#                             over GITHUB_REPOSITORY_OWNER and the git-remote
#                             fallback (e.g. "Adam-S-Daniel jodidaniel")
#   GITHUB_REPOSITORY_OWNER — org/user to scan (auto-set in GitHub Actions)
#   BUMP_REGISTRY           — OWNER/REPO of the registry being bumped
#                             (default: repos.yml skills_bootstrap.registry)
#   BUMP_CHECKOUTS          — where each registry a lock may name is checked
#                             out on THIS machine, as space- or newline-
#                             separated OWNER/REPO=PATH entries. Required: a
#                             guessed path is how sync-skills once enumerated
#                             nothing (see AGENTS.md), so nothing here encodes
#                             a repo location as a constant.
#   BUMP_GENERATOR          — path to generate_skills_lock.py (default: the
#                             copy inside the BUMP_REGISTRY checkout, which is
#                             the repo that owns it)
#   BUMP_BRANCH             — branch the PR is opened from
#                             (default: skills-lock-bump/update)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCK_REL_PATH="skills.lock"
BRANCH_NAME="${BUMP_BRANCH:-skills-lock-bump/update}"
DRY_RUN=false
WORK_DIR=$(mktemp -d)

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

# Parsed as a closed set rather than sniffed. `[[ $1 == --dry-run ]]` alone
# fails OPEN: `--dry-runn`, `-n`, or `--dry-run` given anywhere but first all
# left DRY_RUN=false and went on to clone, commit, push and open pull
# requests. This run holds installation tokens with write scope across two
# owners, so the flag that means "write nothing" has to fail CLOSED — an
# argument this script does not recognise stops it. After the trap, so a
# usage exit still removes the work directory.
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

# ── Helpers ────────────────────────────────────────────────────────────────

log()  { echo "  $*"; }
fail() { echo "  ERROR: $*"; }

FAIL_COUNT=0
OK_COUNT=0
SKIP_COUNT=0

# lock_plan <file> — the registries a lock names, one per line:
#
#   registry <primary>
#   ref <the commit it currently pins>
#   source <a federated source's registry>   (zero or more)
#
# python3, never a regex: the lock is JSON, this repo forbids hand-rolled
# parsers for structured formats, and a regex that reads `"registry"` out of a
# federated lock cannot tell the primary from a source — which is the one
# distinction every decision below turns on.
lock_plan() {
    python3 -c '
import json, sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        lock = json.load(handle)
except Exception as exc:
    sys.exit("not readable JSON (%s)" % exc.__class__.__name__)

if not isinstance(lock, dict):
    sys.exit("top level is not an object")

registry = lock.get("registry")
if not isinstance(registry, str) or not registry.strip():
    sys.exit("registry is missing or empty")
ref = lock.get("ref")
if not isinstance(ref, str) or not ref.strip():
    sys.exit("ref is missing or empty")
print("registry %s" % registry)
print("ref %s" % ref)

sources = lock.get("sources")
if sources is None:
    sources = []
# A malformed `sources` is refused rather than read as "no sources". The
# generator refuses it on the same grounds — treating it as absent would let
# this script report a federated lock as ordinary and re-pin it as one.
if not isinstance(sources, list):
    sys.exit("sources is present but is not a list")
for index, source in enumerate(sources):
    if not isinstance(source, dict):
        sys.exit("sources[%d] is not an object" % index)
    source_registry = source.get("registry")
    if not isinstance(source_registry, str) or not source_registry.strip():
        sys.exit("sources[%d].registry is missing or empty" % index)
    print("source %s" % source_registry)
' "$1"
}

# lock_summary <file> <registry> — "registry@shortref" for one entry of a lock,
# used to describe in the PR body what moved and what did not.
lock_summary() {
    python3 -c '
import json, sys

with open(sys.argv[1], encoding="utf-8") as handle:
    lock = json.load(handle)
wanted = sys.argv[2]
entries = [lock] + [s for s in (lock.get("sources") or []) if isinstance(s, dict)]
for entry in entries:
    if entry.get("registry") == wanted:
        print("%s@%s" % (entry.get("registry"), str(entry.get("ref", "?"))[:7]))
        break
' "$1" "$2"
}

# primary_only <lock> <destination> — the same lock with its federated
# `sources` array removed, i.e. a lock that asks only about its primary.
# Used to scope the currency question; see the gate in the per-repo loop for
# why the combined question is the wrong one to decide on.
primary_only() {
    python3 -c '
import json, sys

with open(sys.argv[1], encoding="utf-8") as handle:
    lock = json.load(handle)
lock.pop("sources", None)
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    handle.write(json.dumps(lock, indent=2, ensure_ascii=False) + "\n")
' "$1" "$2"
}

# skills_shrink_reason <lock before> <lock after> — prints why a re-pin must
# not be proposed, or nothing at all.
#
# Two shapes are refused: a re-pinned lock that declares no skills, and one
# that lost every skill of a bundle it still declares. Both mean a bundle
# directory stopped existing at the registry's new HEAD — a rename, a deleted
# plugin, a layout change — which is a registry-side decision, not a lock
# chore, and it hits every consumer in the same run.
skills_shrink_reason() {
    python3 -c '
import json, sys


def skills(path):
    with open(path, encoding="utf-8") as handle:
        found = json.load(handle).get("skills")
    return found if isinstance(found, dict) else {}


def per_bundle(mapping):
    counts = {}
    for key in mapping:
        bundle = key.split("/", 1)[0]
        counts[bundle] = counts.get(bundle, 0) + 1
    return counts


before, after = skills(sys.argv[1]), skills(sys.argv[2])
if not after:
    print("the re-pinned lock declares no skills at all (it had %d)" % len(before))
    sys.exit(0)
counts_after = per_bundle(after)
gone = sorted(b for b, n in per_bundle(before).items() if n and not counts_after.get(b))
if gone:
    print("bundle(s) %s lost every skill they had" % ", ".join(gone))
' "$1" "$2"
}

# ── Load central repos.yml (exclusions + the registry being bumped) ────────

EXCLUDED_REPOS=()
if [[ -f "$REPOS_YML" ]]; then
    while IFS= read -r r; do
        [[ -n "$r" ]] && EXCLUDED_REPOS+=("$r")
    done < <(yq -r '.exclude // [] | .[]' "$REPOS_YML" 2>/dev/null || true)
fi

# The registry this run bumps. Defaulted from the same key sync.sh reads for
# the pinned hook, so the fleet has ONE answer to "which registry is ours"
# rather than a second copy that can disagree with it.
BUMP_REGISTRY="${BUMP_REGISTRY:-}"
if [[ -z "$BUMP_REGISTRY" && -f "$REPOS_YML" ]]; then
    BUMP_REGISTRY=$(yq -r '.skills_bootstrap.registry // ""' "$REPOS_YML" 2>/dev/null || echo "")
fi
if [[ -z "$BUMP_REGISTRY" ]]; then
    echo "ERROR: no registry to bump — set BUMP_REGISTRY, or give repos.yml a skills_bootstrap.registry." >&2
    exit 2
fi

# ── Locate each registry's checkout ────────────────────────────────────────
#
# Digests are read from git, so every registry a lock names needs a local
# clone: the primary for `--repo`, each federated source for `--source-repo`.
# Missing one is not a reason to re-pin half a lock (see the per-repo loop) —
# it is a reason to leave that consumer alone this run.

declare -A CHECKOUT_DIR=()
for entry in ${BUMP_CHECKOUTS:-}; do
    [[ "$entry" == *=* ]] || {
        echo "ERROR: BUMP_CHECKOUTS entry '$entry' is not OWNER/REPO=PATH." >&2
        exit 2
    }
    checkout_path="${entry#*=}"
    # Absolutised here, once. The per-repo loop cds into a clone of the
    # consumer before it passes this to --repo, so a relative path would
    # resolve against that clone and the generator would report a checkout
    # that is "not that registry" — a wrong-repo hunt with a working-directory
    # cause.
    [[ "$checkout_path" == /* ]] || checkout_path="$PWD/$checkout_path"
    CHECKOUT_DIR["${entry%%=*}"]="$checkout_path"
done

# checkout_problem <registry> — empty when that registry has a usable
# full-depth checkout, otherwise the reason it does not. Re-probed per call
# rather than cached: every caller reads it through a command substitution, so
# a cache would live in that subshell and die with it, and two cheap local git
# calls are not worth a helper that lies about being memoized.
checkout_problem() {
    local registry="$1" dir problem=""
    dir="${CHECKOUT_DIR[$registry]:-}"
    if [[ -z "$dir" ]]; then
        problem="no checkout configured for $registry (add it to BUMP_CHECKOUTS as '$registry=<path>')"
    elif [[ ! -d "$dir/.git" ]]; then
        problem="no git checkout of $registry at $dir"
    elif [[ "$(git -C "$dir" rev-parse --is-shallow-repository 2>/dev/null || echo unknown)" != "false" ]]; then
        # Said plainly because the symptom otherwise reads as drift: --repin
        # probes that --repo contains the commit the lock ALREADY pins, and a
        # shallow clone does not contain it — so every consumer would fail
        # with "this checkout is not that registry" and a reader would go
        # looking for a wrong registry rather than a missing `fetch-depth: 0`.
        problem="$dir is a SHALLOW clone of $registry — re-pinning needs full history (git fetch --unshallow, or fetch-depth: 0 in CI), because the pin already in a lock is what proves this clone is that registry"
    fi

    printf '%s' "$problem"
}

# The registry being bumped has to be usable before anything else runs: with
# no clone of it there is nothing to bump against, and a run that skipped
# every repo for that reason would print the same "nothing to do" a genuinely
# current fleet prints.
registry_problem=$(checkout_problem "$BUMP_REGISTRY")
if [[ -n "$registry_problem" ]]; then
    echo "ERROR: $registry_problem" >&2
    exit 2
fi

GENERATOR="${BUMP_GENERATOR:-${CHECKOUT_DIR[$BUMP_REGISTRY]}/scripts/generate_skills_lock.py}"
if [[ ! -f "$GENERATOR" ]]; then
    echo "ERROR: no lock generator at $GENERATOR — set BUMP_GENERATOR." >&2
    exit 2
fi

# --repin is the one thing this script deliberately cannot reimplement, and by
# default the generator comes from a checkout of the registry's DEFAULT
# BRANCH — so a run can meet a generator that predates the flag (a rollback, a
# checkout pinned elsewhere, an ordering where the bumper landed first).
# Probed once here because the shortfall otherwise surfaces only when some
# consumer is genuinely stale, as one argparse exit 2 per stale repo and a red
# scheduled run every night: a version skew that reads as a fleet breakage.
if ! generator_help=$(python3 "$GENERATOR" --help 2>&1) \
   || ! grep -q -- '--repin' <<< "$generator_help"; then
    echo "ERROR: $GENERATOR does not support --repin — this script advances a lock with that flag and will not hand-roll one. Point BUMP_GENERATOR at a generator that has it, or update the registry checkout." >&2
    exit 2
fi

echo "Bumping consumer locks onto $BUMP_REGISTRY"
echo "  generator: $GENERATOR"
echo "  registry checkout: ${CHECKOUT_DIR[$BUMP_REGISTRY]}"
$DRY_RUN && echo "  DRY RUN — nothing will be written or pushed"
echo ""

# Where this run came from, named in every PR body it opens. Read from
# GITHUB_REPOSITORY rather than composed from the owner being scanned: the
# bumper lives in exactly one repo, and interpolating $ORG (which sync.sh does
# for its own PR body) sends a jodidaniel consumer's reviewer to
# github.com/jodidaniel/_agent-guidance, which does not exist.
if [[ -n "${GITHUB_REPOSITORY:-}" ]]; then
    BUMPER_SOURCE="[\`${GITHUB_REPOSITORY}\`](${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY})"
else
    BUMPER_SOURCE="\`_agent-guidance\`"
fi

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

# ── Discover repos ─────────────────────────────────────────────────────────

echo "Scanning repos for: $ORG"
echo ""

# Captured through a command substitution, never process substitution: <(...)
# swallows the error and the script reports success while doing nothing. The
# failure branch is explicit because a bare `set -e` death here is the wrong
# shape too — with SYNC_OWNERS ordered "Adam-S-Daniel jodidaniel", one owner
# missing its App installation ends the run before the OTHER owner is scanned
# at all, printing a raw gh error and no summary, while this workflow's own
# comment and warning promise that owner's repos are merely "skipped this
# run". Counted per owner instead, so the scheduled run still goes red and
# names which owner could not be read.
if ! repo_list_raw=$(
    gh repo list "$ORG" \
        --no-archived \
        --source \
        --json nameWithOwner \
        --limit 1000 \
        --jq '.[].nameWithOwner' 2>&1
); then
    fail "$ORG: could not list repos — $(head -1 <<< "$repo_list_raw")"
    ((FAIL_COUNT++)) || true
    continue
fi

# NOTE FOR ANYONE COMPARING THIS WITH sync.sh / drift-report.sh: those two
# drop $SELF_REPO here (`grep -v "/${SELF_REPO}$"`) and this one deliberately
# does NOT. They drop it because delivering guidance to itself and reporting
# drift against itself are both meaningless. Re-pinning is neither: this repo
# SELF-HOSTS the bootstrap hook and carries a skills.lock of its own (ADR
# 0004), which goes stale exactly like a consumer's and has no other mechanism
# watching it. Its PR arrives through the same review path as everyone else's.
mapfile -t REPOS < <(echo "$repo_list_raw" | sed '/^$/d' | sort)   # drop the blank line an empty owner produces

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
    echo "No repos found in $ORG — nothing to bump."
    continue
fi

echo "Found ${#REPOS[@]} repo(s):"
printf '  %s\n' "${REPOS[@]}"
echo ""

# ── Main loop ──────────────────────────────────────────────────────────────

for repo_name in "${REPOS[@]}"; do
    echo "=== $repo_name ==="

    # The registry itself is never bumped from here. It owns the order of
    # operations — content commit, then re-pin, then lock commit — and its own
    # CI already asserts it with --check-current, so a bot PR racing that
    # would re-pin a lock its author is mid-way through re-pinning by hand.
    # Same carve-out shape drift-report.sh uses to exempt the registry from
    # `unmanaged`.
    if [[ "$repo_name" == "$BUMP_REGISTRY" ]]; then
        log "the registry itself — it owns its own re-pin; skipping."
        ((SKIP_COUNT++)) || true
        continue
    fi

    # ── Fetch the lock ─────────────────────────────────────────────────
    # On HTTP errors (404 when the file is absent) gh api prints the raw error
    # JSON body to stdout — the --jq filter is not applied — so `|| true` alone
    # would leave garbage here and base64-decode it into a "lock". Discard
    # output on failure explicitly.
    if ! encoded=$(gh api "repos/$repo_name/contents/$LOCK_REL_PATH" \
        --jq '.content' 2>/dev/null); then
        encoded=""
    fi

    if [[ -z "$encoded" ]]; then
        # Not a fault, and by far the commonest outcome: most repos in the
        # fleet declare no bundles at all. Logged rather than silent so a run
        # says what it considered.
        log "no $LOCK_REL_PATH — nothing to re-pin."
        ((SKIP_COUNT++)) || true
        continue
    fi

    lock_file="$WORK_DIR/$(echo "$repo_name" | tr '/' '_').lock"
    if ! echo "$encoded" | base64 -d > "$lock_file" 2>/dev/null; then
        fail "$repo_name: could not decode $LOCK_REL_PATH."
        ((FAIL_COUNT++)) || true
        continue
    fi

    # ── Which registries does it name? ─────────────────────────────────

    if ! plan=$(lock_plan "$lock_file" 2>&1); then
        fail "$repo_name: $LOCK_REL_PATH is unusable — $plan"
        ((FAIL_COUNT++)) || true
        continue
    fi

    primary_registry=$(echo "$plan" | sed -n 's/^registry //p')
    old_ref=$(echo "$plan" | sed -n 's/^ref //p')
    mapfile -t source_registries < <(echo "$plan" | sed -n 's/^source //p')

    # Naming the registry is not enough; it has to be the PRIMARY. Everything
    # downstream targets the primary — `primary_dir` is its checkout, --repin
    # advances its `ref`, and `new_ref` is read back from the lock's top-level
    # `ref` — so a lock that federates this registry under some OTHER primary
    # would have that other registry's pin advanced (the advance ADR 0005
    # reserves for a human), while the drift that started it went untouched,
    # under a title pairing this registry's name with a sha it does not
    # contain. No lock has that shape today; nothing else rejects it either.
    if [[ "$primary_registry" != "$BUMP_REGISTRY" ]]; then
        federates_registry=false
        for s in ${source_registries[@]+"${source_registries[@]}"}; do
            [[ "$s" == "$BUMP_REGISTRY" ]] && federates_registry=true
        done
        if $federates_registry; then
            log "$LOCK_REL_PATH federates $BUMP_REGISTRY but pins $primary_registry as its primary — --repin advances only the primary ref, so that federated pin is a human's to advance; skipping."
        else
            log "$LOCK_REL_PATH pins $primary_registry and never names $BUMP_REGISTRY — skipping."
        fi
        ((SKIP_COUNT++)) || true
        continue
    fi

    # ── Locate every checkout this lock needs ──────────────────────────
    # --repin advances ONLY the primary ref, but it re-derives EVERY digest,
    # including each federated source's at that source's own unchanged pin. A
    # missing checkout therefore means the lock cannot be rebuilt at all, and
    # skipping is the only safe outcome: half-re-pinning a federated lock is
    # the exact damage ADR 0001 named. (The currency question below is scoped
    # to the primary and would not need them; the rebuild does.)

    missing_checkout=""
    for reg in "$primary_registry" ${source_registries[@]+"${source_registries[@]}"}; do
        problem=$(checkout_problem "$reg")
        if [[ -n "$problem" ]]; then
            missing_checkout="$problem"
            break
        fi
    done
    if [[ -n "$missing_checkout" ]]; then
        log "WARN: $missing_checkout — skipping $repo_name rather than re-pinning part of its lock."
        ((SKIP_COUNT++)) || true
        continue
    fi

    primary_dir="${CHECKOUT_DIR[$primary_registry]}"
    source_repo_args=()
    for reg in ${source_registries[@]+"${source_registries[@]}"}; do
        source_repo_args+=(--source-repo "$reg=${CHECKOUT_DIR[$reg]}")
    done

    # ── Is a re-pin NEEDED? ────────────────────────────────────────────
    # The generator's own primitive, not a home-grown one: --check-current
    # compares the bundle content at the ref a lock pins against the
    # registry's working tree. Exit 0 means the ref may be old while the
    # BUNDLE is unchanged — the lock is doing its job and gets no PR. That
    # asymmetry is the whole anti-churn design; see docs/decisions/0005.
    #
    # THE QUESTION IS SCOPED TO THE PRIMARY, and that is the design rather
    # than an optimisation. --check-current reads EVERY source a lock names
    # and emits one FAILED: for the whole run if any of them differs, while
    # --repin advances ONLY the primary `ref` — nothing in this system ever
    # advances a federated pin. Deciding on the combined verdict therefore
    # keys the gate on a fact the re-pin cannot change: the moment a federated
    # checkout sits ahead of its pin that FAILED: is permanent, and every
    # commit to the primary registry — docs, a workflow, a Dependabot bump,
    # the registry's own lock commit — yields a pull request whose entire diff
    # is `ref` + `generated_from`, with not one digest changed. Handing the
    # generator a copy of the lock with `sources` removed asks a different
    # QUESTION rather than reinterpreting the combined answer, so it cannot
    # drift with the generator's wording.
    primary_lock="${lock_file%.lock}.primary.lock"
    if ! primary_only "$lock_file" "$primary_lock" 2>/dev/null; then
        fail "$repo_name: could not scope $LOCK_REL_PATH to its primary registry."
        ((FAIL_COUNT++)) || true
        continue
    fi

    current_exit=0
    check_out=$(python3 "$GENERATOR" --check-current \
        --repo "$primary_dir" \
        -o "$primary_lock" 2>&1) || current_exit=$?

    if [[ $current_exit -eq 0 ]]; then
        # The primary is current. A federated half that has moved decides
        # nothing here — this script cannot advance it — but it is worth
        # saying out loud, because that re-pin is a human's and otherwise has
        # no signal at all (ADR 0005, "Left open").
        if [[ ${#source_registries[@]} -gt 0 ]]; then
            fed_exit=0
            fed_out=$(python3 "$GENERATOR" --check-current \
                --repo "$primary_dir" \
                ${source_repo_args[@]+"${source_repo_args[@]}"} \
                -o "$lock_file" 2>&1) || fed_exit=$?
            if [[ $fed_exit -ne 0 ]]; then
                if grep -q '^FAILED:' <<< "$fed_out"; then
                    log "WARN: a FEDERATED source has moved on since the ref this lock pins for it — advancing that pin is a human's job, so nothing is re-pinned here."
                else
                    fail "$repo_name: could not read this lock's federated half — $(head -1 <<< "$fed_out")"
                    ((FAIL_COUNT++)) || true
                    continue
                fi
            fi
        fi
        log "bundle content unchanged since ${old_ref:0:7} — no re-pin needed."
        ((SKIP_COUNT++)) || true
        continue
    fi

    # A non-zero exit is NOT automatically drift. The generator reports a
    # broken lock, an unreachable pinned commit or a mis-pointed --repo with
    # the same exit code and an ERROR line; re-pinning on the strength of one
    # of those writes a lock nobody asked for. Only its own FAILED verdict
    # means "the bundle moved".
    if ! grep -q '^FAILED:' <<< "$check_out"; then
        fail "$repo_name: could not decide whether $LOCK_REL_PATH is current — $(head -1 <<< "$check_out")"
        ((FAIL_COUNT++)) || true
        continue
    fi

    log "bundle has moved since ${old_ref:0:7} — re-pin needed."

    if $DRY_RUN; then
        log "[DRY RUN] Would re-pin $LOCK_REL_PATH onto $primary_registry's current commit and open a PR on $BRANCH_NAME"
        ((SKIP_COUNT++)) || true
        continue
    fi

    # ── Clone the consumer and re-pin ──────────────────────────────────

    repo_dir="$WORK_DIR/$(echo "$repo_name" | tr '/' '_')"
    if ! gh repo clone "$repo_name" "$repo_dir" -- --depth 1; then
        fail "clone failed for $repo_name"
        ((FAIL_COUNT++)) || true
        continue
    fi
    cd "$repo_dir"

    # Configure git identity for commits (not inherited in fresh clones), and
    # commit under the App's noreply address so no real email is baked into
    # commit metadata on a public repo.
    git config user.name "agents-md-sync[bot]"
    git config user.email "agents-md-sync[bot]@users.noreply.github.com"

    # Embed token in remote URL so git push can authenticate in CI (no TTY).
    if [[ -n "${GH_TOKEN:-}" ]]; then
        git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/${repo_name}.git"
    fi

    if ! repin_out=$(python3 "$GENERATOR" --repin \
        --repo "$primary_dir" \
        ${source_repo_args[@]+"${source_repo_args[@]}"} \
        -o "$LOCK_REL_PATH" 2>&1); then
        fail "$repo_name: --repin failed — $(head -1 <<< "$repin_out")"
        ((FAIL_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi

    # A re-pin that empties a bundle is a registry-side decision, not a lock
    # chore. Rename or delete a bundle directory and `collect_skills` finds
    # nothing at the new HEAD, so --repin writes `"skills": {}` — and every
    # consumer gets that proposed in the same run, under a PR body that still
    # says "every digest here is re-derived from the newly pinned commit",
    # vacuously true because there are none. Nor is it a benign no-op
    # downstream: skills-bootstrap writes its claims stream from the routing
    # map precisely so a bundle a lock still declares but has emptied REAPS
    # its old skills, so merging one deletes the installed skills in every
    # ephemeral session in that repo. Refused per repo and counted, so the
    # scheduled run goes red and a human decides before it fans out.
    if ! shrink=$(skills_shrink_reason "$lock_file" "$LOCK_REL_PATH" 2>&1); then
        fail "$repo_name: could not compare the re-pinned lock with the one on the default branch — $(head -1 <<< "$shrink")"
        ((FAIL_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi
    if [[ -n "$shrink" ]]; then
        fail "$repo_name: refusing to propose this re-pin — $shrink. A bundle that vanished from the registry (a rename, a deleted plugin, a layout change) needs a human, not a fan-out."
        ((FAIL_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi

    # Belt and braces on top of --check-current: a ref that moved with the
    # bundle standing still can still re-serialize to the identical file, and
    # an identical file must never become a pull request.
    if git diff --quiet -- "$LOCK_REL_PATH"; then
        log "re-pin produced no change — leaving $repo_name alone."
        ((SKIP_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi

    new_ref=$(python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle).get("ref", "?"))
' "$LOCK_REL_PATH")

    # ── An open bump branch is never force-pushed over ─────────────────
    # Two cases, and neither is answered with --force. sync.sh deliberately
    # does not force-push either, and this repo's AGENTS.md records the
    # recovery for a stale bot branch: merge its PR to free the name.
    branch_exists=false
    branch_matches=false
    if git ls-remote --exit-code --heads origin "$BRANCH_NAME" >/dev/null 2>&1; then
        branch_exists=true
        if git fetch --depth 1 origin "$BRANCH_NAME" >/dev/null 2>&1 \
           && open_lock=$(git show "FETCH_HEAD:$LOCK_REL_PATH" 2>/dev/null) \
           && [[ "$open_lock" == "$(cat "$LOCK_REL_PATH")" ]]; then
            branch_matches=true
        fi
    fi

    if $branch_exists && ! $branch_matches; then
        log "WARN: $BRANCH_NAME already exists with different content — refusing to force-push. Merge or close its PR to free the branch, then re-run."
        ((SKIP_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi

    if $branch_exists; then
        # Nothing to push. Fall through to the PR step anyway: a previous run
        # that died between the push and `gh pr create` — an interrupted job,
        # a cancelled workflow — leaves a branch that is correct and a PR that
        # does not exist, and returning here would strand it forever, since
        # every later run would find the same matching branch and stop.
        log "an open bump branch already carries this exact lock — not pushing again."
    else
        git add "$LOCK_REL_PATH"

        # The mirror image of sync.sh's guard. There, a staged lock stops the
        # repo because the sync must never write one; here the lock is the
        # ONLY thing this script may write, so anything else staged means some
        # code path started touching a consumer's tree, and that must stop the
        # repo too.
        staged=$(git diff --cached --name-only)
        if [[ "$staged" != "$LOCK_REL_PATH" ]]; then
            fail "$repo_name: refusing to commit — expected only $LOCK_REL_PATH staged, got: ${staged//$'\n'/, }"
            ((FAIL_COUNT++)) || true
            cd "$REPO_ROOT"; continue
        fi

        # $primary_registry, not $BUMP_REGISTRY: the sha comes out of the
        # primary's checkout, and a name and a sha in one string must come
        # from the same repository. The guard above makes them equal today;
        # this keeps them equal by construction rather than by that guard.
        git commit -m "chore: re-pin $LOCK_REL_PATH onto ${primary_registry}@${new_ref:0:7}

The bundle content this lock installs has moved since ${old_ref:0:7}, so
nothing added or changed since then reached an ephemeral session. Generated by
generate_skills_lock.py --repin, which inherits this repo's own registry,
bundles and sources and re-resolves only the primary ref." >/dev/null || {
            log "Nothing to commit."
            ((SKIP_COUNT++)) || true
            cd "$REPO_ROOT"; continue
        }

        # Pushed, never with --force.
        push_ok=true
        push_out=$(git push origin "HEAD:refs/heads/$BRANCH_NAME" 2>&1) || push_ok=false
        if ! $push_ok; then
            # Matched on git's own non-fast-forward wording, never on the bare
            # word "rejected": the server prints `! [remote rejected]` for ANY
            # server-side refusal — a ruleset restricting ref creation (GH013),
            # a pre-receive hook, branch protection on creation — and calling
            # one of those a stale bump branch prints a remedy ("merge or close
            # its PR") for a branch that does not exist, counts a genuinely
            # stale consumer as a skip, and leaves the run green, so the
            # scheduled-run-health issue this repo relies on never fires.
            if grep -qiE 'non-fast-forward|fetch first|updates were rejected because' <<< "$push_out"; then
                log "WARN: push to $BRANCH_NAME was rejected as non-fast-forward — refusing to force. Merge or close its PR to free the branch, then re-run."
                ((SKIP_COUNT++)) || true
            else
                fail "push failed for $repo_name — $(head -1 <<< "$push_out")"
                ((FAIL_COUNT++)) || true
            fi
            cd "$REPO_ROOT"; continue
        fi
    fi

    # ── Open the PR ────────────────────────────────────────────────────

    # What the PR discloses is what a reviewer cannot see in a one-line diff:
    # which registry moved, that the digests are re-derived from the newly
    # pinned commit rather than from anyone's working tree, that a federated
    # source's own pin is untouched, and that nothing here was hand-written.
    #
    # The quoted verdict is the PRIMARY-scoped one, so every difference line in
    # it belongs to the ref this PR advances. Its `-o` path is rewritten back
    # to the consumer's own `skills.lock`: the generator names the file it was
    # given, which here is a copy under this run's mktemp directory, and a
    # /tmp path nobody can resolve is the one line of an otherwise checkable
    # body that a reviewer has to take on faith.
    federated_note="This lock has no federated sources."
    if [[ ${#source_registries[@]} -gt 0 ]]; then
        federated_lines=""
        for reg in "${source_registries[@]}"; do
            federated_lines="${federated_lines}
- \`$(lock_summary "$LOCK_REL_PATH" "$reg")\` — **unchanged**"
        done
        federated_note="**Federated sources keep their pins.** \`--repin\` advances the primary
\`ref\` only; it refuses \`--source\` outright, because that flag REPLACES the
inherited \`sources\` array and would silently de-federate the lock. Advancing
one of these is still a human's job:
${federated_lines}"
    fi

    existing_pr=$(gh pr list --head "$BRANCH_NAME" --json number \
        --jq '.[0].number // empty' 2>/dev/null || true)

    if [[ -n "$existing_pr" ]]; then
        log "PR #$existing_pr is already open for this branch — leaving it alone."
        ((SKIP_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi

    if gh pr create \
        --head "$BRANCH_NAME" \
        --title "chore: re-pin skills.lock onto ${primary_registry}@${new_ref:0:7}" \
        --body "$(cat <<EOF
Automated re-pin of this repo's \`$LOCK_REL_PATH\`, opened by
\`scripts/bump-consumer-locks.sh\` in ${BUMPER_SOURCE}.

**What moved:** \`${primary_registry}\` — \`${old_ref:0:7}\` → \`${new_ref:0:7}\`.

The bundle content at the old ref no longer matches the registry's tree, which
is why this PR exists: a lock is not wrong for being old, but a lock pinned
before a skill changed delivers the older skill to every ephemeral session and
reports \`OK\` while doing it. \`--check-current\` says the two have diverged:

\`\`\`
$(sed -n '/^FAILED:/,$p' <<< "$check_out" | head -20 | sed "s#$primary_lock#$LOCK_REL_PATH#g")
\`\`\`

**Every digest here is re-derived from the newly pinned commit**, materialized
with \`git archive\` — never from anyone's working tree — so the lock describes
bytes that are actually published at \`${new_ref:0:7}\`.

$federated_note

**Generated, never hand-edited.** The whole change is
\`generate_skills_lock.py --repin\`'s output. That command inherits this repo's
own \`registry\`, \`bundles\` and \`sources\` from the lock already committed here
and re-resolves only \`ref\`; it cannot be told to change any of them. Nothing
in \`_agent-guidance\` composes a lock of its own — see its
\`docs/decisions/0005\`.
EOF
)" >/dev/null; then
        log "PR created."
        ((OK_COUNT++)) || true
    else
        fail "PR creation failed for $repo_name"
        ((FAIL_COUNT++)) || true
    fi

    cd "$REPO_ROOT"
done

done

echo ""
echo "=== Lock bump complete: $OK_COUNT proposed, $SKIP_COUNT skipped, $FAIL_COUNT failed ==="

if [[ $FAIL_COUNT -gt 0 ]]; then
    exit 1
fi
