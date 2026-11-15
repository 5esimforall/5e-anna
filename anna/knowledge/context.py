"""Read-only source context, target traversal and review dependency evidence."""
from collections import defaultdict
import json
import re
from anna.catalog.concepts import Resolver, aliases
from anna.source.html import key
from anna.knowledge.values import evidence
from anna.knowledge.conditions import CLASSES
from anna.knowledge.model import SCHEMA
from anna.serialization import digest


class Context:
    def __init__(self, db):
        self.entries = {e['id']: e for raw, in db.execute('SELECT data FROM entries') for e in [json.loads(raw)]}
        self.pages = dict(db.execute('SELECT path,text FROM pages'))
        present={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.tables=[json.loads(r[0]) for r in db.execute('SELECT data FROM raw_tables')] if 'raw_tables' in present else []
        self.class_links=[{'subject_id':r[0],'object_id':r[1],'evidence':json.loads(r[2])} for r in db.execute("SELECT subject_id,object_id,evidence FROM relations WHERE predicate='belongs_to_class' ORDER BY subject_id,object_id,evidence")] if 'relations' in present else []
        self.sources = [json.loads(raw) for raw, in db.execute('SELECT data FROM sources')]
        self.source_by_id = {s['id']: s for s in self.sources}
        self.resolver = Resolver(list(self.entries.values()), self.sources)
        self.spell_names = defaultdict(set)
        for e in self.entries.values():
            if e['type']=='spell':
                for n in aliases(e):
                    if len(n)>1: self.spell_names[n].add(e['id'])
        self.name_pattern = re.compile('|'.join(re.escape(n) for n in sorted(self.spell_names,key=lambda n:(-len(n),n))),re.IGNORECASE) if self.spell_names else None

    def class_owner(self, entry):
        ids=set();seen=set();current=entry
        while current and current['id'] not in seen:
            seen.add(current['id'])
            if current['type']=='class':ids.add(current['id'])
            ids.update(r['object_id'] for r in self.class_links if r['subject_id']==current['id'])
            current=self.entries.get(current.get('parent_id'))
        if len(ids)!=1:return None
        owner=self.entries.get(next(iter(ids)))
        return owner if owner and owner['type']=='class' and owner['ruleset']==entry['ruleset'] and owner['name_zh'] in CLASSES else None

    def resolve(self, entry, name, kind):
        return self.resolver.resolve(name, kind, entry['page_path'], entry['title_end'],
            scope={'source_ids':entry['source_ids'], 'ruleset':entry['ruleset']})

    def mentions(self, entry):
        if not self.name_pattern:return []
        text=self.pages[entry['page_path']];out=[];prior=None
        for m in self.name_pattern.finditer(text,entry['title_end'],entry['end']):
            if m[0].isascii() and ((m.start() and text[m.start()-1].isascii() and text[m.start()-1].isalpha()) or (m.end()<len(text) and text[m.end()].isascii() and text[m.end()].isalpha())):continue
            result=self.resolve(entry,m[0],'spell')
            # Adjacent bilingual names are one reference, not two grants.
            if prior and not text[prior['evidence']['span'][1]:m.start()].strip() and set(result['targets'])==set(prior['resolution']['targets']):
                prior['evidence']=evidence(entry['page_path'],text,prior['evidence']['span'][0],m.end());continue
            prior={'kind':'entry','name':m[0],'resolution':result,'evidence':evidence(entry['page_path'],text,m.start(),m.end())}
            out.append(prior)
        return out

    def choice_options(self, entry, target, restrictions=None):
        constraints=target['constraints'];levels=constraints.get('levels')
        if not levels or entry['ruleset'] not in {'5e','5r'}:return []
        classes=constraints.get('class_list',{}).get('values',[])
        if restrictions and restrictions.get('class_list'):
            classes=[c for c in classes if c in restrictions['class_list']]
            if not classes:return []
        schools=constraints.get('schools',[])
        import anna.catalog.views as browse
        options=[]
        for e in self.entries.values():
            if e['type']!='spell' or e['ruleset']!=entry['ruleset']:continue
            # Membership is evidenced by spell-list fields, in the relevant source or
            # the recorded core dependency, never by global name uniqueness.
            same=set(e['source_ids']) & set(entry['source_ids'])
            core=any(self.source_by_id.get(s,{}).get('reference_role')=='core_rules' and
                     self.source_by_id[s]['ruleset_hint']==entry['ruleset'] for s in e['source_ids'])
            trusted=all(self.source_by_id.get(s,{}).get('ruleset_hint')==entry['ruleset'] for s in entry['source_ids'])
            if not same and not (core and trusted):continue
            if not set(map(str,levels)) & set(browse.normalized(e['fields'],'spell_level')):continue
            if schools and not set(schools) & set(browse.normalized(e['fields'],'school')):continue
            if classes and not set(classes) & set(browse.normalized(e['fields'],'spell_classes')):continue
            options.append(e['id'])
        return sorted(options)

    def basis(self, record):
        entry=self.entries[record['subject_id']]
        ids=set(record.get('dependencies',[]))
        ids.add(entry['id'])
        if any(t.get('owner_id') for t in nested_values(record['value'])):
            parent=self.entries.get(entry.get('parent_id'));seen=set()
            while parent and parent['id'] not in seen:
                seen.add(parent['id']);ids.add(parent['id']);parent=self.entries.get(parent.get('parent_id'))
        def definition(e):
            return {k:e.get(k) for k in ('id','name','name_en','name_zh','type','ruleset','source_ids','parent_id','fields','page_path','start','title_end','end')} | {
                'body_sha256':digest(self.pages[e['page_path']][e['start']:e['end']])}
        source_ids={s for i in ids if i in self.entries for s in self.entries[i]['source_ids']}
        return {'schema':SCHEMA,'record':record,
            'definitions':{i:definition(self.entries[i]) if i in self.entries else None for i in sorted(ids)},
            'class_ownership':[r for r in self.class_links if r['subject_id'] in ids],
            'sources':{s:{k:self.source_by_id.get(s,{}).get(k) for k in ('name','ruleset_hint','reference_role','origin','archived')} for s in sorted(source_ids)}}


def walk_targets(value):
    if isinstance(value,dict):
        if 'resolution' in value:yield value
        for k,v in value.items():
            if k!='resolution_evidence':yield from walk_targets(v)
    elif isinstance(value,list):
        for v in value:yield from walk_targets(v)


def nested_values(value):
    if isinstance(value,dict):
        yield value
        for item in value.values():yield from nested_values(item)
    elif isinstance(value,list):
        for item in value:yield from nested_values(item)
