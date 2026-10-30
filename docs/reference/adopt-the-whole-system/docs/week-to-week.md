# Running it week to week

| When | What happens | What you do |
|---|---|---|
| Hourly | The fleet worker | Read its list of pull requests that need you |
| Daily | Fixture supply, the draft lane, retirement verdicts, the changelog watcher, the centralization audit, and on your machine the session miner and usage census | Nothing, unless an issue or pull request is waiting on you |
| Weekly | The roster refresh, the planned sweep (Tuesdays 07:00 UTC), memory curation (Mondays) | Skim the results on `persistent/eval-results` |
| On demand | A `plugin-runtime-review` deployment, a routine prompt change, a leaked key | Only you can do these |
| After any settings change | `bash bootstrap/verify.sh`, and `bash bootstrap/verify.sh --only settings` | Fix what it reports `FAIL` |

## Changing settings

Every repo setting lives in `repo-settings/fleet.yml`. Change it in a pull
request on `repo-settings`; on merge `repo-settings.yml` applies it to both
owners once you approve its `fleet-apply` deployment, and the daily drift
check opens an issue when a repo drifts from it. Never change a ruleset by
hand in the GitHub UI: the next apply puts it back.

## When the allowance runs short

When a week has room left near its end, raise `weekly_cap` in
`skills-evals`' `evals/budget.yml` for that week in a pull request and lower
it after the reset; do not remove the guard.

## When an eval does not arrive

Check in this order: did `routine-eval-fire.yml` start the routine (its run
log says the trigger accepted the request, and a `claude/eval-*` branch
appears)? Did the routine session report a guard refusal? Did
`routine-eval-ingest.yml` reject the branch? A green fire workflow means
only that the API accepted the request.

## Rotating a leaked key or bearer

- **An App key:** generate a new private key on the App's settings page and
  store it under the same name, in the same repo and environment
  ([GitHub Apps](settings-and-apps.md#github-apps) lists them), from stdin:
  `gh secret set <NAME> [--env <environment>] --repo <you>/<repo> < key.pem`.
  Delete the download, then delete the old key on the App's page.
  `bash bootstrap/verify.sh --only apps:<app>` checks the names.
- **The eval runner's bearer:** create a new API trigger token on the
  routine's page at https://claude.ai/code/routines, then
  `bash bootstrap/set-secrets.sh --only routine-fire --rotate`.
- **The privacy denylist:** edit the file outside your repos, then
  `bash bootstrap/set-secrets.sh --only privacy --rotate --denylist <path>`.
