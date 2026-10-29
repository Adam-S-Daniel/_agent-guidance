#!/usr/bin/env bash
# tests/run.sh: run the whole skills-evals suite (tests/ and
# scripts/test_dashboard_refresh.py, per pytest.ini) and exit with pytest's code.
# The suite spawns processes (fake claude, git, node), so on Linux it runs in
# its own PID namespace when unshare allows it: nothing it signals can reach a
# process outside. Extra arguments go to pytest.
# Needs: pip install -r requirements-dev.txt; node with the playwright package
# for tests/test_dashboard_page.py (skipped, with the reason, without them).
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # no __pycache__ in a tree that copy-starter.sh may copy
cd "$(dirname "${BASH_SOURCE[0]}")/.."

cmd=(python3 -m pytest -q -rs -p no:cacheprovider "$@")   # no .pytest_cache either
ns=(unshare --user --map-current-user --pid --fork --mount-proc --)
if [ ! -c /dev/null ]; then
  # The harness gives every git call and check its own scratch HOME and
  # global config file (harness/scorers/common.py scratch_git_env), because a
  # root `git config --global` aimed at /dev/null once replaced the device
  # with a regular file. If some other process has done that here, tools that
  # write to /dev/null leave junk in it and tests can fail in odd ways.
  echo "tests/run.sh: WARNING: /dev/null is not a character device on this host; tests may fail until it is restored (mknod -m 666 /dev/null c 1 3, as root)" >&2
fi
if command -v unshare >/dev/null 2>&1 && "${ns[@]}" true >/dev/null 2>&1; then
  cmd=("${ns[@]}" "${cmd[@]}")
else
  echo "tests/run.sh: no PID namespace available; running without one" >&2
fi
"${cmd[@]}"
