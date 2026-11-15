"""Snapshot-bound reading model. TOC nodes, documents and definitions stay distinct."""
from datetime import datetime
from hashlib import sha256
import re

from bs4 import BeautifulSoup
from pathlib import PurePosixPath

from anna.source.html import resolve_link
from anna.source.inventory import Toc

SCHEMA = 6

# These are the source reader's content assets, not its navigation/application scripts.
TOPIC_RESOURCES = (
    ('webhelpTopicStyles', 'assets/webhelp-topic.css', 'style'),
    ('webhelpContentEnhanceStyles', 'assets/content-enhance.css', 'style'),
    ('webhelpContentEnhanceScript', 'assets/content-enhance.js', 'script'),
    ('webhelpContrastScript', 'assets/webhelp-contrast.js', 'script'),
)


def source_release(source):
    """Read the printed collection release, not a Git date or a rules edition."""
    path = 'topics/写在前面.html'
    file = source / path
    if not file.is_file():
        return None
    soup = BeautifulSoup(file.read_bytes(), 'html.parser')
    for element in soup(['script', 'style']):
        element.decompose()
    versions = set()
    for heading in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        text = heading.get_text(' ', strip=True)
        for version in re.findall(r'DND\s*五版不全书\s*v([0-9]{8}(?:\.[0-9]+)*)(?![\w.])', text, re.I):
            try:
                datetime.strptime(version[:8], '%Y%m%d')
            except ValueError:
                continue
            versions.add(version)
    if len(versions) != 1:
        return None
    return {'version': versions.pop(), 'path': path}


def content_file(path):
    """Publish source content/resources, never the upstream WebHelp application."""
    parts = PurePosixPath(path).parts
    if not parts or any(p.startswith('.') for p in parts):
        return False
    return parts[0] == 'topics' or path in {asset[1] for asset in TOPIC_RESOURCES}


# WinCHM's generated navigation template has no content behavior. Match the entire
# script after normalizing its two page-specific values; preserve other inline JS.
WINCHM_NAVIGATION = re.sub(r'\s+', '', """
function syn(){
if(parent.nav.tree){
 if(parent.nav.tree.loaded){
  parent.nav.tree.selectNode(NODE);
 }else{
  setTimeout("syn()",500);
}
  }else{
  setTimeout("syn()",500);
  }}
if(parent!=self){
  setTimeout("syn()",100);
}else{
  parent.location.href = HOME;
}
originalOnload = window.onload;
if(originalOnload==null){
window.onload = function(){parent.contentLoaded = true;};
}else{
window.onload = function(){originalOnload();parent.contentLoaded = true;};
}
""")


def is_winchm_navigation(script):
    script = re.sub(r'parent\.nav\.tree\.selectNode\(\d+\)', 'parent.nav.tree.selectNode(NODE)', script)
    script = re.sub(r'parent\.location\.href\s*=\s*"[^"\r\n]*index\.htm\?page=[^"\r\n]*"',
                    'parent.location.href = HOME', script)
    return re.sub(r'\s+', '', script) == WINCHM_NAVIGATION


def strip_reader_scripts(raw, page):
    """Retire source-shell script loaders without shifting DOM range node indexes."""
    def replace(match):
        tag = BeautifulSoup(match.group(0).decode('ascii', errors='replace'), 'html.parser').script
        target = resolve_link(page, tag.get('src', '')) if tag else None
        if tag and ((target and tag.get('src') and not content_file(target[0])) or
                    (not tag.get('src') and is_winchm_navigation(tag.get_text()))):
            # One inert node replaces one script node; source text and sibling offsets stay intact.
            return b'<!-- source shell script removed -->'
        return match.group(0)
    return re.sub(rb'<script\b[^>]*>.*?</script\s*>', replace, raw, flags=re.I | re.S)



def build_navigation(source, pages):
    toc = Toc(source)
    path = source / 'webhelpcontents.htm'
    if path.is_file():
        toc.feed(path.read_text('utf-8-sig'))
    nodes = []
    for row in toc.rows:
        target = resolve_link('webhelpcontents.htm', row['href'])
        page, fragment = target if target else ('', '')
        status = 'present' if page in pages else 'placeholder' if row['href'].startswith('#') else 'missing'
        nodes.append({'id': row['node_id'], 'parent_id': row['parent_id'], 'label': row['label'],
                      'path': page if status != 'placeholder' else '', 'fragment': fragment, 'status': status})
    # Mirrors without a TOC still expose all documents, without inventing book membership.
    if not nodes:
        nodes = [{'id': 'page_' + sha256(p.encode()).hexdigest()[:16], 'parent_id': '',
                  'label': PurePosixPath(p).stem, 'path': p, 'fragment': '', 'status': 'present'} for p in sorted(pages)]
    seen = set()
    for node in nodes:
        if not node['id'] or node['id'] in seen or (node['parent_id'] and node['parent_id'] not in seen):
            raise ValueError('原文目录节点重复或父子关系无效，请核对 webhelpcontents.htm')
        seen.add(node['id'])
    default = next((n['path'] for n in nodes if n['status'] == 'present'), '')
    resources = [{'id': id, 'path': path, 'kind': kind} for id, path, kind in TOPIC_RESOURCES
                 if (source / path).is_file()]
    return {'schema': SCHEMA, 'nodes': nodes, 'default_page': default, 'pages': {},
            'presentation_resources': resources, 'source_release': source_release(source)}


class ReaderError(ValueError):
    def __init__(self, code, status):
        super().__init__(code)
        self.code, self.status = code, status


def page_data(reader, manifest, *, path='', entry=''):
    if bool(path) == bool(entry):
        raise ReaderError('invalid_location', 400)
    fragment = ''
    if entry:
        target = manifest['entries'].get(entry)
        if target is None:
            raise ReaderError('entry_missing', 404)
        if not target['locatable']:
            raise ReaderError('entry_unlocatable', 422)
        path, fragment = target['path'], target['fragment']
    page = reader['pages'].get(path)
    if page is None:
        raise ReaderError('page_missing', 404)
    return {'snapshot': reader['snapshot'], 'path': path, 'fragment': fragment, **page}
