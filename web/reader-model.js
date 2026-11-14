/* Directory identity defines a reading scope; source paths identify its documents. */
const annaReaderModel = (() => {
  function index(rows) {
    const nodes = new Map(), children = new Map(), pageNodes = new Map();
    for (const node of rows) {
      if (!node.id || nodes.has(node.id) || (node.parent_id && !nodes.has(node.parent_id))) throw new Error('invalid_location');
      nodes.set(node.id,node);
      if (!children.has(node.parent_id)) children.set(node.parent_id,[]);
      children.get(node.parent_id).push(node);
      if (node.status === 'present') {
        if (!pageNodes.has(node.path)) pageNodes.set(node.path,[]);
        pageNodes.get(node.path).push(node);
      }
    }
    function scope(root, path = '') {
      if (root && !nodes.has(root)) throw new Error('invalid_location');
      const pages = [], byPath = new Map(), memberIds = new Set();
      function visit(node) {
        memberIds.add(node.id);
        if (node.status === 'present') {
          let document = byPath.get(node.path);
          if (!document) {
            document = {path:node.path, label:node.label, node:node.id, fragment:node.fragment, nodes:[]};
            byPath.set(node.path,document); pages.push(document);
          }
          document.nodes.push(node);
        }
        for (const child of children.get(node.id) || []) visit(child);
      }
      if (root) visit(nodes.get(root));
      else if (path) {
        const document = {path, label:'', node:'', fragment:'', nodes:[]};
        pages.push(document); byPath.set(path,document);
      }
      return {root, pages, byPath, memberIds};
    }
    return {nodes, children, pageNodes, scope};
  }
  function validPosition(value) {
    return value && Number.isFinite(value.x) && Number.isFinite(value.y) && value.x >= 0 && value.y >= 0;
  }
  function remembered(value, snapshot, model) {
    if (value?.version !== 2 || value.snapshot !== snapshot || typeof value.page !== 'string' ||
        !value.page.startsWith('topics/') || value.page.split('/').some(p => p === '..' || p === '.') ||
        typeof value.root !== 'string' || typeof value.node !== 'string' || !validPosition(value.position)) return null;
    try {
      const scope = model.scope(value.root,value.page);
      if (!scope.byPath.has(value.page) || (value.node && (!scope.memberIds.has(value.node) || model.nodes.get(value.node)?.path !== value.page))) return null;
    } catch { return null; }
    return value;
  }
  return {index, validPosition, remembered};
})();
if (typeof module !== 'undefined') module.exports = annaReaderModel;
