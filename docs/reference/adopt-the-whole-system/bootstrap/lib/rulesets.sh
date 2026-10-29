# lib/rulesets.sh: build the ruleset bodies for one repo and compare a live
# ruleset with its file. Sourced after lib/common.sh. Used by apply-rulesets.sh
# and verify.sh.
#
# rulesets/persistent-branches.json is the full form (deletion,
# non_fast_forward, update, creation, and an App bypass). Without creation,
# any workflow on a pushed branch could create a NEW persistent/<x> branch
# that a later reader trusts as the App's output. Which App may write
# persistent/** depends on the repo, so the bypass is built per repo and the
# file's own bypass_actors (placeholders 111111 / 222222, or the IDs
# copy-starter.sh filled in) are never applied as they stand:
#   skills-evals    loop-writer    (LOOP_WRITER_APP_ID; persistent/eval-results)
#   agent-guidance  guidance-sync  (GUIDANCE_SYNC_APP_ID; persistent/drift-report)
#   any other repo  no App: deletion and non_fast_forward, no bypass
# A writer repo gets its full ruleset as soon as ITS App ID is in fleet.env;
# until then, deletion and non_fast_forward with no bypass.

RULESET_DIR=${RULESET_DIR:-$HERE/rulesets}

# Repos whose agent-checks.yml has no privacy job (guide 3.4): leave the
# privacy context out of their required checks.
NO_PRIVACY_JOB=(agent-skills-private)

# Required contexts one repo adds to the phase 4 checks, as "repo|context":
# the job name a required workflow of that repo's own reports. agent-skills'
# plugin-runtime-review.yml always reports plugin-runtime-review (its detect
# job decides whether the owner's approval is needed).
EXTRA_CHECKS=("agent-skills|plugin-runtime-review")

rulesets_apply_here() { # repo: rulesets are enforced on private repos only on a paid plan
  [ "$CFG_PLAN" = paid ] || [ "$(repo_visibility "$1")" = public ]
}

# ruleset_names: the rulesets every repo gets.
RULESET_NAMES=(default-branch persistent-branches)

# build_ruleset <name> <repo> <with_required_check 0|1>: set RULESET_JSON to
# the body (no subshell, so the note survives). Sets RULESET_NOTE to a one-line explanation when it departs from the file.
build_ruleset() {
  local name=$1 repo=$2 with_check=$3 f
  RULESET_NOTE=""
  f="$RULESET_DIR/$name.json"
  [ -f "$f" ] || die "missing ruleset file $f"
  case $name in
    default-branch)
      if [ "$with_check" = 1 ]; then
        local rule="$RULESET_DIR/phase4/required-status-checks.json" drop=false r extra=() e
        [ -f "$rule" ] || die "missing $rule"
        for r in "${NO_PRIVACY_JOB[@]}"; do [ "$r" = "$repo" ] && drop=true; done
        for e in "${EXTRA_CHECKS[@]}"; do [ "${e%%|*}" = "$repo" ] && extra+=("${e#*|}"); done
        RULESET_JSON=$(jq -c --slurpfile rule "$rule" --argjson drop "$drop" \
          --argjson extra "$(printf '%s\n' "${extra[@]}" | jq -R . | jq -sc 'map(select(. != ""))')" '
          ($rule[0] | if $drop then .parameters.required_status_checks |= map(select(.context != "privacy")) else . end
            | .parameters.required_status_checks += [$extra[] | {context: ., integration_id: 15368}]) as $r
          | .rules = ([.rules[] | select(.type != "required_status_checks")] + [$r])' "$f")
        RULESET_NOTE="with the phase 4 required checks"
        if [ "$drop" = true ]; then RULESET_NOTE="$RULESET_NOTE (agent-checks only: this repo has no privacy job)"; fi
        if [ "${#extra[@]}" -gt 0 ]; then RULESET_NOTE="$RULESET_NOTE, plus ${extra[*]}"; fi
      else
        RULESET_JSON=$(jq -c . "$f")
      fi
      ;;
    persistent-branches)
      local key="" app="" id=""
      case $repo in
        skills-evals) key=LOOP_WRITER_APP_ID app=loop-writer ;;
        agent-guidance) key=GUIDANCE_SYNC_APP_ID app=guidance-sync ;;
      esac
      [ -z "$key" ] || { local v="CFG_$key"; id=${!v}; }
      if [ -n "$id" ]; then
        RULESET_NOTE="full: $app may write persistent/**"
        RULESET_JSON=$(jq -c --argjson id "$id" '
          .bypass_actors = [{actor_id: $id, actor_type: "Integration", bypass_mode: "always"}]
          | .rules = [{type: "deletion"}, {type: "non_fast_forward"}, {type: "update"}, {type: "creation"}]' "$f")
      else
        if [ -n "$key" ]; then
          RULESET_NOTE="deletion and non_fast_forward only: $key is not in fleet.env yet (guide 3.1)"
        elif [ "$repo" = agent-skills-private ]; then
          # The session miner's lessons are pushed as you (guide 3.1, 10.4).
          RULESET_NOTE="deletion and non_fast_forward only: you push persistent/lessons from your machine"
        else
          RULESET_NOTE="deletion and non_fast_forward only: no App writes persistent/** here"
        fi
        RULESET_JSON=$(jq -c '.bypass_actors = [] | .rules = [{type: "deletion"}, {type: "non_fast_forward"}]' "$f")
      fi
      ;;
    *) die "unknown ruleset $name" ;;
  esac
}

# ruleset_matches <want-json> <live-json>: 0 when the live ruleset carries every
# field of the file with the same value, the same set of rule types and the
# same bypass actors. GitHub adds default fields on read-back (ids, links,
# parameters the file leaves out); those are ignored, nothing else is.
RULESET_JQ='
def norm:
  { name, target, enforcement,
    conditions: (.conditions // {}),
    bypass_actors: ((.bypass_actors // []) | map({actor_id, actor_type, bypass_mode}) | sort_by(.actor_id)),
    rules: ((.rules // [])
      | map(if .parameters.required_status_checks then .parameters.required_status_checks |= sort_by(.context) else . end)
      | sort_by(.type)) };
def sub($a; $b):
  if ($a | type) == "object" then
    (($b | type) == "object") and ([$a | keys[] as $k | ($b | has($k)) and sub($a[$k]; $b[$k])] | all)
  elif ($a | type) == "array" then
    (($b | type) == "array") and (($a | length) == ($b | length))
      and ([range(0; $a | length) as $i | sub($a[$i]; $b[$i])] | all)
  else $a == $b end;
($want | norm) as $w | ($live | norm) as $l
| ([$w.rules[].type] == [$l.rules[].type]) and ($w.bypass_actors == $l.bypass_actors) and sub($w; $l)
'
ruleset_matches() {
  jq -n -e --argjson want "$1" --argjson live "$2" "$RULESET_JQ" >/dev/null
}

# ruleset_diff <want-json> <live-json>: a short, value-free description.
ruleset_diff() {
  jq -n -r --argjson want "$1" --argjson live "$2" '
    "rule types: file \([$want.rules[].type] | sort | join(",")), live \([($live.rules // [])[].type] | sort | join(","))"
    + "; bypass actors: file \([$want.bypass_actors[]?.actor_id] | sort | join(",")), live \([($live.bypass_actors // [])[]?.actor_id] | sort | join(","))"'
}

# find_ruleset_id <repo-full-name> <name>: print the id, or nothing.
find_ruleset_id() {
  if ! gh_read "repos/$1/rulesets?includes_parents=false"; then
    # A dry run before create-repos.sh: the repo is not there yet.
    [ "$GH_HTTP" = 404 ] && [ "$DRY_RUN" = 1 ] && return 0
    die "could not read rulesets of $1 (HTTP $GH_HTTP)"
  fi
  printf '%s' "$GH_OUT" | jq -r --arg n "$2" '[.[] | select(.name == $n) | .id][0] // empty'
}
