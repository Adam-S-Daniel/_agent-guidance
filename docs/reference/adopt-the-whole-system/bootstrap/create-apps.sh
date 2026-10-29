#!/usr/bin/env bash
# create-apps.sh [--config fleet.env] [--dry-run] [--out DIR] [--with-optional] [<app>...]
#
# Creates the GitHub Apps in the guide's 3.4 table through the App manifest
# flow (an App cannot be created by API alone):
#
#   guidance-sync   Contents, Pull requests, Workflows: write  -> env guidance-sync in agent-guidance
#   settings-admin  Administration: write; Metadata: read      -> env settings-admin in repo-settings
#   loop-writer     Contents, Pull requests, Issues: write     -> env loop-writer in skills-evals
#   lessons-reader  Contents: read                             -> env lessons-reader in skills-evals
#   roster-writer   Contents, Pull requests: write (optional, --with-optional) -> env roster-writer
#
# For each App it writes DIR/<app>.manifest.json and DIR/<app>.html (default
# DIR: ./app-manifests). Open the HTML file in a browser signed in as the
# owner; it posts the manifest to GitHub's "new App" page. Click Create, and
# GitHub redirects to https://example.com/?code=...&state=... Paste that URL
# (or just the code) at the prompt. The script then exchanges the code
# (gh api -X POST app-manifests/<code>/conversions), stores the client ID as
# the environment variable and the private key as the environment secret
# (both on stdin to gh), records the numeric App ID in fleet.env, and prints
# the install URL and which repos to pick.
#
# The private key and client secret are never printed, logged or written to
# disk: they live in shell variables and reach gh on a pipe from printf.
#
# Idempotent: an App whose ID is already in fleet.env is skipped. PLAN=free
# skips settings-admin (its environment would be in a private repo; the guide
# says run the apply script from your machine instead). Run
# create-environments.sh first. Needs: gh, jq, python3.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

OUT=./app-manifests WITH_OPTIONAL=0 WANT=()
parse_common_args "$@"
set -- "${REST_ARGS[@]}"
while [ $# -gt 0 ]; do
  case $1 in
    --out) [ $# -ge 2 ] || die "--out needs a directory"; OUT=$2; shift 2 ;;
    --with-optional) WITH_OPTIONAL=1; shift ;;
    -*) die "unknown option: $1" ;;
    *) WANT+=("$1"); shift ;;
  esac
done
require_tools jq python3 "$GH"
load_config "$CONFIG"
O=$CFG_OWNER

wanted() { # app optional
  if [ ${#WANT[@]} -gt 0 ]; then local w; for w in "${WANT[@]}"; do [ "$w" = "$1" ] && return 0; done; return 1; fi
  [ "$2" = no ] || [ "$WITH_OPTIONAL" = 1 ]
}
for w in "${WANT[@]}"; do
  found=0; for e in "${APPS[@]}"; do [ "${e%%|*}" = "$w" ] && found=1; done
  [ "$found" = 1 ] || die "unknown App: $w"
done

if [ "$CFG_OWNER_TYPE" = org ]; then
  NEW_APP_URL="https://github.com/organizations/$O/settings/apps/new"
else
  NEW_APP_URL="https://github.com/settings/apps/new"
fi

write_manifest() { # app perms name state -> files in OUT
  local app=$1 perms=$2 name=$3 state=$4
  mkdir -p "$OUT"
  jq -n --arg name "$name" --arg owner "$O" --argjson perms "$perms" '{
      name: $name,
      url: ("https://github.com/" + $owner),
      hook_attributes: {url: "https://example.com/", active: false},
      redirect_url: "https://example.com/",
      public: false,
      default_permissions: $perms,
      default_events: []
    }' > "$OUT/$app.manifest.json"
  python3 -I - "$OUT/$app.manifest.json" "$OUT/$app.html" "$NEW_APP_URL" "$state" "$name" <<'PY'
import html, json, sys
src, dst, action, state, name = sys.argv[1:6]
manifest = json.dumps(json.load(open(src, encoding="utf-8")), separators=(",", ":"))
page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Create {html.escape(name)}</title></head>
<body>
<p>Sending the manifest for <code>{html.escape(name)}</code> to GitHub. If nothing happens, press the button.</p>
<form id="f" method="post" action="{html.escape(action + '?state=' + state, quote=True)}">
<input type="hidden" name="manifest" value="{html.escape(manifest, quote=True)}">
<button type="submit">Create the App on GitHub</button>
</form>
<script>document.getElementById("f").submit();</script>
</body></html>
"""
open(dst, "w", encoding="utf-8").write(page)
PY
}

# read_code <state>: prompt for the redirect URL or the bare code; set CODE.
read_code() {
  local state=$1 answer got_state=""
  printf 'Paste the URL GitHub redirected to (https://example.com/?code=...), or the code: ' >&2
  IFS= read -r answer || die "no code given"
  [ -t 0 ] || printf '\n' >&2
  answer=${answer//[[:space:]]/}
  if [[ $answer =~ [?\&]code=([A-Za-z0-9_-]+) ]]; then
    CODE=${BASH_REMATCH[1]}
    [[ $answer =~ [?\&]state=([A-Za-z0-9]+) ]] && got_state=${BASH_REMATCH[1]}
    [ -z "$got_state" ] || [ "$got_state" = "$state" ] \
      || die "the state in that URL does not match this run's manifest; open the HTML file this run wrote"
  else
    CODE=$answer
  fi
  [[ $CODE =~ ^[A-Za-z0-9_-]{1,100}$ ]] || die "that does not look like a manifest code"
}

create_one() { # app perms installed env_repo env var secret key
  local app=$1 perms=$2 installed=$3 env_repo=$4 env=$5 var=$6 sec=$7 key=$8
  local name state id slug client_id pem cfgval
  cfgval=CFG_$key
  if [ -n "${!cfgval}" ]; then
    skip "$app already created (App ID ${!cfgval} in fleet.env)"
    if env_names secret "$O/$env_repo" "$env" && ! grep -qxF -- "$sec" <<<"$ENV_NAMES_OUT"; then
      warn "$sec is missing from $O/$env_repo environment $env: generate a private key on the App's settings page and run: gh secret set $sec --env $env --repo $O/$env_repo < <downloaded .pem>, then delete the download"
    fi
    return 0
  fi
  if ! env_available "$env_repo"; then
    log "skip: $app: its environment would be in the private repo $env_repo and PLAN=free."
    log "      The guide (3.4): run repo-settings' apply script (apply-rulesets.sh) from your own machine with gh instead; this App is not needed."
    return 0
  fi
  if ! gh_read "repos/$O/$env_repo/environments/$env"; then
    if [ "$DRY_RUN" = 1 ]; then log "note: environment $env in $O/$env_repo does not exist yet (run create-environments.sh)"
    else die "environment $env in $O/$env_repo does not exist (HTTP $GH_HTTP): run create-environments.sh first"; fi
  fi
  name="$CFG_APP_NAME_PREFIX-$app"
  [ ${#name} -le 34 ] || die "App name $name is longer than GitHub's 34 characters; set a shorter APP_NAME_PREFIX"
  state=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
  write_manifest "$app" "$perms" "$name" "$state"
  log "== $app ($name)"
  log "Open this file in a browser signed in to GitHub as $O, and click Create:"
  log "  file://$(cd "$OUT" && pwd)/$app.html"
  if [ "$DRY_RUN" = 1 ]; then
    WRITES=$((WRITES + 3))
    printf 'DRY-RUN: %s api -X POST app-manifests/<code>/conversions\n' "$GH"
    printf 'DRY-RUN: %s variable set %s --env %s --repo %s   (client ID on stdin)\n' "$GH" "$var" "$env" "$O/$env_repo"
    printf 'DRY-RUN: %s secret set %s --env %s --repo %s   (private key on stdin, never shown)\n' "$GH" "$sec" "$env" "$O/$env_repo"
    printf 'DRY-RUN: record %s=<App ID> in %s\n' "$key" "$CONFIG_PATH"
    return 0
  fi
  read_code "$state"
  gh_write_api POST "app-manifests/$CODE/conversions"
  id=$(printf '%s' "$GH_OUT" | jq -r '.id // empty')
  slug=$(printf '%s' "$GH_OUT" | jq -r '.slug // empty')
  client_id=$(printf '%s' "$GH_OUT" | jq -r '.client_id // empty')
  pem=$(printf '%s' "$GH_OUT" | jq -r '.pem // empty')
  GH_OUT=""   # drop the response: it holds the client secret and the key
  [[ $id =~ ^[1-9][0-9]{0,11}$ ]] || die "the conversion response has no App id"
  [[ $slug =~ ^[a-z0-9][a-z0-9-]{0,60}$ ]] || die "the conversion response has no valid slug"
  [[ $client_id =~ ^[A-Za-z0-9.]{1,64}$ ]] || die "the conversion response has no valid client ID"
  [[ $pem == -----BEGIN* ]] || die "the conversion response has no private key"
  # Record the ID first: if a later step fails, a re-run will not make a second App.
  set_config_value "$key" "$id"
  log "created App $slug (ID $id); recorded $key in $CONFIG_PATH"
  WRITES=$((WRITES + 2))
  printf '%s' "$client_id" | run_write_stdin "$GH" variable set "$var" --env "$env" --repo "$O/$env_repo"
  log "stored $var (variable) in $O/$env_repo environment $env"
  printf '%s' "$pem" | run_write_stdin "$GH" secret set "$sec" --env "$env" --repo "$O/$env_repo"
  pem=""
  log "stored $sec (secret) in $O/$env_repo environment $env"
  log "Install it: https://github.com/apps/$slug/installations/new"
  if [ "$installed" = fleet ]; then
    log "  Pick: Only select repositories: ${FLEET_REPOS[*]}, plus every consumer repo in agent-guidance's repos.yml."
  else
    log "  Pick: Only select repositories: $installed"
  fi
}

log "Owner $O, plan $CFG_PLAN$([ "$DRY_RUN" = 1 ] && echo ', DRY RUN')"
for entry in "${APPS[@]}"; do
  IFS='|' read -r app perms installed env_repo env var sec key optional <<<"$entry"
  wanted "$app" "$optional" || { log "skip: $app is optional (pass --with-optional or name it)"; continue; }
  create_one "$app" "$perms" "$installed" "$env_repo" "$env" "$var" "$sec" "$key"
done
finish
