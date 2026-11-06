/* Preserve fine-grained entry fragments when the source reader updates its page URL. */
(() => {
  'use strict';
  const frame = document.getElementById('content');
  if (!frame) return;
  function rememberDefinition() {
    try {
      const topic = new URL(frame.contentWindow.location.href);
      if (topic.origin !== location.origin || !topic.pathname.startsWith('/topics/') || !topic.hash.startsWith('#anna-')) return;
      const reader = new URL(location.href);
      reader.searchParams.set('page', decodeURIComponent(topic.pathname.slice(1)) + topic.hash);
      history.replaceState(history.state, '', reader);
    } catch { /* A non-topic frame has no anna location to preserve. */ }
  }
  frame.addEventListener('load', () => {
    rememberDefinition();
    try { frame.contentWindow.addEventListener('hashchange', rememberDefinition); } catch {}
  });
  rememberDefinition();
})();
