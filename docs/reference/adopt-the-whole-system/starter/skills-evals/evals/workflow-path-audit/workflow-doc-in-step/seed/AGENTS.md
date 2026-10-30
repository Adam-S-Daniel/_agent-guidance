# AGENTS.md

Notes for anyone, human or agent, changing this repository.

## Running locally

- `npm ci`, then `npm test` runs the unit tests.
- `pip install mkdocs && mkdocs serve` previews the docs site.

## CI workflows

| Workflow | Triggers | Salient paths |
|---|---|---|
| `tests.yml` | pull_request, push to main | all paths |
| `docs-site.yml` | pull_request, push to main | all paths |
| `nightly-sweep.yml` | schedule (03:00 UTC), manual dispatch | n/a: schedule only |

## Conventions

- Commit messages explain why, not what.
- Keep `src/` free of runtime dependencies.
