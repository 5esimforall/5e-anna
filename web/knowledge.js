'use strict';
// Rule-chain wording is kept together in both interface languages. Source quotations
// are always shown in their original language; structured values have stable keys.
window.annaKnowledge=(()=>{
  const {esc}=annaKit;
  const labels={
    zh:{title:'规则链',filter:'施法链条件',hint:'这些条件必须命中同一授予、同一法术和同一使用分支。可选法术表示允许选择，不表示全部授予。',any:'不限',apply:'应用施法链条件',clear:'移除施法链条件',spell:'固定或可选法术名称',
      fields:{mode:'授予方式',resource:'法术位消耗',recovery:'该分支的恢复条件',selection:'目标方式'},
      mode:{known:'习得',prepared:'始终准备',cast:'允许施展',list:'加入法术列表'},resource:{slot_free:'无需消耗法术位',spell_slot:'消耗法术位',not_stated:'本段未明示'},recovery:{long_rest:'长休',short_rest:'短休',short_or_long_rest:'短休或长休',not_stated:'本分支未明示恢复'},selection:{fixed:'固定法术',choice:'受限选择'},
      empty:'尚未提取到这条规则链；不代表原文没有相关能力。',scope:'此处整理施法相关规则。尚未解析的限定明确列出，不作为无条件查询结果。',
      status:{resolved:'已解析',partial:'部分解析',review:'待核',rejected:'已否决'},review:{unreviewed:'未人工核准',applied:'审核已采用',revoked:'审核已撤销',stale:'依据已变化',conflict:'审核冲突'},
      kinds:{spell_access:'施法能力',spell_effect:'法术效果',feat_access:'授予专长',definition_role:'定义角色'},
      roles:{glossary:'术语简释',explanation:'正文详解',template:'模板小标题'},
      evidence:'原文证据',reader:'在 Stella 中阅读',export:'导出本条规则链 JSON',raw:'结构化记录与审核依据',unparsed:'未解析的原文',
      branches:'使用分支',uses:'次数',perSpell:'每道法术',perGrant:'本项能力',ability:'施法属性',prerequisites:'前置条件',
      atLeast:'至少',level:'等级',choose:'选择',options:'可选法术',notAll:'允许选择，不表示全部授予',inspectSpell:'查看此法术的效果',spellDetails:'法术条目',
      list:'法术列表',schools:'学派',levels:'环阶',cantrip:'戏法',count:'个',abilityIncrease:'本专长提升的属性',
      alternative:'独立使用分支',notStated:'本段未明示',notParsed:'未解析',slotLevel:'法术位环阶',appropriate:'合适环阶',available:'拥有的任意法术位',spell_level:'该法术最低环阶',
      modifiers:'选择与替换规则',gainLevel:'获得新等级时，可替换一道同列表、同环阶法术',repeat:'可复选，每次须选择不同的法术列表',
      generalRules:'涉及的通则与例外',uses_rule:'使用通则',exception_to:'修改通则',ambiguous:'目标有歧义',missing:'尚无确定目标',
      incoming:'可固定授予或允许选择此法术的条目',preview:'正在查阅法术',noPreview:'展开可选法术，选择一个查看其效果；此操作只改变阅读位置，不建立角色选择。',
      action:'施法时间',range:'施法距离',components:'法术成分',duration:'持续时间',
      effect:{teleport:'传送',healing:'恢复生命值',saving_throw:'要求豁免',condition:'施加状态',attitude:'态度变化',awareness:'获知效应',upcast:'升环变化'},
      target:'目标',self:'自身',creature:'生物',humanoid:'类人生物',visible:'可见',unoccupied:'未被占据',touch:'触碰',foot:'尺',
      max:'至多',saveFailure:'豁免失败时',ends:'结束条件',spellEnds:'法术结束',damageEnds:'施法者或其同伴对目标造成伤害',
      advantage:'目标正与施法者或其同伴战斗时，此次豁免具有优势',friendly:'受该状态影响期间，对施法者友好',aware:'法术结束时，目标知道该状态由施法者造成',
      upcast:'法术位每高于 {level} 环一环',additional:'额外目标',spellModifier:'施法属性调整值',modifier:'调整值',minimum:'最低',
      confirmHint:'核对后可用 knowledge.py accept / reject 保存；重建后生效。',loading:'正在读取规则链…',error:'规则链读取失败',retry:'重新读取'},
    en:{title:'Rule chain',filter:'Spell-access conditions',hint:'All conditions must match the same grant, spell and usage branch. Eligible choices are not all granted spells.',any:'Any',apply:'Apply casting conditions',clear:'Remove casting conditions',spell:'Fixed or eligible spell name',
      fields:{mode:'Access',resource:'Spell-slot cost',recovery:'Branch recovery',selection:'Target selection'},
      mode:{known:'Learned',prepared:'Always prepared',cast:'Permission to cast',list:'Added to spell list'},resource:{slot_free:'No spell slot consumed',spell_slot:'Consumes a spell slot',not_stated:'Not stated here'},recovery:{long_rest:'Long rest',short_rest:'Short rest',short_or_long_rest:'Short or long rest',not_stated:'Recovery not stated for this branch'},selection:{fixed:'Fixed spell',choice:'Constrained choice'},
      empty:'No rule chain extracted yet; this does not establish absence.',scope:'This view describes spell-related rules. Unparsed limitations remain visible and are not treated as unconditional query results.',
      status:{resolved:'Parsed',partial:'Partially parsed',review:'Needs review',rejected:'Rejected'},review:{unreviewed:'Not human-approved',applied:'Review applied',revoked:'Review revoked',stale:'Evidence changed',conflict:'Review conflict'},
      kinds:{spell_access:'Spell access',spell_effect:'Spell effects',feat_access:'Granted feat',definition_role:'Definition role'},
      roles:{glossary:'Glossary definition',explanation:'Chapter explanation',template:'Template heading'},
      evidence:'Source evidence',reader:'Read in Stella',export:'Export this rule chain as JSON',raw:'Structured record and review basis',unparsed:'Unparsed source clauses',
      branches:'Usage branches',uses:'Uses',perSpell:'Per spell',perGrant:'For this grant',ability:'Spellcasting ability',prerequisites:'Prerequisites',
      atLeast:'At least',level:'Level',choose:'Choose',options:'Eligible spells',notAll:'Eligible choices, not all granted',inspectSpell:'Inspect this spell’s effects',spellDetails:'Spell entry',
      list:'Spell list',schools:'Schools',levels:'Spell level',cantrip:'Cantrip',count:'',abilityIncrease:'Ability increased by this feat',
      alternative:'Separate usage branch',notStated:'Not stated here',notParsed:'Unparsed',slotLevel:'Slot level',appropriate:'Appropriate level',available:'Any available spell slot',spell_level:'Spell’s lowest level',
      modifiers:'Choice and replacement rules',gainLevel:'On gaining a level, replace one spell with another of the same list and level',repeat:'Repeatable; choose a different spell list each time',
      generalRules:'Governing rules and exceptions',uses_rule:'Uses rule',exception_to:'Modifies rule',ambiguous:'Ambiguous target',missing:'No confirmed target',
      incoming:'Entries granting or permitting a choice of this spell',preview:'Inspecting spell',noPreview:'Expand the eligible spells and inspect an effect. This changes reading position, not a character’s selection.',
      action:'Casting time',range:'Range',components:'Components',duration:'Duration',
      effect:{teleport:'Teleport',healing:'Restore hit points',saving_throw:'Saving throw',condition:'Apply condition',attitude:'Attitude',awareness:'Awareness',upcast:'Higher-level slot'},
      target:'Target',self:'Self',creature:'Creature',humanoid:'Humanoid',visible:'Visible',unoccupied:'Unoccupied',touch:'Touch',foot:'ft',
      max:'Up to',saveFailure:'On a failed save',ends:'Ends when',spellEnds:'Spell ends',damageEnds:'Caster or caster’s allies damage the target',
      advantage:'Advantage on this save if in combat with the caster or the caster’s allies',friendly:'Friendly to the caster while affected by this condition',aware:'When the spell ends, the target knows the caster caused the condition',
      upcast:'For each slot level above {level}',additional:'Additional targets',spellModifier:'Spellcasting ability modifier',modifier:'modifier',minimum:'minimum',
      confirmHint:'Use knowledge.py accept / reject after checking the evidence; rebuild to publish.',loading:'Loading rule chain…',error:'Unable to read the rule chain',retry:'Try again'}
  };
  const l=labels[document.documentElement.lang.startsWith('en')?'en':'zh'];
  const badge=t=>`<span class="badge">${esc(t)}</span>`;
  const entryLink=(id,n)=>`<button type="button" class="link-button" data-entry="${esc(id)}" data-keep-tab>${esc(n||id)}</button>`;
  const sourceLink=ev=>`<details class="chain-evidence"><summary>${esc(l.evidence)}</summary><blockquote>${esc(ev.raw)}</blockquote><a href="/source?${esc(new URLSearchParams({path:ev.path,start:ev.span[0],end:ev.span[1]}))}#evidence" target="_blank" rel="noopener">${esc(l.evidence)} ↗</a></details>`;
  function expression(v){
    if(!v)return l.notParsed;
    if(v.kind==='constant')return String(v.value);
    if(v.kind==='dice')return `${v.count}d${v.sides}`;
    if(v.kind==='sum')return v.terms.map(expression).join(' + ');
    if(v.kind==='modifier')return `${v.ability==='spellcasting'?l.spellModifier:v.ability+' '+l.modifier}${v.minimum!==undefined?' ('+l.minimum+' '+v.minimum+')':''}`;
    return l.notParsed;
  }
  function ability(v,contexts){
    if(!v)return l.notStated;
    if(v.kind==='fixed')return v.value;
    if(v.kind==='choose_one')return `${l.choose} 1：${v.values.join(' / ')}`;
    if(v.kind==='by_class')return Object.entries(v.values).map(([c,a])=>`${c} → ${a}`).join('；');
    if(v.kind==='ability_increase_choice')return l.abilityIncrease+(contexts?.ability_increase?' ('+contexts.ability_increase.values.join(' / ')+')':'');
    return l.notParsed;
  }
  function target(t,nodes){
    const r=t.resolution;
    if(r?.status==='resolved')return entryLink(r.targets[0],nodes.get(r.targets[0])?.name||t.name);
    return `<strong>${esc(t.name||'')}</strong> ${badge(r?.status==='ambiguous'?l.ambiguous:l.missing)}${r?.targets?.length?`<details><summary>${esc(l.options)} ${r.targets.length}</summary>${r.targets.map(id=>entryLink(id,nodes.get(id)?.name)).join(' ')}</details>`:''}`;
  }
  function branch(b){
    const resource=b.resource,kind=resource.consumed===false?'slot_free':resource.consumed===true?'spell_slot':'not_stated';
    return `<section class="chain-branch"><h5>${esc(l.alternative)}</h5><dl class="detail-fields"><dt>${esc(l.fields.resource)}</dt><dd>${esc(l.resource[kind])}</dd>${resource.level?`<dt>${esc(l.slotLevel)}</dt><dd>${esc(l[resource.level]||resource.level)}</dd>`:''}${b.cast_level?`<dt>${esc(l.level)}</dt><dd>${esc(l[b.cast_level.kind])}</dd>`:''}<dt>${esc(l.uses)}</dt><dd>${b.uses?esc(expression(b.uses.quantity)+' · '+(b.uses.per==='spell'?l.perSpell:l.perGrant)):esc(l.notStated)}</dd><dt>${esc(l.fields.recovery)}</dt><dd>${esc(l.recovery[b.recovery?.event||'not_stated'])}</dd></dl>${b.evidence.map(sourceLink).join('')}</section>`;
  }
  function effect(e){
    let text='';
    if(e.kind==='teleport')text=`${l.self} → ${l.max} ${e.destination.distance.maximum} ${l.foot} · ${l.visible} · ${l.unoccupied}`;
    if(e.kind==='healing')text=`${l.touch} · ${e.target?.count||1} ${l.creature} → ${expression(e.quantity)}`;
    if(e.kind==='saving_throw')text=`${e.target.count} ${l[e.target.kind]} · ${l.visible} · ${l.range} → ${e.ability}；${l.advantage}`;
    if(e.kind==='condition')text=`${l.saveFailure} → ${e.condition}；${l.ends}：${l.spellEnds} / ${l.damageEnds}`;
    if(e.kind==='attitude')text=l.friendly;
    if(e.kind==='awareness')text=l.aware;
    if(e.kind==='upcast')text=l.upcast.replace('{level}',e.threshold)+' → '+(e.effect.kind==='additional_targets'?`${l.additional} +${e.effect.count}`:`${l.effect.healing} +${expression(e.effect.quantity)}`);
    return `<section class="chain-effect"><h4>${esc(l.effect[e.kind]||e.kind)}</h4><p>${esc(text)}</p>${sourceLink(e.evidence)}</section>`;
  }
  function record(r,chain,nodes,audit){
    const v=r.value,subject=nodes.get(r.subject_id);let body='';
    if(r.kind==='definition_role')body=`<p>${esc(l.roles[v.role]||v.role)}</p>`;
    if(r.kind==='feat_access')body=`<p>${target(v.target,nodes)}</p>${v.constraints.class_list?`<p>${esc(l.list)}：${esc(v.constraints.class_list.join(' / '))}</p>`:''}${sourceLink(v.target.evidence)}`;
    if(r.kind==='spell_access'){
      const pre=v.prerequisites;
      body=(pre?.status!=='not_stated'?`<p>${esc(l.prerequisites)}：${esc(pre?.expression?`${l.level} ≥ ${pre.expression.minimum}`:l.notParsed)}</p>${pre?.evidence?sourceLink(pre.evidence):''}`:'');
      body+=v.grants.map(g=>{
        const t=g.target,options=chain.options.filter(o=>o.subject_id===r.subject_id&&o.grant_id===g.id),ids=[...new Set(options.flatMap(o=>o.spell_ids))];
        const allowed=ids.length?ids:(r.status==='resolved'?t.options||[]:[]),c=t.constraints||{};
        const heading=t.kind==='entry'?target(t,nodes):`${esc(l.choose)} ${t.count} ${esc(l.count)} · ${esc(l.notAll)}`;
        const domain=t.kind==='choice'?`<p class="muted">${esc(l.levels)}：${esc((c.levels||[]).map(n=>n===0?l.cantrip:n).join(' / '))}${c.schools?' · '+esc(l.schools+'：'+c.schools.join(' / ')):''}${c.class_list?' · '+esc(l.list+'：'+(chain.options.find(o=>o.subject_id===r.subject_id)?.constraints.class_list||c.class_list.values).join(' / ')):''}</p>`:'';
        const choices=allowed.length?`<details class="chain-choices" ${t.kind==='entry'?'open':''}><summary>${esc(t.kind==='entry'?l.inspectSpell:l.options)} (${allowed.length})</summary><ul>${allowed.map(id=>`<li><button type="button" class="link-button" data-chain-spell="${esc(id)}" ${id===chain.selected_spell_id?'aria-current="true"':''}>${esc(nodes.get(id)?.name||id)}</button></li>`).join('')}</ul></details>`:'';
        return `<article class="chain-grant"><h4>${badge(l.mode[g.mode]||l.notParsed)} ${heading}</h4>${domain}<p>${esc(l.ability)}：${esc(ability(g.ability,v.contexts))}</p>${g.evidence.map(sourceLink).join('')}${choices}<h4>${esc(l.branches)}</h4><div class="chain-branches">${g.branches.map(branch).join('')}</div></article>`;
      }).join('');
      if(v.modifiers.length)body+=`<h4>${esc(l.modifiers)}</h4>${v.modifiers.map(m=>`<p>${esc(m.kind==='replace_choice'?l.gainLevel:l.repeat)}</p>${sourceLink(m.evidence)}`).join('')}`;
    }
    if(r.kind==='spell_effect'){
      body=`<dl class="detail-fields">${['action','range','components','duration'].filter(k=>v.casting[k]).map(k=>[k,v.casting[k]]).map(([k,c])=>`<dt>${esc(l[k]||k)}</dt><dd>${esc(c.raw)}${sourceLink(c.evidence)}</dd>`).join('')}</dl>${v.effects.map(effect).join('')}`;
    }
    if(v.unparsed?.length)body+=`<details class="chain-gaps"><summary>${esc(l.unparsed)} (${v.unparsed.length})</summary>${v.unparsed.map(g=>sourceLink(g.evidence)).join('')}</details>`;
    if(v.rules?.length)body+=`<details class="chain-rules"><summary>${esc(l.generalRules)}</summary>${v.rules.map(r=>`<p>${esc(l[r.relation])}：${target(r.target,nodes)}</p>${sourceLink(r.target.evidence)}`).join('')}</details>`;
    return `<section class="knowledge-record" data-knowledge="${esc(r.id)}"><header><h3>${esc(l.kinds[r.kind])} · ${entryLink(r.subject_id,subject?.name)}</h3>${badge(l.status[r.status])}${audit?badge(l.review[r.review_status]):''}</header>${body}<div class="detail-links"><a href="/read?entry=${encodeURIComponent(r.subject_id)}" target="_blank" rel="noopener">${esc(l.reader)} ↗</a></div>${audit?`<details><summary>${esc(l.raw)}</summary><p>${esc(l.confirmHint)}</p><code>${esc(r.id)}</code><a href="/api/knowledge-inspect?id=${encodeURIComponent(r.id)}" target="_blank" rel="noopener"> JSON ↗</a><pre class="body-text">${esc(JSON.stringify(r,null,2))}</pre></details>`:''}</section>`;
  }
  function render(entry,audit){
    const c=entry.rule_chain||{records:[],nodes:[],options:[],incoming:[]},nodes=new Map(c.nodes.map(n=>[n.id,n]));
    return `<div class="rule-chain"><p class="muted">${esc(l.scope)}</p><a href="/api/knowledge-export?id=${encodeURIComponent(entry.id)}${c.selected_spell_id?'&spell='+encodeURIComponent(c.selected_spell_id):''}" download>${esc(l.export)}</a>${c.records.length?[...c.records].sort((a,b)=>(a.subject_id===entry.id?0:a.kind==='spell_access'?1:2)-(b.subject_id===entry.id?0:b.kind==='spell_access'?1:2)).map(r=>record(r,c,nodes,audit)).join(''):`<p>${esc(l.empty)}</p>`}${c.options.some(o=>o.selection==='choice')&&!c.selected_spell_id?`<p>${esc(l.noPreview)}</p>`:''}${c.incoming.length?`<details><summary>${esc(l.incoming)} (${c.incoming.length})</summary>${c.incoming.map(x=>entryLink(x.entry_id,`${x.name} · ${x.ruleset==='5e'?'2014':'2024'}`)).join(' ')}</details>`:''}</div>`;
  }
  function description(raw){
    try{return l.filter+'：'+Object.entries(JSON.parse(raw)).map(([k,v])=>k==='spell'?l.spell+' '+v:l[k]?.[v]||v).join(' · ');}catch{return l.filter;}
  }
  function install({params,changed,api}){
    const box=document.querySelector('#casting-filters');let last='',generation=0;
    box.innerHTML=`<summary>${esc(l.filter)}</summary><p class="muted">${esc(l.hint)}</p><div class="casting-controls">${Object.entries(l.fields).map(([key,label])=>`<label>${esc(label)}<select data-casting="${key}" aria-label="${esc(label)}"><option value="">${esc(l.any)}</option>${Object.entries(l[key]).map(([value,text])=>`<option value="${esc(value)}">${esc(text)}</option>`).join('')}</select></label>`).join('')}<label>${esc(l.spell)}<input type="text" data-casting="spell" maxlength="200" aria-label="${esc(l.spell)}"></label></div><div class="casting-actions"><button type="button" data-casting-apply>${esc(l.apply)}</button><button type="button" data-casting-clear>${esc(l.clear)}</button></div>`;
    const read=()=>Object.fromEntries([...box.querySelectorAll('[data-casting]')].filter(el=>el.value.trim()).map(el=>[el.dataset.casting,el.value.trim()]));
    box.querySelector('[data-casting-apply]').onclick=()=>{const value=read();if(Object.keys(value).length)params().set('casting',JSON.stringify(value));else params().delete('casting');params().delete('not_casting');changed();};
    box.querySelector('[data-casting-clear]').onclick=()=>{params().delete('casting');params().delete('not_casting');changed();};
    box.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.matches('input')){event.preventDefault();box.querySelector('[data-casting-apply]').click();}});
    async function counts(){
      if(!box.open)return;const gen=++generation,q=new URLSearchParams(params());const value=read();q.delete('not_casting');
      if(Object.keys(value).length)q.set('casting',JSON.stringify(value));else q.delete('casting');
      try{const result=await api('/api/casting-facets?'+q);if(gen!==generation)return;
        for(const [key,items] of Object.entries(result.groups))for(const item of items){const option=[...box.querySelector(`[data-casting="${key}"]`).options].find(o=>o.value===item.value);if(option)option.textContent=l[key][item.value]+` (${item.count})`;}
      }catch{/* Main query owns error presentation; counts never replace edited values. */}
    }
    box.addEventListener('toggle',counts);box.addEventListener('change',counts);
    return {sync(){const raw=params().get('casting')||params().get('not_casting')||'';if(raw!==last){let value={};try{value=raw?JSON.parse(raw):{};}catch{}for(const input of box.querySelectorAll('[data-casting]'))input.value=value[input.dataset.casting]||'';if(raw)box.open=true;last=raw;}counts();}};
  }
  return {labels:l,render,description,install};
})();
