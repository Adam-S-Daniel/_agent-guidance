"use strict";
// test-stale-claims.js — node:test coverage for scripts/stale-claims.js.
// Every instant is a fixed ISO string, `gh` is a fake that records its
// argument arrays, and nothing reaches the network. Run via
// test/run-tests.sh's test_stale_claims.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const mod = require("../scripts/stale-claims.js");

const ROOT = path.join(__dirname, "..");
const NOW = new Date("2026-11-22T05:00:00Z");
const REPO = "example-owner/widgets";
const ISSUE = { repo: REPO, number: 120, title: "Sweep findings", labels: ["agent-working"] };

function claimed(at) {
  return { event: "labeled", label: { name: "agent-working" }, created_at: at };
}

function pr({ repo = REPO, number = 121, state = "closed", merged_at = null, body = "", labels = [] }) {
  return {
    event: "cross-referenced",
    source: {
      issue: {
        number,
        state,
        body,
        labels: labels.map((name) => ({ name })),
        repository: { full_name: repo },
        html_url: `https://github.com/${repo}/pull/${number}`,
        pull_request: { merged_at },
      },
    },
  };
}

test("a merged Refs PR after the claim, with none open, is requeued (the wsl-automation#120 shape)", () => {
  const tl = [claimed("2026-11-17T08:01:04Z"),
    pr({ merged_at: "2026-11-18T20:54:01Z", body: `## TLDR\nx\n\nRefs ${REPO}#120\n` })];
  const v = mod.classify(ISSUE, tl, NOW);
  assert.equal(v.action, "requeue");
  assert.deepEqual(v.prs.map((p) => p.number), [121]);
});

test("a short #n reference counts only for a PR in the issue's own repo", () => {
  assert.deepEqual(mod.references("Refs #120", REPO, REPO, 120), { refs: true, closes: false });
  assert.deepEqual(mod.references("Refs #120", "example-owner/other", REPO, 120), { refs: false, closes: false });
  assert.deepEqual(mod.references(`Refs ${REPO}#120`, "example-owner/other", REPO, 120), { refs: true, closes: false });
  assert.deepEqual(mod.references("Refs #1200", REPO, REPO, 120), { refs: false, closes: false });
  assert.deepEqual(mod.references(`Closes ${REPO}#120`, REPO, REPO, 120), { refs: false, closes: true });
  assert.deepEqual(mod.references("fixes #120", REPO, REPO, 120), { refs: false, closes: true });
});

test("a Refs PR from another repo requeues a cross-repo tracking issue", () => {
  const tl = [claimed("2026-11-21T19:26:00Z"),
    pr({ repo: "example-owner/other", number: 9, merged_at: "2026-11-21T22:00:00Z", body: `Refs ${REPO}#120` })];
  assert.equal(mod.classify(ISSUE, tl, NOW).action, "requeue");
});

test("an open linked PR means the work is in progress", () => {
  const tl = [claimed("2026-11-17T08:01:04Z"),
    pr({ merged_at: "2026-11-18T20:54:01Z", body: `Refs ${REPO}#120` }),
    pr({ number: 130, state: "open", body: `Refs ${REPO}#120` })];
  assert.equal(mod.classify(ISSUE, tl, NOW).action, "in-progress");
});

test("a Refs PR merged before the latest claim does not requeue again", () => {
  const tl = [claimed("2026-11-17T08:01:04Z"),
    pr({ merged_at: "2026-11-18T20:54:01Z", body: `Refs ${REPO}#120` }),
    claimed("2026-11-19T08:00:00Z")];
  assert.equal(mod.classify(ISSUE, tl, NOW).action, "stale");
});

test("on-hold on the issue or a linked PR is report only", () => {
  const tl = [claimed("2026-11-17T08:01:04Z"),
    pr({ merged_at: "2026-11-18T20:54:01Z", body: `Refs ${REPO}#120` })];
  assert.equal(mod.classify({ ...ISSUE, labels: ["agent-working", "on-hold"] }, tl, NOW).action, "hold");
  const held = [claimed("2026-11-17T08:01:04Z"), pr({ state: "open", labels: ["on-hold"] })];
  assert.equal(mod.classify(ISSUE, held, NOW).action, "hold");
});

test("a fixture-request issue is the sweep's to close, never requeued", () => {
  for (const label of mod.SWEEP_OWNED) {
    const tl = [claimed("2026-11-17T08:01:04Z"),
      pr({ merged_at: "2026-11-18T20:54:01Z", body: `Refs ${REPO}#120` })];
    assert.equal(mod.classify({ ...ISSUE, labels: ["agent-working", label] }, tl, NOW).action, "sweep-owned");
  }
});

test("a merged Closes PR on a still-open issue is reported, not requeued", () => {
  const tl = [claimed("2026-11-17T08:01:04Z"),
    pr({ merged_at: "2026-11-18T20:54:01Z", body: `Closes ${REPO}#120` })];
  assert.equal(mod.classify(ISSUE, tl, NOW).action, "closes-open");
});

test("no PR: stale after 24 hours, fresh before", () => {
  assert.equal(mod.classify(ISSUE, [claimed("2026-11-21T04:59:59Z")], NOW).action, "stale");
  assert.equal(mod.classify(ISSUE, [claimed("2026-11-21T05:00:01Z")], NOW).action, "fresh");
});

test("a closed, unmerged PR does not requeue", () => {
  const tl = [claimed("2026-11-17T08:01:04Z"), pr({ body: `Refs ${REPO}#120` })];
  assert.equal(mod.classify(ISSUE, tl, NOW).action, "stale");
});

function fakeGh({ issueLabels = ["agent-working"], state = "open" } = {}) {
  const calls = [];
  const timeline = [claimed("2026-11-17T08:01:04Z"),
    pr({ merged_at: "2026-11-18T20:54:01Z", body: `Refs ${REPO}#120` })];
  const gh = (args) => {
    calls.push(args);
    const target = args.find((a) => a.startsWith("repos/") || a === "search/issues");
    if (target === "search/issues") {
      return JSON.stringify([{ items: [
        { repository_url: `https://api.github.com/repos/${REPO}`, number: 120, title: "t", labels: [{ name: "agent-working" }] },
        { repository_url: "https://api.github.com/repos/example-owner/not-fleet", number: 1, title: "t", labels: [{ name: "agent-working" }] },
      ] }]);
    }
    if (target.includes("/timeline")) return JSON.stringify([timeline]);
    if (args.includes("-X")) return "{}";
    return JSON.stringify({ state, labels: issueLabels.map((name) => ({ name })) });
  };
  return { gh, calls };
}

const writes = (calls) => calls.filter((a) => a.includes("-X") && a[a.indexOf("-X") + 1] !== "GET");

test("a dry run writes nothing and keeps to fleet repos", () => {
  const { gh, calls } = fakeGh();
  const out = mod.run({ gh, owners: ["example-owner"], fleet: new Set(["widgets"]), now: NOW, apply: false });
  assert.deepEqual(out.issues.map((i) => [i.number, i.action, i.applied]), [[120, "requeue", false]]);
  assert.equal(writes(calls).length, 0);
  const search = calls[0];
  assert.ok(search.includes("q=label:agent-working state:open is:issue user:example-owner"));
});

test("--apply swaps the labels and comments with the marker, in that order", () => {
  const { gh, calls } = fakeGh();
  const out = mod.run({ gh, owners: ["example-owner"], fleet: new Set(["widgets"]), now: NOW, apply: true });
  assert.equal(out.issues[0].applied, true);
  const w = writes(calls);
  assert.equal(w.length, 3);
  assert.deepEqual(w[0], ["api", "-X", "DELETE", `repos/${REPO}/issues/120/labels/agent-working`]);
  assert.deepEqual(w[1], ["api", "-X", "POST", `repos/${REPO}/issues/120/labels`, "-f", "labels[]=agent-ready"]);
  const body = w[2][w[2].length - 1];
  assert.ok(body.startsWith(`body=${mod.MARKER}`));
  assert.ok(body.includes(`https://github.com/${REPO}/pull/121`));
  assert.ok(body.trimEnd().endsWith("on behalf of @Adam-S-Daniel</sub>"));
});

test("--apply re-reads the labels and refuses on on-hold or a claim already gone", () => {
  for (const issueLabels of [["agent-working", "on-hold"], ["agent-ready"]]) {
    const { gh, calls } = fakeGh({ issueLabels });
    const out = mod.run({ gh, owners: ["example-owner"], fleet: new Set(["widgets"]), now: NOW, apply: true });
    assert.equal(out.issues[0].applied, false);
    assert.equal(writes(calls).length, 0);
  }
});

test("owners come from SYNC_OWNERS or --owners, never a default", () => {
  assert.throws(() => mod.parseArgs([], {}), /no owners/);
  assert.deepEqual(mod.parseArgs([], { SYNC_OWNERS: "a b" }).owners, ["a", "b"]);
  assert.deepEqual(mod.parseArgs(["--owners", "c"], { SYNC_OWNERS: "a b" }).owners, ["c"]);
  assert.throws(() => mod.parseArgs(["--now", "nope"], { SYNC_OWNERS: "a" }), /not a date/);
});

test("the fleet worker and the issue worker docs name the script", () => {
  for (const doc of ["docs/routines/fleet-worker.md", "docs/reference/agent-issue-worker.md"]) {
    const text = fs.readFileSync(path.join(ROOT, doc), "utf8");
    assert.ok(text.includes("scripts/stale-claims.js"), `${doc} must name scripts/stale-claims.js`);
  }
});
