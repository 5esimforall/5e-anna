"""Encode and identify the default home responses shared by both hosts."""
import json

from anna.paths import PROJECT
from anna.server import static_cache

LOCAL_CACHE = PROJECT / 'data/home.json'
LOCAL_SCOPES_CACHE = PROJECT / 'data/home-scopes.json'
ROUTES = ('/api/library', '/api/meta', '/api/facets', '/api/entries?offset=0&limit=150')
extra_scopes = static_cache.extra_scopes


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


def _encode_home_dict(handle, libraries=None) -> dict:
    responses = {}
    for route in scope_routes(libraries):
        status, _, body = handle(route)
        if status != 200:
            raise ValueError(f'Could not cache {route} (HTTP {status})')
        responses[route] = json.loads(body)
    return responses


def encode_home(handle, libraries=None):
    return static_cache.encode_json(_encode_home_dict(handle, libraries))


def encode_local_scopes(handle, ready) -> bytes | None:
    return static_cache.encode_scopes_bundle(handle, ready, lambda libs: _encode_home_dict(handle, libs))


def filename(payload):
    return static_cache.cache_filename('home', payload)


def mark_home(html, name):
    return static_cache.mark_attr(html, 'data-anna-home', name)


def mark_home_scopes(html: bytes, scopes: dict[str, str]) -> bytes:
    return static_cache.mark_scopes_attr(html, 'data-anna-home-scopes', scopes)


def local_cache(registry):
    try:
        payload, data = static_cache.read_local_single(LOCAL_CACHE)
        collections = data['/api/library']['collections']
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if collections == registry.describe():
        return filename(payload), payload
    return None


def local_scope_caches(registry) -> tuple[dict[str, str], dict[str, bytes]]:
    try:
        collections, _, names, payloads = static_cache.read_local_scopes(LOCAL_SCOPES_CACHE, 'home')
    except (OSError, ValueError, KeyError, TypeError):
        return {}, {}
    if collections == registry.describe():
        return names, payloads
    return {}, {}
