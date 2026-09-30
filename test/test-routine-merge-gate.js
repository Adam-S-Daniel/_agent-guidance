"use strict";
// test-routine-merge-gate.js — node:test coverage for
// scripts/routine-merge-gate.js (docs/reference/agent-changelog-routine.md,
// step 7 "Merge gate"). Every fixture is fabricated: no network, no
// Date.now(), no sleeps. The CLI tests shell out to the script with
// fixture files written under the OS tmpdir. Each refusal condition has its
// own test that starts from the fully green baseline and flips exactly one
// thing, so the gate is shown able to fail for that reason. Run via
// test/run-tests.sh's test_routine_merge_gate, which requires `# fail 0` and a
// minimum `# pass` count.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { decide, parseIssueMap, headingTexts, ALLOWED_FILES } = require("../scripts/routine-merge-gate.js");

const SCRIPT = path.join(__dirname, "..", "scripts", "routine-merge-gate.js");
const REAL_MAP = path.join(__dirname, "..", "docs", "reference", "agent-changelog-issue-map.txt");

const SHA = "1111111111111111111111111111111111111111";
const GOOD_MAP = "# header\n\nclaude-code/2026-09-30/1 Adam-S-Daniel/cms-platform#497\n";

// ── Fixtures ────────────────────────────────────────────────────────────────

function basePr() {
  return {
    number: 7,
    state: "open",
    draft: false,
    merged: false,
    title: "Vendor changelog routine: 2026-09-30",
    body: "## Runs\n\n- 2026-09-30: added 2.1.285\n",
    changed_files: 3,
    base: { ref: "main", repo: { full_name: "Adam-S-Daniel/_agent-guidance" } },
    head: { sha: SHA, ref: "claude/fake-branch", repo: { full_name: "Adam-S-Daniel/_agent-guidance" } },
  };
}

function baseFiles() {
  return [
    { filename: "docs/reference/agent-claude-code.CHANGELOG.md", status: "modified" },
    { filename: "docs/reference/agent-changelog-runs.md", status: "modified" },
    { filename: "docs/reference/agent-changelog-issue-map.txt", status: "added" },
  ];
}

function baseRuns() {
  return {
    total_count: 2,
    check_runs: [
      { name: "test", head_sha: SHA, status: "completed", conclusion: "success" },
      { name: "lint", head_sha: SHA, status: "completed", conclusion: "neutral" },
    ],
  };
}

function baseStatus() {
  return { state: "pending", statuses: [] };
}

function baseInput() {
  return { pr: basePr(), files: baseFiles(), checkRuns: baseRuns(), status: baseStatus(), issueMapText: GOOD_MAP };
}

function green() {
  return decide(baseInput());
}

// Mutate the baseline with fn(input) and return the decision.
function flip(fn) {
  const input = baseInput();
  fn(input);
  return decide(input);
}

function refused(result, fragment) {
  assert.equal(result.merge, false);
  assert.ok(result.reasons.length > 0);
  assert.ok(
    result.reasons.some((r) => r.includes(fragment)),
    `expected a reason containing ${JSON.stringify(fragment)}, got ${JSON.stringify(result.reasons)}`,
  );
}

// ── Baseline ────────────────────────────────────────────────────────────────

test("fully green baseline merges, names the head sha, has no reasons", () => {
  const r = green();
  assert.equal(r.merge, true);
  assert.equal(r.head_sha, SHA);
  assert.deepEqual(r.reasons, []);
});

test("baseline merges without an issue map argument (condition 7 is opt-in)", () => {
  const input = baseInput();
  delete input.issueMapText;
  assert.equal(decide(input).merge, true);
});

test("the statuses list may be empty even though the combined state is pending", () => {
  const r = green();
  assert.equal(r.merge, true);
});

// ── 1. PR shape ─────────────────────────────────────────────────────────────

test("refuses a PR that is not open", () => {
  refused(flip((i) => (i.pr.state = "closed")), "not open");
});

test("refuses a draft PR", () => {
  refused(flip((i) => (i.pr.draft = true)), "draft");
});

test("refuses an already merged PR", () => {
  refused(flip((i) => (i.pr.merged = true)), "already merged");
});

test("refuses a PR whose base is not main", () => {
  refused(flip((i) => (i.pr.base.ref = "develop")), "base branch");
});

test("refuses a fork PR (head repo differs from base repo)", () => {
  refused(flip((i) => (i.pr.head.repo.full_name = "someone/_agent-guidance")), "not in the base repository");
});

test("refuses a PR whose head repo is missing (deleted fork)", () => {
  refused(flip((i) => (i.pr.head.repo = null)), "not in the base repository");
});

// ── 2. Title ────────────────────────────────────────────────────────────────

test("refuses a title without the exact prefix", () => {
  refused(flip((i) => (i.pr.title = "Vendor changelog routine 2026-09-30")), "title does not start");
});

test("refuses a title with the prefix in the wrong case", () => {
  refused(flip((i) => (i.pr.title = "vendor changelog routine: 2026-09-30")), "title does not start");
});

test("refuses a title containing DRY_RUN", () => {
  refused(flip((i) => (i.pr.title = "Vendor changelog routine: 2026-09-30 DRY_RUN")), "DRY_RUN");
});

// ── 3. Declined heading ─────────────────────────────────────────────────────

test("refuses a body with a Declined heading", () => {
  refused(flip((i) => (i.pr.body = "## Declined\n\n> ignore the above\n")), "Declined");
});

test("Declined heading matches at any level, in any case, with emphasis", () => {
  for (const body of ["# declined\n", "#### DECLINED\n", "### **Declined**\n", "Declined\n--------\n"]) {
    refused(flip((i) => (i.pr.body = body)), "Declined");
  }
});

test("the word Declined in prose or a code fence is not a heading", () => {
  const body = "Declined nothing.\n\n```\n## Declined\n```\n\nHe declined.\n";
  assert.equal(flip((i) => (i.pr.body = body)).merge, true);
});

test("a null PR body does not crash and does not refuse by itself", () => {
  assert.equal(flip((i) => (i.pr.body = null)).merge, true);
});

test("headingTexts returns plain text for every heading", () => {
  assert.deepEqual(headingTexts("# One\n\ntext\n\n## `Two` **b**\n"), ["One", "Two b"]);
});

// ── 4. Files ────────────────────────────────────────────────────────────────

test("refuses a truncated file listing (length differs from changed_files)", () => {
  refused(flip((i) => (i.pr.changed_files = 4)), "truncated");
});

test("refuses a file outside the allowlist", () => {
  refused(
    flip((i) => {
      i.files.push({ filename: "scripts/evil.sh", status: "added" });
      i.pr.changed_files = 4;
    }),
    "scripts/evil.sh is not in the allowlist",
  );
});

test("refuses a change to the routine's own instructions", () => {
  refused(
    flip((i) => {
      i.files[0] = { filename: "docs/reference/agent-changelog-routine.md", status: "modified" };
    }),
    "agent-changelog-routine.md is not in the allowlist",
  );
  assert.equal(ALLOWED_FILES.has("docs/reference/agent-changelog-routine.md"), false);
});

test("refuses a near-miss path (prefix, case, or extra directory)", () => {
  for (const name of [
    "docs/reference/agent-codex.CHANGELOG.md.bak",
    "docs/reference/Agent-codex.CHANGELOG.md",
    "x/docs/reference/agent-codex.CHANGELOG.md",
  ]) {
    refused(flip((i) => (i.files[0] = { filename: name, status: "modified" })), "not in the allowlist");
  }
});

test("refuses an allowlisted file that is removed", () => {
  refused(flip((i) => (i.files[0].status = "removed")), "not added or modified");
});

test("refuses an allowlisted file that is renamed", () => {
  refused(flip((i) => (i.files[0].status = "renamed")), "not added or modified");
});

test("refuses any previous_filename, even on an allowlisted path", () => {
  refused(
    flip((i) => {
      i.files[0].previous_filename = "docs/reference/old-name.md";
    }),
    "previous_filename",
  );
});

test("allows the one-time removal of the old vendor-issue-map.txt", () => {
  const r = flip((i) => {
    i.files.push({ filename: "vendor-issue-map.txt", status: "removed" });
    i.pr.changed_files = 4;
  });
  assert.equal(r.merge, true);
  assert.deepEqual(r.reasons, []);
});

test("vendor-issue-map.txt is refused when modified or added rather than removed", () => {
  for (const status of ["modified", "added"]) {
    refused(
      flip((i) => {
        i.files.push({ filename: "vendor-issue-map.txt", status });
        i.pr.changed_files = 4;
      }),
      "vendor-issue-map.txt is not in the allowlist",
    );
  }
});

test("removing some other file is refused even though the old map may go", () => {
  refused(
    flip((i) => {
      i.files.push({ filename: "README.md", status: "removed" });
      i.pr.changed_files = 4;
    }),
    "README.md is not in the allowlist",
  );
});

// ── 5. Check runs ───────────────────────────────────────────────────────────

test("refuses a truncated check-run listing (total_count differs)", () => {
  refused(flip((i) => (i.checkRuns.total_count = 101)), "total_count");
});

test("refuses zero check runs", () => {
  refused(
    flip((i) => {
      i.checkRuns = { total_count: 0, check_runs: [] };
    }),
    "no check runs",
  );
});

test("refuses a check run for a different head sha", () => {
  refused(flip((i) => (i.checkRuns.check_runs[1].head_sha = "2".repeat(40))), "different commit");
});

test("refuses a check run that has not completed", () => {
  refused(
    flip((i) => {
      i.checkRuns.check_runs[1].status = "in_progress";
      i.checkRuns.check_runs[1].conclusion = null;
    }),
    "not completed",
  );
});

test("refuses a failed, cancelled, timed-out or action-required conclusion", () => {
  for (const c of ["failure", "cancelled", "timed_out", "action_required", "stale", null]) {
    refused(flip((i) => (i.checkRuns.check_runs[1].conclusion = c)), "concluded");
  }
});

test("accepts skipped and neutral conclusions next to a green test run", () => {
  const r = flip((i) => {
    i.checkRuns.check_runs[1].conclusion = "skipped";
    i.checkRuns.check_runs.push({ name: "docs", head_sha: SHA, status: "completed", conclusion: "neutral" });
    i.checkRuns.total_count = 3;
  });
  assert.equal(r.merge, true);
});

test("refuses when no run is named test", () => {
  refused(flip((i) => (i.checkRuns.check_runs[0].name = "build")), "named test");
});

test("refuses when the run named test is only skipped or neutral", () => {
  for (const c of ["skipped", "neutral"]) {
    refused(flip((i) => (i.checkRuns.check_runs[0].conclusion = c)), "named test");
  }
});

// ── 6. Legacy statuses ──────────────────────────────────────────────────────

test("refuses a pending commit status", () => {
  refused(flip((i) => (i.status.statuses = [{ context: "ci/legacy", state: "pending" }])), "ci/legacy is pending");
});

test("refuses a failed commit status", () => {
  refused(flip((i) => (i.status.statuses = [{ context: "ci/legacy", state: "failure" }])), "ci/legacy is failure");
});

test("accepts all-success commit statuses", () => {
  const r = flip((i) => {
    i.status = { state: "success", statuses: [{ context: "ci/legacy", state: "success" }] };
  });
  assert.equal(r.merge, true);
});

// ── 7. Issue map ────────────────────────────────────────────────────────────

test("refuses a malformed issue map", () => {
  refused(flip((i) => (i.issueMapText = GOOD_MAP + "1 497\n")), "issue map line 4");
});

test("refuses an issue map with a duplicate key for the same repo", () => {
  refused(
    flip((i) => (i.issueMapText = GOOD_MAP + "claude-code/2026-09-30/1 Adam-S-Daniel/cms-platform#498\n")),
    "key and repo already on line 3",
  );
});

// ── Everything at once, and input shape ─────────────────────────────────────

test("collects every reason instead of stopping at the first", () => {
  const r = flip((i) => {
    i.pr.state = "closed";
    i.pr.draft = true;
    i.pr.title = "Something else DRY_RUN";
    i.pr.body = "# Declined\n";
    i.files.push({ filename: "scripts/evil.sh", status: "added" });
    i.checkRuns.check_runs[0].conclusion = "failure";
    i.status.statuses = [{ context: "x", state: "error" }];
    i.issueMapText = "garbage\n";
  });
  assert.equal(r.merge, false);
  for (const fragment of [
    "not open",
    "draft",
    "title does not start",
    "DRY_RUN",
    "Declined",
    "truncated",
    "scripts/evil.sh",
    "concluded failure",
    "named test",
    "commit status x",
    "issue map line 1",
  ]) {
    assert.ok(
      r.reasons.some((x) => x.includes(fragment)),
      `missing reason for ${fragment}: ${JSON.stringify(r.reasons)}`,
    );
  }
  assert.equal(r.head_sha, SHA);
});

test("decide throws on a structurally wrong input rather than guessing", () => {
  assert.throws(() => decide({ ...baseInput(), files: {} }), /bad input/);
  assert.throws(() => decide({ ...baseInput(), pr: { ...basePr(), head: {} } }), /bad input/);
  assert.throws(() => decide({ ...baseInput(), checkRuns: {} }), /bad input/);
  assert.throws(() => decide({ ...baseInput(), status: {} }), /bad input/);
});

// ── parseIssueMap ───────────────────────────────────────────────────────────

test("parseIssueMap accepts comments, blank lines and valid lines", () => {
  const text = [
    "# comment",
    "",
    "claude-code/2026-09-30/1 Adam-S-Daniel/cms-platform#497",
    "codex/2026-10-01/12 jodidaniel/jodidaniel.com#3",
    "claude-code/2026-09-30/1 Adam-S-Daniel/_agent-guidance#9",
    "",
  ].join("\r\n");
  const { entries, errors } = parseIssueMap(text);
  assert.deepEqual(errors, []);
  assert.equal(entries.length, 3);
  assert.deepEqual(entries[1], {
    key: "codex/2026-10-01/12",
    repo: "jodidaniel/jodidaniel.com",
    number: 3,
    line: 4,
  });
});

test("parseIssueMap rejects the old '<group> <issue>' format", () => {
  const { errors } = parseIssueMap("1 497\n");
  assert.equal(errors.length, 1);
  assert.match(errors[0], /line 1/);
});

test("parseIssueMap rejects each way a line can be malformed", () => {
  const bad = [
    "gemini/2026-09-30/1 Adam-S-Daniel/cms-platform#497",
    "claude-code/2026-9-30/1 Adam-S-Daniel/cms-platform#497",
    "claude-code/2026-09-30/0 Adam-S-Daniel/cms-platform#497",
    "claude-code/2026-09-30/1 cms-platform#497",
    "claude-code/2026-09-30/1 Adam-S-Daniel/cms-platform#0",
    "claude-code/2026-09-30/1 Adam-S-Daniel/cms-platform#007",
    "claude-code/2026-09-30/1 Adam-S-Daniel/cms-platform#497 ",
    " claude-code/2026-09-30/1 Adam-S-Daniel/cms-platform#497",
    "claude-code/2026-09-30/1  Adam-S-Daniel/cms-platform#497",
    "claude-code/2026-09-30/1 Adam-S-Daniel/cms-platform",
  ];
  for (const line of bad) {
    assert.equal(parseIssueMap(line + "\n").errors.length, 1, `should reject ${JSON.stringify(line)}`);
  }
});

test("parseIssueMap flags a repeated key and repo, but allows one key across repos", () => {
  const dup = parseIssueMap(
    "claude-code/2026-09-30/1 A/r#1\nclaude-code/2026-09-30/1 a/R#2\n",
  );
  assert.equal(dup.errors.length, 1);
  assert.match(dup.errors[0], /line 2/);
  const ok = parseIssueMap("claude-code/2026-09-30/1 A/r#1\nclaude-code/2026-09-30/1 A/s#1\n");
  assert.deepEqual(ok.errors, []);
});

test("parseIssueMap flags the same owner/repo#n on two lines", () => {
  const { errors } = parseIssueMap("claude-code/2026-09-30/1 A/r#1\ncodex/2026-09-30/2 A/r#1\n");
  assert.equal(errors.length, 1);
  assert.match(errors[0], /issue already on line 1/);
});

test("parseIssueMap errors never echo the offending line's text", () => {
  const { errors } = parseIssueMap("SECRET-TOKEN-VALUE\n");
  assert.ok(!errors.join(" ").includes("SECRET-TOKEN-VALUE"));
});

test("the committed docs/reference/agent-changelog-issue-map.txt parses with zero errors", () => {
  const { entries, errors } = parseIssueMap(fs.readFileSync(REAL_MAP, "utf8"));
  assert.deepEqual(errors, []);
  assert.ok(entries.length >= 1);
  assert.ok(entries.some((e) => e.key === "claude-code/2026-09-30/1" && e.repo === "Adam-S-Daniel/cms-platform" && e.number === 497));
});

// ── CLI: exit codes ─────────────────────────────────────────────────────────

function withFixtures(mutate, issueMapText, fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "routine-merge-gate-"));
  try {
    const input = baseInput();
    if (mutate) mutate(input);
    const paths = {
      pr: path.join(dir, "pr.json"),
      files: path.join(dir, "files.json"),
      runs: path.join(dir, "check-runs.json"),
      status: path.join(dir, "status.json"),
      map: path.join(dir, "map.txt"),
    };
    fs.writeFileSync(paths.pr, JSON.stringify(input.pr));
    fs.writeFileSync(paths.files, JSON.stringify(input.files));
    fs.writeFileSync(paths.runs, JSON.stringify(input.checkRuns));
    fs.writeFileSync(paths.status, JSON.stringify(input.status));
    fs.writeFileSync(paths.map, issueMapText === undefined ? GOOD_MAP : issueMapText);
    return fn(paths, dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function runCli(args) {
  return spawnSync(process.execPath, [SCRIPT, ...args], { encoding: "utf8" });
}

function cliArgs(p, withMap) {
  const a = ["--pr", p.pr, "--files", p.files, "--check-runs", p.runs, "--status", p.status];
  if (withMap) a.push("--issue-map", p.map);
  return a;
}

test("CLI exits 0 and prints one JSON object when the gate is green", () => {
  withFixtures(null, undefined, (p) => {
    const r = runCli(cliArgs(p, true));
    assert.equal(r.status, 0, r.stderr);
    const out = JSON.parse(r.stdout);
    assert.deepEqual(out, { merge: true, head_sha: SHA, reasons: [] });
  });
});

test("CLI exits 2 with reasons when the gate refuses", () => {
  withFixtures((i) => (i.pr.draft = true), undefined, (p) => {
    const r = runCli(cliArgs(p, true));
    assert.equal(r.status, 2);
    const out = JSON.parse(r.stdout);
    assert.equal(out.merge, false);
    assert.ok(out.reasons.length > 0);
  });
});

test("CLI exits 2 when --issue-map points at a bad file", () => {
  withFixtures(null, GOOD_MAP + "bad line\n", (p) => {
    const r = runCli(cliArgs(p, true));
    assert.equal(r.status, 2);
    assert.match(JSON.parse(r.stdout).reasons.join(" "), /issue map line 4/);
  });
});

test("CLI ignores the issue map when --issue-map is absent", () => {
  withFixtures(null, "bad line\n", (p) => {
    assert.equal(runCli(cliArgs(p, false)).status, 0);
  });
});

test("CLI exits 1 on a missing required flag", () => {
  withFixtures(null, undefined, (p) => {
    const r = runCli(["--pr", p.pr, "--files", p.files, "--status", p.status]);
    assert.equal(r.status, 1);
    assert.match(r.stderr, /missing --check-runs/);
    assert.equal(r.stdout, "");
  });
});

test("CLI exits 1 on an unknown flag", () => {
  const r = runCli(["--merge-anyway", "yes"]);
  assert.equal(r.status, 1);
  assert.match(r.stderr, /unknown argument/);
});

test("CLI exits 1 on an unreadable file and on invalid JSON without echoing contents", () => {
  withFixtures(null, undefined, (p, dir) => {
    const missing = runCli(cliArgs({ ...p, pr: path.join(dir, "nope.json") }, false));
    assert.equal(missing.status, 1);
    assert.match(missing.stderr, /cannot read --pr file/);
    fs.writeFileSync(p.files, "SECRET-TOKEN-VALUE not json");
    const bad = runCli(cliArgs(p, false));
    assert.equal(bad.status, 1);
    assert.match(bad.stderr, /not valid JSON/);
    assert.ok(!bad.stderr.includes("SECRET-TOKEN-VALUE"));
  });
});

test("CLI exits 1 when the JSON has the wrong shape", () => {
  withFixtures((i) => (i.files = { not: "an array" }), undefined, (p) => {
    const r = runCli(cliArgs(p, false));
    assert.equal(r.status, 1);
    assert.match(r.stderr, /bad input/);
  });
});

test("CLI refuses (exit 2) against a COPY of the real map with a bad line appended", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "routine-merge-gate-"));
  try {
    const copy = path.join(dir, "map.txt");
    fs.writeFileSync(copy, fs.readFileSync(REAL_MAP, "utf8") + "1 497\n");
    withFixtures(null, undefined, (p) => {
      const r = runCli([...cliArgs(p, false), "--issue-map", copy]);
      assert.equal(r.status, 2);
    });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
