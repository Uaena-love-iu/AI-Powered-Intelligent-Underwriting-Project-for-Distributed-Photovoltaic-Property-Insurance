const test=require('node:test');
const assert=require('node:assert/strict');
const {revisionFor}=require('../../frontend/case-review.js');
test('initial analysis needs no revision context',()=>assert.equal(revisionFor({}),undefined));
test('resubmission requires accountable actor and reason',()=>{
  for(const revisionDraft of [{},{actor:'x',reason:' '},{actor:' ',reason:'change'}])
    assert.throws(()=>revisionFor({fullCase:{case_id:'prior'},revisionDraft}));
});
test('round context carries prior run and correction evidence',()=>{
  const context=revisionFor({fullCase:{case_id:'prior'},revisionDraft:{actor:' test ',reason:' changed ',corrections:[{field:'component_model',evidence:'page 2'}]}});
  assert.deepEqual(context,{parent_case_id:'prior',actor:'test',reason:'changed',corrections:[{field:'component_model',evidence:'page 2'}]});
});
