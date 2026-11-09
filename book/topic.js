/* Reader bridge. The source bytes and DOM remain intact; only navigation is mediated.
   A versioned protocol coordinates the source document and application. */
(() => {
  'use strict';
  if (parent === window) return;
  const channel = 'anna-reader', version = 1;
  const documentId = crypto.randomUUID();
  let appOrigin, snapshot, navigationId, initialized = false, positionTimer;
  let initialLocation, interacted = false;
  const path = decodeURIComponent(location.pathname.slice('/book/'.length));
  const send = (type, detail = {}) => {
    if (appOrigin) parent.postMessage({channel, version, type, documentId, snapshot, path, navigationId, ...detail}, appOrigin);
  };
  function position() {
    clearTimeout(positionTimer);
    if (initialized) send('position', {position: {x: scrollX, y: scrollY}});
  }
  function target(href) {
    let url;
    try { url = new URL(href, location.href); } catch { return null; }
    if (url.origin !== location.origin) return null;
    try {
      if (url.pathname.startsWith('/book/definition/')) return {entry: decodeURIComponent(url.pathname.slice('/book/definition/'.length))};
      if (!url.pathname.startsWith('/book/topics/')) return null;
      return {page: decodeURIComponent(url.pathname.slice('/book/'.length)), anchor: decodeURIComponent(url.hash.slice(1))};
    } catch { return null; }
  }
  function readerURL(route) {
    return appOrigin + '/read?' + new URLSearchParams(route);
  }
  function follow(event) {
    if (!initialized || event.defaultPrevented || (event.type === 'click' && event.button !== 0) || (event.type === 'auxclick' && event.button !== 1)) return;
    const link = event.composedPath().find(n => n instanceof HTMLAnchorElement);
    if (!link || link.hasAttribute('download')) return;
    const route = target(link.href);
    if (!route) return;
    event.preventDefault();
    event.stopPropagation();
    position();
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button === 1 || link.target === '_blank') {
      window.open(readerURL(route), '_blank', 'noopener');
    } else {
      send('navigate', {route});
    }
  }
  // Capture reaches anchors in anna's shadow link layer as well as original hyperlinks.
  document.addEventListener('click', follow, true);
  document.addEventListener('auxclick', follow, true);
  window.addEventListener('scroll', () => {
    clearTimeout(positionTimer);
    positionTimer = setTimeout(position, 100);
  }, {passive:true});
  window.addEventListener('pagehide', position);
  document.addEventListener('keydown', event => {
    if (event.key === 'F6') {
      event.preventDefault(); send('focus-tools');
    } else if (event.key === '/' && !event.isComposing && !event.ctrlKey && !event.metaKey && !event.altKey &&
               !event.target.closest('input,textarea,select,[contenteditable=true]')) {
      event.preventDefault(); send('search');
    }
  });
  async function locate(data) {
    navigationId = data.navigationId;
    const requestedNavigation = navigationId;
    initialLocation = data;
    applyTheme(data.theme);
    initialized = false;
    const anchor = typeof data.anchor === 'string' ? data.anchor : '';
    // replaceState does not create a second, nested browsing-history entry.
    history.replaceState(null, '', location.pathname + location.search + (anchor ? '#' + encodeURIComponent(anchor) : ''));
    let found = true;
    if (data.position && Number.isFinite(data.position.x) && Number.isFinite(data.position.y)) {
      scrollTo(Math.max(0, data.position.x), Math.max(0, data.position.y));
    } else if (anchor.startsWith('anna-')) {
      const bindings = JSON.parse(document.getElementById('anna-bindings')?.textContent || '{}');
      found = (bindings.definitions || []).some(d => 'anna-' + d.id === anchor && (d.title || d.body));
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    } else if (anchor) {
      const node = document.getElementById(anchor) || Array.from(document.getElementsByName(anchor))[0];
      found = !!node;
      node?.scrollIntoView({block:'start'});
    } else {
      scrollTo(0, 0);
    }
    await new Promise(requestAnimationFrame);
    if (requestedNavigation !== navigationId) return;
    initialized = true;
    send('located', {found, position: {x:scrollX, y:scrollY}});
  }
  window.addEventListener('message', event => {
    const data = event.data;
    if (!appOrigin || event.source !== parent || event.origin !== appOrigin || !data || data.channel !== channel ||
        data.version !== version || data.snapshot !== snapshot || data.documentId !== documentId) return;
    if (data.type === 'locate' && typeof data.navigationId === 'number') locate(data);
    else if (data.type === 'theme') applyTheme(data.theme);
    else if (data.type === 'print' && initialized) window.print();
    else if (data.type === 'focus') window.focus();
    else if (data.type === 'position') position();
  });
  function applyTheme(theme) {
    if (theme === 'light' || theme === 'dark') document.documentElement.setAttribute('data-webhelp-theme', theme);
  }
  async function preparePresentation(resources) {
    const styles = [];
    for (const asset of resources) {
      if (document.getElementById(asset.id)) continue;
      const element = document.createElement(asset.kind === 'style' ? 'link' : 'script');
      element.id = asset.id;
      if (asset.kind === 'style') {
        element.rel = 'stylesheet'; element.href = '/book/' + asset.path;
        styles.push(new Promise(resolve => { element.onload = resolve; element.onerror = resolve; }));
      } else { element.src = '/book/' + asset.path; element.async = false; }
      document.head.append(element);
    }
    if (resources.some(asset => asset.id === 'webhelpTopicStyles')) document.documentElement.classList.add('webhelp-topic-document');
    await Promise.all(styles);
  }
  for (const event of ['wheel','touchstart','pointerdown','keydown'])
    window.addEventListener(event, () => { interacted = true; }, {passive:true});
  // Show parsed text without waiting for images. Re-align a pending initial location once
  // late media settles, but never pull a reader back after they have started interacting.
  window.addEventListener('load', () => { if (initialLocation && !interacted) locate(initialLocation); });
  async function ready() {
    try {
      const response = await fetch('/book/api/reader-context');
      if (!response.ok) return;
      const context = await response.json();
      const referrer = document.referrer ? new URL(document.referrer).origin : '';
      if (!context.app_origins.includes(referrer)) return;
      appOrigin = referrer; snapshot = context.snapshot;
      if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once:true}));
      await preparePresentation(context.presentation_resources);
      send('ready');
    } catch { /* The shell reports a bounded load failure and keeps its retry/navigation controls. */ }
  }
  ready();
})();
