/* The other companion, beside the theme switch on both pages. Turning to her offers what she would bring up
   first, and its label says where it leads; the menu beside it lists the topic and recent places.
   Both read the shared workspace, so every tab shows the same record. */
// Companion names are not translated; the verbs say what turning to her does.
const annaCompanionLabels = {
  zh:{names:{query:'Anna',book:'Stella'}, topic:{book:'翻到 {name}',query:'查 {name}'}, resume:{book:'接着读 {name}',query:'回到 {name}'},
    go:'{brand}：{action}', more:'最近打开的位置', recent:'最近', showing:'当前', allEntries:'全部条目'},
  en:{names:{query:'Anna',book:'Stella'}, topic:{book:'Turn to {name}',query:'Look up {name}'}, resume:{book:'Keep reading {name}',query:'Back to {name}'},
    go:'{brand}: {action}', more:'Recent places', recent:'Recent', showing:'Current', allEntries:'All entries'}
};
const annaCompanions = (() => {
  let labels;
  const other = {query:'book', book:'query'};
  const fill = (template, values) => Object.entries(values).reduce((s,[k,v]) => s.replaceAll('{'+k+'}', v), template);
  let page, snapshot, slot, go, more, menu;
  // Where each choice leads. A recent place also asks the page it opens to restore that place.
  function bookHref(choice) {
    if (choice.kind === 'topic') return '/read?' + new URLSearchParams({entry:choice.topic.entry});
    const {root, page, node} = choice.memory;
    return '/read?' + new URLSearchParams(Object.entries({root, page, node}).filter(([,value]) => value));
  }
  function queryHref(choice, state) {
    if (choice.kind === 'topic') {
      // The topic opens beside the latest query, so its conditions stay in view.
      const params = new URLSearchParams(state.query[0]?.query || '');
      params.set('entry', choice.topic.entry);
      return '/?' + params;
    }
    const params = new URLSearchParams(choice.memory.query);
    if (choice.memory.entry) {
      params.set('entry', choice.memory.entry);
      const ui=choice.memory.ui;
      if(ui?.readerTab==='chain'){
        params.set('tab','chain');
        if(ui.readerChainSpell)params.set('chain_spell',ui.readerChainSpell);
        if(ui.readerChainPath)params.set('chain_path',ui.readerChainPath);
        if(ui.readerChainEffect)params.set('chain_effect',ui.readerChainEffect);
      }
    }
    return '/' + (params.size ? '?' + params : '');
  }
  const href = (choice, state) => choice.kind === 'home' ? annaTopbar.pages[other[page]].home :
    other[page] === 'book' ? bookHref(choice) : queryHref(choice, state);
  function memoryName(companion, memory) {
    return companion === 'book' ? memory.label || memory.scopeLabel || memory.page.split('/').at(-1).replace(/\.html?$/,'') :
      memory.entryName || memory.title || labels.allEntries;
  }
  function action(choice) {
    const companion = other[page];
    if (choice.kind === 'topic') return fill(labels.topic[companion], {name:choice.topic.name || choice.topic.entry});
    if (choice.kind === 'resume') return fill(labels.resume[companion], {name:memoryName(companion, choice.memory)});
    return '';
  }
  function link(choice, state, className) {
    const anchor = document.createElement('a');
    anchor.href = href(choice, state); anchor.className = className;
    if (choice.kind === 'resume') anchor.dataset.resume = annaWorkspace.memoryId[other[page]](choice.memory);
    return anchor;
  }
  function render() {
    if (!slot) return;
    const state = annaWorkspace.read(snapshot), companion = other[page], choice = annaWorkspace.offer(state, companion);
    const brand = annaBrands[companion], text = action(choice);
    go.href = href(choice, state);
    if (choice.kind === 'resume') go.dataset.resume = annaWorkspace.memoryId[companion](choice.memory);
    else delete go.dataset.resume;
    go.querySelector('.companion-label').textContent = text || brand;
    const name = text ? fill(labels.go, {brand, action:text}) : brand;
    go.setAttribute('aria-label', name); go.title = name;
    // The menu adds something only when it holds more than the one choice the button already makes.
    more.hidden = (state.topic && state.topic.from !== companion ? 1 : 0) + state[companion].length < 2;
    slot.classList.toggle('single', more.hidden);
  }
  function group(label, entries) {
    const element = document.createElement('div'); element.setAttribute('role','group'); element.setAttribute('aria-label',label);
    const heading = document.createElement('div'); heading.className = 'popup-menu-heading'; heading.setAttribute('aria-hidden','true'); heading.textContent = label;
    element.append(heading, ...entries.map(entry => { entry.setAttribute('role','menuitem'); entry.tabIndex = -1; return entry; }));
    return element;
  }
  function entry(choice, state, primary, secondary = '') {
    const anchor = link(choice, state, 'popup-menu-entry');
    const avatar = document.createElement('img'); avatar.className = 'popup-menu-avatar';
    avatar.src = annaTopbar.pages[other[page]].logo; avatar.width = avatar.height = 18; avatar.alt = '';
    const main = document.createElement('span'); main.className = 'popup-menu-label'; main.textContent = primary; anchor.append(avatar, main);
    if (secondary && secondary !== primary) {
      const detail = document.createElement('span'); detail.className = 'popup-menu-detail'; detail.textContent = secondary; anchor.append(detail);
    }
    return anchor;
  }
  function items() {
    const state = annaWorkspace.read(snapshot), companion = other[page], groups = [];
    if (state.topic && state.topic.from !== companion) {
      const choice = {kind:'topic', topic:state.topic};
      groups.push(group(labels.showing, [entry(choice, state, action(choice))]));
    }
    const places = state[companion].map(memory => {
      const choice = {kind:'resume', memory};
      return companion === 'book' ? entry(choice, state, memoryName(companion, memory), memory.scopeLabel) :
        entry(choice, state, memoryName(companion, memory), memory.entryName ? memory.title || labels.allEntries : '');
    });
    if (places.length) groups.push(group(labels.recent, places));
    return groups;
  }
  // Following a recent place in this tab restores it; a new tab or window opens the place afresh.
  function follow(event) {
    const anchor = event.target.closest('a[data-resume]');
    if (!anchor || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    annaWorkspace.requestResume(other[page], anchor.dataset.resume);
  }
  // Pages mount the companion once they know the build snapshot; without the other page there is nobody to turn to.
  function mount(options) {
    ({page, snapshot} = options);
    labels = annaCompanionLabels[document.documentElement.lang.startsWith('en') ? 'en' : 'zh'];
    slot = document.querySelector('.topbar .companion');
    const companion = other[page];
    go = document.createElement('a'); go.className = 'companion-go';
    const avatar = document.createElement('img'); avatar.className = 'companion-avatar'; avatar.src = annaTopbar.pages[companion].logo;
    avatar.width = avatar.height = 26; avatar.alt = '';
    const label = document.createElement('span'); label.className = 'companion-label';
    go.append(avatar, label);
    more = document.createElement('button'); more.type = 'button'; more.className = 'companion-more'; more.id = 'companion-more';
    more.setAttribute('aria-label', labels.more); more.title = labels.more;
    more.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
    menu = document.createElement('div'); menu.className = 'popup-menu companion-menu'; menu.id = 'companion-menu';
    menu.setAttribute('role','menu'); menu.tabIndex = -1; menu.hidden = true;
    slot.replaceChildren(go, more, menu);
    const control = annaMenu.create(menu, {items});
    control.bind(more); control.attach(slot);
    slot.addEventListener('click', follow);
    annaWorkspace.subscribe(render);
    render(); slot.hidden = false;
  }
  return {mount, refresh:render};
})();
