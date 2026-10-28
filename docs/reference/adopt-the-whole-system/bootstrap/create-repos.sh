#!/usr/bin/env bash
# create-repos.sh [--config fleet.env] [--dry-run] [<repo>...]
#
# Creates the five fleet repos the guide names (section 1) and sets what
# phase 1 asks for on each one. Default: all five.
#
#   * Visibility (guide section 2): agent-skills-private and repo-settings are
#     always private. On PLAN=free every other repo is public; on PLAN=paid,
#     agent-guidance and skills-evals follow VISIBILITY_* in fleet.env
#     (default public). agent-skills is always public.
#   * A first commit (a README), so no ruleset meets an empty repo (3.4). The
#     README is generated_readme (lib/common.sh), whose last line is a fixed
#     marker: copy-starter.sh replaces exactly that file without --force.
#   * Merge settings (3.1): merge commit only, auto-merge on, delete the
#     branch on merge.
#   * Actions (3.3): approval for fork pull request runs from all outside
#     collaborators (public repos; GitHub answers 422 on private ones), the
#     default workflow token read-only and unable to approve pull requests,
#     and SHA pinning required (SHA_PINNING_REQUIRED, default true).
#   * Labels (11): agent-ready, on-hold, agent-authored, and vendor-release and
#     agent-check, which routine specs use (LABELS in lib/common.sh).
#
# Idempotent: it reads first and writes only what differs; a second run says
# "Nothing to change". An existing repo with the wrong visibility is reported,
# never changed. --dry-run makes the same reads and prints the writes instead.
# Needs: gh (authenticated as the owner), jq.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

parse_common_args "$@"
require_tools jq "$GH"
load_config "$CONFIG"
O=$CFG_OWNER
REPOS=("${REST_ARGS[@]}")
[ ${#REPOS[@]} -gt 0 ] || REPOS=("${FLEET_REPOS[@]}")
for r in "${REPOS[@]}"; do is_known_repo "$r" || die "unknown repo: $r (expected one of: ${FLEET_REPOS[*]})"; done

PROBLEMS=0

ensure_repo() { # name -> sets FRESH=1 when the repo does not exist yet (dry run)
  local r=$1 want have
  want=$(repo_visibility "$r")
  FRESH=0
  if gh_read "repos/$O/$r"; then
    have=$(printf '%s' "$GH_OUT" | jq -r 'if .visibility then .visibility elif .private then "private" else "public" end')
    if [ "$have" = "$want" ]; then
      skip "$O/$r exists ($have)"
    else
      warn "$O/$r exists but is $have; the guide wants it $want. Not changed: fix it in Settings, General, Danger zone."
      PROBLEMS=$((PROBLEMS + 1))
    fi
    ensure_first_commit "$r"
    return 0
  fi
  [ "$GH_HTTP" = 404 ] || die "could not read $O/$r (HTTP $GH_HTTP)"
  run_write "$GH" repo create "$O/$r" "--$want" --description "$(description_for "$r")"
  did "created $O/$r ($want)"
  if [ "$DRY_RUN" = 1 ]; then
    FRESH=1
    gh_write_api PUT "repos/$O/$r/contents/README.md" "$(readme_body "$r")"
    did "added the generated README to $O/$r"
    return 0
  fi
  ensure_first_commit "$r"
  return 0
}

readme_body() { # name: the contents API body for the generated README
  jq -n --arg c "$(generated_readme "$1" | base64 | tr -d '\n')" \
    '{message: "Add a README so the repo has a first commit", content: $c}'
}

ensure_first_commit() { # name
  local r=$1 body
  if gh_read "repos/$O/$r/commits?per_page=1"; then
    skip "$O/$r has a first commit"; return 0
  fi
  [ "$GH_HTTP" = 409 ] || die "could not read the commits of $O/$r (HTTP $GH_HTTP)"
  gh_write_api PUT "repos/$O/$r/contents/README.md" "$(readme_body "$r")"
  did "added a README to the empty repo $O/$r"
}

ensure_merge_settings() { # name
  local r=$1 want cur
  want='{"allow_merge_commit":true,"allow_squash_merge":false,"allow_rebase_merge":false,"allow_auto_merge":true,"delete_branch_on_merge":true}'
  if [ "$FRESH" = 0 ]; then
    gh_read_or_die "repos/$O/$r" "$O/$r"
    cur=$(printf '%s' "$GH_OUT" | jq -c '{allow_merge_commit, allow_squash_merge, allow_rebase_merge, allow_auto_merge, delete_branch_on_merge}')
    if [ "$(jq -cS . <<<"$cur")" = "$(jq -cS . <<<"$want")" ]; then
      skip "$O/$r merge settings"; return 0
    fi
  fi
  gh_write_api PATCH "repos/$O/$r" "$want"
  did "set merge settings on $O/$r (merge commit only, auto-merge, delete branch on merge)"
}

ensure_actions() { # name
  local r=$1 vis cur body
  vis=$(repo_visibility "$r")
  # SHA pinning (3.3). enabled and allowed_actions are required in the PUT;
  # keep whatever is set.
  if [ "$CFG_SHA_PINNING_REQUIRED" = true ]; then
    cur='{}'
    if [ "$FRESH" = 0 ]; then gh_read_or_die "repos/$O/$r/actions/permissions" "Actions permissions of $O/$r"; cur=$GH_OUT; fi
    if [ "$(printf '%s' "$cur" | jq -r '.sha_pinning_required // false')" = true ]; then
      skip "$O/$r requires SHA-pinned actions"
    else
      body=$(printf '%s' "$cur" | jq -c '{enabled: (.enabled // true), allowed_actions: (.allowed_actions // "all"), sha_pinning_required: true}')
      gh_write_api PUT "repos/$O/$r/actions/permissions" "$body"
      did "required SHA-pinned actions on $O/$r"
    fi
  fi
  # Default workflow token: read-only, cannot approve pull requests.
  cur='{}'
  if [ "$FRESH" = 0 ]; then gh_read_or_die "repos/$O/$r/actions/permissions/workflow" "workflow token settings of $O/$r"; cur=$GH_OUT; fi
  if [ "$(printf '%s' "$cur" | jq -r '"\(.default_workflow_permissions)/\(.can_approve_pull_request_reviews)"')" = "read/false" ]; then
    skip "$O/$r default workflow token is read-only"
  else
    gh_write_api PUT "repos/$O/$r/actions/permissions/workflow" \
      '{"default_workflow_permissions":"read","can_approve_pull_request_reviews":false}'
    did "made the default workflow token read-only on $O/$r"
  fi
  # Fork pull request approval: all outside collaborators. Public repos only.
  if [ "$vis" = public ]; then
    cur='{}'
    if [ "$FRESH" = 0 ]; then gh_read_or_die "repos/$O/$r/actions/permissions/fork-pr-contributor-approval" "fork approval of $O/$r"; cur=$GH_OUT; fi
    if [ "$(printf '%s' "$cur" | jq -r '.approval_policy // ""')" = all_external_contributors ]; then
      skip "$O/$r requires approval for all outside collaborators' fork runs"
    else
      gh_write_api PUT "repos/$O/$r/actions/permissions/fork-pr-contributor-approval" \
        '{"approval_policy":"all_external_contributors"}'
      did "required approval for all outside collaborators' fork runs on $O/$r"
    fi
  else
    skip "$O/$r is private: fork run approval does not apply (forks of a private repo need access)"
  fi
}

ensure_labels() { # name
  local r=$1 names="" entry name color desc body
  if [ "$FRESH" = 0 ]; then
    gh_read_or_die "repos/$O/$r/labels?per_page=100" "labels of $O/$r"
    names=$(printf '%s' "$GH_OUT" | jq -r '.[].name')
  fi
  for entry in "${LABELS[@]}"; do
    IFS='|' read -r name color desc <<<"$entry"
    if grep -qxF -- "$name" <<<"$names"; then
      skip "$O/$r has label $name"
    else
      body=$(jq -n --arg n "$name" --arg c "$color" --arg d "$desc" '{name: $n, color: $c, description: $d}')
      gh_write_api POST "repos/$O/$r/labels" "$body"
      did "created label $name in $O/$r"
    fi
  done
}

log "Owner $O, plan $CFG_PLAN$([ "$DRY_RUN" = 1 ] && echo ', DRY RUN')"
for r in "${REPOS[@]}"; do
  log "== $O/$r"
  ensure_repo "$r"
  ensure_merge_settings "$r"
  ensure_actions "$r"
  ensure_labels "$r"
done
finish
if [ "$PROBLEMS" -gt 0 ]; then
  warn "$PROBLEMS repo(s) need a manual fix (see above)"; exit 1
fi
