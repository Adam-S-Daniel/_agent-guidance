"use strict";
// test-dependabot-config-health.js — node:test coverage for
// scripts/dependabot-config-health.js. Every fixture is fabricated (fake
// `api` objects, a pinned `nowMs`) — no network, no Date.now(), no sleeps, no
// child processes. Run via test/run-tests.sh's test_dependabot_config_health,
// which requires `# fail 0` and `# pass >= 30`.
//
// Fixture timestamps and shas mirror the measured facts in this repo's
// docs/decisions/0014-dependabot-config-health-is-swept-centrally.md and the
// script's own header: cms-platform 9e44154/8ab5799/149755b/acc5df3,
// claude-memory-map e66b678, and the four repos fact 3 measured with zero
// dependabot-updates runs (_agent-guidance, claude-memory-map,
// fastmail-actions, repo-settings).

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const YAML = require("yaml");

const {
  ApiError,
  UPDATES_WORKFLOW_PATH,
  ownerTokenEnvName,
  parseSchedule,
  silenceThresholdDays,
  candidateShas,
  pickDependabotCheck,
  checkState,
  jobsState,
  extractReportedEvidence,
  hiddenEvidenceBlock,
  planIssueAction,
  renderIssueBody,
  logLine,
  assessRepo,
  runSweep,
} = require("../scripts/dependabot-config-health.js");

// ── Shared fixture helpers ─────────────────────────────────────────────────

const WEEKLY_YAML = "updates:\n  - package-ecosystem: npm\n    schedule:\n      interval: weekly\n";
const b64 = (s) => Buffer.from(s, "utf8").toString("base64");
const yamlContent = (text) => ({ content: b64(text), encoding: "base64" });

// A fake `api` with sane, overridable defaults for every method the real
// gh-backed implementation provides. Every default answers "nothing here" so
// a test only has to stub the calls its scenario actually exercises.
function fakeApi(overrides = {}) {
  return {
    listRepos: overrides.listRepos || (() => { throw new Error("listRepos not stubbed"); }),
    getContent: overrides.getContent || (() => { throw new ApiError("Not Found", 404); }),
    newestPathCommit: overrides.newestPathCommit || (() => null),
    commitPulls: overrides.commitPulls || (() => []),
    checkRuns: overrides.checkRuns || (() => ({ total_count: 0, check_runs: [] })),
    workflowsPage: overrides.workflowsPage || (() => ({ total_count: 0, workflows: [] })),
    newestWorkflowRun: overrides.newestWorkflowRun || (() => null),
    openIssuesPage: overrides.openIssuesPage || (() => []),
    issueCommentsPage: overrides.issueCommentsPage || (() => []),
    ensureLabel: overrides.ensureLabel || (() => {}),
    createIssue: overrides.createIssue || (() => ({ number: 1, html_url: "https://example.invalid/issues/1" })),
    commentIssue: overrides.commentIssue || (() => ({})),
    shutIssue: overrides.shutIssue || (() => ({})),
  };
}

// ── 1. ownerTokenEnvName ────────────────────────────────────────────────────

test("ownerTokenEnvName: Adam-S-Daniel -> GH_TOKEN_ADAM_S_DANIEL", () => {
  assert.equal(ownerTokenEnvName("Adam-S-Daniel"), "GH_TOKEN_ADAM_S_DANIEL");
});

test("ownerTokenEnvName: jodidaniel -> GH_TOKEN_JODIDANIEL", () => {
  assert.equal(ownerTokenEnvName("jodidaniel"), "GH_TOKEN_JODIDANIEL");
});

// ── 2. parseSchedule / silenceThresholdDays ────────────────────────────────

test("parseSchedule: weekly and monthly are recognised, shortestDays is 7", () => {
  const r = parseSchedule(
    "updates:\n" +
      "  - package-ecosystem: npm\n    schedule:\n      interval: weekly\n" +
      "  - package-ecosystem: github-actions\n    schedule:\n      interval: monthly\n",
  );
  assert.equal(r.shortestDays, 7);
  assert.deepEqual(r.intervals, ["weekly", "monthly"]);
  assert.deepEqual(r.unrecognized, []);
  assert.equal(r.error, null);
});

test("parseSchedule: an unrecognised interval (cron) is collected, shortestDays null", () => {
  const r = parseSchedule("updates:\n  - package-ecosystem: npm\n    schedule:\n      interval: cron\n");
  assert.equal(r.shortestDays, null);
  assert.deepEqual(r.unrecognized, ["cron"]);
});

test("parseSchedule: invalid YAML produces an error string, shortestDays null", () => {
  const r = parseSchedule("updates:\n  - [unclosed\n");
  assert.equal(r.shortestDays, null);
  assert.ok(typeof r.error === "string" && r.error.length > 0);
});

test("parseSchedule: a config with no updates: array yields shortestDays null", () => {
  const r = parseSchedule("version: 2\n");
  assert.equal(r.shortestDays, null);
  assert.equal(r.error, null);
});

test("silenceThresholdDays(7) === 10", () => {
  assert.equal(silenceThresholdDays(7), 10);
});

test("silenceThresholdDays(null) === null", () => {
  assert.equal(silenceThresholdDays(null), null);
});

// ── 3. candidateShas ────────────────────────────────────────────────────────

test("candidateShas: cms-platform shape returns both the path commit and the PR's merge commit", () => {
  const shas = candidateShas(
    "9e44154",
    [{ merged_at: "2026-08-20T00:00:00Z", merge_commit_sha: "8ab5799", base: { ref: "main" } }],
    "main",
  );
  assert.deepEqual(shas, ["9e44154", "8ab5799"]);
});

test("candidateShas: an unmerged PR and a PR into another base are ignored", () => {
  const shas = candidateShas(
    "abc123",
    [
      { merged_at: null, merge_commit_sha: "unmerged", base: { ref: "main" } },
      { merged_at: "2026-01-01T00:00:00Z", merge_commit_sha: "wrong-base", base: { ref: "release" } },
    ],
    "main",
  );
  assert.deepEqual(shas, ["abc123"]);
});

test("candidateShas: claude-memory-map shape (path commit == merge commit) de-duplicates", () => {
  const shas = candidateShas(
    "e66b678",
    [{ merged_at: "2026-08-10T19:40:00Z", merge_commit_sha: "e66b678", base: { ref: "main" } }],
    "main",
  );
  assert.deepEqual(shas, ["e66b678"]);
});

// ── 4. pickDependabotCheck ──────────────────────────────────────────────────

test("pickDependabotCheck: ignores other apps and other check names", () => {
  const runs = [
    { app: { slug: "github-actions" }, name: ".github/dependabot.yml", completed_at: "2026-01-01T00:00:00Z", id: 100 },
    { app: { slug: "dependabot" }, name: "some-other-check", completed_at: "2026-01-01T00:00:00Z", id: 101 },
    { app: { slug: "dependabot" }, name: ".github/dependabot.yml", completed_at: "2026-01-02T00:00:00Z", id: 1 },
  ];
  const picked = pickDependabotCheck(runs, ".github/dependabot.yml");
  assert.equal(picked.id, 1);
});

test("pickDependabotCheck: picks the newest by completed_at", () => {
  const runs = [
    { app: { slug: "dependabot" }, name: ".github/dependabot.yml", completed_at: "2026-01-01T00:00:00Z", id: 1 },
    { app: { slug: "dependabot" }, name: ".github/dependabot.yml", completed_at: "2026-01-05T00:00:00Z", id: 2 },
    { app: { slug: "dependabot" }, name: ".github/dependabot.yml", completed_at: "2026-01-03T00:00:00Z", id: 3 },
  ];
  assert.equal(pickDependabotCheck(runs, ".github/dependabot.yml").id, 2);
});

// ── 5. checkState ────────────────────────────────────────────────────────────

test("checkState: completed + success -> success", () => {
  assert.equal(checkState({ status: "completed", conclusion: "success" }), "success");
});

test("checkState: completed + failure -> failure", () => {
  assert.equal(checkState({ status: "completed", conclusion: "failure" }), "failure");
});

test("checkState: in_progress -> unknown", () => {
  assert.equal(checkState({ status: "in_progress", conclusion: null }), "unknown");
});

test("checkState: completed + neutral -> unknown", () => {
  assert.equal(checkState({ status: "completed", conclusion: "neutral" }), "unknown");
});

test("checkState: null -> missing", () => {
  assert.equal(checkState(null), "missing");
});

// ── 6. jobsState ─────────────────────────────────────────────────────────────

const NOW = Date.parse("2026-09-15T00:00:00Z");
const daysAgo = (n) => new Date(NOW - n * 86400000).toISOString();

test("jobsState: a job newer than the config commit, within threshold, is recent", () => {
  assert.equal(
    jobsState({ lastJobAt: daysAgo(1), configCommitAt: daysAgo(10), thresholdDays: 10, nowMs: NOW }),
    "recent",
  );
});

test("jobsState: 11 days since the evidence with a 10-day threshold is silent", () => {
  assert.equal(
    jobsState({ lastJobAt: daysAgo(11), configCommitAt: daysAgo(11), thresholdDays: 10, nowMs: NOW }),
    "silent",
  );
});

test("jobsState: exactly 10.0 days is NOT silent", () => {
  const state = jobsState({ lastJobAt: daysAgo(10), configCommitAt: daysAgo(10), thresholdDays: 10, nowMs: NOW });
  assert.notEqual(state, "silent");
});

test("jobsState: a config commit newer than the last job is pending", () => {
  assert.equal(
    jobsState({ lastJobAt: daysAgo(20), configCommitAt: daysAgo(2), thresholdDays: 10, nowMs: NOW }),
    "pending",
  );
});

test("jobsState: never run, config 30 days old, is silent", () => {
  assert.equal(
    jobsState({ lastJobAt: null, configCommitAt: daysAgo(30), thresholdDays: 10, nowMs: NOW }),
    "silent",
  );
});

test("jobsState: a null thresholdDays is unknown regardless of timestamps", () => {
  assert.equal(
    jobsState({ lastJobAt: daysAgo(1), configCommitAt: daysAgo(1), thresholdDays: null, nowMs: NOW }),
    "unknown",
  );
});

test("jobsState: an unparseable timestamp throws", () => {
  assert.throws(() => jobsState({ lastJobAt: "not-a-date", configCommitAt: daysAgo(1), thresholdDays: 10, nowMs: NOW }));
});

// ── 7. assessRepo ────────────────────────────────────────────────────────────

test("assessRepo(a): claude-memory-map shape -> finding with BOTH invalid-config and silent:never", () => {
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "e66b678", date: "2026-08-10T19:36:21Z" }),
    commitPulls: () => [{ merged_at: "2026-08-10T19:40:00Z", merge_commit_sha: "e66b678", base: { ref: "main" } }],
    checkRuns: () => ({
      total_count: 1,
      check_runs: [{
        id: 93574227209, app: { slug: "dependabot" }, name: ".github/dependabot.yml",
        status: "completed", conclusion: "failure", head_sha: "e66b678",
        html_url: "https://github.com/Adam-S-Daniel/claude-memory-map/runs/93574227209",
        output: { title: "Dependabot could not update dependencies", summary: "the config could not be validated" },
        completed_at: "2026-08-10T19:45:00Z",
      }],
    }),
    workflowsPage: () => ({ total_count: 0, workflows: [] }), // fact 3: zero runs, no workflow at all
  });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/claude-memory-map", private: false, defaultBranch: "main" },
    api, Date.parse("2026-09-15T00:00:00Z"), { label: "ci" },
  );
  assert.equal(a.verdict, "finding");
  assert.deepEqual(a.findings.map((f) => f.kind).sort(), ["invalid-config", "silent"]);
  assert.ok(a.findings.some((f) => f.key === "check:93574227209"));
  assert.ok(a.findings.some((f) => f.key === "silent:never"));
});

test("assessRepo(b): cms-platform today -> healthy, check found ONLY on the merge commit", () => {
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "9e44154", date: "2026-09-10T00:00:00Z" }),
    commitPulls: () => [{ merged_at: "2026-09-10T00:05:00Z", merge_commit_sha: "8ab5799", base: { ref: "main" } }],
    checkRuns: (o, r, sha) => (sha === "9e44154"
      ? { total_count: 0, check_runs: [] }
      : {
          total_count: 1,
          check_runs: [{
            id: 555, app: { slug: "dependabot" }, name: ".github/dependabot.yml",
            status: "completed", conclusion: "success", head_sha: "8ab5799",
            completed_at: "2026-09-10T00:10:00Z",
          }],
        }),
    workflowsPage: () => ({ total_count: 1, workflows: [{ id: 42, path: UPDATES_WORKFLOW_PATH }] }),
    newestWorkflowRun: () => ({ created_at: "2026-09-14T00:00:00Z", html_url: "https://x/runs/1" }),
  });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/cms-platform", private: false, defaultBranch: "main" },
    api, Date.parse("2026-09-15T00:00:00Z"), { label: "ci" },
  );
  assert.equal(a.verdict, "healthy");
  assert.equal(a.check.sha, "8ab5799");
});

test("assessRepo(c): cms-platform on 2026-08-15, check failure on 149755b -> finding; recent jobs do not mask it", () => {
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "149755b", date: "2026-08-10T12:00:00Z" }),
    checkRuns: () => ({
      total_count: 1,
      check_runs: [{
        id: 777, app: { slug: "dependabot" }, name: ".github/dependabot.yml",
        status: "completed", conclusion: "failure", head_sha: "149755b",
        output: { title: "Dependabot could not update dependencies", summary: "The property '#/updates/0/cooldown/semver-major-days' is not supported for the package ecosystem 'github-actions'." },
        completed_at: "2026-08-10T12:30:00Z",
      }],
    }),
    workflowsPage: () => ({ total_count: 1, workflows: [{ id: 42, path: UPDATES_WORKFLOW_PATH }] }),
    newestWorkflowRun: () => ({ created_at: "2026-08-11T12:00:00Z", html_url: "https://x/runs/2" }),
  });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/cms-platform", private: false, defaultBranch: "main" },
    api, Date.parse("2026-08-15T00:00:00Z"), { label: "ci" },
  );
  assert.equal(a.verdict, "finding");
  assert.deepEqual(a.findings.map((f) => f.kind), ["invalid-config"]);
  assert.equal(a.jobs.state, "recent");
});

test("assessRepo(d): check missing but jobs recent -> unknown, not healthy", () => {
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "a", date: "2026-08-01T00:00:00Z" }),
    checkRuns: () => ({ total_count: 0, check_runs: [] }),
    workflowsPage: () => ({ total_count: 1, workflows: [{ id: 1, path: UPDATES_WORKFLOW_PATH }] }),
    newestWorkflowRun: () => ({ created_at: "2026-09-10T00:00:00Z", html_url: "https://x" }),
  });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x", private: false, defaultBranch: "main" },
    api, Date.parse("2026-09-15T00:00:00Z"), { label: "ci" },
  );
  assert.equal(a.verdict, "unknown");
  assert.notEqual(a.verdict, "healthy");
});

test("assessRepo(e): check missing and no job ever -> finding silent AND unknowns non-empty", () => {
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "a", date: "2026-08-01T00:00:00Z" }),
    checkRuns: () => ({ total_count: 0, check_runs: [] }),
    workflowsPage: () => ({ total_count: 0, workflows: [] }),
  });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x", private: false, defaultBranch: "main" },
    api, Date.parse("2026-09-15T00:00:00Z"), { label: "ci" },
  );
  assert.equal(a.verdict, "finding");
  assert.ok(a.findings.some((f) => f.kind === "silent"));
  assert.ok(a.unknowns.length > 0);
});

test("assessRepo(f1): 404 on both config paths -> no-config", () => {
  const api = fakeApi({ getContent: () => { throw new ApiError("Not Found", 404); } });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x", private: false, defaultBranch: "main" },
    api, NOW, { label: "ci" },
  );
  assert.equal(a.verdict, "no-config");
  assert.deepEqual(a.findings, []);
});

test("assessRepo(f2): a 500 reading the config is unknown, and the .yaml path is never tried", () => {
  let yamlTried = false;
  const api = fakeApi({
    getContent: (o, r, p) => {
      if (p === ".github/dependabot.yaml") yamlTried = true;
      throw new ApiError("Internal Server Error", 500);
    },
  });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x", private: false, defaultBranch: "main" },
    api, NOW, { label: "ci" },
  );
  assert.equal(a.verdict, "unknown");
  assert.equal(yamlTried, false);
  assert.ok(a.unknowns.some((u) => u.includes("500")));
});

test("assessRepo(f3): the .yaml variant is found when .yml 404s", () => {
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yaml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
  });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x", private: false, defaultBranch: "main" },
    api, NOW, { label: "ci" },
  );
  assert.equal(a.config.path, ".github/dependabot.yaml");
});

test("assessRepo(g): workflows listing truncated (total 150, collected 100) -> jobs unknown", () => {
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "a", date: "2026-08-01T00:00:00Z" }),
    checkRuns: () => ({
      total_count: 1,
      check_runs: [{ id: 1, app: { slug: "dependabot" }, name: ".github/dependabot.yml", status: "completed", conclusion: "success", head_sha: "a", completed_at: "2026-08-01T00:05:00Z" }],
    }),
    workflowsPage: (o, r, page) => (page === 1
      ? { total_count: 150, workflows: Array.from({ length: 100 }, (_, i) => ({ id: i, path: `other-${i}.yml` })) }
      : { total_count: 150, workflows: [] }),
  });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x", private: false, defaultBranch: "main" },
    api, Date.parse("2026-09-15T00:00:00Z"), { label: "ci" },
  );
  assert.equal(a.jobs.state, "unknown");
  assert.equal(a.verdict, "unknown");
  assert.ok(a.jobs.reason.includes("truncated"));
});

test("assessRepo(h): check-runs 403 -> unknown, the status in the reason, no body text", () => {
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "a", date: "2026-08-01T00:00:00Z" }),
    checkRuns: () => { throw new ApiError("Bad credentials", 403); },
    workflowsPage: () => ({ total_count: 1, workflows: [{ id: 1, path: UPDATES_WORKFLOW_PATH }] }),
    newestWorkflowRun: () => ({ created_at: "2026-09-10T00:00:00Z", html_url: "https://x" }),
  });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x", private: false, defaultBranch: "main" },
    api, Date.parse("2026-09-15T00:00:00Z"), { label: "ci" },
  );
  assert.equal(a.check.state, "unknown");
  assert.equal(a.verdict, "unknown");
  assert.equal(a.check.reason, "reading the config check runs failed (HTTP 403): Bad credentials");
});

test("assessRepo(i): fresh config (1 hour old), check success, no job since -> pending", () => {
  const oneHourAgo = new Date(NOW - 3600000).toISOString();
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "a", date: oneHourAgo }),
    checkRuns: () => ({
      total_count: 1,
      check_runs: [{ id: 1, app: { slug: "dependabot" }, name: ".github/dependabot.yml", status: "completed", conclusion: "success", head_sha: "a", completed_at: oneHourAgo }],
    }),
    workflowsPage: () => ({ total_count: 1, workflows: [{ id: 1, path: UPDATES_WORKFLOW_PATH }] }),
    newestWorkflowRun: () => ({ created_at: daysAgo(5), html_url: "https://x" }),
  });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x", private: false, defaultBranch: "main" },
    api, NOW, { label: "ci" },
  );
  assert.equal(a.verdict, "pending");
});

test("assessRepo(j): open-issue lookup 500 -> unknown, even though check+jobs are otherwise healthy", () => {
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "a", date: daysAgo(10) }),
    checkRuns: () => ({
      total_count: 1,
      check_runs: [{ id: 1, app: { slug: "dependabot" }, name: ".github/dependabot.yml", status: "completed", conclusion: "success", head_sha: "a", completed_at: daysAgo(10) }],
    }),
    workflowsPage: () => ({ total_count: 1, workflows: [{ id: 1, path: UPDATES_WORKFLOW_PATH }] }),
    newestWorkflowRun: () => ({ created_at: daysAgo(1), html_url: "https://x" }),
    openIssuesPage: () => { throw new ApiError("Internal Server Error", 500); },
  });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x", private: false, defaultBranch: "main" },
    api, NOW, { label: "ci" },
  );
  assert.equal(a.verdict, "unknown");
  assert.ok(a.unknowns.some((u) => u.includes("500")));
});

// ── 8. planIssueAction matrix ───────────────────────────────────────────────

test("planIssueAction: finding + no issue -> create", () => {
  const plan = planIssueAction({ verdict: "finding", findings: [{ kind: "invalid-config", key: "check:1" }], openIssue: null, reportedKeys: new Set() });
  assert.deepEqual(plan, { action: "create" });
});

test("planIssueAction: finding + issue + some keys unreported -> comment with only the fresh keys", () => {
  const plan = planIssueAction({
    verdict: "finding",
    findings: [{ kind: "invalid-config", key: "check:1" }, { kind: "silent", key: "silent:never" }],
    openIssue: { number: 5 },
    reportedKeys: new Set(["check:1"]),
  });
  assert.deepEqual(plan, { action: "comment", keys: ["silent:never"] });
});

test("planIssueAction: finding + issue + all keys already reported -> none", () => {
  const plan = planIssueAction({
    verdict: "finding", findings: [{ kind: "invalid-config", key: "check:1" }],
    openIssue: { number: 5 }, reportedKeys: new Set(["check:1"]),
  });
  assert.deepEqual(plan, { action: "none" });
});

test("planIssueAction: healthy + issue -> shut", () => {
  assert.deepEqual(
    planIssueAction({ verdict: "healthy", findings: [], openIssue: { number: 5 }, reportedKeys: new Set() }),
    { action: "shut" },
  );
});

test("planIssueAction: no-config + issue -> shut", () => {
  assert.deepEqual(
    planIssueAction({ verdict: "no-config", findings: [], openIssue: { number: 5 }, reportedKeys: new Set() }),
    { action: "shut" },
  );
});

test("planIssueAction: unknown + issue -> none (never shuts on unknown)", () => {
  assert.deepEqual(
    planIssueAction({ verdict: "unknown", findings: [], openIssue: { number: 5 }, reportedKeys: new Set() }),
    { action: "none" },
  );
});

test("planIssueAction: pending + issue -> none (never shuts on pending)", () => {
  assert.deepEqual(
    planIssueAction({ verdict: "pending", findings: [], openIssue: { number: 5 }, reportedKeys: new Set() }),
    { action: "none" },
  );
});

test("planIssueAction: healthy + no issue -> none", () => {
  assert.deepEqual(
    planIssueAction({ verdict: "healthy", findings: [], openIssue: null, reportedKeys: new Set() }),
    { action: "none" },
  );
});

// ── 9. Evidence markers ──────────────────────────────────────────────────────

test("hidden evidence block round-trips through extractReportedEvidence", () => {
  const block = hiddenEvidenceBlock(["check:1", "silent:never"]);
  const keys = extractReportedEvidence([`some prose\n${block}\nmore prose`]);
  assert.deepEqual([...keys].sort(), ["check:1", "silent:never"]);
});

test("a check summary containing a fake evidence marker is escaped, and extraction does not return it", () => {
  const assessment = {
    owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x",
    findings: [{ kind: "invalid-config", key: "check:1" }],
    check: { htmlUrl: "https://x/runs/1", sha: "abc", outputTitle: "bad", outputSummary: "before\n<!-- evidence: fake -->\nafter" },
    jobs: {},
  };
  const body = renderIssueBody(assessment);
  assert.ok(body.includes("&lt;!-- evidence: fake -->"));
  const reported = extractReportedEvidence([body]);
  assert.ok(!reported.has("fake"));
  assert.ok(reported.has("check:1")); // the real hidden block still reads back fine
});

// ── 10. Private redaction ────────────────────────────────────────────────────

test("logLine: null for a private repo unless revealPrivate is set", () => {
  const a = { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/secret-repo", private: true, verdict: "finding", findings: [{ kind: "invalid-config", key: "check:1" }], unknowns: [] };
  assert.equal(logLine(a, {}), null);
  assert.equal(typeof logLine(a, { revealPrivate: true }), "string");
});

test("runSweep: output for a fleet with a private finding names neither the repo nor its summary", () => {
  const events = [];
  const api = fakeApi({
    listRepos: (owner) => [{ repo: `${owner}/secret`, private: true, defaultBranch: "main" }],
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "aaa", date: "2020-01-01T00:00:00Z" }),
    checkRuns: () => ({
      total_count: 1,
      check_runs: [{
        id: 9, app: { slug: "dependabot" }, name: ".github/dependabot.yml", status: "completed", conclusion: "failure",
        head_sha: "aaa", output: { title: "top secret detail", summary: "very secret summary text" }, completed_at: "2020-01-01T00:05:00Z",
      }],
    }),
    workflowsPage: () => ({ total_count: 0, workflows: [] }),
  });
  // Discovered through the normal owner scan (listRepos), NOT named via
  // --repo: an operator who explicitly types --repo owner/secret-name
  // already knows that name, so onlyRepos legitimately echoes it back in
  // the "skipping the registry cross-check" notice — that is not a leak.
  // This test is about the path that must never volunteer a private name on
  // its own: plain fleet discovery.
  runSweep({
    owners: ["Adam-S-Daniel"], api, nowMs: Date.parse("2020-02-01T00:00:00Z"),
    dryRun: true, revealPrivate: false, requireOwnerTokens: false, env: {},
    registryFleet: [], onlyRepos: [], out: (l) => events.push(l),
  });
  const text = events.join("\n");
  assert.ok(!text.includes("secret"), `leaked: ${text}`);
});

// ── 11. runSweep end-to-end over the measured 19-repo fleet ────────────────

function buildFleetApi() {
  const BROKEN = ["_agent-guidance", "claude-memory-map", "fastmail-actions", "repo-settings"]; // fact 3
  const HEALTHY = ["cms-platform", "adamdaniel.ai", "jodidaniel.com", "GHA-bench", "skills-evals"];
  const NO_CONFIG = [
    "agentskills", "agentskills-private", "rss-inator", "wsl-automation",
    "scratch-claude-001", "scratch-claude-002", "scratch-jules-001", "squarespacetemp", "jc", "4A",
  ];
  const ownerOf = (name) => (name === "jodidaniel.com" ? "jodidaniel" : "Adam-S-Daniel");
  const calls = { createIssue: [], commentIssue: [], shutIssue: [] };

  const api = {
    listRepos(owner) {
      return [...BROKEN, ...HEALTHY, ...NO_CONFIG]
        .filter((n) => ownerOf(n) === owner)
        .map((n) => ({ repo: `${owner}/${n}`, private: false, defaultBranch: "main" }));
    },
    getContent(owner, repo, p) {
      const name = repo.split("/")[1];
      if (NO_CONFIG.includes(name) || p !== ".github/dependabot.yml") throw new ApiError("Not Found", 404);
      return yamlContent(WEEKLY_YAML);
    },
    newestPathCommit: () => ({ sha: "cccccc", date: "2026-08-01T00:00:00Z" }),
    commitPulls: () => [],
    checkRuns(owner, repo, sha) {
      const name = repo.split("/")[1];
      const conclusion = BROKEN.includes(name) ? "failure" : "success";
      return { total_count: 1, check_runs: [{ id: 1, app: { slug: "dependabot" }, name: ".github/dependabot.yml", status: "completed", conclusion, head_sha: sha, output: { title: "t", summary: "s" }, completed_at: "2026-08-01T00:05:00Z" }] };
    },
    workflowsPage(owner, repo, page) {
      const name = repo.split("/")[1];
      if (page > 1 || BROKEN.includes(name)) return { total_count: 0, workflows: [] };
      return { total_count: 1, workflows: [{ id: 42, path: UPDATES_WORKFLOW_PATH }] };
    },
    newestWorkflowRun: () => ({ created_at: "2026-09-10T00:00:00Z", html_url: "https://x/runs/1" }),
    openIssuesPage: () => [],
    issueCommentsPage: () => [],
    ensureLabel: () => {},
    createIssue: (owner, repo, payload) => { calls.createIssue.push({ repo, payload }); return { number: 1, html_url: "https://x/issues/1" }; },
    commentIssue: (owner, repo, number, body) => { calls.commentIssue.push({ repo, number, body }); return {}; },
    shutIssue: (owner, repo, number, body) => { calls.shutIssue.push({ repo, number, body }); return {}; },
  };
  return { api, calls, BROKEN, HEALTHY, NO_CONFIG };
}

test("runSweep dry-run over the measured 19-repo fleet flags exactly the 4 broken repos, exit 0, zero writes", () => {
  const { api, calls, BROKEN } = buildFleetApi();
  const result = runSweep({
    owners: ["Adam-S-Daniel", "jodidaniel"], api, nowMs: Date.parse("2026-09-15T00:00:00Z"),
    dryRun: true, revealPrivate: true, requireOwnerTokens: false, env: {},
    registryFleet: [], onlyRepos: [], out: () => {},
  });
  assert.equal(result.exitCode, 0);
  assert.equal(result.assessments.length, 19);
  const findingRepos = result.assessments.filter((a) => a.verdict === "finding").map((a) => a.repo.split("/")[1]).sort();
  assert.deepEqual(findingRepos, [...BROKEN].sort());
  assert.equal(calls.createIssue.length, 0);
  assert.equal(calls.commentIssue.length, 0);
  assert.equal(calls.shutIssue.length, 0);
});

test("runSweep with writes enabled creates exactly one issue per broken repo, in that repo", () => {
  const { api, calls, BROKEN } = buildFleetApi();
  runSweep({
    owners: ["Adam-S-Daniel", "jodidaniel"], api, nowMs: Date.parse("2026-09-15T00:00:00Z"),
    dryRun: false, revealPrivate: true, requireOwnerTokens: false, env: {},
    registryFleet: [], onlyRepos: [], out: () => {},
  });
  assert.equal(calls.createIssue.length, 4);
  const repos = calls.createIssue.map((c) => c.repo).sort();
  assert.deepEqual(repos, BROKEN.map((n) => `Adam-S-Daniel/${n}`).sort());
});

// ── 12. runSweep exit-1 conditions + onlyRepos skipping the registry check ──

test("runSweep: an owner listing failure exits 1", () => {
  const api = fakeApi({ listRepos: () => { throw new ApiError("Bad credentials", 401); } });
  const result = runSweep({ owners: ["Adam-S-Daniel"], api, nowMs: NOW, dryRun: true, requireOwnerTokens: false, env: {}, registryFleet: [], onlyRepos: [], out: () => {} });
  assert.equal(result.exitCode, 1);
  assert.equal(result.ownerFailures.length, 1);
});

test("runSweep: --require-owner-tokens with a missing token is an owner failure naming both knobs", () => {
  const events = [];
  const result = runSweep({ owners: ["Adam-S-Daniel"], api: fakeApi({}), nowMs: NOW, dryRun: true, requireOwnerTokens: true, env: {}, registryFleet: [], onlyRepos: [], out: (l) => events.push(l) });
  assert.equal(result.exitCode, 1);
  const text = events.join("\n");
  assert.ok(text.includes("APP_CLIENT_ID"));
  assert.ok(text.includes("APP_PRIVATE_KEY"));
});

test("runSweep: a registry name not returned by any owner exits 1", () => {
  const api = fakeApi({ listRepos: (owner) => [{ repo: `${owner}/known`, private: false, defaultBranch: "main" }] });
  const result = runSweep({ owners: ["Adam-S-Daniel"], api, nowMs: NOW, dryRun: true, requireOwnerTokens: false, env: {}, registryFleet: ["nonexistent-repo"], onlyRepos: [], out: () => {} });
  assert.equal(result.exitCode, 1);
  assert.deepEqual(result.registryMissing, ["nonexistent-repo"]);
});

test("runSweep: a createIssue failure is a write failure and exits 1", () => {
  const api = fakeApi({
    listRepos: (owner) => [{ repo: `${owner}/broken`, private: false, defaultBranch: "main" }],
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "a", date: "2026-01-01T00:00:00Z" }),
    checkRuns: () => ({ total_count: 1, check_runs: [{ id: 1, app: { slug: "dependabot" }, name: ".github/dependabot.yml", status: "completed", conclusion: "failure", head_sha: "a", output: {}, completed_at: "2026-01-01T00:05:00Z" }] }),
    workflowsPage: () => ({ total_count: 0, workflows: [] }),
    createIssue: () => { throw new ApiError("Server Error", 500); },
  });
  const result = runSweep({ owners: ["Adam-S-Daniel"], api, nowMs: Date.parse("2026-02-01T00:00:00Z"), dryRun: false, requireOwnerTokens: false, env: {}, registryFleet: [], onlyRepos: [], out: () => {} });
  assert.equal(result.exitCode, 1);
  assert.equal(result.writeFailures.length, 1);
});

test("runSweep: --repo skips the registry cross-check even when the registry names an absent repo", () => {
  const api = fakeApi({ listRepos: (owner) => [{ repo: `${owner}/known`, private: false, defaultBranch: "main" }] });
  const result = runSweep({ owners: ["Adam-S-Daniel"], api, nowMs: NOW, dryRun: true, requireOwnerTokens: false, env: {}, registryFleet: ["totally-not-there"], onlyRepos: ["Adam-S-Daniel/known"], out: () => {} });
  assert.equal(result.registryMissing.length, 0);
  assert.equal(result.exitCode, 0);
});

// ── 13. Negative control pair ────────────────────────────────────────────────

test("NEGATIVE CONTROL: a healthy fixture yields no findings (the detector is not always-on)", () => {
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "a", date: "2026-08-01T00:00:00Z" }),
    checkRuns: () => ({ total_count: 1, check_runs: [{ id: 1, app: { slug: "dependabot" }, name: ".github/dependabot.yml", status: "completed", conclusion: "success", head_sha: "a", completed_at: "2026-08-01T00:05:00Z" }] }),
    workflowsPage: () => ({ total_count: 1, workflows: [{ id: 1, path: UPDATES_WORKFLOW_PATH }] }),
    newestWorkflowRun: () => ({ created_at: "2026-09-10T00:00:00Z", html_url: "https://x" }),
  });
  const a = assessRepo({ owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x", private: false, defaultBranch: "main" }, api, Date.parse("2026-09-15T00:00:00Z"), { label: "ci" });
  assert.equal(a.verdict, "healthy");
  assert.deepEqual(a.findings, []);
});

test("NEGATIVE CONTROL: a broken fixture yields a finding (the detector is not always-off)", () => {
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "a", date: "2026-08-01T00:00:00Z" }),
    checkRuns: () => ({ total_count: 1, check_runs: [{ id: 1, app: { slug: "dependabot" }, name: ".github/dependabot.yml", status: "completed", conclusion: "failure", head_sha: "a", output: { title: "t", summary: "s" }, completed_at: "2026-08-01T00:05:00Z" }] }),
    workflowsPage: () => ({ total_count: 1, workflows: [{ id: 1, path: UPDATES_WORKFLOW_PATH }] }),
    newestWorkflowRun: () => ({ created_at: "2026-09-10T00:00:00Z", html_url: "https://x" }),
  });
  const a = assessRepo({ owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x", private: false, defaultBranch: "main" }, api, Date.parse("2026-09-15T00:00:00Z"), { label: "ci" });
  assert.equal(a.verdict, "finding");
  assert.ok(a.findings.some((f) => f.kind === "invalid-config"));
});

// ── 14. Workflow shape ────────────────────────────────────────────────────────

const WORKFLOW_PATH = path.join(__dirname, "..", ".github", "workflows", "dependabot-config-health.yml");
const workflowDoc = () => YAML.parse(fs.readFileSync(WORKFLOW_PATH, "utf8"));

test("workflow shape: triggers are exactly schedule + workflow_dispatch", () => {
  const doc = workflowDoc();
  const on = Object.prototype.hasOwnProperty.call(doc, "on") ? doc.on : doc.true;
  assert.deepEqual(Object.keys(on).sort(), ["schedule", "workflow_dispatch"]);
});

test("workflow shape: top-level permissions is exactly {contents: read}", () => {
  assert.deepEqual(workflowDoc().permissions, { contents: "read" });
});

test("workflow shape: no concurrency block anywhere in the file", () => {
  const doc = workflowDoc();
  assert.equal(doc.concurrency, undefined);
  for (const job of Object.values(doc.jobs || {})) {
    assert.equal(job.concurrency, undefined);
  }
});

test("workflow shape: no step's run: block contains ${{", () => {
  const doc = workflowDoc();
  for (const job of Object.values(doc.jobs || {})) {
    for (const step of job.steps || []) {
      if (typeof step.run === "string") {
        assert.ok(!step.run.includes("${{"), `run: interpolates \${{ }}: ${step.run}`);
      }
    }
  }
});

test("workflow shape: every uses: is pinned to a full 40-char sha with no trailing comment", () => {
  const doc = workflowDoc();
  const usesList = [];
  for (const job of Object.values(doc.jobs || {})) {
    for (const step of job.steps || []) {
      if (typeof step.uses === "string") usesList.push(step.uses);
    }
  }
  assert.ok(usesList.length > 0);
  for (const u of usesList) {
    assert.match(u, /@[0-9a-f]{40}$/, `${u} is not pinned to a bare 40-char sha`);
  }
});

test("workflow shape: the sweep step passes --require-owner-tokens", () => {
  const doc = workflowDoc();
  const runs = [];
  for (const job of Object.values(doc.jobs || {})) {
    for (const step of job.steps || []) {
      if (typeof step.run === "string") runs.push(step.run);
    }
  }
  assert.ok(runs.some((r) => r.includes("--require-owner-tokens")));
});

// ── 15. Review regressions (A-D) ────────────────────────────────────────────
// Four defects a review found in the first cut, each with a dedicated
// regression test so the repair cannot silently slip back in.

test("A: a finding that ALSO carries unknowns (check missing + no job) reds runSweep's exit code and still files", () => {
  const created = [];
  const api = fakeApi({
    listRepos: (owner) => [{ repo: `${owner}/half-broken`, private: false, defaultBranch: "main" }],
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "a", date: "2026-08-01T00:00:00Z" }),
    checkRuns: () => ({ total_count: 0, check_runs: [] }), // missing — an unknown, not a finding by itself
    workflowsPage: () => ({ total_count: 0, workflows: [] }), // never ran — silent, a finding
    createIssue: (owner, repo) => { created.push(repo); return { number: 1, html_url: "https://x/1" }; },
  });
  const result = runSweep({
    owners: ["Adam-S-Daniel"], api, nowMs: Date.parse("2026-09-15T00:00:00Z"),
    dryRun: false, revealPrivate: true, requireOwnerTokens: false, env: {},
    registryFleet: [], onlyRepos: [], out: () => {},
  });
  const a = result.assessments[0];
  assert.equal(a.verdict, "finding");
  assert.ok(a.unknowns.length > 0, "expected the check-missing unknown to survive alongside the finding");
  assert.equal(result.exitCode, 1, "a finding with unknowns must red the run, not just a pure unknown");
  assert.deepEqual(created, ["Adam-S-Daniel/half-broken"], "the finding still files, despite the unknown");
});

test("B: a failed open-issue lookup never files a duplicate, and reds the run", () => {
  const created = [];
  const api = fakeApi({
    listRepos: (owner) => [{ repo: `${owner}/flaky-lookup`, private: false, defaultBranch: "main" }],
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "a", date: "2026-08-01T00:00:00Z" }),
    checkRuns: () => ({
      total_count: 1,
      check_runs: [{ id: 1, app: { slug: "dependabot" }, name: ".github/dependabot.yml", status: "completed", conclusion: "failure", head_sha: "a", output: {}, completed_at: "2026-08-01T00:05:00Z" }],
    }),
    workflowsPage: () => ({ total_count: 0, workflows: [] }),
    openIssuesPage: () => { throw new ApiError("Internal Server Error", 500); },
    createIssue: (owner, repo) => { created.push(repo); return { number: 1, html_url: "https://x/1" }; },
  });
  const result = runSweep({
    owners: ["Adam-S-Daniel"], api, nowMs: Date.parse("2026-09-15T00:00:00Z"),
    dryRun: false, revealPrivate: true, requireOwnerTokens: false, env: {},
    registryFleet: [], onlyRepos: [], out: () => {},
  });
  assert.deepEqual(created, [], "a flaky lookup must never risk a duplicate issue");
  assert.equal(result.exitCode, 1);
  assert.equal(result.assessments[0].issueLookupFailed, true);
});

test("C: runSweep output never doubles the owner (owner/owner/name), and a healthy repo reads owner/name: verdict", () => {
  const { api } = buildFleetApi();
  const events = [];
  runSweep({
    owners: ["Adam-S-Daniel", "jodidaniel"], api, nowMs: Date.parse("2026-09-15T00:00:00Z"),
    dryRun: true, revealPrivate: true, requireOwnerTokens: false, env: {},
    registryFleet: [], onlyRepos: [], out: (l) => events.push(l),
  });
  assert.ok(events.includes("Adam-S-Daniel/cms-platform: healthy"));
  const ownerDoubled = /([^/\s]+)\/\1\//;
  for (const line of events) {
    assert.ok(!ownerDoubled.test(line), `owner doubled in output line: ${line}`);
  }
});

test("C: renderIssueBody's Repo: line names the repo exactly once", () => {
  const assessment = {
    owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/claude-memory-map",
    findings: [{ kind: "invalid-config", key: "check:1" }],
    check: { htmlUrl: "https://x/runs/1", sha: "abc", outputTitle: "bad", outputSummary: "s" },
    jobs: {},
  };
  const body = renderIssueBody(assessment);
  assert.ok(body.includes("Repo: Adam-S-Daniel/claude-memory-map"));
  assert.ok(!body.includes("Repo: Adam-S-Daniel/Adam-S-Daniel/"));
});

test("D: a workflows page short of 100 items stops pagination without an extra empty-page fetch", () => {
  let calls = 0;
  const api = fakeApi({
    getContent: (o, r, p) => (p === ".github/dependabot.yml" ? yamlContent(WEEKLY_YAML) : (() => { throw new ApiError("Not Found", 404); })()),
    newestPathCommit: () => ({ sha: "a", date: "2026-08-01T00:00:00Z" }),
    checkRuns: () => ({
      total_count: 1,
      check_runs: [{ id: 1, app: { slug: "dependabot" }, name: ".github/dependabot.yml", status: "completed", conclusion: "success", head_sha: "a", completed_at: "2026-08-01T00:05:00Z" }],
    }),
    workflowsPage: () => {
      calls++;
      return {
        total_count: 5,
        workflows: [
          { id: 1, path: UPDATES_WORKFLOW_PATH },
          { id: 2, path: "other-a.yml" }, { id: 3, path: "other-b.yml" },
          { id: 4, path: "other-c.yml" }, { id: 5, path: "other-d.yml" },
        ],
      };
    },
    newestWorkflowRun: () => ({ created_at: "2026-09-10T00:00:00Z", html_url: "https://x" }),
  });
  const a = assessRepo(
    { owner: "Adam-S-Daniel", repo: "Adam-S-Daniel/x", private: false, defaultBranch: "main" },
    api, Date.parse("2026-09-15T00:00:00Z"), { label: "ci" },
  );
  assert.equal(calls, 1, "a first page short of 100 items must not trigger a second, confirmatory fetch");
  assert.notEqual(a.jobs.state, "unknown");
});
