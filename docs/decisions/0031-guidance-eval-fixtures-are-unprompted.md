# 0031 — Guidance eval fixtures are unprompted

**Status:** Accepted (2026-10-31), recording what [skills-evals#98](https://github.com/Adam-S-Daniel/skills-evals/issues/98) built.

## Context

[0002](0002-unconditional-rules-live-in-the-guidance-not-a-skill.md) puts a rule in the always-loaded guidance because its failure mode is non-recognition: the agent does not notice the situation the rule covers, so it never reaches for the rule. An eval that tells the agent what the rule says measures reading comprehension, not the failure the section exists to prevent. A fixture whose prompt names the rule passes with or without the section.

A skill eval can cue its skill, because a skill fires on recognition and the skill's description is the cue under test. A guidance eval has no such cue.

## Decision

1. **A guidance fixture's prompt reconstructs the operator's request and never names the rule.** The scaffold (`scripts/scaffold_real_work.py guidance`) writes `prompt` and `unprompted_rationale` as `TODO` fields, and the second says why the prompt does not name the rule. Whether the prompt is clean is a review judgment, not a lint.
2. **The objective checks look for the behavior the section should cause** (for example a parameterized query), and the judge rubric scores it. Neither checks for the section's wording.
3. **Overlap with skill evals is by seed only.** A guidance fixture may reuse a seed workspace a skill fixture also uses. It does not reuse that fixture's prompt or checks, because a skill prompt cues the very recognition this fixture withholds. This is a convention kept by review, not a check.
4. **The four-word lint belongs to real-work fixtures.** Skills-evals' [`harness/answer_leak.py`](https://github.com/Adam-S-Daniel/skills-evals/blob/main/harness/answer_leak.py) refuses a real-work task text that shares four or more consecutive words with a line the merged fix added. Guidance fixtures have no merged fix to compare with, and no check compares their prompt with the section's text.

## Consequences

- The measured effect is the effect on an agent that never asked for the rule, which is the one the fleet gets.
- Deltas are smaller than those of a prompted eval and need more trials; a section whose rule the model already follows unprompted shows no benefit and retires under [0030](0030-a-sections-worth-is-measured-by-ablation-and-weighed-against-its-bytes.md). That is a finding.
- A prompt that accidentally echoes the section is caught only by a reviewer. A lint comparing prompt and section would catch it; none exists.

## Alternatives considered

- **Prompt the agent with the rule, as skill fixtures do.** Rejected: it tests comprehension, and the control arm would pass too.
- **Lint every prompt against its section by four-word runs.** Considered and not built: a run of four words from a section appears in ordinary task text, and the lint's false positives would push authors to contort prompts.

## References

- [skills-evals DESIGN.md "Guidance subject"](https://github.com/Adam-S-Daniel/skills-evals/blob/main/DESIGN.md).
- An example, [`evals/guidance/security/search-query`](https://github.com/Adam-S-Daniel/skills-evals/blob/main/evals/guidance/security/search-query/fixture.yaml): the prompt asks for a search function and never mentions injection.
