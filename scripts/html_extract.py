"""Tolerant HTML parsing, anchored section extraction, and evidence-preserving fields."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import PurePosixPath
import posixpath
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag, Comment, Doctype

BLOCKS = {'p','div','h1','h2','h3','h4','h5','h6','tr','li','ul','ol','blockquote','table','section','hr'}
HEADINGS = {'h1','h2','h3','h4','h5','h6'}
IGNORED = {'script','style','noscript','head'}
GENERIC = re.compile(r'^(?:前言|简介|概述|引言|目录|使用本书|专长|法术|魔法物品|装备|种族|背景|职业|角色选项|玩家选项|起源专长|通用专长|传奇恩惠专长|战斗风格专长|怪物|生物|图鉴|数据卡|法术详述|附录[A-Z]?[：:]?.*|第[一二三四五六七八九十\d]+章.*|[0-9一二三四五六七八九]环|戏法)(?:\s*[A-Za-z].*)?$', re.I)
FIELD_TITLE = re.compile(r'^(?:护甲等级|生命值|速度|力量|敏捷|体质|智力|感知|魅力|技能|感官|语言|挑战等级|挑战|伤害抗性|伤害免疫|状态免疫)(?:\s*[:：].*|\s*)$')
CREATURE_META = re.compile(r'(?m)^[ \t]*(?:微型|小型|中型|大型|巨型|超巨型)(?:或(?:微型|小型|中型|大型|巨型))?\s*(?:异怪|野兽|天族|天界|构装|龙类|元素|妖精|邪魔|巨人|类人|怪兽|泥怪|植物|亡灵)[^\n]{0,120}$')
STATS = re.compile(r'(?:护甲等级|(?<![A-Za-z])AC(?=\s|[:：]|\d)).{0,100}(?:生命值|(?<![A-Za-z])HP(?=\s|[:：]|\d))', re.S|re.I)
SPELL = re.compile(r'施法时间.{0,250}(?:施法距离|射程|距离).{0,250}(?:法术成分|成分)', re.S)
LEGACY = re.compile(r'Legacy\b|[（(]旧版[）)]',re.I)
CLASSES = {'野蛮人','吟游诗人','牧师','德鲁伊','战士','武僧','圣武士','游侠','游荡者','术士','魔契师','邪术师','法师','奇械师','血猎手','邪狱使','拳斗士','铳士','魔战士','怪物猎人'}
CLASS_SHORT = {'诗':'吟游诗人','吟':'吟游诗人','牧':'牧师','德':'德鲁伊','帕':'圣武士','圣':'圣武士','软':'游侠','游':'游侠','术':'术士','锁':'魔契师','契':'魔契师','邪':'邪术师','法':'法师','械':'奇械师','魔':'魔战士'}


def compact(s):
    return re.sub(r'\s+', ' ', str(s)).strip()


def key(s):
    return re.sub(r'[\W_]+','',unicodedata.normalize('NFKC',s).casefold())


def digest(*parts):
    return sha256('\0'.join(str(x) for x in parts).encode()).hexdigest()[:24]


def resolve_link(page, href):
    """Decode once, preserve fragment spelling; reject remote/root-escaping URLs."""
    u=urlsplit(href.strip())
    if u.scheme or u.netloc:
        return None
    path=unquote(u.path)
    if not path:
        target=page
    elif path.startswith('/'):
        target=posixpath.normpath(path.lstrip('/'))
    else:
        target=posixpath.normpath(posixpath.join(posixpath.dirname(page),path.replace('\\','/')))
    if target.startswith('../') or target=='..':
        return None
    return target, unquote(u.fragment)


def split_name(text):
    text=re.sub(r'[（(]旧版[）)]','',text)
    text=compact(re.sub(r'Legacy\s*$','',text,flags=re.I)).strip(' ｜|：:')
    text=re.sub(r'\s*new!\s*','',text,flags=re.I)
    # Keep qualifiers; do not guess equivalence from translated names.
    m=re.search(r'[A-Za-z][A-Za-z\s\d\'’:,/()\-–.!&+]+$',text)
    if m and re.search(r'[\u3400-\u9fff]',text[:m.start()]):
        return text[:m.start()].rstrip(' ｜|'),compact(m.group()).strip()
    return (text,'') if re.search(r'[\u3400-\u9fff]',text) else ('',text)


@dataclass
class Candidate:
    start: int
    title_end: int
    name: str
    rank: int
    method: str
    tag: Tag | None = None
    anchors: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    explicit_end: int | None = None
    end: int = 0
    parent: int | None = None
    local_legacy: bool = False
    fields: dict = field(default_factory=dict)


class Document:
    def __init__(self, path, raw):
        self.path=path
        self.soup=BeautifulSoup(raw,'html5lib')
        self.title=compact(self.soup.title.get_text(' ',strip=True)) if self.soup.title else PurePosixPath(path).stem
        self.body=self.soup.body or self.soup
        self.ranges={}
        self.text_nodes=[]
        self.elements=[]
        self.anchors={}
        parts=[]
        size=0
        def add(s):
            nonlocal size
            parts.append(s);size+=len(s)
        def walk(node):
            if isinstance(node,(Comment,Doctype)):return
            if isinstance(node,NavigableString):
                # Whitespace is canonicalized before offsets are assigned.
                start=size
                add(re.sub(r'\s+',' ',str(node)))
                self.text_nodes.append((start,size,node))
                return
            if not isinstance(node,Tag) or node.name in IGNORED:
                return
            if node.name in BLOCKS or node.name=='br':add('\n')
            start=size
            self.elements.append(node)
            for child in node.children:walk(child)
            self.ranges[id(node)]=(start,size)
            for attr in ('id','name'):
                value=node.get(attr)
                if value and node.name not in {'meta','input','select'}:
                    self.anchors.setdefault(str(value),[]).append(node)
            if node.name in BLOCKS:add('\n')
        walk(self.body)
        self.text=''.join(parts)

    def span(self,tag):return self.ranges.get(id(tag),(0,0))
    def text_of(self,tag):
        a,b=self.span(tag)
        return self.text[a:b]
    def close(self):self.soup.decompose()

    def tables(self):
        out=[]
        for table in self.body.find_all('table'):
            rows=[]
            for tr in table.find_all('tr'):
                if tr.find_parent('table') is not table:continue
                cells=tr.find_all(['th','td'],recursive=False)
                if not cells:continue
                rows.append({'cells':[compact(c.get_text(' ',strip=True)) for c in cells],
                             'cell_spans':[self.span(c) for c in cells],
                             'rowspan':[c.get('rowspan','1') for c in cells],
                             'colspan':[c.get('colspan','1') for c in cells],
                             'span':self.span(tr),'tag':tr})
            if rows:
                out.append({'span':self.span(table),'rows':rows,'tag':table})
        return out

    def quickrefs(self):
        out=[]
        for n,tr in enumerate(self.body.find_all('tr')):
            kind=next((a for a in ('item','monster','spell') if tr.has_attr(a)),None)
            if not kind:continue
            cells=tr.find_all(['td','th'],recursive=False)
            vals=[compact(c.get_text(' ',strip=True)) for c in cells]
            links=[a for a in (cells[0].find_all('a',href=True) if cells else [])]
            table=tr.find_parent('table')
            headers=[]
            if table:
                first=table.find('tr')
                if first:headers=[compact(c.get_text(' ',strip=True)) for c in first.find_all(['th','td'],recursive=False)]
            href=links[0]['href'] if links else ''
            target=resolve_link(self.path,href) if href else None
            out.append({'id':digest(self.path,'row',n), 'path':self.path,'row':n,
                        'span':list(self.span(tr)),'kind':{'item':'magic_item','monster':'creature','spell':'spell'}[kind],
                        'name':compact(tr.get(kind,'')),'headers':headers,'cells':vals,'tags':compact(tr.get('tags','')),
                        'href':href,'target_path':target[0] if target else '', 'target_anchor':target[1] if target else '',
                        'legacy':bool(tr.select('.legacy')) or bool(LEGACY.search(vals[0] if vals else ''))})
        return out

    def named_tables(self):
        """Pair a caption/standalone marked line with its adjacent data table, not nearby prose."""
        title_tags=HEADINGS|{'u','b','strong'}
        def short_title(tag):
            name=compact(self.text_of(tag))
            return (1<len(name)<=120 and not re.search(r'[。；！？!?]',name) and not FIELD_TITLE.fullmatch(name)
                    and not re.fullmatch(r'(?:\d+)?[dD]\d+(?:\s*[-+]\s*\d+)?|\d+(?:[-–~～]\d+)?',name))
        for table in self.body.find_all('table'):
            if table.find_parent('table') or table.find_parent(class_=['stat-block','statblock']):continue
            rows=[r for r in table.find_all('tr') if r.find_parent('table') is table]
            if len(rows)<2 or not any(len(r.find_all(['td','th'],recursive=False))>=2 for r in rows):continue
            caption=table.find('caption',recursive=False)
            if caption and short_title(caption) and self.span(caption)[1]<=self.span(rows[0])[0]:
                yield caption,self.span(table);continue
            # html5lib may retain the table inside a paragraph or move it outside.
            # Only the immediate previous visible sibling can supply a title.
            previous=table.previous_sibling
            while previous is not None and (isinstance(previous,Comment) or isinstance(previous,NavigableString) and not str(previous).strip() or isinstance(previous,Tag) and previous.name=='br'):
                previous=previous.previous_sibling
            if not isinstance(previous,Tag) or previous.name=='table':continue
            ta,tb=self.span(table);matches=[]
            for tag in [previous]+list(previous.find_all(list(title_tags))):
                if tag.name not in title_tags or tag.find_parent(['td','th','table']) or not short_title(tag):continue
                a,b=self.span(tag)
                if b>ta or compact(self.text[b:ta]):continue
                line_start=self.text.rfind('\n',0,a)+1
                if compact(self.text[line_start:a]):continue
                matches.append(tag)
            # Nested wrappers of the same title share a range; separate titles remain ambiguous.
            spans={self.span(t) for t in matches}
            if len(spans)==1:yield matches[0],(ta,tb)

    def candidates(self, observations=()):
        items=[]
        def add(tag,name=None,rank=4,method='heading',anchor='',kind='',ob=None,end=None):
            if id(tag) not in self.ranges:return
            a,b=self.span(tag)
            name=compact(name or self.text[a:b])
            if not name or len(name)>200 or FIELD_TITLE.fullmatch(name):return
            items.append(Candidate(a,b,name,rank,method,tag,[anchor] if anchor else [],[kind] if kind else [],[ob] if ob else [],end))
        for tag in self.elements:
            if tag.name in HEADINGS:
                add(tag,rank=int(tag.name[1]),anchor=tag.get('id',''))
                continue
            if tag.name in {'b','strong'} and not tag.find_parent(list(HEADINGS)):
                block=tag.find_parent('p')
                if block and not block.find_parent(['li','table']):
                    a,b=self.span(tag);ba,bb=self.span(block)
                    prefix=compact(self.text[ba:a])
                    tail=compact(self.text[b:bb])
                    if not prefix and re.match(r'^(?:起源|通用|战斗风格|传奇恩惠|种族|血族)专长',tail):
                        add(tag,rank=4,method='descriptor_heading')
            if tag.name=='p':
                style=tag.get('style','').lower()
                if 'border-bottom' in style and len(compact(self.text_of(tag)))<150 and (tag.find('b') or tag.find('strong')):
                    add(tag,rank=4,method='word_heading')
                    continue
            color=tag.get('color','').lower()
            style=tag.get('style','').lower()
            colored=color in {'#800000','800000','#ff0000','red','maroon'} or bool(re.search(r'color\s*:\s*(?:rgb\(128,\s*0,\s*0\)|#800000)',style))
            if colored and tag.name in {'font','span'}:
                if tag.find_parent(list(HEADINGS)):continue
                txt=compact(self.text_of(tag))
                if not 1<len(txt)<150:continue
                block=tag.find_parent(['p','li','div','td','body'])
                if block is None:continue
                prefix=self.text[self.span(block)[0]:self.span(tag)[0]]
                if compact(prefix.split('\n')[-1]):continue
                # A Word heading's adjacent spans together form one title.
                if block.name=='p' and 'border-bottom' in block.get('style','').lower():continue
                if not (tag.find(['b','strong']) or tag.find_parent(['b','strong']) or re.search('[A-Za-z]{3}',txt)):continue
                add(tag,rank=4,method='colored_heading')
        # Level-prefixed inline headings in class/subclass prose, regardless of color.
        class_context=bool(re.search(r'/(?:'+ '|'.join(re.escape(x) for x in CLASSES) +r')(?:/|[-（(.])|/(?:角色职业|职业|子职|子职业)/',self.path))
        if class_context:
            for block in self.body.find_all('p'):
                if block.find_parent(['table','li']):continue
                ba,bb=self.span(block)
                headings=[]
                for tag in block.find_all(['u','b','strong']):
                    a,b=self.span(tag);name=compact(self.text[a:b])
                    if compact(self.text[ba:a]):continue
                    if re.match(r'^\d+\s*级\s*[:：]',name) and split_name(name)[1] and not re.search(r'[。；！？]',name):
                        if not any(c.start==a and c.title_end==b for c in items):
                            add(tag,rank=4,method='level_inline_heading')
                        headings.append(tag)
                        break
                if not headings:continue
                for tag in block.find_all(['b','strong']):
                    a,b=self.span(tag);name=compact(self.text[a:b]).rstrip('。:：')
                    if a<=self.span(headings[0])[1] or compact(self.text[ba:a].split('\n')[-1]):continue
                    zh,en=split_name(name)
                    if zh and en and len(name)<120 and not re.search(r'[。；！？]',name):
                        add(tag,name=name,rank=5,method='feature_inline_heading',end=bb)
            # Inline children stop at a sibling; their enclosing feature stops at the next heading.
            children=[c for c in items if c.method=='feature_inline_heading']
            for c in children:
                c.explicit_end=min([c.explicit_end]+[x.start for x in children if c.start<x.start<c.explicit_end])
        # Named tables reuse a heading at the same location, or become bounded child entries.
        for tag,table_span in self.named_tables():
            a,b=self.span(tag)
            existing=next((c for c in items if c.start==a and c.title_end==b),None)
            if existing:
                existing.fields['table_span']=list(table_span)
                continue
            preceding=[c for c in items if c.start<a and (c.explicit_end is None or c.explicit_end>a)]
            parent=max(preceding,key=lambda c:c.start) if preceding else None
            add(tag,rank=parent.rank+1 if parent else 2,method='table_caption',end=table_span[1])
            if items and items[-1].tag is tag:items[-1].fields['table_span']=list(table_span)
        # Stat blocks are atomic records, regardless of inner action headings.
        for tag in self.body.select('.stat-block, .statblock'):
            title=tag.find(list(HEADINGS))
            if title:
                add(title,rank=2,method='stat_block',kind='creature',anchor=title.get('id',''),end=self.span(tag)[1])
        # Word-exported stat blocks often have no semantic heading tags.
        # A standalone size/type line plus AC/HP identifies the card; the preceding
        # nonempty line provides its title. Metadata lines never become headings.
        synthetic=[]
        def preceding_title(pos):
            prefix=self.text[:pos].rstrip()
            a=prefix.rfind('\n')+1
            title=compact(prefix[a:])
            if title and not re.search(r'[\u3400-\u9fff]',title):
                before=prefix[:a].rstrip();b=before.rfind('\n')+1
                chinese=compact(before[b:])
                if chinese and len(chinese)<100 and re.search(r'[\u3400-\u9fff]',chinese) and not re.search(r'[A-Za-z。；！？]',chinese):
                    a=b;title=compact(prefix[a:])
            if not title or len(title)>150 or re.search(r'[。；！？]',title):return None
            return a,len(prefix),title
        for m in CREATURE_META.finditer(self.text):
            if not STATS.search(self.text[m.end():m.end()+450]):continue
            previous=preceding_title(m.start())
            if not previous:continue
            a,b,title=previous
            if any(c.explicit_end and c.start<=a<c.explicit_end for c in items):continue
            synthetic.append(Candidate(a,b,title,2,'stat_signature',None,[],['creature']))
        for i,c in enumerate(synthetic):
            following=[x.start for x in synthetic+items if 'creature' in x.kinds and x.start>c.start]
            c.explicit_end=min(following) if following else len(self.text)
            items.append(c)
        item_meta=re.compile(r'(?m)^[ \t]*(?:奇物|药水|卷轴|法杖|魔杖|魔棒|戒指|武器|护甲|盔甲|盾牌|长剑|短剑|弯刀|匕首|巨剑|长棍|权杖|魔法船)[^\n]{0,65}[，,、 ](?:非普通|极珍稀|珍稀|普通|传说|神器|稀有|罕见)[^\n]{0,100}$')
        spell_meta=re.compile(r'(?m)^[ \t]*(?:戏法|[一二三四五六七八九十\d]+环)\s*(?:[｜|/：:]\s*)?(?:防护|咒法|预言|惑控|塑能|幻术|死灵|变化)[^\n]{0,100}$')
        for kind,pattern in [('magic_item',item_meta),('spell',spell_meta)]:
            for m in pattern.finditer(self.text):
                if kind=='spell' and not SPELL.search(self.text[m.end():m.end()+700]):continue
                previous=preceding_title(m.start())
                if not previous:continue
                a,b,title=previous
                if any(c.start<=a<c.title_end and kind in c.kinds for c in items):continue
                items.append(Candidate(a,b,title,4,kind+'_signature',None,[],[kind]))
        for m in re.finditer(r'(?m)^[ \t]*([^\n。]{1,130}[（(](?:服用|吸入|接触|损伤)[）)])。',self.text):
            title=compact(m[1]);a=m.start(1);b=m.end(1)
            if re.search('[A-Za-z]',title):items.append(Candidate(a,b,title,4,'poison_signature',None,[],['poison']))
        for ob in observations:
            anchor=ob.get('target_anchor','')
            tags=self.anchors.get(anchor,[]) if anchor else []
            if len(tags)==1:
                tag=tags[0]
                if not compact(self.text_of(tag)):
                    parent=tag.find_parent(['p']+list(HEADINGS))
                    if parent and len(compact(self.text_of(parent)))<200:
                        tag=parent
                    else:
                        pos=self.span(tag)[0]
                        near=[c for c in items if pos<=c.start<=pos+200]
                        if near:tag=min(near,key=lambda c:c.start).tag
                container=tag.find_parent(class_='stat-block')
                add(tag,name=ob['name'],rank=2 if ob['kind']=='creature' else 4,method='index_anchor',anchor=anchor,kind=ob['kind'],ob=ob,end=self.span(container)[1] if container else None)
            else:
                # Unique exact heading match can recover a stale/missing anchor, not a fuzzy match.
                zh,en=split_name(ob['name'])
                exact=[c for c in items if key(c.name)==key(ob['name'])]
                matches=exact or [c for c in items if en and key(split_name(c.name)[1])==key(en)]
                if len(matches)==1:
                    c=matches[0];c.observations.append(ob);c.kinds.append(ob['kind'])
                    c.method+=':index_name'
                elif not anchor and not items:
                    add(self.body,name=ob['name'],rank=1,method='index_page',kind=ob['kind'],ob=ob,end=len(self.text))
        # Merge overlapping title wrappers, not distinct sections with similar names.
        items.sort(key=lambda c:(c.start,0 if c.observations else 1,0 if c.method=='stat_block' else 1,-c.title_end))
        merged=[]
        for c in items:
            same=next((x for x in reversed(merged[-6:]) if (c.start<=x.title_end and x.start<=c.title_end) and (key(c.name)==key(x.name) or c.observations or x.observations or c.method in {'stat_block','stat_signature'} or x.method in {'stat_block','stat_signature'})),None)
            if same:
                same.anchors=list(dict.fromkeys(same.anchors+c.anchors))
                same.kinds=list(dict.fromkeys(same.kinds+c.kinds))
                same.observations+=c.observations
                same.fields.update(c.fields)
                same.start=min(same.start,c.start);same.title_end=max(same.title_end,c.title_end)
                same.rank=min(same.rank,c.rank)
                if c.explicit_end:same.explicit_end=max(c.explicit_end,same.explicit_end or 0)
                if c.method in {'stat_block','stat_signature'}:same.method=c.method
            else:merged.append(c)
        if not merged:
            merged=[Candidate(0,0,self.title,1,'page_fallback',end=len(self.text))]
        elif merged[0].start<150 and merged[0].rank>=2:
            first_zh,first_en=split_name(merged[0].name)
            title_zh,title_en=split_name(self.title)
            if (first_zh and key(first_zh)==key(title_zh)) or (first_en and key(first_en)==key(title_en)):
                merged[0].rank=1
        # Introductory material before the first heading remains addressable.
        if compact(self.text[:merged[0].start]) and merged[0].start>100:
            merged.insert(0,Candidate(0,0,self.title,0,'page_intro',end=merged[0].start))
        stack=[]
        legacy_markers=[self.span(t) for t in self.elements if t.name in {'sup','em','i'} and compact(self.text_of(t)).casefold()=='legacy']
        for i,c in enumerate(merged):
            while stack and (merged[stack[-1]].rank>=c.rank or merged[stack[-1]].end<=c.start):stack.pop()
            c.parent=stack[-1] if stack else None
            stack.append(i)
            c.end=c.explicit_end or next((x.start for x in merged[i+1:] if x.rank<=c.rank),len(self.text))
            if c.method=='page_intro':c.end=merged[i+1].start
            # The title's immediate marker is local; explanatory prose is not.
            c.local_legacy=bool(re.search(r'[（(](?:旧版|Legacy)[）)]',c.name,re.I))
            marker_limit=min(c.end,c.title_end+40,merged[i+1].start if i+1<len(merged) else c.end)
            if any(c.start<=a<marker_limit and b<=marker_limit for a,b in legacy_markers):c.local_legacy=True
            if c.tag:
                block=c.tag.find_parent(['p']+list(HEADINGS)) or c.tag
                a,b=self.span(block)
                if a<=c.start and b>=c.title_end:
                    for marker in block.find_all(['sup','em','i']):
                        ma,mb=self.span(marker)
                        if c.start<=ma<=c.title_end+40 and compact(self.text[ma:mb]).casefold()=='legacy':c.local_legacy=True
        return merged


FIELD_LABELS = {
    'casting_time': ['施法时间'], 'range': ['施法距离','射程'], 'components':['法术成分','成分'],
    'duration':['持续时间'], 'prerequisite':['先决条件','先决','先决要求','前置条件'],
    'ability_scores':['属性值','属性值加成','属性提升'], 'skills':['技能熟练','技能熟练项'],
    'tools':['工具熟练','工具熟练项'], 'granted_feat':['专长'], 'starting_equipment':['装备'],
    'creature_type':['生物类型'], 'size':['体型'], 'speed':['速度'], 'languages':['语言'],
    'senses':['感官'], 'resistances':['伤害抗性'], 'immunities':['伤害免疫'], 'condition_immunities':['状态免疫'],
    'ac':['护甲等级','AC'], 'hp':['生命值','HP'], 'cr':['挑战等级','挑战','CR'],
}
LABEL_TO_FIELD={label:field for field,labels in FIELD_LABELS.items() for label in labels}
LABEL_RE=re.compile(r'(?<![\u3400-\u9fffA-Za-z])('+'|'.join(re.escape(x) for x in sorted(LABEL_TO_FIELD,key=len,reverse=True))+r')(?:\s*[A-Za-z][A-Za-z ]{0,35})?\s*[:：]\s*')


def text_fields(text,kind):
    """Labeled statements only. Values retain original units and qualifiers."""
    result={}
    matches=list(LABEL_RE.finditer(text))
    for i,m in enumerate(matches):
        end=min(matches[i+1].start() if i+1<len(matches) else len(text),text.find('\n',m.end()) if '\n' in text[m.end():] else len(text))
        value=compact(text[m.end():end])
        if value and len(value)<=600:
            result.setdefault(LABEL_TO_FIELD[m.group(1)],[]).append((value,m.start(),end))
    if kind=='creature':
        for field,labels in [('ac',r'AC|护甲等级'),('hp',r'HP|生命值'),('cr',r'CR|挑战等级|挑战')]:
            m=re.search(r'(?:^|[\s\n])(?:'+labels+r')\s*[:：]?\s*(\d+(?:/\d+)?[^\n]{0,65})',text,re.I)
            if m:result.setdefault(field,[]).append((compact(m[1]),m.start(),m.end()))
        for field,label in [('speed','速度'),('languages','语言'),('senses','感官'),('skills','技能'),('resistances','伤害抗性'),('immunities','伤害免疫'),('condition_immunities','状态免疫')]:
            m=re.search(r'(?:^|[\s\n])'+label+r'\s*[:：]?\s*([^\n]{1,250})',text[:1800])
            if m:result.setdefault(field,[]).append((compact(m[1]),m.start(),m.end()))
        for field,label in [('strength','力量'),('dexterity','敏捷'),('constitution','体质'),('intelligence','智力'),('wisdom','感知'),('charisma','魅力')]:
            m=re.search(label+r'\s*[:：]?\s*(\d{1,2})',text[:1200])
            if m:result.setdefault(field,[]).append((m[1],m.start(),m.end()))
    allowed={
        'spell':{'casting_time','range','components','duration'},
        'feat':{'prerequisite'},
        'background':{'ability_scores','skills','tools','granted_feat','starting_equipment','languages'},
        'species':{'ability_scores','creature_type','size','speed','languages','senses','resistances','immunities'},
        'class_feature':{'prerequisite'}, 'class':set(), 'subclass':{'prerequisite'},
        'vehicle':{'speed'}, 'facility':set(), 'section':set(), 'lore':set(), 'magic_item':set(),
    }
    if kind in allowed:result={k:v for k,v in result.items() if k in allowed[kind]}
    return result


def classify(c:Candidate, text, context, primary=False):
    """Return (type, evidence method). Context rules are labeled inferred downstream."""
    name=compact(c.name)
    intro=text[:1800]
    ctx=context
    path=context.split(' | ')[-1]
    class_context=bool(re.search(r'/(?:'+ '|'.join(re.escape(x) for x in CLASSES) +r')(?:/|[-（(.])',path)) or bool(re.search(r'/(?:角色职业|职业|子职|子职业)/',path))
    if c.kinds:return c.kinds[0],c.method if c.method.endswith('_signature') else 'index_or_statblock'
    if c.method in {'page_intro'}:return 'section','document_structure'
    if c.method=='table_caption':return 'lore','named_table_structure'
    if class_context and c.method in {'level_inline_heading','feature_inline_heading'}:return 'class_feature',c.method
    if (primary or '选项' in name) and re.match(r'^(?:魔能祈唤(?:选项)?|禁令恩惠|子职(?:业)?选项|反派选项|超魔(?:法)?选项|灌注选项)(?:\s*[A-Za-z].*)?$',name):return 'section','document_structure'
    if GENERIC.fullmatch(name) or re.fullmatch(r'(?:专长|法术|魔法物品|装备|背景|种族|物种)(?:描述|详述|列表|总览)(?:\s*[A-Za-z].*)?',name):return 'section','document_structure'
    if re.match(r'^(?:毒药的(?:制作|提取)|采购毒药|制作毒药|提取毒药)',name):return 'rule','context_heading'
    if re.search(r'^(?:子职|子职业|特色设施详述|疾病范例|毒药范例|毒药的(?:制作|提取)|毒药|疾病|专长与背景)(?:\s*[A-Za-z].*)?$',name):return 'rule','context_heading'
    if re.fullmatch(r'(?:动作|附赠动作|反应|特质|传奇动作|巢穴动作)\s*(?:Actions?|Bonus Actions?|Reactions?|Traits?|Legendary Actions?|Lair Actions?)?',name,re.I):
        return ('rule','context_heading') if re.search(r'术语|进行游戏|/战斗/',path) else ('section','document_structure')
    # Catalog headings and field descriptions are not entities. Require a descriptor,
    # not merely a keyword inherited from the page/TOC.
    if re.match(r'^\s*\d+\s*级据点设施',text[max(0,c.title_end-c.start):]):
        return 'facility','facility_signature'
    vehicle_context=bool(re.search(r'载具|船舶|战争机器|魔法船',ctx))
    vehicle_meta=re.search(r'(?m)^\s*(?:大型|巨型|超巨型)\s*(?:载具|车辆|物件|物体|运载工具)',text[max(0,c.title_end-c.start):][:250])
    ship_summary=re.search(r'载货量\s*Cargo\s*[:：]',intro) and re.search(r'船员\s*Crew\s*[:：]',intro)
    vehicle_capacity=re.search(r'(?:生物容量|生物载量|乘员数量|乘员容量)\s*(?:[A-Za-z ]+)?[:：]',intro[:900])
    if (ship_summary or vehicle_context and (vehicle_meta or vehicle_capacity)) and not re.match(r'^(?:操纵|移动|动作|武器|船壳|容量|乘员数量|载货量)',name):
        return 'vehicle','vehicle_signature'
    trap_descriptor=text[max(0,c.title_end-c.start):].lstrip()
    if re.search(r'\s(?:机械|魔法)陷阱$',name) or re.match(r'(?:机械|魔法|简易|复合|致命|妨碍)陷阱(?:\s|[（(]|$)',trap_descriptor):return 'trap','trap_signature'
    if SPELL.search(intro):return 'spell','spell_fields'
    if STATS.search(intro) and re.search(r'挑战|\bCR\b|力量.*敏捷',intro,re.S):return 'creature','stat_fields'
    descriptor=compact(text[max(0,c.title_end-c.start):])[:220]
    if re.match(r'^(?:武器|护甲|奇物|戒指|魔杖|法杖|魔棒|药水|卷轴|弹药|盾牌).{0,60}(?:普通|珍稀|稀有|传说|神器)',descriptor):return 'magic_item','item_descriptor'
    if re.match(r'^(?:起源|通用|战斗风格|传奇恩惠|种族|血族)专长',descriptor):return 'feat','feat_descriptor'
    if re.match(r'^\s*\d+\s*级\s*[:：]',name) and class_context:return 'class_feature','level_heading'
    zh,en=split_name(name)
    short_name=not re.search(r'[。！？；]',name) and len(name)<110
    if primary and any(zh==x for x in CLASSES) and re.search(r'生命[值]?骰|生命值骰|核心特质|等级.{0,15}熟练加值',text):return 'class','class_structure'
    if re.search(r'技能熟练',intro) and re.search(r'工具熟练|语言',intro) and re.search(r'装备',intro) and (primary or re.search(r'背景',ctx)):return 'background','background_fields'
    if re.search(r'生物类型|属性值加成',intro) and re.search(r'体型',intro) and re.search(r'速度',intro) and (primary or '种族' in ctx or '物种' in ctx):return 'species','species_fields'
    if re.match(r'^(?:吸入|服用|损伤|接触)型毒药',descriptor):return 'poison','poison_descriptor'
    if re.search(r'[（(【]状态[）)】]',name) and not name.startswith(('状态','【状态】')):return 'condition','name_label'
    if re.search(r'[（(【]动作[）)】]',name):return 'action','name_label'
    if c.method in {'heading','colored_heading','word_heading'} and not primary and re.search(r'/装备/(?:词条|精通词条)\.htm',path):return 'rule','property_definition'
    if re.search(r'术语汇编',path) and c.method=='heading':return 'rule','context_heading'
    if primary and class_context and zh not in CLASSES and re.search(r'\d+\s*级|第[三六十]级',text):return 'subclass','context_heading'
    if class_context and not primary and en and not re.search(r'作为|成为|职业特性|子职业|特性表|法术列表|扩展法术',name):
        if re.search(r'(?:从|在|到达|达到|第)\s*\d+\s*级',intro[:250]):return 'class_feature','level_statement'
        if c.fields.get('choice_pool'):return 'class_feature','choice_pool_heading'
        if c.method in {'colored_heading','word_heading'} and c.fields.get('class_feature_region'):return 'class_feature','context_heading'
    if short_name and (primary or bool(en)):
        if re.search(r'专长',path.replace('专长与背景','')) and not re.search(r'前置|先决|特性|组成|核心|来自|选择|复选|总览|概述|背景',name):return 'feat','context_heading'
        if re.search(r'角色起源/背景|个性与背景/背景|/背景/',path) and primary:return 'background','context_heading'
        if re.search(r'/种族/|/物种/|/血统/|/族裔/',path) and primary and not re.search(r'特质|特性|列表|简介',name):return 'species','context_heading'
        if primary and (re.search(r'子职|子职业',ctx) or (re.search(r'/角色职业/|/职业/',ctx) and re.search(r'\d+级|第\d+级|第[三六十]级',text))):return 'subclass','context_heading'
        if re.search(r'祈唤|战技|超魔|灌注|职业特性|子职特性',ctx) and en:return 'class_feature','context_heading'
        if class_context and '禁令恩惠选项' in path:
            return ('section','document_structure') if re.match(r'^\d+级禁令恩惠',name) else ('class_feature','choice_pool_heading')
        reward_context=re.search(r'黑暗赠礼|超自然赠礼|超自然奖励|超自然祝福|炼狱祝福|上古邪物的祝福',path)
        reward_overview=re.search(r'^(?:超自然赠礼|超自然奖励|黑暗赠礼(?:描述)?|黑暗交易|祝福|护咒|炼狱祝福|上古邪物的祝福)(?:\s*[A-Za-z].*)?$',name)
        if reward_context and not reward_overview and not re.search(r'翻译暂缺|Adventures of One Character|Charms$',name,re.I):return 'reward','context_heading'
        if '陷阱' in path and primary:return 'rule','context_heading'
        if re.search(r'毒药|毒素',ctx) and re.search(r'DC|豁免|毒性',intro):return 'poison','context_fields'
        if re.search(r'疾病',ctx) and re.search(r'感染|豁免|治疗',intro):return 'disease','context_fields'
    if primary and re.search(r'术语|/状态\.|/状态/|进行游戏|/战斗/|属性值应用',path):return 'rule','context_heading'
    if re.search(r'幕间|停工|虔诚|声望|据点|公司职位|变身',ctx) and short_name:return 'subsystem','context_heading'
    if primary and re.search(r'神祇|神系|诸神|派系|地理|位面|宇宙|城镇|领域|冒险|战役',ctx):return 'lore','context_heading'
    return 'section','document_structure'


VIEW_TYPES = {
    '职业':{'class'}, '子职':{'subclass'}, '职业能力':{'class_feature'}, '种族物种':{'species'},
    '背景':{'background'}, '专长':{'feat'}, '法术':{'spell'}, '装备':{'equipment','magic_item'},
    '魔法物品':{'magic_item'}, '生物数据卡':{'creature'}, '奖励':{'reward'}, '载具':{'vehicle'},
    '动物购置':{'animal_offer'}, '设施':{'facility'}, '状态动作规则':{'condition','action','rule'}, '危害':{'trap','poison','disease','hazard'},
    '扩展机制':{'subsystem'}, '资料索引':{'lore'},
}

EXTRA_VIEWS={
    '魔法武器':("type='magic_item' AND json_extract(fields,'$.item_type')='武器'",lambda e:e['type']=='magic_item' and e['fields'].get('item_type')=='武器'),
    '战斗风格':("type='feat' AND json_extract(fields,'$.feat_category')='战斗风格'",lambda e:e['type']=='feat' and e['fields'].get('feat_category')=='战斗风格'),
    '传奇恩惠':("type='feat' AND json_extract(fields,'$.feat_category')='传奇恩惠'",lambda e:e['type']=='feat' and e['fields'].get('feat_category')=='传奇恩惠'),
    'NPC':("type='creature' AND EXISTS(SELECT 1 FROM json_each(fields,'$.role') WHERE value='NPC')",lambda e:e['type']=='creature' and 'NPC' in str(e['fields'].get('role',''))),
    '伙伴':("type='creature' AND EXISTS(SELECT 1 FROM json_each(fields,'$.role') WHERE value='伙伴')",lambda e:e['type']=='creature' and '伙伴' in str(e['fields'].get('role',''))),
    '坐骑':("type='creature' AND EXISTS(SELECT 1 FROM json_each(fields,'$.role') WHERE value='坐骑')",lambda e:e['type']=='creature' and '坐骑' in str(e['fields'].get('role',''))),
}
