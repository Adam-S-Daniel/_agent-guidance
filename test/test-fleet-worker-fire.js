#!/usr/bin/env node
'use strict';

// Checks for .github/workflows/fleet-worker-fire.yml, which fires the fleet
// worker routine every 3 hours. The YAML is parsed, never scanned by line, and
// the step's own script runs against a fake curl on PATH.

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
// Every slot of a 3-hour schedule, each line on the longest cadence it serves.
const LINES = ['20 0 * * 2', '20 0 * * 0,1,3-6', '20 12 * * *', '20 6,18 * * *', '20 3,9,15,21 * * *'];

test('fires every 3 hours or by hand, with no permissions, no uses and no interpolation in run', () => {
  const d = doc();
  assert.deepEqual(Object.keys(d.on).sort(), ['schedule', 'workflow_dispatch']);
  assert.deepEqual(d.on.schedule, LINES.map(cron => ({ cron })));
  assert.deepEqual(d.permissions, {});
  assert.equal(d.concurrency['cancel-in-progress'], false);
  const job = d.jobs.fire;
  assert.equal(job.permissions, undefined, 'the job must not widen permissions');
  assert.equal(job.steps.length, 1);
  assert.ok(!('uses' in step()));
  assert.ok(!/\$\{\{/.test(step().run), 'no ${{ }} expression may reach a run: block');
  assert.deepEqual(step().env, {
    FIRE_URL: '${{ vars.FLEET_WORKER_FIRE_URL }}',
    FIRE_BEARER: '${{ secrets.FLEET_WORKER_FIRE_BEARER }}',
    EVERY_HOURS: '${{ vars.FLEET_WORKER_EVERY_HOURS }}',
  });
});

test('actions.lock lists the workflow with no actions', () => {
  const lock = YAML.parse(fs.readFileSync(path.join(root, '.github/workflows/actions.lock'), 'utf8'));
  assert.deepEqual(lock.workflows[workflow], []);
});

function runFire({ url = 'https://api.example.com/fire', bearer = 'placeholder-value', status = 200,
  every = '', event = 'schedule', line = '20 3,9,15,21 * * *', hour = '9' } = {}) {
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
    const eventPath = path.join(temp, 'event.json');
    fs.writeFileSync(eventPath, JSON.stringify(event === 'schedule' ? { schedule: line } : {}));
    const result = spawnSync('bash', ['-c', step().run], { encoding: 'utf8', env: {
      PATH: [temp, process.env.PATH].join(path.delimiter), TEST_LOG: log, TEST_STATUS: String(status),
      FIRE_URL: url, FIRE_BEARER: bearer, EVERY_HOURS: every, GITHUB_EVENT_NAME: event,
      GITHUB_EVENT_PATH: eventPath, TEST_HOUR: hour } });
    const curl = fs.existsSync(log) ? fs.readFileSync(log, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)) : [];
    return { result, curl };
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

test('FLEET_WORKER_EVERY_HOURS thins scheduled fires and never blocks a dispatch', () => {
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
  const hoursOf = l => l.split(' ')[1].split(',').map(Number);
  const daysOf = l => l.split(' ')[4] === '*' ? 7 : l.split(' ')[4].split(',').reduce((n, p) => {
    const [a, b = a] = p.split('-').map(Number);
    return n + b - a + 1;
  }, 0);
  const slots = LINES.reduce((n, l) => n + hoursOf(l).length * daysOf(l), 0);
  assert.equal(slots, 56);
  for (const [every, want] of [['3', 56], ['6', 28], ['12', 14], ['24', 7], ['168', 1]]) {
    let fired = 0;
    for (const line of LINES) fired += runFire({ every, line }).curl.length * hoursOf(line).length * daysOf(line);
    assert.equal(fired, want, `every ${every}`);
  }
});

test('an unknown schedule line fails naming it, and fires nothing', () => {
  const { result, curl } = runFire({ every: '6', line: '20 */3 * * *' });
  assert.equal(result.status, 1);
  assert.equal(curl.length, 0);
  assert.ok(result.stdout.includes('unknown schedule line'));
});
