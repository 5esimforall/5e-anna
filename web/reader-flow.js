/* One outer scroll surface. Documents are mounted only on explicit navigation/loading. */
class AnnaReaderFlow {
  constructor({scroll, container, snapshot, contentBase, fetchPage, labels, text, onActive, onNavigate, onTools, onSearch}) {
    Object.assign(this,{scroll,container,snapshot,contentBase,fetchPage,labels,text,onActive,onNavigate,onTools,onSearch});
    this.records = new Map(); this.serial = 0; this.locationSerial = 0; this.scope = null; this.active = null;
    this.scroll.addEventListener('scroll', () => this.schedulePosition(), {passive:true});
    window.addEventListener('message', event => this.message(event));
    this.container.addEventListener('click', event => {
      const button = event.target.closest('[data-load-page]');
      if (!button) return;
      const path = button.dataset.loadPage;
      this.ensure(path).then(record => {
        if (button === document.activeElement || !document.activeElement || document.activeElement === document.body)
          record.heading.focus({preventScroll:true});
        this.capture();
      }).catch(() => {}); // The document slot owns its retry/error state.
    });
  }
  reset(scope) {
    this.locationSerial++;
    for (const record of this.records.values()) {
      record.abort.abort(); clearTimeout(record.timer);
      record.reject?.(new DOMException('Navigation superseded','AbortError'));
      clearTimeout(record.location?.timer);
      record.location?.reject(new DOMException('Navigation superseded','AbortError'));
    }
    clearTimeout(this.positionTimer); this.positionTimer = 0;
    this.records.clear(); this.scope = scope; this.active = null; this.pending = false;
    this.container.replaceChildren(); this.scroll.scrollTop = 0;
  }
  top(record) { return record.frame.getBoundingClientRect().top - this.scroll.getBoundingClientRect().top + this.scroll.scrollTop; }
  updateSlots() {
    const location = this.viewport();
    // Preserve mounted iframe nodes: never append an existing frame to a fragment.
    const desired = [];
    for (let index = 0; index < this.scope.pages.length;) {
      const record = this.records.get(this.scope.pages[index].path);
      if (record) { desired.push(record.element); index++; continue; }
      const start = index;
      while (index < this.scope.pages.length && !this.records.has(this.scope.pages[index].path)) index++;
      const next = this.scope.pages[start === 0 ? index-1 : start];
      const gap = document.createElement('div'); gap.className = 'book-page-gap';
      const note = document.createElement('span'); note.textContent = this.text('unloadedPages',{start:start+1,end:index});
      const button = document.createElement('button'); button.type = 'button'; button.dataset.loadPage = next.path;
      button.textContent = this.text(start === 0 ? 'loadPrevious' : 'loadNext',{name:next.label});
      gap.append(note,button); desired.push(gap);
    }
    if (this.records.get(this.scope.pages.at(-1)?.path)?.status === 'ready') {
      const end = document.createElement('p'); end.className = 'book-scope-end'; end.textContent = this.labels.scopeEnd; desired.push(end);
    }
    for (const element of [...this.container.children]) if (!desired.includes(element)) element.remove();
    let previous = null;
    for (const element of desired) {
      const next = previous ? previous.nextSibling : this.container.firstChild;
      if (element !== next) this.container.insertBefore(element,next);
      previous = element;
    }
    this.keepLocation(location);
  }
  location() {
    if (!this.active || this.active.status !== 'ready') return null;
    return {path:this.active.item.path,position:{x:this.scroll.scrollLeft,y:Math.max(0,this.scroll.scrollTop-this.top(this.active))}};
  }
  viewport() {
    return this.active?.status === 'ready' ? {path:this.active.item.path,position:{x:this.scroll.scrollLeft,y:this.scroll.scrollTop-this.top(this.active)}} : null;
  }
  keepLocation(location) {
    const record = location && this.records.get(location.path);
    if (record?.status === 'ready') this.scroll.scrollTop = this.top(record)+location.position.y;
  }
  schedulePosition() {
    if (this.positionTimer) return;
    this.positionTimer = setTimeout(() => { this.positionTimer = 0; this.capture(); },100);
  }
  capture() {
    if (this.pending) return this.location();
    const ready = [...this.records.values()].filter(r => r.status === 'ready').sort((a,b) => a.index-b.index);
    if (!ready.length) return null;
    const y = this.scroll.scrollTop + 2;
    const record = ready.find(r => y >= this.top(r) && y < this.top(r)+r.height) ||
      ready.find(r => this.top(r) > y) || ready.at(-1);
    this.activate(record);
    const location = this.location();
    this.onActive(record.data,location.position,record.item);
    return location;
  }
  activate(record) {
    if (this.active === record) return;
    if (this.active) this.active.frame.removeAttribute('id');
    this.active = record; record.frame.id = 'content';
  }
  resize(record, height) {
    if (!Number.isFinite(height) || height <= 0 || height > 20000000) return;
    if (!this.pending) this.capture();
    const location = this.viewport();
    record.height = Math.ceil(height); record.frame.style.height = record.height+'px';
    if (!this.pending) this.keepLocation(location);
  }
  async ensure(path, data) {
    const existing = this.records.get(path);
    if (existing && existing.status !== 'error') return existing.promise;
    const index = this.scope.pages.findIndex(item => item.path === path);
    if (index < 0) throw new Error('outside_scope');
    const item = this.scope.pages[index], element = existing?.element || document.createElement('section');
    element.className = 'book-page'; element.dataset.path = path; element.setAttribute('aria-busy','true');
    const heading = document.createElement('div'); heading.className = 'book-page-divider'; heading.tabIndex = -1;
    heading.textContent = this.text('documentPage',{page:index+1,pages:this.scope.pages.length,name:item.label || data?.title || ''});
    const status = document.createElement('p'); status.className = 'book-page-state'; status.textContent = this.labels.loading; status.setAttribute('role','status');
    const frame = document.createElement('iframe'); frame.className = 'book-document'; frame.title = item.label || this.labels.original;
    frame.setAttribute('sandbox','allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-downloads');
    frame.referrerPolicy = 'origin'; frame.style.height = '1px'; frame.style.visibility = 'hidden';
    element.replaceChildren(heading,status,frame);
    const record = {item,index,element,heading,statusElement:status,frame,status:'loading',height:1,
      navigationId:++this.serial,documentId:'',abort:new AbortController(),data};
    record.promise = new Promise((resolve,reject) => { record.resolve=resolve; record.reject=reject; });
    // A rejection is also consumed by the caller; cancellation can happen before it awaits.
    record.promise.catch(() => {});
    this.records.set(path,record); this.updateSlots();
    try {
      record.data ||= await this.fetchPage(path,record.abort.signal);
      if (record.abort.signal.aborted) throw new DOMException('Navigation superseded','AbortError');
      frame.title = record.data.title;
      heading.textContent = this.text('documentPage',{page:index+1,pages:this.scope.pages.length,name:record.data.title});
      record.timer = setTimeout(() => this.fail(record,new Error('frame_timeout')),15000);
      frame.contentWindow.location.replace(this.contentBase + path.split('/').map(encodeURIComponent).join('/'));
    } catch (error) { this.fail(record,error); }
    return record.promise;
  }
  fail(record,error) {
    if (this.records.get(record.item.path) !== record) return;
    clearTimeout(record.timer); record.status = 'error'; record.element.setAttribute('aria-busy','false');
    record.frame.hidden = true;
    record.statusElement.textContent = this.labels.errors[error.message] || this.labels.errors.network;
    record.statusElement.setAttribute('role','alert');
    const retry = document.createElement('button'); retry.type = 'button'; retry.dataset.loadPage = record.item.path; retry.textContent = this.labels.retry;
    record.statusElement.append(' ',retry); record.reject(error);
  }
  async open(data, {anchor='',position=null} = {}) {
    const sequence = ++this.locationSerial; this.pending = true;
    try {
      const record = await this.ensure(data.path,data);
      if (sequence !== this.locationSerial) throw new DOMException('Navigation superseded','AbortError');
      const result = await new Promise((resolve,reject) => {
        clearTimeout(record.location?.timer);
        record.location?.reject(new DOMException('Navigation superseded','AbortError'));
        const timer = setTimeout(() => { if (record.location?.sequence === sequence) { record.location = null; reject(new Error('frame_timeout')); } },15000);
        record.location = {sequence,resolve,reject,timer};
        this.send(record,'locate',{requestId:sequence,anchor});
      });
      if (sequence !== this.locationSerial) throw new DOMException('Navigation superseded','AbortError');
      this.activate(record);
      const destination = position || result.position;
      this.scroll.scrollTo({left:destination.x,top:this.top(record)+destination.y,behavior:'instant'});
      this.pending = false;
      this.onActive(record.data,this.location().position,record.item);
      return result;
    } catch (error) {
      if (sequence === this.locationSerial) this.pending = false;
      throw error;
    }
  }
  send(record,type,detail = {}) {
    if (!record?.documentId) return;
    record.frame.contentWindow.postMessage({channel:'anna-reader',version:2,type,documentId:record.documentId,
      snapshot:this.snapshot,navigationId:record.navigationId,...detail},location.origin);
  }
  theme(value) { for (const record of this.records.values()) this.send(record,'theme',{theme:value}); }
  focus() { this.active?.frame.focus(); this.send(this.active,'focus'); }
  message(event) {
    const data = event.data;
    if (event.origin !== location.origin || !data || data.channel !== 'anna-reader' || data.version !== 2 || data.snapshot !== this.snapshot) return;
    const record = this.records.get(data.path);
    if (!record || event.source !== record.frame.contentWindow || record.status === 'error') return;
    if (data.type === 'ready' && record.status === 'loading' && typeof data.documentId === 'string' && data.documentId.length < 100) {
      record.documentId = data.documentId;
      this.resize(record,data.height);
      this.send(record,'initialize',{theme:document.documentElement.dataset.theme}); return;
    }
    if (data.documentId !== record.documentId || data.navigationId !== record.navigationId) return;
    if (data.type === 'initialized') {
      if (record.status !== 'loading') return;
      clearTimeout(record.timer); this.resize(record,data.height); record.status = 'ready';
      record.frame.style.visibility = ''; record.statusElement.hidden = true; record.element.setAttribute('aria-busy','false');
      this.updateSlots(); record.resolve(record);
    } else if (data.type === 'size') this.resize(record,data.height);
    else if (data.type === 'located' && data.requestId === record.location?.sequence && annaReaderModel.validPosition(data.position)) {
      this.resize(record,data.height); clearTimeout(record.location.timer); record.location.resolve(data); record.location = null;
    } else if (data.type === 'navigate' && data.route && (typeof data.route.page === 'string' || typeof data.route.entry === 'string')) this.onNavigate(data.route);
    else if (data.type === 'focus-tools') this.onTools();
    else if (data.type === 'search') this.onSearch();
  }
}
