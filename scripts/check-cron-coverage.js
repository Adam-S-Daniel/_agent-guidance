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
 * Under all of them sits a fourth assertion — that the target is a REAL REPO
 * and not merely a path that exists (see repoIdentityError). A gate may never
 * certify something it did not audit, and "the path resolved" is not "I read a
 * repo": a file, an off-by-one directory, and an empty file merely NAMED `.git`
 * all scored covered, exit 0. The repo is now identified by reading git's
 * on-disk contract, so no name can stand in for one.
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

// The target must be a REAL REPO, and "a repo" has to be decided by CONTENT.
// Three earlier revisions decided it by path SHAPE instead, and each shipped
// the next evasion: `existsSync(dir)` certified four empty FILES named after
// the adopting repos and an off-by-one `--repos-root /home --require user`;
// adding isDirectory() + existsSync(`.git`) closed those, and still certified a
// directory holding one ZERO-BYTE FILE named `.git` — the very shape this
// suite's own fixtures were built from. A blacklist of bad path shapes can
// never enumerate them all, so this identifies the repo POSITIVELY, by reading
// what git actually writes:
//   resolveGitDir   `.git` as a DIRECTORY is the git dir; `.git` as a FILE is a
//                   gitlink, so READ the `gitdir:` pointer instead of merely
//                   noticing the file. That is what keeps the linked-worktree
//                   and submodule cases — both write `.git` as a file, and the
//                   worktree case is why existence-testing was chosen at all —
//                   without accepting a file whose content says nothing.
//   looksLikeGitDir git's own is_git_directory(): objects/ + refs/, which a
//                   linked worktree borrows from its parent via commondir, AND
//                   a HEAD that VALIDATES as a ref. Accepting a HEAD that
//                   merely EXISTS would be the same name-for-a-repo mistake one
//                   directory further down.
// Measured 2026-08-17 against `git rev-parse --git-dir` — git's own answer — on
// 31 shapes: the 14 repos on this disk, 2 linked worktrees, a real submodule
// (relative pointer), `git init`, a --no-checkout clone, an empty `.git` file,
// an empty `.git` dir, a hand-built objects+refs+HEAD shape, a garbage HEAD, a
// dangling `.git` symlink, a file target, /home, /home/user, /tmp. 31/31
// agreement, zero disagreements; the name-only predicate accepted three shapes
// git itself calls "not a git repository". Requiring `.github/workflows` was
// rejected as the marker for a different reason: it would conflate the two
// answers this function exists to keep apart — 3 of the 14 repos here are real
// repos with no workflows dir, and test case 7 exists precisely so they SKIP.
// Pure fs, never a `git` subprocess: the gate must not need git on PATH.
function resolveGitDir(dir) {
  const marker = path.join(dir, ".git");
  const st = fs.statSync(marker, { throwIfNoEntry: false });
  if (!st) return null;
  if (st.isDirectory()) return marker;
  if (!st.isFile()) return null;
  // A gitlink: `gitdir: <path>`, resolved against the containing directory
  // because git writes it relative for submodules and absolute for worktrees.
  const m = /^gitdir:[ \t]*(.+?)[ \t]*$/m.exec(fs.readFileSync(marker, "utf8"));
  return m ? path.resolve(dir, m[1]) : null;
}

function looksLikeGitDir(gitDir) {
  const has = (n) => fs.existsSync(path.join(gitDir, n));
  if (!((has("objects") && has("refs")) || has("commondir"))) return false;
  let head;
  try {
    head = fs.readFileSync(path.join(gitDir, "HEAD"), "utf8").trim();
  } catch {
    return false;
  }
  return /^ref:[ \t]*refs\//.test(head) || /^[0-9a-f]{40}$|^[0-9a-f]{64}$/.test(head);
}

function repoIdentityError(dir) {
  const st = fs.statSync(dir, { throwIfNoEntry: false });
  // undefined also covers a dangling symlink, which stats ENOENT.
  if (!st) return `no such directory ${dir} — nothing was audited`;
  if (!st.isDirectory()) return `${dir} is not a directory — nothing was audited`;
  let gitDir = null;
  // An unreadable marker is not a repo this gate can vouch for either, and it
  // must not exit on a stack trace that reads like an uncovered repo — the
  // same reason the YAML parse below is wrapped.
  try {
    gitDir = resolveGitDir(dir);
  } catch {
    gitDir = null;
  }
  if (!gitDir || !looksLikeGitDir(gitDir)) {
    return `${dir} is not a repository (no readable git dir) — nothing was audited`;
  }
  return null;
}

// Sorts every caller into one of three buckets, so the verdict is a property
// of the SET and not of which filename happens to sort last:
//   watcher    — has its own on.schedule: AND the scopes: it really does watch;
//   problems   — scheduled but underpermissioned: a nightly 403, i.e. exactly
//                the silently-failing cron this gate exists to prevent, so it
//                fails the repo even alongside a working watcher;
//   unfireable — no on.schedule: at all. A workflow_dispatch-only manual
//                caller is LEGITIMATE — it only runs when a human runs it, so
//                it is not a silent failure — and is therefore not a problem;
//                it just cannot be the thing that covers the repo.
// Callers arrive in sorted-file order, so `problems[0]`/`unfireable[0]` and the
// chosen watcher are all deterministic.
function classifyCallers(callers) {
  let watcher = null;
  const problems = [];
  const unfireable = [];
  for (const caller of callers) {
    if (!hasSchedule(onBlock(caller.doc))) {
      unfireable.push(`${caller.file} has no on.schedule: — the audit can never fire`);
      continue;
    }
    const missing = missingScopes(effectivePermissions(caller.doc, caller.spec));
    if (missing.length) {
      problems.push(
        `${caller.file} job '${caller.job}' lacks permissions ${missing.join(" + ")}`
          + " — the audit would 403 nightly",
      );
      continue;
    }
    if (!watcher) watcher = caller;
  }
  return { watcher, problems, unfireable };
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
  const identity = repoIdentityError(dir);
  if (identity) {
    return { name, status: "ERROR", detail: identity };
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
  const callers = [];
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
        // EVERY caller is collected, not just the last by filename. Assigning
        // a single `caller` here made the verdict depend on sort order, and
        // the comment that justified leaving it there was wrong: it called
        // red-flagging a legitimate workflow_dispatch-only manual caller a
        // hypothetical cost of tightening, when the shipped code already did
        // exactly that whenever the manual caller sorted last. Measured
        // 2026-08-17 — one dispatch-only caller plus one good caller scored
        // FAIL as `zzz-manual-health.yml` and OK as `aaa-manual-health.yml`,
        // same repo, same coverage. See classifyCallers for the rule that
        // replaced it.
        callers.push({ file, job, doc, spec });
      }
    }
  }
  if (scheduled === 0) {
    return { name, status: "SKIP", detail: "no scheduled workflows — nothing to watch" };
  }
  if (!callers.length) {
    return {
      name,
      status: "FAIL",
      detail: `${scheduled} scheduled workflow(s), no ${REUSABLE} caller`,
    };
  }
  const { watcher, problems, unfireable } = classifyCallers(callers);
  // A scheduled caller that would 403 is itself a new silently-failing cron,
  // so it fails the repo even when a second caller does watch the crons.
  if (problems.length) {
    return { name, status: "FAIL", detail: problems[0] };
  }
  // Nothing broken, but nothing that can fire either: every caller is manual.
  if (!watcher) {
    return { name, status: "FAIL", detail: unfireable[0] };
  }
  return {
    name,
    status: "OK",
    detail: `${scheduled} scheduled workflow(s) watched by ${watcher.file}`,
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
