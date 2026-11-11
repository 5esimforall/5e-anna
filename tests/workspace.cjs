/* The shared workspace record: per-snapshot validity, merging of remembered places, and what a companion offers. */
const assert=require('node:assert/strict');
const workspace=require('../web/workspace.js');
const values=new Map();
workspace.useStorage({get:key=>values.has(key)?values.get(key):null,set:(key,value)=>{value===null?values.delete(key):values.set(key,value);return true;}});
const snap='s1';
const book=(root,y=10,extra={})=>({root,page:`topics/${root}.htm`,node:root,position:{x:0,y},label:root,...extra});
const query=(q,extra={})=>({query:q,entry:'',entryName:'',title:q,ui:{tableTop:40},...extra});

// Missing, malformed and unknown records read as an empty workspace for the current snapshot.
for(const raw of [null,'{','[]','{"version":2,"snapshot":"s1"}','{"version":1}'])
  assert.deepEqual(workspace.normalize(raw==='{'?raw:JSON.parse(raw),snap),{version:1,snapshot:snap,topic:null,query:[],book:[]});
values.set(workspace.key,'{');
assert.deepEqual(workspace.read(snap).book,[]);

// Malformed memories and topics are dropped one by one; valid ones keep only known fields.
let state=workspace.normalize({version:1,snapshot:snap,topic:{entry:'',from:'query'},
  query:[query('type=spell'),{query:5},null,{...query('q=x'),extra:true}],
  book:[book('a'),book('b',-1),{...book('c'),page:3},{...book('d'),position:{x:0,y:'9'}}]},snap);
assert.equal(state.topic,null);
assert.deepEqual(state.query.map(m=>m.query),['type=spell','q=x']);
assert.equal('extra' in state.query[1],false);
assert.deepEqual(state.book.map(m=>m.root),['a']);
assert.equal(state.book[0].snapshot,snap);
assert.equal(workspace.normalize({version:1,snapshot:snap,topic:{entry:'e',from:'elsewhere'}},snap).topic,null);

// Places are newest first, merged by query conditions or reading scope, and limited to five per companion.
state=workspace.normalize(null,snap);
for(const root of ['a','b','c','d','e','f'])state=workspace.rememberBook(state,book(root));
assert.deepEqual(state.book.map(m=>m.root),['f','e','d','c','b']);
state=workspace.rememberBook(state,book('c',400,{label:'third'}));
assert.deepEqual(state.book.map(m=>m.root),['c','f','e','d','b']);
assert.deepEqual([state.book[0].position.y,state.book[0].label],[400,'third']);
assert.equal(workspace.rememberBook(state,book('x',-5)),state,'an invalid place changes nothing');
// Refining a query replaces it until an entry was opened in it; after that it stays among the places.
state=workspace.rememberQuery(state,query('type=spell'));
state=workspace.rememberQuery(state,query('type=creature'));
assert.deepEqual(state.query.map(m=>m.query),['type=creature']);
state=workspace.rememberQuery(state,query('type=spell',{entry:'fireball',entryName:'火球术'}));
state=workspace.rememberQuery(state,query('type=creature'));
state=workspace.rememberQuery(state,query('type=creature&q=x'));
assert.deepEqual(state.query.map(m=>[m.query,m.entry,m.opened]),[['type=creature&q=x','',false],['type=spell','fireball',true]]);
state=workspace.rememberQuery(state,query('type=spell'));
assert.deepEqual(state.query.map(m=>[m.query,m.entry,m.opened]),[['type=spell','',true]],'a passing refinement gives way; the closed entry leaves its query worth returning to');
// Pages outside the contents have no directory node; each is its own scope.
let loose=workspace.rememberBook(workspace.normalize(null,snap),book('',5,{page:'topics/x.htm',node:''}));
loose=workspace.rememberBook(loose,book('',6,{page:'topics/y.htm',node:''}));
assert.deepEqual(loose.book.map(workspace.memoryId.book),['topics/y.htm','topics/x.htm']);
assert.equal(workspace.limit,5);

// Another snapshot keeps query conditions, without the entries, names and restored views that named old builds.
state=workspace.setTopic(state,{entry:'fireball',name:'火球术',from:'query',at:5});
const rebuilt=workspace.normalize(JSON.parse(JSON.stringify(state)),'s2');
assert.equal(rebuilt.snapshot,'s2');
assert.deepEqual(rebuilt.query.map(m=>[m.query,m.entry,m.entryName,m.ui]),[['type=spell','','',null]]);
assert.deepEqual([rebuilt.book,rebuilt.topic],[[],null]);

// Turning to a companion offers a topic shown elsewhere since the last visit, then her latest place, then her home.
const at=(memory,time)=>({...memory,at:time});
let shared=workspace.normalize(null,snap);
assert.deepEqual(workspace.offer(shared,'book'),{kind:'home'});
shared=workspace.rememberBook(shared,at(book('a'),10));
assert.equal(workspace.offer(shared,'book').kind,'resume');
shared=workspace.setTopic(shared,{entry:'fireball',name:'火球术',from:'query',at:20});
assert.deepEqual(workspace.offer(shared,'book'),{kind:'topic',topic:shared.topic,memory:shared.book[0]});
assert.equal(workspace.offer(shared,'query').kind,'home','a companion is never offered her own topic');
shared=workspace.rememberBook(shared,at(book('a'),30));
assert.equal(workspace.offer(shared,'book').kind,'resume','a later visit has already seen the topic');
shared=workspace.setTopic(shared,{entry:'fly',name:'飞行术',from:'book',at:40});
shared=workspace.rememberQuery(shared,at(query('type=spell',{entry:'fly'}),35));
assert.equal(workspace.offer(shared,'query').kind,'resume','anna already shows the topic');
shared=workspace.rememberQuery(shared,at(query('type=spell',{entry:'fireball'}),35));
assert.equal(workspace.offer(shared,'query').kind,'topic');

// Showing the topic again keeps who brought it up; the companion who closes it clears it.
let talk=workspace.setTopic(workspace.normalize(null,snap),{entry:'fireball',name:'火球术',from:'query',at:5});
assert.equal(workspace.setTopic(talk,{entry:'fireball',name:'火球术',from:'book',at:9}),talk);
assert.equal(workspace.setTopic(talk,{entry:'fly',name:'飞行术',from:'book',at:9}).topic.from,'book');
assert.equal(workspace.clearTopic(talk,'fly'),talk);
assert.equal(workspace.clearTopic(talk,'fireball').topic,null);

// Records survive a write and read; refused storage leaves an empty workspace and writes nothing.
values.clear();
let heard=0;workspace.subscribe(()=>heard++);
workspace.update(snap,current=>workspace.rememberBook(current,book('a')));
assert.deepEqual(workspace.read(snap).book.map(m=>m.root),['a']);
assert.equal(heard,1,'listeners hear updates made in this tab');
workspace.useStorage({get:()=>null,set:()=>false});
assert.deepEqual(workspace.read(snap).book,[]);
assert.deepEqual(workspace.update(snap,current=>workspace.rememberBook(current,book('b'))).book.map(m=>m.root),['b']);

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
console.log('workspace ok');
