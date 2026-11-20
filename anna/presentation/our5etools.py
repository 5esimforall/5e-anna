"""Read-only presentation providers for the our5etools collection: catalog, tour, and Stella reader."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
import csv
import dataclasses
import html as _html
import io
import ipaddress
import json
import math
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sqlite3
import threading
import time
from typing import Any
from urllib.parse import quote, unquote, urlsplit
import urllib.request

import anna.our5etools.types as otypes
import anna.paths as paths
from anna.presentation.collections import Collection, SearchHits
from anna.presentation.reader import ReaderError, SCHEMA as READER_SCHEMA
from anna.presentation import tour as tour_curation
from anna.presentation.tour import DIVIDER, HINTED_GROUP, TourError, clip, divider, first_sentence

COLLECTION_KEY = 'our5etools'
COLLECTION_ZH = 'our5etools'
COLLECTION_EN = 'our5etools'
READER_BASE = '/our5etools-book/'
MEDIA_BASE = '/our5etools-media/'
TOUR_URL = '/tour?collection=our5etools'
READER_URL = '/read?collection=our5etools'

FIVETOOLS_COLLECTION_KEY = '5etools'
FIVETOOLS_READER_BASE = '/5etools-book/'
FIVETOOLS_MEDIA_BASE = '/5etools-media/'
MAX_REMOTE_MEDIA_BYTES = 25 * 1024 * 1024
NEGATIVE_MEDIA_TTL_SECONDS = 60.0
REMOTE_IMG_BASES: tuple[str, ...] = (
    'https://5e.kiwee.top/img/',
    'https://raw.githubusercontent.com/5etools-mirror-3/5etools-img/main/',
)
SAFE_ROOT_MEDIA_EXTS: frozenset[str] = frozenset({
    '.png', '.webp', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
})
LAZY_MEDIA_FIGURE_RE = re.compile(
    r'<figure\b([^>]*\bdata-lazy-media="(/(?:[a-z0-9-]+)-media/([^"]+))"[^>]*)>(.*?)</figure>',
    re.DOTALL,
)
LAZY_MEDIA_AUDIO_RE = re.compile(
    r'<div\b([^>]*\bdata-lazy-media="(/(?:[a-z0-9-]+)-media/([^"]+))"[^>]*)>(.*?)</div>',
    re.DOTALL,
)
FIGCAPTION_RE = re.compile(r'<figcaption>.*?</figcaption>', re.DOTALL)
MEDIA_LINK_RE = re.compile(r'<a\b[^>]*class="o5e-media-link"[^>]*>.*?</a>', re.DOTALL)
CLASS_ATTR_RE = re.compile(r'\bclass="([^"]*)"')
ALT_ATTR_RE = re.compile(r'\bdata-media-alt="([^"]*)"')
TITLE_ATTR_RE = re.compile(r'\btitle="([^"]*)"')

BATCH = 150
RESTORE_LIMIT = 1500
MAX_SOURCES_FILTER = 600
ENTRY_LIST_LIMIT = 60
GROUP_LIMIT = 400
LEAD_LIMIT = 220

NOT = 'not_'
ORIGINS = ('官方', '合作', '本站精选', '社区')
RULESETS = ('5e', '5r', '待核')
LEGACIES = ('entry_marked', 'source_archived', 'unmarked')
COPY_STATUSES = ('ok', 'missing_base', 'ambiguous_base', 'cycle', 'mod_warning')
NEGATABLE = frozenset({'type', 'q', 'body', 'ruleset', 'legacy', 'origin', 'source', 'package', 'copy_status'})
# The library scope bounds the universe; counts never set it aside and `without` never offers removing it.
SCOPE_KEY = '__scope'

VERSION_FIELDS = (
    'id', 'name', 'name_zh', 'name_en', 'ruleset', 'source_ids', 'source_names',
    'fields', 'field_conflicts', 'quickref', 'legacy', 'package_id',
)

TERM_RE = re.compile(r'"([^"]+)"|(\S+)')
SLUG_SAFE_RE = re.compile(r'[^a-zA-Z0-9._-]+')
BOOK_SEGMENT_RE = re.compile(r'^book\.([A-Za-z0-9._-]+)$')
PACK_SEGMENT_RE = re.compile(r'^pack\.([A-Za-z0-9._-]+)$')
CONTENTS_SEGMENT_RE = re.compile(r'^n(.+)$')
ENTRY_SEGMENT_RE = re.compile(r'^o5e_[0-9a-f]{16}$')
FLUFF_SECTION_RE = re.compile(r'<section class="o5e-fluff">.*?</section>', re.DOTALL)
ORIGIN_RANK: dict[str, int] = {'官方': 0, '合作': 1, '本站精选': 2, '社区': 3, '待核': 4}

MIME_OVERRIDES: dict[str, str] = {
    '.opus': 'audio/ogg; codecs=opus',
    '.ogg': 'audio/ogg',
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.webp': 'image/webp',
    '.png': 'image/png',
    '.avif': 'image/avif',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
    '.woff': 'font/woff',
    '.ttf': 'font/ttf',
    '.otf': 'font/otf',
    '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.htm': 'text/html; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.m4a': 'audio/mp4',
}

ALLOWED_MEDIA_ROOTS = frozenset({'audio', 'img', 'fonts', 'ext'})
FALLBACK_BOOK_ASSETS = frozenset({'links.js', 'topic.js', 'link-labels.js'})

COMMON_LABELS: dict[str, str] = {
    'type': '类型',
    'ruleset': '规则版本',
    'origin': '来源性质',
    'source': '来源',
    'package': '资料包',
    'legacy': '旧版标记',
    'copy_status': '继承状态',
}


def _first(params: dict[str, list[str]], name: str, default: str = '') -> str:
    vals = params.get(name)
    if not vals:
        return default
    return vals[0]


def _file_sig(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
        return (st.st_ino, st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _json_loads(raw: Any, default: Any) -> Any:
    if raw is None or raw == '':
        return default
    if not isinstance(raw, (str, bytes)):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def _field_of(key: str) -> str:
    key = key.removeprefix(NOT)
    if key == 'typing':
        return 'q'
    return re.sub(r'^(q|body)\d+$', r'\1', key)


def _search_terms(params: dict[str, list[str]], key: str = 'q') -> list[str]:
    terms: list[str] = []
    for raw in params.get(key, []):
        val = raw.strip()
        if val and val not in terms:
            terms.append(val)
    return terms


def _typing_term(params: dict[str, list[str]]) -> str:
    return _first(params, 'typing').strip()


def _latest_term(params: dict[str, list[str]]) -> str:
    typing = _typing_term(params)
    if typing:
        return typing
    terms = _search_terms(params, 'q')
    return terms[-1] if terms else ''


def _parse_cr(value: str) -> float:
    num = otypes.number(value)
    if num is None:
        raise ValueError(f'无效 CR 数值：{value}')
    return float(num)


def _entry_types(params: dict[str, list[str]]) -> list[str]:
    chosen = list(dict.fromkeys(v for v in params.get('type', []) if v))
    if any(t not in otypes.TYPES for t in chosen):
        raise ValueError('未知条目类型')
    return chosen


def _result_window(params: dict[str, list[str]]) -> tuple[int, int]:
    try:
        offset = int(_first(params, 'offset', '0'))
        limit = int(_first(params, 'limit', str(BATCH)))
    except ValueError:
        raise ValueError('offset 与 limit 必须是整数') from None
    if offset < 0:
        raise ValueError('offset 不能为负数')
    if not 1 <= limit <= RESTORE_LIMIT:
        raise ValueError(f'每次读取 1 至 {RESTORE_LIMIT} 行')
    return offset, limit


def _slug(value: str, fallback: str = 'item') -> str:
    cleaned = SLUG_SAFE_RE.sub('-', value.strip()).strip('-.')
    return cleaned or fallback


def _safe_relpath(relpath: str) -> PurePosixPath | None:
    if not relpath or '\x00' in relpath or '\\' in relpath:
        return None
    stripped = relpath.lstrip('/')
    if not stripped:
        return None
    posix = PurePosixPath(stripped)
    if posix.is_absolute() or any(part in {'', '.', '..'} for part in posix.parts):
        return None
    return posix


def _sniff_media_mime(path: Path) -> str | None:
    try:
        with path.open('rb') as f:
            head = f.read(64)
    except OSError:
        return None
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    if head.startswith(b'OggS'):
        return 'audio/ogg; codecs=opus' if b'OpusHead' in head else 'audio/ogg'
    if head.startswith(b'ID3') or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return 'audio/mpeg'
    if len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == b'WAVE':
        return 'audio/wav'
    low = head.lstrip().lower()
    if low.startswith((b'<svg', b'<?xml')) and b'<svg' in low:
        return 'image/svg+xml'
    return None


def _mime_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in MIME_OVERRIDES:
        return MIME_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed:
        return guessed
    return _sniff_media_mime(path) or 'application/octet-stream'


def _is_safe_cached_file(media_dir: Path, relpath: str) -> bool:
    posix = _safe_relpath(relpath)
    if posix is None:
        return False
    base = media_dir.resolve()
    cand = (base / posix.as_posix()).resolve()
    return cand.is_relative_to(base) and not cand.is_symlink() and cand.is_file()


def promote_media_html(html_text: str, media_dir: Path, *, lazy_media: bool = False) -> str:
    """Promote ``data-lazy-media`` figures/audio to ``<img>``/``<audio>`` when lazy mode is on or cached locally."""
    if not html_text or 'data-lazy-media=' not in html_text:
        return html_text

    def _replace_figure(m: re.Match[str]) -> str:
        attrs, src, encoded_rel, inner = m.group(1), m.group(2), m.group(3), m.group(4)
        rel = unquote(encoded_rel)
        if not lazy_media and not _is_safe_cached_file(media_dir, rel):
            return m.group(0)
        cls_m = CLASS_ATTR_RE.search(attrs)
        classes = [c for c in (cls_m.group(1).split() if cls_m else ['o5e-figure']) if c != 'o5e-media-remote']
        if 'o5e-figure' not in classes:
            classes.insert(0, 'o5e-figure')
        alt_m = ALT_ATTR_RE.search(attrs)
        alt_attr = alt_m.group(1) if alt_m else '配图'
        title_m = TITLE_ATTR_RE.search(attrs)
        title_attr = f' title="{title_m.group(1)}"' if title_m else ''
        fig_m = FIGCAPTION_RE.search(inner)
        link_m = MEDIA_LINK_RE.search(inner)
        tail = (fig_m.group(0) if fig_m else '') + (link_m.group(0) if link_m else '')
        return (
            f'<figure class="{" ".join(classes)}"{title_attr}>'
            f'<img src="{src}" alt="{alt_attr}" loading="lazy" decoding="async" data-o5e-fallback="1">'
            f'<span class="o5e-media-fallback-note" hidden>远端配图（未离线收录）</span>'
            f'{tail}</figure>'
        )

    def _replace_audio(m: re.Match[str]) -> str:
        src, encoded_rel = m.group(2), m.group(3)
        rel = unquote(encoded_rel)
        if not lazy_media and not _is_safe_cached_file(media_dir, rel):
            return m.group(0)
        return (
            f'<div class="o5e-audio">'
            f'<span class="o5e-audio-label">读音</span>'
            f'<audio controls preload="none" src="{src}"></audio>'
            f'</div>'
        )

    out = LAZY_MEDIA_FIGURE_RE.sub(_replace_figure, html_text)
    out = LAZY_MEDIA_AUDIO_RE.sub(_replace_audio, out)
    return out


def _validate_remote_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname:
        raise ValueError(f'Unsafe remote media URL scheme/host: {url!r}')
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f'Credentials not allowed in remote media URL: {url!r}')
    port = parsed.port or (443 if parsed.scheme.lower() == 'https' else 80)
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f'DNS resolution failed for {parsed.hostname}: {exc}') from exc
    if not infos:
        raise ValueError(f'No DNS results for {parsed.hostname}')
    for _, _, _, _, sockaddr in infos:
        host_ip = sockaddr[0].split('%', 1)[0]
        addr = ipaddress.ip_address(host_ip)
        if not addr.is_global or addr.is_multicast:
            raise ValueError(f'Blocked non-global target IP {host_ip} for {url!r}')
    return url


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_repeats = 3
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_remote_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _sniff_bytes_valid_media(chunk: bytes, content_type: str) -> bool:
    ct = content_type.split(';', 1)[0].strip().lower()
    if ct in {'text/html', 'application/xhtml+xml', 'application/json', 'text/plain', 'application/xml'}:
        return False
    if ct.startswith(('image/', 'audio/')):
        return True
    if chunk.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'GIF87a', b'GIF89a', b'OggS', b'ID3')):
        return True
    if len(chunk) >= 12 and chunk[:4] == b'RIFF' and chunk[8:12] in {b'WEBP', b'WAVE'}:
        return True
    if len(chunk) >= 2 and chunk[0] == 0xFF and (chunk[1] & 0xE0) == 0xE0:
        return True
    low = chunk.lstrip().lower()
    if low.startswith((b'<svg', b'<?xml')) and b'<svg' in low:
        return True
    return False


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    otypes.register(db)
    return db


def _parse_search_terms(keyword: str) -> list[str]:
    terms: list[str] = []
    for match in TERM_RE.finditer(keyword):
        raw = match.group(1) or match.group(2) or ''
        for part in raw.split('|'):
            term = part.strip().lower()
            if term and term not in terms:
                terms.append(term)
    return terms


def _make_preview(text: str, text_lower: str, terms: list[str]) -> str:
    pos = -1
    for term in terms:
        found = text_lower.find(term)
        if found != -1 and (pos == -1 or found < pos):
            pos = found
    if pos == -1:
        snippet = text[:420]
        return snippet + ('…' if len(text) > 420 else '')
    start = max(0, pos - 140)
    end = min(len(text), pos + 320)
    return ('…' if start > 0 else '') + text[start:end] + ('…' if end < len(text) else '')


# ---------------------------------------------------------------------------
# 1. Our5etoolsCatalog
# ---------------------------------------------------------------------------

class Our5etoolsCatalog:
    """Read-only CatalogProvider over our5etools catalog.sqlite."""

    def __init__(self, data_dir: Path | str, *, lazy_media: bool = False, collection_key: str = COLLECTION_KEY):
        self.data_dir = Path(data_dir).resolve()
        self.collection_key = (collection_key or COLLECTION_KEY).strip() or COLLECTION_KEY
        self.tour_url = f'/tour?collection={self.collection_key}'
        self.media_dir = self.data_dir / 'media'
        self.lazy_media = bool(lazy_media)
        self.db_path = self.data_dir / 'catalog.sqlite'
        self.reader_path = self.data_dir / 'reader' / 'anna-reader.json'
        self.summary_path = self.data_dir / 'summary.json'
        self._lock = threading.Lock()
        self._cache_sig: tuple[int, int, int] | None = None
        self._sources_map: dict[str, dict[str, Any]] = {}
        self._packages_map: dict[str, dict[str, Any]] = {}
        self._held_types: set[str] = set()
        self._bases: dict[str, tuple[str, str, str]] = {}
        self._scope_universe_cache: dict[tuple[str, ...], tuple[int, dict[str, set[str]], dict[str, int]]] = {}
        self._unfiltered_facets_cache: dict[str, Any] | None = None
        self._entry_locations: dict[str, dict[str, Any]] = {}
        self._reader_roots: dict[str, str] = {}
        self._reader_sig: tuple[int, int, int] | None = None

    def _connect(self) -> sqlite3.Connection:
        return _connect_ro(self.db_path)

    def _ensure_static_cache(self, db: sqlite3.Connection) -> None:
        sig = _file_sig(self.db_path)
        with self._lock:
            if sig is not None and sig == self._cache_sig:
                return
            sources_map: dict[str, dict[str, Any]] = {}
            for row in db.execute(
                'SELECT id, package_id, origin, ruleset, short_name, name, data_json FROM sources'
            ):
                data = _json_loads(row['data_json'], {})
                sources_map[row['id']] = {
                    'id': row['id'],
                    'package_id': row['package_id'],
                    'origin': row['origin'] or data.get('origin') or '社区',
                    'ruleset': row['ruleset'] or data.get('ruleset') or '待核',
                    'short_name': row['short_name'] or data.get('short_label') or data.get('code') or row['id'],
                    'name': row['name'] or data.get('name') or row['id'],
                    'data': data,
                }
            packages_map: dict[str, dict[str, Any]] = {}
            for row in db.execute(
                'SELECT id, name, origin, ours, entry_count, book_count, data_json FROM packages'
            ):
                data = _json_loads(row['data_json'], {})
                packages_map[row['id']] = {
                    'id': row['id'],
                    'name': row['name'] or data.get('name') or row['id'],
                    'origin': row['origin'] or data.get('origin') or '社区',
                    'ours': bool(row['ours']),
                    'entry_count': int(row['entry_count'] or 0),
                    'book_count': int(row['book_count'] or 0),
                    'data': data,
                }
            held = {r[0] for r in db.execute('SELECT DISTINCT type FROM entries') if r[0]}
            # A copied entry (5etools `_copy`) names the entry it was copied from and that entry's work.
            bases: dict[str, tuple[str, str, str]] = {}
            for copy, base, base_work, base_name_en in db.execute(
                    "SELECT r.source_id, r.target_id, b.source_id, b.name_en FROM relations r JOIN entries b ON b.id = r.target_id "
                    "WHERE r.type = 'copied_from' ORDER BY r.source_id, r.target_id"):
                bases.setdefault(copy, (base, base_work, base_name_en))
            self._bases = bases
            self._sources_map = sources_map
            self._packages_map = packages_map
            self._held_types = held
            self._scope_universe_cache.clear()
            self._unfiltered_facets_cache = None
            self._cache_sig = sig

    def _load_reader_index(self) -> None:
        """Entry locations and each work's book root from the built reader, reloaded when it changes."""
        sig = _file_sig(self.reader_path)
        with self._lock:
            if sig == self._reader_sig and sig is not None:
                return
            locations: dict[str, dict[str, Any]] = {}
            roots: dict[str, str] = {}
            if sig is not None:
                try:
                    raw = json.loads(self.reader_path.read_text('utf-8'))
                    locs = raw.get('entry_locations')
                    locations = locs if isinstance(locs, dict) else {}
                    for root in raw.get('roots') or []:
                        if isinstance(root, dict) and root.get('source_id') and root.get('id'):
                            roots.setdefault(root['source_id'], root['id'])
                except (OSError, ValueError):
                    locations, roots = {}, {}
            self._entry_locations, self._reader_roots, self._reader_sig = locations, roots, sig

    def _get_entry_locations(self) -> dict[str, dict[str, Any]]:
        self._load_reader_index()
        return self._entry_locations

    # --- Filtering & SQL builder (shared identically by entries() and facets()) ---

    def _included_conditions(self, params: dict[str, list[str]]) -> list[tuple[str, str, list[Any]]]:
        out: list[tuple[str, str, list[Any]]] = []

        types = _entry_types(params)
        if types:
            out.append(('type', 'e.type IN (' + ','.join('?' for _ in types) + ')', list(types)))

        terms = _search_terms(params, 'q')
        for i, term in enumerate(terms):
            out.append((f'q{i}', 'instr(lower(e.search_name), lower(?)) > 0', [term]))

        typing = _typing_term(params)
        if typing and typing not in terms:
            out.append(('typing', 'instr(lower(e.search_name), lower(?)) > 0', [typing]))

        for i, term in enumerate(_search_terms(params, 'body')):
            out.append((f'body{i}', 'instr(lower(e.search_body), lower(?)) > 0', [term]))

        for key, col, allowed in (
            ('ruleset', 'e.ruleset', RULESETS),
            ('legacy', 'e.legacy', LEGACIES),
            ('copy_status', 'e.copy_status', COPY_STATUSES),
        ):
            chosen = list(dict.fromkeys(v for v in params.get(key, []) if v))
            if not chosen:
                continue
            if chosen == [otypes.NONE]:
                out.append((key, '0', []))
                continue
            if any(v not in allowed for v in chosen):
                raise ValueError('无效筛选：' + key)
            out.append((key, f'{col} IN (' + ','.join('?' for _ in chosen) + ')', chosen))

        origins = list(dict.fromkeys(v for v in params.get('origin', []) if v))
        if origins:
            if origins == [otypes.NONE]:
                out.append(('origin', '0', []))
            elif any(o not in ORIGINS for o in origins):
                raise ValueError('无效来源性质')
            else:
                ph = ','.join('?' for _ in origins)
                out.append((
                    'origin',
                    f"EXISTS(SELECT 1 FROM entry_facets ef WHERE ef.entry_id=e.id AND ef.facet_key='origin' AND ef.facet_value IN ({ph}))",
                    origins,
                ))

        sources = list(dict.fromkeys(v for v in params.get('source', []) if v))
        if sources:
            if len(sources) > MAX_SOURCES_FILTER:
                raise ValueError('来源过多')
            if sources == [otypes.NONE]:
                out.append(('source', '0', []))
            else:
                ph = ','.join('?' for _ in sources)
                out.append(('source', f'e.source_id IN ({ph})', sources))

        packages = list(dict.fromkeys(v for v in params.get('package', []) if v))
        if packages:
            if len(packages) > MAX_SOURCES_FILTER:
                raise ValueError('资料包过多')
            if packages == [otypes.NONE]:
                out.append(('package', '0', []))
            else:
                ph = ','.join('?' for _ in packages)
                out.append(('package', f'e.package_id IN ({ph})', packages))

        if _first(params, 'conflict') == '1':
            out.append(('conflict', "json_array_length(COALESCE(e.conflicts_json, '[]')) > 0", []))

        # Facet parameters f_<field>
        for key in sorted(k for k in params if k.startswith('f_')):
            field = key[2:]
            if field not in otypes.LABELS:
                raise ValueError('无效筛选：' + key)
            vals = list(dict.fromkeys(v for v in params.get(key, []) if v))
            if not vals:
                continue
            if vals == [otypes.NONE]:
                out.append((key, '0', []))
                continue
            ph = ','.join('?' for _ in vals)
            out.append((
                key,
                f'EXISTS(SELECT 1 FROM entry_facets ef WHERE ef.entry_id=e.id AND ef.facet_key=? AND ef.facet_value IN ({ph}))',
                [field, *vals],
            ))

        # CR numeric range
        cr_min_raw = _first(params, 'cr_min').strip()
        cr_max_raw = _first(params, 'cr_max').strip()
        cr_min = _parse_cr(cr_min_raw) if cr_min_raw else None
        cr_max = _parse_cr(cr_max_raw) if cr_max_raw else None
        if cr_min is not None and cr_max is not None and cr_min > cr_max:
            raise ValueError('CR 下限不能大于上限')
        if cr_min is not None:
            out.append(('cr_min', 'e.cr_num IS NOT NULL AND e.cr_num >= ?', [cr_min]))
        if cr_max is not None:
            out.append(('cr_max', 'e.cr_num IS NOT NULL AND e.cr_num <= ?', [cr_max]))

        return out

    @staticmethod
    def _restriction(restrict: Any) -> list[tuple[str, str, list[Any]]]:
        """The reader's library scope as one always-held condition (`__scope`), never set aside by a count."""
        if restrict is None:
            return []
        clauses: list[str] = []
        values: list[Any] = []
        if restrict.rulesets is not None:
            clauses.append('e.ruleset IN (' + ','.join('?' for _ in restrict.rulesets) + ')')
            values.extend(sorted(restrict.rulesets))
        if restrict.sources is not None:
            clauses.append('e.source_id IN (' + ','.join('?' for _ in restrict.sources) + ')' if restrict.sources else '0')
            values.extend(sorted(restrict.sources))
        return [(SCOPE_KEY, ' AND '.join(clauses), values)] if clauses else []

    def _conditions(self, params: dict[str, list[str]], restrict: Any = None) -> list[tuple[str, str, list[Any]]]:
        excluded: dict[str, list[str]] = {}
        for k, v in params.items():
            if not k.startswith(NOT):
                continue
            base = k[len(NOT):]
            if base not in NEGATABLE and not base.startswith('f_'):
                raise ValueError('无法排除：' + base)
            excluded[base] = v
        inc = self._included_conditions(params)
        exc_raw = self._included_conditions(excluded) if excluded else []
        exc = [(NOT + key, f'NOT COALESCE(({sql}), 0)', vals) for key, sql, vals in exc_raw]
        return self._restriction(restrict) + inc + exc

    @staticmethod
    def _where_from(conds: list[tuple[str, str, list[Any]]]) -> tuple[str, list[Any]]:
        if not conds:
            return '', []
        sql = ' WHERE ' + ' AND '.join(f'({c[1]})' for c in conds)
        vals = [v for _, _, vs in conds for v in vs]
        return sql, vals

    def _order_sql(self, params: dict[str, list[str]], default_sort: str, has_search: bool) -> str:
        raw_sort = _first(params, 'sort')
        if not raw_sort:
            key = 'match' if has_search else default_sort
            direction = 'ASC'
        else:
            desc = raw_sort.startswith('-')
            key = raw_sort[1:] if desc else raw_sort
            direction = 'DESC' if desc else 'ASC'

        tie = 'e.sort_name ASC, e.id ASC'
        if key == 'match':
            return f'browse_match(e.name_zh, e.name_en, e.name, browse_query()) ASC, {tie}'
        if key == 'name':
            return f'e.sort_name {direction}, e.id ASC'
        if key == 'name_en':
            return f"CASE WHEN e.name_en = '' THEN 1 ELSE 0 END ASC, lower(e.name_en) {direction}, {tie}"
        if key == 'type':
            return f'{otypes.TYPE_RANK} {direction}, {tie}'
        if key == 'ruleset':
            return f"CASE e.ruleset WHEN '5r' THEN 0 WHEN '5e' THEN 1 ELSE 2 END {direction}, {tie}"
        if key == 'source':
            return f'lower(e.source_code) {direction}, {tie}'
        if key == 'cr':
            return f'CASE WHEN e.cr_num IS NULL THEN 1 ELSE 0 END ASC, e.cr_num {direction}, {tie}'
        if key in otypes.COLUMN_LABELS or key in otypes.LABELS:
            return (
                f"CASE WHEN browse_rank(e.fields_json, '{key}') IS NULL THEN 1 ELSE 0 END ASC, "
                f"browse_rank(e.fields_json, '{key}') {direction}, {tie}"
            )
        return f'e.sort_name {direction}, e.id ASC'

    def _row_to_entry(self, row: sqlite3.Row) -> dict[str, Any]:
        fields = _json_loads(row['fields_json'], {})
        quickref = _json_loads(row['quickref_json'], {})
        conflicts = _json_loads(row['conflicts_json'], [])
        provenance = _json_loads(row['provenance_json'], {})
        sid = row['source_id'] or ''
        srec = self._sources_map.get(sid)
        sname = (srec['name'] if srec else None) or provenance.get('source_name') or row['source_code'] or sid
        slabel = (srec['short_name'] if srec else None) or row['source_code'] or sid
        origin = (srec['origin'] if srec else None) or provenance.get('origin') or '社区'
        col_key = str(provenance.get('collection') or self.collection_key)
        provenance.setdefault('collection', col_key)

        entry: dict[str, Any] = {
            'id': row['id'],
            'collection': col_key,
            'type': row['type'],
            'type_label': otypes.TYPES.get(row['type'], row['type']),
            'raw_kind': row['raw_kind'],
            'name': row['name'],
            'name_zh': row['name_zh'] or row['name'],
            'name_en': row['name_en'] or '',
            'ruleset': row['ruleset'] or '待核',
            'legacy': row['legacy'] or 'unmarked',
            'package_id': row['package_id'],
            'source_id': sid,
            'source_code': row['source_code'] or '',
            'source_ids': [sid] if sid else [],
            'source_names': [sname] if sname else [],
            'source_short': slabel,
            'origin': origin,
            'owner_class': row['owner_class'] or '',
            'owner_subclass': row['owner_subclass'] or '',
            'owner_level': row['owner_level'],
            'copy_status': row['copy_status'] or 'ok',
            'fields': fields,
            'quickref': quickref,
            'field_conflicts': conflicts,
            'provenance': provenance,
        }
        if not entry['quickref']:
            otypes.enrich(entry)
        return entry

    # --- CatalogProvider methods ---

    def meta(self) -> dict[str, Any]:
        with closing(self._connect()) as db:
            self._ensure_static_cache(db)
            summary_row = db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()
            if summary_row is not None:
                summary = _json_loads(summary_row[0], {})
            elif self.summary_path.is_file():
                summary = _json_loads(self.summary_path.read_text('utf-8'), {})
            else:
                total = db.execute('SELECT count(*) FROM entries').fetchone()[0]
                summary = {'counts': {'entries': total}, 'total': total}

            if 'total' not in summary:
                summary['total'] = (summary.get('counts') or {}).get('entries') or db.execute('SELECT count(*) FROM entries').fetchone()[0]

            src_counts = dict(db.execute('SELECT source_id, count(*) FROM entries GROUP BY source_id'))
            sources_list: list[dict[str, Any]] = []
            for sid, srec in sorted(self._sources_map.items(), key=lambda kv: (kv[1]['name'], kv[0])):
                data = srec['data']
                code = data.get('code') or srec['short_name']
                aliases = list(dict.fromkeys(
                    str(x) for x in (code, srec['short_name'], data.get('abv'), data.get('abbreviation'))
                    if x and str(x) != srec['name']
                ))
                sources_list.append({
                    'id': sid,
                    'name': srec['name'],
                    'short_name': srec['short_name'],
                    'label': srec['short_name'],
                    'aliases': aliases,
                    'coverage': data.get('coverage') or srec['origin'],
                    'count': int(src_counts.get(sid, 0)),
                    'origin': srec['origin'],
                    'package_id': srec['package_id'],
                    'ours': bool(data.get('ours')),
                    'authors': list(data.get('authors') or []),
                    'translators': list(data.get('translators') or []),
                    'tier': data.get('tier'),
                    'ruleset': srec['ruleset'],
                })

            packages_list: list[dict[str, Any]] = []
            for pid, prec in sorted(
                self._packages_map.items(),
                key=lambda kv: (0 if kv[0] == 'official' else (1 if kv[1]['ours'] else 2), kv[1]['name']),
            ):
                pdata = prec['data']
                packages_list.append({
                    'id': pid,
                    'name': prec['name'],
                    'origin': prec['origin'],
                    'ours': prec['ours'],
                    'entry_count': prec['entry_count'],
                    'book_count': prec['book_count'],
                    'groups': list(pdata.get('groups') or []),
                    'edition': pdata.get('edition'),
                    'ruleset': pdata.get('ruleset'),
                })

            fields_labels = {
                **otypes.COLUMN_LABELS,
                'package_kind': '资料包分类',
                'tier': '推荐分级',
                'origin': '来源性质',
                'package': '资料包',
                'copy_status': '继承状态',
            }

            return {
                'summary': summary,
                'types': dict(otypes.TYPES),
                'type_order': [t for t in otypes.TYPE_ORDER if t in self._held_types],
                'fields': fields_labels,
                'sources': sources_list,
                'packages': packages_list,
                'presets': list(otypes.PRESETS),
                'type_conditions': {t: otypes.type_conditions(t) for t in otypes.TYPES},
                'tour_url': self.tour_url,
                'collection': self.collection_key,
                'lazy_media': self.lazy_media,
            }

    def entries(self, params: dict[str, list[str]], restrict: Any = None, *, _export: bool = False) -> dict[str, Any]:
        with closing(self._connect()) as db:
            self._ensure_static_cache(db)
            conds = self._conditions(params, restrict)
            where, values = self._where_from(conds)
            has_search = bool(_search_terms(params, 'q') or _typing_term(params))
            latest = _latest_term(params)
            db.create_function('browse_query', 0, lambda: latest)

            chosen_types = _entry_types(params)
            config = otypes.schema_for(chosen_types)
            order = self._order_sql(params, config['default_sort'], has_search)
            offset, limit = _result_window(params)

            # Support both versions=stacked|each and stack=1|0
            stack_param = _first(params, 'stack')
            versions = _first(params, 'versions')
            if not versions:
                if stack_param in {'0', 'false', 'False'}:
                    versions = 'each'
                else:
                    versions = 'stacked'
            if versions not in {'stacked', 'each'}:
                raise ValueError('无效版本显示方式')

            prefer = _first(params, 'prefer', '5r')
            if prefer not in otypes.RULESET_PREFERENCES:
                raise ValueError('无效规则版本偏好')

            cols = (
                'e.id, e.type, e.raw_kind, e.name, e.name_zh, e.name_en, e.ruleset, e.legacy, '
                'e.package_id, e.source_id, e.source_code, e.owner_class, e.owner_subclass, '
                'e.owner_level, e.copy_status, e.fields_json, e.quickref_json, e.conflicts_json, '
                'e.provenance_json'
            )

            if _export or versions == 'each':
                total = db.execute('SELECT count(*) FROM entries e' + where, values).fetchone()[0]
                sql = f'SELECT {cols} FROM entries e{where} ORDER BY {order}'
                q_vals = list(values)
                if not _export:
                    sql += ' LIMIT ? OFFSET ?'
                    q_vals.extend([limit, offset])
                rows = [self._row_to_entry(r) for r in db.execute(sql, q_vals)]
                return {
                    'total': total,
                    'versions_total': total,
                    'versions': versions,
                    'prefer': prefer,
                    'offset': 0 if _export else offset,
                    'rows': rows,
                    'schema': config,
                    'section_matches': 0,
                }

            first_choice, second_choice = otypes.RULESET_PREFERENCES[prefer]
            matched = db.execute(
                'WITH matched AS ('
                '  SELECT e.id, COALESCE(im.group_id, e.id) AS stack_key, im.group_id AS group_id, '
                '         COALESCE(im.is_primary, 0) AS is_primary, '
                f'         ROW_NUMBER() OVER (ORDER BY {order}) AS pos, '
                '         CASE e.ruleset WHEN ? THEN 0 WHEN ? THEN 1 ELSE 2 END AS pref '
                '  FROM entries e '
                '  LEFT JOIN identity_members im ON im.entry_id = e.id'
                f'{where}'
                ') '
                'SELECT id, stack_key, group_id, pos, '
                '       ROW_NUMBER() OVER (PARTITION BY stack_key ORDER BY pref ASC, is_primary DESC, pos ASC) AS rnk '
                'FROM matched',
                [first_choice, second_choice, *values],
            ).fetchall()

            heads = sorted((r for r in matched if r['rnk'] == 1), key=lambda r: r['pos'])
            total = len(heads)
            shown = heads[offset:offset + limit]
            shown_keys = {r['stack_key'] for r in shown}
            members_by_stack: dict[str, list[str]] = defaultdict(list)
            for r in sorted((r for r in matched if r['stack_key'] in shown_keys), key=lambda r: (r['stack_key'], r['rnk'])):
                members_by_stack[r['stack_key']].append(r['id'])

            all_ids = [eid for ids in members_by_stack.values() for eid in ids]
            fetched: dict[str, dict[str, Any]] = {}
            for start in range(0, len(all_ids), 400):
                batch = all_ids[start:start + 400]
                ph = ','.join('?' for _ in batch)
                for r in db.execute(f'SELECT {cols} FROM entries e WHERE e.id IN ({ph})', batch):
                    fetched[r['id']] = self._row_to_entry(r)

            rows = []
            for head in shown:
                row = fetched[head['id']]
                stacked = [fetched[eid] for eid in members_by_stack[head['stack_key']] if eid in fetched]
                row['versions'] = [{k: v.get(k) for k in VERSION_FIELDS} for v in stacked]
                row['stack_count'] = len(stacked)
                row['group_id'] = head['group_id']
                row['stack'] = (
                    {'id': head['group_id'], 'status': 'reprint_group', 'count': len(stacked)}
                    if head['group_id'] else None
                )
                row['version_diffs'] = otypes.version_diffs(config, row, stacked[1:])
                rows.append(row)

            return {
                'total': total,
                'versions_total': len(matched),
                'versions': versions,
                'prefer': prefer,
                'offset': offset,
                'rows': rows,
                'schema': config,
                'section_matches': 0,
            }

    def _scope_universe(
        self,
        db: sqlite3.Connection,
        chosen_types: list[str],
        domain: list[str],
        scope: list[tuple[str, str, list[Any]]] = (),
    ) -> tuple[int, dict[str, set[str]], dict[str, int]]:
        """Cached (scope_total, facet_key -> set[values], facet_key -> non-unknown count) for a type tuple."""
        cache_key = (tuple(sorted(chosen_types)), tuple((sql, tuple(vals)) for _, sql, vals in scope))
        with self._lock:
            cached = self._scope_universe_cache.get(cache_key)
        if cached is not None:
            return cached

        bounds = [f'({sql})' for _, sql, _ in scope]
        vals_t: list[Any] = [v for _, _, vals in scope for v in vals]
        if chosen_types:
            bounds.append('e.type IN (' + ','.join('?' for _ in chosen_types) + ')')
            vals_t.extend(chosen_types)
        where_t = ' WHERE ' + ' AND '.join(bounds) if bounds else ''

        scope_total = int(db.execute('SELECT count(*) FROM entries e' + where_t, vals_t).fetchone()[0])
        uni: dict[str, set[str]] = {f: set() for f in domain}
        cover: dict[str, int] = {f: 0 for f in domain}
        if domain:
            domain_set = set(domain)
            if where_t:
                sql = (
                    'SELECT ef.facet_key, ef.facet_value, count(*) '
                    'FROM entries e CROSS JOIN entry_facets ef ON ef.entry_id = e.id '
                    f'WHERE ({where_t[7:]}) '
                    'GROUP BY ef.facet_key, ef.facet_value'
                )
                rows_u = db.execute(sql, vals_t)
            else:
                f_ph = ','.join('?' for _ in domain)
                sql = (
                    'SELECT facet_key, facet_value, count(*) FROM entry_facets '
                    f'WHERE facet_key IN ({f_ph}) GROUP BY facet_key, facet_value'
                )
                rows_u = db.execute(sql, domain)
            for fk, fv, cnt in rows_u:
                if fk in domain_set:
                    uni.setdefault(fk, set()).add(fv)
                    if fv != otypes.UNKNOWN:
                        cover[fk] = cover.get(fk, 0) + int(cnt)

        res = (scope_total, uni, cover)
        with self._lock:
            if len(self._scope_universe_cache) > 64:
                self._scope_universe_cache.clear()
            self._scope_universe_cache[cache_key] = res
        return res

    def facets(self, params: dict[str, list[str]], restrict: Any = None) -> dict[str, Any]:
        with closing(self._connect()) as db:
            self._ensure_static_cache(db)
            chosen_types = _entry_types(params)
            config = otypes.schema_for(chosen_types)
            conds = self._conditions(params, restrict)
            scope = self._restriction(restrict)
            if not conds:
                with self._lock:
                    if self._unfiltered_facets_cache is not None:
                        return self._unfiltered_facets_cache
                # Covering-index-only fast path for unfiltered facets (avoids scanning full entries rows)
                total = int(db.execute('SELECT count(*) FROM entries').fetchone()[0])
                type_counts = Counter(dict(db.execute('SELECT type, count(*) FROM entries GROUP BY type')))
                rs_counts = Counter(dict(db.execute('SELECT ruleset, count(*) FROM entries GROUP BY ruleset')))
                src_counts = Counter(dict(db.execute('SELECT source_id, count(*) FROM entries GROUP BY source_id')))
                pkg_counts = Counter(dict(db.execute('SELECT package_id, count(*) FROM entries GROUP BY package_id')))
                or_counts = Counter(dict(db.execute(
                    "SELECT facet_value, count(*) FROM entry_facets WHERE facet_key='origin' GROUP BY facet_value"
                )))
                type_group = {
                    'key': 'type',
                    'label': COMMON_LABELS['type'],
                    'mode': 'multi',
                    'options': [
                        {'value': t, 'label': otypes.TYPES[t], 'count': int(type_counts.get(t, 0))}
                        for t in otypes.TYPE_ORDER if t in self._held_types
                    ],
                }
                rs_group = {
                    'key': 'ruleset',
                    'label': COMMON_LABELS['ruleset'],
                    'mode': 'multi',
                    'options': [{'value': v, 'label': v, 'count': int(rs_counts.get(v, 0))} for v in RULESETS],
                }
                or_group = {
                    'key': 'origin',
                    'label': COMMON_LABELS['origin'],
                    'mode': 'multi',
                    'options': [{'value': v, 'label': v, 'count': int(or_counts.get(v, 0))} for v in ORIGINS],
                }
                src_options = sorted(
                    (
                        {
                            'value': sid,
                            'label': self._sources_map[sid]['short_name'],
                            'title': self._sources_map[sid]['name'],
                            'search': ' '.join(filter(None, [
                                self._sources_map[sid]['name'],
                                self._sources_map[sid]['short_name'],
                                self._sources_map[sid]['data'].get('code'),
                            ])),
                            'count': int(src_counts.get(sid, 0)),
                        }
                        for sid, c in src_counts.items() if c > 0 and sid in self._sources_map
                    ),
                    key=lambda o: (o['label'].lower(), o['title']),
                )
                src_group = {'key': 'source', 'label': COMMON_LABELS['source'], 'mode': 'multi', 'options': src_options}
                pkg_options = sorted(
                    (
                        {
                            'value': pid,
                            'label': self._packages_map[pid]['name'],
                            'title': self._packages_map[pid]['name'],
                            'origin': self._packages_map[pid]['origin'],
                            'ours': self._packages_map[pid]['ours'],
                            'count': int(pkg_counts.get(pid, 0)),
                        }
                        for pid, c in pkg_counts.items() if c > 0 and pid in self._packages_map
                    ),
                    key=lambda o: (0 if o['value'] == 'official' else (1 if o['ours'] else 2), o['label']),
                )
                pkg_group = {'key': 'package', 'label': COMMON_LABELS['package'], 'mode': 'multi', 'options': pkg_options}
                result = {
                    'schema': config,
                    'groups': [type_group, rs_group, or_group, src_group, pkg_group],
                    'total': total,
                    'scope_total': total,
                    'without': {},
                    'elsewhere': [],
                }
                with self._lock:
                    self._unfiltered_facets_cache = result
                return result

            fields_of = [_field_of(k) for k, _, _ in conds]
            active_fields = set(fields_of)
            domain = [*config['facets'], *config['flags']]

            # 1. Cached scope total & domain universe for chosen types
            scope_total, uni_map, unknown_cover = self._scope_universe(db, chosen_types, domain, scope)

            # 2. Fetch matched entries ONCE for total + all unfiltered facet groups
            where_all, vals_all = self._where_from(conds)
            matched_rows = db.execute(
                'SELECT e.id, e.ruleset, e.source_id, e.package_id, e.type FROM entries e' + where_all,
                vals_all,
            ).fetchall()
            total = len(matched_rows)
            matched_ids = [r[0] for r in matched_rows]

            def _conds_without_field(target_field: str) -> tuple[str, list[Any]]:
                sub = [c for c, f in zip(conds, fields_of) if f != target_field]
                return self._where_from(sub)

            def _count_facets_for_ids(facet_keys: list[str], ids: list[str]) -> dict[str, Counter[str]]:
                out: dict[str, Counter[str]] = {fk: Counter() for fk in facet_keys}
                if not facet_keys or not ids:
                    return out
                key_set = set(facet_keys)
                if len(ids) <= 8000:
                    for start in range(0, len(ids), 800):
                        batch = ids[start:start + 800]
                        id_ph = ','.join('?' for _ in batch)
                        sql = (
                            f'SELECT facet_key, facet_value, count(*) FROM entry_facets '
                            f'WHERE entry_id IN ({id_ph}) '
                            f'GROUP BY facet_key, facet_value'
                        )
                        for fk, fv, cnt in db.execute(sql, batch):
                            if fk in key_set:
                                out[fk][fv] += int(cnt)
                else:
                    sql = (
                        'SELECT ef.facet_key, ef.facet_value, count(*) '
                        'FROM entries e CROSS JOIN entry_facets ef ON ef.entry_id = e.id '
                        + (f'WHERE ({where_all[7:]}) ' if where_all else '')
                        + 'GROUP BY ef.facet_key, ef.facet_value'
                    )
                    for fk, fv, cnt in db.execute(sql, vals_all):
                        if fk in key_set:
                            out[fk][fv] += int(cnt)
                return out

            # 3. Type counts (reuse matched_rows when type & type-bound filters are inactive!)
            type_bound_indices = [
                (i, f[2:] if f.startswith('f_') else 'cr')
                for i, f in enumerate(fields_of)
                if f.startswith('f_') or f in {'cr_min', 'cr_max'}
            ]
            non_type_non_bound = [
                c for c, f in zip(conds, fields_of)
                if f != 'type' and not (f.startswith('f_') or f in {'cr_min', 'cr_max'})
            ]
            type_counts: Counter[str] = Counter()
            if 'type' not in active_fields and not type_bound_indices:
                type_counts = Counter(r[4] for r in matched_rows)
            elif not type_bound_indices:
                w_t, v_t = self._where_from(non_type_non_bound)
                for r in db.execute('SELECT e.type, count(*) FROM entries e' + w_t + ' GROUP BY e.type', v_t):
                    type_counts[r[0]] = int(r[1])
            else:
                answers = {t: set(otypes.type_conditions(t)) for t in self._held_types}
                by_subset: dict[tuple[int, ...], list[str]] = defaultdict(list)
                for t in self._held_types:
                    applicable = tuple(i for i, fname in type_bound_indices if fname in answers[t])
                    by_subset[applicable].append(t)
                for applicable, t_list in by_subset.items():
                    ph = ','.join('?' for _ in t_list)
                    sub = [('type_bucket', f'e.type IN ({ph})', t_list), *non_type_non_bound]
                    for idx in applicable:
                        sub.append(conds[idx])
                    w_sub, v_sub = self._where_from(sub)
                    for r in db.execute('SELECT e.type, count(*) FROM entries e' + w_sub + ' GROUP BY e.type', v_sub):
                        type_counts[r[0]] = int(r[1])

            # 4. `without`: additional results gained when removing one condition
            without: dict[str, int] = {}
            field_counts_once = Counter(fields_of)
            for i, (ckey, _, _) in enumerate(conds):
                if ckey == SCOPE_KEY:
                    continue
                f_i = fields_of[i]
                if f_i == 'type' and field_counts_once['type'] == 1 and not type_bound_indices:
                    diff = sum(type_counts.values()) - total
                    if diff > 0:
                        without[ckey] = diff
                    continue
                sub_conds = conds[:i] + conds[i + 1:]
                w_sub, v_sub = self._where_from(sub_conds)
                c_without = int(db.execute('SELECT count(*) FROM entries e' + w_sub, v_sub).fetchone()[0])
                diff = c_without - total
                if diff > 0:
                    without[ckey] = diff

            # 5. Domain facets & flags: batch ALL unfiltered facets into ONE query!
            unfiltered_domain = [f for f in domain if ('f_' + f) not in active_fields]
            filtered_domain = [f for f in domain if ('f_' + f) in active_fields]
            batched_counts = _count_facets_for_ids(unfiltered_domain, matched_ids)

            for f in filtered_domain:
                fkey = 'f_' + f
                w_f, v_f = _conds_without_field(fkey)
                f_counts: Counter[str] = Counter()
                sql_counts = (
                    'SELECT ef.facet_value, count(*) FROM entries e '
                    'CROSS JOIN entry_facets ef ON ef.entry_id = e.id '
                    'WHERE ef.facet_key = ?'
                    + (f' AND ({w_f[7:]})' if w_f else '')
                    + ' GROUP BY ef.facet_value'
                )
                for fv, cnt in db.execute(sql_counts, [f, *v_f]):
                    f_counts[fv] = int(cnt)
                batched_counts[f] = f_counts

            domain_groups: list[dict[str, Any]] = []
            for f in domain:
                fkey = 'f_' + f
                f_counts = batched_counts.get(f, Counter())
                f_universe = set(uni_map.get(f, set())) | set(f_counts.keys())
                for v in (*params.get(fkey, []), *params.get(NOT + fkey, [])):
                    if v and v != otypes.NONE:
                        f_universe.add(v)
                options = [
                    {
                        'value': v,
                        'label': otypes.option_label(f, v),
                        'count': int(f_counts.get(v, 0)),
                    }
                    for v in sorted(f_universe, key=lambda val: otypes.option_order(f, val))
                ]
                mode = 'flag' if f in config['flags'] else 'multi'
                domain_groups.append({
                    'key': fkey,
                    'label': otypes.LABELS.get(f, f),
                    'mode': mode,
                    'options': options,
                })

            if len(config['kinds']) > 1:
                domain_groups.sort(key=lambda g: -unknown_cover.get(g['key'][2:], 0))

            # 6. Ruleset, Origin, Source, Package counts (reuse matched_rows whenever unfiltered!)
            if 'ruleset' not in active_fields:
                rs_counts = Counter(r[1] for r in matched_rows)
            else:
                w_rs, v_rs = _conds_without_field('ruleset')
                rs_counts = Counter(dict(db.execute('SELECT e.ruleset, count(*) FROM entries e' + w_rs + ' GROUP BY e.ruleset', v_rs)))
            rs_group = {
                'key': 'ruleset',
                'label': COMMON_LABELS['ruleset'],
                'mode': 'multi',
                'options': [{'value': v, 'label': v, 'count': int(rs_counts.get(v, 0))} for v in RULESETS],
            }

            if 'origin' not in active_fields:
                or_counts = Counter(
                    self._sources_map[r[2]]['origin']
                    for r in matched_rows if r[2] in self._sources_map
                )
            else:
                w_or, v_or = _conds_without_field('origin')
                or_counts = Counter(dict(db.execute(
                    "SELECT ef.facet_value, count(*) FROM entry_facets ef JOIN entries e ON e.id=ef.entry_id "
                    "WHERE ef.facet_key='origin'" + (f' AND ({w_or[7:]})' if w_or else '') + ' GROUP BY ef.facet_value',
                    v_or,
                )))
            or_group = {
                'key': 'origin',
                'label': COMMON_LABELS['origin'],
                'mode': 'multi',
                'options': [{'value': v, 'label': v, 'count': int(or_counts.get(v, 0))} for v in ORIGINS],
            }

            if 'source' not in active_fields:
                src_counts = Counter(r[2] for r in matched_rows if r[2])
            else:
                w_src, v_src = _conds_without_field('source')
                src_counts = Counter(dict(db.execute('SELECT e.source_id, count(*) FROM entries e' + w_src + ' GROUP BY e.source_id', v_src)))
            src_universe = {s for s, c in src_counts.items() if c > 0}
            for v in (*params.get('source', []), *params.get(NOT + 'source', [])):
                if v and v != otypes.NONE:
                    src_universe.add(v)
            src_options = sorted(
                (
                    {
                        'value': sid,
                        'label': self._sources_map[sid]['short_name'],
                        'title': self._sources_map[sid]['name'],
                        'search': ' '.join(filter(None, [
                            self._sources_map[sid]['name'],
                            self._sources_map[sid]['short_name'],
                            self._sources_map[sid]['data'].get('code'),
                        ])),
                        'count': int(src_counts.get(sid, 0)),
                    }
                    for sid in src_universe if sid in self._sources_map
                ),
                key=lambda o: (o['label'].lower(), o['title']),
            )
            src_group = {
                'key': 'source',
                'label': COMMON_LABELS['source'],
                'mode': 'multi',
                'options': src_options,
            }

            if 'package' not in active_fields:
                pkg_counts = Counter(r[3] for r in matched_rows if r[3])
            else:
                w_pkg, v_pkg = _conds_without_field('package')
                pkg_counts = Counter(dict(db.execute('SELECT e.package_id, count(*) FROM entries e' + w_pkg + ' GROUP BY e.package_id', v_pkg)))
            pkg_universe = {p for p, c in pkg_counts.items() if c > 0}
            for v in (*params.get('package', []), *params.get(NOT + 'package', [])):
                if v and v != otypes.NONE:
                    pkg_universe.add(v)
            pkg_options = sorted(
                (
                    {
                        'value': pid,
                        'label': self._packages_map[pid]['name'],
                        'title': self._packages_map[pid]['name'],
                        'origin': self._packages_map[pid]['origin'],
                        'ours': self._packages_map[pid]['ours'],
                        'count': int(pkg_counts.get(pid, 0)),
                    }
                    for pid in pkg_universe if pid in self._packages_map
                ),
                key=lambda o: (0 if o['value'] == 'official' else (1 if o['ours'] else 2), o['label']),
            )
            pkg_group = {
                'key': 'package',
                'label': COMMON_LABELS['package'],
                'mode': 'multi',
                'options': pkg_options,
            }

            type_group = {
                'key': 'type',
                'label': COMMON_LABELS['type'],
                'mode': 'multi',
                'options': [
                    {'value': t, 'label': otypes.TYPES[t], 'count': int(type_counts.get(t, 0))}
                    for t in otypes.TYPE_ORDER if t in self._held_types
                ],
            }

            # 7. Elsewhere hints when current narrowed query returns 0 rows
            elsewhere: list[dict[str, Any]] = []
            if total == 0 and conds:
                for relax_field, label in (
                    ('source', '放宽来源限制'),
                    ('package', '放宽资料包限制'),
                    ('type', '放宽类型限制'),
                    ('ruleset', '放宽规则版本'),
                ):
                    if relax_field in active_fields:
                        w_rel, v_rel = _conds_without_field(relax_field)
                        rel_cnt = int(db.execute('SELECT count(*) FROM entries e' + w_rel, v_rel).fetchone()[0])
                        if rel_cnt > 0:
                            elsewhere.append({'field': relax_field, 'label': label, 'count': rel_cnt})

            return {
                'schema': config,
                'groups': [type_group, *domain_groups, rs_group, or_group, src_group, pkg_group],
                'total': total,
                'scope_total': scope_total,
                'without': without,
                'elsewhere': elsewhere,
            }

    # --- Library federation: works, cheap ranking rows and full rows by ID ---

    def works(self) -> list[dict[str, Any]]:
        """Every work with its origin, rules generation and entry count: what the reader's library can include."""
        self._load_reader_index()
        with closing(self._connect()) as db:
            self._ensure_static_cache(db)
            counts = dict(db.execute('SELECT source_id, count(*) FROM entries GROUP BY source_id'))
            out = []
            for sid, srec in sorted(self._sources_map.items(), key=lambda kv: (kv[1]['name'], kv[0])):
                package = self._packages_map.get(srec['package_id'])
                data = srec['data']
                out.append({
                    'id': sid, 'name': srec['name'], 'label': srec['short_name'], 'origin': srec['origin'],
                    'ruleset': srec['ruleset'] if srec['ruleset'] in {'5e', '5r'} else '待核',
                    'group': package['name'] if package else srec['package_id'],
                    'count': int(counts.get(sid, 0)),
                    # The names another collection may know this work by: its 5etools code and short label.
                    'codes': [data['code']] if data.get('code') else [],
                    'abbreviations': [srec['short_name']] if srec['short_name'] else [],
                    'titles': list(dict.fromkeys(t for t in (srec['name'], data.get('code')) if t)),
                    # The Stella root that opens this work's book, if it has one.
                    'reader_root': self._reader_roots.get(sid),
                })
            return out

    def ranked(self, params: dict[str, list[str]], restrict: Any = None, sort_field: str | None = None) -> list[dict[str, Any]]:
        """One cheap row per matching entry: what a library spanning several catalogs sorts, stacks and pages."""
        with closing(self._connect()) as db:
            self._ensure_static_cache(db)
            where, values = self._where_from(self._conditions(params, restrict))
            latest = _latest_term(params)
            db.create_function('browse_query', 0, lambda: latest)
            if sort_field and sort_field not in otypes.COLUMN_LABELS and sort_field not in otypes.LABELS:
                sort_field = None
            value = 'e.cr_num' if sort_field == 'cr' else (f"browse_rank(e.fields_json, '{sort_field}')" if sort_field else 'NULL')
            sql = (f'SELECT e.id, e.type, e.name, e.name_zh, e.name_en, e.ruleset, e.source_id, e.source_code, im.group_id, {value} AS value '
                   f'FROM entries e LEFT JOIN identity_members im ON im.entry_id = e.id{where}')
            out = []
            for r in db.execute(sql, values):
                source = self._sources_map.get(r['source_id'])
                base, base_work, base_name_en = self._bases.get(r['id'], (None, None, None))
                out.append({'id': r['id'], 'type': r['type'], 'name': r['name'], 'name_zh': r['name_zh'], 'name_en': r['name_en'],
                            'ruleset': r['ruleset'] or '待核', 'stack': r['group_id'], 'value': r['value'],
                            'source_key': ((r['source_code'] or '').lower(),), 'origin': source['origin'] if source else '社区',
                            'work': r['source_id'] or None, 'base': base, 'base_work': base_work, 'base_name_en': base_name_en})
            return out

    def rows(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        cols = (
            'e.id, e.type, e.raw_kind, e.name, e.name_zh, e.name_en, e.ruleset, e.legacy, '
            'e.package_id, e.source_id, e.source_code, e.owner_class, e.owner_subclass, '
            'e.owner_level, e.copy_status, e.fields_json, e.quickref_json, e.conflicts_json, '
            'e.provenance_json'
        )
        out: dict[str, dict[str, Any]] = {}
        ids = list(dict.fromkeys(ids))
        with closing(self._connect()) as db:
            self._ensure_static_cache(db)
            for start in range(0, len(ids), 400):
                batch = ids[start:start + 400]
                ph = ','.join('?' for _ in batch)
                for r in db.execute(f'SELECT {cols} FROM entries e WHERE e.id IN ({ph})', batch):
                    out[r['id']] = self._row_to_entry(r)
        return out

    def schema(self, types: list[str]) -> dict[str, Any]:
        return otypes.schema_for([t for t in types if t in otypes.TYPES])

    def capabilities(self) -> Any:
        from anna.presentation.collections import Capabilities
        meta = self.meta()
        return Capabilities(
            conditions=frozenset({'type', 'q', 'typing', 'body', 'ruleset', 'legacy', 'copy_status', 'origin', 'source',
                                  'package', 'conflict', 'cr_min', 'cr_max'}),
            facets=frozenset(otypes.LABELS), types=frozenset(meta['type_order']),
            sources=frozenset(s['id'] for s in meta['sources']), packages=frozenset(p['id'] for p in meta['packages']),
            values={'ruleset': frozenset(RULESETS), 'legacy': frozenset(LEGACIES), 'copy_status': frozenset(COPY_STATUSES),
                    'origin': frozenset(ORIGINS)},
            stackable=frozenset(otypes.STACKABLE_TYPES),
            # Identity groups gather recorded reprints only; same-name entries of other works are the library's to stack.
            same_name_groups=False,
        )

    def signature(self) -> tuple:
        return (str(self.db_path), *(_file_sig(self.db_path) or ()))

    def detail(self, entry_id: str) -> dict[str, Any]:
        if not entry_id:
            raise LookupError('条目不存在')
        with closing(self._connect()) as db:
            self._ensure_static_cache(db)
            row = db.execute(
                'SELECT id, type, raw_kind, name, name_zh, name_en, ruleset, legacy, '
                'package_id, source_id, source_code, owner_class, owner_subclass, owner_level, '
                'copy_status, fields_json, quickref_json, conflicts_json, provenance_json, body_text '
                'FROM entries WHERE id = ?',
                (entry_id,),
            ).fetchone()
            if row is None:
                raise LookupError('条目不存在')

            entry = self._row_to_entry(row)
            entry['body'] = (row['body_text'] or '').strip()
            entry['field_order'] = otypes.field_order(entry)

            # Enrich source_ids / source_names from entry_sources
            es_rows = db.execute('SELECT source_id FROM entry_sources WHERE entry_id = ? ORDER BY source_id', (entry_id,)).fetchall()
            if es_rows:
                sids = [r[0] for r in es_rows]
                entry['source_ids'] = sids
                entry['source_names'] = [
                    self._sources_map[s]['name'] if s in self._sources_map else s
                    for s in sids
                ]

            # Identity group / version comparison
            im_row = db.execute('SELECT group_id FROM identity_members WHERE entry_id = ?', (entry_id,)).fetchone()
            members_compact: list[dict[str, Any]] = []
            versions_compare: dict[str, Any] | None = None
            if im_row is not None:
                gid = im_row['group_id']
                mem_rows = db.execute(
                    'SELECT e.id, e.type, e.raw_kind, e.name, e.name_zh, e.name_en, e.ruleset, e.legacy, '
                    'e.package_id, e.source_id, e.source_code, e.owner_class, e.owner_subclass, e.owner_level, '
                    'e.copy_status, e.fields_json, e.quickref_json, e.conflicts_json, e.provenance_json '
                    'FROM identity_members im JOIN entries e ON e.id = im.entry_id '
                    'WHERE im.group_id = ? '
                    "ORDER BY CASE e.ruleset WHEN '5r' THEN 0 WHEN '5e' THEN 1 ELSE 2 END, im.is_primary DESC, e.id ASC",
                    (gid,),
                ).fetchall()
                mem_entries = [self._row_to_entry(mr) for mr in mem_rows]
                order = list(dict.fromkeys(
                    k for m in mem_entries for k in otypes.field_order(m)
                    if k not in otypes.METADATA_FIELDS
                ))
                diff_fields = []
                for k in order:
                    known = {otypes.comparable(m['fields'], k) for m in mem_entries} - {None}
                    diff_fields.append({
                        'field': k,
                        'values': [m['fields'].get(k) for m in mem_entries],
                        'differs': len(known) > 1,
                    })
                members_compact = [
                    {k: m.get(k) for k in ('id', 'name', 'name_zh', 'name_en', 'ruleset', 'source_ids', 'source_names', 'legacy', 'fields', 'quickref', 'package_id')}
                    for m in mem_entries
                ]
                versions_compare = {
                    'group_id': gid,
                    'status': 'reprint_group',
                    'members': members_compact,
                    'fields': diff_fields,
                }
            entry['members'] = members_compact
            entry['versions'] = versions_compare

            # Relations (outgoing, enriched with target name/type/source_code)
            rel_rows = db.execute(
                'SELECT r.type AS rel_type, r.target_id, r.meta_json, '
                't.name AS target_name, t.type AS target_type, t.source_code AS target_source_code '
                'FROM relations r LEFT JOIN entries t ON t.id = r.target_id '
                'WHERE r.source_id = ? ORDER BY r.type, t.name, r.target_id',
                (entry_id,),
            ).fetchall()
            relations_out: list[dict[str, Any]] = []
            for rr in rel_rows:
                t_type = rr['target_type'] or ''
                relations_out.append({
                    'type': rr['rel_type'],
                    'predicate': rr['rel_type'],
                    'target_id': rr['target_id'],
                    'object_id': rr['target_id'],
                    'name': rr['target_name'] or rr['target_id'],
                    'target_label': rr['target_name'] or rr['target_id'],
                    'target_type': t_type,
                    'target_type_label': otypes.TYPES.get(t_type, t_type),
                    'source_code': rr['target_source_code'] or '',
                    'status': 'ok' if rr['target_name'] else 'missing',
                    'meta': _json_loads(rr['meta_json'], {}),
                })
            entry['relations'] = relations_out

            # Reader location from provenance or anna-reader.json
            loc = entry['provenance'].get('reader_location')
            if not loc:
                loc = self._get_entry_locations().get(entry_id)
            entry['reader_location'] = loc

            return entry

    def layout(self, entry_id: str) -> dict[str, Any]:
        if not entry_id:
            raise LookupError('条目不存在')
        with closing(self._connect()) as db:
            row = db.execute(
                'SELECT layout_html, provenance_json FROM entries WHERE id = ?',
                (entry_id,),
            ).fetchone()
            if row is None:
                raise LookupError('条目不存在')
            html_body = row['layout_html'] or ''
            prov = _json_loads(row['provenance_json'], {})
            if not html_body:
                return {'status': 'empty', 'media': prov.get('media_summary')}
            promoted = promote_media_html(html_body, self.media_dir, lazy_media=self.lazy_media)
            return {'status': 'ok', 'html': promoted, 'media': prov.get('media_summary')}

    def export(self, params: dict[str, list[str]], restrict: Any = None) -> bytes:
        result = self.entries(params, restrict, _export=True)
        rows = result['rows']
        config = result['schema']
        cols_cfg = config.get('export_columns') or config.get('columns') or []
        extras = [f for f in cols_cfg if f not in otypes.IDENTITY_COLUMNS]

        buf = io.StringIO(newline='')
        writer = csv.writer(buf)
        writer.writerow(
            ['条目ID', '中文名', '英文名', '类型', '规则版本', '资料库', '来源', '资料包', '来源性质', '继承状态', '字段冲突']
            + [otypes.COLUMN_LABELS.get(f, f) for f in extras]
        )
        for r in rows:
            cells: list[Any] = [
                r['id'],
                r['name_zh'],
                r['name_en'],
                otypes.TYPES.get(r['type'], r['type']),
                r['ruleset'],
                r.get('collection') or self.collection_key,
                '；'.join(r.get('source_names') or []),
                r.get('package_id', ''),
                r.get('origin', ''),
                r.get('copy_status', 'ok'),
                '；'.join(r.get('field_conflicts') or []),
            ]
            for f in extras:
                val = r['fields'].get(f)
                if val is None:
                    cells.append('')
                elif isinstance(val, (list, dict)):
                    cells.append(json.dumps(val, ensure_ascii=False))
                else:
                    cells.append(str(val))
            safe_cells = ["'" + s if s.lstrip().startswith(('=', '+', '-', '@')) else s for s in map(str, cells)]
            writer.writerow(safe_cells)
        return ('\ufeff' + buf.getvalue()).encode('utf-8')


# ---------------------------------------------------------------------------
# 2. Our5etoolsTour
# ---------------------------------------------------------------------------

class Our5etoolsTour:
    """The levels of the tour in one our5etools snapshot: its contents, books, packages and entries.

    The library tour keeps the curated first column, the shelf and the topics, and asks this one for the levels
    below them, named by this collection's own segments (`n…`, `book.…`, `pack.…`, `o5e_…`) and for the package
    listings its dynamic topics name."""

    def __init__(
        self,
        data_dir: Path | str,
        curation_path: Path | str | None = None,
        *,
        lazy_media: bool = False,
        collection_key: str = COLLECTION_KEY,
    ):
        self.data_dir = Path(data_dir).resolve()
        self.collection_key = (collection_key or COLLECTION_KEY).strip() or COLLECTION_KEY
        self.db_path = self.data_dir / 'catalog.sqlite'
        self.reader_path = self.data_dir / 'reader' / 'anna-reader.json'
        self.curation_path = Path(curation_path).resolve() if curation_path else tour_curation.CURATION
        self.lazy_media = bool(lazy_media)
        self.catalog = Our5etoolsCatalog(
            self.data_dir,
            lazy_media=self.lazy_media,
            collection_key=self.collection_key,
        )
        self._lock = threading.Lock()
        self._loaded_sig: tuple[Any, ...] | None = None
        self._snapshot: str = ''
        self._topics: dict[str, Any] = {}
        self._nodes: dict[str, dict[str, Any]] = {}
        self._children: dict[str, list[str]] = defaultdict(list)
        self._label_path: dict[str, str] = {}
        self._by_label_path: dict[str, list[str]] = defaultdict(list)
        self._entry_locations: dict[str, dict[str, Any]] = {}
        self._books_by_slug: dict[str, dict[str, Any]] = {}
        self._books_by_node: dict[str, str] = {}
        self._books_order: list[str] = []
        self._packs_by_slug: dict[str, dict[str, Any]] = {}
        self._packs_by_id: dict[str, str] = {}
        self._packs_order: list[str] = []
        self._sources_by_id: dict[str, dict[str, Any]] = {}
        self._choices_by_node: dict[str, list[str]] = defaultdict(list)
        self._subclass_records: list[dict[str, Any]] = []

    @property
    def snapshot(self) -> str:
        self._ensure_loaded()
        return self._snapshot

    def _ensure_loaded(self) -> None:
        sig = (_file_sig(self.db_path), _file_sig(self.reader_path), _file_sig(self.curation_path))
        with self._lock:
            if sig == self._loaded_sig and self._loaded_sig is not None:
                return
            self._reload_locked()
            self._loaded_sig = sig

    def _reload_locked(self) -> None:
        # 1. The curated topics this collection's levels name: choices at its contents nodes, dynamic listings.
        curation = tour_curation.load(self.curation_path)
        self._topics = curation['topics']

        # 2. Load reader navigation tree
        reader = json.loads(self.reader_path.read_text('utf-8')) if self.reader_path.is_file() else {}
        self._snapshot = str(reader.get('snapshot') or '')
        self._entry_locations = reader.get('entry_locations') if isinstance(reader.get('entry_locations'), dict) else {}
        raw_nodes = reader.get('nodes') or []
        self._nodes = {n['id']: n for n in raw_nodes}
        self._children = defaultdict(list)
        self._label_path = {}
        self._by_label_path = defaultdict(list)
        for n in raw_nodes:
            pid = n.get('parent_id') or ''
            self._children[pid].append(n['id'])
            parent_lp = self._label_path.get(pid)
            lp = f"{parent_lp} / {n['label']}" if parent_lp else n['label']
            self._label_path[n['id']] = lp
            self._by_label_path[lp].append(n['id'])

        self._choices_by_node = defaultdict(list)
        for where, keys in curation['choices'].get(self.collection_key, {}).items():
            hits = self._by_label_path.get(where, [])
            if len(hits) == 1:
                self._choices_by_node[hits[0]].extend(keys)

        # 3. Load packages, sources, and books from catalog.sqlite + reader nodes
        self._sources_by_id = {}
        self._packs_by_slug = {}
        self._packs_by_id = {}
        self._packs_order = []
        self._books_by_slug = {}
        self._books_by_node = {}
        self._books_order = []

        with closing(_connect_ro(self.db_path)) as db:
            for r in db.execute('SELECT id, package_id, origin, ruleset, short_name, name, data_json FROM sources'):
                self._sources_by_id[r['id']] = {
                    'id': r['id'],
                    'package_id': r['package_id'],
                    'origin': r['origin'],
                    'ruleset': r['ruleset'],
                    'short_name': r['short_name'],
                    'name': r['name'],
                    'data': _json_loads(r['data_json'], {}),
                }

            used_pack_slugs: set[str] = set()
            for r in db.execute(
                'SELECT id, name, origin, ours, entry_count, book_count, data_json FROM packages '
                'ORDER BY CASE WHEN id="official" THEN 0 WHEN ours=1 THEN 1 ELSE 2 END, name, id'
            ):
                pid = r['id']
                base = _slug(pid.replace('brew:', '').replace('site:', '').replace('.json', ''), 'pack')
                slug = base
                idx = 2
                while slug in used_pack_slugs:
                    slug = f'{base}-{idx}'
                    idx += 1
                used_pack_slugs.add(slug)
                pdata = _json_loads(r['data_json'], {})
                rec = {
                    'slug': slug,
                    'id': pid,
                    'name': r['name'] or pid,
                    'origin': r['origin'] or '社区',
                    'ours': bool(r['ours']),
                    'entry_count': int(r['entry_count'] or 0),
                    'book_count': int(r['book_count'] or 0),
                    'data': pdata,
                    'book_slugs': [],
                }
                self._packs_by_slug[slug] = rec
                self._packs_by_id[pid] = slug
                self._packs_order.append(slug)

            # Build book list from reader top-level non-divider nodes (preserving shelf categories)
            bp_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in db.execute(
                'SELECT page_path, root_id, book_id, kind, package_id, source_id, title, node_id FROM book_pages ORDER BY rowid'
            ):
                bp_by_root[r['root_id']].append(dict(r))

            used_book_slugs: set[str] = set()
            current_shelf = 'books_official'
            shelf_map = {
                '官方规则书': 'books_official',
                '官方冒险模组': 'adventures_official',
                '社区与扩展书籍': 'books_brew',
                '社区与扩展冒险': 'adventures_brew',
                '本站科普与特辑': 'books_site',
            }
            for nid in self._children.get('', []):
                node = self._nodes[nid]
                div_name = divider(node)
                if div_name is not None:
                    current_shelf = shelf_map.get(div_name, current_shelf)
                    continue
                meta = node.get('meta') or {}
                pages = bp_by_root.get(nid, [])
                first_page = pages[0] if pages else {}
                book_id = str(meta.get('book_id') or first_page.get('book_id') or nid)
                kind = str(meta.get('kind') or first_page.get('kind') or ('adventure' if 'adventure' in current_shelf else 'book'))
                pkg_id = str(meta.get('package_id') or first_page.get('package_id') or 'official')
                src_code = str(meta.get('source_code') or '')
                src_id = str(first_page.get('source_id') or (f'src:{src_code}' if src_code else ''))
                if src_id and src_id not in self._sources_by_id:
                    # Match by code within package if scoped
                    for sid, srec in self._sources_by_id.items():
                        if srec['package_id'] == pkg_id and (srec['data'].get('code') == src_code or srec['short_name'] == src_code):
                            src_id = sid
                            break
                edition = meta.get('edition')
                ruleset = '5r' if edition == 'one' else ('5e' if edition == 'classic' else (self._sources_by_id.get(src_id, {}).get('ruleset') or '待核'))

                base = _slug(f'{kind}-{book_id}', _slug(nid, 'book'))
                slug = base
                idx = 2
                while slug in used_book_slugs:
                    slug = f'{base}-{idx}'
                    idx += 1
                used_book_slugs.add(slug)

                chapter_ids = [cid for cid in self._children.get(nid, []) if divider(self._nodes[cid]) is None]
                brec = {
                    'slug': slug,
                    'node_id': nid,
                    'book_id': book_id,
                    'title': node['label'],
                    'kind': kind,
                    'shelf': current_shelf,
                    'package_id': pkg_id,
                    'source_id': src_id if src_id in self._sources_by_id else '',
                    'source_code': src_code,
                    'ruleset': ruleset,
                    'chapters': chapter_ids,
                    'page_count': len(pages) or len(chapter_ids),
                    'first_page': (self._nodes[chapter_ids[0]]['path'] if chapter_ids else node.get('path') or ''),
                }
                self._books_by_slug[slug] = brec
                self._books_by_node[nid] = slug
                self._books_order.append(slug)
                pslug = self._packs_by_id.get(pkg_id)
                if pslug:
                    self._packs_by_slug[pslug]['book_slugs'].append(slug)

            # Subclass records for the unified library tour's subclass index.
            parts_counts = dict(db.execute(
                "SELECT r.target_id, count(*) FROM relations r JOIN entries f ON f.id = r.source_id "
                "WHERE r.type = 'part_of' AND f.type = 'class_feature' GROUP BY r.target_id"
            ))
            stacks = dict(db.execute('SELECT entry_id, group_id FROM identity_members'))
            copies = {r[0] for r in db.execute("SELECT DISTINCT source_id FROM relations WHERE type = 'copied_from'")}
            subclass_records: list[dict[str, Any]] = []
            for r in db.execute(
                "SELECT id, name, name_zh, name_en, owner_class, ruleset, source_id, source_code "
                "FROM entries WHERE type = 'subclass' ORDER BY sort_name, id"
            ):
                sid = r['source_id'] or ''
                srec = self._sources_by_id.get(sid)
                subclass_records.append({
                    'id': r['id'],
                    'title': r['name_zh'] or r['name'],
                    'name_en': r['name_en'] or '',
                    'owner_class': (r['owner_class'] or '').strip() or None,
                    'ruleset': r['ruleset'] or '待核',
                    'source_ids': [sid] if sid else [],
                    'source_name': (srec['name'] if srec else '') or r['source_code'] or '',
                    'source_short': (srec['short_name'] if srec else '') or r['source_code'] or '',
                    'origin': (srec['origin'] if srec else '') or '社区',
                    'parts_count': int(parts_counts.get(r['id'], 0)),
                    'stack': stacks.get(r['id']),
                    'is_copy': r['id'] in copies,
                })
            self._subclass_records = subclass_records

    # --- What the library tour asks of a collection ---

    def subclass_records(self) -> list[dict[str, Any]]:
        self._ensure_loaded()
        return self._subclass_records

    def resolve(self, label_path: str) -> tuple[str, str | None]:
        """A curated contents path names exactly one node, or reports why it does not."""
        self._ensure_loaded()
        hits = self._by_label_path.get(label_path, [])
        return ('ok', hits[0]) if len(hits) == 1 else ('missing' if not hits else 'ambiguous', None)

    def below(self, nid: str) -> int | None:
        """How many contents rows a node opens onto."""
        self._ensure_loaded()
        return sum(divider(self._nodes[c]) is None for c in self._children.get(nid, [])) or None

    def _query_count(self, query: dict[str, list[str]]) -> int:
        with closing(_connect_ro(self.db_path)) as db:
            conds = self.catalog._conditions(query)
            where, vals = self.catalog._where_from(conds)
            return int(db.execute('SELECT count(*) FROM entries e' + where, vals).fetchone()[0])

    @staticmethod
    def _scope_query(source: str | None, package: str | None) -> dict[str, list[str]]:
        """A book's entries by its source, a package's by the package."""
        return {'source': [source]} if source else {'package': [package]} if package else {}

    def _type_items(self, scope: dict[str, list[str]]) -> list[dict[str, Any]]:
        with closing(_connect_ro(self.db_path)) as db:
            conds = self.catalog._conditions(scope)
            where, vals = self.catalog._where_from(conds)
            counts = dict(db.execute('SELECT e.type, count(*) FROM entries e' + where + ' GROUP BY e.type', vals))
        return [{'id': None, 'kind': 'query', 'title': otypes.TYPES[t], 'type': t, 'count': int(counts[t]),
                 'query': {**scope, 'type': [t]}} for t in otypes.TYPE_ORDER if int(counts.get(t, 0)) > 0]

    @staticmethod
    def _group(key: str, items: list[dict[str, Any]], *, label: Any = None, ordered: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {'key': key, 'label': label, 'ordered': ordered, 'items': items[:GROUP_LIMIT]}
        if len(items) > GROUP_LIMIT:
            out['more'] = len(items) - GROUP_LIMIT
        return out

    @staticmethod
    def _shaped(view: dict[str, Any]) -> dict[str, Any]:
        lead = view.get('lead')
        has_children = any(g.get('key') != 'relations' for g in view.get('groups') or ())
        view['shape'] = 'detail' if lead and (lead.get('truncated') or not has_children) else 'index'
        return view

    def _book_item(self, slug: str) -> dict[str, Any]:
        b = self._books_by_slug[slug]
        return {
            'id': 'book.' + slug,
            'kind': 'book',
            'title': b['title'],
            'ruleset': b['ruleset'] if b['ruleset'] in {'5e', '5r'} else None,
            'status': 'ok',
            'count': b['page_count'] or None,
            'hint': b['source_code'] or None,
        }

    def _pack_item(self, slug: str) -> dict[str, Any]:
        p = self._packs_by_slug[slug]
        hint_parts = [p['origin']]
        if p['book_count']:
            hint_parts.append(f"{p['book_count']} 册书目")
        return {
            'id': 'pack.' + slug,
            'kind': 'package',
            'title': p['name'],
            'status': 'ok',
            'count': p['entry_count'] or p['book_count'] or None,
            'hint': ' · '.join(hint_parts),
        }

    def _contents_item(self, nid: str) -> dict[str, Any]:
        node = self._nodes[nid]
        child_cnt = sum(divider(self._nodes[c]) is None for c in self._children.get(nid, []))
        return {
            'id': 'n' + nid,
            'kind': 'contents',
            'title': node['label'],
            'status': node.get('status', 'present'),
            'count': child_cnt or None,
        }

    # --- Public view & layout ---

    def view(self, at: str = '') -> dict[str, Any]:
        self._ensure_loaded()
        segment = (at or '').strip()
        if m := BOOK_SEGMENT_RE.fullmatch(segment):
            v = self._book_view(m.group(1))
        elif m := PACK_SEGMENT_RE.fullmatch(segment):
            v = self._pack_view(m.group(1))
        elif ENTRY_SEGMENT_RE.fullmatch(segment):
            v = self._entry_view(segment)
        elif segment in self._topics and self._topics[segment].dynamic_in(self.collection_key):
            v = self._listing_view(self._topics[segment])
        elif m := CONTENTS_SEGMENT_RE.fullmatch(segment):
            nid = m.group(1)
            if nid not in self._nodes:
                raise TourError('node_missing', 404)
            # A book's root reads as the book: its chapters and the entries it holds.
            v = self._book_view(self._books_by_node[nid], 'n' + nid) if nid in self._books_by_node else self._contents_view(nid)
        else:
            raise TourError('node_missing', 404)
        return self._shaped(v)

    def _book_view(self, slug: str, segment: str | None = None) -> dict[str, Any]:
        b = self._books_by_slug.get(slug)
        if b is None:
            raise TourError('node_missing', 404)
        scope = self._scope_query(b['source_id'] or None, None if b['source_id'] else (b['package_id'] or None))
        scoped_total = self._query_count(scope) if scope else 0
        srec = self._sources_by_id.get(b['source_id']) if b['source_id'] else None
        pslug = self._packs_by_id.get(b['package_id'])
        prec = self._packs_by_slug.get(pslug) if pslug else None
        sources_labels = [srec['name']] if srec else ([prec['name']] if prec else [])

        groups: list[dict[str, Any]] = []
        if b['chapters']:
            groups.append(self._group(
                'contents',
                [self._contents_item(cid) for cid in b['chapters']],
                label={'zh': '章节目录', 'en': 'Chapters'},
                ordered=True,
            ))
        type_items = self._type_items(scope) if scope else []
        if type_items:
            groups.append(self._group('facet', type_items, label={'zh': '本书条目分类', 'en': 'Entries in this book'}))
        return {
            'id': segment or 'book.' + slug,
            'kind': 'book',
            'trail': [],
            'title': b['title'],
            'ruleset': b['ruleset'] if b['ruleset'] in {'5e', '5r'} else None,
            'sources': sources_labels,
            'status': 'ok',
            'lead': None,
            'read': {'root': b['node_id'], 'page': b['first_page']} if b['first_page'] else {'root': b['node_id']},
            'anna': {'query': scope, 'count': scoped_total} if scoped_total > 0 else None,
            'groups': groups,
        }

    def _pack_view(self, slug: str) -> dict[str, Any]:
        p = self._packs_by_slug.get(slug)
        if p is None:
            raise TourError('node_missing', 404)
        pdata = p['data']
        lead_parts = [f"来源性质：{p['origin']}"]
        if pdata.get('authors'):
            lead_parts.append('作者：' + '、'.join(map(str, pdata['authors'])))
        if pdata.get('translators'):
            lead_parts.append('译者：' + '、'.join(map(str, pdata['translators'])))
        if pdata.get('version'):
            lead_parts.append(f"版本：{pdata['version']}")
        lead_text = '；'.join(lead_parts)
        scope = self._scope_query(None, p['id'])
        groups: list[dict[str, Any]] = []
        if p['book_slugs']:
            groups.append(self._group('contents', [self._book_item(bs) for bs in p['book_slugs']],
                                      label={'zh': '包内书目', 'en': 'Books in pack'}))
        type_items = self._type_items(scope)
        if type_items:
            groups.append(self._group('facet', type_items, label={'zh': '包内条目分类', 'en': 'Entries in pack'}))
        return {
            'id': 'pack.' + slug,
            'kind': 'package',
            'trail': ['资料包', p['origin']],
            'title': p['name'],
            'sources': [p['name']],
            'status': 'ok',
            'lead': {'text': lead_text, 'truncated': False} if lead_text else None,
            'read': None,
            'anna': {'query': scope, 'count': p['entry_count']},
            'groups': groups,
            'provenance': {
                'package_id': p['id'],
                'origin': p['origin'],
                'ours': p['ours'],
                'authors': list(pdata.get('authors') or []),
                'translators': list(pdata.get('translators') or []),
                'url': pdata.get('url'),
                'version': pdata.get('version'),
            },
        }

    def _contents_view(self, nid: str) -> dict[str, Any]:
        node = self._nodes[nid]
        div_name = divider(node)
        ancestors: list[str] = []
        parent = node.get('parent_id') or ''
        while parent and parent in self._nodes:
            ancestors.append(self._nodes[parent]['label'])
            parent = self._nodes[parent].get('parent_id') or ''
        groups: list[dict[str, Any]] = []
        if self._choices_by_node.get(nid):
            # The library tour answers each topic for the whole library; here it is only named.
            groups.append(self._group('choices', [{'id': k, 'kind': 'topic'} for k in self._choices_by_node[nid]]))
        children = [c for c in self._children.get(nid, []) if divider(self._nodes[c]) is None]
        if children:
            groups.append(self._group('contents', [self._contents_item(c) for c in children]))
        return {
            'id': 'n' + nid,
            'kind': 'contents',
            'trail': ancestors[::-1],
            'title': div_name or node['label'],
            'sources': [],
            'status': node.get('status', 'present'),
            'lead': None,
            'read': {'root': nid, 'page': node['path']} if node.get('path') else {'root': nid},
            'anna': None,
            'groups': groups,
        }

    @staticmethod
    def _feature_title(name: str, level: int | None) -> str:
        if level is None:
            return name
        prefix = f'{level}级'
        if name.startswith(prefix) or name.endswith((f'（{prefix}）', f'({prefix})')):
            return name
        return f'{prefix}：{name}'

    @staticmethod
    def _root_prose(body_text: str, sibling_names: set[str]) -> str:
        lines = [line.strip() for line in (body_text or '').splitlines() if line.strip()]
        while lines and lines[-1] in sibling_names:
            lines.pop()
        return ' '.join(lines)

    def _subclass_parts(self, db: sqlite3.Connection, entry_id: str) -> list[sqlite3.Row]:
        return db.execute(
            'SELECT e.id, e.name, e.name_zh, e.ruleset, e.owner_level, e.body_text '
            'FROM relations r JOIN entries e ON e.id = r.source_id '
            "WHERE r.target_id = ? AND r.type = 'part_of' AND e.type = 'class_feature' "
            'ORDER BY COALESCE(e.owner_level, 999), e.rowid',
            (entry_id,),
        ).fetchall()

    @staticmethod
    def _subclass_root_feature(feats: list[sqlite3.Row], name: str, name_zh: str) -> sqlite3.Row | None:
        if not feats:
            return None
        min_level = feats[0]['owner_level']
        wanted = {x for x in (name, name_zh) if x}
        return next((f for f in feats if f['owner_level'] == min_level and (f['name'] in wanted or f['name_zh'] in wanted)), None)

    def _entry_view(self, entry_id: str) -> dict[str, Any]:
        try:
            d = self.catalog.detail(entry_id)
        except LookupError as exc:
            raise TourError('node_missing', 404) from exc
        lead_source = d.get('body') or ''
        groups: list[dict[str, Any]] = []
        trail = [d['type_label']]
        seen_rel_targets: set[str] = set()

        if d['type'] == 'subclass':
            if d.get('owner_class'):
                trail.append(d['owner_class'])
            with closing(_connect_ro(self.db_path)) as db:
                feats = self._subclass_parts(db, entry_id)
            root = self._subclass_root_feature(feats, d['name'], d.get('name_zh') or '')
            if root is not None:
                siblings = {x for f in feats if f['id'] != root['id'] for x in (f['name'], f['name_zh']) if x}
                prose = self._root_prose(root['body_text'] or '', siblings)
                if prose:
                    lead_source = prose
            if feats:
                hinted = len(feats) <= HINTED_GROUP
                part_items = []
                for f in feats:
                    item: dict[str, Any] = {
                        'id': f['id'],
                        'kind': 'entry',
                        'title': self._feature_title(f['name'], f['owner_level']),
                        'type': 'class_feature',
                        'type_label': otypes.TYPES['class_feature'],
                        'ruleset': f['ruleset'],
                    }
                    if hinted:
                        item['hint'] = first_sentence(' '.join((f['body_text'] or '').split())) or None
                    part_items.append(item)
                groups.append(self._group('parts', part_items))
            class_items = []
            for r in d.get('relations') or []:
                tid = r.get('target_id')
                if tid and r.get('target_type') == 'class' and r.get('type') in {'belongs_to_class', 'part_of'} and tid not in seen_rel_targets:
                    seen_rel_targets.add(tid)
                    class_items.append({
                        'id': tid,
                        'kind': 'entry',
                        'title': r['name'],
                        'type': 'class',
                        'type_label': otypes.TYPES['class'],
                    })
            if class_items:
                groups.append(self._group('class', class_items))
        elif d['type'] == 'class':
            with closing(_connect_ro(self.db_path)) as db:
                cfeats = db.execute(
                    'SELECT e.id, e.name, e.ruleset, e.owner_level, e.body_text '
                    'FROM relations r JOIN entries e ON e.id = r.source_id '
                    "WHERE r.target_id = ? AND r.type = 'part_of' AND e.type = 'class_feature' AND e.owner_subclass = '' "
                    'ORDER BY COALESCE(e.owner_level, 999), e.rowid',
                    (entry_id,),
                ).fetchall()
                csubs = db.execute(
                    'SELECT DISTINCT e.id, e.name, e.name_zh, e.ruleset, e.source_id, e.source_code, e.sort_name '
                    'FROM relations r JOIN entries e ON e.id = r.source_id '
                    "WHERE r.target_id = ? AND r.type = 'belongs_to_class' AND e.type = 'subclass'",
                    (entry_id,),
                ).fetchall()
            if cfeats:
                hinted = len(cfeats) <= HINTED_GROUP
                part_items = []
                for f in cfeats:
                    item = {
                        'id': f['id'],
                        'kind': 'entry',
                        'title': self._feature_title(f['name'], f['owner_level']),
                        'type': 'class_feature',
                        'type_label': otypes.TYPES['class_feature'],
                        'ruleset': f['ruleset'],
                    }
                    if hinted:
                        item['hint'] = first_sentence(' '.join((f['body_text'] or '').split())) or None
                    part_items.append(item)
                groups.append(self._group('parts', part_items))
            if csubs:
                def _sub_key(row: sqlite3.Row) -> tuple[Any, ...]:
                    srec = self._sources_by_id.get(row['source_id'] or '')
                    origin = srec['origin'] if srec else '社区'
                    rs_rank = 0 if row['ruleset'] == '5r' else (1 if row['ruleset'] == '5e' else 2)
                    return (ORIGIN_RANK.get(origin, 9), rs_rank, row['sort_name'] or '', row['id'])
                sub_items = []
                for s in sorted(csubs, key=_sub_key):
                    srec = self._sources_by_id.get(s['source_id'] or '')
                    sub_items.append({
                        'id': s['id'],
                        'kind': 'entry',
                        'title': s['name_zh'] or s['name'],
                        'type': 'subclass',
                        'type_label': otypes.TYPES['subclass'],
                        'ruleset': s['ruleset'],
                        'hint': (srec['name'] if srec else '') or s['source_code'] or None,
                    })
                groups.append(self._group('subclasses', sub_items))
            if d.get('name_zh'):
                spell_q = {'type': ['spell'], 'f_spell_classes': [d['name_zh']],
                           **({'ruleset': [d['ruleset']]} if d.get('ruleset') in {'5e', '5r'} else {})}
                spell_count = self._query_count(spell_q)
                if spell_count:
                    groups.append(self._group('spells', [{'id': None, 'kind': 'query', 'query': spell_q,
                                                          'count': spell_count, 'title': None, 'role': 'spells'}]))

        elif d['type'] == 'class_feature':
            for owner in (d.get('owner_class'), d.get('owner_subclass')):
                if owner and owner not in trail:
                    trail.append(owner)

        rel_items = []
        for r in d.get('relations') or []:
            tid = r.get('target_id')
            if not tid or tid in seen_rel_targets:
                continue
            if d['type'] == 'class_feature' and r.get('type') in {'part_of', 'belongs_to_class'}:
                continue
            seen_rel_targets.add(tid)
            rel_items.append({
                'id': tid,
                'kind': 'entry',
                'title': r['name'],
                'type': r['target_type'],
                'type_label': r['target_type_label'],
                'status': r['status'],
            })
        if rel_items:
            groups.append(self._group('relations', rel_items, label={'zh': '关联条目', 'en': 'Related entries'}))
        shown, truncated = clip(lead_source, LEAD_LIMIT) if lead_source else ('', False)
        loc = d.get('reader_location')
        return {
            'id': entry_id,
            'kind': 'entry',
            'trail': trail,
            'title': d['name'],
            'ruleset': d['ruleset'],
            'type': d['type'],
            'type_label': d['type_label'],
            'sources': d.get('source_names') or [],
            'status': 'ok',
            'lead': {'text': shown, 'truncated': truncated} if shown else None,
            'read': {'entry': entry_id} if loc and loc.get('locatable') else None,
            'anna': {'entry': entry_id, 'type': d['type']},
            'groups': groups,
        }

    def _listing_view(self, topic: Any) -> dict[str, Any]:
        """The package listing a dynamic topic names, in this collection's own order."""
        dyn = topic.dynamic_in(self.collection_key)
        if dyn == 'packs_official':
            pslug = self._packs_by_id.get('official')
            if pslug:
                return self._pack_view(pslug)
        packs = {
            'packs_ours': [s for s in self._packs_order if self._packs_by_slug[s]['ours']],
            'packs_community': [s for s in self._packs_order
                                if self._packs_by_slug[s]['id'] != 'official' and not self._packs_by_slug[s]['ours']],
        }.get(dyn)
        if packs is None:
            raise TourError('node_missing', 404)
        return {
            'id': topic.key, 'kind': 'topic', 'trail': [], 'label': topic.label, 'title': topic.label['zh'],
            'ruleset': topic.ruleset, 'sources': [], 'status': 'ok', 'lead': None, 'read': None, 'anna': None,
            'groups': [self._group('packages', [self._pack_item(s) for s in packs], label=topic.label)],
        }

    def listing_count(self, topic: Any) -> int | None:
        """How many rows a dynamic topic lists."""
        self._ensure_loaded()
        dyn = topic.dynamic_in(self.collection_key)
        if dyn == 'packs_ours':
            return sum(1 for s in self._packs_order if self._packs_by_slug[s]['ours'])
        if dyn == 'packs_community':
            return sum(1 for s in self._packs_order if self._packs_by_slug[s]['id'] != 'official' and not self._packs_by_slug[s]['ours'])
        if dyn == 'packs_official':
            return self._packs_by_slug.get(self._packs_by_id.get('official', ''), {}).get('entry_count')
        return None

    def layout(self, segment: str) -> dict[str, Any]:
        self._ensure_loaded()
        if not segment or not ENTRY_SEGMENT_RE.fullmatch(segment):
            return {'status': 'empty'}
        with closing(_connect_ro(self.db_path)) as db:
            row = db.execute('SELECT type, name, name_zh FROM entries WHERE id = ?', (segment,)).fetchone()
            if row is not None and row['type'] == 'subclass':
                feats = self._subclass_parts(db, segment)
                root = self._subclass_root_feature(feats, row['name'], row['name_zh'] or '')
                if root is not None:
                    root_layout = self.catalog.layout(root['id'])
                    if root_layout.get('status') == 'ok' and root_layout.get('html'):
                        own_layout = self.catalog.layout(segment)
                        fluff = FLUFF_SECTION_RE.search(own_layout.get('html') or '') if own_layout.get('status') == 'ok' else None
                        html = root_layout['html'] + (fluff.group(0) if fluff else '')
                        return {**root_layout, 'html': html}
        return self.catalog.layout(segment)


# ---------------------------------------------------------------------------
# 3. Our5etoolsReader
# ---------------------------------------------------------------------------

class Our5etoolsReader:
    """Stella reader provider for our5etools chapters, book search, and self-contained/lazy media."""

    def __init__(
        self,
        data_dir: Path | str,
        source_dir: Path | str | None = None,
        *,
        lazy_media: bool = False,
        collection_key: str = COLLECTION_KEY,
        book_base: str | None = None,
        media_base: str | None = None,
    ):
        self.data_dir = Path(data_dir).resolve()
        self.collection_key = (collection_key or COLLECTION_KEY).strip() or COLLECTION_KEY
        raw_book = book_base or f'/{self.collection_key}-book/'
        self.book_base = raw_book if raw_book.endswith('/') else (raw_book + '/')
        raw_media = media_base or f'/{self.collection_key}-media/'
        self.media_base = raw_media if raw_media.endswith('/') else (raw_media + '/')
        self.db_path = self.data_dir / 'catalog.sqlite'
        self.reader_dir = self.data_dir / 'reader'
        self.reader_path = self.reader_dir / 'anna-reader.json'
        self.media_dir = self.data_dir / 'media'
        self.remotes_path = self.data_dir / 'media-remotes.json'
        self.lazy_media = bool(lazy_media)
        self._unused_source_dir = Path(source_dir).resolve() if source_dir else None
        self._lock = threading.Lock()
        self._reader_sig: tuple[int, int, int] | None = None
        self._reader_data: dict[str, Any] = {}
        self._remotes_sig: tuple[int, int, int] | None = None
        self._remotes_entries: dict[str, list[str]] = {}
        self._fetch_locks: dict[str, threading.Lock] = {}
        self._fetch_guard = threading.Lock()
        self._negative_cache: dict[str, float] = {}

    def _load_reader(self) -> dict[str, Any]:
        sig = _file_sig(self.reader_path)
        with self._lock:
            if sig is not None and sig == self._reader_sig:
                return self._reader_data
            if sig is None:
                raise ReaderError('reader_unavailable', 503)
            data = json.loads(self.reader_path.read_text('utf-8'))
            if data.get('schema') != READER_SCHEMA:
                raise ReaderError('reader_schema_mismatch', 500)
            self._reader_data = data
            self._reader_sig = sig
            return data

    def _load_remotes(self) -> dict[str, list[str]]:
        sig = _file_sig(self.remotes_path)
        if sig is None:
            return {}
        with self._lock:
            if sig == self._remotes_sig:
                return self._remotes_entries
            try:
                raw = json.loads(self.remotes_path.read_text('utf-8'))
            except (OSError, ValueError):
                return {}
            entries = raw.get('entries') if isinstance(raw, dict) else {}
            if not isinstance(entries, dict):
                entries = {}
            self._remotes_entries = {
                str(k): [str(u) for u in v if u]
                for k, v in entries.items()
                if isinstance(k, str) and isinstance(v, list)
            }
            self._remotes_sig = sig
            return self._remotes_entries

    def _list_cached_lazy_media(self) -> list[str]:
        media_root = self.media_dir.resolve()
        out: list[str] = []
        for subroot in ('img', 'ext'):
            sub = media_root / subroot
            if not sub.is_dir() or sub.is_symlink():
                continue
            for p in sub.rglob('*'):
                if p.is_symlink() or not p.is_file() or p.name.startswith('.'):
                    continue
                out.append(p.relative_to(media_root).as_posix())
        out.sort()
        return out

    @property
    def snapshot(self) -> str:
        return str(self._load_reader().get('snapshot') or '')

    def navigation(self) -> dict[str, Any]:
        data = self._load_reader()
        return {
            'schema': data['schema'],
            'snapshot': data['snapshot'],
            'roots': data.get('roots', []),
            'nodes': data.get('nodes', []),
            'default_page': data.get('default_page', ''),
            'page_base': data.get('page_base') or self.book_base,
            'content_base': data.get('page_base') or self.book_base,
            'media_base': data.get('media_base') or self.media_base,
            'presentation_resources': data.get('presentation_resources', []),
            'source_release': data.get('source_release'),
            'collection': data.get('collection') or self.collection_key,
            'lazy_media': self.lazy_media,
            'cached_media': self._list_cached_lazy_media(),
        }

    def page_data(self, path: str | None = None, entry: str | None = None) -> dict[str, Any]:
        path = (path or '').strip()
        entry = (entry or '').strip()
        if bool(path) == bool(entry):
            raise ReaderError('invalid_location', 400)

        data = self._load_reader()
        fragment = ''
        if entry:
            entry_locations = data.get('entry_locations') or {}
            target = entry_locations.get(entry)
            if target is None:
                # Distinguish missing entry (404) from existing unlocatable entry (422)
                exists = False
                if self.db_path.is_file():
                    with closing(_connect_ro(self.db_path)) as db:
                        exists = db.execute('SELECT 1 FROM entries WHERE id = ?', (entry,)).fetchone() is not None
                if exists:
                    raise ReaderError('entry_unlocatable', 422)
                raise ReaderError('entry_missing', 404)
            if not target.get('locatable') or not target.get('path'):
                raise ReaderError('entry_unlocatable', 422)
            path = str(target['path'])
            fragment = str(target.get('fragment') or '')

        pages = data.get('pages') or {}
        page = pages.get(path)
        if page is None:
            raise ReaderError('page_missing', 404)
        return {'snapshot': data['snapshot'], 'path': path, 'fragment': fragment, **page}

    def search(self, keyword: str, *, title_only: bool = False) -> SearchHits:
        """Chapter sections matching every term, best first, or any term when none matches all; ranked by the
        same rule as the book's own search, so a library can interleave both."""
        keyword = (keyword or '').strip()
        if len(keyword) > 2000:
            raise ReaderError('invalid_search', 400)
        terms = _parse_search_terms(keyword)
        if not terms:
            return SearchHits('all', [], lambda key, score: {})

        with closing(_connect_ro(self.db_path)) as db:
            def _fetch_hits(require_all: bool) -> list[sqlite3.Row]:
                term_clauses: list[str] = []
                term_vals: list[Any] = []
                for t in terms:
                    if title_only:
                        term_clauses.append('instr(lower(title), ?) > 0')
                        term_vals.append(t)
                    else:
                        term_clauses.append('(instr(lower(title), ?) > 0 OR instr(lower(snippet_text), ?) > 0)')
                        term_vals.extend([t, t])
                joiner = ' AND ' if require_all else ' OR '
                sql = (
                    'SELECT id, root_id, page_path, node_id, title, scope_label, snippet_text '
                    'FROM book_search WHERE (' + joiner.join(term_clauses) + ')'
                )
                return db.execute(sql, term_vals).fetchall()

            rows = _fetch_hits(require_all=True)
            match_mode = 'all'
            if not rows and len(terms) > 1:
                rows = _fetch_hits(require_all=False)
                match_mode = 'any'

        found: dict[int, dict[str, Any]] = {}
        scored: list[tuple[int, int]] = []
        for r in rows:
            title = r['title'] or ''
            tl = title.lower()
            text = r['snippet_text'] or ''
            body_l = text.lower()
            t_counts = [tl.count(t) for t in terms]
            if title_only:
                score = sum(c * 1000 for c in t_counts)
            else:
                b_counts = [body_l.count(t) for t in terms]
                score = sum(t_counts[i] * 1000 + min(b_counts[i], 200) for i in range(len(terms)))
                if match_mode == 'all' and all(c > 0 for c in t_counts):
                    score += 500
            index = int(r['id'])
            scored.append((score, index))
            found[index] = {
                'index': index,
                'title': title,
                'rawTitle': title,
                'path': r['page_path'],
                'sourcePath': r['page_path'],
                'preview': _make_preview(text, body_l, terms),
                'category': r['scope_label'] or r['root_id'] or '',
            }
        scored.sort(key=lambda item: (-item[0], item[1]))
        return SearchHits(match_mode, scored, lambda key, score: {**found[key], 'rank': score})

    def resolve_book_asset(self, relpath: str) -> tuple[Path, str] | None:
        posix = _safe_relpath(relpath)
        if posix is None:
            return None
        rel = posix.as_posix()
        # Never serve internal reader/manifest metadata via static asset route
        if rel.startswith('anna-') and not rel.startswith('anna-assets/'):
            return None

        reader_root = self.reader_dir.resolve()
        candidate = (reader_root / rel).resolve()
        if candidate.is_relative_to(reader_root) and candidate.is_file():
            return candidate, _mime_for(candidate)

        # Fallback for shared Stella client scripts under PROJECT/book/
        if len(posix.parts) == 2 and posix.parts[0] == 'anna-assets' and posix.parts[1] in FALLBACK_BOOK_ASSETS:
            book_root = (paths.PROJECT / 'book').resolve()
            fallback = (book_root / posix.parts[1]).resolve()
            if fallback.is_relative_to(book_root) and fallback.is_file():
                return fallback, _mime_for(fallback)

        return None

    @staticmethod
    def _is_allowed_media_posix(posix: PurePosixPath) -> bool:
        top = posix.parts[0]
        if top in ALLOWED_MEDIA_ROOTS:
            return True
        if len(posix.parts) >= 3 and top == '__ours' and posix.parts[1] == 'fonts':
            return True
        if len(posix.parts) >= 3 and top == '__brewrepo' and posix.parts[1] == '_font':
            return True
        if len(posix.parts) == 1 and Path(posix.parts[0]).suffix.lower() in SAFE_ROOT_MEDIA_EXTS:
            return True
        return False

    def _remote_urls_for(self, rel_posix: str) -> list[str]:
        entries = self._load_remotes()
        urls = list(entries.get(rel_posix) or ())
        if rel_posix.startswith('img/'):
            sub = rel_posix.removeprefix('img/').lstrip('/')
            if sub:
                quoted = '/'.join(quote(seg, safe="()'!,;-_~") for seg in sub.split('/'))
                for base in REMOTE_IMG_BASES:
                    cand_url = f'{base}{quoted}'
                    if cand_url not in urls:
                        urls.append(cand_url)
        return urls

    def _fetch_remote_media_safely(self, rel_posix: str, dest_path: Path) -> bool:
        urls = self._remote_urls_for(rel_posix)
        if not urls:
            return False
        now = time.monotonic()
        with self._fetch_guard:
            neg_until = self._negative_cache.get(rel_posix, 0.0)
            if neg_until > now:
                return False
            key_lock = self._fetch_locks.setdefault(rel_posix, threading.Lock())

        with key_lock:
            if dest_path.is_file() and not dest_path.is_symlink():
                return True
            if self._negative_cache.get(rel_posix, 0.0) > time.monotonic():
                return False

            opener = urllib.request.build_opener(_SafeRedirectHandler())
            for url in urls:
                try:
                    _validate_remote_url(url)
                    req = urllib.request.Request(url, headers={'User-Agent': '5e-anna/1.0'})
                    with opener.open(req, timeout=10) as resp:
                        status = getattr(resp, 'status', None) or resp.getcode()
                        if status != 200:
                            continue
                        _validate_remote_url(resp.geturl())
                        ct = str(resp.headers.get('Content-Type') or '')
                        cl_raw = resp.headers.get('Content-Length')
                        if cl_raw:
                            try:
                                if int(cl_raw) > MAX_REMOTE_MEDIA_BYTES:
                                    continue
                            except ValueError:
                                pass
                        first = resp.read(512)
                        if not first or not _sniff_bytes_valid_media(first, ct):
                            continue
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        tmp_path = dest_path.parent / f'.{dest_path.name}.tmp.{threading.get_ident()}'
                        total = len(first)
                        try:
                            with tmp_path.open('wb') as out_f:
                                out_f.write(first)
                                while True:
                                    chunk = resp.read(65536)
                                    if not chunk:
                                        break
                                    total += len(chunk)
                                    if total > MAX_REMOTE_MEDIA_BYTES:
                                        raise ValueError('Remote media exceeds size limit')
                                    out_f.write(chunk)
                            os.replace(tmp_path, dest_path)
                            return True
                        except BaseException:
                            if tmp_path.exists():
                                try:
                                    tmp_path.unlink()
                                except OSError:
                                    pass
                            raise
                except Exception:
                    continue

            with self._fetch_guard:
                self._negative_cache[rel_posix] = time.monotonic() + NEGATIVE_MEDIA_TTL_SECONDS
            return False

    def resolve_media_asset(self, relpath: str) -> tuple[Path, str] | None:
        posix = _safe_relpath(relpath)
        if posix is None or not self._is_allowed_media_posix(posix):
            return None

        media_root = self.media_dir.resolve()
        rel_posix = posix.as_posix()
        raw_target = media_root / rel_posix
        if not raw_target.is_symlink():
            candidate = raw_target.resolve()
            if candidate.is_relative_to(media_root) and candidate.is_file():
                return candidate, _mime_for(candidate)

        if not self.lazy_media:
            return None

        # Only fetch remote images/audio into img/ or ext/
        if posix.parts[0] not in {'img', 'ext'}:
            return None
        resolved_dest = (media_root / rel_posix).resolve()
        if not resolved_dest.is_relative_to(media_root):
            return None
        if self._fetch_remote_media_safely(rel_posix, resolved_dest) and resolved_dest.is_file():
            return resolved_dest, _mime_for(resolved_dest)
        return None


# ---------------------------------------------------------------------------
# 4. Collection Loader
# ---------------------------------------------------------------------------

def _load_5etools_family_collection(
    data_dir: Path | str,
    source_dir: Path | str | None = None,
    curation_path: Path | str | None = None,
    *,
    collection_key: str,
    zh: str,
    en: str,
    lazy_media: bool = False,
) -> Collection | None:
    """Load one 5etools-family collection when its published artifacts match ``collection_key``."""
    root = Path(data_dir).resolve()
    manifest_path = root / 'manifest.json'
    db_path = root / 'catalog.sqlite'
    reader_path = root / 'reader' / 'anna-reader.json'
    if not (manifest_path.is_file() and db_path.is_file() and reader_path.is_file()):
        return None
    try:
        manifest = json.loads(manifest_path.read_text('utf-8'))
        reader_json = json.loads(reader_path.read_text('utf-8'))
    except (OSError, ValueError):
        return None
    if reader_json.get('schema') != READER_SCHEMA:
        return None
    if manifest.get('collection') != collection_key or reader_json.get('collection') != collection_key:
        return None

    snapshot = str(reader_json.get('snapshot') or manifest.get('snapshot') or '')
    raw_book = str(reader_json.get('page_base') or f'/{collection_key}-book/')
    book_base = raw_book if raw_book.endswith('/') else (raw_book + '/')
    raw_media = str(reader_json.get('media_base') or f'/{collection_key}-media/')
    media_base = raw_media if raw_media.endswith('/') else (raw_media + '/')
    return Collection(
        collection_key, zh, en,
        provider=Our5etoolsCatalog(root, lazy_media=lazy_media, collection_key=collection_key),
        snapshot=snapshot,
        tour=Our5etoolsTour(root, curation_path, lazy_media=lazy_media, collection_key=collection_key),
        reader=Our5etoolsReader(root, source_dir, lazy_media=lazy_media, collection_key=collection_key,
                                book_base=book_base, media_base=media_base),
        book_base=book_base,
        media_base=media_base,
    )


def load_our5etools_collection(
    data_dir: Path | str = paths.OUR5ETOOLS_DATA,
    source_dir: Path | str | None = None,
    curation_path: Path | str | None = None,
    *,
    lazy_media: bool = False,
) -> Collection | None:
    """Load the read-only our5etools Collection when built artifacts are complete; else return None."""
    return _load_5etools_family_collection(
        data_dir, source_dir, curation_path,
        collection_key=COLLECTION_KEY, zh=COLLECTION_ZH, en=COLLECTION_EN, lazy_media=lazy_media,
    )


def load_fivetools_collection(
    data_dir: Path | str = paths.FIVETOOLS_DATA,
    source_dir: Path | str | None = None,
    curation_path: Path | str | None = None,
    *,
    lazy_media: bool = False,
) -> Collection | None:
    """Load the reserved standalone 5etools Collection when built separately; else return None."""
    return _load_5etools_family_collection(
        data_dir, source_dir, curation_path,
        collection_key=FIVETOOLS_COLLECTION_KEY, zh=FIVETOOLS_COLLECTION_KEY, en=FIVETOOLS_COLLECTION_KEY,
        lazy_media=lazy_media,
    )
