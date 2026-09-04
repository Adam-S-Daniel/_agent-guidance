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
change, newest first. `scripts/check-guidance-touch.js` (CI: the "Guidance
touch gate" step in `ci.yml`) enforces that a PR touching a section's extent
adds an entry for it here.

**This repo is public and scanned. Nothing sensitive in an entry, ever** — a
sensitive rejection is recorded by PR link alone.

## Entry format

```
## YYYY-MM-DD — <section-id> — <create|edit|rename|remove|rejected>
- Motivation: one line — the incident, pattern, or issue that prompted it
- Change: one line — what changed (PR #NNN)
- Eval: a result (exit 0, section 7.0/8 vs none 4.3/8, n=3, model, report
  link), "none — no fixture yet" (legal only while the manifest row is
  `gap`), or "exempt (skipped row)"
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
