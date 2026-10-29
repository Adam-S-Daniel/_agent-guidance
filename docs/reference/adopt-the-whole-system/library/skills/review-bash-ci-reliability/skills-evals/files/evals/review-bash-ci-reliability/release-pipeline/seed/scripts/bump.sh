#!/usr/bin/env bash
# Bump the package version and commit the change.
set -euo pipefail

VERSION=$(jq -r '.version' package.json)
NEXT_VERSION="${VERSION%.*}.$(( ${VERSION##*.} + 1 ))"

sed -i "s/\"version\": \"${VERSION}\"/\"version\": \"${NEXT_VERSION}\"/" package.json

git add package.json
git commit -m "chore: bump version to ${NEXT_VERSION}"
git push origin HEAD:main
