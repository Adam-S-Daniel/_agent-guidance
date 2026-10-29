# lib/common.sh: shared helpers for the bootstrap scripts. Sourced, never run.
#
# Provides:
#   load_config <path>     parse fleet.env strictly (no `source`), set CFG_* vars
#   FLEET_REPOS            the six repos the kit exports (DECISIONS C1 names)
#   kit_tree <repo>        the kit's starter/<tree> folder name for a repo
#   EVAL_RESULTS_BRANCH    skills-evals' persistent/eval-results, which create-repos.sh creates
#   repo_visibility <name> public|private, from the plan and fleet.env
#   gh_read <endpoint>     GET; sets GH_OUT (body) and GH_HTTP (status); never prints the body
#   gh_write_api <METHOD> <endpoint> [json]   write call, or a printed DRY-RUN line
#   run_write <cmd...>     any other write command (gh repo create, gh pr create, ...)
#   run_write_stdin <cmd...>  write command whose value arrives on stdin (secrets)
#   value_names <secret|variable> <owner/repo> <env|->  names set (never values)
#   finish                 the "changed N things" / "nothing to change" summary
#
# The registries below (ENVIRONMENTS, APPS, OTHER_VALUES, REPO_VARIABLES,
# LABELS) list what the exported repos' workflows actually read. The kit's
# tests (tests/check-refs.py) parse every starter workflow and fail on a name
# no registry or the README's by-hand list covers.
#
# Error output carries only an HTTP status or a fixed message, never a body.

# bash 4.4 or later: the scripts use associative arrays, ${var,,} and
# "${arr[@]}" on an empty array under `set -u`, which bash 3.2 (macOS's
# /bin/bash) and 4.0-4.3 reject or get wrong. This check itself parses in 3.2.
bash_version_ok() { # major minor
  [ "$1" -gt 4 ] || { [ "$1" -eq 4 ] && [ "$2" -ge 4 ]; }
}
if [ -z "${BASH_VERSINFO:-}" ] || ! bash_version_ok "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}"; then
  printf 'ERROR: the bootstrap needs bash 4.4 or later, and this is bash %s. On macOS: brew install bash, then run the script as: "$(brew --prefix)/bin/bash" <script> ...\n' "${BASH_VERSION:-unknown}" >&2
  exit 1
fi

set -euo pipefail

GH=${GH:-gh}
DRY_RUN=${DRY_RUN:-0}
WRITES=0
SKIPS=0

# The six repos, as the owner names them (DECISIONS C1: the registries drop
# the owner's name; _agent-guidance, repo-settings and skills-evals keep theirs).
FLEET_REPOS=(_agent-guidance repo-settings acsi-agentskills acsi-agentskills-private skills-evals wsl-automation)
# Created only when named or with --with-optional: the waiting feed's private
# data repo (REGENERATE.md, wsl-automation), which no kit tree fills.
OPTIONAL_REPOS=(waiting-feed)
# Private on every plan: the private registry and its lessons, the settings
# engine's config (the owner's is private), and the waiting feed (session titles).
ALWAYS_PRIVATE=(acsi-agentskills-private repo-settings waiting-feed)
# Public on every plan: the plugin marketplace other machines add by URL.
ALWAYS_PUBLIC=(acsi-agentskills)
# The one results branch the eval loop's workflows fetch or check out and
# nothing else creates, so a brand-new account fails on it (cold start):
# skills-evals' persistent/eval-results. create-repos.sh makes it an orphan
# commit holding a README; verify.sh's `branches` group checks it. The other
# persistent/* branches are created by the script or routine that writes them.
EVAL_RESULTS_REPO=skills-evals
EVAL_RESULTS_BRANCH=persistent/eval-results

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

# kit_tree <repo>: the starter folder that holds a repo's export.
kit_tree() {
  case $1 in
    _agent-guidance) echo agent-guidance ;;
    acsi-agentskills) echo agent-skills ;;
    acsi-agentskills-private) echo agent-skills-private ;;
    repo-settings|skills-evals|wsl-automation) echo "$1" ;;
    *) return 1 ;;
  esac
}

# ------------------------------------------------------------------ config
CONFIG_KEYS=(
  OWNER OWNER_TWO OWNER_TYPE PLAN REVIEWER OWNER_NAME
  VISIBILITY_AGENT_GUIDANCE VISIBILITY_SKILLS_EVALS VISIBILITY_WSL_AUTOMATION
  APP_NAME_PREFIX REPOS_DIR
  GUIDANCE_SYNC_APP_ID SETTINGS_ADMIN_APP_ID SETTINGS_READ_APP_ID
  EVAL_IMPROVE_APP_ID ROSTER_WRITER_APP_ID REGISTRY_READ_APP_ID
  ANTHROPIC_FEDERATION_RULE_ID ANTHROPIC_ORGANIZATION_ID
  ANTHROPIC_SERVICE_ACCOUNT_ID ANTHROPIC_WORKSPACE_ID
)

_valid_value() { # key value
  local k=$1 v=$2
  [ -z "$v" ] && return 0   # empty means "use the default"; required keys are checked after parsing
  case $k in
    OWNER|OWNER_TWO|REVIEWER) [[ $v =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,38})$ ]] ;;
    OWNER_TYPE) [[ $v == user || $v == org ]] ;;
    PLAN) [[ $v == free || $v == paid ]] ;;
    # No apostrophe: the name lands inside single-quoted shell and PowerShell strings.
    OWNER_NAME) [[ $v =~ ^[A-Za-z0-9\ .-]{0,80}$ ]] ;;
    VISIBILITY_*) [[ $v == public || $v == private ]] ;;
    APP_NAME_PREFIX) [[ $v =~ ^[a-z0-9][a-z0-9-]{0,19}$ ]] ;;
    REPOS_DIR) [[ $v =~ ^(~|/)[A-Za-z0-9._/~-]{0,200}$ ]] ;;
    *_APP_ID) [[ $v =~ ^[1-9][0-9]{0,11}$ ]] ;;
    ANTHROPIC_FEDERATION_RULE_ID) [[ $v =~ ^fdrl_[A-Za-z0-9]{8,64}$ ]] ;;
    ANTHROPIC_ORGANIZATION_ID) [[ $v =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] ;;
    ANTHROPIC_SERVICE_ACCOUNT_ID) [[ $v =~ ^svac_[A-Za-z0-9]{8,64}$ ]] ;;
    ANTHROPIC_WORKSPACE_ID) [[ $v =~ ^wrkspc_[A-Za-z0-9]{8,64}$ ]] ;;
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
    _valid_value "$key" "$val" || die "$path:$n: invalid value for $key$([ "$key" != OWNER_NAME ] || echo " (letters, digits, spaces, . and - only; no apostrophe, it ends a single-quoted string in the starter's scripts)")"
    printf -v "CFG_$key" '%s' "$val"
  done < "$path"
  [ -n "$CFG_OWNER" ] || die "$path: OWNER is required"
  [ -n "$CFG_PLAN" ] || die "$path: PLAN is required (free or paid)"
  : "${CFG_OWNER_TYPE:=user}" "${CFG_REVIEWER:=$CFG_OWNER}"
  # <your name> and "Example Adopter" land in public repos: default to the
  # public handle, never a legal name.
  : "${CFG_OWNER_NAME:=$CFG_OWNER}"
  : "${CFG_REPOS_DIR:=$HOME/repos}"
  CFG_REPOS_DIR=${CFG_REPOS_DIR/#\~/$HOME}
  if [ -z "$CFG_APP_NAME_PREFIX" ]; then
    CFG_APP_NAME_PREFIX=$(printf '%s' "$CFG_OWNER" | tr '[:upper:]' '[:lower:]' | cut -c1-20)
  fi
  CONFIG_PATH=$path
}

# owners: the owner, then the second owner when it is set and different.
owners() {
  printf '%s\n' "$CFG_OWNER"
  if [ -n "$CFG_OWNER_TWO" ] && [ "${CFG_OWNER_TWO,,}" != "${CFG_OWNER,,}" ]; then printf '%s\n' "$CFG_OWNER_TWO"; fi
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
  for p in "${ALWAYS_PUBLIC[@]}"; do [ "$p" = "$r" ] && { echo public; return; }; done
  if [ "$CFG_PLAN" = paid ]; then
    case $r in
      _agent-guidance) echo "${CFG_VISIBILITY_AGENT_GUIDANCE:-public}"; return ;;
      skills-evals) echo "${CFG_VISIBILITY_SKILLS_EVALS:-public}"; return ;;
      wsl-automation) echo "${CFG_VISIBILITY_WSL_AUTOMATION:-public}"; return ;;
    esac
  fi
  echo public
}

description_for() {
  case $1 in
    _agent-guidance) echo "Fleet guidance, the sync that delivers it, the agent-checks runner and the routine specs" ;;
    repo-settings) echo "Repository settings as code: the engine, fleet.yml and the fan-out workflow" ;;
    acsi-agentskills) echo "Reusable agent skills, packaged as a Claude Code plugin marketplace" ;;
    acsi-agentskills-private) echo "Private agent skills and the lessons mined from your sessions" ;;
    skills-evals) echo "Evals for the skills and guidance, and the loop that improves and retires them" ;;
    wsl-automation) echo "Scheduled jobs for your durable machine: usage census, waiting feed, session miner" ;;
    waiting-feed) echo "Private data branch for the waiting-on-you feed" ;;
  esac
}

# The README create-repos.sh writes. copy-starter.sh replaces a README.md only
# when it is still exactly these bytes; any edit makes it yours (--force).
GENERATED_README_MARKER='<!-- generated by bootstrap/create-repos.sh; copy-starter.sh replaces this file while it is unchanged -->'
generated_readme() { # name
  printf '# %s\n\n%s\n\n%s\n' "$1" "$(description_for "$1")" "$GENERATED_README_MARKER"
}

# The README on the orphan EVAL_RESULTS_BRANCH commit: what the branch holds.
eval_results_readme() {
  printf '%s\n' "# $EVAL_RESULTS_BRANCH" "" \
    "Measured results of the eval loop: ingested routine runs, usage counts, the coverage census and badges." \
    "The workflows write this branch and read it back; it never merges into main, and nothing here is edited by hand." \
    "Do not delete it: the weekly plan, the allowance guard and the dashboard read it." \
    "" "<!-- created by bootstrap/create-repos.sh as an orphan commit, so the workflows that read this branch find it on day one -->"
}

is_known_repo() { local r; for r in "${FLEET_REPOS[@]}" "${OPTIONAL_REPOS[@]}"; do [ "$r" = "$1" ] && return 0; done; return 1; }
is_fleet_repo() { local r; for r in "${FLEET_REPOS[@]}"; do [ "$r" = "$1" ] && return 0; done; return 1; }

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

# sha256_stdin: the hex sha256 of stdin. python3, not sha256sum, which macOS lacks.
sha256_stdin() { python3 -I -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'; }

require_tools() { local t; for t in "$@"; do command -v "$t" >/dev/null 2>&1 || die "$t is required"; done; }

# ------------------------------------------------------------------ registry
# Every GitHub Actions environment a kit workflow names, with its protection:
#   repo-settings  fleet-apply            the admin App's key; the owner approves
#                                         every apply; deploys from main only
#                                         (repo-settings README, "One-time setup" 5)
#   acsi-agentskills plugin-runtime-review  the owner approves plugin runtime
#                                         changes; no branch policy, because it
#                                         runs on pull_request_target (ADR 0016)
#   skills-evals   routine-fire           the eval routine's bearer and trigger
#                                         id; default branch only (routine-eval-fire.yml)
# Format: repo|environment|reviewer (owner|none)|branches (default|any)
ENVIRONMENTS=(
  "repo-settings|fleet-apply|owner|default"
  "acsi-agentskills|plugin-runtime-review|owner|any"
  "skills-evals|routine-fire|none|default"
)

# Labels a routine reads that no fleet.yml entry declares. The settings
# engine creates agent-authored (fleet.yml defaults) and eval-scaffold; the
# workflows create ci, needs-owner and hook-proposal on first use.
# Format: name|color|description
LABELS=(
  "agent-ready|0e8a16|An agent may fix this (the fleet worker's queue)."
  "on-hold|b60205|Paused by the owner. Only the owner adds or removes it."
)

# Every GitHub App the exported workflows mint a token from, named as the
# kit names them (APP_NAME_PREFIX-<app>), with the owner's permissions:
#   guidance-sync   _agent-guidance's agents-md-sync App (README, "Required
#                   secrets" a; widened for the Dependabot sweep, ADR 0014)
#   settings-admin  repo-settings' admin App (README, One-time setup 1-5)
#   settings-read   repo-settings' read-only App (One-time setup 6)
#   eval-improve    skills-evals' improve, draft and retirement PRs (ADR 0005)
#   roster          skills-evals' roster, scaffold and fixture PRs (ADR 0003);
#                   fleet.yml names its id as a bypass on roster/proposal
#   registry-read   skills-evals' read of acsi-agentskills-private (lessons, census)
# Format: app|permissions (JSON)|installed on ("fleet" or repos)|repo holding
# the credentials|client id kind:name|key environment (- = repository level)|
# key secret|config key|optional (yes|no)
APPS=(
  'guidance-sync|{"contents":"write","pull_requests":"write","metadata":"read","actions":"read","checks":"read","issues":"write"}|fleet|_agent-guidance|variable:APP_CLIENT_ID|-|APP_PRIVATE_KEY|GUIDANCE_SYNC_APP_ID|no'
  'settings-admin|{"administration":"write","actions":"write","issues":"write","metadata":"read"}|fleet|repo-settings|variable:REPO_SETTINGS_APP_CLIENT_ID|fleet-apply|REPO_SETTINGS_APP_PRIVATE_KEY|SETTINGS_ADMIN_APP_ID|no'
  'settings-read|{"administration":"read","actions":"read","issues":"read","metadata":"read"}|fleet|repo-settings|variable:REPO_SETTINGS_READ_APP_CLIENT_ID|-|REPO_SETTINGS_READ_APP_PRIVATE_KEY|SETTINGS_READ_APP_ID|no'
  'eval-improve|{"contents":"write","pull_requests":"write"}|acsi-agentskills _agent-guidance|skills-evals|secret:EVAL_IMPROVE_APP_CLIENT_ID|-|EVAL_IMPROVE_APP_PEM|EVAL_IMPROVE_APP_ID|no'
  'roster|{"contents":"write","pull_requests":"write"}|skills-evals|skills-evals|variable:ROSTER_APP_CLIENT_ID|-|ROSTER_APP_PRIVATE_KEY|ROSTER_WRITER_APP_ID|no'
  'registry-read|{"contents":"read"}|acsi-agentskills-private|skills-evals|variable:REGISTRY_READ_APP_CLIENT_ID|-|REGISTRY_READ_APP_PEM|REGISTRY_READ_APP_ID|no'
)

# The generated files regenerate.sh rebuilds (export/REGENERATE.md), in the
# order the quick start runs them; verify.sh's regen:<task> groups.
REGEN_TASKS=(skills-locks hook-pin run-health-sha codex-pin actions-lock)

# Secrets and variables that are not App credentials (set-secrets.sh).
# Format: repo|environment (- = repository level)|kind (secret|variable)|name|source
OTHER_VALUES=(
  "acsi-agentskills|-|secret|PRIVACY_DENYLIST|file"
  "skills-evals|routine-fire|secret|EVAL_ROUTINE_FIRE_BEARER|prompt"
  "skills-evals|routine-fire|variable|ROUTINE_TRIGGER_ID|prompt"
)
# The group name set-secrets.sh --only and verify.sh values:<group> take.
value_group() { case $1 in PRIVACY_DENYLIST) echo privacy ;; *) echo routine-fire ;; esac; }

# Repository variables (repo level, not an environment's) that the kit's
# workflows read and no App step sets. create-environments.sh sets them;
# verify.sh compares them. repo-settings.yml maps both into the engine's
# environment, and fleet.yml names them as "${NAME}" bypass actors
# (DECISIONS C12): the engine refuses a plan while either is unset.
# Format: repo|NAME|cfg:<fleet.env key>; empty in fleet.env leaves it alone.
# repo|name|source|verify group. The `federation` group is eval.yml's
# Anthropic workload identity federation set-up (docs/evals-and-loop.md,
# "Workload identity federation"): optional, so verify.sh checks it only when
# named (--only federation), and an empty fleet.env key is skipped.
REPO_VARIABLES=(
  "repo-settings|GUIDANCE_SYNC_APP_ID|cfg:GUIDANCE_SYNC_APP_ID|variables"
  "repo-settings|ROSTER_WRITER_APP_ID|cfg:ROSTER_WRITER_APP_ID|variables"
  "skills-evals|ANTHROPIC_FEDERATION_RULE_ID|cfg:ANTHROPIC_FEDERATION_RULE_ID|federation"
  "skills-evals|ANTHROPIC_ORGANIZATION_ID|cfg:ANTHROPIC_ORGANIZATION_ID|federation"
  "skills-evals|ANTHROPIC_SERVICE_ACCOUNT_ID|cfg:ANTHROPIC_SERVICE_ACCOUNT_ID|federation"
  "skills-evals|ANTHROPIC_WORKSPACE_ID|cfg:ANTHROPIC_WORKSPACE_ID|federation"
)

# repo_variable_value <source>: the value a REPO_VARIABLES source stands for.
repo_variable_value() {
  case $1 in
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

# value_names <secret|variable> <owner/repo> <env|->: set NAMES_OUT to the
# names set in an environment, or at repository level for "-", one per line
# (names only: gh never lists secret values, and variable values are not
# asked for). Called directly, not in $( ), so GH_HTTP survives; returns 1
# with GH_HTTP set when unreadable.
value_names() {
  local out err="$BOOT_WORK/err" scope=()
  NAMES_OUT=""
  [ "$3" = - ] || scope=(--env "$3")
  if out=$("$GH" "$1" list "${scope[@]}" --repo "$2" --json name 2>"$err"); then
    GH_HTTP=200; NAMES_OUT=$(printf '%s' "$out" | jq -r '.[].name'); return 0
  fi
  GH_HTTP=$(_status_from "$err"); GH_HTTP=${GH_HTTP:-000}; return 1
}

# set_value <secret|variable> <owner/repo> <env|-> <name>: store the value on
# this function's stdin, at repository level for "-".
set_value() {
  local scope=()
  [ "$3" = - ] || scope=(--env "$3")
  run_write_stdin "$GH" "$1" set "$4" "${scope[@]}" --repo "$2"
}

# ------------------------------------------------------------------ git
# Remote clones go through GIT_BASE (tests point it at local bare repos).
GIT_BASE=${BOOTSTRAP_GIT_BASE:-${COPY_STARTER_GIT_BASE:-https://github.com}}

# clone_repo <owner/repo> <dir> [--depth N]: a clone of the default branch.
clone_repo() {
  local repo=$1 dir=$2; shift 2
  git clone --quiet "$@" "$GIT_BASE/$repo.git" "$dir" 2>"$BOOT_WORK/err" || die "could not clone $repo"
}
