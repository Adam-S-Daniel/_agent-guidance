import pick_fixture_candidates as P

SKILLS = {"workflow-path-audit": [r"^\.github/workflows/"], "docs-style": [r"\.md$"]}


def pr(n, files, at="2026-10-20T00:00:00Z", repo="o/app"):
    return {"repo": repo, "number": n, "merged_at": at, "files": files}


def test_ranking_and_filters():
    prs = [pr(1, [".github/workflows/a.yml"]),
           pr(2, [".github/workflows/a.yml", ".github/workflows/b.yml"]),
           pr(3, ["src/x.py"]),
           pr(4, [".github/workflows/a.yml"] + [f"f{i}" for i in range(30)]),
           pr(5, ["README.md"], at="2026-10-25T00:00:00Z"),
           pr(6, [".github/workflows/z.yml"], at="2026-10-26T00:00:00Z"),
           pr(9, [".github/workflows/q.yml"])]
    out = P.rank(prs, SKILLS, {"o/app#9"}, limit=3)
    assert [(r["number"], r["skill"]) for r in out] == [
        (2, "workflow-path-audit"), (6, "workflow-path-audit"), (5, "docs-style")]
    assert out[0]["matched"] == 2


def test_limit_and_empty():
    assert P.rank([], SKILLS, set()) == []
    many = [pr(i, ["README.md"]) for i in range(10)]
    assert len(P.rank(many, SKILLS, set(), limit=3)) == 3
