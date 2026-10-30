# The incidents behind `agents-md/base.md`'s rules

`agents-md/base.md` loads in every session, so its rules carry the mechanism
and drop the date and incident number
([ADR 0018](../decisions/0018-incident-narratives-live-in-docs-evidence.md);
the prompt-audit finding in _agent-guidance#348). This file keeps what was
taken out, by section, with the original wording, so a rule can still be
traced to what taught it.

## Anything you name gets its link

- Original: "**what you hand over** (jodidaniel.com#176), **what YOU are
  waiting on** (every time you name it), **what you cite as DONE**
  (2026-08-29)." The handover example is jodidaniel/jodidaniel.com#176; the
  "cite as DONE" rule dates from 2026-08-29.

## Two GitHub connectors, and which one you are holding

- Read both `get_check_runs` and `get_status`: "#83", where `get_status`
  read `pending` while every check run was green. The original did not name
  the repo, and _agent-guidance#83 is an unrelated pull request.
- Fewer tools is not less dangerous: 2026-08-19, `github-mcp` 404ed on the
  private `repo-settings`.

## A GitHub 404 means "not authorized", not "not there"

- Try the other connector first: 2026-08-19, a mid-session MCP reconnect
  brought up a server blind to a private repo the other read.

## The fleet spans TWO owners, and a scoped search will not say so

- 2026-08-25: a `user:example-adopter` search "proved" example-site-b had no
  `skills.lock`.
- The denominator: Adam-S-Daniel/_agent-guidance#37, three deleted repos
  counted and a live one missed.

## "The watch finished" is not "CI passed"

- A pipe into `grep -q` is a race: Adam-S-Daniel/_agent-guidance#81 (20
  trials per size: 48 kB 0/20, 95 kB 18/20).

## A successful `git push` does not mean your commit exists

- `secrets-scan` failing closed without `gitleaks`: 2026-08-25, example-site-a.
- A push carrying the wrong ref: 2026-08-29, the commit landed on `main` and
  the push updated a stale local branch.

## Dependency updates

- example-site-platform excluded from the 7-day cooldown: Adam-S-Daniel/cms-platform#424.
- No `semver-major-days` for `github-actions`: Adam-S-Daniel/_agent-guidance#133.

## A name you choose becomes data a scanner reads

- Suppress by value, never by path: Adam-S-Daniel/cms-platform#260.

## A test that can signal can kill every session

- The mocked pid that became `kill(-1, SIGKILL)` was 2026-10-04
  ([evidence](2026-10-04-killpg-on-a-mocked-pid.md)); the resumed agents that
  made one kill fifteen were the same day.

## Subagent delegation (model routing)

- `python3 test_foo.py` exiting 0 having asserted nothing: 2026-08-22.
- A push mid-flight plus a subagent's `git commit --amend` diverging a
  published branch: 2026-08-22.
- Unreaped background children (a 999/0 tree read 985/14): 2026-08-29.

## Git practices

- Squash stranding a lockfile-pinned sha (`cannot resolve ref`): 2026-08-15.
- Closing keywords closing the wrong item: Adam-S-Daniel/cms-platform#283 via
  `78617e1`, and Adam-S-Daniel/_agent-guidance#136 closed by
  Adam-S-Daniel/cms-platform#434.
