"""Build and identify the default home responses shared by both hosts."""
import argparse
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from anna.paths import FIVETOOLS_DATA, OUR5ETOOLS_DATA, PROJECT, WEB

LOCAL_CACHE = PROJECT / 'data/home.json'
ROUTES = ('/api/library', '/api/meta', '/api/facets', '/api/entries?offset=0&limit=150')


def encode_home(handle):
    responses = {}
    for route in ROUTES:
        status, _, body = handle(route)
        if status != 200:
            raise ValueError(f'Could not cache {route} (HTTP {status})')
        responses[route] = json.loads(body)
    return json.dumps(responses, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def filename(payload):
    return f'home.{sha256(payload).hexdigest()[:16]}.json'


def mark_home(html, name):
    return html.replace(b'<html', f'<html data-anna-home="{name}"'.encode('ascii'), 1)


@lru_cache(maxsize=1)
def _read_local(revision):
    payload = LOCAL_CACHE.read_bytes()
    return filename(payload), json.loads(payload)['/api/library']['collections'], payload


def local_cache(registry):
    try:
        stat = LOCAL_CACHE.stat()
        name, collections, payload = _read_local((stat.st_ino, stat.st_size, stat.st_mtime_ns))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if collections == registry.describe():
        return name, payload
    return None


def build_local(our5etools_data, fivetools_data, *, lazy_media=False):
    from anna.presentation.catalog import DB
    from anna.server.app import Bundle, _try_load_fivetools, _try_load_our5etools, validate_bundle
    from anna.server.linked_book import BOOK
    from anna.server.query import QueryRequest

    if not (BOOK / 'anna-manifest.json').is_file():
        return  # Building the companion library can run before the default library exists.
    manifest = validate_bundle(WEB, DB, BOOK)
    bundle = Bundle(WEB, DB, BOOK, manifest, ())
    collections = (_try_load_our5etools(our5etools_data, lazy_media=lazy_media),
                   _try_load_fivetools(fivetools_data, lazy_media=lazy_media))
    registry = bundle.registry(tuple(c for c in collections if c is not None))

    payload = encode_home(QueryRequest(registry).handle_path)
    with TemporaryDirectory(dir=LOCAL_CACHE.parent) as staging:
        temporary = Path(staging) / LOCAL_CACHE.name
        temporary.write_bytes(payload)
        temporary.replace(LOCAL_CACHE)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--our5etools-data', type=Path, default=OUR5ETOOLS_DATA)
    parser.add_argument('--5etools-data', dest='fivetools_data', type=Path, default=FIVETOOLS_DATA)
    parser.add_argument('--lazy-media', action='store_true')
    args = parser.parse_args()
    build_local(args.our5etools_data, args.fivetools_data, lazy_media=args.lazy_media)
