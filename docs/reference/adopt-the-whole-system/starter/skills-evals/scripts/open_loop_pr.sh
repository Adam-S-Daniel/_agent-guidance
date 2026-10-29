#!/usr/bin/env bash
# open_loop_pr.sh --repo OWNER/REPO --dir CHECKOUT --branch NAME --title TEXT
#                 --body-file FILE --draft true|false
#
# The write half of every loop lane after the improve gate (removal,
# scaffold, draft): push the prepared local branch to OWNER/REPO and open one
# pull request, labeled agent-authored. GH_TOKEN is the App token the step
# before minted for that one repo; it reaches git as an http.extraheader,
# never in a URL or argv. Refuses (exit 0, nothing pushed) while any loop pull
# request is open there: one at a time per target repo. --draft false turns
# on auto-merge (merge commit) pinned to the pushed commit, so a later push to
# the branch cannot ride the same approval; --draft true never does.
# Exit 0 opened or nothing to do, 1 a step failed, 2 bad usage.
set -euo pipefail
repo="" dir="" branch="" title="" body="" draft=""
while [ $# -gt 0 ]; do
  [ $# -ge 2 ] || { echo "open_loop_pr: $1 needs a value" >&2; exit 2; }
  case $1 in
    --repo) repo=$2 ;; --dir) dir=$2 ;; --branch) branch=$2 ;;
    --title) title=$2 ;; --body-file) body=$2 ;; --draft) draft=$2 ;;
    *) echo "open_loop_pr: unknown argument" >&2; exit 2 ;;
  esac
  shift 2
done
[[ $repo =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || { echo "open_loop_pr: bad --repo" >&2; exit 2; }
[[ $branch =~ ^(eval-improve|eval-scaffold|eval-draft|retire-skill)/[A-Za-z0-9._-]+$ ]] \
  || { echo "open_loop_pr: --branch is not a loop branch" >&2; exit 2; }
[[ $draft == true || $draft == false ]] || { echo "open_loop_pr: --draft takes true or false" >&2; exit 2; }
[ -n "$title" ] && [ "${#title}" -le 200 ] || { echo "open_loop_pr: bad --title" >&2; exit 2; }
[ -f "$body" ] && [ -d "$dir" ] || { echo "open_loop_pr: --body-file or --dir missing" >&2; exit 2; }
[ -n "${GH_TOKEN:-}" ] || { echo "open_loop_pr: GH_TOKEN is not set" >&2; exit 2; }
sha=$(git -C "$dir" rev-parse --verify "refs/heads/$branch^{commit}")

open=$(gh pr list --repo "$repo" --state open --limit 100 --json headRefName \
  --jq '[.[] | select(.headRefName | test("^(eval-improve|eval-scaffold|eval-draft|retire-skill|eval-guidance-improve)/"))] | length')
if [ "$open" != 0 ]; then
  echo "A loop pull request is already open in $repo; nothing opened."; exit 0
fi
auth=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)
[ -z "${GITHUB_ACTIONS:-}" ] || echo "::add-mask::$auth"
git -C "$dir" -c http.extraheader="AUTHORIZATION: basic $auth" \
  push --quiet "https://github.com/$repo.git" "refs/heads/$branch:refs/heads/$branch"
gh label create agent-authored --repo "$repo" --force >/dev/null
args=(--repo "$repo" --head "$branch" --label agent-authored --title "$title" --body-file "$body")
[ "$draft" = true ] && args+=(--draft)
url=$(gh pr create "${args[@]}")
if [ "$draft" = false ]; then
  gh pr merge --auto --merge --match-head-commit "$sha" --repo "$repo" "$url"
fi
echo "opened a pull request in $repo (draft=$draft)"
