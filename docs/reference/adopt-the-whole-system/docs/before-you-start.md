# Before you start

## What you need

| Need | Why |
|---|---|
| Claude Max, ideally Max 20x | Cloud sessions and routines (scheduled and API-triggered cloud runs) work on Pro too, but the eval budget is a share of your plan's weekly allowance. On Max 20x, one eval fire reserves 3.5% of the week under the 30% cap. Pro's allowance is roughly a twentieth of that, so one fire would cost more than a week; Max 5x fits about two fires a week and never an improve fire. These figures are scaled from the owner's Max 20x, not measured on smaller plans; nothing here was tested on a Team plan. |
| Claude Code, current release | Hooks, plugins, skills. The eval harness pins nothing about the CLI and breaks loudly when a release changes behavior. |
| A GitHub account, and optionally a second | The exports name two owners throughout ([one owner or two](#one-owner-or-two)). |
| A GitHub plan you know | **Rulesets, required checks and environments are enforced on private repos only on a paid GitHub plan** (Pro for a personal account, Team for an organization). See [free or paid](#free-or-paid-github-plan). |
| One durable machine | Mining your own session transcripts, the waiting-on-you feed and machine memory curation never run in the cloud. |
| git 2.54 or newer on that machine | Config-based git hooks. Older git ignores them silently. |
| `gh`, `git`, `jq`, `python3` with PyYAML, bash 4.4+ | The bootstrap scripts use them, on Linux, WSL or macOS (where the system bash is 3.2: install a current one, e.g. with Homebrew, and run the scripts with it). They avoid GNU-only flags; the kit's own test suites (`run-all-tests.sh`, `bootstrap/tests/`) are run on Linux and assume GNU `sed` and coreutils. `gh` must be signed in as the owner (`gh auth login`, then `gh auth refresh -s workflow` so a push may add workflow files, and `gh auth setup-git`). |
| `node` and `npm` | `_agent-guidance`'s tests and the guidance improve lane. |
| PowerShell 7 (Windows only) | `wsl-automation`'s scheduled tasks. |

## What it costs

- **Hands-on setup:** the [quick start](quick-start.md)'s 31 steps come to
  about 40 commands and browser actions, roughly one to two days. The
  browser steps (creating and installing six GitHub Apps, saving six
  routines) and reviewing six starter pull requests take most of it.
- **Calendar time:** most of the rest is waiting on the eval budget, not
  work. `skills-evals`' `evals/budget.yml` gives all automated evals 30% of
  the weekly allowance (`weekly_cap: 0.30`), and reserves 0.035 for one eval
  fire (`per_run_cap` plus `per_run_overrun`) until it is measured. An
  improve fire is charged twelve eval fires at their measured mean, or
  twelve full reservations (0.42, more than the whole cap) while nothing is
  measured, so **no improve fire can go until eval runs have been
  measured**. The weekly planner runs on Tuesdays at 07:00 UTC, after the
  allowance week turns over (Monday 17:00 UTC). An improve fire needs four
  fixtures for its skill, and a fixture the loop adds stays off the schedule
  until it has two clean runs. The kit's core skills ship with their fixtures
  (`workflow-path-audit` has four), so the planner has something to run on day one, but
  an improve fire for your own skills needs your own first. Expect the first improvement merged or
  rejected one to two weeks after the first eval runs.
- **Running it:** evals run as routines on your plan, so they draw down the
  weekly allowance like any session, within the 30% cap and with a hard stop
  when the account meter reads 80% (`stop_when_account_above`). Set your
  own numbers in `evals/budget.yml` before the planner first runs. The fire
  API allows 30 fires an hour per routine and 100 an hour per account.

## The config file

`fleet.env` is the one file you fill in. Start from
[`bootstrap/fleet.env.example`](../bootstrap/fleet.env.example):

```bash
cp bootstrap/fleet.env.example fleet.env
```

| Key | Meaning |
|---|---|
| `OWNER` | Your user or organization, exactly as in your clone URLs. Required. |
| `OWNER_TWO` | The second owner the exports name, or the same as `OWNER` ([one owner or two](#one-owner-or-two)). |
| `OWNER_TYPE` | `user` or `org` (default `user`). An org creates Apps under `organizations/<OWNER>/settings/apps`. |
| `PLAN` | `free` or `paid`. Required. |
| `REVIEWER` | Who approves `fleet-apply` and `plugin-runtime-review` (default `OWNER`). |
| `OWNER_NAME` | Fills `Example Adopter` and `<your name>` in public plugin manifests and notices (default `OWNER`). Letters, digits, spaces, `.` and `-` only (no apostrophe). Use a public handle, never your legal name: the privacy check would trip on it. |
| `VISIBILITY_AGENT_GUIDANCE`, `VISIBILITY_SKILLS_EVALS`, `VISIBILITY_WSL_AUTOMATION` | Paid plan only: `public` (default) or `private`. |
| `APP_NAME_PREFIX` | Apps are named `<prefix>-<app>` (default `OWNER` lowercased). App names are unique across GitHub, at most 34 characters. |
| `REPOS_DIR` | Where `install-machine.sh` keeps its clones (default `~/repos`). |
| `*_APP_ID` | Filled by `create-apps.sh`. |

**Never put a secret in `fleet.env`.** No private key, client secret,
bearer or denylist: those go into GitHub secrets on stdin, through
`create-apps.sh` and `set-secrets.sh`. The scripts parse the file strictly
(it is never `source`d), and refuse an unknown key or a malformed value.

Keep `fleet.env` and the `app-manifests/` folder `create-apps.sh` writes out
of every repo you push. They hold no secret, but they hold your App ids and
names, and no repo needs them.

## One owner or two

The owner's fleet spans two GitHub accounts, so the exports name a second
owner everywhere a workflow or registry enumerates the fleet:
`_agent-guidance`'s `SYNC_OWNERS`, `repo-settings.yml`'s per-owner token
steps and matrix, `fleet.yml`'s `example-site-b`, the git-hook installer's
`includeIf` list, and more. `copy-starter.sh` replaces the second owner's
name with `OWNER_TWO`, and refuses a tree that names it while `OWNER_TWO` is
empty.

- **Two accounts:** set `OWNER_TWO` to the second. Install the three
  fleet-wide Apps there too (`create-apps.sh` makes them public for this,
  and prints the steps), and run `apply-settings.sh --owner <OWNER_TWO>`
  signed in as that account.
- **One account:** set `OWNER_TWO` to the same login as `OWNER`.
  `copy-starter.sh` then drops the second owner's legs from every YAML
  file before it fills in your name (`lib/substitute.py`, which parses each
  file and needs PyYAML for this step): the second token and plan steps,
  the second owner's env keys, its matrix entry, and its word in
  `SYNC_OWNERS` and `OWNERS`. What is left names you once, so no workflow
  repeats a key or a step id. Prose and scripts inside `run:` blocks keep
  their two-owner wording (a check for "either owner" still reads both
  tokens, which are now the same one); `fleet.yml`'s `example-site-b` keeps
  its entry under your login.

## Free or paid GitHub plan

`create-repos.sh` applies this from `PLAN`:

- **Free:** every repo is public except `acsi-agentskills-private`,
  `repo-settings` (and the optional `waiting-feed`), which are private and,
  on Free, unprotected: GitHub does not enforce rulesets there and offers no
  environments, so the `fleet-apply` environment is skipped and you run the
  settings apply from your own machine (`apply-settings.sh`) instead of
  through `repo-settings.yml`'s approved job. The engine logs the skip.
- **Paid:** the private repos get rulesets and environments too, and
  `_agent-guidance`, `skills-evals` and `wsl-automation` may be private.

## Where to work

Work from a copy of this kit outside any repo you push, with `fleet.env`
beside it. Every command in the quick start runs from the kit's folder.
Each script takes `--dry-run`, which makes the same reads and prints each
write instead; run it first every time. Each one is idempotent: a second
run prints `Nothing to change`.
