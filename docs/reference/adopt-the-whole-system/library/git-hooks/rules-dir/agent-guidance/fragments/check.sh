    case "$path" in
      .claude/rules/*)
        problem rules-dir "$path: path-scoped rules (.claude/rules/) are not used in this fleet. Only Claude Code reads them. Put the rule in AGENTS.md or a skill, or make it a check in .githooks/agent-checks." ;;
    esac
