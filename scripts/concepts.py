"""Evidence-backed concept definitions and occurrence accounting; never merge names into identities."""
from collections import Counter,defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from bs4 import BeautifulSoup
from html_extract import Document,compact,digest,key,resolve_link,split_name

RULE_TYPES={'rule','action','condition','hazard','subsystem'}

def aliases(entry):
    names=[]
    for value in (entry['name'],entry.get('name_zh',''),entry.get('name_en','')):
        value=compact(re.sub(r'【[^】]+】','',value)).strip('。:：')
        if entry['type']=='class_feature':value=re.sub(r'^\d+\s*级\s*[:：]\s*','',value)
        if value and value not in names:names.append(value)
    if not names:return []
    zh,en=split_name(names[0])
    return list(dict.fromkeys(names+[n for n in (zh,en) if n]))

def format_kind(tag):
    """Require a uniform mark across the whole occurrence, not just one styled child."""
    kinds=set()
    for node in tag.find_all(string=True):
        if not str(node).strip():continue
        ancestors=list(node.parents);boundary=next(i for i,p in enumerate(ancestors) if p is tag)
        inner=any(p.name in {'em','i'} for p in ancestors[:boundary])
        outer=any(p.name in {'em','i'} for p in ancestors[boundary+1:])
        bold=any(p.name in {'b','strong'} for p in ancestors)
        kinds.add('ambiguous' if sum((inner,outer,bold))>1 else
                  'creature' if bold else 'spell' if inner else 'magic_item' if outer else 'concept')
    return kinds.pop() if len(kinds)==1 else 'ambiguous'

def style_profile(source,relative_path='topics/style.css'):
    """Read the source's documented convention; a changed/missing rule is not trusted."""
    path=Path(source)/relative_path
    if not path.is_file():return None
    raw=path.read_bytes();css=raw.decode('utf-8',errors='replace')
    expected={'concept':('下划线→术语格式',{'u'}),
              'spell':('下划线+斜体→法术格式',{'u em','u i'}),
              'magic_item':('斜体+下划线→魔法物品格式',{'em u','i u'}),
              'creature':('粗体+下划线→怪物格式',{'b u','u b','strong u','u strong'})}
    rules={}
    for kind,(comment,selectors) in expected.items():
        m=re.search(r'/\*\s*'+re.escape(comment)+r'\s*\*/\s*([^{}]+)\{([^{}]+)\}',css)
        if not m or {compact(s).lower() for s in m[1].split(',')}!=selectors:continue
        rules[kind]={'selectors':sorted(selectors),'declarations':compact(m[2]),'annotation':comment}
    return {'id':'source_underline_v1','path':relative_path,'sha256':sha256(raw).hexdigest(),'rules':rules,'supported':len(rules)==len(expected)}

def format_evidence(tag,profile,linked):
    # Ordered paths preserve outer/inner nesting and partial or conflicting markup.
    paths=[]
    for node in tag.find_all(string=True):
        if str(node).strip():
            path=[p.name for p in reversed(list(node.parents)) if p.name in {'u','em','i','b','strong'}]
            if path not in paths:paths.append(path)
    return {'tag_paths':paths,'markup':str(tag),'profile_id':profile['id'] if profile and linked else None,
            'stylesheet_path':profile['path'] if profile and linked else None,
            'stylesheet_sha256':profile['sha256'] if profile and linked else None}

def reference_spans(text,start,end,kind,resolver):
    """Split verified name lists, never treat a markup element as a single rule identity."""
    def known(a,b):
        ids=resolver.names.get(key(compact(text[a:b])),set())
        wanted=RULE_TYPES|{'class_feature'} if kind=='concept' else {kind}
        return any(resolver.entries[i]['type'] in wanted for i in ids)
    if known(start,end):return [(start,end)]  # punctuation may be part of a real name
    parts=[];begin=start;stack=[];pairs={'(':')','（':'）','[':']','【':'】','「':'」','“':'”'}
    def append(a,b):
        while a<b and text[a].isspace():a+=1
        while b>a and text[b-1].isspace():b-=1
        if a<b:parts.append((a,b))
    for i in range(start,end):
        ch=text[i]
        if ch in pairs:stack.append(pairs[ch]);continue
        if stack:
            if ch==stack[-1]:stack.pop()
            continue
        punctuation=ch in '，、；;,'
        # Preserve numeric/parenthesized qualifiers instead of linking a stripped base name.
        if punctuation and re.match(r'\s*(?:[+−-]?\d|[（(])',text[i+1:end]):punctuation=False
        bilingual_boundary=(ch.isspace() and i>begin and re.match(r'[A-Za-z*]',text[i-1])
                            and re.match(r'\s*[\u3400-\u9fff]',text[i:end]))
        if punctuation or bilingual_boundary:append(begin,i);begin=i+1
    append(begin,end)
    # Unrecognized fragments retain their own outcomes only when a list is evidenced
    # by at least two known names. Never link the whole mark to its final English name.
    return parts if len(parts)>1 and sum(known(a,b) for a,b in parts)>=2 else [(start,end)]

class Resolver:
    def __init__(self,entries,sources=()):
        self.sources={s["id"]:s for s in sources};self.local_definitions=defaultdict(list)
        self.entries={e['id']:e for e in entries};self.names=defaultdict(set);self.pages=defaultdict(list);self.anchors=defaultdict(set)
        for e in entries:
            self.pages[e['page_path']].append(e)
            for n in aliases(e):self.names[key(n)].add(e['id'])
            for a in e.get('anchors',[]):self.anchors[(e['page_path'],a)].add(e['id'])
        self.indexed=set();self.feature_scopes={}
        for e in entries:
            if e['type']!='class_feature':continue
            scope=e;parent=e;seen={e['id']}
            while parent.get('parent_id') in self.entries and parent['parent_id'] not in seen:
                parent=self.entries[parent['parent_id']];seen.add(parent['id'])
                if scope is e:scope=parent
                if parent['type'] in {'class','subclass'}:
                    scope=parent;break
            self.feature_scopes[e['id']]=scope
    def locator(self,path,fragment):
        ids=self.anchors.get((path,fragment),set()) if fragment else set()
        return sorted(ids)
    def resolve(self,label,kind,path,position=None):
        excluded_features=[]
        def result(candidates,reason,status=None):
            ids=sorted(e['id'] for e in candidates)
            result={'targets':ids,'reason':reason,'status':status or ('resolved' if len(ids)==1 else 'ambiguous' if ids else 'unresolved')}
            if excluded_features:result['resolution_evidence']={'out_of_scope_feature_ids':excluded_features}
            return result
        local=[d for d in self.local_definitions[path] if d['kind']==kind and key(label) in d['name_keys'] and position is not None and d['scope'][0]<=position<d['scope'][1]]
        if local:
            size=min(d['scope'][1]-d['scope'][0] for d in local)
            local=[d for d in local if d['scope'][1]-d['scope'][0]==size]
            found=result([self.entries[i] for i in sorted({d['definition_id'] for d in local})],'local_definition')
            found['resolution_evidence']={'local_definition_ids':[d['id'] for d in local]}
            return found
        ids=self.names.get(key(label),set())
        wanted=RULE_TYPES|{'class_feature'} if kind=='concept' else {kind}
        def matches_kind(e):return e['type'] in wanted or kind=='concept' and e.get('table_span') and e['type']=='lore'
        candidates=[self.entries[i] for i in ids if matches_kind(self.entries[i])]
        for e in candidates:
            if e['type']!='class_feature':continue
            scope=self.feature_scopes[e['id']]
            if position is None or scope['page_path']!=path or not scope['start']<=position<scope['end']:
                excluded_features.append(e['id'])
        candidates=[e for e in candidates if e['id'] not in excluded_features]
        if not candidates and excluded_features:return result([], 'feature_out_of_scope','review')
        if not candidates:
            return result([self.entries[i] for i in ids],'type_mismatch' if ids else 'no_definition','review' if ids else 'unresolved')
        local=self.pages.get(path,[]);sources={s for e in local for s in e.get('source_ids',[])}
        rules={e['ruleset'] for e in local if e['ruleset'] not in {'待核','冲突'}}
        same_page=[e for e in candidates if e['page_path']==path]
        if same_page:return result(same_page,'same_page')
        if len(rules)==1:candidates=[e for e in candidates if e['ruleset'] in rules]
        else:return result(candidates,'unknown_ruleset','review')
        if not candidates:return result([self.entries[i] for i in ids if matches_kind(self.entries[i]) and i not in excluded_features],'ruleset_mismatch','review')
        same_source=[e for e in candidates if sources.intersection(e.get('source_ids',[]))]
        if same_source:
            return result(same_source,'same_source')
        # A source's recorded ruleset establishes an inferred dependency on its core books.
        # Restrict to catalogued core sources, never arbitrary same-edition supplements.
        context_sources=[self.sources[s] for s in sorted(sources) if s in self.sources]
        if context_sources and all(s['ruleset_hint'] in rules for s in context_sources):
            core=[e for e in candidates if any(self.sources.get(s,{}).get('reference_role')=='core_rules' and self.sources[s]['ruleset_hint'] in rules for s in e['source_ids'])]
            if core:
                found=result(core,'source_core_rules')
                found.setdefault('resolution_evidence',{}).update({'status':'上下文推得','rule':'source_ruleset_core_dependency',
                    'sources':[{'id':s['id'],'ruleset':s['ruleset_hint'],'evidence':s['evidence']} for s in context_sources],
                    'target_sources':[{'id':sid,'role_evidence':self.sources.get(sid,{}).get('reference_role_evidence',[])} for sid in sorted({sid for e in core for sid in e['source_ids']})]})
                return found
        # Core glossary is an explicitly curated definition registry for that ruleset.
        core=[e for e in candidates if '/术语汇编/' in e['page_path'] and e['id'] in self.indexed]
        if core:return result(core,'core_registry')
        return result(candidates,'cross_source_unconfirmed','review')

def build_concepts(db,source,out,entries,pages,sources=()):
    resolver=Resolver(entries,sources);observations=[];definition_ids={e['id'] for e in entries if e['type'] in RULE_TYPES or e.get('table_span') and e['type']=='lore'}
    def observe(path,a,b,label,signal,kind,targets=(),reason='',status=None,role='mention',evidence=None,resolution_evidence=None):
        targets=sorted(set(targets));status=status or ('resolved' if len(targets)==1 else 'ambiguous' if targets else 'unresolved')
        row={'id':'concept_obs_'+digest(path,a,b,signal,kind),'path':path,'start':a,'end':b,'text':label,'signal':signal,'kind':kind,
             'status':status,'reason':reason,'role':role,'evidence':evidence or {},
             'target_id':targets[0] if status=='resolved' else None,'candidates':targets}
        if resolution_evidence:row['evidence']['resolution']=resolution_evidence
        observations.append(row);return row
    # Walk the actual navigation subtree, not a directory-name heuristic.
    quick_paths=set();toc=Path(source)/'webhelpcontents.htm'
    if toc.exists():
        soup=BeautifulSoup(toc.read_text(),'html5lib')
        for a in soup.find_all('a',href=True):
            if a.get_text(strip=True)=='本书速查':
                container=a.find_parent('div');subtree=container.find('div',recursive=False) if container else None
                if subtree:
                    for link in subtree.find_all('a',href=True):
                        target=resolve_link('webhelpcontents.htm',link['href'])
                        if target and target[0] in pages:quick_paths.add(target[0])
        soup.decompose()
    # Every explicit link in a glossary/quickref registry gets an outcome, even broken anchors.
    registry_paths={p for p in pages if '术语速查' in p or '词条精通速查' in p}
    registry_paths|=quick_paths
    from build import read_page
    for path in sorted(registry_paths):
        doc=Document(path,read_page(Path(source)/path)[0])
        for a in doc.body.find_all('a',href=True):
            dest=resolve_link(path,a['href'])
            if not dest or not dest[0].startswith('topics/'):continue
            ids=resolver.locator(*dest)
            if not dest[1]:ids=[e['id'] for e in resolver.pages.get(dest[0],[]) if e.get('is_primary') and e['type']!='section']
            row=observe(path,*doc.span(a),compact(a.get_text(' ',strip=True)),'index','entry',ids,'explicit_locator',role='index',evidence={'href':a['href'],'target_path':dest[0],'fragment':dest[1]})
            if row['status']=='resolved':resolver.indexed.add(row['target_id'])
        doc.close()
    terms=sorted({n for e in entries if e['id'] in definition_ids for n in aliases(e) if len(n)>=2},key=lambda n:(-len(n),n))
    pattern=re.compile('|'.join(re.escape(n) for n in terms),re.I) if terms else None
    profiles={};page_coverage=[];discovery_inputs={'webhelpcontents.htm'}
    for path,page in sorted(pages.items()):
        if page.get('parse_status')!='ok':continue
        doc=Document(path,read_page(Path(source)/path)[0]);occupied=[]
        titles=[(e['start'],e['title_end']) for e in resolver.pages[path]]
        def in_title(a,b):return any(x<=a and b<=y for x,y in titles)
        stylesheet_paths=[]
        for link in doc.soup.find_all('link',href=True):
            dest=resolve_link(path,link['href'])
            if 'stylesheet' not in link.get('rel',[]) or not dest:continue
            css_path=dest[0]
            if css_path not in stylesheet_paths:stylesheet_paths.append(css_path)
            discovery_inputs.add(css_path)
            if css_path not in profiles:profiles[css_path]=style_profile(source,css_path)
        supported=[profiles[p] for p in stylesheet_paths if profiles[p] and profiles[p]['supported']]
        profile=supported[0] if supported else None
        linked=bool(profile);trusted=bool(supported);marked=0
        # A named object defined inside a subfeature points to that subfeature's rule paragraph.
        # This is a scoped definition binding, not a global alias for an external item.
        if trusted:
            for tag in doc.body.find_all('u'):
                a,b=doc.span(tag);kind=format_kind(tag)
                if kind not in {'magic_item','spell','creature'} or tag.find_parent('a',href=True):continue
                if any({'example','ex-right'}.intersection(p.get('class',[])) for p in tag.parents):continue
                owners=[e for e in resolver.pages[path] if e.get('boundary_rule')=='feature_inline_heading' and e['title_end']<=a<b<=e['end']]
                if not owners:continue
                owner=min(owners,key=lambda e:e['end']-e['start'])
                if not re.search(r'(?:名为|称为|称作)[“「\s]*$',doc.text[max(owner['title_end'],a-50):a]):continue
                # A naming phrase must be followed by an object description, not just a comparison.
                if not re.match(r'[”」]?的(?:魔法)?(?:容器|物品|武器|护甲|法术|生物)',doc.text[b:b+30]):continue
                label=compact(doc.text[a:b]);zh,en=split_name(label)
                parent=resolver.entries.get(owner.get('parent_id'),owner)
                record={'id':'local_def_'+digest(path,a,b),'path':path,'name':label,'kind':kind,
                    'name_keys':sorted({key(n) for n in (label,zh,en) if n}),
                    'span':[a,b],'definition_id':owner['id'],'scope':[parent['start'],parent['end']],
                    'scope_entry_id':parent['id'],'rule':'named_object_in_subfeature'}
                resolver.local_definitions[path].append(record)
        for tag in doc.body.find_all('u'):
            if tag.find_parent('u'):continue
            marked+=1
            a,b=doc.span(tag);occupied.append((a,b));label=compact(doc.text[a:b]);kind=format_kind(tag)
            evidence=format_evidence(tag,profile,linked)
            evidence['stylesheets']=[{'path':p,'sha256':profiles[p]['sha256'] if profiles[p] else None,'supported':bool(profiles[p] and profiles[p]['supported'])} for p in stylesheet_paths]
            evidence['format_mark_id']='mark_'+digest(path,a,b)
            evidence['format_mark_span']=[a,b]
            if in_title(a,b):observe(path,a,b,label,'format',kind,reason='definition_title',status='excluded',role='definition',evidence=evidence);continue
            if tag.find_parent('a',href=True) or tag.find('a',href=True):
                observe(path,a,b,label,'format',kind,reason='explicit_link',status='excluded',role='explicit_reference',evidence=evidence);continue
            if any({'example','ex-right'}.intersection(p.get('class',[])) for p in tag.parents):
                observe(path,a,b,label,'format',kind,reason='example_context',status='review',role='example',evidence=evidence);continue
            spans=reference_spans(doc.text,a,b,kind,resolver) if trusted and kind!='ambiguous' else [(a,b)]
            for start,end in spans:
                part_evidence=dict(evidence)
                if len(spans)>1:part_evidence['segmentation']='verified_name_list'
                if not trusted:resolution={'reason':'unknown_style','status':'review'}
                elif kind=='ambiguous':resolution={'reason':'mixed_format','status':'review'}
                else:resolution=resolver.resolve(compact(doc.text[start:end]),kind,path,start)
                observe(path,start,end,compact(doc.text[start:end]),'format',kind,**resolution,evidence=part_evidence)
        page_coverage.append({'path':path,'book':path.split('/')[1],'profile_applies':trusted,'format_count':marked})
        # Unclassified boundaries and definitional prose remain visible as candidates, not invented definitions.
        for e in resolver.pages[path]:
            if e['type']=='section' and e['title_end']>e['start']:
                observe(path,e['start'],e['title_end'],e['name'],'heading','concept',reason='unclassified_definition',status='review',role='definition_candidate')
        for m in re.finditer(r'(?:称为|称作|定义为)[“「]?([^。；\n”」]{2,24})',doc.text):
            observe(path,m.start(1),m.end(1),m[1],'definition_phrase','concept',reason='semantic_candidate',status='review',role='definition_candidate')
        if pattern:
            for m in pattern.finditer(doc.text):
                a,b=m.span()
                if in_title(a,b) or any(x<b and a<y for x,y in occupied):continue
                if m[0].isascii() and ((a and re.match(r'[A-Za-z0-9_]',doc.text[a-1])) or (b<len(doc.text) and re.match(r'[A-Za-z0-9_]',doc.text[b]))):continue
                resolution=resolver.resolve(m[0],'concept',path,a)
                observe(path,a,b,m[0],'plain','concept',resolution['targets'],'lexical_candidate',status='review',evidence={'resolution_reason':resolution['reason']})
        doc.close()
    db.executescript('''
    CREATE TABLE concepts(id TEXT PRIMARY KEY,definition_id TEXT UNIQUE REFERENCES entries(id),identity_status TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE concept_observations(id TEXT PRIMARY KEY,page_path TEXT REFERENCES pages(path),start INTEGER CHECK(start>=0),end INTEGER CHECK(end>=start),signal TEXT CHECK(signal IN ('index','format','heading','definition_phrase','plain')),status TEXT CHECK(status IN ('resolved','ambiguous','unresolved','review','excluded')),target_id TEXT REFERENCES entries(id),data TEXT NOT NULL);
    CREATE INDEX concept_observations_page ON concept_observations(page_path,start);
    CREATE INDEX concept_observations_target ON concept_observations(target_id);
    ''')
    for eid in sorted(definition_ids):
        e=resolver.entries[eid];record={'id':'concept_'+e['entity_id'],'definition_id':eid,'identity_status':'provisional','aliases':aliases(e),'type':e['type'],'ruleset':e['ruleset'],'source_ids':e['source_ids']}
        db.execute('INSERT INTO concepts VALUES(?,?,?,?)',(record['id'],eid,'provisional',json.dumps(record,ensure_ascii=False)))
    observations=list({o['id']:o for o in observations}.values())
    with (out/'concept_observations.jsonl').open('w') as f:
        for o in observations:
            assert 0<=o['start']<=o['end']<=len(pages[o['path']]['text'])
            data=json.dumps(o,ensure_ascii=False);f.write(data+'\n')
            db.execute('INSERT INTO concept_observations VALUES(?,?,?,?,?,?,?,?)',(o['id'],o['path'],o['start'],o['end'],o['signal'],o['status'],o['target_id'],data))
    local_definitions=[d for defs in resolver.local_definitions.values() for d in defs]
    db.execute('CREATE TABLE local_definitions(id TEXT PRIMARY KEY,definition_id TEXT REFERENCES entries(id),scope_entry_id TEXT REFERENCES entries(id),data TEXT NOT NULL)')
    for d in local_definitions:
        db.execute('INSERT INTO local_definitions VALUES(?,?,?,?)',(d['id'],d['definition_id'],d['scope_entry_id'],json.dumps(d,ensure_ascii=False)))
    (out/'local_definitions.jsonl').write_text(''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in local_definitions))
    counts=Counter((o['signal'],o['status']) for o in observations)
    by_book={}
    for page in page_coverage:
        stats=by_book.setdefault(page['book'],{'scanned_pages':0,'profile_pages':0,'marked_pages':0,'outcomes':{}})
        stats['scanned_pages']+=1;stats['profile_pages']+=int(page['profile_applies']);stats['marked_pages']+=int(page['format_count']>0)
    for o in observations:
        stats=by_book.setdefault(o['path'].split('/')[1],{'scanned_pages':0,'profile_pages':0,'marked_pages':0,'outcomes':{}})
        outcomes=stats['outcomes'].setdefault(o['signal'],{});outcomes[o['status']]=outcomes.get(o['status'],0)+1
    report={'local_definitions':len(local_definitions),'definitions':len(definition_ids),'observations':len(observations),'quickref_paths':sorted(quick_paths),'registry_paths':sorted(registry_paths),
            'outcomes':{signal:dict((status,n) for (s,status),n in counts.items() if s==signal) for signal,_ in counts},
            'by_book':by_book,'format_marks':sum(p['format_count'] for p in page_coverage),'style_profiles':[profiles[p] for p in sorted(profiles) if profiles[p] and profiles[p]['rules']],
            'discovery_inputs':{p:sha256((Path(source)/p).read_bytes()).hexdigest() if (Path(source)/p).exists() else None for p in sorted(discovery_inputs)},
            'review_reasons':dict(Counter(o['reason'] for o in observations if o['status'] in {'review','unresolved','ambiguous'})),
            'gaps':{'index_without_target':[o['id'] for o in observations if o['signal']=='index' and o['status']!='resolved'],
                    'marked_without_target':[o['id'] for o in observations if o['signal']=='format' and o['status'] not in {'resolved','excluded'}],
                    'unclassified_definitions':[o['id'] for o in observations if o['signal']=='heading'],
                    'ambiguous_targets':[o['id'] for o in observations if o['status']=='ambiguous']},
            'unindexed_definitions':sorted(definition_ids-resolver.indexed),
            'policy':'provisional definition identities; plain mentions and semantic candidates require review; no name-based merge'}
    (out/'concept_coverage.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    db.execute('INSERT INTO metadata VALUES(?,?)',('concept_coverage',json.dumps(report,ensure_ascii=False)))
    return report
