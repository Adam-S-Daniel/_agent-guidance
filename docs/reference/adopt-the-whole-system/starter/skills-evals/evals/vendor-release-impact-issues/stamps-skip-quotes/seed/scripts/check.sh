#!/usr/bin/env bash
# Pre-push gate: run ExampleCLI's checks and explain a configuration error.
set -uo pipefail
example-cli check --config .example-cli/settings.json
status=$?
if [ "$status" -eq 1 ]; then
  echo "check.sh: .example-cli/settings.json has a configuration error" >&2
fi
exit "$status"
