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

def style_profile(source):
    """Read the source's documented convention; a changed/missing rule is not trusted."""
    path=Path(source)/'topics/style.css'
    if not path.exists():return None
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
    return {'id':'source_underline_v1','path':'topics/style.css','sha256':sha256(raw).hexdigest(),'rules':rules,'supported':len(rules)==len(expected)}

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

class Resolver:
    def __init__(self,entries):
        self.entries={e['id']:e for e in entries};self.names=defaultdict(set);self.pages=defaultdict(list);self.anchors=defaultdict(set)
        for e in entries:
            self.pages[e['page_path']].append(e)
            for n in aliases(e):self.names[key(n)].add(e['id'])
            for a in e.get('anchors',[]):self.anchors[(e['page_path'],a)].add(e['id'])
        self.indexed=set()
    def locator(self,path,fragment):
        ids=self.anchors.get((path,fragment),set()) if fragment else set()
        return sorted(ids)
    def resolve(self,label,kind,path):
        def result(candidates,reason,status=None):
            ids=sorted(e['id'] for e in candidates)
            return {'targets':ids,'reason':reason,'status':status or ('resolved' if len(ids)==1 else 'ambiguous' if ids else 'unresolved')}
        ids=self.names.get(key(label),set())
        wanted=RULE_TYPES if kind=='concept' else {kind}
        candidates=[self.entries[i] for i in ids if self.entries[i]['type'] in wanted]
        if not candidates:
            return result([self.entries[i] for i in ids],'type_mismatch' if ids else 'no_definition','review' if ids else 'unresolved')
        local=self.pages.get(path,[]);sources={s for e in local for s in e.get('source_ids',[])}
        rules={e['ruleset'] for e in local if e['ruleset'] not in {'待核','冲突'}}
        same_page=[e for e in candidates if e['page_path']==path]
        if same_page:return result(same_page,'same_page')
        if len(rules)==1:candidates=[e for e in candidates if e['ruleset'] in rules]
        else:return result(candidates,'unknown_ruleset','review')
        if not candidates:return result([self.entries[i] for i in ids if self.entries[i]['type'] in wanted],'ruleset_mismatch','review')
        same_source=[e for e in candidates if sources.intersection(e.get('source_ids',[]))]
        if same_source:
            return result(same_source,'same_source')
        # Core glossary is an explicitly curated definition registry for that ruleset.
        core=[e for e in candidates if '/术语汇编/' in e['page_path'] and e['id'] in self.indexed]
        if core:return result(core,'core_registry')
        return result(candidates,'cross_source_unconfirmed','review')

def build_concepts(db,source,out,entries,pages):
    resolver=Resolver(entries);observations=[];definition_ids={e['id'] for e in entries if e['type'] in RULE_TYPES}
    def observe(path,a,b,label,signal,kind,targets=(),reason='',status=None,role='mention',evidence=None):
        targets=sorted(set(targets));status=status or ('resolved' if len(targets)==1 else 'ambiguous' if targets else 'unresolved')
        row={'id':'concept_obs_'+digest(path,a,b,signal,kind),'path':path,'start':a,'end':b,'text':label,'signal':signal,'kind':kind,
             'status':status,'reason':reason,'role':role,'evidence':evidence or {},
             'target_id':targets[0] if status=='resolved' else None,'candidates':targets}
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
    profile=style_profile(source);page_coverage=[]
    for path,page in sorted(pages.items()):
        if page.get('parse_status')!='ok':continue
        doc=Document(path,read_page(Path(source)/path)[0]);occupied=[]
        titles=[(e['start'],e['title_end']) for e in resolver.pages[path]]
        def in_title(a,b):return any(x<=a and b<=y for x,y in titles)
        linked=any(resolve_link(path,t.get('href',''))==('topics/style.css','') and 'stylesheet' in t.get('rel',[]) for t in doc.soup.find_all('link',href=True))
        trusted=bool(profile and profile['supported'] and linked);marked=0
        for tag in doc.body.find_all('u'):
            if tag.find_parent('u'):continue
            marked+=1
            a,b=doc.span(tag);occupied.append((a,b));label=compact(doc.text[a:b]);kind=format_kind(tag)
            evidence=format_evidence(tag,profile,linked)
            if in_title(a,b):observe(path,a,b,label,'format',kind,reason='definition_title',status='excluded',role='definition',evidence=evidence);continue
            if tag.find_parent('a',href=True) or tag.find('a',href=True):
                observe(path,a,b,label,'format',kind,reason='explicit_link',status='excluded',role='explicit_reference',evidence=evidence);continue
            if any({'example','ex-right'}.intersection(p.get('class',[])) for p in tag.parents):
                observe(path,a,b,label,'format',kind,reason='example_context',status='review',role='example',evidence=evidence);continue
            if not trusted:resolution={'reason':'unknown_style','status':'review'}
            elif kind=='ambiguous':resolution={'reason':'mixed_format','status':'review'}
            else:resolution=resolver.resolve(label,kind,path)
            observe(path,a,b,label,'format',kind,**resolution,evidence=evidence)
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
                resolution=resolver.resolve(m[0],'concept',path)
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
    counts=Counter((o['signal'],o['status']) for o in observations)
    by_book={}
    for page in page_coverage:
        stats=by_book.setdefault(page['book'],{'scanned_pages':0,'profile_pages':0,'marked_pages':0,'outcomes':{}})
        stats['scanned_pages']+=1;stats['profile_pages']+=int(page['profile_applies']);stats['marked_pages']+=int(page['format_count']>0)
    for o in observations:
        stats=by_book.setdefault(o['path'].split('/')[1],{'scanned_pages':0,'profile_pages':0,'marked_pages':0,'outcomes':{}})
        outcomes=stats['outcomes'].setdefault(o['signal'],{});outcomes[o['status']]=outcomes.get(o['status'],0)+1
    report={'definitions':len(definition_ids),'observations':len(observations),'quickref_paths':sorted(quick_paths),'registry_paths':sorted(registry_paths),
            'outcomes':{signal:dict((status,n) for (s,status),n in counts.items() if s==signal) for signal,_ in counts},
            'by_book':by_book,'style_profiles':[profile] if profile else [],
            'discovery_inputs':{p:sha256((Path(source)/p).read_bytes()).hexdigest() if (Path(source)/p).exists() else None for p in ('topics/style.css','webhelpcontents.htm')},
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
