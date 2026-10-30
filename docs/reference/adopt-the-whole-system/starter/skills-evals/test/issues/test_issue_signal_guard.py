"""Every os.kill / os.killpg call site refuses an unsafe pid.

`killpg(1, sig)` is `kill(-1, sig)`: it signals every process the user owns.
A test that replaced `subprocess.Popen` with a bare MagicMock once ran cleanup
on `MagicMock().pid` (which indexes to 1) and killed the user's whole session
about fifteen times. Each signal site validates an integer pid greater than 1 and
refuses the caller's current pid and process group before signaling. This
module pins it two ways, with no real signal ever sent:

  * the walk FINDS every os.kill / os.killpg call under harness/ and scripts/
    by parsing each file, and the set must equal SIGNAL_SITES, so a new site
    fails until it is guarded and given a behavioral driver;
  * each registered site is driven with a mock proc and unsafe pids while
    os.kill and os.killpg are recorders, and neither may be called.
"""

from __future__ import annotations

import ast
import importlib
import os
import signal
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness"))
sys.path.insert(0, str(REPO / "scripts"))

SIGNAL_NAMES = frozenset({"kill", "killpg"})
UNSAFE_PIDS = (1, 0, -1, True)


def _module_for(rel: str):
    return importlib.import_module(
        {"harness/scorers/commands.py": "scorers.commands",
         "scripts/probe_model_defaults.py": "probe_model_defaults"}[rel])


class _SignalScan(ast.NodeVisitor):
    """The enclosing qualname of every os.kill / os.killpg call in one module,
    through `os.kill(...)`, `import os as x` and `from os import kill`."""

    def __init__(self, tree: ast.Module):
        self.os_names = {"os"}
        self.bare = set()
        self.stack: list[str] = []
        self.found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.os_names |= {a.asname or a.name for a in node.names
                                  if a.name == "os"}
            elif isinstance(node, ast.ImportFrom) and node.module == "os":
                self.bare |= {a.asname or a.name for a in node.names
                              if a.name in SIGNAL_NAMES}
        self.visit(tree)

    def _scope(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

    def visit_Call(self, node: ast.Call):
        func = node.func
        if ((isinstance(func, ast.Attribute) and func.attr in SIGNAL_NAMES
                and isinstance(func.value, ast.Name)
                and func.value.id in self.os_names)
                or (isinstance(func, ast.Name) and func.id in self.bare)):
            self.found.add(".".join(self.stack) or "<module>")
        self.generic_visit(node)


def _signal_sites() -> set:
    """Every (relative path, enclosing function qualname) that calls os.kill
    or os.killpg, from the TREE: every `*.py` under harness/ and scripts/."""
    out = set()
    for top in ("harness", "scripts"):
        for path in sorted((REPO / top).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            rel = path.relative_to(REPO).as_posix()
            out |= {(rel, name) for name in _SignalScan(tree).found}
    return out


def _drive_stop_process(proc):
    try:
        _module_for("harness/scorers/commands.py")._stop_process(proc)
    except ValueError:
        pass  # the refusal is the correct outcome


def _drive_signal_group(proc):
    _module_for("scripts/probe_model_defaults.py")._signal_group(proc, signal.SIGTERM)


def _drive_stop(proc):
    _module_for("scripts/probe_model_defaults.py")._stop(proc)


class TestSignalGuard(unittest.TestCase):
    #: (path, function) -> a callable that takes a proc and reaches the site.
    SIGNAL_SITES = {
        ("harness/scorers/commands.py", "_stop_process"): _drive_stop_process,
        ("scripts/probe_model_defaults.py", "_signal_group"): _drive_signal_group,
        ("scripts/probe_model_defaults.py", "_stop"): _drive_stop,
    }

    def test_the_walk_is_not_vacuous(self):
        self.assertGreaterEqual(len(_signal_sites()), 2)

    def test_every_signal_site_is_registered(self):
        found = _signal_sites()
        unregistered = sorted(found - set(self.SIGNAL_SITES))
        self.assertFalse(
            unregistered,
            f"os.kill/os.killpg called at {unregistered}: require an integer pid > 1 "
            "that differs from os.getpid() and os.getpgrp() "
            "(killpg(1, sig) is kill(-1, sig)) "
            "and register it in SIGNAL_SITES with a behavioral driver.")
        stale = sorted(set(self.SIGNAL_SITES) - found)
        self.assertFalse(stale, f"SIGNAL_SITES names sites the walk no longer finds: {stale}")

    def test_no_registered_site_signals_a_mock_or_unsafe_pid(self):
        procs = [("MagicMock()", mock.MagicMock)]
        for (rel, name), drive in sorted(self.SIGNAL_SITES.items()):
            unsafe_pids = (*UNSAFE_PIDS, os.getpid(), os.getpgrp())
            site_procs = procs + [
                (f"pid={pid!r}", lambda pid=pid: mock.MagicMock(pid=pid))
                for pid in unsafe_pids]
            for label, make in site_procs:
                for running in (True, False):
                    with self.subTest(site=f"{rel}::{name}", proc=label,
                                      running=running), \
                            mock.patch("os.killpg") as killpg, \
                            mock.patch("os.kill") as kill:
                        proc = make()
                        proc.poll.return_value = None if running else 0
                        drive(proc)
                        killpg.assert_not_called()
                        kill.assert_not_called()

    def test_each_site_refuses_current_pid_and_group_at_call_time(self):
        for (rel, name), drive in sorted(self.SIGNAL_SITES.items()):
            module = _module_for(rel)
            for identity, ids in (("pid", (1101, 1102)),
                                  ("group", (2201, 2202))):
                with self.subTest(site=f"{rel}::{name}", identity=identity), \
                        mock.patch.object(module.os, "getpid", side_effect=[1101, 1102]), \
                        mock.patch.object(module.os, "getpgrp", side_effect=[2201, 2202]), \
                        mock.patch("os.killpg") as killpg, \
                        mock.patch("os.kill") as kill:
                    for current_id in ids:
                        proc = mock.Mock(pid=current_id)
                        proc.poll.return_value = 0
                        drive(proc)
                    killpg.assert_not_called()
                    kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
