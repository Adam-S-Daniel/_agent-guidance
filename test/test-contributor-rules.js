// Tests for scripts/build-contributor-rules.js (ADR 0043 decision 1).
// Fixture trees under a temp directory; no network, no clock.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const SCRIPT = path.join(__dirname, "..", "scripts", "build-contributor-rules.js");
const { build, OUTPUT_REL } = require(SCRIPT);

function tree(base, manifest) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "contrib-rules-"));
  fs.mkdirSync(path.join(dir, "agents-md"));
  fs.writeFileSync(path.join(dir, "agents-md", "base.md"), base);
  fs.writeFileSync(path.join(dir, "agents-md", "eval-coverage.yml"), manifest);
  return dir;
}

function row(id, heading, audience) {
  const a = audience === undefined ? "" : `  audience: ${audience}\n`;
  return `- id: ${id}\n  heading: ${heading}\n  file: agents-md/base.md\n${a}  status: gap\n  bytes: 0\n`;
}

const BASE = [
  "# Title",
  "",
  "Owner preamble that never ships.",
  "",
  "## Alpha",
  "alpha body",
  "",
  "```",
  "## not a heading, inside a fence",
  "```",
  "",
  "## Beta",
  "beta body (owner)",
  "",
  "## Gamma",
  "gamma body",
  "",
].join("\n");

const MANIFEST = row("alpha", "Alpha", "contributor") + row("beta", "Beta", "owner") + row("gamma", "Gamma", "contributor");

test("copies contributor sections in base.md order, byte for byte, and nothing else", () => {
  const out = build(tree(BASE, MANIFEST));
  assert.match(out, /^<!-- Managed by _agent-guidance/);
  const body = out.slice(out.indexOf("## Alpha"));
  assert.strictEqual(
    body,
    "## Alpha\nalpha body\n\n```\n## not a heading, inside a fence\n```\n\n## Gamma\ngamma body\n",
  );
  assert.ok(!out.includes("Owner preamble"), "the base.md preamble is the owner's");
  assert.ok(!out.includes("beta body"), "an owner section never ships");
});

test("refuses a heading with no manifest row", () => {
  const dir = tree(BASE, row("alpha", "Alpha", "contributor") + row("gamma", "Gamma", "contributor"));
  assert.throws(() => build(dir), /heading "Beta" .* has no row/);
});

test("refuses a row with no audience or an unknown one", () => {
  for (const bad of [undefined, "everyone"]) {
    const dir = tree(BASE, row("alpha", "Alpha", bad) + row("beta", "Beta", "owner") + row("gamma", "Gamma", "owner"));
    assert.throws(() => build(dir), /audience ".*", expected contributor or owner/);
  }
});

test("refuses when no section is for contributors", () => {
  const dir = tree(BASE, row("alpha", "Alpha", "owner") + row("beta", "Beta", "owner") + row("gamma", "Gamma", "owner"));
  assert.throws(() => build(dir), /no section .* audience: contributor/);
});

test("--check exits 1 on a missing or stale file and 0 after --write", () => {
  const dir = tree(BASE, MANIFEST);
  const run = (...a) => spawnSync(process.execPath, [SCRIPT, "--repo-root", dir, ...a], { encoding: "utf8" });
  let r = run("--check");
  assert.strictEqual(r.status, 1);
  assert.match(r.stderr, /is missing; regenerate it with/);
  assert.strictEqual(run("--write").status, 0);
  assert.strictEqual(run("--check").status, 0);
  fs.appendFileSync(path.join(dir, OUTPUT_REL), "hand edit\n");
  r = run("--check");
  assert.strictEqual(r.status, 1);
  assert.match(r.stderr, /is stale/);
});

test("a source it cannot build from exits 2", () => {
  const dir = tree(BASE, "not: a list\n");
  const r = spawnSync(process.execPath, [SCRIPT, "--repo-root", dir, "--check"], { encoding: "utf8" });
  assert.strictEqual(r.status, 2);
});

test("this repo's committed file is current, and holds no owner section", () => {
  const root = path.join(__dirname, "..");
  const committed = fs.readFileSync(path.join(root, OUTPUT_REL), "utf8");
  assert.strictEqual(committed, build(root));
  const YAML = require("yaml");
  const rows = YAML.parse(fs.readFileSync(path.join(root, "agents-md", "eval-coverage.yml"), "utf8"));
  const owner = rows.filter((r) => r.file === "agents-md/base.md" && r.audience === "owner");
  assert.ok(owner.length > 0);
  for (const r of owner) {
    assert.ok(!committed.includes(`\n## ${r.heading}\n`), `owner section "${r.heading}" must not ship`);
  }
});
