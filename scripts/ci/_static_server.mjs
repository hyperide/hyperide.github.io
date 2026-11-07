/**
 * Static file server shared by the report render checks (127.0.0.1 only, files below one root).
 * It serves the MIME types of the report assets and answers HTTP byte ranges the way GitHub Pages does:
 * 206 for a range, a suffix range counted from the end, 416 when no byte of the file is in range, and a
 * malformed or reversed range ignored (plain 200), so the recordings can be loaded and seeked by every browser.
 */
import { readFileSync, statSync } from 'node:fs';
import { extname, join, resolve, sep } from 'node:path';

const MIME = {
  '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.mp4': 'video/mp4', '.webm': 'video/webm',
};

function parseRange(header, size) {
  const m = /^bytes=(\d*)-(\d*)$/.exec(header ?? '');
  if (!m || (m[1] === '' && m[2] === '')) return null;
  if (m[1] === '') {
    const n = Number(m[2]);
    return n === 0 || size === 0 ? { unsatisfiable: true } : { start: Math.max(size - n, 0), end: size - 1 };
  }
  const start = Number(m[1]);
  const end = m[2] === '' ? size - 1 : Math.min(Number(m[2]), size - 1);
  if (m[2] !== '' && Number(m[2]) < start) return null;
  return start >= size ? { unsatisfiable: true } : { start, end };
}

function respond(req, res, body, type) {
  const range = parseRange(req.headers.range, body.length);
  if (range?.unsatisfiable) {
    return res.writeHead(416, { 'Content-Range': `bytes */${body.length}` }).end();
  }
  if (range) {
    return res
      .writeHead(206, { 'Content-Type': type, 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${range.start}-${range.end}/${body.length}`, 'Content-Length': range.end - range.start + 1 })
      .end(body.subarray(range.start, range.end + 1));
  }
  return res.writeHead(200, { 'Content-Type': type, 'Accept-Ranges': 'bytes', 'Content-Length': body.length }).end(body);
}

/** Request handler for a static root: favicon 204, traversal 403, directory index.html, missing file 404. */
export function staticHandler(root) {
  const base = resolve(root);
  return (req, res) => {
    try {
      const path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
      // No favicon is shipped for this static site; a 404 would register as a console error indistinguishable from a real bug.
      if (path === '/favicon.ico') return res.writeHead(204).end();
      let file = resolve(base, '.' + path);
      if (file !== base && !file.startsWith(base + sep)) return res.writeHead(403).end();
      if (statSync(file).isDirectory()) {
        // Like GitHub Pages: a folder URL without its trailing slash is redirected, so relative asset paths keep working.
        if (!path.endsWith('/')) return res.writeHead(301, { Location: `${path}/` }).end();
        file = join(file, 'index.html');
      }
      return respond(req, res, readFileSync(file), MIME[extname(file)] ?? 'application/octet-stream');
    } catch {
      return res.writeHead(404).end('not found');
    }
  };
}
