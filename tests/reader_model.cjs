const assert=require('node:assert/strict');
const model=require('../web/reader-model.js');
const node=(id,parent_id,path='',fragment='',status=path?'present':'placeholder')=>({id,parent_id,path,fragment,status,label:id});
const rows=[node('root','','topics/root.htm'),node('a','root','topics/a.htm'),node('group','root'),
  node('b','group','topics/b.htm','one'),node('alias','group','topics/b.htm','two'),node('missing','group','topics/missing.htm','','missing'),
  node('c','missing','topics/c.htm'),node('outside','','topics/outside.htm'),node('other_alias','outside','topics/b.htm'),node('empty','')];
const index=model.index(rows),scope=index.scope('root');
assert.deepEqual(scope.pages.map(x=>x.path),['topics/root.htm','topics/a.htm','topics/b.htm','topics/c.htm']);
assert.deepEqual(scope.byPath.get('topics/b.htm').nodes.map(x=>[x.id,x.fragment]),[['b','one'],['alias','two']]);
assert.equal(scope.memberIds.has('outside'),false);
assert.deepEqual(index.scope('a').pages.map(x=>x.path),['topics/a.htm']);
assert.deepEqual(index.scope('group').pages.map(x=>x.path),['topics/b.htm','topics/c.htm']);
assert.equal(index.scope('empty').pages.length,0);
assert.deepEqual(index.scope('outside').pages.map(x=>x.path),['topics/outside.htm','topics/b.htm']);
assert.equal(index.scope('','topics/unlisted.htm').pages[0].node,'');
assert.throws(()=>index.scope('unknown'),/invalid_location/);
assert.throws(()=>model.index([node('a','missing')]),/invalid_location/);
assert.throws(()=>model.index([node('a',''),node('a','')]),/invalid_location/);

// A remembered place names its collection and holds while that collection's build is the one it was saved in.
const shelf=model.index([node('5echm:root','','5echm:topics/root.htm'),node('5echm:b','5echm:root','5echm:topics/b.htm'),
  node('brew:n','','brew:books/n/0.html')]);
const builds={'5echm':'s1',brew:'b1'},snapshotOf=key=>builds[key]||'';
const valid={version:2,snapshot:'s1',root:'5echm:root',page:'5echm:topics/b.htm',node:'5echm:b',position:{x:0,y:900}};
assert.deepEqual(model.remembered(valid,snapshotOf,shelf),valid);
const brew={version:2,snapshot:'b1',root:'brew:n',page:'brew:books/n/0.html',node:'brew:n',position:{x:0,y:0}};
assert.deepEqual(model.remembered(brew,snapshotOf,shelf),brew);
for(const change of [{version:3},{snapshot:'old'},{snapshot:'b1'},{root:'brew:n'},{node:'brew:n'},{root:'unknown'},{page:'topics/b.htm'},
  {page:'5echm:topics/../root.htm'},{page:'5echm:elsewhere/b.htm'},{page:'ghost:topics/b.htm'},
  {position:{x:0,y:-1}},{position:{x:0,y:NaN}},{position:{x:Infinity,y:0}},{position:{x:0,y:'900'}},{node:null}])
  assert.equal(model.remembered({...valid,...change},snapshotOf,shelf),null,JSON.stringify(change));
const old={version:1,snapshot:'s1',page:'5echm:topics/b.htm',node:'5echm:b',position:{x:0,y:900}};
assert.equal(model.remembered(old,snapshotOf,shelf),null);
console.log('reader scope and position model passed');
