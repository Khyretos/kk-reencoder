// Takes the README screenshots from a running reencoder: BASE=http://reencoder:8080 node shoot.mjs
// (run in mcr.microsoft.com/playwright with playwright in node_modules, like tests/ui/check.mjs).
import { chromium } from 'playwright';
const base = process.env.BASE || 'http://127.0.0.1:8080';
const out = process.env.OUT || '.';
const b = await chromium.launch();
async function page(w, h, scheme, mobile = false) {
  const ctx = await b.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, isMobile: mobile, hasTouch: mobile, deviceScaleFactor: mobile ? 3 : 1.5 });
  const p = await ctx.newPage();
  await p.goto(base + '/'); await p.waitForSelector('#folders .folder'); await p.waitForTimeout(3000);
  return p;
}
let p = await page(1400, 1000, 'dark');
await p.screenshot({ path: `${out}/01-library.png` });
await p.locator('.toolbar').scrollIntoViewIfNeeded(); await p.screenshot({ path: `${out}/02-series.png` });
await p.fill('#q', 'compass'); await p.waitForTimeout(500);
await p.locator('#folders .folder').first().click({ position: { x: 40, y: 140 } }); await p.waitForTimeout(1500);
const cmp = p.getByRole('button', { name: 'Compare' }).first();
if (await cmp.count()) { await cmp.click(); await p.waitForTimeout(2500); await p.locator('#compare .cmp').scrollIntoViewIfNeeded(); }
await p.screenshot({ path: `${out}/03-compare.png` });
await p.goto(base + '/overview'); await p.waitForTimeout(4000); await p.screenshot({ path: `${out}/04-overview.png`, fullPage: true });
p = await page(1400, 1000, 'light'); await p.screenshot({ path: `${out}/05-light.png` });
p = await page(390, 844, 'dark', true); await p.click('#view-list'); await p.locator('.toolbar').scrollIntoViewIfNeeded(); await p.screenshot({ path: `${out}/06-phone.png` });
await p.click('#view-grid');
await b.close();
