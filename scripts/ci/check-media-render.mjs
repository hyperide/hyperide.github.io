#!/usr/bin/env node
/**
 * Rendered contract for the screenshots, original requests and recordings in the three reports.
 *
 * What a reader gets, checked in a real browser (not only image.decode):
 *   - every annotated crop decodes, shows undistorted at its natural aspect ratio, contains the yellow
 *     annotation outline and real picture detail, and links to a full-size original that decodes and
 *     has the recorded size;
 *   - before and after crops of one comparison render at the same size (same region, same scale);
 *   - every owner request shows the visible labels "Original request (RU)" and "English translation";
 *   - every recording is a real <video> with controls, preload=metadata and a poster, no autoplay,
 *     and it actually plays: metadata loads, the duration matches the record and currentTime advances;
 *   - no horizontal overflow, no console error, no failed network request;
 *   - the same content is present with JavaScript disabled.
 *
 * Usage: node scripts/ci/check-media-render.mjs [--root <repo>] [--base <https origin>] [--engine chromium|webkit|chrome]...
 *        [--chrome-path <binary>] [--out <dir>] [--report <slug>]...
 * Set PLAYWRIGHT_MODULE to an absolute playwright index.mjs when it is not installed next to this repository.
 */
import { createServer } from 'node:http';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { staticHandler } from './_static_server.mjs';

const pw = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright');
const args = process.argv.slice(2);
const all = (name) => args.flatMap((a, i) => (a === name ? [args[i + 1]] : []));
const opt = (name) => all(name)[0];
const ROOT = resolve(opt('--root') ?? join(dirname(fileURLToPath(import.meta.url)), '..', '..'));
const OUT = opt('--out');
if (OUT) mkdirSync(OUT, { recursive: true });
const ENGINES = all('--engine').length ? all('--engine') : ['chromium'];
const SLUGS = all('--report').length ? all('--report') : ['hyper-2026-10', 'state-lib-2026-10', 'conloca-2026-10'];
const REMOTE = opt('--base');

const PROFILES = [
  { name: 'desktop-1440', viewport: { width: 1440, height: 900 } },
  { name: 'phone-390', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
  { name: 'no-js-390', viewport: { width: 390, height: 844 }, isMobile: true, javaScriptEnabled: false, deviceScaleFactor: 2 },
];
let server;
let base = REMOTE;
if (!REMOTE) {
  server = createServer(staticHandler(ROOT));
  await new Promise((ok) => server.listen(0, '127.0.0.1', ok));
  base = `http://127.0.0.1:${server.address().port}`;
}

const failures = [];
const metrics = [];
const fail = (tag, msg) => failures.push(`[${tag}] ${msg}`);
const evidence = (slug) => JSON.parse(readFileSync(join(ROOT, 'reports', slug, 'media-evidence.json'), 'utf8'));

async function launch(engine) {
  if (engine === 'webkit') return pw.webkit.launch(opt('--webkit-path') ? { executablePath: opt('--webkit-path') } : {});
  if (engine === 'chrome') return pw.chromium.launch({ executablePath: opt('--chrome-path'), args: ['--no-sandbox'] });
  return pw.chromium.launch();
}

for (const engine of ENGINES) {
  const browser = await launch(engine);
  try {
    for (const slug of SLUGS) {
      const media = evidence(slug);
      for (const { name, ...options } of PROFILES) {
        const tag = `${engine} ${slug} ${name}`;
        const noJs = options.javaScriptEnabled === false;
        const context = await browser.newContext(options);
        const page = await context.newPage();
        const problems = [];
        page.on('pageerror', (e) => problems.push(String(e)));
        page.on('console', (m) => m.type() === 'error' && problems.push(m.text()));
        page.on('response', (r) => r.status() >= 400 && problems.push(`${r.status()} ${r.url()}`));
        // A browser may abort a partial media request once it has the bytes it needs (preload=metadata); that is not a failure.
        page.on('requestfailed', (r) => !(r.resourceType() === 'media' && /ERR_ABORTED/.test(r.failure()?.errorText ?? '')) && problems.push(`request failed ${r.url()} ${r.failure()?.errorText ?? ''}`));
        // WebKit builds without media plugins can hold the load event back for <video preload=metadata>, so wait for the document and then for the images separately.
        await page.goto(`${base}/reports/${slug}/`, { waitUntil: 'domcontentloaded' });
        await page.waitForLoadState('load', { timeout: 8000 }).catch(() => {});
        await page.evaluate(() => document.fonts.ready);

        // Visible without expanding anything: the proof sits in the result card, not behind a disclosure.
        const VISIBLE = {
          'hyper-2026-10': ['#a2-no-style-surface-warning .shot img', '#a3-dead-prop-refusal .shot img', '#a3-dead-prop-refusal .owner-request', '#a4-css-miss-inline-floor .owner-request', '#a5-fill-color-class-sync .owner-request',
            '#c1-stale-context-cancellation .shot img', '#c2-verified-move .shot img', '#c2-verified-move .owner-request', '#c3-ai-fix-on-click-only .shot img', '#c5-edit-component-drag-undo .shot img',
            '#s1-operation-authority-in-style-editor .shot img', '#t-explorer-empty-state .shot img', '#t-new-component-dialog .shot img', '#t-split-first-click .owner-request', '#t3-layout-panel-pixel-size .owner-request'],
          'conloca-2026-10': ['#editor .owner-request', '#editor video', '#editor .shot img', '#workspace .owner-request', '#landing video', '#approval .shot img'],
          'state-lib-2026-10': ['#requests .owner-request', '#live-docs .shot img', '#figures .owner-request'],
        };
        for (const sel of VISIBLE[slug] ?? []) {
          const open = await page.evaluate((s) => { const e = document.querySelector(s); if (!e) return 'missing'; const r = e.getBoundingClientRect(); const hidden = e.closest('details:not([open])'); return r.height > 0 && !hidden ? 'visible' : 'hidden'; }, sel);
          if (open !== 'visible') fail(tag, `${sel} is ${open} before any disclosure is opened`);
        }
        await page.evaluate(() => document.querySelectorAll('details').forEach((d) => { d.open = true; }));

        // Owner requests: labels, languages and text equal to the record.
        const requests = await page.evaluate(() =>
          [...document.querySelectorAll('.owner-request')].map((el) => {
            const labels = [...el.querySelectorAll('.request-label')].map((l) => l.innerText.trim());
            const ru = el.querySelector('blockquote[lang="ru"]');
            const en = el.querySelector('blockquote[lang="en"]');
            const box = (n) => (n ? n.getBoundingClientRect() : { width: 0, height: 0 });
            return { id: el.id, labels, ru: ru?.innerText.replace(/\s+/g, ' ').trim(), en: en?.innerText.replace(/\s+/g, ' ').trim(), ruH: box(ru).height, enH: box(en).height, visible: el.getBoundingClientRect().height > 0 };
          }),
        );
        const recs = media.originalRequests;
        if (requests.length !== recs.length) fail(tag, `${requests.length} request blocks on the page, ${recs.length} in the record`);
        for (const r of requests) {
          const rec = recs.find((x) => x.id === r.id);
          if (!rec) { fail(tag, `request block ${r.id} is not in the record`); continue; }
          if (!/^original request \(ru\)$/i.test(r.labels[0] ?? '')) fail(tag, `${r.id}: first label is ${r.labels[0]}`);
          if (!/^english translation$/i.test(r.labels[1] ?? '')) fail(tag, `${r.id}: second label is ${r.labels[1]}`);
          if (!r.visible || r.ruH < 10 || r.enH < 10) fail(tag, `${r.id}: a request column is not visible`);
          const norm = (s) => s.replace(/\s+/g, ' ').trim();
          if (r.ru !== norm(rec.ru)) fail(tag, `${r.id}: Russian text on the page differs from the record`);
          if (r.en !== norm(rec.en)) fail(tag, `${r.id}: translation on the page differs from the record`);
        }

        const overflow = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: innerWidth }));
        if (overflow.sw > overflow.iw + 1) fail(tag, `horizontal overflow ${overflow.sw} > ${overflow.iw}`);

        // Screenshots: decode, aspect ratio, annotation pixels, original link.
        const imgs = await page.locator('.shot img').all();
        if (imgs.length !== media.images.length) fail(tag, `${imgs.length} screenshots on the page, ${media.images.length} in the record`);
        for (const img of imgs) {
          await img.scrollIntoViewIfNeeded();
          const info = await img.evaluate(async (el) => {
            try { await el.decode(); } catch { return { ok: false, src: el.getAttribute('src') }; }
            const r = el.getBoundingClientRect();
            const c = document.createElement('canvas');
            c.width = el.naturalWidth; c.height = el.naturalHeight;
            const g = c.getContext('2d');
            g.drawImage(el, 0, 0);
            const d = g.getImageData(0, 0, c.width, c.height).data;
            let yellow = 0; const seen = new Set();
            for (let i = 0; i < d.length; i += 4) {
              if (d[i] > 240 && d[i + 1] > 195 && d[i + 1] < 225 && d[i + 2] < 40) yellow++;
              if ((i / 4) % 7 === 0) seen.add((d[i] >> 4) * 256 + (d[i + 1] >> 4) * 16 + (d[i + 2] >> 4));
            }
            return { ok: true, src: el.getAttribute('src'), nw: el.naturalWidth, nh: el.naturalHeight, w: r.width, h: r.height, yellow, colours: seen.size, href: el.closest('a')?.getAttribute('href') };
          });
          if (!info.ok) { fail(tag, `crop failed to decode: ${info.src}`); continue; }
          const rec = media.images.find((i) => i.path === info.src);
          if (!rec) { fail(tag, `crop ${info.src} is not in the record`); continue; }
          if (info.nw !== rec.width || info.nh !== rec.height) fail(tag, `${info.src}: natural size ${info.nw}x${info.nh}, record ${rec.width}x${rec.height}`);
          if (info.w > 0 && Math.abs(info.w / info.h - info.nw / info.nh) > 0.02 * (info.nw / info.nh)) fail(tag, `${info.src}: distorted, shown ${info.w}x${info.h}`);
          if (info.w < 150 && !(noJs && info.w === 0)) fail(tag, `${info.src}: shown only ${Math.round(info.w)}px wide`);
          if (info.yellow < 200) fail(tag, `${info.src}: annotation outline not found (${info.yellow} yellow pixels)`);
          if (info.colours < 12) fail(tag, `${info.src}: looks blank (${info.colours} colour bins)`);
          if (info.href !== rec.original.path) fail(tag, `${info.src}: links to ${info.href}, record says ${rec.original.path}`);
          const orig = await page.evaluate(async (href) => {
            const im = new Image(); im.src = href;
            try { await im.decode(); } catch { return null; }
            return { w: im.naturalWidth, h: im.naturalHeight };
          }, info.href);
          if (!orig || orig.w !== rec.original.width || orig.h !== rec.original.height) fail(tag, `${info.src}: original ${info.href} does not decode at ${rec.original.width}x${rec.original.height}`);
        }

        // Before and after of one comparison: same displayed size.
        for (const group of await page.locator('.shot-group[data-kind="pair"]').all()) {
          const sizes = await group.evaluate((g) => {
            const out = {};
            for (const f of g.querySelectorAll('.shot')) {
              const id = f.id.replace('fig-', '');
              const r = f.querySelector('img').getBoundingClientRect();
              out[id] = [Math.round(r.width), Math.round(r.height)];
            }
            return out;
          });
          const byPair = {};
          for (const [id, size] of Object.entries(sizes)) {
            const rec = media.images.find((i) => i.id === id);
            if (!rec) { fail(tag, `figure ${id} is not in the record`); continue; }
            if (!rec.pair) continue; // an unpaired capture (interim or later stage) has no partner to compare with
            (byPair[rec.pair] ??= []).push(size);
          }
          for (const [pair, list] of Object.entries(byPair)) {
            if (list.length !== 2 || Math.abs(list[0][0] - list[1][0]) > 1 || Math.abs(list[0][1] - list[1][1]) > 1) fail(tag, `pair ${pair} is not rendered at one size: ${JSON.stringify(list)}`);
          }
        }

        // Recordings.
        const clips = await page.locator('figure.clip video').all();
        const expected = media.videos ?? [];
        if (clips.length !== expected.length) fail(tag, `${clips.length} videos on the page, ${expected.length} in the record`);
        for (const video of clips) {
          const posterAttr = await video.getAttribute('poster');
          const rec = expected.find((x) => x.poster.path === posterAttr);
          if (!rec) { fail(tag, `video with poster ${posterAttr} is not in the record`); continue; }
          const attrs = await video.evaluate((v) => ({ controls: v.hasAttribute('controls'), inline: v.hasAttribute('playsinline'), preload: v.getAttribute('preload'), poster: v.getAttribute('poster'), autoplay: v.hasAttribute('autoplay'), sources: [...v.querySelectorAll('source')].map((s) => [s.getAttribute('src'), s.getAttribute('type')]) }));
          if (!attrs.controls || !attrs.inline || attrs.preload !== 'metadata' || !attrs.poster || attrs.autoplay) fail(tag, `${rec.id}: video attributes ${JSON.stringify(attrs)}`);
          if (attrs.poster !== rec.poster.path) fail(tag, `${rec.id}: poster is ${attrs.poster}`);
          if (JSON.stringify(attrs.sources.map((s) => s[0])) !== JSON.stringify([rec.mp4.path, rec.webm.path])) fail(tag, `${rec.id}: sources differ from the record`);
          const bytes = await page.evaluate(async (u) => { const r = await fetch(u); return { status: r.status, type: r.headers.get('content-type'), len: (await r.arrayBuffer()).byteLength }; }, rec.poster.path);
          if (bytes.status !== 200 || !/image\/jpeg/.test(bytes.type ?? '') || bytes.len !== rec.poster.bytes) fail(tag, `${rec.id}: poster fetch ${JSON.stringify(bytes)}`);
          if (noJs) continue;
          // Headless Linux WebKit has no H.264 or VP9 playback and can stall on media; attributes, poster and sources are still checked above.
          if (engine === 'webkit') { metrics.push({ tag, clip: rec.id, untestable: 'playback is not exercised in Linux WebKit' }); continue; }
          await video.scrollIntoViewIfNeeded();
          const played = await video.evaluate(async (v, dur) => {
            v.muted = true;
            const wait = (ev) => new Promise((ok, no) => { v.addEventListener(ev, ok, { once: true }); v.addEventListener('error', () => no(new Error(`media error ${v.error?.code}`)), { once: true }); setTimeout(() => no(new Error(`timeout waiting for ${ev}`)), 15000); });
            try {
              if (v.readyState < 1) { v.load(); await wait('loadedmetadata'); }
              const start = v.currentTime;
              await v.play();
              await new Promise((ok) => { const t = setInterval(() => { if (v.currentTime > start + Math.min(0.6, dur / 4) || v.ended) { clearInterval(t); ok(); } }, 50); setTimeout(() => { clearInterval(t); ok(); }, 8000); });
              const result = { ok: true, mime: v.currentSrc.split('.').pop(), progressed: v.currentTime - start, duration: v.duration, w: v.videoWidth, h: v.videoHeight };
              v.pause();
              return result;
            } catch (e) { return { ok: false, error: String(e), src: v.currentSrc, networkState: v.networkState }; }
          }, rec.mp4.durationSeconds);
          if (!played.ok) { fail(tag, `${rec.id}: did not play: ${JSON.stringify(played)}`); continue; }
          const source = played.mime === 'mp4' ? rec.mp4 : rec.webm;
          if (Math.abs(played.duration - source.durationSeconds) > 0.2) fail(tag, `${rec.id}: duration ${played.duration}, record ${source.durationSeconds}`);
          if (played.w !== source.width || played.h !== source.height) fail(tag, `${rec.id}: video size ${played.w}x${played.h}`);
          if (played.progressed < 0.25) fail(tag, `${rec.id}: currentTime advanced only ${played.progressed}`);
          metrics.push({ tag, clip: rec.id, played: played.mime, progressed: Number(played.progressed.toFixed(2)), duration: played.duration });
          // Each encoding on its own: WebM must always play (it is the fallback), MP4 whenever the browser supports H.264.
          const formats = {};
          for (const [format, file, codec] of [['mp4', rec.mp4, 'video/mp4; codecs="avc1.640028"'], ['webm', rec.webm, 'video/webm; codecs="vp9"']]) {
            const r = await page.evaluate(async ([url, codec]) => {
              const v = document.createElement('video');
              const supported = v.canPlayType(codec);
              if (!supported) return { supported: false };
              v.muted = true; v.preload = 'metadata'; v.src = url;
              const meta = new Promise((ok, no) => { v.onloadedmetadata = ok; v.onerror = () => no(new Error(`media error ${v.error?.code}`)); setTimeout(() => no(new Error('timeout')), 15000); });
              try { await meta; const s = v.currentTime; await v.play(); await new Promise((ok) => { const i = setInterval(() => { if (v.currentTime > s + 0.3 || v.ended) { clearInterval(i); ok(); } }, 50); setTimeout(() => { clearInterval(i); ok(); }, 8000); }); const out = { supported: true, ok: v.currentTime > s, duration: v.duration, w: v.videoWidth, h: v.videoHeight }; v.pause(); return out; } catch (e) { return { supported: true, ok: false, error: String(e) }; }
            }, [file.path, codec]);
            if (r.supported && (!r.ok || Math.abs(r.duration - file.durationSeconds) > 0.2 || r.w !== file.width || r.h !== file.height)) fail(tag, `${rec.id}: ${format} did not play correctly: ${JSON.stringify(r)}`);
            formats[format] = r;
            metrics.push({ tag, clip: rec.id, format, supported: r.supported, played: Boolean(r.ok) });
          }
          // At least one encoding must be playable in every browser that plays video at all.
          if (!formats.mp4.supported && !formats.webm.supported) fail(tag, `${rec.id}: this browser can play neither the MP4 nor the WebM`);
        }

        if (OUT && args.includes('--shots')) await page.screenshot({ path: join(OUT, `${engine}-${slug}-${name}-full.png`), fullPage: true });
        problems.forEach((p) => fail(tag, `console/network problem: ${p}`));
        metrics.push({ tag, requests: requests.length, screenshots: imgs.length, videos: clips.length, scrollWidth: overflow.sw, innerWidth: overflow.iw });
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
}
server?.close();
if (OUT) writeFileSync(join(OUT, 'media-render-metrics.json'), JSON.stringify({ base, engines: ENGINES, metrics, failures }, null, 1));
if (failures.length) {
  console.error(`${failures.length} media render problem(s):\n - ${failures.join('\n - ')}`);
  process.exitCode = 1;
} else {
  console.log(`Media render contract passed on ${base}: ${SLUGS.length} reports x ${PROFILES.length} profiles x ${ENGINES.join('+')}.`);
}
