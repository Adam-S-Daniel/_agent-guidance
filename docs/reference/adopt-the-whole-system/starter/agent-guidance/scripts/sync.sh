#!/usr/bin/env bash
# sync.sh [--dry-run]: write the guidance files into every repo in repos.yml.
# For each repo: clone, write the files, and if anything changed commit on
# guidance-sync/update, push without force, and open a pull request with
# auto-merge (never for a PR that changes .github/workflows/). Nothing
# changed means nothing is done. It skips its own repo.
#
# Environment:
#   SYNC_OWNER        owner for bare names in repos.yml (required)
#   SYNC_SELF_REPO    this repo, skipped (default: $SYNC_OWNER/agent-guidance)
#   SYNC_SRC          the agent-guidance checkout (default: the parent of scripts/)
#   SYNC_REPOS_FILE   default: $SYNC_SRC/repos.yml
#   SYNC_BRANCH       default: guidance-sync/update
#   SYNC_GIT_BASE     default: https://github.com
#   SYNC_COMMIT_EMAIL default: guidance-sync@users.noreply.github.com
#   GH_TOKEN          an App installation token: clones and gh use it. It is
#                     never printed and never put in a URL or a command line.
set -euo pipefail

SELF=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")
SRC=${SYNC_SRC:-$(cd "$(dirname "$SELF")/.." && pwd)}
BRANCH=${SYNC_BRANCH:-guidance-sync/update}
GIT_BASE=${SYNC_GIT_BASE:-https://github.com}
EMAIL=${SYNC_COMMIT_EMAIL:-guidance-sync@users.noreply.github.com}
TRAILER='Guidance-Sync: managed'
MARKER='## Repo-specific additions'

# source path in agent-guidance : path in the consumer. A template that does
# not exist yet (a later phase) is skipped, so the sync grows with the guide.
TEMPLATE_FILES=(
  "templates/fleet-memory.sh:.claude/hooks/fleet-memory.sh"
  "templates/agent-checks:.githooks/agent-checks"
  "templates/check-privacy.py:.githooks/check-privacy.py"
  "templates/agent-checks.yml:.github/workflows/agent-checks.yml"
  "templates/agent-checks-guard.sh:.claude/hooks/agent-checks-guard.sh"
  "templates/skills-bootstrap.sh:.claude/hooks/skills-bootstrap.sh"
  "templates/skill-digest.py:scripts/skill-digest.py"
)
GITATTRIBUTES_LINES=(
  '.claude/hooks/* text eol=lf'
  '.githooks/* text eol=lf'
)

log() { printf '%s\n' "$*"; }
die() { printf 'sync: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- one repo
# Runs in its own process (--one-repo) so that set -e holds inside it.
sync_one() {
  local repo=$1 dry=$2 url default remote_ref base_ref foreign sha msg version
  local changed pr_url pr_num body unmerged
  work=$(mktemp -d) || die "cannot create a temp dir"   # global on purpose: the EXIT trap runs after this function returns
  trap 'rm -rf "$work"' EXIT

  if [ -n "${GH_TOKEN:-}" ]; then
    # The token reaches git only through this helper's environment.
    cat > "$work/askpass" <<'ASKPASS'
#!/bin/sh
case "$1" in
  Username*) echo x-access-token ;;
  *) printf '%s\n' "$GH_TOKEN" ;;
esac
ASKPASS
    chmod 700 "$work/askpass"
    export GIT_ASKPASS="$work/askpass"
  fi
  export GIT_TERMINAL_PROMPT=0

  url="$GIT_BASE/$repo.git"
  if ! git clone --quiet "$url" "$work/repo" 2>"$work/clone.err"; then
    log "[$repo] FAILED: cannot clone (check the App is installed on this repo)"
    return 1
  fi
  cd "$work/repo"
  default=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null) || default=""
  default=${default#origin/}
  if [ -z "$default" ] || ! git rev-parse -q --verify "refs/remotes/origin/$default^{commit}" >/dev/null; then
    log "[$repo] FAILED: no default branch with a commit; give the repo a first commit"
    return 1
  fi
  git() { command git -c user.name="guidance-sync[bot]" -c user.email="$EMAIL" -c commit.gpgsign=false "$@"; }

  # Which commit do we build on? The default branch, unless the update branch
  # already holds our own unmerged commits (then build on top of them, so the
  # push is a fast-forward and an open pull request keeps working).
  remote_ref="refs/remotes/origin/$BRANCH"
  base_ref="origin/$default"
  if git rev-parse -q --verify "$remote_ref^{commit}" >/dev/null; then
    if git merge-base --is-ancestor "$remote_ref" "origin/$default"; then
      log "[$repo] $BRANCH is already merged; starting from $default"
    else
      foreign=0
      unmerged=$(git rev-list "origin/$default..$remote_ref")   # captured first: a failure stops the run
      while IFS= read -r sha; do
        [ -n "$sha" ] || continue
        msg=$(git show -s --format=%B "$sha")
        grep -qxF -- "$TRAILER" <<<"$msg" || foreign=1
      done <<<"$unmerged"
      if [ "$foreign" = 1 ]; then
        log "[$repo] REFUSED: $BRANCH has diverged (it holds commits this sync did not make). Merge or close that work, then delete the branch."
        return 1
      fi
      base_ref=$remote_ref
    fi
  fi
  git checkout --quiet -B "$BRANCH" "$base_ref"
  if [ "$base_ref" = "$remote_ref" ]; then
    # Bring the branch up to date with the default branch.
    if ! git merge --quiet --no-edit -m "Merge $default into $BRANCH" -m "$TRAILER" "origin/$default" >/dev/null 2>&1; then
      git merge --abort >/dev/null 2>&1 || true
      log "[$repo] REFUSED: $BRANCH conflicts with $default; resolve it by hand"
      return 1
    fi
  fi

  write_files

  changed=$(command git status --porcelain --untracked-files=all)
  if [ -z "$changed" ]; then
    log "[$repo] up to date"
    return 0
  fi
  if [ "$dry" = 1 ]; then
    log "[$repo] would change:"
    while IFS= read -r line; do log "    $line"; done <<<"$changed"
    if [ "$base_ref" = "$remote_ref" ]; then
      log "[$repo] would add a commit to the open $BRANCH"
    else
      log "[$repo] would create $BRANCH and open a pull request into $default"
    fi
    return 0
  fi

  version=$(sed -n '1s/^<!-- guidance-version: \([0-9][0-9]*\) -->$/\1/p' .claude/hooks/fleet-guidance.md)
  git add -A
  git commit --quiet -m "Sync agent guidance (v$version)" -m "$TRAILER"
  sha=$(git rev-parse HEAD)
  if ! git push --quiet origin "HEAD:refs/heads/$BRANCH" 2>"$work/push.err"; then
    log "[$repo] FAILED: push of $BRANCH was rejected (not forced). The branch moved while we worked; run again."
    return 1
  fi
  # A successful push does not prove the commit is there. Check the remote.
  git fetch --quiet origin "+refs/heads/$BRANCH:$remote_ref"
  if ! git merge-base --is-ancestor "$sha" "$remote_ref"; then
    log "[$repo] FAILED: commit ${sha:0:12} is not on origin/$BRANCH after the push"
    return 1
  fi
  log "[$repo] pushed ${sha:0:12} to $BRANCH"

  pr_num=$(gh pr list --repo "$repo" --head "$BRANCH" --state open --json number --jq '.[0].number // empty') \
    || { log "[$repo] FAILED: cannot list pull requests"; return 1; }
  if [ -z "$pr_num" ]; then
    body="Automated guidance sync (v$version) from the central guidance repo. Changed files:"$'\n\n'
    while IFS= read -r line; do body+="- \`${line:3}\`"$'\n'; done <<<"$changed"
    pr_url=$(gh pr create --repo "$repo" --base "$default" --head "$BRANCH" \
      --title "Sync agent guidance (v$version)" --body "$body") \
      || { log "[$repo] FAILED: gh pr create"; return 1; }
    pr_num=${pr_url##*/}
    log "[$repo] opened pull request #$pr_num"
  else
    log "[$repo] pull request #$pr_num is open; updated"
  fi
  # A PR whose diff against the default branch touches a workflow never gets
  # auto-merge: a workflow change can rewrite the check that would approve it.
  # Judged on the whole PR, so a later sync cannot re-arm an earlier change.
  wf=$(git diff --name-only "origin/$default...$sha" -- .github/workflows/)
  if [ -n "$wf" ]; then
    gh pr merge "$pr_num" --repo "$repo" --disable-auto >/dev/null 2>&1 || true
    log "[$repo] changes a workflow; auto-merge stays off. Review and merge #$pr_num by hand"
    return 0
  fi
  # Until a required check exists, auto-merge can be refused; merge by hand then.
  if gh pr merge "$pr_num" --repo "$repo" --auto --merge >/dev/null 2>&1; then
    log "[$repo] auto-merge on"
  else
    log "[$repo] WARNING: could not turn on auto-merge; merge #$pr_num by hand"
  fi
}

# Writes every managed file into the current directory (the consumer clone).
write_files() {
  local pair src dest version line existing

  # 1. AGENTS.md: the stub, then the repo's own part.
  bash "$SRC/scripts/render-agents-md.sh" "$SRC/agents-md/stub.md" AGENTS.md
  chmod 644 AGENTS.md

  # 2. CLAUDE.md only if missing.
  [ -e CLAUDE.md ] || printf '@AGENTS.md\n' > CLAUDE.md

  # 3. The payload, first line = the version of base.md's last change.
  if [ "$(command git -C "$SRC" rev-parse --is-shallow-repository)" = true ]; then
    die "SYNC_SRC is a shallow clone; check agent-guidance out with fetch-depth: 0"
  fi
  version=$(command git -C "$SRC" log -1 --format=%ct -- agents-md/base.md)
  [ -n "$version" ] || die "agents-md/base.md has no commit"
  mkdir -p .claude/hooks
  { printf '<!-- guidance-version: %s -->\n' "$version"; cat "$SRC/agents-md/base.md"; } > .claude/hooks/fleet-guidance.md

  # 4. Templates (hooks, runner, workflow, scripts).
  for pair in "${TEMPLATE_FILES[@]}"; do
    src=${pair%%:*} dest=${pair#*:}
    [ -f "$SRC/$src" ] || continue
    mkdir -p "$(dirname "$dest")"
    cp "$SRC/$src" "$dest"
    if [ -x "$SRC/$src" ]; then chmod 755 "$dest"; else chmod 644 "$dest"; fi
  done

  # 5. settings.json: merge the managed hook entries, keep everything else.
  mkdir -p .claude
  python3 -I - .claude/settings.json \
    "$([ -f "$SRC/templates/skills-bootstrap.sh" ] && echo 1 || echo 0)" \
    "$([ -f "$SRC/templates/agent-checks-guard.sh" ] && echo 1 || echo 0)" <<'PY'
import json, os, sys
path, with_bootstrap, with_guard = sys.argv[1], sys.argv[2] == "1", sys.argv[3] == "1"
SS = "startup|resume|fork"
def cmd(name):
    return 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/%s"' % name
managed = [("SessionStart", SS, cmd("fleet-memory.sh"), 30)]
if with_bootstrap:
    managed.append(("SessionStart", SS, cmd("skills-bootstrap.sh"), 90))
if with_guard:
    managed.append(("PreToolUse", "Bash|Edit|Write|MultiEdit", cmd("agent-checks-guard.sh"), 10))

settings = {}
if os.path.exists(path) and os.path.getsize(path):
    try:
        with open(path, encoding="utf-8") as f:
            settings = json.load(f)
    except ValueError:
        sys.exit("sync: %s is not valid JSON; fix it by hand, the sync will not overwrite it" % path)
    if not isinstance(settings, dict):
        sys.exit("sync: %s is not a JSON object" % path)
before = json.dumps(settings, sort_keys=True)

hooks = settings.setdefault("hooks", {})
if not isinstance(hooks, dict):
    sys.exit("sync: hooks in %s is not an object" % path)
for event, matcher, command, timeout in managed:
    entry = {"type": "command", "command": command, "timeout": timeout}
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        sys.exit("sync: hooks.%s in %s is not a list" % (event, path))
    done = False
    for g in groups:
        inner = g.get("hooks") if isinstance(g, dict) else None
        if not isinstance(inner, list):
            continue
        for i, h in enumerate(inner):
            if isinstance(h, dict) and h.get("command") == command:
                inner[i] = entry          # replace in place: same command = same entry
                if len(inner) == 1:
                    g["matcher"] = matcher
                done = True
                break
        if done:
            break
    if not done:
        groups.append({"matcher": matcher, "hooks": [entry]})
settings["disableSkillShellExecution"] = True

if json.dumps(settings, sort_keys=True) != before or not os.path.exists(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
PY

  # 6. .gitattributes: append what is missing.
  touch .gitattributes
  existing=$(cat .gitattributes)
  for line in "${GITATTRIBUTES_LINES[@]}"; do
    if ! grep -qxF -- "$line" <<<"$existing"; then
      if [ -s .gitattributes ] && [ -n "$(tail -c 1 .gitattributes)" ]; then echo >> .gitattributes; fi
      printf '%s\n' "$line" >> .gitattributes
      existing+=$'\n'"$line"
    fi
  done
}

# ---------------------------------------------------------------- the list
list_repos() {
  python3 -I - "$1" "$SYNC_OWNER" <<'PY'
import re, sys
path, owner = sys.argv[1:3]
text = open(path, encoding="utf-8").read()
try:
    import yaml
    doc = yaml.safe_load(text) or {}
    entries = doc.get("repos") or []
except ImportError:      # repos.yml is a plain list; read it without PyYAML
    entries = re.findall(r"^\s*-\s+([^\s#]+)", text, re.M)
ok = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?")
for e in entries:
    if not isinstance(e, str) or not ok.fullmatch(e):
        sys.exit("sync: bad entry in repos.yml: %r" % (e,))
    print(e if "/" in e else owner + "/" + e)
PY
}

main() {
  local dry=0 repo rc failed=0 listing self
  case "${1:-}" in
    --one-repo) shift; sync_one "$1" "${2:-0}"; return ;;
    --dry-run) dry=1 ;;
    "") ;;
    *) die "usage: sync.sh [--dry-run]" ;;
  esac
  [ -n "${SYNC_OWNER:-}" ] || die "set SYNC_OWNER (your GitHub user or organization)"
  self=${SYNC_SELF_REPO:-$SYNC_OWNER/agent-guidance}
  listing=$(list_repos "${SYNC_REPOS_FILE:-$SRC/repos.yml}") || exit 1
  while IFS= read -r repo; do
    [ -n "$repo" ] || continue
    if [ "${repo,,}" = "${self,,}" ]; then
      log "[$repo] skipped (this is the guidance repo)"
      continue
    fi
    rc=0
    bash "$SELF" --one-repo "$repo" "$dry" || rc=$?
    [ "$rc" = 0 ] || failed=$((failed + 1))
  done <<<"$listing"
  if [ "$failed" -gt 0 ]; then
    log "sync: $failed repo(s) failed or were refused"
    exit 1
  fi
}

main "$@"
