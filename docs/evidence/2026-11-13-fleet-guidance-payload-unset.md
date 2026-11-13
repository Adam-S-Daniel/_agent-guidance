# 2026-11-13: no fleet repo sets FLEET_GUIDANCE_PAYLOAD or FLEET_GUIDANCE_OWNER

Evidence for [ADR 0042](../decisions/0042-the-guidance-hooks-are-opt-in-on-a-durable-machine.md),
decision 1: a nonempty `FLEET_GUIDANCE_PAYLOAD`, or `FLEET_GUIDANCE_OWNER` on, is
an owner signal. That is safe only while nothing the sync delivers into a
consumer sets either name, because a stranger's clone would then pass the gate.
This file keeps the measurement, under
[ADR 0018](../decisions/0018-incident-narratives-live-in-docs-evidence.md);
the question was
[issue 526](https://github.com/Adam-S-Daniel/_agent-guidance/issues/526).

## Denominator

The ADR's first check covered the 15 repos cloned on the workstation on
2026-11-12, a local-disk list. This one starts from the registry:

- **`cron_coverage.fleet` in `repos.yml`: 16 repos.** Read from `origin/main`
  at `41e5d53` with a YAML parser, not by counting lines.
- **`SYNC_OWNERS` in `.github/workflows/sync.yml`: `"Adam-S-Daniel jodidaniel"`**,
  both owners. 15 fleet repos live under `Adam-S-Daniel`; `jodidaniel.com` lives
  under `jodidaniel`.
- **Checked: 16 of 16.** The sweep was a read-only `git grep -E
  'FLEET_GUIDANCE_(PAYLOAD|OWNER)'` over every tracked file of each default
  branch, a shallow clone of each. `security-alerts` (private) was invisible to
  the first sweep session (a clone prompted for a password, which is what a
  missing or unauthorized repo does, so that proved nothing) and was read from
  a session that can see it.

## Result

Hits per repo, at the commit checked. `hooks` means the two delivered hook
copies, `.claude/hooks/fleet-memory.sh` (11 lines) and
`.claude/hooks/guidance-load-record.sh` (2 lines).

| Repo | Commit | Hits | Where | Classification |
|---|---|---|---|---|
| `_agent-guidance` | `41e5d53` | 120 in 12 files | hooks; `README.md`; `docs/codex-cloud.md`; ADRs 0016, 0032, 0042; `test/run-tests.sh` and four Python tests | reads or documents; tests set it in the environment of one child process |
| `acsi-kit` | `90f3c8f` | 218 in 29 files | `starter/` copies of everything the three repos above and below hold | same as the originals; a starter is a template for a new fleet, not a fleet repo's session |
| `adam-agentskills` | `730e7e2` | 43 in 6 files | hooks; ADR 0023; plugin `fleet-memory.sh` (reads) and `on-session-start.sh`; `scripts/test_fleet_guidance_hook.py` | the plugin's `on-session-start.sh` sets both for the one child it starts |
| `adam-agentskills-private` | `418a611` | 13 in 2 files | hooks | reads |
| `adamdaniel.ai` | `3231c25` | 13 in 2 files | hooks | reads |
| `ai-usage-dashboard` | `bb6d53a` | 13 in 2 files | hooks | reads |
| `claude-memory-map` | `c75f8a0` | 13 in 2 files | hooks | reads |
| `cms-platform` | `d2c0686` | 13 in 2 files | hooks | reads |
| `fastmail-actions` | `456a716` | 13 in 2 files | hooks | reads |
| `jodidaniel.com` | `b40e1d5` | 13 in 2 files | hooks | reads |
| `repo-settings` | `92bf9a0` | 13 in 2 files | hooks | reads |
| `rss-inator` | `0f0f4ca` | 13 in 2 files | hooks | reads |
| `security-alerts` | `d1b9149` | 13 in 2 files | hooks (lines 71, 72, 160, 162, 187, 188, 509, 534, 1153, 1155, 1156 of `fleet-memory.sh`; 306, 315 of `guidance-load-record.sh`) | reads |
| `skills-evals` | `c7efc7b` | 20 in 8 files | hooks; `DESIGN.md`; `README.md`; `harness/guidance.py`; `test/issues/test_issue_adr0012_delivery.py`; the frozen `evals/real-work/_agent-guidance-196/seed/` copy of two files | `harness/guidance.py` sets it for the scratch hook run; the seed is data |
| `verify-actions-lock` | `c19e5c2` | 13 in 2 files | hooks | reads |
| `wsl-automation` | `af14c7e` | 13 in 2 files | hooks | reads |

Nothing in any repo's `.claude/settings.json`, any workflow `env:` block, any
setup script or any `CLAUDE_ENV_FILE` writer names either variable.

### How a hit was classified

A hit is a **read** when it is a comment, documentation, or a parameter
expansion (`"${FLEET_GUIDANCE_PAYLOAD:-}"`, `"${FLEET_GUIDANCE_OWNER:-}"`). It
**sets** the variable when it exports it, prefixes a command with it, or
writes it to a settings file or environment file, so that a session or a
process inherits it. The classification ran the same rule as the guard test
below over every file with a hit, then each flagged line was read by hand.

- **The delivered hooks only read.** In all 16 repos, and in the `starter/`
  copies, `fleet-memory.sh` and `guidance-load-record.sh` mention the names
  only in comments and in `${...:-}` expansions, so the rule flags no line of
  either.
- **The flagged lines are the three expected places.**
  - `_agent-guidance`: prose in `README.md`, `docs/` and ADR 0042 (documents the
    variable), and tests that put the name in the environment of a hook they
    run as a child process. None is delivered.
  - `adam-agentskills` (and its `acsi-kit` starter copy):
    `plugins/adam-anything-anywhere/hooks/fleet-guidance/on-session-start.sh`
    runs `fleet-memory.sh` with `FLEET_GUIDANCE_OWNER=1` and, for a fetched
    copy, `FLEET_GUIDANCE_PAYLOAD=<tmp file>`, as `env` prefixes on that one
    command (lines 69, 77, 238 to 240). The plugin is the owner's opt-in
    (ADR 0042, decision 2), and the values do not outlive the child.
  - `skills-evals`: `harness/guidance.py` builds the environment of the scratch
    profile in which it runs the pinned hook, with `FLEET_GUIDANCE_PAYLOAD`
    pointing at the assembled file (line 897). The `_agent-guidance-196` seed
    is a frozen copy of this repo's test, kept as eval data.

## Conclusion

No repo outside `_agent-guidance`, `adam-agentskills` and `skills-evals` sets
either name, or even holds a line that the rule flags (acsi-kit's `starter/`
holds copies of those three repos' files). Of those three, `_agent-guidance`
sets them only inside tests, `adam-agentskills` only for the one child process
its plugin hook starts, and `skills-evals` only for the scratch profile of a
harness run. None of the three delivers a setting into a consumer's session.
The claim in ADR 0042 holds for 16 of 16 fleet repos.

The sweep is a snapshot of default branches on 2026-11-13. What keeps it true
afterward is a test:
`OwnerSignalNotDeliveredTests` in `test/test_guidance_load_record.py` (group
`guidance_record`) fails if `scripts/sync.sh`, `.claude/settings.json`, any
hook under `.claude/hooks/` or anything under `templates/` would set either
name. It does not cover a consumer repo's own edits, which only a later sweep
sees.
