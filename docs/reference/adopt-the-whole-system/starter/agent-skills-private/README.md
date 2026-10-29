# acsi-agentskills-private

The **private** half of the owner's agent skills — content that is simply
personal rather than generally reusable. It is packaged exactly like the
public registry: skills are grouped into **bundle plugins** under
`plugins/<bundle>/skills/<skill>/`, and the repo root is a Claude Code plugin
**marketplace** (`.claude-plugin/marketplace.json`).

The public, generally-reusable skills live in
[example-adopter/acsi-agentskills](https://github.com/example-adopter/acsi-agentskills),
the canonical upstream registry. The rule for what goes where — public vs
private — is documented in that repo's
[`STRATEGY.md`](https://github.com/example-adopter/acsi-agentskills/blob/main/STRATEGY.md):
**public = reusable + no secrets → acsi-agentskills; private (here) = anything
personal or sensitive → acsi-agentskills-private.**

> The `acsi-private-anything-anywhere` bundle holds skills that run
> everywhere: claude.ai (web, iOS, Chrome, Desktop) and Claude Code. More
> private bundles may be added later, following the same structure and the
> placement/promotion rules in the public `STRATEGY.md`. Invoke a skill as
> `/acsi-private-anything-anywhere:<skill>` (bundle-namespaced, same as the
> public registry's `/<bundle>:<skill>` form).

## Install — Claude Code

Use **Claude Code v2.1.280 or later**. That release fixes background
marketplace updates ignoring Git credential helpers, which could prevent this
private marketplace from updating or cause it to be re-cloned every run
([release notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.280)).

One command resolves the source, offers to add this marketplace, then installs
the bundle:

```bash
/plugin install acsi-private-anything-anywhere --marketplace example-adopter/acsi-agentskills-private
```

Installation refreshes the marketplace automatically, so newly published
plugins do not need a manual marketplace update first
([v2.1.232 release notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.232)).
Installing, enabling, or disabling a plugin from `/plugin` takes effect when
you close the menu; `/reload-plugins` is no longer needed afterward
([v2.1.268 release notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.268)).

The public registry installs the same way. Its
[README](https://github.com/example-adopter/acsi-agentskills#install--claude-code) has
the command.

On older CLIs, add this marketplace alongside the public one, then install by
`plugin@marketplace`:

```bash
/plugin marketplace add example-adopter/acsi-agentskills
/plugin marketplace add example-adopter/acsi-agentskills-private
/plugin install acsi-private-anything-anywhere@acsi-agentskills-private
```

Skills are namespaced by bundle, e.g. `/acsi-private-anything-anywhere:<skill>`.
To refresh the catalog manually later, use
`/plugin marketplace update acsi-agentskills-private`.

## Install — Codex, Gemini, Cursor, and local use

Run `setup.sh` once **in each environment** (Windows Git Bash *and* WSL). It links
every private skill under `plugins/*/skills/*` into the standard per-agent skill
homes — the same way the public repo does, and sharing the same de-dup so Claude
Code stays marketplace-only (never `~/.claude/skills`):

```bash
bash setup.sh
```

It is safe to run while a bundle has no skills yet — it simply reports that
there are none to link.

## Repo layout

```
.claude-plugin/marketplace.json       # private marketplace catalog (lists every bundle)
plugins/
  <bundle>/                           # acsi-private-anything-anywhere (more may be added later)
    .claude-plugin/plugin.json        # bundle manifest
    skills/<skill>/SKILL.md           # one dir per skill (+ scripts/, tests/, hooks/)
setup.sh                              # link private skills into per-agent dirs (non-Claude-Code)
```

Validate the marketplace and any plugin with `claude plugin validate <path>`.
