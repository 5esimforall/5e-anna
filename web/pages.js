/* GitHub Pages transport for the server's GET routes. One worker serves requests for this document. */
const siteBase = new URL('.', import.meta.url);
let worker, nextId = 0, entriesLoader;
let active = null, warmRequested = false, warmSent = false;
const queue = [];

const PRIORITY_HIGH = 0;
const PRIORITY_MEDIUM = 1;
const PRIORITY_LOW = 2;

function priorityOf(path) {
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//')) return PRIORITY_HIGH;
  const pathname = new URL(path, 'https://anna.invalid').pathname;
  if (pathname === '/api/entry-versions' || pathname === '/api/meta' || pathname === '/api/library' ||
      pathname === '/api/entry' || pathname === '/api/entry-layout') return PRIORITY_HIGH;
  if (pathname === '/api/facets') return PRIORITY_LOW;
  return PRIORITY_MEDIUM;
}

async function entryBundleRequest(path, signal) {
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//')) return null;
  const pathname = new URL(path, 'https://anna.invalid').pathname;
  if (pathname !== '/api/entry' && pathname !== '/api/entry-layout') return null;
  entriesLoader ??= import(new URL('pages-entries.js', siteBase).href).catch(() => null);
  const loader = await entriesLoader;
  signal?.throwIfAborted();
  return loader ? loader.request(path, {signal}) : null;
}

function connect() {
  if (worker) return worker;
  const current = new Worker(new URL('pages-worker.js', siteBase), {type:'module'});
  worker = current;
  function fail(message) {
    if (worker !== current) return;
    worker = undefined;
    current.terminate();
    const error = new Error(message || 'The Pages worker failed.');
    const activeJob = active;
    active = null;
    activeJob?.cleanup?.();
    if (activeJob && !activeJob.aborted) activeJob.reject(error);
    while (queue.length) {
      const job = queue.shift();
      job.cleanup?.();
      if (!job.aborted) job.reject(error);
    }
    warmRequested = false;
    warmSent = false;
  }
  current.addEventListener('error', event => fail(event.message));
  current.addEventListener('messageerror', () => fail());
  current.addEventListener('message', ({data}) => {
    if (worker !== current) return;
    if (data.type === 'error') { fail(data.error); return; }
    if (!active || data.id !== active.id) return;
    const currentJob = active;
    active = null;
    currentJob.cleanup?.();
    if (!currentJob.aborted) {
      try {
        const {status, headers, body} = data;
        currentJob.resolve(new Response([204, 205, 304].includes(status) ? null : body, {status, headers}));
      } catch (error) {
        currentJob.reject(error);
      }
    }
    pump();
  });
  current.postMessage({type:'init', baseUrl:siteBase.href});
  return current;
}

function takeNext() {
  if (!queue.length) return null;
  let best = 0;
  for (let i = 1; i < queue.length; i++) {
    if (queue[i].priority < queue[best].priority) best = i;
  }
  return queue.splice(best, 1)[0];
}

function pump() {
  if (active) return;
  const job = takeNext();
  if (job) {
    active = job;
    try {
      connect().postMessage({id: job.id, path: job.path});
    } catch (error) {
      active = null;
      job.cleanup?.();
      if (!job.aborted) job.reject(error);
      pump();
    }
    return;
  }
  if (warmRequested && !warmSent) {
    warmSent = true;
    warmRequested = false;
    connect().postMessage({type:'warm'});
  }
}

export function warm() {
  if (warmSent) return;
  if (!active && !queue.length) {
    warmSent = true;
    connect().postMessage({type:'warm'});
  } else {
    warmRequested = true;
  }
}

export async function request(path, {signal} = {}) {
  signal?.throwIfAborted();
  const bundled = await entryBundleRequest(path, signal);
  if (bundled) return bundled;
  signal?.throwIfAborted();
  connect();
  const id = ++nextId, priority = priorityOf(path);
  return new Promise((resolve, reject) => {
    const job = {id, path, priority, aborted: false, resolve, reject, cleanup: null};
    const abort = () => {
      if (job.aborted) return;
      job.aborted = true;
      job.cleanup?.();
      const idx = queue.indexOf(job);
      if (idx !== -1) queue.splice(idx, 1);
      reject(signal.reason);
    };
    job.cleanup = () => signal?.removeEventListener('abort', abort);
    signal?.addEventListener('abort', abort, {once:true});
    queue.push(job);
    pump();
  });
}

export function resetState() {
  if (worker) worker.terminate();
  worker = undefined;
  nextId = 0;
  entriesLoader = undefined;
  active = null;
  warmRequested = false;
  warmSent = false;
  queue.length = 0;
}
