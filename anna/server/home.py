"""Encode and identify the default home responses shared by both hosts."""
from functools import lru_cache
from hashlib import sha256
import json

from anna.paths import PROJECT

LOCAL_CACHE = PROJECT / 'data/home.json'
LOCAL_SCOPES_CACHE = PROJECT / 'data/home-scopes.json'
ROUTES = ('/api/library', '/api/meta', '/api/facets', '/api/entries?offset=0&limit=150')


def scope_routes(libraries: tuple[str, ...] | None = None) -> tuple[str, ...]:
    if not libraries or tuple(libraries) == ('5echm',):
        return ROUTES
    libs = ','.join(libraries)
    return (
        '/api/library',
        f'/api/meta?libraries={libs}',
        f'/api/facets?libraries={libs}',
        f'/api/entries?offset=0&limit=150&libraries={libs}',
    )


def extra_scopes(ready) -> list[tuple[str, ...]]:
    keys = tuple(ready)
    if len(keys) <= 1:
        return []
    first, other = keys[0], keys[1]
    return [(first, other), (other,), (other, first)]


def encode_home(handle, libraries=None):
    responses = {}
    for route in scope_routes(libraries):
        status, _, body = handle(route)
        if status != 200:
            raise ValueError(f'Could not cache {route} (HTTP {status})')
        responses[route] = json.loads(body)
    return json.dumps(responses, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def encode_local_scopes(handle, ready) -> bytes | None:
    scopes = extra_scopes(ready)
    if not scopes:
        return None
    status, _, body = handle('/api/library')
    if status != 200:
        raise ValueError(f'Could not cache /api/library (HTTP {status})')
    collections = json.loads(body)['collections']
    encoded_scopes = {}
    for libs in scopes:
        encoded_scopes[','.join(libs)] = json.loads(encode_home(handle, libs))
    return json.dumps({'collections': collections, 'scopes': encoded_scopes},
                      ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def filename(payload):
    return f'home.{sha256(payload).hexdigest()[:16]}.json'


def mark_home(html, name):
    return html.replace(b'<html', f'<html data-anna-home="{name}"'.encode('ascii'), 1)


def mark_home_scopes(html: bytes, scopes: dict[str, str]) -> bytes:
    if not scopes:
        return html
    value = ';'.join(f'{k}={v}' for k, v in scopes.items())
    return html.replace(b'<html', f'<html data-anna-home-scopes="{value}"'.encode('ascii'), 1)


@lru_cache(maxsize=1)
def _read_local(revision):
    payload = LOCAL_CACHE.read_bytes()
    return filename(payload), json.loads(payload)['/api/library']['collections'], payload


@lru_cache(maxsize=1)
def _read_local_scopes(revision):
    data = json.loads(LOCAL_SCOPES_CACHE.read_bytes())
    collections = data['collections']
    names = {}
    payloads = {}
    for scope_key, responses in data['scopes'].items():
        if not isinstance(scope_key, str) or not isinstance(responses, dict):
            raise ValueError('Invalid home scopes cache entry')
        payload = json.dumps(responses, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        name = filename(payload)
        names[scope_key] = name
        payloads[name] = payload
    return collections, names, payloads


def local_cache(registry):
    try:
        stat = LOCAL_CACHE.stat()
        name, collections, payload = _read_local((stat.st_ino, stat.st_size, stat.st_mtime_ns))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if collections == registry.describe():
        return name, payload
    return None


def local_scope_caches(registry) -> tuple[dict[str, str], dict[str, bytes]]:
    try:
        stat = LOCAL_SCOPES_CACHE.stat()
        collections, names, payloads = _read_local_scopes((stat.st_ino, stat.st_size, stat.st_mtime_ns))
    except (OSError, ValueError, KeyError, TypeError):
        return {}, {}
    if collections == registry.describe():
        return names, payloads
    return {}, {}
