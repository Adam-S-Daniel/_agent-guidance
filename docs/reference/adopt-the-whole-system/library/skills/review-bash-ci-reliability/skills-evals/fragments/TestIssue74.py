class TestIssue74(unittest.TestCase):
    """The review-bash-ci-reliability fixture's checks (issue #74).

    Every real-finding check must fail on the pristine seed and pass once
    that finding is hand-fixed; every decoy check must PASS on both the
    pristine seed and a hand-fixed copy (the decoys are correct as shipped),
    and FAIL on a copy where the decoy itself was incorrectly "fixed" — that
    is what proves the decoy checks actually have teeth, not just that they
    happen to pass.

    Round 1 of review found that several checks overfit to one exact
    spelling of a correct fix (the skill itself prescribes more than one
    remedy per finding) and that several must_not_match patterns fired on
    `# was: ...` comments quoting the old, now-dead code rather than only on
    live code. The tests below (marked B1-B6, S1-S4 to match the review
    items) pin the *properties* each check is supposed to enforce, not one
    hand-written spelling — using both this class's direct
    objective.run_checks() path and, for the reviewer's named copies, the
    real `run_eval.py --arm objective-only` CLI.
    """

    REAL_FINDING_IDS = (
        "process-substitution-error-propagates",
        "grep-q-avoids-broken-pipe",
        "gh-api-failure-not-swallowed",
        "git-identity-configured",
        "jq-guaranteed-or-replaced",
        "version-read-does-not-depend-on-unguarded-jq",
        "commit-signing-safe-for-ci",
    )
    DECOY_IDS = ("decoy-optional-cleanup-untouched", "decoy-existing-set-e-untouched")
    # Exactly the ids the judge_rubric's Restraint-dimension cap sentence
    # names (pinned by test_restraint_ids_matches_the_ids_the_rubric_names)
    # — deliberately NOT workflow-yaml-parses, which a within-scope but
    # botched edit can trip without any overreach, so it is not itself a
    # restraint violation even though it also starts green on the pristine
    # seed (see test_pristine_seed_passes_the_restraint_checks).
    RESTRAINT_IDS = ("workflow-file-present",) + DECOY_IDS

    GH_API_LINE = ('out=$(gh api "repos/${REPO}/pulls?state=merged" '
                  '--jq \'.[].title\') || true')
    GH_API_FIXED_BLOCK = (
        'if ! out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\'); then\n'
        '    echo "ERROR: gh api call failed" >&2\n'
        '    exit 1\n'
        'fi')
    JQ_LINE = "VERSION=$(jq -r '.version' package.json)"
    JQ_FIXED_LINE = ("VERSION=$(grep -m1 '\"version\"' package.json | "
                     "sed -E 's/.*\"version\":[[:space:]]*\"([^\"]+)\".*/\\1/')")

    def _ws(self) -> Path:
        ws = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
        shutil.copytree(BASH_CI_DIR / "seed", ws, dirs_exist_ok=True)
        return ws

    def _run(self, ws: Path) -> dict:
        fixture = run_eval.load_fixture(BASH_CI_DIR)
        results = objective.run_checks(fixture, str(ws), str(BASH_CI_DIR / "seed"))
        return {r["id"]: r for r in results}

    def _run_cli(self, ws: Path) -> tuple[int, dict]:
        cmd = [sys.executable, str(HARNESS_DIR / "run_eval.py"), str(BASH_CI_DIR),
              "--arm", "objective-only", "--workspace", str(ws)]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return proc.returncode, payload

    # -- hand fixes, one per real finding, mirroring the skill's own remedy --

    def _fix_publish(self, ws: Path) -> None:
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'mapfile -t WATCH_LOG < <(gh run watch "$RUN_ID" | tail -n 5)',
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)')
        text = text.replace(
            'echo "$build_log" | grep -q "Successfully published"',
            'grep -q "Successfully published" <<< "$build_log"')
        path.write_text(text, encoding="utf-8")

    def _fix_collect(self, ws: Path) -> None:
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(self.GH_API_LINE, self.GH_API_FIXED_BLOCK)
        path.write_text(text, encoding="utf-8")

    def _fix_bump(self, ws: Path) -> None:
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(self.JQ_LINE, self.JQ_FIXED_LINE)
        text = text.replace(
            "git add package.json",
            'git config --local user.email "release-bot@example.com"\n'
            'git config --local user.name "release-bot"\n'
            'git config --local commit.gpgsign false\n'
            'git add package.json')
        path.write_text(text, encoding="utf-8")

    def _fix_all(self, ws: Path) -> None:
        self._fix_publish(ws)
        self._fix_collect(ws)
        self._fix_bump(ws)

    # -- reviewer's named copies (round-1 review, real runner on 15 hand-built
    # workspaces): A and L are correct fixes using alternate valid forms; I,
    # K, O are plausible-looking wrong fixes that must still fail. --

    def _apply_copy_A(self, ws: Path) -> None:
        """Independent, skill-faithful hand-fix using different (but equally
        valid) forms than _fix_all's: --global instead of --local, the
        skill's own `|| { ...; exit 1; }` snippet, jq kept and installed in
        the workflow instead of replaced, and ${PIPESTATUS[0]} instead of a
        plain command-substitution capture."""
        publish = ws / "scripts" / "publish.sh"
        text = publish.read_text(encoding="utf-8")
        text = text.replace(
            'mapfile -t WATCH_LOG < <(gh run watch "$RUN_ID" | tail -n 5)',
            'gh run watch "$RUN_ID" | tee "/tmp/watch-log.$$" > /dev/null\n'
            'watch_status="${PIPESTATUS[0]}"\n'
            'if [[ "$watch_status" -ne 0 ]]; then\n'
            '    echo "ERROR: gh run watch failed" >&2\n'
            '    exit 1\n'
            'fi\n'
            'mapfile -t WATCH_LOG < <(tail -n 5 "/tmp/watch-log.$$")')
        text = text.replace(
            'echo "$build_log" | grep -q "Successfully published"',
            'grep -q "Successfully published" <<< "$build_log"')
        publish.write_text(text, encoding="utf-8")

        collect = ws / "scripts" / "collect.sh"
        text = collect.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_LINE,
            'out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\') || '
            '{ echo "ERROR: gh api call failed" >&2; exit 1; }')
        collect.write_text(text, encoding="utf-8")

        bump = ws / "scripts" / "bump.sh"
        text = bump.read_text(encoding="utf-8")
        text = text.replace(
            "git add package.json",
            'git config --global user.email "ci@example.com"\n'
            'git config --global user.name "ci-runner"\n'
            'git config --global commit.gpgsign false\n'
            'git add package.json')
        bump.write_text(text, encoding="utf-8")

        workflow = ws / ".github" / "workflows" / "release.yml"
        text = workflow.read_text(encoding="utf-8")
        text = text.replace(
            "      - name: Bump version",
            "      - name: Install jq\n"
            "        run: sudo apt-get update && sudo apt-get install -y jq\n"
            "      - name: Bump version")
        workflow.write_text(text, encoding="utf-8")

    def _apply_copy_L(self, ws: Path) -> None:
        """A correct fix (mirroring _fix_all) that also leaves `# was: ...`
        comments quoting the original buggy lines — proves every
        must_not_match is anchored to a non-comment prefix (B5)."""
        self._fix_all(ws)
        publish = ws / "scripts" / "publish.sh"
        text = publish.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n',
            '# was: mapfile -t WATCH_LOG < <(gh run watch "$RUN_ID" | tail -n 5)\n'
            'watch_output=$(gh run watch "$RUN_ID")\n')
        text = text.replace(
            'grep -q "Successfully published" <<< "$build_log"',
            '# was: echo "$build_log" | grep -q "Successfully published"\n'
            'grep -q "Successfully published" <<< "$build_log"')
        publish.write_text(text, encoding="utf-8")

        collect = ws / "scripts" / "collect.sh"
        text = collect.read_text(encoding="utf-8")
        text = text.replace(
            "if ! out=$(gh api",
            f"# was: {self.GH_API_LINE}\n"
            "if ! out=$(gh api")
        collect.write_text(text, encoding="utf-8")

        bump = ws / "scripts" / "bump.sh"
        text = bump.read_text(encoding="utf-8")
        text = text.replace(
            "VERSION=$(grep -m1",
            f"# was: {self.JQ_LINE}\n"
            "VERSION=$(grep -m1")
        bump.write_text(text, encoding="utf-8")

    def _apply_copy_I(self, ws: Path) -> None:
        """Every finding correctly fixed except finding 1, where the watch's
        failure is captured but then swallowed with `|| true` instead of
        propagated (B1's forbidden dodge)."""
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n',
            'watch_output=$(gh run watch "$RUN_ID") || true\n')
        path.write_text(text, encoding="utf-8")

    def _apply_copy_K(self, ws: Path) -> None:
        """Every finding correctly fixed, but a redundant bare `set -e`
        (with a trailing comment) is re-added next to the already-correct
        `set -euo pipefail` — B6's decoy dodge."""
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace("set -euo pipefail\n",
                            "set -euo pipefail\nset -e  # for extra safety\n")
        path.write_text(text, encoding="utf-8")

    def _apply_copy_O(self, ws: Path) -> None:
        """Every finding correctly fixed except finding 3, where the gh api
        failure is suppressed with `2>/dev/null` and the failure branch only
        warns; an unrelated `exit 1` elsewhere in the file must not be
        mistaken for handling it (B2's forbidden dodge)."""
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_FIXED_BLOCK,
            'out=$(gh api "repos/${REPO}/pulls?state=merged" '
            "--jq '.[].title' 2>/dev/null)\n"
            'if [[ -z "$out" ]]; then\n'
            '    echo "WARNING: gh api call may have failed silently" >&2\n'
            "fi")
        text += "\n# unrelated failure path, unconnected to the gh api call\nexit 1\n"
        path.write_text(text, encoding="utf-8")

    def test_pristine_seed_fails_every_real_finding(self):
        by_id = self._run(self._ws())
        for check_id in self.REAL_FINDING_IDS:
            self.assertFalse(by_id[check_id]["passed"], by_id[check_id]["detail"])

    def test_pristine_seed_passes_the_restraint_checks(self):
        # The restraint checks (workflow-file-present + both decoys) can
        # only be broken by a careless agent, so they must start out
        # green — otherwise a failure here says nothing about the arm
        # under test.
        by_id = self._run(self._ws())
        for check_id in self.RESTRAINT_IDS:
            self.assertTrue(by_id[check_id]["passed"], by_id[check_id]["detail"])

    def test_restraint_ids_matches_the_ids_the_rubric_names(self):
        # Round-5 N1: round 4 named workflow-file-present in the rubric's
        # Restraint-cap sentence alongside the two decoys, but nothing
        # pinned that wording, or that RESTRAINT_IDS (used elsewhere as
        # "must start green on the pristine seed") tracks the same set —
        # either could silently drift from the other. Deliberately excludes
        # workflow-yaml-parses: it also starts green on the pristine seed,
        # but a within-scope, merely botched edit can trip it without any
        # overreach, so it is not one of the ids the Restraint dimension
        # itself caps on.
        fixture = run_eval.load_fixture(BASH_CI_DIR)
        rubric = fixture["judge_rubric"]
        cap_sentence_start = rubric.index("(2) Restraint")
        cap_sentence_end = rubric.index("(3) Explanation")
        restraint_dimension = rubric[cap_sentence_start:cap_sentence_end]
        self.assertIn("decoy", restraint_dimension.lower())
        self.assertIn("workflow-file-present", restraint_dimension)
        named_ids = set(self.DECOY_IDS) | {"workflow-file-present"}
        self.assertEqual(set(self.RESTRAINT_IDS), named_ids)

    def test_hand_fixed_copy_passes_every_check(self):
        ws = self._ws()
        self._fix_all(ws)
        for check_id, result in self._run(ws).items():
            self.assertTrue(result["passed"], f"{check_id}: {result['detail']}")

    def test_decoy_1_incorrectly_fixed_fails_only_its_own_check(self):
        # An agent that mistakes the genuinely-optional cleanup for a bug and
        # strips its `|| true` must fail decoy-optional-cleanup-untouched —
        # and nothing else, proving the check is isolated to that one line.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            'rm -f "$tmp_response" || true  # temp file cleanup; '
            "harmless if it's already gone", text)
        text = text.replace(
            'rm -f "$tmp_response" || true  # temp file cleanup; '
            "harmless if it's already gone",
            'rm -f "$tmp_response"')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["decoy-optional-cleanup-untouched"]["passed"])
        for check_id in self.REAL_FINDING_IDS + ("decoy-existing-set-e-untouched",
                                                  "workflow-yaml-parses"):
            self.assertTrue(by_id[check_id]["passed"], by_id[check_id]["detail"])

    def test_decoy_2_incorrectly_replaced_fails_only_its_own_check(self):
        # An agent that "fixes" the already-correct set -euo pipefail by
        # splitting it back into set -e / set -o pipefail must fail
        # decoy-existing-set-e-untouched — and nothing else.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail\n", text)
        text = text.replace("set -euo pipefail\n", "set -e\nset -o pipefail\n")
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["decoy-existing-set-e-untouched"]["passed"])
        for check_id in self.REAL_FINDING_IDS + ("decoy-optional-cleanup-untouched",
                                                  "workflow-yaml-parses"):
            self.assertTrue(by_id[check_id]["passed"], by_id[check_id]["detail"])

    def test_decoy_2_redundantly_duplicated_fails_its_check(self):
        # A second, over-cautious "fix": re-adding a bare `set -e` alongside
        # the existing (untouched) `set -euo pipefail` line.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace("set -euo pipefail\n", "set -euo pipefail\nset -e\n")
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["decoy-existing-set-e-untouched"]["passed"])

    def test_suppressing_the_commit_failure_instead_of_fixing_identity_fails(self):
        # A plausible-looking wrong fix: silence the exit-128 symptom with
        # `|| true` instead of configuring git identity. Must still fail —
        # otherwise the check can be gamed by the exact anti-pattern the
        # skill's "Commands with Suppressed Errors" item warns against.
        # Identity is fixed FIRST (as _apply_copy_I/O do for their own
        # findings), so it's the dodge pattern itself that's asserted to
        # fail, not the pristine seed's pre-existing missing-identity
        # failure (round-2 review item 5 — this test used to pass vacuously
        # against the untouched pristine seed).
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'git commit -m "chore: bump version to ${NEXT_VERSION}"',
            'git commit -m "chore: bump version to ${NEXT_VERSION}" || true')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["git-identity-configured"]["passed"])

    def test_objective_only_cli_fails_on_pristine_seed(self):
        cmd = [sys.executable, str(HARNESS_DIR / "run_eval.py"), str(BASH_CI_DIR),
              "--arm", "objective-only"]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["skill"], "review-bash-ci-reliability")
        self.assertEqual(payload["arm"], "objective-only")
        by_id = {c["id"]: c for c in payload["checks"]}
        for check_id in self.REAL_FINDING_IDS:
            self.assertFalse(by_id[check_id]["passed"])
        for check_id in self.RESTRAINT_IDS:
            self.assertTrue(by_id[check_id]["passed"])

    def test_objective_only_cli_passes_on_a_hand_fixed_copy(self):
        ws = self._ws()
        self._fix_all(ws)
        returncode, _ = self._run_cli(ws)
        self.assertEqual(returncode, 0)

    # -- B1: process-substitution-error-propagates must accept any of the
    # skill's remedies (a named-variable capture, PIPESTATUS, or no pipe at
    # all), and must reject the `|| true`/`|| :` dodge on the capture. --

    def test_b1_watch_captured_into_any_variable_name_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace("watch_output=", "watch_raw=")
        text = text.replace('"$watch_output"', '"$watch_raw"')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["process-substitution-error-propagates"]["passed"],
                        by_id["process-substitution-error-propagates"]["detail"])

    def test_b1_pipestatus_remedy_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)',
            'gh run watch "$RUN_ID" | tee "/tmp/watch-log.$$" > /dev/null\n'
            'watch_status="${PIPESTATUS[0]}"\n'
            'if [[ "$watch_status" -ne 0 ]]; then\n'
            '    echo "ERROR: gh run watch failed" >&2\n'
            '    exit 1\n'
            'fi\n'
            'mapfile -t WATCH_LOG < <(tail -n 5 "/tmp/watch-log.$$")')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["process-substitution-error-propagates"]["passed"],
                        by_id["process-substitution-error-propagates"]["detail"])

    def test_b1_no_pipe_remedy_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)',
            'gh run watch "$RUN_ID"\n'
            'mapfile -t WATCH_LOG < <(gh run view "$RUN_ID" --log | tail -n 5)')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["process-substitution-error-propagates"]["passed"],
                        by_id["process-substitution-error-propagates"]["detail"])

    # -- B2: gh-api-failure-not-swallowed must accept the skill's own SAFE
    # snippet and the simplest delete-the-suppression fix, and must reject
    # `|| :` / `2>/dev/null` / `2> /dev/null` on the gh api line. --

    def test_b2_skill_safe_snippet_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_FIXED_BLOCK,
            'out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\') || '
            '{ echo "ERROR: gh api call failed" >&2; exit 1; }')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["gh-api-failure-not-swallowed"]["passed"],
                        by_id["gh-api-failure-not-swallowed"]["detail"])

    def test_b2_simplest_fix_bare_assignment_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_FIXED_BLOCK,
            'out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\')')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["gh-api-failure-not-swallowed"]["passed"],
                        by_id["gh-api-failure-not-swallowed"]["detail"])

    def test_b2_colon_and_spaced_redirect_dodges_fail(self):
        dodges = (
            'out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\') || :',
            'out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\' 2> /dev/null)',
        )
        for dodge in dodges:
            with self.subTest(dodge=dodge):
                ws = self._ws()
                self._fix_all(ws)
                path = ws / "scripts" / "collect.sh"
                text = path.read_text(encoding="utf-8")
                text = text.replace(self.GH_API_FIXED_BLOCK, dodge)
                path.write_text(text, encoding="utf-8")
                by_id = self._run(ws)
                self.assertFalse(by_id["gh-api-failure-not-swallowed"]["passed"])

    # -- B3: git-identity-configured must accept --global (SKILL.md's own
    # example), not just --local. --

    def test_b3_global_git_config_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'git config --local user.email "release-bot@example.com"',
            'git config --global user.email "release-bot@example.com"')
        text = text.replace(
            'git config --local user.name "release-bot"',
            'git config --global user.name "release-bot"')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["git-identity-configured"]["passed"],
                        by_id["git-identity-configured"]["detail"])

    # -- B4: version-read-does-not-depend-on-jq must accept "installed" as
    # well as "replaced", must still reject jq kept-and-not-installed, and
    # must not be satisfiable by deleting bump.sh outright. --

    def test_b4_jq_kept_and_installed_in_workflow_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        bump = ws / "scripts" / "bump.sh"
        text = bump.read_text(encoding="utf-8")
        text = text.replace(self.JQ_FIXED_LINE, self.JQ_LINE)
        bump.write_text(text, encoding="utf-8")
        workflow = ws / ".github" / "workflows" / "release.yml"
        text = workflow.read_text(encoding="utf-8")
        text = text.replace(
            "      - name: Bump version",
            "      - name: Install jq\n"
            "        run: sudo apt-get update && sudo apt-get install -y jq\n"
            "      - name: Bump version")
        workflow.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        for check_id in ("jq-guaranteed-or-replaced", "version-read-does-not-depend-on-unguarded-jq"):
            self.assertTrue(by_id[check_id]["passed"], by_id[check_id]["detail"])

    def test_b4_jq_kept_and_not_installed_fails(self):
        # Reviewer's copy E.
        ws = self._ws()
        self._fix_all(ws)
        bump = ws / "scripts" / "bump.sh"
        text = bump.read_text(encoding="utf-8")
        text = text.replace(self.JQ_FIXED_LINE, self.JQ_LINE)
        bump.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        for check_id in ("jq-guaranteed-or-replaced", "version-read-does-not-depend-on-unguarded-jq"):
            self.assertFalse(by_id[check_id]["passed"])

    def test_b4_deleting_bump_sh_outright_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        (ws / "scripts" / "bump.sh").unlink()
        by_id = self._run(ws)
        # jq-guaranteed-or-replaced trivially passes (no jq mention anywhere
        # once bump.sh is gone) — it's version-read-does-not-depend-on-
        # unguarded-jq's must_match that catches the deleted version-read
        # logic, so deleting bump.sh cannot pass by leaving nothing to
        # violate.
        self.assertFalse(by_id["version-read-does-not-depend-on-unguarded-jq"]["passed"])

    # -- item 2 (round-2 review): the old single jq check decided "is jq
    # installed" via a forward-only lookahead from the usage position, so
    # order (and SKILL.md's own guard-and-exit remedy, which has no install
    # marker at all) broke it. The two replacement checks are existence-only
    # and order-independent; the two tests below prove that. --

    def _bump_with_jq_guard(self, ws: Path, guard_line: str, guard_before: bool) -> None:
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        if guard_before:
            text = text.replace(self.JQ_LINE, f"{guard_line}\n{self.JQ_LINE}")
        else:
            text = text.replace(self.JQ_LINE, f"{self.JQ_LINE}\n{guard_line}")
        path.write_text(text, encoding="utf-8")

    def test_jq_guard_install_correctly_ordered_before_usage_passes(self):
        ws = self._ws()
        self._fix_collect(ws)
        self._fix_bump(ws)
        text = (ws / "scripts" / "bump.sh").read_text(encoding="utf-8")
        text = text.replace(self.JQ_FIXED_LINE, self.JQ_LINE)
        (ws / "scripts" / "bump.sh").write_text(text, encoding="utf-8")
        self._bump_with_jq_guard(
            ws, 'command -v jq >/dev/null || sudo apt-get install -y jq', guard_before=True)
        self._fix_publish(ws)
        by_id = self._run(ws)
        for check_id in ("jq-guaranteed-or-replaced", "version-read-does-not-depend-on-unguarded-jq"):
            self.assertTrue(by_id[check_id]["passed"], by_id[check_id]["detail"])

    def test_jq_guard_install_wrong_order_after_usage_is_left_to_the_judge(self):
        # The lexical check cannot see order, so this passes lexically either
        # way — order correctness is the judge's call, not asserted here.
        ws = self._ws()
        self._fix_collect(ws)
        self._fix_bump(ws)
        text = (ws / "scripts" / "bump.sh").read_text(encoding="utf-8")
        text = text.replace(self.JQ_FIXED_LINE, self.JQ_LINE)
        (ws / "scripts" / "bump.sh").write_text(text, encoding="utf-8")
        self._bump_with_jq_guard(
            ws, 'command -v jq >/dev/null || sudo apt-get install -y jq', guard_before=False)
        self._fix_publish(ws)
        by_id = self._run(ws)
        for check_id in ("jq-guaranteed-or-replaced", "version-read-does-not-depend-on-unguarded-jq"):
            self.assertTrue(by_id[check_id]["passed"], by_id[check_id]["detail"])

    def test_jq_skill_guard_and_exit_form_passes(self):
        # SKILL.md item 6's own remedy: guard-and-exit, no install marker at
        # all. The old check flagged this as an unguarded usage since it has
        # no install/uses: marker; it must pass.
        ws = self._ws()
        self._fix_collect(ws)
        self._fix_bump(ws)
        text = (ws / "scripts" / "bump.sh").read_text(encoding="utf-8")
        text = text.replace(self.JQ_FIXED_LINE, self.JQ_LINE)
        (ws / "scripts" / "bump.sh").write_text(text, encoding="utf-8")
        self._bump_with_jq_guard(
            ws, 'command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }',
            guard_before=True)
        self._fix_publish(ws)
        by_id = self._run(ws)
        for check_id in ("jq-guaranteed-or-replaced", "version-read-does-not-depend-on-unguarded-jq"):
            self.assertTrue(by_id[check_id]["passed"], by_id[check_id]["detail"])

    # -- B5: every must_not_match must ignore comment text. --

    def test_b5_was_comments_do_not_trip_must_not_match_checks(self):
        ws = self._ws()
        self._apply_copy_L(ws)
        by_id = self._run(ws)
        for check_id, result in by_id.items():
            self.assertTrue(result["passed"], f"{check_id}: {result['detail']}")

    # -- B6: the seed no longer names the decoy rule outright, and the decoy
    # check must still catch a duplicated `set -e` even with a trailing
    # comment. --

    def test_b6_seed_no_longer_instructs_against_reintroducing_set_e(self):
        text = (BASH_CI_DIR / "seed" / "scripts" / "publish.sh").read_text(encoding="utf-8")
        self.assertNotIn("should not be re-added or duplicated", text)
        self.assertNotIn("earlier revisions of this script forgot", text)

    def test_b6_set_e_duplicate_with_trailing_comment_fails(self):
        # Reviewer's copy K.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace("set -euo pipefail\n",
                            "set -euo pipefail\nset -e  # for extra safety\n")
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["decoy-existing-set-e-untouched"]["passed"])
        for check_id in self.REAL_FINDING_IDS + ("decoy-optional-cleanup-untouched",
                                                  "workflow-yaml-parses"):
            self.assertTrue(by_id[check_id]["passed"], by_id[check_id]["detail"])

    # -- S1: the decoy match is command-only (comment wording is free), and
    # the seed's tmp_response is a file the cleanup line genuinely wrote. --

    def test_s1_decoy_match_ignores_comment_wording(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'rm -f "$tmp_response" || true  # temp file cleanup; '
            "harmless if it's already gone",
            'rm -f "$tmp_response" || true  # nothing to do if this was never written')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["decoy-optional-cleanup-untouched"]["passed"],
                        by_id["decoy-optional-cleanup-untouched"]["detail"])

    def test_s1_seed_actually_writes_the_temp_file_before_cleaning_it_up(self):
        text = (BASH_CI_DIR / "seed" / "scripts" / "collect.sh").read_text(encoding="utf-8")
        self.assertIn('> "$tmp_response"', text)

    # -- S2: checklist item 5 (commit signing in CI) has a shape and a check. --

    def test_s2_signingkey_remedy_also_satisfies_commit_signing_check(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace("git config --local commit.gpgsign false\n", "")
        text = text.replace(
            "git add package.json",
            'git config --local user.signingkey "0xDEADBEEF"\n'
            "git add package.json")
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["commit-signing-safe-for-ci"]["passed"],
                        by_id["commit-signing-safe-for-ci"]["detail"])

    # -- S3: pin the discriminating power of checks 1, 2, 3 and 5 through the
    # real runner, using the reviewer's named copies. --

    def test_s3_copy_a_skill_faithful_hand_fix_passes(self):
        ws = self._ws()
        self._apply_copy_A(ws)
        returncode, payload = self._run_cli(ws)
        self.assertEqual(returncode, 0, payload)

    def test_s3_copy_l_comments_plus_correct_fix_passes(self):
        ws = self._ws()
        self._apply_copy_L(ws)
        returncode, payload = self._run_cli(ws)
        self.assertEqual(returncode, 0, payload)

    def test_s3_copy_i_watch_dodge_fails(self):
        ws = self._ws()
        self._apply_copy_I(ws)
        returncode, payload = self._run_cli(ws)
        self.assertEqual(returncode, 1)
        by_id = {c["id"]: c for c in payload["checks"]}
        self.assertFalse(by_id["process-substitution-error-propagates"]["passed"])

    def test_s3_copy_k_set_e_duplicate_fails(self):
        ws = self._ws()
        self._apply_copy_K(ws)
        returncode, payload = self._run_cli(ws)
        self.assertEqual(returncode, 1)
        by_id = {c["id"]: c for c in payload["checks"]}
        self.assertFalse(by_id["decoy-existing-set-e-untouched"]["passed"])

    def test_s3_copy_o_gh_api_warn_only_dodge_fails(self):
        ws = self._ws()
        self._apply_copy_O(ws)
        returncode, payload = self._run_cli(ws)
        self.assertEqual(returncode, 1)
        by_id = {c["id"]: c for c in payload["checks"]}
        self.assertFalse(by_id["gh-api-failure-not-swallowed"]["passed"])

    # -- Round-2 review item 1: gh-api-failure-not-swallowed lost its
    # must_match in the round-1 fix commit, so three dodges passed: masking
    # the failure with `|| echo ""` instead of `|| true`, wrapping the call
    # in `set +e` / `set -e` to disable errexit around it, and deleting the
    # call outright. must_match now requires the call itself to survive, and
    # the forbidden set covers `|| echo` and `set +e` too. --

    def test_gh_api_or_echo_dodge_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_FIXED_BLOCK,
            'out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\' || echo "")')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["gh-api-failure-not-swallowed"]["passed"])

    def test_gh_api_set_plus_e_wrapper_dodge_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_FIXED_BLOCK,
            'set +e\n'
            'out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\')\n'
            'set -e')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["gh-api-failure-not-swallowed"]["passed"])

    def test_gh_api_deleted_outright_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(self.GH_API_FIXED_BLOCK, 'out=""')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["gh-api-failure-not-swallowed"]["passed"])

    def test_gh_api_unrelated_or_out_empty_elsewhere_still_passes(self):
        # Proves the widened forbidden patterns stay anchored to the gh api
        # line itself: an unrelated `|| out=""` fallback for a different
        # command, coincidentally reusing the name "out", must not trip it.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text += '\nfallback=$(echo unrelated) || out=""\n'
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["gh-api-failure-not-swallowed"]["passed"],
                        by_id["gh-api-failure-not-swallowed"]["detail"])

    # -- Round-2 review item 3: process-substitution-error-propagates evaded
    # detection two ways: a space after `<(` dodged the must_not_match regex
    # while the pipe-free line satisfied a must_match alternative, and
    # deleting the call while leaving a comment mentioning "gh run watch"
    # also satisfied that same alternative. --

    def test_process_substitution_space_after_paren_still_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)',
            'mapfile -t WATCH_LOG < <( gh run watch "$RUN_ID")')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["process-substitution-error-propagates"]["passed"])

    def test_process_substitution_removed_call_with_comment_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)',
            '# removed the gh run watch call\n'
            'WATCH_LOG=()')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["process-substitution-error-propagates"]["passed"])

    # -- Round-2 review item 4 (round-1 B2, still PARTIAL): must_not_match
    # was only anchored on the side before the anchor token, so a TRAILING
    # comment on an already-fixed live line ("out=$(gh api ...)  # dropped
    # the || true: ...") still tripped it, since [^\n]* between the token and
    # the forbidden text could cross into the comment. Both sides are now
    # [^#\n]*, so the match can't reach past a live line's own `#`. --

    def test_trailing_comment_on_fixed_gh_run_watch_line_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n',
            'watch_output=$(gh run watch "$RUN_ID")  '
            '# dropped the pipe: subshell errors were invisible to set -e\n')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["process-substitution-error-propagates"]["passed"],
                        by_id["process-substitution-error-propagates"]["detail"])

    def test_trailing_comment_on_fixed_gh_api_line_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_FIXED_BLOCK,
            'out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\')'
            '  # dropped the || true: a failed call must abort')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["gh-api-failure-not-swallowed"]["passed"],
                        by_id["gh-api-failure-not-swallowed"]["detail"])

    def test_trailing_comment_on_fixed_git_commit_line_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'git commit -m "chore: bump version to ${NEXT_VERSION}"',
            'git commit -m "chore: bump version to ${NEXT_VERSION}"'
            '  # dropped the || true: a failed commit must abort')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["git-identity-configured"]["passed"],
                        by_id["git-identity-configured"]["detail"])

    # -- Round-3 review B1/S2/S3: must_match was never anchored the way
    # must_not_match was, so a comment quoting the fix (or the bug) could
    # satisfy or dodge a check with no live code present at all. Rather than
    # pin one more hand-written scenario, this asserts the PROPERTY every
    # alternative is now supposed to have: parse the fixture, split each
    # must_match/must_not_match pattern into its top-level alternatives (a
    # nested group's own `|`, and a literal `|` inside a character class
    # like `[^#\n|]`, are not split points — see _split_top_level_alternatives
    # for how a naked top-level alternation and a `(?:...)`-wrapped one are
    # each handled), and require every alternative that asserts something
    # about one line of live code — as opposed to the handful of
    # whole-document `\A...\Z` / `(?=...)` existence checks used by the jq
    # checks, which are already anchored per-line internally and are exempted
    # here — to both start with a non-comment-prefix anchor (`^[^#\n]*` or
    # the pipe-excluding `^[^#\n|]*`) and contain no unanchored run (a bare
    # `.*`, a `[^\n]*`, or any other `[^...]*` whose negated set omits `#`)
    # that would let the match run on into a trailing comment — after
    # stripping a deliberate, optional trailing-comment-tail group like decoy
    # 2's own `(\s*#.*)?$`, which is not itself a violation. Round 4 found
    # this property test blind to an unwrapped top-level alternation (it
    # only ever split a fully-*wrapped* pattern) and to a bare `.*`
    # (UNANCHORED_RUN_RE only matched a literal `[^...]*` class); both gaps
    # are closed in the shared `_anchoring_problems`/`_split_top_level_alternatives`
    # helpers above, exercised directly by the mutation tests below.
    def test_every_shell_check_alternative_is_anchored_to_non_comment_text(self):
        # Self-referential: pins this test's own name against the fixture
        # header's citation of it (round-4 review N2) so a rename of this
        # method without updating the header is caught, rather than the two
        # silently drifting apart.
        header = (BASH_CI_DIR / "fixture.yaml").read_text(encoding="utf-8")
        self.assertIn(self._testMethodName, header)

        fixture = run_eval.load_fixture(BASH_CI_DIR)
        problems = []
        for check in fixture["objective_checks"]:
            if check["type"] != "file_matches":
                continue
            for field in ("must_match", "must_not_match"):
                for pattern in check.get(field, []):
                    problems.extend(
                        _anchoring_problems(f"{check['id']}.{field}", pattern))
        self.assertEqual(problems, [], "\n".join(problems))

    # -- Round-4 review B1: mutation tests pinning the property test's own
    # logic against synthetic patterns, independent of whatever the real
    # fixture happens to contain right now. Each of these must turn red
    # under the OLD (round-3) implementation and green under the fix. --

    def test_property_catches_unwrapped_alternation_with_unanchored_alternative(self):
        problems = _anchoring_problems(
            "synthetic.must_match", r"^[^#\n]*A|B")
        self.assertTrue(problems, "unwrapped alternation with an unanchored "
                        "second alternative must be flagged")

    def test_property_catches_dot_star_rewrite(self):
        problems = _anchoring_problems(
            "synthetic.must_match", r"^[^#\n]*version=.*package\.json")
        self.assertTrue(problems, "a bare .* run must be flagged even "
                        "though it is not a [^...]* class")

    def test_property_catches_negated_class_missing_newline_exclusion(self):
        problems = _anchoring_problems(
            "synthetic.must_match", r"^[^#\n]*version=[^\n]*package\.json")
        self.assertTrue(problems, "[^\\n]* (no '#' in its negated set) must "
                        "be flagged the same as any other unanchored run")

    def test_property_catches_dropped_prefix_anchor_inside_wrapped_group(self):
        problems = _anchoring_problems(
            "synthetic.must_match",
            r"(^[^#\n]*git config user\.email[^#\n]*|-c\s+user\.email[^#\n]*)")
        self.assertTrue(problems, "the second alternative in a wrapped "
                        "group must still be checked for its own anchor")

    def test_property_accepts_properly_anchored_wrapped_alternation(self):
        problems = _anchoring_problems(
            "synthetic.must_match",
            r"(^[^#\n]*git config user\.email[^#\n]*|^[^#\n]*-c\s+user\.email[^#\n]*)")
        self.assertEqual(problems, [])

    def test_property_ignores_deliberate_comment_tail_group(self):
        # decoy 2's own shape: an explicitly optional trailing comment is
        # not itself an unanchored run to flag.
        problems = _anchoring_problems(
            "synthetic.must_match", r"^[^#\n]*set -euo pipefail(\s*#.*)?$")
        self.assertEqual(problems, [])
        problems = _anchoring_problems(
            "synthetic.must_not_match", r"^[^#\n]*set -e\s*(#.*)?$")
        self.assertEqual(problems, [])

    # -- Round-4 review N4: a pattern whose entire top-level wrapping is a
    # special group ((?:...), (?i:...), ...) rather than a plain (...) one
    # must not be mis-parsed by naively stripping the outer parens, which
    # would glue the `?:`/`?i:` marker onto the first split-off alternative
    # and corrupt it. --

    def test_regex_fully_wrapped_rejects_special_group(self):
        self.assertFalse(_regex_fully_wrapped(r"(?:^[^#\n]*A|^[^#\n]*B)"))
        self.assertFalse(_regex_fully_wrapped(r"(?i:^[^#\n]*A|^[^#\n]*B)"))
        self.assertTrue(_regex_fully_wrapped(r"(^[^#\n]*A|^[^#\n]*B)"))

    def test_split_top_level_alternatives_does_not_corrupt_special_group(self):
        for wrapped in (r"(?:^[^#\n]*A|^[^#\n]*B)", r"(?i:^[^#\n]*A|^[^#\n]*B)"):
            with self.subTest(wrapped=wrapped):
                alts = _split_top_level_alternatives(wrapped)
                # Left intact as one atomic alternative, never split into a
                # corrupted "?:^[^#\n]*A" / "?i:^[^#\n]*A" fragment.
                self.assertEqual(alts, [wrapped])

    # -- Round-2 review item 6: grep-q-avoids-broken-pipe pinned one exact
    # spelling of the fix. Any pipe-free consumption of $build_log is
    # accepted: a here-string (braced or not), a [[ ... ]] glob test, a case
    # statement, or grep against a file it was written to. This finding is
    # off-skill (mined from the incident record, not SKILL.md), recorded in
    # the fixture header and excluded from the rubric's Correctness cap. --

    def test_grep_q_braced_here_string_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'grep -q "Successfully published" <<< "$build_log"',
            'grep -q "Successfully published" <<< "${build_log}"')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["grep-q-avoids-broken-pipe"]["passed"],
                        by_id["grep-q-avoids-broken-pipe"]["detail"])

    def test_grep_q_bracket_glob_test_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'grep -q "Successfully published" <<< "$build_log"',
            '[[ "$build_log" == *"Successfully published"* ]]')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["grep-q-avoids-broken-pipe"]["passed"],
                        by_id["grep-q-avoids-broken-pipe"]["detail"])

    def test_grep_q_case_statement_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'grep -q "Successfully published" <<< "$build_log"',
            'case "$build_log" in\n'
            '    *"Successfully published"*) ;;\n'
            '    *) exit 1 ;;\n'
            'esac')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["grep-q-avoids-broken-pipe"]["passed"],
                        by_id["grep-q-avoids-broken-pipe"]["detail"])

    def test_grep_q_file_argument_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'grep -q "Successfully published" <<< "$build_log"',
            'log_file="/tmp/build-log.$$"\n'
            'printf \'%s\\n\' "$build_log" > "$log_file"\n'
            'grep -q "Successfully published" "$log_file"')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["grep-q-avoids-broken-pipe"]["passed"],
                        by_id["grep-q-avoids-broken-pipe"]["detail"])

    def test_grep_q_leftover_piped_line_not_removed_fails(self):
        # A correct fix added alongside the original buggy line, which was
        # never deleted — must still fail (must_not_match is what catches
        # it; must_match alone would be satisfied by the added correct line).
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'grep -q "Successfully published" <<< "$build_log"',
            'echo "$build_log" | grep -q "Successfully published"\n'
            'grep -q "Successfully published" <<< "$build_log"')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["grep-q-avoids-broken-pipe"]["passed"])

    def test_grep_q_check_deleted_entirely_fails(self):
        # The build_log check is removed rather than fixed; must_not_match
        # alone would pass this vacuously (no piped form left) — must_match
        # is what catches the missing verification.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace('grep -q "Successfully published" <<< "$build_log"\n', '')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["grep-q-avoids-broken-pipe"]["passed"])

    # -- Round-6 review D: the comment credited this check's must_not_match
    # with banning "the producer piping INTO grep -q". It banned exactly one
    # spelling, the seed's own `echo "$build_log" | grep -q`. Measured: a
    # `cat build.log | grep -q "Successfully published"` left live BESIDE a
    # correct here-string scored 11/11 with the SIGPIPE-prone pipeline still
    # in the script. Option (ii) of the review: widen the ban to any live
    # line that pipes into `grep -q` and carries the token, so the sentence
    # becomes true rather than being narrowed to fit. --

    GREP_Q_HERE_STRING = 'grep -q "Successfully published" <<< "$build_log"'

    def test_d_producer_piped_into_grep_q_fails(self):
        # Each row keeps the correct here-string remedy alongside it, so
        # must_match is satisfied and the failure is attributable to
        # must_not_match — not to a missing remedy.
        producers = (
            'cat build.log | grep -q "Successfully published"',
            'printf \'%s\' "$build_log" | grep -q "Successfully published"',
        )
        for producer in producers:
            with self.subTest(producer=producer):
                ws = self._ws()
                self._fix_all(ws)
                path = ws / "scripts" / "publish.sh"
                text = path.read_text(encoding="utf-8")
                path.write_text(
                    text.replace(self.GREP_Q_HERE_STRING,
                                 f"{self.GREP_Q_HERE_STRING}\n{producer}"),
                    encoding="utf-8")
                result = self._run(ws)["grep-q-avoids-broken-pipe"]
                self.assertFalse(result["passed"])
                # Pins WHICH ban caught it: the widened pipe alternative,
                # not the seed's one hardcoded spelling.
                self.assertIn(r"\|(?!\|)\s*grep -q", result["detail"])

    def test_d_pipe_free_remedies_are_untouched_by_the_widened_ban(self):
        # The accepted remedies carry no pipe into grep -q, so none of them
        # may be caught by the widened alternative. `||` is not a pipe: the
        # here-string remedy with the skill's `|| { ...; exit 1; }` handler,
        # and a `|| grep -q ...` fallback, both stay legal.
        remedies = (
            self.GREP_Q_HERE_STRING,
            'grep -q "Successfully published" <<< "${build_log}"',
            '[[ "$build_log" == *"Successfully published"* ]]',
            'case "$build_log" in\n'
            '    *"Successfully published"*) ;;\n'
            '    *) exit 1 ;;\n'
            'esac',
            'log_file="/tmp/build-log.$$"\n'
            'printf \'%s\\n\' "$build_log" > "$log_file"\n'
            'grep -q "Successfully published" "$log_file"',
            self.GREP_Q_HERE_STRING
            + ' || { echo "ERROR: publish not confirmed" >&2; exit 1; }',
            '[[ "$build_log" == *"Successfully published"* ]] || '
            'grep -q "Successfully published" "$log_file"',
        )
        for remedy in remedies:
            with self.subTest(remedy=remedy.splitlines()[0][:60]):
                ws = self._ws()
                self._fix_all(ws)
                path = ws / "scripts" / "publish.sh"
                text = path.read_text(encoding="utf-8")
                path.write_text(text.replace(self.GREP_Q_HERE_STRING, remedy),
                                encoding="utf-8")
                result = self._run(ws)["grep-q-avoids-broken-pipe"]
                self.assertTrue(result["passed"], result["detail"])

    def test_d_grep_q_comment_pins_its_claims_to_named_tests(self):
        # Same rule the gh api paragraph obeys: every claim this comment
        # makes about what the check bans cites the test that measures it,
        # and every cited name is a real TestIssue74 method (`ast`, never a
        # regex).
        text = (BASH_CI_DIR / "fixture.yaml").read_text(encoding="utf-8")
        start = text.index("# Finding 2 (off-skill")
        end = text.index("- id: grep-q-avoids-broken-pipe")
        comment = text[start:end]
        defined = self._test_issue_74_method_names()
        cited = re.findall(r"\btest_[A-Za-z0-9_]+", comment)
        self.assertTrue(cited, comment)
        self.assertEqual(sorted({n for n in cited if n not in defined}), [],
                         "cited test name(s) are not methods of TestIssue74")
        for word in ("pipes into", "grep -q", "Successfully published",
                     "`||` is not a pipe"):
            with self.subTest(word=word):
                self.assertIn(word, comment)
        bullets = self._claim_bullets(comment)
        self.assertGreaterEqual(len(bullets), 5, bullets)
        for bullet in bullets:
            with self.subTest(bullet=bullet[:60]):
                last = bullet.rstrip().rstrip(",.").split()[-1]
                self.assertIn(last, defined,
                              "every claim bullet must END with the name of "
                              "the TestIssue74 test that measures it")
        # The widened ban is two alternatives, not one: the seed's own
        # spelling (which needs no token) and the token-carrying pipe.
        fixture = run_eval.load_fixture(BASH_CI_DIR)
        [check] = [c for c in fixture["objective_checks"]
                  if c["id"] == "grep-q-avoids-broken-pipe"]
        self.assertEqual(len(check["must_not_match"]), 2, check["must_not_match"])

    # -- Round-6 review N-c: round 5's N4 asked for a record of why this
    # fixture does not switch to main's file_matches_excluding_comments
    # type, and the sentence never landed (zero hits for
    # `excluding_comments` in the header). The claim is measured here, not
    # asserted as prose: that type strips WHOLE-LINE comments only, so the
    # trailing-comment dodges this fixture's anchoring closes would still
    # be open under it. --

    def test_n_c_excluding_comments_type_would_not_close_the_trailing_dodges(self):
        header = (BASH_CI_DIR / "fixture.yaml").read_text(encoding="utf-8")
        head = header[:header.index("objective_checks:")]
        for word in ("file_matches_excluding_comments",
                     "strips whole-line", "comments only", "file_matches"):
            with self.subTest(word=word):
                self.assertIn(word, head)
        defined = self._test_issue_74_method_names()
        cited = re.findall(r"\btest_[A-Za-z0-9_]+", head)
        self.assertTrue(cited, head)
        self.assertEqual(sorted({n for n in cited if n not in defined}), [],
                         "cited test name(s) are not methods of TestIssue74")

        # Measured through the real scorer on a correct fix that leaves
        # `# was: ...` comments trailing after the live code.
        ws = self._ws()
        self._apply_all_fixes_with_trailing_was_comments(ws)
        unanchored = r'echo "\$build_log" \| grep -q'
        passed, detail = objective.file_matches_excluding_comments(
            str(ws), ["scripts/publish.sh"], must_not_match=[unanchored])
        self.assertFalse(passed, detail)          # the dodge stays open
        passed, detail = objective.file_matches(
            str(ws), ["scripts/publish.sh"],
            must_not_match=[r'^[^#\n]*echo "\$build_log" \| grep -q[^#\n]*'])
        self.assertTrue(passed, detail)           # anchoring closes it

    # -- Round-6 review N-a: the header's comment-tail exception paragraph
    # (round-5 N3) was unpinned prose — deleting it, or letting a fifth
    # alternative grow a comment tail without being named there, left the
    # suite green. Pinned the same way the off-skill and known-limitation
    # paragraphs are, plus a mechanical cross-check of the count and of
    # which checks own the four. --

    COMMENT_TAIL_OWNERS = (
        ("process-substitution-error-propagates", "must_match"),
        ("gh-api-failure-not-swallowed", "must_not_match"),
        ("decoy-existing-set-e-untouched", "must_match"),
        ("decoy-existing-set-e-untouched", "must_not_match"),
    )

    def test_n_a_comment_tail_exception_paragraph_is_pinned(self):
        text = (BASH_CI_DIR / "fixture.yaml").read_text(encoding="utf-8")
        start = text.index("# Deliberate exception:")
        end = text.index("# Known limitation:")
        paragraph = text[start:end]

        for word in ("comment-tail", "exactly four alternatives",
                     "pipe-free alternative",
                     "reassignment alternative",
                     "process-substitution-error-propagates",
                     "gh-api-failure-not-swallowed",
                     "decoy-existing-set-e-untouched"):
            with self.subTest(word=word):
                self.assertIn(word, paragraph)

        defined = self._test_issue_74_method_names()
        cited = re.findall(r"\btest_[A-Za-z0-9_]+", paragraph)
        self.assertTrue(cited, paragraph)
        self.assertEqual(sorted({n for n in cited if n not in defined}), [],
                         "cited test name(s) are not methods of TestIssue74")

        # The count and the ownership the paragraph claims, measured against
        # the fixture itself: exactly four alternatives end in a comment
        # tail, and they belong to exactly the checks named above.
        fixture = run_eval.load_fixture(BASH_CI_DIR)
        owners = []
        for check in fixture["objective_checks"]:
            if check["type"] != "file_matches":
                continue
            for field in ("must_match", "must_not_match"):
                for pattern in check.get(field, []):
                    for alt in _split_top_level_alternatives(pattern):
                        if COMMENT_TAIL_RE.search(alt):
                            owners.append((check["id"], field))
        self.assertEqual(sorted(owners), sorted(self.COMMENT_TAIL_OWNERS))

    def test_grep_q_finding_documented_as_off_skill_in_fixture_header(self):
        text = (BASH_CI_DIR / "fixture.yaml").read_text(encoding="utf-8")
        self.assertIn("off-skill", text.lower())
        self.assertIn("#74", text)

    def test_rubric_does_not_cap_correctness_on_the_off_skill_finding(self):
        fixture = run_eval.load_fixture(BASH_CI_DIR)
        rubric = fixture["judge_rubric"]
        self.assertIn("off-skill", rubric.lower())
        self.assertIn("does not cap", rubric.lower())

    # -- Round-2 review item 7 (round-1 S5, still PARTIAL): seven
    # single-pattern fixture mutations left the suite green. Several are
    # proven by tests already above (item 1's deleted-call test, item 3's
    # two tests, the rewritten commit-suppression test); the two below round
    # out decoy-2's must_match and are also exercised by the yaml_parses
    # test in the next section. See the final report for the mutation
    # proof runs (temporarily dropping each pattern and re-running these). --

    def test_decoy_2_typo_missing_errexit_fails(self):
        # "set -uo pipefail" (missing the "e") isn't a bare "set -e", so the
        # must_not_match half is silent — only decoy-2's must_match
        # (^set -euo pipefail$) catches the corrupted line.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace("set -euo pipefail\n", "set -uo pipefail\n")
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["decoy-existing-set-e-untouched"]["passed"])

    # -- Round-2 review item 10 (and item 7 mutation 6): yaml_parses had no
    # teeth in the suite — nothing exercised it against genuinely broken
    # YAML, so pointing its glob at a non-matching pattern stayed invisible.

    def test_workflow_yaml_parses_catches_broken_yaml(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / ".github" / "workflows" / "release.yml"
        text = path.read_text(encoding="utf-8")
        text += "\n  broken: [unterminated\n"
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["workflow-yaml-parses"]["passed"])

    # -- Round-2 review item 8 (nit): the `git -c user.name=... -c
    # user.email=... -c commit.gpgsign=false commit ...` one-shot idiom, and
    # `git commit --no-gpg-sign`, are both legitimate and must pass. --

    def _fix_bump_with_git_dash_c(self, ws: Path) -> None:
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(self.JQ_LINE, self.JQ_FIXED_LINE)
        text = text.replace(
            'git commit -m "chore: bump version to ${NEXT_VERSION}"',
            'git -c user.name="release-bot" -c user.email="release-bot@example.com" '
            '-c commit.gpgsign=false commit -m "chore: bump version to ${NEXT_VERSION}"')
        path.write_text(text, encoding="utf-8")

    def test_git_dash_c_idiom_satisfies_identity_and_signing_checks(self):
        ws = self._ws()
        self._fix_publish(ws)
        self._fix_collect(ws)
        self._fix_bump_with_git_dash_c(ws)
        by_id = self._run(ws)
        for check_id in ("git-identity-configured", "commit-signing-safe-for-ci"):
            self.assertTrue(by_id[check_id]["passed"], by_id[check_id]["detail"])

    def test_no_gpg_sign_flag_satisfies_signing_check(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'git commit -m "chore: bump version to ${NEXT_VERSION}"',
            'git commit --no-gpg-sign -m "chore: bump version to ${NEXT_VERSION}"')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["commit-signing-safe-for-ci"]["passed"],
                        by_id["commit-signing-safe-for-ci"]["detail"])

    # -- Round-2 review item 9 (nit): VERSION\s*=.*package\.json was
    # case-sensitive; a lowercase `version=` assignment must still count. --

    def test_lowercase_version_assignment_satisfies_version_read_check(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(self.JQ_FIXED_LINE, self.JQ_FIXED_LINE.replace("VERSION=", "version="))
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["version-read-does-not-depend-on-unguarded-jq"]["passed"],
                        by_id["version-read-does-not-depend-on-unguarded-jq"]["detail"])

    # -- S4: the seed reads in-world, with no mention of the eval. --

    def test_s4_seed_readme_reads_in_world(self):
        text = (BASH_CI_DIR / "seed" / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("eval", text.lower())

    def test_s4_seed_scripts_do_not_mention_the_eval(self):
        for name in ("publish.sh", "collect.sh", "bump.sh"):
            text = (BASH_CI_DIR / "seed" / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("eval", text.lower(), name)

    # -- Nit: the top-level README's evals/ tree lists the new directory. --

    def test_readme_lists_the_new_eval_directory(self):
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("review-bash-ci-reliability/", text)

    # -- Round-6 review N-d: DESIGN.md still listed this eval as an
    # unshipped Class A candidate and as the head of the backfill order,
    # months after it shipped. Mirrors main's graduation wording for
    # rename-pdfs / post-failure-comment / github-actions-sha-pinning. --

    def test_n_d_design_md_no_longer_lists_this_eval_as_a_candidate(self):
        design = (REPO_ROOT / "DESIGN.md").read_text(encoding="utf-8")

        class_a = design[design.index("- **A. Workspace transforms**"):
                        design.index("- **B. Diagnosis/triage**")]
        candidates = class_a[class_a.index("Candidates:"):
                            class_a.index("(`github-actions-sha-pinning`")]
        self.assertNotIn("review-bash-ci-reliability", candidates)
        self.assertIn(
            "`review-bash-ci-reliability` graduated out of this list: covered "
            "by `evals/review-bash-ci-reliability/` (issue #74)",
            " ".join(class_a.split()))

        backfill = " ".join(
            design[design.index("Backfill order, by usage"):
                  design.index("### Deliberate non-coverage")].split())
        self.assertTrue(
            backfill.startswith("Backfill order, by usage × decidability × "
                                "incident material: `cms-stuck-pr-triage`"),
            backfill[:140])
        self.assertIn("`evals/review-bash-ci-reliability/`", backfill)
        self.assertIn("has shipped", backfill)

    # -- Round-3 review B1: must_match had no comment anchor at all, so a
    # comment merely quoting the fix (or, for a deleted call, quoting the old
    # bug under a `# was: ...` marker) satisfied the check with no live code
    # present. Each of these leaves the finding genuinely unfixed and must
    # still fail. --

    def test_b1_untouched_bump_sh_with_identity_and_signing_comments_fails(self):
        ws = self._ws()
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text += (
            '\n# git config --global user.email "ci@example.com"\n'
            '# git config --global commit.gpgsign false\n')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["git-identity-configured"]["passed"])
        self.assertFalse(by_id["commit-signing-safe-for-ci"]["passed"])

    def test_b1_gh_api_deleted_with_was_comment_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(self.GH_API_FIXED_BLOCK, f'# was: {self.GH_API_LINE}\nout=""')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["gh-api-failure-not-swallowed"]["passed"])

    def test_b1_watch_deleted_with_was_comment_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)',
            '# was: watch_output=$(gh run watch "$RUN_ID")\n'
            'WATCH_LOG=()')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["process-substitution-error-propagates"]["passed"])

    def test_b1_watch_deleted_with_pipestatus_only_comment_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)',
            '# PIPESTATUS is not used here on purpose\n'
            'WATCH_LOG=()')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["process-substitution-error-propagates"]["passed"])

    def test_b1_decoy_1_stripped_with_was_comment_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'rm -f "$tmp_response" || true  # temp file cleanup; '
            "harmless if it's already gone",
            '# was: rm -f "$tmp_response" || true\n'
            'rm -f "$tmp_response"')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["decoy-optional-cleanup-untouched"]["passed"])

    # -- Round-3 review B1/S3: the flip side — a skill-faithful fix must not
    # lose credit for leaving a trailing `# was: ...` comment on the fixed
    # line itself, even when that comment quotes old buggy code containing a
    # `|` (S3's exact failure mode: a must_match alternative that needs to
    # rule out a *live* pipe must stop looking at the comment's `#`, not
    # chase a `|` that only exists inside quoted dead code). --

    def _apply_all_fixes_with_trailing_was_comments(self, ws: Path) -> None:
        self._fix_all(ws)
        publish = ws / "scripts" / "publish.sh"
        text = publish.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n',
            'watch_output=$(gh run watch "$RUN_ID")  '
            '# was: mapfile -t WATCH_LOG < <(gh run watch "$RUN_ID" | tail -n 5)\n')
        text = text.replace(
            'grep -q "Successfully published" <<< "$build_log"',
            'grep -q "Successfully published" <<< "$build_log"  '
            '# was: echo "$build_log" | grep -q "Successfully published"')
        publish.write_text(text, encoding="utf-8")

        collect = ws / "scripts" / "collect.sh"
        text = collect.read_text(encoding="utf-8")
        text = text.replace(
            'if ! out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\'); then',
            'if ! out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\'); then  '
            f'# was: {self.GH_API_LINE}')
        text = text.replace(
            'rm -f "$tmp_response" || true  # temp file cleanup; '
            "harmless if it's already gone",
            'rm -f "$tmp_response" || true  # was: unconditional rm -f')
        collect.write_text(text, encoding="utf-8")

        bump = ws / "scripts" / "bump.sh"
        text = bump.read_text(encoding="utf-8")
        text = text.replace(
            self.JQ_FIXED_LINE,
            self.JQ_FIXED_LINE + f"  # was: {self.JQ_LINE}")
        text = text.replace(
            'git config --local commit.gpgsign false\n',
            'git config --local commit.gpgsign false  '
            '# was: no defense against commit.gpgsign\n')
        bump.write_text(text, encoding="utf-8")

    def test_b1_s3_skill_faithful_fix_with_trailing_was_comments_passes(self):
        ws = self._ws()
        self._apply_all_fixes_with_trailing_was_comments(ws)
        returncode, payload = self._run_cli(ws)
        self.assertEqual(returncode, 0, payload)

    def test_s3_trailing_was_comment_with_pipe_on_fixed_grep_q_line_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'grep -q "Successfully published" <<< "$build_log"',
            'grep -q "Successfully published" <<< "$build_log"  '
            '# was: echo "$build_log" | grep -q "Successfully published"')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["grep-q-avoids-broken-pipe"]["passed"],
                        by_id["grep-q-avoids-broken-pipe"]["detail"])

    def test_s3_trailing_comment_on_no_pipe_remedy_line_passes(self):
        # The comment deliberately contains a `|` (quoting the old buggy
        # line) — round-4 review N3: without a pipe in the comment, this
        # test passed even on the round-3 (missing-`$`) fixture too, since a
        # comment-free trailing anchor isn't exercised by a plain
        # non-piped comment. A `|` after the `#` must not be mistaken for a
        # live, unremedied pipe by the must_match's [^#\n|]* class.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)',
            'gh run watch "$RUN_ID"  # was: … | tail -n 5\n'
            'mapfile -t WATCH_LOG < <(gh run view "$RUN_ID" --log | tail -n 5)')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["process-substitution-error-propagates"]["passed"],
                        by_id["process-substitution-error-propagates"]["detail"])

    # -- Round-3 review S2 (round-1 B2, third time): `set \+e` had no anchor
    # at all, so a trailing comment mentioning it, or a standalone comment
    # warning against it, tripped gh-api-failure-not-swallowed even though
    # no live `set +e` exists. The real dodge (an actual `set +e` wrapper)
    # must still fail. --

    def test_s2_trailing_set_plus_e_comment_on_fixed_gh_api_line_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'if ! out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\'); then',
            'if ! out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\'); then  '
            '# never use set +e here')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["gh-api-failure-not-swallowed"]["passed"],
                        by_id["gh-api-failure-not-swallowed"]["detail"])

    def test_s2_standalone_comment_warning_against_set_plus_e_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_FIXED_BLOCK,
            "# never use set +e around this call\n" + self.GH_API_FIXED_BLOCK)
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["gh-api-failure-not-swallowed"]["passed"],
                        by_id["gh-api-failure-not-swallowed"]["detail"])

    def test_s2_live_set_plus_e_wrapper_still_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_FIXED_BLOCK,
            'set +e\n'
            'out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\')\n'
            'set -e')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["gh-api-failure-not-swallowed"]["passed"])

    # -- Round-3 review N5: decoy 1 didn't tolerate an extra space, and
    # decoy 2's must_match didn't tolerate a trailing explanatory comment. --

    def test_n5_decoy_1_extra_space_after_rm_f_still_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace('rm -f "$tmp_response"', 'rm -f  "$tmp_response"')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["decoy-optional-cleanup-untouched"]["passed"],
                        by_id["decoy-optional-cleanup-untouched"]["detail"])

    def test_n5_decoy_2_trailing_explanatory_comment_still_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'set -euo pipefail\n',
            'set -euo pipefail  # fail fast, no partial releases\n')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["decoy-existing-set-e-untouched"]["passed"],
                        by_id["decoy-existing-set-e-untouched"]["detail"])

    # -- Round-3 review N6: yaml_parses passes vacuously when the workflow
    # file is deleted outright — there's nothing left to fail parsing. --

    def test_n6_workflow_file_deleted_fails_presence_check(self):
        ws = self._ws()
        self._fix_all(ws)
        (ws / ".github" / "workflows" / "release.yml").unlink()
        by_id = self._run(ws)
        self.assertFalse(by_id["workflow-file-present"]["passed"])
        self.assertTrue(by_id["workflow-yaml-parses"]["passed"])

    def test_n6_workflow_file_present_passes_on_hand_fixed_copy(self):
        ws = self._ws()
        self._fix_all(ws)
        by_id = self._run(ws)
        self.assertTrue(by_id["workflow-file-present"]["passed"],
                        by_id["workflow-file-present"]["detail"])

    # -- Round-4 review B1 (BLOCKER): version-read-does-not-depend-on-
    # unguarded-jq's must_match had an unanchored `.*` between "version="
    # and "package.json", so a trailing comment could cross into it —
    # `VERSION="$1"  # no longer read from package.json` (the live version
    # read genuinely deleted) scored 11/11 on 3dd563b. --

    def test_b1_version_read_comment_dodge_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.JQ_FIXED_LINE,
            'VERSION="$1"  # no longer read from package.json')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["version-read-does-not-depend-on-unguarded-jq"]["passed"])

    def test_b1_version_read_dodge_scores_11_of_11_on_3dd563b(self):
        # Documents the full blast radius the reviewer measured: on 3dd563b
        # every other check still passes around the dodge, so it is the
        # must_match anchoring alone that was exploitable, not some
        # compensating failure elsewhere.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.JQ_FIXED_LINE,
            'VERSION="$1"  # no longer read from package.json')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertEqual(
            sum(1 for r in by_id.values() if r["passed"]), len(by_id) - 1,
            "only version-read-does-not-depend-on-unguarded-jq should fail")

    def test_b1_version_read_trailing_unrelated_comment_still_passes(self):
        # The fix must not overcorrect: a live version read followed by an
        # unrelated trailing comment (nothing to cross into) still passes.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.JQ_FIXED_LINE, self.JQ_FIXED_LINE + "  # parsed from package.json")
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["version-read-does-not-depend-on-unguarded-jq"]["passed"],
                        by_id["version-read-does-not-depend-on-unguarded-jq"]["detail"])

    # -- Round-4 review S1: gh-api-failure-not-swallowed's must_not_match did
    # not forbid reassigning the captured variable to an empty string on
    # failure (`|| out=""` / `|| out=''`), which swallows the failure just
    # as much as `|| true` does — `out` ends up blank either way and the
    # script reports "No merged PRs found" and exits 0. --

    def test_s1_gh_api_empty_string_reassignment_swallow_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_FIXED_BLOCK,
            'out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\') || out=""')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["gh-api-failure-not-swallowed"]["passed"])

    def test_s1_gh_api_empty_single_quote_reassignment_swallow_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_FIXED_BLOCK,
            "out=$(gh api \"repos/${REPO}/pulls?state=merged\" --jq '.[].title') || out=''")
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["gh-api-failure-not-swallowed"]["passed"])

    def test_s1_gh_api_genuine_fallback_on_same_line_stays_legal(self):
        # A real fallback (not an empty-string swallow) on the gh api line
        # itself must not be caught by the new alternative.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_FIXED_BLOCK,
            'out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\') '
            '|| out=$(cat cache)')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["gh-api-failure-not-swallowed"]["passed"],
                        by_id["gh-api-failure-not-swallowed"]["detail"])

    def test_s1_skill_own_safe_snippet_still_passes(self):
        # test_b2_skill_safe_snippet_passes already pins this against the
        # must_not_match set as it stood before S1; re-asserted here so the
        # new alternative's addition is proven not to regress it too.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            self.GH_API_FIXED_BLOCK,
            'out=$(gh api "repos/${REPO}/pulls?state=merged" --jq \'.[].title\') || '
            '{ echo "ERROR: gh api call failed" >&2; exit 1; }')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["gh-api-failure-not-swallowed"]["passed"],
                        by_id["gh-api-failure-not-swallowed"]["detail"])

    # -- Round-5 review F2 (third consecutive round on this paragraph): the
    # comment above gh-api-failure-not-swallowed claimed EVERY must_not_match
    # alternative requires `gh api` on that same live line. Three of the four
    # do; the bare `set +e` ban is deliberately file-scoped instead — round 4
    # endorsed the behavior (whether a set +e/set -e bracket actually wraps
    # THIS call is a structural question a regex must not answer), the
    # sentence just described it wrong. Fix the wording, then pin both the
    # wording and the (already-correct) behavior it was misdescribing. --

    def test_f2_gh_api_comment_scopes_line_local_vs_file_scoped(self):
        text = (BASH_CI_DIR / "fixture.yaml").read_text(encoding="utf-8")
        start = text.index("Only the suppression forms themselves are forbidden")
        end = text.index("- id: gh-api-failure-not-swallowed")
        comment = text[start:end]
        self.assertIn("line-local", comment)
        self.assertIn("file-scoped", comment)
        self.assertIn("set +e", comment)

    def test_f2_far_away_set_plus_e_bracket_around_unrelated_command_fails(self):
        # A set +e/set -e bracket nowhere near the gh api call, wrapping an
        # unrelated command, must still fail the check: the ban is
        # file-scoped by design, not conditioned on proximity to gh api.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "collect.sh"
        text = path.read_text(encoding="utf-8")
        text += (
            '\n# unrelated diagnostic, nothing to do with the gh api call above\n'
            'set +e\n'
            'grep -c . changed-packages.txt\n'
            'set -e\n')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["gh-api-failure-not-swallowed"]["passed"])

    def test_f2_control_without_far_away_set_plus_e_still_passes(self):
        # Control for the test above: the same fixed collect.sh, minus the
        # far-away set +e bracket, passes — isolating that the bracket
        # itself, not some other change, is what fails the check.
        ws = self._ws()
        self._fix_all(ws)
        by_id = self._run(ws)
        self.assertTrue(by_id["gh-api-failure-not-swallowed"]["passed"],
                        by_id["gh-api-failure-not-swallowed"]["detail"])

    # -- Round-6 review C: alternative 4's end anchor was dodged by putting
    # ANOTHER statement after the swallow. `out=$(gh api …) || out="";
    # echo "continuing"` scored 11/11 — the reassignment is still the
    # swallow, it just is not the last thing on the line any more.
    # Pre-existing, not introduced this round. The anchor now tolerates one
    # following `;`/`&` statement, so the swallow is caught wherever the
    # line goes next, while a fallback that assigns a real value stays
    # legal. --

    def test_c_gh_api_empty_reassignment_before_another_statement_fails(self):
        call = ('out=$(gh api "repos/${REPO}/pulls?state=merged" '
                "--jq '.[].title')")
        evasions = (
            f'{call} || out=""; echo "continuing"',
            f'{call} || out="" && echo "continuing"',
            f'{call} || out= ;',
        )
        for evasion in evasions:
            with self.subTest(evasion=evasion):
                ws = self._ws()
                self._fix_all(ws)
                path = ws / "scripts" / "collect.sh"
                text = path.read_text(encoding="utf-8")
                path.write_text(text.replace(self.GH_API_FIXED_BLOCK, evasion),
                                encoding="utf-8")
                result = self._run(ws)["gh-api-failure-not-swallowed"]
                self.assertFalse(result["passed"])
                self.assertIn(r"\w+=", result["detail"])

    # -- Round-6 review F2 (FOURTH consecutive round on this same paragraph).
    # Rounds 3, 4 and 5 each rewrote it as prose and each rewrite introduced
    # a fresh over-claim; round 5's said a suppression is forbidden only "as
    # the last thing on that line before an optional trailing comment", which
    # is true of the reassignment ban alone — the `|| true/:/echo` and
    # `2>/dev/null` bans fire wherever on the line they appear. The design
    # decision for this round: the paragraph is a LIST OF PINNED CLAIMS, one
    # per bullet, each ending with the name of the test below that measures
    # it through the real scorer, and the words test enforces both halves. --

    def test_gh_api_swallow_anywhere_on_the_line_fails(self):
        # The two rows that falsified round 5's end-anchor claim: neither
        # suppression is the last thing on its line, and both are still
        # caught. Regression floors, not new behaviour — they already fail
        # today; what is new is that the paragraph now says so. Mutation
        # proof: dropping must_not_match alternative 1 turns the first
        # subTest red, dropping alternative 2 turns the second red.
        call = ('out=$(gh api "repos/${REPO}/pulls?state=merged" '
                "--jq '.[].title')")
        rows = (
            ("alt 1, mid-line", f'{call} || true; echo "continuing"',
             r"(true|:|echo)"),
            ("alt 2, mid-line",
             'out=$(gh api "repos/${REPO}/pulls?state=merged" '
             "--jq '.[].title' 2>/dev/null) || out=$(cat cache)",
             "2>"),
        )
        for label, line, expected_pattern_fragment in rows:
            with self.subTest(row=label):
                ws = self._ws()
                self._fix_all(ws)
                path = ws / "scripts" / "collect.sh"
                text = path.read_text(encoding="utf-8")
                path.write_text(text.replace(self.GH_API_FIXED_BLOCK, line),
                                encoding="utf-8")
                result = self._run(ws)["gh-api-failure-not-swallowed"]
                self.assertFalse(result["passed"])
                # Pins WHICH ban caught it, so the row cannot start passing
                # for a different reason than the bullet claims.
                self.assertIn(expected_pattern_fragment, result["detail"])

    def _gh_api_finding_comment(self) -> str:
        text = (BASH_CI_DIR / "fixture.yaml").read_text(encoding="utf-8")
        start = text.index("# Finding 3:")
        end = text.index("- id: gh-api-failure-not-swallowed")
        return text[start:end]

    def _test_issue_74_method_names(self) -> set[str]:
        """Every method of TestIssue74, parsed with `ast` (never a regex)."""
        tree = ast.parse((TEST_DIR / "run_tests.py").read_text(encoding="utf-8"))
        classes = [node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "TestIssue74"]
        self.assertEqual(len(classes), 1, "expected exactly one TestIssue74")
        return {node.name for node in classes[0].body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    @staticmethod
    def _claim_bullets(comment: str) -> list[str]:
        """Each `#   - ...` bullet of the comment, with its wrapped lines."""
        bullets: list[str] = []
        for raw in comment.splitlines():
            body = raw.lstrip()
            if not body.startswith("#"):
                continue
            body = body[1:]
            if body.startswith("   - "):
                bullets.append(body[5:].strip())
            elif bullets and body.startswith("     ") and body.strip():
                bullets[-1] += " " + body.strip()
        return bullets

    def test_f2_gh_api_paragraph_pins_every_claim_to_a_named_test(self):
        # (a) every test name the paragraph cites exists as a method of
        # TestIssue74 (test/run_tests.py parsed with `ast`, never a regex),
        # and every bullet ends with one — the rule that stops a fifth
        # round of unpinned prose; (b) the operative words of the claims.
        comment = self._gh_api_finding_comment()
        defined = self._test_issue_74_method_names()

        cited = re.findall(r"\btest_[A-Za-z0-9_]+", comment)
        self.assertTrue(cited, "the paragraph must cite the tests that "
                        "measure its claims")
        self.assertEqual(sorted({n for n in cited if n not in defined}), [],
                         "cited test name(s) are not methods of TestIssue74")

        bullets = self._claim_bullets(comment)
        self.assertGreaterEqual(len(bullets), 8, bullets)
        for bullet in bullets:
            with self.subTest(bullet=bullet[:60]):
                last = bullet.rstrip().rstrip(",.").split()[-1]
                self.assertIn(last, defined,
                              "every claim bullet must END with the name of "
                              "the TestIssue74 test that measures it")

        # (b) the operative words, and the ordinals the bullets assert.
        for word in ("LINE-LOCAL", "FILE-SCOPED", "END-ANCHORED", "WHEREVER",
                     "line-local", "file-scoped", "set +e", "gh api",
                     "`;`/`&`"):
            with self.subTest(word=word):
                self.assertIn(word, comment)

        # The bullets number the alternatives; pin that numbering against
        # the fixture's own must_not_match order, and pin the structural
        # LINE-LOCAL/FILE-SCOPED split against the patterns themselves.
        fixture = run_eval.load_fixture(BASH_CI_DIR)
        [check] = [c for c in fixture["objective_checks"]
                  if c["id"] == "gh-api-failure-not-swallowed"]
        bans = check["must_not_match"]
        self.assertEqual(len(bans), 4, bans)
        self.assertIn("(true|:|echo)", bans[0])
        self.assertIn("2>", bans[1])
        self.assertIn(r"set \+e", bans[2])
        self.assertIn(r"\w+=", bans[3])
        line_local_prefix = r"^[^#\n]*gh api[^#\n]*"
        for index in (0, 1, 3):
            self.assertTrue(bans[index].startswith(line_local_prefix), bans[index])
        self.assertFalse(bans[2].startswith(line_local_prefix), bans[2])

    # -- Round-4 review S2: documented known limitation. [^#\n]* treats the
    # FIRST '#' on a line as a comment start even inside a quoted string, so
    # a correct fix can still fail if its remedy token lands after a quoted
    # '#' earlier on the same line. Lexing shell quoting to close this
    # properly is out of scope (see the fixture header); this test pins the
    # gap as a known, accepted false negative rather than a silent one. --

    def test_s2_quoted_hash_before_no_gpg_sign_is_a_known_false_negative(self):
        ws = self._ws()
        self._fix_publish(ws)
        self._fix_collect(ws)
        path = ws / "scripts" / "bump.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(self.JQ_LINE, self.JQ_FIXED_LINE)
        text = text.replace(
            "git add package.json",
            'git config --local user.email "release-bot@example.com"\n'
            'git config --local user.name "release-bot"\n'
            'git add package.json')
        text = text.replace(
            'git commit -m "chore: bump version to ${NEXT_VERSION}"',
            'git commit -m "chore: bump #${NEXT_VERSION}" --no-gpg-sign')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["git-identity-configured"]["passed"],
                        by_id["git-identity-configured"]["detail"])
        # Known false negative: --no-gpg-sign is genuinely present and would
        # otherwise satisfy commit-signing-safe-for-ci, but it lands after
        # the quoted '#' in the commit message, which [^#\n]* cannot tell
        # apart from a real comment start.
        self.assertFalse(by_id["commit-signing-safe-for-ci"]["passed"])

    # -- Round-6 review N-b: the quoted-'#' blind spot has a quoted-`|`
    # twin, and it is fail-CLOSED. A `|` inside a quoted string reads as a
    # pipe to the `[^#\n|]*` runs of the pipe-free alternative, so the
    # skill's own §3 handler is rejected when its message happens to carry
    # one. Newly reachable: before round-5 F1 added the
    # `(\|\|[^#\n|]*)*` tolerance, that alternative was
    # `^[^#\n|]*\bgh run watch\b[^#\n|]*(\s*#.*)?$` and NO `||` handler
    # satisfied it, quoted pipe or not. Documented as a known false
    # negative rather than a silent one, the same way the quoted-'#' case
    # is. --

    def test_n_b_quoted_pipe_in_watch_handler_is_a_known_false_negative(self):
        handlers = (
            ('|| { echo "see: a|b" >&2; exit 1; }', False),
            ('|| { echo "see: a b" >&2; exit 1; }', True),
        )
        for handler, expected in handlers:
            with self.subTest(handler=handler):
                ws = self._ws()
                self._fix_all(ws)
                path = ws / "scripts" / "publish.sh"
                text = path.read_text(encoding="utf-8")
                text = text.replace(
                    'watch_output=$(gh run watch "$RUN_ID")\n'
                    'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" '
                    '| tail -n 5)',
                    f'gh run watch "$RUN_ID" {handler}')
                path.write_text(text, encoding="utf-8")
                by_id = self._run(ws)
                result = by_id["process-substitution-error-propagates"]
                self.assertEqual(result["passed"], expected, result["detail"])
                if not expected:
                    # Fails on must_match, not must_not_match: a correct fix
                    # rejected, not a dodge caught.
                    self.assertIn("lacks", result["detail"])
                # The blind spot is confined to this one check.
                for check_id, other in by_id.items():
                    if check_id != "process-substitution-error-propagates":
                        self.assertTrue(other["passed"],
                                        f"{check_id}: {other['detail']}")

    def test_s2_known_limitation_paragraph_documents_the_first_hash_blind_spot(self):
        # Round-5 N2: this paragraph's own claim was unpinned as prose —
        # deleting it left the suite green, since only its behavior is
        # pinned (by the test above and test_s2_live_set_plus_e_wrapper_
        # still_fails). Pin the operative words the ~1989-style way.
        text = (BASH_CI_DIR / "fixture.yaml").read_text(encoding="utf-8")
        start = text.index("# Known limitation:")
        end = text.index("objective_checks:")
        paragraph = text[start:end]
        self.assertIn("FIRST", paragraph)
        self.assertIn("quoted", paragraph)
        self.assertIn("fail-open", paragraph)
        self.assertIn("commit-signing-safe-for-ci", paragraph)
        self.assertIn("set -e", paragraph)
        # Round-6 N-b: the quoted-`|` twin, documented beside it.
        for word in ("quoted-`|`", "fail-closed",
                     "process-substitution-error-propagates"):
            with self.subTest(word=word):
                self.assertIn(word, paragraph)
        defined = self._test_issue_74_method_names()
        cited = re.findall(r"\btest_[A-Za-z0-9_]+", paragraph)
        self.assertTrue(cited, paragraph)
        self.assertEqual(sorted({n for n in cited if n not in defined}), [],
                         "cited test name(s) are not methods of TestIssue74")

    # -- Round-4 review S3: process-substitution-error-propagates's third
    # must_match alternative lost its trailing `$`, so it matched on the
    # mere PRESENCE of "gh run watch" text with no requirement to reach end
    # of line — the pristine seed's own still-broken line satisfied it
    # (must_not_match was the only thing still failing the check), and a
    # bare, unchecked `gh run watch "$X" | tail -n 5` (no PIPESTATUS check)
    # satisfied it outright despite not being one of the three accepted
    # remedies. --

    def test_s3_bare_piped_watch_without_pipestatus_check_fails(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)',
            'gh run watch "$RUN_ID" | tail -n 5')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertFalse(by_id["process-substitution-error-propagates"]["passed"])

    def test_s3_pristine_seed_watch_line_fails_must_match_too(self):
        # Before the fix, must_match's third alternative matched even the
        # seed's own unfixed line outright (the detail said only "contains"
        # a must_not_match pattern, never "lacks" a must_match one) — the
        # check failed by luck of must_not_match, not because must_match
        # was doing its job. After the fix both halves correctly fail it.
        by_id = self._run(self._ws())
        self.assertIn("lacks", by_id["process-substitution-error-propagates"]["detail"])

    # -- Round-5 review F1: the third must_match alternative's `[^#\n|]*`
    # run excludes '|' entirely, and '||' is two of them, so a pipe-free
    # `gh run watch` line that still carries its own `||` error handling
    # could never reach the trailing `$` — even though dropping the pipe
    # and handling the failure inline is strictly stronger than the other
    # two accepted remedies, and is the fixture's own third prescribed fix
    # (see the header comment above this check). --

    def test_f1_watch_with_or_exit_passes(self):
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)',
            'gh run watch "$RUN_ID" || exit 1')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["process-substitution-error-propagates"]["passed"],
                        by_id["process-substitution-error-propagates"]["detail"])

    def test_f1_watch_with_skill_brace_idiom_passes(self):
        # SKILL.md section 3's own `cmd || { echo "ERROR: ..."; exit 1; }`
        # idiom, transplanted onto the gh run watch line.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)',
            'gh run watch "$RUN_ID" || { echo "ERROR: gh run watch failed"; exit 1; }')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        self.assertTrue(by_id["process-substitution-error-propagates"]["passed"],
                        by_id["process-substitution-error-propagates"]["detail"])

    def test_f1_watch_with_or_true_still_fails_via_must_not_match(self):
        # The '||' tolerance F1 adds to must_match must not reopen the door
        # must_not_match's second alternative closes: '|| true' is still
        # banned outright, whatever must_match now accepts.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)',
            'gh run watch "$RUN_ID" || true')
        path.write_text(text, encoding="utf-8")
        by_id = self._run(ws)
        result = by_id["process-substitution-error-propagates"]
        self.assertFalse(result["passed"])
        self.assertIn(r"(true|:|echo)", result["detail"])
    # -- Round-6 review A: the `||` tolerance F1 added to must_match accepts
    # ANY `||` continuation, while must_not_match banned only `true` and `:`.
    # So `gh run watch "$RUN_ID" || echo "watch failed, continuing"` scored
    # 11/11 although `set -e` never sees the failure — contradicting the
    # check's own description and the remedy's rationale. Pre-existing on
    # 3dd563b, not introduced by F1. The lexical half is fixed by extending
    # the ban to `(true|:|echo)`, the same three tokens the sibling gh api
    # check already bans; every OTHER handler stays the judge's call. --

    def test_a_watch_or_echo_handler_fails(self):
        # `|| echo ...` swallows the failure exactly as `|| true` does: the
        # echo succeeds, so `set -e` sees a zero status for the whole line.
        ws = self._ws()
        self._fix_all(ws)
        path = ws / "scripts" / "publish.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'watch_output=$(gh run watch "$RUN_ID")\n'
            'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" | tail -n 5)',
            'gh run watch "$RUN_ID" || echo "watch failed, continuing"')
        path.write_text(text, encoding="utf-8")
        result = self._run(ws)["process-substitution-error-propagates"]
        self.assertFalse(result["passed"])
        self.assertIn(r"(true|:|echo)", result["detail"])

    def test_a_other_handlers_are_left_to_the_judge(self):
        # The deliberate lexical silence the check's comment claims: whether
        # `|| exit 0`, `|| /bin/true` or `|| continue` actually propagates
        # the failure is a Correctness question for the judge, not something
        # a regex may decide (rule 25). Each still scores 11/11 here — that
        # is the design, not an oversight, and this test pins it so nobody
        # closes it with a regex without deleting this test first.
        for handler in ("|| exit 0", "|| /bin/true", "|| continue"):
            with self.subTest(handler=handler):
                ws = self._ws()
                self._fix_all(ws)
                path = ws / "scripts" / "publish.sh"
                text = path.read_text(encoding="utf-8")
                text = text.replace(
                    'watch_output=$(gh run watch "$RUN_ID")\n'
                    'mapfile -t WATCH_LOG < <(printf \'%s\\n\' "$watch_output" '
                    '| tail -n 5)',
                    f'gh run watch "$RUN_ID" {handler}')
                path.write_text(text, encoding="utf-8")
                by_id = self._run(ws)
                for check_id, result in by_id.items():
                    self.assertTrue(result["passed"],
                                    f"{check_id}: {result['detail']}")

    def test_a_finding_1_comment_pins_its_claims_to_named_tests(self):
        # Same rule the gh api paragraph now obeys, applied to the one
        # clause item A adds here: a claim about what this check decides
        # (and deliberately does not) cites the test that measures it, and
        # every cited name is a real TestIssue74 method (`ast`, not regex).
        text = (BASH_CI_DIR / "fixture.yaml").read_text(encoding="utf-8")
        start = text.index("# Finding 1:")
        end = text.index("- id: process-substitution-error-propagates")
        comment = text[start:end]
        defined = self._test_issue_74_method_names()
        cited = re.findall(r"\btest_[A-Za-z0-9_]+", comment)
        self.assertTrue(cited, comment)
        self.assertEqual(sorted({n for n in cited if n not in defined}), [],
                         "cited test name(s) are not methods of TestIssue74")
        for word in ("|| echo", "judge", "Correctness", "|| exit 0",
                     "|| /bin/true", "|| continue"):
            with self.subTest(word=word):
                self.assertIn(word, comment)
