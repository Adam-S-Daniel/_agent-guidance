# repo-settings

The fleet's repository settings, as files. This repo is private on every plan.

## What is here

| Path | What it is |
|---|---|
| `rulesets/default-branch.json` | PR-only default branch, merge commits only. |
| `rulesets/persistent-branches.json` | The full form: `persistent/**` cannot be deleted, force-pushed or updated except by an App. `apply-rulesets.sh` builds the bypass per repo (loop-writer in skills-evals, guidance-sync in agent-guidance, none elsewhere, where the update rule is dropped); the file's `111111` / `222222` actors are placeholders it never applies as they stand. |
| `rulesets/phase4/required-status-checks.json` | The `agent-checks` and `privacy` required checks, added in phase 4 only. `bin/lib/rulesets.sh` adds `plugin-runtime-review` for agent-skills alone (`EXTRA_CHECKS`). |
| `bin/` | A copy of the kit's `bootstrap/apply-rulesets.sh` and the two `lib/` files it sources, so the workflow below runs on its own. |
| `.github/workflows/apply.yml` | Re-applies `rulesets/` on a push that changes them and weekly (paid plan only). |
| `tests/run.sh` | JSON parse checks and the workflow's shape. |

These are the files `bootstrap/apply-rulesets.sh` applies. Run from the kit,
it reads `bootstrap/rulesets/` by default; point it at this repo's copy with
`--rulesets-dir`:

```bash
bootstrap/apply-rulesets.sh --all --rulesets-dir <checkout of repo-settings>/rulesets
```

In the kit, `starter/repo-settings/rulesets/` and `bootstrap/rulesets/` are
byte for byte the same, and so are `bin/` and the bootstrap scripts; a test in
`bootstrap/tests/test-bootstrap.sh` fails if they differ. Change a ruleset in
both places, or the next run of one of them undoes the other.

`bootstrap/copy-starter.sh` fills the App ID placeholders from `fleet.env`
when you copy this folder in, so the repo's copy may hold your real App IDs.
`apply-rulesets.sh` handles both forms.

## The workflow (paid plan only)

On GitHub Free a private repo has no environments, so the workflow cannot hold
the App key: run `bootstrap/apply-rulesets.sh` from your machine instead, and
delete `.github/workflows/apply.yml` or leave it failing.

On a paid plan:

1. `bootstrap/create-apps.sh settings-admin` creates the App and stores
   `SETTINGS_ADMIN_CLIENT_ID` (variable) and `SETTINGS_ADMIN_PEM` (secret) in
   this repo's `settings-admin` environment. Install the App on every fleet repo.
2. The repository variables the workflow header lists (`LOOP_WRITER_APP_ID`,
   `GUIDANCE_SYNC_APP_ID`, `REQUIRED_CHECKS`, `OWNER_TYPE`, `VISIBILITY_*`)
   mirror `fleet.env`: `bootstrap/create-environments.sh` sets them here.
   Re-run it after `create-apps.sh` and after `apply-rulesets.sh
   --with-required-check`, which records `REQUIRED_CHECKS=on`. Without that,
   the weekly run removes the phase 4 required checks.
3. Pin each `uses:` line: replace `<sha-of-the-v-tag-you-chose>` with the full
   commit SHA of the release tag you picked (guide 3.3).

Every run reads each ruleset back after writing it and fails on drift, the same
as a run from your machine.
