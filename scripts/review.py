#!/usr/bin/env python3
"""Inspect, save and revoke scoped reference decisions; run make build to publish them."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

from paths import PROJECT
from references import ReviewContext, query_decisions, query_references
from review_store import DEFAULT_DECISIONS, append_event, digest, lifecycle, load_events, locked, require_current


def read_reference(db, reference_id):
    row = db.execute('SELECT data FROM effective_references WHERE id=?', (reference_id,)).fetchone()
    if not row: raise ValueError('引用不存在，请重新查询有效引用 ID')
    return json.loads(row[0])


def inspect(db, reference_id, target=None):
    ref = read_reference(db, reference_id)
    context = ReviewContext(db)
    if target and target not in context.entries: raise ValueError('目标条目不存在')
    basis = context.basis(ref, target)
    page = context.pages[ref['path']]['text']
    candidates = [context.entries[i] for i in ref['automatic']['candidates'] if i in context.entries]
    selected = context.entries.get(target)
    target_text = context.pages[selected['page_path']]['text'][selected['start']:selected['end']] if selected else None
    return {'reference': ref, 'excerpt': page[max(0, ref['start']-160):ref['end']+160],
            'target': selected, 'target_text': target_text, 'candidates': candidates,
            'basis': basis, 'basis_sha256': digest(basis)}


def save(db, path, *, operation, reference_id, target, expected_basis, reviewer, reason, supersedes=None):
    if operation not in {'confirm', 'non_reference'}: raise ValueError('不支持的审核操作')
    if not reviewer.strip() or not reason.strip(): raise ValueError('审核者和依据不能为空')
    with locked(path):
        require_current(db, path)
        view = inspect(db, reference_id, target)
        summary = json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
        if summary['pipeline_sha256'] != view['basis']['pipeline_sha256']:
            raise ValueError('解析实现已变化，请先运行 make build')
        if view['basis_sha256'] != expected_basis: raise ValueError('审核依据已变化，请重新 inspect')
        ref = view['reference']
        if not view['basis']['binding']['text'].strip() or ref['presentation'] == 'definition_title':
            raise ValueError('此处是空范围或定义入口，不能作为正文引用审核')
        if operation == 'confirm' and not view['basis']['target_locatable']:
            raise ValueError('确认引用需要可定位的定义条目')
        if operation == 'non_reference' and target: raise ValueError('非引用决策不能有目标')
        events, log_hash = load_events(path); states = lifecycle(events)
        active = [e['id'] for e in events if states.get(e['id']) == 'active' and e['binding']['reference_id'] == reference_id]
        if active != ([supersedes] if supersedes else []):
            raise ValueError('此引用已有有效或待核决策；用 --supersedes 替代唯一决策，冲突时先逐一撤销')
        event = {'operation': operation, 'reviewer': reviewer.strip(), 'reason': reason.strip(),
                 'binding': view['basis']['binding'], 'basis': view['basis'], 'basis_sha256': view['basis_sha256'],
                 'reviewed_snapshot': {'automatic': ref['automatic'], 'evidence': ref['evidence'],
                                       'target': view['target'], 'target_text': view['target_text']},
                 'target_id': target, 'supersedes': supersedes}
        return append_event(path, event, log_hash)


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
    for name in ('confirm', 'non-reference'):
        cmd = commands.add_parser(name)
        cmd.add_argument('reference'); cmd.add_argument('--basis', required=True)
        if name == 'confirm': cmd.add_argument('--target', required=True)
        cmd.add_argument('--supersedes'); cmd.add_argument('--reviewer', required=True); cmd.add_argument('--reason', required=True)
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
                elif args.command == 'inspect': result = inspect(db, args.reference, args.target)
                else:
                    result = save(db, args.decisions, operation=args.command.replace('-', '_'), reference_id=args.reference,
                                  target=getattr(args, 'target', None), expected_basis=args.basis, reviewer=args.reviewer,
                                  reason=args.reason, supersedes=args.supersedes)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command in {'confirm', 'non-reference', 'revoke'}:
            print('已保存审核事件；运行 make build 重放并生成链接版，随后重启 make up。',file=sys.stderr)
    except (OSError, ValueError, sqlite3.Error) as exc: parser.exit(1, str(exc)+'\n')


if __name__ == '__main__': main()
