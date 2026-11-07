const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../portfolio/static/rest_transport.js'),'utf8');
function harness(fetch) {
  const timers=new Map();
  const module={exports:{}};
  vm.runInNewContext(source,{module,fetch,AbortController,DOMException,
    setTimeout:callback=>{timers.set(1,callback);return 1;},clearTimeout:id=>timers.delete(id)});
  return {...module.exports,timers};
}
const awaitAbort=signal=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>
  reject(new DOMException('Aborted','AbortError')),{once:true}));

test('REST transport returns the complete response including HTTP errors without retry',async()=>{
  let calls=0;
  const response={status:412,text:async()=>'{"detail":"Stale version"}'};
  const h=harness(async(path,options)=>{
    calls++;assert.equal(path,'/api/v1/notes/fixture');assert.equal(options.method,'PATCH');
    assert.equal(options.body,'{}');return response;
  });
  const result=await h.send('/api/v1/notes/fixture',{method:'PATCH',body:'{}'});
  assert.equal(result.response,response);assert.equal(result.text,'{"detail":"Stale version"}');
  assert.equal(calls,1);assert.equal(h.timers.size,0);
});

test('REST deadline remains active while the response body is stalled',async()=>{
  let reading;
  const bodyStarted=new Promise(resolve=>reading=resolve);
  const h=harness(async(_path,{signal})=>({text:()=>{reading();return awaitAbort(signal);}}));
  const result=h.send('/api/v1/notes',{method:'GET'});
  await bodyStarted;
  h.timers.get(1)();
  await assert.rejects(result,error=>error.code==='timeout');
  assert.equal(h.timers.size,0);
});

test('REST cancellation aborts in flight and avoids starting pre-cancelled requests',async()=>{
  let calls=0;
  const h=harness((_path,{signal})=>{calls++;return awaitAbort(signal);});
  const controller=new AbortController();
  const result=h.send('/api/v1/notes',{method:'GET'},{signal:controller.signal});
  controller.abort();
  await assert.rejects(result,error=>error.code==='cancelled');
  await assert.rejects(h.send('/api/v1/notes',{}, {signal:controller.signal}),error=>error.code==='cancelled');
  assert.equal(calls,1);assert.equal(h.timers.size,0);
});

test('REST recovery distinguishes read failures from uncertain writes',async()=>{
  const h=harness(async()=>{throw new TypeError('Failed to fetch');});
  let failure;
  try {await h.send('/api/v1/notes',{method:'POST'});}catch(error){failure=error;}
  assert.equal(failure.code,'network');assert.equal(h.timers.size,0);
  assert.match(h.recoveryMessage(failure,'POST',true),/same Idempotency-Key and body/);
  assert.match(h.recoveryMessage(failure,'PATCH'),/write may have completed.*Fetch the current data/);
  assert.match(h.recoveryMessage(failure,'GET'),/inputs are preserved/);
  assert.doesNotMatch(h.recoveryMessage(failure,'GET'),/write may have completed/);
});
