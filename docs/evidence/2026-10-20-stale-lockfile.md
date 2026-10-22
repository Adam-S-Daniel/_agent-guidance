# 2026-10-20: a stale lockfile ran the wrong commit, and only `--verify` noticed

Evidence for `agents-md/base.md`,
[Pinning GitHub Actions](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/agents-md/base.md#pinning-github-actions):
"the runner runs whatever commit the lockfile names". The rule stays in
`base.md`; this file keeps the measurement, under
[ADR 0018](../decisions/0018-incident-narratives-live-in-docs-evidence.md).
The decision it supports is [ADR 0023](../decisions/0023-actions-pin-through-lockfiles.md).

## What was done

The workflow-lockfile pilot on `Adam-S-Daniel/ai-usage-dashboard`
([PR #11](https://github.com/Adam-S-Daniel/ai-usage-dashboard/pull/11))
pushed a deliberate test commit, `79a6d1c`, that edited only
`.github/workflows/actions.lock`. It changed `actions/checkout@v7.0.1`'s
`commit` from `sha1-3d3c42e5aac5ba805825da76410c181273ba90b1` (the commit
`v7.0.1` points at) to `sha1-9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0` (the
commit `v7.0.0` points at). The workflow still said `@v7.0.1`. The repo has
`sha_pinning_required: true`.

## What happened

[Run 39357694809](https://github.com/Adam-S-Daniel/ai-usage-dashboard/actions/runs/39357694809)
concluded `failure`, not `startup_failure`; there was no "Invalid lockfile"
annotation.

- Both jobs' setup logs: "Using locked action versions from the workflow's
  lockfile", then "Download action repository 'actions/checkout@v7.0.1'
  (SHA:9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0)". The runner ran v7.0.0's
  code under the v7.0.1 label.
- `node-test`, which has no verify step, passed on that code.
- `pester` failed only at its `gh actions-lock --verify` step:
  "✗ Pinned commit not in upstream history actions/checkout@v7.0.1 / tag
  v7.0.1 now points at 3d3c42e5aac5, lockfile pins 9c091bb21b7c". The commit
  *is* in checkout's history; the message also fires when a pin merely
  disagrees with its tag.

The test commit was reverted in `3698c29` and CI went green.

## The second hole

Run locally against the same repo with the API unreachable
(`HTTPS_PROXY=http://127.0.0.1:9`), `gh actions-lock --verify` v0.2.0 printed
"could not verify locked actions/checkout@3d3c42e5aac5 … retry; the runner
will re-check this pin at job start", then "All 1 workflow valid", and exited
0. Its `--json=valid,findings` output keeps `"valid": true` with
`reachability-unknown` findings. Together with the run above, a lockfile edit
landing during an API outage passed the only guard. The pilot's step now fails
on any finding (`405217a`,
[ci.yml](https://github.com/Adam-S-Daniel/ai-usage-dashboard/blob/405217aaaf0a212aae8a036a2598f133694f6dfe/.github/workflows/ci.yml)).
