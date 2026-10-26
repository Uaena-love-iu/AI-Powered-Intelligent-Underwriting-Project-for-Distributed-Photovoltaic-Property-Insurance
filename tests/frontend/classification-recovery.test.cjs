const test = require('node:test');
const assert = require('node:assert/strict');
const {requestClassification, classificationFailures, confirmCategory, prepareItems} = require('../../frontend/material-files.js');
const photo = () => new File([Uint8Array.from([1,2,3,4])], '铭牌.png', {type:'image/png'});
const result = () => new Response(JSON.stringify({category:'component_nameplate',reason:'可见型号',confidence:.9}), {status:200});

test('Safari Load failed retries with the complete file and a fresh multipart body', async () => {
  const item={file:photo()};let attempts=0;let firstBody;
  const data=await requestClassification(item,{allowedCategories:['component_nameplate'],fetchImpl:async(url,options)=>{
    assert.equal(url,'/api/v1/vision/lab/classify');
    assert.deepEqual([...new Uint8Array(await options.body.get('file').arrayBuffer())],[1,2,3,4]);
    attempts++;
    if(attempts===1){firstBody=options.body;throw new TypeError('Load failed');}
    assert.notEqual(options.body,firstBody);return result();
  }});
  assert.equal(attempts,2);assert.equal(data.category,'component_nameplate');
});

test('repeated connection failures produce an actionable error instead of silent routing', async()=>{
  const item={file:photo(),category:'component_nameplate',categorySource:'unclassified'};let attempts=0;
  await assert.rejects(requestClassification(item,{fetchImpl:async()=>{attempts++;throw new TypeError('Load failed');}}),/连接中断.*人工确认/);
  assert.equal(attempts,2);assert.equal(classificationFailures([item]).length,1);
});

test('confirming the already selected category clears restored failures and stale module output',()=>{
  const item={file:photo(),category:'component_nameplate',categorySource:'unclassified',categoryNote:'自动分类失败：Load failed',classificationError:'Load failed',status:'done',visionResults:[{detection_status:'detected'}],ocrResults:[{}]};
  assert.equal(classificationFailures([item]).length,1);
  confirmCategory(item,'component_nameplate','组件铭牌');
  const restored=structuredClone(item);
  assert.equal(classificationFailures([restored]).length,0);
  assert.equal(restored.classificationError,null);assert.equal(restored.categorySource,'manual');
  assert.deepEqual(restored.visionResults,[]);assert.deepEqual(restored.ocrResults,[]);
});

test('PDF failures require retry or manual confirmation like image failures',()=>{
  const legacy={file:photo(),categorySource:undefined,categoryNote:'自动分类失败：Load failed'};
  const good={file:photo(),categorySource:'automatic',categoryNote:'模型自动分类：组件铭牌'};
  const pdf={file:new File([1],'备案.pdf',{type:'application/pdf'}),classificationError:'old error'};
  assert.deepEqual(classificationFailures([legacy,good,pdf]),[legacy,pdf]);
});

test('unreadable files never reach the model; unknown categories and backend errors are not retried',async()=>{
  let attempts=0;const fetchImpl=async()=>{attempts++;return result();};
  await assert.rejects(requestClassification({file:new File([],'empty.png')},{fetchImpl}),/重新选择/);assert.equal(attempts,0);
  await assert.rejects(requestClassification({file:photo()},{fetchImpl,allowedCategories:['panorama']}),/类别无效/);assert.equal(attempts,1);
  attempts=0;
  await assert.rejects(requestClassification({file:photo()},{fetchImpl:async()=>{attempts++;return new Response(JSON.stringify({detail:'模型服务未配置'}),{status:503});}}),/503.*未配置/);assert.equal(attempts,1);
});

test('a stalled classification is aborted and leaves manual recovery available',async()=>{
  let attempts=0;
  await assert.rejects(requestClassification({file:photo()},{timeoutMs:10,fetchImpl:async(url,{signal})=>{
    attempts++;return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}));
  }}),/超时.*人工确认/);
  assert.equal(attempts,1);
});

test('persisted raw bytes recover a Safari File that can no longer be read after reload',async()=>{
  const storedBytes=structuredClone(Uint8Array.from([1,2,3,4]).buffer);
  const stale={name:'铭牌.png',type:'image/png',size:4,arrayBuffer:async()=>{throw new DOMException('Not readable','NotReadableError');}};
  const item={file:stale,fileBytes:storedBytes};
  await prepareItems([item]);
  const reloaded=structuredClone(item);
  await prepareItems([reloaded]);
  assert.deepEqual([...new Uint8Array(await reloaded.file.arrayBuffer())],[1,2,3,4]);
  const data=await requestClassification(reloaded,{fetchImpl:async(url,{body})=>{
    assert.deepEqual([...new Uint8Array(await body.get('file').arrayBuffer())],[1,2,3,4]);return result();
  }});
  assert.equal(data.category,'component_nameplate');
});

test('an incomplete raw-byte backup is rejected without replacing any other item',async()=>{
  const intact={file:photo()};const original=intact.file;
  const broken={file:photo(),fileBytes:Uint8Array.from([1]).buffer};
  await assert.rejects(prepareItems([intact,broken]),/已保存内容不完整/);
  assert.equal(intact.file,original);
});
