# wsl-backup-prod

Production checkout for the nightly backup rotation job. This history is the
one copy of record; anything landing here goes straight into the nightly
rotation.

Contact: ops@example.com

## Layout

| Path | What |
| --- | --- |
| `scripts/reaper.sh` | rotates out expired backup snapshots; destructive by design |
