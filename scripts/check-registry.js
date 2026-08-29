#!/usr/bin/env node
/*
 * check-registry.js — the skills-bootstrap allowlist must classify, not just list.
 *
 * THE PROBLEM. `skills_bootstrap.repos` says which repos the sync delivers the
 * hook to. It has never said anything about the repos it does NOT deliver to,
 * and that half is where the decisions live: agentskills authors the hook,
 * skills-evals would be contaminated by it, this repo self-hosts, five repos
 * are dormant. Today those reasons are PROSE — a 43-line comment block under
 * the list (repos.yml, "DELIBERATELY NOT LISTED"). Prose is unreadable to
 * every tool and unfalsifiable by every gate: a name silently present in both
 * halves, or an exclusion whose reason was never written, looks exactly like a
 * settled decision to the next reader and like nothing at all to CI.
 *
 * THE REMEDY. A real `skills_bootstrap.out_of_scope:` key beside `repos:`,
 * each entry a mapping carrying its own `reason:`, and this gate asserting the
 * two keys stay a partition: disjoint, well-formed, and no name written twice.
 *
 * WHAT THIS GATE PROVES, AND WHAT IT CANNOT.
 * Proves: every name under either key is a SHORT repo name; no name is written
 * twice under one key; no name is written under both; and every out_of_scope
 * entry is a mapping that names a repo and gives a non-empty reason for its
 * absence from delivery.
 * Cannot: (a) that a reason is TRUE, or still true — "dormant since 2026-03"
 * is bytes, and nothing offline can age it. A reason is asserted to EXIST, so
 * that dropping a repo from delivery costs a sentence somebody can later
 * disagree with. (b) That the two keys together cover the account. That is a
 * discovery question and needs the network: drift-report.sh enumerates repos
 * nightly and flags the unclassified ones, exactly as it already does for
 * `cron_coverage`. This script keeps no network on purpose, so the two never
 * contend for the same duty. (c) That an allowlisted repo will actually
 * receive the hook — delivery is double-keyed and the second key
 * (a committed `skills.lock`) lives in the consumer repo, off this disk.
 * (d) That the allowlist is non-empty: an empty `repos:` means delivery is
 * off fleet-wide, which is a decision this gate has no standing to overrule,
 * not an internal contradiction. Widen any of this only with a measurement.
 *
 * THE EXIT-2 ARM IS THE POINT OF THE FILE, so read it before shortening it.
 * Every finding below is a RELATION between the two keys, or a defect in an
 * out_of_scope RECORD. With no `out_of_scope:` beside a populated `repos:`
 * there is no second key to contradict and no record to be malformed: the
 * check becomes structurally incapable of finding anything and prints a clean
 * line, forever, for as long as the key stays missing. That is not a passing
 * gate, it is an unwired one — and the two are indistinguishable from the
 * outside, which is how a gate quietly stops being a gate. So a missing or
 * empty complement key REFUSES (exit 2) rather than certifying: deleting the
 * key turns CI red instead of green, which is the only way an operator learns
 * it was deleted. Same "never pass vacuously" rule check-cron-coverage.js
 * applies to an empty `cron_coverage.fleet`, and it is here for the same
 * measured reason: a green gate that examined zero things reads identically to
 * a green gate that examined everything.
 *
 * REAL YAML PARSE (eemeli `yaml`), never a regex or line scanner. This reasons
 * about STRUCTURE — which key an entry sits under, whether an entry is a
 * mapping, which of its fields are populated — and a line scan matches its
 * needle wherever the bytes happen to sit, including inside the very prose
 * comment block this key replaces. Same pinned 2.9.0 the cron gate already
 * uses; no second parser is added. (Recorded here rather than in package.json:
 * JSON has no comments.)
 *
 * OFFLINE and deterministic: reads exactly one file, repos.yml. No network, no
 * `gh`, no account enumeration, no wall-clock, no sleeps.
 *
 * Usage:
 *   node scripts/check-registry.js                     # this repo's repos.yml
 *   node scripts/check-registry.js --repos-yml <file>  # a fixture
 */
const fs = require("node:fs");
const path = require("node:path");
const YAML = require("yaml");

// The block under audit. Named once so every message spells the path the same
// way an operator will have to grep for.
const BLOCK = "skills_bootstrap";
const DELIVER = `${BLOCK}.repos`;
const EXCLUDE = `${BLOCK}.out_of_scope`;

function arg(name, def = "") {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

// Reasons are prose and can run to paragraphs; a status line is one line. Show
// enough to recognise the entry, never enough to wrap a terminal.
function summarize(text, max = 60) {
  const flat = String(text).replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

// Short repo names, held exactly as `exclude:` and `cron_coverage.fleet` hold
// them — never `owner/repo`, never a path. BOTH separators are rejected, not
// just the POSIX one: these names are joined onto a checkout root by the tools
// that consume them, so a name carrying a separator silently addresses a
// different directory instead of failing.
//
// `field` is the label the detail leads with ("entry", "repo:"), NOT the full
// dotted path: the status line already carries the path in its name column,
// and printing it twice pushed the actual complaint off the right of a
// terminal.
function nameFinding(value, field) {
  if (typeof value !== "string" || !value.trim()) {
    return {
      status: "ERROR",
      detail: `${field} is ${JSON.stringify(value)}, not a repo name — nothing was validated`,
    };
  }
  if (/[\\/]/.test(value)) {
    return {
      status: "FAIL",
      detail: `${field} ${JSON.stringify(value)} contains a path separator`
        + " — these are SHORT names, never owner/repo",
    };
  }
  return null;
}

// ---------------------------------------------------------------------------
// Loading. Returns `{ error }` or `{ repos, outOfScope }` and NEVER throws: a
// registry defect has to reach the exit-2 arm as a sentence, not as a stack
// trace that reads like a finding. Every arm here is a refusal, i.e. the
// absence of a verdict — the script is saying it could not answer, which is a
// different thing from answering "clean".
function blockFromRegistry(file) {
  let doc;
  try {
    doc = YAML.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    return { error: `cannot read the registry in ${file} (${err.code || err.message})` };
  }
  const block = doc && typeof doc === "object" ? doc[BLOCK] : undefined;
  if (!block || typeof block !== "object" || Array.isArray(block)) {
    return { error: `${file} has no ${BLOCK}: block — there is nothing to validate` };
  }
  // Shape defects in either key are refusals rather than findings: a key that
  // is a string or a mapping was not "listed wrong", it was written against a
  // different schema than this gate knows, and iterating it would invent
  // results. An ABSENT key degrades to [] here and is judged below, where the
  // two absences mean different things.
  const repos = block.repos === undefined ? [] : block.repos;
  if (!Array.isArray(repos)) {
    return { error: `${file} ${DELIVER} is not a list — nothing was validated` };
  }
  const outOfScope = block.out_of_scope === undefined ? [] : block.out_of_scope;
  if (!Array.isArray(outOfScope)) {
    return { error: `${file} ${EXCLUDE} is not a list — nothing was validated` };
  }
  // THE VACUOUS-PASS GUARD. Do not "simplify" this into a pass; the whole
  // header paragraph above exists because the simplification is the obvious
  // move and it silently unwires the gate. A populated allowlist with no
  // complement key cannot produce a finding of any kind, so certifying it
  // clean would be certifying that the check ran, not that the registry is
  // sound.
  if (repos.length && !outOfScope.length) {
    return {
      error: `${file} has ${DELIVER} but no ${EXCLUDE} entries`
        + " — with no complement key nothing can ever be unclassified,"
        + " so this check would report clean forever; refusing to certify it",
    };
  }
  // Neither key names anything. Same rule, other shape: zero entries examined,
  // zero findings, "all well-formed and disjoint", exit 0 — a green line about
  // a block this process never looked inside.
  if (!repos.length && !outOfScope.length) {
    return {
      error: `${file} ${BLOCK} names no repos under either key`
        + " — nothing would be validated",
    };
  }
  return { repos, outOfScope };
}

// ---------------------------------------------------------------------------
// Validation. Produces one record per entry plus one per cross-key collision,
// in DECLARATION order, so a line here maps onto a line in the file. Statuses
// split the same way check-cron-coverage.js splits them: ERROR is "this entry
// could not be read as a record at all", FAIL is "it was read and it breaks a
// rule". Both are findings and both exit 1; the distinction is for the human
// reading the line, who fixes a shape defect differently from a rule breach.
function validate(repos, outOfScope) {
  const records = [];
  const delivered = new Set();
  const excluded = new Set();

  repos.forEach((entry, i) => {
    const where = `${DELIVER}[${i}]`;
    const bad = nameFinding(entry, "entry");
    if (bad) {
      records.push({ name: where, ...bad });
      return;
    }
    if (delivered.has(entry)) {
      records.push({
        name: entry,
        status: "FAIL",
        detail: `listed more than once under ${DELIVER}`
          + " — a duplicate reads as two decisions and can be half-reverted",
      });
      return;
    }
    delivered.add(entry);
    records.push({ name: entry, status: "OK", detail: `delivered — allowlisted in ${DELIVER}` });
  });

  outOfScope.forEach((entry, i) => {
    const where = `${EXCLUDE}[${i}]`;
    // A bare string here is the shape this key exists to refuse. `repos:` is a
    // plain list because delivery needs no justification; an EXCLUSION does,
    // and a string cannot carry one — which is exactly how the old prose
    // comment block let reasons drift away from the names they described.
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      records.push({
        name: where,
        status: "ERROR",
        detail: `entry is ${Array.isArray(entry) ? "a list" : JSON.stringify(entry)},`
          + " not a mapping with repo: and reason: — nothing was validated",
      });
      return;
    }
    if (!Object.prototype.hasOwnProperty.call(entry, "repo")) {
      records.push({
        name: where,
        status: "ERROR",
        detail: "entry has no repo: key — nothing was validated",
      });
      return;
    }
    const bad = nameFinding(entry.repo, "repo:");
    if (bad) {
      records.push({ name: where, ...bad });
      return;
    }
    const name = entry.repo;
    if (excluded.has(name)) {
      records.push({
        name,
        status: "FAIL",
        detail: `listed more than once under ${EXCLUDE}`
          + " — two reasons for one absence, and nothing says which one holds",
      });
      return;
    }
    excluded.add(name);
    // The reason is the entire point of the key. Missing, non-string, or
    // whitespace-only all fail identically: each leaves an exclusion that
    // looks already-decided and cannot be reviewed, which is the failure this
    // key was added to end.
    const reason = entry.reason;
    if (typeof reason !== "string" || !reason.trim()) {
      records.push({
        name,
        status: "FAIL",
        detail: `${EXCLUDE} entry has no reason:`
          + " — an exclusion with no reason records a decision nobody can review",
      });
      return;
    }
    records.push({ name, status: "OK", detail: `out of scope — ${summarize(reason)}` });
  });

  // Cross-key collisions last, and ALL of them, not just the first. The cron
  // gate reports `both[0]` because there it is a refusal that stops the run;
  // here it is a finding, and reporting one at a time would make fixing a
  // three-name overlap a three-run loop.
  for (const name of delivered) {
    if (excluded.has(name)) {
      records.push({
        name,
        status: "FAIL",
        detail: `listed under BOTH ${DELIVER} and ${EXCLUDE}`
          + " — delivery and its own written reason for no delivery, in one file",
      });
    }
  }
  return records;
}

// ---------------------------------------------------------------------------
// Resolved from this script rather than the cwd, for the same reason the cron
// gate does it: the check must validate the registry its OWN repo ships, not
// whatever repos.yml happens to sit beside the directory someone ran it from.
const reposYml = arg("repos-yml", path.join(__dirname, "..", "repos.yml"));

const { error, repos, outOfScope } = blockFromRegistry(reposYml);
if (error) {
  console.error(`check-registry: ${error}`);
  process.exit(2);
}

let bad = 0;
for (const record of validate(repos, outOfScope)) {
  const line = `${record.status.padEnd(5)} ${record.name}: ${record.detail}`;
  if (record.status === "FAIL" || record.status === "ERROR") {
    console.error(line);
    bad++;
  } else {
    console.log(line);
  }
}
console.log(
  bad
    ? `\n${bad} finding(s) across ${repos.length} allowlisted`
      + ` and ${outOfScope.length} out-of-scope entries`
    : `\n${repos.length} allowlisted and ${outOfScope.length} out-of-scope repos:`
      + " all well-formed, all reasoned, none listed twice or in both keys",
);
process.exit(bad ? 1 : 0);
