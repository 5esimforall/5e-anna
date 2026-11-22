/* GitHub Pages transport for the server's GET routes. One worker serves requests for this document. */
const siteBase = new URL('.', import.meta.url);
let worker, nextId = 0;
const pending = new Map();

function connect() {
  if (worker) return worker;
  const current = new Worker(new URL('pages-worker.js', siteBase));
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
