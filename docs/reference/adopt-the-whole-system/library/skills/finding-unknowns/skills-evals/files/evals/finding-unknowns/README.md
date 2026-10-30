# Finding unknowns fixture

This Class C fixture covers the pre-implementation pass in
[issue #78](https://github.com/Adam-S-Daniel/skills-evals/issues/78). The seed
contains a working CSV helper, a failing empty-export acceptance test, an
accepted bounded-export decision, and an old route whose compatibility is an
open product question. The request does not identify those sources or supply
the two operator answers.

Three objective checks only certify that the reply cites the relevant file
paths. The fourth checks that Python source files were left unchanged
(bytecode caches from running the seed test are not source). Those checks
cannot establish that the agent read or understood the files, or that it asked
useful questions. The blind pairwise judge carries those judgments, using two
hand-written references outside `seed/` and anchored score levels in the
fixture rubric. A path-only or semantically wrong reply can pass the lexical
checks and should score poorly with the judge. Source edits fail the separate
objective check; the pairwise judge does not receive workspace diffs or check
results and cannot rank on unseen edits.

The references are not copied into the agent workspace. Pairwise rank is
noisy; any later balanced comparison needs at least six trials per arm, a
multiple of six for the three-draft permutation cycle. The schema and scorer
support pairwise ranking, but [`harness/run_eval.py`](../../harness/run_eval.py)
currently rejects a judged pairwise run before either arm with
`judge_mode_unsupported` (exit 2). Its comment attributes the missing runner
wiring to [issue #97](https://github.com/Adam-S-Daniel/skills-evals/issues/97),
which is closed; the implementation gap remains. This package does not claim
a measured A/B improvement or cover the skill's during-build notes and
post-build explanation steps.

The explicit context in [fixture.yaml](fixture.yaml) follows
[ADR 0012](../../docs/decisions/0012-ab-runs-in-the-deployed-context-minus-the-subject.md).
It names [skills-evals](https://github.com/example-adopter/skills-evals) as a
representative Python repository for this general pre-implementation skill,
at [revision 9db7bb3956453f32705d3f9db672900714b3c216](https://github.com/Adam-S-Daniel/skills-evals/commit/9db7bb3956453f32705d3f9db672900714b3c216).
The resolver proved that
[guidance revision b0abbe7dc97eca0a2624bfa9695b0d98b774cef2](https://github.com/Adam-S-Daniel/_agent-guidance/commit/b0abbe7dc97eca0a2624bfa9695b0d98b774cef2)
reproduces the managed guidance bytes shipped at that repository revision;
the pin is an exact byte match, not an inferred latest revision. The measured
guidance is 25099 bytes. The committed limit is 31374 bytes, rounded up with
25% headroom. There is no `skills.lock` at the pinned repository revision,
so there are no adopted skill bundles: catalog and payload measurements are
both zero, with limits of 1 byte each (the schema minimum). Under ADR 0012,
future delivery would add the subject only to the `with` arm while both arms
retain this same deployed context.

These pins and budgets describe resolver-only metadata. They do not implement
in-place context delivery, per-arm delivery guards, or subject addition.
Those remain prerequisites for a deployed-context comparison; the existing
`judge_mode_unsupported` refusal is another runner blocker. Paid runs also
remain outside this package's authorization:
[HANDOFF.md decision 8](../../HANDOFF.md#stopped-by-decision-8-2026-09-08-0120-utc-stop-adding-and-completing-evals-for-specific-skills)
explicitly says no dispatch on [issue #78](https://github.com/Adam-S-Daniel/skills-evals/issues/78).
The later roster hold was lifted, but
that did not reopen this fixture lane. A new owner authorization is required
before a paid run. No paid comparison was run here, and there is no impact
evidence for this fixture.
