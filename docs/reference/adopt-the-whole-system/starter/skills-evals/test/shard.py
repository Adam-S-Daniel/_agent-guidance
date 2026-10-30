"""Which CI shard each test runs in.

ci.yml splits the suite across parallel jobs with
`python3 test/run_tests.py --jobs auto --shard K/N`. The runner (to build the
shard's expected set) and test/conftest.py (to deselect everything else in
each pytest-xdist worker) both call `assign` on the same full set of test
keys, so the two can never disagree; if their sets ever differ, the runner's
collection cross-check fails the shard.

The split balances by measured time, not by count: a few tests take 60-90
seconds while the median takes 0.06, so a hash split put 789 seconds of work
in one of four shards and 556 in another (2026-10-26). `assign` is greedy
longest-first over shard_weights.json, which lists only the tests that took
5 seconds or more; every other test counts as DEFAULT_SECONDS. A stale
weights file costs balance, never correctness. Refresh it from a junit
report of a full run:

    python3 -m pytest test/run_tests.py test/issues -n 4 -p no:cacheprovider \\
        --junitxml=/tmp/suite.xml
    python3 test/shard.py --refresh /tmp/suite.xml
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

WEIGHTS = Path(__file__).resolve().parent / "shard_weights.json"
#: The mean time of a test under the 5-second cut (2026-10-26 local run).
DEFAULT_SECONDS = 0.3
WEIGHT_FLOOR_SECONDS = 5.0


def parse_shard(value: str) -> tuple[int, int]:
    """`"K/N"` as `(K, N)`, 1 <= K <= N; ValueError otherwise."""
    index, sep, total = value.partition("/")
    if not sep:
        raise ValueError(f"shard {value!r} is not K/N")
    k, n = int(index), int(total)
    if n < 1 or not 1 <= k <= n:
        raise ValueError(f"shard {value!r} needs 1 <= K <= N")
    return k, n


def shard_key(module: str, cls: str, method: str) -> str:
    return f"{module}::{cls}::{method}"


def nodeid_key(nodeid: str) -> str:
    """The `shard_key` of a pytest nodeid (`path::Class::method`)."""
    path_part, cls, method = nodeid.split("::")
    return shard_key(Path(path_part).stem, cls, method)


def load_weights(path: Path = WEIGHTS) -> dict[str, float]:
    return json.loads(path.read_text(encoding="utf-8"))


def assign(keys, total: int, weights: dict[str, float] | None = None
           ) -> dict[str, int]:
    """Each key's 1-based shard out of `total`: longest first, each to the
    shard with the least estimated time so far, ties to the lower shard and
    then the lower key, so the result depends only on the inputs."""
    if weights is None:
        weights = load_weights()
    loads = [0.0] * total
    shards = {}
    for key in sorted(set(keys), key=lambda k: (-weights.get(k, DEFAULT_SECONDS), k)):
        index = min(range(total), key=lambda i: (loads[i], i))
        loads[index] += weights.get(key, DEFAULT_SECONDS)
        shards[key] = index + 1
    return shards


def weights_from_junit(report: Path) -> dict[str, float]:
    """`{key: seconds}` for every test in a pytest junit report that took
    WEIGHT_FLOOR_SECONDS or more. junit's classname is the dotted module
    path plus the class (`test.issues.test_issue_97.TestIssue97`)."""
    weights = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        seconds = float(case.get("time") or 0)
        if seconds < WEIGHT_FLOOR_SECONDS:
            continue
        module, _, cls = case.get("classname", "").rpartition(".")
        key = shard_key(module.rpartition(".")[2], cls, case.get("name", ""))
        weights[key] = round(seconds, 1)
    return weights


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--refresh":
        sys.exit("usage: shard.py --refresh <junit.xml>")
    found = weights_from_junit(Path(sys.argv[2]))
    WEIGHTS.write_text(json.dumps(dict(sorted(found.items())), indent=2) + "\n",
                       encoding="utf-8")
    print(f"wrote {len(found)} weights to {WEIGHTS}")
