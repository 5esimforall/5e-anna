/* The topbar both pages share: whose page this is, the page's own controls, the other companion, then the
   theme switch.
   It runs right after the page's header so the bar is complete before the first paint. The header names its
   page and holds only the controls that belong to that page; this script adds everything the pages share. */
const annaTopbar = (() => {
  // Each page faces one companion: Anna answers queries, Stella reads the book with you.
  const pages = {
    query:{home:'/', logo:'/anna-logo.png'},
    book:{home:'/read', logo:'/stella-logo.png'}
  };
  const themeIcons = `
    <svg class="theme-icon-system" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="1.5"/><path d="M8 21h8M12 17v4"/></svg>
    <svg class="theme-icon-light" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
    <svg class="theme-icon-dark" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 15.2A8.4 8.4 0 0 1 8.8 4a8.5 8.5 0 1 0 11.2 11.2z"/></svg>`;
  function render(header) {
    const page = header.dataset.page, {home, logo} = pages[page];
    const brand = document.createElement('a'); brand.className = 'brand'; brand.href = home;
    const image = document.createElement('img'); image.className = 'brand-logo'; image.src = logo;
    image.width = image.height = 36; image.alt = ''; // The name beside it already labels the link.
    brand.append(image, annaBrands[page]);
    const actions = document.createElement('div'); actions.className = 'topbar-actions';
    actions.append(...header.childNodes);
    // companions.js fills the slot once the page knows its build; theme.js reveals the button once it can switch.
    const companion = document.createElement('div'); companion.className = 'companion'; companion.hidden = true;
    const theme = document.createElement('button'); theme.className = 'icon-button theme-button'; theme.id = 'theme';
    theme.type = 'button'; theme.hidden = true; theme.innerHTML = themeIcons;
    actions.append(companion, theme);
    header.replaceChildren(brand, actions);
  }
  for (const header of document.querySelectorAll('header.topbar[data-page]')) render(header);
  return {pages};
})();
