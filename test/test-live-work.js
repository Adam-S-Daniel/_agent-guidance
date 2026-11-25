"use strict";
// test-live-work.js — node:test coverage for scripts/live-work.js.
// Every instant is a fixed ISO string, `gh` is a fake that records its
// argument arrays, and nothing reaches the network. Run via
// test/run-tests.sh's test_live_work.

const test = require("node:test");
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const path = require("node:path");

const mod = require("../scripts/live-work.js");

const NOW = new Date("2026-11-25T12:00:00Z");
const REPO = "example-owner/widgets";
const ITEM = `${REPO}#198`;
const OLD_ISSUE = { created_at: "2026-11-01T00:00:00Z" };

function prRef(number, state, repo = REPO) {
  return {
    event: "cross-referenced",
    created_at: "2026-11-10T00:00:00Z",
    source: { issue: { number, state, repository: { full_name: repo }, pull_request: {} } },
  };
}

// timelines: an array of pages; page N is timelines[N-1].
function fakeGh(issue, pages) {
  const calls = [];
  const gh = (args) => {
    calls.push(args);
    const url = args[1];
    if (url.endsWith("/issues/198")) return JSON.stringify(issue);
    const m = /[?&]page=([0-9]+)/.exec(url);
    return JSON.stringify(pages[Number(m[1]) - 1] || []);
  };
  return { gh, calls };
}

function go(issue, pages, minutes = 60) {
  const { gh, calls } = fakeGh(issue, pages);
  return { out: mod.run({ gh, items: [ITEM], now: NOW, minutes }).items[0], calls };
}

test("an open PR cross-reference makes the item live", () => {
  const { out } = go(OLD_ISSUE, [[prRef(210, "open", "example-owner/other")]]);
  assert.equal(out.live, true);
  assert.deepEqual(out.reasons, ["open PR example-owner/other#210"]);
});

test("a closed PR cross-reference is not live", () => {
  assert.equal(go(OLD_ISSUE, [[prRef(210, "closed")]]).out.live, false);
});

test("a cross-reference whose source is the item itself is skipped", () => {
  assert.equal(go(OLD_ISSUE, [[prRef(198, "open")]]).out.live, false);
});

test("html_url supplies the repo when the source has no repository", () => {
  const ev = prRef(210, "open");
  delete ev.source.issue.repository;
  ev.source.issue.html_url = "https://github.com/example-owner/other/pull/210";
  assert.deepEqual(go(OLD_ISSUE, [[ev]]).out.reasons, ["open PR example-owner/other#210"]);
});

test("a comment inside the window is live, using the later of created and updated", () => {
  const ev = { event: "commented", created_at: "2026-11-25T09:00:00Z", updated_at: "2026-11-25T11:30:00Z" };
  const { out } = go(OLD_ISSUE, [[ev]]);
  assert.equal(out.live, true);
  assert.deepEqual(out.reasons, ["activity commented at 2026-11-25T11:30:00.000Z"]);
});

test("only old activity is not live, and --minutes widens the window", () => {
  const ev = { event: "labeled", created_at: "2026-11-25T09:00:00Z" };
  assert.equal(go(OLD_ISSUE, [[ev]]).out.live, false);
  assert.equal(go(OLD_ISSUE, [[ev]], 240).out.live, true);
});

test("an event type outside the list is ignored", () => {
  const ev = { event: "subscribed", created_at: "2026-11-25T11:59:00Z" };
  assert.equal(go(OLD_ISSUE, [[ev]]).out.live, false);
});

test("the filer labeling a brand-new issue within 5 minutes is not live", () => {
  const issue = { created_at: "2026-11-25T11:50:00Z" };
  const early = { event: "labeled", created_at: "2026-11-25T11:53:00Z" };
  assert.equal(go(issue, [[early]]).out.live, false);
  const later = { event: "commented", created_at: "2026-11-25T11:57:00Z", updated_at: "2026-11-25T11:57:00Z" };
  assert.equal(go(issue, [[early, later]]).out.live, true);
});

test("the timeline is paged until a short page", () => {
  const filler = Array.from({ length: 100 }, () => ({ event: "subscribed", created_at: "2026-11-01T00:00:00Z" }));
  const last = { event: "closed", created_at: "2026-11-25T11:00:00Z" };
  const { out, calls } = go(OLD_ISSUE, [filler, [last]]);
  assert.equal(out.live, true);
  const pages = calls.filter((c) => c[1].includes("/timeline")).map((c) => c[1]);
  assert.deepEqual(pages, [
    `repos/${REPO}/issues/198/timeline?per_page=100&page=1`,
    `repos/${REPO}/issues/198/timeline?per_page=100&page=2`,
  ]);
});

test("an API error propagates from run", () => {
  const gh = () => { const e = new Error("boom"); e.status = 1; throw e; };
  assert.throws(() => mod.run({ gh, items: [ITEM], now: NOW }), /boom/);
});

test("the CLI exits 1 on a usage error without reaching the network", () => {
  const script = path.join(__dirname, "..", "scripts", "live-work.js");
  const r = spawnSync(process.execPath, [script, "not-an-item"], { encoding: "utf8" });
  assert.equal(r.status, 1);
  assert.match(r.stderr, /live-work: bad item/);
  assert.equal(r.stdout, "");
});

test("parseArgs reads --minutes and --now and rejects no items", () => {
  const o = mod.parseArgs(["--minutes", "30", "--now", "2026-11-25T12:00:00Z", ITEM]);
  assert.equal(o.minutes, 30);
  assert.equal(o.now.toISOString(), "2026-11-25T12:00:00.000Z");
  assert.throws(() => mod.parseArgs([]), /no items/);
});
