// Shelf addresses are qualified; linked content URLs and Anna's query entry IDs are local.
const bookAddress=local=>local?'5echm:'+local:'';
const localAddress=address=>address.slice(address.indexOf(':')+1);
const bookCollection=shelf=>shelf.collections.find(c=>c.id==='5echm');
const bookNode=row=>({...row,id:bookAddress(row.id),parent_id:bookAddress(row.parent_id),path:bookAddress(row.path)});

module.exports={bookAddress,localAddress,bookCollection,bookNode};
