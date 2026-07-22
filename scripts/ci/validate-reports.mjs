#!/usr/bin/env node
/**
 * CI validation for hyperide.github.io report pages.
 *
 * This is a static GitHub Pages site (report cards built on top of
 * reports/shared/{styles.css,components.js}). There is no build step, so
 * the failure mode this guards against is: a report ships with a broken
 * relative asset path, malformed HTML, or a `Report.init({...})` payload
 * that throws at runtime — all invisible until someone opens the live page.
 *
 * Scope: only report `index.html` files that changed in the current
 * diff (BASE_SHA..HEAD_SHA). Falls back to validating every report when
 * run outside a diff context (e.g. manual dispatch) so the check still
 * has meaning locally.
 *
 * Checks per changed report file:
 *   1. HTML structural well-formedness (balanced tags).
 *   2. Local <link>/<script> src|href paths resolve to real files.
 *   3. A headless-browser smoke render: page loads, `Report.init` runs
 *      without throwing, no console errors, and at least one
 *      `[data-section]` element ends up populated.
 *   4. Internal (repo-relative) links in the report resolve to real paths.
 *
 * Usage: node scripts/ci/validate-reports.mjs [file ...]
 *   With no args, discovers changed files via BASE_SHA/HEAD_SHA env vars,
 *   or validates every report's index.html if those are unset.
 */

import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import { createServer } from 'node:http';
import { readFileSync, existsSync, statSync, readdirSync } from 'node:fs';
import { join, dirname, resolve as resolvePath, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolvePath(__dirname, '..', '..');

/** Recursively find every reports/&lt;name&gt;/index.html, stdlib-only (no fs.globSync — Node 22+ only). */
function findAllReportIndexFiles() {
  const reportsDir = join(REPO_ROOT, 'reports');
  // Without this guard, a missing reports/ dir (e.g. a repo layout change,
  // or this running against an unexpected checkout) throws ENOENT out to
  // main().catch — an opaque crash instead of a clean "nothing to validate".
  if (!existsSync(reportsDir)) return [];
  const found = [];
  for (const entry of readdirSync(reportsDir, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name === 'shared' || entry.name === '_template') continue;
    const indexPath = join(reportsDir, entry.name, 'index.html');
    if (existsSync(indexPath)) found.push(`reports/${entry.name}/index.html`);
  }
  return found;
}

const VOID_ELEMENTS = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
  'link', 'meta', 'param', 'source', 'track', 'wbr',
]);

function discoverChangedReportFiles() {
  const explicitArgs = process.argv.slice(2);
  if (explicitArgs.length > 0) return explicitArgs;

  const base = process.env.BASE_SHA;
  const head = process.env.HEAD_SHA || 'HEAD';
  if (base) {
    // Three-dot: diff against the merge-base, not the base branch's current
    // tip — a plain two-dot diff would also surface files that changed on
    // the base branch AFTER this PR branched, unrelated to this PR's diff.
    // --diff-filter=d excludes deletions: a PR that removes or renames a
    // report is legitimate and must not fail validation on the old path.
    const out = execFileSync('git', ['diff', '--name-only', '--diff-filter=d', `${base}...${head}`], {
      cwd: REPO_ROOT,
      encoding: 'utf8',
    });
    const changedFiles = out
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l.length > 0);

    // Every report is built on reports/shared/{styles.css,components.js} and
    // Report.init — the single highest-blast-radius change is exactly there.
    // If a PR only touches shared/ (no index.html changed), scoping to "no
    // changed report pages" would validate zero pages and report green with
    // nothing checked. Fall back to validating every report in that case.
    if (changedFiles.some((l) => l.startsWith('reports/shared/'))) {
      return findAllReportIndexFiles();
    }

    return changedFiles
      .filter((l) => /(^|\/)index\.html$/.test(l))
      .filter((l) => l.startsWith('reports/'))
      .filter((l) => !l.startsWith('reports/shared/'))
      .filter((l) => !l.startsWith('reports/_template/'));
  }

  // Fallback: no diff context — validate every report page.
  return findAllReportIndexFiles();
}

/**
 * Strip <script>/<style> bodies and HTML comments so tag-balance checks
 * don't trip on JS/CSS content or commented-out example markup
 * (e.g. `<!-- old <ul> layout -->` would otherwise push/pop a fake tag).
 *
 * Order matters: script/style bodies are stripped FIRST, comments SECOND.
 * A `Report.init({...})` payload commonly embeds HTML strings (e.g. the
 * `motivation`/`links` fields), and a literal `<!--` inside such a JS string
 * is not an HTML comment — but if comment-stripping ran on the raw HTML
 * first, its lazy `[\s\S]*?-->` would match through to the next *real*
 * `-->` anywhere in the document, silently eating the `</script>` tag in
 * between and leaving an apparently-unclosed <script>.
 */
function stripScriptAndStyleBodies(html) {
  return html
    .replace(/(<script\b[^>]*>)([\s\S]*?)(<\/script>)/gi, (_, open, _body, close) => open + close)
    .replace(/(<style\b[^>]*>)([\s\S]*?)(<\/style>)/gi, (_, open, _body, close) => open + close)
    .replace(/<!--[\s\S]*?-->/g, '');
}

// Elements whose closing tag HTML5 makes optional. Two distinct implicit-close
// rules apply to these, both needed or common valid markup false-positives:
//   1. Same-tag sibling: opening a second <li> implicitly closes the first
//      (`<ul><li>a<li>b</li></ul>`).
//   2. Ancestor close: the *parent's* closing tag implicitly closes a still-open
//      optional-close child (`<ul><li>a</ul>`, `<table><tr><td>x</table>`).
// Without rule 2, `</ul>` finding `li` on top of the stack (instead of `ul`)
// reads as "unbalanced" even though every browser accepts this markup.
const OPTIONAL_CLOSE_SIBLINGS = new Set([
  'li', 'p', 'td', 'th', 'tr', 'option', 'dt', 'dd',
  // Table sectioning elements also have an optional end tag in HTML5
  // (`<table><tbody><tr><td>a</table>` is valid with no explicit
  // </tbody>/</tr>/</td>) — omitting these made the ancestor-close walk
  // (Rule 2) stop at the first sectioning tag and misreport it as unbalanced.
  'tbody', 'thead', 'tfoot', 'colgroup', 'caption',
]);

// Block-level tags that HTML5 has implicitly close a still-open <p> when they
// are opened as its sibling (`<p>text<ul>...` — the <p> has no explicit
// closing tag but is not "unclosed", the <ul> ends it).
const P_CLOSING_TAGS = new Set([
  'address', 'article', 'aside', 'blockquote', 'details', 'div', 'dl',
  'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3',
  'h4', 'h5', 'h6', 'header', 'hr', 'main', 'nav', 'ol', 'p', 'pre',
  'section', 'table', 'ul',
]);

function checkHtmlStructure(html, stripped, relPath) {
  const errors = [];
  if (!/^\s*<!DOCTYPE html>/i.test(html.replace(/^\uFEFF/, ''))) {
    errors.push('missing <!DOCTYPE html> at start of file');
  }

  const tagRe = /<\/?([a-zA-Z][a-zA-Z0-9-]*)\b([^>]*)>/g;
  const stack = [];
  let match;
  while ((match = tagRe.exec(stripped)) !== null) {
    const [full, name, attrs] = match;
    const tag = name.toLowerCase();
    const isClosing = full.startsWith('</');
    const isSelfClosing = /\/\s*>$/.test(full) || VOID_ELEMENTS.has(tag);

    if (isClosing) {
      if (stack.length > 0 && stack[stack.length - 1] === tag) {
        stack.pop();
      } else {
        // Rule 2: walk down through any still-open optional-close elements
        // looking for this closing tag's match — each one is implicitly
        // closed by the ancestor's closing tag, not a real error.
        let idx = -1;
        for (let i = stack.length - 1; i >= 0; i--) {
          if (stack[i] === tag) {
            idx = i;
            break;
          }
          if (!OPTIONAL_CLOSE_SIBLINGS.has(stack[i])) break; // real mismatch
        }
        if (idx !== -1) {
          stack.length = idx;
        } else if (OPTIONAL_CLOSE_SIBLINGS.has(tag)) {
          // A stray explicit close for an optional-close element that is no
          // longer on the stack — it was already implicitly closed earlier
          // (Rule 1 sibling-close or the P_CLOSING_TAGS case below).
          // Browsers silently ignore an unmatched closing tag like this;
          // e.g. `<p>text<div>...</div></p>` implicitly closes <p> at
          // <div>, leaving the trailing explicit </p> a harmless no-op.
        } else {
          errors.push(`unbalanced tag: found </${tag}> but stack top is ${stack[stack.length - 1] || '(empty)'}`);
          // Best-effort recovery: drop matching tag if present anywhere in stack.
          const anyIdx = stack.lastIndexOf(tag);
          if (anyIdx !== -1) stack.length = anyIdx;
        }
      }
    } else if (!isSelfClosing) {
      // Rule 1: an optional-close element (e.g. a second <li>) implicitly
      // closes a same-tag sibling still open at the top of the stack.
      if (OPTIONAL_CLOSE_SIBLINGS.has(tag) && stack[stack.length - 1] === tag) {
        stack.pop();
      } else if (stack[stack.length - 1] === 'p' && P_CLOSING_TAGS.has(tag)) {
        // A block-level sibling implicitly closes a still-open <p>.
        stack.pop();
      }
      stack.push(tag);
    }
    void attrs;
  }

  if (stack.length > 0) {
    errors.push(`unclosed tag(s) at end of file: ${stack.join(', ')}`);
  }

  return errors.map((e) => `[${relPath}] structure: ${e}`);
}

function checkLocalAssetPaths(html, relPath, fileDir) {
  const errors = [];
  // `(?<=\s)` (not `\b`) before href|src — a bare word boundary also matches
  // inside "data-href" (the hyphen is a non-word char, same as a boundary
  // before a real "href"), so a <link> carrying both a real href and an
  // unrelated data-href would have its data-href value captured instead.
  const attrRe = /<(?:link|script)\b[^>]*(?<=\s)(?:href|src)="([^"]+)"[^>]*>/gi;
  let match;
  while ((match = attrRe.exec(html)) !== null) {
    const url = match[1];
    if (/^(https?:)?\/\//i.test(url) || url.startsWith('data:') || url.startsWith('#')) continue;
    // A single-slash path (`/reports/shared/styles.css`) is repo-root-relative,
    // not filesystem-root-relative — resolve it against REPO_ROOT, not fileDir
    // (path.resolve(fileDir, '/x') would discard fileDir and return '/x').
    const resolved = url.startsWith('/')
      ? resolvePath(REPO_ROOT, url.slice(1))
      : resolvePath(fileDir, url);
    if (!existsSync(resolved)) {
      errors.push(`[${relPath}] asset path does not resolve: "${url}" -> ${resolved}`);
    }
  }
  return errors;
}

function checkInternalLinks(html, relPath, fileDir) {
  const errors = [];
  const hrefRe = /<a\b[^>]*(?<=\s)href="([^"]+)"[^>]*>/gi;
  let match;
  while ((match = hrefRe.exec(html)) !== null) {
    const url = match[1];
    // `(https?:)?\/\/` also skips protocol-relative URLs ("//cdn.example.com/x") —
    // without this a protocol-relative link is mistaken for a root-relative
    // internal path and resolved against the filesystem root, always "missing".
    if (/^(https?:)?\/\//i.test(url) || /^(mailto:|tel:)/i.test(url) || url.startsWith('#') || url.startsWith('data:')) continue;
    if (!url.startsWith('.') && !url.startsWith('/')) continue; // skip bare/unknown schemes
    const cleanUrl = url.split('#')[0].split('?')[0];
    if (!cleanUrl) continue;
    const base = url.startsWith('/') ? REPO_ROOT : fileDir;
    const resolved = resolvePath(base, cleanUrl.startsWith('/') ? cleanUrl.slice(1) : cleanUrl);
    // A link to a directory implicitly means its index.html.
    const ok = existsSync(resolved)
      ? (statSync(resolved).isDirectory() ? existsSync(join(resolved, 'index.html')) : true)
      : false;
    if (!ok) {
      errors.push(`[${relPath}] internal link does not resolve: "${url}" -> ${resolved}`);
    }
  }
  return errors;
}

async function smokeRenderCheck(browser, baseUrl, relPath) {
  const errors = [];
  const page = await browser.newPage();
  const consoleErrors = [];
  const pageErrors = [];

  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', (err) => {
    pageErrors.push(String(err));
  });

  const url = `${baseUrl}/${relPath}`;
  try {
    const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 15000 });
    if (!response || !response.ok()) {
      errors.push(`[${relPath}] smoke-render: navigation failed (status ${response ? response.status() : 'none'})`);
    }

    if (consoleErrors.length > 0) {
      errors.push(`[${relPath}] smoke-render: ${consoleErrors.length} console error(s): ${consoleErrors.join(' | ')}`);
    }
    if (pageErrors.length > 0) {
      errors.push(`[${relPath}] smoke-render: ${pageErrors.length} uncaught page error(s): ${pageErrors.join(' | ')}`);
    }

    const populatedCount = await page.evaluate(() => {
      const sections = Array.from(document.querySelectorAll('[data-section]'));
      return sections.filter((el) => el.innerHTML.trim().length > 0).length;
    });
    const totalSections = await page.evaluate(() => document.querySelectorAll('[data-section]').length);

    if (totalSections > 0 && populatedCount === 0) {
      errors.push(`[${relPath}] smoke-render: 0 of ${totalSections} [data-section] elements populated — Report.init likely did not run`);
    }

    // Rendered-DOM link check: catches internal links built at runtime by
    // Report.init (e.g. the `links`/productChanges file-link data), which
    // never appear as literal <a href="..."> in the static HTML source.
    const siteOrigin = new URL(baseUrl).origin;
    const renderedHrefs = await page.evaluate((origin) => {
      return Array.from(document.querySelectorAll('a[href]'))
        .map((a) => a.href)
        .filter((href) => href.startsWith(origin));
    }, siteOrigin);

    for (const absoluteUrl of renderedHrefs) {
      const urlPath = decodeURIComponent(new URL(absoluteUrl).pathname);
      const resolved = resolvePath(REPO_ROOT, urlPath.replace(/^\/+/, ''));
      // Mirror checkInternalLinks: a link to a directory only resolves if
      // that directory has an index.html — existsSync(resolved) alone would
      // wrongly pass a directory that exists but has no index page.
      const ok = existsSync(resolved)
        ? (statSync(resolved).isDirectory() ? existsSync(join(resolved, 'index.html')) : true)
        : false;
      if (!ok) {
        errors.push(`[${relPath}] rendered internal link does not resolve: "${absoluteUrl}" -> ${resolved}`);
      }
    }
  } catch (err) {
    // A goto timeout/navigation exception must not abort the whole run —
    // record it against this file and let the remaining files still get
    // validated.
    errors.push(`[${relPath}] smoke-render: exception during navigation/evaluation: ${err}`);
  } finally {
    await page.close();
  }

  return errors;
}

function startStaticServer(root) {
  const MIME = {
    '.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript',
    '.mjs': 'application/javascript', '.json': 'application/json',
    '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg',
  };
  const server = createServer((req, res) => {
    try {
      const urlPath = decodeURIComponent(req.url.split('?')[0]);
      // No favicon is shipped for this static site; Chromium auto-requests
      // it on every page load and a 404 would otherwise register as a
      // console error indistinguishable from a real bug. Answer 204.
      if (urlPath === '/favicon.ico') {
        res.writeHead(204);
        res.end();
        return;
      }
      const filePath = join(root, urlPath);
      // startsWith(root) alone would also match a sibling directory that
      // happens to share root's prefix (e.g. root + "-secrets"); require an
      // exact match or a path separator boundary.
      if (filePath !== root && !filePath.startsWith(root + sep)) {
        res.writeHead(403);
        res.end();
        return;
      }
      const data = readFileSync(filePath);
      res.writeHead(200, { 'Content-Type': MIME[extname(filePath)] || 'application/octet-stream' });
      res.end(data);
    } catch {
      res.writeHead(404);
      res.end('not found');
    }
  });
  return new Promise((resolvePromise, reject) => {
    // Without an error listener, a bind failure leaves this promise pending
    // forever — the run hangs with no diagnostic instead of failing fast.
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => resolvePromise(server));
  });
}

async function main() {
  const changed = discoverChangedReportFiles();
  if (changed.length === 0) {
    console.log('No changed report index.html files to validate. Skipping.');
    return;
  }

  console.log(`Validating ${changed.length} report file(s):\n  ${changed.join('\n  ')}\n`);

  let allErrors = [];

  for (const relPath of changed) {
    const abs = resolvePath(REPO_ROOT, relPath);
    if (!existsSync(abs)) {
      // Not an error: a deleted/renamed report is a legitimate change and
      // must not fail validation. discoverChangedReportFiles() already
      // excludes deletions via --diff-filter=d; this only fires for an
      // explicit CLI arg naming a path that no longer exists.
      console.log(`[${relPath}] file does not exist (deleted?) — skipping static checks`);
      continue;
    }
    const html = readFileSync(abs, 'utf8');
    const fileDir = dirname(abs);
    // Strip comments/script/style bodies once and reuse everywhere: a link
    // or asset path mentioned only inside an HTML comment or inline JS/CSS
    // string is not a live reference and must not be checked as one.
    const stripped = stripScriptAndStyleBodies(html);

    allErrors = allErrors.concat(checkHtmlStructure(html, stripped, relPath));
    allErrors = allErrors.concat(checkLocalAssetPaths(stripped, relPath, fileDir));
    allErrors = allErrors.concat(checkInternalLinks(stripped, relPath, fileDir));
  }

  const existingFiles = changed.filter((f) => existsSync(resolvePath(REPO_ROOT, f)));
  if (existingFiles.length > 0) {
    // Server AND browser launch both live inside the try/finally: a
    // chromium.launch() failure (missing browser, sandbox error, OOM) must
    // still close the still-listening HTTP server, or that open server
    // keeps the event loop alive and the CI job hangs instead of failing.
    const server = await startStaticServer(REPO_ROOT);
    let browser;
    try {
      const port = server.address().port;
      const baseUrl = `http://127.0.0.1:${port}`;
      browser = await chromium.launch();
      for (const relPath of existingFiles) {
        const errs = await smokeRenderCheck(browser, baseUrl, relPath);
        allErrors = allErrors.concat(errs);
      }
    } finally {
      if (browser) await browser.close();
      server.close();
    }
  }

  if (allErrors.length > 0) {
    console.error(`\n${allErrors.length} problem(s) found:\n`);
    for (const e of allErrors) console.error(' - ' + e);
    process.exitCode = 1;
    return;
  }

  console.log('All changed report files passed structural, path, and smoke-render checks.');
}

main().catch((err) => {
  console.error('validate-reports.mjs crashed:', err);
  process.exitCode = 1;
});
