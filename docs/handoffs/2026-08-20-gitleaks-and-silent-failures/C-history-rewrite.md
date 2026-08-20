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

## Status as of 2026-08-20T00:35Z — VERIFIED, gate NOT met

Both consumers' `skills.lock` on `main` still contain
`cms-platform/cms-platform-secrets` and do NOT contain
`cms-platform/consumer-repo-provisioning`. `sources[0].ref` is `3264e159`, the
pre-rename cms-platform commit. The rename IS merged in cms-platform and shipped
in `v0.1.86`, and both consumers have merged their bump PRs — and none of that
moved the lock, exactly as predicted. **Do the federated advance first.**

## PREREQUISITE GATE — verify all three, in writing, before touching history

1. The rename has landed in `cms-platform`, a release is cut, and **both**
   consumers' committed `skills.lock` no longer contains any gitleaks keyword.

   **This does NOT happen on its own — budget a manual step.** Each site's lock
   pins cms-platform as a federated source at an explicit `sources[0].ref`, and
   nothing advances it: `--repin` advances only the PRIMARY ref (the script says
   *"that federated pin is a human's to advance"*), and `platform-bump.yml`
   contains no reference to `skills.lock` at all. Advance the federated ref to a
   cms-platform commit containing the rename, regenerate, and land it — THEN
   verify per repo:
   ```bash
   python3 -c "
   import json,re,sys
   KW=re.compile(r'access|auth|api|credential|creds|key|passwd|password|secret|token',re.I)
   d=json.load(open('skills.lock'))
   bad=[k for k in d['skills'] if KW.search(k)]
   print('KEYWORD-BEARING LOCK KEYS:',bad); sys.exit(1 if bad else 0)"
   ```
2. No OTHER repo pins a commit of the repo you are about to rewrite. A rewrite
   changes every subsequent sha; a lockfile naming one of them then pins
   something a fresh clone does not contain. Search `platform.lock`,
   `skills.lock`, `platform_ref:` inputs and any `uses:@<sha>` across the fleet.
   **If anything pins these repos, STOP and resolve that first.**
3. You have an inventory of every ref containing an offending commit — local
   branches, remote branches, tags, and **open pull requests**. A PR ref
   (`refs/pull/N/head`) keeps a commit reachable permanently, so an un-closed
   PR silently defeats the entire rewrite.

## The rewrite

Affected repos and fingerprints:
- `jodidaniel/jodidaniel.com` — `fee19ee47fae4b1a9360feac56863f59b32bce4d:skills.lock:generic-api-key:31`
- `Adam-S-Daniel/adamdaniel.ai` — `39d925036922e2c09344f062886e45a035fc996d:skills.lock:generic-api-key:31`

Use **`git filter-repo`**, never `filter-branch` (deprecated, slow, and its
default behaviour leaves originals reachable). Rewrite only the `skills.lock`
blob content — do not drop the commits, which would lose unrelated changes.

Then, in this order:
1. Force-push every rewritten branch.
2. Delete every stale ref found in gate step 3. Close (do not merge) any PR that
   carries an offending commit; reopen equivalent work from rewritten history.
3. Empty each `.gitleaksignore` down to a placeholder comment, or delete it.
4. **Verify with a `workflow_dispatch` run of `secrets-scan.yml`** on the branch.
   That is the only lane that reads full history; a pull request's own `scan`
   job structurally cannot test this, which is precisely how the original
   regression shipped green.
5. Note that GitHub garbage-collects unreachable objects on its own schedule
   (days to weeks). Until then the old commits remain reachable **by sha**. For
   a public repo you can ask GitHub Support to expedite. Say so plainly in the
   report rather than implying the data is gone the moment you force-push.

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
   all history, not just the tip:
   ```bash
   git log --all -S 'cms-platform-secrets' --oneline
   ```
   Empty output is the pass condition. Run it in the fresh clone.
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

## Definition of done

- Both `.gitleaksignore` files empty or gone.
- A `workflow_dispatch` `secrets-scan.yml` run **green on each rewritten repo**,
  reported with its run id and parsed conclusion — never "the watch finished".
- `git log --all -S 'cms-platform-secrets' --oneline` empty in a **fresh** clone
  of each repo.
- The pre-push guard installed and demonstrated rejecting a synthetic bad push
  **and** allowing a good one. A guard shown only to reject proves half of what
  matters.
