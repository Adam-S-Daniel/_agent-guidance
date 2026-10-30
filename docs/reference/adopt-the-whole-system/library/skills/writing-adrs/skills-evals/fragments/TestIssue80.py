class TestIssue80(unittest.TestCase):
    """evals/writing-adrs: the first two of three fixtures for the writing-adrs
    skill (issue #80); the third, `supersede`, has its own tests in
    test/issues/test_issue_80_supersede.py.

    `existing-convention` (Class A, format half) — a repo that already has
    docs/decisions/ in a house Status/Context/Decision/Consequences format;
    the task is to record an already-implemented retry-policy decision as
    the next ADR, following the HOUSE convention rather than the skill's own
    default template, updating the index, and linking the code. `bootstrap`
    covers the other half of the skill: no docs/decisions/ exists yet, so
    the correct answer is the skill's own carried template, landed in the
    same change as the first ADR. Both fixtures share the same underlying
    repo (README.md, CHANGELOG.md, scripts/retry.sh) and the same prompt;
    only the presence of docs/decisions/ differs.

    Every test below isolates ONE objective check per mutation: the
    hand-written "correct" fix satisfies every check, and each mutation
    breaks exactly the one check it's named for while leaving the others
    green — proving the checks have teeth individually, not just in
    aggregate.
    """

    # ---- existing-convention: hand-written correct ADR 0004 ---------------

    EXISTING_ADR_0004 = """\
# 0004. Retry transient failures up to 5 times with capped exponential backoff

## Status

Accepted

## Context

Order Sync's poller calls the fulfillment service and other upstream
services that occasionally return a transient failure such as a 503. On
2026-03 (PR #41), one such failure was retried in a tight loop with no
cap and no delay, pinning a worker for 40 minutes and starving the queue
behind it.

## Decision

Retry a failing call up to 5 times, backing off exponentially (1s, 2s, 4s,
8s, 16s) before giving up.

## Consequences

A transient blip no longer surfaces as a customer-facing error, and a
persistent failure now gives up after a bounded delay instead of looping
forever. A call that legitimately needs more than 5 tries within about 30
seconds will fail; none observed so far need that.

## Alternatives considered

Retrying forever with a fixed delay was rejected: it would have masked the
same runaway-retry failure again, just slower.

Failing fast with no retry at all was rejected too: it would turn every
transient blip into a customer-facing error.

## References

PR #41.
"""

    # Same content with Context and Decision swapped — breaks section ORDER
    # only; every field is still present, nothing else about the file changes.
    EXISTING_ADR_0004_WRONG_ORDER = """\
# 0004. Retry transient failures up to 5 times with capped exponential backoff

## Status

Accepted

## Decision

Retry a failing call up to 5 times, backing off exponentially (1s, 2s, 4s,
8s, 16s) before giving up.

## Context

Order Sync's poller calls the fulfillment service and other upstream
services that occasionally return a transient failure such as a 503. On
2026-03 (PR #41), one such failure was retried in a tight loop with no
cap and no delay, pinning a worker for 40 minutes and starving the queue
behind it.

## Consequences

A transient blip no longer surfaces as a customer-facing error, and a
persistent failure now gives up after a bounded delay instead of looping
forever.
"""

    EXISTING_INDEX_ROW = ("| [0004](0004-retry-with-capped-exponential-backoff.md) | "
                          "Retry transient failures up to 5 times with capped "
                          "exponential backoff | Accepted |\n")

    EXISTING_README_ANCHOR = (
        "| [0003](0003-poll-fulfillment-service-every-10s.md) | Poll the "
        "fulfillment service every 10 seconds | Accepted |\n")

    EXISTING_RETRY_LINK = (
        "# See docs/decisions/0004-retry-with-capped-exponential-backoff.md\n"
        "# for why these retry parameters were chosen.\n")

    RETRY_SH_ANCHOR = "set -euo pipefail\n"

    # A second, unwarranted ADR for the CHANGELOG's routine fact — correct
    # house format in isolation, but its mere existence is the violation.
    DECOY_ADR_FOR_CHANGELOG = """\
# 0005. Remove the moment dependency

## Status

Accepted

## Context

CHANGELOG.md records that the moment dependency was removed.

## Decision

Use the platform Date APIs instead.

## Consequences

One fewer dependency to update.
"""

    # ---- bootstrap: hand-written correct README + ADR 0001 -----------------

    BOOTSTRAP_README = """\
# Architecture Decision Records

This folder captures why non-obvious decisions were made in this repo.

## When to write one

Write an ADR when, in six months, someone proposing to revert the change
would need three paragraphs to be talked out of it.

## Naming and numbering

- `NNNN-kebab-title.md`, numbered sequentially from `0001`.
- Title is an imperative verb + object.

## Status values

`Proposed` -> `Accepted` -> `Superseded by NNNN` (or `Deprecated`).

## Template

Copy everything between the rules into `NNNN-kebab-title.md`.

---

```markdown
# NNNN. Imperative title matching the index row

- **Status:** Proposed
- **Date:** YYYY-MM-DD

## Context

## Decision

## Consequences

## Alternatives considered

## References
```

---

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-retry-with-capped-exponential-backoff.md) | Retry transient failures up to 5 times with capped exponential backoff | Accepted |
"""

    BOOTSTRAP_ADR_0001 = """\
# 0001. Retry transient failures up to 5 times with capped exponential backoff

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

Order Sync's poller calls the fulfillment service and other upstream
services that occasionally return a transient failure such as a 503. On
2026-03 (PR #41), one such failure was retried in a tight loop with no
cap and no delay, pinning a worker for 40 minutes and starving the queue
behind it.

## Decision

Retry a failing call up to 5 times, backing off exponentially (1s, 2s, 4s,
8s, 16s) before giving up.

## Consequences

A transient blip no longer surfaces as a customer-facing error, and a
persistent failure now gives up after a bounded delay instead of looping
forever.

## Alternatives considered

Retrying forever with a fixed delay was rejected: it would have masked the
same runaway-retry failure again, just slower.

Failing fast with no retry at all was rejected too: it would turn every
transient blip into a customer-facing error.

## References

PR #41.
"""

    # Context/Decision swapped, same as the existing-convention wrong-order
    # fixture above — breaks order only.
    BOOTSTRAP_ADR_0001_WRONG_ORDER = """\
# 0001. Retry transient failures up to 5 times with capped exponential backoff

- **Status:** Accepted
- **Date:** 2026-09-05

## Decision

Retry a failing call up to 5 times, backing off exponentially (1s, 2s, 4s,
8s, 16s) before giving up.

## Context

Order Sync's poller calls the fulfillment service and other upstream
services that occasionally return a transient failure such as a 503.

## Consequences

A transient blip no longer surfaces as a customer-facing error.

## Alternatives considered

Retrying forever with a fixed delay was rejected.

## References

PR #41.
"""

    BOOTSTRAP_RETRY_LINK = (
        "# See docs/decisions/0001-retry-with-capped-exponential-backoff.md\n"
        "# for why these retry parameters were chosen.\n")

    BOOTSTRAP_DECOY_ADR_FOR_CHANGELOG = """\
# 0002. Remove the moment dependency

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

CHANGELOG.md records that the moment dependency was removed.

## Decision

Use the platform Date APIs instead.

## Consequences

One fewer dependency to update.
"""

    # Bootstrap step 4: "Add a pointer paragraph in AGENTS.md (or README.md
    # if there's no AGENTS.md) under a new '### Architecture Decision
    # Records' heading". The seed's AGENTS.md has no such heading yet, so
    # the skill-faithful answer appends this. Its second line is the same
    # sentence the existing-convention seed's AGENTS.md already carries
    # under that heading — see the two seeds' AGENTS.md files themselves,
    # and test_seed_files_are_byte_identical_except_for_their_premise below.
    BOOTSTRAP_AGENTS_MD_POINTER = """
### Architecture Decision Records

Non-obvious decisions live in [`docs/decisions/`](docs/decisions/README.md)
— read the index there before assuming a past choice was arbitrary.
"""

    # An ad-hoc, non-skill-shaped bootstrap README: it has an index row (so
    # index-gained-a-row-for-0001 is satisfied) but none of the skill's
    # template headings — for S5's "present but wrong" test.
    BOOTSTRAP_ADHOC_README = """\
# Decisions

## Log

| [0001](0001-retry-with-capped-exponential-backoff.md) | Retry transient failures up to 5 times with capped exponential backoff | Accepted |
"""

    # ---- shared helpers -----------------------------------------------------

    def _ws(self, eval_dir: Path) -> Path:
        ws = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
        shutil.copytree(eval_dir / "seed", ws, dirs_exist_ok=True)
        return ws

    def _checks(self, eval_dir: Path, ws: Path) -> dict:
        fixture = run_eval.load_fixture(eval_dir)
        results = objective.run_checks(fixture, str(ws), str(eval_dir / "seed"))
        return {r["id"]: r for r in results}

    def _run_cli(self, eval_dir: Path, ws: Path) -> tuple[int, dict]:
        # N3: run_eval.py resolves+validates registries even for
        # --arm objective-only (main() does this before any arm, on
        # purpose — see its own comment). A stale $SKILLS_EVALS_REGISTRIES
        # or $AGENTSKILLS_DIR left over in the calling shell's environment
        # then makes main() print "registry configuration error: ..." and
        # exit 2 instead of scoring the workspace — and that plain-text
        # output isn't JSON, so json.loads below raised, turning the two
        # *_cli_objective_only_exit_codes tests into errors instead of the
        # clean skip a missing registry gets elsewhere in this class. Drop
        # both vars explicitly rather than inheriting whatever the caller's
        # shell happens to have set; objective-only never installs a skill,
        # so it never needs either.
        env = os.environ.copy()
        env.pop("SKILLS_EVALS_REGISTRIES", None)
        env.pop("AGENTSKILLS_DIR", None)
        cmd = [sys.executable, str(HARNESS_DIR / "run_eval.py"), str(eval_dir),
              "--arm", "objective-only", "--workspace", str(ws)]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                              cwd=str(REPO_ROOT))
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return proc.returncode, payload

    def _copy_fixture_with_link_pattern(self, eval_dir: Path, bad_pattern: str) -> Path:
        """A throwaway copy of `eval_dir` with every link_targets_exist
        check's link_pattern replaced by `bad_pattern` — the checked-in
        fixture.yaml is never touched.
        """
        tmp_eval_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp_eval_dir, ignore_errors=True)
        shutil.copytree(eval_dir, tmp_eval_dir, dirs_exist_ok=True)
        fixture_path = tmp_eval_dir / "fixture.yaml"
        fixture = yaml.safe_load(fixture_path.read_text(encoding="utf-8"))
        mutated = [c for c in fixture["objective_checks"] if c["type"] == "link_targets_exist"]
        self.assertTrue(mutated, "no link_targets_exist check found to mutate")
        for check in mutated:
            check["link_pattern"] = bad_pattern
        fixture_path.write_text(yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")
        return tmp_eval_dir

    def _copy_fixture_with_link_base(self, eval_dir: Path, bad_base: str) -> Path:
        """A throwaway copy of `eval_dir` with every link_targets_exist
        check's `base` replaced by `bad_base` — the checked-in fixture.yaml
        is never touched. Same shape as `_copy_fixture_with_link_pattern`,
        for round 4's N-a: an absolute `base` fixture config mistake.
        """
        tmp_eval_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp_eval_dir, ignore_errors=True)
        shutil.copytree(eval_dir, tmp_eval_dir, dirs_exist_ok=True)
        fixture_path = tmp_eval_dir / "fixture.yaml"
        fixture = yaml.safe_load(fixture_path.read_text(encoding="utf-8"))
        mutated = [c for c in fixture["objective_checks"] if c["type"] == "link_targets_exist"]
        self.assertTrue(mutated, "no link_targets_exist check found to mutate")
        for check in mutated:
            check["base"] = bad_base
        fixture_path.write_text(yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")
        return tmp_eval_dir

    def _link_retry_sh(self, ws: Path, link_comment: str) -> None:
        path = ws / "scripts" / "retry.sh"
        text = path.read_text(encoding="utf-8")
        self.assertIn(self.RETRY_SH_ANCHOR, text, "retry.sh anchor drifted out of the seed")
        text = text.replace(self.RETRY_SH_ANCHOR, link_comment + self.RETRY_SH_ANCHOR, 1)
        path.write_text(text, encoding="utf-8")

    # ---- existing-convention: apply the correct fix in pieces --------------

    def _write_adr_0004(self, ws: Path, content: str | None = None) -> None:
        adr = ws / "docs" / "decisions" / "0004-retry-with-capped-exponential-backoff.md"
        adr.write_text(content or self.EXISTING_ADR_0004, encoding="utf-8")

    def _add_index_row_existing(self, ws: Path) -> None:
        readme = ws / "docs" / "decisions" / "README.md"
        text = readme.read_text(encoding="utf-8")
        self.assertIn(self.EXISTING_README_ANCHOR, text,
                      "README.md anchor drifted out of the seed")
        text = text.replace(self.EXISTING_README_ANCHOR,
                            self.EXISTING_README_ANCHOR + self.EXISTING_INDEX_ROW, 1)
        readme.write_text(text, encoding="utf-8")

    def _link_retry_sh_existing(self, ws: Path) -> None:
        self._link_retry_sh(ws, self.EXISTING_RETRY_LINK)

    def _apply_correct_existing(self, ws: Path) -> None:
        self._write_adr_0004(ws)
        self._add_index_row_existing(ws)
        self._link_retry_sh_existing(ws)

    # ---- bootstrap: apply the correct fix in pieces -------------------------

    def _bootstrap_docs_decisions(self, ws: Path, readme: str, adr: str) -> None:
        decisions = ws / "docs" / "decisions"
        decisions.mkdir(parents=True, exist_ok=True)
        (decisions / "README.md").write_text(readme, encoding="utf-8")
        (decisions / "0001-retry-with-capped-exponential-backoff.md").write_text(
            adr, encoding="utf-8")

    def _link_retry_sh_bootstrap(self, ws: Path) -> None:
        self._link_retry_sh(ws, self.BOOTSTRAP_RETRY_LINK)

    def _add_agents_md_pointer_bootstrap(self, ws: Path) -> None:
        path = ws / "AGENTS.md"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("### Architecture Decision Records", text,
                         "AGENTS.md seed already carries a pointer — bootstrap "
                         "fixture's seed drifted from its existing-convention twin")
        path.write_text(text + self.BOOTSTRAP_AGENTS_MD_POINTER, encoding="utf-8")

    def _apply_correct_bootstrap(self, ws: Path) -> None:
        self._bootstrap_docs_decisions(ws, self.BOOTSTRAP_README, self.BOOTSTRAP_ADR_0001)
        self._link_retry_sh_bootstrap(ws)
        self._add_agents_md_pointer_bootstrap(ws)

    # N4/S6: index-gained-a-row-for-*'s file_matches regex only confirms a
    # row of the right SHAPE is present in docs/decisions/README.md — it
    # never cross-checked the linked filename against the filesystem, so an
    # index row naming a slug no file matches used to still satisfy it. A
    # test-level helper (_assert_index_link_target_exists) covered that gap
    # at the test level rather than widening objective.py's check surface
    # for one fixture. Round 3, S1's fix to link_targets_exist (every link
    # on a line, not just the first) turned readme-index-links-resolve
    # itself into that cross-check for real, so the test-level helper and
    # the two tests built solely to exercise it (which asserted a mismatched
    # slug "passes every fixture check", enumerating a hardcoded tuple of
    # ids that omitted the two link_targets_exist checks) are retired:
    # test_existing_mismatched_index_slug_fails_only_the_link_targets_check
    # and its bootstrap twin already cover this scenario against the real
    # check.

    # ======================================================================
    # existing-convention
    # ======================================================================

    def test_existing_pristine_seed_fails_every_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        for check_id in ("adr-0004-house-format-sections-in-order",
                        "index-gained-a-row-for-0004", "retry-sh-links-the-adr",
                        "exactly-one-new-adr-file"):
            self.assertFalse(by_id[check_id]["passed"], by_id[check_id]["detail"])
        # nothing-else-touched is a trivial pass on the pristine seed.
        self.assertTrue(by_id["nothing-else-touched"]["passed"])

    def test_existing_hand_written_correct_fix_passes_every_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._apply_correct_existing(ws)
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        for check_id, result in by_id.items():
            self.assertTrue(result["passed"], f"{check_id}: {result['detail']}")

    def test_existing_root_readme_rewrite_fails_only_restraint_check(self):
        # S2 (round 2): nothing-else-touched didn't guard the repo-root
        # README.md. This fixture's index lives at docs/decisions/README.md
        # — a different path files_unchanged's "README.md" glob never
        # matched to begin with — so recording the ADR correctly required
        # no edit to the root README.md, but a wholesale, unrelated rewrite
        # of it went uncaught: a correct ADR 0004 plus a rewritten root
        # README.md scored 5/5.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._apply_correct_existing(ws)
        (ws / "README.md").write_text("# Completely different\n", encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        self.assertFalse(by_id["nothing-else-touched"]["passed"],
                         by_id["nothing-else-touched"]["detail"])
        self.assertTrue(by_id["adr-0004-house-format-sections-in-order"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0004"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["exactly-one-new-adr-file"]["passed"])

    def test_existing_mismatched_index_slug_fails_only_the_link_targets_check(self):
        # S3 (round 2): a dangling slug in the index row — naming a file
        # nothing wrote — used to be invisible to every REAL fixture check;
        # only the test-level cross-check above caught it, so a real eval
        # run was blind to it. link_targets_exist closes that gap for real.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._apply_correct_existing(ws)
        readme = ws / "docs" / "decisions" / "README.md"
        text = readme.read_text(encoding="utf-8")
        self.assertIn(self.EXISTING_INDEX_ROW, text)
        mismatched_row = self.EXISTING_INDEX_ROW.replace(
            "0004-retry-with-capped-exponential-backoff.md", "0004-retry-policy.md")
        readme.write_text(text.replace(self.EXISTING_INDEX_ROW, mismatched_row),
                          encoding="utf-8")

        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        self.assertFalse(by_id["readme-index-links-resolve"]["passed"],
                         by_id["readme-index-links-resolve"]["detail"])
        for check_id in ("adr-0004-house-format-sections-in-order",
                        "index-gained-a-row-for-0004", "retry-sh-links-the-adr",
                        "retry-sh-link-resolves", "exactly-one-new-adr-file",
                        "nothing-else-touched"):
            self.assertTrue(by_id[check_id]["passed"], f"{check_id}: {by_id[check_id]['detail']}")

    def test_existing_retry_sh_dangling_link_fails_only_the_link_targets_check(self):
        # S3 (round 2): a retry.sh comment naming a slug no file has — the
        # ADR itself keeps its correct name — is the twin dodge to the
        # index-row one above.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._apply_correct_existing(ws)
        retry_sh = ws / "scripts" / "retry.sh"
        text = retry_sh.read_text(encoding="utf-8")
        self.assertIn("docs/decisions/0004-retry-with-capped-exponential-backoff.md", text)
        retry_sh.write_text(
            text.replace("docs/decisions/0004-retry-with-capped-exponential-backoff.md",
                        "docs/decisions/0004-retry-policy.md"),
            encoding="utf-8")

        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        self.assertFalse(by_id["retry-sh-link-resolves"]["passed"],
                         by_id["retry-sh-link-resolves"]["detail"])
        for check_id in ("adr-0004-house-format-sections-in-order",
                        "index-gained-a-row-for-0004", "retry-sh-links-the-adr",
                        "readme-index-links-resolve", "exactly-one-new-adr-file",
                        "nothing-else-touched"):
            self.assertTrue(by_id[check_id]["passed"], f"{check_id}: {by_id[check_id]['detail']}")

    def test_existing_second_dangling_link_on_index_row_fails_only_the_link_targets_check(self):
        # S1 (round 3): link_targets_exist matched only the FIRST link on a
        # line (regex.search). An otherwise-correct index row with a second,
        # dangling link appended (e.g. a "supersedes" aside) used to score
        # 7/7 — only the link the row's OWN linked slug names was ever
        # inspected.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._apply_correct_existing(ws)
        readme = ws / "docs" / "decisions" / "README.md"
        text = readme.read_text(encoding="utf-8")
        self.assertIn(self.EXISTING_INDEX_ROW, text)
        row_with_second_link = (self.EXISTING_INDEX_ROW.rstrip("\n") +
                                " <!-- supersedes [0002](0002-ghost.md) -->\n")
        readme.write_text(text.replace(self.EXISTING_INDEX_ROW, row_with_second_link),
                          encoding="utf-8")

        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        self.assertFalse(by_id["readme-index-links-resolve"]["passed"],
                         by_id["readme-index-links-resolve"]["detail"])
        self.assertIn("0002-ghost.md", by_id["readme-index-links-resolve"]["detail"])
        for check_id in ("adr-0004-house-format-sections-in-order",
                        "index-gained-a-row-for-0004", "retry-sh-links-the-adr",
                        "retry-sh-link-resolves", "exactly-one-new-adr-file",
                        "nothing-else-touched"):
            self.assertTrue(by_id[check_id]["passed"], f"{check_id}: {by_id[check_id]['detail']}")

    def test_existing_cli_objective_only_exit_codes(self):
        ws_pristine = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        code, _ = self._run_cli((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws_pristine)
        self.assertEqual(code, 1)

        ws_correct = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._apply_correct_existing(ws_correct)
        code, payload = self._run_cli((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws_correct)
        self.assertEqual(code, 0, payload)

    def test_cli_survives_a_link_pattern_with_an_unbalanced_paren(self):
        # S2 (round 3): an unbalanced paren makes link_pattern fail to
        # compile (re.error) — this used to crash run_eval.py with a raw
        # traceback and no JSON on stdout, the same defect class round 2's
        # S4 fixed for file_count.
        tmp_eval_dir = self._copy_fixture_with_link_pattern(
            (REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), r'\[[0-9]{4}\]\(([0-9]{4}-[a-z0-9-]+\.md')
        ws = self._ws(tmp_eval_dir)
        self._apply_correct_existing(ws)
        code, payload = self._run_cli(tmp_eval_dir, ws)
        self.assertEqual(code, 1, payload)
        self.assertIn("checks", payload, payload)
        by_id = {c["id"]: c for c in payload["checks"]}
        self.assertFalse(by_id["readme-index-links-resolve"]["passed"])
        self.assertIn("does not compile", by_id["readme-index-links-resolve"]["detail"])

    def test_cli_survives_a_link_pattern_with_no_capture_group(self):
        # S2 (round 3): a link_pattern with no capture group compiles fine
        # but crashes at m.group(1) with an IndexError — again no JSON on
        # stdout, the twin dodge to the unbalanced-paren case above.
        tmp_eval_dir = self._copy_fixture_with_link_pattern(
            (REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), r'[0-9]{4}-[a-z0-9-]+\.md')
        ws = self._ws(tmp_eval_dir)
        self._apply_correct_existing(ws)
        code, payload = self._run_cli(tmp_eval_dir, ws)
        self.assertEqual(code, 1, payload)
        self.assertIn("checks", payload, payload)
        by_id = {c["id"]: c for c in payload["checks"]}
        self.assertFalse(by_id["readme-index-links-resolve"]["passed"])
        self.assertIn("capture group", by_id["readme-index-links-resolve"]["detail"])

    def test_cli_survives_a_link_targets_exist_check_with_an_absolute_base(self):
        # Round 4, N-a: an absolute `base` in a fixture is a config mistake,
        # refused once up front — through the real CLI, on an otherwise
        # correct workspace, not just the unit-level objective.py call.
        tmp_eval_dir = self._copy_fixture_with_link_base((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), "/etc")
        ws = self._ws(tmp_eval_dir)
        self._apply_correct_existing(ws)
        code, payload = self._run_cli(tmp_eval_dir, ws)
        self.assertEqual(code, 1, payload)
        self.assertIn("checks", payload, payload)
        by_id = {c["id"]: c for c in payload["checks"]}
        detail = by_id["readme-index-links-resolve"]["detail"]
        self.assertFalse(by_id["readme-index-links-resolve"]["passed"])
        self.assertIn("must be a workspace-relative path, not absolute", detail)
        self.assertEqual(detail.count("must be a workspace-relative path"), 1, detail)

    def test_cli_objective_only_ignores_stale_registry_env_vars(self):
        # N3: --arm objective-only never installs a skill, so it shouldn't
        # matter that the calling shell has a stale $SKILLS_EVALS_REGISTRIES
        # or $AGENTSKILLS_DIR pointing nowhere — but run_eval.py's main()
        # resolves+validates registries before ANY arm, objective-only
        # included, so an unfixed _run_cli used to let that bogus override
        # reach the child process, main() printed a plain-text "registry
        # configuration error" and exited 2, and json.loads on that
        # non-JSON stdout raised — an ERROR, not the assertEqual(code, 1)
        # failure a real bug should produce.
        ws_pristine = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        with mock.patch.dict(os.environ, {
            "SKILLS_EVALS_REGISTRIES": "/does/not/exist/anywhere",
            "AGENTSKILLS_DIR": "/does/not/exist/either",
        }):
            code, payload = self._run_cli((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws_pristine)
        self.assertEqual(code, 1, payload)
        self.assertIn("checks", payload)

    def test_existing_adr_written_but_index_not_updated_fails_only_index_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._write_adr_0004(ws)
        self._link_retry_sh_existing(ws)
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        self.assertFalse(by_id["index-gained-a-row-for-0004"]["passed"])
        self.assertTrue(by_id["adr-0004-house-format-sections-in-order"]["passed"],
                        by_id["adr-0004-house-format-sections-in-order"]["detail"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["exactly-one-new-adr-file"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])

    def test_existing_adr_and_index_but_no_code_link_fails_only_link_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._write_adr_0004(ws)
        self._add_index_row_existing(ws)
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        self.assertFalse(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["adr-0004-house-format-sections-in-order"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0004"]["passed"])
        self.assertTrue(by_id["exactly-one-new-adr-file"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])

    def test_existing_wrong_section_order_fails_only_format_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._write_adr_0004(ws, self.EXISTING_ADR_0004_WRONG_ORDER)
        self._add_index_row_existing(ws)
        self._link_retry_sh_existing(ws)
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        self.assertFalse(by_id["adr-0004-house-format-sections-in-order"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0004"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["exactly-one-new-adr-file"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])

    def test_existing_retry_sh_header_gutted_fails_only_the_link_check(self):
        # N5: retry-sh-links-the-adr used to pass on the ADR path appearing
        # ANYWHERE in retry.sh, with nothing guarding the header's own
        # decision sentence — the thing the judge rubric leans on to grade
        # the ADR's content against. Keep the ADR link but gut that
        # sentence; only this check should now catch it.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._apply_correct_existing(ws)
        retry_sh = ws / "scripts" / "retry.sh"
        text = retry_sh.read_text(encoding="utf-8")
        sentence = "retry a transient command failure with capped exponential backoff"
        self.assertIn(sentence, text, "retry.sh header sentence drifted out of the seed")
        retry_sh.write_text(text.replace(sentence, "do a thing"), encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        self.assertFalse(by_id["retry-sh-links-the-adr"]["passed"],
                         by_id["retry-sh-links-the-adr"]["detail"])
        self.assertTrue(by_id["adr-0004-house-format-sections-in-order"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0004"]["passed"])
        self.assertTrue(by_id["exactly-one-new-adr-file"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])

    def test_existing_retry_sh_link_appended_after_body_fails_the_link_check(self):
        # N1 (round 2): retry-sh-links-the-adr's description claimed the
        # HEADER links the ADR, but the old regex matched the path ANYWHERE
        # in the file — appending the link after the function body, nowhere
        # near the header, still passed.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._write_adr_0004(ws)
        self._add_index_row_existing(ws)
        retry_sh = ws / "scripts" / "retry.sh"
        retry_sh.write_text(
            retry_sh.read_text(encoding="utf-8") +
            "\n# See docs/decisions/0004-retry-with-capped-exponential-backoff.md\n",
            encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        self.assertFalse(by_id["retry-sh-links-the-adr"]["passed"],
                         by_id["retry-sh-links-the-adr"]["detail"])
        self.assertTrue(by_id["adr-0004-house-format-sections-in-order"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0004"]["passed"])
        self.assertTrue(by_id["exactly-one-new-adr-file"]["passed"])

    def test_existing_second_adr_for_changelog_fact_fails_only_count_check(self):
        # The correct fix, PLUS an extra ADR nobody asked for, recording
        # CHANGELOG.md's routine dependency-removal fact. Everything about
        # ADR 0004 itself, the index row, and the code link is still right —
        # only the count check should catch the extra file.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._apply_correct_existing(ws)
        (ws / "docs" / "decisions" / "0005-remove-moment-dependency.md").write_text(
            self.DECOY_ADR_FOR_CHANGELOG, encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        self.assertFalse(by_id["exactly-one-new-adr-file"]["passed"],
                         by_id["exactly-one-new-adr-file"]["detail"])
        self.assertTrue(by_id["adr-0004-house-format-sections-in-order"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0004"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])

    def test_existing_delete_and_replace_adr_passes_the_stale_count_check_but_fails_restraint(self):
        # S2: exactly-one-new-adr-file is a TOTAL count (min=4, max=4).
        # Deleting 0002 and adding a differently-numbered decoy (0006) for
        # the CHANGELOG's routine fact keeps the total at 4 — the count
        # check alone scores this 4/4. nothing-else-touched is what catches
        # it: 0002 must stay byte-identical, and its absence is a violation
        # even though total count still looks right.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"))
        self._apply_correct_existing(ws)
        (ws / "docs" / "decisions" / "0002-poll-fulfillment-service-every-30s.md").unlink()
        (ws / "docs" / "decisions" / "0006-remove-moment-dependency.md").write_text(
            self.DECOY_ADR_FOR_CHANGELOG.replace("# 0005.", "# 0006.", 1),
            encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "existing-convention"), ws)
        self.assertTrue(by_id["exactly-one-new-adr-file"]["passed"],
                        by_id["exactly-one-new-adr-file"]["detail"])
        self.assertFalse(by_id["nothing-else-touched"]["passed"],
                         by_id["nothing-else-touched"]["detail"])
        # Round 4, N-b: deleting 0002 also dangles its own index row (the
        # seed's README.md still links to it); record that here rather than
        # leaving it unasserted. This goes red if link_targets_exist is ever
        # narrowed to fail open on a dangling target.
        self.assertFalse(by_id["readme-index-links-resolve"]["passed"],
                         by_id["readme-index-links-resolve"]["detail"])
        self.assertTrue(by_id["adr-0004-house-format-sections-in-order"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0004"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])

    # ======================================================================
    # bootstrap
    # ======================================================================

    def test_bootstrap_pristine_seed_fails_every_real_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        for check_id in ("readme-bootstrapped-in-skill-shape",
                        "index-gained-a-row-for-0001", "adr-0001-sections-in-order",
                        "retry-sh-links-the-adr", "exactly-one-adr-file",
                        "agents-md-gained-the-pointer"):
            self.assertFalse(by_id[check_id]["passed"], by_id[check_id]["detail"])
        # Restraint is a trivial pass on the pristine seed: nothing changed yet.
        self.assertTrue(by_id["nothing-else-touched"]["passed"])

    def test_bootstrap_hand_written_correct_fix_passes_every_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._apply_correct_bootstrap(ws)
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        for check_id, result in by_id.items():
            self.assertTrue(result["passed"], f"{check_id}: {result['detail']}")

    def test_bootstrap_mismatched_index_slug_fails_only_the_link_targets_check(self):
        # S3 (round 2), bootstrap side: same gap as existing-convention's
        # twin test — a dangling index-row slug is now caught by a real
        # fixture check, not only by the unit-test cross-check above.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._apply_correct_bootstrap(ws)
        readme = ws / "docs" / "decisions" / "README.md"
        text = readme.read_text(encoding="utf-8")
        original_row = ("| [0001](0001-retry-with-capped-exponential-backoff.md) | Retry "
                        "transient failures up to 5 times with capped exponential "
                        "backoff | Accepted |")
        self.assertIn(original_row, text)
        mismatched_row = original_row.replace(
            "0001-retry-with-capped-exponential-backoff.md", "0001-retry-policy.md")
        readme.write_text(text.replace(original_row, mismatched_row), encoding="utf-8")

        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["readme-index-links-resolve"]["passed"],
                         by_id["readme-index-links-resolve"]["detail"])
        for check_id in ("readme-bootstrapped-in-skill-shape", "index-gained-a-row-for-0001",
                        "adr-0001-sections-in-order", "retry-sh-links-the-adr",
                        "retry-sh-link-resolves", "exactly-one-adr-file",
                        "nothing-else-touched", "agents-md-gained-the-pointer"):
            self.assertTrue(by_id[check_id]["passed"], f"{check_id}: {by_id[check_id]['detail']}")

    def test_bootstrap_retry_sh_dangling_link_fails_only_the_link_targets_check(self):
        # S3 (round 2), bootstrap side: same twin dodge as existing-
        # convention's retry.sh test.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._apply_correct_bootstrap(ws)
        retry_sh = ws / "scripts" / "retry.sh"
        text = retry_sh.read_text(encoding="utf-8")
        self.assertIn("docs/decisions/0001-retry-with-capped-exponential-backoff.md", text)
        retry_sh.write_text(
            text.replace("docs/decisions/0001-retry-with-capped-exponential-backoff.md",
                        "docs/decisions/0001-retry-policy.md"),
            encoding="utf-8")

        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["retry-sh-link-resolves"]["passed"],
                         by_id["retry-sh-link-resolves"]["detail"])
        for check_id in ("readme-bootstrapped-in-skill-shape", "index-gained-a-row-for-0001",
                        "adr-0001-sections-in-order", "retry-sh-links-the-adr",
                        "readme-index-links-resolve", "exactly-one-adr-file",
                        "nothing-else-touched", "agents-md-gained-the-pointer"):
            self.assertTrue(by_id[check_id]["passed"], f"{check_id}: {by_id[check_id]['detail']}")

    def test_bootstrap_cli_objective_only_exit_codes(self):
        ws_pristine = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        code, _ = self._run_cli((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws_pristine)
        self.assertEqual(code, 1)

        ws_correct = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._apply_correct_bootstrap(ws_correct)
        code, payload = self._run_cli((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws_correct)
        self.assertEqual(code, 0, payload)

    def test_bootstrap_adr_written_but_index_not_updated_fails_only_index_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        # README carries the bootstrap shape but never gained the 0001 row.
        readme_without_row = self.BOOTSTRAP_README.replace(
            "| [0001](0001-retry-with-capped-exponential-backoff.md) | Retry "
            "transient failures up to 5 times with capped exponential "
            "backoff | Accepted |\n", "")
        self._bootstrap_docs_decisions(ws, readme_without_row, self.BOOTSTRAP_ADR_0001)
        self._link_retry_sh_bootstrap(ws)
        self._add_agents_md_pointer_bootstrap(ws)
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["index-gained-a-row-for-0001"]["passed"])
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"],
                        by_id["readme-bootstrapped-in-skill-shape"]["detail"])
        self.assertTrue(by_id["adr-0001-sections-in-order"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["exactly-one-adr-file"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])
        self.assertTrue(by_id["agents-md-gained-the-pointer"]["passed"],
                        by_id["agents-md-gained-the-pointer"]["detail"])

    def test_bootstrap_adr_and_index_but_no_code_link_fails_only_link_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._bootstrap_docs_decisions(ws, self.BOOTSTRAP_README, self.BOOTSTRAP_ADR_0001)
        self._add_agents_md_pointer_bootstrap(ws)
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0001"]["passed"])
        self.assertTrue(by_id["adr-0001-sections-in-order"]["passed"])
        self.assertTrue(by_id["exactly-one-adr-file"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])
        self.assertTrue(by_id["agents-md-gained-the-pointer"]["passed"])

    def test_bootstrap_wrong_section_order_fails_only_format_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._bootstrap_docs_decisions(
            ws, self.BOOTSTRAP_README, self.BOOTSTRAP_ADR_0001_WRONG_ORDER)
        self._link_retry_sh_bootstrap(ws)
        self._add_agents_md_pointer_bootstrap(ws)
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["adr-0001-sections-in-order"]["passed"])
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0001"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["exactly-one-adr-file"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])
        self.assertTrue(by_id["agents-md-gained-the-pointer"]["passed"])

    def test_bootstrap_retry_sh_header_gutted_fails_only_the_link_check(self):
        # N5, bootstrap side: same guard as existing-convention's twin test.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._apply_correct_bootstrap(ws)
        retry_sh = ws / "scripts" / "retry.sh"
        text = retry_sh.read_text(encoding="utf-8")
        sentence = "retry a transient command failure with capped exponential backoff"
        self.assertIn(sentence, text, "retry.sh header sentence drifted out of the seed")
        retry_sh.write_text(text.replace(sentence, "do a thing"), encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["retry-sh-links-the-adr"]["passed"],
                         by_id["retry-sh-links-the-adr"]["detail"])
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0001"]["passed"])
        self.assertTrue(by_id["adr-0001-sections-in-order"]["passed"])
        self.assertTrue(by_id["exactly-one-adr-file"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])
        self.assertTrue(by_id["agents-md-gained-the-pointer"]["passed"])

    def test_bootstrap_retry_sh_link_appended_after_body_fails_the_link_check(self):
        # N1 (round 2), bootstrap side: same anchoring gap as existing-
        # convention's twin test.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._bootstrap_docs_decisions(ws, self.BOOTSTRAP_README, self.BOOTSTRAP_ADR_0001)
        self._add_agents_md_pointer_bootstrap(ws)
        retry_sh = ws / "scripts" / "retry.sh"
        retry_sh.write_text(
            retry_sh.read_text(encoding="utf-8") +
            "\n# See docs/decisions/0001-retry-with-capped-exponential-backoff.md\n",
            encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["retry-sh-links-the-adr"]["passed"],
                         by_id["retry-sh-links-the-adr"]["detail"])
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0001"]["passed"])
        self.assertTrue(by_id["adr-0001-sections-in-order"]["passed"])
        self.assertTrue(by_id["exactly-one-adr-file"]["passed"])
        self.assertTrue(by_id["agents-md-gained-the-pointer"]["passed"])

    def test_bootstrap_second_adr_for_changelog_fact_fails_only_count_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._apply_correct_bootstrap(ws)
        (ws / "docs" / "decisions" / "0002-remove-moment-dependency.md").write_text(
            self.BOOTSTRAP_DECOY_ADR_FOR_CHANGELOG, encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["exactly-one-adr-file"]["passed"],
                         by_id["exactly-one-adr-file"]["detail"])
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0001"]["passed"])
        self.assertTrue(by_id["adr-0001-sections-in-order"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])
        self.assertTrue(by_id["agents-md-gained-the-pointer"]["passed"])

    def test_bootstrap_touching_changelog_fails_only_restraint_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._apply_correct_bootstrap(ws)
        changelog = ws / "CHANGELOG.md"
        changelog.write_text(
            changelog.read_text(encoding="utf-8") + "\n## 1.3.1\n\n- Unrelated edit.\n",
            encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["nothing-else-touched"]["passed"])
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0001"]["passed"])
        self.assertTrue(by_id["adr-0001-sections-in-order"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["exactly-one-adr-file"]["passed"])
        self.assertTrue(by_id["agents-md-gained-the-pointer"]["passed"])

    def test_bootstrap_readme_edit_fails_only_restraint_check(self):
        # The B1 bug this fixture used to have: with no AGENTS.md in the
        # seed, Bootstrap step 4 ("AGENTS.md, or README.md if there's no
        # AGENTS.md") pointed a skill-faithful agent at README.md, and that
        # correct-per-the-skill answer lost nothing-else-touched. Now the
        # seed carries an AGENTS.md, so editing README.md instead is a
        # genuine, not merely accidental, violation.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._apply_correct_bootstrap(ws)
        readme = ws / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") +
            "\n### Architecture Decision Records\n\nSee `docs/decisions/`.\n",
            encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["nothing-else-touched"]["passed"])
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0001"]["passed"])
        self.assertTrue(by_id["adr-0001-sections-in-order"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["exactly-one-adr-file"]["passed"])
        self.assertTrue(by_id["agents-md-gained-the-pointer"]["passed"])

    def test_bootstrap_missing_agents_md_pointer_fails_only_the_pointer_check(self):
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._bootstrap_docs_decisions(ws, self.BOOTSTRAP_README, self.BOOTSTRAP_ADR_0001)
        self._link_retry_sh_bootstrap(ws)
        # No _add_agents_md_pointer_bootstrap call: AGENTS.md stays pristine.
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["agents-md-gained-the-pointer"]["passed"])
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0001"]["passed"])
        self.assertTrue(by_id["adr-0001-sections-in-order"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["exactly-one-adr-file"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])

    def test_bootstrap_agents_md_rewritten_from_scratch_fails_the_pointer_check(self):
        # A new AGENTS.md carrying the heading but none of the seed's
        # original content is a rewrite, not the append Bootstrap step 4
        # calls for — agents-md-gained-the-pointer's must_match on an
        # original paragraph is what catches this.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._apply_correct_bootstrap(ws)
        (ws / "AGENTS.md").write_text(
            "# AGENTS.md\n\n### Architecture Decision Records\n\n"
            "Non-obvious decisions live in `docs/decisions/`.\n",
            encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["agents-md-gained-the-pointer"]["passed"],
                         by_id["agents-md-gained-the-pointer"]["detail"])
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0001"]["passed"])
        self.assertTrue(by_id["adr-0001-sections-in-order"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["exactly-one-adr-file"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])

    def test_bootstrap_bare_pointer_heading_with_no_link_fails_the_pointer_check(self):
        # S1 (round 2): a bare "### Architecture Decision Records" heading
        # with no link into docs/decisions/ used to satisfy
        # agents-md-gained-the-pointer (7/7) — a pointer must point.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._bootstrap_docs_decisions(ws, self.BOOTSTRAP_README, self.BOOTSTRAP_ADR_0001)
        self._link_retry_sh_bootstrap(ws)
        agents = ws / "AGENTS.md"
        agents.write_text(
            agents.read_text(encoding="utf-8") + "\n### Architecture Decision Records\n",
            encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["agents-md-gained-the-pointer"]["passed"],
                         by_id["agents-md-gained-the-pointer"]["detail"])
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0001"]["passed"])
        self.assertTrue(by_id["adr-0001-sections-in-order"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["exactly-one-adr-file"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])

    def test_bootstrap_agents_md_gutted_conventions_section_fails_the_pointer_check(self):
        # S1 (round 2): deleting AGENTS.md's ## Conventions section (which
        # names scripts/retry.sh) while keeping a correct pointer
        # heading+link still scored 7/7 — must_match only checked the
        # heading and the file's FIRST paragraph, never that the append
        # landed on top of the rest of the file rather than replacing it.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._apply_correct_bootstrap(ws)
        agents = ws / "AGENTS.md"
        text = agents.read_text(encoding="utf-8")
        gutted = text.split("## Conventions")[0] + self.BOOTSTRAP_AGENTS_MD_POINTER
        agents.write_text(gutted, encoding="utf-8")
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["agents-md-gained-the-pointer"]["passed"],
                         by_id["agents-md-gained-the-pointer"]["detail"])
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"])
        self.assertTrue(by_id["index-gained-a-row-for-0001"]["passed"])
        self.assertTrue(by_id["adr-0001-sections-in-order"]["passed"])
        self.assertTrue(by_id["retry-sh-links-the-adr"]["passed"])
        self.assertTrue(by_id["exactly-one-adr-file"]["passed"])
        self.assertTrue(by_id["nothing-else-touched"]["passed"])

    def test_bootstrap_adhoc_readme_shape_fails_only_the_shape_check(self):
        # readme-bootstrapped-in-skill-shape had no "present but wrong" test:
        # an ad-hoc README with an index row but none of the skill's own
        # template headings must fail ONLY this check.
        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._bootstrap_docs_decisions(ws, self.BOOTSTRAP_ADHOC_README, self.BOOTSTRAP_ADR_0001)
        self._link_retry_sh_bootstrap(ws)
        self._add_agents_md_pointer_bootstrap(ws)
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertFalse(by_id["readme-bootstrapped-in-skill-shape"]["passed"],
                         by_id["readme-bootstrapped-in-skill-shape"]["detail"])
        for check_id in ("index-gained-a-row-for-0001", "adr-0001-sections-in-order",
                         "retry-sh-links-the-adr", "exactly-one-adr-file",
                         "nothing-else-touched", "agents-md-gained-the-pointer"):
            self.assertTrue(by_id[check_id]["passed"], f"{check_id}: {by_id[check_id]['detail']}")

    # ======================================================================
    # The bootstrap README-shape check is pinned to the skill's OWN template
    # at test time, so a future edit to that template that drops one of the
    # headings this fixture relies on fails HERE, loudly, instead of the
    # fixture silently testing a shape the skill no longer produces.
    # ======================================================================

    def _derive_bootstrap_readme(self, template_text: str, index_row: str) -> str:
        """Copy the skill's own bootstrap template the way it instructs: drop
        its two-line "seeded from" quote block (both lines start with `>`) —
        NOT just the first line, which used to leave the second line's
        "house style, then delete this quote line." dangling in the derived
        README — and fill in the index row.
        """
        lines = [line for line in template_text.splitlines()
                if not line.strip().startswith(">")]
        derived = "\n".join(lines) + "\n"
        self.assertIn("| _none yet_ | | |", derived,
                      "template's empty-index placeholder drifted — update "
                      "this test's replacement below")
        return derived.replace("| _none yet_ | | |", index_row)

    def test_derive_bootstrap_readme_drops_the_whole_seeded_from_quote_block(self):
        # N2: the old filter only dropped lines starting with "> Seeded
        # from", leaving the template's second quote line ("> house style,
        # then delete this quote line.") behind in the derived README.
        template_text = (
            "# Architecture Decision Records\n\n"
            "> Seeded from the `writing-adrs` skill. Adjust wording to match "
            "this repo's\n"
            "> house style, then delete this quote line.\n\n"
            "## Index\n\n"
            "| ADR | Title | Status |\n"
            "|-----|-------|--------|\n"
            "| _none yet_ | | |\n"
        )
        derived = self._derive_bootstrap_readme(
            template_text, "| [0001](x.md) | T | Accepted |")
        self.assertNotIn(">", derived)
        self.assertNotIn("Seeded from", derived)
        self.assertNotIn("house style", derived)

    def test_bootstrap_readme_derived_from_live_skill_template_still_passes(self):
        registries = run_eval.resolve_registries(
            None, os.environ.get("SKILLS_EVALS_REGISTRIES"), REPO_ROOT,
            os.environ.get("AGENTSKILLS_DIR"))
        entry = registries["acsi-agentskills"]
        if not entry["path"].is_dir():
            reason = (f"no acsi-agentskills checkout at {entry['path']} — skipping "
                      "the live-template drift check")
            print(reason)
            self.skipTest(reason)

        glob_pattern = run_eval._skill_md_glob(entry["layout"], "writing-adrs")
        matches = sorted(p.parent for p in entry["path"].glob(glob_pattern) if p.is_file())
        if not matches:
            reason = (f"no SKILL.md matched {entry['path'] / glob_pattern} — "
                      "skipping the live-template drift check")
            print(reason)
            self.skipTest(reason)

        template_path = matches[0] / "references" / "decisions-README-template.md"
        if not template_path.is_file():
            reason = (f"writing-adrs skill has no {template_path} — skipping "
                      "the live-template drift check")
            print(reason)
            self.skipTest(reason)

        # Derive a README the way the skill instructs: copy the template,
        # drop its two-line "seeded from" quote block, fill in the index
        # row. If the live template drops one of the headings
        # readme-bootstrapped-in-skill-shape looks for, this derived text
        # stops carrying it and the assertion below is what catches the
        # drift.
        template_text = template_path.read_text(encoding="utf-8")
        derived_readme = self._derive_bootstrap_readme(
            template_text,
            "| [0001](0001-retry-with-capped-exponential-backoff.md) | Retry "
            "transient failures up to 5 times with capped exponential "
            "backoff | Accepted |")

        ws = self._ws((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"))
        self._bootstrap_docs_decisions(ws, derived_readme, self.BOOTSTRAP_ADR_0001)
        self._link_retry_sh_bootstrap(ws)
        by_id = self._checks((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), ws)
        self.assertTrue(by_id["readme-bootstrapped-in-skill-shape"]["passed"],
                        by_id["readme-bootstrapped-in-skill-shape"]["detail"])
        self.assertTrue(by_id["index-gained-a-row-for-0001"]["passed"],
                        by_id["index-gained-a-row-for-0001"]["detail"])

    # ======================================================================
    # S6: the repo README's evals/ tree omitted writing-adrs/ entirely.
    # ======================================================================

    def test_readme_lists_the_writing_adrs_eval_directory(self):
        readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("writing-adrs/", readme_text)

    # ======================================================================
    # Round 3, S5: DESIGN.md's Class A candidates list still named
    # writing-adrs after it shipped its own eval (this one) — the same
    # graduation rename-pdfs and post-failure-comment already record for
    # themselves.
    # ======================================================================

    def test_design_doc_no_longer_lists_writing_adrs_as_a_class_a_candidate(self):
        design_text = (REPO_ROOT / "DESIGN.md").read_text(encoding="utf-8")
        self.assertNotIn("`writing-adrs` (the format half)", design_text)
        self.assertIn("`writing-adrs` graduated out of this list", design_text)
        self.assertIn("`evals/writing-adrs/` (issue #80)", design_text)

    # ======================================================================
    # N5: the two fixtures' scope notes disagreed about direction — both sit
    # before objective_checks: (i.e. the named paths are always BELOW the
    # note in the file), but bootstrap's said "above".
    # ======================================================================

    def test_scope_note_direction_agrees_in_both_fixtures(self):
        directions = {}
        for eval_dir in ((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), (REPO_ROOT / "evals" / "writing-adrs" / "existing-convention")):
            lines = (eval_dir / "fixture.yaml").read_text(encoding="utf-8").splitlines()
            # Comment lines wrap, so join them before matching rather than
            # assuming "named" and "above"/"below" share one physical line.
            normalized = " ".join(line.lstrip("#").strip()
                                  for line in lines if line.startswith("#"))
            m = re.search(r"the paths named (above|below)", normalized)
            self.assertIsNotNone(m, f"{eval_dir.name}: scope note direction not found")
            directions[eval_dir.name] = m.group(1)
        self.assertEqual(directions["bootstrap"], directions["existing-convention"],
                         directions)
        self.assertEqual(directions["bootstrap"], "below")

    # ======================================================================
    # N1: both seeds' retry.sh cited a 2026-06-02 outage / PR #142; issue #80
    # says 2026-03 / PR #41.
    # ======================================================================

    def test_retry_sh_cites_the_issues_outage_date_and_pr_in_both_seeds(self):
        for eval_dir in ((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), (REPO_ROOT / "evals" / "writing-adrs" / "existing-convention")):
            with self.subTest(eval_dir=eval_dir.name):
                text = (eval_dir / "seed" / "scripts" / "retry.sh").read_text(
                    encoding="utf-8")
                self.assertIn("2026-03", text)
                self.assertIn("PR #41", text)
                self.assertNotIn("2026-06-02", text)
                self.assertNotIn("PR #142", text)

    # ======================================================================
    # N6: both seeds' scripts/retry.sh were mode 100644; every other seed's
    # scripts are 100755.
    # ======================================================================

    def test_retry_sh_is_executable_in_both_seeds(self):
        for eval_dir in ((REPO_ROOT / "evals" / "writing-adrs" / "bootstrap"), (REPO_ROOT / "evals" / "writing-adrs" / "existing-convention")):
            with self.subTest(eval_dir=eval_dir.name):
                path = eval_dir / "seed" / "scripts" / "retry.sh"
                self.assertTrue(os.access(path, os.X_OK), f"{path} is not executable")

    # ======================================================================
    # N7: the two seeds must stay byte-identical except for what each
    # fixture's premise changes (existing-convention already has
    # docs/decisions/, and its AGENTS.md already carries the ADR pointer).
    # ======================================================================

    def test_seed_files_are_byte_identical_except_for_their_premise(self):
        bootstrap_seed = (REPO_ROOT / "evals" / "writing-adrs" / "bootstrap") / "seed"
        existing_seed = (REPO_ROOT / "evals" / "writing-adrs" / "existing-convention") / "seed"

        for rel in ("README.md", "CHANGELOG.md", "scripts/retry.sh"):
            with self.subTest(file=rel):
                self.assertEqual(
                    (bootstrap_seed / rel).read_bytes(),
                    (existing_seed / rel).read_bytes(),
                    f"{rel} differs between the two seeds")

        # AGENTS.md differs by exactly the pointer paragraph the
        # existing-convention premise (docs/decisions/ already exists) adds.
        bootstrap_agents = (bootstrap_seed / "AGENTS.md").read_text(encoding="utf-8")
        existing_agents = (existing_seed / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(
            existing_agents, bootstrap_agents + self.BOOTSTRAP_AGENTS_MD_POINTER,
            "AGENTS.md diverges by more than the ADR pointer paragraph")

        # docs/decisions/ is the other premise difference: bootstrap has none.
        self.assertFalse((bootstrap_seed / "docs").exists())
        self.assertTrue((existing_seed / "docs" / "decisions" / "README.md").is_file())

        def _files(root: Path) -> set[str]:
            return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}

        bootstrap_files = _files(bootstrap_seed)
        existing_files = _files(existing_seed)
        self.assertEqual(bootstrap_files - existing_files, set(),
                         "bootstrap seed has files existing-convention lacks")
        self.assertEqual(existing_files - bootstrap_files, {
            "docs/decisions/README.md",
            "docs/decisions/0001-cache-order-status-in-redis.md",
            "docs/decisions/0002-poll-fulfillment-service-every-30s.md",
            "docs/decisions/0003-poll-fulfillment-service-every-10s.md",
        }, "existing-convention seed's extra files are not exactly its "
           "docs/decisions/ premise")
