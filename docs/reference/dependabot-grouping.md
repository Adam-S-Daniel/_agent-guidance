# Dependabot grouping: should we, and do we

GitHub reference:
[grouping security updates into one PR](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/configure-security-updates#grouping-dependabot-security-updates-into-a-single-pull-request).

## Should we? Yes, with two carve-outs

Sibling Dependabot PRs strand. The daily sweep
([`dependabot-auto-merge.yml`](../../templates/dependabot-auto-merge/dependabot-auto-merge.yml))
merges one, the rest fall behind, and the sweep never pushes, so each waits for
the fleet worker's `@dependabot rebase`: a day or more per sibling. Action bumps
that edit the same workflow file can fail outright, since `GITHUB_TOKEN` has no
`workflows` permission (skills-evals #384-386, 2026-10-19). Security advisories
arrive in batches too (cms-platform #118-122). One grouped PR has no sibling.

Grouping does not change the merge policy. Each release still waits out its own
cooldown before a group can include it, and security updates skip cooldown,
grouped or not, so they still open at once.

| Update kind | Group? | Shape |
|---|---|---|
| Third-party actions, version updates | Yes | one group, `patterns: ["*"]` |
| Our own actions (`Adam-S-Daniel/*`, `jodidaniel/*`) | **No** | `exclude-patterns`: they skip cooldown, so a cooled-down group would hold them back |
| npm / bundler / pip, version updates | Yes, minor + patch | `update-types: ["minor", "patch"]`; majors stay separate (30-day cooldown, revertible alone) |
| Security updates, any ecosystem | Yes | a group with `applies-to: security-updates`; exclude a package whose bad bump would block the rest (cms-platform excludes `@playwright/test`) |

Declare it in `dependabot.yml`. The repo-level "Grouped security updates"
toggle has no API (repo-settings
[ADR 0002](https://github.com/Adam-S-Daniel/repo-settings/blob/main/docs/decisions/0002-declare-dependabot-security-settings-including-the-unenforceable-one.md)),
so whether it is on cannot be checked, and `dependabot.yml` groups override it.
A group without `applies-to` covers version updates only.

## Do we? Mostly not

Default branches of every non-archived repo under both owners, read through the
contents API on 2026-11-20 (two repos have no `dependabot.yml`):

| Repo | Version-update group | Security group |
|---|---|---|
| `skills-evals` | actions, own excluded | none |
| `fastmail-actions` | actions, own excluded | none |
| `cms-platform` | actions minor + patch | npm (`@playwright/test` excluded) |
| the other 21 | none | none |

So grouping is the right default but is not rolled out. Rolling it out means a
`groups:` block in each repo's `dependabot.yml`; nothing syncs that file.
