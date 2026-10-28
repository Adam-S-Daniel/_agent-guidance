#!/usr/bin/env bash
# test-sync.sh: end-to-end test of sync.sh against local bare repos.
# No network, no sleeps, no wall-clock: remotes are file:// repos reached
# through url.insteadOf in a scratch GIT_CONFIG_GLOBAL, and gh is a stub on
# PATH that records its arguments. Run it: bash test-sync.sh
# Exit 0 and "N assertions passed" mean it worked; any failed assertion
# prints FAIL and the exit code is 1.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# The workflow sits beside this script in the guide's listing and at
# .github/workflows/sync.yml in agent-guidance; check whichever is here.
SYNC_YML=$HERE/sync.yml
[ -f "$SYNC_YML" ] || SYNC_YML=$HERE/../.github/workflows/sync.yml
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
OWNER=acme-example
FAKE_TOKEN="ghs_""FAKE0000000000000000000000000000000000"  # split so secret scanners do not match the literal

pass=0 fail=0
check() {  # check <description> <command...>: passes when the command exits 0
  local d=$1; shift
  if "$@" >/dev/null 2>&1; then pass=$((pass + 1)); else fail=$((fail + 1)); echo "FAIL: $d"; fi
}
has() { grep -qF -- "$2" <<<"$1"; }          # has <text> <needle>
lacks() { ! grep -qF -- "$2" <<<"$1"; }

# ---- scratch git environment (nothing here touches the real config)
export GIT_CONFIG_GLOBAL=$T/gitconfig GIT_CONFIG_NOSYSTEM=1
git config --global user.name tester
git config --global user.email tester@example.com
git config --global init.defaultBranch main
git config --global "url.file://$T/remotes/.insteadOf" https://github.com/

# ---- gh stub: records its arguments, keeps "open PR" state per repo
mkdir -p "$T/bin" "$T/ghstate"
cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
a=("$@"); line="${a[*]}"; echo "${line//$'\n'/ }" >> "$GH_STUB_LOG"
[ -n "${GH_TOKEN:-}" ] && echo token-set >> "$GH_STUB_LOG.token"
repo=""; for i in "${!a[@]}"; do [ "${a[$i]}" = --repo ] && repo=${a[$((i + 1))]}; done
f="$GH_STUB_STATE/${repo//\//_}.pr"
case "${a[0]} ${a[1]}" in
  "pr list")   [ -f "$f" ] && cat "$f"; exit 0 ;;
  "pr create") echo 7 > "$f"; echo "https://github.com/$repo/pull/7"; exit 0 ;;
  "pr merge")  exit 0 ;;
esac
echo "gh stub: unexpected call: $line" >&2; exit 1
STUB
chmod +x "$T/bin/gh"
export GH_STUB_LOG=$T/gh.log GH_STUB_STATE=$T/ghstate
: > "$GH_STUB_LOG"

# ---- the guidance repo (the sync's source), with a fixed commit time
G=$T/guidance
mkdir -p "$G/agents-md" "$G/templates" "$G/scripts"
printf '# Stub\nThe guidance arrives through the hook.\n' > "$G/agents-md/stub.md"
printf '# Base guidance\nRule one.\n' > "$G/agents-md/base.md"
printf '#!/usr/bin/env bash\necho fleet-memory\n' > "$G/templates/fleet-memory.sh"
printf '#!/usr/bin/env bash\necho bootstrap\n' > "$G/templates/skills-bootstrap.sh"
printf '#!/usr/bin/env bash\necho guard\n' > "$G/templates/agent-checks-guard.sh"
printf '#!/usr/bin/env bash\necho runner\n' > "$G/templates/agent-checks"; chmod +x "$G/templates/agent-checks"
cp "$HERE/sync.sh" "$HERE/render-agents-md.sh" "$G/scripts/"
git -C "$G" init -q
git -C "$G" add -A
GIT_AUTHOR_DATE="1700000000 +0000" GIT_COMMITTER_DATE="1700000000 +0000" git -C "$G" commit -q -m "guidance v1"

# ---- remotes
mk_remote() {  # mk_remote <name>: bare repo with one commit on main
  git init -q --bare "$T/remotes/$OWNER/$1.git"
  git clone -q "$T/remotes/$OWNER/$1.git" "$T/w-$1" 2>/dev/null
  printf '# %s\n' "$1" > "$T/w-$1/README.md"
  git -C "$T/w-$1" add -A && git -C "$T/w-$1" commit -q -m "first commit" && git -C "$T/w-$1" push -q origin main
}
rsha() { git --git-dir="$T/remotes/$OWNER/$1.git" rev-parse -q --verify "$2" 2>/dev/null; }
rshow() { git --git-dir="$T/remotes/$OWNER/$1.git" show "$2:$3"; }
BR=guidance-sync/update

for r in app-a app-b app-c app-d app-e app-f app-g; do mk_remote "$r"; done
# app-b has its own settings keys, its own hook, its own AGENTS.md and CLAUDE.md
(
  cd "$T/w-app-b"
  mkdir -p .claude
  cat > .claude/settings.json <<'JSON'
{"permissions": {"allow": ["Bash(ls:*)"]}, "model": "example-model",
 "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo own-hook"}]}]}}
JSON
  printf '# Old managed top\nold text\n\n## Repo-specific additions\n\nKeep this line: TANGERINE-41\n' > AGENTS.md
  printf 'custom claude text\n' > CLAUDE.md
  printf '*.png binary' > .gitattributes
  git add -A && git commit -q -m "repo files" && git push -q origin main
)
# app-c: a human pushed a fix to the update branch
(
  cd "$T/w-app-c"
  git checkout -q -b "$BR"
  echo "human fix" > fix.txt
  git add -A && git -c user.name=alice -c user.email=alice@example.com commit -q -m "fix by hand" && git push -q origin "$BR"
)
# app-e: a stale update branch that is already merged (an ancestor of main)
(cd "$T/w-app-e" && git push -q origin "main:refs/heads/$BR")
# app-f: settings.json that is not JSON
(cd "$T/w-app-f" && mkdir -p .claude && echo '{bad' > .claude/settings.json && git add -A && git commit -q -m s && git push -q origin main)

run_sync() {  # run_sync <repos-list-file> [args]: sets RC and OUT
  local list=$1; shift
  OUT=$(PATH="$T/bin:$PATH" SYNC_OWNER=$OWNER SYNC_SRC=$G SYNC_REPOS_FILE=$list \
        GH_TOKEN=$FAKE_TOKEN bash "$G/scripts/sync.sh" "$@" 2>&1)
  RC=$?
}
ghlines() { grep -c -- "$1" "$GH_STUB_LOG" || true; }

printf 'repos:\n  - %s/agent-guidance\n  - app-a\n  - %s/app-b\n' "$OWNER" "$OWNER" > "$T/list1.yml"

# ============================================================ run 1
main_a=$(rsha app-a refs/heads/main); main_b=$(rsha app-b refs/heads/main)
run_sync "$T/list1.yml"
check "run 1 exits 0" test "$RC" = 0
check "run 1 skips the guidance repo" has "$OUT" "agent-guidance] skipped"
check "run 1 never touches the guidance repo in gh" test "$(ghlines agent-guidance)" = 0
check "run 1 opens one PR per repo" test "$(ghlines 'pr create')" = 2
check "run 1 turns on auto-merge with a merge commit" test "$(ghlines 'pr merge 7 --repo acme-example/app-a --auto --merge')" = 1
check "run 1 PR targets main from the update branch" grep -qF -- "--base main --head $BR" "$GH_STUB_LOG"
check "run 1 does not print the token" lacks "$OUT" "$FAKE_TOKEN"
check "run 1 gives gh the token" test -s "$GH_STUB_LOG.token"
check "run 1 leaves main alone (app-a)" test "$(rsha app-a refs/heads/main)" = "$main_a"
check "run 1 leaves main alone (app-b)" test "$(rsha app-b refs/heads/main)" = "$main_b"
check "run 1 pushes the branch (app-a)" test -n "$(rsha app-a "refs/heads/$BR")"

A_AGENTS=$(rshow app-a "$BR" AGENTS.md)
check "app-a AGENTS.md starts with the stub" test "$(head -1 <<<"$A_AGENTS")" = "# Stub"
check "app-a AGENTS.md has the marker line" grep -qxF -- "## Repo-specific additions" <<<"$A_AGENTS"
check "app-a CLAUDE.md is @AGENTS.md" test "$(rshow app-a "$BR" CLAUDE.md)" = "@AGENTS.md"
check "app-a payload starts with the version line" test "$(rshow app-a "$BR" .claude/hooks/fleet-guidance.md | head -1)" = "<!-- guidance-version: 1700000000 -->"
check "app-a payload holds base.md" grep -qxF "Rule one." <<<"$(rshow app-a "$BR" .claude/hooks/fleet-guidance.md)"
check "app-a hook copied" test "$(rshow app-a "$BR" .claude/hooks/fleet-memory.sh)" = "$(cat "$G/templates/fleet-memory.sh")"
check "app-a runner copied" test "$(rshow app-a "$BR" .githooks/agent-checks)" = "$(cat "$G/templates/agent-checks")"
check "app-a skips templates that do not exist yet" test -z "$(rshow app-a "$BR" .githooks/check-privacy.py 2>/dev/null)"
A_SET=$(rshow app-a "$BR" .claude/settings.json)
check "app-a settings are valid JSON with 3 managed commands" python3 -I -c '
import json,sys
d=json.loads(sys.argv[1])
cmds=[h["command"] for g in d["hooks"]["SessionStart"]+d["hooks"]["PreToolUse"] for h in g["hooks"]]
assert len(cmds)==3 and all("$CLAUDE_PROJECT_DIR/.claude/hooks/" in c for c in cmds), cmds
assert d["disableSkillShellExecution"] is True
assert d["hooks"]["SessionStart"][0]["matcher"]=="startup|resume|fork"
' "$A_SET"
check "app-a settings are exactly the guide's complete file (4.4)" python3 -I -c '
import json,sys
cmd=lambda n: "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/%s\"" % n
group=lambda m,n,t: {"matcher": m, "hooks": [{"type": "command", "command": cmd(n), "timeout": t}]}
want={"hooks": {"SessionStart": [group("startup|resume|fork", "fleet-memory.sh", 30),
                                 group("startup|resume|fork", "skills-bootstrap.sh", 90)],
                "PreToolUse": [group("Bash|Edit|Write|MultiEdit", "agent-checks-guard.sh", 10)]},
      "disableSkillShellExecution": True}
got=json.loads(sys.argv[1])
assert got==want, got
' "$A_SET"
A_ATTR=$(rshow app-a "$BR" .gitattributes)
check "app-a .gitattributes has both lines" bash -c 'grep -qxF ".claude/hooks/* text eol=lf" <<<"$1" && grep -qxF ".githooks/* text eol=lf" <<<"$1"' _ "$A_ATTR"

# a repo with its own settings keeps them
B_SET=$(rshow app-b "$BR" .claude/settings.json)
check "app-b keeps permissions, model and its own hook; adds ours" python3 -I -c '
import json,sys
d=json.loads(sys.argv[1])
assert d["permissions"]=={"allow":["Bash(ls:*)"]} and d["model"]=="example-model"
ss=d["hooks"]["SessionStart"]
assert ss[0]=={"matcher":"startup","hooks":[{"type":"command","command":"echo own-hook"}]}, ss
assert any(h["command"].endswith("fleet-memory.sh\"") for g in ss for h in g["hooks"])
assert len(ss)==3, len(ss)
' "$B_SET"
# repo-specific AGENTS.md content survives, the managed half is replaced
B_AGENTS=$(rshow app-b "$BR" AGENTS.md)
check "app-b keeps its repo-specific content" has "$B_AGENTS" "Keep this line: TANGERINE-41"
check "app-b managed half is replaced" lacks "$B_AGENTS" "old text"
check "app-b AGENTS.md starts with the stub" test "$(head -1 <<<"$B_AGENTS")" = "# Stub"
check "app-b marker appears once" test "$(grep -cxF '## Repo-specific additions' <<<"$B_AGENTS")" = 1
check "app-b CLAUDE.md is not overwritten" test "$(rshow app-b "$BR" CLAUDE.md)" = "custom claude text"
B_ATTR=$(rshow app-b "$BR" .gitattributes)
check "app-b .gitattributes keeps its line and gains ours on their own lines" bash -c '
[ "$(sed -n 1p <<<"$1")" = "*.png binary" ] && grep -qxF ".claude/hooks/* text eol=lf" <<<"$1" && grep -qxF ".githooks/* text eol=lf" <<<"$1"' _ "$B_ATTR"

# ============================================================ run 2: no-op
tip_a=$(rsha app-a "refs/heads/$BR"); calls=$(wc -l < "$GH_STUB_LOG")
run_sync "$T/list1.yml"
check "run 2 exits 0" test "$RC" = 0
check "run 2 says up to date for both repos" test "$(grep -c 'up to date' <<<"$OUT")" = 2
check "run 2 makes no gh call" test "$(wc -l < "$GH_STUB_LOG")" = "$calls"
check "run 2 pushes nothing" test "$(rsha app-a "refs/heads/$BR")" = "$tip_a"

# ============================================================ run 3: base.md changed
printf '# Base guidance\nRule one.\nRule two.\n' > "$G/agents-md/base.md"
git -C "$G" add -A
GIT_AUTHOR_DATE="1700001000 +0000" GIT_COMMITTER_DATE="1700001000 +0000" git -C "$G" commit -q -m "guidance v2"
run_sync "$T/list1.yml"
check "run 3 exits 0" test "$RC" = 0
check "run 3 adds a commit to the open branch (fast-forward)" bash -c '
git --git-dir="$1" merge-base --is-ancestor "$2" "refs/heads/$3"' _ "$T/remotes/$OWNER/app-a.git" "$tip_a" "$BR"
check "run 3 branch is two commits ahead of main" test "$(git --git-dir="$T/remotes/$OWNER/app-a.git" rev-list --count "main..$BR")" = 2
check "run 3 writes the new version line" test "$(rshow app-a "$BR" .claude/hooks/fleet-guidance.md | head -1)" = "<!-- guidance-version: 1700001000 -->"
check "run 3 opens no second PR" test "$(ghlines 'pr create')" = 2
check "run 3 re-arms auto-merge" test "$(ghlines 'pr merge 7 --repo acme-example/app-a --auto --merge')" = 2
check "run 3 keeps one managed entry per command in settings (idempotent merge)" python3 -I -c '
import json,sys
d=json.loads(sys.argv[1])
cmds=[h["command"] for g in d["hooks"]["SessionStart"]+d["hooks"]["PreToolUse"] for h in g["hooks"]]
assert len(cmds)==len(set(cmds))==3, cmds' "$(rshow app-a "$BR" .claude/settings.json)"

# ============================================================ run 4: app-a merged, main moved
git --git-dir="$T/remotes/$OWNER/app-a.git" update-ref refs/heads/main "$(rsha app-a "refs/heads/$BR")"
git --git-dir="$T/remotes/$OWNER/app-a.git" update-ref -d "refs/heads/$BR"
rm -f "$T/ghstate/${OWNER}_app-a.pr"
(cd "$T/w-app-b" && git pull -q origin main && echo x > other.txt && git add -A && git commit -q -m other && git push -q origin main)
tip_b=$(rsha app-b "refs/heads/$BR"); calls=$(wc -l < "$GH_STUB_LOG")
run_sync "$T/list1.yml"
check "run 4 exits 0" test "$RC" = 0
check "run 4 after a merge: nothing to do, no gh call" test "$(wc -l < "$GH_STUB_LOG")" = "$calls"
check "run 4 open branch behind main but already current: not pushed" test "$(rsha app-b "refs/heads/$BR")" = "$tip_b"

# ============================================================ a diverged branch is refused
printf 'repos:\n  - app-c\n' > "$T/list-c.yml"
tip_c=$(rsha app-c "refs/heads/$BR"); main_c=$(rsha app-c refs/heads/main); calls=$(wc -l < "$GH_STUB_LOG")
run_sync "$T/list-c.yml"
check "diverged: exits non-zero" test "$RC" = 1
check "diverged: says so" has "$OUT" "diverged"
check "diverged: branch untouched" test "$(rsha app-c "refs/heads/$BR")" = "$tip_c"
check "diverged: main untouched" test "$(rsha app-c refs/heads/main)" = "$main_c"
check "diverged: no gh call" test "$(wc -l < "$GH_STUB_LOG")" = "$calls"
check "diverged: the failure is counted" has "$OUT" "1 repo(s) failed"

# ============================================================ a stale merged branch is reused
printf 'repos:\n  - app-e\n' > "$T/list-e.yml"
old_e=$(rsha app-e "refs/heads/$BR")
run_sync "$T/list-e.yml"
check "stale branch: exits 0" test "$RC" = 0
check "stale branch: fast-forwarded by one commit" test "$(git --git-dir="$T/remotes/$OWNER/app-e.git" rev-list --count "$old_e..$BR")" = 1

# ============================================================ invalid settings.json is not overwritten
printf 'repos:\n  - app-f\n' > "$T/list-f.yml"
run_sync "$T/list-f.yml"
check "bad settings: exits non-zero" test "$RC" = 1
check "bad settings: nothing pushed" test -z "$(rsha app-f "refs/heads/$BR")"
check "bad settings: says what to do" has "$OUT" "not valid JSON"

# ============================================================ dry run
printf 'repos:\n  - app-d\n' > "$T/list-d.yml"
calls=$(wc -l < "$GH_STUB_LOG")
run_sync "$T/list-d.yml" --dry-run
check "dry run: exits 0" test "$RC" = 0
check "dry run: prints what would change" has "$OUT" "would change"
check "dry run: lists AGENTS.md" has "$OUT" "AGENTS.md"
check "dry run: says it would open a pull request" has "$OUT" "would create $BR"
check "dry run: pushes nothing" test -z "$(rsha app-d "refs/heads/$BR")"
check "dry run: no gh call" test "$(wc -l < "$GH_STUB_LOG")" = "$calls"

# ============================================================ bad repos.yml entry
printf 'repos:\n  - "app-a; rm -rf /"\n' > "$T/list-bad.yml"
run_sync "$T/list-bad.yml"
check "a bad repos.yml entry is refused before anything runs" test "$RC" = 1

# ============================================================ a workflow change never auto-merges
mkdir -p "$G/templates"; printf 'name: agent-checks\non: pull_request_target\n' > "$G/templates/agent-checks.yml"
git -C "$G" add -A
GIT_AUTHOR_DATE="1700002000 +0000" GIT_COMMITTER_DATE="1700002000 +0000" git -C "$G" commit -q -m "guidance v3"
printf 'repos:\n  - app-d\n' > "$T/list-d.yml"
run_sync "$T/list-d.yml"
check "workflow: exits 0" test "$RC" = 0
check "workflow: the file is on the branch" rshow app-d "$BR" .github/workflows/agent-checks.yml
check "workflow: no auto-merge" test "$(ghlines 'pr merge 7 --repo acme-example/app-d --auto')" = 0
check "workflow: auto-merge turned off" test "$(ghlines 'pr merge 7 --repo acme-example/app-d --disable-auto')" = 1
check "workflow: says to review by hand" has "$OUT" "auto-merge stays off"
printf '# Base guidance\nRule three.\n' > "$G/agents-md/base.md"
git -C "$G" add -A
GIT_AUTHOR_DATE="1700003000 +0000" GIT_COMMITTER_DATE="1700003000 +0000" git -C "$G" commit -q -m "guidance v4"
run_sync "$T/list-d.yml"
check "workflow, later sync of other files: still no auto-merge" test "$(ghlines 'pr merge 7 --repo acme-example/app-d --auto')" = 0

# ============================================================ the delivered agent-checks.yml
# The real template, delivered by sync: its privacy job must skip on a private
# repo (no environment there on a free plan) and for a fork's pull request.
REAL_CHECKS=$HERE/../templates/agent-checks.yml
if [ -f "$REAL_CHECKS" ] && python3 -c 'import yaml' 2>/dev/null; then
  cp "$REAL_CHECKS" "$G/templates/agent-checks.yml"
  git -C "$G" add -A
  GIT_AUTHOR_DATE="1700004000 +0000" GIT_COMMITTER_DATE="1700004000 +0000" git -C "$G" commit -q -m "guidance v5"
  printf 'repos:\n  - app-g\n' > "$T/list-g.yml"
  run_sync "$T/list-g.yml"
  check "delivered agent-checks.yml: sync exits 0" test "$RC" = 0
  rshow app-g "$BR" .github/workflows/agent-checks.yml > "$T/delivered-checks.yml" 2>/dev/null
  check "delivered agent-checks.yml: byte for byte the template" cmp -s "$REAL_CHECKS" "$T/delivered-checks.yml"
  PRIV=$(python3 -I - "$T/delivered-checks.yml" <<'PY'
import os, re, subprocess, sys, yaml
jobs = yaml.safe_load(open(sys.argv[1]))["jobs"]
cond = jobs["privacy-scan"]["if"]
# Evaluate the scan's condition for each case by translating its tokens to Python.
py = re.sub(r"!(?!=)", " not ", cond).replace("&&", " and ").replace("||", " or ")
py = re.sub(r"\b(github(?:\.[a-z_]+)+)", lambda m: f"ctx[{m.group(1)!r}]", py)
# The required context, privacy, is the reporter: run its one step's script
# with the env GitHub would give it, the scan's result included.
report = jobs["privacy"]
assert report["needs"] == "privacy-scan" and report["if"] == "always()" and "environment" not in report
script = report["steps"][0]["run"]
def case(private, event, head, scan_ok=True):
    ctx = {"github.event.repository.private": private, "github.event_name": event,
           "github.event.pull_request.head.repo.full_name": head, "github.repository": "acme-example/app"}
    runs = eval(py, {"ctx": ctx})
    env = dict(os.environ, SCAN=("success" if scan_ok else "failure") if runs else "skipped",
               EVENT=event, HEAD_REPO=head or "", REPO="acme-example/app",
               PRIVATE="true" if private else "false")
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip().replace("\n", " ")
    leak = " LEAK" if head and head.startswith("outsider") and "outsider" in out else ""
    return f"{'runs' if runs else 'skips'}, reports {'pass' if r.returncode == 0 else 'FAIL'}: {out}{leak}"
for private, event, head, ok in [(False, "push", None, True), (False, "pull_request_target", "acme-example/app", True),
                                 (False, "pull_request_target", "acme-example/app", False),
                                 (False, "pull_request_target", "outsider/app", True), (True, "push", None, True),
                                 (True, "pull_request_target", "acme-example/app", True)]:
    print(f"{'private' if private else 'public'} {event} {head} {'ok' if ok else 'dirty'}: {case(private, event, head, ok)}")
PY
)
  check "privacy: a public repo's push is scanned and passes" has "$PRIV" "public push None ok: runs, reports pass"
  check "privacy: a public repo's own pull request is scanned and passes" has "$PRIV" "public pull_request_target acme-example/app ok: runs, reports pass"
  check "privacy: a failed scan fails the required context" has "$PRIV" "public pull_request_target acme-example/app dirty: runs, reports FAIL"
  check "privacy: a fork's pull request is not scanned and FAILS" has "$PRIV" "public pull_request_target outsider/app ok: skips, reports FAIL"
  check "privacy: and says how to bring the change in" has "$PRIV" "bring this change in through a branch of your own"
  check "privacy: and never prints the fork's name" bash -c '! grep -q LEAK <<<"$1"' _ "$PRIV"
  check "privacy: a private repo's push skips and passes" has "$PRIV" "private push None ok: skips, reports pass"
  check "privacy: a private repo's pull request skips and passes" has "$PRIV" "private pull_request_target acme-example/app ok: skips, reports pass"
else
  echo "SKIP: templates/agent-checks.yml or PyYAML missing, delivered privacy job not checked"
fi

# ============================================================ the workflow file
if python3 -c 'import yaml' 2>/dev/null; then
  check "sync.yml: least privilege, environment, deep checkout, pinned App-token step, no template expressions in run:" \
    python3 -I - "$SYNC_YML" <<'PY'
import sys, yaml
w = yaml.safe_load(open(sys.argv[1]))
on = w.get("on", w.get(True))
assert w["permissions"] == {"contents": "read"}
assert on["push"]["paths"] == ["agents-md/**", "templates/**", "scripts/**", "repos.yml"], on["push"]
assert "schedule" in on and "workflow_dispatch" in on
job = w["jobs"]["sync"]
assert job["environment"] == "guidance-sync"
steps = job["steps"]
assert any(s.get("uses", "").startswith("actions/checkout@") and s["with"]["fetch-depth"] == 0 for s in steps)
assert any(s.get("uses", "").startswith("actions/create-github-app-token@") for s in steps)
for s in steps:
    assert "${{" not in s.get("run", ""), s
PY
else
  echo "SKIP: PyYAML missing, sync.yml not checked"
fi

echo "$pass assertions passed, $fail failed"
[ "$fail" = 0 ]
