# 2026-08-19: a first-occurrence split doubled this repo's managed block

Evidence for the regeneration rule in this repo's
[`AGENTS.md`](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/AGENTS.md#repo-specific-additions)
(`## Repo-specific additions`). The rule stays there; this file keeps the
story that justifies it, moved out under
[ADR 0018](../decisions/0018-incident-narratives-live-in-docs-evidence.md)
by [_agent-guidance#282](https://github.com/Adam-S-Daniel/_agent-guidance/issues/282).

## Original text

Moved verbatim from `AGENTS.md` (added in `3d55d7e`, 2026-08-19, "Catch a
corrupt AGENTS.md that regenerates to itself, and repair this one"):

> The recipe above is line-anchored, but between commit `c86465f` and this fix
> some tooling split the file on the first OCCURRENCE of the marker substring
> instead — and the managed block's own BEGIN header quotes the marker verbatim
> (`DO NOT EDIT ABOVE "## Repo-specific additions"`), so that split anchored on
> the header line rather than the real heading and treated the entire prior
> managed block as repo-specific content to preserve. Every regen after that
> prepended a fresh managed block on top of the old one, so the file carried two
> managed blocks — including two contradictory copies of the skills-ecosystem
> rule — for four commits (through `7b87581`). Because the recipe's own anchor
> kept matching the same corrupted line, the doubled file was a fixed point of
> regeneration, so the staleness check above stayed green throughout; only a
> check that counts markers and asserts their order can tell a doubled file from
> a well-formed one, which is what `scripts/check-agents-md.sh` does, and CI now
> runs it ahead of the staleness check for exactly this reason.

"This fix" is `3d55d7e`. `c86465f` is dated 2026-08-18 and `7b87581`
2026-08-19.

## Related record

- The "AGENTS.md structure is sound" step in
  [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs
  `scripts/check-agents-md.sh` before the staleness diff, and its comment
  tells the same story.
