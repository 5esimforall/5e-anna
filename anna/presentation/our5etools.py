"""Read-only presentation providers for the our5etools collection: catalog, tour, and Stella reader."""
from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from contextlib import closing, contextmanager
import gzip
import ipaddress
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import socket
import sqlite3
import threading
import time
from typing import Any
from urllib.parse import quote, unquote, urlsplit
import urllib.request

import anna.our5etools.types as otypes
from anna.our5etools.vocab import MIME_OVERRIDES, REMOTE_IMAGE_MIRRORS as REMOTE_IMG_BASES, safe_relpath as _vocab_safe_relpath
import anna.paths as paths
from anna.presentation import catalog, expression
from anna.presentation.catalog import _ascii_lower, first as _first, format_csv_bytes, result_window as _result_window
from anna.presentation.collections import Collection, SearchHits, _ConnectionSlot
from anna.presentation.expression import Query
from anna.presentation.reader import ReaderError, SCHEMA as READER_SCHEMA
from anna.presentation import tour as tour_curation
from anna.presentation.tour import HINTED_GROUP, TourError, clip, divider, first_sentence
from anna.source.search import make_preview as _make_search_preview, parse_terms as _parse_search_terms

COLLECTION_KEY = 'our5etools'
COLLECTION_ZH = 'our5etools'
COLLECTION_EN = 'our5etools'

FIVETOOLS_COLLECTION_KEY = '5etools'
MAX_REMOTE_MEDIA_BYTES = 25 * 1024 * 1024
NEGATIVE_MEDIA_TTL_SECONDS = 60.0
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

SLUG_SAFE_RE = re.compile(r'[^a-zA-Z0-9._-]+')
BOOK_SEGMENT_RE = re.compile(r'^book\.([A-Za-z0-9._-]+)$')
PACK_SEGMENT_RE = re.compile(r'^pack\.([A-Za-z0-9._-]+)$')
CONTENTS_SEGMENT_RE = re.compile(r'^n(.+)$')
ENTRY_SEGMENT_RE = re.compile(r'^o5e_[0-9a-f]{16}$')
FLUFF_SECTION_RE = re.compile(r'<section class="o5e-fluff">.*?</section>', re.DOTALL)
ORIGIN_RANK: dict[str, int] = {'官方': 0, '合作': 1, '本站精选': 2, '社区': 3, '待核': 4}

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


def _file_sig(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
        return (st.st_ino, st.st_size, st.st_mtime_ns)
    except OSError:
        return None


_NAV_LOCK = threading.Lock()
_NAV_CACHE: OrderedDict[tuple[str, tuple[int, int, int]], dict[str, Any]] = OrderedDict()
_NAV_CACHE_LIMIT = 4


def _load_shared_nav(nav_path: Path, reader_path: Path) -> dict[str, Any] | None:
    """Load and cache navigation tree JSON once across Our5etoolsTour and Our5etoolsReader."""
    nav_sig = _file_sig(nav_path)
    if nav_sig is not None:
        chosen, sig, compressed = nav_path, nav_sig, True
    else:
        reader_sig = _file_sig(reader_path)
        if reader_sig is None:
            return None
        chosen, sig, compressed = reader_path, reader_sig, False
    key = (str(chosen), sig)
    with _NAV_LOCK:
        cached = _NAV_CACHE.get(key)
        if cached is not None:
            _NAV_CACHE.move_to_end(key)
            return cached
    raw = gzip.decompress(chosen.read_bytes()).decode('utf-8') if compressed else chosen.read_text('utf-8')
    data = json.loads(raw)
    with _NAV_LOCK:
        _NAV_CACHE[key] = data
        _NAV_CACHE.move_to_end(key)
        while len(_NAV_CACHE) > _NAV_CACHE_LIMIT:
            _NAV_CACHE.popitem(last=False)
    return data


def _json_loads(raw: Any, default: Any) -> Any:
    if raw is None or raw == '':
        return default
    if not isinstance(raw, (str, bytes)):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


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


def _slug(value: str, fallback: str = 'item') -> str:
    cleaned = SLUG_SAFE_RE.sub('-', value.strip()).strip('-.')
    return cleaned or fallback


def _safe_relpath(relpath: str) -> PurePosixPath | None:
    return _vocab_safe_relpath(relpath, allow_leading_slash=True)


def _sniff_bytes_mime(head: bytes) -> str | None:
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


def _sniff_media_mime(path: Path) -> str | None:
    try:
        with path.open('rb') as f:
            return _sniff_bytes_mime(f.read(64))
    except OSError:
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
    return _sniff_bytes_mime(chunk) is not None


def _connect_ro(db_path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    db = sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True, check_same_thread=check_same_thread)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    otypes.register(db)
    return db


def build_sources_map(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
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
    return sources_map


def build_entry_row(
    row: sqlite3.Row,
    sources_map: dict[str, dict[str, Any]],
    collection_key: str,
    *,
    include_provenance: bool = True,
) -> dict[str, Any]:
    fields = _json_loads(row['fields_json'], {})
    quickref = _json_loads(row['quickref_json'], {})
    conflicts = _json_loads(row['conflicts_json'], [])
    provenance = _json_loads(row['provenance_json'], {}) if include_provenance else None
    sid = row['source_id'] or ''
    srec = sources_map.get(sid)
    sname = (srec['name'] if srec else None) or (provenance.get('source_name') if provenance else None) or row['source_code'] or sid
    slabel = (srec['short_name'] if srec else None) or row['source_code'] or sid
    origin = (srec['origin'] if srec else None) or (provenance.get('origin') if provenance else None) or '社区'
    col_key = str((provenance.get('collection') if provenance else None) or collection_key)
    if provenance is not None:
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
    }
    if provenance is not None:
        entry['provenance'] = provenance
    if not entry['quickref']:
        otypes.enrich(entry)
    return entry


def build_version_compare(gid: str, mem_entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
    return members_compact, versions_compare


def build_relations_list(rel_rows: list[Any], targets_map: dict[str, tuple[str, str, str]]) -> list[dict[str, Any]]:
    relations_out: list[dict[str, Any]] = []
    enriched_rels = []
    for rr in rel_rows:
        tid = rr['target_id']
        target_info = targets_map.get(tid)
        t_name = target_info[0] if target_info else None
        t_type = (target_info[1] or '') if target_info else ''
        t_code = (target_info[2] or '') if target_info else ''
        enriched_rels.append((rr['rel_type'], t_name is not None, t_name or '', tid, t_name, t_type, t_code, rr['meta_json']))
    enriched_rels.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    for rel_type, _, _, tid, t_name, t_type, t_code, meta_json in enriched_rels:
        relations_out.append({
            'type': rel_type,
            'predicate': rel_type,
            'target_id': tid,
            'object_id': tid,
            'name': t_name or tid,
            'target_label': t_name or tid,
            'target_type': t_type,
            'target_type_label': otypes.TYPES.get(t_type, t_type),
            'source_code': t_code,
            'status': 'ok' if t_name else 'missing',
            'meta': _json_loads(meta_json, {}),
        })
    return relations_out


# ---------------------------------------------------------------------------
# 1. Our5etoolsCatalog
# ---------------------------------------------------------------------------

class Our5etoolsCatalog:
    """Read-only CatalogProvider over our5etools catalog.sqlite."""

    CONDITIONS = frozenset({'type', 'q', 'typing', 'body', 'ruleset', 'legacy', 'copy_status', 'origin', 'source',
                            'package', 'conflict', 'cr_min', 'cr_max'})
    same_name_groups = False

    def __init__(self, data_dir: Path | str, *, lazy_media: bool = False, collection_key: str = COLLECTION_KEY):
        self.data_dir = Path(data_dir).resolve()
        self.collection_key = (collection_key or COLLECTION_KEY).strip() or COLLECTION_KEY
        self.tour_url = f'/tour?collection={self.collection_key}'
        self.media_dir = self.data_dir / 'media'
        self.lazy_media = bool(lazy_media)
        self.db_path = self.data_dir / 'catalog.sqlite'
        self.manifest_path = self.data_dir / 'manifest.json'
        self.reader_path = self.data_dir / 'reader' / 'anna-reader.json'
        self.reader_index_path = self.data_dir / 'reader-index.json'
        self.nav_path = self.data_dir / 'nav-nodes.json.gz'
        self.summary_path = self.data_dir / 'summary.json'
        self.rank_index_path = self.data_dir / 'rank-index.json.gz'
        self._lock = threading.Lock()
        self._local = threading.local()
        self._cache_sig: tuple[int, int, int] | None = None
        self._sources_map: dict[str, dict[str, Any]] = {}
        self._packages_map: dict[str, dict[str, Any]] = {}
        self._held_types: set[str] = set()
        self._bases: dict[str, tuple[str, str, str]] = {}
        self._bases_loaded = False
        self._has_entry_core = False
        self._has_entry_fields = False
        self._has_entry_meta = False
        self._has_quickref_in_fields = False
        self._has_body_text_table = False
        self._has_search_body_table = False
        self._has_layout_gzip = False
        self._has_build_order = False
        self._has_name_fold_index = False
        self._pages_source_counts: dict[str, int] | None = None
        self._pages_type_ruleset_counts: list[list[Any]] | None = None
        self._pages_unfiltered_facets: dict[str, Any] | None = None
        self._pages_type_facets: dict[str, dict[str, dict[str, int]]] | None = None
        self._pages_type_facets_loaded = False
        self._rank_index_cache: tuple[tuple[Any, ...] | None, list[dict[str, Any]]] | None = None
        self._manifest_types_cache: tuple[tuple[Any, ...] | None, frozenset[str]] | None = None
        self._name_fold_cache: dict[tuple[str, str], tuple[str, ...]] | None = None
        self._scope_universe_cache: dict[tuple[str, ...], tuple[int, dict[str, set[str]], dict[str, int]]] = {}
        self._unfiltered_facets_cache: dict[str, Any] | None = None
        self._entry_locations: dict[str, dict[str, Any]] = {}
        self._reader_roots: dict[str, str] = {}
        self._reader_sig: tuple[str, tuple[int, int, int] | None] | None = None
        self._roots_sig: tuple[str, tuple[int, int, int] | None] | None = None

    @property
    def _entry_table(self) -> str:
        return 'entry_core' if self._has_entry_core else 'entries'

    def _fields_col(self, name: str) -> str:
        if name == 'quickref_json':
            return 'efld.quickref_json' if self._has_quickref_in_fields else self._meta_col(name)
        return f'efld.{name}' if self._has_entry_fields else f'e.{name}'

    def _meta_col(self, name: str) -> str:
        return f'em.{name}' if self._has_entry_meta else f'e.{name}'

    def _body_col(self, name: str) -> str:
        if not self._has_entry_core:
            return f'e.{name}'
        if name == 'body_text' and self._has_body_text_table:
            return 'ebt.body_gzip'
        if name == 'search_body' and self._has_search_body_table:
            return 'esb.search_body'
        return f'eb.{name}'

    def _layout_col(self, name: str) -> str:
        if not self._has_entry_core:
            return f'e.{name}'
        if self._has_layout_gzip:
            return 'el.layout_gzip'
        return f'el.{name}'

    def _from_entries(
        self,
        *,
        need_fields: bool = False,
        need_meta: bool = False,
        need_body_text: bool = False,
        need_search_body: bool = False,
        need_layout: bool = False,
    ) -> str:
        if not self._has_entry_core:
            return 'entries e'
        parts = ['entry_core e']
        if need_fields and self._has_entry_fields:
            parts.append('LEFT JOIN entry_fields efld ON efld.entry_rowid = e.rowid')
        if need_meta and self._has_entry_meta:
            parts.append('LEFT JOIN entry_meta em ON em.entry_rowid = e.rowid')
        if need_body_text:
            if self._has_body_text_table:
                parts.append('LEFT JOIN entry_body_text ebt ON ebt.entry_rowid = e.rowid')
            else:
                parts.append('LEFT JOIN entry_body eb ON eb.entry_rowid = e.rowid')
        if need_search_body:
            if self._has_search_body_table:
                parts.append('LEFT JOIN entry_search_body esb ON esb.entry_rowid = e.rowid')
            elif 'entry_body eb' not in parts[-1]:
                parts.append('LEFT JOIN entry_body eb ON eb.entry_rowid = e.rowid')
        if need_layout:
            parts.append('LEFT JOIN entry_layout el ON el.entry_rowid = e.rowid')
        return ' '.join(parts)

    def _row_select_cols(self, *, include_provenance: bool = True) -> str:
        fields_col = self._fields_col('fields_json')
        quickref_col = self._fields_col('quickref_json')
        prov_part = f', {self._meta_col("provenance_json")} AS provenance_json' if include_provenance else ''
        return (
            'e.id, e.type, e.raw_kind, e.name, e.name_zh, e.name_en, e.ruleset, e.legacy, '
            'e.package_id, e.source_id, e.source_code, e.owner_class, e.owner_subclass, '
            f'e.owner_level, e.copy_status, {fields_col} AS fields_json, '
            f'{quickref_col} AS quickref_json, e.conflicts_json{prov_part}'
        )

    @staticmethod
    def _where_needs_body(where_sql: str) -> bool:
        return 'search_body' in where_sql

    @staticmethod
    def _order_needs_fields(order_sql: str) -> bool:
        return 'fields_json' in order_sql

    def known_types(self) -> frozenset[str]:
        sig = (_file_sig(self.manifest_path), _file_sig(self.summary_path))
        with self._lock:
            if self._manifest_types_cache is not None and self._manifest_types_cache[0] == sig:
                return self._manifest_types_cache[1]
        types_found: frozenset[str] | None = None
        if sig[0] is not None:
            manifest_data = _json_loads(self.manifest_path.read_text('utf-8'), {})
            raw_types = manifest_data.get('types') if isinstance(manifest_data, dict) else None
            if isinstance(raw_types, list):
                types_found = frozenset(t for t in raw_types if t in otypes.TYPE_ORDER)
        if types_found is None and sig[1] is not None:
            summary_data = _json_loads(self.summary_path.read_text('utf-8'), {})
            by_type = summary_data.get('by_type') if isinstance(summary_data, dict) else None
            if isinstance(by_type, dict):
                types_found = frozenset(t for t, c in by_type.items() if c > 0 and t in otypes.TYPE_ORDER)
        if types_found is None:
            types_found = self.capabilities().types
        with self._lock:
            self._manifest_types_cache = (sig, types_found)
        return types_found

    def _connect(self) -> sqlite3.Connection:
        sig = _file_sig(self.db_path)
        slot = getattr(self._local, 'slot', None)
        cached_sig = getattr(self._local, 'conn_sig', None)
        if slot is not None:
            if sig is not None and cached_sig == sig:
                return slot.conn
            self._local.slot = None
            self._local.conn_sig = None
            slot.close()
        if sig is None:
            return _connect_ro(self.db_path)
        conn = _connect_ro(self.db_path, check_same_thread=False)
        self._local.slot = _ConnectionSlot(conn)
        self._local.conn_sig = sig
        return conn

    def close(self) -> None:
        slot = getattr(self._local, 'slot', None)
        self._local.slot = None
        self._local.conn_sig = None
        if slot is not None:
            slot.close()

    @contextmanager
    def _db(self):
        db = self._connect()
        try:
            yield db
        finally:
            slot = getattr(self._local, 'slot', None)
            if slot is None or slot.conn is not db:
                db.close()

    def _ensure_static_cache(self, db: sqlite3.Connection) -> None:
        sig = _file_sig(self.db_path)
        with self._lock:
            if sig is not None and sig == self._cache_sig:
                return
            sources_map = build_sources_map(db)
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
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            has_core = 'entry_core' in tables
            has_fields = 'entry_fields' in tables
            has_meta = 'entry_meta' in tables
            has_quickref_in_fields = (
                has_fields and any(r[1] == 'quickref_json' for r in db.execute('PRAGMA table_info(entry_fields)'))
            )
            has_body_text_table = 'entry_body_text' in tables
            has_search_body_table = 'entry_search_body' in tables
            has_layout_gzip = (
                'entry_layout' in tables
                and any(r[1] == 'layout_gzip' for r in db.execute('PRAGMA table_info(entry_layout)'))
            )
            has_build_order = (
                has_core and any(r[1] == 'build_order' for r in db.execute('PRAGMA table_info(entry_core)'))
            )
            entry_table = 'entry_core' if has_core else 'entries'
            meta_rows = dict(db.execute(
                "SELECT key, value FROM metadata WHERE key IN ('pages_source_counts', 'pages_type_ruleset_counts', 'pages_unfiltered_facets')"
            ))
            src_counts = _json_loads(meta_rows.get('pages_source_counts'), None)
            tr_counts = _json_loads(meta_rows.get('pages_type_ruleset_counts'), None)
            unfiltered_facets = _json_loads(meta_rows.get('pages_unfiltered_facets'), None)
            if isinstance(tr_counts, list):
                held = {t for t, _, c in tr_counts if t and c > 0}
            else:
                held = {r[0] for r in db.execute(f'SELECT DISTINCT type FROM {entry_table}') if r[0]}
            self._bases = {}
            self._bases_loaded = False
            self._sources_map = sources_map
            self._packages_map = packages_map
            self._held_types = held
            self._has_entry_core = has_core
            self._has_entry_fields = has_fields
            self._has_entry_meta = has_meta
            self._has_quickref_in_fields = has_quickref_in_fields
            self._has_body_text_table = has_body_text_table
            self._has_search_body_table = has_search_body_table
            self._has_layout_gzip = has_layout_gzip
            self._has_build_order = has_build_order
            self._has_name_fold_index = 'name_fold_index' in tables
            self._pages_source_counts = src_counts if isinstance(src_counts, dict) else None
            self._pages_type_ruleset_counts = tr_counts if isinstance(tr_counts, list) else None
            self._pages_unfiltered_facets = unfiltered_facets if isinstance(unfiltered_facets, dict) else None
            self._pages_type_facets = None
            self._pages_type_facets_loaded = False
            self._rank_index_cache = None
            self._name_fold_cache = None
            self._scope_universe_cache.clear()
            self._unfiltered_facets_cache = None
            self._cache_sig = sig

    def _ensure_type_facets(self, db: sqlite3.Connection) -> dict[str, dict[str, dict[str, int]]] | None:
        with self._lock:
            if self._pages_type_facets_loaded:
                return self._pages_type_facets
        row = db.execute("SELECT value FROM metadata WHERE key = 'pages_type_facets'").fetchone()
        parsed = _json_loads(row[0], None) if row is not None else None
        data = parsed if isinstance(parsed, dict) else None
        with self._lock:
            self._pages_type_facets = data
            self._pages_type_facets_loaded = True
            return data

    def _ensure_bases(self, db: sqlite3.Connection) -> None:
        """Load copied_from base mapping only when cross-collection stacking or same_name needs it."""
        self._ensure_static_cache(db)
        with self._lock:
            if self._bases_loaded:
                return
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        entry_table = 'entry_core' if self._has_entry_core else 'entries'
        bases: dict[str, tuple[str, str, str]] = {}
        if 'pages_copied_bases' in tables:
            for copy, base, base_work, base_name_en in db.execute(
                'SELECT source_id, target_id, base_source_id, base_name_en FROM pages_copied_bases ORDER BY source_id, target_id'
            ):
                bases.setdefault(copy, (base, base_work, base_name_en))
        else:
            for copy, base, base_work, base_name_en in db.execute(
                f"SELECT r.source_id, r.target_id, b.source_id, b.name_en FROM relations r JOIN {entry_table} b ON b.id = r.target_id "
                "WHERE r.type = 'copied_from' ORDER BY r.source_id, r.target_id"
            ):
                bases.setdefault(copy, (base, base_work, base_name_en))
        with self._lock:
            self._bases = bases
            self._bases_loaded = True

    @staticmethod
    def _extract_roots(raw: Any) -> dict[str, str]:
        roots: dict[str, str] = {}
        raw_roots = raw.get('roots') if isinstance(raw, dict) else None
        if isinstance(raw_roots, dict):
            roots = {str(k): str(v) for k, v in raw_roots.items() if k and v}
        elif isinstance(raw_roots, list):
            for root in raw_roots:
                if isinstance(root, dict) and root.get('source_id') and root.get('id'):
                    roots.setdefault(root['source_id'], root['id'])
        return roots

    def _load_reader_roots(self) -> dict[str, str]:
        """Each work's book root from nav-nodes.json.gz, reader-index.json, or anna-reader.json."""
        nav_sig = _file_sig(self.nav_path)
        index_sig = _file_sig(self.reader_index_path) if nav_sig is None else None
        if nav_sig is None and index_sig is not None:
            sig: tuple[str, Any] | None = ('index', index_sig)
        else:
            reader_sig = _file_sig(self.reader_path) if nav_sig is None else None
            sig = ('shared', nav_sig or reader_sig) if (nav_sig or reader_sig) else None
        with self._lock:
            if sig == self._roots_sig and sig is not None:
                return self._reader_roots
        roots: dict[str, str] = {}
        if sig is not None:
            try:
                raw = (
                    json.loads(self.reader_index_path.read_text('utf-8'))
                    if sig[0] == 'index'
                    else _load_shared_nav(self.nav_path, self.reader_path)
                )
                roots = self._extract_roots(raw)
            except (OSError, ValueError):
                roots = {}
        with self._lock:
            self._reader_roots, self._roots_sig = roots, sig
            return roots

    def _load_reader_index(self) -> None:
        """Entry locations from the compact index or full reader, reloaded on change."""
        index_sig = _file_sig(self.reader_index_path)
        reader_sig = _file_sig(self.reader_path) if index_sig is None else None
        sig: tuple[str, tuple[int, int, int] | None] | None = (
            ('index', index_sig) if index_sig is not None else (('reader', reader_sig) if reader_sig is not None else None)
        )
        with self._lock:
            if sig == self._reader_sig and sig is not None:
                return
            locations: dict[str, dict[str, Any]] = {}
            if sig is not None:
                target = self.reader_index_path if sig[0] == 'index' else self.reader_path
                try:
                    raw = json.loads(target.read_text('utf-8'))
                    locs = raw.get('entry_locations')
                    locations = locs if isinstance(locs, dict) else {}
                except (OSError, ValueError):
                    locations = {}
            self._entry_locations, self._reader_sig = locations, sig

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
        if typing:
            out.append(('typing', 'instr(lower(e.search_name), lower(?)) > 0', [typing]))

        for i, term in enumerate(_search_terms(params, 'body')):
            out.append((f'body{i}', f'instr(lower({self._body_col("search_body")}), lower(?)) > 0', [term]))

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

    def _card_condition(self, card: expression.Card) -> tuple[str, list[Any]]:
        """One card's SQL. Its values are alternatives; an excluded card holds for entries that meet none of them.
        A card this catalog cannot read holds for every entry."""
        if card.negated and card.base not in NEGATABLE and not card.base.startswith('f_'):
            raise ValueError('无法排除：' + card.base)
        found = self._included_conditions({card.base: list(card.values)})
        if not found:
            return '1', []
        _, sql, values = found[0]
        return (f'NOT COALESCE(({sql}), 0)', values) if card.negated else (sql, values)

    def _clauses(self, node: expression.Node, scope: list[tuple[str, str, list[Any]]]) -> tuple[list[str], list[Any]]:
        parts, values = [f'({sql})' for _, sql, _ in scope], [v for _, _, vals in scope for v in vals]
        text, found = expression.sql(node, self._card_condition)
        if text != '1':
            parts.append(text)
            values.extend(found)
        return parts, values

    def _where_of(self, node: expression.Node, scope: list[tuple[str, str, list[Any]]]) -> tuple[str, list[Any]]:
        parts, values = self._clauses(node, scope)
        return (' WHERE ' + ' AND '.join(parts), values) if parts else ('', [])

    def _where(self, params: dict[str, list[str]], restrict: Any = None) -> tuple[str, list[Any]]:
        """The WHERE clause for the query's conditions as written, within the library scope."""
        return self._where_of(Query.of(params).where, self._restriction(restrict))

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
            field_col = self._fields_col('fields_json')
            return (
                f"CASE WHEN browse_rank({field_col}, '{key}') IS NULL THEN 1 ELSE 0 END ASC, "
                f"browse_rank({field_col}, '{key}') {direction}, {tie}"
            )
        return f'e.sort_name {direction}, e.id ASC'

    def _row_to_entry(self, row: sqlite3.Row, *, include_provenance: bool = True) -> dict[str, Any]:
        return build_entry_row(row, self._sources_map, self.collection_key, include_provenance=include_provenance)

    # --- CatalogProvider methods ---

    def meta(self) -> dict[str, Any]:
        with self._db() as db:
            self._ensure_static_cache(db)
            entry_table = self._entry_table
            summary_row = db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()
            if summary_row is not None:
                summary = _json_loads(summary_row[0], {})
            elif self.summary_path.is_file():
                summary = _json_loads(self.summary_path.read_text('utf-8'), {})
            else:
                total = db.execute(f'SELECT count(*) FROM {entry_table}').fetchone()[0]
                summary = {'counts': {'entries': total}, 'total': total}

            if 'total' not in summary:
                summary['total'] = (summary.get('counts') or {}).get('entries') or db.execute(f'SELECT count(*) FROM {entry_table}').fetchone()[0]

            src_counts = (
                self._pages_source_counts
                if self._pages_source_counts is not None else
                dict(db.execute(f'SELECT source_id, count(*) FROM {entry_table} GROUP BY source_id'))
            )
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

    def _ensure_type_ruleset_counts(self, db: sqlite3.Connection) -> list[list[Any]]:
        with self._lock:
            if self._pages_type_ruleset_counts is not None:
                return self._pages_type_ruleset_counts
        rows = [
            [r[0], r[1], int(r[2])]
            for r in db.execute(
                f"SELECT type, COALESCE(ruleset, ''), count(*) FROM {self._entry_table} "
                "GROUP BY type, COALESCE(ruleset, '') ORDER BY type, COALESCE(ruleset, '')"
            )
        ]
        with self._lock:
            if self._pages_type_ruleset_counts is None:
                self._pages_type_ruleset_counts = rows
            return self._pages_type_ruleset_counts

    def count(self, params: dict[str, list[str]], restrict: Any = None) -> int:
        """Count matching versions without reading payloads or stacking."""
        with self._db() as db:
            self._ensure_static_cache(db)
            params = Query.of(params)
            if not params.probing:
                simple = catalog.simple_type_ruleset_filter(
                    params, set(otypes.TYPES), set(RULESETS), restrict,
                )
                if simple is not None:
                    types, rulesets = simple
                    counts_rows = self._ensure_type_ruleset_counts(db)
                    return sum(
                        cnt for t, r, cnt in counts_rows
                        if (types is None or t in types) and (rulesets is None or r in rulesets)
                    )
            where, values = self._where(params, restrict)
            from_sql = self._from_entries(need_search_body=self._where_needs_body(where))
            return int(db.execute(f'SELECT count(*) FROM {from_sql}{where}', values).fetchone()[0])

    def entries(self, params: dict[str, list[str]], restrict: Any = None, *, _export: bool = False) -> dict[str, Any]:
        with self._db() as db:
            self._ensure_static_cache(db)
            params = Query.of(params)
            where, values = self._where(params, restrict)
            has_search = bool(_search_terms(params, 'q') or _typing_term(params))
            latest = _latest_term(params)
            db.create_function('browse_query', 0, lambda: latest)

            chosen_types = _entry_types(params)
            config = otypes.schema_for(chosen_types, expression.open_types(params.where))
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

            need_body = self._where_needs_body(where)
            need_fields = self._order_needs_fields(order)
            need_meta = self._has_entry_meta and not self._has_quickref_in_fields
            cols = self._row_select_cols(include_provenance=False)

            if _export or versions == 'each':
                from_count = self._from_entries(need_search_body=need_body)
                total = db.execute(f'SELECT count(*) FROM {from_count}{where}', values).fetchone()[0]
                if not _export:
                    from_list = self._from_entries(need_fields=need_fields, need_search_body=need_body)
                    id_sql = f'SELECT e.id FROM {from_list}{where} ORDER BY {order} LIMIT ? OFFSET ?'
                    ids = [r[0] for r in db.execute(id_sql, [*values, limit, offset])]
                    fetched = self._rows_by_ids(db, ids) if ids else {}
                    rows = [fetched[eid] for eid in ids if eid in fetched]
                    return {
                        'total': total,
                        'versions_total': total,
                        'versions': versions,
                        'prefer': prefer,
                        'offset': offset,
                        'rows': rows,
                        'schema': config,
                        'section_matches': 0,
                    }
                from_export = self._from_entries(need_fields=True, need_meta=need_meta, need_search_body=need_body)
                sql = f'SELECT {cols} FROM {from_export}{where} ORDER BY {order}'
                rows = [self._row_to_entry(r, include_provenance=False) for r in db.execute(sql, values)]
                return {
                    'total': total,
                    'versions_total': total,
                    'versions': versions,
                    'prefer': prefer,
                    'offset': 0,
                    'rows': rows,
                    'schema': config,
                    'section_matches': 0,
                }

            first_choice, second_choice = otypes.RULESET_PREFERENCES[prefer]
            from_stack = self._from_entries(need_fields=need_fields, need_search_body=need_body)
            matched = db.execute(
                'WITH matched AS ('
                '  SELECT e.id, COALESCE(im.group_id, e.id) AS stack_key, im.group_id AS group_id, '
                '         COALESCE(im.is_primary, 0) AS is_primary, '
                f'         ROW_NUMBER() OVER (ORDER BY {order}) AS pos, '
                '         CASE e.ruleset WHEN ? THEN 0 WHEN ? THEN 1 ELSE 2 END AS pref '
                f'  FROM {from_stack} '
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
            fetched = self._rows_by_ids(db, all_ids) if all_ids else {}

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

        if not scope and (type_facets := self._ensure_type_facets(db)) is not None:
            unfiltered = self._pages_unfiltered_facets
            selected_types = chosen_types if chosen_types else list(type_facets.keys())
            if chosen_types and unfiltered is not None:
                scope_total = sum(int(unfiltered['type'].get(t, 0)) for t in selected_types)
            elif unfiltered is not None:
                scope_total = int(unfiltered['total'])
            else:
                scope_total = None
            if scope_total is not None:
                uni: dict[str, set[str]] = {f: set() for f in domain}
                cover: dict[str, int] = {f: 0 for f in domain}
                domain_set = set(domain)
                for typ in selected_types:
                    fmap = type_facets.get(typ)
                    if not fmap:
                        continue
                    for fk, vcounts in fmap.items():
                        if fk not in domain_set:
                            continue
                        u = uni[fk]
                        for fv, cnt in vcounts.items():
                            u.add(fv)
                            if fv != otypes.UNKNOWN:
                                cover[fk] += int(cnt)
                res = (scope_total, uni, cover)
                with self._lock:
                    if len(self._scope_universe_cache) > 64:
                        self._scope_universe_cache.clear()
                    self._scope_universe_cache[cache_key] = res
                return res

        bounds = [f'({sql})' for _, sql, _ in scope]
        vals_t: list[Any] = [v for _, _, vals in scope for v in vals]
        if chosen_types:
            bounds.append('e.type IN (' + ','.join('?' for _ in chosen_types) + ')')
            vals_t.extend(chosen_types)
        where_t = ' WHERE ' + ' AND '.join(bounds) if bounds else ''
        entry_table = self._entry_table

        scope_total = int(db.execute(f'SELECT count(*) FROM {entry_table} e' + where_t, vals_t).fetchone()[0])
        uni: dict[str, set[str]] = {f: set() for f in domain}
        cover: dict[str, int] = {f: 0 for f in domain}
        if domain:
            domain_set = set(domain)
            if where_t:
                sql = (
                    'SELECT ef.facet_key, ef.facet_value, count(*) '
                    f'FROM {entry_table} e CROSS JOIN entry_facets ef ON ef.entry_id = e.id '
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

    def _build_facet_groups(
        self,
        config: dict[str, Any],
        domain: list[str],
        params: Query,
        *,
        type_counts: Counter[str],
        rs_counts: Counter[str],
        or_counts: Counter[str],
        src_counts: Counter[str],
        pkg_counts: Counter[str],
        domain_counts: dict[str, Counter[str]] | None = None,
        uni_map: dict[str, set[str]] | None = None,
        unknown_cover: dict[str, int] | None = None,
        always: int = 0,
    ) -> list[dict[str, Any]]:
        domain_counts = domain_counts or {}
        uni_map = uni_map or {}
        unknown_cover = unknown_cover or {}

        type_group = {
            'key': 'type',
            'label': COMMON_LABELS['type'],
            'mode': 'multi',
            'options': [
                {'value': t, 'label': otypes.TYPES[t], 'count': int(type_counts.get(t, 0)) + always}
                for t in otypes.TYPE_ORDER if t in self._held_types
            ],
        }

        domain_groups: list[dict[str, Any]] = []
        for f in domain:
            fkey = 'f_' + f
            f_counts = domain_counts.get(f, Counter())
            f_universe = set(uni_map.get(f, set())) | set(f_counts.keys())
            for v in (*params.get(fkey, []), *params.get(NOT + fkey, [])):
                if v and v != otypes.NONE:
                    f_universe.add(v)
            options = [
                {
                    'value': v,
                    'label': otypes.option_label(f, v),
                    'count': int(f_counts.get(v, 0)) + always,
                }
                for v in sorted(f_universe, key=lambda val: otypes.option_order(f, val))
            ]
            domain_groups.append({
                'key': fkey,
                'label': otypes.LABELS.get(f, f),
                'mode': 'flag' if f in config['flags'] else 'multi',
                'options': options,
            })
        if len(config['kinds']) > 1:
            domain_groups.sort(key=lambda g: -unknown_cover.get(g['key'][2:], 0))

        rs_group = {
            'key': 'ruleset',
            'label': COMMON_LABELS['ruleset'],
            'mode': 'multi',
            'options': [{'value': v, 'label': v, 'count': int(rs_counts.get(v, 0)) + always} for v in RULESETS],
        }
        or_group = {
            'key': 'origin',
            'label': COMMON_LABELS['origin'],
            'mode': 'multi',
            'options': [{'value': v, 'label': v, 'count': int(or_counts.get(v, 0)) + always} for v in ORIGINS],
        }

        def _universe_with_params(key: str, counts: Counter[str]) -> set[str]:
            u = {s for s, c in counts.items() if c > 0}
            for v in (*params.get(key, []), *params.get(NOT + key, [])):
                if v and v != otypes.NONE:
                    u.add(v)
            return u

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
                    'count': int(src_counts.get(sid, 0)) + always,
                }
                for sid in _universe_with_params('source', src_counts) if sid in self._sources_map
            ),
            key=lambda o: (o['label'].lower(), o['title'], o['value']),
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
                    'count': int(pkg_counts.get(pid, 0)) + always,
                }
                for pid in _universe_with_params('package', pkg_counts) if pid in self._packages_map
            ),
            key=lambda o: (0 if o['value'] == 'official' else (1 if o['ours'] else 2), o['label'], o['value']),
        )
        pkg_group = {'key': 'package', 'label': COMMON_LABELS['package'], 'mode': 'multi', 'options': pkg_options}

        return [type_group, *domain_groups, rs_group, or_group, src_group, pkg_group]

    def facets(self, params: dict[str, list[str]], restrict: Any = None) -> dict[str, Any]:
        with self._db() as db:
            self._ensure_static_cache(db)
            params = Query.of(params)
            chosen_types = _entry_types(params)
            config = otypes.schema_for(chosen_types, expression.open_types(params.where))
            where = params.where
            scope = self._restriction(restrict)
            if where == expression.TRUE and not params.probing and not scope:
                with self._lock:
                    if self._unfiltered_facets_cache is not None:
                        return self._unfiltered_facets_cache
                if self._pages_unfiltered_facets is not None:
                    pre = self._pages_unfiltered_facets
                    total = int(pre['total'])
                    type_counts = Counter(pre['type'])
                    rs_counts = Counter(pre['ruleset'])
                    src_counts = Counter(pre['source'])
                    pkg_counts = Counter(pre['package'])
                    or_counts = Counter(pre['origin'])
                else:
                    entry_table = self._entry_table
                    total = int(db.execute(f'SELECT count(*) FROM {entry_table}').fetchone()[0])
                    type_counts = Counter(dict(db.execute(f'SELECT type, count(*) FROM {entry_table} GROUP BY type')))
                    rs_counts = Counter(dict(db.execute(f'SELECT ruleset, count(*) FROM {entry_table} GROUP BY ruleset')))
                    src_counts = Counter(dict(db.execute(f'SELECT source_id, count(*) FROM {entry_table} GROUP BY source_id')))
                    pkg_counts = Counter(dict(db.execute(f'SELECT package_id, count(*) FROM {entry_table} GROUP BY package_id')))
                    or_counts = Counter(dict(db.execute(
                        "SELECT facet_value, count(*) FROM entry_facets WHERE facet_key='origin' GROUP BY facet_value"
                    )))
                result = {
                    'schema': config,
                    'groups': self._build_facet_groups(
                        config, [], params,
                        type_counts=type_counts, rs_counts=rs_counts, or_counts=or_counts,
                        src_counts=src_counts, pkg_counts=pkg_counts,
                    ),
                    'total': total,
                    'scope_total': total,
                    'without': {},
                    'elsewhere': [],
                }
                with self._lock:
                    self._unfiltered_facets_cache = result
                return result

            # A field counts with its own cards set aside; while the caret probes, every field counts the rows the
            # probe decides, plus those that match wherever it stands (`probe_base`).
            line = params.cards()
            active_fields = set() if params.probing else {c.field for c in line}
            domain = [*config['facets'], *config['flags']]

            # 1. Cached scope total & domain universe for chosen types
            scope_total, uni_map, unknown_cover = self._scope_universe(db, chosen_types, domain, scope)

            # 2. Check fast path when only a single non-negated `type` card is active without scope
            type_facets = None
            if (
                not scope
                and not params.probing
                and active_fields == {'type'}
                and len(line) == 1
                and not line[0].negated
                and line[0].base == 'type'
                and bool(chosen_types)
                and self._pages_unfiltered_facets is not None
                and (type_facets := self._ensure_type_facets(db)) is not None
            ):
                unfiltered = self._pages_unfiltered_facets
                type_counts = Counter(unfiltered['type'])
                t_ph = ','.join('?' for _ in chosen_types)
                entry_tbl = self._entry_table
                core_rows = db.execute(
                    f'SELECT ruleset, source_id, package_id FROM {entry_tbl} WHERE type IN ({t_ph})',
                    chosen_types,
                ).fetchall()
                total = len(core_rows)
                diff_t = int(unfiltered['total']) - total
                without = {str(line[0].index): diff_t} if diff_t > 0 else {}
                batched_counts: dict[str, Counter[str]] = {f: Counter() for f in domain}
                domain_set = set(domain)
                or_counts: Counter[str] = Counter()
                for typ in chosen_types:
                    for fk, vcounts in (type_facets.get(typ) or {}).items():
                        if fk in domain_set:
                            batched_counts[fk].update(vcounts)
                        elif fk == 'origin':
                            or_counts.update(vcounts)
                rs_counts = Counter(r[0] for r in core_rows)
                src_counts = Counter(r[1] for r in core_rows if r[1])
                pkg_counts = Counter(r[2] for r in core_rows if r[2])
                if not or_counts:
                    or_counts = Counter(
                        self._sources_map[r[1]]['origin']
                        for r in core_rows if r[1] in self._sources_map
                    )
                return {
                    'schema': config,
                    'groups': self._build_facet_groups(
                        config, domain, params,
                        type_counts=type_counts, rs_counts=rs_counts, or_counts=or_counts,
                        src_counts=src_counts, pkg_counts=pkg_counts,
                        domain_counts=batched_counts, uni_map=uni_map, unknown_cover=unknown_cover,
                    ),
                    'total': total,
                    'scope_total': scope_total,
                    'without': without,
                    'elsewhere': [],
                }

            # 2b. Fetch matched entries ONCE for total + all unfiltered facet groups
            where_all, vals_all = self._where_of(where, scope)
            from_all = self._from_entries(need_search_body=self._where_needs_body(where_all))
            matched_rows = db.execute(
                f'SELECT e.id, e.ruleset, e.source_id, e.package_id, e.type FROM {from_all}' + where_all,
                vals_all,
            ).fetchall()
            total = len(matched_rows)
            always = 0
            count_rows, count_where, count_vals = matched_rows, where_all, vals_all
            if params.probing:
                at = lambda value: expression.substitute(params.tree, lambda c: value if c is expression.PROBE else c)
                w_u, v_u = self._where_of(at(expression.FALSE), scope)
                from_u = self._from_entries(need_search_body=self._where_needs_body(w_u))
                always = int(db.execute(f'SELECT count(*) FROM {from_u}' + w_u, v_u).fetchone()[0])
                parts, count_vals = self._clauses(at(expression.TRUE), scope)
                unplaced, v_unplaced = expression.sql(at(expression.FALSE), self._card_condition)
                count_where = ' WHERE ' + ' AND '.join([*parts, f'NOT {unplaced}'])
                count_vals = count_vals + v_unplaced
                from_cnt = self._from_entries(need_search_body=self._where_needs_body(count_where))
                count_rows = db.execute(
                    f'SELECT e.id, e.ruleset, e.source_id, e.package_id, e.type FROM {from_cnt}' + count_where,
                    count_vals,
                ).fetchall()
            count_ids = [r[0] for r in count_rows]

            def _conds_without_field(target_field: str) -> tuple[str, list[Any]]:
                return self._where_of(expression.relax(where, lambda c: c.field == target_field), scope)

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
                    from_large = self._from_entries(need_search_body=self._where_needs_body(count_where))
                    sql = (
                        'SELECT ef.facet_key, ef.facet_value, count(*) '
                        f'FROM {from_large} CROSS JOIN entry_facets ef ON ef.entry_id = e.id '
                        + (f'WHERE ({count_where[7:]}) ' if count_where else '')
                        + 'GROUP BY ef.facet_key, ef.facet_value'
                    )
                    for fk, fv, cnt in db.execute(sql, count_vals):
                        if fk in key_set:
                            out[fk][fv] += int(cnt)
                return out

            # 3. Type counts: what choosing each type matches with every other condition kept as written.
            type_counts: Counter[str] = Counter()
            if 'type' not in active_fields:
                type_counts = Counter(r[4] for r in count_rows)
            else:
                w_t, v_t = _conds_without_field('type')
                if not w_t and self._pages_unfiltered_facets is not None:
                    type_counts = Counter(self._pages_unfiltered_facets['type'])
                else:
                    from_t = self._from_entries(need_search_body=self._where_needs_body(w_t))
                    for r in db.execute(f'SELECT e.type, count(*) FROM {from_t}' + w_t + ' GROUP BY e.type', v_t):
                        type_counts[r[0]] = int(r[1])

            # 4. `without`: the matches each card's removal from the line would add, by the card's place.
            without: dict[str, int] = {}
            type_cards = sum(c.field == 'type' for c in line)
            for card in line:
                if card.field == 'type' and type_cards == 1 and not params.probing and expression.required(where, card.index):
                    diff = sum(type_counts.values()) - total
                else:
                    w_sub, v_sub = self._where_of(expression.without(where, card.index), scope)
                    from_sub = self._from_entries(need_search_body=self._where_needs_body(w_sub))
                    diff = int(db.execute(f'SELECT count(*) FROM {from_sub}' + w_sub, v_sub).fetchone()[0]) - total
                if diff > 0:
                    without[str(card.index)] = diff

            # 5. Domain facets & flags: batch ALL unfiltered facets into ONE query!
            unfiltered_domain = [f for f in domain if ('f_' + f) not in active_fields]
            filtered_domain = [f for f in domain if ('f_' + f) in active_fields]
            batched_counts = _count_facets_for_ids(unfiltered_domain, count_ids)

            for f in filtered_domain:
                fkey = 'f_' + f
                w_f, v_f = _conds_without_field(fkey)
                f_counts: Counter[str] = Counter()
                from_f = self._from_entries(need_search_body=self._where_needs_body(w_f))
                sql_counts = (
                    f'SELECT ef.facet_value, count(*) FROM {from_f} '
                    'CROSS JOIN entry_facets ef ON ef.entry_id = e.id '
                    'WHERE ef.facet_key = ?'
                    + (f' AND ({w_f[7:]})' if w_f else '')
                    + ' GROUP BY ef.facet_value'
                )
                for fv, cnt in db.execute(sql_counts, [f, *v_f]):
                    f_counts[fv] = int(cnt)
                batched_counts[f] = f_counts

            # 6. Ruleset, Origin, Source, Package counts (reuse the counted rows whenever unfiltered!)
            if 'ruleset' not in active_fields:
                rs_counts = Counter(r[1] for r in count_rows)
            else:
                w_rs, v_rs = _conds_without_field('ruleset')
                from_rs = self._from_entries(need_search_body=self._where_needs_body(w_rs))
                rs_counts = Counter(dict(db.execute(f'SELECT e.ruleset, count(*) FROM {from_rs}' + w_rs + ' GROUP BY e.ruleset', v_rs)))

            if 'origin' not in active_fields:
                or_counts = Counter(
                    self._sources_map[r[2]]['origin']
                    for r in count_rows if r[2] in self._sources_map
                )
            else:
                w_or, v_or = _conds_without_field('origin')
                if self._has_entry_core and self._where_needs_body(w_or):
                    body_join = (' LEFT JOIN entry_search_body esb ON esb.entry_rowid = e.rowid'
                                 if self._has_search_body_table else ' LEFT JOIN entry_body eb ON eb.entry_rowid = e.rowid')
                else:
                    body_join = ''
                or_counts = Counter(dict(db.execute(
                    f'SELECT ef.facet_value, count(*) FROM entry_facets ef JOIN {self._entry_table} e ON e.id=ef.entry_id{body_join} '
                    "WHERE ef.facet_key='origin'" + (f' AND ({w_or[7:]})' if w_or else '') + ' GROUP BY ef.facet_value',
                    v_or,
                )))

            if 'source' not in active_fields:
                src_counts = Counter(r[2] for r in count_rows if r[2])
            else:
                w_src, v_src = _conds_without_field('source')
                from_src = self._from_entries(need_search_body=self._where_needs_body(w_src))
                src_counts = Counter(dict(db.execute(f'SELECT e.source_id, count(*) FROM {from_src}' + w_src + ' GROUP BY e.source_id', v_src)))

            if 'package' not in active_fields:
                pkg_counts = Counter(r[3] for r in count_rows if r[3])
            else:
                w_pkg, v_pkg = _conds_without_field('package')
                from_pkg = self._from_entries(need_search_body=self._where_needs_body(w_pkg))
                pkg_counts = Counter(dict(db.execute(f'SELECT e.package_id, count(*) FROM {from_pkg}' + w_pkg + ' GROUP BY e.package_id', v_pkg)))

            # 7. Elsewhere hints when current narrowed query returns 0 rows
            elsewhere: list[dict[str, Any]] = []
            if total == 0 and line:
                for relax_field, label in (
                    ('source', '放宽来源限制'),
                    ('package', '放宽资料包限制'),
                    ('type', '放宽类型限制'),
                    ('ruleset', '放宽规则版本'),
                ):
                    if relax_field in active_fields:
                        w_rel, v_rel = _conds_without_field(relax_field)
                        from_rel = self._from_entries(need_search_body=self._where_needs_body(w_rel))
                        rel_cnt = int(db.execute(f'SELECT count(*) FROM {from_rel}' + w_rel, v_rel).fetchone()[0])
                        if rel_cnt > 0:
                            elsewhere.append({'field': relax_field, 'label': label, 'count': rel_cnt})

            return {
                'schema': config,
                'groups': self._build_facet_groups(
                    config, domain, params,
                    type_counts=type_counts, rs_counts=rs_counts, or_counts=or_counts,
                    src_counts=src_counts, pkg_counts=pkg_counts,
                    domain_counts=batched_counts, uni_map=uni_map, unknown_cover=unknown_cover, always=always,
                ),
                'total': total,
                'scope_total': scope_total,
                'without': without,
                'elsewhere': elsewhere,
                **({'probe_base': always} if params.probing else {}),
            }

    # --- Library federation: works, cheap ranking rows and full rows by ID ---

    def works(self) -> list[dict[str, Any]]:
        """Every work with its origin, rules generation and entry count: what the reader's library can include."""
        reader_roots = self._load_reader_roots()
        with self._db() as db:
            self._ensure_static_cache(db)
            counts = (
                self._pages_source_counts
                if self._pages_source_counts is not None else
                dict(db.execute(f'SELECT source_id, count(*) FROM {self._entry_table} GROUP BY source_id'))
            )
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
                    'reader_root': reader_roots.get(sid),
                })
            return out

    def has(self, entry_id: str) -> bool:
        if not entry_id:
            return False
        with self._db() as db:
            self._ensure_static_cache(db)
            entry_table = 'entry_core' if self._has_entry_core else 'entries'
            return db.execute(f'SELECT 1 FROM {entry_table} WHERE id = ? LIMIT 1', (entry_id,)).fetchone() is not None

    def _ensure_name_fold_cache(self, db: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, ...]]:
        with self._lock:
            if self._name_fold_cache is not None:
                return self._name_fold_cache
        from anna.presentation.library import name_fold
        entry_table = 'entry_core' if self._has_entry_core else 'entries'
        kinds = ','.join("'" + k + "'" for k in otypes.STACKABLE_TYPES)
        built: dict[tuple[str, str], list[str]] = {}
        for eid, typ, name_en in db.execute(
            f"SELECT id, type, name_en FROM {entry_table} WHERE type IN ({kinds}) AND name_en != ''"
        ):
            folded = name_fold(name_en)
            if folded:
                built.setdefault((typ, folded), []).append(eid)
        index = {k: tuple(v) for k, v in built.items()}
        with self._lock:
            self._name_fold_cache = index
            return index

    def same_name(self, entry_type: str, folded: str, include_ids: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        """Cheap ranked rows for entries matching a normalized English name (plus explicit IDs)."""
        with self._db() as db:
            self._ensure_bases(db)
            ids = set(include_ids)
            if folded and entry_type in otypes.STACKABLE_TYPES:
                if self._has_name_fold_index:
                    ids.update(r[0] for r in db.execute(
                        'SELECT entry_id FROM name_fold_index WHERE type = ? AND folded = ?',
                        (entry_type, folded),
                    ))
                else:
                    ids.update(self._ensure_name_fold_cache(db).get((entry_type, folded), ()))
            if not ids:
                return []
            ordered_ids = list(dict.fromkeys(ids))
            entry_table = self._entry_table
            out = []
            for start in range(0, len(ordered_ids), 400):
                batch = ordered_ids[start:start + 400]
                ph = ','.join('?' for _ in batch)
                sql = (
                    f'SELECT e.id, e.type, e.name, e.name_zh, e.name_en, e.ruleset, e.source_id, e.source_code, im.group_id '
                    f'FROM {entry_table} e LEFT JOIN identity_members im ON im.entry_id = e.id WHERE e.id IN ({ph})'
                )
                for r in db.execute(sql, batch):
                    source = self._sources_map.get(r['source_id'])
                    base, base_work, base_name_en = self._bases.get(r['id'], (None, None, None))
                    out.append({
                        'id': r['id'], 'type': r['type'], 'name': r['name'], 'name_zh': r['name_zh'], 'name_en': r['name_en'],
                        'ruleset': r['ruleset'] or '待核', 'stack': r['group_id'], 'value': None,
                        'source_key': ((r['source_code'] or '').lower(),), 'origin': source['origin'] if source else '社区',
                        'work': r['source_id'] or None, 'base': base, 'base_work': base_work, 'base_name_en': base_name_en,
                    })
            return out

    def _load_rank_index(self) -> list[dict[str, Any]] | None:
        sig = (_file_sig(self.rank_index_path), _file_sig(self.db_path))
        if sig[0] is None:
            return None
        with self._lock:
            if self._rank_index_cache is not None and self._rank_index_cache[0] == sig:
                return self._rank_index_cache[1]
        raw = json.loads(gzip.decompress(self.rank_index_path.read_bytes()).decode('utf-8'))
        types_lut = raw['types']
        sources_lut = raw['sources']
        codes_lut = raw['codes']
        origins = raw['origins']
        bases = raw['bases']
        parsed: list[dict[str, Any]] = []
        for row in raw['rows']:
            eid, ti, name, nz, name_en, rs, si, ci, gid = row
            name_zh = name if nz is None else nz
            sid = sources_lut[si] if si >= 0 else ''
            code = codes_lut[ci] if ci >= 0 else ''
            base_tuple = bases.get(eid)
            base, base_work, base_name_en = (base_tuple[0], base_tuple[1], base_tuple[2]) if base_tuple else (None, None, None)
            parts = list(dict.fromkeys(p for p in (name, name_zh, name_en) if p))
            search_name = _ascii_lower(' '.join(parts))
            parsed.append({
                'id': eid,
                'type': types_lut[ti],
                'name': name,
                'name_zh': name_zh,
                'name_en': name_en,
                'ruleset': rs,
                'stack': gid,
                'value': None,
                'source_key': (code.lower(),),
                'origin': origins.get(sid, '社区'),
                'work': sid or None,
                'base': base,
                'base_work': base_work,
                'base_name_en': base_name_en,
                'search_name': search_name,
            })
        with self._lock:
            self._rank_index_cache = (sig, parsed)
        return parsed

    def ranked(self, params: dict[str, list[str]], restrict: Any = None, sort_field: str | None = None) -> list[dict[str, Any]]:
        """One cheap row per matching entry: what a library spanning several catalogs sorts, stacks and pages."""
        params = Query.of(params)
        if sort_field and sort_field not in otypes.COLUMN_LABELS and sort_field not in otypes.LABELS:
            sort_field = None
        if sort_field is None and (rank_rows := self._load_rank_index()) is not None:
            _search_terms(params, 'q')
            _typing_term(params)
            simple = catalog.simple_rank_filter(
                params, set(otypes.TYPES), set(RULESETS), restrict=restrict, max_sources=MAX_SOURCES_FILTER,
            )
            if simple is not None:
                types, rulesets, sources, terms = simple
                if types == set() or rulesets == set() or sources == set():
                    return []
                out = []
                for r in rank_rows:
                    if types is not None and r['type'] not in types:
                        continue
                    if rulesets is not None and r['ruleset'] not in rulesets:
                        continue
                    w = r['work'] or ''
                    if sources is not None and w not in sources:
                        continue
                    if terms:
                        sn = r['search_name']
                        if any(term not in sn for term in terms):
                            continue
                    out.append({
                        'id': r['id'], 'type': r['type'], 'name': r['name'], 'name_zh': r['name_zh'],
                        'name_en': r['name_en'], 'ruleset': r['ruleset'], 'stack': r['stack'], 'value': None,
                        'source_key': r['source_key'], 'origin': r['origin'], 'work': r['work'],
                        'base': r['base'], 'base_work': r['base_work'], 'base_name_en': r['base_name_en'],
                    })
                return out
        with self._db() as db:
            self._ensure_bases(db)
            where, values = self._where(params, restrict)
            latest = _latest_term(params)
            db.create_function('browse_query', 0, lambda: latest)
            need_fields = bool(sort_field and sort_field != 'cr')
            field_col = self._fields_col('fields_json')
            value = 'e.cr_num' if sort_field == 'cr' else (f"browse_rank({field_col}, '{sort_field}')" if sort_field else 'NULL')
            from_sql = self._from_entries(need_fields=need_fields, need_search_body=self._where_needs_body(where))
            sql = (f'SELECT e.id, e.type, e.name, e.name_zh, e.name_en, e.ruleset, e.source_id, e.source_code, im.group_id, {value} AS value '
                   f'FROM {from_sql} LEFT JOIN identity_members im ON im.entry_id = e.id{where}')
            out = []
            for r in db.execute(sql, values):
                source = self._sources_map.get(r['source_id'])
                base, base_work, base_name_en = self._bases.get(r['id'], (None, None, None))
                out.append({'id': r['id'], 'type': r['type'], 'name': r['name'], 'name_zh': r['name_zh'], 'name_en': r['name_en'],
                            'ruleset': r['ruleset'] or '待核', 'stack': r['group_id'], 'value': r['value'],
                            'source_key': ((r['source_code'] or '').lower(),), 'origin': source['origin'] if source else '社区',
                            'work': r['source_id'] or None, 'base': base, 'base_work': base_work, 'base_name_en': base_name_en})
            return out

    def _rows_by_ids(self, db: sqlite3.Connection, ids: list[str]) -> dict[str, dict[str, Any]]:
        need_meta = self._has_entry_meta and not self._has_quickref_in_fields
        cols = self._row_select_cols(include_provenance=False)
        from_sql = self._from_entries(need_fields=True, need_meta=need_meta)
        out: dict[str, dict[str, Any]] = {}
        unique_ids = list(dict.fromkeys(ids))
        for start in range(0, len(unique_ids), 400):
            batch = unique_ids[start:start + 400]
            ph = ','.join('?' for _ in batch)
            for r in db.execute(f'SELECT {cols} FROM {from_sql} WHERE e.id IN ({ph})', batch):
                out[r['id']] = self._row_to_entry(r, include_provenance=False)
        return out

    def rows(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        with self._db() as db:
            self._ensure_static_cache(db)
            return self._rows_by_ids(db, ids)

    def schema(self, types: list[str], widened: bool = False) -> dict[str, Any]:
        return otypes.schema_for([t for t in types if t in otypes.TYPES], widened)

    def capabilities(self) -> Any:
        from anna.presentation.collections import Capabilities
        with self._db() as db:
            self._ensure_static_cache(db)
            held = frozenset(t for t in otypes.TYPE_ORDER if t in self._held_types)
            sources = frozenset(self._sources_map.keys())
            packages = frozenset(self._packages_map.keys())
        return Capabilities(
            conditions=self.CONDITIONS,
            facets=frozenset(otypes.LABELS), types=held,
            sources=sources, packages=packages,
            values={'ruleset': frozenset(RULESETS), 'legacy': frozenset(LEGACIES), 'copy_status': frozenset(COPY_STATUSES),
                    'origin': frozenset(ORIGINS)},
            stackable=frozenset(otypes.STACKABLE_TYPES),
            # Identity groups gather recorded reprints only; same-name entries of other works are the library's to stack.
            same_name_groups=self.same_name_groups,
        )

    def signature(self) -> tuple:
        return (str(self.db_path), *(_file_sig(self.db_path) or ()))

    def _version_compare(self, db: sqlite3.Connection, entry_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        im_row = db.execute('SELECT group_id FROM identity_members WHERE entry_id = ?', (entry_id,)).fetchone()
        if im_row is None:
            return [], None
        gid = im_row['group_id']
        need_meta = self._has_entry_meta and not self._has_quickref_in_fields
        cols = self._row_select_cols(include_provenance=False)
        entry_table = self._entry_table
        from_mem = f'identity_members im JOIN {entry_table} e ON e.id = im.entry_id'
        if self._has_entry_core:
            if self._has_entry_fields:
                from_mem += ' LEFT JOIN entry_fields efld ON efld.entry_rowid = e.rowid'
            if need_meta:
                from_mem += ' LEFT JOIN entry_meta em ON em.entry_rowid = e.rowid'
        mem_rows = db.execute(
            f'SELECT {cols} FROM {from_mem} WHERE im.group_id = ? '
            "ORDER BY CASE e.ruleset WHEN '5r' THEN 0 WHEN '5e' THEN 1 ELSE 2 END, im.is_primary DESC, e.id ASC",
            (gid,),
        ).fetchall()
        mem_entries = [self._row_to_entry(mr, include_provenance=False) for mr in mem_rows]
        return build_version_compare(gid, mem_entries)

    def versions(self, entry_id: str) -> dict[str, Any]:
        """Return only the entry's version-comparison data without loading body, layout or relations."""
        if not entry_id:
            raise LookupError('条目不存在')
        with self._db() as db:
            self._ensure_static_cache(db)
            row = db.execute(
                f'SELECT id, type, name_en FROM {self._entry_table} WHERE id = ?',
                (entry_id,),
            ).fetchone()
            if row is None:
                raise LookupError('条目不存在')
            _, versions_compare = self._version_compare(db, entry_id)
            return {
                'id': row['id'],
                'type': row['type'],
                'name_en': row['name_en'] or '',
                'versions': versions_compare,
            }

    def detail(self, entry_id: str) -> dict[str, Any]:
        if not entry_id:
            raise LookupError('条目不存在')
        with self._db() as db:
            self._ensure_static_cache(db)
            cols = self._row_select_cols(include_provenance=True)
            body_col = self._body_col('body_text')
            from_sql = self._from_entries(need_fields=True, need_meta=True, need_body_text=True)
            row = db.execute(
                f'SELECT {cols}, {body_col} AS body_text FROM {from_sql} WHERE e.id = ?',
                (entry_id,),
            ).fetchone()
            if row is None:
                raise LookupError('条目不存在')

            entry = self._row_to_entry(row, include_provenance=True)
            raw_body = row['body_text']
            if self._has_body_text_table and isinstance(raw_body, (bytes, bytearray, memoryview)):
                body_str = gzip.decompress(bytes(raw_body)).decode('utf-8')
            else:
                body_str = raw_body or ''
            entry['body'] = body_str.strip()
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

            # Identity groups / version comparison
            members_compact, versions_compare = self._version_compare(db, entry_id)
            entry['members'] = members_compact
            entry['versions'] = versions_compare

            # Relations (outgoing, enriched with target name/type/source_code without materializing entries view)
            entry_table = self._entry_table
            rel_rows = db.execute(
                'SELECT type AS rel_type, target_id, meta_json FROM relations WHERE source_id = ?',
                (entry_id,),
            ).fetchall()
            targets_map: dict[str, tuple[str, str, str]] = {}
            if rel_rows:
                target_ids = list(dict.fromkeys(r['target_id'] for r in rel_rows if r['target_id']))
                for start in range(0, len(target_ids), 400):
                    batch = target_ids[start:start + 400]
                    ph = ','.join('?' for _ in batch)
                    for tr in db.execute(
                        f'SELECT id, name, type, source_code FROM {entry_table} WHERE id IN ({ph})',
                        batch,
                    ):
                        targets_map[tr['id']] = (tr['name'], tr['type'], tr['source_code'])
            entry['relations'] = build_relations_list(rel_rows, targets_map)

            # Reader location from provenance or anna-reader.json
            loc = entry['provenance'].get('reader_location')
            if not loc:
                loc = self._get_entry_locations().get(entry_id)
            entry['reader_location'] = loc

            return entry

    def layout(self, entry_id: str) -> dict[str, Any]:
        if not entry_id:
            raise LookupError('条目不存在')
        with self._db() as db:
            self._ensure_static_cache(db)
            layout_col = self._layout_col('layout_html')
            prov_col = self._meta_col('provenance_json')
            from_sql = self._from_entries(need_meta=True, need_layout=True)
            row = db.execute(
                f'SELECT {layout_col} AS layout_html, {prov_col} AS provenance_json FROM {from_sql} WHERE e.id = ?',
                (entry_id,),
            ).fetchone()
            if row is None:
                raise LookupError('条目不存在')
            raw_layout = row['layout_html']
            if self._has_layout_gzip and isinstance(raw_layout, (bytes, bytearray, memoryview)):
                html_body = gzip.decompress(bytes(raw_layout)).decode('utf-8')
            else:
                html_body = raw_layout or ''
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
        headers = ['条目ID', '中文名', '英文名', '类型', '规则版本', '资料库', '来源', '资料包', '来源性质', '继承状态', '字段冲突'] + [
            otypes.COLUMN_LABELS.get(f, f) for f in extras
        ]

        def iter_rows():
            for r in rows:
                cells: list[Any] = [
                    r['id'], r['name_zh'], r['name_en'], otypes.TYPES.get(r['type'], r['type']), r['ruleset'],
                    r.get('collection') or self.collection_key, '；'.join(r.get('source_names') or []),
                    r.get('package_id', ''), r.get('origin', ''), r.get('copy_status', 'ok'),
                    '；'.join(r.get('field_conflicts') or []),
                ]
                for f in extras:
                    val = r['fields'].get(f)
                    cells.append(json.dumps(val, ensure_ascii=False) if isinstance(val, (list, dict)) else '' if val is None else str(val))
                yield cells

        return format_csv_bytes(headers, iter_rows())


# ---------------------------------------------------------------------------
# 2. Our5etoolsTour
# ---------------------------------------------------------------------------

def build_subclass_records(
    db: sqlite3.Connection,
    sources_by_id: dict[str, dict[str, Any]],
    entry_table: str = 'entries',
) -> list[dict[str, Any]]:
    """Build compact subclass records for the library tour without scanning full entry payloads."""
    parts_counts = dict(db.execute(
        f"SELECT r.target_id, count(*) FROM relations r JOIN {entry_table} f ON f.id = r.source_id "
        "WHERE r.type = 'part_of' AND f.type = 'class_feature' GROUP BY r.target_id"
    ))
    stacks = dict(db.execute('SELECT entry_id, group_id FROM identity_members'))
    copies = {r[0] for r in db.execute("SELECT DISTINCT source_id FROM relations WHERE type = 'copied_from'")}
    records: list[dict[str, Any]] = []
    for r in db.execute(
        'SELECT id, name, name_zh, name_en, owner_class, ruleset, source_id, source_code '
        f"FROM {entry_table} WHERE type = 'subclass' ORDER BY sort_name, id"
    ):
        sid = r['source_id'] or ''
        srec = sources_by_id.get(sid)
        records.append({
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
    return records


def _pick_subclass_root_feature(feats: list[sqlite3.Row], name: str, name_zh: str) -> sqlite3.Row | None:
    if not feats:
        return None
    min_level = feats[0]['owner_level']
    wanted = {x for x in (name, name_zh) if x}
    return next((f for f in feats if f['owner_level'] == min_level and (f['name'] in wanted or f['name_zh'] in wanted)), None)


def subclass_root_feature_id(
    db: sqlite3.Connection,
    entry_table: str,
    entry_id: str,
    name: str,
    name_zh: str,
    *,
    order_col: str = 'e.rowid',
) -> str | None:
    """Return the root class-feature ID shared with a subclass tour layout, if present."""
    feats = db.execute(
        f'SELECT e.id, e.name, e.name_zh, e.owner_level '
        f'FROM relations r JOIN {entry_table} e ON e.id = r.source_id '
        "WHERE r.target_id = ? AND r.type = 'part_of' AND e.type = 'class_feature' "
        f'ORDER BY COALESCE(e.owner_level, 999), {order_col}',
        (entry_id,),
    ).fetchall()
    root = _pick_subclass_root_feature(feats, name, name_zh)
    return root['id'] if root is not None else None


def merge_subclass_tour_layout(root_layout: dict[str, Any], own_layout: dict[str, Any]) -> dict[str, Any] | None:
    """Combine a subclass's root-feature layout with its own fluff section for tour detail rendering."""
    if root_layout.get('status') == 'ok' and root_layout.get('html'):
        fluff = FLUFF_SECTION_RE.search(own_layout.get('html') or '') if own_layout.get('status') == 'ok' else None
        html = root_layout['html'] + (fluff.group(0) if fluff else '')
        return {**root_layout, 'html': html}
    return None


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
        catalog: Our5etoolsCatalog | None = None,
    ):
        self.data_dir = Path(data_dir).resolve()
        self.collection_key = (collection_key or COLLECTION_KEY).strip() or COLLECTION_KEY
        self.db_path = self.data_dir / 'catalog.sqlite'
        self.nav_path = self.data_dir / 'nav-nodes.json.gz'
        self.subclasses_path = self.data_dir / 'tour-subclasses.json.gz'
        self.reader_path = self.data_dir / 'reader' / 'anna-reader.json'
        self.curation_path = Path(curation_path).resolve() if curation_path else tour_curation.CURATION
        self.lazy_media = bool(lazy_media)
        self.catalog = catalog if catalog is not None else Our5etoolsCatalog(
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
        self._books_by_slug: dict[str, dict[str, Any]] = {}
        self._books_by_node: dict[str, str] = {}
        self._books_order: list[str] = []
        self._packs_by_slug: dict[str, dict[str, Any]] = {}
        self._packs_by_id: dict[str, str] = {}
        self._packs_order: list[str] = []
        self._sources_by_id: dict[str, dict[str, Any]] = {}
        self._choices_by_node: dict[str, list[str]] = defaultdict(list)
        self._subclass_records: list[dict[str, Any]] | None = None

    @property
    def snapshot(self) -> str:
        self._ensure_loaded()
        return self._snapshot

    def _ensure_loaded(self) -> None:
        nav_sig = _file_sig(self.nav_path) or _file_sig(self.reader_path)
        sig = (_file_sig(self.db_path), nav_sig, _file_sig(self.curation_path))
        with self._lock:
            if sig == self._loaded_sig and self._loaded_sig is not None:
                return
            self._reload_locked()
            self._loaded_sig = sig

    def _reload_locked(self) -> None:
        # 1. The curated topics this collection's levels name: choices at its contents nodes, dynamic listings.
        curation = tour_curation.load(self.curation_path)
        self._topics = curation['topics']

        # 2. Load reader navigation tree from compact nav-nodes.json.gz when present
        reader = _load_shared_nav(self.nav_path, self.reader_path) or {}
        self._snapshot = str(reader.get('snapshot') or '')
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
        self._subclass_records = None

        with self.catalog._db() as db:
            self.catalog._ensure_static_cache(db)
            self._sources_by_id = self.catalog._sources_map

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

    # --- What the library tour asks of a collection ---

    def subclass_records(self) -> list[dict[str, Any]]:
        self._ensure_loaded()
        with self._lock:
            if self._subclass_records is not None:
                return self._subclass_records
        if self.subclasses_path.is_file():
            records = json.loads(gzip.decompress(self.subclasses_path.read_bytes()).decode('utf-8'))
        else:
            with self.catalog._db() as db:
                self.catalog._ensure_static_cache(db)
                records = build_subclass_records(db, self._sources_by_id, self.catalog._entry_table)
        with self._lock:
            self._subclass_records = records
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
        return self.catalog.count(query)

    @staticmethod
    def _scope_query(source: str | None, package: str | None) -> dict[str, list[str]]:
        """A book's entries by its source, a package's by the package."""
        return {'source': [source]} if source else {'package': [package]} if package else {}

    def _type_items(self, scope: dict[str, list[str]]) -> list[dict[str, Any]]:
        with self.catalog._db() as db:
            self.catalog._ensure_static_cache(db)
            where, vals = self.catalog._where(scope)
            from_sql = self.catalog._from_entries(need_search_body=self.catalog._where_needs_body(where))
            counts = dict(db.execute(f'SELECT e.type, count(*) FROM {from_sql}' + where + ' GROUP BY e.type', vals))
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

    @staticmethod
    def _decode_body(raw_body: Any) -> str:
        if isinstance(raw_body, (bytes, bytearray, memoryview)):
            return gzip.decompress(bytes(raw_body)).decode('utf-8')
        return raw_body or ''

    def _subclass_parts(self, db: sqlite3.Connection, entry_id: str) -> list[sqlite3.Row]:
        entry_table = self.catalog._entry_table
        body_col = self.catalog._body_col('body_text')
        if self.catalog._has_entry_core:
            body_join = ('LEFT JOIN entry_body_text ebt ON ebt.entry_rowid = e.rowid '
                         if self.catalog._has_body_text_table else 'LEFT JOIN entry_body eb ON eb.entry_rowid = e.rowid ')
        else:
            body_join = ''
        order_col = 'e.build_order' if self.catalog._has_build_order else 'e.rowid'
        return db.execute(
            f'SELECT e.id, e.name, e.name_zh, e.ruleset, e.owner_level, {body_col} AS body_text '
            f'FROM relations r JOIN {entry_table} e ON e.id = r.source_id {body_join}'
            "WHERE r.target_id = ? AND r.type = 'part_of' AND e.type = 'class_feature' "
            f'ORDER BY COALESCE(e.owner_level, 999), {order_col}',
            (entry_id,),
        ).fetchall()

    @staticmethod
    def _subclass_root_feature(feats: list[sqlite3.Row], name: str, name_zh: str) -> sqlite3.Row | None:
        return _pick_subclass_root_feature(feats, name, name_zh)

    def _feature_group(self, feats: list[sqlite3.Row]) -> dict[str, Any]:
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
                item['hint'] = first_sentence(' '.join(self._decode_body(f['body_text']).split())) or None
            part_items.append(item)
        return self._group('parts', part_items)

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
            with self.catalog._db() as db:
                self.catalog._ensure_static_cache(db)
                feats = self._subclass_parts(db, entry_id)
            root = self._subclass_root_feature(feats, d['name'], d.get('name_zh') or '')
            if root is not None:
                siblings = {x for f in feats if f['id'] != root['id'] for x in (f['name'], f['name_zh']) if x}
                prose = self._root_prose(self._decode_body(root['body_text']), siblings)
                if prose:
                    lead_source = prose
            if feats:
                groups.append(self._feature_group(feats))
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
            with self.catalog._db() as db:
                self.catalog._ensure_static_cache(db)
                entry_table = self.catalog._entry_table
                body_col = self.catalog._body_col('body_text')
                if self.catalog._has_entry_core:
                    body_join = ('LEFT JOIN entry_body_text ebt ON ebt.entry_rowid = e.rowid '
                                 if self.catalog._has_body_text_table else 'LEFT JOIN entry_body eb ON eb.entry_rowid = e.rowid ')
                else:
                    body_join = ''
                order_col = 'e.build_order' if self.catalog._has_build_order else 'e.rowid'
                cfeats = db.execute(
                    f'SELECT e.id, e.name, e.ruleset, e.owner_level, {body_col} AS body_text '
                    f'FROM relations r JOIN {entry_table} e ON e.id = r.source_id {body_join}'
                    "WHERE r.target_id = ? AND r.type = 'part_of' AND e.type = 'class_feature' AND e.owner_subclass = '' "
                    f'ORDER BY COALESCE(e.owner_level, 999), {order_col}',
                    (entry_id,),
                ).fetchall()
                csubs = db.execute(
                    'SELECT DISTINCT e.id, e.name, e.name_zh, e.ruleset, e.source_id, e.source_code, e.sort_name '
                    f'FROM relations r JOIN {entry_table} e ON e.id = r.source_id '
                    "WHERE r.target_id = ? AND r.type = 'belongs_to_class' AND e.type = 'subclass'",
                    (entry_id,),
                ).fetchall()
            if cfeats:
                groups.append(self._feature_group(cfeats))
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
        root_id = None
        with self.catalog._db() as db:
            self.catalog._ensure_static_cache(db)
            row = db.execute(
                f'SELECT type, name, name_zh FROM {self.catalog._entry_table} WHERE id = ?',
                (segment,),
            ).fetchone()
            if row is not None and row['type'] == 'subclass':
                order_col = 'e.build_order' if self.catalog._has_build_order else 'e.rowid'
                root_id = subclass_root_feature_id(
                    db, self.catalog._entry_table, segment, row['name'], row['name_zh'] or '',
                    order_col=order_col,
                )
        if root_id is not None:
            merged = merge_subclass_tour_layout(self.catalog.layout(root_id), self.catalog.layout(segment))
            if merged is not None:
                return merged
        return self.catalog.layout(segment)


# ---------------------------------------------------------------------------
# 3. Our5etoolsReader
# ---------------------------------------------------------------------------

class Our5etoolsReader:
    """Stella reader provider for our5etools chapters, book search, and self-contained/lazy media."""

    def __init__(
        self,
        data_dir: Path | str,
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
        self.nav_path = self.data_dir / 'nav-nodes.json.gz'
        self.cached_media_path = self.data_dir / 'cached-media.json'
        self.reader_dir = self.data_dir / 'reader'
        self.reader_path = self.reader_dir / 'anna-reader.json'
        self.media_dir = self.data_dir / 'media'
        self.remotes_path = self.data_dir / 'media-remotes.json'
        self.lazy_media = bool(lazy_media)
        self._lock = threading.Lock()
        self._nav_sig: tuple[int, int, int] | None = None
        self._nav_data: dict[str, Any] = {}
        self._reader_sig: tuple[int, int, int] | None = None
        self._reader_data: dict[str, Any] = {}
        self._cached_media_sig: tuple[int, int, int] | None = None
        self._cached_media_list: list[str] = []
        self._remotes_sig: tuple[int, int, int] | None = None
        self._remotes_entries: dict[str, list[str]] = {}
        self._fetch_locks: dict[str, threading.Lock] = {}
        self._fetch_guard = threading.Lock()
        self._negative_cache: dict[str, float] = {}

    def _load_nav(self) -> dict[str, Any]:
        sig = _file_sig(self.nav_path)
        if sig is None:
            return self._load_reader()
        with self._lock:
            if sig == self._nav_sig:
                return self._nav_data
        data = _load_shared_nav(self.nav_path, self.reader_path)
        if data is None:
            return self._load_reader()
        if data.get('schema') != READER_SCHEMA:
            raise ReaderError('reader_schema_mismatch', 500)
        with self._lock:
            self._nav_data = data
            self._nav_sig = sig
        return data

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
        if not self.lazy_media and (sig := _file_sig(self.cached_media_path)) is not None:
            with self._lock:
                if sig == self._cached_media_sig:
                    return list(self._cached_media_list)
            raw = _json_loads(self.cached_media_path.read_text('utf-8'), None)
            if isinstance(raw, list):
                items = [str(x) for x in raw]
                with self._lock:
                    self._cached_media_list = items
                    self._cached_media_sig = sig
                return list(items)
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
        return str(self._load_nav().get('snapshot') or '')

    def navigation(self) -> dict[str, Any]:
        data = self._load_nav()
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
                        has_core = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entry_core'").fetchone() is not None
                        entry_table = 'entry_core' if has_core else 'entries'
                        exists = db.execute(f'SELECT 1 FROM {entry_table} WHERE id = ?', (entry,)).fetchone() is not None
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
                'preview': _make_search_preview(text, body_l, terms, max_len=420, before=140, after=320),
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

        # Fallback for shared Stella client scripts under PROJECT/web/book/
        if len(posix.parts) == 2 and posix.parts[0] == 'anna-assets' and posix.parts[1] in FALLBACK_BOOK_ASSETS:
            book_root = (paths.PROJECT / 'web' / 'book').resolve()
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
    curation_path: Path | str | None = None,
    *,
    collection_key: str,
    zh: str,
    en: str,
    lazy_media: bool = False,
    lazy_reader: bool = False,
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
    except (OSError, ValueError):
        return None
    if manifest.get('collection') != collection_key:
        return None
    reader_meta = manifest.get('reader')
    if lazy_reader and isinstance(reader_meta, dict):
        if reader_meta.get('schema') != READER_SCHEMA:
            return None
        if reader_meta.get('collection') not in (None, collection_key):
            return None
        snapshot = str(reader_meta.get('snapshot') or manifest.get('snapshot') or '')
        raw_book = str(reader_meta.get('page_base') or f'/{collection_key}-book/')
        raw_media = str(reader_meta.get('media_base') or f'/{collection_key}-media/')
    else:
        try:
            reader_json = json.loads(reader_path.read_text('utf-8'))
        except (OSError, ValueError):
            return None
        if reader_json.get('schema') != READER_SCHEMA or reader_json.get('collection') != collection_key:
            return None
        snapshot = str(reader_json.get('snapshot') or manifest.get('snapshot') or '')
        raw_book = str(reader_json.get('page_base') or f'/{collection_key}-book/')
        raw_media = str(reader_json.get('media_base') or f'/{collection_key}-media/')
    book_base = raw_book if raw_book.endswith('/') else (raw_book + '/')
    media_base = raw_media if raw_media.endswith('/') else (raw_media + '/')
    provider = Our5etoolsCatalog(root, lazy_media=lazy_media, collection_key=collection_key)
    return Collection(
        collection_key, zh, en,
        provider=provider,
        snapshot=snapshot,
        tour=Our5etoolsTour(root, curation_path, lazy_media=lazy_media, collection_key=collection_key, catalog=provider),
        reader=Our5etoolsReader(root, lazy_media=lazy_media, collection_key=collection_key,
                                book_base=book_base, media_base=media_base),
        book_base=book_base,
        media_base=media_base,
    )


def load_our5etools_collection(
    data_dir: Path | str = paths.OUR5ETOOLS_DATA,
    curation_path: Path | str | None = None,
    *,
    lazy_media: bool = False,
    lazy_reader: bool = False,
) -> Collection | None:
    """Load the read-only our5etools Collection when built artifacts are complete; else return None."""
    return _load_5etools_family_collection(
        data_dir, curation_path,
        collection_key=COLLECTION_KEY, zh=COLLECTION_ZH, en=COLLECTION_EN,
        lazy_media=lazy_media, lazy_reader=lazy_reader,
    )


def load_fivetools_collection(
    data_dir: Path | str = paths.FIVETOOLS_DATA,
    curation_path: Path | str | None = None,
    *,
    lazy_media: bool = False,
    lazy_reader: bool = False,
) -> Collection | None:
    """Load the reserved standalone 5etools Collection when built separately; else return None."""
    return _load_5etools_family_collection(
        data_dir, curation_path,
        collection_key=FIVETOOLS_COLLECTION_KEY, zh=FIVETOOLS_COLLECTION_KEY, en=FIVETOOLS_COLLECTION_KEY,
        lazy_media=lazy_media, lazy_reader=lazy_reader,
    )
