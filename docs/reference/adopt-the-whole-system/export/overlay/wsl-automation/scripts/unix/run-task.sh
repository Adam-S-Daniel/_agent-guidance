#!/usr/bin/env bash
# Run one of this repo's scheduled jobs on Linux or macOS the way Task Scheduler
# runs it on Windows: never two at once, a hard time limit, the checkout updated
# from main first, and one log line per run. The systemd timers, launchd agents
# and cron lines that install-schedule.sh writes all call this script, so the
# guard is the same whichever scheduler fires it.
#
# Usage: bash scripts/unix/run-task.sh <job> [arguments passed to the job's entry script]
#
#   job            entry (scripts/)         limit   checkout update
#   usage-census   publish-usage-census.sh  15 min  every run
#   waiting-feed   publish-waiting-feed.sh   5 min  at most every 5 minutes
#   session-miner  run-session-miner.sh     30 min  every run
#
# In order:
#   1. PATH. A scheduler starts jobs with a minimal environment and no profile
#      (cron's PATH is /usr/bin:/bin), so ~/.local/bin, Homebrew's and the
#      usual system directories are appended, and GNU coreutils' gnubin
#      directories (Homebrew on macOS) go first: publish-waiting-feed.sh needs
#      GNU date.
#   2. No overlap. `flock -n` on a per-job lock; a run that finds the previous
#      one still going logs `skipped=running` and exits 0, like Task
#      Scheduler's MultipleInstances IgnoreNew. macOS ships no flock (`brew
#      install flock`); without flock, or without coreutils timeout (`timeout`,
#      or `gtimeout` from `brew install coreutils`), the job refuses to start.
#   3. The checkout. Fetch origin, switch a clean checkout to main, and
#      fast-forward it to origin/main, then re-exec this script once from disk
#      (bash reads a script as it runs). A dirty, diverged, offline or slow
#      checkout warns and the job runs its current code: the update fails open
#      and never blocks the job, as on Windows (update-task-checkout.ps1). The
#      job runs whatever is on main with your credentials in reach, so keep
#      main protected.
#   4. The job, under timeout with its limit and a 10 second kill grace. The
#      entry's own Windows checkout bootstrap is skipped
#      (WSL_AUTOMATION_TASK_REEXEC), because step 3 did that work natively.
#   5. One line appended to ~/.cache/wsl-automation-tasks.log: UTC time, job,
#      exit code. The job's own log (for example ~/.cache/usage-census.log)
#      holds its totals. Past 2,000 lines the log keeps its newest 1,000.
#
# Environment seams (for tests):
#   RUN_TASK_STATE_DIR  locks and update stamps
#                       (default ${XDG_STATE_HOME:-~/.local/state}/wsl-automation)
#   RUN_TASK_LOG        the runner log (default ~/.cache/wsl-automation-tasks.log)
#   RUN_TASK_LIMIT      the job's limit in seconds (default: the table above)
#   RUN_TASK_NOW        epoch seconds for the update throttle (default: now)
set -euo pipefail

job=${1:-}
case $job in
    usage-census) entry=publish-usage-census.sh limit=900 interval=0 ;;
    waiting-feed) entry=publish-waiting-feed.sh limit=300 interval=300 ;;
    session-miner) entry=run-session-miner.sh limit=1800 interval=0 ;;
    *)
        printf '%s\n' 'usage: run-task.sh <usage-census|waiting-feed|session-miner> [arguments]' >&2
        exit 2
        ;;
esac
shift
limit=${RUN_TASK_LIMIT:-$limit}
if ! [[ $limit =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' 'run-task: RUN_TASK_LIMIT must be a positive number of seconds' >&2
    exit 2
fi

unix_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd -- "$unix_dir/../.." && pwd)
scripts_dir=$repo/scripts
state_dir=${RUN_TASK_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/wsl-automation}
runner_log=${RUN_TASK_LOG:-$HOME/.cache/wsl-automation-tasks.log}

for gnubin in /usr/local/opt/coreutils/libexec/gnubin /opt/homebrew/opt/coreutils/libexec/gnubin; do
    if [[ -d $gnubin && :$PATH: != *":$gnubin:"* ]]; then PATH=$gnubin:$PATH; fi
done
PATH=$PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
export PATH
# Never wait on a credential prompt: a scheduled run has no one to answer it.
export GIT_TERMINAL_PROMPT=0

log_line() {
    mkdir -p -- "$(dirname -- "$runner_log")"
    printf '%s job=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$job" "$1" >>"$runner_log"
    if (($(wc -l <"$runner_log") > 2000)); then
        tail -n 1000 -- "$runner_log" >"$runner_log.tmp" && mv -f -- "$runner_log.tmp" "$runner_log"
    fi
}

flock_cmd=$(command -v flock || true)
timeout_cmd=$(command -v timeout || command -v gtimeout || true)
if [[ -z $flock_cmd ]]; then
    log_line 'exit=1 refused=no-flock'
    printf '%s\n' 'run-task: flock is required to keep runs from overlapping (macOS: brew install flock)' >&2
    exit 1
fi
if [[ -z $timeout_cmd ]]; then
    log_line 'exit=1 refused=no-timeout'
    printf '%s\n' 'run-task: coreutils timeout is required to bound a run (macOS: brew install coreutils)' >&2
    exit 1
fi

warn() {
    printf 'WARNING: checkout update skipped (%s); continuing with current code\n' "$1" >&2
}

git_quiet() {
    "$timeout_cmd" --kill-after=5s 60s git -C "$repo" "$@" >/dev/null 2>&1
}

update_checkout() {
    local stamp=$state_dir/$job.update-stamp now last='' branch status
    now=${RUN_TASK_NOW:-$(date +%s)}
    # The stamp holds the time of the last attempt as text, so the throttle needs
    # no GNU or BSD stat. It records the attempt, not a success: an offline
    # machine retries once per interval, not every run.
    if ((interval > 0)) && [[ -f $stamp ]]; then
        last=$(<"$stamp") || last=''
        if [[ $last =~ ^[0-9]+$ ]] && ((now >= last && now - last < interval)); then
            return 0
        fi
    fi
    printf '%s\n' "$now" >"$stamp" 2>/dev/null || true

    if ! git_quiet fetch --quiet origin main; then
        warn 'fetch failed or timed out'
        return 0
    fi
    if ! status=$("$timeout_cmd" --kill-after=5s 60s git -C "$repo" status --porcelain 2>/dev/null); then
        warn 'status failed'
        return 0
    fi
    if [[ -n $status ]]; then
        warn 'the working tree has changes'
        return 0
    fi
    branch=$(git -C "$repo" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
    if [[ $branch != main ]] && ! git_quiet switch --quiet main; then
        warn 'could not switch to main'
        return 0
    fi
    if ! git_quiet merge --ff-only --quiet origin/main; then
        warn 'main is not a fast-forward of origin/main'
    fi
}

if [[ ${RUN_TASK_REEXEC:-} != "$job" ]]; then
    mkdir -p -- "$state_dir"
    # The lock is taken once, before the update. fd 9 stays open across exec and
    # into the job, so neither the re-exec nor the job lets a second run in.
    exec 9>"$state_dir/$job.lock"
    if ! "$flock_cmd" -n 9; then
        log_line 'exit=0 skipped=running'
        printf 'run-task: the previous %s run is still going; skipped\n' "$job" >&2
        exit 0
    fi
    update_checkout
    export RUN_TASK_REEXEC=$job
    exec "$BASH" "$unix_dir/run-task.sh" "$job" "$@"
fi

rc=0
WSL_AUTOMATION_TASK_REEXEC=$scripts_dir/$entry \
    "$timeout_cmd" --kill-after=10s "${limit}s" "$BASH" "$scripts_dir/$entry" "$@" || rc=$?
if ((rc == 124)); then
    log_line "exit=124 timed-out=${limit}s"
else
    log_line "exit=$rc"
fi
exit "$rc"
