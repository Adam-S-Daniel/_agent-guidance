#!/usr/bin/env node
'use strict';

// Checks for .github/workflows/fleet-worker-fire.yml, which fires the fleet
// worker and the loop worker (ADR 0046) from one matrix job. The YAML is
// parsed, never scanned by line, and the step's own script runs against a
// fake curl on PATH, once per matrix entry.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const YAML = require('yaml');

const root = path.resolve(__dirname, '..');
const workflow = '.github/workflows/fleet-worker-fire.yml';
const doc = () => YAML.parse(fs.readFileSync(path.join(root, workflow), 'utf8'));
const step = () => doc().jobs.fire.steps[0];
const entries = () => doc().jobs.fire.strategy.matrix.include;
const entry = role => entries().find(e => e.worker === role);
// Every slot of a 3-hour schedule, each line on the longest cadence it serves.
const LINES = ['20 0 * * 2', '20 0 * * 0,1,3-6', '20 12 * * *', '20 6,18 * * *', '20 3,9,15,21 * * *'];

test('fires on the schedule or by hand, with no permissions, no uses and no interpolation in run', () => {
  const d = doc();
  assert.deepEqual(Object.keys(d.on).sort(), ['schedule', 'workflow_dispatch']);
  assert.deepEqual(d.on.schedule, LINES.map(cron => ({ cron })));
  assert.deepEqual(d.permissions, {});
  const job = d.jobs.fire;
  assert.deepEqual(Object.keys(d.jobs), ['fire']);
  assert.equal(job.permissions, undefined, 'the job must not widen permissions');
  assert.equal(job.steps.length, 1);
  assert.ok(!('uses' in step()));
  assert.ok(!/\$\{\{/.test(step().run), 'no ${{ }} expression may reach a run: block');
  assert.ok(!/secrets\[/.test(fs.readFileSync(path.join(root, workflow), 'utf8')),
    'no dynamic secrets[...] index: it exposes every secret to the runner');
  assert.deepEqual(step().env, {
    FIRE_URL: '${{ vars[matrix.url_var] }}',
    FLEET_BEARER: '${{ secrets.FLEET_WORKER_FIRE_BEARER }}',
    LOOP_BEARER: '${{ secrets.LOOP_WORKER_FIRE_BEARER }}',
    EVERY_HOURS: '${{ vars[matrix.every_var] }}',
    WORKER: '${{ matrix.worker }}',
    URL_VAR: '${{ matrix.url_var }}',
    BEARER_VAR: '${{ matrix.bearer_var }}',
    EVERY_VAR: '${{ matrix.every_var }}',
    EVERY_REQUIRED: '${{ matrix.every_required }}',
    FIRE_TEXT: '${{ matrix.text }}',
  });
});

test('the matrix fires the fleet worker and the loop worker, each with its own names and group', () => {
  const job = doc().jobs.fire;
  assert.equal(job.strategy['fail-fast'], false, 'one failed fire must not cancel the other');
  assert.equal(doc().concurrency, undefined, 'no workflow-level group: it would couple the two fires');
  assert.equal(job.concurrency.group, '${{ matrix.concurrency }}');
  assert.equal(job.concurrency['cancel-in-progress'], false);
  assert.deepEqual(entries().map(e => e.worker), ['fleet', 'loop']);
  const pick = k => entries().map(e => e[k]);
  assert.equal(new Set(pick('concurrency')).size, 2, 'the groups must differ');
  assert.deepEqual(entry('fleet'), { worker: 'fleet', concurrency: 'fleet-worker-fire',
    url_var: 'FLEET_WORKER_FIRE_URL', bearer_var: 'FLEET_WORKER_FIRE_BEARER',
    every_var: 'FLEET_WORKER_EVERY_HOURS', every_required: 'false',
    text: 'Scheduled fire from fleet-worker-fire.yml' });
  assert.deepEqual(entry('loop'), { worker: 'loop', concurrency: 'loop-worker-fire',
    url_var: 'LOOP_WORKER_FIRE_URL', bearer_var: 'LOOP_WORKER_FIRE_BEARER',
    every_var: 'LOOP_WORKER_EVERY_HOURS', every_required: 'true',
    text: 'Scheduled fire of the loop worker from fleet-worker-fire.yml' });
});

test('actions.lock lists the workflow with no actions', () => {
  const lock = YAML.parse(fs.readFileSync(path.join(root, '.github/workflows/actions.lock'), 'utf8'));
  assert.deepEqual(lock.workflows[workflow], []);
});

// The step's env: with the matrix entry and the three configured values.
function stepEnv(role, { url, bearer, every }) {
  const e = entry(role);
  const out = {};
  for (const [name, expr] of Object.entries(step().env)) {
    const lookup = expr.match(/^\$\{\{ (vars|secrets)\[matrix\.(\w+)\] \}\}$/);
    const plain = expr.match(/^\$\{\{ matrix\.(\w+) \}\}$/);
    const named = expr.match(/^\$\{\{ secrets\.(\w+) \}\}$/);
    if (named) out[name] = named[1] === e.bearer_var ? bearer : 'other-task-value';
    else if (lookup) out[name] = ({ [e.url_var]: url, [e.bearer_var]: bearer, [e.every_var]: every })[e[lookup[2]]];
    else if (plain) out[name] = e[plain[1]];
    else throw new Error(`unresolved env expression ${expr}`);
  }
  return out;
}

function runFire({ role = 'fleet', url = 'https://api.example.com/fire', bearer = 'placeholder-value', status = 200,
  every = role === 'loop' ? '6' : '', event = 'schedule', line = '20 3,9,15,21 * * *', hour = '9',
  hold = 'pace_hold=false', gitStatus = 0 } = {}) {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'fleet-fire-test-'));
  try {
    const log = path.join(temp, 'calls.jsonl');
    fs.writeFileSync(path.join(temp, 'curl'), `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
const config = args[args.indexOf('--config') + 1] === '-' ? fs.readFileSync(0, 'utf8') : '';
fs.appendFileSync(process.env.TEST_LOG, JSON.stringify({ args, config }) + '\\n');
fs.writeFileSync(args[args.indexOf('-o') + 1], '{"secret_field":"response-canary"}');
process.stdout.write(process.env.TEST_STATUS);
`, { mode: 0o755 });
    // A fake date: GitHub starts scheduled runs late, so the clock must
    // never decide whether a run fires.
    fs.writeFileSync(path.join(temp, 'date'), `#!/usr/bin/env bash
echo "$TEST_HOUR"
`, { mode: 0o755 });
    // Fake git and python3 for the Loop worker's pace hold: git logs its
    // arguments and makes the clone directory; python3 imports yaml and
    // prints TEST_HOLD for `allowance_guard.py hold`.
    const gitLog = path.join(temp, 'git.jsonl');
    fs.writeFileSync(path.join(temp, 'git'), `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.appendFileSync(process.env.TEST_GIT_LOG, JSON.stringify(args) + '\\n');
if (args[0] === 'clone') fs.mkdirSync(args[args.length - 1], { recursive: true });
process.exit(Number(process.env.TEST_GIT_STATUS));
`, { mode: 0o755 });
    fs.writeFileSync(path.join(temp, 'python3'), `#!/usr/bin/env node
const args = process.argv.slice(2);
if (args[0] === '-c') process.exit(0);
if (args[0] === 'scripts/allowance_guard.py' && args[1] === 'hold') {
  process.stdout.write(process.env.TEST_HOLD ? process.env.TEST_HOLD + '\\n' : '');
  process.exit(0);
}
process.exit(2);
`, { mode: 0o755 });
    const eventPath = path.join(temp, 'event.json');
    fs.writeFileSync(eventPath, JSON.stringify(event === 'schedule' ? { schedule: line } : {}));
    const result = spawnSync('bash', ['-c', step().run], { encoding: 'utf8', env: {
      PATH: [temp, process.env.PATH].join(path.delimiter), TEST_LOG: log, TEST_STATUS: String(status),
      ...stepEnv(role, { url, bearer, every }), GITHUB_EVENT_NAME: event,
      GITHUB_EVENT_PATH: eventPath, TEST_HOUR: hour, TEST_HOLD: hold, TEST_GIT_LOG: gitLog,
      TEST_GIT_STATUS: String(gitStatus) } });
    const lines = f => fs.existsSync(f) ? fs.readFileSync(f, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)) : [];
    return { result, curl: lines(log), git: lines(gitLog) };
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
}

test('fires once with the bearer on stdin only, and never prints the bearer or the response', () => {
  const { result, curl } = runFire();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(curl.length, 1);
  const { args, config } = curl[0];
  assert.equal(args[args.indexOf('-X') + 2], 'https://api.example.com/fire');
  assert.ok(!args.some(a => a.includes('placeholder-value')), 'bearer must not be in curl argv');
  assert.equal(config, 'header = "Authorization: Bearer placeholder-value"\n');
  assert.deepEqual(JSON.parse(args[args.indexOf('-d') + 1]), { text: 'Scheduled fire from fleet-worker-fire.yml' });
  for (const text of [result.stdout, result.stderr]) {
    for (const canary of ['placeholder-value', 'response-canary']) assert.ok(!text.includes(canary), `leaked ${canary}`);
  }
});

test('an unconfigured URL or bearer fails naming it, and fires nothing', () => {
  for (const [over, name] of [[{ url: '' }, 'FLEET_WORKER_FIRE_URL'], [{ bearer: '' }, 'FLEET_WORKER_FIRE_BEARER']]) {
    const { result, curl } = runFire(over);
    assert.equal(result.status, 1);
    assert.equal(curl.length, 0);
    assert.ok(result.stdout.includes(name), `error must name ${name}`);
  }
});

test('a failed fire fails the run with the status code only', () => {
  const { result } = runFire({ status: 401 });
  assert.equal(result.status, 1);
  assert.ok(result.stdout.includes('fire failed (HTTP 401)'));
  assert.ok(!result.stdout.includes('response-canary'));
});

test('FLEET_WORKER_EVERY_HOURS thins the fleet worker\'s scheduled fires and never blocks a dispatch', () => {
  const cases = [
    [{ every: '' }, 1],
    [{ every: '3', line: '20 3,9,15,21 * * *' }, 1],
    [{ every: '6', line: '20 3,9,15,21 * * *' }, 0],
    [{ every: '6', line: '20 6,18 * * *' }, 1],
    [{ every: '6', line: '20 12 * * *' }, 1],
    [{ every: '12', line: '20 6,18 * * *' }, 0],
    [{ every: '12', line: '20 12 * * *' }, 1],
    [{ every: '24', line: '20 12 * * *' }, 0],
    [{ every: '24', line: '20 0 * * 0,1,3-6' }, 1],
    [{ every: '24', line: '20 0 * * 2' }, 1],
    [{ every: '168', line: '20 0 * * 2' }, 1],
    [{ every: '168', line: '20 0 * * 0,1,3-6' }, 0],
    [{ every: '168', line: '20 12 * * *' }, 0],
    [{ every: '168', line: '20 3,9,15,21 * * *', event: 'workflow_dispatch' }, 1],
    [{ every: '24', line: '20 3,9,15,21 * * *', event: 'workflow_dispatch' }, 1],
  ];
  for (const [over, fires] of cases) {
    const { result, curl } = runFire(over);
    assert.equal(result.status, 0, `${JSON.stringify(over)}: ${result.stdout}${result.stderr}`);
    assert.equal(curl.length, fires, JSON.stringify(over));
  }
});

test('a FLEET_WORKER_EVERY_HOURS outside 3, 6, 12, 24 and 168 fails naming it, and fires nothing', () => {
  for (const every of ['5', '0', 'daily', '48', '72']) {
    const { result, curl } = runFire({ every });
    assert.equal(result.status, 1, every);
    assert.equal(curl.length, 0, every);
    assert.ok(result.stdout.includes('FLEET_WORKER_EVERY_HOURS'), every);
  }
});

test('a late start still fires on its cron line, whatever the clock says (2026-11-10 to 11-12 regression)', () => {
  // The 18:20 UTC slot started at 19:43; the old UTC-hour test skipped it.
  for (const hour of ['1', '11', '19', '7']) {
    const { result, curl } = runFire({ every: '6', line: '20 6,18 * * *', hour });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.equal(curl.length, 1, `hour ${hour}`);
  }
});

test('the five cron lines cover each 3-hour slot of the week once, and each cadence fires at its own spacing', () => {
  // Expand each line to its exact (day, hour) slots, so a line moved to
  // another day or hour fails here, not just a changed total.
  const hoursOf = l => l.split(' ')[1].split(',').map(Number);
  const daysOf = l => {
    const field = l.split(' ')[4];
    if (field === '*') return [0, 1, 2, 3, 4, 5, 6];
    return field.split(',').flatMap(p => {
      const [a, b = a] = p.split('-').map(Number);
      return Array.from({ length: b - a + 1 }, (_, i) => a + i);
    });
  };
  const slotsOf = l => daysOf(l).flatMap(d => hoursOf(l).map(h => `${d}@${h}`));
  const sorted = a => [...a].sort((x, y) => x.localeCompare(y, 'en', { numeric: true }));
  const all = [];
  for (let d = 0; d < 7; d++) for (let h = 0; h < 24; h += 3) all.push(`${d}@${h}`);
  assert.deepEqual(sorted(LINES.flatMap(slotsOf)), sorted(all));
  assert.equal(LINES[0].split(' ')[4], '2', 'the weekly line fires on Tuesday');
  const want = {
    '3': all,
    '6': all.filter(s => Number(s.split('@')[1]) % 6 === 0),
    '12': all.filter(s => Number(s.split('@')[1]) % 12 === 0),
    '24': all.filter(s => s.endsWith('@0')),
    '168': ['2@0'],
  };
  for (const [every, slots] of Object.entries(want)) {
    const fired = LINES.filter(line => runFire({ every, line }).curl.length === 1).flatMap(slotsOf);
    assert.deepEqual(sorted(fired), sorted(slots), `every ${every}`);
  }
});

test('an unknown schedule line fails naming it, and fires nothing', () => {
  const { result, curl } = runFire({ every: '6', line: '20 */3 * * *' });
  assert.equal(result.status, 1);
  assert.equal(curl.length, 0);
  assert.ok(result.stdout.includes('unknown schedule line'));
});

test('the loop worker fires once with its own text, and the bearer on stdin only', () => {
  const { result, curl } = runFire({ role: 'loop', line: '20 6,18 * * *', bearer: 'loop-placeholder', url: 'https://api.example.com/loop' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(curl.length, 1);
  const { args, config } = curl[0];
  assert.equal(args[args.indexOf('-X') + 2], 'https://api.example.com/loop');
  assert.ok(!args.some(a => a.includes('loop-placeholder')), 'bearer must not be in curl argv');
  assert.equal(config, 'header = "Authorization: Bearer loop-placeholder"\n');
  assert.deepEqual(JSON.parse(args[args.indexOf('-d') + 1]), { text: 'Scheduled fire of the loop worker from fleet-worker-fire.yml' });
  assert.ok(result.stdout.includes('loop worker fired'));
  for (const text of [result.stdout, result.stderr]) {
    for (const canary of ['loop-placeholder', 'response-canary']) assert.ok(!text.includes(canary), `leaked ${canary}`);
  }
});

test('the loop worker has no hidden defaults: a missing URL, bearer or cadence fails naming it, and fires nothing', () => {
  for (const [over, name] of [[{ url: '' }, 'LOOP_WORKER_FIRE_URL'], [{ bearer: '' }, 'LOOP_WORKER_FIRE_BEARER'],
    [{ every: '' }, 'LOOP_WORKER_EVERY_HOURS'], [{ every: '', event: 'workflow_dispatch' }, 'LOOP_WORKER_EVERY_HOURS']]) {
    const { result, curl } = runFire({ role: 'loop', line: '20 6,18 * * *', ...over });
    assert.equal(result.status, 1, JSON.stringify(over));
    assert.equal(curl.length, 0);
    assert.ok(result.stdout.includes(name), `error must name ${name}`);
    assert.ok(!result.stdout.includes('FLEET_WORKER'), 'must not name the other task\'s variables');
  }
  // The fleet worker's names never leak into the loop's own lookups.
  const { result } = runFire({ role: 'fleet', url: '' });
  assert.ok(result.stdout.includes('FLEET_WORKER_FIRE_URL') && !result.stdout.includes('LOOP_WORKER'));
});

test('LOOP_WORKER_EVERY_HOURS gates scheduled loop fires on the cron line, and a dispatch always fires', () => {
  const cases = [
    [{ every: '3', line: '20 3,9,15,21 * * *' }, 1],
    [{ every: '12', line: '20 6,18 * * *' }, 0],
    [{ every: '12', line: '20 12 * * *' }, 1],
    [{ every: '168', line: '20 0 * * 0,1,3-6' }, 0],
    [{ every: '168', line: '20 0 * * 2' }, 1],
    [{ every: '168', line: '20 12 * * *', event: 'workflow_dispatch' }, 1],
  ];
  for (const [over, fires] of cases) {
    const { result, curl } = runFire({ role: 'loop', line: '20 6,18 * * *', ...over });
    assert.equal(result.status, 0, `${JSON.stringify(over)}: ${result.stdout}${result.stderr}`);
    assert.equal(curl.length, fires, JSON.stringify(over));
  }
  for (const every of ['5', 'daily', '48']) {
    const { result, curl } = runFire({ role: 'loop', every });
    assert.equal(result.status, 1, every);
    assert.equal(curl.length, 0, every);
    assert.ok(result.stdout.includes('LOOP_WORKER_EVERY_HOURS'), every);
  }
});

test('a scheduled loop fire is skipped while skills-evals reports the pace hold', () => {
  const { result, curl, git } = runFire({ role: 'loop', line: '20 6,18 * * *', hold: 'pace_hold=true' });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.equal(curl.length, 0);
  assert.ok(result.stdout.includes('skipped: the account is ahead of its weekly pace'));
  // It reads skills-evals' main and its persistent/eval-results branch.
  const clones = git.filter(a => a[0] === 'clone');
  assert.equal(clones.length, 2);
  assert.ok(clones.every(a => a.includes('https://github.com/Adam-S-Daniel/skills-evals.git')));
  assert.ok(clones[1].includes('persistent/eval-results'));
  assert.ok(!clones[0].includes('--branch'), 'the hold is read from the default branch');
  const sparse = git.filter(a => a.includes('sparse-checkout'));
  assert.deepEqual(sparse.map(a => a.slice(a.indexOf('set') + 1)),
    [['scripts', 'harness', 'evals', 'vendor'], ['routine-results']]);
});

test('a scheduled loop fire goes when the hold is off', () => {
  const { result, curl } = runFire({ role: 'loop', line: '20 6,18 * * *', hold: 'pace_hold=false' });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.equal(curl.length, 1);
});

test('an unreadable pace hold fails the loop fire closed, and fires nothing', () => {
  for (const over of [{ hold: '' }, { hold: 'something else' }, { gitStatus: 128 }]) {
    const { result, curl } = runFire({ role: 'loop', line: '20 6,18 * * *', ...over });
    assert.equal(result.status, 1, JSON.stringify(over));
    assert.equal(curl.length, 0, JSON.stringify(over));
    assert.ok(result.stdout.includes('could not read the pace hold'), JSON.stringify(over));
  }
});

test('the pace hold never gates the fleet worker, a dispatch or a skipped cadence slot', () => {
  for (const over of [{ role: 'fleet', hold: 'pace_hold=true' },
    { role: 'loop', event: 'workflow_dispatch', hold: 'pace_hold=true' }]) {
    const { result, curl, git } = runFire(over);
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.equal(curl.length, 1, JSON.stringify(over));
    assert.equal(git.length, 0, JSON.stringify(over));
  }
  // A slot off the loop's cadence skips before any clone.
  const { curl, git } = runFire({ role: 'loop', every: '12', line: '20 6,18 * * *', hold: 'pace_hold=true' });
  assert.equal(curl.length, 0);
  assert.equal(git.length, 0);
});
