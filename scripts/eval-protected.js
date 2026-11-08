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
 *   pr (--explorer <latest.json> | --no-evidence) --repo <owner/name> \
 *      --files <files.json> --body <pr.json> [--base-md <path>] \
 *      [--coverage <path>] [--head-ref <ref>] [--author-type <type>]
 *     files.json  GET /repos/{o}/{r}/pulls/{n}/files?per_page=100 (array)
 *     pr.json     GET /repos/{o}/{r}/pulls/{n} (.body, .head.ref, .user.type)
 *     --no-evidence treats the explorer as empty (nothing protected), for a
 *     repo with no eval results yet; it excludes --explorer. The explorer is
 *     read only when a changed file maps to a subject path: a PR that touches
 *     none exits 0 without reading it, so --explorer may then be omitted or
 *     name a missing file. When a subject path is touched, a missing or
 *     unreadable explorer is exit 1 (fail closed).
 *     Maps changed files to subjects (REPOS table):
 *       Adam-S-Daniel/adam-agentskills and Adam-S-Daniel/adam-agentskills-private:
 *         plugins/<bundle>/skills/<name>/** is skill <name> (any file in the
 *         directory moves its digest), matched against explorer skills whose
 *         registry is the repo's own (adam-agentskills or
 *         adam-agentskills-private).
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
 *     "reasons"} (plus "exempt" when exempt). Every reason is collected, not
 *     just the first.
 *     Eval-lane exemption: skills-evals' eval-backed improve (eval-improve/,
 *     eval-guidance-improve/), retirement (retire-skill/, retire-guidance/)
 *     and new-skill (draft-skill/) PRs are opened by a GitHub App, and evals,
 *     not this audit, judge their changes. A PR whose head ref (--head-ref, or
 *     pr.json .head.ref) starts with one of those prefixes AND whose author
 *     type (--author-type, or pr.json .user.type) is exactly "Bot" is exempt:
 *     merge is true, "exempt" names the prefix, and the other fields are still
 *     computed for the record. Anyone can name a branch, so the same prefix
 *     from a "User" is not exempt.
 *   ci --repo <owner/name> --pr-json <pr.json> --files <files.json> \
 *      (--explorer <file> | --no-evidence) [--base-md <path>] [--coverage <path>]
 *     The `pr` decision for GitHub Actions. On refusal (exit 2) it also writes
 *     one `::error title=eval-protected::<reason>` annotation per reason to
 *     stderr, plus one line saying how to fix it. Reasons hold only subject
 *     names and section ids, never file contents. JSON still goes to stdout.
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

// repo -> how its paths map to subjects. `skills`: plugins/<bundle>/skills/<name>
// is skill <name> in explorer registry `registry`; `guidance`: base.md and
// sections/*.md. Any repo not listed maps to nothing.
const REPOS = {
  "Adam-S-Daniel/adam-agentskills": { kind: "skills", registry: "adam-agentskills" },
  "Adam-S-Daniel/adam-agentskills-private": { kind: "skills", registry: "adam-agentskills-private" },
  "Adam-S-Daniel/_agent-guidance": { kind: "guidance" },
};
const LANE_PREFIXES = ["eval-improve/", "eval-guidance-improve/", "retire-skill/", "retire-guidance/", "draft-skill/"];
const EMPTY_EXPLORER = { skills: [], guidance: { sections: [] } };
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

// laneExemption(headRef, authorType) — the matching lane prefix, or undefined.
function laneExemption(headRef, authorType) {
  if (authorType !== "Bot" || typeof headRef !== "string") return undefined;
  return LANE_PREFIXES.find((p) => headRef.startsWith(p));
}

// decidePr({explorer, repo, files, body, baseMd, coverage, headRef, authorType})
// — the `pr` result. `explorer` is an object, or a function returning one that
// is called only when a subject path is touched.
function decidePr({ explorer, repo, files, body, baseMd, coverage, headRef, authorType }) {
  if (typeof explorer !== "function") validateExplorer(explorer);
  const repoInfo = Object.hasOwn(REPOS, repo) ? REPOS[repo] : undefined;
  let subjectPath = false;
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
      if (repoInfo && repoInfo.kind === "skills") {
        const m = SKILL_PATH.exec(name);
        if (!m) continue;
        subjectPath = true;
        touched.add(`skill:${m[1]}`);
        if (name === f.filename && !hasPatch) noPatch(name);
      } else if (repoInfo && repoInfo.kind === "guidance") {
        if (name === BASE_MD) {
          subjectPath = true;
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
          subjectPath = true;
          const row = rows.find((r) => r.file === name);
          if (row) touched.add(`section:${row.id}`);
          else unmapped.add(name);
          if (name === f.filename && !hasPatch) noPatch(name);
        }
      }
    }
  }

  if (!subjectPath) {
    return { merge: true, touched: [], protected: [], unmarked: [], unmapped: [], reasons: [] };
  }
  if (typeof explorer === "function") {
    explorer = explorer();
    validateExplorer(explorer);
  }
  const registry = repoInfo.kind === "skills" ? repoInfo.registry : undefined;
  const skillIndex = new Map(
    explorer.skills.filter((s) => s.registry === registry).map((s) => [s.name, s]),
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

  const exempt = laneExemption(headRef, authorType);
  const result = {
    merge: exempt !== undefined || reasons.length === 0,
    touched: [...touched].sort().map((k) => k.slice(k.indexOf(":") + 1)),
    protected: prot,
    unmarked,
    unmapped: unmappedList,
    reasons,
  };
  if (exempt !== undefined) result.exempt = exempt;
  return result;
}

// ── CLI ──────────────────────────────────────────────────────────────────

const FLAGS = {
  "--explorer": "explorer",
  "--repo": "repo",
  "--files": "files",
  "--body": "body",
  "--pr-json": "prJson",
  "--base-md": "baseMd",
  "--coverage": "coverage",
  "--head-ref": "headRef",
  "--author-type": "authorType",
};
const BOOLEAN_FLAGS = { "--no-evidence": "noEvidence" };
const flagName = (key) => "--" + key.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());

const USAGE = [
  "usage:",
  "  eval-protected.js list --explorer <latest.json>",
  "  eval-protected.js pr (--explorer <latest.json> | --no-evidence) --repo <owner/name> --files <files.json> --body <pr.json>",
  "      [--base-md <path>] [--coverage <path>] [--head-ref <ref>] [--author-type <type>]",
  "  eval-protected.js ci (--explorer <latest.json> | --no-evidence) --repo <owner/name> --files <files.json> --pr-json <pr.json>",
  "      [--base-md <path>] [--coverage <path>]",
].join("\n");

function parseArgs(argv) {
  const cmd = argv[0];
  need(cmd === "list" || cmd === "pr" || cmd === "ci", USAGE);
  const out = { cmd };
  const rest = argv.slice(1);
  for (let i = 0; i < rest.length; i++) {
    const bool = BOOLEAN_FLAGS[rest[i]];
    if (bool) {
      need(out[bool] === undefined, `${rest[i]} given twice`);
      out[bool] = true;
      continue;
    }
    const key = FLAGS[rest[i]];
    need(key, `unknown argument ${rest[i]}`);
    need(i + 1 < rest.length && !rest[i + 1].startsWith("--"), `${rest[i]} needs a value`);
    need(out[key] === undefined, `${rest[i]} given twice`);
    out[key] = rest[++i];
  }
  const allowed = {
    list: ["explorer"],
    pr: ["explorer", "noEvidence", "repo", "files", "body", "baseMd", "coverage", "headRef", "authorType"],
    ci: ["explorer", "noEvidence", "repo", "files", "prJson", "baseMd", "coverage"],
  }[cmd];
  for (const k of Object.keys(out)) need(k === "cmd" || allowed.includes(k), `${flagName(k)} does not apply to ${cmd}`);
  const required = { list: ["explorer"], pr: ["repo", "files", "body"], ci: ["repo", "files", "prJson"] }[cmd];
  for (const req of required) need(out[req] !== undefined, `missing ${flagName(req)}`);
  need(!(out.explorer !== undefined && out.noEvidence), "--explorer and --no-evidence are mutually exclusive");
  if (out.prJson !== undefined) out.body = out.prJson;
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

function annotations(result) {
  const lines = result.reasons.map((r) => `::error title=eval-protected::${r.replace(/[\r\n]+/g, " ")}`);
  lines.push(
    "eval-protected: for a real error, add a PR body line `Correctness fix: <subject> — <class>: <what was wrong>` " +
      `(class: ${CLASSES.join(", ")}) and re-run this check; wording changes to a protected subject go through skills-evals' eval-backed improve lane instead.`,
  );
  return lines.join("\n") + "\n";
}

function main(argv) {
  try {
    const args = parseArgs(argv);
    if (args.cmd === "list") {
      process.stdout.write(JSON.stringify(listProtected(readJson("--explorer", args.explorer))) + "\n");
      process.exit(0);
    }
    const pr = readJson(args.cmd === "ci" ? "--pr-json" : "--body", args.body);
    need(isObject(pr), "the PR json must be a JSON object");
    const loadExplorer = () => {
      if (args.noEvidence) return EMPTY_EXPLORER;
      need(args.explorer !== undefined, "--explorer or --no-evidence is required when a subject path changed");
      return readJson("--explorer", args.explorer);
    };
    const result = decidePr({
      explorer: loadExplorer,
      repo: args.repo,
      files: readJson("--files", args.files),
      body: typeof pr.body === "string" ? pr.body : "",
      baseMd: args.baseMd === undefined ? undefined : readText("--base-md", args.baseMd),
      coverage: parseCoverage(readText("--coverage", args.coverage || DEFAULT_COVERAGE)),
      headRef: args.headRef !== undefined ? args.headRef : isObject(pr.head) ? pr.head.ref : undefined,
      authorType: args.authorType !== undefined ? args.authorType : isObject(pr.user) ? pr.user.type : undefined,
    });
    process.stdout.write(JSON.stringify(result) + "\n");
    if (!result.merge && args.cmd === "ci") process.stderr.write(annotations(result));
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
  laneExemption,
  parseArgs,
  main,
};
