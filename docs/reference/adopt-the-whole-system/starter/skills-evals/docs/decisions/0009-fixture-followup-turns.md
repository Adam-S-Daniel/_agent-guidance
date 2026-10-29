# ADR 0009: A fixture may send scripted follow-up turns, identically in both arms

- **Status:** proposed (2026-10-05)
- **Issue:** none; found by the session-9 triage of the rename-pdfs
  qualification runs, which scored both arms at the same objective floor.
- **Decider:** the owner (pending).

## Context

`run_agent` makes one `claude -p` call per arm. No user ever answers, so a
skill that correctly stops to ask before acting can never act. The
`rename-pdfs` skill requires per-file confirmation unless the user types
`auto`. In two local N=3 runs, all six with-skill trials proposed renames
and applied none (one proposed all six correctly), so they scored the 4/8
that a no-op scores, the same as an arm that guessed. The fixture already
recorded this confound and declined to fix it in the prompt. Scoring
proposals instead of files was rejected: the task is a workspace transform
(Class A), and its objective checks score the final files.

Relaxing the skill's confirmation rule to pass the eval was also rejected:
the rule is a real safety property, and the eval should measure a skill that
keeps it.

## Decision

Add an optional fixture key, `followups:`, a non-empty list of non-blank
strings. After the prompt's call succeeds, `run_agent` sends each entry as
one more user turn in the same session and workspace: the first call's
command with the entry in the prompt's place and `--resume <session_id>`
appended, where the session id comes from the previous call's result.

- **Both arms get the same follow-ups.** The text is fixed in the fixture,
  never derived from what the agent said, so it cannot favor either arm.
  Write it so it reads as plain consent without the skill as well as with
  it.
- **Scoring is unchanged.** Objective checks score the workspace after the
  last turn. The judge reads every reply in order, with each follow-up
  between them. A resumed call reports `total_cost_usd` and `modelUsage`
  for the whole session so far, but `usage`, `num_turns` and `duration_ms`
  for that call alone (measured on Claude Code 2.1.289). So cost and
  `modelUsage` come from the last call, and the other three are summed.
  `raw.json` is the last call's result with those totals, and every call's
  result under `turns`.
- **Errors keep their types.** A failed follow-up fails the arm with the
  same error type a failed first call would (`timeout`, `nonzero_exit`,
  `invalid_json`, `agent_error`), its detail prefixed with the follow-up's
  number. A result with no `session_id` to resume is `invalid_json`.
- **Each call gets the whole `timeout_s`.** A fixture with follow-ups can
  take up to (1 + follow-ups) times the timeout; a fixture author keeps
  that inside the eval job's budget.
- **Malformed values fail at load** with a named configuration error (rc 2),
  before any arm runs.
- **Opt-in.** Without `followups:`, the command line, the result dict and
  every error detail are what they were before.

`evals/rename-pdfs/` is the first user, with one follow-up: "Yes, apply all
of the proposed renames (auto)."

## Consequences

- A fixture can now measure a skill that asks first, at the cost of one
  more CLI call per arm per follow-up.
- Follow-ups are scripted, not conversational. A skill that asks a question
  the script does not answer still floors; that is a fixture design problem,
  not something the harness tries to solve.
- The judge sees the follow-up text, so a rubric must not reward an arm
  merely for being told to proceed.
- A real-CLI probe (Claude Code 2.1.289) confirmed that `--resume` keeps
  the session id, the conversation, the workspace and the permission mode.
  No eval run has used this yet.
- If a CLI release changes which result fields are cumulative, the cost
  totals go wrong silently; the hermetic tests pin today's shape, not the
  CLI's.
