let currentReaderId=null, detailTab='body';
const wideReader=window.matchMedia('(min-width: 1200px)');
// Below 761px the reader already fills the screen, so full-width reading has nothing to add.
const widenableReader=window.matchMedia('(min-width: 761px)');
// Touch-only devices have neither Esc nor F6, so their hint names only the on-screen arrows.
const touchOnly=window.matchMedia('(hover: none) and (pointer: coarse)');
const readerHint=()=>touchOnly.matches?ui.readerHintTouch:wideReader.matches?ui.readerHint:ui.readerHintNarrow;
// The row being read is the reader's anchor in the results: whatever the reader does to the workspace, that
// row stays where the pointer left it. The layout keeps rows and the lines above them at one height, so the
// correction is normally zero; it covers what CSS cannot promise, such as an open filter panel reflowing.
const resultRow=id=>id?$('#results').querySelector(`[data-entry="${CSS.escape(id)}"]`)?.closest('tr'):null;
function holdResultRow(id,change){
  const row=resultRow(id),before=row?.getBoundingClientRect().top;
  change();
  if(!row?.isConnected)return;
  const shift=row.getBoundingClientRect().top-before;if(Math.abs(shift)>=1)$('.table-wrap').scrollTop+=shift;
}
// Stepping through the results from the reader keeps the current row in sight, below the pinned header.
function revealResultRow(id){
  const row=resultRow(id);if(!row)return;
  const wrap=$('.table-wrap'),view=wrap.getBoundingClientRect(),box=row.getBoundingClientRect(),head=$('#columns').getBoundingClientRect().height;
  if(box.top<view.top+head)wrap.scrollTop-=view.top+head-box.top;else if(box.bottom>view.bottom)wrap.scrollTop+=box.bottom-view.bottom;
}
function placeReader(){
  const dialog=$('#detail'),active=document.activeElement,top=$('#detail-content').scrollTop;
  holdResultRow(currentReaderId||lastResultEntry,()=>{
    if(dialog.open)dialog.close();
    document.body.classList.toggle('detail-open',!!currentReaderId);
    document.body.classList.toggle('detail-modal',!!currentReaderId&&!wideReader.matches);
    if(!currentReaderId)return;
    wideReader.matches?dialog.show():dialog.showModal();
    dialog.setAttribute('aria-modal',String(!wideReader.matches));
    applyReaderLayout();
  });
  if(!currentReaderId)return;
  const hint=$('#detail-content .reader-hint');if(hint)hint.textContent=readerHint();
  if(active&&dialog.contains(active))active.focus({preventScroll:true});
  $('#detail-content').scrollTop=top;
}
wideReader.addEventListener('change',()=>{if(currentReaderId)placeReader();});
// The reader's share of the workspace is a per-browser preference like density and theme.
// Full-width reading, which sets the results aside entirely, belongs to one reading session: it lasts until
// the reader closes, and this tab's history restores it.
const READER_SHARE_KEY='anna.readerShare';
const DEFAULT_READER_SHARE=.6, MIN_READER_WIDTH=400, MIN_LIST_WIDTH=360;
const storedReaderShare=()=>{const v=Number(annaKit.stored.get(READER_SHARE_KEY));return v>0&&v<1?v:DEFAULT_READER_SHARE;};
let readerShare=storedReaderShare(), readerFull=false;
// The reader and the results share the whole workspace.
function sharedBox(){
  const box=$('.workspace').getBoundingClientRect();
  return {left:box.left,right:box.right,width:box.width||innerWidth};
}
// The same bounds as the grid track in style.css (--anna-reader-min, --anna-list-min).
function readerShareBounds(){
  const {width}=sharedBox();
  return [Math.min(MIN_READER_WIDTH/width,.5),Math.max(1-MIN_LIST_WIDTH/width,.5)];
}
function applyReaderLayout(){
  const [min,max]=readerShareBounds(),share=Math.min(max,Math.max(min,readerShare)),percent=Math.round(share*100);
  $('.workspace').style.setProperty('--anna-reader-share',String(readerShare));
  document.body.classList.toggle('reader-full',readerFull);
  $('#reader-full').setAttribute('aria-pressed',String(readerFull));
  const separator=$('#reader-resizer');
  separator.setAttribute('aria-valuemin',String(Math.round(min*100)));separator.setAttribute('aria-valuemax',String(Math.round(max*100)));
  separator.setAttribute('aria-valuenow',String(percent));separator.setAttribute('aria-valuetext',ui.readerShare.replace('{percent}',percent));
}
function setReaderShare(share,save=true){
  const [min,max]=readerShareBounds();readerShare=Math.min(max,Math.max(min,share));applyReaderLayout();
  if(save)annaKit.stored.set(READER_SHARE_KEY,Math.abs(readerShare-DEFAULT_READER_SHARE)<.005?null:readerShare.toFixed(3));
}
// Widening, the reader's edges move out over the results; narrowing, they move back and uncover the results where
// they were. Either way the text is set again at every width on the way.
function setReaderFull(on,{save=true}={}){
  const reader=$('#detail'),shown=reader.open,before=reader.getBoundingClientRect();
  readerFull=on;applyReaderLayout();markTableFrames();if(save)saveUiState();
  if(shown)annaMotion.reshape(reader,before);
}
// Runs from app.js once the shared helpers and labels exist.
function initReaderLayout(){
  const separator=$('#reader-resizer');
  separator.title=ui.resizeReaderHint;separator.setAttribute('aria-label',ui.resizeReader);
  const shareAt=x=>{const box=sharedBox();return (box.right-x)/box.width;};
  separator.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();separator.setPointerCapture(e.pointerId);document.body.classList.add('reader-resizing');});
  separator.addEventListener('pointermove',e=>{if(separator.hasPointerCapture(e.pointerId))setReaderShare(shareAt(e.clientX),false);});
  separator.addEventListener('lostpointercapture',()=>{document.body.classList.remove('reader-resizing');setReaderShare(readerShare);});
  separator.addEventListener('dblclick',()=>setReaderShare(DEFAULT_READER_SHARE));
  // The separator moves like the boundary it is: left widens the reader, Home and End reach its bounds.
  separator.addEventListener('keydown',e=>{
    const step=e.shiftKey?.1:.02,[min,max]=readerShareBounds();
    const next={ArrowLeft:readerShare+step,ArrowRight:readerShare-step,Home:min,End:max}[e.key];
    if(next===undefined)return;e.preventDefault();setReaderShare(next);
  });
  $('#reader-full').addEventListener('click',()=>setReaderFull(!readerFull));
  window.addEventListener('resize',()=>{if(currentReaderId)applyReaderLayout();});
  new ResizeObserver(markTableFrames).observe($('#detail-content'));
  annaKit.stored.watch(READER_SHARE_KEY,()=>{readerShare=storedReaderShare();applyReaderLayout();});
  applyReaderLayout();
}
// A stacked row contains every listed version; reading any of them places the reader on that row.
const rowHas=(row,id)=>row.id===id||!!row.versions?.some(v=>v.id===id);
function updateReaderNavigation(){
  const rows=currentData?.rows||[],index=rows.findIndex(r=>rowHas(r,currentReaderId));
  const busy=$('#results').getAttribute('aria-busy')==='true';
  // The next arrow continues past the loaded rows while more results remain.
  $('#entry-prev').disabled=busy||index<=0;$('#entry-next').disabled=busy||index<0||(index>=rows.length-1&&!hasMore());
  $('#detail-position').textContent=currentReaderId?(index<0?ui.outsideResults:ui.listPosition.replace('{current}',fmt(index+1)).replace('{total}',fmt(currentData.total))):'';
  for(const tr of $('#results').querySelectorAll('tr')){
    const buttons=[...tr.querySelectorAll('[data-entry]')];
    tr.classList.toggle('row-selected',buttons.some(b=>b.dataset.entry===currentReaderId));
    for(const b of buttons){
      const active=b.dataset.entry===currentReaderId;
      if(active)b.setAttribute('aria-current','true');else b.removeAttribute('aria-current');
      if(b.classList.contains('entry-name')){b.setAttribute('aria-controls','detail');b.setAttribute('aria-expanded',String(buttons.some(x=>x.dataset.entry===currentReaderId)));}
    }
  }
}
function focusResult(){
  const id=currentReaderId||lastResultEntry;
  const buttons=[...$('#results').querySelectorAll('[data-entry]')];
  const button=buttons.find(b=>b.dataset.entry===id&&b.classList.contains('entry-name'))||buttons.find(b=>b.dataset.entry===id);
  (button||$('#q')).focus({preventScroll:true});
}
async function stepEntry(delta){
  const index=(currentData?.rows||[]).findIndex(r=>rowHas(r,currentReaderId));if(index<0)return;
  if(delta>0&&index===currentData.rows.length-1&&!(await loadMore()))return;
  // The next entry comes up from below, as the rows run; the previous one comes down from above.
  const next=currentData?.rows[index+delta];if(next){openEntry(next.id,true,'body',true,delta>0?'below':'above');revealResultRow(next.id);}
}
// An entry's book is its own collection's: the default book reads anna's extraction, the others their chapters.
const nativeEntry=e=>(e?.collection||metadata.collection||'5echm')===(metadata.default_collection||'5echm');
// Stella opens an entry where its collection's book defines it: every entry of the default book, another
// collection's where it has a place in that collection's chapters.
function bookHref(e){
  const collection=e?.collection||metadata.collection;
  if(!metadata.collections?.find(c=>c.id===collection)?.reader)return '';
  const loc=e.reader_location||e.provenance?.reader_location;
  if(!nativeEntry(e)&&!(loc&&loc.locatable!==false&&(loc.path||loc.page)))return '';
  return '/read?'+new URLSearchParams({entry:collection+':'+e.id});
}
function safeExternalHref(raw){
  if(typeof raw!=='string')return '';
  const s=raw.trim();
  return /^https?:\/\//i.test(s)?s:'';
}
function formatRating(r){
  if(r===null||r===undefined||r==='')return '';
  if(typeof r==='object'){
    const bits=[];
    if(r.tier)bits.push(String(r.tier));
    if(r.score!==undefined&&r.score!==null)bits.push(String(r.score));
    if(r.rank!==undefined&&r.rank!==null)bits.push('#'+r.rank);
    const head=bits.join(' · ');
    return r.list_title?`${head}${head?' — ':''}${r.list_title}`:head;
  }
  return String(r);
}
function renderProvenance(e){
  const p=e.provenance;
  if(!p||typeof p!=='object')return '';
  const originTone=p.origin==='官方'||p.origin==='本站精选'?'green':'gray';
  const headBadges=[
    p.origin?badge(p.origin,originTone):'',
    p.ours?badge(ui.provOurs,'green'):'',
    p.tier?badge(`${ui.provTier} ${p.tier}`,'amber'):''
  ].filter(Boolean).join('');
  const rows=[];
  if(p.collection)rows.push([ui.provCollection,esc(p.collection)]);
  if(p.package_name)rows.push([ui.provPackage,esc(p.package_name)+(p.ours?` <span class="badge green">${esc(ui.provOurs)}</span>`:'')]);
  if(p.source_name||p.source_code){
    const srcText=p.source_name&&p.source_code&&p.source_name!==p.source_code?`${p.source_name}（${p.source_code}）`:(p.source_name||p.source_code);
    rows.push([ui.provSource,esc(srcText)]);
  }
  if(Array.isArray(p.authors)&&p.authors.length)rows.push([ui.provAuthors,esc(p.authors.join('、'))]);
  if(Array.isArray(p.translators)&&p.translators.length)rows.push([ui.provTranslators,esc(p.translators.join('、'))]);
  const ratingText=formatRating(p.rating);
  if(p.tier||ratingText){
    const parts=[p.tier?`${ui.provTier} ${p.tier}`:'',ratingText].filter(Boolean);
    rows.push([ui.provRating,esc(parts.join(' · '))]);
  }
  if(p.copied_from&&p.copied_from.id){
    rows.push([ui.provCopiedFrom,`<button type="button" class="ref" data-entry="${esc(p.copied_from.id)}">${esc(p.copied_from.name||p.copied_from.id)}</button>`]);
  }
  if(p.license_note){
    const safeUrl=safeExternalHref(p.license_note);
    rows.push([ui.provLicense,safeUrl?`<a class="o5e-prov-link" href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer">${esc(ui.provViewLink)}</a>`:esc(p.license_note)]);
  }
  const media=e.layout?.media||p.media_summary;
  if(media&&typeof media==='object'){
    const localCount=(media.local_media||[]).length;
    const remoteCount=(media.remote_media||[]).length;
    const pills=[];
    if(localCount)pills.push(`<span class="badge green">${esc(ui.provMediaLocal.replace('{count}',fmt(localCount)))}</span>`);
    if(remoteCount)pills.push(`<span class="o5e-remote-media">${esc(ui.provMediaRemote.replace('{count}',fmt(remoteCount)))}</span>`);
    if(pills.length)rows.push([ui.provMedia,`<span class="o5e-prov-media">${pills.join(' ')}</span>`]);
  }
  const loc=e.reader_location||p.reader_location,locHref=loc?bookHref(e):'';
  if(locHref){
    rows.push([ui.provReadBook,`<a class="o5e-prov-link" target="_blank" rel="noopener" href="${esc(locHref)}">${esc(loc.title||ui.provReadBook)} ↗</a>`]);
  }
  if(!headBadges&&!rows.length)return '';
  return `<section class="o5e-provenance" aria-label="${esc(ui.provenance)}"><div class="o5e-prov-head"><strong>${esc(ui.provenance)}</strong>${headBadges?`<div class="badges">${headBadges}</div>`:''}</div>${rows.length?`<dl class="o5e-prov-grid">${rows.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>`:''}</section>`;
}
function renderDetail(tab='body') {
  const e=currentEntry;if(!e)return;
  if(!['body','fields','versions','relations'].includes(tab))tab='body';
  detailTab=tab;
  const linkLabels=annaLinkLabels[document.documentElement.lang.startsWith('en') ? 'en' : 'zh'];
  const members=e.versions?.members||[];
  const rels=e.relations||[],conflicts=e.field_conflicts||[],sourceIds=e.source_ids||[];
  const tabs=[['body',ui.body],['fields',ui.fields],...(members.length>1?[['versions',ui.versionsTab,members.length]]:[]),['relations',ui.relations,rels.length]];
  const tabButton=([id,name,count])=>`<button role="tab" id="tab-${id}" aria-controls="detail-panel" aria-selected="${tab===id}" tabindex="${tab===id?0:-1}" class="${tab===id?'active':''} ${count===0?'empty':''}" data-tab="${id}">${esc(count===undefined?name:`${name} ${count}`)}</button>`;
  let content='';
  if(tab==='body'){content=renderBody(e);loadLayout(e);}
  // Attributes follow the type's table columns; fields naming the version (type, book, rules) come last.
  if(tab==='fields') content=`<dl class="detail-fields">${(e.field_order||Object.keys(e.fields||{})).map(k=>`<dt>${esc(fieldNames[k]||k)}</dt><dd>${esc(fieldText(e,k))}${conflicts.includes(k)?' '+badge('冲突','amber'):''}</dd>`).join('')}</dl>`;
  if(tab==='versions'&&members.length>1) content=renderVersions(e);
  if(tab==='relations') content=rels.length?rels.map(r=>`<div class="related"><span class="muted">${esc(relationNames[r.predicate]||r.predicate)}</span>${r.object_id?`<button data-entry="${esc(r.object_id)}">${esc(r.target_label)}</button>`:`<span>${esc(r.target_label)} ${badge('目标未关联','amber')}</span>`}${badge(r.status)}</div>`).join(''):'<p class="muted">未提取到关系；不代表不存在。</p>';
  // One compact head: name, then what and where it is, then its versions and outside links. The tabs stay
  // pinned while the text scrolls, and the toolbar takes over the name once the head has scrolled away.
  const badges=`${e.legacy&&e.legacy!=='unmarked'&&legacy[e.legacy]?badge(legacy[e.legacy]):''}${conflicts.length?badge(`${conflicts.length} 个字段冲突`,'amber'):''}`;
  const versions=members.length>1?`<div class="reader-versions" role="group" aria-label="${esc(ui.versionsTab)}">${members.map(m=>`<button type="button" class="version-pill ${m.ruleset==='待核'?'unverified':''}" data-entry="${esc(m.id)}" data-keep-tab ${m.id===e.id?'aria-current="true"':''} title="${esc(versionTitle(m))}">${esc(versionLabel(m))}${relationMark(m)}</button>`).join('')}</div>`:'';
  const native=nativeEntry(e),stella=bookHref(e);
  const bookLink=stella?`<a class="book-definition" target="_blank" rel="noopener" href="${esc(stella)}"><img src="/stella-logo.png" width="18" height="18" alt="">${esc(native?linkLabels.bookDefinition:ui.provReadBook)} ↗</a>`:'';
  const rangeLink=native&&metadata.source_text!==false?`<a target="_blank" rel="noopener" href="${esc(sourceURL(e.page_path,e.start,e.end))}">${esc(linkLabels.extractedRange)} ↗</a>`:'';
  const links=bookLink||rangeLink?`<div class="detail-links">${bookLink}${rangeLink}</div>`:'';
  const name=e.name_zh||e.name;
  $('#detail-content').innerHTML=`<header class="reader-head"><div class="reader-heading"><h2 id="entry-title">${esc(name)}</h2>${e.name_en?`<span class="english">${esc(e.name_en)}</span>`:''}</div><div class="reader-meta"><span class="reader-type">${esc(e.type_label)}</span><span class="reader-ruleset">${esc(rulesetFull(e.ruleset))}</span><span class="reader-source">${esc(sourceIds.map(sourceFull).join('；'))}</span></div><div class="reader-row">${versions}${links}</div>${badges?`<div class="badges">${badges}</div>`:''}</header><div class="detail-tabs" role="tablist" aria-label="${esc(ui.detailTabs)}">${tabs.map(tabButton).join('')}</div><section id="detail-panel" role="tabpanel" tabindex="0" aria-labelledby="tab-${tab}">${content}</section><p class="reader-hint">${esc(readerHint())}</p>`;
  $('#reader-name').textContent=name;updatePageChrome();watchReaderTitle();markTableFrames();
  if(tab==='body')markDifferences(e);
  $('#detail').setAttribute('aria-labelledby','entry-title');
}
// A tab to the right of the current one comes in from the right, one to the left from the left.
function switchTab(tab){
  const order=[...$('#detail-content').querySelectorAll('[data-tab]')].map(b=>b.dataset.tab);
  const from=order.indexOf(tab)>order.indexOf(detailTab)?'right':order.indexOf(tab)<order.indexOf(detailTab)?'left':null;
  renderDetail(tab);annaMotion.arrive($('#detail-panel'),from);
  params.delete('tab');historyUpdate(true);$('#tab-'+tab).focus();saveUiState();
}
// The entry and its source layout load together, and the text appears once, already in the book's layout;
// a layout slower than this still follows, replacing the extracted text that stands in for it.
const LAYOUT_WAIT=400;
const layoutOf=id=>api('/api/entry-layout?id='+encodeURIComponent(id)).catch(()=>({status:'error'}));
// `from` is the side the entry comes in from when it replaces another in an open reader; without it, it fades in.
async function openEntry(id,update=true,tab='body',fromResult=false,from=null) {
  const gen=++detailGeneration,content=$('#detail-content');
  if(fromResult&&!currentReaderId)lastResultEntry=id;
  if(update){params.set('entry',id);params.delete('tab');historyUpdate();}
  if(fromResult)lastResultEntry=id;
  const wasOpen=$('#detail').open;
  currentReaderId=id;currentEntry=null;detailTab=tab;updatePageChrome();
  // A wide reader comes in from the right edge and the results' edge moves aside with it (style.css, Motion).
  if(!wasOpen){const list=$('#query-main'),before=list.getBoundingClientRect();placeReader();annaMotion.reshape(list,before);}
  $('#detail').setAttribute('aria-labelledby','reader-title');$('#detail').classList.remove('title-away');readerTitleObserver?.disconnect();
  content.setAttribute('aria-busy','true');
  // Stepping keeps the current entry in place until the next one is ready, instead of flashing a loading line;
  // a reader that was closed starts empty rather than showing the entry it last held.
  if(!wasOpen||!content.querySelector('.reader-head')){content.innerHTML=`<p class="reader-message" role="status">${esc(ui.loading)}</p>`;content.scrollTop=0;}
  updateReaderNavigation();
  const layout=layoutOf(id);
  try {
    const e=await api('/api/entry?id='+encodeURIComponent(id));if(gen!==detailGeneration)return;
    e.layoutRequest=layout;e.layout=await Promise.race([layout,new Promise(done=>setTimeout(done,LAYOUT_WAIT,null))]);
    if(gen!==detailGeneration)return;
    currentEntry=e;renderDetail(tab);content.scrollTop=0;
    // The next entry comes from its side; the first one fades in where the reader waited for it.
    annaMotion.arrive(content,wasOpen?from:null);
  } catch(e) {
    if(gen===detailGeneration){content.innerHTML=`<div class="reader-message" role="alert"><strong>${esc(ui.readError)}</strong><p>${esc(e.message)}</p><button data-retry>${esc(ui.retry)}</button></div>`;content.scrollTop=0;}
  } finally {
    if(gen===detailGeneration){content.setAttribute('aria-busy','false');saveUiState();shareTopic();}
  }
}
// The body shows the book's own layout; anna's readings of it sit beside the text as notes.
// Until the layout arrives, or when the source page is unavailable, the extracted text stands in.
function renderBody(e){
  const l=e.layout;
  const prov=renderProvenance(e);
  if(!l||l.status!=='ok'){
    const notice=l?`<p class="layout-notice muted">${esc(ui.layoutUnavailable[l.status]||ui.layoutUnavailable.error)}</p>`:'';
    return `${notice}<div class="body-text" ${l?'':'aria-busy="true"'}>${esc(e.body||'')}</div>${prov}`;
  }
  const notes=(l.notes||[]).map(n=>{
    const label=`${fieldNames[n.field]||n.field}：${fieldText(e,n.field)} · ${n.status}${n.located?'':' · '+ui.noteElsewhere}`;
    const inner=`<span class="note-field">${esc(fieldNames[n.field]||n.field)}</span><span class="note-value">${esc(fieldText(e,n.field))}</span>`;
    return `<li>${n.located?`<button type="button" class="field-note ${n.status==='原文明示'?'':'inferred'}" data-note="${esc(n.field)}" title="${esc(label)}">${inner}</button>`:`<span class="field-note unlocated" title="${esc(label)}">${inner}</span>`}</li>`;
  }).join('');
  return `${differenceBar(e)}<div class="layout-reading${notes?' with-notes':''}">${notes?`<ul class="field-notes" aria-label="${esc(ui.fieldNotes)}">${notes}</ul>`:''}<div class="layout-text">${framedTables(l.html)}</div></div>${prov}`;
}
// Versions whose text comes from the same collection can be marked in this version's layout: the same
// translation and renderer, so what differs is the rules text, not the wording. A copy opens marked against the
// entry it was copied from.
function differenceTargets(e){
  const here=e.collection||metadata.collection;
  return (e.versions?.members||[]).filter(m=>m.id!==e.id&&(m.collection||here)===here&&m.relation!=='translation');
}
function differenceBar(e){
  const targets=differenceTargets(e);
  if(!targets.length)return '';
  if(e.differenceWith===undefined){const base=e.provenance?.copied_from?.id;e.differenceWith=targets.some(m=>m.id===base)?base:null;}
  const buttons=targets.map(m=>{const on=m.id===e.differenceWith,name=versionLabel(m);
    return `<button type="button" class="difference-toggle" data-difference="${esc(m.id)}" aria-pressed="${on}">${esc((on?ui.diffShown:ui.diffAgainst).replace('{name}',name))}</button>`;}).join('');
  return `<div class="difference-bar">${buttons}<p class="difference-status muted" role="status"></p></div>`;
}
async function markDifferences(e){
  const id=e.differenceWith,text=$('#detail-content .layout-text'),status=$('#detail-content .difference-status');
  const target=differenceTargets(e).find(m=>m.id===id);
  if(!target||!text||!status)return;
  const name=versionLabel(target);
  e.differenceLayouts||={};
  const other=await (e.differenceLayouts[id]||=layoutOf(id));
  if(currentEntry!==e||e.differenceWith!==id||!text.isConnected)return;
  if(other?.status!=='ok'){status.textContent=ui.diffUnavailable.replace('{name}',name);return;}
  const template=document.createElement('template');template.innerHTML=other.html;
  const result=annaDifferences.mark(text,template.content);
  text.classList.toggle('differences-marked',result.status==='marked');
  status.textContent=(result.status==='marked'?ui.diffLegend:result.status==='same'?ui.diffNone:ui.diffTooMany).replace('{name}',name);
}
function toggleDifferences(id){
  const e=currentEntry;if(!e||detailTab!=='body')return;
  e.differenceWith=e.differenceWith===id?null:id;
  const top=$('#detail-content').scrollTop;
  $('#detail-panel').innerHTML=renderBody(e);$('#detail-content').scrollTop=top;markTableFrames();markDifferences(e);
  $('#detail-panel').querySelector(`[data-difference="${CSS.escape(id)}"]`)?.focus();
}
// Each source table scrolls inside its own frame, so a wide table never drags the prose sideways.
// A frame whose table is wider than the reader offers full-width reading.
function framedTables(html){
  const template=document.createElement('template');template.innerHTML=html;
  for(const table of template.content.querySelectorAll('table')){
    if(table.parentElement?.closest('table'))continue;
    const frame=document.createElement('div');
    frame.className=table.classList.contains('o5e-progression')||table.classList.contains('o5e-table')?'table-frame o5e-table-wrap':'table-frame';
    frame.innerHTML=`<button type="button" class="table-wide-hint" data-reader-full hidden>${esc(ui.wideTable)}</button><div class="table-scroll"></div>`;
    table.replaceWith(frame);frame.lastElementChild.append(table);
  }
  return template.innerHTML;
}
function markTableFrames(){
  for(const frame of $('#detail-content').querySelectorAll('.table-frame')){
    const scroll=frame.querySelector('.table-scroll'),wide=scroll.scrollWidth>scroll.clientWidth+1;
    frame.classList.toggle('overflowing',wide);
    frame.querySelector('.table-wide-hint').hidden=!wide||readerFull||!widenableReader.matches;
  }
}
// The toolbar names the entry only after the head carrying the name has scrolled out of view.
let readerTitleObserver;
function watchReaderTitle(){
  readerTitleObserver?.disconnect();
  readerTitleObserver=new IntersectionObserver(([seen])=>$('#detail').classList.toggle('title-away',!seen.isIntersecting),{root:$('#detail-content')});
  readerTitleObserver.observe($('#entry-title'));
}
function loadLayout(e){
  if(e.layout||e.layoutAwaited)return;
  e.layoutAwaited=(e.layoutRequest||layoutOf(e.id)).then(layout=>{
    e.layout=layout;
    if(currentEntry===e&&detailTab==='body'){const top=$('#detail-content').scrollTop;$('#detail-panel').innerHTML=renderBody(e);$('#detail-content').scrollTop=top;markTableFrames();markDifferences(e);annaMotion.arrive($('#detail-panel'));}
  });
}
// A note and the words it was read from light up together; activating a note brings its words into view.
function lightNotes(fields,on){
  for(const el of $('#detail-content').querySelectorAll('mark.note'))el.classList.toggle('lit',on&&el.dataset.notes.split(' ').some(f=>fields.includes(f)));
  for(const el of $('#detail-content').querySelectorAll('.field-note[data-note]'))el.classList.toggle('lit',on&&fields.includes(el.dataset.note));
}
// Versions are compared field by field; differing fields lead, equal or unextracted ones fold away.
function renderVersions(e){
  const {members,fields}=e.versions;
  const head=`<thead><tr><th scope="col">${esc(ui.fields)}</th>${members.map(m=>`<th scope="col"><button type="button" class="link-button" data-entry="${esc(m.id)}" data-keep-tab ${m.id===e.id?'aria-current="true"':''}>${esc(versionLabel(m))}${relationMark(m)}</button>${m.id===e.id?` <span class="muted">${esc(ui.currentVersion)}</span>`:''}</th>`).join('')}</tr></thead>`;
  const rows=list=>list.map(f=>`<tr class="${f.differs?'differs':''}"><th scope="row">${esc(fieldNames[f.field]||f.field)}</th>${members.map(m=>`<td>${esc(fieldText(m,f.field))}</td>`).join('')}</tr>`).join('');
  const differing=fields.filter(f=>f.differs),same=fields.filter(f=>!f.differs);
  return `<p class="muted versions-intro">${esc(e.versions.status==='library_candidate'?ui.versionsIntroLibrary:ui.versionsIntro)}</p>
    <h3 class="versions-heading">${esc(ui.differingFields)} ${differing.length}</h3>
    ${differing.length?`<div class="version-table-wrap"><table class="version-table">${head}<tbody>${rows(differing)}</tbody></table></div>`:''}
    ${same.length?`<details class="versions-same"><summary>${esc(ui.sameFields.replace('{count}',same.length))}</summary><div class="version-table-wrap"><table class="version-table">${head}<tbody>${rows(same)}</tbody></table></div></details>`:''}`;
}
function closeEntry({update=true,restoreFocus=true}={}) {
  detailGeneration++;
  if(restoreFocus)lastResultEntry=lastResultEntry||currentReaderId;
  // A wide reader leaves to the right at the width it had, and the results' edge follows it (style.css, Motion).
  const list=$('#query-main'),before=list.getBoundingClientRect();
  if($('#detail').open)$('#detail').style.setProperty('--anna-leaving-width',$('#detail').getBoundingClientRect().width+'px');
  holdResultRow(currentReaderId,()=>{
    if($('#detail').open)$('#detail').close();
    document.body.classList.remove('detail-open','detail-modal');
    if(readerFull)setReaderFull(false,{save:false});
  });
  annaMotion.reshape(list,before);
  endTopic(currentEntry);
  currentReaderId=null;currentEntry=null;detailTab='body';updatePageChrome();readerTitleObserver?.disconnect();$('#detail').classList.remove('title-away');
  if(restoreFocus)focusResult();
  updateReaderNavigation();
  if(update&&params.has('entry')){params.delete('entry');params.delete('tab');historyUpdate(true);}
  saveUiState();
}
document.addEventListener('error',e=>{
  const img=e.target;
  if(!(img instanceof HTMLImageElement)||!img.hasAttribute('data-o5e-fallback'))return;
  const fig=img.closest('figure.o5e-figure');
  if(!fig)return;
  fig.classList.add('o5e-img-failed','o5e-media-remote');
  const note=fig.querySelector('.o5e-media-fallback-note');
  if(note)note.hidden=false;
},true);
