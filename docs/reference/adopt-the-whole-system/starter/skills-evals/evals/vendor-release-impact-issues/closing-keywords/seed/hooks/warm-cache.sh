#!/usr/bin/env bash
# Warms the dependency cache at session start. A cold cache takes about a
# minute to fill, longer than the 20-second timeout in
# .example-cli/settings.json; ExampleCLI has never enforced that timeout.
set -euo pipefail
npm ci --prefer-offline --no-audit >/dev/null
