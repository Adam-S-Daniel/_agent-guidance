    def test_changelog_check(self):
        fixture = GUIDANCE_EVALS / "finding-your-unknowns" / "changelog-check"
        ws = workspace(self, fixture)
        (ws / "AGENTS.md").write_text("Always update CHANGELOG.md when you change src/.\n",
                                      encoding="utf-8")
        self.assertEqual(set(score(fixture, ws).values()), {False})
        (ws / ".agent-checks/local.sh").write_text(
            "local_checks() {\n  if grep -q '^src/' <<<\"$STAGED\" && "
            "! grep -qx 'CHANGELOG.md' <<<\"$STAGED\"; then\n"
            "    fail \"src/ changed without a CHANGELOG.md entry\"\n  fi\n}\n", encoding="utf-8")
        self.assertEqual(set(score(fixture, ws).values()), {True})
        # The check really gates a commit through the seed's own runner.
        with open(ws / "src/app.py", "a", encoding="utf-8") as f:
            f.write("# changed\n")
        sh(ws, "git", "add", "src/app.py")
        self.assertNotEqual(sh(ws, "bash", ".githooks/agent-checks", check=False).returncode, 0)
        with open(ws / "CHANGELOG.md", "a", encoding="utf-8") as f:
            f.write("- Change app.\n")
        sh(ws, "git", "add", "CHANGELOG.md")
        self.assertEqual(sh(ws, "bash", ".githooks/agent-checks", check=False).returncode, 0)
