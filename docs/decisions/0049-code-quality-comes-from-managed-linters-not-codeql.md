# 0049 — Code quality comes from centrally managed linters, not CodeQL's quality suite

**Status:** Accepted (2026-11-20)

## Context

The fleet runs CodeQL default setup on every managed public repo, with the
`extended` (`security-extended`) suite as the default (repo-settings
[`docs/code-scanning.md`](https://github.com/Adam-S-Daniel/repo-settings/blob/main/docs/code-scanning.md)).
CodeQL's third suite, `security-and-quality`
([query suites](https://docs.github.com/en/code-security/concepts/code-scanning/codeql/codeql-query-suites#security-and-quality-query-suite)),
adds maintainability and reliability queries, but default setup cannot run it:
the default-setup API offers only `default` and `extended`. Running it means a
CodeQL workflow in each repo (advanced setup), which:

- adds about 19 workflows to pin, bump and keep alike, with languages listed
  by hand instead of detected;
- works against repo-settings, which manages default setup and gates turning
  it on over a repo's own CodeQL workflow;
- puts quality findings in the Security tab beside security alerts, for the
  security triage to sort;
- can block merges where the `code_scanning` ruleset rule is on, since some
  quality queries are error-level;
- slows every scan.

## Decision

The owner's call (2026-11-20):

1. **No `security-and-quality` suite.** CodeQL stays on default setup and
   covers security only.
2. **Maintainability and reliability come from linters**, configured to fit
   each repo's languages and ensured centrally and automatically (the same
   way the sync and repo-settings ensure other fleet standards), running in
   each repo's CI, its git hooks, or both.
3. **The lint strategy leans decisively toward sanity**, not coverage:
   - Start from a small rule set with a low false-positive rate; a rule
     earns its place by catching real bugs, not style.
   - **Existing code is addressed gradually.** Findings already in a repo are
     baselined, or the rule is scoped to changed lines, and burned down over
     time; adopting a linter never turns a repo red.
   - **Block only on new findings of high-confidence rules.** Everything else
     reports without failing the check.
   - A rule that keeps producing noise is turned off, not suppressed line by
     line.

## Consequences

- Quality findings stay out of code scanning and its triage; the Security tab
  carries security alerts only.
- Something central has to choose, deliver and keep the per-language linter
  configs current. That work is open; it builds on the per-repo language
  detection that the nightly drift report and ADR 0048 make trustworthy.
- A baseline can hide a real bug that predates it; the burn-down is what
  catches it, so the baseline has to shrink, not just exist.
- Revisit if default setup gains a quality suite, which would remove the
  per-repo workflow cost.
