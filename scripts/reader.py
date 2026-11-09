"""Snapshot-bound reading model. TOC nodes, documents and definitions stay distinct."""
from hashlib import sha256
from pathlib import PurePosixPath

from html_extract import resolve_link
from inventory import Toc

SCHEMA = 3

# These are the source reader's content assets, not its navigation/application scripts.
TOPIC_RESOURCES = (
    ('webhelpTopicStyles', 'assets/webhelp-topic.css', 'style'),
    ('webhelpContentEnhanceStyles', 'assets/content-enhance.css', 'style'),
    ('webhelpContentEnhanceScript', 'assets/content-enhance.js', 'script'),
    ('webhelpContrastScript', 'assets/webhelp-contrast.js', 'script'),
)


def content_file(path):
    """Publish source content/resources, never the upstream WebHelp application."""
    parts = PurePosixPath(path).parts
    if not parts or any(p.startswith('.') for p in parts):
        return False
    if len(parts) == 1:
        return PurePosixPath(path).suffix.lower() in {'.css', '.js', '.ico', '.png', '.jpg', '.gif', '.svg'}
    return True


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
            'presentation_resources': resources}


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
