#!/usr/bin/env bash
# copy-starter.sh [--config fleet.env] [--dry-run] [--force] <starter-subdir> <owner/repo>
#
# Copies one exported starter tree (for example starter/agent-guidance, the
# export of _agent-guidance) into your repo through a pull request:
#
#   1. copies the tree to a temp dir (deleted on exit), keeping symlinks
#      (each must be relative and stay inside the tree) and skipping caches
#      and local junk (__pycache__/, *.pyc, .pytest_cache/, node_modules/,
#      .DS_Store);
#   2. gives it your names with lib/substitute.py, in text and in file and
#      folder names: every form of the invented owners the export wrote
#      (example-adopter, EXAMPLE_ADOPTER, example_adopter -> OWNER;
#      example-adopter-two and its forms -> OWNER_TWO; Example Adopter and
#      <your name> -> OWNER_NAME), and the waiting feed's placeholder commit
#      identity 0+example-adopter@users.noreply.github.com -> your own
#      <id>+<login>@users.noreply.github.com, the id read from GitHub. A tree
#      that names the second owner is refused while OWNER_TWO is unset;
#   3. clones <owner/repo> and refuses, listing the paths, when a file
#      already on the default branch differs from the starter's, unless
#      --force. The one exception is a README.md that is still byte for byte
#      the one create-repos.sh wrote (it ends with a marker line);
#   4. commits on branch starter/<folder name>, pushes it without force
#      (never the default branch), checks the commit is on the remote branch
#      with git merge-base --is-ancestor, and opens a pull request with gh.
#
# Idempotent: nothing new to copy means no commit; an open pull request for
# the branch is reused. --dry-run clones and reports what would change, and
# commits, pushes and opens nothing. Nested .git entries and special files are
# refused. Needs: git, gh, jq, python3. Git must be able to push to
# github.com (for example after `gh auth setup-git`).

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"
# shellcheck source=lib/pr.sh
. "$HERE/lib/pr.sh"

FORCE=0
parse_common_args "$@"
set -- "${REST_ARGS[@]}"
POS=()
while [ $# -gt 0 ]; do
  case $1 in
    --force) FORCE=1; shift ;;
    -*) die "unknown option: $1" ;;
    *) POS+=("$1"); shift ;;
  esac
done
[ ${#POS[@]} = 2 ] || { usage; die "give a starter folder and an owner/repo"; }
SRC=${POS[0]} REPO=${POS[1]}
require_tools git jq python3 "$GH"
load_config "$CONFIG"

[ -d "$SRC" ] || die "not a folder: $SRC"
SRC=$(cd "$SRC" && pwd -P)
NAME=$(basename "$SRC")
[[ $NAME =~ ^[a-z0-9][a-z0-9._-]{0,62}$ ]] || die "the starter folder name must be lowercase letters, digits, . _ -"
[[ $REPO =~ ^([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})$ ]] || die "the repo must be owner/name"
[ "${BASH_REMATCH[1],,}" = "${CFG_OWNER,,}" ] || die "the repo's owner is not OWNER in fleet.env"
REPO_NAME=${BASH_REMATCH[2]}
# Caches and local junk never leave the machine: a .pyc embeds local absolute
# paths. They are pruned from every check below and from the copy.
JUNK=( \( -name __pycache__ -o -name .pytest_cache -o -name node_modules -o -name '*.pyc' -o -name .DS_Store \) -prune -o )
[ -n "$(find "$SRC" "${JUNK[@]}" -name .git -print -quit)" ] && die "the starter folder contains a .git entry"
[ -n "$(find "$SRC" "${JUNK[@]}" ! -type f ! -type d ! -type l -print -quit)" ] && die "the starter folder contains a special file"
[ -n "$(find "$SRC" "${JUNK[@]}" -type f -print -quit)" ] || die "the starter folder has no files"
# A symlink is kept (git stores it as a link) only when it is relative and
# resolves inside the tree, so the copy can never point outside your repo.
while IFS= read -r -d '' l; do
  t=$(readlink "$l")
  case $t in /*) die "the symlink ${l#"$SRC"/} is absolute" ;; esac
  # os.path.realpath, not realpath -m (GNU only): resolves what exists, keeps the rest.
  real=$(python3 -I -c 'import os, sys; print(os.path.realpath(os.path.join(sys.argv[1], sys.argv[2])))' "$(dirname "$l")" "$t")
  case $real/ in "$SRC"/*) ;; *) die "the symlink ${l#"$SRC"/} points outside the starter folder" ;; esac
done < <(find "$SRC" "${JUNK[@]}" -type l -print0)

W="$BOOT_WORK/w"
mkdir -p "$W"

# ---- stage: copy and give it your names, outside the clone
cp -a "$SRC" "$W/stage"
junk=$(find "$W/stage" \( -name __pycache__ -o -name .pytest_cache -o -name node_modules -o -name '*.pyc' -o -name .DS_Store \) -prune -print | wc -l | tr -d ' ')
find "$W/stage" \( -name __pycache__ -o -name .pytest_cache -o -name node_modules -o -name '*.pyc' -o -name .DS_Store \) -prune -exec rm -rf {} +
[ "$junk" = 0 ] || log "skipped $junk cache or junk path(s) (__pycache__, *.pyc, .pytest_cache, node_modules, .DS_Store)"
noreply=()
if grep -rqlF '0+example-adopter@users.noreply.github.com' "$W/stage"; then
  gh_read_or_die "users/$CFG_OWNER" "your GitHub account $CFG_OWNER"
  uid=$(printf '%s' "$GH_OUT" | jq -r '.id // empty')
  [[ $uid =~ ^[1-9][0-9]{0,11}$ ]] || die "$CFG_OWNER has no numeric id"
  noreply=(--noreply "$uid+$CFG_OWNER@users.noreply.github.com")
fi
two=()
[ -z "$CFG_OWNER_TWO" ] || two=(--owner-two "$CFG_OWNER_TWO")
python3 -I "$HERE/lib/substitute.py" "$W/stage" --owner "$CFG_OWNER" "${two[@]}" \
  --name "$CFG_OWNER_NAME" "${noreply[@]}" > "$BOOT_WORK/subst.log" \
  || die "could not give the starter your names (see above)"
log "gave the starter your names in $(grep -c '^filled: ' "$BOOT_WORK/subst.log" || true) file(s)"

# ---- clone and compare with the default branch
clone_repo "$REPO" "$W/repo"
G=(git -C "$W/repo")
DEFAULT=$(default_branch_of "$W/repo")
NEW=() SAME=() CONFLICT=() GENERATED=()
same_as_default() { # rel staged-path
  if [ -L "$2" ]; then
    [ "$("${G[@]}" cat-file -t "origin/$DEFAULT:$1" 2>/dev/null)" = blob ] \
      && [ "$("${G[@]}" ls-tree "origin/$DEFAULT" -- "$1" | cut -d' ' -f1)" = 120000 ] \
      && [ "$("${G[@]}" cat-file -p "origin/$DEFAULT:$1")" = "$(readlink "$2")" ]
  else
    "${G[@]}" show "origin/$DEFAULT:$1" | cmp -s - "$2"
  fi
}
while IFS= read -r -d '' f; do
  rel=${f#"$W/stage/"}
  if "${G[@]}" cat-file -e "origin/$DEFAULT:$rel" 2>/dev/null; then
    if same_as_default "$rel" "$f"; then SAME+=("$rel")
    elif [ "$rel" = README.md ] && is_known_repo "$REPO_NAME" \
        && "${G[@]}" show "origin/$DEFAULT:$rel" | cmp -s - <(generated_readme "$REPO_NAME"); then
      GENERATED+=("$rel")
    else CONFLICT+=("$rel"); fi
  else
    NEW+=("$rel")
  fi
done < <(find "$W/stage" \( -type f -o -type l \) -print0 | sort -z)

[ ${#SAME[@]} = 0 ] || skip "$REPO already has ${#SAME[@]} of the starter's files, identical"
if [ ${#CONFLICT[@]} -gt 0 ]; then
  if [ "$FORCE" = 1 ]; then
    for p in "${CONFLICT[@]}"; do warn "--force: replacing $p, which differs on $DEFAULT"; done
  else
    for p in "${CONFLICT[@]}"; do printf 'REFUSED: %s exists on %s and differs from the starter\n' "$p" "$DEFAULT" >&2; done
    die "${#CONFLICT[@]} file(s) would be overwritten; merge them by hand or pass --force"
  fi
fi
CHANGE=("${NEW[@]}" "${GENERATED[@]}" "${CONFLICT[@]}")
log "new: ${#NEW[@]} file(s)"
for p in "${GENERATED[@]}"; do log "replacing: $p (still the README create-repos.sh wrote)"; done
if [ ${#CHANGE[@]} = 0 ]; then
  finish; exit 0
fi

BRANCH="starter/$NAME"
start_branch "$W/repo" "$DEFAULT" "$BRANCH"
for p in "${CHANGE[@]}"; do
  mkdir -p "$W/repo/$(dirname "$p")"
  rm -f "$W/repo/$p"
  cp -a "$W/stage/$p" "$W/repo/$p"
done
BODY=$(printf 'Adds the %s tree of the adoption kit: an export of the owner'"'"'s real repo, with the kit'"'"'s invented names replaced by yours from fleet.env (bootstrap/lib/substitute.py). %s file(s).\n\nReview every file, above all anything under .github/workflows/, before merging. The kit'"'"'s export/REGENERATE.md lists the generated files bootstrap/regenerate.sh rebuilds once this is merged.' \
  "$NAME" "${#CHANGE[@]}")
open_pr "$W/repo" "$REPO" "$DEFAULT" "$BRANCH" "Add the $NAME starter files" "$BODY" "${CHANGE[@]}"
finish
