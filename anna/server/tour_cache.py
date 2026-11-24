"""Encode and identify the static tour cache payloads shared by both hosts."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path

from anna.paths import PROJECT
from anna.presentation.tour import CURATION, load as load_curation
from anna.server import home

LOCAL_CACHE = PROJECT / 'data/tour.json'
LOCAL_SCOPES_CACHE = PROJECT / 'data/tour-scopes.json'
SCHEMA = 1


def curation_hash(path=CURATION) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def encode_skeleton(curation_path=CURATION) -> dict:
    """Minimal curation structure so custom scopes can render level 0 immediately."""
    loaded = load_curation(curation_path)
    topics = loaded['topics']
    sections = []
    for sec in loaded['sections']:
        items = []
        for k in sec['topics']:
            t = topics[k]
            items.append({
                'id': k,
                'label': t.label,
                'ruleset': t.ruleset,
                'collections': sorted(set(t.contents) | set(t.dynamic)),
                'global': t.query is not None or t.browse is not None,
            })
        sections.append({'key': sec['key'], 'label': sec['label'], 'topics': items})
    return {'sections': sections}


def _libs_suffix(libraries: tuple[str, ...] | None) -> str:
    if not libraries or tuple(libraries) == ('5echm',):
        return ''
    return '&libraries=' + ','.join(libraries)


def encode_tour(handle, libraries=None, *, curation_path=CURATION, skeleton=None) -> bytes:
    """Encode /api/library, root tour view, level-1 topic views/layouts, and curation skeleton."""
    status, _, lib_body = handle('/api/library')
    if status != 200:
        raise ValueError(f'Could not cache /api/library (HTTP {status})')
    libs_qs = _libs_suffix(libraries)
    root_route = f'/api/tour?at={libs_qs}'
    status, _, root_body = handle(root_route)
    if status != 200:
        raise ValueError(f'Could not cache {root_route} (HTTP {status})')
    root_view = json.loads(root_body)
    responses = {
        '/api/library': json.loads(lib_body),
        root_route: root_view,
    }
    for group in root_view.get('groups') or ():
        if group.get('key') != 'section':
            continue
        for item in group.get('items') or ():
            if item.get('kind') != 'topic' or item.get('status') != 'ok':
                continue
            key = item['id']
            topic_route = f'/api/tour?at={key}{libs_qs}'
            status, _, topic_body = handle(topic_route)
            if status != 200:
                raise ValueError(f'Could not cache {topic_route} (HTTP {status})')
            topic_view = json.loads(topic_body)
            responses[topic_route] = topic_view
            if topic_view.get('shape') == 'detail':
                layout_route = f'/api/tour-layout?at={key}{libs_qs}'
                status, _, layout_body = handle(layout_route)
                if status != 200:
                    raise ValueError(f'Could not cache {layout_route} (HTTP {status})')
                responses[layout_route] = json.loads(layout_body)
    payload = {
        'schema': SCHEMA,
        'snapshot': root_view.get('snapshot') or '',
        'curation_sha256': curation_hash(curation_path),
        'skeleton': skeleton if skeleton is not None else encode_skeleton(curation_path),
        'responses': responses,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def encode_local_scopes(handle, ready, *, curation_path=CURATION) -> bytes | None:
    scopes = home.extra_scopes(ready)
    if not scopes:
        return None
    status, _, body = handle('/api/library')
    if status != 200:
        raise ValueError(f'Could not cache /api/library (HTTP {status})')
    collections = json.loads(body)['collections']
    skeleton = encode_skeleton(curation_path)
    encoded_scopes = {}
    for libs in scopes:
        encoded_scopes[','.join(libs)] = json.loads(
            encode_tour(handle, libs, curation_path=curation_path, skeleton=skeleton)
        )
    return json.dumps({
        'collections': collections,
        'curation_sha256': curation_hash(curation_path),
        'scopes': encoded_scopes,
    }, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def filename(payload: bytes) -> str:
    return f'tour.{sha256(payload).hexdigest()[:16]}.json'


def mark_tour(html: bytes, name: str) -> bytes:
    return html.replace(b'<html', f'<html data-anna-tour="{name}"'.encode('ascii'), 1)


def mark_tour_scopes(html: bytes, scopes: dict[str, str]) -> bytes:
    if not scopes:
        return html
    value = ';'.join(f'{k}={v}' for k, v in scopes.items())
    return html.replace(b'<html', f'<html data-anna-tour-scopes="{value}"'.encode('ascii'), 1)


@lru_cache(maxsize=1)
def _read_local(revision):
    payload = LOCAL_CACHE.read_bytes()
    data = json.loads(payload)
    return (
        filename(payload),
        data['responses']['/api/library']['collections'],
        data.get('curation_sha256'),
        payload,
    )


@lru_cache(maxsize=1)
def _read_local_scopes(revision):
    data = json.loads(LOCAL_SCOPES_CACHE.read_bytes())
    collections = data['collections']
    curation_sha = data.get('curation_sha256')
    names = {}
    payloads = {}
    for scope_key, entry in data['scopes'].items():
        if not isinstance(scope_key, str) or not isinstance(entry, dict):
            raise ValueError('Invalid tour scopes cache entry')
        payload = json.dumps(entry, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        name = filename(payload)
        names[scope_key] = name
        payloads[name] = payload
    return collections, curation_sha, names, payloads


def local_cache(registry, curation_path=CURATION):
    try:
        stat = LOCAL_CACHE.stat()
        name, collections, cached_sha, payload = _read_local((stat.st_ino, stat.st_size, stat.st_mtime_ns))
        expected_sha = curation_hash(curation_path)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if collections == registry.describe() and cached_sha == expected_sha:
        return name, payload
    return None


def local_scope_caches(registry, curation_path=CURATION) -> tuple[dict[str, str], dict[str, bytes]]:
    try:
        stat = LOCAL_SCOPES_CACHE.stat()
        collections, cached_sha, names, payloads = _read_local_scopes((stat.st_ino, stat.st_size, stat.st_mtime_ns))
        expected_sha = curation_hash(curation_path)
    except (OSError, ValueError, KeyError, TypeError):
        return {}, {}
    if collections == registry.describe() and cached_sha == expected_sha:
        return names, payloads
    return {}, {}
