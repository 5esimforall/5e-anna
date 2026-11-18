'use strict';
/* Anna's tour: where to start and what to read next. Each column is one level of the way down, from a purpose
   to the book's chapters, steps and entries; choosing a row opens the next level beside it and keeps the levels
   above in view. Leads quote the source; rows lead on to the next level, to Stella's reading or to Anna's table. */
const annaTourLabels = {
  zh:{title:'导览', skip:'跳到导览', intro:'从想做的事出发，逐层走进原书的章节、步骤与条目。',
    groups:{choices:'这一步要选择', contents:'目录', outline:'本页', parts:'包含', subclasses:'子职', class:'所属职业',
      feats:'授予专长', spells:'法术', entries:'条目', shelf:'书架'},
    facet:'按{field}', readBook:'在 Stella 中阅读', openEntry:'在 Anna 中查看', openQuery:'在 Anna 中查看 {count} 条',
    spellList:'可用法术', more:'另有 {count} 项', moreInAnna:'另有 {count} 项，在 Anna 中查看全部',
    previous:'上一项：{name}', next:'下一项：{name}', back:'‹ {name}', table:'表', versions:'其他版本',
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
  const views = new Map();
  let snapshot = null, path = [], generation = 0, mounted = false, topic = null;
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
  async function fetchView(segment) {
    if (views.has(segment)) return views.get(segment);
    const params = new URLSearchParams({node:segment});
    if (snapshot) params.set('snapshot', snapshot);
    let response, body = null;
    try { response = await fetch('/api/tour?' + params); } catch { throw new LoadError('network', 0); }
    try { body = await response.json(); } catch { body = null; }
    if (!response.ok || !body) throw new LoadError(body?.code || 'error', response.status);
    snapshot ||= body.snapshot;
    views.set(segment, body);
    return body;
  }

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
    return head(view, depth) + status + lead + (actions.length ? `<div class="tour-actions">${actions.join('')}</div>` : '') +
      steps(view, depth) + sequence;
  }

  function column(segment, depth) {
    const section = document.createElement('section');
    section.className = 'tour-column'; section.dataset.depth = depth; section.dataset.segment = segment;
    section.setAttribute('aria-labelledby', `tour-title-${depth}`);
    section.setAttribute('aria-busy', 'true');
    section.innerHTML = `<div class="tour-column-body"><h2 id="tour-title-${depth}">${esc(ui.loading)}</h2></div>`;
    return section;
  }
  function render(section, view, depth) {
    section.dataset.state = 'ready'; section.removeAttribute('aria-busy');
    section.firstElementChild.innerHTML = body(view, depth);
    // An entry with no parts of its own is a leaf: show it as the book sets it, in place of the quoted lead.
    if (view.kind === 'entry' && !view.groups.some(g => g.key === 'parts')) preview(section, view);
  }
  async function preview(section, view) {
    let layout = null;
    try { layout = await (await fetch('/api/entry-layout?id=' + encodeURIComponent(view.id))).json(); } catch { return; }
    if (layout?.status !== 'ok' || !section.isConnected || section.dataset.segment !== view.id) return;
    const box = document.createElement('div'); box.className = 'tour-preview layout-text'; box.lang = 'zh-CN'; box.innerHTML = layout.html;
    const lead = section.querySelector('.tour-lead');
    lead ? lead.replaceWith(box) : section.querySelector('header').after(box);
  }
  function failed(section, error, depth) {
    section.dataset.state = 'failed'; section.removeAttribute('aria-busy');
    section.firstElementChild.innerHTML = `<h2 id="tour-title-${depth}">${esc(ui.readError)}</h2><p class="tour-status">${esc(error.code)}<button type="button" class="tour-retry">${esc(ui.retry)}</button></p>`;
  }
  function say(message) { notice.textContent = message; notice.hidden = !message; }

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
  function focusLevel(section) {
    const target = section?.querySelector('.tour-item[aria-current=true]') || section?.querySelector('.tour-item') || section?.querySelector('h2');
    if (!target) return;
    if (target.matches('h2')) { target.tabIndex = -1; }
    else section.querySelectorAll('.tour-item').forEach(link => { link.tabIndex = link === target ? 0 : -1; });
    target.focus({preventScroll:true});
    target.scrollIntoView({block:'nearest'});
  }

  async function show(segments, options = {}) {
    const run = ++generation, wanted = ['', ...segments];
    path = segments;
    say('');
    const existing = [...columns.children];
    let keep = 0;
    while (keep < existing.length && keep < wanted.length && existing[keep].dataset.segment === wanted[keep] && existing[keep].dataset.state === 'ready') keep++;
    existing.slice(keep).forEach(section => section.remove());
    const fresh = wanted.slice(keep).map((segment, i) => columns.appendChild(column(segment, keep + i)));
    markCurrent(); reveal();
    const loaded = await Promise.allSettled(wanted.slice(keep).map(fetchView));
    if (run !== generation) return;
    for (const [i, result] of loaded.entries()) {
      const depth = keep + i, section = fresh[i];
      if (result.status === 'fulfilled') { render(section, result.value, depth); continue; }
      const error = result.reason;
      if (error.status === 409) say(ui.bundleChanged);
      else if (depth === 0) say(ui.unavailable);
      // A level that no longer exists in this snapshot returns the reader to the level above it.
      if (error.status === 404 && depth > 0) {
        const kept = segments.slice(0, depth - 1);
        history.replaceState({tour:kept.join('.')}, '', tourHref(kept));
        await show(kept, options);
        if (run + 1 === generation) say(ui.nodeMissing);
        return;
      }
      failed(section, error, depth);
      fresh.slice(i + 1).forEach(rest => rest.remove());
      break;
    }
    markCurrent(); reveal();
    settle();
    if (options.focus === 'new') focusLevel(columns.lastElementChild);
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
  function go(segments, options = {}) {
    if (segments.join('.') === path.join('.')) {
      if (options.focus === 'new') focusLevel(columns.lastElementChild);
      return;
    }
    history.pushState({tour:segments.join('.')}, '', tourHref(segments));
    return show(segments, options);
  }

  columns.addEventListener('click', event => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const retry = event.target.closest('.tour-retry');
    if (retry) { show(path); return; }
    // A reference inside a previewed entry opens its definition as the next level.
    const ref = event.target.closest('button.ref[data-entry]');
    if (ref) { go([...path.slice(0, Number(ref.closest('.tour-column').dataset.depth)), ref.dataset.entry], {focus:'new'}); return; }
    const link = event.target.closest('a[data-at]');
    if (!link) return;
    event.preventDefault();
    if (link.getAttribute('aria-disabled') === 'true') return;
    go(segmentsOf(new URL(link.href).search));
  });
  columns.addEventListener('keydown', event => {
    const link = event.target.closest('.tour-item');
    if (!link || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const section = link.closest('.tour-column'), items = [...section.querySelectorAll('.tour-item')];
    const i = items.indexOf(link);
    let target = null;
    if (event.key === 'ArrowDown') target = items[Math.min(i + 1, items.length - 1)];
    else if (event.key === 'ArrowUp') target = items[Math.max(i - 1, 0)];
    else if (event.key === 'Home') target = items[0];
    else if (event.key === 'End') target = items.at(-1);
    else if (event.key === 'ArrowLeft') {
      const above = section.previousElementSibling;
      if (above) { event.preventDefault(); focusLevel(above); }
      return;
    } else if (event.key === 'ArrowRight') {
      if (!link.hasAttribute('data-at') || link.getAttribute('aria-disabled') === 'true') return;
      event.preventDefault();
      if (link.getAttribute('aria-current') === 'true' && section.nextElementSibling) focusLevel(section.nextElementSibling);
      else go(segmentsOf(new URL(link.href).search), {focus:'new'});
      return;
    } else return;
    event.preventDefault();
    items.forEach(each => { each.tabIndex = each === target ? 0 : -1; });
    target.focus();
  });
  window.addEventListener('popstate', () => show(segmentsOf(location.search)));

  const start = segmentsOf(location.search);
  history.replaceState({tour:start.join('.')}, '', tourHref(start));
  show(start);
  return {show, go};
})();
