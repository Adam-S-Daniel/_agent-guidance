"use strict";
// test-eval-protected.js — node:test coverage for scripts/eval-protected.js.
// Every fixture is invented (test/fixtures/eval-protected/): no network, no
// Date.now(), no sleeps. CLI tests shell out to the script with files written
// under the OS tmpdir. Run via test/run-tests.sh's test_eval_protected, which
// requires `# fail 0` and a minimum `# pass` count.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { isProtected, decidePr, parseCoverage, h2Headings, patchLines } = require("../scripts/eval-protected.js");

const SCRIPT = path.join(__dirname, "..", "scripts", "eval-protected.js");
const FIX = path.join(__dirname, "fixtures", "eval-protected");
const EXPLORER = JSON.parse(fs.readFileSync(path.join(FIX, "explorer.json"), "utf8"));
const COVERAGE_PATH = path.join(FIX, "coverage.yml");
const COVERAGE = parseCoverage(fs.readFileSync(COVERAGE_PATH, "utf8"));
const BASE_MD_PATH = path.join(FIX, "base.md");
const BASE_MD = fs.readFileSync(BASE_MD_PATH, "utf8");

const SKILLS = "Adam-S-Daniel/adam-agentskills";
const GUIDANCE = "Adam-S-Daniel/_agent-guidance";

// base.md line numbers: 3 Alpha heading, 8 Beta, 12 Gamma, 20 Orphan.
const PATCH_ALPHA = "@@ -4,2 +4,3 @@\n a1\n+a1b\n a2";
const PATCH_PREAMBLE = "@@ -1,1 +1,2 @@\n+Intro0\n Intro line";
const PATCH_DELETE_GAMMA = "@@ -12,3 +12,2 @@\n ## Gamma section\n-g0\n g1";
const PATCH_SPAN = "@@ -9,6 +9,6 @@\n b1\n-b2\n+b2n\n \n ## Gamma section\n-g0\n+g1x";
const PATCH_ORPHAN = "@@ -20,2 +20,2 @@\n ## Orphan section\n-o0\n+o1";

function run(overrides) {
  return decidePr({
    explorer: EXPLORER,
    repo: SKILLS,
    files: [],
    body: "",
    baseMd: BASE_MD,
    coverage: COVERAGE,
    ...overrides,
  });
}

function skillFile(name, file, extra) {
  return { filename: `plugins/example-bundle/skills/${name}/${file}`, status: "modified", patch: "@@ -1 +1 @@\n-a\n+b", ...extra };
}

function marker(subject, cls = "wrong-command", dash = "—") {
  return `Summary\n\nCorrectness fix: ${subject} ${dash} ${cls}: the path was wrong\n`;
}

function reasonMatches(result, fragment) {
  assert.ok(result.reasons.some((r) => r.includes(fragment)), `expected a reason containing "${fragment}", got ${JSON.stringify(result.reasons)}`);
}

// ── isProtected ────────────────────────────────────────────────────────────

test("isProtected: retire is not protected even with runs", () => {
  assert.equal(isProtected({ verdict: "retire", current_text_runs: 5 }), false);
});

test("isProtected: shorten-or-merge is not protected even with runs", () => {
  assert.equal(isProtected({ verdict: "shorten-or-merge", current_text_runs: 5 }), false);
});

test("isProtected: keep is protected with zero runs", () => {
  assert.equal(isProtected({ verdict: "keep", current_text_runs: 0 }), true);
  assert.equal(isProtected({ verdict: "keep" }), true);
});

test("isProtected: other verdict with runs is protected", () => {
  assert.equal(isProtected({ verdict: null, current_text_runs: 1 }), true);
  assert.equal(isProtected({ verdict: "insufficient", current_text_runs: 2 }), true);
});

test("isProtected: no runs, missing, or non-integer runs is not protected", () => {
  assert.equal(isProtected({ verdict: null, current_text_runs: 0 }), false);
  assert.equal(isProtected({ verdict: null }), false);
  assert.equal(isProtected({ verdict: null, current_text_runs: "3" }), false);
  assert.equal(isProtected({ verdict: null, current_text_runs: 1.5 }), false);
  assert.equal(isProtected({ verdict: null, current_text_runs: -1 }), false);
});

// ── parsing helpers ────────────────────────────────────────────────────────

test("h2Headings ignores a heading-looking line inside a fence", () => {
  assert.deepEqual(
    h2Headings(BASE_MD).map((h) => [h.line, h.text]),
    [
      [3, "Alpha section"],
      [8, "Beta section"],
      [12, "Gamma section"],
      [20, "Orphan section"],
    ],
  );
});

test("patchLines: pure deletion yields the new-side position and the line before", () => {
  assert.deepEqual(patchLines("@@ -5,2 +5,1 @@\n a\n-b"), [6, 5]);
});

// ── skill path mapping ─────────────────────────────────────────────────────

test("skill: edit to a protected skill without a marker is refused", () => {
  const r = run({ files: [skillFile("example-runs", "SKILL.md")] });
  assert.equal(r.merge, false);
  assert.deepEqual(r.touched, ["example-runs"]);
  assert.deepEqual(r.protected, ["example-runs"]);
  assert.deepEqual(r.unmarked, ["example-runs"]);
  reasonMatches(r, "example-runs is eval-protected");
});

test("skill: a PURPOSE.md in a skill dir moves the digest, so it counts", () => {
  const r = run({ files: [skillFile("example-keep", "PURPOSE.md", { status: "added" })] });
  assert.equal(r.merge, false);
  assert.deepEqual(r.touched, ["example-keep"]);
});

test("skill: a nested file under a skill dir counts", () => {
  const r = run({ files: [skillFile("example-runs", "scripts/helper.py")] });
  assert.deepEqual(r.touched, ["example-runs"]);
});

test("skill: a rename out of a skill dir touches the old skill", () => {
  const r = run({
    files: [{ filename: "docs/moved.md", previous_filename: "plugins/example-bundle/skills/example-runs/SKILL.md", status: "renamed", patch: "" }],
  });
  assert.deepEqual(r.touched, ["example-runs"]);
  assert.equal(r.merge, false);
});

test("skill: marker accepted with each dash form", () => {
  for (const dash of ["—", "-", "--"]) {
    const r = run({ files: [skillFile("example-runs", "SKILL.md")], body: marker("example-runs", "broken-path", dash) });
    assert.equal(r.merge, true, `dash ${dash}`);
    assert.deepEqual(r.unmarked, []);
  }
});

test("skill: every defined class is accepted", () => {
  for (const cls of ["wrong-command", "broken-path", "contradiction", "safety-gap"]) {
    assert.equal(run({ files: [skillFile("example-runs", "SKILL.md")], body: marker("example-runs", cls) }).merge, true, cls);
  }
});

test("skill: a wrong class is rejected", () => {
  const r = run({ files: [skillFile("example-runs", "SKILL.md")], body: marker("example-runs", "typo-fix") });
  assert.equal(r.merge, false);
  assert.deepEqual(r.unmarked, ["example-runs"]);
});

test("skill: a marker with no text is rejected", () => {
  const r = run({ files: [skillFile("example-runs", "SKILL.md")], body: "Correctness fix: example-runs — wrong-command: \n" });
  assert.equal(r.merge, false);
});

test("skill: a marker naming a different subject is rejected", () => {
  const r = run({ files: [skillFile("example-runs", "SKILL.md")], body: marker("example-keep") });
  assert.equal(r.merge, false);
  assert.deepEqual(r.unmarked, ["example-runs"]);
});

test("skill: a marker for a subject prefix does not match", () => {
  const r = run({ files: [skillFile("example-runs", "SKILL.md")], body: marker("example-run") });
  assert.equal(r.merge, false);
});

test("skill: an unprotected skill needs no marker", () => {
  const r = run({ files: [skillFile("example-plain", "SKILL.md")] });
  assert.equal(r.merge, true);
  assert.deepEqual(r.touched, ["example-plain"]);
  assert.deepEqual(r.protected, []);
});

test("skill: a skill missing from the explorer needs no marker", () => {
  const r = run({ files: [skillFile("example-unknown", "SKILL.md")] });
  assert.equal(r.merge, true);
  assert.deepEqual(r.touched, ["example-unknown"]);
});

test("skill: a retire skill needs no marker", () => {
  assert.equal(run({ files: [skillFile("example-retire", "SKILL.md")] }).merge, true);
});

test("skill: a shorten-or-merge skill needs no marker", () => {
  assert.equal(run({ files: [skillFile("example-cut", "SKILL.md")] }).merge, true);
});

test("skill: a skill in another registry is not matched", () => {
  assert.equal(run({ files: [skillFile("example-other", "SKILL.md")] }).merge, true);
});

test("skill: a missing patch refuses with a reason", () => {
  const r = run({ files: [skillFile("example-plain", "assets/logo.png", { patch: undefined })] });
  assert.equal(r.merge, false);
  reasonMatches(r, "has no patch");
});

test("skill: all reasons are collected", () => {
  const r = run({ files: [skillFile("example-runs", "SKILL.md"), skillFile("example-keep", "SKILL.md")] });
  assert.equal(r.reasons.length, 2);
  assert.deepEqual(r.unmarked, ["example-keep", "example-runs"]);
});

test("skill: one marker per subject; marking one leaves the other unmarked", () => {
  const r = run({ files: [skillFile("example-runs", "SKILL.md"), skillFile("example-keep", "SKILL.md")], body: marker("example-keep") });
  assert.deepEqual(r.unmarked, ["example-runs"]);
});

test("skill: CRLF bodies and indented markers are accepted", () => {
  const body = "Intro\r\n  Correctness fix: example-runs - contradiction: two rules disagree\r\n";
  assert.equal(run({ files: [skillFile("example-runs", "SKILL.md")], body }).merge, true);
});

// ── other repos and paths ──────────────────────────────────────────────────

test("other repo: protected-looking paths map to nothing", () => {
  const r = run({ repo: "Adam-S-Daniel/example-repo", files: [skillFile("example-runs", "SKILL.md")] });
  assert.equal(r.merge, true);
  assert.deepEqual(r.touched, []);
});

test("skills repo: a path outside a skill dir maps to nothing", () => {
  const r = run({ files: [{ filename: "docs/notes.md", status: "modified", patch: "@@ -1 +1 @@\n-a\n+b" }, { filename: "plugins/example-bundle/README.md", status: "modified", patch: "x" }] });
  assert.equal(r.merge, true);
  assert.deepEqual(r.touched, []);
});

test("guidance repo: a skill-shaped path maps to nothing", () => {
  const r = run({ repo: GUIDANCE, files: [skillFile("example-runs", "SKILL.md")] });
  assert.deepEqual(r.touched, []);
});

// ── sections/<x>.md ────────────────────────────────────────────────────────

test("sections file: maps through the coverage row's file", () => {
  const r = run({ repo: GUIDANCE, files: [{ filename: "agents-md/sections/go.md", status: "modified", patch: "@@ -1 +1 @@\n-a\n+b" }] });
  assert.deepEqual(r.touched, ["go"]);
  assert.equal(r.merge, true);
});

test("sections file: a protected section needs a marker", () => {
  const explorer = JSON.parse(JSON.stringify(EXPLORER));
  explorer.guidance.sections.find((s) => s.id === "docker").current_text_runs = 2;
  const files = [{ filename: "agents-md/sections/docker.md", status: "modified", patch: "@@ -1 +1 @@\n-a\n+b" }];
  const refusedResult = run({ explorer, repo: GUIDANCE, files });
  assert.equal(refusedResult.merge, false);
  assert.deepEqual(refusedResult.unmarked, ["docker"]);
  assert.equal(run({ explorer, repo: GUIDANCE, files, body: marker("docker", "safety-gap") }).merge, true);
});

test("sections file: a non-integer current_text_runs counts as 0", () => {
  const r = run({ repo: GUIDANCE, files: [{ filename: "agents-md/sections/docker.md", status: "modified", patch: "@@ -1 +1 @@\n-a\n+b" }] });
  assert.equal(r.merge, true);
});

test("sections file: no coverage row is unmapped and refuses", () => {
  const r = run({ repo: GUIDANCE, files: [{ filename: "agents-md/sections/example-new.md", status: "added", patch: "@@ -0,0 +1 @@\n+a" }] });
  assert.equal(r.merge, false);
  assert.deepEqual(r.unmapped, ["agents-md/sections/example-new.md"]);
});

test("sections file: a missing patch refuses", () => {
  const r = run({ repo: GUIDANCE, files: [{ filename: "agents-md/sections/go.md", status: "modified" }] });
  assert.equal(r.merge, false);
  reasonMatches(r, "has no patch");
});

// ── base.md hunk mapping ───────────────────────────────────────────────────

function baseFile(patch) {
  return { filename: "agents-md/base.md", status: "modified", patch };
}

test("base.md: an added line maps to its enclosing section", () => {
  const r = run({ repo: GUIDANCE, files: [baseFile(PATCH_ALPHA)] });
  assert.deepEqual(r.touched, ["alpha"]);
  assert.deepEqual(r.protected, ["alpha"]);
  assert.equal(r.merge, false);
});

test("base.md: a marked edit to the right section passes", () => {
  const r = run({ repo: GUIDANCE, files: [baseFile(PATCH_ALPHA)], body: marker("alpha", "contradiction") });
  assert.equal(r.merge, true);
});

test("base.md: a marker for another section does not cover it", () => {
  const r = run({ repo: GUIDANCE, files: [baseFile(PATCH_ALPHA)], body: marker("beta") });
  assert.equal(r.merge, false);
});

test("base.md: lines before the first heading map to nothing", () => {
  const r = run({ repo: GUIDANCE, files: [baseFile(PATCH_PREAMBLE)] });
  assert.deepEqual(r.touched, []);
  assert.equal(r.merge, true);
});

test("base.md: a pure-deletion hunk maps to the section it shrinks", () => {
  const r = run({ repo: GUIDANCE, files: [baseFile(PATCH_DELETE_GAMMA)] });
  assert.deepEqual(r.touched, ["gamma"]);
  assert.equal(r.merge, true, "gamma is unprotected");
});

test("base.md: a hunk spanning two sections touches both", () => {
  const r = run({ repo: GUIDANCE, files: [baseFile(PATCH_SPAN)] });
  assert.deepEqual(r.touched, ["beta", "gamma"]);
  assert.deepEqual(r.protected, ["beta"]);
  assert.deepEqual(r.unmarked, ["beta"]);
});

test("base.md: an unprotected section needs no marker", () => {
  const r = run({ repo: GUIDANCE, files: [baseFile("@@ -13,1 +13,2 @@\n g1\n+g1b")] });
  assert.deepEqual(r.touched, ["gamma"]);
  assert.equal(r.merge, true);
});

test("base.md: a retire section needs no marker", () => {
  const md = "## Delta section\nd1\n";
  const r = run({ repo: GUIDANCE, baseMd: md, files: [baseFile("@@ -1,2 +1,3 @@\n ## Delta section\n d1\n+d2")] });
  assert.deepEqual(r.touched, ["delta"]);
  assert.equal(r.merge, true);
});

test("base.md: a heading with no coverage row is unmapped and refuses", () => {
  const r = run({ repo: GUIDANCE, files: [baseFile(PATCH_ORPHAN)] });
  assert.equal(r.merge, false);
  assert.deepEqual(r.unmapped, ["Orphan section"]);
  reasonMatches(r, "no coverage row");
});

test("base.md: a heading-looking line in a fence does not start a section", () => {
  // Line 17 is inside the fence after Gamma, so it belongs to Gamma.
  const r = run({ repo: GUIDANCE, files: [baseFile("@@ -17,1 +17,2 @@\n ## Not a heading\n+x")] });
  assert.deepEqual(r.touched, ["gamma"]);
});

test("base.md: a missing patch refuses", () => {
  const r = run({ repo: GUIDANCE, files: [{ filename: "agents-md/base.md", status: "modified" }] });
  assert.equal(r.merge, false);
  reasonMatches(r, "has no patch");
});

test("base.md: no --base-md is a usage error", () => {
  assert.throws(() => run({ repo: GUIDANCE, baseMd: undefined, files: [baseFile(PATCH_ALPHA)] }), /--base-md is required/);
});

test("base.md: unmapped and protected reasons are both collected", () => {
  const patch = `${PATCH_ALPHA}\n${PATCH_ORPHAN}`;
  const r = run({ repo: GUIDANCE, files: [baseFile(patch)] });
  assert.equal(r.reasons.length, 2);
});

// ── bad input ──────────────────────────────────────────────────────────────

test("decidePr: malformed explorer or files throw", () => {
  assert.throws(() => run({ explorer: {} }), /bad input/);
  assert.throws(() => run({ explorer: { skills: [], guidance: {} } }), /bad input/);
  assert.throws(() => run({ files: {} }), /bad input/);
  assert.throws(() => run({ files: [{}] }), /bad input/);
});

test("parseCoverage: rejects non-list and incomplete rows", () => {
  assert.throws(() => parseCoverage("a: 1"), /bad input/);
  assert.throws(() => parseCoverage("- id: x"), /bad input/);
  assert.throws(() => parseCoverage("- [unclosed"), /bad input/);
});

// ── CLI ────────────────────────────────────────────────────────────────────

function withTmp(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "eval-protected-"));
  try {
    return fn(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function cli(args) {
  const r = spawnSync(process.execPath, [SCRIPT, ...args], { encoding: "utf8" });
  return { code: r.status, out: r.stdout, err: r.stderr };
}

function writeJson(dir, name, value) {
  const p = path.join(dir, name);
  fs.writeFileSync(p, JSON.stringify(value));
  return p;
}

const EXPLORER_PATH = path.join(FIX, "explorer.json");

test("cli list: protected subjects only, sorted", () => {
  const r = cli(["list", "--explorer", EXPLORER_PATH]);
  assert.equal(r.code, 0);
  assert.deepEqual(JSON.parse(r.out), {
    skills: [
      { name: "example-keep", registry: "adam-agentskills", verdict: "keep", current_text_runs: 0 },
      { name: "example-other", registry: "example-private-registry", verdict: "keep", current_text_runs: 4 },
      { name: "example-runs", registry: "adam-agentskills", verdict: null, current_text_runs: 2 },
    ],
    sections: [
      { id: "alpha", heading: "Alpha section", verdict: "keep", current_text_runs: 1 },
      { id: "beta", heading: "Beta section", verdict: null, current_text_runs: 2 },
    ],
  });
});

test("cli list: a skill with a missing current_text_runs is read as 0", () => {
  withTmp((dir) => {
    const p = writeJson(dir, "e.json", {
      skills: [{ name: "example-a", registry: "adam-agentskills", verdict: "insufficient" }],
      guidance: { sections: [] },
    });
    const r = cli(["list", "--explorer", p]);
    assert.equal(r.code, 0);
    assert.deepEqual(JSON.parse(r.out), { skills: [], sections: [] });
  });
});

test("cli pr: refused exits 2 and allowed exits 0", () => {
  withTmp((dir) => {
    const files = writeJson(dir, "files.json", [skillFile("example-runs", "SKILL.md")]);
    const bad = writeJson(dir, "pr-bad.json", { body: "nothing" });
    const good = writeJson(dir, "pr-good.json", { body: marker("example-runs") });
    const common = ["pr", "--explorer", EXPLORER_PATH, "--repo", SKILLS, "--files", files, "--coverage", COVERAGE_PATH];
    const refusedRun = cli([...common, "--body", bad]);
    assert.equal(refusedRun.code, 2);
    assert.equal(JSON.parse(refusedRun.out).merge, false);
    const allowedRun = cli([...common, "--body", good]);
    assert.equal(allowedRun.code, 0);
    assert.equal(JSON.parse(allowedRun.out).merge, true);
  });
});

test("cli pr: base.md with --base-md, and a null body is read as empty", () => {
  withTmp((dir) => {
    const files = writeJson(dir, "files.json", [baseFile(PATCH_ALPHA)]);
    const pr = writeJson(dir, "pr.json", { body: null });
    const r = cli(["pr", "--explorer", EXPLORER_PATH, "--repo", GUIDANCE, "--files", files, "--body", pr, "--base-md", BASE_MD_PATH, "--coverage", COVERAGE_PATH]);
    assert.equal(r.code, 2);
    assert.deepEqual(JSON.parse(r.out).touched, ["alpha"]);
  });
});

test("cli pr: base.md changed without --base-md exits 1", () => {
  withTmp((dir) => {
    const files = writeJson(dir, "files.json", [baseFile(PATCH_ALPHA)]);
    const pr = writeJson(dir, "pr.json", { body: "" });
    const r = cli(["pr", "--explorer", EXPLORER_PATH, "--repo", GUIDANCE, "--files", files, "--body", pr, "--coverage", COVERAGE_PATH]);
    assert.equal(r.code, 1);
    assert.match(r.err, /--base-md is required/);
  });
});

test("cli pr: the default coverage file is this repo's real one", () => {
  withTmp((dir) => {
    const files = writeJson(dir, "files.json", []);
    const pr = writeJson(dir, "pr.json", { body: "" });
    const r = cli(["pr", "--explorer", EXPLORER_PATH, "--repo", GUIDANCE, "--files", files, "--body", pr]);
    assert.equal(r.code, 0);
  });
});

test("cli: a malformed explorer file exits 1 without echoing its contents", () => {
  withTmp((dir) => {
    const bad = path.join(dir, "e.json");
    fs.writeFileSync(bad, "{ SECRET-CONTENT");
    for (const args of [["list", "--explorer", bad], ["pr", "--explorer", bad, "--repo", SKILLS, "--files", bad, "--body", bad]]) {
      const r = cli(args);
      assert.equal(r.code, 1);
      assert.ok(!r.err.includes("SECRET-CONTENT"));
      assert.equal(r.out, "");
    }
  });
});

test("cli: a well-formed JSON explorer of the wrong shape exits 1", () => {
  withTmp((dir) => {
    const p = writeJson(dir, "e.json", { skills: "nope" });
    assert.equal(cli(["list", "--explorer", p]).code, 1);
  });
});

test("cli: an unreadable explorer file exits 1", () => {
  assert.equal(cli(["list", "--explorer", path.join(os.tmpdir(), "eval-protected-missing-file.json")]).code, 1);
});

test("cli: usage errors exit 1", () => {
  assert.equal(cli([]).code, 1);
  assert.equal(cli(["bogus"]).code, 1);
  assert.equal(cli(["list"]).code, 1);
  assert.equal(cli(["list", "--explorer", EXPLORER_PATH, "--repo", SKILLS]).code, 1);
  assert.equal(cli(["list", "--explorer"]).code, 1);
  assert.equal(cli(["list", "--explorer", EXPLORER_PATH, "--explorer", EXPLORER_PATH]).code, 1);
  assert.equal(cli(["list", "--nope", "x"]).code, 1);
  assert.equal(cli(["pr", "--explorer", EXPLORER_PATH]).code, 1);
});
