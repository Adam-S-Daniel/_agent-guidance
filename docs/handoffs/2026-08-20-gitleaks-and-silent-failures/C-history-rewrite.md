# Prompt — retire the gitleaks exclusions by rewriting history

Run this **only after** the prerequisite gate below passes. It is the one
sanctioned exception to "never force-push": a security-hygiene remediation.
Do the whole thing with adversarial review, and pull any emergent issue into
scope recursively rather than deferring it.

## Why this exists

`skills.lock` used to serialise a skill named `cms-platform-secrets`, so a
generated, committed, scanned file carried
`"cms-platform/cms-platform-secrets": "<64-hex>"`. gitleaks' `generic-api-key`
rule fires on a keyword beside a high-entropy value; the word "secrets" in the
skill's own name was the entire trigger. Both consumer sites went red on every
push to `main` — adamdaniel.ai for eight consecutive pushes, each one a blocked
editorial publish.

Two upstream fixes have landed: digests are labelled `sha256:<hex>`, and the
skill is renamed `consumer-repo-provisioning`. **Neither can touch history.**
Each affected repo therefore still carries one `.gitleaksignore` fingerprint,
and those files exist only to be deleted.

## Status — re-derived 2026-08-20, gate STILL NOT met, for DIFFERENT reasons

The verdict is unchanged; every reason behind it has changed, so read this
rather than the paragraph you remember.

**Also corrected against
[`_agent-guidance#52`](https://github.com/Adam-S-Daniel/_agent-guidance/issues/52),
which re-measured this gate.** Its verdict matches this file's on all three
conditions. Two of its numbers do NOT match, and this file wins both times
because it measured later — see condition 3 below, and `README.md`,
"Corrections pass".

**Condition 1 now PASSES.** The federated advance this file used to open with
has been done: both consumers' `skills.lock` on `main` carry
`cms-platform/consumer-repo-provisioning`, carry no
`cms-platform/cms-platform-secrets`, and pin `sources[0].ref` at `a59763c6`
(post-rename). The pack's own keyword check exits 0 on both. Read through
`mcp__github__` at `refs/heads/main`; the two files are the same blob,
`a83236d4`.

**Condition 2 PASSES.** Nothing in the fleet pins a commit of either site — no
lock, no `uses:`, no `platform_ref:`. Neither site is a registry and neither
publishes a reusable, so there is nothing for a pin to name.

**Confirmed a second way, which is the form to re-run** (from #52): sweep every
distinct 40-hex token across every repo's default-branch tip and **resolve** each
one, instead of trusting that a commit sha only ever appears in pin syntax. Of
**124** tokens, three resolve inside a site repo and the single cross-repo hit,
`62a5f8f4`, is a **blob** — the `preview-media` sentinel
`assets/images/uploads/e2e-preview-media-probe.png`, gated on its git blob sha1.
That is the answer you want and a grep cannot give you: a rewrite changes commit
shas and leaves blob hashes alone, so the one hit that *looks* like a cross-repo
pin is structurally immune to C.

**Condition 3 FAILS, structurally, and is what actually blocks C.** 117 of
adamdaniel.ai's 3197 PR refs and 24 of jodidaniel.com's 148 contain the
offending commit; 116 and 24 of those respectively belong to pull requests that
are ALREADY closed or merged, and their `refs/pull/N/head` are still live.
Closing a PR does not delete its ref and no client can. Full numbers and method
in `README.md`, "The blocking fact for `C`".

**#52 quotes an earlier figure — "112 PR refs … only 3 are open", and 20 for
jodidaniel.com — and the numbers above supersede it.** Not because this document
outranks that one, but because these were measured afterwards, and the count
only ever moves in one direction: every new PR branched off `main` adds another
permanent ref. **Deferring C makes C harder, monotonically**, which is why a
stale count here always under-states the problem — and why the drift is worth
recording rather than silently overwriting.

**Two more blockers this gate never listed.** Both are below, under "Blockers
the gate does not list". Neither is a wait; each needs a decision someone has to
take deliberately.

**So: do NOT proceed.** And do not spend the first cycle re-doing the federated
advance — it is done.

## PREREQUISITE GATE — verify all three, in writing, before touching history

1. **MET as of 2026-08-20 — re-verify, do not re-do.** The rename has landed in
   `cms-platform`, a release is cut, and **both** consumers' committed
   `skills.lock` no longer contains any gitleaks keyword. The manual federated
   advance described below was performed; `sources[0].ref` is `a59763c6` on both.
   Keep the check as a regression guard — a later lock could reintroduce a
   keyword-bearing key — but budget no work for the advance itself.

   **This did NOT happen on its own — it took a manual step.** Each site's lock
   pins cms-platform as a federated source at an explicit `sources[0].ref`, and
   nothing advances it: `--repin` advances only the PRIMARY ref (the script says
   *"that federated pin is a human's to advance"*), and `platform-bump.yml`
   contains no reference to `skills.lock` at all. Somebody advanced the
   federated ref to a cms-platform commit carrying the rename, regenerated and
   landed it — which is why condition 1 is green and why it would not have gone
   green on its own. The check that confirms it, per repo:
   ```bash
   python3 -c "
   import json,re,sys
   KW=re.compile(r'access|auth|api|credential|creds|key|passwd|password|secret|token',re.I)
   d=json.load(open('skills.lock'))
   bad=[k for k in d['skills'] if KW.search(k)]
   print('KEYWORD-BEARING LOCK KEYS:',bad); sys.exit(1 if bad else 0)"
   ```
2. **MET as of 2026-08-20.** No OTHER repo pins a commit of the repo you are
   about to rewrite. A rewrite changes every subsequent sha; a lockfile naming
   one of them then pins something a fresh clone does not contain. Searched
   `platform.lock`, `skills.lock`, `platform_ref:` inputs and every `uses:` across
   the nine local clones — nothing names either site as a source of code, which
   is structural rather than lucky: neither site is a skills registry and neither
   publishes a reusable workflow. Re-run the search anyway; it costs one grep.
   **If anything pins these repos, STOP and resolve that first.**
3. **NOT MET, and not by waiting.** You have an inventory of every ref
   containing an offending commit — local branches, remote branches, tags, and
   pull requests. A PR ref (`refs/pull/N/head`) keeps a commit reachable
   permanently.

   The inventory exists (README, "The blocking fact for `C`"): 117 of
   adamdaniel.ai's 3197 PR refs and 24 of jodidaniel.com's 148 contain the
   offending commit. **The trap is the word "open".** This gate used to say
   "**open** pull requests", and step 2 of the rewrite says to close them —
   both of which imply a closed PR is harmless. It is not: 116 and 24 of those
   refs are on PRs that are ALREADY closed or merged and every one still
   resolves (`git ls-remote` against each origin, 2026-08-20). GitHub owns the
   `refs/pull/*` namespace and a client cannot write or delete into it — stated
   from documented behaviour, NOT measured here, because measuring it would be
   a write; what IS measured is that 140 of these refs belong to PRs that are
   already closed and are all still live, which is the same conclusion by
   observation. While those refs exist the old commits
   stay *reachable*, so GitHub's own GC never collects them either, and the
   "days to weeks" note further down does not apply to them at all.

   Whoever takes this on has to answer that first, in writing, and it is a
   judgement call rather than a step: either accept that a rewrite removes the
   line from `main` and from every fresh clone while leaving it fetchable by
   sha through ~141 PR refs, or take it to GitHub Support. Do not start a
   force-push having quietly downgraded the goal.

## Blockers the gate does not list

Neither of these is in the three-condition gate above, and both stop the
rewrite dead. They were found by re-deriving the gate on 2026-08-20, not by
attempting anything.

### 1. Both `main` rulesets forbid the force-push, with no bypass actor

Measured live (`GET /repos/{owner}/{repo}/rulesets/{id}`, authenticated REST —
no MCP tool exposes rulesets):

| | adamdaniel.ai | jodidaniel.com |
|---|---|---|
| ruleset | `13985217` "main", **active** | `17032014` "main", **active** |
| rules | deletion, non_fast_forward, pull_request, required_status_checks | same four |
| `bypass_actors` | **`[]`** | **`[]`** |
| `current_user_can_bypass` | **`never`** | **`never`** |

`non_fast_forward` is exactly the rule a rewritten `main` violates, and with an
empty `bypass_actors` there is no actor — not the owner, not an admin, not an
App — who can push through it. Step 1 of the rewrite ("Force-push every
rewritten branch") therefore fails on both repos as configured.

These rulesets are **managed as code**, which is what makes this a decision
rather than a wall: `cms-platform/repo-settings.yml` declares them (the
`consumer-main` ruleset; its comment records that `bypass_actors: []` was a
deliberate convergence), and `node scripts/audit-repo-settings.js --fix --yes`
applies them. So the sequence is: land a `repo-settings.yml` PR granting a
scoped, temporary bypass, apply it, do the rewrite, then land the PR that takes
it away again — and expect the daily `repo-settings-audit` to file a drift issue
in between, which is the system working. Do not hand-flip it in the UI: the
drift report flags exactly that, and a hand-flip that is never reverted is how a
protected branch quietly stops being protected.

### 2. The ZENDA re-clone step cannot be done — or checked — from here

"Preventing re-introduction from existing clones" below is the part that
actually bites, and it is the part a cloud session structurally cannot do.
ZENDA is the durable Windows workstation; its clones live at
`D:\repos\<github-owner-or-org>\<repo>`; a session running in a Linux
container has no path to that filesystem and cannot enumerate its branches,
stashes or worktrees, let alone re-clone them. Nor can it *verify* the step
afterwards — a report that "every clone was re-cloned" written from a cloud
session is an assertion about a machine it never touched.

So: whoever runs C runs the ZENDA half **on ZENDA**, and a cloud session's part
of C ends before it. Say which machine each step ran on. Treat "every machine"
as a real inventory, per the instruction at the end of that section.

## The rewrite

Affected repos, and the fingerprint each `.gitleaksignore` carries:
- `jodidaniel/jodidaniel.com` — `fee19ee47fae4b1a9360feac56863f59b32bce4d:skills.lock:generic-api-key:31`
- `Adam-S-Daniel/adamdaniel.ai` — `39d925036922e2c09344f062886e45a035fc996d:skills.lock:generic-api-key:31`

**A fingerprint names ONE commit; the line lives in more than one.** gitleaks
reports the commit whose DIFF added the line, so the fingerprint is a report,
not an inventory. Measured 2026-08-20 by reading `skills.lock` out of every
commit reachable from `main` that touches it: the offending line is in the tree
of **five** adamdaniel.ai commits (`39d92503`, `1580339a`, `927cd9e9`,
`9b9fcc25`, `d1ce0c55`) and **three** jodidaniel.com commits (`fee19ee`,
`6f93d0a`, `b7663a9`) — every re-pin between the introduction and the labelling
carried it forward unchanged. A blob-content rewrite covers all of them at once,
which is why the instruction below is the right one; a rewrite aimed at the
fingerprinted commit alone would leave four and two behind.

Use **`git filter-repo`**, never `filter-branch` (deprecated, slow, and its
default behaviour leaves originals reachable). Rewrite only the `skills.lock`
blob content — do not drop the commits, which would lose unrelated changes.

Then, in this order:
1. Force-push every rewritten branch. **This is blocked today** — see "Blockers
   the gate does not list", blocker 1; the bypass has to be granted through
   `cms-platform/repo-settings.yml` and taken away again afterwards.
2. Delete (or rewrite) every stale ref found in gate step 3 that you CAN
   delete — branches and tags. **This is NOT nothing, and an earlier draft of
   this file said it was.** Re-measured 2026-08-20 by walking every live remote
   head with `git ls-remote --heads` and testing `git merge-base --is-ancestor`,
   there is exactly one non-`main` branch per repo, and each one's TIP TREE
   still carries the offending line:

   | repo | branch | tip `skills.lock` offending lines |
   |---|---|---|
   | adamdaniel.ai | `cms/posts/delete-5a7734ca-1787068075722` | 1 |
   | jodidaniel.com | `claude/fix-history-secrets-scan` | 1 |

   Tags are genuinely clear: `git ls-remote --tags` returns zero tags on both
   repos, so no tag pins the commit.

   These two are ordinary branches — deletable, and reachable by any
   `git fetch`. Rewriting only `main` leaves the line live on the remote and
   leaves this file's own Definition of done unreachable, because the scoped
   `-G` predicate runs `--all` in a fresh clone and would still match them.
   Decide per branch: delete it if the work is dead (the adamdaniel one is a
   spent Decap `delete-` branch), otherwise rewrite it alongside `main` and
   force-push both. Enumerate from `ls-remote` rather than from a local clone —
   a clone shows stale and local-only branches and can miss remote ones.

   After that, the only refs left are `refs/pull/*/head`, which cannot be
   deleted by anyone. Close the one still-open PR that carries the commit
   (adamdaniel.ai#3198) and reopen equivalent work from rewritten history — but
   do not record closing it as removing the ref, because it does not: the other
   116 are closed already and every one still resolves.
3. Empty each `.gitleaksignore` down to a placeholder comment, or delete it.
4. **Verify with a `workflow_dispatch` run of `secrets-scan.yml`** on the branch.
   That is the only lane that reads full history; a pull request's own `scan`
   job structurally cannot test this, which is precisely how the original
   regression shipped green.
5. Note that GitHub garbage-collects UNREACHABLE objects on its own schedule
   (days to weeks). Until then the old commits remain reachable **by sha**. For
   a public repo you can ask GitHub Support to expedite. Say so plainly in the
   report rather than implying the data is gone the moment you force-push.

   **And note what that paragraph does NOT cover.** GC collects the unreachable;
   `refs/pull/*/head` makes these commits *reachable*, so no amount of waiting
   collects them. The ~141 PR refs are not a delay, they are a different
   outcome, and only GitHub Support can change it.

## Preventing re-introduction from existing clones — the part that actually bites

A rewrite is undone by one careless `git pull`. A stale clone still holds the
old commits and its `main` still points at the old tip; pulling **merges the old
history straight back in**, and pushing that returns every offending commit to
GitHub — by which time the `.gitleaksignore` is gone, so it lands as a hard red
on a protected branch.

**`ZENDA` is the specific risk**: it is the durable Windows workstation, its
clones live at `D:\repos\<github-owner-or-org>\<repo>`, and it drops sessions
mid-task — so a half-finished clone with local commits is an ordinary state
there, not an edge case.

Required, per machine, for each rewritten repo:

1. **Re-clone. Do not pull, do not reset.** Re-cloning is the only reliable
   answer: `git reset --hard` leaves the old objects alive in the reflog, and
   stashes, worktrees and other local branches each keep them reachable
   independently. A "clean" `git status` says nothing about any of that.
2. **Before deleting the old clone, rescue local work**: check every branch for
   unpushed commits, `git stash list`, and `git worktree list`. Export anything
   worth keeping as a patch (`git format-patch`), then re-apply it onto the
   rewritten history — never by merging the old clone in.
3. **Verify after re-cloning** that the offending line is genuinely absent from
   all history, not just the tip — with the SCOPED predicate below, never the
   bare name (see "Two checks that look right and are not").

   **Unshallow first, or the verification lies to you.** A `git clone --depth 1`
   — which is what a CI checkout and several tooling paths produce by default —
   cannot see the history it is meant to be searching, and it does not say so.
   Measured on a `--depth 1` clone of jodidaniel.com, 2026-08-20:

   ```
   $ git cat-file -t fee19ee47fae4b1a9360feac56863f59b32bce4d
   fatal: git cat-file: could not get object info      # exit 128
   $ git log --all -S 'cms-platform-secrets' --oneline | wc -l
   1                                                   # the true answer is 5
   ```

   That `cat-file` failure reads exactly like "the fingerprint is stale, the
   commit is gone" — the conclusion you would most like to reach and the one
   that ends the work early. After `git fetch --unshallow` the same commands
   return `commit` at exit 0 and five commits. Check
   `git rev-parse --is-shallow-repository` before believing any history search,
   and note the shallow answer was *smaller*, not an error: a truncated search
   under-reports silently in the direction of "all clear".

   **The scale of the truncation, measured (#52): reachable commits go from 76
   to 4084 on unshallowing** — a 98% blind spot presented as a complete answer.
   The site clones arrive shallow, so this is the DEFAULT state a re-verifier
   meets, not an edge case they have to construct. It is the same failure mode
   as the bare blobless clone in `README.md` ("Enumerate branches from
   `ls-remote`"): **a view you narrowed yourself shows you only what you asked
   for, and reports the difference as good news.**
4. **Add a `pre-push` guard so this cannot silently recur.** ZENDA already
   carries a global pre-push hook (the sync-skills one), so there is an
   established place and pattern for it. The guard should reject a push whose
   outgoing commits reintroduce a keyword-bearing lock key. Scope it to the
   commits actually being pushed (`git rev-list <remote-sha>..<local-sha>`), not
   the whole history, or it will refuse every push from a not-yet-re-cloned
   clone with no way forward. Make the failure message say "re-clone this repo"
   explicitly — a guard whose message does not name the remedy just gets
   bypassed with `--no-verify`.

Treat "every machine" as a real inventory, not an assumption: list the machines
and clones you believe exist, say how you established that list, and flag any
you could not verify rather than declaring the fleet clean.

## Two checks that look right and are not

**`git log --all -S 'cms-platform-secrets' --oneline` can never be empty, and
should not be.** The name appears in history for four legitimate reasons
besides the lock line, measured 2026-08-20 across both repos:

| commit | file | why it legitimately says the name |
|---|---|---|
| jodidaniel `db88cc8` / adamdaniel `1e816f18` | `AGENTS.md` | the fleet incident writeup names the skill, and the sync fans it out to ~20 repos |
| jodidaniel `aff0d2c` / adamdaniel `50314fe5` | `.gitleaksignore` | its own comment explains the root cause by name |
| jodidaniel `12709fa` / adamdaniel `3315e0d4` | `.gitleaks.toml` | the earlier allowlist |
| adamdaniel `39d92503`, `6eee343f` | `.claude/skills/cms-platform-secrets/SKILL.md` | the vendored mirror, since deleted — the name is a PATH |

Making that command empty would mean rewriting the incident documentation out
of history, which is the opposite of the goal. It also contradicts this file's
own instruction to rewrite the `skills.lock` blob and nothing else. Use the
predicate gitleaks actually fires on — a keyword-bearing lock KEY beside a bare
64-hex VALUE, in `skills.lock`:

```bash
git log --all --oneline -G '"cms-platform/cms-platform-secrets": "[0-9a-f]{64}"' -- skills.lock
```

Empty output is the pass condition. Today it returns three commits on
adamdaniel.ai and two on jodidaniel.com (the introducing and the labelling
commit either side of the run named earlier).

**A green `secrets-scan.yml` is necessary, not sufficient.** It proves the
scanner no longer fires; it does not prove the bytes are unreachable, because
the PR refs above keep them fetchable by sha. Report both facts, separately.

## Definition of done

- Both `.gitleaksignore` files empty or gone.
- A `workflow_dispatch` `secrets-scan.yml` run **green on each rewritten repo**,
  reported with its run id and parsed conclusion — never "the watch finished".
- The scoped `-G` predicate above empty in a **fresh, unshallowed** clone of
  each repo — not the bare-name `-S` search, which cannot go empty.
- An explicit statement of what remains reachable by sha through
  `refs/pull/*/head`, and whether GitHub Support was asked. "Force-pushed" is
  not "gone".
- The pre-push guard installed and demonstrated rejecting a synthetic bad push
  **and** allowing a good one. A guard shown only to reject proves half of what
  matters.
- Which machine ran which step. The ZENDA half cannot be done or verified from
  a cloud session.
