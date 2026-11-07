#!/usr/bin/env python3
"""Inspect, save and revoke scoped reference decisions; run make build to publish them."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

from paths import PROJECT
from review_search import search_candidates
from references import ReviewContext, query_decisions, query_references, query_missing_definitions
from review_store import DEFAULT_DECISIONS, append_event, append_events, digest, lifecycle, load_events, locked, require_appendable, input_snapshot


def read_reference(db, reference_id):
    row = db.execute('SELECT data FROM effective_references WHERE id=?', (reference_id,)).fetchone()
    if not row: raise ValueError('引用不存在，请重新查询有效引用 ID')
    return json.loads(row[0])


def inspect(db, reference_id, target=None, *, context=None, path=DEFAULT_DECISIONS, missing_definition=False, query=''):
    ref = read_reference(db, reference_id)
    context = context or ReviewContext(db)
    if target and target not in context.entries: raise ValueError('目标条目不存在')
    if missing_definition and target: raise ValueError('缺失定义核对不能指定目标')
    basis = context.basis(ref, target, search_query=query if missing_definition else None)
    page = context.pages[ref['path']]['text']
    candidates = [context.entries[i] for i in ref['automatic']['candidates'] if i in context.entries]
    selected = context.entries.get(target)
    target_text = context.pages[selected['page_path']]['text'][selected['start']:selected['end']] if selected else None
    return {'reference': ref, 'excerpt': page[max(0, ref['start']-160):ref['end']+160],
            'target': selected, 'target_text': target_text, 'candidates': candidates,
            **({'candidate_search':search_candidates(db,reference_id,query=query,context=context)} if missing_definition else {}),
            'basis': basis, 'basis_sha256': digest(basis),
            'snapshot_sha256': input_snapshot(db), 'log_sha256': load_events(path)[1]}


def save(db, path, *, operation, reference_id, target, expected_basis, reviewer, reason, supersedes=None, search_query=''):
    return save_batch(db, path, [{'operation': operation, 'reference_id': reference_id, 'target': target,
        'expected_basis': expected_basis, 'reviewer': reviewer, 'reason': reason, 'supersedes': supersedes, 'search_query': search_query}])[0]


def save_batch(db, path, items, *, expected_snapshot=None, expected_log=None):
    if not isinstance(items, list) or not items: raise ValueError('批次必须是非空审核列表')
    with locked(path):
        require_appendable(db, path)
        if expected_snapshot is not None and expected_snapshot != input_snapshot(db):
            raise ValueError('构建快照已变化，请重新 inspect 整个批次')
        events, log_hash = load_events(path)
        if expected_log is not None and expected_log != log_hash:
            raise ValueError('审核日志已变化，请重新核对待保存批次')
        states = lifecycle(events)
        context = ReviewContext(db)
        pending = []; seen = set()
        for item in items:
            if not isinstance(item, dict): raise ValueError('每条审核必须是对象')
            operation = item.get('operation'); reference_id = item.get('reference_id'); target = item.get('target')
            reviewer = item.get('reviewer'); reason = item.get('reason'); supersedes = item.get('supersedes')
            if operation not in {'confirm', 'non_reference', 'missing_definition'}: raise ValueError('不支持的审核操作')
            if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reason, str) or not reason.strip():
                raise ValueError('审核者和依据不能为空')
            if not isinstance(reference_id, str) or reference_id in seen: raise ValueError('批次引用缺失或重复')
            if target is not None and not isinstance(target, str): raise ValueError('目标 ID 必须是字符串')
            seen.add(reference_id)
            view = inspect(db, reference_id, target, context=context, path=path,
                           missing_definition=operation=='missing_definition', query=item.get('search_query',''))
            if view['basis_sha256'] != item.get('expected_basis'): raise ValueError('审核依据已变化，请重新 inspect：'+reference_id)
            ref = view['reference']
            if not view['basis']['binding']['text'].strip() or ref['presentation'] == 'definition_title':
                raise ValueError('此处是空范围或定义入口，不能作为正文引用审核')
            if operation == 'confirm' and not view['basis']['target_locatable']:
                raise ValueError('确认引用需要可定位的定义条目')
            if operation in {'non_reference','missing_definition'} and target is not None: raise ValueError('非引用或缺失定义决策不能有目标')
            active = [e['id'] for e in events if states.get(e['id']) == 'active' and e['binding']['reference_id'] == reference_id]
            if active != ([supersedes] if supersedes else []):
                raise ValueError('此引用已有有效或待核决策；用 supersedes 替代唯一决策，冲突时先逐一撤销')
            pending.append({'operation': operation, 'reviewer': reviewer.strip(), 'reason': reason.strip(),
                'binding': view['basis']['binding'], 'basis': view['basis'], 'basis_sha256': view['basis_sha256'],
                'reviewed_snapshot': {'automatic': ref['automatic'], 'evidence': ref['evidence'],
                                     'target': view['target'], 'target_text': view['target_text']},
                'target_id': target, 'supersedes': supersedes,
                **({'search_query':item.get('search_query',''), 'candidate_search':view['candidate_search']} if operation=='missing_definition' else {})})
        # A single durable replace: any invalid item leaves the log completely unchanged.
        return append_events(path, pending, log_hash)


def revoke(path, decision_id, reviewer, reason):
    if not reviewer.strip() or not reason.strip(): raise ValueError('审核者和依据不能为空')
    with locked(path):
        events, log_hash = load_events(path)
        if lifecycle(events).get(decision_id) != 'active': raise ValueError('只能撤销尚未撤销或替代的决策')
        return append_event(path, {'operation': 'revoke', 'decision_id': decision_id,
                                  'reviewer': reviewer.strip(), 'reason': reason.strip()}, log_hash)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=PROJECT/'data/extracted/catalog.sqlite')
    parser.add_argument('--decisions', type=Path, default=DEFAULT_DECISIONS)
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list', help='列出当前构建的有效引用')
    for flag in ('path', 'status', 'signal', 'review-status'): listing.add_argument('--'+flag, default='')
    listing.add_argument('--limit', type=int, default=30); listing.add_argument('--offset', type=int, default=0)
    decisions = commands.add_parser('decisions', help='列出重放结果，包括失效、消失、冲突和撤销历史')
    decisions.add_argument('--status', default=''); decisions.add_argument('--limit', type=int, default=30)
    decisions.add_argument('--offset', type=int, default=0)
    viewing = commands.add_parser('inspect', help='核对原文、目标和保存所需依据指纹')
    viewing.add_argument('reference'); viewing.add_argument('--target')
    viewing.add_argument('--missing-definition', action='store_true'); viewing.add_argument('--query', default='')
    candidates = commands.add_parser('candidates', help='只读搜索待核目标；按匹配证据排序，不自动批准')
    candidates.add_argument('reference'); candidates.add_argument('--query',default='')
    candidates.add_argument('--type',default=''); candidates.add_argument('--ruleset',default=''); candidates.add_argument('--source',default='')
    candidates.add_argument('--limit',type=int,default=20); candidates.add_argument('--offset',type=int,default=0)
    missing = commands.add_parser('missing', help='查看可追踪的缺失定义队列及处理历史')
    missing.add_argument('--status',default=''); missing.add_argument('--path',default='')
    missing.add_argument('--limit',type=int,default=30); missing.add_argument('--offset',type=int,default=0)
    for name in ('confirm', 'non-reference', 'missing-definition'):
        cmd = commands.add_parser(name)
        cmd.add_argument('reference'); cmd.add_argument('--basis', required=True)
        if name == 'confirm': cmd.add_argument('--target', required=True)
        if name == 'missing-definition': cmd.add_argument('--query',default='')
        cmd.add_argument('--supersedes'); cmd.add_argument('--reviewer', required=True); cmd.add_argument('--reason', required=True)
    batch = commands.add_parser('batch', help='原子保存同一构建快照的一批审核，统一重放')
    batch.add_argument('file', type=Path, help='含 snapshot_sha256、log_sha256 与 items 的 JSON 文件')
    undo = commands.add_parser('revoke')
    undo.add_argument('decision'); undo.add_argument('--reviewer', required=True); undo.add_argument('--reason', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'revoke':
            result = revoke(args.decisions, args.decision, args.reviewer, args.reason)
        else:
            with closing(sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro', uri=True)) as db:
                if args.command == 'list':
                    result = query_references(db, path=args.path, status=args.status, signal=args.signal,
                                              review_status=args.review_status, limit=args.limit, offset=args.offset)
                elif args.command == 'decisions': result = query_decisions(db, status=args.status, limit=args.limit, offset=args.offset)
                elif args.command == 'inspect': result = inspect(db, args.reference, args.target, path=args.decisions,
                    missing_definition=args.missing_definition,query=args.query)
                elif args.command == 'candidates': result = search_candidates(db,args.reference,query=args.query,
                    entry_type=args.type,ruleset=args.ruleset,source_id=args.source,limit=args.limit,offset=args.offset)
                elif args.command == 'missing': result = query_missing_definitions(db,status=args.status,path=args.path,
                    limit=args.limit,offset=args.offset)
                elif args.command == 'batch':
                    batch = json.loads(args.file.read_text())
                    if not isinstance(batch, dict) or not all(isinstance(batch.get(k), str) and batch[k] for k in ('snapshot_sha256', 'log_sha256')):
                        raise ValueError('批次缺少构建快照或日志指纹')
                    result = save_batch(db, args.decisions, batch.get('items'),
                                        expected_snapshot=batch['snapshot_sha256'], expected_log=batch['log_sha256'])
                else:
                    result = save(db, args.decisions, operation=args.command.replace('-', '_'), reference_id=args.reference,
                                  target=getattr(args, 'target', None), expected_basis=args.basis, reviewer=args.reviewer,
                                  reason=args.reason, supersedes=args.supersedes, search_query=getattr(args,'query',''))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command in {'confirm', 'non-reference', 'missing-definition', 'revoke', 'batch'}:
            print('已保存审核事件；运行 make build 重放并生成链接版，随后重启 make up。',file=sys.stderr)
    except (OSError, ValueError, LookupError, sqlite3.Error) as exc: parser.exit(1, str(exc)+'\n')


if __name__ == '__main__': main()
