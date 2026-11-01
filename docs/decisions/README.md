# Architecture Decision Records

Lightweight [Nygard-style](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
ADRs for decisions in this repo that are **non-obvious, load-bearing, and
would rot if the reasoning lived only in a PR description**.

Most of what this repo does is explained well enough by the code and the
README. An ADR earns its place when the next reader's most likely instinct is
to *undo* the decision — because the code shows the "what" but not the
counterfactual that was rejected, and re-litigating it costs more than reading
a page.

## Index

| ADR | Title |
|-----|-------|
| [0001](0001-skills-bootstrap-delivery-is-opt-in.md) | skills-bootstrap delivery is opt-in and double-keyed, not fleet-wide |
| [0002](0002-unconditional-rules-live-in-the-guidance-not-a-skill.md) | Unconditional rules live in the managed guidance, not in a skill |
| [0003](0003-cron-coverage-is-fleet-listed.md) | Cron coverage is measured against a declared fleet, not against a disk |
| [0004](0004-skills-bootstrap-adopted-where-sessions-happen.md) | skills-bootstrap is adopted wherever sessions happen, and this repo self-hosts |
| [0005](0005-consumer-locks-are-re-pinned-from-here.md) | Consumer locks are re-pinned from here, and only when the bundle moved |
| [0006](0006-bump-prs-land-on-a-sweep.md) | The bumper's own pull requests land on a sweep, not on a reviewer |
| [0007](0007-sha-pins-carry-no-version-comment.md) | A SHA pin carries no version comment |
| [0008](0008-downloaded-binaries-are-pinned-and-digest-checked.md) | A downloaded binary is pinned by version and by digest, and the digest lives here |
| [0009](0009-a-federated-pin-advances-on-a-scoped-question.md) | A federated pin advances, and only on a question scoped to that source |
| [0010](0010-the-hook-pin-is-re-pinned-from-here.md) | The hook pin is re-pinned nightly too, and only ever proposed |
| [0011](0011-skills-bootstrap-scope-is-exhaustive.md) | Skills-bootstrap scope is exhaustive, and an unclassified repo is a nudge |
| [0012](0012-codex-gets-the-guidance-as-user-instructions.md) | Codex gets the guidance as global user instructions, and the project budget is warned, never blocked |
| [0013](0013-a-memory-note-outside-a-repo-names-its-home.md) | A memory note outside a repo names its home, and a session that wrote one without it does not stop |
| [0014](0014-dependabot-config-health-is-swept-centrally.md) | Dependabot config health is swept centrally, from here |
| [0015](0015-bump-prs-never-ask-for-native-auto-merge.md) | Bump pull requests never ask for native auto-merge (shares number 0015 with the next row; distinct decisions) |
| [0015](0015-audit-codex-memories-after-generation-not-at-stop.md) | Audit Codex memories after generation, not at Stop — Accepted 2026-10-02 (shares number 0015 with the previous row; distinct decisions) |
| [0016](0016-the-freshest-delivery-wins-the-shared-global-block.md) | The freshest delivery wins the shared global block |
| [0017](0017-prompt-audit-runs-as-an-event-triggered-sweep.md) | Prompt-audit runs as an event-triggered sweep, never as a check or a schedule |
| [0018](0018-incident-narratives-live-in-docs-evidence.md) | Incident narratives live in `docs/evidence/`, and the guidance keeps a pointer |
| [0019](0019-sync-pins-hook-scripts-to-lf-via-gitattributes.md) | The sync pins the hook scripts to LF through each repo's `.gitattributes` |
| [0020](0020-the-api-credit-lane-is-machine-local-not-fleet-guidance.md) | The API-credit lane is machine-local, delivered by plugin hooks, not by the fleet guidance |
| [0021](0021-the-sync-delivers-agent-checks-to-every-fleet-repo.md) | The sync delivers the agent-checks runner, CI check and --no-verify guard to every fleet repo |
| [0022](0022-the-changelog-routine-does-not-read-claudeissues-com.md) | The changelog routine does not read claudeissues.com |
| [0023](0023-pro-max-only-claude-code-tested-codex-best-effort.md) | Pro/Max only, Claude Code tested, Codex best effort, and the Projects-era strategy (shares number 0023 with the next row; distinct decisions) |
| [0023](0023-mark-agent-contributions.md) | Mark every agent-made GitHub contribution, on Adam's account for now (shares number 0023 with the previous row; distinct decisions) |
| [0024](0024-actions-pin-through-lockfiles.md) | Actions are pinned through workflow lockfiles, and the lockfile is verified in a required check |
| [0025](0025-a-cloud-fleet-worker-merges-narrow-instruction-fixes.md) | A cloud fleet worker merges narrow instruction fixes and tells the owner about the rest |
| [0026](0026-memory-is-curated-by-a-routine-and-promoted-through-the-fleet-worker.md) | Memory is curated by a routine, and its durable facts are promoted through the fleet worker |
| [0027](0027-lessons-become-agent-checks-through-the-fleet-worker.md) | Lessons become agent-checks through the fleet worker, and their PRs auto-merge when green |
| [0028](0028-the-fleet-worker-merges-skill-guidance-and-partial-fixes.md) | The fleet worker also merges skill, fleet guidance and partial fixes |
| [0029](0029-skills-never-run-shell-commands-on-load.md) | Skills never run shell commands on load: a runner check refuses them and the sync sets `disableSkillShellExecution` |
| [0030](0030-a-sections-worth-is-measured-by-ablation-and-weighed-against-its-bytes.md) | A section's worth is measured by ablation and weighed against its bytes, and retirement is automatic (see also 0002) |
| [0031](0031-guidance-eval-fixtures-are-unprompted.md) | Guidance eval fixtures are unprompted (see also 0002) |
| [0032](0032-guidance-evals-deliver-through-user-memory-and-prove-it-per-arm.md) | Guidance evals deliver through user memory and prove it per arm |
| [0033](0033-audits-leave-eval-protected-subjects-alone-except-for-correctness.md) | Audits leave eval-protected subjects alone except for correctness fixes |
| [0034](0034-the-eval-protection-gate-runs-in-required-ci.md) | The eval-protection gate runs in required CI |
| [0035](0035-sessions-prove-the-guidance-loaded-with-a-check-value.md) | Sessions prove the guidance loaded with a check value |

## Format

`NNNN-kebab-title.md`, numbered sequentially, with:

- **Status** — Accepted / Superseded by NNNN
- **Context** — what was true when the decision was made, with the numbers
- **Decision** — what was chosen, stated so it can be checked against the code
- **Consequences** — including what this makes *worse*, and what it leaves open

An ADR is immutable once merged. Changing course means a new ADR that
supersedes it, not an edit — the record of what we believed, and why, is the
point.
