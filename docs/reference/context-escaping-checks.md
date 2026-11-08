# Context escaping: survey and check design

Design for [issue #468](https://github.com/Adam-S-Daniel/_agent-guidance/issues/468).
It serves the owner's rule in `agents-md/base.md`'s Security section
([#467](https://github.com/Adam-S-Daniel/_agent-guidance/pull/467)): untrusted
data is escaped or encoded for each output context, with a well-established
library or built-in, just before it is placed into that context. Rejecting or
stripping input is no substitute.

Measured 2026-11-07 on `origin/HEAD` of the 14 repos in `cron_coverage.fleet`
(both owners). **The sink counts are file counts from a heuristic text scan**
that skips fixtures, tests, vendored code and Markdown. They size the problem;
they do not prove a finding. The workflow-injection counts come from parsing
every workflow and action file with PyYAML, so they are exact for `run:` and
`script:` values.

## What already enforces the rule

| Layer | Covers | Runs |
|---|---|---|
| `agent-checks` (`templates/agent-checks/agent-checks`) and each repo's `.agent-checks/local.sh` | commit-time rules; none today reasons about output encoding | pre-commit, pre-push, required CI check |
| cms-platform `secrets-scan.sh`, `lint-staged.sh` | secrets; eslint, prettier, ruff, rubocop, shellcheck, shfmt, stylelint on staged files | pre-commit in cms-platform and its consumers |
| cms-platform `liquid-escape-lint.rb` ([cms-platform#980](https://github.com/Adam-S-Daniel/cms-platform/pull/980), pending) | Liquid `{{ }}` output must end in an encoder for its context | pre-commit, and `site-verify.yml` (required on the consumers' main) |
| CodeQL | seen as a check run on open PRs in `_agent-guidance`, `adam-agentskills`, `adamdaniel.ai`, `cms-platform`, `jodidaniel.com`, `skills-evals`; not verified for the other eight (no open PR to read, or not looked at) | CI, asynchronous; see `code-scanning-alerts.md` |
| The fleet rule "don't interpolate `${{ inputs.* }}` / `${{ github.event.* }}` into `run:`" | prose only; **no check enforces it** | none |

## Coverage matrix: repo by context

`E` = an existing check covers it. `Q` = a file-count signal below, worth a
look. `-` = no signal. Blank cells mean the repo has no code in that context.

| Repo | HTML / JS sink | Liquid | Shell / `run:` | SQL | XML / feed | Log lines (`::error::`) |
|---|---|---|---|---|---|---|
| `_agent-guidance` | - | | **Q 4 interpolations** | Q 1 | Q 1 | Q 5 |
| `adam-agentskills` | - | | **Q 3** | Q 1 | - | - |
| `adam-agentskills-private` | - | | - | - | | |
| `adamdaniel.ai` | Q 3 | E (#980 pending) | - | | Q 2 | - |
| `ai-usage-dashboard` | Q 1 (`innerHTML` with an `esc()` helper) | | - | | | |
| `claude-memory-map` | Q 5 | | **Q 3** | | | Q 1 |
| `cms-platform` | **Q 23**, eval/`Function` 7, `child_process` exec 4 | E (#980 pending) | **Q 10** | Q 8 | Q 10 | Q 6 |
| `fastmail-actions` | | | **Q 3** | | | |
| `jodidaniel.com` | - | E (#481 pending) | - | | | |
| `repo-settings` | | | **Q 3** | | | |
| `rss-inator` | | | - | | Q 3 (`ElementTree` plus `html.escape`: the right built-ins) | |
| `security-alerts` | | | - | | | |
| `skills-evals` | eval/`Function` 2 | | `shell=True` in 1 scorer file | | Q 1 | Q 3 |
| `wsl-automation` | | | - | | | |

CSV, YAML-as-output, Markdown-as-output and email headers produced no sink
signal worth a column in this pass; the owner's rule still applies to them
(see "Gaps").

## Workflow interpolation: the measured case

Parsing every workflow and action file, an expression whose root is
`github.event.*`, `inputs.*`, `github.head_ref`, `github.ref_name`, or a
`steps.*.outputs.*`, `needs.*.outputs.*` or `matrix.*` value appears inside a
`run:` or `script:` value **26 times in 6 repos** (counts in the matrix).
Three groups:

- **The shared `dependabot-auto-merge.yml`** puts
  `github.event.pull_request.base.ref` and `.head.sha` into a `run:` in
  `_agent-guidance`, `adam-agentskills`, `claude-memory-map`,
  `fastmail-actions` and `repo-settings` (3 each). `head.sha` is a hex
  string; `base.ref` is a branch name the repository controls. Low risk, but
  the fleet rule says pass both through `env:`.
- **`cms-platform`** has 10: `steps.gate.outputs.run` (a boolean written by an
  earlier step) in three loop workflows, and `inputs.branch_prefix`,
  `inputs.label_prefix` and `inputs.fix && '--fix' || ''` in
  `editorial-label-audit.yml`. `inputs.*` of a reusable workflow is
  caller-controlled text and is the real injection shape.
- `_agent-guidance`'s `sync.yml` interpolates `inputs.dry_run` (a boolean
  input).

None of these was traced to an outside contributor's input (the cms-platform ones were not read in full); each is the
shape the rule exists to stop, and nothing stops a new one.

## Static analyzers weighed for a commit hook

| Tool | Adds | Fits a commit hook? | Verdict |
|---|---|---|---|
| zizmor | Expression-injection (`template-injection`) and other Actions findings, with a context-aware list of which expressions are safe | A Rust binary, offline, well under a second on a few workflow files; needs installing on each machine and runner | **Best fit for the workflow context.** Needs a pinned install path first. |
| actionlint expression checks | Syntax and type of `${{ }}`; it does not flag injection | Already a required lane in cms-platform | Keeps its job; not an injection check |
| Semgrep, pinned local ruleset | Taint rules for XSS, command and SQL injection across languages | Seconds on staged files when run with `--config` of a local file and no network; a Python install per machine | Worth it only for cms-platform's JS (23 HTML-sink files) and only with rules the fleet writes and pins; a generic registry ruleset is out (network, and it moves) |
| `eslint-plugin-no-unsanitized` | `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write` | Needs a root `npm ci`; `adamdaniel.ai` has deliberately no lint toolchain | Fits cms-platform only, where `e2e/` already has `npm ci`; its `esc()`-style helpers need declaring as escapers |
| Bandit | Python `shell=True`, `os.system`, string-built SQL | `pip install bandit`; fast | Marginal: one `shell=True` file fleet-wide, in `skills-evals`; ruff's `S` rules (flake8-bandit) would cover the same where ruff already runs |
| Brakeman | Rails | No Rails app in the fleet | Skip |
| shellcheck | Unquoted expansions in shell | Already on in cms-platform | Keep; it does not see `${{ }}` because GitHub substitutes it before the shell runs |

## What CodeQL covers, and what a commit hook still adds

CodeQL's default suites for JavaScript/TypeScript, Python, Ruby, Go, C# and
Actions catch DOM XSS, command injection, SQL injection, and, with the Actions
language enabled (an `Analyze (actions)` job ran on the PRs read), the
script-injection queries. Where that job runs it likely covers the workflow-interpolation case above, asynchronously, and a
finding does not block a merge. A commit-time check is worth its cost only
where it (a) is faster than waiting for a CodeQL result, (b) can name the
fleet's own approved encoder, which CodeQL cannot, or (c) covers a repo with
no CodeQL. The repos where CodeQL was not verified are small and mostly shell,
PowerShell, YAML and Markdown; for them the workflow check is the only
context with real exposure. Confirm each repo's code-scanning setup before
relying on CodeQL for it.

## Gaps

1. **Workflow-expression interpolation into `run:`/`script:`** has a stated
   rule and no commit-time check (CodeQL may flag it where enabled).
2. **JS HTML sinks outside Liquid**: `innerHTML` and friends in cms-platform's
   admin and e2e code and in single-file tools (`ai-usage-dashboard`,
   `claude-memory-map`). Each file seen uses a local `esc()`; nothing makes
   that the rule.
3. **Dynamic code**: `eval` and `new Function` in cms-platform (7 files) and
   `skills-evals` (2). Probably intended (harness code); needs a human read,
   not a hook.
4. **Log lines and workflow commands**: `echo "::error::...$var"` with a
   variable that can hold a newline lets that value start a new workflow
   command. 17 files across 4 repos. Mostly constants and `$VAR` from `env:`.
5. **Contexts with no signal**: CSV, YAML and Markdown output, and email
   headers. None showed a sink pattern in this pass; revisit when a repo
   starts emitting one.

## What is built: `gha-template-injection`

The first check is in `templates/agent-checks/agent-checks`, so the sync
delivers it to every fleet repo's `.githooks/agent-checks`, where it runs from
the pre-commit and pre-push hooks and from the required `agent-checks` CI job.

- **It runs zizmor, not a hand-written list.** The rule asks for a
  well-established tool, and zizmor's `template-injection` audit parses the
  YAML and knows which expressions an outsider can steer, which a fleet-written
  list would get wrong. The check copies each changed
  `.github/workflows/*.yml|yaml` and local `action.yml|yaml` (root or
  `.github/actions/`) out of the content being checked into a temporary
  directory and runs `zizmor --offline --no-config` on it. Fixture copies of
  workflows under other paths (skills-evals' `evals/**/seed/`) are not
  audited, because GitHub never runs them.
- **It refuses Medium and High findings only.** zizmor rates an earlier
  step's output Informational; those go to review, not refusal.
- **zizmor is pinned**: version 1.31.1 (released 2026-10-15; 1.32.0 was under
  7 days old), installed once per machine by pip with `--require-hashes`
  against every wheel's sha256, into
  `${XDG_CACHE_HOME:-$HOME/.cache}/agent-checks/zizmor/1.31.1`. The first run
  takes about 30 seconds; later runs take under a second per repo. A zizmor
  on `PATH` at that version is used as is.
- **It fails closed only in CI.** Without python and pip, or offline, a local
  run warns and skips; the CI job refuses, because CI is the enforcement.
- **The escape hatch is zizmor's own**: `# zizmor: ignore[template-injection]`
  on the line, with the reason, for a value that provably cannot carry code.

Measured with `agent-checks tree` on the fleet mains on 2026-11-08: 13 of
the 14 repos (`security-alerts` is private and was not readable from the
session that measured it):

| Repo | Refusals | Where |
|---|---|---|
| `_agent-guidance`, `adam-agentskills`, `claude-memory-map`, `fastmail-actions`, `repo-settings` | 2 each | the shared `dependabot-auto-merge.yml` (`base.ref`, `head.sha`); fixed at its template in the same PR as the check, and the sync carries the fix to the other four |
| `cms-platform` | 2 | `editorial-label-audit.yml` lines 57 and 58 (`inputs.*` of a reusable workflow); filed as an issue there |
| `adam-agentskills-private`, `adamdaniel.ai`, `ai-usage-dashboard`, `jodidaniel.com`, `rss-inator`, `skills-evals`, `wsl-automation` | 0 | |

A repo whose workflow still has a finding is not blocked until a change
touches that file; then the refusal names the line and the fix.

## Recommended next checks, in order

1. **zizmor's other audits** stay out of the hook: on today's mains they report
   hundreds of `unpinned-uses` and `artipacked` findings that the fleet's
   lockfile pins already answer or that need a separate decision.
2. **A cms-platform-only `.agent-checks/local.sh` rule** that refuses a new
   `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`document.write` outside a
   file on an allowlist, parsed with `acorn`/`acorn-walk`, or
   `eslint-plugin-no-unsanitized` in its existing `lint-staged.sh`. Not for
   the fleet: five other repos have one or two such files each, and `esc()`
   use there looks right. Filed as an issue in cms-platform.
3. **Leave to CodeQL**: SQL, XML and command injection in code, and dynamic
   `eval`/`Function`. The counts are small and CodeQL's queries are better
   than a hook can be. CodeQL is asynchronous and does not block a merge, so
   it is a backstop, not a substitute for check 1 where a hook can do it.
4. **Log lines and workflow commands** (`echo "::error::$var"`): no
   established tool checks this; revisit if a finding shows real exposure.
