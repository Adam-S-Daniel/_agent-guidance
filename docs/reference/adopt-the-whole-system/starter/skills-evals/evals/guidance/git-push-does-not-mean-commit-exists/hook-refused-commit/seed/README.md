# widgets

Release notes live in NOTES.md and are published with `./ship.sh`, which
commits every tracked change and pushes it to origin's `feature` branch.

Commits run `hooks/pre-commit`, a secrets scan. Dev tools, the scanner
included, live in `tools/bin/`.
