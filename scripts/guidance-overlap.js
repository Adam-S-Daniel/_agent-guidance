#!/usr/bin/env node
/*
 * guidance-overlap.js — warn-only report of repo-specific guidance that looks
 * like it belongs in the shared guidance, and of section opt-ins that do not
 * fit a repo's languages.
 *
 * WHY. The daily "Guidance centralization audit" routine was the only thing
 * that looked under each consumer's `## Repo-specific additions` for text that
 * is fleet-general or already in base.md, and it was disabled on 2026-11-03
 * (_agent-guidance#424). The mechanical half of that job, finding text that
 * repeats, is a check, so it runs here, nightly, inside drift-report.yml. The
 * judgment half (should a repeated paragraph move into base.md, a skill or an
 * agent-check, or simply be deleted) is the weekly Fleet health audit's, which
 * reads this section of the drift report (docs/routines/fleet-health-audit.md).
 *
 * WHAT IT FINDS.
 *   1. A block (paragraph, top-level list item, table, fenced code) under one
 *      repo's `## Repo-specific additions` whose wording substantially repeats
 *      a block in another repo's additions.
 *   2. A block that substantially repeats agents-md/base.md or a file under
 *      agents-md/sections/, i.e. a local copy of a rule the fleet already ships.
 *   3. A repo whose `.agents-sync.yml` requests a section for a language GitHub
 *      reports none of. The other direction (a language with no section) is
 *      NOT reported: the sections are opt-in and deliberately unused, and a
 *      byte count cannot tell first-party code from generated output (the
 *      spec's GHA-bench trap). That stays a judgment call.
 *
 * Blocks come from a real markdown parse (markdown-it token maps), never a line
 * scan, per the fleet rule on shape checks. Similarity is word 5-gram
 * containment, which is order-aware and robust to small edits.
 *
 * PRIVACY. The drift report is published on a branch of this PUBLIC repo, and
 * the input includes private repos. The output therefore never quotes repo
 * text: it names the repo and a line range (a link only people with access can
 * open) and, for shared guidance, the public base.md or section heading.
 *
 * INPUT. A directory written by drift-report.sh when DRIFT_GUIDANCE_DIR is set:
 *   <owner>__<repo>.agents.md       the repo's AGENTS.md as read
 *   <owner>__<repo>.sections        requested sections, one per line
 *   <owner>__<repo>.languages.json  GitHub's languages map, when it was read
 *
 * USAGE. node scripts/guidance-overlap.js <dir> [--base <base.md>]
 *            [--sections-dir <dir>]
 * Writes a markdown section to stdout. Exit 0 whatever it finds (warn-only);
 * exit 2 only on bad usage or an unreadable input directory.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const MarkdownIt = require('markdown-it');

const MARKER = '## Repo-specific additions';
const SHINGLE = 5;
// A block shorter than this many words is too generic to compare ("Run the
// tests first.") and produces noise, not findings.
const MIN_WORDS = 15;
// Share of a block's 5-grams that must appear in the other block. Measured on
// 18 fleet AGENTS.md files on 2026-11-09: paragraphs copied between repos
// scored 0.91 to 0.93; the closest restatement of base.md scored 0.48 and the
// next 0.21, and unrelated blocks on the same topic stay under 0.1. Shared
// guidance gets the lower bar because a local restatement is usually edited.
// A paraphrase with little shared wording is not found; that stays judgment.
const REPO_THRESHOLD = 0.5;
const SHARED_THRESHOLD = 0.4;

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

const md = new MarkdownIt();

function words(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().split(' ').filter(Boolean);
}

function shingles(ws) {
  const set = new Set();
  for (let i = 0; i + SHINGLE <= ws.length; i++) set.add(ws.slice(i, i + SHINGLE).join(' '));
  return set;
}

function containment(a, b) {
  if (a.size === 0) return 0;
  let hit = 0;
  for (const s of a) if (b.has(s)) hit++;
  return hit / a.size;
}

// Split markdown into comparable blocks with 1-based line ranges relative to
// the whole file. `offset` is the 0-based line the source starts at.
function blocks(source, offset) {
  const lines = source.split('\n');
  const tokens = md.parse(source, {});
  const out = [];
  let listDepth = 0;
  let heading = '';
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.type === 'bullet_list_open' || t.type === 'ordered_list_open') listDepth++;
    if (t.type === 'bullet_list_close' || t.type === 'ordered_list_close') listDepth--;
    if (t.type === 'heading_open') {
      heading = tokens[i + 1] && tokens[i + 1].type === 'inline' ? tokens[i + 1].content : '';
      continue;
    }
    let take = false;
    if (t.type === 'list_item_open' && listDepth === 1) take = true;
    if (listDepth === 0 && t.level === 0 &&
        ['paragraph_open', 'fence', 'code_block', 'table_open', 'blockquote_open'].includes(t.type)) take = true;
    if (!take || !t.map) continue;
    const text = lines.slice(t.map[0], t.map[1]).join('\n');
    const ws = words(text);
    if (ws.length < MIN_WORDS) continue;
    out.push({ start: offset + t.map[0] + 1, end: offset + t.map[1], heading, sh: shingles(ws) });
  }
  return out;
}

// The repo-specific part of a consumer AGENTS.md: everything after the LAST
// whole-line marker (the managed block's header quotes the marker, so the
// first occurrence of the substring is the wrong anchor).
function additions(source) {
  const lines = source.split('\n');
  let idx = -1;
  for (let i = 0; i < lines.length; i++) if (lines[i].trimEnd() === MARKER) idx = i;
  if (idx < 0) return null;
  return { text: lines.slice(idx + 1).join('\n'), offset: idx + 1 };
}

function readIf(p) {
  try { return fs.readFileSync(p, 'utf8'); } catch { return null; }
}

function loadRepos(dir) {
  const repos = new Map();
  for (const f of fs.readdirSync(dir).sort()) {
    const m = f.match(/^(.+?)__(.+)\.(agents\.md|sections|languages\.json)$/);
    if (!m) continue;
    const name = `${m[1]}/${m[2]}`;
    if (!repos.has(name)) repos.set(name, {});
    const r = repos.get(name);
    const body = fs.readFileSync(path.join(dir, f), 'utf8');
    if (m[3] === 'agents.md') r.agents = body;
    if (m[3] === 'sections') r.sections = body.split('\n').map((s) => s.trim()).filter(Boolean);
    if (m[3] === 'languages.json') {
      try { r.languages = JSON.parse(body); } catch { r.languages = null; }
    }
  }
  return repos;
}

function loadShared(basePath, sectionsDir) {
  const shared = [];
  const base = readIf(basePath);
  if (base !== null) {
    for (const b of blocks(base, 0)) shared.push({ ...b, file: 'agents-md/base.md' });
  }
  if (sectionsDir && fs.existsSync(sectionsDir)) {
    for (const f of fs.readdirSync(sectionsDir).sort()) {
      if (!f.endsWith('.md')) continue;
      const src = fs.readFileSync(path.join(sectionsDir, f), 'utf8');
      for (const b of blocks(src, 0)) shared.push({ ...b, file: `agents-md/sections/${f}` });
    }
  }
  return { shared, baseRead: base !== null };
}

function analyze(repos, shared) {
  const units = [];
  for (const [name, r] of repos) {
    if (!r.agents) continue;
    const a = additions(r.agents);
    if (!a) continue;
    for (const b of blocks(a.text, a.offset)) units.push({ repo: name, ...b });
  }

  // 1. Cross-repo repeats, clustered with union-find.
  const parent = units.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const score = new Map();
  for (let i = 0; i < units.length; i++) {
    for (let j = i + 1; j < units.length; j++) {
      if (units[i].repo === units[j].repo) continue;
      const s = Math.max(containment(units[i].sh, units[j].sh), containment(units[j].sh, units[i].sh));
      if (s >= REPO_THRESHOLD) {
        const a = find(i), b = find(j);
        if (a !== b) parent[a] = b;
        score.set(i, Math.max(score.get(i) || 0, s));
        score.set(j, Math.max(score.get(j) || 0, s));
      }
    }
  }
  const clusters = new Map();
  for (let i = 0; i < units.length; i++) {
    if (!score.has(i)) continue;
    const root = find(i);
    if (!clusters.has(root)) clusters.set(root, []);
    clusters.get(root).push(i);
  }
  const repeats = [...clusters.values()]
    .map((members) => {
      const ms = members.map((i) => units[i]).sort((x, y) => x.repo.localeCompare(y.repo) || x.start - y.start);
      return { members: ms, repos: [...new Set(ms.map((m) => m.repo))], top: Math.max(...members.map((i) => score.get(i))) };
    })
    .filter((c) => c.repos.length >= 2)
    .sort((x, y) => y.repos.length - x.repos.length || x.members[0].repo.localeCompare(y.members[0].repo) || x.members[0].start - y.members[0].start);

  // 2. Local copies of shared guidance.
  const copies = [];
  for (const u of units) {
    let best = null;
    for (const s of shared) {
      const c = containment(u.sh, s.sh);
      if (c >= SHARED_THRESHOLD && (!best || c > best.score)) best = { score: c, file: s.file, heading: s.heading };
    }
    if (best) copies.push({ unit: u, ...best });
  }
  copies.sort((x, y) => x.unit.repo.localeCompare(y.unit.repo) || x.unit.start - y.unit.start);

  // 3. Sections requested for a language the repo has none of.
  const misfits = [];
  for (const [name, r] of repos) {
    if (!r.sections || !r.languages) continue;
    for (const s of r.sections) {
      const langs = SECTION_LANGUAGES[s];
      if (!langs) continue;
      if (!langs.some((l) => (r.languages[l] || 0) > 0)) misfits.push({ repo: name, section: s, langs });
    }
  }

  return { units: units.length, repeats, copies, misfits };
}

function loc(u) {
  const lines = u.start === u.end ? `L${u.start}` : `L${u.start}-L${u.end}`;
  return `[\`${u.repo}\` ${lines.replace('-L', '-')}](https://github.com/${u.repo}/blob/HEAD/AGENTS.md#${lines})`;
}

function render(result, meta) {
  const out = [];
  out.push('## Repo-specific guidance overlap (warn-only)');
  out.push('');
  out.push(`Compared ${result.units} blocks from the \`${MARKER}\` part of ${meta.repos} repos. ` +
    'Nothing here fails the run. Each entry is input for the weekly Fleet health audit, which decides ' +
    'whether to promote it to base.md, a skill or an agent-check, or delete the local copy. ' +
    'Repo text is not quoted because this report is public and some repos are private.');
  if (!meta.baseRead) out.push('', '**base.md could not be read, so local copies of shared guidance were not checked.**');
  out.push('');
  out.push(`### Repeated across repos (${result.repeats.length})`);
  out.push('');
  if (result.repeats.length === 0) out.push('None.');
  for (const c of result.repeats) {
    out.push(`- ${c.repos.length} repos, up to ${Math.round(c.top * 100)}% shared wording: ${c.members.map(loc).join(', ')}`);
  }
  out.push('');
  out.push(`### Local copies of shared guidance (${result.copies.length})`);
  out.push('');
  if (result.copies.length === 0) out.push('None.');
  for (const c of result.copies) {
    const where = c.heading ? `\`${c.file}\` "${c.heading}"` : `\`${c.file}\``;
    out.push(`- ${loc(c.unit)} repeats ${Math.round(c.score * 100)}% of ${where}`);
  }
  out.push('');
  out.push(`### Sections requested for a language the repo does not use (${result.misfits.length})`);
  out.push('');
  if (result.misfits.length === 0) out.push('None.');
  for (const m of result.misfits) {
    out.push(`- \`${m.repo}\` requests \`${m.section}\`, but GitHub reports no ${m.langs.join(' or ')} code`);
  }
  if (meta.noLanguages.length) {
    out.push('', `Languages not read for: ${meta.noLanguages.map((n) => `\`${n}\``).join(', ')}.`);
  }
  out.push('');
  return out.join('\n');
}

function main(argv) {
  const args = argv.slice(2);
  let dir = null;
  let base = path.join(__dirname, '..', 'agents-md', 'base.md');
  let sectionsDir = path.join(__dirname, '..', 'agents-md', 'sections');
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--base') base = args[++i];
    else if (args[i] === '--sections-dir') sectionsDir = args[++i];
    else if (!dir && !args[i].startsWith('--')) dir = args[i];
    else { process.stderr.write(`unknown argument: ${args[i]}\n`); return 2; }
  }
  if (!dir || !base) { process.stderr.write('usage: guidance-overlap.js <dir> [--base <file>] [--sections-dir <dir>]\n'); return 2; }
  let repos;
  try { repos = loadRepos(dir); } catch (e) { process.stderr.write(`cannot read ${dir}: ${e.code || e.message}\n`); return 2; }
  const { shared, baseRead } = loadShared(base, sectionsDir);
  const result = analyze(repos, shared);
  const noLanguages = [...repos].filter(([, r]) => r.sections && r.sections.length && !r.languages).map(([n]) => n);
  process.stdout.write(render(result, { repos: [...repos.values()].filter((r) => r.agents).length, baseRead, noLanguages }));
  return 0;
}

if (require.main === module) process.exitCode = main(process.argv);

module.exports = { additions, blocks, analyze, render, main, loadRepos, loadShared };
