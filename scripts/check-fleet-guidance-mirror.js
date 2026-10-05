#!/usr/bin/env node
/*
 * check-fleet-guidance-mirror.js — two consistency checks over the guidance
 * this repo ships to itself.
 *
 * 1. MIRROR. `.claude/hooks/fleet-guidance.md` is the payload the fleet-memory
 *    hook installs into user memory, and in this repo it is a hand-made COPY of
 *    `agents-md/base.md`: byte-identical, no script derives it (the hook's
 *    PAYLOAD default, scripts/sync.sh and scripts/drift-report.sh all treat it
 *    as an opaque file). This repo is excluded from the sync
 *    (SYNC_SELF_REPO), so nothing overwrites a stale copy and no drift report
 *    names it, and its own AGENTS.md is the stub — so the payload is the only
 *    copy of the guidance this repo's own sessions load. A base.md edit with no
 *    re-copy leaves those sessions reading something the fleet is not.
 *    test_self_hosted_fleet_payload (test/run-tests.sh) already compares the two
 *    inside the integration suite; this runs as its own early CI step
 *    so the failure names the one-line fix instead of a buried FAIL line.
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
 * Usage: node scripts/check-fleet-guidance-mirror.js [--root <dir>]
 * Exit 0 when both hold; exit 1 with one line per violation; exit 2 on a
 * missing input (a check that cannot find its inputs must not certify).
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const PAYLOAD = ".claude/hooks/fleet-guidance.md";
const SOURCE = "agents-md/base.md";
const POINTER_SOURCES = ["agents-md/base.md", "agents-md/stub.md"];
const EVIDENCE_RE = /docs\/evidence\/([A-Za-z0-9._-]+\.md)/g;

function firstDifferingLine(a, b) {
  const x = a.toString("utf8").split("\n");
  const y = b.toString("utf8").split("\n");
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) if (x[i] !== y[i]) return i + 1;
  return 0;
}

function checkMirror(root) {
  const src = fs.readFileSync(path.join(root, SOURCE));
  const payloadPath = path.join(root, PAYLOAD);
  if (!fs.existsSync(payloadPath) || fs.statSync(payloadPath).size === 0) {
    return [`${PAYLOAD} is missing or empty — this repo's own sessions would open DEGRADED. Fix: cp ${SOURCE} ${PAYLOAD}`];
  }
  const payload = fs.readFileSync(payloadPath);
  if (src.equals(payload)) return [];
  const line = firstDifferingLine(src, payload);
  return [
    `${PAYLOAD} differs from ${SOURCE} (${payload.length} vs ${src.length} bytes, first difference at line ${line}) — nothing syncs this repo, so re-copy it. Fix: cp ${SOURCE} ${PAYLOAD}`,
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

function run(root) {
  for (const rel of POINTER_SOURCES) {
    if (!fs.existsSync(path.join(root, rel))) return { code: 2, lines: [`missing input ${rel} under ${root}`] };
  }
  const lines = [...checkMirror(root), ...checkEvidencePointers(root)];
  return { code: lines.length ? 1 : 0, lines };
}

module.exports = { run, checkMirror, checkEvidencePointers };

if (require.main === module) {
  const i = process.argv.indexOf("--root");
  const root = path.resolve(i > -1 ? process.argv[i + 1] : path.join(__dirname, ".."));
  const { code, lines } = run(root);
  for (const l of lines) console.error(`::error::${l}`);
  if (code === 0) console.log("fleet-guidance mirror: payload is byte-identical to agents-md/base.md; every docs/evidence/ pointer resolves");
  process.exit(code);
}
