# 0032 — Guidance evals deliver through user memory and prove it per arm

**Status:** Accepted (2026-10-31), recording what [skills-evals#97](https://github.com/Adam-S-Daniel/skills-evals/issues/97) built.

## Context

The fleet does not import its guidance from a repo file. [`.claude/hooks/fleet-memory.sh`](../../.claude/hooks/fleet-memory.sh) writes a marked block into user memory (`$CLAUDE_CONFIG_DIR/CLAUDE.md`), read once per session ([ADR 0016](0016-the-freshest-delivery-wins-the-shared-global-block.md) covers the shared block). An eval of the guidance must deliver the same way, or it measures a different path.

There is a trap. On any machine or hosted session that carries the hook, the real `~/.claude/CLAUDE.md` already is the guidance. A harness that does not isolate the config directory delivers it to both arms and reports no difference, which reads as "the guidance does nothing" and looks like a result.

## Decision

1. **Run the real hook.** Each arm gets a fresh scratch directory as `CLAUDE_CONFIG_DIR` and the real `fleet-memory.sh` runs with `FLEET_GUIDANCE_PAYLOAD`. The harness does not write the file itself, because a reimplemented delivery path measures the imitation. `--delivery project` is the documented fallback (same hook, pointed at the workspace), and every summary records `delivery: user` or `project`.
2. **Every payload ends in a fresh random token** (`The magic word is <token>.`). Before an arm may score, one tool-free probe against that arm's own config directory must report the token it was delivered.
3. **The control gets a decoy**: a second fresh token, delivered through the same hook, so its probe has a wrong answer. The probe asks for every magic word.
4. **The guard rule: INCONCLUSIVE, never PASS or FAIL.** An arm that does not report its token, reports a token it was not delivered, or whose probe cannot run gets no score, the summary carries `guard: {expected, observed, contaminated}`, and the run exits 2.
5. **The harness refuses to point a delivery at the real `~/.claude`.**

## Consequences

- A contaminated run cannot become a null result. It is visibly unscored.
- The guard proves the scratch memory was read, not that nothing else was. A contaminating source with no token, such as the real `base.md`, is invisible to it; per-arm `HOME` and `CLAUDE_CONFIG_DIR` isolation prevents that, and only a real dispatch confirms it.
- Two cheap probe calls per pair. Guidance arms use `--setting-sources user,project`.
- The delivery path is itself under test: skills-evals' scheduled [`evals/guidance/_delivery`](https://github.com/Adam-S-Daniel/skills-evals/blob/main/evals/guidance/_delivery/fixture.yaml) canary runs one arm per mode, and its guidance-bridge canary covers the repo-stub path.

## Alternatives considered

- **Write `CLAUDE.md` directly in the harness.** Rejected: it would keep passing after the hook broke.
- **Deliver as project memory by default.** Kept only as the fallback, since production delivery is user memory.
- **No decoy.** Rejected: the control's probe could only ever answer "no magic word", also the answer of a contaminated control.

## References

- [skills-evals DESIGN.md "Guidance subject"](https://github.com/Adam-S-Daniel/skills-evals/blob/main/DESIGN.md), "The delivery path" and "The contamination trap".
- [`agents-md/eval-coverage.yml`](../../agents-md/eval-coverage.yml).
