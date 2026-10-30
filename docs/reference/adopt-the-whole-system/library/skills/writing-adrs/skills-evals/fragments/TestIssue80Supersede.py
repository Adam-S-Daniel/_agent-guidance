class TestIssue80Supersede(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adr-supersede-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fixture = run_eval.load_fixture(EVAL_DIR)
        self.checks = {c["id"]: c for c in self.fixture["objective_checks"]}
        self._workspaces = 0

    # ---- helpers -------------------------------------------------------------

    def _ws(self) -> Path:
        """A fresh copy of the pristine seed, under this test's own tmp."""
        self._workspaces += 1
        ws = self.tmp / f"ws-{self._workspaces}"
        shutil.copytree(SEED, ws)
        return ws

    def _good(self) -> Path:
        """A seed copy with the hand-written good result applied."""
        ws = self._ws()
        (ws / ADR_0004).write_text(GOOD_ADR_0004, encoding="utf-8")
        replace_once(ws / ADR_0002, SEED_STATUS_0002, GOOD_STATUS_0002)
        replace_once(ws / INDEX, SEED_ROW_0002, GOOD_ROW_0002)
        replace_once(ws / INDEX, SEED_ROW_0003, SEED_ROW_0003 + GOOD_ROW_0004)
        replace_once(ws / SCRIPT, SEED_POINTER, GOOD_POINTER)
        return ws

    def _score(self, ws: Path | None = None) -> tuple[int, dict[str, bool]]:
        """(exit code, {check id: passed}) from the harness's command line.

        With no workspace the harness scores its own copy of the pristine
        seed. The environment is built here: PATH is the platform default,
        HOME and TMPDIR are under this test's tmp, and PYTHONPATH repeats
        this interpreter's own import path so the child can import what
        this process imported, wherever it was installed.
        """
        home, child_tmp = self.tmp / "home", self.tmp / "child-tmp"
        home.mkdir(exist_ok=True)
        child_tmp.mkdir(exist_ok=True)
        env = {"PATH": os.defpath, "HOME": str(home), "TMPDIR": str(child_tmp),
               "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
        extra = ["--workspace", str(ws)] if ws is not None else []
        cmd = [sys.executable, str(HARNESS_DIR / "run_eval.py"), str(EVAL_DIR),
               "--arm", "objective-only",
               "--results-dir", str(self.tmp / "results"), *extra]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                              cwd=str(REPO_ROOT), timeout=300)
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"run_eval.py printed no JSON (exit {proc.returncode}):\n"
                      f"{proc.stdout}\n{proc.stderr}")
        self.assertEqual(payload["arm"], "objective-only")
        results = {c["id"]: c["passed"] for c in payload["checks"]}
        self.assertEqual(len(results), len(payload["checks"]), "duplicate check id")
        self.assertEqual(proc.returncode, 0 if all(results.values()) else 1,
                         "the exit code must agree with the per-check results")
        return proc.returncode, results

    def _failed(self, ws: Path) -> set[str]:
        code, results = self._score(ws)
        failed = {check_id for check_id, passed in results.items() if not passed}
        self.assertEqual(code, 1 if failed else 0)
        return failed

    def _assert_passes_everything(self, ws: Path) -> None:
        code, results = self._score(ws)
        self.assertEqual({k for k, v in results.items() if not v}, set())
        self.assertEqual(list(results), list(EXPECTED_PRISTINE))
        self.assertEqual(code, 0)

    # ---- the pristine seed: the asymmetry ------------------------------------

    def test_pristine_seed_fails_every_behavior_check_and_passes_every_restraint_check(self):
        code, results = self._score()
        self.assertEqual(code, 1)
        self.assertEqual(results, EXPECTED_PRISTINE)
        self.assertEqual(list(results), list(EXPECTED_PRISTINE),
                         "check order drifted from the fixture's")
        self.assertEqual(list(results), [c["id"] for c in self.fixture["objective_checks"]])
        self.assertIn(False, results.values())
        self.assertIn(True, results.values())

    def test_scoring_a_seed_copy_as_a_workspace_agrees_with_the_default_run(self):
        # `--workspace` is what every other test here scores through, so it
        # has to agree with the no-workspace run on the same content.
        self.assertEqual(self._score(self._ws()), self._score())

    # ---- the good result, and one mutation per check -------------------------

    def test_hand_written_good_result_passes_every_check(self):
        self._assert_passes_everything(self._good())

    def _mutations(self) -> dict:
        """For each check id, an edit to the good result that breaks only it."""

        def no_alternatives_heading(ws):
            replace_once(ws / ADR_0004, "## Alternatives considered\n", "## Options\n")

        def never_names_0002(ws):
            replace_once(ws / ADR_0004, SUPERSEDES_SENTENCE,
                         "This replaces the age rule.")

        def status_0002_left_accepted(ws):
            replace_once(ws / ADR_0002, GOOD_STATUS_0002, SEED_STATUS_0002)

        def decision_0002_rewritten_in_place(ws):
            replace_once(ws / ADR_0002, SEED_DECISION_0002,
                         "Keep the newest 14 snapshots and delete the rest.\n")

        def no_index_row_for_0004(ws):
            replace_once(ws / INDEX, GOOD_ROW_0004, "")

        def index_row_0002_left_accepted(ws):
            replace_once(ws / INDEX, GOOD_ROW_0002, SEED_ROW_0002)

        def index_row_0003_changed(ws):
            replace_once(ws / INDEX, SEED_ROW_0003,
                         SEED_ROW_0003.replace("| Accepted |", "| Rejected |"))

        def index_row_0004_names_a_missing_file(ws):
            replace_once(ws / INDEX, "(0004-keep-the-newest-14-snapshots.md)",
                         "(0004-keep-fourteen.md)")

        def script_pointer_left_at_0002(ws):
            replace_once(ws / SCRIPT, GOOD_POINTER, SEED_POINTER)

        def script_pointer_names_a_missing_file(ws):
            replace_once(ws / SCRIPT, GOOD_POINTER,
                         "# Retention policy: docs/decisions/0004-keep-fourteen.md\n")

        def script_keeps_30(ws):
            replace_once(ws / SCRIPT, "keep=14\n", "keep=30\n")

        def second_new_adr(ws):
            (ws / DECISIONS / "0005-fix-the-dry-run-help-text.md").write_text(
                DECOY_ADR_0005, encoding="utf-8")

        def changelog_edited(ws):
            replace_once(ws / "CHANGELOG.md", "## 2.1.0 - 2026-06-09\n",
                         "## 2.1.0 - 2026-06-09\n\n- Added ADR 0004.\n")

        return {
            "new-adr-0004-is-a-house-format-entry": no_alternatives_heading,
            "new-adr-0004-names-0002": never_names_0002,
            "adr-0002-status-says-superseded-by-0004": status_0002_left_accepted,
            "adr-0002-keeps-what-it-recorded": decision_0002_rewritten_in_place,
            "index-gained-a-row-for-0004": no_index_row_for_0004,
            "index-row-for-0002-says-superseded-by-0004": index_row_0002_left_accepted,
            "index-rows-for-0001-and-0003-kept": index_row_0003_changed,
            "decision-log-links-resolve": index_row_0004_names_a_missing_file,
            "script-comment-points-at-0004": script_pointer_left_at_0002,
            "script-adr-pointers-resolve": script_pointer_names_a_missing_file,
            "script-still-keeps-the-newest-14": script_keeps_30,
            "exactly-one-new-adr-file": second_new_adr,
            "nothing-else-touched": changelog_edited,
        }

    def test_every_check_has_a_mutation(self):
        self.assertEqual(sorted(self._mutations()), sorted(self.checks))
        self.assertEqual(sorted(EXPECTED_PRISTINE), sorted(self.checks))

    def test_each_minimally_wrong_result_fails_exactly_its_own_check(self):
        for check_id, mutate in self._mutations().items():
            with self.subTest(check=check_id):
                ws = self._good()
                mutate(ws)
                self.assertEqual(self._failed(ws), {check_id})

    def test_each_required_line_of_the_new_adr_is_required(self):
        for label, old, new in (
                ("title line in another shape", "# 0004. Keep", "# ADR 0004: Keep"),
                ("title line with no title",
                 "# 0004. Keep the newest 14 snapshots instead of pruning by age\n",
                 "# 0004. \n"),
                ("no status line", "- **Status:** Accepted\n", ""),
                ("a status outside the vocabulary", "- **Status:** Accepted\n",
                 "- **Status:** Done\n"),
                ("no Context heading", "## Context\n", "## Background\n"),
                ("no Decision heading", "## Decision\n", "## What we do\n"),
                ("no Consequences heading", "## Consequences\n", "## Effects\n"),
                ("no Alternatives heading", "## Alternatives considered\n",
                 "## Options\n")):
            with self.subTest(missing=label):
                ws = self._variant((ADR_0004, old, new))
                self.assertEqual(self._failed(ws),
                                 {"new-adr-0004-is-a-house-format-entry"})

    def test_each_untouched_index_row_is_pinned(self):
        for row in ("| [0001](0001-take-snapshots-with-pg-basebackup.md) | Take "
                    "snapshots with pg_basebackup, not pg_dump | Accepted |\n",
                    SEED_ROW_0003):
            for label, old, new in (
                    ("status", "| Accepted |", "| Rejected |"),
                    ("title", "napshots ", "napshot "),
                    ("row removed", row, "")):
                with self.subTest(row=row[:8], edit=label):
                    changed = row.replace(old, new, 1)
                    self.assertNotEqual(changed, row)
                    ws = self._variant((INDEX, row, changed))
                    self.assertEqual(self._failed(ws),
                                     {"index-rows-for-0001-and-0003-kept"})

    def test_each_pinned_script_line_is_pinned(self):
        for label, old, new in (
                ("the header sentence",
                 "# prune-snapshots.sh: keep the newest 14 snapshots and delete the rest.\n",
                 "# prune-snapshots.sh: prune old snapshots.\n"),
                ("keep", "keep=14\n", "keep=30\n"),
                ("keep removed", "keep=14\n", "")):
            with self.subTest(edit=label):
                ws = self._variant((SCRIPT, old, new))
                self.assertEqual(self._failed(ws), {"script-still-keeps-the-newest-14"})

    def test_each_file_that_must_not_change_is_guarded(self):
        guarded = ("README.md", "CHANGELOG.md", "AGENTS.md",
                   "docs/decisions/0001-take-snapshots-with-pg-basebackup.md",
                   "docs/decisions/0003-store-snapshots-on-a-separate-volume.md")
        for rel in guarded:
            with self.subTest(edited=rel):
                ws = self._good()
                with open(ws / rel, "a", encoding="utf-8") as f:
                    f.write("\nOne more line.\n")
                self.assertEqual(self._failed(ws), {"nothing-else-touched"})
        with self.subTest(removed="README.md"):
            ws = self._good()
            (ws / "README.md").unlink()
            self.assertEqual(self._failed(ws), {"nothing-else-touched"})

    def test_a_dangling_adr_link_anywhere_in_the_log_fails_the_link_check(self):
        ghost = "[0004](0004-keep-fourteen.md)"
        for label, path, old, new in (
                ("the index", INDEX, "(0004-keep-the-newest-14-snapshots.md)",
                 "(0004-keep-fourteen.md)"),
                ("0002's status", ADR_0002, "Superseded by 0004\n",
                 f"Superseded by {ghost}\n"),
                ("the new ADR", ADR_0004, SUPERSEDES_SENTENCE,
                 "This supersedes [0002](0002-prune-by-age.md)."),
                ("a second link on one line", INDEX, "| Superseded by 0004 |",
                 "| Superseded by [0004](0004-keep-the-newest-14-snapshots.md), "
                 f"was {ghost} |")):
            with self.subTest(where=label):
                ws = self._variant((path, old, new))
                self.assertEqual(self._failed(ws), {"decision-log-links-resolve"})

    def test_too_few_and_too_many_adr_files_both_fail_the_count(self):
        with self.subTest(files=5):
            ws = self._good()
            (ws / DECISIONS / "0005-fix-the-dry-run-help-text.md").write_text(
                DECOY_ADR_0005, encoding="utf-8")
            self.assertEqual(self._failed(ws), {"exactly-one-new-adr-file"})
        with self.subTest(files=3):
            # The new ADR written under a name the glob does not count.
            ws = self._good()
            (ws / ADR_0004).rename(ws / DECISIONS / "adr-0004.md")
            self.assertIn("exactly-one-new-adr-file", self._failed(ws))

    # ---- rule 1: the prescribed formats and their reasonable variants --------

    def _variant(self, *edits) -> Path:
        ws = self._good()
        for path, old, new in edits:
            replace_once(ws / path, old, new)
        return ws

    def test_status_spellings_of_superseded_by_all_pass(self):
        link = "[0004](0004-keep-the-newest-14-snapshots.md)"
        for status in ("Superseded by 0004",
                       f"Superseded by {link}",
                       "Superseded by ADR 0004",
                       "Superseded by ADR-0004",
                       "Superseded-by: 0004",
                       "Accepted, then superseded by 0004",
                       "**Superseded by 0004** (2026-06-09)"):
            with self.subTest(status=status):
                ws = self._variant(
                    (ADR_0002, "- **Status:** Superseded by 0004\n",
                     f"- **Status:** {status}\n"),
                    (INDEX, "| Superseded by 0004 |", f"| {status} |"))
                self._assert_passes_everything(ws)

    def test_status_phrase_from_the_live_skill_template_passes(self):
        # The skill's own bootstrap README names its status values; if the
        # phrase for a superseded ADR ever changes there, this is where the
        # fixture is shown to reject what the skill now prescribes.
        registries = run_eval.resolve_registries(None, None, REPO_ROOT)
        entry = registries["acsi-agentskills"]
        pattern = run_eval._skill_md_glob(entry["layout"], "writing-adrs")
        skill_dirs = sorted(p.parent for p in entry["path"].glob(pattern)
                            if p.is_file()) if entry["path"].is_dir() else []
        if not skill_dirs:
            reason = (f"no writing-adrs skill under {entry['path']} — skipping "
                      "the live status-phrase check")
            print(reason)
            self.skipTest(reason)
        template = (skill_dirs[0] / "references" /
                    "decisions-README-template.md").read_text(encoding="utf-8")
        section = template.split("## Status values", 1)
        self.assertEqual(len(section), 2, "the template has no Status values section")
        body = section[1].split("\n## ", 1)[0]
        phrases = sorted(set(re.findall(r"`([^`\n]*NNNN[^`\n]*)`", body)))
        self.assertTrue(phrases, "the template names no status value carrying NNNN")
        for phrase in phrases:
            status = phrase.replace("NNNN", "0004")
            with self.subTest(status=status):
                ws = self._variant(
                    (ADR_0002, "- **Status:** Superseded by 0004\n",
                     f"- **Status:** {status}\n"),
                    (INDEX, "| Superseded by 0004 |", f"| {status} |"))
                self._assert_passes_everything(ws)

    def test_new_adr_in_the_skills_own_template_shape_passes(self):
        # The skill's default template: a Deciders line and a References
        # section on top of what this repo's README asks for.
        ws = self._variant(
            (ADR_0004, "- **Date:** 2026-06-09\n",
             "- **Date:** 2026-06-09\n- **Deciders:** the on-call rotation\n"),
            (ADR_0004, "months.\n",
             "months.\n\n## References\n\n- PR #58\n"))
        self._assert_passes_everything(ws)

    def test_ways_of_naming_0002_in_the_new_adr_all_pass(self):
        link = "[0002](0002-prune-snapshots-older-than-30-days.md)"
        for sentence in ("Supersedes 0002.",
                         "This supersedes ADR 0002.",
                         f"This supersedes {link}.",
                         "ADR-0002 pruned by age; this replaces it."):
            with self.subTest(sentence=sentence):
                self._assert_passes_everything(
                    self._variant((ADR_0004, SUPERSEDES_SENTENCE, sentence)))
        with self.subTest(where="a Supersedes metadata line"):
            self._assert_passes_everything(self._variant(
                (ADR_0004, SUPERSEDES_SENTENCE, "This replaces the age rule."),
                (ADR_0004, "- **Date:** 2026-06-09\n",
                 "- **Date:** 2026-06-09\n- **Supersedes:** 0002\n")))
        with self.subTest(where="the Status line"):
            self._assert_passes_everything(self._variant(
                (ADR_0004, SUPERSEDES_SENTENCE, "This replaces the age rule."),
                (ADR_0004, "- **Status:** Accepted\n",
                 "- **Status:** Accepted (supersedes 0002)\n")))

    def test_new_adr_status_proposed_passes(self):
        self._assert_passes_everything(self._variant(
            (ADR_0004, "- **Status:** Accepted\n", "- **Status:** Proposed\n"),
            (INDEX, "pruning by age | Accepted |", "pruning by age | Proposed |")))

    def test_a_forward_pointer_added_around_the_old_text_passes(self):
        note = ("> Superseded by [0004](0004-keep-the-newest-14-snapshots.md): "
                "see there for why.\n")
        with self.subTest(where="above Context"):
            self._assert_passes_everything(self._variant(
                (ADR_0002, "\n## Context\n", f"\n{note}\n## Context\n")))
        with self.subTest(where="after the last line"):
            ws = self._good()
            with open(ws / ADR_0002, "a", encoding="utf-8") as f:
                f.write(f"\n{note}")
            self._assert_passes_everything(ws)

    def test_a_padded_index_table_passes(self):
        ws = self._good()
        index = ws / INDEX
        lines = index.read_text(encoding="utf-8").splitlines(keepends=True)
        rows = [i for i, line in enumerate(lines) if line.startswith("| [")]
        self.assertEqual(len(rows), 4)
        for i in rows:
            cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            lines[i] = "|  " + "   |   ".join(cells) + "    |\n"
        index.write_text("".join(lines), encoding="utf-8")
        self._assert_passes_everything(ws)

    def test_other_slugs_for_the_new_adr_pass(self):
        for slug in ("0004-keep-newest-14.md", "0004-prune-snapshots-by-count-not-age.md"):
            with self.subTest(slug=slug):
                ws = self._good()
                (ws / ADR_0004).rename(ws / DECISIONS / slug)
                replace_once(ws / INDEX, f"({ADR_0004.name})", f"({slug})")
                replace_once(ws / SCRIPT, ADR_0004.name, slug)
                self._assert_passes_everything(ws)

    def test_other_placements_of_the_script_pointer_pass(self):
        path = "docs/decisions/0004-keep-the-newest-14-snapshots.md"
        with self.subTest(where="in the header, old pointer removed"):
            self._assert_passes_everything(self._variant(
                (SCRIPT, GOOD_POINTER, ""),
                (SCRIPT, "set -euo pipefail\n",
                 f"#\n# See {path} for why.\nset -euo pipefail\n")))
        with self.subTest(where="trailing the keep line"):
            self._assert_passes_everything(self._variant(
                (SCRIPT, GOOD_POINTER, ""),
                (SCRIPT, "keep=14\n", f"keep=14  # {path}\n")))
        with self.subTest(where="naming both"):
            self._assert_passes_everything(self._variant(
                (SCRIPT, GOOD_POINTER,
                 f"# Retention policy: {path}\n"
                 "# (supersedes docs/decisions/"
                 "0002-prune-snapshots-older-than-30-days.md)\n")))

    # ---- rule 2: results that do not do the task -----------------------------

    def _seed_adrs(self) -> list[Path]:
        adrs = sorted((SEED / DECISIONS).glob("[0-9][0-9][0-9][0-9]-*.md"))
        self.assertEqual(len(adrs), 3)
        return adrs

    def test_a_seed_adr_pasted_under_the_new_number_is_refused(self):
        # Each seed ADR, renumbered in its title line and given the sentence
        # the second check looks for: still not a new entry.
        for adr in self._seed_adrs():
            with self.subTest(pasted=adr.name):
                text = adr.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(f"# {adr.name[:4]}. "))
                pasted = "# 0004. " + text[len("# 0000. "):] + "\nSupersedes 0002.\n"
                ws = self._good()
                (ws / ADR_0004).write_text(pasted, encoding="utf-8")
                self.assertEqual(self._failed(ws),
                                 {"new-adr-0004-is-a-house-format-entry"})

    def test_a_seed_adr_pasted_with_a_new_title_is_refused(self):
        # The title replaced as well: the pasted Decision lines alone refuse it.
        for adr in self._seed_adrs():
            with self.subTest(pasted=adr.name):
                body = adr.read_text(encoding="utf-8").split("\n", 1)[1]
                pasted = ("# 0004. Keep the newest 14 snapshots\n" + body
                          + "\nSupersedes 0002.\n")
                ws = self._good()
                (ws / ADR_0004).write_text(pasted, encoding="utf-8")
                self.assertEqual(self._failed(ws),
                                 {"new-adr-0004-is-a-house-format-entry"})

    def test_every_seed_decision_line_and_title_is_refused_in_the_new_adr(self):
        # The refused lines are read from the seed, not retyped: a seed ADR
        # edited without the fixture following it goes red here. Each line
        # is added, alone, to the otherwise good new ADR.
        seen = 0
        for adr in self._seed_adrs():
            lines = adr.read_text(encoding="utf-8").splitlines()
            start = lines.index("## Decision")
            end = lines.index("## Consequences")
            decision = [line for line in lines[start + 1:end] if line.strip()]
            self.assertTrue(decision, f"{adr.name}: empty Decision section")
            title = "# 0004. " + lines[0][len("# 0000. "):]
            for line in [title, *decision]:
                seen += 1
                with self.subTest(adr=adr.name, line=line):
                    ws = self._good()
                    with open(ws / ADR_0004, "a", encoding="utf-8") as f:
                        f.write(f"\n{line}\n")
                    self.assertEqual(self._failed(ws),
                                     {"new-adr-0004-is-a-house-format-entry"})
        self.assertEqual(seen, 8)

    def test_adr_0002_copied_verbatim_as_0004_fails_both_new_adr_checks(self):
        ws = self._good()
        shutil.copyfile(SEED / ADR_0002, ws / ADR_0004)
        self.assertEqual(self._failed(ws), {"new-adr-0004-is-a-house-format-entry",
                                            "new-adr-0004-names-0002"})

    def test_an_empty_file_with_the_right_name_fails_both_new_adr_checks(self):
        for content in ("", "\n\n"):
            with self.subTest(content=content):
                ws = self._good()
                (ws / ADR_0004).write_text(content, encoding="utf-8")
                # The file exists, so the count and the links are satisfied:
                # those two say nothing about what is in it.
                self.assertEqual(self._failed(ws),
                                 {"new-adr-0004-is-a-house-format-entry",
                                  "new-adr-0004-names-0002"})

    def test_naming_0002_only_in_a_heading_does_not_count(self):
        ws = self._variant(
            (ADR_0004, SUPERSEDES_SENTENCE, "This replaces the age rule."),
            (ADR_0004, "## Context\n", "## Context\n\n### What 0002 said\n"))
        self.assertEqual(self._failed(ws), {"new-adr-0004-names-0002"})

    def test_a_longer_number_containing_the_digits_does_not_count(self):
        with self.subTest(where="0002 in the new ADR"):
            ws = self._variant((ADR_0004, SUPERSEDES_SENTENCE, "See ticket 100021."))
            self.assertEqual(self._failed(ws), {"new-adr-0004-names-0002"})
        with self.subTest(where="0004 in the old status and the index"):
            ws = self._variant(
                (ADR_0002, "Superseded by 0004\n", "Superseded by 00040\n"),
                (INDEX, "| Superseded by 0004 |", "| Superseded by 00040 |"))
            self.assertEqual(self._failed(ws),
                             {"adr-0002-status-says-superseded-by-0004",
                              "index-row-for-0002-says-superseded-by-0004"})

    def test_required_words_inside_an_html_comment_do_not_count(self):
        with self.subTest(where="0002's status, visible status left Accepted"):
            ws = self._variant(
                (ADR_0002, GOOD_STATUS_0002,
                 SEED_STATUS_0002 + "<!-- - **Status:** Superseded by 0004 -->\n"))
            self.assertEqual(self._failed(ws),
                             {"adr-0002-status-says-superseded-by-0004"})
        with self.subTest(where="0002's status, on its own line inside a comment"):
            ws = self._variant(
                (ADR_0002, GOOD_STATUS_0002,
                 "<!--\n- **Status:** Superseded by 0004\n-->\n"
                 "- **Date:** 2025-11-18\n"))
            self.assertEqual(self._failed(ws),
                             {"adr-0002-status-says-superseded-by-0004"})
        with self.subTest(where="the new ADR's mention of 0002"):
            ws = self._variant(
                (ADR_0004, SUPERSEDES_SENTENCE,
                 "This replaces the age rule.\n<!-- supersedes 0002 -->"))
            self.assertEqual(self._failed(ws),
                             {"new-adr-0004-is-a-house-format-entry",
                              "new-adr-0004-names-0002"})
        with self.subTest(where="the index rows"):
            # No visible row for 0002 or 0004 at all: both rows sit inside
            # the comment, so only the comment guard can refuse them.
            ws = self._variant(
                (INDEX, GOOD_ROW_0002, ""),
                (INDEX, GOOD_ROW_0004,
                 "\n<!--\n" + GOOD_ROW_0002 + GOOD_ROW_0004 + "-->\n"))
            self.assertEqual(self._failed(ws),
                             {"index-gained-a-row-for-0004",
                              "index-row-for-0002-says-superseded-by-0004"})

    def test_superseded_by_on_a_line_that_is_not_the_status_line_does_not_count(self):
        for status in ("Accepted", "Deprecated"):
            with self.subTest(status_line=status):
                ws = self._variant(
                    (ADR_0002, "- **Status:** Superseded by 0004\n",
                     f"- **Status:** {status}\n"))
                with open(ws / ADR_0002, "a", encoding="utf-8") as f:
                    f.write("\nSuperseded by 0004.\n")
                self.assertEqual(self._failed(ws),
                                 {"adr-0002-status-says-superseded-by-0004"})

    def test_the_index_row_for_0002_must_still_be_0002s_row(self):
        with self.subTest(edit="title replaced"):
            ws = self._variant(
                (INDEX, "| Prune snapshots older than 30 days | Superseded",
                 "| Keep the newest 14 snapshots | Superseded"))
            self.assertEqual(self._failed(ws),
                             {"index-row-for-0002-says-superseded-by-0004"})
        with self.subTest(edit="status says superseded, by nothing"):
            ws = self._variant((INDEX, "| Superseded by 0004 |", "| Superseded |"))
            self.assertEqual(self._failed(ws),
                             {"index-row-for-0002-says-superseded-by-0004"})
        with self.subTest(edit="link retargeted"):
            ws = self._variant(
                (INDEX, "[0002](0002-prune-snapshots-older-than-30-days.md)",
                 "[0002](0004-keep-the-newest-14-snapshots.md)"))
            self.assertEqual(self._failed(ws),
                             {"index-row-for-0002-says-superseded-by-0004"})

    def test_the_index_row_for_0004_needs_a_title_and_a_status(self):
        for label, row in (
                ("no title", "| [0004](0004-keep-the-newest-14-snapshots.md) |  | Accepted |\n"),
                ("no status", "| [0004](0004-keep-the-newest-14-snapshots.md) | Keep 14 | |\n"),
                ("two cells", "| [0004](0004-keep-the-newest-14-snapshots.md) | Keep 14 |\n"),
                ("not a row", "[0004](0004-keep-the-newest-14-snapshots.md) Keep 14, Accepted\n"),
                ("link not in the first cell",
                 "| Keep 14 | [0004](0004-keep-the-newest-14-snapshots.md) | Accepted |\n")):
            with self.subTest(row=label):
                ws = self._variant((INDEX, GOOD_ROW_0004, row))
                self.assertEqual(self._failed(ws), {"index-gained-a-row-for-0004"})

    def test_the_new_adrs_path_outside_a_comment_does_not_count(self):
        path = "docs/decisions/0004-keep-the-newest-14-snapshots.md"
        for label, line in (("a command", f'echo "{path}"\n'),
                            ("an assignment", f"policy={path}\n")):
            with self.subTest(line=label):
                ws = self._variant((SCRIPT, GOOD_POINTER, SEED_POINTER + line))
                self.assertEqual(self._failed(ws), {"script-comment-points-at-0004"})

    def test_a_second_status_line_beside_the_accepted_one_does_not_count(self):
        ws = self._variant(
            (ADR_0002, GOOD_STATUS_0002,
             "- **Status:** Accepted\n" + GOOD_STATUS_0002))
        self.assertEqual(self._failed(ws), {"adr-0002-status-says-superseded-by-0004"})

    def test_a_second_index_row_for_0002_beside_the_accepted_one_does_not_count(self):
        ws = self._variant((INDEX, GOOD_ROW_0002, SEED_ROW_0002 + GOOD_ROW_0002))
        self.assertEqual(self._failed(ws),
                         {"index-row-for-0002-says-superseded-by-0004"})

    def test_new_adr_added_but_0002_and_its_row_untouched_fails_those_two(self):
        # The likeliest wrong answer: a correct 0004 beside a log that still
        # says 0002 is in force.
        ws = self._variant((ADR_0002, GOOD_STATUS_0002, SEED_STATUS_0002),
                           (INDEX, GOOD_ROW_0002, SEED_ROW_0002))
        self.assertEqual(self._failed(ws),
                         {"adr-0002-status-says-superseded-by-0004",
                          "index-row-for-0002-says-superseded-by-0004"})

    def test_rewriting_0002_in_place_with_no_new_adr_fails(self):
        # The other likely wrong answer: the accepted ADR edited to say the
        # new thing, title and all, and nothing else written.
        ws = self._ws()
        replace_once(ws / ADR_0002, "# 0002. Prune snapshots older than 30 days\n",
                     "# 0002. Keep the newest 14 snapshots\n")
        replace_once(ws / ADR_0002, SEED_DECISION_0002,
                     "Keep the newest 14 snapshots and delete the rest.\n")
        replace_once(ws / INDEX, "| Prune snapshots older than 30 days | Accepted |",
                     "| Keep the newest 14 snapshots | Accepted |")
        self.assertEqual(self._failed(ws), {
            "new-adr-0004-is-a-house-format-entry", "new-adr-0004-names-0002",
            "adr-0002-status-says-superseded-by-0004",
            "adr-0002-keeps-what-it-recorded", "index-gained-a-row-for-0004",
            "index-row-for-0002-says-superseded-by-0004",
            "script-comment-points-at-0004", "exactly-one-new-adr-file"})

    def test_each_edit_inside_0002s_accepted_text_fails_the_keep_check(self):
        for label, old, new in (
                ("title", "# 0002. Prune snapshots older than 30 days\n",
                 "# 0002. Prune snapshots by count\n"),
                ("date", "- **Date:** 2025-11-18\n", "- **Date:** 2026-06-09\n"),
                ("context", "ever asked to restore", "ever needed to restore"),
                ("consequences", "good.\n", "good. See 0004.\n"),
                ("last line", "for lack of space.\n", "for lack of room.\n"),
                ("a heading", "## Alternatives considered\n", "## Alternatives\n")):
            with self.subTest(edit=label):
                ws = self._variant((ADR_0002, old, new))
                self.assertEqual(self._failed(ws), {"adr-0002-keeps-what-it-recorded"})

    def test_the_keep_check_pins_0002_from_context_to_its_last_line(self):
        # Read from the seed: the multi-line literal has to span everything
        # from `## Context` to the end of the seed file, so no trailing part
        # of the accepted text is left unpinned.
        text = (SEED / ADR_0002).read_text(encoding="utf-8")
        patterns = self.checks["adr-0002-keeps-what-it-recorded"]["must_match"]
        spans = [m.group(0) for m in
                 (re.search(p, text, re.MULTILINE) for p in patterns) if m]
        self.assertEqual(len(spans), len(patterns))
        self.assertIn(text[text.index("## Context"):].rstrip("\n"), spans)
        self.assertEqual(text.count("## Context"), 1)

    def test_every_behavior_check_that_reads_markdown_refuses_an_html_comment(self):
        guard = "^[^\\n]*<!--[^\\n]*$"
        reading_markdown = [
            c["id"] for c in self.checks.values()
            if c["type"] == "file_matches" and not EXPECTED_PRISTINE[c["id"]]
            and all(path.endswith(".md") for path in c["paths"])]
        self.assertEqual(len(reading_markdown), 5)
        for check_id in reading_markdown:
            with self.subTest(check=check_id):
                self.assertIn(guard, self.checks[check_id]["must_not_match"])

    def test_what_the_floor_does_not_certify(self):
        # Pinned so the fixture's own account of its limits stays true: the
        # patterns read lines, not meaning and not Markdown structure.
        with self.subTest(limit="a negated status still names 0004"):
            self._assert_passes_everything(self._variant(
                (ADR_0002, "- **Status:** Superseded by 0004\n",
                 "- **Status:** Not superseded by 0004\n")))
        with self.subTest(limit="a line inside a fenced block is still a line"):
            self._assert_passes_everything(self._variant(
                (ADR_0004, SUPERSEDES_SENTENCE,
                 "This replaces the age rule.\n\n```\n0002\n```")))
        with self.subTest(limit="the old script pointer may stay beside the new one"):
            self._assert_passes_everything(self._variant(
                (SCRIPT, GOOD_POINTER, SEED_POINTER + GOOD_POINTER)))
        with self.subTest(limit="sections of the new ADR may be empty or out of order"):
            self._assert_passes_everything(self._variant(
                (ADR_0004, "## Context\n", "## Decision\n\n## Context\n"),
                (ADR_0004, "\n## Decision\n\nKeep the newest 14 snapshots and "
                 "delete the rest, counting snapshots rather\nthan days. ",
                 "\n")))

    # ---- rule 6: an absent file is not a pass ---------------------------------

    def test_every_check_with_a_must_not_match_requires_the_file(self):
        carrying = [c for c in self.checks.values() if c.get("must_not_match")]
        self.assertTrue(carrying)
        for check in carrying:
            with self.subTest(check=check["id"]):
                self.assertEqual(check["type"], "file_matches")
                self.assertIs(check.get("require_present"), True)
                self.assertTrue(check.get("must_match"))

    def test_deleting_a_file_the_checks_read_fails_them(self):
        for label, rel, expected in (
                ("the superseded ADR", ADR_0002,
                 {"adr-0002-status-says-superseded-by-0004",
                  "adr-0002-keeps-what-it-recorded", "decision-log-links-resolve",
                  "exactly-one-new-adr-file"}),
                ("the index", INDEX,
                 {"index-gained-a-row-for-0004",
                  "index-row-for-0002-says-superseded-by-0004",
                  "index-rows-for-0001-and-0003-kept"}),
                ("the script", SCRIPT,
                 {"script-comment-points-at-0004",
                  "script-still-keeps-the-newest-14"})):
            with self.subTest(deleted=label):
                ws = self._good()
                (ws / rel).unlink()
                self.assertEqual(self._failed(ws), expected)

    # ---- the anchoring property, over the fixture as parsed YAML -------------

    def _patterns(self) -> list[tuple[str, str]]:
        doc = yaml.safe_load((EVAL_DIR / "fixture.yaml").read_text(encoding="utf-8"))
        found = []
        for check in doc["objective_checks"]:
            for key in ("must_match", "must_not_match"):
                for pattern in check.get(key) or []:
                    found.append((f"{check['id']}.{key}", pattern))
        return found

    def test_every_pattern_is_anchored_in_each_alternative_and_line_local(self):
        patterns = self._patterns()
        by_check = {label.split(".")[0] for label, _ in patterns}
        self.assertEqual(by_check, {c["id"] for c in self.checks.values()
                                    if c["type"] == "file_matches"})
        for label, pattern in patterns:
            with self.subTest(pattern=label):
                self.assertEqual(anchoring_problems(pattern), [], pattern)

    def test_only_one_pattern_spells_out_a_newline(self):
        multiline = [label for label, pattern in self._patterns() if "\n" in pattern]
        self.assertEqual(multiline, ["adr-0002-keeps-what-it-recorded.must_match"])

    def test_the_anchoring_property_flags_each_kind_of_loose_pattern(self):
        for pattern, expected in (
                ("foo", "does not start with ^"),
                ("^foo", "does not end with $"),
                ("foo$", "does not start with ^"),
                ("^a$|b", "alternative 'b' does not start with ^"),
                ("^a$|^b", "alternative '^b' does not end with $"),
                ("^a$|", "alternative '' does not start with ^"),
                ("^a.*b$", "has an unescaped ."),
                ("^a[^x]*b$", "negated class [^x] admits a newline"),
                ("^a\\s*b$", "\\s outside a class can match a newline"),
                ("^a\\W$", "\\W outside a class can match a newline"),
                ("^a\\$", "does not end with $"),
                ("(?s)^a$", "carries an inline flag"),
                ("(?i)^a$", "carries an inline flag")):
            with self.subTest(pattern=pattern):
                self.assertTrue(any(expected in problem
                                    for problem in anchoring_problems(pattern)),
                                anchoring_problems(pattern))

    def test_the_anchoring_property_accepts_the_shapes_the_fixture_uses(self):
        for pattern in ("^a$", "^a[^\\n]*(b|c)\\b[^\\n]*$", "^\\|[^|\\s][^|\\n]*\\|$",
                        "^(?!#)[^\\n]*(?<![0-9])0002(?![0-9])[^\\n]*$",
                        "^a\\.\n\nb[|]c$", "^a$|^b$"):
            with self.subTest(pattern=pattern):
                self.assertEqual(anchoring_problems(pattern), [])

    # ---- the fixture file itself ----------------------------------------------

    def test_only_existing_check_types_and_keys_are_used(self):
        for check in self.checks.values():
            with self.subTest(check=check["id"]):
                self.assertIn(check["type"], objective.CHECKS)
                allowed = (objective._CHECK_META_KEYS
                           | objective._CHECK_ALLOWED_KEYS.get(check["type"], set()))
                self.assertEqual(set(check) - allowed, set())

    def test_models_are_pinned_as_the_other_writing_adrs_fixtures_pin_them(self):
        for sibling in SIBLING_FIXTURES:
            with self.subTest(sibling=sibling.name):
                other = run_eval.load_fixture(sibling)
                self.assertEqual(self.fixture["skill"], other["skill"])
                self.assertEqual(self.fixture["registry"], other["registry"])
                self.assertEqual(self.fixture["arms"], other["arms"])
                # Every writing-adrs fixture follows the roster.
                self.assertNotIn("model", other)
                self.assertNotIn("model", other["judge"])
                self.assertNotIn("model", self.fixture)
                self.assertNotIn("model", self.fixture["judge"])

    def test_judge_weights_sum_to_one_and_name_the_rubrics_dimensions(self):
        weights = self.fixture["judge"]["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        named = re.findall(r"\((\d)\) ([a-z0-9-]+) —", self.fixture["judge_rubric"])
        self.assertEqual([name for _, name in named], list(weights))
        self.assertEqual([number for number, _ in named], ["1", "2", "3"])

    def test_the_prompt_is_the_sibling_prompt_with_only_the_subject_changed(self):
        # An allowlist of one shape: nothing in it but a subject noun, so it
        # cannot name a rule, a file or a number.
        prompt = self.fixture["prompt"].strip()
        self.assertRegex(prompt, r"\ARecord the [a-z]+(-[a-z]+)* decision properly\.\Z")
        for sibling in SIBLING_FIXTURES:
            other = run_eval.load_fixture(sibling)["prompt"].strip()
            self.assertRegex(other, r"\ARecord the [a-z]+(-[a-z]+)* decision properly\.\Z")

    # ---- the seed does not announce the answer ---------------------------------

    def test_the_seed_is_exactly_these_files(self):
        files = sorted(str(p.relative_to(SEED)) for p in SEED.rglob("*") if p.is_file())
        self.assertEqual(files, [
            "AGENTS.md", "CHANGELOG.md", "README.md",
            "docs/decisions/0001-take-snapshots-with-pg-basebackup.md",
            "docs/decisions/0002-prune-snapshots-older-than-30-days.md",
            "docs/decisions/0003-store-snapshots-on-a-separate-volume.md",
            "docs/decisions/README.md", "scripts/prune-snapshots.sh"])

    def test_the_seed_never_says_supersede_outside_the_status_vocabulary(self):
        # One line in the seed may carry the word: the README's list of
        # status values, which is the repo's format, not an instruction.
        hits = []
        for path in sorted(p for p in SEED.rglob("*") if p.is_file()):
            for line in path.read_text(encoding="utf-8").splitlines():
                if re.search(r"supersed|0004", line, re.IGNORECASE):
                    hits.append((str(path.relative_to(SEED)), line))
        self.assertEqual(hits, [(
            "docs/decisions/README.md",
            "- **Status:** `Proposed`, `Accepted`, `Superseded by NNNN`, or `Rejected`.")])

    def test_the_seed_script_is_executable(self):
        self.assertTrue(os.access(SEED / SCRIPT, os.X_OK))
