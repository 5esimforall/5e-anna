"""Encode and identify the static tour cache payloads shared by both hosts."""
from hashlib import sha256
import json
from pathlib import Path

from anna.paths import PROJECT
from anna.presentation.tour import CURATION, load as load_curation
from anna.server import static_cache

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


def _encode_tour_dict(handle, libraries=None, *, curation_path=CURATION, skeleton=None) -> dict:
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
    return {
        'schema': SCHEMA,
        'snapshot': root_view.get('snapshot') or '',
        'curation_sha256': curation_hash(curation_path),
        'skeleton': skeleton if skeleton is not None else encode_skeleton(curation_path),
        'responses': responses,
    }


def encode_tour(handle, libraries=None, *, curation_path=CURATION, skeleton=None) -> bytes:
    """Encode /api/library, root tour view, level-1 topic views/layouts, and curation skeleton."""
    return static_cache.encode_json(
        _encode_tour_dict(handle, libraries, curation_path=curation_path, skeleton=skeleton)
    )


def encode_local_scopes(handle, ready, *, curation_path=CURATION) -> bytes | None:
    skeleton = encode_skeleton(curation_path)
    return static_cache.encode_scopes_bundle(
        handle,
        ready,
        lambda libs: _encode_tour_dict(handle, libs, curation_path=curation_path, skeleton=skeleton),
        curation_sha256=curation_hash(curation_path),
    )


def filename(payload: bytes) -> str:
    return static_cache.cache_filename('tour', payload)


def mark_tour(html: bytes, name: str) -> bytes:
    return static_cache.mark_attr(html, 'data-anna-tour', name)


def mark_tour_scopes(html: bytes, scopes: dict[str, str]) -> bytes:
    return static_cache.mark_scopes_attr(html, 'data-anna-tour-scopes', scopes)


def local_cache(registry, curation_path=CURATION):
    try:
        payload, data = static_cache.read_local_single(LOCAL_CACHE)
        collections = data['responses']['/api/library']['collections']
        cached_sha = data.get('curation_sha256')
        expected_sha = curation_hash(curation_path)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if collections == registry.describe() and cached_sha == expected_sha:
        return filename(payload), payload
    return None


def local_scope_caches(registry, curation_path=CURATION) -> tuple[dict[str, str], dict[str, bytes]]:
    try:
        collections, data, names, payloads = static_cache.read_local_scopes(LOCAL_SCOPES_CACHE, 'tour')
        cached_sha = data.get('curation_sha256')
        expected_sha = curation_hash(curation_path)
    except (OSError, ValueError, KeyError, TypeError):
        return {}, {}
    if collections == registry.describe() and cached_sha == expected_sha:
        return names, payloads
    return {}, {}
