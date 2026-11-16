"""Read-only reference and review views over the published snapshot."""
import json
from anna.presentation.contracts import reference


def query_references(db, *, path='', reference_id='', status='', signal='', review_status='', limit=50, offset=0):
    if not 1 <= limit <= 300 or offset < 0: raise ValueError('引用查询范围无效')
    clauses = []; values = []
    for field, value in [('page_path', path), ('id', reference_id), ('status', status), ('signal', signal), ('review_status', review_status)]:
        if value: clauses.append(field+'=?'); values.append(value)
    where = ' WHERE '+' AND '.join(clauses) if clauses else ''
    total = db.execute('SELECT count(*) FROM effective_references'+where, values).fetchone()[0]
    rows = [reference(json.loads(row[0])) for row in db.execute('SELECT data FROM effective_references'+where+' ORDER BY page_path,start,id LIMIT ? OFFSET ?', values+[limit, offset])]
    return {'total': total, 'offset': offset, 'limit': limit, 'rows': rows}



def query_decisions(db, *, status='', limit=50, offset=0):
    if not 1 <= limit <= 300 or offset < 0: raise ValueError('审核查询范围无效')
    clauses = ["kind='reference'"]; values = []
    if status: clauses.append('status=?'); values.append(status)
    where = ' WHERE '+' AND '.join(clauses)
    total = db.execute('SELECT count(*) FROM knowledge_annotations'+where, values).fetchone()[0]
    rows = [json.loads(row[0]) for row in db.execute('SELECT data FROM knowledge_annotations'+where+' ORDER BY annotation_id,id LIMIT ? OFFSET ?', values+[limit, offset])]
    return {'total': total, 'offset': offset, 'limit': limit, 'rows': rows}



def query_missing_definitions(db, *, status='', path='', limit=50, offset=0):
    if not 1 <= limit <= 300 or offset < 0: raise ValueError('缺失定义查询范围无效')
    clauses=[]; values=[]
    for field,value in [('status',status),('page_path',path)]:
        if value: clauses.append(field+'=?'); values.append(value)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    total=db.execute('SELECT count(*) FROM missing_definitions'+where,values).fetchone()[0]
    rows=[json.loads(r[0]) for r in db.execute('SELECT data FROM missing_definitions'+where+' ORDER BY page_path,id LIMIT ? OFFSET ?',values+[limit,offset])]
    return {'total':total,'rows':rows,'limit':limit,'offset':offset}
