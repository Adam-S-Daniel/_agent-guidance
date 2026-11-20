#!/usr/bin/env node
/*
 * language-fit.js — warn-only nightly report of how well each fleet repo's
 * language-dependent configuration fits what the repo actually contains.
 *
 * WHY. No fleet repo requests a language section in `.agents-sync.yml`, so none
 * gets python.md, javascript.md and the rest. GitHub's language statistics are
 * also polluted by files the sync itself delivers (`.githooks/**`,
 * `.claude/hooks/**`), which makes most repos read "Shell 80%". And
 * Dependabot covers only the directories a repo's dependabot.yml names, so a
 * manifest added in a new directory goes unwatched. Each of these is
 * mechanical to find and judgment to fix, so this report finds them and a
 * fleet worker fixes them (one PR per repo) from the nudge issue
 * drift-report.yml opens.
 *
 * CHECKS (per repo).
 *   a. Sections, both directions. A requested section with no matching
 *      language is "remove" (this moved here from guidance-overlap.js's check
 *      3). A language with no requested section is "add" when it is at least
 *      10% of the repo's OWN bytes and at least 2,000 bytes. Own bytes are
 *      GitHub's, minus (by file extension, from the tree) the files under
 *      .githooks/ and .claude/hooks/ and under fixture, generated, vendored or
 *      docs paths (check b's patterns). A file a linguist-vendored, -generated
 *      or -documentation rule already covers is not subtracted twice: GitHub
 *      excludes it already. A
 *      Dockerfile anywhere outside fixture paths adds `docker`. The report
 *      prints the exact `sections:` list the repo should have.
 *   b. Linguist. A language whose remaining bytes come at least 80% from
 *      fixture, generated, vendored or docs paths that no linguist-* attribute
 *      covers yet; a suggested .gitattributes line follows. Whether a path is
 *      vendored, generated or documentation is the fixer's judgment, so the
 *      line is a suggestion. Trace languages (under 1% of bytes) are listed
 *      for information and are not counted as findings.
 *   c. Dependabot manifests. Every manifest directory needs an updates[]
 *      entry for its ecosystem; an entry whose directory holds no manifest of
 *      its ecosystem is flagged (Dependabot errors on it); a repo with
 *      workflows needs a github-actions entry; a package.json needs a lockfile
 *      beside it; a repo with manifests needs a dependabot.yml. A manifest is
 *      exempt under node_modules, fixture/fixtures/testdata/seed/seeds
 *      segments, .githooks/dated-phrasing/, or a path .gitattributes marks
 *      linguist-vendored or -generated. Schedule and
 *      silence are scripts/dependabot-config-health.js's, not this script's.
 *
 * PRIVACY. The report is published on a branch of a PUBLIC repo and the nudge
 * issue is public. For a repo with the `.private` marker the output holds only
 * a category and a count: never a path, a file name or a language byte count.
 * File contents are never printed. A tree that is missing or truncated is
 * "not checked", never clean. So is an input whose read FAILED:
 * drift-report.sh writes `<stem>.<name>.unread` (no data file) for any read
 * that failed other than a confirmed 404, and no finding is drawn from it.
 *
 * INPUT. The directory drift-report.sh writes when DRIFT_GUIDANCE_DIR is set;
 * per repo `<owner>__<repo>.` plus: sections, languages.json, tree.json,
 * dependabot.yml, gitattributes, private, and `<name>.unread` markers.
 *
 * USAGE. node scripts/language-fit.js <dir> [--findings <file>]
 * Markdown to stdout; --findings writes one plain-text line per finding (empty
 * when there are none). Exit 0 whatever it finds; 2 on bad usage or an
 * unreadable directory.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const YAML = require('yaml');

// Section file -> the GitHub (linguist) language names that justify it.
const SECTION_LANGUAGES = {
  docker: ['Dockerfile'],
  dotnet: ['C#', 'F#', 'Visual Basic .NET'],
  go: ['Go'],
  javascript: ['JavaScript'],
  python: ['Python'],
  rust: ['Rust'],
  typescript: ['TypeScript'],
};

const ADD_MIN_SHARE = 0.1;
const ADD_MIN_BYTES = 2000;
const LINGUIST_SHARE = 0.8;
const TRACE_SHARE = 0.01;

// Directories whose files the sync delivers; their bytes are not the repo's.
const SYNCED_DIRS = ['.githooks/', '.claude/hooks/'];

const EXT_LANGUAGE = {
  '.sh': 'Shell', '.bash': 'Shell',
  '.js': 'JavaScript', '.mjs': 'JavaScript', '.cjs': 'JavaScript',
  '.ts': 'TypeScript', '.mts': 'TypeScript', '.cts': 'TypeScript',
  '.py': 'Python', '.rb': 'Ruby', '.go': 'Go', '.rs': 'Rust', '.cs': 'C#',
  '.ps1': 'PowerShell', '.psm1': 'PowerShell',
  '.html': 'HTML', '.css': 'CSS', '.scss': 'SCSS', '.php': 'PHP',
  '.java': 'Java', '.c': 'C', '.h': 'C', '.cpp': 'C++',
};

// Path segments that mark fixture, generated, vendored or docs content, and
// the linguist attribute suggested for each.
const DIR_KINDS = {
  fixture: 'vendored', fixtures: 'vendored', __fixtures__: 'vendored', testdata: 'vendored',
  evals: 'vendored', vendor: 'vendored', third_party: 'vendored',
  dist: 'generated', build: 'generated', _site: 'generated',
  docs: 'documentation',
};
const FIXTURE_SEGMENTS = new Set(['fixture', 'fixtures', '__fixtures__', 'testdata', 'seed', 'seeds']);
const LINGUIST_ATTRS = ['linguist-vendored', 'linguist-generated', 'linguist-documentation'];

// ── .gitattributes ──────────────────────────────────────────────────────────

// A glob to an anchored RegExp: `**/` zero or more directories, `**` anything,
// `*` and `?` stay inside one path segment.
function globToRegExp(glob) {
  let re = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === '*') {
      if (glob[i + 1] === '*') {
        if (glob[i + 2] === '/') { re += '(?:.*/)?'; i += 2; } else { re += '.*'; i += 1; }
      } else re += '[^/]*';
    } else if (c === '?') re += '[^/]';
    else re += c.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp(`^${re}$`);
}

function parseGitattributes(text) {
  const rules = [];
  for (const raw of (text || '').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const [pattern, ...attrs] = line.split(/\s+/);
    let p = pattern;
    const anchored = p.startsWith('/') || p.replace(/\/$/, '').includes('/');
    p = p.replace(/^\//, '');
    if (p.endsWith('/')) p += '**';
    const re = globToRegExp(p);
    const state = {};
    for (const a of attrs) {
      const m = a.match(/^(-|!)?([^=]+)(?:=(.*))?$/);
      if (!m) continue;
      if (m[1] === '-') state[m[2]] = false;
      else if (m[1] === '!') state[m[2]] = undefined;
      else state[m[2]] = m[3] === undefined ? true : m[3] !== 'false';
    }
    rules.push({ re, anchored, state });
  }
  return rules;
}

// The last matching rule that mentions the attribute wins.
function attrOf(rules, filePath, attr) {
  let value;
  for (const r of rules) {
    if (!(attr in r.state)) continue;
    const target = r.anchored ? filePath : filePath.split('/').pop();
    if (r.re.test(target)) value = r.state[attr];
  }
  return value;
}

function linguistCovered(rules, filePath) {
  return LINGUIST_ATTRS.some((a) => attrOf(rules, filePath, a) === true);
}

// ── input ───────────────────────────────────────────────────────────────────

function loadRepos(dir) {
  const repos = new Map();
  const re = /^(.+?)__(.+)\.(agents\.md|sections|languages\.json|tree\.json|dependabot\.yml|gitattributes|private)(\.unread)?$/;
  for (const f of fs.readdirSync(dir).sort()) {
    const m = f.match(re);
    if (!m) continue;
    const name = `${m[1]}/${m[2]}`;
    if (!repos.has(name)) repos.set(name, { private: false, unread: new Set() });
    const r = repos.get(name);
    if (m[4]) { r.unread.add(m[3]); continue; }
    const body = fs.readFileSync(path.join(dir, f), 'utf8');
    const kind = m[3];
    if (kind === 'sections') r.sections = body.split('\n').map((s) => s.trim()).filter(Boolean);
    else if (kind === 'languages.json') r.languages = parseJson(body);
    else if (kind === 'tree.json') r.tree = parseJson(body);
    else if (kind === 'dependabot.yml') r.dependabot = body;
    else if (kind === 'gitattributes') r.gitattributes = body;
    else if (kind === 'private') r.private = true;
  }
  return repos;
}

function parseJson(body) {
  try { return JSON.parse(body); } catch { return null; }
}

// The tree's blob list, or null when the tree was not read whole.
function blobs(r) {
  if (!r.tree || r.tree.truncated || !Array.isArray(r.tree.tree)) return null;
  return r.tree.tree.filter((e) => e && e.type === 'blob' && typeof e.path === 'string')
    .map((e) => ({ path: e.path, size: Number(e.size) || 0 }));
}

// Why an input is missing: a read that failed, or one that was never made.
const why = (r, kind) => (r.unread && r.unread.has(kind) ? 'read failed' : 'not read');

function extOf(p) {
  const base = p.split('/').pop();
  const i = base.lastIndexOf('.');
  return i <= 0 ? '' : base.slice(i).toLowerCase();
}

function languageOf(p) {
  const l = EXT_LANGUAGE[extOf(p)];
  if (l) return l;
  if (p.startsWith('.githooks/') && extOf(p) === '') return 'Shell';
  return null;
}

const inSynced = (p) => SYNCED_DIRS.some((d) => p.startsWith(d));
const hasSegment = (p, set) => p.split('/').some((s) => set.has(s));

// ── a. sections ─────────────────────────────────────────────────────────────

// "Own" bytes: GitHub's per-language bytes minus the files that are not the
// repo's own code: the synced hook directories and fixture, generated,
// vendored or docs paths (patternOf, defined with check b). A file a
// linguist-vendored/generated/documentation rule already covers is NOT
// subtracted again: GitHub's numbers exclude it already.
function nonVendoredBytes(languages, files, rules) {
  const out = { ...languages };
  for (const f of files) {
    if (linguistCovered(rules, f.path)) continue;
    if (!inSynced(f.path) && !patternOf(f.path)) continue;
    const l = languageOf(f.path) || EXT_LANGUAGE[extOf(f.path)];
    if (l && out[l] !== undefined) out[l] = Math.max(0, out[l] - f.size);
  }
  return out;
}

function checkSections(r) {
  const res = { findings: [], notChecked: [] };
  if (!r.sections) { res.notChecked.push('sections (AGENTS.md or .agents-sync.yml not read)'); return res; }
  if (!r.languages) res.notChecked.push(`languages (${why(r, 'languages.json')})`);
  let files = blobs(r);
  if (!files) res.notChecked.push(`tree (${why(r, 'tree.json')}; added sections)`);
  if (files && r.unread.has('gitattributes')) {
    res.notChecked.push('.gitattributes (read failed; added sections)');
    files = null;
  }
  const rules = parseGitattributes(r.gitattributes);
  const hasDockerfile = files ? files.some((f) => f.path.split('/').pop() === 'Dockerfile' &&
    !hasSegment(f.path, FIXTURE_SEGMENTS) && !f.path.split('/').includes('node_modules')) : null;

  const remove = [];
  if (r.languages) {
    for (const s of r.sections) {
      const langs = SECTION_LANGUAGES[s];
      if (!langs) continue;
      const has = langs.some((l) => (r.languages[l] || 0) > 0) || (s === 'docker' && hasDockerfile === true);
      if (!has) remove.push(s);
    }
  }
  const add = [];
  if (files && r.languages) {
    const adj = nonVendoredBytes(r.languages, files, rules);
    const total = Object.values(adj).reduce((a, b) => a + b, 0);
    for (const [s, langs] of Object.entries(SECTION_LANGUAGES)) {
      if (s === 'docker' || r.sections.includes(s)) continue;
      const bytes = langs.reduce((a, l) => a + (adj[l] || 0), 0);
      if (bytes >= ADD_MIN_BYTES && total > 0 && bytes / total >= ADD_MIN_SHARE) add.push({ s, bytes, share: bytes / total });
    }
  }
  if (hasDockerfile && !r.sections.includes('docker')) add.push({ s: 'docker', bytes: null, share: null });

  const complete = Boolean(files && r.languages);
  const desired = r.sections.filter((s) => !remove.includes(s));
  for (const a of add) if (!desired.includes(a.s)) desired.push(a.s);
  res.remove = remove;
  res.add = add;
  res.desired = desired;
  res.complete = complete;

  const n = (k, w) => `${k} ${w}${k === 1 ? '' : 's'}`;
  if (remove.length) {
    res.findings.push({
      category: 'sections',
      pub: `remove ${remove.map((s) => `\`${s}\``).join(', ')} (no matching language in the repo)`,
      priv: `remove ${n(remove.length, 'section')}`,
    });
  }
  if (add.length) {
    res.findings.push({
      category: 'sections',
      pub: `add ${add.map((a) => (a.bytes === null ? `\`${a.s}\` (a Dockerfile)` :
        `\`${a.s}\` (${Math.round(a.share * 100)}% of non-vendored bytes, ${a.bytes.toLocaleString('en-US')} B)`)).join(', ')}`,
      priv: `add ${n(add.length, 'section')}`,
    });
  }
  return res;
}

// ── b. linguist ─────────────────────────────────────────────────────────────

function patternOf(p) {
  const segs = p.split('/');
  const dirs = segs.slice(0, -1);
  const i = dirs.findIndex((s) => Object.prototype.hasOwnProperty.call(DIR_KINDS, s));
  if (i >= 0) return { glob: `${dirs.slice(0, i + 1).join('/')}/**`, kind: DIR_KINDS[dirs[i]] };
  if (p.endsWith('.min.js')) return { glob: '*.min.js', kind: 'generated' };
  return null;
}

function checkLinguist(r) {
  const res = { findings: [], info: [], notChecked: [] };
  const files = blobs(r);
  if (!files) { res.notChecked.push(`tree (${why(r, 'tree.json')}; linguist)`); return res; }
  if (r.unread.has('gitattributes')) { res.notChecked.push('.gitattributes (read failed; linguist)'); return res; }
  const rules = parseGitattributes(r.gitattributes);
  const byLang = new Map();
  for (const f of files) {
    if (inSynced(f.path)) continue; // the sync's own files; marked vendored by the sync
    const l = EXT_LANGUAGE[extOf(f.path)];
    if (!l || linguistCovered(rules, f.path)) continue;
    if (!byLang.has(l)) byLang.set(l, { total: 0, pat: 0, globs: new Map() });
    const e = byLang.get(l);
    e.total += f.size;
    const pt = patternOf(f.path);
    if (pt) {
      e.pat += f.size;
      const k = `${pt.glob} linguist-${pt.kind}`;
      e.globs.set(k, (e.globs.get(k) || 0) + f.size);
    }
  }
  for (const [l, e] of [...byLang].sort((a, b) => a[0].localeCompare(b[0]))) {
    if (e.total === 0 || e.pat / e.total < LINGUIST_SHARE) continue;
    const lines = [...e.globs].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k]) => k);
    res.findings.push({
      category: 'linguist',
      pub: `${l}: ${Math.round((e.pat / e.total) * 100)}% of its ${e.total.toLocaleString('en-US')} B is under fixture, generated, vendored or docs paths with no linguist attribute; consider ${lines.map((x) => `\`${x}\``).join(', ')}`,
      priv: `1 language is mostly fixture, generated, vendored or docs files without a linguist attribute`,
    });
  }
  if (r.languages) {
    const total = Object.values(r.languages).reduce((a, b) => a + b, 0);
    const trace = Object.entries(r.languages).filter(([, b]) => total > 0 && b / total < TRACE_SHARE)
      .sort((a, b) => a[0].localeCompare(b[0]));
    if (trace.length) {
      res.info.push({
        pub: `trace languages (under 1%): ${trace.map(([l, b]) => `${l} (${b.toLocaleString('en-US')} B)`).join(', ')}`,
        priv: `${trace.length} trace language${trace.length === 1 ? '' : 's'} (under 1%)`,
      });
    }
  }
  return res;
}

// ── c. dependabot ───────────────────────────────────────────────────────────

const MANIFESTS = [
  ['package.json', 'npm'], ['Gemfile', 'bundler'], ['pyproject.toml', 'pip'],
  ['go.mod', 'gomod'], ['Cargo.toml', 'cargo'], ['packages.config', 'nuget'],
  ['composer.json', 'composer'], ['Dockerfile', 'docker'],
];

function manifestEcosystem(p, siblings) {
  const base = p.split('/').pop();
  for (const [name, eco] of MANIFESTS) {
    if (base === name) {
      if (name === 'pyproject.toml' && siblings.has('uv.lock')) return 'uv';
      return eco;
    }
  }
  if (/^requirements.*\.txt$/.test(base)) return 'pip';
  if (base.endsWith('.csproj')) return 'nuget';
  if (base.endsWith('.tf')) return 'terraform';
  if (p === '.devcontainer/devcontainer.json') return 'devcontainers';
  return null;
}

// Not ours to bump: node_modules, fixtures and seeds, anything .gitattributes
// marks vendored or generated, and the synced dated-phrasing copy (kept as a
// literal so the check is right before the sync's vendored line lands).
function exemptPath(p, rules) {
  const segs = p.split('/');
  return segs.includes('node_modules') || p.startsWith('.githooks/dated-phrasing/') ||
    segs.some((s) => FIXTURE_SEGMENTS.has(s)) ||
    attrOf(rules, p, 'linguist-vendored') === true || attrOf(rules, p, 'linguist-generated') === true;
}

const normDir = (d) => String(d == null ? '' : d).replace(/^\/+|\/+$/g, '');
const dirOf = (p) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');

function entryCovers(entry, dir, eco) {
  const e = entry['package-ecosystem'];
  const ecoOk = e === eco || (eco === 'uv' && e === 'pip') || (eco === 'pip' && e === 'uv');
  if (!ecoOk) return false;
  // Dependabot reads .devcontainer/ from the repo root.
  const dirs = eco === 'devcontainers' ? [dir, ''] : [dir];
  if (typeof entry.directory === 'string' && dirs.includes(normDir(entry.directory))) return true;
  if (Array.isArray(entry.directories)) {
    return entry.directories.some((g) => typeof g === 'string' && dirs.some((d) => globToRegExp(normDir(g)).test(d)));
  }
  return false;
}

function checkDependabot(r) {
  const res = { findings: [], notChecked: [] };
  const files = blobs(r);
  if (!files) { res.notChecked.push(`tree (${why(r, 'tree.json')}; dependabot)`); return res; }
  if (r.unread.has('gitattributes')) { res.notChecked.push('.gitattributes (read failed; dependabot)'); return res; }
  const rules = parseGitattributes(r.gitattributes);
  const names = new Set(files.map((f) => f.path));
  const manifests = [];
  for (const f of files) {
    const dir = dirOf(f.path);
    const siblings = new Set(files.filter((g) => dirOf(g.path) === dir).map((g) => g.path.split('/').pop()));
    const eco = manifestEcosystem(f.path, siblings);
    if (eco) manifests.push({ path: f.path, dir, eco });
  }
  const live = manifests.filter((m) => !exemptPath(m.path, rules));
  const hasWorkflows = files.some((f) => f.path.startsWith('.github/workflows/') && /\.ya?ml$/.test(f.path));

  const n = (k, w) => `${k} ${w === 'manifest directory' && k !== 1 ? 'manifest directories' : `${w}${k === 1 ? '' : 's'}`}`;
  const add = (pub, priv) => res.findings.push({ category: 'dependabot', pub, priv });

  let entries = null;
  if (r.unread.has('dependabot.yml')) {
    res.notChecked.push('dependabot.yml (read failed)');
  } else if (r.dependabot === undefined) {
    if (live.length || hasWorkflows) {
      add('no dependabot.yml, but the repo has manifests or workflows', 'no dependabot.yml, but the repo has manifests or workflows');
    }
  } else {
    let doc = null;
    try { doc = YAML.parse(r.dependabot); } catch { doc = null; }
    if (!doc || !Array.isArray(doc.updates)) {
      res.notChecked.push('dependabot.yml (could not be parsed)');
    } else {
      entries = doc.updates.filter((u) => u && typeof u === 'object');
    }
  }

  if (entries) {
    const lacking = [];
    const seen = new Set();
    for (const m of live) {
      const k = `${m.eco}\0${m.dir}`;
      if (seen.has(k)) continue;
      seen.add(k);
      if (!entries.some((e) => entryCovers(e, m.dir, m.eco))) lacking.push(m);
    }
    if (lacking.length) {
      const list = lacking.map((m) => `\`/${m.dir}\` (${m.eco})`);
      add(`${n(lacking.length, 'manifest directory')} without an updates entry: ${list.join(', ')}`,
        `${n(lacking.length, 'manifest directory')} lack${lacking.length === 1 ? 's' : ''} a Dependabot entry`);
    }
    // An entry whose single directory holds no manifest of its ecosystem.
    const dead = [];
    for (const e of entries) {
      const eco = e['package-ecosystem'];
      if (typeof e.directory !== 'string' || !eco || eco === 'github-actions') continue;
      if (!['npm', 'bundler', 'pip', 'uv', 'gomod', 'cargo', 'nuget', 'composer', 'docker', 'terraform'].includes(eco)) continue;
      const dir = normDir(e.directory);
      const ok = manifests.some((m) => m.dir === dir && (m.eco === eco || (eco === 'pip' && m.eco === 'uv') || (eco === 'uv' && m.eco === 'pip')));
      if (!ok) dead.push({ eco, dir });
    }
    if (dead.length) {
      add(`${n(dead.length, 'updates entry')} whose directory has no manifest of its ecosystem (Dependabot errors on it): ${dead.map((d) => `${d.eco} \`/${d.dir}\``).join(', ')}`,
        `${n(dead.length, 'updates entry')} point at a directory with no manifest`);
    }
    if (hasWorkflows && !entries.some((e) => e['package-ecosystem'] === 'github-actions')) {
      add('workflows exist but there is no github-actions updates entry', 'no github-actions updates entry');
    }
  }

  const lockless = live.filter((m) => m.eco === 'npm' && m.path.endsWith('package.json') &&
    !['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml'].some((l) => names.has(m.dir ? `${m.dir}/${l}` : l)));
  if (lockless.length) {
    add(`${n(lockless.length, 'package.json')} without a lockfile beside it: ${lockless.map((m) => `\`${m.path}\``).join(', ')}`,
      `${n(lockless.length, 'package.json file')} lack a lockfile`);
  }
  return res;
}

// ── analysis and rendering ──────────────────────────────────────────────────

function analyze(repos) {
  const out = [];
  for (const [name, r] of repos) {
    const sec = checkSections(r);
    const lin = checkLinguist(r);
    const dep = checkDependabot(r);
    out.push({
      name, private: r.private, sec, lin, dep,
      findings: [...sec.findings, ...lin.findings, ...dep.findings],
      info: lin.info,
      notChecked: [...new Set([...sec.notChecked, ...lin.notChecked, ...dep.notChecked])],
    });
  }
  return out;
}

const text = (r, f) => (r.private ? f.priv : f.pub);

function render(results) {
  const out = [];
  out.push('## Language fit (warn-only)');
  out.push('');
  out.push('Per repo: sections that do not match its languages, language statistics skewed by fixture, ' +
    'generated or vendored files, and manifests Dependabot does not cover. Nothing here fails the run; ' +
    'the nudge issue "Language fit findings" asks a fleet worker to fix each repo. Private repos show ' +
    'a category and a count only, because this report is public.');
  out.push('');
  const sections = [
    ['sections', 'Sections'],
    ['linguist', 'Linguist overrides'],
    ['dependabot', 'Dependabot manifests'],
  ];
  for (const [cat, title] of sections) {
    const rows = [];
    for (const r of results) {
      for (const f of r.findings.filter((x) => x.category === cat)) rows.push(`- \`${r.name}\`: ${text(r, f)}`);
      if (cat === 'sections' && !r.private && r.sec.complete && r.findings.some((x) => x.category === 'sections')) {
        rows.push(`  - should be \`sections: [${r.sec.desired.join(', ')}]\` in \`.agents-sync.yml\``);
      } else if (cat === 'sections' && !r.private && r.findings.some((x) => x.category === 'sections')) {
        rows.push(`  - partial list (a read was missing): \`sections: [${r.sec.desired.join(', ')}]\``);
      }
      if (cat === 'linguist') for (const i of r.info) rows.push(`- \`${r.name}\` (info): ${text(r, i)}`);
    }
    const count = results.reduce((a, r) => a + r.findings.filter((x) => x.category === cat).length, 0);
    out.push(`### ${title} (${count})`, '');
    out.push(...(rows.length ? rows : ['None.']));
    out.push('');
  }
  const nc = results.filter((r) => r.notChecked.length);
  out.push(`### Not checked (${nc.length})`, '');
  if (!nc.length) out.push('None.');
  for (const r of nc) out.push(`- \`${r.name}\`: ${r.notChecked.join('; ')}`);
  out.push('');
  return out.join('\n');
}

function findingsText(results) {
  const lines = [];
  for (const r of results) for (const f of r.findings) lines.push(`${r.name}: ${f.category}: ${text(r, f)}`);
  return lines.length ? `${lines.join('\n')}\n` : '';
}

function main(argv) {
  const args = argv.slice(2);
  let dir = null;
  let findings = null;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--findings') {
      findings = args[++i];
      if (!findings) { process.stderr.write('--findings needs a file\n'); return 2; }
    } else if (!dir && !args[i].startsWith('--')) dir = args[i];
    else { process.stderr.write(`unknown argument: ${args[i]}\n`); return 2; }
  }
  if (!dir) { process.stderr.write('usage: language-fit.js <dir> [--findings <file>]\n'); return 2; }
  let repos;
  try { repos = loadRepos(dir); } catch (e) { process.stderr.write(`cannot read ${dir}: ${e.code || e.message}\n`); return 2; }
  const results = analyze(repos);
  process.stdout.write(render(results));
  if (findings) {
    try { fs.writeFileSync(findings, findingsText(results)); } catch (e) {
      process.stderr.write(`cannot write ${findings}: ${e.code || e.message}\n`);
      return 2;
    }
  }
  return 0;
}

if (require.main === module) process.exitCode = main(process.argv);

module.exports = {
  main, loadRepos, analyze, render, findingsText, parseGitattributes, attrOf, globToRegExp,
  nonVendoredBytes, checkSections, checkLinguist, checkDependabot, SECTION_LANGUAGES,
};
