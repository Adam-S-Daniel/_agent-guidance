# Settings, environments and Apps

## The six repos

`bootstrap/create-repos.sh` creates `_agent-guidance`, `repo-settings`,
`acsi-agentskills`, `acsi-agentskills-private`, `skills-evals` and `wsl-automation`
under `OWNER` ([free or paid](before-you-start.md#free-or-paid-github-plan)
decides which are private), gives each a first commit (a generated README
that `copy-starter.sh` later replaces), and creates the two labels no
settings file declares: `agent-ready` (the fleet worker's queue) and
`on-hold` (paused by you; only you add or remove it). In `skills-evals` it also
creates the branch `persistent/eval-results` as an orphan commit holding only a
README: the weekly plan, the ingest, the fixture supply, the coverage census and
the usage census all fetch it, none of them creates it, and they fail on a brand-new
account without it (`verify.sh --only branches` checks it; the other
`persistent/*` branches are created by the script or routine that writes them).
`--with-optional` also
creates `waiting-feed`, the private repo the waiting-on-you feed publishes
to ([machines](machine.md#scheduled-jobs-wsl-automation)).

Every other setting is `repo-settings`' job.

## Repo settings as code

`repo-settings/fleet.yml` in your `repo-settings` is the desired state of
every repo, and `scripts/repo_settings.py` is the engine that applies it
([its README](../starter/repo-settings/README.md) has the full list). The
defaults give every repo:

- merge commits only (squash and rebase off), auto-merge on, branches
  deleted on merge;
- Actions: full-SHA pins required, a read-only default token that cannot
  approve pull requests, approval for every outside contributor's fork run;
- Dependabot alerts and security updates;
- the label `agent-authored`;
- the ruleset "default branch protection": a pull request for every change,
  merge commits only, the required check `agent-checks`, admin bypass, and
  the `guidance-sync` App as the one bypass actor, so the sync can push.

Each repo adds its own required checks, the job names its workflows really
publish:

| Repo | Required checks |
|---|---|
| `acsi-agentskills` | `privacy`, `pytest`, `pytest-windows`, `plugin-runtime-approval`, `agent-checks` |
| `skills-evals` | `test`, `agent-checks` |
| `_agent-guidance` | `test`, `agent-checks` |
| `wsl-automation` | `shell`, `test`, `test-linux`, `agent-checks` |
| `repo-settings` | `agent-checks`, `unit` |
| `acsi-agentskills-private` | `agent-checks` |

`skills-evals` also gets the `eval-scaffold` label, a ruleset on
`roster/proposal` that only the `roster` App may push, and, with
`_agent-guidance`, a deletion-only ruleset on `persistent/**`.

**`pull_request_target`.** GitHub blocks that event on public repos by
default from 2026-11-02 unless an Actions policy allows it. `fleet.yml`
allows it for exactly one workflow,
`acsi-agentskills`' `.github/workflows/plugin-runtime-review.yml` (it publishes
the required `plugin-runtime-approval`), and the engine applies that policy with the
rest. A trigger added to that workflow must be added to the policy first.

`fleet.yml` names the two bypass Apps by id as `"${GUIDANCE_SYNC_APP_ID}"`
and `"${ROSTER_WRITER_APP_ID}"`, read from `repo-settings`' repository
variables, and the engine refuses to plan while either is unset. That is
why the Apps come before the first apply.

After the first apply, `repo-settings`' `repo-settings.yml` reconciles on
every merge and on a schedule: a read-only `diff` job with the
`settings-read` App, then an apply job behind the `fleet-apply`
environment's approval with the `settings-admin` App.

## Environments

`bootstrap/create-environments.sh` creates the three the workflows name:

| Repo | Environment | Protection | Holds |
|---|---|---|---|
| `repo-settings` | `fleet-apply` | you approve; default branch only | `REPO_SETTINGS_APP_PRIVATE_KEY` |
| `acsi-agentskills` | `plugin-runtime-review` | you approve; no branch policy (it runs on `pull_request_target`) | nothing: the approval is the gate |
| `skills-evals` | `routine-fire` | default branch only | `EVAL_ROUTINE_FIRE_BEARER`, `ROUTINE_TRIGGER_ID` |

It also sets `repo-settings`' variables `GUIDANCE_SYNC_APP_ID` and
`ROSTER_WRITER_APP_ID` from `fleet.env`, once `create-apps.sh` has filled
them, so run it again after the Apps. On `PLAN=free` the private
`repo-settings` has no environments: `fleet-apply` is skipped and you apply
from your machine.

**Never approve `plugin-runtime-review` for an agent, and never let one
approve it.** It holds a pull request that changes how a plugin runs code
(hooks, `bin/`, a package install, plugin settings) until you have read it.

## GitHub Apps

The workflows mint short-lived tokens from six Apps; nothing uses a personal
token. `bootstrap/create-apps.sh` creates each through GitHub's App
manifest flow: it writes `app-manifests/<app>.html`, you open it in a
browser signed in as the owner and click Create, and paste back the URL
GitHub redirects to (`http://127.0.0.1/?code=...`: the page cannot load, so
copy it from the address bar; the one-time code never goes to another site). The script records the App id in `fleet.env` first,
then stores the client id and private key where the workflows read them,
on stdin, never printed.

| App | Permissions | Installed on | Client id | Private key |
|---|---|---|---|---|
| `guidance-sync` | contents, pull requests, issues: write; actions, checks, metadata: read | every fleet repo | variable `APP_CLIENT_ID` in `_agent-guidance` | secret `APP_PRIVATE_KEY` in `_agent-guidance` |
| `settings-admin` | administration, actions, issues: write; metadata: read | every fleet repo | variable `REPO_SETTINGS_APP_CLIENT_ID` in `repo-settings` | secret `REPO_SETTINGS_APP_PRIVATE_KEY` in `fleet-apply` |
| `settings-read` | administration, actions, issues, metadata: read | every fleet repo | variable `REPO_SETTINGS_READ_APP_CLIENT_ID` in `repo-settings` | secret `REPO_SETTINGS_READ_APP_PRIVATE_KEY` in `repo-settings` |
| `eval-improve` | contents, pull requests: write | `acsi-agentskills`, `_agent-guidance` | secret `EVAL_IMPROVE_APP_CLIENT_ID` in `skills-evals` | secret `EVAL_IMPROVE_APP_PEM` in `skills-evals` |
| `roster` | contents, pull requests: write | `skills-evals` | variable `ROSTER_APP_CLIENT_ID` in `skills-evals` | secret `ROSTER_APP_PRIVATE_KEY` in `skills-evals` |
| `registry-read` | contents: read | `acsi-agentskills-private` | variable `REGISTRY_READ_APP_CLIENT_ID` in `skills-evals` | secret `REGISTRY_READ_APP_PEM` in `skills-evals` |

`guidance-sync` is the owner's `agents-md-sync` App: the sync, the nightly
drift report, the skills-lock bumper and the Dependabot sweep all use it.
`roster`'s id is `ROSTER_WRITER_APP_ID`. `eval-improve`'s client id is a
secret because its gates check that both values exist before minting.
App names are `<APP_NAME_PREFIX>-<app>`.

A lost private key cannot be recovered: generate a new one on the App's
settings page and store it with the `gh secret set` command
`create-apps.sh` prints for a missing key.

## Installing the Apps

Installing is a browser step: open each
`https://github.com/apps/<slug>/installations/new` link `create-apps.sh`
printed, choose "Only select repositories", and pick the repos the table
names. For the three fleet-wide Apps, that is the six repos and every
consumer you list in `_agent-guidance`'s `repos.yml`. With a second owner,
the three are public Apps; sign in as that owner and install them there too.
`bash bootstrap/verify.sh --only installs:<app>` checks an installation.

## The first apply

`bootstrap/apply-settings.sh` clones your `repo-settings`, checks that every
repo `fleet.yml` declares for the owner exists (until you remove the
example platform and its sites, it refuses;
[example repos](generated-files.md#example-repos-in-fleetyml)), runs the
engine's `apply` with your gh login, and reads back with the engine's
`diff`, which must report no drift. `--dry-run` shows the engine's own
preview; `--check` is the read-back alone (`verify.sh --only settings`).
With a second owner, sign gh in as that account (`gh auth switch`) and run
it again with `--owner <OWNER_TWO>`.

From then on, a pull request is the only way to change a default branch.
Check it: `git push origin main` from a clone is rejected with `GH013`, and
squash and rebase are greyed out on a pull request.

## Merging the starter pull requests

`copy-starter.sh` gives a starter your names (every form of the kit's
invented owners: `example-adopter`, `EXAMPLE_ADOPTER`, `example_adopter`,
the second owner's forms, `Example Adopter` and `<your name>`), refuses to
overwrite a file that differs on your default branch unless you pass
`--force`, and opens a pull request from `starter/<folder>`. It never
pushes to a default branch.

Review each one before merging: it is a whole repo, and its workflows run
on merge. Some checks on a starter pull request cannot pass yet, because
they read a generated file that still holds the export's placeholder, or
because they come from a workflow the base branch does not have yet:

- `acsi-agentskills`' `pytest` (its `skills.lock` checks) and every check that
  reads a `skills.lock`: fixed by step 14;
- `acsi-agentskills`' `plugin-runtime-approval`: it runs on
  `pull_request_target`, which uses the base branch's workflow, and an empty
  repo has none, so it never reports on the first pull request;
- anything that verifies `actions.lock` against GitHub, the hook pin or the
  scheduled-run-health pin: steps 15 to 18.

Nothing is protected before step 20, so these do not block the merge. Any
other failure is a real one: read it before you merge.

Until step 20, `repo-settings.yml`'s apply job waits for your approval on
each merge to `repo-settings`; reject those deployments until then.
