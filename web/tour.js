'use strict';
/* Anna's tour: where to start and what to read next. Each column is one level of the way down, from a purpose
   to the book's chapters, steps and entries; choosing a row opens the next level beside it. At most three levels
   stay open and the ones above them fold into spines on the left. Only the deepest level widens, and only when it
   reads as a detail: then its own words show as the book sets them, and once a row below it opens it narrows back
   to its quoted lead. Rows lead on to the next level, to Stella's reading or to Anna's table. A level opens once it
   is ready, in its final form, and the columns change width without rewrapping the text in them. */
const annaTourLabels = {
  zh:{title:'导览', skip:'跳到导览', intro:'从想做的事出发，逐层走进原书的章节、步骤与条目。',
    groups:{choices:'这一步要选择', contents:'目录', outline:'本页', parts:'包含', subclasses:'子职', class:'所属职业',
      feats:'授予专长', spells:'法术', entries:'条目', shelf:'书架'},
    facet:'按{field}', readBook:'在 Stella 中阅读', openEntry:'在 Anna 中查看', openQuery:'在 Anna 中查看 {count} 条',
    spellList:'可用法术', more:'另有 {count} 项', moreInAnna:'另有 {count} 项，在 Anna 中查看全部',
    previous:'上一项：{name}', next:'下一项：{name}', back:'‹ {name}', table:'表', versions:'其他版本',
    rest:'选择一项，它会在这里展开', unfold:'展开「{name}」',
    loading:'正在读取…', retry:'重试', readError:'无法读取这一层。', nodeMissing:'这一层已不在当前原文快照中，已回到上一层。',
    bundleChanged:'数据已经重建，请刷新页面。', unavailable:'导览需要完整服务：请使用 make up 启动后打开。',
    status:{missing:'未找到对应原文', ambiguous:'原文对应不唯一', placeholder:'分组'},
    rulesets:{'5e':['2014','2014 版规则（5e）'],'5r':['2024','2024 版规则（5r）'],'待核':['待核','规则版本待核']}},
  en:{title:'Tour', skip:'Skip to the tour', intro:'Start from what you want to do, then go down through the book\'s chapters, steps and entries.',
    groups:{choices:'Choose in this step', contents:'Contents', outline:'On this page', parts:'Contains', subclasses:'Subclasses', class:'Class',
      feats:'Granted feats', spells:'Spells', entries:'Entries', shelf:'Bookshelf'},
    facet:'By {field}', readBook:'Read in Stella', openEntry:'Open in Anna', openQuery:'Open {count} in Anna',
    spellList:'Spell list', more:'{count} more', moreInAnna:'{count} more; open them all in Anna',
    previous:'Previous: {name}', next:'Next: {name}', back:'‹ {name}', table:'Table', versions:'Other versions',
    rest:'Choose an item to open it here', unfold:'Show {name}',
    loading:'Loading…', retry:'Try again', readError:'Unable to load this level.', nodeMissing:'That level is not in the current source snapshot; showing the level above.',
    bundleChanged:'The data was rebuilt. Refresh this page.', unavailable:'The tour needs the full service: start it with make up.',
    status:{missing:'Source not found', ambiguous:'Source match not unique', placeholder:'Group'},
    rulesets:{'5e':['2014','2014 rules (5e)'],'5r':['2024','2024 rules (5r)'],'待核':['Unverified','Rules version unverified']}}
};
const annaTour = (() => {
  const lang = document.documentElement.lang.startsWith('en') ? 'en' : 'zh';
  const ui = annaTourLabels[lang], {esc, stored} = annaKit;
  const MEMORY = 'anna.tour', SEGMENTS = /^[\w-]+(\.[\w-]+)*$/;
  const columns = document.querySelector('#tour-columns'), notice = document.querySelector('#tour-notice');
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  // How many levels stay open side by side; the rest fold. Phones read one level at a time.
  const roomy = window.matchMedia('(min-width: 1280px)'), phone = window.matchMedia('(max-width: 760px)');
  const openLevels = () => roomy.matches ? 3 : phone.matches ? 1 : 2;
  // A held key passes over rows; the row it rests on opens once the key is released or after this long.
  const FOLLOW_DELAY = 150;
  // Levels that answer within this time open in one step; slower ones first show that they are loading.
  const LOADING_DELAY = 180;
  // How long the columns take to reach their new widths, on the shared easing curve.
  const MOVE_TIME = 200, ease = getComputedStyle(document.documentElement).getPropertyValue('--anna-ease').trim() || 'ease-out';
  const views = new Map(), layouts = new Map(), reading = new WeakMap(), motions = new WeakMap();
  /* `shown` is the generation whose levels are in place; `landing` is where the focus goes once the levels on their
     way are. `openOnRest` opens the row a held key rests on, when the key is released or `restTimer` runs out. */
  let snapshot = null, path = [], generation = 0, shown = 0, landing = null, mounted = false, topic = null;
  let restTimer = 0, openOnRest = null;
  for (const el of document.querySelectorAll('[data-ui]')) el.textContent = ui[el.dataset.ui];

  const fill = (template, values) => Object.entries(values).reduce((s, [k, v]) => s.replaceAll('{' + k + '}', v), template);
  const fmt = n => Number(n).toLocaleString(lang === 'en' ? 'en' : 'zh-CN');
  const nameOf = item => item.label ? item.label[lang] || item.label.zh : item.title || '';
  const rulesetTag = r => r && ui.rulesets[r] ? `<span class="tour-ruleset${r === '待核' ? ' unverified' : ''}" title="${esc(ui.rulesets[r][1])}">${esc(ui.rulesets[r][0])}</span>` : '';
  const segmentsOf = search => {
    const at = new URLSearchParams(search).get('at') || '';
    return SEGMENTS.test(at) ? at.split('.') : [];
  };
  const tourHref = segments => '/tour' + (segments.length ? '?at=' + segments.join('.') : '');
  const queryHref = query => '/?' + new URLSearchParams(Object.entries(query).flatMap(([key, values]) => values.map(v => [key, v])));
  const readHref = read => '/read?' + new URLSearchParams(read);
  const entryHref = anna => '/?' + new URLSearchParams(anna.type ? {type:anna.type, entry:anna.entry} : {entry:anna.entry});
  const icon = '<svg class="tour-item-go" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>';

  class LoadError extends Error {
    constructor(code, status) { super(code); this.code = code; this.status = status; }
  }
  async function load(api, cache, segment) {
    if (cache.has(segment)) return cache.get(segment);
    const params = new URLSearchParams({node:segment});
    if (snapshot) params.set('snapshot', snapshot);
    let response, body = null;
    try { response = await fetch(api + '?' + params); } catch { throw new LoadError('network', 0); }
    try { body = await response.json(); } catch { body = null; }
    if (!response.ok || !body) throw new LoadError(body?.code || 'error', response.status);
    snapshot ||= body.snapshot;
    cache.set(segment, body);
    return body;
  }
  const fetchView = segment => load('/api/tour', views, segment);

  // One row. Rows that open a level carry the path they lead to; rows that leave for Anna's table do not.
  function item(row, group, index, depth, view) {
    const title = row.kind === 'query' && !row.title ? ui.spellList : nameOf(row);
    const tags = [];
    if (['section', 'entries', 'subclasses', 'feats', 'class', 'choices'].includes(group.key) && row.ruleset !== view.ruleset) tags.push(rulesetTag(row.ruleset));
    if (row.table) tags.push(`<span class="tour-tag">${esc(ui.table)}</span>`);
    const status = ui.status[row.status] && row.status !== 'placeholder' ? ui.status[row.status] : '';
    const hint = status || row.hint || '';
    const main = `<span class="tour-item-main"><span class="tour-item-title">${esc(title)}</span>${hint ? `<span class="tour-item-hint">${esc(hint)}</span>` : ''}</span>` +
      (tags.length ? `<span class="tour-item-tags">${tags.join('')}</span>` : '') +
      (row.count ? `<span class="tour-item-count">${fmt(row.count)}</span>` : '');
    if (row.kind === 'query')
      return `<li><a class="tour-item" href="${esc(queryHref(row.query))}">${main}<img class="tour-item-out" src="/anna-logo.png" alt="" width="18" height="18"></a></li>`;
    const at = [...path.slice(0, depth), row.id];
    const disabled = row.kind === 'topic' && row.status !== 'ok';
    const step = group.ordered ? `<span class="tour-step">${index + 1}</span>` : '';
    const versions = row.versions?.length ? `<div class="tour-versions"><span>${esc(ui.versions)}</span>${row.versions.map(v =>
      `<a href="${esc(tourHref([...path.slice(0, depth), v.id]))}" data-at data-segment="${esc(v.id)}" title="${esc(ui.rulesets[v.ruleset]?.[1] || v.ruleset || '')}">${esc(ui.rulesets[v.ruleset]?.[0] || v.ruleset || '?')}</a>`).join('')}</div>` : '';
    return `<li><a class="tour-item" href="${esc(tourHref(at))}" data-at data-segment="${esc(row.id)}" tabindex="-1"${disabled ? ' aria-disabled="true"' : ''}>${step}${main}${icon}</a>${versions}</li>`;
  }
  function groupHeading(group, index, groups) {
    if (group.key === 'section') return nameOf(group);
    if (group.key === 'facet') return fill(ui.facet, {field:group.label || ''});
    if (group.key === 'contents') return group.label || (groups.findIndex(g => g.key === 'contents') === index ? ui.groups.contents : '');
    return ui.groups[group.key] || '';
  }
  // Steps number on across the groups a bare divider splits them into, so the path reads as one sequence.
  function groupHtml(group, index, view, depth, first = 0) {
    const heading = groupHeading(group, index, view.groups);
    const more = group.more ? `<p class="tour-more">${view.anna?.query ?
      `<a href="${esc(queryHref(view.anna.query))}">${esc(fill(ui.moreInAnna, {count:fmt(group.more)}))}</a>` : esc(fill(ui.more, {count:fmt(group.more)}))}</p>` : '';
    const label = heading ? ` aria-label="${esc(heading)}"` : '';
    return `<section class="tour-group${group.ordered ? ' ordered' : ''}" data-group="${esc(group.key)}"${label}>${heading ? `<h3>${esc(heading)}</h3>` : ''}` +
      `<${group.ordered ? 'ol' : 'ul'} role="list"${group.ordered && first ? ` start="${first + 1}"` : ''}>${group.items.map((row, i) => item(row, group, first + i, depth, view)).join('')}</${group.ordered ? 'ol' : 'ul'}>${more}</section>`;
  }
  function head(view, depth) {
    const title = view.kind === 'root' ? ui.title : nameOf(view);
    const parent = depth > 0 ? views.get(['', ...path][depth - 1]) : null;
    const back = parent ? `<a class="tour-back" href="${esc(tourHref(path.slice(0, depth - 1)))}" data-at>${esc(fill(ui.back, {name:parent.kind === 'root' ? ui.title : nameOf(parent)}))}</a>` : '';
    const trail = view.trail?.length ? `<p class="tour-trail" title="${esc(view.trail.join(' › '))}">${esc(view.trail.join(' › '))}</p>` : '';
    const meta = [rulesetTag(view.ruleset), view.type_label && view.type !== 'section' ? esc(view.type_label) : '', esc((view.sources || []).join('；'))].filter(Boolean);
    return `<header>${back}${trail}<h2 id="tour-title-${depth}">${esc(title)}</h2>${meta.length ? `<p class="tour-meta">${meta.join('<span aria-hidden="true">·</span>')}</p>` : ''}` +
      (view.kind === 'root' ? `<p class="tour-intro">${esc(ui.intro)}</p>` : '') + '</header>';
  }
  function steps(view, depth) {
    let first = 0;
    return view.groups.map((group, i) => {
      const html = groupHtml(group, i, view, depth, first);
      if (group.ordered) first += group.items.length;
      return html;
    }).join('');
  }
  function body(view, depth) {
    const lead = view.lead ? `<blockquote class="tour-lead" lang="zh-CN">${esc(view.lead.text)}${view.lead.truncated && !view.lead.text.endsWith('…') ? '<span class="tour-ellipsis"> …</span>' : ''}</blockquote>` : '';
    const actions = [];
    if (view.read) actions.push(`<a class="tour-go" href="${esc(readHref(view.read))}"><img src="/stella-logo.png" alt="" width="24" height="24">${esc(ui.readBook)}</a>`);
    if (view.anna?.query) actions.push(`<a class="tour-go" href="${esc(queryHref(view.anna.query))}"><img src="/anna-logo.png" alt="" width="24" height="24">${esc(fill(ui.openQuery, {count:fmt(view.anna.count)}))}</a>`);
    else if (view.anna?.entry) actions.push(`<a class="tour-go" href="${esc(entryHref(view.anna))}"><img src="/anna-logo.png" alt="" width="24" height="24">${esc(ui.openEntry)}</a>`);
    const status = view.status && !['ok', 'present', 'placeholder'].includes(view.status) ? `<p class="tour-status">${esc(ui.status[view.status] || view.status)}</p>` : '';
    const sequence = view.sequence && depth > 0 ? `<nav class="tour-sequence" aria-label="${esc(nameOf(view))}">${[['previous', 'prev'], ['next', 'next']].map(([key, rel]) => {
      const sibling = view.sequence[key];
      return sibling ? `<a rel="${rel}" href="${esc(tourHref([...path.slice(0, depth - 1), sibling.id]))}" data-at title="${esc(sibling.title)}">${esc(fill(ui[key], {name:sibling.title}))}</a>` : '';
    }).join('')}</nav>` : '';
    // The level's own words, then where it leads; a wide detail sets the second beside the first.
    const rail = (actions.length ? `<div class="tour-actions">${actions.join('')}</div>` : '') + steps(view, depth) + sequence;
    return head(view, depth) + `<div class="tour-main">${status}${lead}</div>` + (rail ? `<div class="tour-rail">${rail}</div>` : '');
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
    const box = document.createElement('div'); box.className = 'tour-body layout-text'; box.lang = 'zh-CN'; box.innerHTML = layout.html;
    const lead = section.querySelector('.tour-lead');
    lead ? lead.after(box) : section.querySelector('.tour-main').append(box);
    section.dataset.layout = 'ready';
    if (section.isConnected && !reduced.matches) box.animate({opacity:[0, 1]}, {duration:MOVE_TIME, easing:ease});
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
      const items = [...section.querySelectorAll('.tour-item')];
      const keep = items.find(link => link.tabIndex === 0) || current || items[0];
      items.forEach(link => { link.tabIndex = link === (current || keep) ? 0 : -1; });
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
  function focusRows(section, open = false) {
    if (!section) return;
    if (section.dataset.place === 'folded') { section.querySelector('.tour-spine').focus(); return; }
    const target = section.querySelector('.tour-item[aria-current=true]') || section.querySelector('.tour-item[tabindex="0"]') ||
      section.querySelector('.tour-item') || section.querySelector('h2');
    if (!target) return;
    if (target.matches('h2')) { target.tabIndex = -1; }
    else section.querySelectorAll('.tour-item').forEach(link => { link.tabIndex = link === target ? 0 : -1; });
    target.focus({preventScroll:true});
    target.scrollIntoView({block:'nearest'});
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

  async function show(segments) {
    const run = ++generation, wanted = ['', ...segments];
    path = segments;
    say('');
    const existing = [...columns.children];
    let keep = 0;
    while (keep < existing.length && keep < wanted.length && existing[keep].dataset.segment === wanted[keep] && existing[keep].dataset.state === 'ready') keep++;
    /* The levels being replaced stay until the new ones can take their place, and the row that opens them shows at
       once. New levels that load within LOADING_DELAY open in one step, the deepest with a detail's own words; past
       it they open showing that they are loading, and take their final form in a second step. */
    const leaving = existing.slice(keep);
    let fresh = wanted.slice(keep).map((segment, i) => column(segment, keep + i)), opening = null;
    const placed = motion => motion.then(() => { if (run === generation) reveal(); });
    const open = () => opening ||= placed(move(() => { leaving.forEach(section => section.remove()); columns.append(...fresh); markCurrent(); arrange(); }));
    markCurrent();
    const waited = new Promise(resolve => setTimeout(resolve, LOADING_DELAY));
    waited.then(() => { if (run === generation) open(); });
    const loaded = await Promise.allSettled(wanted.slice(keep).map(fetchView));
    if (run !== generation) return;
    const failure = loaded.findIndex(result => result.status === 'rejected'), error = loaded[failure]?.reason;
    if (error) {
      const depth = keep + failure;
      if (error.status === 409) say(ui.bundleChanged);
      else if (depth === 0) say(ui.unavailable);
      // A level that no longer exists in this snapshot returns the reader to the level above it.
      if (error.status === 404 && depth > 0) {
        const kept = segments.slice(0, depth - 1);
        history.replaceState({tour:kept.join('.')}, '', tourHref(kept));
        await show(kept);
        if (run + 1 === generation) say(ui.nodeMissing);
        return;
      }
      // Levels below one that failed do not open.
      fresh.slice(failure + 1).forEach(rest => rest.remove());
      fresh = fresh.slice(0, failure + 1);
    }
    const draw = () => fresh.forEach((section, i) => i === failure ? failed(section, error, keep + i) : render(section, loaded[i].value, keep + i));
    let moved;
    if (opening) moved = placed(move(() => { draw(); markCurrent(); arrange(); }));
    else {
      draw();
      if (fresh.length) await Promise.race([expand(fresh.at(-1)), waited]);
      if (run !== generation) return;
      moved = open();
    }
    fresh.forEach(section => { if (section.dataset.place === 'open') keepCurrent(section); });
    settle();
    shown = run;
    land();
    return moved;
  }
  // After a level opens: the title names it, the place is remembered, and an entry shown here is the topic.
  function settle() {
    const last = views.get(['', ...path].at(-1));
    document.title = annaPageTitle('tour', last && last.kind !== 'root' ? nameOf(last) : ui.title);
    stored.set(MEMORY, path.length ? path.join('.') : null);
    if (!snapshot) return;
    if (!mounted && typeof annaCompanions !== 'undefined') { annaCompanions.mount({page:'tour', snapshot}); mounted = true; }
    const latest = annaWorkspace.read(snapshot).query[0];
    annaTopbar.pointSwitch('query', latest ? '/?' + latest.query : '/');
    const shown = last?.kind === 'entry' && last.type !== 'section' ? last : null;
    if (topic && topic !== shown?.id) annaWorkspace.update(snapshot, state => annaWorkspace.clearTopic(state, topic));
    if (shown && topic !== shown.id) annaWorkspace.update(snapshot, state => annaWorkspace.setTopic(state, {entry:shown.id, name:shown.title, from:'query', at:Date.now()}));
    topic = shown?.id || null;
  }
  // Opening a level is a step in history; `replace` changes the current step instead. `focus` is where the focus
  // lands once the levels are in place: `depth` names the level, `enter` enters it rather than its rows, and `open`
  // opens the row it lands on.
  function go(segments, options = {}) {
    if (segments.join('.') === path.join('.')) {
      if (options.focus) aim(options.focus);
      return;
    }
    landing = options.focus ?? null;
    history[options.replace ? 'replaceState' : 'pushState']({tour:segments.join('.')}, '', tourHref(segments));
    return show(segments);
  }

  columns.addEventListener('click', event => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const retry = event.target.closest('.tour-retry');
    if (retry) { show(path); return; }
    const spine = event.target.closest('.tour-spine');
    if (spine) { unfold(spine.closest('.tour-column')); return; }
    // A reference inside a detail's own words opens its definition as the next level.
    const ref = event.target.closest('button.ref[data-entry]');
    if (ref) {
      const depth = Number(ref.closest('.tour-column').dataset.depth);
      go([...path.slice(0, depth), ref.dataset.entry], {focus:{depth:depth + 1, enter:true}});
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
  // From a level to the left, Space and Page Up/Down read on in the deepest level without leaving the rows.
  function readOn(event, section) {
    const last = deepest();
    if (section === last || !last || ![' ', 'PageDown', 'PageUp'].includes(event.key)) return false;
    const down = event.key === 'PageDown' || (event.key === ' ' && !event.shiftKey);
    // Presses that come before the last page has finished scrolling add to where it was going.
    const from = reading.get(last) ?? last.scrollTop;
    const top = Math.max(0, Math.min(last.scrollHeight - last.clientHeight, from + (down ? 1 : -1) * last.clientHeight * 0.85));
    if (top === last.scrollTop) return true;
    reading.set(last, top);
    last.addEventListener('scrollend', () => reading.delete(last), {once:true});
    last.scrollTo({top, behavior:reduced.matches ? 'auto' : 'smooth'});
    return true;
  }
  columns.addEventListener('keydown', event => {
    stopFollowing();
    if (event.altKey || event.ctrlKey || event.metaKey || event.isComposing) return;
    const section = event.target.closest('.tour-column');
    if (!section) return;
    const depth = Number(section.dataset.depth);
    const spine = event.target.closest('.tour-spine');
    if (spine) {
      if (event.shiftKey) return;
      const next = section.nextElementSibling;
      if (event.key === 'ArrowLeft') { event.preventDefault(); climb(section); }
      else if (event.key === 'ArrowRight' && next) { event.preventDefault(); next.dataset.place === 'folded' ? unfold(next) : enter(next, true); }
      return;
    }
    // Escape closes the levels to the right of this one; the focus stays where it is.
    if (event.key === 'Escape' && section !== deepest() && !event.shiftKey) { event.preventDefault(); go(path.slice(0, depth)); return; }
    // Reading a detail: the arrow keys scroll its text natively; → moves on to its rows, ← climbs.
    if (event.target === section) {
      if (event.shiftKey) return;
      if (event.key === 'ArrowLeft') { event.preventDefault(); climb(section); }
      else if (event.key === 'ArrowRight' && section.querySelector('.tour-item')) { event.preventDefault(); focusRows(section, true); }
      return;
    }
    const link = event.target.closest('.tour-item');
    if (!link) return;
    if (readOn(event, section)) { event.preventDefault(); return; }
    if (event.shiftKey) return;
    const items = [...section.querySelectorAll('.tour-item')], i = items.indexOf(link);
    let target = null;
    if (event.key === 'ArrowDown') target = items[Math.min(i + 1, items.length - 1)];
    else if (event.key === 'ArrowUp') {
      if (i === 0 && detailReady(section)) { event.preventDefault(); read(section); return; }
      target = items[Math.max(i - 1, 0)];
    }
    else if (event.key === 'Home') target = items[0];
    else if (event.key === 'End') target = items.at(-1);
    else if (event.key === 'ArrowLeft') { event.preventDefault(); climb(section); return; }
    // → and Enter enter the level the row opened, opening it first when it is not open yet.
    else if (event.key === 'ArrowRight' || event.key === 'Enter') {
      if (!opens(link)) return;
      event.preventDefault();
      const focus = {depth:depth + 1, enter:true, open:true};
      if (link.getAttribute('aria-current') === 'true') aim(focus);
      else go(segmentsOf(new URL(link.href).search), {focus});
      return;
    } else return;
    event.preventDefault();
    items.forEach(each => { each.tabIndex = each === target ? 0 : -1; });
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

  const start = segmentsOf(location.search);
  history.replaceState({tour:start.join('.')}, '', tourHref(start));
  show(start);
  return {show, go};
})();
