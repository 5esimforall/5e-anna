'use strict';
const $ = s => document.querySelector(s);
const {esc,stored}=annaKit;
// New workbench copy lives in one bilingual resource; data/source labels remain unchanged.
const annaUiLabels = {
  zh: {bundleChanged:'数据已经重建，请重启 make up，再刷新页面。',bookReader:annaBrands.book,skipSearch:'跳到搜索',emptyTitle:'没有匹配条目',emptyHint:'试试移除已选条件，或清空全部条件。',
    readerTitle:'条目阅读',readerHint:'Esc 关闭 · F6 切换列表与详情',readerHintNarrow:'Esc 关闭 · 上方箭头连续查阅结果',readerHintTouch:'用上方箭头连续查阅结果',closeDetail:'关闭详情',
    fullWidth:'全宽阅读',resizeReader:'调整阅读栏宽度',resizeReaderHint:'拖动或用左右方向键调整阅读栏宽度，双击恢复默认',wideTable:'表格宽于阅读栏 · 全宽阅读',readerShare:'阅读栏占 {percent}%',
    previousEntry:'上一条',nextEntry:'下一条',listPosition:'第 {current} / {total} 行',
    outsideResults:'不在已加载的结果中',loadMore:'继续加载',loadingMore:'正在加载…',retryLoad:'重新加载',
    loadedRange:'已显示 {shown} / {total} {unit}',allLoaded:'已全部显示 {total} {unit}',noResults:'0 条结果',units:{stacked:'行',each:'条'},loading:'正在读取条目…',retry:'重新读取',readError:'无法读取条目',
    noEvidence:'未提取到字段证据；不代表不存在。',body:'正文',fields:'属性',facts:'证据',relations:'关系',reviews:'待核',
    detailTabs:'详情栏目',moreFilters:'更多筛选条件',allEntries:'全部条目',
    moreTypes:'{first}等 {count} 类',nameField:'名称',bodyField:'正文',and:'且',excludeGroup:'排除',allExcept:'全部条目（不含{list}）',excludeInstead:'改为排除',includeInstead:'改为包含',isNot:'不是',lacks:'不含',excludeCount:'排除它会去掉 {count} 条',nameContains:'包含“{text}”',presetField:'预设',clearGroup:'清除',invertGroup:'反选',optionCount:'选中后 {count} 条',
    elsewhere:'其他类型另有 {count} 条',elsewhereTitle:'搜索在其他类型中的匹配：{list}',dropCondition:'去掉 {condition}',
    auditMode:'核对',auditOn:'核对模式已开启：显示提取依据、审核状态与覆盖线索。单击切换为阅读模式',auditOff:'阅读模式：审核细节已收起。单击开启核对模式',
    auditSummary:'核对',auditSummaryHint:'开启核对模式并查看证据',
    marks:{verbal:['言','言语'],somatic:['姿','姿势'],material:['材','材料'],ritual:['仪','仪式'],concentration:['专','专注']},
    markStates:{absent:'无{name}',unknown:'{name}未提取',conflict:'{name}存在冲突',special:'{name}（特殊材料）'},
    stackVersions:'合并同名版本',compactRows:'紧凑行',sortMatch:'匹配度',preferRuleset:'优先显示',sortLabel:'排序',displayOptions:'显示',
    stackedCount:'找到 {rows} 行 · {versions} 个版本',shownVersion:'当前行显示此版本',
    stackStatus:{same_name_candidate:'同名候选 {count} 个版本，各自独立',exact_text_candidate:'同文候选 {count} 个版本，各自独立'},
    fieldNotes:'字段批注',noteElsewhere:'证据不在本段原文',
    layoutUnavailable:{missing:'找不到原文页面，显示提取文本。',stale:'原文已更新，与数据库快照不一致，显示提取文本。',empty:'原文排版为空，显示提取文本。',error:'无法读取原文排版，显示提取文本。'},
    versionsTab:'版本对照',versionsIntro:'同名候选按规则版本排列；候选关系不代表同一规则身份。',differingFields:'不同的字段',sameFields:'相同或未提取的字段 {count} 项',currentVersion:'当前',
    rulesets:{'5e':['2014','2014 版规则（5e）'],'5r':['2024','2024 版规则（5r）'],'待核':['待核','规则版本待核']},
    flags:{true:'是',false:'否'},legend:'✓ 是　· 否　— 未提取　⚠ 存在字段冲突　·　点击结果行查看正文与证据'},
  en: {bundleChanged:'The data was rebuilt. Restart make up, then refresh this page.',bookReader:annaBrands.book,skipSearch:'Skip to search',emptyTitle:'No matching entries',emptyHint:'Remove an active condition or clear them all.',
    readerTitle:'Entry reader',readerHint:'Esc to close · F6 to switch between results and reader',readerHintNarrow:'Esc to close · Use the arrows above to browse the results',readerHintTouch:'Use the arrows above to browse the results',closeDetail:'Close details',
    fullWidth:'Full-width reading',resizeReader:'Resize reader',resizeReaderHint:'Drag or use the arrow keys to resize the reader; double-click to reset',wideTable:'Table wider than the reader · Read full width',readerShare:'Reader at {percent}%',
    previousEntry:'Previous entry',nextEntry:'Next entry',listPosition:'Row {current} of {total}',
    outsideResults:'Not among the loaded results',loadMore:'Load more',loadingMore:'Loading…',retryLoad:'Try again',
    loadedRange:'Showing {shown} of {total} {unit}',allLoaded:'All {total} {unit} shown',noResults:'No results',units:{stacked:'rows',each:'entries'},loading:'Loading entry…',retry:'Try again',readError:'Unable to load entry',
    noEvidence:'No field evidence extracted; this does not establish absence.',body:'Text',fields:'Attributes',facts:'Evidence',relations:'Relations',reviews:'Review',
    detailTabs:'Detail sections',moreFilters:'More filters',allEntries:'All entries',
    moreTypes:'{first} and {count} types',nameField:'Name',bodyField:'Text',and:'and',excludeGroup:'Exclude',allExcept:'All entries except {list}',excludeInstead:'Exclude instead',includeInstead:'Include instead',isNot:'is not',lacks:'lacks',excludeCount:'Excluding it removes {count}',nameContains:'contains “{text}”',presetField:'Preset',clearGroup:'Clear',invertGroup:'Invert',optionCount:'{count} results if chosen',
    elsewhere:'{count} more in other types',elsewhereTitle:'Matches for this search in other types: {list}',dropCondition:'Without {condition}',
    auditMode:'Audit',auditOn:'Audit mode is on: extraction evidence, review states and coverage signals are shown. Click for reading mode',auditOff:'Reading mode: review details are folded away. Click to turn on audit mode',
    auditSummary:'Audit',auditSummaryHint:'Turn on audit mode and show evidence',
    marks:{verbal:['V','Verbal'],somatic:['S','Somatic'],material:['M','Material'],ritual:['R','Ritual'],concentration:['C','Concentration']},
    markStates:{absent:'No {name}',unknown:'{name} not extracted',conflict:'{name} conflicts',special:'{name} (special material)'},
    stackVersions:'Stack same-name versions',compactRows:'Compact rows',sortMatch:'Best match',preferRuleset:'Show first',sortLabel:'Sort',displayOptions:'Display',
    stackedCount:'{rows} rows · {versions} versions',shownVersion:'This version is shown in the row',
    stackStatus:{same_name_candidate:'{count} same-name candidates, each kept separate',exact_text_candidate:'{count} same-text candidates, each kept separate'},
    fieldNotes:'Field notes',noteElsewhere:'evidence outside this passage',
    layoutUnavailable:{missing:'The source page is missing; showing extracted text.',stale:'The source changed since this database snapshot; showing extracted text.',empty:'The source layout is empty; showing extracted text.',error:'Unable to load the source layout; showing extracted text.'},
    versionsTab:'Versions',versionsIntro:'Same-name candidates ordered by rules version; a candidate relation is not a shared rule identity.',differingFields:'Fields that differ',sameFields:'{count} fields equal or not extracted',currentVersion:'current',
    rulesets:{'5e':['2014','2014 rules (5e)'],'5r':['2024','2024 rules (5r)'],'待核':['Unverified','Rules version unverified']},
    flags:{true:'Yes',false:'No'},legend:'✓ yes　· no　— not extracted　⚠ conflicting field　·　Select a row for its text and evidence'}
};
const ui = annaUiLabels[document.documentElement.lang.startsWith('en') ? 'en' : 'zh'];
// A ruleset is a rules generation, not a publication year: short labels name it, the full label says so.
const rulesetShort=r=>ui.rulesets[r]?.[0]||r, rulesetFull=r=>ui.rulesets[r]?.[1]||r;
document.querySelector('#prefer').innerHTML=['5r','5e'].map(value=>`<option value="${value}" title="${esc(rulesetFull(value))}">${esc(rulesetShort(value))}</option>`).join('');
for(const el of document.querySelectorAll('[data-ui]'))el.textContent=ui[el.dataset.ui];
for(const el of document.querySelectorAll('[data-ui-label]')){el.setAttribute('aria-label',ui[el.dataset.uiLabel]);el.title=ui[el.dataset.uiLabel];}
const fmt = n => Number(n).toLocaleString('zh-CN');
const pretty = v => typeof v==='boolean'?ui.flags[v]:typeof v==='object'?JSON.stringify(v,null,2):String(v??'');
const legacy = {entry_marked:'条目标旧',source_archived:'整书归档',unmarked:'未见标记'};
const fieldNames = {name:'名称',name_en:'英文名',source:'文献顺序',sources:'来源',type:'类型',source_id:'来源',ruleset:'规则版本',legacy:'旧版标记',classification_status:'分类依据',casting_time:'施法时间',casting_time_detail:'施法时间原文',range:'射程',components:'成分',duration:'持续时间',prerequisite:'前置条件',ability_scores:'属性值',skills:'技能',tools:'工具',granted_feat:'授予专长',starting_equipment:'起始装备',creature_type:'生物类型',size:'体型',speed:'速度',languages:'语言',senses:'感官',resistances:'伤害抗性',immunities:'伤害免疫',condition_immunities:'状态免疫',ac:'AC',hp:'HP',source_code:'来源缩写',spell_classes:'职业',level:'等级',ritual:'仪式',attunement_requirements:'同调条件',role:'使用角色',item_subtypes:'子类别',legendary_actions:'传奇动作',price:'价格',carrying_capacity:'载重',statblock_name:'引用数据卡',armor_class:'护甲等级',table_group:'表内分组',facility_kind:'设施类别',quantity:'份量',container:'容器',properties:'词条',strength_requirement:'力量要求',stealth:'隐匿',crew:'船员',passengers:'乘客',cargo_tons:'货物（吨）',damage_threshold:'伤害阈值',weight:'重量',damage:'伤害',mastery:'精通',origin:'来源性质',status:'分类依据',conflict:'字段冲突',sections:'未分类段落',cr_min:'最低 CR',cr_max:'最高 CR',strength:'力量',dexterity:'敏捷',constitution:'体质',intelligence:'智力',wisdom:'感知',charisma:'魅力'};
const relationNames = {uses_statblock:'使用数据卡',catalog_offer:'购置条目',lists_mount:'列为坐骑',part_of:'属于',section_of:'所属段落',grants:'授予',grants_feat:'授予专长',references:'引用',requires:'要求',belongs_to:'属于',casts:'施展'};
const reasonNames = {animal_statblock_unresolved:'动物数据卡关联待核',catalog_table_unhandled:'商品表结构待支持',catalog_row_layout_unresolved:'合并单元格待核',field_conflict:'字段冲突',duplicate_anchor:'重复锚点',anchor_recovered_by_name:'按名称恢复锚点',possible_incomplete_content:'正文可能不完整',unclassified_page:'页面尚未分类',index_entry_unresolved:'索引未关联',source_ruleset_unresolved:'来源版本待核',catalog_without_locator:'仅见资源名录',index_link_recovered:'已恢复索引链接'};
const LEVELS=['戏法','一环','二环','三环','四环','五环','六环','七环','八环','九环'];
const NONE='__none__';
const BOOL_FIELDS=new Set(['concentration','ritual','verbal','somatic','material','special_material','attunement','legendary_actions']);
const COMMON=new Set(['ruleset','source','origin','status','legacy','conflict','sections']);
// Audit mode is a per-browser viewing preference, never part of a shared query URL.
const AUDIT_KEY='anna.audit', AUDIT_COLUMNS=new Set(['classification_status','legacy']), AUDIT_PARAMS=['status','legacy','conflict','sections'];
const AUDIT_TABS=new Set(['facts','discoveries','references','reviews']);
let auditMode=stored.get(AUDIT_KEY)==='on';
const visibleColumns=()=>schema.columns.filter(k=>auditMode||!AUDIT_COLUMNS.has(k));
let metadata, schema, groups=[], params=new URLSearchParams(location.search), request, generation=0, panelGeneration=0, timer;
let currentData, currentEntry, detailGeneration=0, ready=false, facetData=null, facetGeneration=0, panelShape='';
const badge=(text,tone='gray')=>`<span class="badge ${tone}">${esc(text)}</span>`;
const sourceURL=(path,start=0,end=0)=>'/source?'+new URLSearchParams({path,start,end})+'#evidence';
const sourceOf=id=>metadata.sources.find(s=>s.id===id);
const sourceName=id=>sourceOf(id)?.name||id, sourceShort=id=>sourceOf(id)?.short_name||id;
// The reader names a book in full and adds the short label the table uses, so the two can be matched.
const sourceFull=id=>sourceShort(id)===sourceName(id)?sourceName(id):`${sourceName(id)}（${sourceShort(id)}）`;
function displayValue(field,value){
  if(field==='source_id')return (Array.isArray(value)?value:[value]).map(sourceFull).join('；');
  if(field==='type')return metadata.types[value]||pretty(value);
  if(field==='ruleset')return rulesetFull(value);
  return pretty(value);
}
// One reading of each field. The table, the reader's attributes, its field notes and the version comparison
// all show the normalized value, so one entry never reads 锁 in one place and 契 in another; the source
// wording stays in the evidence. Short form (the table) abbreviates classes; the reader spells them out.
const CLASS_SHORT={吟游诗人:'诗',牧师:'牧',德鲁伊:'德',圣武士:'圣',游侠:'游',术士:'术',法师:'法',魔契师:'契',奇械师:'械',魔战士:'魔'};
const MISSING_TOKENS=new Set(['__unknown__','__na__']);
function tokenText(field,token,short){
  if(token==='__conflict__')return '⚠';
  if(field==='spell_level'&&/^\d$/.test(token))return LEVELS[Number(token)];
  if(field==='spell_classes'&&short)return CLASS_SHORT[token]||token;
  if(BOOL_FIELDS.has(field)&&(token==='true'||token==='false'))return ui.flags[token];
  return token;
}
function fieldText(entry,field,short=false){
  const tokens=entry.quickref?.[field];
  if(tokens){const parts=tokens.filter(t=>!MISSING_TOKENS.has(t)).map(t=>tokenText(field,t,short));return parts.length?parts.join(short&&field==='spell_classes'?' ':'、'):'—';}
  const raw=entry.fields?.[field];
  if(raw===undefined||raw===null||raw==='')return '—';
  return Array.isArray(raw)?raw.map(v=>displayValue(field,v)).join('、'):displayValue(field,raw);
}
async function api(path,signal){const r=await fetch(path,{signal});const d=await r.json();if(!r.ok)throw new Error(d.code==='bundle_changed'?ui.bundleChanged:d.error||'请求失败');return d;}
let restoringUi=false, uiSaveTimer, lastResultEntry=null;
history.scrollRestoration='manual';
function captureUiState(){
  return {tableTop:$('.table-wrap').scrollTop,tableLeft:$('.table-wrap').scrollLeft,
    mainTop:$('#query-main').scrollTop,windowTop:window.scrollY,filterTop:$('#filter-panel').scrollTop,
    filtersOpen:!$('#filter-panel').hidden,sourceOpen:$('#source-details').open,auditOpen:$('#audit-details').open,extraOpen:$('#domain-extra')?.open,
    sourceSearch:$('#source-search').value,readerId:currentReaderId,readerTab:detailTab,readerFull,
    readerTop:$('#detail-content').scrollTop,lastResultEntry,loadedRows:currentData?.rows.length||0};
}
function saveUiState(){if(!restoringUi)history.replaceState({...history.state,anna:captureUiState()},'');}
function historyUpdate(replace=false){
  if(!replace)saveUiState();
  const search=params.toString();history[replace?'replaceState':'pushState'](history.state,'',location.pathname+(search?'?'+search:''));
}
function filterSources(){
  const q=$('#source-search').value.trim().toLowerCase();
  for(const label of $('#source-filters').querySelectorAll('[data-option-search]'))label.hidden=!label.dataset.optionSearch.includes(q);
}
function restoreUiState(state){
  if(!state)return;
  // The panel starts collapsed; only an explicit opening in this tab's history reopens it.
  $('#filter-panel').hidden=!state.filtersOpen;
  $('#toggle-filters').setAttribute('aria-expanded',!!state.filtersOpen);
  $('#source-details').open=!!state.sourceOpen;$('#audit-details').open=!!state.auditOpen;
  if($('#domain-extra')&&typeof state.extraOpen==='boolean')$('#domain-extra').open=state.extraOpen;
  $('#source-search').value=state.sourceSearch||'';filterSources();
  // Full-width reading changes the text's line breaks, so it returns before the reader's scroll position.
  if(currentReaderId&&state.readerId===currentReaderId)setReaderFull(!!state.readerFull,{save:false});
  $('#filter-panel').scrollTop=state.filterTop||0;
  $('.table-wrap').scrollTop=state.tableTop||0;$('.table-wrap').scrollLeft=state.tableLeft||0;
  $('#query-main').scrollTop=state.mainTop||0;window.scrollTo(0,state.windowTop||0);
  $('#detail-content').scrollTop=state.readerTop||0;lastResultEntry=state.lastResultEntry||null;
}
function queueUiSave(){clearTimeout(uiSaveTimer);uiSaveTimer=setTimeout(saveUiState,80);}
for(const el of [window,$('.table-wrap'),$('#query-main'),$('#filter-panel'),$('#detail-content')])el.addEventListener('scroll',queueUiSave,{passive:true});
for(const el of [$('#source-details'),$('#audit-details')])el.addEventListener('toggle',queueUiSave);
window.addEventListener('pagehide',saveUiState);
function setParam(key,value){value!==''&&value!==null?params.set(key,value):params.delete(key);}
// The URL records only a sort the reader chose. Without one, a search ranks by match quality and an
// empty search uses the table's default; the server applies the same rule (browse.sort_key).
function effectiveSort(){
  const sort=params.get('sort')||(searching()?'match':schema.default_sort);
  return sort==='match'&&!searching()?schema.default_sort:sort;
}
function renderSortOptions(){
  const sorts=[...(searching()?['match']:[]),...schema.sorts];
  $('#sort').innerHTML=sorts.map(k=>`<option value="${esc(k)}">${esc(k==='match'?ui.sortMatch:fieldNames[k]||k)}</option>`).join('');
  $('#sort').value=effectiveSort();
}
// Every field reads the same way: nothing chosen sets no condition, choosing a value includes it, and values
// chosen in one field are alternatives. Choosing every value is the same as choosing none. A field's values
// can instead be excluded (`not_<key>`): the field then holds for entries with none of them. The panel edits
// whichever list the field uses; a field holding only excluded values is in exclude mode.
const NOT='not_';
const baseKey=key=>key.replace(/^not_/,'');
const excluding=g=>params.has(NOT+g.key)&&!params.has(g.key);
const modeKey=g=>excluding(g)?NOT+g.key:g.key;
function selected(g){return new Set(params.getAll(modeKey(g)).filter(v=>v!==NONE));}
function setSelection(g,values,key=modeKey(g)){
  params.delete(key);
  if(!values.length||key===g.key&&g.options.every(o=>values.includes(o.value)))return;
  values.forEach(v=>params.append(key,v));
}
function clearField(key){params.delete(key);params.delete(NOT+key);}
// Flipping a field moves its values between included and excluded, merging with any already on the other side.
function flipField(key){
  const to=key.startsWith(NOT)?baseKey(key):NOT+key,values=[...new Set([...params.getAll(to),...params.getAll(key)])].filter(v=>v!==NONE);
  params.delete(key);params.delete(to);values.forEach(v=>params.append(to,v));
  if(baseKey(key)==='type'){chooseTypes(params.getAll('type'));return;}
  changed();
}
function changed(replace=false){
  clearTimeout(timer);params.delete('entry');historyUpdate(replace);closeEntry({update:false,restoreFocus:false});sync();$('.table-wrap').scrollTop=0;load();refreshFacets();
}
// Each option counts what choosing it would match under every other condition (serve.facets).
const optionTitle=(o,g)=>`${o.title||o.label} · ${(g&&excluding(g)?ui.excludeCount:ui.optionCount).replace('{count}',fmt(o.count||0))}`;
function multiGroup(g){
  const chosen=selected(g),free=!chosen.size;
  return `<fieldset class="facet ${free?'unconstrained':''} ${excluding(g)?'excluding':''}" data-group="${esc(g.key)}"><legend>${esc(g.label)}</legend><div class="facet-line"><div class="group-actions"><button type="button" data-action="clear" aria-label="${esc(g.label)}${ui.clearGroup}" ${free?'disabled':''}>${ui.clearGroup}</button><button type="button" data-action="invert" aria-label="${esc(g.label)}${ui.invertGroup}" ${free?'disabled':''}>${ui.invertGroup}</button><button type="button" data-action="exclude" aria-label="${esc(g.label)}${ui.excludeGroup}" aria-pressed="${excluding(g)}" ${free?'disabled':''}>${ui.excludeGroup}</button></div><div class="choices">${g.options.map(o=>`<label class="chip ${o.count?'':'empty'}" title="${esc(optionTitle(o,g))}" data-option-search="${esc((o.search||o.label).toLowerCase())}"><input type="checkbox" value="${esc(o.value)}" aria-label="${esc(g.label+'：'+o.label)}" ${chosen.has(o.value)?'checked':''}><span>${esc(o.label)}<small class="chip-count">${fmt(o.count||0)}</small></span></label>`).join('')}</div></div></fieldset>`;
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
  $('#conflict').checked=params.get('conflict')==='1';$('#sections').checked=params.get('sections')==='1';
  renderSortOptions();
  const stacked=params.get('versions')!=='each';$('#stack-versions').checked=stacked;
  $('#prefer').value=params.get('prefer')||'5r';$('#prefer').disabled=!stacked;$('.prefer-control').classList.toggle('inactive',!stacked);
  // A shared link may carry a presentation other than the default; the menu marks it so the rows explain themselves.
  $('#display-toggle').classList.toggle('changed',!stacked||params.has('prefer'));
  const desc=params.get('dir')==='desc';$('#direction').textContent=desc?'降序 ↓':'升序 ↑';
  $('#direction').setAttribute('aria-label',desc?'切换为升序':'切换为降序');
  $('#title').textContent=typeTitle();updatePageChrome();
  for(const key of ['cr_min','cr_max'])if($('#'+key))$('#'+key).value=params.get(key)||'';
  for(const g of groups){
    const el=document.querySelector(`[data-group="${g.key}"]`);if(!el)continue;
    const values=selected(g);el.classList.toggle('unconstrained',!values.size);el.classList.toggle('excluding',excluding(g));
    for(const input of el.querySelectorAll('input[type=checkbox]'))input.checked=values.has(input.value);
    for(const b of el.querySelectorAll('[data-action]'))b.disabled=!values.size;
    el.querySelector('[data-action=exclude]')?.setAttribute('aria-pressed',String(excluding(g)));
    for(const b of el.querySelectorAll('[data-flag]'))b.setAttribute('aria-pressed',(params.get(g.key)||'')===b.dataset.flag);
  }
  // Every condition is its own chip, and chips always combine with ∧: the types lead, each kept name and body
  // search follows, then the other fields. Clicking a chip's text flips it between “is” and “is not”; ×
  // removes it. The text still being typed is not a chip yet.
  const fieldKey=k=>k.startsWith('f_')||COMMON.has(k)||['type','cr_min','cr_max'].includes(k);
  const active=[...new Set(params.keys())].filter(k=>fieldKey(k)||k.startsWith(NOT)&&fieldKey(baseKey(k))&&!['cr_min','cr_max'].includes(baseKey(k)))
    .sort((a,b)=>(baseKey(b)==='type')-(baseKey(a)==='type')||(b==='type')-(a==='type'));
  const terms=committedTerms(),termKeys=[...terms.map((_,i)=>'q'+i),...termList('not_q').map((_,i)=>'not_q'+i),...bodyTerms().map((_,i)=>'body'+i),...termList('not_body').map((_,i)=>'not_body'+i)];
  $('#filter-count').textContent=active.length+termKeys.length||'';
  const extraCount=groups.filter(g=>(params.has(g.key)||params.has(NOT+g.key))&&$('#domain-extra')?.querySelector(`[data-group="${g.key}"]`)).length;
  if($('#extra-count'))$('#extra-count').textContent=extraCount?String(extraCount):'';
  $('#common-count').textContent=active.some(k=>['source','ruleset','origin'].includes(baseKey(k)))?'已筛选':'';
  $('#audit-count').textContent=active.some(k=>AUDIT_PARAMS.includes(baseKey(k)))?'已筛选':'';
  $('#audit-details').hidden=!auditMode&&!AUDIT_PARAMS.some(k=>params.has(k));
  const chip=key=>{
    const text=key==='cr_min'||key==='cr_max'?`${fieldNames[key]}：${params.get(key)}`:conditionText(key),negated=key.startsWith(NOT);
    const flip=negated?ui.includeInstead:ui.excludeInstead,flippable=!['cr_min','cr_max','conflict','sections'].includes(key);
    return `<span class="condition ${negated?'negated':''}">${flippable?`<button type="button" class="condition-text" data-flip="${esc(key)}" aria-label="${esc(text)}，${esc(flip)}" title="${esc(flip)}">${esc(text)}</button>`:`<span class="condition-text">${esc(text)}</span>`}`+
      `<button type="button" class="condition-remove" data-remove="${esc(key)}" aria-label="移除 ${esc(text)}" title="移除 ${esc(text)}">×</button></span>`;
  };
  const lead=active.filter(k=>baseKey(k)==='type').length;
  const chips=[...active.slice(0,lead),...termKeys,...active.slice(lead)].map(chip);
  $('#active-filters').innerHTML=chips.join(`<span class="and" role="img" aria-label="${esc(ui.and)}">∧</span>`);
  markStripEdges($('#active-filters'));
}
// The table keeps the last completed results, dimmed and inert, until the next response renders
// header, rows and paging together; a failure clears them instead of leaving stale counts behind.
const awaitingResults=()=>$('#results').getAttribute('aria-busy')==='true';
function awaitResults(status){
  $('#error').hidden=true;$('#result-count').textContent=status;$('#result-count').title='';$('#export').disabled=true;$('#load-more').disabled=true;
  $('#results').setAttribute('aria-busy','true');
}
function failResults(message,status){
  currentData=null;$('#error').hidden=false;$('#error').textContent=message;$('#result-count').textContent=status;
  $('#results').replaceChildren();$('#empty').hidden=true;$('#range').textContent='';renderLoadState();
}
const RULESET_ORDER=['5e','5r','待核'];
// Server groups carry counts; the interface supplies its own words for rules versions and legacy marks.
function readGroups(data){
  return data.groups.map(g=>({...g,options:g.options.map(o=>g.key==='ruleset'?{...o,label:rulesetShort(o.value),title:rulesetFull(o.value)}:g.key==='legacy'?{...o,label:legacy[o.value]||o.label}:o)}));
}
// The panel is rebuilt only when its fields or options change (new types, other sources in scope);
// otherwise counts and states update in place, so focus and open sections stay put while choosing.
function renderPanel(){
  const multi=groups.filter(g=>g.key.startsWith('f_')&&g.mode==='multi'),flags=groups.filter(g=>g.mode==='flag');
  const extraGroups=[...multi.slice(2),...flags];
  const extra=multi.slice(2).map(multiGroup).join('')+
    (flags.length?`<div class="flag-grid">${flags.map(flagGroup).join('')}</div>`:'')+
    (schema.flags.includes('special_material')?'<p class="material-note">特殊材料：注明价值或会消耗的材料；“是 / 否”均限含材料的法术。</p>':'');
  const focused=$('#filter-panel').contains(document.activeElement)?document.activeElement:null;
  const focusAt=focused&&{group:focused.closest('[data-group]')?.dataset.group,value:focused.value,action:focused.dataset.action};
  $('#type-filters').innerHTML=multiGroup(groups.find(g=>g.key==='type'));
  $('#domain-filters').innerHTML=(schema.range?rangeGroup():'')+multi.slice(0,2).map(multiGroup).join('')+
    (extra?`<details id="domain-extra" ${extraGroups.some(g=>params.has(g.key)||params.has(NOT+g.key))||$('#domain-extra')?.open?'open':''}><summary>${esc(ui.moreFilters)} <span id="extra-count"></span></summary>${extra}</details>`:'');
  $('#domain-extra')?.addEventListener('toggle',queueUiSave);
  $('#common-filters').innerHTML=groups.filter(g=>['ruleset','origin'].includes(g.key)).map(multiGroup).join('');
  $('#source-filters').innerHTML=multiGroup(groups.find(g=>g.key==='source'));filterSources();
  $('#audit-filters').innerHTML=groups.filter(g=>['legacy','status'].includes(g.key)).map(multiGroup).join('');
  if(focusAt?.group){
    const box=document.querySelector(`[data-group="${CSS.escape(focusAt.group)}"]`);
    (focusAt.action?box?.querySelector(`[data-action="${focusAt.action}"]`):box?.querySelector(`input[value="${CSS.escape(focusAt.value||'')}"]`))?.focus();
  }
}
function updateCounts(){
  for(const g of groups){
    const box=document.querySelector(`[data-group="${CSS.escape(g.key)}"]`);if(!box)continue;
    for(const o of g.options){
      const input=box.querySelector(`input[value="${CSS.escape(o.value)}"]`);if(!input)continue;
      const chip=input.closest('.chip');chip.classList.toggle('empty',!o.count);chip.title=optionTitle(o,g);chip.querySelector('.chip-count').textContent=fmt(o.count||0);
    }
  }
}
function applyFacets(data){
  facetData=data;schema=data.schema;groups=readGroups(data);
  const shape=JSON.stringify(groups.map(g=>[g.key,g.mode,g.options.map(o=>o.value)]));
  if(shape!==panelShape){panelShape=shape;renderPanel();}else updateCounts();
  $('#type-total').textContent=`${fmt(data.scope_total)} 个版本候选`;
  renderElsewhere();sync();
}
const facetsURL=()=>'/api/facets?'+new URLSearchParams([...params].filter(([k])=>!['entry','sort','dir','versions','prefer'].includes(k)));
// Counts follow every condition, so they refresh beside each query; a late response never replaces a newer one.
async function refreshFacets(){
  const gen=++facetGeneration;
  try{const data=await api(facetsURL());if(gen===facetGeneration)applyFacets(data);}catch{/* The results report query errors; counts keep their last values. */}
}
// New types change the table itself: its columns, fields and sorts arrive before the rows are read.
async function changeTable(){
  const gen=++panelGeneration,fgen=++facetGeneration;request?.abort();generation++;ready=false;
  awaitResults('正在读取筛选…');$('.table-wrap').scrollTop=0;$('.table-wrap').scrollLeft=0;
  try{
    const data=await api(facetsURL());if(gen!==panelGeneration||fgen!==facetGeneration)return;
    // A link may carry conditions the table cannot answer; they leave with the old types.
    const answerable=new Set([...data.schema.facets,...data.schema.flags]);
    const stale=[...params.keys()].filter(key=>baseKey(key).startsWith('f_')&&!answerable.has(baseKey(key).slice(2))||['cr_min','cr_max'].includes(key)&&!data.schema.range);
    if(stale.length){stale.forEach(k=>params.delete(k));return changeTable();}
    applyFacets(data);
    if(params.has('sort')&&![...schema.sorts,'match'].includes(params.get('sort')))params.delete('sort');
    $('#source-details').open=['source','ruleset','origin'].some(k=>params.has(k));
    $('#audit-details').open=AUDIT_PARAMS.some(k=>params.has(k));
    historyUpdate(true);sync();ready=true;await load();
  }catch(e){if(gen===panelGeneration){failResults(e.message,'读取失败');$('#columns').replaceChildren();$('#results').setAttribute('aria-busy','false');}}
}
function renderColumns(){
  $('#columns').innerHTML='<tr>'+visibleColumns().map(k=>{
    const sortable=schema.sorts.includes(k),active=effectiveSort()===k;
    const order=params.get('dir')==='desc'?'descending':'ascending';
    return `<th scope="col" ${active?`aria-sort="${order}"`:''}>${sortable?`<button data-sort="${esc(k)}" aria-label="按${esc(fieldNames[k]||k)}排序">${esc(fieldNames[k]||k)} <span>${active?(order==='descending'?'↓':'↑'):'↕'}</span></button>`:esc(fieldNames[k]||k)}</th>`;
  }).join('')+'</tr>';
}
// A composite column shows several boolean fields as one glyph strip; filters and export keep the parts.
function marksHTML(row,key){
  return `<span class="marks">${schema.composites[key].map(field=>{
    const [glyph,name]=ui.marks[field],value=row.quickref[field]||['__unknown__'];
    const special=field==='material'&&row.quickref.special_material?.[0]==='true';
    const state=row.field_conflicts.includes(field)||value[0]==='__conflict__'?'conflict':value[0]==='true'?(special?'special':'present'):value[0]==='false'?'absent':'unknown';
    const label=state==='present'?name:ui.markStates[state].replace('{name}',name);
    return `<span class="mark ${state}" title="${esc(label)}"><span aria-hidden="true">${esc(glyph)}${state==='special'?'*':state==='conflict'?'⚠':''}</span><span class="sr-only">${esc(label)}</span></span>`;
  }).join('')}</span>`;
}
// A yes/no column is scanned for the yeses: ✓ carries the weight, a faint dot marks no, — stays “not extracted”.
function flagHTML(row,key){
  const token=row.quickref[key]?.length===1?row.quickref[key][0]:'__conflict__';
  if(token!=='true'&&token!=='false')return null;
  return `<span class="flag ${token==='true'?'yes':'no'}"><span aria-hidden="true">${token==='true'?'✓':'·'}</span><span class="sr-only">${esc(ui.flags[token])}</span></span>`;
}
const versionSource=v=>v.source_ids.map(sourceShort).join(' / ');
const versionLabel=v=>`${versionSource(v)} ${rulesetShort(v.ruleset)}`;
const versionTitle=v=>`${v.source_names.join('；')} · ${rulesetFull(v.ruleset)}`;
// Other versions' values sit under the displayed one, so a rule change is visible in place.
function versionDiffs(row,key){
  const ids=row.version_diffs?.[key];if(!ids)return '';
  return ids.map(id=>{const v=row.versions.find(v=>v.id===id);
    const value=schema.composites?.[key]?marksHTML(v,key):flagHTML(v,key)||esc(fieldText(v,key,true));
    return `<div class="version-diff"><span class="version-source">${esc(versionSource(v))}</span> ${value}</div>`;}).join('');
}
function versionPills(row){
  if(!(row.versions?.length>1))return '';
  const status=ui.stackStatus[row.stack?.status]||ui.stackStatus.same_name_candidate;
  return `<div class="versions" role="group" aria-label="${esc(status.replace('{count}',row.versions.length))}" title="${esc(status.replace('{count}',row.versions.length))}">${row.versions.map((v,i)=>
    `<button type="button" class="version-pill ${i===0?'shown':''} ${v.ruleset==='待核'?'unverified':''}" data-entry="${esc(v.id)}" title="${esc(versionTitle(v)+(i===0?' · '+ui.shownVersion:''))}">${esc(versionLabel(v))}</button>`).join('')}</div>`;
}
function cell(row,key){
  const label=`data-label="${esc(fieldNames[key]||key)}"`,differs=row.version_diffs?.[key]?' differs':'';
  if(schema.composites?.[key]){
    const raw=key==='components'?pretty(row.fields.components):'';
    return `<td ${label} class="${differs}" ${raw?`title="${esc(raw)}"`:''}>${marksHTML(row,key)}${versionDiffs(row,key)}</td>`;
  }
  if(key==='name')return `<td class="name-cell"><button class="entry-name" data-entry="${esc(row.id)}">${esc(row.name_zh||row.name)}</button><div class="english">${esc(row.name_en)}</div>${row.field_conflicts.length?'<span class="row-warning" title="存在字段冲突；打开详情核对">⚠</span>':''}${versionPills(row)}</td>`;
  if(key==='sources')return `<td class="source-cell" ${label}>${row.source_ids.map(id=>`<abbr title="${esc(sourceName(id))}">${esc(sourceShort(id))}</abbr>`).join(' / ')}</td>`;
  if(key==='type')return `<td ${label}>${esc(row.type_label)}</td>`;
  if(key==='ruleset')return `<td ${label}><span class="badge ${row.ruleset==='待核'?'amber':'gray'}" title="${esc(rulesetFull(row.ruleset))}">${esc(rulesetShort(row.ruleset))}</span></td>`;
  if(key==='legacy')return `<td ${label}>${esc(legacy[row.legacy])}</td>`;
  if(key==='classification_status')return `<td ${label}>${esc(row.classification_status)}</td>`;
  const text=fieldText(row,key,true),flag=flagHTML(row,key);
  const conflict=row.field_conflicts.includes(key),full=fieldText(row,key);
  return `<td ${label} class="${['prerequisite','attunement_requirements','item_subtypes'].includes(key)?'wide-cell':''} ${text==='—'?'missing':''} ${conflict?'conflicted':''}${differs}" title="${esc((conflict?'字段冲突：':'')+full)}"><span class="cell-value">${flag||esc(text)}${conflict&&!text.includes('⚠')?' ⚠':''}</span>${versionDiffs(row,key)}</td>`;
}
const rowHTML=(row,columns)=>'<tr>'+columns.map(k=>cell(row,k)).join('')+'</tr>';
function renderResults(){
  renderColumns();const columns=visibleColumns();
  $('#results').innerHTML=(currentData?.rows||[]).map(r=>rowHTML(r,columns)).join('');
  updateReaderNavigation();
}
// Results arrive in batches. A new query replaces the list (old rows stay dimmed until then); scrolling
// near the end, the “load more” button, the reader's next arrow and ArrowDown on the last name append
// the next batch without dimming what is already shown. History restores how many rows were loaded.
const BATCH=150, RESTORE_LIMIT=1500;
let appending=null, appendPromise=null, appendFailed=false, restoreRows=0;
const NEAR_END=600;
// Measured directly: after an append, the observer's last report still describes the old layout.
function nearEnd(){
  const row=$('#load-more-row');if(row.hidden)return false;
  return row.getBoundingClientRect().top<$('.table-wrap').getBoundingClientRect().bottom+NEAR_END;
}
const entriesURL=(offset,limit)=>'/api/entries?'+new URLSearchParams([...params,['offset',offset],['limit',limit]]);
const hasMore=()=>!!currentData&&currentData.rows.length<currentData.total;
function renderLoadState(){
  const data=currentData,unit=ui.units[data?.versions]||ui.units.each;
  $('#range').textContent=!data?'':!data.total?ui.noResults:(hasMore()?ui.loadedRange:ui.allLoaded).replace('{shown}',fmt(data.rows.length)).replace('{total}',fmt(data.total)).replace('{unit}',unit);
  $('#load-more-row').hidden=!hasMore();
  $('#load-more').disabled=!!appending||awaitingResults();
  $('#load-more').textContent=appending?ui.loadingMore:appendFailed?ui.retryLoad:ui.loadMore;
  $('#load-more').setAttribute('aria-busy',String(!!appending));
}
// Scrolling, the button, ArrowDown and the reader share one in-flight batch; callers await the same result.
function loadMore(){
  if(appending)return appendPromise;
  if(!hasMore()||awaitingResults())return Promise.resolve(false);
  return appendPromise=appendBatch();
}
async function appendBatch(){
  const gen=generation,controller=new AbortController();appending=controller;appendFailed=false;$('#load-error').hidden=true;renderLoadState();
  try{
    const data=await api(entriesURL(currentData.rows.length,BATCH),controller.signal);
    if(gen!==generation||appending!==controller)return false;
    currentData.rows.push(...data.rows);
    $('#results').insertAdjacentHTML('beforeend',data.rows.map(r=>rowHTML(r,visibleColumns())).join(''));
    updateReaderNavigation();queueUiSave();return true;
  }catch(e){
    if(e.name==='AbortError'||gen!==generation)return false;
    appendFailed=true;$('#load-error').hidden=false;$('#load-error').textContent=e.message;return false;
  }finally{
    if(appending===controller){appending=null;renderLoadState();if(!appendFailed&&nearEnd())setTimeout(loadMore);}
  }
}
new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)&&!appendFailed)loadMore();},
  {root:$('.table-wrap'),rootMargin:`0px 0px ${NEAR_END}px 0px`}).observe($('#load-more-row'));
$('#load-more').onclick=()=>loadMore();
async function load(){
  if(!ready)return;request?.abort();appending?.abort();appending=null;appendFailed=false;$('#load-error').hidden=true;
  request=new AbortController();const gen=++generation;
  awaitResults('正在查询…');updateReaderNavigation();
  const limit=Math.min(RESTORE_LIMIT,Math.max(BATCH,Math.ceil(restoreRows/BATCH)*BATCH));restoreRows=0;
  try{
    const data=await api(entriesURL(0,limit),request.signal);if(gen!==generation)return;
    currentData=data;
    $('#result-count').innerHTML=data.versions==='stacked'?ui.stackedCount.replace('{rows}',`<strong>${fmt(data.total)}</strong>`).replace('{versions}',fmt(data.versions_total)):`找到 <strong>${fmt(data.total)}</strong> 条`;
    // A narrow results area cuts the count short rather than wrapping it; the whole count stays on hover.
    $('#result-count').title=$('#result-count').textContent;
    renderResults();
    $('#empty').hidden=data.total!==0;$('#export').disabled=!data.total;
  }catch(e){if(e.name==='AbortError'||gen!==generation)return;failResults(e.message,'查询失败');renderColumns();}
  finally{if(gen===generation){$('#results').setAttribute('aria-busy','false');renderLoadState();updateReaderNavigation();saveUiState();}}
}
// Stacking and edition preference change presentation only; the open entry stays matched, so the reader stays.
function presented(){clearTimeout(timer);historyUpdate();sync();$('.table-wrap').scrollTop=0;load();}
$('#stack-versions').onchange=()=>{setParam('versions',$('#stack-versions').checked?'':'each');presented();};
$('#prefer').onchange=()=>{setParam('prefer',$('#prefer').value==='5r'?'':$('#prefer').value);presented();};
function sortBy(key){if(effectiveSort()===key)setParam('dir',params.get('dir')==='desc'?'asc':'desc');else{params.set('sort',key);params.delete('dir');}changed();}
$('#filter-panel').addEventListener('change',e=>{
  const el=e.target;
  if(el.type==='checkbox'&&el.closest('[data-group]')){
    const g=groups.find(g=>g.key===el.closest('[data-group]').dataset.group);const set=selected(g);
    el.checked?set.add(el.value):set.delete(el.value);
    if(g.key==='type'&&!excluding(g)){chooseTypes([...set]);return;}
    setSelection(g,[...set]);
    if(g.key==='status'&&set.size===1&&set.has('未分类段落'))params.set('sections','1');changed();
  }else if(['cr_min','cr_max'].includes(el.id)){
    setParam(el.id,el.value);const val=s=>s.includes('/')?s.split('/').map(Number).reduce((a,b)=>a/b):Number(s);
    if(params.has('cr_min')&&params.has('cr_max')&&val(params.get('cr_min'))>val(params.get('cr_max'))){const other=el.id==='cr_min'?'cr_max':'cr_min';params.set(other,el.value);$('#'+other).value=el.value;}changed();
  }else if(['conflict','sections'].includes(el.id)){setParam(el.id,el.checked?'1':'');changed();}
});
$('#filter-panel').addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;const group=b.closest('[data-group]');if(!group)return;const g=groups.find(g=>g.key===group.dataset.group);
  if(b.hasAttribute('data-flag')){setParam(g.key,b.dataset.flag);changed();return;}
  if(b.dataset.action==='exclude'){flipField(modeKey(g));return;}
  if(b.dataset.action==='clear'){clearField(g.key);if(g.key==='type')chooseTypes([]);else changed();return;}
  const current=selected(g),next=g.options.map(o=>o.value).filter(v=>!current.has(v));
  if(g.key==='type'&&!excluding(g)){chooseTypes(next);return;}
  setSelection(g,next);changed();
});
$('#source-search').addEventListener('input',()=>{filterSources();saveUiState();});
// Name searches are conditions like any other. Kept searches are `q`; the text being typed is `typing`, a live
// search that Enter keeps as a chip, so a reload returns it to the field rather than making it a chip.
// Both are page state, read from the URL only on load and history navigation: the URL trails the field by the
// debounce, and inferring kept searches from it once took half-typed IME text for a kept search.
const searchTerms=()=>[...new Set(params.getAll('q').map(t=>t.trim()).filter(Boolean))];
// Body searches look in an entry's text after its title; they are added from the suggestions, never typed live.
// Excluded searches (`not_q`, `not_body`) come from flipping a search chip.
const termList=key=>[...new Set(params.getAll(key).map(t=>t.trim()).filter(Boolean))];
const bodyTerms=()=>termList('body');
function setList(key,terms){params.delete(key);[...new Set(terms)].forEach(t=>params.append(key,t));}
// A search chip's key names its list and position: q0, not_q1, body0, not_body2.
function termAt(key){const [,list,i]=key.match(/^((?:not_)?(?:q|body))(\d+)$/);return {list,index:Number(i),terms:list==='q'?committed:termList(list)};}
function writeList(list,terms){if(list==='q'){committed=terms;writeTerms();}else setList(list,terms);}
function flipTerm(key){
  const {list,index,terms}=termAt(key),term=terms[index],to=list.startsWith(NOT)?baseKey(list):NOT+list;
  writeList(list,terms.filter((_,i)=>i!==index));writeList(to,[...(to==='q'?committed:termList(to)),term]);changed();
}
let committed=[],draft='';
const committedTerms=()=>committed;
const searching=()=>committed.length>0||!!draft;
function writeTerms(){params.delete('q');[...new Set(committed)].forEach(t=>params.append('q',t));setParam('typing',draft);}
function readTerms(){committed=searchTerms();draft=(params.get('typing')||'').trim();$('#q').value=draft;}
function clearDraft(){draft='';suggestionGroups=null;suggestionGeneration++;$('#q').value='';closeSuggestions();}
let composingSearch=false;
function queueSearch(){
  clearTimeout(timer);if(composingSearch)return;
  draft=$('#q').value.trim();renderSuggestions();
  timer=setTimeout(()=>{writeTerms();changed(true);refreshSuggestionCounts();},160);
}
// Choosing a suggested value drops the draft, so suggestions count with the draft left out.
let suggestionGroups=null,suggestionGeneration=0;
async function refreshSuggestionCounts(){
  const gen=++suggestionGeneration,query=new URLSearchParams([...params].filter(([k])=>!['entry','sort','dir','versions','prefer','typing'].includes(k)));
  try{const data=await api('/api/facets?'+query);if(gen!==suggestionGeneration)return;suggestionGroups=readGroups(data);
    if(!$('#suggestions').hidden){const keep=activeSuggestion;renderSuggestions();if(keep<suggestions.length){activeSuggestion=0;moveSuggestion(keep);}}}catch{/* Suggestions keep their last counts. */}
}
$('#q').addEventListener('compositionstart',()=>{composingSearch=true;clearTimeout(timer);});
$('#q').addEventListener('compositionend',()=>{composingSearch=false;queueSearch();});
$('#q').addEventListener('input',e=>{if(!e.isComposing)queueSearch();});
$('#q').addEventListener('focus',()=>{if($('#q').value.trim())renderSuggestions();});
$('#q').addEventListener('blur',()=>setTimeout(()=>{if(document.activeElement!==$('#q'))closeSuggestions();},0));
$('#search-form').addEventListener('submit',e=>e.preventDefault());
// Typing offers the name search first, then any field value whose label matches: “法术” finds the type,
// “塑能” the school, “PHB” the sources, “坐骑” the preset. Choosing a value adds it to its field.
let suggestions=[],activeSuggestion=0;
const fold=t=>String(t||'').normalize('NFKC').toLowerCase();
function suggestionList(text){
  const q=fold(text);if(!q)return [];
  const visible=g=>auditMode||!['legacy','status'].includes(g.key);
  const values=(suggestionGroups&&draft?suggestionGroups:groups).filter(g=>g.mode==='multi'&&visible(g)).flatMap(g=>{const chosen=new Set(params.getAll(g.key));
    return g.options.filter(o=>!chosen.has(o.value)&&(fold(o.label).includes(q)||fold(o.search).includes(q)))
      .map(o=>({kind:'value',group:g,option:o,field:g.label,label:o.label,count:o.count||0,rank:fold(o.label)===q?0:fold(o.label).startsWith(q)?1:2}));});
  const presets=(metadata?.presets||[]).filter(p=>fold(p.name).includes(q)).map(p=>({kind:'preset',preset:p,field:ui.presetField,label:`${p.name}（${typeName(p.type)}）`,count:null,rank:fold(p.name)===q?0:1}));
  const ranked=[...values,...presets].sort((a,b)=>a.rank-b.rank||(b.count??-1)-(a.count??-1)).slice(0,8);
  return [{kind:'name',field:ui.nameField,label:ui.nameContains.replace('{text}',text)},
    {kind:'body',field:ui.bodyField,label:ui.nameContains.replace('{text}',text)},...ranked];
}
function renderSuggestions(){
  const text=$('#q').value.trim();suggestions=suggestionList(text);activeSuggestion=0;
  const list=$('#suggestions');
  if(!suggestions.length){closeSuggestions();return;}
  list.innerHTML=suggestions.map((s,i)=>`<li role="option" id="suggestion-${i}" data-index="${i}" aria-selected="${i===activeSuggestion}"><span class="field">${esc(s.field)}</span><span class="value">${esc(s.label)}</span>${s.kind==='name'?'<kbd>Enter</kbd>':s.count!==null?`<span class="count">${fmt(s.count)}</span>`:''}</li>`).join('');
  list.hidden=false;$('#q').setAttribute('aria-expanded','true');$('#q').setAttribute('aria-activedescendant','suggestion-0');
}
function closeSuggestions(){$('#suggestions').hidden=true;$('#q').setAttribute('aria-expanded','false');$('#q').removeAttribute('aria-activedescendant');}
function moveSuggestion(step){
  if($('#suggestions').hidden)return;
  activeSuggestion=(activeSuggestion+step+suggestions.length)%suggestions.length;
  for(const li of $('#suggestions').children)li.setAttribute('aria-selected',String(Number(li.dataset.index)===activeSuggestion));
  $('#q').setAttribute('aria-activedescendant','suggestion-'+activeSuggestion);
  $('#suggestion-'+activeSuggestion).scrollIntoView({block:'nearest'});
}
function applySuggestion(item){
  clearTimeout(timer);
  if(!item||item.kind==='name'){
    // Keep the draft as a search chip; the field empties for the next search.
    const text=$('#q').value.trim();clearDraft();if(!text)return;
    committed=[...new Set([...committed,text])];writeTerms();changed();return;
  }
  if(item.kind==='body'){
    const text=$('#q').value.trim();clearDraft();writeTerms();
    if(!bodyTerms().includes(text)){params.append('body',text);}changed();return;
  }
  // The typed text found the value; it was not meant as a name search.
  clearDraft();writeTerms();
  if(item.kind==='preset'){chooseTypes([item.preset.type],item.preset);return;}
  // A suggestion always includes its value; if the value was excluded, it leaves the excluded list.
  const g=item.group,v=item.option.value,values=[...params.getAll(g.key).filter(x=>x!==NONE),v];
  setSelection(g,params.getAll(NOT+g.key).filter(x=>x!==v),NOT+g.key);
  if(g.key==='type'){chooseTypes(values);return;}
  setSelection(g,values,g.key);changed();
}
$('#q').addEventListener('keydown',e=>{
  // Safari reports the key that ends a composition after compositionend, with keyCode 229; it is not an Enter.
  if(e.isComposing||composingSearch||e.keyCode===229)return;
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){if(!$('#suggestions').hidden){e.preventDefault();moveSuggestion(e.key==='ArrowDown'?1:-1);}return;}
  if(e.key==='Enter'){e.preventDefault();applySuggestion($('#suggestions').hidden?null:suggestions[activeSuggestion]);return;}
  if(e.key==='Escape'&&!$('#suggestions').hidden){e.preventDefault();e.stopPropagation();closeSuggestions();return;}
  // An empty field gives Backspace to the conditions: it removes the last chip.
  if(e.key==='Backspace'&&!$('#q').value){const last=[...$('#active-filters').querySelectorAll('[data-remove]')].at(-1);if(last){e.preventDefault();dropCondition(last.dataset.remove);}}
});
$('#suggestions').addEventListener('pointerdown',e=>{const li=e.target.closest('[data-index]');if(!li)return;e.preventDefault();applySuggestion(suggestions[Number(li.dataset.index)]);});
$('#toggle-filters').onclick=()=>{const hidden=!$('#filter-panel').hidden;$('#filter-panel').hidden=hidden;$('#toggle-filters').setAttribute('aria-expanded',!hidden);saveUiState();};
// Clearing removes every condition, the types included; presentation choices (sort, stacking) stay.
$('#reset').onclick=()=>{
  clearDraft();committed=[];
  for(const key of [...params.keys()])if(!['sort','dir','versions','prefer'].includes(key))params.delete(key);
  historyUpdate();closeEntry({update:false,restoreFocus:false});changeTable();
};
function dropCondition(key){
  if(key==='type'){chooseTypes([]);return;}
  // A chip's key indexes the kept searches; whatever is being typed stays in the field.
  if(/^(not_)?(q|body)\d+$/.test(key)){const {list,index,terms}=termAt(key);writeList(list,terms.filter((_,i)=>i!==index));changed();return;}
  if(key==='cr'){params.delete('cr_min');params.delete('cr_max');}else params.delete(key);
  changed();
}
$('#active-filters').addEventListener('click',e=>{
  const remove=e.target.closest('[data-remove]');if(remove){dropCondition(remove.dataset.remove);return;}
  const flip=e.target.closest('[data-flip]');if(!flip)return;
  const key=flip.dataset.flip;/^(not_)?(q|body)\d+$/.test(key)?flipTerm(key):flipField(key);
});
$('#sort').onchange=()=>{params.set('sort',$('#sort').value);changed();};
$('#direction').onclick=()=>{setParam('dir',params.get('dir')==='desc'?'asc':'desc');changed();};
$('#columns').addEventListener('click',e=>{if(awaitingResults())return;const b=e.target.closest('[data-sort]');if(b)sortBy(b.dataset.sort);});
$('#export').onclick=()=>{location.href='/api/export?'+params;};
// The whole row is the target: anywhere on it reads the row's entry, a version pill reads that version.
// Selecting text in a row reads nothing, so values stay copyable.
let resultClick=false;
$('#results').addEventListener('click',e=>{
  if(awaitingResults())return;
  const pill=e.target.closest('[data-entry]');
  const row=!pill&&!e.target.closest('a,button,input,select,summary,label')&&getSelection().isCollapsed?e.target.closest('tr'):null;
  const target=pill||row?.querySelector('.entry-name');if(!target)return;
  resultClick=true;openEntry(target.dataset.entry,true,'body',true);
});
// A double click reads the result at full width. The first click has already opened the entry, so the second,
// which lands on the same row, means only that: it neither reopens the row nor selects its text.
document.addEventListener('click',e=>{
  if(e.detail<2){resultClick=false;return;}
  if(!resultClick)return;
  e.preventDefault();e.stopPropagation();
  if(e.detail===2&&currentReaderId&&widenableReader.matches)setReaderFull(true);
},true);
for(const type of ['mousedown','dblclick'])document.addEventListener(type,e=>{if(e.detail>1&&resultClick){e.preventDefault();e.stopPropagation();}},true);
$('#results').addEventListener('keydown',e=>{
  if(e.isComposing||!['ArrowUp','ArrowDown'].includes(e.key))return;
  const b=e.target.closest('.entry-name');if(!b)return;
  const buttons=[...$('#results').querySelectorAll('.entry-name')],i=buttons.indexOf(b);
  const next=buttons[i+(e.key==='ArrowDown'?1:-1)];if(next){e.preventDefault();next.focus();return;}
  if(e.key==='ArrowDown'&&hasMore()){e.preventDefault();loadMore().then(ok=>{if(ok)$('#results').querySelectorAll('.entry-name')[i+1]?.focus();});}
});
$('#detail-content').addEventListener('click',e=>{
  const b=e.target.closest('[data-entry]');if(b)openEntry(b.dataset.entry,true,b.hasAttribute('data-keep-tab')?detailTab:'body');
  const t=e.target.closest('[data-tab]');if(t){renderDetail(t.dataset.tab);$('#tab-'+t.dataset.tab).focus();saveUiState();}
  if(e.target.closest('[data-retry]')&&currentReaderId)openEntry(currentReaderId,false);
  const note=e.target.closest('.field-note[data-note]');
  if(note){lightNotes([note.dataset.note],true);$('#detail-content').querySelector(`mark.note[data-notes~="${CSS.escape(note.dataset.note)}"]`)?.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}
  if(e.target.closest('[data-audit-summary]')){setAuditMode(true);renderDetail('facts');$('#tab-facts').focus();saveUiState();}
  if(e.target.closest('[data-reader-full]')){setReaderFull(true);$('#reader-full').focus();}
});
for(const [type,on] of [['mouseover',true],['focusin',true],['mouseout',false],['focusout',false]])
  $('#detail-content').addEventListener(type,e=>{const el=e.target.closest('.field-note[data-note],mark.note');if(!el)return;
    lightNotes(el.dataset.note?[el.dataset.note]:el.dataset.notes.split(' '),on);});
$('#detail-content').addEventListener('keydown',e=>{
  const t=e.target.closest('[data-tab]');if(!t||!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
  e.preventDefault();const tabs=[...$('#detail-content').querySelectorAll('[data-tab]')],index=tabs.indexOf(t);
  const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
  const name=tabs[next].dataset.tab;renderDetail(name);$('#tab-'+name).focus();saveUiState();
});
$('#close-detail').onclick=()=>closeEntry();
$('#detail').addEventListener('cancel',e=>{e.preventDefault();closeEntry();});
$('#detail').addEventListener('keydown',e=>{
  if(e.key!=='Tab'||!$('#detail').matches(':modal'))return;
  const elements=[...$('#detail').querySelectorAll('a[href],button,input,select,textarea,summary,[tabindex]')]
    .filter(el=>el.tabIndex>=0&&!el.disabled&&el.getClientRects().length);
  const first=elements[0],last=elements.at(-1);
  if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
  else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
});
$('#entry-prev').onclick=()=>stepEntry(-1);$('#entry-next').onclick=()=>stepEntry(1);
let navigationGeneration=0;
window.addEventListener('popstate',async e=>{
  const gen=++navigationGeneration,state=e.state?.anna,oldTypes=chosenTypes().join();
  clearTimeout(timer);clearTimeout(uiSaveTimer);restoringUi=true;
  params=new URLSearchParams(location.search);restoreRows=state?.loadedRows||0;clearDraft();readTerms();
  if(!params.has('entry'))closeEntry({update:false,restoreFocus:false});
  if(oldTypes===chosenTypes().join()&&ready){sync();refreshFacets();await load();}else await changeTable();
  if(gen!==navigationGeneration)return;
  if(params.has('entry'))await openEntry(params.get('entry'),false,state?.readerId===params.get('entry')?state.readerTab:'body');
  if(gen!==navigationGeneration)return;
  restoreUiState(state);
  if(currentReaderId)($('#tab-'+detailTab)||$('#close-detail')).focus({preventScroll:true});
  else if(state?.lastResultEntry)focusResult();
  restoringUi=false;saveUiState();
});
document.addEventListener('keydown',e=>{
  if(e.isComposing)return;
  if(e.key==='Escape'&&!$('#display-panel').hidden){e.preventDefault();setDisplayMenu(false,{focus:true});return;}
  if(e.key==='Escape'&&$('#detail').open){e.preventDefault();closeEntry();return;}
  // F6 moves to the results, so full-width reading gives them their place back first.
  if(e.key==='F6'&&$('#detail').open&&wideReader.matches){
    e.preventDefault();if($('#detail').contains(document.activeElement)){if(readerFull)setReaderFull(false);focusResult();}else $('#close-detail').focus();return;
  }
  if(e.key==='/'&&!$('#detail').matches(':modal')&&!['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)&&!e.target.isContentEditable){e.preventDefault();$('#q').focus();}
});
// The active filters scroll sideways without a scrollbar, whose thumb read as a selection under the first
// item; only an edge that still hides more fades.
function markStripEdges(strip){
  strip.classList.toggle('more-before',strip.scrollLeft>1);strip.classList.toggle('more-after',strip.scrollLeft+strip.clientWidth<strip.scrollWidth-1);
}
for(const strip of [$('#active-filters')]){
  strip.addEventListener('scroll',()=>markStripEdges(strip),{passive:true});
  new ResizeObserver(()=>markStripEdges(strip)).observe(strip);
}
// Presentation choices open as one menu over the results; it stays open while choices change and closes on
// Esc or a pointer elsewhere.
function setDisplayMenu(open,{focus=false}={}){
  $('#display-panel').hidden=!open;$('#display-toggle').setAttribute('aria-expanded',String(open));
  if(open&&focus)$('#display-panel input,#display-panel select')?.focus();
  if(!open&&focus)$('#display-toggle').focus();
}
$('#display-toggle').addEventListener('click',()=>setDisplayMenu($('#display-panel').hidden));
document.addEventListener('pointerdown',e=>{
  if(!$('#display-panel').hidden&&!e.target.closest('#display-panel,#display-toggle'))setDisplayMenu(false);
});
// Entry types are one field among the others. They also pick the table's columns, fields and sorts, and the
// heading names them.
const chosenTypes=()=>params.getAll('type');
const typeName=t=>metadata?.types[t]||t;
const typeRank=t=>{const i=metadata.type_order.indexOf(t);return i<0?metadata.type_order.length:i;};
// A preset is one type plus one facet value; it names the heading while exactly that slice is chosen.
function activePreset(){
  const types=chosenTypes();if(types.length!==1||!metadata)return null;
  return metadata.presets.find(p=>p.type===types[0]&&params.getAll('f_'+p.field).join()===p.value)||null;
}
// The tab title names the open entry, else the chosen slice; the book link carries the open entry along.
function updatePageChrome(){
  const heading=typeTitle(),subject=currentEntry?(currentEntry.name_zh||currentEntry.name):heading===ui.allEntries?'':heading;
  document.title=annaPageTitle('query',subject);
  $('#book-reader').href=currentReaderId?(metadata?.reader_url||'/read')+'?entry='+encodeURIComponent(currentReaderId):metadata?.reader_url||'/read';
}
function typeTitle(){
  const types=chosenTypes(),left=params.getAll(NOT+'type');
  if(!types.length)return left.length?ui.allExcept.replace('{list}',left.map(typeName).join('、')):ui.allEntries;
  const preset=activePreset();if(preset)return preset.name;
  return types.length<=2?types.map(typeName).join('、'):ui.moreTypes.replace('{first}',typeName(types[0])).replace('{count}',types.length);
}
// Changing the types keeps a type-specific condition only when every chosen type can answer it: adding 法术 to
// 魔法武器 lists the spells too instead of filtering them all out by item category.
function chooseTypes(types,preset=null){
  types=[...new Set(types)].sort((a,b)=>typeRank(a)-typeRank(b));
  params.delete('type');types.forEach(t=>params.append('type',t));
  const shared=types.length?types.map(t=>new Set(metadata.type_conditions[t])).reduce((a,b)=>new Set([...a].filter(x=>b.has(x)))):new Set();
  for(const key of [...params.keys()]){
    const base=baseKey(key),condition=base.startsWith('f_')?base.slice(2):['cr_min','cr_max'].includes(key)?'cr':null;
    if(condition&&(preset||!shared.has(condition)))params.delete(key);
  }
  if(preset)params.set('f_'+preset.field,preset.value);
  params.delete('entry');historyUpdate();closeEntry({update:false,restoreFocus:false});changeTable();
}
// While searching within some types, matches in the others stay one step away; an empty result names the
// conditions whose removal would bring results back.
function renderElsewhere(){
  const types=chosenTypes(),typeGroup=groups.find(g=>g.key==='type');
  const others=(searching()||bodyTerms().length)&&types.length?typeGroup.options.filter(o=>o.count&&!types.includes(o.value)):[];
  const total=others.reduce((sum,o)=>sum+o.count,0);
  $('#elsewhere').hidden=!total;$('#elsewhere').textContent=total?ui.elsewhere.replace('{count}',fmt(total)):'';
  $('#elsewhere').title=total?ui.elsewhereTitle.replace('{list}',others.map(o=>`${o.label} ${fmt(o.count)}`).join(' · ')):'';
  const drops=Object.entries(facetData?.without||{}).filter(([,n])=>n).map(([key,n])=>({key,n,text:conditionText(key)})).filter(d=>d.text);
  $('#empty-elsewhere').innerHTML=drops.map(d=>`<button type="button" data-drop="${esc(d.key)}">${esc(ui.dropCondition.replace('{condition}',d.text))} <span class="muted">→ ${fmt(d.n)}</span></button>`).join('');
}
function conditionText(key){
  if(key==='cr')return `${fieldNames.cr_min.replace('最低 ','')}：${params.get('cr_min')||'0'}–${params.get('cr_max')||'∞'}`;
  if(key==='conflict')return fieldNames.conflict;
  if(key==='typing')return `${ui.nameField}：${draft}`;
  const negated=key.startsWith(NOT);
  if(/^(not_)?(q|body)\d+$/.test(key)){const {list,index,terms}=termAt(key);return `${baseKey(list)==='q'?ui.nameField:ui.bodyField}${negated?ui.lacks:''}：${terms[index]}`;}
  const g=groups.find(g=>g.key===baseKey(key)),labels=params.getAll(key).map(v=>g?.options.find(o=>o.value===v)?.label||v);
  return labels.length?`${g?.label||fieldNames[baseKey(key)]||key}${negated?ui.isNot:''}：${labels.length>3?labels.slice(0,2).join('、')+` 等 ${labels.length} 项`:labels.join('、')}`:'';
}
$('#elsewhere').addEventListener('click',()=>{
  $('#filter-panel').hidden=false;$('#toggle-filters').setAttribute('aria-expanded','true');
  const box=$('#type-filters');box.scrollIntoView({block:'nearest'});box.querySelector('input')?.focus();saveUiState();
});
$('#empty-elsewhere').addEventListener('click',e=>{
  const b=e.target.closest('[data-drop]');if(!b)return;const key=b.dataset.drop;
  if(key==='typing'){clearDraft();writeTerms();changed();return;}
  dropCondition(key);
});
// Reading mode folds review apparatus away; filters already in the URL stay visible and editable.
function applyAuditMode(){
  document.documentElement.dataset.audit=auditMode?'on':'off';
  const toggle=$('#audit-mode');toggle.setAttribute('aria-pressed',String(auditMode));toggle.title=auditMode?ui.auditOn:ui.auditOff;
  $('#audit-details').hidden=!auditMode&&!AUDIT_PARAMS.some(k=>params.has(k));
  if(schema&&currentData)renderResults();else if(schema)renderColumns();
  if(currentEntry)renderDetail(AUDIT_TABS.has(detailTab)&&!auditMode?'body':detailTab);
}
function setAuditMode(on){
  auditMode=on;stored.set(AUDIT_KEY,on?'on':null); // Blocked storage still applies the mode to this page.
  applyAuditMode();saveUiState();
}
$('#audit-mode').onclick=()=>setAuditMode(!auditMode);
// Row density is a per-browser reading preference like the theme; shared links never carry it.
const DENSITY_KEY='anna.density';
const storedDensity=()=>stored.get(DENSITY_KEY)==='compact';
function applyDensity(compact){document.documentElement.dataset.density=compact?'compact':'comfortable';$('#compact-rows').checked=compact;}
$('#compact-rows').onchange=()=>{
  const compact=$('#compact-rows').checked;
  stored.set(DENSITY_KEY,compact?'compact':null);
  applyDensity(compact);
};
stored.watch(DENSITY_KEY,()=>applyDensity(storedDensity()));
applyDensity(storedDensity());
initReaderLayout();
stored.watch(AUDIT_KEY,()=>{auditMode=stored.get(AUDIT_KEY)==='on';applyAuditMode();});
async function init(){
  const saved=history.state?.anna;restoringUi=true;
  try{
    metadata=await api('/api/meta');Object.assign(fieldNames,metadata.fields);
    $('#book-reader').hidden=!metadata.reader_url;
    // Preserve older bookmarked single-attribute queries.
    // Paging was replaced by batch loading; old links keep their query and drop page and size.
    params.delete('page');params.delete('size');restoreRows=saved?.loadedRows||0;
    // Category views became type filters; an old view link opens on all entries with its other conditions.
    params.delete('view');readTerms();
    if(params.has('field')&&params.has('value')){const f=params.get('field');let v=params.get('value');if(f==='spell_level'&&LEVELS.includes(v))v=String(LEVELS.indexOf(v));params.set('f_'+f,v);params.delete('field');params.delete('value');}
    await changeTable();if(params.has('entry'))await openEntry(params.get('entry'),false,saved?.readerId===params.get('entry')?saved.readerTab:'body');
    restoreUiState(saved);
  }catch(e){$('#error').hidden=false;$('#error').textContent=e.message;$('#result-count').textContent='服务未就绪';}
  finally{restoringUi=false;saveUiState();}
}
applyAuditMode();
init();

const reviewLink=document.createElement("a");reviewLink.href="/review";reviewLink.className="audit-only";reviewLink.textContent=annaLinkLabels[document.documentElement.lang.startsWith("en")?"en":"zh"].reviewSearch.title;document.querySelector(".topbar-actions").prepend(reviewLink);
