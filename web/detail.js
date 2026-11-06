function renderDetail(tab='body') {
  const e=currentEntry;
  const tabs=[['body','正文'],['fields','属性'],['facts',`证据 ${e.facts.length}`],['relations',`关系 ${e.relations.length}`],['reviews',`待核 ${e.reviews.length}`]];
  let content='';
  if(tab==='body') content=`<div class="body-text">${esc(e.body)}</div>`;
  if(tab==='fields') content=`<dl class="detail-fields">${Object.entries(e.fields).map(([k,v])=>`<dt>${esc(fieldNames[k]||k)}</dt><dd>${esc(displayValue(k,v))}${e.field_conflicts.includes(k)?' '+badge('冲突','amber'):''}</dd>`).join('')}</dl>`;
  if(tab==='facts') content=e.facts.map(f=>`<article class="fact"><div class="fact-head"><strong>${esc(fieldNames[f.field]||f.field)}</strong>${badge(f.status,f.status==='原文明示'?'green':'amber')}</div><div class="fact-value">${esc(displayValue(f.field,f.value))}</div><small>规则：${esc(f.rule)} · 作用域：${esc(f.scope)}</small>${f.excerpt?`<blockquote>${esc(f.excerpt)}</blockquote>`:''}${f.evidence.path?`<a href="${esc(sourceURL(f.evidence.path,...(f.evidence.span||[0,0])))}" target="_blank" rel="noopener">${esc(f.evidence.path)} ↗</a>`:`<details><summary class="muted">定位依据</summary><pre class="body-text">${esc(JSON.stringify(f.evidence,null,2))}</pre></details>`}</article>`).join('');
  if(tab==='relations') content=e.relations.length?e.relations.map(r=>`<div class="related"><span class="muted">${esc(relationNames[r.predicate]||r.predicate)}</span>${r.object_id?`<button data-entry="${esc(r.object_id)}">${esc(r.target_label)}</button>`:`<span>${esc(r.target_label)} ${badge('目标未关联','amber')}</span>`}${badge(r.status)}</div>`).join(''):'<p class="muted">未提取到关系；不代表不存在。</p>';
  if(tab==='reviews') content=`<p class="muted">仅展示关联此条目或所在页面的问题。没有待核记录不代表已经核实。</p>`+e.reviews.map(r=>`<article class="fact"><strong>${esc(reasonNames[r.reason]||r.reason)}</strong><pre class="body-text">${esc(pretty(r.detail))}</pre></article>`).join('');
  $('#detail-content').innerHTML=`<div class="eyebrow">${esc(e.type_label)} / ${esc(e.ruleset)}</div><h2>${esc(e.name_zh||e.name)}</h2><div class="english">${esc(e.name_en)}</div><div class="detail-meta">${esc(e.source_names.join('；'))}</div><div class="badges">${badge(e.classification_status,e.classification_status==='原文明示'?'green':'amber')}${badge(legacy[e.legacy])}${e.field_conflicts.length?badge(`${e.field_conflicts.length} 个字段冲突`,'amber'):''}</div><div class="detail-meta">${metadata.book_url?`<a target="_blank" rel="noopener" href="${esc(metadata.book_url+'/definition/'+encodeURIComponent(e.id)+'?reader=1')}">查看不全书定义 ↗</a> · `:''}<a target="_blank" rel="noopener" href="${esc(sourceURL(e.page_path,e.start,e.end))}">查看提取文本与条目范围 ↗</a></div><div class="detail-tabs" role="tablist" aria-label="详情栏目">${tabs.map(([id,name])=>`<button role="tab" id="tab-${id}" aria-controls="detail-panel" aria-selected="${tab===id}" class="${tab===id?'active':''}" data-tab="${id}">${esc(name)}</button>`).join('')}</div><section id="detail-panel" role="tabpanel" aria-labelledby="tab-${tab}">${content}</section><div class="footnote">${esc(e.id)} · 自动版本候选</div>`;
}
async function openEntry(id,update=true) {
  const gen=++detailGeneration;
  if(update){params.set('entry',id);historyUpdate();}
  if(!$('#detail').open)$('#detail').showModal();
  $('#detail-content').textContent='正在读取…';
  try {const e=await api('/api/entry?id='+encodeURIComponent(id));if(gen!==detailGeneration)return;currentEntry=e;renderDetail();}
  catch(e){if(gen===detailGeneration)$('#detail-content').textContent=e.message;}
}
function closeEntry() {detailGeneration++;if($('#detail').open)$('#detail').close();if(params.has('entry')){params.delete('entry');historyUpdate(true);}}
