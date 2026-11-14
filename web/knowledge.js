'use strict';
// Rule-chain wording is kept together in both interface languages. Source quotations
// are always shown in their original language; structured values have stable keys.
window.annaKnowledge=(()=>{
  const {esc}=annaKit;
  const labels={
    zh:{path:'阅读路径',overview:'返回问题起点',step:'本步关系',provided_by:'获取途径',modified_by:'被这些能力修改',grants_feat:'授予专长',grants_spell:'固定授予',eligible_spell:'允许选择',coverage:'各环节的整理状态',content:'本段内容',dependencies:'通则目标',unresolvedRules:'仍有未确定的通则目标',scopeLabel:'适用范围',change:'修改字段',when:'适用条件',all:'同时满足',anyOf:'满足任一',not:'不满足',save:'豁免',failure:'失败',success:'成功',conditionActive:'状态生效',combat:'目标正与施法者或其同伴战斗',branchSelected:'使用此分支时',endsWhen:'结束条件',sourceText:'当前条目的原文',binding:'本链的施法属性供给',unknownBinding:'尚未明确施法属性',readingHint:'查看候选只改变阅读位置，不确认关系。路径不判断角色是否满足条件。',title:'规则链',filter:'施法链条件',hint:'这些条件必须命中同一授予、同一法术和同一使用分支。可选法术表示允许选择，不表示全部授予。',any:'不限',apply:'应用施法链条件',clear:'移除施法链条件',spell:'固定或可选法术名称',
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
      advantageName:'优势',advantage:'目标正与施法者或其同伴战斗时，此次豁免具有优势',friendly:'受该状态影响期间，对施法者友好',aware:'法术结束时，目标知道该状态由施法者造成',
      upcast:'法术位每高于 {level} 环一环',additional:'额外目标',spellModifier:'施法属性调整值',modifier:'调整值',minimum:'最低',
      confirmHint:'核对后可用 knowledge.py accept / reject 保存；重建后生效。',loading:'正在读取规则链…',error:'规则链读取失败',retry:'重新读取'},
    en:{path:'Reading path',overview:'Back to the root question',step:'Relationship at this step',provided_by:'Provided by',modified_by:'Modified by',grants_feat:'Grants feat',grants_spell:'Grants spell',eligible_spell:'Eligible choice',coverage:'Progress by segment',content:'Statement content',dependencies:'Governing targets',unresolvedRules:'Some governing targets remain unresolved',scopeLabel:'Scope',change:'Changed field',when:'Applies when',all:'All of',anyOf:'Any of',not:'Not',save:'Save',failure:'Failure',success:'Success',conditionActive:'Condition active',combat:'Target is in combat with the caster or their allies',branchSelected:'When using this branch',endsWhen:'Ends when',sourceText:'Current definition text',binding:'Spellcasting ability supplied by this chain',unknownBinding:'Spellcasting ability is not established',readingHint:'Inspecting a candidate does not confirm the relationship. The path does not evaluate a character’s eligibility.',title:'Rule chain',filter:'Spell-access conditions',hint:'All conditions must match the same grant, spell and usage branch. Eligible choices are not all granted spells.',any:'Any',apply:'Apply casting conditions',clear:'Remove casting conditions',spell:'Fixed or eligible spell name',
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
      advantageName:'Advantage',advantage:'Advantage on this save if in combat with the caster or the caster’s allies',friendly:'Friendly to the caster while affected by this condition',aware:'When the spell ends, the target knows the caster caused the condition',
      upcast:'For each slot level above {level}',additional:'Additional targets',spellModifier:'Spellcasting ability modifier',modifier:'modifier',minimum:'minimum',
      confirmHint:'Use knowledge.py accept / reject after checking the evidence; rebuild to publish.',loading:'Loading rule chain…',error:'Unable to read the rule chain',retry:'Try again'}
  };
  const extended={
    zh:{
      fields:{effect:'效果种类',damage_type:'伤害类型',delivery:'效应依据',timing:'效应时序',save_ability:'本效果的豁免属性',trigger:'适用条件涉及',end_trigger:'结束条件涉及',access_condition:'获取或启用条件涉及',casting_target:'本分支施法目标'},
      save_ability:{力量:'力量',敏捷:'敏捷',体质:'体质',智力:'智力',感知:'感知',魅力:'魅力',not_stated:'本效果未明示'},
      trigger:{attack_result:'攻击结果',save_result:'豁免结果',turn:'回合时点',damage_received:'受到伤害',wake_action:'动作唤醒',outside_spell_range:'超出施法距离',cover:'掩护',action_spent:'使用动作',requires_sleep:'睡眠需求',condition_immunity:'状态免疫'},
      end_trigger:{turn:'回合时点',spell_ends:'法术结束',damage_from:'施法者或同伴造成伤害'},
      access_condition:{level:'角色等级',class_level:'职业等级',ability_score:'属性值',class:'职业',campaign:'战役',requirement:'具名选项',item_state:'物品状态'},casting_target:{self:'仅自身',not_stated:'本分支未限定'},
      item_state:{held:'持握此物品',worn:'穿戴此物品',carried:'携带此物品',attuned:'与此物品同调'},
      requires_option:'前置选项',atWill:'次数不限',waived:'免除成分',disadvantageName:'劣势',allSpellSaves:'对抗此法术的全部豁免',requiresSleep:'目标需要睡眠',immuneTo:'目标免疫',wakeAction:'其他生物用动作唤醒目标，距离至多',ordinalCheck:'第 {n} 次豁免',inheritedAbility:'属性沿检定引用取得',chosenByCaster:'由施法者选择区域内生物',radius:'半径',pairwise:'各目标相互距离至多',acquisition:'取得资格',activation:'启用条件',scopeConditions:'沿此路径保留的限定',
      effect:{targets:'选定目标',attack_roll:'攻击检定',damage:'造成伤害',modifier:'数值或检定修正',restriction:'限制',behavior:'行为',check_modifier:'特定检定修正',end_spell:'结束法术',level_scale:'等级成长',ignite:'点燃',save_override:'豁免结果例外',target_constraint:'目标范围限制'},
      damage_type:{acid:'强酸',cold:'寒冷',fire:'火焰',force:'力场',lightning:'闪电',necrotic:'暗蚀',poison:'毒素',psychic:'心灵',radiant:'光耀',thunder:'雷鸣',bludgeoning:'钝击',piercing:'穿刺',slashing:'挥砍'},
      delivery:{attack_roll:'攻击检定',saving_throw:'豁免检定',automatic:'直接生效，不再攻击',not_stated:'未归类'},
      timing:{immediate:'初次效应',ongoing:'后续回合施用',repeat:'重复豁免',not_stated:'未归类'},
      attack_type:{ranged_spell:'远程法术攻击',melee_spell:'近战法术攻击'},
      property:{object:'物件',flammable:'可燃',worn:'被着装',carried:'被携带'},
      shape:{cone:'锥状',sphere:'球状',cube:'立方'},actor:{caster:'施法者',target:'目标'},
      phase:{start:'开始',end:'结束',during:'期间'},relative:{next:'下一回合',each:'每回合',subsequent:'后续各回合'},
      mechanic:{speed:'速度',attack_roll:'攻击检定','healing.allowed':'允许恢复生命值','prone.self_end':'自行结束倒地','spell_slot.consumed':'法术位消耗','casting.target':'施法目标','casting.targets':'目标间距','components.waived':'免除成分','saving_throw.modifier':'特定豁免修正','saving_throw.result':'豁免结果'},
      hit:'命中',miss:'未命中',spellActive:'法术仍生效',takesDamage:'受到伤害',outsideRange:'目标超出施法距离',totalCover:'目标相对施法者处于全身掩护',otherAction:'施法者用动作做其他事情',canLaugh:'目标能够发笑',withinArea:'位于区域',
      referencedQuantity:'引用数量',localLinks:'沿效果关系阅读',targetDefinition:'目标定义',initial:'初次',repeatCheck:'重复',attackOutcome:'首击命中要求',independentAttack:'无论首击是否命中',unresolvedAttack:'原文未明确后续伤害与首击的条件关系',
      ongoingCost:'每次后续施用',bonusAction:'附赠动作',standardAction:'动作',oncePerTurn:'每回合一次',subtract:'减少',yes:'是',no:'否',laugh:'不受控制地狂笑',endAll:'结束法术',endTarget:'结束目标身上的效应',nextAttack:'对目标的下一次攻击',
      sameEffect:'效果条件必须命中同一法术中的同一效果；后续自动伤害不借用初次攻击的命中条件。'
    },
    en:{
      fields:{effect:'Effect kind',damage_type:'Damage type',delivery:'Effect delivery',timing:'Effect timing',save_ability:'Save ability for this effect',trigger:'Applicability mentions',end_trigger:'Ending condition mentions',access_condition:'Access condition mentions',casting_target:'Branch casting target'},
      save_ability:{力量:'Strength',敏捷:'Dexterity',体质:'Constitution',智力:'Intelligence',感知:'Wisdom',魅力:'Charisma',not_stated:'Not stated for this effect'},
      trigger:{attack_result:'Attack result',save_result:'Save result',turn:'Turn timing',damage_received:'Damage received',wake_action:'Wake action',outside_spell_range:'Beyond spell range',cover:'Cover',action_spent:'Action spent',requires_sleep:'Sleep requirement',condition_immunity:'Condition immunity'},
      end_trigger:{turn:'Turn timing',spell_ends:'Spell ends',damage_from:'Damage from caster or allies'},
      access_condition:{level:'Character level',class_level:'Class level',ability_score:'Ability score',class:'Class',campaign:'Campaign',requirement:'Named option',item_state:'Item state'},casting_target:{self:'Self only',not_stated:'Not restricted by this branch'},
      item_state:{held:'Holding this item',worn:'Wearing this item',carried:'Carrying this item',attuned:'Attuned to this item'},
      requires_option:'Prerequisite option',atWill:'At will',waived:'Waived components',disadvantageName:'Disadvantage',allSpellSaves:'All saves against this spell',requiresSleep:'Target needs sleep',immuneTo:'Target is immune to',wakeAction:'Another creature uses an action to wake the target, within',ordinalCheck:'Save number {n}',inheritedAbility:'Ability supplied by the referenced check',chosenByCaster:'Caster chooses creatures in the area',radius:'Radius',pairwise:'Maximum distance between each pair of targets',acquisition:'Acquisition eligibility',activation:'Activation condition',scopeConditions:'Limitations retained along this path',
      effect:{targets:'Target selection',attack_roll:'Attack roll',damage:'Deal damage',modifier:'Modifier',restriction:'Restriction',behavior:'Behavior',check_modifier:'Specific check modifier',end_spell:'End spell',level_scale:'Level scaling',ignite:'Ignite',save_override:'Save result override',target_constraint:'Target constraint'},
      damage_type:{acid:'Acid',cold:'Cold',fire:'Fire',force:'Force',lightning:'Lightning',necrotic:'Necrotic',poison:'Poison',psychic:'Psychic',radiant:'Radiant',thunder:'Thunder',bludgeoning:'Bludgeoning',piercing:'Piercing',slashing:'Slashing'},
      delivery:{attack_roll:'Attack roll',saving_throw:'Saving throw',automatic:'Automatic, no further attack',not_stated:'Not classified'},
      timing:{immediate:'Initial effect',ongoing:'Use on later turns',repeat:'Repeated save',not_stated:'Not classified'},
      attack_type:{ranged_spell:'Ranged spell attack',melee_spell:'Melee spell attack'},
      property:{object:'Object',flammable:'Flammable',worn:'Worn',carried:'Carried'},
      shape:{cone:'Cone',sphere:'Sphere',cube:'Cube'},actor:{caster:'Caster',target:'Target'},
      phase:{start:'Start',end:'End',during:'During'},relative:{next:'Next turn',each:'Each turn',subsequent:'Subsequent turns'},
      mechanic:{speed:'Speed',attack_roll:'Attack roll','healing.allowed':'Healing allowed','prone.self_end':'End own prone condition','spell_slot.consumed':'Spell slot consumed','casting.target':'Casting target','casting.targets':'Target spacing','components.waived':'Waived components','saving_throw.modifier':'Specific save modifier','saving_throw.result':'Save result'},
      hit:'Hit',miss:'Miss',spellActive:'Spell remains active',takesDamage:'Takes damage',outsideRange:'Target is beyond spell range',totalCover:'Target has total cover from caster',otherAction:'Caster spends the action on something else',canLaugh:'Target can laugh',withinArea:'Within area',
      referencedQuantity:'Referenced quantity',localLinks:'Follow effect relationships',targetDefinition:'Target definition',initial:'Initial',repeatCheck:'Repeated',attackOutcome:'Initial hit requirement',independentAttack:'Regardless of the initial attack result',unresolvedAttack:'Source does not state how ongoing damage depends on the initial hit',
      ongoingCost:'Each later use',bonusAction:'Bonus action',standardAction:'Action',oncePerTurn:'Once per turn',subtract:'Subtract',yes:'Yes',no:'No',laugh:'Laugh uncontrollably',endAll:'End the spell',endTarget:'End the effect on this target',nextAttack:'Next attack against this target',
      sameEffect:'Effect conditions must match one effect of the same spell. Ongoing automatic damage does not inherit the initial attack’s hit condition.'
    }
  };
  for(const lang of ['zh','en']){
    const {fields,effect,...rest}=extended[lang];Object.assign(labels[lang].fields,fields);Object.assign(labels[lang].effect,effect);Object.assign(labels[lang],rest);
  }
  const l=labels[document.documentElement.lang.startsWith('en')?'en':'zh'];
  const badge=t=>`<span class="badge">${esc(t)}</span>`;
  const entryLink=(id,n,from='')=>`<button type="button" class="link-button" data-chain-entry="${esc(id)}" data-chain-from="${esc(from||id)}">${esc(n||id)}</button>`;
  const sourceLink=ev=>`<details class="chain-evidence"><summary>${esc(l.evidence)}</summary><blockquote>${esc(ev.raw)}</blockquote><a href="/source?${esc(new URLSearchParams({path:ev.path,start:ev.span[0],end:ev.span[1]}))}#evidence" target="_blank" rel="noopener">${esc(l.evidence)} ↗</a></details>`;
  function expression(v){
    if(!v)return l.notParsed;
    if(v.kind==='constant')return String(v.value)+(v.unit==='foot'?' '+l.foot:'');
    if(v.kind==='quantity_ref')return l.referencedQuantity+' '+(v.effect_id||l.notParsed)+' × '+v.factor.numerator+'/'+v.factor.denominator;
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
  function target(t,nodes,from){
    const r=t.resolution;
    if(r?.status==='resolved')return entryLink(r.targets[0],nodes.get(r.targets[0])?.name||t.name,from);
    return `<strong>${esc(t.name||'')}</strong> ${badge(r?.status==='ambiguous'?l.ambiguous:l.missing)}${r?.targets?.length?`<details><summary>${esc(l.options)} ${r.targets.length}</summary>${r.targets.map(id=>entryLink(id,nodes.get(id)?.name,from)).join(' ')}</details>`:''}`;
  }
  function condition(c){
    if(!c)return l.notStated;
    if(c.all||c.any){const terms=c.all||c.any;return (c.all?l.all:l.anyOf)+' ('+terms.map(condition).join(' · ')+')';}
    if(c.not)return l.not+' ('+condition(c.not)+')';
    if(c.kind==='class_level')return c.class+' '+l.level+' ≥ '+c.minimum;
    if(c.kind==='class')return c.value;
    if(c.kind==='campaign')return l.access_condition.campaign+' · '+c.name;
    if(c.kind==='requirement')return c.name+(c.qualifier?' ('+c.qualifier+')':'');
    if(c.kind==='item_state')return l.item_state[c.state];
    if(c.kind==='requires_sleep')return l.requiresSleep;
    if(c.kind==='condition_immunity')return l.immuneTo+' '+c.condition;
    if(c.kind==='wake_action')return l.wakeAction+' '+c.distance.maximum+' '+l.foot;
    if(c.kind==='level')return l.level+' ≥ '+c.minimum;
    if(c.kind==='ability_score')return c.ability+' ≥ '+c.minimum;
    if(c.kind==='attack_result')return (c.effect_id||l.notParsed)+' · '+l[c.result];
    if(c.kind==='spell_active')return l.spellActive;
    if(c.kind==='turn')return l.actor[c.subject]+' · '+l.relative[c.relative]+' · '+l.phase[c.phase];
    if(c.kind==='damage_received')return l.actor[c.subject]+' '+l.takesDamage;
    if(c.kind==='target_property')return l.property[c.property];
    if(c.kind==='can_laugh')return l.canLaugh;
    if(c.kind==='outside_spell_range')return l.outsideRange;
    if(c.kind==='cover')return l.totalCover;
    if(c.kind==='action_spent')return l.otherAction;
    if(c.kind==='within_area')return l.withinArea+' '+(c.area.effect_id||l.notParsed);
    if(c.kind==='save_result')return l.save+' '+(c.effect_id||l.notParsed)+' · '+l[c.result]+(c.occurrence?' · '+l.ordinalCheck.replace('{n}',c.occurrence):'');
    if(c.kind==='condition')return l.conditionActive+' '+c.value+' ('+(c.effect_id||l.notParsed)+')';
    if(c.kind==='combat_with')return l.combat;
    if(c.kind==='spell_ends')return l.spellEnds;
    if(c.kind==='damage_from')return l.damageEnds;
    if(c.kind==='branch_selected')return l.branchSelected+' · '+c.grant_id+' / '+c.branch_id;
    return l.notParsed;
  }
  function conditionView(c,nodes,from){
    if(c?.all||c?.any)return esc(c.all?l.all:l.anyOf)+' ('+(c.all||c.any).map(term=>conditionView(term,nodes,from)).join(' · ')+')';
    if(c?.not)return esc(l.not)+' ('+conditionView(c.not,nodes,from)+')';
    if(c?.kind==='requirement'&&c.target)return target(c.target,nodes,from)+(c.qualifier?' ('+esc(c.qualifier)+')':'');
    return esc(condition(c));
  }
  function scopeText(scope){
    if(scope.kind==='usage_branch')return scope.grant_id+' / '+scope.branch_id;
    if(scope.kind==='effect')return scope.effect_id;
    if(scope.kind==='casting_field')return l[scope.field]||scope.field;
    if(scope.kind==='acquisition'&&scope.expression)return condition(scope.expression);
    if(scope.kind==='acquisition')return scope.constraints.class_list?l.list+': '+scope.constraints.class_list.join(' / '):l.notStated;
    if(scope.grant_id)return scope.grant_id+(scope.constraints?.class_list?' · '+l.list+': '+scope.constraints.class_list.join(' / '):'');
    return Object.values(scope).flat().join(' / ')||l.notStated;
  }
  function branch(b,grantId){
    const resource=b.resource,kind=resource.consumed===false?'slot_free':resource.consumed===true?'spell_slot':'not_stated';
    return `<section class="chain-branch" data-branch="${esc(grantId+'/'+b.id)}"><h5>${esc(l.alternative)} <small>${esc(grantId+' / '+b.id)}</small></h5><dl class="detail-fields"><dt>${esc(l.fields.resource)}</dt><dd>${esc(l.resource[kind])}</dd>${resource.level?`<dt>${esc(l.slotLevel)}</dt><dd>${esc(l[resource.level]||resource.level)}</dd>`:''}${b.cast_level?`<dt>${esc(l.level)}</dt><dd>${esc(l[b.cast_level.kind])}</dd>`:''}<dt>${esc(l.uses)}</dt><dd>${b.uses?esc(expression(b.uses.quantity)+' · '+(b.uses.per==='spell'?l.perSpell:l.perGrant)):esc(l.notStated)}</dd><dt>${esc(l.fields.recovery)}</dt><dd>${esc(l.recovery[b.recovery?.event||'not_stated'])}</dd></dl>${b.frequency?`<p>${esc(l.atWill)}</p>`:''}${b.action?`<p>${esc(l.action)}：${esc(l.standardAction)}</p>`:''}${b.when?`<p>${esc(l.activation)}：${esc(condition(b.when))}</p>`:''}${b.target?`<p>${esc(l.target)}：${esc(targetDescription(b.target))}</p>`:''}${b.components?`<p>${esc(l.waived)}：${esc(b.components.waived.join(' / '))}</p>`:''}${b.evidence.map(sourceLink).join('')}</section>`;
  }
  function targetDescription(t){
    if(!t)return l.notStated;
    if(t.kind==='target_ref')return l.targetDefinition+' '+(t.effect_id||l.notParsed);
    if(t.kind==='area')return (t.measure==='radius'?l.radius+' ':'')+t.size+' '+l.foot+' '+l.shape[t.shape]+' · '+(t.chosen_by==='caster'?l.chosenByCaster:l.creature);
    if(t.kind==='choice')return t.count+' · '+t.choices.map(k=>l[k]||l.property[k]||k).join(' / ');
    return [t.count,t.kind?l[t.kind]||t.kind:'',t.visible?l.visible:'',t.within==='spell_range'?l.range:''].filter(Boolean).join(' · ');
  }
  function changeText(c){
    const value=c.operation==='subtract'?l.subtract+' '+expression(c.quantity):c.operation==='advantage'?l.advantageName:
      typeof c.value==='boolean'?l[c.value?'yes':'no']:c.value?.kind==='pairwise_distance'?l.pairwise+' '+c.value.maximum+' '+l.foot:Array.isArray(c.value)?c.value.join(' / '):c.value==='self'?l.self:c.value==='advantage'?l.advantageName:c.value==='disadvantage'?l.disadvantageName:l[c.value]||String(c.value??l.notStated);
    return (l.mechanic[c.field]||c.field)+' → '+value+(c.applies==='next_attack_against_target'?' · '+l.nextAttack:'')+(c.check?' · '+(c.check.effect_id||l.notParsed):'')+(c.applies_to?.kind==='spell_saves'?' · '+l.allSpellSaves:'');
  }
  const localLink=(subject,id,label)=>`<button type="button" class="link-button" data-chain-effect="${esc(id)}" data-chain-subject="${esc(subject)}">${esc(label||id)}</button>`;
  function scopeView(scope,subject){
    const base=scope.kind==='effect'&&subject?localLink(subject,scope.effect_id):esc(scopeText(scope));
    return base+(scope.usage||[]).map(u=>`<span class="chain-scope-conditions"><strong>${esc(u.branch_id)}</strong>${u.target!=='not_stated'?` · ${esc(l.casting_target[u.target])}`:''}${u.conditions.map(c=>`<br>${esc(c.scope.kind==='acquisition'?l.acquisition:l.activation)}：${esc(condition(c.expression))}`).join('')}</span>`).join('');
  }
  function effect(e,subject){
    let text='';
    if(e.kind==='targets')text=targetDescription(e.selection);
    if(e.kind==='attack_roll')text=l.attack_type[e.attack_type]+' · '+targetDescription(e.target);
    if(e.kind==='damage')text=expression(e.quantity)+' · '+l.damage_type[e.damage_type]+' · '+targetDescription(e.target);
    if(e.kind==='modifier'||e.kind==='restriction')text=changeText(e);
    if(e.kind==='behavior')text=l.laugh;
    if(e.kind==='check_modifier')text=(e.check.effect_id||l.notParsed)+' → '+(e.modifier==='advantage'?l.advantageName:l.disadvantageName);
    if(e.kind==='end_spell')text=e.scope==='target'?l.endTarget:l.endAll;
    if(e.kind==='save_override')text=l.allSpellSaves+' → '+l[e.result];
    if(e.kind==='target_constraint')text=l.pairwise+' '+e.constraint.maximum+' '+l.foot;
    if(e.kind==='ignite')text=l.effect.ignite;
    if(e.kind==='level_scale')text=e.levels.map(x=>l.level+' '+x.level+' → '+expression(x.quantity)).join(' · ');
    if(e.kind==='teleport')text=`${l.self} → ${l.max} ${e.destination.distance.maximum} ${l.foot} · ${l.visible} · ${l.unoccupied}`;
    if(e.kind==='healing')text=`${l.touch} · ${e.target?.count||1} ${l.creature} → ${expression(e.quantity)}`;
    if(e.kind==='saving_throw')text=targetDescription(e.target)+' → '+(e.ability||l.notParsed)+' · '+(e.check_role==='repeat'?l.repeatCheck:l.initial)+(e.occurrence?' · '+l.ordinalCheck.replace('{n}',e.occurrence):'')+(e.ability_source?' · '+l.inheritedAbility:'');
    if(e.kind==='condition')text=e.condition;
    if(e.kind==='attitude')text=l.friendly;
    if(e.kind==='awareness')text=l.aware;
    if(e.kind==='upcast')text=l.upcast.replace('{level}',e.threshold)+' → '+(e.effect.kind==='additional_targets'?`${l.additional} +${e.effect.count}`:`${l.effect[e.effect.kind]} +${expression(e.effect.quantity)}`);
    const links=(e.links||[]).filter(x=>x.target_effect_id).filter((x,i,a)=>a.findIndex(y=>y.target_effect_id===x.target_effect_id)===i);
    return `<section class="chain-effect" data-effect="${esc(e.id)}" tabindex="-1"><h4>${esc(l.effect[e.kind]||e.kind)} <small>${esc(e.id)}</small></h4><p>${esc(text)}</p>${e.when?`<p>${esc(l.when)}：${esc(condition(e.when))}</p>`:''}${e.ends?`<p>${esc(l.endsWhen)}：${esc(condition(e.ends))}</p>`:''}${e.use?`<p>${esc(l.ongoingCost)}：${esc(e.use.action==='bonus_action'?l.bonusAction:l.standardAction)} · ${esc(l.oncePerTurn)}</p>`:''}${e.initial_attack_result?`<p>${esc(e.initial_attack_result==='irrelevant'?l.independentAttack:l.unresolvedAttack)}</p>`:''}${(e.modifiers||[]).map(m=>`<p>${esc(l.when)}：${esc(condition(m.when))} → ${esc(m.effect==='advantage'?l.advantageName:m.effect)}</p>`).join('')}${links.length?`<p class="effect-links">${esc(l.localLinks)}：${links.map(x=>localLink(subject,x.target_effect_id)).join(' · ')}</p>`:''}${sourceLink(e.evidence)}</section>`;
  }
  function record(r,chain,nodes,audit){
    const v=r.value,subject=nodes.get(r.subject_id);let body='';
    if(r.kind==='definition_role')body=`<p>${esc(l.roles[v.role]||v.role)}</p>`;
    if(r.kind==='feat_access')body=`<p>${target(v.target,nodes,r.subject_id)}</p>${v.constraints.class_list?`<p>${esc(l.list)}：${esc(v.constraints.class_list.join(' / '))}</p>`:''}${sourceLink(v.target.evidence)}`;
    if(r.kind==='spell_access'){
      const pre=v.prerequisites;
      body=(pre?.status!=='not_stated'?`<p>${esc(l.prerequisites)}：${pre?.expression?conditionView(pre.expression,nodes,r.subject_id):esc(l.notParsed)}</p>${pre?.evidence?sourceLink(pre.evidence):''}`:'');
      if(v.activation)body+=`<p>${esc(l.activation)}：${esc(condition(v.activation.expression))}</p>${sourceLink(v.activation.evidence)}`;
      body+=v.grants.map(g=>{
        const t=g.target,edges=chain.edges.filter(e=>e.source_id===r.subject_id&&e.scope.grant_id===g.id&&['eligible_spell','grants_spell'].includes(e.relation)&&e.status==='resolved');
        const allowed=[...new Set(edges.map(e=>e.target_id).filter(Boolean))],c=t.constraints||{};
        const heading=t.kind==='entry'?target(t,nodes,r.subject_id):`${esc(l.choose)} ${t.count} ${esc(l.count)} · ${esc(l.notAll)}`;
        const domain=t.kind==='choice'?`<p class="muted">${esc(l.levels)}：${esc((c.levels||[]).map(n=>n===0?l.cantrip:n).join(' / '))}${c.schools?' · '+esc(l.schools+'：'+c.schools.join(' / ')):''}${c.class_list?' · '+esc(l.list+'：'+(edges[0]?.scope.constraints?.class_list||c.class_list.values).join(' / ')):''}</p>`:'';
        const choices=allowed.length?`<details class="chain-choices" ${t.kind==='entry'?'open':''}><summary>${esc(t.kind==='entry'?l.inspectSpell:l.options)} (${allowed.length})</summary><ul>${allowed.map(id=>`<li><button type="button" class="link-button" data-chain-spell="${esc(id)}" data-chain-from="${esc(r.subject_id)}" ${id===chain.selected_spell_id?'aria-current="true"':''}>${esc(nodes.get(id)?.name||id)}</button></li>`).join('')}</ul></details>`:'';
        return `<article class="chain-grant" data-grant="${esc(g.id)}"><h4>${badge(l.mode[g.mode]||l.notParsed)} ${heading} <small>${esc(g.id)}</small></h4>${domain}${g.when?`<p>${esc(l.activation)}：${esc(condition(g.when))}</p>`:''}<p>${esc(l.ability)}：${esc(ability(g.ability,v.contexts))}</p>${g.evidence.map(sourceLink).join('')}${choices}<h4>${esc(l.branches)}</h4><div class="chain-branches">${g.branches.map(b=>branch(b,g.id)).join('')}</div></article>`;
      }).join('');
      if(v.modifiers.length)body+=`<h4>${esc(l.modifiers)}</h4>${v.modifiers.map(m=>`<p>${esc(m.kind==='replace_choice'?l.gainLevel:l.repeat)}</p>${sourceLink(m.evidence)}`).join('')}`;
    }
    if(r.kind==='spell_effect'){
      body=`<dl class="detail-fields">${['action','range','components','duration'].filter(k=>v.casting[k]).map(k=>[k,v.casting[k]]).map(([k,c])=>`<dt>${esc(l[k]||k)}</dt><dd>${esc(c.raw)}${sourceLink(c.evidence)}</dd>`).join('')}</dl>${v.effects.map(e=>effect(e,r.subject_id)).join('')}`;
    }
    if(v.unparsed?.length)body+=`<details class="chain-gaps"><summary>${esc(l.unparsed)} (${v.unparsed.length})</summary>${v.unparsed.map(g=>sourceLink(g.evidence)).join('')}</details>`;
    if(v.rules?.length)body+=`<details class="chain-rules"><summary>${esc(l.generalRules)}</summary>${v.rules.map(link=>`<p>${esc(l[link.relation])}：${target(link.target,nodes,r.subject_id)}</p><p>${esc(l.scopeLabel)}：${scopeView(link.scope,r.subject_id)}</p>${link.when?`<p>${esc(l.when)}：${esc(condition(link.when))}</p>`:''}${link.ends?`<p>${esc(l.endsWhen)}：${esc(condition(link.ends))}</p>`:''}${link.change?`<p>${esc(l.change)}：${esc(changeText(link.change))}</p>`:''}${sourceLink(link.target.evidence)}`).join('')}</details>`;
    return `<section class="knowledge-record" data-knowledge="${esc(r.id)}" data-subject="${esc(r.subject_id)}"><header><h3>${esc(l.kinds[r.kind])} · ${entryLink(r.subject_id,subject?.name)}</h3>${badge(l.status[r.status])}${audit?badge(l.review[r.review_status]):''}</header>${body}<div class="detail-links"><a href="/read?entry=${encodeURIComponent(r.subject_id)}" target="_blank" rel="noopener">${esc(l.reader)} ↗</a></div>${audit?`<details><summary>${esc(l.raw)}</summary><p>${esc(l.confirmHint)}</p><code>${esc(r.id)}</code><a href="/api/knowledge-inspect?id=${encodeURIComponent(r.id)}" target="_blank" rel="noopener"> JSON ↗</a><pre class="body-text">${esc(JSON.stringify(r,null,2))}</pre></details>`:''}</section>`;
  }
  function readingView(c,nodes){
    if(!c.reading)return '';
    const r=c.reading;
    const breadcrumb=`<nav class="chain-path" aria-label="${esc(l.path)}">${r.path.map((id,index)=>`<button type="button" class="link-button" data-chain-path="${esc(JSON.stringify(r.path.slice(0,index+1)))}" ${index===r.path.length-1?'aria-current="step"':''}>${esc(nodes.get(id)?.name||id)}</button>`).join(' → ')}</nav>`;
    const steps=r.steps.slice(-1).map(step=>`<section class="chain-step"><h3>${esc(l.step)}</h3>${step.relations.map(e=>`<p>${esc(l[e.relation]||e.relation)} · ${badge(l.status[e.status]||l[e.status]||l.notParsed)}${e.target_status&&e.target_status!==e.status?' · '+badge(l[e.target_status]||l.status[e.target_status]||l.notParsed):''} · ${scopeView(e.scope,c.records.find(r=>r.id===e.record_id)?.subject_id)}</p>${e.when?`<p>${esc(condition(e.when))}</p>`:''}${e.ends?`<p>${esc(l.endsWhen)}：${esc(condition(e.ends))}</p>`:''}${e.change?`<p>${esc(l.change)}：${esc(changeText(e.change))}</p>`:''}${sourceLink(e.evidence)}${(e.acquisition||[]).map(a=>sourceLink(a.evidence)).join('')}`).join('')}</section>`).join('');
    const coverage=`<details class="chain-coverage"><summary>${esc(l.coverage)}</summary>${c.segments.map(s=>`<p>${entryLink(s.subject_id,nodes.get(s.subject_id)?.name)} · ${esc(l.kinds[s.kind])} · ${esc(l.content)}：${badge(l.status[s.content_status])} · ${badge(l.review[s.review_status])}${s.rule_targets.some(t=>t.status!=='resolved')?' · '+esc(l.unresolvedRules):''}</p>`).join('')}</details>`;
    const rules=c.edges.filter(e=>e.source_id===r.focus_id&&e.target_id&&e.relation==='modified_by');
    const reverse=rules.length?`<details class="chain-modifiers"><summary>${esc(l.modified_by)} (${rules.length})</summary>${rules.map(e=>`<p>${entryLink(e.target_id,nodes.get(e.target_id)?.name,e.source_id)} · ${badge(l.status[e.status]||l[e.status]||l.notParsed)}${e.target_status&&e.target_status!==e.status?' · '+badge(l[e.target_status]||l.status[e.target_status]||l.notParsed):''} · ${scopeView(e.scope,c.records.find(r=>r.id===e.record_id)?.subject_id)}</p>${sourceLink(e.evidence)}`).join('')}</details>`:'';
    const bindings=c.bindings.filter((b,i,all)=>all.findIndex(x=>x.subject_id===b.subject_id&&x.grant_id===b.grant_id)===i);
    const variables=bindings.length?`<details class="chain-bindings"><summary>${esc(l.binding)}</summary>${bindings.map(b=>`<p>${entryLink(b.subject_id,nodes.get(b.subject_id)?.name)} → ${esc(b.value?ability(b.value):l.unknownBinding)}</p>${b.evidence?sourceLink(b.evidence):''}`).join('')}</details>`:'';
    return breadcrumb+`<p class="muted">${esc(l.readingHint)}</p>`+steps+coverage+variables+reverse+(r.text?`<details class="chain-source"><summary>${esc(l.sourceText)} · ${esc(nodes.get(r.focus_id)?.name)}</summary><pre class="body-text">${esc(r.text)}</pre><a href="/read?entry=${encodeURIComponent(r.focus_id)}" target="_blank" rel="noopener">${esc(l.reader)} ↗</a></details>`:'');
  }
  function pathTo(c,target,from){
    if(!c.reading)return [target];
    const root=c.reading.root_id;
    const find=id=>{const queue=[[root]],seen=new Set();while(queue.length){const path=queue.shift(),last=path.at(-1);if(last===id)return path;if(seen.has(last))continue;seen.add(last);for(const e of c.edges)if(e.source_id===last&&e.target_id&&!path.includes(e.target_id))queue.push([...path,e.target_id]);}return null;};
    if(target===from)return c.reading.path.includes(target)?c.reading.path.slice(0,c.reading.path.indexOf(target)+1):find(target);
    const prefix=c.reading.path.includes(from)?c.reading.path.slice(0,c.reading.path.indexOf(from)+1):find(from);
    if(!prefix)return find(target);
    return prefix.includes(target)?prefix.slice(0,prefix.indexOf(target)+1):[...prefix,target];
  }
  function render(entry,audit){
    const c=entry.rule_chain||{records:[],nodes:[],options:[],incoming:[]},nodes=new Map(c.nodes.map(n=>[n.id,n]));
    return `<div class="rule-chain"><p class="muted">${esc(l.scope)}</p>${readingView(c,nodes)}<a href="/api/knowledge-export?id=${encodeURIComponent(entry.id)}${c.selected_spell_id?'&spell='+encodeURIComponent(c.selected_spell_id):''}${c.reading?'&path='+encodeURIComponent(JSON.stringify(c.reading.path)):''}${c.reading?.effect_id?'&effect='+encodeURIComponent(c.reading.effect_id):''}" download>${esc(l.export)}</a>${c.records.length?[...c.records].sort((a,b)=>(a.subject_id===entry.id?0:a.kind==='spell_access'?1:2)-(b.subject_id===entry.id?0:b.kind==='spell_access'?1:2)).map(r=>record(r,c,nodes,audit)).join(''):`<p>${esc(l.empty)}</p>`}${c.options.some(o=>o.selection==='choice')&&!c.selected_spell_id?`<p>${esc(l.noPreview)}</p>`:''}${c.incoming.length?`<details><summary>${esc(l.incoming)} (${c.incoming.length})</summary>${c.incoming.map(x=>entryLink(x.entry_id,`${x.name} · ${x.ruleset==='5e'?'2014':'2024'}`,entry.id)).join(' ')}</details>`:''}</div>`;
  }
  function description(raw){
    try{return l.filter+'：'+Object.entries(JSON.parse(raw)).map(([k,v])=>k==='spell'?l.spell+' '+v:l[k]?.[v]||v).join(' · ');}catch{return l.filter;}
  }
  function install({params,changed,api}){
    const box=document.querySelector('#casting-filters');let last='',generation=0,countTimer,countRequest;
    box.innerHTML=`<summary>${esc(l.filter)}</summary><p class="muted">${esc(l.hint)}</p><p class="muted">${esc(l.sameEffect)}</p><div class="casting-controls">${Object.entries(l.fields).map(([key,label])=>`<label>${esc(label)}<select data-casting="${key}" aria-label="${esc(label)}"><option value="">${esc(l.any)}</option>${Object.entries(l[key]).map(([value,text])=>`<option value="${esc(value)}">${esc(text)}</option>`).join('')}</select></label>`).join('')}<label>${esc(l.spell)}<input type="text" data-casting="spell" maxlength="200" aria-label="${esc(l.spell)}"></label></div><div class="casting-actions"><button type="button" data-casting-apply>${esc(l.apply)}</button><button type="button" data-casting-clear>${esc(l.clear)}</button></div>`;
    const read=()=>Object.fromEntries([...box.querySelectorAll('[data-casting]')].filter(el=>el.value.trim()).map(el=>[el.dataset.casting,el.value.trim()]));
    box.querySelector('[data-casting-apply]').onclick=()=>{const value=read();if(Object.keys(value).length)params().set('casting',JSON.stringify(value));else params().delete('casting');params().delete('not_casting');changed();};
    box.querySelector('[data-casting-clear]').onclick=()=>{params().delete('casting');params().delete('not_casting');changed();};
    box.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.matches('input')){event.preventDefault();box.querySelector('[data-casting-apply]').click();}});
    async function counts(gen){
      const q=new URLSearchParams(params());const value=read();q.delete('not_casting');
      if(Object.keys(value).length)q.set('casting',JSON.stringify(value));else q.delete('casting');
      countRequest=new AbortController();
      try{const result=await api('/api/casting-facets?'+q,countRequest.signal);if(gen!==generation)return;
        for(const [key,items] of Object.entries(result.groups))for(const item of items){const option=[...box.querySelector(`[data-casting="${key}"]`).options].find(o=>o.value===item.value);if(option)option.textContent=l[key][item.value]+` (${item.count})`;}
      }catch{/* Main query owns error presentation; counts never replace edited values. */}
    }
    function scheduleCounts(){clearTimeout(countTimer);countRequest?.abort();const gen=++generation;if(box.open)countTimer=setTimeout(()=>counts(gen),180);}
    box.addEventListener('toggle',scheduleCounts);box.addEventListener('change',scheduleCounts);
    return {sync(){const raw=params().get('casting')||params().get('not_casting')||'';if(raw!==last){let value={};try{value=raw?JSON.parse(raw):{};}catch{}for(const input of box.querySelectorAll('[data-casting]'))input.value=value[input.dataset.casting]||'';if(raw)box.open=true;last=raw;}scheduleCounts();}};
  }
  return {labels:l,render,description,install,pathTo};
})();
