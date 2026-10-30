## Git habits

- Concise commit messages that explain *why*; one logical change per commit.
  Never amend published commits or force-push shared branches.
- **A closing keyword before `#N` or an issue/PR URL, any repo, closes it,
  PRs too**, from a PR body or any commit message; backticks don't help,
  and linked issues won't show it (cms-platform#283 via `78617e1`,
  _agent-guidance#136 by cms-platform#434). To close: `Closes #N`, not
  `For #N`; else omit it. After merging, check what should stay open is.
- **Before a PR, `git log origin/<base>..HEAD --oneline` lists only this
  task's commits.** A reusable worktree (`.claude/worktrees/<name>/`) carries
  stale WIP, once a commit that *deleted* files the task edits. Anything
  extra: `git checkout -b <new> origin/<base>` and redo the work.
- **One worktree per independent coding session**, pre-authorized: reuse it
  on resume, not per turn; edit, build, test there, leaving other checkouts
  untouched. If creation fails, resolve or explain before using the shared
  checkout. No git: a separate copy. Read-only tasks and requested
  global-settings changes use targets directly.
