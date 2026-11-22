'use strict';
const $ = s => document.querySelector(s);
const {esc,stored}=annaKit;
// New workbench copy lives in one bilingual resource; data/source labels remain unchanged.
const annaUiLabels = {
  zh: {bundleChanged:'数据已经重建，请重启 make up，再刷新页面。',reloading:'数据正在更新，服务会在完整构建后自动换包；换包完成后刷新页面。',reloadFailed:'数据重建未通过校验，服务未切换到新快照；请查看 make upper 的终端输出并重新构建。',skipSearch:'跳到搜索',emptyTitle:'没有匹配条目',emptyHint:'试试移除已选条件，或清空全部条件。',
    readerTitle:'条目阅读',readerHint:'Esc 关闭 · F6 切换列表与详情',readerHintNarrow:'Esc 关闭 · 上方箭头连续查阅结果',readerHintTouch:'用上方箭头连续查阅结果',closeDetail:'关闭详情',
    fullWidth:'全宽阅读',resizeReader:'调整阅读栏宽度',resizeReaderHint:'拖动或用左右方向键调整阅读栏宽度，双击恢复默认',wideTable:'表格宽于阅读栏 · 全宽阅读',readerShare:'阅读栏占 {percent}%',
    previousEntry:'上一条',nextEntry:'下一条',listPosition:'第 {current} / {total} 行',
    outsideResults:'不在已加载的结果中',loadMore:'继续加载',loadingMore:'正在加载…',retryLoad:'重新加载',exportError:'无法导出 CSV。',
    loadedRange:'已显示 {shown} / {total} {unit}',allLoaded:'已全部显示 {total} {unit}',noResults:'0 条结果',units:{stacked:'行',each:'条'},loading:'正在读取条目…',retry:'重新读取',readError:'无法读取条目',
    body:'正文',fields:'属性',relations:'关系',
    detailTabs:'详情栏目',moreFilters:'更多筛选条件',allEntries:'全部条目',
    librarySettings:'资料库设置…',
    emptyLibrary:'资料库设置收窄了规则版本或作品范围，部分条目未列出。',filterLibraryHint:'资料库、规则版本与作品范围在顶栏「资料库」中设置。',
    libraryState:{narrowed:'已收窄',each:'不去重'},
    collectionErrors:{invalid_collection:'资料库地址无效。',unknown_collection:'未找到这个资料库。',collection_unavailable:'这个资料库尚未接入。'},
    moreTypes:'{first}等 {count} 类',nameField:'名称',bodyField:'正文',and:'且',or:'或',joinAnd:'且，改为或',joinOr:'或，改为且',openParen:'左括号',closeParen:'右括号',ghostParen:'未配对的括号按行首或行尾补全',impliedOpen:'“或”连起的一组开始',impliedClose:'“或”连起的一组结束',impliedGroup:'“或”比“且”结合得更紧，括起的几项读作一组',sectionsScope:'含未分类段落',mergeInto:'并入',excludeGroup:'排除',allExcept:'全部条目（不含{list}）',excludeInstead:'改为排除',includeInstead:'改为包含',isNot:'不是',lacks:'不含',excludeCount:'排除它会去掉 {count} 条',nameContains:'包含“{text}”',presetField:'预设',clearGroup:'清除',invertGroup:'反选',optionCount:'选中后 {count} 条',
    elsewhere:'其他类型另有 {count} 条',elsewhereTitle:'搜索在其他类型中的匹配：{list}',dropCondition:'去掉 {condition}',
    problems:'查询提示',unknownField:'anna 不认识“{condition}”这个条件，它不影响结果。',lacksSome:'{types}没有{fields}，不会出现在结果中。',lacksAll:'{types}没有{fields}，没有条目满足{conditions}。',
    crInverted:'最低 CR {min} 高于最高 CR {max}，两者不能同时成立。',cannotBoth:'条目只有一个{field}，{a}与{b}不能同时成立。',fixUseOr:'改为“或”',fixMerge:'合并为一张卡片',crUnreadable:'CR “{value}”不是数字或分数。',queryRejected:'这个查询无法执行：{message}',queryHeld:'条件需要修改',
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
    outsideResults:'Not among the loaded results',loadMore:'Load more',loadingMore:'Loading…',retryLoad:'Try again',exportError:'Unable to export CSV.',
    loadedRange:'Showing {shown} of {total} {unit}',allLoaded:'All {total} {unit} shown',noResults:'No results',units:{stacked:'rows',each:'entries'},loading:'Loading entry…',retry:'Try again',readError:'Unable to load entry',
    body:'Text',fields:'Attributes',relations:'Relations',
    detailTabs:'Detail sections',moreFilters:'More filters',allEntries:'All entries',
    librarySettings:'Library settings…',
    emptyLibrary:'Your library settings leave out some rules versions or works, so some entries are not listed.',filterLibraryHint:'Collections, rules versions and works are set under Library in the top bar.',
    libraryState:{narrowed:'narrowed',each:'not deduplicated'},
    collectionErrors:{invalid_collection:'Invalid collection address.',unknown_collection:'Collection not found.',collection_unavailable:'This collection is not connected yet.'},
    moreTypes:'{first} and {count} types',nameField:'Name',bodyField:'Text',and:'and',or:'or',joinAnd:'and; switch to or',joinOr:'or; switch to and',openParen:'Opening parenthesis',closeParen:'Closing parenthesis',ghostParen:'An unmatched parenthesis is completed at the start or end of the line',impliedOpen:'Start of a group joined by “or”',impliedClose:'End of a group joined by “or”',impliedGroup:'“or” binds tighter than “and”, so the bracketed items read as one group',sectionsScope:'Including untyped sections',mergeInto:'Add to',excludeGroup:'Exclude',allExcept:'All entries except {list}',excludeInstead:'Exclude instead',includeInstead:'Include instead',isNot:'is not',lacks:'lacks',excludeCount:'Excluding it removes {count}',nameContains:'contains “{text}”',presetField:'Preset',clearGroup:'Clear',invertGroup:'Invert',optionCount:'{count} results if chosen',
    elsewhere:'{count} more in other types',elsewhereTitle:'Matches for this search in other types: {list}',dropCondition:'Without {condition}',
    problems:'Query notes',unknownField:'anna does not know the condition “{condition}”; it does not affect the results.',lacksSome:'{types} have no {fields}, so they do not appear in the results.',lacksAll:'{types} have no {fields}, so nothing meets {conditions}.',
    crInverted:'Minimum CR {min} is above maximum CR {max}; both cannot hold.',cannotBoth:'An entry has one {field}, so {a} and {b} cannot both hold.',fixUseOr:'Use “or”',fixMerge:'Merge into one card',crUnreadable:'CR “{value}” is not a number or fraction.',queryRejected:'This query cannot run: {message}',queryHeld:'Conditions need a fix',
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
const sourceURL=(path,start=0,end=0)=>annaKit.href('/source?'+new URLSearchParams({path,start,end})+'#evidence');
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
async function api(path,signal){const r=await annaKit.request(annaLibrary.withScope(path),{signal});const d=await r.json();
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
// A remembered query is its kept conditions: the open entry and unfinished typing are not part of it.
function queryKey(){return address({typed:false,omit:['entry','tab']}).toString();}
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
  // The address writes the line in the bar's order, so a reload or Back shows each card where it was.
  if(!replace)saveUiState();
  const search=address().toString();history[replace?'replaceState':'pushState'](history.state,'',location.pathname+(search?'?'+search:''));
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
// chosen in one card are alternatives. A card can instead exclude its values (`not_<key>`): it then holds for
// entries with none of them. The panel edits a field's card where it stands; a field whose card excludes is in
// exclude mode. A card stays as chosen, every value included, until the reader removes it.
const NOT='not_';
const baseKey=key=>key.replace(/^not_/,'');
// The condition bar reads like a line of text: cards, parentheses and the joins between them, with the text
// field as the caret. Neighbouring cards join with ∧ until the reader turns a join to ∨, and ∨ binds tighter than
// ∧: `A ∧ B ∨ C` reads A ∧ (B ∨ C), so one turned join adds an alternative and leaves the rest required.
// Parentheses regroup. The address writes the line in its order, as anna/presentation/expression.py reads it:
// consecutive values of one field are one card, each search term is a card of its own, and the markers `or`,
// `and`, `open` and `close` stand between them.
const TERM_KEYS=new Set(['q','body','typing','not_q','not_body']);
const FIELD_KEYS=new Set(['type','cr_min','cr_max','conflict','ruleset','legacy','status','source','origin','package','copy_status']);
const NEGATABLE=new Set(['type','q','body','ruleset','legacy','status','source','origin','package','copy_status']);
const MARKERS=new Set(['or','and','open','close']);
const cardKey=k=>TERM_KEYS.has(k)||FIELD_KEYS.has(k)||k.startsWith('f_')||k.startsWith(NOT)&&(NEGATABLE.has(baseKey(k))||baseKey(k).startsWith('f_'));
// A token keeps its `uid` while it stays in the line; `or` says it joins the token before it with ∨. `caret`
// counts the tokens before the field; `caretOr` is the join a card typed at the caret takes where no card follows.
let line=[],caret=0,caretOr=false,tokenCount=0;
const token=t=>({uid:++tokenCount,or:false,...t});
const card=(key,values)=>token({kind:'card',key,values});
const ends=t=>t?.kind==='card'||t?.kind==='close';
const begins=t=>t?.kind==='card'||t?.kind==='open';
// A join stands after a card or `)` and before a card or `(`.
const joined=i=>ends(line[i-1])&&begins(line[i]);
function settleJoins(){line.forEach((t,i)=>{if(!joined(i))t.or=false;});}
const cardsOf=test=>line.filter(t=>t.kind==='card'&&test(t));
const valuesOf=key=>cardsOf(t=>t.key===key).flatMap(t=>t.values);
const tokenOf=uid=>line.find(t=>t.uid===Number(uid))||null;
const orTurned=()=>line.some((t,i)=>t.or&&joined(i));
// The join at the caret: the one between its neighbours, or the one the next card takes where none follows.
const gapOr=()=>joined(caret)?line[caret].or:caretOr;
function setGapOr(or){
  if(!ends(line[caret-1]))return;
  if(joined(caret)){if(line[caret].or!==or){line[caret].or=or;commit();}}else{caretOr=or;record();renderCards();}
}
// New tokens land at the caret and take its join on both sides: a card typed among alternatives joins them.
function insert(tokens){
  tokens[0].or=gapOr();line.splice(caret,0,...tokens);caret+=tokens.length;caretOr=false;settleJoins();
}
function partner(i){
  const step=line[i].kind==='open'?1:-1;let depth=0;
  for(let j=i;j>=0&&j<line.length;j+=step){if(line[j].kind===line[i].kind)depth++;else if(line[j].kind!=='card'&&--depth===0)return j;}
  return -1;
}
// A card leaves with the tighter of its joins, so it leaves its alternatives and the rest keeps its shape. A
// parenthesis leaves with its partner, ungrouping what they held.
function removeAt(i){
  const t=line[i];if(!t)return;
  for(const j of t.kind==='card'?[i]:[i,partner(i)].filter(j=>j>=0).sort((a,b)=>b-a)){
    const gone=line[j],next=line[j+1];
    if(gone.kind==='card'&&joined(j)&&joined(j+1))next.or=gone.or&&next.or;
    if(gone.kind==='open'&&next)next.or=gone.or;
    line.splice(j,1);if(caret>j)caret--;
  }
  settleJoins();
}
// The tokens as the line reads: a `(` it leaves open closes at its end and a stray `)` opens at its start (`ghost`).
// With `typed`, the words being typed stand at the caret as a card taking the caret's join, as the address writes them.
function reading(typed=false){
  const tokens=typed&&draft?[...line.slice(0,caret),{kind:'card',key:'typing',values:[draft],draft:true,or:gapOr()},...line.slice(caret)]:line;
  let depth=0,stray=0;
  for(const t of tokens){if(t.kind==='open')depth++;else if(t.kind==='close'){if(depth)depth--;else stray++;}}
  const ghosts=(kind,n)=>Array.from({length:n},()=>({kind,ghost:true}));
  return [...ghosts('open',stray),...tokens,...ghosts('close',depth)];
}
// The line as the server reads it: cards are leaves, `{op,parts}` their ∧ (`all`) or ∨ (`any`). An empty group
// stands for nothing. `spans` collects the groups by the first and last index of their tokens: each pair of
// parentheses, and each run of alternatives that ∨ binds among ∧-joined neighbours (`implied`), which reads as
// though parenthesized while the line shows it level with its neighbours.
function lineTree(ts=reading(),spans=null){
  let at=0;const more=()=>at<ts.length&&ts[at].kind!=='close';
  const join=(op,parts)=>{parts=parts.filter(Boolean).flatMap(p=>p.op===op?p.parts:[p]);return parts.length>1?{op,parts}:parts[0]||null;};
  const atom=()=>{const from=at,t=ts[at++];if(t.kind!=='open')return t;const inner=all(),to=at++;spans?.push({from,to});return inner;};
  const any=()=>{const from=at,parts=[atom()];while(more()&&ts[at].or)parts.push(atom());return {from,to:at-1,alternatives:parts.filter(Boolean).length,node:join('any',parts)};};
  const all=()=>{
    const runs=[];while(more())runs.push(any());
    if(runs.filter(r=>r.node).length>1)for(const {from,to,alternatives} of runs)if(alternatives>1)spans?.push({from,to,implied:true});
    return join('all',runs.map(r=>r.node));
  };
  return all()||{op:'all',parts:[]};
}
// Whether two cards must both hold: they stand side by side in one conjunction.
const conjoined=(a,b,node=lineTree())=>!!node.op&&(node.op==='all'&&node.parts.includes(a)&&node.parts.includes(b)||node.parts.some(p=>conjoined(a,b,p)));
// Three-valued: whether the line can hold given what `leaf` knows of each card (true, false, or null for unknown).
function possible(node,leaf){
  if(!node.op)return leaf(node);
  const values=node.parts.map(p=>possible(p,leaf));
  if(node.op==='all')return values.includes(false)?false:values.every(v=>v===true)?true:null;
  return values.includes(true)?true:values.every(v=>v===false)?false:null;
}
// The address of the line. The draft (`typing`) or the probe stands at the caret and takes its join; `cards`
// lists the cards in the order the server numbers them, which is how counts name a card.
function lineParams({typed=true,probe=false}={}){
  const tokens=[...line],extra=probe?{kind:'probe'}:typed&&draft?{kind:'card',key:'typing',values:[draft],draft:true}:null;
  if(extra)tokens.splice(caret,0,{...extra,or:gapOr()});
  const out=new URLSearchParams(),cards=[];
  const left=t=>ends(t)||t?.kind==='probe',right=t=>begins(t)||t?.kind==='probe';
  tokens.forEach((t,i)=>{
    const prev=tokens[i-1],join=left(prev)&&right(t);
    if(join&&t.or)out.append('or','');
    else if(join&&t.kind==='card'&&prev.kind==='card'&&prev.key===t.key&&!TERM_KEYS.has(t.key))out.append('and','');
    if(t.kind!=='card'){out.append(t.kind,'');return;}
    cards.push(t.draft?'typing':t.uid);for(const v of t.values)out.append(t.key,v);
  });
  return {params:out,cards};
}
// The page's address: the line, then the request's other parameters (sort, entry, sections…).
function address({typed=true,probe=false,omit=[]}={}){
  const out=lineParams({typed,probe}).params;
  for(const [k,v] of params)if(!omit.includes(k))out.append(k,v);
  return out;
}
// A link's line read back. The draft returns to the field at its place; every other parameter is kept in `params`.
function readLine(search){
  const tokens=[],options=new URLSearchParams();let or=false,split=true,typed=null;
  for(const [k,v] of new URLSearchParams(search)){
    if(MARKERS.has(k)){if(k==='open')tokens.push(token({kind:'open',or}));else if(k==='close')tokens.push(token({kind:'close'}));or=k==='or';split=true;continue;}
    if(k==='probe'){or=false;split=true;continue;}
    if(!cardKey(k)){options.append(k,v);continue;}
    const value=TERM_KEYS.has(k)?v.trim():v;if(!value)continue;
    if(k==='typing'){typed??={text:value,at:tokens.length,or};or=false;split=true;continue;}
    const last=tokens.at(-1);
    if(!split&&!TERM_KEYS.has(k)&&last?.kind==='card'&&last.key===k)last.values.push(value);
    else tokens.push({...card(k,[value]),or});
    or=false;split=false;
  }
  line=tokens;settleJoins();caret=typed?typed.at:line.length;
  caretOr=!!typed&&!joined(caret)&&ends(line[caret-1])&&typed.or;
  draft=typed?.text||'';$('#q').value=draft;params=options;
}
// Every edit of the line ends here. A change of the chosen types reads a new table; any other reads the results.
let tableTypes=null;
function commit(replace=false){
  record();
  if(tableKey()!==tableTypes){clearTimeout(timer);params.delete('entry');historyUpdate(replace);closeEntry({update:false,restoreFocus:false});changeTable();return;}
  changed(replace);
}
function changed(replace=false){
  clearTimeout(timer);params.delete('entry');historyUpdate(replace);closeEntry({update:false,restoreFocus:false});
  // A table the server could not read is read again once the reader edits the query.
  if(tableRejected){changeTable();return;}
  sync();$('.table-wrap').scrollTop=0;load();refreshFacets();
}
// Undo and redo step through the bar's revisions as in a text editor. A revision is what the bar shows: its tokens,
// the join chosen for the next card, the words being typed and the sections scope. It keeps the caret where the
// reader left it, but moving the caret alone makes no revision, and words typed in one run at one place make one.
// Stepping back is an edit like any other: it writes the address, so Back returns to the revision undone.
const REVISIONS=100;
let revision=null,undoable=[],redoable=[],typingRun=false;
const copyTokens=tokens=>tokens.map(t=>({...t,...t.values&&{values:[...t.values]}}));
const barState=()=>({tokens:copyTokens(line),caret,caretOr,text:$('#q').value,sections:params.get('sections')||''});
const shown=r=>JSON.stringify([r.tokens.map(({uid,...t})=>t),r.caretOr,r.text.trim(),r.sections]);
function record({typing=false}={}){
  const now=barState(),was=revision;revision=now;
  if(!was)return;
  if(shown(now)===shown(was)){if(now.caret!==was.caret)typingRun=false;return;}
  if(!(typing&&typingRun)){undoable.push(was);if(undoable.length>REVISIONS)undoable.shift();}
  redoable=[];typingRun=typing;
}
function travel(from,to){
  const r=from.pop();if(!r)return;
  clearTimeout(timer);to.push(barState());
  line=copyTokens(r.tokens);caret=r.caret;caretOr=r.caretOr;setParam('sections',r.sections);
  $('#q').value=r.text;draft=r.text.trim();suggestionGroups=null;suggestionGeneration++;closeSuggestions();
  revision=barState();typingRun=false;commit();
}
const undo=()=>travel(undoable,redoable),redo=()=>travel(redoable,undoable);
// ⌘Z and ⇧⌘Z (Ctrl+Z, Ctrl+Shift+Z or Ctrl+Y elsewhere) act on the bar from the field and from the query around
// it; another text field and the reader keep their own.
const apple=/Mac|iPhone|iPad/.test(navigator.platform);
function revisionCommand(e){
  if(e.altKey||e.isComposing||composingSearch||!(apple?e.metaKey&&!e.ctrlKey:e.ctrlKey&&!e.metaKey))return null;
  const key=e.key.toLowerCase();
  return key==='z'?(e.shiftKey?redo:undo):key==='y'&&!apple&&!e.shiftKey?redo:null;
}
const writesText=el=>el.isContentEditable||el.matches('textarea,input:not([type=checkbox],[type=radio],[type=button],[type=range])');
const revisesBar=el=>el===$('#q')||el===document.body||$('#query-main').contains(el)&&!writesText(el);
// The browser's own Undo (its Edit menu, a touch gesture) reaches the field as an input it has not made.
$('#q').addEventListener('beforeinput',e=>{
  const step=e.inputType==='historyUndo'?undo:e.inputType==='historyRedo'?redo:null;
  if(step){e.preventDefault();step();}
});
// The panel edits a field's card: the last card written for the field, included or excluded. A field without one
// gets a new card at the end of the line.
const panelCard=key=>cardsOf(t=>baseKey(t.key)===key).at(-1)||null;
const excluding=g=>!!panelCard(g.key)?.key.startsWith(NOT);
function selected(g){return new Set((panelCard(g.key)?.values||[]).filter(v=>v!==NONE));}
const fieldValue=key=>panelCard(key)?.values[0]||'';
function setSelection(key,values){
  const at=panelCard(key);values=[...new Set(values)];
  if(key==='type')values.sort((a,b)=>typeRank(a)-typeRank(b));
  if(at){if(values.length)at.values=values;else removeAt(line.indexOf(at));return;}
  if(!values.length)return;
  const end=caret===line.length;line.push(card(key,values));settleJoins();if(end)caret=line.length;
}
function clearField(key){for(const t of cardsOf(t=>baseKey(t.key)===key))removeAt(line.indexOf(t));}
// Turning a card over moves its values between included and excluded; it keeps its place.
function flipCard(t){t.key=t.key.startsWith(NOT)?baseKey(t.key):NOT+t.key;commit();}
function dropToken(t){const i=line.indexOf(t);if(i<0)return;removeAt(i);commit();}
// Each option counts what choosing it would match under every other condition (serve.facets).
const optionTitle=(o,g)=>`${o.title||o.label} · ${(g&&excluding(g)?ui.excludeCount:ui.optionCount).replace('{count}',fmt(o.count||0))}`;
function multiGroup(g){
  const chosen=selected(g),free=!chosen.size;
  return `<fieldset class="facet ${free?'unconstrained':''} ${excluding(g)?'excluding':''}" data-group="${esc(g.key)}"><legend>${esc(g.label)}</legend><div class="facet-line"><div class="group-actions"><button type="button" data-action="clear" aria-label="${esc(g.label)}${ui.clearGroup}" ${free?'disabled':''}>${ui.clearGroup}</button><button type="button" data-action="invert" aria-label="${esc(g.label)}${ui.invertGroup}" ${free?'disabled':''}>${ui.invertGroup}</button><button type="button" data-action="exclude" aria-label="${esc(g.label)}${ui.excludeGroup}" aria-pressed="${excluding(g)}" ${free?'disabled':''}>${ui.excludeGroup}</button></div><div class="choices">${g.options.map(o=>`<label class="chip ${o.count?'':'empty'}" title="${esc(optionTitle(o,g))}" data-option-search="${esc((o.search||o.label).toLowerCase())}"><input type="checkbox" value="${esc(o.value)}" aria-label="${esc(g.label+'：'+o.label)}" ${chosen.has(o.value)?'checked':''}><span>${esc(o.label)}<small class="chip-count">${fmt(o.count||0)}</small></span></label>`).join('')}</div></div></fieldset>`;
}
function flagGroup(g){
  const active=fieldValue(g.key);
  const known=g.options.filter(o=>o.value==='true'||o.value==='false');
  const options=[{value:'',label:'任意'},...known.map(o=>({...o,label:g.key==='f_attunement'?(o.value==='true'?'需要':'无需'):o.value==='true'?'是':'否'})),...g.options.filter(o=>!['true','false','__na__'].includes(o.value))];
  return `<fieldset class="flag-group" data-group="${esc(g.key)}"><legend>${esc(g.label)}</legend><div class="segmented">${options.map(o=>`<button type="button" data-flag="${esc(o.value)}" aria-label="${esc(g.label+'：'+o.label)}" aria-pressed="${active===o.value}">${esc(o.label)}</button>`).join('')}</div></fieldset>`;
}
function rangeGroup(){
  const values=['0','1/8','1/4','1/2',...Array.from({length:30},(_,i)=>String(i+1))];
  for(const key of ['cr_min','cr_max'])if(fieldValue(key)&&!values.includes(fieldValue(key)))values.push(fieldValue(key));
  return `<fieldset class="facet cr-range"><legend>挑战等级</legend><div class="range-controls">${['cr_min','cr_max'].map((key,i)=>`<label>${i?'最高':'最低'} <select id="${key}" aria-label="${i?'最高':'最低'} CR"><option value="">不限</option>${values.map(v=>`<option value="${v}" ${fieldValue(key)===v?'selected':''}>${v}</option>`).join('')}</select></label>`).join('<span>—</span>')}<span class="muted">含边界；未提取与冲突值不进入范围结果</span></div></fieldset>`;
}
function cardHTML(t,note){
  const {key}=t,text=conditionText(t),negated=key.startsWith(NOT);
  const flip=negated?ui.includeInstead:ui.excludeInstead,flippable=!['cr_min','cr_max','conflict'].includes(key);
  const described=note?` aria-describedby="${note.id}"`:'';
  return `<span class="condition${negated?' negated':''}"${note?` data-tone="${note.tone}"`:''} data-uid="${t.uid}" data-key="${esc(key)}">`+
    (flippable?`<button type="button" class="condition-text" data-flip="${t.uid}" aria-label="${esc(text)}，${esc(flip)}" title="${esc(flip)}"${described}>${esc(text)}</button>`:`<span class="condition-text"${described}>${esc(text)}</span>`)+
    `<button type="button" class="condition-remove" data-remove="${t.uid}" aria-label="移除 ${esc(text)}" title="移除 ${esc(text)}">×</button></span>`;
}
// A parenthesis is written by the reader, completed at an end of the line (`ghost`), or implied where ∨ binds tighter
// than the ∧ beside it (`implied`, keyed by the token it stands beside). `pair` names the group it opens or closes.
function parenHTML({kind,uid,ghost,implied},pair,around){
  const open=kind==='open',label=implied?(open?ui.impliedOpen:ui.impliedClose):ghost?ui.ghostParen:open?ui.openParen:ui.closeParen;
  return `<span class="paren${ghost?' ghost':''}${implied?' implied':''}${around?' around-caret':''}" data-pair="${pair}"`+
    (uid?` data-uid="${uid}"`:'')+(implied?` data-implied="${esc(implied)}"`:'')+(ghost?' aria-hidden="true"':` role="img" aria-label="${esc(label)}"`)+
    ` title="${esc(implied?ui.impliedGroup:label)}">${open?'(':')'}</span>`;
}
// A join reads ∧ or ∨ and turns over when pressed. `at` names the token after it, or the caret.
function joinHTML(or,at,extra=''){
  const label=or?ui.joinOr:ui.joinAnd;
  return at==null?`<span class="join${extra}" data-op="${or?'or':'and'}" role="img" aria-label="${esc(or?ui.or:ui.and)}">${or?'∨':'∧'}</span>`
    :`<button type="button" class="join${extra}" data-op="${or?'or':'and'}" data-join="${at}" aria-label="${esc(label)}" title="${esc(label)}">${or?'∨':'∧'}</button>`;
}
// A card and the join before it stay the same while they keep their uid, whichever way they are turned. The
// joins beside the caret belong to no card: they may be hidden, and a hidden box has no place to slide from or to.
function conditionKey(el){
  if(el.dataset.implied)return 'i'+el.dataset.implied;
  if(el.matches('.join'))return el.dataset.join&&el.dataset.join!=='caret'?'j'+el.dataset.join:null;
  return el.dataset.uid?'t'+el.dataset.uid:null;
}
// The tokens are placed around the field, never the field among them: moving a focused field would blur it.
// Beside the caret the join shows while words are typed there or while it joins two cards, so the line always
// reads card ∧ card; a ∨ chosen for the next card shows at once. The groups the line reads in show as
// parentheses: the reader's, those completing the line, and those implied where ∨ binds tighter. The innermost
// group around the caret, the words being typed counted at their place, is marked `around-caret`.
let cardsShape='';
function renderCards({animate=false}={}){
  const strip=$('#active-filters'),input=$('#q'),notes=problemNotes();
  const ts=reading(true),spans=[];lineTree(ts,spans);
  let opens=0;while(ts[opens]?.ghost&&ts[opens].kind==='open')opens++;
  const at=opens+caret,typed=!!ts[at]?.draft,next=typed?at+1:at;
  const inside=s=>typed?s.from<=at&&at<=s.to:s.from<at&&at<=s.to;
  const around=spans.filter(inside).sort((a,b)=>(a.to-a.from)-(b.to-b.from))[0];
  // Implied parentheses open outermost first and close innermost first.
  const pairs=new Map(),opening=new Map(),closing=new Map(),add=(map,i,s)=>map.set(i,[...map.get(i)||[],s]);
  for(const s of spans)if(s.implied){add(opening,s.from,s);add(closing,s.to,s);}else{pairs.set(s.from,s);pairs.set(s.to,s);}
  const name=i=>ts[i].uid??(ts[i].draft?'typing':'g'+i);
  const implied=(kind,i)=>((kind==='open'?opening.get(i)?.sort((a,b)=>b.to-a.to):closing.get(i)?.sort((a,b)=>b.from-a.from))||[])
    .map((s,n)=>parenHTML({kind,implied:`${kind}${name(i)}.${n}`},spans.indexOf(s),s===around)).join('');
  // Each token with the join before it; the join at the caret stands beside the field instead.
  const piece=i=>{const t=ts[i];
    return (i!==next&&ends(ts[i-1])&&begins(t)?joinHTML(t.or,t.uid):'')+implied('open',i)+
      (t.kind==='card'?cardHTML(t,notes.get(t.uid)):parenHTML(t,spans.indexOf(pairs.get(i)),!!around&&pairs.get(i)===around))+implied('close',i);};
  const left=line[caret-1],right=line[caret],range=(a,b)=>Array.from({length:b-a},(_,k)=>piece(a+k)).join('');
  const before=range(0,at)+(ends(left)?joinHTML(gapOr(),'caret',' slot-before'+(joined(caret)?' joins':'')+(caretOr?' pending':'')):'')+(typed?implied('open',at):'');
  const after=(typed?implied('close',at):'')+(begins(right)?joinHTML(right.or,joined(caret)?'caret':null,' slot-after'):'')+range(next,ts.length)+
    (params.get('sections')==='1'?`<span class="condition scope" data-scope="sections"><span class="condition-text">${esc(ui.sectionsScope)}</span><button type="button" class="condition-remove" data-remove-scope="sections" aria-label="移除 ${esc(ui.sectionsScope)}" title="移除 ${esc(ui.sectionsScope)}">×</button></span>`:'');
  const shape=before+'\n'+after;
  if(shape!==cardsShape){
    cardsShape=shape;
    const active=strip.contains(document.activeElement)&&document.activeElement!==input&&document.activeElement;
    const keep=active&&{uid:active.closest('[data-uid]')?.dataset.uid,join:active.dataset.join,remove:active.matches('.condition-remove')};
    // The field takes its new width inside the change, so tokens are measured where they finally stand.
    const place=()=>{for(const el of [...strip.children])if(el!==input)el.remove();input.insertAdjacentHTML('beforebegin',before);input.insertAdjacentHTML('afterend',after);fitCaret();};
    animate?annaMotion.rearrange(strip,'.condition,.join,.paren',conditionKey,place):place();
    if(keep?.join)strip.querySelector(`[data-join="${CSS.escape(keep.join)}"]`)?.focus({preventScroll:true});
    else if(keep?.uid)strip.querySelector(`[data-uid="${CSS.escape(keep.uid)}"]`)?.querySelector(keep.remove?'.condition-remove':'button.condition-text')?.focus({preventScroll:true});
  }else fitCaret();
}
// At the end the field takes the rest of the line and shows its hint; between tokens, or before a `)` the line
// closes for the reader, it is as wide as its words.
let measure=null;
function fitCaret(){
  const strip=$('#active-filters'),input=$('#q'),inside=caret<line.length||!!reading().at(-1)?.ghost;
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
  caret=Math.max(0,Math.min(line.length,to));caretOr=false;record();renderCards();
}
// The gap a point falls in: after every token that ends above it or whose middle lies before it on its line.
function gapAt(x,y){
  let gap=0;
  for(const el of $('#active-filters').querySelectorAll('.condition[data-uid],.paren[data-uid]')){const r=el.getBoundingClientRect();if(r.bottom<=y||r.top<=y&&(r.left+r.right)/2<x)gap++;else break;}
  return gap;
}
// A query stays as the reader wrote it. Where cards cannot all hold, a chosen type cannot answer one, or the
// server cannot run the query, a note below the bar says so and offers the edits that would resolve it.
let rejection=null,shownProblems=[],problemsShape='';
const rejectionKey=()=>address({omit:['entry','tab']}).toString();
const crValue=s=>!s?null:s.includes('/')?s.split('/').map(Number).reduce((a,b)=>a/b):Number(s);
// The fields one value at a time: an entry has one type, one rules version, one legacy mark and one basis.
const SINGLE_FIELDS=new Set(['type','ruleset','legacy','status']);
function queryProblems(){
  const out=[],list=xs=>xs.join(ui.listJoin),quoted=xs=>list(xs.map(x=>`“${x}”`));
  const remove=c=>({label:ui.fixRemove.replace('{condition}',conditionText(c)),run:()=>dropToken(c)});
  const rejected=rejection&&rejection.query===rejectionKey()?{tone:'error',cards:[],text:ui.queryRejected.replace('{message}',rejection.message),fixes:[{label:ui.fixClearAll,run:clearAll}]}:null;
  if(rejected)out.push(rejected);
  const tree=lineTree(),crCards=cardsOf(c=>c.key==='cr_min'||c.key==='cr_max');
  // A bound that is not a number explains why the server cannot run the query; its note replaces the server's.
  for(const c of crCards)if(!Number.isFinite(crValue(c.values[0])))out.push({tone:'error',cards:[c.uid],explains:true,text:ui.crUnreadable.replace('{value}',c.values[0]),fixes:[remove(c)]});
  if(out.some(p=>p.explains))return out.filter(p=>p!==rejected);
  if(metadata){
    // A type-specific card holds for no entry of a type that cannot answer it. Where that leaves a chosen type out
    // of the results, the cards responsible share one note, which offers each removal and one narrowing.
    const conditions=metadata.type_conditions||{},answered=new Set(Object.values(conditions).flat());
    const field=c=>{const b=baseKey(c.key);return b.startsWith('f_')?b.slice(2):b==='cr_min'||b==='cr_max'?'cr':null;};
    for(const c of cardsOf(c=>field(c)&&!answered.has(field(c))))out.push({tone:'warning',cards:[c.uid],text:ui.unknownField.replace('{condition}',conditionText(c)),fixes:[remove(c)]});
    const lacks=(t,c)=>!!field(c)&&answered.has(field(c))&&!(conditions[t]||[]).includes(field(c));
    const knows=(t,free)=>c=>c===free?null:baseKey(c.key)==='type'?c.values.includes(t)!==c.key.startsWith(NOT):lacks(t,c)?c.key.startsWith(NOT):null;
    const can=t=>possible(tree,knows(t))!==false,types=chosenTypes(),groups=new Map();
    for(const t of types){
      if(can(t))continue;
      const blamed=cardsOf(c=>!c.key.startsWith(NOT)&&lacks(t,c)&&possible(tree,knows(t,c))!==false);
      if(!blamed.length)continue;
      const id=blamed.map(c=>c.uid).join();if(!groups.has(id))groups.set(id,{lacking:[],blamed});groups.get(id).lacking.push(t);
    }
    const anything=(metadata.type_order||[]).some(can),answering=types.filter(can),typeCards=cardsOf(c=>c.key==='type');
    for(const {lacking,blamed} of groups.values()){
      const narrow=typeCards.length!==1?[]:answering.length?[{label:ui.fixKeepTypes.replace('{types}',list(answering.map(typeName))),run:()=>{typeCards[0].values=answering;commit();}}]:!anything?[remove(typeCards[0])]:[];
      out.push({tone:anything?'warning':'error',cards:blamed.map(c=>c.uid),fixes:[...blamed.map(remove),...narrow],
        text:(anything?ui.lacksSome:ui.lacksAll).replace('{types}',list(lacking.map(typeName))).replace('{fields}',quoted([...new Set(blamed.map(c=>field(c)==='cr'?'CR':fieldLabel(baseKey(c.key))))])).replace('{conditions}',quoted(blamed.map(conditionText)))});
    }
  }
  // Two cards of a field that holds one value per entry cannot both hold with no value in common.
  const single=cardsOf(c=>SINGLE_FIELDS.has(c.key));
  single.forEach((a,i)=>single.slice(i+1).forEach(b=>{
    if(a.key!==b.key||a.values.some(v=>b.values.includes(v))||!conjoined(a,b,tree))return;
    const next=line.indexOf(b)===line.indexOf(a)+1;
    out.push({tone:'error',cards:[a.uid,b.uid],text:ui.cannotBoth.replace('{field}',fieldLabel(a.key)).replace('{a}',`“${conditionText(a)}”`).replace('{b}',`“${conditionText(b)}”`),
      fixes:[...(next?[{label:ui.fixUseOr,run:()=>{b.or=true;commit();}}]:[]),{label:ui.fixMerge,run:()=>{a.values=[...new Set([...a.values,...b.values])];if(a.key==='type')a.values.sort((x,y)=>typeRank(x)-typeRank(y));dropToken(b);}}]});
  }));
  // An inverted range cannot hold where both bounds are required together; under ∨ it reads outside the range.
  for(const min of crCards.filter(c=>c.key==='cr_min'))for(const max of crCards.filter(c=>c.key==='cr_max')){
    const [low,high]=[min.values[0],max.values[0]];
    if(!(crValue(low)>crValue(high))||!conjoined(min,max,tree))continue;
    out.push({tone:'error',cards:[min.uid,max.uid],text:ui.crInverted.replace('{min}',low).replace('{max}',high),
      fixes:[{label:ui.fixSwapCr.replace('{min}',high).replace('{max}',low),run:()=>{min.values=[high];max.values=[low];commit();}}]});
  }
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
  shownProblems.forEach((p,i)=>p.cards.forEach(uid=>{if(!notes.has(uid))notes.set(uid,{id:'query-problem-'+i,tone:p.tone});}));
  return notes;
}
// Cards and notes render even before a table is known, so a query the server rejects can still be edited.
function renderQuery(){
  renderProblems();renderCards({animate:true});
  $('#filter-count').textContent=cardsOf(()=>true).length||'';
  if(metadata){$('#title').textContent=typeTitle();updatePageChrome();}
}
function sync(){
  renderQuery();
  if(!schema)return;
  renderSortOptions();
  const desc=params.get('dir')==='desc';$('#direction').textContent=desc?'降序 ↓':'升序 ↑';
  $('#direction').setAttribute('aria-label',desc?'切换为升序':'切换为降序');
  for(const key of ['cr_min','cr_max'])if($('#'+key))$('#'+key).value=fieldValue(key);
  for(const g of groups){
    const el=document.querySelector(`[data-group="${g.key}"]`);if(!el)continue;
    const values=selected(g);el.classList.toggle('unconstrained',!values.size);el.classList.toggle('excluding',excluding(g));
    for(const input of el.querySelectorAll('input[type=checkbox]'))input.checked=values.has(input.value);
    for(const b of el.querySelectorAll('[data-action]'))b.disabled=!values.size;
    el.querySelector('[data-action=exclude]')?.setAttribute('aria-pressed',String(excluding(g)));
    for(const b of el.querySelectorAll('[data-flag]'))b.setAttribute('aria-pressed',fieldValue(g.key)===b.dataset.flag);
  }
  const extraCount=groups.filter(g=>panelCard(g.key)&&$('#domain-extra')?.querySelector(`[data-group="${g.key}"]`)).length;
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
    (extra?`<details id="domain-extra" ${extraGroups.some(g=>panelCard(g.key))||$('#domain-extra')?.open?'open':''}><summary>${esc(ui.moreFilters)} <span id="extra-count"></span></summary>${extra}</details>`:'');
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
// Counts name each card by its place; the cards a response describes are kept with it.
let facetCards=[];
const facetsURL=()=>'/api/facets?'+address({omit:['entry','sort','dir']});

// Counts follow every condition, so they refresh beside each query; a late response never replaces a newer one.
async function refreshFacets(){
  const gen=++facetGeneration;
  const cards=lineParams().cards;
  try{const data=await api(facetsURL());if(gen===facetGeneration){facetCards=cards;applyFacets(data);}}catch{/* The results report query errors; counts keep their last values. */}
}
// New types change the table itself: its columns, fields and sorts arrive before the rows are read.
// Conditions the new table cannot answer stay on the bar, with a note saying which types they leave out.
let tableRejected=false;
async function changeTable(){
  const gen=++panelGeneration,fgen=++facetGeneration;request?.abort();generation++;ready=false;tableRejected=false;tableTypes=tableKey();
  awaitResults('正在读取筛选…');$('.table-wrap').scrollTop=0;$('.table-wrap').scrollLeft=0;renderQuery();
  const cards=lineParams().cards;
  try{
    const data=await api(facetsURL());if(gen!==panelGeneration||fgen!==facetGeneration)return;
    facetCards=cards;applyFacets(data);
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
function entriesURL(offset,limit){const query=address();query.append('offset',offset);query.append('limit',limit);return '/api/entries?'+query;}
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
    setSelection(g.key,[...set]);commit();
  }else if(['cr_min','cr_max'].includes(el.id)){
    // An inverted range stays as chosen; the note below the bar offers to swap its ends.
    setSelection(el.id,el.value?[el.value]:[]);commit();
  }
});
$('#filter-panel').addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;const group=b.closest('[data-group]');if(!group)return;const g=groups.find(g=>g.key===group.dataset.group);
  if(b.hasAttribute('data-flag')){setSelection(g.key,b.dataset.flag?[b.dataset.flag]:[]);commit();return;}
  if(b.dataset.action==='exclude'){const at=panelCard(g.key);if(at)flipCard(at);return;}
  if(b.dataset.action==='clear'){clearField(g.key);commit();return;}
  const current=selected(g);setSelection(g.key,g.options.map(o=>o.value).filter(v=>!current.has(v)));commit();
});
// Name searches are cards like any other: each kept search is a `q` card, and the text being typed is the draft,
// a live search at the caret that Enter keeps as a card. The address carries the draft as `typing`, so a reload
// returns it to the field. Both are page state, read from the address only on load and history navigation: it
// trails the field by the debounce, and inferring kept searches from it once took half-typed IME text for one.
const searchTerms=()=>[...new Set(valuesOf('q'))];
// Body searches look in an entry's text after its title; they are added from the suggestions, never typed live.
const bodyTerms=()=>valuesOf('body');
let draft='';
const searching=()=>valuesOf('q').length>0||!!draft;
function clearDraft(){draft='';suggestionGroups=null;suggestionGeneration++;$('#q').value='';closeSuggestions();}
let composingSearch=false;
// Results that follow each keystroke replace the rows without fading, so the list does not flicker while typing.
let typedQuery=false;
// The field reads `(` and `)` as parentheses and `|` and `&` as the join at the caret, in either width; words
// typed before one are kept as a search where they stand.
const SYNTAX={'(':'open','（':'open',')':'close','）':'close','|':'or','｜':'or','&':'and','＆':'and'},SYNTAX_RE=/[()（）|｜&＆]/;
function takeSyntax(){
  const field=$('#q');let text=field.value;if(!SYNTAX_RE.test(text))return false;
  for(let m;(m=SYNTAX_RE.exec(text));){
    const words=text.slice(0,m.index).trim(),kind=SYNTAX[m[0]];text=text.slice(m.index+1);
    if(words)insert([card('q',[words])]);
    if(kind==='open'||kind==='close')insert([token({kind})]);
    else if(ends(line[caret-1])){if(joined(caret))line[caret].or=kind==='or';else caretOr=kind==='or';}
  }
  clearTimeout(timer);field.value=text.trimStart();draft=field.value.trim();
  if(!draft)closeSuggestions();else renderSuggestions();
  commit();return true;
}
function queueSearch(){
  clearTimeout(timer);if(composingSearch)return;
  if(takeSyntax())return;
  draft=$('#q').value.trim();record({typing:true});renderCards();renderSuggestions();
  timer=setTimeout(()=>{typedQuery=true;changed(true);refreshSuggestionCounts();},160);
}
// Suggestions count with the probe where the caret stands: each value says what placing it there would match.
let suggestionGroups=null,suggestionGeneration=0;
async function refreshSuggestionCounts(){
  const gen=++suggestionGeneration,query=address({probe:true,omit:['entry','sort','dir']});
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
// “塑能” the school, “PHB” the sources, “坐骑” the preset. Choosing a value adds its card at the caret; where the
// field already has a card, the best value can also join that card instead.
let suggestions=[],activeSuggestion=0;
// Rules versions and kinds of content belong to the reader's library, not to one query.
const LIBRARY_GROUPS=new Set(['ruleset','origin']);
const fold=t=>String(t||'').normalize('NFKC').toLowerCase();
function suggestionList(text){
  const q=fold(text);if(!q)return [];
  const values=(suggestionGroups&&draft?suggestionGroups:groups).filter(g=>g.mode==='multi'&&!LIBRARY_GROUPS.has(g.key)).flatMap(g=>
    g.options.filter(o=>fold(o.label).includes(q)||fold(o.search).includes(q))
      .map(o=>({kind:'value',group:g,option:o,field:g.label,label:o.label,count:o.count||0,rank:fold(o.label)===q?0:fold(o.label).startsWith(q)?1:2})));
  const presets=(metadata?.presets||[]).filter(p=>fold(p.name).includes(q)).map(p=>({kind:'preset',preset:p,field:ui.presetField,label:`${p.name}（${typeName(p.type)}）`,count:null,rank:fold(p.name)===q?0:1}));
  const ranked=[...values,...presets].sort((a,b)=>a.rank-b.rank||(b.count??-1)-(a.count??-1)).slice(0,8);
  // The nearest card of the best value's field, if it lacks the value, is offered to take it.
  const best=ranked.find(s=>s.kind==='value'),near=c=>Math.abs(line.indexOf(c)+.5-caret);
  const into=best&&cardsOf(c=>c.key===best.group.key&&!c.values.includes(best.option.value)).sort((a,b)=>near(a)-near(b))[0];
  if(into)ranked.splice(ranked.indexOf(best)+1,0,{kind:'merge',card:into,option:best.option,field:ui.mergeInto,count:null,
    label:conditionText({key:into.key,values:[...into.values,best.option.value]})});
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
  insert([card('q',[text])]);commit();
}
// What a suggestion adds lands at the caret, as a card of its own; the merge item adds the value to its card.
// The typed text found the value, so it is not kept as a name search.
function applySuggestion(item){
  clearTimeout(timer);
  if(!item||item.kind==='name'){keepDraft();return;}
  const text=$('#q').value.trim();clearDraft();
  if(item.kind==='body')insert([card('body',[text])]);
  else if(item.kind==='merge'){
    const into=item.card;into.values=[...new Set([...into.values,item.option.value])];
    if(into.key==='type')into.values.sort((a,b)=>typeRank(a)-typeRank(b));
  }else if(item.kind==='preset'){
    // A preset is one slice, its type and its value; among alternatives it stands in parentheses as one of them.
    const slice=[card('type',[item.preset.type]),card('f_'+item.preset.field,[item.preset.value])];
    insert(gapOr()?[token({kind:'open'}),...slice,token({kind:'close'})]:slice);
  }else insert([card(item.group.key,[item.option.value])]);
  commit();
}
// The field edits the line like text. At the field's start, Backspace removes the token before the caret and ←
// steps over it; at its end, Delete and → do the same for the token after it. Home and End, once the caret
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
  if(e.key==='Backspace'&&atStart&&caret>0)act=()=>dropToken(line[caret-1]);
  else if(e.key==='Delete'&&atEnd&&caret<line.length)act=()=>dropToken(line[caret]);
  else if(home&&atStart&&caret>0)act=()=>moveCaret(0);
  else if(end&&atEnd&&caret<line.length)act=()=>moveCaret(line.length);
  else if(e.key==='ArrowLeft'&&!e.metaKey&&atStart&&caret>0)act=()=>moveCaret(caret-1);
  else if(e.key==='ArrowRight'&&!e.metaKey&&atEnd&&caret<line.length)act=()=>moveCaret(caret+1);
  if(act){e.preventDefault();act();}
});
// A press between tokens, or on the bar around them, puts the caret there.
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
  clearDraft();line=[];caret=0;caretOr=false;rejection=null;
  for(const key of [...params.keys()])if(!['sort','dir'].includes(key))params.delete(key);
  record();historyUpdate();closeEntry({update:false,restoreFocus:false});changeTable();
}
$('#reset').onclick=clearAll;
// × removes its card and leaves the caret where the card was, unless words typed elsewhere hold the caret; whatever
// is being typed stays in the field. A join turns between ∧ and ∨ where it stands.
$('#active-filters').addEventListener('click',e=>{
  const remove=e.target.closest('[data-remove]');
  if(remove){const t=tokenOf(remove.dataset.remove),at=line.indexOf(t);if(at>=0&&!$('#q').value.trim()){caret=at;caretOr=false;}dropToken(t);$('#q').focus({preventScroll:true});return;}
  if(e.target.closest('[data-remove-scope]')){params.delete('sections');commit();$('#q').focus({preventScroll:true});return;}
  const join=e.target.closest('[data-join]');
  if(join){if(join.dataset.join==='caret')setGapOr(!gapOr());else{const t=tokenOf(join.dataset.join);if(t){t.or=!t.or;commit();}}return;}
  const flip=e.target.closest('[data-flip]');if(flip){const t=tokenOf(flip.dataset.flip);if(t)flipCard(t);}
});
// A parenthesis under the pointer lights the one it pairs with.
for(const [type,on] of [['mouseover',true],['mouseout',false]])$('#active-filters').addEventListener(type,e=>{
  const paren=e.target.closest('.paren[data-pair]');if(!paren)return;
  for(const el of $('#active-filters').querySelectorAll(`.paren[data-pair="${paren.dataset.pair}"]`))el.classList.toggle('pointed',on);
});
$('#query-problems').addEventListener('click',e=>{
  const b=e.target.closest('[data-fix]');if(!b)return;const [i,j]=b.dataset.fix.split(':').map(Number);shownProblems[i]?.fixes[j]?.run();
});
$('#sort').onchange=()=>{params.set('sort',$('#sort').value);changed();};
$('#direction').onclick=()=>{setParam('dir',params.get('dir')==='desc'?'asc':'desc');changed();};
$('#columns').addEventListener('click',e=>{if(awaitingResults())return;const b=e.target.closest('[data-sort]');if(b)sortBy(b.dataset.sort);});
let exporting=false;
$('#export').onclick=async()=>{
  const path=annaLibrary.withScope('/api/export?'+address());
  if(!annaKit.pages){location.href=path;return;}
  if(exporting)return;
  exporting=true;
  const status=$('#export-error');status.hidden=true;
  try{
    const response=await annaKit.request(path);
    if(!response.ok){
      const error=await response.json().catch(()=>({}));
      throw new Error(error.error||error.code||response.status);
    }
    const url=URL.createObjectURL(await response.blob());
    const link=document.createElement('a');link.href=url;
    link.download=response.headers.get('Content-Disposition')?.match(/\bfilename="([^"]+)"/i)?.[1]||'5e-anna.csv';
    document.body.append(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(e){status.textContent=[ui.exportError,e.message].filter(Boolean).join(' ');status.hidden=false;}
  finally{exporting=false;}
};
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
  const gen=++navigationGeneration,state=e.state?.anna,oldTable=tableKey();
  clearTimeout(timer);clearTimeout(uiSaveTimer);restoringUi=true;
  restoreRows=state?.loadedRows||0;clearDraft();readLine(location.search);record();rejection=null;
  if(!params.has('entry'))closeEntry({update:false,restoreFocus:false});
  if(oldTable===tableKey()&&ready){sync();refreshFacets();await load();}else await changeTable();
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
  const step=revisionCommand(e);
  if(step&&revisesBar(e.target)&&!$('#detail').matches(':modal')){
    // A token that the step takes away hands the focus back to the field.
    e.preventDefault();const inBar=$('.condition-bar').contains(e.target);step();
    if(inBar&&!$('.condition-bar').contains(document.activeElement))$('#q').focus({preventScroll:true});
    return;
  }
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
// Entry types are one field among the others. The types of every type card together pick the table's columns,
// fields and sorts, and the heading names them.
const chosenTypes=()=>[...new Set(valuesOf('type'))];
// Where a ∨ lets entries of any type match, the types no longer bound the table: it reads as all entries, widened
// by the chosen types' columns.
const openTypes=()=>possible(lineTree(),c=>baseKey(c.key)==='type'?null:true)===true;
const tableKey=()=>chosenTypes().join()+(openTypes()?'|*':'');
const boundTypes=()=>openTypes()?[]:chosenTypes();
const typeName=t=>metadata?.types[t]||t;
const typeRank=t=>{const i=metadata.type_order.indexOf(t);return i<0?metadata.type_order.length:i;};
// A preset is one type plus one facet value; it names the heading while exactly that slice is required.
function activePreset(){
  const types=chosenTypes();if(types.length!==1||!metadata||orTurned())return null;
  return metadata.presets.find(p=>p.type===types[0]&&valuesOf('f_'+p.field).join()===p.value)||null;
}
// The tab title names the open entry, else the chosen slice.
function updatePageChrome(){
  const heading=typeTitle(),subject=currentEntry?(currentEntry.name_zh||currentEntry.name):heading===ui.allEntries?'':heading;
  document.title=annaPageTitle('query',subject);
}
function typeTitle(){
  const types=boundTypes(),left=openTypes()?[]:valuesOf(NOT+'type');
  if(!types.length)return left.length?ui.allExcept.replace('{list}',left.map(typeName).join('、')):ui.allEntries;
  const preset=activePreset();if(preset)return preset.name;
  return types.length<=2?types.map(typeName).join('、'):ui.moreTypes.replace('{first}',typeName(types[0])).replace('{count}',types.length);
}
// An empty name search can still have matches among the untyped sections. The server counts them
// under the same conditions, so the offer never leads to a result the filter cannot return.
// While searching within some types, matches in the others stay one step away; an empty result names the
// conditions whose removal would bring results back.
function renderElsewhere(){
  const types=boundTypes(),typeGroup=groups.find(g=>g.key==='type');
  const others=(searching()||bodyTerms().length)&&types.length?typeGroup.options.filter(o=>o.count&&!types.includes(o.value)):[];
  const total=others.reduce((sum,o)=>sum+o.count,0);
  $('#elsewhere').hidden=!total;$('#elsewhere').textContent=total?ui.elsewhere.replace('{count}',fmt(total)):'';
  $('#elsewhere').title=total?ui.elsewhereTitle.replace('{list}',others.map(o=>`${o.label} ${fmt(o.count)}`).join(' · ')):'';
  // Counts name a card by its place in the line the response answered.
  const drops=Object.entries(facetData?.without||{}).filter(([,n])=>n).map(([at,n])=>{const ref=facetCards[Number(at)],t=ref==='typing'?ref:tokenOf(ref);
    return t&&{key:ref,n,text:t==='typing'?(draft?conditionText(t):''):conditionText(t)};}).filter(d=>d?.text);
  $('#empty-elsewhere').innerHTML=drops.map(d=>`<button type="button" data-drop="${esc(d.key)}">${esc(ui.dropCondition.replace('{condition}',d.text))} <span class="muted">→ ${fmt(d.n)}</span></button>`).join('');
}
// A field the current table lacks still reads by its own name and values, so a card it left out stays legible.
const fieldLabel=base=>groups.find(g=>g.key===base)?.label||(base==='type'?'类型':fieldNames[base.replace(/^f_/,'')])||base;
function valueLabel(base,v){
  const option=groups.find(g=>g.key===base)?.options.find(o=>o.value===v);if(option)return option.label;
  return base==='package'?packageName(v):base==='source'?sourceName(v):base==='type'?typeName(v):base==='ruleset'?rulesetShort(v):base.startsWith('f_')?tokenText(base.slice(2),v):v;
}
// Reads a card, or the draft (`typing`), as the bar shows it.
function conditionText(t){
  if(t==='typing')return `${ui.nameField}：${draft}`;
  const {key,values}=t,negated=key.startsWith(NOT),base=baseKey(key);
  if(key==='conflict')return fieldNames.conflict;
  if(TERM_KEYS.has(key))return `${base==='q'?ui.nameField:ui.bodyField}${negated?ui.lacks:''}：${values[0]}`;
  if(key==='cr_min'||key==='cr_max')return `${fieldNames[key]}：${values[0]}`;
  const labels=values.map(v=>valueLabel(base,v));
  return `${fieldLabel(base)}${negated?ui.isNot:''}：${labels.length>3?labels.slice(0,2).join('、')+` 等 ${labels.length} 项`:labels.join('、')}`;
}
$('#elsewhere').addEventListener('click',()=>{
  $('#filter-panel').hidden=false;$('#toggle-filters').setAttribute('aria-expanded','true');
  const box=$('#type-filters');box.scrollIntoView({block:'nearest'});box.querySelector('input')?.focus();saveUiState();
});
$('#empty-elsewhere').addEventListener('click',e=>{
  const b=e.target.closest('[data-drop]');if(!b)return;const key=b.dataset.drop;
  if(key==='typing'){clearDraft();commit();return;}
  const t=tokenOf(key);if(t)dropToken(t);
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
    readLine(location.search);
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
    // Category views became type filters; an old view link opens on all entries with its other conditions, and its
    // single attribute becomes a card at the end of the line.
    if(params.has('field')&&params.has('value')){
      const f=params.get('field');let v=params.get('value');if(f==='spell_level'&&LEVELS.includes(v))v=String(LEVELS.indexOf(v));
      const end=caret===line.length;line.push(card('f_'+f,[v]));settleJoins();if(end)caret=line.length;params.delete('field');params.delete('value');
    }
    record();
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
