const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const stores=new Map();
function request(value){const q={};queueMicrotask(()=>{q.result=value;q.onsuccess?.();});return q;}
const database={objectStoreNames:{contains:n=>stores.has(n)},createObjectStore(n){stores.set(n,new Map());},close(){},transaction(name){const tx={objectStore(){return {get:key=>request(structuredClone(stores.get(name).get(key))),put(value,key){stores.get(name).set(key,structuredClone(value));queueMicrotask(()=>tx.oncomplete?.());}};}};return tx;}};
const location={origin:'http://localhost:8768',pathname:'/evaluation',search:'?panel=process',href:'http://localhost:8768/evaluation?panel=process'};
const sandbox={window:{},location,URL,crypto:require('node:crypto').webcrypto,structuredClone,indexedDB:{open(){const q={};queueMicrotask(()=>{q.result=database;q.onupgradeneeded?.();q.onsuccess?.();});return q;}}};
vm.runInNewContext(fs.readFileSync(require('node:path').resolve(__dirname,'../../frontend/workbench.js'),'utf8'),sandbox);
const wb=sandbox.window.PvWorkbench;
(async()=>{
 assert.equal(wb.safeReturn('https://example.com/evaluation'),'/evaluation');
 assert.equal(wb.safeReturn('//example.com/evaluation'),'/evaluation');
 assert.equal(wb.safeReturn('/risk-lab'),'/evaluation');
 assert.equal(wb.safeReturn('/evaluation?panel=report'),'/evaluation?panel=report');
 const original={work_id:'case-a',full_case:{case_id:'test-case',component_profile:{wind_load_pa:300}},item:{file:new Blob([new Uint8Array([1,2,3])],{type:'image/jpeg'})}};
 const target=await wb.handoff('risk',original);
 original.full_case.component_profile.wind_load_pa=999;
 location.href=location.origin+target;
 const imported=await wb.incoming('risk');
 assert.equal(imported.full_case.component_profile.wind_load_pa,300,'Handoff freezes the original run parameters');
 assert.equal(imported.return_url,'/evaluation?panel=process&work=case-a','Return links identify the original case, not the newest workspace');
 assert.equal(imported.item.file.size,3,'File bytes survive persistence');
 await assert.rejects(()=>wb.incoming('image'),/调试来源不存在/,'Wrong module must not consume the snapshot');
 location.href=location.origin+'/risk-lab?context=missing';
 await assert.rejects(()=>wb.incoming('risk'),/调试来源不存在/);
 console.log('Workbench context isolation, file retention and return-path checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
