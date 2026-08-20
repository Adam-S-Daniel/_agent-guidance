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

if [[ -f "$REPOS_YML" ]]; then
    BOOTSTRAP_REGISTRY=$(yq -r '.skills_bootstrap.registry // ""' "$REPOS_YML" 2>/dev/null || echo "")
    BOOTSTRAP_PATH=$(yq -r '.skills_bootstrap.path // ""' "$REPOS_YML" 2>/dev/null || echo "")
    BOOTSTRAP_REF=$(yq -r '.skills_bootstrap.ref // ""' "$REPOS_YML" 2>/dev/null || echo "")
    BOOTSTRAP_SHA256=$(yq -r '.skills_bootstrap.sha256 // ""' "$REPOS_YML" 2>/dev/null || echo "")

    while IFS= read -r r; do
        [[ -n "$r" ]] && BOOTSTRAP_REPOS+=("$r")
    done < <(yq -r '.skills_bootstrap.repos // [] | .[]' "$REPOS_YML" 2>/dev/null || true)
fi

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
