# Traps that cost real outages

Each of these happened on the owner's account. The rule beside it is what
the system now does, or what you should do.

| Trap | What happened | The rule |
|---|---|---|
| Inlined guidance | 19 copies loaded in one session, 332,000 tokens | Deliver it once through user memory ([the sync](guidance.md#the-sync)) |
| A global hook at an absolute path | The script moved; every push from every repo failed | Hooks are config-based and run the repo's own `.githooks/agent-checks` ([machines](machine.md#install-on-each-durable-machine)) |
| `cmd \| grep -q` under `pipefail` | `grep -q` exits at the first match; past 64 KiB the writer dies with 141 and a present match reads as absent | Pass data as a here-string: `grep -qxF -- "$m" <<<"$s"` |
| A refused commit, a successful push | The push sent the previous commit; the agent reported success | Verify with `git merge-base --is-ancestor <sha> origin/<branch>` |
| `git push -u origin <name>` from the wrong branch | It pushed a stale local branch of that name | Check the commit is on the remote branch after every push |
| A test that mocked a process id | It became `kill(-1, SIGKILL)` and killed every process of the user | Code that sends signals refuses any pid that is not an integer above 1; run such tests in a PID namespace (`unshare --user --map-current-user --pid --fork --mount-proc`) |
| `python3 test_x.py` | The file had no `__main__` block; it exited 0 having run nothing | Name the runner (`python3 -m pytest`) and check the test count |
| A watch command's exit code | `cmd \| tail` reports `tail`'s status | After any CI watch, read each check's conclusion |
| A path-filtered required check | The check never reported; the pull request could not merge | Always run it; skip steps inside ([settings](settings-and-apps.md#repo-settings-as-code)) |
| A cancelled run as the final status | Two runs on one commit; the cancelled one won | No concurrency group on a required check |
| A squash merge | A lock pinned a commit that no longer existed after the merge | Merge commits only; `fleet.yml` turns squash and rebase off |
| A setup script that exits non-zero | Sessions failed to start | End every setup script with `exit 0` ([cloud environments](machine.md#cloud-environments)) |
| A `startup\|resume` matcher | Forked sessions got no guidance | Use `startup\|resume\|fork` |
| A one-spelling entrypoint check | A cloud session reported itself as local and installed nothing | Test `CLAUDE_CODE_REMOTE`, not one entrypoint value |
| Secret-looking names in generated files | A name with "secret" in it next to a hash tripped the secret scanner on every push | Name things after their purpose, not "secret" or "token" |
| A reserved marketplace name | `agent-skills` is reserved, so the marketplace would not add | The registry's marketplace is `acsi-agentskills`; keep a name that is not reserved |
| Repository-wide secrets | Any workflow on any branch can read them, including one an agent just pushed | Keep the powerful App keys in environments limited to the default branch ([environments](settings-and-apps.md#environments)) |
| A GitHub 404 | Read as "it does not exist" | A 404 can mean "you may not see it"; check the repo with another credential before saying something is gone |
| `GIT_CONFIG_GLOBAL=/dev/null` for a git call run as root | A `git config --global` write renames over its target, so `/dev/null` became a regular file for every process on the host | Never let a git call that can write config run as root with `/dev/null` as its config; give it a scratch `HOME` and an empty scratch config file |
| Six fires in about ten seconds | Three never started; the workflow still said success | At most three in flight, a minute apart, each confirmed ([evals](evals-and-loop.md#the-eval-runner-and-its-lanes)) |
