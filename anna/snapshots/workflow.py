#!/usr/bin/env python3
"""Preserve accumulated data and audit migration without changing extraction semantics."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import json
import os
from pathlib import Path
import tempfile

from anna.snapshots.baseline import (activate, active, capture, connect, fingerprint,
                                metadata, quote, tables, verify, materialize, decisions_path, export_snapshot)
from anna.paths import PROJECT, WEB, FIVETOOLS
from anna.serialization import canonical, digest
from anna.review.store import DEFAULT_DECISIONS, lifecycle, load_events
from anna.snapshots.correspondence import Correspondence, UnmappedEvidence, records


def write_jsonl(path, rows):
    with Path(path).open('w') as stream:
        for row in rows:
            stream.write(canonical(row) + '\n')


def differences(before, after):
    """Bounded diagnostics: values stay in the saved datasets, not duplicated in reports."""
    from anna.catalog.references import basis_changes
    return [{'path': r['path'], 'kind': r['kind'], 'before_sha256': digest(r.get('before')),
             'after_sha256': digest(r.get('after'))} for r in basis_changes(before, after)]


def provenance_only(table, row, newer):
    if table != 'metadata' or row['key'] != 'summary':
        return False
    ignored = {'runtime_seconds', 'pipeline_sha256', 'extraction_sha256'}
    old, new = json.loads(row['value']), json.loads(newer['value'])
    return {k: v for k, v in old.items() if k not in ignored} == {k: v for k, v in new.items() if k not in ignored}


def audit_assets(before, after, baseline_path, output):
    """Compare every stored row, including unknown tables and unresolved observations.

    Exact row accounting is deliberately independent of semantic correspondence. A
    matching anchor must never hide a dropped fact, changed status or missing table.
    """
    old_tables, new_tables = tables(before), tables(after)
    result = {}
    after.execute('ATTACH DATABASE ? AS previous', (Path(baseline_path).resolve().as_uri() + '?mode=ro',))
    with (output / 'asset_changes.jsonl').open('w') as stream:
        for name in sorted(old_tables.keys() | new_tables.keys()):
            old, new = old_tables.get(name), new_tables.get(name)
            stats = {'before': old['rows'] if old else 0, 'after': new['rows'] if new else 0,
                     'unchanged': 0, 'provenance_only': 0, 'changed': 0, 'missing': 0, 'added': 0}
            result[name] = stats
            if old is None or new is None or old['columns'] != new['columns'] or old['key'] != new['key'] or not old['key']:
                stats.update(status='schema_changed', missing=stats['before'], added=stats['after'])
                stream.write(canonical({'table': name, 'status': 'schema_changed', 'before': old, 'after': new}) + '\n')
                continue
            stats['status'] = 'compared'
            if old['sql'] != new['sql']:
                stats['status'] = 'schema_changed'
            cols, keys = old['columns'], old['key']
            join = ' AND '.join('a.' + quote(k) + ' IS b.' + quote(k) for k in keys)
            distinct = ' OR '.join('a.' + quote(k) + ' IS NOT b.' + quote(k) for k in cols)
            sql = f'SELECT a.*, b.* FROM previous.{quote(name)} a LEFT JOIN main.{quote(name)} b ON {join} WHERE b.{quote(keys[0])} IS NULL OR ({distinct})'
            for raw in after.execute(sql):
                old_row = dict(zip(cols, raw[:len(cols)]))
                new_row = dict(zip(cols, raw[len(cols):]))
                missing = new_row[keys[0]] is None
                state = 'missing' if missing else 'provenance_only' if provenance_only(name, old_row, new_row) else 'changed'
                stats[state] += 1
                stream.write(canonical({'table': name, 'key': {k: old_row[k] for k in keys}, 'status': state,
                                        'before_sha256': digest(old_row), 'after_sha256': None if missing else digest(new_row),
                                        'changed_columns': [] if missing else [k for k in cols if old_row[k] != new_row[k]]}) + '\n')
            sql = f'SELECT b.* FROM main.{quote(name)} b LEFT JOIN previous.{quote(name)} a ON {join} WHERE a.{quote(keys[0])} IS NULL'
            for raw in after.execute(sql):
                row = dict(zip(cols, raw))
                stats['added'] += 1
                stream.write(canonical({'table': name, 'key': {k: row[k] for k in keys}, 'status': 'added', 'after_sha256': digest(row)}) + '\n')
            stats['unchanged'] = stats['before'] - stats['changed'] - stats['missing'] - stats['provenance_only']
            if stats['unchanged'] + stats['provenance_only'] + stats['changed'] + stats['added'] != stats['after']:
                raise ValueError('Asset accounting failed: ' + name)
    # Keep the attached baseline in this read transaction until the caller closes it.
    old_views = dict(before.execute("SELECT name,sql FROM sqlite_master WHERE type='view'"))
    new_views = dict(after.execute("SELECT name,sql FROM sqlite_master WHERE type='view'"))
    return result, differences(old_views, new_views)


def table_dependencies(context, entry):
    return sorted((t for t in context.tables if t['page_path'] == entry['page_path']
                   and t['start'] < entry['end'] and entry['start'] < t['end']),
                  key=lambda t: (t['page_path'], t['start'], t['end'], t['id']))


def knowledge_dependencies(before, after, alignment):
    from anna.knowledge.repository import Context
    old_context, new_context = Context(before), Context(after)
    old_records, new_records = records(before, 'rule_knowledge'), records(after, 'rule_knowledge')
    by_subject = defaultdict(list)
    for r in new_records.values():
        by_subject[(r['subject_id'], r['kind'])].append(r)
    matches = {}
    for rid, r in old_records.items():
        candidates = by_subject.get((alignment.entry_map.get(r['subject_id']), r['kind']), [])
        if len(candidates) == 1:
            current = candidates[0]
            matches[rid] = current
            alignment.ids[rid] = current['id']
    # Table identities also depend on physical position. Map by a proven range;
    # structure/content are still retained for the separate dependency comparison.
    table_ranges = defaultdict(list)
    for t in new_context.tables:
        table_ranges[(t['page_path'], t['start'], t['end'])].append(t)
    old_ranges = Counter((t['page_path'], t['start'], t['end']) for t in old_context.tables)
    for table in old_context.tables:
        found = alignment.span(table['page_path'], table['start'], table['end'])['after']
        if found and old_ranges[(table['page_path'], table['start'], table['end'])] == 1:
            targets = table_ranges.get((found['path'], *found['span']), [])
            if len(targets) == 1:
                alignment.ids[table['id']] = targets[0]['id']
    for rid, record in sorted(old_records.items()):
        target = matches.get(rid)
        row = {'before': rid, 'after': target['id'] if target else None, 'subject_id': record['subject_id'],
               'status': 'missing', 'changes': []}
        if not target:
            row['reason'] = 'subject_or_record_missing_or_ambiguous'
            yield row
            continue
        old_entry, new_entry = old_context.entries[record['subject_id']], new_context.entries[target['subject_id']]
        old_basis = {'declared': old_context.basis(record),
                     'enclosing_sources': alignment.context_dependencies(record['subject_id']),
                     'tables': table_dependencies(old_context, old_entry)}
        new_basis = {'declared': new_context.basis(target),
                     'enclosing_sources': alignment.context_dependencies(target['subject_id'], False),
                     'tables': table_dependencies(new_context, new_entry)}
        row.update(before_basis_sha256=digest(old_basis), after_basis_sha256=digest(new_basis))
        if old_basis == new_basis:
            row.update(status='unchanged', reason='dependencies_equal')
        else:
            try:
                transported = alignment.transport(old_basis, old_entry['page_path'])
                row['transported_basis_sha256'] = digest(transported)
                row['changes'] = differences(transported, new_basis)
                row.update(status='relocated' if not row['changes'] else 'needs_review',
                           reason='dependencies_equal_after_relocation' if not row['changes'] else 'dependencies_changed')
            except UnmappedEvidence as exc:
                row.update(status='needs_review', reason=str(exc), changes=differences(old_basis, new_basis))
        yield row


def review_results(db):
    result = {}
    for name in ('review_decisions', 'knowledge_decisions'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone():
            for raw, in db.execute('SELECT data FROM ' + name):
                value = json.loads(raw)
                result[value['event']['id']] = value
    return result


def review_dependencies(before, after, alignment, saved_log, current_log, knowledge_checks=None):
    from anna.knowledge.repository import Context
    from anna.catalog.references import ReviewContext
    from anna.review.store import KNOWLEDGE_OPERATIONS
    old_events, _ = load_events(saved_log)
    events, _ = load_events(current_log)
    by_id = {e['id']: e for e in events}
    states = lifecycle(events)
    previous, current = review_results(before), review_results(after)
    if knowledge_checks is None:
        knowledge_checks = list(knowledge_dependencies(before, after, alignment)) if any(e['operation'] in KNOWLEDGE_OPERATIONS for e in old_events) else []
    checked_knowledge = {r['before']: r for r in knowledge_checks}
    reference_context = knowledge_context = None
    for event in old_events:
        eid = event['id']
        row = {'decision_id': eid, 'before_status': previous.get(eid, {}).get('status', 'pending'),
               'after_status': current.get(eid, {}).get('status', 'pending'), 'status': 'needs_review', 'changes': []}
        if by_id.get(eid) != event:
            row['reason'] = 'historical_event_missing_or_changed'
        elif event['operation'] == 'revoke' or states.get(eid) in {'revoked', 'superseded'}:
            row.update(status='history_retained', reason='revocation_recorded' if event['operation'] == 'revoke' else states[eid])
        elif row['before_status'] != 'applied':
            # Reanchoring must not turn an old stale/ambiguous/pending decision into approval.
            row.update(status='history_retained' if row['after_status'] == row['before_status'] else 'needs_review',
                       reason='no_valid_baseline_approval')
        else:
            try:
                binding = event['binding']
                if event['operation'] in KNOWLEDGE_OPERATIONS:
                    check = checked_knowledge.get(event['binding']['knowledge_id'])
                    if not check or check['status'] not in {'unchanged', 'relocated'}:
                        raise UnmappedEvidence('knowledge_dependencies_require_review')
                    knowledge_context = knowledge_context or Context(after)
                    rid = alignment.ids.get(event['binding']['knowledge_id'], event['binding']['knowledge_id'])
                    found = after.execute('SELECT data FROM rule_knowledge WHERE id=?', (rid,)).fetchone()
                    if not found:
                        raise UnmappedEvidence('knowledge_unmapped')
                    basis = knowledge_context.basis(json.loads(found[0]))
                    row['target_record'] = rid
                else:
                    reference_context = reference_context or ReviewContext(after)
                    found = alignment.span(binding['path'], binding['start'], binding['end'])['after']
                    if not found:
                        raise UnmappedEvidence('reference_range_unmapped')
                    refs = [json.loads(r[0]) for r in after.execute(
                        'SELECT data FROM effective_references WHERE page_path=? AND start=? AND end=? AND signal=?',
                        (found['path'], *found['span'], binding['signal']))]
                    refs = [r for r in refs if r['kind'] == binding['kind'] and r['role'] == binding['role']]
                    if len(refs) != 1:
                        raise UnmappedEvidence('reference_missing_or_ambiguous')
                    ref = refs[0]
                    alignment.ids[binding['reference_id']] = ref['id']
                    target = alignment.ids.get(event.get('target_id'), event.get('target_id'))
                    basis = reference_context.basis(ref, target, search_query=event.get('search_query', '')
                                                    if event['operation'] == 'missing_definition' else None)
                    row['target_record'] = ref['id']
                transported = alignment.transport(event['basis'], binding['path'])
                row['changes'] = differences(transported, basis)
                row.update(status='eligible' if not row['changes'] else 'needs_review',
                           reason='dependencies_equal' if not row['changes'] else 'dependencies_changed')
            except UnmappedEvidence as exc:
                row['reason'] = str(exc)
        yield row
    for event in events:
        if event['id'] not in {e['id'] for e in old_events}:
            yield {'decision_id': event['id'], 'status': 'added', 'reason': 'new_review_event',
                   'after_status': current.get(event['id'], {}).get('status', 'pending'), 'changes': []}


def _report(database, baseline, output, decisions):
    baseline, database, output = map(lambda p: Path(p).resolve(), (baseline, database, output))
    if output == baseline or output.is_relative_to(baseline) or baseline.is_relative_to(output):
        raise ValueError('Migration report must not overwrite a baseline bundle')
    manifest = verify(baseline)
    output.mkdir(parents=True, exist_ok=True)
    database_hash = fingerprint(database)
    log_hash = load_events(decisions)[1]
    baseline_database = materialize(baseline)
    with closing(connect(baseline_database)) as before, closing(connect(database)) as after:
        # Stable read snapshots even if another process publishes a new database file.
        before.execute('BEGIN'); after.execute('BEGIN')
        summary = metadata(after, 'summary')
        if not summary or not summary.get('full_scan'):
            raise ValueError('Migration acceptance requires a full dataset')
        assets, views = audit_assets(before, after, baseline_database, output)
        alignment = Correspondence(before, after)
        write_jsonl(output / 'source_pages.jsonl', alignment.page_results)
        write_jsonl(output / 'source_entries.jsonl', alignment.entry_results)
        knowledge = list(knowledge_dependencies(before, after, alignment))
        write_jsonl(output / 'knowledge_dependencies.jsonl', knowledge)
        reviews = list(review_dependencies(before, after, alignment, decisions_path(baseline), decisions, knowledge))
        write_jsonl(output / 'review_dependencies.jsonl', reviews)
        write_jsonl(output / 'evidence_ranges.jsonl', alignment.span_cache.values())
        changes = sum(r['changed'] + r['missing'] + r['added'] for r in assets.values())
        counts = lambda rows: dict(sorted(Counter(r['status'] for r in rows).items()))
        complete = not changes and not views and all(s['status'] == 'compared' for s in assets.values())
        complete &= all(r['status'] in {'unchanged', 'relocated'} for r in knowledge)
        complete &= all(r['status'] in {'eligible', 'history_retained'} for r in reviews)
        complete &= all(r['after'] is not None for r in alignment.page_results + alignment.entry_results)
        result = {'schema': 1, 'baseline_id': manifest['id'], 'baseline': str(baseline),
                  'database_sha256': database_hash, 'source_snapshot': summary.get('input_manifest_sha256'),
                  'status': 'preserved' if complete else 'needs_review', 'mode': 'preview', 'approvals_applied': False,
                  'assets': assets, 'view_changes': views, 'pages': counts(alignment.page_results), 'entries': counts(alignment.entry_results),
                  'knowledge_dependencies': counts(knowledge), 'review_dependencies': counts(reviews),
                  'evidence_ranges': counts(list(alignment.span_cache.values())),
                  'review_log_sha256': log_hash,
                  'scope': 'all_baseline_tables_and_review_events',
                  'limits': ['no_automatic_identity_merge', 'no_approval_migration', 'unchanged_extraction_semantics',
                             'conservative_enclosing_scope_dependencies', 'split_merge_and_unmatched_locations_require_review']}
    if fingerprint(database) != database_hash or load_events(decisions)[1] != log_hash:
        raise ValueError('Database or review log changed during migration audit; no report published')
    (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    lines = ['# 整理基准迁移核对', '', f"基准：`{manifest['id']}`。结果：`{result['status']}`。", '',
             '本报告覆盖基准中的全部表和审核事件。来源对应只表示位置关系；依赖核验是迁移预览，不改写实体身份、人工日志或有效结果。', '',
             '| 数据表 | 原有 | 原样保留 | 构建来源信息变化 | 内容变化 | 未找到 | 新增 |', '|---|---:|---:|---:|---:|---:|---:|']
    for name, stats in assets.items():
        lines.append('| ' + name + ' | ' + ' | '.join(str(stats[k]) for k in ('before', 'unchanged', 'provenance_only', 'changed', 'missing', 'added')) + ' |')
    lines += ['', '## 来源与依赖', '']
    for label, field in [('页面对应', 'pages'), ('条目范围对应', 'entries'), ('知识依赖', 'knowledge_dependencies'), ('审核历史与依赖', 'review_dependencies')]:
        lines.append(f'- {label}：`{canonical(result[field])}`。')
    lines += ['', '逐项去向见 `source_pages.jsonl`、`source_entries.jsonl`；具体差异见 `asset_changes.jsonl`；',
              '依赖变化路径见 `knowledge_dependencies.jsonl` 与 `review_dependencies.jsonl`，检查过的证据范围见 `evidence_ranges.jsonl`。', '',
              '未找到不等于已删除，候选对应不等于批准。整段或上级范围仍是现有依赖时，变化保守地进入待核；本阶段不推断更细的规则作用域。',
              '已有待核、否决与撤回也是基准的一部分。新输出不会自动替换基准；差异经核对后才能显式保存下一份基准。', '']
    (output / 'report.md').write_text('\n'.join(lines))
    return result


def report(database, baseline, output, decisions=DEFAULT_DECISIONS):
    """Finish the audit before replacing any previous generated report files."""
    database, baseline, output = (Path(p).resolve() for p in (database, baseline, output))
    if output == database.parent or output == baseline or output.is_relative_to(baseline) or baseline.is_relative_to(output):
        raise ValueError('Report output must be separate from the dataset and baseline bundle')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.migration-', dir=output.parent) as tmp:
        result = _report(database, baseline, Path(tmp), decisions)
        output.mkdir(parents=True, exist_ok=True)
        # Summary is the completion marker. Leave unrelated user files untouched.
        (output / 'summary.json').unlink(missing_ok=True)
        for path in sorted(Path(tmp).iterdir(), key=lambda p: (p.name == 'summary.json', p.name)):
            os.replace(path, output / path.name)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest='command', required=True)
    save = commands.add_parser('capture', help='save a new immutable full baseline; never overwrite')
    save.add_argument('--database', type=Path, default=PROJECT / 'data/extracted/catalog.sqlite')
    save.add_argument('--source', type=Path, default=WEB)
    save.add_argument('--decisions', type=Path, default=DEFAULT_DECISIONS)
    save.add_argument('--5etools', dest='counterparts', type=Path, default=FIVETOOLS)
    save.add_argument('--output', type=Path, required=True)
    check = commands.add_parser('verify', help='verify every saved artifact against its manifest')
    check.add_argument('baseline', type=Path)
    select = commands.add_parser('activate', help='explicitly change the active baseline; does not remove history')
    select.add_argument('baseline', type=Path)
    select.add_argument('--reviewer', required=True)
    select.add_argument('--reason', required=True)
    select.add_argument('--bootstrap', action='store_true')
    restore = commands.add_parser('materialize', help='rebuild or verify a disposable query cache')
    restore.add_argument('baseline', type=Path)
    export = commands.add_parser('export', help='regenerate exports or restore frozen source inputs')
    export.add_argument('baseline', type=Path)
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--group', choices=['exports', 'source_files', 'counterpart_files'], default='exports')
    assessment = commands.add_parser('assess', help='compare immutable candidate with its parent')
    assessment.add_argument('baseline', type=Path)
    assessment.add_argument('--output', type=Path, required=True)
    disposition = commands.add_parser('dispose', help='record evidence-backed disposition; does not approve knowledge')
    disposition.add_argument('baseline', type=Path)
    disposition.add_argument('--issue', action='append', required=True)
    disposition.add_argument('--action', choices=['accept_change', 'retain_unresolved'], required=True)
    disposition.add_argument('--reviewer', required=True)
    disposition.add_argument('--reason', required=True)
    disposition.add_argument('--evidence', action='append', required=True)
    compare = commands.add_parser('compare', help='audit full accumulated data, source correspondence and dependencies')
    compare.add_argument('--baseline', type=Path)
    compare.add_argument('--database', type=Path, default=PROJECT / 'data/extracted/catalog.sqlite')
    compare.add_argument('--decisions', type=Path, default=DEFAULT_DECISIONS)
    compare.add_argument('--output', type=Path, default=PROJECT / 'data/extracted/migration')
    args = ap.parse_args()
    if args.command == 'capture':
        result = capture(args.database, args.output, args.source, args.decisions, args.counterparts)
        print(canonical({'baseline_id': result['id'], 'path': str(args.output.resolve())}))
    elif args.command == 'verify':
        print(canonical({'baseline_id': verify(args.baseline)['id'], 'status': 'verified'}))
    elif args.command == 'activate':
        print(canonical(activate(args.baseline, reviewer=args.reviewer, reason=args.reason, bootstrap=args.bootstrap)))
    elif args.command == 'materialize':
        print(canonical({'database': str(materialize(args.baseline))}))
    elif args.command == 'export':
        print(canonical(export_snapshot(args.baseline, args.output, args.group)))
    elif args.command == 'assess':
        from anna.snapshots.gate import assess, dispositions
        result = assess(args.baseline, args.output)
        print(canonical({'assessment_id': result['id'], 'issues': len(result['issues']), 'remaining': len(dispositions(args.baseline, result))}))
    elif args.command == 'dispose':
        from anna.snapshots.gate import dispose
        print(canonical(dispose(args.baseline, args.issue, args.action, args.reviewer, args.reason, args.evidence)))
    else:
        baseline = args.baseline or active()
        if not baseline:
            ap.error('No baseline selected; capture one explicitly first')
        result = report(args.database, baseline, args.output, args.decisions)
        print(canonical({'status': result['status'], 'baseline_id': result['baseline_id'], 'report': str(args.output / 'report.md')}))
        if result['status'] != 'preserved':
            raise SystemExit(2)


if __name__ == '__main__':
    main()
