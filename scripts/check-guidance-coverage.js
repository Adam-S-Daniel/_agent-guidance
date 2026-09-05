#!/usr/bin/env node
/*
 * check-guidance-coverage.js — the section-manifest graduation gate.
 *
 * THE PROBLEM. A `##` section of agents-md/base.md (or a file under
 * agents-md/sections/) has no identity but its own heading text, and headings
 * get reworded on their own schedule (#116 did it the week this file was
 * added). Nothing said which sections had an eval and nothing went red when a
 * new section shipped with none. ci.yml's "AGENTS.md structure is sound"
 * checks that the rendered AGENTS.md is a well-formed FILE; nothing checked
 * its CONTENT against the eval backlog.
 *
 * THE REMEDY. agents-md/eval-coverage.yml is one row per heading, keyed by a
 * stable `id` that never moves when the heading's wording does. This script
 * is the join: every heading in the real markdown must have exactly one row
 * whose `heading` field matches it byte-for-byte, and every row must point at
 * a heading that still exists. A new section with no row, or a renamed
 * heading whose row was not updated, is red — that is the graduation gate.
 *
 * THE TWO FALSE GREENS THIS CATCHES FIRST, by design: (1) a heading reworded
 * AND its body edited in the same commit, with the manifest row's `heading`
 * left stale (still the OLD text) — reported here as a stale row (nearest
 * current heading offered as the likely rename target) rather than silently
 * read as "no row for this heading, and some unrelated row is simply
 * unmatched"; (2) two manifest rows sharing one `id` — an id lookup that used
 * a plain array `.find()`/Map insert would silently let the SECOND row shadow
 * the first (or vice versa) with no error at all, so the duplicate-id check
 * below is explicit and separate from the per-row heading-resolution checks,
 * catching the collision before anything downstream (check-guidance-touch.js,
 * which joins by `id`) can resolve either row to the wrong heading.
 *
 * REAL MARKDOWN PARSE (markdown-it), never a regex or line scanner. base.md
 * contains fenced code blocks, and a `## ` inside one is not a heading — a
 * line scanner cannot tell the difference; a real parser's token stream
 * already has. (Same reasoning as scripts/check-cron-coverage.js's use of the
 * `yaml` parser for workflow files: this reasons about document STRUCTURE.)
 *
 * BYTES. Each row's `bytes` is the section's extent — from its heading
 * through the line before the next `##` heading in the same file, or end of
 * file — computed fresh every run. `--write-bytes` updates the manifest;
 * `--check-bytes` (what CI passes) fails when the stored value has drifted,
 * which happens whenever a section's prose grows or shrinks without anyone
 * touching eval-coverage.yml by hand. It is the per-session context cost the
 * eval harness's ablation arm and any future retirement gate weigh, and this
 * repo is the one that knows each section's byte extent.
 *
 * Deterministic: pure filesystem, no network, no wall-clock. Exit codes:
 *   0 — every heading has a valid row (gap/skipped/covered counts printed).
 *       With --fail-on-gap, a nonzero gap count becomes exit 1 instead.
 *   1 — a missing row, a stale row, a malformed row, a missing fixture, or a
 *       stale `bytes` value under --check-bytes.
 *   2 — could not run at all: an unreadable source file, a manifest that
 *       isn't a YAML list, or zero headings found anywhere (never 0 on zero,
 *       per the fleet's own convention — a gate that finds nothing must not
 *       read the same as a gate that found nothing wrong).
 *
 * Usage:
 *   node scripts/check-guidance-coverage.js
 *   node scripts/check-guidance-coverage.js --skills-evals ../skills-evals --check-bytes
 *   node scripts/check-guidance-coverage.js --write-bytes
 *   node scripts/check-guidance-coverage.js --format json
 *   node scripts/check-guidance-coverage.js --fail-on-gap
 *   node scripts/check-guidance-coverage.js --repo-root <dir>   # a fixture tree in tests
 *
 * `--repo-root` defaults to this repo (the directory this script's own
 * parent lives in) and resolves `agents-md/base.md`, `agents-md/sections/`
 * and `agents-md/eval-coverage.yml` under it — the same shape a test fixture
 * has to reproduce, which is what lets the test suite point this at a temp
 * directory instead of the real tree.
 */
const fs = require("node:fs");
const path = require("node:path");
const YAML = require("yaml");
const { extractHeadings } = require("./lib/markdown-sections");

function flag(name) {
  return process.argv.includes(`--${name}`);
}

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  if (i === -1) return def;
  const val = process.argv[i + 1];
  if (val === undefined) {
    throw new RunError(`--${name} requires a value, got nothing — it was the last argument`);
  }
  if (val === "" || val.startsWith("--")) {
    throw new RunError(
      `--${name} requires a value, got ${val === "" ? "an empty string" : `flag-like "${val}"`} instead`,
    );
  }
  return val;
}

// Thrown for anything that means "could not run at all" — exit 2, never 0 or
// 1, and never confused with "ran and found a defect."
class RunError extends Error {}

// ── Markdown extraction ─────────────────────────────────────────────────
//
// The `##` heading parse itself (markdown-it, never a regex or line
// scanner) lives in scripts/lib/markdown-sections.js — shared with
// scripts/check-guidance-touch.js (_agent-guidance#120), which additionally
// needs the startLine/endLine range that function returns alongside each
// heading to compare a section's body text across two commits.

function collectHeadings(repoRoot) {
  const baseFile = path.join(repoRoot, "agents-md", "base.md");
  const sectionsDir = path.join(repoRoot, "agents-md", "sections");

  const docs = [{ rel: "agents-md/base.md", abs: baseFile }];
  if (fs.existsSync(sectionsDir)) {
    for (const name of fs.readdirSync(sectionsDir).sort()) {
      if (name.endsWith(".md")) {
        docs.push({ rel: `agents-md/sections/${name}`, abs: path.join(sectionsDir, name) });
      }
    }
  }

  const headings = [];
  for (const doc of docs) {
    let src;
    try {
      src = fs.readFileSync(doc.abs, "utf8");
    } catch (e) {
      throw new RunError(`cannot read ${doc.rel}: ${e.message}`);
    }
    headings.push(...extractHeadings(src, doc.rel));
  }
  return headings;
}

// ── Manifest ─────────────────────────────────────────────────────────────

function loadManifestDoc(manifestPath) {
  let raw;
  try {
    raw = fs.readFileSync(manifestPath, "utf8");
  } catch (e) {
    throw new RunError(`cannot read manifest ${manifestPath}: ${e.message}`);
  }
  let doc;
  try {
    doc = YAML.parseDocument(raw, { prettyErrors: true });
  } catch (e) {
    throw new RunError(`manifest ${manifestPath} is not valid YAML: ${e.message}`);
  }
  if (doc.errors && doc.errors.length > 0) {
    throw new RunError(`manifest ${manifestPath} is not valid YAML: ${doc.errors[0].message}`);
  }
  const rows = doc.toJS();
  if (!Array.isArray(rows)) {
    throw new RunError(`manifest ${manifestPath} must be a YAML list of rows, got ${typeof rows}`);
  }
  return { doc, rows };
}

// ── Levenshtein, for the stale-row rename hint ──────────────────────────

function levenshtein(a, b) {
  const m = a.length;
  const n = b.length;
  const dp = new Array(n + 1);
  for (let j = 0; j <= n; j++) dp[j] = j;
  for (let i = 1; i <= m; i++) {
    let prev = dp[0];
    dp[0] = i;
    for (let j = 1; j <= n; j++) {
      const tmp = dp[j];
      dp[j] = a[i - 1] === b[j - 1] ? prev : 1 + Math.min(prev, dp[j], dp[j - 1]);
      prev = tmp;
    }
  }
  return dp[n];
}

function nearestHeading(target, candidates) {
  let best = null;
  let bestDist = Infinity;
  for (const c of candidates) {
    const d = levenshtein(target, c);
    if (d < bestDist) {
      bestDist = d;
      best = c;
    }
  }
  return { heading: best, distance: bestDist };
}

// ── Validation ───────────────────────────────────────────────────────────

// A manifest row must be a YAML mapping. `!row` only catches `null` (a bare
// `-` list entry) — a bare `- oops` parses to a truthy, non-object string,
// which is just as malformed but passes a falsy check.
function isMalformedRow(row) {
  return typeof row !== "object" || row === null;
}

function validate({ headings, rows, skillsEvals }) {
  const errors = [];
  const notes = [];

  // A property of the RUN, not of any one row: whether fixture paths were
  // verifiable at all. Printed once here, up front, rather than inside the
  // per-row `covered` branch below — nested there, it never fired on a tree
  // with zero covered rows and fired once per covered row otherwise.
  if (!skillsEvals) {
    notes.push("fixture paths not verified (no --skills-evals given)");
  }

  // Source-side sanity: two sections sharing heading text would make the
  // join ambiguous. Not something the fleet's own docs do today, but a
  // silent Map overwrite would misreport a real defect as a clean run, so
  // it is caught explicitly rather than left to corrupt the rest of the walk.
  const headingsByText = new Map();
  for (const h of headings) {
    if (headingsByText.has(h.heading)) {
      const prior = headingsByText.get(h.heading);
      errors.push(
        `duplicate heading "${h.heading}" appears in both ${prior.file} and ${h.file} — headings must be unique across the guidance source`,
      );
      continue;
    }
    headingsByText.set(h.heading, h);
  }
  const headingTexts = [...headingsByText.keys()];

  // id uniqueness across the manifest.
  const seenIds = new Map();
  for (const row of rows) {
    // `!row` is falsy-only: a bare `-` (null) is caught, but a bare `- oops`
    // (a truthy, non-object YAML scalar string) is not, and `row.id` on a
    // string is `undefined` — which the length/type check below still
    // catches, so this branch was never wrong, only fragile. Checked by
    // type here so the two loops (and the --format json filter, which had
    // the same gap) agree on what counts as malformed.
    if (isMalformedRow(row) || typeof row.id !== "string" || row.id.length === 0) {
      errors.push(`manifest row is missing a non-empty 'id': ${JSON.stringify(row)}`);
      continue;
    }
    if (seenIds.has(row.id)) {
      errors.push(`duplicate id "${row.id}" in eval-coverage.yml`);
    }
    seenIds.set(row.id, row);
  }

  // Every row: structural validity + does its heading still exist.
  const rowsById = new Map();
  for (const row of rows) {
    // A malformed row (a bare `-` list entry, or any other non-object, such
    // as a bare `- oops`) was already reported by the id-uniqueness loop
    // above — skip it here rather than crash on `row.heading` of a value
    // with no properties.
    if (isMalformedRow(row)) continue;
    if (typeof row.heading !== "string" || row.heading.length === 0) {
      errors.push(`row "${row.id}" is missing a non-empty 'heading'`);
      continue;
    }
    if (typeof row.file !== "string" || row.file.length === 0) {
      errors.push(`row "${row.id}" is missing a non-empty 'file'`);
    }
    if (!["gap", "covered", "skipped"].includes(row.status)) {
      errors.push(`row "${row.id}" has status "${row.status}", expected gap, covered or skipped`);
    }

    if (row.status === "covered") {
      if (typeof row.fixture !== "string" || row.fixture.length === 0) {
        errors.push(`row "${row.id}" is status: covered but has no 'fixture'`);
      } else if (skillsEvals) {
        const fixturePath = path.join(skillsEvals, row.fixture);
        if (!fs.existsSync(fixturePath)) {
          errors.push(
            `row "${row.id}" names fixture "${row.fixture}", which does not exist under --skills-evals ${skillsEvals}`,
          );
        }
      }
    }

    if (row.status === "skipped") {
      if (typeof row.reason !== "string" || row.reason.trim().length === 0) {
        errors.push(`row "${row.id}" is status: skipped but has no 'reason'`);
      }
      if (typeof row.since !== "string" || row.since.trim().length === 0) {
        errors.push(`row "${row.id}" is status: skipped but has no 'since'`);
      }
    }

    const heading = headingsByText.get(row.heading);
    if (!heading) {
      const { heading: hint, distance } = nearestHeading(row.heading, headingTexts);
      errors.push(
        hint === null
          ? `row "${row.id}" (heading: "${row.heading}") is stale — no such heading exists anywhere in the guidance source`
          : `row "${row.id}" (heading: "${row.heading}") is stale — no such heading found; nearest current heading is "${hint}" (edit distance ${distance}) — likely a rename, update the row's heading text`,
      );
      continue;
    }
    if (heading.file !== row.file) {
      errors.push(
        `row "${row.id}" declares file "${row.file}" but heading "${row.heading}" was found in "${heading.file}"`,
      );
    }
    rowsById.set(row.id, { row, heading });
  }

  // Every discovered heading: exactly one row.
  for (const h of headings) {
    const matches = rows.filter((row) => row && row.heading === h.heading);
    if (matches.length === 0) {
      errors.push(
        `heading "${h.heading}" in ${h.file} has no row in eval-coverage.yml — add a covered row naming a fixture, or a skipped row naming a reason`,
      );
    } else if (matches.length > 1) {
      errors.push(
        `heading "${h.heading}" in ${h.file} matches ${matches.length} rows (${matches
          .map((r) => r.id)
          .join(", ")}) — expected exactly one`,
      );
    }
  }

  const counts = { gap: 0, covered: 0, skipped: 0 };
  for (const row of rows) {
    if (row && counts[row.status] !== undefined) counts[row.status] += 1;
  }

  return { errors, notes, counts, rowsById };
}

function checkBytes(rowsById) {
  const errors = [];
  for (const [id, { row, heading }] of rowsById) {
    if (row.bytes !== heading.bytes) {
      errors.push(
        `row "${id}" has bytes: ${row.bytes ?? "(none)"}, but the section is currently ${heading.bytes} bytes — run with --write-bytes to refresh it`,
      );
    }
  }
  return errors;
}

function writeBytes(doc, rowsById, errors) {
  for (const item of doc.contents.items) {
    // A non-mapping list item (`- oops`, a bare `-`) has no `.get`/`.set` —
    // report it here, at the point of the crash it used to cause, rather
    // than skipping the write for the whole manifest because ONE row is
    // malformed.
    if (typeof item.get !== "function") {
      errors.push(
        `manifest row is not a YAML mapping, cannot write bytes to it: ${JSON.stringify(item.toJSON())}`,
      );
      continue;
    }
    const id = item.get("id");
    const matched = rowsById.get(id);
    if (matched) {
      item.set("bytes", matched.heading.bytes);
    }
  }
}

// ── Main ─────────────────────────────────────────────────────────────────

function main() {
  const repoRoot = path.resolve(arg("repo-root", path.join(__dirname, "..")));
  const manifestPath = path.join(repoRoot, "agents-md", "eval-coverage.yml");
  const skillsEvals = arg("skills-evals", null);
  const format = arg("format", "text");
  const writeBytesFlag = flag("write-bytes");
  const checkBytesFlag = flag("check-bytes");
  const failOnGap = flag("fail-on-gap");

  const headings = collectHeadings(repoRoot);
  if (headings.length === 0) {
    throw new RunError(`found zero '##' headings under ${repoRoot}/agents-md — nothing to check`);
  }

  const { doc, rows } = loadManifestDoc(manifestPath);

  const { errors, notes, counts, rowsById } = validate({
    headings,
    rows,
    skillsEvals: skillsEvals ? path.resolve(skillsEvals) : null,
  });

  // --write-bytes wins unconditionally, even when other errors exist (an
  // un-rowed heading, a stale row) — AGENTS.md and the manifest header both
  // document it as the unconditional fix for byte drift, and gating it on
  // `errors.length === 0` meant it silently declined to write whenever any
  // OTHER error was present, with `--write-bytes --check-bytes` reporting
  // the drift and telling the user to run the flag they had just run.
  // writeBytes() itself reports (rather than crashes on) a non-mapping list
  // item, so a malformed row can't block the well-formed rows from being
  // refreshed.
  if (writeBytesFlag) {
    writeBytes(doc, rowsById, errors);
    fs.writeFileSync(manifestPath, doc.toString());
  } else if (checkBytesFlag) {
    errors.push(...checkBytes(rowsById));
  }

  // In JSON mode stdout must carry ONLY the JSON payload — a note or the
  // summary line sharing the stream breaks `| jq .` and any other consumer
  // that expects one parseable document. Both still go to stderr, where a
  // human running this locally will still see them.
  const notePrinter = format === "json" ? console.error : console.log;
  for (const note of notes) {
    notePrinter(`note: ${note}`);
  }

  if (format === "json") {
    // Field names mirror the manifest row itself — `id`, not a renamed
    // "section" — so this is a direct machine-readable echo of
    // eval-coverage.yml, plus a `subject` tag for skills-evals' coverage
    // census (#64), which merges rows from several subjects (skills,
    // guidance, ...) and needs that tag to tell them apart. A malformed row
    // (null, or any other non-object such as a bare `- oops`) is already
    // named in `errors` by validate() — exclude it here rather than emit an
    // entry with `id`/`heading`/`file`/`status` silently dropped by
    // `JSON.stringify` (id is the census join key).
    const out = rows.filter((row) => !isMalformedRow(row)).map((row) => ({
      subject: "guidance",
      id: row.id,
      heading: row.heading,
      file: row.file,
      status: row.status,
      fixture: row.fixture ?? null,
      reason: row.reason ?? null,
      since: row.since ?? null,
      bytes: rowsById.has(row.id) ? rowsById.get(row.id).heading.bytes : (row.bytes ?? null),
    }));
    console.log(JSON.stringify(out, null, 2));
  }

  // `process.exitCode` + `return`, never `process.exit()`, from here down: a
  // process piped into another command (`| cat`, command substitution, CI
  // log capture) gets a non-blocking stdout, and a write past the kernel's
  // pipe buffer (64 KiB on Linux) is queued rather than synchronous —
  // `process.exit()` tears the process down mid-write and truncates it.
  // `--format json`'s console.log above can be arbitrarily large. Setting
  // `exitCode` and returning lets the event loop drain pending writes before
  // the process exits on its own.
  if (errors.length > 0) {
    for (const e of errors) console.error(`check-guidance-coverage: ${e}`);
    process.exitCode = 1;
    return;
  }

  const summaryLine = `${counts.gap} gap · ${counts.skipped} skipped · ${counts.covered} covered`;
  notePrinter(summaryLine);

  if (failOnGap && counts.gap > 0) {
    console.error(`check-guidance-coverage: --fail-on-gap set and ${counts.gap} row(s) are gap`);
    process.exitCode = 1;
    return;
  }
}

try {
  main();
} catch (e) {
  if (e instanceof RunError) {
    console.error(`check-guidance-coverage: ${e.message}`);
    process.exit(2);
  }
  throw e;
}
