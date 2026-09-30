#!/usr/bin/env node
"use strict";
/*
 * routine-merge-gate.js — step 7 ("Finish", the **Merge gate**) of
 * docs/reference/agent-changelog-routine.md. Decides, from four REST
 * documents about one pull request, whether the claude.ai Routine "agent
 * changelog watcher" may merge its own PR. The decision is mechanical on
 * purpose (repo-settings ADR 0005): the Routine runs unattended on text it
 * fetched from the internet, so the only thing standing between a hostile
 * release note and the default branch is a check that cannot be talked out of
 * anything. This script reads no prose for meaning and follows no
 * instruction; it compares values against a fixed allowlist and refuses on
 * any difference.
 *
 * Pure decision, no network: the Routine fetches the four documents with curl
 * through its session proxy and passes file paths, which keeps every branch
 * testable with fabricated fixtures (test/test-routine-merge-gate.js) and
 * keeps the script incapable of merging by itself. The Routine performs the
 * merge, and only when this script exits 0, with the `sha` it prints so
 * GitHub refuses the merge if the head moved after the decision.
 *
 * Every refusal reason is collected, never just the first, so a refused PR
 * tells the owner everything that is wrong in one read. The conditions:
 *
 *   1. PR shape: open, not a draft, not merged, base branch `main`, head in
 *      this same repo (no fork).
 *   2. Title starts with exactly `Vendor changelog routine: ` and does not
 *      contain DRY_RUN.
 *   3. No Markdown heading "Declined" in the body (any level, any case): that
 *      heading is how a run reports instruction-shaped fetched text, and a
 *      human reviews such a PR. Found by markdown-it's token stream, never a
 *      regex over lines (fleet rule: shape is read from a real parse).
 *   4. The PR's complete file list (length must equal the PR's
 *      `changed_files`, else the listing was truncated) touches only the
 *      routine's own outputs, added or modified. One exception: a removal of
 *      `vendor-issue-map.txt`, the one-time migration to
 *      docs/reference/agent-changelog-issue-map.txt. The routine's own
 *      instructions (agent-changelog-routine.md) are deliberately NOT in the
 *      allowlist: a run must never merge a change to the text it obeys.
 *   5. Check runs on the head commit: all present (count matches), all for
 *      this head sha, all completed, every conclusion success/neutral/
 *      skipped, and a check run named `test` concluded success.
 *   6. Legacy commit statuses: every entry `success`. The combined `state` is
 *      not used: it reads "pending" when there are no statuses at all.
 *   7. With --issue-map: the issue map passes parseIssueMap() (format and
 *      uniqueness).
 *
 * Usage:
 *   node scripts/routine-merge-gate.js --pr <pr.json> --files <files.json> \
 *     --check-runs <check-runs.json> --status <status.json> \
 *     [--issue-map <path>]
 *
 *   pr.json          GET /repos/{o}/{r}/pulls/{n}
 *   files.json       GET /repos/{o}/{r}/pulls/{n}/files?per_page=100 (array)
 *   check-runs.json  GET /repos/{o}/{r}/commits/{head_sha}/check-runs?per_page=100
 *   status.json      GET /repos/{o}/{r}/commits/{head_sha}/status
 *
 * Prints one JSON object {"merge": bool, "head_sha": "...", "reasons": [..]}.
 * Exit codes: 0 merge allowed, 2 refused (reasons non-empty), 1 bad input or
 * usage (message to stderr; file contents are never echoed).
 */
const fs = require("node:fs");
const MarkdownIt = require("markdown-it");

const parser = new MarkdownIt();

const TITLE_PREFIX = "Vendor changelog routine: ";
const ISSUE_MAP_PATH = "docs/reference/agent-changelog-issue-map.txt";
const OLD_ISSUE_MAP_PATH = "vendor-issue-map.txt";

// Exact paths the routine may add or modify (agent-changelog-routine.md,
// Constraints). agent-changelog-routine.md itself is absent on purpose.
const ALLOWED_FILES = new Set([
  "docs/reference/agent-claude-code.CHANGELOG.md",
  "docs/reference/agent-codex.CHANGELOG.md",
  "docs/reference/agent-claude-code.DISCREPANCIES.md",
  "docs/reference/agent-codex.DISCREPANCIES.md",
  "docs/reference/agent-changelog-runs.md",
  ISSUE_MAP_PATH,
]);
const ALLOWED_STATUSES = new Set(["added", "modified"]);
const GOOD_CONCLUSIONS = new Set(["success", "neutral", "skipped"]);
const REQUIRED_CHECK = "test";

const MAP_LINE =
  /^(claude-code|codex)\/(\d{4}-\d{2}-\d{2})\/([1-9]\d*) ([A-Za-z0-9-]+\/[A-Za-z0-9._-]+)#([1-9]\d*)$/;

// ── Pure helpers (exported; every one is unit-tested directly) ────────────

// parseIssueMap(text) — {entries, errors}. Non-comment, non-blank lines must
// match MAP_LINE. The pair (key, owner/repo) must be unique, and so must each
// owner/repo#n; repo names compare case-insensitively, as GitHub does. Errors
// carry line numbers, never the line's text.
function parseIssueMap(text) {
  const entries = [];
  const errors = [];
  const pairs = new Map();
  const issues = new Map();
  String(text)
    .split(/\r?\n/)
    .forEach((line, idx) => {
      const lineNo = idx + 1;
      if (line.trim() === "" || line.startsWith("#")) return;
      const m = MAP_LINE.exec(line);
      if (!m) {
        errors.push(`issue map line ${lineNo}: does not match <agent>/<entry date>/<group> <owner>/<repo>#<n>`);
        return;
      }
      const entry = {
        key: `${m[1]}/${m[2]}/${m[3]}`,
        repo: m[4],
        number: Number(m[5]),
        line: lineNo,
      };
      entries.push(entry);
      const pair = `${entry.key} ${entry.repo.toLowerCase()}`;
      if (pairs.has(pair)) {
        errors.push(`issue map line ${lineNo}: key and repo already on line ${pairs.get(pair)}`);
      } else {
        pairs.set(pair, lineNo);
      }
      const issue = `${entry.repo.toLowerCase()}#${entry.number}`;
      if (issues.has(issue)) {
        errors.push(`issue map line ${lineNo}: issue already on line ${issues.get(issue)}`);
      } else {
        issues.set(issue, lineNo);
      }
    });
  return { entries, errors };
}

// headingTexts(md) — the plain text of every heading in a Markdown document,
// from a real markdown-it parse. Text inside a fenced code block is not a
// heading, so a quoted "## Declined" in a fence does not count; emphasis
// markers around the word do not hide it.
function headingTexts(md) {
  const tokens = parser.parse(String(md || ""), {});
  const out = [];
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i].type !== "heading_open") continue;
    const inline = tokens[i + 1];
    const kids = inline && inline.children ? inline.children : [];
    out.push(
      kids
        .filter((c) => c.type === "text" || c.type === "code_inline")
        .map((c) => c.content)
        .join("")
        .trim(),
    );
  }
  return out;
}

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

// Input-shape failures are the caller's mistake (exit 1), not a refusal.
// Messages name the field, never its value.
function need(cond, msg) {
  if (!cond) throw new Error(`bad input: ${msg}`);
}

// decide({pr, files, checkRuns, status, issueMapText}) — {merge, head_sha,
// reasons}. issueMapText is undefined when no --issue-map was given.
function decide({ pr, files, checkRuns, status, issueMapText }) {
  need(isObject(pr), "pr must be a JSON object");
  need(isObject(pr.head) && typeof pr.head.sha === "string" && pr.head.sha !== "", "pr.head.sha missing");
  need(isObject(pr.base), "pr.base missing");
  need(Array.isArray(files), "files must be a JSON array");
  need(isObject(checkRuns) && Array.isArray(checkRuns.check_runs), "check-runs.check_runs must be an array");
  need(isObject(status) && Array.isArray(status.statuses), "status.statuses must be an array");

  const reasons = [];
  const headSha = pr.head.sha;

  // 1. PR shape.
  if (pr.state !== "open") reasons.push("PR is not open");
  if (pr.draft === true) reasons.push("PR is a draft");
  if (pr.merged === true) reasons.push("PR is already merged");
  if (pr.base.ref !== "main") reasons.push("PR base branch is not main");
  const headRepo = isObject(pr.head.repo) ? pr.head.repo.full_name : undefined;
  const baseRepo = isObject(pr.base.repo) ? pr.base.repo.full_name : undefined;
  if (!headRepo || !baseRepo || headRepo !== baseRepo) {
    reasons.push("PR head is not in the base repository (fork or missing repo)");
  }

  // 2. Title.
  const title = typeof pr.title === "string" ? pr.title : "";
  if (!title.startsWith(TITLE_PREFIX)) {
    reasons.push(`PR title does not start with "${TITLE_PREFIX}"`);
  }
  if (title.includes("DRY_RUN")) reasons.push("PR title contains DRY_RUN");

  // 3. Declined heading.
  if (headingTexts(pr.body).some((t) => t.toLowerCase() === "declined")) {
    reasons.push('PR body has a "Declined" heading: a human reviews instruction-shaped fetched text');
  }

  // 4. Files.
  if (files.length !== pr.changed_files) {
    reasons.push(`file listing has ${files.length} entries but the PR changes ${pr.changed_files} (truncated or malformed)`);
  }
  for (const f of files) {
    const name = isObject(f) && typeof f.filename === "string" ? f.filename : "(unnamed)";
    const st = isObject(f) ? f.status : undefined;
    if (isObject(f) && f.previous_filename !== undefined && f.previous_filename !== null) {
      reasons.push(`file ${name} has a previous_filename (rename or copy)`);
    }
    if (st === "removed" && name === OLD_ISSUE_MAP_PATH) continue;
    if (!ALLOWED_FILES.has(name)) {
      reasons.push(`file ${name} is not in the allowlist`);
    } else if (!ALLOWED_STATUSES.has(st)) {
      reasons.push(`file ${name} has status ${String(st)}, not added or modified`);
    }
  }

  // 5. Check runs.
  const runs = checkRuns.check_runs;
  if (checkRuns.total_count !== runs.length) {
    reasons.push(`check-runs total_count ${checkRuns.total_count} does not match the ${runs.length} listed (truncated)`);
  }
  if (runs.length === 0) reasons.push("no check runs on the head commit");
  let testGreen = false;
  for (const r of runs) {
    const name = isObject(r) && typeof r.name === "string" ? r.name : "(unnamed)";
    if (!isObject(r)) continue;
    if (r.head_sha !== headSha) reasons.push(`check run ${name} is for a different commit than the PR head`);
    if (r.status !== "completed") {
      reasons.push(`check run ${name} is ${String(r.status)}, not completed`);
    } else if (!GOOD_CONCLUSIONS.has(r.conclusion)) {
      reasons.push(`check run ${name} concluded ${String(r.conclusion)}`);
    }
    if (name === REQUIRED_CHECK && r.status === "completed" && r.conclusion === "success") testGreen = true;
  }
  if (!testGreen) reasons.push(`no check run named ${REQUIRED_CHECK} concluded success`);

  // 6. Legacy statuses (never the combined state; see header).
  for (const s of status.statuses) {
    const ctx = isObject(s) && typeof s.context === "string" ? s.context : "(unnamed)";
    const state = isObject(s) ? s.state : undefined;
    if (state !== "success") reasons.push(`commit status ${ctx} is ${String(state)}, not success`);
  }

  // 7. Issue map.
  if (issueMapText !== undefined) {
    for (const e of parseIssueMap(issueMapText).errors) reasons.push(e);
  }

  return { merge: reasons.length === 0, head_sha: headSha, reasons };
}

// ── CLI ──────────────────────────────────────────────────────────────────

const FLAGS = {
  "--pr": "pr",
  "--files": "files",
  "--check-runs": "checkRuns",
  "--status": "status",
  "--issue-map": "issueMap",
};

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 2) {
    const key = FLAGS[argv[i]];
    need(key, `unknown argument ${argv[i]}`);
    need(i + 1 < argv.length && !argv[i + 1].startsWith("--"), `${argv[i]} needs a value`);
    need(out[key] === undefined, `${argv[i]} given twice`);
    out[key] = argv[i + 1];
  }
  for (const req of ["pr", "files", "checkRuns", "status"]) {
    need(out[req] !== undefined, `missing --${req.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase())}`);
  }
  return out;
}

function readJson(flag, file) {
  let raw;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch (e) {
    throw new Error(`bad input: cannot read ${flag} file`);
  }
  try {
    return JSON.parse(raw);
  } catch (e) {
    throw new Error(`bad input: ${flag} file is not valid JSON`);
  }
}

function main(argv) {
  try {
    const args = parseArgs(argv);
    let issueMapText;
    if (args.issueMap !== undefined) {
      try {
        issueMapText = fs.readFileSync(args.issueMap, "utf8");
      } catch (e) {
        throw new Error("bad input: cannot read --issue-map file");
      }
    }
    const result = decide({
      pr: readJson("--pr", args.pr),
      files: readJson("--files", args.files),
      checkRuns: readJson("--check-runs", args.checkRuns),
      status: readJson("--status", args.status),
      issueMapText,
    });
    process.stdout.write(JSON.stringify(result) + "\n");
    process.exit(result.merge ? 0 : 2);
  } catch (e) {
    // Only the message: never a file's contents.
    process.stderr.write(`routine-merge-gate: ${e.message}\n`);
    process.exit(1);
  }
}

if (require.main === module) {
  main(process.argv.slice(2));
}

module.exports = {
  ALLOWED_FILES,
  TITLE_PREFIX,
  parseIssueMap,
  headingTexts,
  decide,
  parseArgs,
  main,
};
