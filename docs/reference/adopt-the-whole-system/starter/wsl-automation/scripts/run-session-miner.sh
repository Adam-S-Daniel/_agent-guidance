#!/usr/bin/env bash
# Run the daily session miner from inside WSL (or natively on Linux or macOS).
#
# Run by the 'Session Miner' scheduled task, which calls wsl.exe directly (no pwsh
# in between), or on Linux and macOS by scripts/unix/run-task.sh session-miner.
# Like publish-usage-census.sh it shallow-clones skills-evals `main` and runs that
# copy's scripts/run_session_miner.sh (skills-evals ADR 0015), so a scheduled run
# always executes the reviewed main version, never a local feature branch.
#
# What it reads: this machine's real Claude Code transcripts. What leaves the
# machine: short, redacted, hashed lesson records, pushed to the private
# registry's persistent/lessons branch. This wrapper never copies a transcript
# anywhere, and it refuses to run in CI or in a cloud session.
#
# Setup a scheduled run cannot do for you (README.md, "Session miner task"):
#   - a privacy denylist kept outside every repository (PRIVACY_DENYLIST_FILE,
#     default ~/.config/privacy-denylist.txt); without it nothing is mined;
#   - the private registry clone whose origin receives persistent/lessons
#     (LESSONS_CLONE, default ~/repos/acsi-agentskills-private), with a git
#     credential that works with no desktop session (`gh auth git-credential`
#     and a file-stored token, not a desktop credential manager);
#   - python3 with PyYAML;
#   - one run by hand with --dry-run. Until one has succeeded, a scheduled run
#     (no arguments) refuses with exit 3 and mines nothing.
#
# Settings come from the environment or, because a scheduled action has none,
# from an optional KEY=value file (SESSION_MINER_CONFIG, default
# ~/.config/wsl-automation/session-miner.env). The file is read as data, never
# sourced: blank lines and # comments are skipped, only the keys below are
# accepted, a leading ~/ is expanded, and a variable already in the
# environment wins.
#   PRIVACY_DENYLIST_FILE  LESSONS_CLONE  GUIDANCE_MANIFEST  MINER_REGISTRIES
#   WIN_HOME  MINER_STATE  SKILLS_EVALS_URL  PYTHON
#   FILE_ISSUES            `on` allows --file-issues (off until a sample of
#                          records has been rated for usefulness and privacy)
#
# In WSL, WIN_HOME (the Windows profile as a WSL path, for example
# /mnt/c/Users/<you>) adds the Windows transcripts and the desktop app's
# metadata, which lets the miner drop scheduled-task sessions as noise. Without
# it the miner would ask cmd.exe, which is untested in the session-0 logon a
# scheduled task gets, so this wrapper reads only the WSL transcripts instead
# and says so in the log line.
#
# Usage: run-session-miner.sh [--dry-run] [--file-issues]
#
# One line per run is appended to the log: UTC time, exit code, and the miner's
# own summary. Only lines the miner prints with its "session miner: " prefix
# (counts) are kept; any other output, a Python traceback for example, could
# carry a path or an excerpt, so it is counted, not copied. No path or personal
# data reaches the log or stdout.
#
# Environment seams (for tests):
#   SESSION_MINER_LOG     log file (default ~/.cache/session-miner.log)
#   SESSION_MINER_MARKER  first-dry-run marker
#                         (default ~/.local/state/session-miner/dry-run-ok)
#   SESSION_MINER_WSL     1 when running in WSL (default: detected)
set -euo pipefail

# Update the checkout from main and re-exec this entry first; see
# update-task-checkout.sh. A daily run always tries (interval 0).
scripts_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/update-task-checkout.sh
. "$scripts_dir/update-task-checkout.sh"
wsl_automation_task_bootstrap "$scripts_dir/run-session-miner.sh" 0 300 "$@"

log=${SESSION_MINER_LOG:-$HOME/.cache/session-miner.log}
marker=${SESSION_MINER_MARKER:-$HOME/.local/state/session-miner/dry-run-ok}
config=${SESSION_MINER_CONFIG:-$HOME/.config/wsl-automation/session-miner.env}
notes=''

# finish CODE MESSAGE: log one line, print the message, exit with CODE.
finish() {
    local message=$2
    if [[ -n $notes ]]; then message="$message ($notes)"; fi
    mkdir -p -- "$(dirname -- "$log")"
    printf '%s exit=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$message" >>"$log"
    printf '%s\n' "$message"
    exit "$1"
}

note() {
    notes=${notes:+$notes; }$1
}

dry_run=0
file_issues=0
for arg in "$@"; do
    case $arg in
        --dry-run) dry_run=1 ;;
        --file-issues) file_issues=1 ;;
        *)
            printf '%s\n' 'usage: run-session-miner.sh [--dry-run] [--file-issues]' >&2
            exit 2
            ;;
    esac
done

# skills-evals ADR 0015: real transcripts are read only on the owner's own
# computer, never in CI or a cloud session, which must never be given any.
if [[ -n ${CI:-} || -n ${GITHUB_ACTIONS:-} || -n ${CLAUDE_CODE_REMOTE:-} || -n ${CLAUDE_CODE_REMOTE_SESSION_ID:-} ]]; then
    finish 2 'session miner: refusing to run in CI or a cloud session'
fi

allowed=' PRIVACY_DENYLIST_FILE LESSONS_CLONE GUIDANCE_MANIFEST MINER_REGISTRIES WIN_HOME MINER_STATE SKILLS_EVALS_URL PYTHON FILE_ISSUES '
if [[ -f $config ]]; then
    line_number=0
    while IFS= read -r line || [[ -n $line ]]; do
        line_number=$((line_number + 1))
        if [[ $line =~ ^[[:space:]]*(#.*)?$ ]]; then continue; fi
        # The message names the line, not the file: the path holds the user name.
        if ! [[ $line =~ ^([A-Z_]+)=(.*)$ ]] || [[ $allowed != *" ${BASH_REMATCH[1]} "* ]]; then
            finish 2 "session miner: config line $line_number is not an allowed KEY=value"
        fi
        key=${BASH_REMATCH[1]}
        value=${BASH_REMATCH[2]}
        # A literal ~/ prefix, not an expansion: the value is data.
        # shellcheck disable=SC2088
        if [[ $value == '~/'* ]]; then value=$HOME/${value#'~/'}; fi
        if [[ -z ${!key+set} ]]; then export "$key=$value"; fi
    done <"$config"
fi

export PRIVACY_DENYLIST_FILE=${PRIVACY_DENYLIST_FILE:-$HOME/.config/privacy-denylist.txt}
export LESSONS_CLONE=${LESSONS_CLONE:-$HOME/repos/acsi-agentskills-private}
export SKILLS_EVALS_URL=${SKILLS_EVALS_URL:-https://github.com/example-adopter/skills-evals.git}
# Never wait on a credential prompt: a scheduled run has no one to answer it.
export GIT_TERMINAL_PROMPT=0

if [[ ! -r $PRIVACY_DENYLIST_FILE ]]; then
    finish 2 'session miner: no readable privacy denylist (PRIVACY_DENYLIST_FILE); nothing mined'
fi
if [[ -z ${LESSONS_REPO_URL:-} ]] && ! git -C "$LESSONS_CLONE" remote get-url origin >/dev/null 2>&1; then
    finish 1 'session miner: no private registry clone with an origin at LESSONS_CLONE'
fi
if ! "${PYTHON:-python3}" -c 'import yaml' >/dev/null 2>&1; then
    finish 1 'session miner: python3 with PyYAML is required'
fi
if ((file_issues)) && [[ ${FILE_ISSUES:-} != on ]]; then
    finish 2 'session miner: --file-issues stays off until a sample of records is rated (FILE_ISSUES=on)'
fi
if ((!dry_run)) && [[ ! -f $marker ]]; then
    finish 3 'session miner: not set up yet; run it by hand with --dry-run first'
fi

in_wsl=${SESSION_MINER_WSL:-}
if [[ -z $in_wsl ]]; then
    in_wsl=0
    if [[ -n ${WSL_DISTRO_NAME:-} || -e /proc/sys/fs/binfmt_misc/WSLInterop ]]; then in_wsl=1; fi
fi
if [[ $in_wsl == 1 && -z ${WIN_HOME:-} && -z ${MINER_ROOTS:-} ]]; then
    export MINER_ROOTS="wsl=$HOME/.claude/projects"
    note 'WIN_HOME unset: Windows transcripts skipped'
fi
if [[ ! -f ${MINER_STATE:-$HOME/.local/state/session-miner/state.json} ]]; then
    note 'no state file yet: every session is read'
fi

tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

if ! git clone --quiet --depth 1 -- "$SKILLS_EVALS_URL" "$tmp/skills-evals" >/dev/null 2>&1; then
    finish 1 'session miner: could not clone skills-evals'
fi

miner_args=()
if ((dry_run)); then miner_args+=(--dry-run); fi
if ((file_issues)); then miner_args+=(--file-issues); fi
rc=0
bash "$tmp/skills-evals/scripts/run_session_miner.sh" "${miner_args[@]+"${miner_args[@]}"}" \
    >"$tmp/out" 2>"$tmp/err" </dev/null || rc=$?

summary=$(awk '
    /^session miner: / { sub(/^session miner: /, ""); kept = kept (kept == "" ? "" : " | ") $0; next }
    NF { withheld++ }
    END {
        if (kept == "") kept = "no summary"
        if (withheld) kept = kept " [" withheld " other output line(s) withheld]"
        print kept
    }' "$tmp/out" "$tmp/err")

if ((dry_run)) && ((rc == 0)); then
    mkdir -p -- "$(dirname -- "$marker")"
    date -u +%Y-%m-%dT%H:%M:%SZ >"$marker"
fi
finish "$rc" "session miner: $summary"
