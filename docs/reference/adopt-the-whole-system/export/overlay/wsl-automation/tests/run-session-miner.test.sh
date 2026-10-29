#!/usr/bin/env bash
# Behavior test for scripts/run-session-miner.sh. Everything it touches is a
# throwaway under one temporary directory: a fixture skills-evals whose
# run_session_miner.sh is a stub, a fixture private-registry clone, a fake HOME,
# and stubs for the Windows bootstrap. No network, no real transcripts.
set -euo pipefail

repository_root=$(cd "$(dirname "$0")/.." && pwd)
script="$repository_root/scripts/run-session-miner.sh"
test_root=$(mktemp -d)
trap 'rm -rf -- "$test_root"' EXIT

# The wrapper refuses CI and cloud sessions; this test is often run in CI, so
# clear those for every case except the one that sets them on purpose.
unset CI GITHUB_ACTIONS CLAUDE_CODE_REMOTE CLAUDE_CODE_REMOTE_SESSION_ID
unset PRIVACY_DENYLIST_FILE LESSONS_CLONE LESSONS_REPO_URL GUIDANCE_MANIFEST MINER_REGISTRIES
unset MINER_ROOTS WIN_HOME MINER_STATE PYTHON FILE_ISSUES SESSION_MINER_CONFIG WSL_DISTRO_NAME
export HOME="$test_root/home"
mkdir -p "$HOME"

# Stub only the Windows bootstrap, as tests/publish-usage-census.test.sh does.
mkdir -p "$test_root/bin"
export BOOTSTRAP_CALLS="$test_root/bootstrap-calls"
cat >"$test_root/bin/wslpath" <<'EOF_STUB'
#!/usr/bin/env bash
printf '%s\n' "$2"
EOF_STUB
cat >"$test_root/bin/pwsh.exe" <<'EOF_STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$BOOTSTRAP_CALLS"
exit 0
EOF_STUB
cat >"$test_root/bin/timeout" <<'EOF_STUB'
#!/usr/bin/env bash
shift 2
exec "$@"
EOF_STUB
printf '#!/usr/bin/env bash\nexit 0\n' >"$test_root/bin/python-ok"
printf '#!/usr/bin/env bash\nexit 1\n' >"$test_root/bin/python-noyaml"
chmod +x "$test_root/bin/"*
export PATH="$test_root/bin:$PATH"
export WSL_AUTOMATION_UPDATE_STAMP="$test_root/state/checkout-update.stamp"

passed=0
fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}
pass() { passed=$((passed + 1)); }
assert_eq() {
    [[ $1 == "$2" ]] || fail "$3: expected '$2', got '$1'"
    pass
}
assert_match() {
    [[ $1 =~ $2 ]] || fail "$3: '$1' does not match '$2'"
    pass
}
assert_no_match() {
    [[ ! $1 =~ $2 ]] || fail "$3: '$1' matches '$2'"
    pass
}

# A throwaway skills-evals whose miner records its arguments and environment to
# a file, prints env-controlled output and exits with an env-controlled code.
fixture="$test_root/skills-evals-src"
mkdir -p "$fixture/scripts"
cat >"$fixture/scripts/run_session_miner.sh" <<'EOF'
#!/usr/bin/env bash
{
    printf 'args=%s\n' "$*"
    printf 'denylist=%s\n' "${PRIVACY_DENYLIST_FILE:-}"
    printf 'clone=%s\n' "${LESSONS_CLONE:-}"
    printf 'roots=%s\n' "${MINER_ROOTS:-}"
    printf 'win_home=%s\n' "${WIN_HOME:-}"
    printf 'prompt=%s\n' "${GIT_TERMINAL_PROMPT:-}"
    printf 'stdin=%s\n' "$(cat)"
} >"$STUB_RECORD"
printf '%b' "${STUB_OUTPUT-session miner: 3 sessions, 1 record\n}"
printf '%b' "${STUB_STDERR:-}" >&2
exit "${STUB_EXIT:-0}"
EOF
git -C "$fixture" init --quiet
git -C "$fixture" add scripts/run_session_miner.sh
git -C "$fixture" -c user.name=Test -c user.email=test@example.com -c commit.gpgsign=false \
    commit --quiet -m 'fixture'
export SKILLS_EVALS_URL="file://$fixture"
export STUB_RECORD="$test_root/record"

# The private registry clone: only its origin is checked.
clone="$test_root/registry"
git init --quiet "$clone"
git -C "$clone" remote add origin 'https://example.com/registry.git'

denylist="$test_root/denylist.txt"
printf '%s\n' 'example-pattern' >"$denylist"
log_file="$test_root/cache/session-miner.log"
marker="$test_root/state/dry-run-ok"
export SESSION_MINER_LOG="$log_file" SESSION_MINER_MARKER="$marker" SESSION_MINER_WSL=0
export SESSION_MINER_CONFIG="$test_root/no-config.env"

line_count() {
    if [[ -f $log_file ]]; then wc -l <"$log_file" | tr -d ' '; else echo 0; fi
}
last_line() { tail -n 1 -- "$log_file"; }
record() { sed -n "s/^$1=//p" "$STUB_RECORD"; }

# Every case below skips the Windows bootstrap the way run-task.sh does, except
# the one that checks it runs.
export WSL_AUTOMATION_TASK_REEXEC="$script"
run() {  # run ENV=... -- ARGS: sets rc and out
    rc=0
    out=$(env "$@" 2>&1) || rc=$?
}
base=(PRIVACY_DENYLIST_FILE="$denylist" LESSONS_CLONE="$clone" PYTHON="$test_root/bin/python-ok")

# 1. A scheduled run (no arguments) refuses until a dry run has succeeded.
rm -f -- "$STUB_RECORD"
run "${base[@]}" bash "$script"
assert_eq "$rc" 3 'scheduled run before any dry run exits 3'
assert_eq "$out" 'session miner: not set up yet; run it by hand with --dry-run first' 'not-set-up message'
assert_eq "$([[ -f $STUB_RECORD ]] && echo ran || echo idle)" idle 'the miner is not run before a dry run'
assert_match "$(last_line)" '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z exit=3 session miner: not set up yet' 'refusal is logged'

# 2. The dry run reaches the miner with --dry-run, writes the marker, logs the summary.
run "${base[@]}" bash "$script" --dry-run
assert_eq "$rc" 0 'dry run exits 0'
assert_eq "$(record args)" '--dry-run' '--dry-run reaches the miner'
assert_eq "$(record prompt)" 0 'git credential prompts are off'
assert_eq "$(record stdin)" '' 'the miner gets no stdin'
assert_eq "$([[ -f $marker ]] && echo yes || echo no)" yes 'a successful dry run writes the marker'
assert_match "$out" '^session miner: 3 sessions, 1 record \(no state file yet: every session is read\)$' 'summary and note on stdout'
assert_match "$(last_line)" ' exit=0 session miner: 3 sessions, 1 record \(no state file yet' 'summary is logged'

# 3. After the marker, a scheduled run mines with no arguments; the exit code propagates.
run "${base[@]}" STUB_EXIT=4 bash "$script"
assert_eq "$rc" 4 'scheduled run propagates the miner exit code'
assert_eq "$(record args)" '' 'a scheduled run passes no arguments'
assert_match "$(last_line)" ' exit=4 session miner: ' 'the failure is logged'

# 4. A failed dry run leaves no marker.
rm -f -- "$marker"
run "${base[@]}" STUB_EXIT=1 bash "$script" --dry-run
assert_eq "$rc" 1 'failed dry run exits 1'
assert_eq "$([[ -f $marker ]] && echo yes || echo no)" no 'a failed dry run writes no marker'
run "${base[@]}" bash "$script" --dry-run
assert_eq "$rc" 0 'second dry run succeeds'

# 5. Output other than the miner's summary lines is counted, never copied.
run "${base[@]}" STUB_OUTPUT='session miner: 2 sessions\nTraceback /home/someone/secret.jsonl\n' \
    STUB_STDERR='File "/home/someone/x.py", line 3\n' bash "$script"
assert_eq "$rc" 0 'noisy run exits 0'
assert_match "$(last_line)" 'session miner: 2 sessions \[2 other output line\(s\) withheld\]' 'other lines are counted'
assert_no_match "$(last_line)" 'someone|secret|x\.py' 'no path from other output reaches the log'
assert_no_match "$out" 'someone|secret' 'no path from other output reaches stdout'
run "${base[@]}" STUB_OUTPUT='session miner: a\nsession miner: b\n' bash "$script"
assert_match "$(last_line)" 'session miner: a \| b \(' 'several summary lines join with |'
run "${base[@]}" STUB_OUTPUT='' bash "$script"
assert_match "$(last_line)" 'session miner: no summary' 'no summary is said so'

# 6. Preflight refusals, each with no path in the message.
run PRIVACY_DENYLIST_FILE="$test_root/missing.txt" LESSONS_CLONE="$clone" PYTHON="$test_root/bin/python-ok" bash "$script" --dry-run
assert_eq "$rc" 2 'missing denylist exits 2'
assert_eq "$out" 'session miner: no readable privacy denylist (PRIVACY_DENYLIST_FILE); nothing mined' 'missing denylist message'
run PRIVACY_DENYLIST_FILE="$denylist" LESSONS_CLONE="$test_root/not-a-clone" PYTHON="$test_root/bin/python-ok" bash "$script" --dry-run
assert_eq "$rc" 1 'missing registry clone exits 1'
assert_eq "$out" 'session miner: no private registry clone with an origin at LESSONS_CLONE' 'missing clone message'
run PRIVACY_DENYLIST_FILE="$denylist" LESSONS_CLONE="$test_root/not-a-clone" LESSONS_REPO_URL='https://example.com/r.git' \
    PYTHON="$test_root/bin/python-ok" bash "$script" --dry-run
assert_eq "$rc" 0 'LESSONS_REPO_URL stands in for the clone origin'
run PRIVACY_DENYLIST_FILE="$denylist" LESSONS_CLONE="$clone" PYTHON="$test_root/bin/python-noyaml" bash "$script" --dry-run
assert_eq "$rc" 1 'missing PyYAML exits 1'
assert_eq "$out" 'session miner: python3 with PyYAML is required' 'PyYAML message'
run "${base[@]}" bash "$script" --file-issues
assert_eq "$rc" 2 '--file-issues without FILE_ISSUES=on exits 2'
assert_match "$out" 'stays off until a sample of records is rated' '--file-issues refusal message'
run "${base[@]}" FILE_ISSUES=on bash "$script" --file-issues
assert_eq "$rc" 0 '--file-issues with FILE_ISSUES=on runs'
assert_eq "$(record args)" '--file-issues' '--file-issues reaches the miner'
run "${base[@]}" bash "$script" --bogus
assert_eq "$rc" 2 'unknown argument exits 2'
run "${base[@]}" SKILLS_EVALS_URL="file://$test_root/does-not-exist" bash "$script" --dry-run
assert_eq "$rc" 1 'clone failure exits 1'
assert_match "$out" '^session miner: could not clone skills-evals( \(|$)' 'clone failure message'

# 7. CI and cloud sessions are refused before anything else.
for var in CI GITHUB_ACTIONS CLAUDE_CODE_REMOTE CLAUDE_CODE_REMOTE_SESSION_ID; do
    rm -f -- "$STUB_RECORD"
    run "${base[@]}" "$var=1" bash "$script" --dry-run
    assert_eq "$rc" 2 "$var refuses with exit 2"
    assert_eq "$out" 'session miner: refusing to run in CI or a cloud session' "$var refusal message"
    assert_eq "$([[ -f $STUB_RECORD ]] && echo ran || echo idle)" idle "$var: the miner is not run"
done

# 8. Defaults under HOME, then a config file read as data.
mkdir -p "$HOME/.config" "$HOME/repos"
cp -- "$denylist" "$HOME/.config/privacy-denylist.txt"
git init --quiet "$HOME/repos/acsi-agentskills-private"
git -C "$HOME/repos/acsi-agentskills-private" remote add origin 'https://example.com/registry.git'
run PYTHON="$test_root/bin/python-ok" bash "$script" --dry-run
assert_eq "$rc" 0 'defaults run'
assert_eq "$(record denylist)" "$HOME/.config/privacy-denylist.txt" 'default denylist path'
assert_eq "$(record clone)" "$HOME/repos/acsi-agentskills-private" 'default registry clone'

config="$test_root/session-miner.env"
cat >"$config" <<EOF
# comment line

PRIVACY_DENYLIST_FILE=$denylist
LESSONS_CLONE=~/repos/acsi-agentskills-private
PYTHON=$test_root/bin/python-ok
WIN_HOME=~/win
EOF
run SESSION_MINER_CONFIG="$config" bash "$script" --dry-run
assert_eq "$rc" 0 'config file run'
assert_eq "$(record denylist)" "$denylist" 'config sets the denylist'
assert_eq "$(record clone)" "$HOME/repos/acsi-agentskills-private" 'config expands a leading ~/'
assert_eq "$(record win_home)" "$HOME/win" 'config sets WIN_HOME'
run SESSION_MINER_CONFIG="$config" WIN_HOME=/mnt/c/Users/example bash "$script" --dry-run
assert_eq "$(record win_home)" /mnt/c/Users/example 'the environment wins over the config file'

printf 'PATH=/evil\n' >>"$config"
rm -f -- "$STUB_RECORD"
run SESSION_MINER_CONFIG="$config" bash "$script" --dry-run
assert_eq "$rc" 2 'a key outside the allowlist exits 2'
assert_eq "$out" 'session miner: config line 7 is not an allowed KEY=value' 'bad config line is named by number'
assert_no_match "$out" "$test_root" 'the config path is not printed'
# A literal command substitution: the file must never run it.
# shellcheck disable=SC2016
printf 'PRIVACY_DENYLIST_FILE=%s\n$(touch %s)\n' "$denylist" "$test_root/pwned" >"$config"
run SESSION_MINER_CONFIG="$config" bash "$script" --dry-run
assert_eq "$rc" 2 'a non KEY=value line exits 2'
assert_eq "$([[ -e $test_root/pwned ]] && echo ran || echo inert)" inert 'the config file is never executed'

# 9. In WSL without WIN_HOME the wrapper reads only the WSL transcripts and says so.
run "${base[@]}" SESSION_MINER_WSL=1 bash "$script" --dry-run
assert_eq "$(record roots)" "wsl=$HOME/.claude/projects" 'WSL without WIN_HOME limits the roots'
assert_match "$(last_line)" 'WIN_HOME unset: Windows transcripts skipped' 'the skipped Windows roots are logged'
run "${base[@]}" SESSION_MINER_WSL=1 WIN_HOME=/mnt/c/Users/example bash "$script" --dry-run
assert_eq "$(record roots)" '' 'WSL with WIN_HOME leaves the roots to the miner'
assert_no_match "$(last_line)" 'WIN_HOME unset' 'no note when WIN_HOME is set'
mkdir -p "$HOME/.local/state/session-miner"
printf '{}\n' >"$HOME/.local/state/session-miner/state.json"
run "${base[@]}" bash "$script" --dry-run
assert_match "$(last_line)" ' exit=0 session miner: 3 sessions, 1 record$' 'no notes when a state file exists'

# 10. Without the re-exec guard, the shared Windows bootstrap runs once per run.
rm -f -- "$BOOTSTRAP_CALLS"
run "${base[@]}" WSL_AUTOMATION_TASK_REEXEC= bash "$script" --dry-run
assert_eq "$rc" 0 'bootstrapped run exits 0'
assert_eq "$(wc -l <"$BOOTSTRAP_CALLS" | tr -d ' ')" 1 'bootstrap runs once'
assert_match "$(cat "$BOOTSTRAP_CALLS")" 'update-task-checkout.ps1 -UpdateOnly$' 'bootstrap uses the shared updater'

# 11. Runs append one line each.
before=$(line_count)
run "${base[@]}" bash "$script" --dry-run
assert_eq "$(line_count)" "$((before + 1))" 'one log line per run'

printf 'PASS: %s assertions\n' "$passed"
