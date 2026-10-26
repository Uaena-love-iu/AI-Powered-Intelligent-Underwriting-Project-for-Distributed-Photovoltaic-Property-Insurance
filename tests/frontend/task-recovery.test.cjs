const test=require('node:test');
const assert=require('node:assert/strict');
const recovery=require('../../frontend/task-recovery.js');

test('进度断线只重连原任务，不重复提交',async()=>{
  const urls=[],seen=[];let calls=0;
  const result=await recovery.poll('original',j=>seen.push(j.status),{wait:async()=>{},load:async url=>{
    urls.push(url);calls++;if(calls<3)throw Error('offline');return {status:'completed',result:{}};
  }});
  assert.equal(result.status,'completed');assert.equal(calls,3);
  assert.ok(urls.every(url=>url==='/api/v1/underwriting/jobs/original'));
  assert.deepEqual(seen,['completed']);
});
test('持续断线到达边界，保留后台状态未知',async()=>{
  let calls=0,updates=0;
  await assert.rejects(recovery.poll('j',()=>updates++,{wait:async()=>{},load:async()=>{calls++;throw Error('offline');}}),/offline/);
  assert.equal(calls,3);assert.equal(updates,0);
});
test('停止与失败是终止状态，不能轮询到完成后冒充成功',async()=>{
  for(const status of ['failed','cancelled']){
    const result=await recovery.poll('j',()=>{},{load:async()=>({status}),wait:async()=>{throw Error('unexpected wait');}});
    assert.equal(result.status,status);
  }
});
test('页面超时不修改后台任务状态',async()=>{
  const seen=[];
  await assert.rejects(recovery.poll('j',job=>seen.push(job.status),{maxPolls:2,wait:async()=>{},load:async()=>({status:'running'})}),/后台任务未被停止/);
  assert.deepEqual(seen,['running','running']);
});
test('权限错误直接提示，不进行无意义轮询',async()=>{
  let calls=0;
  await assert.rejects(recovery.poll('j',()=>{},{wait:async()=>{},load:async()=>{calls++;throw Object.assign(Error('权限'),{status:403});}}));
  assert.equal(calls,1);
});
