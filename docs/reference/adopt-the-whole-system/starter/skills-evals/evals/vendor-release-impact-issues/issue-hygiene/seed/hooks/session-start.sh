#!/usr/bin/env bash
# Runs on every SessionStart event matched by .example-cli/settings.json's
# "startup|resume" matcher. Logs the session source so the team can audit
# which sessions start cold vs. resume a previous one.
set -euo pipefail
echo "session-start: source=${EXAMPLE_CLI_SOURCE:-unknown}" >> .example-cli/session-start.log
