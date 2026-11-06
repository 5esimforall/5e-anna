/* Link hit areas live in an isolated layer: source DOM, typography and layout stay intact. */
(() => {
  'use strict';
  const payload = document.getElementById('anna-bindings');
  if (!payload) return;
  const data = JSON.parse(payload.textContent);
  const labels = annaLinkLabels[document.documentElement.lang.startsWith('en') ? 'en' : 'zh'];
  const resolve = ([path, offset]) => {
    let node = document.body;
    for (const index of path) node = node?.childNodes[index];
    if (!node || node.nodeType !== Node.TEXT_NODE || offset > node.length) return null;
    return [node, offset];
  };
  function range(binding) {
    if (!binding || !binding.a[0].isConnected || !binding.b[0].isConnected) return null;
    const r = document.createRange();
    r.setStart(...binding.a); r.setEnd(...binding.b);
    return r;
  }
  function bind(spec, verify = true) {
    if (!spec) return null;
    const a = resolve(spec.a), b = resolve(spec.b);
    if (!a || !b) return null;
    const binding = {a, b, order:[...spec.a[0], spec.a[1]]};
    if (verify && range(binding).toString() !== spec.text) return null;
    return binding;
  }
  // Retain text nodes, not live Ranges: source enhancements can move table nodes.
  const definitions = new Map(data.definitions.map(d => [d.id, {...d, body: bind(d.body, false), title: bind(d.title)}]));
  const references = data.references.map(r => ({id:r.id, binding:bind(r.range), text:r.range.text}));
  const failed = data.definitions.filter(d => d.title && !definitions.get(d.id).title).map(d => d.id);
  if (failed.length) console.warn('anna: definition DOM binding mismatch', failed);
  const failedReferences = references.filter(r => !r.binding);
  if (failedReferences.length) console.warn('anna: reference DOM binding mismatch', failedReferences.map(r => r.id));
  let root, queued = false;
  const links = [];
  function navigateFragment() {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
    if (!id.startsWith('anna-')) return;
    const d = definitions.get(id.slice(5));
    const r = range(d?.title || d?.body);
    if (r) window.scrollBy(0, r.getBoundingClientRect().top - 20);
    else console.warn(labels.missing, id);
  }
  function render() {
    queued = false;
    if (!root) return;
    for (const link of links) {
      const r = range(link.binding);
      const parent = r?.startContainer.parentElement;
      const boxes = parent && getComputedStyle(parent).visibility !== 'hidden'
        ? Array.from(r.getClientRects()).filter(b => b.width >= 1 && b.height >= 1) : [];
      boxes.forEach((box, i) => {
        let a = link.anchors[i];
        if (!a) {
          a = document.createElement('a'); link.anchors.push(a);
          a.href = link.href; a.setAttribute('aria-label', link.label); a.dataset.kind = link.kind;
          a.target = link.kind === 'definition' ? '_blank' : '_self';
          a.title = link.label;
          if (link.kind === 'definition') {
            a.rel = 'noopener';
            a.setAttribute('aria-description', labels.newTab);
            a.title += ' · ' + labels.newTab;
          }
          a.style.zIndex = link.kind === 'definition' ? '2' : '1';
          a.tabIndex = i === 0 ? 0 : -1;
          if (i > 0) a.setAttribute('aria-hidden', 'true');
          a.addEventListener('focus', () => {
            const rect = range(link.binding)?.getBoundingClientRect();
            if (rect && (rect.top < 0 || rect.bottom > innerHeight)) window.scrollBy(0, rect.top - 20);
            schedule();
          });
          link.group.append(a);
        }
        Object.assign(a.style, {left:`${box.left}px`,top:`${box.top}px`,width:`${box.width}px`,height:`${box.height}px`});
      });
      while (link.anchors.length > boxes.length) link.anchors.pop().remove();
    }
  }
  function schedule() { if (!queued) { queued = true; requestAnimationFrame(render); } }
  function init() {
    const host = document.createElement('anna-links');
    host.style.cssText = 'position:fixed!important;inset:0!important;width:0!important;height:0!important;pointer-events:none!important;z-index:2147483000!important;';
    document.documentElement.append(host); root = host.attachShadow({mode:'open'});
    const style = document.createElement('style');
    style.textContent = 'a{position:fixed;display:block;pointer-events:auto;background:transparent;border:0;margin:0;padding:0;cursor:pointer}a:focus-visible{outline:2px solid #7050bf;outline-offset:2px}';
    root.append(style);
    for (const r of references) if (r.binding) links.push({binding:r.binding, href:'/definition/'+encodeURIComponent(r.id), label:labels.reference+': '+r.text, kind:'reference', anchors:[]});
    for (const d of definitions.values()) if (d.title) links.push({binding:d.title, href:'/anna/entry/'+encodeURIComponent(d.id), label:labels.definition+': '+d.name, kind:'definition', anchors:[]});
    // Keyboard traversal follows reading order; overlapping definitions still win hit testing.
    links.sort((a,b) => {
      const x=a.binding.order, y=b.binding.order;
      for (let i=0; i<Math.min(x.length,y.length); i++) if (x[i]!==y[i]) return x[i]-y[i];
      return x.length-y.length;
    });
    for (const link of links) { link.group = document.createElement('span'); root.append(link.group); }
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
