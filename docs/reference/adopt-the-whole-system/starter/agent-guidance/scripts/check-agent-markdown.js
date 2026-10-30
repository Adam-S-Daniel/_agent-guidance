#!/usr/bin/env node
/*
 * check-agent-markdown.js — two WARN-ONLY checks over the Markdown that agents
 * read as instructions. Both are deterministic and offline; neither can fail
 * CI unless `--strict` is passed (the tests do).
 *
 * Why these two. A prompt-audit sweep (the R2 section of the sprint's
 * s7-doctor-integration report) found the same defect classes in more than one
 * repo, and a defect class that recurs belongs in a check, not in an LLM's
 * attention. Owner decision D5 ("5. Warn") makes both advisory: they print
 * GitHub `::warning` annotations and exit 0. The fleet already has the rule
 * the first one enforces (American English spelling in everything an agent
 * adds or changes); until now only review applied it.
 *
 * Scope (agent-facing Markdown): `agents-md/**` (*.md), `AGENTS.md`,
 * `CLAUDE.md`, every `SKILL.md`, and `.claude/hooks/fleet-guidance.md`.
 * The walk skips `.git`, `node_modules`, `.claude/worktrees` and any
 * subdirectory that is itself a git checkout (CI checks skills-evals out into
 * the workspace; its text is not this repo's).
 *
 * 1. BRITISH SPELLINGS, from the FIXED lists below and nothing else — there is
 *    no generic `-ise` rule, because `-ise` is also a legitimate American
 *    ending (exercise, promise, otherwise). A word is flagged only when it is
 *    on the list, or is `<listed stem>` plus one listed suffix. Words that are
 *    ordinary American English too (analyses, cancelled, judgement) are
 *    deliberately absent. Case-insensitive, whole words.
 *      Exempt: fenced and indented code, inline code, block quotes, HTML,
 *      and URLs. Everything else — headings, list items, table cells, link
 *      text — is checked.
 *      Allowlist: one lowercase word per line in
 *      `docs/agent-markdown-allowlist.txt` (override with `--allowlist`);
 *      blank lines and `#` comments are ignored.
 *
 * 2. DANGLING "see X above/below". An inline reference of the form
 *    `see "X" above`, ``see `## X` below``, `see **X** above` or
 *    `see [X](#x) below` (optionally `the`, `also`, `section`, `heading`)
 *    whose X is not the text of ANY heading in the same file. X must be
 *    delimited (quotes, backticks, bold/italic or a link) — an undelimited
 *    "see the rules above" names no heading and is not guessed at. Existence
 *    is checked, not direction. Headings come from a real Markdown parse
 *    (markdown-it, the parser the guidance gates already use), never a line
 *    scan, so a `## ` inside a fenced block is not a heading and a heading
 *    with inline code is compared by its rendered text. Block quotes are
 *    exempt here too: they quote another document.
 *
 * Usage: node scripts/check-agent-markdown.js [--root <dir>] [--strict]
 *                                             [--allowlist <file>]
 * Output: one `::warning file=<path>,line=<n>,title=<rule>::<message>` per
 * finding, then one summary line. Exit 0 always, except: `--strict` and any
 * finding exits 1; an unreadable explicit `--allowlist`, or (with `--strict`)
 * a root that holds no agent-facing Markdown at all, exits 2 — a check that
 * cannot find its inputs must not certify.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const MarkdownIt = require("markdown-it");

// html: true so raw HTML parses as html_block/html_inline tokens (exempt),
// not as prose.
const md = new MarkdownIt({ html: true });

const DEFAULT_ALLOWLIST = "docs/agent-markdown-allowlist.txt";

// ── The fixed word lists ───────────────────────────────────────────────────

// `<stem>` + each suffix, all lowercase: behaviour, colour, honourable, ...
const OUR_STEMS = [
  "behavi", "col", "hon", "fav", "neighb", "lab", "flav", "harb", "rum",
  "hum", "endeav", "vap", "arm", "sav",
];
const OUR_SUFFIXES = [
  "our", "ours", "oured", "ouring", "ourable", "ourite", "ourites",
  "oural", "ourally", "ourful", "ourless", "ourhood", "ourhoods",
];

// `-ise` verbs, from an explicit stem list (the part before `ise`). Each
// yields -ise/-ised/-ises/-ising/-isation/-isations.
const ISE_STEMS = [
  "organ", "recogn", "initial", "normal", "optim", "summar", "standard",
  "synchron", "minim", "maxim", "custom", "serial", "util", "priorit",
  "author", "final", "real", "emphas", "apolog", "categor", "general",
  "visual", "special", "sanit", "material", "capital", "random", "central",
  "familiar", "memor", "mobil", "modern", "penal", "legal", "local",
  "digit", "stabil", "formal",
];
const ISE_SUFFIXES = ["ise", "ised", "ises", "ising", "isation", "isations"];

// Words that do not follow the patterns above, spelled out. `analyse` is here
// by hand, with no `analyses`, because that is also the American plural of
// "analysis".
const EXPLICIT = [
  "analyse", "analysed", "analysing", "analyser",
  "organisational", "recognisable", "recognisably",
  "catalogue", "catalogues", "catalogued", "cataloguing",
  "centre", "centres", "centred", "centring", "centrepiece",
  "licence", "licences",
  "grey", "greys", "greyed",
  "defence", "defences", "offence", "offences", "pretence",
  "practise", "practised", "practises", "practising",
  "programme", "programmes",
  "metre", "metres", "litre", "litres", "theatre", "fibre", "calibre",
  "artefact", "artefacts",
  "fulfil", "fulfils", "enrol", "enrols", "instalment", "instalments",
  "skilful", "sceptic", "sceptical", "sceptics",
  "learnt", "whilst", "amongst",
  "aluminium", "tyre", "tyres", "kerb", "cheque", "cheques",
  "mould", "moulds", "plough", "storey", "storeys", "ageing",
];

function buildBritishList() {
  const words = new Set(EXPLICIT);
  for (const stem of OUR_STEMS) for (const suf of OUR_SUFFIXES) words.add(stem + suf);
  for (const stem of ISE_STEMS) for (const suf of ISE_SUFFIXES) words.add(stem + suf);
  return words;
}

const BRITISH = buildBritishList();

// ── File discovery ─────────────────────────────────────────────────────────

const SKIP_DIRS = new Set([".git", "node_modules"]);

function isNestedCheckout(dir, root) {
  return dir !== root && fs.existsSync(path.join(dir, ".git"));
}

function walk(root) {
  const found = [];
  (function visit(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))) {
      const full = path.join(dir, entry.name);
      const rel = path.relative(root, full).split(path.sep).join("/");
      if (entry.isDirectory()) {
        if (SKIP_DIRS.has(entry.name) || rel === ".claude/worktrees" || isNestedCheckout(full, root)) continue;
        visit(full);
      } else if (entry.isFile()) {
        found.push(rel);
      }
    }
  })(root);
  return found;
}

function isAgentFacing(rel) {
  if (rel === "AGENTS.md" || rel === "CLAUDE.md" || rel === ".claude/hooks/fleet-guidance.md") return true;
  if (rel.startsWith("agents-md/") && rel.endsWith(".md")) return true;
  return rel === "SKILL.md" || rel.endsWith("/SKILL.md");
}

function agentFiles(root) {
  return walk(root).filter(isAgentFacing);
}

// ── Allowlist ──────────────────────────────────────────────────────────────

function readAllowlist(file, required) {
  if (!fs.existsSync(file)) {
    if (required) throw new Error(`allowlist ${file} does not exist`);
    return new Set();
  }
  const words = new Set();
  for (const raw of fs.readFileSync(file, "utf8").split("\n")) {
    const line = raw.replace(/#.*$/, "").trim().toLowerCase();
    if (line) words.add(line);
  }
  return words;
}

// ── Markdown walk ──────────────────────────────────────────────────────────

const URL_RE = /(?:https?:\/\/|www\.)[^\s<>)\]]*/gi;
const OPEN = "\u0001";
const CLOSE = "\u0002";

// Flatten one inline token's children into plain text and into a "marked"
// string for reference detection. Returns segments per source line:
// `lines[k]` is the text of the k-th physical line of the inline run (a soft
// or hard break starts the next one); `marked` is the whole run on one line
// with code spans wrapped in backticks and em/strong/link wrapped in
// OPEN..CLOSE, and `markedLineAt(i)` maps an offset back to a line index.
function flattenInline(token) {
  const lines = [""];
  let marked = "";
  const lineStarts = [0]; // offset in `marked` where each physical line begins
  const codeRanges = []; // [start, end) of each code span's text in `marked`
  const push = (text, markedText) => {
    lines[lines.length - 1] += text;
    marked += markedText === undefined ? text : markedText;
  };
  const brk = () => {
    lines.push("");
    marked += " ";
    lineStarts.push(marked.length);
  };
  const walk = (children) => {
    for (const c of children || []) {
      switch (c.type) {
        case "text":
        case "text_special":
          push(c.content);
          break;
        case "softbreak":
        case "hardbreak":
          brk();
          break;
        case "code_inline":
          // Exempt from spelling; kept (backticked) for reference detection.
          marked += "`";
          codeRanges.push([marked.length, marked.length + c.content.length]);
          marked += c.content + "`";
          break;
        case "em_open":
        case "strong_open":
        case "link_open":
          marked += OPEN;
          break;
        case "em_close":
        case "strong_close":
        case "link_close":
          marked += CLOSE;
          break;
        case "image":
          walk(c.children);
          break;
        default:
          // html_inline, strikethrough markers, etc.: no prose of their own.
          break;
      }
    }
  };
  walk(token.children);
  const lineAt = (offset) => {
    let k = 0;
    for (let i = 0; i < lineStarts.length; i++) if (lineStarts[i] <= offset) k = i;
    return k;
  };
  const inCode = (offset) => codeRanges.some(([a, b]) => offset >= a && offset < b);
  return { lines, marked, lineAt, inCode };
}

// The rendered text of a heading (code spans included, markers dropped),
// normalized for comparison.
function normalizeName(s) {
  return s
    .replace(/[\u0001\u0002`*_"“”'‘’]/g, "")
    .replace(/^#+\s*/, "")
    .replace(/\s+/g, " ")
    .replace(/[\s:.]+$/, "")
    .trim()
    .toLowerCase();
}

function headingText(inlineToken) {
  let out = "";
  for (const c of inlineToken.children || []) {
    if (c.type === "text" || c.type === "text_special" || c.type === "code_inline") out += c.content;
    else if (c.type === "softbreak" || c.type === "hardbreak") out += " ";
  }
  return normalizeName(out);
}

const SEE_RE = new RegExp(
  "\\bsee\\s+(?:also\\s+)?(?:the\\s+)?(?:section\\s+)?" +
    "(?:[\"“]([^\"”]+)[\"”]|`([^`]+)`|\\u0001([^\\u0002]+)\\u0002)" +
    "(?:\\s+(?:section|heading|part))?\\s+(above|below)\\b",
  "gi"
);

function scanFile(rel, src, britishAllowed) {
  const findings = [];
  const tokens = md.parse(src, {});
  const headings = new Set();
  const refs = []; // {name, direction, line}
  let quoteDepth = 0;
  let blockMap = null; // nearest enclosing block's [start, end): table cells carry none

  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.map) blockMap = t.map;
    if (t.type === "blockquote_open") quoteDepth++;
    else if (t.type === "blockquote_close") quoteDepth--;
    else if (t.type === "heading_open") {
      const inline = tokens[i + 1];
      if (inline && inline.type === "inline") headings.add(headingText(inline));
    }
    if (t.type !== "inline" || quoteDepth > 0) continue;
    const map = t.map || blockMap;
    if (!map) continue;

    const flat = flattenInline(t);
    const baseLine = map[0] + 1; // 1-indexed

    flat.lines.forEach((text, k) => {
      const prose = text.replace(URL_RE, " ");
      for (const m of prose.matchAll(/\p{L}+/gu)) {
        const word = m[0].toLowerCase();
        if (BRITISH.has(word) && !britishAllowed.has(word)) {
          findings.push({
            file: rel,
            line: baseLine + k,
            rule: "british-spelling",
            message: `British spelling "${m[0]}" — the fleet writes American English. Reword, or add "${word}" to ${DEFAULT_ALLOWLIST} if it is quoted or a proper name.`,
          });
        }
      }
    });

    for (const m of flat.marked.matchAll(SEE_RE)) {
      if (flat.inCode(m.index)) continue; // "see ..." quoted inside a code span
      const name = normalizeName(m[1] || m[2] || m[3]);
      refs.push({ name, shown: (m[1] || m[2] || m[3]).trim(), direction: m[4].toLowerCase(), line: baseLine + flat.lineAt(m.index) });
    }
  }

  for (const r of refs) {
    if (!headings.has(r.name)) {
      findings.push({
        file: rel,
        line: r.line,
        rule: "dangling-reference",
        message: `"see ${r.shown} ${r.direction}" names no heading in this file — fix the name or point at where it lives now.`,
      });
    }
  }
  return findings;
}

// ── Entry points ───────────────────────────────────────────────────────────

function escapeData(s) {
  return s.replace(/%/g, "%25").replace(/\r/g, "%0D").replace(/\n/g, "%0A");
}
function escapeProp(s) {
  return escapeData(s).replace(/:/g, "%3A").replace(/,/g, "%2C");
}

function annotation(f) {
  return `::warning file=${escapeProp(f.file)},line=${f.line},title=${escapeProp(f.rule)}::${escapeData(f.message)}`;
}

function run(root, opts = {}) {
  const strict = Boolean(opts.strict);
  let allowlist;
  try {
    const explicit = opts.allowlist !== undefined;
    allowlist = readAllowlist(explicit ? path.resolve(opts.allowlist) : path.join(root, DEFAULT_ALLOWLIST), explicit);
  } catch (err) {
    return { code: 2, findings: [], lines: [`::error::${escapeData(err.message)}`] };
  }

  const files = agentFiles(root);
  const findings = [];
  for (const rel of files) {
    findings.push(...scanFile(rel, fs.readFileSync(path.join(root, rel), "utf8"), allowlist));
  }
  findings.sort((a, b) => (a.file === b.file ? a.line - b.line : a.file < b.file ? -1 : 1));

  const lines = findings.map(annotation);
  lines.push(`check-agent-markdown: ${files.length} file(s), ${findings.length} warning(s)${strict ? " (strict)" : " (warn-only)"}`);

  let code = 0;
  if (strict && files.length === 0) code = 2;
  else if (strict && findings.length > 0) code = 1;
  return { code, findings, lines };
}

function main(argv) {
  let root = process.cwd();
  const opts = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--root") root = path.resolve(argv[++i]);
    else if (argv[i] === "--strict") opts.strict = true;
    else if (argv[i] === "--allowlist") opts.allowlist = argv[++i];
    else {
      console.error(`unknown argument: ${argv[i]}`);
      return 2;
    }
  }
  const r = run(root, opts);
  for (const l of r.lines) console.log(l);
  return r.code;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));

module.exports = { run, main, BRITISH, scanFile, agentFiles };
