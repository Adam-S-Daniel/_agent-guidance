# example-org audit tools

`.github/workflows/visibility-audit.yml` runs `scripts/audit-visibility.sh`
every night. It lists the organization's public repositories with `gh` and
fails the job for any that is not named in `config/public-allowlist.txt`, so
a repository made public by mistake is caught the next morning.
