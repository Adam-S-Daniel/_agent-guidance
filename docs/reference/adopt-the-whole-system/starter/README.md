# The six exports

Each folder here is an export of one of the owner's real repos at a pinned
commit, with every owner name replaced by an invented one, the owner's own
content left out, and the generated files rebuilt.
[`../export/`](../export/DECISIONS.md) holds how: one manifest per repo
(`export/manifests/`), the patches and overlays, and `export.py`, which
rebuilds these folders from the source repos; `export.py --check` fails if
a folder differs from a fresh export by a byte. Do not edit a folder by
hand: change its manifest, patch or overlay, and export again.

| Folder | Goes into | What it is |
|---|---|---|
| [`agent-guidance/`](agent-guidance/README.md) | `<you>/_agent-guidance` | The fleet guidance, the sync that delivers it, the hooks, templates and agent-checks runner, and the routine specs ([guidance](../docs/guidance.md)) |
| [`repo-settings/`](repo-settings/README.md) | `<you>/repo-settings` | Every repo's settings as code (`repo-settings/fleet.yml`) and the engine that applies them ([settings](../docs/settings-and-apps.md)) |
| [`agent-skills/`](agent-skills/README.md) | `<you>/acsi-agentskills` | The skills registry: a plugin marketplace with four empty plugins, the privacy check and the plugin runtime review ([skills](../docs/skills.md)) |
| [`agent-skills-private/`](agent-skills-private/README.md) | `<you>/acsi-agentskills-private` | The private registry, packaged the same way; its `persistent/lessons` branch holds the session miner's lessons |
| [`skills-evals/`](skills-evals/README.md) | `<you>/skills-evals` | The eval harness, the planner, the gates and the lanes of the loop ([evals](../docs/evals-and-loop.md)) |
| [`wsl-automation/`](wsl-automation/README.md) | `<you>/wsl-automation` | The jobs that run on your machine: the session miner, the usage census, the waiting feed ([machines](../docs/machine.md#scheduled-jobs-wsl-automation)) |

`../bootstrap/copy-starter.sh starter/<folder> <you>/<repo>` copies one in
through a pull request and gives it your names. A few generated files still
hold a placeholder or the owner's value until you rebuild them on your
account; [`../export/REGENERATE.md`](../export/REGENERATE.md) lists them and
[generated files](../docs/generated-files.md) says which step handles each.

## Running the tests

```bash
unshare --user --map-current-user --pid --fork --mount-proc -- bash run-all-tests.sh
```

(As root, `unshare --pid --fork --mount-proc --`; `known-failures.txt` is
calibrated for that form, and under the mapped user namespace as root some
ids report as `ERROR` instead of `FAIL`, so they are not excused.) It runs each export's own
suite through `../export/run_suites.py`, which copies the folder to a
temporary directory, makes it a git repo where the suite reads its history,
runs the repo's own setup and test command in a PID namespace, and prints
one PASS or FAIL line per folder. Each suite needs what its repo's CI
installs; the manifest's `suite.needs` names it. `_agent-guidance`'s needs
mikefarah's Go `yq` v4 on `PATH`: Debian's `yq` is a different program, and
with it `scripts/sync.sh` refuses to run, so about 250 of that suite's
assertions fail. The `skills-evals` suite
takes the longest (about five minutes on four cores; much longer on two): run
it alone with
`python3 ../export/run_suites.py --kit-name skills-evals`, in the background
with a log.

Failures that come from the machine running the tests rather than from the
kit are listed in [`known-failures.txt`](known-failures.txt), each by its
exact test id and with the reason. `run-all-tests.sh` passes a folder whose
failures are all on that list, names each one it excused, and still fails
on any failure that is not listed. Read the exit code directly, not through
a pipe.
