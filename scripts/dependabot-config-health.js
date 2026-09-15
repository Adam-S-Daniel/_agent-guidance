#!/usr/bin/env node
"use strict";
/*
 * dependabot-config-health.js — a central sweep over every repo in both
 * SYNC_OWNERS that files ONE issue IN the affected repo when its Dependabot
 * config is invalid or silent. See
 * docs/decisions/0014-dependabot-config-health-is-swept-centrally.md for the
 * full decision record this header summarises.
 *
 * THE PROBLEM. claude-memory-map's `.github/dependabot.yml` landed broken
 * (commit e66b678, 2026-08-10T19:36:21Z) and sat that way for 36 days before
 * anyone noticed by accident. Nothing watches per-repo Dependabot config
 * health fleet-wide: `scheduled-run-health` audits *this repo's own* crons,
 * not a repo's Dependabot machinery, and a repo with a broken config emits
 * nothing that any existing sweep reads. GitHub itself KNOWS the config is
 * broken (a `dependabot` check run concludes failure) but tells nobody
 * outside that one repo's own PR/commit UI.
 *
 * THE REMEDY. A daily sweep of every non-fork, non-archived repo under both
 * SYNC_OWNERS, using two independent signals per repo:
 *   1. GitHub's own `.github/dependabot.yml` check run (app slug
 *      `dependabot`) on the commit that brought the config onto the default
 *      branch — INVALID when it concludes failure.
 *   2. Whether an update-job workflow run has fired recently enough given
 *      the config's own shortest schedule interval — SILENT when it has not.
 * A finding files or updates a single `ci`-labelled tracking issue in the
 * AFFECTED repo (never here), keyed on a hidden evidence marker so repeat
 * runs don't spam duplicate comments, and the issue is shut once both
 * signals clear.
 *
 * MEASURED FACTS THE DESIGN RESTS ON:
 *
 *   1. The `dependabot` app's check run (name `.github/dependabot.yml`) lands
 *      on the commit that brought the change onto the default branch. For a
 *      merge-commit PR that is the MERGE commit, not the commit
 *      `GET /repos/{r}/commits?sha=<default>&path=.github/dependabot.yml`
 *      returns. Measured: cms-platform `9e44154` (the path commit) carries no
 *      such check; its PR #369 merge `8ab5799` does. claude-memory-map
 *      `e66b678` is both the path commit AND the PR #16 merge commit (check
 *      = failure). So every candidate sha — the path commit AND the merge
 *      commit of any PR that merged it into the default branch — has to be
 *      checked; checking only one silently misses the other shape.
 *
 *   2. An invalid config that REPLACES a valid one does not stop update jobs.
 *      cms-platform's config was invalid from `149755b` (2026-08-10, check
 *      failure: "The property '#/updates/0/cooldown/semver-major-days' is
 *      not supported for the package ecosystem 'github-actions'.") to
 *      `acc5df3` (2026-08-19, success), and update jobs still ran on
 *      2026-08-11 and 2026-08-18, both `success`. So a recent job is NOT
 *      evidence of a VALID config — the two signals are independent and
 *      BOTH must be checked; a check-run failure has to surface as a finding
 *      even while jobs still look recent.
 *
 *   3. Update jobs are Actions runs with event `dynamic` under a workflow
 *      whose `path` is `dynamic/dependabot/dependabot-updates` (listed by
 *      `GET /repos/{r}/actions/workflows`). A repo whose config never
 *      validated has no such workflow at all (claude-memory-map,
 *      _agent-guidance, fastmail-actions, repo-settings: zero runs) — so
 *      "the workflow doesn't exist" and "the workflow exists but never ran"
 *      are both read as SILENT, not as two different unknowns.
 *
 *   4. Weekly schedules are precise: across 261 successful update jobs on
 *      five public repos (cms-platform 56, adamdaniel.ai 147, jodidaniel.com
 *      40, GHA-bench 13, skills-evals 5; 2026-04-29..2026-09-15) the largest
 *      gap between consecutive jobs is exactly 7.00 days. Cooldown does not
 *      pause jobs (skills-evals, GHA-bench: `default-days: 7`, still every 7
 *      days). SILENCE_SLACK_DAYS below is the margin above the shortest
 *      configured interval before "no job yet" becomes a finding rather than
 *      normal jitter.
 *
 *   5. SchemaStore's dependabot-2.0.json allows `cooldown.semver-major-days`
 *      for every ecosystem, so schema validation would have passed all four
 *      broken configs this sweep exists to catch — which is why pre-merge
 *      schema validation was rejected as an alternative (see the ADR).
 *
 *   6. As first measured, no existing fleet App had the reach this needs:
 *      `agents-md-sync` declared contents:write, pull_requests:write,
 *      metadata:read; `cms-platform-automation` the same plus
 *      workflows:write. Neither could read Checks or Actions runs, or write
 *      Issues, in an arbitrary fleet repo. Rather than provision a second
 *      App and a second private key in this repo, `agents-md-sync` was
 *      WIDENED on 2026-09-15 (Actions: Read, Checks: Read, Issues: Read &
 *      write added; both installations re-accepted) — see README.md and the
 *      ADR for the credential this sweep actually runs under.
 *
 * WHAT THIS SWEEP PROVES, AND WHAT IT CANNOT.
 * Proves: for every repo the two token-backed owner listings return, whether
 * GitHub's own config check run is known to have failed, and whether an
 * update-job run is known to have fired inside its own schedule's threshold
 * — as of the moment this run's API calls answered. An UNKNOWN reason is
 * always more specific than "something went wrong": a 404 that could not be
 * distinguished from absence, a truncated pagination, an unparseable
 * timestamp, are each named.
 * Cannot: (a) that a config which currently reads healthy stayed that way
 * between two runs — this is a poll, not a webhook. (b) that a PRIVATE repo
 * the App is not installed on was even seen — it is genuinely invisible to
 * the owner listing, not merely unreadable once found. A PUBLIC repo the App
 * lacks write access to IS seen (public data, listed regardless of
 * installation) and is only discovered when a write fails (see Consequences
 * in the ADR). (c) that "no config found" (404 on both paths) means Dependabot is
 * genuinely off — a config could exist under a ref this sweep did not probe.
 * (d) anything about a private repo's specifics in this process's own stdout
 * — see logLine and the private-redaction tests; a private finding is
 * counted, never named, in the public log this workflow's Actions run
 * produces.
 *
 * Exit codes:
 *   0 — the sweep ran to completion with no owner-listing failure, no
 *       registry-name mismatch, no write failure, and no assessment left
 *       UNKNOWN. Findings alone are exit 0 — the in-repo issue is the alert,
 *       not this process's own exit code.
 *   1 — at least one owner's `gh repo list` failed (or, with
 *       --require-owner-tokens, a required per-owner token was missing), a
 *       name under `cron_coverage.fleet` was not returned by any owner
 *       listing, a write (create/comment/shut) failed, or at least one
 *       assessed repo could not be fully classified (verdict "unknown").
 *   2 — could not run at all: no owners (env and --owners both empty), an
 *       unknown CLI flag, or (unless --repo was given) a registry that
 *       cannot be parsed or whose `cron_coverage.fleet` is not a non-empty
 *       array of strings.
 *
 * Usage:
 *   SYNC_OWNERS="Adam-S-Daniel jodidaniel" node scripts/dependabot-config-health.js --dry-run
 *   node scripts/dependabot-config-health.js --owners "Adam-S-Daniel jodidaniel" --require-owner-tokens
 *   node scripts/dependabot-config-health.js --repo Adam-S-Daniel/cms-platform --dry-run --reveal-private
 */
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const YAML = require("yaml");

// ── Constants (spec-pinned; do not derive these from anything else) ───────
const CONFIG_PATHS = [".github/dependabot.yml", ".github/dependabot.yaml"];
const UPDATES_WORKFLOW_PATH = "dynamic/dependabot/dependabot-updates";
const CHECK_APP_SLUG = "dependabot";
const INTERVAL_DAYS = {
  daily: 1,
  weekly: 7,
  monthly: 31,
  quarterly: 92,
  semiannually: 183,
  yearly: 366,
};
const SILENCE_SLACK_DAYS = 3;
const MARKER = "<!-- dependabot-config-health -->";
const ISSUE_TITLE = "Dependabot config is invalid or silent (automated sweep)";
const DEFAULT_LABEL = "ci";
const MAX_PAGES = 10; // pagination ceiling for every list* call below

// ── Errors ─────────────────────────────────────────────────────────────────

// Thrown by the gh-backed `api` on any non-zero `gh api`/`gh repo` exit.
// `status` is the HTTP status parsed from stderr, or null when gh printed
// nothing that looked like one (a network failure before a response, say).
// `message` is deliberately the LAST non-empty stderr line only — gh writes
// the raw error BODY to stdout on an HTTP error, and that body can carry a
// repo's private issue text; it must never reach a log line or an ::error::.
class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status === undefined ? null : status;
  }
}

// Never include a response body in a reason string — only the status (if
// known) and the already-body-free ApiError message.
function describeApiError(action, err) {
  const status = err instanceof ApiError && err.status ? ` (HTTP ${err.status})` : "";
  return `${action} failed${status}: ${err.message}`;
}

// ── Tokens ───────────────────────────────────────────────────────────────

function ownerTokenEnvName(owner) {
  return "GH_TOKEN_" + owner.toUpperCase().replace(/[-.]/g, "_");
}

// The env a `gh` call for `owner` should run under: that owner's minted
// token when one is set, otherwise the child inherits `env` unchanged (local
// `gh auth login` covers both owners at once in a developer's shell).
function envForOwner(owner, env) {
  const token = env[ownerTokenEnvName(owner)];
  return token ? { ...env, GH_TOKEN: token } : env;
}

// ── gh plumbing (the only impure layer) ───────────────────────────────────

function execGh(args, env) {
  try {
    return execFileSync("gh", args, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      maxBuffer: 64 * 1024 * 1024,
      env,
    });
  } catch (e) {
    const stderr = (e.stderr || "").toString();
    const lines = stderr.split("\n").map((l) => l.trim()).filter(Boolean);
    const last = lines.length ? lines[lines.length - 1] : "gh printed no error output";
    const m = /HTTP (\d{3})/.exec(stderr);
    throw new ApiError(last, m ? Number(m[1]) : null);
  }
}

// buildApi(env) — the real, gh-backed implementation of every method
// runSweep/assessRepo call through `api`. `env` is captured once (normally
// process.env) and re-derived per owner via envForOwner on every call, so a
// single api object serves every owner in one sweep.
function buildApi(env) {
  const call = (owner, args) => execGh(args, envForOwner(owner, env));
  const getJson = (owner, args) => JSON.parse(call(owner, args));

  return {
    listRepos(owner) {
      const rows = getJson(owner, [
        "repo", "list", owner,
        "--source", "--no-archived", "--limit", "1000",
        "--json", "nameWithOwner,isPrivate,defaultBranchRef",
      ]);
      return rows.map((r) => ({
        repo: r.nameWithOwner,
        private: Boolean(r.isPrivate),
        defaultBranch: (r.defaultBranchRef && r.defaultBranchRef.name) || null,
      }));
    },
    getContent(owner, repo, filePath, ref) {
      return getJson(owner, ["api", `repos/${repo}/contents/${filePath}?ref=${encodeURIComponent(ref)}`]);
    },
    newestPathCommit(owner, repo, branch, filePath) {
      const rows = getJson(owner, [
        "api",
        `repos/${repo}/commits?sha=${encodeURIComponent(branch)}&path=${encodeURIComponent(filePath)}&per_page=1`,
      ]);
      if (!Array.isArray(rows) || !rows.length) return null;
      const c = rows[0];
      return { sha: c.sha, date: c.commit && c.commit.committer && c.commit.committer.date };
    },
    commitPulls(owner, repo, sha) {
      return getJson(owner, ["api", `repos/${repo}/commits/${sha}/pulls`]);
    },
    checkRuns(owner, repo, sha, checkName) {
      return getJson(owner, [
        "api",
        `repos/${repo}/commits/${sha}/check-runs?check_name=${encodeURIComponent(checkName)}&per_page=100`,
      ]);
    },
    workflowsPage(owner, repo, page) {
      return getJson(owner, ["api", `repos/${repo}/actions/workflows?per_page=100&page=${page}`]);
    },
    newestWorkflowRun(owner, repo, workflowId) {
      const data = getJson(owner, ["api", `repos/${repo}/actions/workflows/${workflowId}/runs?per_page=1`]);
      const runs = data && data.workflow_runs;
      return Array.isArray(runs) && runs.length ? runs[0] : null;
    },
    openIssuesPage(owner, repo, label, page) {
      return getJson(owner, [
        "api",
        `repos/${repo}/issues?state=open&labels=${encodeURIComponent(label)}&per_page=100&page=${page}`,
      ]);
    },
    issueCommentsPage(owner, repo, number, page) {
      return getJson(owner, ["api", `repos/${repo}/issues/${number}/comments?per_page=100&page=${page}`]);
    },
    ensureLabel(owner, repo, label) {
      // Swallow every error: the label usually already exists (422), and a
      // label-creation hiccup must never be what blocks filing the issue it
      // is merely decorating.
      try {
        call(owner, [
          "api", `repos/${repo}/labels`,
          "-f", `name=${label}`,
          "-f", "color=d93f0b",
          "-f", "description=Filed by the dependabot-config-health sweep",
        ]);
      } catch {
        // intentionally empty
      }
    },
    createIssue(owner, repo, { title, body, label }) {
      return getJson(owner, [
        "api", `repos/${repo}/issues`,
        "-f", `title=${title}`,
        "-f", `body=${body}`,
        "-f", `labels[]=${label}`,
      ]);
    },
    commentIssue(owner, repo, number, body) {
      return getJson(owner, ["api", `repos/${repo}/issues/${number}/comments`, "-f", `body=${body}`]);
    },
    shutIssue(owner, repo, number, body) {
      call(owner, ["api", `repos/${repo}/issues/${number}/comments`, "-f", `body=${body}`]);
      return call(owner, [
        "api", `repos/${repo}/issues/${number}`,
        "-X", "PATCH",
        "-f", "state=closed",
        "-f", "state_reason=completed",
      ]);
    },
  };
}

// ── Pure helpers (exported; every one is unit-tested directly) ────────────

// parseSchedule(text) — walk `updates[].schedule.interval` in a Dependabot
// config's YAML text. A recognised interval maps to INTERVAL_DAYS; anything
// else (a `cron:`-only entry, a missing interval) is collected in
// `unrecognized` rather than silently ignored, so a caller can explain an
// unknown threshold instead of just reporting one.
function parseSchedule(text) {
  let doc;
  try {
    doc = YAML.parse(text);
  } catch (err) {
    return { shortestDays: null, intervals: [], unrecognized: [], error: err.message };
  }
  const updates = doc && typeof doc === "object" ? doc.updates : undefined;
  if (!Array.isArray(updates)) {
    return { shortestDays: null, intervals: [], unrecognized: [], error: null };
  }
  const intervals = [];
  const unrecognized = [];
  for (const u of updates) {
    const interval = u && u.schedule && u.schedule.interval;
    if (typeof interval === "string" && Object.prototype.hasOwnProperty.call(INTERVAL_DAYS, interval)) {
      intervals.push(interval);
    } else {
      unrecognized.push(interval === undefined || interval === null ? "missing" : String(interval));
    }
  }
  const days = intervals.map((i) => INTERVAL_DAYS[i]);
  return {
    shortestDays: days.length ? Math.min(...days) : null,
    intervals,
    unrecognized,
    error: null,
  };
}

function silenceThresholdDays(shortestDays) {
  return shortestDays === null || shortestDays === undefined ? null : shortestDays + SILENCE_SLACK_DAYS;
}

// candidateShas — the path commit plus the merge commit of every PR that
// actually merged it into the default branch (fact 1). Order preserved,
// de-duplicated, so the same sha (claude-memory-map's shape, where the path
// commit IS the merge commit) is only checked once.
function candidateShas(pathCommitSha, pulls, defaultBranch) {
  const out = [];
  const seen = new Set();
  const add = (sha) => {
    if (sha && !seen.has(sha)) {
      seen.add(sha);
      out.push(sha);
    }
  };
  add(pathCommitSha);
  for (const pr of pulls || []) {
    if (pr && pr.merged_at && pr.base && pr.base.ref === defaultBranch) {
      add(pr.merge_commit_sha);
    }
  }
  return out;
}

// pickDependabotCheck — among runs actually posted by the `dependabot` app
// under this exact config path, the newest by completed_at (falling back to
// started_at for a run GitHub has not yet completed).
function pickDependabotCheck(checkRuns, configPath) {
  const at = (c) => c.completed_at || c.started_at || "";
  let newest = null;
  for (const c of checkRuns || []) {
    if (!c || !c.app || c.app.slug !== CHECK_APP_SLUG || c.name !== configPath) continue;
    if (!newest || at(c) > at(newest)) newest = c;
  }
  return newest;
}

const FAILURE_CONCLUSIONS = new Set(["failure", "action_required", "timed_out", "startup_failure"]);

function checkState(check) {
  if (!check) return "missing";
  if (check.status === "completed") {
    if (check.conclusion === "success") return "success";
    if (FAILURE_CONCLUSIONS.has(check.conclusion)) return "failure";
    return "unknown"; // neutral, cancelled, skipped, stale, ...
  }
  return "unknown"; // queued, in_progress, ...
}

// jobsState — see this file's header, fact 2, for why "a recent job" alone
// is never read as "a valid config": this function knows nothing about the
// check run at all, only about timing. THROWS on a present-but-unparseable
// timestamp deliberately — a malformed date from the API is a defect worth
// surfacing loudly, not a silent "unknown" this function would otherwise
// have to invent a reason string for.
function jobsState({ lastJobAt, configCommitAt, thresholdDays, nowMs }) {
  if (thresholdDays === null || thresholdDays === undefined) return "unknown";
  const parseTs = (label, v) => {
    if (v === null || v === undefined) return null;
    const t = Date.parse(v);
    if (Number.isNaN(t)) {
      throw new Error(`jobsState: ${label} is not a parseable timestamp: ${JSON.stringify(v)}`);
    }
    return t;
  };
  const lastJobMs = parseTs("lastJobAt", lastJobAt);
  const configMs = parseTs("configCommitAt", configCommitAt);
  if (lastJobMs === null && configMs === null) return "unknown";
  const evidence = Math.max(lastJobMs === null ? -Infinity : lastJobMs, configMs === null ? -Infinity : configMs);
  const ageDays = (nowMs - evidence) / 86400000;
  if (ageDays > thresholdDays) return "silent"; // exactly == thresholdDays is NOT silent
  if (lastJobMs !== null && (configMs === null || lastJobMs >= configMs)) return "recent";
  return "pending";
}

// verdictFor — the single place the two signals combine into one verdict.
// "healthy" therefore requires check success AND jobs recent — see the
// precedence chain at the bottom.
function verdictFor({ config, check, jobs }) {
  if (!config) {
    return { verdict: "no-config", findings: [], unknowns: [] };
  }
  const findings = [];
  const unknowns = [];
  if (check.state === "failure") {
    findings.push({ kind: "invalid-config", key: `check:${check.id}` });
  }
  if (jobs.state === "silent") {
    findings.push({ kind: "silent", key: `silent:${jobs.lastJobAt || "never"}` });
  }
  if (check.state === "missing") {
    unknowns.push("GitHub's config check run could not be found on the path commit or its PR merge commit");
  } else if (check.state === "unknown") {
    unknowns.push(check.reason || "the config check run's status/conclusion could not be classified");
  }
  if (jobs.state === "unknown") {
    unknowns.push(jobs.reason || "whether Dependabot update jobs are running could not be determined");
  }
  let verdict;
  if (findings.length) verdict = "finding";
  else if (unknowns.length) verdict = "unknown";
  else if (jobs.state === "pending") verdict = "pending";
  else verdict = "healthy";
  return { verdict, findings, unknowns };
}

// ── Evidence markers ───────────────────────────────────────────────────────

function hiddenEvidenceBlock(keys) {
  return `<!-- evidence: ${keys.join(" ")} -->`;
}

// extractReportedEvidence — every key any hidden `<!-- evidence: ... -->`
// block across `texts` already names. Used against an issue's own body plus
// every comment on it, so a repeat run knows which findings it already told
// the reader about.
function extractReportedEvidence(texts) {
  const keys = new Set();
  const re = /<!--\s*evidence:([^>]*?)-->/g;
  for (const text of texts || []) {
    if (typeof text !== "string") continue;
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text))) {
      for (const k of m[1].trim().split(/\s+/).filter(Boolean)) keys.add(k);
    }
  }
  return keys;
}

// A GitHub check's `output.summary` is repo-author-influenced text (in the
// broad sense: it flows from whatever the Dependabot service or a repo's own
// config produced) and gets quoted verbatim into an issue body next to a
// REAL hidden evidence block. Escaping every literal `<!--` before quoting
// means a summary that happens to contain `<!-- evidence: ... -->` can never
// be mistaken for this sweep's own marker when extractReportedEvidence later
// reads the rendered issue back.
function escapeHiddenMarkers(text) {
  return text.split("<!--").join("&lt;!--");
}

// ── Issue lifecycle ────────────────────────────────────────────────────────

// planIssueAction — NEVER shuts on "unknown" or "pending": both mean this
// sweep could not or should not conclude the repo is healthy, and closing an
// issue on anything short of a positive "healthy"/"no-config" read would be
// the sweep declaring victory on a guess. NEVER acts at all when the
// open-issue lookup itself failed: with no reliable read of whether an issue
// is already open, `create` risks a duplicate and `shut` risks closing one
// that should stay open — the only safe action is none, regardless of
// verdict.
function planIssueAction({ verdict, findings, openIssue, reportedKeys, issueLookupFailed }) {
  if (issueLookupFailed) return { action: "none" };
  if (verdict === "finding") {
    if (!openIssue) return { action: "create" };
    const keys = new Set(reportedKeys || []);
    const fresh = findings.filter((f) => !keys.has(f.key)).map((f) => f.key);
    return fresh.length ? { action: "comment", keys: fresh } : { action: "none" };
  }
  if ((verdict === "healthy" || verdict === "no-config") && openIssue) {
    return { action: "shut" };
  }
  return { action: "none" };
}

function renderInvalidConfigSection(assessment) {
  const c = assessment.check;
  const summary = escapeHiddenMarkers(c.outputSummary || "(no summary provided)");
  const quoted = summary.split("\n").map((l) => `> ${l}`).join("\n");
  return [
    "**Invalid config.** GitHub's own check run on this config concluded failure:",
    "",
    `- Check run: ${c.htmlUrl || "(no URL)"}`,
    `- Commit it ran on: ${c.sha || "(unknown)"}`,
    `- ${c.outputTitle || "(no title)"}`,
    "",
    quoted,
  ];
}

function renderSilentSection(assessment) {
  const j = assessment.jobs;
  const lastLine = j.lastJobAt
    ? `Last update job: ${j.lastJobAt}${j.lastJobUrl ? ` (${j.lastJobUrl})` : ""}`
    : "No update job has ever run.";
  return [
    "**Silent.** No Dependabot update job has run recently enough for this config's own schedule:",
    "",
    lastLine,
    `Threshold: ${j.thresholdDays} day(s), set by the shortest recognised (\`${j.intervalBasis || "unknown"}\`) schedule interval plus a ${SILENCE_SLACK_DAYS}-day slack.`,
    "",
    "Update jobs can keep running on an OLD config even after it is replaced by an invalid one " +
      "(a replacing invalid config does not stop them), so silence and validity are separate " +
      "questions — clearing one does not clear the other.",
  ];
}

const FOOTER = "_Filed by the dependabot-config-health sweep in Adam-S-Daniel/_agent-guidance._";

function renderIssueBody(assessment) {
  const keys = assessment.findings.map((f) => f.key);
  const lines = [MARKER, hiddenEvidenceBlock(keys), "", `Repo: ${assessment.repo}`, ""];
  if (assessment.findings.some((f) => f.kind === "invalid-config")) {
    lines.push(...renderInvalidConfigSection(assessment), "");
  }
  if (assessment.findings.some((f) => f.kind === "silent")) {
    lines.push(...renderSilentSection(assessment), "");
  }
  lines.push(
    "**What to do:** repair the config on the default branch. The sweep comments new evidence " +
      "and shuts this issue once the newest check is success and an update job has run since the " +
      "newest config commit.",
    "",
    FOOTER,
  );
  return lines.join("\n");
}

function renderComment(assessment, freshFindings) {
  const keys = freshFindings.map((f) => f.key);
  const lines = [MARKER, hiddenEvidenceBlock(keys), "", "New evidence since the last comment:", ""];
  if (freshFindings.some((f) => f.kind === "invalid-config")) {
    lines.push(...renderInvalidConfigSection(assessment), "");
  }
  if (freshFindings.some((f) => f.kind === "silent")) {
    lines.push(...renderSilentSection(assessment), "");
  }
  lines.push(FOOTER);
  return lines.join("\n");
}

function renderShutComment() {
  return [
    MARKER,
    "The newest config check run is success and an update job has run since the newest config " +
      "commit — shutting this issue.",
    "",
    FOOTER,
  ].join("\n");
}

// logLine — NEVER includes a check summary, and for a private repo returns
// null (nothing to print) unless the caller explicitly opted into
// --reveal-private. This is the ONLY function output funnels through for
// per-repo lines, which is what keeps a private repo's name and findings out
// of a public Actions log by construction rather than by remembering to
// filter every call site.
function logLine(assessment, { revealPrivate } = {}) {
  if (assessment.private && !revealPrivate) return null;
  const parts = [`${assessment.repo}: ${assessment.verdict}`];
  if (assessment.findings.length) parts.push(`kinds=${assessment.findings.map((f) => f.kind).join(",")}`);
  if (assessment.unknowns.length) parts.push(`unknowns=${assessment.unknowns.join(" | ")}`);
  return parts.join(" ");
}

// ── assessRepo ─────────────────────────────────────────────────────────────

function buildCheckEnvelope(run) {
  const state = checkState(run);
  if (state === "missing") {
    return {
      state, id: null, htmlUrl: null, sha: null,
      outputTitle: null, outputSummary: null, status: null, conclusion: null,
      reason: null,
    };
  }
  return {
    state,
    id: run.id !== undefined && run.id !== null ? run.id : null,
    htmlUrl: run.html_url || null,
    sha: run.head_sha || null,
    outputTitle: (run.output && run.output.title) || null,
    outputSummary: (run.output && run.output.summary) || null,
    status: run.status || null,
    conclusion: run.conclusion || null,
    reason: state === "unknown"
      ? `the config check run's status/conclusion could not be classified (status=${run.status || "unknown"}, conclusion=${run.conclusion || "none"})`
      : null,
  };
}

function intervalBasisText(schedule) {
  if (!schedule || schedule.shortestDays === null || schedule.shortestDays === undefined) return null;
  return schedule.intervals.find((i) => INTERVAL_DAYS[i] === schedule.shortestDays) || null;
}

function describeScheduleUnknown(schedule) {
  if (!schedule) return "the update schedule could not be read";
  if (schedule.error) return `the Dependabot config's schedule could not be parsed as YAML: ${schedule.error}`;
  if (!schedule.intervals.length && !schedule.unrecognized.length) {
    return "the Dependabot config has no updates: entries to read a schedule interval from";
  }
  return `no recognised schedule interval was found among: ${schedule.unrecognized.join(", ") || "(none)"}`;
}

function buildJobsEnvelope({ lastJobAt, lastJobUrl, configCommitAt, thresholdDays, nowMs, intervalBasis, schedule }) {
  let state;
  try {
    state = jobsState({ lastJobAt, configCommitAt, thresholdDays, nowMs });
  } catch (e) {
    // A malformed timestamp from the API is a defect, not a silent skip —
    // but ONE repo's malformed data must not crash the whole sweep, so it
    // becomes this repo's own unknown reason instead of an uncaught throw.
    return { state: "unknown", lastJobAt, lastJobUrl, thresholdDays, intervalBasis, reason: e.message };
  }
  let reason = null;
  if (state === "unknown") {
    reason = thresholdDays === null
      ? describeScheduleUnknown(schedule)
      : "no update job has ever run and the config's own commit date could not be determined";
  }
  return { state, lastJobAt, lastJobUrl, thresholdDays, intervalBasis, reason };
}

function findOpenIssue(owner, repo, api, label) {
  const issues = [];
  for (let page = 1; page <= MAX_PAGES; page++) {
    const items = api.openIssuesPage(owner, repo, label, page) || [];
    issues.push(...items);
    if (items.length < 100) break;
  }
  return issues.find((i) => i && !i.pull_request && typeof i.body === "string" && i.body.includes(MARKER)) || null;
}

function collectCommentTexts(owner, repo, number, api) {
  const texts = [];
  for (let page = 1; page <= MAX_PAGES; page++) {
    const items = api.issueCommentsPage(owner, repo, number, page) || [];
    for (const c of items) if (c && typeof c.body === "string") texts.push(c.body);
    if (items.length < 100) break;
  }
  return texts;
}

// assessRepo — the whole per-repo read pipeline. Every individual read
// failure becomes a specific unknown reason and the assessment continues
// where it still can; only a hard failure that leaves NOTHING to reason
// about (no default branch; the config read itself errored) short-circuits
// straight to a verdict.
function assessRepo(target, api, nowMs, opts) {
  const owner = target.owner;
  const repo = target.repo;
  const isPrivate = Boolean(target.private);
  const defaultBranch = target.defaultBranch;
  const label = (opts && opts.label) || DEFAULT_LABEL;

  if (!defaultBranch) {
    return {
      owner, repo, private: isPrivate,
      verdict: "unknown",
      findings: [],
      unknowns: ["no default branch (empty repository)"],
      issueLookupFailed: false,
      config: null, check: null, jobs: null, openIssue: null,
    };
  }

  const extraUnknowns = [];
  let config = null;
  let configErrorReason = null;

  for (const p of CONFIG_PATHS) {
    let content;
    try {
      content = api.getContent(owner, repo, p, defaultBranch);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) continue; // absent — try the next path
      configErrorReason = describeApiError(`reading ${p}`, e);
      break;
    }
    try {
      const encoding = content && content.encoding === "base64" ? "base64" : "utf8";
      config = { path: p, raw: Buffer.from((content && content.content) || "", encoding).toString("utf8") };
    } catch (e) {
      configErrorReason = `decoding ${p} failed: ${e.message}`;
    }
    break;
  }

  let verdictResult;
  let check = null;
  let jobs = null;

  if (configErrorReason) {
    verdictResult = { verdict: "unknown", findings: [], unknowns: [configErrorReason] };
  } else if (!config) {
    verdictResult = { verdict: "no-config", findings: [], unknowns: [] };
  } else {
    const schedule = parseSchedule(config.raw);
    const thresholdDays = silenceThresholdDays(schedule.shortestDays);
    const intervalBasis = intervalBasisText(schedule);

    let pathCommit = null;
    try {
      pathCommit = api.newestPathCommit(owner, repo, defaultBranch, config.path);
    } catch (e) {
      extraUnknowns.push(describeApiError("finding the newest commit that touched the config", e));
    }

    let pulls = [];
    if (pathCommit) {
      try {
        pulls = api.commitPulls(owner, repo, pathCommit.sha);
      } catch (e) {
        extraUnknowns.push(describeApiError("finding the PR that merged the config commit", e));
      }
    }

    const candidates = candidateShas(pathCommit ? pathCommit.sha : null, pulls, defaultBranch);
    const allRuns = [];
    let checkRunsError = null;
    for (const sha of candidates) {
      try {
        const page = api.checkRuns(owner, repo, sha, config.path);
        allRuns.push(...((page && page.check_runs) || []));
      } catch (e) {
        checkRunsError = describeApiError("reading the config check runs", e);
        break;
      }
    }

    check = checkRunsError
      ? {
          state: "unknown", id: null, htmlUrl: null, sha: null,
          outputTitle: null, outputSummary: null, status: null, conclusion: null,
          reason: checkRunsError,
        }
      : buildCheckEnvelope(pickDependabotCheck(allRuns, config.path));

    const configCommitAt = pathCommit ? pathCommit.date : null;
    let workflows = [];
    let total = null;
    let listError = null;
    for (let page = 1; page <= MAX_PAGES; page++) {
      let data;
      try {
        data = api.workflowsPage(owner, repo, page);
      } catch (e) {
        listError = describeApiError("listing workflows", e);
        break;
      }
      if (data && typeof data.total_count === "number") total = data.total_count;
      const items = (data && data.workflows) || [];
      workflows.push(...items);
      // < 100, not === 0: a page short of a full page is the last real page —
      // fetching one more to confirm emptiness is a wasted call on every repo
      // whose workflow count isn't an exact multiple of 100. total_count vs
      // collected still catches genuine truncation below.
      if (items.length < 100) break;
    }

    if (listError) {
      jobs = { state: "unknown", lastJobAt: null, lastJobUrl: null, thresholdDays, intervalBasis, reason: listError };
    } else if (total !== null && workflows.length < total) {
      // cms-platform#425's lesson: a shortfall against total_count is
      // TRUNCATED, not "the workflow doesn't exist" — treated as unknown
      // rather than silently concluding absence from a partial page.
      jobs = {
        state: "unknown", lastJobAt: null, lastJobUrl: null, thresholdDays, intervalBasis,
        reason: `the workflows listing was truncated (total_count ${total}, collected ${workflows.length}) — the updates workflow could not be confirmed present or absent`,
      };
    } else {
      const updatesWf = workflows.find((w) => w && w.path === UPDATES_WORKFLOW_PATH);
      if (!updatesWf) {
        jobs = buildJobsEnvelope({ lastJobAt: null, lastJobUrl: null, configCommitAt, thresholdDays, nowMs, intervalBasis, schedule });
      } else {
        let run = null;
        let runError = null;
        try {
          run = api.newestWorkflowRun(owner, repo, updatesWf.id);
        } catch (e) {
          runError = describeApiError("reading the newest update-job run", e);
        }
        jobs = runError
          ? { state: "unknown", lastJobAt: null, lastJobUrl: null, thresholdDays, intervalBasis, reason: runError }
          : buildJobsEnvelope({
              lastJobAt: run ? run.created_at : null,
              lastJobUrl: run ? run.html_url : null,
              configCommitAt, thresholdDays, nowMs, intervalBasis, schedule,
            });
      }
    }

    verdictResult = verdictFor({ config, check, jobs });
  }

  const unknowns = [...extraUnknowns, ...verdictResult.unknowns];
  let verdict = verdictResult.verdict;
  if (verdict !== "finding" && unknowns.length) verdict = "unknown";

  // A repo with no config STILL gets this lookup: a stale issue must be shut
  // when the config that triggered it has since been deleted outright.
  let openIssue = null;
  let issueLookupFailed = false;
  try {
    const found = findOpenIssue(owner, repo, api, label);
    if (found) {
      const texts = collectCommentTexts(owner, repo, found.number, api);
      openIssue = { number: found.number, url: found.html_url, reportedKeys: extractReportedEvidence([found.body, ...texts]) };
    }
  } catch (e) {
    // issueLookupFailed, not just an unknown reason: with the lookup itself
    // unusable we cannot tell "no issue is open" from "one already is", and
    // guessing either way risks a duplicate `create` or a wrongly skipped
    // `shut` — planIssueAction refuses to act at all when this is set.
    issueLookupFailed = true;
    unknowns.push(describeApiError("looking up the open tracking issue", e));
    if (verdict !== "finding") verdict = "unknown";
  }

  return {
    owner, repo, private: isPrivate,
    verdict,
    findings: verdictResult.findings,
    unknowns,
    issueLookupFailed,
    config, check, jobs, openIssue,
  };
}

// ── The write side ─────────────────────────────────────────────────────────

function performAction(plan, assessment, api, label) {
  if (plan.action === "create") {
    api.ensureLabel(assessment.owner, assessment.repo, label);
    api.createIssue(assessment.owner, assessment.repo, { title: ISSUE_TITLE, body: renderIssueBody(assessment), label });
  } else if (plan.action === "comment") {
    const fresh = assessment.findings.filter((f) => plan.keys.includes(f.key));
    api.commentIssue(assessment.owner, assessment.repo, assessment.openIssue.number, renderComment(assessment, fresh));
  } else if (plan.action === "shut") {
    api.shutIssue(assessment.owner, assessment.repo, assessment.openIssue.number, renderShutComment());
  }
}

// ── runSweep ────────────────────────────────────────────────────────────────

const VERDICTS = ["finding", "unknown", "pending", "healthy", "no-config"];

function runSweep({
  owners, api, nowMs, dryRun, label, revealPrivate, requireOwnerTokens, env,
  registryFleet, onlyRepos, out,
}) {
  const output = out || console.log;
  const effectiveLabel = label || DEFAULT_LABEL;
  const ownerFailures = [];
  const registryMissing = [];
  const writeFailures = [];
  let allRepos = [];

  for (const owner of owners) {
    if (requireOwnerTokens) {
      const envName = ownerTokenEnvName(owner);
      if (!env[envName]) {
        const msg = `${owner}: missing repository variable APP_CLIENT_ID / secret APP_PRIVATE_KEY (env ${envName} is empty) — the agents-md-sync App must be installed on ${owner} with Actions: Read, Checks: Read and Issues: Read & write`;
        output(`::error::${msg}`);
        ownerFailures.push({ owner, reason: msg });
        continue;
      }
    }
    let repos;
    try {
      repos = api.listRepos(owner);
    } catch (e) {
      const msg = `${owner}: could not list repositories — ${describeApiError("listRepos", e)}`;
      output(`::error::${msg}`);
      ownerFailures.push({ owner, reason: msg });
      continue;
    }
    allRepos.push(...repos.map((r) => ({ ...r, owner })));
  }

  if (onlyRepos && onlyRepos.length) {
    const only = new Set(onlyRepos);
    allRepos = allRepos.filter((r) => only.has(r.repo));
    output(`--repo given: skipping the registry cross-check (limited to ${onlyRepos.join(", ")})`);
  } else if (!ownerFailures.length) {
    const shortNames = new Set(allRepos.map((r) => r.repo.split("/")[1]));
    for (const name of registryFleet || []) {
      if (!shortNames.has(name)) {
        output(`::error::${name} is listed under cron_coverage.fleet but was not returned by any owner listing`);
        registryMissing.push(name);
      }
    }
  }

  allRepos.sort((a, b) => a.repo.localeCompare(b.repo));

  const assessments = [];
  for (const target of allRepos) {
    let assessment;
    try {
      assessment = assessRepo(target, api, nowMs, { label: effectiveLabel });
    } catch (e) {
      assessment = {
        owner: target.owner, repo: target.repo, private: target.private,
        verdict: "unknown", findings: [], unknowns: [`assessment crashed: ${e.message}`],
        issueLookupFailed: false,
        config: null, check: null, jobs: null, openIssue: null,
      };
    }
    assessments.push(assessment);

    const line = logLine(assessment, { revealPrivate });
    if (line) output(line);
    // ANY unknowns red the run and get an ::error:: line, not just a verdict
    // of "unknown" — a finding can carry unknowns too (e.g. a missing check
    // run alongside a silent-jobs finding), and the owner's rule is that
    // combination must red the sweep just as loudly as a pure unknown.
    if (assessment.unknowns.length && !assessment.private) {
      output(`::error title=Dependabot config health::${assessment.repo}: ${assessment.unknowns.join("; ")}`);
    }

    const plan = planIssueAction({
      verdict: assessment.verdict,
      findings: assessment.findings,
      openIssue: assessment.openIssue,
      reportedKeys: (assessment.openIssue && assessment.openIssue.reportedKeys) || new Set(),
      issueLookupFailed: assessment.issueLookupFailed,
    });

    if (dryRun) {
      if (plan.action !== "none") {
        output(assessment.private
          ? `(dry-run) would ${plan.action} on a private repo (name withheld)`
          : `(dry-run) would ${plan.action} on ${assessment.repo}`);
      }
      continue;
    }

    if (plan.action === "none") continue;
    try {
      performAction(plan, assessment, api, effectiveLabel);
    } catch (e) {
      const who = assessment.private ? "a private repo" : assessment.repo;
      const msg = `${who}: ${plan.action} failed — ${describeApiError("write", e)}`;
      output(`::error::${msg}`);
      writeFailures.push({ repo: assessment.private ? null : assessment.repo, reason: msg });
    }
  }

  const counts = Object.fromEntries(VERDICTS.map((v) => [v, 0]));
  let privateCount = 0;
  let privateFindings = 0;
  let privateUnknown = 0;
  for (const a of assessments) {
    counts[a.verdict] = (counts[a.verdict] || 0) + 1;
    if (a.private) {
      privateCount++;
      if (a.verdict === "finding") privateFindings++;
      if (a.verdict === "unknown") privateUnknown++;
    }
  }
  output(`finding: ${counts.finding}, unknown: ${counts.unknown}, pending: ${counts.pending}, healthy: ${counts.healthy}, no-config: ${counts["no-config"]}`);
  output(`${privateCount} private repo(s): ${privateFindings} with findings, ${privateUnknown} unknown (names withheld from this public log)`);

  // ANY assessment carrying unknowns reds the run, whatever its verdict — a
  // finding alongside an unknown (e.g. a missing check run next to a silent
  // jobs finding) must red the sweep exactly as loudly as a pure unknown.
  const exitCode = ownerFailures.length || registryMissing.length || writeFailures.length
    || assessments.some((a) => a.unknowns.length > 0)
    ? 1 : 0;

  return { exitCode, assessments, ownerFailures, registryMissing, writeFailures };
}

// ── CLI ─────────────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const flags = {
    dryRun: false,
    owners: (process.env.SYNC_OWNERS || "").split(/\s+/).filter(Boolean),
    repoList: [],
    registryPath: path.join(__dirname, "..", "repos.yml"),
    label: DEFAULT_LABEL,
    revealPrivate: false,
    requireOwnerTokens: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--dry-run") { flags.dryRun = true; continue; }
    if (a === "--reveal-private") { flags.revealPrivate = true; continue; }
    if (a === "--require-owner-tokens") { flags.requireOwnerTokens = true; continue; }
    if (a === "--owners") { flags.owners = (argv[++i] || "").split(/\s+/).filter(Boolean); continue; }
    if (a === "--repo") { flags.repoList.push(argv[++i]); continue; }
    if (a === "--registry") { flags.registryPath = argv[++i]; continue; }
    if (a === "--label") { flags.label = argv[++i]; continue; }
    console.error(`dependabot-config-health: unknown flag ${a}`);
    process.exit(2);
  }
  return flags;
}

function main() {
  const flags = parseArgs(process.argv.slice(2));

  if (!flags.owners.length) {
    console.error("dependabot-config-health: no owners — set SYNC_OWNERS or pass --owners \"<space separated>\"");
    process.exit(2);
  }

  let registryFleet = [];
  if (!flags.repoList.length) {
    let doc;
    try {
      doc = YAML.parse(fs.readFileSync(flags.registryPath, "utf8"));
    } catch (e) {
      console.error(`dependabot-config-health: cannot read the registry in ${flags.registryPath} (${e.code || e.message})`);
      process.exit(2);
    }
    const fleet = doc && doc.cron_coverage && doc.cron_coverage.fleet;
    if (!Array.isArray(fleet) || !fleet.length || fleet.some((f) => typeof f !== "string" || !f.trim())) {
      console.error(`dependabot-config-health: ${flags.registryPath} cron_coverage.fleet must be a non-empty array of strings`);
      process.exit(2);
    }
    registryFleet = fleet;
  }

  const api = buildApi(process.env);
  const result = runSweep({
    owners: flags.owners,
    api,
    nowMs: Date.now(), // the ONLY clock read in this whole file
    dryRun: flags.dryRun,
    label: flags.label,
    revealPrivate: flags.revealPrivate,
    requireOwnerTokens: flags.requireOwnerTokens,
    env: process.env,
    registryFleet,
    onlyRepos: flags.repoList,
    out: console.log,
  });
  process.exit(result.exitCode);
}

if (require.main === module) {
  main();
}

module.exports = {
  // constants
  CONFIG_PATHS, UPDATES_WORKFLOW_PATH, CHECK_APP_SLUG, INTERVAL_DAYS,
  SILENCE_SLACK_DAYS, MARKER, ISSUE_TITLE, DEFAULT_LABEL,
  // errors
  ApiError,
  // pure helpers
  ownerTokenEnvName,
  parseSchedule,
  silenceThresholdDays,
  candidateShas,
  pickDependabotCheck,
  checkState,
  jobsState,
  verdictFor,
  extractReportedEvidence,
  hiddenEvidenceBlock,
  planIssueAction,
  renderIssueBody,
  renderComment,
  renderShutComment,
  logLine,
  // the assessment + sweep
  assessRepo,
  runSweep,
  // impure api builder, exposed for anything that wants the real thing
  buildApi,
};
