"""View-specific quick-reference semantics. Derived values never overwrite source fields."""
from collections import Counter
from fractions import Fraction
from functools import lru_cache
import json
import math
import re
import unicodedata

UNKNOWN = '__unknown__'
CONFLICT = '__conflict__'
NONE = '__none__'
LABELS = {
    'spell_level':'环阶', 'school':'学派', 'spell_classes':'职业', 'casting_time':'施法时间',
    'concentration':'专注', 'ritual':'仪式', 'verbal':'言语', 'somatic':'姿势', 'material':'材料',
    'special_material':'特殊材料', 'rarity':'稀有度', 'item_type':'物品类别', 'attunement':'同调',
    'size':'体型', 'creature_type':'生物类型', 'legendary_actions':'传奇动作', 'cr':'CR',
    'feat_category':'专长类别', 'role':'使用角色', 'mastery':'精通', 'level':'等级',
}
COLUMN_LABELS = {**LABELS, 'components':'成分', 'spell_markers':'仪式/专注', 'attunement_requirements':'同调条件','item_subtypes':'子类别','prerequisite':'前置条件','ac':'AC','hp':'HP','price':'价格','carrying_capacity':'载重','speed':'速度','weight':'重量','damage':'伤害'}
BOOL_FIELDS = {'concentration','ritual','verbal','somatic','material','special_material','attunement','legendary_actions'}
CLASSES = {'诗':'吟游诗人','吟':'吟游诗人','牧':'牧师','德':'德鲁伊','帕':'圣武士','圣':'圣武士','软':'游侠','游':'游侠','术':'术士','法':'法师','锁':'魔契师','契':'魔契师','邪':'魔契师','邪术师':'魔契师','械':'奇械师','魔':'魔战士','无':'无职'}
LEVELS = ['戏法']+[x+'环' for x in '一二三四五六七八九']
ORDERS = {
    'spell_level':list(map(str,range(10))),
    'level':list(map(str,range(1,21))),
    'school':['防护','咒法','预言','惑控','塑能','幻术','死灵','变化'],
    'spell_classes':['吟游诗人','牧师','德鲁伊','圣武士','游侠','术士','法师','魔契师','奇械师','魔战士','无职'],
    'casting_time':['动作','附赠动作','反应','其他'],
    'rarity':['普通','非普通','珍稀','极珍稀','传说','神器','多种稀有度','稀有','罕见','独特'],
    'size':['微型','小型','中型','大型','巨型','超巨型'],
    'creature_type':['异怪','野兽','天族','构装','龙类','元素','妖精','邪魔','巨人','类人','怪兽','泥怪','植物','亡灵'],
    'feat_category':['起源','通用','战斗风格','传奇恩惠','种族','血族'],
}
# Table columns may be composites of boolean fields; CSV export keeps every part as its own column.
COMPOSITES = {'components':['verbal','somatic','material'], 'spell_markers':['ritual','concentration']}
SCHEMAS = {
    'spell': {'facets':['spell_level','school','spell_classes','casting_time'],
              'flags':['concentration','ritual','verbal','somatic','material','special_material'],
              'columns':['name','spell_level','school','spell_classes','casting_time','components','spell_markers','sources','ruleset'],
              'export_columns':['name','spell_level','school','spell_classes','casting_time','verbal','somatic','material','ritual','concentration','sources','ruleset'],
              'sorts':['source','name','name_en','spell_level','school','casting_time'], 'default_sort':'spell_level'},
    'creature': {'facets':['size','creature_type','role'], 'flags':['legendary_actions'], 'range':'cr',
                 'columns':['name','size','creature_type','cr','ac','hp','legendary_actions','sources','ruleset'],
                 'sorts':['source','name','name_en','cr','size','creature_type','ac','hp'], 'default_sort':'cr'},
    'item': {'facets':['rarity','item_type'], 'flags':['attunement'],
             'columns':['name','rarity','item_type','item_subtypes','attunement','attunement_requirements','sources','ruleset'],
             'sorts':['source','name','name_en','rarity','item_type'], 'default_sort':'rarity'},
    'feat': {'facets':['feat_category'], 'flags':[],
             'columns':['name','feat_category','prerequisite','sources','ruleset'],
             'sorts':['source','name','name_en','feat_category'], 'default_sort':'name'},
    'equipment': {'facets':['mastery'], 'flags':[],
                  'columns':['name','price','weight','damage','mastery','sources','ruleset'],
                  'sorts':['source','name','name_en','mastery'], 'default_sort':'name'},
    'feature': {'facets':['level'], 'flags':[], 'columns':['name','level','sources','ruleset'],
                'sorts':['source','name','name_en','level'], 'default_sort':'level'},
    'animal': {'facets':['role'], 'flags':[], 'columns':['name','role','price','carrying_capacity','speed','sources','ruleset'], 'sorts':['source','name','name_en'],'default_sort':'source'},
    'generic': {'facets':[], 'flags':[], 'columns':['name','type','sources','ruleset','classification_status','legacy'],
                'sorts':['source','name','name_en','type','ruleset'], 'default_sort':'type'},
}


def configure(kind):
    return finish({'kinds':[kind], **SCHEMAS[kind]})


def finish(config):
    config.setdefault('export_columns', config['columns'])
    config['composites'] = {k:COMPOSITES[k] for k in config['columns'] if k in COMPOSITES}
    return config


# An entry's own type picks its quick-reference schema wherever it is read, whichever filter listed it.
TYPE_KINDS = {'spell':'spell', 'creature':'creature', 'magic_item':'item', 'equipment':'equipment', 'feat':'feat', 'animal_offer':'animal', 'class_feature':'feature'}
GENERIC = 'generic'


def combine(kinds):
    """Mixed kinds read as the generic table (name, type, then identity columns)
    widened by each kind's own columns, facets and sorts, in the kinds' reading order."""
    base,own = SCHEMAS[GENERIC],[SCHEMAS[k] for k in kinds]
    merge = lambda key:list(dict.fromkeys(x for s in [base,*own] for x in s.get(key,[])))
    lead = [c for c in base['columns'] if c not in IDENTITY_COLUMNS or c in {'name','type'}]
    tail = [c for c in base['columns'] if c not in lead]
    readings = lambda key:[c for s in own for c in s.get(key,s['columns']) if c not in IDENTITY_COLUMNS]
    return finish({'kinds':[GENERIC,*kinds], 'facets':merge('facets'), 'flags':merge('flags'),
                   'range':next((s['range'] for s in own if s.get('range')),None),
                   'columns':list(dict.fromkeys(lead+readings('columns')+tail)),
                   'export_columns':list(dict.fromkeys(lead+readings('export_columns')+tail)),
                   'sorts':merge('sorts'), 'default_sort':base['default_sort']})


def type_conditions(entry_type):
    """The type-specific conditions an entry type can answer: its kind's facets, flags and range."""
    own=SCHEMAS[TYPE_KINDS.get(entry_type,GENERIC)]
    return [*own['facets'],*own['flags'],*([own['range']] if own.get('range') else [])]


def schema_for(types):
    """The table a set of entry types reads as: one kind's own schema, or the generic table
    widened by every kind present when types mix (装备 lists mundane gear beside magic items)."""
    types = sorted(set(types),key=lambda t:TYPE_ORDER.index(t) if t in TYPE_ORDER else len(TYPE_ORDER))
    kinds = list(dict.fromkeys(TYPE_KINDS.get(t,GENERIC) for t in types))
    if len(kinds)<=1:return configure(kinds[0] if kinds else GENERIC)
    return combine([k for k in kinds if k!=GENERIC])


# Presets name a common slice of one type: the type plus one facet value. They are ordinary filters
# (the reader sees and removes the facet like any other); the type picker offers them beside their type.
PRESETS = (
    {'name':'NPC','type':'creature','field':'role','value':'NPC'},
    {'name':'伙伴','type':'creature','field':'role','value':'伙伴'},
    {'name':'坐骑','type':'creature','field':'role','value':'坐骑'},
    {'name':'魔法武器','type':'magic_item','field':'item_type','value':'武器'},
    {'name':'战斗风格','type':'feat','field':'feat_category','value':'战斗风格'},
    {'name':'传奇恩惠','type':'feat','field':'feat_category','value':'传奇恩惠'},
)


# These fields name or locate a version (what it is, which book, which rules); they are not readings of its rules.
METADATA_FIELDS = ('type','source_id','source_code','ruleset','legacy')
DETAIL_SUFFIX = '_detail'


def field_order(entry):
    """Reader order: the type's quick-reference columns as the table shows them, the remaining rule
    fields as extracted with each X_detail right after X, then the version's metadata."""
    fields=entry['fields'];config=configure(TYPE_KINDS.get(entry['type'],GENERIC))
    lead=[f for column in config['columns'] for f in [column,*config['composites'].get(column,[])]]
    rules=[f for f in fields if f not in METADATA_FIELDS]
    ordered=[f for f in dict.fromkeys(lead) if f in rules]+[f for f in rules if f not in lead]
    details=[f for f in ordered if f.endswith(DETAIL_SUFFIX) and f[:-len(DETAIL_SUFFIX)] in ordered]
    for f in details:
        ordered.remove(f);ordered.insert(ordered.index(f[:-len(DETAIL_SUFFIX)])+1,f)
    return ordered+[f for f in METADATA_FIELDS if f in fields]


def scalar_values(v):
    return v if isinstance(v,list) else ([] if v is None else [v])


def number(v):
    try:
        text=str(v).strip()
        if not re.fullmatch(r'\d+(?:/\d+|\.\d+)?',text):return None
        result=float(Fraction(text))
        return result if math.isfinite(result) else None
    except (ValueError,ZeroDivisionError):return None


def normalized(fields, field):
    """Exact tokens, explicit missing/conflict states, no absence-as-false inference."""
    raw=fields.get('material' if field=='special_material' else field)
    out=[]
    for value in scalar_values(raw):
        if value is None or value=='':continue
        text=str(value).strip()
        if field=='spell_level':
            if text in LEVELS:out.append(str(LEVELS.index(text)))
            elif re.fullmatch(r'[0-9]环',text):out.append(text[0])
            elif text=='零环':out.append('0')
            else:out.append(text)
        elif field=='spell_classes':
            out.extend(CLASSES.get(x,x) for x in re.split(r'[\s、,，/；;]+',text) if x)
        elif field=='casting_time':
            out.append({'附赠':'附赠动作','1个动作':'动作','一动作':'动作'}.get(text,text))
        elif field=='size':out.append('超巨型' if text=='超型' else text)
        elif field=='creature_type':out.extend(x for x in re.split(r'[/／]',text) if x)
        elif field in BOOL_FIELDS:
            if field=='material':
                result={'M':'true','M*':'true','×':'false'}.get(text)
            elif field=='special_material':
                result={'M':'false','M*':'true','×':'__na__'}.get(text)
            else:result={'True':'true','False':'false','是':'true','否':'false','有':'true','无':'false','√':'true','×':'false'}.get(text)
            out.append(result or text)
        else:out.append(text)
    out=list(dict.fromkeys(out))
    if field in BOOL_FIELDS and len(out)>1:return [CONFLICT]
    return out or [UNKNOWN]


@lru_cache(maxsize=4096)
def parse_fields(raw):return json.loads(raw)


def field_values(raw,field):return json.dumps(normalized(parse_fields(raw),field),ensure_ascii=False)


def sort_value(raw,field):
    vals=normalized(parse_fields(raw),field)
    if len(vals)!=1 or vals[0].startswith('__'):return None
    v=vals[0]
    if field in {'cr','ac','hp','spell_level','level'}:return number(v)
    if field in ORDERS:return ORDERS[field].index(v) if v in ORDERS[field] else None
    return v.casefold()


def name_key(text):
    """Name collation: digit runs compare by value (2级 before 10级); case and full/half width are ignored."""
    if not text:return None
    return re.sub(r'\d+',lambda m:m[0].zfill(8),unicodedata.normalize('NFKC',text).casefold())


# Mixed results list kinds in the order a reader meets them: character options, spells, gear, creatures, then rules.
TYPE_ORDER = ('class','subclass','class_feature','species','background','feat','spell','equipment','magic_item','creature',
              'animal_offer','vehicle','reward','facility','condition','action','rule','hazard','trap','poison','disease',
              'subsystem','lore','section')
TYPE_RANK = 'CASE e.type '+' '.join(f"WHEN '{t}' THEN {i}" for i,t in enumerate(TYPE_ORDER))+f' ELSE {len(TYPE_ORDER)} END'


def register(db):
    db.create_function('browse_values',2,field_values,deterministic=True)
    db.create_function('browse_rank',2,sort_value,deterministic=True)
    db.create_function('browse_name',1,name_key,deterministic=True)
    db.create_function('browse_match',4,match_rank,deterministic=True)
    db.create_function('browse_query',0,lambda:'')


def facet_conditions(params):
    """One condition per facet field the request constrains, keyed by its URL parameter; a CR range is one condition."""
    out=[]
    for field in LABELS:
        chosen=list(dict.fromkeys(params.get('f_'+field,[])))
        if not chosen:continue
        if len(chosen)>200 or any(len(x)>200 for x in chosen):raise ValueError('筛选值过多或过长')
        if chosen==[NONE]:out.append(('f_'+field,'0',[]));continue
        out.append(('f_'+field,'EXISTS(SELECT 1 FROM json_each(browse_values(e.fields,?)) f WHERE f.value IN ('+','.join('?' for _ in chosen)+'))',[field]+chosen))
    bounds={}
    for name in ['cr_min','cr_max']:
        if params.get(name):
            value=number(params[name][0])
            if value is None:raise ValueError('CR 必须是非负数字或分数')
            bounds[name]=value
    if bounds.get('cr_min',0)>bounds.get('cr_max',math.inf):raise ValueError('最低 CR 不能大于最高 CR')
    if bounds:
        sql=' AND '.join("browse_rank(e.fields,'cr')"+('>=?' if name=='cr_min' else '<=?') for name in bounds)
        out.append(('cr',sql,list(bounds.values())))
    return out


MATCH = 'match'


def match_rank(zh, en, name, query):
    """How well a name matches the search: 0 an exact name, 1 a name starting with it, 2 one containing it.
    Chinese and English names are compared separately, so 火球 is a prefix of 火球术 Fireball."""
    words=lambda text:unicodedata.normalize('NFKC',text or '').casefold().strip()
    q=words(query);names=[words(n) for n in (zh,en) if n] or [words(name)]
    if not q:return 2
    return 0 if q in names else 1 if any(n.startswith(q) for n in names) else 2


def search_terms(params):
    """The kept name searches, in the order the reader added them; every one must match."""
    terms=list(dict.fromkeys(t.strip() for t in params.get('q',[]) if t.strip()))
    if len(terms)>20 or any(len(t)>200 for t in terms):raise ValueError('关键词过多或过长')
    return terms


def body_terms(params):
    """Searches in an entry's text after its title; every one must match."""
    terms=list(dict.fromkeys(t.strip() for t in params.get('body',[]) if t.strip()))
    if len(terms)>20 or any(len(t)>200 for t in terms):raise ValueError('正文关键词过多或过长')
    return terms


def typing_term(params):
    """The name search still being typed (`typing`): it filters like a kept one until Enter keeps it."""
    term=params.get('typing',[''])[0].strip()
    if len(term)>200:raise ValueError('关键词过长')
    return term


def latest_term(params):
    """Match ranking follows the search the reader is typing, else the one kept last."""
    return typing_term(params) or (search_terms(params) or [''])[-1]


def sort_key(params, default):
    """The sort in effect: an explicit choice, else match quality while searching, else the table's default."""
    searching=bool(latest_term(params))
    sort=params.get('sort',[MATCH if searching else default])[0]
    return default if sort==MATCH and not searching else sort


def order_sql(params, default='name'):
    """ORDER BY for the effective sort. Match ranking reads the latest search term through browse_query(),
    which the caller registers on its connection (one connection per request)."""
    sort=sort_key(params,default)
    direction=params.get('dir',['asc'])[0]
    if direction not in {'asc','desc'}:raise ValueError('无效排序方向')
    direct={'name':"browse_name(COALESCE(NULLIF(e.name_zh,''),e.name))",'name_en':"browse_name(NULLIF(e.name_en,''))",
            'type':TYPE_RANK,'ruleset':'e.ruleset','source':'e.page_path'}
    if sort==MATCH:
        # Within one match class, shorter names are closer matches; then the table's usual order.
        rank="browse_match(e.name_zh,e.name_en,e.name,browse_query())"
        return f"{rank} {direction.upper()},length(COALESCE(NULLIF(e.name_zh,''),e.name)),"+order_sql({'sort':[default]},default)
    sortable={x for s in SCHEMAS.values() for x in s['sorts']}
    if sort not in sortable:raise ValueError('无效排序')
    expr=direct.get(sort) or "browse_rank(e.fields,'"+sort+"')"
    # NULL (missing, ambiguous numeric rank) remains last in either direction.
    return f'({expr} IS NULL),{expr} {direction.upper()},'+('e.start ASC,' if sort=='source' else '')+'e.name,e.id'


# Same-name candidates are stacked only where the English name identifies one published object
# (a spell, creature, item...). Class features are scoped by class, and rule or lore sections
# share generic headings, so equal names there do not mean versions of one thing.
STACKABLE_TYPES = ('spell','magic_item','creature','feat','equipment','species','background','class','subclass',
                   'vehicle','animal_offer','reward','condition','action','trap','poison','disease','hazard','facility')
RULESET_PREFERENCES = {'5r':('5r','5e'), '5e':('5e','5r')}
# Columns that name or locate a version differ by definition; they are never reported as rule differences.
IDENTITY_COLUMNS = {'name','sources','ruleset','type','classification_status','legacy'}
COMPARE_EXTRA = {'components':['special_material']}


def comparable(fields, field):
    """Normalized value for version comparison; unknown, conflicting or inapplicable values never differ."""
    values=normalized(fields,field)
    if any(v.startswith('__') for v in values):return None
    return tuple(sorted(unicodedata.normalize('NFKC',re.sub(r'\s+','',v)).casefold() for v in values))


def values_differ(a, b, field):
    x,y=comparable(a,field),comparable(b,field)
    return x is not None and y is not None and x!=y


def column_fields(config, column):
    return [*config['composites'].get(column,[column]),*COMPARE_EXTRA.get(column,[])]


def version_diffs(config, head, others):
    """Table columns whose values differ between the displayed version and each other version."""
    out={}
    for column in config['columns']:
        if column in IDENTITY_COLUMNS:continue
        ids=[o['id'] for o in others if any(values_differ(head['fields'],o['fields'],f) for f in column_fields(config,column))]
        if ids:out[column]=ids
    return out


def option_label(field,value):
    if value==UNKNOWN:return '未提取'
    if value==CONFLICT:return '冲突'
    if value=='__na__':return '无材料'
    if field=='spell_level' and value in ORDERS['spell_level']:return LEVELS[int(value)]
    if field in BOOL_FIELDS:return {'true':'是','false':'否'}.get(value,value)
    return value


def option_order(field,value):
    ordered=ORDERS.get(field,['true','false','特殊'])
    if value in ordered:return (0,ordered.index(value),'')
    if value==UNKNOWN:return (3,0,'')
    if value==CONFLICT:return (2,0,'')
    return (1,0,value)


def enrich(row):
    fields=row['fields'];row['quickref']={f:normalized(fields,f) for f in LABELS}
    return row
