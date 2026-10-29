# Skills

## The registry

[`acsi-agentskills`](../starter/agent-skills/README.md) is a Claude Code plugin
marketplace (`.claude-plugin/marketplace.json`) with four plugins, grouped by
audience and runtime: `acsi-anything-anywhere` and `acsi-coding-anywhere` (cloud-safe,
enabled by default), `acsi-coding-local` and `acsi-non-coding-local` (machine-bound,
opt-in). A skill is a folder `plugins/<plugin>/skills/<skill>/` holding a
`SKILL.md`, and the same file works in Codex and any agent that reads the
Agent Skills format.

The kit ships its skills in tiers ([choosing optional
content](#choosing-optional-content)). The four core skills are always in
the registry: `workflow-path-audit`, `skills-doctor`,
`github-actions-repo-settings` and `vendor-release-impact-issues`, copied
unchanged from the owner's registry into `acsi-coding-anywhere`, with their
eval fixtures in `skills-evals` (`evals/<skill>/`), so the loop has
something to measure on day one. The optional ones are added with
`bootstrap/add-content.sh`. The template is `templates/skill/` in the
registry (a `SKILL.md` and a `PURPOSE.md` with `<placeholders>`); it sits
outside `plugins/`, so nothing loads, lists or locks it. To add your own
skill:

1. Copy `templates/skill/` to `plugins/<plugin>/skills/<skill>/`, fill in the
   `SKILL.md`, and write the `PURPOSE.md` saying what incident or pattern
   motivated it.
2. Add it to `scripts/plugin_layout.json` (the canonical list of which
   plugin ships which skill) and an entry to `docs/skill-impact.md`.
3. Give it at least one eval fixture in `skills-evals` (`evals/<skill>/`):
   a skill graduates into the registry only with a green `with_skill` arm
   ([evals](evals-and-loop.md#fixtures)). `workflow-path-audit`'s fixtures
   are the worked example.

Its folder name is a one-way door once shipped: a consumer's `skills.lock`
keys on it, and the marketplace has no `renames` map. The registry's
`AGENTS.md` says what else each change needs.

**Durable machines** add the marketplace once and get the enabled plugins:
`install-machine.sh` runs the registry's `setup.sh --owner-machine`, which
writes both marketplaces and the plugins into `~/.claude/settings.json`
([machines](machine.md#install-on-each-durable-machine)).

## Choosing optional content

Every skill, guidance section and git-hook check the kit ships is in one of
three tiers, listed in
[`bootstrap/content-catalog.json`](../bootstrap/content-catalog.json):

- **Core**: what the rest of the system depends on. It is always in
  `starter/`, and the automatic retirement never removes it
  ([evals](evals-and-loop.md#the-loop)).
- **Recommended**: skills and guidance most adopters want: three skills
  (`review-bash-ci-reliability`, `debug-github-workflows`,
  `disarm-inherited-reach`), four safety sections and two sections of
  everyday working and git habits.
- **Opinionated**: one way of working that may not match yours: two skills
  (`writing-adrs`, `finding-unknowns`), four guidance sections and the
  `rules-dir` git hook. Each has a `details` text that says what it does and
  when it clashes with how you work.

The optional items live in [`library/`](../library/README.md), each with
everything it needs: the skill folder or the section's text or the check's
code, its eval fixtures and weekly schedule rows, its eval-coverage row, its
impact-log entry and its tests. Step 1 of the quick start adds the ones you
choose to your copy of `starter/`, before any tree is copied to GitHub:

```bash
bash bootstrap/add-content.sh --items recommended --dry-run    # what would change
bash bootstrap/add-content.sh --items recommended,writing-adrs # add them
bash bootstrap/add-content.sh --items none                     # add nothing
```

It is idempotent, refuses an id the catalog does not offer, rebuilds the
files generated from what it changed (the guidance mirror in every repo,
`AGENTS.md`, the section byte counts, the registry's README table and
`skills.lock`), and records your choices beside `status.sh`'s state file.
Run later, once the repos exist, it changes only `starter/` and lists the
repos that need a pull request. The `/adopt` skill asks the questions for
you.

## Cloud sessions and skills.lock

A cloud session starts with no plugins. Each repo instead commits a
`skills.lock` (the registry, an immutable commit, the bundles, and a sha256
per skill) and the `skills-bootstrap.sh` SessionStart hook, which the sync
delivers. The hook fetches the locked skills at session start, checks every
digest, and copies them into `~/.claude/skills`; on a durable machine it
does nothing. In a session opened on several repos it installs the union of
every lock, which needs a setup script in the cloud environment
([cloud environments](machine.md#cloud-environments)).

The exports' locks still pin the owner's registry (or forty zeros, in the
registries themselves) until you regenerate them
([the skills locks](generated-files.md#the-skills-locks)). After that,
`_agent-guidance`'s nightly `skills-lock-bump.yml` re-pins every consumer's
lock and the hook pin with the `guidance-sync` App.

## Plugin runtime review

A pull request that changes how a plugin runs code (its hooks, `bin/`, a
package install, plugin settings, a script a hook calls) waits on the
`plugin-runtime-review` environment, which only you approve
([ADR 0016](../starter/agent-skills/docs/decisions/0016-open-plugin-folders-and-gate-runtime-changes-on-owner-approval.md)).
`plugin-runtime-review.yml` runs on `pull_request_target`, so it judges a
pull request with the base branch's code; its `detect` job decides whether a
change is salient, and an ordinary one passes at once. The check it
publishes, `plugin-runtime-approval`, is required. When a pull request waits
on it, open the waiting job's page, read the change, and approve or reject
there. Never approve it for an agent.

## The privacy check

The registries' predecessor was retired because its git history carried
personal data, so `acsi-agentskills`' required `privacy` job scans every added
line and commit message of a change against a denylist of your real names,
emails, hosts and account ids. The list is the repository secret
`PRIVACY_DENYLIST`, never a file in a repo; the script prints only
`file:line` and the pattern's number, never the match, and fails closed
without the secret (a fork's pull request included).

Write the list as a file outside every repo, one regular expression per
line, and store it before the `acsi-agentskills` starter pull request:

```bash
bash bootstrap/set-secrets.sh --only privacy --denylist ~/private/denylist
```

The script refuses a path inside a git work tree, checks every line
compiles, and sends the file to `gh` on stdin. Run the same check locally
with `PRIVACY_DENYLIST_FILE=<path> python3 scripts/check_privacy_denylist.py`
in your `acsi-agentskills` clone. Everything the public repos hold uses invented
data: `example.com`, made-up names, `<user>` placeholders.

## The private registry and lessons

[`acsi-agentskills-private`](../starter/agent-skills-private/README.md) is
packaged the same way, for skills that are personal rather than reusable.
Its branch `persistent/lessons` holds the lesson records the session miner
writes from your transcripts on your machine
([machines](machine.md#scheduled-jobs-wsl-automation)); `skills-evals`'
daily draft lane reads them with the `registry-read` App and turns one
new-skill lesson a day into a draft skill, or, when a check would do, a
hook proposal ([evals](evals-and-loop.md#the-loop)).
