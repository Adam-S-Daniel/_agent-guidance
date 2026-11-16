#!/usr/bin/env node
/*
 * check-fleet-guidance-mirror.js — three consistency checks over the guidance
 * sources in this repo. (The name predates ADR 0043, when check 1 compared a
 * mirror; it now requires the mirror's absence.)
 *
 * 1. NO MIRROR. Until ADR 0043, `.claude/hooks/fleet-guidance.md` was a
 *    hand-made byte copy of `agents-md/base.md` that this repo's own
 *    fleet-memory registration installed. ADR 0043 Decision 3 retired it: the
 *    owner's plugin installs `base.md` into this repo's sessions as into every
 *    other, so a copy here is a second, unsynced payload that nothing reads and
 *    that would go stale on the next base.md edit. A tool that still re-copies
 *    base.md here (an older improvement or removal lane) is what this catches.
 *    `.claude/hooks/fleet-memory.sh` and `guidance-load-record.sh` stay: they
 *    are the sources the tests run.
 *
 * 2. EVIDENCE POINTERS. #251 moved long incident narratives out of base.md and
 *    stub.md into docs/evidence/ and left dated links behind. Nothing checks a
 *    link target exists, so a rename or delete under docs/evidence/ turns a
 *    pointer into a 404 that every agent in the fleet reads. Every
 *    `docs/evidence/<name>.md` named in agents-md/base.md, agents-md/stub.md or
 *    agents-md/sections/*.md must be a file in this checkout. (A lexical match
 *    on a path token, not a reading of code shape, so a regex is the right
 *    tool.)
 *
 * 3. MARKERS. Session transcripts record the text of every memory file Claude
 *    Code loaded, with HTML comments stripped, so the version comment the
 *    fleet-memory hook writes is not there to count. The guidance-load Stop
 *    hook (.claude/hooks/guidance-load-record.sh) counts a session as having
 *    the full guidance or only the repo stub by one visible line in each:
 *    base.md carries exactly one `guidance-marker: fleet-full` line and no
 *    `fleet-stub` marker, stub.md exactly one `guidance-marker: fleet-stub`
 *    line and no `fleet-full` marker. A deleted, duplicated or swapped marker
 *    would silently miscount every session in the fleet. (A whole-line prefix
 *    match on a lexical token, so a regex-free line scan is enough.)
 *
 * Usage: node scripts/check-fleet-guidance-mirror.js [--root <dir>]
 * Exit 0 when all three hold; exit 1 with one line per violation; exit 2 on a
 * missing input (a check that cannot find its inputs must not certify).
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const PAYLOAD = ".claude/hooks/fleet-guidance.md";
const SOURCE = "agents-md/base.md";
const POINTER_SOURCES = ["agents-md/base.md", "agents-md/stub.md"];
const MARKERS = [
  { file: "agents-md/base.md", want: "fleet-full", forbid: "fleet-stub" },
  { file: "agents-md/stub.md", want: "fleet-stub", forbid: "fleet-full" },
];
const MARKER_LINE = (kind) => `guidance-marker: ${kind} (transcript counts use this line; keep it)`;
const EVIDENCE_RE = /docs\/evidence\/([A-Za-z0-9._-]+\.md)/g;

function checkNoMirror(root) {
  if (!fs.existsSync(path.join(root, PAYLOAD))) return [];
  return [
    `${PAYLOAD} exists, but ADR 0043 retired this repo's payload mirror: the owner's plugin installs ${SOURCE}, and nothing reads this copy. Fix: git rm ${PAYLOAD} (and stop whatever tool re-copied it)`,
  ];
}

function checkEvidencePointers(root) {
  const files = [...POINTER_SOURCES];
  const sectionsDir = path.join(root, "agents-md", "sections");
  if (fs.existsSync(sectionsDir)) {
    for (const f of fs.readdirSync(sectionsDir).sort()) if (f.endsWith(".md")) files.push(`agents-md/sections/${f}`);
  }
  const problems = [];
  for (const rel of files) {
    const full = path.join(root, rel);
    if (!fs.existsSync(full)) continue;
    const lines = fs.readFileSync(full, "utf8").split("\n");
    lines.forEach((text, i) => {
      for (const m of text.matchAll(EVIDENCE_RE)) {
        const target = `docs/evidence/${m[1]}`;
        if (!fs.existsSync(path.join(root, target))) {
          problems.push(`${rel}:${i + 1} points at ${target}, which does not exist — restore the file or fix the link`);
        }
      }
    });
  }
  return problems;
}

function checkMarkers(root) {
  const problems = [];
  for (const { file, want, forbid } of MARKERS) {
    const lines = fs.readFileSync(path.join(root, file), "utf8").split("\n");
    const wanted = lines.filter((l) => l.startsWith(`guidance-marker: ${want}`)).length;
    const forbidden = lines.filter((l) => l.startsWith(`guidance-marker: ${forbid}`)).length;
    if (wanted === 0) {
      problems.push(`${file} has no \`guidance-marker: ${want}\` line — transcript counts cannot see it. Fix: restore the line \`${MARKER_LINE(want)}\` after the managed header`);
    } else if (wanted > 1) {
      problems.push(`${file} has ${wanted} \`guidance-marker: ${want}\` lines — keep exactly one, right after the managed header`);
    }
    if (forbidden > 0) {
      problems.push(`${file} carries a \`guidance-marker: ${forbid}\` line, which belongs to the other file — transcript counts would misclassify it. Fix: remove it and keep only \`${MARKER_LINE(want)}\``);
    }
  }
  return problems;
}

function run(root) {
  for (const rel of POINTER_SOURCES) {
    if (!fs.existsSync(path.join(root, rel))) return { code: 2, lines: [`missing input ${rel} under ${root}`] };
  }
  const lines = [...checkNoMirror(root), ...checkEvidencePointers(root), ...checkMarkers(root)];
  return { code: lines.length ? 1 : 0, lines };
}

module.exports = { run, checkNoMirror, checkEvidencePointers, checkMarkers };

if (require.main === module) {
  const i = process.argv.indexOf("--root");
  const root = path.resolve(i > -1 ? process.argv[i + 1] : path.join(__dirname, ".."));
  const { code, lines } = run(root);
  for (const l of lines) console.error(`::error::${l}`);
  if (code === 0) console.log("fleet-guidance sources: no payload mirror (ADR 0043); every docs/evidence/ pointer resolves; each guidance marker appears once, in its own file");
  process.exit(code);
}
