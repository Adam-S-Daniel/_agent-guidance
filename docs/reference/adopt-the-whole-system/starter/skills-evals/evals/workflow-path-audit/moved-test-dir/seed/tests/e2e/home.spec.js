// @ts-check
const { test, expect } = require("@playwright/test");

test("the root path answers with the generic greeting", async ({ request }) => {
  const response = await request.get("/");
  expect(response.status()).toBe(200);
  expect(await response.text()).toBe("hello\n");
});

test("a named path is echoed back", async ({ request }) => {
  const response = await request.get("/world");
  expect(await response.text()).toBe("hello, world\n");
});
