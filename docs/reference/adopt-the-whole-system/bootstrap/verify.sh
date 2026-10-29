#!/usr/bin/env bash
# verify.sh [--config fleet.env] [--only <group>[,<group>...]]
#
# Read-only. Reports PASS, FAIL or SKIP for each thing the other scripts set
# up, grouped so status.sh can tell the quick start's steps apart:
#
#   repos           the six repos exist, with the visibility the kit wants
#   labels          agent-ready and on-hold (LABELS in lib/common.sh)
#   branches        skills-evals has its persistent/eval-results branch
#                   (EVAL_RESULTS_BRANCH in lib/common.sh), which the eval
#                   loop's workflows fetch and nothing but create-repos.sh creates
#   environments    fleet-apply, plugin-runtime-review, routine-fire exist,
#                   with their reviewer and branch policy (ENVIRONMENTS)
#   variables       repo-settings' GUIDANCE_SYNC_APP_ID and
#                   ROSTER_WRITER_APP_ID match fleet.env (REPO_VARIABLES)
#   federation      skills-evals' four ANTHROPIC_* federation ids match
#                   fleet.env (REPO_VARIABLES; optional, so only when named)
#   apps:<app>      one App (APPS): its id is in fleet.env, and its client id
#                   and private key are stored where the workflows read them
#                   (by NAME only)
#   installs:<app>  that App is installed on OWNER's repos it needs (read
#                   from your installations: GET user/installations)
#   values:<group>  privacy (PRIVACY_DENYLIST in acsi-agentskills) or routine-fire
#                   (the eval routine's bearer and trigger id), by name only
#   settings        repo-settings' engine reports no drift from fleet.yml
#                   (apply-settings.sh --check, which clones repo-settings)
#   regen:<task>    a regenerate.sh task is done on your default branches
#                   (regenerate.sh --check <task>, which clones the repos)
#
# Without --only: repos, labels, branches, environments, variables, every App, its
# installation and every values group (the fast ones; settings and regen:* clone repos, so
# name them, and federation is optional, so name it too). Secret and variable values are never read or printed; the
# variables group compares a value to fleet.env's without printing either.
# Exit 0 when nothing failed, 1 otherwise. Makes no write call. Needs: gh, jq;
# git and python3 for settings and regen:*.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

ONLY=""
parse_common_args "$@"
set -- "${REST_ARGS[@]}"
while [ $# -gt 0 ]; do
  case $1 in
    --only) [ $# -ge 2 ] || die "--only needs a group list"; ONLY=$2; shift ;;
    --only=*) ONLY=${1#--only=} ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
app_known() { local e; for e in "${APPS[@]}"; do [ "${e%%|*}" = "$1" ] && return 0; done; return 1; }
group_known() { local e n; for e in "${OTHER_VALUES[@]}"; do IFS='|' read -r _ _ _ n _ <<<"$e"; [ "$(value_group "$n")" = "$1" ] && return 0; done; return 1; }
task_known() { local t; for t in "${REGEN_TASKS[@]}"; do [ "$t" = "$1" ] && return 0; done; return 1; }
declare -A SEL
if [ -n "$ONLY" ]; then
  case ",$ONLY," in *,,*) die "--only has an empty group: $ONLY" ;; esac
  IFS=',' read -r -a only_list <<<"$ONLY"
  for g in "${only_list[@]}"; do
    case $g in
      repos|labels|branches|environments|variables|federation|settings) ;;
      apps:*) app_known "${g#apps:}" || die "unknown App in --only: ${g#apps:}" ;;
      installs:*) app_known "${g#installs:}" || die "unknown App in --only: ${g#installs:}" ;;
      values:*) group_known "${g#values:}" || die "unknown values group in --only: ${g#values:}" ;;
      regen:*) task_known "${g#regen:}" || die "unknown regenerate task in --only: ${g#regen:}" ;;
      *) die "unknown group in --only: $g (repos, labels, branches, environments, variables, federation, apps:<app>, installs:<app>, values:<group>, settings, regen:<task>)" ;;
    esac
    SEL[$g]=1
  done
fi
sel() { # group [default-on]
  if [ -n "$ONLY" ]; then [ -n "${SEL[$1]:-}" ]; else [ "${2:-on}" = on ]; fi
}
[ "$DRY_RUN" = 0 ] || log "verify.sh is read-only; --dry-run changes nothing"
require_tools jq "$GH"
load_config "$CONFIG"
O=$CFG_OWNER
[ -z "$ONLY" ] || log "checking only: $ONLY"
PASS=0 FAIL=0 SKIPPED=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$*"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$*"; }
skp()  { SKIPPED=$((SKIPPED + 1)); printf 'SKIP: %s\n' "$*"; }
check() { # description jq-expression (against GH_OUT)
  if printf '%s' "$GH_OUT" | jq -e "$2" >/dev/null 2>&1; then pass "$1"; else fail "$1"; fi
}
where() { if [ "$1" = - ]; then printf ''; else printf ' environment %s' "$1"; fi; }

declare -A EXISTS DEFAULT_BRANCH
for r in "${FLEET_REPOS[@]}"; do
  if ! gh_read "repos/$O/$r"; then
    EXISTS[$r]=0
    fail "$O/$r exists (HTTP $GH_HTTP)"; continue
  fi
  EXISTS[$r]=1
  DEFAULT_BRANCH[$r]=$(printf '%s' "$GH_OUT" | jq -r '.default_branch // "main"')
  if sel repos; then
    want=$(repo_visibility "$r")
    pass "$O/$r exists"
    check "$O/$r is $want" "(if .visibility then .visibility elif .private then \"private\" else \"public\" end) == \"$want\""
  fi
  if sel labels; then
    if gh_read "repos/$O/$r/labels?per_page=100"; then
      for entry in "${LABELS[@]}"; do l=${entry%%|*}; check "$O/$r has label $l" "any(.[]; .name == \"$l\")"; done
    else fail "$O/$r labels readable (HTTP $GH_HTTP)"; fi
  fi
  if sel branches && [ "$r" = "$EVAL_RESULTS_REPO" ]; then
    if gh_read "repos/$O/$r/git/ref/heads/$EVAL_RESULTS_BRANCH"; then
      pass "$O/$r has the $EVAL_RESULTS_BRANCH branch"
    else
      fail "$O/$r has the $EVAL_RESULTS_BRANCH branch (HTTP $GH_HTTP; run create-repos.sh)"
    fi
  fi
done

for entry in "${ENVIRONMENTS[@]}"; do
  sel environments || break
  IFS='|' read -r r e rev br <<<"$entry"
  if ! env_available "$r"; then skp "$O/$r environment $e (private repo on PLAN=free)"; continue; fi
  [ "${EXISTS[$r]:-0}" = 1 ] || { fail "$O/$r environment $e (repo missing)"; continue; }
  if ! gh_read "repos/$O/$r/environments/$e"; then fail "$O/$r environment $e exists (HTTP $GH_HTTP)"; continue; fi
  pass "$O/$r environment $e exists"
  if [ "$br" = default ]; then
    check "$O/$r environment $e uses custom branch policies" '.deployment_branch_policy.custom_branch_policies == true and .deployment_branch_policy.protected_branches == false'
  else
    check "$O/$r environment $e has no branch policy (it runs on pull_request_target)" '.deployment_branch_policy == null'
  fi
  if [ "$rev" = owner ]; then
    env_json=$GH_OUT
    if gh_read "users/$CFG_REVIEWER"; then
      rid=$(printf '%s' "$GH_OUT" | jq -r .id); GH_OUT=$env_json
      check "$O/$r environment $e requires $CFG_REVIEWER's approval" "any(.protection_rules[]?; .type == \"required_reviewers\" and any(.reviewers[]?; .reviewer.id == $rid))"
    else fail "reviewer $CFG_REVIEWER readable (HTTP $GH_HTTP)"; fi
  fi
  [ "$br" = default ] || continue
  d=${DEFAULT_BRANCH[$r]}
  if gh_read "repos/$O/$r/environments/$e/deployment-branch-policies"; then
    check "$O/$r environment $e deploys only from $d" "[.branch_policies[]? | \"\(.type // \"branch\")/\(.name)\"] == [\"branch/$d\"]"
  else fail "$O/$r environment $e branch policies readable (HTTP $GH_HTTP)"; fi
done

declare -A REPO_VARS
for entry in "${REPO_VARIABLES[@]}"; do
  IFS='|' read -r r name src group <<<"$entry"
  if [ "$group" = federation ]; then sel federation off || continue; else sel "$group" || continue; fi
  want=$(repo_variable_value "$src")
  if [ -z "$want" ]; then
    if [ "$group" = federation ]; then fail "$O/$r variable $name: ${src#cfg:} is empty in fleet.env (docs/evals-and-loop.md#workload-identity-federation)"
    else fail "$O/$r variable $name: ${src#cfg:} is empty in fleet.env (run create-apps.sh)"; fi
    continue
  fi
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

has_name() { # kind repo env name
  if ! value_names "$1" "$O/$2" "$3"; then fail "$O/$2$(where "$3") ${1}s readable (HTTP $GH_HTTP)"; return; fi
  if grep -qxF -- "$4" <<<"$NAMES_OUT"; then pass "$O/$2$(where "$3") has $1 $4"; else fail "$O/$2$(where "$3") has $1 $4"; fi
}
INSTALLS=""
installs() { # sets INSTALLS to "app_id<TAB>installation id" lines, once
  [ -z "$INSTALLS" ] || return 0
  gh_read "user/installations?per_page=100" || return 1
  INSTALLS=$(printf '%s' "$GH_OUT" | jq -r '.installations[]? | "\(.app_id)\t\(.id)\t\(.account.login)"')
  INSTALLS=${INSTALLS:-none}
}
for entry in "${APPS[@]}"; do
  IFS='|' read -r app _perms installed r idspec env sec key _optional <<<"$entry"
  sel "apps:$app" || sel "installs:$app" || continue
  if [ "$env" != - ] && ! env_available "$r"; then skp "$app App (its key would be in a private repo's environment on PLAN=free)"; continue; fi
  cfg=CFG_$key
  if [ -z "${!cfg}" ]; then fail "$app App: $key is in fleet.env (run create-apps.sh $app)"; continue; fi
  if sel "apps:$app"; then
    pass "$app App: $key is in fleet.env"
    if [ "${EXISTS[$r]:-0}" = 1 ]; then
      has_name "${idspec%%:*}" "$r" - "${idspec#*:}"
      has_name secret "$r" "$env" "$sec"
    fi
  fi
  sel "installs:$app" || continue
  if ! installs; then fail "$app App: your installations readable (HTTP $GH_HTTP)"; continue; fi
  iid=$(awk -F'\t' -v a="${!cfg}" -v o="${O,,}" '$1 == a && tolower($3) == o {print $2; exit}' <<<"$INSTALLS")
  if [ -z "$iid" ]; then fail "$app App is installed on $O (create-apps.sh printed the link)"; continue; fi
  if ! gh_read "user/installations/$iid/repositories?per_page=100"; then fail "$app App: its repositories readable (HTTP $GH_HTTP)"; continue; fi
  if [ "$installed" = fleet ]; then need=("${FLEET_REPOS[@]}"); else read -r -a need <<<"$installed"; fi
  for n in "${need[@]}"; do
    check "$app App is installed on $O/$n" "any(.repositories[]?; (.full_name | ascii_downcase) == (\"$O/$n\" | ascii_downcase))"
  done
done

for entry in "${OTHER_VALUES[@]}"; do
  IFS='|' read -r r e kind name _src <<<"$entry"
  sel "values:$(value_group "$name")" || continue
  if [ "$e" != - ] && ! env_available "$r"; then skp "$O/$r$(where "$e") $kind $name (private repo on PLAN=free)"; continue; fi
  [ "${EXISTS[$r]:-0}" = 1 ] || continue
  has_name "$kind" "$r" "$e" "$name"
done

sub_check() { # description cmd...
  local out="$BOOT_WORK/sub.out" d=$1; shift
  if "$@" >"$out" 2>&1; then pass "$d"
  else fail "$d:"; sed -n 's/^\(NOT DONE\|ERROR\|MISSING\|WARNING\): /    &/p' "$out" | head -n 20; fi
}
if sel settings off; then
  while IFS= read -r o; do
    if [ "${o,,}" = "${O,,}" ]; then
      sub_check "$o's repos match fleet.yml (repo-settings engine diff)" bash "$HERE/apply-settings.sh" --config "$CONFIG" --check
    else
      skp "$o's repos (sign gh in as $o, then: apply-settings.sh --check --owner $o)"
    fi
  done < <(owners)
fi
for t in "${REGEN_TASKS[@]}"; do
  sel "regen:$t" off || continue
  sub_check "regenerate.sh $t is done" bash "$HERE/regenerate.sh" --config "$CONFIG" --check "$t"
done

printf '%d passed, %d failed, %d skipped\n' "$PASS" "$FAIL" "$SKIPPED"
[ "$FAIL" = 0 ]
