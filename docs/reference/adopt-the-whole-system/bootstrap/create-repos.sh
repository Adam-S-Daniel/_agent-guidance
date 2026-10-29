#!/usr/bin/env bash
# create-repos.sh [--config fleet.env] [--dry-run] [--with-optional] [<repo>...]
#
# Creates the six repos the kit exports, under OWNER, with the names the
# owner's own repos have (export/DECISIONS.md C1): _agent-guidance,
# repo-settings, acsi-agentskills, acsi-agentskills-private, skills-evals and
# wsl-automation. Default: all six. --with-optional (or naming it) also
# creates waiting-feed, the private repo the waiting-on-you feed publishes
# to (export/REGENERATE.md, wsl-automation), which no starter tree fills.
#
#   * Visibility: acsi-agentskills-private, repo-settings and waiting-feed are
#     always private, acsi-agentskills always public. On PLAN=free every other repo
#     is public; on PLAN=paid, _agent-guidance, skills-evals and
#     wsl-automation follow VISIBILITY_* in fleet.env (default public).
#   * A first commit (a README), so a clone has a default branch to branch
#     from. The README is generated_readme (lib/common.sh), whose last line is
#     a fixed marker: copy-starter.sh replaces exactly that file without --force.
#   * Labels agent-ready and on-hold (LABELS in lib/common.sh): the fleet
#     worker reads both and no fleet.yml entry declares them.
#
# Everything else a repo's settings hold (merge methods, Actions policy,
# security features, the rulesets and the agent-authored label) is
# repo-settings' fleet.yml, applied by apply-settings.sh with the exported
# engine, as the owner's own fleet is.
#
# Idempotent: it reads first and writes only what differs; a second run says
# "Nothing to change". An existing repo with the wrong visibility is reported,
# never changed. --dry-run makes the same reads and prints the writes instead.
# Needs: gh (authenticated as the owner), jq.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

WITH_OPTIONAL=0
parse_common_args "$@"
REPOS=()
for a in "${REST_ARGS[@]}"; do
  case $a in
    --with-optional) WITH_OPTIONAL=1 ;;
    -*) die "unknown option: $a" ;;
    *) is_known_repo "$a" || die "unknown repo: $a (expected one of: ${FLEET_REPOS[*]} ${OPTIONAL_REPOS[*]})"; REPOS+=("$a") ;;
  esac
done
require_tools jq "$GH"
load_config "$CONFIG"
O=$CFG_OWNER
if [ ${#REPOS[@]} = 0 ]; then
  REPOS=("${FLEET_REPOS[@]}")
  [ "$WITH_OPTIONAL" = 0 ] || REPOS+=("${OPTIONAL_REPOS[@]}")
fi

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
      warn "$O/$r exists but is $have; the kit wants it $want. Not changed: fix it in Settings, General, Danger zone."
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
}

readme_body() { # name: the contents API body for the generated README
  jq -n --arg c "$(generated_readme "$1" | base64 | tr -d '\n')" \
    '{message: "Add a README so the repo has a first commit", content: $c}'
}

ensure_first_commit() { # name
  local r=$1
  if gh_read "repos/$O/$r/commits?per_page=1"; then
    skip "$O/$r has a first commit"; return 0
  fi
  [ "$GH_HTTP" = 409 ] || die "could not read the commits of $O/$r (HTTP $GH_HTTP)"
  gh_write_api PUT "repos/$O/$r/contents/README.md" "$(readme_body "$r")"
  did "added a README to the empty repo $O/$r"
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
  if is_fleet_repo "$r"; then ensure_labels "$r"; fi
done
finish
if [ "$PROBLEMS" -gt 0 ]; then
  warn "$PROBLEMS repo(s) need a manual fix (see above)"; exit 1
fi
