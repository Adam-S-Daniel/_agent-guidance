# 0007 — A SHA pin carries no version comment

**Status:** Accepted (2026-08-20). Supersedes, in part only, [0002](0002-unconditional-rules-live-in-the-guidance-not-a-skill.md) — exactly one item of the policy it moved into `base.md`, "the dated version comment". Everything else 0002 decided stands, including the conditionality split that put the pinning rule in the managed guidance in the first place.

## Context

0002 moved the SHA-pinning rule out of a skill and into `agents-md/base.md`,
and in doing so it moved the *whole* rule as it then stood — including the
requirement that every pin carry a trailing version comment:

```yaml
uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11 # v4.1.1 (2023-10-17)
```

The case for the comment is intuitive and is the reason it survived the move:
forty hex characters say nothing on their own, so the version says what the pin
*is* and the date says how stale it is. `base.md` even conceded the drift —
"Dependabot rewrites the SHA and the version but not the date, so dates drift —
cosmetic, a chore, never an incident."

That concession was wrong on its facts, and the error is not one of degree.
**Dependabot's rewriting of the comment is inconsistent, not merely
incomplete.** Measured across the fleet on 2026-08-20:

- In `GHA-bench#52` it rewrote a bare `# v5` to `# v7.0.0` while leaving `# v4`
  stale on the line directly above it — same file, same pull request.
- In `skills-evals#38`, `#39` and `#40` it moved the SHAs and left every
  `# vX.Y.Z (YYYY-MM-DD)` comment untouched.

The result in `skills-evals` is the failure in its clearest form: one action,
`actions/checkout`, actually at **v7.0.1**, labelled `# v4.3.1` in one file and
`# v6.0.0` in two others — three answers in one repo, none of them right.

"Cosmetic, a chore" assumed the label decays into noise. It does not; it decays
into a **wrong answer that is read and believed**. The comment exists precisely
so that a reviewer or an agent can judge currency without resolving the SHA, so
a stale one misinforms exactly the reader it was written for, and it does so
silently — nothing goes red, and the staleness the comment was supposed to
advertise is the thing it hides.

Two facts make this a fleet-level decision rather than a per-repo lint. The
comment is unmaintained *by construction*: nothing in this account writes it,
and the one external agent that touches it has now been measured doing so
unreliably. And `base.md` reaches ~20 repos on the next sync, so leaving the
mandate in place is a standing instruction to ~20 repos' agents to keep
producing labels no one maintains.

## Decision

**Drop the trailing version comment. A SHA pin is `@<sha>` and nothing after
it.**

```yaml
uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11
```

Scope, stated so it can be checked against a diff:

- Strip a trailing comment only when the ref is a full 40-hex SHA **and** the
  comment is purely a version, optionally with a date. A comment carrying other
  meaning — `# zizmor: ignore`, a security note, an explanatory sentence — is
  untouched.
- The bullet in `base.md` is **replaced, not deleted**. The argument for the
  comment is intuitive enough that the next person will re-derive it, so the
  guidance now carries the measurement that defeated it. A rule with its
  counterfactual removed invites its own reversal.
- The own-account carve-out is unaffected in principle but **widens in one
  shape**: a `cms-platform` **composite action** referenced from another repo
  now takes the same tag form as a reusable workflow
  (`…@v0.1.88`), rather than a SHA plus a `# vX.Y.Z` comment. That comment was
  the only thing tying such a pin to `platform.lock`'s `platform_ref`; removing
  it without moving the ref to a tag would have left the pin with nothing
  connecting it to the release it belongs to, and
  `check-platform-pin-consistency.js` asserts exactly that connection.

Everything else in the pinning section is untouched: the full 40-character SHA,
the 7-day cooling-off, annotated-tag dereferencing, the `./local/path` and
`docker://` exemptions, and `sha_pinning_required: true`.

## Consequences

- **The pin can no longer lie.** The only token left on the line is the one
  that is always correct.
- **Reading a version now costs a lookup.** This is the real price, and it is
  the whole of the case against this ADR. It is paid by whoever actually needs
  the version, at the moment they need it, and it returns a right answer —
  where the comment was free to read and returned a wrong one.
  `git ls-remote <url> | grep <sha>`, or the title of the Dependabot pull
  request that moved it.
- **Staleness stops being invisible.** There is no longer a field that looks
  maintained while rotting, so Dependabot's inconsistency has nothing left to
  be inconsistent about.
- **~20 repos' AGENTS.md change on the next sync, and their existing pins do
  not.** The guidance moves in one commit; the ~1,000 already-committed pins
  across the fleet do not rewrite themselves. Expect a period where both forms
  are in the tree, and treat a surviving comment as debt to strip on the next
  edit to that file rather than as a violation to sweep.
- **A test went away with the rule.** `test/run-tests.sh`'s version+date
  assertion enforced precisely what this reverses and was deleted; its sibling
  40-hex assertion is kept, because it reads the parsed `uses:` value and is
  comment-independent. There is now no automated check that a *new* pin arrives
  without a comment — review is the only gate, which is acceptable for a rule
  whose failure mode is a cosmetic extra token rather than an insecure pin.
- **This is the second amendment to 0002's payload, and the bar it set still
  binds.** 0002's own *Consequences* warned that "it's important, put it in
  `base.md`" applies to almost anything and that two more acceptances stop the
  block being short enough to read. This ADR removes lines rather than adding a
  section, so it spends none of that budget — but the replacement bullet is
  longer than the mandate it replaced, and that is a deliberate, bounded cost:
  the measurement is what stops the rule being re-reverted.
