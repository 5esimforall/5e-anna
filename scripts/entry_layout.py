"""Original layout for one entry: a sanitized rendering of the source DOM within the entry's text range.

The reader shows tables, headings and emphasis as the book sets them, instead of flattened text.
Field evidence and resolved references are marked by the same canonical text offsets the
extractor recorded, so the marks point at exactly the words a value was read from.
The linked book preserves source content and layout; this derived view is never written back.
"""
from __future__ import annotations

import html
import json

from bs4 import Comment, Doctype, NavigableString, Tag

from browse import METADATA_FIELDS
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
BLOCK_OUTPUT = {'p','div','h3','h4','h5','table','ul','ol','dl','blockquote','hr'}
SHORT_CELL = 12
# Readers place an entry of these types by one field before reading its text (a spell by its level, cantrip
# through 9th), so that field leads the notes wherever its evidence lies: in the passage or a spell list.
LEADING_NOTES = {'spell':('spell_level',)}
# A block's own line breaks separate paragraphs; consecutive label lines form a parameter card.
LINE_BLOCKS = {'p','div','section','center'}
WRAPPERS = {'span','font','a'}
BOLD = {'strong','b'}
LABEL_LENGTH = 12
MIN_PARAMETERS = 2
WIDE_VALUE = 24
DROPPED = {'img','svg','video','audio','iframe','object','embed','input','button','select','textarea','form','map','area'}


def attribute(value):
    return html.escape(str(value), quote=True)


def render(doc, start, end, layers):
    """Serialize the DOM intersecting [start, end); layers are (a, b, kind, payload) text ranges."""
    spans = {id(node):(a,b) for a,b,node in doc.text_nodes}
    out = []
    omitted = set()

    def text(node):
        a,b = spans.get(id(node),(0,0))
        lo,hi = max(a,start),min(b,end)
        if hi<=lo:return
        cuts = sorted({lo,hi}|{x for la,lb,_,_ in layers for x in (la,lb) if lo<x<hi}|{x for o in omitted for x in (o,o+1) if lo<x<hi})
        for x,y in zip(cuts,cuts[1:]):
            if x in omitted and y==x+1:continue
            segment = html.escape(doc.text[x:y])
            covering = [(kind,payload) for la,lb,kind,payload in layers if la<=x and y<=lb]
            ref = next((payload for kind,payload in covering if kind=='ref'),None)
            notes = sorted({payload['field'] for kind,payload in covering if kind=='note'})
            if ref:segment = f'<button type="button" class="ref" data-entry="{attribute(ref)}">{segment}</button>'
            if notes:
                inferred = all(payload['status']!='原文明示' for kind,payload in covering if kind=='note')
                segment = f'<mark class="note{" inferred" if inferred else ""}" data-notes="{attribute(" ".join(notes))}">{segment}</mark>'
            out.append(segment)

    def extent(nodes):
        """Text range of a run of sibling nodes; empty when none of it is text."""
        ranges = [spans.get(id(n)) if isinstance(n,NavigableString) else doc.ranges.get(id(n)) for n in nodes]
        ranges = [r for r in ranges if r and r[1]>r[0]]
        return (min(r[0] for r in ranges),max(r[1] for r in ranges)) if ranges else (0,0)

    def visible(nodes):
        a,b = extent(nodes)
        return max(a,start)<min(b,end) and doc.text[max(a,start):min(b,end)].strip()

    def meaningful(nodes):
        return next((n for n in nodes if not (isinstance(n,NavigableString) and not str(n).strip())),None)

    def label(line):
        """A line that opens with bold text ending in a colon is a label and its value (施法时间：动作).
        Returns the bold tag, the colon's offset, and the line's top-level node holding the label."""
        top = meaningful(line);node = top
        while isinstance(node,Tag) and node.name in WRAPPERS:node = meaningful(node.children)
        if not (isinstance(node,Tag) and node.name in BOLD):return None
        a,b = doc.ranges.get(id(node),(0,0));words = doc.text[a:b].rstrip()
        if not (words[-1:] in ('：',':') and 2<=len(words[:-1].strip())<=LABEL_LENGTH):return None
        return node,a+len(words)-1,top

    def emit_line(line):
        # A line holding its own blocks (a table, a list) cannot sit inside a paragraph.
        tag = 'div' if any(isinstance(n,Tag) and TAGS.get(n.name) in BLOCK_OUTPUT for n in line) else 'p'
        out.append(f'<{tag}>')
        for child in line:walk(child)
        out.append(f'</{tag}>')

    def emit_parameters(run):
        out.append('<dl class="parameters">')
        for line in run:
            bold,colon,top = label(line)
            # The card pairs each label with its value, so the label's trailing colon is not repeated.
            omitted.add(colon)
            # The value is everything after the label: its siblings inside any wrapper, then the rest of the line.
            after,node = [],bold
            while node is not top:after += list(node.next_siblings);node = node.parent
            value = after+line[line.index(top)+1:]
            a,b = extent(value)
            # A long value (an equipment choice, a skill list) takes the card's full width.
            wide = len(doc.text[max(a,start):min(b,end)].strip())>WIDE_VALUE
            out.append('<div class="wide"><dt>' if wide else '<div><dt>')
            for child in bold.children:walk(child)
            out.append('</dt><dd>')
            for child in value:walk(child)
            out.append('</dd></div>')
        out.append('</dl>')

    def lines(node):
        """A block's own <br>s split it into lines; each line reads as a paragraph."""
        split = [[]]
        for child in node.children:
            if isinstance(child,Tag) and child.name=='br':split.append([])
            else:split[-1].append(child)
        shown = [line for line in split if visible(line)]
        index = 0
        while index<len(shown):
            run = []
            while index<len(shown) and label(shown[index]):run.append(shown[index]);index += 1
            if len(run)>=MIN_PARAMETERS:emit_parameters(run);continue
            for line in run:emit_line(line)
            if index<len(shown):emit_line(shown[index]);index += 1

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
        if node.name in LINE_BLOCKS and any(isinstance(c,Tag) and c.name=='br' for c in node.children):
            out.append('<div>');lines(node);out.append('</div>')
            return
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


def text_start(entry):
    """The reader header already shows the parsed name, so an entry's text starts after its title line."""
    return entry['title_end'] if entry['start']<entry.get('title_end',entry['start'])<entry['end'] else entry['start']


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
        body_start = text_start(e)
        facts = []
        for field,value,evidence,rule,status in db.execute('SELECT field,value,evidence,rule,status FROM facts WHERE entry_id=? ORDER BY id',(entry_id,)):
            if field in METADATA_FIELDS:continue
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
    # A type's leading notes come first; the rest follow reading order, each at its first located span,
    # and fields read elsewhere come last.
    lead = LEADING_NOTES.get(e['type'],())
    notes = {}
    for f in facts:
        note = notes.setdefault(f['field'],{'field':f['field'],'status':f['status'],'located':False,'position':None})
        if f['status']=='原文明示':note['status']='原文明示'
        if f['span']:
            note['located'] = True
            note['position'] = f['span'][0] if note['position'] is None else min(note['position'],f['span'][0])
    rank = lambda field:lead.index(field) if field in lead else len(lead)
    ordered = sorted(notes.values(),key=lambda n:(rank(n['field']),n['position'] is None,n['position'] or 0,n['field']))
    return {'status':'ok','path':path,'html':body,'notes':[{k:n[k] for k in ('field','status','located')} for n in ordered]}
