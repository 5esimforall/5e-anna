/* The reader's library: which collections Anna and Stella read, in what priority, how same-name entries show,
   whose text leads a work several collections hold, and which rules generations and works count. Like the theme
   it belongs to this browser and every page shares it: the topbar opens it on every page, and every request of
   the query page, the tour and Stella carries it.
   It loads before topbar.js; the button and its dialog are wired once the page has parsed. */
const annaLibrary = (() => {
  const KEY = 'anna.library', CATALOG_KEY = 'anna.libraryCatalog', DEFAULT = '5echm', EVENT = 'anna-library';
  const CONTENT = ['official','partnered','unverified','curated','community'];
  const RULESETS = ['5r','5e','待核'];
  // The two kinds of content readers ask for first; each stands for the classes it covers.
  const KINDS = {official:['official'], thirdParty:['partnered','curated','community'], unverified:['unverified']};
  const VALID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/, WORK = /^[a-z0-9]+(?:-[a-z0-9]+)*:.{1,300}$/;
  const labels = {
    zh:{button:'资料库', buttonTitle:'资料库设置 · {summary}', narrowed:'已收窄范围', title:'资料库',
      intro:'选择查阅和阅读哪些资料，以及重复的条目与作品如何显示。设置保存在本浏览器，查询、导览与 Stella 共用。',
      libraries:'资料库与优先级', librariesHint:'勾选要查阅的资料库；拖动或用箭头排序，靠前的资料库在同名条目中优先。',
      entries:'{count} 部作品', reserved:'未接入', tour:'导览', reader:'阅读', moveUp:'上移 {name}', moveDown:'下移 {name}', drag:'拖动以排序',
      lastLibrary:'至少保留一个资料库',
      duplicates:'同名条目', dedupe:'去重：同名条目合为一行', prefer:'优先显示',
      dedupeHint:'同名的出版物与家酿合为一行：先显示官方与合作出版物，再按优先的规则版本、领头的译本与资料库顺序。其余版本列在行内，标出它与所示版本的关系：同一作品、派生或同名候选。',
      editions:'同一作品的领头译本', editionsCount:'{count} 部作品在多个资料库中都有', editionsChosen:'已指定 {count} 部',
      editionsHint:'同一部作品在几个资料库中各有一份文本时，选择由谁领头：同名条目的行首，以及书架上默认打开的文本；未指定时按资料库顺序。', editionsFilter:'筛选作品名或缩写', byOrder:'按资料库顺序',
      rulesets:'规则版本', rulesetNames:{'5e':['2014 版','5e'],'5r':['2024 版','5.5e'],'待核':['待核','规则版本未确认']}, lastRuleset:'至少保留一种规则版本',
      content:'内容范围', kinds:{official:['官方内容','威世智出版物'],thirdParty:['第三方内容','合作出版物与社区家酿'],unverified:['性质待核','模组、UA、Drops 等出处尚未确认的作品']},
      classes:{official:'官方',partnered:'合作',unverified:'待核',curated:'本站精选',community:'社区'},
      works:'按作品挑选', worksHidden:'隐藏了 {count} 部', worksFilter:'筛选作品名或缩写', worksEmpty:'没有匹配的作品。', kindCount:'{on} / {all} 部 · {entries} 条',
      reset:'恢复默认', done:'完成', close:'关闭', summary:'{libraries} · {works} / {total} 部作品',
      loading:'正在读取资料库…', loadError:'无法读取资料库：{message}', retry:'重试'},
    en:{button:'Library', buttonTitle:'Library settings · {summary}', narrowed:'Scope narrowed', title:'Library',
      intro:'Choose what to search and read, and how repeated entries and works show. The setting stays in this browser and applies to search, the tour and Stella.',
      libraries:'Collections and priority', librariesHint:'Tick the collections to read; drag or use the arrows to order them. Earlier collections lead same-name entries.',
      entries:'{count} works', reserved:'not connected', tour:'Tour', reader:'Book', moveUp:'Move {name} up', moveDown:'Move {name} down', drag:'Drag to reorder',
      lastLibrary:'Keep at least one collection',
      duplicates:'Same-name entries', dedupe:'Deduplicate: one row per name', prefer:'Show first',
      dedupeHint:'Publications and homebrew of the same name share a row: official and partnered publications first, then the preferred rules, the leading text and the collection order. The other versions stay in the row, each marked by how it relates to the one shown: same work, derived or same name.',
      editions:'Leading text of shared works', editionsCount:'{count} works appear in several collections', editionsChosen:'{count} chosen',
      editionsHint:'When a work appears in several collections, each with its own text, choose whose text leads: at the head of same-name rows and when the work opens from the shelf; otherwise the collection order decides.', editionsFilter:'Filter by title or abbreviation', byOrder:'By collection order',
      rulesets:'Rules', rulesetNames:{'5e':['2014','5e'],'5r':['2024','5.5e'],'待核':['Unverified','Rules version unverified']}, lastRuleset:'Keep at least one rules version',
      content:'Content', kinds:{official:['Official','Wizards of the Coast publications'],thirdParty:['Third-party','Partnered publications and homebrew'],unverified:['Unverified origin','Adventures, UA, Drops and other works of unconfirmed origin']},
      classes:{official:'Official',partnered:'Partnered',unverified:'Unverified',curated:'Site curated',community:'Community'},
      works:'Choose works', worksHidden:'{count} hidden', worksFilter:'Filter by title or abbreviation', worksEmpty:'No matching works.', kindCount:'{on} of {all} works · {entries} entries',
      reset:'Reset', done:'Done', close:'Close', summary:'{libraries} · {works} of {total} works',
      loading:'Loading the library…', loadError:'Unable to load the library: {message}', retry:'Try again'}
  };
  const {esc, fill, lang} = annaKit;
  const ui = () => labels[lang()];
  const fmt = n => Number(n).toLocaleString(lang() === 'en' ? 'en' : 'zh-CN');
  const parse = raw => { try { return JSON.parse(raw); } catch { return null; } };

  // --- The setting, as stored ---
  // order: every collection seen, in priority order; enabled: those read; versions/prefer: same-name rows;
  // rulesets/content: what counts; hidden: works left out one by one, as {collection: [work id]};
  // lead: works whose text leads the same work's other texts, as ['collection:work id'].
  function catalog() {
    const raw = parse(annaKit.stored.get(CATALOG_KEY));
    return Array.isArray(raw) ? raw.filter(c => c && VALID.test(c.id)) : [];
  }
  function normalize(raw) {
    const s = raw && typeof raw === 'object' ? raw : {};
    const known = catalog().map(c => c.id);
    const list = value => Array.isArray(value) ? [...new Set(value.filter(k => typeof k === 'string' && VALID.test(k)))] : null;
    const order = [...new Set([...(list(s.order) || []), ...(known.length ? known : [DEFAULT])])];
    let enabled = list(s.enabled) || [DEFAULT];
    if (!enabled.length) enabled = [DEFAULT];
    const choose = (value, allowed) => Array.isArray(value) ? allowed.filter(v => value.includes(v)) : [...allowed];
    let rulesets = choose(s.rulesets, RULESETS), content = choose(s.content, CONTENT);
    if (!rulesets.length) rulesets = [...RULESETS];
    const hidden = {};
    if (s.hidden && typeof s.hidden === 'object') {
      for (const [key, ids] of Object.entries(s.hidden)) {
        if (VALID.test(key) && Array.isArray(ids)) {
          const kept = [...new Set(ids.filter(id => typeof id === 'string' && id && id.length <= 300))];
          if (kept.length) hidden[key] = kept;
        }
      }
    }
    const lead = Array.isArray(s.lead) ? [...new Set(s.lead.filter(v => typeof v === 'string' && WORK.test(v)))] : [];
    return {order, enabled: enabled.filter(k => order.includes(k)), versions: s.versions === 'each' ? 'each' : 'stacked',
      prefer: s.prefer === '5e' ? '5e' : '5r', rulesets, content, hidden, lead};
  }
  const read = () => normalize(parse(annaKit.stored.get(KEY)));
  function write(settings) {
    annaKit.stored.set(KEY, JSON.stringify(normalize(settings)));
    document.dispatchEvent(new CustomEvent(EVENT));
  }
  // Readers who never changed the setting read the default book alone, as before.
  const isDefault = s => JSON.stringify(normalize(s)) === JSON.stringify(normalize(null));
  function reset() { annaKit.stored.set(KEY, null); document.dispatchEvent(new CustomEvent(EVENT)); }
  // The collections read, in priority order; a collection the server no longer offers is skipped by the server.
  const libraries = (s = read()) => s.order.filter(k => s.enabled.includes(k));
  const narrowed = (s = read()) => s.rulesets.length < RULESETS.length || s.content.length < CONTENT.length || Object.keys(s.hidden).length > 0;
  function subscribe(listener) {
    document.addEventListener(EVENT, listener);
    annaKit.stored.watch(KEY, listener);
  }
  // The scope every request of the query page, the tour and Stella carries.
  function params(s = read()) {
    const out = [];
    const libs = libraries(s);
    if (libs.join() !== DEFAULT) out.push(['libraries', libs.join(',')]);
    if (s.versions !== 'stacked') out.push(['versions', s.versions]);
    if (s.prefer !== '5r') out.push(['prefer', s.prefer]);
    if (s.rulesets.length < RULESETS.length) out.push(['rulesets', s.rulesets.join(',')]);
    if (s.content.length < CONTENT.length) out.push(['content', s.content.join(',')]);
    for (const [key, ids] of Object.entries(s.hidden)) for (const id of ids) out.push(['hide', key + ':' + id]);
    for (const work of s.lead) out.push(['lead', work]);
    return out;
  }
  function withScope(path) {
    const url = new URL(path, location.origin);
    for (const key of ['libraries','versions','prefer','rulesets','content','hide','lead']) url.searchParams.delete(key);
    for (const [k, v] of params()) url.searchParams.append(k, v);
    return url.pathname + url.search;
  }
  const nameOf = id => { const c = catalog().find(x => x.id === id); return c?.label?.[lang()] || c?.label?.zh || id; };

  // --- The server's collections and works ---
  let described = null, describing = null;
  function remember(collections) {
    annaKit.stored.set(CATALOG_KEY, JSON.stringify(collections.map(({id, label, status, tour, reader}) => ({id, label, status, tour, reader}))));
  }
  async function describe(works = false) {
    if (described && (!works || described.works)) return described;
    if (!describing || works) {
      describing = annaKit.request('/api/library' + (works ? '?works=1' : '')).then(async r => {
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || body.code || r.status);
        described = {...body, works};
        remember(body.collections);
        return described;
      }).finally(() => { describing = null; });
    }
    return describing;
  }

  // --- The topbar button ---
  let button, dialog, page = '';
  function summary(s = read()) {
    const names = libraries(s).map(nameOf);
    return names.length ? names.join(' + ') : nameOf(DEFAULT);
  }
  function renderButton() {
    if (!button) return;
    const s = read(), text = summary(s), changed = narrowed(s) || s.versions !== 'stacked' || s.prefer !== '5r' || s.lead.length > 0;
    button.querySelector('.library-summary').textContent = text;
    button.classList.toggle('changed', changed);
    const title = fill(ui().buttonTitle, {summary: text}) + (changed ? ' · ' + ui().narrowed : '');
    button.title = title; button.setAttribute('aria-label', title);
  }
  function mount() {
    const header = document.querySelector('header.topbar[data-page]');
    button = header?.querySelector('.library-button');
    if (!button) return;
    page = header.dataset.page;
    button.querySelector('.library-word').textContent = ui().button;
    button.hidden = false;
    renderButton();
    button.addEventListener('click', open);
    subscribe(() => { renderButton(); if (dialog?.open) sync(); });
    describe().then(renderButton).catch(() => {});
  }
  // --- The dialog ---
  let filterText = '';
  function open() {
    if (!dialog) build();
    dialog.showModal();
    renderBody();
    describe(true).then(() => { if (dialog.open) renderBody(); }).catch(error => {
      if (!dialog.open) return;
      dialog.querySelector('.library-body').innerHTML = `<p class="library-message" role="alert">${esc(fill(ui().loadError, {message: error.message}))} <button type="button" data-retry>${esc(ui().retry)}</button></p>`;
    });
  }
  function build() {
    dialog = document.createElement('dialog');
    dialog.id = 'library-dialog'; dialog.className = 'library-dialog';
    dialog.setAttribute('aria-labelledby', 'library-title');
    dialog.innerHTML = `<header class="library-head"><h2 id="library-title">${esc(ui().title)}</h2><button type="button" class="icon-button" data-close aria-label="${esc(ui().close)}" title="${esc(ui().close)}">×</button></header>
      <div class="library-body" tabindex="-1"></div>
      <footer class="library-foot"><button type="button" class="quiet-button" data-reset>${esc(ui().reset)}</button><span class="library-total" role="status"></span><button type="button" class="library-done" data-close>${esc(ui().done)}</button></footer>`;
    document.body.append(dialog);
    dialog.addEventListener('click', event => {
      if (event.target === dialog || event.target.closest('[data-close]')) { dialog.close(); return; }
      if (event.target.closest('[data-reset]')) { reset(); renderBody(); return; }
      if (event.target.closest('[data-retry]')) { described = null; open(); return; }
      const move = event.target.closest('[data-move]');
      if (move) { reorder(move.dataset.library, Number(move.dataset.move)); return; }
      const prefer = event.target.closest('[data-prefer]');
      if (prefer) { change(s => { s.prefer = prefer.dataset.prefer; }); }
    });
    dialog.addEventListener('change', onChange);
    dialog.addEventListener('input', event => {
      if (event.target.matches('.library-filter')) { filterText = event.target.value.trim().toLocaleLowerCase(); filterWorks(); }
      if (event.target.matches('.library-edition-filter')) { editionText = event.target.value.trim().toLocaleLowerCase(); filterEditions(); }
    });
    dialog.addEventListener('close', () => button?.focus());
    // Esc closes the dialog only; the page beneath (an open reader, a menu) keeps its own state.
    dialog.addEventListener('keydown', event => { if (event.key === 'Escape') event.stopPropagation(); });
    // Rows reorder by dragging their handle as well as with the arrow buttons.
    let dragging = null;
    dialog.addEventListener('dragstart', event => {
      const row = event.target.closest('[data-library-row]');
      if (!row) return;
      dragging = row.dataset.libraryRow; event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', dragging); row.classList.add('dragging');
    });
    dialog.addEventListener('dragover', event => {
      const row = event.target.closest('[data-library-row]');
      if (!dragging || !row || row.dataset.libraryRow === dragging) return;
      event.preventDefault();
      const s = read(), from = s.order.indexOf(dragging), to = s.order.indexOf(row.dataset.libraryRow);
      reorder(dragging, to - from, false);
    });
    dialog.addEventListener('dragend', () => { dragging = null; dialog.querySelector('.dragging')?.classList.remove('dragging'); });
  }
  function change(edit) {
    const s = read(); edit(s); write(s);
  }
  // The arrows move a row and the rows it passes slide to their new places; a dragged row already follows the pointer,
  // so dragging reorders in place.
  function reorder(id, delta, slide = true) {
    const s = read(), from = s.order.indexOf(id), to = Math.max(0, Math.min(s.order.length - 1, from + delta));
    if (from < 0 || from === to) return;
    s.order.splice(to, 0, ...s.order.splice(from, 1));
    write(s);
    if (slide && typeof annaMotion !== 'undefined')
      annaMotion.rearrange(dialog.querySelector('.library-order'), '[data-library-row]', row => row.dataset.libraryRow, renderLibraries);
    else renderLibraries();
    const control = dialog.querySelector(`[data-library-row="${CSS.escape(id)}"] [data-move="${delta < 0 ? -1 : 1}"]:not(:disabled)`)
      || dialog.querySelector(`[data-library-row="${CSS.escape(id)}"] input`);
    control?.focus();
  }
  const works = () => (described?.collections || []).filter(c => c.works);
  const classOf = work => work.content;
  const hiddenSet = s => new Set(Object.entries(s.hidden).flatMap(([k, ids]) => ids.map(id => k + ':' + id)));
  const visible = (s, key, work, hidden = hiddenSet(s)) => s.content.includes(classOf(work)) && !hidden.has(key + ':' + work.id);
  function onChange(event) {
    const el = event.target;
    if (el.matches('[data-library]')) {
      change(s => { s.enabled = el.checked ? [...s.enabled, el.dataset.library] : s.enabled.filter(k => k !== el.dataset.library); });
      sync(); return;
    }
    if (el.matches('[data-dedupe]')) { change(s => { s.versions = el.checked ? 'stacked' : 'each'; }); sync(); return; }
    if (el.matches('[data-ruleset]')) {
      change(s => { s.rulesets = el.checked ? RULESETS.filter(r => r === el.dataset.ruleset || s.rulesets.includes(r)) : s.rulesets.filter(r => r !== el.dataset.ruleset); });
      sync(); return;
    }
    if (el.matches('[data-kind]')) { setClasses(KINDS[el.dataset.kind], el.checked); sync(); return; }
    if (el.matches('[data-class]')) { setClasses([el.dataset.class], el.checked); sync(); return; }
    if (el.matches('[data-work]')) { setWork(el.dataset.collection, el.dataset.work, el.checked); sync(); return; }
    if (el.matches('[data-edition]')) {
      const members = shownEditions[Number(el.dataset.edition)]?.members.map(m => m.key) || [];
      change(s => { s.lead = [...s.lead.filter(k => !members.includes(k)), ...(el.value ? [el.value] : [])]; });
      sync();
    }
  }
  // Works that are one publication in several of the collections read, each a choice of whose text leads.
  let shownEditions = [], editionText = '';
  function editions(s = read()) {
    const libs = libraries(s), groups = works().filter(c => libs.includes(c.id)).sort((a, b) => libs.indexOf(a.id) - libs.indexOf(b.id));
    const index = new Map(groups.flatMap(c => c.works.map(w => [c.id + ':' + w.id, {collection: c.id, work: w}])));
    const seen = new Set(), out = [];
    for (const c of groups) for (const w of c.works) {
      const key = c.id + ':' + w.id;
      if (seen.has(key) || !w.counterparts?.length) continue;
      const members = [key, ...w.counterparts.map(p => p.collection + ':' + p.id)].filter(k => index.has(k));
      if (new Set(members.map(k => index.get(k).collection)).size < 2) continue;
      for (const k of members) seen.add(k);
      out.push({members: members.map(k => ({key: k, ...index.get(k)}))});
    }
    return out;
  }
  function renderEditions() {
    const box = dialog.querySelector('.library-editions');
    if (!box) return;
    const t = ui();
    shownEditions = described?.works ? editions() : [];
    box.hidden = !shownEditions.length;
    box.querySelector('.library-editions-body').innerHTML = `<p class="library-hint">${esc(t.editionsHint)}</p><input type="search" class="library-edition-filter" placeholder="${esc(t.editionsFilter)}" aria-label="${esc(t.editionsFilter)}" value="${esc(editionText)}"><ul>${shownEditions.map((edition, i) => {
      const first = edition.members[0].work, words = edition.members.map(m => m.work.name + ' ' + m.work.label).join(' ').toLocaleLowerCase();
      return `<li data-filter="${esc(words)}"><fieldset class="library-edition"><legend>${esc(first.name)}</legend>
        <label><input type="radio" name="lead-${i}" data-edition="${i}" value=""><span>${esc(t.byOrder)}</span></label>
        ${edition.members.map(m => `<label><input type="radio" name="lead-${i}" data-edition="${i}" value="${esc(m.key)}"><span><strong>${esc(nameOf(m.collection))}</strong> ${esc(m.work.label)} · ${esc(m.work.name)}</span></label>`).join('')}</fieldset></li>`;
    }).join('')}</ul>`;
    filterEditions();
  }
  function filterEditions() {
    for (const li of dialog?.querySelectorAll('.library-editions li[data-filter]') || []) li.hidden = !!editionText && !li.dataset.filter.includes(editionText);
  }
  // Turning a kind of content on shows all of its works; turning it off leaves the works' own choices behind.
  function setClasses(classes, on) {
    change(s => {
      s.content = on ? CONTENT.filter(c => classes.includes(c) || s.content.includes(c)) : s.content.filter(c => !classes.includes(c));
      for (const c of works()) {
        const ids = new Set(c.works.filter(w => classes.includes(classOf(w))).map(w => w.id));
        if (s.hidden[c.id]) s.hidden[c.id] = s.hidden[c.id].filter(id => !ids.has(id));
      }
    });
  }
  // Choosing one work of a kind that is off shows that work alone among its kind.
  function setWork(key, id, on) {
    change(s => {
      const work = works().find(c => c.id === key)?.works.find(w => w.id === id);
      if (!work) return;
      const hidden = new Set(s.hidden[key] || []);
      if (on && !s.content.includes(classOf(work))) {
        s.content = CONTENT.filter(c => c === classOf(work) || s.content.includes(c));
        for (const c of works()) {
          const others = c.works.filter(w => classOf(w) === classOf(work) && !(c.id === key && w.id === id)).map(w => w.id);
          s.hidden[c.id] = [...new Set([...(c.id === key ? hidden : s.hidden[c.id] || []), ...others])];
        }
        s.hidden[key] = s.hidden[key].filter(x => x !== id);
        return;
      }
      on ? hidden.delete(id) : hidden.add(id);
      s.hidden[key] = [...hidden];
    });
  }
  function renderBody() {
    const body = dialog.querySelector('.library-body');
    const t = ui();
    body.innerHTML = `<p class="library-intro">${esc(t.intro)}</p>
      <section class="library-section" aria-labelledby="library-order-title"><h3 id="library-order-title">${esc(t.libraries)}</h3><p class="library-hint">${esc(t.librariesHint)}</p><ol class="library-order"></ol></section>
      <section class="library-section" aria-labelledby="library-dup-title"><h3 id="library-dup-title">${esc(t.duplicates)}</h3>
        <div class="library-line"><label class="library-check"><input type="checkbox" data-dedupe><span>${esc(t.dedupe)}</span></label>
        <div class="library-prefer"><span>${esc(t.prefer)}</span><div class="segmented" role="group" aria-label="${esc(t.prefer)}">${['5r','5e'].map(r => `<button type="button" data-prefer="${r}" title="${esc(t.rulesetNames[r].join(' · '))}">${esc(t.rulesetNames[r][0])}</button>`).join('')}</div></div></div>
        <p class="library-hint">${esc(t.dedupeHint)}</p>
        <details class="library-editions" hidden><summary>${esc(t.editions)} <span class="library-editions-count"></span></summary><div class="library-editions-body"></div></details></section>
      <section class="library-section" aria-labelledby="library-rules-title"><h3 id="library-rules-title">${esc(t.rulesets)}</h3>
        <div class="library-chips" role="group" aria-labelledby="library-rules-title">${RULESETS.map(r => `<label class="chip"><input type="checkbox" data-ruleset="${esc(r)}"><span>${esc(t.rulesetNames[r][0])}<small class="chip-count">${esc(t.rulesetNames[r][1])}</small></span></label>`).join('')}</div></section>
      <section class="library-section" aria-labelledby="library-content-title"><h3 id="library-content-title">${esc(t.content)}</h3>
        <div class="library-kinds">${Object.keys(KINDS).map(kind => `<label class="library-kind" data-kind-card="${kind}"><input type="checkbox" data-kind="${kind}"><span class="library-kind-text"><strong>${esc(t.kinds[kind][0])}</strong><small>${esc(t.kinds[kind][1])}</small><small class="library-kind-count"></small></span></label>`).join('')}</div>
        <details class="library-works"><summary>${esc(t.works)} <span class="library-hidden-count"></span></summary><div class="library-works-body"></div></details></section>`;
    renderLibraries();
    renderWorks();
    renderEditions();
    sync();
  }
  function renderLibraries() {
    const list = dialog.querySelector('.library-order');
    if (!list) return;
    const s = read(), t = ui(), known = new Map((described?.collections || catalog()).map(c => [c.id, c]));
    const counts = new Map(works().map(c => [c.id, c.works.length]));
    list.innerHTML = s.order.filter(id => known.has(id)).map((id, i, ids) => {
      const c = known.get(id), ready = c.status === 'ready', name = nameOf(id);
      const offers = [ready && c.tour ? t.tour : '', ready && c.reader ? t.reader : ''].filter(Boolean).join(' · ');
      const meta = ready ? [counts.has(id) ? fill(t.entries, {count: fmt(counts.get(id))}) : '', offers].filter(Boolean).join(' · ') : t.reserved;
      return `<li class="library-row ${ready ? '' : 'reserved'}" data-library-row="${esc(id)}" draggable="${ready}">
        <span class="library-handle" aria-hidden="true" title="${esc(t.drag)}"><svg viewBox="0 0 24 24"><path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01"/></svg></span>
        <label class="library-name"><input type="checkbox" data-library="${esc(id)}" ${ready ? '' : 'disabled'}><span><strong>${esc(name)}</strong><small>${esc(meta)}</small></span></label>
        <span class="library-moves"><button type="button" class="icon-button" data-library="${esc(id)}" data-move="-1" aria-label="${esc(fill(t.moveUp, {name}))}" title="${esc(fill(t.moveUp, {name}))}" ${i ? '' : 'disabled'}><svg viewBox="0 0 24 24"><path d="M6 15l6-6 6 6"/></svg></button><button type="button" class="icon-button" data-library="${esc(id)}" data-move="1" aria-label="${esc(fill(t.moveDown, {name}))}" title="${esc(fill(t.moveDown, {name}))}" ${i < ids.length - 1 ? '' : 'disabled'}><svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg></button></span>
      </li>`;
    }).join('');
    sync();
  }
  function renderWorks() {
    const box = dialog.querySelector('.library-works-body');
    if (!box) return;
    const t = ui(), groups = works();
    if (!described?.works) { box.innerHTML = `<p class="library-message">${esc(t.loading)}</p>`; return; }
    box.innerHTML = `<input type="search" class="library-filter" placeholder="${esc(t.worksFilter)}" aria-label="${esc(t.worksFilter)}" value="${esc(filterText)}">` +
      groups.map(c => `<section class="library-works-group" data-works-of="${esc(c.id)}"><h4>${esc(nameOf(c.id))}</h4>${CONTENT.map(cls => {
        const list = c.works.filter(w => classOf(w) === cls);
        if (!list.length) return '';
        return `<fieldset class="library-class"><legend><label class="library-check"><input type="checkbox" data-class="${cls}"><span>${esc(t.classes[cls])}</span></label><small>${esc(fill(t.entries, {count: fmt(list.length)}))}</small></legend>
          <ul>${list.map(w => `<li data-filter="${esc((w.name + ' ' + w.label).toLocaleLowerCase())}"><label><input type="checkbox" data-collection="${esc(c.id)}" data-work="${esc(w.id)}"><span class="library-work-label">${esc(w.label)}</span><span class="library-work-name">${esc(w.name)}</span>${w.ruleset !== '待核' ? `<span class="library-work-rules">${esc(t.rulesetNames[w.ruleset]?.[0] || w.ruleset)}</span>` : ''}<span class="library-work-count">${esc(fmt(w.count))}</span></label></li>`).join('')}</ul></fieldset>`;
      }).join('')}</section>`).join('') + `<p class="library-message library-works-empty" hidden>${esc(t.worksEmpty)}</p>`;
    filterWorks();
  }
  function filterWorks() {
    if (!dialog) return;
    let shown = 0;
    for (const li of dialog.querySelectorAll('.library-works li[data-filter]')) {
      li.hidden = !!filterText && !li.dataset.filter.includes(filterText);
      shown += !li.hidden;
    }
    for (const set of dialog.querySelectorAll('.library-class')) set.hidden = !set.querySelector('li:not([hidden])');
    const empty = dialog.querySelector('.library-works-empty');
    if (empty) empty.hidden = !!shown || !dialog.querySelector('.library-works li');
  }
  // States and counts change in place, so focus stays on the control just used.
  function sync() {
    if (!dialog?.open) return;
    const s = read(), t = ui(), hidden = hiddenSet(s), libs = libraries(s);
    for (const input of dialog.querySelectorAll('input[data-library]')) {
      const reserved = !!input.closest('.reserved');
      input.checked = !reserved && s.enabled.includes(input.dataset.library);
      const last = input.checked && libs.length === 1;
      input.disabled = reserved || last;
      input.closest('label').title = last ? t.lastLibrary : '';
    }
    const dedupe = dialog.querySelector('[data-dedupe]');
    if (dedupe) dedupe.checked = s.versions === 'stacked';
    // The shared works follow the collections read; their choices follow the stored lead.
    if (described?.works && editions(s).map(e => e.members.map(m => m.key).join()).join('|') !== shownEditions.map(e => e.members.map(m => m.key).join()).join('|')) renderEditions();
    for (const input of dialog.querySelectorAll('[data-edition]')) {
      const members = shownEditions[Number(input.dataset.edition)]?.members.map(m => m.key) || [];
      input.checked = input.value ? s.lead.includes(input.value) : !members.some(k => s.lead.includes(k));
    }
    const editionsCount = dialog.querySelector('.library-editions-count');
    if (editionsCount) {
      const chosen = shownEditions.filter(e => e.members.some(m => s.lead.includes(m.key))).length;
      editionsCount.textContent = [fill(t.editionsCount, {count: fmt(shownEditions.length)}), chosen ? fill(t.editionsChosen, {count: fmt(chosen)}) : ''].filter(Boolean).join(' · ');
    }
    for (const b of dialog.querySelectorAll('[data-prefer]')) { b.setAttribute('aria-pressed', String(s.prefer === b.dataset.prefer)); b.disabled = s.versions !== 'stacked'; }
    for (const input of dialog.querySelectorAll('[data-ruleset]')) {
      input.checked = s.rulesets.includes(input.dataset.ruleset);
      input.disabled = input.checked && s.rulesets.length === 1;
      input.closest('label').title = input.disabled ? t.lastRuleset : t.rulesetNames[input.dataset.ruleset][1];
    }
    // A kind or class is ticked when all of its works show, mixed when some do, empty when none do.
    const read_ = libs.length ? works().filter(c => libs.includes(c.id)) : works();
    const tally = classes => {
      let all = 0, on = 0, entries = 0;
      for (const c of read_) for (const w of c.works) if (classes.includes(classOf(w))) {
        all++;
        if (visible(s, c.id, w, hidden)) { on++; entries += w.count; }
      }
      return {all, on, entries};
    };
    const mark = (input, {all, on}, classes) => {
      const enabled = classes.some(c => s.content.includes(c));
      input.checked = enabled && (all === 0 || on > 0);
      input.indeterminate = enabled && all > 0 && on > 0 && on < all;
    };
    for (const input of dialog.querySelectorAll('[data-kind]')) {
      const classes = KINDS[input.dataset.kind], counts = tally(classes);
      mark(input, counts, classes);
      const card = input.closest('[data-kind-card]');
      card.hidden = !!described?.works && counts.all === 0 && input.dataset.kind === 'unverified';
      card.querySelector('.library-kind-count').textContent = described?.works ? fill(t.kindCount, {on: fmt(counts.on), all: fmt(counts.all), entries: fmt(counts.entries)}) : '';
    }
    for (const input of dialog.querySelectorAll('[data-class]')) mark(input, tally([input.dataset.class]), [input.dataset.class]);
    for (const input of dialog.querySelectorAll('[data-work]')) {
      const work = works().find(c => c.id === input.dataset.collection)?.works.find(w => w.id === input.dataset.work);
      if (work) input.checked = visible(s, input.dataset.collection, work, hidden);
    }
    const off = works().reduce((n, c) => n + c.works.filter(w => !visible(s, c.id, w, hidden)).length, 0);
    const count = dialog.querySelector('.library-hidden-count');
    if (count) count.textContent = off ? fill(t.worksHidden, {count: fmt(off)}) : '';
    const shownWorks = read_.reduce((n, c) => n + c.works.filter(w => visible(s, c.id, w, hidden)).length, 0);
    const totalWorks = read_.reduce((n, c) => n + c.works.length, 0);
    dialog.querySelector('.library-total').textContent = described?.works ? fill(t.summary, {libraries: libs.map(nameOf).join(' + '), works: fmt(shownWorks), total: fmt(totalWorks)}) : '';
    dialog.querySelector('[data-reset]').disabled = isDefault(s);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
  return {read, write, reset, params, withScope, libraries, narrowed, subscribe, describe, open, nameOf, DEFAULT};
})();
