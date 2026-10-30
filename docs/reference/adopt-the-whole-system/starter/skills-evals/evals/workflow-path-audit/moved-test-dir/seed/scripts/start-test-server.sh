#!/usr/bin/env bash
# Start the service for the end-to-end suite on the port the specs expect.
set -euo pipefail

export PORT=8080
exec node src/index.js
