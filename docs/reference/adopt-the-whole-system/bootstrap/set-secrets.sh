#!/usr/bin/env bash
# set-secrets.sh [--config fleet.env] [--dry-run] [--rotate] [--denylist PATH] [--only privacy|routine-fire]
#
# Sets the secrets and variables the exported workflows read that are not App
# credentials (create-apps.sh stores those). OTHER_VALUES in lib/common.sh:
#
#   acsi-agentskills  (repository)  secret   PRIVACY_DENYLIST          group privacy
#   skills-evals      routine-fire  secret   EVAL_ROUTINE_FIRE_BEARER  group routine-fire
#   skills-evals      routine-fire  variable ROUTINE_TRIGGER_ID        group routine-fire
#
# PRIVACY_DENYLIST feeds acsi-agentskills' required `privacy` check
# (scripts/check_privacy_denylist.py), which fails closed without it, so set
# it before the acsi-agentskills starter pull request. It is a file OUTSIDE every
# repo, one regular expression per line (your real names, emails, hosts,
# account ids); the script refuses a path inside a git work tree and never
# prints a pattern.
#
# The bearer and the trigger id come from the eval runner routine's API
# trigger at claude.ai/code/routines (skills-evals docs/routine-prompt.md);
# the bearer is shown once. routine-eval-fire.yml reads both from the
# routine-fire environment and refuses a trigger id that is not trig_...
#
# Values reach gh on stdin only and are never echoed. --only <group> sets one
# group (privacy comes long before the routine exists).
#
# Idempotent: a value that already exists is left alone (GitHub cannot show a
# secret, so it is not compared); --rotate replaces them. PLAN=free skips
# environments in private repos. Needs: gh, jq, python3.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

ROTATE=0 DENYLIST="" ONLY=""
parse_common_args "$@"
set -- "${REST_ARGS[@]}"
while [ $# -gt 0 ]; do
  case $1 in
    --rotate) ROTATE=1; shift ;;
    --denylist) [ $# -ge 2 ] || die "--denylist needs a path"; DENYLIST=$2; shift 2 ;;
    --only) [ $# -ge 2 ] || die "--only needs an environment"; ONLY=$2; shift 2
      [[ $ONLY == privacy || $ONLY == routine-fire ]] || die "--only takes privacy or routine-fire" ;;
    *) die "unknown argument: $1" ;;
  esac
done
require_tools jq python3 "$GH"
load_config "$CONFIG"
O=$CFG_OWNER

BEARER="" TRIGGER="" HAVE_BEARER=0 HAVE_TRIGGER=0 HAVE_DENYLIST=0

get_bearer() {
  [ "$HAVE_BEARER" = 1 ] && return 0
  printf 'EVAL_ROUTINE_FIRE_BEARER (the API trigger token from claude.ai/code/routines; input hidden): ' >&2
  IFS= read -rs BEARER || die "no value given"
  printf '\n' >&2
  [[ $BEARER =~ ^[[:graph:]]{16,4096}$ ]] || die "that does not look like a bearer token (16+ printable characters, no spaces)"
  HAVE_BEARER=1
}

get_trigger() {
  [ "$HAVE_TRIGGER" = 1 ] && return 0
  printf 'ROUTINE_TRIGGER_ID (the routine ID, trig_...): ' >&2
  IFS= read -r TRIGGER || die "no value given"
  TRIGGER=${TRIGGER//[[:space:]]/}
  [[ $TRIGGER =~ ^trig_[A-Za-z0-9]{1,64}$ ]] || die "a routine ID looks like trig_ followed by letters and digits"
  HAVE_TRIGGER=1
}

get_denylist() {
  [ "$HAVE_DENYLIST" = 1 ] && return 0
  if [ -z "$DENYLIST" ]; then
    printf 'Path to your privacy denylist file (outside every repo): ' >&2
    IFS= read -r DENYLIST || die "no path given"
  fi
  DENYLIST=${DENYLIST/#\~/$HOME}
  [ -f "$DENYLIST" ] || die "the denylist path is not a file"
  local dir; dir=$(cd "$(dirname "$DENYLIST")" && pwd)
  if git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    die "the denylist is inside a git work tree; keep it outside every repo"
  fi
  # Each pattern must compile, and there must be at least one. Report line
  # numbers only, never a pattern.
  python3 -I - "$DENYLIST" <<'PY' || die "the denylist failed validation (see above)"
import re, sys
bad, n = [], 0
for i, l in enumerate(open(sys.argv[1], encoding="utf-8"), 1):
    l = l.strip()
    if not l or l.startswith("#"):
        continue
    n += 1
    try:
        re.compile(l, re.I)
    except re.error:
        bad.append(i)
if bad:
    print("denylist: line(s) " + ", ".join(map(str, bad)) + " are not valid regular expressions", file=sys.stderr)
    sys.exit(1)
if n == 0:
    print("denylist: no patterns; the privacy check would fail closed", file=sys.stderr)
    sys.exit(1)
PY
  # The mode bits from stat(2), not `find -perm /077`: BSD find (macOS) has
  # no `/` form, so it erred, the error was discarded, and nothing warned.
  if python3 -I -c 'import os, sys; sys.exit(0 if os.stat(sys.argv[1]).st_mode & 0o077 else 1)' "$DENYLIST"; then
    warn "the denylist is readable by other users; consider chmod 600"
  fi
  HAVE_DENYLIST=1
}

set_one() { # repo env kind name
  local repo=$1 env=$2 kind=$3 name=$4 at
  if [ "$env" = - ]; then at=""; else at=" environment $env"; fi
  if [ "$env" != - ] && ! env_available "$repo"; then
    log "skip: $O/$repo$at $name: $repo is private and PLAN=free, so it has no environments"
    return 0
  fi
  if value_names "$kind" "$O/$repo" "$env"; then
    if grep -qxF -- "$name" <<<"$NAMES_OUT" && [ "$ROTATE" = 0 ]; then
      skip "$O/$repo$at has $kind $name"; return 0
    fi
  elif [ "$DRY_RUN" = 0 ]; then
    die "could not list the ${kind}s of $O/$repo$at (HTTP $GH_HTTP): run create-repos.sh$([ "$env" = - ] || echo " and create-environments.sh") first"
  fi
  if [ "$DRY_RUN" = 1 ]; then
    set_value "$kind" "$O/$repo" "$env" "$name" </dev/null
  else
    case $name in
      EVAL_ROUTINE_FIRE_BEARER) get_bearer; set_value "$kind" "$O/$repo" "$env" "$name" < <(printf '%s' "$BEARER") ;;
      ROUTINE_TRIGGER_ID) get_trigger; set_value "$kind" "$O/$repo" "$env" "$name" < <(printf '%s' "$TRIGGER") ;;
      PRIVACY_DENYLIST) get_denylist; set_value "$kind" "$O/$repo" "$env" "$name" < "$DENYLIST" ;;
      *) die "no source for $name" ;;
    esac
  fi
  did "set $kind $name in $O/$repo$at"
}

log "Owner $O, plan $CFG_PLAN$([ "$DRY_RUN" = 1 ] && echo ', DRY RUN')"
for entry in "${OTHER_VALUES[@]}"; do
  IFS='|' read -r repo env kind name _src <<<"$entry"
  [ -z "$ONLY" ] || [ "$ONLY" = "$(value_group "$name")" ] || continue
  set_one "$repo" "$env" "$kind" "$name"
done
BEARER="" TRIGGER=""
finish
