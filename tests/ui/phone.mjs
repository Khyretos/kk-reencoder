// Run like check.mjs (same image, BASE): a phone-like browser (6x CPU throttle, touch, 384 px) on a library-sized
// database. Must print 8/8 taps kept and a freeze under 500 ms; run it before every UI deploy.
// closer to a real phone: 6x CPU throttling (mid-range Android), touch, 384 px, and taps at random moments
import { chromium } from 'playwright';
const b = await chromium.launch(); const out = [];
const ctx = await b.newContext({ viewport: { width: 384, height: 853 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3.75,
  userAgent: 'Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36' });
const p = await ctx.newPage();
const cdp = await ctx.newCDPSession(p); await cdp.send('Emulation.setCPUThrottlingRate', { rate: 6 });
await p.goto(process.env.BASE + '/'); await p.waitForSelector('#folders .folder input.pick', { timeout: 60000 }); await p.waitForTimeout(3000);
const t0 = Date.now(); await p.evaluate(() => window.__kkRenderTest = performance.now());
const renderMs = await p.evaluate(() => new Promise(r => { const t = performance.now(); document.getElementById('view-list').click(); requestAnimationFrame(() => r(performance.now() - t)); }));
out.push('switch to list took ' + Math.round(renderMs) + ' ms (throttled)');
let ok = 0, n = 0;
for (let i = 0; i < 8; i++) {
  const pick = p.locator('#folders .folder input.pick').nth(i);
  try { await pick.scrollIntoViewIfNeeded({ timeout: 3000 }); await pick.tap({ timeout: 3000 }); } catch (e) { out.push('tap ' + i + ' failed: ' + e.message.split('\n')[0]); } await p.waitForTimeout(1200 + i * 700);
  n++; if (await p.locator('#folders .folder input.pick').nth(i).isChecked()) ok++;
}
out.push(`taps that stayed checked: ${ok}/${n}; button: ${await p.locator('#encode-selected').textContent()}`);
const gridMs = await p.evaluate(() => new Promise(r => { const t = performance.now(); document.getElementById('view-grid').click(); requestAnimationFrame(() => r(performance.now() - t)); }));
out.push('switch to grid took ' + Math.round(gridMs) + ' ms; folders class: ' + await p.locator('#folders').getAttribute('class'));
const longTasks = await p.evaluate(() => new Promise(r => { let worst = 0; const t = performance.now(); const tick = () => { const n = performance.now(); worst = Math.max(worst, n - last); last = n; if (n - t < 12000) setTimeout(tick, 16); else r(Math.round(worst)); }; let last = performance.now(); tick(); }));
out.push('longest main-thread freeze over 12 s of polling: ' + longTasks + ' ms');
console.log(out.join('\n')); await b.close();
