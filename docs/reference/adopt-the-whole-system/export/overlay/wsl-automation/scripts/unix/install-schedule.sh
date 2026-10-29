#!/usr/bin/env bash
# Schedule this repo's jobs on Linux or macOS: the native counterpart of
# register-tasks.ps1. Every entry it writes runs scripts/unix/run-task.sh <job>,
# which supplies the overlap guard (flock), the time limit (timeout), the
# checkout update and the log line, so the three schedulers behave alike.
#
# Usage: bash scripts/unix/install-schedule.sh <systemd|launchd|cron> [--jobs JOB[,JOB...]] [--print]
#
#   systemd  a user service and timer per job in ~/.config/systemd/user, then
#            `systemctl --user daemon-reload` and `enable --now` each timer.
#            Timers are Persistent=true: a run missed while the machine was off
#            or asleep starts once at the next boot or wake. Native Linux has no
#            WSL post-wake transition window, so that catch-up is wanted there.
#            Jobs run without a login once linger is on:
#            `loginctl enable-linger "$USER"` (this script tells you if it is off).
#   launchd  a LaunchAgent per job in ~/Library/LaunchAgents, loaded with
#            `launchctl bootstrap gui/<uid>`. launchd starts a calendar job
#            missed during sleep once on wake, and never starts a second
#            instance of a job that is still running. A LaunchAgent needs your
#            login session; run-task.sh adds the flock guard and the limit.
#   cron     one block in your crontab, between marker lines, replaced on every
#            run and leaving the rest of the crontab alone. Plain cron has no
#            catch-up: a run missed while the machine was off stays missed.
#   --jobs   usage-census (the default), waiting-feed, session-miner. The
#            session miner refuses scheduled runs until you have run it by hand
#            with --dry-run (see README.md); the waiting feed needs its private
#            repository and a signed-in Claude Code.
#   --print  show what would be written and run, and change nothing.
#
#   job            schedule
#   usage-census   daily at 12:37 (midday, off the hour)
#   session-miner  daily at 07:17
#   waiting-feed   every minute
#
# Scheduled output goes to the logs the jobs keep themselves
# (~/.cache/wsl-automation-tasks.log and each job's own log), never to cron mail.
# The scheduler supplies no profile, so put git credentials where a job without
# a desktop session can reach them: `gh auth setup-git` with a token stored in a
# file, not the macOS keychain or a desktop credential manager, which need a
# login session.
#
# Check it:
#   systemd  systemctl --user list-timers 'wsl-automation-*'
#            journalctl --user -u wsl-automation-usage-census.service
#   launchd  launchctl print gui/$(id -u)/local.wsl-automation.usage-census
#   cron     crontab -l
#   all      tail ~/.cache/wsl-automation-tasks.log ~/.cache/usage-census.log
#
# Environment seams (for tests): INSTALL_SCHEDULE_SYSTEMCTL, _LOGINCTL,
# _LAUNCHCTL and _CRONTAB name the commands to run (default systemctl,
# loginctl, launchctl, crontab).
set -euo pipefail

usage() {
    printf '%s\n' 'usage: install-schedule.sh <systemd|launchd|cron> [--jobs JOB[,JOB...]] [--print]' >&2
    exit 2
}

target=${1:-}
case $target in
    systemd | launchd | cron) shift ;;
    *) usage ;;
esac
jobs=usage-census
print_only=0
while (($#)); do
    case $1 in
        --jobs)
            [[ $# -ge 2 ]] || usage
            jobs=$2
            shift 2
            ;;
        --jobs=*)
            jobs=${1#--jobs=}
            shift
            ;;
        --print)
            print_only=1
            shift
            ;;
        *) usage ;;
    esac
done

job_list=()
IFS=, read -r -a requested <<<"$jobs"
for job in "${requested[@]}"; do
    case $job in
        usage-census | waiting-feed | session-miner) ;;
        *)
            printf 'install-schedule: unknown job %s\n' "$job" >&2
            exit 2
            ;;
    esac
    for seen in "${job_list[@]+"${job_list[@]}"}"; do
        if [[ $seen == "$job" ]]; then continue 2; fi
    done
    job_list+=("$job")
done
if ((${#job_list[@]} == 0)); then usage; fi

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
runner=$repo/scripts/unix/run-task.sh
# The path lands inside unit files, a plist and a crontab line. Refuse anything
# that one of those would read as syntax rather than escaping it three ways.
if ! [[ $repo =~ ^/[A-Za-z0-9._/@+\ -]+$ ]]; then
    printf '%s\n' 'install-schedule: the checkout path has a character a unit, plist or crontab would misread; move the checkout to a plain path' >&2
    exit 1
fi

# A newer bash when Homebrew has one (macOS ships bash 3.2); else /bin/bash.
bash_path=/bin/bash
for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    if [[ $target == launchd && -x $candidate ]]; then
        bash_path=$candidate
        break
    fi
done

# job -> limit seconds (as in run-task.sh), hour, minute; minute "*" is every minute.
job_limit() {
    case $1 in
        usage-census) echo 900 ;;
        waiting-feed) echo 300 ;;
        session-miner) echo 1800 ;;
    esac
}
job_time() {
    case $1 in
        usage-census) echo '12 37' ;;
        waiting-feed) echo '* *' ;;
        session-miner) echo '7 17' ;;
    esac
}

systemd_service() {
    local job=$1
    cat <<EOF
# Written by wsl-automation's scripts/unix/install-schedule.sh; re-run it to change this file.
[Unit]
Description=wsl-automation scheduled job: $job

[Service]
# A oneshot unit is never started again while it is still running.
Type=oneshot
ExecStart=$bash_path "$runner" $job
# run-task.sh enforces the job's own limit with timeout; this is the outer bound.
TimeoutStartSec=$(($(job_limit "$job") + 60))
EOF
}

systemd_timer() {
    local job=$1 hour minute calendar
    read -r hour minute <<<"$(job_time "$job")"
    if [[ $minute == '*' ]]; then
        calendar='*-*-* *:*:00'
    else
        calendar=$(printf '*-*-* %02d:%02d:00' "$hour" "$minute")
    fi
    cat <<EOF
# Written by wsl-automation's scripts/unix/install-schedule.sh; re-run it to change this file.
[Unit]
Description=wsl-automation schedule: $job

[Timer]
OnCalendar=$calendar
# A run missed while the machine was off or asleep starts once at the next boot
# or wake (for an every-minute job, that is one extra run at most).
Persistent=true

[Install]
WantedBy=timers.target
EOF
}

launchd_plist() {
    local job=$1 hour minute schedule
    read -r hour minute <<<"$(job_time "$job")"
    if [[ $minute == '*' ]]; then
        schedule='  <key>StartInterval</key>
  <integer>60</integer>'
    else
        schedule="  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>$hour</integer>
    <key>Minute</key>
    <integer>$minute</integer>
  </dict>"
    fi
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- Written by wsl-automation's scripts/unix/install-schedule.sh; re-run it to change this file. -->
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.wsl-automation.$job</string>
  <key>ProgramArguments</key>
  <array>
    <string>$bash_path</string>
    <string>$runner</string>
    <string>$job</string>
  </array>
$schedule
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>/dev/null</string>
  <key>StandardErrorPath</key>
  <string>/dev/null</string>
</dict>
</plist>
EOF
}

cron_begin='# BEGIN wsl-automation jobs (scripts/unix/install-schedule.sh; re-run it to change this block)'
cron_end='# END wsl-automation jobs'

cron_block() {
    local job hour minute
    printf '%s\n' "$cron_begin"
    for job in "${job_list[@]}"; do
        read -r hour minute <<<"$(job_time "$job")"
        printf "%s %s * * * %s '%s' %s >/dev/null 2>&1\n" "$minute" "$hour" "$bash_path" "$runner" "$job"
    done
    printf '%s\n' "$cron_end"
}

write_file() {  # write_file PATH CONTENT
    if ((print_only)); then
        printf '==> %s\n%s\n' "$1" "$2"
    else
        mkdir -p -- "$(dirname -- "$1")"
        printf '%s\n' "$2" >"$1.tmp" && mv -f -- "$1.tmp" "$1"
        printf 'wrote %s\n' "$1"
    fi
}

run() {
    if ((print_only)); then
        printf '==> would run: %s\n' "$*"
    else
        "$@"
    fi
}

case $target in
    systemd)
        systemctl=${INSTALL_SCHEDULE_SYSTEMCTL:-systemctl}
        loginctl=${INSTALL_SCHEDULE_LOGINCTL:-loginctl}
        unit_dir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
        for job in "${job_list[@]}"; do
            write_file "$unit_dir/wsl-automation-$job.service" "$(systemd_service "$job")"
            write_file "$unit_dir/wsl-automation-$job.timer" "$(systemd_timer "$job")"
        done
        run "$systemctl" --user daemon-reload
        for job in "${job_list[@]}"; do
            run "$systemctl" --user enable --now "wsl-automation-$job.timer"
        done
        linger=$("$loginctl" show-user "$(id -un)" --property=Linger 2>/dev/null || true)
        if [[ $linger != Linger=yes ]]; then
            # shellcheck disable=SC2016 # the command is printed for the reader to run
            printf '%s\n' 'note: linger is off, so the timers stop when you log out. To run them without a login:' \
                '  loginctl enable-linger "$USER"'
        fi
        ;;
    launchd)
        launchctl=${INSTALL_SCHEDULE_LAUNCHCTL:-launchctl}
        agent_dir=$HOME/Library/LaunchAgents
        domain=gui/$(id -u)
        for job in "${job_list[@]}"; do
            plist=$agent_dir/local.wsl-automation.$job.plist
            write_file "$plist" "$(launchd_plist "$job")"
            # bootout fails when the agent is not loaded yet; that is fine.
            if ((print_only)); then
                run "$launchctl" bootout "$domain/local.wsl-automation.$job"
            else
                "$launchctl" bootout "$domain/local.wsl-automation.$job" >/dev/null 2>&1 || true
            fi
            run "$launchctl" bootstrap "$domain" "$plist"
        done
        ;;
    cron)
        crontab=${INSTALL_SCHEDULE_CRONTAB:-crontab}
        block=$(cron_block)
        if ((print_only)); then
            printf '==> crontab block\n%s\n' "$block"
            exit 0
        fi
        # Only "no crontab for <user>" (exit 1, Linux and macOS alike) means
        # there is nothing to keep. Any other failure (no permission, a
        # broken cron daemon) leaves the crontab unread, and writing the
        # block then would replace every job in it, so refuse.
        list_err=$(mktemp)
        list_rc=0
        current=$("$crontab" -l 2>"$list_err") || list_rc=$?
        list_msg=$(<"$list_err")
        rm -f -- "$list_err"
        if ((list_rc != 0)); then
            if [[ $list_msg == *"no crontab for"* ]]; then
                current=''
            else
                printf 'install-schedule.sh: %s -l failed (exit %s), so the crontab was not read; refusing to replace it\n' "$crontab" "$list_rc" >&2
                exit 1
            fi
        fi
        # The block must be absent or one BEGIN followed by one END. Anything
        # else (a hand-edited or half-deleted block) would make the filter
        # below drop the user's own lines up to the end of the file.
        begins=$(grep -cxF -- "$cron_begin" <<<"$current" || true)
        ends=$(grep -cxF -- "$cron_end" <<<"$current" || true)
        if ((begins != ends || begins > 1)) || { ((begins == 1)) && ! awk -v begin="$cron_begin" -v end="$cron_end" '
                $0 == begin { b = NR } $0 == end { e = NR } END { exit !(b < e) }' <<<"$current"; }; then
            printf 'install-schedule.sh: the crontab has %s BEGIN and %s END wsl-automation marker line(s), not one matched pair; fix it with crontab -e, then re-run\n' "$begins" "$ends" >&2
            exit 1
        fi
        kept=$(awk -v begin="$cron_begin" -v end="$cron_end" '
            $0 == begin { skipping = 1; next }
            $0 == end { skipping = 0; next }
            !skipping { print }' <<<"$current")
        if [[ -n $kept ]]; then
            printf '%s\n%s\n' "$kept" "$block" | "$crontab" -
        else
            printf '%s\n' "$block" | "$crontab" -
        fi
        printf '%s\n' 'installed the crontab block:' "$block"
        ;;
esac
