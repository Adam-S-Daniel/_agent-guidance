#!/usr/bin/env bash
set -euo pipefail
#
# bump-consumer-locks.sh — re-pin every consumer's skills.lock onto the
# registry's current commit, one pull request per repo.
#
# Discovers repos dynamically via `gh repo list`, exactly as sync.sh does, and
# then makes TWO passes over what it found.
#
# PASS 1 — SWEEP. Merge the bump pull requests a PREVIOUS run left open, where
# every safety condition in sweep_bump_prs() holds. This is what makes these
# PRs land without anyone clicking merge, and it runs FIRST for a reason that
# is easy to lose: a PR merged seconds after it was opened is merged before
# any check has started, so the day between two nightly runs IS the window a
# consumer's CI gets. Native auto-merge would be the obvious mechanism and
# cannot arm on most of this fleet (see the attempt after `gh pr create`, and
# docs/decisions/0006).
#
# PASS 2 — PROPOSE. For each repo the script:
#   1. Fetches skills.lock from the default branch (absent → nothing to do)
#   2. Reads which registries that lock names — the primary and every
#      federated source — and skips a lock that never names the registry
#      this run is bumping
#   3. Asks the GENERATOR whether a re-pin is needed at all — TWO separate
#      questions, either of which is sufficient:
#        a. `--check-current`: does the bundle content at the PRIMARY's
#           pinned ref still match that registry's tree?
#        b. `--check-format`: are the digests this lock STORES in the
#           canonical `sha256:<hex>` shape? (a) cannot answer this — it
#           re-digests both trees and never reads the stored values — which
#           is how eight consumer locks of bare hex stayed unrepaired while
#           this script reported "no re-pin needed" at them nightly.
#      Unchanged AND well-formed → no PR, no push
#   4. Otherwise re-pins with `--repin` inside a clone of the consumer, and
#      opens (or leaves alone) one PR carrying that single file, whose body
#      names WHICH of the two questions forced it
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
#   BUMP_PR_AUTHOR          — the login that opens this bumper's pull requests,
#                             and the only author the sweep will merge
#                             (default: agents-md-sync[bot], the App this
#                             repo's workflows authenticate as). Run by hand
#                             under a personal token, PRs are authored by that
#                             human instead, so the sweep merges nothing until
#                             this is set — which is the safe direction.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Every sentence this script can put into a pull request title, a pull request
# body or a commit message lives there, beside the run state that makes it
# true, and `compose_bump_artifacts` is the only thing here that builds one.
# Split out because the property that matters is testable only in isolation: a
# body has to be renderable for a run state without a fleet, a clone or a
# network, across every combination of them at once.
# shellcheck source=lib/bump-pr-claims.sh
source "$SCRIPT_DIR/lib/bump-pr-claims.sh"
LOCK_REL_PATH="skills.lock"
# The canonical STORED shape of a lock digest, for THIS SCRIPT'S OWN prose —
# the line it logs when the shape gate fires, and the sentence in the PR body
# that restates the rule. This script never validates a digest itself; it asks
# the generator (--check-format) and quotes the answer. Named once so those two
# places cannot drift apart, and so the test that reads them has one string.
#
# It is NOT a claim that every mention of the shape in a bump PR is spelled
# this way, and an earlier version of this comment said so wrongly. The body
# also QUOTES --check-format's verdict verbatim, and the generator writes its
# own wording there — today `sha256:<64 lowercase hex>`, the same shape spelled
# more precisely. So both spellings appear in one body, a few lines apart. That
# is correct and must stay: a quotation edited to match the local prose around
# it stops being evidence, which is the whole reason the verdict is quoted
# rather than summarised.
LOCK_DIGEST_SHAPE='sha256:<64 hex>'
BRANCH_NAME="${BUMP_BRANCH:-skills-lock-bump/update}"
PR_AUTHOR="${BUMP_PR_AUTHOR:-agents-md-sync[bot]}"
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

# generator_error_line <captured output> — the one line that says WHAT went
# wrong, for a failure report that has room for one line.
#
# `head -1` is wrong for exactly one output shape, and it is the shape a
# generator VERSION SKEW produces: argparse prints its `usage:` banner on the
# FIRST line and `<prog>: error: unrecognized arguments: ...` on the LAST, so
# the first line of that failure is the only line that says nothing. A
# scheduled run then goes red every night quoting a usage banner, with the
# cause it holds in hand discarded. That is the same defect commit 6744fcf
# fixed in the sweep, and this script walked back into it the moment a soft
# probe could arm a gate against a generator that lacks the flag.
#
# Prefers the LAST line carrying an error marker — `ERROR:` is this
# generator's own report, `: error: ` is argparse's — and falls back to the
# first non-empty line, which is what every single-line `ERROR:` report the
# old `head -1` handled correctly already was.
generator_error_line() {
    local marked
    marked=$(grep -E '^ERROR:|: error: ' <<< "$1" | tail -1) || marked=""
    if [[ -n "$marked" ]]; then
        printf '%s' "$marked"
        return 0
    fi
    grep -v '^[[:space:]]*$' <<< "$1" | head -1 || true
}

FAIL_COUNT=0
OK_COUNT=0
SKIP_COUNT=0
# Merges are counted apart from OK_COUNT/SKIP_COUNT rather than folded into
# them: those two answer "how many consumers got a proposal, and how many did
# not", one line per repo, and the same repo can be swept AND proposed in one
# run. Folding would make the summary's numbers stop adding up to the fleet.
MERGE_COUNT=0

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

# pr_merge_verdict <pr json> <branch> <author> <lock path> — one line, either
#
#   READY <what the checks said>
#   SKIP  <why this pull request must not be merged>
#
# and a non-zero exit with a message when the JSON cannot be judged at all.
# Every reason to refuse a merge lives HERE, in one place, so a reviewer can
# read the whole gate at once rather than reconstructing it from a chain of
# early `continue`s. python3 rather than a jq filter for the same reason
# lock_plan() gives: this is structured data, and the branches below turn on
# distinctions (a null conclusion vs a missing key) that a one-line filter
# hides.
#
# The gate exists because merging is the one thing here nobody reviews. The
# three refusals that carry it:
#   * the PR must be OURS — head branch AND author, never one of the two. The
#     branch name is a convention anybody can push to; the author is the only
#     thing a stranger cannot forge.
#   * the diff must be the lock ALONE. A human who pushed another file onto
#     this branch owns it now, and merging it would land their work unread.
#   * no check may be un-green or unfinished — while an ABSENCE of checks is
#     not a failure, because most consumers in this fleet have no CI at all.
#     REQUIRED is not part of that test, and deliberately so: nothing is
#     required anywhere in this fleet (`required_status_checks: []`), so a gate
#     that only weighed required checks would stand open everywhere. The cost
#     is that ANY conclusion on the bump branch's head can stall the sweep,
#     including one a human put there by hand — `.github/workflows/ci.yml` in
#     this repo carries a `workflow_dispatch` and documents that residual above
#     its trigger. A stall that names the offending check in the log, and
#     clears on a green re-run, is the side of the trade this gate is on.
pr_merge_verdict() {
    python3 -c '
import json, sys

path, branch, author, lock_path = sys.argv[1:5]

try:
    with open(path, encoding="utf-8") as handle:
        pr = json.load(handle)
except Exception as exc:
    sys.exit("not readable JSON (%s)" % exc.__class__.__name__)
if not isinstance(pr, dict):
    sys.exit("top level is not an object")


def verdict(word, detail):
    print("%s %s" % (word, detail))
    raise SystemExit(0)


def normalize(login):
    # gh has named a GitHub App both as "name[bot]" and as "app/name" across
    # versions and endpoints. Normalizing BOTH sides means a gh upgrade cannot
    # quietly turn every PR into "not ours" — the sweep would simply stop
    # merging, and nothing about a green run would say so. A genuinely
    # different author still fails the comparison, which is what this is for.
    login = (login or "").strip().lower()
    if login.startswith("app/"):
        login = login[4:]
    if login.endswith("[bot]"):
        login = login[: -len("[bot]")]
    return login


head = pr.get("headRefName")
if head != branch:
    verdict("SKIP", "its head branch is %r, not the bump branch %r" % (head, branch))

pr_author = (pr.get("author") or {}).get("login")
# An empty expected author refuses everything rather than matching everything:
# BUMP_PR_AUTHOR="" must not become "merge anyone".
if not normalize(author) or normalize(pr_author) != normalize(author):
    verdict("SKIP", "it was opened by %r, and this bumper opens pull requests as %r"
                    % (pr_author, author))

if pr.get("isDraft"):
    verdict("SKIP", "it is a draft")

if (pr.get("reviewDecision") or "") == "CHANGES_REQUESTED":
    verdict("SKIP", "a reviewer has requested changes")

mergeable = pr.get("mergeable")
if mergeable != "MERGEABLE":
    # CONFLICTING is a real merge conflict; UNKNOWN is GitHub still computing
    # the answer, which is not a yes.
    verdict("SKIP", "GitHub reports mergeable=%s" % mergeable)

state = str(pr.get("mergeStateStatus") or "")
if state in ("DIRTY", "BLOCKED", "DRAFT", "UNKNOWN"):
    # DIRTY is a conflict the mergeable field has not caught up with. BLOCKED
    # is the repository itself holding this PR back — a required review, or a
    # required check this sweep cannot satisfy — and is a SKIP, not a failure,
    # because attempting the merge would be refused every night and paint the
    # scheduled run permanently red for a repo behaving exactly as configured.
    verdict("SKIP", "mergeStateStatus=%s" % state)

files = pr.get("files")
if not isinstance(files, list) or not files:
    verdict("SKIP", "GitHub reported no files for this pull request")
paths = sorted({str(entry.get("path")) for entry in files if isinstance(entry, dict)})
if paths != [lock_path]:
    verdict("SKIP", "its diff is not %s alone — it touches %s"
                    % (lock_path, ", ".join(paths)))

rollup = pr.get("statusCheckRollup")
if rollup is None:
    rollup = []
if not isinstance(rollup, list):
    sys.exit("statusCheckRollup is present but is not a list")

GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}
UNFINISHED = {"", "PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", "EXPECTED"}
pending, failing = [], []
for entry in rollup:
    if not isinstance(entry, dict):
        sys.exit("statusCheckRollup holds an entry that is not an object")
    name = entry.get("name") or entry.get("context") or "?"
    # A check RUN carries .conclusion, null until it concludes; a legacy commit
    # STATUS carries .state and has no conclusion key at all. Read only one and
    # failures of the other kind come back clean — this account has an incident
    # about exactly that (AGENTS.md, "The watch finished is not CI passed").
    result = str(entry.get("conclusion") or entry.get("state") or "").upper()
    if result in UNFINISHED:
        pending.append(str(name))
    elif result not in GREEN:
        failing.append("%s: %s" % (name, result))

if failing:
    verdict("SKIP", "%d check(s) are not green (%s)" % (len(failing), "; ".join(failing)))
if pending:
    verdict("SKIP", "%d check(s) have not concluded (%s)" % (len(pending), ", ".join(pending)))
if not rollup:
    # Not a failure, and the commonest case in this fleet: said in its own
    # words so "no checks ran" and "the checks passed" are different lines in
    # the log rather than one indistinguishable OK.
    verdict("READY", "no checks ran on it — this repo reports none")
verdict("READY", "all %d check(s) concluded green" % len(rollup))
' "$1" "$2" "$3" "$4"
}

# sweep_bump_prs — merge the bump pull requests a PREVIOUS run left open.
#
# WHY THIS RUNS BEFORE THE PROPOSE PASS AND NOT AFTER IT. Merging a PR seconds
# after opening it merges it before any check has started, so the sweep would
# be reading an empty rollup and calling it "no CI here" on repos that have
# CI. Sweeping first instead makes the gap between two nightly runs the window
# a consumer's CI gets — a full day — with no waiting, no polling and no state
# kept between runs: the open PR IS the state.
#
# It runs per owner, inside that owner's iteration, because the credential
# that can read and merge these PRs is that owner's own installation token.
# One owner's proposals therefore land before the next owner's sweep, which
# weakens nothing: the reason for the ordering is per PR — give its checks a
# day — and no PR this run opens is a candidate for this run's sweep.
#
# One repo's failure is counted and the loop continues, exactly as the propose
# pass does. Nothing here aborts the fleet.
# delete_bump_branch <repo> <branch> — remove a bump branch that is fully
# merged. Returns 0 when the branch is gone afterwards, 1 otherwise.
#
# WHY THIS EXISTS AT ALL. The proposer refuses to force-push a branch whose
# content it did not write, which is right — that rule is what stops it
# clobbering someone's work. But a MERGED bump PR whose branch was never
# deleted trips the same rule, and the repo then receives no lock update
# again, ever, with nothing anywhere going red: the bumper exits 0, and the
# consumer's own session-start verdict reads OK while it serves a stale
# bundle. Measured 2026-08-25: five of ten lock-carrying repos had been stuck
# that way since 2026-08-21 — agentskills-private, fastmail-actions,
# repo-settings, wsl-automation and jodidaniel/scratch-claude-002.
#
# So the bot cleans up after itself. It created the branch; leaving it behind
# is what disables the next run.
#
# Deleting an ALREADY-ABSENT ref is success, not failure: `--delete-branch`
# on the merge, or a repo with "automatically delete head branches" enabled,
# may have removed it microseconds earlier, and a WARN there would train a
# reader to ignore the line that matters.
delete_bump_branch() {
    local repo_name="$1" branch="$2" delete_out
    if [[ "$DRY_RUN" == "true" ]]; then
        log "[DRY RUN] Would delete $repo_name's $branch."
        return 0
    fi
    if delete_out=$(gh api -X DELETE "repos/$repo_name/git/refs/heads/$branch" 2>&1); then
        log "$repo_name: deleted $branch."
        return 0
    fi
    if grep -qiE 'not found|does not exist|reference does not exist' <<< "$delete_out"; then
        log "$repo_name: $branch was already gone."
        return 0
    fi
    log "$repo_name: WARN could not delete $branch — $(head -1 <<< "$delete_out")"
    return 1
}

# branch_adds_nothing_to_base <repo> <branch> — true when every commit on
# <branch> is already contained in the repo's default branch.
#
# Asked of GitHub rather than of git, because the propose pass clones with
# `--depth 1`: there is no history locally for `merge-base --is-ancestor` to
# walk, and deepening every consumer's clone to answer one question is a poor
# trade. The compare API answers it directly — `behind` and `identical` both
# mean the branch carries nothing the base lacks, so deleting it is provably
# lossless. Anything else (`ahead`, `diverged`) means real work would be
# thrown away, and the caller must refuse instead.
#
# An unreadable answer is NOT a yes. Every failure path here returns 1, so a
# rate limit or a network blip leaves the branch alone.
branch_adds_nothing_to_base() {
    local repo_name="$1" branch="$2" base status
    base=$(gh api "repos/$repo_name" --jq '.default_branch' 2>/dev/null) || return 1
    [[ -n "$base" ]] || return 1
    status=$(gh api "repos/$repo_name/compare/$base...$branch" --jq '.status' 2>/dev/null) || return 1
    [[ "$status" == "behind" || "$status" == "identical" ]]
}

sweep_bump_prs() {
    local repo_name numbers_raw number pr_json view_err verdict_line verdict detail merge_out
    local head_oid
    local -a match_args
    local -a numbers

    echo "Sweeping bump pull requests left open by a previous run"

    for repo_name in "${REPOS[@]}"; do
        # The same carve-out the propose pass makes: this bumper opens nothing
        # on the registry, so anything sitting on that branch name there is
        # not ours to merge.
        if [[ "$repo_name" == "$BUMP_REGISTRY" ]]; then
            log "$repo_name — the registry itself; nothing here opens a pull request on it, so nothing is swept."
            continue
        fi

        # Captured with its exit status, and only parsed on success: on an HTTP
        # error gh prints the raw error body to stdout and the --jq filter
        # never runs, so `|| true` here would read that JSON as a list of PR
        # numbers. Silence is not an option either — "could not ask" and "no
        # open PRs" are the same empty string, and only one of them is fine.
        if ! numbers_raw=$(gh pr list --repo "$repo_name" \
            --head "$BRANCH_NAME" --state open --json number \
            --jq '.[].number' 2>&1); then
            fail "$repo_name: could not list bump pull requests — $(head -1 <<< "$numbers_raw")"
            ((FAIL_COUNT++)) || true
            continue
        fi

        mapfile -t numbers < <(sed '/^$/d' <<< "$numbers_raw")
        # No line at all for the commonest case. Most repos in the fleet have
        # no bump PR on any given night, and twenty "nothing to merge" lines
        # would bury the ones that say something.
        [[ ${#numbers[@]} -eq 0 ]] && continue

        for number in "${numbers[@]}"; do
            pr_json="$WORK_DIR/$(echo "$repo_name" | tr '/' '_')-pr-$number.json"
            # Captured with its reason, the way `gh pr list` above already
            # does it: an unreadable pull request is a counted failure, and a
            # counted failure a scheduled run cannot diagnose from its own log
            # is only half reported.
            #
            # THE REDIRECTION ORDER IS LOAD-BEARING AND IS NOT A TIDY-UP.
            # Redirections are applied left to right, so `2>&1 >"$pr_json"`
            # first points stderr at wherever stdout goes right now — the
            # command substitution — and only THEN sends stdout to the file.
            # The reason lands in $view_err and the JSON lands in $pr_json.
            # Written the other way round, `>"$pr_json" 2>&1` sends BOTH to the
            # file: $view_err is always empty and the reason is discarded
            # again, which is the bug this line is fixing.
            if ! view_err=$(gh pr view "$number" --repo "$repo_name" --json \
                number,headRefName,headRefOid,isDraft,author,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,files \
                2>&1 >"$pr_json"); then
                fail "$repo_name#$number: could not read the pull request — $(head -1 <<< "$view_err")"
                ((FAIL_COUNT++)) || true
                continue
            fi

            if ! verdict_line=$(pr_merge_verdict "$pr_json" "$BRANCH_NAME" \
                "$PR_AUTHOR" "$LOCK_REL_PATH" 2>&1); then
                # "Cannot judge it" is not permission to merge it.
                fail "$repo_name#$number: could not judge whether this pull request is safe to merge — $(head -1 <<< "$verdict_line")"
                ((FAIL_COUNT++)) || true
                continue
            fi
            verdict="${verdict_line%% *}"
            detail="${verdict_line#* }"

            if [[ "$verdict" != "READY" ]]; then
                log "$repo_name#$number: not merged — $detail"
                continue
            fi

            if $DRY_RUN; then
                log "[DRY RUN] Would merge $repo_name#$number with a merge commit — $detail"
                continue
            fi

            # --merge, never --squash or --rebase. Both are disabled on every
            # fleet repo, so --squash fails outright rather than falling back;
            # and squash is actively unsafe for a fleet that pins commits by
            # sha, because it strands the commit a lock names on no branch.
            # See AGENTS.md, "Git practices".
            # Everything above judged a SNAPSHOT. Pinning the merge to the
            # head that snapshot described closes the gap between the two: if
            # anything reached the branch in between, GitHub refuses rather
            # than landing a diff nothing checked. An unreadable oid is
            # "cannot judge it" all over again, and is not permission to merge.
            head_oid=$(python3 -c '
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    oid = json.load(handle).get("headRefOid")
sys.stdout.write(oid if isinstance(oid, str) and re.fullmatch(r"[0-9a-f]{40}", oid) else "")
' "$pr_json" 2>/dev/null) || head_oid=""
            match_args=()
            if [[ -n "$MERGE_MATCH_FLAG" ]]; then
                if [[ -z "$head_oid" ]]; then
                    fail "$repo_name#$number: its head commit did not read back as a sha, so the merge could not be pinned to the commit that was just checked."
                    ((FAIL_COUNT++)) || true
                    continue
                fi
                match_args=("$MERGE_MATCH_FLAG" "$head_oid")
            fi

            if merge_out=$(gh pr merge "$number" --repo "$repo_name" --merge \
                ${match_args[@]+"${match_args[@]}"} 2>&1); then
                log "$repo_name#$number: MERGED with a merge commit — $detail"
                ((MERGE_COUNT++)) || true
                # The branch this bot created is the bot's to clean up. Left
                # behind, it is what makes the NEXT run refuse to propose here
                # — see delete_bump_branch. Deliberately not folded into
                # `gh pr merge --delete-branch`: a deletion that fails there
                # can take the merge's exit code with it, turning a landed
                # merge into a reported failure and a wrong MERGE_COUNT.
                # Separate call, separate consequence: the merge already
                # counted, and a failed delete warns without unwinding it.
                delete_bump_branch "$repo_name" "$BRANCH_NAME" || true
            else
                fail "$repo_name#$number: merge was refused — $(head -1 <<< "$merge_out")"
                ((FAIL_COUNT++)) || true
            fi
        done
    done

    echo ""
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

# ── Is the generator able to answer the SHAPE question? ───────────────────
# `--check-current` is structurally blind to a lock's stored digest VALUES: it
# digests the pinned tree and the working tree afresh and compares those, and
# never reads `skills` at all. That is deliberate on the generator's side —
# `_label_digests`' docstring says labelling is applied at the DOCUMENT
# BOUNDARY precisely so every comparison between builder outputs keeps working
# on bare hex — and it is exactly what leaves this script's anti-churn gate
# unable to see a malformed lock. Eight consumers (cms-platform, GHA-bench,
# _agent-guidance itself, agentskills-private, claude-memory-map,
# fastmail-actions, repo-settings, wsl-automation) stored bare 64-hex where
# the canonical shape is `sha256:<hex>`, all pinning 94cdcc81; because the
# `adam` bundle had not moved, `--check-current` answered `OK: ...` at exit 0
# every night and this script logged "no re-pin needed" and skipped. A healing
# mechanism reporting success while doing nothing about the defect it exists
# to fix.
#
# Probed, not assumed, for the same reason --repin is above: this script runs
# against whatever generator checkout it is pointed at, and the flag is newer
# than the script's other requirements. Unlike --repin it is NOT load-bearing
# — without it the gate simply asks one question instead of two and behaves
# exactly as it did before — so a shortfall must never be a hard failure, and
# must never be silent either. It is announced as a workflow annotation rather
# than the plain `NOTE:` its sibling probe below uses because this script's
# real caller is a NIGHTLY SCHEDULED run, where a line on stderr is seen by
# nobody; an annotation is the one form that survives to the run summary.
FORMAT_GATE_AVAILABLE=true
if ! grep -q -- '--check-format' <<< "$generator_help"; then
    FORMAT_GATE_AVAILABLE=false
    echo "::warning::$GENERATOR has no --check-format; this run can only ask whether each bundle has MOVED, not whether a lock's stored digests are the canonical sha256:<hex>. A lock malformed that way will keep reporting 'no re-pin needed' until the generator carries the flag." >&2
fi

# ── Can the generator say WHICH source moved, and advance just that one? ──
# Two flags, one capability, and they are only useful together.
# `--check-current --only <REGISTRY>` is the GATE primitive: it asks about one
# registry the lock plans, so this script learns which half drifted from the
# exit code of the question it ASKED rather than from the wording of a
# combined answer. `--repin --repin-source '<REGISTRY>@'` is the REMEDY: it
# merges one source's pin into the inherited `sources` array by registry key,
# so it can never add, drop or reorder a source.
#
# BOTH, OR NEITHER, and the flag pair is deliberately not split into two
# capabilities. A scoped question with no way to act on it is the report-only
# behaviour this script already had; acting without the scoped question is the
# failure docs/decisions/0009 exists to prevent, because a full-lock verdict
# says FAILED for a PRIMARY-only drift and would advance every federated pin
# on every routine bump night.
#
# SOFT, exactly like --check-format above, and for a sharper version of the
# same reason: this script's generator comes from a checkout of the registry's
# DEFAULT BRANCH, and these flags arrive there in their own pull request. A
# hard probe would ground every nightly run in the window between that merge
# and this one — a fleet-wide outage produced by the ordering of two green
# PRs. Degrading costs nothing that was ever promised: the federated half goes
# back to being reported and not acted on, which is what it was before.
#
# A CAPABILITY CHECK, never a substring of --help, and this is the one probe
# on which that distinction has teeth. `grep -q -- '--only'` matches any
# longer flag whose name merely BEGINS `--only`, and argparse's default
# `allow_abbrev=True` then accepts `--only <registry>` as an unambiguous
# prefix abbreviation of that other flag — so the gate arms and the scoped
# question silently becomes an UNSCOPED one, whose FAILED: for a primary-only
# drift this script reads as source drift. Measured on this repo's own
# negative-control fixture with the stub's `--only` replaced by an unrelated
# `--only-bundles`: no warning, no error, and the federated pin advanced on a
# drift that was entirely the primary's. Prose in another flag's help text
# arms it just as well.
#
# So each flag is asked to REFUSE, which is a behaviour no prefix match and no
# help text can fake: passed on its own, `--only` and `--repin-source` each
# name the flag they only mean anything beside, and the generator says so and
# exits 2. Nothing is written: neither probe passes `--repin`, the refusal
# happens in argparse's post-parse block before any path is opened, and `-o`
# names /dev/null anyway. That `-o` is there because it is REQUIRED by some
# generators (the suite's stand-in among them) and optional in others, and an
# argparse "the following arguments are required" is not this flag's refusal —
# without it the probe degrades every run that meets such a generator.
#
# The cost is that this reads the generator's WORDING, which a gate must never
# do. This is not a gate: it decides only whether to ASK, and it fails in the
# safe direction. Reword either refusal in agentskills and every run degrades
# loudly to report-only until this needle is updated, which is the behaviour
# the fleet had before these flags existed.
#
# Each probe degrades on EITHER of two outcomes, and both matter: the command
# succeeding at all (a generator that accepts a lone `--only` is not refusing
# it, whatever it did instead), or the refusal not being this flag's.
FED_ADVANCE_AVAILABLE=true
if only_probe=$(python3 "$GENERATOR" --only probe/probe -o /dev/null 2>&1) \
   || ! grep -q -- '--only scopes --check-current' <<< "$only_probe"; then
    FED_ADVANCE_AVAILABLE=false
    echo "::warning::$GENERATOR has no '--check-current --only <REGISTRY>' (a lone --only was not refused the way that flag refuses one); this run cannot ask which HALF of a federated lock moved, so a federated source that has moved on is reported and left alone. Update the registry checkout to advance those pins." >&2
fi
if repin_source_probe=$(python3 "$GENERATOR" --repin-source 'probe/probe@' -o /dev/null 2>&1) \
   || ! grep -q -- '--repin-source advances a pin the lock already carries' <<< "$repin_source_probe"; then
    FED_ADVANCE_AVAILABLE=false
    echo "::warning::$GENERATOR has no '--repin --repin-source <REGISTRY>@' (a lone --repin-source was not refused the way that flag refuses one); this run cannot advance a federated source's pin, so one that has moved on is reported and left alone. Update the registry checkout to advance those pins." >&2
fi

# `gh pr merge --match-head-commit <sha>` refuses the merge if the head moved
# since we read it, which is what makes the sweep's verdict and its merge one
# decision instead of two. Probed rather than assumed: unlike --repin the flag
# is not load-bearing — without it the sweep is merely non-atomic, and a hard
# exit here would ground the whole fleet over a gh old enough to lack it. So a
# shortfall degrades and SAYS SO, once, instead of failing or going quiet.
MERGE_MATCH_FLAG=""
if gh_merge_help=$(gh pr merge --help 2>&1) \
   && grep -q -- '--match-head-commit' <<< "$gh_merge_help"; then
    MERGE_MATCH_FLAG="--match-head-commit"
else
    echo "NOTE: this gh has no 'gh pr merge --match-head-commit'; the sweep will merge without the head-match guard, so a push landing on a bump branch between the safety check and the merge would not be caught." >&2
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

# ── Pass 1: sweep, before anything is proposed ─────────────────────────────
sweep_bump_prs

# ── Pass 2: propose ────────────────────────────────────────────────────────

echo "Proposing re-pins for consumers whose bundle has moved"
echo ""

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
    # DEDUPED, first occurrence kept. A registry may appear in `sources` more
    # than once — `plan_sources`' uniqueness check is keyed on BUNDLE, so two
    # entries may share a registry while carrying different bundles and their
    # own pins — and every use of this array downstream is keyed on the
    # REGISTRY NAME: one scoped question per name, one `--repin-source
    # <name>@` per name, one line per name in the PR body. Left undeduped, the
    # gate asks the same question twice and then builds the same flag twice,
    # which the generator refuses as a command-line mistake ("names <reg>
    # twice; one pin per source") — so the run reports the bumper's own
    # duplicated flag as the fault and SUPPRESSES the generator's accurate
    # diagnosis of the lock, which is that it federates that registry twice
    # and one spec cannot say which entry is meant. Same outcome either way —
    # nothing is written and the night goes red — and only one of them tells a
    # human what to fix.
    mapfile -t source_registries < <(echo "$plan" | sed -n 's/^source //p' | awk '!seen[$0]++')

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
            log "$LOCK_REL_PATH federates $BUMP_REGISTRY but pins $primary_registry as its primary — advancing it here means re-pinning $primary_registry, which is another registry's decision and not this run's; skipping."
        else
            log "$LOCK_REL_PATH pins $primary_registry and never names $BUMP_REGISTRY — skipping."
        fi
        ((SKIP_COUNT++)) || true
        continue
    fi

    # ── Locate every checkout this lock needs ──────────────────────────
    # --repin re-derives EVERY digest, each source's at whatever ref that
    # source ends the run pinning — its own unchanged pin, or the advanced one
    # if `--repin-source` names it. A missing checkout therefore means the lock
    # cannot be rebuilt at all, and skipping is the only safe outcome:
    # half-re-pinning a federated lock is the exact damage ADR 0001 named.
    # (The primary currency question below is scoped to the primary and would
    # not need them; the per-source questions and the rebuild both do.)

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
    # and emits one FAILED: for the whole run if any of them differs, while a
    # bare --repin advances ONLY the primary `ref`. So a combined verdict
    # cannot say WHICH half moved, and the half it does not name is the half
    # this branch would act on: every commit to the primary registry — docs, a
    # workflow, a Dependabot bump, the registry's own lock commit — would
    # yield a pull request for a federated lock whose entire diff is `ref` +
    # `generated_from`, with not one digest changed, or worse, one that
    # advanced a federated pin nothing had asked about. Handing the generator
    # a copy of the lock with `sources` removed asks a different QUESTION
    # rather than reinterpreting the combined answer, so it cannot drift with
    # the generator's wording.
    #
    # This paragraph used to end with an absolute — that nothing in this
    # system advances a federated pin — and rested the case for scoping on the
    # PERMANENCE of a federated FAILED:, a source ahead of its pin being
    # something no re-pin could clear. Both were true when written and neither
    # survived the loop forty lines
    # below, which advances one on a question scoped to that source. Left
    # standing, a reader going top to bottom met the false absolute before
    # reaching the code that contradicts it — and this repo's own rule is that
    # a comment must not assert anything a reader cannot check. The scoping is
    # still necessary; the reason is attribution, not permanence.
    primary_lock="${lock_file%.lock}.primary.lock"
    if ! primary_only "$lock_file" "$primary_lock" 2>/dev/null; then
        fail "$repo_name: could not scope $LOCK_REL_PATH to its primary registry."
        ((FAIL_COUNT++)) || true
        continue
    fi

    # Which QUESTION forced this repo's re-pin: "" (none yet), `content` (the
    # bundle moved) or `format` (it did not, but the stored digests are
    # malformed). Reset per repo — a leftover value would carry one consumer's
    # reason into the next consumer's PR body.
    repin_reason=""
    fed_drifted_regs=()
    fed_check_out=""
    current_exit=0
    check_out=$(python3 "$GENERATOR" --check-current \
        --repo "$primary_dir" \
        -o "$primary_lock" 2>&1) || current_exit=$?

    # A non-zero exit is NOT automatically drift, and this is where that has
    # to be read — before anything else asks anything. The generator reports a
    # broken lock, an unreachable pinned commit or a mis-pointed --repo with
    # the same exit code and an ERROR line; re-pinning on the strength of one
    # of those writes a lock nobody asked for. Only its own FAILED verdict
    # means "the bundle moved".
    #
    # HOISTED ABOVE THE FEDERATED SECTION, and that is a bug fix rather than
    # tidying. It used to sit in the `else` of the currency branch a hundred
    # lines below, so a lock whose PRIMARY question could not be answered went
    # on to be asked a federated question anyway — and on a degraded run the
    # combined `--check-current` returned the SAME primary-side error, which
    # that path reports as "could not read this lock's federated half".
    # Measured on a federated lock pinning an unresolvable primary ref: the
    # unresolvable ref is the primary's, in the primary's checkout, and the
    # lock's federated half is intact. A failure report that names the wrong
    # half sends the reader to the wrong repository. Asked first, the primary
    # question's own failure is the one that gets reported, and nothing spends
    # a generator run on a lock already known unreadable.
    if [[ $current_exit -ne 0 ]] && ! grep -q '^FAILED:' <<< "$check_out"; then
        fail "$repo_name: could not decide whether $LOCK_REL_PATH is current — $(generator_error_line "$check_out")"
        ((FAIL_COUNT++)) || true
        continue
    fi

    # ── WHICH federated sources have moved? ONE SCOPED QUESTION EACH ───
    # This is the whole point of docs/decisions/0009, so it is spelled out
    # rather than left to be inferred from the loop.
    #
    # THE THING NOT TO DO, because it is the obvious refactor and it is
    # catastrophic: run `--check-current` once over the FULL lock and read its
    # single verdict as "the federated half moved". Measured against the real
    # generator on a two-source lock — primary edited, both sources sitting
    # exactly at their pins — that run prints ONE `FAILED:` headline, anchored
    # on the PRIMARY's ref, and exits 1. So a combined verdict says "federated
    # drift" on every night the primary registry has a new commit, and every
    # federated pin in the fleet would be advanced to its source's HEAD by a
    # routine bump. ADR 0005 reserved that advance for a human precisely
    # because it is not reversible by another bump.
    #
    # Asking one scoped question per source makes attribution a property of
    # WHICH QUESTION WAS ASKED rather than of what the answer said — the same
    # discipline `primary_only` above already applies to the primary half, and
    # for the same stated reason: it cannot drift with the generator's wording.
    #
    # Asked for BOTH outcomes of the primary question, not just the current
    # one. A stale primary and a moved source are independent facts and both
    # can be true; the re-pin that fixes one is the re-pin that fixes the
    # other, and splitting them across two nights would open two PRs whose
    # diffs overlap.
    #
    # The else-fail discipline is verbatim what the primary branch below uses:
    # a non-zero exit is NOT drift. A bad `--only` value lands on exactly that
    # path, because the generator raises for it and exits 1 with an ERROR:
    # line and no `^FAILED:` at all.
    #
    # THE PRIMARY'S OWN NAME IS NOT A SCOPED QUESTION, and skipping it is not
    # an optimisation. A lock may name one registry as BOTH its primary and a
    # federated source — `plan_sources`' uniqueness check is keyed on BUNDLE,
    # so two entries may share a registry — and the generator refuses to scope
    # to such a name because it has two answers, correctly. Asking anyway
    # lands on the else-fail path below, which is safe but has NO WAY BACK:
    # the lock is red every night until a human edits it, where on the
    # combined-verdict behaviour this replaced the same lock was re-pinned
    # normally. So the question this script cannot ask is not asked, the
    # primary's own currency is answered by `$primary_lock` above as it always
    # was, and that entry's pin is carried through untouched — which is also
    # what `--repin-source` would do with it, since it refuses the primary's
    # name outright. No lock in this fleet has that shape today; nothing else
    # rejects it either.
    #
    # COUNTED AS WELL AS FLAGGED, because a remedy below depends on the
    # difference. `fed_listed_sources` is the sources a scoped question COULD
    # be put about — everything the lock names except its own primary
    # registry — and it is the same distinction `CLAIM_LISTED_SOURCES` draws
    # in the claims library, derived here because this loop runs long before
    # `claims_state` does.
    fed_self_named=false
    fed_listed_sources=0
    for reg in ${source_registries[@]+"${source_registries[@]}"}; do
        if [[ "$reg" == "$primary_registry" ]]; then
            fed_self_named=true
        else
            fed_listed_sources=$((fed_listed_sources + 1))
        fi
    done
    # THE REASON, not just the fact, and the two are not the same on every
    # run. With the scoped flags present the question is declined BECAUSE the
    # name has two answers; without them no per-source question could be put
    # about any source, whatever any name meant. Both sentences live in
    # lib/bump-pr-claims.sh beside the two PR-body claims that say the same
    # thing, branching on the same condition — so the log a human reads in a
    # red nightly cannot contradict the body of the pull request that run
    # opened, which is what it did until this line was gated.
    if $fed_self_named; then
        log "$(self_named_log_line "$FED_ADVANCE_AVAILABLE" "$LOCK_REL_PATH" "$primary_registry")"
    fi

    fed_gate_failed=false
    if [[ ${#source_registries[@]} -gt 0 ]] && $FED_ADVANCE_AVAILABLE; then
        for reg in "${source_registries[@]}"; do
            [[ "$reg" == "$primary_registry" ]] && continue
            fed_exit=0
            fed_out=$(python3 "$GENERATOR" --check-current --only "$reg" \
                --repo "$primary_dir" \
                ${source_repo_args[@]+"${source_repo_args[@]}"} \
                -o "$lock_file" 2>&1) || fed_exit=$?
            [[ $fed_exit -eq 0 ]] && continue
            if grep -q '^FAILED:' <<< "$fed_out"; then
                fed_drifted_regs+=("$reg")
                fed_check_out="${fed_check_out}${fed_out}
"
            else
                fail "$repo_name: could not decide whether $LOCK_REL_PATH's $reg source is current — $(generator_error_line "$fed_out")"
                fed_gate_failed=true
                break
            fi
        done
    fi
    if $fed_gate_failed; then
        ((FAIL_COUNT++)) || true
        continue
    fi

    # THE DEGRADED PATH, and only that. With the scoped flags present the loop
    # above has already answered per source; this is what is left when the
    # generator predates them, and it is exactly what this script did before:
    # ask the combined question, and if anything moved, SAY SO and act on
    # nothing. Announced as a workflow annotation rather than through log(),
    # which writes to stdout — and a green nightly's stdout is read by nobody,
    # which is how a federated source could sit ahead of its pin indefinitely
    # with a signal that reached no one.
    #
    # ASKED FOR BOTH OUTCOMES of the primary question, like the scoped loop
    # above and for the same reason. It used to sit inside the
    # `current_exit -eq 0` branch, which left it silent on precisely the
    # degraded repos that DO open a pull request — the ones whose primary also
    # drifted — so the half of the degraded population whose PR body has a
    # federated section to be read got no annotation at all.
    #
    # TWO WORDINGS, because the combined verdict means different things on
    # either side of that question and one sentence cannot be true of both. A
    # combined `--check-current` reports one FAILED: for the whole lock: with
    # the primary answering OK on its own scoped copy, that FAILED can only be
    # a source, and the first wording says so. With the primary drifted it can
    # be the primary alone, so the second says only what is left — that this
    # generator cannot split the two, and no pin here was verified.
    if [[ ${#source_registries[@]} -gt 0 ]] && ! $FED_ADVANCE_AVAILABLE; then
        fed_exit=0
        fed_out=$(python3 "$GENERATOR" --check-current \
            --repo "$primary_dir" \
            ${source_repo_args[@]+"${source_repo_args[@]}"} \
            -o "$lock_file" 2>&1) || fed_exit=$?
        if [[ $fed_exit -ne 0 ]]; then
            if grep -q '^FAILED:' <<< "$fed_out"; then
                if [[ $current_exit -eq 0 ]]; then
                    echo "::warning::$repo_name: a FEDERATED source has moved on since the ref this lock pins for it, and this run did not ask which one or advance it — this generator has no ${SCOPED_FLAG_PAIR} pair, and a scoped question this script cannot act on is one it does not put. Nothing is re-pinned here." >&2
                else
                    # THE REMEDY IS GATED ON THERE BEING ONE, which is the same
                    # defect 89c6231 closed on the log() line eight lines above
                    # and left standing here. "Update the registry checkout to
                    # ask one scoped question per source" is an instruction
                    # whose result is ZERO scoped questions on a lock whose only
                    # `sources` entry names its own primary registry: this
                    # script never scopes a question to that name and
                    # `--repin-source` refuses it, so the limitation is a
                    # permanent property of the LOCK and not of the generator's
                    # age. Measured on a real run of the degraded
                    # self-federating fixture: this annotation sent the reader
                    # to a checkout upgrade while the same run's PR body said
                    # "No federated source in this lock could be asked about."
                    # Two artifacts of one run, disagreeing.
                    # Both arms live in lib/bump-pr-claims.sh beside the
                    # self-named log line, for the reason that one does: a
                    # guard on either has to read it from there rather than
                    # re-type it.
                    fed_degraded_remedy="$(degraded_fed_remedy "$fed_listed_sources" "$primary_registry")"
                    echo "::warning::$repo_name: the primary has moved, and this run did not ask whether a FEDERATED source has moved with it — this generator has no ${SCOPED_FLAG_PAIR} pair, and a combined --check-current answers for the whole lock at once. Every federated pin here is carried through unverified. $fed_degraded_remedy" >&2
                fi
            else
                fail "$repo_name: could not read this lock's federated half — $(generator_error_line "$fed_out")"
                ((FAIL_COUNT++)) || true
                continue
            fi
        fi
    fi

    if [[ $current_exit -eq 0 ]]; then
        # ── SECOND QUESTION: are the stored digests the right SHAPE? ───
        # Not a reinterpretation of the verdict above — a different question
        # put to the generator, which is the same discipline the `sources`
        # scoping a few lines up is written for. --check-current cannot answer
        # this one at all: it compares two freshly digested trees and never
        # reads `skills`, because `_label_digests` applies the `sha256:` label
        # at the DOCUMENT BOUNDARY expressly so comparisons between builder
        # outputs keep seeing bare hex. So a lock whose every stored digest is
        # malformed is GREEN above, forever, for as long as the bundle stands
        # still — which is how eight consumer locks sat unrepaired on 94cdcc81
        # while this script logged "no re-pin needed" at them every night.
        #
        # Borrowing --check's verdict instead was the tempting shortcut and is
        # the one to avoid: it does fail on such a lock, but it reports a wrong
        # SHAPE in the same words as content drift ("digest changed"), so the
        # gate would be keyed on wording rather than on a fact. A distinct
        # question gets a distinct flag.
        #
        # Asked of the FULL lock, not the primary-scoped copy: unlike currency,
        # which is per-source and answered per-source, the shape defect is a
        # property of the whole file — a federated source's skills land in the
        # same `skills` map and are malformed or not alongside the primary's.
        #
        # ONE-TIME CONSEQUENCE, stated plainly: the repair is a re-pin, and a
        # re-pin also advances `ref` to the registry's current HEAD. So the
        # first run after this lands opens one PR per affected repo whose diff
        # is `ref` + `generated_from` + the relabelled digests — more than the
        # relabelling strictly needed, and the honest cost of healing through
        # the generator's own primitive rather than hand-editing a label onto
        # a value nobody recomputed. It is self-limiting: once labelled, this
        # gate is satisfied and the anti-churn behaviour above resumes.
        #
        # THE ONE SHAPE THIS GATE CANNOT HEAL, named here rather than left to
        # be discovered from a red run. A lock whose `skills` map is EMPTY —
        # or missing, or not a map — is not a lock with malformed digests, it
        # is a lock with no digests to have a shape at all. The generator
        # answers those with ERROR: rather than FAILED:, deliberately, so the
        # branch below routes them to the report-and-count path instead of the
        # rewrite path. That is what stops a re-pin loop with no exit: --repin
        # over a registry with no skills writes the same empty map straight
        # back, and `skills_shrink_reason`'s `if not after` then refuses to
        # propose it, correctly, because an emptied lock REAPS the installed
        # skills of every ephemeral session in that repo. Re-pinning it nightly
        # only to refuse the result every night would be motion, not repair.
        #
        # So such a lock counts a failure and is LEFT ALONE. Not live — no lock
        # in this fleet has an empty map, and one would mean a registry whose
        # bundles had all vanished, which is precisely what the shrink guard
        # exists to stop fanning out. Left as a loud nightly failure rather
        # than skipped quietly, because that IS a human's decision and the run
        # saying so every night is how the human hears about it. What must not
        # happen is someone meeting that red and "fixing" it either by
        # loosening the shrink guard or by teaching this branch to treat an
        # ERROR: as a licence to rewrite.
        if $FORMAT_GATE_AVAILABLE; then
            format_exit=0
            format_out=$(python3 "$GENERATOR" --check-format \
                -o "$lock_file" 2>&1) || format_exit=$?
            if [[ $format_exit -ne 0 ]]; then
                # Same refusal to read a bare exit code as a verdict that the
                # --check-current branch below makes, and for the same reason:
                # a missing file and unparseable JSON also exit 1 here, and
                # both print ERROR: rather than FAILED:. Only the flag's own
                # FAILED: means "these digests are malformed"; anything else
                # means the question could not be answered, which is not a
                # licence to rewrite a consumer's lock.
                if grep -q '^FAILED:' <<< "$format_out"; then
                    repin_reason=format
                else
                    fail "$repo_name: could not decide whether $LOCK_REL_PATH's digests are well-formed — $(generator_error_line "$format_out")"
                    ((FAIL_COUNT++)) || true
                    continue
                fi
            fi
        fi

        if [[ -z "$repin_reason" && ${#fed_drifted_regs[@]} -eq 0 ]]; then
            log "bundle content unchanged since ${old_ref:0:7} — no re-pin needed."
            ((SKIP_COUNT++)) || true
            continue
        fi

        # Said distinctly from the bundle-moved case below, because the reason
        # is what a reader of this log (and of the PR it produces) has to be
        # able to tell apart: nothing moved, and the lock is still being
        # rewritten.
        #
        # `federated` is a THIRD value of $repin_reason, not an overload of
        # either existing one: the shape question and the currency question are
        # independent of it, and a run can have both. It is set only where
        # neither of those fired, so it answers "why does this PR exist at all"
        # for the one case where the answer is a source's pin and nothing else.
        if [[ "$repin_reason" == "format" ]]; then
            log "bundle content unchanged since ${old_ref:0:7}, but this lock's stored digests are not ${LOCK_DIGEST_SHAPE} — re-pin needed to relabel them."
            log "the pin stays at ${old_ref:0:7}: a shape repair is not a content advance."
        else
            repin_reason=federated
            log "bundle content unchanged since ${old_ref:0:7}, but a federated source has moved — re-pin needed to advance its pin."
            log "the pin stays at ${old_ref:0:7}: advancing a federated source is not a primary content advance."
        fi
    else
        # The `^FAILED:` reading was done above, before any federated question
        # was put, so reaching here means the generator's own verdict said the
        # bundle moved.
        repin_reason=content
        log "bundle has moved since ${old_ref:0:7} — re-pin needed."
    fi

    # Said once, after both branches, because a federated advance rides along
    # with whichever question forced the re-pin and is not a fourth reason.
    if [[ ${#fed_drifted_regs[@]} -gt 0 ]]; then
        log "federated sources whose pins this re-pin advances: ${fed_drifted_regs[*]}"
    fi

    if $DRY_RUN; then
        # Branched because the pin only moves for a content re-pin. The
        # unconditional wording claimed an advance onto the registry's current
        # commit, which a format or federated-only re-pin does not make.
        if [[ "$repin_reason" == "content" ]]; then
            log "[DRY RUN] Would re-pin $LOCK_REL_PATH onto $primary_registry's current commit and open a PR on $BRANCH_NAME"
        else
            log "[DRY RUN] Would re-pin $LOCK_REL_PATH with its pin held at ${old_ref:0:7} and open a PR on $BRANCH_NAME"
        fi
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

    # ── A SHAPE repair is never a CONTENT advance ──────────────────────
    # `--repin` deliberately does not inherit `ref` — advancing the pin IS the
    # operation — so an invocation without `--ref` falls through to the
    # generator's `resolve_ref(repo, "HEAD")` and re-pins onto whatever commit
    # this run's registry checkout happens to be sitting on. That is exactly
    # right for `repin_reason=content`, where a moved bundle is the reason the
    # PR exists, and exactly wrong for `format`, where the complaint is about
    # the digests STORED here and the bundle at the pinned ref is — by the
    # gate above, which only fires after `--check-current` returned 0 —
    # unchanged.
    #
    # Measured on a copy of repo-settings' real bare lock, which is one of the
    # eight that sat malformed on 94cdcc81: without `--ref` the pin moved to
    # the clone's HEAD; with `--ref` naming the lock's own pin, `ref`,
    # `generated_from` and every digest's hex came back byte-identical and the
    # entire diff was the eight `sha256:` labels. All eight were healed BY HAND
    # that morning and every pin was preserved; had this script reached them
    # first it would have advanced all eight in one sweep, silently turning a
    # shape repair into a fleet-wide content advance under a PR body whose
    # every digest line proves nothing diverged.
    #
    # agentskills #108 put `--ref` into the report a HUMAN reads
    # (`--check-format`'s remediation line); this is the same anchor on the
    # unattended path that runs every night. It also makes that quoted line
    # REPRODUCE the diff beneath it: the body prints the generator's verdict
    # verbatim, so with no `--ref` here a reviewer read a command naming the
    # old pin above a diff that had moved it.
    #
    # A SIBLING SITE MOVES WITH THIS, and it is not editable from here.
    # agentskills' `report_digest_format` prints the `--check-format`
    # remediation line this script quotes verbatim into a PR body, and that
    # line carries a `--ref` of its own for the same reason this branch does.
    # So the two paths AGREE: a shape repair holds the pin whether a human at
    # a terminal or this nightly performs it, which is what makes quoting the
    # report honest — the command a reviewer reads is the command that
    # produced the diff beneath it. The comment beside that print block says
    # the same from the other side and names this one, so neither half is a
    # pointer to nowhere.
    #
    # This block asked, until _agent-guidance#65, for a paragraph over there to
    # be rewritten — the one that stated this script's own re-pin passes no
    # `--ref` and called the resulting mismatch an asymmetry working as
    # intended. That request is DISCHARGED: read on ag58-generator before this
    # edit, the paragraph is gone and its replacement states the agreement
    # above, naming this block from the other side. The
    # request is deleted rather than left standing, because a cross-repo
    # instruction pointing at text that is not there is read and believed, and
    # costs the next reader a reconstruction of whether the debt was paid or
    # the paragraph merely moved. Nothing compares the two copies
    # automatically; a change to either half still has to be carried across by
    # hand, which is why the heading stays and only its content moved.
    #
    # `$old_ref` is safe to resolve: `--check-current` exited 0 for this repo,
    # and it gets there only by resolving and `git archive`-ing that very
    # commit out of `$primary_dir`. Nothing is stranded by holding the pin
    # either — currency is a separate question asked afresh every night, so a
    # bundle that later moves gets its own PR, headed by the move.
    # `!= content` rather than `== format`, and the third case is why: a
    # re-pin forced ONLY by a federated source must hold the primary's pin for
    # the same reason a shape repair does. Advancing a source is not a primary
    # content advance, and the primary's own currency was asked and answered
    # `OK` on the branch that got here.
    repin_ref_args=()
    if [[ "$repin_reason" != "content" ]]; then
        repin_ref_args=(--ref "$old_ref")
    fi

    # EXACTLY the registries whose own scoped question returned FAILED, never
    # all of `source_registries`. This is the line the gate above exists to
    # protect: passing every source here is what turns a primary-only drift
    # into a fleet-wide advance of every federated pin. The empty ref means
    # "that source's HEAD", resolved by the generator before it is written, so
    # a literal `HEAD` can never reach a lock.
    repin_source_args=()
    for reg in ${fed_drifted_regs[@]+"${fed_drifted_regs[@]}"}; do
        repin_source_args+=(--repin-source "$reg@")
    done

    # TWO AXES, and every artifact states both. `$repin_reason` answers what
    # the PRIMARY half needed — `content`, `format`, or `federated` meaning
    # the primary needed nothing — and whether `fed_drifted_regs` is empty
    # answers whether a source's pin moves as well. They are INDEPENDENT: a
    # shape repair and a source advance can ride in one re-pin, and so can a
    # content advance and a source advance. Both are derived into the claim
    # state in lib/bump-pr-claims.sh, which is where every sentence that turns
    # on them now lives.
    #
    # The precedence is deliberate and it is only a precedence over the NAME.
    # `format` beats `federated` because a lock whose stored digests are
    # malformed is malformed whatever else is true of it, and that is what the
    # reader has to be told first. What the precedence must NOT do is silence
    # the other axis — the first cut branched the title, the header, the
    # why-paragraph, the digest sentence and the commit message on
    # `$repin_reason` ALONE, so a format+federated re-pin went out titled
    # "(pin unchanged)" and bodied "the digest SHAPE ... and nothing else"
    # over a diff that advanced a source ref and changed one of its digests.
    # Measured by a verifier on this suite's own fed-current fixture with its
    # labels stripped.

    # WHAT THIS RUN ADDED to the `--check-format` remediation line the body
    # quotes, rendered for the body to quote back. Built from the SAME array
    # the invocation below uses, so it cannot describe flags that were not
    # passed.
    #
    # It is named as an ADDITION rather than presented as the whole command,
    # and that is the correction of a correction. The remediation line carries
    # no `--repin-source`, so on a run that advanced a source it is only half
    # of what ran; the first fix said "the whole of it was <command>" and
    # printed a command with no `--repo`, no `--source-repo` and no `-o`,
    # which would not run at all — a weaker claim than the quoted remediation
    # line above it, which does carry those placeholders.
    repin_source_flags_shown=""
    for reg in ${fed_drifted_regs[@]+"${fed_drifted_regs[@]}"}; do
        repin_source_flags_shown="${repin_source_flags_shown}${repin_source_flags_shown:+ }--repin-source '$reg@'"
    done

    # AND THE OTHER ADDITION, which naming only the first left out. The
    # generator finds a source's clone by looking beside `--repo` at the
    # sibling `../<repo-name>`, and `--source-repo` is what overrides that
    # lookup; a source whose clone is anywhere else stops the run at
    # "<registry>: no checkout at <path>". This script cannot rely on the
    # sibling layout at all — its clones are wherever `BUMP_CHECKOUTS` put
    # them — so it passes one `--source-repo` per source, every run, at the
    # invocation below. A body naming only the `--repin-source` flags is
    # therefore not false but incomplete, in the one way that costs the
    # reader an afternoon: append what it names to the quoted remediation
    # line and the command can stop before it writes anything.
    #
    # Derived from `source_repo_args` — the array the invocation below
    # actually passes — rather than rebuilt from `source_registries` beside
    # it, so it cannot come to describe flags this run did not pass. That is
    # the same rule the sibling above states for `fed_drifted_regs`.
    #
    # The path is replaced with a placeholder: `source_repo_args` carries this
    # machine's checkout directories, and a PR body carries no path from the
    # machine that ran the bump. The quoted remediation line uses the same
    # device for `--repo`.
    repin_source_repo_shown=""
    for arg in ${source_repo_args[@]+"${source_repo_args[@]}"}; do
        [[ "$arg" == "--source-repo" ]] && continue
        repin_source_repo_shown="${repin_source_repo_shown}${repin_source_repo_shown:+ }--source-repo '${arg%%=*}=<a clone of it>'"
    done

    # A NEWLY REACHABLE FAILURE, named here rather than left to be met on a
    # red night. Advancing a source's pin re-derives that source's digests at
    # a ref that has MOVED, so a bundle renamed or emptied there, or a skill
    # basename that now collides across registries, lands on this path (or on
    # the shrink refusal below) where before it could not: a federated source
    # was only ever re-digested at its own unchanged pin. The policy is the
    # one already written into both refusals — count the failure, leave the
    # lock alone, and let the run go red — because a cross-registry collision
    # is an adjudication between two registries and neither this script nor a
    # retry can make it. See docs/decisions/0009.
    if ! repin_out=$(python3 "$GENERATOR" --repin \
        --repo "$primary_dir" \
        ${repin_ref_args[@]+"${repin_ref_args[@]}"} \
        ${repin_source_args[@]+"${repin_source_args[@]}"} \
        ${source_repo_args[@]+"${source_repo_args[@]}"} \
        -o "$LOCK_REL_PATH" 2>&1); then
        fail "$repo_name: --repin failed — $(generator_error_line "$repin_out")"
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

    # ── Every sentence this PR will carry, built once ──────────────────
    # Title, commit message and body together, from lib/bump-pr-claims.sh,
    # because they state the same two facts and the way that fails is one of
    # them being left behind: the first cut branched the body's header and not
    # its title, and a PR list shows the title alone.
    #
    # A CONSTRUCTION ERROR IS A PER-REPO FAILURE. `compose_bump_artifacts`
    # returns non-zero when a sentence reached an artifact that this run's
    # state does not establish — an unregistered claim, an ungated one, or one
    # whose condition is false. That is not a cosmetic complaint: the whole
    # value of these bodies is that a reviewer can check every line, and one
    # that asserts a question this run never put is read and believed. So it
    # fails closed, exactly like a refused shrink: the lock is left alone, no
    # branch is pushed, no pull request is opened, and the scheduled run goes
    # red where a human sees it.
    if ! compose_bump_artifacts; then
        fail "$repo_name: refusing to open a pull request — $CLAIM_ERRORS. This is a bug in scripts/lib/bump-pr-claims.sh, not in this consumer's lock."
        ((FAIL_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi

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
        # Before refusing, ask whether there is anything left to protect.
        #
        # The refusal below is about UNKNOWN content — work someone pushed
        # that a force-push would destroy. A branch whose every commit is
        # already in the default branch is not that: it is the leftover of a
        # bump PR that merged and was never cleaned up, and it carries
        # nothing. Deleting it there is provably lossless, and it is the
        # difference between a repo that recovers on its own and one that is
        # stuck until a human notices — which, measured 2026-08-25, took four
        # days across five repos precisely because nothing ever went red.
        #
        # Gated on `branch_adds_nothing_to_base`, which returns false on any
        # unreadable answer, so the refusal still stands whenever the question
        # cannot be settled.
        if branch_adds_nothing_to_base "$repo_name" "$BRANCH_NAME" \
           && delete_bump_branch "$repo_name" "$BRANCH_NAME"; then
            log "$BRANCH_NAME was a merged leftover and carried nothing the default branch lacks — deleted it, and proposing the re-pin now."
            branch_exists=false
            git fetch --prune origin >/dev/null 2>&1 || true
        else
            log "WARN: $BRANCH_NAME already exists with different content — refusing to force-push. Merge or close its PR to free the branch, then re-run."
            ((SKIP_COUNT++)) || true
            cd "$REPO_ROOT"; continue
        fi
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
        # this keeps them equal by construction rather than by that guard —
        # and the composer reads $primary_registry for the same reason.
        #
        # Branched on the reason for the same cause as the PR body, and it is
        # the same defect one artifact further in: this message used to say
        # "the bundle content this lock installs has moved since <old>"
        # unconditionally, which on a format re-pin is false in a diff that
        # disproves it — and, now that the pin is held, would name the same
        # commit in its subject and in its "since" clause. Both halves are
        # claims in lib/bump-pr-claims.sh now, so neither branch can forget
        # the other axis.
        git commit -m "$COMMIT_SUBJECT

$COMMIT_BODY" >/dev/null || {
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

    existing_pr=$(gh pr list --head "$BRANCH_NAME" --json number \
        --jq '.[0].number // empty' 2>/dev/null || true)

    if [[ -n "$existing_pr" ]]; then
        log "PR #$existing_pr is already open for this branch — leaving it alone."
        ((SKIP_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi

    # What the PR discloses is what a reviewer cannot see in a one-line diff:
    # which registry moved, that the digests are re-derived from a pinned
    # commit rather than from anyone's working tree, what happened to each
    # federated pin, and that nothing here was hand-written.
    #
    # THE BODY IS BUILT NOWHERE ELSE. Every sentence of it, and of the title
    # and the commit message above, is a claim in lib/bump-pr-claims.sh with
    # the run state that makes it true written beside it — because the way
    # this body has failed, five times now, is always the same: a sentence
    # written for one shape of run left unbranched when a second shape became
    # reachable. Composing it here, inline, is what made each of those a thing
    # a reviewer had to notice. See that file's header for the list.
    #
    # Every quoted verdict in it is a SCOPED one — the primary-scoped copy for
    # a content re-pin, `--check-current --only <registry>` for a federated
    # one — so every difference line belongs to the pin named beside it. The
    # `-o` path is rewritten back to the consumer's own `skills.lock`: the
    # generator names the file it was given, which here is a copy under this
    # run's mktemp directory, and a /tmp path nobody can resolve is the one
    # line of an otherwise checkable body that a reviewer has to take on
    # faith. BOTH copies are substituted, because the two scoped questions are
    # asked against two different temp files.
    # Output captured rather than discarded: gh prints the new PR's URL, and
    # the auto-merge attempt below needs something to name.
    if pr_create_out=$(gh pr create \
        --head "$BRANCH_NAME" \
        --title "$PR_TITLE" \
        --body "$PR_BODY"); then
        log "PR created."
        ((OK_COUNT++)) || true

        # Ask for native auto-merge as well, and do not care whether it takes.
        # On this fleet it will not: the default-branch rulesets set
        # `required_status_checks: []`, and with nothing to hold the merge FOR,
        # GitHub refuses to arm auto-merge at all — it errors that the PR is
        # already in a clean, mergeable state (measured; see the header of
        # .github/workflows/dependabot-auto-merge.yml, which keeps its own
        # `--auto` attempt for exactly this reason). Note what that refusal is
        # NOT: it does not merge the PR here and now, seconds after opening it
        # and before any check could start.
        #
        # It costs one API call and starts working for free the day any repo
        # here grows a required check — on that repo, and only that repo, the
        # PR then lands the moment its checks pass instead of waiting for
        # tomorrow's sweep. A failure to arm is neither a run failure nor a
        # per-repo failure: the sweep is what actually lands these.
        pr_url=$(tail -1 <<< "$pr_create_out")
        if auto_out=$(gh pr merge --auto --merge --repo "$repo_name" "$pr_url" 2>&1); then
            log "native auto-merge armed — it lands when the checks it waits on pass."
        else
            log "native auto-merge did not arm (expected where the ruleset requires no checks) — tomorrow's sweep merges this PR instead: $(head -1 <<< "$auto_out")"
        fi
    else
        fail "PR creation failed for $repo_name"
        ((FAIL_COUNT++)) || true
    fi

    cd "$REPO_ROOT"
done

done

echo ""
echo "=== Lock bump complete: $MERGE_COUNT merged, $OK_COUNT proposed, $SKIP_COUNT skipped, $FAIL_COUNT failed ==="

if [[ $FAIL_COUNT -gt 0 ]]; then
    exit 1
fi
