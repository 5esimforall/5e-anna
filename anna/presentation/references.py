"""Read-only reference and review views over the published snapshot."""
import json
from anna.presentation.contracts import reference


def query_references(db, *, path='', reference_id='', status='', signal='', review_status='', reason='', kind='', limit=50, offset=0):
    if not 1 <= limit <= 300 or offset < 0: raise ValueError('引用查询范围无效')
    cols = {r[1] for r in db.execute('PRAGMA table_info(effective_references)')}
    clauses = ['is_effective=1'] if 'is_effective' in cols else []
    values = []
    for field, value in [('page_path', path), ('id', reference_id), ('status', status), ('signal', signal), ('review_status', review_status)]:
        if value: clauses.append(field+'=?'); values.append(value)
    for json_field, value in [('reason', reason), ('kind', kind)]:
        if value: clauses.append(f"json_extract(data,'$.{json_field}')=?"); values.append(value)
    where = ' WHERE '+' AND '.join(clauses) if clauses else ''
    total = db.execute('SELECT count(*) FROM effective_references'+where, values).fetchone()[0]
    rows = [reference(json.loads(row[0])) for row in db.execute('SELECT data FROM effective_references'+where+' ORDER BY page_path,start,id LIMIT ? OFFSET ?', values+[limit, offset])]
    return {'total': total, 'offset': offset, 'limit': limit, 'rows': rows}


def query_missing_candidates(db, *, signal='', kind='', path='', reason='', text='', limit=50, offset=0):
    """Group unreviewed unresolved references without an open missing-definition ticket."""
    if not 1 <= limit <= 300 or offset < 0: raise ValueError('缺失定义候选查询范围无效')
    cols = {r[1] for r in db.execute('PRAGMA table_info(effective_references)')}
    clauses = ["status='unresolved'", "review_status='unreviewed'", "TRIM(COALESCE(json_extract(data,'$.text'),''))!=''"]
    if 'is_effective' in cols:
        clauses.append('is_effective=1')
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='missing_definitions'").fetchone():
        clauses.append('id NOT IN (SELECT reference_id FROM missing_definitions WHERE reference_id IS NOT NULL)')
    values = []
    if signal: clauses.append('signal=?'); values.append(signal)
    if kind: clauses.append("json_extract(data,'$.kind')=?"); values.append(kind)
    if path: clauses.append('page_path=?'); values.append(path)
    if reason: clauses.append("json_extract(data,'$.reason')=?"); values.append(reason)
    if text: clauses.append("instr(lower(COALESCE(json_extract(data,'$.text'),'')), lower(?)) > 0"); values.append(text)
    where = ' WHERE ' + ' AND '.join(clauses)
    grouped_sql = (
        "SELECT signal, json_extract(data,'$.kind') AS ref_kind, json_extract(data,'$.text') AS ref_text, "
        "COUNT(*) AS cnt, COUNT(DISTINCT page_path) AS page_cnt "
        f"FROM effective_references{where} GROUP BY 1,2,3"
    )
    total = db.execute(f'SELECT COUNT(*) FROM ({grouped_sql})', values).fetchone()[0]
    groups = db.execute(
        f'{grouped_sql} ORDER BY cnt DESC, ref_kind, ref_text LIMIT ? OFFSET ?',
        values + [limit, offset],
    ).fetchall()
    rows = []
    for sig, rkind, rtext, cnt, page_cnt in groups:
        item_where = where + " AND signal=? AND json_extract(data,'$.kind')=? AND json_extract(data,'$.text')=?"
        item_vals = values + [sig, rkind, rtext]
        sample_id = db.execute(
            f'SELECT id FROM effective_references{item_where} ORDER BY page_path, start, id LIMIT 1',
            item_vals,
        ).fetchone()[0]
        reasons = sorted({
            r[0] for r in db.execute(
                f"SELECT DISTINCT json_extract(data,'$.reason') FROM effective_references{item_where}",
                item_vals,
            ) if r[0]
        })
        sample_pages = [
            r[0] for r in db.execute(
                f'SELECT DISTINCT page_path FROM effective_references{item_where} ORDER BY page_path LIMIT 5',
                item_vals,
            )
        ]
        rows.append({
            'signal': sig,
            'kind': rkind,
            'text': rtext,
            'count': cnt,
            'page_count': page_cnt,
            'reasons': reasons,
            'sample_reference_id': sample_id,
            'sample_pages': sample_pages,
        })
    return {'total': total, 'offset': offset, 'limit': limit, 'rows': rows}


def query_decisions(db, *, status='', annotation_id='', path='', ruling='', limit=50, offset=0):
    if not 1 <= limit <= 500 or offset < 0: raise ValueError('审核查询范围无效')
    clauses = ["kind='reference'"]; values = []
    if status: clauses.append('status=?'); values.append(status)
    if annotation_id: clauses.append('annotation_id=?'); values.append(annotation_id)
    if path: clauses.append("json_extract(data,'$.subject.page_path')=?"); values.append(path)
    if ruling: clauses.append("json_extract(data,'$.event.statements.ruling')=?"); values.append(ruling)
    where = ' WHERE '+' AND '.join(clauses)
    total = db.execute('SELECT count(*) FROM knowledge_annotations'+where, values).fetchone()[0]
    counts = dict(db.execute("SELECT status, count(*) FROM knowledge_annotations WHERE kind='reference' GROUP BY status"))
    rows = [json.loads(row[0]) for row in db.execute('SELECT data FROM knowledge_annotations'+where+' ORDER BY annotation_id,id LIMIT ? OFFSET ?', values+[limit, offset])]
    return {'total': total, 'offset': offset, 'limit': limit, 'counts': counts, 'rows': rows}


def query_missing_definitions(db, *, status='', path='', text='', limit=50, offset=0):
    if not 1 <= limit <= 300 or offset < 0: raise ValueError('缺失定义查询范围无效')
    clauses=[]; values=[]
    for field,value in [('status',status),('page_path',path)]:
        if value: clauses.append(field+'=?'); values.append(value)
    if text:
        clauses.append("instr(lower(COALESCE(json_extract(data,'$.text'),'')), lower(?)) > 0")
        values.append(text)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    total=db.execute('SELECT count(*) FROM missing_definitions'+where,values).fetchone()[0]
    counts=dict(db.execute('SELECT status, count(*) FROM missing_definitions GROUP BY status'))
    rows=[json.loads(r[0]) for r in db.execute('SELECT data FROM missing_definitions'+where+' ORDER BY page_path,id LIMIT ? OFFSET ?',values+[limit,offset])]
    return {'total':total,'counts':counts,'rows':rows,'limit':limit,'offset':offset}
