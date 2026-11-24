# 0050 — agent-checks lints every changed file in two tiers: a few errors refuse, the rest warn

**Status:** Accepted (2026-11-20)

## Context

[ADR 0049](0049-code-quality-comes-from-managed-linters-not-codeql.md) decided
that maintainability and reliability come from linters that are ensured
centrally and automatically in each repo's CI and git hooks, with a strategy
that leans toward sanity: a small high-confidence rule set that blocks, existing
code addressed gradually, noise turned off. It left the delivery open.

The fleet already has one thing that reaches every repo, runs in the git hooks
and in a required CI check, and is overwritten on drift: the `agent-checks`
runner ([ADR 0021](0021-the-sync-delivers-agent-checks-to-every-fleet-repo.md)),
delivered by the sync with a workflow the sync does not overwrite after first
delivery. Repos' own linters are a patchwork: some run PSScriptAnalyzer or
actionlint in a workflow with a hand-pinned binary, a managed
`scripts/lint-staged.sh` mirrors a CI gate for cms-platform-style sites, and
most repos run no linter at all.

## Decision

`templates/agent-checks/agent-checks` gains a **lint pass** over the files the
event already considers changed (pre-commit: staged; pre-push and `ci`: the
range; `tree`: every path), dispatched by file type, never by repo config, so
it works in every repo with no setup:

| Files | Tool |
|-------|------|
| `*.sh`, `*.bash`, `.githooks/*`, extensionless with a sh-family shebang | shellcheck |
| `*.py` | ruff |
| `.github/workflows/*.y*ml` | actionlint |
| `*.ps1` `*.psm1` `*.psd1` | PSScriptAnalyzer through pwsh, when pwsh exists |
| `*.js` `*.mjs` `*.cjs` | `node --check` (syntax only) |
| `*.rb` | `ruby -wc` (syntax only) |

**Two tiers, the owner's distinction between lints that must pass and lints
that are advice:**

- **Required** refuses, as `lint-required-<language>`, over the whole changed
  file: shellcheck `--severity=error`; ruff `E9,F63,F7,F82` (syntax errors,
  undefined names, invalid comparisons; not unused imports); actionlint errors
  (its `runner-label` kind is advisory, since a repo's own runner labels are
  not knowable here); PSScriptAnalyzer Error and ParseError; a failing
  `node --check` or `ruby -c`. An error anywhere in a file you touch is a bug.
- **Advisory** prints `agent-checks (warning):` lines (`::warning` annotations
  in CI) and never changes the exit status: shellcheck at its default severity,
  ruff `F,B,PLE,PLW,W605` and the bug-shaped `E7xx` (no pyupgrade, no
  simplify, no formatting: measured on the fleet they were pure style),
  PSScriptAnalyzer Warning, and actionlint's embedded shellcheck. Only findings
  on **changed lines** show in pre-commit, pre-push and `ci` (the diff hunks),
  so existing code is burned down as it is touched and a repo never turns red
  on adoption; `tree` shows everything. At most 20 findings print per file.

**A repo's own config wins.** `.shellcheckrc`, ruff's config (`ruff.toml`,
`.ruff.toml`, `pyproject.toml` `[tool.ruff]`: the advisory tier then uses the
repo's rule selection while the required list still applies and the repo's
`exclude` is respected), `.github/actionlint.yaml` and a root
`PSScriptAnalyzerSettings.psd1` are read from the content being checked.
Vendored, fixture, snapshot and fragment paths are skipped.

**Opt-out is a committed file**, `.agent-checks/lint.conf`: `key=value` lines
(`required=off`, `advisory=off`, `python=off`, `python.advisory=off`,
`exclude=<glob>`), parsed as data, never sourced. `AGENT_CHECKS_LINT=off`
does the same for one run.

**Tools.** Locally a missing tool skips its language with one notice and never
refuses or touches the network. In CI a missing shellcheck, actionlint or ruff
is downloaded as its release binary at the exact version and sha256 pinned at
the top of the runner (verified with `sha256sum -c`), and a failed download or
a digest mismatch **refuses** (`lint-tool-install`), because CI is the
enforcement. PSScriptAnalyzer is installed pinned by version with
`Install-PSResource`, in CI only and only when pwsh exists (a Gallery module has
no archive digest to pin).

**Repo-owned linters that need the repo's dependencies stay in the repo's
CI:** ESLint, RuboCop, yamllint, markdownlint, Prettier and anything else that
needs `npm ci` or a bundle. The runner only does what a single pinned binary
can do with no setup. A repo may drop a workflow step the pass now covers
(a bare shellcheck, actionlint or PSScriptAnalyzer step) and keep the config.

## Consequences

- Every fleet repo gets syntax and undefined-name protection for shell,
  Python, workflows, PowerShell, JavaScript and Ruby at once, with the same
  message each time, and the run log counts `lint-required-<language>`.
- Existing code is never a wall, but findings on code nobody touches are never
  shown until `tree` is run: burn-down depends on someone running it.
- The required set is small on purpose; a rule that proves noisy moves to the
  advisory tier here, once, for the whole fleet.
- The runner grows by the lint section and the pins. Bumping a pin means
  replacing the version and digests together, after the release is 7 days old.
- A CI run now depends on GitHub release downloads when the runner image lacks
  a tool; it fails closed, not open.
- Hand-pinned installs in repo workflows (for example cms-platform's
  `self-ci.yml`) are now duplicated by the runner and can be removed per repo,
  not by this change.
