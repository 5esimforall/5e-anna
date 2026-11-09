/* Reader bridge. Source content keeps its layout; navigation uses the app protocol.
   A versioned protocol coordinates the source document and application. */
(() => {
  'use strict';
  const context = JSON.parse(document.getElementById('anna-bindings').textContent).reader;
  if (context?.presentation_resources.some(asset => asset.id === 'webhelpTopicStyles'))
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
  const path = decodeURIComponent(location.pathname.slice('/book/'.length));
  const send = (type, detail = {}) => {
    parent.postMessage({channel, version, type, documentId, snapshot, path, navigationId, ...detail}, location.origin);
  };
  function height() {
    const body = document.body, rect = body.getBoundingClientRect(), style = getComputedStyle(body);
    return Math.max(1,Math.ceil(rect.top+scrollY+Math.max(rect.height,body.scrollHeight)+(parseFloat(style.marginBottom)||0)));
  }
  function measure() {
    if (!initialized || sizeFrame) return;
    sizeFrame = requestAnimationFrame(() => { sizeFrame = 0; send('size',{height:height()}); });
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
    return location.origin + '/read?' + new URLSearchParams(route);
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
  window.addEventListener('message', event => {
    const data = event.data;
    if (event.source !== parent || event.origin !== location.origin || !data || data.channel !== channel ||
        data.version !== version || data.snapshot !== snapshot || data.documentId !== documentId) return;
    if (data.type === 'initialize' && !initialized && Number.isInteger(data.navigationId)) {
      navigationId = data.navigationId; initialized = true; applyTheme(data.theme);
      send('initialized',{height:height()});
    }
    if (!initialized || data.navigationId !== navigationId) return;
    if (data.type === 'locate' && Number.isInteger(data.requestId)) locate(data);
    else if (data.type === 'theme') { applyTheme(data.theme); measure(); }
    else if (data.type === 'focus') window.focus();
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
