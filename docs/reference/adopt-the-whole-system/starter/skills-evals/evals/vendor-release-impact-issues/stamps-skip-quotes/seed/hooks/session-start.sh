#!/usr/bin/env bash
# Runs on every SessionStart event matched by .example-cli/settings.json's
# "startup|resume" matcher and logs where the session came from.
set -euo pipefail
echo "session-start: source=${EXAMPLE_CLI_SOURCE:-unknown}" >> .example-cli/session-start.log
