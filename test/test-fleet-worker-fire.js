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

test('fires every 3 hours or by hand, with no permissions, no uses and no interpolation in run', () => {
  const d = doc();
  assert.deepEqual(Object.keys(d.on).sort(), ['schedule', 'workflow_dispatch']);
  assert.deepEqual(d.on.schedule, [{ cron: '20 */3 * * *' }]);
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
  });
});

test('actions.lock lists the workflow with no actions', () => {
  const lock = YAML.parse(fs.readFileSync(path.join(root, '.github/workflows/actions.lock'), 'utf8'));
  assert.deepEqual(lock.workflows[workflow], []);
});

function runFire({ url = 'https://api.example.com/fire', bearer = 'placeholder-value', status = 200 } = {}) {
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
    const result = spawnSync('bash', ['-c', step().run], { encoding: 'utf8', env: {
      PATH: [temp, process.env.PATH].join(path.delimiter), TEST_LOG: log, TEST_STATUS: String(status),
      FIRE_URL: url, FIRE_BEARER: bearer } });
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
