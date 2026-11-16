'use strict';
// node:test suite for scripts/guidance-overlap.js. Run by test/run-tests.sh
// (test_guidance_overlap). Fixtures are written to a temp dir per test; no
// network, no clock.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { main } = require('../scripts/guidance-overlap.js');

const SCRIPT_ARGS = ['node', 'guidance-overlap.js'];

const PIN_RULE = 'Workflows name third-party actions by release tag and a lockfile locks each tag to an exact commit, so the runner runs the locked commit even when the tag moves; write the exact tag by hand and regenerate the lockfile with the tool, then commit both files together.';
const PRIVATE_RULE = 'The nightly backup copies the private vault folder to the external drive with restic and prunes snapshots older than ninety days unless the owner pinned one by hand in the config file.';
const BASE_RULE = 'Never print personal or sensitive data to a log, including emails, contacts, names, identifiers, mailbox counts and tokens; deliver results out of band and log only a status that identifies nobody.';

function agents(additions) {
  return [
    '<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->',
    '# AGENTS.md',
    '',
    'Managed text.',
    '<!-- END MANAGED SECTION -->',
    '',
    '## Repo-specific additions',
    '',
    additions,
    '',
  ].join('\n');
}

function fixture(files, base = `# Base\n\n## Data exposure\n\n${BASE_RULE}\n`) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'overlap-'));
  const input = path.join(dir, 'in');
  fs.mkdirSync(input);
  for (const [name, body] of Object.entries(files)) fs.writeFileSync(path.join(input, name), body);
  const basePath = path.join(dir, 'base.md');
  fs.writeFileSync(basePath, base);
  const sectionsDir = path.join(dir, 'sections');
  fs.mkdirSync(sectionsDir);
  fs.writeFileSync(path.join(sectionsDir, 'python.md'), '## Python\n\nUse a virtual environment.\n');
  return { input, basePath, sectionsDir };
}

function run(f) {
  let out = '';
  const orig = process.stdout.write;
  process.stdout.write = (s) => { out += s; return true; };
  let rc;
  try {
    rc = main([...SCRIPT_ARGS, f.input, '--base', f.basePath, '--sections-dir', f.sectionsDir]);
  } finally {
    process.stdout.write = orig;
  }
  return { rc, out };
}

function sectionOf(out, heading) {
  const start = out.indexOf(`### ${heading}`);
  assert.ok(start >= 0, `missing heading ${heading}`);
  const next = out.indexOf('\n### ', start + 4);
  return out.slice(start, next < 0 ? undefined : next);
}

test('a paragraph repeated in two repos is reported once, naming both', () => {
  const { rc, out } = run(fixture({
    'own__one.agents.md': agents(`- ${PIN_RULE}`),
    'own__two.agents.md': agents(`Intro line.\n\n${PIN_RULE}`),
    'own__three.agents.md': agents(PRIVATE_RULE),
  }));
  assert.strictEqual(rc, 0);
  const s = sectionOf(out, 'Repeated across repos');
  assert.match(s, /\(1\)/);
  assert.match(s, /2 repos/);
  assert.match(s, /own\/one/);
  assert.match(s, /own\/two/);
  assert.doesNotMatch(s, /own\/three/);
});

test('a repeat inside one repo is not a cross-repo finding', () => {
  const { out } = run(fixture({
    'own__one.agents.md': agents(`${PIN_RULE}\n\n${PIN_RULE}`),
    'own__two.agents.md': agents(PRIVATE_RULE),
  }));
  assert.match(sectionOf(out, 'Repeated across repos'), /\(0\)[\s\S]*None\./);
});

test('a local copy of base.md is reported with the base.md heading', () => {
  const { out } = run(fixture({ 'own__one.agents.md': agents(`Our rule: ${BASE_RULE}`) }));
  const s = sectionOf(out, 'Local copies of shared guidance');
  assert.match(s, /\(1\)/);
  assert.match(s, /agents-md\/base\.md` "Data exposure"/);
});

test('the managed block above the marker is never compared', () => {
  // The base rule sits in the managed half only; the additions are unrelated.
  const body = agents(PRIVATE_RULE).replace('Managed text.', BASE_RULE);
  const { out } = run(fixture({ 'own__one.agents.md': body }));
  assert.match(sectionOf(out, 'Local copies of shared guidance'), /\(0\)/);
});

test('the marker quoted in the managed header is not taken as the split point', () => {
  // Only the whole-line marker counts; line numbers point past it.
  const { out } = run(fixture({
    'own__one.agents.md': agents(PIN_RULE),
    'own__two.agents.md': agents(PIN_RULE),
  }));
  assert.match(out, /own\/one` L9\]/);
});

test('blocks shorter than the minimum are ignored', () => {
  const short = 'Run the tests before you push the branch.';
  const { out } = run(fixture({
    'own__one.agents.md': agents(short),
    'own__two.agents.md': agents(short),
  }));
  assert.match(out, /Compared 0 blocks/);
});

test('repo text is never quoted in the output', () => {
  const { out } = run(fixture({
    'own__one.agents.md': agents(PRIVATE_RULE),
    'own__two.agents.md': agents(PRIVATE_RULE),
  }));
  assert.match(sectionOf(out, 'Repeated across repos'), /\(1\)/);
  assert.doesNotMatch(out, /restic|vault|ninety/);
});

test('a section requested for a language the repo has none of is reported', () => {
  const { out } = run(fixture({
    'own__one.agents.md': agents(PRIVATE_RULE),
    'own__one.sections': 'python\n',
    'own__one.languages.json': JSON.stringify({ JavaScript: 1000 }),
    'own__two.agents.md': agents(PIN_RULE),
    'own__two.sections': 'python\n',
    'own__two.languages.json': JSON.stringify({ Python: 10 }),
  }));
  const s = sectionOf(out, 'Sections requested for a language the repo does not use');
  assert.match(s, /\(1\)/);
  assert.match(s, /own\/one` requests `python`/);
  assert.doesNotMatch(s, /own\/two/);
});

test('a repo with sections whose languages were not read is named', () => {
  const { out } = run(fixture({
    'own__one.agents.md': agents(PRIVATE_RULE),
    'own__one.sections': 'python\n',
  }));
  assert.match(out, /Languages not read for: `own\/one`/);
});

test('an unreadable base.md is said, not silently skipped', () => {
  const f = fixture({ 'own__one.agents.md': agents(PRIVATE_RULE) });
  fs.unlinkSync(f.basePath);
  const { rc, out } = run(f);
  assert.strictEqual(rc, 0);
  assert.match(out, /base\.md could not be read/);
});

test('a missing input directory is a usage error, exit 2', () => {
  const f = fixture({});
  fs.rmSync(f.input, { recursive: true });
  const orig = process.stderr.write;
  process.stderr.write = () => true;
  try {
    assert.strictEqual(main([...SCRIPT_ARGS, f.input, '--base', f.basePath]), 2);
  } finally {
    process.stderr.write = orig;
  }
});
