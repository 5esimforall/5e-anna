/* The shared workspace record: per-collection validity, merging of remembered places, and what a companion offers. */
const assert=require('node:assert/strict');
const workspace=require('../web/workspace.js');
const values=new Map();
workspace.useStorage({get:key=>values.has(key)?values.get(key):null,set:(key,value)=>{value===null?values.delete(key):values.set(key,value);return true;}});
const builds={'5echm':'s1',brew:'b1'};
const book=(root,y=10,extra={})=>({root:'5echm:'+root,page:`5echm:topics/${root}.htm`,node:'5echm:'+root,position:{x:0,y},label:root,snapshot:'s1',...extra});
const query=(q,extra={})=>({query:q,entry:'',entryName:'',title:q,ui:{tableTop:40},...extra});
const empty={version:2,topic:null,query:[],book:[],tour:null};

// Addresses name their collection.
assert.equal(workspace.collectionOf('brew:books/a.html'),'brew');
assert.equal(workspace.localOf('brew:books/a.html'),'books/a.html');
for(const bare of ['','topics/a.htm','Brew:x','brew:'])assert.equal(workspace.collectionOf(bare),'',bare);

// Missing, malformed and older records read as an empty workspace.
for(const raw of [null,'{','[]','{"version":1,"snapshot":"s1"}','{"version":3}'])
  assert.deepEqual(workspace.normalize(raw==='{'?raw:JSON.parse(raw),builds),empty);
values.set(workspace.key,'{');
assert.deepEqual(workspace.read(builds).book,[]);

// Malformed memories and topics are dropped one by one; valid ones keep only known fields.
let state=workspace.normalize({version:2,topic:{entry:'',from:'query'},
  query:[query('type=spell'),{query:5},null,{...query('q=x'),extra:true}],
  book:[book('a'),book('b',-1),{...book('c'),page:3},{...book('d'),position:{x:0,y:'9'}},{...book('e'),page:'topics/e.htm'}]},builds);
assert.equal(state.topic,null);
assert.deepEqual(state.query.map(m=>m.query),['type=spell','q=x']);
assert.equal('extra' in state.query[1],false);
assert.deepEqual(state.book.map(m=>m.root),['5echm:a'],'a place outside any collection is not a place');
assert.equal(workspace.normalize({version:2,topic:{entry:'5echm:e',from:'elsewhere'}},builds).topic,null);
assert.equal(workspace.normalize({version:2,topic:{entry:'e',from:'query'}},builds).topic,null,'a topic names its collection');

// Places are newest first, merged by query conditions or reading scope, and limited to five per companion.
state=workspace.normalize(null,builds);
for(const root of ['a','b','c','d','e','f'])state=workspace.rememberBook(state,book(root));
assert.deepEqual(state.book.map(m=>m.label),['f','e','d','c','b']);
state=workspace.rememberBook(state,book('c',400,{label:'third'}));
assert.deepEqual(state.book.map(m=>m.root),['5echm:c','5echm:f','5echm:e','5echm:d','5echm:b']);
assert.deepEqual([state.book[0].position.y,state.book[0].label],[400,'third']);
assert.equal(workspace.rememberBook(state,book('x',-5)),state,'an invalid place changes nothing');
// Refining a query replaces it until an entry was opened in it; after that it stays among the places.
state=workspace.rememberQuery(state,query('type=spell'));
state=workspace.rememberQuery(state,query('type=creature'));
assert.deepEqual(state.query.map(m=>m.query),['type=creature']);
state=workspace.rememberQuery(state,query('type=spell',{entry:'5echm:fireball',entryName:'火球术',snapshot:'s1'}));
state=workspace.rememberQuery(state,query('type=creature'));
state=workspace.rememberQuery(state,query('type=creature&q=x'));
assert.deepEqual(state.query.map(m=>[m.query,m.entry,m.opened]),[['type=creature&q=x','',false],['type=spell','5echm:fireball',true]]);
state=workspace.rememberQuery(state,query('type=spell'));
assert.deepEqual(state.query.map(m=>[m.query,m.entry,m.opened]),[['type=spell','',true]],'a passing refinement gives way; the closed entry leaves its query worth returning to');
// Pages outside the contents have no directory node; each is its own scope.
let loose=workspace.rememberBook(workspace.normalize(null,builds),book('',5,{root:'',page:'5echm:topics/x.htm',node:''}));
loose=workspace.rememberBook(loose,book('',6,{root:'',page:'brew:books/y.html',node:'',snapshot:'b1'}));
assert.deepEqual(loose.book.map(workspace.memoryId.book),['brew:books/y.html','5echm:topics/x.htm']);
assert.equal(workspace.limit,5);

// A rebuilt collection drops what it named: its places, its topic and the entry of a query; the rest stays.
state=workspace.rememberBook(state,book('',7,{root:'brew:n',page:'brew:books/y.html',node:'brew:n',snapshot:'b1'}));
state=workspace.rememberQuery(state,query('type=spell',{entry:'brew:o5e_1',entryName:'火球术',snapshot:'b1'}));
state=workspace.setTopic(state,{entry:'brew:o5e_1',name:'火球术',from:'query',snapshot:'b1',at:5});
const rebuilt=workspace.normalize(JSON.parse(JSON.stringify(state)),{'5echm':'s1',brew:'b2'});
assert.deepEqual(rebuilt.book.map(m=>m.root),state.book.filter(m=>!m.root.startsWith('brew:')).map(m=>m.root));
assert.deepEqual(rebuilt.query.map(m=>[m.query,m.entry,m.entryName,m.ui]),[['type=spell','','',null]]);
assert.equal(rebuilt.topic,null);
// A page that does not know a collection keeps what it named for the pages that do.
const unknown=workspace.normalize(JSON.parse(JSON.stringify(state)),{'5echm':'s1'});
assert.equal(unknown.topic.entry,'brew:o5e_1');
assert.equal(unknown.book[0].root,'brew:n');

// Turning to a companion offers a topic shown elsewhere since the last visit, then her latest place, then her home.
const at=(memory,time)=>({...memory,at:time});
let shared=workspace.normalize(null,builds);
assert.deepEqual(workspace.offer(shared,'book'),{kind:'home'});
shared=workspace.rememberBook(shared,at(book('a'),10));
assert.equal(workspace.offer(shared,'book').kind,'resume');
shared=workspace.setTopic(shared,{entry:'5echm:fireball',name:'火球术',from:'query',snapshot:'s1',at:20});
assert.deepEqual(workspace.offer(shared,'book'),{kind:'topic',topic:shared.topic,memory:shared.book[0]});
assert.equal(workspace.offer(shared,'query').kind,'home','a companion is never offered her own topic');
shared=workspace.rememberBook(shared,at(book('a'),30));
assert.equal(workspace.offer(shared,'book').kind,'resume','a later visit has already seen the topic');
shared=workspace.setTopic(shared,{entry:'5echm:fly',name:'飞行术',from:'book',snapshot:'s1',at:40});
shared=workspace.rememberQuery(shared,at(query('type=spell',{entry:'5echm:fly',snapshot:'s1'}),35));
assert.equal(workspace.offer(shared,'query').kind,'resume','anna already shows the topic');
shared=workspace.rememberQuery(shared,at(query('type=spell',{entry:'5echm:fireball',snapshot:'s1'}),35));
assert.equal(workspace.offer(shared,'query').kind,'topic');

// Showing the topic again keeps who brought it up; the companion who closes it clears it.
let talk=workspace.setTopic(workspace.normalize(null,builds),{entry:'5echm:fireball',name:'火球术',from:'query',at:5});
assert.equal(workspace.setTopic(talk,{entry:'5echm:fireball',name:'火球术',from:'book',at:9}),talk);
assert.equal(workspace.setTopic(talk,{entry:'5echm:fly',name:'飞行术',from:'book',at:9}).topic.from,'book');
assert.equal(workspace.clearTopic(talk,'5echm:fly'),talk);
assert.equal(workspace.clearTopic(talk,'5echm:fireball').topic,null);
const located=workspace.setTopic(talk,{entry:'5echm:fireball',from:'book',bookLocation:{page:'5echm:topics/a.htm',root:'5echm:a'},at:9});
assert.deepEqual(located.topic.bookLocation,{page:'5echm:topics/a.htm',root:'5echm:a'},'a later visit adds where the topic is read');

// Records survive a write and read; refused storage leaves an empty workspace and writes nothing.
values.clear();
let heard=0;workspace.subscribe(()=>heard++);
workspace.update(builds,current=>workspace.rememberBook(current,book('a')));
assert.deepEqual(workspace.read(builds).book.map(m=>m.root),['5echm:a']);
assert.equal(heard,1,'listeners hear updates made in this tab');
workspace.useStorage({get:()=>null,set:()=>false});
assert.deepEqual(workspace.read(builds).book,[]);
assert.deepEqual(workspace.update(builds,current=>workspace.rememberBook(current,book('b'))).book.map(m=>m.root),['5echm:b']);

// A resume request belongs to one companion and is consumed once.
const session=new Map();
global.sessionStorage={getItem:key=>session.get(key)??null,setItem:(key,value)=>session.set(key,value),removeItem:key=>session.delete(key)};
workspace.requestResume('book','a');
assert.equal(workspace.takeResume('query'),null);
workspace.requestResume('book','a');
assert.equal(workspace.takeResume('book'),'a');
assert.equal(workspace.takeResume('book'),null);
global.sessionStorage={getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');},removeItem(){}};
workspace.requestResume('book','a');
assert.equal(workspace.takeResume('book'),null);

// Tour positions live in the shared workspace, drop collection levels when that collection rebuilds, and keep curated paths.
let toured=workspace.normalize(null,builds);
toured=workspace.rememberTour(toured,{path:'create/5echm:n924',title:'第一步：选择职业',snapshots:{'5echm':'s1'},at:50});
assert.deepEqual(toured.tour,{path:'create/5echm:n924',title:'第一步：选择职业',snapshots:{'5echm':'s1'},at:50});
assert.equal(workspace.rememberTour(toured,{path:'../bad',title:'bad'}).tour.path,'create/5echm:n924','invalid tour path is ignored');
assert.equal(workspace.normalize(JSON.parse(JSON.stringify(toured)),{'5echm':'s1'}).tour.path,'create/5echm:n924');
assert.equal(workspace.normalize(JSON.parse(JSON.stringify(toured)),{'5echm':'s2'}).tour,null,'rebuilt collection drops its tour level');
toured=workspace.rememberTour(toured,{path:'create',title:'创建角色',snapshots:{},at:60});
assert.equal(workspace.normalize(JSON.parse(JSON.stringify(toured)),{'5echm':'s2'}).tour.path,'create','curated topic path survives rebuilds');
toured=workspace.rememberTour(toured,{path:'create/brew:n1',title:'家酿节点',snapshots:{brew:'b1'},at:70});
assert.equal(workspace.normalize(JSON.parse(JSON.stringify(toured)),{'5echm':'s1'}).tour.path,'create/brew:n1','unknown collection tour stays for pages that know it');
assert.equal(workspace.clearTour(toured).tour,null);
console.log('workspace ok');
