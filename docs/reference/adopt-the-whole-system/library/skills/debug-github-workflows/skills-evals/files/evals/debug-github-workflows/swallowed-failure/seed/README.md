# Example labels

Keeps the shared issue labels in `labels.txt` on every repository in the
example-org organization. The nightly `Sync labels` workflow runs
`scripts/sync-labels.sh`, which creates each label or updates it in place.

Add a label by adding a line to `labels.txt`; the next nightly run picks it up.
