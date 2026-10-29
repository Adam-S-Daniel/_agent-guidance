#!/usr/bin/env bash
# install-agent-hooks.sh [--system]: once per machine. Registers config-based
# git hooks (git 2.54+) that run the agent-checks runner, only in clones of
# your own repos. The hook runs the runner as it is on origin's default branch
# (origin/HEAD, else origin/main), never the working tree, so checking out
# someone else's pull request cannot make your next commit run their code.
set -u
owner="<you>"                     # your GitHub owner, as in your clone URLs
lc=$(printf '%s' "$owner" | tr '[:upper:]' '[:lower:]')
scope=--global; [ "${1:-}" = --system ] && scope=--system
inc="$HOME/.config/git/agent-hooks.inc"
[ "$scope" = --system ] && inc=/etc/gitconfig.d/agent-hooks.inc
mkdir -p "$(dirname "$inc")"
rm -f "$inc"
# The include fires when ANY remote is yours; the hook then also checks that
# origin, whose runner it would run, is yours (a fork clone has origin = fork).
# It reads the configured URL, as includeIf does, not the insteadOf rewrite.
mine="https://github.com/$owner/*|git@github.com:$owner/*|ssh://git@github.com/$owner/*"
[ "$lc" = "$owner" ] || mine="$mine|https://github.com/$lc/*|git@github.com:$lc/*|ssh://git@github.com/$lc/*"
for event in pre-commit pre-push; do
  git config --file "$inc" "hook.agent-checks-$event.event" "$event"
  git config --file "$inc" "hook.agent-checks-$event.command" \
    "case \$(git config --get remote.origin.url) in $mine) ;; *) exit 0 ;; esac; for r in origin/HEAD origin/main; do src=\$(git show \"\$r:.githooks/agent-checks\" 2>/dev/null) && AGENT_CHECKS_REF=\$r exec bash -c \"\$src\" agent-checks $event; done; exit 0"
done
for prefix in https://github.com/ git@github.com: ssh://git@github.com/; do
 for o in $(printf '%s\n' "$owner" "$lc" | sort -u); do
  key="includeIf.hasconfig:remote.*.url:${prefix}${o}/**.path"
  have=$(git config "$scope" --get-all "$key" 2>/dev/null) || have=""
  grep -qxF -- "$inc" <<<"$have" || git config "$scope" --add "$key" "$inc"
 done
done
git version | awk '{split($3,v,"."); if (!(v[1]>2 || (v[1]==2 && v[2]>=54))) print "WARNING: " $0 " ignores config-based hooks; CI still checks"}'
