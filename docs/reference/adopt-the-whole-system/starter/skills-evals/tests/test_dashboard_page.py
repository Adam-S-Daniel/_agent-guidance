"""dashboard/index.html against the datasets scripts/dashboard-refresh.py writes.

The refresh runs on scripts/fixtures/dashboard/results through
scripts/build-dashboard-site.sh, then tests/dashboard_page.js loads the page in
headless Chromium (Playwright for node, no server, no network) and reports what
it rendered. Skipped, with the reason, when node or its playwright package is
missing."""
import json
import os
import shutil
import subprocess

import pytest

from conftest import ROOT

DRIVER = ROOT / "tests" / "dashboard_page.js"
FIXTURE_RESULTS = ROOT / "scripts" / "fixtures" / "dashboard" / "results"
PAGE_DIR = ROOT / "dashboard"


def node_env():
    env = dict(os.environ)
    node = shutil.which("node")
    npm = shutil.which("npm")
    if npm and not env.get("NODE_PATH"):
        root = subprocess.run([npm, "root", "-g"], capture_output=True, text=True).stdout.strip()
        env["NODE_PATH"] = root
    return node, env


def playwright_or_skip():
    node, env = node_env()
    if not node:
        pytest.skip("node is not installed")
    probe = subprocess.run([node, "-e", "require('playwright')"], env=env, capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("node cannot require('playwright')")
    return node, env


def build(tmp_path, results=FIXTURE_RESULTS):
    data = tmp_path / "data"
    r = subprocess.run(["bash", str(ROOT / "scripts" / "build-dashboard-site.sh"), str(results)],
                       env={**os.environ, "DASHBOARD_OUT": str(data)}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return data


def render(data):
    node, env = playwright_or_skip()
    r = subprocess.run([node, str(DRIVER), str(PAGE_DIR), str(data)], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def pct(x):
    return f"{x * 100:.1f}%"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    data = build(tmp_path_factory.mktemp("site"))
    return data, render(data)


def test_build_script_writes_every_dataset_and_says_how_to_serve(tmp_path):
    r = subprocess.run(["bash", str(ROOT / "scripts" / "build-dashboard-site.sh"), str(FIXTURE_RESULTS)],
                       env={**os.environ, "DASHBOARD_OUT": str(tmp_path / "d")}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert sorted(os.listdir(tmp_path / "d")) == ["allowance.json", "gates.json", "manifest.json", "runs.json"]
    assert "Datasets written to" in r.stdout
    bad = subprocess.run(["bash", str(ROOT / "scripts" / "build-dashboard-site.sh")], capture_output=True, text=True)
    assert bad.returncode == 2


def test_every_dataset_the_page_fetches_is_one_the_refresh_writes(built):
    data, out = built
    written = set(os.listdir(data))
    fetched = {r[len("data/"):] for r in out["requests"] if r.startswith("data/")}
    assert fetched, out["requests"]
    assert fetched <= written, fetched - written
    # and the page shows every dataset but the manifest, which is bookkeeping
    assert fetched == written - {"manifest.json"}
    assert out["external"] == [], out["external"]
    assert out["errors"] == [], out["errors"]


def test_runs_per_fixture_and_model_show_each_arm(built):
    data, out = built
    runs = json.loads((data / "runs.json").read_text())
    rows = out["dom"]["runs"]
    assert [(r["fixture"], r["model"]) for r in rows] == [(c["fixture"], c["model"]) for c in runs["cells"]]
    for row, cell in zip(rows, runs["cells"]):
        for arm in ("with_skill", "without_skill", "bare"):
            shown = row["arms"][arm]
            if arm in cell["arms"]:
                assert shown["pass_rate"] == cell["arms"][arm]["pass_rate"]
                assert pct(cell["arms"][arm]["pass_rate"]) in shown["text"]
                if cell["arms"][arm]["judge"] is not None:
                    assert f"judge {cell['arms'][arm]['judge']:.1f} / 10" in shown["text"]
            else:
                assert shown["pass_rate"] is None and shown["text"] == "—"
    for arm, value in runs["overall_pass_rate"].items():
        assert out["dom"]["overall"][arm] == (pct(value) if value is not None else "—")
    assert "1 run directory failed validation and was skipped." in out["dom"]["status"]["runs"]["text"]


def test_a_cell_with_all_three_arms_and_an_errored_trial(built):
    _, out = built
    row = next(r for r in out["dom"]["runs"]
               if r["fixture"] == "evals/workflow-path-audit/ci-skips-docs" and r["model"] == "claude-sonnet-5-5")
    assert row["arms"]["bare"]["pass_rate"] == 0.2
    assert "1 errored trial" in row["arms"]["with_skill"]["text"]
    assert "+20.0 pts" in row["text"]


def test_allowance_used_per_week(built):
    data, out = built
    allowance = json.loads((data / "allowance.json").read_text())
    weeks = out["dom"]["weeks"]
    assert [w["week"] for w in weeks] == [w["week_resets_at"] for w in allowance["weeks"]]
    for shown, w in zip(weeks, allowance["weeks"]):
        assert shown["used"] == w["used"] and pct(w["fraction_of_cap"]) in shown["text"]
        assert shown["over"] == w["over_cap"]
    assert "Weekly cap: 30.0%" in out["dom"]["status"]["allowance"]["text"]


def test_gate_status(built):
    data, out = built
    gates = json.loads((data / "gates.json").read_text())
    for cls, n in gates["cell_classes"].items():
        assert out["dom"]["classes"][cls] == f"{cls}: {n}"
    assert [(s["skill"], s["verdict"]) for s in out["dom"]["skills"]] == \
        [(s["skill"], s["verdict"]) for s in gates["skills"]]


def test_a_missing_dataset_is_reported_and_the_rest_still_render(tmp_path):
    data = build(tmp_path)
    (data / "gates.json").unlink()
    out = render(data)
    assert out["dom"]["status"]["gates"]["failed"] is True
    assert "gates.json" in out["dom"]["status"]["gates"]["text"]
    assert out["dom"]["runs"] and out["dom"]["weeks"]


def test_no_results_yet_renders_empty_states(tmp_path):
    empty = tmp_path / "results"
    empty.mkdir()
    out = render(build(tmp_path, empty))
    assert out["dom"]["runs"] == [] and out["dom"]["status"]["runs"]["text"] == "No runs yet."
    assert "No gate status yet" in out["dom"]["status"]["gates"]["text"]
    assert out["errors"] == []


def test_the_page_is_self_contained():
    html = (PAGE_DIR / "index.html").read_text()
    assert "<script src" not in html and "<link" not in html
    assert "innerHTML" not in html     # dataset values are written as text only
