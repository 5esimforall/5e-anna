/* Helpers every page shares: HTML escaping and browser storage that keeps working when the browser refuses it. */
const annaKit = (() => {
  const entities = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => entities[c]);
  // Preferences and remembered places live in localStorage; a refusal leaves the current page working.
  const stored = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) {
      try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value); return true; }
      catch { return false; }
    },
    // Other tabs of this site report their writes; a cleared store reports a null key.
    watch(key, listener) {
      if (typeof window === 'undefined') return;
      window.addEventListener('storage', event => { if (event.key === key || event.key === null) listener(); });
    }
  };
  // The exported HTML marks Pages mode, and kit.js lives at the root of either deployment.
  const pages = typeof document !== 'undefined' && document.documentElement.hasAttribute('data-anna-pages');
  const base = pages ? new URL('.', document.currentScript.src) : null;
  function href(path) {
    if (!pages || !/^\/(?!\/)/.test(path)) return path;
    const route = path.replace(/^\/(read|tour|source)(?=[?#]|$)/, '/$1/');
    return base.pathname + route.slice(1);
  }
  // Rendered entry and tour fragments also use the server's site-root URL convention.
  function rebaseLinks(fragment) {
    if (!pages) return;
    const attributes = ['href', 'src', 'poster', 'data-font', 'data-lazy-media'];
    for (const element of fragment.querySelectorAll(attributes.map(name => `[${name}]`).join(','))) {
      for (const name of attributes) {
        const path = element.getAttribute(name);
        if (path !== null) element.setAttribute(name, href(path));
      }
    }
  }
  async function request(path, options) {
    if (!pages) return fetch(path, options);
    options?.signal?.throwIfAborted();
    const transport = await import(new URL('pages.js', base).href);
    return transport.request(path, options);
  }
  return {esc, stored, pages, href, rebaseLinks, request};
})();
if (typeof module !== 'undefined') module.exports = annaKit;
