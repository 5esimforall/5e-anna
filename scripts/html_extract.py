"""Tolerant HTML parsing, anchored section extraction, and evidence-preserving fields."""
from __future__ import annotations

from collections import Counter, defaultdict
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
RULE_TYPES = {'rule','action','condition','hazard','subsystem'}
EXAMPLE_CLASSES = {'example','ex-right'}
GENERIC = re.compile(r'^(?:前言|简介|概述|引言|目录|使用本书|专长|法术|魔法物品|装备|种族|背景|职业|角色选项|玩家选项|起源专长|通用专长|传奇恩惠专长|战斗风格专长|怪物|生物|图鉴|数据卡|法术详述|附录[A-Z]?[：:]?.*|第[一二三四五六七八九十\d]+章.*|[0-9一二三四五六七八九]环|戏法)(?:\s*[A-Za-z].*)?$', re.I)
FIELD_TITLE = re.compile(r'^(?:护甲等级|生命值|速度|力量|敏捷|体质|智力|感知|魅力|技能|感官|语言|挑战等级|挑战|伤害抗性|伤害免疫|状态免疫)(?:\s*[:：].*|\s*)$')
CREATURE_META = re.compile(r'(?m)^[ \t]*(?:微型|小型|中型|大型|巨型|超巨型)(?:或(?:微型|小型|中型|大型|巨型))?\s*(?:异怪|野兽|天族|天界|构装|龙类|元素|妖精|邪魔|巨人|类人|怪兽|泥怪|植物|亡灵)[^\n]{0,120}$')
STATS = re.compile(r'(?:护甲等级|(?<![A-Za-z])AC(?=\s|[:：]|\d)).{0,100}(?:生命值|(?<![A-Za-z])HP(?=\s|[:：]|\d))', re.S|re.I)
SPELL = re.compile(r'施法时间.{0,250}(?:施法距离|射程|距离).{0,250}(?:法术成分|成分)', re.S)
LEGACY = re.compile(r'Legacy\b|[（(]旧版[）)]',re.I)
# The two rule generations print the same rule differently. A 2024 stat block lists initiative and a
# modifier/save column per ability; a 2014 one is a table of scores with modifiers in parentheses
# (several of them, so a prose note such as 智力18（+4） is not a table). A 2024 spell drops the
# count from its casting time (动作, not 1 动作). These are layouts, not book names.
GENERATION_MARKERS = {
    'creature':{'5r':(re.compile(r'先攻\s*[+＋\-−]?\d|调整\s*豁免'),1),
                '5e':(re.compile(r'\d{1,2}\s*[（(]\s*[+＋\-−]\s*\d+\s*[)）]'),3)},
    'spell':{'5r':(re.compile(r'施法时间\s*[:：]\s*(?:动作|附赠|反应)'),1),
             '5e':(re.compile(r'施法时间\s*[:：]\s*[1１一]\s*个?\s*(?:动作|附赠|反应)'),1)},
}


def printed_generation(kind,text):
    """(ruleset, span) when an entry's text is laid out in exactly one generation's format."""
    found={}
    for rule,(pattern,needed) in GENERATION_MARKERS.get(kind,{}).items():
        matches=list(pattern.finditer(text))
        if len(matches)>=needed:found[rule]=(matches[0].start(),matches[needed-1].end())
    return next(iter(found.items())) if len(found)==1 else None
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


# A conjunction counts as a separator only right after an English run and before the next
# Chinese name, so 或 inside a Chinese name (微型或小型) never splits it.
JOINERS = ('或','/','／','、')
ALTERNATIVE = re.compile(r'(?<=[A-Za-z])(\s*(?:'+'|'.join(map(re.escape,JOINERS))+r')\s*)(?=[㐀-鿿\d])')
# Without a conjunction, a translation can run straight into the next Chinese name
# (蜥蜴之王Lizard King蜥蜴女王Lizard Queen). Only an English word run marks that boundary:
# initialisms and codes (AHA成员, X射线戒指) are part of the Chinese name they open.
ADJACENT = re.compile(r'(?<=[A-Za-z])(\s*)(?=[㐀-鿿])(?!'+'|'.join(map(re.escape,JOINERS))+')')


SENTENCE = re.compile(r'[，。？！；?]')


def adjacent_cuts(text,han):
    """Boundaries where an English word run that follows Chinese text meets the next Chinese name."""
    cuts=[]
    for m in ADJACENT.finditer(text):
        start=m.start(1);run=start
        while run>0 and not han(text[run-1]):run-=1
        if run>0 and re.search(r'[a-z]',text[run:start]):cuts.append(m.span(1))
    return cuts


def parse_alternatives(text,result,han,latin,english,trim,spans):
    """Several bilingual names joined by conjunctions, such as a table row offering one price for
    alternative goods. Each name pairs its own Chinese and English text; a single English suffix
    would otherwise claim only the last one. Every segment must be a clean pair or nothing splits."""
    # A sentence that glosses its terms inline (所有魔法 黑暗Darkness 都会阻挡…？) is not a list of names.
    if SENTENCE.search(text):return None
    cuts=sorted([m.span(1) for m in ALTERNATIVE.finditer(text)]+adjacent_cuts(text,han))
    if not cuts:return None
    bounds=[0]+[x for cut in cuts for x in cut]+[len(text)]
    pairs=[]
    for a,b in zip(bounds[::2],bounds[1::2]):
        boundary=next((i for i in range(a,b) if latin(text[i]) and han(text[a:i]) and english(text[i:b])
                       and sum(c.isalnum() for c in text[i:b])>=2),None)
        if boundary is None or any(latin(ch) for ch in text[a:boundary]):return None
        pairs.append((trim(a,boundary),trim(boundary,b)))
    zh=[];en=[]
    for (za,zb),(ea,eb) in pairs:
        for language,(x,y),out in (('zh',(za,zb),zh),('en',(ea,eb),en)):
            out.append(compact(text[x:y]))
            result['parts'].append({'language':language,'text':out[-1],'spans':spans(x,y)})
    # Space before a conjunction separated the English word; space after it stays (或 1 头绵羊).
    # Names that met without one are joined by the slash the English projection uses.
    joiners=[re.sub(r'\s+',' ',text[a:b]).lstrip() or '/' for a,b in cuts]
    result['annotations'].extend({'role':'alternative_separator','text':text[a:b],'spans':spans(a,b)} for a,b in cuts)
    # The Chinese projection keeps the source's own conjunctions; the English one has none to keep.
    result['name_zh']=zh[0]+''.join(j+z for j,z in zip(joiners,zh[1:]))
    result['name_en']=' / '.join(en)
    result.update(status='parsed',rule='bilingual_alternatives')
    return result


LEVEL_LABEL = re.compile(r'\s*(\d+)\s*级\s*[:：]\s*')


def parse_name(raw):
    """Parse bilingual structure; all spans address the unchanged input name, not HTML.

    Parentheses wrapping a translation differ from qualifiers inside either name.
    Uncertain structure stays intact; display normalization never establishes identity.
    """
    raw=str(raw);removed=set();annotations=[]
    # Existing source editorial conventions, recorded separately from name content.
    for pattern in (r'（旧版）|\(旧版\)', r'Legacy\s*$', r'(?<![A-Za-z])new!(?=\s|$)'):
        for match in re.finditer(pattern,raw,re.I):
            removed.update(range(*match.span()))
            annotations.append({'role':'editorial_marker','text':match[0],'spans':[list(match.span())]})
    # A leading level label (3级：) says when a class grants the feature; it qualifies the name and is
    # recorded apart from it, so the display name and aliases read 绝望打击, not 3级：绝望打击.
    level=LEVEL_LABEL.match(raw)
    if level and raw[level.end():].strip():
        removed.update(range(*level.span()))
        annotations.append({'role':'level_label','text':level[0],'value':int(level[1]),'spans':[list(level.span())]})
    positions=[i for i in range(len(raw)) if i not in removed]
    while positions and raw[positions[0]] in ' \t\r\n｜|：:':positions.pop(0)
    while positions and raw[positions[-1]] in ' \t\r\n｜|：:':positions.pop()
    text=''.join(raw[i] for i in positions)
    result={'raw':raw,'offset_basis':'raw_name_codepoints','status':'single_language','rule':'single_language',
            'name_zh':'','name_en':'','parts':[],'groups':[],'annotations':annotations}
    han=lambda s:bool(re.search(r'[\u3400-\u9fff]',s))
    latin=lambda ch:ch.isalpha() and 'LATIN' in unicodedata.name(ch,'')
    def english(s):
        # A qualifier belongs to its surrounding name even when its explanatory
        # text is in another language. Only the ungrouped name establishes language.
        grouped={'(':')','（':'）','[':']','【':'】'};nest=[];seen=False
        for ch in s:
            if ch in grouped:nest.append(grouped[ch]);continue
            if nest:
                if ch==nest[-1]:nest.pop()
                continue
            if ch.isalpha():
                if not latin(ch):return False
                seen=True
        return seen and not nest
    def trim(a,b,separators=''):
        while a<b and (text[a].isspace() or text[a] in separators):a+=1
        while b>a and (text[b-1].isspace() or text[b-1] in separators):b-=1
        return a,b
    def spans(a,b):
        ranges=[]
        for i in positions[a:b]:
            if ranges and ranges[-1][1]==i:ranges[-1][1]=i+1
            else:ranges.append([i,i+1])
        return ranges
    def part(language,a,b):
        a,b=trim(a,b)
        value=compact(text[a:b]);result['name_'+language]=value
        result['parts'].append({'language':language,'text':value,'spans':spans(a,b)})
    def unsplit(reason=None):
        if reason:result.update(status='review',rule=reason)
        if text:part('zh' if han(text) else 'en',0,len(text))
        else:result.update(status='review',rule='empty_name')
        return result
    pairs={'(':')','（':'）','[':']','【':'】','“':'”','「':'」','『':'』','"':'"'}
    stack=[];groups=[];depth=[]
    for i,ch in enumerate(text):
        depth.append(len(stack))
        if ch=='"' and stack and stack[-1][1]=='"':
            start,_=stack.pop();groups.append((start,i+1,len(stack)))
        elif ch in pairs:stack.append((i,ch))
        elif ch in pairs.values():
            if not stack or pairs[stack[-1][1]]!=ch:return unsplit('unbalanced_brackets')
            start,_=stack.pop();groups.append((start,i+1,len(stack)))
    if stack:return unsplit('unbalanced_brackets')
    if han(text) and not groups:
        alternatives=parse_alternatives(text,result,han,latin,english,trim,spans)
        if alternatives:return alternatives
    boundary=None;wrapper=None
    if han(text):
        # A language transition is allowed only outside balanced groups. Prefer the
        # whole English suffix, so English Name (Alias) keeps its internal qualifier.
        for i,ch in enumerate(text):
            if (depth[i]==0 and (latin(ch) or ch in '“「『"') and han(text[:i])
                    and english(text[i:]) and sum(c.isalnum() for c in text[i:])>=2):
                boundary=i
                # Digits glued to the English word belong to it (2nd-Level, 13th-Level).
                while boundary>0 and text[boundary-1].isdigit():boundary-=1
                if not han(text[:boundary]):boundary=i
                # Keep a separated numeric qualifier with the following English name.
                qualifier=re.search(r'(?<!\S)[+−-]?\d+\s+$',text[:boundary])
                if qualifier and han(text[:qualifier.start()]):boundary=qualifier.start()
                break
        if boundary is None:
            for a,b,level in groups:
                if level==0 and b==len(text) and text[a] in '(（' and han(text[:a]) and english(text[a+1:b-1]):
                    boundary=a;wrapper=(a,b);break
    # A preceding English-only group followed by another English suffix could be
    # a second translation or a qualifier. Do not assign it to the Chinese field.
    if boundary is not None and any(b<=boundary and english(text[a+1:b-1]) for a,b,_ in groups):
        boundary=None;wrapper=None
    # An English word run inside the Chinese field is a translation the pairs above could not place.
    if boundary is not None and adjacent_cuts(text[:boundary],han):
        boundary=None;wrapper=None
    if boundary is None:
        unsplit('mixed_name_unresolved' if han(text) and any(latin(ch) for ch in text) else None)
    else:
        a,b=trim(0,boundary,'｜|')
        part('zh',a,b)
        start,end=(wrapper[0]+1,wrapper[1]-1) if wrapper else (boundary,len(text))
        part('en',start,end)
        result.update(status='parsed',rule='wrapped_translation' if wrapper else 'bilingual_suffix')
    for a,b,_ in sorted(groups):
        is_wrapper=wrapper==(a,b)
        language=None
        if is_wrapper:role='translation_wrapper'
        elif result['status']=='review':role='undetermined'
        else:
            role='quoted_name' if text[a] in '“「『"' else 'qualifier'
            language=('en' if a>=boundary else 'zh') if boundary is not None else ('zh' if han(text) else 'en')
        result['groups'].append({'role':role,'language':language,'text':text[a:b],'spans':spans(a,b),
            'delimiter_spans':spans(a,a+1)+spans(b-1,b)})
    return result


def split_name(text):
    """Two-field projection of the shared structural parser for existing consumers."""
    parsed=parse_name(text)
    return parsed['name_zh'],parsed['name_en']


# Page structure names an entry's role where a title or a folder name does not. A class prints a hit
# die and a core-traits table; its features begin at 1st level. A subclass prints only the features
# it adds, which begin at 3rd level and carry no core traits. These are layouts, not name whitelists.
CLASS_STRUCTURE=re.compile(r'生命[值]?骰[\s\S]{0,40}?每\s*([^\s，。；：:]{1,12}?)\s*等级')
CLASS_TRAITS=re.compile(r'核心特质|熟练加值|熟练项')
LEVEL_HEADING=re.compile(r'^\s*(\d{1,2})\s*级\s*[:：]')
CONDITION_TOPIC_NAMES={'状态'}
CONDITION_STRUCTURE=re.compile(r'^(?:状态|持续时间|状态不叠加|豁免|概述|简介|来源|结束)(?:\s|[A-Za-z]|$)')
# Environmental hazards share a section but not a title scheme. Inside such a section, a page or heading
# is a hazard when its body states a danger or an effect: a save, damage, an effect table, a trigger.
HAZARD_CONTEXT=re.compile(r'/(?:环境灾害|环境危害)/')
HAZARD_EVIDENCE=re.compile(r'DC\s*\d+|豁免|伤害|效应|效果|危险|致命|灾害|表|触发|检定|陷入|力竭')


def hazard_context(path):
    """The environmental-hazard section provides the category; the body supplies the evidence."""
    return bool(HAZARD_CONTEXT.search(path))


def hazard_structure(text):
    """A hazard body states a danger, a saving throw, damage, an effect, or an effect table."""
    return bool(HAZARD_EVIDENCE.search(text[:6000]))


def class_structure(text,title=''):
    """A hit die plus core traits describes a class page, whatever its name or directory.

    The hit die must name the class it belongs to (每战士等级), so a rule that mentions hit dice
    in general (每当你获得新的等级) is not a class page.
    """
    head=text[:8000]
    if not CLASS_TRAITS.search(head):return False
    m=CLASS_STRUCTURE.search(head)
    if not m:return False
    label=compact(m[1])
    if not title:return True
    return label in CLASSES or label in title


def level_heading(name):
    """The level a class or subclass feature heading states, or None for prose and range headings."""
    m=LEVEL_HEADING.match(name)
    return int(m[1]) if m else None


def document_structure_name(name):
    """Names that label a page or a catalog rather than the entry the page is about."""
    return bool(GENERIC.fullmatch(name)) or bool(re.fullmatch(r'(?:专长|法术|魔法物品|装备|背景|种族|物种)(?:描述|详述|列表|总览)(?:\s*[A-Za-z].*)?',name))


def page_topic(name):
    """A page title that gives its child definitions a shared type."""
    zh,en=split_name(name)
    zh=re.sub(r'^附录[A-Za-z]?[：:]\s*','',zh)
    return 'condition' if key(zh) in CONDITION_TOPIC_NAMES else ''


def condition_heading(name):
    """A bilingual child title on a condition page, excluding the page's own rule headings."""
    zh,en=split_name(name)
    return bool(zh and en and len(name)<80 and not CONDITION_STRUCTURE.match(zh))


def refine_page_types(entries,text):
    """Second pass: the page's primary entry types the headings beneath it.

    Classification of a heading cannot know what its page is until the page's own primary entry
    has been classified. This pass applies that context: a class or subclass owns its level
    features, and a condition topic owns its condition definitions.
    """
    if not entries:return
    primary_index=1 if len(entries)>1 and entries[0]['boundary_rule']=='page_intro' else 0
    primary=entries[primary_index]
    full=text[primary['start']:primary['end']]
    structured=not document_structure_name(primary['name'])
    # A stat block owns its page's identity; the framing prose around it is not a second page-level type.
    if primary['type']=='hazard' and any(e is not primary and e['type']=='creature' for e in entries):
        primary['type']='section';primary['classification_rule']='document_structure'
    if primary['type']=='section' and structured and class_structure(full,primary['name']):
        primary['type']='class';primary['classification_rule']='class_structure'
    levels=[(e,level_heading(e['name'])) for e in entries if e is not primary and e['type'] in {'section','class_feature'}]
    levels=[(e,n) for e,n in levels if n is not None]
    # A subclass adds only features above the class's own levels; a class always prints 1st level.
    if primary['type']=='section' and structured and len(levels)>=2 and min(n for _,n in levels)>=3:
        primary['type']='subclass';primary['classification_rule']='subclass_structure'
    if primary['type'] in {'class','subclass'}:
        for e,n in levels:
            e['type']='class_feature'
            e['classification_rule']='level_heading' if primary['type']=='class' else 'parent_class_context'
    if page_topic(primary['name'])=='condition':
        if primary['type']=='section':
            primary['type']='rule';primary['classification_rule']='topic_context'
        for e in entries:
            if e is primary or e['type']!='section':continue
            if condition_heading(e['name']):e['type']='condition';e['classification_rule']='topic_context'


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

    def rule_children(self, start, end, parent_name):
        """Find independent rule terms, not every emphasized subheading in a rule."""
        parent_zh,parent_en=split_name(parent_name)
        def family(name):
            zh,en=split_name(name)
            return any(p and key(p)!=key(n) and key(p) in key(n)
                       for p,n in ((parent_zh,zh),(parent_en,en)))
        def usable(tag):
            return not (tag.find_parent(['table','a','blockquote']+list(HEADINGS))
                        or any(EXAMPLE_CLASSES.intersection(p.get('class',[])) for p in tag.parents))
        # A declared series of marked terms, each with its own parenthesized
        # rule text, differs from a list of names or an incidental gloss.
        for block in self.body.find_all('p'):
            lo,hi=self.span(block)
            if lo<start or hi>end or not usable(block):continue
            terms={}
            for tag in block.find_all('u',recursive=False):
                a,b=self.span(tag);name=self.text[a:b]
                if re.fullmatch(r'[\u3400-\u9fff]{2,24}',name):terms[a]=b
            for intro in re.finditer(r'([^。！？；\n：:]+)[：:]\s*',self.text[lo:hi]):
                lead=intro[1]
                if not re.search(r'(?:[两二三四五六七八九十\d]+种|分为|包括|下列|以下|分别)',lead):continue
                if re.search(r'例如|比如|举例|譬如',lead):continue
                items=[];a=lo+intro.end()
                while a in terms:
                    b=terms[a];tail=self.text[b:hi]
                    opening=re.match(r'\s*([（(])',tail)
                    if not opening:break
                    stack=[opening[1]]
                    for pos in range(b+opening.end(),min(hi,b+180)):
                        ch=self.text[pos]
                        if ch in '（(':stack.append(ch)
                        elif ch in '）)':
                            if ch!=('）' if stack[-1]=='（' else ')'):break
                            stack.pop()
                            if not stack:break
                        elif ch=='\n':break
                    if stack:break
                    gloss=compact(self.text[b+opening.end():pos])
                    if len(gloss)<4 or not re.search(r'[\u3400-\u9fff]',gloss):break
                    items.append((a,b,pos+1,self.text[a:b]))
                    separator=re.match(r'\s*[、，,]\s*',self.text[pos+1:hi])
                    if not separator:
                        after=self.text[pos+1:hi].lstrip()
                        if len(items)>=2 and len({key(t[3]) for t in items})==len(items) and (not after or after[0] in '。；;.!！？'):
                            yield from ((*t,'rule_term_gloss_list') for t in items)
                        break
                    a=pos+1+separator.end()

        # The other layout is parallel lines inside rule prose: either a
        # bold bilingual lead-in or a bold name in a repeated rule clause.
        # Only independently named variants of the enclosing term are
        # exported; generic labels like "Effects" don't become global terms.
        lines=defaultdict(list)
        for tag in self.body.find_all(['b','strong']):
            a,b=self.span(tag)
            if not start<=a<b<=end or tag.find_parent(['b','strong']) or not usable(tag):continue
            line=self.text.rfind('\n',start,a)+1
            if line<start:line=start
            stop=self.text.find('\n',a,end)
            if stop<0:stop=end
            if b>stop:continue
            lines[(line,stop)].append((a,b))
        siblings=defaultdict(list)
        for (line,stop),parts in lines.items():
            a,b=parts[0]
            for x,y in parts[1:]:
                if compact(self.text[b:x]):break
                b=y
            prefix=compact(self.text[line:a])
            raw=compact(self.text[a:b])
            name=raw.rstrip('。:：')
            zh,en=split_name(name)
            if not (zh and en and len(name)<=80 and not re.search(r'[。！？；：:]',name)):continue
            tail=compact(self.text[b:stop])
            if len(tail)<8 or not re.search(r'[。！？]',tail):continue
            if not prefix and raw!=name:
                signature='inline_heading'
            elif re.fullmatch(r'(?:当(?:你|目标|生物)?)?(?:处于|具有|获得|陷入|使用|执行|受到)',prefix):
                continuation=re.match(r'(?:下的|时[，,]|期间|中的|中[，,])',tail)
                if not continuation:continue
                signature=(prefix,continuation[0])
            else:continue
            siblings[signature].append((a,b,b+len(self.text[b:stop].rstrip()),name))
        for group in siblings.values():
            if len({key(t[3]) for t in group})!=len(group) or len(group)<2:continue
            yield from ((*t,'rule_parallel_lead') for t in group if family(t[3]))

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
        class_context=bool(re.search(r'/(?:'+ '|'.join(re.escape(x) for x in CLASSES) +r')(?:/|[-（(.])|/(?:角色职业|职业|子职|子职业)/',self.path)) or class_structure(self.text,self.title)
        if class_context:
            for block in self.body.find_all('p'):
                if block.find_parent(['table','li']):continue
                ba,bb=self.span(block)
                headings=[]
                for tag in block.find_all(['u','b','strong']):
                    a,b=self.span(tag);name=compact(self.text[a:b])
                    if compact(self.text[ba:a]):continue
                    # A formatted heading may span adjacent tags (including its
                    # closing quote). Failed name segmentation must not erase an
                    # already established heading or its child ability boundaries.
                    existing=next((c for c in items if c.start==a and c.method in {'heading','colored_heading','word_heading'}),None)
                    if re.match(r'^\d+\s*级\s*[:：]',name) and (split_name(name)[1] or existing) and not re.search(r'[。；！？]',name):
                        if not existing and not any(c.start==a and c.title_end==b for c in items):
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
    if document_structure_name(name):return 'section','document_structure'
    if primary and class_structure(text,name):return 'class','class_structure'
    if re.match(r'^(?:毒药的(?:制作|提取)|采购毒药|制作毒药|提取毒药)',name):return 'rule','context_heading'
    if re.search(r'^(?:子职|子职业|特色设施详述|疾病范例|毒药范例|毒药的(?:制作|提取)|毒药|疾病|专长与背景)(?:\s*[A-Za-z].*)?$',name):return 'rule','context_heading'
    if re.fullmatch(r'(?:动作|附赠动作|反应|特质|传奇动作|巢穴动作)\s*(?:Actions?|Bonus Actions?|Reactions?|Traits?|Legendary Actions?|Lair Actions?)?',name,re.I):
        return ('rule','context_heading') if re.search(r'术语|进行游戏|/战斗/',path) else ('section','document_structure')
    if hazard_context(path) and hazard_structure(text):return 'hazard','hazard_structure'
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
