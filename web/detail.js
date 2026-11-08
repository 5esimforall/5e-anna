let currentReaderId=null, detailTab='body';
const wideReader=window.matchMedia('(min-width: 1200px)');
function placeReader(){
  const dialog=$('#detail'),active=document.activeElement,top=$('#detail-content').scrollTop;
  if(dialog.open)dialog.close();
  document.body.classList.toggle('detail-open',!!currentReaderId);
  document.body.classList.toggle('detail-modal',!!currentReaderId&&!wideReader.matches);
  if(!currentReaderId)return;
  wideReader.matches?dialog.show():dialog.showModal();
  dialog.setAttribute('aria-modal',String(!wideReader.matches));
  $('.detail-footer').textContent=wideReader.matches?ui.readerHint:ui.readerHintNarrow;
  if(active&&dialog.contains(active))active.focus({preventScroll:true});
  $('#detail-content').scrollTop=top;
}
wideReader.addEventListener('change',()=>{if(currentReaderId)placeReader();});
// A stacked row contains every listed version; reading any of them places the reader on that row.
const rowHas=(row,id)=>row.id===id||!!row.versions?.some(v=>v.id===id);
function updateReaderNavigation(){
  const rows=currentData?.rows||[],index=rows.findIndex(r=>rowHas(r,currentReaderId));
  const busy=$('#results').getAttribute('aria-busy')==='true';
  $('#entry-prev').disabled=busy||index<=0;$('#entry-next').disabled=busy||index<0||index>=rows.length-1;
  $('#detail-position').textContent=currentReaderId?(index<0?ui.outsideResults:ui.pagePosition.replace('{current}',index+1).replace('{total}',rows.length)):'';
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
function stepEntry(delta){
  const rows=currentData?.rows||[],index=rows.findIndex(r=>rowHas(r,currentReaderId)),next=rows[index+delta];
  if(index>=0&&next)openEntry(next.id,true,'body',true);
}
function renderDetail(tab='body') {
  const e=currentEntry;if(!e)return;
  // Reading mode keeps the entry itself; audit tabs fold into one summary that opens audit mode.
  if(AUDIT_TABS.has(tab)&&!auditMode)tab='body';detailTab=tab;
  const linkLabels=annaLinkLabels[document.documentElement.lang.startsWith('en') ? 'en' : 'zh'];
  const counts={facts:e.facts.length,discoveries:(e.concept_observations||[]).length,references:(e.effective_references||[]).length,reviews:e.reviews.length};
  const auditNames={facts:ui.facts,discoveries:linkLabels.discoveries,references:linkLabels.effectiveReferences,reviews:ui.reviews};
  const members=e.versions?.members||[];
  const tabs=[['body',ui.body],['fields',ui.fields],...(members.length>1?[['versions',`${ui.versionsTab} ${members.length}`]]:[]),['facts',`${ui.facts} ${counts.facts}`],['relations',`${ui.relations} ${e.relations.length}`],['discoveries',`${linkLabels.discoveries} ${counts.discoveries}`],['references',`${linkLabels.effectiveReferences} ${counts.references}`],['reviews',`${ui.reviews} ${counts.reviews}`]]
    .filter(([id])=>auditMode||!AUDIT_TABS.has(id));
  const auditSummary=auditMode?'':`<button type="button" class="audit-summary" data-audit-summary title="${esc(ui.auditSummaryHint)}">${esc(ui.auditSummary)}：${Object.entries(counts).map(([k,n])=>`${esc(auditNames[k])} ${n}`).join(' · ')}</button>`;
  let content='';
  if(tab==='body') content=`<div class="body-text">${esc(e.body)}</div>`;
  if(tab==='fields') content=`<dl class="detail-fields">${Object.entries(e.fields).map(([k,v])=>`<dt>${esc(fieldNames[k]||k)}</dt><dd>${esc(displayValue(k,v))}${e.field_conflicts.includes(k)?' '+badge('冲突','amber'):''}</dd>`).join('')}</dl>`;
  if(tab==='facts') content=e.facts.map(f=>`<article class="fact"><div class="fact-head"><strong>${esc(fieldNames[f.field]||f.field)}</strong>${badge(f.status,f.status==='原文明示'?'green':'amber')}</div><div class="fact-value">${esc(displayValue(f.field,f.value))}</div><small>规则：${esc(f.rule)} · 作用域：${esc(f.scope)}</small>${f.excerpt?`<blockquote>${esc(f.excerpt)}</blockquote>`:''}${f.evidence.path?`<a href="${esc(sourceURL(f.evidence.path,...(f.evidence.span||[0,0])))}" target="_blank" rel="noopener">${esc(f.evidence.path)} ↗</a>`:`<details><summary class="muted">定位依据</summary><pre class="body-text">${esc(JSON.stringify(f.evidence,null,2))}</pre></details>`}</article>`).join('');
  if(tab==='facts'&&!content)content=`<p class="muted">${esc(ui.noEvidence)}</p>`;
  if(tab==='versions'&&members.length>1) content=renderVersions(e);
  if(tab==='relations') content=e.relations.length?e.relations.map(r=>`<div class="related"><span class="muted">${esc(relationNames[r.predicate]||r.predicate)}</span>${r.object_id?`<button data-entry="${esc(r.object_id)}">${esc(r.target_label)}</button>`:`<span>${esc(r.target_label)} ${badge('目标未关联','amber')}</span>`}${badge(r.status)}</div>`).join(''):'<p class="muted">未提取到关系；不代表不存在。</p>';
  if(tab==='discoveries') content=(e.concept_observations||[]).map(o=>renderConceptObservation(o,linkLabels)).join('')||`<p class="muted">${esc(linkLabels.emptyDiscoveries)}</p>`;
  if(tab==='references') content=(metadata.review_snapshot?.pending_rebuild?`<p>${badge(linkLabels.pendingReview,'amber')}</p>`:'')+`<p class="muted">${esc(linkLabels.referencePolicy)}</p>`+(e.effective_references||[]).map(o=>renderConceptObservation(o,linkLabels)).join('');
  if(tab==='reviews') content=`<p class="muted">仅展示关联此条目或所在页面的问题。没有待核记录不代表已经核实。</p>`+e.reviews.map(r=>renderReview(r,linkLabels)).join('');
  $('#detail-content').innerHTML=`<div class="eyebrow">${esc(e.type_label)} / ${esc(e.ruleset)}</div><h2 id="entry-title">${esc(e.name_zh||e.name)}</h2><div class="english">${esc(e.name_en)}</div>${members.length>1?`<div class="reader-versions" role="group" aria-label="${esc(ui.versionsTab)}">${members.map(m=>`<button type="button" class="version-pill ${m.ruleset==='待核'?'unverified':''}" data-entry="${esc(m.id)}" data-keep-tab ${m.id===e.id?'aria-current="true"':''} title="${esc(m.source_names.join('；')+' · '+m.ruleset)}">${esc(versionLabel(m))}</button>`).join('')}</div>`:''}<div class="detail-meta">${esc(e.source_names.join('；'))}</div><div class="badges">${auditMode?badge(e.classification_status,e.classification_status==='原文明示'?'green':'amber'):''}${auditMode||e.legacy!=='unmarked'?badge(legacy[e.legacy]):''}${e.field_conflicts.length?badge(`${e.field_conflicts.length} 个字段冲突`,'amber'):''}</div><div class="detail-meta detail-links">${metadata.book_url?`<a target="_blank" rel="noopener" href="${esc(metadata.book_url+'/definition/'+encodeURIComponent(e.id)+'?reader=1')}">${esc(linkLabels.bookDefinition)} ↗</a>`:''}<a target="_blank" rel="noopener" href="${esc(sourceURL(e.page_path,e.start,e.end))}">${esc(linkLabels.extractedRange)} ↗</a></div><div class="detail-tabs" role="tablist" aria-label="${esc(ui.detailTabs)}">${tabs.map(([id,name])=>`<button role="tab" id="tab-${id}" aria-controls="detail-panel" aria-selected="${tab===id}" tabindex="${tab===id?0:-1}" class="${tab===id?'active':''}" data-tab="${id}">${esc(name)}</button>`).join('')}</div><section id="detail-panel" role="tabpanel" tabindex="0" aria-labelledby="tab-${tab}">${content}</section>${auditSummary}<div class="footnote audit-only">${esc(e.id)} · 自动版本候选</div>`;
  $('#detail').setAttribute('aria-labelledby','entry-title');
}
async function openEntry(id,update=true,tab='body',fromResult=false) {
  const gen=++detailGeneration;
  if(fromResult&&!currentReaderId)lastResultEntry=id;
  if(update){params.set('entry',id);historyUpdate();}
  if(fromResult)lastResultEntry=id;
  const wasOpen=$('#detail').open;
  currentReaderId=id;currentEntry=null;detailTab=tab;
  if(!wasOpen)placeReader();
  $('#detail').setAttribute('aria-labelledby','reader-title');
  $('#detail-content').setAttribute('aria-busy','true');
  $('#detail-content').innerHTML=`<p class="reader-message" role="status">${esc(ui.loading)}</p>`;
  $('#detail-content').scrollTop=0;updateReaderNavigation();
  try {
    const e=await api('/api/entry?id='+encodeURIComponent(id));if(gen!==detailGeneration)return;
    currentEntry=e;renderDetail(tab);
  } catch(e) {
    if(gen===detailGeneration)$('#detail-content').innerHTML=`<div class="reader-message" role="alert"><strong>${esc(ui.readError)}</strong><p>${esc(e.message)}</p><button data-retry>${esc(ui.retry)}</button></div>`;
  } finally {
    if(gen===detailGeneration){$('#detail-content').setAttribute('aria-busy','false');saveUiState();}
  }
}
// Versions are compared field by field; differing fields lead, equal or unextracted ones fold away.
function renderVersions(e){
  const {members,fields}=e.versions;
  const value=(field,v)=>v===undefined||v===null||v===''?'—':Array.isArray(v)?v.map(x=>displayValue(field,x)).join('、'):displayValue(field,v);
  const head=`<thead><tr><th scope="col">${esc(ui.fields)}</th>${members.map(m=>`<th scope="col"><button type="button" class="link-button" data-entry="${esc(m.id)}" data-keep-tab ${m.id===e.id?'aria-current="true"':''}>${esc(versionLabel(m))}</button>${m.id===e.id?` <span class="muted">${esc(ui.currentVersion)}</span>`:''}</th>`).join('')}</tr></thead>`;
  const rows=list=>list.map(f=>`<tr class="${f.differs?'differs':''}"><th scope="row">${esc(fieldNames[f.field]||f.field)}</th>${f.values.map(v=>`<td>${esc(value(f.field,v))}</td>`).join('')}</tr>`).join('');
  const differing=fields.filter(f=>f.differs),same=fields.filter(f=>!f.differs);
  return `<p class="muted versions-intro">${esc(ui.versionsIntro)}</p>
    <h3 class="versions-heading">${esc(ui.differingFields)} ${differing.length}</h3>
    ${differing.length?`<div class="version-table-wrap"><table class="version-table">${head}<tbody>${rows(differing)}</tbody></table></div>`:''}
    ${same.length?`<details class="versions-same"><summary>${esc(ui.sameFields.replace('{count}',same.length))}</summary><div class="version-table-wrap"><table class="version-table">${head}<tbody>${rows(same)}</tbody></table></div></details>`:''}`;
}
function closeEntry({update=true,restoreFocus=true}={}) {
  detailGeneration++;
  if(restoreFocus)lastResultEntry=lastResultEntry||currentReaderId;
  if($('#detail').open)$('#detail').close();
  currentReaderId=null;currentEntry=null;detailTab='body';
  document.body.classList.remove('detail-open','detail-modal');
  if(restoreFocus)focusResult();
  updateReaderNavigation();
  if(update&&params.has('entry')){params.delete('entry');historyUpdate(true);}
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
