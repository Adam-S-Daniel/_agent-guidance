#!/usr/bin/env bash
# verify.sh [--config fleet.env] [--phase N] [--with-required-check | --without-required-check] [--with-optional]
#
# Read-only. Reports PASS, FAIL or SKIP for every item the other scripts set.
# --phase N (1 to 10, the guide's phases) checks only what should exist by the
# end of phase N, per the quick start's table: the repos, settings, labels,
# environments, rulesets and the privacy secret from phase 1; the
# guidance-sync App from 2; the required checks from 4 (unless a
# --with/--without-required-check flag says otherwise); loop-writer and the
# routine-fire values from 7; lessons-reader, roster-writer (optional) and
# settings-admin from 8. Anything later is reported SKIP. Without --phase it
# expects everything, and takes the required checks from REQUIRED_CHECKS.
# What it checks:
#
#   * each of the five repos exists with the visibility the guide wants;
#   * merge settings (merge commit only, auto-merge, delete branch on merge);
#   * Actions settings (SHA pinning, read-only token, fork run approval);
#   * labels agent-ready, on-hold, agent-authored, vendor-release, agent-check
#     (LABELS in lib/common.sh);
#   * each environment exists, deploys only from the default branch, and has
#     the owner as required reviewer where the guide asks for one;
#   * rulesets are live and match their files (with the phase 4 required
#     checks when fleet.env has REQUIRED_CHECKS=on, or --with-required-check
#     is given; --without-required-check expects none);
#   * environment variables and secrets are present, by NAME only: values are
#     never read (gh cannot show a secret, and variable values are not fetched);
#   * the repository variables the workflows read (REPO_VARIABLES) hold the
#     value fleet.env implies, compared without printing either value.
#
# Exit 0 when nothing failed, 1 otherwise. Makes no write call. Needs: gh, jq.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"
# shellcheck source=lib/rulesets.sh
. "$HERE/lib/rulesets.sh"

WITH_CHECK="" WITH_OPTIONAL=0 PHASE=""
parse_common_args "$@"
set -- "${REST_ARGS[@]}"
while [ $# -gt 0 ]; do
  case $1 in
    --with-required-check) WITH_CHECK=1 ;;
    --without-required-check) WITH_CHECK=0 ;;
    --with-optional) WITH_OPTIONAL=1 ;;
    --phase) [ $# -ge 2 ] || die "--phase needs a number"; PHASE=$2; shift
      [[ $PHASE =~ ^([1-9]|10)$ ]] || die "--phase must be 1 to 10 (the guide's phases)" ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
# The phase each item first exists in (the guide's quick start table). An
# App or environment value missing from these maps is a kit bug: refuse.
declare -A APP_PHASE=([guidance-sync]=2 [loop-writer]=7 [lessons-reader]=8 [roster-writer]=8 [settings-admin]=8)
declare -A VALUE_PHASE=([privacy]=1 [routine-fire]=7)
REQUIRED_CHECKS_PHASE=4
upto=${PHASE:-99}
due() { [ "$1" -le "$upto" ]; }   # due <phase>: should it exist by now?
[ "$DRY_RUN" = 0 ] || log "verify.sh is read-only; --dry-run changes nothing"
require_tools jq "$GH"
load_config "$CONFIG"
O=$CFG_OWNER
if [ -z "$WITH_CHECK" ]; then
  WITH_CHECK=0
  if [ -n "$PHASE" ]; then due "$REQUIRED_CHECKS_PHASE" && WITH_CHECK=1
  elif [ "$CFG_REQUIRED_CHECKS" = on ]; then WITH_CHECK=1; fi
fi
[ -z "$PHASE" ] || log "checking what should exist by the end of phase $PHASE"
PASS=0 FAIL=0 SKIPPED=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$*"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$*"; }
skp()  { SKIPPED=$((SKIPPED + 1)); printf 'SKIP: %s\n' "$*"; }
check() { # description jq-expression (against GH_OUT)
  if printf '%s' "$GH_OUT" | jq -e "$2" >/dev/null 2>&1; then pass "$1"; else fail "$1"; fi
}

declare -A EXISTS DEFAULT_BRANCH
for r in "${FLEET_REPOS[@]}"; do
  want=$(repo_visibility "$r")
  if ! gh_read "repos/$O/$r"; then
    EXISTS[$r]=0
    fail "$O/$r exists (HTTP $GH_HTTP)"; continue
  fi
  EXISTS[$r]=1
  DEFAULT_BRANCH[$r]=$(printf '%s' "$GH_OUT" | jq -r '.default_branch // "main"')
  pass "$O/$r exists"
  check "$O/$r is $want" "(if .visibility then .visibility elif .private then \"private\" else \"public\" end) == \"$want\""
  check "$O/$r merge settings: merge commit only, auto-merge, delete branch on merge" \
    '.allow_merge_commit == true and .allow_squash_merge == false and .allow_rebase_merge == false and .allow_auto_merge == true and .delete_branch_on_merge == true'
  if [ "$CFG_SHA_PINNING_REQUIRED" = true ]; then
    if gh_read "repos/$O/$r/actions/permissions"; then check "$O/$r requires SHA-pinned actions" '.sha_pinning_required == true'
    else fail "$O/$r Actions permissions readable (HTTP $GH_HTTP)"; fi
  fi
  if gh_read "repos/$O/$r/actions/permissions/workflow"; then
    check "$O/$r default workflow token is read-only and cannot approve" '.default_workflow_permissions == "read" and .can_approve_pull_request_reviews == false'
  else fail "$O/$r workflow token settings readable (HTTP $GH_HTTP)"; fi
  if [ "$want" = public ]; then
    if gh_read "repos/$O/$r/actions/permissions/fork-pr-contributor-approval"; then
      check "$O/$r fork runs need approval for all outside collaborators" '.approval_policy == "all_external_contributors"'
    else fail "$O/$r fork approval readable (HTTP $GH_HTTP)"; fi
  else
    skp "$O/$r fork run approval (private repo)"
  fi
  if gh_read "repos/$O/$r/labels?per_page=100"; then
    for entry in "${LABELS[@]}"; do l=${entry%%|*}; check "$O/$r has label $l" "any(.[]; .name == \"$l\")"; done
  else fail "$O/$r labels readable (HTTP $GH_HTTP)"; fi
done

for entry in "${ENVIRONMENTS[@]}"; do
  IFS='|' read -r r e rev _src optional <<<"$entry"
  if [ "$optional" = yes ] && [ "$WITH_OPTIONAL" = 0 ]; then skp "$O/$r environment $e (optional)"; continue; fi
  if ! env_available "$r"; then skp "$O/$r environment $e (private repo on PLAN=free)"; continue; fi
  [ "${EXISTS[$r]:-0}" = 1 ] || { fail "$O/$r environment $e (repo missing)"; continue; }
  if ! gh_read "repos/$O/$r/environments/$e"; then fail "$O/$r environment $e exists (HTTP $GH_HTTP)"; continue; fi
  pass "$O/$r environment $e exists"
  check "$O/$r environment $e uses custom branch policies" '.deployment_branch_policy.custom_branch_policies == true and .deployment_branch_policy.protected_branches == false'
  if [ "$rev" = owner ]; then
    env_json=$GH_OUT
    if gh_read "users/$CFG_REVIEWER"; then
      rid=$(printf '%s' "$GH_OUT" | jq -r .id); GH_OUT=$env_json
      check "$O/$r environment $e requires $CFG_REVIEWER's approval" "any(.protection_rules[]?; .type == \"required_reviewers\" and any(.reviewers[]?; .reviewer.id == $rid))"
    else fail "reviewer $CFG_REVIEWER readable (HTTP $GH_HTTP)"; fi
  fi
  d=${DEFAULT_BRANCH[$r]}
  if gh_read "repos/$O/$r/environments/$e/deployment-branch-policies"; then
    check "$O/$r environment $e deploys only from $d" "[.branch_policies[]? | \"\(.type // \"branch\")/\(.name)\"] == [\"branch/$d\"]"
  else fail "$O/$r environment $e branch policies readable (HTTP $GH_HTTP)"; fi
done

for r in "${FLEET_REPOS[@]}"; do
  [ "${EXISTS[$r]:-0}" = 1 ] || continue
  if ! rulesets_apply_here "$r"; then skp "$O/$r rulesets (private repo on PLAN=free)"; continue; fi
  for name in "${RULESET_NAMES[@]}"; do
    build_ruleset "$name" "$r" "$WITH_CHECK"
    id=$(find_ruleset_id "$O/$r" "$name") || { fail "$O/$r rulesets readable"; continue; }
    if [ -z "$id" ]; then fail "$O/$r ruleset $name is live"; continue; fi
    if gh_read "repos/$O/$r/rulesets/$id" && ruleset_matches "$RULESET_JSON" "$GH_OUT"; then
      pass "$O/$r ruleset $name is live and matches its file${RULESET_NOTE:+ ($RULESET_NOTE)}"
    else
      live=$GH_OUT; [ -n "$live" ] || live='{}'
      fail "$O/$r ruleset $name matches its file: $(ruleset_diff "$RULESET_JSON" "$live")"
    fi
  done
done

has_name() { # kind repo env name
  if ! env_names "$1" "$O/$2" "$3"; then fail "$O/$2 environment $3 ${1}s readable (HTTP $GH_HTTP)"; return; fi
  if grep -qxF -- "$4" <<<"$ENV_NAMES_OUT"; then pass "$O/$2 environment $3 has $1 $4"; else fail "$O/$2 environment $3 has $1 $4"; fi
}
for entry in "${APPS[@]}"; do
  IFS='|' read -r app _perms _inst r e var sec key optional <<<"$entry"
  [ -n "${APP_PHASE[$app]:-}" ] || die "verify.sh has no phase for the $app App"
  if ! due "${APP_PHASE[$app]}"; then skp "$app App (phase ${APP_PHASE[$app]}, after phase $PHASE)"; continue; fi
  cfg=CFG_$key
  if [ "$optional" = yes ] && [ -z "${!cfg}" ] && [ "$WITH_OPTIONAL" = 0 ]; then skp "$app App (optional)"; continue; fi
  if ! env_available "$r"; then skp "$app App credentials (private repo on PLAN=free)"; continue; fi
  if [ -n "${!cfg}" ]; then pass "$key is in fleet.env"; else fail "$key is in fleet.env (run create-apps.sh)"; fi
  [ "${EXISTS[$r]:-0}" = 1 ] || continue
  has_name variable "$r" "$e" "$var"
  has_name secret "$r" "$e" "$sec"
done
for entry in "${OTHER_VALUES[@]}"; do
  IFS='|' read -r r e kind name _src <<<"$entry"
  [ -n "${VALUE_PHASE[$e]:-}" ] || die "verify.sh has no phase for environment $e's values"
  if ! due "${VALUE_PHASE[$e]}"; then skp "$O/$r environment $e $kind $name (phase ${VALUE_PHASE[$e]}, after phase $PHASE)"; continue; fi
  if ! env_available "$r"; then skp "$O/$r environment $e $kind $name (private repo on PLAN=free)"; continue; fi
  [ "${EXISTS[$r]:-0}" = 1 ] || continue
  has_name "$kind" "$r" "$e" "$name"
done

declare -A REPO_VARS
for entry in "${REPO_VARIABLES[@]}"; do
  IFS='|' read -r r name src <<<"$entry"
  want=$(repo_variable_value "$src")
  if [ -z "$want" ]; then skp "$O/$r variable $name (${src#cfg:} is empty in fleet.env)"; continue; fi
  [ "${EXISTS[$r]:-0}" = 1 ] || continue
  if [ -z "${REPO_VARS[$r]+set}" ]; then
    if ! repo_variables "$O/$r"; then fail "$O/$r variables readable (HTTP $GH_HTTP)"; continue; fi
    REPO_VARS[$r]=$REPO_VARS_OUT
  fi
  if grep -qxF -- "$name"$'\t'"$want" <<<"${REPO_VARS[$r]}"; then
    pass "$O/$r variable $name matches fleet.env"
  else
    fail "$O/$r variable $name is missing or differs from fleet.env (run create-environments.sh)"
  fi
done

printf '%d passed, %d failed, %d skipped\n' "$PASS" "$FAIL" "$SKIPPED"
[ "$FAIL" = 0 ]
