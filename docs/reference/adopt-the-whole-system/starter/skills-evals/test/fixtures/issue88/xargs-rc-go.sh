#!/usr/bin/env bash
files=$(git diff --cached --name-only -- '*.go')
RC=0
if [ -n "$files" ]; then
  if command -v gofmt >/dev/null; then
    printf '%s\n' "$files" | xargs gofmt -l || RC=1
  else
    echo skip >&2
  fi
fi
exit "$RC"
