// Checks worker.ts without a network: the redirect, routing, 404 and header rules that cannot all be exercised on
// workers.dev (the www host and plain http only reach the Worker after the cutover). Run: node --test worker.test.mjs
// The expected answers are what Vercel gave marvbrownfilms.com on 31 Oct 2026 (docs/cloudflare/PARITY.md).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import worker from './worker.ts';

// A stand-in for the static assets binding (html_handling and not_found_handling "none"): exact file names only.
const files = new Map([
  ['/index.html', { type: 'text/html', body: 'home' }],
  ['/_next/static/runtime/main-1.js', { type: 'text/javascript; charset=utf-8', body: 'js' }],
  ['/static/logos/logo-black.png', { type: 'image/png', body: 'png' }],
]);
const requested = [];
const env = {
  ASSETS: {
    async fetch(request) {
      const { pathname } = new URL(request.url);
      requested.push(pathname);
      const hit = files.get(pathname);
      return hit
        ? new Response(hit.body, { headers: { 'content-type': hit.type, 'cache-control': 'public, max-age=0, must-revalidate', etag: '"x"' } })
        : new Response(null, { status: 404 });
    },
  },
};
const get = (url, init) => worker.fetch(new Request(url, init), env);
const HSTS = 'max-age=63072000';
const CACHE = 'public, max-age=0, must-revalidate';

test('plain http 308s to https on the same host, keeping path and query, with no HSTS header', async () => {
  for (const host of ['marvbrownfilms.com', 'www.marvbrownfilms.com']) {
    const res = await get(`http://${host}/foo?x=1`);
    assert.equal(res.status, 308, host);
    assert.equal(res.headers.get('location'), `https://${host}/foo?x=1`, host);
    assert.equal(res.headers.get('strict-transport-security'), null);
  }
  // before anything else: a duplicate slash survives the first hop, as it did on Vercel
  const res = await get('http://www.marvbrownfilms.com//x?q=1');
  assert.equal(res.headers.get('location'), 'https://www.marvbrownfilms.com//x?q=1');
});

test('www 307s to the apex with path and query, as text/plain "Redirecting..."', async () => {
  const res = await get('https://www.marvbrownfilms.com/about?x=1&y=2');
  assert.equal(res.status, 307);
  assert.equal(res.headers.get('location'), 'https://marvbrownfilms.com/about?x=1&y=2');
  assert.equal(res.headers.get('content-type'), 'text/plain');
  assert.equal(res.headers.get('cache-control'), CACHE);
  assert.equal(res.headers.get('strict-transport-security'), HSTS);
  assert.equal(await res.text(), 'Redirecting...\n');
  const root = await get('https://www.marvbrownfilms.com');
  assert.equal(root.status, 307);
  assert.equal(root.headers.get('location'), 'https://marvbrownfilms.com/');
});

test('duplicate slashes 308 to the collapsed path (a relative Location, as Vercel sent it), before the www redirect', async () => {
  const cases = [
    ['https://marvbrownfilms.com//x?q=1', '/x?q=1'],
    ['https://marvbrownfilms.com//', '/'],
    ['https://marvbrownfilms.com/x//y/?q=1', '/x/y/?q=1'],
    ['https://marvbrownfilms.com/static//logos/logo-black.png', '/static/logos/logo-black.png'],
    ['https://www.marvbrownfilms.com//x?q=1', '/x?q=1'],
  ];
  for (const [from, to] of cases) {
    const res = await get(from);
    assert.equal(res.status, 308, from);
    assert.equal(res.headers.get('location'), to, from);
    assert.equal(res.headers.get('content-type'), 'text/plain', from);
  }
});

test('the apex serves the home page at /, /index and with a trailing slash or a query', async () => {
  for (const path of ['/', '/index', '/index/', '/?x=1', '/index?x=1']) {
    requested.length = 0;
    const res = await get(`https://marvbrownfilms.com${path}`);
    assert.equal(res.status, 200, path);
    assert.equal(await res.text(), 'home', path);
    assert.equal(res.headers.get('content-type'), 'text/html; charset=utf-8', path);
    assert.deepEqual(requested, ['/index.html'], path);
  }
});

test('the workers.dev address serves the site itself, without a www or http redirect', async () => {
  const res = await get('https://marv-brown-films-web.chewybytes.workers.dev/');
  assert.equal(res.status, 200);
  assert.equal(await res.text(), 'home');
});

test('every successful response carries Vercel\'s cache-control and HSTS', async () => {
  for (const path of ['/', '/_next/static/runtime/main-1.js', '/static/logos/logo-black.png']) {
    const res = await get(`https://marvbrownfilms.com${path}`);
    assert.equal(res.status, 200, path);
    assert.equal(res.headers.get('cache-control'), CACHE, path);
    assert.equal(res.headers.get('strict-transport-security'), HSTS, path);
  }
});

test('JavaScript keeps Vercel\'s content type; images keep their own', async () => {
  const js = await get('https://marvbrownfilms.com/_next/static/runtime/main-1.js');
  assert.equal(js.headers.get('content-type'), 'application/javascript; charset=utf-8');
  const png = await get('https://marvbrownfilms.com/static/logos/logo-black.png/');
  assert.equal(png.status, 200, 'a trailing slash on a file is ignored, as on Vercel');
  assert.equal(png.headers.get('content-type'), 'image/png');
});

test('index.html is not a page; neither are directories, other cases, or unknown paths', async () => {
  for (const path of ['/index.html', '/index.html/', '/INDEX', '/Index', '/static/', '/static/logos/', '/_next/', '/robots.txt', '/sitemap.xml', '/favicon.ico', '/static/favicon.ico', '/about', '/a/b/c/d', '/_next/static/development/pages/index.js']) {
    const res = await get(`https://marvbrownfilms.com${path}`, { headers: { 'cf-ray': 'abc123-LHR' } });
    assert.equal(res.status, 404, path);
    assert.equal(res.headers.get('content-type'), 'text/plain; charset=utf-8', path);
    assert.equal(res.headers.get('cache-control'), CACHE, path);
    assert.equal(res.headers.get('strict-transport-security'), HSTS, path);
    assert.equal(await res.text(), 'The page could not be found\n\nNOT_FOUND\n\nabc123-LHR\n', path);
  }
});

test('other methods are refused, GET and HEAD are not', async () => {
  const post = await get('https://marvbrownfilms.com/', { method: 'POST', body: 'x' });
  assert.equal(post.status, 405);
  assert.equal(post.headers.get('allow'), 'GET, HEAD');
  const head = await get('https://marvbrownfilms.com/', { method: 'HEAD' });
  assert.equal(head.status, 200);
});
