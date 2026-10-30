# Real-work fixtures

Each directory here is one fixture mined from a merged pull request in your
fleet: the repository's tree before the fix as a seed, the pull request's own
tests as a hidden checker, and the closing issue as the prompt. The fixture
supply routine (`.github/workflows/routine-fixture-supply.yml`) finds the
candidates, the eval runner routine scaffolds each one on a
`claude/scaffold-<fixture id>` branch, and `routine-scaffold-gate.yml` opens
the pull request that adds it here. A draft with two clean ingested runs is
promoted onto `evals/scheduled.yml`.

The adoption kit ships this directory empty: the source repository's
fixtures were snapshots of its owner's own repositories at pinned commits.
`scripts/scaffold_real_work.py` documents the layout, and
`test/fixtures/evals/real-work/` holds the invented example the tests use.
