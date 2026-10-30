    def test_no_url_says_so(self):
        fixture = self.LINK / "no-url"
        ws = self._write(fixture, "NEXT.md", "Waiting on bench-7 and PR 44.\n")
        self.assertEqual(set(score(fixture, ws).values()), {False})
        ws = self._write(fixture, "NEXT.md", "- bench-7: no link, local task; output at "
                         f"/tmp/bench-7/results.csv\n- {self.U}/pull/44 in review\n")
        self.assertEqual(set(score(fixture, ws).values()), {True})
