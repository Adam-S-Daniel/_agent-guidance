# guidance-impact.md — the fleet-guidance change audit trail

Every change to a `##` section of `agents-md/base.md` or a file under
`agents-md/sections/` gets an entry here — creations, edits, renames,
removals, and **rejected proposals**. The rejected ones are the reason the
file exists: git history records what landed, but nothing records what was
tried and turned down, so the next session re-derives and re-proposes it. An
approach already ruled out is the expensive thing to lose. Modeled on
agentskills' [`docs/skill-impact.md`](https://github.com/Adam-S-Daniel/agentskills/blob/main/docs/skill-impact.md),
with `<section-id>` (the stable key in `agents-md/eval-coverage.yml`) in
place of `<bundle>/<skill>` — guidance sections have no bundle to sit in.

What this file is NOT for: harness, hook, CI, lock or docs changes that don't
touch a `##` section's own extent. Entries append in the same PR as the
change, newest first — a convention for readers, not a rule the gate checks:
`scripts/check-guidance-touch.js` (CI: the "Guidance touch gate" step in
`ci.yml`) compares entries by membership, never by position, so a merge that
re-sorts them is not a violation. What it does enforce is that a PR touching
a section's extent adds an entry for it here.

**This repo is public and scanned. Nothing sensitive in an entry, ever** — a
sensitive rejection is recorded by PR link alone.

## Entry format

```
## YYYY-MM-DD — <section-id> — <create|edit|rename|remove|rejected>
- Motivation: one line — the incident, pattern, or issue that prompted it
- Change: one line — what changed (PR #NNN)
- Eval: a real result naming both what was measured and its outcome — an
  exit code (`exit 0`), a score fraction (`7.0/8`), or a sample size (`n=3`),
  alongside a fixture path, eval id, or report link for context; "none — no
  fixture yet" (legal only while the manifest row is `gap`); or "exempt
  (skipped row)" (legal only while the manifest row is `skipped`). Nothing
  else satisfies this bullet — a placeholder like "TBD" or "TBD (PR #NNN)"
  does not, even though the latter contains a digit.
- Outcome: merged YYYY-MM-DD, or rejected YYYY-MM-DD — one line why. The
  full proposal survives in the closed PR; link it rather than pasting it.
```

Rules:

- **A rejected proposal is the highest-value entry.** Record it even when it
  feels like noise — especially then.
- **Append-only.** A wrong entry gets a correcting entry, not an edit.
- **Whitespace-only changes are still changes.** There is no "small edit"
  exemption — one line in the log costs nothing, and the exemption would be
  the hole every edit walks through.

Entries before 2026-09-04 predate this file and live only in git history —
no backfill is planned; the file adds the fields git does not capture.

## 2026-09-07 — session-start-verdict-is-not-what-loaded — rejected

- Motivation: [#123](https://github.com/Adam-S-Daniel/_agent-guidance/issues/123)'s
  item 1(b) asked the load-time hook to hash a repo's managed region against
  "the synced template recorded in the state file", so a hand edit inside a
  structurally intact managed block would be caught. Round-1 review found the
  verdict named `EDITED ABOVE THE MARKER` while measuring only marker
  structure — a name an operator reads as the check 1(b) asked for.
- Change: the comparison is NOT implemented, and the verdict is renamed to
  `MANAGED BLOCK MALFORMED` so it claims only what it measures (PR
  [#124](https://github.com/Adam-S-Daniel/_agent-guidance/pull/124)). The
  section and the repo stub now say plainly that a hand edit inside an intact
  block reads `current`.
- Eval: none — no fixture yet
- Outcome: rejected 2026-09-07 — there is no comparand to hash against.
  `fleet-guidance.state` records the digest of the PAYLOAD
  (`agents-md/base.md`), while a repo's managed region is rendered per-repo by
  `build-agents-md.sh` from that repo's own `sections:` list. Closing 1(b)
  means emitting a `Managed-sha256:` header line into the managed block at
  sync time and verifying it in the hook — a change to the managed-block
  format in ~20 repos, and a self-referential digest (the line cannot cover
  itself). Worth doing on its own terms and in its own PR; not worth
  smuggling in behind a verdict name.

## 2026-09-05 — session-start-verdict-is-not-what-loaded — create

- Motivation: the session-start verdict reports what `fleet-memory.sh` did to
  `~/.claude/CLAUDE.md`, not what the session loaded. On 2026-09-05 a test
  mutation cut that file from 56,099 bytes to 154 while a session ran, and
  nothing said so until the next SessionStart ([#123](https://github.com/Adam-S-Daniel/_agent-guidance/issues/123)).
- Change: one section naming the gap, the load-time receipt lines the next
  session prints, and `scripts/instructions-report.sh` — the per-session
  measurement that replaces the 332.3k figure quoted by hand since 2026-08-29
  (branch `claude/agent-guidance-123`).
- Eval: none — no fixture yet
- Outcome: opened 2026-09-05; merges with this PR.
