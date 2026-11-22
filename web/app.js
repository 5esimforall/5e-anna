'use strict';
const $ = s => document.querySelector(s);
const {esc,stored}=annaKit;
// New workbench copy lives in one bilingual resource; data/source labels remain unchanged.
const annaUiLabels = {
  zh: {bundleChanged:'数据已经重建，请重启 make up，再刷新页面。',reloading:'数据正在更新，服务会在完整构建后自动换包；换包完成后刷新页面。',reloadFailed:'数据重建未通过校验，服务未切换到新快照；请查看 make upper 的终端输出并重新构建。',skipSearch:'跳到搜索',emptyTitle:'没有匹配条目',emptyHint:'试试移除已选条件，或清空全部条件。',
    readerTitle:'条目阅读',readerHint:'Esc 关闭 · F6 切换列表与详情',readerHintNarrow:'Esc 关闭 · 上方箭头连续查阅结果',readerHintTouch:'用上方箭头连续查阅结果',closeDetail:'关闭详情',
    fullWidth:'全宽阅读',resizeReader:'调整阅读栏宽度',resizeReaderHint:'拖动或用左右方向键调整阅读栏宽度，双击恢复默认',wideTable:'表格宽于阅读栏 · 全宽阅读',readerShare:'阅读栏占 {percent}%',
    previousEntry:'上一条',nextEntry:'下一条',listPosition:'第 {current} / {total} 行',
    outsideResults:'不在已加载的结果中',loadMore:'继续加载',loadingMore:'正在加载…',retryLoad:'重新加载',
    loadedRange:'已显示 {shown} / {total} {unit}',allLoaded:'已全部显示 {total} {unit}',noResults:'0 条结果',units:{stacked:'行',each:'条'},loading:'正在读取条目…',retry:'重新读取',readError:'无法读取条目',
    body:'正文',fields:'属性',relations:'关系',
    detailTabs:'详情栏目',moreFilters:'更多筛选条件',allEntries:'全部条目',
    librarySettings:'资料库设置…',
    emptyLibrary:'资料库设置收窄了规则版本或作品范围，部分条目未列出。',filterLibraryHint:'资料库、规则版本与作品范围在顶栏「资料库」中设置。',
    libraryState:{narrowed:'已收窄',each:'不去重'},
    collectionErrors:{invalid_collection:'资料库地址无效。',unknown_collection:'未找到这个资料库。',collection_unavailable:'这个资料库尚未接入。'},
    moreTypes:'{first}等 {count} 类',nameField:'名称',bodyField:'正文',and:'且',excludeGroup:'排除',allExcept:'全部条目（不含{list}）',excludeInstead:'改为排除',includeInstead:'改为包含',isNot:'不是',lacks:'不含',excludeCount:'排除它会去掉 {count} 条',nameContains:'包含“{text}”',presetField:'预设',clearGroup:'清除',invertGroup:'反选',optionCount:'选中后 {count} 条',
    elsewhere:'其他类型另有 {count} 条',elsewhereTitle:'搜索在其他类型中的匹配：{list}',dropCondition:'去掉 {condition}',
    problems:'查询提示',unknownField:'anna 不认识“{condition}”这个条件，它不影响结果。',lacksSome:'{types}没有{fields}，不会出现在结果中。',lacksAll:'{types}没有{fields}，没有条目满足{conditions}。',
    crInverted:'最低 CR {min} 高于最高 CR {max}，两者不能同时成立。',crUnreadable:'CR “{value}”不是数字或分数。',queryRejected:'这个查询无法执行：{message}',queryHeld:'条件需要修改',
    fixRemove:'移除“{condition}”',fixKeepTypes:'类型只保留{types}',fixSwapCr:'改为 CR {min}–{max}',fixClearAll:'清空条件',listJoin:'、',
    marks:{verbal:['言','言语'],somatic:['姿','姿势'],material:['材','材料'],ritual:['仪','仪式'],concentration:['专','专注']},
    markStates:{absent:'无{name}',unknown:'{name}未提取',conflict:'{name}存在冲突',special:'{name}（特殊材料）'},
    stackVersions:'合并同名版本',compactRows:'紧凑行',sortMatch:'匹配度',preferRuleset:'优先显示',sortLabel:'排序',displayOptions:'显示',
    stackedCount:'找到 {rows} 行 · {versions} 个版本',shownVersion:'当前行显示此版本',
    stackStatus:{same_name_candidate:'同名候选 {count} 个版本，各自独立',exact_text_candidate:'同文候选 {count} 个版本，各自独立',reprint_group:'同名/重印版本 {count} 个，各自独立',library_candidate:'跨资料库同名候选 {count} 个版本，各自独立'},
    fieldNotes:'字段批注',noteElsewhere:'证据不在本段原文',
    layoutUnavailable:{missing:'找不到原文页面，显示提取文本。',stale:'原文已更新，与数据库快照不一致，显示提取文本。',empty:'原文排版为空，显示提取文本。',error:'无法读取原文排版，显示提取文本。'},
    versionsTab:'版本对照',versionsIntro:'同名候选按规则版本排列；候选关系不代表同一规则身份。',versionsIntroLibrary:'同名候选来自资料库中的各个资料库；候选关系不代表同一规则身份。',differingFields:'不同的字段',sameFields:'相同或未提取的字段 {count} 项',currentVersion:'当前',
    relationNames:{translation:'同一作品',derived:'派生',candidate:'同名候选'},
    relationHints:{translation:'同一部作品在另一资料库中的文本',derived:'复制自同名条目或其所在作品，并作了修改',candidate:'只有类型与英文名相同，尚无证据表明是同一对象'},
    diffAgainst:'标出与 {name} 的不同',diffShown:'正在标出与 {name} 的不同',diffLegend:'高亮：仅本版有　删除线：仅 {name} 有',diffNone:'与 {name} 的正文相同。',diffTooMany:'与 {name} 的正文差异较多，逐处标出反而难读；请在「版本对照」中逐字段比较。',diffUnavailable:'无法读取 {name} 的排版，不能标出差异。',
    rulesets:{'5e':['2014','2014 版规则（5e）'],'5r':['2024','2024 版规则（5r）'],'待核':['待核','规则版本待核']},
    provenance:'来源与出处',provCollection:'资料库',provOrigin:'来源性质',provPackage:'内容包',provOurs:'本站自有',provSource:'书籍来源',provAuthors:'作者',provTranslators:'译者',provTier:'社区分档',provRating:'评分',provCopiedFrom:'模板继承',provLicense:'许可与说明',provMedia:'媒体资源',provMediaLocal:'离线收录 {count} 项',provMediaRemote:'远端未随包 {count} 项',provMediaNone:'无外部媒体',provReadBook:'在书中阅读',provViewLink:'查看来源说明 ↗',
    flags:{true:'是',false:'否'},legend:'✓ 是　· 否　— 未提取　⚠ 存在字段冲突　·　点击结果行查看正文与证据'},
  en: {bundleChanged:'The data was rebuilt. Restart make up, then refresh this page.',reloading:'The data is being updated; the service switches snapshots after a complete build. Refresh when it finishes.',reloadFailed:'The rebuild did not validate, so the service kept its previous snapshot. Check the make upper terminal and build again.',skipSearch:'Skip to search',emptyTitle:'No matching entries',emptyHint:'Remove an active condition or clear them all.',
    readerTitle:'Entry reader',readerHint:'Esc to close · F6 to switch between results and reader',readerHintNarrow:'Esc to close · Use the arrows above to browse the results',readerHintTouch:'Use the arrows above to browse the results',closeDetail:'Close details',
    fullWidth:'Full-width reading',resizeReader:'Resize reader',resizeReaderHint:'Drag or use the arrow keys to resize the reader; double-click to reset',wideTable:'Table wider than the reader · Read full width',readerShare:'Reader at {percent}%',
    previousEntry:'Previous entry',nextEntry:'Next entry',listPosition:'Row {current} of {total}',
    outsideResults:'Not among the loaded results',loadMore:'Load more',loadingMore:'Loading…',retryLoad:'Try again',
    loadedRange:'Showing {shown} of {total} {unit}',allLoaded:'All {total} {unit} shown',noResults:'No results',units:{stacked:'rows',each:'entries'},loading:'Loading entry…',retry:'Try again',readError:'Unable to load entry',
    body:'Text',fields:'Attributes',relations:'Relations',
    detailTabs:'Detail sections',moreFilters:'More filters',allEntries:'All entries',
    librarySettings:'Library settings…',
    emptyLibrary:'Your library settings leave out some rules versions or works, so some entries are not listed.',filterLibraryHint:'Collections, rules versions and works are set under Library in the top bar.',
    libraryState:{narrowed:'narrowed',each:'not deduplicated'},
    collectionErrors:{invalid_collection:'Invalid collection address.',unknown_collection:'Collection not found.',collection_unavailable:'This collection is not connected yet.'},
    moreTypes:'{first} and {count} types',nameField:'Name',bodyField:'Text',and:'and',excludeGroup:'Exclude',allExcept:'All entries except {list}',excludeInstead:'Exclude instead',includeInstead:'Include instead',isNot:'is not',lacks:'lacks',excludeCount:'Excluding it removes {count}',nameContains:'contains “{text}”',presetField:'Preset',clearGroup:'Clear',invertGroup:'Invert',optionCount:'{count} results if chosen',
    elsewhere:'{count} more in other types',elsewhereTitle:'Matches for this search in other types: {list}',dropCondition:'Without {condition}',
    problems:'Query notes',unknownField:'anna does not know the condition “{condition}”; it does not affect the results.',lacksSome:'{types} have no {fields}, so they do not appear in the results.',lacksAll:'{types} have no {fields}, so nothing meets {conditions}.',
    crInverted:'Minimum CR {min} is above maximum CR {max}; both cannot hold.',crUnreadable:'CR “{value}” is not a number or fraction.',queryRejected:'This query cannot run: {message}',queryHeld:'Conditions need a fix',
    fixRemove:'Remove “{condition}”',fixKeepTypes:'Keep only {types}',fixSwapCr:'Use CR {min}–{max}',fixClearAll:'Clear conditions',listJoin:', ',
    marks:{verbal:['V','Verbal'],somatic:['S','Somatic'],material:['M','Material'],ritual:['R','Ritual'],concentration:['C','Concentration']},
    markStates:{absent:'No {name}',unknown:'{name} not extracted',conflict:'{name} conflicts',special:'{name} (special material)'},
    stackVersions:'Stack same-name versions',compactRows:'Compact rows',sortMatch:'Best match',preferRuleset:'Show first',sortLabel:'Sort',displayOptions:'Display',
    stackedCount:'{rows} rows · {versions} versions',shownVersion:'This version is shown in the row',
    stackStatus:{same_name_candidate:'{count} same-name candidates, each kept separate',exact_text_candidate:'{count} same-text candidates, each kept separate',reprint_group:'{count} versions/reprints, each kept separate',library_candidate:'{count} same-name candidates across collections, each kept separate'},
    fieldNotes:'Field notes',noteElsewhere:'evidence outside this passage',
    layoutUnavailable:{missing:'The source page is missing; showing extracted text.',stale:'The source changed since this database snapshot; showing extracted text.',empty:'The source layout is empty; showing extracted text.',error:'Unable to load the source layout; showing extracted text.'},
    versionsTab:'Versions',versionsIntro:'Same-name candidates ordered by rules version; a candidate relation is not a shared rule identity.',versionsIntroLibrary:'Same-name candidates from the collections in your library; a candidate relation is not a shared rule identity.',differingFields:'Fields that differ',sameFields:'{count} fields equal or not extracted',currentVersion:'current',
    relationNames:{translation:'Same work',derived:'Derived',candidate:'Same name'},
    relationHints:{translation:'The same work’s text in another collection',derived:'Copied from a same-name entry or its work, then changed',candidate:'Only the type and English name match; nothing shows it is the same object'},
    diffAgainst:'Mark differences from {name}',diffShown:'Marking differences from {name}',diffLegend:'Highlighted: only in this version　Struck through: only in {name}',diffNone:'The text matches {name}.',diffTooMany:'The text differs from {name} too much to mark word by word; compare the fields under Versions instead.',diffUnavailable:'Unable to load the layout of {name}, so differences cannot be marked.',
    rulesets:{'5e':['2014','2014 rules (5e)'],'5r':['2024','2024 rules (5r)'],'待核':['Unverified','Rules version unverified']},
    provenance:'Provenance',provCollection:'Collection',provOrigin:'Origin',provPackage:'Package',provOurs:'Site curated',provSource:'Source',provAuthors:'Authors',provTranslators:'Translators',provTier:'Tier',provRating:'Rating',provCopiedFrom:'Copied from',provLicense:'License & notes',provMedia:'Media',provMediaLocal:'{count} bundled offline',provMediaRemote:'{count} remote unbundled',provMediaNone:'No external media',provReadBook:'Read in book',provViewLink:'View source note ↗',
    flags:{true:'Yes',false:'No'},legend:'✓ yes　· no　— not extracted　⚠ conflicting field　·　Select a row for its text and evidence'}
};
const locale = document.documentElement.lang.startsWith('en') ? 'en' : 'zh';
const ui = annaUiLabels[locale];
// A ruleset is a rules generation, not a publication year: short labels name it, the full label says so.
const rulesetShort=r=>ui.rulesets[r]?.[0]||r, rulesetFull=r=>ui.rulesets[r]?.[1]||r;
for(const el of document.querySelectorAll('[data-ui]'))el.textContent=ui[el.dataset.ui];
for(const el of document.querySelectorAll('[data-ui-label]')){el.setAttribute('aria-label',ui[el.dataset.uiLabel]);el.title=ui[el.dataset.uiLabel];}
$('#q').dataset.placeholder=$('#q').placeholder;$('#query-problems').setAttribute('aria-label',ui.problems);
const fmt = n => Number(n).toLocaleString('zh-CN');
const pretty = v => typeof v==='boolean'?ui.flags[v]:typeof v==='object'?JSON.stringify(v,null,2):String(v??'');
const legacy = {entry_marked:'条目标旧',source_archived:'整书归档',unmarked:'未见标记'};
const fieldNames = {name:'名称',name_en:'英文名',source:'文献顺序',sources:'来源',package:'资料包',package_kind:'资料包分类',tier:'推荐分级',copy_status:'继承状态',type:'类型',source_id:'来源',ruleset:'规则版本',legacy:'旧版标记',classification_status:'分类依据',casting_time:'施法时间',casting_time_detail:'施法时间原文',range:'射程',components:'成分',duration:'持续时间',prerequisite:'前置条件',ability_scores:'属性值',skills:'技能',tools:'工具',granted_feat:'授予专长',starting_equipment:'起始装备',creature_type:'生物类型',size:'体型',speed:'速度',languages:'语言',senses:'感官',resistances:'伤害抗性',immunities:'伤害免疫',condition_immunities:'状态免疫',ac:'AC',hp:'HP',source_code:'来源缩写',spell_classes:'职业',level:'等级',ritual:'仪式',attunement_requirements:'同调条件',role:'使用角色',item_subtypes:'子类别',legendary_actions:'传奇动作',lair_actions:'巢穴动作',has_local_audio:'离线读音',owner_class:'所属职业',feature_kind:'能力类别',book_kind:'书籍类别',price:'价格',carrying_capacity:'载重',statblock_name:'引用数据卡',armor_class:'护甲等级',table_group:'表内分组',facility_kind:'设施类别',quantity:'份量',container:'容器',properties:'词条',strength_requirement:'力量要求',stealth:'隐匿',crew:'船员',passengers:'乘客',cargo_tons:'货物（吨）',damage_threshold:'伤害阈值',weight:'重量',damage:'伤害',mastery:'精通',origin:'来源性质',status:'分类依据',conflict:'字段冲突',sections:'未分类段落',cr_min:'最低 CR',cr_max:'最高 CR',ability:'属性',strength:'力量',dexterity:'敏捷',constitution:'体质',intelligence:'智力',wisdom:'感知',charisma:'魅力'};
const relationNames = {uses_statblock:'使用数据卡',catalog_offer:'购置条目',lists_mount:'列为坐骑',part_of:'属于',section_of:'所属段落',grants:'授予',grants_feat:'授予专长',references:'引用',requires:'要求',belongs_to:'属于',belongs_to_class:'所属职业',casts:'施展',reprinted_as:'重印版本',copied_from:'继承自',variant_of:'变体源自',has_legendary_group:'传奇/巢穴组'};
const LEVELS=['戏法','一环','二环','三环','四环','五环','六环','七环','八环','九环'];
const NONE='__none__';
const BOOL_FIELDS=new Set(['concentration','ritual','verbal','somatic','material','special_material','attunement','legendary_actions','lair_actions','has_local_audio']);
const COMMON=new Set(['ruleset','source','origin','package','status','legacy','conflict','sections']);
let metadata, schema, groups=[], params=new URLSearchParams(location.search), request, generation=0, panelGeneration=0, timer;
let currentData, currentEntry, detailGeneration=0, ready=false, facetData=null, facetGeneration=0, panelShape='', focusSections=false;
const badge=(text,tone='gray')=>`<span class="badge ${tone}">${esc(text)}</span>`;
const sourceURL=(path,start=0,end=0)=>'/source?'+new URLSearchParams({path,start,end})+'#evidence';
const sourceOf=id=>metadata?.sources?.find(s=>s.id===id);
const sourceName=id=>sourceOf(id)?.name||id, sourceShort=id=>sourceOf(id)?.short_name||id;
const packageOf=id=>metadata?.packages?.find(p=>p.id===id);
const packageName=id=>packageOf(id)?.name||id;
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
// Every request carries the reader's library (annaLibrary).
// A failed request keeps its HTTP status: 400 means the server cannot run the query as written.
async function api(path,signal){const r=await fetch(annaLibrary.withScope(path),{signal});const d=await r.json();
  if(!r.ok)throw Object.assign(new Error(d.code==='bundle_changed'?ui.bundleChanged:d.code==='reloading'?ui.reloading:d.code==='reload_failed'?ui.reloadFailed:ui.collectionErrors[d.code]||d.error||'请求失败'),{status:r.status,code:d.code||null});return d;}
const collectionName=id=>metadata?.collections?.find(c=>c.id===id)?.label?.[locale]||annaLibrary.nameOf(id);
function showLibrary(){
  // The note qualifies anna's own extraction from the book; it shows while the book is among the collections read.
  $('.heading-note').hidden=!(metadata.library?.libraries||[metadata.collection]).includes(metadata.default_collection);
  const library=metadata.library||{},state=[library.narrowed?ui.libraryState.narrowed:'',library.versions==='each'?ui.libraryState.each:''].filter(Boolean).join(' · ');
  $('#library-state').textContent=state;
  $('#display-toggle').classList.toggle('changed',!!state);
}
// A changed library changes the table's universe: its types, works and counts. The query keeps its conditions
// (those another collection cannot answer hold for none of its entries) and the reader keeps its entry.
let libraryTimer;
async function reloadLibrary(){
  clearTimeout(libraryTimer);
  // The rows on screen belong to the previous library; they dim at once, as for any new table.
  request?.abort();generation++;ready=false;awaitResults('正在读取筛选…');
  try{
    const meta=await api('/api/meta');
    metadata=meta;Object.assign(fieldNames,metadata.fields);showLibrary();panelShape='';
    // The open entry stays open: it still exists, whichever versions now lead the rows.
    historyUpdate(true);await changeTable();
  }catch(e){failResults(e.message,'读取失败');}
}
annaLibrary.subscribe(()=>{if(!metadata)return;clearTimeout(libraryTimer);libraryTimer=setTimeout(reloadLibrary,250);});
for(const b of document.querySelectorAll('#open-library,[data-open-library]'))b.addEventListener('click',()=>{setDisplayMenu(false);annaLibrary.open();});
let restoringUi=false, uiSaveTimer, lastResultEntry=null;
history.scrollRestoration='manual';
function captureUiState(){
  return {tableTop:$('.table-wrap').scrollTop,tableLeft:$('.table-wrap').scrollLeft,
    mainTop:$('#query-main').scrollTop,windowTop:window.scrollY,filterTop:$('#filter-panel').scrollTop,
    filtersOpen:!$('#filter-panel').hidden,extraOpen:$('#domain-extra')?.open,readerId:currentReaderId,readerTab:detailTab,readerFull,
    readerTop:$('#detail-content').scrollTop,lastResultEntry,loadedRows:currentData?.rows.length||0};
}
function saveUiState(){if(restoringUi)return;clearTimeout(uiSaveTimer);uiSaveTimer=0;history.replaceState({...history.state,anna:captureUiState()},'');rememberQuery();}
// Anna shares her places with Stella when the server reads books; each place is bound to its collection's build.
const sharing=()=>!!metadata?.reader_url;
const snapshots=()=>Object.fromEntries((metadata?.collections||[]).filter(c=>c.snapshot).map(c=>[c.id,c.snapshot]));
// An entry belongs to its own collection: Stella and the shared workspace name it `collection:id`.
const entryCollection=e=>e?.collection||metadata.collection;
const qualifiedEntry=e=>e?entryCollection(e)+':'+e.id:'';
// A remembered query is its committed conditions: the open entry and unfinished typing are not part of it.
function queryKey(){const query=new URLSearchParams(params);query.delete('entry');query.delete('typing');query.delete('tab');return query.toString();}
// Names a remembered query by its heading and search words, as a reader would describe it.
function queryTitle(){const terms=searchTerms(),title=typeTitle();return !terms.length?title:title===ui.allEntries?terms.join(' '):`${title} · ${terms.join(' ')}`;}
// This tab's query is Anna's latest place in the shared workspace, so Stella's switcher can lead back to it.
function rememberQuery(){
  if(!sharing())return;
  const entry=currentEntry&&currentEntry.id===currentReaderId?qualifiedEntry(currentEntry):'';
  annaWorkspace.update(snapshots(),state=>annaWorkspace.rememberQuery(state,{query:queryKey(),entry,snapshot:entry?snapshots()[entryCollection(currentEntry)]:'',
    entryName:entry?(currentEntry.name_zh||currentEntry.name):'',title:queryTitle(),ui:captureUiState(),at:Date.now()}));
}
// A remembered query chosen from Stella's switcher returns as this tab last showed it; another entry opens fresh.
function resumedUi(id){
  if(id===null||!sharing())return null;
  const memory=annaWorkspace.read(snapshots()).query.find(m=>m.query===id);
  if(!memory?.ui||memory.query!==queryKey())return null;
  return annaWorkspace.localOf(memory.entry)===(params.get('entry')||'')?memory.ui:{...memory.ui,readerId:null,readerTab:'body',readerTop:0,readerFull:false};
}
// The open entry is the topic Stella can turn to; closing it ends the topic. Restoring a remembered place or a
// history entry shows its entry again without bringing it up as a new topic.
let quietTopic=false;
function shareTopic(){
  if(!sharing()||!currentEntry||quietTopic)return;
  const collection=entryCollection(currentEntry),book=metadata.collections?.find(c=>c.id===collection);
  if(!book?.reader)return;
  // Another collection's entry is a topic for Stella only where it has a place in that collection's book.
  const loc=currentEntry.reader_location||null,page=loc&&(loc.page||loc.path);
  if(collection!==metadata.default_collection&&!page)return;
  const q=local=>local?collection+':'+local:'';
  const bookLocation=page?{root:q(loc.root),page:q(page),node:q(loc.node_id||loc.node),fragment:loc.fragment||''}:null;
  annaWorkspace.update(snapshots(),state=>annaWorkspace.setTopic(state,{entry:qualifiedEntry(currentEntry),name:currentEntry.name_zh||currentEntry.name,
    from:'query',snapshot:book.snapshot,bookLocation,at:Date.now()}));
}
function endTopic(entry){
  if(sharing()&&entry)annaWorkspace.update(snapshots(),state=>annaWorkspace.clearTopic(state,qualifiedEntry(entry)));
}
function historyUpdate(replace=false){
  // The address lists the conditions in the bar's order, so a reload or Back shows them where they were.
  placeCards();params=orderedParams();
  if(!replace)saveUiState();
  const search=params.toString();history[replace?'replaceState':'pushState'](history.state,'',location.pathname+(search?'?'+search:''));
}
function restoreUiState(state){
  if(!state)return;
  // The panel starts collapsed; only an explicit opening in this tab's history reopens it.
  $('#filter-panel').hidden=!state.filtersOpen;
  $('#toggle-filters').setAttribute('aria-expanded',!!state.filtersOpen);
  if($('#domain-extra')&&typeof state.extraOpen==='boolean')$('#domain-extra').open=state.extraOpen;
  // Full-width reading changes the text's line breaks, so it returns before the reader's scroll position.
  if(currentReaderId&&state.readerId===currentReaderId)setReaderFull(!!state.readerFull,{save:false});
  $('#filter-panel').scrollTop=state.filterTop||0;
  $('.table-wrap').scrollTop=state.tableTop||0;$('.table-wrap').scrollLeft=state.tableLeft||0;
  $('#query-main').scrollTop=state.mainTop||0;window.scrollTo(0,state.windowTop||0);
  $('#detail-content').scrollTop=state.readerTop||0;lastResultEntry=state.lastResultEntry||null;
}
// Scroll events arrive continuously; one state write after the reader settles keeps the address bar quiet.
const UI_SAVE_DELAY=200;
function queueUiSave(){clearTimeout(uiSaveTimer);uiSaveTimer=setTimeout(saveUiState,UI_SAVE_DELAY);}
for(const el of [window,$('.table-wrap'),$('#query-main'),$('#filter-panel'),$('#detail-content')])el.addEventListener('scroll',queueUiSave,{passive:true});
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
// chosen in one field are alternatives. A field's values can instead be excluded (`not_<key>`): the field then
// holds for entries with none of them. The panel edits whichever list the field uses; a field holding only
// excluded values is in exclude mode. A condition stays as chosen, every value included, until the reader removes it.
const NOT='not_';
const baseKey=key=>key.replace(/^not_/,'');
const excluding=g=>params.has(NOT+g.key)&&!params.has(g.key);
const modeKey=g=>excluding(g)?NOT+g.key:g.key;
function selected(g){return new Set(params.getAll(modeKey(g)).filter(v=>v!==NONE));}
function setSelection(g,values,key=modeKey(g)){
  params.delete(key);values.forEach(v=>params.append(key,v));
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
  clearTimeout(timer);params.delete('entry');historyUpdate(replace);closeEntry({update:false,restoreFocus:false});
  // A table the server could not read is read again once the reader edits the query.
  if(tableRejected){changeTable();return;}
  sync();$('.table-wrap').scrollTop=0;load();refreshFacets();
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
// The condition bar reads like a line of text. Each card is one condition and keeps the place it was given; the
// text field is the caret and can sit between any two cards. A field's card is named by its URL key (`type`,
// `not_f_school`, `cr_min`), a kept search by its list and words (`q:火`, `not_body:专注`), since removing one
// search renumbers the rest. Including or excluding the same field or words is the same card turned over.
const TERM_LISTS=['q','not_q','body','not_body'];
const fieldKey=k=>k.startsWith('f_')||COMMON.has(k)||['type','cr_min','cr_max'].includes(k);
const isFieldCard=k=>fieldKey(k)||k.startsWith(NOT)&&fieldKey(baseKey(k))&&!['cr_min','cr_max'].includes(baseKey(k));
const isCardParam=k=>isFieldCard(k)||TERM_LISTS.includes(k);
function cardParts(id){const i=id.indexOf(':');return i<0?{key:id}:{key:id.slice(0,i),term:id.slice(i+1)};}
// `caret` counts the cards before the field. `cardsAt` says where the next new cards go: 'caret' when the
// reader adds them from the bar, otherwise after the others.
let cardOrder=[],caret=0,cardsAt=null;
// The cards a query holds in its address's order, as a link or a history entry lists them.
function naturalCards(){
  const out=new Set();
  for(const [k,v] of params){if(isFieldCard(k))out.add(k);else if(TERM_LISTS.includes(k)&&v.trim())out.add(k+':'+v.trim());}
  return [...out];
}
function resetCards(){cardOrder=naturalCards();caret=cardOrder.length;cardsAt=null;}
// Cards that remain keep their order and the caret keeps its place among them. A card turned over takes the
// place of the one it replaces; a new one lands at the caret, or after the others. Nothing is resorted or merged.
const CARET=Symbol('caret');
function placeCards(){
  const present=naturalCards(),held=new Set(present),known=new Set(cardOrder);
  const line=[...cardOrder.slice(0,caret),CARET,...cardOrder.slice(caret)],fresh=[];
  for(const id of present){
    if(known.has(id))continue;
    const i=line.findIndex(x=>x!==CARET&&!held.has(x)&&baseKey(x)===baseKey(id));
    if(i>=0)line[i]=id;else fresh.push(id);
  }
  const kept=line.filter(x=>x===CARET||held.has(x));
  kept.splice(cardsAt==='caret'||kept.at(-1)===CARET?kept.indexOf(CARET):kept.length,0,...fresh);
  cardsAt=null;caret=kept.indexOf(CARET);cardOrder=kept.filter(x=>x!==CARET);
  committed=cardOrder.filter(id=>id.startsWith('q:')).map(id=>id.slice(2));
}
function orderedParams(){
  const out=new URLSearchParams();
  for(const id of cardOrder){const {key,term}=cardParts(id);if(term!==undefined)out.append(key,term);else for(const v of params.getAll(key))out.append(key,v);}
  for(const [k,v] of params)if(!isCardParam(k))out.append(k,v);
  return out;
}
// Counts and the server name a kept search by its list and position in the bar (`q0`, `not_body1`).
function serverKey(id){const {key,term}=cardParts(id);return term===undefined?key:key+cardOrder.filter(c=>cardParts(c).key===key).indexOf(id);}
function cardOf(key){const m=/^((?:not_)?(?:q|body))(\d+)$/.exec(key);return m?cardOrder.filter(c=>cardParts(c).key===m[1])[Number(m[2])]||null:key;}
const termsOf=list=>list==='q'?committed:termList(list);
const AND=extra=>`<span class="and${extra}" role="img" aria-label="${esc(ui.and)}">∧</span>`;
function cardHTML(id,note){
  const {key}=cardParts(id),server=serverKey(id),text=conditionText(id),negated=key.startsWith(NOT);
  const flip=negated?ui.includeInstead:ui.excludeInstead,flippable=!['cr_min','cr_max','conflict','sections'].includes(key);
  const described=note?` aria-describedby="${note.id}"`:'';
  return `<span class="condition${negated?' negated':''}"${note?` data-tone="${note.tone}"`:''} data-card="${esc(id)}">`+
    (flippable?`<button type="button" class="condition-text" data-flip="${esc(server)}" aria-label="${esc(text)}，${esc(flip)}" title="${esc(flip)}"${described}>${esc(text)}</button>`:`<span class="condition-text"${described}>${esc(text)}</span>`)+
    `<button type="button" class="condition-remove" data-remove="${esc(server)}" aria-label="移除 ${esc(text)}" title="移除 ${esc(text)}">×</button></span>`;
}
// A card and its ∧ stay the same while they name the same condition, whether it includes or excludes. The ∧
// beside the caret belongs to no card: it may be hidden, and a hidden box has no place to slide from or to.
function conditionKey(el){
  if(el.matches('.and'))return !el.matches('.slot-before,.slot-after')&&el.nextElementSibling?.dataset.card?baseKey(el.nextElementSibling.dataset.card)+' ∧':null;
  return baseKey(el.dataset.card);
}
// The cards are placed around the field, never the field among them: moving a focused field would blur it.
// Beside the caret, a ∧ shows only while words are typed there, so the line always reads card ∧ card.
let cardsShape='';
function renderCards({animate=false}={}){
  const strip=$('#active-filters'),input=$('#q'),notes=problemNotes();
  const join=ids=>ids.map(id=>cardHTML(id,notes.get(id))).join(AND(''));
  const before=cardOrder.slice(0,caret),after=cardOrder.slice(caret);
  const html=[join(before)+(before.length?AND(' slot-before'+(after.length?' joins':'')):''),(after.length?AND(' slot-after'):'')+join(after)];
  const shape=html.join('\n');
  if(shape!==cardsShape){
    cardsShape=shape;
    const active=strip.contains(document.activeElement)&&document.activeElement.closest('.condition');
    const keep=active&&{base:baseKey(active.dataset.card),remove:document.activeElement.matches('.condition-remove')};
    // The field takes its new width inside the change, so cards are measured where they finally stand.
    const place=()=>{for(const el of [...strip.children])if(el!==input)el.remove();input.insertAdjacentHTML('beforebegin',html[0]);input.insertAdjacentHTML('afterend',html[1]);fitCaret();};
    animate?annaMotion.rearrange(strip,'.condition,.and',conditionKey,place):place();
    if(keep)[...strip.querySelectorAll('.condition')].find(c=>baseKey(c.dataset.card)===keep.base)
      ?.querySelector(keep.remove?'.condition-remove':'button.condition-text')?.focus({preventScroll:true});
  }else fitCaret();
}
// At the end the field takes the rest of the line and shows its hint; between cards it is as wide as its words.
let measure=null;
function fitCaret(){
  const strip=$('#active-filters'),input=$('#q'),inside=caret<cardOrder.length;
  strip.classList.toggle('drafting',!!input.value);strip.classList.toggle('caret-inside',inside);
  input.placeholder=inside?'':input.dataset.placeholder;
  if(inside){
    measure??=document.createElement('canvas').getContext('2d');
    const s=getComputedStyle(input);measure.font=`${s.fontStyle} ${s.fontWeight} ${s.fontSize} ${s.fontFamily}`;
    input.style.width=Math.ceil(measure.measureText(input.value).width)+4+'px';
  }else input.style.width='';
  if(document.activeElement===input){
    const s=strip.getBoundingClientRect(),r=input.getBoundingClientRect();
    if(r.left<s.left)strip.scrollLeft-=s.left-r.left+16;else if(r.right>s.right)strip.scrollLeft+=r.right-s.right+16;
  }
  markStripEdges(strip);
}
// Moving the caret off words being typed leaves them where they were typed, kept as a search.
function moveCaret(to){
  if(to===caret)return;
  const from=caret;
  if($('#q').value.trim()){keepDraft();if(to>from)to+=caret-from;}
  caret=Math.max(0,Math.min(cardOrder.length,to));renderCards();
}
// The gap a point falls in: after every card that ends above it or whose middle lies before it on its line.
function gapAt(x,y){
  let gap=0;
  for(const card of $('#active-filters').querySelectorAll('.condition')){const r=card.getBoundingClientRect();if(r.bottom<=y||r.top<=y&&(r.left+r.right)/2<x)gap++;else break;}
  return gap;
}
// A query stays as the reader wrote it. Where conditions cannot all hold, a chosen type cannot answer one, or
// the server cannot run the query, a note below the bar says so and offers the edits that would resolve it.
let rejection=null,shownProblems=[],problemsShape='';
const rejectionKey=()=>{const query=new URLSearchParams(params);query.delete('entry');query.delete('tab');return query.toString();};
const crValue=s=>!s?null:s.includes('/')?s.split('/').map(Number).reduce((a,b)=>a/b):Number(s);
function queryProblems(){
  const out=[],list=xs=>xs.join(ui.listJoin),types=params.getAll('type').filter(t=>t!==NONE);
  const rejected=rejection&&rejection.query===rejectionKey()?{tone:'error',cards:[],text:ui.queryRejected.replace('{message}',rejection.message),fixes:[{label:ui.fixClearAll,run:clearAll}]}:null;
  if(rejected)out.push(rejected);
  if(metadata){
    // Conditions that leave out the same chosen types share one note, which offers each removal and one narrowing.
    const answered=Object.values(metadata.type_conditions||{}).flat(),seen=new Set(),lacks=new Map(),quoted=xs=>list(xs.map(x=>`“${x}”`));
    for(const id of cardOrder){
      const condition=id.startsWith('f_')?id.slice(2):id==='cr_min'||id==='cr_max'?'cr':null;
      if(!condition||seen.has(condition))continue;seen.add(condition);
      const cards=condition==='cr'?cardOrder.filter(c=>c==='cr_min'||c==='cr_max'):[id],key=condition==='cr'?'cr':id,text=conditionText(key);
      const remove={label:ui.fixRemove.replace('{condition}',text),run:()=>dropCondition(key)};
      if(!answered.includes(condition)){out.push({tone:'warning',cards,text:ui.unknownField.replace('{condition}',text),fixes:[remove]});continue;}
      const lacking=types.filter(t=>!(metadata.type_conditions[t]||[]).includes(condition));if(!lacking.length)continue;
      const group=lacks.get(lacking.join())||{lacking,answering:types.filter(t=>!lacking.includes(t)),cards:[],fields:[],texts:[],fixes:[]};
      group.cards.push(...cards);group.fields.push(condition==='cr'?'CR':fieldLabel(id));group.texts.push(text);group.fixes.push(remove);lacks.set(lacking.join(),group);
    }
    for(const {lacking,answering,cards,fields,texts,fixes} of lacks.values()){
      const narrow=answering.length?{label:ui.fixKeepTypes.replace('{types}',list(answering.map(typeName))),run:()=>chooseTypes(answering)}
        :{label:ui.fixRemove.replace('{condition}',conditionText('type')),run:()=>dropCard('type')};
      out.push({tone:answering.length?'warning':'error',cards,fixes:[...fixes,narrow],
        text:(answering.length?ui.lacksSome:ui.lacksAll).replace('{types}',list(lacking.map(typeName))).replace('{fields}',quoted(fields)).replace('{conditions}',quoted(texts))});
    }
  }
  const min=params.get('cr_min'),max=params.get('cr_max');
  // A bound that is not a number explains why the server cannot run the query; its note replaces the server's.
  for(const key of ['cr_min','cr_max']){const value=params.get(key);if(value&&!Number.isFinite(crValue(value)))
    out.push({tone:'error',cards:[key],explains:true,text:ui.crUnreadable.replace('{value}',value),fixes:[{label:ui.fixRemove.replace('{condition}',conditionText(key)),run:()=>dropCard(key)}]});}
  if(out.some(p=>p.explains))return out.filter(p=>p!==rejected);
  if(crValue(min)>crValue(max)&&max)out.push({tone:'error',cards:['cr_min','cr_max'],text:ui.crInverted.replace('{min}',min).replace('{max}',max),
    fixes:[{label:ui.fixSwapCr.replace('{min}',max).replace('{max}',min),run:()=>{params.set('cr_min',max);params.set('cr_max',min);changed();}}]});
  return out;
}
function renderProblems(){
  shownProblems=queryProblems();
  const shape=shownProblems.map((p,i)=>`<div class="query-problem" data-tone="${p.tone}" id="query-problem-${i}"><span class="problem-mark" aria-hidden="true">!</span>`+
    `<p>${esc(p.text)}</p><div class="problem-fixes">${p.fixes.map((f,j)=>`<button type="button" data-fix="${i}:${j}">${esc(f.label)}</button>`).join('')}</div></div>`).join('');
  if(shape===problemsShape)return;problemsShape=shape;
  const box=$('#query-problems');box.hidden=!shownProblems.length;box.innerHTML=shape;
}
function problemNotes(){
  const notes=new Map();
  shownProblems.forEach((p,i)=>p.cards.forEach(id=>{if(!notes.has(id))notes.set(id,{id:'query-problem-'+i,tone:p.tone});}));
  return notes;
}
// Cards and notes render even before a table is known, so a query the server rejects can still be edited.
function renderQuery(){
  placeCards();renderProblems();renderCards({animate:true});
  $('#filter-count').textContent=cardOrder.length||'';
  if(metadata){$('#title').textContent=typeTitle();updatePageChrome();}
}
function sync(){
  renderQuery();
  if(!schema)return;
  renderSortOptions();
  const desc=params.get('dir')==='desc';$('#direction').textContent=desc?'降序 ↓':'升序 ↑';
  $('#direction').setAttribute('aria-label',desc?'切换为升序':'切换为降序');
  for(const key of ['cr_min','cr_max'])if($('#'+key))$('#'+key).value=params.get(key)||'';
  for(const g of groups){
    const el=document.querySelector(`[data-group="${g.key}"]`);if(!el)continue;
    const values=selected(g);el.classList.toggle('unconstrained',!values.size);el.classList.toggle('excluding',excluding(g));
    for(const input of el.querySelectorAll('input[type=checkbox]'))input.checked=values.has(input.value);
    for(const b of el.querySelectorAll('[data-action]'))b.disabled=!values.size;
    el.querySelector('[data-action=exclude]')?.setAttribute('aria-pressed',String(excluding(g)));
    for(const b of el.querySelectorAll('[data-flag]'))b.setAttribute('aria-pressed',(params.get(g.key)||'')===b.dataset.flag);
  }
  const extraCount=groups.filter(g=>(params.has(g.key)||params.has(NOT+g.key))&&$('#domain-extra')?.querySelector(`[data-group="${g.key}"]`)).length;
  if($('#extra-count'))$('#extra-count').textContent=extraCount?String(extraCount):'';
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
// Server groups carry counts; the interface supplies its own words for rules versions.
function readGroups(data){
  return data.groups.map(g=>({...g,options:g.options.map(o=>g.key==='ruleset'?{...o,label:rulesetShort(o.value),title:rulesetFull(o.value)}:o)}));
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
const facetsURL=()=>'/api/facets?'+new URLSearchParams([...params].filter(([k])=>!['entry','sort','dir'].includes(k)));
// Counts follow every condition, so they refresh beside each query; a late response never replaces a newer one.
async function refreshFacets(){
  const gen=++facetGeneration;
  try{const data=await api(facetsURL());if(gen===facetGeneration)applyFacets(data);}catch{/* The results report query errors; counts keep their last values. */}
}
// New types change the table itself: its columns, fields and sorts arrive before the rows are read.
// Conditions the new table cannot answer stay on the bar, with a note saying which types they leave out.
let tableRejected=false;
async function changeTable(){
  const gen=++panelGeneration,fgen=++facetGeneration;request?.abort();generation++;ready=false;tableRejected=false;
  awaitResults('正在读取筛选…');$('.table-wrap').scrollTop=0;$('.table-wrap').scrollLeft=0;renderQuery();
  try{
    const data=await api(facetsURL());if(gen!==panelGeneration||fgen!==facetGeneration)return;
    applyFacets(data);
    if(params.has('sort')&&![...schema.sorts,'match'].includes(params.get('sort')))params.delete('sort');
    historyUpdate(true);sync();ready=true;await load();
  }catch(e){
    if(gen!==panelGeneration)return;
    if(e.status===400){tableRejected=true;rejectQuery(e.message);$('#columns').replaceChildren();}
    else{failResults(e.message,'读取失败');$('#columns').replaceChildren();}
    $('#results').setAttribute('aria-busy','false');
  }
}
// A query the server cannot run shows why below the bar; the results wait, empty, for the fix.
function rejectQuery(message){
  rejection={query:rejectionKey(),message};
  currentData=null;$('#error').hidden=true;$('#result-count').textContent=ui.queryHeld;$('#result-count').title='';
  $('#results').replaceChildren();$('#empty').hidden=true;$('#range').textContent='';$('#export').disabled=true;renderLoadState();renderQuery();
}
function renderColumns(){
  $('#columns').innerHTML='<tr>'+schema.columns.map(k=>{
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
  const list=row.quickref?.[key];
  if(!list||!list.length)return null;
  const token=list.length===1?list[0]:'__conflict__';
  if(token!=='true'&&token!=='false')return null;
  return `<span class="flag ${token==='true'?'yes':'no'}"><span aria-hidden="true">${token==='true'?'✓':'·'}</span><span class="sr-only">${esc(ui.flags[token])}</span></span>`;
}
const versionSource=v=>(v.source_ids||[]).map(sourceShort).join(' / ');
// A version from another collection than the page's first names its collection, so 不全书's PHB24 and
// our5etools' XPHB read as two collections' versions of one name.
const foreignVersion=v=>v.collection&&v.collection!==metadata.collection;
const versionLabel=v=>`${foreignVersion(v)?collectionName(v.collection)+' · ':''}${versionSource(v)} ${rulesetShort(v.ruleset)}`;
// How a version relates to the one shown: the same work elsewhere, a copy, or only the name. A version of the
// collection's own group, or linked by a chain of evidence, carries no mark.
const relationMark=v=>ui.relationNames[v.relation]?`<span class="version-relation" data-relation="${esc(v.relation)}">${esc(ui.relationNames[v.relation])}</span>`:'';
const versionTitle=v=>`${v.collection?collectionName(v.collection)+' · ':''}${(v.source_names||v.source_ids||[]).join('；')} · ${rulesetFull(v.ruleset)}${ui.relationHints[v.relation]?' · '+ui.relationHints[v.relation]:''}`;
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
    `<button type="button" class="version-pill ${i===0?'shown':''} ${v.ruleset==='待核'?'unverified':''}" data-entry="${esc(v.id)}" title="${esc(versionTitle(v)+(i===0?' · '+ui.shownVersion:''))}">${esc(versionLabel(v))}${relationMark(v)}</button>`).join('')}</div>`;
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
  const text=fieldText(row,key,true),flag=flagHTML(row,key);
  const conflict=row.field_conflicts.includes(key),full=fieldText(row,key);
  return `<td ${label} class="${['prerequisite','attunement_requirements','item_subtypes'].includes(key)?'wide-cell':''} ${text==='—'?'missing':''} ${conflict?'conflicted':''}${differs}" title="${esc((conflict?'字段冲突：':'')+full)}"><span class="cell-value">${flag||esc(text)}${conflict&&!text.includes('⚠')?' ⚠':''}</span>${versionDiffs(row,key)}</td>`;
}
const rowHTML=(row,columns)=>'<tr>'+columns.map(k=>cell(row,k)).join('')+'</tr>';
function renderResults(){
  renderColumns();const columns=schema.columns;
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
    $('#results').insertAdjacentHTML('beforeend',data.rows.map(r=>rowHTML(r,schema.columns)).join(''));
    // Appended rows in sight fade in below the ones already read; those still out of sight just wait there.
    for(const tr of [...$('#results').children].slice(-data.rows.length))annaMotion.arrive(tr);
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
  const typed=typedQuery;typedQuery=false;
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
    // The new rows settle in from the dimmed look the old ones waited in.
    if(!typed)annaMotion.arrive($('#results'),null,{opacity:.45});
    $('#empty').hidden=data.total!==0;$('#export').disabled=!data.total;
    $('#empty-library').hidden=!(data.total===0&&metadata.library?.narrowed);
    if(focusSections){focusSections=false;($('#results .entry-name')||$('.table-wrap')).focus();}
  }catch(e){if(e.name==='AbortError'||gen!==generation)return;if(e.status===400)rejectQuery(e.message);else failResults(e.message,'查询失败');renderColumns();}
  finally{if(gen===generation){$('#results').setAttribute('aria-busy','false');renderLoadState();updateReaderNavigation();saveUiState();}}
}
function sortBy(key){if(effectiveSort()===key)setParam('dir',params.get('dir')==='desc'?'asc':'desc');else{params.set('sort',key);params.delete('dir');}changed();}
$('#filter-panel').addEventListener('change',e=>{
  const el=e.target;
  if(el.type==='checkbox'&&el.closest('[data-group]')){
    const g=groups.find(g=>g.key===el.closest('[data-group]').dataset.group);const set=selected(g);
    el.checked?set.add(el.value):set.delete(el.value);
    if(g.key==='type'&&!excluding(g)){chooseTypes([...set]);return;}
    setSelection(g,[...set]);changed();
  }else if(['cr_min','cr_max'].includes(el.id)){
    // An inverted range stays as chosen; the note below the bar offers to swap its ends.
    setParam(el.id,el.value);changed();
  }
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
function writeList(list,terms){if(list==='q'){committed=terms;writeTerms();}else setList(list,terms);}
// Turning a search card over moves its words to the other list; the card keeps its place in the bar.
function flipTerm(id){
  const {key:list,term}=cardParts(id),to=list.startsWith(NOT)?baseKey(list):NOT+list;
  writeList(list,termsOf(list).filter(t=>t!==term));writeList(to,[...termsOf(to),term]);changed();
}
let committed=[],draft='';
const searching=()=>committed.length>0||!!draft;
function writeTerms(){params.delete('q');[...new Set(committed)].forEach(t=>params.append('q',t));setParam('typing',draft);}
function readTerms(){committed=searchTerms();draft=(params.get('typing')||'').trim();$('#q').value=draft;}
function clearDraft(){draft='';suggestionGroups=null;suggestionGeneration++;$('#q').value='';closeSuggestions();}
let composingSearch=false;
// Results that follow each keystroke replace the rows without fading, so the list does not flicker while typing.
let typedQuery=false;
function queueSearch(){
  clearTimeout(timer);if(composingSearch)return;
  draft=$('#q').value.trim();renderSuggestions();
  timer=setTimeout(()=>{writeTerms();typedQuery=true;changed(true);refreshSuggestionCounts();},160);
}
// Choosing a suggested value drops the draft, so suggestions count with the draft left out.
let suggestionGroups=null,suggestionGeneration=0;
async function refreshSuggestionCounts(){
  const gen=++suggestionGeneration,query=new URLSearchParams([...params].filter(([k])=>!['entry','sort','dir','typing'].includes(k)));
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
// Rules versions and kinds of content belong to the reader's library, not to one query.
const LIBRARY_GROUPS=new Set(['ruleset','origin']);
const fold=t=>String(t||'').normalize('NFKC').toLowerCase();
function suggestionList(text){
  const q=fold(text);if(!q)return [];
  const values=(suggestionGroups&&draft?suggestionGroups:groups).filter(g=>g.mode==='multi'&&!LIBRARY_GROUPS.has(g.key)).flatMap(g=>{const chosen=new Set(params.getAll(g.key));
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
  list.innerHTML=suggestions.map((s,i)=>`<li role="option" id="suggestion-${i}" data-index="${i}" aria-selected="${i===activeSuggestion}"><span class="field">${esc(s.field)}</span><span class="value">${esc(s.label)}</span>${s.kind==='name'?'<kbd>Enter</kbd>':s.count!=null?`<span class="count">${fmt(s.count)}</span>`:''}</li>`).join('');
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
// Keep the draft as a search card at the caret; the field empties for the next search.
function keepDraft(){
  clearTimeout(timer);const text=$('#q').value.trim();clearDraft();if(!text)return;
  cardsAt='caret';committed=[...new Set([...committed,text])];writeTerms();changed();
}
// A suggestion always includes its value; if the value was excluded, it leaves the excluded list.
function includeValue(key,value){
  const excluded=params.getAll(NOT+key).filter(x=>x!==value),included=[...params.getAll(key).filter(x=>x!==NONE&&x!==value),value];
  params.delete(NOT+key);excluded.forEach(x=>params.append(NOT+key,x));params.delete(key);included.forEach(x=>params.append(key,x));
}
// What a suggestion adds lands at the caret. A value joins its field's card wherever that card is.
function applySuggestion(item){
  clearTimeout(timer);
  if(!item||item.kind==='name'){keepDraft();return;}
  const text=$('#q').value.trim();clearDraft();writeTerms();cardsAt='caret';
  if(item.kind==='body'){if(!bodyTerms().includes(text))params.append('body',text);changed();return;}
  // The typed text found the value; it was not meant as a name search. A preset adds its type, then its value.
  if(item.kind==='preset'){includeValue('type',item.preset.type);placeCards();cardsAt='caret';includeValue('f_'+item.preset.field,item.preset.value);chooseTypes(params.getAll('type'));return;}
  includeValue(item.group.key,item.option.value);
  if(item.group.key==='type'){chooseTypes(params.getAll('type'));return;}
  changed();
}
// The field edits the line like text. At the field's start, Backspace removes the card before the caret and ←
// steps over it; at its end, Delete and → do the same for the card after it. Home and End, once the caret
// reaches the end of the words, go to the ends of the line. Shift keeps selecting within the words.
$('#q').addEventListener('keydown',e=>{
  // Safari reports the key that ends a composition after compositionend, with keyCode 229; it is not an Enter.
  if(e.isComposing||composingSearch||e.keyCode===229)return;
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){if(!$('#suggestions').hidden){e.preventDefault();moveSuggestion(e.key==='ArrowDown'?1:-1);}return;}
  if(e.key==='Enter'){e.preventDefault();applySuggestion($('#suggestions').hidden?null:suggestions[activeSuggestion]);return;}
  if(e.key==='Escape'&&!$('#suggestions').hidden){e.preventDefault();e.stopPropagation();closeSuggestions();return;}
  if(e.shiftKey)return;
  const q=$('#q'),collapsed=q.selectionStart===q.selectionEnd,atStart=collapsed&&q.selectionStart===0,atEnd=collapsed&&q.selectionEnd===q.value.length;
  const home=e.key==='Home'||e.metaKey&&e.key==='ArrowLeft',end=e.key==='End'||e.metaKey&&e.key==='ArrowRight';
  let act=null;
  if(e.key==='Backspace'&&atStart&&caret>0)act=()=>{dropCard(cardOrder[caret-1]);};
  else if(e.key==='Delete'&&atEnd&&caret<cardOrder.length)act=()=>{dropCard(cardOrder[caret]);};
  else if(home&&atStart&&caret>0)act=()=>moveCaret(0);
  else if(end&&atEnd&&caret<cardOrder.length)act=()=>moveCaret(cardOrder.length);
  else if(e.key==='ArrowLeft'&&!e.metaKey&&atStart&&caret>0)act=()=>moveCaret(caret-1);
  else if(e.key==='ArrowRight'&&!e.metaKey&&atEnd&&caret<cardOrder.length)act=()=>moveCaret(caret+1);
  if(act){e.preventDefault();act();}
});
// A press between cards, or on the bar around them, puts the caret there.
$('.condition-bar').addEventListener('mousedown',e=>{
  if(e.button||composingSearch||e.target.closest('button,#suggestions')||e.target===$('#q'))return;
  e.preventDefault();if($('#active-filters').contains(e.target))moveCaret(gapAt(e.clientX,e.clientY));$('#q').focus();
});
$('#q').addEventListener('input',fitCaret);
$('#q').addEventListener('focus',fitCaret);
$('#suggestions').addEventListener('pointerdown',e=>{const li=e.target.closest('[data-index]');if(!li)return;e.preventDefault();applySuggestion(suggestions[Number(li.dataset.index)]);});
$('#toggle-filters').onclick=()=>{const hidden=!$('#filter-panel').hidden;$('#filter-panel').hidden=hidden;$('#toggle-filters').setAttribute('aria-expanded',!hidden);saveUiState();};
// Clearing removes every condition, the types included; presentation choices (sort, stacking) stay.
function clearAll(){
  clearDraft();committed=[];rejection=null;
  for(const key of [...params.keys()])if(!['sort','dir'].includes(key))params.delete(key);
  historyUpdate();closeEntry({update:false,restoreFocus:false});changeTable();
}
$('#reset').onclick=clearAll;
// Removing a card removes only its condition; whatever is being typed stays in the field.
function dropCard(id){
  if(!id)return;
  const {key,term}=cardParts(id);
  if(term!==undefined){writeList(key,termsOf(key).filter(t=>t!==term));changed();return;}
  params.delete(key);
  if(key==='type'){chooseTypes([]);return;}
  changed();
}
// Counts name a condition the way the server does: `q0`, `f_school`, or `cr` for both ends of the range.
function dropCondition(key){
  if(key==='cr'){params.delete('cr_min');params.delete('cr_max');changed();return;}
  dropCard(cardOf(key));
}
// × removes its card and leaves the caret where the card was, unless words typed elsewhere hold the caret.
$('#active-filters').addEventListener('click',e=>{
  const remove=e.target.closest('[data-remove]');
  if(remove){const id=cardOf(remove.dataset.remove),at=cardOrder.indexOf(id);if(at>=0&&!$('#q').value.trim())caret=at;dropCard(id);$('#q').focus({preventScroll:true});return;}
  const flip=e.target.closest('[data-flip]');if(!flip)return;
  const id=cardOf(flip.dataset.flip);cardParts(id).term!==undefined?flipTerm(id):flipField(id);
});
$('#query-problems').addEventListener('click',e=>{
  const b=e.target.closest('[data-fix]');if(!b)return;const [i,j]=b.dataset.fix.split(':').map(Number);shownProblems[i]?.fixes[j]?.run();
});
$('#sort').onchange=()=>{params.set('sort',$('#sort').value);changed();};
$('#direction').onclick=()=>{setParam('dir',params.get('dir')==='desc'?'asc':'desc');changed();};
$('#columns').addEventListener('click',e=>{if(awaitingResults())return;const b=e.target.closest('[data-sort]');if(b)sortBy(b.dataset.sort);});
$('#export').onclick=()=>{location.href=annaLibrary.withScope('/api/export?'+params);};
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
  const t=e.target.closest('[data-tab]');if(t)switchTab(t.dataset.tab);
  if(e.target.closest('[data-retry]')&&currentReaderId)openEntry(currentReaderId,false);
  const note=e.target.closest('.field-note[data-note]');
  if(note){lightNotes([note.dataset.note],true);$('#detail-content').querySelector(`mark.note[data-notes~="${CSS.escape(note.dataset.note)}"]`)?.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}
  if(e.target.closest('[data-reader-full]')){setReaderFull(true);$('#reader-full').focus();}
  const difference=e.target.closest('[data-difference]');if(difference)toggleDifferences(difference.dataset.difference);
});
for(const [type,on] of [['mouseover',true],['focusin',true],['mouseout',false],['focusout',false]])
  $('#detail-content').addEventListener(type,e=>{const el=e.target.closest('.field-note[data-note],mark.note');if(!el)return;
    lightNotes(el.dataset.note?[el.dataset.note]:el.dataset.notes.split(' '),on);});
$('#detail-content').addEventListener('keydown',e=>{
  const t=e.target.closest('[data-tab]');if(!t||!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
  e.preventDefault();const tabs=[...$('#detail-content').querySelectorAll('[data-tab]')],index=tabs.indexOf(t);
  const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
  switchTab(tabs[next].dataset.tab);
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
  params=new URLSearchParams(location.search);restoreRows=state?.loadedRows||0;clearDraft();readTerms();resetCards();rejection=null;
  if(!params.has('entry'))closeEntry({update:false,restoreFocus:false});
  if(oldTypes===chosenTypes().join()&&ready){sync();refreshFacets();await load();}else await changeTable();
  if(gen!==navigationGeneration)return;
  quietTopic=true;
  if(params.has('entry'))await openEntry(params.get('entry'),false,state?.readerId===params.get('entry')?state.readerTab:'body');
  quietTopic=false;
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
// The tab title names the open entry, else the chosen slice.
function updatePageChrome(){
  const heading=typeTitle(),subject=currentEntry?(currentEntry.name_zh||currentEntry.name):heading===ui.allEntries?'':heading;
  document.title=annaPageTitle('query',subject);
}
function typeTitle(){
  const types=chosenTypes(),left=params.getAll(NOT+'type');
  if(!types.length)return left.length?ui.allExcept.replace('{list}',left.map(typeName).join('、')):ui.allEntries;
  const preset=activePreset();if(preset)return preset.name;
  return types.length<=2?types.map(typeName).join('、'):ui.moreTypes.replace('{first}',typeName(types[0])).replace('{count}',types.length);
}
// Changing the types changes the table, never the other conditions. One the chosen types cannot all answer
// stays on the bar: adding 法术 to 魔法武器 keeps its item-category condition, and the note below the bar says
// the spells are left out and offers to remove it.
function chooseTypes(types){
  types=[...new Set(types)].sort((a,b)=>typeRank(a)-typeRank(b));
  params.delete('type');types.forEach(t=>params.append('type',t));
  params.delete('entry');historyUpdate();closeEntry({update:false,restoreFocus:false});changeTable();
}
// An empty name search can still have matches among the untyped sections. The server counts them
// under the same conditions, so the offer never leads to a result the filter cannot return.
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
// A field the current table lacks still reads by its own name and values, so a card it left out stays legible.
const fieldLabel=base=>groups.find(g=>g.key===base)?.label||(base==='type'?'类型':fieldNames[base.replace(/^f_/,'')])||base;
function valueLabel(base,v){
  const option=groups.find(g=>g.key===base)?.options.find(o=>o.value===v);if(option)return option.label;
  return base==='package'?packageName(v):base==='source'?sourceName(v):base==='type'?typeName(v):base==='ruleset'?rulesetShort(v):base.startsWith('f_')?tokenText(base.slice(2),v):v;
}
// Reads a card (`q:火`, `f_school`) or a condition as counts name it (`q0`, `cr`, `typing`).
function conditionText(key){
  if(key==='cr')return `CR：${params.get('cr_min')||'0'}–${params.get('cr_max')||'∞'}`;
  if(key==='conflict')return fieldNames.conflict;
  if(key==='typing')return `${ui.nameField}：${draft}`;
  key=cardOf(key);if(!key)return '';
  const {key:list,term}=cardParts(key),negated=list.startsWith(NOT);
  if(term!==undefined)return `${baseKey(list)==='q'?ui.nameField:ui.bodyField}${negated?ui.lacks:''}：${term}`;
  if(key==='cr_min'||key==='cr_max')return `${fieldNames[key]}：${params.get(key)}`;
  const base=baseKey(key),labels=params.getAll(key).map(v=>valueLabel(base,v));
  return labels.length?`${fieldLabel(base)}${negated?ui.isNot:''}：${labels.length>3?labels.slice(0,2).join('、')+` 等 ${labels.length} 项`:labels.join('、')}`:'';
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
// Row density is a per-browser reading preference like the theme; shared links never carry it.
const DENSITY_KEY='anna.density';
const storedDensity=()=>stored.get(DENSITY_KEY)==='compact';
function applyDensity(compact){document.documentElement.dataset.density=compact?'compact':'comfortable';$('#compact-rows').checked=compact;}
$('#compact-rows').onchange=()=>{
  const compact=$('#compact-rows').checked;
  stored.set(DENSITY_KEY,compact?'compact':null);
  annaMotion.restyle(()=>applyDensity(compact));
};
stored.watch(DENSITY_KEY,()=>applyDensity(storedDensity()));
applyDensity(storedDensity());
initReaderLayout();
async function init(){
  let saved=history.state?.anna;restoringUi=true;
  try{
    // Static files can be updated while the old Python process is still serving them.
    // Leave its original unscoped queries usable, but never read it as a requested library.
    // Same-name rows and the preferred rules belong to the reader's library now; old links drop them.
    params.delete('versions');params.delete('prefer');
    // A link no longer narrows the page to one collection: the reader's library decides what Anna reads.
    params.delete('collection');
    const meta=await api('/api/meta');
    metadata=meta;Object.assign(fieldNames,metadata.fields);showLibrary();
    const pageSwitch=document.querySelector('.topbar .page-switch');
    if(pageSwitch){
      if(metadata.tour_url===null){pageSwitch.hidden=true;}
      else{
        pageSwitch.hidden=false;
        const tourHref=annaTopbar?.pages?.tour?.resume?.()||metadata.tour_url;
        if(tourHref&&typeof annaTopbar?.pointSwitch==='function')annaTopbar.pointSwitch('tour',tourHref);
      }
    }
    if(sharing())annaCompanions.mount({page:'query',snapshots:snapshots()});
    // Preserve older bookmarked single-attribute queries.
    // Paging was replaced by batch loading; old links keep their query and drop page and size.
    params.delete('page');params.delete('size');
    // Category views became type filters; an old view link opens on all entries with its other conditions.
    readTerms();
    if(params.has('field')&&params.has('value')){const f=params.get('field');let v=params.get('value');if(f==='spell_level'&&LEVELS.includes(v))v=String(LEVELS.indexOf(v));params.set('f_'+f,v);params.delete('field');params.delete('value');}
    resetCards();
    const resumed=resumedUi(annaWorkspace.takeResume('query'));saved=saved||resumed;restoreRows=saved?.loadedRows||0;quietTopic=!!saved;
    await changeTable();if(params.has('entry'))await openEntry(params.get('entry'),false,saved?.readerId===params.get('entry')?saved.readerTab:'body');
    restoreUiState(saved);
  }catch(e){
    const error=$('#error');error.hidden=false;error.textContent=e.message;
    $('#result-count').textContent='服务未就绪';
  }
  finally{restoringUi=false;quietTopic=false;saveUiState();annaMotion.settle();}
}
init();
