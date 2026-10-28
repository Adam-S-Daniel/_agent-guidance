# Starting guidance

This file is delivered into every session's `~/.claude/CLAUDE.md` by the
session-start hook, so every line costs context in every session. Keep only
what is specific to you and learned the hard way. To grow it, add one `##`
section per lesson (the rule, then one line of why) and a row for that heading
in `eval-coverage.yml` in the same change. Keep the file small; depth belongs
in a repo's `docs/` or in a skill.

## Working in these repos

- Fix what was asked: no speculative features, premature abstractions or
  unused helpers. Prefer editing an existing file over creating one.
- A public interface change updates its tests; new behavior gets a test, a
  bug fix a regression test. Run the existing suite before calling a task
  complete and say what you ran.
- Tests are deterministic: no sleeps, network or wall-clock time.
- Use American English spelling in text you add or change. (Change this line
  to your own house spelling; what matters is one choice, written down.)

## Anything you name gets its link

Any noun the reader might open gets its URL in the same sentence: what you
hand over, what you are waiting on, what you cite as done.

- A link is not a description: one clause of identification travels with it.
  A bare `repo#123` autolinks only inside that repo; elsewhere, full URL.
- No URL? Say so: "No link, local task `abc123`, output at `/tmp/...`".

## A successful `git push` does not mean your commit exists

A pre-commit hook that refuses the commit does not stop the push: `git push`
pushes the base commit and prints an ordinary success.

- Verify the commit, not the push: `git log --oneline -1` shows your message
  and `git status --short` is clean.
- Chain commit into push with `&&`, never a newline or `;`.
- After every push, `git merge-base --is-ancestor <sha> origin/<branch>` is
  the one check that names both the commit and the remote branch.

## "The watch finished" is not "CI passed"

Never read pass/fail off a watch command's exit code: in `cmd | tail` `$?` is
`tail`'s, and `tail -N` hides FAILURE lines.

- Capture the real code with `${PIPESTATUS[0]}`, or do not pipe the watch.
- Afterward, query the parsed conclusions of every check and report those.

## Branch protection and merges

- The default branch is PR-only; a direct push is rejected, even from the
  repo's own workflows. Never design a bot that pushes to it.
- Merge with a merge commit. Do not amend published commits or force-push a
  shared branch.

## Security and data exposure in CI

CI logs, artifacts and git history are public on a public repo.

- Validate anything crossing a trust boundary. Never build SQL, shell
  commands or HTML by concatenating untrusted data.
- Never commit secrets or `.env` files, and never print personal data to a
  log: log a status code and error type, not a response body.
- Do not interpolate `${{ inputs.* }}` or `${{ github.event.* }}` into a
  `run:` block; the rendered command is echoed.
- Use minimal `permissions:` (usually `contents: read`). Fixtures use
  `example.com` only.

## <Your lessons>

<!--
Shape of a new rule. Replace this comment with real sections, one per lesson,
and give each its own row in eval-coverage.yml:

  ## <The short name of the trap>

  <The rule, in one or two sentences an agent can act on.>

  - <Why, in one line: the incident or cost that made it a rule.>
-->
