"use strict";
// test-base-md-merge-window.js — node:test coverage for
// scripts/base-md-merge-window.js (ADR 0039). Every instant is a fixed ISO
// string: no Date.now(), no network, no sleeps. The shipped bounds file is
// parsed with the yaml package, and the fleet worker's prompt is held to name
// the script. Run via test/run-tests.sh's test_base_md_merge_window.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const YAML = require("yaml");

const mod = require("../scripts/base-md-merge-window.js");

const ROOT = path.join(__dirname, "..");
const SCRIPT = path.join(ROOT, "scripts", "base-md-merge-window.js");
const WINDOW = mod.loadWindow();

// 2026-11-09 is a Monday, in EST (UTC-5): noon ET is 17:00 UTC.
const MON_OPEN = "2026-11-09T17:00:00Z";
const MON_LAST = "2026-11-09T18:59:59Z";
const MON_CLOSE = "2026-11-09T19:00:00Z";

test("the shipped bounds are Monday noon to 2 PM US Eastern", () => {
  assert.deepEqual(WINDOW, { timezone: "America/New_York", weekday: 1, start: "12:00", end: "14:00" });
  const raw = YAML.parse(fs.readFileSync(path.join(ROOT, "scripts", "base-md-merge-window.yml"), "utf8"));
  assert.deepEqual(Object.keys(raw).sort(), ["end", "start", "timezone", "weekday"]);
});

test("daylight saving time moves the UTC instants with the clocks", () => {
  // 2026-06-15 is a Monday in EDT (UTC-4): noon ET is 16:00 UTC.
  assert.equal(mod.inWindow(new Date("2026-06-15T15:59:59Z"), WINDOW), false);
  assert.equal(mod.inWindow(new Date("2026-06-15T16:00:00Z"), WINDOW), true);
  assert.equal(mod.inWindow(new Date("2026-06-15T17:59:59Z"), WINDOW), true);
  assert.equal(mod.inWindow(new Date("2026-06-15T18:00:00Z"), WINDOW), false);
  // Clocks go back Sunday 2026-11-01 and forward Sunday 2027-03-14; the next
  // start is found across each change.
  assert.equal(mod.nextWindowStart(new Date("2026-10-27T00:00:00Z"), WINDOW), "2026-11-02T17:00:00.000Z");
  assert.equal(mod.nextWindowStart(new Date("2027-03-09T00:00:00Z"), WINDOW), "2027-03-15T16:00:00.000Z");
  assert.equal(mod.nextWindowStart(new Date("2026-10-26T15:00:00Z"), WINDOW), "2026-10-26T16:00:00.000Z");
});

test("the weekday is the zone's, not UTC's", () => {
  // Monday 23:30 UTC is still Monday 18:30 ET; Tuesday 01:00 UTC is Monday 20:00 ET.
  const late = { timezone: "America/New_York", weekday: 1, start: "18:00", end: "21:00" };
  assert.equal(mod.inWindow(new Date("2026-11-09T23:30:00Z"), late), true);
  assert.equal(mod.inWindow(new Date("2026-11-10T01:00:00Z"), late), true);
  assert.equal(mod.inWindow(new Date("2026-11-10T02:00:00Z"), late), false);
});

test("the window is half open: start is inside, end is outside", () => {
  assert.equal(mod.inWindow(new Date("2026-11-09T16:59:59Z"), WINDOW), false);
  assert.equal(mod.inWindow(new Date(MON_OPEN), WINDOW), true);
  assert.equal(mod.inWindow(new Date(MON_LAST), WINDOW), true);
  assert.equal(mod.inWindow(new Date(MON_CLOSE), WINDOW), false);
});

test("the same hours on any other day are outside", () => {
  for (const day of ["10", "11", "12", "13", "14", "15", "08"]) {
    assert.equal(mod.inWindow(new Date(`2026-11-${day}T17:30:00Z`), WINDOW), false, day);
  }
});

test("Sunday is ISO weekday 7, not 0", () => {
  const sunday = { timezone: "UTC", weekday: 7, start: "17:00", end: "19:00" };
  assert.equal(mod.inWindow(new Date("2026-11-08T17:30:00Z"), sunday), true);
  assert.equal(mod.inWindow(new Date(MON_OPEN), sunday), false);
});

test("only base.md and sections files are guidance paths", () => {
  assert.equal(mod.isGuidancePath("agents-md/base.md"), true);
  assert.equal(mod.isGuidancePath("agents-md/sections/example.md"), true);
  for (const p of ["agents-md/stub.md", "agents-md/eval-coverage.yml", "AGENTS.md", "docs/agents-md/base.md",
    "agents-md/base.md.bak", "agents-md/sections"]) {
    assert.equal(mod.isGuidancePath(p), false, p);
  }
});

test("a guidance change is allowed inside the window and refused outside", () => {
  const files = ["agents-md/base.md", ".claude/hooks/fleet-guidance.md"];
  assert.equal(mod.decide({ files, now: new Date(MON_OPEN), window: WINDOW }).allowed, true);
  const out = mod.decide({ files, now: new Date(MON_CLOSE), window: WINDOW });
  assert.equal(out.allowed, false);
  assert.equal(out.gated, true);
  assert.equal(out.nextWindowStart, "2026-11-16T17:00:00.000Z");
});

test("a change that touches no guidance is never gated", () => {
  const out = mod.decide({ files: ["docs/x.md"], now: new Date("2026-11-11T03:00:00Z"), window: WINDOW });
  assert.equal(out.allowed, true);
  assert.equal(out.gated, false);
});

test("the next start is later the same day before the window and next week after it", () => {
  assert.equal(mod.nextWindowStart(new Date("2026-11-09T12:00:00Z"), WINDOW), "2026-11-09T17:00:00.000Z");
  assert.equal(mod.nextWindowStart(new Date(MON_OPEN), WINDOW), "2026-11-16T17:00:00.000Z");
  assert.equal(mod.nextWindowStart(new Date("2026-12-31T23:00:00Z"), WINDOW), "2027-01-04T17:00:00.000Z");
});

test("checkWindow rejects bad bounds", () => {
  const bad = [
    null, [], { timezone: "UTC", weekday: 1, start: "17:00" },
    { weekday: 1, start: "17:00", end: "19:00" },
    { timezone: "UTC", weekday: 1, start: "17:00", end: "19:00", tz: "UTC" },
    { timezone: "Eastern", weekday: 1, start: "17:00", end: "19:00" },
    { timezone: 5, weekday: 1, start: "17:00", end: "19:00" },
    { timezone: "UTC", weekday: 0, start: "17:00", end: "19:00" },
    { timezone: "UTC", weekday: 8, start: "17:00", end: "19:00" },
    { timezone: "UTC", weekday: "1", start: "17:00", end: "19:00" },
    { timezone: "UTC", weekday: 1, start: "5pm", end: "19:00" },
    { timezone: "UTC", weekday: 1, start: "17:00", end: "24:00" },
    { timezone: "UTC", weekday: 1, start: "19:00", end: "17:00" },
    { timezone: "UTC", weekday: 1, start: "17:00", end: "17:00" },
  ];
  assert.doesNotThrow(() => mod.checkWindow({ timezone: "UTC", weekday: 1, start: "17:00", end: "19:00" }));
  for (const doc of bad) assert.throws(() => mod.checkWindow(doc), undefined, JSON.stringify(doc));
});

test("pathsFromJson reads strings and GitHub file objects, renames included", () => {
  assert.deepEqual(
    mod.pathsFromJson(["a", { filename: "b" }, { filename: "agents-md/sections/x.md", previous_filename: "agents-md/base.md" }]),
    ["a", "b", "agents-md/sections/x.md", "agents-md/base.md"]);
  assert.throws(() => mod.pathsFromJson({}));
  assert.throws(() => mod.pathsFromJson([1]));
});

function run(args) {
  const r = spawnSync(process.execPath, [SCRIPT, ...args], { encoding: "utf8" });
  return { code: r.status, out: r.stdout, err: r.stderr };
}

test("CLI: exit 0 inside the window, 2 outside, for a guidance change", () => {
  assert.equal(run(["--now", MON_OPEN, "--files", "agents-md/base.md"]).code, 0);
  const refused = run(["--now", MON_CLOSE, "--files", "agents-md/sections/x.md"]);
  assert.equal(refused.code, 2);
  assert.match(refused.out, /next window starts 2026-11-16T17:00:00\.000Z/);
  assert.equal(run(["--now", MON_CLOSE, "--files", "README.md"]).code, 0);
});

test("CLI: --files-json and --window", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bmw-"));
  try {
    const files = path.join(dir, "files.json");
    fs.writeFileSync(files, JSON.stringify([{ filename: "agents-md/base.md" }]));
    const win = path.join(dir, "win.yml");
    fs.writeFileSync(win, "timezone: UTC\nweekday: 3\nstart: \"01:00\"\nend: \"02:00\"\n");
    assert.equal(run(["--now", MON_OPEN, "--files-json", files]).code, 0);
    assert.equal(run(["--now", MON_OPEN, "--files-json", files, "--window", win]).code, 2);
    assert.equal(run(["--now", "2026-11-11T01:30:00Z", "--files-json", files, "--window", win]).code, 0);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("CLI: no --now, a bad instant, an unknown flag or an unreadable window is exit 1", () => {
  assert.equal(run(["--files", "agents-md/base.md"]).code, 1);
  assert.equal(run(["--now", "yesterday", "--files", "agents-md/base.md"]).code, 1);
  assert.equal(run(["--now", MON_OPEN, "--bogus"]).code, 1);
  assert.equal(run(["--now", MON_OPEN, "--files", "x", "--window", "/nonexistent/win.yml"]).code, 1);
});

test("the fleet worker's prompt names the check and the ADR", () => {
  const doc = fs.readFileSync(path.join(ROOT, "docs", "routines", "fleet-worker.md"), "utf8");
  assert.ok(doc.includes("scripts/base-md-merge-window.js"));
  assert.ok(doc.includes("0039-"));
});
