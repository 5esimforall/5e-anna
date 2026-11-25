"""Per-entry static bundle hashing and collection record serialization for Pages exports."""
from collections import defaultdict
from contextlib import closing
import gzip
import json
import sqlite3

from anna.presentation import catalog, contracts
from anna.presentation.collections import DEFAULT
from anna.presentation.layout import text_start
from anna.presentation.library import name_fold
from anna.presentation.our5etools import (
    COLLECTION_KEY as OUR5ETOOLS_KEY, build_entry_row, build_relations_list,
    build_sources_map, build_version_compare, merge_subclass_tour_layout,
    promote_media_html, subclass_root_feature_id,
)
from anna.publish.pages_project import _json
import anna.catalog.views as browse
import anna.our5etools.types as otypes


ENTRY_BUCKETS_MAX = 4096
ENTRY_BUCKET_TARGET = 32


def entry_bucket_index(entry_id: str, buckets: int) -> int:
    """Deterministic UTF-8 FNV-1a 32-bit bucket index for a static entry bundle."""
    h = 0x811c9dc5
    for byte in entry_id.encode('utf-8'):
        h = ((h ^ byte) * 0x01000193) & 0xffffffff
    return h % buckets


def choose_entry_buckets(total: int) -> int:
    """Smallest power of two in [1, ENTRY_BUCKETS_MAX] keeping average bucket load <= 32."""
    buckets = 1
    while buckets < ENTRY_BUCKETS_MAX and total > buckets * ENTRY_BUCKET_TARGET:
        buckets *= 2
    return buckets


def _render_our_layout(layout_gzip, provenance_or_media, media_dir):
    if isinstance(provenance_or_media, (str, bytes)):
        media = (json.loads(provenance_or_media) or {}).get('media_summary')
    elif provenance_or_media is None:
        media = None
    else:
        media = provenance_or_media
    html_body = gzip.decompress(layout_gzip).decode('utf-8') if layout_gzip is not None else ''
    if not html_body:
        return {'status': 'empty', 'media': media}
    return {
        'status': 'ok',
        'html': promote_media_html(html_body, media_dir, lazy_media=False),
        'media': media,
    }


def _collect_default_entry_records(db_path):
    """Yield (eid, detail_dict, layout_json_bytes, tour_layout_json_bytes) matching BookCatalog/Tour."""
    with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        pages_text = dict(conn.execute('SELECT path, text FROM pages'))
        tour_layouts_by_id = {}
        for span_key, payload_gzip in conn.execute('SELECT span_key, payload_gzip FROM pages_tour_layout'):
            owner = span_key.rsplit('\0', 1)[-1]
            if owner:
                tour_layouts_by_id[owner] = payload_gzip
        relations_by_subject = defaultdict(list)
        for subj, pred, obj_id, label, status in conn.execute(
            'SELECT subject_id, predicate, object_id, target_label, status '
            'FROM relations ORDER BY subject_id, predicate, target_label, id'
        ):
            relations_by_subject[subj].append({
                'predicate': pred, 'object_id': obj_id, 'target_label': label, 'status': status,
            })

        group_rows = defaultdict(list)
        entry_group = {}
        for gid, status, eid, data_json in conn.execute(
            'SELECT m.group_id, m.status, m.entry_id, d.data '
            'FROM identity_candidate_members m '
            'JOIN entry_core e ON e.id = m.entry_id '
            'LEFT JOIN entry_data d ON d.entry_rowid = e.rowid '
            'ORDER BY m.group_id, '
            "CASE e.ruleset WHEN '5r' THEN 0 WHEN '5e' THEN 1 ELSE 2 END, "
            'e.page_path, e.start, e.id'
        ):
            entry_group[eid] = gid
            group_rows[gid].append((status, contracts.entry(json.loads(data_json))))
        versions_by_group = {
            gid: catalog._build_version_compare(gid, items[0][0], [row for _, row in items])
            for gid, items in group_rows.items()
        }

        for eid, data_json, payload_gzip in conn.execute(
            'SELECT e.id, d.data, l.payload_gzip '
            'FROM entry_core e '
            'LEFT JOIN entry_data d ON d.entry_rowid = e.rowid '
            'LEFT JOIN pages_entry_layout l ON l.entry_rowid = e.rowid '
            'ORDER BY e.rowid'
        ):
            e = browse.enrich(contracts.entry(json.loads(data_json)))
            e['field_order'] = browse.field_order(e)
            start = text_start(e)
            e['body'] = pages_text[e['page_path']][start:e['end']].strip() if e['end'] > start else ''
            e['relations'] = relations_by_subject.get(eid, [])
            gid = entry_group.get(eid)
            e['versions'] = versions_by_group.get(gid) if gid is not None else None
            e['collection'] = DEFAULT
            layout_bytes = (
                gzip.decompress(payload_gzip)
                if payload_gzip is not None
                else _json({'status': 'missing', 'path': e['page_path']})
            )
            tour_layout_bytes = (
                gzip.decompress(tour_layouts_by_id[eid])
                if eid in tour_layouts_by_id
                else _json({'status': 'empty', 'path': e['page_path']})
            )
            yield eid, e, layout_bytes, tour_layout_bytes


def _collect_our_entry_records(db_path, media_dir, extra_locations):
    """Yield (eid, detail_dict, layout_json_bytes, tour_layout_json_bytes_or_none) matching Our5etoolsCatalog/Tour."""
    with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        sources_map = build_sources_map(conn)
        entry_sources_map = defaultdict(list)
        for eid, sid in conn.execute('SELECT entry_id, source_id FROM entry_sources ORDER BY entry_id, source_id'):
            entry_sources_map[eid].append(sid)
        targets_map = {
            r[0]: (r[1], r[2], r[3])
            for r in conn.execute('SELECT id, name, type, source_code FROM entry_core')
        }
        relations_raw = defaultdict(list)
        for rr in conn.execute('SELECT source_id, type AS rel_type, target_id, meta_json FROM relations'):
            relations_raw[rr['source_id']].append(rr)

        subclass_root_map = {}
        for sc_id, sc_name, sc_name_zh in conn.execute(
            "SELECT id, name, name_zh FROM entry_core WHERE type = 'subclass'"
        ):
            root_id = subclass_root_feature_id(
                conn, 'entry_core', sc_id, sc_name, sc_name_zh or '', order_col='e.build_order',
            )
            if root_id is not None:
                subclass_root_map[sc_id] = root_id
        root_layouts = {}
        needed_roots = sorted(set(subclass_root_map.values()))
        for start in range(0, len(needed_roots), 400):
            batch = needed_roots[start:start + 400]
            ph = ','.join('?' for _ in batch)
            for rrow in conn.execute(
                'SELECT e.id, em.provenance_json, el.layout_gzip '
                'FROM entry_core e '
                'LEFT JOIN entry_meta em ON em.entry_rowid = e.rowid '
                'LEFT JOIN entry_layout el ON el.entry_rowid = e.rowid '
                f'WHERE e.id IN ({ph})',
                batch,
            ):
                root_layouts[rrow['id']] = _render_our_layout(
                    rrow['layout_gzip'], rrow['provenance_json'], media_dir,
                )

        group_rows = defaultdict(list)
        entry_group = {}
        for mr in conn.execute(
            'SELECT im.group_id, e.id, e.type, e.raw_kind, e.name, e.name_zh, e.name_en, e.ruleset, e.legacy, '
            'e.package_id, e.source_id, e.source_code, e.owner_class, e.owner_subclass, '
            'e.owner_level, e.copy_status, efld.fields_json, efld.quickref_json, e.conflicts_json, em.provenance_json '
            'FROM identity_members im '
            'JOIN entry_core e ON e.id = im.entry_id '
            'LEFT JOIN entry_fields efld ON efld.entry_rowid = e.rowid '
            'LEFT JOIN entry_meta em ON em.entry_rowid = e.rowid '
            'ORDER BY im.group_id, '
            "CASE e.ruleset WHEN '5r' THEN 0 WHEN '5e' THEN 1 ELSE 2 END, "
            'im.is_primary DESC, e.id ASC'
        ):
            gid = mr['group_id']
            entry_group[mr['id']] = gid
            group_rows[gid].append(build_entry_row(mr, sources_map, OUR5ETOOLS_KEY))
        versions_by_group = {
            gid: build_version_compare(gid, mems)
            for gid, mems in group_rows.items()
        }

        for row in conn.execute(
            'SELECT e.id, e.type, e.raw_kind, e.name, e.name_zh, e.name_en, e.ruleset, e.legacy, '
            'e.package_id, e.source_id, e.source_code, e.owner_class, e.owner_subclass, '
            'e.owner_level, e.copy_status, efld.fields_json, efld.quickref_json, e.conflicts_json, '
            'em.provenance_json, ebt.body_gzip, el.layout_gzip '
            'FROM entry_core e '
            'LEFT JOIN entry_fields efld ON efld.entry_rowid = e.rowid '
            'LEFT JOIN entry_meta em ON em.entry_rowid = e.rowid '
            'LEFT JOIN entry_body_text ebt ON ebt.entry_rowid = e.rowid '
            'LEFT JOIN entry_layout el ON el.entry_rowid = e.rowid '
            'ORDER BY e.rowid'
        ):
            eid = row['id']
            entry = build_entry_row(row, sources_map, OUR5ETOOLS_KEY)
            raw_body = gzip.decompress(row['body_gzip']).decode('utf-8') if row['body_gzip'] is not None else ''
            entry['body'] = raw_body.strip()
            entry['field_order'] = otypes.field_order(entry)
            sids = entry_sources_map.get(eid)
            if sids:
                entry['source_ids'] = sids
                entry['source_names'] = [
                    sources_map[s]['name'] if s in sources_map else s
                    for s in sids
                ]
            gid = entry_group.get(eid)
            members_compact, versions_compare = versions_by_group[gid] if gid is not None else ([], None)
            entry['members'] = members_compact
            entry['versions'] = versions_compare
            rel_rows = relations_raw.get(eid)
            entry['relations'] = build_relations_list(rel_rows, targets_map) if rel_rows else []
            loc = entry['provenance'].get('reader_location')
            if not loc:
                loc = extra_locations.get(eid)
            entry['reader_location'] = loc
            entry['collection'] = OUR5ETOOLS_KEY

            layout_dict = _render_our_layout(
                row['layout_gzip'], entry['provenance'].get('media_summary'), media_dir,
            )
            tour_layout_bytes = None
            if eid in subclass_root_map:
                merged = merge_subclass_tour_layout(root_layouts[subclass_root_map[eid]], layout_dict)
                if merged is not None and merged != layout_dict:
                    tour_layout_bytes = _json(merged)
            yield eid, entry, _json(layout_dict), tour_layout_bytes


def _export_entry_buckets(stage, default_projection, our_projection=None, our_media_dir=None,
                          our_extra_locations=None, gzip_shards=True):
    """Write hashed per-entry detail+layout bundles to stage/pages-entries for direct browser fetch."""
    collections = [(DEFAULT, default_projection, browse.STACKABLE_TYPES)]
    if our_projection is not None:
        collections.append((OUR5ETOOLS_KEY, our_projection, otypes.STACKABLE_TYPES))

    stackable_names = defaultdict(set)
    seen_ids = set()
    total_entries = 0
    for col_key, db_path, stackable_types in collections:
        with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as conn:
            for eid, typ, name_en in conn.execute('SELECT id, type, name_en FROM entry_core'):
                if eid in seen_ids:
                    raise ValueError(f'Duplicate entry ID across collections: {eid}')
                seen_ids.add(eid)
                total_entries += 1
                if typ in stackable_types and name_en:
                    folded = name_fold(name_en)
                    if folded:
                        stackable_names[(typ, folded)].add((col_key, eid))
    del seen_ids

    buckets = choose_entry_buckets(total_entries)
    bucket_items = [[] for _ in range(buckets)]

    def add_records(col_key, stackable_types, records):
        for eid, detail, layout_bytes, tour_layout_bytes in records:
            typ = detail.get('type')
            name_en = detail.get('name_en') or ''
            folded = name_fold(name_en) if typ in stackable_types and name_en else ''
            if folded:
                own_versions = (detail.get('versions') or {}).get('members') or ()
                mine = {(col_key, eid), *((col_key, m['id']) for m in own_versions)}
                has_extra = bool(stackable_names[(typ, folded)] - mine)
            else:
                has_extra = False
            tour_part = (
                b',"tour_layout":' + tour_layout_bytes
                if tour_layout_bytes is not None and tour_layout_bytes != layout_bytes
                else b''
            )
            extra = b',"has_extra_versions":true' if has_extra else b''
            record_bytes = b'{"detail":' + _json(detail) + b',"layout":' + layout_bytes + tour_part + extra + b'}'
            bucket_items[entry_bucket_index(eid, buckets)].append((eid, record_bytes))

    add_records(DEFAULT, browse.STACKABLE_TYPES, _collect_default_entry_records(default_projection))
    if our_projection is not None:
        add_records(
            OUR5ETOOLS_KEY,
            otypes.STACKABLE_TYPES,
            _collect_our_entry_records(our_projection, our_media_dir, our_extra_locations or {}),
        )
    stackable_names.clear()

    entries_dir = stage / 'pages-entries'
    entries_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(buckets):
        items = bucket_items[idx]
        bucket_items[idx] = []
        items.sort(key=lambda pair: pair[0])
        raw = b'{' + b','.join(_json(eid) + b':' + payload for eid, payload in items) + b'}'
        stored = gzip.compress(raw, compresslevel=6, mtime=0) if gzip_shards else raw
        (entries_dir / f'{idx:03x}.bin').write_bytes(stored)

    encoding = 'gzip' if gzip_shards else 'identity'
    (entries_dir / 'index.json').write_bytes(_json({
        'schema': 1,
        'buckets': buckets,
        'encoding': encoding,
        'collections': [col_key for col_key, _, _ in collections],
    }))
