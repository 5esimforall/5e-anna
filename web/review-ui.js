/* Read-only review workspace. All saved decisions still use the guarded CLI. */
(() => {
  const lang=document.documentElement.lang.startsWith('en')?'en':'zh';
  const labels=annaLinkLabels[lang], l=labels.reviewSearch;
  const $=s=>document.querySelector(s), esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sourceLink=b=>`/source?${new URLSearchParams({path:b.path,start:b.start,end:b.end})}#evidence`;
  const set=(id,text)=>{$('#'+id).textContent=text;};
  for(const [id,text] of Object.entries({'review-back':l.back,'review-heading':l.title,'review-policy':l.policy,'candidate-heading':l.candidates,'reference-label':l.reference,'candidate-query-label':l.query,'candidate-submit':l.search,'missing-heading':l.missing,'missing-help':l.missingHelp,'missing-status-label':l.status,'missing-submit':l.refresh,'candidate-prev':l.prev,'candidate-next':l.next,'missing-prev':l.prev,'missing-next':l.next}))set(id,text);
  document.title=annaPageTitle('query',l.title);
  const select=$('#missing-form select');select.innerHTML=`<option value="">${esc(l.all)}</option>`+Object.entries(l.states).map(([v,t])=>`<option value="${v}">${esc(t)}</option>`).join('');select.value='open';
  let candidateOffset=0,missingOffset=0,candidateGeneration=0,missingGeneration=0;
  let candidateInput={reference:new URLSearchParams(location.search).get('reference')||'',query:''};
  const fetchJSON=async url=>{const response=await fetch(url);const data=await response.json();if(!response.ok)throw Error(data.error||l.error);return data;};
  const pageState=(prefix,data)=>{set(prefix+'-count',`${data.offset+1>data.total?0:data.offset+1}–${Math.min(data.total,data.offset+data.rows.length)} / ${data.total}`);$('#'+prefix+'-prev').disabled=data.offset===0;$('#'+prefix+'-next').disabled=data.offset+data.rows.length>=data.total;};
  async function candidates(){
    const gen=++candidateGeneration;set('candidate-results',l.loading);$('#candidate-prev').disabled=true;$('#candidate-next').disabled=true;
    try {
      const data=await fetchJSON('/api/reference-candidates?'+new URLSearchParams({id:candidateInput.reference,q:candidateInput.query,limit:20,offset:candidateOffset}));if(gen!==candidateGeneration)return;
      const b=data.binding;
      $('#candidate-results').innerHTML=`<article class="fact"><blockquote>${esc(b.before)}<mark>${esc(b.text)}</mark>${esc(b.after)}</blockquote><a href="${esc(sourceLink(b))}" target="_blank" rel="noopener">${esc(labels.extractedRange)} ↗</a><p><code>${esc(data.reference_id)}</code></p></article>`+
        (data.rows.length?data.rows.map(r=>`<article class="fact"><h3><a href="/?entry=${encodeURIComponent(r.id)}" target="_blank" rel="noopener">${esc(r.name)} ↗</a></h3><div>${esc(r.type)} · ${esc(r.ruleset)} · ${esc((r.source_names||[]).join('；'))}</div><p>${r.reasons.map(reason=>`<span class="badge">${esc(l.reasons[reason.code]||reason.code)}</span>`).join(' ')}</p>${r.warnings.length?`<p>${r.warnings.map(w=>esc(l.warnings[w]||w)).join('；')}</p>`:''}<p><code>${esc(r.id)}</code></p><a href="${esc(sourceLink({path:r.page_path,start:r.start,end:r.end}))}" target="_blank" rel="noopener">${esc(r.page_path)} ↗</a><details><summary>${esc(l.excerpt)}</summary><pre class="body-text">${esc(r.excerpt)}${r.excerpt_truncated?'…':''}</pre></details></article>`).join(''):`<p>${esc(l.empty)}</p>`);
      pageState('candidate',data);
    }catch(e){if(gen===candidateGeneration)set('candidate-results',e.message);}
  }
  async function missing(){
    const gen=++missingGeneration;set('missing-results',l.loading);$('#missing-prev').disabled=true;$('#missing-next').disabled=true;
    try{
      const data=await fetchJSON('/api/missing-definitions?'+new URLSearchParams({status:select.value,limit:20,offset:missingOffset}));if(gen!==missingGeneration)return;
      $('#missing-results').innerHTML=data.rows.length?data.rows.map(r=>`<article class="fact"><h3>${esc(r.text)} <span class="badge">${esc(l.states[r.status]||r.status)}</span></h3><blockquote>${esc(r.event.binding.before)}<mark>${esc(r.text)}</mark>${esc(r.event.binding.after)}</blockquote><p>${esc(r.event.reason)}</p><small>${esc(r.event.reviewer)} · ${esc(r.event.created_at)}</small><p><code>${esc(r.id)}</code></p><p>${esc(l.latest)} <code>${esc(r.latest_decision_id)}</code> · ${esc(r.latest_status)}</p><button type="button" data-reference="${esc(r.reference_id)}">${esc(l.searchAgain)}</button> <a href="${esc(sourceLink(r.event.binding))}" target="_blank" rel="noopener">${esc(labels.extractedRange)} ↗</a>${r.target_id?` <a href="/?entry=${encodeURIComponent(r.target_id)}" target="_blank" rel="noopener">${esc(l.target)} ↗</a>`:''}<details><summary>${esc(labels.evidence)}</summary><pre class="body-text">${esc(JSON.stringify({search:r.event.candidate_search,changes:r.changes},null,2))}</pre></details></article>`).join(''):`<p>${esc(l.emptyQueue)}</p>`;pageState('missing',data);
    }catch(e){if(gen===missingGeneration)set('missing-results',e.message);}
  }
  $('#candidate-form [name=reference]').value=candidateInput.reference;
  $('#candidate-form').addEventListener('submit',e=>{e.preventDefault();candidateOffset=0;candidateInput={reference:e.target.elements.reference.value.trim(),query:e.target.elements.query.value.trim()};candidates();});
  $('#missing-form').addEventListener('submit',e=>{e.preventDefault();missingOffset=0;missing();});
  $('#candidate-prev').onclick=()=>{candidateOffset=Math.max(0,candidateOffset-20);candidates();};$('#candidate-next').onclick=()=>{candidateOffset+=20;candidates();};
  $('#missing-prev').onclick=()=>{missingOffset=Math.max(0,missingOffset-20);missing();};$('#missing-next').onclick=()=>{missingOffset+=20;missing();};
  $('#missing-results').addEventListener('click',e=>{const b=e.target.closest('[data-reference]');if(!b)return;candidateInput={reference:b.dataset.reference,query:''};candidateOffset=0;$('#candidate-form [name=reference]').value=candidateInput.reference;$('#candidate-form [name=query]').value='';candidates();$('#candidate-form [name=reference]').focus();});
  select.addEventListener('change',()=>{missingOffset=0;missing();});
  fetchJSON('/api/meta').then(data=>{if(data.review_snapshot?.pending_rebuild)set('review-pending',labels.pendingReview);}).catch(e=>set('review-pending',e.message));
  if(candidateInput.reference)candidates();missing();
})();
