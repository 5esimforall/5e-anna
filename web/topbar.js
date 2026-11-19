/* The topbar every page shares: whose page this is, the page's own controls, the other companion, then the
   theme switch.
   It runs right after the page's header so the bar is complete before the first paint. The header names its
   page and holds only the controls that belong to that page; this script adds everything the pages share. */
const annaTopbar = (() => {
  const defaultCollection = '5echm';
  const thisPage = document.querySelector('header.topbar[data-page]')?.dataset.page;
  const library = typeof annaLibrary === 'undefined' ? null : annaLibrary;
  // The collection this page shows: the address's, else (on the tour and Stella) the first one in the reader's library.
  function activeCollection(page = thisPage) {
    if (typeof location === 'undefined') return defaultCollection;
    const raw = new URLSearchParams(location.search).get('collection');
    if (raw && raw.trim()) return raw.trim();
    return library && (page === 'tour' || page === 'book') ? library.pageCollection(page) : defaultCollection;
  }
  // Anna's query page reads the whole library; a link there names a collection only when the library leaves it out.
  function withCollection(href, collection = activeCollection()) {
    if (!href || !collection || collection === defaultCollection) return href;
    if (library && /^\/(\?|$)/.test(href) && library.libraries().includes(collection)) return href;
    try {
      const url = new URL(href, 'http://anna.local');
      if (!url.searchParams.has('collection')) {
        const next = new URLSearchParams();
        next.set('collection', collection);
        for (const [k, v] of url.searchParams.entries()) next.append(k, v);
        return url.pathname + '?' + next + url.hash;
      }
      return url.pathname + url.search + url.hash;
    } catch {
      return href;
    }
  }
  function tourStorageKey(collection = activeCollection()) {
    return collection && collection !== defaultCollection ? 'anna.tour.' + collection : 'anna.tour';
  }
  // Each page belongs to one companion: Anna answers queries and keeps the tour, Stella reads the book with you.
  const pages = {
    query:{home:'/', logo:'/anna-logo.png', companion:'query'},
    // The tour remembers the last level the reader opened; turning back to it from the query page returns there.
    tour:{home:'/tour', logo:'/anna-logo.png', companion:'query', resume() {
      const col = activeCollection('tour');
      const at = annaKit.stored.get(tourStorageKey(col));
      if (!at) return null;
      if (col === defaultCollection) {
        return /^[\w-]+(\.[\w-]+)*$/.test(at) ? '/tour?at=' + at : null;
      }
      if (!/^[\w.-]+(\/[\w.-]+)*$/.test(at) || at.split('/').some(seg => seg === '.' || seg === '..')) return null;
      const params = new URLSearchParams({collection: col, at});
      return '/tour?' + params;
    }},
    book:{home:'/read', logo:'/stella-logo.png', companion:'book'}
  };
  // A companion with more than one page names them beside her brand, so the reader sees where else she can help.
  const switchLabels = {
    zh:{label:'Anna 的页面', tour:'导览', query:'查询'},
    en:{label:"Anna's pages", tour:'Tour', query:'Search'}
  };
  const themeIcons = `
    <svg class="theme-icon-system" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="1.5"/><path d="M8 21h8M12 17v4"/></svg>
    <svg class="theme-icon-light" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
    <svg class="theme-icon-dark" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 15.2A8.4 8.4 0 0 1 8.8 4a8.5 8.5 0 1 0 11.2 11.2z"/></svg>`;
  const libraryIcon = '<svg class="library-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19V5.5A1.5 1.5 0 0 1 5.5 4H7v15H5.5A1.5 1.5 0 0 1 4 17.5"/><path d="M7 4h3.5v15H7M10.5 6.5l3-1 4.2 12.6-3 1z"/></svg>';
  function pageSwitch(page) {
    const siblings = Object.keys(pages).filter(key => pages[key].companion === pages[page].companion);
    if (siblings.length < 2) return null;
    const labels = switchLabels[document.documentElement.lang.startsWith('en') ? 'en' : 'zh'];
    const nav = document.createElement('nav'); nav.className = 'page-switch'; nav.setAttribute('aria-label', labels.label);
    for (const key of ['tour', 'query'].filter(key => siblings.includes(key))) {
      const link = document.createElement('a');
      link.href = withCollection(key === page ? pages[key].home : pages[key].resume?.() || pages[key].home, activeCollection(key === 'query' ? page : key));
      link.dataset.page = key;
      link.textContent = labels[key];
      if (key === page) link.setAttribute('aria-current', 'page');
      nav.append(link);
    }
    return nav;
  }
  function render(header) {
    const page = header.dataset.page, {home, logo} = pages[page];
    const brand = document.createElement('a'); brand.className = 'brand'; brand.href = withCollection(home);
    const image = document.createElement('img'); image.className = 'brand-logo'; image.src = logo;
    image.width = image.height = 36; image.alt = ''; // The name beside it already labels the link.
    brand.append(image, annaBrands[page]);
    const lead = document.createElement('div'); lead.className = 'topbar-lead';
    lead.append(brand, ...[pageSwitch(page)].filter(Boolean));
    const actions = document.createElement('div'); actions.className = 'topbar-actions';
    actions.append(...header.childNodes);
    // library.js wires the library button; companions.js fills the slot once the page knows its build; theme.js
    // reveals the button once it can switch.
    const books = document.createElement('button'); books.type = 'button'; books.className = 'library-button'; books.hidden = !library;
    books.setAttribute('aria-haspopup', 'dialog');
    books.innerHTML = `${libraryIcon}<span class="library-word"></span><span class="library-summary"></span>`;
    const companion = document.createElement('div'); companion.className = 'companion'; companion.hidden = true;
    const theme = document.createElement('button'); theme.className = 'icon-button theme-button'; theme.id = 'theme';
    theme.type = 'button'; theme.hidden = true; theme.innerHTML = themeIcons;
    actions.append(books, companion, theme);
    header.replaceChildren(lead, actions);
  }
  for (const header of document.querySelectorAll('header.topbar[data-page]')) render(header);
  // A page that remembers where the reader was points its switch link there instead of at its home.
  function pointSwitch(page, href) {
    const link = document.querySelector(`.page-switch a[data-page="${page}"]`);
    if (link && href) link.href = withCollection(href);
  }
  return {pages, pointSwitch, activeCollection, withCollection, tourStorageKey};
})();
