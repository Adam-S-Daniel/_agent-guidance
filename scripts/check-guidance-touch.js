#!/usr/bin/env node
/*
 * check-guidance-touch.js — the docs/guidance-impact.md touch gate.
 *
 * THE PROBLEM. An edit to a `##` section of agents-md/base.md (or a file
 * under agents-md/sections/) ships on the strength of its PR description.
 * Nothing records what it was measured against, and nothing records a
 * proposal that was tried and turned down, so the next session re-derives
 * it. scripts/check-guidance-coverage.js (_agent-guidance#119) proves every
 * section HAS a manifest row; this script proves that a PR CHANGING a
 * section's content also adds an entry to docs/guidance-impact.md for it.
 *
 * THE JOIN. agents-md/eval-coverage.yml's `id` is the stable key across a
 * heading's own wording changing — see that file's header. This script reads
 * the manifest and the guidance source at both the PR's base and head shas,
 * matches rows by `id` (never by heading text, which a rename changes), and
 * for every id whose section BODY (the extent's text after its own heading
 * line — comparing the heading line itself would flag a pure rename, which
 * needs no entry per the issue's test list) differs between the two commits,
 * requires a new entry for that id in this diff's addition to
 * docs/guidance-impact.md. An id present at head with no counterpart at base
 * is a `create`; an id present at base with no counterpart at head is a
 * `remove` and specifically requires a `remove`-typed entry. An id whose
 * heading text does not resolve to a real heading at its own commit (a stale
 * manifest row, or a heading with no row at all) is left alone — that
 * defect belongs to check-guidance-coverage.js, and reporting it here too
 * would be the same failure reported twice.
 *
 * THE BASE/HEAD SHAS come from $GITHUB_EVENT_PATH
 * (pull_request.base.sha / pull_request.head.sha), read inside this script —
 * never `${{ github.event.pull_request.* }}` interpolated into a workflow
 * `run:` block, which echoes the rendered value into the log.
 * agents-md/base.md, agents-md/sections/*.md AND agents-md/eval-coverage.yml
 * are all read at both shas via `git show <sha>:<path>` (not the checked-out
 * working tree) — the working tree holds only one commit at a time, and a
 * `pull_request` checkout's default HEAD is a synthetic merge commit, not
 * literally `pull_request.head.sha`.
 *
 * docs/guidance-impact.md itself is parsed structurally: `##` entry headings
 * via the same markdown-it token walk as scripts/lib/markdown-sections.js,
 * and each entry's `- Eval: ...` bullet by matching list-item INLINE TOKENS
 * (never a regex scanning the raw file — a bullet illustrating the format
 * inside the fenced code block under "## Entry format" is a `fence` token,
 * not a list item, so it is never mistaken for a real entry).
 *
 * Exit codes:
 *   0 — every touched id has a sufficient new entry (including: nothing was
 *       touched at all).
 *   1 — a touched id has no new entry, an insufficient one (an Eval: none
 *       line while its manifest row is not `gap`), or a removed id's entry
 *       is not typed `remove`. Errors name the id and the entry format.
 *   2 — could not run at all: no $GITHUB_EVENT_PATH, an unreadable or
 *       unparseable event file, or docs/guidance-impact.md missing at head.
 *
 * Usage:
 *   GITHUB_EVENT_PATH=/path/to/event.json node scripts/check-guidance-touch.js
 *   node scripts/check-guidance-touch.js --repo-root <dir>   # a fixture tree in tests
 */
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const YAML = require("yaml");
const MarkdownIt = require("markdown-it");
const { extractHeadings } = require("./lib/markdown-sections");

const md = new MarkdownIt();

const ENTRY_TYPES = ["create", "edit", "rename", "remove", "rejected"];

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

// ── The PR event ────────────────────────────────────────────────────────

function readEvent() {
  const eventPath = process.env.GITHUB_EVENT_PATH;
  if (!eventPath) {
    throw new RunError("no event file: GITHUB_EVENT_PATH is not set");
  }
  let raw;
  try {
    raw = fs.readFileSync(eventPath, "utf8");
  } catch (e) {
    throw new RunError(`no event file: cannot read ${eventPath}: ${e.message}`);
  }
  let event;
  try {
    event = JSON.parse(raw);
  } catch (e) {
    throw new RunError(`no event file: ${eventPath} is not valid JSON: ${e.message}`);
  }
  const baseSha = event && event.pull_request && event.pull_request.base && event.pull_request.base.sha;
  const headSha = event && event.pull_request && event.pull_request.head && event.pull_request.head.sha;
  if (typeof baseSha !== "string" || !baseSha || typeof headSha !== "string" || !headSha) {
    throw new RunError(
      `no event file: ${eventPath} has no pull_request.base.sha / pull_request.head.sha — this check only runs on pull_request`,
    );
  }
  return { baseSha, headSha };
}

// ── git show, at a given ref ────────────────────────────────────────────

// gitShow — the file's content at <sha>, or null if it does not exist there
// (a file added or removed between base and head). Anything else (a broken
// repo, a sha git cannot resolve) is a RunError — the caller cannot tell
// "file absent" from "git failed" from a null return alone otherwise.
function gitShow(repoRoot, sha, relPath) {
  try {
    return execFileSync("git", ["show", `${sha}:${relPath}`], {
      cwd: repoRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (e) {
    const stderr = (e.stderr || "").toString();
    if (/does not exist in|exists on disk, but not in|fatal: path .* does not exist/i.test(stderr)) {
      return null;
    }
    throw new RunError(`git show ${sha}:${relPath} failed: ${stderr.trim() || e.message}`);
  }
}

// gitListFiles — every file under <dirRelPath> that exists in the tree at
// <sha>, recursively. Used to discover agents-md/sections/*.md at a
// historical commit, where there is no filesystem directory to `readdirSync`
// — only `git show`/`git ls-tree` reach a non-checked-out commit.
function gitListFiles(repoRoot, sha, dirRelPath) {
  let out;
  try {
    out = execFileSync("git", ["ls-tree", "-r", "--name-only", sha, "--", dirRelPath], {
      cwd: repoRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (e) {
    throw new RunError(`git ls-tree ${sha} -- ${dirRelPath} failed: ${(e.stderr || e.message).toString().trim()}`);
  }
  return out.split("\n").filter(Boolean);
}

// ── Guidance source (base.md + sections/*.md) at a ref ─────────────────

function collectHeadingsAt(repoRoot, sha) {
  const files = gitListFiles(repoRoot, sha, "agents-md");
  const docs = files
    .filter((f) => f === "agents-md/base.md" || /^agents-md\/sections\/[^/]+\.md$/.test(f))
    .sort();

  const headings = [];
  const sources = {};
  for (const rel of docs) {
    const src = gitShow(repoRoot, sha, rel);
    if (src === null) continue; // listed by ls-tree but unreadable — treat as absent
    sources[rel] = src;
    headings.push(...extractHeadings(src, rel));
  }
  return { headings, sources };
}

// sectionBody — the section's text AFTER its own heading line, through the
// line before the next heading (or EOF). Deliberately excludes the heading
// line itself: a pure rename (heading text changes, body does not) must not
// register as a body change, per the issue's own test list.
function sectionBody(sources, heading) {
  const src = sources[heading.file];
  const lines = src.split("\n");
  return lines.slice(heading.startLine + 1, heading.endLine).join("\n");
}

// ── The section manifest (agents-md/eval-coverage.yml) at a ref ────────

// loadManifestAt — id -> row, at <sha>. A manifest missing at HEAD is fatal
// (RunError, exit 2: a fundamental input is absent). A manifest missing at
// BASE is not — older history may predate the manifest entirely — and
// resolves to an empty map rather than an error.
function loadManifestAt(repoRoot, sha, { required }) {
  const raw = gitShow(repoRoot, sha, "agents-md/eval-coverage.yml");
  if (raw === null) {
    if (required) {
      throw new RunError(`agents-md/eval-coverage.yml does not exist at ${sha}`);
    }
    return new Map();
  }
  let rows;
  try {
    rows = YAML.parse(raw);
  } catch (e) {
    if (required) {
      throw new RunError(`agents-md/eval-coverage.yml at ${sha} is not valid YAML: ${e.message}`);
    }
    return new Map();
  }
  if (!Array.isArray(rows)) return new Map();
  const byId = new Map();
  for (const row of rows) {
    if (row && typeof row === "object" && typeof row.id === "string" && row.id) {
      byId.set(row.id, row);
    }
  }
  return byId;
}

// ── docs/guidance-impact.md ─────────────────────────────────────────────

// parseImpactEntries — every `##` heading in the file plus the text of its
// first `- Eval: ...` bullet, in document order. Structural: headings come
// from markdown-it's token stream (never a line scan — the fenced example
// under "## Entry format" contains a literal "- Eval:" line that a line
// scanner would mistake for a real entry; a `fence` token is not a
// `list_item`/`inline` pair, so the real walk never sees it), and the Eval
// bullet comes from matching an inline token's own content, not a regex over
// the file.
function parseImpactEntries(src) {
  const tokens = md.parse(src, {});
  const entries = [];
  let current = null;
  let expectHeadingText = false;
  for (const t of tokens) {
    if (t.type === "heading_open" && t.tag === "h2") {
      current = { heading: null, evalLine: null };
      entries.push(current);
      expectHeadingText = true;
      continue;
    }
    if (t.type !== "inline") continue;
    if (expectHeadingText) {
      if (current) current.heading = t.content;
      expectHeadingText = false;
      continue;
    }
    if (current && current.evalLine === null && /^Eval:/.test(t.content.trim())) {
      current.evalLine = t.content.trim();
    }
  }
  return entries;
}

// toDatedEntries — filter parseImpactEntries()'s raw headings down to real
// dated entries ("YYYY-MM-DD — <section-id> — <type>"), dropping boilerplate
// like "## Entry format" that is a real h2 but not an entry.
function toDatedEntries(rawEntries) {
  const out = [];
  for (const e of rawEntries) {
    if (typeof e.heading !== "string") continue;
    const parts = e.heading.split(" — ").map((s) => s.trim());
    if (parts.length !== 3) continue;
    const [date, id, type] = parts;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) continue;
    if (!ENTRY_TYPES.includes(type)) continue;
    out.push({ date, id, type, evalLine: e.evalLine, heading: e.heading });
  }
  return out;
}

// addedEntries — head's dated entries minus base's, by exact heading text.
// docs/guidance-impact.md is append-only (never edited in place), so a
// dated heading present at head and absent at base was added by this diff.
function addedEntries(repoRoot, baseSha, headSha) {
  const headSrc = gitShow(repoRoot, headSha, "docs/guidance-impact.md");
  if (headSrc === null) {
    throw new RunError(`docs/guidance-impact.md does not exist at ${headSha} — it must be created before this check can run`);
  }
  const baseSrc = gitShow(repoRoot, baseSha, "docs/guidance-impact.md");

  const headEntries = toDatedEntries(parseImpactEntries(headSrc));
  const baseEntries = baseSrc === null ? [] : toDatedEntries(parseImpactEntries(baseSrc));
  const baseHeadings = new Set(baseEntries.map((e) => e.heading));
  return headEntries.filter((e) => !baseHeadings.has(e.heading));
}

// ── The join: which ids did this diff touch, and how ────────────────────

function computeTouched({ headManifest, baseManifest, headGuidance, baseGuidance }) {
  const headHeadingByText = new Map(headGuidance.headings.map((h) => [h.heading, h]));
  const baseHeadingByText = new Map(baseGuidance.headings.map((h) => [h.heading, h]));

  const allIds = new Set([...headManifest.keys(), ...baseManifest.keys()]);
  const touched = [];

  for (const id of allIds) {
    const headRow = headManifest.get(id);
    const baseRow = baseManifest.get(id);

    if (headRow && !baseRow) {
      // A brand-new id. Its heading must actually exist at head to count as
      // a real, checkable creation — a row with no matching heading at all
      // is check-guidance-coverage.js's failure, not this one's.
      if (headHeadingByText.has(headRow.heading)) {
        touched.push({ id, kind: "create", row: headRow });
      }
      continue;
    }

    if (!headRow && baseRow) {
      // A removed id: it had a row at base and has none at head. Whether its
      // heading still resolves at base is not load-bearing here — the row's
      // prior existence is itself the signal that a section was retired.
      touched.push({ id, kind: "remove", row: baseRow });
      continue;
    }

    // Present at both: compare the section's BODY text (never the heading
    // line — see sectionBody's own comment) between the two commits.
    const headHeading = headHeadingByText.get(headRow.heading);
    const baseHeading = baseHeadingByText.get(baseRow.heading);
    if (!headHeading || !baseHeading) continue; // a stale row — #119's job

    const headBody = sectionBody(headGuidance.sources, headHeading);
    const baseBody = sectionBody(baseGuidance.sources, baseHeading);
    if (headBody !== baseBody) {
      touched.push({ id, kind: "edit", row: headRow });
    }
  }

  return touched;
}

// ── Validation: does every touched id have a sufficient new entry? ──────

function checkEntries(touched, addedByHead) {
  const byId = new Map();
  for (const e of addedByHead) {
    if (!byId.has(e.id)) byId.set(e.id, []);
    byId.get(e.id).push(e);
  }

  const errors = [];
  for (const t of touched) {
    const entries = byId.get(t.id) || [];
    if (entries.length === 0) {
      errors.push(
        `section "${t.id}" changed but has no new entry in docs/guidance-impact.md — add ` +
          `"## YYYY-MM-DD — ${t.id} — ${t.kind === "remove" ? "remove" : t.kind === "create" ? "create" : "edit"}" ` +
          `with a Motivation/Change/Eval/Outcome block`,
      );
      continue;
    }

    if (t.kind === "remove") {
      if (!entries.some((e) => e.type === "remove")) {
        errors.push(
          `section "${t.id}" was removed but none of its new docs/guidance-impact.md entries is typed "remove"`,
        );
      }
      continue;
    }

    const rowStatus = t.row.status;
    const sufficient = entries.some((e) => {
      if (!e.evalLine) return false;
      const isNone = /^Eval:\s*none\b/i.test(e.evalLine);
      return !isNone || rowStatus === "gap";
    });
    if (!sufficient) {
      errors.push(
        `section "${t.id}" has a new entry in docs/guidance-impact.md, but its Eval: line is insufficient ` +
          `— "none — no fixture yet" is only legal while the manifest row is "gap" (row "${t.id}" is "${rowStatus}"); ` +
          `add a real result or "exempt (skipped row)"`,
      );
    }
  }
  return errors;
}

// ── Main ─────────────────────────────────────────────────────────────────

function main() {
  const repoRoot = path.resolve(arg("repo-root", path.join(__dirname, "..")));

  const { baseSha, headSha } = readEvent();

  const headManifest = loadManifestAt(repoRoot, headSha, { required: true });
  const baseManifest = loadManifestAt(repoRoot, baseSha, { required: false });

  const headGuidance = collectHeadingsAt(repoRoot, headSha);
  const baseGuidance = collectHeadingsAt(repoRoot, baseSha);

  const touched = computeTouched({ headManifest, baseManifest, headGuidance, baseGuidance });

  if (touched.length === 0) {
    console.log("check-guidance-touch: no guidance section changed between base and head — nothing to require");
    return;
  }

  // Only read/parse docs/guidance-impact.md once something actually needs an
  // entry — a diff that touches zero sections is not required to have a
  // valid impact file (though in practice it always will, since the file is
  // committed).
  const added = addedEntries(repoRoot, baseSha, headSha);
  const errors = checkEntries(touched, added);

  if (errors.length > 0) {
    for (const e of errors) console.error(`check-guidance-touch: ${e}`);
    process.exitCode = 1;
    return;
  }

  console.log(
    `check-guidance-touch: ${touched.length} section(s) touched, all have a sufficient docs/guidance-impact.md entry`,
  );
}

try {
  main();
} catch (e) {
  if (e instanceof RunError) {
    console.error(`check-guidance-touch: ${e.message}`);
    process.exit(2);
  }
  throw e;
}
