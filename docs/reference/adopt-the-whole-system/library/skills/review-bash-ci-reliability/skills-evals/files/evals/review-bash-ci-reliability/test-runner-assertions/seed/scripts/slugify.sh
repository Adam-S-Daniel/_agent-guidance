#!/usr/bin/env bash
# Print a URL slug for the title given as the arguments: lowercase ASCII
# letters and digits, with every other run of characters collapsed to "-".
set -euo pipefail

title="$*"
slug=$(printf '%s' "$title" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')
slug=${slug#-}
slug=${slug%-}
printf '%s\n' "$slug"
