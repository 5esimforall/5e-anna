/* Link hit areas live in an isolated layer: source DOM, typography and layout stay intact. */
(() => {
  'use strict';
  const payload = document.getElementById('anna-bindings');
  if (!payload) return;
  const data = JSON.parse(payload.textContent);
  const resolve = ([path, offset]) => {
    let node = document.body;
    for (const index of path) node = node?.childNodes[index];
    if (!node || node.nodeType !== Node.TEXT_NODE || offset > node.length) return null;
    return [node, offset];
  };
  function range(spec, verify = true) {
    if (!spec) return null;
    const a = resolve(spec.a), b = resolve(spec.b);
    if (!a || !b) return null;
    const r = document.createRange();
    r.setStart(...a); r.setEnd(...b);
    if (verify && r.toString() !== spec.text) return null;
    return r;
  }
  // Resolve synchronously, before deferred source enhancements rearrange tables.
  const definitions = new Map(data.definitions.map(d => [d.id, {...d, body: range(d.body, false), title: range(d.title)}]));
  const references = data.references.map(r => ({id:r.id, range:range(r.range)}));
  const failed = data.definitions.filter(d => d.title && !definitions.get(d.id).title).map(d => d.id);
  if (failed.length) console.warn('anna: definition DOM binding mismatch', failed);
  let root, host, queued = false;
  const labels = {definition:'查看 anna 条目 / Open anna entry', reference:'查看定义 / Open definition', missing:'条目定位已失效 / Entry location unavailable'};
  function navigateFragment() {
    const id = decodeURIComponent(location.hash.slice(1));
    if (!id.startsWith('anna-')) return;
    const d = definitions.get(id.slice(5));
    const r = d?.title || d?.body;
    if (r?.startContainer.isConnected) {
      const rect = r.getBoundingClientRect();
      window.scrollBy(0, rect.top - 20);
    } else console.warn(labels.missing, id);
  }
  function render() {
    queued = false;
    if (!root) return;
    // Keep focused anchors alive during scrolling/resize.
    if (root.activeElement) return;
    root.querySelectorAll('a').forEach(a => a.remove());
    const add = (r, href, label, kind) => {
      if (!r || !r.startContainer.isConnected) return;
      const parent = r.startContainer.parentElement;
      if (!parent || getComputedStyle(parent).visibility === 'hidden') return;
      let first = true;
      for (const box of r.getClientRects()) {
        if (box.width < 1 || box.height < 1) continue;
        const a = document.createElement('a');
        a.href = href; a.setAttribute('aria-label', label); a.dataset.kind = kind;
        if (kind === 'definition') a.target = '_top';
        a.tabIndex = first ? 0 : -1; first = false;
        Object.assign(a.style, {left:`${box.left}px`,top:`${box.top}px`,width:`${box.width}px`,height:`${box.height}px`});
        a.addEventListener('blur', schedule);
        root.append(a);
      }
    };
    // Definitions take precedence over overlapping source references.
    for (const r of references) add(r.range, '/definition/'+encodeURIComponent(r.id), labels.reference, 'reference');
    for (const d of definitions.values()) add(d.title, '/anna/entry/'+encodeURIComponent(d.id), labels.definition+': '+d.name, 'definition');
  }
  function schedule() { if (!queued) { queued = true; requestAnimationFrame(render); } }
  function init() {
    host = document.createElement('anna-links');
    host.style.cssText = 'position:fixed!important;inset:0!important;width:0!important;height:0!important;pointer-events:none!important;z-index:2147483000!important;';
    document.documentElement.append(host); root = host.attachShadow({mode:'open'});
    const style = document.createElement('style');
    style.textContent = 'a{position:fixed;display:block;pointer-events:auto;background:transparent;border:0;margin:0;padding:0;cursor:pointer}a:focus-visible{outline:2px solid #7050bf;outline-offset:2px}';
    root.append(style);
    window.addEventListener('scroll', schedule, true);
    window.addEventListener('resize', schedule);
    window.addEventListener('hashchange', () => { navigateFragment(); schedule(); });
    new MutationObserver(schedule).observe(document.body, {subtree:true,childList:true,attributes:true});
    document.fonts?.ready.then(() => { navigateFragment(); schedule(); });
    window.addEventListener('load', () => { navigateFragment(); schedule(); });
    navigateFragment(); schedule();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true}); else init();
})();
