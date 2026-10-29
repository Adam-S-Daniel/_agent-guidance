# Files the export cannot regenerate offline

DECISIONS P3 says generated files are rebuilt with each repo's own tooling.
`export.py` does that wherever the tool runs offline (the fleet payload
mirror, the section byte counts, `AGENTS.md`, the vendored hook's sha256 in
`repos.yml`). The files below need a GitHub API, a commit that exists only
once you have pushed, or your own GitHub App, so the export leaves them
substituted and you run the command after adoption. No test in the exported
trees depends on their hashes.

Run each command in your own clone of the repo named, on a branch, and open
a pull request with the result.

## `_agent-guidance`

- **`skills.lock`** names your registry's commit and each skill's digest.
  The digests change with your registry's content (the kit ships it with one
  skill, `workflow-path-audit`, DECISIONS X9). After your `acsi-agentskills` repo has its first commit:

  ```bash
  python3 ../acsi-agentskills/scripts/generate_skills_lock.py --repo ../acsi-agentskills \
    --registry <owner>/acsi-agentskills --bundles acsi-anything-anywhere,acsi-coding-anywhere -o skills.lock
  ```

  (`--repin` cannot do this first one: it checks the clone against the
  commit the lock already pins, which is the source registry's. After that,
  the nightly `skills-lock-bump.yml` re-pins it once its App is installed.
  The lock names the skills your registry has: the one the kit ships, then
  whatever you add. The exported lock still names the source registry's full
  list, which your registry does not have; this command replaces it.)

- **`repos.yml` `skills_bootstrap.ref`** names the registry commit the
  vendored `.claude/hooks/skills-bootstrap.sh` came from. The export
  recomputes `sha256` from the exported hook; `ref` still names the source
  commit. Set it to the commit of your `acsi-agentskills` that carries the same
  hook bytes:

  ```bash
  bash scripts/bump-hook-pin.sh   # run by skills-lock-bump.yml nightly
  ```

- **`.github/workflows/actions.lock`** was written by `gh actions-lock`. The
  export added the entry for `scheduled-run-health-reusable.yml` (DECISIONS
  C4) using tags this file already locks. Confirm, with network access:

  ```bash
  gh actions-lock --no-narrow && git diff --exit-code .github/workflows/actions.lock
  ```

## `repo-settings`

- **`.github/workflows/scheduled-run-health.yml`** calls
  `_agent-guidance`'s reusable at a placeholder sha (forty zeros: a
  cross-repo reusable takes a full sha, and yours does not exist until you
  push `_agent-guidance`). Replace it with your `_agent-guidance` main:

  ```bash
  sha=$(git ls-remote https://github.com/<owner>/_agent-guidance refs/heads/main | cut -f1)
  sed -i "s|scheduled-run-health-reusable.yml@0\{40\}|scheduled-run-health-reusable.yml@$sha|" \
    .github/workflows/scheduled-run-health.yml
  ```

- **`skills.lock`**: as for `_agent-guidance` above.

- **`.github/workflows/actions.lock`** is the source's: its one owner-named
  entry is the published `verify-actions-lock` action, kept on purpose
  (DECISIONS C5), with its publisher's public `owner_id`. Confirm with
  `gh actions-lock --no-narrow`.

- **GitHub App ids** (DECISIONS C12). `repo-settings/fleet.yml` names the
  two Apps it grants a ruleset bypass as `"${GUIDANCE_SYNC_APP_ID}"` and
  `"${ROSTER_WRITER_APP_ID}"`; the workflow maps them from repository
  variables of those names, and the engine refuses to plan while either is
  unset. Set both on your `repo-settings` repo to your Apps' numeric ids
  (the bootstrap's `create-environments.sh` sets both from `fleet.env`):

  ```bash
  gh variable set GUIDANCE_SYNC_APP_ID --repo <owner>/repo-settings --body <id>
  gh variable set ROSTER_WRITER_APP_ID --repo <owner>/repo-settings --body <id>
  ```

- **`repo-settings/fleet.yml`'s repo list** names only the kit's repos
  (DECISIONS C11). If you leave one out of your fleet (the example platform
  and its two sites, say), remove its element, and its rows in
  `tests/test_repo_settings.py`, before the first apply.

## `wsl-automation`

- **`skills.lock`**: as for `_agent-guidance` above.

- **`.github/workflows/actions.lock`** is the source's: its
  lowercase `adam-s-daniel/verify-actions-lock` key is the published action,
  kept on purpose (DECISIONS C5). Confirm with `gh actions-lock --no-narrow`.

- **The Codex Cloud bootstrap pin** (wsl-automation notes Q6).
  `scripts/sync-codex-cloud-environments.sh` downloads your registry's
  `.claude/hooks/skills-bootstrap.sh` at a reviewed commit and checks its
  digest. The export ships both as zeros, which fail closed (setup reports
  skills `DEGRADED`). After your `acsi-agentskills` repo has a reviewed commit:

  ```bash
  rev=$(git -C ../acsi-agentskills rev-parse HEAD)
  sum=$(git -C ../acsi-agentskills show "$rev:.claude/hooks/skills-bootstrap.sh" | sha256sum | cut -d' ' -f1)
  sed -i -e "s/^readonly bootstrap_revision='0\{40\}'/readonly bootstrap_revision='$rev'/" \
    -e "s/^readonly bootstrap_sha256='0\{64\}'/readonly bootstrap_sha256='$sum'/" \
    scripts/sync-codex-cloud-environments.sh
  bash tests/sync-codex-cloud-environments.test.sh
  ```

- **The waiting feed's commit identity** (DECISIONS C12).
  `scripts/publish-waiting-feed.sh` falls back to
  `0+example-adopter@users.noreply.github.com` when no git identity is set.
  Put your own noreply address there (GitHub shows it under Settings, Emails;
  it is `<your numeric id>+<your login>@users.noreply.github.com`), and the
  same string in `tests/publish-waiting-feed.test.sh`'s "fallback commit
  identity" assertion.

- **The waiting feed's repository** is not one of the kit's repos.
  `WAITING_FEED_URL` defaults to `https://github.com/<owner>/waiting-feed.git`:
  create that repository **private** (its feed holds your session titles),
  or point `WAITING_FEED_URL` elsewhere, before you register the task. Its
  `persistent/waiting-feed` branch is created by the first publish.

## `skills-evals`

- **`.github/workflows/scheduled-run-health.yml`** calls `_agent-guidance`'s
  reusable at a placeholder sha (DECISIONS C4), as in `repo-settings` above;
  the same `sed` command fixes it.

- **`.github/workflows/actions.lock`** is the source's: its one
  owner-named entry is the published `verify-actions-lock` action (DECISIONS
  C5), with its publisher's public `owner_id`. Confirm with
  `gh actions-lock --no-narrow`.

- **The routine trigger id** is not in any file (DECISIONS C7):
  `routine-eval-fire.yml` reads the variable `ROUTINE_TRIGGER_ID` from the
  `routine-fire` environment, beside the secret `EVAL_ROUTINE_FIRE_BEARER`.
  Create the routine from `docs/routine-prompt.md`, then store both:

  ```bash
  bash bootstrap/set-secrets.sh --only routine-fire
  ```

- **`.github/workflows/eval.yml`**'s `fixture` input defaults to
  `evals/workflow-path-audit/required-check-routing`, a fixture the kit ships
  (the one real skill's, DECISIONS X9), so a dispatch that keeps the default
  runs. Change the default when you have a fixture of your own.

## `acsi-agentskills` and `acsi-agentskills-private`

- **`skills.lock`** (both repos) is the public registry's own generator's
  output: the cloud-safe bundles with their one skill, `workflow-path-audit`
  (DECISIONS X9). Its `ref` and
  `generated_from` are forty zeros, because the commit they name is your
  first `acsi-agentskills` commit. Until you replace them, CI's
  `generate_skills_lock.py --check` and `--check-current` steps fail, and two
  dogfood tests skip ("pinned ref ... is not present"). After the starter
  pull request merges, in your `acsi-agentskills` clone:

  ```bash
  python3 scripts/generate_skills_lock.py   # pins HEAD; writes skills.lock
  python3 scripts/generate_skills_lock.py --check && python3 scripts/generate_skills_lock.py --check-current
  ```

  and copy the same `skills.lock` into `acsi-agentskills-private`. (Checked on
  the exported tree: after this both checks print OK, and the dogfood tests
  pass.) `--check-format` refuses a lock with no skills by design, so a
  registry that removed its last skill would fail it; the kit's one skill
  keeps it passing.

- **`.github/workflows/scheduled-run-health.yml`** (acsi-agentskills) calls
  `_agent-guidance`'s reusable at a placeholder sha, as in `repo-settings`
  above; the same `sed` command fixes it.

- **`.github/workflows/actions.lock`** (both) is the source's, owners
  substituted; its one owner-named entry is the published
  `verify-actions-lock` action (DECISIONS C5). Confirm with
  `gh actions-lock --no-narrow`.

- **`<your name>`** in `.claude-plugin/marketplace.json` and each plugin's
  two manifests is the kit's placeholder; `copy-starter.sh` fills it from
  `OWNER_NAME`.
