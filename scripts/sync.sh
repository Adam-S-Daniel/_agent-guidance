#!/usr/bin/env bash
set -euo pipefail
#
# sync.sh — Sync the managed AGENTS.md to every repo in the organization.
#
# Discovers repos dynamically via `gh repo list`. For each repo the script:
#   1. Reads the repo's .agents-sync.yml (sections to include)
#   2. Builds the managed portion via build-agents-md.sh
#   3. Preserves any content below "## Repo-specific additions"
#   4. Opens (or updates) a PR if the managed content has changed
#
# Requirements: gh (GitHub CLI, authenticated), yq, git
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
MARKER="## Repo-specific additions"
BRANCH_NAME="agents-md-sync/update"
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

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

trap 'rm -rf "$WORK_DIR"' EXIT

# ── Helpers ────────────────────────────────────────────────────────────────

log()  { echo "  $*"; }
fail() { echo "  ERROR: $*"; }

FAIL_COUNT=0
OK_COUNT=0
SKIP_COUNT=0

read_sections_from_yaml() {
    yq -r '.sections // [] | .[]' 2>/dev/null || true
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

echo "Scanning repos for: $ORG (excluding $SELF_REPO)"
echo ""

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

    # On HTTP errors (e.g. 404 when the file is absent) gh api prints the raw
    # error JSON body to stdout — the --jq filter is not applied — so `|| true`
    # alone would leave garbage in remote_yaml, break the base64 decode, and
    # silently defeat the default_sections fallback. Discard output on failure.
    if ! remote_yaml=$(gh api "repos/$repo_name/contents/.agents-sync.yml" \
        --jq '.content' 2>/dev/null); then
        remote_yaml=""
    fi

    if [[ -n "$remote_yaml" ]]; then
        while IFS= read -r s; do
            [[ -n "$s" ]] && sections+=("$s")
        done < <(echo "$remote_yaml" | base64 -d | read_sections_from_yaml)
    else
        sections=("${DEFAULT_SECTIONS[@]}")
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
    git config user.email "agents-md-sync[bot]@users.noreply.github.com"

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
        if grep -qF "$MARKER" AGENTS.md; then
            repo_specific=$(sed -n "/^${MARKER}/,\$p" AGENTS.md)
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
    # is already in place — a repo can be AGENTS.md-current but still
    # missing CLAUDE.md (e.g. it predates the bridge), and that case must
    # still get a commit that adds just the bridge file.

    agents_up_to_date=false
    if [[ -f AGENTS.md ]] && diff -q <(echo "$new_agents_md") AGENTS.md &>/dev/null; then
        agents_up_to_date=true
    fi

    claude_md_present=false
    [[ -f CLAUDE.md ]] && claude_md_present=true

    if $agents_up_to_date && $claude_md_present; then
        log "Up to date — skipping."
        ((SKIP_COUNT++)) || true
        cd "$REPO_ROOT"
        continue
    fi

    if $DRY_RUN; then
        if $agents_up_to_date; then
            log "[DRY RUN] AGENTS.md up to date; would add missing CLAUDE.md bridge"
        else
            log "[DRY RUN] Would update AGENTS.md"
        fi
        ((SKIP_COUNT++)) || true
        cd "$REPO_ROOT"
        continue
    fi

    # ── Branch, commit, push ───────────────────────────────────────────

    git checkout -b "$BRANCH_NAME" 2>/dev/null || git checkout "$BRANCH_NAME" 2>/dev/null || {
        fail "could not create branch in $repo_name"
        ((FAIL_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    }

    echo "$new_agents_md" > AGENTS.md

    # ── CLAUDE.md bridge ────────────────────────────────────────────────
    # Claude Code reads CLAUDE.md, not AGENTS.md — it never sees the managed
    # guidance unless something imports it. Anthropic's documented pattern
    # is a CLAUDE.md containing `@AGENTS.md`. We only ever CREATE this file;
    # an existing CLAUDE.md is left completely untouched even if it doesn't
    # import AGENTS.md, since we must not clobber someone's hand-written file.
    claude_md_added=false
    if ! $claude_md_present; then
        cat > CLAUDE.md <<'CLAUDEEOF'
<!-- Managed by _agent-guidance: bridges Claude Code (which reads CLAUDE.md) to AGENTS.md. -->
@AGENTS.md
CLAUDEEOF
        claude_md_added=true
    elif ! grep -qF '@AGENTS.md' CLAUDE.md; then
        log "WARN: CLAUDE.md exists but does not import @AGENTS.md — Claude Code will not see the managed guidance."
    fi

    if $claude_md_added; then
        git add AGENTS.md CLAUDE.md
    else
        git add AGENTS.md
    fi

    if $agents_up_to_date; then
        commit_message="chore: add CLAUDE.md bridge for AGENTS.md sync

AGENTS.md was already up to date. Adds a CLAUDE.md that imports
@AGENTS.md so Claude Code (which reads CLAUDE.md, not AGENTS.md) sees
the managed guidance."
    else
        commit_message="chore: sync AGENTS.md from _agent-guidance

Sections: ${sections[*]:-none}
Managed content updated by the central _agent-guidance repository."
    fi

    git commit -m "$commit_message" || {
        log "Nothing to commit."
        ((SKIP_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    }

    if ! git push -u origin "$BRANCH_NAME"; then
        fail "push failed for $repo_name"
        ((FAIL_COUNT++)) || true
        cd "$REPO_ROOT"; continue
    fi

    # ── Open or update PR ──────────────────────────────────────────────

    existing_pr=$(gh pr list --head "$BRANCH_NAME" --json number \
        --jq '.[0].number // empty' 2>/dev/null || true)

    if [[ -n "$existing_pr" ]]; then
        log "PR #$existing_pr already exists — branch updated."
        ((OK_COUNT++)) || true
    else
        # Guarded assignment: a bare command substitution would abort the
        # entire run under set -e on a transient API error, breaking the
        # per-repo fail isolation used by the other steps. The -z check
        # also catches an empty-but-exit-0 response.
        if ! default_branch=$(gh repo view "$repo_name" --json defaultBranchRef \
            --jq .defaultBranchRef.name) || [[ -z "$default_branch" ]]; then
            fail "could not resolve default branch for $repo_name"
            ((FAIL_COUNT++)) || true
            cd "$REPO_ROOT"; continue
        fi

        # --head: gh cannot infer the head branch in a fresh temp clone.
        # --base: the default branch varies across repos (main vs master),
        #         and omitting --base while passing --head can mistarget.
        if gh pr create \
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
existing CLAUDE.md is never modified.
EOF
)"; then
            log "PR created."
            ((OK_COUNT++)) || true
        else
            fail "PR creation failed for $repo_name"
            ((FAIL_COUNT++)) || true
        fi
    fi

    cd "$REPO_ROOT"
done

done

echo ""
echo "=== Sync complete: $OK_COUNT synced, $SKIP_COUNT skipped, $FAIL_COUNT failed ==="

if [[ $FAIL_COUNT -gt 0 ]]; then
    exit 1
fi
