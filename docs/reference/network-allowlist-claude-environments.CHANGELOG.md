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

### Three mechanics worth restating, because they shape every entry

- **Wildcards do not cover the apex.** `*.example.com` matches subdomains of
  `example.com`; it does not match `example.com` itself. That is why most
  entries appear as a pair.
- **"Also include default list of common package managers" is a separate,
  invisible input.** With it checked, the environment ALSO allows Anthropic's
  ~200-domain Trusted list (npm, RubyGems, PyPI, Ubuntu archives,
  `*.amazonaws.com`, `*.githubusercontent.com`, and more). A line that looks
  missing here may be covered there. Record the checkbox state whenever it
  changes, because the same `.txt` behaves very differently under each setting.
- **An edit to the environment takes effect in a session already running.**
  Measured 2026-09-04: three domains went from `000` to reachable in a session
  half an hour old, with no restart, no new container, no reconnect. So a
  blocked domain can be fixed without losing the session that hit it — and, the
  direction that matters for this file, a probe result is evidence only about
  the moment it was taken. Re-probe rather than cite a measurement from earlier
  in the same session.

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

---

## 2026-08-28 — three apex/host additions, measured not inferred

**Environment:** `My Whitelist`
**Checkbox "Also include default list of common package managers":** checked (unchanged)
**Change:** three domains ADDED. Nothing removed, nothing rewritten.

```
+ decapcms.org
+ playwright.azureedge.net
+ unpkg.com
```

### How these were established

Not by reading the list. Each was probed from inside a session running under
this allowlist, with `curl -s -o /dev/null -w '%{http_code}' https://<host>`.
A `000` is the egress proxy refusing to connect; any HTTP status means the
host was reached.

| Probe | Result | What it establishes |
|---|---|---|
| `decapcms.org` | `000` blocked | The apex is not reachable… |
| `www.decapcms.org` | `301` reached | …while a subdomain of it is. |
| `unpkg.com` | `000` blocked | Not in the list, and not in Trusted either. |
| `playwright.azureedge.net` | `000` blocked | The apex is not reachable… |
| `cdn.playwright.dev` | `400` reached | …though its sibling Playwright entry works. |

The first two rows are the load-bearing pair. They **prove**, rather than
assume, that `*.example.com` matches subdomains and does **not** match the
apex — the trap named in this file's header, now measured on this exact
environment.

### Per-domain justification

- **`unpkg.com`** — the `/admin` shells load the Decap CMS bundle from
  `https://unpkg.com/decap-cms@…` (confirmed in `cms-platform/theme/admin/*.html`).
  A session running the `@admin-read` / `@admin-write` Playwright lanes drives a
  real browser at `/admin`, so the session itself fetches this. Without it Decap
  never mounts and only the static "PENDING" banner renders — the failure mode
  already written up in `adamdaniel.ai/AGENTS.md` under "Running the admin e2e
  lane in a sandboxed / Claude-Code-web session". Not covered by Trusted.
- **`playwright.azureedge.net`** — one of the three hosts Playwright fetches
  browser binaries from (`adamdaniel.ai/docs/TESTING.md`). The list already
  carried `*.playwright.azureedge.net`, which matches *subdomains of* that host
  — and the binaries are on the host itself, so that entry was matching nothing
  reachable. The apex is the fix; the wildcard is left in place as harmless.
- **`decapcms.org`** — the Decap documentation site, and the apex half of the
  `*.decapcms.org` entry that was already here. This resolves an open question
  from the entry above: `*.decapcms.org` is **not** a stray. It sits in the same
  category as the `docs.microsoft.com` and `learn.microsoft.com` pairs already
  in this list — reference material an agent reads — and both of those were
  correctly listed in apex + wildcard form. This one was not.

### Deliberately NOT applied

Two further corrections were identified and **rejected for this list**, because
they are needed only where CI runs, and belong in
`network-allowlist-github-runners.txt` instead:

- **`*.githubusercontent.com`** — release-binary downloads (`gitleaks`,
  `actionlint`, `yq`) redirect from `github.com/.../releases/download/...` to
  `objects.githubusercontent.com`. Real, but Anthropic's Trusted list already
  carries `objects.`, `release-assets.` and `raw.githubusercontent.com`, and the
  package-manager checkbox is on. Adding it here would be redundant.
- **`*.amazonaws.com`** — `aws s3 sync` / `cloudfront create-invalidation` in
  the deploy lanes, and the Decap OAuth proxy on API Gateway. A deploy is a
  runner concern, and Trusted covers `*.amazonaws.com` regardless.

If the package-manager checkbox is ever **unchecked**, both of the above stop
being redundant and must be added here explicitly. That is the single change
that would invalidate this entry.

---

## 2026-09-04 — union with the live environment; two additions, three absences noted

**Environment:** `My Whitelist`
**Checkbox "Also include default list of common package managers":** checked (unchanged)
**Change:** two domain pairs ADDED, from a snapshot of the live environment
dialog pasted by the operator on this date. Nothing removed, nothing rewritten.

```
+ *.agents.md
+ agents.md
+ *.developers.openai.com
+ developers.openai.com
```

### Per-domain justification

Both pairs are **reference material an agent reads**, the same category as the
`docs.microsoft.com` / `learn.microsoft.com` / `decapcms.org` pairs already
here. Neither is covered by Trusted, and neither is reached by any build step —
so neither belongs in the runner list.

- **`agents.md` / `*.agents.md`** — the `AGENTS.md` convention's own site. Every
  repo in this fleet carries an `AGENTS.md` generated from `agents-md/base.md`
  in this repo, so a session working on that machinery has direct reason to read
  the upstream spec.
- **`developers.openai.com` / `*.developers.openai.com`** — OpenAI's developer
  documentation, read as reference when reasoning about another provider's API.
  Added by the operator; no build step in this fleet calls it.

Both are recorded in apex + wildcard form, which is the correct shape per this
file's header — the apex line is what actually gets fetched, and the wildcard
does not cover it.

### Three domains in this file are NOT in the live environment

The snapshot was diffed against this file in both directions. Three lines are
here and absent from `My Whitelist`:

```
decapcms.org
playwright.azureedge.net
unpkg.com
```

Those are **exactly** the three additions of the 2026-08-28 entry above. Each
was established by probe, not inference, and each has a justification recorded
there that still holds — `unpkg.com` in particular is what the `/admin` shells
load the Decap bundle from, whose absence is the "Decap never mounts, only the
PENDING banner" failure already written up in `adamdaniel.ai/AGENTS.md`.

So this is not drift in the file; it is the file recording a correction that
was never applied to the environment dialog. **They are deliberately retained
here.** Applying them is a one-time paste into the environment at
[claude.ai/code](https://claude.ai/code) and is the operator's call; until then
this file is a superset of what is in force, and the next snapshot diff should
show these three again rather than treating them as strays to delete.

### Two long-asserted Trusted-list claims, now measured

Probed from a session running under this environment on 2026-09-04, same
method as the 2026-08-28 entry: `curl -s -o /dev/null -m 15 -w '%{http_code}'
https://<host>`. `000` is the egress proxy refusing to connect; any HTTP status
means the host was reached.

The 2026-08-28 entry rejected `*.githubusercontent.com` and `*.amazonaws.com`
from this list on the grounds that the package-manager checkbox already covers
them. That reasoning was never probed — the five probes in that entry are all
about `decapcms.org`, `unpkg.com` and the Playwright hosts. It was inference,
of exactly the kind that entry exists to replace, and it is load-bearing:
it is the entire reason two domains are absent here.

Both claims hold.

| Probe | Result | |
|---|---|---|
| `objects.githubusercontent.com` | `404` reached | release-binary redirect target |
| `release-assets.githubusercontent.com` | `404` reached | ditto |
| `raw.githubusercontent.com` | `301` reached | |
| `sts.amazonaws.com` | `302` reached | OIDC role assumption |
| `s3.amazonaws.com` | `307` reached | |
| `s3.us-east-1.amazonaws.com` | `307` reached | |
| `zkrofo300b.execute-api.us-east-1.amazonaws.com` | `404` reached | the jodidaniel.com Decap OAuth proxy |
| `registry.npmjs.org`, `rubygems.org`, `pypi.org`, `archive.ubuntu.com` | `200` reached | checkbox confirmed on |

**Controls, because a probe that cannot fail proves nothing.** `example.org`,
`www.wikipedia.org` and `nytimes.com` all returned `000` — in neither list and
not plausibly a package manager. `github.com` (`400`), `claude.ai` (`403`),
`agents.md` (`200`) and `developers.openai.com` (`200`) all returned a status,
confirming the two pairs added above are live in the environment and that a
reached host is distinguishable from a blocked one.

### New: Trusted covers the SUBDOMAINS, not the apex

```
githubusercontent.com   000 blocked
amazonaws.com           000 blocked
```

The same wildcard/apex trap this file's header names, now shown to apply to the
checkbox's list as well as to a hand-written line. It costs nothing today —
nothing in this fleet fetches either apex, and both are parked domains — but it
means "Trusted covers `*.amazonaws.com`" is literally true and must not be read
as "Trusted covers `amazonaws.com`". If a build step ever does reach an apex
under this checkbox, expect a `000` and add the apex explicitly.

### The three retained lines: blocked at 14:1x, APPLIED and reachable by 14:4x

They were re-measured blocked earlier in the same session, identical to
2026-08-28:

```
unpkg.com                 000 blocked
decapcms.org              000 blocked
playwright.azureedge.net  000 blocked
```

The operator then added them in the environment dialog, and all three now
answer:

```
unpkg.com                 200 reached
decapcms.org              200 reached
playwright.azureedge.net  307 reached
```

So the correction recorded on 2026-08-28 is finally in force, and this file is
no longer a superset of the environment. The two lists agree.

### An allowlist edit takes effect in a session ALREADY RUNNING

This is the reusable finding, and it was not written down anywhere. The probes
above were run from a session that had been running for half an hour before the
edit, with no restart, no new container and no reconnect. The proxy picked the
change up live.

That is worth knowing in both directions. A missing domain can be fixed without
losing the session you hit it in — no need to re-run an hour of work in a fresh
container. And a session's reach is **not** fixed at start, so a probe result is
only evidence about the moment it was taken; an allowlist measurement more than
a few minutes old should be re-taken rather than cited.

### The wildcard/apex mechanic was re-verified at the same time, independently

`decapcms.org` going green needed ruling out as a change to the header's
central rule, since that rule's original proof pair was `decapcms.org` /
`www.decapcms.org` — the very domain being edited. So it was re-tested on
domains whose wildcard is listed and whose apex has never been, in this file or
in the environment:

| Probe | Result | |
|---|---|---|
| `download.prss.microsoft.com` | `000` blocked | apex, never listed |
| `test.download.prss.microsoft.com` | `404` reached | a subdomain of it |
| `frame.claudeusercontent.com` | `000` blocked | apex, never listed |
| `frame.staging.claudeusercontent.com` | `000` blocked | apex, never listed |

The rule holds: `*.example.com` still does not match `example.com`. So
`decapcms.org` is reachable because it was added explicitly, not because its
wildcard started covering it — which also means the three `*.`-only lines above
are genuine gaps if anything ever fetches those apexes. Nothing does today.

**Controls for this round**, since the question was whether the list had simply
opened up: `example.org`, `example.net`, `nytimes.com`, `www.wikipedia.org`,
`stackoverflow.com`, `news.ycombinator.com` and `reddit.com` all returned `000`,
and the `githubusercontent.com` / `amazonaws.com` apexes stayed blocked.

### Deliberately NOT applied elsewhere

Neither new pair was added to `network-allowlist-github-runners.txt`. That
file's own changelog already rejects the whole reference-documentation category
("Nothing in CI reads documentation"), and these two sit squarely in it.

---

## 2026-09-04 (second entry) — three additions, and one duplicate line to tidy

**Environment:** `My Whitelist`
**Checkbox "Also include default list of common package managers":** checked (unchanged)
**Change:** three domains ADDED, appended to the environment by the operator and
mirrored here. Nothing removed, nothing rewritten.

```
+ *.agentskills.io
+ agentskills.io
+ learn.chatgpt.com
```

### Measured, from a session running under this environment

Per the header mechanic added earlier today, an environment edit lands in a
running session, so these were probed immediately rather than deferred:

| Probe | Result |
|---|---|
| `agentskills.io` | `308` → `/home` (relative, same host) → `200` |
| `learn.chatgpt.com` | `200` |
| `example.org` (control) | `000` |

Neither redirects off its own host, so neither needs a companion entry — the
trap that made `cdn.playwright.dev` worth checking does not apply here.

### Per-domain justification

- **`agentskills.io` / `*.agentskills.io`** — added by the operator. Note this
  is a **third-party domain**, unrelated to this account's own skills registry:
  that one is the GitHub repository `Adam-S-Daniel/agentskills`, reached over
  `github.com`, and it has no web domain. The name collision is worth stating
  once so a future reader does not assume this line is what delivers the fleet's
  skill bundles — it is not, and removing it would not affect skill delivery.
- **`learn.chatgpt.com`** — reference documentation, the same category as
  `developers.openai.com` beside it.

The `*.agentskills.io` wildcard covers nothing reachable today: neither
`www.agentskills.io` nor `docs.agentskills.io` has a DNS record. It is recorded
as harmless future-proofing, and as the apex+wildcard pairing this file prefers.

`learn.chatgpt.com` is deliberately recorded WITHOUT a wildcard. It is itself a
subdomain of `chatgpt.com` (which stays blocked, measured `000`, and which
nothing here needs); a `*.learn.chatgpt.com` line would match nothing.

### `developers.openai.com` was appended a second time

The operator's append list also carried `developers.openai.com`, which this
file and the environment both already had from the first 2026-09-04 entry. It
is a **duplicate line in the environment dialog**, not a new domain — no
behaviour change, and this file is a set so it absorbs it silently. Worth
tidying in the dialog only to keep a future snapshot diff honest: a list with
duplicates makes a line-count comparison disagree with a set comparison, and
every reconciliation in this file is done as a set.

---

## 2026-09-04 (third entry) — the Playwright docs gap closed, and the first REMOVAL

**Environment:** `My Whitelist`
**Checkbox "Also include default list of common package managers":** checked (unchanged)
**Change:** one pair ADDED, one line REMOVED, one duplicate tidied.

```
+ *.playwright.dev
+ playwright.dev
- cdn.playwright.dev
```

### Why the pair was added

A wildcard audit of the whole list found exactly two lines with no `*.` twin:
`cdn.playwright.dev` and `unpkg.com`. Neither merited one — but the audit
surfaced an adjacent gap. `playwright.dev` and `www.playwright.dev` both
resolved and were both **blocked** (`000`), so the list carried the Playwright
CDN and not the Playwright **documentation** — the one Playwright surface an
agent actually reads while writing or debugging a spec. Same category as
`docs.microsoft.com` and `decapcms.org`. Now `200` and `301` respectively.

### Why the removal is safe — measured, not reasoned

`*.playwright.dev` subsumes `cdn.playwright.dev`, so the explicit line became
redundant and was dropped. **This is the first line ever removed from this
file**, so it was verified end to end rather than argued from the wildcard
rule:

| Probe | Result |
|---|---|
| `cdn.playwright.dev` | `400` reached — identical to when it was listed explicitly |
| the real browser download | `307` → `playwright.download.prss.microsoft.com` → `200`, `Content-Length: 182166967` |
| a ranged request for the same object | `206`, 1024 bytes actually transferred |

The last row is the one that matters. A reachable host and a completed download
are different claims, and only the second is what CI and the `@admin-*` lanes
depend on; bytes moved, so the chain is whole. The redirect target remains
covered by `*.download.prss.microsoft.com`, unchanged.

**The standing caveat this creates.** `cdn.playwright.dev` is now reachable
only by virtue of a broader line. Anyone who later narrows `*.playwright.dev`
— or removes it as "just documentation" — silently breaks Playwright browser
downloads in every session under this environment, and the failure appears as a
browser-install timeout rather than as a network denial. If that narrowing is
ever proposed, restore the explicit `cdn.playwright.dev` line in the same edit.

### The duplicate is gone

The previous entry flagged `developers.openai.com` as appearing twice in the
environment dialog. The snapshot behind this entry carries 35 lines, all
unique, so it has been tidied. Line count and set size now agree, which is what
makes a future reconciliation's line-count sanity check trustworthy.

### Parity

The `.txt` and the environment are identical as sets, 35 lines each, verified
with `diff` over sorted unique lists in both directions.
