    def test_done_summary_needs_every_link(self):
        fixture = self.LINK / "done-summary"
        ws = self._write(fixture, "DONE.md", f"{self.U}/pull/38 and {self.U}/pull/41 merged.\n")
        self.assertEqual(score(fixture, ws), {"merged-pull-requests-linked": True,
                                              "closed-issue-linked": False,
                                              "release-run-linked": False})
        ws = self._write(fixture, "DONE.md", f"{self.U}/pull/38, {self.U}/pull/41, "
                         f"{self.U}/issues/9, {self.U}/actions/runs/9099001\n")
        self.assertEqual(set(score(fixture, ws).values()), {True})
