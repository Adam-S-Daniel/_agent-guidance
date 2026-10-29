# Skills

## The registry

[`agentskills`](../starter/agent-skills/README.md) is a Claude Code plugin
marketplace (`.claude-plugin/marketplace.json`) with four plugins, grouped by
audience and runtime: `anything-anywhere` and `coding-anywhere` (cloud-safe,
enabled by default), `coding-local` and `non-coding-local` (machine-bound,
opt-in). A skill is a folder `plugins/<plugin>/skills/<skill>/` holding a
`SKILL.md`, and the same file works in Codex and any agent that reads the
Agent Skills format.

The kit ships every plugin empty (DECISIONS X9). To add your first skill:

1. Write `plugins/<plugin>/skills/<skill>/SKILL.md`, and a `PURPOSE.md`
   beside it saying what incident or pattern motivated it.
2. Add it to `scripts/plugin_layout.json` (the canonical list of which
   plugin ships which skill) and an entry to `docs/skill-impact.md`.
3. Give it at least one eval fixture in `skills-evals` (`evals/<skill>/`):
   a skill graduates into the registry only with a green `with_skill` arm
   ([evals](evals-and-loop.md#fixtures)).

Its folder name is a one-way door once shipped: a consumer's `skills.lock`
keys on it, and the marketplace has no `renames` map. The registry's
`AGENTS.md` says what else each change needs.

**Durable machines** add the marketplace once and get the enabled plugins:
`install-machine.sh` runs the registry's `setup.sh --owner-machine`, which
writes both marketplaces and the plugins into `~/.claude/settings.json`
([machines](machine.md#install-on-each-durable-machine)).

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
personal data, so `agentskills`' required `privacy` job scans every added
line and commit message of a change against a denylist of your real names,
emails, hosts and account ids. The list is the repository secret
`PRIVACY_DENYLIST`, never a file in a repo; the script prints only
`file:line` and the pattern's number, never the match, and fails closed
without the secret (a fork's pull request included).

Write the list as a file outside every repo, one regular expression per
line, and store it before the `agentskills` starter pull request:

```bash
bash bootstrap/set-secrets.sh --only privacy --denylist ~/private/denylist
```

The script refuses a path inside a git work tree, checks every line
compiles, and sends the file to `gh` on stdin. Run the same check locally
with `PRIVACY_DENYLIST_FILE=<path> python3 scripts/check_privacy_denylist.py`
in your `agentskills` clone. Everything the public repos hold uses invented
data: `example.com`, made-up names, `<user>` placeholders.

## The private registry and lessons

[`agentskills-private`](../starter/agent-skills-private/README.md) is
packaged the same way, for skills that are personal rather than reusable.
Its branch `persistent/lessons` holds the lesson records the session miner
writes from your transcripts on your machine
([machines](machine.md#scheduled-jobs-wsl-automation)); `skills-evals`'
daily draft lane reads them with the `registry-read` App and turns one
new-skill lesson a day into a draft skill, or, when a check would do, a
hook proposal ([evals](evals-and-loop.md#the-loop)).
