/* One scroll surface, bounded live documents, and measured placeholders for released pages. */
const PAGE_STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 1.5C12.7 8.6 15.4 11.3 22.5 12 15.4 12.7 12.7 15.4 12 22.5 11.3 15.4 8.6 12.7 1.5 12 8.6 11.3 11.3 8.6 12 1.5Z"/></svg>';
class AnnaReaderFlow {
  constructor({scroll, container, snapshot, contentBase, fetchPage, labels, text, onActive, onNavigate, onTools, onSearch}) {
    Object.assign(this,{scroll,container,snapshot,contentBase,fetchPage,labels,text,onActive,onNavigate,onTools,onSearch});
    this.records = new Map(); this.documentSequence = 0; this.locationSequence = 0; this.scope = null; this.active = null;
    this.windowRadius = 2; this.maxDocuments = 5;
    new ResizeObserver(() => this.scheduleMaintenance()).observe(this.scroll);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) this.scheduleMaintenance(); });
    this.scroll.addEventListener('scroll', () => this.schedulePosition(), {passive:true});
    window.addEventListener('message', event => this.message(event));
    this.container.addEventListener('click', event => {
      const button = event.target.closest('[data-load-page]');
      if (!button) return;
      const path = button.dataset.loadPage;
      this.ensure(path).then(record => {
        if (button === document.activeElement || !document.activeElement || document.activeElement === document.body)
          record.heading.focus({preventScroll:true});
        this.capture(); this.scheduleMaintenance();
      }).catch(() => {}); // The document slot owns its retry/error state.
    });
  }
  reset(scope) {
    this.locationSequence++;
    for (const record of this.records.values()) this.cancel(record);
    clearTimeout(this.maintenanceTimer); this.maintenanceTimer = 0; this.automaticRequest = null;
    clearTimeout(this.positionTimer); this.positionTimer = 0;
    this.records.clear(); this.scope = scope; this.active = null; this.locating = false; this.locationTargetPath = '';
    this.container.replaceChildren(); this.scroll.scrollTop = 0;
  }
  top(record) { return record.surface.getBoundingClientRect().top - this.scroll.getBoundingClientRect().top + this.scroll.scrollTop; }
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
    if (['ready','released'].includes(this.records.get(this.scope.pages.at(-1)?.path)?.status)) {
      const end = document.createElement('p'); end.className = 'book-scope-end'; end.append(this.mark(this.labels.scopeEnd)); desired.push(end);
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
  // A star and a short label: the shared mark of page ends and the scope end.
  mark(text) {
    const mark = document.createElement('span'); mark.className = 'book-page-mark'; mark.innerHTML = PAGE_STAR;
    const label = document.createElement('span'); label.textContent = text; mark.append(label);
    return mark;
  }
  // Each page opens with a heading read aloud and focused after manual loads; its folio is drawn where the page ends.
  label(heading, index, name) {
    heading.textContent = this.text('pageCount',{page:index+1,pages:this.scope.pages.length}) + ' ' + name;
  }
  location() {
    if (!this.active || this.active.status !== 'ready') return null;
    return {path:this.active.item.path,position:{x:this.scroll.scrollLeft,y:Math.max(0,this.scroll.scrollTop-this.top(this.active))}};
  }
  visibleRecord() {
    const top = this.scroll.getBoundingClientRect().top + 2, records = [...this.records.values()];
    const record = records.find(record => {
      const rect = record.element.getBoundingClientRect();
      return rect.top <= top && rect.bottom > top;
    });
    // A page's foot closes it: with the viewport top there, the reader stands at the start of the next loaded page.
    if (record && record.surface.getBoundingClientRect().bottom <= top) return records.find(next => next.index === record.index+1) || record;
    return record;
  }
  viewport() {
    const record = this.visibleRecord() || this.active;
    return record ? {path:record.item.path,position:{x:this.scroll.scrollLeft,y:this.scroll.scrollTop-this.top(record)}} : null;
  }
  keepLocation(location) {
    const record = location && this.records.get(location.path);
    if (record) this.scroll.scrollTop = this.top(record)+location.position.y;
  }
  schedulePosition() {
    if (this.positionTimer) return;
    this.positionTimer = setTimeout(() => { this.positionTimer = 0; this.capture(); this.scheduleMaintenance(); },100);
  }
  capture() {
    if (this.locating) return this.location();
    // A released/loading slot is not successfully displayed content. Keep the last confirmed position.
    const visible = this.visibleRecord();
    if (visible && visible.status !== 'ready') return null;
    const ready = [...this.records.values()].filter(r => r.status === 'ready').sort((a,b) => a.index-b.index);
    if (!ready.length) return null;
    const y = this.scroll.scrollTop + 2;
    const record = visible || ready.find(r => this.top(r) > y) || ready.at(-1);
    this.activate(record);
    const location = this.location();
    this.onActive(record.data,location.position,record.item);
    return location;
  }
  scheduleMaintenance() {
    if (this.maintenanceTimer) return;
    this.maintenanceTimer = setTimeout(() => { this.maintenanceTimer = 0; this.maintainWindow(); },100);
  }
  maintainWindow() {
    if (this.locating || !this.active || document.hidden) return;
    this.capture();
    const current = this.visibleRecord() || this.active;
    this.trim(current.index);
    if (this.automaticRequest) return;
    const top = this.scroll.getBoundingClientRect().top, bottom = top + this.scroll.clientHeight;
    const margin = Math.min(600,this.scroll.clientHeight);
    const nearby = [...this.records.values()].filter(record => {
      const rect = record.element.getBoundingClientRect();
      return Math.abs(record.index-current.index) <= this.windowRadius && rect.bottom >= top-margin && rect.top <= bottom+margin;
    }).sort((a,b) => Math.abs(a.index-current.index)-Math.abs(b.index-current.index));
    // Rehydrate measured slots in either direction before loading new forward content.
    let path = nearby.find(record => record.status === 'released')?.item.path;
    if (!path) {
      for (const record of nearby) {
        const next = this.scope.pages[record.index+1];
        if (record.status === 'ready' && record.index >= current.index && next && record.index+1 <= current.index+this.windowRadius &&
            record.surface.getBoundingClientRect().bottom <= bottom+margin && !this.records.has(next.path)) {
          path = next.path; break;
        }
      }
    }
    if (!path) return;
    const ticket = {path}; this.automaticRequest = ticket;
    this.ensure(path).then(() => this.capture()).catch(() => {
      // Errors stay in their slot. Only the reader's explicit Retry starts another attempt.
    }).finally(() => {
      if (this.automaticRequest !== ticket) return;
      this.automaticRequest = null; this.scheduleMaintenance();
    });
  }
  cancel(record) {
    record.abort?.abort(); clearTimeout(record.timer); clearTimeout(record.location?.timer);
    const error = new DOMException('Navigation superseded','AbortError');
    record.reject?.(error); record.location?.reject(error);
    record.location = null;
  }
  placeholder(record) {
    const surface = document.createElement('div'); surface.className = 'book-page-placeholder';
    surface.style.height = record.height+'px';
    const controls = document.createElement('div'); controls.className = 'book-page-resume';
    const note = document.createElement('span'); note.textContent = this.labels.releasedPage;
    const button = document.createElement('button'); button.type = 'button'; button.dataset.loadPage = record.item.path;
    button.textContent = this.text('reloadPage',{name:record.item.label});
    controls.append(note,button); surface.append(controls); return surface;
  }
  release(record) {
    const location = this.viewport();
    this.cancel(record);
    if (document.activeElement === record.frame || record.element.contains(document.activeElement)) this.scroll.focus({preventScroll:true});
    const surface = this.placeholder(record);
    record.surface.replaceWith(surface); record.surface = surface; record.frame = null;
    record.statusElement.hidden = true; record.element.setAttribute('aria-busy','false');
    record.status = 'released'; record.documentId = ''; record.data = null;
    record.promise = null; record.resolve = null; record.reject = null;
    this.keepLocation(location);
  }
  trim(center, reserve = false) {
    const mounted = () => [...this.records.values()].filter(r => r.frame && r !== this.active && r.item.path !== this.locationTargetPath)
      .sort((a,b) => Math.abs(b.index-center)-Math.abs(a.index-center));
    for (const record of mounted()) if (Math.abs(record.index-center) > this.windowRadius) this.release(record);
    if (reserve) {
      let count = [...this.records.values()].filter(r => r.frame).length;
      for (const record of mounted()) {
        if (count < this.maxDocuments) break;
        this.release(record); count--;
      }
    }
  }
  activate(record) {
    if (this.active === record) return;
    if (this.active?.frame) this.active.frame.removeAttribute('id');
    this.active = record; record.frame.id = 'content';
  }
  resize(record, height) {
    if (!Number.isFinite(height) || height <= 0 || height > 20000000) return;
    if (!this.locating) this.capture();
    const location = this.viewport();
    record.height = Math.ceil(height); record.frame.style.height = record.height+'px';
    if (!this.locating) this.keepLocation(location);
    this.scheduleMaintenance();
  }
  async ensure(path, data) {
    const existing = this.records.get(path);
    if (existing && ['loading','ready'].includes(existing.status)) return existing.promise;
    const index = this.scope.pages.findIndex(item => item.path === path);
    if (index < 0) throw new Error('outside_scope');
    const location = this.viewport();
    const center = this.locating ? this.scope.pages.findIndex(item => item.path === this.locationTargetPath) : (this.visibleRecord() || this.active)?.index ?? index;
    this.trim(center,true);
    const item = this.scope.pages[index], element = existing?.element || document.createElement('section');
    element.className = 'book-page'; element.dataset.path = path; element.setAttribute('aria-busy','true');
    const heading = document.createElement('div'); heading.className = 'book-page-start sr-only'; heading.tabIndex = -1;
    this.label(heading,index,item.label || data?.title || '');
    const status = document.createElement('p'); status.className = 'book-page-state'; status.textContent = this.labels.loading; status.setAttribute('role','status');
    const statusHost = document.createElement('div'); statusHost.className = 'book-page-status'; statusHost.append(status);
    const frame = document.createElement('iframe'); frame.className = 'book-document'; frame.title = item.label || this.labels.original;
    frame.setAttribute('sandbox','allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-downloads');
    frame.referrerPolicy = 'origin'; frame.style.height = (existing?.height || 120)+'px'; frame.style.visibility = 'hidden';
    // The last page has no folio of its own: the scope end closes it.
    const foot = document.createElement('div'); foot.className = 'book-page-foot'; foot.setAttribute('aria-hidden','true');
    foot.append(this.mark(this.text('pageCount',{page:index+1,pages:this.scope.pages.length})));
    element.replaceChildren(heading,statusHost,frame,...(index < this.scope.pages.length-1 ? [foot] : []));
    const record = {item,index,element,heading,statusElement:status,frame,surface:frame,status:'loading',height:existing?.height || 120,
      navigationId:++this.documentSequence,documentId:'',abort:new AbortController(),data};
    record.promise = new Promise((resolve,reject) => { record.resolve=resolve; record.reject=reject; });
    const completion = record.promise;
    // A rejection is also consumed by the caller; cancellation can happen before it awaits.
    record.promise.catch(() => {});
    this.records.set(path,record); this.updateSlots(); this.keepLocation(location);
    try {
      const resolved = record.data || await this.fetchPage(path,record.abort.signal);
      if (record.abort.signal.aborted) throw new DOMException('Navigation superseded','AbortError');
      record.data = resolved;
      frame.title = record.data.title;
      this.label(heading,index,record.data.title);
      record.timer = setTimeout(() => this.fail(record,new Error('frame_timeout')),15000);
      frame.contentWindow.location.replace(this.contentBase + path.split('/').map(encodeURIComponent).join('/'));
    } catch (error) { this.fail(record,error); }
    return completion;
  }
  fail(record,error) {
    if (this.records.get(record.item.path) !== record || record.status !== 'loading') return;
    clearTimeout(record.timer); record.status = 'error'; record.element.setAttribute('aria-busy','false');
    const location = this.viewport();
    const surface = document.createElement('div'); surface.style.height = record.height+'px';
    record.surface.replaceWith(surface); record.surface = surface; record.frame = null;
    this.keepLocation(location);
    record.statusElement.textContent = this.labels.errors[error.message] || this.labels.errors.network;
    record.statusElement.setAttribute('role','alert');
    const retry = document.createElement('button'); retry.type = 'button'; retry.dataset.loadPage = record.item.path; retry.textContent = this.labels.retry;
    record.statusElement.append(' ',retry); record.data = null; record.documentId = ''; record.reject(error);
  }
  async open(data, {anchor='',position=null} = {}) {
    const sequence = ++this.locationSequence; this.locating = true; this.locationTargetPath = data.path;
    try {
      const record = await this.ensure(data.path,data);
      if (sequence !== this.locationSequence) throw new DOMException('Navigation superseded','AbortError');
      const result = await new Promise((resolve,reject) => {
        clearTimeout(record.location?.timer);
        record.location?.reject(new DOMException('Navigation superseded','AbortError'));
        const timer = setTimeout(() => { if (record.location?.sequence === sequence) { record.location = null; reject(new Error('frame_timeout')); } },15000);
        record.location = {sequence,resolve,reject,timer};
        this.send(record,'locate',{requestId:sequence,anchor});
      });
      if (sequence !== this.locationSequence) throw new DOMException('Navigation superseded','AbortError');
      this.activate(record);
      const destination = position || result.position;
      this.scroll.scrollTo({left:destination.x,top:this.top(record)+destination.y,behavior:'instant'});
      this.locating = false; this.locationTargetPath = '';
      this.onActive(record.data,this.location().position,record.item);
      this.scheduleMaintenance();
      return result;
    } catch (error) {
      if (sequence === this.locationSequence) { this.locating = false; this.locationTargetPath = ''; }
      throw error;
    }
  }
  send(record,type,detail = {}) {
    if (!record?.documentId || !record.frame) return;
    record.frame.contentWindow.postMessage({channel:'anna-reader',version:2,type,documentId:record.documentId,
      snapshot:this.snapshot,navigationId:record.navigationId,...detail},location.origin);
  }
  theme(value) { for (const record of this.records.values()) this.send(record,'theme',{theme:value}); }
  focus() { this.active?.frame.focus(); this.send(this.active,'focus'); }
  message(event) {
    const data = event.data;
    if (event.origin !== location.origin || !data || data.channel !== 'anna-reader' || data.version !== 2 || data.snapshot !== this.snapshot) return;
    const record = this.records.get(data.path);
    if (!record?.frame || event.source !== record.frame.contentWindow || record.status === 'error') return;
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
      this.updateSlots(); record.resolve(record); this.scheduleMaintenance();
    } else if (data.type === 'size') this.resize(record,data.height);
    else if (data.type === 'located' && data.requestId === record.location?.sequence && annaReaderModel.validPosition(data.position)) {
      this.resize(record,data.height); clearTimeout(record.location.timer); record.location.resolve(data); record.location = null;
    } else if (data.type === 'navigate' && data.route && (typeof data.route.page === 'string' || typeof data.route.entry === 'string')) this.onNavigate(data.route);
    else if (data.type === 'focus-tools') this.onTools();
    else if (data.type === 'search') this.onSearch();
  }
}
