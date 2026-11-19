/* Differences between two texts of one entry, marked in the shown version's own layout.
   A homebrew copy of an official item, or two printings of one spell, often differ in a price line or a school;
   reading the shown version with the other version's changes marked in place answers "what changed" without
   setting the two side by side. The comparison is word by word (each CJK character, each Latin word or number);
   when too much differs, marks would bury the text, so the caller is told to compare the fields instead. */
const annaDifferences = (() => {
  // Beyond this share of changed words, or this many edits, the two texts are not marked.
  const SHARE = .4, MAX_EDITS = 1200;

  function tokens(text) {
    const out = [], re = /[\p{Script=Latin}\p{Nd}]+|\S/gu;
    for (let m; (m = re.exec(text));) out.push({text: m[0], start: m.index, end: m.index + m[0].length});
    return out;
  }

  // Myers' shortest edit script from a to b, as runs: `own` (only in a) and `other` (only in b, placed before
  // a[at]). Null when the script needs more than `limit` edits.
  function runs(a, b, limit) {
    let start = 0;
    while (start < a.length && start < b.length && a[start] === b[start]) start++;
    let endA = a.length, endB = b.length;
    while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
    const A = a.slice(start, endA), B = b.slice(start, endB), n = A.length, m = B.length;
    const max = Math.min(n + m, limit), off = max + 1, v = new Int32Array(2 * max + 3), trace = [];
    let found = -1;
    search: for (let d = 0; d <= max; d++) {
      for (let k = -d; k <= d; k += 2) {
        let x = k === -d || (k !== d && v[off + k - 1] < v[off + k + 1]) ? v[off + k + 1] : v[off + k - 1] + 1, y = x - k;
        while (x < n && y < m && A[x] === B[y]) { x++; y++; }
        v[off + k] = x;
        if (x >= n && y >= m) { found = d; break search; }
      }
      trace.push(v.slice(off - d - 1, off + d + 2));
    }
    if (found < 0) return null;
    const steps = [];
    let x = n, y = m;
    for (let d = found; d > 0; d--) {
      const before = trace[d - 1], at = k => before[k + d], k = x - y;
      const down = k === -d || (k !== d && at(k - 1) < at(k + 1));
      const prevK = down ? k + 1 : k - 1, prevX = at(prevK), prevY = prevX - prevK;
      while (x > prevX && y > prevY) { x--; y--; }
      if (down) steps.push({kind: 'other', a: x, b: y - 1}); else steps.push({kind: 'own', a: x - 1, b: y});
      x = prevX; y = prevY;
    }
    steps.reverse();
    const out = [];
    for (const s of steps) {
      const last = out[out.length - 1];
      if (s.kind === 'own') {
        if (last?.kind === 'own' && last.to === s.a + start) last.to++;
        else out.push({kind: 'own', from: s.a + start, to: s.a + start + 1});
      } else if (last?.kind === 'other' && last.at === s.a + start && last.to === s.b + start) last.to++;
      else out.push({kind: 'other', at: s.a + start, from: s.b + start, to: s.b + start + 1});
    }
    return out;
  }

  function compare(a, b) {
    const total = a.length + b.length;
    if (!total) return [];
    return runs(a, b, Math.min(Math.floor(total * SHARE), MAX_EDITS));
  }

  // The text of a layout as the reader sees it, with each text node's span; controls and earlier marks of the
  // other version's text are not part of it.
  function textOf(root) {
    const nodes = [];
    let text = '';
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: node => node.parentElement?.closest('button,script,style,del.diff-other') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    for (let node; (node = walker.nextNode());) {
      nodes.push({node, start: text.length, end: text.length + node.data.length});
      text += node.data;
    }
    return {text, nodes};
  }

  function wrap(root, start, end) {
    const {nodes} = textOf(root);
    for (const span of nodes.reverse()) {
      const from = Math.max(start, span.start) - span.start, to = Math.min(end, span.end) - span.start;
      if (to <= from || !span.node.data.slice(from, to).trim()) continue;
      let target = span.node;
      if (to < target.data.length) target.splitText(to);
      if (from > 0) target = target.splitText(from);
      const mark = document.createElement('ins');
      mark.className = 'diff-own';
      target.replaceWith(mark);
      mark.append(target);
    }
  }

  // Outside the shown version's own marks, so that another version's words never read as part of them.
  function outside(node, edge) {
    while (node.parentElement?.matches('ins.diff-own') && (edge === 'first' ? !node.previousSibling : !node.nextSibling)) node = node.parentElement;
    return node;
  }

  function insert(root, at, text) {
    const {nodes} = textOf(root);
    const mark = document.createElement('del');
    mark.className = 'diff-other';
    mark.textContent = text;
    const starting = nodes.find(s => s.start === at && s.node.data.trim());
    if (starting) { outside(starting.node, 'first').before(mark); return; }
    const inside = nodes.find(s => s.start < at && at < s.end);
    if (inside) { inside.node.splitText(at - inside.start).before(mark); return; }
    const ending = [...nodes].reverse().find(s => s.end === at) || nodes[nodes.length - 1];
    if (ending) outside(ending.node, 'last').after(mark); else root.append(mark);
  }

  // Mark in `root` (the shown layout) what only it has and, struck through, what only `other` has.
  function mark(root, other) {
    const shown = textOf(root), theirs = textOf(other);
    const a = tokens(shown.text), b = tokens(theirs.text);
    const found = compare(a.map(t => t.text), b.map(t => t.text));
    if (!found) return {status: 'too_different'};
    if (!found.length) return {status: 'same'};
    const edits = found.map(r => r.kind === 'own'
      ? {kind: 'own', at: a[r.from].start, end: a[r.to - 1].end}
      : {kind: 'other', at: r.at < a.length ? a[r.at].start : a.length ? a[a.length - 1].end : 0, text: theirs.text.slice(b[r.from].start, b[r.to - 1].end)});
    // From the end, so earlier positions hold; at one position the other version's words go before this one's.
    edits.sort((x, y) => y.at - x.at || (x.kind === 'own' ? -1 : 1));
    for (const e of edits) e.kind === 'own' ? wrap(root, e.at, e.end) : insert(root, e.at, e.text);
    return {status: 'marked', own: found.filter(r => r.kind === 'own').length, other: found.filter(r => r.kind === 'other').length};
  }

  return {tokens, compare, mark};
})();
if (typeof module !== 'undefined') module.exports = annaDifferences;
