    def test_secret_named_entry_fails_and_a_purpose_name_passes(self):
        fixture = self._fixture("name-becomes-scanner-data", "bundle-lock-name")
        ws = workspace(self, fixture)
        sh(ws, sys.executable, "scripts/lock.py", "add", "deploy-secrets", "bundles/new-bundle")
        self.assertEqual(score(fixture, ws), {"bundle-pinned": True,
                                              "name-is-not-a-scanner-keyword": False,
                                              "no-scanner-allowlist": True})
        (ws / ".gitleaksignore").write_text("abc123:bundles.lock:generic-api-key:4\n",
                                            encoding="utf-8")
        self.assertEqual(score(fixture, ws)["no-scanner-allowlist"], False)
        ws = workspace(self, fixture)
        sh(ws, sys.executable, "scripts/lock.py", "add", "deploy-provisioning",
           "bundles/new-bundle")
        self.assertEqual(set(score(fixture, ws).values()), {True})
