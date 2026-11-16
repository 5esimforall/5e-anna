"""Materialized rule relationships shared by query and snapshot consumers."""
import json

from anna.knowledge.effects import resolved_effect_ids
from anna.knowledge.model import grant_is_resolved


def _scope_is_resolved(record, link, resolved_effects, grants_by_id, *, entry_wide=False):
    if record['status'] == 'resolved':
        return True
    if entry_wide or record['status'] != 'partial':
        return False
    value = record['value']
    if value.get('prerequisites', {}).get('status') == 'unparsed':
        return False
    if (value.get('activation') or {}).get('status') == 'unparsed':
        return False
    scope = link['scope']
    skind = scope.get('kind')
    if skind == 'parameter':
        param = value.get('parameters', {}).get(scope.get('field'), {})
        return bool(param) and param.get('kind') != 'unparsed'
    if skind == 'usage_branch':
        grant = grants_by_id.get(scope.get('grant_id'))
        return bool(grant) and grant_is_resolved(record, grant, entry_wide=False)
    if skind == 'effect':
        return scope.get('effect_id') in resolved_effects
    return False


def rule_rows(records, *, entry_wide=False):
    for record in records:
        rules = record['value'].get('rules', [])
        if not rules:
            continue
        resolved_effects = resolved_effect_ids(record, entry_wide=entry_wide)
        grants_by_id = {g['id']: g for g in record['value'].get('grants', [])}
        for link in rules:
            target = link['target']['resolution']
            scope_resolved = _scope_is_resolved(
                record, link, resolved_effects, grants_by_id, entry_wide=entry_wide
            )
            status = target['status'] if scope_resolved else record['status']
            for tid in target['targets'] or [None]:
                row = {'id': record['id'] + ':' + link['id'] + ':' + str(tid), 'source_id': record['subject_id'],
                       'target_id': tid, 'relation': link['relation'], 'status': status, 'record_id': record['id'],
                       'review_status': record['review_status'], 'target_status': target['status'], 'scope': link['scope'], 'when': link['when'],
                       'change': link['change'], 'ends': link['ends'], 'evidence': link['target']['evidence']}
                yield row


def build_rule_index(db, records):
    db.executescript('''CREATE TABLE knowledge_rule_links(id TEXT PRIMARY KEY,source_id TEXT REFERENCES entries(id),
        target_id TEXT REFERENCES entries(id),relation TEXT,status TEXT,data TEXT NOT NULL);
        CREATE INDEX knowledge_rule_target ON knowledge_rule_links(target_id,relation,status);
        CREATE INDEX knowledge_rule_source ON knowledge_rule_links(source_id);''')
    for row in rule_rows(records):
        db.execute('INSERT INTO knowledge_rule_links VALUES(?,?,?,?,?,?)',
            tuple(row[k] for k in ('id','source_id','target_id','relation','status'))+(json.dumps(row,ensure_ascii=False),))
