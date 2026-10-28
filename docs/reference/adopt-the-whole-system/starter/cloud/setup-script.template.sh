#!/bin/bash
# Cloud environment setup script. Runs once, as root, before Claude Code.
# A setup script that exits non-zero stops sessions from starting, so every
# step is allowed to fail and the script always exits 0.
project=/home/user
mkdir -p "$project/.claude" || true

# The eval harness's sandbox needs bubblewrap and socat (phase 7).
apt-get update -q >/dev/null 2>&1 && apt-get install -y -q bubblewrap socat >/dev/null 2>&1 \
  || echo "setup: bubblewrap/socat install failed; sandboxed evals will refuse to run"

# Hooks for multi-repo sessions: run the first attached repo's copy of each.
cat > "$project/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "SessionStart": [
      { "matcher": "startup|resume|fork",
        "hooks": [ { "type": "command", "timeout": 30,
          "command": "bash -c 'for h in \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/fleet-memory.sh \"$CLAUDE_PROJECT_DIR\"/*/.claude/hooks/fleet-memory.sh; do [ -f \"$h\" ] && exec bash \"$h\"; done; exit 0'" } ] },
      { "matcher": "startup|resume|fork",
        "hooks": [ { "type": "command", "timeout": 90,
          "command": "bash -c 'for h in \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/skills-bootstrap.sh \"$CLAUDE_PROJECT_DIR\"/*/.claude/hooks/skills-bootstrap.sh; do [ -f \"$h\" ] && exec bash \"$h\"; done; exit 0'" } ] }
    ],
    "PreToolUse": [
      { "matcher": "Bash|Edit|Write|MultiEdit",
        "hooks": [ { "type": "command", "timeout": 10,
          "command": "bash -c 'for h in \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/agent-checks-guard.sh \"$CLAUDE_PROJECT_DIR\"/*/.claude/hooks/agent-checks-guard.sh; do [ -f \"$h\" ] && exec bash \"$h\"; done; exit 0'" } ] }
    ]
  },
  "disableSkillShellExecution": true
}
JSON

# Config-based git hooks need git 2.54+; the image ships an older git.
if ! git version | awk '{split($3,v,"."); exit !(v[1]>2 || (v[1]==2 && v[2]>=54))}'; then
  apt-get install -y -q build-essential libcurl4-openssl-dev libssl-dev zlib1g-dev >/dev/null 2>&1 || true
  src=$(mktemp -d)
  if git clone -q --depth 1 --branch v2.56.0 https://github.com/git/git "$src/git" \
     && make -C "$src/git" -j"$(nproc)" prefix=/usr/local sysconfdir=/etc \
          NO_GETTEXT=1 NO_TCLTK=1 NO_PERL=1 NO_PYTHON=1 NO_EXPAT=1 install >/dev/null 2>&1; then
    echo "setup: $(/usr/local/bin/git version)"
  else
    echo "setup: git build failed; local hooks will not run, CI still checks" >&2
  fi
  rm -rf "$src"
fi

# The hook include from section 6.2, system-wide (the session's ~/.gitconfig
# is managed by the harness). Same script as 6.2; keep the two in step.
cat > /usr/local/sbin/install-agent-hooks.sh <<'INSTALL'
@INSTALL_AGENT_HOOKS@
INSTALL
bash /usr/local/sbin/install-agent-hooks.sh --system || true
exit 0
