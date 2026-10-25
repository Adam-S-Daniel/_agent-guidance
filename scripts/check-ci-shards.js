#!/usr/bin/env node
/*
 * check-ci-shards.js — the integration suite's CI shards cover every test
 * group exactly once.
 *
 * WHY THIS EXISTS. ci.yml's `suite` job runs test/run-tests.sh as a matrix of
 * shards, each naming its groups in TEST_GROUPS_ONLY, so the suite finishes in
 * the time of its slowest shard instead of all 18 groups queued on one runner.
 * That split has one silent failure mode: a group added to run-tests.sh's
 * TEST_GROUPS but to no shard simply stops running in CI, and every shard
 * stays green, because a shard only ever reports on the groups it was handed.
 * The same is true of a group renamed in one place and not the other — though
 * run-tests.sh refuses an unknown name, so that one is loud — and a group
 * listed in two shards runs twice and doubles its cost without anyone
 * noticing. So the union of the shards' lists must be EXACTLY the canonical
 * list, each group exactly once.
 *
 * THE CANONICAL LIST comes from `test/run-tests.sh --list-groups`, which
 * prints TEST_GROUPS one per line — asked of bash, not parsed out of the
 * script's text, for the same reason check_group_coverage asks `declare -F`:
 * a comment or a string must not be able to satisfy it. TEST_GROUPS_ONLY is
 * removed from that child's environment, because with it set --list-groups
 * prints the SELECTED subset, and a checker run inside a shard would then
 * certify the shard against itself.
 *
 * THE SHARDS are read from ci.yml PARSED as YAML (the `yaml` package), never
 * line-scanned: a group name inside a comment, or under a matrix key nothing
 * reads, is bytes a grep matches and a parser does not. And the lists alone
 * prove nothing unless something consumes them, so the suite job must also
 * hand `${{ matrix.groups }}` to run-tests.sh through a step's `env:` — the
 * only place it may arrive, since a `${{ }}` inside a `run:` block is
 * rendered into the command GitHub echoes.
 *
 * Usage: node scripts/check-ci-shards.js [--workflow <ci.yml>] [--groups <file>]
 *   --workflow  default: .github/workflows/ci.yml beside this script's repo
 *   --groups    a file with the canonical list, one per line (tests use this);
 *               default: run test/run-tests.sh --list-groups
 * Exit 0 when the shards partition the groups; exit 1 with one line per
 * violation; exit 2 on an input it cannot read (a check that cannot find its
 * inputs must not certify).
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const REPO = path.join(__dirname, "..");
const YAML = require(path.join(REPO, "node_modules", "yaml"));

const SUITE_JOB = "suite";

function canonicalFromRunner(repo) {
  const env = { ...process.env };
  delete env.TEST_GROUPS_ONLY;
  delete env.TEST_GROUP;
  const out = execFileSync("bash", [path.join(repo, "test", "run-tests.sh"), "--list-groups"], {
    env,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  return parseList(out);
}

function parseList(text) {
  return text.split("\n").map((l) => l.trim()).filter(Boolean);
}

// check(workflowText, canonical) -> { code, lines }
function check(workflowText, canonical) {
  if (!Array.isArray(canonical) || canonical.length === 0) {
    return { code: 2, lines: ["the canonical group list is empty — nothing to check the shards against"] };
  }
  let doc;
  try {
    doc = YAML.parse(workflowText);
  } catch (e) {
    return { code: 2, lines: [`the workflow does not parse as YAML: ${e.message.split("\n")[0]}`] };
  }
  const job = doc && doc.jobs && doc.jobs[SUITE_JOB];
  if (!job || typeof job !== "object") {
    return { code: 2, lines: [`the workflow has no \`${SUITE_JOB}\` job`] };
  }
  const include = job.strategy && job.strategy.matrix && job.strategy.matrix.include;
  if (!Array.isArray(include) || include.length === 0) {
    return { code: 2, lines: [`jobs.${SUITE_JOB}.strategy.matrix.include is missing or empty`] };
  }

  const lines = [];
  const seen = new Map(); // group -> shard labels
  include.forEach((entry, i) => {
    const label = entry && entry.shard !== undefined ? `shard ${entry.shard}` : `matrix entry ${i + 1}`;
    if (!entry || typeof entry.groups !== "string" || entry.groups.trim() === "") {
      lines.push(`${label} names no groups (a \`groups\` string is required)`);
      return;
    }
    for (const g of entry.groups.trim().split(/\s+/)) {
      if (!seen.has(g)) seen.set(g, []);
      seen.get(g).push(label);
    }
  });

  const known = new Set(canonical);
  for (const g of canonical) {
    if (!seen.has(g)) lines.push(`group ${g} is in no shard — add it to one of jobs.${SUITE_JOB}'s matrix entries, or it never runs in CI`);
  }
  for (const [g, where] of seen) {
    if (!known.has(g)) lines.push(`${where.join(", ")} names ${g}, which is not a test group (run-tests.sh --list-groups)`);
    if (where.length > 1) lines.push(`group ${g} is in more than one shard (${where.join(", ")}) — it would run ${where.length} times`);
  }

  if (!job.strategy || job.strategy["fail-fast"] !== false) {
    lines.push(`jobs.${SUITE_JOB}.strategy.fail-fast must be false — one red shard must not cancel the others' reports`);
  }

  // The lists must actually reach run-tests.sh, through env:, never through
  // an interpolation inside run:.
  const steps = Array.isArray(job.steps) ? job.steps : [];
  const wired = steps.some((s) =>
    s && typeof s.run === "string" && s.run.includes("./test/run-tests.sh") && !/\$\{\{/.test(s.run)
      && s.env && typeof s.env.TEST_GROUPS_ONLY === "string"
      && /^\$\{\{\s*matrix\.groups\s*\}\}$/.test(s.env.TEST_GROUPS_ONLY.trim()));
  if (!wired) {
    lines.push(`no step in jobs.${SUITE_JOB} runs ./test/run-tests.sh with env TEST_GROUPS_ONLY: \${{ matrix.groups }} and no \${{ }} in its run: body`);
  }

  return { code: lines.length ? 1 : 0, lines };
}

module.exports = { check, parseList, canonicalFromRunner };

if (require.main === module) {
  const arg = (name) => {
    const i = process.argv.indexOf(name);
    return i > -1 ? process.argv[i + 1] : undefined;
  };
  const wfPath = path.resolve(arg("--workflow") || path.join(REPO, ".github", "workflows", "ci.yml"));
  let wfText;
  let canonical;
  try {
    wfText = fs.readFileSync(wfPath, "utf8");
  } catch (e) {
    console.error(`::error::cannot read ${wfPath}: ${e.code || e.message}`);
    process.exit(2);
  }
  try {
    const groupsFile = arg("--groups");
    canonical = groupsFile ? parseList(fs.readFileSync(groupsFile, "utf8")) : canonicalFromRunner(REPO);
  } catch (e) {
    console.error(`::error::cannot read the canonical group list: ${(e.message || String(e)).split("\n")[0]}`);
    process.exit(2);
  }
  const { code, lines } = check(wfText, canonical);
  for (const l of lines) console.error(`::error::${l}`);
  if (code === 0) console.log(`ci shards: the ${SUITE_JOB} matrix runs each of the ${canonical.length} test groups exactly once`);
  process.exit(code);
}
