# Eval, keep, remove and pause rules

Read this before you change, cite or build on how skills, guidance sections
and git hooks are evaluated, kept, removed or paused.

- **Rules, in plain English:** skills-evals'
  [`docs/rules.md`](https://github.com/Adam-S-Daniel/skills-evals/blob/main/docs/rules.md).
- **Every value, machine-readable:**
  [`docs/rules.json`](https://github.com/Adam-S-Daniel/skills-evals/blob/main/docs/rules.json),
  keyed `<file>#<block>.<key>`.
- **The source:** the policy YAMLs in skills-evals'
  [`evals/`](https://github.com/Adam-S-Daniel/skills-evals/tree/main/evals).
  Both pages above are generated from them; change a rule there and nowhere
  else.

Never restate a threshold in a doc, comment, skill, guidance line or memory
note: link `docs/rules.md`. skills-evals' `scripts/check_rule_restatements.py`
fails CI on a restated value that disagrees, and its daily
`rules-consistency.yml` scans this repo, adam-agentskills, acsi-kit and
adamdaniel.ai and opens an `agent-ready` issue for each disagreement.
