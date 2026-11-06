"""Source ownership from the real TOC, not a hand-maintained directory map."""
from __future__ import annotations
from collections import defaultdict, Counter
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit,unquote

from html_extract import compact,key,digest,Document,split_name
from inventory import Toc,clean,bucket

CONTAINERS={'本书速查','模组相关','电子资源','纸质出版物','电子出版物','破解奥秘UA','空际远坠Drop','穆克系列','鬼魅幽谷','德拉肯海姆','瓦尔达的秘密尖塔','谦卑林','异界传送系列','怪物纲要系列'}
WORLD_WORDS=['被遗忘的国度','艾伯伦','拉尼卡','荒洲','塞洛斯','鸦阁','斯翠海文','克莱恩','塔尔多雷','德拉肯海姆','谦卑林','胧忆岛','鬼魅幽谷']


def clean_label(s):
    s=re.sub(r'new!','',s,flags=re.I).replace('*','')
    s=re.sub(r'[（(](?:无模组|无具体模组内容|有缺|旧版|缺少翻译)[）)]','',s)
    return compact(s).strip('○')


def namekey(s):
    s=clean_label(s).replace('地下城主','城主').replace('2014','')
    s=s.replace('魔邓肯巨献：','').replace('毕格比巨献：','')
    return key(s)


def catalog_rows(web):
    raw=(web/'topics/速查/资源简写.htm').read_text()
    rows=[];end=0
    for table in re.finditer(r'<table\b[^>]*>(.*?)</table>',raw,re.I|re.S):
        headings=re.findall(r'<strong\b[^>]*>(.*?)</strong>',raw[end:table.start()],re.I|re.S)
        section=clean(headings[-1]) if headings else ''
        end=table.end()
        for n,tr in enumerate(re.findall(r'<tr\b[^>]*>(.*?)</tr>',table.group(1),re.I|re.S)):
            cells=re.split(r'<td\b[^>]*>',tr,flags=re.I)[1:]
            if len(cells)!=4:continue
            en,zh,abbr,alias=map(clean,cells)
            if en=='英文名':continue
            level='collection' if zh.lstrip('○').endswith('系列') or zh.lstrip('○') in {'被遗忘的国度','魔法船：冒险于太空中','异度风景：多元宇宙之冒险','瓦尔达的秘密尖塔','破解奥秘','空际远坠'} else 'publication'
            rule='5r' if '2024' in section or section=='核心（三宝书）' else '5e' if section in {'规则扩展','战役设定','旧版资源','合作内容'} else '待核'
            rows.append({'id':'cat_'+digest(section,zh,en),'section':section,'name_zh':zh.lstrip('○'),'name_en':en.lstrip('○'),'abbreviations':abbr,'aliases':alias,'level':level,'ruleset_hint':rule,'evidence':{'path':'topics/速查/资源简写.htm','table':section,'row':n}})
    return rows


class SourceRegistry:
    def __init__(self,web):
        self.web=web
        self.catalog=catalog_rows(web)
        toc=Toc(web);toc.feed((web/'webhelpcontents.htm').read_text())
        self.nodes={r['node_id']:r for r in toc.rows}
        self.rows=toc.rows
        self.children=defaultdict(list)
        for r in self.rows:self.children[r['parent_id']].append(r['node_id'])
        self.sources={}
        self.page_sources=defaultdict(set)
        self.page_toc=defaultdict(list)
        self.node_source={}
        self.catalog_matches=defaultdict(list)
        self.dir_sources=defaultdict(set)
        self.issues=[]
        for r in self.rows:
            path=unquote(urlsplit(r['href']).path)
            if path.startswith('topics/'):self.page_toc[path].append(r)
        current='未定'
        for node_id in self.children['']:
            node=self.nodes[node_id]
            label=node['label']
            if re.match(r'^[—－-]{2,}',label):
                if any(x in label for x in ['核心','规则扩展','战役设定','旧版','合作','其余']):current=label.strip('—－-')
                continue
            if label in {'写在前面','更新日志','鸣谢列表'} or clean_label(label)=='本书速查':continue
            self._visit_source(node_id,current,[])
        self.toc_references={p:set(ids) for p,ids in self.page_sources.items()}
        # A TOC link is not proof of publication: books also link to other books.
        namespaces={}
        for sid,s in self.sources.items():
            own=[p for p,ids in self.page_sources.items() if sid in ids and not p.startswith('topics/速查/')]
            rootpath=unquote(urlsplit(self.nodes[s['toc_node']]['href']).path)
            if rootpath.startswith('topics/'):
                ns=str(PurePosixPath(rootpath).parent)
            else:
                dirs=Counter(str(p) for path in own for p in PurePosixPath(path).parents if str(p) not in {'.','topics'})
                choices=[p for p,n in dirs.items() if n>=max(1,len(own)*0.7)]
                ns=max(choices,key=lambda p:len(PurePosixPath(p).parts)) if choices else ''
            namespaces[sid]=ns
            s['namespace_hint']=ns
        canonical=defaultdict(set)
        for path,ids in self.page_sources.items():
            candidates=[s for s in ids if namespaces[s] and path.startswith(namespaces[s]+'/')]
            if candidates:
                depth=max(len(PurePosixPath(namespaces[s]).parts) for s in candidates)
                canonical[path].update(s for s in candidates if len(PurePosixPath(namespaces[s]).parts)==depth)
        self.page_sources=canonical
        for path,source_ids in self.page_sources.items():
            for parent in PurePosixPath(path).parents:
                if str(parent) in {'.','topics'}:break
                self.dir_sources[str(parent)].update(source_ids)
        # Catalog-only publications stay visible, without inventing content.
        for row in self.catalog:
            if row['level']=='publication' and not self.catalog_matches[row['id']]:
                sid='src_'+digest('catalog',row['id'])
                self.sources[sid]={'id':sid,'name':row['name_zh'] if row['name_zh']!='——' else row['name_en'],'toc_node':'','group':row['section'],'source_type':self._type(row['section']),
                    'origin':'合作' if '合作' in row['section'] else '待核','release_status':'待核','ruleset_hint':row['ruleset_hint'],
                    'archived':row['section']=='旧版资源','catalog_ids':[row['id']],'aliases':self._aliases([row]),'parent_name':'','setting':[],
                    'coverage':'catalog_only','paths':[], 'namespace_hint':'','evidence':[row['evidence']],'metadata_status':'上下文推得'}
        for sid,source in self.sources.items():
            source['paths']=sorted(path for path,ids in self.page_sources.items() if sid in ids)
            if source['paths']:source['coverage']='toc_located'
        self.aliases=defaultdict(set)
        for sid,s in self.sources.items():
            for a in [s['name']]+s['aliases']:
                if key(a):self.aliases[key(a)].add(sid)

    def _type(self,group):
        if '核心' in group:return '核心'
        if '规则扩展' in group:return '规则扩展'
        if '战役设定' in group:return '战役设定'
        if '旧版' in group:return '归档资源'
        if '模组' in group:return '冒险'
        if 'UA' in group or '破解奥秘' in group:return '测试材料'
        if '电子' in group or 'Drops' in group or 'Drop' in group:return '数字增补'
        return '待核'

    def _aliases(self,rows):
        vals=[]
        for row in rows:
            vals.extend([row['name_zh'],row['name_en']])
            vals+=re.split(r'[、；;]',row['abbreviations']+'、'+row['aliases'])
        return sorted({compact(v) for v in vals if v.strip() not in {'','——','—'}})

    def _match_catalog(self,name,parent,english_names=()):
        nk=namekey(name);full=namekey(''.join(parent)+name)
        scores=[]
        for row in self.catalog:
            candidates=[namekey(row['name_zh'])]+[namekey(a) for a in re.split('[、；]',row['aliases']) if len(a)>1 and a!='——']
            score=0
            if key(row['name_en']) in english_names and sum(key(r['name_en'])==key(row['name_en']) for r in self.catalog)==1:score=130
            for ci,ck in enumerate(candidates):
                if not ck:continue
                if nk==ck or full==ck:score=max(score,120 if ci==0 else 90)
                elif parent and ci==0 and ck.replace(namekey(''.join(parent)),'')==nk:score=max(score,110)
                elif len(ck)>=4 and ck in full:score=max(score,80 if ci==0 else 60)
                elif len(nk)>=5 and nk in ck:score=max(score,70)
            if score:scores.append((score,row))
        if not scores:return []
        best=max(x[0] for x in scores)
        matches=[r for score,r in scores if score==best]
        # Never let a collection's rule generation label override its sub-publication.
        leaf=[r for r in matches if r['level']=='publication']
        return leaf or matches

    def _visit_source(self,node_id,group,parents):
        n=self.nodes[node_id];name=clean_label(n['label'])
        children=self.children[node_id]
        nk=key(name)
        is_container=nk in {key(x) for x in CONTAINERS}
        if is_container and children:
            if name.startswith('模组'):group='模组'
            elif name.startswith('电子资源'):group='电子资源'
            elif '破解奥秘' in name:group='破解奥秘UA'
            elif '空际远坠' in name:group='Drops'
            for child in children:
                cn=self.nodes[child]
                if re.match(r'^[—－-]{2,}',cn['label']):continue
                self._visit_source(child,group,parents+[name])
            return
        display=' / '.join(parents+[name]) if parents else name
        sid='src_'+digest(display)
        english_names=set()
        landing=unquote(urlsplit(n['href']).path)
        if landing.startswith('topics/') and (self.web/landing).is_file():
            doc=Document(landing,(self.web/landing).read_text('utf-8',errors='replace'))
            titles=[doc.title]+[compact(t.get_text(' ',strip=True)) for t in doc.body.find_all(['h1','h2','h3'],limit=3)]
            english_names={key(split_name(t)[1]) for t in titles if split_name(t)[1]}
            doc.close()
        matches=self._match_catalog(name,parents,english_names)
        rules={r['ruleset_hint'] for r in matches if r['ruleset_hint']!='待核'}
        if re.search(r'2024|2025',name) and group=='核心规则':rules.add('5r')
        if '2014' in n['label']:rules.add('5e')
        rule=next(iter(rules)) if len(rules)==1 else '冲突' if len(rules)>1 else '待核'
        origin='合作' if group=='合作内容' else '官方' if group in {'核心规则','规则扩展','战役设定','旧版内容'} else '待核'
        if any('合作' in r['section'] for r in matches):origin='合作'
        release='测试' if 'UA' in group else '正式' if origin in {'官方','合作'} else '待核'
        archived='旧版' in n['label'] or group=='旧版内容'
        types={self._type(r['section']) for r in matches}
        types.discard('待核')
        stype=self._type(group)
        if stype=='待核' and len(types)==1:stype=next(iter(types))
        evidence=[{'path':'webhelpcontents.htm','node_id':node_id,'label':n['label'],'group':group}]+[r['evidence'] for r in matches]
        self.sources[sid]={'id':sid,'name':display,'toc_node':node_id,'group':group,'source_type':stype,'origin':origin,'release_status':release,'ruleset_hint':rule,
            'archived':archived,'catalog_ids':[r['id'] for r in matches],'aliases':self._aliases(matches), 'parent_name':' / '.join(parents),
            'setting':[w for w in WORLD_WORDS if w in display], 'coverage':'toc_only','paths':[], 'evidence':evidence,'metadata_status':'上下文推得'}
        for r in matches:self.catalog_matches[r['id']].append(sid)
        def assign(node):
            self.node_source[node]=sid
            r=self.nodes[node]
            path=unquote(urlsplit(r['href']).path)
            if path.startswith('topics/') and path not in {'topics/分隔符.htm','topics/旧版说明.htm','topics/第三方/关于第三方.htm'}:
                self.page_sources[path].add(sid)
            for child in self.children[node]:assign(child)
        assign(node_id)

    def owners(self,path):
        direct=self.page_sources.get(path)
        if direct:return sorted(direct),'filesystem_inference' if path in getattr(self,'orphan_paths',set()) else 'toc_ownership'
        for p in PurePosixPath(path).parents:
            sources=self.dir_sources.get(str(p),set())
            if len(sources)==1:return sorted(sources),'directory_inference'
        return [],'unresolved'

    def register_orphans(self,paths):
        """Retain orphan material as provisional path sources, never discard it."""
        groups=defaultdict(list);self.orphan_paths=set()
        for path in paths:
            if self.owners(path)[0]:continue
            b=bucket(PurePosixPath(path).relative_to('topics'))
            if b:groups[b].append(path)
        for b,files in sorted(groups.items()):
            name=b.split('/')[-1]
            matches=[r for r in self.catalog if r['level']=='publication' and namekey(r['name_zh'])==namekey(name)]
            candidates=[s for s in self.sources.values() if s['coverage']=='catalog_only' and set(s['catalog_ids'])&{r['id'] for r in matches}]
            if len(candidates)==1:s=candidates[0]
            else:
                sid='src_'+digest('filesystem',b)
                s={'id':sid,'name':b,'toc_node':'','group':'未编入目录','source_type':'待核','origin':'合作' if b.startswith('第三方/') else '待核',
                    'release_status':'待核','ruleset_hint':'待核','archived':False,'catalog_ids':[],'aliases':[], 'parent_name':'', 'setting':[],
                    'coverage':'filesystem_only','paths':[],'namespace_hint':'topics/'+b,'evidence':[{'path':'topics/'+b,'rule':'orphan_path_group'}],'metadata_status':'待核'}
                self.sources[sid]=s
            s['coverage']='filesystem_only';s['paths']=sorted(files);s['namespace_hint']='topics/'+b
            s['evidence'].append({'path':'topics/'+b,'rule':'exact_directory_name' if candidates else 'orphan_path_group'})
            for path in files:
                self.page_sources[path].add(s['id']);self.orphan_paths.add(path)
            for a in [s['name']]+s['aliases']:
                self.aliases[key(a)].add(s['id'])

    def alias_candidates(self,alias,path=''):
        exact=self.aliases.get(key(alias),set())
        owners=set(self.owners(path)[0]) if path else set()
        overlap=exact&owners
        return sorted(overlap or exact or owners)

    def context(self,path):
        nodes=[n for n in self.page_toc.get(path,[]) if not n['toc_path'].startswith('本书速查')]
        return ' | '.join(n['toc_path'] for n in nodes) or path
