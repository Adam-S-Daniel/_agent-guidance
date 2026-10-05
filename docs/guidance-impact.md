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

## 2026-10-04 — test-that-can-signal-kills-every-session — edit
- Motivation: owner decision on the session-7 prompt audit's "History narratives" findings: move incident narratives longer than about two lines to `docs/evidence/` and leave a pointer (ADR 0018).
- Change: the four-line `kill(-1)` story moved verbatim to `docs/evidence/2026-10-04-killpg-on-a-mocked-pid.md`, replaced by a one-clause dated pointer with its full URL; the five rules are unchanged; section 1,011 -> 943 bytes (-68).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — two-github-connectors — edit
- Motivation: the section says to probe the org connector by NAME, never a remembered prefix, yet labeled it `mcp__github-mcp__*`; a 2026-10-04 terminal session exposed it as `mcp__claude_ai_github-mcp__*` (flagged by 6 of the session-7 prompt audits).
- Change: label the bullet by connector name `github-mcp` and say its tool prefix varies by surface; the dated `mcp__b26ebb34-…__*` history stays; section 1,396 -> 1,410 bytes (+14).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — two-setup-gaps — edit
- Motivation: [_agent-guidance#175](https://github.com/Adam-S-Daniel/_agent-guidance/issues/175) rechecks plugin commit-recording fixes without treating metadata changes as proof that installed contents refresh.
- Change: require CLI ≥ 2.1.280 for recorded SHAs, inspect differing commits, locate bundle contents via `installPath`, try update and recheck before conditional reinstall; retain the historical version-gate distinction and next-session loading rule; section 2,146 -> 2,029 bytes (-117).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — test-that-can-signal-kills-every-session — create
- Motivation: 2026-10-04 skills-evals test (https://github.com/Adam-S-Daniel/skills-evals/pull/250) killed every process the user owned: a mocked `Popen` made cleanup run `os.killpg(1, SIGKILL)`, i.e. `kill(-1, SIGKILL)`; the kill recurred ~15 times when resumed agents re-ran the suite.
- Change: new section: refuse any pid or group but an `int` > 1, patch every signal call when mocking a spawn, treat a sandboxed 137 with no OOM as a finding (never escalate out), a session dying mid-verifier is evidence against the verifier, run spawning/signaling suites in a PID namespace; section 1,011 bytes (new).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — working-in-these-repos — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: dropped `(email, bio, proposal)` after the writing-style rule; AST-check bullet: dropped the parenthetical `(jodidaniel host-loop gap, `cms-platform`)` and the `page.goto(...)` example, keeping `regex cannot see a template-literal variable`; section 1,376 -> 1,268 bytes (-108).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — finding-your-unknowns — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: removed emphasis bold from two words; section 794 -> 786 bytes (-8).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — workstation-layout — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: removed emphasis bold from `Windows`/`WSL`; section 157 -> 149 bytes (-8).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — data-exposure-in-ci — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: removed emphasis bold on `public` and `PRs`, dropped the undated aside `(a workflow once logged email addresses)` and the parenthetical `(objects stay fetchable by SHA until GC)`; section 1,289 -> 1,197 bytes (-92).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — network-allowlists — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: condensed the intro sentence, removed emphasis bold, shortened the quoted checkbox label to `also include default list…`; section 662 -> 618 bytes (-44).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — automation-vs-branch-protection — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: dropped the quoted `405 ... is cancelled` error text and `(e.g. the AGENTS.md sync App)`, removed emphasis bold on `parses`; section 2,170 -> 2,083 bytes (-87).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — two-github-connectors — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: removed emphasis bold on `strict subset` and `only`; section 1,404 -> 1,396 bytes (-8).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — fleet-spans-two-owners — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: removed emphasis bold on `to a search index` and `plausible, complete-shaped, wrong`; section 1,061 -> 1,053 bytes (-8).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — github-404-means-not-authorized — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: removed emphasis bold on `404, not 403`; `add_repo "already attached" is session scope, not the connector's` -> `... is session scope`, `the other had just read` -> `the other had read`; section 918 -> 884 bytes (-34).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — watch-finished-is-not-ci-passed — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: dropped the undated `(e2e and lint FAILURE once read green)` and `(it broke sync.sh)` asides and the second, redundant here-string idiom for the `grep -q` fix; the first idiom and the rule stay; section 1,485 -> 1,375 bytes (-110).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — name-becomes-scanner-data — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: removed emphasis bold on `keyword`, tightened the `cms-platform-secrets` anecdote (same facts, minus `until history is rewritten`); section 1,106 -> 1,022 bytes (-84).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — pinning-github-actions — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: dropped the example `uses:` block (the sentence above states it), the closing `Nothing third-party is ever a tag.` (the lead already says never a tag), and `Resolve a version with` -> `Resolve one with`, dropped `or the Dependabot PR title`; section 1,506 -> 1,357 bytes (-149).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — subagent-delegation — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: dropped the undated `(a reviewer once did)`, shortened the `echo $?` parenthetical, dropped `output faces the same test/CI proof` (restated by the verifier bullet and Working in these repos), removed emphasis bold on two phrases, `e.g. ` before Explore/Plan, `before disarming anything` -> `before disarming`; section 2,681 -> 2,581 bytes (-100).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — skills-ecosystem — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: dropped the `(cloud-safe, default-on)` and `(machine-bound)` plugin annotations, removed emphasis bold on `into` and `no`; section 1,277 -> 1,228 bytes (-49).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — two-setup-gaps — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: removed emphasis bold on six phrases, `Neither check` -> `Neither`; section 2,184 -> 2,146 bytes (-38).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — git-practices — edit
- Motivation: Making room under the 25,600-byte cap for the new kill-minus-one section (skills-evals#250 incident); wording-only trim, no rule, date or link dropped.
- Change: condensed the worktree bullet (`other checkouts untouched`), `redo the work there` -> `redo the work`; section 1,518 -> 1,484 bytes (-34).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — section-python — edit
- Motivation: the same 2026-10-04 incident: `MagicMock` implements `__index__`/`__int__` as `1`, so a mock reaching an int-taking OS call acts on pid 1.
- Change: added a bullet: patch `os.kill`/`os.killpg`/`os.waitpid`/`os.close` whenever `subprocess` is mocked, or use `spec=subprocess.Popen` with an explicit `pid`; section 544 -> 786 bytes (+242).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — working-in-these-repos — edit
- Motivation: review found the new pause rule unclear about `on-hold` being a label and the owner pausing the item.
- Change: clarified the label, prohibition, passive status, and owner control; the base grew from 25,154 to 25,251 bytes (+97 this revision; +321 total versus origin/main, including the initial +224-byte addition).
- Eval: local deterministic [test/test-on-hold-label.js](../test/test-on-hold-label.js), policy mutation: 1 failed, 8 skipped, exit 1; unmutated suite: 9 passed, exit 0.
- Outcome: pending — proposed 2026-10-04

## 2026-10-04 — working-in-these-repos — edit
- Motivation: the owner requested a standard pause for issues and PRs on 2026-10-04, using the [GHA-bench cloud benchmark PR](https://github.com/Adam-S-Daniel/GHA-bench/pull/44) as the example.
- Change: added the owner-controlled `on-hold` rule, passive status allowance, and prohibition on paused work and follow-ups; full policy and provisioning in [on-hold-label.md](reference/on-hold-label.md); +224 bytes.
- Eval: local deterministic [test/test-on-hold-label.js](../test/test-on-hold-label.js): `node --test test/test-on-hold-label.js`, 9 tests, exit 0; all 9 guards independently reject in-memory policy or provisioning mutations (each 1 failed, 8 skipped, exit 1).
- Outcome: pending — proposed 2026-10-04

## 2026-10-04 — fleet-spans-two-owners — edit
- Motivation: [#177](https://github.com/Adam-S-Daniel/_agent-guidance/issues/177): CLI 2.1.282 added attaching a repo from a different GitHub owner to a running cloud session, so "hosted sessions refuse cross-owner attachment" is stale.
- Change: the reach bullet now says a session's reach is per-session and may miss an owner, true with or without cross-owner attachment; -6 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — two-setup-gaps — edit
- Motivation: [#175](https://github.com/Adam-S-Daniel/_agent-guidance/issues/175): the "install behind" check failed silently. Measured on CLI 2.1.289 in a scratch config: the marketplace clone is `git clone --depth 1` (re-cloned on update), so after a refresh `merge-base --is-ancestor <gitCommitSha> HEAD` exits 128 and `rev-list --count` prints nothing; at sha == HEAD the ancestor test succeeds, reading "behind".
- Change: compare `gitCommitSha` with the clone's `rev-parse HEAD` (equal current, other behind, missing unknown) and say why ancestry and counting fail; to stay byte-neutral, locate the clone and bundle by `installLocation`/`installPath` (both measured in the same run) instead of spelled-out paths; -46 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — subagent-delegation — edit
- Motivation: [#176](https://github.com/Adam-S-Daniel/_agent-guidance/issues/176): since CLI 2.1.271, custom and plugin subagents with `omitClaudeMd` run without user, project and local CLAUDE.md, so they never see this guidance.
- Change: named `omitClaudeMd` agents among the children that skip this file; +6 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-04 — skills-ecosystem — edit
- Motivation: [#174](https://github.com/Adam-S-Daniel/_agent-guidance/issues/174): the "CLI 2.1.273+" floor is disputed (docs say 2.1.273, the changelog 2.1.275) and synced skills answer to their short name since 2.1.269/2.1.281.
- Change: dropped the version floor; synced skills run as `/<name>`, or `/anthropic-skills:<name>` when the short name is taken; +14 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-04

## 2026-10-02 — subagent-delegation — edit
- Motivation: the owner's user-level delegation preamble (`~/.claude/CLAUDE.md`) had no repo source and was Claude-only; the fleet section named model families and Claude-specific mechanics.
- Change: made the delegation bullet vendor-neutral (cheapest capable tier, mid tier, orchestrator, child session), folded in the spec contents and test/CI-proof gate, kept the Claude Code `settingSources: []` trap as a labeled example (PR #231).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02 (PR #231)

## 2026-10-02 — git-practices — edit
- Motivation: the owner's user-level "Separate workspaces" preamble (`~/.codex/AGENTS.md`) had no repo source and reached Codex only.
- Change: added the one-worktree-per-independent-coding-session rule, vendor-neutral (PR #231).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02 (PR #231)

## 2026-09-29 — dependency-updates — edit
- Motivation: cms-platform, fastmail-actions, adam-agentskills, repo-settings, and claude-memory-map already exclude their own cms-platform releases from cooldown because required checks gate the release tag; scheduled-run-health callers fell 20 releases behind in [cms-platform#424](https://github.com/Adam-S-Daniel/cms-platform/issues/424), closed 2026-09-22.
- Change: Records cms-platform's cooldown exclusion (#424) (PR #214).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-29 (PR #214)

## 2026-09-29 — pinning-github-actions — edit
- Motivation: cms-platform, fastmail-actions, adam-agentskills, repo-settings, and claude-memory-map already exclude their own cms-platform releases from cooldown because required checks gate the release tag; scheduled-run-health callers fell 20 releases behind in [cms-platform#424](https://github.com/Adam-S-Daniel/cms-platform/issues/424), closed 2026-09-22.
- Change: Limits the 7-day wait to third-party releases, preserving the cms-platform release-tag carve-out (PR #214).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-29 (PR #214)

## 2026-09-29 — sessions-get-cut-off — remove
- Motivation: the owner said the section is out of date (2026-09-29): the laptop-drops-sessions premise, and the commit-as-you-go / resume-pointer duties built on it, no longer apply.
- Change: removed the "Sessions get cut off" section and its eval-coverage.yml row (PR #213).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-29 (PR #213)

## 2026-09-28 — workstation-layout — edit
- Motivation: One item was missing and one did not belong.
- Change: Added WSL paths. Removed misplaced prompt-elevation note. Dropped the `$env:COMPUTERNAME` hint. A Git Bash full-path note drafted for this section was left out because adam-agentskills' README already carries it (adam-agentskills PR #36).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-28 (PR #203)

## 2026-09-28 — skills-ecosystem — edit
- Motivation: adam-agentskills PR #34 (ADR 0014) retired sync-skills and its global pre-push hook, so the "re-run `bash setup.sh`" advice for a push failing in every repo was false; a machine that pulled it without cleanup still fails because the hook points at a deleted script.
- Change: the every-repo push-failure bullet now names the retired hook and `setup.sh --owner-machine` as the cleanup; ADR 0014 reference dropped for budget (PR pending).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-28

## 2026-09-22 — workstation-layout — edit
- Motivation: ZENDA is retired (_agent-guidance#117); a bullet naming the old host sends every session to a machine that no longer exists.
- Change: Windows guidance no longer includes a real machine name. It also adds where clones live in WSL; the manifest row moves from `skipped` (it was parked pending this rewrite) to `gap` (PR #157)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-22 (PR #157)
## 2026-09-28 — working-in-these-repos — edit
- Motivation: the owner asked that every added or changed text use American English spelling (behavior, not behaviour), fleet-wide across both owners; the managed text itself said "behaviour".
- Change: new bullet requiring American English spelling in all added or changed text, comments and commits included; "New behaviour" → "New behavior". Room made inside the 28 KiB full-build budget by shortening the preamble's truncation sentence (PR #193).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-28

## 2026-09-28 — name-becomes-scanner-data — edit
- Motivation: same owner request; the section said "serialising".
- Change: "serialising" → "serializing", no other change (PR #193).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-28

## 2026-09-28 — two-setup-gaps — edit
- Motivation: bytes for the American English bullet inside the full-build size budget (28,668 of 28,672 before).
- Change: the opening sentence stops restating the heading ("Two setup gaps no repo can commit" → "No repo can commit either"); meaning unchanged (PR #193).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-28

## 2026-09-28 — dependency-updates — edit
- Motivation: the owner reversed "the installed one, else latest" (written with a frequently updated laptop CLI in mind): a runner's preinstalled CLI can be any age, so a run should always install the newest release.
- Change: a run (CI, an eval) installs the harness CLI at npm `latest`, not the installed one, and records the version and models used (PR #188; companions https://github.com/Adam-S-Daniel/skills-evals/pull/203 and https://github.com/Adam-S-Daniel/adam-agentskills/pull/29).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-28

## 2026-09-27 — dependency-updates — edit
- Motivation: the owner asked to stop adopting harness releases through manual pin-bump PRs: use the harness the environment already has, else install latest, and record which harness and model versions each run used.
- Change: harness CLIs (Claude Code, Codex; not `uses:` refs or SDK packages) are no longer pinned at all: the installed one, else latest, recording the harness version and the models used. Replaces this PR's earlier "newest release, still exact" wording; the "(they inherit `default-days`)" aside is dropped to stay inside the 28,672-byte full-build cap (PR #186; companions https://github.com/Adam-S-Daniel/skills-evals/pull/203 and https://github.com/Adam-S-Daniel/adam-agentskills/pull/28).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-27

## 2026-09-27 — dependency-updates — edit
- Motivation: the owner adopts new model harnesses on day one in practice, and asked the fleet rule to match; the 7-day wait had CI running Claude Code 2.1.211 while `latest` was 2.1.283.
- Change: model harnesses (Claude Code, Codex CLI) skip the 7-day wait for a hand bump and take the newest release, still pinned exact; the rest of the section is reworded to pay for it inside the 28,672-byte full-build cap (PR #186; companion pin bumps in https://github.com/Adam-S-Daniel/skills-evals/pull/203 and https://github.com/Adam-S-Daniel/adam-agentskills/pull/28).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-27

## 2026-09-24 — workstation-layout — edit
- Motivation: the public skills registry moved from Adam-S-Daniel/agentskills to Adam-S-Daniel/adam-agentskills (its ADR 0013), and the owner asked for the laptop's hostname to leave the fleet guidance.
- Change: The clone-location bullet names "the owner's Windows laptop" instead of a hostname; the WSL-elevation bullet drops the retired `adam-local` bundle name and keeps the skill name (PR #167)
- Eval: exempt (skipped row)
- Outcome: pending — opened 2026-09-24 (PR #167)

## 2026-09-24 — sessions-get-cut-off — edit
- Motivation: the owner asked for the laptop's hostname to leave the fleet guidance; the full build sits at its 28,672-byte cap, so the added words are paid for in the same PR.
- Change: The opening line names "the owner's laptop" instead of a hostname; "frequently" -> "often" and "the commit message ... or an ADR" -> "a commit message ... or ADR", wording only (PR #167)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-24 (PR #167)

## 2026-09-24 — subagent-delegation — edit
- Motivation: the public skills registry moved to Adam-S-Daniel/adam-agentskills, where `disarm-inherited-reach` ships in the `adam-coding-anywhere` plugin, so `/adam:disarm-inherited-reach` no longer resolves.
- Change: The invocation is now `/adam-coding-anywhere:disarm-inherited-reach`; "(a reviewer once did, unasked)" loses "unasked" to stay inside the byte cap (PR #167)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-24 (PR #167)

## 2026-09-24 — skills-ecosystem — edit
- Motivation: the public skills registry moved from Adam-S-Daniel/agentskills (bundles `adam`, `adam-local`, `fastmail`) to Adam-S-Daniel/adam-agentskills (plugins `adam-anything-anywhere`, `adam-coding-anywhere`, `adam-coding-local`, `adam-non-coding-local`), and agentskills-private was renamed adam-agentskills-private.
- Change: Names the new registry, its four plugins and the `/<plugin>:<skill>` invocation form; ADR citations read "registry ADR NNNN" (same numbers in adam-agentskills); the private registry's new name; "at session start" -> "at start" for the byte cap (PR #167)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-24 (PR #167)

## 2026-09-24 — two-setup-gaps — edit
- Motivation: the marketplace a durable machine adds and updates is now adam-agentskills, the multi-repo snippet lives in that repo, and the owner asked for the laptop's hostname to leave the fleet guidance.
- Change: `marketplace add`/`update` name adam-agentskills; the snippet's home names adam-agentskills; the INSTALL example names "the owner's laptop"; small wording trims ("Once per session", "unset in", "never assume it", "yet says it did") keep the full build inside its 28,672-byte cap (PR #167)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-24 (PR #167)

## 2026-09-22 — skills-ecosystem — edit
- Motivation: Claude Code 2.1.273+ syncs the claude.ai account store into terminal sessions too (agentskills issue #158); the section still read as though that channel were cloud-only.
- Change: Added the terminals bullet (setup.sh opts a machine out, cloud sessions can't; agentskills' ADR 0010) and tightened the section's other bullets, wording only, so the full build stays at the 28,672-byte cap; section 1274 -> 1287 bytes (PR #153)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-22 (PR #153)

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

## 2026-09-15 — dependency-updates — edit
- Motivation: the section prescribed `semver-major-days: 30` for every ecosystem with no exception; GitHub rejects that key on `github-actions` as a schema error, and four repos ran zero Dependabot updates from 2026-08-10 until found (#133).
- Change: restricts `semver-major-days` to ecosystems that support SemVer cooldown and states it is never valid on `github-actions` (PR #136)
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-15 (PR #136)

## 2026-09-15 — git-practices — edit
- Motivation: cms-platform#434's body put a closing keyword before the full URL of _agent-guidance#136, a PR in another repo, and merging #434 left that PR closed unmerged; the rule from PR #137 named only issue numbers (_agent-guidance#132).
- Change: The closing-keyword bullet now covers full issue/PR URLs in any repo, says a PR is closed too, and no longer implies backticks protect anything (PR #142)
- Eval: scratch tests 2026-09-15, n=6, one per case: a keyword before a full URL closed its target 4/4 where no backtick touched the URL (same-repo issue and cross-repo PR from a PR body; cross-repo PR from a commit message, plain and in backticks with a trailing space) and 0/2 where one did (PR-body code span; commit-message backticks abutting the URL) — https://github.com/Adam-S-Daniel/_agent-guidance/issues/132
- Outcome: pending — opened 2026-09-15 (PR #142)

## 2026-09-15 — git-practices — edit
- Motivation: cms-platform#283 closed unfixed through merge commit `78617e1`, whose hand-written message quoted the closing keyword in a code span (_agent-guidance#132).
- Change: Adds the closing-keyword rule and tightens the squash bullet so the full build stays under its 28,672-byte ceiling (PR #137)
- Eval: scratch-claude-001 tests, n=6, one per case: a code span in a commit message closed its issue 3/3 (branch commit, merge-time `--body`, `PR_BODY` merge commit); a PR body closed 1/1 plain and 1/1 in double quotes, 0/1 in a code span — https://github.com/Adam-S-Daniel/_agent-guidance/issues/132#issuecomment-5687434470
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

## 2026-09-24 — git-practices — edit
- Motivation: agentskills#179 said "For #176", so merging it left #176 open for a manual close.
- Change: The closing-keyword bullet now says `Closes #N`, not `For #N`, when closing is meant; net −2 bytes (dropped the `--body`/`PR_BODY` aside) because the full build sat at its 28672-byte budget.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-24

## 2026-09-24 — subagent-delegation — edit
- Motivation: a delegated `_agent-guidance#163` check ran `bash test/run-tests.sh; echo "Exit code: $?"`, printed `Exit code: 1` and `1409 passed, 2 failed`, and reported "exit code 0" — the tool's code for the trailing `echo`. The parent caught it by re-running.
- Change: The verifier bullet now says to run the verifier LAST in the delegated command and to re-run a self-contradicting report; the section was trimmed elsewhere to fit, net +2 bytes (the full build was at its 28672-byte budget).
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-24

## 2026-09-28 — automation-vs-branch-protection — edit
- Motivation: repo-settings#51 — skills-evals' roster PR (opened and auto-merged with GITHUB_TOKEN behind the required `test` check) became the declared exception to "PR + auto-merge is not a sanctioned bot-write path"; the rule said the opposite with no exception.
- Change: the auto-merge bullet names the exception and cites repo-settings ADR 0003 (PR #191); three phrases trimmed to fit, net +1 byte, full build 28668 of 28672.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-28

## 2026-09-28 — skills-ecosystem — edit
- Motivation: adam-agentskills ADR 0014 retires the claude.ai account-store upload path and the global sync-skills pre-push hook; the section still described the hook as live and implied the account store carries the fleet's skills.
- Change: the every-repo push-failure bullet now names a missing `sync-skills` hook script and `setup.sh --owner-machine` (which unregisters it); the terminal bullet says terminals still load Anthropic's own account skills; net small byte change.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-09-28

## 2026-10-02 — automation-vs-branch-protection — edit
- Motivation: the vendor changelog routine now accumulates on a branch that outlives its PRs, and nothing in such a branch's name told a person, an agent or delete-on-merge to leave it alone (repo-settings ADR 0007).
- Change: one bullet naming `persistent/<purpose>` for branches that outlive their PRs, guarded by a per-repo deletion-only `extra_rulesets` entry in `fleet.yml`, never deleted and skipped by stale-branch cleanups; +278 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — automation-vs-branch-protection — edit
- Motivation: skills-evals' results branch was renamed `eval-results` → `persistent/eval-results` (repo-settings ADR 0007), so the section's example named a branch about to be deleted.
- Change: the results-branch example now names `persistent/eval-results`; +11 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — working-in-these-repos — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: one bullet routing writing under Adam's name to the `adam-writing-style` skill; test and tests-for-interface bullets tightened; +75 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — git-practices — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: new bullet: before a PR, `git log origin/<base>..HEAD` lists only this task's commits (stale worktree WIP trap); +295 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — automation-vs-branch-protection — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: two bullets: every PR CI job is a required check with its ruleset change in the same PR; workflows filter on salient paths, required checks use always-run + early-skip; +667 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — anything-you-name-gets-its-link — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -65 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — finding-your-unknowns — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -14 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — workstation-layout — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: clone-location list collapsed to one sentence, no rule change; -117 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — security — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -4 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — data-exposure-in-ci — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -42 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — network-allowlists — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -26 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — two-github-connectors — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -11 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — github-404-means-not-authorized — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -20 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — fleet-spans-two-owners — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: two bullets merged and wording condensed, no rule change; -55 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — watch-finished-is-not-ci-passed — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -53 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — git-push-does-not-mean-commit-exists — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -39 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — dependency-updates — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -3 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — name-becomes-scanner-data — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -52 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — subagent-delegation — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -76 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — skills-ecosystem — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -28 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — two-setup-gaps — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: wording condensed, no rule change; -71 bytes.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02

## 2026-10-02 — pinning-github-actions — edit
- Motivation: Fold the Claude-only user-level rules (~/.claude/AGENTS.md) into the fleet block so Codex receives them too; the block had 520 bytes of headroom under 24 KiB, so every section was condensed.
- Change: one punctuation tweak, no rule change; 0 bytes net.
- Eval: none — no fixture yet
- Outcome: pending — opened 2026-10-02
