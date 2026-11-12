// Small deterministic DOM fixture; real-browser layout checks are documented separately.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const callbacks = {}, raf = [], warnings = [];
let scrollY = 0, textTop = 100, rangeCount = 0;
const renderPhases = [];
class Element {
  constructor(tag) { this.tag=tag; this.style=new Proxy({}, {set(object,key,value){renderPhases.push('write');object[key]=value;return true;}}); this.dataset={}; this.childNodes=[]; this.events={}; this.isConnected=true; }
  append(e) { e.parentElement=this; this.childNodes.push(e); }
  setAttribute(k,v) { this[k]=v; }
  addEventListener(k,f) { this.events[k]=f; }
  remove() { this.isConnected=false; this.parentElement.childNodes=this.parentElement.childNodes.filter(e=>e!==this); }
  attachShadow() { return this.shadowRoot=new Element('shadow'); }
}
const body=new Element('body'), html=new Element('html');html.lang='zh-CN';
const node={nodeType:3,length:5,isConnected:true,parentElement:body}; body.childNodes=[node];
const refNode={nodeType:3,length:5,isConnected:true,parentElement:body};body.childNodes.push(refNode);
const spec={a:[[0],0],b:[[0],5],text:'Alpha'};
const payload={definitions:[{id:'ver_a',name:'Alpha',body:spec,title:spec}],references:[{id:'ver_a',range:{a:[[1],0],b:[[1],5],text:'Alpha'}}]};
const document={body,documentElement:html,readyState:'complete',
  getElementById:()=>({textContent:JSON.stringify(payload)}),createElement:tag=>new Element(tag),
  createRange:()=>{rangeCount++;return {
    setStart(n,o){this.startContainer=n;this.startOffset=o}, setEnd(n,o){this.endContainer=n;this.endOffset=o},
    toString(){return 'Alpha'},compareBoundaryPoints(){return 0},
    getBoundingClientRect(){return {top:textTop-scrollY,bottom:textTop-scrollY+20}},
    getClientRects(){renderPhases.push('read');return [{left:10,top:textTop-scrollY,width:50,height:20}]}
  }}
};
// Link marks paint as named text highlights; this registry stands in for CSS.highlights.
const highlights=new Map(), CSS={highlights};
class Highlight extends Set { constructor(...ranges){super(ranges);this.priority=0;} }
class StaticRange { constructor(init){Object.assign(this,init);} }
class CSSStyleSheet { replaceSync(text){this.text=text;} }
document.adoptedStyleSheets=[];
const window={CSS,addEventListener:(event,callback)=>{callbacks[event]=callback},scrollBy:(x,y)=>{scrollY+=y;callbacks.scroll?.()}};
const context=vm.createContext({document,window,CSS,Highlight,StaticRange,CSSStyleSheet,location:{hash:''},Node:{TEXT_NODE:3},Range:{START_TO_START:0},innerHeight:600,
  requestAnimationFrame:f=>raf.push(f),getComputedStyle:()=>({visibility:'visible'}),
  MutationObserver:class {constructor(cb){callbacks.mutation=cb}observe(){}},console:{warn:(...v)=>warnings.push(v)}});
vm.runInContext(fs.readFileSync(__dirname+'/../web/link-labels.js','utf8'),context);
assert.equal(vm.runInContext('JSON.stringify(Object.keys(annaLinkLabels.zh)) === JSON.stringify(Object.keys(annaLinkLabels.en))',context),true);
vm.runInContext(fs.readFileSync(__dirname+'/../book/links.js','utf8'),context);
function flush(){while(raf.length)raf.shift()()}
renderPhases.length=0;
flush();
assert.ok(renderPhases.lastIndexOf('read') < renderPhases.indexOf('write'),'batch geometry reads before overlay writes');
const root=html.childNodes[0].shadowRoot,group=root.childNodes[1],anchor=group.childNodes[0];
assert.equal(anchor.href,'/?entry=ver_a');assert.equal(anchor.style.top,'100px');
const reference=root.childNodes[2].childNodes[0];assert.equal(reference.href,'/book/definition/ver_a');
assert.equal(anchor.target,'_blank');assert.equal(anchor.rel,'noopener');
assert.equal(anchor['aria-description'],'在新标签页打开');
assert.equal(reference.target,'_self');assert.equal(reference['aria-description'],undefined);
root.activeElement=anchor;window.scrollBy(0,50);flush();
assert.equal(group.childNodes[0],anchor,'scroll must retain keyboard focus');
assert.equal(anchor.style.top,'50px','focused hit area must still follow content');
// The original text node is retained across source-table wrapper moves and fresh ranges are created.
const before=rangeCount;node.parentElement=new Element('td');textTop=140;callbacks.mutation();flush();
assert.ok(rangeCount>before);assert.equal(anchor.style.top,'90px');
textTop=1000;anchor.events.focus();flush();assert.equal(anchor.style.top,'20px');
context.location.hash='#anna-ver_a';callbacks.hashchange();flush();assert.equal(anchor.style.top,'20px');
context.location.hash='#%not-valid';callbacks.hashchange();flush();
// Marks are off until the reader asks; turning them on adds highlights and one style sheet, never DOM nodes.
const links=context.window.annaSourceLinks, bodyNodes=[...body.childNodes];
assert.equal(highlights.size,0);assert.equal(document.adoptedStyleSheets.length,0);
links.markLinks(true);
const marked=name=>[...highlights.get(name)].map(r=>[r.startContainer,r.startOffset,r.endContainer,r.endOffset]);
assert.deepEqual(marked('anna-definition'),[[node,0,node,5]]);
assert.deepEqual(marked('anna-reference'),[[refNode,0,refNode,5]]);
assert.ok(highlights.get('anna-definition').priority>highlights.get('anna-reference').priority,'definitions win where they overlap references');
assert.equal(document.adoptedStyleSheets.length,1);
assert.match(document.adoptedStyleSheets[0].text,/:root\[data-webhelp-theme=dark\] ::highlight\(anna-reference\)/);
assert.deepEqual(body.childNodes,bodyNodes);
links.markLinks('on');assert.equal(highlights.size,0,'only an explicit true marks links');
links.markLinks(true);links.markLinks(true);assert.equal(document.adoptedStyleSheets.length,1,'the style sheet is adopted once');
links.markLinks(false);assert.equal(highlights.size,0);
node.isConnected=false;callbacks.mutation();flush();assert.equal(group.childNodes.length,0);
// A browser without text highlights keeps reading and following links; the marks simply stay away.
delete CSS.highlights;links.markLinks(true);assert.equal(highlights.size,0);
assert.deepEqual(warnings,[]);
