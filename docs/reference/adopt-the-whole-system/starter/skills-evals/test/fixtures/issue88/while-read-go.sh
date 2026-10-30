#!/usr/bin/env bash
set -e
files=()
while IFS= read -r -d '' f; do
  files+=("$f")
done < <(git diff --cached --name-only -z -- '*.go')
[ "${#files[@]}" -eq 0 ] && exit 0
command -v gofmt >/dev/null 2>&1 || exit 0
bad=$(gofmt -l "${files[@]}")
[ -n "$bad" ] && exit 1
exit 0
