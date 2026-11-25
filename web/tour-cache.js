/* Cached GET responses and curation skeleton for the tour on either host. */
import {LIBS_RE, getCachedArtifact, jsonObject, parseCacheRequest} from './cache-util.js';

const TOPIC_RE = /^[a-z][a-z0-9-]*$/;
const FILE_RE = /^tour\.[a-f0-9]{16}\.json$/;
const artifacts = new Map();

function canonicalPath(path, cachedSnapshot) {
  const parsed = parseCacheRequest(path);
  if (!parsed) return null;
  const {pathname, searchParams} = parsed;
  if (pathname === '/api/library') {
    return searchParams.size === 0 ? '/api/library' : null;
  }
  if (pathname !== '/api/tour' && pathname !== '/api/tour-layout') return null;
  if (!searchParams.has('at')) return null;
  const at = searchParams.get('at') ?? '';
  if (at !== '' && !TOPIC_RE.test(at)) return null;
  for (const key of searchParams.keys()) {
    if (key !== 'at' && key !== 'libraries' && key !== 'snapshot') return null;
  }
  const libs = searchParams.get('libraries');
  if (libs !== null && !LIBS_RE.test(libs)) return null;
  if (searchParams.has('snapshot')) {
    const snap = searchParams.get('snapshot') || '';
    if (cachedSnapshot && snap && snap !== cachedSnapshot) return null;
  }
  return libs !== null ? `${pathname}?at=${at}&libraries=${libs}` : `${pathname}?at=${at}`;
}

async function getArtifact(filename, signal) {
  signal?.throwIfAborted();
  if (!FILE_RE.test(filename)) return null;
  const data = await getCachedArtifact(artifacts, filename, import.meta.url, signal);
  signal?.throwIfAborted();
  return jsonObject(data) && data.schema === 1 ? data : null;
}

export async function request(path, filename, signal) {
  signal?.throwIfAborted();
  if (!FILE_RE.test(filename)) return;
  const data = await getArtifact(filename, signal);
  if (!data || !jsonObject(data.responses)) return;
  const key = canonicalPath(path, typeof data.snapshot === 'string' ? data.snapshot : '');
  if (!key || !Object.hasOwn(data.responses, key)) return;
  const body = data.responses[key];
  if (jsonObject(body)) return Response.json(body, {headers:{'X-Anna-Tour-Cache':'1'}});
}

export async function skeleton(filename, signal) {
  const data = await getArtifact(filename, signal);
  return data && jsonObject(data.skeleton) && Array.isArray(data.skeleton.sections) ? data.skeleton : null;
}
