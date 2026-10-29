#!/usr/bin/env bash
# create-apps.sh [--config fleet.env] [--dry-run] [--out DIR] [<app>...]
#
# Creates the six GitHub Apps the exported workflows mint tokens from (APPS
# in lib/common.sh), with the permissions the owner's own Apps have, through
# GitHub's App manifest flow (an App cannot be created by API alone):
#
#   guidance-sync   contents, pull requests, issues: write; actions, checks,
#                   metadata: read. _agent-guidance's sync, Dependabot sweep
#                   and fleet reads. Variable APP_CLIENT_ID, secret
#                   APP_PRIVATE_KEY, in _agent-guidance. Installed fleet-wide.
#   settings-admin  administration, actions, issues: write; metadata: read.
#                   repo-settings' apply. Variable REPO_SETTINGS_APP_CLIENT_ID
#                   (repository), secret REPO_SETTINGS_APP_PRIVATE_KEY in the
#                   fleet-apply environment. Installed fleet-wide.
#   settings-read   administration, actions, issues, metadata: read. The drift
#                   check. Variable REPO_SETTINGS_READ_APP_CLIENT_ID, secret
#                   REPO_SETTINGS_READ_APP_PRIVATE_KEY. Installed fleet-wide.
#   eval-improve    contents, pull requests: write, on acsi-agentskills and
#                   _agent-guidance. Secrets EVAL_IMPROVE_APP_CLIENT_ID and
#                   EVAL_IMPROVE_APP_PEM in skills-evals (both secrets: the
#                   gates check that both exist before minting).
#   roster          contents, pull requests: write, on skills-evals. Variable
#                   ROSTER_APP_CLIENT_ID, secret ROSTER_APP_PRIVATE_KEY. Its
#                   App id is ROSTER_WRITER_APP_ID, a ruleset bypass actor.
#   registry-read   contents: read, on acsi-agentskills-private. Variable
#                   REGISTRY_READ_APP_CLIENT_ID, secret REGISTRY_READ_APP_PEM.
#
# Default: all six; name some to create only those.
#
# For each App it writes DIR/<app>.manifest.json and DIR/<app>.html (default
# DIR: ./app-manifests). Open the HTML file in a browser signed in as the
# owner; it posts the manifest to GitHub's "new App" page. Click Create, and
# GitHub redirects to http://127.0.0.1/?code=...&state=... Nothing listens
# there, so the browser shows a connection error: copy the URL from the
# address bar and paste it (or just the code) at the prompt. The redirect goes
# to this machine, never to a third-party site, because the one-time code can
# be converted into the App's private key. The script exchanges the code
# (gh api -X POST app-manifests/<code>/conversions), records the numeric App
# id in fleet.env first (so a rerun never makes a second App), stores the
# client id and the private key where the workflows read them (both on stdin
# to gh), and prints the install link and which repos to pick. Installing is
# a browser step; verify.sh apps:<app> checks it.
#
# The private key and client secret are never printed, logged or written to
# disk: they live in shell variables and reach gh on a pipe.
#
# With OWNER_TWO set, the three fleet-wide Apps are made public, because
# GitHub lets an account install another account's App only when it is
# public; anyone can then see its name, not use it (it acts only where it is
# installed, and only its owner installs it on that owner's repos).
#
# Idempotent: an App whose id is already in fleet.env is not created again;
# its credentials are checked by name and a missing one is reported with the
# command that fixes it. PLAN=free skips settings-admin (its key would be in
# the fleet-apply environment of the private repo-settings): apply fleet.yml
# from your machine with apply-settings.sh. Run create-environments.sh first.
# Needs: gh, jq, python3.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

OUT=./app-manifests WANT=()
parse_common_args "$@"
set -- "${REST_ARGS[@]}"
while [ $# -gt 0 ]; do
  case $1 in
    --out) [ $# -ge 2 ] || die "--out needs a directory"; OUT=$2; shift 2 ;;
    -*) die "unknown option: $1" ;;
    *) WANT+=("$1"); shift ;;
  esac
done
require_tools jq python3 "$GH"
load_config "$CONFIG"
O=$CFG_OWNER
TWO_OWNERS=0
[ "$(owners | wc -l)" -lt 2 ] || TWO_OWNERS=1

wanted() { # app
  [ ${#WANT[@]} = 0 ] && return 0
  local w; for w in "${WANT[@]}"; do [ "$w" = "$1" ] && return 0; done; return 1
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

write_manifest() { # app perms name state public
  local app=$1 perms=$2 name=$3 state=$4 public=$5
  mkdir -p "$OUT"
  jq -n --arg name "$name" --arg owner "$O" --argjson perms "$perms" --argjson pub "$public" '{
      name: $name,
      url: ("https://github.com/" + $owner),
      hook_attributes: {url: "https://example.com/", active: false},
      redirect_url: "http://127.0.0.1/",
      public: $pub,
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
  printf 'Paste the address-bar URL GitHub redirected to (http://127.0.0.1/?code=..., the page itself fails to load), or the code: ' >&2
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

# where <env|->: " in environment E" or " (repository level)".
where() { if [ "$1" = - ]; then printf ' (repository level)'; else printf ' in environment %s' "$1"; fi; }
env_flag() { [ "$1" = - ] || printf -- '--env %s ' "$1"; }

install_hint() { # slug installed
  log "Install it: https://github.com/apps/$1/installations/new"
  if [ "$2" = fleet ]; then
    log "  Pick: Only select repositories: every repo fleet.yml in repo-settings lists for $O"
    log "  (the six kit repos, plus each consumer in _agent-guidance's repos.yml)."
    [ "$TWO_OWNERS" = 0 ] || log "  Then sign in as $CFG_OWNER_TWO and install it there too, on that owner's fleet repos."
  else
    log "  Pick: Only select repositories: $2"
  fi
}

check_existing() { # app repo id-kind:id-name key-env key-secret
  local r=$2 idk=${3%%:*} idn=${3#*:} env=$4 sec=$5
  if value_names "$idk" "$O/$r" -; then
    grep -qxF -- "$idn" <<<"$NAMES_OUT" || warn "$idk $idn is missing from $O/$r: copy the Client ID from the App's settings page and run: $GH $idk set $idn --repo $O/$r"
  fi
  if value_names secret "$O/$r" "$env"; then
    grep -qxF -- "$sec" <<<"$NAMES_OUT" || warn "$sec is missing from $O/$r$(where "$env"): generate a private key on the App's settings page and run: $GH secret set $sec $(env_flag "$env")--repo $O/$r < <downloaded .pem>, then delete the download"
  fi
}

create_one() { # app perms installed repo id-kind:id-name key-env key-secret cfg-key
  local app=$1 perms=$2 installed=$3 r=$4 idspec=$5 env=$6 sec=$7 key=$8
  local idk=${5%%:*} idn=${5#*:} name state id slug client_id pem cfgval public=false
  cfgval=CFG_$key
  if [ -n "${!cfgval}" ]; then
    skip "$app already created (App id ${!cfgval} in fleet.env)"
    check_existing "$app" "$r" "$idspec" "$env" "$sec"
    return 0
  fi
  if [ "$env" != - ] && ! env_available "$r"; then
    log "skip: $app: its key would be in environment $env of the private repo $r, and PLAN=free has none there."
    log "      Apply fleet.yml from your machine with apply-settings.sh instead; this App is not needed."
    return 0
  fi
  if [ "$env" != - ] && ! gh_read "repos/$O/$r/environments/$env"; then
    if [ "$DRY_RUN" = 1 ]; then log "note: environment $env in $O/$r does not exist yet (run create-environments.sh)"
    else die "environment $env in $O/$r does not exist (HTTP $GH_HTTP): run create-environments.sh first"; fi
  fi
  name="$CFG_APP_NAME_PREFIX-$app"
  [ ${#name} -le 34 ] || die "App name $name is longer than GitHub's 34 characters; set a shorter APP_NAME_PREFIX"
  [ "$installed" != fleet ] || [ "$TWO_OWNERS" = 0 ] || public=true
  log "== $app ($name$([ "$public" = true ] && echo ', public so both owners can install it'))"
  if [ "$DRY_RUN" = 1 ]; then
    # A dry run writes nothing, the manifest files included.
    WRITES=$((WRITES + 4))
    printf 'DRY-RUN: write %s/%s.manifest.json and %s/%s.html, to open in a browser signed in as %s\n' "$OUT" "$app" "$OUT" "$app" "$O"
    printf 'DRY-RUN: %s api -X POST app-manifests/<code>/conversions\n' "$GH"
    printf 'DRY-RUN: record %s=<App id> in %s\n' "$key" "$CONFIG_PATH"
    printf 'DRY-RUN: %s %s set %s --repo %s   (client id on stdin)\n' "$GH" "$idk" "$idn" "$O/$r"
    printf 'DRY-RUN: %s secret set %s %s--repo %s   (private key on stdin, never shown)\n' "$GH" "$sec" "$(env_flag "$env")" "$O/$r"
    return 0
  fi
  state=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
  write_manifest "$app" "$perms" "$name" "$state" "$public"
  log "Open this file in a browser signed in to GitHub as $O, and click Create:"
  log "  file://$(cd "$OUT" && pwd)/$app.html"
  read_code "$state"
  gh_write_api POST "app-manifests/$CODE/conversions"
  id=$(printf '%s' "$GH_OUT" | jq -r '.id // empty')
  slug=$(printf '%s' "$GH_OUT" | jq -r '.slug // empty')
  client_id=$(printf '%s' "$GH_OUT" | jq -r '.client_id // empty')
  pem=$(printf '%s' "$GH_OUT" | jq -r '.pem // empty')
  GH_OUT=""   # drop the response: it holds the client secret and the key
  [[ $id =~ ^[1-9][0-9]{0,11}$ ]] || die "the conversion response has no App id"
  [[ $slug =~ ^[a-z0-9][a-z0-9-]{0,60}$ ]] || die "the conversion response has no valid slug"
  [[ $client_id =~ ^[A-Za-z0-9.]{1,64}$ ]] || die "the conversion response has no valid client id"
  [[ $pem == -----BEGIN* ]] || die "the conversion response has no private key"
  # Record the id first: if a later step fails, a rerun will not make a second App.
  set_config_value "$key" "$id"
  log "created App $slug (id $id); recorded $key in $CONFIG_PATH"
  set_value "$idk" "$O/$r" - "$idn" < <(printf '%s' "$client_id")
  log "stored $idn ($idk) in $O/$r"
  set_value secret "$O/$r" "$env" "$sec" < <(printf '%s' "$pem")
  pem=""
  log "stored $sec (secret) in $O/$r$(where "$env")"
  install_hint "$slug" "$installed"
}

log "Owner $O, plan $CFG_PLAN$([ "$DRY_RUN" = 1 ] && echo ', DRY RUN')"
for entry in "${APPS[@]}"; do
  IFS='|' read -r app perms installed r idspec env sec key _optional <<<"$entry"
  wanted "$app" || continue
  create_one "$app" "$perms" "$installed" "$r" "$idspec" "$env" "$sec" "$key"
done
log "Then run create-environments.sh again: it copies GUIDANCE_SYNC_APP_ID and ROSTER_WRITER_APP_ID into repo-settings' variables."
finish
