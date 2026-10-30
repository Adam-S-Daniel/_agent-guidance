"""run_suites.py's known-failure matching, and the list it is given.

A known failure is excused only by its exact id, and only when the listed
ids account for the whole failure; these tests hold both halves, and hold
starter/known-failures.txt to the format and to trees that exist.

Run: python3 -m pytest KIT/export/tests -q
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
EXPORT_DIR = HERE.parent
KIT = EXPORT_DIR.parent

_spec = importlib.util.spec_from_file_location("kit_run_suites", EXPORT_DIR / "run_suites.py")
run_suites = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("kit_run_suites", run_suites)
_spec.loader.exec_module(run_suites)

PYTEST_OUT = """\
FAILED test/a.py::T::test_one - AssertionError: 1 != 2
SUBFAILED(case="'x y'") test/a.py::T::test_two - AssertionError
ERROR test/b.py::test_setup
SKIPPED [1] test/c.py:3: needs bwrap
====== 2 failed, 10 passed, 1 skipped, 1 error in 3.21s ======
"""
GROUP_OUT = """\
      codex_cloud        - passed,   - failed, exit 1, 63s
      FAILED GROUPS: codex_cloud
      Results: 2212 passed, 1 failed
"""
UNITTEST_GROUP_OUT = """\
test_ok (test_m.T.test_ok) ... ok
======================================================================
FAIL: test_a (test_m.T.test_a) (kind='unreadable')
----------------------------------------------------------------------
AssertionError: 1 != 0
======================================================================
FAIL: test_b (test_m.T.test_b)
----------------------------------------------------------------------
AssertionError
----------------------------------------------------------------------
Ran 90 tests in 45.213s

FAILED (failures=2, skipped=1)
  Groups (TEST_JOBS=4):
  codex_cloud        - passed,   - failed, exit 1, 45s (+1 failed: exited 1 without reporting a failed assertion)
  FAILED GROUPS: codex_cloud
  Results: 0 passed, 1 failed
"""
UNITTEST_IDS = {"FAIL: test_m.T.test_a (kind='unreadable')", "FAIL: test_m.T.test_b"}
IDS = {"FAILED test/a.py::T::test_one", "SUBFAILED(case=\"'x y'\") test/a.py::T::test_two", "ERROR test/b.py::test_setup"}


def test_pytest_ids_drop_the_reason_and_keep_subtest_parameters():
    ids, problem = run_suites.failure_ids(PYTEST_OUT)
    assert ids == IDS
    assert problem is None


def test_group_ids_come_from_the_failed_groups_line():
    assert run_suites.failure_ids(GROUP_OUT) == ({"group:codex_cloud"}, None)


def test_an_exit_only_group_is_named_by_its_unittest_failures():
    assert run_suites.failure_ids(UNITTEST_GROUP_OUT) == (UNITTEST_IDS, None)
    ok, lines = run_suites.judge(UNITTEST_GROUP_OUT, {i: 1 for i in UNITTEST_IDS})
    assert ok, lines


def test_a_new_unittest_failure_in_an_excused_group_is_not_hidden():
    ok, lines = run_suites.judge(UNITTEST_GROUP_OUT, {"FAIL: test_m.T.test_a (kind='unreadable')": 1})
    assert not ok
    assert "NEW FAILURE (not in the known list): FAIL: test_m.T.test_b" in lines


def test_a_group_with_a_failed_assertion_is_still_named_as_a_group():
    out = UNITTEST_GROUP_OUT.replace(
        "  codex_cloud        - passed,   - failed, exit 1, 45s (+1 failed: exited 1 without reporting a failed assertion)",
        "  codex_cloud        3 passed,   1 failed, exit 1, 45s").replace("  Results: 0 passed", "  FAIL: a shell check\n  Results: 3 passed")
    ok, lines = run_suites.judge(out, {i: 1 for i in UNITTEST_IDS})
    assert not ok
    assert "NEW FAILURE (not in the known list): group:codex_cloud" in lines


def test_unittest_headers_must_account_for_its_count():
    out = UNITTEST_GROUP_OUT.replace("FAILED (failures=2, skipped=1)", "FAILED (failures=2, errors=1, skipped=1)")
    ok, lines = run_suites.judge(out, {i: 1 for i in UNITTEST_IDS})
    assert not ok
    assert any("unittest reports 3 failures and errors, but 2" in line for line in lines)


def test_all_known_failures_are_excused_and_named():
    ok, lines = run_suites.judge(PYTEST_OUT, {i: 1 for i in IDS})
    assert ok
    assert sum(line.startswith("known failure (excused): ") for line in lines) == 3


def test_one_new_failure_is_not_excused():
    known = {i: 1 for i in IDS if "test_one" not in i}
    ok, lines = run_suites.judge(PYTEST_OUT, known)
    assert not ok
    assert "NEW FAILURE (not in the known list): FAILED test/a.py::T::test_one" in lines


def test_a_failure_that_names_nothing_is_never_excused():
    ok, lines = run_suites.judge("Traceback (most recent call last):\nboom\n", {"group:codex_cloud": 1})
    assert not ok
    assert any(line.startswith("NOT EXCUSED: ") for line in lines)


def test_ids_that_do_not_account_for_the_pytest_count_are_not_excused():
    # A failure line the parser cannot read (here, cut from the log) must not
    # let the rest pass as the whole failure.
    out = PYTEST_OUT.replace("ERROR test/b.py::test_setup\n", "")
    ok, lines = run_suites.judge(out, {i: 1 for i in IDS})
    assert not ok
    assert any("pytest reports 3 failed or errored, but 2" in line for line in lines)


def test_a_listed_id_that_did_not_fail_is_reported_not_fatal():
    ok, lines = run_suites.judge(GROUP_OUT, {"group:codex_cloud": 1, "group:sync": 2})
    assert ok
    assert "listed as known but did not fail: group:sync" in lines


def test_load_known_refuses_a_duplicate_and_a_bare_line(tmp_path):
    p = tmp_path / "k.txt"
    p.write_text("# c\nt FAILED x::y\nt FAILED x::y\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="listed twice"):
        run_suites.load_known(p)
    p.write_text("lonely\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="want '<tree> <failure id>'"):
        run_suites.load_known(p)


def test_the_kits_known_failures_name_real_trees_with_well_formed_ids():
    known = run_suites.load_known(KIT / "starter" / "known-failures.txt")
    trees = {m["kit_name"] for m in run_suites.export.load_manifests().values()}
    assert set(known) <= trees
    for tree, ids in known.items():
        for i in ids:
            assert (i.startswith("group:") or run_suites.PYTEST_ID.fullmatch(i)
                    or re.fullmatch(r"(?:FAIL|ERROR): [\w.]+(?: \(.*\))?", i)), (tree, i)
