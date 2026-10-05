"use strict";
// test-check-agent-markdown.js — node:test coverage for
// scripts/check-agent-markdown.js. Every fixture is a temp tree; no network,
// clock or sleeps. The tables are the contract: each rule has rows that must
// WARN (and so exit 1 under --strict) and rows that must stay quiet, so the
// checker is shown able to fail for each reason and to pass. Run via
// test/run-tests.sh's test_check_agent_markdown, which requires `# fail 0`
// and a minimum `# pass` count.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const YAML = require("yaml");

const SCRIPT = path.join(__dirname, "..", "scripts", "check-agent-markdown.js");
const REPO = path.join(__dirname, "..");
const { run } = require(SCRIPT);

function tree(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agentmd-"));
  for (const [rel, body] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    fs.writeFileSync(path.join(root, rel), body);
  }
  return root;
}

const rules = (r) => r.findings.map((f) => `${f.rule}@${f.line}`);

// ── Rule 1: British spellings ──────────────────────────────────────────────

const SPELLING_WARN = [
  ["-our family", "Check the behaviour here.\n", ["british-spelling@1"]],
  ["-our, capitalized", "Colour matters.\n", ["british-spelling@1"]],
  ["-our derived form", "An honourable exit.\n", ["british-spelling@1"]],
  ["favour", "We favour tests.\n", ["british-spelling@1"]],
  ["-ise verb", "Please organise the files.\n", ["british-spelling@1"]],
  ["-isation noun", "The organisation owns it.\n", ["british-spelling@1"]],
  ["recognise", "We recognise the form.\n", ["british-spelling@1"]],
  ["analyse", "Analyse the log.\n", ["british-spelling@1"]],
  ["catalogue", "See the catalogue.\n", ["british-spelling@1"]],
  ["centre", "Centre the text.\n", ["british-spelling@1"]],
  ["licence", "The licence file.\n", ["british-spelling@1"]],
  ["summarise", "Summarise the diff.\n", ["british-spelling@1"]],
  ["in a heading", "# The behaviour\n", ["british-spelling@1"]],
  ["in a list item", "- first\n- the colour red\n", ["british-spelling@2"]],
  ["in a table cell", "| a | b |\n|---|---|\n| x | colour |\n", ["british-spelling@3"]],
  ["in link text", "[behaviour](https://example.com/x)\n", ["british-spelling@1"]],
  ["on the second line of a paragraph", "fine line\nthe colour here\n", ["british-spelling@2"]],
  ["after a code fence closes", "```\ncolour\n```\n\ncolour\n", ["british-spelling@5"]],
  ["two words, one line", "behaviour and colour\n", ["british-spelling@1", "british-spelling@1"]],
];

const SPELLING_QUIET = [
  ["American spellings", "The behavior, color, honor, organize and analyze.\n"],
  ["exercise/promise/otherwise are not flagged", "Exercise the promise otherwise.\n"],
  ["analyses (American plural) is not flagged", "Two analyses agree.\n"],
  ["cancelled (API value) is not flagged", "The run was cancelled.\n"],
  ["fenced code", "```\nthe colour and behaviour\n```\n"],
  ["fenced code with a language", "```yaml\ncolour: red\n```\n"],
  ["indented code", "text\n\n    colour = 1\n"],
  ["inline code", "Set `colour` to red.\n"],
  ["block quote", "> The vendor writes behaviour here.\n"],
  ["nested block quote", "> > colour\n"],
  ["lazy block quote continuation", "> quoted\ncolour continues the quote\n"],
  ["URL in prose", "See https://example.com/colour/behaviour for it.\n"],
  ["autolink", "See <https://example.com/organisation>.\n"],
  ["link target", "[docs](https://example.com/behaviour)\n"],
  ["HTML comment", "<!-- colour -->\n"],
  ["whole-word match only", "xcolour colourx behavioral.\n"],
];

for (const [name, body, want] of SPELLING_WARN) {
  test(`spelling warns: ${name}`, () => {
    const root = tree({ "AGENTS.md": body });
    const warn = run(root);
    assert.equal(warn.code, 0, "warn-only exits 0");
    assert.deepEqual(rules(warn), want);
    const strict = run(root, { strict: true });
    assert.equal(strict.code, 1, "--strict exits 1");
  });
}

for (const [name, body] of SPELLING_QUIET) {
  test(`spelling quiet: ${name}`, () => {
    const root = tree({ "AGENTS.md": body });
    assert.deepEqual(rules(run(root, { strict: true })), []);
    assert.equal(run(root, { strict: true }).code, 0);
  });
}

// ── Allowlist ──────────────────────────────────────────────────────────────

test("default allowlist silences a word, case-insensitively, with comments", () => {
  const root = tree({
    "AGENTS.md": "A Grey area and colour.\n",
    "docs/agent-markdown-allowlist.txt": "# vendor quote\nGREY  # a name\n\n",
  });
  assert.deepEqual(rules(run(root, { strict: true })), ["british-spelling@1"]);
  assert.match(run(root).findings[0].message, /"colour"/);
});

test("allowlisting one word does not silence another", () => {
  const root = tree({ "AGENTS.md": "colour behaviour\n", "docs/agent-markdown-allowlist.txt": "colour\n" });
  assert.match(run(root).findings[0].message, /"behaviour"/);
  assert.equal(run(root).findings.length, 1);
});

test("an explicit --allowlist file replaces the default location", () => {
  const root = tree({ "AGENTS.md": "colour\n", "elsewhere.txt": "colour\n" });
  const r = run(root, { strict: true, allowlist: path.join(root, "elsewhere.txt") });
  assert.deepEqual([r.code, r.findings.length], [0, 0]);
});

test("a missing explicit --allowlist is exit 2, not a silent empty list", () => {
  const root = tree({ "AGENTS.md": "fine\n" });
  assert.equal(run(root, { allowlist: path.join(root, "nope.txt") }).code, 2);
});

// ── Rule 2: dangling "see X above/below" ───────────────────────────────────

const REF_WARN = [
  ["quoted name, no such heading", '# Top\n\nSee "Missing" above.\n', ["dangling-reference@3"]],
  ["curly quotes", "# Top\n\nsee “Missing” below.\n", ["dangling-reference@3"]],
  ["backticked heading marker", "# Top\n\nsee `## Missing` below.\n", ["dangling-reference@3"]],
  ["bold name", "# Top\n\nsee **Missing** above.\n", ["dangling-reference@3"]],
  ["italic name", "# Top\n\nsee *Missing* above.\n", ["dangling-reference@3"]],
  ["link text", "# Top\n\nsee [Missing](#missing) below.\n", ["dangling-reference@3"]],
  ["with 'the' and 'section'", '# Top\n\nSee the "Missing" section above.\n', ["dangling-reference@3"]],
  ["with 'also'", '# Top\n\nSee also "Missing" below.\n', ["dangling-reference@3"]],
  ["inside a list item", '# Top\n\n- one\n- (see "Missing" above)\n', ["dangling-reference@4"]],
  ["on a later line of the paragraph", '# Top\n\nline one\nand see "Missing"\nabove.\n', ["dangling-reference@4"]],
  [
    "a '## ' inside a fence is not a heading (real parser)",
    '# Top\n\n```\n## Ghost\n```\n\nsee "Ghost" above.\n',
    ["dangling-reference@7"],
  ],
  ["an indented heading-looking line is code", '# Top\n\n    ## Ghost\n\nsee "Ghost" above.\n', ["dangling-reference@5"]],
  ["a near miss is still dangling", '## The rule\n\nsee "The rules" above.\n', ["dangling-reference@3"]],
  ["two refs, one good one bad", '## Good\n\nsee "Good" above, see "Bad" below.\n', ["dangling-reference@3"]],
];

const REF_QUIET = [
  ["quoted name matches an h2", '## Setup\n\ntext\n\nsee "Setup" above.\n'],
  ["match is case-insensitive", '## Setup\n\nsee "setup" above.\n'],
  ["match ignores trailing colon and spacing", '## Setup:\n\nsee "Setup" above.\n'],
  ["matches an h3", '### Deep\n\nsee "Deep" above.\n'],
  ["a later heading satisfies 'below'", 'see "Later" below.\n\n## Later\n'],
  ["backticked name with ## marker", '## Setup\n\nsee `## Setup` above.\n'],
  ["a heading with inline code compares by rendered text", '## The `foo` flag\n\nsee "The foo flag" above.\n'],
  ["a setext heading counts", 'Setup\n=====\n\nsee "Setup" above.\n'],
  ["undelimited name is not guessed at", "## Top\n\nsee the rules above and the notes below.\n"],
  ["'above' without 'see' is not a reference", '## Top\n\nthe "Missing" thing is described above.\n'],
  ["inside a block quote", '## Top\n\n> see "Missing" above.\n'],
  ["inside a fence", '## Top\n\n```\nsee "Missing" above\n```\n'],
  ["inside inline code", '## Top\n\n`see "Missing" above`\n'],
];

for (const [name, body, want] of REF_WARN) {
  test(`reference warns: ${name}`, () => {
    const root = tree({ "AGENTS.md": body });
    const warn = run(root);
    assert.equal(warn.code, 0, "warn-only exits 0");
    assert.deepEqual(rules(warn), want);
    assert.equal(run(root, { strict: true }).code, 1);
  });
}

for (const [name, body] of REF_QUIET) {
  test(`reference quiet: ${name}`, () => {
    const root = tree({ "AGENTS.md": body });
    assert.deepEqual(rules(run(root, { strict: true })), []);
  });
}

test("a heading in ANOTHER file does not satisfy a reference", () => {
  const root = tree({ "AGENTS.md": '# A\n\nsee "Other" above.\n', "CLAUDE.md": "## Other\n" });
  assert.deepEqual(rules(run(root)), ["dangling-reference@3"]);
  assert.equal(run(root).findings[0].file, "AGENTS.md");
});

// ── Scope ──────────────────────────────────────────────────────────────────

test("scans exactly the agent-facing set, and skips the rest", () => {
  const bad = "colour\n";
  const root = tree({
    "AGENTS.md": bad,
    "CLAUDE.md": bad,
    "agents-md/base.md": bad,
    "agents-md/sections/go.md": bad,
    ".claude/hooks/fleet-guidance.md": bad,
    "plugins/p/skills/s/SKILL.md": bad,
    "SKILL.md": bad,
    // not agent-facing:
    "README.md": bad,
    "docs/notes.md": bad,
    "agents-md/eval-coverage.yml": "colour: x\n",
    "scripts/SKILL.md.bak": bad,
    "node_modules/pkg/SKILL.md": bad,
    ".claude/worktrees/w/AGENTS.md": bad,
    "nested/.git": "gitdir: elsewhere\n",
    "nested/AGENTS.md": bad,
  });
  const r = run(root);
  assert.deepEqual(
    r.findings.map((f) => f.file),
    [
      ".claude/hooks/fleet-guidance.md",
      "AGENTS.md",
      "CLAUDE.md",
      "SKILL.md",
      "agents-md/base.md",
      "agents-md/sections/go.md",
      "plugins/p/skills/s/SKILL.md",
    ]
  );
});

test("strict with no agent-facing Markdown at all is exit 2 (cannot certify)", () => {
  const root = tree({ "README.md": "colour\n" });
  assert.equal(run(root, { strict: true }).code, 2);
  assert.equal(run(root).code, 0);
});

// ── Output and CLI ─────────────────────────────────────────────────────────

test("emits GitHub warning annotations with file, line and title, then a summary", () => {
  const root = tree({ "agents-md/base.md": "ok\n\nthe colour\n" });
  const r = run(root);
  assert.match(
    r.lines[0],
    /^::warning file=agents-md\/base\.md,line=3,title=british-spelling::British spelling "colour"/
  );
  assert.equal(r.lines[1], "check-agent-markdown: 1 file(s), 1 warning(s) (warn-only)");
});

test("annotation messages escape %, CR and LF; properties escape : and ,", () => {
  const root = tree({ "agents-md/a,b:c.md": '# T\n\nsee "50%" above.\n' });
  const line = run(root).lines[0];
  assert.match(line, /^::warning file=agents-md\/a%2Cb%3Ac\.md,line=3,title=dangling-reference::/);
  assert.match(line, /see 50%25 above/);
  assert.ok(!/[\r\n]/.test(line));
});

function cli(root, args = []) {
  return spawnSync(process.execPath, [SCRIPT, "--root", root, ...args], { encoding: "utf8" });
}

test("CLI: findings exit 0 without --strict, 1 with it; stdout carries the annotations", () => {
  const root = tree({ "AGENTS.md": "colour\n" });
  const warn = cli(root);
  assert.equal(warn.status, 0);
  assert.match(warn.stdout, /^::warning file=AGENTS\.md,line=1/);
  const strict = cli(root, ["--strict"]);
  assert.equal(strict.status, 1);
  assert.match(strict.stdout, /\(strict\)/);
});

test("CLI: a clean tree exits 0 under --strict", () => {
  assert.equal(cli(tree({ "AGENTS.md": "# Fine\n\nAll good.\n" }), ["--strict"]).status, 0);
});

test("CLI: an unknown argument is exit 2", () => {
  assert.equal(cli(tree({ "AGENTS.md": "x\n" }), ["--bogus"]).status, 2);
});

// ── The word list itself ───────────────────────────────────────────────────

test("the fixed list holds the words the owner named, and no generic -ise", () => {
  const { BRITISH } = require(SCRIPT);
  for (const w of ["behaviour", "colour", "honour", "favour", "organise", "organisation", "recognise", "analyse", "catalogue", "centre", "licence"]) {
    assert.ok(BRITISH.has(w), `${w} should be listed`);
  }
  for (const w of ["exercise", "promise", "otherwise", "advise", "revise", "analyses", "cancelled", "judgement", "license"]) {
    assert.ok(!BRITISH.has(w), `${w} must not be listed`);
  }
});

// ── CI wiring ──────────────────────────────────────────────────────────────

test("ci.yml runs the checker as an unconditional, non-strict step that cannot fail the job", () => {
  const doc = YAML.parse(fs.readFileSync(path.join(REPO, ".github", "workflows", "ci.yml"), "utf8"));
  const steps = doc.jobs.test.steps.filter((s) => typeof s.run === "string" && s.run.includes("check-agent-markdown.js"));
  assert.equal(steps.length, 1, "exactly one step runs the checker");
  const [step] = steps;
  assert.equal(step.if, undefined, "no if: — it reports on every push and pull request");
  assert.ok(!/--strict/.test(step.run), "CI never passes --strict (warn-only, D5)");
  assert.equal(step["continue-on-error"], undefined, "exit 0 makes continue-on-error unnecessary");
  // A required check may not carry paths filters or a concurrency group.
  const on = doc.on || doc[true];
  for (const trigger of ["push", "pull_request"]) {
    assert.ok(!(on[trigger] && on[trigger].paths), `${trigger} has no paths filter`);
  }
  assert.equal(doc.concurrency, undefined);
  assert.equal(doc.jobs.test.concurrency, undefined);
});
