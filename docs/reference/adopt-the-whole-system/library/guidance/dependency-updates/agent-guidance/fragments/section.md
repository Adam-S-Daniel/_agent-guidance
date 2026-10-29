## Dependency updates

Dependabot `cooldown`: `default-days: 7` (example-site-platform `exclude`d, #424);
`semver-major-days: 30` where supported (not `github-actions`, #133). Version
updates only (advisories bypass it); unset still waits 3 days; leave
`semver-minor-days` / `-patch-days` undefined.
**By hand**, nothing watches: take the newest release past 7 days
(`npm view <pkg> time --json`), pinned exact. **Harness CLIs are unpinned**
(Claude Code, Codex; not `uses:` or SDKs): a run installs npm `latest`;
record its version and the models used.
