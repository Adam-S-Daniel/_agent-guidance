#!/usr/bin/env node
"use strict";
/*
 * stale-claims.js — finds open issues the fleet worker claimed
 * (`agent-working`) whose work has stopped, and hands back the ones a merged
 * `Refs` PR left half done.
 *
 * Why it exists: the issue worker leaves `agent-working` on an issue while its
 * PR is open (docs/reference/agent-issue-worker.md, step 6). A `Refs` PR fixes
 * only some of an issue's findings, and the swap back to `agent-ready` that
 * lets a later issue pass take the rest happened only inside the fleet
 * worker's own merge (docs/routines/fleet-worker.md, steps 2b and 2d). A Refs
 * PR merged any other way (by the owner, by a project thread, by a repo's own
 * auto-merge) left the issue labeled `agent-working` with nothing open, and
 * the issue pass skips such an issue forever: wsl-automation#120 sat that way
 * from 2026-11-18. This makes the swap depend on the merge, not on who merged.
 *
 * Classification of each open `agent-working` issue in a fleet repo (classify):
 *   hold         the issue or a linked PR carries `on-hold`: report only.
 *   sweep-owned  a label in SWEEP_OWNED: the issue's filer closes it when its
 *                need is met (skills-evals' scripts/work_item_issues.py), so
 *                its Refs PRs are not partial work. Skipped.
 *   in-progress  a linked PR is open.
 *   requeue      no linked PR is open, and a PR that says `Refs` for this
 *                issue merged after the issue's latest `agent-working` label:
 *                swap `agent-working` for `agent-ready` and comment the
 *                merged PRs, so a later issue pass does only what is left
 *                (agent-issue-worker.md step 3). Measuring from the latest
 *                claim means a requeued issue that strands again without a
 *                new merged PR is reported, never requeued twice for one PR.
 *   closes-open  a merged PR says `Closes` (or fixes/resolves) for this issue
 *                and the issue is still open: report only.
 *   stale        none of the above and the claim is over 24 hours old:
 *                report only (agent-issue-worker.md, "Stale claims").
 *   fresh        none of the above, claimed within 24 hours.
 *
 * Usage:
 *   SYNC_OWNERS="<owner> <owner>" node scripts/stale-claims.js [--apply] [--now <ISO>]
 *   node scripts/stale-claims.js --owners "<owner> <owner>" ...
 * Owners come from SYNC_OWNERS or --owners, never a default. Fleet repos are
 * repos.yml's `cron_coverage.fleet`. Without --apply nothing is written.
 * Prints {"issues":[{repo,number,title,action,reason,prs,applied}]} on stdout.
 * Exit 0 on success, 1 on a usage or API error (stderr carries a status and an
 * error type, never a response body).
 *
 * GitHub access is REST only, through `gh api` with an argument array (the
 * cloud refuses GraphQL). Tests inject a fake runner: test/test-stale-claims.js.
 */

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const CLAIM = "agent-working";
const READY = "agent-ready";
const HOLD = "on-hold";
const SWEEP_OWNED = ["needs-fixtures", "needs-harder-fixtures"];
const STALE_MS = 24 * 60 * 60 * 1000;
const MARKER = "<!-- fleet-worker: refs-requeued -->";
const SIGNATURE = "<sub>🤖 Written by Claude Code on behalf of @Adam-S-Daniel</sub>";

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// The PR body's references to issue <number> in <issueRepo>, found as
// lexical tokens: `<keyword> <owner>/<repo>#<n>`, or `<keyword> #<n>` when the
// PR lives in the issue's own repo. Returns {refs, closes}.
function references(body, prRepo, issueRepo, number) {
  const text = String(body || "");
  const target = `(?:${escapeRe(issueRepo)}#${number}` +
    (prRepo.toLowerCase() === issueRepo.toLowerCase() ? `|#${number}` : "") + ")(?![0-9])";
  const closes = new RegExp(`\\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\\s+${target}`, "i").test(text);
  const refs = new RegExp(`\\brefs?:?\\s+${target}`, "i").test(text);
  return { refs, closes };
}

function labelNames(labels) {
  return (labels || []).map((l) => (typeof l === "string" ? l : l.name));
}

// Linked PRs from an issue's timeline: cross-referenced events whose source is
// a pull request. Deduped on repo and number, keeping the last copy seen.
function linkedPrs(timeline) {
  const byKey = new Map();
  for (const ev of timeline || []) {
    if (ev.event !== "cross-referenced") continue;
    const src = ev.source && ev.source.issue;
    if (!src || !src.pull_request) continue;
    const repo = (src.repository && src.repository.full_name) ||
      String(src.repository_url || "").replace(/^.*\/repos\//, "");
    byKey.set(`${repo}#${src.number}`, {
      repo,
      number: src.number,
      state: src.state,
      merged_at: src.pull_request.merged_at || null,
      body: src.body || "",
      labels: labelNames(src.labels),
      url: src.html_url,
    });
  }
  return [...byKey.values()];
}

function claimedAt(timeline) {
  let at = null;
  for (const ev of timeline || []) {
    if (ev.event === "labeled" && ev.label && ev.label.name === CLAIM) at = ev.created_at;
  }
  return at;
}

// Pure decision for one issue. issue: {repo, number, labels, title};
// timeline: GET /repos/{o}/{r}/issues/{n}/timeline; now: a Date.
function classify(issue, timeline, now) {
  const labels = labelNames(issue.labels);
  const prs = linkedPrs(timeline);
  const claim = claimedAt(timeline);
  const claimMs = claim ? Date.parse(claim) : 0;
  const brief = (p) => ({ repo: p.repo, number: p.number, url: p.url });

  if (labels.includes(HOLD) || prs.some((p) => p.labels.includes(HOLD))) {
    return { action: "hold", reason: "on-hold on the issue or a linked PR", prs: [] };
  }
  const owned = SWEEP_OWNED.find((l) => labels.includes(l));
  if (owned) return { action: "sweep-owned", reason: `label ${owned}: its filer closes it`, prs: [] };

  const open = prs.filter((p) => p.state === "open");
  if (open.length) return { action: "in-progress", reason: "a linked PR is open", prs: open.map(brief) };

  const merged = prs.filter((p) => p.merged_at);
  const tagged = merged.map((p) => ({ p, ...references(p.body, p.repo, issue.repo, issue.number) }));
  const refsAfterClaim = tagged.filter((t) => t.refs && !t.closes && Date.parse(t.p.merged_at) > claimMs);
  if (refsAfterClaim.length) {
    return {
      action: "requeue",
      reason: "a Refs PR merged after the claim and none is open",
      prs: refsAfterClaim.map((t) => brief(t.p)),
    };
  }
  const closers = tagged.filter((t) => t.closes);
  if (closers.length) {
    return { action: "closes-open", reason: "a merged PR closes it but it is open", prs: closers.map((t) => brief(t.p)) };
  }
  if (now.getTime() - claimMs > STALE_MS) {
    return { action: "stale", reason: "claimed over 24 hours ago with no open PR", prs: merged.map(brief) };
  }
  return { action: "fresh", reason: "claimed within 24 hours", prs: [] };
}

function requeueComment(prs) {
  const list = prs.map((p) => `- ${p.url || `${p.repo}#${p.number}`}`).join("\n");
  return `${MARKER}\nRequeued by the fleet worker: these \`Refs\` PRs merged and no PR for this issue is open, so the rest of its findings go back to the issue pass (\`agent-working\` swapped for \`agent-ready\`). The next pass does only what no merged PR fixed, and closes the issue as completed if nothing is left.\n\n${list}\n\n${SIGNATURE}`;
}

function defaultGh(args) {
  return execFileSync("gh", args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], maxBuffer: 64 * 1024 * 1024 });
}

function ghJson(gh, args) {
  const out = gh(args);
  return out.trim() ? JSON.parse(out) : null;
}

function loadFleet(root) {
  const YAML = require("yaml");
  const doc = YAML.parse(fs.readFileSync(path.join(root, "repos.yml"), "utf8"));
  const fleet = doc && doc.cron_coverage && doc.cron_coverage.fleet;
  if (!Array.isArray(fleet) || !fleet.length) throw new Error("repos.yml has no cron_coverage.fleet");
  return new Set(fleet.map((n) => String(n).toLowerCase()));
}

function listClaimed(gh, owners) {
  const q = [`label:${CLAIM}`, "state:open", "is:issue", ...owners.map((o) => `user:${o}`)].join(" ");
  const pages = ghJson(gh, ["api", "-X", "GET", "search/issues", "-f", `q=${q}`, "-f", "per_page=100", "--paginate", "--slurp"]) || [];
  return pages.flatMap((p) => p.items || []).map((it) => ({
    repo: String(it.repository_url).replace(/^.*\/repos\//, ""),
    number: it.number,
    title: it.title,
    labels: labelNames(it.labels),
  }));
}

function timelineOf(gh, repo, number) {
  const pages = ghJson(gh, ["api", `repos/${repo}/issues/${number}/timeline?per_page=100`, "--paginate", "--slurp"]) || [];
  return pages.flat();
}

// Re-reads the issue's labels just before writing; refuses on on-hold or a
// claim that is already gone (another run got there first).
function applyRequeue(gh, issue, prs) {
  const fresh = ghJson(gh, ["api", `repos/${issue.repo}/issues/${issue.number}`]);
  const labels = labelNames(fresh.labels);
  if (fresh.state !== "open" || labels.includes(HOLD) || !labels.includes(CLAIM)) return false;
  gh(["api", "-X", "DELETE", `repos/${issue.repo}/issues/${issue.number}/labels/${CLAIM}`]);
  gh(["api", "-X", "POST", `repos/${issue.repo}/issues/${issue.number}/labels`, "-f", `labels[]=${READY}`]);
  gh(["api", "-X", "POST", `repos/${issue.repo}/issues/${issue.number}/comments`, "-f", `body=${requeueComment(prs)}`]);
  return true;
}

function run({ gh = defaultGh, owners, fleet, now, apply }) {
  const issues = listClaimed(gh, owners)
    .filter((i) => fleet.has(i.repo.split("/")[1].toLowerCase()))
    .sort((a, b) => a.repo.localeCompare(b.repo) || a.number - b.number);
  const results = [];
  for (const issue of issues) {
    const verdict = classify(issue, timelineOf(gh, issue.repo, issue.number), now);
    const row = { repo: issue.repo, number: issue.number, title: issue.title, ...verdict, applied: false };
    if (apply && verdict.action === "requeue") row.applied = applyRequeue(gh, issue, verdict.prs);
    results.push(row);
  }
  return { issues: results };
}

function parseArgs(argv, env) {
  const opts = { apply: false, now: null, owners: (env.SYNC_OWNERS || "").split(/\s+/).filter(Boolean) };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--apply") opts.apply = true;
    else if (a === "--now") opts.now = argv[++i];
    else if (a === "--owners") opts.owners = String(argv[++i] || "").split(/\s+/).filter(Boolean);
    else throw new Error(`unknown argument ${a}`);
  }
  if (!opts.owners.length) throw new Error("no owners: set SYNC_OWNERS or pass --owners");
  const now = opts.now ? new Date(opts.now) : new Date();
  if (Number.isNaN(now.getTime())) throw new Error("--now is not a date");
  opts.now = now;
  return opts;
}

function main() {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2), process.env);
  } catch (e) {
    console.error(`stale-claims: ${e.message}`);
    return 1;
  }
  try {
    const fleet = loadFleet(path.join(__dirname, ".."));
    const out = run({ owners: opts.owners, fleet, now: opts.now, apply: opts.apply });
    process.stdout.write(JSON.stringify(out, null, 2) + "\n");
    return 0;
  } catch (e) {
    // Never echo an HTTP body: report the exit status and the error type only.
    console.error(`stale-claims: failed (${e.name}${e.status != null ? `, exit ${e.status}` : ""})`);
    return 1;
  }
}

module.exports = { classify, references, linkedPrs, claimedAt, requeueComment, run, parseArgs, MARKER, SWEEP_OWNED };

if (require.main === module) process.exitCode = main();
