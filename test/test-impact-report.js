"use strict";
// test-impact-report.js — node:test coverage for scripts/impact-report.js and
// .github/workflows/impact-report.yml. The fleet is invented
// (test/fixtures/impact-report-fleet.json); `api` is fixture-backed and
// `nowMs` pinned, so nothing touches the network or the clock. Run via
// test/run-tests.sh's test_impact_report.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const YAML = require("yaml");

const {
  ApiError, HOOK_NAME, HOOK_WORKFLOW_PATH, CONCURRENCY, RETRY_DELAYS_MS,
  limiter, isTransient, withRetry, weekOf, windowFor, runReport, shouldPublish,
} = require("../scripts/impact-report.js");

const ROOT = path.join(__dirname, "..");
const FLEET = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "impact-report-fleet.json"), "utf8"));
const NOW = Date.parse("2026-10-28T12:00:00Z");
const OWNERS = ["example-owner", "example-org"];

function page(list, p) {
  return (list || []).slice((p - 1) * 100, p * 100);
}

function fixtureApi(overrides = {}) {
  return {
    listRepos: (owner) => FLEET.repos[owner],
    pullsPage: (owner, repo, p) => page(FLEET.pulls[repo], p),
    checkRunsPage: (owner, repo, sha, p) => ({ check_runs: page(FLEET.check_runs[sha], p) }),
    workflowsPage: (owner, repo, p) => ({ workflows: page(FLEET.workflows[repo], p) }),
    workflowRunsPage: (owner, repo, id, since, p) => ({ workflow_runs: page(FLEET.workflow_runs[String(id)], p) }),
    ...overrides,
  };
}

async function report(overrides, extra = {}) {
  const lines = [];
  const result = await runReport({
    owners: OWNERS, api: fixtureApi(overrides), nowMs: NOW, env: {},
    out: (l) => lines.push(l), ...extra,
  });
  return { ...result, lines };
}

const row = (doc, week, kind, repo) => doc.rows.find((r) => r.week === week && r.kind === kind && r.repo === repo);
const zero = { prs_opened: 0, prs_merged: 0, prs_closed_unmerged: 0, prs_reverted: 0, agent_prs_merged: 0, ci_red_after: 0 };

test("weekOf names the Monday, with the boundary at 00:00Z Monday", () => {
  assert.equal(weekOf(Date.parse("2026-10-25T23:59:59Z")), "2026-10-19");
  assert.equal(weekOf(Date.parse("2026-10-26T00:00:00Z")), "2026-10-26");
  assert.equal(weekOf(Date.parse("2026-11-01T23:59:59Z")), "2026-10-26");
  assert.equal(weekOf(Date.parse("2026-01-01T12:00:00Z")), "2025-12-29");
});

test("windowFor covers the 8 weeks ending with the current one", () => {
  const { weeks, fromMs } = windowFor(NOW);
  assert.equal(weeks.length, 8);
  assert.equal(weeks[0], "2026-09-07");
  assert.equal(weeks[7], "2026-10-26");
  assert.equal(new Date(fromMs).toISOString(), "2026-09-07T00:00:00.000Z");
});

test("the document has the agreed envelope", async () => {
  const { exitCode, doc } = await report();
  assert.equal(exitCode, 0);
  assert.equal(doc.schema, 1);
  assert.equal(doc.generated_at, "2026-10-28T12:00:00Z");
  assert.deepEqual(doc.sources, [{
    surface: "ci", scope: "github", from: "2026-09-07T00:00:00Z", to: "2026-10-28T12:00:00Z", repos_scanned: 4,
  }]);
  assert.deepEqual(Object.keys(doc.rows[0]), [
    "week", "kind", "name", "repo", "surface", "invocations", "sessions", "blocked", "overridden", "outcome",
  ]);
});

test("every repo counter, bucketed by the week of its own event", async () => {
  const { doc } = await report();
  const w = "example-owner/widgets";
  assert.deepEqual(row(doc, "2026-10-26", "repo", w).outcome,
    { ...zero, prs_opened: 1, prs_merged: 2, prs_reverted: 1, agent_prs_merged: 1, ci_red_after: 1 });
  // Created 23:59:59 Sunday: opened in the earlier week, merged in the later.
  assert.deepEqual(row(doc, "2026-10-19", "repo", w).outcome, { ...zero, prs_opened: 1, prs_closed_unmerged: 1 });
  // `Revert widget docs` has no quote, so it is no revert; its base is not the
  // default branch, so its red merge commit is not counted.
  assert.deepEqual(row(doc, "2026-10-12", "repo", w).outcome, { ...zero, prs_opened: 2, prs_merged: 1 });
  // Opened before the window, merged inside it.
  assert.deepEqual(row(doc, "2026-09-07", "repo", w).outcome, { ...zero, prs_merged: 1 });
  assert.deepEqual(row(doc, "2026-10-19", "repo", "example-owner/gadgets").outcome,
    { ...zero, prs_opened: 1, prs_merged: 1, ci_red_after: 1 });
  assert.deepEqual(row(doc, "2026-09-14", "repo", w).outcome, zero);
  assert.equal(row(doc, "2026-10-26", "repo", w).invocations, null);
});

test("a PR last updated before the window is never counted", async () => {
  const { doc } = await report();
  const total = doc.rows.filter((r) => r.kind === "repo").reduce((n, r) => n + r.outcome.agent_prs_merged, 0);
  assert.equal(total, 2); // widgets 4711 and the private 4732, never 4716
});

test("private repos fold into one row per week and kind", async () => {
  const { doc } = await report();
  const p = row(doc, "2026-10-19", "repo", "private");
  assert.equal(p.name, "private");
  assert.deepEqual(p.outcome,
    { ...zero, prs_opened: 3, prs_merged: 2, prs_reverted: 1, agent_prs_merged: 1, ci_red_after: 1 });
  const repos = new Set(doc.rows.map((r) => r.repo));
  assert.deepEqual([...repos].sort(), ["example-owner/gadgets", "example-owner/widgets", "private"]);
  assert.equal(doc.rows.filter((r) => r.repo === "private" && r.kind === "repo").length, 8);
});

test("agent-checks hook rows count completed PR runs and their failures", async () => {
  const { doc } = await report();
  const w = "example-owner/widgets";
  assert.deepEqual(
    (({ name, invocations, sessions, blocked, overridden, outcome }) => ({ name, invocations, sessions, blocked, overridden, outcome }))(row(doc, "2026-10-19", "hook", w)),
    { name: HOOK_NAME, invocations: 2, sessions: null, blocked: 2, overridden: 2, outcome: null });
  assert.equal(row(doc, "2026-10-26", "hook", w).invocations, 1);
  assert.equal(row(doc, "2026-10-26", "hook", w).blocked, 0);
  assert.equal(row(doc, "2026-10-19", "hook", "private").invocations, 2);
  assert.equal(row(doc, "2026-10-19", "hook", "private").blocked, 1);
  // gadgets has no agent-checks workflow: no hook rows at all.
  assert.equal(doc.rows.some((r) => r.kind === "hook" && r.repo === "example-owner/gadgets"), false);
});

test("rows are sorted by week, kind, name", async () => {
  const { doc } = await report();
  assert.equal(doc.rows.length, 40);
  const key = (r) => [r.week, r.kind, r.name, r.repo].join("\u0000");
  const keys = doc.rows.map(key);
  assert.deepEqual(keys, [...keys].sort());
  assert.deepEqual(doc.rows.slice(0, 5).map((r) => [r.kind, r.repo]), [
    ["hook", "example-owner/widgets"], ["hook", "private"],
    ["repo", "example-owner/gadgets"], ["repo", "example-owner/widgets"], ["repo", "private"],
  ]);
});

test("no title, author, branch, PR number or private name reaches the output or the log", async () => {
  const { doc, lines } = await report();
  const text = JSON.stringify(doc) + "\n" + lines.join("\n");
  const forbidden = ["secret-plans", "hidden-thing", "example-org"];
  for (const list of Object.values(FLEET.pulls)) {
    for (const pr of list) forbidden.push(pr.title, pr.user.login, pr.head.ref, String(pr.number));
  }
  for (const s of forbidden) assert.equal(text.includes(s), false, `output carries ${JSON.stringify(s)}`);
  for (const l of lines) assert.equal(/example-owner/.test(l), false, "a log line names a repo");
});

test("an API failure fails the run, writes nothing, and logs only status and type", async () => {
  const { exitCode, doc, lines } = await report({
    pullsPage: (owner, repo) => { if (repo === "example-owner/secret-plans") throw new ApiError(404); return []; },
  });
  assert.equal(exitCode, 1);
  assert.equal(doc, null);
  assert.ok(lines.some((l) => l === "::error::impact-report: repo: ApiError HTTP 404"));
  assert.equal(lines.join("\n").includes("secret"), false);
});

test("a truncated listing fails rather than undercounting", async () => {
  const busy = Array.from({ length: 100 }, () => ({ status: "completed", conclusion: "success", event: "pull_request", created_at: "2026-10-27T00:00:00Z" }));
  const { exitCode, lines } = await report({ workflowRunsPage: () => ({ workflow_runs: busy }) });
  assert.equal(exitCode, 1);
  assert.ok(lines.some((l) => l.includes("RangeError")));
});

test("a missing owner token fails the run under --require-owner-tokens", async () => {
  const { exitCode } = await report({}, { requireOwnerTokens: true, env: { GH_TOKEN_EXAMPLE_OWNER: "x" } });
  assert.equal(exitCode, 1);
});

test("shouldPublish skips unchanged rows younger than 6 days only", async () => {
  const { doc } = await report();
  const old = (days) => ({ ...doc, generated_at: new Date(NOW - days * 86400000).toISOString() });
  assert.equal(shouldPublish(null, doc, NOW), true);
  assert.equal(shouldPublish(old(1), doc, NOW), false);
  assert.equal(shouldPublish(old(6), doc, NOW), true);
  const changed = { ...old(1), rows: doc.rows.slice(1) };
  assert.equal(shouldPublish(changed, doc, NOW), true);
});

// Resolves on a later macrotask, so concurrent calls genuinely overlap.
const later = (v) => new Promise((resolve) => setImmediate(() => resolve(v)));

test("limiter runs at most n tasks at once and returns each task's own result", async () => {
  const slot = limiter(3);
  let active = 0;
  let peak = 0;
  const task = (i) => slot(async () => {
    active++;
    peak = Math.max(peak, active);
    await later();
    active--;
    if (i === 7) throw new RangeError("task 7");
    return i * 2;
  });
  const settled = await Promise.allSettled(Array.from({ length: 12 }, (_, i) => task(i)));
  assert.equal(peak, 3);
  assert.equal(active, 0);
  assert.deepEqual(settled.map((s) => (s.status === "fulfilled" ? s.value : s.reason.message)),
    [0, 2, 4, 6, 8, 10, 12, "task 7", 16, 18, 20, 22]);
  assert.throws(() => limiter(0), RangeError);
  assert.ok(Number.isInteger(CONCURRENCY) && CONCURRENCY >= 1 && CONCURRENCY <= 10);
});

test("an api that answers asynchronously yields the same document", async () => {
  const sync = await report();
  const base = fixtureApi();
  const asyncApi = Object.fromEntries(Object.entries(base).map(([k, f]) => [k, (...a) => later(f(...a))]));
  const asyncResult = await report(asyncApi);
  assert.equal(asyncResult.exitCode, 0);
  assert.deepEqual(asyncResult.doc, sync.doc);
});

test("a failed check-run lookup fails its repo and logs only status and type", async () => {
  const { exitCode, doc, lines } = await report({
    checkRunsPage: () => later().then(() => { throw new ApiError(502); }),
  });
  assert.equal(exitCode, 1);
  assert.equal(doc, null);
  assert.ok(lines.some((l) => l === "::error::impact-report: repo: ApiError HTTP 502"));
});

test("only 5xx, 429 and status-less errors are transient", () => {
  for (const s of [null, 429, 500, 502, 503]) assert.equal(isTransient(new ApiError(s)), true, String(s));
  for (const s of [400, 401, 403, 404, 422]) assert.equal(isTransient(new ApiError(s)), false, String(s));
  assert.equal(isTransient(new RangeError("pagination ceiling reached")), false);
});

test("withRetry retries a transient failure with the given waits, then succeeds", async () => {
  const waits = [];
  let calls = 0;
  const value = await withRetry(async () => {
    calls++;
    if (calls === 1) throw new ApiError(null);
    if (calls === 2) throw new ApiError(502);
    return "ok";
  }, [5, 10, 20], async (ms) => { waits.push(ms); });
  assert.equal(value, "ok");
  assert.equal(calls, 3);
  assert.deepEqual(waits, [5, 10]);
});

test("withRetry gives up after the last wait, and never retries a 404", async () => {
  const waits = [];
  let calls = 0;
  await assert.rejects(withRetry(async () => { calls++; throw new ApiError(503); }, [1, 2], async (ms) => { waits.push(ms); }),
    (e) => e instanceof ApiError && e.status === 503);
  assert.equal(calls, 3);
  assert.deepEqual(waits, [1, 2]);
  calls = 0;
  await assert.rejects(withRetry(async () => { calls++; throw new ApiError(404); }, [1, 2], async () => {}),
    (e) => e.status === 404);
  assert.equal(calls, 1);
  assert.equal(RETRY_DELAYS_MS.length >= 2, true);
});

// ── The workflow and the template it measures (parsed, never regexed) ──────

const WF = YAML.parse(fs.readFileSync(path.join(ROOT, ".github/workflows/impact-report.yml"), "utf8"));
const steps = WF.jobs.report.steps;

test("workflow: schedule and dispatch only, never a PR or push trigger", () => {
  assert.deepEqual(Object.keys(WF.on).sort(), ["schedule", "workflow_dispatch"]);
  assert.equal(WF.on.schedule.length, 1);
});

test("workflow: the job holds contents: read and nothing more", () => {
  assert.deepEqual(WF.permissions, { contents: "read" });
  for (const job of Object.values(WF.jobs)) assert.equal(job.permissions, undefined);
});

test("workflow: read tokens are read-only; the write token reaches skills-evals contents alone", () => {
  const mints = steps.filter((s) => String(s.uses || "").startsWith("actions/create-github-app-token@"));
  assert.equal(mints.length, 3);
  const perms = (s) => Object.fromEntries(Object.entries(s.with).filter(([k]) => k.startsWith("permission-")));
  const writers = mints.filter((s) => Object.values(perms(s)).includes("write"));
  assert.equal(writers.length, 1);
  assert.deepEqual(perms(writers[0]), { "permission-contents": "write" });
  assert.equal(writers[0].with.repositories, "skills-evals");
  assert.equal(writers[0].with.owner, "Adam-S-Daniel");
  for (const s of mints.filter((m) => m !== writers[0])) {
    assert.equal(s.with.repositories, undefined);
    assert.deepEqual(Object.values(perms(s)).sort(), ["read", "read", "read", "read", "read"]);
  }
});

test("workflow: every uses: is an exact tag locked in actions.lock", () => {
  const lock = YAML.parse(fs.readFileSync(path.join(ROOT, ".github/workflows/actions.lock"), "utf8"));
  const locked = lock.workflows[".github/workflows/impact-report.yml"];
  assert.ok(Array.isArray(locked), "actions.lock lists impact-report.yml");
  for (const s of steps.filter((x) => x.uses)) {
    assert.match(s.uses, /^[\w.-]+\/[\w.-]+@v\d+\.\d+\.\d+$/);
    const [name, ref] = s.uses.split("@");
    assert.ok(locked.includes(`${name.toLowerCase()}@${ref}`), `${s.uses} is not locked`);
  }
});

test("workflow: the publish commits only usage/weekly-ci.json to persistent/eval-results", () => {
  const env = WF.jobs.report.env;
  assert.equal(env.PUBLISH_PATH, "usage/weekly-ci.json");
  assert.equal(env.PUBLISH_BRANCH, "persistent/eval-results");
  assert.equal(env.PUBLISH_REPO, "Adam-S-Daniel/skills-evals");
  const publish = steps.find((s) => s.name === "Publish usage/weekly-ci.json");
  const adds = publish.run.split("\n").map((l) => l.trim()).filter((l) => l.startsWith("git add"));
  assert.deepEqual(adds, ['git add -- "$PUBLISH_PATH"']);
  assert.ok(publish.run.includes('"$staged" != "$PUBLISH_PATH"'));
  assert.ok(publish.run.includes('git merge-base --is-ancestor HEAD "origin/$PUBLISH_BRANCH"'));
  assert.ok(publish.run.includes('-m "propagation: impact report [skip ci]"'));
});

test("workflow: no inputs or event payload is interpolated into a run: block", () => {
  for (const s of steps.filter((x) => x.run)) {
    assert.equal(s.run.includes("${{"), false, `${s.name || "a step"} interpolates an expression into run:`);
  }
});

test("template: agent-checks has exactly one job, so a run's conclusion is its check run's", () => {
  const tmpl = YAML.parse(fs.readFileSync(path.join(ROOT, "templates/agent-checks/agent-checks.yml"), "utf8"));
  assert.deepEqual(Object.keys(tmpl.jobs), [HOOK_NAME]);
  assert.equal(tmpl.jobs[HOOK_NAME].name, undefined);
  assert.equal(HOOK_WORKFLOW_PATH, ".github/workflows/agent-checks.yml");
});
