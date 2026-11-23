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
  // Both hosts mark cached homepages, and only Pages marks its worker transport.
  const root = typeof document === 'undefined' ? null : document.documentElement;
  const pages = root?.hasAttribute('data-anna-pages') ?? false;
  // Keep deep links on the normal transport even if app.js later rewrites their address.
  const home = root?.hasAttribute('data-anna-home') && !location.search && !location.hash && root.getAttribute('data-anna-home');
  const base = pages || home ? new URL('.', document.currentScript.src) : null;
  function href(path) {
    if (!pages || !/^\/(?!\/)/.test(path)) return path;
    const route = path.replace(/^\/(read|tour|source)(?=[?#]|$)/, '/$1/');
    return base.pathname + route.slice(1);
  }
  // Older collection layouts name the collection separately. Reader addresses qualify each location.
  function readerLink(path) {
    if (!path.startsWith('/read?')) return path;
    const url = new URL(path, base.origin);
    const collection = url.searchParams.get('collection');
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(collection || '')) return path;
    url.searchParams.delete('collection');
    for (const key of ['root', 'page', 'entry', 'node']) {
      const value = url.searchParams.get(key);
      if (value && !/^[a-z0-9]+(?:-[a-z0-9]+)*:./.test(value)) url.searchParams.set(key, collection + ':' + value);
    }
    return url.pathname + url.search + url.hash;
  }
  // Rendered entry and tour fragments also use the server's site-root URL convention.
  function rebaseLinks(fragment) {
    if (!pages) return;
    const attributes = ['href', 'src', 'poster', 'data-font', 'data-lazy-media'];
    for (const element of fragment.querySelectorAll(attributes.map(name => `[${name}]`).join(','))) {
      for (const name of attributes) {
        const path = element.getAttribute(name);
        if (path !== null) element.setAttribute(name, href(name === 'href' ? readerLink(path) : path));
      }
    }
  }
  function hydrateImages(root, measure) {
    if (!pages || !root.querySelector('figure[data-lazy-media]')) return;
    import(new URL('pages-media.js', base).href)
      .then(media => media.hydrateImages(root, {measure}))
      .catch(() => {});
  }
  async function request(path, options) {
    const signal = options?.signal;
    signal?.throwIfAborted();
    if (home && !location.search && !location.hash && !annaLibrary.params().length &&
        (options?.method === undefined || /^GET$/i.test(options.method))) {
      const cache = await import(new URL('home.js', base).href).catch(() => null);
      const response = await cache?.request(path, home, signal);
      signal?.throwIfAborted();
      if (response) return response;
    }
    if (!pages) return fetch(path, options);
    const transport = await import(new URL('pages.js', base).href);
    return transport.request(path, options);
  }
  function warm() {
    if (!pages) return;
    import(new URL('pages.js', base).href)
      .then(transport => transport.warm())
      .catch(() => {});
  }
  return {esc, stored, pages, href, rebaseLinks, hydrateImages, request, warm};
})();
if (typeof module !== 'undefined') module.exports = annaKit;
