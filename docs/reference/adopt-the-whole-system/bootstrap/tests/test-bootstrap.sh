#!/usr/bin/env bash
# test-bootstrap.sh: end-to-end tests of the bootstrap scripts.
#
# No network, no sleeps, no wall-clock: gh and claude are stubs on PATH
# (tests/stubs) that log their argv and keep state in a JSON file, and the
# remotes copy-starter.sh pushes to are local bare repos reached through
# url.insteadOf in a scratch GIT_CONFIG_GLOBAL. Everything happens under one
# temp dir, removed at the end.
#
# Run: bash tests/test-bootstrap.sh
# Exit 0 and "N assertions passed" mean it worked; each failed assertion
# prints FAIL and the exit code is 1.

set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # no __pycache__ in a tree that copy-starter.sh may copy
B=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/bootstrap-test.XXXXXX")
trap 'rm -rf "$T"' EXIT
PASSES=0 FAILS=0
ok()   { PASSES=$((PASSES + 1)); }
bad()  { FAILS=$((FAILS + 1)); printf 'FAIL: %s\n' "$*"; }
assert()      { local d=$1; shift; if "$@" >/dev/null 2>&1; then ok; else bad "$d"; fi; }
assert_not()  { local d=$1; shift; if "$@" >/dev/null 2>&1; then bad "$d"; else ok; fi; }
assert_eq()   { if [ "$2" = "$3" ]; then ok; else bad "$1: expected [$3], got [$2]"; fi; }
has()         { grep -qF -- "$2" "$1"; }       # file contains text
writes()      { python3 -I -c '
import json, sys
n = 0
for line in open(sys.argv[1], encoding="utf-8"):
    a = json.loads(line)
    if a[0] == "api":
        m = a[a.index("-X") + 1] if "-X" in a else "GET"
        n += m != "GET"
    elif a[:2] in (["repo", "create"], ["variable", "set"], ["secret", "set"], ["pr", "create"]):
        n += 1
print(n)' "$1"; }
log_lines()   { wc -l < "$1" | tr -d ' '; }
jqs()         { jq -r "$1" "$GH_STUB_STATE"; }

export PATH="$B/tests/stubs:$PATH"
export TMPDIR="$T/tmp"; mkdir -p "$TMPDIR"
export GH_LOG="$T/gh.log" CLAUDE_LOG="$T/claude.log" CLAUDE_STUB_STATE="$T/claude.state"
export GH_STUB_STATE="$T/gh-state.json"
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$T/gitconfig"
unset GH_TOKEN GITHUB_TOKEN CLAUDE_CONFIG_DIR
touch "$GH_LOG"
git config --global user.name "Test User"
git config --global user.email "test-user@example.com"
git config --global init.defaultBranch main
git config --global protocol.file.allow always
git config --global "url.file://$T/remotes/.insteadOf" "https://github.com/"

SCRIPTS=(create-repos.sh create-environments.sh create-apps.sh set-secrets.sh copy-starter.sh apply-rulesets.sh install-machine.sh verify.sh)
OUT="$T/out" ERR="$T/err"
run() { "$@" >"$OUT" 2>"$ERR"; }   # run a script, keep its stdout and stderr

# Unique values that must never leave the stubs' memory.
PEM_MARK="PEMBODY$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
CS_MARK="CLIENTSECRET$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
BEARER="bearer-$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
export GH_STUB_FAKE_PEM=$'-----BEGIN FAKE KEY-----\n'"$PEM_MARK"$'\n-----END FAKE KEY-----\n'
export GH_STUB_CLIENT_SECRET="$CS_MARK"
sha() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }

new_config() { # path plan
  cat > "$1" <<EOF
# test config
OWNER=acme-example
OWNER_TYPE=user
PLAN=$2
OWNER_NAME=Acme Example
GUIDANCE_SYNC_APP_ID=
LOOP_WRITER_APP_ID=
EOF
}
CFG="$T/fleet.env"; new_config "$CFG" free

# ======================================================== syntax and lint
for s in "${SCRIPTS[@]}" lib/common.sh lib/rulesets.sh tests/stubs/gh tests/stubs/claude; do
  assert "bash -n $s" bash -n "$B/$s"
done
if command -v shellcheck >/dev/null 2>&1; then
  for s in "${SCRIPTS[@]}"; do assert "shellcheck $s" shellcheck -x -S warning "$B/$s"; done
else
  echo "note: shellcheck not installed; skipped"
fi
for s in "${SCRIPTS[@]}"; do
  assert "$s has a usage header" grep -q '^# .*\.sh' "$B/$s"
  assert "$s uses set -euo pipefail" grep -qx 'set -euo pipefail' "$B/$s"
done
for f in "$B"/rulesets/*.json "$B"/rulesets/phase4/*.json; do assert "$(basename "$f") is JSON" jq -e . "$f"; done

# ======================================================== config parsing
printf 'OWNER=acme-example\nPLAN=free\nTOKEN=x\n' > "$T/bad1.env"
assert_not "an unknown config key is refused" bash "$B/create-repos.sh" --config "$T/bad1.env" --dry-run
printf 'OWNER=$(touch %s/pwned)\nPLAN=free\n' "$T" > "$T/bad2.env"
assert_not "a shell expression as OWNER is refused" bash "$B/create-repos.sh" --config "$T/bad2.env" --dry-run
assert_not "the config is never sourced" test -e "$T/pwned"
printf 'OWNER=acme-example\nPLAN=cheap\n' > "$T/bad3.env"
assert_not "an invalid PLAN is refused" bash "$B/create-repos.sh" --config "$T/bad3.env" --dry-run
assert_not "a missing config is refused" bash "$B/create-repos.sh" --config "$T/none.env" --dry-run

# ======================================================== create-repos.sh
run bash "$B/create-repos.sh" --config "$CFG" --dry-run; rc=$?
assert_eq "create-repos dry run exits 0" "$rc" 0
assert_eq "create-repos dry run makes no write calls" "$(writes "$GH_LOG")" 0
assert "create-repos dry run prints the repo create call" has "$OUT" "DRY-RUN: gh repo create acme-example/agent-skills-private --private"
assert "create-repos dry run prints the merge settings call" grep -q 'DRY-RUN: gh api -X PATCH repos/acme-example/agent-guidance' "$OUT"
assert "create-repos dry run says nothing was written" has "$OUT" "nothing was written"

run bash "$B/create-repos.sh" --config "$CFG"; rc=$?
assert_eq "create-repos exits 0" "$rc" 0
for r in agent-guidance agent-skills skills-evals; do
  assert_eq "free: $r is public" "$(jqs ".repos[\"acme-example/$r\"].visibility")" public
done
for r in agent-skills-private repo-settings; do
  assert_eq "free: $r is private" "$(jqs ".repos[\"acme-example/$r\"].visibility")" private
done
assert_eq "every repo has a first commit" "$(jqs '[.repos[] | select(.has_commits)] | length')" 5
assert_not "repos are created without GitHub's own README" grep -qF -- '--add-readme' "$GH_LOG"
assert_eq "every README is the generated one, ending in the marker" \
  "$(jqs '[.repos[] | select(. as $r | ($r.readme // "") | startswith("# " + $r.name + "\n") and endswith("copy-starter.sh replaces this file while it is unchanged -->\n"))] | length')" 5
assert_eq "merge settings on all five" \
  "$(jqs '[.repos[] | select(.allow_merge_commit and (.allow_squash_merge|not) and (.allow_rebase_merge|not) and .allow_auto_merge and .delete_branch_on_merge)] | length')" 5
assert_eq "SHA pinning on all five" "$(jqs '[.repos[] | select(.actions.sha_pinning_required)] | length')" 5
assert_eq "read-only token on all five" "$(jqs '[.repos[] | select(.workflow.default_workflow_permissions == "read" and (.workflow.can_approve_pull_request_reviews|not))] | length')" 5
assert_eq "fork approval on the three public repos" "$(jqs '[.repos[] | select(.fork.approval_policy == "all_external_contributors")] | length')" 3
assert_eq "labels on all five" "$(jqs '[.repos[] | select([.labels[].name] | index("agent-ready") and index("on-hold") and index("agent-authored") and index("vendor-release") and index("agent-check"))] | length')" 5
assert_eq "every label it creates carries a description" "$(jqs '[.repos[].labels[] | select(.name != "bug" and .name != "enhancement") | select((.description // "") == "")] | length')" 0
n=$(log_lines "$GH_LOG")
run bash "$B/create-repos.sh" --config "$CFG"; rc=$?
assert_eq "create-repos second run exits 0" "$rc" 0
assert_eq "create-repos second run makes no write calls" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 0
assert "create-repos second run says nothing to change" has "$OUT" "Nothing to change"

# An empty existing repo gets a README through the contents API.
jq '.repos["acme-example/agent-skills"].has_commits = false' "$GH_STUB_STATE" > "$T/s" && mv "$T/s" "$GH_STUB_STATE"
run bash "$B/create-repos.sh" --config "$CFG" agent-skills
assert "an empty repo gets a README commit" grep -q '"PUT", "repos/acme-example/agent-skills/contents/README.md"' "$GH_LOG"
# A visibility mismatch is reported and not changed.
jq '.repos["acme-example/repo-settings"].visibility = "public" | .repos["acme-example/repo-settings"].private = false' "$GH_STUB_STATE" > "$T/s" && mv "$T/s" "$GH_STUB_STATE"
run bash "$B/create-repos.sh" --config "$CFG" repo-settings; rc=$?
assert_eq "a visibility mismatch fails the run" "$rc" 1
assert "a visibility mismatch is named" has "$ERR" "repo-settings exists but is public"
assert_eq "a visibility mismatch is not changed" "$(jqs '.repos["acme-example/repo-settings"].visibility')" public
jq '.repos["acme-example/repo-settings"].visibility = "private" | .repos["acme-example/repo-settings"].private = true' "$GH_STUB_STATE" > "$T/s" && mv "$T/s" "$GH_STUB_STATE"

# ======================================================== create-environments.sh
n=$(log_lines "$GH_LOG")
run bash "$B/create-environments.sh" --config "$CFG" --dry-run; rc=$?
assert_eq "create-environments dry run exits 0" "$rc" 0
assert_eq "create-environments dry run makes no write calls" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 0
run bash "$B/create-environments.sh" --config "$CFG"; rc=$?
assert_eq "create-environments exits 0" "$rc" 0
for e in agent-guidance:guidance-sync agent-guidance:privacy agent-skills:privacy agent-skills:plugin-runtime-review \
         skills-evals:privacy skills-evals:loop-writer skills-evals:routine-fire skills-evals:lessons-reader; do
  r=${e%%:*} n2=${e#*:}
  assert_eq "environment $n2 in $r deploys only from main" \
    "$(jqs ".envs[\"acme-example/$r\"][\"$n2\"] | [.deployment_branch_policy.custom_branch_policies, ([.policies[].name] | join(\",\"))] | join(\"/\")")" "true/main"
done
assert_eq "free: no environment in private repo-settings" "$(jqs '.envs["acme-example/repo-settings"] // {} | length')" 0
assert "free: the skip says what the guide says instead" has "$OUT" "from your own machine with gh instead of from a workflow"
assert_eq "plugin-runtime-review requires the owner" \
  "$(jqs '.envs["acme-example/agent-skills"]["plugin-runtime-review"].protection_rules[0].reviewers[0].reviewer.id')" \
  "$(python3 -c 'print(5000 + sum(map(ord, "acme-example")))')"
assert_eq "privacy has no reviewer" "$(jqs '.envs["acme-example/agent-guidance"].privacy.protection_rules | length')" 0
assert_eq "roster-writer is optional and skipped" "$(jqs '.envs["acme-example/skills-evals"]["roster-writer"] // "none"')" none
n=$(log_lines "$GH_LOG")
run bash "$B/create-environments.sh" --config "$CFG"; rc=$?
assert_eq "create-environments second run exits 0" "$rc" 0
assert_eq "create-environments second run makes no write calls" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 0
assert "create-environments second run says nothing to change" has "$OUT" "Nothing to change"

# ======================================================== apply-rulesets.sh
n=$(log_lines "$GH_LOG")
run bash "$B/apply-rulesets.sh" --config "$CFG" --all --dry-run; rc=$?
assert_eq "apply-rulesets dry run exits 0" "$rc" 0
assert_eq "apply-rulesets dry run makes no write calls" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 0
run bash "$B/apply-rulesets.sh" --config "$CFG" --all; rc=$?
assert_eq "apply-rulesets exits 0" "$rc" 0
assert "apply-rulesets reads back what it wrote" has "$OUT" "read back: acme-example/agent-guidance ruleset default-branch matches its file"
assert_eq "free: no ruleset on private repos" "$(jqs '(.rulesets["acme-example/repo-settings"] // {} | length) + (.rulesets["acme-example/agent-skills-private"] // {} | length)')" 0
assert "free: the private-repo skip is explained" has "$OUT" "rulesets are not enforced there"
assert_eq "without App IDs the persistent ruleset is deletion and non_fast_forward" \
  "$(jqs '[.rulesets["acme-example/agent-guidance"][] | select(.name == "persistent-branches") | .rules[].type] | join(",")')" deletion,non_fast_forward
assert_eq "without App IDs no repo has a persistent bypass" \
  "$(jqs '[.rulesets[][] | select(.name == "persistent-branches") | .bypass_actors[]] | length')" 0
assert_eq "default-branch ruleset has no required check before phase 4" \
  "$(jqs '[.rulesets["acme-example/agent-skills"][] | select(.name == "default-branch") | .rules[].type] | join(",")')" "deletion,non_fast_forward,pull_request"
n=$(log_lines "$GH_LOG")
run bash "$B/apply-rulesets.sh" --config "$CFG" --all; rc=$?
assert_eq "apply-rulesets second run exits 0" "$rc" 0
assert_eq "apply-rulesets second run makes no write calls" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 0
assert "apply-rulesets second run says nothing to change" has "$OUT" "Nothing to change"

# ======================================================== create-apps.sh
MAN="$T/manifests"
n=$(log_lines "$GH_LOG")
run bash "$B/create-apps.sh" --config "$CFG" --out "$MAN" --dry-run </dev/null; rc=$?
assert_eq "create-apps dry run exits 0" "$rc" 0
assert_eq "create-apps dry run makes no write calls" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 0
assert "free: create-apps skips settings-admin" has "$OUT" "skip: settings-admin"
printf 'https://example.com/?code=code1\n code2 \nhttps://example.com/?code=code3\n' \
  | bash "$B/create-apps.sh" --config "$CFG" --out "$MAN" >"$OUT" 2>"$ERR"; rc=$?
assert_eq "create-apps exits 0" "$rc" 0
assert "the guidance-sync manifest asks for the guide's permissions" \
  jq -e '.default_permissions == {"contents":"write","pull_requests":"write","workflows":"write"} and .hook_attributes.active == false and .redirect_url == "https://example.com/" and .name == "acme-example-guidance-sync"' "$MAN/guidance-sync.manifest.json"
assert "the loop-writer manifest asks for the guide's permissions" \
  jq -e '.default_permissions == {"contents":"write","pull_requests":"write","issues":"write"}' "$MAN/loop-writer.manifest.json"
assert "the lessons-reader manifest asks for contents: read" jq -e '.default_permissions == {"contents":"read"}' "$MAN/lessons-reader.manifest.json"
assert "the HTML posts to GitHub's new App page" grep -q 'action="https://github.com/settings/apps/new?state=' "$MAN/guidance-sync.html"
assert "the HTML auto-submits" has "$MAN/guidance-sync.html" '.submit()'
assert "the conversion is called with the code" grep -qF '["api", "-X", "POST", "app-manifests/code1/conversions"]' "$GH_LOG"
assert_eq "the PEM reached GUIDANCE_SYNC_PEM on stdin" \
  "$(jqs '.values["acme-example/agent-guidance|guidance-sync|secret"].GUIDANCE_SYNC_PEM')" "$(sha "${GH_STUB_FAKE_PEM%$'\n'}")"
assert_eq "the client ID reached GUIDANCE_SYNC_CLIENT_ID" \
  "$(jqs '.values["acme-example/agent-guidance|guidance-sync|variable"].GUIDANCE_SYNC_CLIENT_ID')" "$(sha Iv23liExample1)"
assert_eq "LOOP_WRITER_PEM is in the loop-writer environment" \
  "$(jqs '.values["acme-example/skills-evals|loop-writer|secret"] | keys | join(",")')" LOOP_WRITER_PEM
assert "the App IDs are recorded in fleet.env" grep -qx 'GUIDANCE_SYNC_APP_ID=900001' "$CFG"
assert "the loop-writer ID is recorded in fleet.env" grep -qx 'LOOP_WRITER_APP_ID=900002' "$CFG"
assert "the lessons-reader ID is appended to fleet.env" grep -qx 'LESSONS_READER_APP_ID=900003' "$CFG"
assert "fleet.env keeps its other lines" grep -qx 'OWNER_NAME=Acme Example' "$CFG"
assert "the install URL is printed" has "$OUT" "https://github.com/apps/acme-example-app-1/installations/new"
assert "the repos to pick are printed" has "$OUT" "Only select repositories: agent-skills-private"
# The private key and client secret are nowhere: not in the gh log, the
# output, fleet.env, the manifests, the temp dir or the bootstrap folder.
for m in "$PEM_MARK" "$CS_MARK"; do
  assert_not "a secret is not in the gh argv log" grep -rqF -- "$m" "$GH_LOG"
  assert_not "a secret is not on stdout or stderr" grep -qF -- "$m" "$OUT" "$ERR"
  assert_not "a secret is in no file left behind" grep -rqF -- "$m" "$T" "$B"
done
assert_eq "create-apps leaves nothing in TMPDIR" "$(find "$TMPDIR" -mindepth 1 | wc -l | tr -d ' ')" 0
n=$(log_lines "$GH_LOG")
run bash "$B/create-apps.sh" --config "$CFG" --out "$MAN" </dev/null; rc=$?
assert_eq "create-apps second run exits 0" "$rc" 0
assert_eq "create-apps second run makes no write calls" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 0
assert "create-apps second run says nothing to change" has "$OUT" "Nothing to change"
# A failed conversion prints the status, never the body.
cp "$CFG" "$T/fresh.env"; sed -i '/_APP_ID=/d' "$T/fresh.env"
printf 'expired\n' | bash "$B/create-apps.sh" --config "$T/fresh.env" --out "$MAN" guidance-sync >"$OUT" 2>"$ERR"; rc=$?
assert_eq "a failed conversion fails the run" "$rc" 1
assert "a failed conversion names the HTTP status" has "$ERR" "HTTP 404"
assert_not "a failed conversion never prints the response body" grep -qF BODY-MUST-NOT-BE-PRINTED "$OUT" "$ERR"
printf 'https://example.com/?code=abc&state=0000\n' | bash "$B/create-apps.sh" --config "$T/fresh.env" --out "$MAN" guidance-sync >"$OUT" 2>"$ERR"; rc=$?
assert_eq "a redirect URL with another run's state is refused" "$rc" 1

# ======================================================== set-secrets.sh
mkdir -p "$T/private"; printf '# real names\nAlice Example\nalice@example\\.net\n' > "$T/private/denylist"; chmod 600 "$T/private/denylist"
n=$(log_lines "$GH_LOG")
run bash "$B/set-secrets.sh" --config "$CFG" --dry-run --denylist "$T/private/denylist" </dev/null; rc=$?
assert_eq "set-secrets dry run exits 0" "$rc" 0
assert_eq "set-secrets dry run makes no write calls" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 0
mkdir -p "$T/inrepo"; git -C "$T/inrepo" init -q; cp "$T/private/denylist" "$T/inrepo/denylist"
printf '%s\ntrig_01Example\n' "$BEARER" | bash "$B/set-secrets.sh" --config "$CFG" --denylist "$T/inrepo/denylist" >"$OUT" 2>"$ERR"; rc=$?
assert_eq "a denylist inside a git work tree is refused" "$rc" 1
printf 'ok\n(unclosed\n' > "$T/private/broken"
printf '%s\ntrig_01Example\n' "$BEARER" | bash "$B/set-secrets.sh" --config "$CFG" --denylist "$T/private/broken" >"$OUT" 2>"$ERR"; rc=$?
assert_eq "a denylist with a bad pattern is refused" "$rc" 1
assert "the bad pattern is named by line number only" has "$ERR" "line(s) 2"
assert_not "the bad pattern text is not printed" grep -qF '(unclosed' "$OUT" "$ERR"
printf '%s\ntrig_01Example\n' "$BEARER" | bash "$B/set-secrets.sh" --config "$CFG" --denylist "$T/private/denylist" >"$OUT" 2>"$ERR"; rc=$?
assert_eq "set-secrets exits 0" "$rc" 0
assert_eq "the bearer reached EVAL_ROUTINE_FIRE_BEARER" \
  "$(jqs '.values["acme-example/skills-evals|routine-fire|secret"].EVAL_ROUTINE_FIRE_BEARER')" "$(sha "$BEARER")"
assert_eq "ROUTINE_TRIGGER_ID is a variable in routine-fire" \
  "$(jqs '.values["acme-example/skills-evals|routine-fire|variable"].ROUTINE_TRIGGER_ID')" "$(sha trig_01Example)"
for r in agent-guidance agent-skills skills-evals; do
  assert_eq "PRIVACY_DENYLIST in $r" "$(jqs ".values[\"acme-example/$r|privacy|secret\"].PRIVACY_DENYLIST")" "$(sha256sum < "$T/private/denylist" | cut -d' ' -f1)"
done
assert "free: privacy in private repo-settings is skipped" has "$OUT" "skip: acme-example/repo-settings/privacy"
assert_not "the bearer is not in the gh log" grep -qF -- "$BEARER" "$GH_LOG"
assert_not "the bearer is not on stdout or stderr" grep -qF -- "$BEARER" "$OUT" "$ERR"
assert_not "the bearer is in no file left behind" grep -rqF -- "$BEARER" "$T" "$B"
assert_not "denylist patterns are not printed" grep -qF 'Alice Example' "$OUT" "$ERR"
run bash "$B/set-secrets.sh" --config "$CFG" --rotate --only privacy --denylist "$T/private/denylist" </dev/null; rc=$?
assert_eq "--only privacy needs no bearer prompt" "$rc" 0
assert_not "--only privacy does not ask for the bearer" grep -qF EVAL_ROUTINE_FIRE_BEARER "$ERR"
n=$(log_lines "$GH_LOG")
run bash "$B/set-secrets.sh" --config "$CFG" --denylist "$T/private/denylist" </dev/null; rc=$?
assert_eq "set-secrets second run exits 0 without prompting" "$rc" 0
assert_eq "set-secrets second run makes no write calls" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 0
assert "set-secrets second run says nothing to change" has "$OUT" "Nothing to change"
# An unreadable environment names ITS status: env_names runs in the caller's
# shell (not in $( )), so the GH_HTTP it sets is the one the message reads.
GH_STUB_ENV_LIST_STATUS=403 run bash "$B/set-secrets.sh" --config "$CFG" --denylist "$T/private/denylist" </dev/null; rc=$?
assert_not "set-secrets with an unreadable environment fails" test "$rc" = 0
assert "and that status is 403" bash -c 'cat "$1" "$2" | grep -F "could not list" | grep -qF "(HTTP 403)"' _ "$OUT" "$ERR"

# ======================================================== rulesets with App IDs, drift
n=$(log_lines "$GH_LOG")
run bash "$B/apply-rulesets.sh" --config "$CFG" agent-guidance; rc=$?
assert_eq "apply-rulesets with App IDs exits 0" "$rc" 0
assert_eq "with App IDs: one PUT (persistent-branches)" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 1
PB() { jqs "[.rulesets[\"acme-example/$1\"][] | select(.name == \"persistent-branches\") | $2] | join(\",\")"; }
assert_eq "agent-guidance: only guidance-sync may write persistent/**" "$(PB agent-guidance '.bypass_actors[].actor_id | tostring')" 900001
assert_eq "agent-guidance: with its App ID, all four rules" "$(PB agent-guidance '.rules[].type')" "deletion,non_fast_forward,update,creation"
run bash "$B/apply-rulesets.sh" --config "$CFG" skills-evals agent-skills; rc=$?
assert_eq "skills-evals: only loop-writer may write persistent/**" "$(PB skills-evals '.bypass_actors[].actor_id | tostring')" 900002
assert_eq "skills-evals: with its App ID, all four rules" "$(PB skills-evals '.rules[].type')" "deletion,non_fast_forward,update,creation"
assert_eq "the ruleset file's full form names the same four rules" \
  "$(jq -r '[.rules[].type] | join(",")' "$B/rulesets/persistent-branches.json")" "deletion,non_fast_forward,update,creation"
assert_eq "creation is bypassed only by the repo's own App" "$(PB skills-evals '[.bypass_actors[] | .actor_type + ":" + (.actor_id | tostring)] | join(",")')" "Integration:900002"
assert_eq "agent-skills: no App bypass even with both App IDs" "$(PB agent-skills '.bypass_actors[].actor_id | tostring')" ""
assert_eq "agent-skills: deletion and non_fast_forward only" "$(PB agent-skills '.rules[].type')" "deletion,non_fast_forward"
# A writer repo gets its full ruleset as soon as ITS App ID exists.
ONE="$T/one-app.env"; sed '/^LOOP_WRITER_APP_ID=/d' "$CFG" > "$ONE"
run bash "$B/apply-rulesets.sh" --config "$ONE" --all --dry-run; rc=$?
assert "with only guidance-sync's ID, agent-guidance is full" has "$OUT" "acme-example/agent-guidance persistent-branches: full: guidance-sync may write"
assert "and skills-evals waits for LOOP_WRITER_APP_ID" has "$OUT" "acme-example/skills-evals persistent-branches: deletion and non_fast_forward only: LOOP_WRITER_APP_ID is not in fleet.env yet"
run bash "$B/apply-rulesets.sh" --config "$CFG" --with-required-check agent-skills; rc=$?
assert_eq "--with-required-check exits 0" "$rc" 0
assert_eq "--with-required-check adds agent-checks and privacy from Actions" \
  "$(jqs '[.rulesets["acme-example/agent-skills"][] | select(.name == "default-branch") | .rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[] | "\(.context):\(.integration_id)"] | join(",")')" \
  "agent-checks:15368,privacy:15368,plugin-runtime-review:15368"
assert "--with-required-check records REQUIRED_CHECKS=on" grep -qx 'REQUIRED_CHECKS=on' "$CFG"
run bash "$B/apply-rulesets.sh" --config "$CFG" skills-evals; rc=$?
assert_eq "plugin-runtime-review is required in agent-skills only" \
  "$(jqs '[.rulesets["acme-example/skills-evals"][] | select(.name == "default-branch") | .rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context] | join(",")')" \
  "agent-checks,privacy"
# Every context the phase 4 ruleset makes required, in EVERY fleet repo, is
# reported by a job that always runs on a pull request in that repo: its own
# starter workflows, plus what the sync delivers (templates/agent-checks.yml)
# when the repo is in agent-guidance's repos.yml. A required context nothing
# reports blocks every merge there for good.
REQ=$(HERE=$B bash -c 'set -e; . "$1/lib/common.sh"; . "$1/lib/rulesets.sh"
  for r in "${FLEET_REPOS[@]}"; do build_ruleset default-branch "$r" 1
    printf "%s\t%s\n" "$r" "$(jq -r "[.rules[] | select(.type == \"required_status_checks\") | .parameters.required_status_checks[].context] | join(\",\")" <<<"$RULESET_JSON")"
  done' _ "$B")
assert_eq "phase 4 requires a context in every fleet repo" "$(grep -c $'\t[a-z]' <<<"$REQ")" 5
assert "every required context is reported by a job that always runs on a pull request in that repo" \
  python3 -I - "$B/../starter" "$REQ" <<'PY'
import sys, pathlib, yaml
starter, req = pathlib.Path(sys.argv[1]), sys.argv[2]
synced = {e.split("/", 1)[1] for e in yaml.safe_load((starter / "agent-guidance" / "repos.yml").read_text())["repos"]}
def reported(paths):
    names = set()
    for wf in paths:
        doc = yaml.safe_load(wf.read_text())
        trig = doc.get(True, doc.get("on")) or {}
        if not any(t in trig for t in ("pull_request", "pull_request_target")):
            continue
        for jid, job in doc["jobs"].items():
            if "if" not in job or job["if"] == "always()":
                names.add(job.get("name", jid))
    return names
bad = []
for line in req.strip().splitlines():
    repo, ctxs = line.split("\t")
    wfs = list((starter / repo / ".github" / "workflows").glob("*.yml"))
    if repo in synced:
        wfs.append(starter / "agent-guidance" / "templates" / "agent-checks.yml")
    have = reported(wfs)
    bad += [(repo, c) for c in ctxs.split(",") if c not in have]
assert not bad, bad
PY
run bash "$B/apply-rulesets.sh" --config "$CFG" agent-skills; rc=$?
assert_eq "a later plain run keeps the required checks" \
  "$(jqs '[.rulesets["acme-example/agent-skills"][] | select(.name == "default-branch") | .rules[].type] | join(",")')" "deletion,non_fast_forward,pull_request,required_status_checks"
assert "and says it read the choice" has "$OUT" "required checks on"
run bash "$B/apply-rulesets.sh" --config "$CFG" --with-required-check --without-required-check agent-skills; rc=$?
assert_eq "both flags together are refused" "$rc" 1
cp "$CFG" "$T/cfg.before"
run bash "$B/apply-rulesets.sh" --config "$CFG" --without-required-check --dry-run agent-skills; rc=$?
assert "a dry run does not rewrite fleet.env" cmp -s "$CFG" "$T/cfg.before"
run bash "$B/apply-rulesets.sh" --config "$CFG" --without-required-check agent-skills; rc=$?   # back to the phase 1 shape
assert "--without-required-check records REQUIRED_CHECKS=off" grep -qx 'REQUIRED_CHECKS=off' "$CFG"
assert_eq "--without-required-check removes the checks" \
  "$(jqs '[.rulesets["acme-example/agent-skills"][] | select(.name == "default-branch") | .rules[].type] | join(",")')" "deletion,non_fast_forward,pull_request"
GH_STUB_DRIFT=1 run bash "$B/apply-rulesets.sh" --config "$CFG" skills-evals; rc=$?
assert_eq "a mismatched read-back fails apply-rulesets" "$rc" 1
assert "the drift is reported" has "$ERR" "DRIFT: acme-example/skills-evals ruleset"
assert_not "the drift report prints no response body" grep -qF '"_links"' "$OUT" "$ERR"
run bash "$B/apply-rulesets.sh" --config "$CFG" skills-evals; rc=$?
assert_eq "without drift the same repo passes" "$rc" 0

# --rulesets-dir: the files come from the named folder (a repo-settings
# checkout's rulesets/), not bootstrap's own.
RS="$T/rulesets-copy"; cp -R "$B/rulesets" "$RS"
jq '.enforcement = "evaluate"' "$B/rulesets/default-branch.json" > "$RS/default-branch.json"
run bash "$B/apply-rulesets.sh" --config "$CFG" --rulesets-dir "$RS" skills-evals; rc=$?
assert_eq "--rulesets-dir exits 0" "$rc" 0
assert "--rulesets-dir names the folder it read" has "$OUT" "rulesets from $RS"
assert_eq "--rulesets-dir applies that folder's file" \
  "$(jqs '[.rulesets["acme-example/skills-evals"][] | select(.name == "default-branch") | .enforcement] | join(",")')" evaluate
run bash "$B/apply-rulesets.sh" --config "$CFG" --rulesets-dir="$RS" skills-evals; rc=$?
assert "--rulesets-dir=<dir> works, and a second run changes nothing" has "$OUT" "Nothing to change"
run bash "$B/apply-rulesets.sh" --config "$CFG" skills-evals; rc=$?
assert_eq "without --rulesets-dir, bootstrap's own rulesets/ is the default" \
  "$(jqs '[.rulesets["acme-example/skills-evals"][] | select(.name == "default-branch") | .enforcement] | join(",")')" active
run bash "$B/apply-rulesets.sh" --config "$CFG" --rulesets-dir "$T/no-such-dir" skills-evals; rc=$?
assert_eq "a missing --rulesets-dir fails" "$rc" 1
mkdir -p "$T/rulesets-empty"
run bash "$B/apply-rulesets.sh" --config "$CFG" --rulesets-dir "$T/rulesets-empty" skills-evals; rc=$?
assert_eq "a --rulesets-dir without the ruleset files fails before any call" "$rc" 1
assert "and names the missing file" has "$ERR" "missing ruleset file $T/rulesets-empty/default-branch.json"

# The kit ships the rulesets (and the script that applies them) twice: here and
# in starter/repo-settings/. The two copies must not drift apart silently.
RSET="$B/../starter/repo-settings"
assert "starter/repo-settings/ exists beside bootstrap/" test -d "$RSET/rulesets"
assert "starter/repo-settings/rulesets/ is byte for byte bootstrap/rulesets/" diff -r "$B/rulesets" "$RSET/rulesets"
for f in apply-rulesets.sh lib/common.sh lib/rulesets.sh; do
  assert "starter/repo-settings/bin/$f is byte for byte bootstrap/$f" cmp -s "$B/$f" "$RSET/bin/$f"
done

# Paid plan: private repos get rulesets and environments; agent-skills-private
# keeps deletion and non_fast_forward with no bypass.
PAID="$T/paid"; mkdir -p "$PAID"
(
  export GH_STUB_STATE="$PAID/state.json" GH_LOG="$PAID/gh.log" GH_STUB_PLAN=paid; touch "$GH_LOG"
  new_config "$PAID/fleet.env" paid
  printf 'GUIDANCE_SYNC_APP_ID=11\nLOOP_WRITER_APP_ID=22\n' >> "$PAID/fleet.env"; sed -i '/_APP_ID=$/d' "$PAID/fleet.env"
  bash "$B/create-repos.sh" --config "$PAID/fleet.env" >/dev/null 2>&1 || exit 11
  bash "$B/create-environments.sh" --config "$PAID/fleet.env" >/dev/null 2>&1 || exit 12
  bash "$B/apply-rulesets.sh" --config "$PAID/fleet.env" --all --with-required-check >/dev/null 2>&1 || exit 13
); rc=$?
assert_eq "paid plan setup exits 0" "$rc" 0
PJ() { jq -r "$1" "$PAID/state.json"; }
assert_eq "paid: repo-settings has settings-admin and privacy" "$(PJ '.envs["acme-example/repo-settings"] | keys | join(",")')" "privacy,settings-admin"
assert_eq "paid: agent-skills-private persistent ruleset" \
  "$(PJ '[.rulesets["acme-example/agent-skills-private"][] | select(.name == "persistent-branches") | (.rules | map(.type) | join(",")), (.bypass_actors | length | tostring)] | join("/")')" "deletion,non_fast_forward/0"
assert_eq "paid: agent-skills-private requires only agent-checks" \
  "$(PJ '[.rulesets["acme-example/agent-skills-private"][] | select(.name == "default-branch") | .rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context] | join(",")')" agent-checks
assert_eq "paid: fork approval is not set on private repos (GitHub answers 422)" \
  "$(PJ '.repos["acme-example/repo-settings"].fork.approval_policy')" first_time_contributors

# ======================================================== repository variables
# create-environments.sh again, as the README says after create-apps.sh: the
# repository variables pick up the App IDs fleet.env now holds.
n=$(log_lines "$GH_LOG")
run bash "$B/create-environments.sh" --config "$CFG"; rc=$?
assert_eq "create-environments after create-apps exits 0" "$rc" 0
RV() { jqs ".values[\"acme-example/$1||variable\"].$2 // \"<unset>\""; }
assert_eq "skills-evals REGISTRY_REPO names the registry" "$(RV skills-evals REGISTRY_REPO)" acme-example/agent-skills
assert_eq "skills-evals GUIDANCE_REPO names the guidance repo" "$(RV skills-evals GUIDANCE_REPO)" acme-example/agent-guidance
assert_eq "skills-evals LESSONS_REPO names the private registry" "$(RV skills-evals LESSONS_REPO)" acme-example/agent-skills-private
for key in LOOP_WRITER_APP_ID GUIDANCE_SYNC_APP_ID REQUIRED_CHECKS OWNER_TYPE; do
  want=$(sed -n "s/^$key=//p" "$CFG" | tail -n 1)
  case $key in REQUIRED_CHECKS) want=${want:-off} ;; OWNER_TYPE) want=${want:-user} ;; esac
  assert_eq "repo-settings mirrors $key from fleet.env" "$(RV repo-settings "$key")" "$want"
done
assert_eq "an empty fleet.env key leaves its variable unset" "$(RV repo-settings VISIBILITY_AGENT_GUIDANCE)" "<unset>"
assert "values went on stdin, never in argv" bash -c '! grep -E "\"variable\", \"set\".*\"--body\"" "$1"' _ "$GH_LOG"
n=$(log_lines "$GH_LOG")
run bash "$B/create-environments.sh" --config "$CFG"; rc=$?
assert_eq "create-environments, variables in place: no write calls" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 0
assert "and it says so" has "$OUT" "ok: acme-example/skills-evals variable REGISTRY_REPO"

# ======================================================== verify.sh
n=$(log_lines "$GH_LOG")
run bash "$B/verify.sh" --config "$CFG"; rc=$?
assert_eq "verify passes on the complete free setup" "$rc" 0
assert "verify reports passes" grep -q '^PASS: acme-example/skills-evals environment routine-fire has secret EVAL_ROUTINE_FIRE_BEARER' "$OUT"
assert "verify skips private-repo environments on free" grep -q '^SKIP: acme-example/repo-settings environment settings-admin' "$OUT"
# --phase N: only what should exist by the end of phase N (guide quick start).
run bash "$B/verify.sh" --config "$CFG" --phase 8 --without-required-check; rc=$?
assert_eq "verify --phase 8 passes on the complete setup (its checks are off here, so the flag says so)" "$rc" 0
run bash "$B/verify.sh" --config "$CFG" --phase 11; rc=$?
assert_eq "verify --phase 11 is refused" "$rc" 1
run bash "$B/verify.sh" --config "$CFG" --phase 3; rc=$?
assert_eq "verify --phase 3 passes on the complete setup (no required checks expected yet)" "$rc" 0
assert "and skips what comes later" grep -q '^SKIP: loop-writer App (phase 7, after phase 3)' "$OUT"
assert "including the routine-fire values" grep -q '^SKIP: acme-example/skills-evals environment routine-fire secret EVAL_ROUTINE_FIRE_BEARER (phase 7, after phase 3)' "$OUT"
run bash "$B/verify.sh" --config "$CFG" --phase 4; rc=$?
assert_eq "verify --phase 4 expects the required checks, which are off here" "$rc" 1
assert "and names the ruleset" grep -q '^FAIL: acme-example/agent-skills ruleset default-branch matches its file' "$OUT"
run bash "$B/verify.sh" --config "$CFG" --phase 4 --without-required-check; rc=$?
assert_eq "an explicit --without-required-check wins over the phase" "$rc" 0
# A fresh account at the end of phase 1: repos, rulesets, environments and the
# privacy secret, and nothing else.
P1="$T/phase1"; mkdir -p "$P1"
(
  export GH_STUB_STATE="$P1/state.json" GH_LOG="$P1/gh.log"; touch "$GH_LOG"
  new_config "$P1/fleet.env" free
  bash "$B/create-repos.sh" --config "$P1/fleet.env" >/dev/null 2>&1 || exit 31
  bash "$B/apply-rulesets.sh" --config "$P1/fleet.env" --all >/dev/null 2>&1 || exit 32
  bash "$B/create-environments.sh" --config "$P1/fleet.env" >/dev/null 2>&1 || exit 33
  bash "$B/set-secrets.sh" --config "$P1/fleet.env" --only privacy --denylist "$T/private/denylist" </dev/null >/dev/null 2>&1 || exit 34
  bash "$B/verify.sh" --config "$P1/fleet.env" --phase 1 > "$P1/v1.out" 2>&1 || exit 35
  bash "$B/verify.sh" --config "$P1/fleet.env" > "$P1/vall.out" 2>&1 && exit 36
  bash "$B/verify.sh" --config "$P1/fleet.env" --phase 2 > "$P1/v2.out" 2>&1 && exit 37
  exit 0
); rc=$?
assert_eq "after phase 1: verify --phase 1 passes, a full verify and --phase 2 fail" "$rc" 0
assert "phase 1 checked the privacy secret" grep -q '^PASS: acme-example/agent-skills environment privacy has secret PRIVACY_DENYLIST' "$P1/v1.out"
assert "phase 1 skips the guidance-sync App" grep -q '^SKIP: guidance-sync App (phase 2, after phase 1)' "$P1/v1.out"
assert "phase 2 asks for the guidance-sync App" grep -q '^FAIL: GUIDANCE_SYNC_APP_ID is in fleet.env' "$P1/v2.out"
assert_not "phase 2 does not ask for loop-writer yet" grep -q 'FAIL: LOOP_WRITER_APP_ID' "$P1/v2.out"
# The optional roster-writer App needs its optional environment first: the
# guide's order is create-environments.sh --with-optional, then create-apps.sh.
RW="$T/roster"; mkdir -p "$RW"
(
  export GH_STUB_STATE="$RW/state.json" GH_LOG="$RW/gh.log"; touch "$GH_LOG"
  new_config "$RW/fleet.env" free
  bash "$B/create-repos.sh" --config "$RW/fleet.env" >/dev/null 2>&1 || exit 41
  bash "$B/create-environments.sh" --config "$RW/fleet.env" >/dev/null 2>&1 || exit 42
  printf 'https://example.com/?code=code9\n' | bash "$B/create-apps.sh" --config "$RW/fleet.env" --out "$RW/man" --with-optional roster-writer > "$RW/early.out" 2>&1 && exit 43
  bash "$B/create-environments.sh" --config "$RW/fleet.env" --with-optional > "$RW/env.out" 2>&1 || exit 44
  jq -e '.envs["acme-example/skills-evals"]["roster-writer"]' "$GH_STUB_STATE" >/dev/null || exit 45
  printf 'https://example.com/?code=code9\n' | bash "$B/create-apps.sh" --config "$RW/fleet.env" --out "$RW/man" --with-optional roster-writer > "$RW/late.out" 2>&1 || exit 46
  grep -qx 'ROSTER_WRITER_APP_ID=[0-9]*' "$RW/fleet.env" || exit 47
); rc=$?
assert_eq "roster-writer: refused before its environment, created after create-environments --with-optional" "$rc" 0
assert "roster-writer before its environment says to run create-environments.sh first" grep -q 'run create-environments.sh first' "$RW/early.out"
assert_eq "verify makes no write calls" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 0
assert_not "verify reads no secret value" grep -qF -- "$BEARER" "$OUT"
jq '.repos["acme-example/agent-skills"].allow_squash_merge = true' "$GH_STUB_STATE" > "$T/s" && mv "$T/s" "$GH_STUB_STATE"
run bash "$B/verify.sh" --config "$CFG"; rc=$?
assert_eq "verify fails when a setting drifts" "$rc" 1
assert "verify names the failed item" grep -q '^FAIL: acme-example/agent-skills merge settings' "$OUT"
jq '.repos["acme-example/agent-skills"].allow_squash_merge = false | .repos["acme-example/skills-evals"].labels |= map(select(.name != "vendor-release"))' "$GH_STUB_STATE" > "$T/s" && mv "$T/s" "$GH_STUB_STATE"
run bash "$B/verify.sh" --config "$CFG"; rc=$?
assert_eq "verify fails when a routine's label is missing" "$rc" 1
assert "verify names the missing label" grep -q '^FAIL: acme-example/skills-evals has label vendor-release' "$OUT"
assert "verify checks agent-check too" grep -q '^PASS: acme-example/skills-evals has label agent-check' "$OUT"
GH_STUB_DRIFT=1 run bash "$B/verify.sh" --config "$CFG"; rc=$?
assert "verify fails on a drifted ruleset" grep -q '^FAIL: acme-example/agent-guidance ruleset default-branch matches its file' "$OUT"
assert "verify compares the repository variables" grep -q '^PASS: acme-example/skills-evals variable REGISTRY_REPO matches fleet.env' "$OUT"
jq '.values["acme-example/skills-evals||variable"].REGISTRY_REPO = "someone-else/agent-skills"' "$GH_STUB_STATE" > "$T/s" && mv "$T/s" "$GH_STUB_STATE"
run bash "$B/verify.sh" --config "$CFG"; rc=$?
assert_eq "verify fails when a repository variable drifts" "$rc" 1
assert "verify names the variable" grep -q '^FAIL: acme-example/skills-evals variable REGISTRY_REPO is missing or differs' "$OUT"
assert_not "verify prints neither value" grep -q 'someone-else' "$OUT"
run bash "$B/create-environments.sh" --config "$CFG"; rc=$?
assert_eq "create-environments puts the variable back" "$(RV skills-evals REGISTRY_REPO)" acme-example/agent-skills
GH_STUB_ENV_LIST_STATUS=403 run bash "$B/verify.sh" --config "$CFG"; rc=$?
assert_eq "verify fails when environment names are unreadable" "$rc" 1
assert "verify names the list's own HTTP status" grep -qE '^FAIL: acme-example/[a-z-]+ environment [a-z-]+ (secret|variable)s readable \(HTTP 403\)' "$OUT"

# ======================================================== copy-starter.sh
mkremote() { # owner/repo, with a main branch holding README.md
  local d="$T/remotes/$1.git" w="$T/seed-${1//\//-}"
  git init -q --bare "$d"
  git init -q "$w"; printf '# %s\n' "${1#*/}" > "$w/README.md"
  git -C "$w" add README.md; git -C "$w" commit -qm "First commit"; git -C "$w" push -q "$d" HEAD:refs/heads/main
  git -C "$d" symbolic-ref HEAD refs/heads/main
}
mkremote acme-example/agent-guidance
S="$T/starter/agent-guidance"; mkdir -p "$S/scripts" "$S/docs"
printf '#!/usr/bin/env bash\nowner="<you>"\n' > "$S/scripts/install-agent-hooks.sh"; chmod +x "$S/scripts/install-agent-hooks.sh"
printf 'Read docs in `<you>/agent-guidance`, by <your name>.\n' > "$S/docs/readme.md"
printf '{ "bypass_actors": [ { "actor_id": 111111 }, { "actor_id": 222222 } ] }\n' > "$S/docs/ruleset.json"
printf '<you>\0\001binary' > "$S/docs/logo.bin"
printf '# agent-guidance\n' > "$S/README.md"          # identical to the remote's
main_before=$(git -C "$T/remotes/acme-example/agent-guidance.git" rev-parse main)
n=$(log_lines "$GH_LOG")
run bash "$B/copy-starter.sh" --config "$CFG" --dry-run "$S" acme-example/agent-guidance; rc=$?
assert_eq "copy-starter dry run exits 0" "$rc" 0
assert_not "copy-starter dry run pushes no branch" git -C "$T/remotes/acme-example/agent-guidance.git" rev-parse --verify -q refs/heads/starter/agent-guidance
assert_eq "copy-starter dry run makes no write calls" "$(tail -n +"$((n + 1))" "$GH_LOG" > "$T/delta"; writes "$T/delta")" 0
run bash "$B/copy-starter.sh" --config "$CFG" "$S" acme-example/agent-guidance; rc=$?
assert_eq "copy-starter exits 0" "$rc" 0
RG=(git -C "$T/remotes/acme-example/agent-guidance.git")
assert "the starter branch is on the remote" "${RG[@]}" rev-parse --verify -q refs/heads/starter/agent-guidance
assert_eq "the default branch is untouched" "$("${RG[@]}" rev-parse main)" "$main_before"
assert_eq "<you> replaced in a script" "$("${RG[@]}" show starter/agent-guidance:scripts/install-agent-hooks.sh | sed -n 2p)" 'owner="acme-example"'
assert_eq "<you> and <your name> replaced in a doc" "$("${RG[@]}" show starter/agent-guidance:docs/readme.md)" 'Read docs in `acme-example/agent-guidance`, by Acme Example.'
assert_eq "App ID placeholders replaced in JSON" "$("${RG[@]}" show starter/agent-guidance:docs/ruleset.json | jq -c '[.bypass_actors[].actor_id]')" "[900002,900001]"
assert "a binary file is copied byte for byte" cmp -s <("${RG[@]}" show starter/agent-guidance:docs/logo.bin) "$S/docs/logo.bin"
assert_eq "the exec bit is kept" "$("${RG[@]}" ls-tree starter/agent-guidance scripts/install-agent-hooks.sh | cut -c1-6)" 100755
assert_eq "the starter folder itself is not modified" "$(sed -n 2p "$S/scripts/install-agent-hooks.sh")" 'owner="<you>"'
assert "the push is verified on the remote branch" has "$OUT" "verified on the remote branch"
assert_eq "one pull request opened" "$(grep -c '"pr", "create"' "$GH_LOG")" 1
assert "the pull request targets main from the starter branch" grep -qF '"--base", "main", "--head", "starter/agent-guidance"' "$GH_LOG"
branch_before=$("${RG[@]}" rev-parse starter/agent-guidance)
run bash "$B/copy-starter.sh" --config "$CFG" "$S" acme-example/agent-guidance; rc=$?
assert_eq "copy-starter second run exits 0" "$rc" 0
assert_eq "copy-starter second run adds no commit" "$("${RG[@]}" rev-parse starter/agent-guidance)" "$branch_before"
assert_eq "copy-starter second run opens no second pull request" "$(grep -c '"pr", "create"' "$GH_LOG")" 1
assert "copy-starter second run says nothing to change" has "$OUT" "Nothing to change"
mkremote acme-example/skills-evals
S2="$T/starter/skills-evals"; mkdir -p "$S2"; printf '# a different README\n' > "$S2/README.md"; printf 'new\n' > "$S2/new.txt"
run bash "$B/copy-starter.sh" --config "$CFG" "$S2" acme-example/skills-evals; rc=$?
assert_eq "copy-starter refuses to overwrite a different file" "$rc" 1
assert "the refused path is named" has "$ERR" "REFUSED: README.md exists on main"
assert_not "nothing is pushed after a refusal" git -C "$T/remotes/acme-example/skills-evals.git" rev-parse --verify -q refs/heads/starter/skills-evals
run bash "$B/copy-starter.sh" --config "$CFG" --force "$S2" acme-example/skills-evals; rc=$?
assert_eq "--force overwrites" "$rc" 0
assert_eq "--force puts the starter file on the branch" "$(git -C "$T/remotes/acme-example/skills-evals.git" show starter/skills-evals:README.md)" "# a different README"
ln -s /etc/passwd "$S2/link"
run bash "$B/copy-starter.sh" --config "$CFG" "$S2" acme-example/skills-evals; rc=$?
assert_eq "a symlink in the starter folder is refused" "$rc" 1
rm "$S2/link"
run bash "$B/copy-starter.sh" --config "$CFG" "$S2" other-owner/skills-evals; rc=$?
assert_eq "a repo outside OWNER is refused" "$rc" 1
assert_eq "copy-starter leaves nothing in TMPDIR" "$(find "$TMPDIR" -mindepth 1 | wc -l | tr -d ' ')" 0

# <you> in a PATH: the kit's own agent-skills folder has plugins/<you>-coding-anywhere/.
mkremote acme-example/agent-skills
KIT_AS="$B/../starter/agent-skills"
run bash "$B/copy-starter.sh" --config "$CFG" "$KIT_AS" acme-example/agent-skills; rc=$?
assert_eq "copy-starter of the kit's agent-skills exits 0" "$rc" 0
AG=(git -C "$T/remotes/acme-example/agent-skills.git")
assert_eq "no path on the branch still holds <you>" "$("${AG[@]}" ls-tree -r --name-only starter/agent-skills | grep -c '<you>')" 0
assert "<you> in a folder name becomes OWNER" "${AG[@]}" cat-file -e "starter/agent-skills:plugins/acme-example-coding-anywhere/skills/workflow-path-audit/SKILL.md"
assert_eq "and the file inside is filled too" \
  "$("${AG[@]}" show starter/agent-skills:plugins/acme-example-coding-anywhere/.claude-plugin/plugin.json | jq -r .name)" acme-example-coding-anywhere
assert_eq "the marketplace source names the renamed folder" \
  "$("${AG[@]}" show starter/agent-skills:.claude-plugin/marketplace.json | jq -r '.plugins[0].source')" ./plugins/acme-example-coding-anywhere
assert "the rename is reported" has "$OUT" "renamed: plugins/<you>-coding-anywhere -> plugins/acme-example-coding-anywhere"
assert "the kit's folder keeps its <you> path" test -d "$KIT_AS/plugins/<you>-coding-anywhere"
assert_not "and gains no renamed copy" test -e "$KIT_AS/plugins/acme-example-coding-anywhere"
S4="$T/starter/clash"; mkdir -p "$S4/<you>-x" "$S4/acme-example-x"; printf 'a\n' > "$S4/<you>-x/a"; printf 'b\n' > "$S4/acme-example-x/b"
run bash "$B/copy-starter.sh" --config "$CFG" "$S4" acme-example/agent-skills; rc=$?
assert_eq "a rename that would replace an existing path is refused" "$rc" 1
assert "and names both paths" has "$ERR" "renaming <you>-x would replace acme-example-x"
# The README create-repos.sh wrote may be replaced; an edited one may not.
seed_readme_remote() { # owner/repo readme-file
  local d="$T/remotes/$1.git" w="$T/seed2-${1//\//-}"
  git init -q --bare "$d"; git init -q "$w"; cp "$2" "$w/README.md"
  git -C "$w" add README.md; git -C "$w" commit -qm "Add a README so the repo has a first commit"; git -C "$w" push -q "$d" HEAD:refs/heads/main
  git -C "$d" symbolic-ref HEAD refs/heads/main
}
jq -j '.repos["acme-example/repo-settings"].readme' "$GH_STUB_STATE" > "$T/gen-readme.md"
seed_readme_remote acme-example/repo-settings "$T/gen-readme.md"
S5="$T/starter/repo-settings"; mkdir -p "$S5"; printf '# repo-settings, from the starter\n' > "$S5/README.md"
run bash "$B/copy-starter.sh" --config "$CFG" "$S5" acme-example/repo-settings; rc=$?
assert_eq "copy-starter replaces the generated README without --force" "$rc" 0
assert "and says so" has "$OUT" "replacing: README.md (still the README create-repos.sh wrote)"
assert_eq "the starter README is on the branch" \
  "$(git -C "$T/remotes/acme-example/repo-settings.git" show starter/repo-settings:README.md)" "# repo-settings, from the starter"
{ cat "$T/gen-readme.md"; printf 'My own line.\n'; } > "$T/edited-readme.md"
seed_readme_remote acme-example/agent-skills-private "$T/edited-readme.md"
S6="$T/starter/agent-skills-private"; mkdir -p "$S6"; printf '# private, from the starter\n' > "$S6/README.md"
run bash "$B/copy-starter.sh" --config "$CFG" "$S6" acme-example/agent-skills-private; rc=$?
assert_eq "an edited generated README still needs --force" "$rc" 1
assert "and is named" has "$ERR" "REFUSED: README.md exists on main"
BADCFG="$T/bad-owner.env"; sed 's|^OWNER=.*|OWNER=acme/../x|' "$CFG" > "$BADCFG"
run bash "$B/copy-starter.sh" --config "$BADCFG" "$KIT_AS" acme-example/agent-skills; rc=$?
assert_eq "an OWNER that is not a plain login is refused" "$rc" 1
assert_eq "copy-starter still leaves nothing in TMPDIR" "$(find "$TMPDIR" -mindepth 1 | wc -l | tr -d ' ')" 0
# Caches and local junk never reach a repo: a .pyc embeds local absolute paths.
mkremote acme-example/junk-test
S7="$T/starter/junk-test"; mkdir -p "$S7/scripts/__pycache__" "$S7/.pytest_cache/v" "$S7/node_modules/pkg" "$S7/tests"
printf 'print(1)\n' > "$S7/scripts/a.py"
printf 'x' > "$S7/scripts/__pycache__/a.cpython-311.pyc"; printf 'x' > "$S7/tests/b.pyc"
printf 'x' > "$S7/.pytest_cache/v/cache"; printf 'x' > "$S7/node_modules/pkg/index.js"; printf 'x' > "$S7/.DS_Store"
ln -s ../pkg "$S7/node_modules/pkg/self"     # a symlink inside node_modules is pruned, not refused
run bash "$B/copy-starter.sh" --config "$CFG" "$S7" acme-example/junk-test; rc=$?
assert_eq "copy-starter with caches in the folder exits 0" "$rc" 0
JT=$(git -C "$T/remotes/acme-example/junk-test.git" ls-tree -r --name-only starter/junk-test | grep -vx README.md)
assert_eq "only the real file is copied" "$JT" "scripts/a.py"
assert "and the skip is reported" has "$OUT" "skipped 5 cache or junk path(s)"
for r in agent-guidance agent-skills agent-skills-private skills-evals repo-settings; do
  assert "starter/$r ships a .gitignore naming every path copy-starter.sh skips" bash -c '
    for p in "__pycache__/" "*.pyc" ".pytest_cache/" "node_modules/" ".DS_Store"; do grep -qxF -- "$p" "$1" || exit 1; done' _ "$B/../starter/$r/.gitignore"
done
assert "every test runner sets PYTHONDONTWRITEBYTECODE=1" bash -c '
  for f in "$1"/starter/*/tests/run.sh "$1"/starter/run-all-tests.sh "$1"/run-all-tests.sh "$1"/bootstrap/tests/test-bootstrap.sh; do
    grep -q "^export PYTHONDONTWRITEBYTECODE=1" "$f" || { echo "$f"; exit 1; }; done' _ "$B/.."
# OWNER_NAME defaults to OWNER: <your name> lands in public repos.
NONAME="$T/no-name.env"; sed '/^OWNER_NAME=/d' "$CFG" > "$NONAME"
mkremote acme-example/name-test
S8="$T/starter/name-test"; mkdir -p "$S8"; printf 'by <your name>\n' > "$S8/NOTICE"
run bash "$B/copy-starter.sh" --config "$NONAME" "$S8" acme-example/name-test; rc=$?
assert_eq "without OWNER_NAME, <your name> becomes OWNER" "$(git -C "$T/remotes/acme-example/name-test.git" show starter/name-test:NOTICE)" "by acme-example"
assert_not "and no <your name> warning" has "$ERR" "still has <your name>"
# A mixed-case owner: plugin and marketplace NAMES are lowercased (as
# install-machine.sh installs them); an owner in owner/repo keeps its case.
MIXED="$T/mixed.env"; sed 's/^OWNER=.*/OWNER=Acme-Example/' "$CFG" > "$MIXED"
mkremote Acme-Example/agent-skills
S9="$T/starter/agent-skills-mixed"; cp -R "$KIT_AS" "$S9"
printf 'Install from `<you>/agent-skills`.\n' > "$S9/OWNER-NOTE.md"
run bash "$B/copy-starter.sh" --config "$MIXED" "$S9" Acme-Example/agent-skills; rc=$?
assert_eq "copy-starter with a mixed-case owner exits 0" "$rc" 0
AM=(git -C "$T/remotes/Acme-Example/agent-skills.git")
AMB=starter/agent-skills-mixed
assert "the plugin folder is lowercase" "${AM[@]}" cat-file -e "$AMB:plugins/acme-example-coding-anywhere/.claude-plugin/plugin.json"
assert_eq "no path keeps the mixed case" "$("${AM[@]}" ls-tree -r --name-only "$AMB" | grep -c 'Acme-Example')" 0
assert_eq "the plugin name is lowercase" \
  "$("${AM[@]}" show "$AMB:plugins/acme-example-coding-anywhere/.claude-plugin/plugin.json" | jq -r .name)" acme-example-coding-anywhere
assert_eq "the marketplace and its plugin are lowercase, matching install-machine.sh" \
  "$("${AM[@]}" show "$AMB:.claude-plugin/marketplace.json" | jq -r '[.name, .plugins[0].name, .plugins[0].source] | join(" ")')" \
  "acme-example-skills acme-example-coding-anywhere ./plugins/acme-example-coding-anywhere"
assert_eq "an owner/repo keeps the owner's case" "$("${AM[@]}" show "$AMB:OWNER-NOTE.md")" 'Install from `Acme-Example/agent-skills`.'
assert "install-machine.sh lowercases the same way" grep -qF 'OL=${O,,}' "$B/install-machine.sh"

# ======================================================== install-machine.sh
# Before the agent-skills pull request is merged, the marketplace add fails:
# the git hooks are installed anyway, and the run says to merge, then rerun.
HE="$T/home-early"; mkdir -p "$HE/.claude"
( export HOME="$HE" GIT_CONFIG_GLOBAL="$HE/.gitconfig" CLAUDE_STUB_NO_MARKET=1 CLAUDE_STUB_STATE="$T/claude-early.state" CLAUDE_LOG="$T/claude-early.log"
  bash "$B/install-machine.sh" --config "$CFG" --machine-dir "$B/tests/fixtures/machine" > "$T/im-early.out" 2>&1 ); rc=$?
assert_eq "install-machine before the registry is merged still exits 0" "$rc" 0
assert "the git hooks are installed first" grep -q 'https://github.com/acme-example/\*' "$HE/.config/git/agent-hooks.inc"
hooks_at=$(grep -n 'registered the agent-checks git hooks' "$T/im-early.out" | head -n 1 | cut -d: -f1)
market_at=$(grep -n 'could not add the marketplace' "$T/im-early.out" | head -n 1 | cut -d: -f1)
assert "the git hooks step runs before the marketplace step" test "${hooks_at:-999999}" -lt "${market_at:-0}"
assert "it says to merge the agent-skills starter pull request, then rerun" has "$T/im-early.out" "merge the agent-skills starter pull request (copy-starter.sh opened it), then rerun install-machine.sh"
assert "and that it is not finished" has "$T/im-early.out" "Not finished: rerun install-machine.sh"
assert "the marketplace add was tried" grep -qx 'plugin marketplace add acme-example/agent-skills' "$T/claude-early.log"
assert_not "and no plugin install after it failed" grep -q 'plugin install' "$T/claude-early.log"

H="$T/home"; mkdir -p "$H/.claude"
cat > "$H/.claude/settings.json" <<'EOF'
{
  "theme": "dark",
  "model": "example-model",
  "hooks": { "PreToolUse": [ { "matcher": "Bash", "hooks": [ { "type": "command", "command": "echo example" } ] } ] }
}
EOF
cp "$H/.claude/settings.json" "$T/settings.orig"
M="$B/tests/fixtures/machine"
(
  export HOME="$H" GIT_CONFIG_GLOBAL="$H/.gitconfig"
  bash "$B/install-machine.sh" --config "$CFG" --machine-dir "$M" --dry-run > "$T/im-dry.out" 2>&1 || exit 21
  cmp -s "$H/.claude/settings.json" "$T/settings.orig" || exit 22
  [ ! -e "$H/.claude/hooks/memory-home.py" ] || exit 23
  [ ! -s "$CLAUDE_LOG" ] || ! grep -q 'install\|add' "$CLAUDE_LOG" || exit 24
  bash "$B/install-machine.sh" --config "$CFG" --machine-dir "$M" > "$T/im1.out" 2>&1 || exit 25
  bash "$B/install-machine.sh" --config "$CFG" --machine-dir "$M" > "$T/im2.out" 2>&1 || exit 26
); rc=$?
assert_eq "install-machine runs (dry run, then twice)" "$rc" 0
S_J="$H/.claude/settings.json"
assert "install-machine dry run prints the merged settings" has "$T/im-dry.out" '"disableSkillShellExecution": true'
assert "install-machine keeps unrelated keys" jq -e '.theme == "dark" and .model == "example-model"' "$S_J"
assert "install-machine keeps existing hooks" jq -e '.hooks.PreToolUse[0].hooks[0].command == "echo example"' "$S_J"
assert "install-machine sets disableSkillShellExecution" jq -e '.disableSkillShellExecution == true' "$S_J"
assert "install-machine adds the memory-home Stop hook once" \
  jq -e '[.hooks.Stop[].hooks[] | select(.command == "python3 ~/.claude/hooks/memory-home.py" and .timeout == 10)] | length == 1' "$S_J"
assert "install-machine backs up settings.json first" cmp -s "$S_J.bak.1" "$T/settings.orig"
assert_not "the second run makes no second backup" test -e "$S_J.bak.2"
assert "memory-home.py is installed" cmp -s "$H/.claude/hooks/memory-home.py" "$M/memory-home.py"
assert "the marketplace is added" grep -qx 'plugin marketplace add acme-example/agent-skills' "$CLAUDE_LOG"
assert "the plugin is installed" grep -qx 'plugin install acme-example-coding-anywhere@acme-example-skills' "$CLAUDE_LOG"
assert_eq "the second run adds and installs nothing" "$(grep -c 'add\|install' "$CLAUDE_LOG")" 2
assert "the git hooks are registered for OWNER" grep -q 'https://github.com/acme-example/\*' "$H/.config/git/agent-hooks.inc"
assert "the include points at the hook file" grep -qF "$H/.config/git/agent-hooks.inc" "$H/.gitconfig"
assert "the second run says nothing to change" has "$T/im2.out" "Nothing to change"
printf '{ not json' > "$T/home/.claude/settings.json"; cp "$T/home/.claude/settings.json" "$T/bad.orig"
HOME="$H" GIT_CONFIG_GLOBAL="$H/.gitconfig" bash "$B/install-machine.sh" --config "$CFG" --machine-dir "$M" >"$OUT" 2>"$ERR"; rc=$?
assert_eq "invalid settings.json is refused" "$rc" 1
assert "invalid settings.json is left as it was" cmp -s "$T/home/.claude/settings.json" "$T/bad.orig"

# ======================================================== workflow variables and secrets
# Every vars./secrets. name the kit's workflows read is set by a script here
# (or listed as by hand in README.md), in the repo and environment the job uses.
python3 "$B/tests/check-refs.py" > "$T/refs.out" 2>&1; rc=$?
assert_eq "check-refs.py: every workflow value has a script that sets it" "$rc" 0
[ "$rc" = 0 ] || sed 's/^/    /' "$T/refs.out"
assert "check-refs.py checked something" grep -qE '^[1-9][0-9]* assertions passed, 0 failed$' "$T/refs.out"

# ======================================================== loop branch names across the kit
python3 "$B/tests/check-branch-prefixes.py" > "$T/prefixes.out" 2>&1; rc=$?
assert_eq "check-branch-prefixes.py: routine docs, gates and disarms agree" "$rc" 0
[ "$rc" = 0 ] || sed 's/^/    /' "$T/prefixes.out"
assert "check-branch-prefixes.py checked something" grep -qE '^[1-9][0-9]* assertions passed, 0 failed$' "$T/prefixes.out"

# ======================================================== the guide's hand-kept lists
python3 "$B/tests/check-guide-lists.py" "$B/.." > "$T/lists.out" 2>&1; rc=$?
assert_eq "check-guide-lists.py: pull_request_target and pin-placeholder lists cover the kit" "$rc" 0
[ "$rc" = 0 ] || sed 's/^/    /' "$T/lists.out"
assert "check-guide-lists.py checked something" grep -qE '^[1-9][0-9]* assertions passed, 0 failed$' "$T/lists.out"

# ======================================================== nothing left behind
assert_eq "no script left files in TMPDIR" "$(find "$TMPDIR" -mindepth 1 | wc -l | tr -d ' ')" 0
assert_not "no secret_looking_body anywhere in outputs" grep -rqF BODY-MUST-NOT-BE-PRINTED "$OUT" "$ERR" "$T"/im*.out

if [ "$FAILS" -gt 0 ]; then
  printf '%d of %d assertions failed\n' "$FAILS" "$((PASSES + FAILS))"
  exit 1
fi
printf '%d assertions passed\n' "$PASSES"
