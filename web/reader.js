/* One application history, one source frame. The reader consumes the same definitions as anna. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const ui = annaReaderLabels[document.documentElement.lang.startsWith('en') ? 'en' : 'zh'];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const text = (key, values = {}) => Object.entries(values).reduce((s, [k,v]) => s.replaceAll('{'+k+'}', v), ui[key]);
  for (const element of document.querySelectorAll('[data-text]')) element.textContent = ui[element.dataset.text];
  for (const element of document.querySelectorAll('[data-label]')) {
    element.setAttribute('aria-label', ui[element.dataset.label]); element.title = ui[element.dataset.label];
  }
  for (const element of document.querySelectorAll('[data-placeholder]')) element.placeholder = ui[element.dataset.placeholder];
  $('#content').title = ui.original;
  const frame = $('#content'), sidebar = $('#book-sidebar'), narrow = matchMedia('(max-width:800px)');
  const channel = 'anna-reader', version = 1;
  let metadata, page, route, nodeId = '', focusedNode = '', nodes = new Map(), children = new Map(), pageNodes = new Map();
  let expanded = new Set(), pane = 'contents', sidebarOpen = !narrow.matches;
  let position = {x:0,y:0}, pendingPosition = null, navigationId = 0, request, loading = true;
  let framePath = '', documentId = '', frameReady = false, loadTimer, frameNavigationId = 0;
  let searchRequest, searchSequence = 0, searchPage = 1, searchTotalPages = 0, searchQuery = '', searchTitleOnly = false;
  let restoring = true;
  history.scrollRestoration = 'manual';

  function readRoute() {
    const p = new URL(location.href).searchParams;
    for (const key of ['entry','page','anchor','node']) if (p.getAll(key).length > 1) throw new Error('invalid_location');
    if (p.has('entry') && p.has('page') || p.has('entry') && !p.get('entry') || p.has('page') && !p.get('page')) throw new Error('invalid_location');
    return p.has('entry') ? {entry:p.get('entry'), node:p.get('node') || ''} :
      {page:p.get('page') || metadata?.default_page || '', anchor:p.get('anchor') || '', node:p.get('node') || ''};
  }
  function routeURL(target) {
    const params = new URLSearchParams();
    for (const key of ['entry','page','anchor','node']) if (target[key]) params.set(key, target[key]);
    return '/read' + (params.size ? '?' + params : '');
  }
  function nodeRoute(node) { return {page:node.path, anchor:node.fragment, node:node.id}; }
  function save() {
    if (!metadata || restoring) return;
    const state = {snapshot:metadata.snapshot, position, expanded:[...expanded], pane, sidebar:sidebarOpen, node:nodeId,
      filter:$('#book-filter').value, treeScroll:$('#book-tree-scroll').scrollTop, outlineScroll:$('#panel-outline').scrollTop,
      search:{query:searchQuery, titleOnly:searchTitleOnly, page:searchPage, draft:$('#book-search').value, scroll:$('#panel-search').scrollTop}};
    try { history.replaceState({...history.state, book:state}, '', location.href); } catch { /* History storage may be unavailable. */ }
  }
  async function api(path, signal) {
    let response;
    try { response = await fetch(path, {signal}); } catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new Error('network');
    }
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.code || (response.status === 404 && path === '/api/reader' ? 'unavailable' : 'network'));
    }
    return response.json();
  }
  function setSidebar(open, focus = false) {
    sidebarOpen = open;
    const modal = sidebar.matches(':modal');
    if (sidebar.open && (!open || modal !== narrow.matches)) sidebar.close();
    if (open && !sidebar.open) narrow.matches ? sidebar.showModal() : sidebar.show();
    $('.book-workspace').classList.toggle('sidebar-closed', !open || narrow.matches);
    $('#book-menu').setAttribute('aria-expanded', String(open));
    if (focus) {
      if (!open) $('#book-menu').focus();
      else if (pane === 'search') $('#book-search').focus();
      else $(`#tab-${pane}`).focus();
    }
    save();
  }
  function selectPane(value, focus = false) {
    pane = ['contents','outline','search'].includes(value) ? value : 'contents';
    document.querySelectorAll('[data-pane]').forEach(button => {
      const active = button.dataset.pane === pane;
      button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1;
      $('#panel-'+button.dataset.pane).hidden = !active;
    });
    if (focus) $('#tab-'+pane).focus();
    save();
  }
  function ancestors(id) {
    const result = [];
    for (let node = nodes.get(id); node; node = nodes.get(node.parent_id)) result.unshift(node);
    return result;
  }
  function reveal(id) {
    ancestors(id).slice(0,-1).forEach(node => expanded.add(node.id));
  }
  function renderTree(focusId = '') {
    if (!metadata) return;
    const query = $('#book-filter').value.trim().toLocaleLowerCase();
    $('#book-filter-clear').hidden = !query;
    const allowed = new Set();
    if (query) {
      const terms = query.split(/\s+/);
      for (const node of nodes.values()) if (terms.every(term => node.label.toLocaleLowerCase().includes(term)))
        ancestors(node.id).forEach(parent => allowed.add(parent.id));
    }
    const visibleIds = [];
    function branch(parent, depth) {
      const siblings = (children.get(parent) || []).filter(node => !query || allowed.has(node.id));
      return siblings.map((node,index) => {
        visibleIds.push(node.id);
        const hasChildren = !!children.get(node.id)?.length;
        const open = query ? !!children.get(node.id)?.some(child => allowed.has(child.id)) : expanded.has(node.id);
        const selected = node.id === nodeId;
        return `<li role="treeitem" data-node="${esc(node.id)}" tabindex="-1" aria-label="${esc(node.label)}" aria-level="${depth+1}" aria-posinset="${index+1}" aria-setsize="${siblings.length}"${hasChildren?` aria-expanded="${open}"`:''}>
          <div class="toc-row${selected?' current':''}${node.status==='missing'?' missing':''}">
          ${hasChildren?`<button type="button" tabindex="-1" data-expand="${esc(node.id)}" aria-label="${esc(text(open?'collapseNode':'expandNode',{name:node.label}))}">${open?'▾':'▸'}</button>`:'<span class="toc-indent"></span>'}
          ${node.status==='present'?`<a tabindex="-1" href="${esc(routeURL(nodeRoute(node)))}"${selected?' aria-current="page"':''}>${esc(node.label)}</a>`:`<span class="toc-label"${node.status==='missing'?` title="${esc(ui.missingNode)}"`:''}>${esc(node.label)}</span>`}</div>
          ${hasChildren && open?`<ul role="group">${branch(node.id,depth+1)}</ul>`:''}</li>`;
      }).join('');
    }
    $('#book-tree').innerHTML = branch('',0);
    $('#book-tree-empty').hidden = visibleIds.length > 0;
    const id = [focusId,focusedNode,nodeId,visibleIds[0]].find(id => visibleIds.includes(id));
    const row = id && Array.from($('#book-tree').querySelectorAll('[data-node]')).find(el => el.dataset.node === id);
    if (row) { row.tabIndex = 0; focusedNode = id; if (focusId) row.focus({preventScroll:true}); }
  }
  function renderPage() {
    document.title = page.title + ' · 5e anna';
    const crumbs = ancestors(nodeId);
    $('#book-breadcrumb').innerHTML = crumbs.length ? crumbs.map(node => node.status === 'present'
      ? `<a href="${esc(routeURL(nodeRoute(node)))}">${esc(node.label)}</a>` : esc(node.label)).join(' <span aria-hidden="true">/</span> ') : esc(ui.notInContents);
    $('#book-breadcrumb').title = crumbs.map(n => n.label).join(' / ') || page.path;
    $('#book-outline-title').textContent = page.title;
    const definitions = page.definitions.filter(entry => entry.locatable);
    $('#book-outline-empty').hidden = definitions.length > 0;
    $('#book-outline').innerHTML = definitions.map(entry => `<li><a href="${esc(routeURL({entry:entry.id,node:nodeId}))}"${route.entry===entry.id || route.anchor===entry.fragment?' aria-current="location"':''}>${esc(entry.name)}</a><a href="/?entry=${encodeURIComponent(entry.id)}" target="_blank" rel="noopener" aria-label="${esc(ui.entry+': '+entry.name)}" title="${esc(ui.entry)}">↗</a></li>`).join('');
    const ordered = metadata.nodes.filter(node => node.status === 'present');
    const index = ordered.findIndex(node => node.id === nodeId);
    for (const [selector,offset] of [['#book-previous',-1],['#book-next',1]]) {
      const link = $(selector), node = index >= 0 ? ordered[index+offset] : null;
      link.setAttribute('aria-disabled', String(!node));
      if (node) { link.href = routeURL(nodeRoute(node)); link.title = node.label; link.tabIndex = 0; }
      else { link.removeAttribute('href'); link.removeAttribute('title'); link.tabIndex = -1; }
    }
    renderTree();
  }
  function send(type, detail = {}) {
    if (!documentId || !metadata) return;
    frame.contentWindow.postMessage({channel, version, type, documentId, snapshot:metadata.snapshot, navigationId, ...detail}, metadata.content_origin);
  }
  function locateFrame() {
    send('locate', {anchor:route.entry ? page.fragment : route.anchor || '', position:pendingPosition, theme:document.documentElement.dataset.theme});
  }
  function showError(code) {
    clearTimeout(loadTimer); loading = false;
    $('#book-loading').hidden = true; $('#book-error').hidden = false;
    document.title = ui.emptyTitle + ' · 5e anna';
    $('#book-error-text').textContent = ui.errors[code] || ui.errors.network;
    frame.style.visibility = 'hidden'; $('#book-print').disabled = true;
    $('#book-main').setAttribute('aria-busy','false'); $('#book-status').textContent = '';
  }
  async function load(target, saved) {
    request?.abort(); request = new AbortController();
    const id = ++navigationId;
    clearTimeout(loadTimer);
    loading = true; pendingPosition = saved?.position || null;
    $('#book-loading').hidden = false; $('#book-error').hidden = true;
    frame.style.visibility = 'hidden'; $('#book-print').disabled = true;
    $('#book-main').setAttribute('aria-busy','true');
    $('#book-status').hidden = true;
    try {
      const query = new URLSearchParams({snapshot:metadata.snapshot});
      if (target.entry) query.set('entry',target.entry);
      else if (target.page) query.set('path',target.page);
      else throw new Error('invalid_location');
      const data = await api('/api/reader-page?' + query, request.signal);
      if (id !== navigationId) return;
      page = data; route = target; frameNavigationId = id;
      const choices = pageNodes.get(page.path) || [];
      nodeId = [target.node,saved?.node,nodeId,choices[0]?.id].find(id => choices.some(n => n.id === id)) || '';
      if (!saved) reveal(nodeId);
      renderPage();
      if (saved) {
        $('#book-tree-scroll').scrollTop = saved.treeScroll || 0;
        $('#panel-outline').scrollTop = saved.outlineScroll || 0;
      }
      frame.hidden = false;
      // The iframe keeps its layout while loading, so DOM ranges are measured at the final width.
      if (framePath === page.path && frameReady) locateFrame();
      else {
        frameReady = false; documentId = ''; framePath = page.path;
        const url = metadata.content_origin + '/' + page.path.split('/').map(encodeURIComponent).join('/');
        frame.contentWindow.location.replace(url);
      }
      loadTimer = setTimeout(() => { if (id === navigationId) showError('frame_timeout'); }, 15000);
    } catch (error) {
      if (id === navigationId && error.name !== 'AbortError') showError(error.message);
    }
  }
  async function navigate(target, push = true) {
    save();
    const url = routeURL(target);
    if (push && url !== location.pathname+location.search) history.pushState(null, '', url);
    else history.replaceState(history.state, '', url);
    position = {x:0,y:0};
    if (narrow.matches) setSidebar(false);
    await load(target);
  }
  function restore(saved) {
    expanded = new Set((saved?.expanded || []).filter(id => nodes.has(id)));
    $('#book-filter').value = saved?.filter || '';
    selectPane(saved?.pane || 'contents');
    setSidebar(saved?.sidebar ?? !narrow.matches);
    $('#book-search').value = saved?.search?.draft || saved?.search?.query || '';
    $('#book-title-only').checked = !!saved?.search?.titleOnly;
  }
  async function search(query, number = 1, titleOnly = $('#book-title-only').checked, scroll = 0) {
    searchRequest?.abort(); searchRequest = new AbortController();
    const sequence = ++searchSequence;
    searchQuery = query.trim(); searchPage = number; searchTitleOnly = titleOnly;
    $('#book-query-search').href = '/?' + new URLSearchParams({q:searchQuery});
    $('#book-search-status').textContent = ui.searching;
    $('#book-search-results').inert = true; $('#book-search-paging').hidden = true;
    $('#book-search-fallback').hidden = true; save();
    try {
      const params = new URLSearchParams({keyword:searchQuery,page:number,pageSize:30,titleOnly,snapshot:metadata.snapshot});
      const data = await api('/api/book-search?' + params, searchRequest.signal);
      if (sequence !== searchSequence) return;
      searchTotalPages = data.totalPages;
      $('#book-search-status').textContent = data.total ? text('found',{count:data.total.toLocaleString()}) : ui.searchEmpty;
      $('#book-search-fallback').hidden = data.match !== 'any';
      $('#book-search-results').innerHTML = data.results.map(result => `<li><a href="${esc(routeURL({page:result.path}))}">${esc(result.title)}</a><small>${esc(result.category)}</small><p>${esc(result.preview)}</p></li>`).join('');
      $('#book-search-page').textContent = text('searchPage',{page:number,pages:Math.max(1,data.totalPages)});
      $('#book-search-prev').disabled = number <= 1; $('#book-search-next').disabled = number >= data.totalPages;
      $('#book-search-paging').hidden = data.totalPages <= 1;
      $('#panel-search').scrollTop = scroll;
    } catch (error) {
      if (sequence !== searchSequence || error.name === 'AbortError') return;
      $('#book-search-status').textContent = ui.errors[error.message] || ui.searchError;
      $('#book-search-results').replaceChildren();
    } finally {
      if (sequence === searchSequence) { $('#book-search-results').inert = false; save(); }
    }
  }

  window.addEventListener('message', event => {
    const data = event.data;
    if (!metadata || event.source !== frame.contentWindow || event.origin !== metadata.content_origin || !data ||
        data.channel !== channel || data.version !== version || data.snapshot !== metadata.snapshot || !page || data.path !== page.path || frameNavigationId !== navigationId) return;
    if (data.type === 'ready' && typeof data.documentId === 'string' && data.documentId.length < 100) {
      documentId = data.documentId; frameReady = true; locateFrame(); return;
    }
    if (data.documentId !== documentId || data.navigationId !== navigationId) return;
    if (data.type === 'located') {
      clearTimeout(loadTimer); loading = false;
      frame.style.visibility = ''; $('#book-loading').hidden = true; $('#book-error').hidden = true;
      $('#book-print').disabled = false; $('#book-main').setAttribute('aria-busy','false');
      $('#book-status').textContent = data.found ? '' : ui.errors.fragment_missing;
      $('#book-status').hidden = !!data.found;
      $('#book-status').title = $('#book-status').textContent;
    }
    if (['located','position'].includes(data.type) && !loading && data.position &&
        Number.isFinite(data.position.x) && Number.isFinite(data.position.y)) {
      position = {x:Math.max(0,data.position.x),y:Math.max(0,data.position.y)}; save();
    } else if (data.type === 'navigate' && data.route && (typeof data.route.page === 'string' || typeof data.route.entry === 'string')) {
      navigate(data.route);
    } else if (data.type === 'focus-tools') $('#book-menu').focus();
    else if (data.type === 'search') { selectPane('search'); setSidebar(true,true); }
  });
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.target === '_blank') return;
    const url = new URL(link.href);
    if (url.origin !== location.origin || url.pathname !== '/read') return;
    event.preventDefault();
    const params = url.searchParams;
    const target = params.has('entry') ? {entry:params.get('entry'),node:params.get('node') || ''} :
      {page:params.get('page') || metadata?.default_page,anchor:params.get('anchor') || '',node:params.get('node') || ''};
    if (metadata) navigate(target);
  });
  $('#book-tree').addEventListener('click', event => {
    const button = event.target.closest('[data-expand]');
    if (button) {
      const id = button.dataset.expand;
      expanded.has(id) ? expanded.delete(id) : expanded.add(id);
      renderTree(id); save();
    }
  });
  $('#book-tree').addEventListener('focusin', event => {
    const row = event.target.closest('[data-node]');
    if (!row) return;
    $('#book-tree').querySelectorAll('[tabindex="0"]').forEach(el => el.tabIndex = -1);
    row.tabIndex = 0; focusedNode = row.dataset.node;
  });
  $('#book-tree').addEventListener('keydown', event => {
    const row = event.target.closest('[data-node]');
    if (!row) return;
    const id = row.dataset.node, node = nodes.get(id);
    const rows = Array.from($('#book-tree').querySelectorAll('[data-node]'));
    let index = rows.indexOf(row), next;
    if (event.key === 'ArrowDown') next = rows[index+1];
    else if (event.key === 'ArrowUp') next = rows[index-1];
    else if (event.key === 'Home') next = rows[0];
    else if (event.key === 'End') next = rows.at(-1);
    else if (event.key === 'ArrowRight') {
      if (row.getAttribute('aria-expanded') === 'false') { expanded.add(id); renderTree(id); }
      else if (children.get(id)?.length) next = rows[index+1];
    } else if (event.key === 'ArrowLeft') {
      if (row.getAttribute('aria-expanded') === 'true' && !$('#book-filter').value.trim()) { expanded.delete(id); renderTree(id); }
      else next = rows.find(el => el.dataset.node === node.parent_id);
    } else if (['Enter',' '].includes(event.key)) {
      if (node.status === 'present') navigate(nodeRoute(node));
      else { expanded.has(id) ? expanded.delete(id) : expanded.add(id); renderTree(id); }
    } else return;
    event.preventDefault(); next?.focus(); save();
  });
  $('#book-filter').addEventListener('input', () => { renderTree(); save(); });
  $('#book-filter-clear').onclick = () => { $('#book-filter').value = ''; renderTree(); $('#book-filter').focus(); save(); };
  $('#book-collapse').onclick = () => { expanded.clear(); $('#book-filter').value = ''; renderTree(); save(); };
  $('#book-locate').onclick = () => {
    $('#book-filter').value = ''; reveal(nodeId); renderTree(nodeId);
    const row = Array.from($('#book-tree').querySelectorAll('[data-node]')).find(el => el.dataset.node === nodeId);
    row?.scrollIntoView({block:'nearest'}); save();
  };
  $('#book-menu').onclick = () => setSidebar(!sidebarOpen, true);
  $('#book-close').onclick = () => setSidebar(false, true);
  sidebar.addEventListener('cancel', event => { event.preventDefault(); setSidebar(false, true); });
  sidebar.addEventListener('click', event => {
    const rect = sidebar.getBoundingClientRect();
    if (sidebar.matches(':modal') && event.target === sidebar && (event.clientX > rect.right || event.clientY > rect.bottom)) setSidebar(false,true);
  });
  document.querySelectorAll('[data-pane]').forEach(button => {
    button.onclick = () => selectPane(button.dataset.pane);
    button.addEventListener('keydown', event => {
      const tabs = Array.from(document.querySelectorAll('[data-pane]'));
      const index = tabs.indexOf(button);
      const next = event.key === 'ArrowRight' ? (index+1)%tabs.length : event.key === 'ArrowLeft' ? (index+tabs.length-1)%tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 : null;
      if (next !== null) { event.preventDefault(); selectPane(tabs[next].dataset.pane,true); }
    });
  });
  $('#book-search-form').onsubmit = event => { event.preventDefault(); if (metadata) search($('#book-search').value); };
  $('#book-search-prev').onclick = () => search(searchQuery,Math.max(1,searchPage-1),searchTitleOnly);
  $('#book-search-next').onclick = () => search(searchQuery,Math.min(searchTotalPages,searchPage+1),searchTitleOnly);
  $('#book-search').oninput = save;
  $('#book-print').onclick = () => send('print');
  // The content origin cannot read app storage; share the resolved theme through the bridge.
  new MutationObserver(() => send('theme', {theme:document.documentElement.dataset.theme}))
    .observe(document.documentElement, {attributes:true, attributeFilter:['data-theme']});
  narrow.addEventListener('change', () => setSidebar(!narrow.matches));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && $('#book-filter').value && sidebar.contains(document.activeElement) && pane === 'contents') {
      event.preventDefault(); $('#book-filter-clear').click();
    } else if (event.key === 'F6') {
      event.preventDefault(); if (sidebar.matches(':modal')) setSidebar(false);
      frame.focus(); send('focus');
    } else if (event.key === '/' && !event.isComposing && !event.ctrlKey && !event.metaKey && !event.altKey &&
               !event.target.closest('input,textarea,select,[contenteditable=true]')) {
      event.preventDefault(); selectPane('search'); setSidebar(true,true);
    }
  });
  $('.skip-link').onclick = event => { event.preventDefault(); frame.focus(); send('focus'); };
  for (const selector of ['#book-tree-scroll','#panel-outline','#panel-search']) $(selector).addEventListener('scroll',save,{passive:true});
  window.addEventListener('pagehide',save);
  window.addEventListener('popstate', async () => {
    if (!metadata) return;
    const saved = history.state?.book?.snapshot === metadata.snapshot ? history.state.book : null;
    restoring = true; restore(saved); restoring = false;
    if (saved?.search?.query) search(saved.search.query,saved.search.page,saved.search.titleOnly,saved.search.scroll);
    try { await load(readRoute(),saved); } catch (error) { showError(error.message); }
  });
  $('#book-retry').onclick = () => {
    try { metadata ? load(readRoute()) : boot(); } catch (error) { showError(error.message); }
  };
  async function boot() {
    const saved = history.state?.book;
    restoring = true; setSidebar(!narrow.matches);
    try {
      metadata = await api('/api/reader');
      for (const node of metadata.nodes) {
        nodes.set(node.id,node);
        if (!children.has(node.parent_id)) children.set(node.parent_id,[]);
        children.get(node.parent_id).push(node);
        if (node.status === 'present') {
          if (!pageNodes.has(node.path)) pageNodes.set(node.path,[]);
          pageNodes.get(node.path).push(node);
        }
      }
      $('#book-snapshot').textContent = text('snapshot',{id:metadata.snapshot.slice(0,8)});
      const current = saved?.snapshot === metadata.snapshot ? saved : null;
      restore(current); renderTree(); restoring = false;
      if (current?.search?.query) search(current.search.query,current.search.page,current.search.titleOnly,current.search.scroll);
      await load(readRoute(),current);
    } catch (error) { showError(error.message); }
    finally { restoring = false; }
  }
  boot();
})();
