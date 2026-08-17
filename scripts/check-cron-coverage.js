#!/usr/bin/env node
/*
 * check-cron-coverage.js — a repo that runs crons must actually watch them.
 *
 * THE PROBLEM. A scheduled workflow fails SILENTLY: `event=schedule` has no PR
 * to go red on and notifies nobody, so a failure is invisible outside the
 * Actions tab. Measured in THIS repo: drift-report.yml failed 26 consecutive
 * nights (runs 147-172, 2026-07-22 -> 2026-08-16) with nobody told.
 *
 * THE REMEDY. cms-platform's `scheduled-run-health` reusable: a daily audit of
 * the caller repo's own scheduled runs that opens/updates/closes one
 * `ci`-labelled tracking issue. This gate asserts a repo that HAS crons calls
 * it — and, crucially, that the caller it has can actually do its job.
 *
 * PRESENCE IS NOT COVERAGE, so three things are asserted, not one:
 *   1. some job `uses:` the scheduled-run-health reusable;
 *   2. the caller workflow itself has an `on.schedule:` trigger — a caller
 *      that can never fire watches nothing;
 *   3. its effective permissions grant `actions: read` + `issues: write`. Per
 *      the reusable's own header, a caller's grant CAPS a reusable's, so
 *      without those the audit 403s and fails loud EVERY night — a brand new
 *      silently-failing cron rather than a fix.
 * (2) and (3) are not hypothetical: the first draft of this gate checked only
 * (1), and fixtures with a scheduleless caller and with a caller missing
 * `issues: write` both scored "OK". test/run-tests.sh pins all three.
 *
 * REAL YAML PARSE (eemeli `yaml`), never a regex or line scanner. This reasons
 * about workflow STRUCTURE — which triggers exist, which job calls what, which
 * permissions block applies — and GitHub has allowed anchors/aliases in
 * workflows since 2025-09-18, which a line scanner silently mis-reads.
 * That dep is pinned exactly, and to the same 2.9.0 cms-platform's e2e lint
 * suite already resolves, so the two trees cannot disagree about what a
 * workflow means. (Recorded here rather than in package.json: JSON has no
 * comments, and the rationale crammed into `description` was a 432-col line.)
 *
 * Deterministic: pure filesystem. No network, no sleeps, no wall-clock.
 *
 * Usage:
 *   node scripts/check-cron-coverage.js                  # audit the cwd repo
 *   node scripts/check-cron-coverage.js --repo <dir>     # audit one repo
 *   node scripts/check-cron-coverage.js --repos-root /home/user \
 *     --require _agent-guidance,skills-evals             # local multi-repo
 *
 * The cwd default is the load-bearing one: a CI runner has exactly ONE repo
 * checked out, so a gate keyed to sibling clones on a developer's disk could
 * never run in CI and would rot while looking like a guarantee.
 */
const fs = require("node:fs");
const path = require("node:path");
const YAML = require("yaml");

// Matched against a job's `uses:` value, so it catches both a cross-repo
// `owner/repo/.github/workflows/scheduled-run-health.yml@tag` and a local
// `./.github/workflows/self-scheduled-run-health.yml` self-call.
const REUSABLE = "scheduled-run-health";

// The scopes the reusable declares it needs. A caller grant that omits either
// is the 403-every-night failure mode described above.
const NEEDED = { actions: "read", issues: "write" };

function arg(name, def = "") {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

// `on:` parses to the string key "on" under YAML 1.2 core (what `yaml` uses);
// a YAML 1.1 parser folds the same token to boolean true. Accept both so a
// trigger block can never be missed because of spec version.
function onBlock(doc) {
  if (!doc || typeof doc !== "object") return undefined;
  for (const key of ["on", "true"]) {
    if (Object.prototype.hasOwnProperty.call(doc, key)) return doc[key];
  }
  return undefined;
}

function hasSchedule(on) {
  if (!on || typeof on !== "object" || Array.isArray(on)) return false;
  return Array.isArray(on.schedule) && on.schedule.length > 0;
}

// GitHub semantics: a job-level `permissions:` REPLACES the workflow-level map
// for that job — the two are not merged — and the scalar forms are
// all-or-nothing. Anything else (absent, `read-all`, `{}`) leaves the audit
// without the scopes it needs, whatever the repo's default token happens to
// grant today.
function effectivePermissions(doc, job) {
  const perms = job && job.permissions !== undefined ? job.permissions : doc.permissions;
  if (perms === "write-all") return { ...NEEDED };
  if (!perms || typeof perms !== "object" || Array.isArray(perms)) return {};
  return perms;
}

function missingScopes(perms) {
  return Object.entries(NEEDED)
    .filter(([scope, level]) => perms[scope] !== level)
    .map(([scope, level]) => `${scope}: ${level}`);
}

function auditRepo(dir) {
  const name = path.basename(path.resolve(dir));
  // "A real repo with nothing to audit" and "you pointed me at nothing" are
  // different answers, and only the first is a pass. Without this the audit
  // scores a typo'd or mislocated path as covered: `--repos-root D:\repos`
  // (the Windows layout this fleet's own AGENTS.md documents) run from a
  // POSIX shell resolves to nothing and printed "All audited repos covered",
  // exit 0 — a green gate that audited zero repos. Same "never pass
  // vacuously" rule the suite applies to a missing node_modules/yaml.
  if (!fs.existsSync(dir)) {
    return { name, status: "ERROR", detail: `no such directory ${dir} — nothing was audited` };
  }
  const wfDir = path.join(dir, ".github", "workflows");
  // No workflows at all means no crons, and a repo cannot fail to watch what
  // it does not have. This is a SKIP, not a FAIL: the first draft FAILed here
  // and so reddened rss-inator, a repo with nothing to audit.
  if (!fs.existsSync(wfDir)) {
    return { name, status: "SKIP", detail: "no .github/workflows — nothing to watch" };
  }
  const files = fs.readdirSync(wfDir).filter((f) => /\.ya?ml$/.test(f)).sort();
  let scheduled = 0;
  let caller = null;
  for (const file of files) {
    let doc;
    try {
      doc = YAML.parse(fs.readFileSync(path.join(wfDir, file), "utf8"));
    } catch (err) {
      // A parse failure is its OWN outcome. Left uncaught it exits 1 with a
      // YAMLParseError stack trace that reads exactly like an uncovered repo,
      // so a broken workflow and a missing audit become indistinguishable.
      return { name, status: "ERROR", detail: `${file} is unparseable YAML: ${err.message}` };
    }
    if (!doc || typeof doc !== "object") continue;
    if (hasSchedule(onBlock(doc))) scheduled++;
    for (const [job, spec] of Object.entries(doc.jobs || {})) {
      if (spec && typeof spec.uses === "string" && spec.uses.includes(REUSABLE)) {
        // ORDER DEPENDENCE, known and bounded. `files` is sorted, so with two
        // callers the last one by filename wins — a broken caller can be
        // masked by a good one that sorts after it. Deterministic, never
        // flaky. Deliberately NOT tightened to "every caller must be valid":
        // that would red-flag a legitimate workflow_dispatch-only manual
        // caller, which has no schedule by design, only runs when a human
        // runs it, and so is not a silently-failing cron at all.
        // Measured 2026-08-17 across every repo on disk: the only three
        // callers that exist (adamdaniel.ai, jodidaniel.com, cms-platform)
        // are one apiece, so nothing is masked today in either direction.
        caller = { file, job, doc, spec };
      }
    }
  }
  if (scheduled === 0) {
    return { name, status: "SKIP", detail: "no scheduled workflows — nothing to watch" };
  }
  if (!caller) {
    return {
      name,
      status: "FAIL",
      detail: `${scheduled} scheduled workflow(s), no ${REUSABLE} caller`,
    };
  }
  if (!hasSchedule(onBlock(caller.doc))) {
    return {
      name,
      status: "FAIL",
      detail: `${caller.file} has no on.schedule: — the audit can never fire`,
    };
  }
  const missing = missingScopes(effectivePermissions(caller.doc, caller.spec));
  if (missing.length) {
    return {
      name,
      status: "FAIL",
      detail: `${caller.file} job '${caller.job}' lacks permissions ${missing.join(" + ")}`
        + " — the audit would 403 nightly",
    };
  }
  return {
    name,
    status: "OK",
    detail: `${scheduled} scheduled workflow(s) watched by ${caller.file}`,
  };
}

const reposRoot = arg("repos-root");
const required = arg("require").split(",").map((s) => s.trim()).filter(Boolean);
let targets;
if (reposRoot || required.length) {
  if (!reposRoot || !required.length) {
    console.error("check-cron-coverage: --repos-root and --require must be given together");
    process.exit(2);
  }
  targets = required.map((repo) => path.join(reposRoot, repo));
} else {
  targets = [path.resolve(arg("repo", process.cwd()))];
}

let bad = 0;
for (const dir of targets) {
  const result = auditRepo(dir);
  const line = `${result.status.padEnd(5)} ${result.name}: ${result.detail}`;
  if (result.status === "FAIL" || result.status === "ERROR") {
    console.error(line);
    bad++;
  } else {
    console.log(line);
  }
}
console.log(bad ? `\n${bad} repo(s) uncovered or unparseable` : "\nAll audited repos covered");
process.exit(bad ? 1 : 0);
