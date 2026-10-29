// @ts-check
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests/e2e",
  use: { baseURL: "http://127.0.0.1:8080" },
  webServer: {
    command: "bash scripts/start-test-server.sh",
    url: "http://127.0.0.1:8080/healthz",
    reuseExistingServer: false,
  },
});
