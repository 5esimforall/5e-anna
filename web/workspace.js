/* State the two companions share. Anna and Stella each remember a few places you visited with them, and both
   know the topic: the entry the latest of them showed you. Every tab of this site reads and writes one record,
   bound to the build snapshot; per-tab details such as back and forward stay in each page's history state. */
const annaWorkspace = (() => {
  const key = 'anna.workspace', resumeKey = 'anna.resume', version = 1, limit = 5;
  const text = value => typeof value === 'string' ? value : '';
  const time = value => Number.isFinite(value) && value > 0 ? value : 0;
  const position = value => value && Number.isFinite(value.x) && Number.isFinite(value.y) && value.x >= 0 && value.y >= 0;
  /* A query memory is one set of conditions; opening entries and scrolling within them update it in place.
     A query becomes worth returning to once an entry was opened in it; until then refining it replaces it. */
  function queryMemory(value) {
    if (!value || typeof value.query !== 'string' || value.query.length > 4000) return null;
    return {query:value.query, entry:text(value.entry), entryName:text(value.entryName), title:text(value.title),
      opened:value.opened === true || !!text(value.entry), ui:value.ui && typeof value.ui === 'object' ? value.ui : null, at:time(value.at)};
  }
  // A book memory is one reading scope and the place reached within it; its shape is Stella's saved position.
  function bookMemory(value) {
    if (!value || typeof value.page !== 'string' || typeof value.root !== 'string' || typeof value.node !== 'string' || !position(value.position)) return null;
    return {version:2, snapshot:text(value.snapshot), root:value.root, page:value.page, node:value.node,
      position:{x:value.position.x, y:value.position.y}, label:text(value.label), scopeLabel:text(value.scopeLabel), at:time(value.at)};
  }
  function topic(value) {
    if (!value || typeof value.entry !== 'string' || !value.entry || !['query','book'].includes(value.from)) return null;
    return {entry:value.entry, name:text(value.name), from:value.from, at:time(value.at)};
  }
  const list = (values, read) => (Array.isArray(values) ? values : []).map(read).filter(Boolean).slice(0, limit);
  /* Reads a stored record for this snapshot. Places in the book and the topic name build-specific identities,
     so another snapshot keeps only the query conditions; a malformed record reads as an empty workspace. */
  function normalize(raw, snapshot) {
    const empty = {version, snapshot, topic:null, query:[], book:[]};
    if (!raw || raw.version !== version || typeof raw.snapshot !== 'string') return empty;
    const query = list(raw.query, queryMemory);
    if (raw.snapshot !== snapshot)
      return {...empty, query:query.map(memory => ({...memory, entry:'', entryName:'', ui:null}))};
    return {version, snapshot, topic:topic(raw.topic), query, book:list(raw.book, bookMemory).map(memory => ({...memory, snapshot}))};
  }
  // The newest memory comes first; returning to a remembered place moves it to the front instead of adding one.
  function remember(memories, memory, same) {
    return [memory, ...memories.filter(other => !same(other, memory))].slice(0, limit);
  }
  // A scope is its directory node; a page outside the contents is its own scope.
  const memoryId = {query:memory => memory.query, book:memory => memory.root || memory.page};
  const sameQuery = (a, b) => a.query === b.query, sameScope = (a, b) => memoryId.book(a) === memoryId.book(b);
  function rememberQuery(state, memory) {
    const value = queryMemory(memory);
    if (!value) return state;
    value.opened ||= !!state.query.find(other => sameQuery(other, value))?.opened;
    const [front] = state.query, passing = front && !front.opened && !sameQuery(front, value) ? front : null;
    return {...state, query:remember(state.query.filter(other => other !== passing), value, sameQuery)};
  }
  function rememberBook(state, memory) {
    const value = bookMemory({...memory, snapshot:state.snapshot});
    return value ? {...state, book:remember(state.book, value, sameScope)} : state;
  }
  // Showing the topic again keeps who brought it up and when, so following it back is not a new topic.
  function setTopic(state, value) {
    const next = topic(value);
    return next && next.entry === state.topic?.entry ? state : {...state, topic:next};
  }
  function clearTopic(state, entry) { return state.topic?.entry === entry ? {...state, topic:null} : state; }
  /* What turning to a companion offers first: the topic another companion showed you since your last visit
     with her, otherwise the place you left her, otherwise her home. */
  function offer(state, companion) {
    const memory = state[companion][0] || null, latest = state.topic;
    const shown = latest && latest.from !== companion && latest.at > (memory?.at || 0) &&
      !(companion === 'query' && memory?.entry === latest.entry);
    return shown ? {kind:'topic', topic:latest, memory} : memory ? {kind:'resume', memory} : {kind:'home'};
  }
  // Storage access stays here so the rules above remain pure and testable without a browser.
  let storage = null;
  const stored = () => storage || (typeof annaKit !== 'undefined' ? annaKit.stored : null);
  function read(snapshot) {
    let raw = null;
    try { raw = JSON.parse(stored()?.get(key) || 'null'); } catch { raw = null; }
    return normalize(raw, snapshot);
  }
  // Listeners hear this tab's updates directly and other tabs' through the storage event.
  const listeners = new Set();
  let watching = false;
  function update(snapshot, change) {
    const next = change(read(snapshot));
    stored()?.set(key, JSON.stringify(next));
    listeners.forEach(listener => listener());
    return next;
  }
  function subscribe(listener) {
    listeners.add(listener);
    if (!watching && stored()?.watch) { stored().watch(key, () => listeners.forEach(each => each())); watching = true; }
  }
  /* Choosing a remembered place asks the page it opens to restore that place. The request lives in this tab's
     session storage, so it survives the navigation and nothing else: shared links and new tabs start fresh. */
  function requestResume(companion, id) {
    try { sessionStorage.setItem(resumeKey, JSON.stringify({companion, id})); } catch { /* The page opens without restoring. */ }
  }
  function takeResume(companion) {
    let request = null;
    try { request = JSON.parse(sessionStorage.getItem(resumeKey) || 'null'); sessionStorage.removeItem(resumeKey); } catch { return null; }
    return request?.companion === companion && typeof request.id === 'string' ? request.id : null;
  }
  return {key, limit, normalize, rememberQuery, rememberBook, setTopic, clearTopic, offer, read, update, subscribe, requestResume, takeResume, memoryId,
    useStorage(value) { storage = value; }};
})();
if (typeof module !== 'undefined') module.exports = annaWorkspace;
