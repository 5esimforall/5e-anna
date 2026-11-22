/* Pages renders remote image placeholders without routing media through Python. */
const siteBase = new URL('.', import.meta.url);
const publishedMediaBase = new URL('our5etools-media/', siteBase).pathname;
const figureSelector = 'figure[data-lazy-media]:not([data-media-kind="audio"])';
const imageCDNs = [
  'https://5e.kiwee.top/img/',
  'https://raw.githubusercontent.com/5etools-mirror-3/5etools-img/main/'
];
const audioPath = /\.(?:mp3|ogg|opus|wav|m4a|aac|flac|weba)$/i;
const attempted = new WeakSet();
let manifest;

function readManifest() {
  return manifest ??= fetch(new URL('our5etools-media-remotes.json', siteBase))
    .then(response => response.ok ? response.json() : null)
    .catch(() => null);
}

function mediaPath(source, mediaBase) {
  if (!source?.startsWith(mediaBase) || /[?#]/.test(source)) return '';
  try {
    const path = decodeURIComponent(source.slice(mediaBase.length));
    const parts = path.split('/');
    if (parts.length < 2 || !['img', 'ext'].includes(parts[0]) || audioPath.test(path) ||
        parts.some(part => !part || part.startsWith('.') || /[\\\u0000-\u001f\u007f]/.test(part))) return '';
    return path;
  } catch { return ''; }
}

function imageSources(source, path, cached, entries) {
  const urls = new Set(cached.has(path) ? [source] : []);
  const remote = entries?.[path];
  for (const candidate of Array.isArray(remote) ? remote : [remote]) {
    if (typeof candidate !== 'string') continue;
    try {
      const url = new URL(candidate);
      if (url.protocol === 'https:' && !url.username && !url.password && !audioPath.test(url.pathname)) urls.add(url.href);
    } catch { /* The other candidates remain available. */ }
  }
  if (path.startsWith('img/')) {
    const encoded = path.slice(4).split('/').map(encodeURIComponent).join('/');
    for (const base of imageCDNs) urls.add(base + encoded);
  }
  return [...urls];
}

function showImage(figure, source, urls, measure) {
  const badge = figure.querySelector('.o5e-media-badge');
  const img = figure.ownerDocument.createElement('img');
  img.alt = figure.getAttribute('data-media-alt') || '';
  img.loading = 'lazy';
  img.decoding = 'async';
  img.referrerPolicy = 'no-referrer';
  let index = 0;
  function loaded() {
    img.removeEventListener('error', failed);
    if (badge) badge.style.display = 'none';
    figure.classList.remove('o5e-media-remote');
    measure?.();
  }
  function failed() {
    if (++index < urls.length) {
      img.src = urls[index];
      return;
    }
    img.removeEventListener('load', loaded);
    img.remove();
    figure.setAttribute('data-lazy-media', source);
    measure?.();
  }
  img.addEventListener('load', loaded, {once:true});
  img.addEventListener('error', failed);
  // Claim the placeholder so the chapter's cached-media handler cannot start a second image.
  figure.removeAttribute('data-lazy-media');
  img.src = urls[index];
  figure.prepend(img);
}

// Resolves after the image elements are set up. Each lazy image reports its own completion.
export async function hydrateImages(root, {mediaBase = publishedMediaBase, cachedMedia, measure} = {}) {
  if (mediaBase !== publishedMediaBase) return;
  const figures = [...root.querySelectorAll(figureSelector)];
  if (!figures.length) return;
  const connected = root.isConnected;
  const data = await readManifest();
  if (connected && !root.isConnected) return;
  const cached = new Set([
    ...(Array.isArray(data?.cachedMedia) ? data.cachedMedia : []), ...(cachedMedia || [])
  ]);
  for (const figure of figures) {
    if (!root.contains(figure) || attempted.has(figure) || figure.querySelector('img')) continue;
    const source = figure.getAttribute('data-lazy-media');
    const path = mediaPath(source, mediaBase);
    if (!path) continue;
    const urls = imageSources(source, path, cached, data?.entries);
    if (!urls.length) continue;
    attempted.add(figure);
    showImage(figure, source, urls, measure);
  }
}

// Exported chapters load this module directly, including when opened on their own.
const bindings = globalThis.document?.getElementById('anna-bindings');
if (bindings) {
  try {
    const mediaBase = JSON.parse(bindings.textContent).reader?.media_base;
    if (mediaBase === publishedMediaBase) hydrateImages(document, {mediaBase}).catch(() => {});
  } catch { /* The chapter still has its original placeholder. */ }
}
