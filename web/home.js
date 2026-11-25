/* Cached GET responses for the default Anna homepage on either host. */
import {LIBS_RE, getCachedArtifact, jsonObject, parseCacheRequest} from './cache-util.js';

const routes = {
  '/api/meta': body => jsonObject(body.fields) && jsonObject(body.types) && Array.isArray(body.collections),
  '/api/facets': ({schema, groups, scope_total}) => jsonObject(schema) &&
    ['columns', 'flags', 'sorts'].every(key => Array.isArray(schema[key])) &&
    Array.isArray(groups) && groups.every(group => Array.isArray(group?.options)) && Number.isInteger(scope_total),
  '/api/entries': body => Array.isArray(body.rows) && Number.isInteger(body.total),
  '/api/library': body => Array.isArray(body.collections) && body.collections.every(jsonObject)
};
const artifacts = new Map();

function canonicalPath(path) {
  const parsed = parseCacheRequest(path);
  if (!parsed) return null;
  const {pathname, searchParams, counts} = parsed;
  if (!Object.hasOwn(routes, pathname)) return null;
  if (pathname === '/api/library') {
    return counts.size === 0 ? {key: pathname, pathname} : null;
  }
  const libs = searchParams.get('libraries');
  if (libs !== null && !LIBS_RE.test(libs)) return null;
  if (pathname === '/api/meta' || pathname === '/api/facets') {
    const allowed = libs !== null ? 1 : 0;
    if (counts.size !== allowed) return null;
    return {key: libs !== null ? `${pathname}?libraries=${libs}` : pathname, pathname};
  }
  // /api/entries: requires offset=0 and limit=150, plus optional libraries
  if (searchParams.get('offset') !== '0' || searchParams.get('limit') !== '150') return null;
  const expected = 2 + (libs !== null ? 1 : 0);
  if (counts.size !== expected) return null;
  return {
    key: libs !== null ? `/api/entries?offset=0&limit=150&libraries=${libs}` : '/api/entries?offset=0&limit=150',
    pathname,
  };
}

export async function request(path, filename, signal) {
  signal?.throwIfAborted();
  const parsed = canonicalPath(path);
  if (!/^home\.[a-f0-9]{16}\.json$/.test(filename) || !parsed) return;
  // Share the fetch while letting each caller cancel its own wait.
  const data = await getCachedArtifact(artifacts, filename, import.meta.url, signal);
  signal?.throwIfAborted();
  if (!jsonObject(data) || !Object.hasOwn(data, parsed.key)) return;
  const body = data[parsed.key];
  if (jsonObject(body) && routes[parsed.pathname](body)) return Response.json(body, {headers:{'X-Anna-Home-Cache':'1'}});
}
