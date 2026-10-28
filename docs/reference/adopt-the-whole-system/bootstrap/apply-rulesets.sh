#!/usr/bin/env bash
# apply-rulesets.sh [--config fleet.env] [--dry-run] [--with-required-check | --without-required-check] [--rulesets-dir <dir>] (<repo>... | --all)
#
# Applies the guide's 3.1 rulesets (files in rulesets/, or in --rulesets-dir,
# for example a repo-settings checkout's rulesets/) to each named repo:
#
#   default-branch       PR-only default branch, merge commits only.
#                        --with-required-check adds rulesets/phase4/
#                        required-status-checks.json (agent-checks and privacy,
#                        from the GitHub Actions app). The guide says: add it
#                        only in phase 4, after agent-checks has reported on a
#                        pull request, or every pull request waits forever.
#                        The choice is kept: --with-required-check writes
#                        REQUIRED_CHECKS=on to fleet.env, and every later run
#                        keeps the checks until --without-required-check
#                        writes REQUIRED_CHECKS=off.
#   persistent-branches  persistent/** cannot be deleted or force-pushed in
#                        any repo. In skills-evals (loop-writer) and
#                        agent-guidance (guidance-sync) it also blocks
#                        updates except by that one App, as soon as that
#                        App's ID is in fleet.env. Every other repo, and a
#                        writer repo before its App exists: deletion and
#                        non_fast_forward, no bypass (lib/rulesets.sh).
#
# A ruleset of the same name is updated with PUT, else created with POST;
# one that already matches is left alone. After every write the live ruleset
# is read back and compared with the file (jq-normalized; fields GitHub adds
# on read-back are ignored): any drift fails the run.
#
# PLAN=free: rulesets are not enforced on private repos, so those are skipped
# (the guide: accept that the two private repos are unprotected).
# Idempotent; a second run says "Nothing to change". Needs: gh, jq.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"
# shellcheck source=lib/rulesets.sh
. "$HERE/lib/rulesets.sh"

CHECK_FLAG=""
parse_common_args "$@"
set -- "${REST_ARGS[@]}"
REPOS=()
while [ $# -gt 0 ]; do
  case $1 in
    --with-required-check|--without-required-check)
      [ -z "$CHECK_FLAG" ] || [ "$CHECK_FLAG" = "$1" ] || die "--with-required-check and --without-required-check together"
      CHECK_FLAG=$1 ;;
    --rulesets-dir) [ $# -ge 2 ] || die "--rulesets-dir needs a folder"; RULESET_DIR=$2; shift ;;
    --rulesets-dir=*) RULESET_DIR=${1#--rulesets-dir=} ;;
    --all) REPOS=("${FLEET_REPOS[@]}") ;;
    -*) die "unknown option: $1" ;;
    *) is_known_repo "$1" || die "unknown repo: $1"; REPOS+=("$1") ;;
  esac
  shift
done
[ -d "$RULESET_DIR" ] || die "not a folder: $RULESET_DIR (--rulesets-dir)"
for name in "${RULESET_NAMES[@]}"; do
  [ -f "$RULESET_DIR/$name.json" ] || die "missing ruleset file $RULESET_DIR/$name.json"
done
[ ${#REPOS[@]} -gt 0 ] || { usage; die "name a repo, or pass --all"; }
require_tools jq "$GH"
load_config "$CONFIG"
O=$CFG_OWNER
DRIFT=0
# The phase 4 required checks: a flag sets the choice and records it in
# fleet.env; with no flag, the recorded choice holds, so a later plain run
# cannot drop the checks.
case $CHECK_FLAG in
  --with-required-check) want_checks=on ;;
  --without-required-check) want_checks=off ;;
  *) want_checks=$CFG_REQUIRED_CHECKS ;;
esac
if [ -n "$CHECK_FLAG" ] && [ "$want_checks" != "$CFG_REQUIRED_CHECKS" ]; then
  if [ "$DRY_RUN" = 1 ]; then
    log "would record REQUIRED_CHECKS=$want_checks in $CONFIG_PATH"
  else
    set_config_value REQUIRED_CHECKS "$want_checks"
    log "recorded REQUIRED_CHECKS=$want_checks in $CONFIG_PATH"
  fi
fi
WITH_CHECK=0; [ "$want_checks" = on ] && WITH_CHECK=1

apply_one() { # repo name
  local repo=$1 name=$2 full="$CFG_OWNER/$1" want id live
  build_ruleset "$name" "$repo" "$WITH_CHECK"; want=$RULESET_JSON
  [ -z "$RULESET_NOTE" ] || log "note: $full $name: $RULESET_NOTE"
  id=$(find_ruleset_id "$full" "$name")
  if [ -n "$id" ]; then
    gh_read_or_die "repos/$full/rulesets/$id" "ruleset $name of $full"
    if ruleset_matches "$want" "$GH_OUT"; then
      skip "$full ruleset $name matches its file"; return 0
    fi
    gh_write_api PUT "repos/$full/rulesets/$id" "$want"
    did "updated ruleset $name on $full"
  else
    gh_write_api POST "repos/$full/rulesets" "$want"
    did "created ruleset $name on $full"
    [ "$DRY_RUN" = 1 ] && return 0
    id=$(printf '%s' "$GH_OUT" | jq -r '.id // empty')
    [ -n "$id" ] || id=$(find_ruleset_id "$full" "$name")
  fi
  [ "$DRY_RUN" = 1 ] && return 0
  # Read back what is live and compare it with the file (guide 3.1).
  [[ $id =~ ^[0-9]+$ ]] || die "no id for ruleset $name on $full after the write"
  gh_read_or_die "repos/$full/rulesets/$id" "ruleset $name of $full"
  live=$GH_OUT
  if ruleset_matches "$want" "$live"; then
    log "read back: $full ruleset $name matches its file"
  else
    printf 'DRIFT: %s ruleset %s does not match its file after the write: %s\n' "$full" "$name" "$(ruleset_diff "$want" "$live")" >&2
    DRIFT=$((DRIFT + 1))
  fi
}

log "Owner $O, plan $CFG_PLAN, rulesets from $RULESET_DIR, required checks $want_checks$([ "$DRY_RUN" = 1 ] && echo ', DRY RUN')"
for repo in "${REPOS[@]}"; do
  if ! rulesets_apply_here "$repo"; then
    log "skip: $O/$repo is private and PLAN=free: rulesets are not enforced there (guide section 2)"
    continue
  fi
  for name in "${RULESET_NAMES[@]}"; do apply_one "$repo" "$name"; done
done
finish
[ "$DRIFT" = 0 ] || die "$DRIFT ruleset(s) drifted from their files"
