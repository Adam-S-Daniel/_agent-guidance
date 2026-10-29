# agent-skills-private

The private half of the skills registry. This repo is private on every plan
and must stay private: never make it public, never copy its contents into a
public repo, and never paste them into a public issue, pull request or log.

## What goes here

- **Lessons**, on the branch `persistent/lessons` (never on `main`). The
  session miner (`skills-evals/scripts/mine_sessions.py`) runs on your own
  machine only, because it reads your transcripts; it refuses to run in a
  cloud session. From its output you write one `lessons/<subject>.json` per
  lesson and push them to `persistent/lessons` yourself. The draft-skill lane
  reads that branch with the read-only lessons-reader App and turns the most
  frequent lesson into a draft skill or hook proposal. The file shape is in
  [`lessons/README.md`](lessons/README.md).
- **Private skills**: skills that hold anything you would not publish (your
  own hosts, accounts, people, private procedures). Lay them out like the
  public registry, `plugins/<plugin>/skills/<skill>/SKILL.md`.

## What never goes here

Secrets of any kind (private keys, tokens, passwords, denylists): those live in
GitHub environment secrets. A lesson is redacted text about what went wrong,
not a transcript.

## Protection

`bootstrap/apply-rulesets.sh` gives `persistent/lessons` a ruleset with
deletion and non-fast-forward blocked and no bypass: you push it from your
machine, never force-push it, and never delete it. On GitHub Free, rulesets
are not enforced on private repos, so that rests on you.

`tests/run.sh` checks that the documentation is present and that the example
in `lessons/README.md` has the shape the picker reads.
