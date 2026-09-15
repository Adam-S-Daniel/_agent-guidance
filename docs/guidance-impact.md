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

## 2026-09-15 — git-practices — edit
- Motivation: cms-platform#283 closed unfixed through merge commit `78617e1`, whose hand-written message quoted the closing keyword in a code span (_agent-guidance#132).
- Change: Adds the closing-keyword rule and tightens the squash bullet so the full build stays under its 28,672-byte ceiling (PR #137)
- Eval: scratch-claude-001 tests, n=6, one per case: a code span in a commit message closed its issue 3/3 (branch commit, merge-time `--body`, `PR_BODY` merge commit); a PR body closed 1/1 plain and 1/1 in double quotes, 0/1 in a code span — https://github.com/Adam-S-Daniel/_agent-guidance/issues/132#issuecomment-5687434470
- Outcome: pending — opened 2026-09-15 (PR #137)

## 2026-09-15 — two-github-connectors — edit
- Motivation: room for the closing-keyword rule (_agent-guidance#132): the full build with every section was 28,648 of the suite's 28,672-byte ceiling.
- Change: Drops "a 404 means not visible to THIS connector — re-check on the other", which the adjacent 404 section already says (PR #137)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-15 (PR #137)

## 2026-09-15 — fleet-spans-two-owners — edit
- Motivation: room for the closing-keyword rule (_agent-guidance#132), same size budget as the two-github-connectors entry.
- Change: Folds the "Enumerate owners; never hardcode one" bullet into the intro sentence that already names `SYNC_OWNERS` (PR #137)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-15 (PR #137)

## 2026-09-14 — finding-your-unknowns — edit
- Motivation: a memory note written this session held facts that lived only on a PR branch; nothing checked the "never the only copy" rule.
- Change: States the memory-home contract (metadata.home) and the Stop gate that enforces it (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — working-in-these-repos — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 1366 to 862 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — anything-you-name-gets-its-link — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 2681 to 1056 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — finding-your-unknowns — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 1520 to 729 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — workstation-layout — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 653 to 462 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: exempt (skipped row)
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — sessions-get-cut-off — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 1152 to 536 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — security — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 493 to 372 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — data-exposure-in-ci — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 2247 to 1331 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — network-allowlists — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 1223 to 688 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — automation-vs-branch-protection — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 2483 to 1242 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — two-github-connectors — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 4055 to 1492 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — github-404-means-not-authorized — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 1787 to 938 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — fleet-spans-two-owners — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 2987 to 1169 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — watch-finished-is-not-ci-passed — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 3988 to 1509 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — git-push-does-not-mean-commit-exists — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 3026 to 1182 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — dependency-updates — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 1086 to 531 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — name-becomes-scanner-data — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 3292 to 1215 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — pinning-github-actions — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 3963 to 1513 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — subagent-delegation — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 7761 to 2714 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — skills-ecosystem — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 2612 to 1274 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — two-setup-gaps — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 5615 to 2256 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)

## 2026-09-14 — git-practices — edit
- Motivation: Codex 0.154 truncates project instructions at 32,768 bytes (`project_doc_max_bytes`) silently; base.md alone was 55,954 bytes.
- Change: Condensed from 1393 to 714 bytes (section) so the full-mode AGENTS.md and the ~/.codex/AGENTS.md copy fit Codex's project-doc budget (PR #128)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-14 (PR #128)
