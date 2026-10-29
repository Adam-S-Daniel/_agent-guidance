# example-repo

Requires ExampleCLI 4.1.0 or later.

`.example-cli/settings.json` registers a `SessionStart` hook
(`hooks/session-start.sh`) matched on `startup|resume`, and
`scripts/check.sh` runs `example-cli check` before every push.
