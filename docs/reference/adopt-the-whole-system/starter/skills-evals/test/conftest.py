"""pytest hooks for `run_tests.py --jobs N --shard K/N` (ci.yml's matrix).

Loaded by every pytest-xdist worker, which is where collection happens, so
the shard filter has to live here: a plugin object passed to `pytest.main`
in the controller never reaches the workers. Without the option this file
does nothing, so `python3 -m pytest test/issues/<file>.py` is unchanged.
"""

from __future__ import annotations

from shard import assign, nodeid_key, parse_shard


def pytest_addoption(parser):
    parser.addoption("--skills-evals-shard", default=None, metavar="K/N",
                     help="run only the tests test/shard.py assigns to "
                          "shard K of N (set by run_tests.py --shard)")


def pytest_collection_modifyitems(config, items):
    value = config.getoption("--skills-evals-shard")
    if not value:
        return
    k, n = parse_shard(value)
    shards = assign([nodeid_key(item.nodeid) for item in items], n)
    keep, drop = [], []
    for item in items:
        (keep if shards[nodeid_key(item.nodeid)] == k else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
