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
#   5. Pushes the update directly to the default branch (the sync App has a
#      ruleset bypass, declared in repo-settings); falls back to a PR with
#      auto-merge for repos whose protection rejects the push
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
BRIDGE_SCRIPT="$SCRIPT_DIR/bridge-status.sh"
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

    if $agents_up_to_date && $claude_md_present && ! $needs_claude_fix; then
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
        if $agents_up_to_date && $needs_claude_fix; then
            log "[DRY RUN] AGENTS.md up to date; would rewrite CLAUDE.md to the standard @AGENTS.md bridge (fix_claude_md: true)"
        elif $agents_up_to_date; then
            log "[DRY RUN] AGENTS.md up to date; would add missing CLAUDE.md bridge"
        else
            log "[DRY RUN] Would update AGENTS.md (direct push to $default_branch; PR fallback if rejected)"
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

    if $claude_md_added || $claude_md_fixed; then
        git add AGENTS.md CLAUDE.md
    else
        git add AGENTS.md
    fi

    if $agents_up_to_date && $claude_md_fixed; then
        commit_message="chore: rewrite CLAUDE.md to the @AGENTS.md bridge

AGENTS.md was already up to date, but CLAUDE.md did not import it, so
Claude Code never saw the managed guidance. Rewritten to the standard
@AGENTS.md bridge per this repo's fix_claude_md: true opt-in."
    elif $agents_up_to_date; then
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

        # Force-push: a stale agents-md-sync/update branch from the old PR-era
        # (built on a since-superseded default branch) has diverged from this
        # run's HEAD, so a plain push is rejected (fetch first). Force is safe
        # here — the branch is bot-owned, this sync is its only writer, and it
        # is regenerated from the current default branch every run; force just
        # replaces a stale proposal. The default branch itself stays gated by
        # the repo's protection.
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

        # Enable auto-merge so the PR lands on its own once checks pass. Try
        # squash first, then a plain merge; a repo may disable one method.
        # A PR left open for manual merge is an acceptable degraded outcome,
        # so none of these count as a repo failure.
        if [[ -n "$pr_number" ]]; then
            if ! gh pr merge "$pr_number" --auto --squash 2>/dev/null \
                && ! gh pr merge "$pr_number" --auto --merge 2>/dev/null; then
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
