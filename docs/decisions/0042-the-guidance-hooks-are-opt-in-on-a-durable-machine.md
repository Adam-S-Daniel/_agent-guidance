# 0042 — The guidance hooks are opt-in on a durable machine

**Status:** Accepted (2026-11-12).

## Context

Every fleet repo is public and carries `.claude/hooks/fleet-memory.sh`,
registered as a SessionStart hook in its `.claude/settings.json`. The hook
writes the owner's personal agent guidance into user-level memory:
`~/.claude/CLAUDE.md`, and `~/.codex/AGENTS.md` when `~/.codex` exists
([0012](0012-codex-gets-the-guidance-as-user-instructions.md)). Until now its
only switch was `FLEET_GUIDANCE_SKIP`, an opt-out. So anyone who cloned one of
these repos, opened Claude Code in it and accepted the workspace-trust prompt
got the owner's guidance written into their own global memory, on their own
durable machine, loading in every project they open there. The second
delivered hook, `.claude/hooks/guidance-load-record.sh` (Stop), likewise
created `~/.local/state/fleet-guidance/` on that machine.

The owner's own machines get the guidance by four routes, and none may stop:

| Route | How it runs the hook | What it passes today |
|---|---|---|
| A fleet repo's project hook (Claude Code) | `bash "$CLAUDE_PROJECT_DIR/.claude/hooks/fleet-memory.sh"` | nothing |
| Local Codex | the user-level entry `scripts/register-codex-hook.sh` writes to `~/.codex/hooks.json`, or on Windows `.claude/hooks/codex-session-start.sh` | nothing, or `--workspace <dir>` |
| The `adam-anything-anywhere` plugin | `hooks/fleet-guidance/on-session-start.sh` runs a bundled byte copy of the hook | `FLEET_GUIDANCE_PAYLOAD` (fetched copy) or nothing (bundled fallback) |
| The skills-evals harness (`harness/guidance.py` `deliver`) | the pinned hook in a scratch profile, with only `PATH`, `HOME`, `TMPDIR`, `CLAUDE_CONFIG_DIR` and `FLEET_GUIDANCE_PAYLOAD` | `FLEET_GUIDANCE_PAYLOAD` |

Hosted sessions are different in kind: their home dies with the container.
The hooks already tell them apart by `CLAUDE_CODE_REMOTE=true` (the
hosted git-hooks step of
[0021](0021-the-sync-delivers-agent-checks-to-every-fleet-repo.md), and phases
2 and 3 of the Stop hook) or a nonempty `CLAUDE_CODE_REMOTE_SESSION_ID` (the
Stop hook's `remote` field). Codex Cloud runs `fleet-memory.sh --codex-cloud`
from its own setup script.

## Decision

1. **`fleet-memory.sh` delivers only when the run carries a signal.** Any one
   of these is enough:
   - `--codex-cloud`, the explicit Codex Cloud setup mode;
   - `FLEET_GUIDANCE_SKIP` on (it must still be able to remove a block);
   - `CLAUDE_CODE_REMOTE=true`, or a nonempty `CLAUDE_CODE_REMOTE_SESSION_ID`
     (a hosted, ephemeral session);
   - `FLEET_GUIDANCE_OWNER` on (`FLEET_GUIDANCE_SKIP`'s spelling: empty, `0`,
     `false`, `no` and `off` in any case are off);
   - a nonempty `FLEET_GUIDANCE_PAYLOAD`: a caller that chose the payload
     itself is a caller, not a repo default. Nothing the sync delivers sets
     it: `scripts/sync.sh`, `templates/` and `.claude/settings.json` here do
     not set it (a test fails if one ever does), and a search of every
     tracked file on the default branch of all 16 repos in
     `cron_coverage.fleet`, taken from the registry on 2026-11-13, found only
     reads in the delivered hooks and the expected setters in this repo's
     tests, adam-agentskills' plugin hook and skills-evals' harness
     ([evidence](../evidence/2026-11-13-fleet-guidance-payload-unset.md)). So
     a stranger's clone does not set it unless the stranger does;
   - the marker file `${XDG_CONFIG_HOME:-$HOME/.config}/fleet-guidance/owner`
     (`XDG_CONFIG_HOME` only when absolute, as the XDG spec requires), a
     regular file whose content is ignored.

   With none, the hook exits 0 before it touches anything: no file under
   `HOME` is read for delivery, created or modified, no receipt is written,
   nothing is printed (not even the `--workspace` repo list), whatever the
   arguments. This is not the `FLEET_GUIDANCE_SKIP` path, which removes the
   managed block.

2. **The owner signal is the marker, plus `FLEET_GUIDANCE_OWNER` from the
   user-level callers.** `setup.sh --owner-machine` in adam-agentskills writes
   the marker, once per home (Windows Git Bash and WSL each run it). The
   plugin's `on-session-start.sh` passes `FLEET_GUIDANCE_OWNER=1` on all three
   of its calls, because installing the plugin is itself the opt-in. The
   variable is the caller's channel, not something to remember in a shell
   profile; the marker covers every route that passes nothing.

3. **`guidance-load-record.sh` takes the same gate,** less the two signals
   only a delivery has (`FLEET_GUIDANCE_SKIP`, a caller's payload): a hosted
   session, `FLEET_GUIDANCE_OWNER` on, or the marker. Without one it exits 0
   before reading stdin and writes no state. Its record exists to measure
   whether the owner's sessions loaded the guidance; on anyone else's machine
   it measures nothing and only leaves files. It keys on the owner signal, not
   on whether a managed block is installed, because the case it most needs to
   record is an owner session where delivery failed.

4. **A block already installed on a durable machine without the signal is
   left alone.** `FLEET_GUIDANCE_SKIP=1` for one session removes it.

5. **A stranger running Claude Code on the web against a clone still gets the
   install,** in the hosted container. Nothing durable is written, and the
   hosted case cannot tell owner from stranger without an identity lookup
   this hook will not make offline
   ([0016](0016-the-freshest-delivery-wins-the-shared-global-block.md) keeps it
   network-free).

`agent-checks-guard.sh` is unchanged: it enforces the repo's own commit rules,
which a contributor needs. `skills-bootstrap.sh` lives in adam-agentskills and
is already a no-op on a developer machine.

## Rollout order

The owner signal reaches every owner home before any gated copy can run there.
An older copy ignores both signals, so steps 1 and 2 are safe on their own.

1. **adam-agentskills:** the plugin passes `FLEET_GUIDANCE_OWNER=1`, and
   `setup.sh --owner-machine` writes the marker. Merge it.
2. **Each of the owner's durable homes** (Windows Git Bash and WSL are two):
   pull adam-agentskills and run `bash setup.sh --owner-machine`, or create
   the marker by hand:
   `mkdir -p ~/.config/fleet-guidance && touch ~/.config/fleet-guidance/owner`.
   Check with `test -f ~/.config/fleet-guidance/owner`. Update the plugin
   (`claude plugin update`) as a second route.
3. **This repo:** merge the gate. The pull request stays a draft until step 2
   is done on every home.
4. **The sync** delivers the gated hooks to every fleet repo on its next run.
5. **The plugin's bundled copy** is refreshed later, when `drift-report.sh`
   ("Plugin copies of the fleet guidance") flags it. Step 1 made that safe.

Hosted sessions, Codex Cloud setup and the skills-evals harness need nothing:
they carry a signal already.

## Rejected

- **Gating on "a managed block is already installed".** It would close the
  rollout gap with no owner action, but every machine an ungated run already
  reached would keep receiving updates forever.
- **Removing the block when no signal is present.** A missing marker on the
  owner's own machine would then wipe his guidance silently: the exact failure
  this gate must not have.
- **A command-line flag for the user-level callers.** The hook refuses an
  unknown argument (exit 2, `DEGRADED`), so a caller updated before the hook
  copy it runs would break. An environment variable is ignored by older
  copies.
- **Changing the registered local-Codex command to pass the variable.**
  Codex trusts a hook by its definition's hash, so every machine would have to
  re-register and re-trust in `/hooks`, for a route the per-home marker
  already covers.
- **Treating any GitHub Actions runner as ephemeral.** A search of the
  workflows on the default branch of the fleet repos checked out on the
  owner's workstation on 2026-11-12 found Claude Code run only by
  skills-evals' `eval.yml`, whose harness delivers the guidance itself with
  a payload. That search was not every fleet repo, and a self-hosted runner
  is durable, so a runner gets no exemption of its own.
- **An identity check** (git `user.email`, `gh auth status`). It needs the
  network or trusts a value anyone can set, and a wrong answer on the owner's
  machine would be a silent gap.

## Consequences

- A stranger's durable home is never written by a fleet repo's hooks. One who
  wants the guidance sets `FLEET_GUIDANCE_OWNER=1` or creates the marker.
- A stranger's session sees no verdict line, which the repo stub reads as "not
  in context": it reads `agents-md/base.md` by hand and reports running
  degraded. That is the right behavior for them, so the stub is unchanged.
- A new owner home gets no guidance from a fleet repo until
  `setup.sh --owner-machine` runs there; the plugin still delivers if it is
  installed.
- The gate is two copies of a short shell function, one in each hook, because
  each file is delivered alone; the tests run both against the same signals.
