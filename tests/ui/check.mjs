// UI smoke check against a running reencoder (BASE, default the local preview): no page errors,
// the library page fills its tiles and folder cards, the detail dialog opens, the overview draws charts.
// Prints "error: <file>: <why>" lines and exits 1 on any problem.
import { chromium } from 'playwright';
const base = process.env.BASE || 'http://127.0.0.1:18080';
const problems = [];
const b = await chromium.launch();
async function open(path) {
  const p = await b.newPage({ viewport: { width: 1300, height: 900 } });
  p.on('pageerror', e => problems.push(`error: ${path === '/' ? 'app.js/detail.js/chart.js' : 'overview.js'}: ${e.message}`));
  p.on('console', m => { if (m.type() === 'error') problems.push(`error: ${path === '/' ? 'app.js/detail.js' : 'overview.js'}: console: ${m.text().slice(0, 300)}`); });
  p.on('response', r => { if (r.status() >= 400) problems.push(`error: ${path}: ${r.status()} for ${r.url()}`); });
  await p.goto(base + path, { waitUntil: 'load' });
  await p.waitForTimeout(3000);
  return p;
}
const lib = await open('/');
const tileValues = await lib.locator('#tiles .tile-value').allTextContents();
if (tileValues.length !== 4 || tileValues.some(t => !/\d/.test(t))) problems.push(`error: app.js: tiles not filled (${JSON.stringify(tileValues)})`);
const cards = await lib.locator('#folders .folder').count();
if (cards < 1) problems.push('error: app.js: no .folder cards rendered in #folders');
if ((await lib.locator('#libchart > *').count()) < 1) problems.push('error: chart.js: #libchart is empty');
const libsApi = await (await fetch(base + '/api/libraries')).json();
await lib.waitForTimeout(11000);  // the library chart redraws every 10 s
const chartRows = await lib.locator('#libchart .kk-chart-row').count();
if (chartRows !== libsApi.length) problems.push(`error: chart.js: #libchart has ${chartRows} rows for ${libsApi.length} libraries after a redraw`);
if (!/Live/.test(await lib.locator('#conn').textContent())) problems.push('error: app.js: #conn does not say Live');
if (cards) {
  await lib.locator('#folders .folder').first().click({ position: { x: 10, y: 10 } });
  await lib.waitForTimeout(1500);
  if (!(await lib.locator('#detail').evaluate(d => d.open))) problems.push('error: detail.js: clicking a folder card does not open #detail');
  else if ((await lib.locator('#detail-body tr').count()) < 2) problems.push('error: detail.js: #detail-body has no file rows');
}
const ov = await open('/overview');
for (const id of ['ch-donut', 'ch-history', 'ch-libs', 'ch-top', 'ch-codecs', 'ch-live']) {
  if ((await ov.locator(`#${id} canvas, #${id} svg`).count()) < 1) problems.push(`error: overview.js: chart #${id} not drawn`);
}
if (!/\d/.test(await ov.locator('#saved-big').textContent())) problems.push('error: overview.js: #saved-big has no number');
await b.close();
for (const p of [...new Set(problems)]) console.log(p);
process.exit(problems.length ? 1 : 0);
