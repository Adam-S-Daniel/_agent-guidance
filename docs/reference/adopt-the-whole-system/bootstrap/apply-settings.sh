#!/usr/bin/env bash
# apply-settings.sh [--config fleet.env] [--dry-run | --check] [--owner LOGIN]
#
# Applies repo-settings' fleet.yml to your repos with repo-settings' own
# engine, the way the owner's fleet is run: merge methods, auto-merge, the
# Actions policy (full-SHA pins, read-only token, fork-run approval), the
# security features, the agent-authored and eval-scaffold labels, and the
# rulesets with each repo's real required checks (agentskills: privacy,
# pytest, pytest-windows, plugin-runtime-approval, agent-checks; skills-evals:
# test, agent-checks; and so on, as fleet.yml lists them). The kit keeps no
# ruleset files of its own.
#
#   1. clones OWNER/repo-settings (its default branch: merge the repo-settings
#      starter pull request first) to a temp dir;
#   2. refuses while GUIDANCE_SYNC_APP_ID or ROSTER_WRITER_APP_ID is empty in
#      fleet.env: fleet.yml names both Apps as ruleset bypass actors, and the
#      engine refuses to plan without their ids (export/DECISIONS.md C12).
#      This is why the Apps come before the first apply;
#   3. refuses while fleet.yml names a repo of this owner that does not
#      exist (the example platform and its sites, until you remove them:
#      export/REGENERATE.md, "repo-settings/fleet.yml's repo list");
#   4. runs `scripts/repo_settings.py apply --config repo-settings/fleet.yml
#      --owner <owner> --cms-label-only skip` (with --dry-run, the engine's
#      own preview), as repo-settings.yml's apply job does;
#   5. reads back with `scripts/repo_settings.py diff`, which exits 0 only
#      when nothing drifts.
#
# The engine calls `gh api` with your gh login, which must be an admin of
# every repo it touches. --owner LOGIN applies another owner's repos (your
# OWNER_TWO): sign gh in as that account first (gh auth switch). After this
# first apply, repo-settings.yml applies on every merge, behind the
# fleet-apply environment's approval.
#
# --check runs steps 1 to 3 and 5 only: read-only, exit 0 when nothing drifts
# (verify.sh's settings group). Idempotent: the engine writes only what differs. Needs: git, gh, python3
# with PyYAML (repo-settings' requirements.txt).

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

TARGET="" CHECK=0
parse_common_args "$@"
set -- "${REST_ARGS[@]}"
while [ $# -gt 0 ]; do
  case $1 in
    --owner) [ $# -ge 2 ] || die "--owner needs a login"; TARGET=$2; shift 2 ;;
    --check) CHECK=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
require_tools git python3 "$GH"
load_config "$CONFIG"
TARGET=${TARGET:-$CFG_OWNER}
known=0
while IFS= read -r o; do [ "${o,,}" = "${TARGET,,}" ] && known=1; done < <(owners)
[ "$known" = 1 ] || die "--owner must be OWNER or OWNER_TWO from fleet.env"
python3 -c 'import yaml' 2>/dev/null || die "the engine needs PyYAML: python3 -m pip install -r requirements.txt in repo-settings"

missing=()
[ -n "$CFG_GUIDANCE_SYNC_APP_ID" ] || missing+=(GUIDANCE_SYNC_APP_ID)
[ -n "$CFG_ROSTER_WRITER_APP_ID" ] || missing+=(ROSTER_WRITER_APP_ID)
[ ${#missing[@]} = 0 ] || die "${missing[*]} empty in fleet.env: fleet.yml names those Apps as ruleset bypass actors and the engine refuses to plan without them. Run create-apps.sh first."

RS="$BOOT_WORK/repo-settings"
clone_repo "$CFG_OWNER/repo-settings" "$RS" --depth 1
ENGINE="$RS/scripts/repo_settings.py" FLEET="$RS/repo-settings/fleet.yml"
[ -f "$ENGINE" ] && [ -f "$FLEET" ] \
  || die "$CFG_OWNER/repo-settings has no scripts/repo_settings.py or repo-settings/fleet.yml on its default branch: merge the repo-settings starter pull request first"

# The repos fleet.yml declares for this owner (names only).
mapfile -t DECLARED < <(python3 -I - "$FLEET" "$TARGET" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
owner = sys.argv[2].lower()
for r in doc.get("repos") or []:
    name = str((r or {}).get("name", ""))
    if name.split("/", 1)[0].lower() == owner:
        print(name)
PY
)
[ ${#DECLARED[@]} -gt 0 ] || die "fleet.yml in $CFG_OWNER/repo-settings declares no repo of $TARGET"
absent=()
for r in "${DECLARED[@]}"; do
  if gh_read "repos/$r"; then continue; fi
  [ "$GH_HTTP" = 404 ] || die "could not read $r (HTTP $GH_HTTP)"
  absent+=("$r")
done
if [ ${#absent[@]} -gt 0 ]; then
  for r in "${absent[@]}"; do printf 'MISSING: fleet.yml declares %s, which this gh login cannot see (HTTP 404)\n' "$r" >&2; done
  die "remove the repos you do not have from repo-settings/fleet.yml (and their rows in tests/test_repo_settings.py) in a pull request, or create them; then run this again"
fi
log "fleet.yml declares ${#DECLARED[@]} repo(s) of $TARGET; all exist"

run_engine() { # subcommand [args]
  env GUIDANCE_SYNC_APP_ID="$CFG_GUIDANCE_SYNC_APP_ID" ROSTER_WRITER_APP_ID="$CFG_ROSTER_WRITER_APP_ID" \
    python3 -I "$ENGINE" "$1" --config "$FLEET" --owner "$TARGET" "${@:2}"
}
[ "$CHECK" = 0 ] || [ "$DRY_RUN" = 0 ] || die "--check is read-only already; drop --dry-run"
if [ "$CHECK" = 0 ] && [ "$DRY_RUN" = 1 ]; then
  log "== engine preview (apply --dry-run)"
  run_engine apply --dry-run --cms-label-only skip || die "the engine's preview failed (see above)"
  log "Dry run: the engine wrote nothing."
  exit 0
fi
if [ "$CHECK" = 0 ]; then
  log "== apply"
  run_engine apply --cms-label-only skip || die "the engine's apply failed (see above)"
  WRITES=$((WRITES + 1))
fi
log "== read back (diff)"
rc=0
run_engine diff > "$BOOT_WORK/diff.out" 2>&1 || rc=$?
case $rc in
  0) log "no drift: every repo of $TARGET matches fleet.yml" ;;
  1) cat "$BOOT_WORK/diff.out"; die "$([ "$CHECK" = 1 ] && echo "the repos drift from fleet.yml; run apply-settings.sh" || echo "drift remains after apply") (see above)" ;;
  *) cat "$BOOT_WORK/diff.out"; die "the engine's diff failed (exit $rc)" ;;
esac
[ "$CHECK" = 1 ] || finish
