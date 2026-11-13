"""Typed catalog tables; preserve cell evidence and refuse ambiguous layouts."""
import re
from hashlib import sha256
from html_extract import compact, digest, split_name, key

HEADERS = {
    '价格':'price','费用':'price','市场价格':'price','重量':'weight','载重':'carrying_capacity',
    '速度':'speed','伤害':'damage','属性':'properties','词条':'properties','精通':'mastery',
    '护甲等级（AC）':'armor_class','护甲等级（ AC ）':'armor_class','护甲等级':'armor_class',
    '力量':'strength_requirement','隐匿':'stealth','特殊':'special',
    '份量':'quantity','数量':'quantity','容器':'container','存放容器':'container',
    '船员':'crew','乘客':'passengers','货物（吨）':'cargo_tons','AC':'ac','HP':'hp','伤害阈值':'damage_threshold',
}
NAMES={'物品','项目','名称','商品','船只类型','护甲','护甲名称','法器','徽章','种类','类型','大小','水晶大小','死亡象征','货物'}
VEHICLE_NAMES=re.compile(r'Carriage|Wagon|Cart|Chariot|Sled\b|Galley|Keelboat|Longship|Rowboat|Sailing Ship|Warship|Airship',re.I)
# A table whose first header names its rows' kind and whose every row prints a bilingual name defines those
# terms: 技能 | 属性 | 适用示例 (特技Acrobatics | 敏捷 | …), 属性 | 数值衡量…… (力量Strength | 力气). Tables that
# list the same words without English names (属性 | 进行检定用来……) show uses of terms defined elsewhere.
DEFINITION_KINDS={'技能':'skill','属性':'ability'}
DEFINITION_FIELDS={'skill':{'属性':'ability'}}


def definition_rows(doc,table):
    """Entries for the rows of a definition table, or [] when the table is not one."""
    rows=table['rows'];headers=[re.sub(r'\s+','',h) for h in rows[0]['cells']]
    kind=DEFINITION_KINDS.get(headers[0]) if headers else None
    body=rows[1:]
    if not kind or len(body)<2:return []
    if any(len(r['cells'])!=len(headers) or any(s!='1' for s in r['rowspan']+r['colspan']) or not all(split_name(r['cells'][0])) for r in body):return []
    columns={j:field for j,h in enumerate(headers) for label,field in DEFINITION_FIELDS.get(kind,{}).items() if h==label}
    out=[]
    for row in body:
        left,right=row['cell_spans'][0][0],row['cell_spans'][-1][1]
        fields={field:[(row['cells'][j],row['cell_spans'][j][0]-left,row['cell_spans'][j][1]-left)] for j,field in columns.items() if row['cells'][j]}
        out.append({'name':row['cells'][0],'start':left,'title_end':row['cell_spans'][0][1],'end':right,'anchors':[],
            'parent_index':None,'type':kind,'classification_rule':'definition_table','boundary_rule':'table_row',
            'legacy_direct':False,'fields':fields,'extra_facts':[],'observation_ids':[],
            'body_hash':sha256(compact(doc.text[left:right]).encode()).hexdigest(),'level':9,'is_primary':False})
    return out


def header_field(h):
    h=re.sub(r'\s+','',h)
    if h.startswith('护甲等级'):return 'armor_class'
    return HEADERS.get(h)


def extract_tables(doc, candidates):
    output=[];tables=[];issues=[]
    for ti,t in enumerate(doc.tables()):
        rows=t['rows'];a,b=t['span']
        tables.append({'id':'tbl_'+digest(doc.path,ti),'page_path':doc.path,'start':a,'end':b,
            'headers':rows[0]['cells'],'rows':[{k:r[k] for k in ('cells','span','rowspan','colspan')} for r in rows],
            'layout_warning':any(any(x!='1' for x in r['rowspan']+r['colspan']) for r in rows)})
        defined=definition_rows(doc,t)
        if defined:
            output.extend(defined);continue
        # A caption/group row can precede the column headers. Only bounded, explicit schemas.
        hi=next((i for i,r in enumerate(rows[:4]) if any(header_field(h)=='price' for h in r['cells'])
                 and any(h in NAMES for h in r['cells'])),None)
        if hi is None:continue
        headers=rows[hi]['cells']
        namecols=[i for i,h in enumerate(headers) if h in NAMES]
        before=[c for c in candidates if c.start<a]
        title=max(before,key=lambda c:c.start).name if before else doc.title
        caption=t['tag'].find_previous(['p','h1','h2','h3','h4','h5','h6'])
        if caption:
            ca,cb=doc.span(caption)
            caption_text=compact(doc.text[ca:min(a,cb)])
            if caption_text and len(caption_text)<=100:title=caption_text
        # Repeated name/price groups are separate catalogs, never zip both halves into one row.
        groups=[]
        for i,nc in enumerate(namecols):
            lo=nc if nc else 0;end=namecols[i+1] if i+1<len(namecols) else len(headers)
            if len(namecols)==1:lo=0
            cols=list(range(lo,end))
            if not any(header_field(headers[j])=='price' for j in cols):continue
            mapped={header_field(headers[j]) for j in cols}
            animal='carrying_capacity' in mapped and bool(re.search(r'坐骑.*动物',title))
            vehicle=('speed' in mapped and re.search('载具|船',doc.path+title)) or ('carrying_capacity' in mapped and not animal and '载具' in doc.path)
            equipment='weight' in mapped or ('price' in mapped and title.startswith('机巧奇具'))
            goods=len(headers)==2 and headers[nc] in {'货物','商品'} and re.search('商业货物|其他开支',doc.path)
            if not (animal or vehicle or equipment or goods):
                issues.append({'reason':'catalog_table_unhandled','detail':{'headers':headers,'title':title},'span':[a,b]});continue
            groups.append((nc,cols,'animal_offer' if animal else 'vehicle' if vehicle else 'equipment'))
        group_label=''
        for row in rows[hi+1:]:
            vals=row['cells']
            if len(vals)==1 and row['colspan'][0]!='1':
                group_label=vals[0];continue
            if len(vals)!=len(headers) or any(s!='1' for s in row['rowspan']+row['colspan']):
                if groups:issues.append({'reason':'catalog_row_layout_unresolved','detail':vals,'span':list(row['span'])})
                continue
            for nc,cols,kind in groups:
                name=vals[nc]
                if not name or len(name)>100:continue
                price=next((vals[j] for j in cols if header_field(headers[j])=='price'),'')
                if not re.search(r'\d|×|多[种类]|不等|—|－',price):continue
                # Shared tack tables mix vehicles, services and accessories: classify rows, not the page.
                actual='vehicle' if kind=='equipment' and '坐骑' in doc.path and VEHICLE_NAMES.search(name) else kind
                left=min(row['cell_spans'][j][0] for j in cols);right=max(row['cell_spans'][j][1] for j in cols)
                fields={}
                for j in cols:
                    field=header_field(headers[j]);v=vals[j]
                    if field and v:fields[field]=[(v,row['cell_spans'][j][0]-left,row['cell_spans'][j][1]-left)]
                extra=[]
                # A table lists purchasable animals, NOT a statblock; roles are attached separately.
                if actual=='animal_offer':
                    if doc.path.startswith(('topics/玩家手册/','topics/玩家手册2024/')):
                        extra.append(('role','坐骑',{'path':doc.path,'span':[a,b],'table_title':title},'mount_catalog_context','上下文推得'))
                    zh,en=split_name(name)
                    matching=[c for c in candidates if key(split_name(re.sub(r'[（(].*','',c.name))[0])==key(zh)]
                    if len(matching)==1:
                        c=matching[0];body=doc.text[c.start:c.end]
                        # Scope to this animal's paragraph, not other animals sharing the page.
                        role='宠物' if re.search('宠物|送信',body) else '役畜' if re.search('拉动.*雪橇|拉一辆雪橇',body) else None
                        if role:extra.append(('role',role,{'path':doc.path,'span':[c.start,c.end]},'animal_description','原文明示'))
                        m=re.search(r'使用\s*(.*?)\s*数据卡',body,re.S)
                        if m:extra.append(('statblock_name',compact(m[1]),{'path':doc.path,'span':[c.start+m.start(),c.start+m.end()]},'statblock_reference','原文明示'))
                if group_label:extra.append(('table_group',group_label,{'path':doc.path,'span':[a,b]},'table_group','原文明示'))
                output.append({'name':name,'start':left,'title_end':row['cell_spans'][nc][1],'end':right,'anchors':[],
                    'parent_index':None,'type':actual,'classification_rule':actual+'_table','boundary_rule':'table_row',
                    'legacy_direct':False,'fields':fields,'extra_facts':extra,'observation_ids':[],
                    'body_hash':sha256(compact(doc.text[left:right]).encode()).hexdigest(),'level':9,'is_primary':False})
    return output,tables,issues
