#!/usr/bin/env node
'use strict';

// Policy checks for the prompt-audit sweep (ADR 0017): the sweep spec, the
// changelog routine's step 4a that fires it, and the ADR index. Markdown is
// parsed with markdown-it, never scanned by line. PROMPT_AUDIT_MUTATION
// alters one in-memory document so a negative control can prove a check
// fails; live files are never written.

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
const spec = 'docs/routines/prompt-audit-sweep.md';
const routine = 'docs/reference/agent-changelog-routine.md';
const adr = 'docs/decisions/0017-prompt-audit-runs-as-an-event-triggered-sweep.md';
const runs = 'docs/reference/prompt-audit-runs.md';
const index = 'docs/decisions/README.md';
const worker = 'docs/routines/fleet-worker.md';
const issueWorker = 'docs/reference/agent-issue-worker.md';
const adr25 = 'docs/decisions/0025-a-cloud-fleet-worker-merges-narrow-instruction-fixes.md';
const adr28 = 'docs/decisions/0028-the-fleet-worker-also-merges-sweep-fixes-to-skill-text.md';

const mutations = {
  mergeall: [worker, '**Merge only what the merge rule (below) allows.**', '**Merge any green PR.**'],
  approve: [worker, 'never\n  approve a deployment waiting on an environment', 'approve\n  a deployment waiting on an environment'],
  stamp: [worker, 'exactly `Independent review: CLEAN at <the full head sha>`', 'exactly `Independent review: CLEAN`'],
  managed: [worker, 'below the `## Repo-specific additions` heading in both', 'anywhere in'],
  frontmatter: [worker, 'is byte-identical\n     in the base and the head', 'may change\n     in the base and the head'],
  allowlist: [worker, 'whose diff\n     only removes lines', 'whose diff\n     changes any lines'],
  nodelete: [worker, 'No file is deleted or renamed.', 'Files may be deleted.'],
  dependabot: [worker, 'Skip Dependabot PRs:', 'Include Dependabot PRs:'],
  expected: [worker, 'with `expected_head_sha` set to the head you read', 'with no expected head'],
  once: [worker, '**Only a\nPR that just gained the label is news.**', '**Every labeled PR is news.**'],
  joblink: [worker, '`https://github.com/<owner>/<repo>/actions/runs/<run_id>/job/<job_id>`', 'the PR page'],
  owners: [spec, 'and `jodidaniel`, and holds', 'and holds'],
  diffs: [spec, 'never apply one, in whole or in part', 'apply it when confident'],
  label: [routine, '- **No label.** It is not `agent-ready`', '- **Label `agent-ready`.** It is'],
  prefix: [routine, '**Title:** `Prompt-audit sweep trigger: Claude', '**Title:** `Prompt audit trigger: Claude'],
  warn: [adr, '**warns and never blocks**', '**blocks**'],
  payload: [routine, "'{text: $t}'", "'{text: ($t + \" DRY_RUN\")}'"],
  index: [index, '| [0016](0016-the-freshest', '| [0017](0016-the-freshest'],
  dedupe: [spec, '`^Prompt-audit sweep \\d{4}-\\d{2}-\\d{2}: `', '`^Prompt-audit sweep `'],
  canonical: [spec, 'dedupe on `full_name`', 'take the first that answers'],
  private: [spec, 'name a private repo and its finding count **only**', 'name a private repo with its findings'],
  pushonly: [spec, '**Push only to `Adam-S-Daniel/_agent-guidance`.**', '**Push to any reached repo.**'],
  fixed: [spec, '`git merge-base --is-ancestor <sha> origin/persistent/prompt-audit-runs`', '`git merge-base --is-ancestor <sha> origin/main`'],
  fforce: [spec, 'Never force it.', 'Force it when needed.'],
  retry: [spec, 'Try at most 3 times;', 'Try as often as needed;'],
  sixhours: [spec, 'started less than 6 hours ago means', 'started less than 6 days ago means'],
  nopr: [spec, '- no pull request: the sweep never opens, updates or merges one;', '- one draft pull request, which the sweep may update;'],
  skip: [spec, 'second one: comment on it instead,', 'second one: skip the repo instead,'],
  newonly: [spec, 'The comment holds only findings that neither', 'The comment repeats every finding, including ones that neither'],
  relabel: [spec, "Never change that issue's labels, title or body.", 'Relabel it as needed.'],
  closeall: [spec, 'leave the issue\n  open and add the `agent-ready` label', 'close the issue\n  and add the `agent-ready` label'],
  argv: [routine, '|\n     curl -sS --config - ', '|\n     curl -sS -H "Authorization: Bearer $PROMPT_AUDIT_SWEEP_FIRE_BEARER" '],
};

const read = name => {
  let source = fs.readFileSync(path.join(root, name), 'utf8');
  const mutation = mutations[process.env.PROMPT_AUDIT_MUTATION];
  if (mutation && mutation[0] === name) {
    assert.ok(source.includes(mutation[1]), `mutation anchor missing: ${mutation[1]}`);
    source = source.replace(mutation[1], mutation[2]);
  }
  return source;
};
const parsed = new Map();
const tokens = name => {
  if (!parsed.has(name)) parsed.set(name, md.parse(read(name), {}));
  return parsed.get(name);
};
function section(name, heading) {
  const all = tokens(name);
  const start = all.findIndex((t, i) => t.type === 'heading_open' && all[i + 1].content === heading);
  assert.notEqual(start, -1, `${name}: missing heading ${heading}`);
  const end = all.findIndex((t, i) => i > start && t.type === 'heading_open' && t.tag <= all[start].tag);
  return all.slice(start + 3, end < 0 ? undefined : end);
}
const prose = list => list.filter(t => t.type === 'inline').map(t => t.content).join(' ').replaceAll('\n', ' ');
function includes(text, phrases) {
  for (const phrase of phrases) assert.ok(text.includes(phrase), `missing: ${phrase}`);
}
const links = list => list.flatMap(t => t.children || []).filter(c => c.type === 'link_open').map(c => c.attrGet('href'));

test('sweep covers the declared fleet across both owners', () => {
  const body = prose(section(spec, 'Repos considered'));
  includes(body, ["`cron_coverage.fleet` key on `origin/main`", '`Adam-S-Daniel` and `jodidaniel`', 'Never guess the owner', 'never treated as clean', 'dedupe on `full_name`', 'one canonical name is one repo']);
  const fleet = YAML.parse(read('repos.yml')).cron_coverage.fleet;
  assert.ok(Array.isArray(fleet) && fleet.includes('_agent-guidance') && fleet.length >= 10);
  const owners = Object.values(YAML.parse(read('.github/workflows/sync.yml')).jobs)
    .flatMap(job => job.steps).map(step => step.env?.SYNC_OWNERS).filter(Boolean);
  for (const owner of String(owners[0]).split(/[\s,]+/).filter(Boolean)) assert.ok(body.includes(`\`${owner}\``), `owner ${owner} not named`);
});

test('sweep is read-only, never merges, and files one labeled issue per repo', () => {
  includes(prose(section(spec, 'Constraints')), ['never apply one, in whole or in part', 'no pull request: the sweep never opens, updates or merges one', 'one comment on an open earlier sweep issue', 'at most one per repo', 'created with the `agent-ready` label and no other label', 'never fire another routine', 'on-hold']);
  includes(prose(section(spec, '4. Render one issue per repo')), ['One issue per repo', 'still open there', 'Do not apply the audit\'s proposed diff', 'Do not delete dated incident evidence']);
  includes(prose(section(spec, '5. File and verify')), ['`"labels": ["agent-ready"]` in the create call itself', 'raw REST `GET`']);
  includes(prose(section(spec, '3. Route every finding')), ['routes to `_agent-guidance`', '**warns and never blocks**', 'Confidence Low** stays in the run log']);
  includes(prose(section(spec, '0. Before anything else')), ['prints `true`, `firstParty` and either `claude.ai` or `oauth_token`', '`oauth_token` means an OAuth token supplied through the environment', 'BLOCKED: nested claude is not on the subscription (loggedIn=<loggedIn>, apiProvider=<apiProvider>, authMethod=<authMethod>)', '`ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` must be unset', 'is **not verified**', 'Never imitate the audit']);
});

test('each run logs on persistent/prompt-audit-runs and opens no pull request', () => {
  const branch = 'persistent/prompt-audit-runs';
  const rules = prose(section(spec, 'Constraints'));
  includes(rules, [`on the fixed branch \`${branch}\``, 'Nothing merges that branch', 'deletion-only ruleset on `persistent/**`', 'Pushing a branch the harness did not assign works', 'no pull request: the sweep never opens, updates or merges one', '**Push only to `Adam-S-Daniel/_agent-guidance`.**', 'is read-only', 'never push to a default branch, never force-push, never merge']);
  assert.ok(links(section(spec, 'Constraints')).some(h => h.endsWith('0007-persistent-branches-use-the-persistent-prefix-and-a-deletion-ruleset.md')), 'no link to repo-settings ADR 0007');
  const start = prose(section(spec, '0. Before anything else'));
  includes(start, [`\`${branch}\``, 'Fetch `origin persistent/prompt-audit-runs`', 'create the branch from `origin/main`', 'merge `origin/main` into it with a normal merge commit, never a rebase or a force', 'started less than 6 hours ago', 'BLOCKED: another sweep is in progress (started <time>)', '`abandoned (no result after 6 hours)`', 'push with a plain `git push origin HEAD:persistent/prompt-audit-runs`. Never force it.', 'fast-forward of the head this run just checked', 'A rejected push means another run got there first', 'Try at most 3 times', 'After 3 rejected tries, stop', 'BLOCKED: could not record the start on persistent/prompt-audit-runs (<status>)', 'never a response body', 'Never force-push or delete the branch']);
  const finish = prose(section(spec, '7. Finish'));
  includes(finish, [`\`git merge-base --is-ancestor <sha> origin/${branch}\``, 'No pull request is opened, updated or merged']);
  includes(prose(section(spec, '6. Write the run log')), ['Lines are appended in push order', 'push as a fast-forward (never force)', 'up to 3 times']);
  const all = tokens(spec).flatMap(t => [t, ...(t.children || [])]);
  const text = all.filter(t => t.type === 'inline').map(t => t.content).join(' ');
  for (const gone of ['git branch --show-current', 'sweep PR', 'Prompt-audit sweep: ', 'duplicate start', 'PR body']) {
    assert.ok(!text.includes(gone), `spec still names removed text: ${gone}`);
  }
  const code = all.filter(t => t.type === 'code_inline' || t.type === 'fence').map(t => t.content);
  assert.ok(code.some(c => c === branch || c.includes(`HEAD:${branch}`)), 'spec never names the persistent branch in code');
});

test('an open earlier sweep issue gets a comment of new findings, not a skip', () => {
  const step = prose(section(spec, '4. Render one issue per repo'));
  includes(step, ['do not file a second one: comment on it instead', 'at most one open sweep issue', 'The comment holds only findings that neither the body nor an earlier comment already lists', 'headed `Prompt-audit sweep <date>: <n> new findings`', 'same lint and private-repo rules', 'post no comment and log `still open: <link>, no new findings`', 'log `updated: <link>`', "Never change that issue's labels, title or body", 'agent-working', 'still comment', 'found again by the next sweep, because the audit re-reads the files', 'A trigger issue', 'never counts as an earlier sweep issue']);
  assert.ok(!step.includes('deferred:'), 'step 4 still defers');
  includes(prose(section(spec, '5. File and verify')), ['Under `DRY_RUN`, skip this step: no issue and no comment', 'REST `GET` of that comment']);
  includes(prose(section(spec, 'Constraints')), ['one comment on an open earlier sweep issue in a repo in **Repos considered**', 'carrying only new findings', "never changes that issue's labels, title or body", 'nothing else on any issue it did not create']);
  includes(prose(section(spec, '6. Write the run log')), ['`updated: <link>`', '`still open: <link>, no new findings`']);
});

test('a trigger issue with hands-on checks stays open and gets agent-ready', () => {
  const finish = prose(section(spec, '7. Finish'));
  includes(finish, ['comment `Swept: <run-log line link and issue links>`', 'any item other than the one about the sweep logging a result is still unchecked (`- [ ]`)', 'leave the issue open and add the `agent-ready` label', 'Otherwise close it as completed', 'On a `BLOCKED` stop, comment the `BLOCKED` line and leave it open with no label', 'Under `DRY_RUN`, change nothing on it']);
  includes(prose(section(spec, 'Constraints')), ['then either its closing or the `agent-ready` label']);
  includes(prose(section(spec, 'Switches')), ['change nothing on the trigger issue']);
});

test('earlier-sweep dedupe matches sweep issues but never the trigger issue', () => {
  const text = prose(section(spec, '4. Render one issue per repo'));
  const pattern = text.match(/regular expression `([^`]+)`/);
  assert.ok(pattern, 'no dedupe pattern');
  const re = new RegExp(pattern[1]);
  const title = text.match(/\*\*Title:\*\* `Prompt-audit sweep <date>: ([^`]+)`/);
  assert.ok(title, 'no sweep issue title');
  assert.ok(re.test(`Prompt-audit sweep 2026-10-05: ${title[1].replace('<n>', '3')}`), 'pattern misses a sweep issue');
  const trigger = prose(section(routine, '4a. Fire the prompt-audit sweep')).match(/\*\*Title:\*\* `([^`]+)`/)[1];
  assert.ok(!re.test(trigger.replace('<version or range>', '2.1.290').replace('<what changed>', 'adds a model')), 'pattern defers on the trigger issue');
});

test('private repos appear in public outputs as name and count only', () => {
  const text = prose(section(spec, 'Private repos'));
  includes(text, ['`private` field is `true` at run time', 'never rely on that list, read the field', 'name a private repo and its finding count **only**', 'the run log, `_agent-guidance`\'s issue', 'including a cross-repo item', 'push notification', 'no quote, no path']);
  includes(prose(section(spec, '6. Write the run log')), ['A private repo gets `private, reached` and its finding count']);
  includes(prose(section(adr, 'Decision')), ['names a private repo', 'finding count **only** in every public output']);
});

test('trigger issue prefix and payload agree between the routine and the sweep', () => {
  const step = section(routine, '4a. Fire the prompt-audit sweep');
  const title = prose(step).match(/\*\*Title:\*\* `([^`]*?: )/);
  assert.ok(title, 'routine names no trigger-issue title');
  const accepted = prose(section(spec, 'The trigger')).match(/title starts with exactly `([^`]+)`/);
  assert.ok(accepted, 'sweep names no accepted title prefix');
  assert.equal(title[1], accepted[1]);
  includes(prose(step), ['**No label.** It is not `agent-ready`', 'never retry', 'File nothing and fire nothing', 'Codex releases never fire it']);
  includes(prose(section(routine, 'Constraints')), ['which it files with no label at all', 'at most once per run']);
});

test('the documented fire call sends only the issue URL and never prints the bearer', () => {
  const fence = section(routine, '4a. Fire the prompt-audit sweep').find(t => t.type === 'fence' && t.info === 'bash');
  assert.ok(fence, 'no bash fence in step 4a');
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'sweep-fire-test-'));
  try {
    const log = path.join(temp, 'curl-args.json');
    fs.writeFileSync(path.join(temp, 'curl'), `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
const config = args[args.indexOf('--config') + 1] === '-' ? fs.readFileSync(0, 'utf8') : '';
fs.writeFileSync(process.env.TEST_LOG, JSON.stringify({ args, config }));
fs.writeFileSync(args[args.indexOf('-o') + 1], '{"claude_code_session_url":"https://claude.ai/code/session_x"}');
process.stdout.write('200');
`, { mode: 0o755 });
    const issue = 'https://github.com/Adam-S-Daniel/_agent-guidance/issues/1';
    const script = `${fence.content}\nprintf '%s' "$code"`;
    const result = spawnSync('bash', ['-c', script], { encoding: 'utf8', env: { ...process.env, PATH: [temp, process.env.PATH].join(path.delimiter), TEST_LOG: log, TRIGGER_ISSUE_URL: issue, PROMPT_AUDIT_SWEEP_FIRE_URL: 'https://api.example.com/fire', PROMPT_AUDIT_SWEEP_FIRE_BEARER: 'placeholder-value' } });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, '200');
    assert.ok(!result.stdout.includes('placeholder-value') && !result.stderr.includes('placeholder-value'));
    const { args, config } = JSON.parse(fs.readFileSync(log, 'utf8'));
    assert.deepEqual(JSON.parse(args[args.indexOf('-d') + 1]), { text: issue });
    assert.ok(!args.some(a => a.includes('placeholder-value')), 'bearer must not be in curl argv');
    assert.equal(config, 'header = "Authorization: Bearer placeholder-value"\n');
    for (const header of ['anthropic-beta: experimental-cc-routine-2026-04-01', 'anthropic-version: 2023-06-01', 'Content-Type: application/json']) {
      assert.ok(args.includes(header), `missing header ${header}`);
    }
    assert.equal(args[args.indexOf('-X') + 1], 'POST');
    assert.ok(args.includes('https://api.example.com/fire'));
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});

test('ADR 0017 records the trigger, subscription usage and warn-only checks', () => {
  includes(prose(section(adr, 'Decision')), ['**no schedule**', 'The changelog routine fires it', 'The owner starts it by hand', 'subscription', '`agent-ready`', '**warns and never blocks**', 'never merges or auto-merges']);
  includes(prose(section(adr, 'Consequences')), ['`CLAUDE_CODE_REMOTE`', 'never attached to `api.anthropic.com`', 'dedicated `Changelog Routine`', 'metered overage']);
  includes(prose(section(routine, 'The trigger')), ['dedicated `Changelog Routine`', 'plus `api.anthropic.com`', 'only environment that holds']);
  for (const doc of [adr, spec]) {
    for (const href of links(tokens(doc)).filter(h => !/^[a-z]+:/.test(h))) {
      assert.ok(fs.existsSync(path.join(root, path.dirname(doc), href.split('#')[0])), `${doc}: dangling link ${href}`);
    }
  }
  assert.ok(fs.existsSync(path.join(root, runs)));
});

test('every ADR index row is numbered like the file it links', () => {
  const rows = links(tokens(index)).filter(h => /^\d{4}-.*\.md$/.test(h));
  assert.ok(rows.length >= 17);
  const labels = tokens(index).flatMap(t => t.children || []).reduce((acc, c, i, all) => {
    if (c.type === 'link_open' && /^\d{4}-/.test(c.attrGet('href'))) acc.push([all[i + 1].content, c.attrGet('href')]);
    return acc;
  }, []);
  for (const [label, href] of labels) {
    assert.equal(label, href.slice(0, 4), `index row ${label} links ${href}`);
    assert.ok(fs.existsSync(path.join(root, 'docs/decisions', href)), `missing ${href}`);
  }
  assert.ok(rows.includes(path.basename(adr)));
});

test('fleet worker merges only narrow, reviewed instruction fixes and approves nothing', () => {
  includes(prose(section(worker, 'Constraints')), ['**Merge only what the merge rule (below) allows.**', 'never enable auto-merge', 'never force-push', 'Never submit an APPROVE review', 'never approve a deployment waiting on an environment', '`plugin-runtime-review`', 'on-hold']);
  const merge = prose(section(worker, '2. Review and merge a narrow instruction fix'));
  includes(merge, ['`agent/issue-<n>-<slug>`', '`^Prompt-audit sweep \\d{4}-\\d{2}-\\d{2}: `', 'Every changed file is one of these.', 'a `CLAUDE.md` or `AGENTS.md` at any depth', 'a `SKILL.md` at any depth whose frontmatter', 'is byte-identical in the base and the head', 'No other file in a skill\'s directory qualifies.', 'a `.freshness-allow.yml` at any depth whose diff only removes lines', 'In a `SKILL.md`, `CLEAN` also requires that the frontmatter is untouched', 'No file is deleted or renamed.', 'below the `## Repo-specific additions` heading in both', 'exactly `Independent review: CLEAN at <the full head sha>`', 'A push after the stamp makes it stale', 'a fresh prompt', 'never this run\'s other context', '`expected_head_sha` set to the reviewed head', 'which squash']);
  const sweepRe = prose(section(spec, '4. Render one issue per repo')).match(/regular expression `([^`]+)`/)[1];
  assert.ok(merge.includes(`\`${sweepRe}\``), 'merge rule and sweep disagree on the sweep issue title');
  includes(prose(section(adr25, 'Decision')), ['separately prompted subagent', 'stays with the owner', 'never approves a deployment']);
  includes(prose(section(adr28, 'Decision')), ['byte-identical', 'only removes lines', 'never approves anything', 'Memory-promotion fixes stay limited']);
  includes(prose(section(issueWorker, 'Where it runs')), ['**The owner turns that laptop task off**']);
});

test('fleet worker updates PRs a required check never ran on and tells the owner once', () => {
  const update = prose(section(worker, '1. Bring it up to date when a required check never ran'));
  includes(update, ['`required_status_checks`', 'with **no** check run and **no** status at all', '`behind_by` above 0', 'with `expected_head_sha` set to the head you read', 'Skip Dependabot PRs:', 'do not resolve it here']);
  const tell = prose(section(worker, '3. Tell the owner which PRs need him'));
  includes(tell, ['`waiting` on an environment', '`https://github.com/<owner>/<repo>/actions/runs/<run_id>/job/<job_id>`', '`needs-owner` label once', '**Only a PR that just gained the label is news.**', 'starts with `Needs you:`']);
  includes(prose(section(worker, 'The trigger')), ['Push notifications on', 'Read and follow `docs/routines/fleet-worker.md`']);
});
