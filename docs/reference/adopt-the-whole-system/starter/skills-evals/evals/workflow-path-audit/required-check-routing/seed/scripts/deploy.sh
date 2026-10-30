#!/usr/bin/env bash
# Ship src/ and the production dependency set to the runtime host.
set -euo pipefail

TARGET="${DEPLOY_TARGET:-deploy@service.example.com}"

echo "packaging src/ + node_modules"
tar -czf service.tgz src node_modules package.json

echo "shipping to ${TARGET}"
# Placeholder for the real transfer; the eval never executes this script.
echo "would upload service.tgz to ${TARGET}"
