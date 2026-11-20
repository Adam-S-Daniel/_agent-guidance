'use strict';
// node:test suite for scripts/language-fit.js. Run by test/run-tests.sh
// (test_language_fit). Fixtures are written to a temp dir per test; no
// network, no clock.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { main, parseGitattributes, attrOf, globToRegExp } = require('../scripts/language-fit.js');

const blob = (p, size = 1000) => ({ path: p, type: 'blob', size });
const tree = (...entries) => JSON.stringify({ truncated: false, tree: entries });

function run(files, extraArgs = []) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'langfit-'));
  const input = path.join(dir, 'in');
  fs.mkdirSync(input);
  for (const [name, body] of Object.entries(files)) fs.writeFileSync(path.join(input, name), body);
  const findings = path.join(dir, 'findings.txt');
  let out = '';
  const orig = process.stdout.write;
  process.stdout.write = (s) => { out += s; return true; };
  let rc;
  try { rc = main(['node', 'language-fit.js', input, '--findings', findings, ...extraArgs]); } finally { process.stdout.write = orig; }
  return { rc, out, findings: fs.existsSync(findings) ? fs.readFileSync(findings, 'utf8') : null };
}

function sectionOf(out, heading) {
  const start = out.indexOf(`### ${heading}`);
  assert.ok(start >= 0, `missing heading ${heading}`);
  const next = out.indexOf('\n### ', start + 4);
  return out.slice(start, next < 0 ? undefined : next);
}

const DEP = 'version: 2\nupdates:\n  - package-ecosystem: github-actions\n    directory: /\n    schedule: {interval: weekly}\n';

test('a requested section with no matching language is "remove"', () => {
  const { rc, out } = run({
    'o__r.sections': 'python\njavascript\n',
    'o__r.languages.json': JSON.stringify({ JavaScript: 50000 }),
    'o__r.tree.json': tree(blob('a.js', 50000)),
  });
  assert.strictEqual(rc, 0);
  const s = sectionOf(out, 'Sections');
  assert.match(s, /`o\/r`: remove `python`/);
  assert.match(s, /sections: \[javascript\]/);
});

test('a language over 10% and 2,000 bytes with no section is "add"; a small one is not', () => {
  const { out } = run({
    'o__r.sections': '',
    'o__r.languages.json': JSON.stringify({ Python: 30000, Go: 1500, Ruby: 100000 }),
    'o__r.tree.json': tree(blob('a.py', 30000), blob('b.go', 1500), blob('c.rb', 100000)),
  });
  const s = sectionOf(out, 'Sections');
  assert.match(s, /add `python`/);
  assert.doesNotMatch(s, /`go`/);
  assert.match(s, /sections: \[python\]/);
});

test('a language under 10% of bytes is not added', () => {
  const { out } = run({
    'o__r.sections': '',
    'o__r.languages.json': JSON.stringify({ Python: 5000, Ruby: 95000 }),
    'o__r.tree.json': tree(blob('a.py', 5000), blob('c.rb', 95000)),
  });
  assert.doesNotMatch(sectionOf(out, 'Sections'), /add/);
});

test('hook bytes are subtracted before the 10% test', () => {
  const files = {
    'o__r.sections': '',
    'o__r.languages.json': JSON.stringify({ Shell: 80000, JavaScript: 20000, Go: 3000 }),
    'o__r.tree.json': tree(blob('.githooks/x.sh', 80000), blob('.claude/hooks/y.js', 19000), blob('main.go', 3000), blob('z.js', 1000)),
  };
  const { out } = run(files);
  const s = sectionOf(out, 'Sections');
  // JavaScript falls to 1,000 B (under 2,000) and Go is now 3,000 of 4,000 B.
  assert.match(s, /add `go`/);
  assert.doesNotMatch(s, /javascript/);
});

test('hooks already marked vendored in .gitattributes are not subtracted twice', () => {
  const { out } = run({
    'o__r.sections': '',
    'o__r.languages.json': JSON.stringify({ JavaScript: 20000 }),
    'o__r.tree.json': tree(blob('.claude/hooks/y.js', 19000), blob('z.js', 1000)),
    'o__r.gitattributes': '.claude/hooks/** linguist-vendored\n',
  });
  // GitHub's own number already excludes them, so nothing is subtracted.
  assert.match(sectionOf(out, 'Sections'), /add `javascript`/);
});

test('a Dockerfile adds docker, but not inside a fixture path', () => {
  const base = { 'o__r.sections': '', 'o__r.languages.json': '{}' };
  const yes = run({ ...base, 'o__r.tree.json': tree(blob('app/Dockerfile', 50)) });
  assert.match(sectionOf(yes.out, 'Sections'), /add `docker`/);
  const no = run({ ...base, 'o__r.tree.json': tree(blob('test/fixtures/Dockerfile', 50)) });
  assert.doesNotMatch(sectionOf(no.out, 'Sections'), /docker/);
});

test('a truncated or missing tree is "not checked", never clean', () => {
  const { out } = run({
    'o__r.sections': '',
    'o__r.languages.json': JSON.stringify({ Python: 30000 }),
    'o__r.tree.json': JSON.stringify({ truncated: true, tree: [blob('a.py')] }),
    'o__q.sections': '',
    'o__q.languages.json': '{}',
  });
  const s = sectionOf(out, 'Not checked');
  assert.match(s, /`o\/r`:.*tree/);
  assert.match(s, /`o\/q`:.*tree/);
  assert.doesNotMatch(sectionOf(out, 'Sections'), /add/);
});

test('a repo whose sections were not read is named as not checked', () => {
  const { out } = run({ 'o__r.languages.json': '{}', 'o__r.tree.json': tree() });
  assert.match(sectionOf(out, 'Not checked'), /`o\/r`: sections/);
});

test('a language mostly in fixture paths is flagged with a suggested line', () => {
  const { out, findings } = run({
    'o__r.sections': 'javascript\n',
    'o__r.languages.json': '{}',
    'o__r.tree.json': tree(blob('evals/a.js', 9000), blob('evals/b.js', 9000), blob('src/c.js', 1000), blob('x.min.js', 100)),
  });
  const s = sectionOf(out, 'Linguist overrides');
  assert.match(s, /JavaScript: 9\d% .*`evals\/\*\* linguist-vendored`/);
  assert.match(findings, /^o\/r: linguist: JavaScript/m);
});

test('a path already covered by a linguist attribute is not flagged', () => {
  const { out } = run({
    'o__r.sections': 'javascript\n',
    'o__r.languages.json': '{}',
    'o__r.tree.json': tree(blob('evals/a.js', 9000), blob('src/c.js', 1000)),
    'o__r.gitattributes': 'evals/** linguist-vendored\n',
  });
  assert.match(sectionOf(out, 'Linguist overrides'), /\(0\)/);
});

test('trace languages are listed as information, not counted', () => {
  const { out, findings } = run({
    'o__r.sections': 'python\n',
    'o__r.languages.json': JSON.stringify({ Python: 100000, Makefile: 200 }),
    'o__r.tree.json': tree(blob('a.py', 100000)),
  });
  const s = sectionOf(out, 'Linguist overrides');
  assert.match(s, /\(0\)/);
  assert.match(s, /\(info\): trace languages \(under 1%\): Makefile \(200 B\)/);
  assert.doesNotMatch(findings, /Makefile/);
});

test('gitattributes: later rule wins, negation, anchoring and ** work', () => {
  const rules = parseGitattributes('# c\n*.js linguist-vendored\nsrc/** -linguist-vendored\n/docs/** linguist-documentation=true\n**/gen/*.go linguist-generated\n');
  assert.strictEqual(attrOf(rules, 'lib/a.js', 'linguist-vendored'), true);
  assert.strictEqual(attrOf(rules, 'src/a.js', 'linguist-vendored'), false);
  assert.strictEqual(attrOf(rules, 'docs/x/y.md', 'linguist-documentation'), true);
  assert.strictEqual(attrOf(rules, 'a/docs/y.md', 'linguist-documentation'), undefined);
  assert.strictEqual(attrOf(rules, 'a/b/gen/x.go', 'linguist-generated'), true);
  assert.ok(globToRegExp('a/**').test('a/b/c'));
});

test('a manifest directory with no dependabot entry is flagged; a covered one is not', () => {
  const dep = DEP + '  - package-ecosystem: npm\n    directory: /\n    schedule: {interval: weekly}\n';
  const { out } = run({
    'o__r.sections': '',
    'o__r.languages.json': '{}',
    'o__r.dependabot.yml': dep,
    'o__r.tree.json': tree(blob('package.json'), blob('package-lock.json'), blob('web/package.json'), blob('web/yarn.lock'),
      blob('tools/go.mod'), blob('.github/workflows/ci.yml')),
  });
  const s = sectionOf(out, 'Dependabot manifests');
  assert.match(s, /2 manifest directories without an updates entry: `\/web` \(npm\), `\/tools` \(gomod\)/);
});

test('directories globs cover nested manifests', () => {
  const dep = DEP + '  - package-ecosystem: npm\n    directories: ["/", "/apps/*"]\n    schedule: {interval: weekly}\n';
  const { out } = run({
    'o__r.sections': '',
    'o__r.languages.json': '{}',
    'o__r.dependabot.yml': dep,
    'o__r.tree.json': tree(blob('package.json'), blob('package-lock.json'), blob('apps/a/package.json'), blob('apps/a/package-lock.json'), blob('.github/workflows/ci.yml')),
  });
  assert.match(sectionOf(out, 'Dependabot manifests'), /\(0\)/);
});

test('exempt paths need no entry: dated-phrasing, fixtures, seeds, node_modules', () => {
  const { out } = run({
    'o__r.sections': '',
    'o__r.languages.json': '{}',
    'o__r.dependabot.yml': DEP,
    'o__r.tree.json': tree(blob('.githooks/dated-phrasing/package.json'), blob('test/fixtures/x/package.json'),
      blob('node_modules/a/package.json'), blob('evals/q/seed/go.mod'), blob('seeds/Gemfile'), blob('testdata/Cargo.toml'), blob('.github/workflows/ci.yml')),
  });
  assert.match(sectionOf(out, 'Dependabot manifests'), /\(0\)/);
});

test('an entry for a directory with no manifest, a missing actions entry, a missing lockfile and a missing file are flagged', () => {
  const dep = 'version: 2\nupdates:\n  - package-ecosystem: pip\n    directory: /py\n    schedule: {interval: weekly}\n  - package-ecosystem: npm\n    directory: /\n    schedule: {interval: weekly}\n';
  const a = run({
    'o__r.sections': '', 'o__r.languages.json': '{}', 'o__r.dependabot.yml': dep,
    'o__r.tree.json': tree(blob('package.json'), blob('.github/workflows/ci.yml')),
  });
  const s = sectionOf(a.out, 'Dependabot manifests');
  assert.match(s, /updates entry whose directory has no manifest.*pip `\/py`/);
  assert.match(s, /no github-actions updates entry/);
  assert.match(s, /package\.json without a lockfile beside it: `package\.json`/);
  const b = run({
    'o__r.sections': '', 'o__r.languages.json': '{}',
    'o__r.tree.json': tree(blob('go.mod')),
  });
  assert.match(sectionOf(b.out, 'Dependabot manifests'), /no dependabot\.yml/);
});

test('private repos show categories and counts, never paths, names or byte counts', () => {
  const { out, findings } = run({
    'o__r.private': '',
    'o__r.sections': 'python\n',
    'o__r.languages.json': JSON.stringify({ Go: 90000, Makefile: 10 }),
    'o__r.tree.json': tree(blob('secret/app/go.mod'), blob('secret/main.go', 90000), blob('evals/q.js', 5000), blob('secret/Dockerfile', 10)),
    'o__r.dependabot.yml': DEP,
  });
  for (const text of [out, findings]) {
    assert.doesNotMatch(text, /secret|go\.mod|main\.go|90,000|evals|Makefile|\d+ B\b/);
  }
  assert.match(out, /`o\/r`: remove 1 section/);
  assert.match(out, /`o\/r`: add 2 sections/);
  assert.match(out, /2 manifest directories lack a Dependabot entry/);
  assert.match(findings, /^o\/r: sections: remove 1 section$/m);
});

test('public repos may show paths', () => {
  const { out } = run({
    'o__r.sections': '', 'o__r.languages.json': '{}', 'o__r.dependabot.yml': DEP,
    'o__r.tree.json': tree(blob('svc/go.mod')),
  });
  assert.match(out, /`\/svc` \(gomod\)/);
});

test('a dependabot.yml that does not parse is not checked, not clean', () => {
  const { out } = run({
    'o__r.sections': '', 'o__r.languages.json': '{}', 'o__r.dependabot.yml': 'updates: [unclosed\n',
    'o__r.tree.json': tree(blob('go.mod')),
  });
  assert.match(sectionOf(out, 'Not checked'), /dependabot\.yml \(could not be parsed\)/);
});

test('--findings is written empty when nothing is found; exit 0 whatever is found', () => {
  const { rc, findings } = run({
    'o__r.sections': 'go\n', 'o__r.languages.json': JSON.stringify({ Go: 5000 }),
    'o__r.dependabot.yml': DEP, 'o__r.tree.json': tree(blob('main.go', 5000)),
  });
  assert.strictEqual(rc, 0);
  assert.strictEqual(findings, '');
});

test('bad usage and a missing directory exit 2', () => {
  const orig = process.stderr.write;
  process.stderr.write = () => true;
  try {
    assert.strictEqual(main(['node', 'language-fit.js']), 2);
    assert.strictEqual(main(['node', 'language-fit.js', '/nonexistent-dir-langfit']), 2);
    assert.strictEqual(main(['node', 'language-fit.js', os.tmpdir(), '--bogus']), 2);
  } finally {
    process.stderr.write = orig;
  }
});

test('a manifest .gitattributes marks vendored or generated is exempt; documentation is not', () => {
  const files = {
    'o__r.sections': '', 'o__r.languages.json': '{}', 'o__r.dependabot.yml': DEP,
    'o__r.gitattributes': 'evals/** linguist-vendored\ngen/** linguist-generated\ndocs/** linguist-documentation\n',
    'o__r.tree.json': tree(blob('evals/a/go.mod'), blob('gen/Cargo.toml'), blob('docs/Gemfile'), blob('.github/workflows/ci.yml')),
  };
  const s = sectionOf(run(files).out, 'Dependabot manifests');
  assert.match(s, /1 manifest directory without an updates entry: `\/docs` \(bundler\)/);
});

test('own bytes for the add check exclude fixture paths but not paths a linguist rule already covers', () => {
  // GitHub saw 100,000 B of JavaScript; 95,000 B of it is under evals/.
  const files = {
    'o__r.sections': '',
    'o__r.languages.json': JSON.stringify({ JavaScript: 100000, Ruby: 100000 }),
    'o__r.tree.json': tree(blob('evals/a.js', 95000), blob('src/b.js', 5000), blob('c.rb', 100000)),
  };
  const before = sectionOf(run(files).out, 'Sections');
  // 5,000 of 105,000 own bytes is under 10%.
  assert.doesNotMatch(before, /javascript/);
  assert.match(before, /add `ruby`|\(0\)/);
  // Once evals/ is marked vendored GitHub's own numbers already exclude it; nothing is subtracted twice.
  const covered = {
    ...files,
    'o__r.languages.json': JSON.stringify({ JavaScript: 5000, Ruby: 100000 }),
    'o__r.gitattributes': 'evals/** linguist-vendored\n',
  };
  const after = sectionOf(run(covered).out, 'Sections');
  assert.doesNotMatch(after, /javascript/);
  const ok = run({ ...covered, 'o__r.languages.json': JSON.stringify({ JavaScript: 5000 }), 'o__r.tree.json': tree(blob('evals/a.js', 95000), blob('src/b.js', 5000)) });
  assert.match(sectionOf(ok.out, 'Sections'), /add `javascript` \(100% /);
});

test('a failed read is "not checked", never an absent file, and yields no finding', () => {
  const base = { 'o__r.sections': '', 'o__r.languages.json': '{}' };
  for (const [marker, extra, pattern] of [
    ['o__r.tree.json.unread', { 'o__r.dependabot.yml': DEP }, /tree \(read failed; dependabot\)/],
    ['o__r.dependabot.yml.unread', { 'o__r.tree.json': tree(blob('go.mod')) }, /dependabot\.yml \(read failed\)/],
    ['o__r.gitattributes.unread', { 'o__r.dependabot.yml': DEP, 'o__r.tree.json': tree(blob('evals/a.js', 9000)) }, /\.gitattributes \(read failed; linguist\)/],
  ]) {
    const { out, findings } = run({ ...base, ...extra, [marker]: '' });
    assert.match(sectionOf(out, 'Not checked'), pattern);
    assert.strictEqual(findings, '', marker);
  }
  const { out } = run({ 'o__r.sections': 'go\n', 'o__r.languages.json.unread': '', 'o__r.tree.json': tree() });
  assert.match(sectionOf(out, 'Not checked'), /languages \(read failed/);
  assert.doesNotMatch(sectionOf(out, 'Sections'), /remove/);
});
