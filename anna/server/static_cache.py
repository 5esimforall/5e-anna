"""Shared manifest, digest, HTML attribute marking, and local cache readers for static home & tour caches."""
from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


def cache_filename(prefix: str, payload: bytes) -> str:
    return f'{prefix}.{sha256(payload).hexdigest()[:16]}.json'


def encode_json(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def file_revision(path: Path) -> tuple[str, int, int, int]:
    st = path.stat()
    return (str(path), st.st_ino, st.st_size, st.st_mtime_ns)


def mark_attr(html: bytes, attr: str, value: str) -> bytes:
    return html.replace(b'<html', f'<html {attr}="{value}"'.encode('ascii'), 1)


def mark_scopes_attr(html: bytes, attr: str, scopes: dict[str, str]) -> bytes:
    if not scopes:
        return html
    encoded = ';'.join(f'{k}={v}' for k, v in scopes.items())
    return html.replace(b'<html', f'<html {attr}="{encoded}"'.encode('ascii'), 1)


def extra_scopes(ready: Any) -> list[tuple[str, ...]]:
    keys = tuple(ready)
    if len(keys) <= 1:
        return []
    first, other = keys[0], keys[1]
    return [(first, other), (other,), (other, first)]


def encode_scopes_bundle(
    handle: Callable[[str], tuple[int, Any, bytes]],
    ready: Any,
    encode_scope: Callable[[tuple[str, ...]], Any],
    **extra_fields: Any,
) -> bytes | None:
    scopes = extra_scopes(ready)
    if not scopes:
        return None
    status, _, lib_body = handle('/api/library')
    if status != 200:
        raise ValueError(f'Could not cache /api/library (HTTP {status})')
    collections = json.loads(lib_body)['collections']
    encoded_scopes = {','.join(libs): encode_scope(libs) for libs in scopes}
    return encode_json({'collections': collections, **extra_fields, 'scopes': encoded_scopes})


@lru_cache(maxsize=4)
def _load_json_payload(rev: tuple[str, int, int, int]) -> tuple[bytes, Any]:
    path = Path(rev[0])
    payload = path.read_bytes()
    return payload, json.loads(payload)


def clear_cache() -> None:
    _load_json_payload.cache_clear()


def read_local_single(cache_path: Path) -> tuple[bytes, Any]:
    return _load_json_payload(file_revision(cache_path))


def read_local_scopes(
    scopes_path: Path,
    prefix: str,
) -> tuple[Any, dict[str, Any], dict[str, str], dict[str, bytes]]:
    _, data = _load_json_payload(file_revision(scopes_path))
    collections = data['collections']
    names: dict[str, str] = {}
    payloads: dict[str, bytes] = {}
    for scope_key, entry in data['scopes'].items():
        if not isinstance(scope_key, str) or not isinstance(entry, dict):
            raise ValueError(f'Invalid {prefix} scopes cache entry')
        payload = encode_json(entry)
        name = cache_filename(prefix, payload)
        names[scope_key] = name
        payloads[name] = payload
    return collections, data, names, payloads
