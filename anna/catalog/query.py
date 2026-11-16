#!/usr/bin/env python3
"""Read-only queries over the extracted dataset via anna.presentation.catalog."""
import argparse
import json
from pathlib import Path
import re
import sys

from anna.catalog import views as browse
from anna.paths import PROJECT
from anna.presentation import catalog

DB = PROJECT / 'data/extracted/catalog.sqlite'
PRESETS = {p['name']: p for p in browse.PRESETS}


def _matching_sources(db, names, release):
    if not names and not release:
        return None
    matched = []
    for sid, sname, rstatus, raw in db.execute('SELECT id, name, release_status, data FROM sources'):
        if release and rstatus != release:
            continue
        if names:
            aliases = [str(a).casefold() for a in json.loads(raw).get('aliases', [])]
            if not any(term == sid or term.lower() in sname.lower() or term.casefold() in aliases for term in names):
                continue
        matched.append(sid)
    return matched or [browse.NONE]


def _normalize_flag_value(field, value):
    if field == 'spell_level':
        if value in browse.LEVELS:
            return str(browse.LEVELS.index(value))
        m = re.fullmatch(r'([0-9])环', value)
        if m:
            return m.group(1)
        if value == '零环':
            return '0'
    return value


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', type=Path, default=DB)
    ap.add_argument('--type', action='append', help='repeatable: spell, feat, creature, ...')
    ap.add_argument('--preset', help='named preset, e.g. 魔法武器, 坐骑, NPC, 传奇恩惠')
    ap.add_argument('--ruleset', choices=['5e', '5r', '待核'])
    ap.add_argument('--source', action='append', help='book name, ID, or abbreviation; repeatable union')
    ap.add_argument('--origin', choices=['官方', '合作', '待核'])
    ap.add_argument('--release', choices=['正式', '测试', '待核'])
    ap.add_argument('--legacy', choices=['entry_marked', 'source_archived', 'unmarked'])
    ap.add_argument('--status', choices=['原文明示', '上下文推得', '未分类段落'])
    ap.add_argument('--search', default='')
    ap.add_argument('--field', action='append', default=[], help='field=value, e.g. school=塑能')
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--include-sections', action='store_true')
    ap.add_argument('--show', help='version ID: include original text, evidence and relations')
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--csv', action='store_true')
    args = ap.parse_args()
    if not args.db.exists():
        ap.error('run build.py first')

    db = catalog.connect(args.db)
    try:
        if args.stats:
            row = db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()
            print(json.dumps(json.loads(row[0]), ensure_ascii=False, indent=2))
            return
        if args.show:
            try:
                data = catalog.detail(db, args.show)
            except LookupError:
                ap.error('version ID not found')
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return

        params = {'versions': ['each']}
        if args.preset:
            preset = PRESETS.get(args.preset)
            if not preset:
                ap.error('unknown preset: ' + args.preset)
            params.setdefault('type', []).append(preset['type'])
            params.setdefault('f_' + preset['field'], []).append(preset['value'])
        if args.type:
            params.setdefault('type', []).extend(args.type)
        if args.include_sections:
            params['sections'] = ['1']
        for key, value in [('ruleset', args.ruleset), ('legacy', args.legacy), ('status', args.status), ('origin', args.origin)]:
            if value:
                params[key] = [value]
        if args.search:
            params['q'] = [args.search]
        sources = _matching_sources(db, args.source, args.release)
        if sources is not None:
            params['source'] = sources
        for spec in args.field:
            if '=' not in spec:
                ap.error('--field requires name=value')
            field, value = spec.split('=', 1)
            if not re.fullmatch(r'[a-z_]+', field):
                ap.error('invalid field name')
            if field in browse.LABELS and field != 'cr':
                params.setdefault('f_' + field, []).append(_normalize_flag_value(field, value))
            else:
                params['field'] = [field]
                params['value'] = [value]

        try:
            result = catalog.query(db, params, export=True)
        except ValueError as exc:
            ap.error(str(exc))

        total = result['total']
        rows = result['rows']
        limit = args.limit if args.limit is not None else (None if args.csv else 20)
        if limit is not None:
            rows = rows[:max(0, limit)]

        if args.csv:
            sys.stdout.buffer.write(catalog.csv_export(rows, browse.schema_for(catalog.entry_types(params))))
        elif args.json:
            print(json.dumps({'count': total, 'rows': rows}, ensure_ascii=False, indent=2))
        else:
            print(f'{total} version candidates; showing {len(rows)}. Inferences and pending reviews are not confirmed.')
            for r in rows:
                print('\t'.join([r['id'], r['name'], r['type'], r['ruleset'], r['legacy'], '; '.join(r['source_names'])]))
    finally:
        db.close()


if __name__ == '__main__':
    main()
