'use strict';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => Number(n).toLocaleString('zh-CN');
const pretty = v => typeof v==='boolean'?(v?'是':'否'):typeof v==='object'?JSON.stringify(v,null,2):String(v??'');
const legacy = {entry_marked:'条目标旧',source_archived:'整书归档',unmarked:'未见标记'};
const fieldNames = {name:'名称',name_en:'英文名',source:'文献顺序',sources:'来源',type:'类型',source_id:'来源',ruleset:'版本',legacy:'旧版标记',classification_status:'分类依据',casting_time:'施法时间',casting_time_detail:'施法时间原文',range:'射程',components:'成分',duration:'持续时间',prerequisite:'前置条件',ability_scores:'属性值',skills:'技能',tools:'工具',granted_feat:'授予专长',starting_equipment:'起始装备',creature_type:'生物类型',size:'体型',speed:'速度',languages:'语言',senses:'感官',resistances:'伤害抗性',immunities:'伤害免疫',condition_immunities:'状态免疫',ac:'AC',hp:'HP',source_code:'来源缩写',spell_classes:'职业',ritual:'仪式',attunement_requirements:'同调条件',role:'使用角色',item_subtypes:'子类别',legendary_actions:'传奇动作',price:'价格',carrying_capacity:'载重',statblock_name:'引用数据卡',armor_class:'护甲等级',table_group:'表内分组',facility_kind:'设施类别',quantity:'份量',container:'容器',properties:'词条',strength_requirement:'力量要求',stealth:'隐匿',crew:'船员',passengers:'乘客',cargo_tons:'货物（吨）',damage_threshold:'伤害阈值',weight:'重量',damage:'伤害',mastery:'精通',origin:'来源性质',status:'分类依据',conflict:'字段冲突',sections:'未分类段落',cr_min:'最低 CR',cr_max:'最高 CR'};
const relationNames = {uses_statblock:'使用数据卡',catalog_offer:'购置条目',lists_mount:'列为坐骑',part_of:'属于',section_of:'所属段落',grants:'授予',grants_feat:'授予专长',references:'引用',requires:'要求',belongs_to:'属于',casts:'施展'};
const reasonNames = {animal_statblock_unresolved:'动物数据卡关联待核',catalog_table_unhandled:'商品表结构待支持',catalog_row_layout_unresolved:'合并单元格待核',field_conflict:'字段冲突',duplicate_anchor:'重复锚点',anchor_recovered_by_name:'按名称恢复锚点',possible_incomplete_content:'正文可能不完整',unclassified_page:'页面尚未分类',index_entry_unresolved:'索引未关联',source_ruleset_unresolved:'来源版本待核',catalog_without_locator:'仅见资源名录',index_link_recovered:'已恢复索引链接'};
const LEVELS=['戏法','一环','二环','三环','四环','五环','六环','七环','八环','九环'];
const NONE='__none__';
const BOOL_FIELDS=new Set(['concentration','ritual','verbal','somatic','material','special_material','attunement','legendary_actions']);
const COMMON=new Set(['ruleset','source','origin','status','legacy','conflict','sections']);
const viewOrder=['法术','生物数据卡','魔法物品','魔法武器','专长','战斗风格','传奇恩惠','职业','子职','职业能力','种族物种','背景','装备','NPC','伙伴','坐骑','动物购置','奖励','载具','设施','状态动作规则','危害','扩展机制','资料索引'];
let metadata, schema, groups=[], params=new URLSearchParams(location.search), request, generation=0, panelGeneration=0, timer;
let currentData, currentEntry, detailGeneration=0, ready=false;
const badge=(text,tone='gray')=>`<span class="badge ${tone}">${esc(text)}</span>`;
const sourceURL=(path,start=0,end=0)=>'/source?'+new URLSearchParams({path,start,end})+'#evidence';
const sourceName=id=>metadata.sources.find(s=>s.id===id)?.name||id;
function displayValue(field,value){
  if(field==='source_id')return (Array.isArray(value)?value:[value]).map(sourceName).join('；');
  if(field==='type')return metadata.types[value]||pretty(value);
  return pretty(value);
}
async function api(path,signal){const r=await fetch(path,{signal});const d=await r.json();if(!r.ok)throw new Error(d.error||'请求失败');return d;}
function historyUpdate(replace=false){const search=params.toString();history[replace?'replaceState':'pushState'](null,'',location.pathname+(search?'?'+search:''));}
function setParam(key,value){value!==''&&value!==null?params.set(key,value):params.delete(key);}
function selected(g){return new Set(params.has(g.key)?params.getAll(g.key):g.options.map(o=>o.value));}
function setSelection(g,values){
  params.delete(g.key);
  if(values.length===g.options.length&&g.options.every(o=>values.includes(o.value)))return;
  (values.length?values:[NONE]).forEach(v=>params.append(g.key,v));
}
function changed(replace=false){
  clearTimeout(timer);params.delete('page');params.delete('entry');historyUpdate(replace);sync();load();
}
function shortSource(s){
  const code=s.aliases.find(v=>/^[A-Z][A-Z0-9]{1,11}$/.test(v));
  return code||s.name.split(' / ').at(-1);
}
function multiGroup(g){
  const choices=selected(g);const all=!params.has(g.key);
  return `<fieldset class="facet" data-group="${esc(g.key)}"><legend>${esc(g.label)}</legend><div class="facet-line"><div class="group-actions"><button type="button" data-action="all" aria-label="${esc(g.label)}全选" class="${all?'on':''}">全选</button><button type="button" data-action="none" aria-label="${esc(g.label)}清空">清空</button><button type="button" data-action="invert" aria-label="${esc(g.label)}反选">反选</button></div><div class="choices">${g.options.map(o=>`<label class="chip" title="${esc(o.title||o.label)}${o.count!==undefined?' · 本视图 '+fmt(o.count)+' 条':''}" data-option-search="${esc((o.search||o.label).toLowerCase())}"><input type="checkbox" value="${esc(o.value)}" aria-label="${esc(g.label+'：'+o.label)}" ${choices.has(o.value)?'checked':''}><span>${esc(o.label)}</span></label>`).join('')}</div></div></fieldset>`;
}
function flagGroup(g){
  const active=params.get(g.key)||'';
  const known=g.options.filter(o=>o.value==='true'||o.value==='false');
  const options=[{value:'',label:'任意'},...known.map(o=>({...o,label:g.key==='f_attunement'?(o.value==='true'?'需要':'无需'):o.value==='true'?'是':'否'})),...g.options.filter(o=>!['true','false','__na__'].includes(o.value))];
  return `<fieldset class="flag-group" data-group="${esc(g.key)}"><legend>${esc(g.label)}</legend><div class="segmented">${options.map(o=>`<button type="button" data-flag="${esc(o.value)}" aria-label="${esc(g.label+'：'+o.label)}" aria-pressed="${active===o.value}">${esc(o.label)}</button>`).join('')}</div></fieldset>`;
}
function rangeGroup(){
  const values=['0','1/8','1/4','1/2',...Array.from({length:30},(_,i)=>String(i+1))];
  for(const key of ['cr_min','cr_max'])if(params.has(key)&&!values.includes(params.get(key)))values.push(params.get(key));
  return `<fieldset class="facet cr-range"><legend>挑战等级</legend><div class="range-controls">${['cr_min','cr_max'].map((key,i)=>`<label>${i?'最高':'最低'} <select id="${key}" aria-label="${i?'最高':'最低'} CR"><option value="">不限</option>${values.map(v=>`<option value="${v}" ${params.get(key)===v?'selected':''}>${v}</option>`).join('')}</select></label>`).join('<span>—</span>')}<span class="muted">含边界；未提取与冲突值不进入范围结果</span></div></fieldset>`;
}
function sync(){
  if(!schema)return;
  $('#q').value=params.get('q')||'';
  $('#conflict').checked=params.get('conflict')==='1';$('#sections').checked=params.get('sections')==='1';
  $('#sort').value=params.get('sort')||schema.default_sort;$('#page-size').value=params.get('size')||'150';
  const desc=params.get('dir')==='desc';$('#direction').textContent=desc?'降序 ↓':'升序 ↑';
  $('#direction').setAttribute('aria-label',desc?'切换为升序':'切换为降序');
  for(const button of $('#views').querySelectorAll('[data-view]')){const active=button.dataset.view===(params.get('view')||'');button.classList.toggle('active',active);button.setAttribute('aria-pressed',active);}
  for(const g of groups){
    const el=document.querySelector(`[data-group="${g.key}"]`);if(!el)continue;
    const values=selected(g);
    for(const input of el.querySelectorAll('input[type=checkbox]'))input.checked=values.has(input.value);
    el.querySelector('[data-action=all]')?.classList.toggle('on',!params.has(g.key));
    for(const b of el.querySelectorAll('[data-flag]'))b.setAttribute('aria-pressed',(params.get(g.key)||'')===b.dataset.flag);
  }
  const active=[...new Set(params.keys())].filter(k=>k.startsWith('f_')||COMMON.has(k)||['cr_min','cr_max','q'].includes(k));
  $('#filter-count').textContent=active.length||'';
  $('#common-count').textContent=active.some(k=>['source','ruleset','origin'].includes(k))?'已筛选':'';
  $('#audit-count').textContent=active.some(k=>['status','legacy','conflict','sections'].includes(k))?'已筛选':'';
  $('#active-filters').innerHTML=active.map(k=>{
    const g=groups.find(g=>g.key===k);const values=params.getAll(k);
    const labels=values.map(v=>v===NONE?'无选项':g?.options.find(o=>o.value===v)?.label||v);
    const text=k==='q'?'搜索：'+values[0]:(g?.label||fieldNames[k]||k)+'：'+(labels.length>4?labels.slice(0,3).join('、')+` 等 ${labels.length} 项`:labels.join('、'));
    return `<button type="button" data-remove="${esc(k)}" aria-label="移除 ${esc(text)}" title="移除 ${esc(text)}">${esc(text)} <span aria-hidden="true">×</span></button>`;
  }).join('');
  renderColumns();
}
async function changeView(){
  const gen=++panelGeneration;request?.abort();generation++;ready=false;$('#export').disabled=true;
  $('#results').replaceChildren();$('#result-count').textContent='正在读取筛选…';$('#error').hidden=true;
  const view=params.get('view')||'';
  try{
    const data=await api('/api/facets?'+new URLSearchParams({view}));if(gen!==panelGeneration)return;
    schema=data.schema;groups=data.groups.map(g=>({...g,key:'f_'+g.key}));
    groups.push(...[
      {key:'ruleset',label:'规则版本',options:['5e','5r','待核'].map(value=>({value,label:value}))},
      {key:'origin',label:'来源性质',mode:'single',options:['官方','合作','待核'].map(value=>({value,label:value}))},
      {key:'legacy',label:'旧版标记',options:Object.entries(legacy).map(([value,label])=>({value,label}))},
      {key:'status',label:'分类依据',options:['原文明示','上下文推得','未分类段落'].map(value=>({value,label:value}))},
      {key:'source',label:'来源',options:metadata.sources.filter(s=>data.source_counts[s.id]||params.getAll('source').includes(s.id)).map(s=>({value:s.id,label:shortSource(s),title:s.name,count:data.source_counts[s.id]||0,search:[s.name,...s.aliases].join(' ')}))}
    ]);
    $('#domain-filters').innerHTML=(schema.range?rangeGroup():'')+groups.filter(g=>g.key.startsWith('f_')&&g.mode==='multi').map(multiGroup).join('')+
      (schema.flags.length?`<div class="flag-grid">${groups.filter(g=>g.mode==='flag').map(flagGroup).join('')}</div>`:'')+
      (schema.kind==='spell'?'<p class="material-note">特殊材料：注明价值或会消耗的材料；“是 / 否”均限含材料的法术。</p>':'');
    $('#common-filters').innerHTML=groups.filter(g=>['ruleset','origin'].includes(g.key)).map(multiGroup).join('');
    $('#source-filters').innerHTML=multiGroup(groups.find(g=>g.key==='source'));
    $('#audit-filters').innerHTML=groups.filter(g=>['legacy','status'].includes(g.key)).map(multiGroup).join('');
    $('#source-search').value='';
    $('#title').textContent=view||'全部条目';$('#view-total').textContent=`${fmt(data.total)} 个版本候选`;
    $('#sort').innerHTML=schema.sorts.map(k=>`<option value="${esc(k)}">${esc(fieldNames[k]||k)}</option>`).join('');
    if(!schema.sorts.includes(params.get('sort')))params.set('sort',schema.default_sort);
    $('#page-size').innerHTML=[40,50,150,300].filter(n=>n!==40||params.get('size')==='40').map(n=>`<option value="${n}">${n}</option>`).join('');
    $('#source-details').open=['source','ruleset','origin'].some(k=>params.has(k));
    $('#audit-details').open=['status','legacy','conflict','sections'].some(k=>params.has(k));
    historyUpdate(true);sync();ready=true;await load();
  }catch(e){if(gen===panelGeneration){$('#error').hidden=false;$('#error').textContent=e.message;$('#result-count').textContent='读取失败';}}
}
function renderColumns(){
  $('#columns').innerHTML='<tr>'+schema.columns.map(k=>{
    const sortable=schema.sorts.includes(k),active=(params.get('sort')||schema.default_sort)===k;
    const order=params.get('dir')==='desc'?'descending':'ascending';
    return `<th scope="col" ${active?`aria-sort="${order}"`:''}>${sortable?`<button data-sort="${esc(k)}" aria-label="按${esc(fieldNames[k]||k)}排序">${esc(fieldNames[k]||k)} <span>${active?(order==='descending'?'↓':'↑'):'↕'}</span></button>`:esc(fieldNames[k]||k)}</th>`;
  }).join('')+'</tr>';
}
function quickLabel(field,value){
  if(value==='__unknown__')return '—';if(value==='__conflict__')return '⚠';if(value==='__na__')return '—';
  if(field==='spell_level'&&/^\d$/.test(value))return LEVELS[Number(value)];
  if(field==='spell_classes')return ({吟游诗人:'诗',牧师:'牧',德鲁伊:'德',圣武士:'圣',游侠:'游',术士:'术',法师:'法',魔契师:'契',奇械师:'械',魔战士:'魔'})[value]||value;
  if(BOOL_FIELDS.has(field))return value==='true'?'是':value==='false'?'否':value;
  return value;
}
function cell(row,key){
  if(key==='name')return `<td class="name-cell"><button class="entry-name" data-entry="${esc(row.id)}">${esc(row.name_zh||row.name)}</button><div class="english">${esc(row.name_en)}</div>${row.field_conflicts.length?'<span class="row-warning" title="存在字段冲突；打开详情核对">⚠</span>':''}</td>`;
  if(key==='sources')return `<td class="source-cell">${row.source_ids.map(id=>`<abbr title="${esc(sourceName(id))}">${esc(shortSource(metadata.sources.find(s=>s.id===id)||{name:id,aliases:[]}))}</abbr>`).join(' / ')}</td>`;
  if(key==='type')return `<td>${esc(row.type_label)}</td>`;
  if(key==='ruleset')return `<td>${badge(row.ruleset,row.ruleset==='待核'?'amber':'gray')}</td>`;
  if(key==='legacy')return `<td>${esc(legacy[row.legacy])}</td>`;
  if(key==='classification_status')return `<td>${esc(row.classification_status)}</td>`;
  const values=row.quickref[key];let text=values?values.map(v=>quickLabel(key,v)).join(key==='spell_classes'?' ':' / '):pretty(row.fields[key]);
  if(!text)text='—';
  const conflict=row.field_conflicts.includes(key);const raw=pretty(row.fields[key]);
  return `<td class="${['prerequisite','attunement_requirements','item_subtypes'].includes(key)?'wide-cell':''} ${text==='—'?'missing':''} ${conflict?'conflicted':''}" title="${esc((conflict?'字段冲突：':'')+raw)}">${esc(text)}${conflict&&!text.includes('⚠')?' ⚠':''}</td>`;
}
async function load(){
  if(!ready)return;request?.abort();request=new AbortController();const gen=++generation;
  $('#error').hidden=true;$('#result-count').textContent='正在查询…';$('#export').disabled=true;
  for(const id of ['prev','next','bottom-prev','bottom-next'])$('#'+id).disabled=true;
  $('#results').setAttribute('aria-busy','true');
  try{
    const data=await api('/api/entries?'+params,request.signal);if(gen!==generation)return;
    currentData=data;
    $('#result-count').innerHTML=`找到 <strong>${fmt(data.total)}</strong> 条`;
    $('#results').innerHTML=data.rows.map(r=>'<tr>'+schema.columns.map(k=>cell(r,k)).join('')+'</tr>').join('');
    $('#empty').hidden=data.total!==0;$('#export').disabled=!data.total;
    $('#page').textContent=`${data.page} / ${data.pages}`;
    $('#range').textContent=data.total?`${fmt((data.page-1)*data.page_size+1)}–${fmt(Math.min(data.page*data.page_size,data.total))} / ${fmt(data.total)}`:'0 条结果';
    for(const id of ['prev','bottom-prev'])$('#'+id).disabled=data.page<=1;
    for(const id of ['next','bottom-next'])$('#'+id).disabled=data.page>=data.pages;
  }catch(e){if(e.name==='AbortError')return;$('#error').hidden=false;$('#error').textContent=e.message;$('#result-count').textContent='查询失败';$('#results').replaceChildren();}
  finally{if(gen===generation)$('#results').setAttribute('aria-busy','false');}
}
function page(delta){if(!currentData)return;params.set('page',currentData.page+delta);historyUpdate();load();$('.results-bar').scrollIntoView({block:'start',behavior:'smooth'});}
function sortBy(key){if(params.get('sort')===key)setParam('dir',params.get('dir')==='desc'?'asc':'desc');else{params.set('sort',key);params.delete('dir');}changed();}
$('#filter-panel').addEventListener('change',e=>{
  const el=e.target;
  if(el.type==='checkbox'&&el.closest('[data-group]')){
    const g=groups.find(g=>g.key===el.closest('[data-group]').dataset.group);const set=selected(g);
    set.delete(NONE);el.checked?set.add(el.value):set.delete(el.value);setSelection(g,[...set]);
    if(g.key==='status'&&set.size===1&&set.has('未分类段落'))params.set('sections','1');changed();
  }else if(['cr_min','cr_max'].includes(el.id)){
    setParam(el.id,el.value);const val=s=>s.includes('/')?s.split('/').map(Number).reduce((a,b)=>a/b):Number(s);
    if(params.has('cr_min')&&params.has('cr_max')&&val(params.get('cr_min'))>val(params.get('cr_max'))){const other=el.id==='cr_min'?'cr_max':'cr_min';params.set(other,el.value);$('#'+other).value=el.value;}changed();
  }else if(['conflict','sections'].includes(el.id)){setParam(el.id,el.checked?'1':'');changed();}
});
$('#filter-panel').addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;const group=b.closest('[data-group]');if(!group)return;const g=groups.find(g=>g.key===group.dataset.group);
  if(b.hasAttribute('data-flag')){setParam(g.key,b.dataset.flag);changed();return;}
  const all=g.options.map(o=>o.value),current=selected(g);
  setSelection(g,b.dataset.action==='all'?all:b.dataset.action==='none'?[]:all.filter(v=>!current.has(v)));changed();
});
$('#source-search').addEventListener('input',e=>{const q=e.target.value.trim().toLowerCase();for(const label of $('#source-filters').querySelectorAll('[data-option-search]'))label.hidden=!label.dataset.optionSearch.includes(q);});
$('#q').addEventListener('input',e=>{clearTimeout(timer);setParam('q',$('#q').value.trim());if(!e.isComposing)timer=setTimeout(()=>changed(true),160);});
$('#search-form').addEventListener('submit',e=>{e.preventDefault();setParam('q',$('#q').value.trim());changed();});
$('#toggle-filters').onclick=()=>{const hidden=!$('#filter-panel').hidden;$('#filter-panel').hidden=hidden;$('#toggle-filters').setAttribute('aria-expanded',!hidden);};
$('#reset').onclick=()=>{const view=params.get('view');params=new URLSearchParams();if(view)params.set('view',view);historyUpdate();changeView();};
$('#views').addEventListener('click',e=>{
  const b=e.target.closest('[data-view]');if(!b)return;
  for(const key of [...params.keys()])if(key.startsWith('f_')||['page','entry','sort','dir','cr_min','cr_max','field','value'].includes(key))params.delete(key);
  setParam('view',b.dataset.view);historyUpdate();changeView();
});
$('#active-filters').addEventListener('click',e=>{const b=e.target.closest('[data-remove]');if(!b)return;const key=b.dataset.remove;params.delete(key);if(['cr_min','cr_max'].includes(key))$('#'+key).value='';changed();});
$('#sort').onchange=()=>{params.set('sort',$('#sort').value);changed();};
$('#direction').onclick=()=>{setParam('dir',params.get('dir')==='desc'?'asc':'desc');changed();};
$('#columns').addEventListener('click',e=>{const b=e.target.closest('[data-sort]');if(b)sortBy(b.dataset.sort);});
$('#page-size').onchange=()=>{params.set('size',$('#page-size').value);changed();};
$('#prev').onclick=$('#bottom-prev').onclick=()=>page(-1);$('#next').onclick=$('#bottom-next').onclick=()=>page(1);
$('#export').onclick=()=>{location.href='/api/export?'+params;};
$('#results').addEventListener('click',e=>{const b=e.target.closest('[data-entry]');if(b)openEntry(b.dataset.entry);});
$('#detail-content').addEventListener('click',e=>{const b=e.target.closest('[data-entry]');if(b)openEntry(b.dataset.entry);const t=e.target.closest('[data-tab]');if(t){renderDetail(t.dataset.tab);$('#tab-'+t.dataset.tab).focus();}});
$('#close-detail').onclick=closeEntry;$('#detail').addEventListener('cancel',e=>{e.preventDefault();closeEntry();});
window.addEventListener('popstate',async()=>{params=new URLSearchParams(location.search);await changeView();params.has('entry')?openEntry(params.get('entry'),false):closeEntry();});
document.addEventListener('keydown',e=>{if(e.key==='/'&&!$('#detail').open&&!['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)){e.preventDefault();$('#q').focus();}});
async function init(){
  try{
    metadata=await api('/api/meta');Object.assign(fieldNames,metadata.fields);
    $('#snapshot').textContent=`${fmt(metadata.summary.processed_html_files)} 页 · ${metadata.summary.snapshot_commit.slice(0,7)} · 本地只读`;
    metadata.views.sort((a,b)=>viewOrder.indexOf(a.name)-viewOrder.indexOf(b.name));
    $('#views').innerHTML=[{name:'',count:metadata.summary.typed_versions},...metadata.views].map(v=>`<button data-view="${esc(v.name)}">${esc(v.name||'全部条目')}<span>${fmt(v.count)}</span></button>`).join('');
    // Preserve older bookmarked single-attribute queries.
    if(params.has('field')&&params.has('value')){const f=params.get('field');let v=params.get('value');if(f==='spell_level'&&LEVELS.includes(v))v=String(LEVELS.indexOf(v));params.set('f_'+f,v);params.delete('field');params.delete('value');}
    await changeView();if(params.has('entry'))openEntry(params.get('entry'),false);
  }catch(e){$('#error').hidden=false;$('#error').textContent=e.message;$('#result-count').textContent='服务未就绪';}
}
init();
