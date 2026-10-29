# example-repo

Uses [ExampleCLI](https://github.com/example-vendor/example-cli) for local
development; see `.example-cli/settings.json` for the hook configuration.

We register a `SessionStart` hook (`hooks/session-start.sh`) matched on
`startup|resume` to log where each session came from.

## Synced skills

Invoke a synced skill by its bare name, e.g. `/review-checklist`, from any
session in this repo.
