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
function setReaderFull(on,{save=true}={}){readerFull=on;applyReaderLayout();markTableFrames();if(save)saveUiState();}
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
  const id=lastResultEntry||currentReaderId;
  const buttons=[...$('#results').querySelectorAll('[data-entry]')];
  const button=buttons.find(b=>b.dataset.entry===id&&b.classList.contains('entry-name'))||buttons.find(b=>b.dataset.entry===id);
  (button||$('#q')).focus({preventScroll:true});
}
async function stepEntry(delta){
  const index=(currentData?.rows||[]).findIndex(r=>rowHas(r,currentReaderId));if(index<0)return;
  if(delta>0&&index===currentData.rows.length-1&&!(await loadMore()))return;
  const next=currentData?.rows[index+delta];if(next){openEntry(next.id,true,'body',true);revealResultRow(next.id);}
}
function renderDetail(tab='body') {
  const e=currentEntry;if(!e)return;
  // Reading mode keeps the entry itself; audit tabs fold into one summary that opens audit mode.
  if(AUDIT_TABS.has(tab)&&!auditMode)tab='body';if(tab!==detailTab)++chainGeneration;detailTab=tab;
  const linkLabels=annaLinkLabels[document.documentElement.lang.startsWith('en') ? 'en' : 'zh'];
  const counts={facts:e.facts.length,discoveries:(e.concept_observations||[]).length,references:(e.effective_references||[]).length,reviews:e.reviews.length};
  const auditNames={facts:ui.facts,discoveries:linkLabels.discoveries,references:linkLabels.effectiveReferences,reviews:ui.reviews};
  const members=e.versions?.members||[];
  // Reading tabs come first; audit tabs follow under their own group label, and a tab with nothing in it fades.
  const readingTabs=[['body',ui.body],['fields',ui.fields],...(members.length>1?[['versions',ui.versionsTab,members.length]]:[]),['relations',ui.relations,e.relations.length],['chain',annaKnowledge.labels.title]];
  const auditTabs=auditMode?Object.entries(counts).map(([id,n])=>[id,auditNames[id],n]):[];
  const tabButton=([id,name,count])=>`<button role="tab" id="tab-${id}" aria-controls="detail-panel" aria-selected="${tab===id}" tabindex="${tab===id?0:-1}" class="${tab===id?'active':''} ${count===0?'empty':''}" data-tab="${id}">${esc(count===undefined?name:`${name} ${count}`)}</button>`;
  const auditSummary=auditMode?'':`<button type="button" class="audit-summary" data-audit-summary title="${esc(ui.auditSummaryHint)}">${esc(ui.auditSummary)}：${Object.entries(counts).map(([k,n])=>`${esc(auditNames[k])} ${n}`).join(' · ')}</button>`;
  let content='';
  if(tab==='body'){content=renderBody(e);loadLayout(e);}
  // Attributes follow the type's table columns; fields naming the version (type, book, rules) come last.
  if(tab==='fields') content=`<dl class="detail-fields">${(e.field_order||Object.keys(e.fields)).map(k=>`<dt>${esc(fieldNames[k]||k)}</dt><dd>${esc(fieldText(e,k))}${e.field_conflicts.includes(k)?' '+badge('冲突','amber'):''}</dd>`).join('')}</dl>`;
  if(tab==='facts') content=e.facts.map(f=>`<article class="fact"><div class="fact-head"><strong>${esc(fieldNames[f.field]||f.field)}</strong>${badge(f.status,f.status==='原文明示'?'green':'amber')}</div><div class="fact-value">${esc(displayValue(f.field,f.value))}</div><small>规则：${esc(f.rule)} · 作用域：${esc(f.scope)}</small>${f.excerpt?`<blockquote>${esc(f.excerpt)}</blockquote>`:''}${f.evidence.path?`<a href="${esc(sourceURL(f.evidence.path,...(f.evidence.span||[0,0])))}" target="_blank" rel="noopener">${esc(f.evidence.path)} ↗</a>`:`<details><summary class="muted">定位依据</summary><pre class="body-text">${esc(JSON.stringify(f.evidence,null,2))}</pre></details>`}</article>`).join('');
  if(tab==='facts'&&!content)content=`<p class="muted">${esc(ui.noEvidence)}</p>`;
  if(tab==='chain')content=annaKnowledge.render(e,auditMode);
  if(tab==='versions'&&members.length>1) content=renderVersions(e);
  if(tab==='relations') content=e.relations.length?e.relations.map(r=>`<div class="related"><span class="muted">${esc(relationNames[r.predicate]||r.predicate)}</span>${r.object_id?`<button data-entry="${esc(r.object_id)}">${esc(r.target_label)}</button>`:`<span>${esc(r.target_label)} ${badge('目标未关联','amber')}</span>`}${badge(r.status)}</div>`).join(''):'<p class="muted">未提取到关系；不代表不存在。</p>';
  if(tab==='discoveries') content=(e.concept_observations||[]).map(o=>renderConceptObservation(o,linkLabels)).join('')||`<p class="muted">${esc(linkLabels.emptyDiscoveries)}</p>`;
  if(tab==='references') content=(metadata.review_snapshot?.pending_rebuild?`<p>${badge(linkLabels.pendingReview,'amber')}</p>`:'')+`<p class="muted">${esc(linkLabels.referencePolicy)}</p>`+(e.effective_references||[]).map(o=>renderConceptObservation(o,linkLabels)).join('');
  if(tab==='reviews') content=`<p class="muted">仅展示关联此条目或所在页面的问题。没有待核记录不代表已经核实。</p>`+e.reviews.map(r=>renderReview(r,linkLabels)).join('');
  // One compact head: name, then what and where it is, then its versions and outside links. The tabs stay
  // pinned while the text scrolls, and the toolbar takes over the name once the head has scrolled away.
  const badges=`${auditMode?badge(e.classification_status,e.classification_status==='原文明示'?'green':'amber'):''}${auditMode||e.legacy!=='unmarked'?badge(legacy[e.legacy]):''}${e.field_conflicts.length?badge(`${e.field_conflicts.length} 个字段冲突`,'amber'):''}`;
  const versions=members.length>1?`<div class="reader-versions" role="group" aria-label="${esc(ui.versionsTab)}">${members.map(m=>`<button type="button" class="version-pill ${m.ruleset==='待核'?'unverified':''}" data-entry="${esc(m.id)}" data-keep-tab ${m.id===e.id?'aria-current="true"':''} title="${esc(versionTitle(m))}">${esc(versionLabel(m))}</button>`).join('')}</div>`:'';
  const links=`<div class="detail-links">${metadata.reader_url?`<a class="book-definition" target="_blank" rel="noopener" href="${esc(metadata.reader_url+'?entry='+encodeURIComponent(e.id))}"><img src="/stella-logo.png" width="18" height="18" alt="">${esc(linkLabels.bookDefinition)} ↗</a>`:''}<a target="_blank" rel="noopener" href="${esc(sourceURL(e.page_path,e.start,e.end))}">${esc(linkLabels.extractedRange)} ↗</a></div>`;
  const name=e.name_zh||e.name;
  $('#detail-content').innerHTML=`<header class="reader-head"><div class="reader-heading"><h2 id="entry-title">${esc(name)}</h2>${e.name_en?`<span class="english">${esc(e.name_en)}</span>`:''}</div><div class="reader-meta"><span class="reader-type">${esc(e.type_label)}</span><span class="reader-ruleset">${esc(rulesetFull(e.ruleset))}</span><span class="reader-source">${esc(e.source_ids.map(sourceFull).join('；'))}</span></div><div class="reader-row">${versions}${links}</div>${badges?`<div class="badges">${badges}</div>`:''}</header><div class="detail-tabs" role="tablist" aria-label="${esc(ui.detailTabs)}">${readingTabs.map(tabButton).join('')}${auditTabs.length?`<span class="tab-group" aria-hidden="true">${esc(ui.auditSummary)}</span>${auditTabs.map(tabButton).join('')}`:''}</div><section id="detail-panel" role="tabpanel" tabindex="0" aria-labelledby="tab-${tab}">${content}</section>${auditSummary}<div class="footnote audit-only">${esc(e.id)} · 自动版本候选</div><p class="reader-hint">${esc(readerHint())}</p>`;
  $('#reader-name').textContent=name;updatePageChrome();watchReaderTitle();markTableFrames();
  $('#detail').setAttribute('aria-labelledby','entry-title');
}
// The entry and its source layout load together, and the text appears once, already in the book's layout;
// a layout slower than this still follows, replacing the extracted text that stands in for it.
const LAYOUT_WAIT=400;
const layoutOf=id=>api('/api/entry-layout?id='+encodeURIComponent(id)).catch(()=>({status:'error'}));
async function openEntry(id,update=true,tab='body',fromResult=false) {
  const gen=++detailGeneration,content=$('#detail-content');
  if(fromResult&&!currentReaderId)lastResultEntry=id;
  if(update){if(id!==currentReaderId){params.delete('chain_spell');params.delete('chain_path');params.delete('chain_effect');}params.set('entry',id);if(tab==='chain')params.set('tab','chain');else params.delete('tab');historyUpdate();}
  if(fromResult)lastResultEntry=id;
  const wasOpen=$('#detail').open;
  currentReaderId=id;currentEntry=null;detailTab=tab;updatePageChrome();
  if(!wasOpen)placeReader();
  $('#detail').setAttribute('aria-labelledby','reader-title');$('#detail').classList.remove('title-away');readerTitleObserver?.disconnect();
  content.setAttribute('aria-busy','true');
  // Stepping keeps the current entry in place until the next one is ready, instead of flashing a loading line;
  // a reader that was closed starts empty rather than showing the entry it last held.
  if(!wasOpen||!content.querySelector('.reader-head')){content.innerHTML=`<p class="reader-message" role="status">${esc(ui.loading)}</p>`;content.scrollTop=0;}
  updateReaderNavigation();
  const layout=layoutOf(id);
  try {
    const e=await api('/api/entry?id='+encodeURIComponent(id)+(params.has('chain_spell')?'&chain_spell='+encodeURIComponent(params.get('chain_spell')):'')+(params.has('chain_path')?'&chain_path='+encodeURIComponent(params.get('chain_path')):'')+(params.has('chain_effect')?'&chain_effect='+encodeURIComponent(params.get('chain_effect')):''));if(gen!==detailGeneration)return;
    e.layoutRequest=layout;e.layout=await Promise.race([layout,new Promise(done=>setTimeout(done,LAYOUT_WAIT,null))]);
    if(gen!==detailGeneration)return;
    currentEntry=e;renderDetail(tab);content.scrollTop=0;if(tab==='chain'&&e.rule_chain?.reading?.effect_id)focusChainLocation(e.rule_chain);
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
  if(!l||l.status!=='ok'){
    const notice=l?`<p class="layout-notice muted">${esc(ui.layoutUnavailable[l.status]||ui.layoutUnavailable.error)}</p>`:'';
    return `${notice}<div class="body-text" ${l?'':'aria-busy="true"'}>${esc(e.body)}</div>`;
  }
  const notes=l.notes.map(n=>{
    const label=`${fieldNames[n.field]||n.field}：${fieldText(e,n.field)} · ${n.status}${n.located?'':' · '+ui.noteElsewhere}`;
    const inner=`<span class="note-field">${esc(fieldNames[n.field]||n.field)}</span><span class="note-value">${esc(fieldText(e,n.field))}</span>`;
    return `<li>${n.located?`<button type="button" class="field-note ${n.status==='原文明示'?'':'inferred'}" data-note="${esc(n.field)}" title="${esc(label)}">${inner}</button>`:`<span class="field-note unlocated" title="${esc(label)}">${inner}</span>`}</li>`;
  }).join('');
  return `<div class="layout-reading${notes?' with-notes':''}">${notes?`<ul class="field-notes" aria-label="${esc(ui.fieldNotes)}">${notes}</ul>`:''}<div class="layout-text">${framedTables(l.html)}</div></div>`;
}
// Each source table scrolls inside its own frame, so a wide table never drags the prose sideways.
// A frame whose table is wider than the reader offers full-width reading.
function framedTables(html){
  const template=document.createElement('template');template.innerHTML=html;
  for(const table of template.content.querySelectorAll('table')){
    if(table.parentElement?.closest('table'))continue;
    const frame=document.createElement('div');frame.className='table-frame';
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
    if(currentEntry===e&&detailTab==='body'){const top=$('#detail-content').scrollTop;$('#detail-panel').innerHTML=renderBody(e);$('#detail-content').scrollTop=top;markTableFrames();}
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
  const head=`<thead><tr><th scope="col">${esc(ui.fields)}</th>${members.map(m=>`<th scope="col"><button type="button" class="link-button" data-entry="${esc(m.id)}" data-keep-tab ${m.id===e.id?'aria-current="true"':''}>${esc(versionLabel(m))}</button>${m.id===e.id?` <span class="muted">${esc(ui.currentVersion)}</span>`:''}</th>`).join('')}</tr></thead>`;
  const rows=list=>list.map(f=>`<tr class="${f.differs?'differs':''}"><th scope="row">${esc(fieldNames[f.field]||f.field)}</th>${members.map(m=>`<td>${esc(fieldText(m,f.field))}</td>`).join('')}</tr>`).join('');
  const differing=fields.filter(f=>f.differs),same=fields.filter(f=>!f.differs);
  return `<p class="muted versions-intro">${esc(ui.versionsIntro)}</p>
    <h3 class="versions-heading">${esc(ui.differingFields)} ${differing.length}</h3>
    ${differing.length?`<div class="version-table-wrap"><table class="version-table">${head}<tbody>${rows(differing)}</tbody></table></div>`:''}
    ${same.length?`<details class="versions-same"><summary>${esc(ui.sameFields.replace('{count}',same.length))}</summary><div class="version-table-wrap"><table class="version-table">${head}<tbody>${rows(same)}</tbody></table></div></details>`:''}`;
}
function closeEntry({update=true,restoreFocus=true}={}) {
  detailGeneration++;
  if(restoreFocus)lastResultEntry=lastResultEntry||currentReaderId;
  holdResultRow(currentReaderId,()=>{
    if($('#detail').open)$('#detail').close();
    document.body.classList.remove('detail-open','detail-modal');
    if(readerFull)setReaderFull(false,{save:false});
  });
  endTopic(currentReaderId);
  currentReaderId=null;currentEntry=null;detailTab='body';updatePageChrome();readerTitleObserver?.disconnect();$('#detail').classList.remove('title-away');
  if(restoreFocus)focusResult();
  updateReaderNavigation();
  if(update&&params.has('entry')){params.delete('entry');params.delete('tab');params.delete('chain_spell');params.delete('chain_path');params.delete('chain_effect');historyUpdate(true);}
  saveUiState();
}


function renderConceptObservation(o,labels) {
  const title=o.target_id
    ? `<button data-entry="${esc(o.target_id)}">${esc(o.text)}</button>`
    : `<strong>${esc(o.text)}</strong>`;
  const role=labels[o.role==='definition'?'role_definition':o.role]||o.role;
  const candidates=!o.target_id && o.candidates.length
    ? `<details><summary>${esc(labels.candidates)} (${o.candidates.length})</summary>${o.candidate_entries.map(c=>`<button data-entry="${esc(c.id)}">${esc(c.name)} · ${esc(c.ruleset)}</button>`).join(' ')}</details>`
    : '';
  return `<article class="fact">
    <div class="fact-head">${title}${badge(labels[o.status]||o.status)}</div>
    <div><small>${esc(labels[o.signal]||o.signal)} · ${esc(role)} · ${esc(labels.reasons[o.reason]||o.reason)}</small></div>
    <div><a target="_blank" rel="noopener" href="${esc(sourceURL(o.path,o.start,o.end))}">${esc(labels.extractedRange)} ↗</a></div>
    ${o.automatic?`<div><small>${esc(labels.reviewStates[o.review_status]||o.review_status)} · ${esc(labels.presentations[o.presentation]||o.presentation)}</small></div><div><code>${esc(o.id)}</code></div><details><summary>${esc(labels.automaticResult)}</summary><pre class="body-text">${esc(JSON.stringify({automatic:o.automatic,decision_ids:o.decision_ids},null,2))}</pre></details>`:''}
    ${candidates}
    ${o.automatic?`<p><a href="/review?reference=${encodeURIComponent(o.id)}" target="_blank" rel="noopener">${esc(labels.reviewSearch.candidates)} ↗</a></p>`:''}
    <details><summary>${esc(labels.evidence)}</summary><pre class="body-text">${esc(JSON.stringify(o.evidence,null,2))}</pre></details>
  </article>`;
}

function renderReview(record, labels) {
  const result=record.detail, event=result?.event, changes=result?.changes||[];
  const title=labels.reasons[record.reason]||reasonNames[record.reason]||record.reason;
  if(!event)return `<article class="fact"><strong>${esc(title)}</strong><pre class="body-text">${esc(pretty(result))}</pre></article>`;
  const l=labels.reviewDiff;
  return `<article class="fact"><strong>${esc(title)}</strong>
    <blockquote>${esc(event.binding?.before||'')}<mark>${esc(event.binding?.text||'')}</mark>${esc(event.binding?.after||'')}</blockquote>
    <p>${esc(event.reason)}</p>${event.operation==='missing_definition'?`<p><a href="/review?reference=${encodeURIComponent(event.binding.reference_id)}" target="_blank" rel="noopener">${esc(labels.reviewSearch.missing)} ↗</a></p>`:''}<small>${esc(event.reviewer)} · ${esc(event.created_at)}</small>
    <h3>${esc(l.title)} (${changes.length})</h3>
    ${changes.map(c=>`<section class="fact"><strong>${esc(l.fields[c.path[0]]||c.path[0])}</strong>
      <div><code>${esc(c.path.slice(1).join(' / '))}</code> · ${esc(l[c.kind]||c.kind)}</div>
      ${c.reason?`<p>${esc(l[c.reason]||c.reason)}</p>`:''}
      <details><summary>${esc(l.values)}</summary><dl><dt>${esc(l.before)}</dt><dd><pre class="body-text">${esc(pretty(c.before))}</pre></dd><dt>${esc(l.after)}</dt><dd><pre class="body-text">${esc(pretty(c.after))}</pre></dd></dl></details>
    </section>`).join('')}
    <details><summary>${esc(labels.evidence)}</summary><pre class="body-text">${esc(pretty(result))}</pre></details>
  </article>`;
}

let chainGeneration=0;
async function inspectChainSpell(id,from){
  const chain=currentEntry?.rule_chain;if(!chain)return;
  const path=annaKnowledge.pathTo(chain,id,from);
  if(chain.options.some(o=>o.spell_ids.includes(id)))return navigateChain(path,id);
  if(path)return navigateChain(path);
}
function focusChainLocation(chain,{scroll=true}={}){
  const r=chain.reading;
  const target=r.effect_id?document.querySelector(`.knowledge-record[data-subject="${CSS.escape(r.focus_id)}"] [data-effect="${CSS.escape(r.effect_id)}"]`):$('.chain-path [aria-current="step"]');
  target?.focus({preventScroll:true});if(scroll)target?.scrollIntoView({block:r.effect_id?'start':'nearest'});
  return !!target;
}
async function navigateChain(path,spell=currentEntry?.rule_chain?.selected_spell_id,effectId=null){
  const entry=currentEntry,gen=++chainGeneration;if(!entry||detailTab!=='chain')return;
  if(path&&entry.rule_chain.options.some(o=>o.spell_ids.includes(path.at(-1))))spell=path.at(-1);
  const panel=$('#detail-panel');panel.setAttribute('aria-busy','true');
  const active=()=>gen===chainGeneration&&currentEntry===entry&&detailTab==='chain';
  try{
    const query=new URLSearchParams({id:entry.id});
    if(spell)query.set('spell',spell);
    if(path)query.set('path',JSON.stringify(path));
    if(effectId)query.set('effect',effectId);
    const chain=await api('/api/knowledge-chain?'+query);
    if(!active())return;
    entry.rule_chain=chain;
    if(chain.selected_spell_id)params.set('chain_spell',chain.selected_spell_id);else params.delete('chain_spell');
    params.set('chain_path',JSON.stringify(chain.reading.path));params.set('tab','chain');
    if(chain.reading.effect_id)params.set('chain_effect',chain.reading.effect_id);else params.delete('chain_effect');
    historyUpdate();renderDetail('chain');
    focusChainLocation(chain);saveUiState();
  }catch(error){if(active()){const message=document.createElement('p');message.setAttribute('role','alert');message.textContent=annaKnowledge.labels.error+': '+error.message;panel.append(message);}}
  finally{if(gen===chainGeneration&&currentEntry===entry)$('#detail-panel')?.removeAttribute('aria-busy');}
}
