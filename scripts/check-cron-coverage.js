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
 * Under all of them sits a fourth assertion — that the thing audited really is
 * THIS repo, fully checked out (repoIdentity + checkoutError). A gate may never
 * certify something it did not read, and five revisions of that assertion each
 * shipped the next evasion, because each described a SHAPE the target had to
 * have rather than a FACT it had to prove. Scored covered, exit 0, in turn: a
 * file; an off-by-one directory; an empty file merely NAMED `.git`; a git dir
 * with a zero-byte HEAD; a `gitdir:` pointer BORROWED from another real repo;
 * and — with nothing hand-written at all — an ordinary sparse clone of this
 * very repo, whose tree simply lacked the workflows its own index lists.
 *
 * WHAT THIS GATE NOW PROVES, AND WHAT IT STILL CANNOT.
 * Proves: the directory audited is a real git repository that claims this
 * directory back, its index was read, and every workflow file that index tracks
 * was present on disk and parsed. A verdict is therefore about files this
 * process actually opened, not about a path that merely resolved.
 * Cannot: (a) that the checked-out ref is the one you meant — HEAD is never
 * compared to a branch, a remote or anything else, so a complete checkout of a
 * stale or unrelated commit audits clean (measured: a detached old commit,
 * exit 0). (b) That the repo is the one the NAME says. `name` is the path's
 * basename and there is no canonical repo identity on disk to check it
 * against, so this one is reachably GREEN, not merely mislabelled: measured
 * 2026-08-17, a symlink `_agent-guidance -> <a different, covered repo>`
 * prints `OK _agent-guidance … All audited repos covered`, exit 0. A
 * `fleet:`/`--require` list is trusted to name the directories you meant.
 * (c) That untracked or ignored workflow files on disk belong there — reading
 * more than git tracks can only make a verdict louder, so they are allowed
 * through.
 * (d) That a repo cannot claim a directory it does not own: `core.worktree`
 * lets a git dir nominate any path as its checkout, and this gate agrees with
 * git about that on purpose — writing that config means owning the repo, and
 * the materialized-files check still has to pass afterwards. (e) Anything at
 * all about a repo whose git dir or index it cannot read: those are REFUSED,
 * never skipped — a loud stop, which is the absence of a verdict rather than a
 * guarantee. (f) That the DECLARED fleet is the whole account. A disk-root run
 * now fails on a repo repos.yml names and the disk lacks — that gap was issue
 * #37 and is closed — but a repo created in the account and never added to
 * `cron_coverage.fleet` is invisible here, because nothing offline can know
 * about it. drift-report.sh discovers and flags exactly that; this script
 * deliberately keeps no network. Widen any of this only with a measurement.
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
 *   node scripts/check-cron-coverage.js --repos-root /home/user
 *                                                        # every repo repos.yml
 *                                                        # names under
 *                                                        # cron_coverage.fleet
 *   node scripts/check-cron-coverage.js --repos-root /home/user \
 *     --require _agent-guidance,skills-evals             # an explicit subset
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

// A gitlink is a POINTER, and following one proves a repository exists
// SOMEWHERE — never that THIS directory is it. Measured 2026-08-17: four
// directories, each holding the single line `gitdir: /home/user/_agent-guidance
// /.git` and audited under a DIFFERENT --require name, all scored "SKIP …
// nothing to watch" and "All audited repos covered", exit 0. One real repo's
// git dir vouched for four fakes — round 1's four-empty-files evasion
// reconstituted at content level, and the answer to "can this audit ever report
// a different repo than --require named": yes.
// So assert the RETURN LEG, which git writes itself rather than this gate
// inventing a shape: a linked worktree's git dir holds a `gitdir` file naming
// this `.git`, and a submodule's config holds `core.worktree` naming this
// directory (both measured on real ones). When `.git` IS the git dir there is
// nothing to borrow and the check is met by construction, which is every
// ordinary clone.
function samePath(a, b) {
  if (path.resolve(a) === path.resolve(b)) return true;
  // A repo reached through a symlinked path is still that repo, so fall back to
  // the resolved form rather than failing a legitimate layout on spelling.
  const real = (p) => {
    try {
      return fs.realpathSync(p);
    } catch {
      return null;
    }
  };
  const ra = real(a);
  return ra !== null && ra === real(b);
}

// git config is INI-shaped, so track the current section rather than scanning
// the file for a bare `worktree` key: that name under any other section means
// something else entirely.
function coreWorktree(gitDir) {
  let text;
  try {
    text = fs.readFileSync(path.join(gitDir, "config"), "utf8");
  } catch {
    return null;
  }
  let section = "";
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    const head = /^\[[ \t]*([A-Za-z0-9.-]+)/.exec(line);
    if (head) {
      section = head[1].toLowerCase();
      continue;
    }
    const kv = /^worktree[ \t]*=[ \t]*(.*)$/i.exec(line);
    if (section === "core" && kv) return kv[1].trim().replace(/^"(.*)"$/, "$1");
  }
  return null;
}

function gitDirBelongsTo(dir, gitDir) {
  const marker = path.join(dir, ".git");
  if (samePath(gitDir, marker)) return true;
  let back = null;
  try {
    back = fs.readFileSync(path.join(gitDir, "gitdir"), "utf8").trim();
  } catch {
    back = null;
  }
  if (back && samePath(path.resolve(gitDir, back), marker)) return true;
  const wt = coreWorktree(gitDir);
  return Boolean(wt) && samePath(path.resolve(gitDir, wt), dir);
}

function repoIdentity(dir) {
  const st = fs.statSync(dir, { throwIfNoEntry: false });
  // undefined also covers a dangling symlink, which stats ENOENT.
  if (!st) return { error: `no such directory ${dir} — nothing was audited` };
  if (!st.isDirectory()) return { error: `${dir} is not a directory — nothing was audited` };
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
    return { error: `${dir} is not a repository (no readable git dir) — nothing was audited` };
  }
  if (!gitDirBelongsTo(dir, gitDir)) {
    return {
      error: `${dir} is not this repository (its git dir ${gitDir} does not point back`
        + ") — nothing was audited",
    };
  }
  return { gitDir };
}

// ---------------------------------------------------------------------------
// Was the repo actually CHECKED OUT? Identity settles "this directory is that
// repository"; it says nothing about whether the files are here. Measured
// 2026-08-17 on two clones of THIS repo at the SAME commit, no hand-written
// file anywhere:
//   git clone --no-checkout + sparse-checkout set scripts  ->  SKIP,  exit 0
//   git clone                                              ->  FAIL,  exit 1
// and `git worktree add --no-checkout` yields a directory whose only entry is a
// 61-byte `.git`, where the ARGUMENT-LESS cwd form ci.yml actually runs said
// "no .github/workflows — nothing to watch", exit 0. `git rev-parse --git-dir`
// blesses all three. The verdict flipped red -> green purely on which files were
// materialized: the same "I did not actually look" failure the gate exists to
// prevent, one level below a fabricated path.
//
// git already records the answer, so read it instead of inferring one. The
// index lists every path tracked at the checked-out ref, which separates three
// states a bare `existsSync` collapses into one:
//   index absent or unreadable — the checkout was never verified. ERROR, loud;
//     this is the --no-checkout worktree, which has no index at all.
//   index names a workflow the tree lacks — the checkout omitted it. ERROR.
//     Catches the missing DIRECTORY and the subtler missing FILE alike: a
//     --no-cone sparse checkout that materialized .github/workflows/ci.yml but
//     not nightly.yml scored "SKIP … no scheduled workflows", exit 0.
//   index names nothing under .github/workflows/ — genuinely nothing to watch,
//     so the SKIP stands. Measured on all 14 repos on this disk the index and
//     the tree agree 14/14, so the three that legitimately SKIP (rss-inator,
//     SonosAmpJuicePi, agentskills-private) still do — the exact constraint
//     that ruled out using `.github/workflows` as the repo marker in the first
//     place.
// An UNTRACKED workflow file is not an error: reading more than git tracks can
// only make a verdict louder, never green.
const WORKFLOW_PREFIX = ".github/workflows/";

// Decodes gitformat-index far enough to list the tracked paths. A substring
// scan for ".github/workflows/" over the raw file is the obvious shortcut and
// is WRONG: index version 4 prefix-compresses each path against the previous
// one, so a v4 index of a repo that tracks .github/workflows/n.yml contains no
// such substring — measured 2026-08-17 — and paired with a sparse checkout that
// shortcut waves the evasion straight through, failing OPEN. Versions 2, 3 and
// 4 are parsed; any other version throws and the caller fails loud. Object ids
// are 20 bytes under sha1 and 32 under sha256 and the header does not say
// which, so both are tried and whichever consumes the entries cleanly wins.
// Validated 2026-08-17 against `git ls-files` over 23 indexes — the 14 repos
// here, a v3 sparse clone, two v4 indexes, a real submodule, a linked worktree,
// two empty ones, and a 22,073-entry index with non-ASCII paths: identical
// output, zero disagreements.
function readVarint(buf, off) {
  if (off >= buf.length) return null;
  let c = buf[off++];
  let val = c & 0x7f;
  while (c & 0x80) {
    if (off >= buf.length) return null;
    val += 1;
    c = buf[off++];
    val = (val << 7) + (c & 0x7f);
  }
  return [val, off];
}

function readIndexEntries(buf, version, count, idLen) {
  const paths = [];
  let off = 12;
  let prev = "";
  for (let i = 0; i < count; i++) {
    const start = off;
    // ctime+mtime+dev+ino+mode+uid+gid+size = 40 bytes, then the object id,
    // then the 2-byte flags whose 0x4000 bit adds 2 more in v3+.
    off += 40 + idLen + 2;
    if (off > buf.length) return null;
    if (version >= 3 && buf.readUInt16BE(off - 2) & 0x4000) off += 2;
    let name;
    if (version < 4) {
      const nul = buf.indexOf(0, off);
      if (nul === -1) return null;
      name = buf.toString("utf8", off, nul);
      // 1-8 NUL bytes pad the record to a multiple of 8.
      off = start + (((nul - start) + 8) & ~7);
    } else {
      const v = readVarint(buf, off);
      if (!v) return null;
      const [strip, after] = v;
      if (strip > prev.length) return null;
      const nul = buf.indexOf(0, after);
      if (nul === -1) return null;
      name = prev.slice(0, prev.length - strip) + buf.toString("utf8", after, nul);
      off = nul + 1;
    }
    if (off > buf.length || !name) return null;
    paths.push(name);
    prev = name;
  }
  // What follows the entries is the trailing checksum, optionally preceded by
  // extensions whose 4-byte signature is alphabetic. Anything else means the
  // entries were misaligned, so this id length was the wrong guess.
  const left = buf.length - off;
  if (left < idLen) return null;
  if (left !== idLen && !/^[A-Za-z]{4}$/.test(buf.toString("latin1", off, off + 4))) return null;
  return paths;
}

function parseIndexPaths(buf) {
  if (buf.length < 12 || buf.toString("latin1", 0, 4) !== "DIRC") {
    throw new Error("not a git index");
  }
  const version = buf.readUInt32BE(4);
  if (version < 2 || version > 4) throw new Error(`unsupported index version ${version}`);
  const count = buf.readUInt32BE(8);
  for (const idLen of [20, 32]) {
    const paths = readIndexEntries(buf, version, count, idLen);
    if (paths) return paths;
  }
  throw new Error("entries did not parse");
}

function checkoutError(dir, gitDir) {
  let tracked;
  try {
    tracked = parseIndexPaths(fs.readFileSync(path.join(gitDir, "index")));
  } catch (err) {
    return `${dir} has no readable git index (${err.code || err.message}) — the`
      + " checkout was never verified, so nothing was audited";
  }
  const want = tracked.filter((p) => p.startsWith(WORKFLOW_PREFIX));
  const missing = want.filter((p) => !fs.existsSync(path.join(dir, p)));
  if (missing.length) {
    return `${dir} tracks ${want.length} workflow file(s) but this checkout did not`
      + ` materialize ${missing.length} (e.g. ${missing[0]}) — nothing was audited`;
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
  const { error, gitDir } = repoIdentity(dir);
  if (error) {
    return { name, status: "ERROR", detail: error };
  }
  // …and a real repo whose workflows were never checked out is not one this
  // gate may certify either: see checkoutError.
  const incomplete = checkoutError(dir, gitDir);
  if (incomplete) {
    return { name, status: "ERROR", detail: incomplete };
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

// ---------------------------------------------------------------------------
// WHICH repos a disk-root audit is answering about. Everything above decides
// whether a given directory is covered; this decides which directories had to
// be there in the first place, and it is the difference between a verdict and
// an anecdote. Measured 2026-08-17 (issue #37): a session holding 14 of the
// account's 25 repos audited its 14, found three uncovered, and printed
// nothing whatsoever about the 11 it never saw — because a disk-scoped audit
// has no way to miss a repo that is not on the disk. Its clean line and a
// genuinely complete one are the same bytes.
//
// So the fleet is DECLARED, in repos.yml, and a name that does not resolve to
// a readable repo is an ERROR by the same rule as everywhere else in this
// file: absence is never a pass. `--require` still overrides, for auditing a
// subset on purpose; what it can no longer do is be the only way to name
// anything, which is what left the default silent.
//
// The list is the one hand-maintained repo inventory in this repo — sync.sh
// and drift-report.sh both discover instead — so it can go stale. It cannot go
// stale QUIETLY: drift-report.sh discovers every repo nightly and flags any it
// reaches that neither `fleet:` nor `out_of_scope:` classifies. That split of
// duties is deliberate and is why this script keeps its no-network property;
// see docs/decisions/0003-cron-coverage-is-fleet-listed.md.
function fleetFromRegistry(file) {
  let doc;
  try {
    doc = YAML.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    return { error: `cannot read the fleet list in ${file} (${err.code || err.message})` };
  }
  const scope = doc && typeof doc === "object" ? doc.cron_coverage : undefined;
  if (!scope || typeof scope !== "object" || Array.isArray(scope)) {
    return { error: `${file} has no cron_coverage: block — there is no fleet to audit` };
  }
  const fleet = scope.fleet;
  // An EMPTY list is the vacuous-pass shape this whole file is built against:
  // zero repos audited, zero failures, "All audited repos covered", exit 0.
  if (!Array.isArray(fleet) || !fleet.length) {
    return { error: `${file} lists no cron_coverage.fleet repos — nothing would be audited` };
  }
  const bad = fleet.find((r) => typeof r !== "string" || !r.trim() || /[\\/]/.test(r));
  if (bad !== undefined) {
    return { error: `${file} cron_coverage.fleet entry ${JSON.stringify(bad)} is not a repo name` };
  }
  // A name under both keys is a contradiction, and the two keys are read by
  // different tools (this one reads `fleet:`, drift-report.sh reads both), so
  // an overlap would make them disagree about the same repo rather than fail.
  const out = Array.isArray(scope.out_of_scope) ? scope.out_of_scope : [];
  const both = fleet.filter((r) => out.includes(r));
  if (both.length) {
    return { error: `${file} lists ${both[0]} as both cron_coverage.fleet and out_of_scope` };
  }
  return { fleet };
}

const reposRoot = arg("repos-root");
const required = arg("require").split(",").map((s) => s.trim()).filter(Boolean);
// Resolved from this script rather than the cwd: the gate must audit the fleet
// its OWN repo declares, not whatever repos.yml happens to sit beside the
// directory someone ran it from.
const reposYml = arg("repos-yml", path.join(__dirname, "..", "repos.yml"));
let targets;
if (reposRoot) {
  let names = required;
  if (!names.length) {
    const { error, fleet } = fleetFromRegistry(reposYml);
    if (error) {
      console.error(`check-cron-coverage: ${error}`);
      process.exit(2);
    }
    names = fleet;
  }
  targets = names.map((repo) => path.join(reposRoot, repo));
} else if (required.length) {
  console.error("check-cron-coverage: --require needs --repos-root to resolve names against");
  process.exit(2);
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
