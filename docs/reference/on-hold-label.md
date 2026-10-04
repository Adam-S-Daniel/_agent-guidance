# On-hold label

The same repository label applies to both issues and PRs:

- Name: `on-hold`
- Color: `BFBFBF`
- Description: `Owner paused this: agents must not work on it until the owner removes the label`

## Meaning

The owner has decided to pause this work. Agents must not start, continue,
review, fix, rebase, merge main into, push to, merge, close, or file follow-up
work doing the paused work. This includes replacement work in another repo.
An open held PR remains open: never close it or convert it to a draft or issue.

Only the owner adds or removes `on-hold`, except when the owner explicitly
directs an agent to do so in that session. Other labels do not override it.
Read labels freshly before acting; if it appears during work, stop. A failed
label read is not permission to proceed. Removing it lets normal eligibility
rules apply again; it does not automatically claim the item.

Agents may read, cite, and list the item as `on hold` in a read-only status.
Do not comment, relabel, remind, or ask the owner again whether to resume.
Leave the held PR and branch untouched.

## Why

The owner's request on 2026-10-04:

> Come up with a standard label or something for items like GHA-bench #44 (support this for PRs as well as issues) that tells agents and me that I have decided not to work further on them for the time being. Document in _agent-skills and/or the appropriate agentskills repo

The example is the [GHA-bench cloud benchmark PR](https://github.com/Adam-S-Daniel/GHA-bench/pull/44).
A pause preserves the open item's context without treating it as rejected or
completed. The fleet rule lives in [`agents-md/base.md`](../../agents-md/base.md); worker and changelog
procedures link here for the full semantics.

## Provisioning (owner only)

Run this from the `_agent-guidance` repository root only when the owner
chooses to provision the label, or explicitly directs an agent in that
session. It creates or updates the repository label, never applies it to an
issue or PR. Requires Bash, mikefarah `yq` v4, `jq`, and authenticated `gh`.
This documentation change does not execute provisioning.

The denominator is the remote source, non-archived repository list for every
owner discovered from the sync workflow's step environment, not local disk.
All owners are enumerated before any label write. An enumeration failure,
invalid response, or a saturated 1000-repo limit aborts without claiming fleet
success. A write/readback failure may leave partial provisioning; fix the
failure and rerun, never report the partial result as fleet success.

```bash
set -euo pipefail
owners_json=$(yq -o=json '[.jobs[].steps[].env.SYNC_OWNERS | select(. != null)]' .github/workflows/sync.yml)
jq -e 'length > 0 and all(.[]; type == "string" and length > 0)' <<<"$owners_json" >/dev/null
owners_text=$(jq -r 'unique | .[]' <<<"$owners_json")
owners=()
while IFS= read -r owner_group; do
  read -r -a group <<<"$owner_group"
  owners+=("${group[@]}")
done <<<"$owners_text"
repos=()
for owner in "${owners[@]}"; do
  [[ "$owner" =~ ^[A-Za-z0-9][A-Za-z0-9-]*$ ]] || exit 1
  inventory=$(gh repo list "$owner" --source --no-archived --json nameWithOwner --limit 1000)
  jq -e --arg owner "$owner" '
    type == "array" and length < 1000 and all(.[];
      (.nameWithOwner | type == "string") and
      (.nameWithOwner | split("/") | length == 2) and
      (.nameWithOwner | split("/")[0] | ascii_downcase) == ($owner | ascii_downcase) and
      (.nameWithOwner | test("^[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$")))
  ' <<<"$inventory" >/dev/null
  repo_text=$(jq -r '.[].nameWithOwner' <<<"$inventory")
  if [[ -n "$repo_text" ]]; then
    while IFS= read -r repo; do repos+=("$repo"); done <<<"$repo_text"
  fi
done
description='Owner paused this: agents must not work on it until the owner removes the label'
for repo in "${repos[@]}"; do
  gh label create on-hold -R "$repo" --color BFBFBF --description "$description" --force
  actual=$(gh api "repos/$repo/labels/on-hold")
  jq -e --arg description "$description" '
    .name == "on-hold" and (.color | ascii_downcase) == "bfbfbf" and
    .description == $description
  ' <<<"$actual" >/dev/null
done
printf 'on-hold provisioning verified for the complete remote inventory\n'
```

An empty owner's inventory is allowed if the API returned a validated empty
array. A failed command is never substituted with an empty array.
