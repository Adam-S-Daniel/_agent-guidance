#!/usr/bin/env bash
# set-secrets.sh [--config fleet.env] [--dry-run] [--rotate] [--denylist PATH] [--only privacy|routine-fire]
#
# Sets the environment secrets and variables the guide names that are not App
# credentials (create-apps.sh stores those):
#
#   skills-evals  routine-fire  secret   EVAL_ROUTINE_FIRE_BEARER  (9.4; prompted, hidden)
#   skills-evals  routine-fire  variable ROUTINE_TRIGGER_ID        (9.4; trig_..., prompted)
#   agent-guidance, agent-skills, skills-evals, repo-settings
#                 privacy       secret   PRIVACY_DENYLIST          (6.5; from a file)
#
# The bearer is shown once, on the routine's API trigger at
# claude.ai/code/routines. The denylist is a file OUTSIDE every repo, one
# regular expression per line (your real names, emails, hosts, account IDs);
# the script refuses a path inside a git work tree and never prints a pattern.
# Values reach gh on stdin only, and are never echoed. --only <environment>
# sets just that environment's values (privacy comes before the routine exists).
#
# Idempotent: a secret or variable that already exists is left alone (GitHub
# cannot show a secret, so it is not compared); --rotate replaces them.
# PLAN=free skips environments in private repos. Run create-environments.sh
# first. Needs: gh, jq, python3.

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
    die "the denylist is inside a git work tree; keep it outside every repo (guide 6.5)"
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
  if [ -n "$(find "$DENYLIST" -perm /077 2>/dev/null)" ]; then
    warn "the denylist is readable by other users; consider chmod 600"
  fi
  HAVE_DENYLIST=1
}

set_one() { # repo env kind name source
  local repo=$1 env=$2 kind=$3 name=$4 src=$5
  if ! env_available "$repo"; then
    log "skip: $O/$repo/$env $name: $repo is private and PLAN=free, so it has no environments (guide 3.4)"
    return 0
  fi
  if env_names "$kind" "$O/$repo" "$env"; then
    if grep -qxF -- "$name" <<<"$ENV_NAMES_OUT" && [ "$ROTATE" = 0 ]; then
      skip "$O/$repo environment $env has $kind $name"; return 0
    fi
  elif [ "$DRY_RUN" = 0 ]; then
    die "could not list ${kind}s of environment $env in $O/$repo (HTTP $GH_HTTP): run create-environments.sh first"
  fi
  if [ "$DRY_RUN" = 1 ]; then
    WRITES=$((WRITES + 1))
    printf 'DRY-RUN: %s %s set %s --env %s --repo %s   (value on stdin, never shown)\n' "$GH" "$kind" "$name" "$env" "$O/$repo"
    return 0
  fi
  WRITES=$((WRITES + 1))
  case $name in
    EVAL_ROUTINE_FIRE_BEARER) get_bearer; printf '%s' "$BEARER" | run_write_stdin "$GH" secret set "$name" --env "$env" --repo "$O/$repo" ;;
    ROUTINE_TRIGGER_ID) get_trigger; printf '%s' "$TRIGGER" | run_write_stdin "$GH" variable set "$name" --env "$env" --repo "$O/$repo" ;;
    PRIVACY_DENYLIST) get_denylist; run_write_stdin "$GH" secret set "$name" --env "$env" --repo "$O/$repo" < "$DENYLIST" ;;
    *) die "no source for $name ($src)" ;;
  esac
  did "set $kind $name in $O/$repo environment $env"
}

log "Owner $O, plan $CFG_PLAN$([ "$DRY_RUN" = 1 ] && echo ', DRY RUN')"
for entry in "${OTHER_VALUES[@]}"; do
  IFS='|' read -r repo env kind name src <<<"$entry"
  [ -z "$ONLY" ] || [ "$ONLY" = "$env" ] || continue
  set_one "$repo" "$env" "$kind" "$name" "$src"
done
BEARER="" TRIGGER=""
finish
