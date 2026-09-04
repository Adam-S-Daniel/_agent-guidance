# Changelog — GitHub Actions runner egress allowlist (proposed)

Sidecar history for `network-allowlist-github-runners.txt`.

## Status: PROPOSED, not in force anywhere

Nothing enforces this list today. It is a reference draft, and the distinction
matters more here than in its Claude sibling: that one records a configuration
that is live, this one records a configuration that is not. Do not cite it as a
control.

**Default GitHub-hosted runners have unrestricted outbound egress**, and there
is no supported way to change that on a standard runner. GitHub's own roadmap
item for it — [github/roadmap#821, "Actions: Outbound network control for
GitHub-hosted runners"](https://github.com/github/roadmap/issues/821) — is
**closed as not planned**. So this list exists for the day one of the
mechanisms below is adopted, and as the thing an audit gets diffed against.

## How to write an entry here

Same three requirements as the Claude sibling, since an allowlist line has no
room for a comment and this file is the only place the *why* survives:

1. **The date** the change was applied (`YYYY-MM-DD`).
2. **The enforcement surface** it was applied to — which mechanism, which
   repos, and whether in audit or block mode. This is the field that replaces
   the Claude file's "environment name", and it is load-bearing for the same
   reason: an entry that does not say where a line is in force invites the
   assumption that it is in force everywhere.
3. **An explanation / justification per domain**, with the evidence — the spec,
   build step, or observed failure that proves something reaches for it.

Record **deliberate exclusions** too. A domain that was considered and rejected
looks exactly like a domain nobody thought of.

## Mechanisms this list could be fed into

| Mechanism | Notes |
|---|---|
| [StepSecurity Harden-Runner](https://github.com/step-security/harden-runner) | Works on standard runners today. Wants `host:port` — render each line as `<domain>:443`. Wildcard handling differs from the Claude environment's, so re-verify rather than assuming a `*.` line transfers. |
| [GitHub native egress firewall](https://github.com/github-early-access/actions-native-egress-firewall) | `runs-on: ubuntu-24.04-firewall`. Early access, **audit mode only** — no rule enforcement yet, so the allowlist syntax is not published and this file cannot yet be written in it. |
| Azure VNET private networking | Larger runners only; standard GitHub-hosted runners are not supported. You own the NSG, so you own the filtering. |

## The list is incomplete by construction — read this before enforcing

It covers what **this fleet's own jobs** reach. It does **not** include the
endpoints the Actions runner needs to function at all (job orchestration, log
upload, cache, artifacts). Those are published by GitHub at
`https://api.github.com/meta` under `domains.actions`, or
`domains.actions_inbound.full_domains` for the unwildcarded set, and they change
over time — which is exactly why GitHub recommends allowlisting by domain from
that endpoint rather than freezing a copy into a file like this one.

Fetching it was attempted while drafting this file and **failed**: the authoring
session's own egress proxy answered `This GitHub API path is not available:
sessions are bound to their configured repositories`. So the runner-platform
half is a stated gap, not an omission — resolve it against the live endpoint
before any block-mode rollout.

**The right order of operations is audit first.** Run Harden-Runner with
`egress-policy: audit` across a full CI cycle, diff the observed egress against
this file, and only then switch to `block`. A hand-written allowlist that has
never been measured will break a build; the only question is which one.

---

## 2026-08-28 — initial draft

**Enforcement surface:** none (proposed).
**Derived from:** static inspection of `cms-platform`, `adamdaniel.ai` and
`jodidaniel.com` at the commits checked out on this date. Not from an
observed-traffic audit.

| Domain(s) | Why |
|---|---|
| `github.com`, `*.github.com` | `actions/checkout`; `api.github.com` for the PR/label/auto-merge automation; release-binary downloads for `gitleaks`, `actionlint` and `yq` (`github.com/<org>/<repo>/releases/download/...`, seen in `_agent-guidance/.github/workflows/ci.yml` and the platform's `secrets-scan` lane). |
| `*.githubusercontent.com` | **Not covered by `*.github.com` — different apex.** A `releases/download/` URL redirects to `objects.githubusercontent.com` / `release-assets.githubusercontent.com`, so every binary fetch above lands here. Also `raw.githubusercontent.com` and the runner's own `*.actions.githubusercontent.com` control plane. |
| `registry.npmjs.org` | `npm ci` in the e2e harness and in `_agent-guidance`'s own CI. |
| `rubygems.org`, `index.rubygems.org`, `api.rubygems.org` | Bundler resolving the Jekyll toolchain and the `cms-platform-theme` gem. |
| `azure.archive.ubuntu.com`, `archive.ubuntu.com`, `security.ubuntu.com`, `ppa.launchpad.net` | `npx playwright install --with-deps` runs `apt-get` for ~90 system packages on **every** e2e job — the prebaked container image was deliberately not ported (see `adamdaniel.ai/docs/TESTING.md`, "CI hits these CDNs — and apt — on EVERY job"). GitHub-hosted Ubuntu runners resolve to the Azure mirror first, hence it is listed explicitly. |
| `cdn.playwright.dev`, `playwright.azureedge.net`, `playwright.download.prss.microsoft.com` | Playwright browser-binary download, same per-job path. The three hosts are the set named in `adamdaniel.ai/docs/TESTING.md`. Note `playwright.azureedge.net` is listed at its **apex**, which is where the binaries actually are. |
| `unpkg.com` | The `/admin` shells load the Decap CMS bundle from `https://unpkg.com/decap-cms@…` (confirmed in `cms-platform/theme/admin/*.html`). The `@admin-read` / `@admin-write` e2e lanes drive a real browser against `/admin`, so the **runner** fetches this, not just an editor's laptop. |
| `*.amazonaws.com` | `aws s3 sync` / `aws s3 rm` / `aws cloudfront create-invalidation` in the deploy lanes, STS for the OIDC role assumption, and the Decap OAuth proxy on API Gateway (`<id>.execute-api.us-east-1.amazonaws.com`). |
| `adamdaniel.ai`, `*.adamdaniel.ai`, `jodidaniel.com`, `*.jodidaniel.com` | The prod publish loops and preview probes fetch the live sites and the `preview-pr<N>` subdomains as the assertion under test. |

### Deliberately excluded

- **`ghcr.io`** — the GHCR prebaked ci-runner image was dropped in favour of
  inline browser installs and is referenced by no workflow in these repos.
  Re-add only if that decision is reversed.
- **`*.githubassets.com`** — static assets for the github.com **web UI**. A
  runner never loads them. Present in the Claude environment list, where a
  browsing agent legitimately does.
- **`docs.microsoft.com`, `learn.microsoft.com`, `githubstatus.com`,
  `*.claude.ai`, `*.claude.com`, `*.claudeusercontent.com`** — reference and
  session-infrastructure domains for an interactive agent. Nothing in CI reads
  documentation or talks to Claude. Carrying them here would widen the runner's
  reach for no build step's benefit.
- **`decapcms.org` / `*.decapcms.org`** — the Decap **documentation** site.
  Resolved 2026-08-28: it is reference material an agent reads, not something
  any build step fetches — the Decap bundle comes from `unpkg.com` (above). It
  belongs in the Claude environment list, where it now appears in apex +
  wildcard form, and correctly does not belong here. CI reads no documentation.

---

## 2026-09-04 — diffed against a live `My Whitelist` snapshot; exclusions re-affirmed, no change

**Enforcement surface:** none (proposed) — unchanged.
**Change to the `.txt`:** **none.** This entry exists because the diff was done
deliberately, and an absent domain and a rejected domain look identical in the
`.txt`.

The operator pasted the live domain list from the `My Whitelist` Claude
environment on this date and asked for both allowlists to be reconciled against
it. `network-allowlist-claude-environments.txt` took two new pairs from it
(`agents.md`, `developers.openai.com` — see its sidecar). This file took
nothing, and that is the decision, not an oversight.

Twenty-one of the snapshot's twenty-eight lines are absent here. Every one of
them falls under a category the 2026-08-28 entry above already rejected:

| Snapshot lines absent here | Category | Already rejected as |
|---|---|---|
| `*.claude.ai`, `claude.ai`, `*.claude.com`, `claude.com`, `*.frame.claudeusercontent.com`, `*.frame.staging.claudeusercontent.com` | session infrastructure for an interactive agent | "Nothing in CI … talks to Claude." |
| `*.docs.microsoft.com`, `docs.microsoft.com`, `*.learn.microsoft.com`, `learn.microsoft.com`, `*.decapcms.org`, `*.agents.md`, `agents.md`, `*.developers.openai.com`, `developers.openai.com` | reference documentation an agent reads | "CI reads no documentation." |
| `*.githubassets.com`, `githubassets.com` | static assets for the github.com **web UI** | "A runner never loads them." |
| `*.githubstatus.com`, `githubstatus.com` | status page an agent reads when triaging | same reference category |

The two new pairs (`agents.md`, `developers.openai.com`) are new instances of
the documentation category, not a new case — so the existing exclusion covers
them without needing to be widened.

### The three lines here that are absent from the snapshot are also correct

`*.amazonaws.com`, `*.githubusercontent.com` and the package-manager block
(`registry.npmjs.org`, the three `rubygems.org` hosts, the three Ubuntu archive
hosts, `ppa.launchpad.net`) are absent from `My Whitelist` because that
environment has **"Also include default list of common package managers"**
checked, which supplies them invisibly. They are not optional here: this list
has no such checkbox behind it, so a runner allowlist that omitted them would
fail `npm ci`, `bundle install` and `apt-get` on the first job.

That asymmetry is the reason a plain union of the two files would be wrong in
both directions, and it is why neither file is derivable from the other.

### Standing caveat, restated

Per the 2026-08-28 entry: if the package-manager checkbox on `My Whitelist` is
ever **unchecked**, `*.githubusercontent.com` and `*.amazonaws.com` stop being
redundant *there* and must be added to the Claude list explicitly. Nothing about
this file changes in that case.
