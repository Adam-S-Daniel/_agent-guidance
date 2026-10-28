import json

import pytest

import mine_sessions as M

# Built from pieces so secret scanners do not match a literal key header.
PEM = "-----BEGIN " + "PRIVATE KEY-----\nMIIabc\n-----END " + "PRIVATE KEY-----"
SECRETS = ["jane.doe@example.com", "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2", "password=hunter2hunter2",
           "192.168.10.77", "/home/jane/projects/app", "https://example.com/path?token=abc123abc123",
           "0123456789abcdef0123456789abcdef0123", "sk-" + "x" * 30,
           PEM]


@pytest.mark.parametrize("secret", SECRETS)
def test_redaction_removes_each_kind(secret):
    out = M.redact(f"before {secret} after")
    key = {"password=hunter2hunter2": "hunter2", PEM: "MIIabc"}.get(secret, secret)
    assert key not in out and "before" in out and "after" in out


def test_redaction_forms():
    assert M.redact("mail me at jane.doe@example.com") == "mail me at <email>"
    assert M.redact("password=hunter2hunter2") == "password=<secret>"
    assert M.redact("API_KEY: abcd1234") == "API_KEY=<secret>"
    assert M.redact("Authorization: Bearer abcdefgh12345678") == "Authorization: Bearer <secret>"
    assert M.redact("see /Users/jane/x and C:\\Users\\jane\\y") == "see ~/x and ~\\y"
    assert M.redact("ordinary words only") == "ordinary words only"


def test_excerpt_is_short_and_redacted():
    out = M.excerpt("word " * 200 + "jane.doe@example.com")
    assert len(out) <= 200 and "@" not in out


def test_validate_lesson():
    assert M.validate_lesson("The agent pushed before running the checks.") is None
    assert M.validate_lesson("x" * 2001) == "too long"
    assert M.validate_lesson("") == "empty"
    assert M.validate_lesson(5) == "empty"
    assert M.validate_lesson("mail jane.doe@example.com") == "contains something redaction would remove"
    assert M.validate_lesson("bad \x07 bell") == "control characters"


def line(role, content, ts, sid="sess-1", **kw):
    return json.dumps({"type": role, "sessionId": sid, "timestamp": ts,
                       "message": {"role": role, "content": content}, **kw})


def tool_use(i, cmd):
    return [{"type": "tool_use", "id": i, "name": "Bash", "input": {"command": cmd}}]


def result(i, text, err=False):
    return [{"type": "tool_result", "tool_use_id": i, "content": text, "is_error": err}]


def transcript(lines):
    return "\n".join(lines) + "\n"


def make(tmp_path, lines, sid_dir="proj", name="a.jsonl"):
    d = tmp_path / "projects" / sid_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(transcript(lines))
    return str(tmp_path / "projects")


def rich_session():
    t = lambda n: f"2026-10-28T10:00:{n:02d}Z"  # noqa: E731
    out = [line("user", "please fix the build", t(0)),
           line("assistant", tool_use("a", "make build"), t(1)),
           line("user", result("a", "boom", True), t(2)),
           line("assistant", tool_use("b", "make build"), t(3)),
           line("user", result("b", "boom", True), t(4)),
           line("assistant", tool_use("c", "make build"), t(5)),
           line("user", result("c", "boom", True), t(6)),
           line("user", "no, don't touch my password=hunter2hunter2 file", t(7)),
           line("user", "[Request interrupted by user]", t(8)),
           line("assistant", tool_use("d", "git push origin work"), t(9)),
           line("user", result("d", "ok"), t(10)),
           line("assistant", tool_use("e", "gh pr checks 5"), t(11)),
           line("user", result("e", "build  fail  1m"), t(12)),
           line("assistant", [{"type": "text", "text": "Still need to fix the remaining test."}], t(13))]
    return out


def test_all_five_kinds_are_detected(tmp_path):
    recs, newest = M.mine(make(tmp_path, rich_session()))
    kinds = sorted(r["kind"] for r in recs)
    assert kinds == ["ci-red-after-push", "correction", "interrupt", "repeated-failure", "unfinished"]
    assert newest == "2026-10-28T10:00:13Z"


def test_records_are_hashed_and_redacted(tmp_path):
    recs, _ = M.mine(make(tmp_path, rich_session()))
    blob = json.dumps(recs)
    assert "sess-1" not in blob and "hunter2hunter2" not in blob
    assert all(len(r["sid"]) == 12 and len(r["excerpt"]) <= 200 for r in recs)
    assert len({r["sid"] for r in recs}) == 1


def test_a_session_without_real_work_is_skipped(tmp_path):
    lines = [line("user", "no, stop", "2026-10-28T10:00:00Z"), line("assistant", [{"type": "text", "text": "ok"}], "2026-10-28T10:00:01Z")]
    assert M.mine(make(tmp_path, lines))[0] == []


def test_cursor_makes_the_second_run_empty(tmp_path, capsys):
    proj = make(tmp_path, rich_session())
    cur = tmp_path / "cursor"
    assert M.main(["--projects-dir", proj, "--cursor", str(cur)], environ={}) == 0
    first = capsys.readouterr().out.strip().splitlines()
    assert len(first) == 5 and cur.read_text().strip() == "2026-10-28T10:00:13Z"
    assert M.main(["--projects-dir", proj, "--cursor", str(cur)], environ={}) == 0
    assert capsys.readouterr().out == ""


def test_refuses_in_the_cloud(tmp_path, capsys):
    proj = make(tmp_path, rich_session())
    out = tmp_path / "out.jsonl"
    code = M.main(["--projects-dir", proj, "--out", str(out)], environ={"CLAUDE_CODE_REMOTE": "true"})
    assert code == 3 and not out.exists()
    assert capsys.readouterr().out == ""
    assert M.main(["--projects-dir", proj, "--out", str(out)], environ={"CLAUDE_CODE_REMOTE": "false"}) == 0


def test_bad_lines_are_ignored(tmp_path):
    proj = make(tmp_path, ["not json", "[1,2]"] + rich_session())
    assert len(M.mine(proj)[0]) == 5
