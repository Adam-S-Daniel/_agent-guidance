#!/usr/bin/env bash
# copy-starter.sh [--config fleet.env] [--dry-run] [--force] <starter-subdir> <owner/repo>
#
# Copies the files of one starter folder (for example starter/agent-guidance)
# into a repo through a pull request:
#
#   1. clones <owner/repo> into a temp dir (deleted on exit);
#   2. copies the folder's files, skipping caches and local junk
#      (__pycache__/, *.pyc, .pytest_cache/, node_modules/, .DS_Store: the
#      kit's own .gitignore names the same), filling the guide's
#      placeholders: <you>- (the plugin and marketplace NAMES, in file and
#      folder names and in text) -> the LOWERCASED owner, so
#      plugins/<you>-coding-anywhere/ becomes plugins/owner-coding-anywhere/
#      as install-machine.sh expects; every other <you> (repo owners in URLs
#      and owner/repo) -> OWNER, case kept; in text files <your name> ->
#      OWNER_NAME (default OWNER), and in .json files
#      "actor_id": 111111 / 222222 -> LOOP_WRITER_APP_ID /
#      GUIDANCE_SYNC_APP_ID (when set). Binary files are copied byte for byte;
#   3. refuses, listing the paths, when a file already on the default branch
#      differs from the starter's, unless --force. The one exception is a
#      README.md that is still byte for byte the one create-repos.sh wrote
#      (generated_readme in lib/common.sh, with its marker line): it is replaced;
#   4. commits on branch starter/<folder name>, pushes it without force (never
#      the default branch), checks the commit is on the remote branch with
#      git merge-base --is-ancestor, and opens a pull request with gh.
#
# Idempotent: nothing new to copy means no commit; an open pull request for
# the branch is reused. --dry-run clones and reports what would change, and
# commits, pushes and opens nothing. The starter folder layout is yours to
# choose: pass any folder path. Symlinks and nested .git dirs are refused.
# Needs: git, gh, python3. Git must be able to push to github.com (for
# example after `gh auth setup-git`).

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

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
require_tools git python3 "$GH"
load_config "$CONFIG"

[ -d "$SRC" ] || die "not a folder: $SRC"
SRC=$(cd "$SRC" && pwd)
NAME=$(basename "$SRC")
[[ $NAME =~ ^[a-z0-9][a-z0-9._-]{0,62}$ ]] || die "the starter folder name must be lowercase letters, digits, . _ -"
# OWNER goes into file names, so it must be a plain GitHub login: no "/",
# no "..", nothing a path could read as a separator.
[[ $CFG_OWNER =~ ^[A-Za-z0-9-]{1,39}$ ]] || die "OWNER must match ^[A-Za-z0-9-]{1,39}\$ (it becomes part of file names)"
[[ $REPO =~ ^([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})$ ]] || die "the repo must be owner/name"
[ "${BASH_REMATCH[1],,}" = "${CFG_OWNER,,}" ] || die "the repo's owner is not OWNER in fleet.env"
# Caches and local junk never leave the machine: a .pyc embeds local absolute
# paths. They are pruned from every check below and from the copy.
JUNK=( \( -name __pycache__ -o -name .pytest_cache -o -name node_modules -o -name '*.pyc' -o -name .DS_Store \) -prune -o )
[ -n "$(find "$SRC" "${JUNK[@]}" -type l -print -quit)" ] && die "the starter folder contains a symlink; the guide wants real files only"
[ -n "$(find "$SRC" "${JUNK[@]}" -name .git -print -quit)" ] && die "the starter folder contains a .git entry"
[ -n "$(find "$SRC" "${JUNK[@]}" ! -type f ! -type d -print -quit)" ] && die "the starter folder contains a special file"
[ -n "$(find "$SRC" "${JUNK[@]}" -type f -print -quit)" ] || die "the starter folder has no files"

BRANCH="starter/$NAME"
GIT_BASE=${COPY_STARTER_GIT_BASE:-https://github.com}
W="$BOOT_WORK/w"
mkdir -p "$W"

git clone --quiet "$GIT_BASE/$REPO.git" "$W/repo" 2>"$BOOT_WORK/err" || die "could not clone $REPO"
G=(git -C "$W/repo")
DEFAULT=$("${G[@]}" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null) || die "$REPO has no default branch (empty repo?): run create-repos.sh"
DEFAULT=${DEFAULT#origin/}
[ "$BRANCH" != "$DEFAULT" ] || die "refusing: the starter branch would be the default branch"

# ---- stage: copy and fill placeholders, outside the clone
cp -R "$SRC" "$W/stage"
junk=$(find "$W/stage" \( -name __pycache__ -o -name .pytest_cache -o -name node_modules -o -name '*.pyc' -o -name .DS_Store \) -prune -print | wc -l | tr -d ' ')
find "$W/stage" \( -name __pycache__ -o -name .pytest_cache -o -name node_modules -o -name '*.pyc' -o -name .DS_Store \) -prune -exec rm -rf {} +
[ "$junk" = 0 ] || log "skipped $junk cache or junk path(s) (__pycache__, *.pyc, .pytest_cache, node_modules, .DS_Store)"
python3 -I - "$W/stage" "$CFG_OWNER" "$CFG_OWNER_NAME" "$CFG_LOOP_WRITER_APP_ID" "$CFG_GUIDANCE_SYNC_APP_ID" <<'PY'
import os, re, sys
root, owner, name, loop_id, sync_id = sys.argv[1:6]
if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", owner):
    sys.exit("ERROR: OWNER is not a plain GitHub login")
# Names first, deepest first, so a renamed folder's children are already done.
for d, dirs, files in os.walk(root, topdown=False):
    for n in sorted(dirs + files):
        if "<you>" not in n:
            continue
        new_name = n.replace("<you>-", owner.lower() + "-").replace("<you>", owner)
        src, dst = os.path.join(d, n), os.path.join(d, new_name)
        if os.path.lexists(dst):
            sys.exit(f"ERROR: renaming {os.path.relpath(src, root)} would replace {os.path.relpath(dst, root)}")
        os.rename(src, dst)
        print(f"renamed: {os.path.relpath(src, root)} -> {os.path.relpath(dst, root)}")
for d, _, files in os.walk(root):
    for f in sorted(files):
        p = os.path.join(d, f)
        rel = os.path.relpath(p, root)
        data = open(p, "rb").read()
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        # Plugin and marketplace names are lowercase (install-machine.sh
        # installs <owner lowercased>-<plugin>@<owner lowercased>-skills);
        # an owner in a URL or owner/repo keeps its case.
        new = text.replace("<you>-", owner.lower() + "-").replace("<you>", owner)
        if name:
            new = new.replace("<your name>", name)
        if f.endswith(".json"):
            if loop_id:
                new = re.sub(r'("actor_id"\s*:\s*)111111\b', r"\g<1>" + loop_id, new)
            if sync_id:
                new = re.sub(r'("actor_id"\s*:\s*)222222\b', r"\g<1>" + sync_id, new)
        if new != text:
            st = os.stat(p).st_mode
            with open(p, "w", encoding="utf-8", newline="") as out:
                out.write(new)
            os.chmod(p, st & 0o7777)
            print(f"filled placeholders: {rel}")
        if "<your name>" in new:
            print(f"WARNING: {rel} still has <your name>; set OWNER_NAME in fleet.env", file=sys.stderr)
        if re.search(r'"actor_id"\s*:\s*(111111|222222)\b', new):
            print(f"WARNING: {rel} still has a placeholder App ID; run create-apps.sh first", file=sys.stderr)
PY

# ---- compare with the default branch
NEW=() SAME=() CONFLICT=() GENERATED=()
REPO_NAME=${REPO#*/}
while IFS= read -r -d '' f; do
  rel=${f#"$W/stage/"}
  if "${G[@]}" cat-file -e "origin/$DEFAULT:$rel" 2>/dev/null; then
    if "${G[@]}" show "origin/$DEFAULT:$rel" | cmp -s - "$f"; then SAME+=("$rel")
    elif [ "$rel" = README.md ] && is_known_repo "$REPO_NAME" \
        && "${G[@]}" show "origin/$DEFAULT:$rel" | cmp -s - <(generated_readme "$REPO_NAME"); then
      GENERATED+=("$rel")
    else CONFLICT+=("$rel"); fi
  else
    NEW+=("$rel")
  fi
done < <(find "$W/stage" -type f -print0 | sort -z)

for p in "${SAME[@]}"; do skip "$REPO already has $p, identical"; done
if [ ${#CONFLICT[@]} -gt 0 ]; then
  if [ "$FORCE" = 1 ]; then
    for p in "${CONFLICT[@]}"; do warn "--force: replacing $p, which differs on $DEFAULT"; done
  else
    for p in "${CONFLICT[@]}"; do printf 'REFUSED: %s exists on %s and differs from the starter\n' "$p" "$DEFAULT" >&2; done
    die "${#CONFLICT[@]} file(s) would be overwritten; merge them by hand or pass --force"
  fi
fi
CHANGE=("${NEW[@]}" "${GENERATED[@]}" "${CONFLICT[@]}")
for p in "${NEW[@]}"; do log "new: $p"; done
for p in "${GENERATED[@]}"; do log "replacing: $p (still the README create-repos.sh wrote)"; done

if [ ${#CHANGE[@]} = 0 ]; then
  finish; exit 0
fi

# ---- branch: reuse the remote starter branch if a previous run pushed one
if "${G[@]}" ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  "${G[@]}" fetch --quiet origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
  "${G[@]}" checkout --quiet -b "$BRANCH" "origin/$BRANCH"
else
  "${G[@]}" checkout --quiet -b "$BRANCH" "origin/$DEFAULT"
fi
for p in "${CHANGE[@]}"; do
  mkdir -p "$W/repo/$(dirname "$p")"
  cp -p "$W/stage/$p" "$W/repo/$p"
done
"${G[@]}" add -- "${CHANGE[@]}"

if "${G[@]}" diff --cached --quiet; then
  log "branch $BRANCH already carries these files"
else
  MSG=$(printf 'Add the %s starter files\n\nCopied from the starter folder %s/ by copy-starter.sh, with the\nguide'"'"'s placeholders filled from fleet.env. Files: %s.\n' "$NAME" "$NAME" "${#CHANGE[@]}")
  if [ "$DRY_RUN" = 1 ]; then
    WRITES=$((WRITES + 1))
    printf 'DRY-RUN: git commit on %s (%s file(s)), then git push origin HEAD:refs/heads/%s\n' "$BRANCH" "${#CHANGE[@]}" "$BRANCH"
  else
    base=$("${G[@]}" rev-parse HEAD)
    "${G[@]}" commit --quiet -m "$MSG" || die "the commit was refused (a hook?)"
    sha=$("${G[@]}" rev-parse HEAD)
    [ "$sha" != "$base" ] || die "no commit was made"
    [ "$("${G[@]}" rev-parse --abbrev-ref HEAD)" = "$BRANCH" ] || die "not on $BRANCH"
    WRITES=$((WRITES + 1))
    "${G[@]}" push --quiet origin "HEAD:refs/heads/$BRANCH" 2>"$BOOT_WORK/err" || die "the push to $BRANCH failed"
    "${G[@]}" fetch --quiet origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
    "${G[@]}" merge-base --is-ancestor "$sha" "origin/$BRANCH" \
      || die "the commit $sha is not on origin/$BRANCH after the push"
    log "pushed $sha to $REPO $BRANCH (verified on the remote branch)"
  fi
fi

# ---- pull request
if [ "$DRY_RUN" = 1 ]; then
  WRITES=$((WRITES + 1))
  printf 'DRY-RUN: %s pr create --repo %s --base %s --head %s (unless one is open)\n' "$GH" "$REPO" "$DEFAULT" "$BRANCH"
  finish; exit 0
fi
open=$("$GH" pr list --repo "$REPO" --head "$BRANCH" --state open --json url 2>"$BOOT_WORK/err") \
  || die "could not list pull requests of $REPO (HTTP $(_status_from "$BOOT_WORK/err"))"
url=$(printf '%s' "$open" | jq -r '.[0].url // empty')
if [ -n "$url" ]; then
  skip "pull request already open: $url"
else
  WRITES=$((WRITES + 1))
  printf 'Adds the files of the %s starter folder from the adoption guide.\n\nFiles:\n%s\n\nPlaceholders (<you> and, where set, <your name> and App IDs) were filled from fleet.env. Review every file, above all anything under .github/workflows/, before merging.\n' \
    "$NAME" "$(printf -- '- `%s`\n' "${CHANGE[@]}")" \
    | run_write_stdin "$GH" pr create --repo "$REPO" --base "$DEFAULT" --head "$BRANCH" \
        --title "Add the $NAME starter files" --body-file -
  did "opened a pull request for $BRANCH in $REPO"
fi
finish
