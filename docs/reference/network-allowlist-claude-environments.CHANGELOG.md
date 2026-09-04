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

## 2026-09-04 — four documentation hosts, for the #114 primary-doc check

**Environment:** `My Whitelist`
**Checkbox "Also include default list of common package managers":** checked (unchanged)
**Change:** four domains ADDED by the operator, in the environment dialog,
during the session working https://github.com/Adam-S-Daniel/_agent-guidance/issues/114.
Nothing removed, nothing rewritten.

```
+ developers.openai.com
+ learn.chatgpt.com
+ *.agentskills.io
+ agentskills.io
```

### Why

Issue #114 asks a session to verify the base.md-vs-skill decision for
host-bound procedures against the primary docs, and names the three sources:
OpenAI's Codex `AGENTS.md` and skills guides, `agents.md`, and the Agent
Skills specification. The session that drafted the issue had all three
blocked, so its OpenAI-side facts came from search summaries and
`openai/codex` issues. The first session to work it hit the same wall — and
one more: `developers.openai.com/codex/...` answers **308** to
`learn.chatgpt.com/docs/...`, so the OpenAI docs need BOTH hosts, and the
redirect target is the one nobody would have guessed to list.

### How these were established

Probed from inside the session that was running when the operator applied the
change, with `curl -s -o /dev/null -w '%{http_code}' https://<host>/`. A `000`
is the egress proxy refusing to connect; any HTTP status means the host was
reached.

| Probe | Before | After | What it establishes |
|---|---|---|---|
| `developers.openai.com` | `000` blocked | `200` reached | Apex now in force. |
| `learn.chatgpt.com` | `000` blocked | `200` reached | The 308 target is reachable, so the OpenAI guides render end to end. |
| `agentskills.io` | `000` blocked | `308` reached | Apex in force (it redirects `/` to the spec site's landing page). |
| `www.agentskills.io` | `000` blocked | `000` blocked | The `*.agentskills.io` wildcard was NOT observed matching a subdomain in this session. See below. |

Two things about the "after" column:

- **The change reached `curl` in the running session without a restart**, so
  the list is applied live by the proxy, not baked in at container start.
- **The `WebFetch` tool still reported `EGRESS_BLOCKED` for all three hosts
  after `curl` could reach them.** That tool carries its own allow decision and
  did not pick the change up mid-session. So "the domain is allowed" and "the
  fetch tool can use it" are two different facts; when one of these hosts is
  needed from `WebFetch`, expect to need a fresh session, and probe with `curl`
  before concluding the list is wrong.
- `www.agentskills.io` staying `000` is either the wildcard line not yet in
  force or the name not resolving; it was not needed (the spec and client list
  live on the apex), so it is recorded and not chased.

### Per-domain justification

- **`developers.openai.com`** — the entry point for OpenAI's Codex docs:
  `/codex/guides/agents-md` (AGENTS.md discovery order, `AGENTS.override.md`,
  the `project_doc_max_bytes` limit) and `/codex/skills`. Reference material an
  agent reads; same category as the `learn.microsoft.com` pair.
- **`learn.chatgpt.com`** — where the two paths above actually resolve (308).
  Without it the apex is a dead door. Listed as a bare host, not a wildcard:
  only `learn.` was observed and nothing else on `chatgpt.com` is wanted.
- **`agentskills.io`** + **`*.agentskills.io`** — the Agent Skills
  specification (`/specification`) and the client showcase (`/clients`), which
  `agentskills`' `scripts/skills_registries.yml` names as the source of truth
  for the SKILL.md frontmatter contract. Apex + wildcard in the same shape as
  the other documentation pairs in this list.

### Deliberately NOT applied

- **`github.com/agentskills/agentskills`** was already reachable (it is under
  the existing `github.com` entry) and served the spec's source in the
  meantime. It stays the fallback; the apex is what the issue and the
  registry's own comments cite.
