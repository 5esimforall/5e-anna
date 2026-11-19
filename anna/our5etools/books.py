"""Unified our5etools book & adventure catalog and Stella-ready HTML chapter builder.

Covers all five readable sources in our5etools:
1. Official books (`data/books.json` + `data/book/book-*.json`)
2. Official adventures (`data/adventures.json` + `data/adventure/adventure-*.json`,
   explicitly noting placeholder files such as `data/adventure/adventure-fraif.json`)
3. Reconstructed brew books (paired strictly by `id`, never by array index)
4. Reconstructed brew adventures (paired strictly by `id`, never by array index)
5. Standalone site books/comics (e.g. `homebrew/likoko; DND5e新人科普漫画.json`)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
import html
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag


READER_COLLECTION = '5etools'
CONTENT_BASE = '/our5etools-book/'
DEFINITION_BASE = '/our5etools-book/definition/'
PAGE_PREFIXES = ('books/', 'adventures/', 'brew-books/', 'brew-adventures/')
PRESENTATION_RESOURCES = (
    {
        'id': 'our5etoolsBookStyles',
        'path': 'anna-assets/our5etools-book.css',
        'kind': 'style',
    },
)
LINK_LAYER_MARKER = b'<!-- anna-link-layer -->'
LINK_LAYER_END = b'<!-- /anna-link-layer -->'

# Top-level TOC dividers matching anna.presentation.tour.DIVIDER (`[—–-]{2,}(.*?)[—–-]{2,}`).
TOC_DIVIDERS = {
    'official_books': ('div_official_books', '——官方规则书——'),
    'official_adventures': ('div_official_adventures', '——官方冒险模组——'),
    'brew_books': ('div_brew_books', '——社区与扩展书籍——'),
    'brew_adventures': ('div_brew_adventures', '——社区与扩展冒险——'),
    'site_books': ('div_site_books', '——本站科普与特辑——'),
}

CATEGORY_LABELS = {
    ('official', 'book'): '官方规则书',
    ('official', 'adventure'): '官方冒险模组',
    ('brew', 'book'): '社区与扩展书籍',
    ('brew', 'adventure'): '社区与扩展冒险',
    ('site', 'book'): '本站科普与特辑',
    ('site', 'adventure'): '本站科普与特辑',
}

ORDINAL_LABELS = {
    'chapter': ('第 ', ' 章'),
    'part': ('第 ', ' 部分'),
    'appendix': ('附录 ', ''),
    'episode': ('第 ', ' 集'),
    'level': ('第 ', ' 层'),
    'act': ('第 ', ' 幕'),
    'section': ('第 ', ' 节'),
}

ANCHORABLE_TYPES = frozenset({
    'section',
    'entries',
    'inset',
    'insetReadaloud',
    'variant',
    'variantInner',
    'variantSub',
    'table',
    'tableGroup',
    'statblock',
    'statblockInline',
    'quote',
    'options',
    'homebrew',
    'abilityDc',
    'abilityAttackMod',
    'abilityGeneric',
    'spellcasting',
})

SAFE_SLUG_RE = re.compile(r'[^a-z0-9_-]+')
TAG_RE = re.compile(r'\{@([a-zA-Z0-9]+)(?:\s+([^{}]*))?\}')
HTML_TAG_RE = re.compile(r'(?s)<[^>]+>')
SCRIPT_STYLE_RE = re.compile(r'(?is)<(script|style)\b[^>]*>.*?</\1>')
BLOCK_BREAK_RE = re.compile(
    r'(?i)<(?:br|/p|/div|/h[1-6]|/li|/tr|/table|/section|/article|/aside|/ul|/ol|/blockquote|/figcaption)\b[^>]*>'
)
SAFE_URL_RE = re.compile(r'^(?:https?://|/)', re.I)


@dataclass
class BookMeta:
    """Normalized metadata and chapter payload for a single readable book or adventure."""

    book_id: str
    title: str
    title_en: str
    source_code: str
    package_id: str
    kind: str  # 'book' | 'adventure'
    group: str
    edition: str
    contents: list[dict[str, Any]]
    data_chapters: list[dict[str, Any]]
    author: str
    translator: str
    cover: dict[str, Any] | None
    maps_entry: dict[str, Any] | None
    book_key: str = ''
    origin: str = 'official'  # 'official' | 'brew' | 'site'
    dir_prefix: str = 'books'
    dir_name: str = ''
    published: str = ''
    storyline: str = ''
    level: dict[str, Any] | None = None
    package_name: str = ''
    ours: bool = False
    chapter_paths: list[str] = field(default_factory=list)
    chapter_anchors: list[dict[tuple[int, ...], str]] = field(default_factory=list)
    chapter_headings: list[list[dict[str, Any]]] = field(default_factory=list)


@dataclass
class BookIndex:
    """Result of phase 1 (`collect_book_index`): ordered catalog + cross-link anchor lookup."""

    books: list[BookMeta]
    anchor_lookup: dict[tuple[str, str, str], tuple[str, str]]
    by_key: dict[str, BookMeta] = field(default_factory=dict)
    by_id: dict[str, list[BookMeta]] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def resolve_anchor(
        self,
        book_id: str,
        chapter: str | int = '',
        header: str = '',
        header_index: int | str | None = None,
        *,
        kind: str = '',
    ) -> tuple[str, str] | None:
        """Resolve a `{@book ...}` or `{@adventure ...}` target to `(page_path, fragment)`."""
        bid = _norm_key(book_id)
        if not bid:
            return None
        ch = _norm_key(str(chapter)) if chapter is not None else ''
        hdr = _norm_key(header)
        prefixes = [f'{kind.lower()}:{bid}', bid] if kind else [bid]
        for bkey in prefixes:
            if hdr and header_index is not None and str(header_index).strip() != '':
                indexed = self.anchor_lookup.get((bkey, ch, f'{hdr}#{str(header_index).strip()}'))
                if indexed:
                    return indexed
            for key in (
                (bkey, ch, hdr),
                (bkey, '', hdr) if hdr else None,
                (bkey, ch, '') if ch else None,
                (bkey, '', ''),
            ):
                if key is None:
                    continue
                hit = self.anchor_lookup.get(key)
                if hit is not None:
                    return hit
        return None


def _norm_key(text: Any) -> str:
    if text is None:
        return ''
    return ' '.join(str(text).strip().split()).casefold()


def _strip_tags_plain(text: Any) -> str:
    """Strip 5etools `{@tag ...}` wrappers down to readable display text."""
    if text is None:
        return ''
    s = str(text)
    for _ in range(6):
        if '{@' not in s:
            break
        nxt = TAG_RE.sub(_tag_to_plain, s)
        if nxt == s:
            break
        s = nxt
    return ' '.join(s.split())


def _tag_to_plain(match: re.Match[str]) -> str:
    tag = match.group(1)
    body = (match.group(2) or '').strip()
    if not body:
        return ''
    parts = body.split('|')
    if tag in ('book', 'adventure', 'filter', 'link', '5etools', 'quickref'):
        return parts[0].strip()
    if tag in ('classFeature',):
        return (parts[4] if len(parts) > 4 and parts[4].strip() else parts[0]).strip()
    if tag in ('subclassFeature',):
        return (parts[6] if len(parts) > 6 and parts[6].strip() else parts[0]).strip()
    if len(parts) >= 3 and parts[2].strip():
        return parts[2].strip()
    return parts[0].strip()


def _join_str(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value if v is not None and str(v).strip()]
        return '、'.join(items)
    return str(value).strip()


def _safe_ascii_slug(raw: str, fallback: str = 'item') -> str:
    cleaned = SAFE_SLUG_RE.sub('-', raw.strip().lower()).strip('-_')
    if not cleaned:
        cleaned = fallback
    return cleaned[:48].rstrip('-_') or fallback


def _safe_brew_dir(package_id: str, book_id: str, used: set[str]) -> str:
    base = _safe_ascii_slug(book_id, fallback='brew')
    digest8 = sha256(f'{package_id}\0{book_id}'.encode('utf-8')).hexdigest()[:8]
    candidate = f'{base}_{digest8}'
    suffix = 2
    while candidate in used:
        candidate = f'{base}_{digest8}_{suffix}'
        suffix += 1
    used.add(candidate)
    return candidate


def _safe_official_dir(book_id: str, used: set[str]) -> str:
    base = _safe_ascii_slug(book_id, fallback='doc')
    candidate = base
    if candidate in used:
        digest8 = sha256(book_id.encode('utf-8')).hexdigest()[:8]
        candidate = f'{base}_{digest8}'
    suffix = 2
    while candidate in used:
        candidate = f'{base}_{suffix}'
        suffix += 1
    used.add(candidate)
    return candidate


def _validate_rel_path(rel_path: str) -> str:
    p = PurePosixPath(rel_path)
    if p.is_absolute() or not p.parts or any(part in ('', '.', '..') for part in p.parts):
        raise ValueError(f'Unsafe relative chapter path: {rel_path!r}')
    if not any(rel_path.startswith(prefix) for prefix in PAGE_PREFIXES):
        raise ValueError(f'Chapter path outside allowed prefixes: {rel_path!r}')
    return p.as_posix()


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text('utf-8'))


def _resolve_edition(
    meta: dict[str, Any],
    source_code: str,
    official_sources: dict[str, dict[str, Any]],
    official_casefold: dict[str, str],
    pkg_meta: dict[str, Any] | None = None,
    *,
    is_official: bool = False,
) -> str:
    explicit = meta.get('edition')
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if is_official:
        src_key = source_code
        if src_key not in official_sources and source_code:
            src_key = official_casefold.get(source_code.casefold(), source_code)
        src_info = official_sources.get(src_key)
        if isinstance(src_info, dict) and src_info.get('is_classic') is not None:
            return 'classic' if src_info['is_classic'] else 'one'
        published = str(meta.get('published') or '')
        if len(published) >= 10:
            return 'one' if published >= '2024-09-17' else 'classic'
    if isinstance(pkg_meta, dict):
        pkg_ed = pkg_meta.get('edition')
        if isinstance(pkg_ed, str) and pkg_ed.strip():
            return pkg_ed.strip()
    return ''


def _lookup_maps_entry(
    maps_data: dict[str, Any],
    book_id: str,
    kind: str,
) -> dict[str, Any] | None:
    if not isinstance(maps_data, dict) or not book_id:
        return None
    direct = maps_data.get(book_id)
    if isinstance(direct, dict) and direct.get('prop') in (None, kind):
        return direct
    target_cf = book_id.casefold()
    for key, val in maps_data.items():
        if key.casefold() == target_cf and isinstance(val, dict):
            if val.get('prop') in (None, kind):
                return val
    return None


def _pair_by_id(
    metas: list[dict[str, Any]],
    datas: list[dict[str, Any]],
    *,
    kind: str,
    package_id: str,
    warnings: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Pair `book`/`bookData` or `adventure`/`adventureData` strictly by `id`, NEVER by index."""
    exact: dict[str, dict[str, Any]] = {}
    ci: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for idx, item in enumerate(datas):
        if not isinstance(item, dict):
            warnings.append({
                'code': 'invalid_book_data_item',
                'package_id': package_id,
                'kind': kind,
                'index': idx,
            })
            continue
        did = str(item.get('id') or '').strip()
        if not did:
            warnings.append({
                'code': 'missing_book_data_id',
                'package_id': package_id,
                'kind': kind,
                'index': idx,
            })
            continue
        if did in exact:
            warnings.append({
                'code': 'duplicate_book_data_id',
                'package_id': package_id,
                'kind': kind,
                'book_id': did,
            })
            continue
        exact[did] = item
        ci[did.casefold()].append((did, item))

    matched_ids: set[str] = set()
    paired: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for meta in metas:
        if not isinstance(meta, dict):
            continue
        mid = str(meta.get('id') or '').strip()
        data_obj: dict[str, Any] | None = None
        if mid and mid in exact:
            data_obj = exact[mid]
            matched_ids.add(mid)
        elif mid and len(ci.get(mid.casefold(), ())) == 1:
            did, data_obj = ci[mid.casefold()][0]
            matched_ids.add(did)
            warnings.append({
                'code': 'case_insensitive_book_id_pair',
                'package_id': package_id,
                'kind': kind,
                'book_id': mid,
                'data_id': did,
            })
        else:
            warnings.append({
                'code': 'missing_book_data',
                'package_id': package_id,
                'kind': kind,
                'book_id': mid,
            })
        chapters: list[dict[str, Any]] = []
        if isinstance(data_obj, dict):
            raw_data = data_obj.get('data')
            if isinstance(raw_data, list):
                chapters = [c for c in raw_data if isinstance(c, dict)]
            else:
                warnings.append({
                    'code': 'invalid_book_data_chapters',
                    'package_id': package_id,
                    'kind': kind,
                    'book_id': mid,
                })
        paired.append((meta, chapters))

    for did in exact:
        if did not in matched_ids:
            warnings.append({
                'code': 'orphan_book_data',
                'package_id': package_id,
                'kind': kind,
                'book_id': did,
            })
    return paired


def _format_chapter_title(
    content_entry: dict[str, Any] | None,
    chapter_data: dict[str, Any] | None,
    chapter_idx: int,
) -> tuple[str, str]:
    """Return `(display_title, english_title)` for a chapter."""
    raw_name = ''
    raw_en = ''
    if isinstance(content_entry, dict):
        raw_name = _strip_tags_plain(content_entry.get('name') or '')
        raw_en = _strip_tags_plain(content_entry.get('ENG_name') or '')
    if not raw_name and isinstance(chapter_data, dict):
        raw_name = _strip_tags_plain(chapter_data.get('name') or '')
    if not raw_en and isinstance(chapter_data, dict):
        raw_en = _strip_tags_plain(chapter_data.get('ENG_name') or '')
    base_title = raw_name or raw_en or f'第 {chapter_idx + 1} 章'

    ordinal = content_entry.get('ordinal') if isinstance(content_entry, dict) else None
    if isinstance(ordinal, dict):
        otype = str(ordinal.get('type') or '').strip().lower()
        ident = ordinal.get('identifier')
        if ident is not None and str(ident).strip() != '':
            prefix, suffix = ORDINAL_LABELS.get(otype, ('', ''))
            ord_label = f'{prefix}{ident}{suffix}'.strip()
            if ord_label and not base_title.startswith(ord_label):
                base_title = f'{ord_label}：{base_title}'
    return base_title, raw_en


def _extract_chapter_anchors(
    chapter: dict[str, Any],
) -> tuple[dict[tuple[int, ...], str], list[dict[str, Any]]]:
    """Deterministically assign `sec-...` anchors to named blocks in a chapter tree."""
    anchors: dict[tuple[int, ...], str] = {}
    headings: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    seq = 0

    def next_anchor(label: str, en_label: str, block_id: str) -> str:
        nonlocal seq
        seq += 1
        seed = en_label or block_id or label
        slug = SAFE_SLUG_RE.sub('-', seed.strip().lower()).strip('-_')
        if slug:
            slug = slug[:36].rstrip('-_')
        base = f'sec-{slug}' if slug else f'sec-{seq}'
        if base in used_ids:
            n = 2
            while f'{base}-{n}' in used_ids:
                n += 1
            base = f'{base}-{n}'
        used_ids.add(base)
        return base

    def walk(node: Any, path: tuple[int, ...], depth: int) -> None:
        if isinstance(node, list):
            for idx, child in enumerate(node):
                walk(child, path + (idx,), depth)
            return
        if not isinstance(node, dict):
            return
        btype = str(node.get('type') or ('entries' if 'entries' in node else ''))
        raw_name = node.get('name') or node.get('caption') or ''
        name = _strip_tags_plain(raw_name)
        name_en = _strip_tags_plain(node.get('ENG_name') or '')
        block_id = str(node.get('id') or '').strip()

        # Assign anchor to any named/id'd anchorable block after the chapter root.
        if path and (name or name_en or block_id) and (btype in ANCHORABLE_TYPES or 'entries' in node or name):
            anchor_id = next_anchor(name, name_en, block_id)
            anchors[path] = anchor_id
            headings.append({
                'path': path,
                'anchor': anchor_id,
                'name': name,
                'name_en': name_en,
                'block_id': block_id,
                'type': btype,
                'depth': depth,
            })

        child_depth = depth + (1 if path and (name or btype in ('section', 'entries')) else 0)
        if isinstance(node.get('entries'), list):
            for idx, child in enumerate(node['entries']):
                walk(child, path + (idx,), child_depth)
        if isinstance(node.get('items'), list):
            for idx, child in enumerate(node['items']):
                walk(child, path + (100000 + idx,), child_depth)
        if isinstance(node.get('tables'), list):
            for idx, child in enumerate(node['tables']):
                walk(child, path + (200000 + idx,), child_depth)

    walk(chapter, (), 0)
    return anchors, headings


def _register_anchor(
    lookup: dict[tuple[str, str, str], tuple[str, str]],
    key: tuple[str, str, str],
    target: tuple[str, str],
) -> None:
    if key not in lookup:
        lookup[key] = target


def _populate_book_anchors(
    book: BookMeta,
    lookup: dict[tuple[str, str, str], tuple[str, str]],
    *,
    allow_global_id: bool,
    allow_kind_id: bool = True,
) -> None:
    """Populate lean, fast `anchor_lookup` entries for a book's chapters and headings."""
    if not book.chapter_paths:
        return

    bid_key = _norm_key(book.book_id)
    kind_bid_key = f'{book.kind}:{bid_key}' if bid_key else ''
    src_key = _norm_key(book.source_code)

    primary_keys: list[str] = []
    if allow_global_id and bid_key:
        primary_keys.append(bid_key)
    if allow_kind_id and kind_bid_key and kind_bid_key not in primary_keys:
        primary_keys.append(kind_bid_key)
    if not primary_keys:
        primary_keys.append(_norm_key(book.book_key))

    alias_keys: list[str] = []
    if allow_global_id and src_key and src_key != bid_key:
        alias_keys.append(src_key)

    first_page = book.chapter_paths[0]
    for bkey in (*primary_keys, *alias_keys):
        _register_anchor(lookup, (bkey, '', ''), (first_page, ''))

    for ch_idx, page_path in enumerate(book.chapter_paths):
        content_entry = book.contents[ch_idx] if ch_idx < len(book.contents) else None
        chapter_data = book.data_chapters[ch_idx] if ch_idx < len(book.data_chapters) else None
        ch_title, ch_en = _format_chapter_title(content_entry, chapter_data, ch_idx)
        raw_ch_name = _strip_tags_plain(
            (content_entry.get('name') if isinstance(content_entry, dict) else '')
            or (chapter_data.get('name') if isinstance(chapter_data, dict) else '')
        )
        ch_idx_str = str(ch_idx)
        ch_name_keys: list[str] = []
        for candidate in (raw_ch_name, ch_title, ch_en):
            nk = _norm_key(candidate)
            if nk and nk != ch_idx_str and nk not in ch_name_keys:
                ch_name_keys.append(nk)

        for bkey in (*primary_keys, *alias_keys):
            _register_anchor(lookup, (bkey, ch_idx_str, ''), (page_path, ''))
            for ckey in ch_name_keys:
                _register_anchor(lookup, (bkey, ckey, ''), (page_path, ''))

        # Index extracted headings from chapter JSON without Cartesian blow-up.
        headings = book.chapter_headings[ch_idx] if ch_idx < len(book.chapter_headings) else []
        occ_counts: dict[str, int] = defaultdict(int)
        heading_anchor_by_name: dict[str, str] = {}
        heading_occ_anchors: dict[tuple[str, int], str] = {}

        for h in headings:
            anchor = h['anchor']
            # Primary labels (Chinese & English heading names)
            for label in (h['name'], h['name_en']):
                hk = _norm_key(label)
                if not hk:
                    continue
                idx_num = occ_counts[hk]
                occ_counts[hk] += 1
                heading_anchor_by_name.setdefault(hk, anchor)
                heading_occ_anchors[(hk, idx_num)] = anchor
                for bkey in primary_keys:
                    _register_anchor(lookup, (bkey, ch_idx_str, hk), (page_path, anchor))
                    if idx_num > 0:
                        _register_anchor(lookup, (bkey, ch_idx_str, f'{hk}#{idx_num}'), (page_path, anchor))
                    _register_anchor(lookup, (bkey, '', hk), (page_path, anchor))
                    if ch_name_keys:
                        _register_anchor(lookup, (bkey, ch_name_keys[0], hk), (page_path, anchor))
                for bkey in alias_keys:
                    _register_anchor(lookup, (bkey, ch_idx_str, hk), (page_path, anchor))

            # Block ID (e.g. "1b0") indexed directly on chapter index.
            bk = _norm_key(h['block_id'])
            if bk:
                for bkey in primary_keys:
                    _register_anchor(lookup, (bkey, ch_idx_str, bk), (page_path, anchor))

        # Index declared headers from `contents[ch_idx]['headers']`.
        if isinstance(content_entry, dict):
            for raw_hdr in content_entry.get('headers') or ():
                if isinstance(raw_hdr, str):
                    h_text = _strip_tags_plain(raw_hdr)
                    h_index = None
                elif isinstance(raw_hdr, dict):
                    h_text = _strip_tags_plain(raw_hdr.get('header') or raw_hdr.get('name') or '')
                    h_index = raw_hdr.get('index')
                else:
                    continue
                hk = _norm_key(h_text)
                if not hk:
                    continue
                anchor = ''
                if isinstance(h_index, int) and (hk, h_index) in heading_occ_anchors:
                    anchor = heading_occ_anchors[(hk, h_index)]
                elif hk in heading_anchor_by_name:
                    anchor = heading_anchor_by_name[hk]
                for bkey in primary_keys:
                    _register_anchor(lookup, (bkey, ch_idx_str, hk), (page_path, anchor))
                    _register_anchor(lookup, (bkey, '', hk), (page_path, anchor))
                    if ch_name_keys:
                        _register_anchor(lookup, (bkey, ch_name_keys[0], hk), (page_path, anchor))


def _load_maps_lookup(inventory: Any, source_root: Path) -> dict[str, Any]:
    lookups = _get_attr(inventory, 'generated_lookups', {}) or {}
    for key in ('data/generated/gendata-maps.json', 'gendata-maps.json', 'maps'):
        val = lookups.get(key)
        if isinstance(val, dict):
            return val
    maps_path = source_root / 'data/generated/gendata-maps.json'
    if maps_path.is_file():
        return _load_json(maps_path)
    return {}


def _extract_pkg_meta(doc: dict[str, Any], pkg_record: dict[str, Any]) -> tuple[str, str, str, bool]:
    """Return `(author, translator, edition, ours)` for a brew/site package."""
    meta = doc.get('_meta') if isinstance(doc.get('_meta'), dict) else {}
    sources = meta.get('sources') if isinstance(meta.get('sources'), list) else []
    authors: list[str] = []
    translators: list[str] = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        for a in (src.get('authors') or ()):
            if isinstance(a, str) and a.strip() and a.strip() not in authors:
                authors.append(a.strip())
        tr = src.get('translator')
        if isinstance(tr, str) and tr.strip() and tr.strip() not in translators:
            translators.append(tr.strip())
    for tr in (meta.get('translators') or ()):
        if isinstance(tr, str) and tr.strip() and tr.strip() not in translators:
            translators.append(tr.strip())
    edition = str(meta.get('edition') or pkg_record.get('edition') or '').strip()
    ours = bool(pkg_record.get('ours', False))
    return '、'.join(authors), '、'.join(translators), edition, ours


def _package_origin(pkg_id: str, pkg_record: dict[str, Any]) -> str:
    if pkg_id.startswith('site:'):
        return 'site'
    kind = str(pkg_record.get('kind') or pkg_record.get('origin') or pkg_record.get('role') or '').lower()
    if kind in ('site', 'site_brew', 'site_package'):
        return 'site'
    rel = str(pkg_record.get('path') or pkg_record.get('relpath') or pkg_record.get('file') or '')
    if rel.startswith('homebrew/') and not rel.startswith('homebrew/_'):
        return 'site'
    return 'brew'


def collect_book_index(inventory: Any) -> BookIndex:
    """Phase 1: Collect all official, brew, and site books/adventures + build `anchor_lookup`."""
    source_root = Path(_get_attr(inventory, 'source_root')).resolve()
    official_sources = _get_attr(inventory, 'official_sources', {}) or {}
    official_casefold = _get_attr(inventory, 'official_source_casefold', {}) or {}
    maps_data = _load_maps_lookup(inventory, source_root)

    warnings: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    books: list[BookMeta] = []

    used_official_book_dirs: set[str] = set()
    used_official_adv_dirs: set[str] = set()
    used_brew_book_dirs: set[str] = set()
    used_brew_adv_dirs: set[str] = set()

    counts = {
        'official_books': 0,
        'official_adventures': 0,
        'brew_books': 0,
        'brew_adventures': 0,
        'site_books': 0,
        'site_adventures': 0,
        'total': 0,
    }

    # 1. Official books (data/books.json + data/book/book-*.json)
    books_json_path = source_root / 'data/books.json'
    official_book_ids: set[str] = set()
    if books_json_path.is_file():
        books_raw = _load_json(books_json_path).get('book', [])
        for meta in books_raw:
            if not isinstance(meta, dict):
                continue
            bid = str(meta.get('id') or '').strip()
            if not bid:
                continue
            official_book_ids.add(bid.lower())
            data_file = source_root / f'data/book/book-{bid.lower()}.json'
            if data_file.is_file():
                data_chapters = [c for c in (_load_json(data_file).get('data') or []) if isinstance(c, dict)]
            else:
                data_chapters = []
                warnings.append({
                    'code': 'missing_official_book_file',
                    'book_id': bid,
                    'path': f'data/book/book-{bid.lower()}.json',
                })
            src = str(meta.get('source') or bid).strip()
            dir_name = _safe_official_dir(bid, used_official_book_dirs)
            contents = [c for c in (meta.get('contents') or []) if isinstance(c, dict)]
            if len(contents) != len(data_chapters):
                warnings.append({
                    'code': 'contents_chapter_count_mismatch',
                    'package_id': 'official',
                    'kind': 'book',
                    'book_id': bid,
                    'contents_count': len(contents),
                    'data_count': len(data_chapters),
                })
            bm = BookMeta(
                book_id=bid,
                title=_strip_tags_plain(meta.get('name') or meta.get('ENG_name') or bid),
                title_en=_strip_tags_plain(meta.get('ENG_name') or ''),
                source_code=src,
                package_id='official',
                kind='book',
                group=str(meta.get('group') or ''),
                edition=_resolve_edition(meta, src, official_sources, official_casefold, is_official=True),
                contents=contents,
                data_chapters=data_chapters,
                author=_join_str(meta.get('author')),
                translator=_join_str(meta.get('translator')),
                cover=meta.get('cover') if isinstance(meta.get('cover'), dict) else None,
                maps_entry=_lookup_maps_entry(maps_data, bid, 'book'),
                book_key=f'book_{dir_name}',
                origin='official',
                dir_prefix='books',
                dir_name=dir_name,
                published=str(meta.get('published') or ''),
            )
            books.append(bm)
            counts['official_books'] += 1

    # 2. Official adventures (data/adventures.json + data/adventure/adventure-*.json)
    advs_json_path = source_root / 'data/adventures.json'
    official_adv_ids: set[str] = set()
    if advs_json_path.is_file():
        advs_raw = _load_json(advs_json_path).get('adventure', [])
        for meta in advs_raw:
            if not isinstance(meta, dict):
                continue
            aid = str(meta.get('id') or '').strip()
            if not aid:
                continue
            official_adv_ids.add(aid.lower())
            data_file = source_root / f'data/adventure/adventure-{aid.lower()}.json'
            if data_file.is_file():
                data_chapters = [c for c in (_load_json(data_file).get('data') or []) if isinstance(c, dict)]
            else:
                data_chapters = []
                warnings.append({
                    'code': 'missing_official_adventure_file',
                    'book_id': aid,
                    'path': f'data/adventure/adventure-{aid.lower()}.json',
                })
            src = str(meta.get('source') or aid).strip()
            dir_name = _safe_official_dir(aid, used_official_adv_dirs)
            contents = [c for c in (meta.get('contents') or []) if isinstance(c, dict)]
            if len(contents) != len(data_chapters):
                warnings.append({
                    'code': 'contents_chapter_count_mismatch',
                    'package_id': 'official',
                    'kind': 'adventure',
                    'book_id': aid,
                    'contents_count': len(contents),
                    'data_count': len(data_chapters),
                })
            bm = BookMeta(
                book_id=aid,
                title=_strip_tags_plain(meta.get('name') or meta.get('ENG_name') or aid),
                title_en=_strip_tags_plain(meta.get('ENG_name') or ''),
                source_code=src,
                package_id='official',
                kind='adventure',
                group=str(meta.get('group') or ''),
                edition=_resolve_edition(meta, src, official_sources, official_casefold, is_official=True),
                contents=contents,
                data_chapters=data_chapters,
                author=_join_str(meta.get('author')),
                translator=_join_str(meta.get('translator')),
                cover=meta.get('cover') if isinstance(meta.get('cover'), dict) else None,
                maps_entry=_lookup_maps_entry(maps_data, aid, 'adventure'),
                book_key=f'adv_{dir_name}',
                origin='official',
                dir_prefix='adventures',
                dir_name=dir_name,
                published=str(meta.get('published') or ''),
                storyline=str(meta.get('storyline') or ''),
                level=meta.get('level') if isinstance(meta.get('level'), dict) else None,
            )
            books.append(bm)
            counts['official_adventures'] += 1

    # Detect placeholder/unlisted adventure data files (specifically data/adventure/adventure-fraif.json).
    adv_dir = source_root / 'data/adventure'
    if adv_dir.is_dir():
        for extra in sorted(adv_dir.glob('adventure-*.json')):
            stem_id = extra.stem[len('adventure-'):].lower()
            if stem_id not in official_adv_ids:
                book_twin = source_root / f'data/book/book-{stem_id}.json'
                note = {
                    'code': 'adventure_placeholder_file',
                    'path': f'data/adventure/{extra.name}',
                    'points_to': f'data/book/book-{stem_id}.json' if book_twin.is_file() else '',
                    'book_id': stem_id,
                    'message': (
                        f'data/adventure/{extra.name} 为占位文件（未列入 adventures.json），'
                        f'正文内容位于 data/book/book-{stem_id}.json。'
                        if book_twin.is_file()
                        else f'data/adventure/{extra.name} 未列入 adventures.json。'
                    ),
                }
                warnings.append(note)
                notes.append(note)

    # 3 & 4 & 5. Brew books, brew adventures, and standalone site books/comics.
    packages = list(_get_attr(inventory, 'packages', []) or [])
    package_docs = _get_attr(inventory, 'package_docs', {}) or {}
    seen_package_ids: set[str] = set()
    seen_site_files: set[str] = set()

    brew_books_list: list[BookMeta] = []
    brew_advs_list: list[BookMeta] = []
    site_books_list: list[BookMeta] = []

    def ingest_package(pkg_id: str, pkg_record: dict[str, Any], doc: dict[str, Any], origin: str) -> None:
        if not isinstance(doc, dict):
            return
        pkg_author, pkg_translator, pkg_edition, ours = _extract_pkg_meta(doc, pkg_record)
        pkg_name = str(
            pkg_record.get('name')
            or pkg_record.get('doc_id')
            or pkg_record.get('file')
            or pkg_id
        )

        # Books paired strictly by id.
        raw_books = doc.get('book') if isinstance(doc.get('book'), list) else []
        raw_book_data = doc.get('bookData') if isinstance(doc.get('bookData'), list) else []
        if raw_books or raw_book_data:
            for meta, data_chapters in _pair_by_id(
                raw_books, raw_book_data, kind='book', package_id=pkg_id, warnings=warnings
            ):
                bid = str(meta.get('id') or '').strip()
                if not bid:
                    continue
                src = str(meta.get('source') or bid).strip()
                dir_name = _safe_brew_dir(pkg_id, bid, used_brew_book_dirs)
                contents = [c for c in (meta.get('contents') or []) if isinstance(c, dict)]
                if len(contents) != len(data_chapters):
                    warnings.append({
                        'code': 'contents_chapter_count_mismatch',
                        'package_id': pkg_id,
                        'kind': 'book',
                        'book_id': bid,
                        'contents_count': len(contents),
                        'data_count': len(data_chapters),
                    })
                key_prefix = 'sbook' if origin == 'site' else 'bbook'
                bm = BookMeta(
                    book_id=bid,
                    title=_strip_tags_plain(meta.get('name') or meta.get('ENG_name') or bid),
                    title_en=_strip_tags_plain(meta.get('ENG_name') or ''),
                    source_code=src,
                    package_id=pkg_id,
                    kind='book',
                    group=str(meta.get('group') or ('site' if origin == 'site' else 'homebrew')),
                    edition=_resolve_edition(
                        meta, src, official_sources, official_casefold,
                        pkg_meta=doc.get('_meta') if isinstance(doc.get('_meta'), dict) else {'edition': pkg_edition},
                        is_official=False,
                    ) or pkg_edition,
                    contents=contents,
                    data_chapters=data_chapters,
                    author=_join_str(meta.get('author')) or pkg_author,
                    translator=_join_str(meta.get('translator')) or pkg_translator,
                    cover=meta.get('cover') if isinstance(meta.get('cover'), dict) else None,
                    maps_entry=_lookup_maps_entry(maps_data, bid, 'book') if origin == 'official' else None,
                    book_key=f'{key_prefix}_{dir_name}',
                    origin=origin,
                    dir_prefix='brew-books',
                    dir_name=dir_name,
                    published=str(meta.get('published') or ''),
                    package_name=pkg_name,
                    ours=ours,
                )
                if origin == 'site':
                    site_books_list.append(bm)
                    counts['site_books'] += 1
                else:
                    brew_books_list.append(bm)
                    counts['brew_books'] += 1

        # Adventures paired strictly by id.
        raw_advs = doc.get('adventure') if isinstance(doc.get('adventure'), list) else []
        raw_adv_data = doc.get('adventureData') if isinstance(doc.get('adventureData'), list) else []
        if raw_advs or raw_adv_data:
            for meta, data_chapters in _pair_by_id(
                raw_advs, raw_adv_data, kind='adventure', package_id=pkg_id, warnings=warnings
            ):
                aid = str(meta.get('id') or '').strip()
                if not aid:
                    continue
                src = str(meta.get('source') or aid).strip()
                dir_name = _safe_brew_dir(pkg_id, aid, used_brew_adv_dirs)
                contents = [c for c in (meta.get('contents') or []) if isinstance(c, dict)]
                if len(contents) != len(data_chapters):
                    warnings.append({
                        'code': 'contents_chapter_count_mismatch',
                        'package_id': pkg_id,
                        'kind': 'adventure',
                        'book_id': aid,
                        'contents_count': len(contents),
                        'data_count': len(data_chapters),
                    })
                key_prefix = 'sadv' if origin == 'site' else 'badv'
                bm = BookMeta(
                    book_id=aid,
                    title=_strip_tags_plain(meta.get('name') or meta.get('ENG_name') or aid),
                    title_en=_strip_tags_plain(meta.get('ENG_name') or ''),
                    source_code=src,
                    package_id=pkg_id,
                    kind='adventure',
                    group=str(meta.get('group') or ('site' if origin == 'site' else 'homebrew')),
                    edition=_resolve_edition(
                        meta, src, official_sources, official_casefold,
                        pkg_meta=doc.get('_meta') if isinstance(doc.get('_meta'), dict) else {'edition': pkg_edition},
                        is_official=False,
                    ) or pkg_edition,
                    contents=contents,
                    data_chapters=data_chapters,
                    author=_join_str(meta.get('author')) or pkg_author,
                    translator=_join_str(meta.get('translator')) or pkg_translator,
                    cover=meta.get('cover') if isinstance(meta.get('cover'), dict) else None,
                    maps_entry=None,
                    book_key=f'{key_prefix}_{dir_name}',
                    origin=origin,
                    dir_prefix='brew-adventures',
                    dir_name=dir_name,
                    published=str(meta.get('published') or ''),
                    storyline=str(meta.get('storyline') or ''),
                    level=meta.get('level') if isinstance(meta.get('level'), dict) else None,
                    package_name=pkg_name,
                    ours=ours,
                )
                if origin == 'site':
                    site_books_list.append(bm)
                    counts['site_adventures'] += 1
                else:
                    brew_advs_list.append(bm)
                    counts['brew_adventures'] += 1

    # Iterate packages in deterministic order.
    for pkg in packages:
        if not isinstance(pkg, dict):
            continue
        pkg_id = str(pkg.get('package_id') or pkg.get('id') or '').strip()
        if not pkg_id or pkg_id in seen_package_ids:
            continue
        doc = (
            package_docs.get(pkg_id)
            or package_docs.get(pkg.get('doc_id') or '')
            or package_docs.get(pkg.get('id') or '')
            or package_docs.get(pkg.get('path') or '')
        )
        if not isinstance(doc, dict):
            continue
        seen_package_ids.add(pkg_id)
        origin = _package_origin(pkg_id, pkg)
        if origin == 'site':
            for candidate in (pkg.get('file'), pkg.get('path'), pkg.get('relpath'), pkg.get('id'), pkg_id):
                if isinstance(candidate, str) and candidate:
                    seen_site_files.add(Path(candidate).name)
        ingest_package(pkg_id, pkg, doc, origin)

    # Catch any package_docs entries not listed in `inventory.packages`.
    for doc_key in sorted(package_docs):
        if doc_key in seen_package_ids:
            continue
        doc = package_docs[doc_key]
        if not isinstance(doc, dict):
            continue
        pkg_id = doc_key if (doc_key.startswith('brew:') or doc_key.startswith('site:')) else f'brew:{doc_key}'
        if pkg_id in seen_package_ids:
            continue
        seen_package_ids.add(pkg_id)
        origin = 'site' if pkg_id.startswith('site:') else 'brew'
        if origin == 'site':
            seen_site_files.add(Path(doc_key.removeprefix('site:')).name)
        ingest_package(pkg_id, {'package_id': pkg_id, 'id': doc_key}, doc, origin)

    # Fallback check for standalone site imports listed in `homebrew/index.json['toImport']`.
    hb_index_path = source_root / 'homebrew/index.json'
    if hb_index_path.is_file():
        hb_index = _load_json(hb_index_path)
        for fname in hb_index.get('toImport') or ():
            if not isinstance(fname, str) or not fname.strip():
                continue
            base_name = Path(fname).name
            if base_name in seen_site_files:
                continue
            site_file = source_root / 'homebrew' / fname
            if not site_file.is_file():
                warnings.append({'code': 'missing_site_brew_file', 'path': f'homebrew/{fname}'})
                continue
            pkg_id = f'site:homebrew/{fname}'
            if pkg_id in seen_package_ids:
                continue
            seen_package_ids.add(pkg_id)
            seen_site_files.add(base_name)
            doc = _load_json(site_file)
            ingest_package(pkg_id, {'package_id': pkg_id, 'file': fname, 'ours': True}, doc, 'site')

    # Sort non-official lists deterministically (ours first, then title/id/package_id).
    brew_books_list.sort(key=lambda b: (not b.ours, b.title, b.book_id.casefold(), b.package_id))
    brew_advs_list.sort(key=lambda b: (not b.ours, b.title, b.book_id.casefold(), b.package_id))
    site_books_list.sort(key=lambda b: (b.title, b.book_id.casefold(), b.package_id))

    books.extend(brew_books_list)
    books.extend(brew_advs_list)
    books.extend(site_books_list)
    counts['total'] = len(books)

    # Precompute chapter paths, deterministic `sec-...` anchors, and `anchor_lookup`.
    anchor_lookup: dict[tuple[str, str, str], tuple[str, str]] = {}
    by_key: dict[str, BookMeta] = {}
    by_id: dict[str, list[BookMeta]] = defaultdict(list)
    seen_kind_id: dict[tuple[str, str], BookMeta] = {}

    for book in books:
        book.chapter_paths = [
            _validate_rel_path(f'{book.dir_prefix}/{book.dir_name}/{idx}.html')
            for idx in range(len(book.data_chapters))
        ]
        book.chapter_anchors = []
        book.chapter_headings = []
        for ch in book.data_chapters:
            ch_anchors, ch_headings = _extract_chapter_anchors(ch)
            book.chapter_anchors.append(ch_anchors)
            book.chapter_headings.append(ch_headings)

        by_key[book.book_key] = book
        bid_cf = _norm_key(book.book_id)
        is_first_for_id = bid_cf not in by_id
        kind_pair = (book.kind, bid_cf)
        is_first_for_kind_id = kind_pair not in seen_kind_id
        by_id[bid_cf].append(book)
        if is_first_for_kind_id:
            seen_kind_id[kind_pair] = book
        else:
            warnings.append({
                'code': 'duplicate_book_id',
                'kind': book.kind,
                'book_id': book.book_id,
                'package_id': book.package_id,
                'primary_package_id': seen_kind_id[kind_pair].package_id,
            })
        if not is_first_for_id and is_first_for_kind_id:
            notes.append({
                'code': 'shared_book_adventure_id',
                'kind': book.kind,
                'book_id': book.book_id,
                'package_id': book.package_id,
                'primary_kind': by_id[bid_cf][0].kind,
            })
        _populate_book_anchors(
            book,
            anchor_lookup,
            allow_global_id=is_first_for_id,
            allow_kind_id=is_first_for_kind_id,
        )

    return BookIndex(
        books=books,
        anchor_lookup=anchor_lookup,
        by_key=by_key,
        by_id=dict(by_id),
        warnings=warnings,
        notes=notes,
        counts=counts,
    )


# ---------------------------------------------------------------------------
# Phase 2: Stella-ready HTML chapters, TOC nodes, DOM bindings, search rows
# ---------------------------------------------------------------------------

def _load_render_module() -> Any | None:
    try:
        from anna.our5etools import render as render_mod
        return render_mod
    except Exception:
        return None


def _safe_url(url: str) -> str | None:
    u = str(url or '').strip()
    if not u:
        return None
    if u.startswith('/__remote/'):
        rest = u[len('/__remote/'):].lstrip('/')
        if not rest or rest.startswith('/'):
            return None
        u = f'https://{rest}'
    if SAFE_URL_RE.match(u):
        return u
    return None


def _media_route(relpath: str) -> str:
    return '/our5etools-media/' + '/'.join(quote(seg, safe='-._~') for seg in relpath.split('/'))


def _remote_media_helpers(render_mod: Any, href_type: str, raw_value: str) -> tuple[str | None, list[str]]:
    """Resolve deterministic cache key and upstream mirror URLs via ``render`` when available."""
    if href_type == 'internal':
        if render_mod is not None and hasattr(render_mod, 'normalize_internal_media_path'):
            san = render_mod.normalize_internal_media_path(raw_value, 'img/')
            urls = render_mod.internal_image_remote_urls(san) if san else []
            return san, urls
        cleaned = '/'.join(p for p in raw_value.strip().replace('\\', '/').lstrip('/').split('/') if p and p != '.')
        if not cleaned or '..' in cleaned.split('/'):
            return None, []
        san = cleaned if cleaned.startswith('img/') else f'img/{cleaned}'
        sub = san.removeprefix('img/')
        quoted = '/'.join(quote(seg, safe="()'!,;-_~") for seg in sub.split('/'))
        return san, [
            f'https://5e.kiwee.top/img/{quoted}',
            f'https://raw.githubusercontent.com/5etools-mirror-3/5etools-img/main/{quoted}',
        ]
    safe = _safe_url(raw_value)
    if not safe or not safe.lower().startswith(('http://', 'https://')):
        return None, []
    if render_mod is not None and hasattr(render_mod, 'remote_media_cache_path'):
        cache_rel = render_mod.remote_media_cache_path(safe)
        return cache_rel, ([safe] if cache_rel else [])
    ext = PurePosixPath(urlparse(safe).path).suffix.lower()
    if ext not in {'.png', '.webp', '.jpg', '.jpeg', '.gif', '.svg', '.avif', '.opus', '.ogg', '.mp3', '.wav', '.m4a'}:
        ext = ''
    return f'ext/{sha256(safe.encode("utf-8")).hexdigest()[:24]}{ext}', [safe]


def _rewrite_reader_hrefs(fragment_html: str) -> str:
    """Rewrite `/read?collection=5etools&...&page=...` links to `/our5etools-book/...` for Stella."""
    if '/read?' not in fragment_html:
        return fragment_html
    soup = BeautifulSoup(fragment_html, 'html.parser')
    changed = False
    for a in soup.find_all('a', href=True):
        href = a['href']
        if not isinstance(href, str) or not href.startswith('/read?'):
            continue
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        page_vals = qs.get('page') or []
        if not page_vals:
            continue
        page_rel = unquote(page_vals[0]).lstrip('/')
        if not any(page_rel.startswith(p) for p in PAGE_PREFIXES):
            continue
        anchor_vals = qs.get('anchor') or []
        anchor = unquote(anchor_vals[0]) if anchor_vals else ''
        new_href = CONTENT_BASE + '/'.join(quote(seg, safe='') for seg in page_rel.split('/'))
        if anchor:
            new_href += '#' + quote(anchor, safe='-_.~')
        a['href'] = new_href
        changed = True
    return str(soup) if changed else fragment_html


def _render_inline_fallback(text: str, resolver: Any = None) -> str:
    """Safe, XSS-escaped inline 5etools tag renderer used when render.py inline helper is unavailable."""
    if not text:
        return ''
    out: list[str] = []
    pos = 0
    for m in TAG_RE.finditer(text):
        if m.start() > pos:
            out.append(html.escape(text[pos:m.start()], quote=True))
        tag = m.group(1)
        body = (m.group(2) or '').strip()
        parts = body.split('|') if body else ['']
        if tag in ('b', 'bold'):
            out.append(f'<strong>{_render_inline_fallback(body, resolver)}</strong>')
        elif tag in ('i', 'italic'):
            out.append(f'<em>{_render_inline_fallback(body, resolver)}</em>')
        elif tag in ('u', 'underline', 'u2', 'underlineDouble'):
            out.append(f'<u>{_render_inline_fallback(body, resolver)}</u>')
        elif tag in ('s', 'strike', 's2', 'strikeDouble'):
            out.append(f'<s>{_render_inline_fallback(body, resolver)}</s>')
        elif tag in ('sup',):
            out.append(f'<sup>{_render_inline_fallback(body, resolver)}</sup>')
        elif tag in ('sub',):
            out.append(f'<sub>{_render_inline_fallback(body, resolver)}</sub>')
        elif tag in ('code',):
            out.append(f'<code>{html.escape(body, quote=True)}</code>')
        elif tag in ('kbd',):
            out.append(f'<kbd>{html.escape(body, quote=True)}</kbd>')
        elif tag in ('dice', 'damage', 'scaledamage', 'scaledice', 'hit', 'dc', 'd20', 'chance', 'recharge'):
            display = parts[1].strip() if len(parts) > 1 and parts[1].strip() else parts[0].strip()
            if tag == 'hit' and display and not display.startswith(('+', '-')):
                display = '+' + display
            elif tag == 'dc' and display and not display.upper().startswith('DC'):
                display = 'DC ' + display
            elif tag == 'recharge':
                display = f'（充能 {display}–6）' if display else '（充能 6）'
            out.append(f'<span class="o5e-roll">{html.escape(display, quote=True)}</span>')
        elif tag in ('book', 'adventure'):
            display = parts[0].strip()
            bid = parts[1].strip() if len(parts) > 1 else ''
            ch = parts[2].strip() if len(parts) > 2 else ''
            hdr = parts[3].strip() if len(parts) > 3 else ''
            target = None
            if resolver is not None and hasattr(resolver, 'resolve_book'):
                try:
                    target = resolver.resolve_book(tag, bid, ch, hdr)
                except TypeError:
                    target = resolver.resolve_book(bid, ch, hdr)
            elif resolver is not None and hasattr(resolver, 'book_anchor_lookup'):
                lookup = resolver.book_anchor_lookup
                if isinstance(lookup, dict):
                    bid_k, ch_k, hdr_k = _norm_key(bid), _norm_key(ch), _norm_key(hdr)
                    for bkey in (f'{tag}:{bid_k}', bid_k):
                        target = (
                            lookup.get((bkey, ch_k, hdr_k))
                            or lookup.get((bkey, ch_k, ''))
                            or (lookup.get((bkey, '', hdr_k)) if hdr_k else None)
                            or lookup.get((bkey, '', ''))
                        )
                        if target:
                            break
            page_rel, frag = '', ''
            if isinstance(target, dict):
                page_rel = str(target.get('page') or '')
                frag = str(target.get('fragment') or target.get('anchor') or '')
            elif isinstance(target, (tuple, list)):
                if len(target) >= 3:
                    page_rel, frag = str(target[1] or ''), str(target[2] or '')
                elif len(target) == 2:
                    page_rel, frag = str(target[0] or ''), str(target[1] or '')
                elif len(target) == 1:
                    page_rel = str(target[0] or '')
            if page_rel:
                href = CONTENT_BASE + '/'.join(quote(seg, safe='') for seg in page_rel.split('/'))
                if frag:
                    href += '#' + quote(frag, safe='-_.~')
                out.append(f'<a class="ref-book" href="{html.escape(href, quote=True)}">{html.escape(display, quote=True)}</a>')
            else:
                out.append(html.escape(display, quote=True))
        elif tag == 'link':
            display = parts[0].strip()
            href = _safe_url(parts[1].strip()) if len(parts) > 1 else None
            if href:
                out.append(
                    f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">'
                    f'{html.escape(display, quote=True)}</a>'
                )
            else:
                out.append(html.escape(display, quote=True))
        else:
            display = _tag_to_plain(m)
            out.append(html.escape(display, quote=True))
        pos = m.end()
    if pos < len(text):
        out.append(html.escape(text[pos:], quote=True))
    return ''.join(out)


def _make_render_context(
    render_mod: Any,
    resolver: Any,
    media_checker: Callable[[str], bool] | None,
    default_source: str = '',
) -> Any | None:
    if render_mod is None or not hasattr(render_mod, 'RenderContext'):
        return None
    kwargs: dict[str, Any] = {'default_source': default_source}
    if resolver is not None:
        kwargs['resolver'] = resolver
    if callable(media_checker):
        kwargs['media_checker'] = media_checker
    try:
        return render_mod.RenderContext(**kwargs)
    except Exception:
        return None


def _render_inline_text(
    text: Any,
    resolver: Any,
    render_mod: Any,
    render_ctx: Any = None,
) -> str:
    if text is None:
        return ''
    s = str(text)
    if render_mod is not None and render_ctx is not None:
        fn = getattr(render_mod, 'render_inline', None)
        if callable(fn):
            try:
                rendered = fn(s, render_ctx)
                if isinstance(rendered, str):
                    return _rewrite_reader_hrefs(rendered)
            except Exception:
                pass
    return _render_inline_fallback(s, resolver)


def _render_image_figure(
    img: dict[str, Any],
    media_checker: Callable[[str], bool] | None,
    resolver: Any,
    render_mod: Any,
    *,
    extra_class: str = '',
    render_ctx: Any = None,
    remote_media_map: dict[str, list[str]] | None = None,
) -> str:
    """Render local offline vs remote image cleanly without ever faking offline assets."""
    href = img.get('href') if isinstance(img.get('href'), dict) else {}
    href_type = str(href.get('type') or '').strip().lower()
    title = _strip_tags_plain(img.get('title') or img.get('altText') or '')
    credit = _strip_tags_plain(img.get('credit') or '')
    caption_parts: list[str] = []
    if title:
        caption_parts.append(html.escape(title, quote=True))
    if credit:
        caption_parts.append(f'<span class="o5e-media-credit">（{html.escape(credit, quote=True)}）</span>')
    caption_html = f'<figcaption>{"".join(caption_parts)}</figcaption>' if caption_parts else ''

    cls = 'o5e-figure' + (f' {extra_class}' if extra_class else '')
    if href_type == 'internal':
        rel = str(href.get('path') or '').lstrip('/')
        san, remote_urls = _remote_media_helpers(render_mod, 'internal', rel)
        local_rel = san or (rel if rel.startswith('img/') else f'img/{rel}')
        is_local = False
        if callable(media_checker):
            is_local = bool(media_checker(local_rel) or media_checker(rel))
        alt = html.escape(title or (Path(rel).stem if rel else '配图'), quote=True)
        if is_local:
            served = local_rel if callable(media_checker) and media_checker(local_rel) else rel
            src = _media_route(served)
            return (
                f'<figure class="{cls}">'
                f'<img src="{html.escape(src, quote=True)}" alt="{alt}" loading="lazy" decoding="async" data-o5e-fallback="1">'
                f'<span class="o5e-media-fallback-note" hidden>远端配图（未离线收录）</span>'
                f'{caption_html}</figure>'
            )
        if san and remote_urls:
            if render_ctx is not None and hasattr(render_ctx, 'record_remote_urls'):
                render_ctx.record_remote_urls(san, remote_urls)
            if remote_media_map is not None:
                bucket = remote_media_map.setdefault(san, [])
                for u in remote_urls:
                    if u not in bucket:
                        bucket.append(u)
        lazy_attr = f' data-lazy-media="{html.escape(_media_route(san), quote=True)}"' if san else ''
        title_attr = f' title="{html.escape(san or rel, quote=True)}"' if (san or rel) else ''
        return (
            f'<figure class="{cls} o5e-media-remote"{lazy_attr} data-media-alt="{alt}"{title_attr}>'
            f'<span class="o5e-media-badge">远端配图（未离线收录）</span>'
            f'{caption_html}</figure>'
        )

    url = _safe_url(str(href.get('url') or ''))
    cache_rel, remote_urls = _remote_media_helpers(render_mod, 'external', url or '')
    if cache_rel and remote_urls:
        if render_ctx is not None and hasattr(render_ctx, 'record_remote_urls'):
            render_ctx.record_remote_urls(cache_rel, remote_urls)
        if remote_media_map is not None:
            bucket = remote_media_map.setdefault(cache_rel, [])
            for u in remote_urls:
                if u not in bucket:
                    bucket.append(u)
    alt = html.escape(title or '配图', quote=True)
    lazy_attr = f' data-lazy-media="{html.escape(_media_route(cache_rel), quote=True)}"' if cache_rel else ''
    link_html = (
        f'<a class="o5e-media-link" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">查看原图 ↗</a>'
        if url
        else ''
    )
    return (
        f'<figure class="{cls} o5e-media-remote"{lazy_attr} data-media-alt="{alt}">'
        f'<span class="o5e-media-badge">远端配图（未离线收录）</span>'
        f'{caption_html}{link_html}</figure>'
    )


def _render_cell(cell: Any, resolver: Any, render_mod: Any, render_ctx: Any = None) -> str:
    if isinstance(cell, dict):
        if cell.get('type') == 'cell':
            if 'entry' in cell:
                return _render_inline_text(cell['entry'], resolver, render_mod, render_ctx)
            if 'roll' in cell and isinstance(cell['roll'], dict):
                roll = cell['roll']
                if 'exact' in roll:
                    return html.escape(str(roll['exact']), quote=True)
                lo = roll.get('min', '')
                hi = roll.get('max', '')
                return html.escape(f'{lo}–{hi}', quote=True)
        return _render_inline_text(cell.get('entry') or cell.get('name') or '', resolver, render_mod, render_ctx)
    return _render_inline_text(cell, resolver, render_mod, render_ctx)


def _render_table_block(
    block: dict[str, Any],
    anchor_id: str | None,
    resolver: Any,
    render_mod: Any,
    render_ctx: Any = None,
) -> str:
    """Render a 5etools table with explicit `<thead>` and `<tbody>` (HTML5 DOM safe)."""
    id_attr = f' id="{html.escape(anchor_id, quote=True)}"' if anchor_id else ''
    parts: list[str] = [f'<div class="table-frame o5e-table-wrap"{id_attr}><table>']
    caption = block.get('caption') or block.get('name')
    if caption:
        parts.append(f'<caption>{_render_inline_text(caption, resolver, render_mod, render_ctx)}</caption>')
    col_labels = block.get('colLabels') or []
    if isinstance(col_labels, list) and col_labels:
        parts.append('<thead><tr>')
        for label in col_labels:
            parts.append(f'<th>{_render_inline_text(label, resolver, render_mod, render_ctx)}</th>')
        parts.append('</tr></thead>')
    rows = block.get('rows') or []
    parts.append('<tbody>')
    for row in rows:
        if isinstance(row, dict) and row.get('type') == 'row':
            cells = row.get('row') or []
        elif isinstance(row, list):
            cells = row
        else:
            continue
        parts.append('<tr>')
        for cell in cells:
            parts.append(f'<td>{_render_cell(cell, resolver, render_mod, render_ctx)}</td>')
        parts.append('</tr>')
    parts.append('</tbody></table>')
    footnotes = block.get('footnotes') or []
    if isinstance(footnotes, list) and footnotes:
        parts.append('<div class="o5e-table-footnotes">')
        for fn in footnotes:
            parts.append(f'<p>{_render_inline_text(fn, resolver, render_mod, render_ctx)}</p>')
        parts.append('</div>')
    parts.append('</div>')
    return ''.join(parts)


def _resolve_statblock_entry(
    block: dict[str, Any],
    resolver: Any,
    entry_lookup: dict[str, list[dict[str, Any]]],
) -> tuple[str | None, str]:
    """Return `(entry_id, display_name)` if a statblock resolves to an our5etools entry."""
    tag = str(block.get('tag') or block.get('dataType') or '').strip()
    name = _strip_tags_plain(
        block.get('name')
        or (block.get('data', {}).get('name') if isinstance(block.get('data'), dict) else '')
        or ''
    )
    source = str(
        block.get('source')
        or (block.get('data', {}).get('source') if isinstance(block.get('data'), dict) else '')
        or ''
    ).strip()
    display = _strip_tags_plain(block.get('displayName') or name or tag or '资料卡')

    if resolver is not None and tag and name:
        for fn_name in ('resolve_entity', 'resolve'):
            fn = getattr(resolver, fn_name, None)
            if callable(fn):
                try:
                    res = fn(tag, name, source)
                    if getattr(res, 'status', None) in ('ok', 'resolved') and getattr(res, 'entry_id', None):
                        return str(res.entry_id), display
                    if isinstance(res, str) and res:
                        return res, display
                    if isinstance(res, dict) and res.get('id'):
                        return str(res['id']), display
                    if isinstance(res, (tuple, list)) and len(res) >= 2 and res[0] in ('ok', 'resolved') and res[1]:
                        return str(res[1]), display
                except Exception:
                    pass
    nk = _norm_key(name)
    if nk and nk in entry_lookup:
        candidates = entry_lookup[nk]
        if source:
            src_cf = source.casefold()
            for c in candidates:
                if str(c.get('source_code') or '').casefold() == src_cf:
                    return str(c['id']), display
        if len(candidates) == 1:
            return str(candidates[0]['id']), display
    return None, display


def _render_chapter_node(
    node: Any,
    path: tuple[int, ...],
    depth: int,
    anchors: dict[tuple[int, ...], str],
    resolver: Any,
    media_checker: Callable[[str], bool] | None,
    render_mod: Any,
    entry_lookup: dict[str, list[dict[str, Any]]],
    render_ctx: Any = None,
) -> str:
    """Recursively render a chapter block tree with exact `sec-...` anchors and clean HTML5."""
    if node is None:
        return ''
    if isinstance(node, str):
        return f'<p>{_render_inline_text(node, resolver, render_mod, render_ctx)}</p>'
    if isinstance(node, list):
        return ''.join(
            _render_chapter_node(
                item, path + (idx,), depth, anchors, resolver, media_checker, render_mod, entry_lookup, render_ctx
            )
            for idx, item in enumerate(node)
        )
    if not isinstance(node, dict):
        return f'<p>{html.escape(str(node), quote=True)}</p>'

    btype = str(node.get('type') or ('entries' if 'entries' in node else ''))
    anchor_id = anchors.get(path)
    id_attr = f' id="{html.escape(anchor_id, quote=True)}"' if anchor_id else ''
    name = node.get('name') or ''
    name_plain = _strip_tags_plain(name)
    h_level = min(6, max(2, depth + 2))
    child_depth = depth + (1 if path and (name_plain or btype in ('section', 'entries')) else 0)

    def render_children(items: Any, offset: int = 0) -> str:
        if not isinstance(items, list):
            return ''
        return ''.join(
            _render_chapter_node(
                child, path + (offset + idx,), child_depth, anchors, resolver, media_checker, render_mod, entry_lookup, render_ctx
            )
            for idx, child in enumerate(items)
        )

    if btype in ('section', 'entries', 'variant', 'variantInner', 'variantSub', 'options', 'homebrew', 'wrapper'):
        cls = f'o5e-block o5e-{_safe_ascii_slug(btype, "section")}'
        heading_html = ''
        if path and name_plain:
            heading_html = f'<h{h_level} class="o5e-heading">{_render_inline_text(name, resolver, render_mod, render_ctx)}</h{h_level}>'
        inner = render_children(node.get('entries'))
        if btype == 'section' and not path:
            return inner
        return f'<section class="{cls}"{id_attr}>{heading_html}{inner}</section>'

    if btype in ('inset', 'insetReadaloud'):
        cls = 'o5e-inset o5e-inset-readaloud' if btype == 'insetReadaloud' else 'o5e-inset'
        heading_html = ''
        if name_plain:
            heading_html = f'<h{h_level} class="o5e-inset-title">{_render_inline_text(name, resolver, render_mod, render_ctx)}</h{h_level}>'
        inner = render_children(node.get('entries'))
        return f'<aside class="{cls}"{id_attr}>{heading_html}{inner}</aside>'

    if btype == 'quote':
        inner = render_children(node.get('entries'))
        by = node.get('by')
        from_src = node.get('from')
        cite_parts = []
        if by:
            cite_parts.append(_render_inline_text(by, resolver, render_mod, render_ctx))
        if from_src:
            cite_parts.append(_render_inline_text(from_src, resolver, render_mod, render_ctx))
        footer = f'<footer>— {", ".join(cite_parts)}</footer>' if cite_parts else ''
        return f'<blockquote class="o5e-quote"{id_attr}>{inner}{footer}</blockquote>'

    if btype == 'list':
        items = node.get('items') or []
        parts = [f'<ul class="o5e-list"{id_attr}>']
        for idx, item in enumerate(items):
            if isinstance(item, str):
                parts.append(f'<li>{_render_inline_text(item, resolver, render_mod, render_ctx)}</li>')
            elif isinstance(item, dict) and item.get('type') in ('item', 'itemSub', 'itemSpell'):
                item_name = item.get('name') or ''
                lead = f'<strong class="o5e-item-name">{_render_inline_text(item_name, resolver, render_mod, render_ctx)}</strong> ' if item_name else ''
                if 'entry' in item:
                    parts.append(f'<li>{lead}{_render_inline_text(item["entry"], resolver, render_mod, render_ctx)}</li>')
                else:
                    sub = _render_chapter_node(
                        item.get('entries') or [], path + (100000 + idx,), child_depth,
                        anchors, resolver, media_checker, render_mod, entry_lookup, render_ctx
                    )
                    parts.append(f'<li>{lead}{sub}</li>')
            else:
                sub = _render_chapter_node(
                    item, path + (100000 + idx,), child_depth,
                    anchors, resolver, media_checker, render_mod, entry_lookup, render_ctx
                )
                parts.append(f'<li>{sub}</li>')
        parts.append('</ul>')
        return ''.join(parts)

    if btype == 'table':
        return _render_table_block(node, anchor_id, resolver, render_mod, render_ctx)

    if btype == 'tableGroup':
        heading_html = f'<h{h_level} class="o5e-heading">{_render_inline_text(name, resolver, render_mod, render_ctx)}</h{h_level}>' if name_plain else ''
        tables_html = render_children(node.get('tables'), offset=200000)
        return f'<section class="o5e-table-group"{id_attr}>{heading_html}{tables_html}</section>'

    if btype == 'image':
        return _render_image_figure(node, media_checker, resolver, render_mod, render_ctx=render_ctx)

    if btype == 'gallery':
        imgs = ''.join(
            _render_image_figure(im, media_checker, resolver, render_mod, render_ctx=render_ctx)
            for im in (node.get('images') or ())
            if isinstance(im, dict)
        )
        return f'<div class="o5e-gallery"{id_attr}>{imgs}</div>'

    if btype in ('statblock', 'statblockInline'):
        def_id, display = _resolve_statblock_entry(node, resolver, entry_lookup)
        def_attrs = (
            f' data-anna-def-id="{html.escape(def_id, quote=True)}" data-anna-def-name="{html.escape(display, quote=True)}"'
            if def_id
            else ''
        )
        title_html = f'<h{h_level} class="o5e-statblock-title"{def_attrs}>{html.escape(display, quote=True)}</h{h_level}>'
        body_parts: list[str] = []
        if btype == 'statblockInline' and isinstance(node.get('data'), dict):
            data = node['data']
            if isinstance(data.get('entries'), list):
                body_parts.append(render_children(data['entries']))
        elif def_id:
            body_parts.append(
                f'<p class="o5e-statblock-ref"><button type="button" class="ref" data-entry="{html.escape(def_id, quote=True)}">'
                f'{html.escape(display, quote=True)}</button></p>'
            )
        else:
            src = str(node.get('source') or '').strip()
            if src:
                body_parts.append(f'<p class="o5e-statblock-meta">来源：{html.escape(src, quote=True)}</p>')
        return f'<section class="o5e-statblock"{id_attr}>{title_html}{"".join(body_parts)}</section>'

    if btype in ('abilityDc', 'abilityAttackMod', 'abilityGeneric'):
        attrs = _join_str(node.get('attributes'))
        text = str(node.get('text') or '')
        label = name_plain or ('豁免 DC' if btype == 'abilityDc' else '攻击加值' if btype == 'abilityAttackMod' else '能力数值')
        detail = f'{label} = {text}' if text else f'{label}（{attrs}）' if attrs else label
        return f'<p class="o5e-ability-formula"{id_attr}><strong>{html.escape(detail, quote=True)}</strong></p>'

    if btype == 'hr':
        return '<hr class="o5e-hr">'

    if btype in ('inline', 'inlineBlock'):
        inner = ''.join(
            _render_inline_text(c, resolver, render_mod, render_ctx) if isinstance(c, str)
            else _render_chapter_node(c, path + (idx,), child_depth, anchors, resolver, media_checker, render_mod, entry_lookup, render_ctx)
            for idx, c in enumerate(node.get('entries') or ())
        )
        return f'<div class="o5e-inline-block"{id_attr}>{inner}</div>'

    if btype == 'link':
        display = _strip_tags_plain(node.get('text') or name or '')
        href_obj = node.get('href') if isinstance(node.get('href'), dict) else {}
        url = _safe_url(str(href_obj.get('url') or ''))
        if url:
            return (
                f'<p><a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
                f'{html.escape(display or url, quote=True)}</a></p>'
            )
        return f'<p>{html.escape(display, quote=True)}</p>' if display else ''

    # Delegate specialized block types (spellcasting, flowchart, refClassFeature, etc.) to render.py when available.
    if render_mod is not None:
        render_block_fn = getattr(render_mod, 'render_block', None)
        render_blocks_fn = getattr(render_mod, 'render_blocks', None)
        try:
            rendered = None
            if callable(render_block_fn) and render_ctx is not None:
                rendered = render_block_fn(node, render_ctx)
            elif callable(render_blocks_fn):
                rendered = render_blocks_fn([node], resolver, media_checker)
            if isinstance(rendered, tuple):
                rendered = rendered[0]
            if isinstance(rendered, str) and rendered.strip():
                rendered = _rewrite_reader_hrefs(rendered)
                return f'<div class="o5e-block o5e-delegated"{id_attr}>{rendered}</div>'
        except Exception:
            pass

    # Graceful fallback: never swallow unknown block content.
    fallback_parts: list[str] = []
    if name_plain:
        fallback_parts.append(f'<h{h_level} class="o5e-heading">{_render_inline_text(name, resolver, render_mod, render_ctx)}</h{h_level}>')
    if 'entry' in node:
        fallback_parts.append(f'<p>{_render_inline_text(node["entry"], resolver, render_mod, render_ctx)}</p>')
    if isinstance(node.get('entries'), list):
        fallback_parts.append(render_children(node['entries']))
    if isinstance(node.get('items'), list):
        fallback_parts.append(render_children(node['items'], offset=100000))
    if isinstance(node.get('headerEntries'), list):
        fallback_parts.append(render_children(node['headerEntries'], offset=300000))
    if isinstance(node.get('footerEntries'), list):
        fallback_parts.append(render_children(node['footerEntries'], offset=400000))
    if not fallback_parts:
        return ''
    return f'<section class="o5e-block o5e-fallback"{id_attr}>{"".join(fallback_parts)}</section>'


def _normalize_tables_html5(soup: BeautifulSoup) -> bool:
    """Ensure every `<table>` wraps direct `<tr>` children in an explicit `<tbody>`."""
    changed = False
    for table in soup.find_all('table'):
        direct_trs = [c for c in list(table.contents) if isinstance(c, Tag) and c.name == 'tr']
        if not direct_trs:
            continue
        tbody = soup.new_tag('tbody')
        first_tr = direct_trs[0]
        first_tr.insert_before(tbody)
        for tr in direct_trs:
            tbody.append(tr.extract())
        changed = True
    return changed


def _utf16_len(text: str) -> int:
    return len(text.encode('utf-16-le')) // 2


def _dom_pointer(body: Tag, text_node: NavigableString, char_offset: int) -> list[Any]:
    """Compute `[childIndices, utf16_offset]` from `document.body`, matching `book/links.js`."""
    path: list[int] = []
    node: Any = text_node
    while node is not body:
        parent = node.parent
        if parent is None:
            raise ValueError('DOM text node outside <body>')
        idx = next(i for i, child in enumerate(parent.contents) if child is node)
        path.append(idx)
        node = parent
    path.reverse()
    return [path, _utf16_len(str(text_node)[:char_offset])]


def _element_text_nodes(element: Tag) -> list[NavigableString]:
    nodes: list[NavigableString] = []
    for desc in element.descendants:
        if isinstance(desc, NavigableString) and type(desc) is NavigableString:
            if str(desc):
                nodes.append(desc)
    return nodes


def _range_spec_for_element(body: Tag, element: Tag) -> dict[str, Any] | None:
    """Build `{a: [path, utf16], b: [path, utf16], text}` spanning an element's text nodes."""
    text_nodes = _element_text_nodes(element)
    if not text_nodes:
        return None
    first = text_nodes[0]
    last = text_nodes[-1]
    full_text = ''.join(str(n) for n in text_nodes)
    if not full_text.strip():
        return None
    # Trim outer whitespace only on the first/last node while keeping exact inner text.
    first_str = str(first)
    last_str = str(last)
    if first is last:
        lo = len(first_str) - len(first_str.lstrip())
        hi = len(first_str.rstrip())
        if hi <= lo:
            return None
        return {
            'a': _dom_pointer(body, first, lo),
            'b': _dom_pointer(body, first, hi),
            'text': first_str[lo:hi],
        }
    return {
        'a': _dom_pointer(body, first, 0),
        'b': _dom_pointer(body, last, len(last_str)),
        'text': full_text,
    }


def _verify_dom_binding(body: Tag, spec: dict[str, Any] | None) -> bool:
    """Verify a binding spec using the exact `book/links.js` `resolve()` + `range().toString()` rules."""
    if not spec:
        return False

    def resolve(ptr: list[Any]) -> tuple[NavigableString, int] | None:
        if not isinstance(ptr, list) or len(ptr) != 2:
            return None
        path, utf16_off = ptr
        node: Any = body
        for idx in path:
            if not isinstance(node, Tag) or not isinstance(idx, int) or idx < 0 or idx >= len(node.contents):
                return None
            node = node.contents[idx]
        if type(node) is not NavigableString:
            return None
        if not isinstance(utf16_off, int) or utf16_off < 0 or utf16_off > _utf16_len(str(node)):
            return None
        return node, utf16_off

    a = resolve(spec.get('a'))
    b = resolve(spec.get('b'))
    if a is None or b is None:
        return False

    def slice_utf16(text: str, start_u16: int, end_u16: int | None = None) -> str:
        raw = text.encode('utf-16-le')
        lo = start_u16 * 2
        hi = len(raw) if end_u16 is None else end_u16 * 2
        return raw[lo:hi].decode('utf-16-le')

    if a[0] is b[0]:
        if a[1] > b[1]:
            return False
        return slice_utf16(str(a[0]), a[1], b[1]) == spec.get('text')

    # Collect text nodes in document order between a[0] and b[0].
    collecting = False
    chunks: list[str] = []
    for desc in body.descendants:
        if type(desc) is not NavigableString:
            continue
        if desc is a[0]:
            collecting = True
            chunks.append(slice_utf16(str(desc), a[1], None))
        elif desc is b[0]:
            if not collecting:
                return False
            chunks.append(slice_utf16(str(desc), 0, b[1]))
            return ''.join(chunks) == spec.get('text')
        elif collecting:
            chunks.append(str(desc))
    return False


def _normalize_entry_record(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        eid = item.get('id')
        name = item.get('name') or item.get('name_zh') or item.get('name_en')
        if not eid or not name:
            return None
        return {
            'id': str(eid),
            'name': str(name),
            'name_zh': str(item.get('name_zh') or ''),
            'name_en': str(item.get('name_en') or item.get('ENG_name') or ''),
            'source_code': str(item.get('source_code') or item.get('source') or ''),
        }
    eid = getattr(item, 'id', None)
    name = getattr(item, 'name', None) or getattr(item, 'name_zh', None) or getattr(item, 'name_en', None)
    if not eid or not name:
        return None
    return {
        'id': str(eid),
        'name': str(name),
        'name_zh': str(getattr(item, 'name_zh', '') or ''),
        'name_en': str(getattr(item, 'name_en', '') or ''),
        'source_code': str(getattr(item, 'source_code', '') or getattr(item, 'source', '') or ''),
    }


def _collect_chapter_entries(
    entry_index_by_book_chapter: Any,
    book: BookMeta,
    ch_idx: int,
) -> dict[str, list[dict[str, Any]]]:
    """Gather candidate entries for `(book, ch_idx)` indexed by normalized name."""
    if not isinstance(entry_index_by_book_chapter, dict) or not entry_index_by_book_chapter:
        return {}
    bid_cf = _norm_key(book.book_id)
    src_cf = _norm_key(book.source_code)
    keys = [
        (book.book_key, ch_idx),
        (book.book_key, str(ch_idx)),
        (book.package_id, bid_cf, ch_idx),
        (book.package_id, src_cf, ch_idx),
        (bid_cf, ch_idx),
        (bid_cf, str(ch_idx)),
        (book.book_id, ch_idx),
        (src_cf, ch_idx),
        (src_cf, str(ch_idx)),
        (book.source_code, ch_idx),
        (bid_cf, None),
        (src_cf, None),
        bid_cf,
        src_cf,
    ]
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_ids: set[str] = set()
    for key in keys:
        items = entry_index_by_book_chapter.get(key)
        if not items:
            continue
        if not isinstance(items, (list, tuple, set)):
            items = [items]
        for raw in items:
            rec = _normalize_entry_record(raw)
            if rec is None or rec['id'] in seen_ids:
                continue
            seen_ids.add(rec['id'])
            for nm in (rec['name'], rec['name_zh'], rec['name_en']):
                nk = _norm_key(nm)
                if nk:
                    by_name[nk].append(rec)
    return dict(by_name)


def _extract_plain_search_text(raw_html: str) -> str:
    cleaned = SCRIPT_STYLE_RE.sub(' ', raw_html)
    body_match = re.search(r'(?is)<body[^>]*>(.*)</body>', cleaned)
    body = body_match.group(1) if body_match else cleaned
    body = BLOCK_BREAK_RE.sub('\n', body)
    text = html.unescape(HTML_TAG_RE.sub('', body)).replace('\u00a0', ' ')
    text = re.sub(r'[ \t\r\f\v]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _inject_link_layer(raw_html_bytes: bytes, payload: dict[str, Any]) -> bytes:
    """Append `<!-- anna-link-layer -->...` after `</html>`, matching `anna/server/linked_book.py`."""
    data = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c')
    addition = (
        LINK_LAYER_MARKER
        + b'<script type="application/json" id="anna-bindings">'
        + data.encode('ascii')
        + b'</script>'
        + b'<script charset="utf-8" src="/our5etools-book/anna-assets/link-labels.js"></script>'
        + b'<script charset="utf-8" src="/our5etools-book/anna-assets/links.js"></script>'
        + b'<script charset="utf-8" src="/our5etools-book/anna-assets/topic.js"></script>'
        + LINK_LAYER_END
    )
    return raw_html_bytes + addition


def _validate_toc_nodes(nodes: list[dict[str, Any]]) -> None:
    """Enforce Stella's `annaReaderModel.index(nodes)` invariant (parent before children, unique IDs)."""
    seen: set[str] = set()
    for node in nodes:
        nid = node.get('id')
        pid = node.get('parent_id', '')
        if not nid or nid in seen or (pid and pid not in seen):
            raise ValueError(f'Invalid Stella TOC node ordering or duplicate id: {nid!r} (parent={pid!r})')
        seen.add(nid)


def _book_bucket(book: BookMeta) -> str:
    if book.origin == 'official':
        return 'official_books' if book.kind == 'book' else 'official_adventures'
    if book.origin == 'site':
        return 'site_books'
    return 'brew_books' if book.kind == 'book' else 'brew_adventures'


def _chapter_maps(book: BookMeta, ch_idx: int) -> list[dict[str, Any]]:
    if not isinstance(book.maps_entry, dict):
        return []
    out: list[dict[str, Any]] = []
    for ch in book.maps_entry.get('chapters') or ():
        if isinstance(ch, dict) and ch.get('ix') == ch_idx:
            for img in ch.get('images') or ():
                if isinstance(img, dict):
                    out.append(img)
    return out


def build_reader_pages(
    book_index: BookIndex | list[BookMeta],
    output_dir: Path,
    resolver: Any = None,
    media_checker: Callable[[str], bool] | None = None,
    entry_index_by_book_chapter: dict[Any, Any] | None = None,
    *,
    snapshot: str,
) -> dict[str, Any]:
    """Phase 2: Generate self-contained Stella HTML chapter files, TOC nodes, and search rows."""
    if isinstance(book_index, BookIndex):
        books = book_index.books
        index_warnings = list(book_index.warnings)
        index_notes = list(book_index.notes)
        index_counts = dict(book_index.counts)
    else:
        books = list(book_index)
        index_warnings = []
        index_notes = []
        index_counts = {'total': len(books)}

    out_root = Path(output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    render_mod = _load_render_module()

    reader_context = {
        'snapshot': snapshot,
        'collection': READER_COLLECTION,
        'content_base': CONTENT_BASE,
        'definition_base': DEFINITION_BASE,
        'page_prefixes': list(PAGE_PREFIXES),
        'presentation_resources': [dict(res) for res in PRESENTATION_RESOURCES],
    }

    # Group books into the five Stella shelf sections in canonical order.
    grouped: dict[str, list[BookMeta]] = {
        'official_books': [],
        'official_adventures': [],
        'brew_books': [],
        'brew_adventures': [],
        'site_books': [],
    }
    for book in books:
        grouped[_book_bucket(book)].append(book)

    nodes: list[dict[str, Any]] = []
    pages_meta: dict[str, dict[str, Any]] = {}
    outputs: dict[str, str] = {}
    search_rows: list[tuple[str, str, str, str, str]] = []
    shared_remote_media: dict[str, list[str]] = {}

    bound_definitions = 0
    bound_references = 0
    binding_mismatches = 0
    chapters_written = 0
    maps_rendered = 0
    empty_books: list[str] = []

    for bucket_key in ('official_books', 'official_adventures', 'brew_books', 'brew_adventures', 'site_books'):
        bucket_books = grouped[bucket_key]
        if not bucket_books:
            continue
        div_id, div_label = TOC_DIVIDERS[bucket_key]
        nodes.append({
            'id': div_id,
            'parent_id': '',
            'label': div_label,
            'path': '',
            'fragment': '',
            'status': 'placeholder',
        })

        for book in bucket_books:
            root_node_id = f'n_{book.book_key}'
            book_meta_payload = {
                'book_id': book.book_id,
                'source_code': book.source_code,
                'package_id': book.package_id,
                'edition': book.edition,
                'kind': book.kind,
                'group': book.group,
                'origin': book.origin,
            }
            if not book.data_chapters:
                empty_books.append(book.book_key)
                nodes.append({
                    'id': root_node_id,
                    'parent_id': '',
                    'label': book.title,
                    'path': '',
                    'fragment': '',
                    'status': 'missing',
                    'meta': book_meta_payload,
                })
                continue

            nodes.append({
                'id': root_node_id,
                'parent_id': '',
                'label': book.title,
                'path': '',
                'fragment': '',
                'status': 'placeholder',
                'meta': book_meta_payload,
            })

            category_label = CATEGORY_LABELS.get((book.origin, book.kind), '规则书')

            for ch_idx, chapter_data in enumerate(book.data_chapters):
                page_path = (
                    book.chapter_paths[ch_idx]
                    if ch_idx < len(book.chapter_paths)
                    else _validate_rel_path(f'{book.dir_prefix}/{book.dir_name}/{ch_idx}.html')
                )
                content_entry = book.contents[ch_idx] if ch_idx < len(book.contents) else None
                ch_title, ch_en = _format_chapter_title(content_entry, chapter_data, ch_idx)
                ch_node_id = f'n_{book.book_key}_{ch_idx}'

                nodes.append({
                    'id': ch_node_id,
                    'parent_id': root_node_id,
                    'label': ch_title,
                    'path': page_path,
                    'fragment': '',
                    'status': 'present',
                })

                anchors = (
                    book.chapter_anchors[ch_idx]
                    if ch_idx < len(book.chapter_anchors)
                    else _extract_chapter_anchors(chapter_data)[0]
                )
                headings = (
                    book.chapter_headings[ch_idx]
                    if ch_idx < len(book.chapter_headings)
                    else _extract_chapter_anchors(chapter_data)[1]
                )

                # Emit top-level section sub-nodes under the chapter node (parent always emitted first).
                sec_sub_idx = 0
                for h in headings:
                    if h['depth'] == 0 and h['name']:
                        nodes.append({
                            'id': f'{ch_node_id}_s{sec_sub_idx}',
                            'parent_id': ch_node_id,
                            'label': h['name'],
                            'path': page_path,
                            'fragment': h['anchor'],
                            'status': 'present',
                        })
                        sec_sub_idx += 1

                entry_lookup = _collect_chapter_entries(entry_index_by_book_chapter, book, ch_idx)

                # Render chapter HTML body.
                meta_pills: list[str] = []
                if book.source_code:
                    meta_pills.append(f'<span class="o5e-pill o5e-pill-source">{html.escape(book.source_code, quote=True)}</span>')
                if book.edition:
                    ed_label = '2024 / 5r' if book.edition == 'one' else '2014 / 5e' if book.edition == 'classic' else book.edition
                    meta_pills.append(f'<span class="o5e-pill o5e-pill-edition">{html.escape(ed_label, quote=True)}</span>')
                if book.author:
                    meta_pills.append(f'<span class="o5e-pill">作者：{html.escape(book.author, quote=True)}</span>')
                if book.translator:
                    meta_pills.append(f'<span class="o5e-pill">译者：{html.escape(book.translator, quote=True)}</span>')
                if book.published:
                    meta_pills.append(f'<span class="o5e-pill">{html.escape(book.published, quote=True)}</span>')

                render_ctx = _make_render_context(render_mod, resolver, media_checker, default_source=book.source_code)

                cover_html = ''
                if ch_idx == 0 and isinstance(book.cover, dict):
                    cover_img = {'href': book.cover, 'title': book.title}
                    cover_html = _render_image_figure(
                        cover_img, media_checker, resolver, render_mod, extra_class='o5e-cover',
                        render_ctx=render_ctx, remote_media_map=shared_remote_media,
                    )

                body_html = _render_chapter_node(
                    chapter_data, (), 0, anchors, resolver, media_checker, render_mod, entry_lookup, render_ctx
                )

                ch_maps = _chapter_maps(book, ch_idx)
                maps_html = ''
                if ch_maps:
                    maps_rendered += len(ch_maps)
                    map_figs = ''.join(
                        _render_image_figure(
                            m, media_checker, resolver, render_mod, extra_class='o5e-map',
                            render_ctx=render_ctx, remote_media_map=shared_remote_media,
                        )
                        for m in ch_maps
                    )
                    maps_html = (
                        '<section class="o5e-chapter-maps" id="sec-chapter-maps">'
                        '<h2 class="o5e-heading">本章地图</h2>'
                        f'<div class="o5e-maps-grid">{map_figs}</div>'
                        '</section>'
                    )

                if render_ctx is not None and isinstance(getattr(render_ctx, 'remote_media_map', None), dict):
                    for k_rel, u_list in render_ctx.remote_media_map.items():
                        bucket = shared_remote_media.setdefault(k_rel, [])
                        for u in u_list:
                            if u not in bucket:
                                bucket.append(u)

                subtitle_html = (
                    f'<p class="o5e-chapter-subtitle">{html.escape(ch_en, quote=True)}</p>'
                    if ch_en and ch_en.casefold() != ch_title.casefold()
                    else ''
                )
                doc_title = f'{book.title} — {ch_title}' if ch_title != book.title else book.title

                raw_doc_html = (
                    '<!DOCTYPE html>'
                    '<html lang="zh-CN">'
                    '<head>'
                    '<meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    f'<title>{html.escape(doc_title, quote=True)}</title>'
                    '<link id="our5etoolsBookStyles" rel="stylesheet" href="/our5etools-book/anna-assets/our5etools-book.css">'
                    '</head>'
                    '<body>'
                    f'<article class="o5e-chapter" data-book-id="{html.escape(book.book_id, quote=True)}" data-chapter="{ch_idx}">'
                    '<header class="o5e-chapter-header">'
                    f'<div class="o5e-book-kicker">{html.escape(book.title, quote=True)}</div>'
                    f'<h1 class="o5e-chapter-title">{html.escape(ch_title, quote=True)}</h1>'
                    f'{subtitle_html}'
                    f'<div class="o5e-chapter-meta">{"".join(meta_pills)}</div>'
                    f'{cover_html}'
                    '</header>'
                    f'<div class="o5e-chapter-body">{body_html}{maps_html}</div>'
                    '</article>'
                    '</body>'
                    '</html>'
                )

                # Normalize tables to explicit <tbody> before computing DOM bindings,
                # then re-parse the exact serialized bytes so BeautifulSoup and browser DOM match 1-to-1.
                soup = BeautifulSoup(raw_doc_html, 'html.parser')
                if _normalize_tables_html5(soup):
                    raw_doc_html = str(soup)
                    soup = BeautifulSoup(raw_doc_html, 'html.parser')

                body_tag = soup.body
                definitions: list[dict[str, Any]] = []
                references: list[dict[str, Any]] = []
                page_entries: list[dict[str, Any]] = []
                bound_def_ids: set[str] = set()

                if body_tag is not None:
                    # 1. Bind matching entry headings & statblocks to `definitions`.
                    for heading in body_tag.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                        matched_id = heading.get('data-anna-def-id')
                        matched_name = heading.get('data-anna-def-name')
                        if not matched_id and entry_lookup:
                            h_text = _norm_key(heading.get_text())
                            cands = entry_lookup.get(h_text)
                            if cands:
                                chosen = next((c for c in cands if c['id'] not in bound_def_ids), None)
                                if chosen is not None:
                                    matched_id = chosen['id']
                                    matched_name = chosen['name']
                        if not matched_id or matched_id in bound_def_ids:
                            continue
                        title_spec = _range_spec_for_element(body_tag, heading)
                        if not title_spec or not _verify_dom_binding(body_tag, title_spec):
                            if title_spec is not None:
                                binding_mismatches += 1
                            continue
                        container = heading.find_parent(['section', 'aside', 'article']) or heading
                        body_spec = _range_spec_for_element(body_tag, container)
                        if not body_spec or not _verify_dom_binding(body_tag, body_spec):
                            body_spec = title_spec
                        bound_def_ids.add(str(matched_id))
                        definitions.append({
                            'id': str(matched_id),
                            'name': str(matched_name or title_spec['text']),
                            'title': title_spec,
                            'body': body_spec,
                        })
                        page_entries.append({
                            'id': str(matched_id),
                            'name': str(matched_name or title_spec['text']),
                            'fragment': f'anna-{matched_id}',
                            'locatable': True,
                        })
                        bound_definitions += 1

                    # 2. Bind inline entity reference buttons (`button.ref[data-entry]`) to `references`.
                    ref_seq = 0
                    for btn in body_tag.find_all('button', class_='ref'):
                        target_id = btn.get('data-entry')
                        if not isinstance(target_id, str) or not target_id.strip():
                            continue
                        ref_spec = _range_spec_for_element(body_tag, btn)
                        if not ref_spec or not _verify_dom_binding(body_tag, ref_spec):
                            if ref_spec is not None:
                                binding_mismatches += 1
                            continue
                        ref_seq += 1
                        references.append({
                            'id': target_id.strip(),
                            'reference_id': f'{book.book_key}_{ch_idx}_r{ref_seq}',
                            'range': ref_spec,
                        })
                        bound_references += 1

                payload = {
                    'definitions': definitions,
                    'references': references,
                    'reader': reader_context,
                }
                final_bytes = _inject_link_layer(raw_doc_html.encode('utf-8'), payload)
                dest = out_root / page_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(final_bytes)

                file_sha = sha256(final_bytes).hexdigest()
                outputs[page_path] = file_sha
                pages_meta[page_path] = {
                    'title': doc_title,
                    'book_id': book.book_id,
                    'book_key': book.book_key,
                    'book_title': book.title,
                    'source_code': book.source_code,
                    'package_id': book.package_id,
                    'kind': book.kind,
                    'chapter_index': ch_idx,
                    'chapter_title': ch_title,
                    'definitions': page_entries,
                }
                plain_text = _extract_plain_search_text(raw_doc_html)
                search_rows.append((page_path, doc_title, f'{category_label} / {book.title}', plain_text, file_sha))
                chapters_written += 1

    _validate_toc_nodes(nodes)

    report = {
        'counts': index_counts,
        'chapters_written': chapters_written,
        'toc_nodes': len(nodes),
        'bound_definitions': bound_definitions,
        'bound_references': bound_references,
        'binding_mismatches': binding_mismatches,
        'maps_rendered': maps_rendered,
        'empty_books': empty_books,
        'warnings': index_warnings,
        'notes': index_notes,
    }
    return {
        'nodes': nodes,
        'pages_meta': pages_meta,
        'outputs': outputs,
        'search_rows': search_rows,
        'remote_media_map': {k: list(v) for k, v in sorted(shared_remote_media.items())},
        'report': report,
    }
