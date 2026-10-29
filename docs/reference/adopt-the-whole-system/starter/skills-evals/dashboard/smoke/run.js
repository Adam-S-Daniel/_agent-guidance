// Open <work dir>/test.html (from mk.py), click every tab, and print page errors.
// Usage: node dashboard/smoke/run.js <work dir>   (needs Playwright and Chromium)
const { chromium } = require('playwright');
const work = require('path').resolve(process.argv[2]);
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1100, height: 900 } });
  const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => m.type() === 'error' && errs.push(m.text()));
  await p.goto('file://' + work + '/test.html');
  await p.waitForTimeout(300);
  for (const tab of await p.$$eval('[id^="tab-"]', (els) => els.map((e) => e.id))) {
    await p.click('#' + tab);
    await p.waitForTimeout(150);
    await p.screenshot({ path: work + '/' + tab + '.png', fullPage: true });
  }
  console.log('errors', JSON.stringify(errs));
  await b.close();
  process.exit(errs.length ? 1 : 0);
})();
