"""Evidence-bounded level tables and acquisition edges, not character evaluation."""
from copy import deepcopy
import re
from rule_conditions import CLASSES, conjunction
from rule_model import rule_value
from rule_values import evidence
from table_extract import table_grid


def compact(text):return re.sub(r'\s+', '', text)


def class_requirement(entry, context):
    text=context.pages[entry['page_path']]
    ev=evidence(entry['page_path'],text,entry['start'],entry['title_end'])
    level=re.match(r'\s*(\d+)级[：:]',ev['raw'])
    owner=context.class_owner(entry)
    if not level or not 1<=int(level[1])<=20 or not owner:return None,ev
    return {'kind':'class_level','class':owner['name_zh'],'owner_id':owner['id'],'minimum':int(level[1])},ev


def feature_access(entry, context):
    if entry['type']!='subclass':return None
    children=[e for e in context.entries.values() if e.get('parent_id')==entry['id'] and e['type']=='class_feature']
    grants=[];gaps=[]
    for child in sorted(children,key=lambda e:e['start']):
        condition,ev=class_requirement(child,context)
        # A heading and its structural membership state acquisition, not its effects.
        if condition:
            grants.append({'id':'feature_'+child['id'],'entry_type':'class_feature','mode':'gain',
                'target':{'kind':'entry','name':child['name'],'resolution':{'status':'resolved','targets':[child['id']],
                'reason':'leveled_child'},'evidence':ev},'constraints':{},'when':condition,'branches':[], 'evidence':[ev]})
        else:gaps.append({'reason':'unresolved_feature_level','evidence':ev})
    return rule_value(grants=grants,unparsed=gaps) if children else None


def named_table(entry, context, label):
    captions=[e for e in context.entries.values() if e['page_path']==entry['page_path'] and
              entry['title_end']<=e['start']<e['end']<=entry['end'] and e.get('table_span') and e['name_zh']==label]
    tables=[(e,t) for e in captions for t in context.tables if t['page_path']==entry['page_path'] and
            e['table_span']==[t['start'],t['end']]]
    if len(captions)!=1 or len(tables)!=1:raise ValueError('missing_or_competing_grant_table')
    caption,table=tables[0]
    try:grid=table_grid(table)
    except ValueError as exc:raise ValueError('invalid_grant_table_geometry') from exc
    return caption,table,grid


def spell_target(entry, context, span):
    text=context.pages[entry['page_path']];a,b=span
    while a<b and text[a].isspace():a+=1
    while b>a and text[b-1].isspace():b-=1
    raw=text[a:b]
    # The entire label must be a name; unknown suffixes cannot be discarded.
    match=re.fullmatch(r'([\u3400-\u9fff]+)(?:\s*([A-Za-z][A-Za-z\s’\'\-]*))?',raw)
    if not match:raise ValueError('unparsed_table_spell_name')
    names=[n.strip() for n in match.groups() if n]
    lookups=[{'name':n,'resolution':context.resolve(entry,n,'spell')} for n in names]
    viable=[x['resolution'] for x in lookups if x['resolution']['status'] in {'resolved','ambiguous'}]
    ids=sorted({i for r in viable or [x['resolution'] for x in lookups] for i in r['targets']})
    resolution={'targets':ids,'status':('resolved' if len(ids)==1 else 'ambiguous') if viable else 'review' if ids else 'unresolved',
                'reason':'printed_table_names','resolution_evidence':{'lookups':lookups}}
    return {'kind':'entry','name':raw,'lookup_names':names,'resolution':resolution,'evidence':evidence(entry['page_path'],text,a,b)}


def parse_table_access(entry, context):
    text=context.pages[entry['page_path']];body=text[entry['title_end']:entry['end']]
    lineage=re.search(r'从([^。\n]+?)表格中选择',body) if entry['type']=='species' and entry.get('is_primary') else None
    prepared=re.search(r'当你到达([^。\n]+?)表中特定的([^。\n]+?)等级时',body) if entry['type']=='class_feature' else None
    if not lineage and not prepared:return None
    ref=lineage or prepared;label=compact(ref[1]);value=rule_value()
    scope_start=entry['title_end']+ref.start() if lineage else entry['title_end']
    ev=evidence(entry['page_path'],text,scope_start,entry['end'])
    try:
        if lineage:
            prefix=body[:ref.start()]
            headings=list(re.finditer(r'(?:^|\n)\s*'+re.escape(label)+r'[A-Za-z][A-Za-z ]*。',prefix))
            # The named trait bounds its own preamble. A newline must not hide a
            # prerequisite between that heading and the table-selection clause.
            if len(headings)>1:raise ValueError('ambiguous_table_scope')
            intro=compact(prefix[headings[0].end():] if headings else prefix)
            if intro and (not headings or not re.fullmatch(r'你属于一支[\u3400-\u9fff]+血系，并因此获得了超自然能力。',intro)):
                raise ValueError('unparsed_table_prefix')
        caption,table,grid=named_table(entry,context,label)
        if caption['start']<=entry['title_end']+ref.end():raise ValueError('unsupported_table_scope')
        tail=text[table['end']:entry['end']].strip()
        if tail and (not lineage or not re.match(r'^[\u3400-\u9fff]+[A-Za-z][A-Za-z ]*[。.]',tail)):
            raise ValueError('unparsed_table_suffix')
        policy=evidence(entry['page_path'],text,scope_start,caption['start'])
        value['scope']={'kind':'table_grants','table_id':table['id'],'evidence':evidence(entry['page_path'],text,scope_start,table['end'])}
        value['evidence']=[policy]
        headers=[compact(c['text']) for c in grid[0]]
        if any(c['rowspan']!=1 or c['colspan']!=1 for c in grid[0]):raise ValueError('unsupported_grant_table_header')
        if lineage:
            grammar=(r'从'+re.escape(label)+r'表格中选择其一。你获得该血系的1级好处。'
                r'当你到达(\d+)级和(\d+)级时，你分别习得一道表格上更高级的法术；你时刻准备着这道习得的法术，且可以不消耗法术位施展此法术一次，当你完成一次长休时，你重获施展该道法术的能力。'
                r'你也可以用任何你拥有的相应环阶法术位施展该道法术。当你选择血系时，选择智力、感知、或魅力之一，该属性即是你用此特质施展法术时的施法属性。')
            match=re.fullmatch(grammar,compact(policy['raw']))
            if not match:raise ValueError('unparsed_table_policy')
            levels=[1,int(match[1]),int(match[2])]
            if not 1<levels[1]<levels[2]<=20 or headers!=['血系',*[str(n)+'级' for n in levels]]:raise ValueError('unsupported_grant_table_header')
            options=[];value['contexts']['lineage']={'kind':'choose_one','options':options,'evidence':policy}
            for ri,row in enumerate(grid[1:],1):
                if any(c['rowspan']!=1 or c['colspan']!=1 for c in row):raise ValueError('unsupported_grant_table_geometry')
                choice=row[0];name=choice['text'].strip()
                if not name or any(o['name']==name for o in options):raise ValueError('ambiguous_table_choice')
                option_id='row_'+str(ri);options.append({'id':option_id,'name':name,'evidence':evidence(entry['page_path'],text,*choice['span'])})
                selected={'kind':'option_selected','context_id':'lineage','option_id':option_id,'name':name}
                for ci,level in enumerate(levels,1):
                    cell=row[ci];cell_ev=evidence(entry['page_path'],text,*cell['span']);target_span=cell['span'];replacement=False
                    if ci==1:
                        match=re.fullmatch(r'\s*(?:(你的(?:黑暗视觉|速度)提升至\d+尺。))?你(?:习得|知晓)戏法(.+?)\s*。(每当你完成长休时，你可以将它替换为('+ '|'.join(CLASSES)+r')法术列表中的另一个戏法。)?\s*',cell_ev['raw'],re.S)
                        if not match:raise ValueError('unparsed_table_cell')
                        target_span=[cell['span'][0]+match.start(2),cell['span'][0]+match.end(2)]
                        if match[1]:value['descriptions'].append(evidence(entry['page_path'],text,cell['span'][0]+match.start(1),cell['span'][0]+match.end(1)))
                        replacement=bool(match[3]);replacement_class=match[4]
                    target=spell_target(entry,context,target_span)
                    if ci==1 and target['resolution']['status']=='resolved':
                        from browse import normalized
                        spell=context.entries[target['resolution']['targets'][0]]
                        if normalized(spell['fields'],'spell_level')!=['0']:raise ValueError('conflicting_table_spell_level')
                    gid='grant_'+str(ri)+'_'+str(level)
                    branches=[{'id':'branch_1','resource':{'kind':'not_stated'},'evidence':[cell_ev]}] if ci==1 else [
                        {'id':'branch_1','resource':{'kind':'spell_slot','consumed':False},'uses':{'quantity':{'kind':'constant','value':1},'per':'spell'},'recovery':{'event':'long_rest','amount':'all'},'evidence':[policy,cell_ev]},
                        {'id':'branch_2','resource':{'kind':'spell_slot','consumed':True,'count':1,'level':'appropriate'},'evidence':[policy,cell_ev]}]
                    value['grants'].append({'id':gid,'entry_type':'spell','mode':'known' if ci==1 else 'prepared','target':target,'branches':branches,
                        'when':conjunction(deepcopy(selected),{'kind':'level','minimum':level}),
                        'ability':{'kind':'choose_one','values':['智力','感知','魅力'],'evidence':policy},'evidence':[cell_ev,policy]})
                    if replacement:value['modifiers'].append({'kind':'replace_choice','trigger':'long_rest','count':1,'grant_id':gid,'when':deepcopy(selected),
                        'target':{'kind':'choice','count':1,'constraints':{'levels':[0],'class_list':{'kind':'fixed','values':[replacement_class]},'different_from':'current_spell'}},'evidence':cell_ev})
        else:
            owner=context.class_owner(entry);condition,heading=class_requirement(entry,context)
            if not owner or not condition or compact(prepared[2])!=owner['name_zh']:raise ValueError('unresolved_level_owner')
            if headers!=[owner['name_zh']+'等级','准备法术']:raise ValueError('unsupported_grant_table_header')
            grammar=(r'你与此神圣领域的链接使你始终准备着特定的法术。当你到达'+re.escape(label)+r'表中特定的'+re.escape(owner['name_zh'])+r'等级时，你就始终准备着表中对应的法术。')
            if not re.fullmatch(grammar,compact(policy['raw'])):raise ValueError('unparsed_table_policy')
            value['prerequisites']={'status':'parsed','expression':condition,'evidence':heading}
            prior=0
            for ri,row in enumerate(grid[1:],1):
                level_cell,spell_cell=row
                if level_cell['colspan']!=1 or spell_cell['colspan']!=1 or spell_cell['rowspan']!=1:raise ValueError('unsupported_grant_table_geometry')
                raw=compact(level_cell['text'])
                if not re.fullmatch(r'\d+',raw) or not condition['minimum']<=int(raw)<=20:raise ValueError('invalid_grant_level')
                level=int(raw)
                if level_cell['row']==ri:
                    if level<=prior:raise ValueError('invalid_grant_level')
                    prior=level
                lev_ev=evidence(entry['page_path'],text,*level_cell['span'])
                a,b=spell_cell['span'];cursor=a
                for segment in re.split(r'([，、])',text[a:b]):
                    if segment in {'，','、'}:cursor+=len(segment);continue
                    target=spell_target(entry,context,[cursor,cursor+len(segment)]);cursor+=len(segment)
                    value['grants'].append({'id':'grant_'+str(len(value['grants'])+1),'entry_type':'spell','mode':'prepared','target':target,
                        'when':{**condition,'minimum':level},'branches':[{'id':'branch_1','resource':{'kind':'not_stated'},'evidence':[policy]}],
                        'evidence':[target['evidence'],lev_ev,policy]})
        if len(grid)<2:raise ValueError('empty_grant_table')
    except ValueError as exc:
        value['unparsed'].append({'reason':str(exc),'evidence':ev})
    return value
