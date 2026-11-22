/* GitHub Pages transport for the server's GET routes. One worker serves requests for this document. */
const siteBase = new URL('.', import.meta.url);
let worker, nextId = 0;
const pending = new Map();

const jsonObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const homeRoutes = {
  '/api/meta': body => jsonObject(body.fields) && jsonObject(body.types) && Array.isArray(body.collections),
  '/api/facets': ({schema, groups, scope_total}) => jsonObject(schema) &&
    ['columns', 'flags', 'sorts'].every(key => Array.isArray(schema[key])) &&
    Array.isArray(groups) && groups.every(group => Array.isArray(group?.options)) && Number.isInteger(scope_total),
  '/api/entries?offset=0&limit=150': body => Array.isArray(body.rows) && Number.isInteger(body.total),
  '/api/library': body => Array.isArray(body.collections) && body.collections.every(jsonObject)
};
let homeData;

async function loadHome(filename) {
  try {
    const response = await fetch(new URL(filename, siteBase), {mode:'same-origin'});
    if (response.ok) return await response.json();
  } catch {
    // A missing or unreadable artifact leaves these requests on the normal transport.
  }
}

function waitForHome(signal) {
  if (!signal) return homeData;
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener('abort', abort, {once:true});
    homeData.then(data => {
      signal.removeEventListener('abort', abort);
      resolve(data);
    });
  });
}

async function homeResponse(path, filename, signal) {
  if (!/^pages-home(?:\.[a-f0-9]{16})?\.json$/.test(filename) || !Object.hasOwn(homeRoutes, path)) return;
  homeData ??= loadHome(filename);
  const data = await waitForHome(signal);
  if (!jsonObject(data) || !Object.hasOwn(data, path)) return;
  const body = data[path];
  if (jsonObject(body) && homeRoutes[path](body)) return Response.json(body, {headers:{'X-Anna-Home-Cache':'1'}});
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

export async function request(path, {signal, homeCache} = {}) {
  signal?.throwIfAborted();
  if (homeCache) {
    const cached = await homeResponse(path, homeCache, signal);
    signal?.throwIfAborted();
    if (cached) return cached;
  }
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

async function showSource(view) {
  const labels = {
    zh:{title:'页面文本', home:'← 条目查询', hint:'这是提取后的文本，高亮为所选证据范围。', loading:'正在读取页面文本…', error:'无法读取页面文本。'},
    en:{title:'Page text', home:'← Search entries', hint:'Extracted text with the selected evidence highlighted.', loading:'Loading page text…', error:'Unable to load page text.'}
  };
  const ui = labels[document.documentElement.lang.startsWith('en') ? 'en' : 'zh'];
  document.title = `${ui.title} · 5e Anna`;
  for (const element of view.querySelectorAll('[data-source-label]')) element.textContent = ui[element.dataset.sourceLabel];
  const status = view.querySelector('[role="status"]');
  try {
    const response = await request('/source' + location.search);
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error || error.code || response.status);
    }
    // The server returns escaped plain text with one mark. Read it in an inert template and rebuild that view.
    const template = document.createElement('template');
    template.innerHTML = await response.text();
    const text = template.content.querySelector('pre'), evidence = text?.querySelector('#evidence');
    if (!evidence) throw new Error();
    const pre = document.createElement('pre');
    const mark = document.createElement('mark'); mark.id = 'evidence'; mark.textContent = evidence.textContent;
    pre.append(...Array.from(text.childNodes, node => node === evidence ? mark : node.textContent));
    view.querySelector('[data-source-path]').textContent = new URLSearchParams(location.search).get('path');
    status.replaceWith(pre);
    if (location.hash === '#evidence') mark.scrollIntoView();
  } catch (error) {
    status.setAttribute('role', 'alert');
    status.textContent = [ui.error, error.message].filter(Boolean).join(' ');
  }
}

const sourceView = document.querySelector('[data-source-view]');
if (sourceView) showSource(sourceView);
