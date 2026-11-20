'use strict';
/* How anna's pages move. A change of state is complete the moment it happens: the DOM, the focus, the address and
   what assistive technology reads change at once. Motion only shows where the change came from and where it went,
   in a fraction of a second, and never makes the reader wait for it:
   - content that takes another's place settles in from the side it comes from, or fades in where it has no side;
   - siblings drawn again keep their identity and slide from where they stood; new ones fade in;
   - a box that a panel resizes moves its edges with the panel, and what it holds is set again at every size on the
     way, so nothing inside jumps to its final place first;
   - a box that grows uncovers its new area from the edges it had;
   - a restyled page (theme, row density) fades from its old look into the new one.
   Layers, popups and panels that appear or leave are styled in style.css under `[data-motion]` with the same
   durations and curves (--anna-enter, --anna-exit, --anna-move). A page restoring itself on load shows its state in
   place: motion starts once the page settles or with the reader's first action, and never when the reader asks for
   reduced motion. */
const annaMotion = (() => {
  const root = document.documentElement;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  let settled = false;
  // The page has shown the state it restored; what changes from now on moves.
  function settle() {
    if (settled) return;
    settled = true; root.dataset.motion = '';
  }
  for (const type of ['pointerdown', 'keydown']) window.addEventListener(type, settle, {capture:true, once:true, passive:true});
  const moving = () => settled && !reduced.matches;
  const token = name => getComputedStyle(root).getPropertyValue('--anna-' + name).trim();
  const timing = (duration, easing = 'ease') => ({duration:parseFloat(token(duration)) || 0, easing:token(easing) || 'ease'});
  // One motion per element: a newer one replaces the one still running.
  const running = new WeakMap();
  function play(element, keyframes, options) {
    running.get(element)?.cancel();
    const animation = element.animate(keyframes, options);
    running.set(element, animation);
    animation.finished.then(() => { if (running.get(element) === animation) running.delete(element); }, () => {});
  }
  const SIDES = {below:[0, 1], above:[0, -1], right:[1, 0], left:[-1, 0]};
  const seen = box => box.bottom > 0 && box.top < innerHeight && box.right > 0 && box.left < innerWidth && box.width + box.height > 0;
  const offset = (from, to) => [from.left - to.left, from.top - to.top];
  const moved = ([x, y]) => Math.abs(x) >= 1 || Math.abs(y) >= 1;
  const glide = ([x, y]) => ({translate:[`${x}px ${y}px`, '0 0']});
  // How far each edge of `now` lies inside the box it had (`before`): top, right, bottom, left.
  const insets = (before, now) => [before.top - now.top, now.right - before.right, now.bottom - before.bottom, before.left - now.left];
  const SIDES_CSS = ['Top', 'Right', 'Bottom', 'Left'];

  // Content that takes another's place settles in: from `from` (below, above, left or right), else in place.
  function arrive(element, from = null, {distance = 8, opacity = 0} = {}) {
    if (!element || !moving() || !seen(element.getBoundingClientRect())) return;
    const [x, y] = SIDES[from] || [0, 0];
    play(element, {opacity:[opacity, 1], translate:[`${x * distance}px ${y * distance}px`, '0 0']}, timing('enter'));
  }
  /* A box that a layout change resized takes its edges from the box it had (`before`) to its new one. Its real size
     changes on the way, so a centred title, a control on its far edge or a wrapped line moves continuously with the
     edge. A box sized by its area (a stretched grid item) moves its edges through its margins; a fixed box through
     its position and size. */
  function reshape(element, before) {
    if (!element || !before || !moving()) return;
    const now = element.getBoundingClientRect(), edges = insets(before, now);
    if (!edges.some(edge => Math.abs(edge) >= 1)) return;
    const style = getComputedStyle(element);
    if (style.position === 'fixed') {
      const boxes = [before, now];
      play(element, {inset:boxes.map(box => `${box.top}px auto auto ${box.left}px`), width:boxes.map(box => `${box.width}px`),
        height:boxes.map(box => `${box.height}px`), margin:['0px', '0px'], boxSizing:['border-box', 'border-box']}, timing('move'));
      return;
    }
    const keyframes = {};
    SIDES_CSS.forEach((side, i) => {
      if (Math.abs(edges[i]) < 1) return;
      const margin = parseFloat(style['margin' + side]) || 0;
      keyframes['margin' + side] = [`${margin + edges[i]}px`, `${margin}px`];
    });
    play(element, keyframes, timing('move'));
  }
  /* `change` draws the items matching `selector` again; `key` names each item across the two drawings. Items that
     stay slide from where they stood, new ones fade in, and items out of sight are left alone. */
  function rearrange(container, selector, key, change) {
    if (!container || !moving()) { change(); return; }
    const before = new Map();
    for (const item of container.querySelectorAll(selector)) {
      const name = key(item);
      if (name !== null && name !== undefined && !before.has(name)) before.set(name, item.getBoundingClientRect());
    }
    change();
    for (const item of container.querySelectorAll(selector)) {
      const now = item.getBoundingClientRect(), was = before.get(key(item));
      if (!seen(now) && !(was && seen(was))) continue;
      if (!was) play(item, {opacity:[0, 1]}, timing('enter'));
      else if (moved(offset(was, now))) play(item, glide(offset(was, now)), timing('move'));
    }
  }
  // A box that grew uncovers its new area from the edges it had (`before`); a box that shrank has nothing to show.
  function grow(element, before) {
    if (!element || !before || !moving()) return;
    const inset = insets(before, element.getBoundingClientRect()).map(edge => Math.max(0, Math.round(edge)));
    if (inset.some(edge => edge >= 1)) play(element, {clipPath:[`inset(${inset.join('px ')}px)`, 'inset(0px)']}, timing('move'));
  }
  /* A restyled page fades from its old look into the new one. Controls skip their own colour transitions meanwhile,
     so the new look is complete beneath the fade; without view transitions it simply changes. */
  function restyle(change) {
    root.dataset.restyling = '';
    const done = () => delete root.dataset.restyling;
    if (moving() && document.startViewTransition) document.startViewTransition(change).finished.finally(done);
    else { change(); requestAnimationFrame(() => requestAnimationFrame(done)); }
  }
  return {settle, arrive, reshape, rearrange, grow, restyle, get moving() { return moving(); }};
})();
