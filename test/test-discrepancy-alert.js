"use strict";
// test-discrepancy-alert.js — node:test coverage for
// scripts/discrepancy-alert.js (docs/reference/agent-discrepancy-process.md,
// section 5: "Alert (automatic)"). Every fixture is fabricated (fake `api`
// objects, no network, no Date.now(), no sleeps) except one end-to-end test
// that shells out to a REAL, disposable git repo it creates under the OS
// tmpdir and cleans up afterwards — never the network, never `gh`. Run via
// test/run-tests.sh's test_discrepancy_alert, which requires `# fail 0` and
// `# pass >= 20`.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const {
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
} = require("../scripts/discrepancy-alert.js");

// ── Shared fixtures ─────────────────────────────────────────────────────────

const NO_ENTRIES_MD = [
  "# Discrepancies — Claude Code behavior vs. its release log",
  "",
  "## How to add an entry",
  "",
  "Some prose about how to add an entry, including a heading-shaped",
  "line inside a fence:",
  "",
  "```markdown",
  "### YYYY-MM-DD — <one-line summary>",
  "",
  "- **Kind:** contradicts changelog",
  "```",
  "",
  "## Entries",
  "",
  "None yet.",
  "",
].join("\n");

const TWO_ENTRIES_MD = [
  "# Discrepancies — Claude Code behavior vs. its release log",
  "",
  "## How to add an entry",
  "",
  "```markdown",
  "### YYYY-MM-DD — <one-line summary>",
  "",
  "- **Kind:** contradicts changelog",
  "```",
  "",
  "## Entries",
  "",
  "### 2026-09-20 — Second, newer entry",
  "",
  "- **Kind:** undocumented change",
  "- **Status:** open",
  "",
  "### 2026-09-10 — First, older entry",
  "",
  "- **Kind:** contradicts changelog",
  "- **Status:** open",
  "- **Evidence:** see the linked run",
  "",
  "## Follow-up at every new changelog entry",
  "",
  "Some trailing prose that must never be mistaken for entry 1's source.",
  "",
].join("\n");

function fakeAlertApi(overrides = {}) {
  return {
    ensureLabel: overrides.ensureLabel || (() => {}),
    findIssueByTitle: overrides.findIssueByTitle || (() => null),
    createIssue: overrides.createIssue || (() => ({})),
  };
}

// ── 1. parseEntries ──────────────────────────────────────────────────────────

test("parseEntries: no ## Entries heading -> []", () => {
  const md = "# Title\n\n## Something Else\n\n### not an entry\n";
  assert.deepEqual(parseEntries(md), []);
});

test('parseEntries: "None yet." under Entries -> []', () => {
  assert.deepEqual(parseEntries(NO_ENTRIES_MD), []);
});

test("parseEntries: a ### inside a fenced ```markdown block (the template) is ignored", () => {
  const entries = parseEntries(NO_ENTRIES_MD);
  assert.deepEqual(entries, []);
  assert.ok(!entries.some((e) => e.heading.includes("YYYY-MM-DD")));
});

test("parseEntries: an h3 BEFORE ## Entries is ignored", () => {
  const md = "# Title\n\n### Before Entries, must be ignored\n\n## Entries\n\n### 2026-01-01 — Real entry\n\nbody\n";
  const entries = parseEntries(md);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].heading, "2026-01-01 — Real entry");
});

test("parseEntries: collection stops at a following h2", () => {
  const md = "# T\n\n## Entries\n\n### 2026-01-01 — Only entry\n\nbody line\n\n## Follow-up section\n\n### Not an entry, past the boundary\n";
  const entries = parseEntries(md);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].heading, "2026-01-01 — Only entry");
});

test("parseEntries: two entries, both returned in document order with correct source spans", () => {
  const entries = parseEntries(TWO_ENTRIES_MD);
  assert.equal(entries.length, 2);
  assert.equal(entries[0].heading, "2026-09-20 — Second, newer entry");
  assert.equal(entries[1].heading, "2026-09-10 — First, older entry");

  // First entry's source spans from its own heading up to (not including) the
  // second entry's heading, trailing blank lines stripped.
  assert.ok(entries[0].source.startsWith("### 2026-09-20 — Second, newer entry"));
  assert.ok(entries[0].source.includes("undocumented change"));
  assert.ok(!entries[0].source.includes("First, older entry"));
  assert.ok(!entries[0].source.endsWith("\n"));

  // Second entry's source spans to EOF (minus the trailing "## Follow-up"
  // section, which is a sibling h2, not part of the entry) and must not leak
  // the trailing prose under that h2.
  assert.ok(entries[1].source.startsWith("### 2026-09-10 — First, older entry"));
  assert.ok(entries[1].source.includes("Evidence"));
  assert.ok(!entries[1].source.includes("must never be mistaken"));
});

test("parseEntries: regression — the real agent-claude-code.DISCREPANCIES.md parses to []", () => {
  const p = path.join(__dirname, "..", "docs", "reference", "agent-claude-code.DISCREPANCIES.md");
  const text = fs.readFileSync(p, "utf8");
  assert.deepEqual(parseEntries(text), []);
});

test("parseEntries: regression — the real agent-codex.DISCREPANCIES.md parses to []", () => {
  const p = path.join(__dirname, "..", "docs", "reference", "agent-codex.DISCREPANCIES.md");
  const text = fs.readFileSync(p, "utf8");
  assert.deepEqual(parseEntries(text), []);
});

// ── 2. newEntries ────────────────────────────────────────────────────────────

test("newEntries: an added heading is detected", () => {
  const before = "# T\n\n## Entries\n\n### 2026-01-01 — Old entry\n\nbody\n";
  const after = "# T\n\n## Entries\n\n### 2026-02-01 — New entry\n\nnew body\n\n### 2026-01-01 — Old entry\n\nbody\n";
  const added = newEntries(before, after);
  assert.equal(added.length, 1);
  assert.equal(added[0].heading, "2026-02-01 — New entry");
});

test("newEntries: editing an existing entry's body is NOT new", () => {
  const before = "# T\n\n## Entries\n\n### 2026-01-01 — Old entry\n\noriginal body\n";
  const after = "# T\n\n## Entries\n\n### 2026-01-01 — Old entry\n\nEDITED body, status changed\n";
  assert.deepEqual(newEntries(before, after), []);
});

test("newEntries: a removed entry produces nothing", () => {
  const before = "# T\n\n## Entries\n\n### 2026-01-01 — Gone soon\n\nbody\n\n### 2026-02-01 — Stays\n\nbody\n";
  const after = "# T\n\n## Entries\n\n### 2026-02-01 — Stays\n\nbody\n";
  assert.deepEqual(newEntries(before, after), []);
});

test('newEntries: beforeMd = "" returns every entry in afterMd', () => {
  const after = TWO_ENTRIES_MD;
  const added = newEntries("", after);
  assert.equal(added.length, 2);
  assert.deepEqual(added.map((e) => e.heading), parseEntries(after).map((e) => e.heading));
});

// ── 3. parseDraft ────────────────────────────────────────────────────────────

test("parseDraft: title comes from the first h1", () => {
  const md = "# [BUG] Something is wrong\n\n- **Vendor repo:** `anthropics/claude-code`\n";
  const { title } = parseDraft(md, "docs/reference/vendor-issue-drafts/claude-code/2026-09-25-x.md");
  assert.equal(title, "[BUG] Something is wrong");
});

test("parseDraft: no h1 falls back to the file's basename without .md", () => {
  const md = "No heading here, just prose.\n";
  const { title } = parseDraft(md, "docs/reference/vendor-issue-drafts/codex/2026-09-25-no-heading.md");
  assert.equal(title, "2026-09-25-no-heading");
});

// ── 4. agentOf / displayName ─────────────────────────────────────────────────

test("agentOf: an agent-<x>.DISCREPANCIES.md path resolves the slug", () => {
  assert.equal(agentOf("docs/reference/agent-claude-code.DISCREPANCIES.md"), "claude-code");
  assert.equal(agentOf("docs/reference/agent-codex.DISCREPANCIES.md"), "codex");
});

test("agentOf: a vendor-issue-drafts/<agent>/<file>.md path resolves the slug", () => {
  assert.equal(agentOf("docs/reference/vendor-issue-drafts/claude-code/2026-09-25-x.md"), "claude-code");
  assert.equal(agentOf("docs/reference/vendor-issue-drafts/codex/2026-09-25-y.md"), "codex");
});

test("agentOf: an unrelated path is null", () => {
  assert.equal(agentOf("docs/reference/agent-discrepancy-process.md"), null);
  assert.equal(agentOf("README.md"), null);
  assert.equal(agentOf("docs/reference/vendor-issue-drafts/README.md"), null);
});

test("displayName: mapping for both known agents and passthrough for unknown", () => {
  assert.equal(displayName("claude-code"), "Claude Code");
  assert.equal(displayName("codex"), "Codex");
  assert.equal(displayName("some-other-agent"), "some-other-agent");
});

// ── 5. fence ─────────────────────────────────────────────────────────────────

test("fence: plain text gets a 3-backtick fence with info string 'text'", () => {
  const f = fence("hello world");
  assert.ok(f.startsWith("```text\n"));
  assert.ok(f.endsWith("\n```"));
  assert.ok(f.includes("hello world"));
});

test("fence: text containing a run of 3 backticks gets a 4-backtick fence", () => {
  const f = fence("before\n```\nafter");
  assert.ok(f.startsWith("````text\n"), f);
  assert.ok(f.endsWith("\n````"), f);
});

// ── 6. renderEntryAlert / renderDraftAlert ───────────────────────────────────

const ENTRY_SOURCE_WITH_URL = [
  "### 2026-09-25 — Tool call ordering regression",
  "",
  "- **Kind:** contradicts changelog",
  "- **Vendor issues:** [#1234](https://github.com/anthropics/claude-code/issues/1234) (open)",
].join("\n");

test("renderEntryAlert: exact title format", () => {
  const { title } = renderEntryAlert({
    agent: "claude-code",
    heading: "2026-09-25 — Tool call ordering regression",
    source: ENTRY_SOURCE_WITH_URL,
    repo: "Adam-S-Daniel/_agent-guidance",
    sha: "abcdef01234567890abcdef01234567890abcde",
    path: "docs/reference/agent-claude-code.DISCREPANCIES.md",
  });
  assert.equal(title, "Vendor discrepancy: Claude Code — 2026-09-25 — Tool call ordering regression");
});

test("renderEntryAlert: title is truncated to at most 200 chars, ending with …", () => {
  const longHeading = "x".repeat(250);
  const { title } = renderEntryAlert({
    agent: "codex", heading: longHeading, source: "irrelevant",
    repo: "o/r", sha: "f".repeat(40), path: "docs/reference/agent-codex.DISCREPANCIES.md",
  });
  assert.ok(title.length <= 200, `length ${title.length}`);
  assert.ok(title.endsWith("…"));
});

test("renderEntryAlert: body starts with the permalink line", () => {
  const { body } = renderEntryAlert({
    agent: "claude-code", heading: "2026-09-25 — X", source: ENTRY_SOURCE_WITH_URL,
    repo: "Adam-S-Daniel/_agent-guidance", sha: "abcdef01234567890abcdef01234567890abcde",
    path: "docs/reference/agent-claude-code.DISCREPANCIES.md",
  });
  assert.ok(
    body.startsWith(
      "A new Claude Code discrepancy was recorded in [`docs/reference/agent-claude-code.DISCREPANCIES.md`]" +
        "(https://github.com/Adam-S-Daniel/_agent-guidance/blob/abcdef01234567890abcdef01234567890abcde/docs/reference/agent-claude-code.DISCREPANCIES.md).",
    ),
    body,
  );
});

test("renderEntryAlert: body contains the fenced source and all three checkboxes", () => {
  const { body } = renderEntryAlert({
    agent: "claude-code", heading: "2026-09-25 — X", source: ENTRY_SOURCE_WITH_URL,
    repo: "o/r", sha: "a".repeat(40), path: "docs/reference/agent-claude-code.DISCREPANCIES.md",
  });
  assert.ok(body.includes(fence(ENTRY_SOURCE_WITH_URL)));
  assert.ok(body.includes("## To do"));
  assert.ok(body.includes("- [ ] Read the entry and, if it names one, the vendor issue draft."));
  assert.ok(body.includes("- [ ] Submit the draft on the vendor's issue form, or comment on the existing vendor issue it names."));
  assert.ok(body.includes("- [ ] Record the vendor issue link in the entry (**Vendor issues**, **Vendor proposal**, **Status**) by PR, then close this issue."));
  assert.ok(body.includes("---"));
  assert.ok(body.includes("_Opened by `.github/workflows/vendor-discrepancy-alert.yml` at aaaaaaa._"));
});

test("renderEntryAlert: a vendor issue URL in the entry appears ONLY inside the fence", () => {
  const url = "https://github.com/anthropics/claude-code/issues/1234";
  const { body } = renderEntryAlert({
    agent: "claude-code", heading: "2026-09-25 — X", source: ENTRY_SOURCE_WITH_URL,
    repo: "o/r", sha: "b".repeat(40), path: "docs/reference/agent-claude-code.DISCREPANCIES.md",
  });
  assert.ok(body.includes(url), "sanity: the url should be present somewhere (inside the fence)");
  const fenced = fence(ENTRY_SOURCE_WITH_URL);
  const withoutFence = body.split(fenced).join("");
  assert.ok(!withoutFence.includes(url), "the vendor URL leaked outside the fence");
});

test("renderDraftAlert: exact title format", () => {
  const { title } = renderDraftAlert({
    agent: "codex", title: "[BUG] Something is wrong", source: "# [BUG] Something is wrong\n",
    repo: "o/r", sha: "c".repeat(40), path: "docs/reference/vendor-issue-drafts/codex/2026-09-25-x.md",
  });
  assert.equal(title, "Vendor issue draft ready: Codex — [BUG] Something is wrong");
});

test("renderDraftAlert: title is truncated to at most 200 chars, ending with …", () => {
  const longTitle = "y".repeat(250);
  const { title } = renderDraftAlert({
    agent: "claude-code", title: longTitle, source: "irrelevant",
    repo: "o/r", sha: "d".repeat(40), path: "docs/reference/vendor-issue-drafts/claude-code/x.md",
  });
  assert.ok(title.length <= 200);
  assert.ok(title.endsWith("…"));
});

test("renderDraftAlert: body starts with the permalink line", () => {
  const { body } = renderDraftAlert({
    agent: "codex", title: "[BUG] X", source: "# [BUG] X\n",
    repo: "Adam-S-Daniel/_agent-guidance", sha: "abcdef01234567890abcdef01234567890abcde",
    path: "docs/reference/vendor-issue-drafts/codex/2026-09-25-x.md",
  });
  assert.ok(
    body.startsWith(
      "A Codex vendor issue draft is ready in [`docs/reference/vendor-issue-drafts/codex/2026-09-25-x.md`]" +
        "(https://github.com/Adam-S-Daniel/_agent-guidance/blob/abcdef01234567890abcdef01234567890abcde/docs/reference/vendor-issue-drafts/codex/2026-09-25-x.md).",
    ),
    body,
  );
});

test("renderDraftAlert: body contains the fenced source and both checkboxes", () => {
  const draftSource = "# [BUG] X\n\n- **Vendor repo:** `openai/codex`\n";
  const { body } = renderDraftAlert({
    agent: "codex", title: "[BUG] X", source: draftSource,
    repo: "o/r", sha: "e".repeat(40), path: "docs/reference/vendor-issue-drafts/codex/2026-09-25-x.md",
  });
  assert.ok(body.includes(fence(draftSource)));
  assert.ok(body.includes("## To do"));
  assert.ok(body.includes("- [ ] Submit it on the vendor form linked in the draft (or post it as the comment it names)."));
  assert.ok(body.includes("- [ ] Set the draft's **Status** to `submitted <link>` and the discrepancy entry's **Vendor issues** / **Vendor proposal** / **Status** by PR, then close this issue."));
  assert.ok(body.includes("_Opened by `.github/workflows/vendor-discrepancy-alert.yml` at eeeeeee._"));
});

test("renderDraftAlert: a vendor URL in the draft appears ONLY inside the fence", () => {
  const url = "https://github.com/openai/codex/issues/new?template=3-cli.yml";
  const draftSource = `# [BUG] X\n\n- **Form:** [CLI bug](${url})\n`;
  const { body } = renderDraftAlert({
    agent: "codex", title: "[BUG] X", source: draftSource,
    repo: "o/r", sha: "1".repeat(40), path: "docs/reference/vendor-issue-drafts/codex/2026-09-25-x.md",
  });
  const withoutFence = body.split(fence(draftSource)).join("");
  assert.ok(!withoutFence.includes(url), "the vendor URL leaked outside the fence");
});

// ── 7. planAlerts ────────────────────────────────────────────────────────────

test("planAlerts: entry alerts come before draft alerts, files/entries/drafts in given order", () => {
  const entryFiles = [{
    path: "docs/reference/agent-claude-code.DISCREPANCIES.md",
    before: "# T\n\n## Entries\n\nNone yet.\n",
    after: TWO_ENTRIES_MD,
  }];
  const drafts = [{
    path: "docs/reference/vendor-issue-drafts/claude-code/2026-09-25-x.md",
    text: "# [BUG] A draft\n",
  }];
  const alerts = planAlerts({ entryFiles, drafts, repo: "o/r", sha: "2".repeat(40) });
  assert.equal(alerts.length, 3);
  assert.ok(alerts[0].title.startsWith("Vendor discrepancy:"));
  assert.ok(alerts[1].title.startsWith("Vendor discrepancy:"));
  assert.ok(alerts[2].title.startsWith("Vendor issue draft ready:"));
  // Entries preserve document order (newest heading in TWO_ENTRIES_MD first).
  assert.ok(alerts[0].title.includes("Second, newer entry"));
  assert.ok(alerts[1].title.includes("First, older entry"));
});

test("planAlerts: an entry file whose agentOf is unknown is skipped entirely", () => {
  const entryFiles = [{ path: "docs/reference/unrelated.md", before: "", after: TWO_ENTRIES_MD }];
  const alerts = planAlerts({ entryFiles, drafts: [], repo: "o/r", sha: "3".repeat(40) });
  assert.deepEqual(alerts, []);
});

test("planAlerts: a draft whose agentOf is unknown is skipped", () => {
  const drafts = [{ path: "docs/reference/vendor-issue-drafts/unknown-place.md", text: "# X\n" }];
  const alerts = planAlerts({ entryFiles: [], drafts, repo: "o/r", sha: "4".repeat(40) });
  assert.deepEqual(alerts, []);
});

test("planAlerts: a draft named README.md is skipped", () => {
  const drafts = [{ path: "docs/reference/vendor-issue-drafts/claude-code/README.md", text: "# Readme\n" }];
  const alerts = planAlerts({ entryFiles: [], drafts, repo: "o/r", sha: "5".repeat(40) });
  assert.deepEqual(alerts, []);
});

// ── 8. runAlerts ─────────────────────────────────────────────────────────────

test("runAlerts: dryRun calls no api method and reports planned=N, created=0", () => {
  const calls = [];
  const api = fakeAlertApi({
    ensureLabel: () => calls.push("ensureLabel"),
    findIssueByTitle: () => { calls.push("findIssueByTitle"); return null; },
    createIssue: () => { calls.push("createIssue"); return {}; },
  });
  const logs = [];
  const alerts = [{ title: "T1", body: "B1" }, { title: "T2", body: "B2" }];
  const result = runAlerts({ alerts, api, assignee: "owner", label: "vendor-discrepancy", dryRun: true, log: (l) => logs.push(l) });
  assert.deepEqual(calls, []);
  assert.deepEqual(result, { planned: 2, created: 0, skipped: 0 });
  assert.ok(logs.some((l) => l.includes("T1")));
  assert.ok(logs.some((l) => l.includes("T2")));
  assert.ok(logs.some((l) => l.includes("2 planned, 0 created")));
});

test("runAlerts: creates an issue when findIssueByTitle returns null, passing label and assignee", () => {
  const created = [];
  const api = fakeAlertApi({
    findIssueByTitle: () => null,
    createIssue: (args) => { created.push(args); return {}; },
  });
  const result = runAlerts({ alerts: [{ title: "New one", body: "B" }], api, assignee: "adam", label: "vendor-discrepancy", dryRun: false, log: () => {} });
  assert.equal(result.created, 1);
  assert.equal(result.skipped, 0);
  assert.equal(created.length, 1);
  assert.equal(created[0].title, "New one");
  assert.equal(created[0].body, "B");
  assert.equal(created[0].label, "vendor-discrepancy");
  assert.equal(created[0].assignee, "adam");
});

test("runAlerts: skips (idempotent) when findIssueByTitle already finds a match, in any state", () => {
  const created = [];
  const api = fakeAlertApi({
    findIssueByTitle: (title) => (title === "Already filed" ? { number: 9, state: "CLOSED" } : null),
    createIssue: (args) => { created.push(args); return {}; },
  });
  const result = runAlerts({
    alerts: [{ title: "Already filed", body: "B" }],
    api, assignee: "adam", label: "vendor-discrepancy", dryRun: false, log: () => {},
  });
  assert.equal(result.created, 0);
  assert.equal(result.skipped, 1);
  assert.deepEqual(created, []);
});

test("runAlerts: ensureLabel is called exactly once for a multi-alert run", () => {
  let ensureLabelCalls = 0;
  const api = fakeAlertApi({ ensureLabel: () => { ensureLabelCalls++; } });
  runAlerts({
    alerts: [{ title: "A", body: "a" }, { title: "B", body: "b" }, { title: "C", body: "c" }],
    api, assignee: "adam", label: "vendor-discrepancy", dryRun: false, log: () => {},
  });
  assert.equal(ensureLabelCalls, 1);
});

test("runAlerts: ensureLabel is NOT called when there are no alerts", () => {
  let ensureLabelCalls = 0;
  const api = fakeAlertApi({ ensureLabel: () => { ensureLabelCalls++; } });
  const result = runAlerts({ alerts: [], api, assignee: "adam", label: "vendor-discrepancy", dryRun: false, log: () => {} });
  assert.equal(ensureLabelCalls, 0);
  assert.deepEqual(result, { planned: 0, created: 0, skipped: 0 });
});

test("runAlerts: an api error (createIssue) propagates", () => {
  const api = fakeAlertApi({
    findIssueByTitle: () => null,
    createIssue: () => { throw new Error("boom: gh issue create failed"); },
  });
  assert.throws(
    () => runAlerts({ alerts: [{ title: "T", body: "B" }], api, assignee: "adam", label: "vendor-discrepancy", dryRun: false, log: () => {} }),
    /boom: gh issue create failed/,
  );
});

test("runAlerts: the final log line format", () => {
  const logs = [];
  const api = fakeAlertApi({
    findIssueByTitle: (title) => (title === "Filed already" ? { number: 1 } : null),
  });
  runAlerts({
    alerts: [{ title: "Filed already", body: "b1" }, { title: "Brand new", body: "b2" }],
    api, assignee: "adam", label: "vendor-discrepancy", dryRun: false, log: (l) => logs.push(l),
  });
  const last = logs[logs.length - 1];
  assert.equal(last, "discrepancy-alert: 2 planned, 1 created, 1 already filed");
});

// ── 9. ghApi (label list, cache, injected exec) ─────────────────────────────
// Every test here builds ghApi("o/r", "vendor-discrepancy", fakeExec), where
// fakeExec records every argv it is called with and returns canned stdout —
// no real `gh` process is ever spawned.

test("ghApi.findIssueByTitle: lists by label with --state all and never uses --search", () => {
  const calls = [];
  const fakeExec = (args) => { calls.push(args); return "[]"; };
  const api = ghApi("o/r", "vendor-discrepancy", fakeExec);

  api.findIssueByTitle("anything");

  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], [
    "issue", "list",
    "--repo", "o/r",
    "--state", "all",
    "--label", "vendor-discrepancy",
    "--limit", "1000",
    "--json", "number,title",
  ]);
  assert.ok(!calls.some((c) => c.includes("--search")), "must never pass --search");
});

test("ghApi.findIssueByTitle: exact title match returns the item; a near miss returns null", () => {
  const title = "Vendor discrepancy: Claude Code — X";
  const fakeExec = () => JSON.stringify([{ number: 3, title }]);
  const api = ghApi("o/r", "vendor-discrepancy", fakeExec);

  assert.deepEqual(api.findIssueByTitle(title), { number: 3, title });
  assert.equal(api.findIssueByTitle(`${title} `), null);
  assert.equal(api.findIssueByTitle(title.toLowerCase()), null);
});

test("ghApi.findIssueByTitle: the label list is fetched once per run (cached)", () => {
  const calls = [];
  const fakeExec = (args) => { calls.push(args); return "[]"; };
  const api = ghApi("o/r", "vendor-discrepancy", fakeExec);

  api.findIssueByTitle("a");
  api.findIssueByTitle("b");
  api.findIssueByTitle("c");

  assert.equal(calls.filter((c) => c[0] === "issue" && c[1] === "list").length, 1);
});

test("ghApi.createIssue: argv carries --label, --assignee and --body-file, and the created title is then found without a new list call", () => {
  const calls = [];
  const fakeExec = (args) => {
    calls.push(args);
    if (args[0] === "issue" && args[1] === "list") return "[]";
    return "";
  };
  const api = ghApi("o/r", "vendor-discrepancy", fakeExec);

  api.createIssue({ title: "Brand new alert", body: "body text", label: "vendor-discrepancy", assignee: "adam" });

  const createCall = calls.find((c) => c[0] === "issue" && c[1] === "create");
  assert.ok(createCall, "expected an issue create call");
  assert.ok(createCall.includes("--label"));
  assert.ok(createCall.includes("--assignee"));
  assert.ok(createCall.includes("--body-file"));
  assert.ok(!createCall.includes("--body"), createCall.join(" "));

  const listCallsBefore = calls.filter((c) => c[0] === "issue" && c[1] === "list").length;
  const found = api.findIssueByTitle("Brand new alert");
  const listCallsAfter = calls.filter((c) => c[0] === "issue" && c[1] === "list").length;

  assert.deepEqual(found, { title: "Brand new alert" });
  assert.equal(listCallsAfter, listCallsBefore, "finding the just-created title must not trigger a new list call");
});

test("ghApi.ensureLabel: goes through the injected exec", () => {
  const calls = [];
  const fakeExec = (args) => { calls.push(args); return ""; };
  const api = ghApi("o/r", "vendor-discrepancy", fakeExec);

  api.ensureLabel("vendor-discrepancy");

  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], [
    "label", "create", "vendor-discrepancy",
    "--repo", "o/r",
    "--color", "B60205",
    "--description", "Vendor behavior that contradicts its changelog",
    "--force",
  ]);
});

// ── 10. End-to-end over a real, disposable git repo ──────────────────────────

function git(cwd, args) {
  return execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

test("end-to-end: a real git repo, two commits, dry-run over the CLI reports both alerts planned", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "discrepancy-alert-e2e-"));
  try {
    git(dir, ["init", "-q"]);

    const entryFile = "docs/reference/agent-claude-code.DISCREPANCIES.md";
    const draftFile = "docs/reference/vendor-issue-drafts/claude-code/2026-09-25-x.md";
    fs.mkdirSync(path.join(dir, "docs", "reference"), { recursive: true });
    fs.mkdirSync(path.join(dir, "docs", "reference", "vendor-issue-drafts", "claude-code"), { recursive: true });

    fs.writeFileSync(
      path.join(dir, entryFile),
      "# Discrepancies — Claude Code behavior vs. its release log\n\n## Entries\n\nNone yet.\n",
    );
    git(dir, ["add", "-A"]);
    git(dir, ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "c1: no entries"]);
    const c1 = git(dir, ["rev-parse", "HEAD"]).trim();

    fs.writeFileSync(
      path.join(dir, entryFile),
      "# Discrepancies — Claude Code behavior vs. its release log\n\n## Entries\n\n" +
        "### 2026-09-25 — Sample tool call ordering regression\n\n- **Kind:** contradicts changelog\n- **Status:** open\n",
    );
    fs.writeFileSync(
      path.join(dir, draftFile),
      "# [BUG] Tool call ordering regression\n\n- **Vendor repo:** `anthropics/claude-code`\n",
    );
    git(dir, ["add", "-A"]);
    git(dir, ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "c2: add entry and draft"]);
    const c2 = git(dir, ["rev-parse", "HEAD"]).trim();

    const scriptPath = path.join(__dirname, "..", "scripts", "discrepancy-alert.js");
    const stdout = execFileSync(
      process.execPath,
      [scriptPath, "--before", c1, "--after", c2, "--repo", "o/r", "--dry-run"],
      { cwd: dir, encoding: "utf8" },
    );

    assert.ok(
      stdout.includes("Vendor discrepancy: Claude Code — 2026-09-25 — Sample tool call ordering regression"),
      stdout,
    );
    assert.ok(
      stdout.includes("Vendor issue draft ready: Claude Code — [BUG] Tool call ordering regression"),
      stdout,
    );
    assert.ok(stdout.includes("2 planned, 0 created"), stdout);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
