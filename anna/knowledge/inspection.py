"""Read observations and their current review basis without replaying or writing decisions."""
import json
from anna.knowledge.context import Context
from anna.serialization import digest


def inspect(db, rid):
    row=db.execute('SELECT data FROM rule_knowledge WHERE id=?',(rid,)).fetchone()
    if not row:raise ValueError('Knowledge record not found')
    record=json.loads(row[0]);context=Context(db);basis=context.basis(record)
    effective=db.execute('SELECT data FROM effective_knowledge WHERE id=?',(rid,)).fetchone()
    return {'observation':record,'effective':json.loads(effective[0]),'basis':basis,'basis_sha256':digest(basis)}
