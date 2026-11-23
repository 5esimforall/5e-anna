"""Package Anna and Stella libraries for static GitHub Pages hosting."""
import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from anna.paths import BOOK, PROJECT, WEB
from anna.presentation.catalog import DB
from anna.server.home import mark_home
from anna.publish.pages_assets import stage_browser_runtime
from anna.publish.pages_data import PAGES_LIMIT, export_pages_data

DOCUMENTS = {
    '/': ('index.html', 'index.html'),
    '/read': ('reader.html', 'read/index.html'),
    '/tour': ('tour.html', 'tour/index.html'),
    '/source': ('source.html', 'source/index.html'),
}
WEB_SUFFIXES = {'.css', '.ico', '.js', '.png'}
HTML_LINK = re.compile(rb'''((?<=\s)(?:src|href|poster|data-lazy-media|data-font)\s*=\s*)(["'])(/(?!/)[^"']*)\2''', re.I)
BOOK_QUERY = re.compile(rb'<a class="ref-filter" href="/\?[^"<>]*"', re.I)
DOCUMENT_SUFFIX = re.compile(r'^(/(?:read|tour|source))(?=[?#]|$)')
BINDINGS = re.compile(rb'(<script type="application/json" id="anna-bindings">)(.*?)(</script>)', re.S)


def mount_url(value, base):
    return base + DOCUMENT_SUFFIX.sub(r'\1/', value)[1:]


def rebase_links(content, resolve):
    def rebase(match):
        key, quote, raw_url = match.groups()
        new_url = resolve(raw_url.decode('ascii', errors='surrogateescape')).encode('ascii', errors='surrogateescape')
        return key + quote + new_url + quote
    return HTML_LINK.sub(rebase, content)


def rebase_book(content, path, base):
    if path.endswith(('/anna-assets/topic.js', '/anna-assets/links.js')):
        content = content.replace(b"'/read'", json.dumps(mount_url('/read', base)).encode())
        content = content.replace(b"'/?entry='", json.dumps(base + '?entry=').encode())
        reader_entry = json.dumps(mount_url('/read', base) + '?entry=').encode()
        return content.replace(b'definitionBase+encodeURIComponent(r.id)',
                               reader_entry + b"+encodeURIComponent((data.reader?.collection || '5echm')+':'+r.id)")
    if not path.lower().endswith(('.htm', '.html')):
        return content
    book = '/' + path.split('/', 1)[0] + '/'

    def book_url(url):
        if url.startswith(('/topics/', '/assets/')):
            return base + book.strip('/') + url
        if url.startswith(('/book/', '/our5etools-book/', '/our5etools-media/', '/?', '/read?')):
            return mount_url(url, base)
        return url

    def bindings(match):
        start, encoded, end = match.groups()
        data = json.loads(encoded)
        context = data['reader']
        context['content_base'] = mount_url(book, base)
        context['definition_base'] = context['content_base'] + 'definition/'
        if context.get('media_base'):
            context['media_base'] = mount_url(context['media_base'], base)
        payload = json.dumps(data, ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c')
        return start + payload.encode('ascii') + end

    if book == '/our5etools-book/':
        content = BOOK_QUERY.sub(lambda match: match[0] + b' target="_blank" rel="noopener"', content)
    content = BINDINGS.sub(bindings, rebase_links(content, book_url))
    if book == '/our5etools-book/' and b'data-lazy-media=' in content:
        module = f'<script type="module" src="{base}pages-media.js"></script>'.encode('ascii')
        content = content.replace(b'</body>', module + b'</body>', 1)
    return content


def build(output, source, db, book, base, *, our5etools_data=None, home_cache=False):
    if re.fullmatch(r'/(?:[A-Za-z0-9_.-]+/)*', base) is None or set(base.split('/')) & {'.', '..'}:
        raise ValueError('Use an absolute Pages base path ending in /')
    if output.exists():
        raise ValueError(f'Choose a new output directory: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pages-', dir=output.parent) as staging:
        stage = Path(staging) / 'site'
        stage.mkdir()
        stage_browser_runtime(stage / 'pages-engine')
        for file in sorted((PROJECT / 'web').iterdir()):
            if file.suffix in WEB_SUFFIXES:
                shutil.copyfile(file, stage / file.name)
        (stage / '.nojekyll').touch()
        total = export_pages_data(stage, source=source, db=db, book=book, our5etools_data=our5etools_data,
                                  rewrite_html=lambda raw, path: rebase_book(raw, path, base), home_cache=home_cache)
        home = next(stage.glob('home.*.json')).name if home_cache else None
        for source_name, target_name in DOCUMENTS.values():
            target = stage / target_name
            target.parent.mkdir(parents=True, exist_ok=True)
            html = (PROJECT / 'web' / source_name).read_bytes().replace(b'<html', b'<html data-anna-pages', 1)
            if home and source_name == 'index.html':
                html = mark_home(html, home)
            target.write_bytes(rebase_links(html, lambda url: mount_url(url, base)))
        with ZipFile(stage / 'pages-runtime.zip', 'w', ZIP_DEFLATED) as runtime:
            for module in sorted((PROJECT / 'anna').rglob('*.py')):
                runtime.write(module, module.relative_to(PROJECT))
        total += sum((stage / target).stat().st_size for _, target in DOCUMENTS.values())
        total += (stage / 'pages-runtime.zip').stat().st_size
        if total > PAGES_LIMIT:
            raise ValueError(f'Pages output is {total:,} bytes, over the {PAGES_LIMIT:,}-byte limit')
        stage.rename(output)
    print(f'{output}: {total:,} bytes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--web', type=Path, default=WEB)
    parser.add_argument('--base', default='/5e-anna/')
    parser.add_argument('--our5etools-data', type=Path)
    parser.add_argument('--home-cache', action='store_true')
    args = parser.parse_args()
    build(args.output.resolve(), args.web, DB, BOOK, args.base, our5etools_data=args.our5etools_data,
          home_cache=args.home_cache)
