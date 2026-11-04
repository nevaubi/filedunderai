import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { JSDOM } from 'jsdom';
const html=fs.readFileSync('public/index.html','utf8');
const reply=(content,finish_reason='stop',tool_calls)=>({choices:[{finish_reason,message:{role:'assistant',content,tool_calls}}],usage:{completion_tokens:100}});
const response=data=>({ok:true,json:async()=>data});
function setup(){
  const dom=new JSDOM(html,{url:'https://filedunder.ai/#research',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,context=dom.getInternalVMContext();
  w.AbortController=AbortController;w.AbortSignal=AbortSignal;w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;w.scrollTo=()=>{};w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
  // Public-data boot waits on the network boundary; every local production module is evaluated intact.
  w.fetch=(url,init)=>url==='/api/ai'?w.modelFetch(url,init):new Promise(()=>{});
  const deferred=[];
  for(const script of w.document.querySelectorAll('script')){
    const src=script.getAttribute('src');if(src&&!src.startsWith('/'))continue;
    const source=src?fs.readFileSync('public'+src,'utf8'):script.textContent;
    if(script.defer)deferred.push([source,src]);else vm.runInContext(source,context,{filename:src||'index-inline.js'});
  }
  for(const [source,src]of deferred)vm.runInContext(source,context,{filename:src});
  return {w,close:()=>w.close()};
}
function evidence(w){return w.FU_EVIDENCE.capture({ticker:'AAA',metric:'Revenue',value:100,unit:'USD',period:'FY25',periodStart:'2025-01-01',periodEnd:'2025-12-31',periodType:'annual',filedDate:'2026-02-01',sourceUrl:'https://www.sec.gov/a'});}
test('actual index script order preserves analyst and research runtime APIs and mounts Research chat',async()=>{
 const h=setup();try{await h.w.pResearch();assert.ok(h.w.document.querySelector('#rs-input'));assert.equal(h.w.document.querySelector('select[aria-label="Research depth"]').value,'detailed');assert.equal(typeof h.w.FU_RESEARCH.load,'function');assert.equal(typeof h.w.FU_RESEARCH.readTheses,'function');assert.equal(typeof h.w.FU_RESEARCH_RUNTIME.runAnalysis,'function');assert.equal(h.w.FU_RESEARCH_RUNTIME.profile().id,'detailed');}finally{h.close();}
});
test('actual chat completion keeps a final answer and whole tool exchanges beyond the former history limit',async()=>{
 const h=setup();try{const {w}=h,host=w.document.createElement('section');w.document.body.append(host);let n=0,finish;const done=new Promise(resolve=>finish=resolve);let followupRequest;
 w.modelFetch=async(url,init)=>{assert.equal(url,'/api/ai');n++;const body=JSON.parse(init.body);if(n===1)return response(reply('','tool_calls',Array.from({length:5},(_,i)=>({id:'t'+i,type:'function',function:{name:'large_source',arguments:JSON.stringify({page:i})}}))));if(n===2)return response(reply(JSON.stringify({markdown:'Completed research remains visible.',claims:[]})));followupRequest=body;return response(reply('Follow-up complete.'));};
 const panel=w.chatPanel(host,{id:'large',suggestions:[],rules:'Use sources',placeholder:'Ask',tools:()=>[{name:'large_source',execute:({page})=>({page,text:'x'.repeat(50000)})}],onDone:()=>finish()});
 panel.ask('Research five sources');await done;assert.match(host.querySelector('.msg.a').textContent,/Completed research remains visible/);assert.ok(!host.querySelector('.msg.a .note'));assert.match(host.querySelector('.resp-meta').textContent,/unverified/);
 const next=new Promise(resolve=>finish=resolve);panel.ask('Follow up on the same evidence');await next;assert.equal(n,3);assert.equal(followupRequest.messages.filter(m=>m.role==='tool').length,5);assert.ok(followupRequest.messages.some(m=>m.content==='Research five sources'));
 }finally{h.close();}
});
test('five continued chunks become one assembled answer and retain original question and evidence on follow-up',async()=>{
 const h=setup();try{const {w}=h,e=evidence(w),report=JSON.stringify({markdown:'Evidence analysis. '.repeat(2500)+`\nRevenue 100 [[E:${e.id}]]`,claims:[{evidenceId:e.id,value:100,unit:'USD'}]}),width=Math.ceil(report.length/5),chunks=Array.from({length:5},(_,i)=>report.slice(i*width,(i+1)*width));let n=0,followup;
 w.modelFetch=async(_,init)=>{n++;if(n===1)return response(reply('','tool_calls',[{id:'source',type:'function',function:{name:'source',arguments:'{}'}}]));if(n<=6)return response(reply(chunks[n-2],n===6?'stop':'length'));followup=JSON.parse(init.body);return response(reply('Follow-up'));};
 const result=await w.agentLoop({system:'Use sources',turns:[{role:'user',content:'Original research question'}],profile:'report',tools:[{name:'source',execute:()=>({evidence:e})}]});assert.equal(result.status,'checked_claims');assert.equal(result.turns.filter(t=>t.role==='user').length,1);assert.equal(result.turns.at(-1).content,report);assert.equal(result.turns.filter(t=>t.role==='tool').length,1);
 await w.agentLoop({system:'Use sources',turns:[...w.conversationWindow(result.turns),{role:'user',content:'Follow up'}],profile:'report',tools:[]});assert.ok(followup.messages.some(m=>m.content==='Original research question'));assert.equal(JSON.parse(followup.messages.find(m=>m.role==='tool').content).evidence.id,e.id);assert.ok(followup.messages.some(m=>m.role==='assistant'&&m.content===report));assert.ok(!followup.messages.some(m=>m.role==='user'&&m.content.startsWith('Continue exactly')));
 }finally{h.close();}
});
test('an unexpected history retention failure cannot erase a completed chat answer',async()=>{
 const h=setup();try{const {w}=h,host=w.document.createElement('section');w.document.body.append(host);const retain=w.conversationWindow;w.conversationWindow=turns=>{if(turns.length>1)throw new Error('retention unavailable');return retain(turns);};w.modelFetch=async()=>response(reply(JSON.stringify({markdown:'The completed answer is preserved.',claims:[]})));let finish;const done=new Promise(resolve=>finish=resolve);const panel=w.chatPanel(host,{id:'retention',suggestions:[],rules:'Answer',placeholder:'Ask',onDone:()=>finish()});panel.ask('Question');await done;assert.match(host.querySelector('.msg.a').textContent,/completed answer is preserved/);assert.match(host.querySelector('.resp-meta').textContent,/history could not be retained/);assert.ok(!host.querySelector('.msg.a .note'));}finally{h.close();}
});
test('oversized history compacts intact tool payloads and preserves source IDs, question and final report',()=>{
 const h=setup();try{const {w}=h,e=evidence(w),calls=Array.from({length:10},(_,i)=>({id:'t'+i,type:'function',function:{name:'source',arguments:'{}'}})),turns=[{role:'user',content:'Original question'},{role:'assistant',content:'',tool_calls:calls},...calls.map(tc=>({role:'tool',tool_call_id:tc.id,content:JSON.stringify({evidenceId:e.id,text:'x'.repeat(59000)})})),{role:'assistant',content:JSON.stringify({markdown:'Final analysis',claims:[{evidenceId:e.id,value:100,unit:'USD'}]})}];const retained=w.conversationWindow(turns);assert.ok(JSON.stringify(retained).length<560000);assert.equal(retained[0].content,'Original question');assert.equal(retained.at(-1).content,turns.at(-1).content);assert.equal(retained.filter(t=>t.role==='tool').length,10);for(const t of retained.filter(t=>t.role==='tool')){const data=JSON.parse(t.content);assert.equal(data.compacted,true);assert.ok(data.evidenceIds.includes(e.id));assert.ok(retained.find(m=>m.tool_calls?.some(tc=>tc.id===t.tool_call_id)));}}finally{h.close();}
});
