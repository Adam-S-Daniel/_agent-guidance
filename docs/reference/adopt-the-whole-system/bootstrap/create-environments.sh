#!/usr/bin/env bash
# create-environments.sh [--config fleet.env] [--dry-run] [--with-optional]
#
# Creates every GitHub Actions environment the guide names, in the repo whose
# workflow uses it, with deployment branches limited to the default branch: a
# custom branch policy named after the repo's default branch (guide 3.4).
#
#   agent-guidance  guidance-sync (4.5), privacy (6.5)
#   agent-skills    privacy (6.5), plugin-runtime-review (10.5 step 6: you are
#                   the required reviewer)
#   skills-evals    privacy (6.5), loop-writer (3.4), routine-fire (9.4),
#                   lessons-reader (10.4), roster-writer (--with-optional)
#   repo-settings   settings-admin (3.4)
#
# It also sets the repository variables the workflows read (REPO_VARIABLES
# in lib/common.sh): skills-evals' REGISTRY_REPO, GUIDANCE_REPO and
# LESSONS_REPO, and repo-settings' mirror of fleet.env (OWNER_TYPE, the
# loop-writer and guidance-sync App IDs, REQUIRED_CHECKS, the visibility
# keys). A key that is empty in fleet.env is left alone. Rerun this script
# after create-apps.sh and after apply-rulesets.sh records REQUIRED_CHECKS.
#
# The guide names no environment for lessons-reader, roster-writer or
# settings-admin, nor the owner-approval one; see README.md for the names used.
#
# PLAN=free: environments exist only in public repos, so the ones in private
# repos are skipped and the script prints what the guide says to do instead.
#
# Idempotent: reads each environment and its branch policies first and writes
# only what differs; a second run says "Nothing to change". Extra branch
# policies someone added are reported, never deleted. Run create-repos.sh first.
# Needs: gh, jq.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

WITH_OPTIONAL=0
parse_common_args "$@"
for a in "${REST_ARGS[@]}"; do
  case $a in
    --with-optional) WITH_OPTIONAL=1 ;;
    *) die "unknown argument: $a" ;;
  esac
done
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

ensure_env() { # repo env reviewer source
  local r=$1 e=$2 rev=$3 src=$4 default cur want_rev have_rev body fresh=0 names
  if ! env_available "$r"; then
    log "skip: $O/$r/$e: $r is private and PLAN=free, where environments exist only in public repos."
    case $e in
      settings-admin) log "      The guide (3.4): run repo-settings' apply script (apply-rulesets.sh here) from your own machine with gh instead of from a workflow; the settings-admin App is then not needed." ;;
      *) log "      The guide (3.4): keep this workflow's credential out of the private repo, or upgrade the plan." ;;
    esac
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
  [ "$src" = guide ] || log "note: the guide gives no name for $e; using '$e' ($src)"

  want_rev=""
  [ "$rev" = owner ] && want_rev=$(reviewer_id)

  if gh_read "repos/$O/$r/environments/$e"; then
    cur=$GH_OUT
  else
    [ "$GH_HTTP" = 404 ] || die "could not read environment $e in $O/$r (HTTP $GH_HTTP)"
    cur='{}'; fresh=1
  fi
  have_rev=$(printf '%s' "$cur" | jq -r '[.protection_rules[]? | select(.type == "required_reviewers") | .reviewers[]? | .reviewer.id] | map(tostring) | sort | join(",")')
  if [ "$fresh" = 0 ] \
     && [ "$(printf '%s' "$cur" | jq -r '"\(.deployment_branch_policy.protected_branches)/\(.deployment_branch_policy.custom_branch_policies)"')" = "false/true" ] \
     && { [ "$rev" != owner ] || [ "$have_rev" = "$want_rev" ]; }; then
    skip "$O/$r environment $e"
  else
    if [ "$rev" = owner ]; then
      body=$(jq -n --argjson id "$want_rev" '{deployment_branch_policy: {protected_branches: false, custom_branch_policies: true}, prevent_self_review: false, reviewers: [{type: "User", id: $id}]}')
    else
      body='{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}'
    fi
    gh_write_api PUT "repos/$O/$r/environments/$e" "$body"
    did "$([ "$fresh" = 1 ] && echo created || echo updated) environment $e in $O/$r$([ "$rev" = owner ] && echo " (required reviewer: $CFG_REVIEWER)")"
  fi

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
    body=$(jq -n --arg n "$default" '{name: $n, type: "branch"}')
    gh_write_api POST "repos/$O/$r/environments/$e/deployment-branch-policies" "$body"
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
  IFS='|' read -r repo env rev src optional <<<"$entry"
  if [ "$optional" = yes ] && [ "$WITH_OPTIONAL" = 0 ]; then
    log "skip: $O/$repo/$env is optional (pass --with-optional)"; continue
  fi
  ensure_env "$repo" "$env" "$rev" "$src"
done
declare -A HAVE_VARS
ensure_repo_variable() { # repo name source
  local r=$1 name=$2 src=$3 want
  want=$(repo_variable_value "$src")
  if [ -z "$want" ]; then
    log "skip: $O/$r variable $name: ${src#cfg:} is empty in fleet.env"; return 0
  fi
  if [ -z "${HAVE_VARS[$r]+set}" ]; then
    if repo_variables "$O/$r"; then HAVE_VARS[$r]=$REPO_VARS_OUT
    elif [ "$GH_HTTP" = 404 ] && [ "$DRY_RUN" = 1 ]; then HAVE_VARS[$r]=""
    else die "could not list the variables of $O/$r (HTTP $GH_HTTP); run create-repos.sh first"; fi
  fi
  if grep -qxF -- "$name"$'\t'"$want" <<<"${HAVE_VARS[$r]}"; then
    skip "$O/$r variable $name"; return 0
  fi
  printf '%s' "$want" | run_write_stdin "$GH" variable set "$name" --repo "$O/$r"
  did "set variable $name in $O/$r"
}
for entry in "${REPO_VARIABLES[@]}"; do
  IFS='|' read -r repo name src <<<"$entry"
  ensure_repo_variable "$repo" "$name" "$src"
done
if [ "$CFG_PLAN" = free ]; then
  log "PLAN=free: agent-skills-private gets no privacy job (it holds private data by design, guide 3.4)."
fi
finish
[ "$PROBLEMS" = 0 ] || { warn "$PROBLEMS environment(s) need a manual fix"; exit 1; }
