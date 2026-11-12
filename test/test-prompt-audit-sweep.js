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
const adr28 = 'docs/decisions/0028-the-fleet-worker-merges-skill-guidance-and-partial-fixes.md';

const mutations = {
  mergeall: [worker, '**Merge only what the merge rule (below) allows.**', '**Merge any green PR.**'],
  approve: [worker, 'never\n  approve a deployment waiting on an environment', 'approve\n  a deployment waiting on an environment'],
  stamp: [worker, 'exactly `Independent review: CLEAN at <the full head sha>`', 'exactly `Independent review: CLEAN`'],
  managed: [worker, 'below the `## Repo-specific additions` heading in both', 'anywhere in'],
  frontmatter: [worker, 'is byte-identical in the base and the\n     head', 'may change in the base and the\n     head'],
  allowlist: [worker, 'whose diff\n     only removes lines', 'whose diff\n     changes any lines'],
  noverdict: [worker, 'post nothing: the PR has no stamp, so it is never merged', 'merge it anyway'],
  guidanceappend: [worker, '`docs/guidance-impact.md`, whose diff only adds lines', '`docs/guidance-impact.md`, any change'],
  nodelete: [worker, 'No file is deleted or renamed.', 'Files may be deleted.'],
  dependabot: [worker, 'Skip Dependabot PRs:', 'Include Dependabot PRs:'],
  expected: [worker, 'with `expected_head_sha` set to the head you read', 'with no expected head'],
  once: [worker, '**Only a\nPR that just gained the label is news.**', '**Every labeled PR is news.**'],
  issueonce: [worker, '**Only an issue that just\ngained the label is news.**', '**Every blocked issue is news.**'],
  issueonhold: [worker, '`on-hold` issues\nleft out', '`on-hold` issues\nincluded'],
  joblink: [worker, '`https://github.com/<owner>/<repo>/actions/runs/<run_id>/job/<job_id>`', 'the PR page'],
  owners: [spec, 'and `jodidaniel`, and holds', 'and holds'],
  diffs: [spec, 'never apply one, in whole or in part', 'apply it when confident'],
  label: [routine, '- **No label.** It is not `agent-ready`', '- **Label `agent-ready`.** It is'],
  prefix: [routine, '**Title:** `Prompt-audit sweep trigger: Claude', '**Title:** `Prompt audit trigger: Claude'],
  warn: [adr, '**warns and never blocks**', '**blocks**'],
  payload: ['.github/workflows/prompt-audit-fire.yml', "'{text: $t}'", "'{text: ($t + \" DRY_RUN\")}'"],
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
  skillimpact: [worker, '`docs/skill-impact.md`, whose diff only\n     adds lines', '`docs/skill-impact.md`, any change'],
  refsrequeue: [worker, 'swap\n`agent-working` for `agent-ready`', 'leave\n`agent-working` on the issue, not'],
  protectedsweep: [spec, 'On a protected subject, keep a finding only if it is one of these', 'On a protected subject, keep every finding'],
  protectedmerge: [worker, '7. **The eval-protection gate exits 0**', '7. **The eval-protection gate is advisory**'],
  curationline: ['docs/routines/memory-curation.md', '`Lessons: read <n>, promoted <m>`', '`Lessons promoted: <m>`'],
  curationdedupe: ['docs/routines/memory-curation.md', 'search the target repo\'s\nissues, open and closed, for that line', 'trust your memory of\nearlier runs'],
  everyrun: [worker, '**every run does all three**', '**a run may stop after any pass**'],
  lessonsline: [worker, '`Lessons: read <n>, new <k>, handled <m>`', '`Lessons handled: <m>`'],
  lessonsfetch: [worker, '`git -C <scratch>/lessons ls-tree -r --name-only HEAD -- lessons/proposals/`', '`list the branch with the connector`'],
  agentstamp: [worker, 'whose first line is\n   exactly `Independent review: CLEAN at <the full head sha>`, written by\n   the review below.', 'whose first line is\n   `Independent review: CLEAN`, written by\n   the review below.'],
  agentcms: [worker, 'in a fleet repo other than the three\n     cms-platform-managed repos', 'in any fleet repo, the three\n     cms-platform-managed repos included'],
  agentage: [worker, 'at least 1 hour before the run\'s\n   start', 'any time before the run\'s\n   start'],
  agentauthority: [worker, 'Such a change always goes to the owner.', 'Such a change is fine.'],
  hookfiles: [worker, 'the\n     changed files are exactly `repos.yml` and', 'the\n     changed files include `repos.yml` and'],
  argv: ['.github/workflows/prompt-audit-fire.yml', "curl -sS --config - -o", 'curl -sS -H "Authorization: Bearer $FIRE_BEARER" -o'],
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
  includes(finish, ['names a question the audit did not answer', 'as a `- [ ]` item in the `Swept:` comment']);
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
  includes(prose(step), ['**No label.** It is not `agent-ready`', 'never fires by hand', 'File nothing, so nothing fires', 'Codex releases never fire it', '`sweep: requested <issue link>`']);
  includes(prose(section(routine, 'Constraints')), ['which it files with no label at all', 'The run fires no routine']);
});

const fireWorkflow = '.github/workflows/prompt-audit-fire.yml';
const fireDoc = () => YAML.parse(read(fireWorkflow));
const fireStep = () => fireDoc().jobs.fire.steps[0];

test('the fire workflow is narrow: owner-only trigger issues, no uses, no interpolation in run', () => {
  const doc = fireDoc();
  assert.deepEqual(Object.keys(doc.on).sort(), ['issues', 'workflow_dispatch']);
  assert.deepEqual(doc.on.issues.types, ['opened']);
  assert.equal(doc.on.workflow_dispatch.inputs.issue_number.required, true);
  assert.deepEqual(doc.permissions, {});
  const job = doc.jobs.fire;
  assert.deepEqual(job.permissions, { issues: 'write' });
  assert.equal(job['runs-on'], 'ubuntu-24.04');
  assert.equal(job.concurrency['cancel-in-progress'], false);
  assert.ok(job.concurrency.group.includes('issue.number') && job.concurrency.group.includes('inputs.issue_number'));
  includes(job.if, ["startsWith(github.event.issue.title, 'Prompt-audit sweep trigger: ')", "github.event.issue.user.login == 'Adam-S-Daniel'", "github.event.issue.author_association == 'OWNER'"]);
  assert.equal(doc.jobs.fire.steps.length, 1);
  for (const step of Object.values(doc.jobs).flatMap(j => j.steps)) {
    assert.ok(!('uses' in step), 'the fire workflow must have no uses: step');
    assert.ok(!/\$\{\{/.test(step.run), 'no ${{ }} expression may reach a run: block');
  }
  assert.equal(fireStep().env.FIRE_BEARER, '${{ secrets.PROMPT_AUDIT_SWEEP_FIRE_BEARER }}');
  assert.equal(fireStep().env.FIRE_URL, '${{ vars.PROMPT_AUDIT_SWEEP_FIRE_URL }}');
});

// Runs the workflow's own script with a fake gh and curl on PATH.
function runFire({ issue, comments = [], url = 'https://api.example.com/fire', bearer = 'placeholder-value', status = 200, dispatch = false }) {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'sweep-fire-test-'));
  try {
    const log = path.join(temp, 'calls.jsonl');
    const state = path.join(temp, 'state.json');
    fs.writeFileSync(state, JSON.stringify({ issue, comments, status }));
    fs.writeFileSync(path.join(temp, 'curl'), `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
const config = args[args.indexOf('--config') + 1] === '-' ? fs.readFileSync(0, 'utf8') : '';
fs.appendFileSync(process.env.TEST_LOG, JSON.stringify({ tool: 'curl', args, config }) + '\\n');
const { status } = JSON.parse(fs.readFileSync(process.env.TEST_STATE, 'utf8'));
fs.writeFileSync(args[args.indexOf('-o') + 1], '{"claude_code_session_url":"https://claude.ai/code/session_x","secret_field":"response-canary"}');
process.stdout.write(String(status));
`, { mode: 0o755 });
    fs.writeFileSync(path.join(temp, 'gh'), `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.appendFileSync(process.env.TEST_LOG, JSON.stringify({ tool: 'gh', args }) + '\\n');
const st = JSON.parse(fs.readFileSync(process.env.TEST_STATE, 'utf8'));
if (args[0] === 'api' && args.some(a => a.endsWith('/comments'))) process.stdout.write(st.comments.join('\\n') + '\\n');
else if (args[0] === 'api') process.stdout.write(JSON.stringify(st.issue));
`, { mode: 0o755 });
    const event = path.join(temp, 'event.json');
    fs.writeFileSync(event, JSON.stringify(dispatch
      ? { repository: { full_name: 'Adam-S-Daniel/_agent-guidance' }, inputs: { issue_number: String(issue.number) } }
      : { repository: { full_name: 'Adam-S-Daniel/_agent-guidance' }, issue: { number: issue.number } }));
    const result = spawnSync('bash', ['-c', fireStep().run], { encoding: 'utf8', env: {
      PATH: [temp, process.env.PATH].join(path.delimiter), TEST_LOG: log, TEST_STATE: state,
      GITHUB_EVENT_PATH: event, GITHUB_EVENT_NAME: dispatch ? 'workflow_dispatch' : 'issues',
      GH_TOKEN: 'placeholder-token', FIRE_URL: url, FIRE_BEARER: bearer } });
    const calls = fs.existsSync(log) ? fs.readFileSync(log, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)) : [];
    return { result, calls, curl: calls.filter(c => c.tool === 'curl'), posted: calls.filter(c => c.tool === 'gh' && c.args[0] === 'issue') };
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
}
const triggerIssue = (over = {}) => ({ number: 7, title: 'Prompt-audit sweep trigger: Claude Code 2.1.999 adds a model', user: { login: 'Adam-S-Daniel' }, author_association: 'OWNER', html_url: 'https://github.com/Adam-S-Daniel/_agent-guidance/issues/7', ...over });
const commentBody = call => call.args[call.args.indexOf('--body') + 1];

test('the fire workflow sends only the issue URL, keeps the bearer off argv and out of every output, and comments once', () => {
  const { result, curl, posted } = runFire({ issue: triggerIssue() });
  assert.equal(result.status, 0, result.stderr);
  for (const text of [result.stdout, result.stderr, ...posted.map(commentBody)]) {
    for (const canary of ['placeholder-value', 'response-canary']) assert.ok(!text.includes(canary), `leaked ${canary}`);
  }
  assert.equal(curl.length, 1);
  const { args, config } = curl[0];
  assert.deepEqual(JSON.parse(args[args.indexOf('-d') + 1]), { text: 'https://github.com/Adam-S-Daniel/_agent-guidance/issues/7' });
  assert.ok(!args.some(a => a.includes('placeholder-value')), 'bearer must not be in curl argv');
  assert.equal(config, 'header = "Authorization: Bearer placeholder-value"\n');
  for (const header of ['anthropic-beta: experimental-cc-routine-2026-04-01', 'anthropic-version: 2023-06-01', 'Content-Type: application/json']) {
    assert.ok(args.includes(header), `missing header ${header}`);
  }
  assert.equal(args[args.indexOf('-X') + 1], 'POST');
  assert.ok(args.some(a => a === 'https://api.example.com/fire'), 'curl must POST to the configured fire URL');
  assert.equal(posted.length, 1);
  assert.equal(commentBody(posted[0]), '<!-- prompt-audit-fired -->\nPrompt-audit sweep started: https://claude.ai/code/session_x');
});

test('the fire workflow skips an issue it already fired for and anything that is not the owner\'s trigger issue', () => {
  const again = runFire({ issue: triggerIssue(), comments: ['<!-- prompt-audit-fired -->\nPrompt-audit sweep started: https://claude.ai/code/session_y'] });
  assert.equal(again.result.status, 0, again.result.stderr);
  assert.deepEqual([again.curl.length, again.posted.length], [0, 0]);
  for (const [name, over] of [['title', { title: 'Something else' }], ['author', { user: { login: 'someone-else' } }], ['association', { author_association: 'CONTRIBUTOR' }], ['pull request', { pull_request: {} }]]) {
    for (const dispatch of [false, true]) {
      const run = runFire({ issue: triggerIssue(over), dispatch });
      assert.notEqual(run.result.status, 0, `${name} (dispatch ${dispatch}) must not fire`);
      assert.deepEqual([run.curl.length, run.posted.length], [0, 0], name);
    }
  }
  assert.equal(runFire({ issue: triggerIssue(), dispatch: true }).curl.length, 1);
});

test('the fire workflow comments and fails instead of firing when unconfigured, and fails on a bad status', () => {
  for (const missing of [{ url: '' }, { bearer: '' }]) {
    const run = runFire({ issue: triggerIssue(), ...missing });
    assert.notEqual(run.result.status, 0, 'an unconfigured fire must fail the run, not pass it');
    assert.equal(run.curl.length, 0);
    assert.equal(run.posted.length, 1);
    assert.ok(commentBody(run.posted[0]).includes('start the sweep by hand'));
    assert.ok(commentBody(run.posted[0]).includes('re-run this workflow with this issue\'s number'));
    assert.ok(!commentBody(run.posted[0]).includes('<!-- prompt-audit-fired -->'));
    assert.ok(run.result.stdout.includes('fire URL or bearer not configured'));
  }
  const failed = runFire({ issue: triggerIssue(), status: 401 });
  assert.notEqual(failed.result.status, 0);
  assert.equal(failed.posted.length, 1);
  assert.equal(commentBody(failed.posted[0]), 'Prompt-audit sweep fire failed (HTTP 401)');
  assert.ok(!failed.posted.map(commentBody).join('').includes('response-canary'));
});

test('ADR 0017 records the trigger, subscription usage and warn-only checks', () => {
  includes(prose(section(adr, 'Decision')), ['**no schedule**', 'The changelog routine fires it', 'The owner starts it by hand', 'subscription', '`agent-ready`', '**warns and never blocks**', 'never merges or auto-merges']);
  includes(prose(section(adr, 'Consequences')), ['`CLAUDE_CODE_REMOTE`', 'never attached to `api.anthropic.com`', 'dedicated `Changelog Routine`', 'metered overage']);
  includes(prose(section(routine, 'The trigger')), ['dedicated `Changelog Routine`', 'plus `api.anthropic.com`', 'It holds no secret']);
  includes(prose(section(adr, 'Addendum (2026-11-04): the fire bearer moves to GitHub Actions')), ['readable by every session', 'never attaches secrets to `api.anthropic.com`', '`prompt-audit-fire.yml`', 'Actions secret']);
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
  includes(merge, ['`agent/issue-<n>-<slug>`', '`^Prompt-audit sweep \\d{4}-\\d{2}-\\d{2}: `', 'Every changed file is one of these.', 'a `CLAUDE.md` or `AGENTS.md` at any depth', 'a `SKILL.md` at any depth whose frontmatter', 'is byte-identical in the base and the head', '`Refs <owner>/<repo>#<n>`', 'in `_agent-guidance` only, the fleet guidance', 'byte-identical `.claude/hooks/fleet-guidance.md` mirror', '`docs/guidance-impact.md`, whose diff only adds lines', 'no rule is weakened or dropped', 'If the subagent cannot run or gives no verdict, post nothing', 'it is never merged', 'No other file in a skill\'s directory qualifies.', 'a `.freshness-allow.yml` at any depth whose diff only removes lines', 'In a `SKILL.md`, `CLEAN` also requires that the frontmatter is untouched', 'No file is deleted or renamed.', 'below the `## Repo-specific additions` heading in both', 'exactly `Independent review: CLEAN at <the full head sha>`', 'A push after the stamp makes it stale', 'a fresh prompt', 'never this run\'s other context', '`expected_head_sha` set to the reviewed head', 'which squash', 'in `Adam-S-Daniel/adam-agentskills` only', '`docs/skill-impact.md`, whose diff only adds lines', 'the `PURPOSE.md` in the same directory as a changed `SKILL.md`', 'each changed skill has a new `docs/skill-impact.md` entry', 'swap `agent-working` for `agent-ready` and comment which findings the merged PR fixed']);
  const sweepRe = prose(section(spec, '4. Render one issue per repo')).match(/regular expression `([^`]+)`/)[1];
  assert.ok(merge.includes(`\`${sweepRe}\``), 'merge rule and sweep disagree on the sweep issue title');
  includes(prose(section(adr25, 'Decision')), ['separately prompted subagent', 'stays with the owner', 'never approves a deployment']);
  includes(prose(section(adr28, 'Decision')), ['byte-identical', 'only removes lines', 'never approves anything', 'Nothing merges without', 'goes to the owner']);
  includes(prose(section(adr28, 'Addendum (2026-10-28)')), ['in `adam-agentskills` only', 'its diff only adds lines', 'unless each changed skill has a new impact entry', 'swaps `agent-working` for `agent-ready`', 'closes the issue as completed when none are left']);
  includes(prose(section(issueWorker, 'Where it runs')), ['**The owner turns that laptop task off**']);
  includes(prose(section(issueWorker, 'Each run')), ['do only the findings no merged PR fixed', 'close the issue as completed with a comment naming the merged PRs', 'raises the bundle\'s `version` in both `plugin.json` files', 'adds a `docs/skill-impact.md` entry (add lines only)', 'a `PURPOSE.md` beside the `SKILL.md`']);
});

test('fleet worker merges reviewed agent-made changes and the hook pin bump (ADR 0041)', () => {
  const rule = prose(section(worker, '2d. Review and merge an agent-made change or a hook pin bump'));
  includes(rule, ['labeled `agent-authored`', 'in a fleet repo other than the three cms-platform-managed repos', '`agents-md-sync[bot]`', 'head branch `hook-pin-bump/update`', 'the changed files are exactly `repos.yml` and `.claude/hooks/skills-bootstrap.sh`', 'It is not a draft.', 'carries `on-hold`', 'at least 1 hour before the run\'s start', 'Items 4, 5 and 7 of the merge rule above hold', 'exactly `Independent review: CLEAN at <the full head sha>`', 'A push after the stamp makes it stale', 'a fresh prompt', 'never this run\'s other context', 'Its job is to break the change, not approve it.', 'no test skipped, disabled or deleted', 'no `${{ }}` expression inside a `run:` block', 'Such a change always goes to the owner.', 'adds no network access', 'post nothing', 'it is never merged under this rule', 'Run the window check of step 2b first', '`expected_head_sha` set to the reviewed head', 'goes to the owner under step 3']);
  includes(prose(section(worker, 'Constraints')), ['Step 2d\'s agent-change rule is part of it.']);
  includes(prose(section(worker, '3. Tell the owner which PRs need him')), ['(or the hook pin bump of step 2d)', 'A step 2d `FINDINGS` review or a refused merge does make it one.']);
  const adr41 = 'docs/decisions/0041-the-fleet-worker-merges-reviewed-agent-made-changes.md';
  includes(prose(section(adr41, 'Decision')), ['independent review', 'Opting out is a draft or `on-hold`.', 'still wait for the owner', 'never approves anything']);
  includes(read('docs/decisions/0010-the-hook-pin-is-re-pinned-from-here.md'), ['Superseded in part by\n[0041]']);
});

test('fleet worker updates PRs a required check never ran on and tells the owner once', () => {
  const update = prose(section(worker, '1. Bring it up to date when a required check never ran'));
  includes(update, ['`required_status_checks`', 'with **no** check run and **no** status at all', '`behind_by` above 0', 'with `expected_head_sha` set to the head you read', 'Skip Dependabot PRs:', 'do not resolve it here']);
  const tell = prose(section(worker, '3. Tell the owner which PRs need him'));
  includes(tell, ['`waiting` on an environment', '`https://github.com/<owner>/<repo>/actions/runs/<run_id>/job/<job_id>`', '`needs-owner` label once', '**Only a PR that just gained the label is news.**', 'starts with `Needs you:`']);
  const blocked = prose(section(worker, 'Tell the owner which issues are blocked'));
  includes(blocked, ['open `agent-blocked` issues', 'both owners', '`on-hold` issues left out', '`needs-owner`', 'that label once', 'loses `needs-owner` too', '**Only an issue that just gained the label is news.**', '`Needs you:` list', '`unblock:`']);
  includes(prose(section(worker, 'The trigger')), ['Push notifications on', 'Read and follow `docs/routines/fleet-worker.md`', 'I authorize the writes that file describes', 'Never approve anything.']);
});

test('fleet worker runs the lesson-check pass every run and always reports it (#427)', () => {
  const intro = prose(tokens(worker).slice(0, tokens(worker).findIndex(t => t.type === 'heading_open' && t.tag === 'h2')));
  includes(intro, ['**every run does all three**', 'No pass\'s cap, budget or empty result ends the run early', 'still does the lesson-check pass afterwards', '"Nothing to do" is decided after the third pass', 'that ends the issue pass, not the run', 'A denied or failed write', 'is not a reason to stop either: skip that one write']);
  const inputs = prose(section(worker, 'Inputs'));
  includes(inputs, ['Call `add_repo` with owner `Adam-S-Daniel`, repo `adam-agentskills-private`', '`git clone --depth 1 --branch persistent/lessons --single-branch', '`git -C <scratch>/lessons ls-tree -r --name-only HEAD -- lessons/proposals/`', '`__agent-check.json`', 'never treat it as "no proposals"', '**read** is the number of lessons found', '**handled** is the number this run took']);
  includes(prose(section(worker, 'Summary')), ['**Always, as the last line, even when all three numbers are zero:**', '`Lessons: read <n>, new <k>, handled <m>`', '`; persistent/lessons NOT REACHED`', 'the lesson-check pass did not run']);
  includes(prose(section(issueWorker, 'Each run')), ['end this pass (in the fleet worker, the lesson-check pass still runs)']);
});

test('memory curation reads memory-note and guidance-edit lessons every run (skills-evals#657)', () => {
  const curation = 'docs/routines/memory-curation.md';
  includes(prose(section(curation, 'Constraints')), ['never an `examples` entry, an excerpt']);
  const read = prose(section(curation, "3b. Read the session miner's lesson proposals (once per run)"));
  includes(read, ['`memory-note` and `guidance-edit`', 'Call `add_repo` with owner `Adam-S-Daniel`, repo `adam-agentskills-private`', '`git clone --depth 1 --branch persistent/lessons --single-branch', '`git -C <scratch>/lessons ls-tree -r --name-only HEAD -- lessons/proposals/`', 'Keep only the newest', '`__memory-note.json`', '`__guidance-edit.json`', 'Never treat it as "no proposals"', 'search the target repo\'s issues, open and closed, for that line', 'Its target is `agents-md/base.md`', 'git hook first rule']);
  includes(prose(section(curation, '4. File promotions')), ['`Lesson: <id>`', 'never an example or excerpt from the proposal']);
  includes(prose(section(curation, '5. Summary')), ['**Always, as the last line, even when both numbers are zero:**', '`Lessons: read <n>, promoted <m>`', '`; persistent/lessons NOT REACHED`']);
});

test('eval-protected subjects get correctness findings and fixes only (ADR 0033)', () => {
  const route = prose(section(spec, '3. Route every finding'));
  includes(route, ['On a protected subject, keep a finding only if it is one of these', '`node scripts/eval-protected.js list --explorer <that file>`', '`wrong-command`', '`broken-path`', '`contradiction`', '`safety-gap`', 'files no issue', '`<n> held (eval-protected)`', 'treat every skill and section as protected for this run']);
  includes(prose(section(spec, '4. Render one issue per repo')), ['`Correctness fix: <skill name or section id> — <class>: <one line>`']);
  const merge = prose(section(worker, '2. Review and merge a narrow instruction fix'));
  includes(merge, ['**The eval-protection gate exits 0**', '`node scripts/eval-protected.js pr', 'Exit 2 is a refusal', 'do not merge, leave the PR as it is, and retry next run', 'all seven items', 'the finding really is of the class it names', 'close the PR without merging', 'gets no `needs-owner` label']);
  includes(prose(section(worker, 'The issue pass')), ['Hold every other finding on a listed subject', 'closed as not planned']);
  assert.ok(fs.existsSync(path.join(root, 'scripts/eval-protected.js')), 'the gate script the specs name is missing');
});
