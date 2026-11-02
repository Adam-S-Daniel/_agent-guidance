#!/usr/bin/env node
/*
 * check-health-reusable-parity.js — this repo's copy of the scheduled-run
 * health reusable must keep doing what cms-platform's does.
 *
 * WHY THERE ARE TWO. A lockfile repo cannot start cms-platform's
 * scheduled-run-health.yml (a reusable is covered by the lockfile of the repo
 * that defines it, and cms-platform has none: ADR 0024's 2026-10-31
 * addendum). So .github/workflows/scheduled-run-health-reusable.yml here is a
 * copy with its actions locked. Both run the SAME audit script, fetched from
 * cms-platform, so most changes reach both. What can drift is the workflow
 * around it: a new input, a new flag on the audit command, a new script the
 * step needs, a new permission. A drifted copy keeps running green and does
 * less than the platform's, the "silence reads as health" failure the audit
 * exists to close. This check makes that drift go red instead.
 *
 * WHAT IS COMPARED, by a real YAML parse (never a regex):
 *   1. inputs: every input here exists on the platform with the same type and
 *      default, and every platform input exists here unless it is one of the
 *      deliberate omissions below;
 *   2. the two steps that do the work, "Audit scheduled-run health" and
 *      "Check this caller is on a current platform release": the same env
 *      keys and values and the same `run:` script, ignoring comment and blank
 *      lines;
 *   3. the scripts the platform's checkout fetches: every one is fetched here
 *      too;
 *   4. the workflow-level permissions.
 *
 * Deliberate omissions: the `platform_repo` and `platform_ref` inputs, which
 * are deprecated on the platform and select nothing there either. Here the
 * release comes from .github/health-audit-platform-ref.
 *
 * The platform side is cms-platform's LATEST RELEASE, the same thing the copy
 * runs the script from. Run daily by health-reusable-parity.yml, whose own
 * failure the scheduled-run health audit then reports.
 *
 * BUMP MODE (--bump). The copy fetches the scripts at the release named in
 * .github/health-audit-platform-ref. When cms-platform's latest release is
 * newer, --bump points the branch health-audit-ref/update at main plus a
 * one-line change to that file, opens a PR from it if none is open, and turns
 * on auto-merge (merge commit), so the bump lands once the required checks
 * pass. It needs GH_TOKEN to be an App token that may write contents and pull
 * requests here: a GITHUB_TOKEN push would not start the required checks.
 * The bump runs AFTER the parity check in the same job, so a release that
 * changed the workflow is not adopted until this copy is brought back in step.
 *
 * Usage:
 *   node scripts/check-health-reusable-parity.js
 *   node scripts/check-health-reusable-parity.js --bump --repo <owner/repo>
 *   node scripts/check-health-reusable-parity.js --platform-file <path>  # offline
 *   node scripts/check-health-reusable-parity.js --local-file <path>
 */
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const YAML = require("yaml");

const PLATFORM_REPO = "Adam-S-Daniel/cms-platform";
const PLATFORM_PATH = ".github/workflows/scheduled-run-health.yml";
const LOCAL_PATH = path.join(__dirname, "..", ".github/workflows/scheduled-run-health-reusable.yml");
// The steps that do the work, compared by env and script. The steps before
// them only decide which commit to fetch, and differ on purpose.
const COMPARED_STEPS = ["Audit scheduled-run health", "Check this caller is on a current platform release"];
const OMITTED_INPUTS = new Set(["platform_repo", "platform_ref"]);
const OMITTED_SCRIPTS = new Set();

function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : "";
}

function gh(args) {
  return execFileSync("gh", args, { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] }).trim();
}

function platformSource() {
  const file = arg("platform-file");
  if (file) return { label: file, text: fs.readFileSync(file, "utf8") };
  const tag = latestRelease();
  const b64 = gh(["api", `repos/${PLATFORM_REPO}/contents/${PLATFORM_PATH}?ref=${tag}`, "--jq", ".content"]);
  return { label: `${PLATFORM_REPO}@${tag}`, text: Buffer.from(b64, "base64").toString("utf8") };
}

// `on:` is the string key "on" under YAML 1.2 core, boolean true under 1.1.
function inputsOf(doc) {
  const on = doc.on ?? doc.true;
  return (on && on.workflow_call && on.workflow_call.inputs) || {};
}

function steps(doc) {
  const jobs = Object.values(doc.jobs || {});
  return jobs.flatMap((j) => j.steps || []);
}

function stripComments(run) {
  return String(run || "")
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"))
    .join("\n");
}

function fetchedScripts(doc) {
  const out = new Set();
  for (const s of steps(doc)) {
    const sparse = s.with && s.with["sparse-checkout"];
    if (sparse) for (const l of String(sparse).split("\n")) if (l.trim()) out.add(l.trim());
  }
  return out;
}

function compare(platform, local) {
  const problems = [];
  const pIn = inputsOf(platform);
  const lIn = inputsOf(local);
  for (const [name, spec] of Object.entries(lIn)) {
    const p = pIn[name];
    if (!p) problems.push(`input '${name}' exists here but not on the platform`);
    else if (p.type !== spec.type || String(p.default) !== String(spec.default))
      problems.push(`input '${name}': here ${spec.type}=${spec.default}, platform ${p.type}=${p.default}`);
  }
  for (const name of Object.keys(pIn))
    if (!lIn[name] && !OMITTED_INPUTS.has(name))
      problems.push(`platform input '${name}' is missing here`);

  for (const name of COMPARED_STEPS) {
    const pStep = steps(platform).find((s) => s.name === name);
    const lStep = steps(local).find((s) => s.name === name);
    if (!pStep || !lStep) {
      problems.push(`step '${name}' not found (${pStep ? "here" : "on the platform"})`);
      continue;
    }
    const pEnv = pStep.env || {};
    const lEnv = lStep.env || {};
    for (const k of new Set([...Object.keys(pEnv), ...Object.keys(lEnv)]))
      if (String(pEnv[k]) !== String(lEnv[k]))
        problems.push(`step '${name}' env '${k}': here '${lEnv[k]}', platform '${pEnv[k]}'`);
    if (stripComments(pStep.run) !== stripComments(lStep.run))
      problems.push(`step '${name}' run: script differs (comments and blank lines ignored):\n--- platform\n${stripComments(pStep.run)}\n--- here\n${stripComments(lStep.run)}`);
  }

  const lScripts = fetchedScripts(local);
  for (const s of fetchedScripts(platform))
    if (!lScripts.has(s) && !OMITTED_SCRIPTS.has(s))
      problems.push(`the platform fetches '${s}' and this copy does not`);

  const pPerm = JSON.stringify(platform.permissions || {});
  const lPerm = JSON.stringify(local.permissions || {});
  if (pPerm !== lPerm) problems.push(`permissions: here ${lPerm}, platform ${pPerm}`);
  return problems;
}

const PIN_FILE = ".github/health-audit-platform-ref";
const BUMP_BRANCH = "health-audit-ref/update";

function latestRelease() {
  const tag = gh(["api", `repos/${PLATFORM_REPO}/releases/latest`, "--jq", ".tag_name"]);
  if (!/^v\d+\.\d+\.\d+$/.test(tag)) throw new Error(`latest release tag '${tag}' is not vX.Y.Z`);
  return tag;
}

function bump(repo) {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) throw new Error(`--repo '${repo}' is not owner/name`);
  const pinned = fs.readFileSync(path.join(__dirname, "..", PIN_FILE), "utf8").trim();
  const latest = latestRelease();
  if (pinned === latest) {
    console.log(`Pin current: ${PIN_FILE} names ${latest}, cms-platform's latest release.`);
    return;
  }
  console.log(`Pin behind: ${PIN_FILE} names ${pinned}; cms-platform's latest release is ${latest}.`);
  const mainSha = gh(["api", `repos/${repo}/git/ref/heads/main`, "--jq", ".object.sha"]);
  // The branch is this job's own: reset it to main every run, so the PR is
  // always exactly one file on top of the current main.
  let exists = true;
  try {
    gh(["api", `repos/${repo}/git/ref/heads/${BUMP_BRANCH}`, "--jq", ".object.sha"]);
  } catch {
    exists = false;
  }
  if (exists) {
    gh(["api", "-X", "PATCH", `repos/${repo}/git/refs/heads/${BUMP_BRANCH}`, "-f", `sha=${mainSha}`, "-F", "force=true"]);
  } else {
    gh(["api", "-X", "POST", `repos/${repo}/git/refs`, "-f", `ref=refs/heads/${BUMP_BRANCH}`, "-f", `sha=${mainSha}`]);
  }
  const blob = gh(["api", `repos/${repo}/contents/${PIN_FILE}?ref=${BUMP_BRANCH}`, "--jq", ".sha"]);
  gh([
    "api", "-X", "PUT", `repos/${repo}/contents/${PIN_FILE}`,
    "-f", `message=Move the health audit's platform pin to cms-platform ${latest}`,
    "-f", `content=${Buffer.from(latest + "\n").toString("base64")}`,
    "-f", `branch=${BUMP_BRANCH}`,
    "-f", `sha=${blob}`,
  ]);
  const open = gh(["pr", "list", "-R", repo, "--head", BUMP_BRANCH, "--state", "open", "--json", "number", "--jq", ".[0].number // \"\""]);
  let number = open;
  if (!number) {
    const body = [
      `Moves \`${PIN_FILE}\` from ${pinned} to ${latest}, the release whose audit scripts`,
      "`scheduled-run-health-reusable.yml` fetches for every caller.",
      "",
      `Opened by \`scripts/check-health-reusable-parity.js --bump\`, after the daily parity check`,
      `found this repo's copy of the workflow still matches cms-platform's at ${latest}.`,
      "It merges itself once the required checks pass. A push to this branch is overwritten",
      "by the next run.",
    ].join("\n");
    const url = gh([
      "pr", "create", "-R", repo, "--head", BUMP_BRANCH, "--base", "main",
      "--title", `Move the health audit's platform pin to cms-platform ${latest}`,
      "--body", body,
    ]);
    number = url.split("/").pop();
    console.log(`Opened ${url}`);
  } else {
    console.log(`Refreshed the open PR #${number}`);
  }
  gh(["pr", "merge", String(number), "-R", repo, "--auto", "--merge"]);
}

function main() {
  if (process.argv.includes("--bump")) return bump(arg("repo"));
  const src = platformSource();
  const localFile = arg("local-file") || LOCAL_PATH;
  const platform = YAML.parse(src.text);
  const local = YAML.parse(fs.readFileSync(localFile, "utf8"));
  const problems = compare(platform, local);
  if (problems.length) {
    for (const p of problems) console.log(`::error::${p.split("\n")[0]}`);
    console.log(`\n${problems.length} difference(s) between ${path.basename(localFile)} and ${src.label}:\n`);
    for (const p of problems) console.log(`- ${p}`);
    console.log(
      "\nMirror the platform change into .github/workflows/scheduled-run-health-reusable.yml," +
        " or, if the difference is deliberate, add it to the omissions in this script with the reason.",
    );
    process.exit(1);
  }
  console.log(`OK: ${path.basename(localFile)} matches ${src.label}`);
}

if (require.main === module) main();
module.exports = { compare };
