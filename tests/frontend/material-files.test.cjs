const assert=require('node:assert/strict');
const {snapshotFile,prepareItems}=require('../../frontend/material-files.js');
(async()=>{
 let available=true;
 const native={name:'first.jpg',type:'image/jpeg',size:4,lastModified:1,arrayBuffer:async()=>available?Uint8Array.from([1,2,3,4]).buffer:new ArrayBuffer(0)};
 const first=await snapshotFile(native);
 available=false; // selecting additional files invalidates the original native handle
 const items=[{file:first},{file:new File([Uint8Array.from([5,6])],'second.jpg',{type:'image/jpeg'})}];
 await prepareItems(items);
 for(let run=0;run<2;run++){
  const form=new FormData();items.forEach(i=>form.append('files',i.file));
  const files=form.getAll('files');
  assert.deepEqual([...new Uint8Array(await files[0].arrayBuffer())],[1,2,3,4]);
  assert.deepEqual([...new Uint8Array(await files[1].arrayBuffer())],[5,6]);
 }
 const original=items[0].file;
 await assert.rejects(prepareItems([items[0],{file:native}]),/为空或不完整/);
 assert.equal(items[0].file,original,'Failed preflight must not partially replace existing files');
 await assert.rejects(snapshotFile(new File([],'empty.jpg')),/重新选择/);
 const persisted=structuredClone(first);
 assert.deepEqual([...new Uint8Array(await persisted.arrayBuffer())],[1,2,3,4]);
 console.log('Repeated multipart upload, native handle invalidation, persistence and empty-file guards passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
