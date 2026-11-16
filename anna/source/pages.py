"""Read source bytes and parse WinCHM navigation without changing original encoding or hash."""
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import unquote, urlsplit

from anna.paths import WEB


def read_page(path):
    raw = path.read_bytes()
    try:
        return raw.decode('utf-8-sig'), 'utf-8', sha256(raw).hexdigest()
    except UnicodeDecodeError:
        try:
            return raw.decode('gb18030'), 'gb18030-inferred', sha256(raw).hexdigest()
        except UnicodeDecodeError:
            return raw.decode('utf-8', errors='replace'), 'utf-8-replacement', sha256(raw).hexdigest()


def clean(s):
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]*>', '', s))).strip()


class Toc(HTMLParser):
    def __init__(self, web=None):
        super().__init__()
        self.web = web or WEB
        self.divs = []
        self.rows = []
        self.anchor = None
        self.labels = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'span' and self.anchor is not None and re.fullmatch(r'l\d+', a.get('id', '')):
            self.anchor['node_id'] = a['id'][1:]
        if tag == 'div':
            self.divs.append(a.get('id', ''))
        if tag == 'a' and a.get('target') == 'content':
            m = re.search(r'LinkClick\((\d+)', a.get('onclick', ''))
            parents = [x[1:] for x in self.divs if re.fullmatch(r'd\d+', x)]
            href = unquote(a.get('href', ''))
            self.anchor = dict(
                node_id=m.group(1) if m else '',
                parent_id=parents[-1] if parents else '',
                href=href,
                label='',
                ancestors=parents,
            )

    def handle_data(self, data):
        if self.anchor is not None:
            self.anchor['label'] += data

    def handle_endtag(self, tag):
        if tag == 'div' and self.divs:
            self.divs.pop()
        if tag == 'a' and self.anchor is not None:
            row = self.anchor
            row['label'] = clean(row['label'])
            self.labels[row['node_id']] = row['label']
            row['toc_path'] = ' / '.join([self.labels.get(p, p) for p in row.pop('ancestors')] + [row['label']])
            row['exists'] = (self.web / urlsplit(row['href']).path).is_file()
            row['link_status'] = (
                'placeholder' if row['href'].startswith('#') else 'present' if row['exists'] else 'missing'
            )
            self.rows.append(row)
            self.anchor = None
