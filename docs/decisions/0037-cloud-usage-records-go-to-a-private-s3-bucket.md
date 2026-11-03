# 0037 — Cloud usage records go to a private S3 bucket

**Status:** Accepted (Adam approved AWS, 2026-11-03). Adds a second upload route to [ADR 0036](0036-cloud-sessions-record-which-skill-copies-they-load.md) and to ADR 0035's phase 1. Neither record changes.

## Context

The [`guidance-load-record.sh`](../../.claude/hooks/guidance-load-record.sh) Stop hook uploads a cloud session's records over git to `Adam-S-Daniel/adam-agentskills-private`, branch `persistent/usage-events`. The cloud proxy authenticates that git push with the session's own credential, and that credential reaches only the repos attached to the session. The proxy also replaces any `Authorization` header sent to api.github.com and refuses `/app/*`, so a GitHub App token brought in as an environment secret never reaches GitHub (measured 2026-10-31). Many projects, and every standalone session, do not attach the private repo. Their phase-1 and phase-2 records die with the container, and the census undercounts cloud use with no way to see it.

The first replacement idea was an encrypted drop: encrypt the records to a public key, push them to a drop branch in any attached fleet repo, and let a workflow move them into the private repo. An adversarial review (2026-11-03, kept outside this repo) rejected it as drafted. Its main findings:

- the ciphertext stays public forever;
- the signal from a fleet repo to the private repo needs a write credential spread fleet-wide;
- the deleting App would need write access to every fleet repo under both owners;
- anyone who can push to a fleet repo can forge records.

## Decision

1. **A private S3 bucket is a second route, chosen per session.** When `USAGE_SINK_BUCKET`, `USAGE_SINK_REGION`, `USAGE_SINK_ACCESS_KEY_ID` and `USAGE_SINK_SECRET_ACCESS_KEY` are all set and non-empty, set as cloud-environment secrets and variables, the hook uploads only to S3 and makes no GitHub call. Otherwise the git route runs unchanged.
2. **One signed PUT per object.** Each object goes to `https://<bucket>.s3.<region>.amazonaws.com/drop/<YYYY-MM-DD UTC>/<32 random hex>.json`, signed with AWS Signature Version 4 by the Python standard library alone. The `If-None-Match: *` header means an existing object is never overwritten.
3. **The body is the same records.** It is `{"v":1,"kind":"usage-drop","sent":…,"guidance":[…],"skills":[…]}`, exactly those keys:
   - `guidance` holds phase 1's line byte for byte as the git route appends it to `cloud-guidance.jsonl`, without `uploaded`;
   - `skills` holds phase 2's shard lines byte for byte.

   When both phases are due they share one object. A body over 256 KiB is split across several objects.
4. **Same pending, backoff and budget rules.**
   - A 200 drops the sent lines from the pending file.
   - A 412 (the key exists) retries once under a new key.
   - A 403 that carries S3's `x-amz-request-id` header is a definite no, which stops tries for the session and keeps the lines pending. A 403 without it, such as a proxy's refusal, is an ordinary failure.
   - Any other answer, or none, backs off from 2 minutes.
   - In a `CLAUDE_CODE_REMOTE=true` session, a phase-1 line that did not land is held beside phase 2's pending lines. It is sent again with them, under the same backoff, until it lands.

   Every PUT sits inside the hook's existing 10 s call cap and 15 s budget, or 6 s when phase 2 uploads alone. The hook prints nothing and always exits 0.
5. **Ingest happens on the private side.** S3 notifies a queue, and a small Lambda function sends one `repository_dispatch` to `adam-agentskills-private`. That repo's workflow reads the bucket through GitHub OIDC (list and get on `drop/` only). It validates each object whole with skills-evals' `scripts/ingest_usage_sink.py` and appends the records to the same `persistent/usage-events` files the census already reads. The stack lives in that repo under `infrastructure/usage-sink/`.

## Consequences

- **A write-only key sits in each cloud environment.** The writer IAM user may only `s3:PutObject` under `drop/`, and only with `If-None-Match`, so it cannot overwrite. It cannot list, read or delete.
- **The S3 host must be on each environment's network allowlist** (`<bucket>.s3.<region>.amazonaws.com`). Where it is not, every PUT fails and backs off, and records are lost as they are today.
- **A leaked key can add records but read nothing.** Integrity is weaker than the git route's. Anyone holding the key can add plausible forged records, timestamped within the 48-hour window the ingest accepts around each object's `LastModified`. The ingest validates every field and dedupes by session and id, but it cannot tell a forged record from a real one. The key cannot read, overwrite or delete anything already stored. The census treats cloud records as "records seen", never as a complete count, so no record from either route proves a skill was or was not used.
- **It costs about $0.10 a month**, and an optional $1 budget alarm watches it.
- **Only S3 is non-free.** The queue, the Lambda function, SSM Parameter Store standard parameters (not Secrets Manager), the logs and OIDC all stay inside AWS's always-free tiers.
- **A service outside GitHub to keep alive.** If the stack breaks, objects wait in the bucket for up to 30 days, the `drop/` lifecycle, and the daily ingest run picks them up once it is fixed.
- **S3's own 403 stops tries for the session for any reason.** That includes a clock skew or a bad signature, so a wrong secret looks the same as a missing grant.
- **Phase 1 is retried only in `CLAUDE_CODE_REMOTE=true` sessions.** A session marked remote only by `CLAUDE_CODE_REMOTE_SESSION_ID` keeps no pending state, so its phase-1 line gets one try. The local record's `uploaded` describes only the first try.
- **A merge ships it fleet-wide**, as with ADR 0036. Sessions without the four variables keep using the git route.

## Hardening (review 2026-11-03)

A second adversarial review, of the built sink, led to these fixes.

In the hook:

- Only a 403 carrying `x-amz-request-id` stops the session's tries; any other 403 backs off.
- `surface` and `cli` are clamped to the patterns the ingest accepts, and sent as null otherwise, on both routes. So one odd transcript value cannot get a whole object rejected, and the two routes still send identical lines.
- The object key's date and `sent` must be ASCII digits before anything is sent.
- A phase-1 line that did not land is held and retried (see Decision point 4).
- Every `USAGE_SINK_*` variable is removed from the environment of every subprocess, whichever route runs.
- The `GUIDANCE_RECORD_NOW` clock seam is read only under `GUIDANCE_RECORD_TEST=1`.

In the ingest and the stack (skills-evals and `adam-agentskills-private`):

- Key patterns are ASCII only.
- Objects are processed in `LastModified` order, and each record's timestamp is checked against `LastModified`.
- A bad record is rejected alone, not with its whole object, and each rejection is tagged with a validator version so a later fix can plan it again.
- An object that cannot be read is skipped instead of failing the run.
- A lifecycle rule handles large objects.
- The ingest script is pinned to a reviewed commit.
- No write token is present while `apply` runs.
- The dead-letter queue has an alarm.

## Alternatives considered

- **The encrypted drop branch.** Rejected after the review above. Fixed, it still leaves permanent public ciphertext and fleet-wide forgery, and it needs a polling ingest across every fleet repo.
- **Auto-attaching the private repo** through a guidance line. Rejected as the only route: a hook cannot attach a repo, and a guidance line depends on the session complying.
- **A GitHub App token from an environment secret.** Ruled out: the proxy replaces its header and refuses `/app/*`.
- **Another non-GitHub sink** (a Cloudflare Worker, a Google Apps Script, an OTLP collector). Not chosen: each needs the same allowlist entry and a write token. S3 gives a write-only, no-overwrite key with no code of ours on the receiving end.
- **Claude Code's OpenTelemetry export.** Deferred: whether its Skill events carry the skill name is unchecked, and it needs a collector too.
