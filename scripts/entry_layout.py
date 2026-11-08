"""Original layout for one entry: a sanitized rendering of the source DOM within the entry's text range.

The reader shows tables, headings and emphasis as the book sets them, instead of flattened text.
Field evidence and resolved references are marked by the same canonical text offsets the
extractor recorded, so the marks point at exactly the words a value was read from.
The linked book stays the byte-preserving original; this is a derived view and never written back.
"""
from __future__ import annotations

import html
import json

from bs4 import Comment, Doctype, NavigableString, Tag

from build import read_page
from html_extract import IGNORED, Document

# Source tags map onto a small, inert vocabulary; anything else is unwrapped, never copied.
TAGS = {
    'p':'p', 'div':'div', 'section':'div', 'center':'div', 'span':'span', 'font':'span', 'a':'span',
    'br':'br', 'hr':'hr', 'h1':'h3', 'h2':'h3', 'h3':'h3', 'h4':'h4', 'h5':'h5', 'h6':'h5',
    'strong':'strong', 'b':'strong', 'em':'em', 'i':'em', 'u':'u', 's':'s', 'sub':'sub', 'sup':'sup', 'small':'small',
    'table':'table', 'thead':'thead', 'tbody':'tbody', 'tfoot':'tfoot', 'tr':'tr', 'th':'th', 'td':'td', 'caption':'caption',
    'ul':'ul', 'ol':'ol', 'li':'li', 'dl':'dl', 'dt':'dt', 'dd':'dd', 'blockquote':'blockquote',
}
VOID = {'br','hr'}
SHORT_CELL = 12
DROPPED = {'img','svg','video','audio','iframe','object','embed','input','button','select','textarea','form','map','area'}
# These fields name or locate the version; they are not readings of its rules.
NOTE_SKIP = {'type','ruleset','source_id','source_code'}


def attribute(value):
    return html.escape(str(value), quote=True)


def render(doc, start, end, layers):
    """Serialize the DOM intersecting [start, end); layers are (a, b, kind, payload) text ranges."""
    spans = {id(node):(a,b) for a,b,node in doc.text_nodes}
    out = []

    def text(node):
        a,b = spans.get(id(node),(0,0))
        lo,hi = max(a,start),min(b,end)
        if hi<=lo:return
        cuts = sorted({lo,hi}|{x for la,lb,_,_ in layers for x in (la,lb) if lo<x<hi})
        for x,y in zip(cuts,cuts[1:]):
            segment = html.escape(doc.text[x:y])
            covering = [(kind,payload) for la,lb,kind,payload in layers if la<=x and y<=lb]
            ref = next((payload for kind,payload in covering if kind=='ref'),None)
            notes = sorted({payload['field'] for kind,payload in covering if kind=='note'})
            if ref:segment = f'<button type="button" class="ref" data-entry="{attribute(ref)}">{segment}</button>'
            if notes:
                inferred = all(payload['status']!='原文明示' for kind,payload in covering if kind=='note')
                segment = f'<mark class="note{" inferred" if inferred else ""}" data-notes="{attribute(" ".join(notes))}">{segment}</mark>'
            out.append(segment)

    def walk(node):
        if isinstance(node,(Comment,Doctype)):return
        if isinstance(node,NavigableString):text(node);return
        if not isinstance(node,Tag) or node.name in IGNORED or node.name in DROPPED:return
        a,b = doc.ranges.get(id(node),(0,0))
        if node.name=='br':
            if start<a<=end:out.append('<br>')
            return
        inside = (b>start and a<end) or (a==b and start<a<end)
        if not inside:return
        tag = TAGS.get(node.name)
        attrs = ''
        if tag in {'td','th'}:
            for key in ('colspan','rowspan'):
                value = str(node.get(key) or '')
                if value.isdigit() and 1<int(value)<=50:attrs += f' {key}="{int(value)}"'
            # Short cells (scores, modifiers, labels) keep their words whole; long cells wrap as prose.
            if len(doc.text[a:b].strip())<=SHORT_CELL:attrs += ' class="short"'
        if tag:out.append(f'<{tag}{attrs}>')
        for child in node.children:walk(child)
        if tag and tag not in VOID:out.append(f'</{tag}>')

    walk(doc.body)
    return ''.join(out)


def entry_layout(db, entry_id, source_root):
    row = db.execute('SELECT data FROM entries WHERE id=?',(entry_id,)).fetchone()
    if not row:raise LookupError('条目不存在')
    e = json.loads(row[0]);path = e['page_path']
    page = db.execute('SELECT text, sha256 FROM pages WHERE path=?',(path,)).fetchone()
    source = source_root/path
    if not page or not source.is_file():return {'status':'missing','path':path}
    raw,_,sha = read_page(source)
    if sha!=page[1]:return {'status':'stale','path':path}
    doc = Document(path,raw)
    try:
        if doc.text!=page[0]:return {'status':'stale','path':path}
        start,end = e['start'],e['end']
        # The reader header already shows the parsed name; the layout starts after the title line.
        body_start = e['title_end'] if start<e.get('title_end',start)<end else start
        facts = []
        for field,value,evidence,rule,status in db.execute('SELECT field,value,evidence,rule,status FROM facts WHERE entry_id=? ORDER BY id',(entry_id,)):
            if field in NOTE_SKIP:continue
            ev = json.loads(evidence);span = ev.get('span') if ev.get('path')==path else None
            located = bool(span and start<=span[0]<span[1]<=end and doc.text[span[0]:span[1]].strip())
            facts.append({'field':field,'value':json.loads(value),'rule':rule,'status':status,'span':span if located else None})
        layers = [(f['span'][0],f['span'][1],'note',f) for f in facts if f['span']]
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='effective_references'").fetchone():
            for (data,) in db.execute('SELECT data FROM effective_references WHERE page_path=? AND start>=? AND end<=? ORDER BY start,id',(path,body_start,end)):
                r = json.loads(data)
                if r.get('target_id') and r['target_id']!=entry_id and r['end']>r['start']:layers.append((r['start'],r['end'],'ref',r['target_id']))
        body = render(doc,body_start,end,layers)
    finally:
        doc.close()
    if not body.strip():return {'status':'empty','path':path}
    # Notes follow reading order: each at its first located span; fields read elsewhere come last.
    notes = {}
    for f in facts:
        note = notes.setdefault(f['field'],{'field':f['field'],'status':f['status'],'located':False,'position':None})
        if f['status']=='原文明示':note['status']='原文明示'
        if f['span']:
            note['located'] = True
            note['position'] = f['span'][0] if note['position'] is None else min(note['position'],f['span'][0])
    ordered = sorted(notes.values(),key=lambda n:(n['position'] is None,n['position'] or 0,n['field']))
    return {'status':'ok','path':path,'html':body,'notes':[{k:n[k] for k in ('field','status','located')} for n in ordered]}
