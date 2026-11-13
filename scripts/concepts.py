"""Evidence-backed concept definitions and occurrence accounting; never merge names into identities."""
from collections import Counter,defaultdict
from bisect import bisect_left
from hashlib import sha256
import json
from pathlib import Path
import re
from bs4 import BeautifulSoup
from html_extract import EXAMPLE_CLASSES,LEVEL_LABEL,NOTATION_JOINER,Document,compact,digest,key,resolve_link,split_name
from entry_types import RULE_TYPES

def aliases(entry):
    names=[]
    for value in (entry['name'],entry.get('name_zh',''),entry.get('name_en','')):
        value=compact(re.sub(r'【[^】]+】','',value)).strip('。:：')
        # The level label qualifies a feature heading; it is never part of the name a reference uses.
        level=LEVEL_LABEL.match(value)
        if level and value[level.end():]:value=value[level.end():]
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

def overlap_check(spans):
    """Build a query over possibly nested source ranges."""
    starts=[];ends=[]
    for a,b in sorted(spans):
        if a<b:
            starts.append(a);ends.append(max(b,ends[-1] if ends else b))
    def overlaps(a,b):
        i=bisect_left(starts,b)-1
        return i>=0 and ends[i]>a
    return overlaps


def term_scanner(names):
    """Keep leftmost-longest matching, testing only names with a compatible first character."""
    by_initial=defaultdict(list)
    for name in sorted(set(names),key=lambda n:(-len(n),n)):
        by_initial[name[0]].append(name)
    initial_pattern=re.compile('['+re.escape(''.join(by_initial))+']',re.I)
    branches={}
    def matches(text):
        end=0
        for hit in initial_pattern.finditer(text):
            start=hit.start()
            if start<end:continue
            initial=hit[0]
            if initial not in branches:
                same=re.compile(re.escape(initial),re.I)
                terms=sorted((n for c in by_initial if same.fullmatch(c) for n in by_initial[c]),
                             key=lambda n:(-len(n),n))
                branches[initial]=re.compile('|'.join(re.escape(n) for n in terms),re.I)
            match=branches[initial].match(text,start)
            if match:
                end=match.end()
                yield match
    return matches


# A class table lists the features the class grants; the cell's structure supplies the context that
# ordinary prose must supply with a rules-use clause. Other definition kinds need their table column's
# semantic role before a bare name cell can be treated as a reference.
TABLE_REFERENCE_TYPES={'class_feature'}
TABLE_QUALIFIER=re.compile(r'\([^()\n]{0,40}\)|（[^（）\n]{0,40}）|\[[^\[\]\n]{0,30}\]|【[^【】\n]{0,30}】')
TABLE_FILLER=re.compile(r'^(?:[\s,，、;；/／·。*†‡+＋\-—\d.()（）\[\]【】:：]|与|和|或|及|以及|或者|and|or)*$',re.I)


def table_cell_names(text,scan):
    """The name spans that fill a table cell, or [] when prose, an unknown item or structure intervenes.

    Parenthesized qualifiers (a die size, 一次, 花费：1d6) are layout, not part of the name. Every
    remaining character must be a separator or layout marker, so mixed cells keep their original text.
    """
    masked=TABLE_QUALIFIER.sub(lambda m:' '*len(m[0]),text)
    found=[];pos=0
    for match in scan(masked):
        a,b=match.span()
        if match[0].isascii() and ((a and re.match(r'[A-Za-z0-9_]',masked[a-1])) or
                                   (b<len(masked) and re.match(r'[A-Za-z0-9_]',masked[b]))):continue
        if not TABLE_FILLER.fullmatch(masked[pos:a]):return []
        found.append((a,b,text[a:b]));pos=b
    return found if found and TABLE_FILLER.fullmatch(masked[pos:]) else []


PLAIN_LEAD=re.compile(
    r'(?:视为|处于|处在|陷入|保持|维持|失去|免疫|无视|提供|给予|获得|具有|承受|受到|执行|进行|完成|消耗|发动|使用|采取|作为|(?<!可)以|(?<![作应运])用)'
    r'(?:了|其|你的?|一个|一种|一次|一项|任何|全部|额外的?){0,3}[ \t]*$')
PLAIN_TAIL=re.compile(r'^[ \t]*(?:$|[。！？；、，,.;:：!?）)」”\n]|状态|词条|动作|期间|之前|之后|之下|下(?=$|[，。）；]|的|来|时)|时(?!钟|间|期|代)|来(?!源|历|临)|以|所|的|执行|发动|施展|进行|和|或|与|及|以及)')
PLAIN_EN_LEAD=re.compile(r'\b(?:gain|have|lose|ignore|provide|grant|maintain|use|take|perform|become)[ \t]+(?:(?:a|an|the|your)[ \t]+)?$',re.I)
PLAIN_EN_TAIL=re.compile(r'^(?:$|[.,;:!?\n)\]”]|[ \t]+(?:action|and|or|to|for|on|until|while|when|during|before|after)\b)',re.I)
ACTION_LEAD=re.compile(r'^(?:执行|采取|(?:take|perform)\b)',re.I)
ACTION_TAIL=re.compile(r'^[ \t]*(?:动作|action\b)',re.I)
PLAIN_JOIN=re.compile(r'[ \t]*(?:、|和|或|与|以及|及|或者|及其|\band\b|\bor\b)[ \t]*',re.I)
# The d20-test notation names its parts, and each part is a definition of its own kind: the test itself (力量检定,
# 豁免检定); what is tested, right before 检定 or 豁免 (体质豁免, 力量或敏捷豁免, 隐匿检定); the ability before the
# parentheses (感知（察觉）检定); and each skill inside them (魅力（欺瞒或游说）检定). 死亡豁免 therefore does not apply
# 死亡 as an ability, and 力量 in 力量检定 is the ability, not a table titled 力量.
CHECK_ROLES={'test':None,'tested':{'ability','skill'},'ability':{'ability'},'skill':{'skill'}}
CHECK_TEST=re.compile(r'(?:检定|豁免)$')
CHECK_TESTED=re.compile(r'(?:[ \t]*'+NOTATION_JOINER+r'[ \t]*(?:(?!的)[㐀-鿿]){1,4}?){0,5}[ \t]*(?:检定|豁免)')
CHECK_ABILITY=re.compile(r'[ \t]*[（(][^（）()\n]{1,30}[）)][ \t]*(?:检定|豁免)')
CHECK_SKILL_LEAD=re.compile(r'[（(][ \t]*(?:[^（）()\n]{0,20}?'+NOTATION_JOINER+r'[ \t]*)?$')
CHECK_SKILL_TAIL=re.compile(r'[ \t]*(?:'+NOTATION_JOINER+r'[^（）()\n]{0,20}?)?[）)][ \t]*检定')


def check_notation(text,a,b):
    """The part of a d20-test notation the name at a..b fills, or None."""
    def part(role,start,end):return {'rule':'check_notation','slot':'rule','role':role,'cue_span':[start,end]}
    if CHECK_TEST.search(text[a:b]):return part('test',a,b)
    for role,pattern in (('tested',CHECK_TESTED),('ability',CHECK_ABILITY)):
        after=pattern.match(text,b)
        if after:return part(role,a,after.end())
    before=CHECK_SKILL_LEAD.search(text[max(0,a-24):a]);after=CHECK_SKILL_TAIL.match(text,b)
    if before and after:return part('skill',a-len(before[0]),after.end())
    return None


def plain_syntax(text,a,b,previous):
    """A name must fill a rules-use slot, not just be a substring of prose. A name in a d20-test notation fills the
    notation's slot; the rules-use clause it may also sit in is kept as `clause` for names the notation cannot use."""
    clause=rule_use_clause(text,a,b,previous)
    notation=None if text[a:b].isascii() else check_notation(text,a,b)
    if notation:return {**notation,'clause':clause}
    return clause


def rule_use_clause(text,a,b,previous):
    english=text[a:b].isascii()
    lead=(PLAIN_EN_LEAD if english else PLAIN_LEAD).search(text[max(0,a-32):a])
    after=text[b:b+24]
    tail=(PLAIN_EN_TAIL if english else PLAIN_TAIL).match(after)
    if not tail:return None
    action=bool(ACTION_TAIL.match(after))
    if lead:
        slot='action' if action or ACTION_LEAD.match(lead[0]) and '一次' not in lead[0] else 'rule'
        return {'rule':'rule_use_clause','slot':slot,'cue_span':[a-len(lead[0]),a]}
    if previous is not None:
        end,slot=previous
        if 0<a-end<=12 and PLAIN_JOIN.fullmatch(text[end:a]):
            return {'rule':'coordinated_rule_use','slot':'action' if action else slot,'cue_span':[end,a]}
    return None


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
    def resolve(self,label,kind,path,position=None,*,scope=None,types=None):
        """`types` narrows a concept to the definition kinds its position admits (a d20-test notation part)."""
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
        wanted=types or (RULE_TYPES|{'class_feature'} if kind=='concept' else {kind})
        def matches_kind(e):return e['type'] in wanted or kind=='concept' and not types and e.get('table_span') and e['type']=='lore'
        candidates=[self.entries[i] for i in ids if matches_kind(self.entries[i])]
        for e in candidates:
            if e['type']!='class_feature':continue
            owner=self.feature_scopes[e['id']]
            if position is None or owner['page_path']!=path or not owner['start']<=position<owner['end']:
                excluded_features.append(e['id'])
        candidates=[e for e in candidates if e['id'] not in excluded_features]
        if not candidates and excluded_features:return result([], 'feature_out_of_scope','review')
        if not candidates:
            return result([self.entries[i] for i in ids],'type_mismatch' if ids else 'no_definition','review' if ids else 'unresolved')
        local=self.pages.get(path,[])
        sources=set(scope['source_ids']) if scope else {s for e in local for s in e.get('source_ids',[])}
        rules={scope['ruleset']} if scope else {e['ruleset'] for e in local if e['ruleset'] not in {'待核','冲突'}}
        if not scope:
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
        core=[e for e in candidates if '/术语汇编/' in e['page_path'] and e['id'] in self.indexed] if not scope else []
        if core:return result(core,'core_registry')
        return result(candidates,'cross_source_unconfirmed','review')

    def plain_scope(self,path,a,b):
        owners=[e for e in self.pages.get(path,[]) if e['title_end']<=a<b<=e['end']]
        if not owners:return None
        size=min(e['end']-e['start'] for e in owners)
        closest=[e for e in owners if e['end']-e['start']==size]
        facts={(tuple(e['source_ids']),e['ruleset']) for e in closest}
        if len(facts)!=1:return None
        source_ids,ruleset=next(iter(facts))
        if len(source_ids)!=1 or ruleset not in {'5e','5r'}:return None
        if self.sources.get(source_ids[0],{}).get('ruleset_hint')!=ruleset:return None
        return {'source_ids':list(source_ids),'ruleset':ruleset,'owner_ids':sorted(e['id'] for e in closest)}

    def resolve_plain(self,label,path,a,b,syntax,non_prose):
        # A d20-test notation part admits only definitions of its own kind. A name with none of that kind is not
        # that part (死亡 in 死亡豁免); it may still fill the rules-use clause it sits in.
        types=CHECK_ROLES.get(syntax.get('role')) if syntax else None
        mismatched=bool(types) and not any(self.entries[i]['type'] in types for i in self.names.get(key(label),()))
        if mismatched and syntax.get('clause'):syntax,types,mismatched=syntax['clause'],None,False
        if syntax:syntax={k:v for k,v in syntax.items() if k!='clause'}
        scope=self.plain_scope(path,a,b) if syntax and not non_prose else None
        resolution=self.resolve(label,'concept',path,a,scope=scope,types=types)
        evidence={'resolution_reason':resolution['reason']}
        if syntax:evidence['syntax']=syntax
        if scope:evidence['scope']=scope
        def pending(reason,status='review',targets=None):
            return {'reason':reason,'status':status,'targets':resolution['targets'] if targets is None else targets}
        if non_prose:return pending('plain_non_prose'),evidence
        if not syntax:return pending('lexical_candidate'),evidence
        if mismatched:return pending('plain_check_part_mismatch'),evidence
        if not scope:return pending('plain_scope_unconfirmed'),evidence
        if resolution['status']!='resolved':return resolution,evidence
        target_id=resolution['targets'][0];target=self.entries[target_id]
        def core(entry):
            return any(self.sources.get(s,{}).get('reference_role')=='core_rules' and
                       self.sources[s]['ruleset_hint']==scope['ruleset'] for s in entry['source_ids'])
        if target['type'] not in RULE_TYPES or not core(target):
            return pending('plain_non_core_target'),evidence
        if target['type']=='action' and syntax['slot']!='action':
            return pending('plain_action_context'),evidence
        if target['page_path']==path and target['start']<=a<b<=target['end']:
            return pending('definition_self_reference'),evidence
        # Plain text supplies no visual type marker; a relevant non-rule homonym cannot be discarded just because the
        # rule candidate is unique. A d20-test notation part states its kind, as a marker would.
        competitors=[] if types else [e['id'] for i in self.names.get(key(label),set()) if (e:=self.entries[i])['type'] not in RULE_TYPES|{'section','class_feature'}
                     and e['ruleset']==scope['ruleset'] and (set(scope['source_ids']).intersection(e['source_ids']) or core(e))]
        if competitors:return pending('plain_competing_kind','ambiguous',[target_id,*competitors]),evidence
        return resolution,evidence

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
    terms={n for e in entries if e['id'] in definition_ids for n in aliases(e) if len(n)>=2}
    scan_terms=term_scanner(terms) if terms else None
    table_terms={n for e in entries if e['type'] in TABLE_REFERENCE_TYPES for n in aliases(e) if len(n)>=2}
    table_scan=term_scanner(table_terms) if table_terms else None
    by_initial=defaultdict(list)
    for n in sorted(terms,key=lambda n:(-len(n),n)):by_initial[n[0].casefold()].append(n)
    def shorter_names(text,a,b):
        """Names that start where a longer match starts, longest first: 力量 inside 力量检定."""
        for n in by_initial.get(text[a].casefold(),[]):
            end=a+len(n)
            if end>=b or text[a:end].casefold()!=n.casefold():continue
            if n.isascii() and end<len(text) and re.match(r'[A-Za-z0-9_]',text[end]):continue
            yield end
    profiles={};page_coverage=[];discovery_inputs={'webhelpcontents.htm'}
    for path,page in sorted(pages.items()):
        if page.get('parse_status')!='ok':continue
        doc=Document(path,read_page(Path(source)/path)[0]);occupied=[doc.span(t) for t in doc.body.find_all('a',href=True)]
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
                if any(EXAMPLE_CLASSES.intersection(p.get('class',[])) for p in tag.parents):continue
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
            if any(EXAMPLE_CLASSES.intersection(p.get('class',[])) for p in tag.parents):
                observe(path,a,b,label,'format',kind,reason='example_context',status='review',role='example',evidence=evidence);continue
            spans=reference_spans(doc.text,a,b,kind,resolver) if trusted and kind!='ambiguous' else [(a,b)]
            for start,end in spans:
                part_evidence=dict(evidence)
                if len(spans)>1:part_evidence['segmentation']='verified_name_list'
                if not trusted:resolution={'reason':'unknown_style','status':'review'}
                elif kind=='ambiguous':resolution={'reason':'mixed_format','status':'review'}
                else:resolution=resolver.resolve(compact(doc.text[start:end]),kind,path,start)
                observe(path,start,end,compact(doc.text[start:end]),'format',kind,**resolution,evidence=part_evidence)
        # A class table cell that is only a list of class-feature names is a reference-bearing
        # structure of its own, like a rules-use clause in prose. Existing links, format marks and
        # definition titles win, and a cell containing prose or an unknown item stays out.
        if table_scan:
            for ti,table in enumerate(doc.tables()):
                if table['tag'].find_parent('table'):continue
                if any(EXAMPLE_CLASSES.intersection(p.get('class',[])) for p in table['tag'].parents):continue
                rows=table['rows'];header=next((r for r in rows[:4] if r['cell_tags'] and
                    all(tag=='th' for tag in r['cell_tags'])),rows[0])
                headers=[compact(h) for h in header['cells']]
                for ri,row in enumerate(rows[1:],1):
                    if row['cell_tags'] and all(tag=='th' for tag in row['cell_tags']):continue
                    for ci,cell_span in enumerate(row['cell_spans']):
                        ca,cb=cell_span;text=doc.text[ca:cb]
                        if not text.strip():continue
                        if any(ca < te and ts < cb for ts,te in titles):continue
                        if any(ca < ob and oa < cb for oa,ob in occupied):continue
                        names=table_cell_names(text,table_scan)
                        if not names:continue
                        linked=[]
                        for a,b,label in names:
                            resolution=resolver.resolve(label,'concept',path,ca+a,types=TABLE_REFERENCE_TYPES)
                            if resolution['status']=='resolved':linked.append((a,b,label,resolution))
                        if not linked:continue
                        merged=[]
                        for a,b,label,resolution in linked:
                            if merged and merged[-1][3]['targets']==resolution['targets'] and not text[merged[-1][1]:a].strip():
                                merged[-1]=(merged[-1][0],b,text[merged[-1][0]:b],merged[-1][3])
                            else:merged.append((a,b,label,resolution))
                        for a,b,label,resolution in merged:
                            evidence={'table':{'id':'tbl_'+digest(path,ti),'span':list(table['span']),
                                'cell_span':[ca,cb],'row':ri,'column':ci,'headers':headers,
                                'cell_text':compact(text)},'resolution_reason':resolution['reason']}
                            if resolution.get('resolution_evidence'):evidence['resolution']=resolution['resolution_evidence']
                            observe(path,ca+a,ca+b,label,'table','concept',role='mention',evidence=evidence,**resolution)
                        occupied.append((ca,cb))
        page_coverage.append({'path':path,'book':path.split('/')[1],'profile_applies':trusted,'format_count':marked})
        # Unclassified boundaries and definitional prose remain visible as candidates, not invented definitions.
        for e in resolver.pages[path]:
            if e['type']=='section' and e['title_end']>e['start']:
                observe(path,e['start'],e['title_end'],e['name'],'heading','concept',reason='unclassified_definition',status='review',role='definition_candidate')
        for m in re.finditer(r'(?:称为|称作|定义为)[“「]?([^。；\n”」]{2,24})',doc.text):
            observe(path,m.start(1),m.end(1),m[1],'definition_phrase','concept',reason='semantic_candidate',status='review',role='definition_candidate')
        if scan_terms:
            covered=overlap_check(occupied+titles)
            non_prose=overlap_check(doc.span(t) for t in doc.elements if t.name in {'table','blockquote'} or EXAMPLE_CLASSES.intersection(t.get('class',[])))
            previous=None
            for m in scan_terms(doc.text):
                a,b=m.span()
                if covered(a,b):continue
                if m[0].isascii() and ((a and re.match(r'[A-Za-z0-9_]',doc.text[a-1])) or (b<len(doc.text) and re.match(r'[A-Za-z0-9_]',doc.text[b]))):continue
                syntax=plain_syntax(doc.text,a,b,previous)
                resolution,evidence=resolver.resolve_plain(m[0],path,a,b,syntax,non_prose(a,b))
                # A longer name defined only in the other rules generation (2014 力量检定 in 2024 text) yields to the
                # longest name inside it that this generation defines.
                if resolution['reason']=='ruleset_mismatch':
                    for end in shorter_names(doc.text,a,b):
                        inner_syntax=plain_syntax(doc.text,a,end,previous)
                        inner,inner_evidence=resolver.resolve_plain(doc.text[a:end],path,a,end,inner_syntax,non_prose(a,end))
                        if inner['status']!='resolved':continue
                        inner_evidence['longer_name']={'text':m[0],'span':[a,b],'resolution_reason':resolution['reason']}
                        b,syntax,resolution,evidence=end,inner_syntax,inner,inner_evidence;break
                if resolution['status']=='resolved':previous=(b,syntax['slot'])
                observe(path,a,b,doc.text[a:b],'plain','concept',**resolution,evidence=evidence)
        doc.close()
    db.executescript('''
    CREATE TABLE concepts(id TEXT PRIMARY KEY,definition_id TEXT UNIQUE REFERENCES entries(id),identity_status TEXT NOT NULL,data TEXT NOT NULL);
    CREATE TABLE concept_observations(id TEXT PRIMARY KEY,page_path TEXT REFERENCES pages(path),start INTEGER CHECK(start>=0),end INTEGER CHECK(end>=start),signal TEXT CHECK(signal IN ('index','format','heading','definition_phrase','plain','table')),status TEXT CHECK(status IN ('resolved','ambiguous','unresolved','review','excluded')),target_id TEXT REFERENCES entries(id),data TEXT NOT NULL);
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
            'policy':'provisional definition identities; plain rule-use clauses require scoped core targets; table name cells require class-feature names in the owning class or subclass; no name-based merge'}
    (out/'concept_coverage.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    db.execute('INSERT INTO metadata VALUES(?,?)',('concept_coverage',json.dumps(report,ensure_ascii=False)))
    return report
