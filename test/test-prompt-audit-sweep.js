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

const mutations = {
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
  marker: [spec, 'Find them\nby that title marker', 'Find them\nby branch name'],
  fixed: [spec, '`git merge-base --is-ancestor <sha> origin/<assigned branch>`', '`git merge-base --is-ancestor <sha> origin/persistent/prompt-audit-sweep`'],
  openfail: [spec, 'fails, stop before auditing:\n   `BLOCKED: could not open the sweep PR (<status>)`', 'fails, continue auditing anyway'],
  reuseopen: [spec, 'stop:\n     `BLOCKED: previous sweep PR still open: <link>`. Never reset, append\n     to or push to that branch;', 'reset it to\n     `origin/main` and force-push;'],
  reusestray: [spec, 'stop:\n     `BLOCKED: assigned branch has commits not on main and no open PR`.\n     Never discard them.', 'reset it to `origin/main`, discarding them.'],
  reuseff: [spec, 'That push is a\n     fast-forward of whatever the branch held; never force it.', 'Force it if the\n     branch held anything.'],
  recheck: [spec, 'If a sweep PR with a **lower** number than', 'If a sweep PR with a higher number than'],
  autofix: [spec, 'is not a run: it performs none of steps 0 to 7', 'is a run: it performs steps 0 to 7'],
  starttitle: [spec, 'as a draft, titled\n   `Prompt-audit sweep: <date>`, all before', 'as a draft, titled\n   `Sweep <date>`, all before'],
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
  includes(prose(section(spec, 'Constraints')), ['never apply one, in whole or in part', 'never merges and never arms for auto-merge', 'at most one per repo', 'created with the `agent-ready` label and no other label', 'never fire another routine', 'on-hold']);
  includes(prose(section(spec, '4. Render one issue per repo')), ['One issue per repo', 'still open there', 'Do not apply the audit\'s proposed diff', 'Do not delete dated incident evidence']);
  includes(prose(section(spec, '5. File and verify')), ['`"labels": ["agent-ready"]` in the create call itself', 'raw REST `GET`']);
  includes(prose(section(spec, '3. Route every finding')), ['routes to `_agent-guidance`', '**warns and never blocks**', 'Confidence Low** stays in the run log']);
  includes(prose(section(spec, '0. Before anything else')), ['prints `true`, `firstParty` and either `claude.ai` or `oauth_token`', '`oauth_token` means an OAuth token supplied through the environment', 'BLOCKED: nested claude is not on the subscription (loggedIn=<loggedIn>, apiProvider=<apiProvider>, authMethod=<authMethod>)', '`ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` must be unset', 'is **not verified**', 'Never imitate the audit']);
});

test('each run logs on its assigned branch in its own PR, found by title marker', () => {
  includes(prose(section(spec, 'Constraints')), ['**Push only to `Adam-S-Daniel/_agent-guidance`.**', 'is read-only', 'no push, no branch, no commit and no PR there', '`git branch --show-current` in the checkout', 'never write it into this spec or the repo', 'auto-fix pushes) are allowed']);
  const start = prose(section(spec, '0. Before anything else'));
  includes(start, ['Find them by that title marker', 'never by branch name', 'must not assume its name', 'on `origin/main` and at the head of every sweep PR', 'Never push to another run\'s branch', 'This run\'s own PR does not exist yet']);
  const intro = all => { const i = all.findIndex(t => t.type === 'heading_open'); return prose(i < 0 ? all : all.slice(0, i)); };
  includes(intro(section(spec, 'Each run')), ['is not a run: it performs none of steps 0 to 7']);
  const items = section(spec, '0. Before anything else');
  const third = items.findIndex(t => t.type === 'list_item_open' && t.info === '3');
  assert.notEqual(third, -1, 'no step 0.3');
  const record = prose(items.slice(third, items.findIndex((t, i) => i > third && t.type === 'list_item_open' && t.level === items[third].level)));
  includes(record, ['If the push or the PR create fails, stop before auditing: `BLOCKED: could not open the sweep PR (<status>)`', 'never a response body', '**Re-check after opening.**', 'If a sweep PR with a **lower** number than this run\'s', 'BLOCKED (duplicate start: <that PR link>)', '`git ls-remote origin refs/heads/<assigned branch>`', 'the same branch to every run']);
  const cases = items.slice(third).filter(t => t.type === 'list_item_open' && t.level > items[third].level);
  assert.ok(cases.length >= 3, 'step 0.3 lists no reused-branch cases');
  includes(record, ['**An open PR has it as its head** (a previous sweep still open): stop: `BLOCKED: previous sweep PR still open: <link>`. Never reset, append to or push to that branch;', '**It exists with commits not on `origin/main`**, and no open PR has it as its head', '`BLOCKED: assigned branch has commits not on main and no open PR`. Never discard them.', 'That push is a fast-forward of whatever the branch held; never force it.', 'Never force-push or delete the assigned branch', 'On a shared branch only one PR can be open from it']);
  const marker = start.match(/whose title starts with exactly `([^`]+)`/);
  assert.ok(marker, 'no sweep PR marker');
  for (const [where, text] of [['step 0.3', record], ['step 7', prose(section(spec, '7. Finish'))]]) {
    const title = text.match(/titled? `(Prompt-audit sweep[^`]*)`/);
    assert.ok(title && title[1].startsWith(marker[1]), `${where} PR title lacks the sweep PR marker`);
  }
  const code = tokens(spec).flatMap(t => [t, ...(t.children || [])]).filter(t => t.type === 'code_inline' || t.type === 'fence').map(t => t.content);
  assert.deepEqual(code.filter(c => /persistent\//.test(c)), [], 'spec names a fixed branch');
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
  includes(text, ['`private` field is `true` at run time', 'never rely on that list, read the field', 'name a private repo and its finding count **only**', 'the run log, the PR body', 'including a cross-repo item', 'push notification', 'no quote, no path']);
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
