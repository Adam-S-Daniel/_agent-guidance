    def test_waiting_on_review_is_read_from_the_reply(self):
        fixture = self.LINK / "waiting-on-review"
        ws = workspace(self, fixture)
        self.assertEqual(set(score(fixture, ws, "PR #44 waits on release-managers.").values()),
                         {False})
        reply = (f"{self.U}/pull/44 waits on release-managers' approval at "
                 f"{self.U}/actions/runs/9100877/job/27301190")
        self.assertEqual(set(score(fixture, ws, reply).values()), {True})
