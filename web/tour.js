'use strict';
/* Anna's tour: where to start and what to read next. Each column is one level of the way down, from a purpose
   to the book's chapters, steps and entries; choosing a row opens the next level beside it. At most three levels
   stay open and the ones above them fold into spines on the left. Only the deepest level widens, and only when it
   reads as a detail: then its own words show as the book sets them, and once a row below it opens it narrows back
   to its quoted lead. Rows lead on to the next level, to Stella's reading or to Anna's table. A level opens once it
   is ready, in its final form, and the columns change width without rewrapping the text in them. */
const annaTourLabels = {
  zh:{title:'导览', skip:'跳到导览', intro:'从想做的事出发，逐层走进原书的章节、步骤与条目。',
    groups:{shelf:'书架', outline:'本页', choices:'这一步要选择', contents:'目录', entries:'条目',
      parts:'包含', class:'所属职业', classes:'职业', subclasses:'子职', feats:'授予专长', spells:'法术',
      books:'书目', packages:'资料包', relations:'关联条目'},
    facet:'按{field}', readBook:'在 Stella 中阅读', openEntry:'在 Anna 中查看', openQuery:'在 Anna 中查看 {count} 条',
    spellList:'可用法术', more:'另有 {count} 项', moreInAnna:'另有 {count} 项，在 Anna 中查看全部',
    previous:'上一项：{name}', next:'下一项：{name}', back:'‹ {name}', table:'表', versions:'其他版本',
    rest:'选择一项，它会在这里展开', onward:'由此继续', onwardJump:'由此继续 · {count} 项 ↓', unfold:'展开「{name}」',
    filterPlaceholder:'筛选此列表…', filterLabel:'筛选 {name}', filterCount:'{shown} / {total}', filterEmpty:'没有匹配项。',
    loading:'正在读取…', retry:'重试', readError:'无法读取这一层。', nodeMissing:'这一层已不在当前原文快照中，已回到上一层。',
    bundleChanged:'数据已经重建，请刷新页面。', unavailable:'导览需要完整服务：请使用 make up 启动后打开。', pagesUnavailable:'无法读取导览数据，请重试。',
    status:{missing:'未找到对应原文', ambiguous:'原文对应不唯一', placeholder:'分组'},
    rulesets:{'5e':['2014','2014 版规则（5e）'],'5r':['2024','2024 版规则（5r）'],'待核':['待核','规则版本待核']}},
  en:{title:'Tour', skip:'Skip to the tour', intro:'Start from what you want to do, then go down through the book\'s chapters, steps and entries.',
    groups:{shelf:'Bookshelf', outline:'On this page', choices:'Choose in this step', contents:'Contents', entries:'Entries',
      parts:'Contains', class:'Class', classes:'Classes', subclasses:'Subclasses', feats:'Granted feats', spells:'Spells',
      books:'Books', packages:'Packages', relations:'Related entries'},
    facet:'By {field}', readBook:'Read in Stella', openEntry:'Open in Anna', openQuery:'Open {count} in Anna',
    spellList:'Spell list', more:'{count} more', moreInAnna:'{count} more; open them all in Anna',
    previous:'Previous: {name}', next:'Next: {name}', back:'‹ {name}', table:'Table', versions:'Other versions',
    rest:'Choose an item to open it here', onward:'Where this leads', onwardJump:'Where this leads · {count} ↓', unfold:'Show {name}',
    filterPlaceholder:'Filter this list…', filterLabel:'Filter {name}', filterCount:'{shown} / {total}', filterEmpty:'No matching items.',
    loading:'Loading…', retry:'Try again', readError:'Unable to load this level.', nodeMissing:'That level is not in the current source snapshot; showing the level above.',
    bundleChanged:'The data was rebuilt. Refresh this page.', unavailable:'The tour needs the full service: start it with make up.', pagesUnavailable:'Unable to load tour data. Please retry.',
    status:{missing:'Source not found', ambiguous:'Source match not unique', placeholder:'Group'},
    rulesets:{'5e':['2014','2014 rules (5e)'],'5r':['2024','2024 rules (5r)'],'待核':['Unverified','Rules version unverified']}}
};
const annaTour = (() => {
  const lang = document.documentElement.lang.startsWith('en') ? 'en' : 'zh';
  const ui = annaTourLabels[lang], {esc, stored} = annaKit;
  // A level is named by one segment: a topic key, or `collection:segment` for a level of one collection's own.
  const MEMORY = 'anna.tour', SEGMENT = /^[A-Za-z0-9_.:-]+$/, FILTER_THRESHOLD = 24;
  const columns = document.querySelector('#tour-columns'), notice = document.querySelector('#tour-notice');
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  // How many levels stay open side by side; the rest fold. Phones read one level at a time.
  const roomy = window.matchMedia('(min-width: 1280px)'), phone = window.matchMedia('(max-width: 760px)');
  const openLevels = () => roomy.matches ? 3 : phone.matches ? 1 : 2;
  // A held key passes over rows; the row it rests on opens once the key is released or after this long.
  const FOLLOW_DELAY = 150;
  // h j k l move as ← ↓ ↑ → do; in a detail's text, j and k scroll it by about the step of the arrow keys.
  const VIM_KEYS = {h:'ArrowLeft', j:'ArrowDown', k:'ArrowUp', l:'ArrowRight'}, LINE_STEP = 40;
  // Levels that answer within this time open in one step; slower ones first show that they are loading.
  const LOADING_DELAY = 180;
  // How long the columns take to reach their new widths: the shared time and curve of a move.
  const motionToken = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const MOVE_TIME = parseFloat(motionToken('--anna-move')) || 200, ease = motionToken('--anna-ease') || 'ease-out';
  const views = new Map(), layouts = new Map(), reading = new WeakMap(), motions = new WeakMap();
  /* `shown` is the generation whose levels are in place; `landing` is where the focus goes once the levels on their
     way are. `openOnRest` opens the row a held key rests on, when the key is released or `restTimer` runs out. */
  let snapshot = null, snapshots = {}, path = [], generation = 0, shown = 0, landing = null, mounted = false, topic = null;
  let restTimer = 0, openOnRest = null;
  for (const el of document.querySelectorAll('[data-ui]')) el.textContent = ui[el.dataset.ui];

  const fill = (template, values) => Object.entries(values).reduce((s, [k, v]) => s.replaceAll('{' + k + '}', v), template);
  const fmt = n => Number(n).toLocaleString(lang === 'en' ? 'en' : 'zh-CN');
  const labelText = value => value && typeof value === 'object' ? (value[lang] || value.zh || value.en || '') : (value || '');
  const nameOf = item => item.label ? labelText(item.label) : item.title || '';
  const rulesetTag = r => r && ui.rulesets[r] ? `<span class="tour-ruleset${r === '待核' ? ' unverified' : ''}" title="${esc(ui.rulesets[r][1])}">${esc(ui.rulesets[r][0])}</span>` : '';
  const joinSegments = segments => segments.join('/');
  const segmentsOf = search => {
    const at = new URLSearchParams(search).get('at') || '';
    const parts = at.split('/').filter(Boolean);
    return parts.length && parts.every(part => SEGMENT.test(part)) ? parts : [];
  };
  const tourHref = segments => annaKit.href('/tour' + (segments.length ? '?' + new URLSearchParams({at:joinSegments(segments)}) : ''));
  const queryHref = query => annaKit.href('/?' + new URLSearchParams(Object.entries(query).flatMap(([key, values]) => (Array.isArray(values) ? values : [values]).map(v => [key, v]))));
  const readHref = read => annaKit.href('/read?' + new URLSearchParams(Object.entries(read).filter(([, v]) => v !== null && v !== undefined && v !== '')));
  // Anna names an entry by its own id; the collection that holds it answers.
  const entryHref = anna => annaKit.href('/?' + new URLSearchParams({...(anna.type ? {type:anna.type} : {}), entry:anna.entry}));
  // A segment of one collection's own: the collection that answers it, and its own name there.
  const collectionOf = segment => annaWorkspace.collectionOf(segment);
  const icon = '<svg class="tour-item-go" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>';

  class LoadError extends Error {
    constructor(code, status) { super(code); this.code = code; this.status = status; }
  }
  // The tour reads the reader's library; every request carries it as Anna's do.
  async function load(api, cache, segment) {
    if (cache.has(segment)) return cache.get(segment);
    const params = new URLSearchParams({at:segment});
    if (snapshot) params.set('snapshot', snapshot);
    let response, body = null;
    try { response = await annaKit.request(annaLibrary.withScope(api + '?' + params)); } catch { throw new LoadError('network', 0); }
    try { body = await response.json(); } catch { body = null; }
    if (!response.ok || !body) throw new LoadError(body?.code || 'error', response.status);
    if (body.snapshot) { snapshot ||= body.snapshot; snapshots = body.snapshots || snapshots; }
    cache.set(segment, body);
    return body;
  }
  const fetchView = segment => load('/api/tour', views, segment);

  function versionLabel(v) {
    const rs = ui.rulesets[v.ruleset]?.[0] || v.ruleset || '?';
    const col = v.collection ? annaLibrary.nameOf(v.collection) : '';
    const src = v.source || '';
    return {
      text:[col, src, rs].filter(Boolean).join(' · '),
      title:[col, src, ui.rulesets[v.ruleset]?.[1] || v.ruleset || ''].filter(Boolean).join(' · ')
    };
  }
  // One row. Rows that open a level carry the path they lead to; rows that leave for Anna's table do not.
  function item(row, group, index, depth, view) {
    const title = row.kind === 'query' && !row.title ? ui.spellList : nameOf(row);
    const tags = [];
    if (['section', 'choices', 'entries', 'class', 'subclasses', 'feats'].includes(group.key) && row.ruleset !== view.ruleset) tags.push(rulesetTag(row.ruleset));
    if (row.table) tags.push(`<span class="tour-tag">${esc(ui.table)}</span>`);
    const status = ui.status[row.status] && row.status !== 'placeholder' ? ui.status[row.status] : '';
    // A part of the shelf a later collection adds names that collection.
    const hint = status || row.hint || (row.collection ? annaLibrary.nameOf(row.collection) : '');
    const main = `<span class="tour-item-main"><span class="tour-item-title">${esc(title)}</span>${hint ? `<span class="tour-item-hint">${esc(hint)}</span>` : ''}</span>` +
      (tags.length ? `<span class="tour-item-tags">${tags.join('')}</span>` : '') +
      (row.count ? `<span class="tour-item-count">${fmt(row.count)}</span>` : '');
    const search = row.search ? ` data-search="${esc(row.search)}"` : '';
    if (row.kind === 'query')
      return `<li${search}><a class="tour-item" href="${esc(queryHref(row.query))}">${main}<img class="tour-item-out" src="${esc(annaKit.href('/anna-logo.png'))}" alt="" width="18" height="18"></a></li>`;
    const at = [...path.slice(0, depth), row.id];
    const disabled = row.kind === 'topic' && row.status !== 'ok';
    const step = group.ordered ? `<span class="tour-step">${index + 1}</span>` : '';
    const versions = row.versions?.length ? `<div class="tour-versions"><span>${esc(ui.versions)}</span>${row.versions.map(v => {
      const label = versionLabel(v);
      return `<a href="${esc(tourHref([...path.slice(0, depth), v.id]))}" data-at data-segment="${esc(v.id)}" title="${esc(label.title)}">${esc(label.text)}</a>`;
    }).join('')}</div>` : '';
    return `<li${search}><a class="tour-item" href="${esc(tourHref(at))}" data-at data-segment="${esc(row.id)}" tabindex="-1"${disabled ? ' aria-disabled="true"' : ''}>${step}${main}${icon}</a>${versions}</li>`;
  }
  function groupHeading(group, index, groups) {
    if (group.key === 'section') return nameOf(group);
    const label = labelText(group.label);
    if (group.key === 'facet') return typeof group.label === 'object' && group.label ? label : fill(ui.facet, {field:label});
    if (group.key === 'contents') return label || (groups.findIndex(g => g.key === 'contents') === index ? ui.groups.contents : '');
    return label || ui.groups[group.key] || '';
  }
  function filterBox(total, name, scope) {
    return `<div class="tour-filter" data-filter-scope="${esc(scope)}"><input type="search" class="tour-filter-input" placeholder="${esc(ui.filterPlaceholder)}" aria-label="${esc(fill(ui.filterLabel, {name:name || ui.title}))}"><span class="tour-filter-count" aria-live="polite">${esc(fmt(total))}</span></div>`;
  }
  // Steps number on across the groups a bare divider splits them into, so the path reads as one sequence.
  function groupHtml(group, index, view, depth, first = 0, columnFiltered = false) {
    const heading = groupHeading(group, index, view.groups);
    const filter = !columnFiltered && group.items.length > FILTER_THRESHOLD ? filterBox(group.items.length, heading, 'group') : '';
    const empty = filter ? `<p class="tour-filter-empty" hidden>${esc(ui.filterEmpty)}</p>` : '';
    const more = group.more ? `<p class="tour-more">${view.anna?.query ?
      `<a href="${esc(queryHref(view.anna.query))}">${esc(fill(ui.moreInAnna, {count:fmt(group.more)}))}</a>` : esc(fill(ui.more, {count:fmt(group.more)}))}</p>` : '';
    const label = heading ? ` aria-label="${esc(heading)}"` : '';
    return `<section class="tour-group${group.ordered ? ' ordered' : ''}" data-group="${esc(group.key)}"${label}>${heading ? `<h3>${esc(heading)}</h3>` : ''}${filter}` +
      `<${group.ordered ? 'ol' : 'ul'} role="list"${group.ordered && first ? ` start="${first + 1}"` : ''}>${group.items.map((row, i) => item(row, group, first + i, depth, view)).join('')}</${group.ordered ? 'ol' : 'ul'}>${empty}${more}</section>`;
  }
  function head(view, depth) {
    const title = view.kind === 'root' ? ui.title : nameOf(view);
    const parent = depth > 0 ? views.get(['', ...path][depth - 1]) : null;
    const back = parent ? `<a class="tour-back" href="${esc(tourHref(path.slice(0, depth - 1)))}" data-at>${esc(fill(ui.back, {name:parent.kind === 'root' ? ui.title : nameOf(parent)}))}</a>` : '';
    const trailParts = (view.trail || []).map(labelText).filter(Boolean);
    const trail = trailParts.length ? `<p class="tour-trail" title="${esc(trailParts.join(' › '))}">${esc(trailParts.join(' › '))}</p>` : '';
    const meta = [rulesetTag(view.ruleset), view.type_label && view.type !== 'section' ? esc(view.type_label) : '', esc((view.sources || []).join('；'))].filter(Boolean);
    return `<header>${back}${trail}<h2 id="tour-title-${depth}">${esc(title)}</h2>${meta.length ? `<p class="tour-meta">${meta.join('<span aria-hidden="true">·</span>')}</p>` : ''}` +
      (view.kind === 'root' ? `<p class="tour-intro">${esc(ui.intro)}</p>` : '') + '</header>';
  }
  function steps(view, depth) {
    const total = view.groups.reduce((n, g) => n + g.items.length, 0);
    const homogeneous = view.groups.length > 1 && new Set(view.groups.map(g => g.key)).size === 1 && !view.groups.some(g => g.ordered);
    const columnFiltered = homogeneous && total > FILTER_THRESHOLD;
    let first = 0;
    const html = view.groups.map((group, i) => {
      const part = groupHtml(group, i, view, depth, first, columnFiltered);
      if (group.ordered) first += group.items.length;
      return part;
    }).join('');
    if (!columnFiltered) return html;
    return filterBox(total, view.kind === 'root' ? ui.title : nameOf(view), 'column') + html + `<p class="tour-filter-empty" hidden>${esc(ui.filterEmpty)}</p>`;
  }
  function body(view, depth) {
    const lead = view.lead ? `<blockquote class="tour-lead" lang="zh-CN">${esc(view.lead.text)}${view.lead.truncated && !view.lead.text.endsWith('…') ? '<span class="tour-ellipsis"> …</span>' : ''}</blockquote>` : '';
    const actions = [];
    if (view.read) actions.push(`<a class="tour-go" href="${esc(readHref(view.read))}"><img src="${esc(annaKit.href('/stella-logo.png'))}" alt="" width="24" height="24">${esc(ui.readBook)}</a>`);
    if (view.anna?.query) actions.push(`<a class="tour-go" href="${esc(queryHref(view.anna.query))}"><img src="${esc(annaKit.href('/anna-logo.png'))}" alt="" width="24" height="24">${esc(fill(ui.openQuery, {count:fmt(view.anna.count)}))}</a>`);
    else if (view.anna?.entry) actions.push(`<a class="tour-go" href="${esc(entryHref(view.anna))}"><img src="${esc(annaKit.href('/anna-logo.png'))}" alt="" width="24" height="24">${esc(ui.openEntry)}</a>`);
    const status = view.status && !['ok', 'present', 'placeholder'].includes(view.status) ? `<p class="tour-status">${esc(ui.status[view.status] || view.status)}</p>` : '';
    const sequence = view.sequence && depth > 0 ? `<nav class="tour-sequence" aria-label="${esc(nameOf(view))}">${[['previous', 'prev'], ['next', 'next']].map(([key, rel]) => {
      const sibling = view.sequence[key];
      return sibling ? `<a rel="${rel}" href="${esc(tourHref([...path.slice(0, depth - 1), sibling.id]))}" data-at title="${esc(sibling.title)}">${esc(fill(ui[key], {name:sibling.title}))}</a>` : '';
    }).join('')}</nav>` : '';
    /* The way out to Stella and Anna, the level's own words, then where it leads, each across the whole width. A
       detail names where it leads, and a link above its words says how many rows wait there while they lie below. */
    const rows = steps(view, depth), detail = view.shape === 'detail' && rows;
    if (detail) actions.push(`<button type="button" class="tour-jump" hidden>${esc(fill(ui.onwardJump, {count:fmt(view.groups.reduce((n, g) => n + g.items.length, 0))}))}</button>`);
    const title = detail ? `<h3 class="tour-onward-title" id="tour-onward-${depth}" tabindex="-1">${esc(ui.onward)}</h3>` : '';
    return head(view, depth) + (actions.length ? `<div class="tour-actions">${actions.join('')}</div>` : '') +
      `<div class="tour-main">${status}${lead}</div>` + (rows ? `<div class="tour-onward">${title}${rows}</div>` : '') + sequence;
  }

  // A folded level keeps a spine that names it; the spine brings it back into view.
  function column(segment, depth) {
    const section = document.createElement('section');
    section.className = 'tour-column'; section.dataset.depth = depth; section.dataset.segment = segment;
    section.setAttribute('aria-labelledby', `tour-title-${depth}`);
    section.setAttribute('aria-busy', 'true');
    section.innerHTML = `<button type="button" class="tour-spine"><span>${esc(ui.loading)}</span>${icon}</button><div class="tour-sheet"><div class="tour-column-body"><h2 id="tour-title-${depth}">${esc(ui.loading)}</h2></div></div>`;
    return section;
  }
  function render(section, view, depth) {
    section.dataset.state = 'ready'; section.dataset.shape = view.shape; section.removeAttribute('aria-busy');
    const name = view.kind === 'root' ? ui.title : nameOf(view), spine = section.querySelector('.tour-spine');
    spine.querySelector('span').textContent = name; spine.title = name;
    spine.setAttribute('aria-label', fill(ui.unfold, {name}));
    section.querySelector('.tour-column-body').innerHTML = body(view, depth);
    watchOnward(section);
  }
  // The link to where a detail leads shows only while that part lies below what the column shows.
  function watchOnward(section) {
    const jump = section.querySelector('.tour-jump'), title = section.querySelector('.tour-onward-title');
    if (!jump || !title) return;
    new IntersectionObserver(([seen]) => {
      jump.hidden = seen.isIntersecting || seen.boundingClientRect.top < (seen.rootBounds?.top ?? 0);
    }, {root:section}).observe(title);
  }
  // Scrolls where a detail leads to the top of its column and puts the focus on its rows.
  function onward(section) {
    section.querySelector('.tour-onward-title').scrollIntoView({block:'start', behavior:reduced.matches ? 'auto' : 'smooth'});
    focusRows(section, false, false);
  }
  // A detail shows its own words as the book sets them while it is the deepest level; its lead stands in for them
  // while they load, when they cannot be laid out, and once a deeper level opens. Words that arrive after the level
  // is already on screen fade in over the lead.
  async function expand(section) {
    if (section.dataset.shape !== 'detail' || section.dataset.layout) return;
    section.dataset.layout = 'loading';
    let layout = null;
    try { layout = await load('/api/tour-layout', layouts, section.dataset.segment); } catch { layout = null; }
    if (layout?.status !== 'ok') { section.dataset.layout = 'none'; return; }
    const box = document.createElement('div'); box.className = 'tour-body layout-text'; box.lang = 'zh-CN';
    if (annaKit.pages) {
      const template = document.createElement('template'); template.innerHTML = layout.html;
      annaKit.rebaseLinks(template.content);
      box.innerHTML = template.innerHTML;
    } else box.innerHTML = layout.html;
    box.addEventListener('error', event => {
      const img = event.target;
      if (!(img instanceof HTMLImageElement) || !img.hasAttribute('data-o5e-fallback')) return;
      const fig = img.closest('figure.o5e-figure');
      if (!fig) return;
      fig.classList.add('o5e-img-failed', 'o5e-media-remote');
      const note = fig.querySelector('.o5e-media-fallback-note');
      if (note) note.hidden = false;
    }, true);
    const lead = section.querySelector('.tour-lead');
    lead ? lead.after(box) : section.querySelector('.tour-main').append(box);
    section.dataset.layout = 'ready';
    if (section.isConnected) annaMotion.arrive(box);
  }
  function failed(section, error, depth) {
    section.dataset.state = 'failed'; section.removeAttribute('aria-busy');
    section.querySelector('.tour-column-body').innerHTML = `<h2 id="tour-title-${depth}">${esc(ui.readError)}</h2><p class="tour-status">${esc(error.code)}<button type="button" class="tour-retry">${esc(ui.retry)}</button></p>`;
  }
  function keepCurrent(section) {
    const row = section.querySelector('.tour-item[aria-current=true]');
    if (!row) return;
    const top = row.getBoundingClientRect().top - section.getBoundingClientRect().top;
    if (top < 0 || top + row.offsetHeight > section.clientHeight) section.scrollTop = Math.max(0, section.scrollTop + top - section.clientHeight / 3);
  }
  // Places each level: the deepest is last, up to two more stay open beside it, and the rest fold.
  function arrange() {
    const sections = [...columns.children], open = openLevels();
    sections.forEach((section, i) => {
      const place = i === sections.length - 1 ? 'last' : i < sections.length - open ? 'folded' : 'open';
      const was = section.dataset.place;
      section.dataset.place = place;
      // Folding shows the level's spine and unfolding its sheet; either fades in as the column's edge moves.
      if (was && (was === 'folded') !== (place === 'folded')) annaMotion.arrive(section.querySelector(place === 'folded' ? '.tour-spine' : '.tour-sheet'));
      // A detail that narrows back to its lead keeps the row it opened in view.
      if (was === 'last' && place === 'open') keepCurrent(section);
      // One that widens again reads from the start of its own words.
      if (was && was !== 'last' && place === 'last' && section.dataset.shape === 'detail') section.scrollTop = 0;
    });
    const last = sections.at(-1);
    if (last?.dataset.state === 'ready' && last.dataset.shape === 'index' && !phone.matches) columns.dataset.rest = ui.rest;
    else delete columns.dataset.rest;
    if (last?.dataset.place === 'last') expand(last);
  }
  function say(message) { notice.textContent = message; notice.hidden = !message; }

  /* The columns move when a level opens, folds, unfolds, widens or narrows; the text in them does not. `change`
     settles the columns at once, then each column whose width changed starts from the width its place had before
     (a level replacing another at the same depth takes over its width, a new one starts from nothing) with its
     sheet held at the width it settles to, so the column's edge uncovers or covers lines already set. Resolves once
     the columns have settled. */
  function move(change) {
    const before = new Map([...columns.children].map(section => [section.dataset.depth, section.getBoundingClientRect().width]));
    for (const section of columns.children) release(section);
    change();
    if (reduced.matches || phone.matches || !before.size) return Promise.resolve();
    const moves = [...columns.children].map(section => ({section, from:before.get(section.dataset.depth) ?? 0,
      to:section.getBoundingClientRect().width, sheet:section.clientWidth})).filter(({from, to}) => Math.abs(to - from) >= 1);
    return Promise.all(moves.map(({section, from, to, sheet}) => {
      section.style.setProperty('--tour-settled', sheet + 'px');
      section.dataset.moving = '';
      const motion = section.animate({flex:[`0 0 ${from}px`, `0 0 ${to}px`]}, {duration:MOVE_TIME, easing:ease});
      motions.set(section, motion);
      return motion.finished.then(() => { if (motions.get(section) === motion) release(section); }, () => {});
    }));
  }
  // A column that stops moving, done or interrupted, lets its sheet follow its width again.
  function release(section) {
    if (!motions.has(section)) return;
    motions.get(section).cancel(); motions.delete(section);
    delete section.dataset.moving; section.style.removeProperty('--tour-settled');
    section.scrollLeft = 0;
  }

  const visibleItems = section => [...section.querySelectorAll('.tour-item')].filter(link => !link.closest('li')?.hidden && !link.closest('.tour-group')?.hidden);
  // The row each level opened is marked in the level above; each level keeps one row in the Tab order.
  function markCurrent() {
    const wanted = ['', ...path];
    for (const section of columns.children) {
      const depth = Number(section.dataset.depth), next = wanted[depth + 1];
      let current = null;
      for (const link of section.querySelectorAll('.tour-item[data-segment]')) {
        const here = next !== undefined && link.dataset.segment === next && !current;
        if (here) { link.setAttribute('aria-current', 'true'); current = link; }
        else link.removeAttribute('aria-current');
      }
      for (const link of section.querySelectorAll('.tour-versions a[data-segment]')) {
        if (next !== undefined && link.dataset.segment === next) link.setAttribute('aria-current', 'true');
        else link.removeAttribute('aria-current');
      }
      const items = visibleItems(section);
      const visibleCurrent = current && !current.closest('li')?.hidden && !current.closest('.tour-group')?.hidden ? current : null;
      const keep = items.find(link => link.tabIndex === 0) || visibleCurrent || items[0];
      section.querySelectorAll('.tour-item').forEach(link => { link.tabIndex = link === (visibleCurrent || keep) ? 0 : -1; });
    }
  }
  function reveal() {
    const last = columns.lastElementChild;
    if (!last) return;
    const right = last.offsetLeft + last.offsetWidth;
    if (right > columns.scrollLeft + columns.clientWidth || last.offsetLeft < columns.scrollLeft)
      columns.scrollTo({left:Math.max(0, right - columns.clientWidth), behavior:reduced.matches ? 'auto' : 'smooth'});
  }
  /* Focus has two places in a level: its rows, where the reader chooses, and, in the deepest level when it is a
     detail, the column itself, where the reader reads and the arrow keys scroll the text. As in a file manager's
     columns, the row holding the focus is the one open beside its level: moving onto a row opens it. Entering a
     level from the left lands where it starts: a detail's text, otherwise the row it opened before or its first
     row, which opens in turn. */
  const deepest = () => columns.lastElementChild;
  const detailReady = section => section.dataset.shape === 'detail' && section.dataset.state === 'ready';
  const readable = section => section === deepest() && detailReady(section);
  const opens = link => link.hasAttribute('data-at') && link.getAttribute('aria-disabled') !== 'true';
  function focusRows(section, open = false, scroll = true) {
    if (!section) return;
    if (section.dataset.place === 'folded') { section.querySelector('.tour-spine').focus(); return; }
    const items = visibleItems(section);
    const target = items.find(link => link.getAttribute('aria-current') === 'true') ||
      items.find(link => link.tabIndex === 0) || items[0] || section.querySelector('h2');
    if (!target) return;
    if (target.matches('h2')) { target.tabIndex = -1; }
    else section.querySelectorAll('.tour-item').forEach(link => { link.tabIndex = link === target ? 0 : -1; });
    target.focus({preventScroll:true});
    if (scroll) target.scrollIntoView({block:'nearest'});
    if (open && target.matches('.tour-item')) follow(target);
  }
  function focusReading(section) {
    section.tabIndex = -1;
    section.focus({preventScroll:true});
  }
  function enter(section, open = false) {
    if (section && readable(section)) focusReading(section);
    else focusRows(section, open);
  }
  // A detail's text lies above its first row; reading it again closes the levels beside it, so it widens.
  function read(section) {
    const depth = Number(section.dataset.depth);
    if (section === deepest()) focusReading(section);
    else go(path.slice(0, depth), {replace:true, focus:{depth, enter:true}});
  }
  // Places the focus once the levels being shown are in place: at once when none are on their way.
  function aim(focus) {
    landing = focus;
    if (shown === generation) land();
  }
  function land() {
    const focus = landing;
    landing = null;
    if (focus === 'kept') keepFocus();
    else if (focus) focus.enter ? enter(columns.children[focus.depth], focus.open) : focusRows(columns.children[focus.depth]);
  }
  // Going back or forward through history keeps the focus in the tour: on the open row of a level that stays,
  // otherwise where the deepest level starts.
  function keepFocus() {
    const active = document.activeElement;
    if (!columns.contains(active)) { enter(deepest()); return; }
    const row = active.closest('.tour-item'), section = row?.closest('.tour-column');
    if (section?.querySelector('.tour-item[aria-current=true]') && row.getAttribute('aria-current') !== 'true') focusRows(section);
  }

  function applyGroupFilter(input) {
    const box = input.closest('.tour-filter');
    const columnScope = box?.dataset.filterScope === 'column';
    const container = columnScope ? input.closest('.tour-onward') : input.closest('.tour-group');
    if (!container) return;
    const term = input.value.trim().toLocaleLowerCase();
    const groups = columnScope ? [...container.querySelectorAll('.tour-group')] : [container];
    let visible = 0, total = 0;
    for (const group of groups) {
      const list = group.querySelector('ul,ol');
      if (!list) continue;
      let groupVisible = 0;
      for (const li of list.children) {
        total++;
        const hay = (li.textContent + ' ' + (li.dataset.search || '')).toLocaleLowerCase();
        const match = !term || hay.includes(term);
        li.hidden = !match;
        if (match) { visible++; groupVisible++; }
      }
      if (columnScope) group.hidden = groupVisible === 0;
    }
    const counter = box.querySelector('.tour-filter-count');
    if (counter) counter.textContent = term ? fill(ui.filterCount, {shown:fmt(visible), total:fmt(total)}) : fmt(total);
    const empty = container.querySelector(':scope > .tour-filter-empty');
    if (empty) empty.hidden = visible > 0;
    markCurrent();
  }

  async function show(segments) {
    const run = ++generation, wanted = ['', ...segments];
    // Where one level shows at a time, a deeper level comes in from the right and a level above from the left.
    const from = segments.length > path.length ? 'right' : segments.length < path.length ? 'left' : null;
    path = segments;
    say('');
    const existing = [...columns.children];
    let keep = 0;
    while (keep < existing.length && keep < wanted.length && existing[keep].dataset.segment === wanted[keep] &&
           existing[keep].dataset.state === 'ready') keep++;
    /* The levels being replaced stay until the new ones can take their place, and the row that opens them shows at
       once. New levels that load within LOADING_DELAY open in one step, the deepest with a detail's own words; past
       it they open showing that they are loading, and take their final form in a second step. */
    const leaving = existing.slice(keep);
    let fresh = wanted.slice(keep).map((segment, i) => column(segment, keep + i)), opening = null;
    const placed = motion => motion.then(() => { if (run === generation) reveal(); });
    const open = () => opening ||= placed(move(() => {
      leaving.forEach(section => section.remove()); columns.append(...fresh); markCurrent(); arrange();
      if (phone.matches) annaMotion.arrive(columns.lastElementChild, from, {distance:24});
    }));
    markCurrent();
    const waited = new Promise(resolve => setTimeout(resolve, LOADING_DELAY));
    waited.then(() => { if (run === generation) open(); });
    const loaded = await Promise.allSettled(wanted.slice(keep).map(fetchView));
    if (run !== generation) return;
    const failure = loaded.findIndex(result => result.status === 'rejected'), error = loaded[failure]?.reason;
    if (error) {
      const depth = keep + failure;
      if (error.status === 409) say(ui.bundleChanged);
      else if (depth === 0) say(annaKit.pages ? ui.pagesUnavailable : ui.unavailable);
      // A level that no longer exists in this snapshot returns the reader to the level above it.
      if (error.status === 404 && depth > 0) {
        const kept = segments.slice(0, depth - 1);
        history.replaceState({tour:joinSegments(kept)}, '', tourHref(kept));
        await show(kept);
        if (run + 1 === generation) say(ui.nodeMissing);
        return;
      }
      // Levels below one that failed do not open.
      fresh.slice(failure + 1).forEach(rest => rest.remove());
      fresh = fresh.slice(0, failure + 1);
    }
    const draw = () => fresh.forEach((section, i) => i === failure ? failed(section, error, keep + i) : render(section, loaded[i].value, keep + i));
    /* A level that takes the place of its own loading state, or of another level at its depth, fades in where it
       stands; a level in a new column is uncovered by the column's moving edge instead. */
    const shownLoading = !!opening, replaced = new Set(leaving.map(section => section.dataset.depth));
    let moved;
    if (opening) moved = placed(move(() => { draw(); markCurrent(); arrange(); }));
    else {
      draw();
      if (fresh.length) await Promise.race([expand(fresh.at(-1)), waited]);
      if (run !== generation) return;
      moved = open();
    }
    fresh.forEach(section => { if (section.dataset.place === 'open') keepCurrent(section); });
    for (const section of fresh) if (shownLoading || replaced.has(section.dataset.depth)) annaMotion.arrive(section.querySelector('.tour-sheet'));
    settle();
    shown = run;
    land();
    return moved;
  }
  // After a level opens: the title names it, the place is remembered, and an entry shown here is the topic.
  function settle() {
    const last = views.get(['', ...path].at(-1));
    document.title = annaPageTitle('tour', last && last.kind !== 'root' ? nameOf(last) : ui.title);
    stored.set(MEMORY, path.length ? joinSegments(path) : null);
    if (!snapshot) return;
    if (!mounted && typeof annaCompanions !== 'undefined') { annaCompanions.mount({page:'tour', snapshots}); mounted = true; }
    const latest = annaWorkspace.read(snapshots).query[0];
    annaTopbar.pointSwitch('query', latest ? '/?' + latest.query : '/');
    // An entry shown here is the topic Stella can turn to when it has a place in its book.
    const shownEntry = last?.kind === 'entry' && last.type !== 'section' && last.read && collectionOf(last.id) ? last : null;
    if (topic && topic !== shownEntry?.id) annaWorkspace.update(snapshots, state => annaWorkspace.clearTopic(state, topic));
    if (shownEntry && topic !== shownEntry.id) {
      const nextTopic = {entry:shownEntry.id, name:shownEntry.title, from:'query', snapshot:snapshots[collectionOf(shownEntry.id)], at:Date.now()};
      if (shownEntry.read?.page) nextTopic.bookLocation = shownEntry.read;
      annaWorkspace.update(snapshots, state => annaWorkspace.setTopic(state, nextTopic));
    }
    topic = shownEntry?.id || null;
  }
  // Opening a level is a step in history; `replace` changes the current step instead. `focus` is where the focus
  // lands once the levels are in place: `depth` names the level, `enter` enters it rather than its rows, and `open`
  // opens the row it lands on.
  function go(segments, options = {}) {
    if (joinSegments(segments) === joinSegments(path)) {
      if (options.focus) aim(options.focus);
      return;
    }
    landing = options.focus ?? null;
    history[options.replace ? 'replaceState' : 'pushState']({tour:joinSegments(segments)}, '', tourHref(segments));
    return show(segments);
  }

  columns.addEventListener('input', event => {
    const input = event.target.closest('.tour-filter-input');
    if (input) applyGroupFilter(input);
  });

  columns.addEventListener('click', event => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const retry = event.target.closest('.tour-retry');
    if (retry) { show(path); return; }
    const spine = event.target.closest('.tour-spine');
    if (spine) { unfold(spine.closest('.tour-column')); return; }
    const jump = event.target.closest('.tour-jump');
    if (jump) { onward(jump.closest('.tour-column')); return; }
    // A reference inside a detail's own words opens its definition as the next level.
    const ref = event.target.closest('button.ref[data-entry]');
    if (ref) {
      const column = ref.closest('.tour-column'), depth = Number(column.dataset.depth);
      // The words are one collection's; the entry they refer to is that collection's own.
      const owner = views.get(column.dataset.segment)?.collection;
      if (owner) go([...path.slice(0, depth), owner + ':' + ref.dataset.entry], {focus:{depth:depth + 1, enter:true}});
      return;
    }
    const link = event.target.closest('a[data-at]');
    if (!link) return;
    event.preventDefault();
    if (link.getAttribute('aria-disabled') === 'true') return;
    go(segmentsOf(new URL(link.href).search));
  });
  // Unfolding a level closes just enough levels below it to bring it back among the open ones.
  function unfold(section) {
    const depth = Number(section.dataset.depth);
    go(path.slice(0, depth + openLevels() - 1), {focus:{depth}});
  }
  // ← climbs to the row that opened this level. A folded level above unfolds to take the focus; where only one
  // level shows, every level above is folded, so climbing goes back to it.
  function climb(section) {
    const above = section.previousElementSibling;
    if (!above) return;
    if (above.dataset.place === 'folded') unfold(above);
    else focusRows(above);
  }
  /* Moving onto a row opens it: its level beside, or, for a row that leads elsewhere, no level beside. A level opened
     past the deepest one is a step in history; changing the levels already open replaces the step. A held key
     opens only the row it rests on. Where one level shows at a time, moving opens nothing. */
  function follow(link, held = false) {
    stopFollowing();
    if (openLevels() === 1) return;
    const open = () => {
      openOnRest = null;
      if (document.activeElement !== link || !link.isConnected || link.getAttribute('aria-current') === 'true') return;
      const depth = Number(link.closest('.tour-column').dataset.depth);
      const segments = opens(link) ? segmentsOf(new URL(link.href).search) : path.slice(0, depth);
      go(segments, {replace:segments.length <= path.length});
    };
    if (!held) { open(); return; }
    openOnRest = open;
    restTimer = setTimeout(open, FOLLOW_DELAY);
  }
  function stopFollowing() {
    clearTimeout(restTimer);
    openOnRest = null;
  }
  // Scrolls a level's text; presses that come before the last one has finished scrolling add to where it was going.
  function scrollText(section, distance) {
    const from = reading.get(section) ?? section.scrollTop;
    const top = Math.max(0, Math.min(section.scrollHeight - section.clientHeight, from + distance));
    if (top === section.scrollTop) return;
    reading.set(section, top);
    section.addEventListener('scrollend', () => reading.delete(section), {once:true});
    section.scrollTo({top, behavior:reduced.matches ? 'auto' : 'smooth'});
  }
  // From a level to the left, Space and Page Up/Down read on in the deepest level without leaving the rows.
  function readOn(event, section) {
    const last = deepest();
    if (section === last || !last || ![' ', 'PageDown', 'PageUp'].includes(event.key)) return false;
    const down = event.key === 'PageDown' || (event.key === ' ' && !event.shiftKey);
    scrollText(last, (down ? 1 : -1) * last.clientHeight * 0.85);
    return true;
  }
  columns.addEventListener('keydown', event => {
    stopFollowing();
    if (event.altKey || event.ctrlKey || event.metaKey || event.isComposing) return;
    const section = event.target.closest('.tour-column');
    if (!section) return;
    const filterInput = event.target.closest('.tour-filter-input');
    if (filterInput) {
      if (event.key === 'Escape' && filterInput.value) {
        event.preventDefault();
        filterInput.value = '';
        applyGroupFilter(filterInput);
      } else if (event.key === 'ArrowDown') {
        const scope = filterInput.closest('.tour-group') || section;
        const first = visibleItems(scope)[0];
        if (first) {
          event.preventDefault();
          section.querySelectorAll('.tour-item').forEach(each => { each.tabIndex = each === first ? 0 : -1; });
          first.focus();
          follow(first);
        }
      }
      return;
    }
    const depth = Number(section.dataset.depth), key = VIM_KEYS[event.key] || event.key;
    const spine = event.target.closest('.tour-spine');
    if (spine) {
      if (event.shiftKey) return;
      const next = section.nextElementSibling;
      if (key === 'ArrowLeft') { event.preventDefault(); climb(section); }
      else if (key === 'ArrowRight' && next) { event.preventDefault(); next.dataset.place === 'folded' ? unfold(next) : enter(next, true); }
      return;
    }
    // Escape closes the levels to the right of this one; the focus stays where it is.
    if (event.key === 'Escape' && section !== deepest() && !event.shiftKey) { event.preventDefault(); go(path.slice(0, depth)); return; }
    // Reading a detail: the arrow keys scroll its text natively, and j k by the same step; → moves on to its rows,
    // ← climbs.
    if (event.target === section) {
      if (event.shiftKey) return;
      if (key === 'ArrowLeft') { event.preventDefault(); climb(section); }
      else if (key === 'ArrowRight' && visibleItems(section).length) { event.preventDefault(); focusRows(section, true); }
      else if (key !== event.key && (key === 'ArrowDown' || key === 'ArrowUp')) {
        event.preventDefault();
        scrollText(section, (key === 'ArrowDown' ? 1 : -1) * LINE_STEP);
      }
      return;
    }
    const link = event.target.closest('.tour-item');
    if (!link) return;
    if (readOn(event, section)) { event.preventDefault(); return; }
    if (event.shiftKey) return;
    const items = visibleItems(section), i = items.indexOf(link);
    if (i < 0) return;
    let target = null;
    if (key === 'ArrowDown') target = items[Math.min(i + 1, items.length - 1)];
    else if (key === 'ArrowUp') {
      if (i === 0 && detailReady(section)) { event.preventDefault(); read(section); return; }
      target = items[Math.max(i - 1, 0)];
    }
    else if (key === 'Home') target = items[0];
    else if (key === 'End') target = items.at(-1);
    else if (key === 'ArrowLeft') { event.preventDefault(); climb(section); return; }
    // → and Enter enter the level the row opened, opening it first when it is not open yet.
    else if (key === 'ArrowRight' || key === 'Enter') {
      if (!opens(link)) return;
      event.preventDefault();
      const focus = {depth:depth + 1, enter:true, open:true};
      if (link.getAttribute('aria-current') === 'true') aim(focus);
      else go(segmentsOf(new URL(link.href).search), {focus});
      return;
    } else return;
    event.preventDefault();
    section.querySelectorAll('.tour-item').forEach(each => { each.tabIndex = each === target ? 0 : -1; });
    target.focus();
    follow(target, event.repeat);
  });
  columns.addEventListener('keyup', () => openOnRest?.());
  columns.addEventListener('pointerdown', stopFollowing);
  window.addEventListener('popstate', () => {
    stopFollowing();
    landing = columns.contains(document.activeElement) ? 'kept' : null;
    show(segmentsOf(location.search));
  });
  for (const query of [roomy, phone]) query.addEventListener('change', () => move(arrange).then(reveal));

  // The library decides what the tour answers; a changed library reads every level afresh.
  let libraryTimer;
  annaLibrary.subscribe(() => {
    clearTimeout(libraryTimer);
    libraryTimer = setTimeout(() => { views.clear(); layouts.clear(); snapshot = null; columns.replaceChildren(); show(path); }, 250);
  });
  const start = segmentsOf(location.search);
  history.replaceState({tour:joinSegments(start)}, '', tourHref(start));
  show(start).finally(annaMotion.settle);
  return {show, go};
})();
