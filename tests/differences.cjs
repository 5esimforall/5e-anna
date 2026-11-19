const assert=require('node:assert/strict');
const differences=require('../web/differences.js');
const words=text=>differences.tokens(text).map(t=>t.text);
// Each CJK character and each Latin word or number is one word; spacing is not compared.
assert.deepEqual(words('价值 3,000 gp'),['价','值','3',',','000','gp']);
assert.deepEqual(differences.tokens('a  bc').map(t=>[t.start,t.end]),[[0,1],[3,5]]);
// A copy that adds a price: one run only in the other text, placed before the word it precedes.
const shown=words('同调 需要同调 重量 4 磅'),priced=words('同调 需要同调 价值 3,000 gp 重量 4 磅');
assert.deepEqual(differences.compare(shown,priced),[{kind:'other',at:6,from:6,to:12}]);
// A changed school: the shown words and the other's words at the same place.
assert.deepEqual(differences.compare(words('6 环 死灵 施法时间 1 动作'),words('6 环 幻术 施法时间 1 动作')),[{kind:'own',from:2,to:4},{kind:'other',at:4,from:2,to:4}]);
// The same change in a short text is too large a share of it to mark.
assert.equal(differences.compare(words('6 环 死灵'),words('6 环 幻术')),null);
// Equal texts have no runs; texts that share too little are not marked at all.
assert.deepEqual(differences.compare(words('完全相同'),words('完全相同')),[]);
assert.deepEqual(differences.compare([],[]),[]);
assert.equal(differences.compare(words('abc def ghi'),words('uvw xyz')),null);
assert.equal(differences.compare(words('甲乙丙丁戊己庚辛'),words('子丑寅卯辰巳午未')),null);
console.log('difference runs passed');
