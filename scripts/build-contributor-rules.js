#!/usr/bin/env node
/*
 * build-contributor-rules.js — build .github/fleet-contributor-rules.md from
 * agents-md/base.md (ADR 0043 decision 1).
 *
 * base.md stays the one hand-edited source and the owner's full payload. Each
 * row of agents-md/eval-coverage.yml says who its section is for
 * (`audience: contributor | owner`); this script copies the base.md sections
 * marked `contributor`, in base.md order, byte for byte, under a fixed header.
 * The sync delivers the result to consumer repos as a managed file that no
 * AGENTS.md or CLAUDE.md imports: an agent reads it on demand, when the stub
 * says the full guidance did not load. The base.md preamble (the text before
 * the first `##`) is the owner's and never ships.
 *
 * Section boundaries come from scripts/lib/markdown-sections.js (markdown-it),
 * the parser the coverage and touch gates use, so a `## ` inside a fenced block
 * is never taken for a heading.
 *
 * Refuses (exit 2) rather than guess: a base.md heading with no manifest row,
 * or a row with no valid `audience`, would otherwise land silently on one side
 * of the boundary. check-guidance-coverage.js reports the same defects with
 * more detail; this script only declines to build from them.
 *
 * Deterministic: filesystem only, no network, no clock.
 *
 * Usage:
 *   node scripts/build-contributor-rules.js            # print to stdout
 *   node scripts/build-contributor-rules.js --write    # write the file
 *   node scripts/build-contributor-rules.js --check    # exit 1 if the file is stale
 *   node scripts/build-contributor-rules.js --repo-root <dir>  # a fixture tree
 *
 * Exit codes: 0 built (or up to date), 1 stale under --check, 2 cannot build.
 */
const fs = require("node:fs");
const path = require("node:path");
const YAML = require("yaml");
const { extractHeadings } = require("./lib/markdown-sections");

const OUTPUT_REL = ".github/fleet-contributor-rules.md";
const BASE_REL = "agents-md/base.md";
const MANIFEST_REL = "agents-md/eval-coverage.yml";

const HEADER = `<!-- Managed by _agent-guidance: built by scripts/build-contributor-rules.js
     from agents-md/base.md (the sections agents-md/eval-coverage.yml marks
     audience: contributor). Do not edit here; the next sync overwrites it. -->

# Fleet contributor rules

These rules apply to anyone, person or agent, changing this repository. They
are the contributor half of the fleet guidance kept in
[\`_agent-guidance\`](https://github.com/Adam-S-Daniel/_agent-guidance). Read
them before non-trivial work when the full guidance did not load in your
session.

`;

class RunError extends Error {}

function arg(name) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

function build(repoRoot) {
  let src, raw;
  try {
    src = fs.readFileSync(path.join(repoRoot, BASE_REL), "utf8");
    raw = fs.readFileSync(path.join(repoRoot, MANIFEST_REL), "utf8");
  } catch (e) {
    throw new RunError(`cannot read the guidance source: ${e.message}`);
  }
  const doc = YAML.parseDocument(raw);
  if (doc.errors.length > 0) throw new RunError(`${MANIFEST_REL} is not valid YAML: ${doc.errors[0].message}`);
  const rows = doc.toJS();
  if (!Array.isArray(rows)) throw new RunError(`${MANIFEST_REL} must be a YAML list of rows`);

  const audienceByHeading = new Map();
  for (const row of rows) {
    if (row && typeof row === "object" && row.file === BASE_REL && typeof row.heading === "string") {
      audienceByHeading.set(row.heading, row.audience);
    }
  }

  const headings = extractHeadings(src, BASE_REL);
  if (headings.length === 0) throw new RunError(`found zero '##' headings in ${BASE_REL}`);

  const lines = src.split("\n");
  const parts = [];
  for (const h of headings) {
    if (!audienceByHeading.has(h.heading)) {
      throw new RunError(`heading "${h.heading}" in ${BASE_REL} has no row in ${MANIFEST_REL}`);
    }
    const audience = audienceByHeading.get(h.heading);
    if (audience !== "contributor" && audience !== "owner") {
      throw new RunError(`row for "${h.heading}" has audience "${audience}", expected contributor or owner`);
    }
    if (audience === "contributor") {
      // Trailing blank lines dropped so every section is joined by exactly one.
      parts.push(lines.slice(h.startLine, h.endLine).join("\n").replace(/\n+$/, ""));
    }
  }
  if (parts.length === 0) throw new RunError(`no section in ${BASE_REL} is marked audience: contributor`);
  return HEADER + parts.join("\n\n") + "\n";
}

function main() {
  const repoRoot = path.resolve(arg("--repo-root") || path.join(__dirname, ".."));
  let out;
  try {
    out = build(repoRoot);
  } catch (e) {
    if (e instanceof RunError) {
      console.error(`build-contributor-rules: ${e.message}`);
      process.exit(2);
    }
    throw e;
  }
  const target = path.join(repoRoot, OUTPUT_REL);
  if (process.argv.includes("--write")) {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, out);
    return;
  }
  if (process.argv.includes("--check")) {
    const current = fs.existsSync(target) ? fs.readFileSync(target, "utf8") : null;
    if (current !== out) {
      console.error(
        `build-contributor-rules: ${OUTPUT_REL} is ${current === null ? "missing" : "stale"}; ` +
          "regenerate it with `node scripts/build-contributor-rules.js --write` and commit the result.",
      );
      process.exit(1);
    }
    console.log(`build-contributor-rules: ${OUTPUT_REL} is up to date`);
    return;
  }
  process.stdout.write(out);
}

if (require.main === module) main();

module.exports = { build, OUTPUT_REL };
