"""Routine specs and prompts fit an adopter's fleet, not the source's (DECISIONS C11, C7).

The guidance-centralization audit's live instructions (its stored prompt in
`docs/routines/guidance-centralization.routine.md` and the procedure in
`docs/routines/guidance-centralization.md`) named the source account's own
forks as the fork filter's required control, listed the source Routine's 20
attached repositories, carried the source's measured counts as expected
values, and named a trigger id. An adopter's enumeration has other forks,
other repos and other counts. So, after substitution:

* the control is "the forks your enumeration lists", by name;
* the attached-repository table lists the kit's six repos;
* counts are computed by the run; a measured count stays only where it is
  marked as the source system's history;
* the trigger is "this Routine's own `trig_` id, from `list_triggers`";
* the stored prompt's two source-only checks (the source's `base.md`
  connector-prefix defect and its pull request) are dropped, since the
  adopter's `base.md` starts blank (X9);
* the spec's two history sections are headed "(source system history)", the
  marker the kit's C11 check (export/tests/test_no_leaks.py) skips.

The snapshot's "Stored prompt" byte count and digest are recomputed for the
edited prompt, the way scripts/capture-routine.py computes them. Every edit
is an exact old/new pair that must match once. Runs on the substituted tree.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

SNAPSHOT = "docs/routines/guidance-centralization.routine.md"
SPEC = "docs/routines/guidance-centralization.md"
SWEEP = "docs/routines/prompt-audit-sweep.md"
KIT_REPOS = ("_agent-guidance", "acsi-agentskills", "acsi-agentskills-private", "skills-evals",
             "repo-settings", "wsl-automation")
HISTORY = "(source system history)"

SNAPSHOT_EDITS = [
    ("> This captures the Routine's **configuration**.",
     "> **In the adoption kit** this is the source system's snapshot, with its\n"
     "> stored prompt and repository list adapted for a new fleet. Create your\n"
     "> Routine from it (docs/routines.md in the kit), then regenerate this file\n"
     "> from your own Routine with `scripts/capture-routine.py`.\n"
     ">\n"
     "> This captures the Routine's **configuration**."),
    ("| Trigger id | `trig_EXAMPLE` |",
     "| Trigger id | your Routine's own `trig_` id (read it with `list_triggers`) |"),
    ("""Read this Routine's own allowlist from the Routine — `mcp__Claude_Code_Remote__list_triggers`, trigger `trig_EXAMPLE`, `job_config""",
     """Read this Routine's own allowlist from the Routine — `mcp__Claude_Code_Remote__list_triggers`, this Routine's own trigger (the "Guidance centralization audit" entry; take its `trig_` id from that listing), `job_config"""),
    ("""**Which wins when they disagree depends on which is newer, so check rather than assume.** This prompt was written 2026-08-29. Parts of it were written against `_agent-guidance` PR #85, which was still open at that date — so a clone of `main` may carry a spec that PREDATES this prompt. Establish it in one command before you rely on either:

```bash
git -C _agent-guidance log -1 --format=%cI -- docs/routines/guidance-centralization.md
```

- Spec commit **newer than 2026-08-29** → the spec wins; report the disagreement so this prompt gets fixed.
- Spec commit **older** → this prompt""",
     """**Which wins when they disagree depends on which is newer, so check rather than assume.** This prompt is committed as `docs/routines/guidance-centralization.routine.md`, and a clone of `main` may carry a spec that PREDATES it. Establish it in one command before you rely on either:

```bash
git -C _agent-guidance log -1 --format=%cI -- docs/routines/guidance-centralization.md
git -C _agent-guidance log -1 --format=%cI -- docs/routines/guidance-centralization.routine.md
```

The first line is the spec's commit date, the second this prompt's.

- Spec commit **newer than this prompt's** → the spec wins; report the disagreement so this prompt gets fixed.
- Spec commit **older** → this prompt"""),
    ("It runs before the audits, because a pass over the nineteen repos this Routine is attached to is not a fleet audit if the account holds twenty-two.",
     "It runs before the audits, because a pass over the repos this Routine is attached to is not a fleet audit if the account holds more."),
    ("on 2026-08-28 a checkout cut sixteen minutes before a merge",
     "in the source system on 2026-08-28, a checkout cut sixteen minutes before a merge"),
    ("Measured 2026-08-28 in two sessions hours apart: `user:example-adopter` + `user:example-adopter-two` returned 17 repos, then 14, against a true 22 — silently dropping all five private repos, both forks, and `example-third/example-third`: eight repos. That last one is the instructive miss, because a third owner is structurally invisible to any owner-scoped query.",
     "In the source system (measured 2026-08-28, in two sessions hours apart), the two owner-scoped searches returned 17 repos, then 14, against a true 22 — silently dropping every private repo, both forks and a repo under a third owner. That last one is the instructive miss, because a third owner is structurally invisible to any owner-scoped query."),
    ("Both forks and `example-third` will be missing from the retention check for this reason alone;",
     "Every fork and every repo under an owner outside `SYNC_OWNERS` will be missing from the retention check for this reason alone;"),
    ("a single guess is not a probe: `example-adopter/squarespacetemp` returns not-found while `example-adopter-two/squarespacetemp` resolves, and a wrong-owner guess",
     "a single guess is not a probe: a repo the second owner holds returns not-found under the first owner, and a wrong-owner guess"),
    ("`r.fork === false` matched 0 of 22 on 2026-08-28 and empties the set permanently, reporting a clean pass forever; `!r.fork` matched 20 of 22. Give the filter a control: it must exclude exactly `OctopusDeploy-Api` and `SonosAmpJuicePi` by name. Retaining 0, or retaining everything, is BLOCKED — not empty.",
     "`r.fork === false` matches no repo at all (measured in the source system) and empties the set permanently, reporting a clean pass forever; `!r.fork` is the filter. Give it a control: it must exclude exactly the forks your enumeration lists (the entries that carry `fork: true`), by name, and keep every other repo. Retaining 0, or retaining everything while a fork is listed, is BLOCKED — not empty."),
    ("A bare-name match would classify a new `example-adopter-two/retired-agentskills` as the already-known `example-adopter/retired-agentskills` and drop it from every set.",
     "A bare-name match would classify a new `example-adopter-two/<name>` as an already-known `example-adopter/<name>` and drop it from every set."),
    ("**This Routine carries an MCP connector and nineteen attached repositories,",
     "**This Routine carries an MCP connector and its attached repositories,"),
    ("""`base.md` carried both of those facts WRONG until 2026-08-29 — it named the org connector `mcp__b26ebb34-…__*` and said it "cannot check" a pull request. Both were measured false on 2026-08-28: the prefix is `mcp__github-mcp__` and `pull_request_read` does read check runs. The correction is in `_agent-guidance` PR #85. **Check whether it landed** (`git show origin/main:agents-md/base.md | grep -n 'b26ebb34\\|cannot check'`) rather than assuming either way:

- **Still present on `main`** → it is a live defect that mis-instructs every fleet session, and #85 is the fix already written. Say so and link the PR; do not open a second one, and do not merge it (`base.md` fans out to ~20 repos — see the merge scope below).
- **Gone** → nothing to do; do not re-file it.

""", ""),
    ("`agents-md/base.md` or `agents-md/sections/**` (lands in ~20 repos on the next sync)",
     "`agents-md/base.md` or `agents-md/sections/**` (lands in every fleet repo on the next sync)"),
    ("""The spec's baseline names two promotion candidates the first survey surfaced (the `gh api --jq` HTTP-error-to-stdout gotcha, and example-site-platform's self-described "the owner's standing rule" about AST-vs-regex lints). Apply the judgement standard to them; neither is pre-approved.""",
     "If the spec's baseline names promotion candidates an earlier survey surfaced, apply the judgement standard to them; none is pre-approved."),
    ("that exact mistake produced a confident, wrong report about `example-site-b` on 2026-08-25.",
     "in the source system that exact mistake produced a confident, wrong report about one of its sites on 2026-08-25."),
    ("""It is not reliably nightly (runs 155–172 failed silently for eighteen consecutive nights), and its `Has marker` column was wrong for any `AGENTS.md` over ~48 KB, which cascaded into a false `drift-detected` — `_agent-guidance` issue #81, root-caused as a SIGPIPE race and fixed by #82 (`echo "$x" | grep -q` past the 64 KiB pipe buffer). Verify against the repo itself (`git show origin/main:AGENTS.md`) rather than the dashboard regardless. Do not re-file #81.""",
     """In the source system it was not reliably nightly (eighteen consecutive nights failed silently), and its `Has marker` column was once wrong for any `AGENTS.md` over ~48 KB, which cascaded into a false `drift-detected` (a SIGPIPE race: `echo "$x" | grep -q` past the 64 KiB pipe buffer, since fixed). Verify against the repo itself (`git show origin/main:AGENTS.md`) rather than the dashboard regardless."""),
    ("(GHA-bench's 1,366 `.ts` files are all generated)",
     "(in the source system, one repo's 1,366 `.ts` files were all generated)"),
    ("""So name it as **"Guidance centralization audit", trigger `trig_EXAMPLE`**, and say""",
     """So name it as **"Guidance centralization audit"** with its `trig_` id from `list_triggers`, and say"""),
    ("""— **compute it, do not carry this number forward.** The last measurement, `list_repos {limit: 200}` at 2026-08-29T02:26Z, `has_more: false`: **22 enumerated = 2 forks (`OctopusDeploy-Api`, `SonosAmpJuicePi`) + 1 owner outside `SYNC_OWNERS` (`example-third/example-third`) + 19 in scope**, and those 19 were exactly the 19 this Routine had attached — all three sets empty. `exclude:` was `[]` on `main`, so nothing narrows the 19 further; of them, `_agent-guidance` itself is `SYNC_SELF_REPO` and carries a committed `AGENTS.md` instead of a synced one, leaving 18 sync targets. A run that computes 22 has forgotten to filter; a run that computes 19 and finds a name it does not recognise has found the thing this check exists for.""",
     """— **compute it on every run; this prompt carries no number for it.** The enumeration is forks, plus repos under owners outside `SYNC_OWNERS`, plus the in-scope repos; the in-scope repos minus `exclude:` are `M`. `_agent-guidance` itself is `SYNC_SELF_REPO` and carries a committed `AGENTS.md` instead of a synced one, so the sync targets are `M` minus one. A run whose `M` equals the whole enumeration while forks are listed has forgotten to filter; a run that finds an in-scope name this Routine is not attached to has found the thing this check exists for."""),
]

SPEC_EDITS = [
    ("""pull request rather than by editing a trigger nobody can diff.

---
""",
     """pull request rather than by editing a trigger nobody can diff.

**In the adoption kit:** the dates, counts and repository names in this file's
"Measured" passages, and everything under a heading marked
"(source system history)", were measured on the system the kit was exported
from. They are history, kept for the lessons. Your fleet has its own repos,
forks and counts: compute them every run, as the procedure says.

---
"""),
    ("against ~1,500 lines of repo-specific prose spread over nineteen repos.",
     "against ~1,500 lines of repo-specific prose spread over the fleet's repos\n(nineteen in the source system)."),
    ("forks (`SonosAmpJuicePi`, `OctopusDeploy-Api`), archived repos, and",
     "forks (whichever the enumeration lists), archived repos, and"),
    ("""A pass over the nineteen repos this
Routine is attached to is not a fleet audit if the account holds twenty-two.""",
     """A pass over the repos this
Routine is attached to is not a fleet audit if the account holds more."""),
    ("""Measured against the org connector `github-mcp` on 2026-08-28, in two sessions
several hours apart:""",
     """Measured in the source system against the org connector `github-mcp` on
2026-08-28, in two sessions several hours apart:"""),
    ("""`mcp__Claude_Code_Remote__list_repos {limit: 200}`. Measured 2026-08-28 at
20:33Z: 22 repos,""",
     """`mcp__Claude_Code_Remote__list_repos {limit: 200}`. In the source system,
measured 2026-08-28 at 20:33Z: 22 repos,"""),
    ("""Only the two forks carry the
  key. Measured 2026-08-28: `!r.fork` matches **20 of 22**; `r.fork === false`
  matches **0 of 22** and empties the set permanently.""",
     """Only forks carry the
  key. Measured in the source system on 2026-08-28: `!r.fork` matched **20 of
  22**; `r.fork === false` matched **0 of 22** and empties the set permanently."""),
    ("""**and** it excludes exactly the two known forks by name (`OctopusDeploy-Api`,
`SonosAmpJuicePi`). If it retains `0`, or retains all 22, the filter is broken —""",
     """**and** it excludes exactly the forks the enumeration lists (the entries that
carry `fork: true`), by name. If it retains `0`, or retains everything while a
fork is listed, the filter is broken —"""),
    ("""in `repos.yml` — `cron_coverage.fleet` (13) plus `cron_coverage.out_of_scope`
(9), which is the union that file's own header calls the human-curated
inventory, plus `skills_bootstrap.repos` (10, all of them already in that union)
and `exclude:` (empty since PR #84). Measured on `main` 2026-08-28 that union is
**22 distinct names**, and it matches the enumeration name for name.""",
     """in `repos.yml` — `cron_coverage.fleet` plus `cron_coverage.out_of_scope`,
which is the union that file's own header calls the human-curated inventory,
plus `skills_bootstrap.repos` (all of them already in that union) and
`exclude:`. Compute that union on `main` every run (in the source system on
2026-08-28 it was 22 distinct names, matching the enumeration name for name)."""),
    ("""(on 2026-08-28 that is `example-adopter`, `example-adopter-two` and
`example-third`)""",
     """(in the source system on 2026-08-28, its two owners and a third)"""),
    ("""Guessing one owner is not a probe:
`git ls-remote https://github.com/example-adopter/squarespacetemp` returns
`repository not found` while `example-adopter-two/squarespacetemp` resolves, so a
wrong-owner guess""",
     """Guessing one owner is not a probe:
`git ls-remote https://github.com/<first owner>/<name>` returns
`repository not found` for a repo the second owner holds, while
`<second owner>/<name>` resolves, so a wrong-owner guess"""),
    ("""Measured 2026-08-28:
`git ls-remote https://github.com/example-adopter/scratch-claude-002` returns the
same head sha as `example-adopter-two/scratch-claude-002`, and the connector follows the
redirect too.""",
     """Measured in the source system on 2026-08-28:
`git ls-remote https://github.com/<first owner>/<name>` returned the same head
sha as `<second owner>/<name>` for a transferred repo, and the connector follows
the redirect too."""),
    ("""create
`example-adopter-two/retired-agentskills` and a bare-name match classifies it as the
already-known `example-adopter/retired-agentskills` and drops it from every set.""",
     """create
`example-adopter-two/<name>` and a bare-name match classifies it as an
already-known `example-adopter/<name>` and drops it from every set."""),
    ("""`example-adopter-two/squarespacetemp` sits in `out_of_scope:` while still receiving the
AGENTS.md sync every run.""",
     """a repo in `out_of_scope:` (in the source system, one of the second owner's)
still receives the AGENTS.md sync every run."""),
    ("""`mcp__Claude_Code_Remote__list_triggers`, find `trig_EXAMPLE`
("Guidance centralization audit"), and read""",
     """`mcp__Claude_Code_Remote__list_triggers`, find this Routine's own trigger
("Guidance centralization audit"; note its `trig_` id), and read"""),
    ("""trigger id (`trig_EXAMPLE`) and say the link could not be""",
     """trigger id (its `trig_` id, from `list_triggers`) and say the link could not be"""),
    ("### Where this stood on 2026-08-28\n",
     f"### Where this stood on 2026-08-28 {HISTORY}\n"),
    ("""it now carries an MCP connector and nineteen attached
repositories,""",
     """it now carries an MCP connector and its attached
repositories,"""),
    ("""the Routine is "Guidance centralization audit", trigger
`trig_EXAMPLE`, edited where""",
     """the Routine is "Guidance centralization audit", trigger
id from `list_triggers`, edited where"""),
    ("The nineteen attached repos settle *reach*;",
     "The attached repos settle *reach*;"),
    ("""the ~20-repo
  fan-out above.""",
     """the fleet-wide
  fan-out above."""),
    ("""`exclude:` — which was 19 on 2026-08-28. Compute both;""",
     """`exclude:` — 19 in the source system on 2026-08-28. Compute both;"""),
    ("## Baseline as of 2026-08-28\n",
     f"## Baseline as of 2026-08-28 {HISTORY}\n\nEverything from here to the end of the file was measured on the system the kit\nwas exported from. It is history, not a figure your runs should match.\n"),
]

SWEEP_EDITS = [
    ("""(on 2026-10-05:
`acsi-agentskills-private`, `repo-settings` and `rss-inator`; never rely on
that list, read the field)""",
     """(in the source system on 2026-10-05, three of its repos; a list
goes stale, so never rely on that list, read the field)"""),
]


def _edit(text: str, edits, rel: str) -> str:
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            raise RuntimeError(f"{rel}: expected one occurrence of {old[:70]!r}, found {n}")
        text = text.replace(old, new)
    return text


def _repo_table(owner: str) -> str:
    rows = "\n".join(f"| [`{owner}/{r}`](https://github.com/{owner}/{r}) |" for r in KIT_REPOS)
    return ("## Attached repositories\n\n"
            "Attach every repo the fleet syncs to: the kit's six below, and any you\n"
            "add to `repos.yml` later. Each gets an outcome branch when you save the\n"
            "Routine. A fired session sees these without `add_repo`; anything else in\n"
            "the account it must attach itself, which is the coverage question the\n"
            "spec's §0.5 is about.\n\n"
            "| Repository |\n|------------|\n" + rows + "\n\n")


def apply(root: Path, ctx) -> None:
    owner = ctx["common"]["kit_owners"][0]

    path = root / SNAPSHOT
    text = _edit(path.read_text(encoding="utf-8"), SNAPSHOT_EDITS, SNAPSHOT)
    start = text.index("## Attached repositories\n")
    end = text.index("## Pre-approved tools\n")
    text = text[:start] + _repo_table(owner) + text[end:]
    # The prompt block, fenced as capture-routine.py fences it.
    m = re.search(r"^## Stored prompt\n\n(\d+) bytes, `sha256:[0-9a-f]{64}`\.\n", text, re.M)
    fence = re.search(r"^(~{3,})text\n", text[m.end():], re.M)
    body_start = m.end() + fence.end()
    body_end = text.index("\n" + fence.group(1) + "\n", body_start)
    prompt = text[body_start:body_end]
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    text = (text[:m.start()] + f"## Stored prompt\n\n{len(prompt)} bytes, `sha256:{digest}`.\n"
            + text[m.end():])
    path.write_text(text, encoding="utf-8")

    path = root / SPEC
    path.write_text(_edit(path.read_text(encoding="utf-8"), SPEC_EDITS, SPEC), encoding="utf-8")
    path = root / SWEEP
    path.write_text(_edit(path.read_text(encoding="utf-8"), SWEEP_EDITS, SWEEP), encoding="utf-8")
