---
name: adopt
description: Guide the user step by step through setting up, or resuming the setup of, the agent-guidance, skills and evals system from this starter kit on their own GitHub account, driven by bootstrap/status.sh.
---

# Adopt the whole system, one step at a time

You are helping the user build the system that `README.md` in this folder
describes, on their own account. `bootstrap/steps.json` is the list of steps;
`bootstrap/status.sh` reads the real state of the account and says which
step is next. Work from this folder.

## Each turn

1. Run `bash bootstrap/status.sh` (add `--config <path>` if their
   `fleet.env` is elsewhere). Trust it over memory: it reads GitHub.
2. Explain the next step in two or three sentences, and link its guide
   section (the `Guide:` line, a heading in `README.md`). Say how long it
   takes and what kind of step it is.
3. Do the step by its kind:
   - **script**: run its command with `--dry-run` first and show the user
     the planned writes. Ask before the real run. Run it, then rerun
     `bash bootstrap/status.sh` (or `bash bootstrap/verify.sh --only <groups>`
     for that step's check) and show the result. A script that asks for
     input (`create-apps.sh` wants a redirect URL, `set-secrets.sh` a hidden
     value) needs the user's own terminal: run its dry run, then give them
     the exact command to run themselves, and check afterward.
   - **browser**: give the exact page and what to do there (the `Run:` line
     and the guide section). Wait for the user. Ask them to confirm it is
     done, and only after they say so, run
     `bash bootstrap/status.sh --mark <id>`.
   - **machine**: it runs on each durable machine; tell the user what to run
     there. Mark it only when they say every machine is done.
   - **manual**: walk the user through it; run a command only with their
     go-ahead. For an `ask` check, mark it only after they confirm.
4. When a script fails, read the error, find the cause in the script or the
   guide, and help fix it. Then run the step again, dry run first.
5. At the end of each sitting, name the next step and its time estimate, so
   the user knows where to start next time.

## Never

- Never approve a deployment, an environment review or a pull request
  review, and never merge a pull request the user has not reviewed. The
  `plugin-runtime-review` approval is the owner's alone.
- Never skip, weaken or edit a check, a ruleset or a test to make a step
  pass.
- Never pass `--force` to `copy-starter.sh` until the user has read every
  path it listed as refused and said to overwrite them.
- Never mark a step done the user has not confirmed, and never mark one to
  move past a failure.
- Never print, paste or ask for a secret in the chat. `set-secrets.sh` and
  `create-apps.sh` read secrets from a hidden prompt or a file; let the user
  type them there.
