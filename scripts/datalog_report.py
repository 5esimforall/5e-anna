"""Small offline trace report; all values are escaped and links come from bound sources."""
from html import escape
import json


def render_report(manifest, mapping, results, explanations, scope, labels, lang):
    def text(value):
        return escape(str(value), quote=True)

    def atom(node):
        if node.get('kind') == 'engine_condition':
            return text(labels[node['condition_kind']])
        return text(node.get('axiom', node.get('premises', '')))

    def tree(node):
        children = node.get('children', [])
        links = ''.join(' <a href="#rule-' + text(o['rule_id']) + '">' + text(o['rule_id']) + '</a>'
                        for o in node.get('origins', []) if o['kind'] == 'rule')
        if not children:
            return '<li><code>' + atom(node) + '</code>' + links + '</li>'
        return '<li><details open><summary><code>' + atom(node) + '</code>' + links + '</summary><ul>' + ''.join(tree(c) for c in children) + '</ul></details></li>'

    def source_refs(ids):
        return ' '.join('<a href="#source-' + text(s) + '">' + text(s) + '</a>' for s in ids)

    chunks = ['<!doctype html><html lang="' + text(lang) + '"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
              '<title>' + text(labels['title']) + '</title>',
              '<style>body{font:16px/1.6 system-ui;max-width:76rem;margin:2rem auto;padding:0 1rem;color:#17212b;background:#fafafa}a{color:#174fa0}a:focus,summary:focus{outline:3px solid #d58000}section,article{border-top:1px solid #ccc;padding:1rem 0}pre,code{white-space:pre-wrap;overflow-wrap:anywhere}ul{padding-left:1.4rem}blockquote{margin:1rem 0;padding:1rem;background:#edf0f3;white-space:pre-wrap}summary{cursor:pointer}nav{display:flex;gap:1rem;flex-wrap:wrap}:target{background:#fff4ce}</style>',
              '<h1>' + text(labels['title']) + '</h1><p>' + text(labels['review']) + '</p>',
              '<nav>' + ''.join('<a href="#' + k + '">' + text(labels[k]) + '</a>' for k in ('explanations', 'rules', 'sources', 'scope')) + '</nav>',
              '<pre>' + text(json.dumps(manifest['counts'], ensure_ascii=False, indent=2)) + '</pre>',
              '<p>' + text(labels['snapshot']) + ': <code>' + text(manifest['snapshot']) + '</code></p>',
              '<p>' + text(labels['proof_note']) + '</p><section id="explanations"><h2>' + text(labels['explanations']) + '</h2>']
    for item in explanations:
        chunks += ['<article id="proof-' + text(item['id']) + '"><h3>' + text(', '.join(item['case_ids'])) + '</h3>',
                   '<p>' + source_refs(item['source_ids']) + '</p><ul>' + tree(item['proof']) + '</ul></article>']
    chunks += ['</section><section id="rules"><h2>' + text(labels['rules']) + '</h2>']
    for rid, rule in mapping['rules'].items():
        chunks += ['<article id="rule-' + text(rid) + '"><h3>' + text(rid) + '</h3><p>' + text(rule['kind']) + ' · ' + text(rule['review_status']) + '</p>',
                   '<pre>' + text(rule['clause']) + '</pre><p>' + source_refs(sorted({s for refs in rule['sources'].values() for s in refs})) + '</p></article>']
    chunks += ['</section><section id="sources"><h2>' + text(labels['sources']) + '</h2>']
    for sid, source in mapping['sources'].items():
        attached = [rid for rid, rule in mapping['rules'].items() if any(sid in refs for refs in rule['sources'].values())]
        # Claims are also a reverse edge from the original text to its executable tables.
        claims = [o for origins in mapping['fact_origins'].values() for o in origins if o['kind'] == 'claim' and sid in o['sources']]
        chunks += ['<article id="source-' + text(sid) + '"><h3>' + text(sid) + '</h3><p>' + text(source['edition']) + ' · ' + text(source['path']) + ' · ' + text(source['span']) + '</p>',
                   '<p><a href="' + text(source['links']['evidence']) + '">' + text(labels['evidence']) + '</a> · <a href="' + text(source['links']['reader']) + '">' + text(labels['reader']) + '</a></p>',
                   '<details><summary>' + text(labels['quote']) + '</summary><blockquote>' + text(source['quote']) + '</blockquote></details>',
                   '<p>' + ' '.join('<a href="#rule-' + text(r) + '">' + text(r) + '</a>' for r in attached) + '</p>',
                   '<p>' + text(labels['claims']) + ': ' + text(', '.join(o['claim_id'] for o in claims)) + '</p></article>']
    chunks += ['</section><section id="scope"><h2>' + text(labels['scope']) + '</h2><pre>' + text(json.dumps(scope, ensure_ascii=False, indent=2)) + '</pre></section></html>']
    return '\n'.join(chunks)
