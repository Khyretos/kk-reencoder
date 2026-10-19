// Renders banner.html to banner.png (1983x793, same size as the Kompanion banner).
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
const here = dirname(fileURLToPath(import.meta.url));
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1983, height: 793 } });
await page.goto("file://" + join(here, "banner.html"));
await page.evaluate(() => document.fonts.ready);
await page.waitForTimeout(800);
await page.screenshot({ path: join(here, "banner.png") });
await browser.close();
