"""Offline build pipeline for the our5etools collection (SQLite + Stella reader + audit metadata)."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
from typing import Any

from anna.paths import OUR5ETOOLS, OUR5ETOOLS_DATA, PROJECT
from anna.our5etools import books, inventory, render, semantics, types


MANIFEST_SCHEMA = 1
READER_SCHEMA = 6
COLLECTION_KEY = 'our5etools'
PAGE_BASE = '/our5etools-book/'

_CR_FRACTIONS: dict[str, float] = {
    '1/8': 0.125,
    '1/4': 0.25,
    '1/2': 0.5,
}

_RULESET_PRIORITY: dict[str, int] = {
    '5r': 0,
    '5e': 1,
    '待核': 2,
}

_OUR5ETOOLS_BOOK_CSS = """\
/* Our5etools Stella Reader stylesheet (light & dark themes). */
:root {
  --o5e-bg: #fbfaf7;
  --o5e-surface: #ffffff;
  --o5e-surface-alt: #f4f1ea;
  --o5e-border: #ddd6c7;
  --o5e-text: #1f2328;
  --o5e-muted: #5f6670;
  --o5e-accent: #8c2d19;
  --o5e-link: #1f5ea8;
  --o5e-inset-bg: #f6f1e4;
  --o5e-readaloud-bg: #eef4f8;
  --o5e-readaloud-border: #4a789c;
  --o5e-warn-bg: #fff5e6;
  --o5e-warn-border: #c97a16;
  --o5e-code-bg: #f0ede6;
}

:root[data-webhelp-theme="dark"] {
  --o5e-bg: #17191d;
  --o5e-surface: #1f2228;
  --o5e-surface-alt: #262a31;
  --o5e-border: #363c46;
  --o5e-text: #e6e8ec;
  --o5e-muted: #9aa3b2;
  --o5e-accent: #e07a5f;
  --o5e-link: #7ab3f0;
  --o5e-inset-bg: #25221d;
  --o5e-readaloud-bg: #1c252e;
  --o5e-readaloud-border: #5c8db5;
  --o5e-warn-bg: #2d2417;
  --o5e-warn-border: #d48c2f;
  --o5e-code-bg: #2b3038;
}

html, body {
  margin: 0;
  padding: 0;
  background: var(--o5e-bg);
  color: var(--o5e-text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  line-height: 1.72;
  font-size: 15px;
}

.o5e-chapter {
  max-width: 920px;
  margin: 0 auto;
  padding: 20px 24px 64px;
}

.o5e-chapter-header {
  border-bottom: 2px solid var(--o5e-accent);
  padding-bottom: 12px;
  margin-bottom: 20px;
}

.o5e-book-kicker {
  font-size: 12px;
  color: var(--o5e-muted);
  margin-bottom: 4px;
}

.o5e-chapter-title {
  margin: 0 0 6px;
  font-size: 1.65em;
  line-height: 1.3;
  color: var(--o5e-accent);
}

.o5e-chapter-subtitle {
  margin: 0 0 8px;
  font-size: 0.95em;
  color: var(--o5e-muted);
}

.o5e-chapter-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.o5e-pill {
  display: inline-block;
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--o5e-surface-alt);
  border: 1px solid var(--o5e-border);
  color: var(--o5e-muted);
}

.o5e-heading {
  color: var(--o5e-accent);
  margin: 1.4em 0 0.5em;
  line-height: 1.35;
}

.o5e-chapter-body p {
  margin: 0.55em 0;
}

.o5e-list {
  margin: 0.5em 0 0.8em;
  padding-left: 1.5em;
}

.o5e-item-name {
  font-weight: 600;
}

.o5e-inset, .o5e-quote, .o5e-statblock {
  margin: 1em 0;
  padding: 12px 16px;
  border-radius: 6px;
  background: var(--o5e-inset-bg);
  border: 1px solid var(--o5e-border);
  border-left: 4px solid var(--o5e-accent);
}

.o5e-inset-readaloud {
  background: var(--o5e-readaloud-bg);
  border-left-color: var(--o5e-readaloud-border);
}

.o5e-inset-title, .o5e-statblock-title {
  font-weight: 700;
  margin-bottom: 6px;
  color: var(--o5e-accent);
}

.o5e-table-wrap {
  overflow-x: auto;
  margin: 1em 0;
}

.o5e-chapter table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92em;
  background: var(--o5e-surface);
}

.o5e-chapter th, .o5e-chapter td {
  padding: 6px 10px;
  border: 1px solid var(--o5e-border);
  text-align: left;
  vertical-align: top;
}

.o5e-chapter th {
  background: var(--o5e-surface-alt);
  font-weight: 600;
}

.o5e-chapter tbody tr:nth-child(even) {
  background: var(--o5e-surface-alt);
}

.o5e-figure {
  margin: 1em 0;
  padding: 10px 12px;
  border: 1px solid var(--o5e-border);
  border-radius: 6px;
  background: var(--o5e-surface);
  text-align: center;
}

.o5e-figure img {
  max-width: 100%;
  height: auto;
  border-radius: 4px;
}

.o5e-figure figcaption {
  font-size: 0.88em;
  color: var(--o5e-muted);
  margin-top: 6px;
}

.o5e-media-remote {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  margin: 0.45em 0;
  background: transparent;
  border: 1px dashed var(--o5e-border);
  border-radius: 5px;
  text-align: left;
  color: var(--o5e-muted);
  font-size: 12px;
}

.o5e-media-badge {
  display: inline-block;
  font-size: 12px;
  font-weight: 500;
  color: var(--o5e-muted);
}

.o5e-figure.o5e-img-failed img {
  display: none;
}

.o5e-media-fallback-note[hidden] {
  display: none;
}

.o5e-figure.o5e-img-failed .o5e-media-fallback-note {
  display: inline-block;
  font-size: 12px;
  color: var(--o5e-muted);
}

.o5e-maps-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
  margin-top: 12px;
}

button.ref {
  appearance: none;
  background: transparent;
  border: 0;
  padding: 0;
  font: inherit;
  color: var(--o5e-link);
  text-decoration: underline;
  text-decoration-style: dotted;
  cursor: pointer;
}

button.ref:hover {
  text-decoration-style: solid;
}

a.ref-book, a.ref-filter, a.o5e-ext-link, a.o5e-media-link {
  color: var(--o5e-link);
}

.ref-unresolved {
  border-bottom: 1px dashed var(--o5e-muted);
}

code {
  background: var(--o5e-code-bg);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 0.9em;
}

hr.o5e-hr {
  border: 0;
  border-top: 1px solid var(--o5e-border);
  margin: 1.4em 0;
}
"""


def _parse_cr_num(fields: dict[str, Any], raw_data: dict[str, Any]) -> float | None:
    """Parse numeric Challenge Rating from extracted fields or raw monster data."""
    cr = fields.get('cr')
    if cr is None:
        raw_cr = raw_data.get('cr')
        if isinstance(raw_cr, dict):
            cr = raw_cr.get('cr')
        else:
            cr = raw_cr
    if cr is None:
        return None
    s = str(cr).strip()
    if not s or s.lower() in {'unknown', 'none', '—', '-', 'varies'}:
        return None
    if s in _CR_FRACTIONS:
        return _CR_FRACTIONS[s]
    try:
        return float(s)
    except ValueError:
        m = re.match(r'^(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)$', s)
        if m:
            den = float(m.group(2))
            return float(m.group(1)) / den if den else None
        return None


def _compute_legacy(entry: dict[str, Any], source: dict[str, Any]) -> str:
    """Map source/entry legacy markers into Anna's legacy vocabulary."""
    if source.get('is_legacy'):
        return 'source_archived'
    raw = entry.get('data') or {}
    if raw.get('isLegacy') or raw.get('_isLegacy') or raw.get('reprintedAs'):
        return 'entry_marked'
    return 'unmarked'


def _build_rating_index(ratings: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Index subclass ratings from __ours/ratings.json by (class_cf, subclass_name_cf)."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    ours = ratings.get('ours_ratings')
    if not isinstance(ours, dict):
        api_r = ratings.get('api_ratings')
        ours = api_r.get('base') if isinstance(api_r, dict) else None
    if not isinstance(ours, dict):
        return index
    for sl in ours.get('subclassLists') or []:
        if not isinstance(sl, dict):
            continue
        list_id = sl.get('id')
        list_title = sl.get('title')
        cls_zh = str(sl.get('class') or '').strip().casefold()
        cls_en = str(sl.get('classEn') or '').strip().casefold()
        for item in sl.get('entries') or []:
            if not isinstance(item, dict):
                continue
            payload = {
                'list_id': list_id,
                'list_title': list_title,
                'tier': item.get('tier'),
                'score': item.get('score'),
                'base': item.get('base'),
                'bonus': item.get('bonus'),
                'rank': item.get('rank'),
                'evidence': item.get('evidence'),
                'from': item.get('from'),
                'combo': item.get('combo'),
                'ref': item.get('ref'),
            }
            zh = str(item.get('zh') or '').strip().casefold()
            en = str(item.get('en') or '').strip().casefold()
            for cls_key in (cls_zh, cls_en, ''):
                if zh:
                    index.setdefault((cls_key, zh), payload)
                if en:
                    index.setdefault((cls_key, en), payload)
    return index


def _lookup_entry_rating(
    entry: dict[str, Any],
    rating_index: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    if not rating_index or entry.get('type') != 'subclass':
        return None
    owner_cls = str(entry.get('owner_class') or '').strip().casefold()
    raw = entry.get('data') or {}
    short_name = str(raw.get('shortName') or '').strip().casefold()
    candidates = [
        str(entry.get('name_zh') or '').strip().casefold(),
        str(entry.get('name') or '').strip().casefold(),
        short_name,
        str(entry.get('name_en') or '').strip().casefold(),
    ]
    for nm in candidates:
        if not nm:
            continue
        if owner_cls and (owner_cls, nm) in rating_index:
            return rating_index[(owner_cls, nm)]
    for nm in candidates:
        if nm and ('', nm) in rating_index:
            return rating_index[('', nm)]
    return None


def _pick_group_primary(
    member_ids: list[str],
    entry_index: dict[str, dict[str, Any]],
    legacy_map: dict[str, str],
) -> str:
    """Choose the primary entry_id for an identity_group (5r > 5e > 待核, non-legacy first)."""
    def _rank(eid: str) -> tuple[int, int, int]:
        e = entry_index.get(eid) or {}
        rs = _RULESET_PRIORITY.get(str(e.get('ruleset') or '待核'), 9)
        leg = 0 if legacy_map.get(eid) == 'unmarked' else 1
        idx = member_ids.index(eid)
        return (rs, leg, idx)

    return min(member_ids, key=_rank)


def _compute_delta(
    prev_summary: dict[str, Any] | None,
    new_counts: dict[str, Any],
    new_by_type: dict[str, int],
) -> dict[str, Any]:
    """Compute count deltas against a previous summary.json, if one existed."""
    if not isinstance(prev_summary, dict):
        return {'previous_snapshot': None, 'is_initial': True, 'counts': {}, 'by_type': {}}
    prev_counts = prev_summary.get('counts') if isinstance(prev_summary.get('counts'), dict) else {}
    prev_by_type = prev_summary.get('by_type') if isinstance(prev_summary.get('by_type'), dict) else {}

    count_diffs: dict[str, dict[str, int]] = {}
    for k in sorted(set(prev_counts) | set(new_counts)):
        old_v = prev_counts.get(k)
        new_v = new_counts.get(k)
        if isinstance(old_v, int) and isinstance(new_v, int) and old_v != new_v:
            count_diffs[k] = {'previous': old_v, 'current': new_v, 'delta': new_v - old_v}

    type_diffs: dict[str, dict[str, int]] = {}
    for k in sorted(set(prev_by_type) | set(new_by_type)):
        old_v = int(prev_by_type.get(k, 0))
        new_v = int(new_by_type.get(k, 0))
        if old_v != new_v:
            type_diffs[k] = {'previous': old_v, 'current': new_v, 'delta': new_v - old_v}

    return {
        'previous_snapshot': prev_summary.get('snapshot'),
        'is_initial': False,
        'counts': count_diffs,
        'by_type': type_diffs,
    }


def _build_entry_index_by_book_chapter(
    book_index: books.BookIndex,
    corpus: semantics.ResolvedCorpus,
) -> dict[Any, list[dict[str, Any]]]:
    """Group corpus entries per (book_key, chapter_idx) strictly scoped by package_id + source_code."""
    by_pkg_src: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in corpus.entries:
        pkg = str(entry.get('package_id') or '')
        src_cf = str(entry.get('source_code') or '').strip().casefold()
        if pkg and src_cf:
            by_pkg_src[(pkg, src_cf)].append(entry)

    chapter_map: dict[Any, list[dict[str, Any]]] = {}
    for book in book_index.books:
        pkg = book.package_id
        src_cf = (book.source_code or '').strip().casefold()
        bid_cf = (book.book_id or '').strip().casefold()
        entries_for_book: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for code_cf in (src_cf, bid_cf):
            if not code_cf:
                continue
            for ent in by_pkg_src.get((pkg, code_cf), ()):
                eid = ent['id']
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    entries_for_book.append(ent)
        if not entries_for_book:
            continue
        for ch_idx in range(len(book.data_chapters)):
            chapter_map[(book.book_key, ch_idx)] = entries_for_book
    return chapter_map


def _validate_toc_order(nodes: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for node in nodes:
        nid = node.get('id')
        pid = node.get('parent_id') or ''
        if not nid or nid in seen:
            raise ValueError(f'Duplicate or empty TOC node id: {nid!r}')
        if pid and pid not in seen:
            raise ValueError(f'TOC node {nid!r} appears before parent {pid!r}')
        seen.add(nid)


def _validate_staged_output(stage: Path, expected_entries: int, *, expected_collection: str = COLLECTION_KEY) -> None:
    """Validate SQLite integrity, reader JSON & files, and manifest.json before atomic swap."""
    db_path = stage / 'catalog.sqlite'
    if not db_path.is_file():
        raise ValueError(f'Missing staged database: {db_path}')
    with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise ValueError(f'SQLite integrity_check failed: {integrity}')
        actual_entries = db.execute('SELECT count(*) FROM entries').fetchone()[0]
        if actual_entries != expected_entries or actual_entries <= 0:
            raise ValueError(f'Entry count mismatch in catalog.sqlite: {actual_entries} != {expected_entries}')
        if db.execute('SELECT count(*) FROM sources').fetchone()[0] <= 0:
            raise ValueError('sources table is empty')
        if db.execute('SELECT count(*) FROM packages').fetchone()[0] <= 0:
            raise ValueError('packages table is empty')
        if db.execute('SELECT count(*) FROM entry_facets').fetchone()[0] <= 0:
            raise ValueError('entry_facets table is empty')
        col_row = db.execute("SELECT value FROM metadata WHERE key = 'collection'").fetchone()
        if not col_row or col_row[0] != expected_collection:
            raise ValueError(f'Unexpected SQLite collection metadata: {col_row[0] if col_row else None!r} != {expected_collection!r}')

    reader_dir = stage / 'reader'
    reader_json_path = reader_dir / 'anna-reader.json'
    if not reader_json_path.is_file():
        raise ValueError(f'Missing staged reader metadata: {reader_json_path}')
    reader_data = json.loads(reader_json_path.read_bytes())
    if reader_data.get('schema') != READER_SCHEMA:
        raise ValueError(f'Unexpected reader schema: {reader_data.get("schema")}')
    if reader_data.get('collection') != expected_collection:
        raise ValueError(f'Unexpected reader collection: {reader_data.get("collection")!r} != {expected_collection!r}')
    _validate_toc_order(reader_data.get('nodes') or [])
    pages = reader_data.get('pages') or {}
    for page_path in pages:
        if not (reader_dir / page_path).is_file():
            raise ValueError(f'Reader page missing on disk: {page_path}')

    manifest_path = stage / 'manifest.json'
    if not manifest_path.is_file():
        raise ValueError(f'Missing staged manifest: {manifest_path}')
    manifest = json.loads(manifest_path.read_bytes())
    for required in ('schema', 'snapshot', 'collection', 'source_root', 'counts', 'reader', 'checks'):
        if required not in manifest:
            raise ValueError(f'manifest.json missing required key: {required}')
    if manifest.get('collection') != expected_collection:
        raise ValueError(f'Unexpected manifest collection: {manifest.get("collection")!r} != {expected_collection!r}')
    if not (stage / 'media').is_dir():
        raise ValueError(f'Missing staged media directory: {stage / "media"}')
    remotes_path = stage / 'media-remotes.json'
    if not remotes_path.is_file():
        raise ValueError(f'Missing staged media remotes registry: {remotes_path}')


def _copy_media_file(src: Path, dst: Path, *, allow_hardlink: bool = False) -> None:
    if src.is_symlink() or not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    if allow_hardlink:
        try:
            os.link(src, dst)
            return
        except OSError:
            pass
    shutil.copy2(src, dst)


def _stage_media_bundle(
    *,
    stage_media: Path,
    inv: inventory.Inventory,
    prev_output: Path,
    img_dir: Path | None,
) -> tuple[set[str], int, int]:
    """Bundle local media + preserved lazy cache + optional img_dir into ``stage/media``."""
    stage_media.mkdir(parents=True, exist_ok=True)
    staged_relpaths: set[str] = set()
    preserved_cached = 0
    bundled_img_dir = 0

    # 1. Preserve any lazy-cached remote assets (`img/`, `ext/`) from the existing built snapshot.
    prev_media = prev_output / 'media'
    if prev_media.is_dir():
        for subroot in ('img', 'ext'):
            src_sub = prev_media / subroot
            if not src_sub.is_dir() or src_sub.is_symlink():
                continue
            for src_file in src_sub.rglob('*'):
                if src_file.is_symlink() or not src_file.is_file():
                    continue
                if src_file.name.startswith('.'):
                    continue
                rel = src_file.relative_to(prev_media).as_posix()
                _copy_media_file(src_file, stage_media / rel, allow_hardlink=True)
                staged_relpaths.add(rel)
                preserved_cached += 1

    # 2. Bundle optional external 5etools-img directory (override/seed before repo files).
    if img_dir is not None:
        img_root = Path(img_dir).expanduser().resolve()
        if img_root.is_dir():
            for src_file in img_root.rglob('*'):
                if src_file.is_symlink() or not src_file.is_file():
                    continue
                parts = src_file.relative_to(img_root).parts
                if not parts or any(p.startswith('.') for p in parts):
                    continue
                rel = '/'.join(parts) if parts[0] == 'img' else 'img/' + '/'.join(parts)
                san = render.sanitize_media_path(rel)
                if not san:
                    continue
                _copy_media_file(src_file, stage_media / san, allow_hardlink=False)
                staged_relpaths.add(san)
                bundled_img_dir += 1

    # 3. Bundle all local media files from the source repository (authoritative for bundled assets).
    for rel in sorted(inv.media_files):
        san = render.sanitize_media_path(rel)
        if not san:
            continue
        src_file = (inv.source_root / san).resolve()
        if not src_file.is_relative_to(inv.source_root) or not src_file.is_file() or src_file.is_symlink():
            raise ValueError(f'Source local media file missing or unsafe: {san}')
        _copy_media_file(src_file, stage_media / san, allow_hardlink=False)
        staged_relpaths.add(san)

    return staged_relpaths, preserved_cached, bundled_img_dir


def _write_json(path: Path, payload: Any, *, compact: bool = False) -> str:
    if compact:
        raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    else:
        raw = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _create_sqlite_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE entries (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            raw_kind TEXT NOT NULL,
            name TEXT NOT NULL,
            name_zh TEXT NOT NULL,
            name_en TEXT NOT NULL,
            sort_name TEXT NOT NULL,
            ruleset TEXT NOT NULL,
            legacy TEXT NOT NULL,
            package_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_code TEXT NOT NULL,
            owner_class TEXT NOT NULL,
            owner_subclass TEXT NOT NULL,
            owner_level INTEGER,
            copy_status TEXT NOT NULL,
            cr_num REAL,
            fields_json TEXT NOT NULL,
            quickref_json TEXT NOT NULL,
            conflicts_json TEXT NOT NULL,
            provenance_json TEXT NOT NULL,
            layout_html TEXT NOT NULL,
            body_text TEXT NOT NULL,
            search_name TEXT NOT NULL,
            search_body TEXT NOT NULL
        );

        CREATE TABLE entry_sources (
            entry_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            PRIMARY KEY (entry_id, source_id)
        );

        CREATE TABLE entry_facets (
            entry_id TEXT NOT NULL,
            facet_key TEXT NOT NULL,
            facet_value TEXT NOT NULL,
            PRIMARY KEY (entry_id, facet_key, facet_value)
        );

        CREATE TABLE sources (
            id TEXT PRIMARY KEY,
            package_id TEXT NOT NULL,
            origin TEXT NOT NULL,
            ruleset TEXT NOT NULL,
            short_name TEXT NOT NULL,
            name TEXT NOT NULL,
            data_json TEXT NOT NULL
        );

        CREATE TABLE packages (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            origin TEXT NOT NULL,
            ours INTEGER NOT NULL,
            entry_count INTEGER NOT NULL,
            book_count INTEGER NOT NULL,
            data_json TEXT NOT NULL
        );

        CREATE TABLE identity_groups (
            group_id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            package_id TEXT NOT NULL,
            name TEXT NOT NULL,
            name_en TEXT NOT NULL,
            primary_id TEXT NOT NULL,
            member_ids_json TEXT NOT NULL
        );

        CREATE TABLE identity_members (
            entry_id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            is_primary INTEGER NOT NULL
        );

        CREATE TABLE relations (
            source_id TEXT NOT NULL,
            type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            meta_json TEXT NOT NULL
        );

        CREATE TABLE book_pages (
            page_path TEXT PRIMARY KEY,
            root_id TEXT NOT NULL,
            book_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            package_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            title TEXT NOT NULL,
            node_id TEXT NOT NULL
        );

        CREATE TABLE book_search (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            root_id TEXT NOT NULL,
            page_path TEXT NOT NULL,
            node_id TEXT NOT NULL,
            title TEXT NOT NULL,
            scope_label TEXT NOT NULL,
            snippet_text TEXT NOT NULL
        );
        """
    )


def _create_sqlite_indexes(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE INDEX idx_entries_type ON entries (type);
        CREATE INDEX idx_entries_package ON entries (package_id);
        CREATE INDEX idx_entries_source ON entries (source_id);
        CREATE INDEX idx_entries_ruleset ON entries (ruleset);
        CREATE INDEX idx_entries_cr_num ON entries (cr_num);
        CREATE INDEX idx_entries_sort_name ON entries (sort_name);
        CREATE INDEX idx_entry_sources_source ON entry_sources (source_id);
        CREATE INDEX idx_entry_facets_kv ON entry_facets (facet_key, facet_value);
        CREATE INDEX idx_relations_source ON relations (source_id);
        CREATE INDEX idx_relations_target ON relations (target_id);
        CREATE INDEX idx_book_pages_root ON book_pages (root_id);
        CREATE INDEX idx_book_pages_book ON book_pages (book_id);
        CREATE INDEX idx_book_pages_package ON book_pages (package_id);
        CREATE INDEX idx_book_search_root ON book_search (root_id);
        CREATE INDEX idx_book_search_page ON book_search (page_path);
        """
    )


def _compute_observed_api_stats(corpus: semantics.ResolvedCorpus) -> dict[str, Any]:
    """Summarize corpus entity counts using the same buckets as __api/stats.json for reconciliation."""
    tracked = ('race', 'subrace', 'class', 'subclass', 'spell', 'feat', 'background', 'item', 'monster', 'optionalfeature')
    official: Counter[str] = Counter()
    brew: Counter[str] = Counter()
    ours: Counter[str] = Counter()

    for e in corpus.entries:
        rk = e.get('raw_kind') or ''
        # Collapse baseitem / itemGroup / magicvariant into item only for raw comparison clarity if needed;
        # keep exact raw_kind and item_combined both visible in reconciliation.
        if rk not in tracked:
            continue
        pkg = e.get('package_id') or ''
        if pkg == 'official':
            official[rk] += 1
        elif e.get('ours'):
            ours[rk] += 1
        else:
            brew[rk] += 1

    return {
        'official': {k: official[k] for k in tracked},
        'brew': {k: brew[k] for k in tracked},
        'ours': {k: ours[k] for k in tracked},
        'entries_total': len(corpus.entries),
        'brew_packages': sum(1 for p in corpus.packages.values() if p.get('kind') == 'brew' and not p.get('ours')),
        'ours_packages': sum(1 for p in corpus.packages.values() if p.get('kind') == 'brew' and p.get('ours')),
        'site_packages': sum(1 for p in corpus.packages.values() if p.get('kind') == 'site'),
    }


def build(
    source: Path = OUR5ETOOLS,
    output: Path = OUR5ETOOLS_DATA,
    *,
    img_dir: Path | None = None,
    collection_key: str = COLLECTION_KEY,
    quiet: bool = False,
) -> dict[str, Any]:
    """Run the full offline our5etools build with atomic staging and rollback safety."""
    t_start = time.perf_counter()
    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    col_key = (collection_key or COLLECTION_KEY).strip()
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', col_key):
        raise ValueError(f'Invalid collection key: {col_key!r}')
    page_base = f'/{col_key}-book/'
    media_base = f'/{col_key}-media/'
    if output == source or source in output.parents or output in source.parents:
        raise ValueError(f'Output directory must be separate from source directory: {output} vs {source}')

    def _log(msg: str) -> None:
        if not quiet:
            print(msg, file=sys.stderr, flush=True)

    # 1. Validate source via load_inventory BEFORE touching output or creating staging dir.
    _log(f'[our5etools.build] Scanning source inventory: {source}')
    inv = inventory.load_inventory(source)
    _log(
        f'[our5etools.build] Inventory ready: {len(inv.file_records)} files, '
        f'{len(inv.official_sources)} official sources, {len(inv.packages)} packages '
        f'({inv.checks["brew_package_count"]} brew + {inv.checks["site_package_count"]} site)'
    )

    prev_summary: dict[str, Any] | None = None
    prev_summary_path = output / 'summary.json'
    if prev_summary_path.is_file():
        try:
            prev_summary = json.loads(prev_summary_path.read_bytes())
        except (OSError, json.JSONDecodeError):
            prev_summary = None

    # 2. Resolve corpus & collect book index
    _log('[our5etools.build] Resolving corpus semantics...')
    corpus = semantics.resolve_corpus(inv, collection=col_key)
    book_index = books.collect_book_index(inv)
    _log(
        f'[our5etools.build] Resolved {len(corpus.entries)} entries, '
        f'{len(corpus.sources)} sources, {len(corpus.packages)} packages, '
        f'{len(book_index.books)} books/adventures'
    )

    built_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    snapshot = hashlib.sha256(
        json.dumps(
            {
                'collection': col_key,
                'input_sha256': inv.snapshot_info['input_sha256'],
                'git_commit': inv.snapshot_info['git_commit'],
                'manifest_schema': MANIFEST_SCHEMA,
                'reader_schema': READER_SCHEMA,
            },
            sort_keys=True,
        ).encode('utf-8')
    ).hexdigest()

    entry_index: dict[str, dict[str, Any]] = {e['id']: e for e in corpus.entries}

    relations_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    copied_from_map: dict[str, dict[str, str]] = {}
    for rel in corpus.relations:
        src_id = rel.get('source_id')
        tgt_id = rel.get('target_id')
        rtype = rel.get('type')
        if src_id:
            relations_by_entry[src_id].append(rel)
        if tgt_id and tgt_id != src_id:
            relations_by_entry[tgt_id].append(rel)
        if rtype == 'copied_from' and src_id and tgt_id and src_id not in copied_from_map:
            parent_entry = entry_index.get(tgt_id)
            copied_from_map[src_id] = {
                'id': tgt_id,
                'name': str(parent_entry.get('name') or '') if parent_entry else '',
            }

    rating_index = _build_rating_index(inv.ratings)
    resolver_source_ids: dict[str, str] = {}
    for sid, sdict in corpus.sources.items():
        code_l = str(sdict.get('code') or '').strip().lower()
        if not code_l:
            continue
        if code_l not in resolver_source_ids or sdict.get('package_id') == 'official':
            resolver_source_ids[code_l] = sid

    resolver = render.LinkResolver(
        corpus.lookup_index,
        book_index.anchor_lookup,
        collection=col_key,
        tag_redirects=inv.generated_lookups.get('data/generated/gendata-tag-redirects.json'),
        entry_index=entry_index,
        source_ids=resolver_source_ids,
    )
    entry_index_by_book_chapter = _build_entry_index_by_book_chapter(book_index, corpus)

    # 3. Create sibling staging directory and populate self-contained media bundle
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.with_name(f'{output.name}.tmp.{os.getpid()}')
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    try:
        _log('[our5etools.build] Bundling self-contained media archive...')
        stage_media = stage / 'media'
        staged_media_set, preserved_cached_media, bundled_img_dir_files = _stage_media_bundle(
            stage_media=stage_media,
            inv=inv,
            prev_output=output,
            img_dir=img_dir,
        )

        def media_checker(relpath: str) -> bool:
            cleaned = render.sanitize_media_path(relpath)
            if not cleaned:
                return False
            return cleaned in staged_media_set or f'audio/{cleaned}' in staged_media_set

        # 4. Build Stella reader HTML chapters & assets in stage/reader
        _log('[our5etools.build] Rendering Stella reader chapters...')
        reader_dir = stage / 'reader'
        books_result = books.build_reader_pages(
            book_index,
            reader_dir,
            resolver=resolver,
            media_checker=media_checker,
            entry_index_by_book_chapter=entry_index_by_book_chapter,
            snapshot=snapshot,
            collection=col_key,
            content_base=page_base,
            media_base=media_base,
        )
        remote_media_map: dict[str, list[str]] = {
            str(k): list(v)
            for k, v in (books_result.get('remote_media_map') or {}).items()
            if k and v
        }
        assets_dir = reader_dir / 'anna-assets'
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / 'our5etools-book.css').write_text(_OUR5ETOOLS_BOOK_CSS, encoding='utf-8')
        for src_rel, dest_name in (
            ('book/links.js', 'links.js'),
            ('book/topic.js', 'topic.js'),
            ('web/link-labels.js', 'link-labels.js'),
        ):
            src_asset = PROJECT / src_rel
            if src_asset.is_file():
                shutil.copyfile(src_asset, assets_dir / dest_name)

        nodes: list[dict[str, Any]] = books_result['nodes']
        pages_meta: dict[str, dict[str, Any]] = books_result['pages_meta']
        search_rows: list[tuple[str, str, str, str, str]] = books_result['search_rows']
        books_report: dict[str, Any] = books_result['report']

        # Map page_path -> (root_id, node_id) and build roots + entry_locations
        node_by_id = {n['id']: n for n in nodes}
        book_root_ids: set[str] = {f'n_{b.book_key}' for b in book_index.books}

        def _ancestor_root(nid: str) -> str:
            cur = nid
            while cur:
                if cur in book_root_ids:
                    return cur
                node = node_by_id.get(cur)
                if not node:
                    break
                cur = node.get('parent_id') or ''
            return ''

        page_to_node: dict[str, tuple[str, str]] = {}
        for n in nodes:
            p = n.get('path') or ''
            if p and not n.get('fragment') and p not in page_to_node:
                page_to_node[p] = (_ancestor_root(n['id']), n['id'])

        # Map (package_id, source_code_cf) -> source_id for book_pages
        src_by_pkg_code: dict[tuple[str, str], str] = {}
        src_by_code: dict[str, str] = {}
        for sid, sdict in corpus.sources.items():
            code_cf = str(sdict.get('code') or '').casefold()
            pkg = str(sdict.get('package_id') or '')
            if code_cf:
                src_by_pkg_code[(pkg, code_cf)] = sid
                src_by_code.setdefault(code_cf, sid)

        roots_payload: list[dict[str, Any]] = []
        book_first_location: dict[tuple[str, str, str], dict[str, Any]] = {}
        books_count_by_package: Counter[str] = Counter()

        for b in book_index.books:
            root_id = f'n_{b.book_key}'
            first_page = b.chapter_paths[0] if b.chapter_paths else ''
            first_node = page_to_node.get(first_page, (root_id, root_id))[1] if first_page else root_id
            sid = (
                src_by_pkg_code.get((b.package_id, (b.source_code or '').casefold()))
                or src_by_code.get((b.source_code or '').casefold())
                or f'src:{b.source_code}'
            )
            roots_payload.append({
                'id': root_id,
                'label': b.title,
                'title': b.title,
                'title_en': b.title_en,
                'book_id': b.book_id,
                'book_key': b.book_key,
                'kind': b.kind,
                'source_code': b.source_code,
                'source_id': sid,
                'package_id': b.package_id,
                'origin': b.origin,
                'edition': b.edition,
                'ours': b.ours,
                'first_page': first_page,
                'page_count': len(b.chapter_paths),
            })
            books_count_by_package[b.package_id] += 1
            if first_page:
                loc = {
                    'path': first_page,
                    'fragment': '',
                    'root': root_id,
                    'node_id': first_node,
                    'book_id': b.book_id,
                    'title': b.title,
                    'locatable': True,
                }
                book_first_location[(b.package_id, b.kind, (b.book_id or '').casefold())] = loc
                book_first_location.setdefault((b.package_id, b.kind, (b.source_code or '').casefold()), loc)

        entry_locations: dict[str, dict[str, Any]] = {}
        for page_path, pmeta in pages_meta.items():
            root_id, ch_node_id = page_to_node.get(page_path, ('', ''))
            for drec in pmeta.get('definitions') or []:
                eid = drec.get('id')
                if not eid or eid in entry_locations:
                    continue
                entry_locations[eid] = {
                    'path': page_path,
                    'fragment': drec.get('fragment') or f'anna-{eid}',
                    'root': root_id,
                    'node_id': ch_node_id,
                    'book_id': pmeta.get('book_id') or '',
                    'title': pmeta.get('title') or '',
                    'locatable': bool(drec.get('locatable', True)),
                }

        # Also wire book/adventure catalog entries directly to their book's first chapter.
        for e in corpus.entries:
            if e['id'] in entry_locations or e.get('type') != 'book':
                continue
            raw = e.get('data') or {}
            bkind = 'adventure' if e.get('raw_kind') == 'adventure' else 'book'
            bid_cf = str(raw.get('id') or e.get('source_code') or '').casefold()
            loc = (
                book_first_location.get((e['package_id'], bkind, bid_cf))
                or book_first_location.get((e['package_id'], bkind, str(e.get('source_code') or '').casefold()))
            )
            if loc:
                entry_locations[e['id']] = dict(loc)

        default_page = next((n['path'] for n in nodes if n.get('status') == 'present' and n.get('path')), '')
        reader_json = {
            'schema': READER_SCHEMA,
            'snapshot': snapshot,
            'collection': col_key,
            'page_base': page_base,
            'media_base': media_base,
            'default_page': default_page,
            'presentation_resources': [dict(res) for res in books.PRESENTATION_RESOURCES],
            'roots': roots_payload,
            'nodes': nodes,
            'pages': pages_meta,
            'entry_locations': entry_locations,
        }
        _write_json(reader_dir / 'anna-reader.json', reader_json, compact=True)

        # 5. Render all entries & populate catalog.sqlite
        _log(f'[our5etools.build] Rendering {len(corpus.entries)} entries and writing catalog.sqlite...')
        db_path = stage / 'catalog.sqlite'
        db = sqlite3.connect(db_path)
        db.execute('PRAGMA synchronous = OFF')
        db.execute('PRAGMA journal_mode = MEMORY')
        db.execute('PRAGMA temp_store = MEMORY')
        _create_sqlite_schema(db)

        by_type: Counter[str] = Counter()
        by_package: Counter[str] = Counter()
        by_source: Counter[str] = Counter()
        by_ruleset: Counter[str] = Counter()
        by_origin: Counter[str] = Counter()
        by_raw_kind: Counter[str] = Counter()
        copy_status_counts: Counter[str] = Counter()

        resolved_entity_links = 0
        resolved_book_links = 0
        resolved_filter_links = 0
        ambiguous_links = 0
        unresolved_links = 0
        unresolved_counter: Counter[tuple[str, str, str]] = Counter()
        ambiguous_counter: Counter[tuple[str, str, str]] = Counter()
        unknown_tags_counter: Counter[str] = Counter()
        unsupported_blocks_counter: Counter[str] = Counter()

        referenced_local_media: set[str] = set()
        referenced_remote_media: set[str] = set()
        referenced_missing_local_media: set[str] = set()

        entry_rows: list[tuple[Any, ...]] = []
        entry_source_rows: list[tuple[str, str]] = []
        entry_facet_rows: list[tuple[str, str, str]] = []
        legacy_map: dict[str, str] = {}

        for entry in corpus.entries:
            eid = entry['id']
            etype = entry['type']
            raw_kind = entry['raw_kind']
            pkg_id = entry['package_id']
            src_id = entry['source_id']
            src_code = entry['source_code']
            pkg_meta = corpus.packages.get(pkg_id) or {}
            src_meta = corpus.sources.get(src_id) or {}
            raw_data = entry.get('data') or {}

            rendered = render.render_entry_detail(
                entry,
                resolver,
                media_checker,
                relations_by_entry.get(eid, ()),
                media_prefix=media_base,
            )
            layout_html: str = rendered.get('html') or ''
            body_text: str = rendered.get('body_text') or ''
            media_summary: dict[str, list[str]] = rendered.get('media_summary') or {
                'local_media': [],
                'remote_media': [],
                'missing_local_media': [],
            }

            resolved_entity_links += layout_html.count('class="ref" data-entry="o5e_')
            resolved_book_links += layout_html.count('class="ref-book"')
            resolved_filter_links += layout_html.count('class="ref-filter"')

            for ev in rendered.get('link_events') or ():
                st = ev.get('status')
                key = (str(ev.get('tag') or ''), str(ev.get('name') or ''), str(ev.get('source') or ''))
                if st == 'ambiguous':
                    ambiguous_links += 1
                    ambiguous_counter[key] += 1
                else:
                    unresolved_links += 1
                    unresolved_counter[key] += 1

            for ut in rendered.get('unknown_tags') or ():
                unknown_tags_counter[str(ut)] += 1
            for us in rendered.get('unsupported_structures') or ():
                unsupported_blocks_counter[str(us)] += 1

            referenced_local_media.update(media_summary.get('local_media') or ())
            referenced_remote_media.update(media_summary.get('remote_media') or ())
            referenced_missing_local_media.update(media_summary.get('missing_local_media') or ())
            for r_rel, r_urls in (rendered.get('remote_media_map') or {}).items():
                bucket = remote_media_map.setdefault(r_rel, [])
                for u in r_urls:
                    if u and u not in bucket:
                        bucket.append(u)

            fields = entry.get('fields') or {}
            quickref = entry.get('quickref') or {}
            conflicts = entry.get('field_conflicts') or []
            legacy = _compute_legacy(entry, src_meta)
            legacy_map[eid] = legacy
            cr_num = _parse_cr_num(fields, raw_data) if etype == 'creature' else None

            name = str(entry.get('name') or '')
            name_zh = str(entry.get('name_zh') or name)
            name_en = str(entry.get('name_en') or '')
            sort_name = types.name_key(name or name_en)
            name_parts = list(dict.fromkeys(p for p in (name, name_zh, name_en) if p))
            search_name = ' '.join(name_parts).lower()
            search_body = body_text.lower()

            rating = _lookup_entry_rating(entry, rating_index)
            provenance = {
                'collection': col_key,
                'package_id': pkg_id,
                'package_name': pkg_meta.get('name') or pkg_id,
                'origin': entry.get('origin') or src_meta.get('origin') or pkg_meta.get('origin') or '',
                'package_kind': entry.get('package_kind') or pkg_meta.get('package_kind') or '',
                'ours': bool(entry.get('ours') or pkg_meta.get('ours')),
                'authors': list(src_meta.get('authors') or pkg_meta.get('authors') or []),
                'translators': list(src_meta.get('translators') or pkg_meta.get('translators') or []),
                'tier': entry.get('tier') or src_meta.get('tier'),
                'rating': rating,
                'source_id': src_id,
                'source_code': src_code,
                'source_name': entry.get('source_name') or src_meta.get('name') or src_code,
                'source_short': entry.get('source_short') or src_meta.get('short_label') or src_code,
                'edition': src_meta.get('edition') or pkg_meta.get('edition'),
                'ruleset': entry.get('ruleset') or '待核',
                'copy_status': entry.get('copy_status') or 'ok',
                'copy_warnings': list(entry.get('copy_warnings') or []),
                'copied_from': copied_from_map.get(eid),
                'is_version': bool(entry.get('is_version')),
                'version_base_id': entry.get('version_base_id'),
                'license_note': src_meta.get('license_note'),
                'media_summary': media_summary,
                'reader_location': entry_locations.get(eid),
                'relpath': entry.get('relpath') or '',
            }

            entry_rows.append((
                eid,
                etype,
                raw_kind,
                name,
                name_zh,
                name_en,
                sort_name,
                entry.get('ruleset') or '待核',
                legacy,
                pkg_id,
                src_id,
                src_code,
                str(entry.get('owner_class') or ''),
                str(entry.get('owner_subclass') or ''),
                entry.get('owner_level'),
                entry.get('copy_status') or 'ok',
                cr_num,
                json.dumps(fields, ensure_ascii=False, separators=(',', ':')),
                json.dumps(quickref, ensure_ascii=False, separators=(',', ':')),
                json.dumps(conflicts, ensure_ascii=False, separators=(',', ':')),
                json.dumps(provenance, ensure_ascii=False, separators=(',', ':')),
                layout_html,
                body_text,
                search_name,
                search_body,
            ))

            entry_source_rows.append((eid, src_id))

            # Pre-expand exact quickref[k] tokens + common facets into entry_facets.
            seen_facets: set[tuple[str, str]] = set()
            for fkey, fvals in quickref.items():
                if not isinstance(fvals, (list, tuple)):
                    fvals = [fvals]
                for fval in fvals:
                    pair = (str(fkey), str(fval))
                    if pair not in seen_facets:
                        seen_facets.add(pair)
                        entry_facet_rows.append((eid, pair[0], pair[1]))
            for common_key, common_val in (
                ('ruleset', entry.get('ruleset') or '待核'),
                ('origin', provenance['origin']),
                ('source', src_id),
                ('package', pkg_id),
                ('legacy', legacy),
                ('copy_status', entry.get('copy_status') or 'ok'),
            ):
                if common_val:
                    pair = (common_key, str(common_val))
                    if pair not in seen_facets:
                        seen_facets.add(pair)
                        entry_facet_rows.append((eid, pair[0], pair[1]))

            by_type[etype] += 1
            by_package[pkg_id] += 1
            by_source[src_id] += 1
            by_ruleset[entry.get('ruleset') or '待核'] += 1
            by_origin[provenance['origin']] += 1
            by_raw_kind[raw_kind] += 1
            copy_status_counts[entry.get('copy_status') or 'ok'] += 1

        db.executemany(
            """
            INSERT INTO entries (
                id, type, raw_kind, name, name_zh, name_en, sort_name, ruleset, legacy,
                package_id, source_id, source_code, owner_class, owner_subclass, owner_level,
                copy_status, cr_num, fields_json, quickref_json, conflicts_json, provenance_json,
                layout_html, body_text, search_name, search_body
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            entry_rows,
        )
        db.executemany('INSERT OR IGNORE INTO entry_sources (entry_id, source_id) VALUES (?,?)', entry_source_rows)
        db.executemany(
            'INSERT OR IGNORE INTO entry_facets (entry_id, facet_key, facet_value) VALUES (?,?,?)',
            entry_facet_rows,
        )

        # Populate sources table
        source_rows = []
        for sid, sdict in sorted(corpus.sources.items()):
            s_payload = dict(sdict)
            s_payload['entry_count'] = by_source.get(sid, 0)
            source_rows.append((
                sid,
                str(sdict.get('package_id') or ''),
                str(sdict.get('origin') or ''),
                str(sdict.get('ruleset') or '待核'),
                str(sdict.get('short_label') or sdict.get('code') or sid),
                str(sdict.get('name') or sdict.get('code') or sid),
                json.dumps(s_payload, ensure_ascii=False, separators=(',', ':')),
            ))
        db.executemany(
            'INSERT INTO sources (id, package_id, origin, ruleset, short_name, name, data_json) VALUES (?,?,?,?,?,?,?)',
            source_rows,
        )

        # Populate packages table
        package_rows = []
        for pid, pdict in sorted(corpus.packages.items()):
            p_payload = dict(pdict)
            ecount = by_package.get(pid, 0)
            bcount = books_count_by_package.get(pid, 0)
            p_payload['entry_count'] = ecount
            p_payload['book_count'] = bcount
            package_rows.append((
                pid,
                str(pdict.get('name') or pid),
                str(pdict.get('origin') or ''),
                1 if pdict.get('ours') else 0,
                ecount,
                bcount,
                json.dumps(p_payload, ensure_ascii=False, separators=(',', ':')),
            ))
        db.executemany(
            'INSERT INTO packages (id, name, origin, ours, entry_count, book_count, data_json) VALUES (?,?,?,?,?,?,?)',
            package_rows,
        )

        # Populate identity_groups & identity_members
        group_rows = []
        member_rows = []
        seen_member_ids: set[str] = set()
        for grp in corpus.identity_groups:
            gid = grp['id']
            mids = [m for m in grp.get('member_ids') or [] if m in entry_index]
            if len(mids) < 2:
                continue
            primary_id = _pick_group_primary(mids, entry_index, legacy_map)
            group_rows.append((
                gid,
                str(grp.get('type') or ''),
                str(grp.get('package_id') or ''),
                str(grp.get('name') or ''),
                str(grp.get('name_en') or ''),
                primary_id,
                json.dumps(mids, ensure_ascii=False, separators=(',', ':')),
            ))
            for mid in mids:
                if mid not in seen_member_ids:
                    seen_member_ids.add(mid)
                    member_rows.append((mid, gid, 1 if mid == primary_id else 0))

        db.executemany(
            'INSERT INTO identity_groups (group_id, type, package_id, name, name_en, primary_id, member_ids_json) VALUES (?,?,?,?,?,?,?)',
            group_rows,
        )
        db.executemany(
            'INSERT OR IGNORE INTO identity_members (entry_id, group_id, is_primary) VALUES (?,?,?)',
            member_rows,
        )

        # Populate relations
        relation_rows = []
        for rel in corpus.relations:
            meta_extra = {k: v for k, v in rel.items() if k not in {'source_id', 'type', 'target_id'}}
            relation_rows.append((
                str(rel.get('source_id') or ''),
                str(rel.get('type') or ''),
                str(rel.get('target_id') or ''),
                json.dumps(meta_extra, ensure_ascii=False, separators=(',', ':')),
            ))
        db.executemany(
            'INSERT INTO relations (source_id, type, target_id, meta_json) VALUES (?,?,?,?)',
            relation_rows,
        )

        # Populate book_pages & book_search
        book_page_rows = []
        for page_path, pmeta in sorted(pages_meta.items()):
            root_id, node_id = page_to_node.get(page_path, ('', ''))
            pkg_id = str(pmeta.get('package_id') or '')
            src_code = str(pmeta.get('source_code') or '')
            sid = (
                src_by_pkg_code.get((pkg_id, src_code.casefold()))
                or src_by_code.get(src_code.casefold())
                or f'src:{src_code}'
            )
            book_page_rows.append((
                page_path,
                root_id,
                str(pmeta.get('book_id') or ''),
                str(pmeta.get('kind') or 'book'),
                pkg_id,
                sid,
                str(pmeta.get('title') or ''),
                node_id,
            ))
        db.executemany(
            'INSERT INTO book_pages (page_path, root_id, book_id, kind, package_id, source_id, title, node_id) VALUES (?,?,?,?,?,?,?,?)',
            book_page_rows,
        )

        book_search_rows = []
        for page_path, title, scope_label, plain_text, _sha in search_rows:
            root_id, node_id = page_to_node.get(page_path, ('', ''))
            book_search_rows.append((
                root_id,
                page_path,
                node_id,
                title,
                scope_label,
                plain_text,
            ))
        db.executemany(
            'INSERT INTO book_search (root_id, page_path, node_id, title, scope_label, snippet_text) VALUES (?,?,?,?,?,?)',
            book_search_rows,
        )

        # Metadata table
        db.executemany(
            'INSERT INTO metadata (key, value) VALUES (?,?)',
            [
                ('schema', str(MANIFEST_SCHEMA)),
                ('reader_schema', str(READER_SCHEMA)),
                ('snapshot', snapshot),
                ('built_at', built_at),
                ('source_root', str(source)),
                ('collection', col_key),
            ],
        )

        _create_sqlite_indexes(db)
        db.execute('ANALYZE')
        db.commit()
        db.execute('PRAGMA journal_mode = DELETE')
        db.close()

        # 6. Write inventory.json, reconciliation.json, attribution.json, summary.json, manifest.json
        _log('[our5etools.build] Writing report & manifest JSON artifacts...')
        counts_summary = {
            'entries': len(corpus.entries),
            'sources': len(corpus.sources),
            'packages': len(corpus.packages),
            'brew_packages': inv.checks['brew_package_count'],
            'site_packages': inv.checks['site_package_count'],
            'relations': len(corpus.relations),
            'identity_groups': len(group_rows),
            'entry_facets': len(entry_facet_rows),
            'books_total': len(book_index.books),
            'book_pages': len(pages_meta),
            'chapters_written': books_report['chapters_written'],
            'toc_nodes': books_report['toc_nodes'],
            'entry_locations': len(entry_locations),
            'files': len(inv.file_records),
            'local_media_files': len(inv.media_files),
            'bundled_media_files': len(staged_media_set),
            'preserved_cached_media': preserved_cached_media,
            'bundled_img_dir_files': bundled_img_dir_files,
            'remote_media_keys': len(remote_media_map),
        }

        delta = _compute_delta(prev_summary, counts_summary, dict(by_type))

        inventory_payload = {
            'collection': col_key,
            'snapshot_info': inv.snapshot_info,
            'checks': inv.checks,
            'total_files': len(inv.file_records),
            'by_disposition': dict(inv.checks['disposition_counts']),
            'files': inv.file_records,
        }
        inv_sha = _write_json(stage / 'inventory.json', inventory_payload)

        api_stats = inv.snapshot_info.get('stats_api') or {}
        observed_stats = _compute_observed_api_stats(corpus)
        stats_diff: dict[str, dict[str, dict[str, int]]] = {}
        for bucket in ('official', 'brew', 'ours'):
            api_b = api_stats.get(bucket) if isinstance(api_stats.get(bucket), dict) else {}
            obs_b = observed_stats.get(bucket) or {}
            bdiff = {}
            for k in sorted(set(api_b) | set(obs_b)):
                av = int(api_b.get(k, 0))
                ov = int(obs_b.get(k, 0))
                if av != ov:
                    bdiff[k] = {'api': av, 'observed': ov, 'delta': ov - av}
            stats_diff[bucket] = bdiff

        api_books_summary = {
            'total': len([p for p in inv.packages if p['kind'] == 'brew']),
            'ours': sum(1 for p in inv.packages if p['kind'] == 'brew' and p['ours']),
            'community': sum(1 for p in inv.packages if p['kind'] == 'brew' and not p['ours']),
            'site_to_import': inv.checks['site_package_count'],
            'books_recipe_keys_match': inv.checks['books_recipe_keys_match'],
            'count_mismatches': inv.checks['count_mismatches'],
        }

        total_resolved_links = resolved_entity_links + resolved_book_links
        reconciliation_payload = {
            'collection': col_key,
            'api_stats': api_stats,
            'api_books': api_books_summary,
            'observed': observed_stats,
            'stats_diff': stats_diff,
            'books_diff': {
                'count_mismatches': inv.checks['count_mismatches'],
                'reader_counts': books_report['counts'],
                'reader_warnings': books_report['warnings'],
                'reader_notes': books_report['notes'],
            },
            'copy_resolution': {
                'summary': corpus.diagnostics.get('copy_summary') or {},
                'by_status': dict(sorted(copy_status_counts.items())),
                'missing_base': corpus.diagnostics.get('copy_missing_base') or [],
                'ambiguous_base': corpus.diagnostics.get('copy_ambiguous_base') or [],
                'cycles': corpus.diagnostics.get('copy_cycles') or [],
                'mod_warnings': corpus.diagnostics.get('copy_mod_warnings') or [],
            },
            'variants': {
                'version_count': corpus.diagnostics.get('version_count', 0),
                'version_warnings': corpus.diagnostics.get('version_warnings') or [],
            },
            'subraces': {
                'orphan_subraces': corpus.diagnostics.get('orphan_subraces') or [],
            },
            'fluff': {
                'attached_count': sum(1 for e in corpus.entries if e.get('fluff')),
                'orphan_fluff_count': len(corpus.diagnostics.get('orphan_fluff') or []),
                'orphan_fluff_samples': (corpus.diagnostics.get('orphan_fluff') or [])[:50],
            },
            'links': {
                'resolved': total_resolved_links,
                'resolved_entity': resolved_entity_links,
                'resolved_book': resolved_book_links,
                'resolved_filter': resolved_filter_links,
                'reader_bound_references': books_report['bound_references'],
                'reader_bound_definitions': books_report['bound_definitions'],
                'reader_binding_mismatches': books_report['binding_mismatches'],
                'ambiguous': ambiguous_links,
                'unresolved': unresolved_links,
                'total': total_resolved_links + ambiguous_links + unresolved_links,
                'top_unresolved': [
                    {'tag': t, 'name': n, 'source': s, 'count': c}
                    for (t, n, s), c in unresolved_counter.most_common(50)
                ],
                'top_ambiguous': [
                    {'tag': t, 'name': n, 'source': s, 'count': c}
                    for (t, n, s), c in ambiguous_counter.most_common(50)
                ],
            },
            'unknown_tags': [
                {'tag': tag, 'count': cnt}
                for tag, cnt in unknown_tags_counter.most_common()
            ],
            'unsupported_blocks': [
                {'type': btype, 'count': cnt}
                for btype, cnt in unsupported_blocks_counter.most_common()
            ],
            'media': {
                'local': len(referenced_local_media),
                'remote': len(referenced_remote_media),
                'missing_local': len(referenced_missing_local_media),
                'missing_local_audio': len(corpus.diagnostics.get('missing_local_audio') or []),
                'indexed_media_counts': dict(inv.checks['media_counts']),
                'local_samples': sorted(referenced_local_media)[:30],
                'remote_samples': sorted(referenced_remote_media)[:30],
                'missing_local_samples': sorted(referenced_missing_local_media)[:30],
            },
            'reprints': {
                'unresolved_reprints_count': len(corpus.diagnostics.get('unresolved_reprints') or []),
                'unresolved_reprints_samples': (corpus.diagnostics.get('unresolved_reprints') or [])[:30],
            },
            'duplicates': {
                'inventory_natural_key_duplicates': len(inv.checks['duplicate_raw_records']),
                'inventory_exact_duplicates': len(inv.checks['exact_duplicate_raw_records']),
                'semantic_duplicate_identity_keys': len(corpus.diagnostics.get('duplicate_identity_keys') or []),
            },
        }
        recon_sha = _write_json(stage / 'reconciliation.json', reconciliation_payload)

        # Build attribution.json
        non_redistributable: list[dict[str, Any]] = [
            {
                'kind': 'remote_images_and_audio',
                'count': len(referenced_remote_media),
                'bundled': False,
                'reason': 'Referenced remote images/audio are not bundled offline in this repository and remain external links.',
                'samples': sorted(referenced_remote_media)[:20],
            },
            {
                'kind': 'local_fonts',
                'count': inv.checks['media_counts'].get('font', 0),
                'bundled': True,
                'license_file': '__ours/fonts/OFL.txt' if (source / '__ours/fonts/OFL.txt').is_file() else None,
                'reason': 'Local fonts remain in the source mirror and are served read-only; check OFL/font licenses before redistribution.',
                'paths': sorted(p for p, m in inv.media_files.items() if m['kind'] == 'font')[:25],
            },
            {
                'kind': 'official_and_partnered_rules',
                'count': by_origin.get('官方', 0) + by_origin.get('合作', 0),
                'bundled': True,
                'reason': 'Wizards of the Coast and partnered publications are copyrighted material parsed locally for personal reference only.',
            },
            {
                'kind': 'community_homebrew_packages',
                'count': len(inv.packages),
                'bundled': True,
                'reason': 'Community and site homebrew packages retain original authors, translators, URLs, and _meta license notes.',
            },
        ]

        attribution_packages = [
            {
                'id': pid,
                'name': p.get('name'),
                'kind': p.get('kind'),
                'origin': p.get('origin'),
                'ours': bool(p.get('ours')),
                'authors': p.get('authors') or [],
                'translators': p.get('translators') or [],
                'url': p.get('url'),
                'version': p.get('version'),
                'entry_count': by_package.get(pid, 0),
                'book_count': books_count_by_package.get(pid, 0),
            }
            for pid, p in sorted(corpus.packages.items())
        ]
        attribution_sources = [
            {
                'id': sid,
                'code': s.get('code'),
                'name': s.get('name'),
                'package_id': s.get('package_id'),
                'origin': s.get('origin'),
                'ours': bool(s.get('ours')),
                'authors': s.get('authors') or [],
                'translators': s.get('translators') or [],
                'tier': s.get('tier'),
                'edition': s.get('edition'),
                'license_note': s.get('license_note'),
                'entry_count': by_source.get(sid, 0),
            }
            for sid, s in sorted(corpus.sources.items())
        ]
        attribution_payload = {
            'collection': col_key,
            'snapshot': snapshot,
            'source_root': str(source),
            'packages': attribution_packages,
            'sources': attribution_sources,
            'non_redistributable': non_redistributable,
        }
        attr_sha = _write_json(stage / 'attribution.json', attribution_payload)

        summary_payload = {
            'schema': MANIFEST_SCHEMA,
            'collection': col_key,
            'snapshot': snapshot,
            'built_at': built_at,
            'counts': counts_summary,
            'by_type': dict(sorted(by_type.items())),
            'by_package': dict(sorted(by_package.items())),
            'by_source': dict(sorted(by_source.items())),
            'by_ruleset': dict(sorted(by_ruleset.items())),
            'by_origin': dict(sorted(by_origin.items())),
            'by_raw_kind': dict(sorted(by_raw_kind.items())),
            'copy_status': dict(sorted(copy_status_counts.items())),
            'delta': delta,
        }
        remotes_payload = {
            'schema': MANIFEST_SCHEMA,
            'snapshot': snapshot,
            'entries': {k: list(v) for k, v in sorted(remote_media_map.items())},
        }
        remotes_sha = _write_json(stage / 'media-remotes.json', remotes_payload, compact=True)

        summary_sha = _write_json(stage / 'summary.json', summary_payload)

        db_sha = hashlib.sha256(db_path.read_bytes()).hexdigest()
        reader_json_sha = hashlib.sha256((reader_dir / 'anna-reader.json').read_bytes()).hexdigest()

        manifest_payload = {
            'schema': MANIFEST_SCHEMA,
            'snapshot': snapshot,
            'collection': col_key,
            'source_root': str(source),
            'git_commit': inv.snapshot_info['git_commit'],
            'input_sha256': inv.snapshot_info['input_sha256'],
            'built_at': built_at,
            'elapsed_seconds': round(time.perf_counter() - t_start, 2),
            'counts': counts_summary,
            'delta': delta,
            'reader': {
                'schema': READER_SCHEMA,
                'snapshot': snapshot,
                'collection': col_key,
                'page_base': page_base,
                'media_base': media_base,
                'default_page': default_page,
                'books_total': len(book_index.books),
                'chapters_written': books_report['chapters_written'],
                'toc_nodes': books_report['toc_nodes'],
                'roots': len(roots_payload),
                'pages': len(pages_meta),
                'bound_definitions': books_report['bound_definitions'],
                'bound_references': books_report['bound_references'],
                'binding_mismatches': books_report['binding_mismatches'],
            },
            'checks': {
                'books_recipe_keys_match': inv.checks['books_recipe_keys_match'],
                'missing_shard_files': len(inv.checks['missing_shard_files']),
                'missing_shard_keys': len(inv.checks['missing_shard_keys']),
                'invalid_shard_paths': len(inv.checks['invalid_shard_paths']),
                'missing_site_packages': len(inv.checks['missing_site_packages']),
                'missing_generated_lookups': len(inv.checks['missing_generated_lookups']),
                'parser_warnings': len(inv.checks['parser_warnings']),
                'copy_missing_base': len(corpus.diagnostics.get('copy_missing_base') or []),
                'copy_ambiguous_base': len(corpus.diagnostics.get('copy_ambiguous_base') or []),
                'copy_cycles': len(corpus.diagnostics.get('copy_cycles') or []),
                'copy_mod_warnings': len(corpus.diagnostics.get('copy_mod_warnings') or []),
                'reader_binding_mismatches': books_report['binding_mismatches'],
                'unknown_tags': len(unknown_tags_counter),
                'unsupported_blocks': len(unsupported_blocks_counter),
            },
            'files': {
                'catalog.sqlite': db_sha,
                'reader/anna-reader.json': reader_json_sha,
                'media-remotes.json': remotes_sha,
                'summary.json': summary_sha,
                'inventory.json': inv_sha,
                'reconciliation.json': recon_sha,
                'attribution.json': attr_sha,
            },
        }
        _write_json(stage / 'manifest.json', manifest_payload)

        # 7. Validate staged output BEFORE touching final <output>
        _log('[our5etools.build] Validating staged artifacts...')
        _validate_staged_output(stage, len(corpus.entries), expected_collection=col_key)

        # 8. Atomic swap <stage> -> <output> with .prev rollback safety
        prev_backup = output.with_name(f'{output.name}.prev.{os.getpid()}')
        if prev_backup.exists():
            shutil.rmtree(prev_backup)

        had_previous = output.exists()
        if had_previous:
            output.rename(prev_backup)
        try:
            stage.rename(output)
        except BaseException:
            if had_previous and prev_backup.exists() and not output.exists():
                prev_backup.rename(output)
            raise
        if had_previous and prev_backup.exists():
            shutil.rmtree(prev_backup)

    except BaseException:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise

    elapsed = time.perf_counter() - t_start
    _log(
        f'[our5etools.build] Done in {elapsed:.1f}s -> {output} '
        f'({counts_summary["entries"]} entries, {counts_summary["book_pages"]} reader pages, '
        f'snapshot={snapshot[:12]})'
    )
    return manifest_payload


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='python -m anna.our5etools.build',
        description='Build the our5etools SQLite catalog, Stella reader pages, and audit artifacts.',
    )
    parser.add_argument(
        '--source',
        type=Path,
        default=OUR5ETOOLS,
        help=f'Path to the local our5etools mirror (default: {OUR5ETOOLS}).',
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=OUR5ETOOLS_DATA,
        help=f'Output data directory (default: {OUR5ETOOLS_DATA}).',
    )
    parser.add_argument(
        '--img-dir',
        type=Path,
        default=Path(os.environ['OUR5ETOOLS_IMG_DIR']) if os.environ.get('OUR5ETOOLS_IMG_DIR') else None,
        help='Optional local 5etools-img directory to bundle offline into data/our5etools/media/img/.',
    )
    parser.add_argument(
        '--collection',
        default=COLLECTION_KEY,
        help=f'Collection key written into snapshot metadata and links (default: {COLLECTION_KEY}).',
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress progress logs on stderr.',
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest = build(
        source=args.source,
        output=args.output,
        img_dir=args.img_dir,
        collection_key=args.collection,
        quiet=args.quiet,
    )
    if not args.quiet:
        print(json.dumps({
            'snapshot': manifest['snapshot'],
            'output': str(args.output.resolve()),
            'counts': manifest['counts'],
            'checks': manifest['checks'],
            'elapsed_seconds': manifest['elapsed_seconds'],
        }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
