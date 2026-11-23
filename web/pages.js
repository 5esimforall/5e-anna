/* GitHub Pages transport for the server's GET routes. One worker serves requests for this document. */
const siteBase = new URL('.', import.meta.url);
let worker, nextId = 0;
const pending = new Map();

function connect() {
  if (worker) return worker;
  const current = new Worker(new URL('pages-worker.js', siteBase), {type:'module'});
  worker = current;
  function fail(message) {
    if (worker !== current) return;
    worker = undefined;
    current.terminate();
    const error = new Error(message || 'The Pages worker failed.');
    for (const entry of pending.values()) entry.reject(error);
    pending.clear();
  }
  current.addEventListener('error', event => fail(event.message));
  current.addEventListener('messageerror', () => fail());
  current.addEventListener('message', ({data}) => {
    if (worker !== current) return;
    if (data.type === 'error') { fail(data.error); return; }
    const entry = pending.get(data.id);
    if (!entry) return;
    try {
      const {status, headers, body} = data;
      entry.resolve(new Response([204, 205, 304].includes(status) ? null : body, {status, headers}));
    } catch (error) {
      entry.reject(error);
    }
  });
  current.postMessage({type:'init', baseUrl:siteBase.href});
  return current;
}

export function warm() {
  connect().postMessage({type:'warm'});
}

export async function request(path, {signal} = {}) {
  signal?.throwIfAborted();
  const current = connect(), id = ++nextId;
  return new Promise((resolve, reject) => {
    function finish(callback, value) {
      pending.delete(id);
      signal?.removeEventListener('abort', abort);
      callback(value);
    }
    const abort = () => finish(reject, signal.reason);
    pending.set(id, {resolve:value => finish(resolve, value), reject:error => finish(reject, error)});
    signal?.addEventListener('abort', abort, {once:true});
    try { current.postMessage({id, path}); }
    catch (error) { finish(reject, error); }
  });
}
