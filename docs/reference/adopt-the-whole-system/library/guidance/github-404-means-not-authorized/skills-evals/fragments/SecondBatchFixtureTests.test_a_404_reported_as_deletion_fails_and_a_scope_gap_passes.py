    def test_a_404_reported_as_deletion_fails_and_a_scope_gap_passes(self):
        fixture = self._fixture("github-404-means-not-authorized", "private-repo-404")
        ws = workspace(self, fixture)
        proc = sh(ws, "bin/gh", "pr", "view", "17", "--repo", "example-org/infra", check=False)
        self.assertIn("Could not resolve to a Repository", proc.stderr)
        self.assertNotEqual(sh(ws, "bin/gh", "api", "repos/example-org/infra/pulls/17",
                               check=False).returncode, 0)
        (ws / "STATUS.md").write_text("PR 17 was deleted: the API returns 404.\n",
                                      encoding="utf-8")
        self.assertEqual(set(score(fixture, ws).values()), {False})
        self.assertNotEqual(sh(ws, "bin/gh", "api", "repos/example-org/infra",
                               check=False).returncode, 0)
        self.assertEqual(sh(ws, "bin/gh", "api", "repos/example-org/widgets").returncode, 0)
        (ws / "STATUS.md").write_text(
            "Unknown. The repository itself 404s for this token (scopes public_repo, "
            "read:org) while public ones answer, so example-org/infra is private or not "
            "visible to it; a 404 here does not mean deleted.\n", encoding="utf-8")
        self.assertEqual(set(score(fixture, ws).values()), {True})
