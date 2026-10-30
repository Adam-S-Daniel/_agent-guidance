# example-configs

`templates/` holds the reviewed source of every service config and
`deploy/` the copies the hosts read. `.github/workflows/drift.yml` runs
`scripts/check-drift.sh` on every pull request and once a day: it compares
each deployed file with its template, prints a unified diff for every file
that drifted, and fails the job if any did.

A template may name the team that owns it on a `# owner:` line; a template
without one is reported as unassigned.
