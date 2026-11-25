"""Read-only audit and invariant verifier for the built our5etools dataset.

Reads built artifacts under `OUR5ETOOLS_DATA` (`catalog.sqlite`, `manifest.json`,
`summary.json`, `inventory.json`, `reconciliation.json`, `attribution.json`, and
`reader/anna-reader.json`), verifies strict corpus and reader invariants, and
writes a structured Chinese Markdown audit report (`audit.md`) plus optional JSON.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Any

from anna.our5etools.inventory import DISPOSITIONS, TOOL_DATA_NAMES
from anna.our5etools.types import BOOL_FIELDS, LABELS, TYPES, TYPE_ORDER, UNKNOWN
from anna.paths import OUR5ETOOLS_DATA

REQUIRED_ARTIFACTS: tuple[str, ...] = (
    'catalog.sqlite',
    'manifest.json',
    'summary.json',
    'inventory.json',
    'reconciliation.json',
    'attribution.json',
    'media-remotes.json',
    'reader/anna-reader.json',
)

COMMON_FACET_KEYS: frozenset[str] = frozenset({
    'ruleset',
    'origin',
    'source',
    'package',
    'legacy',
    'copy_status',
    'type',
})

DISPOSITION_LABELS: dict[str, str] = {
    'entity_data': '官方实体数据',
    'fluff_data': '官方背景与插画数据',
    'book_data': '官方规则书正文',
    'adventure_data': '官方冒险模组正文',
    'brew_shard': '社区扩展分片数据',
    'brew_recipe': '社区扩展装配清单',
    'site_brew': '站点独立资料包',
    'site_metadata': '站点与构建元数据',
    'metadata_alias': '接口与索引别名',
    'derived_index': '预生成衍生索引（不作实体源）',
    'tool_excluded': '工具/演示数据（已排除）',
    'local_media': '本地媒体资源（音频/字体/图像）',
    'i18n': '界面本地化资源',
    'web_runtime': '原站点前端运行文件（不导入实体）',
    'repo_helper': '仓库辅助脚本与文档（不导入实体）',
}

COPY_STATUS_LABELS: dict[str, str] = {
    'ok': '正常',
    'missing_base': '缺失基类',
    'ambiguous_base': '基类歧义',
    'cycle': '循环引用',
    'mod_warning': '修改指令告警',
}

_EXCLUDED_ENTITY_PREFIXES: tuple[str, ...] = (
    'search/',
    'data/generated/',
    'homebrew/_bundle/',
    'homebrew/_indexes/',
)


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def _load_json(path: Path) -> tuple[Any | None, str | None]:
    if not path.is_file():
        return None, f'缺失文件：{path.name}'
    try:
        return json.loads(path.read_text(encoding='utf-8')), None
    except Exception as exc:
        return None, f'JSON 解析失败 ({path.name}): {exc}'


def _is_safe_relpath(relpath: str) -> bool:
    if not isinstance(relpath, str) or not relpath.strip():
        return False
    if '\\' in relpath or relpath.startswith('/'):
        return False
    p = PurePosixPath(relpath)
    return bool(p.parts) and not p.is_absolute() and '..' not in p.parts


def _is_excluded_entity_path(path_str: str) -> bool:
    if any(path_str.startswith(pref) for pref in _EXCLUDED_ENTITY_PREFIXES):
        return True
    name = PurePosixPath(path_str).name
    if name in TOOL_DATA_NAMES:
        return True
    if name.startswith('foundry') and name.endswith('.json'):
        return True
    if name in {'index.json', 'fluff-index.json', 'sources.json'} and path_str.startswith('data/'):
        return True
    return False


def _add_invariant(
    invariants: list[dict[str, Any]],
    *,
    key: str,
    title: str,
    ok: bool,
    detail: str,
    failures: list[str] | None = None,
) -> None:
    invariants.append({
        'key': key,
        'title': title,
        'ok': bool(ok),
        'detail': detail,
        'failure_count': len(failures) if failures else (0 if ok else 1),
        'failures': (failures or [])[:25],
    })


def _check_inventory(inv_data: dict[str, Any] | None, invariants: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(inv_data, dict):
        _add_invariant(
            invariants,
            key='inventory_present',
            title='仓库盘点清单完整',
            ok=False,
            detail='inventory.json 缺失或结构无效',
            failures=['inventory.json 缺失或非对象'],
        )
        return {}

    files = inv_data.get('files')
    if not isinstance(files, list):
        files = []
    total_files_declared = inv_data.get('total_files')
    by_disp_actual: Counter[str] = Counter()
    bytes_by_disp: Counter[str] = Counter()
    disp_failures: list[str] = []
    excluded_as_entity: list[str] = []
    seen_paths: set[str] = set()
    dup_paths: list[str] = []

    for idx, rec in enumerate(files):
        if not isinstance(rec, dict):
            disp_failures.append(f'files[{idx}] 非对象')
            continue
        fpath = rec.get('path')
        disp = rec.get('disposition')
        reason = rec.get('reason')
        size = rec.get('size', 0)
        if not isinstance(fpath, str) or not fpath.strip():
            disp_failures.append(f'files[{idx}] 缺少有效 path')
            continue
        if fpath in seen_paths:
            dup_paths.append(fpath)
        seen_paths.add(fpath)
        if not isinstance(disp, str) or disp not in DISPOSITIONS:
            disp_failures.append(f'{fpath}: 无效 disposition={disp!r}')
        else:
            by_disp_actual[disp] += 1
            if isinstance(size, int):
                bytes_by_disp[disp] += size
        if not isinstance(reason, str) or not reason.strip():
            disp_failures.append(f'{fpath}: 缺少 disposition 说明 reason')
        if disp == 'entity_data' and _is_excluded_entity_path(fpath):
            excluded_as_entity.append(fpath)

    if total_files_declared is not None and total_files_declared != len(files):
        disp_failures.append(f'total_files={total_files_declared} 与 len(files)={len(files)} 不一致')
    if dup_paths:
        disp_failures.append(f'存在重复文件路径：{len(dup_paths)} 个（样例：{dup_paths[:3]}）')
    if not files:
        disp_failures.append('files 为空，未盘点任何仓库文件')

    _add_invariant(
        invariants,
        key='inventory_dispositions',
        title='仓库文件全量处置明确',
        ok=len(disp_failures) == 0,
        detail=f'已盘点 {len(files)} 个文件，覆盖 {len(by_disp_actual)} 类处置，缺失/无效 {len(disp_failures)} 处',
        failures=disp_failures,
    )

    _add_invariant(
        invariants,
        key='inventory_no_excluded_entities',
        title='排除文件未误入实体数据源',
        ok=len(excluded_as_entity) == 0,
        detail=f'发现 {len(excluded_as_entity)} 个应排除文件被标为 entity_data',
        failures=excluded_as_entity,
    )

    checks = inv_data.get('checks') if isinstance(inv_data.get('checks'), dict) else {}
    shard_failures: list[str] = []
    missing_shard_files = checks.get('missing_shard_files') or []
    missing_shard_keys = checks.get('missing_shard_keys') or []
    invalid_shard_paths = checks.get('invalid_shard_paths') or []
    missing_site_packages = checks.get('missing_site_packages') or []
    books_recipe_match = checks.get('books_recipe_keys_match')
    if missing_shard_files:
        shard_failures.append(f'缺失分片文件 {len(missing_shard_files)} 个：{missing_shard_files[:5]}')
    if missing_shard_keys:
        shard_failures.append(f'缺失分片键 {len(missing_shard_keys)} 个：{missing_shard_keys[:5]}')
    if invalid_shard_paths:
        shard_failures.append(f'非法分片路径 {len(invalid_shard_paths)} 个：{invalid_shard_paths[:5]}')
    if missing_site_packages:
        shard_failures.append(f'缺失站点独立包 {len(missing_site_packages)} 个：{missing_site_packages[:5]}')
    if books_recipe_match is False:
        shard_failures.append('__api/books.json 与 homebrew/_recipes/all.json 包键不一致')

    _add_invariant(
        invariants,
        key='inventory_brew_shards',
        title='社区扩展分片与配方完整性',
        ok=len(shard_failures) == 0,
        detail=(
            f'books_recipe_keys_match={books_recipe_match}，缺失分片文件 {len(missing_shard_files)}，'
            f'缺失分片键 {len(missing_shard_keys)}，缺失站点包 {len(missing_site_packages)}'
        ),
        failures=shard_failures,
    )

    return {
        'snapshot_info': inv_data.get('snapshot_info') or {},
        'total_files': len(files),
        'total_bytes': sum(bytes_by_disp.values()),
        'by_disposition': dict(sorted(by_disp_actual.items())),
        'bytes_by_disposition': dict(sorted(bytes_by_disp.items())),
        'checks': checks,
    }


def _check_sqlite(
    db_path: Path,
    invariants: list[dict[str, Any]],
) -> tuple[dict[str, Any], set[str]]:
    if not db_path.is_file():
        _add_invariant(
            invariants,
            key='sqlite_present',
            title='SQLite 目录库可读',
            ok=False,
            detail='catalog.sqlite 不存在',
            failures=['catalog.sqlite 缺失'],
        )
        return {}, set()

    conn = _connect_ro(db_path)
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required_tables = {
            'entries',
            'entry_facets',
            'sources',
            'packages',
            'identity_groups',
            'identity_members',
            'relations',
            'book_pages',
            'book_search',
        }
        missing_tables = sorted(required_tables - tables)
        _add_invariant(
            invariants,
            key='sqlite_schema',
            title='SQLite 必需数据表完整',
            ok=len(missing_tables) == 0,
            detail=f'已检测 {len(tables)} 张表，缺失 {len(missing_tables)} 张',
            failures=[f'缺少表：{t}' for t in missing_tables],
        )
        if missing_tables:
            return {'tables': sorted(tables)}, set()

        source_rows = conn.execute('SELECT id, package_id, origin, ruleset, short_name, name FROM sources').fetchall()
        source_ids = {r['id'] for r in source_rows}
        source_meta = {r['id']: dict(r) for r in source_rows}

        package_rows = conn.execute('SELECT id, name, origin, ours, entry_count, book_count FROM packages').fetchall()
        package_ids = {r['id'] for r in package_rows}
        package_meta = {r['id']: dict(r) for r in package_rows}

        entry_id_failures: list[str] = []
        type_failures: list[str] = []
        provenance_failures: list[str] = []
        bool_failures: list[str] = []
        json_parse_failures: list[str] = []

        entry_ids: set[str] = set()
        expected_quickref: dict[str, set[tuple[str, str]]] = {}
        null_bool_fields: dict[str, set[str]] = {}

        by_type: Counter[str] = Counter()
        by_raw_kind: Counter[str] = Counter()
        by_ruleset: Counter[str] = Counter()
        by_origin: Counter[str] = Counter()
        by_package: Counter[str] = Counter()
        by_source: Counter[str] = Counter()
        by_copy_status: Counter[str] = Counter()
        conflict_entries = 0
        conflict_field_counts: Counter[str] = Counter()
        unknown_bool_counts: Counter[str] = Counter()
        copy_non_ok_samples: list[dict[str, Any]] = []

        cursor = conn.execute(
            'SELECT id, type, raw_kind, name, ruleset, legacy, package_id, source_id, '
            'copy_status, fields_json, quickref_json, conflicts_json FROM entries'
        )
        for row in cursor:
            eid = row['id']
            etype = row['type']
            rkind = row['raw_kind']
            pkg_id = row['package_id']
            src_id = row['source_id']
            ruleset = row['ruleset']
            cstatus = row['copy_status'] or 'ok'

            if not isinstance(eid, str) or not eid.startswith('o5e_'):
                entry_id_failures.append(f'非法条目 ID：{eid!r}')
            elif eid in entry_ids:
                entry_id_failures.append(f'重复条目 ID：{eid}')
            else:
                entry_ids.add(eid)

            if etype not in TYPES:
                type_failures.append(f'{eid}: 未知类型 {etype!r}')

            if not isinstance(src_id, str) or not src_id or src_id not in source_ids:
                provenance_failures.append(f'{eid}: 无效或缺失 source_id={src_id!r}')
            if not isinstance(pkg_id, str) or not pkg_id or pkg_id not in package_ids:
                provenance_failures.append(f'{eid}: 无效或缺失 package_id={pkg_id!r}')

            by_type[etype] += 1
            by_raw_kind[rkind or ''] += 1
            by_ruleset[ruleset or ''] += 1
            src_info = source_meta.get(src_id)
            by_origin[(src_info['origin'] if src_info else '') or '未知'] += 1
            by_package[pkg_id or ''] += 1
            by_source[src_id or ''] += 1
            by_copy_status[cstatus] += 1

            if cstatus != 'ok' and len(copy_non_ok_samples) < 20:
                copy_non_ok_samples.append({
                    'id': eid,
                    'name': row['name'],
                    'type': etype,
                    'source_id': src_id,
                    'copy_status': cstatus,
                })

            try:
                fields = json.loads(row['fields_json']) if row['fields_json'] else {}
                quickref = json.loads(row['quickref_json']) if row['quickref_json'] else {}
                conflicts = json.loads(row['conflicts_json']) if row['conflicts_json'] else []
            except Exception as exc:
                json_parse_failures.append(f'{eid}: JSON 解析失败: {exc}')
                continue

            if isinstance(conflicts, list) and conflicts:
                conflict_entries += 1
                for cf in conflicts:
                    conflict_field_counts[str(cf)] += 1

            if not isinstance(fields, dict):
                json_parse_failures.append(f'{eid}: fields_json 非对象')
                fields = {}
            if not isinstance(quickref, dict):
                json_parse_failures.append(f'{eid}: quickref_json 非对象')
                quickref = {}

            # Record expected quickref pairs for entry_facets parity check
            qr_pairs: set[tuple[str, str]] = set()
            for qk, qvals in quickref.items():
                if isinstance(qvals, list):
                    for qv in qvals:
                        qr_pairs.add((str(qk), str(qv)))
                elif qvals is not None:
                    qr_pairs.add((str(qk), str(qvals)))
            expected_quickref[eid] = qr_pairs

            # Strict boolean unknown check: never coerce missing/None booleans to False / 'false'
            entry_null_bools: set[str] = set()
            mat_raw = fields.get('material')
            has_material_token = mat_raw in {'M', 'M*', '×'} or (
                isinstance(mat_raw, list) and any(x in {'M', 'M*', '×'} for x in mat_raw)
            )
            for bf in BOOL_FIELDS:
                fval = fields.get(bf)
                qval = quickref.get(bf)
                if bf in fields and fval is None:
                    entry_null_bools.add(bf)
                    if qval is not None and qval != [UNKNOWN]:
                        bool_failures.append(f'{eid}: {bf}=None 但 quickref={qval!r}（禁止强制为 false）')
                elif bf not in fields:
                    if not (bf == 'special_material' and has_material_token):
                        entry_null_bools.add(bf)
                        if qval is not None and qval != [UNKNOWN]:
                            bool_failures.append(f'{eid}: 缺失字段 {bf} 但 quickref={qval!r}')
                if qval == [UNKNOWN]:
                    unknown_bool_counts[bf] += 1

                # When copy_status != 'ok', absence-inferred False must never appear on 5etools omit-when-false fields
                if cstatus != 'ok':
                    if etype == 'creature' and bf in {'legendary_actions', 'lair_actions'} and (
                        fval is False or qval == ['false']
                    ):
                        bool_failures.append(f'{eid} ({cstatus}): 生物布尔字段 {bf} 被推断为 False')
                    elif etype == 'magic_item' and bf == 'attunement' and (
                        fval is False or qval == ['false']
                    ):
                        bool_failures.append(f'{eid} ({cstatus}): 魔法物品同调在未解析基类时被推断为 False')
                    elif etype == 'spell' and bf in {'concentration', 'ritual', 'verbal', 'somatic', 'material', 'special_material'}:
                        if bf == 'ritual' and fval is False and not fields.get('casting_time'):
                            bool_failures.append(f'{eid} ({cstatus}): 残缺法术 {bf} 被推断为 False')
                        elif bf == 'concentration' and (fval is False or qval == ['false']):
                            bool_failures.append(f'{eid} ({cstatus}): 残缺法术 {bf} 被推断为 False')
            if entry_null_bools:
                null_bool_fields[eid] = entry_null_bools

        _add_invariant(
            invariants,
            key='entries_ids_unique',
            title='条目 ID 唯一且符合 o5e_* 前缀',
            ok=len(entry_ids) > 0 and len(entry_id_failures) == 0,
            detail=f'共 {len(entry_ids)} 条条目，异常 {len(entry_id_failures)} 处',
            failures=entry_id_failures if entry_id_failures else ([] if entry_ids else ['entries 表为空']),
        )
        _add_invariant(
            invariants,
            key='entries_valid_types',
            title='条目类型均属于 TYPES 词汇表',
            ok=len(type_failures) == 0,
            detail=f'覆盖 {len(by_type)}/{len(TYPES)} 种类型，无效类型 {len(type_failures)} 处',
            failures=type_failures,
        )
        _add_invariant(
            invariants,
            key='entries_valid_provenance',
            title='条目来源与内容包引用完整',
            ok=len(provenance_failures) == 0,
            detail=f'来源 {len(source_ids)} 个，内容包 {len(package_ids)} 个，断链 {len(provenance_failures)} 处',
            failures=provenance_failures,
        )
        _add_invariant(
            invariants,
            key='entries_json_valid',
            title='条目结构化 JSON 字段可解析',
            ok=len(json_parse_failures) == 0,
            detail=f'JSON 解析异常 {len(json_parse_failures)} 处',
            failures=json_parse_failures,
        )

        # Check entry_facets parity with quickref_json and unknown boolean safety
        facet_cols = {r[1] for r in conn.execute('PRAGMA table_info(entry_facets)')}
        if {'facet_key', 'facet_value'} <= facet_cols:
            fk_col, fv_col = 'facet_key', 'facet_value'
        else:
            fk_col, fv_col = 'field', 'value'

        actual_quickref: dict[str, set[tuple[str, str]]] = defaultdict(set)
        dangling_facet_ids: list[str] = []
        facet_rows_count = 0
        for row in conn.execute(f'SELECT entry_id, {fk_col}, {fv_col} FROM entry_facets'):
            facet_rows_count += 1
            eid = row[0]
            fk = str(row[1])
            fv = str(row[2])
            if eid not in entry_ids:
                if len(dangling_facet_ids) < 25:
                    dangling_facet_ids.append(f'孤立 entry_facets: entry_id={eid!r} ({fk}={fv})')
                continue
            if fk in COMMON_FACET_KEYS:
                continue
            actual_quickref[eid].add((fk, fv))
            if fk in BOOL_FIELDS and fv == 'false' and fk in null_bool_fields.get(eid, ()):
                if len(bool_failures) < 25:
                    bool_failures.append(f'{eid}: entry_facets 将未提取布尔 {fk} 存为 false')

        parity_failures: list[str] = list(dangling_facet_ids)
        for eid, exp_set in expected_quickref.items():
            act_set = actual_quickref.get(eid, set())
            if exp_set != act_set:
                missing_in_facets = sorted(exp_set - act_set)
                extra_in_facets = sorted(act_set - exp_set)
                if len(parity_failures) < 25:
                    parity_failures.append(
                        f'{eid}: quickref 与 entry_facets 不一致 '
                        f'(缺 {missing_in_facets[:3]}, 多 {extra_in_facets[:3]})'
                    )

        _add_invariant(
            invariants,
            key='entries_unknown_booleans',
            title='未提取布尔字段严禁强制为 False',
            ok=len(bool_failures) == 0,
            detail=f'检测到 {len(bool_failures)} 处未提取布尔强制为 false 的违规',
            failures=bool_failures,
        )
        _add_invariant(
            invariants,
            key='quickref_facets_parity',
            title='quickref_json 与 entry_facets 严格一致',
            ok=len(parity_failures) == 0,
            detail=f'核对 {len(entry_ids)} 条目与 {facet_rows_count} 行分面索引，不一致 {len(parity_failures)} 处',
            failures=parity_failures,
        )

        # Relational integrity checks (identity_groups / identity_members / relations)
        rel_count = conn.execute('SELECT count(*) FROM relations').fetchone()[0]
        ig_count = conn.execute('SELECT count(*) FROM identity_groups').fetchone()[0]
        im_count = conn.execute('SELECT count(*) FROM identity_members').fetchone()[0]
        bs_count = conn.execute('SELECT count(*) FROM book_search').fetchone()[0]

        rel_failures: list[str] = []
        dangling_im = conn.execute(
            'SELECT im.entry_id, im.group_id FROM identity_members im '
            'LEFT JOIN entries e ON e.id = im.entry_id '
            'LEFT JOIN identity_groups g ON g.group_id = im.group_id '
            'WHERE e.id IS NULL OR g.group_id IS NULL LIMIT 25'
        ).fetchall()
        for r in dangling_im:
            rel_failures.append(f'identity_members 断链: entry_id={r[0]!r}, group_id={r[1]!r}')

        dangling_rel_src = conn.execute(
            'SELECT r.source_id, r.type, r.target_id FROM relations r '
            'LEFT JOIN entries e ON e.id = r.source_id '
            'WHERE e.id IS NULL LIMIT 25'
        ).fetchall()
        for r in dangling_rel_src:
            rel_failures.append(f'relations 源条目不存在: source_id={r[0]!r}, type={r[1]!r}')

        dangling_rel_tgt = conn.execute(
            "SELECT r.source_id, r.type, r.target_id FROM relations r "
            "LEFT JOIN entries e ON e.id = r.target_id "
            "WHERE r.target_id LIKE 'o5e_%' AND e.id IS NULL LIMIT 25"
        ).fetchall()
        for r in dangling_rel_tgt:
            rel_failures.append(f'relations 目标条目不存在: {r[0]} -[{r[1]}]-> {r[2]}')

        _add_invariant(
            invariants,
            key='sqlite_relations_integrity',
            title='关系与叠放群组引用完整',
            ok=len(rel_failures) == 0,
            detail=f'relations={rel_count}，identity_groups={ig_count}，identity_members={im_count}，断链 {len(rel_failures)} 处',
            failures=rel_failures,
        )

        book_page_paths = {r[0] for r in conn.execute('SELECT page_path FROM book_pages')}

        # Top packages and sources for reporting
        top_packages = [
            {
                'id': pid,
                'name': package_meta.get(pid, {}).get('name', pid),
                'origin': package_meta.get(pid, {}).get('origin', ''),
                'ours': bool(package_meta.get(pid, {}).get('ours')),
                'entries': cnt,
                'books': package_meta.get(pid, {}).get('book_count', 0),
            }
            for pid, cnt in by_package.most_common(30)
        ]
        top_sources = [
            {
                'id': sid,
                'short_name': source_meta.get(sid, {}).get('short_name', ''),
                'name': source_meta.get(sid, {}).get('name', sid),
                'origin': source_meta.get(sid, {}).get('origin', ''),
                'ruleset': source_meta.get(sid, {}).get('ruleset', ''),
                'entries': cnt,
            }
            for sid, cnt in by_source.most_common(30)
        ]

        return {
            'counts': {
                'entries': len(entry_ids),
                'sources': len(source_ids),
                'packages': len(package_ids),
                'ours_packages': sum(1 for p in package_rows if p['ours']),
                'community_packages': sum(1 for p in package_rows if p['id'].startswith('brew:') and not p['ours']),
                'relations': rel_count,
                'identity_groups': ig_count,
                'identity_members': im_count,
                'entry_facets': facet_rows_count,
                'book_pages': len(book_page_paths),
                'book_search': bs_count,
            },
            'by_type': {t: by_type[t] for t in TYPE_ORDER if by_type[t]},
            'by_raw_kind': dict(by_raw_kind.most_common()),
            'by_ruleset': dict(by_ruleset.most_common()),
            'by_origin': dict(by_origin.most_common()),
            'by_copy_status': dict(by_copy_status.most_common()),
            'copy_non_ok_samples': copy_non_ok_samples,
            'conflict_entries': conflict_entries,
            'conflict_fields': dict(conflict_field_counts.most_common()),
            'unknown_booleans': dict(unknown_bool_counts.most_common()),
            'top_packages': top_packages,
            'top_sources': top_sources,
        }, book_page_paths
    finally:
        conn.close()


def _check_media_bundle(
    root: Path,
    inv_data: dict[str, Any] | None,
    remotes_data: dict[str, Any] | None,
    invariants: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify self-contained `media/` bundles all local repo assets and valid `media-remotes.json`."""
    media_root = root / 'media'
    missing: list[str] = []
    expected_local = 0
    if not media_root.is_dir():
        missing.append('缺失 media/ 目录')
    elif isinstance(inv_data, dict):
        for rec in inv_data.get('files') or ():
            if not isinstance(rec, dict) or rec.get('disposition') != 'local_media':
                continue
            rel = rec.get('path')
            if not isinstance(rel, str) or not _is_safe_relpath(rel):
                continue
            expected_local += 1
            if not (media_root / rel).is_file():
                if len(missing) < 25:
                    missing.append(f'media/{rel}')
    bundled_count = 0
    bundled_bytes = 0
    cached_remote_count = 0
    if media_root.is_dir():
        for p in media_root.rglob('*'):
            if p.is_symlink() or not p.is_file() or p.name.startswith('.'):
                continue
            bundled_count += 1
            bundled_bytes += p.stat().st_size
            rel = p.relative_to(media_root).as_posix()
            if rel.startswith(('img/', 'ext/')):
                cached_remote_count += 1
    _add_invariant(
        invariants,
        key='media_bundle_self_contained',
        title='快照媒体目录完整自包含',
        ok=len(missing) == 0 and media_root.is_dir(),
        detail=f'核对 {expected_local} 项本地媒体，缺失 {len(missing)} 项；media/ 共 {bundled_count} 个文件（含已缓存远端媒体 {cached_remote_count} 个）',
        failures=missing,
    )
    remote_keys = len(remotes_data.get('entries') or {}) if isinstance(remotes_data, dict) else 0
    return {
        'bundled_count': bundled_count,
        'bundled_bytes': bundled_bytes,
        'cached_remote_count': cached_remote_count,
        'remote_keys': remote_keys,
    }


def _check_collection_identity(
    root: Path,
    manifest_data: dict[str, Any] | None,
    summary_data: dict[str, Any] | None,
    reader_data: dict[str, Any] | None,
    invariants: list[dict[str, Any]],
) -> str:
    """Verify consistent non-empty collection key across manifest, reader, SQLite, and rendered HTML."""
    m_col = str((manifest_data or {}).get('collection') or '').strip() if isinstance(manifest_data, dict) else ''
    s_col = str((summary_data or {}).get('collection') or '').strip() if isinstance(summary_data, dict) else ''
    r_col = str((reader_data or {}).get('collection') or '').strip() if isinstance(reader_data, dict) else ''

    failures: list[str] = []
    if not m_col:
        failures.append('manifest.json 缺少 collection 标识')
    if m_col and s_col and s_col != m_col:
        failures.append(f'summary.json collection={s_col!r} 与 manifest={m_col!r} 不一致')
    if m_col and r_col != m_col:
        failures.append(f'reader/anna-reader.json collection={r_col!r} 与 manifest={m_col!r} 不一致')

    db_path = root / 'catalog.sqlite'
    if db_path.is_file():
        conn = _connect_ro(db_path)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            meta_col = ''
            if 'metadata' in tables:
                row = conn.execute("SELECT value FROM metadata WHERE key = 'collection'").fetchone()
                meta_col = str(row[0] or '').strip() if row else ''
            if m_col and meta_col != m_col:
                failures.append(f'SQLite metadata.collection={meta_col!r} 与 manifest={m_col!r} 不一致')
            if 'entries' in tables and m_col:
                bad_prov = int(
                    conn.execute(
                        "SELECT count(*) FROM entries WHERE coalesce(json_extract(provenance_json, '$.collection'), '') != ?",
                        (m_col,),
                    ).fetchone()[0]
                )
                if bad_prov:
                    failures.append(f'entries.provenance_json 中有 {bad_prov} 条 collection != {m_col!r}')
                if m_col == 'our5etools':
                    stale_hrefs = int(
                        conn.execute(
                            'SELECT count(*) FROM entries WHERE layout_html LIKE \'%collection=5etools%\' OR layout_html LIKE \'%title="5etools 中%\''
                        ).fetchone()[0]
                    )
                    if stale_hrefs:
                        failures.append(f'layout_html 中仍有 {stale_hrefs} 条残留 5etools 标识')
        finally:
            conn.close()

    _add_invariant(
        invariants,
        key='collection_identity',
        title='资料库来源标识一致',
        ok=len(failures) == 0,
        detail=f'collection={m_col or "缺失"}，跨产物一致性偏差 {len(failures)} 处',
        failures=failures,
    )
    return m_col


def _check_reader(
    data_dir: Path,
    reader_data: dict[str, Any] | None,
    book_page_paths: set[str],
    manifest_data: dict[str, Any] | None,
    invariants: list[dict[str, Any]],
) -> dict[str, Any]:
    reader_dir = data_dir / 'reader'
    if not isinstance(reader_data, dict):
        _add_invariant(
            invariants,
            key='reader_index_valid',
            title='阅读器索引与章节页面完整',
            ok=False,
            detail='reader/anna-reader.json 缺失或无效',
            failures=['reader/anna-reader.json 缺失或非对象'],
        )
        return {}

    nodes = reader_data.get('nodes')
    if not isinstance(nodes, list):
        nodes = []
    pages_raw = reader_data.get('pages')
    if isinstance(pages_raw, dict):
        page_paths = list(pages_raw.keys())
    elif isinstance(pages_raw, list):
        page_paths = [
            p['path'] if isinstance(p, dict) and 'path' in p else str(p)
            for p in pages_raw
        ]
    else:
        page_paths = []
    page_set = set(page_paths)

    # 1. Parent-before-child TOC order & unique node IDs
    toc_failures: list[str] = []
    seen_node_ids: set[str] = set()
    present_node_paths: set[str] = set()
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            toc_failures.append(f'nodes[{idx}] 非对象')
            continue
        nid = node.get('id')
        pid = node.get('parent_id') or ''
        status = node.get('status')
        npath = node.get('path') or ''
        if not isinstance(nid, str) or not nid:
            toc_failures.append(f'nodes[{idx}] 缺少有效 id')
            continue
        if nid in seen_node_ids:
            toc_failures.append(f'重复 TOC 节点 id: {nid}')
        if pid and pid not in seen_node_ids:
            toc_failures.append(f'TOC 节点 {nid} 的父节点 {pid} 未在之前出现（违反父先于子顺序）')
        seen_node_ids.add(nid)
        if status == 'present' and npath:
            present_node_paths.add(npath)
            if npath not in page_set:
                toc_failures.append(f'TOC 节点 {nid} 指向未登记页面 {npath}')

    if not nodes:
        toc_failures.append('anna-reader.json 中 nodes 为空')

    _add_invariant(
        invariants,
        key='reader_toc_order',
        title='阅读器目录树满足父先于子且节点唯一',
        ok=len(toc_failures) == 0,
        detail=f'共 {len(nodes)} 个 TOC 节点，违规 {len(toc_failures)} 处',
        failures=toc_failures,
    )

    # 2. Every page in anna-reader.json & book_pages exists on disk
    page_failures: list[str] = []
    all_checked_paths = sorted(page_set | present_node_paths | book_page_paths)
    missing_disk = 0
    for ppath in all_checked_paths:
        if not _is_safe_relpath(ppath):
            page_failures.append(f'非法页面路径：{ppath!r}')
            continue
        disk_file = reader_dir / ppath
        if not disk_file.is_file():
            missing_disk += 1
            if len(page_failures) < 25:
                page_failures.append(f'磁盘缺少阅读器页面：reader/{ppath}')
    if not page_set:
        page_failures.append('anna-reader.json 中 pages 为空')

    # Check book_pages table vs anna-reader.json pages
    unlisted_db_pages = sorted(book_page_paths - page_set)
    for up in unlisted_db_pages[:10]:
        page_failures.append(f'SQLite book_pages 中存在未列入 anna-reader.json 的页面：{up}')

    _add_invariant(
        invariants,
        key='reader_pages_on_disk',
        title='阅读器所有章节页面均存在于磁盘',
        ok=len(page_failures) == 0,
        detail=f'核对 {len(page_set)} 个索引页与 {len(book_page_paths)} 个数据库页，磁盘缺失 {missing_disk} 页',
        failures=page_failures,
    )

    # 3. Binding mismatches from manifest reader summary
    manifest_reader = (
        manifest_data.get('reader')
        if isinstance(manifest_data, dict) and isinstance(manifest_data.get('reader'), dict)
        else {}
    )
    mismatches = manifest_reader.get('binding_mismatches', 0)
    if isinstance(mismatches, int):
        _add_invariant(
            invariants,
            key='reader_bindings',
            title='阅读器 DOM 绑定零失配',
            ok=mismatches == 0,
            detail=f'binding_mismatches={mismatches}',
            failures=[] if mismatches == 0 else [f'DOM 绑定失配 {mismatches} 处'],
        )

    return {
        'schema': reader_data.get('schema'),
        'roots_count': len(reader_data.get('roots') or []),
        'nodes_count': len(nodes),
        'pages_count': len(page_set),
        'entry_locations_count': len(reader_data.get('entry_locations') or {}),
        'manifest_reader': manifest_reader,
    }


def audit_data(data_dir: Path) -> dict[str, Any]:
    """Run all invariants and assemble the full structured audit report."""
    root = Path(data_dir).expanduser().resolve()
    invariants: list[dict[str, Any]] = []

    # 0. Required artifacts presence
    missing_artifacts = [rel for rel in REQUIRED_ARTIFACTS if not (root / rel).is_file()]
    _add_invariant(
        invariants,
        key='artifacts_present',
        title='必需构建产物文件齐全',
        ok=len(missing_artifacts) == 0,
        detail=f'检查 {len(REQUIRED_ARTIFACTS)} 项必需产物，缺失 {len(missing_artifacts)} 项',
        failures=[f'缺少产物：{m}' for m in missing_artifacts],
    )

    manifest_data, manifest_err = _load_json(root / 'manifest.json')
    summary_data, summary_err = _load_json(root / 'summary.json')
    inventory_data, inventory_err = _load_json(root / 'inventory.json')
    reconciliation_data, recon_err = _load_json(root / 'reconciliation.json')
    attribution_data, attr_err = _load_json(root / 'attribution.json')
    remotes_data, remotes_err = _load_json(root / 'media-remotes.json')
    reader_data, reader_err = _load_json(root / 'reader/anna-reader.json')

    json_errs = [e for e in (manifest_err, summary_err, inventory_err, recon_err, attr_err, remotes_err, reader_err) if e]
    _add_invariant(
        invariants,
        key='artifacts_json_valid',
        title='构建产物 JSON 均可正常解析',
        ok=len(json_errs) == 0,
        detail=f'7 份 JSON 产物中解析失败 {len(json_errs)} 份',
        failures=json_errs,
    )

    inv_section = _check_inventory(inventory_data, invariants)
    media_section = _check_media_bundle(root, inventory_data, remotes_data, invariants)
    db_section, book_page_paths = _check_sqlite(root / 'catalog.sqlite', invariants)
    reader_section = _check_reader(root, reader_data, book_page_paths, manifest_data, invariants)
    _check_collection_identity(root, manifest_data, summary_data, reader_data, invariants)

    # Cross-check summary.json counts vs SQLite counts if summary.json is present
    if isinstance(summary_data, dict) and db_section.get('counts'):
        sc = summary_data.get('counts') if isinstance(summary_data.get('counts'), dict) else {}
        summary_mismatches: list[str] = []
        for k in ('entries', 'sources', 'packages', 'relations', 'identity_groups'):
            if k in sc and sc[k] != db_section['counts'].get(k):
                summary_mismatches.append(f'summary.counts.{k}={sc[k]} != sqlite {db_section["counts"].get(k)}')
        _add_invariant(
            invariants,
            key='summary_sqlite_consistency',
            title='summary.json 与 SQLite 计数一致',
            ok=len(summary_mismatches) == 0,
            detail=f'核对核心计数项，不一致 {len(summary_mismatches)} 处',
            failures=summary_mismatches,
        )

    all_ok = all(inv['ok'] for inv in invariants)
    failed_invariants = [inv for inv in invariants if not inv['ok']]

    return {
        'ok': all_ok,
        'data_dir': str(root),
        'manifest': manifest_data if isinstance(manifest_data, dict) else {},
        'summary': summary_data if isinstance(summary_data, dict) else {},
        'invariants': invariants,
        'failed_count': len(failed_invariants),
        'inventory': inv_section,
        'media_bundle': media_section,
        'catalog': db_section,
        'reconciliation': reconciliation_data if isinstance(reconciliation_data, dict) else {},
        'reader': reader_section,
        'attribution': attribution_data if isinstance(attribution_data, dict) else {},
    }


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        return f'{n / 1024:.1f} KiB'
    return f'{n / (1024 * 1024):.2f} MiB'


def _md_escape(val: Any) -> str:
    return str(val).replace('|', '\\|').replace('\n', ' ')


def render_markdown(report: dict[str, Any]) -> str:
    """Render the structured audit report as readable Chinese Markdown."""
    lines: list[str] = []
    manifest = report.get('manifest') or {}
    inv = report.get('inventory') or {}
    snap = inv.get('snapshot_info') or {}
    cat = report.get('catalog') or {}
    cat_counts = cat.get('counts') or {}
    recon = report.get('reconciliation') or {}
    reader = report.get('reader') or {}
    attr = report.get('attribution') or {}
    invariants = report.get('invariants') or []

    status_badge = '通过（全部不变量满足）' if report.get('ok') else f'失败（{report.get("failed_count", 0)} 项不变量未通过）'

    lines.append('# our5etools 数据集构建审计报告')
    lines.append('')
    lines.append(f'- **审计结论**：{status_badge}')
    lines.append(f'- **资料库标识**：`{manifest.get("collection", "")}`')
    lines.append(f'- **数据目录**：`{report.get("data_dir", "")}`')
    lines.append(f'- **快照标识**：`{manifest.get("snapshot") or snap.get("input_sha256", "")}`')
    lines.append(f'- **源仓库提交**：`{manifest.get("git_commit") or snap.get("git_commit", "")}`')
    if snap.get('app') or snap.get('version') or snap.get('data_date'):
        lines.append(
            f'- **上游版本**：{snap.get("app", "our5etools")} {snap.get("version", "")} '
            f'（build `{snap.get("build", "")}`，数据日期 `{snap.get("data_date", "")}`）'
        )
    if manifest.get('built_at'):
        lines.append(f'- **构建时间**：`{manifest["built_at"]}`')
    lines.append('')

    # 1. Invariants table
    lines.append('## 1. 严格不变量核查')
    lines.append('')
    lines.append('| 核查项 | 状态 | 详情 |')
    lines.append('| --- | --- | --- |')
    for item in invariants:
        mark = '通过' if item['ok'] else '**失败**'
        lines.append(f'| {_md_escape(item["title"])} | {mark} | {_md_escape(item["detail"])} |')
    lines.append('')

    failed_items = [i for i in invariants if not i['ok']]
    if failed_items:
        lines.append('### 失败样例')
        lines.append('')
        for item in failed_items:
            lines.append(f'- **{item["title"]}**（共 {item["failure_count"]} 处）：')
            for f in item.get('failures', [])[:15]:
                lines.append(f'  - `{f}`')
        lines.append('')

    # 2. Repository file inventory & disposition
    lines.append('## 2. 仓库文件盘点与处置')
    lines.append('')
    lines.append(
        f'共盘点 **{inv.get("total_files", 0):,}** 个非忽略文件，总计 **{_fmt_bytes(inv.get("total_bytes", 0))}**。'
        '所有文件均具有明确的去向与处置理由。'
    )
    lines.append('')
    by_disp = inv.get('by_disposition') or {}
    bytes_by_disp = inv.get('bytes_by_disposition') or {}
    if by_disp:
        lines.append('| 处置类别 | 说明 | 文件数 | 体积 |')
        lines.append('| --- | --- | ---: | ---: |')
        for disp, cnt in sorted(by_disp.items(), key=lambda kv: (-kv[1], kv[0])):
            label = DISPOSITION_LABELS.get(disp, disp)
            sz = _fmt_bytes(bytes_by_disp.get(disp, 0))
            lines.append(f'| `{disp}` | {label} | {cnt:,} | {sz} |')
        lines.append('')

    checks = inv.get('checks') or {}
    if checks:
        lines.append(
            f'- 配方与书籍清单一致性：`books_recipe_keys_match={checks.get("books_recipe_keys_match")}`；'
            f'缺失分片文件 `{len(checks.get("missing_shard_files") or [])}`；'
            f'缺失分片键 `{len(checks.get("missing_shard_keys") or [])}`；'
            f'缺失站点独立包 `{len(checks.get("missing_site_packages") or [])}`；'
            f'包内计数差异 `{len(checks.get("count_mismatches") or [])}`；'
            f'重复原始记录键 `{len(checks.get("duplicate_raw_records") or [])}`。'
        )
        lines.append('')

    # 3. Reconciliation vs __api/stats.json and __api/books.json
    lines.append('## 3. 输入/输出对账（对比 `__api/stats.json` 与 `__api/books.json`）')
    lines.append('')
    api_stats = recon.get('api_stats') or {}
    observed = recon.get('observed') or {}
    stats_diff = recon.get('stats_diff') or {}
    if api_stats:
        lines.append('### 3.1 `__api/stats.json` 核心实体对账')
        lines.append('')
        lines.append('| 分组 | 类别 | `__api/stats.json` | 本次实测 | 差值 |')
        lines.append('| --- | --- | ---: | ---: | ---: |')
        for scope in ('official', 'brew', 'ours'):
            scope_api = api_stats.get(scope) if isinstance(api_stats.get(scope), dict) else {}
            scope_obs = observed.get(scope) if isinstance(observed.get(scope), dict) else {}
            scope_diff = stats_diff.get(scope) if isinstance(stats_diff.get(scope), dict) else {}
            for kind in sorted(set(scope_api) | set(scope_obs)):
                a_val = scope_api.get(kind, '-')
                o_val = scope_obs.get(kind, '-')
                if kind in scope_diff:
                    d_val = scope_diff[kind]
                elif isinstance(a_val, int) and isinstance(o_val, int):
                    d_val = o_val - a_val
                else:
                    d_val = '-'
                d_str = f'+{d_val}' if isinstance(d_val, int) and d_val > 0 else str(d_val)
                lines.append(f'| `{scope}` | `{kind}` | {a_val} | {o_val} | {d_str} |')
        lines.append('')

    api_books = recon.get('api_books') or {}
    books_diff = recon.get('books_diff') or {}
    if api_books or books_diff:
        lines.append('### 3.2 `__api/books.json` 与书籍/模组对账')
        lines.append('')
        if api_books:
            lines.append(
                f'- `__api/books.json`：共 `{api_books.get("total_packages", api_books.get("total", "-"))}` 个社区扩展包'
                f'（本站精选 `ours={api_books.get("ours_packages", api_books.get("ourBooks", "-"))}`，'
                f'社区扩展 `community={api_books.get("community_packages", api_books.get("books", "-"))}`）。'
            )
        if books_diff:
            for k, v in books_diff.items():
                lines.append(f'- `{k}`：{v}')
        lines.append('')

    # 4. Catalog distribution
    lines.append('## 4. 目录分布统计')
    lines.append('')
    total_entries = cat_counts.get('entries', 0)
    lines.append(
        f'- **条目总数**：**{total_entries:,}**（来源 `{cat_counts.get("sources", 0):,}`，'
        f'内容包 `{cat_counts.get("packages", 0):,}`，关系 `{cat_counts.get("relations", 0):,}`，'
        f'同源叠放群组 `{cat_counts.get("identity_groups", 0):,}`，'
        f'分面索引 `{cat_counts.get("entry_facets", 0):,}` 行）'
    )
    lines.append(
        f'- **规则版本分布**：'
        + '，'.join(f'`{k}` {v:,}' for k, v in (cat.get('by_ruleset') or {}).items())
    )
    lines.append(
        f'- **来源性质分布**：'
        + '，'.join(f'{k} {v:,}' for k, v in (cat.get('by_origin') or {}).items())
    )
    lines.append('')

    lines.append('### 4.1 按条目类型分布')
    lines.append('')
    lines.append('| 类型标识 | 中文名 | 条目数 | 占比 |')
    lines.append('| --- | --- | ---: | ---: |')
    for t, cnt in (cat.get('by_type') or {}).items():
        pct = f'{(cnt / total_entries * 100):.2f}%' if total_entries else '0.00%'
        lines.append(f'| `{t}` | {TYPES.get(t, t)} | {cnt:,} | {pct} |')
    lines.append('')

    top_pkgs = cat.get('top_packages') or []
    if top_pkgs:
        lines.append('### 4.2 主要内容包分布（前 20）')
        lines.append('')
        lines.append('| 内容包 ID | 名称 | 来源性质 | 条目数 | 书籍数 |')
        lines.append('| --- | --- | --- | ---: | ---: |')
        for p in top_pkgs[:20]:
            lines.append(
                f'| `{_md_escape(p["id"])}` | {_md_escape(p["name"])} | {_md_escape(p["origin"])} | '
                f'{p["entries"]:,} | {p["books"]:,} |'
            )
        lines.append('')

    top_srcs = cat.get('top_sources') or []
    if top_srcs:
        lines.append('### 4.3 主要来源分布（前 20）')
        lines.append('')
        lines.append('| 来源 ID | 缩写 | 名称 | 规则版本 | 条目数 |')
        lines.append('| --- | --- | --- | --- | ---: |')
        for s in top_srcs[:20]:
            lines.append(
                f'| `{_md_escape(s["id"])}` | `{_md_escape(s["short_name"])}` | {_md_escape(s["name"])} | '
                f'`{_md_escape(s["ruleset"])}` | {s["entries"]:,} |'
            )
        lines.append('')

    # 5. Semantic resolution & quality diagnostics
    lines.append('## 5. 语义解析与字段质量诊断')
    lines.append('')
    lines.append('### 5.1 `_copy` 解析状态')
    lines.append('')
    lines.append('| 解析状态 | 说明 | 条目数 |')
    lines.append('| --- | --- | ---: |')
    for st, cnt in (cat.get('by_copy_status') or {}).items():
        lines.append(f'| `{st}` | {COPY_STATUS_LABELS.get(st, st)} | {cnt:,} |')
    lines.append('')

    copy_res = recon.get('copy_resolution') or {}
    variants = recon.get('variants') or {}
    subraces = recon.get('subraces') or {}
    fluff = recon.get('fluff') or {}
    lines.append(
        f'- **`_copy` 诊断**：缺失基类 `{copy_res.get("missing_base", "-")}`，'
        f'基类歧义 `{copy_res.get("ambiguous_base", "-")}`，'
        f'循环引用 `{copy_res.get("cycles", "-")}`，'
        f'修改指令告警 `{copy_res.get("mod_warnings", "-")}`。'
    )
    if variants:
        lines.append(f'- **变体展开 (`_versions` / `magicvariant`)**：{json.dumps(variants, ensure_ascii=False)}')
    if subraces:
        lines.append(f'- **亚种合并与继承 (`subrace`)**：{json.dumps(subraces, ensure_ascii=False)}')
    if fluff:
        orphan_count = fluff.get('orphan_count', len(fluff.get('orphans') or []) if isinstance(fluff, dict) else 0)
        lines.append(f'- **背景与插画关联 (`*Fluff`)**：孤立 Fluff `{orphan_count}` 条。')
    lines.append(
        f'- **字段冲突**：共 `{cat.get("conflict_entries", 0)}` 条目存在字段冲突。'
    )
    unknown_bools = cat.get('unknown_booleans') or {}
    if unknown_bools:
        lines.append(
            '- **未提取布尔字段计数（均保持 `__unknown__`，无强制转 `false`）**：'
            + '，'.join(f'{LABELS.get(k, k)}(`{k}`) {v:,}' for k, v in unknown_bools.items())
        )
    lines.append('')

    non_ok_samples = cat.get('copy_non_ok_samples') or []
    if non_ok_samples:
        lines.append('#### 非 `ok` `_copy` 样例（最多 15 条）')
        lines.append('')
        lines.append('| 条目 ID | 名称 | 类型 | 来源 | 状态 |')
        lines.append('| --- | --- | --- | --- | --- |')
        for s in non_ok_samples[:15]:
            lines.append(
                f'| `{s["id"]}` | {_md_escape(s["name"])} | `{s["type"]}` | `{s["source_id"]}` | `{s["copy_status"]}` |'
            )
        lines.append('')

    # 6. Rendering, links & media diagnostics
    lines.append('## 6. 渲染、交叉链接与媒体诊断')
    lines.append('')
    links = recon.get('links') or {}
    if links:
        res_n = links.get('resolved', 0)
        amb_n = links.get('ambiguous', 0)
        unres_n = links.get('unresolved', 0)
        tot_n = links.get('total', res_n + amb_n + unres_n)
        rate = f'{(res_n / tot_n * 100):.2f}%' if tot_n else '0.00%'
        lines.append(
            f'- **`{{@...}}` 链接统计**：总计 **{tot_n:,}**，已解析 **{res_n:,}**（{rate}），'
            f'歧义 **{amb_n:,}**，未解析 **{unres_n:,}**。'
        )
        top_unres = links.get('top_unresolved') or []
        if top_unres:
            lines.append('- **高频未解析链接样例（前 15）**：')
            for item in top_unres[:15]:
                if isinstance(item, dict):
                    lines.append(f'  - `{item.get("raw") or item.get("tag")}` × {item.get("count", 1)}')
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    lines.append(f'  - `{item[0]}` × {item[1]}')
                else:
                    lines.append(f'  - `{item}`')
        top_amb = links.get('top_ambiguous') or []
        if top_amb:
            lines.append('- **高频歧义链接样例（前 15）**：')
            for item in top_amb[:15]:
                if isinstance(item, dict):
                    lines.append(f'  - `{item.get("raw") or item.get("tag")}` × {item.get("count", 1)}')
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    lines.append(f'  - `{item[0]}` × {item[1]}')
                else:
                    lines.append(f'  - `{item}`')

    unk_tags = recon.get('unknown_tags') or {}
    unsup_blocks = recon.get('unsupported_blocks') or {}
    lines.append(f'- **未知标签 (`unknown_tags`)**：{len(unk_tags)} 种' + (f'（{unk_tags}）' if unk_tags else '（无）'))
    lines.append(
        f'- **未支持块类型 (`unsupported_blocks`)**：{len(unsup_blocks)} 种'
        + (f'（{unsup_blocks}）' if unsup_blocks else '（无）')
    )

    media = recon.get('media') or {}
    media_bundle = report.get('media_bundle') or {}
    if media:
        loc_m = media.get('local')
        rem_m = media.get('remote')
        miss_m = media.get('missing_local')
        loc_cnt = len(loc_m) if isinstance(loc_m, list) else loc_m
        rem_cnt = len(rem_m) if isinstance(rem_m, list) else rem_m
        miss_cnt = len(miss_m) if isinstance(miss_m, list) else miss_m
        lines.append(
            f'- **媒体资源分离**：本地已打包引用 `{loc_cnt}`，未打包远程引用 `{rem_cnt}`，'
            f'声明缺失本地媒体 `{miss_cnt}`；索引媒体分类 `{media.get("indexed_media_counts", {})}`。'
        )
    if media_bundle:
        lines.append(
            f'- **自包含媒体包 (`media/`)**：共 **{media_bundle.get("bundled_count", 0):,}** 个文件'
            f'（{_fmt_bytes(int(media_bundle.get("bundled_bytes", 0)))}，含已缓存远端资源 '
            f'{media_bundle.get("cached_remote_count", 0):,} 个），懒加载白名单键 `{media_bundle.get("remote_keys", 0):,}`。'
        )
    lines.append('')

    # 7. Reader & book catalog
    lines.append('## 7. 阅读器与书籍目录核对')
    lines.append('')
    mr = reader.get('manifest_reader') or {}
    lines.append(
        f'- **索引版本**：`schema={reader.get("schema")}`；'
        f'TOC 节点 **{reader.get("nodes_count", 0):,}**；'
        f'章节 HTML 页面 **{reader.get("pages_count", 0):,}**；'
        f'条目正文定位 **{reader.get("entry_locations_count", 0):,}**；'
        f'全文搜索索引 **{cat_counts.get("book_search", 0):,}** 行。'
    )
    if mr:
        lines.append(
            f'- **书籍构建摘要**：已绑定定义 `{mr.get("bound_definitions", "-")}`，'
            f'已绑定引用 `{mr.get("bound_references", "-")}`，'
            f'DOM 绑定失配 `{mr.get("binding_mismatches", 0)}`，'
            f'地图 `{mr.get("maps_rendered", "-")}`，空书 `{mr.get("empty_books", 0)}`。'
        )
        for note in (mr.get('notes') or [])[:10]:
            lines.append(f'  - 备注：{note}')
        for warn in (mr.get('warnings') or [])[:10]:
            lines.append(f'  - 告警：{warn}')
    lines.append('')

    # 8. Source attribution and media notices
    lines.append('## 8. 来源署名与资源说明')
    lines.append('')
    attr_pkgs = attr.get('packages') or []
    attr_srcs = attr.get('sources') or []
    resource_notes = attr.get('resource_notes', attr.get('non_redistributable')) or []
    lines.append(
        f'- **署名登记**：内容包 `{len(attr_pkgs)}` 项，独立来源 `{len(attr_srcs)}` 项。'
    )
    if isinstance(resource_notes, list) and resource_notes:
        lines.append(f'- **资源说明**（共 `{len(resource_notes)}` 项）')
        for item in resource_notes[:30]:
            if isinstance(item, dict):
                lines.append(
                    f'  - `{item.get("path") or item.get("kind") or item.get("category") or item.get("name", "")}`：'
                    f'{item.get("reason") or item.get("note") or item.get("license", "")}'
                )
            else:
                lines.append(f'  - {item}')
    elif isinstance(resource_notes, dict) and resource_notes:
        lines.append(f'- **资源说明**：{json.dumps(resource_notes, ensure_ascii=False)}')
    else:
        lines.append('- **资源说明**：未在 `attribution.json` 中登记额外项。')
    lines.append('')

    return '\n'.join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m anna.our5etools.audit',
        description='核查已构建的 our5etools 数据集不变量并生成中文审计报告。',
    )
    parser.add_argument(
        '--our5etools-data',
        type=Path,
        default=OUR5ETOOLS_DATA,
        help=f'已构建的 our5etools 数据目录（默认：{OUR5ETOOLS_DATA}）',
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=None,
        help='输出 Markdown 审计报告路径（默认：<our5etools-data>/audit.md）',
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='同时输出 JSON 审计报告到标准输出与同目录 .json 文件',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = Path(args.our5etools_data).expanduser().resolve()
    out_path = Path(args.output).expanduser().resolve() if args.output else (data_dir / 'audit.md')

    report = audit_data(data_dir)
    md = render_markdown(report)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding='utf-8')

    if args.json:
        json_path = out_path.with_suffix('.json')
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        json_path.write_text(payload + '\n', encoding='utf-8')
        print(payload)
    else:
        passed = sum(1 for i in report['invariants'] if i['ok'])
        total = len(report['invariants'])
        state = 'PASS' if report['ok'] else 'FAIL'
        print(f'[{state}] our5etools audit: {passed}/{total} invariants passed -> {out_path}')

    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
