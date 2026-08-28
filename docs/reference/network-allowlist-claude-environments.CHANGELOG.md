# Changelog — Claude Code cloud-environment allowlist

Sidecar history for `network-allowlist-claude-environments.txt`.

## How to write an entry here

Every change to the `.txt` gets an entry, and every entry carries all three of
these. An allowlist line is a bare domain with no room for a comment, so this
file is the only place the *why* can live — a domain nobody can justify is a
domain nobody can safely remove.

1. **The date** the change was applied (`YYYY-MM-DD`), not the date it was
   proposed. If the file and the live environment were changed on different
   days, say both.
2. **The Claude environment name(s)** the change was applied to. This list is
   not global — it is per-environment configuration, edited in the environment
   dialog at [claude.ai/code](https://claude.ai/code). As of the first entry
   below, the only environment using it is **`My Whitelist`**. Naming the
   environment is what stops a future reader assuming a line is in force
   somewhere it was never applied.
3. **An explanation / justification per domain** — what actually reaches for
   it, and how that was established (a spec, a build step, an observed
   failure). "Seemed necessary" is not a justification; "the `/admin` shells
   load the Decap bundle from it, confirmed in `cms-platform/theme/admin/*.html`"
   is.

Also note anything **deliberately excluded** and why. An absent domain and a
rejected domain look identical in the `.txt`, and only this file can tell them
apart.

### Two mechanics worth restating, because they shape every entry

- **Wildcards do not cover the apex.** `*.example.com` matches subdomains of
  `example.com`; it does not match `example.com` itself. That is why most
  entries appear as a pair.
- **"Also include default list of common package managers" is a separate,
  invisible input.** With it checked, the environment ALSO allows Anthropic's
  ~200-domain Trusted list (npm, RubyGems, PyPI, Ubuntu archives,
  `*.amazonaws.com`, `*.githubusercontent.com`, and more). A line that looks
  missing here may be covered there. Record the checkbox state whenever it
  changes, because the same `.txt` behaves very differently under each setting.

---

## 2026-08-28 — initial import, verbatim

**Environment:** `My Whitelist`
**Checkbox "Also include default list of common package managers":** checked
**Change:** none. Imported the list exactly as it was already in force, sorted
only — no domain added, removed, or rewritten. Verified as an identical
multiset against the source before commit.

The single edit was cosmetic: `githubstatus.com`'s wildcard line had been
pasted as a markdown link, `[*.githubstatus.com](https://www.githubstatus.com)`,
which is not a parseable domain. It is recorded here as `*.githubstatus.com`.

This entry deliberately records the list **unjustified**. It is a snapshot of
what was in force on this date, captured so that the corrections that follow
have a baseline to diff against. Per-domain justification arrives with the
first correcting entry; where a domain turns out to have no justification, that
finding belongs here too.

Sorting is by registrable key with the leading `*.` stripped, so each wildcard
sits beside its apex, wildcard first. It is presentational only — the
environment does not care about order.
