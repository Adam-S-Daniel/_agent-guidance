# 0036 — Cloud sessions record which skill copies they load

**Status:** Accepted (Adam, 2026-11-02). Extends [ADR 0035](0035-sessions-prove-the-guidance-loaded-with-a-check-value.md), whose record holds no names beyond registry names.

## Context

skills-evals wants to compare the skill copy an eval tested with the copy real sessions load ([skills-evals#536](https://github.com/Adam-S-Daniel/skills-evals/issues/536)). #536 built the shared lock-kind digest and the local private records: on a durable machine, each Skill call's loaded copy is recorded where the census can read it. The cloud half was split into [skills-evals#562](https://github.com/Adam-S-Daniel/skills-evals/issues/562) (scope item 5). A cloud container is gone when the session ends, so anything not uploaded during the session is lost.

The [`guidance-load-record.sh`](../../.claude/hooks/guidance-load-record.sh) Stop hook already uploads from cloud sessions. Phase 1 (ADR 0035) writes one line per session to `events/<YYYY-MM>/cloud-guidance.jsonl` on the `persistent/usage-events` branch of the private repo `Adam-S-Daniel/adam-agentskills-private`. Those commits are made through the session's git credential, which the cloud proxy injects. No GitHub App credential is configured for it.

ADR 0035 point 5 and its addendum keep the record free of paths, titles and content, and its only names are registry repos. Recording which skill a session loaded means sending skill names, tool-use ids and full digests off the container, a new data class. That needs its own decision.

The integrator's cloud probe (2026-11-02) measured what the upload needs:

- The cloud git proxy accepted a depth-1 `--no-checkout` clone of the private repo; `hash-object`, `update-index`, `write-tree` and `commit-tree`; and a push to a new branch.
- Timings: clone 0.84 s, build 0.01 s, push 1.47 s, about 2.3 s per upload.
- The checkout's `origin` URL is plain `https`; the proxy adds the auth.
- The proxy refuses branch deletion (403).
- Every transcript line carries `sessionId`, and each Skill use can be keyed on its Skill `tool_use` id.

## Decision

1. **Phase 2 of the same hook, cloud sessions only.** A session is cloud when `CLAUDE_CODE_REMOTE=true` or `CLAUDE_CODE_REMOTE_SESSION_ID` is set. A local session does no phase-2 work: no file is created and no git or network call is made. Phase 2 collects on every Stop, before phase 1's once-per-session early return, and uploads after phase 1, so phase 1 keeps its budget.
2. **One line per skill use**, keyed on the Skill `tool_use` id, or for a skill loaded by a typed slash command on that transcript entry's uuid, with these fields only:
   - `name`: the skill name, checked against a fixed name pattern, else the literal `invalid`;
   - `id`: the Skill `tool_use` id, or the typed command's entry uuid;
   - `base_kind`: where the loaded copy lives (user skills, plugin cache, repo checkout, builtin, account sync, unknown);
   - `registry` and `ref`: the registry's `owner/repo` and the 40-hex commit it came from, or null;
   - `digest`: the full 64-hex lock-kind digest of the loaded copy (the #536 function), or null with a fixed reason;
   - `ts`, `session` (the same 16-hex session hash phase 1 records), `cli` version, and fixed `v`, `kind` and `surface` values.
3. **It goes to the private store only**: `Adam-S-Daniel/adam-agentskills-private`, branch `persistent/usage-events`, path `events/<YYYY-MM>/cloud-skills/<16 hex of the session>.jsonl`, one shard per session. Each use goes in the month of its own timestamp. Nothing is added to `sessions.jsonl` or to any public file. Public `skill-digest` rows stay skills-evals' job, under its registry-privacy rules.
4. **Never recorded:** paths (the base directory is resolved locally and dropped), titles, prompts, transcript text, `cwd` or machine names, the raw session id, and credentials. The hook sends no token: it uses the session's git credential through the proxy, as phase 1 does.
5. **Git plumbing, append only.** A depth-1 `--no-checkout` clone; `read-tree HEAD` into a private index, so the commit keeps every other file on the branch; `hash-object`, `update-index`, `write-tree`, `commit-tree`; a push of that commit to the branch. A rejected push gets one fresh clone and one retry. The hook never deletes or force-pushes a ref.
6. **Fail silent, bounded.** Phase 2 runs in its own `try`, writes nothing to stdout or stderr, and always exits 0. Network calls share a budget of about 6 s, hashing about 3 s per Stop, both inside the hook's existing 15 s budget and 20 s Stop timeout. A failed upload backs off from 2 minutes. A definite no-credential answer stops uploads for the rest of the session. Unsent lines stay in a local pending file and are retried on the next Stop.
7. **The hook's header comment is rewritten in the same PR** to state the new data class and its destination.

## Consequences

- **Latency only where a skill was used.** A Stop with new settled Skill uses pays about 2.3 s for the clone and push. Other Stops, and every local session, pay nothing measurable.
- **Coverage is "records seen", never complete.** Uses after the last successful upload die with the container. A session that cannot see the private repo records nothing; the no-credential marker keeps that cheap but invisible. The census has to say so.
- **A merge ships it fleet-wide.** `sync.sh` copies the hook byte for byte to every synced repo under both owners, so a bug reaches every cloud session on the next sync. The fleet worker's merge rule does not cover `.claude/hooks/*.sh`, so only Adam merges the code PR.
- **No paid-only GitHub feature.** Everything uses git against an existing private repo branch.
- **The branch grows.** One commit per uploading Stop and one shard per session. Depth-1 clones keep the cost flat until the pack passes about 5 MB, the point to revisit with `--filter=blob:none`. The branch cannot be protected on this plan (private repo), as before. A prompt-injected session with push access could rewrite it, so the reader validates every field.
- **Skill names leave the container**, private skills included, but only to the private repo, which already holds phase 1's records.
- **The hook and skills-evals' `harness/skill_digest.py` each compute the digest.** A shared test vector binds them, and it must stay in both suites.
- **Cut, for now:**
  - the GitHub App or contents-API path for phase 2, since its credential is unproven and undocumented;
  - cloud `skill` totals rows in the census, since they would change the dashboard totals (Adam's call later);
  - a combined phase-1 and phase-2 push.

## Alternatives considered

- **Add the fields to phase 1's record.** Rejected: that record is written once per session, at the first Stop with an `instructions` entry, so it misses later Skill uses. It is also the record ADR 0035 promised holds no names beyond registry names.
- **Record only a digest prefix.** Rejected: the private store is where full digests are resolved against registry commits; the public rows truncate later.
- **Upload through a GitHub App with the contents API.** Deferred: no App credential is configured, and the session's git path is the one measured.

## Addendum (2026-11-02, implementation review)

The implementation review changed three points of the Decision above. The rest of the Decision stands.

- **Phase 2 runs only when `CLAUDE_CODE_REMOTE=true`.** That is narrower than Decision point 1, which also counts `CLAUDE_CODE_REMOTE_SESSION_ID` alone as a cloud session. A session with only `CLAUDE_CODE_REMOTE_SESSION_ID` set does no phase-2 work: no file is created and no git or network call is made. Phase 1 keeps its own test, either variable, unchanged.
- **A rejected push fetches the new tip and rebuilds until the deadline.** This replaces Decision point 5's "one fresh clone and one retry". After a rejection the hook shallow-fetches the branch tip, reads its tree into the private index, re-reads the existing shard from that tip, then commits and pushes again, until the network deadline. Only a network error, a 5xx or a timeout counts as a failure for the backoff. A race that is still lost when time runs out tries again at the next Stop.
- **When phase 1 is also due, both files go in one commit.** This reverses the "Cut, for now" item under Consequences that ruled out a combined phase-1 and phase-2 push. When phase 1 uploads in the same Stop as phase 2, the hook makes one probe and one commit carrying both files, then one push. Later Stops push phase 2 alone.

## Addendum (2026-11-12): phase 3 uploads the agent-checks runs

Phase 3 reads the agent-checks run log (`$AGENT_CHECKS_LOG`, else `${XDG_STATE_HOME:-$HOME/.local/state}/agent-checks/runs.jsonl`) and uploads one line per valid pre-commit or pre-push run, now that hosted sessions run those hooks (ADR 0021 addendum). Each line carries `v`, `id` (the first 16 hex of the sha256 of the raw log line), `ts`, `kind: "hook"`, `surface: "cloud"`, the same 16-hex `session` as phases 1 and 2, `event`, `repo`, `checks`, `failed` and `runner`, and nothing else from the log line.

- **Git route, on from the start.** The lines go to `events/<YYYY-MM of ts>/cloud-hooks/<session>.jsonl` on the same private branch, deduped by id, in the same commit and push as phase 2's shard. The census reads only files it knows, so an unknown directory is harmless until it learns it.
- **It shares phase 2's machinery.** Same pending file, lock, backoff and state, an offset into the log, and 50 lines, 1 MiB and 0.5 s per Stop, 2000 lines per session. Its own errors are caught where they arise, so it cannot delay or fail phases 1 and 2.
- **Malformed lines are skipped silently**, as is any line whose `failed` ids are not within `checks`.
- **Identical lines collapse.** The id is the hash of the line, and the log's timestamp has one-second resolution, so two identical runs in one second count once. The format fixes that; a counter in the log would have to ship in the runner first.
- **Dedupe is per session.** A re-read of the log within a session adds nothing, because the shard's ids absorb it. The shard is named by session, so a container reused across sessions could attribute its earlier runs to the new session; fresh cloud containers do not.
