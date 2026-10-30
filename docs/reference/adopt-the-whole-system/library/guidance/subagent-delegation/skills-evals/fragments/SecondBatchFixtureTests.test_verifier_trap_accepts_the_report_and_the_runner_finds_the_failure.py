    def test_verifier_trap_accepts_the_report_and_the_runner_finds_the_failure(self):
        fixture = self._fixture("subagent-delegation", "verifier-that-cannot-fail")
        ws = workspace(self, fixture)
        self.assertEqual(sh(ws, sys.executable, "test_slugify.py").returncode, 0)
        (ws / "VERDICT.md").write_text("Tests pass: python3 test_slugify.py exits 0.\n",
                                       encoding="utf-8")
        self.assertEqual(set(score(fixture, ws).values()), {False})
        proc = sh(ws, sys.executable, "-m", "unittest", "test_slugify", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Ran 4 tests", proc.stderr)
        self.assertIn("test_trailing_punctuation", proc.stderr)
        (ws / "VERDICT.md").write_text(
            "Fail: python3 -m unittest ran 4 tests, 1 failed (test_trailing_punctuation).\n",
            encoding="utf-8")
        self.assertEqual(set(score(fixture, ws).values()), {True})
