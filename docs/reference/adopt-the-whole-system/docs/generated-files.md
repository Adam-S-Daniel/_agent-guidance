# Generated files and placeholders

The export rebuilt every generated file it could offline. A few need a
GitHub API, a commit that exists only once you have pushed, or your own
Apps, so they arrive holding a placeholder or the owner's value, and
[`export/REGENERATE.md`](../export/REGENERATE.md) lists them.
`bootstrap/regenerate.sh <task>` rebuilds each with the repo's own tooling
and proposes the result as a pull request on `regenerate/<task>`;
`--check` reads your default branches and says whether it is done (that is
what `status.sh` asks). The steps below run after the starter pull requests
are merged, before the first settings apply.

Every item `REGENERATE.md` lists, and the quick start step that handles it:

| Repo | Item | Step |
|---|---|---|
| `_agent-guidance` | `skills.lock` | 13 |
| `_agent-guidance` | `repos.yml` `skills_bootstrap.ref` | 14 |
| `_agent-guidance` | `.github/workflows/actions.lock` | 17 |
| `repo-settings` | `.github/workflows/scheduled-run-health.yml` | 15 |
| `repo-settings` | `skills.lock` | 13 |
| `repo-settings` | `.github/workflows/actions.lock` | 17 |
| `repo-settings` | GitHub App ids | 7 |
| `repo-settings` | `repo-settings/fleet.yml`'s repo list | 8 |
| `wsl-automation` | `skills.lock` | 13 |
| `wsl-automation` | `.github/workflows/actions.lock` | 17 |
| `wsl-automation` | The Codex Cloud bootstrap pin | 16 |
| `wsl-automation` | The waiting feed's commit identity | 12 |
| `wsl-automation` | The waiting feed's repository | 1, 21 |
| `skills-evals` | `.github/workflows/scheduled-run-health.yml` | 15 |
| `skills-evals` | `.github/workflows/actions.lock` | 17 |
| `skills-evals` | The routine trigger id | 23, 24 |
| `skills-evals` | `.github/workflows/eval.yml` | 18 |
| `agentskills` | `skills.lock` | 13 |
| `agentskills-private` | `skills.lock` | 13 |
| `agentskills` | `.github/workflows/scheduled-run-health.yml` | 15 |
| `agentskills-private` | `.github/workflows/scheduled-run-health.yml` | 15 |
| `agentskills` | `.github/workflows/actions.lock` | 17 |
| `agentskills-private` | `.github/workflows/actions.lock` | 17 |
| `agentskills` | `<your name>` | 11 |
| `agentskills-private` | `<your name>` | 11 |

(`agentskills-private` has no `scheduled-run-health.yml`; `REGENERATE.md`
lists the two registries under one heading.)

## The skills locks

`bash bootstrap/regenerate.sh skills-locks`, twice:

1. The first run finds `agentskills`' `skills.lock` still pinning forty
   zeros, and proposes the registry's own generator's output
   (`python3 scripts/generate_skills_lock.py`, which pins the registry's
   HEAD). Merge that pull request.
2. The second run checks it (`--check` and `--check-current`), copies the
   same file into `agentskills-private`, and re-pins `_agent-guidance`,
   `repo-settings` and `wsl-automation` with the registry's generator run on
   a clone of your registry (`--repo`), keeping each lock's bundles. Merge
   the four pull requests.

Until your registry has skills, every lock names none. From then on the
nightly `skills-lock-bump.yml` in `_agent-guidance` keeps the consumers
current.

## The skills-bootstrap hook pin

`_agent-guidance`'s `repos.yml` `skills_bootstrap.ref` names the registry
commit the delivered `.claude/hooks/skills-bootstrap.sh` comes from; its
`sha256` the export already computed from the exported hook.
`bash bootstrap/regenerate.sh hook-pin` sets `ref` to your `agentskills`
HEAD after checking that the hook there has exactly that sha256, and refuses
if the two copies have drifted (then review the hook and run
`_agent-guidance`'s own `scripts/bump-hook-pin.sh`, which re-pins both and
opens its own pull request; the nightly bumper runs it from then on).

## The scheduled-run-health pin

`scheduled-run-health.yml` in `repo-settings`, `skills-evals` and
`agentskills` calls `_agent-guidance`'s reusable workflow, and a cross-repo
reusable takes a full sha; the export ships forty zeros.
`bash bootstrap/regenerate.sh run-health-sha` writes your `_agent-guidance`
default branch's commit into all three.

## The Codex Cloud pin

`wsl-automation`'s `scripts/sync-codex-cloud-environments.sh` downloads your
registry's skills-bootstrap hook at a reviewed commit and checks its digest;
both ship as zeros, which fail closed (setup reports skills `DEGRADED`).
`bash bootstrap/regenerate.sh codex-pin` sets them to your `agentskills`
HEAD and its hook's sha256, runs the script's own test with the new pin,
and proposes the change. Skip it if you do not use Codex.

## The action lockfiles

Every repo pins its actions through `.github/workflows/actions.lock`
(an exact tag per `uses:`, each locked to a commit). The export changed
only owner names in them, but confirm each with the tool that writes them:

```bash
gh extension install github/gh-actions-lock
bash bootstrap/regenerate.sh actions-lock
```

It runs `gh actions-lock --no-narrow` in each repo and proposes any change;
`--check` runs `gh actions-lock --verify`. Review an `actions.lock` diff as
you would a sha pin.

## Example repos in fleet.yml

The owner's fleet includes a site platform and two sites, so
`repo-settings/fleet.yml` declares `example-site-platform`,
`example-site-a` and `<OWNER_TWO>/example-site-b`, `tests/test_repo_settings.py`
has rows for them, and `_agent-guidance`'s `repos.yml` lists them under
`cron_coverage.fleet`. Unless you have those repos, remove them in a pull
request on each repo before the first apply: `apply-settings.sh` refuses
while `fleet.yml` names a repo that does not exist, and a missing
`cron_coverage.fleet` repo is an error for the coverage check. Add your own
consumers the same way later.

## The eval.yml fixture default

`skills-evals`' `.github/workflows/eval.yml` takes a `fixture` input whose
default, `evals/workflow-path-audit/required-check-routing`, is a fixture
the kit does not ship (the content starts blank). A dispatch that keeps the
default is refused by the workflow's own validation. Change the default to
a fixture you have, or add that fixture, in a pull request.

## Filled in by copy-starter.sh

Two items need no step of their own: `copy-starter.sh` writes `OWNER_NAME`
for `<your name>` in the plugin and marketplace manifests, and your own
`<id>+<login>@users.noreply.github.com` (the id read from GitHub) for the
waiting feed's placeholder commit identity, in
`scripts/publish-waiting-feed.sh` and its test.
