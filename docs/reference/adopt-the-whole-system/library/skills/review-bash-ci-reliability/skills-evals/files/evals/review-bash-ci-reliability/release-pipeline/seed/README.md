# example-repo

A small release pipeline. `.github/workflows/release.yml` runs three shell
scripts on push to `main`: `scripts/collect.sh` collects the packages
changed since the last release via the GitHub API, `scripts/publish.sh`
triggers the downstream publish workflow and waits for it, and
`scripts/bump.sh` bumps the package version and commits the change.
