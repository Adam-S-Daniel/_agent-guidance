#!/usr/bin/env python3
"""Is MODEL the `id` of an arm in the committed model roster? (#68, #372 U4)

    roster_arm.py MODEL [--roster PATH]     exit 0: an arm; exit 1: not one

`routine-eval-fire.yml` runs this in its validation step, before the fire
bearer exists, so a `model` input the sweep could not run never starts a
routine session. The roster is `evals/roster.yml` in the checkout (the default
branch's copy, trusted because `main` is pull-request-only; ADR 0001), read
with the harness's own reader and arm extraction, so this agrees with what
`run_eval.select_models` would accept for `--model`: a parsed `arms[].id`,
never a substring of the file. The judge and the preflight model are not arms.

A refusal prints one fixed line to stderr and never the input: the workflow's
log is public. MODEL is always the first argument and is read literally, never
as an option: `-h` is a model that is not an arm. `--roster` points at another
file for tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import run_eval  # noqa: E402

REFUSAL = "input 'model' is not an arm of the committed evals/roster.yml"


def is_arm(model: str, roster_path: Path) -> bool:
    roster, problem = run_eval.read_roster(roster_path)
    if problem:
        return False
    arm_ids, _judge, _is_arm, _skipped = run_eval.roster_models(roster)
    return model in arm_ids


def main(argv: list[str] | None = None) -> int:
    # No option parser: one would read a MODEL of `-h` as a request for help
    # and exit 0, which the workflow takes for "an arm". The first argument is
    # the model, whatever it looks like; any other argument list is refused.
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 1:
        roster_path = run_eval.TRUSTED_ROSTER
    elif len(argv) == 3 and argv[1] == "--roster":
        roster_path = Path(argv[2])
    else:
        print(REFUSAL, file=sys.stderr)
        return 1
    if is_arm(argv[0], roster_path):
        return 0
    print(REFUSAL, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
