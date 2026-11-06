#!/usr/bin/env node
/**
 * Rendered contract for the three reports (HyperIDE product report, StateLib and Conloca October 2026).
 *
 * Complements validate-reports.mjs (structure, assets, smoke render) with what a reader sees:
 *   - the title, summary tiles and outcome sections are present with JavaScript blocked;
 *   - the same sections are visible, fully opaque and in reading order on desktop,
 *     Android-size and iPhone-size viewports, with no horizontal overflow;
 *   - the first screen names the report and starts the overview;
 *   - every image decodes at its natural size and screenshots are readable on phones;
 *   - the figures table stays readable (stacked rows) on phones.
 *
 * Usage: node scripts/ci/check-october-render.mjs [--root <repo root>] [--out <screenshot dir>]
 */
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFileSync, mkdirSync, statSync } from 'node:fs';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
const opt = (name) => {
  const i = args.indexOf(name);
  return i === -1 ? undefined : args[i + 1];
};
const ROOT = resolve(opt('--root') ?? join(dirname(fileURLToPath(import.meta.url)), '..', '..'));
const OUT = opt('--out');
if (OUT) mkdirSync(OUT, { recursive: true });

// The product report's counts come from its own source record, so the page and this check cannot drift apart.
const productRecord = join(ROOT, 'reports', 'hyper-2026-10', 'evidence.json');
let product;
try {
  product = JSON.parse(readFileSync(productRecord, 'utf8'));
} catch (error) {
  throw new Error(`cannot read the product report source record ${productRecord}: ${error.message}`);
}

const REPORTS = [
  { slug: 'hyper-2026-10', title: product.title, outcomes: product.outcomes.length, images: product.images.length },
  { slug: 'state-lib-2026-10', title: 'StateLib: October 2026', outcomes: 7, images: 2 },
  { slug: 'conloca-2026-10', title: 'Conloca: October 2026', outcomes: 7, images: 2 },
];

const PROFILES = [
  { name: 'desktop-1440', viewport: { width: 1440, height: 900 } },
  { name: 'android-390', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
  { name: 'iphone-393', viewport: { width: 393, height: 852 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 },
  { name: 'iphone-se-375', viewport: { width: 375, height: 667 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
  { name: 'no-js-390', viewport: { width: 390, height: 844 }, isMobile: true, javaScriptEnabled: false, deviceScaleFactor: 2 },
];

const MIME = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json', '.png': 'image/png' };

const server = createServer((req, res) => {
  try {
    const path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    if (path === '/favicon.ico') return res.writeHead(204).end();
    let file = resolve(ROOT, '.' + path);
    if (file !== ROOT && !file.startsWith(ROOT + sep)) return res.writeHead(403).end();
    if (statSync(file).isDirectory()) file = join(file, 'index.html');
    res.writeHead(200, { 'Content-Type': MIME[extname(file)] ?? 'application/octet-stream' }).end(readFileSync(file));
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((ok) => server.listen(0, '127.0.0.1', ok));
const base = `http://127.0.0.1:${server.address().port}`;

const failures = [];
const fail = (tag, msg) => failures.push(`[${tag}] ${msg}`);

const browser = await chromium.launch();
try {
  for (const report of REPORTS) {
    for (const { name, ...contextOptions } of PROFILES) {
      const tag = `${report.slug} ${name}`;
      const noJs = contextOptions.javaScriptEnabled === false;
      const context = await browser.newContext(contextOptions);
      const page = await context.newPage();
      const problems = [];
      page.on('pageerror', (e) => problems.push(String(e)));
      page.on('console', (m) => m.type() === 'error' && problems.push(m.text()));
      page.on('response', (r) => r.status() >= 400 && problems.push(`${r.status()} ${r.url()}`));
      await page.goto(`${base}/reports/${report.slug}/`, { waitUntil: 'networkidle' });
      await page.evaluate(() => document.fonts.ready);
      const m = await page.evaluate(() => {
        const box = (el) => el.getBoundingClientRect();
        const h1 = document.querySelector('h1');
        const overview = document.querySelector('#overview p');
        const ids = ['overview', 'outcomes', 'figures', 'proof'].map((id) => document.getElementById(id));
        // The collapsed audit appendix is closed on purpose, so its content has no height until opened.
        const shown = (el) => !el.closest('details.appendix');
        const opaque = [...document.querySelectorAll('.outcome, .summary-card, .figures tbody tr, .report-section')].filter(shown).filter(
          (el) => Number(getComputedStyle(el).opacity) < 1 || getComputedStyle(el).visibility !== 'visible' || box(el).height === 0,
        ).length;
        const rowsStacked = [...document.querySelectorAll('.figures tbody tr')].every((tr) => getComputedStyle(tr.firstElementChild).display === 'block' || innerWidth > 650);
        const imgs = [...document.images].map((i) => i.getAttribute('src'));
        return {
          h1: h1 ? h1.innerText : null,
          h1Top: h1 ? box(h1).top : null,
          overviewTop: overview ? box(overview).top : null,
          viewport: innerHeight,
          scrollWidth: document.documentElement.scrollWidth,
          innerWidth,
          outcomes: document.querySelectorAll('.outcome').length,
          tiles: document.querySelectorAll('.summary-card').length,
          orderOk: ids.every((el, i) => el && (i === 0 || ids[i - 1].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)),
          hiddenOrTransparent: opaque,
          rowsStacked,
          figureRows: document.querySelectorAll('.figures tbody tr').length,
          details: [...document.querySelectorAll('.outcome details')].every((d) => !d.open && d.querySelector('summary')),
          whatVisible: [...document.querySelectorAll('.outcome .what')].filter(shown).every((p) => p.getBoundingClientRect().height > 40 && getComputedStyle(p).visibility === 'visible'),
          imgs,
        };
      });

      if (m.h1 !== report.title) fail(tag, `h1 is ${JSON.stringify(m.h1)}, expected ${JSON.stringify(report.title)}`);
      if (m.h1Top === null || m.h1Top >= m.viewport) fail(tag, 'title is not on the first screen');
      if (m.overviewTop === null || m.overviewTop >= m.viewport) fail(tag, 'overview does not start on the first screen');
      if (m.scrollWidth > m.innerWidth + 1) fail(tag, `horizontal overflow ${m.scrollWidth} > ${m.innerWidth}`);
      if (m.outcomes < report.outcomes) fail(tag, `${m.outcomes} outcome sections, expected at least ${report.outcomes}`);
      if (m.tiles < 3) fail(tag, `${m.tiles} summary tiles`);
      if (!m.orderOk) fail(tag, 'overview, outcomes, figures and proof are not in reading order');
      if (m.hiddenOrTransparent) fail(tag, `${m.hiddenOrTransparent} sections are transparent, hidden or empty`);
      if (!m.details) fail(tag, 'technical detail must be collapsed by default with a summary');
      if (!m.whatVisible) fail(tag, 'an outcome paragraph is not visible');
      if (m.figureRows < 3) fail(tag, `${m.figureRows} figure rows`);
      if (!m.rowsStacked) fail(tag, 'figures table is not stacked on a phone viewport');
      if (m.imgs.length !== report.images) fail(tag, `${m.imgs.length} images, expected ${report.images}`);

      // Technical detail is closed on load and must open on activation, with its content then visible.
      const toggled = await page.evaluate(() => {
        const d = document.querySelector('.outcome details');
        const before = d.open;
        d.querySelector('summary').click();
        const body = d.querySelector('ul');
        const result = { before, after: d.open, visible: body.getBoundingClientRect().height > 0 };
        d.querySelector('summary').click();
        return { ...result, closedAgain: !d.open };
      });
      if (toggled.before || !toggled.after || !toggled.visible || !toggled.closedAgain) fail(tag, `technical detail does not toggle: ${JSON.stringify(toggled)}`);

      // Full-page pass: lazy images only load once they are near the viewport.
      for (const image of await page.locator('img').all()) {
        await image.scrollIntoViewIfNeeded();
        const ok = await image.evaluate((el) => el.decode().then(() => el.naturalWidth > 0, () => false));
        if (!ok) fail(tag, `image failed to decode: ${await image.getAttribute('src')}`);
      }
      if (contextOptions.isMobile) {
        // Phone crop: the visible part of a screenshot must be large enough to read.
        for (const el of await page.locator('.crop').all()) {
          const { w, h, iw } = await el.evaluate((c) => ({ w: c.getBoundingClientRect().width, h: c.getBoundingClientRect().height, iw: c.querySelector('img').getBoundingClientRect().width }));
          if (h < 150) fail(tag, `crop is only ${h}px tall`);
          if (iw < w * 2.5) fail(tag, `image is shown at ${iw}px inside a ${w}px crop; detail is not magnified`);
        }
      }
      if (!noJs) {
        // The shared renderer must have run (the static header alone would satisfy a header check).
        const init = await page.evaluate(() => Boolean(window.Report && window.Report.data && window.Report.data.title));
        if (!init) fail(tag, 'Report.init did not run');
        const header = await page.locator('[data-section="header"] h1').count();
        if (header !== 1) fail(tag, 'rendered header lost its title');
      }
      await page.evaluate(() => window.scrollTo(0, 0));
      if (OUT) {
        await page.screenshot({ path: join(OUT, `${report.slug}-${name}-first-screen.png`) });
        await page.screenshot({ path: join(OUT, `${report.slug}-${name}-full.png`), fullPage: true });
      }
      // Everything the page logged or requested, including lazy images, toggles, crops and screenshots.
      problems.forEach((p) => fail(tag, `console/network problem: ${p}`));
      await context.close();
    }
  }
} finally {
  await browser.close();
  server.close();
}

if (failures.length) {
  console.error(`${failures.length} rendered-contract problem(s):\n - ${failures.join('\n - ')}`);
  process.exitCode = 1;
} else {
  console.log(`October reports rendered contract passed: ${REPORTS.length} reports x ${PROFILES.length} viewports.`);
}
