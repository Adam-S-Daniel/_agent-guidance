#!/usr/bin/env bash
set -e
files=$(git diff --staged --name-only -- '*.go')
[ -z "$files" ] && exit 0
command -v gofmt >/dev/null || exit 0
bad=$(gofmt -l $files)
[ -n "$bad" ] && exit 1
exit 0
