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

## Recommended checks, in order

1. **`gha-run-interpolation` in `agent-checks`.** Refuse a workflow or action
   file where a `${{ }}` whose root is `github.event.*`, `inputs.*`,
   `github.head_ref`, `github.ref_name` or another step's or job's output sits
   inside a `run:` or `script:` value, except the roots that are fixed-shape
   (`github.event.pull_request.number`, `github.sha`, `github.run_id`,
   `github.repository`, `runner.*`). The message says: pass it through
   `env:` and read `"$NAME"` in the shell (or `process.env.NAME` in a
   `script:`), because GitHub substitutes the expression into the script text
   before the shell parses it. **It must parse the YAML** (the fleet's
   AST-not-regex rule): a regex cannot tell a `run:` value from a `with:`
   value or a comment. The runner is dependency-free bash today, so this needs
   a decision before it is built (below). Measured on fleet mains it would
   refuse **26 interpolations in 6 repos** (list above), so it needs
   either the 26 fixed first (one small PR per repo, mostly the shared
   `dependabot-auto-merge.yml` template) or a mode that judges only changed lines.
2. **zizmor in CI, advisory first**, on the repos with workflows. It is the
   maintained answer to check 1 and to the other Actions pitfalls, and it
   answers the "which expressions are safe" question better than a
   hand-written list. Run it as a non-required job, then promote it.
3. **A cms-platform-only `.agent-checks/local.sh` rule** that refuses a new
   `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`document.write` outside a
   file on an allowlist, parsed with `acorn`/`acorn-walk` (already a
   dependency there). Not for the fleet: five other repos have one or two
   such files each, and `esc()` use there looks right.
4. **Leave to CodeQL**: SQL, XML and command-injection in code. The counts
   are small and CodeQL's queries are better than a hook can be.

## Decision for the owner

Check 1 needs a YAML parser in a runner that has none. Options:

- **A.** A small Node helper beside `dated-phrasing.js`, using `yaml` from a
  vendored copy delivered with the runner (the sync copies files, so this is
  possible; it adds one dependency the fleet must review and pin).
- **B.** Call `python3` with PyYAML (preinstalled on GitHub-hosted runners;
  not guaranteed on a developer machine, where the check would skip with a
  warning, as `dated-phrasing` does without node).
- **C.** Skip the hook and adopt zizmor (check 2) as the enforcer.

Recommendation: **C first** (smallest, maintained, and it knows which
expressions are safe), then **B** only if a commit-time refusal is still
wanted, because the file already degrades gracefully when node is missing and
the same pattern works for Python. This design deliberately adds no check to
`templates/agent-checks/agent-checks` yet: check 1 cannot honor the parser
rule with what the runner has, and shipping a regex version would break it.
