# Quick start

The easiest path is the guided one: copy the kit's folder out of the repo it
came in, to a place outside any git repository (the
[README](../README.md#start-here) says why), run `claude` in the copy and type
`/adopt`. It reads the real state of your account with
[`bootstrap/status.sh`](../bootstrap/status.sh), explains the next step and
runs each script with you, dry run first. To see where you are without it,
run `bash bootstrap/status.sh` yourself. The table below is the same list
([`bootstrap/steps.json`](../bootstrap/steps.json)).

Every command runs from the kit's folder, with your config at `./fleet.env`.
`<you>` is your `OWNER`. Each script takes `--dry-run`; run it first every
time. Each is idempotent: a second run prints `Nothing to change`.

A starter pull request carries the export's whole tree, including workflows
that run as soon as it merges. Review it, above all `.github/workflows/`,
before you merge. Nothing is protected until step 19 applies `fleet.yml`, so
a check that cannot pass yet does not block a merge;
[merging the starter pull requests](settings-and-apps.md#merging-the-starter-pull-requests)
lists the failures to expect. Do not add collaborators before step 19.

<!-- quickstart:begin -->
<!-- generated from bootstrap/steps.json by bootstrap/render-quickstart.py: edit steps.json, then rerun it -->

| Step | What | Run | Time | Read |
|---|---|---|---|---|
| 0 | Write fleet.env | `cp bootstrap/fleet.env.example fleet.env`, fill in `OWNER`, `OWNER_TWO` and `PLAN` | 5 min | [before you start](before-you-start.md#the-config-file) |
| 1 | Create the six repos | `bootstrap/create-repos.sh` | 5 min | [settings](settings-and-apps.md#the-six-repos) |
| 2 | Store the privacy denylist | `bootstrap/set-secrets.sh --only privacy --denylist <file outside every repo>` | 10 min | [skills](skills.md#the-privacy-check) |
| 3 | Copy repo-settings and merge it | `bootstrap/copy-starter.sh starter/repo-settings <you>/repo-settings`, then review and merge the pull request | 20 min | [settings](settings-and-apps.md#merging-the-starter-pull-requests) |
| 4 | Create the environments | `bootstrap/create-environments.sh` | 3 min | [settings](settings-and-apps.md#environments) |
| 5 | Create the six GitHub Apps | `bootstrap/create-apps.sh` (GitHub's App manifest flow, once per App, in the browser) | 30 min | [settings](settings-and-apps.md#github-apps) |
| 6 | Install the Apps | Open each `https://github.com/apps/<slug>/installations/new` link `create-apps.sh` printed and pick the repos it names | 15 min | [settings](settings-and-apps.md#installing-the-apps) |
| 7 | Copy the App ids into repo-settings | `bootstrap/create-environments.sh` again | 2 min | [settings](settings-and-apps.md#github-apps) |
| 8 | Drop the example repos from fleet.yml | In a pull request on `<you>/repo-settings`, remove the `example-site-*` entries from `repo-settings/fleet.yml` and their rows in `tests/test_repo_settings.py` (unless you have those repos) | 15 min | [settings](generated-files.md#example-repos-in-fleetyml) |
| 9 | Copy _agent-guidance and merge it | `bootstrap/copy-starter.sh starter/agent-guidance <you>/_agent-guidance`, then review and merge | 30 min | [guidance](guidance.md#what-_agent-guidance-holds) |
| 10 | Drop the example repos from repos.yml | In a pull request on `<you>/_agent-guidance`, remove the `example-site-*` names from `repos.yml` `cron_coverage.fleet` (unless you have those repos) | 10 min | [guidance](generated-files.md#example-repos-in-fleetyml) |
| 11 | Copy the two registries and merge them | `bootstrap/copy-starter.sh starter/agent-skills <you>/agentskills` and `bootstrap/copy-starter.sh starter/agent-skills-private <you>/agentskills-private`, then review and merge | 30 min | [skills](skills.md#the-registry) |
| 12 | Copy skills-evals and wsl-automation and merge them | `bootstrap/copy-starter.sh starter/skills-evals <you>/skills-evals` and `bootstrap/copy-starter.sh starter/wsl-automation <you>/wsl-automation`, then review and merge | 30 min | [evals](evals-and-loop.md#what-skills-evals-holds) |
| 13 | Pin every skills.lock to your registry | `bootstrap/regenerate.sh skills-locks`, merge, then run it again for the other four repos | 15 min | [generated files](generated-files.md#the-skills-locks) |
| 14 | Pin the skills-bootstrap hook | `bootstrap/regenerate.sh hook-pin`, then merge | 5 min | [generated files](generated-files.md#the-skills-bootstrap-hook-pin) |
| 15 | Pin the scheduled-run-health reusable | `bootstrap/regenerate.sh run-health-sha`, then merge the three pull requests | 5 min | [generated files](generated-files.md#the-scheduled-run-health-pin) |
| 16 | Pin the Codex Cloud bootstrap | `bootstrap/regenerate.sh codex-pin`, then merge | 5 min | [generated files](generated-files.md#the-codex-cloud-pin) |
| 17 | Regenerate the action lockfiles | `gh extension install github/gh-actions-lock`, then `bootstrap/regenerate.sh actions-lock` and merge what it opens | 10 min | [generated files](generated-files.md#the-action-lockfiles) |
| 18 | Point eval.yml at a fixture you have | In a pull request on `<you>/skills-evals`, change `.github/workflows/eval.yml`'s `fixture` default to a fixture under `evals/` (or add one) | 10 min | [generated files](generated-files.md#the-evalyml-fixture-default) |
| 19 | Apply fleet.yml: protect every repo | `bootstrap/apply-settings.sh` (and `--owner <OWNER_TWO>` signed in as that account) | 5 min | [settings](settings-and-apps.md#the-first-apply) |
| 20 | Set up each durable machine | On each one: `bootstrap/install-machine.sh` (add `--with-codex` if you use Codex) | 15 min | [machine](machine.md#install-on-each-durable-machine) |
| 21 | Schedule the machine jobs | Windows: `./scripts/register-tasks.ps1` in an elevated PowerShell 7, in your wsl-automation clone; Linux or macOS: `bash scripts/unix/install-schedule.sh <systemd\|launchd\|cron>` | 30 min | [machine](machine.md#scheduled-jobs-wsl-automation) |
| 22 | Set up your cloud environments | At https://claude.ai/code, give each cloud environment the setup script from agentskills' `docs/multi-repo-delivery.md` and the network allowlist from _agent-guidance's `docs/reference/network-allowlist-claude-environments.txt` | 20 min | [machine](machine.md#cloud-environments) |
| 23 | Create the eval runner routine | At https://claude.ai/code/routines: an API-triggered routine with the prompt in skills-evals' `docs/routine-prompt.md`; keep the bearer it shows once and its `trig_...` id | 20 min | [routines](routines.md#the-eval-runner) |
| 24 | Store the eval runner's bearer and trigger id | `bootstrap/set-secrets.sh --only routine-fire` | 5 min | [routines](routines.md#the-eval-runner) |
| 25 | Optional: let eval.yml trade OIDC for an Anthropic token | In the Anthropic Console, set up workload identity federation for `skills-evals`, put its four ids in fleet.env, then `bootstrap/create-environments.sh` | 20 min | [routines (optional)](evals-and-loop.md#workload-identity-federation) |
| 26 | Create the fleet worker routine | At https://claude.ai/code/routines: hourly at :20, the GitHub connector, the prompt in docs/routines.md | 15 min | [routines](routines.md#fleet-worker) |
| 27 | Create the prompt-audit sweep routine | At https://claude.ai/code/routines: API trigger only; keep its fire URL and bearer for the next step | 15 min | [routines](routines.md#prompt-audit-sweep) |
| 28 | Create the agent changelog watcher routine | At https://claude.ai/code/routines: daily at 01:03 UTC, in an environment that holds the sweep's fire URL and bearer | 20 min | [routines](routines.md#agent-changelog-watcher) |
| 29 | Schedule memory curation | A weekly task from a claude.ai project thread, and the same on each durable machine (docs/routines.md has both prompts) | 15 min | [routines](routines.md#memory-curation) |
| 30 | Create the guidance centralization audit routine | At https://claude.ai/code/routines: daily at 08:00 UTC, the GitHub connector, the prompt in _agent-guidance's `docs/routines/guidance-centralization.routine.md` | 20 min | [routines](routines.md#guidance-centralization-audit) |
| 31 | Optional: the project dashboard | Follow skills-evals' `dashboard/README.md` (Adopting it), then schedule its refresh every 2 hours | 30 min | [routines (optional)](routines.md#dashboard-refresh) |
| any | Check | `bootstrap/status.sh` for where you are; `bootstrap/verify.sh` for the fast checks, `--only settings` or `--only regen:<task>` for the slow ones | | [quick start](#where-you-are) |
<!-- quickstart:end -->

## Where you are

`bash bootstrap/status.sh` prints one line per step: `done`, `next`, `todo`,
`optional`, or `asked: done`. Steps marked `files` or `verify` in
`steps.json` are checked against GitHub every time. Steps nothing can see
from here (an App installation is visible, but a routine saved at claude.ai,
a pasted setup script or another machine are not) are `ask` steps:

```bash
bash bootstrap/status.sh --mark 23     # after you saved the eval runner routine
bash bootstrap/status.sh --unmark 23   # if you marked it by mistake
```

Your answers live in a state file outside every repo
(`~/.local/state/adopt-the-whole-system/<owner>.json`).

`bash bootstrap/verify.sh` checks the fast groups (repos, labels,
environments, variables, Apps and their installations, secrets by name);
`--only settings` and `--only regen:<task>` clone repos to check the
settings engine's drift and the regenerated files.
