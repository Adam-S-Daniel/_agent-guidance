// Test-only checker for the invented real-work fixture (the kit's DECISIONS
// X9 and C8): it shows where a pull request's own test file sits.
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { add } = require("../index.js");

test("add sums two numbers", () => {
  assert.strictEqual(add(2, 3), 5);
});
