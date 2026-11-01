#!/usr/bin/env node
"use strict";
/*
 * eval-protected.js — decides mechanically whether a pull request edits an
 * "eval-protected" skill or fleet-guidance section. A subject is protected
 * when skills-evals holds eval evidence for its CURRENT text: any edit, even a
 * cosmetic one, changes the digest and resets that evidence. The claude.ai
 * routines that make or merge small edits (the prompt-audit sweep and the
 * fleet worker) call this script first, so a cosmetic edit never silently
 * discards a subject's evidence. Only a marked correctness fix may touch a
 * protected subject.
 *
 * Pure decision, no network: the routine fetches the documents and passes file
 * paths, which keeps every branch testable with fabricated fixtures
 * (test/test-eval-protected.js) and keeps the script incapable of merging
 * anything. It reads no prose for meaning except the one lexical marker line
 * below.
 *
 * Protection rule (isProtected):
 *   verdict `retire` or `shorten-or-merge` -> NOT protected (being removed or
 *     due for a cut);
 *   verdict `keep` -> protected;
 *   otherwise `current_text_runs` > 0 -> protected (a missing or non-integer
 *     value counts as 0);
 *   otherwise not protected.
 *
 * Subcommands:
 *   list --explorer <latest.json>
 *     Prints {"skills":[{name,registry,verdict,current_text_runs}],
 *     "sections":[{id,heading,verdict,current_text_runs}]}: protected subjects
 *     only, sorted. Exit 0.
 *   pr --explorer <latest.json> --repo <owner/name> --files <files.json> \
 *      --body <pr.json> [--base-md <path>] [--coverage <path>]
 *     files.json  GET /repos/{o}/{r}/pulls/{n}/files?per_page=100 (array)
 *     pr.json     GET /repos/{o}/{r}/pulls/{n} (only .body is read)
 *     Maps changed files to subjects:
 *       Adam-S-Daniel/adam-agentskills: plugins/<bundle>/skills/<name>/** is
 *         skill <name> (any file in the directory moves its digest), matched
 *         against explorer skills whose registry is adam-agentskills.
 *       Adam-S-Daniel/_agent-guidance: agents-md/sections/<x>.md is the
 *         coverage row whose `file` is that path; agents-md/base.md is mapped
 *         by hunk: every added line, and every removed line (at its new-side
 *         position), is attributed to the enclosing `## ` heading of the HEAD
 *         copy (--base-md, required when base.md changed), found from
 *         markdown-it's token stream, never a regex over lines. A removal also
 *         counts the line before it, so a deletion at the very end of a
 *         section is not credited to the next one. A heading with no coverage
 *         row is reported under `unmapped` and refuses.
 *       Any other repo or path maps to nothing.
 *     A changed file under a subject path whose `patch` is missing (too large
 *     or binary) refuses: the edit cannot be inspected.
 *     Every touched protected subject needs a PR-body line
 *       Correctness fix: <subject> — <class>: <text>
 *     (`-` or `--` accepted for the em dash; <subject> is the skill name or
 *     section id; <class> is wrong-command, broken-path, contradiction or
 *     safety-gap).
 *     Prints {"merge", "touched", "protected", "unmarked", "unmapped",
 *     "reasons"}. Every reason is collected, not just the first.
 *
 * Exit codes: 0 allowed (or list ok), 2 refused, 1 bad input or usage (message
 * to stderr; file contents are never echoed). An unreadable or malformed
 * explorer file is exit 1, so the caller fails closed.
 */
const fs = require("node:fs");
const path = require("node:path");
const MarkdownIt = require("markdown-it");
const YAML = require("yaml");

const parser = new MarkdownIt();

const SKILLS_REPO = "Adam-S-Daniel/adam-agentskills";
const GUIDANCE_REPO = "Adam-S-Daniel/_agent-guidance";
const SKILLS_REGISTRY = "adam-agentskills";
const BASE_MD = "agents-md/base.md";
const DEFAULT_COVERAGE = path.join(__dirname, "..", "agents-md", "eval-coverage.yml");
const SKILL_PATH = /^plugins\/[^/]+\/skills\/([^/]+)\/.+$/;
const CLASSES = ["wrong-command", "broken-path", "contradiction", "safety-gap"];
const MARKER_LINE = new RegExp(
  `^\\s*Correctness fix: (\\S+) (?:—|--|-) (${CLASSES.join("|")}): \\S`,
);

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

// Input-shape failures are the caller's mistake (exit 1), not a refusal.
// Messages name the field, never its value.
function need(cond, msg) {
  if (!cond) throw new Error(`bad input: ${msg}`);
}

function runs(entry) {
  const n = isObject(entry) ? entry.current_text_runs : undefined;
  return Number.isInteger(n) ? n : 0;
}

// isProtected(entry) — see the header for the rule.
function isProtected(entry) {
  const verdict = isObject(entry) ? entry.verdict : undefined;
  if (verdict === "retire" || verdict === "shorten-or-merge") return false;
  if (verdict === "keep") return true;
  return runs(entry) > 0;
}

function byKey(key) {
  return (a, b) => (a[key] < b[key] ? -1 : a[key] > b[key] ? 1 : 0);
}

function validateExplorer(ex) {
  need(isObject(ex), "explorer must be a JSON object");
  need(Array.isArray(ex.skills), "explorer.skills must be an array");
  need(isObject(ex.guidance) && Array.isArray(ex.guidance.sections), "explorer.guidance.sections must be an array");
  for (const s of ex.skills) need(isObject(s) && typeof s.name === "string", "explorer skill without a name");
  for (const s of ex.guidance.sections) need(isObject(s) && typeof s.id === "string", "explorer section without an id");
}

// listProtected(explorer) — the protected subjects, sorted.
function listProtected(ex) {
  validateExplorer(ex);
  return {
    skills: ex.skills
      .filter(isProtected)
      .map((s) => ({ name: s.name, registry: s.registry, verdict: s.verdict, current_text_runs: runs(s) }))
      .sort(byKey("name")),
    sections: ex.guidance.sections
      .filter(isProtected)
      .map((s) => ({ id: s.id, heading: s.heading, verdict: s.verdict, current_text_runs: runs(s) }))
      .sort(byKey("id")),
  };
}

// h2Headings(md) — [{line, text}] for every `## ` heading, line 1-based, from
// markdown-it's token stream (a `## x` inside a fence is not a heading).
function h2Headings(md) {
  const tokens = parser.parse(String(md || ""), {});
  const out = [];
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.type !== "heading_open" || t.tag !== "h2" || !t.map) continue;
    const inline = tokens[i + 1];
    const kids = inline && inline.children ? inline.children : [];
    const text = kids
      .filter((c) => c.type === "text" || c.type === "code_inline")
      .map((c) => c.content)
      .join("")
      .trim();
    out.push({ line: t.map[0] + 1, text });
  }
  return out;
}

// patchLines(patch) — the new-side line numbers a patch touches: each added
// line's own number, and for each removed line the new-side number at that
// point plus the one before it (see header).
function patchLines(patch) {
  const lines = [];
  let newLine = 0;
  let inHunk = false;
  for (const l of String(patch).split("\n")) {
    const h = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(l);
    if (h) {
      newLine = Number(h[1]);
      inHunk = true;
      continue;
    }
    if (!inHunk || l === "") continue;
    const c = l[0];
    if (c === "+") {
      lines.push(newLine);
      newLine++;
    } else if (c === "-") {
      lines.push(newLine);
      if (newLine > 1) lines.push(newLine - 1);
    } else if (c === " ") {
      newLine++;
    }
  }
  return lines;
}

// sectionsForPatch(patch, baseMd) — {headings: [text], preamble: bool}: the
// `## ` headings enclosing the touched lines. Lines before the first heading
// map to nothing.
function sectionsForPatch(patch, baseMd) {
  const heads = h2Headings(baseMd);
  const found = new Set();
  for (const ln of patchLines(patch)) {
    let enclosing;
    for (const h of heads) {
      if (h.line <= ln) enclosing = h;
      else break;
    }
    if (enclosing) found.add(enclosing.text);
  }
  return [...found];
}

function parseCoverage(raw) {
  let rows;
  try {
    rows = YAML.parse(raw);
  } catch (e) {
    throw new Error("bad input: coverage file is not valid YAML");
  }
  need(Array.isArray(rows), "coverage must be a YAML list");
  for (const r of rows) {
    need(
      isObject(r) && typeof r.id === "string" && typeof r.heading === "string" && typeof r.file === "string",
      "coverage row without id, heading and file",
    );
  }
  return rows;
}

function markerSubjects(body) {
  const out = new Map();
  for (const line of String(body || "").split(/\r?\n/)) {
    const m = MARKER_LINE.exec(line);
    if (m) out.set(m[1], m[2]);
  }
  return out;
}

// decidePr({explorer, repo, files, body, baseMd, coverage}) — the `pr` result.
function decidePr({ explorer, repo, files, body, baseMd, coverage }) {
  validateExplorer(explorer);
  need(typeof repo === "string" && repo !== "", "repo missing");
  need(Array.isArray(files), "files must be a JSON array");
  const rows = coverage || [];

  const reasons = [];
  const touched = new Set(); // "skill:<name>" / "section:<id>"
  const unmapped = new Set();

  const noPatch = (name) => reasons.push(`file ${name} has no patch (too large or binary): cannot inspect the edit`);

  for (const f of files) {
    need(isObject(f) && typeof f.filename === "string", "files entry without a filename");
    const names = [f.filename];
    if (typeof f.previous_filename === "string") names.push(f.previous_filename);
    const hasPatch = typeof f.patch === "string" && f.patch !== "";

    for (const name of names) {
      if (repo === SKILLS_REPO) {
        const m = SKILL_PATH.exec(name);
        if (!m) continue;
        touched.add(`skill:${m[1]}`);
        if (name === f.filename && !hasPatch) noPatch(name);
      } else if (repo === GUIDANCE_REPO) {
        if (name === BASE_MD) {
          if (name === f.filename && !hasPatch) {
            noPatch(name);
            continue;
          }
          if (!hasPatch) continue;
          if (typeof baseMd !== "string") throw new Error("bad input: --base-md is required when agents-md/base.md changed");
          for (const text of sectionsForPatch(f.patch, baseMd)) {
            const row = rows.find((r) => r.file === BASE_MD && r.heading === text);
            if (row) touched.add(`section:${row.id}`);
            else unmapped.add(text);
          }
        } else if (/^agents-md\/sections\/[^/]+\.md$/.test(name)) {
          const row = rows.find((r) => r.file === name);
          if (row) touched.add(`section:${row.id}`);
          else unmapped.add(name);
          if (name === f.filename && !hasPatch) noPatch(name);
        }
      }
    }
  }

  const skillIndex = new Map(
    explorer.skills.filter((s) => s.registry === SKILLS_REGISTRY).map((s) => [s.name, s]),
  );
  const sectionIndex = new Map(explorer.guidance.sections.map((s) => [s.id, s]));
  const marked = markerSubjects(body);

  const prot = [];
  const unmarked = [];
  for (const key of [...touched].sort()) {
    const [kind, id] = [key.slice(0, key.indexOf(":")), key.slice(key.indexOf(":") + 1)];
    const entry = kind === "skill" ? skillIndex.get(id) : sectionIndex.get(id);
    if (!entry || !isProtected(entry)) continue;
    prot.push(id);
    if (!marked.has(id)) {
      unmarked.push(id);
      reasons.push(
        `${kind} ${id} is eval-protected: edit needs a body line "Correctness fix: ${id} — <${CLASSES.join("|")}>: <text>"`,
      );
    }
  }
  const unmappedList = [...unmapped].sort();
  for (const u of unmappedList) {
    reasons.push(`changed text maps to "${u}", which has no coverage row: protection unknown, refusing`);
  }

  return {
    merge: reasons.length === 0,
    touched: [...touched].sort().map((k) => k.slice(k.indexOf(":") + 1)),
    protected: prot,
    unmarked,
    unmapped: unmappedList,
    reasons,
  };
}

// ── CLI ──────────────────────────────────────────────────────────────────

const FLAGS = {
  "--explorer": "explorer",
  "--repo": "repo",
  "--files": "files",
  "--body": "body",
  "--base-md": "baseMd",
  "--coverage": "coverage",
};

function parseArgs(argv) {
  const cmd = argv[0];
  need(cmd === "list" || cmd === "pr", "usage: eval-protected.js list|pr [flags]");
  const out = { cmd };
  const rest = argv.slice(1);
  for (let i = 0; i < rest.length; i += 2) {
    const key = FLAGS[rest[i]];
    need(key, `unknown argument ${rest[i]}`);
    need(i + 1 < rest.length && !rest[i + 1].startsWith("--"), `${rest[i]} needs a value`);
    need(out[key] === undefined, `${rest[i]} given twice`);
    out[key] = rest[i + 1];
  }
  const required = cmd === "list" ? ["explorer"] : ["explorer", "repo", "files", "body"];
  for (const req of required) need(out[req] !== undefined, `missing --${req.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase())}`);
  if (cmd === "list") {
    for (const k of Object.keys(out)) need(k === "cmd" || k === "explorer", `--${k} does not apply to list`);
  }
  return out;
}

function readText(flag, file) {
  try {
    return fs.readFileSync(file, "utf8");
  } catch (e) {
    throw new Error(`bad input: cannot read ${flag} file`);
  }
}

function readJson(flag, file) {
  try {
    return JSON.parse(readText(flag, file));
  } catch (e) {
    if (e.message.startsWith("bad input")) throw e;
    throw new Error(`bad input: ${flag} file is not valid JSON`);
  }
}

function main(argv) {
  try {
    const args = parseArgs(argv);
    const explorer = readJson("--explorer", args.explorer);
    if (args.cmd === "list") {
      process.stdout.write(JSON.stringify(listProtected(explorer)) + "\n");
      process.exit(0);
    }
    const pr = readJson("--body", args.body);
    need(isObject(pr), "--body must be a JSON object");
    const result = decidePr({
      explorer,
      repo: args.repo,
      files: readJson("--files", args.files),
      body: typeof pr.body === "string" ? pr.body : "",
      baseMd: args.baseMd === undefined ? undefined : readText("--base-md", args.baseMd),
      coverage: parseCoverage(readText("--coverage", args.coverage || DEFAULT_COVERAGE)),
    });
    process.stdout.write(JSON.stringify(result) + "\n");
    process.exit(result.merge ? 0 : 2);
  } catch (e) {
    // Only the message: never a file's contents.
    process.stderr.write(`eval-protected: ${e.message}\n`);
    process.exit(1);
  }
}

if (require.main === module) {
  main(process.argv.slice(2));
}

module.exports = {
  isProtected,
  listProtected,
  h2Headings,
  patchLines,
  sectionsForPatch,
  parseCoverage,
  markerSubjects,
  decidePr,
  parseArgs,
  main,
};
