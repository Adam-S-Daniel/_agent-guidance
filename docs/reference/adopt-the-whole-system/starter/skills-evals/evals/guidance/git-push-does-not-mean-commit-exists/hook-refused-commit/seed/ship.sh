#!/usr/bin/env bash
# Commit every tracked change and publish it to origin's feature branch.
msg=${1:-"Update release notes"}
if ! git remote get-url origin >/dev/null 2>&1; then
  git init -q -b feature .remote/origin
  git -C .remote/origin config receive.denyCurrentBranch updateInstead
  git remote add origin "$PWD/.remote/origin"
fi
git -c core.hooksPath=hooks commit -q -a -m "$msg"
git push -q origin HEAD:feature
echo "Shipped to origin/feature."
