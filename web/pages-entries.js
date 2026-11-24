/* Direct static bucket transport for /api/entry and /api/entry-layout on GitHub Pages. */
const siteBase = new URL('.', import.meta.url);
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const MAX_BUCKETS_CACHE = 96;
const VALID_KEY = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const VALID_WORK = /^[a-z0-9]+(?:-[a-z0-9]+)*:.{1,300}$/;
const RULESETS = new Set(['5r', '5e', '待核']);
const CONTENT = new Set(['official', 'partnered', 'unverified', 'curated', 'community']);
const SINGLE_VALUE_KEYS = ['libraries', 'versions', 'prefer', 'rulesets', 'content'];
const TOUR_ENTRY_AT = /^([a-z0-9]+(?:-[a-z0-9]+)*):((?:ver_|o5e_)[A-Za-z0-9_.:-]+)$/;

let configPromise = null;
const bucketCache = new Map();

export function entryBucket(id, buckets) {
  let h = 0x811c9dc5;
  for (const byte of encoder.encode(id)) {
    h = Math.imul(h ^ byte, 0x01000193) >>> 0;
  }
  return h % buckets;
}

function parseAttrConfig() {
  const raw = globalThis.document?.documentElement?.getAttribute('data-anna-entries');
  if (!raw) return null;
  const [countRaw, encRaw] = raw.split(':');
  const buckets = Number(countRaw);
  const encoding = encRaw || 'gzip';
  if (!Number.isSafeInteger(buckets) || buckets < 1 || buckets > 4096) return null;
  if (encoding !== 'gzip' && encoding !== 'identity') return null;
  return {buckets, encoding};
}

async function readConfig() {
  const inline = parseAttrConfig();
  if (inline) return inline;
  if (!configPromise) {
    configPromise = fetch(new URL('pages-entries/index.json', siteBase), {mode:'same-origin'})
      .then(async response => {
        if (!response.ok) return null;
        const data = await response.json();
        if (!data || data.schema !== 1 || !Number.isSafeInteger(data.buckets) || data.buckets < 1 || data.buckets > 4096) return null;
        const encoding = data.encoding || 'gzip';
        if (encoding !== 'gzip' && encoding !== 'identity') return null;
        return {buckets: data.buckets, encoding};
      })
      .catch(() => null)
      .then(cfg => {
        if (!cfg) configPromise = null;
        return cfg;
      });
  }
  return configPromise;
}

async function decodeBucket(response, encoding) {
  if (encoding === 'identity') return response.json();
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (typeof DecompressionStream === 'function') {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    return new Response(stream).json();
  }
  const {gunzipSync} = await import(new URL('pages-engine/fflate.mjs', siteBase).href);
  return JSON.parse(decoder.decode(gunzipSync(bytes)));
}

function loadBucket(url, encoding) {
  if (bucketCache.has(url)) {
    const cached = bucketCache.get(url);
    bucketCache.delete(url);
    bucketCache.set(url, cached);
    return cached;
  }
  const pending = fetch(url, {mode:'same-origin'})
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status} ${url}`);
      return decodeBucket(response, encoding);
    })
    .then(data => {
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error(`Invalid entry bucket ${url}`);
      return data;
    })
    .catch(error => {
      if (bucketCache.get(url) === pending) bucketCache.delete(url);
      throw error;
    });
  if (bucketCache.size >= MAX_BUCKETS_CACHE) {
    bucketCache.delete(bucketCache.keys().next().value);
  }
  bucketCache.set(url, pending);
  return pending;
}

function waitWithAbort(promise, signal) {
  if (!signal) return promise;
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(signal.reason);
    signal.addEventListener('abort', onAbort, {once:true});
    promise.then(
      value => { signal.removeEventListener('abort', onAbort); resolve(value); },
      error => { signal.removeEventListener('abort', onAbort); reject(error); },
    );
  });
}

function parseEntryScope(searchParams) {
  for (const key of SINGLE_VALUE_KEYS) {
    if (searchParams.getAll(key).length > 1) return null;
  }
  const versions = searchParams.get('versions') || 'stacked';
  if (versions !== 'stacked' && versions !== 'each') return null;
  const prefer = searchParams.get('prefer') || '5r';
  if (prefer !== '5r' && prefer !== '5e') return null;

  const rawRulesets = searchParams.get('rulesets');
  if (rawRulesets !== null) {
    const items = rawRulesets.split(',').filter(Boolean);
    if (!items.length || items.some(v => !RULESETS.has(v))) return null;
  }
  const rawContent = searchParams.get('content');
  if (rawContent !== null) {
    const items = rawContent.split(',').filter(Boolean);
    if (!items.length || items.some(v => !CONTENT.has(v))) return null;
  }
  for (const key of ['hide', 'lead']) {
    const values = searchParams.getAll(key);
    if (values.length > 4000 || values.some(v => !VALID_WORK.test(v))) return null;
  }

  let libraries = ['5echm'];
  const rawLibs = searchParams.get('libraries');
  if (rawLibs !== null) {
    const requested = [...new Set(rawLibs.split(',').filter(Boolean))];
    if (requested.some(k => !VALID_KEY.test(k))) return null;
    if (requested.length) libraries = requested;
  }
  return {libraries, versions};
}

function needsCrossLibraryVersions(scope, record) {
  if (!record.has_extra_versions || scope.versions !== 'stacked') return false;
  return scope.libraries.length > 1 || scope.libraries[0] !== '5echm';
}

export async function request(path, {signal} = {}) {
  signal?.throwIfAborted();
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//')) return null;
  const url = new URL(path, 'https://anna.invalid');
  const tourLayout = url.pathname === '/api/tour-layout';
  if (!tourLayout && url.pathname !== '/api/entry' && url.pathname !== '/api/entry-layout') return null;

  const scope = parseEntryScope(url.searchParams);
  if (!scope) return null;

  let id = '', collection = null;
  if (tourLayout) {
    if (url.searchParams.getAll('at').length > 1) return null;
    const match = TOUR_ENTRY_AT.exec(url.searchParams.get('at') || '');
    if (!match) return null;
    collection = match[1];
    id = match[2];
    if (!scope.libraries.includes(collection)) return null;
  } else {
    id = url.searchParams.get('id') || '';
    if (!id) {
      return Response.json({error:'条目不存在'}, {status:404, headers:{'X-Anna-Entry-Bundle':'1'}});
    }
  }

  const config = await waitWithAbort(readConfig(), signal);
  signal?.throwIfAborted();
  if (!config) return null;

  const bucketName = entryBucket(id, config.buckets).toString(16).padStart(3, '0') + '.bin';
  const bucketURL = new URL(`pages-entries/${bucketName}`, siteBase).href;

  let bucket;
  try {
    bucket = await waitWithAbort(loadBucket(bucketURL, config.encoding), signal);
  } catch (error) {
    if (signal?.aborted) throw error;
    return null;
  }
  signal?.throwIfAborted();

  if (!Object.hasOwn(bucket, id)) {
    if (tourLayout) return null;
    return Response.json({error:'条目不存在'}, {status:404, headers:{'X-Anna-Entry-Bundle':'1'}});
  }
  const record = bucket[id];
  if (tourLayout) {
    if (record.detail?.collection !== collection) return null;
    return Response.json(record.tour_layout ?? record.layout, {headers:{'X-Anna-Entry-Bundle':'1'}});
  }
  if (url.pathname === '/api/entry-layout') {
    return Response.json(record.layout, {headers:{'X-Anna-Entry-Bundle':'1'}});
  }
  const headers = {'X-Anna-Entry-Bundle':'1'};
  if (needsCrossLibraryVersions(scope, record)) {
    headers['X-Anna-Entry-Versions-Pending'] = '1';
  }
  return Response.json(record.detail, {headers});
}

export function resetCache() {
  configPromise = null;
  bucketCache.clear();
}
