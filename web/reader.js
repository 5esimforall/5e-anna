/* Directory-scoped reading, one outer scroll surface and one application history. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const ui = annaReaderLabels[document.documentElement.lang.startsWith('en') ? 'en' : 'zh'];
  const {esc, stored} = annaKit;
  const text = (key, values = {}) => Object.entries(values).reduce((s, [k,v]) => s.replaceAll('{'+k+'}', v), ui[key]);
  for (const element of document.querySelectorAll('[data-text]')) element.textContent = ui[element.dataset.text];
  for (const element of document.querySelectorAll('[data-label]')) {
    element.setAttribute('aria-label', ui[element.dataset.label]); element.title = ui[element.dataset.label];
  }
  for (const element of document.querySelectorAll('[data-placeholder]')) element.placeholder = ui[element.dataset.placeholder];
  const sidebar = $('#book-sidebar'), narrow = matchMedia('(max-width:800px)');
  let metadata, model, scope, flow, page, route, nodeId = '', focusedNode = '', nodes = new Map(), children = new Map(), pageNodes = new Map();
  let expanded = new Set(), pane = 'contents', sidebarOpen = !narrow.matches;
  let position = {x:0,y:0}, navigationId = 0, request, loading = true;
  let searchRequest, searchSequence = 0, searchPage = 1, searchTotalPages = 0, searchQuery = '', searchTitleOnly = false;
  let restoring = true, contentsFollowing = true, contentsSyncTimer;
  history.scrollRestoration = 'manual';

  function readRoute() {
    const p = new URL(location.href).searchParams;
    for (const key of ['root','entry','page','anchor','node']) if (p.getAll(key).length > 1) throw new Error('invalid_location');
    if (p.has('entry') && p.has('page') || p.has('entry') && !p.get('entry') || p.has('page') && !p.get('page')) throw new Error('invalid_location');
    if (p.has('root') && !p.get('root')) throw new Error('invalid_location');
    return p.has('entry') ? {root:p.get('root') || '',entry:p.get('entry'),node:p.get('node') || ''} :
      {root:p.get('root') || '',page:p.get('page') || (p.has('root') ? '' : metadata?.default_page) || '',anchor:p.get('anchor') || '',node:p.get('node') || ''};
  }
  function routeURL(target) {
    const params = new URLSearchParams();
    for (const key of ['root','entry','page','anchor','node']) if (target[key]) params.set(key, target[key]);
    return '/read' + (params.size ? '?' + params : '');
  }
  function nodeRoute(node) { return node.status === 'present' ? {root:node.id,page:node.path,anchor:node.fragment,node:node.id} : {root:node.id}; }
  function pageRoute(item) { return {root:scope.root,page:item.path,anchor:item.fragment,node:item.node}; }
  function save() {
    if (!metadata || restoring || loading) return;
    const state = {version:2,snapshot:metadata.snapshot,root:scope?.root || '',page:page?.path || '',position, expanded:[...expanded], pane, sidebar:sidebarOpen, node:nodeId,
      followContents:contentsFollowing, filter:$('#book-filter').value, treeScroll:$('#book-tree-scroll').scrollTop, outlineScroll:$('#panel-outline').scrollTop,
      search:{query:searchQuery, titleOnly:searchTitleOnly, page:searchPage, draft:$('#book-search').value, scroll:$('#panel-search').scrollTop}};
    try { history.replaceState({...history.state, book:state}, '', location.href); } catch { /* History storage may be unavailable. */ }
  }
  /* Stella's places live in the shared workspace, newest first. A bare /read resumes the latest; a place chosen
     from Anna's switcher resumes in the tab that chose it. */
  function rememberedPosition(id = null) {
    const places = annaWorkspace.read(metadata.snapshot).book;
    const place = id === null ? places[0] : places.find(memory => annaWorkspace.memoryId.book(memory) === id);
    return annaReaderModel.remembered(place, metadata.snapshot, model);
  }
  function rememberPlace(root, path, node, offset) {
    annaWorkspace.update(metadata.snapshot, state => annaWorkspace.rememberBook(state, {root, page:path, node, position:offset,
      label:nodes.get(node)?.label || (path === page?.path ? page.title : ''), scopeLabel:nodes.get(root)?.label || '', at:Date.now()}));
  }
  function rememberPosition() {
    if (!metadata || !page || loading || !flow?.active || flow.active.status !== 'ready') return;
    rememberPlace(scope.root, page.path, nodeId, position);
  }
  // Earlier versions kept one saved position; the first visit makes it the first remembered place and drops it.
  const legacyPositionKey = 'anna.bookPosition';
  function importLegacyPosition() {
    const raw = stored.get(legacyPositionKey);
    if (raw === null) return;
    stored.set(legacyPositionKey, null);
    let value = null;
    try { value = annaReaderModel.remembered(JSON.parse(raw), metadata.snapshot, model); } catch { value = null; }
    if (value && !annaWorkspace.read(metadata.snapshot).book.length) rememberPlace(value.root, value.page, value.node, value.position);
  }
  // The definition you arrive at, and then the one you read, is the topic Anna can look up.
  let topicId = '';
  function followTopic() {
    const current = flow?.definitionInView();
    if (!current || current.path !== page?.path || current.id === topicId) return;
    shareTopic(current.id);
  }
  function shareTopic(id) {
    topicId = id;
    const entry = page?.definitions.find(definition => definition.id === id);
    if (entry) annaWorkspace.update(metadata.snapshot, state => annaWorkspace.setTopic(state, {entry:id, name:entry.name, from:'book', at:Date.now()}));
  }
  function capturePosition() { flow?.capture(); save(); }
  function activeDocument(data, nextPosition, item) {
    if (loading || restoring) return;
    const changed = page?.path !== data.path;
    page = data; position = nextPosition;
    if (changed) {
      nodeId = item.node;
      route = {root:scope.root,page:page.path,node:nodeId};
      history.replaceState(history.state,'',routeURL(route));
      renderPage();
    }
    save(); rememberPosition(); followTopic();
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
    if (open) scheduleContentsSync();
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
    if (pane === 'contents') scheduleContentsSync();
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
  /* The reading bar's path: ancestors outside the scope change it; the scope name and the levels after it move
     within it. Each level after the scope name lists its siblings, and a trailing control lists the current node's
     children, so the path doubles as a one-level-at-a-time view of the contents tree. */
  const chevron = d => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
  function firstReadable(node) {
    if (node.status === 'present') return node;
    for (const child of children.get(node.id) || []) { const found = firstReadable(child); if (found) return found; }
    return null;
  }
  function scopeTarget(node) {
    const target = firstReadable(node);
    return target && {root:scope.root,page:target.path,anchor:target.fragment,node:target.id};
  }
  function renderPath() {
    pathMenu.close(false);
    const path = $('#book-path'), focused = [...path.querySelectorAll('button')].indexOf(document.activeElement);
    const scopeNode = nodes.get(scope.root), scopeName = scopeNode?.label || page.title;
    const outside = scopeNode ? ancestors(scope.root).slice(0,-1) : [];
    const lineage = scopeNode && nodes.has(nodeId) ? ancestors(nodeId) : [];
    const inside = lineage.some(node => node.id === scope.root) ? lineage.slice(lineage.findIndex(node => node.id === scope.root)+1) : [];
    const current = inside.at(-1) || scopeNode;
    const item = (className, ...content) => { const li = document.createElement('li'); li.className = className; li.append(...content); return li; };
    const items = scopeNode ? outside.map(node => {
      if (node.status !== 'present' && !children.has(node.id)) return item('book-path-outside',node.label);
      const link = document.createElement('a'); link.href = routeURL(nodeRoute(node)); link.textContent = node.label;
      return item('book-path-outside',link);
    }) : [item('book-path-outside',ui.notInContents)];
    const title = document.createElement(scopeNode ? 'a' : 'span'); title.id = 'book-scope-title'; title.textContent = scopeName;
    title.title = text('scopeTitle',{name:scopeName});
    if (scopeNode) { title.href = routeURL({root:scope.root}); title.dataset.node = scope.root; }
    if (current === scopeNode) title.setAttribute('aria-current','page');
    items.push(item('book-path-scope' + (current === scopeNode ? ' current' : ''),title));
    const more = document.createElement('button'); more.type = 'button'; more.dataset.menu = 'more'; more.textContent = '…';
    more.setAttribute('aria-label',ui.pathMore); more.title = ui.pathMore;
    const moreItem = item('book-path-more',more); moreItem.hidden = true; items.push(moreItem);
    for (const node of inside) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'book-path-segment';
      button.dataset.menu = 'siblings'; button.dataset.node = node.id; button.title = node.label;
      button.innerHTML = `<span>${esc(node.label)}</span>` + chevron('M6 9l6 6 6-6');
      if (node === current) button.setAttribute('aria-current','page');
      items.push(item(node === current ? 'book-path-level current' : 'book-path-level',button));
    }
    if (current && children.get(current.id)?.length) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'book-path-children';
      button.dataset.menu = 'children'; button.dataset.node = current.id; button.innerHTML = chevron('M9 6l6 6-6 6');
      button.setAttribute('aria-label',text('pathChildren',{name:current.label})); button.title = button.getAttribute('aria-label');
      items.push(item('book-path-down',button));
    }
    for (const [index,button] of items.flatMap(li => [...li.querySelectorAll('button')]).entries()) {
      button.id = 'book-path-control-' + index; pathMenu.bind(button);
    }
    path.replaceChildren(...items);
    $('#book-breadcrumb').title = [...(scopeNode ? outside.map(n => n.label) : [ui.notInContents]), scopeName, ...inside.map(n => n.label)].join(' / ');
    fitPath();
    if (focused >= 0) (path.querySelectorAll('button')[focused] || path.querySelector('[aria-current=page]'))?.focus({preventScroll:true});
  }
  // Fold levels that do not fit: ancestors outside the scope first, then the levels between, then the scope name.
  // The current level is kept and truncated last; folded levels inside the scope stay reachable from "…".
  function fitPath() {
    const path = $('#book-path'), more = path.querySelector('.book-path-more');
    if (!more) return;
    path.querySelectorAll('.folded').forEach(li => li.classList.remove('folded')); more.hidden = true;
    path.classList.add('measuring');
    const overflowing = () => path.scrollWidth > path.clientWidth + 1;
    const candidates = [...path.querySelectorAll('.book-path-outside'),...path.querySelectorAll('.book-path-level:not(.current)'),
      ...path.querySelectorAll('.book-path-scope:not(.current)')];
    for (const li of candidates) {
      if (!overflowing()) break;
      li.classList.add('folded');
      if (!li.classList.contains('book-path-outside')) more.hidden = false;
    }
    path.classList.remove('measuring');
    const visible = [...path.children].filter(li => li.getClientRects().length);
    path.querySelectorAll('.lead').forEach(li => li.classList.remove('lead'));
    visible[0]?.classList.add('lead');
  }
  function pathMenuNodes(button) {
    if (button.dataset.menu === 'more') return [...$('#book-path').querySelectorAll('.folded [data-node]')].map(el => nodes.get(el.dataset.node));
    const node = nodes.get(button.dataset.node);
    return children.get(button.dataset.menu === 'children' ? node.id : node.parent_id) || [];
  }
  // Each item opens the first readable page under its node, within the current scope.
  function pathMenuItems(button) {
    const onPath = new Set(ancestors(nodeId).map(node => node.id));
    if (!nodes.has(nodeId) && scope.root) onPath.add(scope.root);
    return pathMenuNodes(button).map(node => {
      const target = scopeTarget(node), entry = document.createElement(target ? 'a' : 'span');
      if (target) entry.href = routeURL(target); else entry.setAttribute('aria-disabled','true');
      if (onPath.has(node.id)) entry.setAttribute('aria-current', node.id === nodeId ? 'page' : 'location');
      if (button.dataset.menu === 'more') entry.style.setProperty('--depth', ancestors(node.id).length - ancestors(scope.root).length);
      const label = document.createElement('span'); label.className = 'book-path-label'; label.textContent = node.label; entry.append(label);
      const count = children.get(node.id)?.length;
      if (count) { const badge = document.createElement('span'); badge.className = 'book-path-count'; badge.setAttribute('aria-hidden','true'); badge.textContent = count; entry.append(badge); }
      return entry;
    });
  }
  // The vertical arrows move within a menu; the horizontal arrows move to the next visible level's menu.
  const pathMenu = annaMenu.create($('#book-path-menu'), {items:pathMenuItems,
    place(menu, button) {
      const bar = $('#book-bar').getBoundingClientRect(), anchor = button.getBoundingClientRect();
      menu.style.left = Math.max(8, Math.min(anchor.left - bar.left, bar.width - menu.offsetWidth - 8)) + 'px';
    },
    adjacent(button, step) {
      const controls = [...$('#book-path').querySelectorAll('button[aria-haspopup]')].filter(el => el.getClientRects().length);
      return controls[controls.indexOf(button) + step];
    }});
  function contentsRow(id) {
    return Array.from($('#book-tree').querySelectorAll('[data-node]')).find(row => row.dataset.node === id);
  }
  function updateContentsStatus() {
    $('#book-follow-status').textContent = contentsFollowing ? ui.followingContents : ui.pausedContents;
    // The locate tool carries the follow state: lit while the contents follow the text.
    $('#book-locate').dataset.state = contentsFollowing ? 'following' : 'paused';
    $('#book-locate').title = contentsFollowing ? ui.locateFollowing : ui.locateAndFollow;
  }
  function setFilterOpen(open, focus = false) {
    const filtering = open || !!$('#book-filter').value;
    $('.book-tree-tools').classList.toggle('filtering', filtering);
    $('#book-filter-open').setAttribute('aria-expanded', String(filtering));
    if (focus) (filtering ? $('#book-filter') : $('#book-filter-open')).focus();
  }
  function pauseContentsFollow() {
    if (!contentsFollowing) return;
    contentsFollowing = false; updateContentsStatus(); save();
  }
  function updateContentsCurrent() {
    for (const row of $('#book-tree').querySelectorAll('[data-node]')) {
      const selected = row.dataset.node === nodeId, content = row.firstElementChild;
      content.classList.toggle('current',selected);
      const link = content.querySelector('a');
      if (selected) link?.setAttribute('aria-current','page');
      else link?.removeAttribute('aria-current');
    }
  }
  // Reveals a row with the least movement, or puts it at the top so the subtree below it fills the view.
  function scrollContentsTo(id, atTop = false) {
    const item = contentsRow(id)?.firstElementChild, scroll = $('#book-tree-scroll');
    if (!item) return;
    const rect = item.getBoundingClientRect(), top = scroll.getBoundingClientRect().top + scroll.clientTop;
    const bottom = top + scroll.clientHeight;
    if (!atTop && rect.top <= top && rect.bottom >= bottom) return;
    const delta = atTop ? rect.top-top : rect.top < top ? rect.top-top : rect.bottom > bottom ? rect.bottom-bottom : 0;
    // Scroll only the directory, never the page or the reader's document surface.
    if (delta) scroll.scrollTop += delta;
  }
  function syncContents() {
    if (!metadata || restoring || loading) return;
    updateContentsCurrent();
    if (!contentsFollowing || !sidebarOpen || pane !== 'contents' || $('#book-filter').value.trim()) return;
    const count = expanded.size;
    reveal(nodeId);
    if (expanded.size !== count) renderTree();
    scrollContentsTo(nodeId); save();
  }
  function scheduleContentsSync() {
    clearTimeout(contentsSyncTimer);
    contentsSyncTimer = setTimeout(syncContents,180);
  }
  function renderTree(focusId = '') {
    if (!metadata) return;
    const focused = $('#book-tree').contains(document.activeElement) ? document.activeElement : null;
    const retainedFocusId = focused?.closest('[data-node]')?.dataset.node;
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
          ${node.status==='present' || hasChildren?`<a tabindex="-1" href="${esc(routeURL(nodeRoute(node)))}"${selected?' aria-current="page"':''}>${esc(node.label)}</a>`:`<span class="toc-label"${node.status==='missing'?` title="${esc(ui.missingNode)}"`:''}>${esc(node.label)}</span>`}</div>
          ${hasChildren && open?`<ul role="group">${branch(node.id,depth+1)}</ul>`:''}</li>`;
      }).join('');
    }
    $('#book-tree').innerHTML = branch('',0);
    $('#book-tree-empty').hidden = visibleIds.length > 0;
    const id = [focusId,focusedNode,nodeId,visibleIds[0]].find(id => visibleIds.includes(id));
    const row = id && contentsRow(id);
    if (row) { row.tabIndex = 0; focusedNode = id; if (focusId) row.focus({preventScroll:true}); }
    if (!focusId && retainedFocusId) {
      const retained = contentsRow(retainedFocusId);
      const target = focused.matches('a,button') ? retained?.firstElementChild.querySelector(focused.tagName.toLowerCase()) : retained;
      target?.focus({preventScroll:true});
    }
  }
  function renderPage() {
    document.title = annaPageTitle('book', page.title);
    renderPath();
    $('#book-outline-title').textContent = page.title;
    const definitions = page.definitions.filter(entry => entry.locatable);
    $('#book-outline-empty').hidden = definitions.length > 0;
    $('#book-outline').innerHTML = definitions.map(entry => `<li><a href="${esc(routeURL({root:scope.root,entry:entry.id,node:nodeId}))}"${route.entry===entry.id || route.anchor===entry.fragment?' aria-current="location"':''}>${esc(entry.name)}</a><a href="/?entry=${encodeURIComponent(entry.id)}" target="_blank" rel="noopener" aria-label="${esc(ui.entry+': '+entry.name)}" title="${esc(ui.entry)}">↗</a></li>`).join('');
    const ordered = scope.pages;
    const index = ordered.findIndex(item => item.path === page.path);
    $('#book-paging').hidden = false;
    $('#book-expand').disabled = !nodes.has(scope.root);
    $('#book-page-count').textContent = text('pageCount',{page:index+1,pages:ordered.length});
    for (const [selector,offset] of [['#book-previous',-1],['#book-next',1]]) {
      const link = $(selector), node = index >= 0 ? ordered[index+offset] : null;
      link.setAttribute('aria-disabled', String(!node));
      if (node) { link.href = routeURL(pageRoute(node)); link.title = node.label; link.tabIndex = 0; }
      else { link.removeAttribute('href'); link.removeAttribute('title'); link.tabIndex = -1; }
    }
    scheduleContentsSync();
  }
  function showError(code) {
    loading = false;
    $('#book-loading').hidden = true; $('#book-error').hidden = false;
    $('#book-error').classList.toggle('preserve-reading',!!flow?.active);
    if (!flow?.active) document.title = annaPageTitle('book');
    $('#book-error-text').textContent = ui.errors[code] || ui.errors.network;
    $('#book-main').setAttribute('aria-busy','false'); $('#book-status').textContent = '';
  }
  async function load(target, saved, preserveScope = false) {
    request?.abort(); request = new AbortController();
    const id = ++navigationId;
    loading = true;
    $('#book-loading').hidden = false; $('#book-error').hidden = true;
    $('#book-main').setAttribute('aria-busy','true'); $('#book-status').hidden = true;
    try {
      let root = target.root || saved?.root || '';
      if (root && !nodes.has(root)) throw new Error('invalid_location');
      let sourcePath = target.page;
      if (!target.entry && !sourcePath && root) {
        sourcePath = model.scope(root).pages[0]?.path;
        if (!sourcePath) throw new Error('empty_scope');
      }
      const query = new URLSearchParams({snapshot:metadata.snapshot});
      if (target.entry) query.set('entry',target.entry);
      else if (sourcePath) query.set('path',sourcePath);
      else throw new Error('invalid_location');
      const data = await api('/api/reader-page?' + query, request.signal);
      if (id !== navigationId) return;
      const choices = pageNodes.get(data.path) || [];
      if (target.node && !choices.some(n => n.id === target.node)) throw new Error('invalid_location');
      if (!root && preserveScope && scope?.byPath.has(data.path)) root = scope.root;
      if (!root) root = [target.node,saved?.node,choices[0]?.id].find(id => choices.some(n => n.id === id)) || '';
      const nextScope = model.scope(root,data.path);
      if (!nextScope.byPath.has(data.path)) throw new Error('outside_scope');
      const item = nextScope.byPath.get(data.path);
      if (target.node && !nextScope.memberIds.has(target.node)) throw new Error('outside_scope');
      const selectedNode = [target.node,saved?.node,nodeId,item.node].find(id => item.nodes.some(n => n.id === id)) || '';
      if (!scope || scope.root !== root || (!root && !scope.byPath.has(data.path))) {
        scope = nextScope; flow.reset(scope);
      }
      const destination = annaReaderModel.validPosition(saved?.position) ? saved.position : null;
      $('#book-paging').hidden = false;
      const result = await flow.open(data,{anchor:target.entry ? data.fragment : target.anchor || '',position:destination});
      if (id !== navigationId) return;
      page = data; nodeId = selectedNode; position = flow.location().position;
      route = target.entry ? {root,entry:target.entry,node:nodeId} : {root,page:data.path,anchor:target.anchor || '',node:nodeId};
      if (!saved?.expanded && contentsFollowing) reveal(nodeId);
      renderTree(); renderPage();
      if (saved) { $('#book-tree-scroll').scrollTop = saved.treeScroll || 0; $('#panel-outline').scrollTop = saved.outlineScroll || 0; }
      loading = false;
      $('#book-loading').hidden = true; $('#book-error').hidden = true;
      $('#book-main').setAttribute('aria-busy','false');
      $('#book-status').textContent = result.found ? '' : ui.errors.fragment_missing;
      $('#book-status').hidden = !!result.found;
      history.replaceState(history.state,'',routeURL(route));
      scheduleContentsSync(); save(); rememberPosition();
      if (target.entry) shareTopic(target.entry);
    } catch (error) {
      if (id === navigationId && error.name !== 'AbortError') showError(error.message);
    }
  }
  async function navigate(target, push = true, preserveScope = false) {
    capturePosition(); loading = true;
    const url = routeURL(target);
    if (push && url !== location.pathname+location.search) history.pushState(null,'',url);
    else history.replaceState(history.state,'',url);
    if (narrow.matches) setSidebar(false);
    await load(target,null,preserveScope);
  }
  function restore(saved) {
    expanded = new Set((saved?.expanded || []).filter(id => nodes.has(id)));
    $('#book-filter').value = saved?.filter || ''; setFilterOpen(false);
    contentsFollowing = saved?.followContents !== false && !$('#book-filter').value.trim();
    updateContentsStatus();
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

  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.target === '_blank') return;
    const url = new URL(link.href);
    if (url.origin !== location.origin || url.pathname !== '/read') return;
    event.preventDefault();
    const params = url.searchParams;
    const target = params.has('entry') ? {root:params.get('root') || '',entry:params.get('entry'),node:params.get('node') || ''} :
      {root:params.get('root') || '',page:params.get('page') || (params.has('root') ? '' : metadata?.default_page),anchor:params.get('anchor') || '',node:params.get('node') || ''};
    if (metadata) navigate(target);
  });
  $('#book-tree').addEventListener('click', event => {
    const button = event.target.closest('[data-expand]');
    if (button) {
      pauseContentsFollow();
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
    if (['ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Home','End','PageDown','PageUp',' '].includes(event.key)) pauseContentsFollow();
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
      if (node.status === 'present' || children.has(node.id)) navigate(nodeRoute(node));
      else { expanded.has(id) ? expanded.delete(id) : expanded.add(id); renderTree(id); }
    } else return;
    event.preventDefault(); next?.focus(); save();
  });
  $('#book-filter').addEventListener('input', () => { pauseContentsFollow(); renderTree(); save(); });
  $('#book-filter-clear').onclick = () => { pauseContentsFollow(); $('#book-filter').value = ''; renderTree(); $('#book-filter').focus(); save(); };
  $('#book-filter-open').onclick = () => setFilterOpen(true, true);
  $('.book-filter').addEventListener('focusout', event => { if (!$('.book-filter').contains(event.relatedTarget)) setFilterOpen(false); });
  $('#book-collapse').onclick = () => { pauseContentsFollow(); expanded.clear(); renderTree(); save(); };
  // Opens the reading scope's whole subtree and its path, then shows it from its first row.
  $('#book-expand').onclick = () => {
    if (!nodes.has(scope?.root)) return;
    reveal(scope.root);
    (function open(id) { if (!children.get(id)?.length) return; expanded.add(id); children.get(id).forEach(child => open(child.id)); })(scope.root);
    renderTree(); scrollContentsTo(scope.root, true); save();
  };
  $('#book-locate').onclick = () => {
    clearTimeout(contentsSyncTimer); contentsFollowing = true; updateContentsStatus();
    $('#book-filter').value = ''; setFilterOpen(false); reveal(nodeId); renderTree(nodeId);
    scrollContentsTo(nodeId); save();
  };
  for (const type of ['wheel','touchmove']) $('#book-tree-scroll').addEventListener(type,pauseContentsFollow,{passive:true});
  $('#book-tree-scroll').addEventListener('pointerdown',event => {
    if (event.target === $('#book-tree-scroll')) pauseContentsFollow(); // Native scrollbar dragging.
  });
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
  // Choosing an item closes the menu first; the document click handler then navigates within the scope.
  pathMenu.attach($('#book-path'));
  new ResizeObserver(fitPath).observe($('#book-bar'));
  // Share the resolved theme through the same protocol as location and focus.
  new MutationObserver(() => flow?.theme(document.documentElement.dataset.theme))
    .observe(document.documentElement, {attributes:true, attributeFilter:['data-theme']});
  narrow.addEventListener('change', () => setSidebar(!narrow.matches));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && $('#book-filter').value && sidebar.contains(document.activeElement) && pane === 'contents') {
      event.preventDefault(); $('#book-filter-clear').click();
    } else if (event.key === 'Escape' && document.activeElement === $('#book-filter')) {
      event.preventDefault(); setFilterOpen(false, true);
    } else if (event.key === 'F6') {
      event.preventDefault(); if (sidebar.matches(':modal')) setSidebar(false);
      flow?.focus();
    } else if (event.key === '/' && !event.isComposing && !event.ctrlKey && !event.metaKey && !event.altKey &&
               !event.target.closest('input,textarea,select,[contenteditable=true]')) {
      event.preventDefault(); selectPane('search'); setSidebar(true,true);
    }
  });
  $('.skip-link').onclick = event => { event.preventDefault(); flow?.focus(); };
  for (const selector of ['#book-tree-scroll','#panel-outline','#panel-search']) $(selector).addEventListener('scroll',save,{passive:true});
  window.addEventListener('pagehide',capturePosition);
  document.addEventListener('visibilitychange', () => { if (document.hidden) capturePosition(); });
  window.addEventListener('popstate', async () => {
    if (!metadata) return;
    const saved = history.state?.book?.snapshot === metadata.snapshot ? history.state.book : null;
    loading = true; restoring = true; restore(saved); restoring = false;
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
      model = annaReaderModel.index(metadata.nodes);
      ({nodes,children,pageNodes} = model);
      flow = new AnnaReaderFlow({scroll:$('#book-scroll'),container:$('#book-pages'),snapshot:metadata.snapshot,contentBase:metadata.content_base,
        fetchPage:(path,signal) => api('/api/reader-page?'+new URLSearchParams({path,snapshot:metadata.snapshot}),signal),
        labels:ui,text,onActive:activeDocument,onNavigate:target => navigate(target,true,true),
        onTools:() => $('#book-menu').focus(),onSearch:() => { selectPane('search'); setSidebar(true,true); }});
      $('#book-version').hidden = !metadata.source_release;
      $('#book-version').textContent = metadata.source_release ? text('sourceVersion',metadata.source_release) : '';
      importLegacyPosition();
      annaCompanions.mount({page:'book', snapshot:metadata.snapshot});
      const current = saved?.snapshot === metadata.snapshot ? saved : null;
      const params = new URL(location.href).searchParams;
      // A place chosen from Anna's switcher arrives with its location in the URL and its offset in the workspace.
      const resumeId = annaWorkspace.takeResume('book');
      let resumed = !current && resumeId !== null ? rememberedPosition(resumeId) : null;
      if (resumed && (params.get('page') !== resumed.page || (params.get('root') || '') !== resumed.root)) resumed = null;
      const remembered = !current && !resumed && !['root','entry','page','anchor','node'].some(key => params.has(key))
        ? rememberedPosition() : null;
      if (remembered) history.replaceState(history.state, '', routeURL({root:remembered.root,page:remembered.page,node:remembered.node}));
      restore(current); renderTree(); restoring = false;
      if (current?.search?.query) search(current.search.query,current.search.page,current.search.titleOnly,current.search.scroll);
      await load(readRoute(),current || resumed || remembered);
    } catch (error) { showError(error.message); }
    finally { restoring = false; }
  }
  boot();
})();
