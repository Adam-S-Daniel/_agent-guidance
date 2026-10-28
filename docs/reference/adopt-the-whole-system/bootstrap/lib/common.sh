# lib/common.sh: shared helpers for the bootstrap scripts. Sourced, never run.
#
# Provides:
#   load_config <path>     parse fleet.env strictly (no `source`), set CFG_* vars
#   fleet_repos            the five repo names from the guide (section 1)
#   repo_visibility <name> public|private, from the plan and fleet.env
#   gh_read <endpoint>     GET; sets GH_OUT (body) and GH_HTTP (status); never prints the body
#   gh_write_api <METHOD> <endpoint> [json]   write call, or a printed DRY-RUN line
#   run_write <cmd...>     any other write command (gh repo create, gh pr create, ...)
#   run_write_stdin <cmd...>  write command whose value arrives on stdin (secrets)
#   finish                 the "changed N things" / "nothing to change" summary
#
# Error output carries only an HTTP status or a fixed message, never a body.

set -euo pipefail

GH=${GH:-gh}
DRY_RUN=${DRY_RUN:-0}
WRITES=0
SKIPS=0

# The five repos (guide section 1). The names are the guide's suggestions.
FLEET_REPOS=(agent-guidance agent-skills agent-skills-private skills-evals repo-settings)
# The two that must be private on every plan (guide section 2, "What you need").
ALWAYS_PRIVATE=(agent-skills-private repo-settings)

log()  { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
did()  { if [ "$DRY_RUN" = 1 ]; then printf 'would have: %s\n' "$*"; else printf '%s\n' "$*"; fi; }
skip() { SKIPS=$((SKIPS + 1)); printf 'ok: %s\n' "$*"; }

# One work dir per script run; removed on exit. Bodies of reads land here
# only as stderr captures (status lines), never secrets.
BOOT_WORK=$(mktemp -d "${TMPDIR:-/tmp}/bootstrap.XXXXXX")
chmod 700 "$BOOT_WORK"
cleanup_work() { rm -rf "$BOOT_WORK"; }
trap cleanup_work EXIT

# ------------------------------------------------------------------ config
CONFIG_KEYS=(
  OWNER OWNER_TYPE PLAN REVIEWER OWNER_NAME
  VISIBILITY_AGENT_GUIDANCE VISIBILITY_SKILLS_EVALS
  SHA_PINNING_REQUIRED APP_NAME_PREFIX PLUGINS
  GUIDANCE_SYNC_APP_ID SETTINGS_ADMIN_APP_ID LOOP_WRITER_APP_ID
  LESSONS_READER_APP_ID ROSTER_WRITER_APP_ID
  REQUIRED_CHECKS
)

_valid_value() { # key value
  local k=$1 v=$2
  [ -z "$v" ] && return 0   # empty means "use the default"; required keys are checked after parsing
  case $k in
    OWNER|REVIEWER) [[ $v =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,38})$ ]] ;;
    OWNER_TYPE) [[ $v == user || $v == org ]] ;;
    PLAN) [[ $v == free || $v == paid ]] ;;
    OWNER_NAME) [[ $v =~ ^[A-Za-z0-9\ .\'-]{0,80}$ ]] ;;
    VISIBILITY_AGENT_GUIDANCE|VISIBILITY_SKILLS_EVALS) [[ $v == public || $v == private ]] ;;
    SHA_PINNING_REQUIRED) [[ $v == true || $v == false ]] ;;
    APP_NAME_PREFIX) [[ $v =~ ^[a-z0-9][a-z0-9-]{0,19}$ ]] ;;
    PLUGINS) [[ $v =~ ^[a-z0-9@\ ._-]{0,400}$ ]] ;;
    *_APP_ID) [[ $v =~ ^[1-9][0-9]{0,11}$ ]] ;;
    REQUIRED_CHECKS) [[ $v == on || $v == off ]] ;;
    *) return 1 ;;
  esac
}

load_config() { # path
  local path=$1 line key val n=0 known
  [ -f "$path" ] || die "config file not found: $path (copy fleet.env.example to fleet.env and fill it in)"
  for key in "${CONFIG_KEYS[@]}"; do printf -v "CFG_$key" '%s' ""; done
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line=${line%$'\r'}
    [[ $line =~ ^[[:space:]]*(#.*)?$ ]] && continue
    if [[ ! $line =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]]; then
      die "$path:$n: not a KEY=value line"
    fi
    key=${BASH_REMATCH[1]} val=${BASH_REMATCH[2]}
    if [[ $val =~ ^\"(.*)\"$ ]] || [[ $val =~ ^\'(.*)\'$ ]]; then val=${BASH_REMATCH[1]}; fi
    known=0
    for k in "${CONFIG_KEYS[@]}"; do [ "$k" = "$key" ] && known=1; done
    [ "$known" = 1 ] || die "$path:$n: unknown key $key"
    _valid_value "$key" "$val" || die "$path:$n: invalid value for $key"
    printf -v "CFG_$key" '%s' "$val"
  done < "$path"
  [ -n "$CFG_OWNER" ] || die "$path: OWNER is required"
  [ -n "$CFG_PLAN" ] || die "$path: PLAN is required (free or paid)"
  : "${CFG_OWNER_TYPE:=user}" "${CFG_REVIEWER:=$CFG_OWNER}" "${CFG_SHA_PINNING_REQUIRED:=true}" "${CFG_REQUIRED_CHECKS:=off}"
  # <your name> lands in public repos: default to the public handle, never a legal name.
  : "${CFG_OWNER_NAME:=$CFG_OWNER}"
  if [ -z "$CFG_APP_NAME_PREFIX" ]; then
    CFG_APP_NAME_PREFIX=$(printf '%s' "$CFG_OWNER" | tr '[:upper:]' '[:lower:]' | cut -c1-20)
  fi
  : "${CFG_PLUGINS:=${CFG_OWNER,,}-coding-anywhere}"
  CONFIG_PATH=$path
}

# set_config_value <key> <value>: rewrite one KEY= line in fleet.env (or
# append it), atomically, keeping every other line as it is.
set_config_value() {
  local key=$1 val=$2
  _valid_value "$key" "$val" || die "refusing to write an invalid $key"
  python3 -I - "$CONFIG_PATH" "$key" "$val" <<'PY'
import os, sys, tempfile
path, key, val = sys.argv[1:4]
lines = open(path, encoding="utf-8").read().splitlines()
out, done = [], False
for l in lines:
    if l.startswith(key + "="):
        if not done:
            out.append(f"{key}={val}")
            done = True
        continue
    out.append(l)
if not done:
    out.append(f"{key}={val}")
d = os.path.dirname(os.path.abspath(path))
fd, tmp = tempfile.mkstemp(dir=d, prefix=".fleet.env.")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
os.chmod(tmp, os.stat(path).st_mode & 0o777)
os.replace(tmp, path)
PY
  printf -v "CFG_$key" '%s' "$val"
}

repo_visibility() { # name
  local r=$1 p
  for p in "${ALWAYS_PRIVATE[@]}"; do [ "$p" = "$r" ] && { echo private; return; }; done
  if [ "$CFG_PLAN" = paid ]; then
    case $r in
      agent-guidance) echo "${CFG_VISIBILITY_AGENT_GUIDANCE:-public}"; return ;;
      skills-evals) echo "${CFG_VISIBILITY_SKILLS_EVALS:-public}"; return ;;
    esac
  fi
  echo public
}

description_for() {
  case $1 in
    agent-guidance) echo "Fleet guidance, its sync, the git-hook check runner and routine specs" ;;
    agent-skills) echo "Shareable agent skills, packaged as a Claude Code plugin marketplace" ;;
    agent-skills-private) echo "Private agent skills and lesson records" ;;
    skills-evals) echo "Eval fixtures, harness, results, planner and gate workflows" ;;
    repo-settings) echo "Rulesets, merge settings and App bypasses, as JSON applied by a script" ;;
  esac
}

# The README create-repos.sh writes. copy-starter.sh replaces a README.md only
# when it is still exactly these bytes; any edit makes it yours (--force).
GENERATED_README_MARKER='<!-- generated by bootstrap/create-repos.sh; copy-starter.sh replaces this file while it is unchanged -->'
generated_readme() { # name
  printf '# %s\n\n%s\n\n%s\n' "$1" "$(description_for "$1")" "$GENERATED_README_MARKER"
}

is_known_repo() { local r; for r in "${FLEET_REPOS[@]}"; do [ "$r" = "$1" ] && return 0; done; return 1; }

# ------------------------------------------------------------------ gh calls
_status_from() { grep -oE 'HTTP [0-9]{3}' "$1" 2>/dev/null | tail -n1 | cut -d' ' -f2 || true; }

gh_read() { # endpoint
  local err="$BOOT_WORK/err"
  GH_HTTP=""
  if GH_OUT=$("$GH" api "$1" 2>"$err"); then GH_HTTP=200; return 0; fi
  GH_OUT=""
  GH_HTTP=$(_status_from "$err"); GH_HTTP=${GH_HTTP:-000}
  return 1
}

# gh_read_or_die <endpoint> <what>: a read that must succeed.
gh_read_or_die() {
  gh_read "$1" || die "could not read $2 (HTTP $GH_HTTP)"
}

_print_cmd() { printf 'DRY-RUN:'; printf ' %q' "$@"; printf '\n'; }

gh_write_api() { # METHOD endpoint [json]
  local method=$1 endpoint=$2 body=${3:-} err="$BOOT_WORK/err" in="$BOOT_WORK/body.json"
  WRITES=$((WRITES + 1))
  if [ "$DRY_RUN" = 1 ]; then
    if [ -n "$body" ]; then
      _print_cmd "$GH" api -X "$method" "$endpoint" --input - ; printf '         body: %s\n' "$(printf '%s' "$body" | jq -c .)"
    else
      _print_cmd "$GH" api -X "$method" "$endpoint"
    fi
    GH_OUT="{}"; return 0
  fi
  if [ -n "$body" ]; then
    printf '%s' "$body" > "$in"
    if GH_OUT=$("$GH" api -X "$method" "$endpoint" --input "$in" 2>"$err"); then rm -f "$in"; return 0; fi
    rm -f "$in"
  else
    if GH_OUT=$("$GH" api -X "$method" "$endpoint" 2>"$err"); then return 0; fi
  fi
  GH_OUT=""
  local s; s=$(_status_from "$err")
  die "gh api -X $method $endpoint failed (HTTP ${s:-unknown})"
}

run_write() { # cmd...
  WRITES=$((WRITES + 1))
  if [ "$DRY_RUN" = 1 ]; then _print_cmd "$@"; return 0; fi
  "$@" >/dev/null 2>"$BOOT_WORK/err" || die "${*:1:3} failed (HTTP $(_status_from "$BOOT_WORK/err"))"
}

# run_write_stdin <cmd...>: the value comes on this function's stdin, so it is
# never in an argument list. In a dry run stdin is drained, not read into memory.
run_write_stdin() {
  WRITES=$((WRITES + 1))
  if [ "$DRY_RUN" = 1 ]; then cat >/dev/null; _print_cmd "$@"; printf '         (value on stdin, not shown)\n'; return 0; fi
  "$@" >/dev/null 2>"$BOOT_WORK/err" || die "${*:1:3} failed (HTTP $(_status_from "$BOOT_WORK/err"))"
}

finish() {
  if [ "$WRITES" = 0 ]; then
    log "Nothing to change: everything was already in place ($SKIPS checks)."
  elif [ "$DRY_RUN" = 1 ]; then
    log "Dry run: $WRITES change(s) would be made; nothing was written."
  else
    log "Done: $WRITES change(s) made, $SKIPS already in place."
  fi
}

# Common argument parsing: --config, --dry-run. Leftovers in REST_ARGS.
parse_common_args() {
  CONFIG=./fleet.env REST_ARGS=()
  while [ $# -gt 0 ]; do
    case $1 in
      --config) [ $# -ge 2 ] || die "--config needs a path"; CONFIG=$2; shift 2 ;;
      --config=*) CONFIG=${1#--config=}; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) REST_ARGS+=("$1"); shift ;;
    esac
  done
}

usage() { sed -n '2,/^$/{s/^# \{0,1\}//;p}' "$0"; }

require_tools() { local t; for t in "$@"; do command -v "$t" >/dev/null 2>&1 || die "$t is required"; done; }

# ------------------------------------------------------------------ registry
# Every environment, with where its name comes from:
#   guide     named in the guide (3.4, 4.5, 6.5, 9.4)
#   derived   the guide says an App's key lives in an environment but names
#             none; named after the App, as the guide does for guidance-sync
#             and loop-writer
#   reference the guide asks for an owner-approval environment (10.5 step 6)
#             without a name; named as in the reference registry
# Format: repo|environment|reviewer (owner|none)|source|optional (yes|no)
ENVIRONMENTS=(
  "agent-guidance|guidance-sync|none|guide|no"
  "agent-guidance|privacy|none|guide|no"
  "agent-skills|privacy|none|guide|no"
  "agent-skills|plugin-runtime-review|owner|reference|no"
  "skills-evals|privacy|none|guide|no"
  "skills-evals|loop-writer|none|guide|no"
  "skills-evals|routine-fire|none|guide|no"
  "skills-evals|lessons-reader|none|derived|no"
  "skills-evals|roster-writer|none|derived|yes"
  "repo-settings|privacy|none|guide|no"
  "repo-settings|settings-admin|none|derived|no"
)

# Every label a routine spec or workflow reads or writes, created in each repo
# by create-repos.sh and checked by verify.sh.
# Format: name|color|description (guide 11.3 and 10.5; the last two from the
# routine specs in starter/agent-guidance/docs/routines/)
LABELS=(
  "agent-ready|0e8a16|An agent may fix this."
  "on-hold|b60205|Do not touch. Only the owner adds or removes it."
  "agent-authored|5319e7|Opened by an agent or a loop gate."
  "vendor-release|fbca04|A vendor release may affect this repo (changelog watcher)."
  "agent-check|1d76db|A lesson to turn into a commit-time check (fleet worker)."
)

# Every App in the guide's 3.4 table.
# Format: app|permissions (JSON)|installed on (repos, or "fleet")|env repo|environment|variable|secret|config key|optional
APPS=(
  'guidance-sync|{"contents":"write","pull_requests":"write","workflows":"write"}|fleet|agent-guidance|guidance-sync|GUIDANCE_SYNC_CLIENT_ID|GUIDANCE_SYNC_PEM|GUIDANCE_SYNC_APP_ID|no'
  'settings-admin|{"administration":"write","metadata":"read"}|fleet|repo-settings|settings-admin|SETTINGS_ADMIN_CLIENT_ID|SETTINGS_ADMIN_PEM|SETTINGS_ADMIN_APP_ID|no'
  'loop-writer|{"contents":"write","pull_requests":"write","issues":"write"}|agent-skills agent-guidance skills-evals|skills-evals|loop-writer|LOOP_WRITER_CLIENT_ID|LOOP_WRITER_PEM|LOOP_WRITER_APP_ID|no'
  'lessons-reader|{"contents":"read"}|agent-skills-private|skills-evals|lessons-reader|LESSONS_READER_CLIENT_ID|LESSONS_READER_PEM|LESSONS_READER_APP_ID|no'
  'roster-writer|{"contents":"write","pull_requests":"write"}|skills-evals|skills-evals|roster-writer|ROSTER_WRITER_CLIENT_ID|ROSTER_WRITER_PEM|ROSTER_WRITER_APP_ID|yes'
)

# Secrets and variables that are not App credentials (set-secrets.sh).
# Format: repo|environment|kind (secret|variable)|name|source of the value
OTHER_VALUES=(
  "skills-evals|routine-fire|secret|EVAL_ROUTINE_FIRE_BEARER|prompt"
  "skills-evals|routine-fire|variable|ROUTINE_TRIGGER_ID|prompt"
  "agent-guidance|privacy|secret|PRIVACY_DENYLIST|file"
  "agent-skills|privacy|secret|PRIVACY_DENYLIST|file"
  "skills-evals|privacy|secret|PRIVACY_DENYLIST|file"
  "repo-settings|privacy|secret|PRIVACY_DENYLIST|file"
)

# Repository variables (repo level, not an environment's) that the kit's
# workflows read. create-environments.sh sets them; verify.sh compares them.
# Format: repo|NAME|source. repo:<fleet repo> is "$OWNER/<fleet repo>";
# cfg:<KEY> is that fleet.env value, and while it is empty the variable is
# left as it is (the workflow then uses its default). Rerun
# create-environments.sh after create-apps.sh or a changed REQUIRED_CHECKS.
REPO_VARIABLES=(
  "skills-evals|REGISTRY_REPO|repo:agent-skills"
  "skills-evals|GUIDANCE_REPO|repo:agent-guidance"
  "skills-evals|LESSONS_REPO|repo:agent-skills-private"
  "repo-settings|OWNER_TYPE|cfg:OWNER_TYPE"
  "repo-settings|LOOP_WRITER_APP_ID|cfg:LOOP_WRITER_APP_ID"
  "repo-settings|GUIDANCE_SYNC_APP_ID|cfg:GUIDANCE_SYNC_APP_ID"
  "repo-settings|REQUIRED_CHECKS|cfg:REQUIRED_CHECKS"
  "repo-settings|VISIBILITY_AGENT_GUIDANCE|cfg:VISIBILITY_AGENT_GUIDANCE"
  "repo-settings|VISIBILITY_SKILLS_EVALS|cfg:VISIBILITY_SKILLS_EVALS"
)

# repo_variable_value <source>: the value a REPO_VARIABLES source stands for.
repo_variable_value() {
  case $1 in
    repo:*) printf '%s/%s' "$CFG_OWNER" "${1#repo:}" ;;
    cfg:*) local k="CFG_${1#cfg:}"; printf '%s' "${!k}" ;;
    *) die "bad REPO_VARIABLES source: $1" ;;
  esac
}

# repo_variables <owner/repo>: set REPO_VARS_OUT to "NAME<TAB>value" lines,
# one per repository variable (not environment ones). Called directly, not in
# $( ), so GH_HTTP survives; returns 1 with GH_HTTP set when unreadable.
repo_variables() {
  local out err="$BOOT_WORK/err"
  REPO_VARS_OUT=""
  if out=$("$GH" variable list --repo "$1" --json name,value 2>"$err"); then
    GH_HTTP=200; REPO_VARS_OUT=$(printf '%s' "$out" | jq -r '.[] | [.name, .value] | @tsv'); return 0
  fi
  GH_HTTP=$(_status_from "$err"); GH_HTTP=${GH_HTTP:-000}; return 1
}

# env_available <repo>: environments exist in private repos only on a paid plan.
env_available() { [ "$CFG_PLAN" = paid ] || [ "$(repo_visibility "$1")" = public ]; }

# env_names <secret|variable> <owner/repo> <env>: set ENV_NAMES_OUT to the
# names set in an environment, one per line (names only: gh never lists
# values). Called directly, not in $( ), so GH_HTTP survives; returns 1 with
# GH_HTTP set when unreadable.
env_names() {
  local out err="$BOOT_WORK/err"
  ENV_NAMES_OUT=""
  if out=$("$GH" "$1" list --env "$3" --repo "$2" --json name 2>"$err"); then
    GH_HTTP=200; ENV_NAMES_OUT=$(printf '%s' "$out" | jq -r '.[].name'); return 0
  fi
  GH_HTTP=$(_status_from "$err"); GH_HTTP=${GH_HTTP:-000}; return 1
}
