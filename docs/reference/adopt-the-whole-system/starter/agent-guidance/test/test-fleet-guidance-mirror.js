"use strict";
// test-fleet-guidance-mirror.js — node:test coverage for
// scripts/check-fleet-guidance-mirror.js. Every fixture is a temp tree; no
// network, clock or sleeps. Each failure test starts from the green baseline
// and breaks exactly one thing, so the checker is shown able to fail for that
// reason. Run via test/run-tests.sh's test_fleet_guidance_mirror, which
// requires `# fail 0` and a minimum `# pass` count.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const SCRIPT = path.join(__dirname, "..", "scripts", "check-fleet-guidance-mirror.js");
const REPO = path.join(__dirname, "..");
const { run } = require(SCRIPT);

const BASE = "# Guidance\n\nSee [the incident](https://example.com/blob/main/docs/evidence/2026-01-01-x.md).\n";
const STUB = "# Stub\n\nNo pointers here.\n";

function tree(overrides = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mirror-"));
  const files = {
    "agents-md/base.md": BASE,
    "agents-md/stub.md": STUB,
    ".claude/hooks/fleet-guidance.md": BASE,
    "docs/evidence/2026-01-01-x.md": "evidence\n",
    ...overrides,
  };
  for (const [rel, body] of Object.entries(files)) {
    if (body === null) continue;
    fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    fs.writeFileSync(path.join(root, rel), body);
  }
  return root;
}

test("green baseline passes", () => {
  assert.deepEqual(run(tree()), { code: 0, lines: [] });
});

test("a drifted payload fails and names the fix and the first differing line", () => {
  const r = run(tree({ ".claude/hooks/fleet-guidance.md": BASE.replace("Guidance", "Guidance v2") }));
  assert.equal(r.code, 1);
  assert.match(r.lines[0], /differs from agents-md\/base\.md/);
  assert.match(r.lines[0], /first difference at line 1/);
  assert.match(r.lines[0], /cp agents-md\/base\.md \.claude\/hooks\/fleet-guidance\.md/);
});

test("a trailing-newline-only drift fails (byte-identical, not text-equivalent)", () => {
  const r = run(tree({ ".claude/hooks/fleet-guidance.md": BASE + "\n" }));
  assert.equal(r.code, 1);
});

test("a stub copied over the payload fails", () => {
  assert.equal(run(tree({ ".claude/hooks/fleet-guidance.md": STUB })).code, 1);
});

test("a missing or empty payload fails", () => {
  assert.equal(run(tree({ ".claude/hooks/fleet-guidance.md": null })).code, 1);
  assert.equal(run(tree({ ".claude/hooks/fleet-guidance.md": "" })).code, 1);
});

test("a dead docs/evidence pointer in base.md fails, with file and line", () => {
  const base = BASE + "\nAlso docs/evidence/gone.md.\n";
  const r = run(tree({ "agents-md/base.md": base, ".claude/hooks/fleet-guidance.md": base }));
  assert.equal(r.code, 1);
  assert.equal(r.lines.length, 1);
  assert.match(r.lines[0], /^agents-md\/base\.md:5 points at docs\/evidence\/gone\.md,/);
});

test("a dead docs/evidence pointer in stub.md or a section file fails", () => {
  const stub = run(tree({ "agents-md/stub.md": "see docs/evidence/nope.md\n" }));
  assert.equal(stub.code, 1);
  assert.match(stub.lines[0], /^agents-md\/stub\.md:1 /);
  const sec = run(tree({ "agents-md/sections/python.md": "[x](docs/evidence/nope.md)\n" }));
  assert.equal(sec.code, 1);
  assert.match(sec.lines[0], /^agents-md\/sections\/python\.md:1 /);
});

test("deleting the evidence file a pointer names fails", () => {
  assert.equal(run(tree({ "docs/evidence/2026-01-01-x.md": null })).code, 1);
});

test("a bare docs/evidence/ directory mention is not a pointer", () => {
  const base = BASE + "\nLong narratives live under docs/evidence/.\n";
  assert.equal(run(tree({ "agents-md/base.md": base, ".claude/hooks/fleet-guidance.md": base })).code, 0);
});

test("missing base.md or stub.md is exit 2, never a pass", () => {
  assert.equal(run(tree({ "agents-md/stub.md": null })).code, 2);
  assert.equal(run(tree({ "agents-md/base.md": null })).code, 2);
});

test("CLI: exit codes and annotations on a temp tree", () => {
  const ok = spawnSync("node", [SCRIPT, "--root", tree()], { encoding: "utf8" });
  assert.equal(ok.status, 0);
  const bad = spawnSync("node", [SCRIPT, "--root", tree({ ".claude/hooks/fleet-guidance.md": STUB })], { encoding: "utf8" });
  assert.equal(bad.status, 1);
  assert.match(bad.stderr, /^::error::/);
});

test("the real repo is consistent", () => {
  const r = spawnSync("node", [SCRIPT, "--root", REPO], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
});
