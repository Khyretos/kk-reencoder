// Phone regression (2026-10-19): open a series window, close it with x, Escape and a tap on the backdrop,
// reopen it, and after each close Grid/List and the pick toggles must still respond (they did not: the
// page stayed inert behind an invisible modal). Prints "pageerror" or "tap failed" lines on a problem.
import { chromium } from 'playwright';
const b = await chromium.launch(); const out = [];
const ctx = await b.newContext({ viewport: { width: 384, height: 853 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2,
  userAgent: 'Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36' });
const p = await ctx.newPage(); const cdp = await ctx.newCDPSession(p); await cdp.send('Emulation.setCPUThrottlingRate', { rate: 6 });
p.on('pageerror', e => out.push('pageerror ' + e.message)); p.on('console', m => { if (m.type() === 'error') out.push('console ' + m.text().slice(0, 150)); });
await p.goto(process.env.BASE + '/'); await p.waitForSelector('#folders .folder input.pick', { timeout: 60000 }); await p.waitForTimeout(2500);
async function state(label) {
  const r = await p.evaluate(() => ({ dlg: document.getElementById('detail').open, inert: document.querySelector('main')?.inert, grid: document.getElementById('view-grid').getAttribute('aria-pressed') }));
  out.push(label + ' ' + JSON.stringify(r));
}
async function tryTaps(label) {
  try { await p.tap('#view-list', { timeout: 3000 }); await p.waitForTimeout(400); await p.tap('#view-grid', { timeout: 3000 }); } catch (e) { out.push(label + ' view tap failed: ' + e.message.split('\n')[0]); }
  const pick = p.locator('#folders .folder input.pick').nth(3);
  try { await pick.tap({ timeout: 3000 }); await p.waitForTimeout(400); out.push(label + ' pick checked: ' + await pick.isChecked()); await pick.tap({ timeout: 3000 }); } catch (e) { out.push(label + ' pick tap failed: ' + e.message.split('\n')[0]); }
  await state(label);
}
await tryTaps('fresh');
// open a series, open compare, close with the x
await p.locator('#folders .folder').first().tap({ position: { x: 30, y: 150 } }); await p.waitForTimeout(1500); await state('dialog opened');
const cmp = p.getByRole('button', { name: 'Compare' }).first(); if (await cmp.count()) { await cmp.tap(); await p.waitForTimeout(1500); }
await p.tap('#detail-close'); await p.waitForTimeout(800); await tryTaps('after close x');
await p.locator('#folders .folder').first().tap({ position: { x: 30, y: 150 } }); await p.waitForTimeout(1200); await state('reopened'); await p.tap('#detail-close'); await p.waitForTimeout(500); await tryTaps('after second close');
// open again and use the phone back gesture (Escape) and a tap outside
await p.locator('#folders .folder').first().tap({ position: { x: 30, y: 150 } }); await p.waitForTimeout(1500);
await p.keyboard.press('Escape'); await p.waitForTimeout(500); await tryTaps('after escape');
await p.locator('#folders .folder').first().tap({ position: { x: 30, y: 150 } }); await p.waitForTimeout(1500);
await p.touchscreen.tap(5, 5); await p.waitForTimeout(500); await tryTaps('after tap outside');
console.log(out.join('\n')); await b.close();
