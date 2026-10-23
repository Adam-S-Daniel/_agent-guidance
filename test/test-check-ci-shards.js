"use strict";
// test-check-ci-shards.js — node:test coverage for scripts/check-ci-shards.js.
// Every fixture is an in-memory workflow or a temp file; no network, clock or
// sleeps. Each failure test starts from the green fixture and breaks exactly
// one thing, so the checker is shown able to fail for that reason. Run via
// test/run-tests.sh's test_check_ci_shards, which requires `# fail 0` and a
// minimum `# pass` count.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const REPO = path.join(__dirname, "..");
const SCRIPT = path.join(REPO, "scripts", "check-ci-shards.js");
const YAML = require(path.join(REPO, "node_modules", "yaml"));
const { check, canonicalFromRunner } = require(SCRIPT);

const CANON = ["alpha", "beta", "gamma", "delta"];

function workflow({ shards = ["alpha beta", "gamma delta"], failFast = false, env = "${{ matrix.groups }}", run = "./test/run-tests.sh" } = {}) {
  const doc = {
    on: { push: null },
    jobs: {
      suite: {
        "runs-on": "ubuntu-latest",
        strategy: {
          "fail-fast": failFast,
          matrix: { include: shards.map((groups, i) => ({ shard: i + 1, groups })) },
        },
        steps: [
          { uses: "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" },
          { name: "Run integration tests", env: { TEST_GROUPS_ONLY: env }, run },
        ],
      },
    },
  };
  return YAML.stringify(doc);
}

test("green: every group in exactly one shard", () => {
  assert.deepEqual(check(workflow(), CANON), { code: 0, lines: [] });
});

test("a group in no shard fails and names it", () => {
  const r = check(workflow({ shards: ["alpha beta", "gamma"] }), CANON);
  assert.equal(r.code, 1);
  assert.ok(r.lines.some((l) => /group delta is in no shard/.test(l)), r.lines.join("\n"));
});

test("a group in two shards fails and names both", () => {
  const r = check(workflow({ shards: ["alpha beta", "gamma delta beta"] }), CANON);
  assert.equal(r.code, 1);
  assert.ok(r.lines.some((l) => /group beta is in more than one shard \(shard 1, shard 2\)/.test(l)), r.lines.join("\n"));
});

test("a group listed twice in ONE shard is also a duplicate", () => {
  const r = check(workflow({ shards: ["alpha beta alpha", "gamma delta"] }), CANON);
  assert.equal(r.code, 1);
  assert.ok(r.lines.some((l) => /group alpha is in more than one shard/.test(l)), r.lines.join("\n"));
});

test("a missing group AND a duplicated one both fail, each reported", () => {
  const r = check(workflow({ shards: ["alpha beta", "gamma beta"] }), CANON);
  assert.equal(r.code, 1);
  assert.ok(r.lines.some((l) => /group delta is in no shard/.test(l)), r.lines.join("\n"));
  assert.ok(r.lines.some((l) => /group beta is in more than one shard/.test(l)), r.lines.join("\n"));
});

test("a name that is not a test group fails", () => {
  const r = check(workflow({ shards: ["alpha beta", "gamma delta epsilon"] }), CANON);
  assert.equal(r.code, 1);
  assert.ok(r.lines.some((l) => /names epsilon, which is not a test group/.test(l)), r.lines.join("\n"));
});

test("an empty shard fails", () => {
  const r = check(workflow({ shards: ["alpha beta gamma delta", "  "] }), CANON);
  assert.equal(r.code, 1);
  assert.ok(r.lines.some((l) => /shard 2 names no groups/.test(l)), r.lines.join("\n"));
});

test("fail-fast left on fails", () => {
  const r = check(workflow({ failFast: true }), CANON);
  assert.equal(r.code, 1);
  assert.ok(r.lines.some((l) => /fail-fast must be false/.test(l)), r.lines.join("\n"));
});

test("lists that nothing consumes fail: no env wiring", () => {
  const r = check(workflow({ env: "alpha beta gamma delta" }), CANON);
  assert.equal(r.code, 1);
  assert.ok(r.lines.some((l) => /TEST_GROUPS_ONLY/.test(l)), r.lines.join("\n"));
});

test("an interpolation inside run: fails even with env present", () => {
  const r = check(workflow({ run: 'TEST_GROUPS_ONLY="${{ matrix.groups }}" ./test/run-tests.sh' }), CANON);
  assert.equal(r.code, 1);
});

test("a group named only in a YAML comment does not count", () => {
  const text = workflow({ shards: ["alpha beta", "gamma"] }).replace("jobs:", "# shard 2 also runs: delta\njobs:");
  const r = check(text, CANON);
  assert.equal(r.code, 1);
  assert.ok(r.lines.some((l) => /group delta is in no shard/.test(l)), r.lines.join("\n"));
});

test("unreadable inputs refuse (exit 2) rather than certify", () => {
  assert.equal(check(workflow(), []).code, 2);
  assert.equal(check("jobs: {}\n", CANON).code, 2);
  assert.equal(check("jobs:\n  suite:\n    runs-on: x\n", CANON).code, 2);
  assert.equal(check("jobs: [unclosed\n", CANON).code, 2);
});

test("the canonical list comes from run-tests.sh --list-groups, ignoring an ambient TEST_GROUPS_ONLY", () => {
  const saved = process.env.TEST_GROUPS_ONLY;
  process.env.TEST_GROUPS_ONLY = "runner";
  try {
    const groups = canonicalFromRunner(REPO);
    assert.ok(groups.length > 1, `expected the full list, got ${groups.join(" ")}`);
    assert.ok(groups.includes("runner") && groups.includes("sync"));
    assert.equal(new Set(groups).size, groups.length);
  } finally {
    if (saved === undefined) delete process.env.TEST_GROUPS_ONLY;
    else process.env.TEST_GROUPS_ONLY = saved;
  }
});

test("CLI: the real ci.yml passes against the real group list", () => {
  const r = spawnSync(process.execPath, [SCRIPT], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /runs each of the \d+ test groups exactly once/);
});

test("CLI: the real ci.yml with one group removed from its shard fails", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ci-shards-"));
  const doc = YAML.parseDocument(fs.readFileSync(path.join(REPO, ".github", "workflows", "ci.yml"), "utf8"));
  const include = doc.getIn(["jobs", "suite", "strategy", "matrix", "include"]);
  const first = include.items[0];
  const groups = String(first.get("groups")).trim().split(/\s+/);
  const dropped = groups.pop();
  first.set("groups", groups.join(" "));
  const wf = path.join(dir, "ci.yml");
  fs.writeFileSync(wf, doc.toString());
  const r = spawnSync(process.execPath, [SCRIPT, "--workflow", wf], { encoding: "utf8" });
  assert.equal(r.status, 1, r.stderr);
  assert.match(r.stderr, new RegExp(`group ${dropped} is in no shard`));
});

test("CLI: --groups reads a canonical list from a file", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ci-shards-"));
  const wf = path.join(dir, "ci.yml");
  const groups = path.join(dir, "groups.txt");
  fs.writeFileSync(wf, workflow());
  fs.writeFileSync(groups, CANON.join("\n") + "\n");
  assert.equal(spawnSync(process.execPath, [SCRIPT, "--workflow", wf, "--groups", groups]).status, 0);
  fs.writeFileSync(groups, [...CANON, "omega"].join("\n") + "\n");
  assert.equal(spawnSync(process.execPath, [SCRIPT, "--workflow", wf, "--groups", groups]).status, 1);
  assert.equal(spawnSync(process.execPath, [SCRIPT, "--workflow", path.join(dir, "absent.yml"), "--groups", groups]).status, 2);
});
