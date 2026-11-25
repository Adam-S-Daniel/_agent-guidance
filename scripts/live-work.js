#!/usr/bin/env node
"use strict";
/*
 * live-work.js — says whether someone is working an issue right now, so the
 * fleet worker's issue pass leaves it alone.
 *
 * Why it exists: an interactive project thread works an issue in an open PR on
 * a `claude/project-thread-*` branch and never sets `agent-working`, so the
 * issue pass's label-based skip rules cannot see it. The fleet worker claimed
 * adam-agentskills#198 (agent-ready off, agent-working on, then agent-blocked)
 * while a project thread was building the fix (2026-11-25). The signals here
 * do not depend on any label (docs/reference/agent-issue-worker.md, "Each run").
 *
 * An item is `live` when either:
 *   open PR   a `cross-referenced` timeline event whose source is an OPEN pull
 *             request other than the item itself;
 *   activity  an event of type commented, labeled, unlabeled, cross-referenced,
 *             referenced, closed, reopened, assigned or renamed happened within
 *             the last --minutes (default 60). Events within 5 minutes after
 *             the issue's own creation do not count: a filer labeling a new
 *             issue is not live work.
 *
 * Usage:
 *   node scripts/live-work.js [--minutes 60] [--now <ISO>] <owner>/<repo>#<n> ...
 * Prints {"items":[{"item","live","reasons":[...]}]} on stdout. Exit 0 on
 * success even when items are live, 1 on a usage or API error (stderr carries
 * a status and an error type, never a response body). Callers fail closed on 1.
 *
 * GitHub access is REST only, through `gh api` with an argument array (the
 * cloud refuses GraphQL). Tests inject a fake runner: test/test-live-work.js.
 */

const { execFileSync } = require("node:child_process");

const ACTIVITY = new Set([
  "commented", "labeled", "unlabeled", "cross-referenced", "referenced",
  "closed", "reopened", "assigned", "renamed",
]);
const FILER_GRACE_MS = 5 * 60 * 1000;
const PAGE = 100;

function defaultGh(args) {
  return execFileSync("gh", args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], maxBuffer: 64 * 1024 * 1024 });
}

function ghJson(gh, args) {
  const out = gh(args);
  return out.trim() ? JSON.parse(out) : null;
}

function parseItem(s) {
  const m = /^([^/\s#]+)\/([^/\s#]+)#([0-9]+)$/.exec(s);
  if (!m) throw new Error(`bad item ${s} (want <owner>/<repo>#<n>)`);
  return { repo: `${m[1]}/${m[2]}`, number: Number(m[3]) };
}

function timelineOf(gh, repo, number) {
  const all = [];
  for (let page = 1; ; page++) {
    const rows = ghJson(gh, ["api", `repos/${repo}/issues/${number}/timeline?per_page=${PAGE}&page=${page}`]) || [];
    all.push(...rows);
    if (rows.length < PAGE) return all;
  }
}

function sourceRepo(src) {
  if (src.repository && src.repository.full_name) return src.repository.full_name;
  const m = /github\.com\/([^/]+\/[^/]+)\/(?:pull|issues)\//.exec(String(src.html_url || ""));
  return m ? m[1] : "";
}

// Pure decision. issue: the issue object; timeline: its events; now: a Date.
function classify(item, issue, timeline, now, minutes) {
  const reasons = [];
  const created = Date.parse(issue.created_at);
  const since = now.getTime() - minutes * 60 * 1000;
  const self = `${item.repo}#${item.number}`.toLowerCase();

  const seen = new Set();
  for (const ev of timeline) {
    if (ev.event !== "cross-referenced") continue;
    const src = ev.source && ev.source.issue;
    if (!src || !src.pull_request || src.state !== "open") continue;
    const key = `${sourceRepo(src)}#${src.number}`;
    if (key.toLowerCase() === self || seen.has(key)) continue;
    seen.add(key);
    reasons.push(`open PR ${key}`);
  }

  for (const ev of timeline) {
    if (!ACTIVITY.has(ev.event)) continue;
    const times = [ev.created_at, ev.event === "commented" ? ev.updated_at : null]
      .filter(Boolean).map((t) => Date.parse(t)).filter((t) => !Number.isNaN(t));
    if (!times.length) continue;
    const at = Math.max(...times);
    if (at < since || at > now.getTime()) continue;
    if (Number.isFinite(created) && at <= created + FILER_GRACE_MS) continue;
    reasons.push(`activity ${ev.event} at ${new Date(at).toISOString()}`);
  }
  return { item: `${item.repo}#${item.number}`, live: reasons.length > 0, reasons };
}

function run({ gh = defaultGh, items, now, minutes = 60 }) {
  return {
    items: items.map((s) => {
      const item = parseItem(s);
      const issue = ghJson(gh, ["api", `repos/${item.repo}/issues/${item.number}`]);
      return classify(item, issue, timelineOf(gh, item.repo, item.number), now, minutes);
    }),
  };
}

function parseArgs(argv) {
  const opts = { minutes: 60, now: null, items: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--minutes") opts.minutes = Number(argv[++i]);
    else if (a === "--now") opts.now = argv[++i];
    else if (a.startsWith("--")) throw new Error(`unknown argument ${a}`);
    else { parseItem(a); opts.items.push(a); }
  }
  if (!opts.items.length) throw new Error("no items: pass <owner>/<repo>#<n> ...");
  if (!Number.isFinite(opts.minutes) || opts.minutes < 0) throw new Error("--minutes is not a number");
  const now = opts.now ? new Date(opts.now) : new Date();
  if (Number.isNaN(now.getTime())) throw new Error("--now is not a date");
  opts.now = now;
  return opts;
}

function main() {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2));
  } catch (e) {
    console.error(`live-work: ${e.message}`);
    return 1;
  }
  try {
    process.stdout.write(JSON.stringify(run(opts), null, 2) + "\n");
    return 0;
  } catch (e) {
    // Never echo an HTTP body: report the exit status and the error type only.
    console.error(`live-work: failed (${e.name}${e.status != null ? `, exit ${e.status}` : ""})`);
    return 1;
  }
}

module.exports = { classify, run, parseArgs, parseItem, timelineOf };

if (require.main === module) process.exitCode = main();
