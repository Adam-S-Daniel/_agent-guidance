# lib/pr.sh: commit a clone's changes on a branch and open a pull request.
# Sourced after lib/common.sh, never run. Used by copy-starter.sh and
# regenerate.sh. Never pushes to a default branch and never forces a push.
#
#   start_branch <clone dir> <default branch> <branch>
#       check out <branch>: the remote branch of that name if a previous run
#       pushed one (its pull request may be open), else a new one from the
#       default branch. Make the changes after this.
#   open_pr <clone dir> <owner/repo> <default branch> <branch> <title> <body> <path>...
#       stage exactly <path>... (git add -A, so a deletion is staged too),
#       commit (refusing if a hook refused), push HEAD:refs/heads/<branch>
#       without force, check the commit is on the remote branch with
#       git merge-base --is-ancestor (a refused commit can still let a push
#       succeed), and open a pull request unless one is open for the branch.
#       In a dry run it commits nothing and prints what it would do.

start_branch() {
  local dir=$1 default=$2 branch=$3 G=(git -C "$1")
  [ "$branch" != "$default" ] || die "refusing: the branch would be the default branch"
  if "${G[@]}" ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
    "${G[@]}" fetch --quiet origin "+refs/heads/$branch:refs/remotes/origin/$branch"
    "${G[@]}" checkout --quiet -B "$branch" "origin/$branch"
  else
    "${G[@]}" checkout --quiet -B "$branch" "origin/$default"
  fi
}

open_pr() {
  local dir=$1 repo=$2 default=$3 branch=$4 title=$5 body=$6; shift 6
  local G=(git -C "$dir") base sha open url
  [ "$("${G[@]}" rev-parse --abbrev-ref HEAD)" = "$branch" ] || die "open_pr: not on $branch (call start_branch first)"
  "${G[@]}" add -A -- "$@"
  if "${G[@]}" diff --cached --quiet; then
    if [ -z "$("${G[@]}" rev-list "origin/$default..HEAD" 2>/dev/null)" ]; then
      skip "$repo: nothing to propose ($branch would equal $default)"; return 0
    fi
    log "branch $branch already carries these changes"
  elif [ "$DRY_RUN" = 1 ]; then
    WRITES=$((WRITES + 1))
    printf 'DRY-RUN: git commit on %s (%s), then git push origin HEAD:refs/heads/%s\n' \
      "$branch" "$("${G[@]}" diff --cached --name-only | wc -l | tr -d ' ') file(s)" "$branch"
  else
    base=$("${G[@]}" rev-parse HEAD)
    "${G[@]}" commit --quiet -m "$title" -m "$body" || die "the commit was refused (a hook?)"
    sha=$("${G[@]}" rev-parse HEAD)
    [ "$sha" != "$base" ] || die "no commit was made"
    [ "$("${G[@]}" rev-parse --abbrev-ref HEAD)" = "$branch" ] || die "not on $branch"
    WRITES=$((WRITES + 1))
    "${G[@]}" push --quiet origin "HEAD:refs/heads/$branch" 2>"$BOOT_WORK/err" || die "the push to $branch failed"
    "${G[@]}" fetch --quiet origin "+refs/heads/$branch:refs/remotes/origin/$branch"
    "${G[@]}" merge-base --is-ancestor "$sha" "origin/$branch" \
      || die "the commit $sha is not on origin/$branch after the push"
    log "pushed $sha to $repo $branch (verified on the remote branch)"
  fi
  if [ "$DRY_RUN" = 1 ]; then
    WRITES=$((WRITES + 1))
    printf 'DRY-RUN: %s pr create --repo %s --base %s --head %s (unless one is open)\n' "$GH" "$repo" "$default" "$branch"
    return 0
  fi
  open=$("$GH" pr list --repo "$repo" --head "$branch" --state open --json url 2>"$BOOT_WORK/err") \
    || die "could not list pull requests of $repo (HTTP $(_status_from "$BOOT_WORK/err"))"
  url=$(printf '%s' "$open" | jq -r '.[0].url // empty')
  if [ -n "$url" ]; then
    skip "pull request already open: $url"
  else
    run_write_stdin "$GH" pr create --repo "$repo" --base "$default" --head "$branch" \
      --title "$title" --body-file - < <(printf '%s\n' "$body")
    did "opened a pull request for $branch in $repo"
  fi
}

# default_branch_of <clone dir>: the clone's default branch, or die.
default_branch_of() {
  local d
  d=$(git -C "$1" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null) \
    || die "the repo has no default branch (empty repo?): run create-repos.sh"
  printf '%s' "${d#origin/}"
}
