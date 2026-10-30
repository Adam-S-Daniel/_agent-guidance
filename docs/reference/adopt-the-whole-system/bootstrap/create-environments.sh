#!/usr/bin/env bash
# create-environments.sh [--config fleet.env] [--dry-run]
#
# Creates the three GitHub Actions environments the exported workflows name,
# protected the way the owner's are (ENVIRONMENTS in lib/common.sh):
#
#   repo-settings  fleet-apply            REVIEWER must approve; deploys from
#                                         the default branch only. Holds the
#                                         settings-admin App's key.
#   acsi-agentskills plugin-runtime-review  REVIEWER must approve; no branch
#                                         policy (it runs on pull_request_target).
#                                         prevent_self_review is off: agents
#                                         open pull requests as you (ADR 0016).
#   skills-evals   routine-fire           no reviewer; deploys from the default
#                                         branch only. Holds the eval routine's
#                                         bearer and trigger id.
#
# Then sets the repository variables REPO_VARIABLES lists: repo-settings'
# GUIDANCE_SYNC_APP_ID and ROSTER_WRITER_APP_ID, the App ids fleet.yml names
# as ruleset bypass actors (export/DECISIONS.md C12), and skills-evals' four
# ANTHROPIC_* federation ids eval.yml's token exchange reads. A key that is
# still empty in fleet.env is left alone, so run this again after
# create-apps.sh, and after filling in the federation ids.
#
# PLAN=free: environments exist only in public repos, so fleet-apply (in the
# private repo-settings) is skipped, and the script says what to do instead.
#
# Idempotent: reads each environment and its branch policies first and writes
# only what differs; a second run says "Nothing to change". Extra branch
# policies someone added are reported, never deleted. Run create-repos.sh first.
# Needs: gh, jq.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

parse_common_args "$@"
for a in "${REST_ARGS[@]}"; do die "unknown argument: $a"; done
require_tools jq "$GH"
load_config "$CONFIG"
O=$CFG_OWNER
PROBLEMS=0
REVIEWER_ID=""

reviewer_id() {
  if [ -z "$REVIEWER_ID" ]; then
    gh_read_or_die "users/$CFG_REVIEWER" "the reviewer account $CFG_REVIEWER"
    REVIEWER_ID=$(printf '%s' "$GH_OUT" | jq -r '.id')
    [[ $REVIEWER_ID =~ ^[0-9]+$ ]] || die "the reviewer account $CFG_REVIEWER has no numeric id"
  fi
  printf '%s' "$REVIEWER_ID"
}

# env_body <reviewer owner|none> <branches default|any>
env_body() {
  local policy='{"protected_branches":false,"custom_branch_policies":true}'
  [ "$2" = default ] || policy=null
  if [ "$1" = owner ]; then
    jq -nc --argjson id "$(reviewer_id)" --argjson p "$policy" \
      '{deployment_branch_policy: $p, prevent_self_review: false, reviewers: [{type: "User", id: $id}]}'
  else
    jq -nc --argjson p "$policy" '{deployment_branch_policy: $p}'
  fi
}

ensure_env() { # repo env reviewer branches
  local r=$1 e=$2 rev=$3 br=$4 default cur fresh=0 names want_rev have_rev ok
  if ! env_available "$r"; then
    log "skip: $O/$r/$e: $r is private and PLAN=free, where environments exist only in public repos."
    [ "$e" != fleet-apply ] || log "      Without it, apply fleet.yml from your machine with apply-settings.sh; repo-settings.yml's apply job cannot run."
    return 0
  fi
  if gh_read "repos/$O/$r"; then
    default=$(printf '%s' "$GH_OUT" | jq -r '.default_branch // "main"')
  elif [ "$GH_HTTP" = 404 ] && [ "$DRY_RUN" = 1 ]; then
    default=main
    log "note: $O/$r does not exist yet; showing the calls for a new repo (default branch main)"
  else
    die "could not read $O/$r (HTTP $GH_HTTP); run create-repos.sh first"
  fi
  if gh_read "repos/$O/$r/environments/$e"; then
    cur=$GH_OUT
  else
    [ "$GH_HTTP" = 404 ] || die "could not read environment $e in $O/$r (HTTP $GH_HTTP)"
    cur='{}'; fresh=1
  fi
  want_rev=""; [ "$rev" = owner ] && want_rev=$(reviewer_id)
  have_rev=$(printf '%s' "$cur" | jq -r '[.protection_rules[]? | select(.type == "required_reviewers") | .reviewers[]? | .reviewer.id] | map(tostring) | sort | join(",")')
  if [ "$br" = default ]; then
    ok=$(printf '%s' "$cur" | jq -r '"\(.deployment_branch_policy.protected_branches)/\(.deployment_branch_policy.custom_branch_policies)"')
    [ "$ok" = false/true ] && ok=1 || ok=0
  else
    ok=$(printf '%s' "$cur" | jq -r 'if .deployment_branch_policy == null then 1 else 0 end')
  fi
  if [ "$fresh" = 0 ] && [ "$ok" = 1 ] && [ "$have_rev" = "$want_rev" ]; then
    skip "$O/$r environment $e"
  else
    gh_write_api PUT "repos/$O/$r/environments/$e" "$(env_body "$rev" "$br")"
    did "$([ "$fresh" = 1 ] && echo created || echo updated) environment $e in $O/$r$([ "$rev" = owner ] && echo " (required reviewer: $CFG_REVIEWER)")"
  fi
  [ "$br" = default ] || return 0

  names=""
  if [ "$fresh" = 0 ] || [ "$DRY_RUN" = 0 ]; then
    if gh_read "repos/$O/$r/environments/$e/deployment-branch-policies"; then
      names=$(printf '%s' "$GH_OUT" | jq -r '.branch_policies[]? | "\(.type // "branch")/\(.name)"')
    elif [ "$DRY_RUN" = 0 ]; then
      die "could not read branch policies of $e in $O/$r (HTTP $GH_HTTP)"
    fi
  fi
  if grep -qxF -- "branch/$default" <<<"$names"; then
    skip "$O/$r environment $e deploys only from $default"
  else
    gh_write_api POST "repos/$O/$r/environments/$e/deployment-branch-policies" "$(jq -nc --arg n "$default" '{name: $n, type: "branch"}')"
    did "limited environment $e in $O/$r to branch $default"
  fi
  local extra
  extra=$(grep -vxF -- "branch/$default" <<<"$names" | grep -v '^$' || true)
  if [ -n "$extra" ]; then
    warn "$O/$r environment $e also allows: $(tr '\n' ' ' <<<"$extra")- remove them in Settings, Environments, $e"
    PROBLEMS=$((PROBLEMS + 1))
  fi
}

log "Owner $O, plan $CFG_PLAN$([ "$DRY_RUN" = 1 ] && echo ', DRY RUN')"
for entry in "${ENVIRONMENTS[@]}"; do
  IFS='|' read -r repo env rev br <<<"$entry"
  ensure_env "$repo" "$env" "$rev" "$br"
done
declare -A HAVE_VARS
ensure_repo_variable() { # repo name source group
  local r=$1 name=$2 src=$3 group=$4 want hint="run create-apps.sh, then this again"
  [ "$group" = federation ] && hint="optional; docs/evals-and-loop.md#workload-identity-federation"
  want=$(repo_variable_value "$src")
  if [ -z "$want" ]; then
    log "skip: $O/$r variable $name: ${src#cfg:} is empty in fleet.env ($hint)"; return 0
  fi
  if [ -z "${HAVE_VARS[$r]+set}" ]; then
    if repo_variables "$O/$r"; then HAVE_VARS[$r]=$REPO_VARS_OUT
    elif [ "$GH_HTTP" = 404 ] && [ "$DRY_RUN" = 1 ]; then HAVE_VARS[$r]=""
    else die "could not list the variables of $O/$r (HTTP $GH_HTTP); run create-repos.sh first"; fi
  fi
  if grep -qxF -- "$name"$'\t'"$want" <<<"${HAVE_VARS[$r]}"; then
    skip "$O/$r variable $name"; return 0
  fi
  set_value variable "$O/$r" - "$name" < <(printf '%s' "$want")
  did "set variable $name in $O/$r"
}
for entry in "${REPO_VARIABLES[@]}"; do
  IFS='|' read -r repo name src group <<<"$entry"
  ensure_repo_variable "$repo" "$name" "$src" "$group"
done
finish
[ "$PROBLEMS" = 0 ] || { warn "$PROBLEMS environment(s) need a manual fix"; exit 1; }
