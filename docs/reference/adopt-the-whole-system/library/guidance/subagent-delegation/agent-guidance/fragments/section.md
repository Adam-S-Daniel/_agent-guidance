## Subagent delegation (model routing)

- Don't write code in the orchestrating session: delegate to a subagent or
  child session on a cheaper model — the cheapest capable tier for
  exactly-specified edits, a mid tier for a clear spec; escalate rather than
  ship a wrong diff on anything subtle. The orchestrator keeps root cause,
  architecture, the spec (files, exact changes, house style, test command) and
  diff review. A child that skips this
  file (Explore/Plan or `omitClaudeMd` agents, SDK harnesses with
  `settingSources: []`) needs constraints in its prompt.
- Delegated work is done when a verifier exits 0: name the exact
  command, run LAST (a trailing `echo $?` hides the tool's code), and
  require its code back. "Cannot run it" is BLOCKED; a count off-spec,
  stop-and-report; a self-contradicting report ("2 failed",
  "exit 0"), re-run it. **Prove the verifier can fail first:**
  `python3 test_foo.py` with no `__main__` block exits 0 having asserted
  nothing. Name the RUNNER (`python3 -m pytest <paths> -q`),
  never the file, and require the test COUNT beside the exit code.
- **A working subagent OWNS the tree — do not commit or push under it.** A
  push mid-flight plus its `git commit --amend` diverges a published branch
  (recover by reset and a fresh commit, never force-push), and its
  `git checkout -- <file>` discards your edits. Wait for a clean `git status`
  plus a recorded result; decline a stop hook's "commit and push" nudge.
- **A subagent that has REPORTED can still be holding the tree**: its
  BACKGROUND CHILDREN are not reaped (a 999/0 tree read 985/14). Look for descendants before trusting a long run
  (`ps -eo pid,etimes,cmd | grep '[y]our-command'`); `md5sum` inputs before
  and after (differ → not evidence); prefer a per-run output path; kill
  orphans, and say so.
- **A subagent that goes quiet is not working — check activity, not the
  clock.** Its transcript's mtime is the signal; fix the staleness threshold
  and the fallback in advance.
- **A live-test prompt states the credential boundary** — which
  `HOME`/profile, what it may read, and that it must not copy real
  credentials to pass. Supply a throwaway one or run
  unauthenticated; else it's the operator's call.
- **A scratch tree can still reach production.** `cp -a` copies
  `.git/config`, so a copy inherits `origin`; but `git remote remove origin`
  in a `git worktree` strips the PARENT's remote. Run `/acsi-coding-anywhere:disarm-inherited-reach`
  before disarming.
