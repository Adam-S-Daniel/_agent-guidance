#!/usr/bin/env bash
# Behavior test for the Linux and macOS scheduling: scripts/unix/run-task.sh
# (overlap lock, time limit, checkout update, log line) and
# scripts/unix/install-schedule.sh (systemd units, launchd plists, the crontab
# block). Everything runs in fixture checkouts under one temporary directory,
# with stub systemctl, loginctl, launchctl and crontab; nothing is scheduled and
# no network is used.
set -euo pipefail

repository_root=$(cd "$(dirname "$0")/.." && pwd)
runner_src="$repository_root/scripts/unix/run-task.sh"
installer_src="$repository_root/scripts/unix/install-schedule.sh"
test_root=$(mktemp -d)
trap 'rm -rf -- "$test_root"' EXIT

unset CI GITHUB_ACTIONS CLAUDE_CODE_REMOTE CLAUDE_CODE_REMOTE_SESSION_ID
unset RUN_TASK_REEXEC RUN_TASK_LIMIT RUN_TASK_NOW WSL_AUTOMATION_TASK_REEXEC XDG_CONFIG_HOME XDG_STATE_HOME
export HOME="$test_root/home" GIT_CEILING_DIRECTORIES="$test_root"
mkdir -p "$HOME"
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$test_root/gitconfig"
git config --global user.name Test
git config --global user.email test@example.com
git config --global commit.gpgsign false
git config --global init.defaultBranch main

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
assert_line() {  # assert_line TEXT LINE MESSAGE: TEXT has LINE as a whole line
    grep -qxF -- "$2" <<<"$1" || fail "$3: no line '$2' in '$1'"
    pass
}

# A timeout stub that records its arguments, can fake a timed-out job, and
# otherwise runs the command (dropping --kill-after=... and the limit).
mkdir -p "$test_root/bin"
export TIMEOUT_CALLS="$test_root/timeout-calls"
cat >"$test_root/bin/timeout" <<'EOF_STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$TIMEOUT_CALLS"
if [[ -n ${STUB_TIMEOUT_EXIT:-} && $* == *'/scripts/publish-'* ]]; then exit "$STUB_TIMEOUT_EXIT"; fi
shift 2
exec "$@"
EOF_STUB
chmod +x "$test_root/bin/timeout"
export PATH="$test_root/bin:$PATH"

# ---------------------------------------------------------------------------
# run-task.sh
# ---------------------------------------------------------------------------

# A bare origin and a working clone holding run-task.sh and stub job entries.
# Each entry records how it was started and exits with STUB_EXIT.
origin="$test_root/origin.git"
upstream="$test_root/upstream"
mkdir -p "$upstream/scripts/unix"
cp -- "$runner_src" "$upstream/scripts/unix/run-task.sh"
for entry in publish-usage-census.sh publish-waiting-feed.sh run-session-miner.sh; do
    cat >"$upstream/scripts/$entry" <<'EOF'
#!/usr/bin/env bash
{
    printf 'entry=%s\n' "$(basename -- "$0")"
    printf 'version=v1\n'
    printf 'args=%s\n' "$*"
    printf 'reexec=%s\n' "${WSL_AUTOMATION_TASK_REEXEC:-}"
    printf 'self=%s\n' "$(cd -- "$(dirname -- "$0")" && pwd)/$(basename -- "$0")"
    printf 'path=%s\n' "$PATH"
} >"$JOB_RECORD"
exit "${STUB_EXIT:-0}"
EOF
done
git -C "$upstream" init --quiet
git -C "$upstream" add -A
git -C "$upstream" commit --quiet -m fixture
git clone --quiet --bare "$upstream" "$origin"
git -C "$upstream" remote add origin "$origin"
checkout="$test_root/checkout"
git clone --quiet "$origin" "$checkout"
runner="$checkout/scripts/unix/run-task.sh"

export JOB_RECORD="$test_root/job-record"
export RUN_TASK_STATE_DIR="$test_root/task-state"
runner_log="$test_root/cache/tasks.log"
export RUN_TASK_LOG="$runner_log"

record() { sed -n "s/^$1=//p" "$JOB_RECORD"; }
last_log() { tail -n 1 -- "$runner_log"; }
push_upstream() {  # push_upstream FILE SED-EXPRESSION
    sed -i "$2" "$upstream/$1"
    git -C "$upstream" commit --quiet -am "change $1"
    git -C "$upstream" push --quiet origin main
}
run_task() {  # run_task ARGS...: sets rc and out (stdout and stderr)
    rc=0
    out=$(bash "$runner" "$@" 2>&1) || rc=$?
}

# 1. A plain run: the job runs once, gets its arguments, skips the Windows
#    bootstrap, and one log line records the exit code.
run_task usage-census --dry-run
assert_eq "$rc" 0 'census run exits 0'
assert_eq "$(record entry)" publish-usage-census.sh 'usage-census runs its entry'
assert_eq "$(record args)" --dry-run 'arguments reach the entry'
assert_eq "$(record reexec)" "$(record self)" 'the entry sees its own path in WSL_AUTOMATION_TASK_REEXEC'
assert_match "$(last_log)" '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z job=usage-census exit=0$' 'log line format'
assert_match "$(grep -- '/scripts/publish-usage-census.sh' "$TIMEOUT_CALLS")" \
    '^--kill-after=10s 900s .*bash .*/checkout/scripts/publish-usage-census.sh --dry-run$' 'the census runs under a 900 s limit'
assert_no_match "$out" WARNING 'a clean, current checkout warns nothing'

for pair in waiting-feed:publish-waiting-feed.sh:300s session-miner:run-session-miner.sh:1800s; do
    IFS=: read -r job entry limit <<<"$pair"
    : >"$TIMEOUT_CALLS"
    run_task "$job"
    assert_eq "$(record entry)" "$entry" "$job runs its entry"
    assert_match "$(grep -- "/scripts/$entry" "$TIMEOUT_CALLS")" "^--kill-after=10s $limit " "$job limit"
done

# 2. PATH gets the user and system directories a scheduler leaves out.
assert_match ":$(record path):" ":$HOME/.local/bin:" 'PATH has ~/.local/bin'
assert_match ":$(record path):" ':/usr/bin:' 'PATH has /usr/bin'
assert_match ":$(record path):" ':/opt/homebrew/bin:' 'PATH has Homebrew'

# 3. Exit codes propagate; a timed-out job is logged as one.
STUB_EXIT=5 run_task usage-census
assert_eq "$rc" 5 'the job exit code propagates'
assert_eq "$(last_log | sed 's/^[^ ]* //')" 'job=usage-census exit=5' 'nonzero exit is logged'
STUB_TIMEOUT_EXIT=124 run_task usage-census
assert_eq "$rc" 124 'a timed-out job exits 124'
assert_eq "$(last_log | sed 's/^[^ ]* //')" 'job=usage-census exit=124 timed-out=900s' 'the timeout is logged'
RUN_TASK_LIMIT=7 run_task usage-census
assert_match "$(tail -n 1 -- "$TIMEOUT_CALLS")" '^--kill-after=10s 7s ' 'RUN_TASK_LIMIT overrides the limit'
RUN_TASK_LIMIT=0 run_task usage-census
assert_eq "$rc" 2 'a zero limit is refused'
RUN_TASK_LIMIT='5; true' run_task usage-census
assert_eq "$rc" 2 'a non-numeric limit is refused'

# 4. An unknown job is refused before anything runs.
rm -f -- "$JOB_RECORD"
run_task backup
assert_eq "$rc" 2 'an unknown job exits 2'
assert_match "$out" '^usage: run-task.sh ' 'an unknown job prints usage'
run_task
assert_eq "$rc" 2 'no job exits 2'
assert_eq "$([[ -f $JOB_RECORD ]] && echo ran || echo idle)" idle 'nothing ran'

# 5. Overlap: while another run holds the job's lock, a new run skips with 0.
mkdir -p "$RUN_TASK_STATE_DIR"
exec 8>"$RUN_TASK_STATE_DIR/usage-census.lock"
flock -n 8 || fail 'the test could not take the lock'
run_task usage-census
assert_eq "$rc" 0 'a run that finds the lock held exits 0'
assert_eq "$([[ -f $JOB_RECORD ]] && echo ran || echo idle)" idle 'the overlapping run does not start the job'
assert_eq "$(last_log | sed 's/^[^ ]* //')" 'job=usage-census exit=0 skipped=running' 'the skip is logged'
assert_match "$out" 'still going; skipped' 'the skip is said on stderr'
run_task waiting-feed
assert_eq "$(record entry)" publish-waiting-feed.sh 'the lock is per job'
exec 8>&-
run_task usage-census
assert_eq "$(record entry)" publish-usage-census.sh 'the job runs once the lock is free'

# 6. The checkout is fast-forwarded from origin/main, and both the runner and
#    the entry then run the new code from disk.
push_upstream scripts/publish-usage-census.sh 's/version=v1/version=v2/'
# shellcheck disable=SC2016 # a sed script that adds a line to the runner
push_upstream scripts/unix/run-task.sh 's|^set -euo pipefail$|set -euo pipefail\n[[ -z ${RUN_TASK_REEXEC:-} ]] \|\| printf %s\\\\n "$RUN_TASK_REEXEC" >"$JOB_RECORD.runner"|'
rm -f -- "$JOB_RECORD.runner"
run_task usage-census
assert_eq "$rc" 0 'updated run exits 0'
assert_eq "$(record version)" v2 'the job runs the fast-forwarded entry'
assert_eq "$(cat "$JOB_RECORD.runner" 2>/dev/null)" usage-census 'the runner re-execs its updated self'
assert_eq "$(git -C "$checkout" rev-parse HEAD)" "$(git -C "$origin" rev-parse main)" 'the checkout is at origin/main'

# 7. A dirty checkout warns and runs its current code.
push_upstream scripts/publish-usage-census.sh 's/version=v2/version=v3/'
printf '\n' >>"$checkout/scripts/run-session-miner.sh"
run_task usage-census
assert_eq "$rc" 0 'dirty run exits 0'
assert_match "$out" 'WARNING: checkout update skipped \(the working tree has changes\)' 'dirty checkout warns'
assert_eq "$(record version)" v2 'dirty checkout keeps its code'
git -C "$checkout" checkout --quiet -- scripts/run-session-miner.sh

# 8. A checkout on another branch is switched back to main and updated.
git -C "$checkout" switch --quiet -c feature
run_task usage-census
assert_eq "$(git -C "$checkout" symbolic-ref --short HEAD)" main 'the checkout is switched to main'
assert_eq "$(record version)" v3 'and fast-forwarded'

# 9. A diverged main warns and keeps its code.
push_upstream scripts/publish-usage-census.sh 's/version=v3/version=v4/'
git -C "$checkout" commit --quiet --allow-empty -m 'local commit'
run_task usage-census
assert_match "$out" 'WARNING: checkout update skipped \(main is not a fast-forward of origin/main\)' 'diverged main warns'
assert_eq "$(record version)" v3 'diverged main keeps its code'
git -C "$checkout" reset --quiet --hard origin/main

# 10. Offline: a failed fetch warns, and the job still runs.
git -C "$checkout" remote set-url origin "$test_root/missing.git"
STUB_EXIT=3 run_task usage-census
assert_eq "$rc" 3 'offline run keeps the job exit code'
assert_match "$out" 'WARNING: checkout update skipped \(fetch failed or timed out\)' 'offline warns'
assert_eq "$(record entry)" publish-usage-census.sh 'offline run still runs the job'
git -C "$checkout" remote set-url origin "$origin"

# 11. The waiting feed updates at most every 300 seconds, counted by attempt.
push_upstream scripts/publish-waiting-feed.sh 's/version=v1/version=v2/'
RUN_TASK_NOW=1000000 run_task waiting-feed
assert_eq "$(record version)" v2 'the first waiting-feed run updates'
push_upstream scripts/publish-waiting-feed.sh 's/version=v2/version=v3/'
RUN_TASK_NOW=1000200 run_task waiting-feed
assert_eq "$(record version)" v2 'a run inside the interval does not fetch'
RUN_TASK_NOW=1000300 run_task waiting-feed
assert_eq "$(record version)" v3 'a run after the interval updates'

# 12. Missing flock or timeout refuses the run, and says so in the log.
for tool in flock timeout; do
    rm -f -- "$JOB_RECORD"
    rc=0
    out=$(
        export HIDE_TOOL=$tool
        # shellcheck disable=SC2317,SC2329
        command() {
            if [[ $1 == -v && (${2:-} == "$HIDE_TOOL" || ${2:-} == g"$HIDE_TOOL") ]]; then return 1; fi
            builtin command "$@"
        }
        export -f command
        bash "$runner" usage-census 2>&1
    ) || rc=$?
    assert_eq "$rc" 1 "missing $tool exits 1"
    assert_eq "$(last_log | sed 's/^[^ ]* //')" "job=usage-census exit=1 refused=no-$tool" "missing $tool is logged"
    assert_eq "$([[ -f $JOB_RECORD ]] && echo ran || echo idle)" idle "missing $tool: the job does not run"
done

# 13. The runner log keeps its newest 1,000 lines once it passes 2,000.
for ((i = 0; i < 2000; i++)); do printf 'old %d\n' "$i"; done >"$runner_log"
run_task usage-census
assert_eq "$(wc -l <"$runner_log" | tr -d ' ')" 1000 'the log is trimmed to 1000 lines'
assert_match "$(last_log)" 'job=usage-census exit=0$' 'the newest line survives the trim'

# 14. With the real entries, the runner's update replaces the Windows bootstrap:
#     the census entry starts no pwsh.exe and prints no bootstrap warning.
real="$test_root/real"
mkdir -p "$real/scripts/unix"
cp -- "$runner_src" "$real/scripts/unix/run-task.sh"
cp -- "$repository_root/scripts/publish-usage-census.sh" "$repository_root/scripts/update-task-checkout.sh" "$real/scripts/"
rc=0
out=$(SKILLS_EVALS_URL="file://$test_root/no-such-repo" USAGE_CENSUS_LOG="$test_root/census.log" \
    bash "$real/scripts/unix/run-task.sh" usage-census 2>&1) || rc=$?
assert_eq "$rc" 1 'the real census entry runs (and fails to clone the missing fixture)'
assert_match "$out" 'usage census: could not clone skills-evals' 'the real census entry ran its body'
assert_no_match "$out" 'checkout bootstrap unavailable' 'the Windows bootstrap is skipped'
assert_match "$out" 'checkout update skipped' 'a directory that is not a checkout only warns'

# ---------------------------------------------------------------------------
# install-schedule.sh
# ---------------------------------------------------------------------------

inst_repo="$test_root/install/wsl-automation"
mkdir -p "$inst_repo/scripts/unix"
cp -- "$runner_src" "$installer_src" "$inst_repo/scripts/unix/"
installer="$inst_repo/scripts/unix/install-schedule.sh"
inst_runner="$inst_repo/scripts/unix/run-task.sh"
export CALLS="$test_root/scheduler-calls"
for tool in systemctl launchctl; do
    # shellcheck disable=SC2016 # the stub's own $* and $CALLS
    printf '#!/usr/bin/env bash\nprintf "%s %%s\\n" "$*" >>"$CALLS"\n' "$tool" >"$test_root/bin/fake-$tool"
done
cat >"$test_root/bin/fake-loginctl" <<'EOF'
#!/usr/bin/env bash
printf 'Linger=%s\n' "${FAKE_LINGER:-no}"
EOF
export CRONTAB_FILE="$test_root/crontab"
cat >"$test_root/bin/fake-crontab" <<'EOF'
#!/usr/bin/env bash
case $1 in
    -l) if [[ -n ${FAKE_CRONTAB_LIST_FAILS:-} ]]; then echo 'crontab: cannot open your crontab: Permission denied' >&2; exit 1; fi
        if [[ -f $CRONTAB_FILE ]]; then cat -- "$CRONTAB_FILE"; else echo 'no crontab for test' >&2; exit 1; fi ;;
    -) cat >"$CRONTAB_FILE" ;;
    *) exit 64 ;;
esac
EOF
chmod +x "$test_root/bin/"fake-*
export INSTALL_SCHEDULE_SYSTEMCTL=fake-systemctl INSTALL_SCHEDULE_LOGINCTL=fake-loginctl
export INSTALL_SCHEDULE_LAUNCHCTL=fake-launchctl INSTALL_SCHEDULE_CRONTAB=fake-crontab
install() {  # install ARGS...: sets rc and out
    rc=0
    out=$(bash "$installer" "$@" 2>&1) || rc=$?
}

# 15. Argument checks.
install
assert_eq "$rc" 2 'no target exits 2'
install windows
assert_eq "$rc" 2 'an unknown target exits 2'
install cron --jobs usage-census,backup
assert_eq "$rc" 2 'an unknown job exits 2'
install cron --jobs
assert_eq "$rc" 2 '--jobs without a value exits 2'
install cron --verbose
assert_eq "$rc" 2 'an unknown option exits 2'
assert_eq "$([[ -e $CRONTAB_FILE || -e $CALLS ]] && echo touched || echo untouched)" untouched 'refused runs change nothing'

# 16. systemd: a oneshot service and a Persistent=true timer per job.
export XDG_CONFIG_HOME="$test_root/xdg"
unit_dir="$XDG_CONFIG_HOME/systemd/user"
install systemd
assert_eq "$rc" 0 'systemd install exits 0'
service=$(cat "$unit_dir/wsl-automation-usage-census.service")
timer=$(cat "$unit_dir/wsl-automation-usage-census.timer")
assert_line "$service" 'Type=oneshot' 'the service is oneshot'
assert_line "$service" "ExecStart=/bin/bash \"$inst_runner\" usage-census" 'the service runs run-task.sh usage-census'
assert_line "$service" 'TimeoutStartSec=960' 'the service has an outer time bound'
assert_line "$timer" 'OnCalendar=*-*-* 12:37:00' 'the census timer fires at 12:37'
assert_line "$timer" 'Persistent=true' 'the timer is persistent'
assert_line "$timer" 'WantedBy=timers.target' 'the timer installs into timers.target'
assert_eq "$(cat "$CALLS")" $'systemctl --user daemon-reload\nsystemctl --user enable --now wsl-automation-usage-census.timer' 'systemctl calls'
assert_match "$out" 'linger is off' 'linger off is reported'
assert_eq "$(find "$unit_dir" -type f | wc -l | tr -d ' ')" 2 'only the default job is installed'
if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze calendar "$(sed -n 's/^OnCalendar=//p' <<<"$timer")" >/dev/null ||
        fail 'systemd-analyze rejects the census OnCalendar'
    pass
fi

rm -f -- "$CALLS"
FAKE_LINGER=yes install systemd --jobs waiting-feed,session-miner,waiting-feed
assert_eq "$rc" 0 'systemd install of two jobs exits 0'
assert_line "$(cat "$unit_dir/wsl-automation-waiting-feed.timer")" 'OnCalendar=*-*-* *:*:00' 'the waiting feed fires every minute'
assert_line "$(cat "$unit_dir/wsl-automation-waiting-feed.timer")" 'Persistent=true' 'the waiting-feed timer is persistent'
assert_line "$(cat "$unit_dir/wsl-automation-waiting-feed.service")" 'TimeoutStartSec=360' 'waiting-feed outer bound'
assert_line "$(cat "$unit_dir/wsl-automation-session-miner.timer")" 'OnCalendar=*-*-* 07:17:00' 'the miner fires at 07:17'
assert_line "$(cat "$unit_dir/wsl-automation-session-miner.timer")" 'Persistent=true' 'the miner timer is persistent'
assert_line "$(cat "$unit_dir/wsl-automation-session-miner.service")" 'TimeoutStartSec=1860' 'miner outer bound'
assert_eq "$(grep -c 'enable --now' "$CALLS")" 2 'a repeated job is enabled once'
assert_no_match "$out" 'linger is off' 'linger on is not reported'

rm -rf -- "$XDG_CONFIG_HOME" "$CALLS"
install systemd --print
assert_eq "$rc" 0 '--print exits 0'
assert_match "$out" 'would run: fake-systemctl --user daemon-reload' '--print shows the commands'
assert_match "$out" 'Persistent=true' '--print shows the timer'
assert_eq "$([[ -e $XDG_CONFIG_HOME || -e $CALLS ]] && echo touched || echo untouched)" untouched '--print changes nothing'

# 17. launchd: a LaunchAgent per job, valid plist, bootstrapped into gui/<uid>.
install launchd --jobs usage-census,waiting-feed
assert_eq "$rc" 0 'launchd install exits 0'
agents="$HOME/Library/LaunchAgents"
plist_check=$(python3 -I - "$agents" "$inst_runner" <<'EOF'
import plistlib, sys
agents, runner = sys.argv[1], sys.argv[2]
census = plistlib.load(open(f"{agents}/local.wsl-automation.usage-census.plist", "rb"))
feed = plistlib.load(open(f"{agents}/local.wsl-automation.waiting-feed.plist", "rb"))
checks = [
    census["Label"] == "local.wsl-automation.usage-census",
    census["ProgramArguments"][1:] == [runner, "usage-census"],
    census["ProgramArguments"][0].endswith("/bash"),
    census["StartCalendarInterval"] == {"Hour": 12, "Minute": 37},
    "StartInterval" not in census,
    feed["StartInterval"] == 60,
    "StartCalendarInterval" not in feed,
    feed["ProgramArguments"][1:] == [runner, "waiting-feed"],
    census["StandardOutPath"] == "/dev/null",
]
print(" ".join("ok" if c else "bad" for c in checks))
EOF
)
assert_eq "$plist_check" 'ok ok ok ok ok ok ok ok ok' 'the plists parse and carry the schedule'
uid=$(id -u)
assert_eq "$(cat "$CALLS")" "launchctl bootout gui/$uid/local.wsl-automation.usage-census
launchctl bootstrap gui/$uid $agents/local.wsl-automation.usage-census.plist
launchctl bootout gui/$uid/local.wsl-automation.waiting-feed
launchctl bootstrap gui/$uid $agents/local.wsl-automation.waiting-feed.plist" 'launchctl reloads each agent'

# 18. cron: one marked block, idempotent, the rest of the crontab untouched.
printf '%s\n' '# my own jobs' '0 1 * * * echo keep' >"$CRONTAB_FILE"
install cron
assert_eq "$rc" 0 'cron install exits 0'
expected_line="37 12 * * * /bin/bash '$inst_runner' usage-census >/dev/null 2>&1"
assert_eq "$(cat "$CRONTAB_FILE")" "# my own jobs
0 1 * * * echo keep
# BEGIN wsl-automation jobs (scripts/unix/install-schedule.sh; re-run it to change this block)
$expected_line
# END wsl-automation jobs" 'the block is appended after the existing lines'
first=$(cat "$CRONTAB_FILE")
install cron
assert_eq "$(cat "$CRONTAB_FILE")" "$first" 'a second install leaves the crontab unchanged'
install cron --jobs session-miner,waiting-feed
assert_eq "$(grep -c '^# BEGIN wsl-automation' "$CRONTAB_FILE")" 1 'one block after a change'
assert_eq "$(grep -c 'usage-census' "$CRONTAB_FILE")" 0 'the block is replaced, not added to'
assert_line "$(cat "$CRONTAB_FILE")" "17 7 * * * /bin/bash '$inst_runner' session-miner >/dev/null 2>&1" 'the miner cron line'
assert_line "$(cat "$CRONTAB_FILE")" "* * * * * /bin/bash '$inst_runner' waiting-feed >/dev/null 2>&1" 'the waiting-feed cron line'
assert_eq "$(sed -n 2p "$CRONTAB_FILE")" '0 1 * * * echo keep' 'other lines survive'
rm -f -- "$CRONTAB_FILE"
install cron
assert_eq "$(sed -n 1p "$CRONTAB_FILE")" '# BEGIN wsl-automation jobs (scripts/unix/install-schedule.sh; re-run it to change this block)' 'an empty crontab gets just the block'
# A crontab -l that fails for any reason but "no crontab" is refused, and the
# crontab it could not read is left alone.
printf '%s\n' '0 1 * * * echo keep' >"$CRONTAB_FILE"
FAKE_CRONTAB_LIST_FAILS=1 install cron
assert_eq "$rc" 1 'a failed crontab -l is refused'
assert_match "$out" 'refusing to replace it' 'the refusal says why'
assert_eq "$(cat "$CRONTAB_FILE")" '0 1 * * * echo keep' 'the unread crontab is untouched'
# An unpaired marker is refused rather than read as a block to the end of the file.
for broken in begin-only end-only two-begins end-first; do
    case $broken in
        begin-only) printf '%s\n' '# BEGIN wsl-automation jobs (scripts/unix/install-schedule.sh; re-run it to change this block)' '0 1 * * * echo keep' ;;
        end-only) printf '%s\n' '0 1 * * * echo keep' '# END wsl-automation jobs' ;;
        two-begins) printf '%s\n' '# BEGIN wsl-automation jobs (scripts/unix/install-schedule.sh; re-run it to change this block)' '# BEGIN wsl-automation jobs (scripts/unix/install-schedule.sh; re-run it to change this block)' '# END wsl-automation jobs' '0 1 * * * echo keep' ;;
        end-first) printf '%s\n' '# END wsl-automation jobs' '0 1 * * * echo keep' '# BEGIN wsl-automation jobs (scripts/unix/install-schedule.sh; re-run it to change this block)' ;;
    esac >"$CRONTAB_FILE"
    broken_before=$(cat "$CRONTAB_FILE")
    install cron
    assert_eq "$rc" 1 "an unpaired marker ($broken) is refused"
    assert_match "$out" 'not one matched pair' "the refusal names the markers ($broken)"
    assert_eq "$(cat "$CRONTAB_FILE")" "$broken_before" "the crontab is untouched ($broken)"
done
rm -f -- "$CRONTAB_FILE"
install cron
before=$(cat "$CRONTAB_FILE")
install cron --print --jobs waiting-feed
assert_match "$out" 'waiting-feed' '--print shows the block'
assert_eq "$(cat "$CRONTAB_FILE")" "$before" 'cron --print changes nothing'

# 19. A checkout path a unit, plist or crontab would misread is refused.
odd="$test_root/odd\$dir'x/wsl-automation"
mkdir -p "$odd/scripts/unix"
cp -- "$installer_src" "$odd/scripts/unix/"
rc=0
out=$(bash "$odd/scripts/unix/install-schedule.sh" cron 2>&1) || rc=$?
assert_eq "$rc" 1 'an odd checkout path exits 1'
assert_match "$out" 'move the checkout to a plain path' 'the refusal says what to do'
assert_eq "$(cat "$CRONTAB_FILE")" "$before" 'the refused install changes nothing'

printf 'PASS: %s assertions\n' "$passed"
