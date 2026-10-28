#!/usr/bin/env bash
# drift-report.sh [--push] OUT_DIR: report which fleet repos have drifted
# from the guidance, without changing any of them.
#
# Runs `sync.sh --dry-run` (every repo in repos.yml; each is cloned and the
# managed files written in a scratch clone only) and turns its per-repo lines
# into OUT_DIR/report.json and OUT_DIR/report.md:
#
#   current   "[repo] up to date"
#   drift     "[repo] would change:", with the managed paths that would change
#   failed    "[repo] FAILED: ..."   (cannot clone, no default branch, ...)
#   refused   "[repo] REFUSED: ..."  (a diverged or conflicting update branch)
#   skipped   "[repo] skipped ..."   (the guidance repo itself)
#
# Only those states, repo names and managed paths reach the report, never
# sync.sh's free text. The report names the guidance commit it was made from
# instead of a time, so the same inputs give the same bytes.
#
# With --push it then writes the report to drift-report/ on the branch
# persistent/drift-report of DRIFT_REPO (default $SYNC_OWNER/agent-guidance),
# creating the branch if it is missing, as a fast-forward only, and checks
# afterward that the commit is on the remote branch. GH_TOKEN (the
# guidance-sync App token) reaches git through an askpass helper only.
#
# Environment: as sync.sh (SYNC_OWNER, SYNC_SRC, SYNC_REPOS_FILE,
# SYNC_GIT_BASE, SYNC_COMMIT_EMAIL, GH_TOKEN), plus DRIFT_REPO and
# DRIFT_BRANCH (default persistent/drift-report).
# Exit 0 when the report was written (drift is reported, not an error),
# 1 when the dry run could not be read or the push failed, 2 bad usage.
set -euo pipefail

push=0
if [ "${1:-}" = --push ]; then push=1; shift; fi
[ $# = 1 ] || { echo "usage: drift-report.sh [--push] OUT_DIR" >&2; exit 2; }
out=$1
[ -n "${SYNC_OWNER:-}" ] || { echo "drift-report: set SYNC_OWNER" >&2; exit 2; }
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRC=${SYNC_SRC:-$(cd "$HERE/.." && pwd)}
GIT_BASE=${SYNC_GIT_BASE:-https://github.com}
EMAIL=${SYNC_COMMIT_EMAIL:-guidance-sync@users.noreply.github.com}
DRIFT_REPO=${DRIFT_REPO:-$SYNC_OWNER/agent-guidance}
DRIFT_BRANCH=${DRIFT_BRANCH:-persistent/drift-report}
[[ $DRIFT_REPO =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "drift-report: bad DRIFT_REPO" >&2; exit 2; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$out"

rc=0
SYNC_SRC=$SRC bash "$SRC/scripts/sync.sh" --dry-run > "$work/dry.txt" 2>&1 || rc=$?
guidance=$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)

python3 -I - "$work/dry.txt" "$out" "$guidance" "$rc" <<'PY' || exit 1
import json, re, sys
dry, out, guidance, rc = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
REPO = r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
STATES = [(re.compile(rf"^\[{REPO}\] up to date$"), "current"),
          (re.compile(rf"^\[{REPO}\] would change:$"), "drift"),
          (re.compile(rf"^\[{REPO}\] FAILED: "), "failed"),
          (re.compile(rf"^\[{REPO}\] REFUSED: "), "refused"),
          (re.compile(rf"^\[{REPO}\] skipped "), "skipped")]
# git status --porcelain lines of a managed path: "XY path"
PATH = re.compile(r"^    [ MADRCU?!]{2} ([A-Za-z0-9_./-]{1,200})$")
repos, current = {}, None
for line in open(dry, encoding="utf-8", errors="replace").read().splitlines():
    for rx, state in STATES:
        m = rx.match(line)
        if m:
            name = m.group(1)
            # sync.sh prints one state line per repo; a repeat keeps the last.
            repos.setdefault(name, {"repo": name, "state": state, "files": []})["state"] = state
            current = name if state == "drift" else None
            break
    else:
        pm = PATH.match(line)
        if pm and current:
            repos[current]["files"].append(pm.group(1))
if not repos and rc != 0:
    sys.exit("drift-report: sync.sh --dry-run failed before any repo (check SYNC_OWNER and repos.yml)")
rows = sorted(repos.values(), key=lambda r: r["repo"])
for r in rows:
    r["files"] = sorted(set(r["files"]))
counts = {s: sum(1 for r in rows if r["state"] == s) for s in ("current", "drift", "failed", "refused", "skipped")}
report = {"schema_version": 1, "guidance_commit": guidance, "counts": counts, "repos": rows}
with open(f"{out}/report.json", "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2)
    f.write("\n")
md = [f"# Guidance drift", "", f"From guidance commit `{guidance[:12]}`: "
      + ", ".join(f"{n} {s}" for s, n in counts.items()) + ".", "",
      "| repo | state | files that would change |", "|---|---|---|"]
md += [f"| {r['repo']} | {r['state']} | {', '.join('`%s`' % p for p in r['files'])} |" for r in rows]
with open(f"{out}/report.md", "w", encoding="utf-8") as f:
    f.write("\n".join(md) + "\n")
print(f"drift-report: {counts['drift']} drifted, {counts['failed'] + counts['refused']} need attention, "
      f"{counts['current']} current")
PY

[ "$push" = 1 ] || exit 0

if [ -n "${GH_TOKEN:-}" ]; then
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
g() { git -c user.name="guidance-sync[bot]" -c user.email="$EMAIL" -c commit.gpgsign=false "$@"; }

url="$GIT_BASE/$DRIFT_REPO.git"
if g ls-remote --exit-code --heads "$url" "$DRIFT_BRANCH" >/dev/null 2>&1; then
  g clone --quiet --depth 1 --branch "$DRIFT_BRANCH" "$url" "$work/report" \
    || { echo "drift-report: cannot clone $DRIFT_BRANCH"; exit 1; }
else
  git init --quiet "$work/report"
  git -C "$work/report" checkout --quiet -b "$DRIFT_BRANCH"
fi
mkdir -p "$work/report/drift-report"
cp "$out/report.json" "$out/report.md" "$work/report/drift-report/"
g -C "$work/report" add drift-report
if g -C "$work/report" diff --cached --quiet; then
  echo "drift-report: unchanged on $DRIFT_BRANCH"; exit 0
fi
g -C "$work/report" commit --quiet -m "Drift report from guidance ${guidance:0:12}"
sha=$(git -C "$work/report" rev-parse HEAD)
g -C "$work/report" push --quiet "$url" "HEAD:refs/heads/$DRIFT_BRANCH" \
  || { echo "drift-report: push to $DRIFT_BRANCH was rejected (not forced)"; exit 1; }
g -C "$work/report" fetch --quiet "$url" "$DRIFT_BRANCH"
git -C "$work/report" merge-base --is-ancestor "$sha" FETCH_HEAD \
  || { echo "drift-report: commit ${sha:0:12} is not on $DRIFT_BRANCH after the push"; exit 1; }
echo "drift-report: wrote ${sha:0:12} to $DRIFT_BRANCH"
