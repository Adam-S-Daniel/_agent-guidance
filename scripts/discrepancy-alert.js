#!/usr/bin/env node
"use strict";
/*
 * discrepancy-alert.js — step 5 ("Alert (automatic)") of
 * docs/reference/agent-discrepancy-process.md. Opens one issue in THIS repo
 * for every new entry in an agent-<agent>.DISCREPANCIES.md file and every new
 * vendor-issue-drafts/<agent>/*.md draft that a push (or a manual
 * workflow_dispatch) brings in, so the repo owner is notified and has a
 * checklist to work from — without ever putting a vendor issue link where
 * GitHub would turn it into a backlink on the vendor's public issue (see that
 * doc's step 2 "Links in this repo's files are fine... don't put vendor
 * issue links in ... GitHub issues or comments here").
 *
 * Idempotent by exact issue TITLE among every issue carrying the alert label
 * (`gh issue list --label <label> --state all`, any state), never a text
 * search: the label list involves no search index, so unlike a text query it
 * cannot lag a newly created issue by the minute or more GitHub's search
 * index sometimes does, and a title containing `:` (every alert title does:
 * "Vendor discrepancy: Claude Code — …") cannot be misparsed as a query
 * qualifier, because no query is ever run. Known edge case: an alert whose
 * label was removed by hand is no longer seen by the label list, so it would
 * be re-filed on the next matching run. There is no hidden evidence marker
 * here (contrast scripts/dependabot-config-health.js) — a discrepancy alert is a one-shot
 * "look at this" notice the owner closes by hand once step 6 is done, not a
 * long-lived tracking issue that accumulates comments.
 *
 * Every entry/draft's own text — which may itself contain a vendor issue URL
 * or a bare "#123" — is quoted ONLY inside a fenced code block in the issue
 * body, never in prose, so GitHub never renders it as a live link or
 * cross-reference. See fence()/renderEntryAlert()/renderDraftAlert().
 *
 * Usage:
 *   node scripts/discrepancy-alert.js --repo Adam-S-Daniel/_agent-guidance \
 *     --assignee Adam-S-Daniel --before <sha> --after <sha>
 *   node scripts/discrepancy-alert.js --repo Adam-S-Daniel/_agent-guidance --dry-run
 *
 * Exit codes: 0 on success (including nothing to do), 1 on any error. Never
 * prints an issue body or environment on error — see main().
 */
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const MarkdownIt = require("markdown-it");

const parser = new MarkdownIt();

const ENTRY_FILE_PATTERN = "docs/reference/agent-*.DISCREPANCIES.md";
const DRAFTS_DIR = "docs/reference/vendor-issue-drafts/";
const DEFAULT_LABEL = "vendor-discrepancy";
const TITLE_MAX = 200;

// ── Pure helpers (exported; every one is unit-tested directly) ────────────

// parseEntries(md) — every `### ` entry under the `## Entries` heading, in
// document order, via a REAL markdown-it token stream (fleet rule: never
// regex/line-scan for document structure). A heading inside a fenced code
// block (the template every DISCREPANCIES.md carries) never produces a
// heading token in the first place, so it is ignored for free.
function parseEntries(md) {
  const lines = md.split("\n");
  const tokens = parser.parse(md, {});

  let entriesIdx = -1;
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.type === "heading_open" && t.tag === "h2") {
      const inline = tokens[i + 1];
      if (inline && inline.content.trim() === "Entries") {
        entriesIdx = i;
        break;
      }
    }
  }
  if (entriesIdx === -1) return [];

  const h3s = [];
  let stopLine = lines.length;
  for (let i = entriesIdx + 1; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.type === "heading_open" && t.tag === "h2") {
      stopLine = t.map[0];
      break;
    }
    if (t.type === "heading_open" && t.tag === "h3") {
      const inline = tokens[i + 1];
      h3s.push({ startLine: t.map[0], heading: (inline ? inline.content : "").trim() });
    }
  }

  return h3s.map((h, i) => {
    const rawEnd = i + 1 < h3s.length ? h3s[i + 1].startLine : stopLine;
    let end = rawEnd;
    while (end > h.startLine && lines[end - 1].trim() === "") end--;
    return { heading: h.heading, source: lines.slice(h.startLine, end).join("\n") };
  });
}

// newEntries(beforeMd, afterMd) — afterMd's entries whose heading did not
// exist in beforeMd. Editing an existing entry's body is not new; a removed
// entry produces nothing (we only ever walk afterMd's own entries).
function newEntries(beforeMd, afterMd) {
  const beforeHeadings = new Set(parseEntries(beforeMd || "").map((e) => e.heading));
  return parseEntries(afterMd).filter((e) => !beforeHeadings.has(e.heading));
}

// parseDraft(md, filePath) — the draft's title (its first h1, trimmed) and
// its full source text. Falls back to the file's basename (sans .md) when
// there is no h1 at all.
function parseDraft(md, filePath) {
  const tokens = parser.parse(md, {});
  let title = null;
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.type === "heading_open" && t.tag === "h1") {
      const inline = tokens[i + 1];
      title = (inline ? inline.content : "").trim();
      break;
    }
  }
  if (!title) title = path.basename(filePath, ".md");
  return { title, source: md.replace(/\s+$/, "") };
}

// agentOf(filePath) — the agent slug a DISCREPANCIES.md file or a
// vendor-issue-drafts/<agent>/ file belongs to, or null for anything else.
function agentOf(filePath) {
  const p = String(filePath).replace(/\\/g, "/");
  let m = /^docs\/reference\/agent-([^/]+)\.DISCREPANCIES\.md$/.exec(p);
  if (m) return m[1];
  m = /^docs\/reference\/vendor-issue-drafts\/([^/]+)\/[^/]+\.md$/.exec(p);
  if (m) return m[1];
  return null;
}

function displayName(slug) {
  if (slug === "claude-code") return "Claude Code";
  if (slug === "codex") return "Codex";
  return slug;
}

// fence(text) — wrap `text` in a backtick fence with info string `text`,
// long enough that no run of backticks inside `text` can close it early.
function fence(text) {
  const runs = text.match(/`+/g) || [];
  const maxRun = runs.length ? Math.max(...runs.map((r) => r.length)) : 0;
  const fenceLen = Math.max(3, maxRun + 1);
  const bt = "`".repeat(fenceLen);
  return `${bt}text\n${text}\n${bt}`;
}

function truncateTitle(title) {
  if (title.length <= TITLE_MAX) return title;
  return `${title.slice(0, TITLE_MAX - 1)}…`;
}

function permalink(repo, sha, filePath) {
  return `https://github.com/${repo}/blob/${sha}/${filePath}`;
}

function footer(sha) {
  return `_Opened by \`.github/workflows/vendor-discrepancy-alert.yml\` at ${sha.slice(0, 7)}._`;
}

// renderEntryAlert({ agent, heading, source, repo, sha, path }) — the issue
// for a newly recorded discrepancy entry. The entry's own text (which may
// name a vendor issue URL or a bare #N) appears ONLY inside fence(source) —
// never in prose — so GitHub never turns it into a live link or a
// cross-repo/cross-issue backlink.
function renderEntryAlert({ agent, heading, source, repo, sha, path: filePath }) {
  const name = displayName(agent);
  const title = truncateTitle(`Vendor discrepancy: ${name} — ${heading}`);
  const lines = [
    `A new ${name} discrepancy was recorded in [\`${filePath}\`](${permalink(repo, sha, filePath)}).`,
    "",
    fence(source),
    "",
    "## To do",
    "",
    "- [ ] Read the entry and, if it names one, the vendor issue draft.",
    "- [ ] Submit the draft on the vendor's issue form, or comment on the existing vendor issue it names.",
    "- [ ] Record the vendor issue link in the entry (**Vendor issues**, **Vendor proposal**, **Status**) by PR, then close this issue.",
    "",
    "---",
    footer(sha),
  ];
  return { title, body: lines.join("\n") };
}

// renderDraftAlert({ agent, title, source, repo, sha, path }) — the issue for
// a newly added vendor-issue draft. Same fencing discipline as
// renderEntryAlert: the draft's own text is only ever inside fence(source).
function renderDraftAlert({ agent, title, source, repo, sha, path: filePath }) {
  const name = displayName(agent);
  const issueTitle = truncateTitle(`Vendor issue draft ready: ${name} — ${title}`);
  const lines = [
    `A ${name} vendor issue draft is ready in [\`${filePath}\`](${permalink(repo, sha, filePath)}).`,
    "",
    fence(source),
    "",
    "## To do",
    "",
    "- [ ] Submit it on the vendor form linked in the draft (or post it as the comment it names).",
    "- [ ] Set the draft's **Status** to `submitted <link>` and the discrepancy entry's **Vendor issues** / **Vendor proposal** / **Status** by PR, then close this issue.",
    "",
    "---",
    footer(sha),
  ];
  return { title: issueTitle, body: lines.join("\n") };
}

// planAlerts({ entryFiles, drafts, repo, sha }) — every alert this push
// should open, entry alerts first (in the given file order, entries in
// document order), then draft alerts (in the given order). A path whose
// agentOf is unknown, or a draft named README.md, is skipped.
function planAlerts({ entryFiles, drafts, repo, sha }) {
  const alerts = [];
  for (const ef of entryFiles || []) {
    const agent = agentOf(ef.path);
    if (!agent) continue;
    for (const entry of newEntries(ef.before, ef.after)) {
      alerts.push(renderEntryAlert({ agent, heading: entry.heading, source: entry.source, repo, sha, path: ef.path }));
    }
  }
  for (const d of drafts || []) {
    if (path.basename(d.path) === "README.md") continue;
    const agent = agentOf(d.path);
    if (!agent) continue;
    const { title, source } = parseDraft(d.text, d.path);
    alerts.push(renderDraftAlert({ agent, title, source, repo, sha, path: d.path }));
  }
  return alerts;
}

// runAlerts({ alerts, api, assignee, label, dryRun, log }) — the write side.
// dryRun calls no api method at all. Otherwise ensures the label once (only
// if there is at least one alert), then for each alert either skips it
// (already filed, any state) or creates it. Api errors propagate.
function runAlerts({ alerts, api, assignee, label, dryRun, log }) {
  const logger = log || (() => {});

  if (dryRun) {
    for (const a of alerts) logger(`discrepancy-alert: (dry-run) would open: ${a.title}`);
    logger(`discrepancy-alert: ${alerts.length} planned, 0 created, 0 already filed`);
    return { planned: alerts.length, created: 0, skipped: 0 };
  }

  if (alerts.length) api.ensureLabel(label);

  let created = 0;
  let skipped = 0;
  for (const a of alerts) {
    const existing = api.findIssueByTitle(a.title);
    if (existing) {
      skipped++;
      continue;
    }
    api.createIssue({ title: a.title, body: a.body, label, assignee });
    created++;
  }

  logger(`discrepancy-alert: ${alerts.length} planned, ${created} created, ${skipped} already filed`);
  return { planned: alerts.length, created, skipped };
}

// ── gh plumbing (the only impure API layer) ────────────────────────────────

function execGh(args) {
  return execFileSync("gh", args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], maxBuffer: 64 * 1024 * 1024 });
}

// ghApi(repo, label, exec = execGh) — the real, gh-backed implementation of
// every method runAlerts calls through `api`. Every `gh` call goes through
// `exec(args)` (an argv array in, stdout string out — the same contract as
// execGh), so a test can inject a fake and never spawn a real `gh` process.
//
// findIssueByTitle keeps a single Map, title -> item, fetched once (by
// label, not search — see the header comment) and reused for the rest of
// the run. createIssue seeds that same Map with the title it just created,
// so a later findIssueByTitle of that title needs no further `exec` call
// even when it is the very first lookup this ghApi instance ever performs.
function ghApi(repo, label, exec = execGh) {
  let cache = null; // null = not yet fetched; a Map once it has been.

  return {
    ensureLabel(l) {
      exec([
        "label", "create", l,
        "--repo", repo,
        "--color", "B60205",
        "--description", "Vendor behavior that contradicts its changelog",
        "--force",
      ]);
    },
    findIssueByTitle(title) {
      if (cache === null) {
        const out = exec([
          "issue", "list",
          "--repo", repo,
          "--state", "all",
          "--label", label,
          "--limit", "1000",
          "--json", "number,title",
        ]);
        const items = JSON.parse(out);
        cache = new Map(items.map((i) => [i.title, i]));
      }
      return cache.get(title) || null;
    },
    createIssue({ title, body, label: issueLabel, assignee }) {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "discrepancy-alert-"));
      const bodyFile = path.join(dir, "body.md");
      try {
        fs.writeFileSync(bodyFile, body, "utf8");
        exec([
          "issue", "create",
          "--repo", repo,
          "--title", title,
          "--body-file", bodyFile,
          "--label", issueLabel,
          "--assignee", assignee,
        ]);
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
      if (cache === null) cache = new Map();
      cache.set(title, { title });
    },
  };
}

// ── git plumbing (argv arrays via execFileSync, never a shell string) ──────

function isEmptyRef(ref) {
  return !ref || /^0+$/.test(ref);
}

function execGit(args) {
  return execFileSync("git", args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

function splitLines(out) {
  return out.split("\n").filter(Boolean);
}

// showAt(ref, path) — the file's text at `ref`, or "" when `ref` is
// empty/all-zeros or the path does not exist there.
function showAt(ref, filePath) {
  if (isEmptyRef(ref)) return "";
  try {
    return execGit(["show", `${ref}:${filePath}`]);
  } catch {
    return "";
  }
}

// changedEntryFiles(before, after) — agent-*.DISCREPANCIES.md paths changed
// between two refs. An empty/zero `before` (first push, or a manually
// invoked workflow_dispatch with nothing to diff against) lists every such
// path present at `after` instead of diffing.
function changedEntryFiles(before, after) {
  if (isEmptyRef(before)) {
    const out = execGit(["ls-tree", "-r", "--name-only", after, "--", "docs/reference/"]);
    return splitLines(out).filter((p) => /^docs\/reference\/agent-[^/]+\.DISCREPANCIES\.md$/.test(p));
  }
  const out = execGit(["diff", "--name-only", before, after, "--", ENTRY_FILE_PATTERN]);
  return splitLines(out);
}

// addedDrafts(before, after) — .md files ADDED under
// docs/reference/vendor-issue-drafts/ between two refs, excluding README.md.
// Same empty/zero `before` fallback as changedEntryFiles.
function addedDrafts(before, after) {
  if (isEmptyRef(before)) {
    const out = execGit(["ls-tree", "-r", "--name-only", after, "--", DRAFTS_DIR]);
    return splitLines(out).filter((p) => p.endsWith(".md") && path.basename(p) !== "README.md");
  }
  const out = execGit(["diff", "--name-only", "--diff-filter=A", before, after, "--", DRAFTS_DIR]);
  return splitLines(out).filter((p) => p.endsWith(".md") && path.basename(p) !== "README.md");
}

// ── CLI ─────────────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const flags = { before: null, after: "HEAD", repo: null, assignee: null, label: DEFAULT_LABEL, dryRun: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--before") { flags.before = argv[++i]; continue; }
    if (a === "--after") { flags.after = argv[++i]; continue; }
    if (a === "--repo") { flags.repo = argv[++i]; continue; }
    if (a === "--assignee") { flags.assignee = argv[++i]; continue; }
    if (a === "--label") { flags.label = argv[++i]; continue; }
    if (a === "--dry-run") { flags.dryRun = true; continue; }
    throw new Error(`unknown flag ${a}`);
  }
  if (!flags.repo) throw new Error("--repo is required");
  if (!flags.dryRun && !flags.assignee) throw new Error("--assignee is required unless --dry-run");
  return flags;
}

function main(argv) {
  try {
    const flags = parseArgs(argv);

    const entryFiles = changedEntryFiles(flags.before, flags.after).map((p) => ({
      path: p,
      before: showAt(flags.before, p),
      after: showAt(flags.after, p),
    }));
    const drafts = addedDrafts(flags.before, flags.after).map((p) => ({ path: p, text: showAt(flags.after, p) }));

    const sha = execGit(["rev-parse", flags.after]).trim();
    const alerts = planAlerts({ entryFiles, drafts, repo: flags.repo, sha });

    runAlerts({
      alerts,
      api: ghApi(flags.repo, flags.label),
      assignee: flags.assignee,
      label: flags.label,
      dryRun: flags.dryRun,
      log: console.log,
    });
    process.exit(0);
  } catch (e) {
    // Never print an issue body or environment here — only the error message.
    process.stderr.write(`discrepancy-alert: error: ${e.message}\n`);
    process.exit(1);
  }
}

if (require.main === module) {
  main(process.argv.slice(2));
}

module.exports = {
  parseEntries,
  newEntries,
  parseDraft,
  agentOf,
  displayName,
  fence,
  renderEntryAlert,
  renderDraftAlert,
  planAlerts,
  runAlerts,
  ghApi,
  showAt,
  changedEntryFiles,
  addedDrafts,
  main,
};
