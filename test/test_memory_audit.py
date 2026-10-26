"""Tests for scripts/memory_audit.py. Every note here is invented."""

import datetime
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "memory_audit.py"
spec = importlib.util.spec_from_file_location("memory_audit", SCRIPT)
memory_audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory_audit)

TODAY = datetime.date(2026, 10, 26)


def note(name, body, note_type="project", home=None, description="An invented note"):
    lines = ["---", "name: %s" % name, "description: %s" % json.dumps(description),
             "metadata:", "  type: %s" % note_type]
    if home is not None:
        lines.append("  home: %s" % home)
    lines += ["---", "", body]
    return "\n".join(lines) + "\n"


class MemoryAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "memory"
        self.dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text, index=True):
        (self.dir / name).write_text(text, encoding="utf-8")
        if index:
            with open(self.dir / "MEMORY.md", "a", encoding="utf-8") as fh:
                fh.write("- [%s](%s) — invented\n" % (name[:-3], name))

    def run_audit(self, **kw):
        return memory_audit.audit(str(self.dir), TODAY, **kw)

    # (a) homes
    def test_missing_home_flags_non_user_notes_only(self):
        self.write("homed.md", note("homed", "Widgets ship on Tuesdays.",
                                    home="example-org/widget:AGENTS.md"))
        self.write("homeless.md", note("homeless", "Gadgets ship on Fridays."))
        self.write("bad-form.md", note("bad-form", "Sprockets are blue.", home="just-a-word"))
        self.write("person.md", note("person", "Prefers short replies.", note_type="user"))
        r = self.run_audit()
        self.assertEqual(r["findings"]["missing_home"], ["bad-form.md", "homeless.md"])

    def test_unparseable_frontmatter_is_a_finding(self):
        self.write("broken.md", "---\nname: broken\ndescription: one: two: three\n---\nBody\n")
        r = self.run_audit()
        self.assertEqual(r["findings"]["unparseable"],
                         [{"note": "broken.md", "reason": "unparseable frontmatter"}])
        self.assertIn("broken.md", r["findings"]["missing_home"])

    def test_https_blob_home_is_accepted(self):
        self.assertEqual(
            memory_audit.split_home("https://github.com/example-org/widget/blob/main/docs/a.md#L3"),
            ("example-org", "widget", "docs/a.md"))
        self.assertIsNone(memory_audit.split_home("example-org:docs/a.md"))

    # (b) duplicates
    def test_near_duplicates_are_paired_and_distinct_notes_are_not(self):
        text = ("The invented widget service deploys from the main branch every "
                "Tuesday after the nightly build passes and the owner signs off.")
        self.write("a.md", note("a", text))
        self.write("b.md", note("b", text.replace("Tuesday", "Wednesday") + "!"))
        self.write("c.md", note("c", "Completely unrelated: the cafeteria serves soup on Mondays."))
        dups = self.run_audit()["findings"]["duplicates"]
        self.assertEqual([d["notes"] for d in dups], [["a.md", "b.md"]])
        self.assertGreater(dups[0]["similarity"], 0.8)

    # (c) staleness
    def test_old_dated_lines_are_stale_and_recent_ones_are_not(self):
        self.write("dated.md", note("dated", "- 2026-08-01 decided X.\n- 2026-10-20 decided Y.\n"
                                             "- 2026-08-01 then 2026-10-25 revisited Z.\n"
                                             "- 2026-13-45 is not a date."))
        stale = self.run_audit(stale_days=30)["findings"]["stale_dates"]
        self.assertEqual(stale, [{"note": "dated.md", "lines": [{"line": 2, "date": "2026-08-01"}]}])

    def test_resolver_marks_closed_merged_and_missing_references(self):
        body = ("See example-org/widget#12 and https://github.com/example-org/widget/pull/34, "
                "also widget#56 and https://github.com/example-org/widget/blob/main/docs/x.md.")
        self.write("refs.md", note("refs", body, home="example-org/widget:docs/gone.md"))
        states = {"example-org/widget#12": "open", "example-org/widget#34": "merged",
                  "widget#56": "closed", "example-org/widget:docs/x.md": "present",
                  "example-org/widget:docs/gone.md": "missing"}
        r = self.run_audit(resolver=states.get)
        self.assertEqual(r["references"], sorted(states))
        self.assertEqual(
            [(s["ref"], s["state"], s["home"]) for s in r["findings"]["stale_refs"]],
            [("example-org/widget#34", "merged", False),
             ("example-org/widget:docs/gone.md", "missing", True),
             ("widget#56", "closed", False)])

    # (d) index
    def test_index_out_of_step(self):
        self.write("listed.md", note("listed", "Listed.", home="example-org/widget:README.md"))
        self.write("unlisted.md", note("unlisted", "Unlisted.", home="example-org/widget:README.md"),
                   index=False)
        with open(self.dir / "MEMORY.md", "a", encoding="utf-8") as fh:
            fh.write("- [gone](gone.md)\n- [elsewhere](https://example.com/x.md)\n")
        idx = self.run_audit()["findings"]["index"]
        self.assertEqual(idx["unindexed"], ["unlisted.md"])
        self.assertEqual(idx["dangling"], ["gone.md"])

    def test_missing_index_is_one_finding(self):
        self.write("only.md", note("only", "Only.", home="example-org/widget:README.md"), index=False)
        r = self.run_audit()
        self.assertFalse(r["findings"]["index"]["present"])
        self.assertEqual(r["finding_count"], 2)  # the missing index, and the unindexed note

    # (e) size
    def test_oversize_files(self):
        self.write("big.md", note("big", "word " * 1000, home="example-org/widget:README.md"))
        self.write("small.md", note("small", "tiny", home="example-org/widget:README.md"))
        self.assertEqual([o["file"] for o in self.run_audit()["findings"]["oversize"]], ["big.md"])

    # promotion
    def test_promotion_proposes_only_the_missing_paragraphs(self):
        body = "Widgets ship on Tuesdays.\n\n- The build takes 4 minutes.\n- Cache is warm."
        self.write("p.md", note("p", body, home="example-org/widget:AGENTS.md"))
        self.write("done.md", note("done", "**Widgets** ship on tuesdays",
                                   home="example-org/widget:AGENTS.md"))
        self.write("doc.md", note("doc", "Gadgets.", home="example-org/widget:docs/gadgets.md"))
        self.write("me.md", note("me", "Likes tea.", note_type="user",
                                 home="example-org/widget:AGENTS.md"))
        self.write("far.md", note("far", "Far away.", home="example-org/other:CLAUDE.md"))
        files = {("example-org", "widget", "AGENTS.md"):
                 "# AGENTS.md\n\n## Repo-specific additions\n\n- Widgets ship on Tuesdays.\n"
                 "- Cache is warm\n",
                 ("example-org", "widget", "docs/gadgets.md"): ""}
        r = self.run_audit(fetcher=lambda o, rp, p: files.get((o, rp, p)))
        self.assertEqual(r["promotions"], [
            {"note": "doc.md", "repo": "example-org/widget", "path": "docs/gadgets.md",
             "target_exists": False, "section": None, "narrow": False, "text": "Gadgets."},
            {"note": "p.md", "repo": "example-org/widget", "path": "AGENTS.md",
             "target_exists": True, "section": "## Repo-specific additions", "narrow": True,
             "text": "- The build takes 4 minutes."},
        ])
        self.assertEqual(r["promotion_unverified"], ["far.md"])

    # CLI
    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), str(self.dir), "--today",
                               TODAY.isoformat(), *args],
                              capture_output=True, text=True)

    def test_cli_exit_codes_summary_and_clone_root(self):
        clones = Path(self.tmp.name) / "repos"
        (clones / "example-org" / "widget").mkdir(parents=True)
        (clones / "example-org" / "widget" / "CLAUDE.md").write_text("Use tabs.\n", encoding="utf-8")
        self.write("ok.md", note("ok", "Use tabs.", home="example-org/widget:CLAUDE.md"))
        clean = self.cli("--strict", "--clone-root", str(clones))
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertEqual(json.loads(clean.stdout)["finding_count"], 0)

        self.write("secret-ish.md", note("x", "Invented private detail XYZZY.", home="nope"))
        refs = Path(self.tmp.name) / "refs.json"
        refs.write_text(json.dumps({"example-org/widget:CLAUDE.md": "missing"}), encoding="utf-8")
        md = Path(self.tmp.name) / "summary.md"
        loose = self.cli("--summary", str(md), "--refs", str(refs))
        self.assertEqual(loose.returncode, 0, loose.stderr)
        strict = self.cli("--strict")
        self.assertEqual(strict.returncode, 1)
        text = md.read_text(encoding="utf-8")
        self.assertIn("secret-ish.md", text)
        self.assertIn("example-org/widget:CLAUDE.md missing (home)", text)
        self.assertNotIn("XYZZY", text)

    def test_cli_usage_errors_exit_2(self):
        self.assertEqual(self.cli("--today", "yesterday").returncode, 2)
        missing = subprocess.run([sys.executable, str(SCRIPT), str(self.dir / "nope")],
                                 capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)


if __name__ == "__main__":
    unittest.main()
