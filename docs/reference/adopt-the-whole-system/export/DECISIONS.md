# Kit export: what is left out or changed, and why

The owner, 2026-10-29: copy the real setup into the adoption kit; leave nothing
out without a compelling reason, and record every such decision here so it
can be reversed. Each entry says what was decided, why, and how to reverse it.
The per-file detail is in the manifests beside this file.

## Principles

- **P1. Copy, then substitute.** Each repo is exported from a pinned commit.
  Owner-specific literals become placeholders that the adopter's setup fills
  in. Everything else is byte-for-byte.
- **P2. Historical citations stay pointed at the source.** A link to a
  specific issue, pull request, commit or file-at-a-commit in the owner's repos
  records where a lesson came from. Rewriting it to the adopter's owner would
  point at something that doesn't exist. These links stay unchanged. Live
  references (a repo the code reads, an owner it scans, a branch it pushes)
  are substituted.
  Reverse: substitute them too and accept dead links.
- **P3. Generated files are regenerated, never substituted.** Lockfiles
  (`skills.lock`, `actions.lock`), digests in `repos.yml`, the generated
  `AGENTS.md`, `fleet-guidance.md` and the byte counts in `eval-coverage.yml`
  are rebuilt by the export with each repo's own tooling, because they hold
  hashes and numeric GitHub ids.

## Left out

| # | What | Kind | Why | Reverse by |
|---|---|---|---|---|
| X1 | Fastmail skills (`add-from-address`, `add-received-from-addresses`, `fastmail`) | personal | They drive the owner's own mail account through the owner's mail-automation repo. | Include them; an adopter would also need that repo. |
| X2 | The owner's writing-style skill and its eval fixtures | personal | It describes the owner's voice. | None sensible. |
| X3 | `docs/credential-runbook.md` (private registry) | private | It names the owner's real credentials and where they live. | None. |
| X4 | The source platform repo's eval fixtures and the four `evals/real-work/` seeds taken from it (about 1,800 files) | platform | They are copies of the owner's sites and test skills that only apply to that platform repo. | Include them along with the platform repo's skills. |
| X5 | `evals/real-work/_agent-guidance-196` | runtime-data | Its seed and checker pin real commit SHAs and byte-equal checks, so they can't be substituted. An adopter's fixture-supply job mines its own real-work cases. Tests that read these fixtures are pointed at a small invented seed instead. | Ship it unsubstituted as a read-only example. |
| X6 | Eval results branches (`persistent/eval-results`, `persistent/orchestration-state`) | runtime-data | They are the owner's measurements of the owner's skills; an adopter's own runs produce theirs. | Copy them as a baseline. |
| X7 | `docs/evidence/2026-10-07-workstation-retirement-117.md` | personal | It is a checkpoint on retiring the owner's own workstation (its machine and task names); nothing links to it. | Include it with the hostnames replaced. |
| X9 | **Content starts with one real item of each kind and a template** (owner, 2026-10-29 12:29 UTC; it replaces the same day's 02:02 UTC decision that content start fully blank). Skills, guidance sections and agent-checks checks each keep ONE real item copied unchanged (apart from substitution) from the owner's setup, plus a commented-out or inert template beside it: the skill `workflow-path-audit` (with the four eval fixtures under `evals/workflow-path-audit/` and a template in `templates/skill/`), the guidance section "A successful `git push` does not mean your commit exists" (with its fixture `evals/guidance/git-push-does-not-mean-commit-exists/` and an HTML-commented section template in `agents-md/base.md`), and the agent-checks check `skill-shell` (with a commented-out `.orig`/`.rej` example beside it). Every other skill, section and check, and the eval fixtures that test them, is left out. The machinery that delivers, measures, improves and retires them all comes along. Tests that only lock a left-out skill, section or check are removed and listed in the export's record; tests of the machinery use test-only fixtures. | owner choice | An adopter grows their own content through the loop, with a worked example of each kind to copy from instead of a blank page. | Delete the three real items and their fixtures (the first, blank form of X9: re-add the manifest excludes for them, restore the patches' blanking of `blank_content.py`, `blank_registry.py` and `empty_config.py`, and move the restored tests back into `removed_tests.txt` and the patches), or copy more chosen items in, one at a time (drop that item's `exclude` and add its tests back). |
| X10 | From `wsl-automation`: the restic backup, and the Remote Control session keeper. The waiting-on-you feed is **included** (owner, 2026-10-29: the dashboard's waiting list keeps all its plumbing, renamed from "Waiting on" the owner's name to "waiting on you"), and so is the usage census task (wrapper, Task Scheduler registration, checkout updater, tests), with a cron equivalent for Linux and macOS; the session miner is scheduled the same way. | personal | They maintain the owner's machine and dashboard; the census feeds the loop's value score. | Include them. |
| X8 | Scheduled tasks: Morning brief, Heartbeat, Watch for Work locally, Lockfile upstream daily check, Dashboard Hub refresh, Token sprint heartbeat, Rotate-keys reminder, one-shot eval-cap reminders, Watch reaper rejects, Cross-post watcher | personal or platform | They serve the owner's day, other projects or the platform repo, not the loop. | Add their prompts to the kit's routine list. |

## Changed

| # | What | Why | Reverse by |
|---|---|---|---|
| C1 | The two registry repos, whose names start with the owner's first name, become `acsi-agentskills` and `acsi-agentskills-private`: the owner's prefix is replaced by the generic `acsi-` (automatic continuous self-improvement). Plugin names take the same prefix (`acsi-anything-anywhere`, `acsi-coding-anywhere`, `acsi-coding-local`, `acsi-non-coding-local`, `acsi-private-anything-anywhere`), and so does the marketplace, which is named for its repo. The bare owner-name bundle label in fixtures becomes `acsi` (`acsi-writing-style`). | They carry the owner's name. A generic prefix keeps the names distinct from the source owner's and the set still reads as one set, which dropping the prefix would not. Plugin names are one-way once enabled, so an adopter must start with names they will keep. `_agent-guidance`, `skills-evals` and `repo-settings` keep their names. | Drop the prefix instead of replacing it (the first form of C1), or keep the `acsi-` names; an adopter could rename before first enabling. |
| C2 | Owners: the source owner's login → the adopter's owner (`example-adopter`); the second source owner → an optional second owner (`example-adopter-two`), in every case and separator form (`GH_TOKEN_<OWNER>`, `token_<owner>`, ...); a third account's examples → an invented third owner. A one-owner fleet sets the second owner to the first, and `bootstrap/lib/substitute.py` then drops the second owner's legs (One-owner fleets, below). | Owner names. | n/a |
| C3 | Code paths specific to the source platform repo are **kept**, with the repo names replaced by an invented example platform (`example-site-platform` and its consumers): the label-only allowlist in repo-settings (ADR 0016), the federated marketplace entry, the `platform.lock` branch in `sync.sh`, the release-tag carve-out in guidance and scorers. | They are generic mechanisms (a shared platform repo with consumer sites) that an adopter may grow into, and deleting them touches many tests. | Delete them and their tests. |
| C4 | The `scheduled-run-health` reusable workflow is copied from the source platform repo into the kit's `_agent-guidance`, and the four callers point at that copy. | `_agent-guidance`'s cron-coverage gate requires it, and the platform repo is not part of the kit. | Drop the callers and the gate. |
| C5 | The source owner's `verify-actions-lock` action stays as is: adopters use the owner's published, Apache-2.0 action. | It is a public action meant for reuse, and substituting the owner breaks the lockfile. | Fork it and substitute. |
| C6 | `evals/budget.yml` is reset to the standing defaults (weekly 30%, per run 3 points, stop at 80%), keeping the comments that explain the design and dropping the dated, week-by-week cap changes. | Those were the owner's spending decisions for particular weeks. | Copy the file as is. |
| C7 | Claude.ai ids (trigger, artifact, session, environment, project ids) become placeholders. Where a workflow hardcodes a routine trigger id, it reads a repo variable instead. | They are the owner's account objects. | n/a |
| C8 | Tests that read excluded fixtures (about 40 in skills-evals) are pointed at a small invented seed, and their expected values are updated; no test is deleted unless what it tests was itself left out. | Keep the tests' lessons without the excluded data. | n/a |
| C9 | The repo-settings workflow's two hardcoded owner steps (one per source owner) become the adopter's owner and the optional second owner. | Owner names. | n/a |
| C10 | Text that tells a story about the owner by name (for example "<name>'s standing rule", "<name>'s decision on 2026-10-06") says "the owner" instead, capitalized only where a sentence starts. Dates and measurements stay. | Names. | n/a |
| C11 | **Live registries list only the kit's repos.** `repos.yml`, `fleet.yml`, `dependabot.yml` entries, workflow repo lists and any other config the code acts on name only the kit's own repos (and the invented example platform of C3). The owner's other repos (personal tools, sites, scratch repos) are dropped from config, and tests that pin those lists are updated. Mentions of those repos in history (ADRs, evidence, changelog analysis, routine findings) stay, like P2 citations. | Config that names repos an adopter doesn't have would sync into, or report on, repos that don't exist. History mentions are what lessons were learned on. | Keep the full list; the adopter trims it. |
| C12 | **Account-specific numbers become config.** GitHub App ids (the source's two bypass Apps' and any others), installation ids and similar numbers that differ per account are read from a variable or a single config file the bootstrap fills, never hardcoded in the kit. | They are the owner's App ids; an adopter's Apps have different ones. | n/a |

## Kept on purpose (questions an inventory raised)

- **Agent changelogs, run logs and the routine findings** (`agent-claude-code.CHANGELOG.md`, `agent-codex.CHANGELOG.md`, `agent-changelog-runs.md`, `prompt-audit-runs.md`, the measured tail of `docs/routines/guidance-centralization.md`): they are analysis of the same vendors an adopter uses and the measured lessons the routines rely on.
- **ADRs and evidence files**, school bell-schedule ADR mentions, the Fastmail entries in `docs/skill-impact.md`: history, with names substituted.
- **Stub and full guidance modes**, the helper scripts and the full `repos.yml` schema.

## Scheduled tasks in the kit

Included: Fleet worker, agent changelog watcher, prompt-audit sweep, Memory
curation, the eval runner, Guidance centralization audit. Optional: Daily
security alert triage (needs a `security-alerts` repo the kit does not yet
include), Workflow health sweep and the "Dashboard refresh: every 2 hours" task plus its three merge-triggered refreshes, which keep the project dashboard (a claude.ai artifact in the owner's account that shows the loop, eval results and real-work impact) current (both run
scripts from the owner's project files, not a repo). Prompts that live only in
claude.ai are committed to the kit as files.

## Export record: `_agent-guidance` and `repo-settings`

How `export.py` carries out the entries above for these two repos. The
manifests (`manifests/_agent-guidance.yml`, `manifests/repo-settings.yml`)
hold every edit with its `why`; this section records the ones that are more
than a substitution, and every test the X9 blanking changed or removed.

### Substitution details

- **The retired registry.** The source owner's retired `agentskills` registry
  would become `example-adopter/agentskills` under C2 alone. It becomes
  `example-adopter/retired-agentskills` instead, which stays distinct from the
  live `example-adopter/acsi-agentskills` and reads as what it is, so history
  about the retired repo never reads as the live one.
- **C5 is narrowed to uses and links.** The source owner's `verify-actions-lock`
  is kept where it is used (`uses: ...@`), named in backticks, or linked
  (`github.com/...`). The same string as an element of a fleet registry
  (`repo-settings/fleet.yml`) is the owner's own repo list, so it is
  substituted like every other entry.
- **P2 exceptions.** `test/test-routine-merge-gate.js` and
  `docs/reference/agent-changelog-issue-map.txt` hold `owner/repo#N`
  strings that are test data, not citations; they are substituted so the
  fixture and its expected values agree (`protect_except`).

### C4 in practice

The reusable is imported from the source platform repo at a pinned commit to
`.github/workflows/scheduled-run-health-reusable.yml`, with the two scripts
it checks out (`scripts/audit-scheduled-runs.js`,
`scripts/check-platform-currency.js`). Its two legacy SHA pins become the
tags `actions.lock` already maps to the same commits (`actions/checkout@v7.0.1`,
`actions/setup-node@v7.0.0`), and `actions.lock` gets its entry. The
`_agent-guidance` caller uses the local path; the `repo-settings` caller uses
`example-adopter/_agent-guidance/...@<forty zeros>`, a placeholder the
adopter replaces (`REGENERATE.md`). The cron-coverage test that deletes the
caller's `uses:` line matches the key now, not the owner name.

### X9 in practice

- `agents-md/base.md` keeps its preamble, a template section, and one real
  section, "A successful `git push` does not mean your commit exists",
  copied byte for byte from the source (after substitution).
  `scripts/check-guidance-coverage.js` refuses a payload with no `##`
  heading (exit 2), so one real section is also the minimum the tooling
  accepts. The template is an HTML comment placed between the preamble and
  the real section, so no section's byte extent counts it. It shows the
  heading indented, never at the start of a line, so a line-regex reader
  cannot take it for a heading (the checker's markdown parse reads the
  comment as one html block either way). It departs from the manifest's own
  note, "do not put these ids into base.md itself", only by being a comment
  with no ids; the owner chose it.
- `agents-md/sections/*.md` keep their `##` heading and a one-line "None
  yet" body: the sync tests check that a requested section's heading arrives
  in the built `AGENTS.md`.
- `agents-md/eval-coverage.yml` keeps its header, the real section's row as
  the source has it (`covered`, fixture
  `evals/guidance/git-push-does-not-mean-commit-exists/` in `skills-evals`)
  and one `gap` row per empty section file (eight rows: the real section and
  seven sections); byte counts are regenerated.
- `docs/guidance-impact.md` keeps its header and entry format;
  `docs/agent-checks-lessons.md` keeps its header and table head.
- The agent-checks runner (`templates/agent-checks/agent-checks`, and the
  repo's own `.githooks/agent-checks`) keeps its events, the run log, the
  empty lesson slot (`check_lessons`) and `.agent-checks/local.sh`, and the
  `skill-shell` check exactly as the source has it (it refuses a `SKILL.md`
  or command file with a shell-command line, so it also guards the kit's
  own skill and skill template). Beside it is a commented-out example check
  (`.orig`/`.rej` leftovers). Removed checks: `rules-dir`, `secret-file`
  (path checks) and `staged-index`, `push-to-default`, `persistent-delete`
  (pre-push). The machinery's own ids remain: `repo-local`, `runner-error`,
  `tree-rev`.
  The PreToolUse guard (`agent-checks-guard.sh`) is machinery and is kept
  whole.

### X9: tests changed or removed

Moved to a test-only fixture (the test still exercises the same machinery;
the check it uses lives in the test):

- `test/test_agent_check_lane.py` (these use the template with the file's
  own `PATH_ARM` example lesson in place of the removed `secret-file` check,
  and they leave the real `skill-shell` check alone, so a run's `checks`
  list now starts with `skill-shell`): `test_wrong_expectation_is_reported`,
  `test_refusal_without_the_message_is_reported`,
  `test_tree_checks_every_path_of_a_revision`,
  `test_local_check_exiting_0_cannot_suppress_a_fleet_refusal`, and the
  run-log tests `test_passing_pre_commit_writes_one_line`,
  `test_refused_pre_commit_lists_the_check_and_keeps_the_message`,
  `test_repo_local_check_is_logged`, `test_ci_tree_and_unknown_events_write_nothing`,
  `test_unwritable_log_changes_nothing` use a fixture runner (the template
  with the file's own `PATH_ARM` example lesson) in place of the removed
  `secret-file` check.
- `test_case_that_already_refuses_under_base_fails` and the fleet-scan
  fixture's "already refused" repo use a repo-local `.agent-checks/local.sh`
  draft check for the base runner's own refusal.
- `test_pre_push_to_default_lists_push_to_default` becomes
  `test_refused_pre_push_is_logged_as_pre_push`: the run log's pre-push
  entry, from a fixture refusal on a feature branch.
- `CheckIdTests`: the core ids are the machinery's three and `skill-shell`,
  and the minimum `problem` call count is four, not seven.
- `test/fixtures/agent-checks-cases.jsonl` (the lane's case table):
  `ci-earlier-commit-caught` and `tree-refuses-committed-key` (renamed
  `tree-refuses-committed-draft`) keep their range and tree shape with the
  repo-local draft check standing in.
- `test/run-tests.sh`: "the delivered runner's ci mode refuses a committed
  .env" becomes "the delivered runner's ci mode runs the repo's own check",
  with a `.agent-checks/local.sh` committed in the test clone.
- `test/test-on-hold-label.js`: "fleet rule protects issues and PRs..."
  becomes "fleet payload stays under budget and matches its mirror", keeping
  the budget and mirror assertions; the test count stays nine.

Removed, because each locked only content the kit does not ship:

- Lane case table: `rules-dir-refused`, `claude-dir-other-file-passes`,
  `env-refused`, `env-local-refused`, `env-example-passes`, `pem-refused`
  and `ssh-key-refused` (the removed `rules-dir` and `secret-file` checks).

Restored for the one real check (owner, 2026-10-29 12:29 UTC): the six
`skill-shell-*` cases of the lane's case table
(`skill-shell-inline-refused`, `-fenced-refused`, `-command-file-refused`,
`-plugin-command-refused`, `-code-span-passes`, `-in-readme-passes`), which
the first blanking had removed with the check. The tests that asserted a
`checks` list or the set of check ids (`test_passing_pre_commit_writes_one_line`,
`test_refused_pre_push_is_logged_as_pre_push`, `test_repo_local_check_is_logged`,
`CheckIdTests`) name `skill-shell` again.
- `test/run-tests.sh`: "agent-checks persistent: deleting persistent/results
  is refused" (the removed `persistent-delete` check). The two cases beside
  it, a branch delete and a push to `persistent/*` passing, stay.
- `test/test-on-hold-label.js`: the `ON_HOLD_MUTATION=policy` branch, which
  mutated the removed on-hold rule's wording in `base.md`.

The skills registries ship one skill under the same entry (below).

### C11 in practice

`kit_repos` and `kit_owners` in `manifests/_common.yml` are the list; the
patches read it, and `tests/test_no_leaks.py::test_config_names_only_kit_repos`
fails on any other repo in a starter tree's `repos.yml`, `fleet.yml`,
workflows or `dependabot.yml` (parsed YAML, so comments, which are history,
are not checked).

- `_agent-guidance/repos.yml` (`patches/_agent-guidance/kit_repos.py`):
  `skills_bootstrap.repos` drops ai-usage-dashboard, claude-memory-map,
  fastmail-actions, scratch-claude-002 and verify-actions-lock;
  `skills_bootstrap.out_of_scope` drops scratch-claude-001, 4A, jc,
  rss-inator, scratch-jules-001, squarespacetemp and waiting-feed;
  `cron_coverage.fleet` drops ai-usage-dashboard, claude-memory-map,
  fastmail-actions and rss-inator; `cron_coverage.out_of_scope` becomes `[]`
  (every entry was a scratch repo, a fork or the third owner). Each entry
  goes with the comment that explained it. One `_agent-guidance` test pinned
  the list's size: `test/test-prompt-audit-sweep.js` ("sweep covers the
  declared fleet across both owners") now asks for at least 6 fleet repos,
  not 10.
- `repo-settings/fleet.yml` (`patches/repo-settings/kit_config.py`): drops
  retired-agentskills, claude-memory-map, squarespacetemp, fastmail-actions,
  ai-usage-dashboard, verify-actions-lock (the repo element; C5's `uses:`
  pins of the action are untouched), GHA-bench, rss-inator, jc, 4A,
  waiting-feed, security-alerts, the three scratch repos and the two forks.
- History mentions stay: script and workflow comments, ADRs, evidence, and
  `test/test-dependabot-config-health.js`, whose mocked fleet is the
  measured 2026-09 one (test data, not config).

Tests changed (`tests/test_repo_settings.py`):
- `RequiredStatusChecksFleetConfigTests.EXPECTED` and `PRE_EXISTING` lose
  the dropped repos' rows.
- `test_only_known_repos_declare_extras` expects skills-evals and
  `_agent-guidance` only.
- `test_other_repos_render_the_pre_migration_bypass` counts 8 repos, not 22.
- `test_the_mapping_is_one_for_one`: no repo is `manage: []` any more
  (`WERE_FALSE` is empty) and 6 repos are `manage: ["all"]`, not 19.
- `test_an_excluded_repo_is_given_no_labels` adds its own `manage: []`
  fixture element, since the kit's fleet excludes no repo.

Tests removed, because each locked a dropped repo's element:
`test_gha_bench_declares_deletion_only_persistent_ruleset` and
`test_security_alerts_declares_deletion_only_persistent_ruleset`.

### C12 in practice

`fleet.yml` writes the guidance sync App's id (default-branch bypass) as
`actor_id: "${GUIDANCE_SYNC_APP_ID}"` and the roster App's (skills-evals'
`roster/proposal` ruleset) as `"${ROSTER_WRITER_APP_ID}"`, the bootstrap's
names. The engine (`scripts/repo_settings.py`, `_actor_id`) reads that
environment variable when it renders a ruleset and refuses one that is unset,
empty or not a positive integer, so a missing id fails the plan instead of
dropping or inventing a bypass; a literal id still works. The
`repo-settings.yml` workflow maps both from repository variables in a
top-level `env:`; `schema.md` documents the form. The tests set invented ids
(900001, 900003) in `setUpModule`, and five new tests (`EnvAppIdTests`)
cover the variable, unset, empty and non-integer values, a literal id, and
the shipped fleet using both variables. The five ADRs that gave the
source's App ids beside the substituted App slugs now say `<roster App id>`
and `<guidance sync App id>` (`patches/repo-settings/app_ids.py`, which finds
the ids from the ADRs' "App id N" text, so the kit spells neither). This
reverses an earlier choice to keep them as history: paired with the
adopter's App names, the source's ids read as the adopter's.

### Changes to `export.py`

- Patches get `ctx["common"]`, the parsed `_common.yml` (an added key; every
  existing key is unchanged).

## Export record: wsl-automation

How `export.py` carries out X10 and the scheduling additions for
`wsl-automation` at 1c6893002aea500d43dcd25534a5fd7059c87b25. The manifest
(`manifests/wsl-automation.yml`) holds every exclusion and edit with its
`why`; the three patches (`patches/wsl-automation/`) say what they change in
their docstrings. This section records what is more than a substitution.
No change to `export.py` was needed.

### What X10 leaves out, and what stays

- **Left out** (37 files): the restic backup (`wsl-restic-backup.{ps1,sh}`,
  its excludes file, `Invoke-WslResticBackup`, `ConvertTo-WslMountPath`,
  `Invoke-RobocopyExe`, their tests); the session keeper and both launchers
  (`ensure-claude-session.ps1`, `Invoke-ClaudeSessionKeeper`, the session
  start/test/restore/snapshot helpers, `Test-CodexRemoteControl`,
  `Repair-WslUserRuntime`, the Windows Terminal argument builders, their
  tests); and, with no caller left, the backup lock functions. The manual
  `wsl --export` backup (`wsl-ubuntu-backup.ps1`, `Invoke-WslBackup`,
  `Test-WslActivity`, `Get-LastWakeTime`, their tests) goes too (notes Q2):
  it is the same backup feature, and it cannot run without the keeper's
  snapshot and the lock.
- **Kept** (notes Q1, the owner's decision): the usage census, the waiting
  feed, the ccstatusline sync, the Codex Cloud environment sync, the
  agent-trust scripts and `pull-repo.ps1`, with the checkout updater both
  sides share. `grant-keeper-batch-logon.ps1` keeps its name (the README and
  AGENTS.md cite it) and is reworded for the S4U tasks.
- **"Waiting on" the owner's name is "waiting on you"** in the feed script's header; its
  README section already said "waiting on you".

### Edits beyond substitution

- `Set-WslAutomationScheduledTasks` and `register-tasks.ps1`
  (`tasks_installer.py`): the backup, keeper and launcher tasks and their
  parameters go, including the two mandatory ones (`-BackupDir`,
  `-ResticRepoDir`, notes Q5), so `.\scripts\register-tasks.ps1` now needs no
  arguments. The Store-pwsh warning stays (it protects the two pwsh-based S4U
  tasks), and the explanation of the indefinite-repetition trigger moves to
  the ccstatusline trigger. The module manifest and loader drop the ten
  exported functions that are left out, and the `Backup` tag.
- Comments in kept code that named the keeper or the backup are reworded
  (notes Q3): `Get-WslAutomationDefaultPwshPath.ps1`,
  `New-WslAutomationRepoMutex.ps1`, `Update-CcstatuslineConfig.ps1`,
  `tests/CcstatuslineConfig.Tests.ps1`. `Register-WslScheduledTask.ps1`'s
  "interactive session launcher" and `Write-WslAutomationLog.ps1`'s example
  string stay; both read as general statements.
- `README.md` and `AGENTS.md` (`docs.py`): the sections about left-out code
  go (README: "Why staging", "Lock protocol", install step 3, five task
  sections, "Keeper semantics"; AGENTS.md: seven sections, notes Q4). The
  at-logon measurement is folded into "There is no WSL lifecycle event", and
  the transition-window lesson keeps its measurements without the backup's
  functions. The README gains "Session miner task" and "Linux and macOS"
  sections; AGENTS.md gains "Linux and macOS: one runner, three schedulers",
  which keeps the user-manager lesson (linger restarts a dead user manager)
  without the keeper's repair function or its incident link.
- **C12.** The waiting feed's fallback commit identity named the owner and
  carried a numeric GitHub user id; it is `Example Adopter` /
  `0+example-adopter@users.noreply.github.com`, and REGENERATE.md says to put
  the adopter's own there. The id itself is replaced by the common C12
  noreply rule (any `<digits>+<source login>@users.noreply.github.com`
  becomes `0+`), so the manifest no longer spells it.
- **Notes Q6.** The Codex Cloud bootstrap pin (a commit and a sha256 of the
  source registry's hook) becomes zeros that fail closed, with a comment;
  the README's link to that commit is reworded; REGENERATE.md gives the
  command.
- **Notes Q7.** The source owner's Windows repos folder, on another drive,
  becomes `C:\repos` (a repo rule ahead of the common `<drive>:` rule), because the agent-trust tests assert the default
  owner roots by value.
- `.gitattributes` drops the restic stanza; `.gitignore` gains the kit's
  junk lines; `ci.yml`'s `shell` job drops the restic ShellCheck targets and
  test step and gains the two new tests and their ShellCheck targets.

### Pester tests changed or removed

Removed with their subject (X10), as `patches/wsl-automation/pester_tests.py`
lists them: whole files `InvokeWslBackup`, `InvokeWslResticBackup`, `Keeper`,
`KeeperRestore`, `RepairWslUserRuntime` and `WslActivity` (`.Tests.ps1`) and
`wsl-restic-backup.test.sh`; in `Tasks.Tests.ps1`, 20 `It` blocks (backup,
keeper and launcher registration) and two `Context` blocks; in
`Helpers.Tests.ps1`, five `Describe` blocks (the backup lock and the launcher
argument builders); in `RepoUpdate.Tests.ps1`, the keeper and manual-backup
rows and the keeper's dirty-branch `It`.

Changed: every remaining `Set-WslAutomationScheduledTasks` call loses the two
removed arguments (32 calls); the S4U count is five, not six; the
no-interactive-principal check expects none; the history-failure check names
the census and ccstatusline tasks; the AST bootstrap test discovers every
`--exec /bin/bash ./<entry>.sh` action instead of naming two, so it covers
the session miner and any later Bash task. Added: three session miner `It`
blocks and its "already exists" `Context`.

**The Pester suite was not run.** No `pwsh` is installed where the export
was built, so neither Pester nor PSScriptAnalyzer ran against the exported
tree; `ci.yml`'s `test` and `test-linux` jobs are the first run. The bash
suite, ShellCheck over `ci.yml`'s list, and the ASCII check of every
`.ps1`/`.psm1`/`.psd1` did run.

### Added by the kit

- **The session miner task** (`Session Miner`, daily 07:17, S4U, 30 minute
  limit, `IgnoreNew`, no catch-up) runs `scripts/run-session-miner.sh`, a
  wrapper modeled on the census: the checkout bootstrap (interval 0), a
  shallow clone of skills-evals `main` whose `run_session_miner.sh` it runs,
  and one log line. The nine needs in the inventory notes map to: a
  KEY=value settings file read as data (needs 2, 3 and 5, because a
  `wsl.exe` action has no environment); fixed defaults for the denylist and
  the private registry clone (2, 3); preflight checks that exit before
  mining (denylist 2, clone origin 3 and 4, PyYAML 6); a refusal in CI or a
  cloud session (8); a scheduled run that refuses with exit 3 until a dry
  run by hand has succeeded (8); `--file-issues` refused unless
  `FILE_ISSUES=on` (8); in WSL without `WIN_HOME`, only the WSL transcripts,
  noted in the log line, rather than the untested `cmd.exe` lookup from a
  session 0 logon (5); a note when no state file exists yet (7); and a log
  that keeps only the miner's `session miner: ` lines and counts the rest,
  so a traceback's path never reaches it. Need 9 belongs to the skills-evals
  manifest: the miner's default registry paths and the marker string in
  `mine_sessions.py` are substituted there. The wrapper clones skills-evals
  to get the reviewed runner, which clones it again for the miner; the
  second shallow clone is the price of never running a local branch's
  runner.
- **Linux and macOS scheduling**: `scripts/unix/run-task.sh` (the per-run
  guard: PATH for a profile-less scheduler, `flock -n` overlap guard taken
  before the checkout update and held across the re-exec, coreutils
  `timeout` with the Windows task's limit, a native fast-forward-only
  checkout update that fails open, one log line) and
  `scripts/unix/install-schedule.sh` (systemd user service and timer with
  `Persistent=true` and an outer `TimeoutStartSec`; a launchd LaunchAgent;
  an idempotent crontab block). The census is the default job; the waiting
  feed and the session miner are opt-in with `--jobs`.
- **Tests**: `tests/run-session-miner.test.sh` (71 assertions) and
  `tests/unix-schedule.test.sh` (122 assertions since the second review's crontab fixes), both in `ci.yml`'s `shell`
  job and the manifest's suite. Each was checked to fail on a mutation of
  what it guards (the CI refusal, output withholding, the lock, the kill
  grace, `Persistent=true`). Neither ran on macOS: launchd output is checked
  by parsing the plists with `plistlib`, and bash 3.2 compatibility of the
  runner is by inspection only.
- `NOTICE` names the source repository and commit.

## Export record: agentskills

How `export.py` carries out the entries above for the source registry
(pinned d2d81b00c51388baf51c3922a540ae27839adcde, kit tree
`starter/agent-skills/`, repo name `acsi-agentskills`) and its private twin
(pinned c4bf812e4703e7b9fdd3f5408ed9198cba9df9be,
kit tree `starter/agent-skills-private/`, repo name `acsi-agentskills-private`).
Every edit, with its `why`, is in `manifests/agentskills.yml`,
`manifests/agentskills-private.yml` and `patches/agentskills/` (each
manifest's `source:` names the source repo).

### Left out

- **X9:** every skill of the public registry's four `plugins/*/skills/`
  folders (21, including the X1 Fastmail ones) but one, and the private
  registry's one skill (X2). The kept skill is `workflow-path-audit`
  (`acsi-coding-anywhere`), the single file `SKILL.md`: the source has no
  `PURPOSE.md` for it, and adding one would move its digest, so the kit does
  not (the registry's own rule is no mass backfill). The tests inside the
  skills that went went with them (`skills-doctor`,
  `launch-top-level-claude-session`, `migrate-claude-memory`,
  `sync-cc-settings-between-wsl-and-windows`, `rename-pdfs`). The template for
  your own skill is `templates/skill/` (`SKILL.md`, `PURPOSE.md`, and a
  `templates/README.md` saying how to adopt it), an overlay: it sits outside
  `plugins/*/skills/`, which every registry check, the lock generator and the
  README table read, so none of them sees it (checked by the registry's own
  suite).
- **X3:** `docs/credential-runbook.md` of the private registry. Never read
  into the kit.
- The plugins keep their manifests and machinery: `acsi-coding-local` keeps its
  clone-sync and credit-lane hooks and `bin/claude-credit`, which are not
  skills.

### Substitution details

- **The retired registry, bare.** In these two repos a bare `agentskills`
  (`acsi@agentskills`, the checkout `../agentskills`, `agentskills#55`, the
  marketplace key `"agentskills"`) names the retired predecessor. A manifest
  rule renames the bare word to `retired-agentskills` (and `-private`), as
  the common rule already does for the source owner's retired `agentskills`,
  so setup.sh sees the retired registry under a name of its own, distinct
  from the live `acsi-agentskills`, which reads as what it is.
  `agentskills.io` and `AGENTSKILLS_*` are
  untouched. The concatenated slug in `check_consistency.py`
  (`"<owner>/" + "agentskills"`) is caught by the same rule.
- Two hyphenated owner-name bundle labels the common rule's word boundary
  misses (a probe name and a test id) become `acsi`.
- The sync-delivered files (`fleet-guidance.md`, `.githooks/agent-checks`,
  `agent-checks-guard.sh`, `fleet-memory.sh`, `skills-bootstrap.sh`) are
  copied from the exported `_agent-guidance`, so they carry its X9 blanking
  and stay byte-identical to the copy its `repos.yml` pins. The managed half
  of each `AGENTS.md` is that tree's `build-agents-md.sh` output.
- Manifest owner and author names are the kit's `<your name>` placeholder;
  plugin descriptions and keywords describe each plugin's audience, not the
  skills the source shipped.

### C3, C4, C11 in practice

- C3: the federated `example-site-platform` marketplace entry, the
  dependabot cooldown `exclude` and the tests' `example-site-platform/`
  reusable carve-out are kept with the invented name.
- C4: `scheduled-run-health.yml` is kept (the inventory proposed dropping
  it) and calls `_agent-guidance`'s reusable at a forty-zero placeholder sha,
  as in `repo-settings`.
- C11: CI's census step cloned the platform repo and a consumer site; the
  clones and their two `scripts/skills_registries.yml` entries are removed
  (a clone of a missing repo fails the job), with the how-to left as a
  comment. The census keeps this registry and the optional private one.

### X9: machinery changed to accept an empty plugin

Three of the four plugins still start empty, so this stays (the owner's
2026-10-29 12:29 UTC decision added one skill to the fourth).

- `setup.sh` (public) refused a registry with no skills (`ERROR: no skills
  found`); it now prints that and goes on, since it links nothing any more
  (ADR 0017) and its settings and owner-machine steps still apply.
- `scripts/plugin_layout.json` keeps the four plugins and their
  `defaultEnabled`; `acsi-coding-anywhere` lists `workflow-path-audit`, the
  other three have `skills: []`.
- `generate_readme_table.py` refused a plugin with no skills ("renders no
  table rows"). It now renders one row, `none yet — add skills under
  plugins/<plugin>/skills/`, so the rule the refusal protected (every
  published bundle is named in the README) still holds.
- `ci.yml`'s two pytest steps, the AGENTS.md command and the pull request
  template prefix `shopt -s nullglob;`: with no skill tests the two plugin
  globs match nothing and pytest exits 4 on the literal. The kit's `suite:`
  command does the same.
- `skills.lock` is regenerated with the repo's generator (it lists the one
  skill); its ref is forty zeros until the adopter's first commit
  (REGENERATE.md).

### X9: tests changed or removed

Changed (the machinery is still tested):

- `test_check_consistency.py`: `test_the_layout_file_is_in_canonical_form`
  accepts an empty skill list (it asserted each list non-empty);
  `test_the_shipped_plugins_match_the_adr_0013_layout` treats a missing
  `skills/` folder (git keeps no empty folder) as no skills.
- `test_generate_readme_table.py`: `test_a_bundle_that_renders_no_rows_is_a_problem`
  becomes `test_a_bundle_with_no_skills_still_renders_one_row`;
  `test_problems_from_several_bundles_accumulate` and
  `test_build_rows_exits_nonzero_when_a_bundle_renders_nothing` use an
  unclassifiable (`gitlab`) entry as the bundle that renders nothing.
- `test_generate_skills_lock.py`: `test_the_source_limits_are_the_hooks_own`
  (cited by the vendored hook, defined in the left-out skills-doctor skill)
  is declared in
  `_TESTS_IN_ANOTHER_REPO`. (`test_this_repos_committed_lock_passes_check_format`
  had been changed to skip while the shipped lock listed no skills, since
  `--check-format` refuses an empty lock by design. The lock lists one skill
  now, so the test is the source's again and runs.)
- `test_setup_owner_machine.py`:
  `test_a_whole_directory_home_link_is_removed_only_when_it_is_ours` links
  the legacy home to `plugins/acsi-coding-local/hooks/` (a shipped folder under
  `plugins/`) instead of the absent `skills/`.
- `test_agent_checks.py`: each scratch repo gets a test-only copy of the
  runner (which ships `skill-shell`) with a lesson in its slot that refuses
  the paths the source's `secret-file` and `rules-dir` checks refused, so the
  pre-commit, pre-push, ci, installer and real-git tests run unchanged
  against it.

Removed, because each locked only a check the kit does not ship:

- `test_agent_checks.py`: `test_pre_push_refuses_the_default_branch` (two
  cases) and `test_pre_push_uses_the_remotes_recorded_default_branch`
  (`push-to-default`), and
  `test_pre_push_refuses_while_changes_are_staged_but_uncommitted`
  (`staged-index`).

### The private registry's suite

It has no tests; its one CI job runs the agent-checks runner over the
change. The kit's `suite:` runs that (`ci "" HEAD` and `tree HEAD`) and
`setup.sh` with a scratch HOME, which must succeed with no skills.

### Environment note

Two hook tests (`test_every_unsearchable_child_is_named_not_just_the_last`,
`test_an_unsearchable_child_with_an_unsafe_name_is_still_reported`) run
the hook as `nobody` through `setpriv` when the suite is root, so they need
the checkout's parent folders searchable. They fail the same way on the
unmodified source in a 0700 temp folder. The suite's `setup:` opens the
checkout and its parent (`chmod o+rx`) when run as root.

## Export record: skills-evals

How `export.py` carries out X4, X5, X6, X9, C4, C6, C7, C8, C11 and C12 for
`skills-evals` at 99c667f93c67a3ddd0630bf7aadbcb43e506d43c (which adds
`dashboard/`). The manifest (`manifests/skills-evals.yml`) holds every
exclusion, substitution and edit with its `why`; the patches
(`patches/skills-evals/`) say what they change in their docstrings. This
section records what is more than a substitution.

### What X4, X5, X6 and X9 leave out, and what stays

- Every skill fixture directory under `evals/` but one (24 of 25 skills;
  `evals/workflow-path-audit/`, with its four fixtures, stays), every
  guidance fixture but one (28 of 30; `evals/guidance/_delivery/` and
  `evals/guidance/git-push-does-not-mean-commit-exists/` stay), all five
  `evals/real-work/` seeds and `badges/workflow-path-audit.json` (a
  generated result, the owner's measurement). No results branch is copied
  (X6). The kept fixtures are byte copies after substitution. The guidance
  ones are excluded one directory at a time, so a new source section's
  fixture is not copied by accident.
- Kept as machinery: `evals/orchestrator-scenarios/` (the orchestrator's
  own scenarios, no skill), `evals/guidance/_delivery/`,
  `evals/guidance-bridge-canary/` and `evals/propagation/` (probes of the
  delivery path), the config files (`roster.yml`, `roster-policy.yml`,
  `gate-policy.yml`, `improve-policy.yml`, `budget.yml`), `harness/`,
  `scripts/`, every workflow and `dashboard/` with `config.example/` and
  `inputs.example/` (the private `config/` is not in the source tree).
- `evals/scheduled.yml` keeps the rows of the two kept fixtures, as the
  source has them (the skill's `required-check-routing` and the section's
  `hook-refused-commit`, readiness notes and all), so the weekly planner has
  something to run on day one; the other 30 rows are dropped.
  `evals/non-coverage.yml` ships `skips: []`. Each keeps its header
  (`patches/skills-evals/empty_config.py`). `scripts/plan_scheduled_evals.py`
  refused an empty schedule; it still accepts 0 to 256 entries, so an
  adopter who replaces the two sample rows with their own can pass through
  an empty list (the tests of that, `test_issue_scheduled_fixtures.py`, keep
  their X9 edit).
- `evals/real-work/README.md` (overlay) says the directory ships empty and
  how the fixture supply routine fills it.
- Symlinks: the source has 22. The 14 inside left-out fixture seeds went
  with them; the 5 `evals/orchestrator-scenarios/*/seed/bin/gh` links and the
  3 in the kept `evals/workflow-path-audit/*/seed/bin/` stay, and the
  invented `test/fixtures/fake-gh/seed/bin/gh` adds one (9 in all, each
  resolving to `harness/fakes/gh`).

### C8: the invented test-only fixtures

`overlay/skills-evals/test/fixtures/` holds small invented fixtures, laid out
as they would sit in a checkout, named after source skills only as test data
(four `writing-adrs`, four
`guidance/anything-you-name-gets-its-link` and one
`guidance/finding-your-unknowns` fixture, three pairwise `acsi-writing-style`
fixtures, two `github-actions-repo-settings` fixtures, one
`real-work/example-real-work-1` whose seed holds only an npm and a bundler
lockfile, example `non-coverage.yml` rows, and `fake-gh/`, a seed of invented
`gh` payloads for the shared fake). `test/example_evals.py` builds a scratch
git checkout (the repo's tracked files plus those fixtures in `evals/`, and a
four-fixture schedule) for the tests whose machinery reads a checkout's
committed fixtures: dispatch validation, the weekly planner, the improve and
guidance-improve fire checks, the skill census allowlist, `dev_setup.sh`'s
directory lists.

The test edits are 84 exact old/new pairs in
`patches/skills-evals/retargets.txt`, one section per file with its `why`
(24 files; the earlier "85 pairs, 22 files" had drifted). Edits that are more
than a path change:

- `test_issue_scheduled_fixtures.py`: the empty schedule moved from the
  bad-schema list to an assertion that it is valid.
- `test_issue_local_eval.py`: the left-out `ci-watcher-loops` checks are
  inlined.
- `test_issue_97.py`: four lines checking the left-out windows-elevation
  fixture's env block are dropped. (Two harness tests that read the scheduled
  workflow-path-audit fixture had been pointed at an invented copy; the real
  fixture ships now, so they are the source's again.)
- `run_tests.py`: the fake `gh` tests (`TestIssue84*`) drive
  `test/fixtures/fake-gh`; three expected payload strings are the invented
  seed's, the in-world allowlist is empty, and the fixture-wide scans (env
  blocks, `.git` globs) also cover test fixtures and nested `evals/`.
  `TestIssue81` reads the invented pairwise fixtures; its judge-call pin no
  longer reads the left-out README, and its end-to-end rank uses an
  invented candidate.
- `test_issue_followups.py`: follow-ups reaching both arms is checked on a
  fixture the test writes.
- `test_issue_64.py`: the committed non-coverage file must still load; the
  example rows are held to its rules.
- `test_issue_70_retirement.py`: `FIXTURE_ALIASES` is checked only for the
  skills the example checkout has (one).
- `test_issue_real_work_fixtures.py`, `test_issue_dev_setup.py`,
  `test_issue_board_feed.py`: read the invented real-work fixture; the
  floors are its counts (4 manifests, 7 classification cases).
- `test_issue_closed_loop_planner.py`: fire-check is pointed at the example
  checkout's `evals/`, since a module imported earlier in the same worker
  keeps the repository's.

`patches/skills-evals/machinery_tests.py` also prunes `test/shard_weights.json`
to the tests that still exist (40 keys to 17), keeping its serialization.

### Tests removed

Each locks content the kit leaves out. Single tests and classes are listed in
`patches/skills-evals/removed_tests.txt` (673 entries, each group commented):
581 in `test/run_tests.py` (the fixture-hardening classes of the left-out
skill fixtures, `TestIssue74`-`TestIssue92`, the vendor-release check, the
windows-elevation fixture and its stand-in `powershell.exe`, 74 tests of the
cms-stuck-pr-triage fixture's checks and rubric, 12 tests of the
owner's writing-style fixtures' own avoid list, facts and rubrics, which had
passed vacuously over an empty directory),
34 in `test_issue_124_guidance_gates.py`, 35 in `test_issue_89_fixture.py`,
13 in `test_issue_94.py`, 5 in `test_issue_real_work_fixtures.py` (the
committed fixtures' seeds, prompts and red/green runs), 2 in
`test_issue_371_scheduled_fixtures.py` (the source schedule's size and
measured bytes) and one each in `test_issue_64.py` (the test-canary row) and
`test_issue_followups.py` (rename-pdfs' follow-up text), and the whole
`TestIssue80Supersede` class, whose module stays as a helper (below).

**Restored after review (2026-10-29).** Seven tests the first pass removed
test machinery, not left-out content, and are back:
`TestIssue81::test_normalisation_leaves_a_sign_off_on_its_own_line`,
`test_every_committed_shape_keeps_the_sign_off_it_was_written_with` and
`test_the_signoff_survives_the_new_width` (sign-off normalisation, on
invented names: the one-word sign-off is `Alex`, so its expected tail is
`the meantime.\nAlex`); `RealWorkFixtureTests::test_each_fixture_loads_as_a_subject_agnostic_draft_or_promoted`
and `test_each_checker_is_the_pull_requests_own_test_file` (the invented
`example-real-work-1` gains `strip_agent_context`, a `repo_tests` check and a
`checker/` test file to satisfy them); `CommittedFixturesTests::test_the_committed_fixtures_pass_the_static_check`
(its floor of three committed fixtures is dropped: it checks whatever the
adopter's scaffolds commit); and `JudgeDiffTests::test_build_judge_diff_on_the_real_disarm_fixture_hides_prod_internals`
(it builds the invented `test/fixtures/evals/nested-repo-diff` seed, a bare
`prod.git` and a clone, instead of the left-out disarm fixture). The two
fixture-shape tests were checked to fail on a broken input (a fixture
without `strip_agent_context` or `checker/`). The 581 `run_tests.py` entries left were then re-audited
class by class: an AST walk of each removed test, its class, its base
classes and the helpers it calls finds the left-out fixture it reads in
every one. The verdicts, per class, are at the top of `removed_tests.txt`. The manifest leaves out 24 whole modules that test nothing else
(`test_issue_71_trigger_set`, `_75`, `_75_onboarding`, `_76` and its three
siblings, `_78`, `_80_why`, `_87`, `_88_fixture`, `_90`, `_91`, `_93`, the
three `bash_ci_*`, `ci_job_performance_browser_cache`, the three
`skills_doctor_*` and the three `vendor_release_*`) and their helper
(`test/vendor_release_fixtures.py`).

**Restored for the kit's two real fixtures (owner, 2026-10-29 12:29 UTC).**
The first pass left these out with the fixtures they read; the fixtures ship
now, so the tests are back as the source has them:

- The three `wpa_*` modules (`test_issue_wpa_companion_skip_pair`,
  `_moved_test_dir`, `_workflow_doc_in_step`) and their helper
  `test/workflow_path_audit_case.py`, which test the workflow-path-audit
  skill's other three fixtures. `_workflow_doc_in_step` imports the
  pattern-anchoring helpers of `test_issue_80_supersede`, so that module stays
  as a helper: its one test class, which locks the left-out
  `writing-adrs/supersede` fixture, is removed and its docstring says so. The
  runner requires every discovered module to contribute a test, so the helper
  gains `AnchoringHelperTests` (3 tests of `anchoring_problems` itself, added by
  `retargets.txt`).
- `ObjectiveAsymmetryTests` (3 tests, `test/run_tests.py`), which read the
  source `required-check-routing` fixture's seed anchors and eight checks.
- `PushFixtureTests` (3 tests, `test_issue_124_guidance_gates.py`), which run
  the `git push` section's `hook-refused-commit` fixture's trap, its bypass
  and its fix through the scorer. `FixtureLayoutTests::test_each_covered_section_has_fixtures_and_one_has_four`
  stays removed: it pins the source's thirty-section set.
- Retargets undone because the real fixture exists: the invented
  `test/fixtures/evals/workflow-path-audit/required-check-routing` is gone
  from the overlay, and `test_issue_202_harness.py`, `test_issue_66.py`,
  `test_issue_97.py` (two harness tests), `test_issue_71.py` (one
  copy of the fixture) and the `EVAL_DIR` and `FLAT_SOURCE` constants of
  `run_tests.py` and `test_issue_local_eval.py` read `evals/workflow-path-audit/`
  again. The example checkout (`test/example_evals.py`) now gets that
  fixture from the repository's tracked files and names it in its schedule.

Left as they were, because they test machinery on a fixture set the kit does
not have: `test_issue_scheduled_fixtures.py`, `test_issue_371_scheduled_fixtures.py`
(they read the example checkout and its schedule; the source schedule's size
and measured bytes are not the kit's), `test_issue_70_retirement.py` (the
`FIXTURE_ALIASES` loop runs for the skills the checkout has) and the dispatch
validation tests, which still run in the example checkout.

### C4, C6, C7, C12 in practice

- C4: `scheduled-run-health.yml` calls `_agent-guidance`'s
  `scheduled-run-health-reusable.yml` at a placeholder sha
  (REGENERATE.md).
- C6: `evals/budget.yml` drops the temporary-raise comment and sets
  `weekly_cap: 0.30`; the allowance-guard test asserts the loaded budget
  equals the standing limits.
- C7: `routine-eval-fire.yml` reads `ROUTINE_TRIGGER_ID` from the
  `routine-fire` environment (the job now names it), beside the bearer
  secret, as `bootstrap/set-secrets.sh` stores them; the C7 row says "repo
  variable", and an environment variable is the stricter scope the kit's
  bootstrap already uses. The step refuses a value that is not
  `trig_[A-Za-z0-9]{1,64}` before curl runs. Three tests were added
  (URL built from the variable, malformed values stop before curl, the job
  reads the environment) and three were updated. The routine prompt is
  committed as `docs/routine-prompt.md` (`inputs/skills-evals/`).
- C12: ADR 0003's App id is `<roster App id>`.
- `.gitignore` adds `.pytest_cache/`, `node_modules/` and `.DS_Store`, the
  paths `copy-starter.sh` skips that it did not already name.

### protect_except

Three files get their citation-shaped spans substituted because tests
compare them with substituted code: `test/issues/test_issue_70_retire.py`,
`scripts/improve_gate.py` and `test/issues/test_issue_real_work_miner.py`.

### Changes to `export.py`

- `inputs:` (additive): files kept under `export/inputs/<repo>/` are
  substituted and written into the tree.
- `_SENTENCE_PREFIX` is written without nested quantifiers that backtracked
  catastrophically on long comment runs; it matches the same prefixes.

### Suite result

Both runs used the same container, `bash scripts/dev_setup.sh`, then
`python3 test/run_tests.py --jobs auto` in a PID namespace, in a fresh git
repository. The unmodified source archive: 67 failed, 5835 passed, 73
skipped. The export: 67 failed, 4441 passed, 60 skipped. The 67 failing test
ids are the same set in both (`test_issue_202.py`, `test_issue_370.py`,
`test_issue_88.py`, `test_issue_eval_on_merged_roster.py`), so they come from
the source at this commit in this environment, not from the export. (An
earlier version of this record said they "fail the same way with `env -i`";
that was not what was measured.) Their causes were traced afterwards and are
in `starter/known-failures.txt`: this container's `/etc/gitconfig` has an
`includeIf` path starting with `~/` that git cannot expand under the tests'
HOME-less environment (202, eval_on_merged_roster); the container sets
`CLAUDE_CODE_REMOTE_SESSION_ID` (370); Python 3.13's `Path.resolve()` no
longer raises on a symlink loop (88; CI uses 3.12). With the gitconfig masked
and the variable unset, 202, eval_on_merged_roster and 370 pass. None is a
source defect; the first two are tests not hermetic against the machine. `bwrap` is present (`/usr/bin/bwrap`), so
no workspace-executing scorer test failed closed.

## Review fixes (2026-10-29)

Decisions taken, and departures from the entries above, while fixing a
review of the first export. Each is carried out by a manifest rule, a patch
or an overlay, never by editing a starter tree.

### The leak scan reads the whole kit, and private details stay outside it

- `export/tests/test_no_leaks.py` scans every file of the kit (`export/`,
  `docs/`, `bootstrap/`, `.claude/`, `README.md` and `starter/`), paths
  included, for the public tokens listed under `leak_scan:` in
  `manifests/_common.yml`: the source owners' logins and first name (also
  in the possessive form a file name may carry), the sites, the machine names, the Windows repos path, claude.ai
  trigger, session, environment, artifact, project and chat ids (the short
  artifact form too), Anthropic federation ids, numeric noreply ids other
  than `0+`, the left-out platform repo's name and email addresses. Only
  P2 citation spans, `export/manifests/` (the substitution rules must spell
  what they replace) and two counted per-file allowances are exempt.
- Private details (personal details that appear in no source repo) are not
  in the kit at all, so they cannot be in the test either. The test reads
  them at run time from `PRIVACY_DENYLIST` or `PRIVACY_DENYLIST_FILE`
  (outside the kit), the registry's `check_privacy_denylist.py` format, and
  reports hits as `path:line: pattern #N` only. With no list it fails when
  `CI=true` and is skipped locally; a planted-token test proves it can fail
  and that it never prints the match.
- C12 gains three common substitution rules: a numeric noreply id before a
  source login becomes `0+`; the numeric id written beside the source
  owner's login becomes `<user id>`; and the short claude.ai artifact form
  (`claude.ai/artifact/<id>`) becomes `.../EXAMPLE`.

### `actions.lock` keeps its publisher ids

`actions.lock` records each action owner's numeric id. The kept C5 action
is published by the source owner, so its entry carries that account's id.
An earlier fix set it to `owner_id: 0` with a common patch; a second review
reverted that (2026-10-29): the id belongs to the publisher of a shared,
public action, GitHub shows it to anyone, and `gh actions-lock --no-narrow`
writes it back on every regeneration, so a zero only made the lockfile
disagree with its own tool. The patch (`patches/_common/lock_owner_ids.py`)
and `export.py`'s `common.patches` hook that ran it are gone, and the id is
not on the private denylist's list of what the kit must not hold (above).

### Account ids are read from variables

- **Anthropic workload identity federation (C12).** `eval.yml`'s two token
  exchanges named the source's federation rule, organization, service
  account and workspace. They read four `skills-evals` repository
  variables (`ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`,
  `ANTHROPIC_SERVICE_ACCOUNT_ID`, `ANTHROPIC_WORKSPACE_ID`), checked by
  shape and passed to `jq --arg` (`patches/skills-evals/federation_vars.py`).
  A failed exchange logs the HTTP status and the error type only; it used
  to print the whole response body. The bootstrap registers the four as
  optional `fleet.env` keys and `REPO_VARIABLES` in a new `federation`
  group (create-environments.sh sets them when filled in; verify.sh checks
  them only with `--only federation`); `docs/evals-and-loop.md` has the
  set-up ("Workload identity federation"), and the quick start gains an
  optional step 25 (later steps renumbered 26 to 31). Two roster App
  confinement tests forbid any `vars.` reference outside `roster-pr`
  (`test_issue_202.py` `TestRosterAppTokenIsConfined`,
  `test_issue_eval_on_merged_roster.py` `TestRosterPrStillHoldsTheAppTokenAlone`);
  a retarget drops exactly the four federation references before that
  check, so any other `vars.` in the `eval` or `roster` job still fails it.
- **Other per-account ids found by a sweep of every tree:** a handoff
  note's ruleset id and two fixtures' real Claude Code session id in
  `_agent-guidance` (`patches/_agent-guidance/account_ids.py`), and the App
  ids in repo-settings' ADRs (C12 in practice, above).

### skills-evals' HANDOFF.md ships as a blank template

The source's `HANDOFF.md` is that repo's operating record: dated sessions,
the owner's decisions, links to the owner's issues and the owner's numeric
id. The kit ships an overlay with its headings and a one-line purpose
each. `AGENTS.md` keeps its pointer to it, and the pointer and
`docs/how-it-works.md` no longer link the source owner's status-board
issue: the adopter keeps their own, linked from HANDOFF.md.

### Live output links the adopter's own records

Text the adopter's bots publish (pull request bodies, impact entries, a
scaffold's tracking.json) linked issues in the source owner's repos, or
built links on the adopter's repo with the source's issue numbers.
`patches/_agent-guidance/owner_links.py` and
`patches/skills-evals/owner_links.py` point each at the decision record
in the adopter's own repo that holds the same rule (sync.sh: the README's
"The CLAUDE.md bridge"; the improvement loop: skills-evals ADR 0005;
removals: ADR 0014), or drop a "Part of ..." line, and set the scaffold's
tracking issues to null; the tests that pinned the old text assert that no
issue link is left. Left as they are: `harness/run_eval.py`'s and
`bump-consumer-locks.sh`'s citations in messages a person reads at the
terminal, and `scaffold_skill_gaps.py`'s links on the adopter's own repo,
whose body format `test_issue_60` parses.

### Routine specs fit an adopter

`patches/_agent-guidance/routine_specs.py` edits the guidance
centralization Routine (its stored-prompt snapshot and its source): the
fork control is "the forks your enumeration finds", the attached
repositories are the kit's six, counts are computed from the run's own
enumeration rather than carried from the source, the trigger id is the
adopter's Routine's own, and the source's measured history stays under
headings marked "(source system history)". The stored prompt's byte count
and sha256 are recomputed. The prompt-audit sweep's private-repo list is
the kit's. A new test, `test_routine_prompts_name_only_kit_repos`, extends
C11 to every routine prompt file: any `<owner>/<repo>` outside a marked
history section must be a kit repo.

### One-owner fleets

When `OWNER_TWO` equals `OWNER`, `bootstrap/lib/substitute.py` parses every
YAML file that names the second owner and drops the second owner's legs
that would otherwise duplicate the owner's (keys, step items, flow-list
elements, words in an owner list), checking that the result loads to
exactly the original's data minus those nodes. Before this, a one-owner
fleet got workflows with duplicate keys (`GH_TOKEN_<OWNER>` twice), which
a YAML loader rejects. `bootstrap/tests/check-one-owner.py` substitutes
every starter YAML file with one owner and loads each with a
duplicate-key-rejecting loader; with the collapse turned off it fails.

### Smaller fixes

- **Sentence case.** `export.py`'s `_at_sentence_start` capitalized "the
  owner" at the start of every wrapped line. A line that only wraps the
  sentence before it (same comment marker, no list bullet, previous line
  ending without sentence punctuation) is no longer a sentence start.
  `export/tests/test_sentence_case.py` covers it.
- **Names.** The wrapping scorer's sign-off example and the surname-only
  bio test data use the invented `Alex Rivera`. skills-evals ADR 0015's file
  name no longer carries the owner's name.
- **The federated marketplace example** describes an invented platform,
  not the source platform repo's stack.
- **Manifest names.** The two registry manifests are `agentskills.yml` and
  `agentskills-private.yml` (with their overlay and patch folders). Those are
  manifest keys, the names `--only` and `--skip` take, not the exported repo
  names (`acsi-agentskills`, `acsi-agentskills-private`); each keeps the
  source repo's name in `source:`.
- **macOS.** `copy-starter.sh` and `regenerate.sh` use no GNU-only flag
  (`realpath -m`, `sed -i` without a suffix, `sha256sum`); the kit's own
  test suites assume GNU tools (`docs/before-you-start.md`).

## Second review fixes (2026-10-29)

What a second, independent review found, and how each is carried out
(manifests, patches and overlays only; the starter trees are re-exported).
Departures from the entries above are marked **Departure**.

### The kit is committed whole, and `--check` reads git

- The repo-root `.gitignore`'s `**/[Bb]in/*` and `*.log` kept ten exported
  files out of git: `bin/claude-credit`, the five orchestrator scenarios'
  `seed/bin/gh` links, and the fake-gh seed's `bin/gh` and replay `.log`, in
  the starter and the overlay. A clone failed agent-skills' credit-lane
  tests and skills-evals' `TestIssue84*` and `test_issue_65`. Two scoped
  negations at the end of the root `.gitignore` re-include them under the
  kit; each tree's own `.gitignore` still applies inside it.
- `export.py --check` compares a fresh export with the tree git tracks (the
  index's paths, modes and blobs: `git ls-files -s` and `git cat-file
  --batch`), not with the disk, so an ignored or unstaged file fails it.
  Outside a git work tree it compares the disk and says so. The two labels
  in `diff_trees` were reversed and are swapped back ("only in the fresh
  export", "only in the committed tree").
- `export/tests/test_kit_tracked.py` fails on any file under the kit that
  git does not track (test-run junk aside), and proves on a scratch repo
  that an ignored `bin/` file and `.log` are caught and that the negations
  clear them.
- `export.py` gains `--skip REPO ...` (refused when another exported repo
  needs the skipped one), for CI below.

### The kit's tests run in CI

- `run-all-tests.sh` also runs `export/tests` and `export.py --check`.
  **Departure:** the check needs the source checkouts beside the kit's
  repo; where they are missing (any copy of the kit) it prints SKIP, not
  PASS or FAIL.
- `_agent-guidance`'s `ci.yml` `checks` job (no new job, so no ruleset
  change) gains a kit-changed detection step (the kit, the root
  `.gitignore`, `ci.yml`; it runs when it cannot tell), `actions/setup-python`
  (`v7.0.0`, locked in `actions.lock` beside the tags it already held) and
  one step that runs the kit's tests and `export.py --check`. The denylist
  reaches it through `env:` from the `PRIVACY_DENYLIST` repository secret;
  unset or blank, the step fails when the kit changed. `test/run-tests.sh`'s
  `test_ci_adoption_kit_step` (group `self_hosted`) locks the step's shape,
  runs the detection step on scratch git ranges and the check step with a
  blank and a set secret.
- `export/fetch_sources.py` fetches each manifest's source and import at
  its pinned commit (`git fetch --depth 1`, no credential) for that step.
  **Departure:** the private registry's source is private, so CI checks
  every tree but `agent-skills-private` (`--skip agentskills-private`, the manifest key); that
  tree is still leak-scanned and checked as tracked there, and fully
  checked by `run-all-tests.sh` where its source is.
- **Departure (the private scan):** the denylist scan skips exactly one
  line shape, `owner_id: <digits>` in a `.github/workflows/actions.lock`
  (see "`actions.lock` keeps its publisher ids" above): the owner's list
  holds the owner's numeric id, which is that public field's value.
- The leak scan decodes base64 (standard and URL-safe) and hex runs and
  searches the printable ones for the public tokens and the private list
  (a hit reads `<token> (encoded)`). skills-evals' fixture-scan test listed
  a Basic credential that decoded to the source owner's first name; it is
  `alex:hunter2` now (`patches/skills-evals/invented_credential.py`, which
  finds it by decoding and spells neither).

### Federation is optional for the weekly roster refresh

`roster-refresh.yml` dispatches `eval.yml` with `roster_only` weekly, and
the `roster` job minted an Anthropic token on every dispatch, so with the
four federation variables unset it failed every week.
`patches/skills-evals/federation_vars.py` gives the roster job's mint
`id: federation`: all four empty exits 0 with a `::notice::` and
`federation=off`, and "Refresh the model roster" then publishes no roster
and says so in the step summary. One set without the others, or a
malformed value, still fails, and so does the `eval` job's mint, which an
eval leg needs. `test/issues/test_issue_federation_optional.py` (overlay)
runs both steps' scripts under `bash -e` with a fake `curl`.
`docs/evals-and-loop.md` says what happens with the variables unset.

### Setup scripts

- `create-apps.sh --dry-run` writes no manifest file (it prints the two
  paths it would write); `install-machine.sh --dry-run` no longer fetches
  in an existing clone (a fetch writes its refs and `FETCH_HEAD`); it prints
  the fetch and the fast-forward it would do.
- wsl-automation `install-schedule.sh cron` refuses when `crontab -l` fails
  for any reason but "no crontab for <user>", and when the crontab has an
  unpaired, doubled or reversed BEGIN/END marker, changing nothing.
- `bootstrap/lib/common.sh` refuses bash older than 4.4 (`bash_version_ok`),
  before anything runs.
- `set-secrets.sh` reads the denylist's mode with `os.stat`, not GNU
  `find -perm /077`, which BSD find rejects.
- Each has tests: `bootstrap/tests/test-bootstrap.sh` (the dry runs write
  and fetch nothing, the guard, the mode warning both ways) and
  wsl-automation's `tests/unix-schedule.test.sh` (15 new assertions).

### `/adopt` runs from a copy of the kit

Claude Code loads `CLAUDE.md` (and `AGENTS.md` through its import) from
the starting folder and every folder above it, and the enclosing git
repository's root `.claude/settings.local.json`; the shared
`.claude/settings.json` is read from the starting folder only (Claude Code
memory and settings docs; checked with Claude Code 2.1.312 on a scratch
parent repo: the parent's `CLAUDE.md` and `settings.local.json` hook
applied in a subfolder, its `settings.json` hooks did not). So run from
inside the clone, `/adopt` carried the source owner's guidance, and run
from the clone's root, its `fleet-memory` and `skills-bootstrap` hooks.
The README's "Start here" and the quick start now say to copy the kit out
of the clone first, and the skill checks `git rev-parse --show-toplevel`
and stops inside a repository. The kit carries a copy of the repo's
`LICENSE` (the README linked `../../../LICENSE`, outside the kit);
`test_kit_tracked.py` holds it equal to the repo's.

The skill told Claude to `--mark` every browser step, but `status.sh`
accepts `--mark` only for `ask` steps; it now marks only those and re-runs
`status.sh` for the rest (steps 5, 6 and 25 are browser `verify` steps).
A bootstrap test holds the skill's `--mark` paragraphs to `ask` steps.

### Smaller fixes

- `docs/routines.md` names example environments, not the owner's.
- skills-evals: the judge-mode refusal's report cell and the propagation
  Routine's retirement plan no longer link the source owner's issues
  (manifest edits; the history header keeps its citation).
- acsi-agentskills: `install-agent-hooks.sh`'s owner list named the first owner
  twice (the source's two letter cases, folded by C2); one entry now, and
  `test_agent_checks.py` holds the list unique.
- `starter/known-failures.txt` lists the three `codex_cloud` unittest
  failures by id instead of `group:codex_cloud`. `run_suites.py` reads
  unittest failure headers and their `FAILED (failures=N, errors=M)`
  totals, and names a failed group only when the runner's table shows a
  failed assertion of its own; a group that "exited N without reporting a
  failed assertion" is named by its unittest ids, so a new failure there
  is a new id.

### Not changed

- The source owner's environment name (`My Whitelist`) still appears in
  `agent-guidance`'s routine specs (`docs/routines/fleet-worker.md`,
  `prompt-audit-sweep.md`) and its allowlist notes; the review named only
  `docs/routines.md`.

- The starter suites assume a PID namespace only (`unshare --pid --fork
  --mount-proc`), run as root or as an ordinary user. Two kinds of test
  drop to `nobody` when run as root: agent-guidance's `test_codex_cloud`
  preexec and agent-skills' `_run_hook_unprivileged`. Under `unshare --user
  --map-current-user`, `nobody` is unmapped. The three known codex_cloud
  ids then report as `ERROR` (a raised preexec) rather than `FAIL`, so the
  exact list does not excuse them. Two `test_generate_skills_lock`
  unsearchable-child tests fail as well. The second also fails when the
  clone sits under a directory `nobody` cannot traverse, such as a 0700
  temp dir. Neither is a kit defect, so neither is excused.

## Third review fixes (2026-10-29)

What a third review found, and how each is carried out. Departures from
the entries above are marked **Departure**.

### A copy of the kit in a shallow folder works

`export.py` computed `DEFAULT_SOURCES = KIT.parents[3]` at import, which
raised `IndexError` in a copy with fewer than four ancestors (`~/adopt-kit`
is `/home/u/adopt-kit`), and `run_suites.py` imports it, so every suite
failed in the README's own copy-out path. `fetch_sources.py` (`parents[2]`)
and `test_kit_tracked.py` had the same flaw. Now `DEFAULT_SOURCES` is
`None` when the ancestor is missing; `export.py` with no `--sources` and no
`KIT_EXPORT_SOURCES` exits 2 with a message saying so, and `fetch_sources.py`
asks for `--self`. `run-all-tests.sh`'s export check still reports SKIP in a
copy (it looks for the source checkouts itself). The LICENSE comparison runs
only when the repo root is a git work tree holding the kit at
`docs/reference/adopt-the-whole-system`, so a copy two folders below an
unrelated `LICENSE` no longer compares against it.
`test_a_copy_in_a_shallow_folder_still_imports` copies the entry points
under `/tmp` and imports `export`, runs `export.py --check` (exit 2, the
message) and `--help` of the other two scripts; it fails on the old code
(`IndexError: 3`).

### CI: a missing secret only fails when kit files changed

GitHub passes no Actions secrets to a Dependabot- or fork-triggered
`pull_request` run, and Dependabot's weekly github-actions bumps edit
`ci.yml`, a salient path, so the step that failed closed on a blank
`PRIVACY_DENYLIST` turned every such bump red. The detection step now also
outputs `kit_changed` (files under the kit changed; true when it cannot
tell). The check step, with the secret unset or blank:

- fails closed when `kit_changed` is true (a leak scan over kit files that
  checked nothing must not read as green), unless
- the pull request's author is `dependabot[bot]` (read from
  `$GITHUB_EVENT_PATH` with `jq`, never interpolated), which cannot add
  personal data: warn;
- when only `ci.yml` or `.gitignore` changed, warn.

In both warning cases the public scan, the other export tests and
`--check` still run; only the private half is skipped (`CI=false` for that
run makes its test skip, as it does locally). `kit_changed` reaches the step
through `env:`. **Departure:** a fork pull request that changes kit files
still fails (it cannot be told from a maintainer's by anything but its
secret), and is fixed by a maintainer pushing the branch. `test_ci_adoption_kit_step`
locks the new output, the `env:` entry and each case on scratch event files.

### No App id literal in the kit

C12 says the kit spells neither of the source owner's App ids, but
`patches/repo-settings/kit_config.py` and `manifests/skills-evals.yml` did.
The patch now finds both from `fleet.yml`'s own comments by shape
(`agents-md-sync GitHub App (App id N)`, `(App id N). GitHub checks
--match-head-commit`) and puts them into its old texts at run time; the
skills-evals ADR's id is a substitution rule matched by its context
("-roster`, App id N) and"). The exported trees are byte-identical
(`export.py --check`).

### The App manifest redirects to this machine

`create-apps.sh` used `https://example.com/` as `redirect_url`, which sends
the one-time manifest `code` (convertible into the App's private key) to a
host the adopter does not control. It is `http://127.0.0.1/` now: nothing
listens, the browser shows a connection error, and the adopter copies the
URL from the address bar. The prompt, the script header and
`docs/settings-and-apps.md` say so; a bootstrap test holds the manifest's
`redirect_url`.

### Running the tests as root

The README gave only the `--user --map-current-user` form, which as root
leaves `nobody` unmapped (see "Not changed" above). It and the starter
README give both forms and say `known-failures.txt` is calibrated for the
root form (measured as root).

### `OWNER_NAME` takes no apostrophe

The name lands in single-quoted shell
(`publish-waiting-feed.sh`) and PowerShell (`WslAutomation.psd1`) strings.
`bootstrap/lib/common.sh` and `bootstrap/lib/substitute.py` now refuse
`'`, with a message; bootstrap tests check both.

### The root `.gitignore` clears the whole kit

The two negations (`bin/*`, `*.log`) left the VisualStudio rules'
`[Ll]ogs/`, `out/`, `artifacts/`, `x64/`, `Debug/` and others still
dropping exported files. One negation, `!/docs/reference/adopt-the-whole-system/**`,
re-includes everything (it matches the excluded directories themselves, so
git looks inside them); four rules after it put the junk back
(`__pycache__/`, `*.py[cod]`, `node_modules/`, `.pytest_cache/`). Each tree's
own `.gitignore` still applies inside it. `test_the_real_root_gitignore_clears_everything_under_the_kit`
runs the repo's real file on a scratch repo (it fails on the old one).

### Smaller

- agentskills' `skills.lock` step used GNU `sed -i`; it is a `python3 -I -c`
  replacement now (BSD `sed -i` takes a suffix argument).
- The line above listing the owner's private details was cut to a generic
  sentence, and its pointer to a public test dropped.
