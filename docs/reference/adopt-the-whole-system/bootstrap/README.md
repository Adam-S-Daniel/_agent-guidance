# Bootstrap scripts for adopting the whole system

These scripts do the GitHub and machine setup from the guide
[Adopt the whole self-improvement system on your own account](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/reference/adopt-the-whole-system.md),
on your own account, with the GitHub CLI (`gh`). They do the part that can be
scripted. The rest happens in a browser, and the end of this file lists it.

Every script:

- reads one config file, `fleet.env` (`--config <path>`, default `./fleet.env`);
- has `--dry-run`, which makes the same reads and prints each write call
  instead of making it;
- is idempotent: it reads first and writes only what differs, so a second run
  prints `Nothing to change`;
- prints an HTTP status or a fixed message on error, never a response body;
- has a usage header (`<script> --help`).

You need bash 4.4 or newer, `gh` (logged in as the owner: `gh auth login`,
then `gh auth refresh -s workflow` so pushes can add workflow files), `jq`,
`python3` and `git`. `install-machine.sh` also needs `claude`.

## Order

| Step | Command | Guide section |
|---|---|---|
| 0 | `cp fleet.env.example fleet.env` and fill in `OWNER` and `PLAN` | 1, 2 |
| 1 | `./create-repos.sh` | 1, 3.1, 3.3, 11 |
| 2 | `./apply-rulesets.sh --all` | 3.1 (persistent ruleset: deletion and non-fast-forward only, until each repo's App exists) |
| 3 | `./create-environments.sh`: the environments, and the repository variables the workflows read (`REGISTRY_REPO`, `GUIDANCE_REPO`, `LESSONS_REPO` in `skills-evals`; `repo-settings`' mirror of `fleet.env`) | 3.4, 4.5, 6.5, 9.4, 10.5 |
| 4 | `./set-secrets.sh --only privacy --denylist ~/private/denylist`: before the sync delivers `agent-checks.yml`, or every `privacy` check fails | 6.5 |
| 5 | `./copy-starter.sh ../starter/repo-settings <you>/repo-settings` (then `../starter/agent-guidance`, `agent-skills`, `agent-skills-private` and `skills-evals` into the repos of the same name, as you reach each phase; `machine` and `cloud` are not repos) | 3.1 onward |
| 6 | Phase 2: `./create-apps.sh guidance-sync`, then install the App, then `./apply-rulesets.sh --all` (agent-guidance's persistent-branches ruleset now lets guidance-sync write `persistent/drift-report`) and `./create-environments.sh` (repo-settings' `GUIDANCE_SYNC_APP_ID`). | 3.1, 3.4, 4.5 |
| 7 | On each durable machine: `./install-machine.sh` (it reads `../starter/machine`; `--machine-dir <dir>` for another copy) | 2, 5.3, 6.2, 8.1 |
| 8 | Phase 4, once `agent-checks` has reported on a pull request: `./apply-rulesets.sh --all --with-required-check`. It records `REQUIRED_CHECKS=on` in `fleet.env`, so every later `apply-rulesets.sh` and `verify.sh` keeps the checks without the flag (`--without-required-check` turns them off again); then `./create-environments.sh` mirrors the key to `repo-settings`. | 3.1, 6.3 |
| 9 | Phase 7: `./create-apps.sh loop-writer`, install it, then `./apply-rulesets.sh --all` (skills-evals' persistent-branches ruleset now lets loop-writer write `persistent/eval-results`) and `./create-environments.sh` (repo-settings' `LOOP_WRITER_APP_ID`). Create the eval runner routine, then `./set-secrets.sh --only routine-fire` for `EVAL_ROUTINE_FIRE_BEARER` and `ROUTINE_TRIGGER_ID`. | 3.1, 9.4 |
| 10 | Phase 8: `./create-apps.sh lessons-reader` (for the optional roster refresh, `./create-environments.sh --with-optional` and then `./create-apps.sh --with-optional roster-writer`); on a paid plan, `./create-apps.sh settings-admin`, which lets `repo-settings`' `apply.yml` re-apply the rulesets on push and weekly (see `../starter/repo-settings/README.md`) | 3.4, 10.4 |
| 11 | Any time: `./verify.sh --phase <N>` after finishing phase N, which checks only what should exist by then; `./verify.sh` alone expects everything (and the required checks when `fleet.env` has `REQUIRED_CHECKS=on`) | all |

`create-apps.sh` with no App name walks through every App that is not yet in
`fleet.env`, in table order. Pass names to do one phase at a time.

## What each script does, and what it cannot

| Script | Does | Cannot |
|---|---|---|
| `fleet.env.example` | The one file you fill in: owner, plan, App IDs (filled by `create-apps.sh`). No secrets, ever. | |
| `create-repos.sh` | The five repos with the guide's visibility, a generated README as the first commit (it ends with a fixed marker line, so `copy-starter.sh` may replace it while it is unchanged), merge settings (merge commit only, auto-merge, delete branch on merge), Actions settings (fork-run approval for all outside collaborators on public repos, read-only default token that cannot approve, SHA pinning required), labels `agent-ready`, `on-hold`, `agent-authored`, `vendor-release`, `agent-check` (`LABELS` in `lib/common.sh`). | Change the visibility of an existing repo (it reports a mismatch and exits 1). Allow `pull_request_target` for `agent-checks.yml` (see the manual steps). |
| `apply-rulesets.sh` | `default-branch` and `persistent-branches` from `rulesets/` (or `--rulesets-dir <dir>`, such as a `repo-settings` checkout's `rulesets/`), PUT or POST by name, then a read-back compared with the file; drift fails the run. `--with-required-check` adds `rulesets/phase4/required-status-checks.json` and records `REQUIRED_CHECKS=on` in `fleet.env`; `--without-required-check` records `off`. `persistent-branches` is per repo: `skills-evals` gets loop-writer as its one bypass and `agent-guidance` gets guidance-sync, each once its App ID is in `fleet.env`; every other repo, deletion and non-fast-forward with no bypass. | Protect private repos on GitHub Free (GitHub does not enforce rulesets there; they are skipped). |
| `create-environments.sh` | Every environment, deployment branches limited to the default branch by a custom branch policy, you as required reviewer on `plugin-runtime-review`; the repository variables in `REPO_VARIABLES` (`lib/common.sh`), value on stdin, skipping a `fleet.env` key that is empty. | Create environments in private repos on Free (skipped, with the guide's alternative printed). Delete extra branch policies (it reports them). |
| `create-apps.sh` | Writes a manifest and an auto-submitting HTML form per App, exchanges the code you paste, stores the client ID (variable) and private key (secret) in the App's environment through stdin, records the App ID in `fleet.env`, prints the install URL and the repos to pick. | Click Create or install the App for you. Recover a lost key: generate a new one on the App's settings page. |
| `set-secrets.sh` | `EVAL_ROUTINE_FIRE_BEARER`, `ROUTINE_TRIGGER_ID` and `PRIVACY_DENYLIST` (validated: outside any git work tree, every line a valid regular expression), from hidden prompts or the file, on stdin. | Read the bearer from claude.ai: you copy it from the routine's API trigger. Compare an existing secret (GitHub never shows one); `--rotate` replaces them. |
| `copy-starter.sh` | Clones, copies a starter folder, fills `<you>`, `<your name>` and the ruleset App ID placeholders in text files and `<you>` in file and folder names (`plugins/<you>-coding-anywhere/`; a `<you>-` name prefix takes the owner lowercased, URLs keep its case; it never copies `__pycache__`, `*.pyc`, `.pytest_cache`, `node_modules` or `.DS_Store`; `OWNER` must match `^[A-Za-z0-9-]{1,39}$`), refuses to overwrite a different file unless `--force` (except a `README.md` that is still exactly the one `create-repos.sh` generated), commits on `starter/<folder>`, pushes, checks `git merge-base --is-ancestor`, opens a pull request. | Merge the pull request: review it yourself, above all anything under `.github/workflows/` (guide 6.3). |
| `install-machine.sh` | Merges `disableSkillShellExecution` and the `memory-home.py` Stop hook into `~/.claude/settings.json` (backup first), installs `memory-home.py`, runs the git-hook installer with `<you>` filled, then adds the marketplace and installs the plugins. If the marketplace add fails (the `agent-skills` starter pull request is not merged yet), it warns, says to merge it and rerun, and ends `Not finished`. | Install PyYAML (it warns), upgrade git to 2.54+, or set up the desktop scheduled task for memory curation. |
| `verify.sh` | Read-only PASS/FAIL/SKIP for every item above (`--phase N` checks only what phase N should have made, and SKIPs later items naming their phase; `--only <group>,...` checks only those groups, such as `rulesets` or `apps:loop-writer`); names of environment variables and secrets only, and each repository variable compared with the value `fleet.env` implies, printing neither. | See a secret's value, or check anything outside GitHub (routines, the cloud environment, installs). |
| `status.sh` | Read-only: where you are in the quick start. One line per step of `steps.json` (done, next, todo, or asked: done), then the next step, its time estimate and what to run. Script steps are checked with `verify.sh --only` or a contents read of a starter file on the default branch; steps nothing can see (an App installation, a routine, the cloud setup script, another machine) are recorded with `--mark <id>` in a state file outside every repo. The `/adopt` skill (`../.claude/skills/adopt/`) drives it. | See anything `verify.sh` cannot; decide an ask step for you. |

## Manual steps, with the page to visit

Replace `<you>` with your owner, `<repo>` with a repo name, `<slug>` with the
App slug `create-apps.sh` prints.

1. **GitHub plan** (only if you want protection on the private repos):
   https://github.com/settings/billing (personal) or
   `https://github.com/organizations/<you>/settings/billing`.
2. **Create each App**: open the `app-manifests/<app>.html` file the script
   wrote, click Create on GitHub's page, paste the redirect URL back.
3. **Install each App**: `https://github.com/apps/<slug>/installations/new`,
   "Only select repositories", the repos the script names (for
   `guidance-sync`, every repo in `repos.yml` too).
4. **Allow `pull_request_target`** on each public repo, for every workflow
   that uses it (GitHub turns it off by default from 2026-11-02, guide 6.3):
   `https://github.com/<you>/<repo>/settings/actions`. Record the setting in
   `repo-settings`. The workflows:
   - `agent-guidance` and every public repo the sync reaches: `.github/workflows/agent-checks.yml`
   - `agent-skills`: `.github/workflows/plugin-runtime-review.yml`
   - `agent-skills`: `.github/workflows/loop-disarm.yml`
   - `skills-evals`: `.github/workflows/scaffold-disarm.yml`
5. **Pin the actions** in each workflow you copy (guide 3.3):
   `git ls-remote https://github.com/<owner>/<action> 'refs/tags/<tag>^{}'`,
   or the lockfile with `gh actions-lock`.
6. **Routines** at https://claude.ai/code/routines: the eval runner (by hand in
   the web UI, with an API trigger: copy the bearer it shows once into
   `set-secrets.sh`, and the `trig_...` ID), then dashboard refresh, changelog
   watcher, memory curation and fleet worker, each run once by hand (guide
   9.4, 11). Paste each prompt from `agent-guidance`'s
   `docs/routines/<name>.prompt.txt` (in the kit,
   `../starter/agent-guidance/docs/routines/`).
7. **Cloud environment setup script** (guide 7.3): in Claude Code on the web,
   https://claude.ai/code, edit the cloud environment the routines use and
   paste the setup script that `bash ../starter/cloud/build-setup-script.sh --config fleet.env`
   writes to `../starter/cloud/cloud-setup-script.sh`. Check the project directory with `pwd` in a
   session first.
8. **Machine memory curation**: a scheduled task in the Claude desktop app, or
   cron running `claude -p "Run docs/routines/memory-curation.md"` from an
   `agent-guidance` checkout (guide 8.2).
9. **Approve plugin-runtime changes** on the `plugin-runtime-review`
   environment yourself, on the job page of the waiting run:
   `https://github.com/<you>/agent-skills/actions`. Never let an agent approve.
10. **Acceptance checks** (guide 3, 15): `git push origin main` from a clone is
    rejected with `GH013`; squash and rebase are greyed out.

## Names: from the guide, and the ones it leaves out

From the guide, unchanged:

- Repos: `agent-guidance`, `agent-skills`, `agent-skills-private`,
  `skills-evals`, `repo-settings`.
- Environments: `guidance-sync`, `privacy`, `loop-writer`, `routine-fire`.
- Variables: `GUIDANCE_SYNC_CLIENT_ID`, `LOOP_WRITER_CLIENT_ID`,
  `ROUTINE_TRIGGER_ID`. Secrets: `GUIDANCE_SYNC_PEM`, `LOOP_WRITER_PEM`,
  `EVAL_ROUTINE_FIRE_BEARER`, `PRIVACY_DENYLIST`.
- Apps and permissions: the 3.4 table. Labels: `agent-ready`, `on-hold`,
  `agent-authored`, and `vendor-release` and `agent-check`, which the
  changelog-watcher and fleet-worker routine specs read and write. Rulesets: `default-branch`, `persistent-branches`;
  required checks `agent-checks` and `privacy` (agent-skills-private:
  `agent-checks` only), plus `plugin-runtime-review` in agent-skills alone
  (`EXTRA_CHECKS` in `lib/rulesets.sh`), all `integration_id` 15368.
- Marketplace `<you>-skills`, plugin `<you>-coding-anywhere`.

Not named by the guide, so named here (change them in `lib/common.sh`):

- `lessons-reader`, `settings-admin`, `roster-writer` environments, with
  `<APP>_CLIENT_ID` / `<APP>_PEM`: the guide says each App's key lives in an
  environment but names only two; these follow its pattern for
  `guidance-sync` and `loop-writer`. `lessons-reader` and `roster-writer` live
  in `skills-evals`, whose workflows use them.
- `plugin-runtime-review` in `agent-skills`, with you as the required
  reviewer: the guide (10.5 step 6) asks for such an environment without a
  name; this is the reference registry's name.
- Repository variables `GUIDANCE_REPO` and `LESSONS_REPO` in `skills-evals`,
  beside the guide's `REGISTRY_REPO`: the draft lane files hook proposals in
  the guidance repo and reads lessons from the private registry, and names
  each repo the way the improve gate names the registry.

## Variables and secrets set by hand

Every `vars.NAME` and `secrets.NAME` a kit workflow reads is set by a script:
App client IDs and keys by `create-apps.sh`, `EVAL_ROUTINE_FIRE_BEARER`,
`ROUTINE_TRIGGER_ID` and `PRIVACY_DENYLIST` by `set-secrets.sh`, and the
repository variables by `create-environments.sh`. None is left to set by
hand. If you add a workflow that reads a value no script sets, list it here
as a list item, "- `NAME`: where it lives and how to set it";
`tests/check-refs.py` fails on any name that is neither
set by a script nor listed here, and on a name listed here that a script
already sets or nothing reads.

## Security

- No value from `fleet.env` or GitHub reaches a shell command unquoted;
  `fleet.env` is parsed, never sourced, and each key is validated.
- JSON bodies are built with `jq` and passed with `--input`.
- A private key, client secret, bearer or denylist goes to `gh` on stdin from
  `printf` (a builtin, so never in a process list) or from the file itself,
  and is never printed, logged or written to disk. The manifest conversion
  response is dropped as soon as the four fields are read.
- `copy-starter.sh` never pushes to the default branch, never force-pushes,
  and checks its commit is on the remote branch after the push.

## Tests

```bash
bash tests/test-bootstrap.sh
```

No network, no sleeps, no wall-clock. `tests/stubs/gh` is a stateful `gh`
stand-in that logs every argv (secrets are stored only as SHA-256), and
`copy-starter.sh` pushes to local bare repos through `url.insteadOf` in a
scratch `GIT_CONFIG_GLOBAL`. The fixtures in `tests/fixtures/machine/` are the
guide's own `install-agent-hooks.sh` (6.2) and `memory-home.py` (8.1). The
suite also fails if `rulesets/` and the copy in `../starter/repo-settings/`
(with the apply script in its `bin/`) differ by a byte, and
`tests/check-refs.py` (run by the suite) holds every workflow's variables and
secrets to the scripts that set them; `tests/check-branch-prefixes.py` holds
the routine docs, the gates and the disarm workflows to one set of loop
branch names.
