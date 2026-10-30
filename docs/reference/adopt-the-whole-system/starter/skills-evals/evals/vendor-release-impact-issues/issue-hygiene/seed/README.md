# example-repo

Uses [ExampleCLI](https://github.com/example-vendor/example-cli) for local
development and CI checks; see `.example-cli/settings.json` for the hook
configuration and `.github/workflows/ci.yml` for how CI installs it.

We register a `SessionStart` hook (`hooks/session-start.sh`) matched on
`startup|resume` to log where each session came from.

ExampleCLI's plugin resolver used to crash intermittently on cold start
(example-vendor/example-cli#6235); we pin CI to 4.1.0, which carries the fix.

## Synced skills

Invoke a synced skill by its bare name, e.g. `/review-checklist`, from any
session in this repo.
