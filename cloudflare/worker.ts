// Serves the captured copy of https://marvbrownfilms.com (site/) and keeps what Vercel did for it, as checked on
// 31 October 2026 (docs/cloudflare/PARITY.md):
//   http://anything                  308 to the same URL on https
//   //  in a path                    308 to the path with the duplicates collapsed (same host)
//   https://www.marvbrownfilms.com   307 to https://marvbrownfilms.com, same path and query (the apex is canonical)
//   /, /index and a trailing slash   the home page; /index.html is NOT a page (404)
//   anything the site does not have  404 as plain text, "The page could not be found / NOT_FOUND / <request id>"
//   every response                   cache-control: public, max-age=0, must-revalidate and HSTS (two years)
// Nothing else is served: the 2019 development bundles that Vercel also exposed (webpack hot updates, dev builds,
// source maps) are deliberately not part of site/, so they answer 404 here.
interface Env {
  ASSETS: { fetch(request: Request): Promise<Response> };
}

const APEX = 'marvbrownfilms.com';
const WWW = `www.${APEX}`;
const HSTS = 'max-age=63072000';
const CACHE = 'public, max-age=0, must-revalidate';

// Vercel's content types for what the site has (the assets binding spells JavaScript differently).
const TYPES: Record<string, string> = {
  html: 'text/html; charset=utf-8',
  js: 'application/javascript; charset=utf-8',
  png: 'image/png',
};

const redirect = (status: 307 | 308, location: string, secure = true): Response => {
  const headers: Record<string, string> = {
    location,
    'content-type': 'text/plain',
  };
  if (secure) {
    headers['cache-control'] = CACHE;
    headers['strict-transport-security'] = HSTS;
  }
  return new Response('Redirecting...\n', { status, headers });
};

const text = (status: number, body: string, extra: Record<string, string> = {}): Response =>
  new Response(body, {
    status,
    headers: {
      'content-type': 'text/plain; charset=utf-8',
      'cache-control': CACHE,
      'strict-transport-security': HSTS,
      ...extra,
    },
  });

const notFound = (request: Request): Response =>
  text(404, `The page could not be found\n\nNOT_FOUND\n\n${request.headers.get('cf-ray') ?? 'unknown'}\n`);

const worker = {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return text(405, 'Method Not Allowed\n', { allow: 'GET, HEAD' });
    }
    const url = new URL(request.url);

    // 1. http -> https, whatever the host or path (Vercel did this before anything else)
    if (url.protocol === 'http:') {
      url.protocol = 'https:';
      return redirect(308, url.toString(), false);
    }

    // 2. duplicate slashes, on the host that was asked for (Vercel's Location is relative here, not absolute)
    if (url.pathname.includes('//')) {
      return redirect(308, `${url.pathname.replace(/\/{2,}/g, '/')}${url.search}`);
    }

    // 3. www -> apex (a temporary redirect, as on Vercel)
    if (url.hostname === WWW) {
      return redirect(307, `https://${APEX}${url.pathname}${url.search}`);
    }

    // 4. routing: a trailing slash is ignored, / and /index are the home page, index.html is an internal name
    const key = url.pathname.length > 1 ? url.pathname.replace(/\/+$/, '') : url.pathname;
    if (key === '/index.html') {
      return notFound(request);
    }
    const target = key === '/' || key === '/index' ? '/index.html' : key;

    const assetUrl = new URL(request.url);
    assetUrl.pathname = target;
    assetUrl.search = '';
    const asset = await env.ASSETS.fetch(new Request(assetUrl.toString(), { method: request.method, headers: request.headers }));
    if (asset.status === 404) {
      return notFound(request);
    }

    const headers = new Headers(asset.headers);
    const type = TYPES[target.split('.').pop() ?? ''];
    if (type) {
      headers.set('content-type', type);
    }
    headers.set('cache-control', CACHE);
    headers.set('strict-transport-security', HSTS);
    return new Response(asset.body, { status: asset.status, statusText: asset.statusText, headers });
  },
};

export default worker;
