"""Materialized rule relationships shared by query and snapshot consumers."""
import json


def rule_rows(records):
    for record in records:
        for link in record['value'].get('rules',[]):
            target=link['target']['resolution']
            status=target['status'] if record['status']=='resolved' else record['status']
            for tid in target['targets'] or [None]:
                row={'id':record['id']+':'+link['id']+':'+str(tid),'source_id':record['subject_id'],
                     'target_id':tid,'relation':link['relation'],'status':status,'record_id':record['id'],
                     'review_status':record['review_status'],'target_status':target['status'],'scope':link['scope'],'when':link['when'],
                     'change':link['change'],'ends':link['ends'],'evidence':link['target']['evidence']}
                yield row


def build_rule_index(db, records):
    db.executescript('''CREATE TABLE knowledge_rule_links(id TEXT PRIMARY KEY,source_id TEXT REFERENCES entries(id),
        target_id TEXT REFERENCES entries(id),relation TEXT,status TEXT,data TEXT NOT NULL);
        CREATE INDEX knowledge_rule_target ON knowledge_rule_links(target_id,relation,status);
        CREATE INDEX knowledge_rule_source ON knowledge_rule_links(source_id);''')
    for row in rule_rows(records):
        db.execute('INSERT INTO knowledge_rule_links VALUES(?,?,?,?,?,?)',
            tuple(row[k] for k in ('id','source_id','target_id','relation','status'))+(json.dumps(row,ensure_ascii=False),))
