#!/usr/bin/env bash
# status.sh [--config fleet.env] [--mark <id>] [--unmark <id>]
#
# Where you are in the quick start. Read-only toward GitHub: it makes no write
# call. For each step in steps.json it decides done or not done from the real
# state where it can:
#
#   config  fleet.env exists, parses, and sets OWNER and PLAN (step 0);
#   verify  verify.sh --only <groups> reports no FAIL and at least one PASS;
#   files   each named starter file is on the repo's default branch (a
#           contents read), so the starter's pull request has been merged;
#   ask     what nothing here can see (an App installation, a routine saved
#           at claude.ai, a pasted setup script, another machine): done once
#           you say so with --mark <id>, undone with --unmark <id>.
#
# Answers to ask steps live in one JSON file per owner, outside every repo:
# ${ADOPT_STATE_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/adopt-the-whole-system/<owner>.json}.
# A path inside this kit or any git work tree is refused. It never prompts.
#
# Prints one line per step (done, next, todo, optional, or "asked: done"),
# then "N of M done" and the next step: title, time estimate, kind, what to
# run and the docs section that explains it. Steps are sequential: the first
# step not done is next, except that an optional step never blocks the next
# one. A step done earlier can turn not done when something changes under it
# (a merged pull request that breaks a pin); rerun it.
# Exit 0 whatever the progress; 1 only on its own errors. Never prints a
# secret or variable value (verify.sh reads names only). Needs: gh, jq.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

STEPS_FILE=$HERE/steps.json
KIT=$(cd "$HERE/.." && pwd -P)
MARKS=() UNMARKS=()
parse_common_args "$@"
set -- "${REST_ARGS[@]}"
while [ $# -gt 0 ]; do
  case $1 in
    --mark) [ $# -ge 2 ] || die "--mark needs a step id"; MARKS+=("$2"); shift ;;
    --unmark) [ $# -ge 2 ] || die "--unmark needs a step id"; UNMARKS+=("$2"); shift ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
require_tools jq

# ------------------------------------------------------------------ steps
[ -f "$STEPS_FILE" ] || die "steps file not found: $STEPS_FILE"
jq -e '.steps | type == "array" and length > 0' "$STEPS_FILE" >/dev/null 2>&1 || die "$STEPS_FILE: no steps list"
IDS=()
declare -A TITLE KIND RUN LINK MIN CTYPE CARG OPT
while IFS=$'\t' read -r id title kind run link minutes ctype opt carg; do
  [ -z "${TITLE[$id]+set}" ] || die "$STEPS_FILE: step id $id appears twice"
  case $ctype in config|verify|files|ask) ;; *) die "$STEPS_FILE: step $id has an unknown check type: $ctype" ;; esac
  IDS+=("$id") TITLE[$id]=$title KIND[$id]=$kind RUN[$id]=$run LINK[$id]=$link MIN[$id]=$minutes CTYPE[$id]=$ctype CARG[$id]=$carg OPT[$id]=$opt
done < <(jq -r '.steps[] | [.id, .title, .kind, (.run | gsub("`"; "")), .link, (.minutes | tostring), .check.type, (if .optional then "yes" else "no" end),
  (if .check.type == "verify" then (.check.groups | join(","))
   elif .check.type == "files" then ([.check.files[] | .repo + ":" + .path] | join(" "))
   else "" end)] | @tsv' "$STEPS_FILE")

for id in "${MARKS[@]}" "${UNMARKS[@]}"; do
  [ -n "${TITLE[$id]+set}" ] || die "no step $id in steps.json"
  [ "${CTYPE[$id]}" = ask ] || die "step $id is checked against the real state, so status.sh decides it; only ask steps take --mark or --unmark"
done

# ------------------------------------------------------------------ config
CONFIG_OK=0 CONFIG_ERR=""
if [ ! -f "$CONFIG" ]; then
  CONFIG_ERR="no $CONFIG yet"
elif CONFIG_ERR=$( (load_config "$CONFIG") 2>&1 >/dev/null ); then
  load_config "$CONFIG"; CONFIG_OK=1
fi
if [ "$CONFIG_OK" = 0 ] && [ $((${#MARKS[@]} + ${#UNMARKS[@]})) -gt 0 ]; then
  die "--mark and --unmark need a valid fleet.env (its OWNER names the state file): $CONFIG_ERR"
fi

# ------------------------------------------------------------------ state file
# state_path_ok <path>: refuse a path inside this kit or any git work tree.
state_path_ok() {
  local p=$1 d
  d=$(dirname "$p")
  while [ ! -d "$d" ]; do d=$(dirname "$d"); done
  d=$(cd "$d" && pwd -P)
  case $d/ in "$KIT"/*) die "the state file must be outside this kit: $p" ;; esac
  if git -C "$d" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    die "the state file must be outside every git work tree: $p"
  fi
}
declare -A ASKED
if [ "$CONFIG_OK" = 1 ]; then
  STATE=${ADOPT_STATE_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/adopt-the-whole-system/$CFG_OWNER.json}
  case $STATE in /*) ;; *) STATE=$PWD/$STATE ;; esac
  state_path_ok "$STATE"
  if [ -f "$STATE" ]; then
    jq -e '.done | type == "array" and all(.[]; type == "string")' "$STATE" >/dev/null 2>&1 \
      || die "$STATE is not a status state file (want {\"done\": [<step id>, ...]})"
    while IFS= read -r id; do ASKED[$id]=1; done < <(jq -r '.done[]' "$STATE")
  fi
  if [ $((${#MARKS[@]} + ${#UNMARKS[@]})) -gt 0 ]; then
    for id in "${MARKS[@]}"; do ASKED[$id]=1; did "marked step $id done (your answer)"; done
    for id in "${UNMARKS[@]}"; do unset "ASKED[$id]"; did "marked step $id not done"; done
    if [ "$DRY_RUN" = 0 ]; then
      mkdir -p "$(dirname "$STATE")"; chmod 700 "$(dirname "$STATE")"
      tmp=$(mktemp "$(dirname "$STATE")/.status.XXXXXX")
      { for id in "${IDS[@]}"; do [ -z "${ASKED[$id]:-}" ] || printf '%s\n' "$id"; done; } \
        | jq -R . | jq -s '{done: .}' > "$tmp"
      mv "$tmp" "$STATE"
    fi
  fi
fi

# ------------------------------------------------------------------ checks
declare -A DONE DETAIL VERIFY_RC VERIFY_OUT
[ "$CONFIG_OK" = 0 ] || require_tools "$GH"
check_verify() { # id groups
  local g=$2 out="$BOOT_WORK/verify.out" rc=0 summary
  if [ -z "${VERIFY_RC[$g]+set}" ]; then
    bash "$HERE/verify.sh" --config "$CONFIG" --only "$g" >"$out" 2>&1 || rc=$?
    summary=$(grep -E '^[0-9]+ passed, [0-9]+ failed, [0-9]+ skipped$' "$out" | tail -n 1 || true)
    if [ "$rc" = 0 ] && [[ $summary =~ ^([0-9]+)\ passed ]] && [ "${BASH_REMATCH[1]}" -gt 0 ]; then
      VERIFY_RC[$g]=0
    else
      VERIFY_RC[$g]=1
    fi
    VERIFY_OUT[$g]=$(grep -E '^(FAIL|ERROR): ' "$out" || true)
  fi
  if [ "${VERIFY_RC[$g]}" = 0 ]; then DONE[$1]=1; else DETAIL[$1]=${VERIFY_OUT[$g]}; fi
}
check_files() { # id "repo:path ..."
  local f missing=""
  for f in $2; do
    gh_read "repos/$CFG_OWNER/${f%%:*}/contents/${f#*:}" \
      || missing+="missing on $CFG_OWNER/${f%%:*}'s default branch: ${f#*:} (HTTP $GH_HTTP); merge the starter pull request"$'\n'
  done
  if [ -z "$missing" ]; then DONE[$1]=1; else DETAIL[$1]=${missing%$'\n'}; fi
}
for id in "${IDS[@]}"; do
  case ${CTYPE[$id]} in
    config) if [ "$CONFIG_OK" = 1 ]; then DONE[$id]=1; else DETAIL[$id]=$CONFIG_ERR; fi ;;
    verify) [ "$CONFIG_OK" = 0 ] || check_verify "$id" "${CARG[$id]}" ;;
    files)  [ "$CONFIG_OK" = 0 ] || check_files "$id" "${CARG[$id]}" ;;
    ask)    [ -z "${ASKED[$id]:-}" ] || DONE[$id]=1 ;;
  esac
done

# ------------------------------------------------------------------ report
NEXT="" N=0
for id in "${IDS[@]}"; do
  if [ -n "${DONE[$id]:-}" ]; then
    N=$((N + 1))
    if [ "${CTYPE[$id]}" = ask ]; then label="asked: done"; else label=done; fi
  elif [ "${OPT[$id]}" = yes ]; then
    label=optional
  elif [ -z "$NEXT" ]; then
    NEXT=$id label=next
  else
    label=todo
  fi
  printf '%-11s  %-3s  %s\n' "$label" "$id" "${TITLE[$id]}"
done
printf '%d of %d done\n' "$N" "${#IDS[@]}"
if [ -z "$NEXT" ]; then
  log "Next: nothing; every required step is done. bootstrap/verify.sh checks the whole setup."
  exit 0
fi
log "Next: $NEXT ${TITLE[$NEXT]} (about ${MIN[$NEXT]} minutes, ${KIND[$NEXT]})"
log "  Run: ${RUN[$NEXT]}"
log "  Read: ${LINK[$NEXT]}"
if [ -n "${DETAIL[$NEXT]:-}" ]; then
  log "  Not done yet:"
  printf '%s\n' "${DETAIL[$NEXT]}" | sed 's/^/    /'
fi
if [ "${CTYPE[$NEXT]}" = ask ]; then
  log "  status.sh cannot see this one. Once it is done: bootstrap/status.sh --mark $NEXT"
fi
exit 0
