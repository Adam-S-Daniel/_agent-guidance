    def test_handoff_with_bare_numbers_fails_and_with_links_passes(self):
        fixture = self.LINK / "handoff"
        ws = self._write(fixture, "HANDOFF.md",
                         "Done: #41. Blocked: #44 waits on the production review. Next: #12.\n")
        self.assertEqual(set(score(fixture, ws).values()), {False})
        ws = self._write(fixture, "HANDOFF.md",
                         f"Done: [PR 41]({self.U}/pull/41).\nBlocked: [PR 44]({self.U}/pull/44)"
                         f" waits on the [production-approval review]({self.U}/actions/runs/"
                         f"9100123/job/27300456).\nNext: [issue 12]({self.U}/issues/12).\n")
        self.assertEqual(set(score(fixture, ws).values()), {True})
