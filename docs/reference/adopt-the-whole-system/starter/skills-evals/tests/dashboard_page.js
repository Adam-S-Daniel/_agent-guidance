// dashboard_page.js <dashboard dir> <data dir>: load dashboard/index.html in
// headless Chromium and print what it rendered, as JSON on stdout.
// No server and no network: every request to the page's made-up origin is
// answered from disk, and any other request is aborted and recorded.
// Used by tests/test_dashboard_page.py.
"use strict";
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const ORIGIN = "http://dashboard.invalid";
const [pageDir, dataDir] = process.argv.slice(2);
const TYPES = { ".html": "text/html", ".json": "application/json" };

function inside(dir, rel) {
  const p = path.resolve(dir, rel);
  return p === dir || p.startsWith(dir + path.sep) ? p : null;
}

(async () => {
  const browser = await chromium.launch();
  const requests = [];
  const external = [];
  const errors = [];
  try {
    const page = await browser.newPage();
    page.on("pageerror", (e) => errors.push(String(e.message)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    await page.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== ORIGIN) { external.push(url.href); return route.abort(); }
      const rel = decodeURIComponent(url.pathname).replace(/^\/+/, "");
      requests.push(rel);
      const file = rel.startsWith("data/")
        ? inside(path.resolve(dataDir), rel.slice(5))
        : inside(path.resolve(pageDir), rel || "index.html");
      if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
        return route.fulfill({ status: 404, body: "not found" });
      }
      return route.fulfill({ status: 200, body: fs.readFileSync(file),
        contentType: TYPES[path.extname(file)] || "application/octet-stream" });
    });
    await page.goto(ORIGIN + "/index.html");
    await page.waitForSelector("body[data-ready=true]", { timeout: 10000 });
    const dom = await page.evaluate(() => {
      const text = (e) => (e ? e.textContent.trim() : null);
      const status = (id) => {
        const s = document.querySelector("#" + id + " [data-status]");
        return { text: text(s), failed: s.classList.contains("failed"), hidden: s.hidden };
      };
      return {
        meta: text(document.getElementById("meta")),
        overall: Object.fromEntries([...document.querySelectorAll("[data-overall]")]
          .map((t) => [t.dataset.overall, text(t.querySelector(".value"))])),
        runs: [...document.querySelectorAll("#runs-body tr")].map((tr) => ({
          fixture: tr.dataset.fixture,
          model: tr.dataset.model,
          text: text(tr),
          advantage: tr.querySelector("[data-advantage]").dataset.advantage,
          arms: Object.fromEntries([...tr.querySelectorAll("td[data-arm]")].map((td) =>
            [td.dataset.arm, { pass_rate: td.dataset.passRate === undefined ? null : Number(td.dataset.passRate),
                               text: text(td) }])),
        })),
        weeks: [...document.querySelectorAll("#allowance-body tr")].map((tr) => ({
          week: tr.dataset.week, used: Number(tr.querySelector("[data-used]").dataset.used), text: text(tr),
          over: !!tr.querySelector(".bar.over"),
        })),
        classes: Object.fromEntries([...document.querySelectorAll("#gate-classes [data-class]")]
          .map((c) => [c.dataset.class, text(c)])),
        skills: [...document.querySelectorAll("#gates-body tr")].map((tr) => ({
          skill: tr.dataset.skill, verdict: tr.querySelector("[data-verdict]").dataset.verdict, text: text(tr),
        })),
        status: { runs: status("runs"), allowance: status("allowance"), gates: status("gates") },
      };
    });
    process.stdout.write(JSON.stringify({ requests, external, errors, dom }) + "\n");
  } finally {
    await browser.close();
  }
})().catch((e) => { process.stderr.write(String(e && e.stack || e) + "\n"); process.exit(1); });
