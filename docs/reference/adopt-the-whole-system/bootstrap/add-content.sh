#!/usr/bin/env bash
# add-content.sh [--config fleet.env] [--dry-run] --items <ids|recommended|none>
#
# Adds the optional skills, guidance sections and git-hook checks you choose
# (bootstrap/content-catalog.json, tiers recommended and opinionated) to this
# kit copy's starter/, before copy-starter.sh copies the trees to GitHub:
#
#   --items recommended            every recommended item
#   --items writing-adrs,security  the items with those ids (and recommended,
#                                  to add the recommended ones as well)
#   --items none                   nothing; records that you chose nothing
#
# For each item it copies library/<kind>/<id>/ into the starter trees (the
# skill or section or check, its eval fixtures and schedule rows, its
# eval-coverage row, its impact-log entry and its tests), then rebuilds the
# generated files those feed: the guidance mirror and agent-checks runner in
# every tree, each AGENTS.md, the section byte counts, the registry's README
# table and skills.lock (lib/content.py; library/README.md lists the steps).
# Core items are always in starter/ already.
#
# Records what is installed in <state file without .json>.content-choices.json,
# beside status.sh's answers, which closes the quick start's content step.
# Idempotent: a second run changes nothing. --dry-run lists the files it
# would add or change in each starter folder and writes nothing. An unknown
# id is refused. Run it in a copy of the kit outside every git repository
# (the README's "Start here"); inside one it refuses. Once the repos exist
# it changes only starter/ and says which repos need a pull request; it
# never writes to GitHub. Needs: bash 4.4+, git, python3 with PyYAML, gh
# (only to see whether the repos exist).

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

ITEMS=""
parse_common_args "$@"
set -- "${REST_ARGS[@]}"
while [ $# -gt 0 ]; do
  case $1 in
    --items) [ $# -ge 2 ] || die "--items needs a list (ids, recommended or none)"; ITEMS=$2; shift ;;
    --items=*) ITEMS=${1#--items=} ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
[ -n "$ITEMS" ] || { usage; die "give --items <ids|recommended|none>"; }
require_tools git python3
load_config "$CONFIG"
if git -C "$KIT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die "this kit is inside a git work tree ($KIT); copy its folder outside every repository first (cp -R <this folder> ~/adopt-kit) and run it there"
fi
choices_file

args=(install --kit "$KIT" --items "$ITEMS" --record "$CHOICES")
[ "$DRY_RUN" = 0 ] || args+=(--dry-run)
out="$BOOT_WORK/content.out"
python3 -I -B "$HERE/lib/content.py" "${args[@]}" >"$out" || { cat "$out"; exit 1; }
grep -v '^changed-trees: ' "$out" || true
read -r -a trees <<<"$(sed -n 's/^changed-trees: //p' "$out")"
[ ${#trees[@]} -gt 0 ] || exit 0

# Once a repo exists, its copy of the tree is already on GitHub: the change
# reaches it only through a pull request.
command -v "$GH" >/dev/null 2>&1 || { log "(gh is not installed, so whether the repos exist was not checked)"; exit 0; }
for t in "${trees[@]}"; do
  for r in "${FLEET_REPOS[@]}"; do
    [ "$(kit_tree "$r")" = "$t" ] || continue
    gh_read "repos/$CFG_OWNER/$r" || continue
    log "$CFG_OWNER/$r already exists, so this reached only starter/$t. Open a pull request on it"
    log "  (never a push to its default branch) that adds the starter/$t files listed above, with"
    log "  your names as copy-starter.sh writes them; when acsi-agentskills changes, run"
    log "  bootstrap/regenerate.sh skills-locks after it merges."
  done
done
