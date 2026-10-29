import re

import pytest
import yaml

from conftest import ROOT

WF = ROOT / ".github" / "workflows"
NAMES = ["routine-eval-fire.yml", "routine-eval-results-pushed.yml", "routine-eval-ingest.yml"]
SHA = re.compile(r"^[A-Za-z0-9._/-]+@[0-9a-f]{40}$")


def load(name):
    doc = yaml.safe_load((WF / name).read_text())
    doc["on"] = doc.pop(True, doc.get("on"))      # PyYAML reads the key `on` as True
    return doc


def steps(doc):
    return [s for job in doc["jobs"].values() for s in job["steps"]]


@pytest.mark.parametrize("name", NAMES)
def test_every_uses_is_pinned_to_a_full_sha_with_no_version_comment(name):
    for s in steps(load(name)):
        if "uses" in s:
            assert SHA.match(s["uses"]), s["uses"]
    for line in (WF / name).read_text().splitlines():
        if "uses:" in line:
            assert "#" not in line, line


@pytest.mark.parametrize("name", NAMES)
def test_no_expression_from_inputs_or_the_event_inside_a_run_block(name):
    for s in steps(load(name)):
        run = s.get("run", "")
        assert "${{" not in run or not re.search(r"\$\{\{\s*(inputs|github\.event)\.", run), run


def test_fire_workflow_shape():
    doc = load("routine-eval-fire.yml")
    assert list(doc["on"]) == ["workflow_dispatch"]
    job = doc["jobs"]["fire"]
    assert job["environment"] == "routine-fire"
    assert doc["run-name"] == "fire ${{ inputs.mode }} ${{ inputs.run_id }}"
    assert doc["on"]["workflow_dispatch"]["inputs"]["mode"]["options"] == ["eval", "improve", "scaffold", "draft"]
    names = [s.get("name") for s in job["steps"]]
    assert names.index("Validate inputs") < names.index("Allowance guard") < names.index("Fire the routine")
    # the bearer is in exactly one step's env, and it is the fire step
    holders = [s["name"] for s in job["steps"] if "EVAL_ROUTINE_FIRE_BEARER" in str(s.get("env", ""))]
    assert holders == ["Fire the routine"]
    validate = next(s for s in job["steps"] if s.get("name") == "Validate inputs")
    assert "fire_inputs.py" in validate["run"] and "$GITHUB_EVENT_PATH" in validate["run"]
    fire = next(s for s in job["steps"] if s.get("name") == "Fire the routine")["run"]
    assert "--header @-" in fire and "--data-binary" in fire and "claude_code_session_id" in fire
    assert fire.count("curl ") == 1 and not re.search(r"\b(for|while|until)\b", fire.replace("for a retry", ""))


def test_pushed_workflow_holds_no_permissions_and_runs_nothing_from_the_branch():
    doc = load("routine-eval-results-pushed.yml")
    assert doc["permissions"] == {}
    assert doc["on"]["push"] == {"branches": ["claude/eval-results-*"], "paths": ["eval-results/**"]}
    for s in steps(doc):
        assert "uses" not in s and set(s) <= {"run", "name"}
        assert s["run"].startswith("echo ")


def test_ingest_workflow_runs_default_branch_code_and_writes_with_the_app_token():
    doc = load("routine-eval-ingest.yml")
    assert list(doc["on"]) == ["workflow_run"]
    assert doc["on"]["workflow_run"]["workflows"] == ["Eval results pushed"]
    job = doc["jobs"]["ingest"]
    assert job["environment"] == "loop-writer"
    assert doc["permissions"] == {"contents": "read"}
    text = (WF / "routine-eval-ingest.yml").read_text()
    # the branch is checked out as data, to a separate path, without credentials
    branch_co = [s for s in job["steps"] if s.get("with", {}).get("path") == "branch"]
    assert len(branch_co) == 1 and branch_co[0]["with"]["persist-credentials"] is False
    # the branch name is regex-checked in the first step, before any checkout
    assert "grep -Eqx" in job["steps"][0]["run"]
    # nothing executes a file under branch/
    assert not re.search(r"(python3?|bash|sh|node)\s+(\./)?branch/", text)
    # the App token is minted right before the write, then used only there
    names = [s.get("id") or s.get("name") for s in job["steps"]]
    assert names.index("app") + 1 == names.index("Write routine-results to persistent/eval-results")
    assert "routine-results" in text and "merge-base --is-ancestor" in text


PLACEHOLDER = "<sha-of-the-v-tag-you-chose>"   # the guide's stand-in; replace before use


def test_every_workflow_pins_a_full_sha_or_the_guide_placeholder():
    names = sorted(p.name for p in WF.glob("*.yml"))
    assert set(NAMES) <= set(names)
    for name in names:
        for s in steps(load(name)):
            if "uses" in s:
                ref = s["uses"]
                assert SHA.match(ref) or ref.endswith("@" + PLACEHOLDER), (name, ref)


# --- routine prompts name exactly the branches the workflows listen on -------

PROMPTS = [ROOT / "docs" / "routine-prompt.md", ROOT / "docs" / "routine-prompts.md"]
# Lanes whose prompts ship before their pushed workflow does. When one of
# these gets its workflow, the workflow's prefix must equal this one.
PLANNED = {}


def pushed_prefixes():
    """{lane: prefix} from every workflow's on.push.branches."""
    found = {}
    for path in sorted(WF.glob("*.yml")):
        doc = load(path.name)
        push = doc["on"].get("push") if isinstance(doc["on"], dict) else None
        for b in (push or {}).get("branches", []):
            if b.startswith("claude/"):
                assert b.endswith("-*"), (path.name, b)
                prefix = b[:-1]
                lane = prefix[len("claude/eval-"):-1]
                assert found.setdefault(lane, prefix) == prefix
    return found


def prompt_prefixes(text):
    names = set(re.findall(r"claude/eval-[a-z]+-", text))
    bare = re.findall(r"claude/eval-(?![a-z]+-)", text)
    assert not bare, "a branch name without its lane (claude/eval-<run id>)"
    return names


def test_every_routine_prompt_names_only_the_workflow_branch_prefixes():
    pushed = pushed_prefixes()
    assert pushed == {"results": "claude/eval-results-", "improve": "claude/eval-improve-",
                      "scaffold": "claude/eval-scaffold-", "draft": "claude/eval-draft-"}
    allowed = set(pushed.values()) | {p for lane, p in PLANNED.items() if lane not in pushed}
    named = set()
    for path in PROMPTS:
        found = prompt_prefixes(path.read_text())
        assert found, path.name
        assert found <= allowed, (path.name, found - allowed)
        named |= found
    assert named == allowed, allowed - named


def test_the_gate_scripts_accept_the_workflow_branch_prefixes():
    import improve_gate
    import ingest_validate
    pushed = pushed_prefixes()
    run = "20261028T120000Z-1a2b3c"
    assert improve_gate.BRANCH_RE.fullmatch(pushed["improve"] + run)
    assert not improve_gate.BRANCH_RE.fullmatch(pushed["results"] + run)
    assert ingest_validate.BRANCH.match(pushed["results"] + run)
    assert not ingest_validate.BRANCH.match(pushed["improve"] + run)
    import draft_gate
    import scaffold_gate
    for lane, rx in (("scaffold", scaffold_gate.BRANCH_RE), ("draft", draft_gate.BRANCH_RE)):
        assert rx.fullmatch(pushed[lane] + run)
        assert not any(rx.fullmatch(p + run) for k, p in pushed.items() if k != lane)


# --- dispatch_fires.py sends exactly the fire workflow's inputs ---------------

def test_dispatch_fires_builds_exactly_the_fire_workflows_inputs():
    import dispatch_fires as D
    import fire_inputs as fi
    from conftest import FIXTURE_REPO, RUN_ID
    spec = load("routine-eval-fire.yml")["on"]["workflow_dispatch"]["inputs"]
    required = {k for k, v in spec.items() if v.get("required")}
    assert set(spec) == fi.KNOWN
    assert spec["mode"]["options"] == list(fi.MODES)
    fires = [
        {"mode": "eval", "skill": "workflow-path-audit", "fixture": "basic",
         "model": "claude-sonnet-5-5", "arms": "both", "trials": 3},
        {"mode": "eval", "skill": "workflow-path-audit", "fixture": "basic", "model": "",
         "arms": "bare", "trials": 1},
        {"mode": "improve", "skill": "workflow-path-audit"},
        {"mode": "scaffold", "repo": "acme-example/app", "number": 12},
        {"mode": "draft", "lesson_id": "ab" * 32},
    ]
    seen_modes = set()
    for f in fires:
        sent = D.inputs_for(f, RUN_ID)
        assert set(sent) <= set(spec), set(sent) - set(spec)
        assert required <= set(sent)
        assert all(isinstance(v, str) for v in sent.values())
        for k, v in sent.items():
            if spec[k].get("type") == "choice":
                assert v in spec[k]["options"], (k, v)
        # What GitHub hands the fire workflow: the sent inputs over the defaults.
        event = {"inputs": {k: v.get("default", "") for k, v in spec.items()} | sent}
        payload = fi.build_payload(event, ROOT / "evals" / "roster.yml", FIXTURE_REPO)
        assert payload["mode"] == f["mode"] and payload["run_id"] == RUN_ID
        seen_modes.add(payload["mode"])
    assert seen_modes == set(fi.MODES)


# --- every workflow: the kit's style rules ------------------------------------

ALL = sorted(p.name for p in WF.glob("*.yml"))
# The most each workflow may ask of GITHUB_TOKEN (least privilege).
MAX_PERMISSIONS = {
    "routine-eval-fire.yml": {"contents": "read", "actions": "read"},
    "routine-eval-weekly.yml": {"contents": "read", "actions": "write"},
    "fixture-supply.yml": {"contents": "read", "actions": "write"},
    "routine-draft-skill.yml": {"contents": "read", "actions": "write"},
    "scaffold-disarm.yml": {"pull-requests": "write"},
}
PUSHED = {"routine-eval-results-pushed.yml", "routine-improve-pushed.yml",
          "routine-scaffold-pushed.yml", "routine-draft-pushed.yml"}
GATES = {"routine-eval-ingest.yml": "Eval results pushed", "routine-improve-gate.yml": "routine-improve-pushed",
         "routine-scaffold-gate.yml": "routine-scaffold-pushed", "routine-draft-gate.yml": "routine-draft-pushed"}


def all_permissions(doc):
    yield doc.get("permissions")
    for job in doc["jobs"].values():
        if "permissions" in job:
            yield job["permissions"]


@pytest.mark.parametrize("name", ALL)
def test_no_expression_at_all_inside_a_run_block(name):
    for s in steps(load(name)):
        assert "${{" not in s.get("run", ""), (name, s.get("name"))


@pytest.mark.parametrize("name", ALL)
def test_least_privilege(name):
    allowed = MAX_PERMISSIONS.get(name, {"contents": "read"})
    doc = load(name)
    assert isinstance(doc.get("permissions"), dict), name        # never the default token scope
    for perms in all_permissions(doc):
        for k, v in perms.items():
            if (k, v) == ("issues", "write") and name == "compute-gates.yml":
                continue                                          # its fallback issue, one job
            assert allowed.get(k) == v or (v == "read" and allowed.get(k) == "write"), (name, k, v)


@pytest.mark.parametrize("name", ALL)
def test_an_app_token_is_used_only_by_the_step_right_after_its_mint(name):
    for job in load(name)["jobs"].values():
        st = job.get("steps", [])
        for i, s in enumerate(st):
            if s.get("uses", "").startswith("actions/create-github-app-token@"):
                ref = f"steps.{s['id']}.outputs.token"
                users = [j for j, t in enumerate(st) if ref in yaml.safe_dump(t)]
                assert users == [i + 1], (name, s["id"], users)
                assert "repositories" in s["with"], (name, "a token for named repos only")


@pytest.mark.parametrize("name", sorted(PUSHED))
def test_pushed_workflows_hold_nothing(name):
    doc = load(name)
    assert doc["permissions"] == {} and list(doc["on"]) == ["push"]
    (branch,) = doc["on"]["push"]["branches"]
    lane = branch[len("claude/eval-"):-2]
    assert doc["on"]["push"]["paths"] == [f"eval-{lane}/**"]
    for s in steps(doc):
        assert set(s) <= {"run", "name"} and s["run"].startswith("echo ")


@pytest.mark.parametrize("name", sorted(GATES))
def test_gates_follow_their_pushed_workflow_and_run_default_branch_code(name):
    doc = load(name)
    assert list(doc["on"]) == ["workflow_run"]
    assert doc["on"]["workflow_run"]["workflows"] == [GATES[name]]
    first = next(iter(doc["jobs"].values()))
    assert "head_repository.full_name == github.repository" in first["if"]
    assert "conclusion == 'success'" in first["if"]
    text = (WF / name).read_text()
    assert not re.search(r"(python3?|bash|sh|node)\s+(\./)?branch/", text)


def test_weekly_sweep_shape():
    doc = load("routine-eval-weekly.yml")
    assert set(doc["on"]) == {"schedule", "workflow_dispatch"}
    st = doc["jobs"]["sweep"]["steps"]
    names = [s.get("name") or s.get("uses", "").split("@")[0] for s in st]
    order = ["Allowance room", "State and plan", "actions/upload-artifact", "Dispatch the fires"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    text = (WF / "routine-eval-weekly.yml").read_text()
    for needle in ("scripts/list_fires.sh", "allowance_guard.py room", "loop_state.py state",
                   "plan_week.py", "dispatch_fires.py plan/plan.json"):
        assert needle in text, needle
    assert "secrets." not in text


def test_compute_gates_shape():
    doc = load("compute-gates.yml")
    assert set(doc["on"]) == {"schedule", "workflow_dispatch"}
    gates, retire = doc["jobs"]["gates"], doc["jobs"]["retire"]
    assert gates["environment"] == retire["environment"] == "loop-writer"
    assert retire["if"] == "needs.gates.outputs.retire != ''"
    st = {s.get("name") or s.get("id"): s for s in retire["steps"]}
    prep = st["Prepare the removal commit (no write credential here)"]
    assert "retire_skill.py" in prep["run"] and "loop_state.py merge-mode" in prep["run"]
    assert "token" not in yaml.safe_dump(prep.get("env", {})).lower()
    assert st["app"]["with"]["repositories"] == "${{ steps.pre.outputs.repo_name }}"
    assert "open_loop_pr.sh" in st["Open the removal pull request in the registry"]["run"]
    issue = st["File a retirement issue instead (no loop-writer App)"]
    assert issue["if"] == "steps.pre.outputs.app != 'true'" and issue["env"]["GH_TOKEN"] == "${{ github.token }}"
    write = next(s for s in gates["steps"] if s.get("name", "").startswith("Write gates/status.json"))
    assert "merge-base --is-ancestor" in write["run"] and "gates/status.json" in write["run"]


def test_roster_refresh_shape():
    doc = load("roster-refresh.yml")
    assert set(doc["on"]) == {"schedule", "workflow_dispatch"}
    job = doc["jobs"]["refresh"]
    assert job["environment"] == "roster-writer"
    gated = [s for s in job["steps"] if s.get("id") == "app" or s.get("name") == "Open the roster pull request"]
    assert len(gated) == 2 and all("steps.build.outputs.changed == 'true'" in s["if"] for s in gated)
    text = (WF / "roster-refresh.yml").read_text()
    assert "refresh_roster.py --write" in text and "refreshes that file from usage data" in text


def test_fixture_supply_shape():
    doc = load("fixture-supply.yml")
    assert set(doc["on"]) == {"schedule", "workflow_dispatch"}
    text = (WF / "fixture-supply.yml").read_text()
    for needle in ("fixture_supply.py prepare", "pick_fixture_candidates.py", "fixture_supply.py plan",
                   "dispatch_fires.py supply/plan.json", "number,mergedAt,files"):
        assert needle in text, needle
    assert "secrets." not in text
    assert re.findall(r"--json (\S+)", text) == ["number,mergedAt,files"]   # never a title or body


def test_scaffold_and_draft_gates_open_through_the_shared_script():
    sc = load("routine-scaffold-gate.yml")["jobs"]
    assert sc["prepare"]["environment"] == "privacy" and sc["open-pr"]["environment"] == "loop-writer"
    assert "scaffold_gate.py\" prepare" in str(sc["prepare"])
    open_step = sc["open-pr"]["steps"][-1]
    assert "open_loop_pr.sh" in open_step["run"] and '--draft "$DRAFT"' in open_step["run"]
    dr = load("routine-draft-gate.yml")["jobs"]
    assert dr["prepare"]["environment"] == "privacy" and dr["write"]["environment"] == "loop-writer"
    assert "draft_gate.py prepare" in str(dr["prepare"])
    last = dr["write"]["steps"][-1]["run"]
    assert "--draft true" in last and "gh issue create" in last and "--draft false" not in last


def test_draft_skill_picker_shape():
    doc = load("routine-draft-skill.yml")
    assert set(doc["on"]) == {"schedule", "workflow_dispatch"}
    pick, record = doc["jobs"]["pick"], doc["jobs"]["record"]
    assert pick["environment"] == "lessons-reader" and record["environment"] == "loop-writer"
    lessons = next(s for s in pick["steps"] if s.get("with", {}).get("path") == "lessons")
    assert lessons["with"]["ref"] == "persistent/lessons" and lessons["with"]["persist-credentials"] is False
    mint = next(s for s in pick["steps"] if s.get("id") == "app")
    assert mint["with"]["permission-contents"] == "read"
    text = (WF / "routine-draft-skill.yml").read_text()
    assert "pick_lesson.py pick" in text and "dispatch_fires.py" in text and "drafted-subjects.json" in text
    assert set(pick["outputs"]) == {"lesson_id"}
    assert "pick_lesson.py record" in str(record) and "LESSON_ID" in str(record)
