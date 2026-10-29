#!/usr/bin/env bash
# regenerate.sh [--config fleet.env] [--dry-run] [--check] <task>
#
# Rebuilds, after adoption, the generated files the export could not build
# offline (export/REGENERATE.md), with each repo's own tooling, and proposes
# each repo's result as a pull request on branch regenerate/<task>. --check
# changes nothing: it reads your repos' default branches and exits 0 when the
# task is done there, 1 when it is not (verify.sh regen:<task> runs this).
#
# Tasks, in the order the quick start runs them:
#
#   skills-locks   skills.lock in every repo. agentskills: its own generator
#                  pins its HEAD (`scripts/generate_skills_lock.py`), checked
#                  with --check and --check-current. agentskills-private: the
#                  same file, copied. _agent-guidance, repo-settings and
#                  wsl-automation: agentskills' generator run with --repo on
#                  a clone of your agentskills, keeping each lock's bundles.
#                  Merge the agentskills pull request first, then rerun for
#                  the copy in agentskills-private.
#   hook-pin       _agent-guidance repos.yml skills_bootstrap.ref: the
#                  agentskills commit whose .claude/hooks/skills-bootstrap.sh
#                  has the sha256 repos.yml already records (the export
#                  computed it). Refuses when your registry's hook bytes
#                  differ. Afterwards the nightly skills-lock-bump.yml keeps it
#                  current with scripts/bump-hook-pin.sh.
#   run-health-sha the forty-zero placeholder in scheduled-run-health.yml
#                  (repo-settings, skills-evals, agentskills) becomes your
#                  _agent-guidance default branch's commit: a cross-repo
#                  reusable workflow takes a full sha.
#   codex-pin      wsl-automation scripts/sync-codex-cloud-environments.sh:
#                  bootstrap_revision and bootstrap_sha256 become your
#                  agentskills HEAD and its hook's digest, then the script's
#                  own test (tests/sync-codex-cloud-environments.test.sh) runs.
#   actions-lock   `gh actions-lock --no-narrow` in each repo that has
#                  .github/workflows/actions.lock, proposing any change;
#                  --check runs `gh actions-lock --verify`. Needs the gh
#                  extension: gh extension install github/gh-actions-lock.
#
# Each task needs the starter pull requests of the repos it reads merged.
# Idempotent: a repo already current gets no commit and no pull request.
# Pushes only to regenerate/<task>, never with force, and checks the commit
# reached it (lib/pr.sh). Needs: git, gh, jq, python3 (PyYAML for hook-pin).

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"
# shellcheck source=lib/pr.sh
. "$HERE/lib/pr.sh"

CHECK=0 TASK=""
parse_common_args "$@"
for a in "${REST_ARGS[@]}"; do
  case $a in
    --check) CHECK=1 ;;
    -*) die "unknown option: $a" ;;
    *) [ -z "$TASK" ] || die "give one task"; TASK=$a ;;
  esac
done
[ -n "$TASK" ] || { usage; die "give a task: ${REGEN_TASKS[*]}"; }
known=0; for t in "${REGEN_TASKS[@]}"; do [ "$t" = "$TASK" ] && known=1; done
[ "$known" = 1 ] || die "unknown task: $TASK (expected one of: ${REGEN_TASKS[*]})"
require_tools git jq python3 "$GH"
load_config "$CONFIG"
O=$CFG_OWNER
ZERO40=0000000000000000000000000000000000000000
NOT_DONE=0

# clone <repo>: a full clone of OWNER/<repo> at $BOOT_WORK/<repo> (once).
clone() {
  local d="$BOOT_WORK/$1"
  [ -d "$d" ] || clone_repo "$O/$1" "$d"
  CLONE=$d
}
# need <repo> <path>: the starter must be merged for this task to make sense.
need() {
  clone "$1"
  [ -e "$CLONE/$2" ] || die "$O/$1 has no $2 on its default branch: merge its starter pull request first"
}
notdone() { NOT_DONE=$((NOT_DONE + 1)); printf 'NOT DONE: %s\n' "$*"; }
current() { printf 'current: %s\n' "$*"; }

# propose <repo> <title> <body> <edit function> <path>...: run the edit on a
# regenerate/<task> branch of the clone and open a pull request for it.
propose() {
  local repo=$1 title=$2 body=$3 fn=$4; shift 4
  clone "$repo"
  local d=$CLONE default
  default=$(default_branch_of "$d")
  start_branch "$d" "$default" "regenerate/$TASK"
  "$fn" "$d"
  if git -C "$d" diff --quiet -- "$@" && [ -z "$(git -C "$d" status --porcelain -- "$@")" ] \
     && [ -z "$(git -C "$d" rev-list "origin/$default..HEAD")" ]; then
    skip "$O/$repo: already current"; return 0
  fi
  git -C "$d" --no-pager diff --stat -- "$@"
  open_pr "$d" "$O/$repo" "$default" "regenerate/$TASK" "$title" "$body" "$@"
}

# ------------------------------------------------------------ skills-locks
REG_GEN=scripts/generate_skills_lock.py
lock_bundles() { jq -r '.bundles | join(",")' "$1"; }

edit_registry_lock() {
  (cd "$1" && python3 -I "$REG_GEN" --registry "$O/agentskills" --bundles "$(lock_bundles skills.lock)" -o skills.lock >/dev/null)
}
edit_private_lock() { cp "$REG_DIR/skills.lock" "$1/skills.lock"; }
edit_consumer_lock() {
  python3 -I "$REG_DIR/$REG_GEN" --repo "$REG_DIR" --registry "$O/agentskills" \
    --bundles "$(lock_bundles "$1/skills.lock")" -o "$1/skills.lock" >/dev/null
}

task_skills_locks() {
  local r
  need agentskills "$REG_GEN"; REG_DIR=$CLONE
  if [ "$CHECK" = 1 ]; then
    if (cd "$REG_DIR" && python3 -I "$REG_GEN" --check >/dev/null 2>&1 && python3 -I "$REG_GEN" --check-current >/dev/null 2>&1); then
      current "$O/agentskills skills.lock pins a commit of its own and matches it"
    else notdone "$O/agentskills skills.lock (still the export's forty zeros, or stale)"; fi
    clone agentskills-private
    if cmp -s "$REG_DIR/skills.lock" "$CLONE/skills.lock" && ! grep -qF "$ZERO40" "$CLONE/skills.lock"; then
      current "$O/agentskills-private skills.lock is agentskills' copy"
    else notdone "$O/agentskills-private skills.lock differs from agentskills'"; fi
    for r in _agent-guidance repo-settings wsl-automation; do
      clone "$r"
      if python3 -I "$REG_DIR/$REG_GEN" --repo "$REG_DIR" --check -o "$CLONE/skills.lock" >/dev/null 2>&1; then
        current "$O/$r skills.lock pins your agentskills"
      else notdone "$O/$r skills.lock (still pins the source registry's commit)"; fi
    done
    return 0
  fi
  local body="Regenerates skills.lock with agentskills' own scripts/generate_skills_lock.py, pinning your registry (the kit's export/REGENERATE.md). No skill content changes."
  if grep -qF "$ZERO40" "$REG_DIR/skills.lock" || ! (cd "$REG_DIR" && python3 -I "$REG_GEN" --check >/dev/null 2>&1); then
    propose agentskills "Pin skills.lock to this registry" "$body" edit_registry_lock skills.lock
    log "Merge that pull request, then run this again for agentskills-private and the consumers."
    return 0
  fi
  current "$O/agentskills skills.lock"
  propose agentskills-private "Copy agentskills' skills.lock" "$body" edit_private_lock skills.lock
  for r in _agent-guidance repo-settings wsl-automation; do
    need "$r" skills.lock
    propose "$r" "Pin skills.lock to your agentskills" "$body" edit_consumer_lock skills.lock
  done
}

# ------------------------------------------------------------ hook-pin
HOOK=.claude/hooks/skills-bootstrap.sh
repos_yml_pin() { # repos.yml -> "ref sha256"
  python3 -I - "$1" <<'PY'
import sys, yaml
b = (yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}).get("skills_bootstrap") or {}
print(b.get("ref", ""), b.get("sha256", ""))
PY
}
hook_sha_at() { git -C "$REG_DIR" show "$1:$HOOK" 2>/dev/null | sha256_stdin; }
edit_hook_pin() {
  python3 -I - "$1/repos.yml" "$NEW_REF" <<'PY'
import re, sys, yaml
path, ref = sys.argv[1:3]
lines = open(path, encoding="utf-8").read().split("\n")
start = lines.index("skills_bootstrap:")
for i in range(start + 1, len(lines)):
    if lines[i] and not lines[i].startswith((" ", "#")):
        sys.exit("repos.yml: skills_bootstrap has no ref line")
    if re.fullmatch(r"  ref: [0-9a-f]{40}", lines[i]):
        lines[i] = f"  ref: {ref}"
        break
open(path, "w", encoding="utf-8").write("\n".join(lines))
got = (yaml.safe_load(open(path, encoding="utf-8")) or {})["skills_bootstrap"]["ref"]
if got != ref:
    sys.exit("repos.yml: the rewrite did not take")
PY
}
task_hook_pin() {
  local ref sum head
  need agentskills "$HOOK"; REG_DIR=$CLONE
  need _agent-guidance repos.yml
  read -r ref sum < <(repos_yml_pin "$CLONE/repos.yml")
  [[ $sum =~ ^[0-9a-f]{64}$ ]] || die "repos.yml skills_bootstrap.sha256 is not a sha256"
  if git -C "$REG_DIR" cat-file -e "$ref^{commit}" 2>/dev/null && [ "$(hook_sha_at "$ref")" = "$sum" ]; then
    current "$O/_agent-guidance repos.yml pins your agentskills hook"; return 0
  fi
  if [ "$CHECK" = 1 ]; then notdone "$O/_agent-guidance repos.yml skills_bootstrap.ref is not a commit of your agentskills with that hook"; return 0; fi
  head=$(git -C "$REG_DIR" rev-parse HEAD)
  [ "$(hook_sha_at "$head")" = "$sum" ] \
    || die "your agentskills $HOOK at $head does not have the sha256 repos.yml records; the two copies have drifted. Run _agent-guidance's scripts/bump-hook-pin.sh to re-pin both ref and sha256 after reviewing the hook."
  NEW_REF=$head
  propose _agent-guidance "Pin the skills-bootstrap hook to your agentskills" \
    "Sets repos.yml skills_bootstrap.ref to $head, the agentskills commit whose $HOOK has the sha256 repos.yml already records (the kit's export/REGENERATE.md)." \
    edit_hook_pin repos.yml
}

# ------------------------------------------------------------ run-health-sha
RH=.github/workflows/scheduled-run-health.yml
edit_run_health() {
  # -i.bak, then removing the backup: the one in-place form GNU and BSD (macOS) sed share.
  sed -i.bak "s|scheduled-run-health-reusable.yml@$ZERO40|scheduled-run-health-reusable.yml@$AG_SHA|" "$1/$RH"
  rm -f "$1/$RH.bak"
}
task_run_health_sha() {
  local r pinned
  need _agent-guidance .github/workflows/scheduled-run-health-reusable.yml
  AG_SHA=$(git -C "$CLONE" rev-parse HEAD); local ag=$CLONE
  for r in repo-settings skills-evals agentskills; do
    need "$r" "$RH"
    pinned=$(grep -oE 'scheduled-run-health-reusable\.yml@[0-9a-f]{40}' "$CLONE/$RH" | head -n1 | cut -d@ -f2 || true)
    if [ -n "$pinned" ] && [ "$pinned" != "$ZERO40" ] && git -C "$ag" cat-file -e "$pinned^{commit}" 2>/dev/null; then
      current "$O/$r $RH calls your _agent-guidance at $pinned"; continue
    fi
    if [ "$CHECK" = 1 ]; then notdone "$O/$r $RH still calls the reusable at ${pinned:-no} sha"; continue; fi
    propose "$r" "Call your _agent-guidance's scheduled-run-health reusable" \
      "Replaces the export's forty-zero placeholder with $AG_SHA, your _agent-guidance default branch (a cross-repo reusable workflow takes a full sha; the kit's export/REGENERATE.md)." \
      edit_run_health "$RH"
  done
}

# ------------------------------------------------------------ codex-pin
CX=scripts/sync-codex-cloud-environments.sh
edit_codex_pin() {
  sed -i.bak -e "s/^readonly bootstrap_revision='[0-9a-f]\{40\}'/readonly bootstrap_revision='$CX_REV'/" \
    -e "s/^readonly bootstrap_sha256='[0-9a-f]\{64\}'/readonly bootstrap_sha256='$CX_SUM'/" "$1/$CX"
  rm -f "$1/$CX.bak"
  (cd "$1" && bash tests/sync-codex-cloud-environments.test.sh >"$BOOT_WORK/cx-test.out" 2>&1) \
    || { tail -n 20 "$BOOT_WORK/cx-test.out" >&2; die "tests/sync-codex-cloud-environments.test.sh failed with the new pin"; }
  log "tests/sync-codex-cloud-environments.test.sh passed with the new pin"
}
task_codex_pin() {
  local rev sum
  need agentskills "$HOOK"; REG_DIR=$CLONE
  need wsl-automation "$CX"
  rev=$(sed -n "s/^readonly bootstrap_revision='\([0-9a-f]\{40\}\)'$/\1/p" "$CLONE/$CX")
  sum=$(sed -n "s/^readonly bootstrap_sha256='\([0-9a-f]\{64\}\)'$/\1/p" "$CLONE/$CX")
  [ -n "$rev" ] && [ -n "$sum" ] || die "$CX has no bootstrap_revision/bootstrap_sha256 lines"
  if [ "$rev" != "$ZERO40" ] && git -C "$REG_DIR" cat-file -e "$rev^{commit}" 2>/dev/null && [ "$(hook_sha_at "$rev")" = "$sum" ]; then
    current "$O/wsl-automation $CX pins your agentskills hook"; return 0
  fi
  if [ "$CHECK" = 1 ]; then notdone "$O/wsl-automation $CX (bootstrap pin is $([ "$rev" = "$ZERO40" ] && echo 'still zeros' || echo 'not your registry'))"; return 0; fi
  CX_REV=$(git -C "$REG_DIR" rev-parse HEAD)
  CX_SUM=$(hook_sha_at "$CX_REV")
  propose wsl-automation "Pin the Codex Cloud bootstrap to your agentskills" \
    "Sets bootstrap_revision to $CX_REV and bootstrap_sha256 to its $HOOK digest (the kit's export/REGENERATE.md). tests/sync-codex-cloud-environments.test.sh passes with it." \
    edit_codex_pin "$CX"
}

# ------------------------------------------------------------ actions-lock
LOCK=.github/workflows/actions.lock
edit_actions_lock() {
  (cd "$1" && "$GH" actions-lock --no-narrow >"$BOOT_WORK/al.out" 2>&1) \
    || { tail -n 20 "$BOOT_WORK/al.out" >&2; die "gh actions-lock --no-narrow failed in $(basename "$1")"; }
}
task_actions_lock() {
  local r
  "$GH" actions-lock --help >/dev/null 2>&1 || die "gh actions-lock is not installed: gh extension install github/gh-actions-lock"
  for r in "${FLEET_REPOS[@]}"; do
    clone "$r"
    [ -f "$CLONE/$LOCK" ] || { log "skip: $O/$r has no $LOCK"; continue; }
    if [ "$CHECK" = 1 ]; then
      if (cd "$CLONE" && "$GH" actions-lock --verify >/dev/null 2>&1); then current "$O/$r $LOCK verifies"
      else notdone "$O/$r $LOCK (gh actions-lock --verify reports findings)"; fi
      continue
    fi
    propose "$r" "Regenerate actions.lock" \
      "Runs gh actions-lock --no-narrow, as the repo's AGENTS.md says; review this diff as you would a sha pin (the kit's export/REGENERATE.md)." \
      edit_actions_lock "$LOCK"
  done
}

[ "$CHECK" = 0 ] || [ "$DRY_RUN" = 0 ] || die "--check is read-only already; drop --dry-run"
log "Owner $O, task $TASK$([ "$CHECK" = 1 ] && echo ', CHECK')$([ "$DRY_RUN" = 1 ] && echo ', DRY RUN')"
case $TASK in
  skills-locks) task_skills_locks ;;
  hook-pin) task_hook_pin ;;
  run-health-sha) task_run_health_sha ;;
  codex-pin) task_codex_pin ;;
  actions-lock) task_actions_lock ;;
esac
if [ "$CHECK" = 1 ]; then
  if [ "$NOT_DONE" = 0 ]; then log "$TASK: done"; exit 0; fi
  log "$TASK: $NOT_DONE item(s) not done; run bootstrap/regenerate.sh $TASK"; exit 1
fi
finish
