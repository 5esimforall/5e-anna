"""Encode and identify the default home responses shared by both hosts."""
from functools import lru_cache
from hashlib import sha256
import json

from anna.paths import PROJECT

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
