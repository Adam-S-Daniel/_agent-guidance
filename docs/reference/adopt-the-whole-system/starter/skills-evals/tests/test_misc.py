import json
import os

import yaml

import pick_lesson as L

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def les(subject, count, text="The agent pushed before the checks ran."):
    return {"subject": subject, "count": count, "lesson": text}


def test_pick_lesson_skips_drafted_and_invalid():
    lessons = [les("a", 5), les("b", 9, "mail jane.doe@example.com"), les("c", 7), les("d", 8),
               les("Bad Subject", 99), les("e", 6, "x" * 2001)]
    assert L.pick(lessons, {L.lesson_id("d")})["subject"] == "c"
    assert L.pick(lessons, {"d"})["subject"] == "d"     # drafted.json holds ids, not subjects
    assert L.pick([], set()) is None


def test_lesson_id_is_the_sha256_of_the_subject():
    import hashlib
    assert L.lesson_id("a") == hashlib.sha256(b"a").hexdigest()
    assert L.LESSON_ID.match(L.lesson_id("push-before-checks"))


def test_pick_lesson_cli(tmp_path, capsys):
    d = tmp_path / "lessons"
    d.mkdir()
    (d / "a.json").write_text(json.dumps(les("a", 3)))
    drafted, plan = tmp_path / "drafted.json", tmp_path / "plan.json"
    drafted.write_text("[]")
    assert L.main(["pick", "--lessons", str(d), "--drafted", str(drafted), "--plan-out", str(plan)]) == 0
    assert capsys.readouterr().out == L.lesson_id("a") + "\n"
    assert json.loads(plan.read_text()) == {"fires": [{"mode": "draft", "lesson_id": L.lesson_id("a")}]}
    assert L.main(["record", "--drafted", str(drafted), "--lesson-id", L.lesson_id("a")]) == 0
    assert L.main(["record", "--drafted", str(drafted), "--lesson-id", L.lesson_id("a")]) == 0
    assert json.loads(drafted.read_text()) == [L.lesson_id("a")]
    assert L.main(["pick", "--lessons", str(d), "--drafted", str(drafted), "--plan-out", str(plan)]) == 1
    assert L.main(["record", "--drafted", str(drafted), "--lesson-id", "a"]) == 2
    drafted.write_text('["a"]')          # the old slug format is refused, not silently re-drafted
    assert L.main(["pick", "--lessons", str(d), "--drafted", str(drafted), "--plan-out", str(plan)]) == 2
    assert L.main(["id", "a"]) == 0 and capsys.readouterr().out.strip() == L.lesson_id("a")


def test_scaffold_disarm_is_default_branch_code_and_covers_only_scaffold_branches():
    wf = yaml.safe_load(open(os.path.join(ROOT, ".github/workflows/scaffold-disarm.yml")))
    assert wf[True] == {"pull_request_target": {"types": ["synchronize"]}}
    assert wf["permissions"] == {"pull-requests": "write"}
    job = wf["jobs"]["disarm"]
    assert job["if"] == "startsWith(github.event.pull_request.head.ref, 'eval-scaffold/')"
    (step,) = job["steps"]
    assert "${{" not in step["run"] and step["env"]["REPO"] == "${{ github.repository }}"


def test_loop_disarm_lives_in_agent_skills_not_here():
    # Its pull requests are in the registry; here it would never see them (guide 10.5).
    assert not os.path.exists(os.path.join(ROOT, ".github/workflows/loop-disarm.yml"))
