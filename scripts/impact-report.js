#!/usr/bin/env node
"use strict";
/*
 * impact-report.js — weekly real-work outcomes per fleet repo, for the
 * project dashboard's impact view. Run daily by
 * .github/workflows/impact-report.yml, which publishes the output to
 * skills-evals' `persistent/eval-results` branch as usage/weekly-ci.json.
 *
 * For every non-fork, non-archived repo under SYNC_OWNERS and each of the
 * last WEEKS ISO weeks (a week is named by its Monday, UTC), it counts:
 *   repo rows  prs_opened           PRs created that week
 *              prs_merged           PRs merged that week
 *              prs_closed_unmerged  PRs closed unmerged that week
 *              prs_reverted         merged PRs titled `Revert "...` (merge week)
 *              agent_prs_merged     merged PRs labelled `agent-authored`
 *              ci_red_after         merged PRs into the default branch whose
 *                                   merge commit carries a failed check run
 *   hook rows  the agent-checks CI job (templates/agent-checks/agent-checks.yml)
 *              on PR heads: invocations = completed runs, blocked = failed
 *              runs. `overridden` repeats `blocked`: a red CI run means the
 *              commit got past (or never met) the local hook.
 *
 * Runs are read from the agent-checks WORKFLOW's runs (event pull_request),
 * not from per-commit check runs: a PR listing only exposes the current head,
 * so check runs would miss every earlier push. The template has exactly one
 * job, `agent-checks`, so a run's conclusion is that job's check-run
 * conclusion (a test parses the template to keep it that way). Cancelled and
 * skipped runs never reached a verdict and are not counted.
 *
 * PRIVACY. The output lands on a PUBLIC branch. Every private repo is folded
 * into one row per week and kind named "private"; no title, author, branch
 * name or PR number is ever copied into the output or a log line. Log lines
 * carry counts only, and an API error is reduced to its HTTP status and type
 * (gh's stderr names the URL, which names the repo).
 *
 * Counts are exact or absent: any listing failure, truncated pagination or
 * unparseable timestamp fails the run (exit 1) and nothing is written, so the
 * dashboard never shows a partial week as a real one.
 *
 * Usage:
 *   SYNC_OWNERS="Adam-S-Daniel jodidaniel" node scripts/impact-report.js --out weekly-ci.json
 *   node scripts/impact-report.js --out f.json --previous f.json --require-owner-tokens
 * With --previous, the file is left untouched when its rows are unchanged and
 * it is younger than REFRESH_DAYS, so an idle week publishes no commit.
 *
 * Exit codes: 0 written (or deliberately left unchanged); 1 incomplete data,
 * nothing written; 2 bad invocation.
 */
const fs = require("node:fs");
const { execFileSync } = require("node:child_process");

const WEEKS = 8;
const DAY_MS = 86400000;
const REFRESH_DAYS = 6;
const MAX_PAGES = 10;
const AGENT_LABEL = "agent-authored";
const HOOK_NAME = "agent-checks";
const HOOK_WORKFLOW_PATH = ".github/workflows/agent-checks.yml";
const RED = new Set(["failure", "timed_out", "startup_failure"]);
const UNCOUNTED = new Set(["cancelled", "skipped"]);
const OUTCOME_KEYS = [
  "prs_opened", "prs_merged", "prs_closed_unmerged",
  "prs_reverted", "agent_prs_merged", "ci_red_after",
];

class ApiError extends Error {
  constructor(status) {
    super(status ? `HTTP ${status}` : "no HTTP status");
    this.name = "ApiError";
    this.status = status || null;
  }
}

// The one non-identifying description of a failure a log line may carry.
function describeError(e) {
  if (e instanceof ApiError) return `ApiError ${e.message}`;
  return e && e.name ? e.name : "Error";
}

// ── gh plumbing: the only impure layer ─────────────────────────────────────

function ownerTokenEnvName(owner) {
  return "GH_TOKEN_" + owner.toUpperCase().replace(/[-.]/g, "_");
}

function buildApi(env) {
  const call = (owner, args) => {
    const token = env[ownerTokenEnvName(owner)];
    try {
      return JSON.parse(execFileSync("gh", args, {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
        maxBuffer: 64 * 1024 * 1024,
        env: token ? { ...env, GH_TOKEN: token } : env,
      }));
    } catch (e) {
      // Never the message: gh's stderr and stdout carry the URL and body.
      const m = /HTTP (\d{3})/.exec(String(e.stderr || ""));
      throw new ApiError(m ? Number(m[1]) : null);
    }
  };
  return {
    listRepos(owner) {
      return call(owner, [
        "repo", "list", owner, "--source", "--no-archived", "--limit", "1000",
        "--json", "nameWithOwner,isPrivate,defaultBranchRef",
      ]).map((r) => ({
        repo: r.nameWithOwner,
        private: Boolean(r.isPrivate),
        defaultBranch: (r.defaultBranchRef && r.defaultBranchRef.name) || null,
      }));
    },
    pullsPage(owner, repo, page) {
      return call(owner, ["api", `repos/${repo}/pulls?state=all&sort=updated&direction=desc&per_page=100&page=${page}`]);
    },
    checkRunsPage(owner, repo, sha, page) {
      return call(owner, ["api", `repos/${repo}/commits/${sha}/check-runs?per_page=100&page=${page}`]);
    },
    workflowsPage(owner, repo, page) {
      return call(owner, ["api", `repos/${repo}/actions/workflows?per_page=100&page=${page}`]);
    },
    workflowRunsPage(owner, repo, workflowId, sinceDate, page) {
      return call(owner, [
        "api",
        `repos/${repo}/actions/workflows/${workflowId}/runs?event=pull_request&created=${encodeURIComponent(">=" + sinceDate)}&per_page=100&page=${page}`,
      ]);
    },
  };
}

// ── Pure helpers ───────────────────────────────────────────────────────────

function parseTime(iso) {
  const ms = typeof iso === "string" ? Date.parse(iso) : NaN;
  if (Number.isNaN(ms)) throw new TypeError("unparseable timestamp");
  return ms;
}

// The Monday (UTC) of the ISO week containing `ms`, as YYYY-MM-DD.
function weekOf(ms) {
  const d = new Date(ms);
  const midnight = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  const sinceMonday = (d.getUTCDay() + 6) % 7;
  return new Date(midnight - sinceMonday * DAY_MS).toISOString().slice(0, 10);
}

// The WEEKS week labels ending with the current one, oldest first, and the
// window start (00:00Z on the oldest Monday).
function windowFor(nowMs) {
  const current = parseTime(weekOf(nowMs) + "T00:00:00Z");
  const weeks = [];
  for (let i = WEEKS - 1; i >= 0; i--) weeks.push(weekOf(current - i * 7 * DAY_MS));
  return { weeks, fromMs: current - (WEEKS - 1) * 7 * DAY_MS };
}

function isoZ(ms) {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
}

function emptyOutcome() {
  return Object.fromEntries(OUTCOME_KEYS.map((k) => [k, 0]));
}

// Pages a list endpoint until a short page; MAX_PAGES full pages is a
// truncation, never a silent stop. `stop(item)` ends early on sorted input.
function collectPages(fetchPage, pick, stop) {
  const out = [];
  for (let page = 1; page <= MAX_PAGES; page++) {
    const items = pick(fetchPage(page));
    if (!Array.isArray(items)) throw new TypeError("unexpected response shape");
    for (const item of items) {
      if (stop && stop(item)) return out;
      out.push(item);
    }
    if (items.length < 100) return out;
  }
  throw new RangeError("pagination ceiling reached");
}

function hasRedCheck(api, owner, repo, sha) {
  const runs = collectPages((p) => api.checkRunsPage(owner, repo, sha, p), (d) => d && d.check_runs);
  return runs.some((r) => RED.has(r.conclusion));
}

// Counts for one repo, keyed by week. Throws on anything that would make a
// count inexact; the caller turns that into a failed repo.
function measureRepo(target, api, { weeks, fromMs, toMs }) {
  const { owner, repo, defaultBranch } = target;
  const inWindow = (ms) => ms >= fromMs && ms <= toMs;
  const outcome = Object.fromEntries(weeks.map((w) => [w, emptyOutcome()]));
  const bump = (ms, key) => { if (inWindow(ms)) outcome[weekOf(ms)][key]++; };

  // Sorted by updated desc: anything opened, merged or closed in the window
  // was updated in it, so the first older PR ends the listing.
  const pulls = collectPages(
    (p) => api.pullsPage(owner, repo, p),
    (d) => d,
    (pr) => parseTime(pr.updated_at) < fromMs,
  );
  for (const pr of pulls) {
    bump(parseTime(pr.created_at), "prs_opened");
    if (pr.merged_at) {
      const merged = parseTime(pr.merged_at);
      if (!inWindow(merged)) continue;
      bump(merged, "prs_merged");
      if (typeof pr.title === "string" && pr.title.startsWith('Revert "')) bump(merged, "prs_reverted");
      if ((pr.labels || []).some((l) => l && l.name === AGENT_LABEL)) bump(merged, "agent_prs_merged");
      const base = pr.base && pr.base.ref;
      if (base && base === defaultBranch && pr.merge_commit_sha
          && hasRedCheck(api, owner, repo, pr.merge_commit_sha)) {
        bump(merged, "ci_red_after");
      }
    } else if (pr.closed_at) {
      bump(parseTime(pr.closed_at), "prs_closed_unmerged");
    }
  }

  const workflows = collectPages((p) => api.workflowsPage(owner, repo, p), (d) => d && d.workflows);
  const wf = workflows.find((w) => w && w.path === HOOK_WORKFLOW_PATH);
  let hook = null;
  if (wf) {
    hook = Object.fromEntries(weeks.map((w) => [w, { runs: 0, failures: 0 }]));
    const since = weeks[0];
    const runs = collectPages(
      (p) => api.workflowRunsPage(owner, repo, wf.id, since, p),
      (d) => d && d.workflow_runs,
    );
    for (const run of runs) {
      if (run.event !== "pull_request" || run.status !== "completed" || UNCOUNTED.has(run.conclusion)) continue;
      const ms = parseTime(run.created_at);
      if (!inWindow(ms)) continue;
      hook[weekOf(ms)].runs++;
      if (RED.has(run.conclusion)) hook[weekOf(ms)].failures++;
    }
  }
  return { outcome, hook };
}

function compareRows(a, b) {
  for (const k of ["week", "kind", "name"]) {
    if (a[k] < b[k]) return -1;
    if (a[k] > b[k]) return 1;
  }
  return 0;
}

// Turns per-repo measurements into the published rows, folding every
// private repo into one "private" row per week and kind.
function buildRows(measured, weeks) {
  const rows = [];
  const fold = (list, pick) => {
    const named = list.filter((m) => !m.private).map((m) => ({ name: m.repo, data: pick(m) }));
    const priv = list.filter((m) => m.private);
    if (priv.length) named.push({ name: "private", data: priv.map(pick) });
    return named;
  };
  for (const { name, data } of fold(measured, (m) => m.outcome)) {
    for (const week of weeks) {
      const parts = Array.isArray(data) ? data : [data];
      const o = emptyOutcome();
      for (const p of parts) for (const k of OUTCOME_KEYS) o[k] += p[week][k];
      rows.push({
        week, kind: "repo", name, repo: name, surface: "ci",
        invocations: null, sessions: null, blocked: null, overridden: null, outcome: o,
      });
    }
  }
  for (const { name, data } of fold(measured.filter((m) => m.hook), (m) => m.hook)) {
    for (const week of weeks) {
      const parts = Array.isArray(data) ? data : [data];
      const runs = parts.reduce((n, p) => n + p[week].runs, 0);
      const failures = parts.reduce((n, p) => n + p[week].failures, 0);
      rows.push({
        week, kind: "hook", name: HOOK_NAME, repo: name, surface: "ci",
        invocations: runs, sessions: null, blocked: failures, overridden: failures, outcome: null,
      });
    }
  }
  // `name` is the hook name on hook rows, so ties fall back to `repo`.
  return rows.sort((a, b) => compareRows(a, b) || (a.repo < b.repo ? -1 : a.repo > b.repo ? 1 : 0));
}

function runReport({ owners, api, nowMs, env, requireOwnerTokens, out }) {
  const log = out || console.log;
  const { weeks, fromMs } = windowFor(nowMs);
  const span = { weeks, fromMs, toMs: nowMs };
  const failures = [];
  const targets = [];

  for (const owner of owners) {
    if (requireOwnerTokens && !env[ownerTokenEnvName(owner)]) {
      failures.push("missing owner token");
      continue;
    }
    try {
      for (const r of api.listRepos(owner)) targets.push({ ...r, owner });
    } catch (e) {
      failures.push(`owner listing: ${describeError(e)}`);
    }
  }

  const measured = [];
  for (const target of targets) {
    try {
      measured.push({ repo: target.repo, private: target.private, ...measureRepo(target, api, span) });
    } catch (e) {
      failures.push(`repo: ${describeError(e)}`);
    }
  }

  const priv = measured.filter((m) => m.private).length;
  log(`impact-report: ${measured.length} repo(s) measured (${priv} private, folded), ${measured.filter((m) => m.hook).length} with agent-checks, ${failures.length} failure(s)`);
  for (const f of failures) log(`::error::impact-report: ${f}`);
  if (failures.length || !measured.length) return { exitCode: 1, doc: null };

  const doc = {
    generated_at: isoZ(nowMs),
    schema: 1,
    sources: [{ surface: "ci", scope: "github", from: isoZ(fromMs), to: isoZ(nowMs), repos_scanned: measured.length }],
    rows: buildRows(measured, weeks),
  };
  return { exitCode: 0, doc };
}

// True when `next` should replace `previous` on the results branch.
function shouldPublish(previous, next, nowMs) {
  if (!previous || !Array.isArray(previous.rows)) return true;
  if (JSON.stringify(previous.rows) !== JSON.stringify(next.rows)) return true;
  let age;
  try {
    age = nowMs - parseTime(previous.generated_at);
  } catch {
    return true;
  }
  return age >= REFRESH_DAYS * DAY_MS;
}

// ── CLI ────────────────────────────────────────────────────────────────────

function main() {
  const argv = process.argv.slice(2);
  const flags = { owners: (process.env.SYNC_OWNERS || "").split(/\s+/).filter(Boolean), requireOwnerTokens: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--out") flags.out = argv[++i];
    else if (a === "--previous") flags.previous = argv[++i];
    else if (a === "--require-owner-tokens") flags.requireOwnerTokens = true;
    else if (a === "--owners") flags.owners = (argv[++i] || "").split(/\s+/).filter(Boolean);
    else { console.error(`impact-report: unknown flag ${a}`); process.exit(2); }
  }
  if (!flags.out || !flags.owners.length) {
    console.error("impact-report: --out and SYNC_OWNERS (or --owners) are required");
    process.exit(2);
  }
  const nowMs = Date.now(); // the only clock read in this file
  const { exitCode, doc } = runReport({
    owners: flags.owners, api: buildApi(process.env), nowMs,
    env: process.env, requireOwnerTokens: flags.requireOwnerTokens,
  });
  if (exitCode !== 0) process.exit(exitCode);

  let previous = null;
  if (flags.previous && fs.existsSync(flags.previous)) {
    try { previous = JSON.parse(fs.readFileSync(flags.previous, "utf8")); } catch { previous = null; }
  }
  if (!shouldPublish(previous, doc, nowMs)) {
    console.log(`impact-report: rows unchanged and the published file is under ${REFRESH_DAYS} days old; leaving it`);
    return;
  }
  fs.writeFileSync(flags.out, JSON.stringify(doc, null, 2) + "\n");
  console.log(`impact-report: wrote ${doc.rows.length} row(s)`);
}

if (require.main === module) main();

module.exports = {
  WEEKS, REFRESH_DAYS, HOOK_NAME, HOOK_WORKFLOW_PATH, AGENT_LABEL,
  ApiError, describeError, ownerTokenEnvName,
  weekOf, windowFor, measureRepo, buildRows, runReport, shouldPublish, buildApi,
};
