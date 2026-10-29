# Scheduled task: dashboard refresh

Schedule: every 2 hours, bound to the dashboard's project thread (so it can
post there and read project files). Prompt:

> Dashboard refresh, timed run. Follow `dashboard/README.md` ("Refresh") in
> skills-evals at origin/main to refresh the snapshot datasets of
> <dashboard artifact URL>. Use the config in <private config path>. Upload
> only changed files. Reply in the thread only if something needs the owner;
> otherwise update the checklist quietly.

The same steps run at once when a merge relay arrives (`merge-relay.md`). Skip
the run only when a refresh in the last 5 minutes already included that merge.
