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
 *
 * base.sha ITSELF IS NOT WHAT THIS DIFFS FROM. GitHub sets it to the base
 * branch's TIP at event time, not the commit the PR actually forked from —
 * once the base branch advances with its own guidance edit, a two-dot diff
 * against base.sha makes that sibling edit look like part of THIS PR, and
 * this PR gets told to write an entry for a change its author never made.
 * The fix is `git merge-base base.sha head.sha` (needs the checkout's
 * `fetch-depth: 0` in ci.yml — see that step's own comment) and everything
 * below diffs from THAT commit, never from base.sha directly. A merge-base
 * that cannot be resolved (a sha ci.yml's checkout never fetched) is a named
 * exit-2 error, not a raw `git` spawn failure.
 *
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
 * WHICH ENTRY TYPES SATISFY WHICH TOUCH. A `create` touch needs a `create`
 * entry; an `edit` touch needs an `edit` OR a `rename` entry (a heading
 * reworded alongside a body change is still an edit of the section); a
 * `remove` touch needs a `remove` entry. A `rejected` entry never satisfies
 * any of the three — it records a proposal that did NOT land this way, so it
 * must not stand in for the entry the actually-landed change requires.
 *
 * THE ISSUE'S "unparseable impact file" CASE (exit 2) is implemented here as
 * a manifest missing at BOTH head and the base branch's tip (exit 2, see B1
 * below): markdown-it has no unparseable state — it renders any byte stream
 * to SOME token stream — so there is no parse failure to distinguish from a
 * missing file.
 *
 * B1 — docs/guidance-impact.md (or agents-md/eval-coverage.yml itself)
 * ABSENT AT HEAD is not the same failure as "the author never wrote an
 * entry": a PR forked before either file existed (this repo's own pre-#119
 * history is exactly such a case) has neither on its own branch even though
 * the base branch — which is what will actually receive the merge — already
 * carries both. Treating that as exit 2 ("could not run at all") names the
 * wrong remedy ("must be created") for a file that already exists, just not
 * on this branch yet. So: a manifest missing at head falls back to
 * `pull_request.base.sha` (the base branch's CURRENT tip, distinct from the
 * merge-base) via loadManifestWithFallback — an id is stable across the
 * manifest's own history, so a heading that still resolves at head under a
 * tip row's exact text can be trusted to carry that row's id even though
 * head itself has no manifest file at all. EXACT TEXT ONLY: a tip row whose
 * heading has no exact match at `sha` is a named exit 2 — the message gives
 * the row id, the sha, and the same "merge or rebase onto the base branch"
 * remedy — never an approximate match. Round 3's nearest-heading pass, added
 * so that a rename on a pre-manifest fork would resolve at head too,
 * manufactured a false `remove` on a removal beside a rename, waved a
 * wholesale section rewrite through at exit 0, and crossed two pure renames
 * into two false `edit` demands, so round 4 deleted it rather than tuning it
 * (each measurement is recorded on loadManifestWithFallback itself). The
 * window this fallback serves is closing on its own — branches forked before
 * #121 landed the manifest — and merging the base branch resolves every case
 * the exact pass cannot, by making the manifest exist at both shas. Only
 * when the base tip ALSO has no usable manifest is this the OTHER exit 2,
 * naming "merge or rebase onto the base branch" rather than "create the
 * file". docs/guidance-impact.md
 * absent at head gets the parallel treatment in main() itself: if nothing
 * was touched, absence is moot (exit 0, unchanged); if something was, it is
 * exit 1 (fixable by merging/rebasing and then adding an entry), never exit
 * 2 — the check DID run and DID find something to require.
 *
 * S1 — APPEND-ONLY IS ENFORCED TWO WAYS, independently, because each catches
 * a different failure the other cannot: (1) checkAppendOnly asserts
 * STRUCTURALLY that every dated entry already present at the merge-base
 * still appears, byte-for-byte, as a trailing suffix of head's dated
 * entries (the file's own convention prepends new entries ahead of old
 * ones) — this is what actually catches someone rewording an OLD entry's
 * Eval: line in place instead of appending a new one, regardless of what
 * date they leave on it; a pure date check could never catch this on its
 * own, since a reworded entry can carry any date, including a perfectly
 * plausible recent one. (2) dateIssue separately rejects a NEW entry whose
 * own date is not real (`2026-13-45`), predates docs/guidance-impact.md's
 * own documented inception (`2026-09-04`; a bare cutoff, not a comparison
 * against the merge-base commit's real timestamp — the latter would flag
 * this repo's own long-standing test fixtures, which fix their entry dates
 * rather than the wall clock the suite runs under), or is in the future
 * relative to the head commit's own date (`2099-01-01`). Both are needed:
 * (1) alone would wave through a garbage date on an honestly-appended new
 * entry, and (2) alone would wave through an in-place rewording that keeps
 * a plausible date.
 *
 * Exit codes:
 *   0 — every touched id has a sufficient new entry (including: nothing was
 *       touched at all).
 *   1 — a touched id has no new entry, an insufficient one (an Eval: none
 *       line while its manifest row is not `gap`, an `exempt (skipped row)`
 *       line while it is not `skipped`, or a date that fails dateIssue), one
 *       typed to a kind that does not satisfy the touch (a `rejected` entry
 *       against an edit), a removed id's entry not typed `remove`, an
 *       append-only violation (checkAppendOnly), or docs/guidance-impact.md
 *       missing at head while something was touched (see B1 above). Errors
 *       name the id and the entry format.
 *   2 — could not run at all: no $GITHUB_EVENT_PATH, an unreadable or
 *       unparseable event file, an unresolvable merge-base, a malformed
 *       --repo-root, or agents-md/eval-coverage.yml missing at both head and
 *       the base branch's tip, or a base-tip row that the exact fallback
 *       cannot match to any heading at a sha where the manifest is absent
 *       (see B1 above).
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

// SATISFYING_TYPES — which entry TYPES satisfy which TOUCH kind (see this
// file's header). "remove" is handled on its own branch in checkEntries,
// since it also carries its own Eval: rule; this map only covers create/edit.
const SATISFYING_TYPES = {
  create: ["create"],
  edit: ["edit", "rename"],
};

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
    // The file IS there and DID parse — e.g. a `push` event — so this is not
    // "no event file" (that phrase is reserved for the three cases above,
    // where there truly is nothing usable to read).
    throw new RunError(
      `not a pull_request event: ${eventPath} has no pull_request.base.sha / pull_request.head.sha — this check only runs on pull_request`,
    );
  }
  // A sha that is anything but 40 hex characters gets handed straight to
  // `git` argv elsewhere in this file (merge-base, show) — a value like
  // "--help" is read as a FLAG, not a ref, and git's own usage text ends up
  // inside this script's exit-2 message instead of a message this script
  // actually wrote. Rejected here, once, before either sha reaches git.
  const SHA_RE = /^[0-9a-f]{40}$/;
  if (!SHA_RE.test(baseSha) || !SHA_RE.test(headSha)) {
    throw new RunError(
      `${eventPath} has a pull_request.base.sha/head.sha that is not a 40-character hex commit sha ` +
        `(got base "${baseSha}", head "${headSha}")`,
    );
  }
  return { baseSha, headSha };
}

// gitMergeBase — the commit `base.sha` and `head.sha` actually forked from,
// per B1's header note above. Never a plain string return on failure: an
// unresolvable sha (one ci.yml's `fetch-depth: 0` checkout never fetched) is
// a RunError naming the git command, not a raw ENOENT/"fatal:" spawn crash.
function gitMergeBase(repoRoot, baseSha, headSha) {
  try {
    return execFileSync("git", ["merge-base", baseSha, headSha], {
      cwd: repoRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
  } catch (e) {
    const stderr = (e.stderr || "").toString().trim();
    throw new RunError(
      `git merge-base ${baseSha} ${headSha} failed: ${stderr || e.message} — the checkout may be missing one ` +
        `of these commits (needs fetch-depth: 0)`,
    );
  }
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

// gitCommitDate — <sha>'s author date as YYYY-MM-DD, for S1's date-sanity
// checks (see dateIssue below). String-comparable against an entry's own
// "YYYY-MM-DD" heading date without ever parsing it into a Date object.
function gitCommitDate(repoRoot, sha) {
  try {
    return execFileSync("git", ["show", "-s", "--format=%ad", "--date=format:%Y-%m-%d", sha], {
      cwd: repoRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
  } catch (e) {
    const stderr = (e.stderr || "").toString().trim();
    throw new RunError(`git show -s --format=%ad ${sha} failed: ${stderr || e.message}`);
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

// parseManifestRows — raw YAML text (already read from git) -> id -> row.
// Throws (always — the caller decides whether that is fatal) on invalid YAML
// or a non-list top level; a row with no usable string `id` is silently
// dropped (check-guidance-coverage.js already reports that as a malformed
// row — this file only needs the ones it can look up by id).
function parseManifestRows(raw, sha) {
  let rows;
  try {
    rows = YAML.parse(raw);
  } catch (e) {
    throw new RunError(`agents-md/eval-coverage.yml at ${sha} is not valid YAML: ${e.message}`);
  }
  if (!Array.isArray(rows)) {
    throw new RunError(`agents-md/eval-coverage.yml at ${sha} does not parse to a list`);
  }
  const byId = new Map();
  for (const row of rows) {
    if (row && typeof row === "object" && typeof row.id === "string" && row.id) {
      byId.set(row.id, row);
    }
  }
  return byId;
}

// loadManifestWithFallback — id -> row, at <sha>, with a fallback for B1. A
// PR whose HEAD (or merge-base) predates agents-md/eval-coverage.yml entirely
// (it forked before #119 added the file — this repo's own pre-#119 history is
// exactly such a case) used to make `sha`'s absence unconditionally fatal,
// even though the file lives on the base branch and `pull_request.base.sha`
// (`baseTipSha` here — the base branch's CURRENT tip, NOT the merge-base)
// already has it.
//
// When the manifest exists at `sha`, this is loadManifestAt(sha, {required})
// verbatim — the fallback below never runs. When it does not exist at `sha`,
// fall back to `baseTipSha`'s copy and resolve each of its rows by EXACT
// heading text AND file against the headings actually read at `sha`: an `id`
// is stable across the manifest's own history (this file's top header), so a
// row whose heading still exists verbatim at `sha` can be trusted to carry
// that row's id even though `sha` itself has no manifest file at all.
//
// EXACT ONLY, DELIBERATELY. Round 3 added an approximate second pass — a
// nearest-heading (Levenshtein) match, copied from
// check-guidance-coverage.js's stale-row HINT — so that a rename on top of a
// pre-manifest fork would resolve at head too. It was deleted in round 4,
// after three reviews, because every guess it made was wrong in a way that
// wrote something FALSE into the audit trail, and the measurements are worth
// keeping here:
//   - a genuine removal beside a rename (Beta deleted, Gamma renamed) handed
//     Beta's row the only unclaimed heading left and then had nothing for
//     Gamma's, so the gate demanded `gamma — remove`: a RETIREMENT recorded
//     for a section that was renamed;
//   - a section SPLIT ("## Security" -> "## Secrets" carrying the old body,
//     plus an all-new "## Security and secrets") resolved `security` to
//     "Secrets", compared the old body against itself and exited 0 "nothing
//     to require" — a wholesale rewrite through the gate with no entry;
//   - a pure rename whose new heading was farther, by edit distance, than an
//     unrelated unclaimed heading, and two pure renames the greedy walk
//     CROSSED, each produced a false `edit` demand for a section nobody
//     touched.
// The pass was also order-dependent (rows are walked in manifest order) and
// uncapped (no distance ceiling), so none of that was tunable in the small.
// What it bought was a window that is closing on its own — branches forked
// before #121 landed the manifest on 2026-09-04 — and the remedy for every
// case it could not resolve is cheap, correct and unambiguous: merge the base
// branch, after which the manifest exists at both shas and no fallback runs
// at all. So a tip row the exact pass cannot resolve is a named exit 2, not a
// guess.
//
// CONSEQUENCE, and the point of the whole change: no fallback resolution can
// produce a `remove` remedy any more. A row that resolved at the merge-base
// and does not resolve at head is exit 2, never a retirement inferred from
// the asymmetry. Genuine removals — where the manifest is present at BOTH
// shas and the row is really gone from it — keep computeTouched's own
// `remove` path, untouched.
//
// `required` here means "is `sha` missing the manifest with NO usable
// fallback a fatal error" — mirrored from loadManifestAt's own `required`,
// and it governs only that case: an unresolvable row is exit 2 at either sha,
// because a wrong identity at the merge-base manufactures a false `create`
// exactly as one at head manufactured a false `remove`.
function loadManifestWithFallback(repoRoot, sha, baseTipSha, guidance, { required }) {
  const raw = gitShow(repoRoot, sha, "agents-md/eval-coverage.yml");
  if (raw !== null) {
    try {
      return parseManifestRows(raw, sha);
    } catch (e) {
      if (required) throw e;
      return new Map();
    }
  }

  let tipById = new Map();
  const tipRaw = gitShow(repoRoot, baseTipSha, "agents-md/eval-coverage.yml");
  if (tipRaw !== null) {
    try {
      tipById = parseManifestRows(tipRaw, baseTipSha);
    } catch (e) {
      tipById = new Map();
    }
  }

  if (tipById.size === 0) {
    if (required) {
      throw new RunError(
        `agents-md/eval-coverage.yml does not exist at ${sha} or at the base branch tip (${baseTipSha}) — merge ` +
          `or rebase onto the base branch to pick it up`,
      );
    }
    return new Map();
  }

  const byId = new Map();
  const unresolved = [];
  for (const [id, row] of tipById) {
    const exact = guidance.headings.find((h) => h.file === row.file && h.heading === row.heading);
    if (exact) byId.set(id, row);
    else unresolved.push(id);
  }
  if (unresolved.length > 0) {
    // Named, and naming EVERY unresolvable row rather than the first: which
    // one a reader is shown would otherwise depend on manifest row order,
    // and a fixture with two of them (a removal beside a rename) needs both
    // to see what actually happened. The remedy is the same sentence the
    // no-manifest-anywhere case above uses, deliberately.
    throw new RunError(
      `agents-md/eval-coverage.yml is absent at ${sha} and ${unresolved.length === 1 ? "section" : "sections"} ` +
        `${unresolved.map((id) => `"${id}"`).join(", ")} could not be matched by heading there — merge or rebase ` +
        `onto the base branch to pick up the manifest, then re-run`,
    );
  }
  return byId;
}

// ── docs/guidance-impact.md ─────────────────────────────────────────────

// parseImpactEntries — every `##` heading in the file, each carrying its own
// FULL BODY text (through the line before the next `##` heading, or EOF, per
// scripts/lib/markdown-sections.js's extractHeadings — reused here directly
// for the heading/line boundaries — MINUS a trailing `---` separator between
// entries, see BODY BOUNDARY below) plus the text of its first `- Eval: ...`
// bullet, in document order. Structural throughout: headings and their line
// ranges come from extractHeadings' own markdown-it walk (a fenced example
// under "## Entry format" is a `fence` token, never mistaken for a heading),
// and the Eval bullet comes from a second, parallel pass over the SAME parse
// matching a list item's own inline token content — never a regex over the
// file. S3: the body is what appendOnlyViolation now compares (Motivation,
// Change and Outcome included, not just the Eval line), so an old entry
// reworded anywhere in its body — not only its Eval line — is caught.
//
// BODY BOUNDARY. extractHeadings' endLine runs through the line before the
// NEXT heading, which for every entry but the last also swallows the blank
// lines and `---` thematic break this file's own convention places between
// entries. Left in, that means the exact same entry text gets a DIFFERENT
// computed body depending on whether something happens to follow it before
// the next heading (measured: pasting an existing entry ahead of itself, so
// one copy sits at EOF and the other does not, gave the two byte-for-byte
// copies of the SAME entry two different identities — the copy nearer EOF
// carried no trailing separator, the other did — which broke S2's own
// duplicate-detection on the exact fixture it exists to catch). A trailing
// `hr` (thematic break) token — markdown-it's structural read of a lone
// `---`/`***`/`___` line, never a text scan — truncates the body at ITS OWN
// start line, so an entry's identity no longer depends on what, if anything,
// separates it from its neighbor.
//
// TRAILING, AND ONLY TRAILING (S1, round 4). The first version of this took
// the FIRST hr in the entry's range, which is a different rule wherever an
// entry contains a thematic break of its own: everything below that break —
// an `- Outcome:` line, say — fell outside the computed body and so outside
// appendOnlyViolation's compare entirely, and rewriting it in place came
// back exit 0. So the LAST hr in the range is taken instead, and it
// truncates only when every line after it, up to the next heading, is blank
// — i.e. only when it really is the separator this convention places
// BETWEEN entries. An hr with content under it is part of the entry, and
// truncates nothing.
function parseImpactEntries(src) {
  const lines = src.split("\n");
  const headings = extractHeadings(src, "docs/guidance-impact.md");

  const tokens = md.parse(src, {});
  // [startLine, endLine) of every thematic break, from markdown-it's own
  // token map — the end is what "is everything after it blank" is measured
  // from, so a future multi-line hr token needs no change here.
  const hrRanges = tokens.filter((t) => t.type === "hr").map((t) => [t.map[0], t.map[1]]);
  const evalByIndex = [];
  let current = null;
  let expectHeadingText = false;
  for (const t of tokens) {
    if (t.type === "heading_open" && t.tag === "h2") {
      current = { evalLine: null };
      evalByIndex.push(current);
      expectHeadingText = true;
      continue;
    }
    if (t.type !== "inline") continue;
    if (expectHeadingText) {
      expectHeadingText = false;
      continue;
    }
    if (current && current.evalLine === null && /^Eval:/.test(t.content.trim())) {
      current.evalLine = t.content.trim();
    }
  }

  // Both walks see the identical set of h2 headings, in the identical
  // document order — extractHeadings' `heading_open`/tag=="h2" filter is the
  // one used here too — so index-aligning them is safe.
  return headings.map((h, i) => {
    const inRange = hrRanges
      .filter(([from]) => from > h.startLine && from < h.endLine)
      .sort((a, b) => a[0] - b[0]);
    const lastHr = inRange.length > 0 ? inRange[inRange.length - 1] : undefined;
    const isSeparator =
      lastHr !== undefined && lines.slice(lastHr[1], h.endLine).every((line) => line.trim() === "");
    const bodyEndLine = isSeparator ? lastHr[0] : h.endLine;
    return {
      heading: h.heading,
      evalLine: (evalByIndex[i] && evalByIndex[i].evalLine) || null,
      body: lines.slice(h.startLine + 1, bodyEndLine).join("\n"),
    };
  });
}

// toDatedEntries — filter parseImpactEntries()'s raw headings down to real
// dated entries ("YYYY-MM-DD — <section-id> — <type>"), dropping boilerplate
// like "## Entry format" that is a real h2 but not an entry. `type` is kept
// as-is even when it is not one of ENTRY_TYPES — checkEntries names an
// unrecognized type in its own error; dropping it here instead would make it
// indistinguishable from "no entry at all", losing the specific defect.
function toDatedEntries(rawEntries) {
  const out = [];
  for (const e of rawEntries) {
    if (typeof e.heading !== "string") continue;
    const parts = e.heading.split(" — ").map((s) => s.trim());
    if (parts.length !== 3) continue;
    const [date, id, type] = parts;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) continue;
    out.push({ date, id, type, evalLine: e.evalLine, heading: e.heading, body: e.body });
  }
  return out;
}

// entryIdentity — an entry's full identity for both addedEntries' counting
// and appendOnlyViolation's membership check: heading text plus its FULL
// BODY (S3), not just its Eval: line — two entries can share a heading (a
// section edited twice the same day) while genuinely differing anywhere in
// Motivation/Change/Eval/Outcome, and a body-only comparison would miss a
// reword that leaves the Eval line untouched.
function entryIdentity(e) {
  return `${e.heading} ${e.body}`;
}

// addedEntries — head's dated entries minus base's, by a per-entry COUNT
// difference. docs/guidance-impact.md is append-only (never edited in
// place), so any head occurrence beyond how many identical occurrences
// already existed at base was added by this diff. A plain Set of base
// identities cannot express this: a second same-day entry with the exact
// same heading text as one already at base (a legitimate addition — the same
// section, edited again the same day) has text already "seen", so a
// Set-based filter drops BOTH occurrences instead of just the one that
// already existed.
//
// The count is taken over the full entryIdentity, not heading text alone:
// two entries can share a heading while carrying genuinely different
// bodies, and heading-text-only counting cannot tell WHICH occurrence is the
// new one — a plain array traversal picks one by position, which flips
// depending on where the new entry lands in the file (this repo's own
// convention inserts newest first, i.e. ahead of the existing one). Keying
// on the full identity removes the ambiguity: it still counts by heading
// alone whenever the body is identical (where the choice truly does not
// matter), and disambiguates correctly whenever it differs.
//
// S2: this alone is not sufficient — see dedupedAdded below, which this
// function's own result must always be passed through before being trusted
// as "genuinely new."
function addedEntries(headEntries, baseEntries) {
  const baseCounts = new Map();
  for (const e of baseEntries) baseCounts.set(entryIdentity(e), (baseCounts.get(entryIdentity(e)) || 0) + 1);

  const consumed = new Map();
  const added = [];
  for (const e of headEntries) {
    const key = entryIdentity(e);
    const used = consumed.get(key) || 0;
    consumed.set(key, used + 1);
    if (used < (baseCounts.get(key) || 0)) continue; // matches an occurrence already at base
    added.push(e);
  }
  return added;
}

// dedupedAdded — S2. addedEntries()'s count difference still labels ONE
// occurrence "added" when head has a byte-for-byte duplicate of a base entry
// beside a genuine, unrelated touch (copy-pasting an EXISTING entry rather
// than writing a new one for the section actually edited in this diff) — the
// counting is correct arithmetic, but the specific occurrence it hands back
// as "added" can be exactly that duplicate, which documents nothing new.
// Filtered here by full entryIdentity against EVERY base entry (not just the
// one occurrence addedEntries happened to match against) — an entry that is
// a byte-for-byte copy of something already in docs/guidance-impact.md is
// never a genuine new measurement, whichever of the two identical
// occurrences the count-based diff happened to point at.
function dedupedAdded(added, baseEntries) {
  const baseIdentities = new Set(baseEntries.map(entryIdentity));
  return added.filter((e) => !baseIdentities.has(entryIdentity(e)));
}

// appendOnlyViolation — S1 (and S4's fix to it). docs/guidance-impact.md's
// own rules say entries are append-only: "a wrong entry gets a correcting
// entry, not an edit." addedEntries() alone cannot enforce that — it only
// counts occurrences, so rewording an OLD entry's body in place (heading
// text unchanged) just makes the old occurrence's count drop to zero and the
// reworded text look like a brand-new addition, satisfying the touch as if a
// real new entry had been appended. This checks the file's actual STRUCTURE
// instead: every entry already present at the merge-base must still appear,
// byte-for-byte (heading + FULL BODY, S3 — not just its Eval line),
// SOMEWHERE in head's dated entries.
//
// S4: "somewhere," not "at a fixed trailing position." The round-2 version
// required base's entries to survive as an exact trailing SUFFIX of head's —
// which a legitimate merge can break without anything having been edited: a
// PR appends its own entry ahead of an untouched base entry, main
// independently lands a NEWER-dated entry ahead of that same base entry, and
// merging main in correctly re-sorts the file newest-first, landing main's
// entry ABOVE the PR's own (not in the suffix position the old check
// demanded) while the untouched base entry is still there, unchanged. A
// purely positional check rejected that as "changed in place" while happily
// accepting the SAME three entries in the wrong, undocumented order. Set
// (multiset) membership catches a real in-place edit exactly as well — the
// reworded entry's new identity is simply absent from head's counts, full
// stop — without caring where in the file anything landed. Enforcing the
// newest-first ordering itself, if wanted, belongs in a separate, clearly
// named check — this function's job is only "was anything lost or altered,"
// not "is everything in the right order."
//
// Returns an error string, or null when the invariant holds (including: base
// had no dated entries to protect in the first place).
function appendOnlyViolation(headEntries, baseEntries) {
  if (baseEntries.length === 0) return null;
  if (headEntries.length < baseEntries.length) {
    const lost = baseEntries.length - headEntries.length;
    return (
      `docs/guidance-impact.md has ${lost} fewer dated entr${lost === 1 ? "y" : "ies"} at head than at the ` +
      `merge-base — entries are append-only, never removed or reordered`
    );
  }
  const headCounts = new Map();
  for (const e of headEntries) headCounts.set(entryIdentity(e), (headCounts.get(entryIdentity(e)) || 0) + 1);
  for (const b of baseEntries) {
    const key = entryIdentity(b);
    const remaining = headCounts.get(key) || 0;
    if (remaining <= 0) {
      return (
        `docs/guidance-impact.md's existing entry "${b.heading}" was changed in place — entries are ` +
        `append-only; a correction gets a NEW dated entry, never an edit to an old one`
      );
    }
    headCounts.set(key, remaining - 1); // consume one occurrence — a base entry appearing twice needs two at head
  }
  return null;
}

// IMPACT_FILE_INCEPTION_DATE — docs/guidance-impact.md's own header: "Entries
// before 2026-09-04 predate this file and live only in git history — no
// backfill is planned." A dated entry earlier than this is provably wrong —
// nothing in this file can be from before the file itself existed. A FIXED
// cutoff, deliberately never the merge-base commit's own real timestamp:
// this repo's test fixtures pin their entry dates as literal strings rather
// than the wall clock the suite happens to run under, and a merge-base-
// relative floor would flag every one of them the day after its hardcoded
// date.
const IMPACT_FILE_INCEPTION_DATE = "2026-09-04";

// isRealCalendarDate — "2026-13-45" matches toDatedEntries' own
// `/^\d{4}-\d{2}-\d{2}$/` shape check but is not a real date. Round-tripped
// through Date.UTC rather than reimplementing a days-per-month table, so
// leap years are handled by the platform, not by this file.
function isRealCalendarDate(dateStr) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!m) return false;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  const dt = new Date(Date.UTC(year, month - 1, day));
  return dt.getUTCFullYear() === year && dt.getUTCMonth() === month - 1 && dt.getUTCDate() === day;
}

// dateIssue — why `dateStr` (an entry's own "YYYY-MM-DD") cannot be trusted
// as a genuine new measurement, or null when it is fine. Plain string
// comparison against `headCommitDate` and IMPACT_FILE_INCEPTION_DATE is
// valid here specifically because both sides are "YYYY-MM-DD" — lexical
// order matches chronological order for that one shape.
function dateIssue(dateStr, headCommitDate) {
  if (!isRealCalendarDate(dateStr)) {
    return "is not a real calendar date";
  }
  if (dateStr < IMPACT_FILE_INCEPTION_DATE) {
    return `predates docs/guidance-impact.md itself (entries before ${IMPACT_FILE_INCEPTION_DATE} are not recorded here)`;
  }
  if (dateStr > headCommitDate) {
    return `is in the future relative to this PR's head commit (${headCommitDate})`;
  }
  return null;
}

// ── The join: which ids did this diff touch, and how ────────────────────

// headingKey — a heading's identity for lookup purposes is its FILE plus its
// text, never text alone: two different files can share a heading (e.g.
// "## Security" in both base.md and sections/python.md), and a map keyed by
// text alone collapses them, so a lookup for one id's row can silently
// resolve to the OTHER file's heading object — comparing the wrong file's
// (possibly unchanged) body and missing a real edit entirely.
function headingKey(file, heading) {
  return `${file}|${heading}`;
}

function computeTouched({ headManifest, baseManifest, headGuidance, baseGuidance }) {
  const headHeadingByKey = new Map(headGuidance.headings.map((h) => [headingKey(h.file, h.heading), h]));
  const baseHeadingByKey = new Map(baseGuidance.headings.map((h) => [headingKey(h.file, h.heading), h]));

  const allIds = new Set([...headManifest.keys(), ...baseManifest.keys()]);
  const touched = [];

  for (const id of allIds) {
    const headRow = headManifest.get(id);
    const baseRow = baseManifest.get(id);

    if (headRow && !baseRow) {
      // A brand-new id. Its heading must actually exist at head to count as
      // a real, checkable creation — a row with no matching heading at all
      // is check-guidance-coverage.js's failure, not this one's.
      if (headHeadingByKey.has(headingKey(headRow.file, headRow.heading))) {
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
    const headHeading = headHeadingByKey.get(headingKey(headRow.file, headRow.heading));
    const baseHeading = baseHeadingByKey.get(headingKey(baseRow.file, baseRow.heading));
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

// RESULT_SHAPES_HINT — the three accepted forms, quoted verbatim for error
// messages (N7): a reader told an Eval: line is "insufficient" should not
// have to go find "## Entry format" to learn what would satisfy it.
const RESULT_SHAPES_HINT = '"exit 0" (or "exit code 0"), a score fraction like "7.0/8", or a sample size like "n=3"';

// RESULT_PATTERN — what a real measured result must SHOW, per
// docs/guidance-impact.md's "## Entry format": an exit code, a score
// fraction, or a sample size. Deliberately narrower than "contains a digit"
// (the prior rule): "Eval: TBD (PR #122)" and "Eval: exempt (skipped row)
// since 2026-09-04" both carry a digit while citing no measurement at all —
// the PR number and the date are not results. Never a bag of known-bad
// placeholder words either; this is a positive, structural pattern for what
// a real citation looks like, matching the three example forms the format
// section documents.
//
// N7: two boundary fixes on top of the shapes themselves.
//   - The exit-code alternative also accepts the word "code" between "exit"
//     and the number ("exit code 0"), a phrasing check-guidance-touch's own
//     Eval: writers actually use and the original alternative rejected.
//   - The fraction alternative excludes a `YYYY/MM/DD` (or `YYYY-MM-DD`)
//     date: a lookbehind rejects starting the match right after a 4-digit
//     run followed by "-" or "/" (so "09" in "2026/09/05" is never read as a
//     numerator), and a lookahead rejects a match immediately followed by
//     "-" or "/" then a digit (so "2026/09" is never read as a fraction when
//     a third "/05" segment follows) — together excluding every 2-digit
//     window of a 3-segment date without touching a genuine fraction like
//     "7.0/8", which has no such neighbor on either side.
const RESULT_PATTERN =
  /\bexit(?:\s+code)?\s+\d+\b|(?<!\d{4}[-/])\b\d+(?:\.\d+)?\s*\/\s*\d+\b(?![-/]\d)|\bn\s*=\s*\d+\b/i;

// classifyEval — which of the three documented forms (docs/guidance-impact.md's
// own "## Entry format" section) an Eval: line is, structural on the line's
// own text, never a bag of known-bad placeholder words:
//   "none"    — /^Eval:\s*none\b/i; legal only while the row is "gap".
//   "exempt"  — literally "Eval: exempt (skipped row)"; legal only while the
//               row is "skipped".
//   "result"  — matches RESULT_PATTERN above.
//   "missing" — no Eval: bullet at all.
//   "unrecognized" — none of the above; never sufficient.
function classifyEval(evalLine) {
  if (!evalLine) return "missing";
  if (/^Eval:\s*none\b/i.test(evalLine)) return "none";
  if (/^Eval:\s*exempt\s*\(skipped row\)\s*$/i.test(evalLine)) return "exempt";
  if (RESULT_PATTERN.test(evalLine)) return "result";
  return "unrecognized";
}

function evalLegalForStatus(cls, rowStatus) {
  if (cls === "none") return rowStatus === "gap";
  if (cls === "exempt") return rowStatus === "skipped";
  return cls === "result";
}

function checkEntries(touched, addedByHead, headCommitDate) {
  const byId = new Map();
  for (const e of addedByHead) {
    if (!byId.has(e.id)) byId.set(e.id, []);
    byId.get(e.id).push(e);
  }

  const errors = [];
  for (const t of touched) {
    const rawEntries = byId.get(t.id) || [];
    if (rawEntries.length === 0) {
      errors.push(
        `section "${t.id}" changed but has no new entry in docs/guidance-impact.md — add ` +
          `"## YYYY-MM-DD — ${t.id} — ${t.kind === "remove" ? "remove" : t.kind === "create" ? "create" : "edit"}" ` +
          `with a Motivation/Change/Eval/Outcome block`,
      );
      continue;
    }

    const badType = rawEntries.find((e) => !ENTRY_TYPES.includes(e.type));
    if (badType) {
      errors.push(
        `section "${t.id}" has a new entry in docs/guidance-impact.md with an unrecognized type "${badType.type}" ` +
          `— must be one of: ${ENTRY_TYPES.join(", ")}`,
      );
      continue;
    }

    if (t.kind === "remove") {
      const removeEntries = rawEntries.filter((e) => e.type === "remove");
      if (removeEntries.length === 0) {
        errors.push(
          `section "${t.id}" was removed but none of its new docs/guidance-impact.md entries is typed "remove"`,
        );
        continue;
      }
      if (!removeEntries.some((e) => e.evalLine)) {
        errors.push(
          `section "${t.id}" was removed with a "remove"-typed entry, but it has no "- Eval:" bullet at all — ` +
            `"none — no fixture yet" or "exempt (skipped row)" are both fine here`,
        );
        continue;
      }
      // `classifyEval(null)` is "missing", not "unrecognized" — checked here
      // too (not just the no-bullet-at-all branch above) so a sibling
      // "remove" entry with a real Eval: bullet cannot make a DIFFERENT
      // "remove" entry with NO bullet at all look sufficient by association.
      const removeEvalOk = removeEntries.filter((e) => e.evalLine && classifyEval(e.evalLine) !== "unrecognized");
      if (removeEvalOk.length === 0) {
        errors.push(
          `section "${t.id}" was removed, but its "remove"-typed entry's Eval: line is insufficient — use a ` +
            `real result (${RESULT_SHAPES_HINT}), "none — no fixture yet", or "exempt (skipped row)"`,
        );
        continue;
      }
      // S1: an entry format-legal Eval: line still needs a real, in-range
      // date (dateIssue) — see this file's header. Reported separately from
      // the eval-format message above since it is a different defect.
      if (!removeEvalOk.some((e) => !dateIssue(e.date, headCommitDate))) {
        errors.push(
          `section "${t.id}" was removed, but its "remove"-typed entry's date "${removeEvalOk[0].date}" ` +
            `${dateIssue(removeEvalOk[0].date, headCommitDate)}`,
        );
      }
      continue;
    }

    // create/edit: only entries typed to actually satisfy THIS kind count —
    // a "rejected" entry records a proposal that did not land, so it must
    // not, by itself, satisfy the requirement (see SATISFYING_TYPES above).
    const entries = rawEntries.filter((e) => SATISFYING_TYPES[t.kind].includes(e.type));
    if (entries.length === 0) {
      const typesPresent = [...new Set(rawEntries.map((e) => e.type))];
      const kindArticle = /^[aeiou]/i.test(t.kind) ? "an" : "a";
      errors.push(
        `section "${t.id}" changed but its new entr${rawEntries.length > 1 ? "ies are" : "y is"} typed ` +
          `${typesPresent.map((ty) => `"${ty}"`).join(", ")}, which does not satisfy ${kindArticle} ${t.kind} — ` +
          `use "${t.kind}"${t.kind === "edit" ? ' or "rename"' : ""}`,
      );
      continue;
    }

    if (!entries.some((e) => e.evalLine)) {
      errors.push(
        `section "${t.id}" has a new entry in docs/guidance-impact.md with no "- Eval:" bullet at all — add one ` +
          `(a real result — ${RESULT_SHAPES_HINT} — "none — no fixture yet" while the row is "gap", or "exempt ` +
          `(skipped row)")`,
      );
      continue;
    }

    const rowStatus = t.row.status;
    const evalOk = entries.filter((e) => evalLegalForStatus(classifyEval(e.evalLine), rowStatus));
    if (evalOk.length === 0) {
      errors.push(
        `section "${t.id}" has a new entry in docs/guidance-impact.md, but its Eval: line is insufficient — ` +
          `"none — no fixture yet" is only legal while the manifest row is "gap", "exempt (skipped row)" only ` +
          `while it is "skipped" (row "${t.id}" is "${rowStatus}"); add a real result — ${RESULT_SHAPES_HINT}`,
      );
      continue;
    }
    // S1: an entry whose Eval: line is otherwise legal can still fail on its
    // own date (dateIssue) — reported as its own defect, distinct from the
    // eval-format message above.
    if (!evalOk.some((e) => !dateIssue(e.date, headCommitDate))) {
      errors.push(
        `section "${t.id}" has a new entry in docs/guidance-impact.md, but its date "${evalOk[0].date}" ` +
          `${dateIssue(evalOk[0].date, headCommitDate)} — append-only entries must be dated for real, on or ` +
          `after this PR, never a reworded old entry`,
      );
    }
  }
  return errors;
}

// ── Main ─────────────────────────────────────────────────────────────────

function main() {
  const repoRoot = path.resolve(arg("repo-root", path.join(__dirname, "..")));
  if (!fs.existsSync(repoRoot) || !fs.statSync(repoRoot).isDirectory()) {
    throw new RunError(`--repo-root ${repoRoot} does not exist or is not a directory`);
  }

  const { baseSha, headSha } = readEvent();
  const mergeBaseSha = gitMergeBase(repoRoot, baseSha, headSha);

  // headGuidance/baseGuidance come first: loadManifestWithFallback's B1
  // fallback needs headGuidance/baseGuidance to resolve a base-tip row's
  // heading text against what actually exists at that sha.
  const headGuidance = collectHeadingsAt(repoRoot, headSha);
  const baseGuidance = collectHeadingsAt(repoRoot, mergeBaseSha);

  const headManifest = loadManifestWithFallback(repoRoot, headSha, baseSha, headGuidance, { required: true });
  const baseManifest = loadManifestWithFallback(repoRoot, mergeBaseSha, baseSha, baseGuidance, { required: false });

  const touched = computeTouched({ headManifest, baseManifest, headGuidance, baseGuidance });

  // S3: docs/guidance-impact.md's raw content at both shas, fetched ONCE and
  // reused below — needed even when nothing in agents-md changed, because
  // the impact file's own history can still be vandalized on its own (see
  // the touched.length === 0 branch immediately below).
  const headImpactRaw = gitShow(repoRoot, headSha, "docs/guidance-impact.md");
  const baseImpactRaw = gitShow(repoRoot, mergeBaseSha, "docs/guidance-impact.md");
  const headEntries = headImpactRaw === null ? [] : toDatedEntries(parseImpactEntries(headImpactRaw));
  const baseEntries = baseImpactRaw === null ? [] : toDatedEntries(parseImpactEntries(baseImpactRaw));

  if (touched.length === 0) {
    // S3: nothing in agents-md changed, but a PR can still gut the impact
    // file's own entry history — append-only holds regardless of whether any
    // guidance section moved. Skipped only when the file's raw content is
    // IDENTICAL at both shas (this also covers B1's fork-before-impact-file
    // case, where it is absent at both — there is nothing to lose there).
    if (headImpactRaw !== baseImpactRaw) {
      const appendOnlyError = appendOnlyViolation(headEntries, baseEntries);
      if (appendOnlyError) {
        console.error(`check-guidance-touch: ${appendOnlyError}`);
        process.exitCode = 1;
        return;
      }
    }
    console.log("check-guidance-touch: no guidance section changed between base and head — nothing to require");
    return;
  }

  // B1: docs/guidance-impact.md itself can be missing at head — a PR forked
  // before the file existed (see this file's header). That is "not yet
  // mergeable with the base branch", not "the author never wrote an entry":
  // exit 1 (fixable by merging/rebasing and then adding an entry), never
  // exit 2 ("could not run at all" — the check DID run and DID find
  // something to require).
  if (headImpactRaw === null) {
    for (const t of touched) {
      console.error(
        `check-guidance-touch: section "${t.id}" changed, but docs/guidance-impact.md does not exist yet at ` +
          `head (${headSha}) — it lives on the base branch; merge or rebase onto it, then add an entry for "${t.id}"`,
      );
    }
    process.exitCode = 1;
    return;
  }

  const headCommitDate = gitCommitDate(repoRoot, headSha);

  const added = dedupedAdded(addedEntries(headEntries, baseEntries), baseEntries);
  const errors = checkEntries(touched, added, headCommitDate);

  const appendOnlyError = appendOnlyViolation(headEntries, baseEntries);
  if (appendOnlyError) errors.push(appendOnlyError);

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
