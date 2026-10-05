# Codex Cloud fleet guidance

The manual environment bootstrap is verified for a fresh Codex Cloud
container. It does not depend on user-hook registration or trust; Cloud runs
the installer directly before assembling the agent's instructions.

Local Codex sessions are different. From the source, the fleet hook there is a
user-level hook that needs its definition trusted before it runs (a different
hook was observed not to run without the bypass flag; the fleet hook itself
was not exercised). Project `AGENTS.md` is skipped in a project marked
untrusted (observed). See the
[ADR 0012 addendum](decisions/0012-codex-gets-the-guidance-as-user-instructions.md#addendum--trust-and-daemon-evidence-boundaries-2026-10-04)
for what was observed and what remains open.

## Configure the environment

1. Select **Manual** environment setup. Merely adding setup and maintenance
   text while automatic setup remains enabled does not run those scripts, and
   an existing automatic-setup cache can keep using its old setup.
2. For the current supported recipe, set `CODEX_HOME=/opt/codex` as a
   persistent environment variable. This gives setup and the agent the same
   destination; it is not an inherent Codex requirement, as measured below.
3. Preserve the repository's dependency installation, then run the guidance
   hook in both the **setup script** and the **maintenance script**. For this
   repo the dependency step is `npm ci`:

```bash
npm ci
bash .claude/hooks/fleet-memory.sh --codex-cloud
```

4. Reset the environment cache before the first verification. Cloud runs setup
   while caching the default branch for a fresh container, then checks out the
   task's selected branch. It runs maintenance after checkout when resuming a
   cached container.

### Fleet skill setup

Each environment script enters its **selected repository's** checkout under
`/workspace` before running setup. It installs Node dependencies only when
that checkout has `package-lock.json`, runs the guidance hook, then enrolls in
skill delivery only when the same checkout has `skills.lock` and the delivered
`.claude/hooks/skills-bootstrap.sh`. An invalid or symlinked lock, or a missing
or symlinked delivered hook in an enrolled repo, fails setup. Repos without a
lock deliberately skip fleet skills.

The reviewed Codex-capable bootstrap is pinned to
[`1ecea259`](https://github.com/Adam-S-Daniel/adam-agentskills/commit/1ecea2593bcbca6b6073eedf50bb4ffa90ee77e8)
with SHA-256
`e1c79a80a00bad2ade61c959115bf366366c34f5f7f616dcc236ad6e95dc5d19`.
For an enrolled repo, setup and maintenance download that exact file, verify
its bytes, and run it with `--codex-cloud` and `CLAUDE_PROJECT_DIR` set to the
selected checkout. This keeps the Cloud invocation on the reviewed revision
while older delivered hook copies are still being refreshed across the fleet;
the local delivered hook is the opt-in guard, not the invoked file.
The hook drains SessionStart JSON from stdin. Cloud setup can keep stdin open,
so the environment command redirects the hook's stdin from `/dev/null` to let
setup finish.

The environment script's relevant steps are:

```bash
cd "/workspace/$repository_name"
if [[ -f package-lock.json ]]; then npm ci; fi
CODEX_HOME="${CODEX_HOME:-/opt/codex}" bash .claude/hooks/fleet-memory.sh --codex-cloud
if [[ ! -e skills.lock && ! -L skills.lock ]]; then
  printf '%s\n' 'skills: skipped (no skills.lock in selected repository)'
else
  [[ -f skills.lock && ! -L skills.lock ]] || exit 1
  [[ -f .claude/hooks/skills-bootstrap.sh && ! -L .claude/hooks/skills-bootstrap.sh ]] || exit 1
  bootstrap_file=$(mktemp)
  trap 'rm -f -- "$bootstrap_file"' EXIT
  curl --fail --silent --show-error --location --output "$bootstrap_file" \
    'https://raw.githubusercontent.com/Adam-S-Daniel/adam-agentskills/1ecea2593bcbca6b6073eedf50bb4ffa90ee77e8/.claude/hooks/skills-bootstrap.sh'
  printf '%s  %s\n' \
    'e1c79a80a00bad2ade61c959115bf366366c34f5f7f616dcc236ad6e95dc5d19' \
    "$bootstrap_file" | sha256sum --check --status
  CLAUDE_PROJECT_DIR="$PWD" CODEX_HOME="${CODEX_HOME:-/opt/codex}" \
    bash "$bootstrap_file" --codex-cloud </dev/null
fi
```

Here `repository_name` is supplied from each environment's validated target,
not copied from another environment. This recipe is for the rollout script;
keep the existing environment's other setup commands when applying it.

Once the hook is on the default branch, the relative command above is the
durable configuration. Because fresh setup may run against the default branch,
the successful pre-merge check used a temporary bridge: it downloaded the
installer from reviewed commit
[`f417c13`](https://github.com/Adam-S-Daniel/_agent-guidance/commit/f417c13d27f99336169966c9709c1a8293686f74),
verified SHA-256
`d33d8e08de029bb118eb1f395b2b4b2f0f4c70b9cb85828ceb2e9580617e1845`,
and pointed `FLEET_GUIDANCE_PAYLOAD` at the current checkout's
`.claude/hooks/fleet-guidance.md`. That pinned the reviewed installer while
still testing the payload from the branch Cloud actually checked out. The SHA
pin and digest are unnecessary after the hook merges to the default branch.

### Setup and agent environments

A controlled 2026-09-15 probe removed the persistent UI `CODEX_HOME` and ran
the installer with a command-local default:

```bash
CODEX_HOME="${CODEX_HOME:-/opt/codex}" \
  bash .claude/hooks/fleet-memory.sh --codex-cloud
```

The setup log had no `CODEX_HOME`; it also had none of `CODEX_CLOUD`,
`CODEX_CI`, `CODEX_ENVIRONMENT`, or `CODEX_ENV`. This is a measured distinction,
not an exhaustive test of possible signals. In the resulting agent process,
`CODEX_HOME` was `/opt/codex`, and the exact
[`check-codex-cloud-context.py`](../scripts/check-codex-cloud-context.py) check
passed. The persistent UI variable is therefore a convenient way to align both
phases; a command-local default, or an equivalent repo-owned Cloud default,
can provide the same alignment.

The explicit mode targets Codex only. It selects a nonempty
`$CODEX_HOME/AGENTS.override.md` when one exists, otherwise
`$CODEX_HOME/AGENTS.md`, and preserves content outside the fleet's markers. It
also persists one `fleet-guidance:` verdict inside the managed block because
setup stdout is not agent context. Missing payloads, malformed existing
markers, and unwritable destinations print one `DEGRADED` line and exit
nonzero, allowing setup or maintenance to stop instead of silently launching
without the full guidance.

### Mode selection

`--codex-cloud` is this hook's implemented mode switch; setup and maintenance
supply it directly. `CODEX_HOME` chooses the destination location only. The
hook does not currently infer Cloud from the operating system, hostname, or
path. This design choice does not prove automatic detection is impossible, but
detection inside the script could only affect an invocation that already
happened—it cannot cause Cloud to invoke the hook. A normal SessionStart run
keeps the legacy behavior: it writes Claude plus an already-existing Codex home
and retains its exit-zero failure policy.

The Claude multi-repo bootstrap in
[agentskills' delivery guide](https://github.com/Adam-S-Daniel/agentskills/blob/main/docs/multi-repo-delivery.md)
addresses hook discovery across child repositories. The reproduction in
[issue #130](https://github.com/Adam-S-Daniel/_agent-guidance/issues/130) uses
one repository, so the two gaps are separate.

`FLEET_GUIDANCE_SKIP=1` removes the payload and persists an explicit skipped
verdict. Removing the flag (or setting it to `0`, `false`, `no`, or `off`) on a
later setup or maintenance run restores the payload.

## Diagnose delivery without a Codex CLI

Some Cloud shells do not provide a `codex` executable. A short read-only
diagnostic prompt is:

> Do not use tools. Reply with exactly one word: the final word of the
> `fleet-guidance: installed` line in your initial instructions.

The expected word is `maintenance`. This is a convenient session check. The
saved raw task response below is the stronger proof because it does not depend
on a model report.

To prove the installer wrote the file, select the same global file it does and
inspect its persisted verdict:

```bash
codex_home_dir="${CODEX_HOME:-$HOME/.codex}"
if [[ -s "$codex_home_dir/AGENTS.override.md" ]]; then
    codex_global_instructions="$codex_home_dir/AGENTS.override.md"
else
    codex_global_instructions="$codex_home_dir/AGENTS.md"
fi
grep -F 'fleet-guidance:' "$codex_global_instructions"
```

That result proves the setup or maintenance command installed the file. It
does not prove the Cloud harness included the file in the model's context. A
model-visible proof uses
`current_assistant_turn.thread_events.events` in the completed task response.
The checker reads only the initial `rawResponseItem/completed` user
instructions before any tool output, reasoning, or assistant response can echo
the text. In a signed-in browser's developer tools, open the **Network** tab,
reload the completed task, and save the JSON response from
`GET /backend-api/wham/tasks/<task_id>` locally. Then run:

```bash
python3 scripts/check-codex-cloud-context.py \
  saved-task-response.json \
  .claude/hooks/fleet-guidance.md \
  AGENTS.md
```

The checker fails closed unless that envelope contains the payload exactly
as the hook delivers it (byte-exact after dropping the repo-only `# AGENTS.md`
header that the hook's `PAYLOAD_REPO_HEADER` names) inside one complete
managed block, its one persisted installed verdict, and
the expected repo-specific additions through end of file. It prints only a
non-identifying result; it does not fetch authenticated task data. Raw task
captures are authenticated and stay local—do not commit them or paste private
environment IDs or task URLs into the repo. This response shape is an observed
internal endpoint and may change; the checker fails closed if the response is
unavailable or its structure changes.

### Verify skill discovery

For a repo with a committed `skills.lock`, run the separate catalog check on
the same saved, completed task response:

```bash
python3 scripts/check-codex-cloud-skills.py saved-task-response.json skills.lock
```

The checker derives expected names from the basenames of `skills` keys, including
any `sources[*].skills` rows, and requires each name to appear once in the
initial developer `### Available skills` catalog with a
`$HOME/.agents/skills/<name>/SKILL.md` path. The catalog must be complete. An
assistant answer, tool result, or `output_items` echo cannot satisfy this check.
The lock records verified content digests; the catalog check establishes that
Codex actually discovered the installed skills for this task.

For an exact check of installed frontmatter names and paths, make a local
manifest after verifying the installed files against their lock digests:

```json
{"skills": [{"name": "example-skill", "file": "/home/agent/.agents/skills/example-skill/SKILL.md"}]}
```

Pass that JSON file as the second argument instead of `skills.lock`. Each
manifest entry requires `name`; `file` and `description` are optional. Supply
`file` when the frontmatter name differs from its installed directory or when
the absolute path matters; without it, the checker requires the same fleet
path suffix as lock mode. A supplied `description` must match the catalog text
exactly. For an environment whose repo deliberately has no lock, use
`{"skills": []}`; this requires zero fleet skills while allowing Codex's
built-in catalog entries.
Keep local manifests and raw responses outside the repo when they contain
private paths or task data.

The fleet rollout covers 19 environments: 12 repos carry a lock and seven
deliberately do not. The [2026-09-30 verification record](#2026-09-30-fleet-skill-rollout)
documents fresh and cached skill discovery for all 19.

## Verification record

### 2026-09-30 fleet skill rollout

The reviewed implementation is in the [skill installer PR #37](https://github.com/Adam-S-Daniel/adam-agentskills/pull/37),
[environment reconciler PR #41](https://github.com/Adam-S-Daniel/wsl-automation/pull/41),
and [Cloud catalog verifier PR #220](https://github.com/Adam-S-Daniel/_agent-guidance/pull/220).
An initial pilot reached Cloud setup but waited for the bootstrap hook to drain
stdin that setup kept open. Redirecting that invocation from `/dev/null` let
setup finish. A final reconciliation dry run proposed no changes across the 19
environments; their other settings and repository membership stayed in place.
Two `repo_map.size` metadata values refreshed.

All 19 fresh tasks completed and passed
[`check-codex-cloud-skills.py`](../scripts/check-codex-cloud-skills.py) against
their repository's committed `skills.lock`, or `{"skills": []}` for a repo
without one. None of those fresh tasks had a maintenance marker. All 19 cached
tasks completed, logged `Running maintenance scripts...`, and passed the same
initial developer skill catalog check. Each locked repo's expected skill names and
`SKILL.md` paths were present exactly once before assistant or tool output:
ten repos expected nine skills each and two federated repos expected 23 each,
for 136 expected fleet catalog entries per phase (272 across both phases).
Each of the seven lockless repos had zero fleet skills in both phases. These
38 saved-response checks report model-visible discovery; the earlier guidance
checks below established the separate global-instruction delivery path.

### 2026-09-15 fleet guidance delivery

On 2026-09-15, the original automatic-setup baseline contained complete
repo-specific additions but no global block or full payload. After switching
to Manual setup, persisting `CODEX_HOME`, and resetting the cache, two completed
Cloud sessions at reviewed commit `f417c13` passed
`check-codex-cloud-context.py`: the 24,465 byte fleet payload appeared exactly
once in each raw initial instruction envelope, and the repo-specific additions
were complete. Both logs said `Running setup scripts...` and
`fleet-guidance: installed`. A third task resumed the cached environment, also
passed the exact initial-envelope checker, and logged
`Running maintenance scripts...` followed by
`fleet-guidance: current (ve8a1ff3c, 24465 bytes) — ~/.codex/AGENTS.md`.
Together these runs verify both cold setup delivery and cached maintenance
delivery. The public record is
[PR #131](https://github.com/Adam-S-Daniel/_agent-guidance/pull/131) and
[issue #130](https://github.com/Adam-S-Daniel/_agent-guidance/issues/130).

A local Codex CLI control independently loaded the complete 24,465 byte global
payload plus a 32,760 byte project `AGENTS.md` into one 57,583 byte instruction
envelope. Refresh and byte-idempotence through the maintenance command have
deterministic test coverage as well as the live cached verification above.
Live Cloud user-hook availability remains unverified; this manual lifecycle
route does not depend on it.

See OpenAI's documentation for [Cloud environment setup and maintenance](https://learn.chatgpt.com/docs/environments/cloud-environment)
and the [Codex `AGENTS.md` instruction hierarchy](https://learn.chatgpt.com/docs/agent-configuration/agents-md).
