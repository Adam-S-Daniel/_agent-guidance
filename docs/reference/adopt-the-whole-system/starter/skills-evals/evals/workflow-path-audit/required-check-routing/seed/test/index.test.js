"use strict";

const assert = require("node:assert");
const { test } = require("node:test");
const { greeting } = require("../src/util");

test("root path gets the generic greeting", () => {
  assert.strictEqual(greeting("/"), "hello\n");
});

test("a named path is echoed back", () => {
  assert.strictEqual(greeting("/world"), "hello, world\n");
});
