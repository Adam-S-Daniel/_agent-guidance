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

# ── Helpers ────────────────────────────────────────────────────────────────

strip_volatile() {
    grep -v '^<!-- Last synced:' || true
}

fetch_file_content() {
    local repo="$1" path="$2"
    local encoded
    # On HTTP errors gh api prints the raw error JSON body to stdout (the
    # --jq filter is not applied) — discard output on failure, don't decode it.
    encoded=$(gh api "repos/$repo/contents/$path" --jq '.content' 2>/dev/null) || encoded=""
    [[ -n "$encoded" ]] && echo "$encoded" | base64 -d 2>/dev/null || true
}

# ── Load central repos.yml (exclusions + default sections) ─────────────────

EXCLUDED_REPOS=()
DEFAULT_SECTIONS=()

if [[ -f "$REPOS_YML" ]]; then
    while IFS= read -r r; do
        [[ -n "$r" ]] && EXCLUDED_REPOS+=("$r")
    done < <(yq -r '.exclude // [] | .[]' "$REPOS_YML" 2>/dev/null || true)

    while IFS= read -r s; do
        [[ -n "$s" ]] && DEFAULT_SECTIONS+=("$s")
    done < <(yq -r '.default_sections // [] | .[]' "$REPOS_YML" 2>/dev/null || true)
fi

# ── skills-bootstrap delivery config (read-only mirror of sync.sh's) ───────

BOOTSTRAP_REPOS=()
BOOTSTRAP_REGISTRY=""
BOOTSTRAP_PATH=""
BOOTSTRAP_REF=""

if [[ -f "$REPOS_YML" ]]; then
    BOOTSTRAP_REGISTRY=$(yq -r '.skills_bootstrap.registry // ""' "$REPOS_YML" 2>/dev/null || echo "")
    BOOTSTRAP_PATH=$(yq -r '.skills_bootstrap.path // ""' "$REPOS_YML" 2>/dev/null || echo "")
    BOOTSTRAP_REF=$(yq -r '.skills_bootstrap.ref // ""' "$REPOS_YML" 2>/dev/null || echo "")
    while IFS= read -r r; do
        [[ -n "$r" ]] && BOOTSTRAP_REPOS+=("$r")
    done < <(yq -r '.skills_bootstrap.repos // [] | .[]' "$REPOS_YML" 2>/dev/null || true)
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
PINNED_HOOK=""
PINNED_HOOK_TRIED=false
pinned_hook() {
    if ! $PINNED_HOOK_TRIED; then
        PINNED_HOOK_TRIED=true
        [[ -n "$BOOTSTRAP_REGISTRY" && -n "$BOOTSTRAP_PATH" && -n "$BOOTSTRAP_REF" ]] || return 1
        PINNED_HOOK=$(fetch_file_content "$BOOTSTRAP_REGISTRY" "$BOOTSTRAP_PATH?ref=$BOOTSTRAP_REF")
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

bootstrap_blocked() {
    local repo_name="$1" root_ignore claude_ignore probe
    root_ignore=$(fetch_file_content "$repo_name" ".gitignore")
    claude_ignore=$(fetch_file_content "$repo_name" ".claude/.gitignore")
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
repo_list_raw=$(
    gh repo list "$ORG" \
        --no-archived \
        --source \
        --json nameWithOwner \
        --limit 1000 \
        --jq '.[].nameWithOwner'
)

mapfile -t REPOS < <(echo "$repo_list_raw" | grep -v "/${SELF_REPO}$" | sort)

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

    # ── Resolve sections from repo's .agents-sync.yml ──────────────────

    sections=()

    remote_yaml=$(fetch_file_content "$repo_name" ".agents-sync.yml")
    if [[ -n "$remote_yaml" ]]; then
        while IFS= read -r s; do
            [[ -n "$s" ]] && sections+=("$s")
        done < <(echo "$remote_yaml" | yq -r '.sections // [] | .[]' 2>/dev/null || true)
    else
        sections=("${DEFAULT_SECTIONS[@]}")
    fi

    sections_display="${sections[*]:-none}"

    # ── Fetch current AGENTS.md ────────────────────────────────────────

    current_agents=$(fetch_file_content "$repo_name" "AGENTS.md")

    if [[ -z "$current_agents" ]]; then
        status="**no-agents-md**"
        notes="AGENTS.md not found in repo"
    else
        # Check marker header
        if echo "$current_agents" | grep -qF "$MARKER"; then
            has_marker="yes"
        else
            has_marker="no"
        fi

        # Build expected managed content and compare
        expected=$("$BUILD_SCRIPT" "${sections[@]}" 2>/dev/null || true)

        if [[ -z "$expected" ]]; then
            status="**update-failed**"
            notes="Could not build expected content"
        else
            # Extract managed section from current file
            if [[ "$has_marker" == "yes" ]]; then
                current_managed=$(echo "$current_agents" | sed "/$MARKER/,\$d")
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

    # ── Check CLAUDE.md bridge status ───────────────────────────────────

    current_claude=$(fetch_file_content "$repo_name" "CLAUDE.md")

    if [[ -z "$current_claude" ]]; then
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
    current_hook=$(fetch_file_content "$repo_name" "$HOOK_REL_PATH")

    if bootstrap_allowlisted "$repo_name"; then
        current_lock=$(fetch_file_content "$repo_name" "$LOCK_REL_PATH")

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
            settings_probe=$(fetch_file_content "$repo_name" "$SETTINGS_REL_PATH")
            settings_state="missing"
            [[ -n "$settings_probe" ]] && \
                settings_state=$(echo "$settings_probe" | "$BOOTSTRAP_STATUS_SCRIPT" -)

            blocked=no
            [[ "$settings_state" != "unparseable" ]] && \
                { bootstrap_blocked "$repo_name" && blocked=yes || true; }

            if [[ "$settings_state" == "unparseable" ]]; then
                bootstrap_cell="**refused**"
                notes="$notes; \`settings.json\` unparseable"
            elif [[ "$blocked" == yes ]]; then
                bootstrap_cell="**blocked**"
                notes="$notes; \`.claude/\` gitignored"
            else
                bootstrap_cell="**missing**"
            fi
        else
            current_settings=$(fetch_file_content "$repo_name" "$SETTINGS_REL_PATH")
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
    elif [[ -n "$current_hook" ]]; then
        bootstrap_cell="**unmanaged**"
    fi

    # ── Check for open sync PR ─────────────────────────────────────────

    pr_number=$(gh pr list --repo "$repo_name" --head "$BRANCH_NAME" \
        --json number --jq '.[0].number' 2>/dev/null || true)

    if [[ -n "$pr_number" ]]; then
        open_pr="#$pr_number"
        [[ "$status" == "**drift-detected**" ]] && status="**pr-open**"
    fi

    # ── Write row ──────────────────────────────────────────────────────

    echo "| [\`$repo_name\`](https://github.com/$repo_name) | $status | $has_marker | $bridge_cell | $bootstrap_cell | $open_pr | $sections_display | $notes |" >> "$OUTPUT_FILE"
done

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
    echo ""
    echo "**CLAUDE.md bridge legend**"
    echo ""
    echo "| Bridge status | Meaning |"
    echo "|---------------|---------|"
    echo "| bridge-ok | CLAUDE.md imports \`@AGENTS.md\` (line-start, outside code fences) |"
    echo "| **no-import** | CLAUDE.md exists but never imports \`@AGENTS.md\` — Claude Code will not see the managed guidance |"
    echo "| missing | No CLAUDE.md yet — sync adds the bridge in its next PR |"
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
    echo ""
    echo "The **Notes** column carries each allowlisted repo's lock pins"
    echo "(\`registry@shortref\`, one per federated source). Nothing else in the"
    echo "fleet surfaces a stale lock: a lock pinned far behind still installs"
    echo "cleanly and still reports \`OK\` in-session, by design."
} >> "$OUTPUT_FILE"

echo ""
echo "Drift report written to $OUTPUT_FILE"
