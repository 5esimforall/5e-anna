/* Reader bridge. Source content keeps its layout; navigation uses the app protocol.
   A versioned protocol coordinates the source document and application. Stella reads several collections on one
   shelf, so every address the bridge reports names its collection: `collection:path`, `collection:entry`. */
(() => {
  'use strict';
  const context = JSON.parse(document.getElementById('anna-bindings').textContent).reader;
  const pathname = typeof location !== 'undefined' && typeof location.pathname === 'string' ? location.pathname : '';
  const extMatch = pathname.match(/^\/([a-z0-9-]+)-book\//);
  const contentBase = extMatch ? '/' + extMatch[1] + '-book/' : (context?.content_base || '/book/');
  const definitionBase = context?.definition_base || (contentBase + 'definition/');
  const collection = context?.collection || (extMatch ? extMatch[1] : '5echm');
  const pagePrefixes = Array.isArray(context?.page_prefixes) && context.page_prefixes.length
    ? context.page_prefixes
    : ['topics/', 'books/', 'adventures/', 'brew-books/', 'brew-adventures/'];
  if (context?.presentation_resources?.some(asset => asset.id === 'webhelpTopicStyles'))
    document.documentElement.classList.add('webhelp-topic-document');
  if (parent === window) return;
  const channel = 'anna-reader', version = 2;
  const documentId = crypto.randomUUID();
  let navigationId, initialized = false, sizeFrame;
  const snapshot = context.snapshot;
  // The app owns scrolling. Natural document height is independent of its iframe viewport.
  const sizing = document.createElement('style');
  sizing.textContent = 'html,html body{min-height:0!important;height:auto!important}html{overflow-y:hidden!important}';
  document.head.append(sizing);
  const qualify = local => collection + ':' + local;
  const path = qualify(decodeURIComponent(
    location.pathname.startsWith(contentBase) ? location.pathname.slice(contentBase.length) : location.pathname.slice(1)
  ));
  const send = (type, detail = {}) => {
    parent.postMessage({channel, version, type, documentId, snapshot, path, navigationId, ...detail}, location.origin);
  };
  function height() {
    const body = document.body, rect = body.getBoundingClientRect(), style = getComputedStyle(body);
    return Math.max(1,Math.ceil(rect.top+scrollY+Math.max(rect.height,body.scrollHeight)+(parseFloat(style.marginBottom)||0)));
  }
  // Size reports carry where each definition starts, so the reader knows which one is in view.
  const definitions = () => window.annaSourceLinks?.definitionPositions() || [];
  function measure() {
    if (!initialized || sizeFrame) return;
    sizeFrame = requestAnimationFrame(() => { sizeFrame = 0; send('size',{height:height(),definitions:definitions()}); });
  }
  function target(href) {
    let url;
    try { url = new URL(href, location.href); } catch { return null; }
    if (url.origin !== location.origin) return null;
    try {
      if (url.pathname.startsWith(definitionBase)) return {entry: qualify(decodeURIComponent(url.pathname.slice(definitionBase.length)))};
      if (url.pathname.startsWith(contentBase)) {
        const rel = decodeURIComponent(url.pathname.slice(contentBase.length));
        if (!pagePrefixes.some(prefix => rel.startsWith(prefix))) return null;
        return {page: qualify(rel), anchor: decodeURIComponent(url.hash.slice(1))};
      }
      // A Stella address already names its collection.
      if (url.pathname === '/read') {
        const entry = url.searchParams.get('entry');
        const page = url.searchParams.get('page');
        const root = url.searchParams.get('root') || '';
        const anchor = url.searchParams.get('anchor') || decodeURIComponent(url.hash.slice(1));
        if (entry) return {root, entry};
        if (page) return {root, page, anchor};
        if (root) return {root, anchor};
      }
      return null;
    } catch { return null; }
  }
  function readerURL(route) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(route)) if (v) params.set(k, v);
    return location.origin + '/read' + (params.size ? '?' + params : '');
  }
  function follow(event) {
    if (!initialized || event.defaultPrevented || (event.type === 'click' && event.button !== 0) || (event.type === 'auxclick' && event.button !== 1)) return;
    const link = event.composedPath().find(n => n instanceof HTMLAnchorElement);
    if (!link || link.hasAttribute('download')) return;
    const route = target(link.href);
    if (!route) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button === 1 || link.target === '_blank') {
      window.open(readerURL(route), '_blank', 'noopener');
    } else {
      send('navigate', {route});
    }
  }
  // Capture reaches anchors in anna's shadow link layer as well as original hyperlinks.
  document.addEventListener('click', follow, true);
  document.addEventListener('auxclick', follow, true);
  document.addEventListener('keydown', event => {
    if (event.key === 'F6') {
      event.preventDefault(); send('focus-tools');
    } else if (event.key === '/' && !event.isComposing && !event.ctrlKey && !event.metaKey && !event.altKey &&
               !event.target.closest('input,textarea,select,[contenteditable=true]')) {
      event.preventDefault(); send('search');
    }
  });
  function locate(data) {
    const anchor = typeof data.anchor === 'string' ? data.anchor : '';
    history.replaceState(null, '', location.pathname + location.search + (anchor ? '#' + encodeURIComponent(anchor) : ''));
    let position = {x:0,y:0}, found = true;
    if (anchor.startsWith('anna-')) {
      const target = window.annaSourceLinks?.definitionPosition(anchor.slice(5));
      found = !!target;
      if (target) position = target;
    } else if (anchor) {
      const node = document.getElementById(anchor) || Array.from(document.getElementsByName(anchor))[0];
      found = !!node;
      if (node) position.y = Math.max(0,node.getBoundingClientRect().top+scrollY);
    }
    send('located',{requestId:data.requestId,found,position,height:height()});
  }
  function hydrateLazyMedia(lazyMedia, cachedMedia) {
    const cached = new Set(Array.isArray(cachedMedia) ? cachedMedia : []);
    if (!lazyMedia && !cached.size) return;
    const mediaBase = context?.media_base || ('/' + collection + '-media/');
    for (const el of document.querySelectorAll('[data-lazy-media]')) {
      const src = el.getAttribute('data-lazy-media') || '';
      if (!src.startsWith(mediaBase)) continue;
      let rel = '';
      try { rel = decodeURIComponent(src.slice(mediaBase.length)); } catch { continue; }
      if (!lazyMedia && !cached.has(rel)) continue;
      el.removeAttribute('data-lazy-media');
      if (el.getAttribute('data-media-kind') === 'audio') {
        const audio = document.createElement('audio');
        audio.controls = true; audio.preload = 'none'; audio.src = src;
        const lbl = document.createElement('span'); lbl.className = 'o5e-audio-label'; lbl.textContent = '读音';
        el.classList.remove('o5e-media-remote');
        el.replaceChildren(lbl, audio);
        measure();
        continue;
      }
      const badge = el.querySelector('.o5e-media-badge');
      const img = document.createElement('img');
      img.src = src;
      img.alt = el.getAttribute('data-media-alt') || '配图';
      img.loading = 'lazy'; img.decoding = 'async';
      img.setAttribute('data-o5e-fallback', '1');
      const fallback = document.createElement('span');
      fallback.className = 'o5e-media-fallback-note';
      fallback.textContent = '远端配图（未离线收录）';
      fallback.hidden = true;
      img.addEventListener('load', measure, {once:true});
      img.addEventListener('error', () => {
        el.classList.add('o5e-img-failed', 'o5e-media-remote');
        fallback.hidden = false;
        measure();
      }, {once:true});
      el.classList.remove('o5e-media-remote');
      if (badge) badge.replaceWith(img, fallback);
      else el.prepend(img, fallback);
    }
  }
  window.addEventListener('message', event => {
    const data = event.data;
    if (event.source !== parent || event.origin !== location.origin || !data || data.channel !== channel ||
        data.version !== version || data.snapshot !== snapshot || data.documentId !== documentId) return;
    if (data.type === 'initialize' && !initialized && Number.isInteger(data.navigationId)) {
      navigationId = data.navigationId; initialized = true; applyTheme(data.theme); markLinks(data.linkMarks);
      hydrateLazyMedia(!!data.lazyMedia, data.cachedMedia);
      send('initialized',{height:height(),definitions:definitions()});
    }
    if (!initialized || data.navigationId !== navigationId) return;
    if (data.type === 'locate' && Number.isInteger(data.requestId)) locate(data);
    else if (data.type === 'theme') { applyTheme(data.theme); measure(); }
    else if (data.type === 'link-marks') markLinks(data.linkMarks);
    else if (data.type === 'focus') window.focus();
  });
  function applyTheme(theme) {
    if (theme === 'light' || theme === 'dark') document.documentElement.setAttribute('data-webhelp-theme', theme);
  }
  // The reader's switch for marking anna's links; the link layer owns the marks themselves.
  function markLinks(on) { window.annaSourceLinks?.markLinks(on === true); }
  async function preparePresentation(resources) {
    const styles = [];
    for (const asset of resources || []) {
      if (document.getElementById(asset.id)) continue;
      const element = document.createElement(asset.kind === 'style' ? 'link' : 'script');
      element.id = asset.id;
      if (asset.kind === 'style') {
        element.rel = 'stylesheet'; element.href = contentBase + asset.path;
        styles.push(new Promise(resolve => { element.onload = resolve; element.onerror = resolve; }));
      } else { element.src = contentBase + asset.path; element.async = false; }
      document.head.append(element);
    }
    await Promise.all(styles);
  }
  async function ready() {
    try {
      if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once:true}));
      await preparePresentation(context.presentation_resources);
      new ResizeObserver(measure).observe(document.body);
      window.addEventListener('load',measure);
      document.fonts?.ready.then(measure);
      send('ready',{height:height()});
    } catch { /* The shell reports a bounded load failure and keeps its retry/navigation controls. */ }
  }
  ready();
})();
