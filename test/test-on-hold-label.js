#!/usr/bin/env node
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const MarkdownIt = require('markdown-it');
const YAML = require('yaml');
const root = path.resolve(__dirname, '..');
const md = new MarkdownIt();
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const documents = new Map();
function tokens(name) {
  if (!documents.has(name)) {
    let source = read(name);
    if (process.env.ON_HOLD_MUTATION === 'policy' && name === 'agents-md/base.md') {
      source = source.replace('is paused by the owner', 'is ready for agent work');
    }
    if (process.env.ON_HOLD_MUTATION === 'worker' && name.endsWith('agent-issue-worker.md')) {
      source = source.replace('before the 3-item limit', 'after the 3-item limit');
    }
    if (process.env.ON_HOLD_MUTATION === 'reference' && name === reference) {
      source = source.replace('review, fix, rebase', 'fix, rebase');
    }
    if (process.env.ON_HOLD_MUTATION === 'changelog' && name === issues) {
      source = source.replace('suppress duplicate or follow-up work', 'permit duplicate or follow-up work');
    }
    if (process.env.ON_HOLD_MUTATION === 'routine' && name === routine) {
      source = source.replace('before branch changes or the start-log commit/push', 'after branch changes or the start-log commit/push');
    }
    documents.set(name, md.parse(source, {}));
  }
  return documents.get(name);
}
function section(name, heading) {
  const all = tokens(name);
  const start = all.findIndex((token, i) => token.type === 'heading_open' && all[i + 1].content === heading);
  assert.notEqual(start, -1, `missing heading ${heading}`);
  const level = all[start].tag;
  const end = all.findIndex((token, i) => i > start && token.type === 'heading_open' && token.tag <= level);
  return all.slice(start + 3, end < 0 ? undefined : end);
}
function prose(list) {
  return list.filter(token => token.type === 'inline').map(token => token.content).join(' ').replaceAll('\n', ' ');
}
function includes(text, phrases) {
  for (const phrase of phrases) assert.ok(text.includes(phrase), `missing policy: ${phrase}`);
}
function linked(name) {
  return tokens(name).some(token => token.children?.some(child => child.type === 'link_open' && child.attrGet('href') === 'on-hold-label.md'));
}
const reference = 'docs/reference/on-hold-label.md';
const worker = 'docs/reference/agent-issue-worker.md';
const issues = 'docs/reference/agent-changelog-issues.md';
const routine = 'docs/reference/agent-changelog-routine.md';

test('fleet rule protects issues and PRs, with owner control and passive status', () => {
  const body = prose(section('agents-md/base.md', 'Working in these repos'));
  includes(body, ['issue or PR labeled `on-hold` is paused by the owner', 'no commits, reviews, merges, closing or follow-ups that do it', 'read, cite and list it as on hold without asking again', 'A held PR stays open', 'Only the owner adds or removes the label', 'an agent the owner directs in that session']);
  assert.ok(Buffer.byteLength(read('agents-md/base.md')) < 25600);
  assert.equal(read('agents-md/base.md'), read('.claude/hooks/fleet-guidance.md'));
});

test('reference specifies exact label and prohibited work without closing held PRs', () => {
  const list = tokens(reference);
  assert.ok(list.some(token => token.type === 'bullet_list_open'));
  includes(prose(list), ['Name: `on-hold`', 'Color: `BFBFBF`', 'Description: `Owner paused this: agents must not work on it until the owner removes the label`']);
  includes(prose(section(reference, 'Meaning')), ['start, continue, review, fix, rebase, merge main into, push to, merge, close', 'another repo', 'never close it or convert it', 'Only the owner', 'explicitly directs an agent', 'in that session', 'failed label read', 'read-only status', 'Do not comment, relabel, remind, or ask']);
});

test('worker excludes holds before limit and rechecks before every mutation', () => {
  assert.ok(linked(worker));
  includes(prose(section(worker, 'Each run')), ['before the 3-item limit', 'Re-read issue and linked PR labels before any mutation', 'skip if either carries `on-hold`']);
  includes(prose(section(worker, 'Constraints')), ['no comments, label changes, work, push, or review', 'held linked PR', 'commit, push, opening or updating a PR', 'overrides step 7']);
  includes(prose(section(worker, 'Stale claims')), ['stay on hold', 'never suggest resetting']);
});

test('changelog search suppresses held duplicates and owner controls hygiene', () => {
  assert.ok(linked(issues));
  includes(prose(section(issues, 'When to file')), ['issues and PRs', 'cite it as `on hold`', 'suppress duplicate or follow-up work', 'another repo', 'Never edit, close, or relabel']);
  includes(prose(section(issues, 'Hygiene')), ['owner may add or remove `on-hold`', 'explicitly directs it in that session']);
  includes(prose(section(issues, 'Closing')), ['`on-hold` forbids closing']);
});

test('routine holds precede logs and override discrepancy and notification writes', () => {
  assert.ok(linked(routine));
  const start = prose(section(routine, '0. Before anything else'));
  assert.ok(start.indexOf('Hold check first') < start.indexOf('Hard rule'));
  includes(start, ['before branch changes or the start-log commit/push', 'stop quietly', 'no reminders or owner re-asks']);
  includes(prose(section(routine, 'Constraints')), ['Before every later write, commit, push', 'freshly read', 'linked PR labels', 'overrides run-log, stop-report, discrepancy, and notification']);
  includes(prose(section(routine, '4. Write the entry and file the issues')), ['Read mapped issue and linked PR labels', 'search existing issues and PRs', 'suppress replacement or follow-up issues', 'another repo']);
  includes(prose(section(routine, '5. Re-check open discrepancies')), ['linked issue and PR labels', 'without editing it or filing follow-up']);
  includes(prose(section(routine, '8. Notify')), ['An `on-hold` stop sends no notification', 'weekly reminders or requests to resume']);
});

const code = () => section(reference, 'Provisioning (owner only)').find(token => token.type === 'fence' && token.info === 'bash').content;
function provision(scenario = 'success', script = code()) {
  // Negative controls alter only the documented Bash string, never live files.
  if (process.env.ON_HOLD_MUTATION === 'color') script = script.replace('--color BFBFBF', '--color 000000');
  if (process.env.ON_HOLD_MUTATION === 'enumeration') {
    script = script.replace('inventory=$(gh repo list "$owner" --source --no-archived --json nameWithOwner --limit 1000)', `inventory=$(gh repo list "$owner" --source --no-archived --json nameWithOwner --limit 1000) || inventory='[]'`);
  }
  if (process.env.ON_HOLD_MUTATION === 'cap') script = script.replace('length < 1000', 'length <= 1000');
  if (process.env.ON_HOLD_MUTATION === 'readback') script = script.replace('(.color | ascii_downcase) == "bfbfbf"', 'true');

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'c55-label-test-'));
  const log = path.join(temp, 'calls.jsonl');
  try {
    const owners = Object.values(YAML.parse(read('.github/workflows/sync.yml')).jobs)
      .flatMap(job => job.steps).map(step => step.env?.SYNC_OWNERS).filter(Boolean);
    fs.writeFileSync(path.join(temp, 'yq'), '#!/usr/bin/env node\nprocess.stdout.write(process.env.TEST_OWNERS);\n', { mode: 0o755 });
    fs.writeFileSync(path.join(temp, 'gh'), `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.appendFileSync(process.env.TEST_LOG, JSON.stringify(args) + '\\n');
const scenario = process.env.TEST_SCENARIO;
if (args[0] === 'repo') {
  if (scenario === 'enumeration-failure' && args[2] === 'jodidaniel') process.exit(4);
  const repo = {nameWithOwner: args[2] + '/example-repo'};
  console.log(JSON.stringify(scenario === 'saturated' ? Array(1000).fill(repo) : [repo]));
} else if (args[0] === 'api') {
  console.log(JSON.stringify({name:'on-hold', color: scenario === 'bad-readback' ? '000000' : 'bfbfbf', description:'Owner paused this: agents must not work on it until the owner removes the label'}));
} else if (args[0] === 'label' && scenario === 'saturated') {
  // Stop after the first forbidden write; the cap test asserts zero writes.
  process.exit(6);
} else if (args[0] !== 'label') process.exit(5);
`, { mode: 0o755 });
    const sentinelLog = path.join(temp, 'claude-calls.log');
    fs.writeFileSync(path.join(temp, 'claude'), "#!/bin/sh\nprintf '%s\\n' called >> \"$TEST_CLAUDE_LOG\"\nexit 97\n", { mode: 0o755 });
    const result = spawnSync('bash', ['-c', script], { cwd: root, encoding: 'utf8', env: { ...process.env, PATH: [temp, process.env.PATH].join(path.delimiter), TEST_CLAUDE_LOG: sentinelLog, TEST_LOG: log, TEST_SCENARIO: scenario, TEST_OWNERS: JSON.stringify(owners) } });
    assert.ok(!fs.existsSync(sentinelLog), 'provisioning must never invoke claude');
    return { ...result, calls: fs.existsSync(log) ? fs.readFileSync(log, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : [] };
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
}

test('owner-only provisioning discovers workflow owners and reads back every label', () => {
  includes(prose(section(reference, 'Provisioning (owner only)')), ['explicitly directs an agent', 'does not execute provisioning', 'not local disk', '`--force` only overwrites an existing label named `on-hold`', 'no repository in the fleet had that label when this was written (2026-10-04)']);
  includes(code(), ['.jobs[].steps[].env.SYNC_OWNERS', '--source --no-archived', '--limit 1000']);
  const result = provision();
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.calls.filter(call => call[0] === 'repo').map(call => call[2]), ['Adam-S-Daniel', 'jodidaniel']);
  const writes = result.calls.filter(call => call[0] === 'label');
  assert.equal(writes.length, 2);
  for (const call of writes) assert.deepEqual(call.slice(1), ['create', 'on-hold', '-R', call[4], '--color', 'BFBFBF', '--description', 'Owner paused this: agents must not work on it until the owner removes the label', '--force']);
  assert.equal(result.calls.filter(call => call[0] === 'api').length, writes.length);
  assert.ok(result.calls.slice(0, 2).every(call => call[0] === 'repo'));
});

for (const scenario of ['enumeration-failure', 'saturated', 'bad-readback']) {
  test(`provisioning fails closed: ${scenario}`, () => {
    const result = provision(scenario);
    assert.notEqual(result.status, 0);
    assert.ok(!result.stdout.includes('complete remote inventory'));
    if (scenario !== 'bad-readback') assert.equal(result.calls.filter(call => call[0] === 'label').length, 0);
  });
}
