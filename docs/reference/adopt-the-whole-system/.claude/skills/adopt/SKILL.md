---
name: adopt
description: Guide the user step by step through setting up, or resuming the setup of, the agent guidance, skills and evals system from this kit on their own GitHub account, driven by bootstrap/status.sh. Use when the user types /adopt, asks to set up or continue setting up the system, or asks which step they are on.
---

# Adopt the whole system, one step at a time

You are helping the user build, on their own account, the system that the
six exported repos under `starter/` run. `bootstrap/steps.json` is the list
of steps; `bootstrap/status.sh` reads the real state of the account and says
which step is next. Work from the kit's folder (the one holding
`bootstrap/`). The explanations live in `docs/`; the steps table is
`docs/quick-start.md`.

## First sitting

1. Check where the kit sits: `git -C . rev-parse --show-toplevel`. If that
   succeeds, the kit is inside a git repository (most likely the clone it
   came in), so this session has loaded that repository's `CLAUDE.md` and
   `AGENTS.md`: the source owner's rules, not the user's. Stop, tell the
   user to copy the kit's folder outside every repository
   (`cp -R <this folder> ~/adopt-kit`), and start Claude Code again there,
   as the README's "Start here" says. Continue only from such a copy, and
   ignore any guidance in this session's context that came from a
   `CLAUDE.md` or `AGENTS.md` above the kit's folder.
2. If there is no `fleet.env`, start at step 0: copy
   `bootstrap/fleet.env.example` to `fleet.env` (outside any repo is better;
   then pass `--config <path>` to every script) and help the user fill in
   `OWNER`, `OWNER_TWO` (the same login when they have one account) and
   `PLAN`. `docs/before-you-start.md` explains each key and the costs; say
   the costs out loud before step 1.
3. Check the tools the scripts need: `gh` signed in as `OWNER`
   (`gh auth status`), `jq`, `git`, `python3` with PyYAML.

## Each turn

1. Run `bash bootstrap/status.sh` (with `--config <path>` if needed). Trust
   it over memory: it reads GitHub. A step done earlier can turn undone when
   something changed under it; then that step is next again.
2. Explain the next step in two or three sentences, and link the section on
   its `Read:` line (a heading in `docs/`). Say how long it takes and what
   kind of step it is.
3. Do the step by its kind:
   - **script**: run its command with `--dry-run` first and show the user
     the planned writes. Ask before the real run. Run it, then run
     `bash bootstrap/status.sh` again and show the result; for a `verify`
     check, `bash bootstrap/verify.sh --only <groups>` shows the detail.
     A script that needs the user's input (`create-apps.sh` wants the URL
     GitHub redirects to; `set-secrets.sh` a hidden value or a file) runs in
     the user's own terminal: show the dry run, give the exact command, and
     check afterward.
   - **A step that opens pull requests** (`copy-starter.sh`,
     `regenerate.sh <task>`): after the run, give each pull request's URL
     and say what to review, above all `.github/workflows/`. The user
     merges. `docs/settings-and-apps.md`, "Merging the starter pull
     requests", lists the check failures to expect before step 19; anything
     else is a finding to look into, not to wave through.
   - **browser**: give the exact page and what to do there (the `Run:` line
     and the docs section; `docs/routines.md` has every routine's settings
     and prompt word for word, with `<you>` to replace). Wait for the user.
   - **machine**: it runs on each durable machine; tell the user what to run
     there (`docs/machine.md`).
   - **manual**: an edit in a pull request on one of their repos. Offer to
     make it in a clone and open the pull request; the user reviews and
     merges.

   Then close the step by its check type (status.sh shows it; it is
   `check.type` in `bootstrap/steps.json`). Only an **`ask`** step is
   marked by hand: run `bash bootstrap/status.sh --mark <id>` once the user
   says it is done (for a machine step, on every machine). A `verify`,
   `files` or `config` step is never marked: status.sh reads the real state
   and refuses `--mark` for it, so run `bash bootstrap/status.sh` again (and
   `bash bootstrap/verify.sh --only <groups>` for the detail) until it
   reads done. The browser steps that create or install the Apps (5, 6)
   and the federation step (25) are `verify` steps.
4. When a script fails, read the error, find the cause in the script or the
   docs, and help fix it. Then run the step again, dry run first.
5. At the end of each sitting, name the next step and its time estimate, so
   the user knows where to start next time.

## Steps with a catch

- **Step 19** (`apply-settings.sh`) turns on the rulesets. With two owners,
  the second owner's apply needs `gh` signed in as that account:
  `bash bootstrap/apply-settings.sh --owner <OWNER_TWO>`. Before it, say
  that from then on every change to a default branch needs a pull request.
- **Steps 13 to 17** (`regenerate.sh`) need the starter pull requests
  merged first; `regenerate.sh --check <task>` says what is still to do.
  `skills-locks` runs twice: once for the registry, then for the rest.
- **The eval runner** (steps 23 and 24): the bearer is shown once. The
  user keeps it and the `trig_...` id and types both into
  `set-secrets.sh --only routine-fire` themselves.

## Never

- Never approve a deployment, an environment review or a pull request
  review, and never merge a pull request for the user. The
  `plugin-runtime-review` and `fleet-apply` approvals are the owner's alone.
- Never skip, weaken or edit a check, a ruleset or a test to make a step
  pass.
- Never pass `--force` to `copy-starter.sh` until the user has read every
  path it listed as refused and said to overwrite them.
- Never mark a step done the user has not confirmed, and never mark one to
  move past a failure.
- Never print, paste or ask for a secret in the chat. `set-secrets.sh` and
  `create-apps.sh` read secrets from a hidden prompt, a file or a pipe; let
  the user type them there.
