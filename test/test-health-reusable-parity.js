// node:test suite for scripts/check-health-reusable-parity.js. Offline: the
// "platform" side is built from this repo's own copy plus the platform-only
// pieces the script deliberately ignores, then mutated one way per test.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const YAML = require("yaml");
const { compare } = require("../scripts/check-health-reusable-parity.js");

const LOCAL = path.join(__dirname, "..", ".github/workflows/scheduled-run-health-reusable.yml");
const local = () => YAML.parse(fs.readFileSync(LOCAL, "utf8"));

function platform() {
  const p = local();
  const inputs = p.on.workflow_call.inputs;
  inputs.platform_repo = { type: "string", default: "" };
  inputs.platform_ref = { type: "string", default: "" };
  const audit = p.jobs.audit.steps.find((s) => s.name === "Audit scheduled-run health");
  audit.run = "# a platform-only comment\n\n" + audit.run;
  return p;
}

const auditStep = (doc) => doc.jobs.audit.steps.find((s) => s.name === "Audit scheduled-run health");

test("the copy matches a platform that differs only in deliberate omissions and comments", () => {
  assert.deepStrictEqual(compare(platform(), local()), []);
});

test("a new platform input is drift", () => {
  const p = platform();
  p.on.workflow_call.inputs.new_lane = { type: "boolean", default: true };
  assert.match(compare(p, local()).join("\n"), /platform input 'new_lane' is missing here/);
});

test("a changed input default is drift", () => {
  const p = platform();
  p.on.workflow_call.inputs.window_hours.default = "72";
  assert.match(compare(p, local()).join("\n"), /input 'window_hours'/);
});

test("an input only the copy has is drift", () => {
  const l = local();
  l.on.workflow_call.inputs.extra = { type: "string", default: "" };
  assert.match(compare(platform(), l).join("\n"), /input 'extra' exists here but not on the platform/);
});

test("a new flag on the audit command is drift", () => {
  const p = platform();
  auditStep(p).run = auditStep(p).run.replace('"${args[@]}"', '"${args[@]}" --new-flag');
  assert.match(compare(p, local()).join("\n"), /step 'Audit scheduled-run health' run: script differs/);
});

test("a new env key on the audit step is drift", () => {
  const p = platform();
  auditStep(p).env.AUDIT_NEW = "${{ inputs.new }}";
  assert.match(compare(p, local()).join("\n"), /step 'Audit scheduled-run health' env 'AUDIT_NEW'/);
});

test("a script the platform fetches and the copy does not is drift", () => {
  const p = platform();
  const fetch = p.jobs.audit.steps.filter((s) => s.with && s.with["sparse-checkout"]).pop();
  fetch.with["sparse-checkout"] += "scripts/new-helper.js\n";
  assert.match(compare(p, local()).join("\n"), /fetches .scripts\/new-helper.js./);
});

test("a permission change is drift", () => {
  const p = platform();
  p.permissions["pull-requests"] = "read";
  assert.match(compare(p, local()).join("\n"), /permissions:/);
});

test("a renamed audit step is reported, not passed", () => {
  const p = platform();
  auditStep(p).name = "Audit it";
  assert.match(compare(p, local()).join("\n"), /step 'Audit scheduled-run health' not found/);
});

test("a change to the currency step is drift", () => {
  const p = platform();
  const step = p.jobs.audit.steps.find((s) => s.name === "Check this caller is on a current platform release");
  step.run = step.run.replace("--behind-days", "--strict --behind-days");
  assert.match(compare(p, local()).join("\n"), /current platform release' run: script differs/);
});
