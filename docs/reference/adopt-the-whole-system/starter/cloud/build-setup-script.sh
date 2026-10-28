#!/usr/bin/env bash
# build-setup-script.sh (--owner OWNER | --config fleet.env) [--out FILE] [<install-agent-hooks.sh>]
#
# Writes the cloud environment's setup script (guide 7.3) from
# setup-script.template.sh, with the hook installer (guide 6.2) embedded at
# the @INSTALL_AGENT_HOOKS@ line and its <you> filled with your owner, so the
# two never drift. The owner comes from --owner, or from the OWNER= line of
# fleet.env with --config; it must be a plain GitHub login
# (^[A-Za-z0-9-]{1,39}$), and the placeholder <you> itself is refused. The
# owner is filled in the OUTPUT only: the template and the installer keep
# <you>, so the kit's tests and later builds read them unchanged.
#
# Default installer: ../machine/install-agent-hooks.sh. Default output:
# cloud-setup-script.sh next to this script (generated; not part of the kit).
# Run it after editing either input, then paste the output into each cloud
# environment. Exit 0 written, 2 bad usage or a refused owner.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
owner="" config="" out="$here/cloud-setup-script.sh" installer="$here/../machine/install-agent-hooks.sh"
usage() { echo "usage: build-setup-script.sh (--owner OWNER | --config fleet.env) [--out FILE] [<install-agent-hooks.sh>]" >&2; exit 2; }
seen_installer=0
while [ $# -gt 0 ]; do
  case $1 in
    --owner) [ $# -ge 2 ] || usage; owner=$2; shift 2 ;;
    --config) [ $# -ge 2 ] || usage; config=$2; shift 2 ;;
    --out) [ $# -ge 2 ] || usage; out=$2; shift 2 ;;
    -*) usage ;;
    *) [ "$seen_installer" = 0 ] || usage; installer=$1; seen_installer=1; shift ;;
  esac
done
if [ -n "$config" ]; then
  [ -z "$owner" ] || { echo "build-setup-script: give --owner or --config, not both" >&2; exit 2; }
  [ -f "$config" ] || { echo "build-setup-script: no config file $config" >&2; exit 2; }
  owner=$(sed -n 's/^OWNER=//p' "$config" | tail -n 1)
fi
[ -n "$owner" ] || { echo "build-setup-script: give --owner OWNER or --config fleet.env (its OWNER= line)" >&2; exit 2; }
if [ "$owner" = "<you>" ] || [[ ! $owner =~ ^[A-Za-z0-9-]{1,39}$ ]]; then
  echo "build-setup-script: the owner must be your GitHub login (^[A-Za-z0-9-]{1,39}\$), not the placeholder" >&2; exit 2
fi
python3 -I - "$here/setup-script.template.sh" "$installer" "$out" "$owner" <<'PY'
import sys
template, installer, out, owner = sys.argv[1:]
t = open(template, encoding="utf-8", newline="").read()
body = open(installer, encoding="utf-8", newline="").read()
slot = "@INSTALL_AGENT_HOOKS@\n"
if t.count(slot) != 1:
    sys.exit("build-setup-script: the template must hold the slot line exactly once")
if "\r" in body or not body.endswith("\n"):
    sys.exit("build-setup-script: the installer must use LF line endings and end with a newline")
if "<you>" not in body:
    sys.exit("build-setup-script: the installer has no <you> to fill (pass the kit's unedited install-agent-hooks.sh)")
body = body.replace("<you>", owner)
if "INSTALL" in body.splitlines():
    sys.exit("build-setup-script: the installer holds a line 'INSTALL', which would end the heredoc")
with open(out, "w", encoding="utf-8", newline="") as f:
    f.write(t.replace(slot, body))
PY
chmod 755 "$out"
echo "wrote $out (owner $owner)"
