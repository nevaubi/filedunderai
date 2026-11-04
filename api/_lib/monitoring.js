import { createHash, randomUUID, timingSafeEqual } from 'node:crypto';
import '../../public/lib/evidence.js';
import '../../public/lib/analyst.js';
const A = globalThis.FU_ANALYST;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TICKER = /^[A-Z0-9][A-Z0-9.-]{0,11}$/;
export const defaults = Object.freeze({watchlist:[],rules:[],theses:[],email_enabled:false,morning_enabled:false,filing_alerts:true,morning_hour:8,timezone:'UTC'});
const fail = (message,status=400) => Object.assign(new Error(message),{status});
const canonical = x => x && typeof x==='object' ? Array.isArray(x)?x.map(canonical):Object.fromEntries(Object.keys(x).sort().map(k=>[k,canonical(x[k])])):x;
const digest = x => createHash('sha256').update(JSON.stringify(canonical(x))).digest('hex').slice(0,32);
export function publicConfig(env=process.env) {
  const url=(env.SUPABASE_URL || '').replace(/\/$/,''), key=env.SUPABASE_PUBLISHABLE_KEY || env.SUPABASE_ANON_KEY || '';
  let publicKey=key.startsWith('sb_publishable_');
  try { if (!publicKey && key.split('.').length===3) publicKey=JSON.parse(Buffer.from(key.split('.')[1],'base64url').toString()).role==='anon'; } catch {}
  const validURL=/^https:\/\/[^/]+$/.test(url) || /^http:\/\/(?:localhost|127\.0\.0\.1):\d+$/.test(url);
  return {configured:!!(validURL && publicKey),supabaseUrl:validURL && publicKey?url:null,publishableKey:validURL && publicKey?key:null,emailDeliveryConfigured:!!(env.RESEND_API_KEY && env.MONITORING_FROM_EMAIL)};
}
export function validateSettings(input) {
  if (!input || typeof input!=='object' || Array.isArray(input)) throw fail('Settings must be an object');
  for(const key of Object.keys(input))if(!Object.hasOwn(defaults,key))throw fail('Unsupported setting: '+key);
  const out={};
  if(input.watchlist!==undefined){if(!Array.isArray(input.watchlist)||input.watchlist.length>100||input.watchlist.some(t=>typeof t!=='string'||!TICKER.test(t)))throw fail('Watchlist must contain at most 100 valid uppercase tickers');out.watchlist=[...new Set(input.watchlist)];}
  if(input.rules!==undefined){if(!Array.isArray(input.rules)||input.rules.length>25)throw fail('At most 25 rules are supported');out.rules=input.rules.map((r,i)=>{const v=A.validateRule(r);if(!v.ok)throw fail(`Rule ${i+1}: ${v.error}`);if(r.enabled!==undefined&&typeof r.enabled!=='boolean')throw fail('Rule enabled must be Boolean');return {...r,id:typeof r.id==='string'?r.id.slice(0,100):digest(r),scope:r.scope||'watchlist',enabled:r.enabled!==false};});}
  if(input.theses!==undefined){if(!Array.isArray(input.theses)||input.theses.length>50)throw fail('At most 50 theses are supported');out.theses=input.theses.map((t,i)=>{if(!t||!TICKER.test(t.ticker||'')||typeof t.title!=='string'||!t.title.trim()||t.title.length>2000||!Array.isArray(t.criteria)||!t.criteria.length||t.criteria.length>20)throw fail(`Thesis ${i+1} needs a ticker, title and one to twenty criteria`);for(const c of t.criteria){const v=A.validateRule({...c,type:c.type||'threshold',scope:'tickers',tickers:[t.ticker]});if(!v.ok)throw fail(`Thesis ${i+1}: ${v.error}`);}return {...t,id:typeof t.id==='string'?t.id.slice(0,100):digest(t)};});}
  for(const k of ['email_enabled','morning_enabled','filing_alerts'])if(input[k]!==undefined){if(typeof input[k]!=='boolean')throw fail(k+' must be Boolean');out[k]=input[k];}
  if(input.morning_hour!==undefined){if(!Number.isInteger(input.morning_hour)||input.morning_hour<0||input.morning_hour>23)throw fail('Morning hour must be an integer from 0 to 23');out.morning_hour=input.morning_hour;}
  if(input.timezone!==undefined){if(typeof input.timezone!=='string'||input.timezone.length>80)throw fail('Invalid time zone');try{new Intl.DateTimeFormat('en',{timeZone:input.timezone}).format();}catch{throw fail('Use an IANA time zone such as America/New_York');}out.timezone=input.timezone;}
  if(JSON.stringify(out).length>180000)throw fail('Settings exceed 180 KB',413);
  return out;
}
export function secretMatches(actual, expected) {if(typeof actual!=='string'||!expected)return false;const a=Buffer.from(actual),b=Buffer.from(expected);return a.length===b.length&&timingSafeEqual(a,b);}
export function serviceClient(env=process.env,fetcher=fetch) {
  if(!env.SUPABASE_URL||!env.SUPABASE_SERVICE_ROLE_KEY)throw fail('Scheduled monitoring is not configured',503);
  const base=env.SUPABASE_URL.replace(/\/$/,''),key=env.SUPABASE_SERVICE_ROLE_KEY;
  return {
    rest:(path,options={})=>request(base+'/rest/v1/'+path,{...options,headers:{apikey:key,Authorization:'Bearer '+key,...options.headers}},fetcher),
    user:id=>request(base+'/auth/v1/admin/users/'+encodeURIComponent(id),{headers:{apikey:key,Authorization:'Bearer '+key}},fetcher),
    public:path=>request(base+'/storage/v1/object/public/public-data/'+path,{},fetcher),
  };
}
export async function request(url,options={},fetcher=fetch) {
  const r=await fetcher(url,{...options,signal:options.signal||AbortSignal.timeout(12000),headers:{'Content-Type':'application/json',...options.headers},body:options.body===undefined?undefined:JSON.stringify(options.body)});
  const data=await r.json().catch(()=>null);
  if(!r.ok)throw fail(data?.message||data?.msg||data?.error_description||data?.error||'Monitoring service request failed',r.status);
  return data;
}
export async function authenticate(req,{env=process.env,fetcher=fetch}={}) {
  const cfg=publicConfig(env);if(!cfg.configured)throw fail('Cloud sign-in is not configured',503);
  const token=String(req.headers.authorization||'').match(/^Bearer ([^\s]+)$/)?.[1];if(!token)throw fail('Sign in to access your cloud research',401);
  const user=await request(cfg.supabaseUrl+'/auth/v1/user',{headers:{apikey:cfg.publishableKey,Authorization:'Bearer '+token}},fetcher);
  if(!user||!UUID.test(user.id)||user.is_anonymous)throw fail('A verified account session is required',401);
  return {user,token,rest:(path,options={})=>request(cfg.supabaseUrl+'/rest/v1/'+path,{...options,headers:{apikey:cfg.publishableKey,Authorization:'Bearer '+token,...options.headers}},fetcher)};
}
export async function readAccount(auth) {
  const [rows,alerts]=await Promise.all([auth.rest(`research_settings?user_id=eq.${auth.user.id}&select=*&limit=1`),auth.rest(`research_alerts?user_id=eq.${auth.user.id}&select=*&order=created_at.desc&limit=50`)]);
  return {user:{id:auth.user.id,email:auth.user.email||null,emailVerified:!!auth.user.email_confirmed_at},settings:rows?.[0]||null,alerts:alerts||[]};
}
export async function saveSettings(auth,input,revision) {
  const settings=validateSettings(input);
  if(settings.email_enabled&&!auth.user.email_confirmed_at)throw fail('Verify your email before enabling email delivery');
  if(revision==null){try{const rows=await auth.rest('research_settings',{method:'POST',headers:{Prefer:'return=representation'},body:{...defaults,...settings,user_id:auth.user.id}});return rows[0];}catch(e){if(e.status===409)throw fail('Cloud research already exists. Load it before saving changes.',409);throw e;}}
  if(!Number.isSafeInteger(revision)||revision<1)throw fail('A valid current revision is required');
  const rows=await auth.rest(`research_settings?user_id=eq.${auth.user.id}&revision=eq.${revision}`,{method:'PATCH',headers:{Prefer:'return=representation'},body:settings});
  if(!rows?.length)throw fail('Cloud research changed elsewhere. Load the latest version before saving.',409);
  return rows[0];
}
function localClock(now,timeZone){const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',hourCycle:'h23'}).formatToParts(now).map(x=>[x.type,x.value]));return{date:`${p.year}-${p.month}-${p.day}`,hour:+p.hour};}
const compact = x => ({value:x.v,unit:x.unit,period:x.end,metric:x.label,evidence:x.evidence||null});
function evidenceClosure(observations) {const seen=new Map();function visit(e){if(!e||seen.has(e.id)||seen.size>=80)return;seen.set(e.id,e);for(const id of e.inputEvidenceIds||[])visit(globalThis.FU_EVIDENCE.get(id));}for(const o of observations)visit(o.evidence);return [...seen.values()];}
function financialSnapshot(company,asOf){const changes=A.changes(company,{asOf});const observations=changes.rows.flatMap(r=>[r.current,r.yoy]).filter(Boolean);return{periodEnd:changes.periodEnd,availableAt:changes.availableAt,financials:changes.rows.filter(r=>r.current).map(r=>({metric:r.metric,current:compact(r.current),yoy:r.yoy?compact(r.yoy):null,changeType:r.changeType})),evidence:evidenceClosure(observations),financialContext:'Latest comparable filed values available by '+asOf+'. The reporting period may predate this event; year-over-year changes compare aligned periods.'};}
function transactionEvidence(t,ticker,index){const evidence=[];const capture=(metric,value,unit,inputs=[])=>{if(!Number.isFinite(value))return null;const e=globalThis.FU_EVIDENCE.capture({ticker,metric,value,unit,period:t.tx_date,periodStart:null,periodEnd:t.tx_date,periodType:'instant',concept:'Form4:nonDerivativeTransaction:'+index,accession:t.accession,filedDate:t.filed,availabilityDate:t.filed,sourceUrl:t.sourceUrl,inputEvidenceIds:inputs,derivation:inputs.length?'Reported transaction shares × reported transaction price':null});evidence.push(e);return e;};const shares=capture('Insider purchase shares',t.shares,'shares'),price=capture('Insider purchase price',t.price,'USD/shares');if(shares&&price)capture('Insider purchase value',t.value,'USD',[shares.id,price.id]);return{...t,evidence};}
export function evidenceUrls(record,all=[]){const byId=new Map(all.map(e=>[e.id,e])),urls=new Set(),seen=new Set();function visit(e){if(!e||seen.has(e.id))return;seen.add(e.id);if(globalThis.FU_EVIDENCE.safeUrl(e.sourceUrl))urls.add(e.sourceUrl);for(const id of e.inputEvidenceIds||[])visit(byId.get(id));}visit(record);return [...urls];}
export function buildMonitoringEvents(settings,companies,{now=new Date(),lastCheckedAt=null,lastBriefDate=null,coverage={},extras={}}={}) {
  const date=now.toISOString().slice(0,10),clock=localClock(now,settings.timezone),events=[],unknown=[];
  // Every slice overlaps a full broad-universe sweep. Stable event keys deduplicate it.
  const since=new Date(now-7*86400000).toISOString().slice(0,10);
  const add=(key,kind,title,content,email=true)=>events.push({event_key:key,kind,title,content:{...content,coverage},email});
  for(const c of companies){
    if(settings.filing_alerts&&settings.watchlist.includes(c.ticker))for(const f of A.newEvents(c,{since:lastCheckedAt?since:new Date(now-86400000).toISOString().slice(0,10),until:date}))add(f.id,f.type==='release'?'release':'filing',`${c.ticker}: ${f.title} filed`,{ticker:c.ticker,event:f,...financialSnapshot(c,f.date)});
    for(const rule of settings.rules.filter(r=>r.enabled!==false)){
      if((rule.scope||'watchlist')==='watchlist'&&!settings.watchlist.includes(c.ticker))continue;
      const result=A.evaluateRule(rule,c,{asOf:date,watchlist:settings.watchlist,...(extras[c.ticker]||{})});
      if(result.status==='unknown'){unknown.push({ticker:c.ticker,ruleId:rule.id,reason:result.reason});continue;}if(result.status!=='triggered')continue;
      const observations=result.observations.map(compact),transactions=(result.transactions||[]).map((t,i)=>transactionEvidence(t,c.ticker,i)),identity={rule,observations:observations.map(o=>o.evidence?.id||[o.period,o.value]),transactions:transactions.map(t=>[t.accession,t.tx_date,t.shares])};
      add(`rule:${rule.id}:${c.ticker}:${digest(identity)}`,'rule',`${c.ticker}: standing order matched`,{ticker:c.ticker,ruleId:rule.id,description:A.describeRule(rule),status:result.status,reason:result.reason,observations,transactions,evidence:[...evidenceClosure(result.observations),...transactions.flatMap(t=>t.evidence)]});
    }
    for(const thesis of settings.theses.filter(t=>t.ticker===c.ticker)){const result=A.thesis(thesis,c,{asOf:date});const observations=result.criteria.flatMap(x=>x.result.observations||[]);if(result.status==='insufficient'){unknown.push({ticker:c.ticker,thesisId:thesis.id,reason:'Thesis criteria need more data'});continue;}add(`thesis:${thesis.id}:${digest({status:result.status,ids:observations.map(x=>x.evidence?.id||[x.end,x.v])})}`,'thesis',`${c.ticker}: thesis ${result.status}`,{ticker:c.ticker,thesisId:thesis.id,title:thesis.title,status:result.status,measured:result.measured,total:result.total,score:result.score,observations:observations.map(compact),evidence:evidenceClosure(observations)});}
  }
  let briefDate=null;
  if(settings.morning_enabled&&clock.hour>=settings.morning_hour&&lastBriefDate!==clock.date){const watch=companies.filter(c=>settings.watchlist.includes(c.ticker));const brief=A.brief(watch,{since:new Date(now-86400000).toISOString().slice(0,10),until:date,failed:coverage.failed||[]});const items=brief.items.map(i=>({ticker:i.ticker,name:i.name,events:i.events,...financialSnapshot(watch.find(c=>c.ticker===i.ticker),date)}));add(`morning:${clock.date}`,'brief',`Morning research brief · ${clock.date}`,{date:clock.date,timeZone:settings.timezone,items,eventCount:brief.eventCount,unknown,freshness:'Only source events within the stated date window are new; financial periods may be older.',since:brief.since,until:date});briefDate=clock.date;}
  if(unknown.length||coverage.failed?.length||coverage.partial)add(`coverage:${date}:${digest({unknown,coverage})}`,'coverage','Some monitoring checks need data',{unknown,...coverage},false);
  return {events,briefDate,unknown};
}
export async function enqueueEvents(client,userId,events,emailEnabled,beforeBatch=async()=>{}){
  for(let i=0;i<events.length;i+=40){await beforeBatch();const part=events.slice(i,i+40);const alerts=part.map(({email,...alert})=>({user_id:userId,...alert}));await client.rest('research_alerts?on_conflict=user_id,event_key',{method:'POST',headers:{Prefer:'resolution=ignore-duplicates,return=minimal'},body:alerts});const outbox=part.filter(e=>emailEnabled&&e.email).map(e=>({user_id:userId,event_key:e.event_key,subject:e.title,content:{kind:e.kind,...e.content},status:'pending'}));if(outbox.length)await client.rest('research_outbox?on_conflict=user_id,event_key',{method:'POST',headers:{Prefer:'resolution=ignore-duplicates,return=minimal'},body:outbox});}
}
export function renderEmail(row){const c=row.content;const lines=[row.subject,'',c.freshness||c.reason||c.description||'',c.ticker?'Company: '+c.ticker:''];
  const numeric=(label,o,all)=>{if(!o)return;lines.push(`${label}: ${A.format(o.value,o.unit)} (${o.period})`);const urls=evidenceUrls(o.evidence,all);lines.push(...(urls.length?urls:['Source unavailable; treat this value as unverified.']));};
  const financials=(item)=>{if(item.financialContext)lines.push(item.financialContext);for(const r of item.financials||[]){numeric(r.metric,r.current,item.evidence);numeric(r.changeType==='percentage_points'?'YoY margin change (percentage points)':'YoY change',r.yoy,item.evidence);}};
  for(const o of c.observations||[])numeric(o.metric,o,c.evidence);
  if(c.event)lines.push(`${c.event.title} · ${c.event.date}`,c.event.url||'');financials(c);
  for(const t of c.transactions||[]){lines.push(`Insider purchase · transaction ${t.tx_date} · filed ${t.filed}`);for(const e of t.evidence||[])numeric(e.metric,{value:e.value,unit:e.unit,period:e.periodEnd,evidence:e},c.evidence);if(t.drawdown!=null)lines.push(`Price drawdown: ${(t.drawdown*100).toFixed(2)}% as of ${t.priceDate}. Computed from the separately sourced daily price history; the Form 4 verifies the transaction, not market prices.`);if(t.sourceUrl)lines.push(t.sourceUrl);}
  for(const i of c.items||[]){lines.push('',i.ticker+' — '+(i.name||''));if(!i.events.length)lines.push('No new source events in the brief window.');for(const e of i.events)lines.push(`${e.title} · ${e.date}`,e.url||'');financials(i);}
  if(c.coverage?.partial)lines.push(`Coverage is partial: ${c.coverage.loaded} of ${c.coverage.total} companies. Remaining companies will be checked in a later scheduled slice.`);if(c.unknown?.length)lines.push(`${c.unknown.length} checks need additional data.`);lines.push('','Open Filed Under Watchlists to change monitoring preferences. Email acceptance does not prove delivery.');const text=lines.filter(x=>x!==undefined&&x!==null).join('\n');return text.length>50000?text.slice(0,49500)+'\n\nEmail excerpt truncated. Open the full in-app alert for the remaining records and source links.':text;
}
export async function dispatchOutbox(client,{env=process.env,fetcher=fetch,now=new Date(),limit=20,beforeEach=async()=>{}}={}) {
  if(!env.RESEND_API_KEY||!env.MONITORING_FROM_EMAIL)return {configured:false,accepted:0};
  if(limit<1)return {configured:true,accepted:0,deferred:true};
  const stale=new Date(now-5*60000).toISOString();
  const rows=await client.rest(`research_outbox?status=in.(pending,failed,sending)&next_attempt_at=lte.${encodeURIComponent(now.toISOString())}&order=created_at.asc&limit=${Math.min(30,limit)}`);let accepted=0,skipped=0;
  for(const row of rows||[]){await beforeEach();if(row.status==='sending'&&row.last_attempt_at>stale){skipped++;continue;}const first=row.content?.delivery?.firstAttemptAt;
    const patch=body=>client.rest(`research_outbox?id=eq.${row.id}&status=eq.${row.status}&attempts=eq.${row.attempts}`,{method:'PATCH',headers:{Prefer:'return=representation'},body});
    if(row.attempts>=5||(row.attempts>0&&(!first||now-new Date(first)>23*3600000))){await patch({status:'needs_review',last_error:'Retry window exhausted; provider acceptance must be reconciled before any new send.'});continue;}
    const [settings,userResponse]=await Promise.all([client.rest(`research_settings?user_id=eq.${row.user_id}&select=email_enabled,morning_enabled,filing_alerts,rules,theses,watchlist&limit=1`),client.user(row.user_id)]);const user=userResponse?.user||userResponse;
    const preference=settings?.[0],kind=row.content.kind;
    const ruleActive=kind!=='rule'||preference?.rules?.some(r=>r.id===row.content.ruleId&&r.enabled!==false);
    const thesisActive=kind!=='thesis'||preference?.theses?.some(t=>t.id===row.content.thesisId);
    if(!ruleActive||!thesisActive||!preference?.email_enabled||(kind==='brief'&&!preference.morning_enabled)||(['filing','release'].includes(kind)&&!preference.filing_alerts)||!user?.email||!user.email_confirmed_at||user.is_anonymous){await patch({status:'cancelled',last_error:'Email opt-in, active instruction, or verified recipient is no longer present.'});continue;}
    const delivery=row.content.delivery||{firstAttemptAt:now.toISOString(),from:env.MONITORING_FROM_EMAIL,to:user.email,text:renderEmail(row),subject:row.subject};
    if(delivery.to!==user.email){await patch({status:'needs_review',last_error:'Verified recipient changed after first attempt.'});continue;}
    const claimed=await patch({status:'sending',attempts:row.attempts+1,last_attempt_at:now.toISOString(),content:{...row.content,delivery}});if(!claimed?.length){skipped++;continue;}
    const update=body=>client.rest(`research_outbox?id=eq.${row.id}&status=eq.sending&attempts=eq.${row.attempts+1}`,{method:'PATCH',body});
    try{const r=await fetcher('https://api.resend.com/emails',{method:'POST',signal:AbortSignal.timeout(15000),headers:{Authorization:'Bearer '+env.RESEND_API_KEY,'Content-Type':'application/json','Idempotency-Key':'filedunder-monitoring/'+row.id},body:JSON.stringify({from:delivery.from,to:[delivery.to],subject:delivery.subject,text:delivery.text})});const data=await r.json().catch(()=>null);if(!r.ok){const terminal=r.status<500&&![408,429].includes(r.status);await update({status:terminal?'needs_review':'failed',last_error:'Provider HTTP '+r.status,next_attempt_at:new Date(now.getTime()+Math.min(3600000,2**row.attempts*60000)).toISOString()});continue;}if(!data?.id)throw new Error('Ambiguous provider acknowledgement');await update({status:'accepted',provider_id:data.id,accepted_at:now.toISOString(),last_error:null});accepted++;}
    catch{await update({status:'failed',last_error:'Delivery outcome unknown; retry only with the same idempotency key and payload.',next_attempt_at:new Date(now.getTime()+5*60000).toISOString()});}
  }
  return {configured:true,accepted,skipped};
}
export async function attachRequiredBusinessMetrics(company,settings,loadPublic){
  const labels=new Set(Object.values(company.statements||{}).flat().map(r=>String(r.label).toLowerCase()));
  const calculated=new Set(['free cash flow','operating margin','gross margin','net margin','revenue growth yoy']);
  const selected=[...settings.rules.filter(r=>r.enabled!==false&&r.type!=='insider_drawdown'&&((r.scope||'watchlist')!=='watchlist'||settings.watchlist.includes(company.ticker))&&A.inScope(r,company,settings.watchlist)).map(r=>r.metric),...settings.theses.filter(t=>t.ticker===company.ticker).flatMap(t=>t.criteria.map(c=>c.metric))];
  const metrics=[...new Set(selected.filter(m=>typeof m==='string'&&!labels.has(m.toLowerCase())&&!calculated.has(m.toLowerCase())))];
  if(!metrics.length)return{metrics:[],errors:[]};
  const errors=[],path='kpis/'+company.ticker+'.json';let bundle;
  try{bundle=await loadPublic(path);}catch(e){return{metrics,errors:[{ticker:company.ticker,dataset:path,reason:e.message}]};}
  const needsDates=(bundle?.groups||[]).some(g=>(g.items||[]).some(r=>Object.values(r.values?.Q||{}).some(v=>v.doc&&!v.filed&&!v.published_at)));
  const dates={};
  if(needsDates){const irPath='ir/'+company.ticker+'.json';try{const ir=await loadPublic(irPath);for(const doc of ir?.documents||[]){const published=doc.published_at||doc.publication_date||doc.filed||doc.filed_at;if(doc.id&&typeof published==='string'&&/^\d{4}-\d{2}-\d{2}(?:T|$)/.test(published)&&Number.isFinite(Date.parse(published)))dates[doc.id]=published;}}catch(e){errors.push({ticker:company.ticker,dataset:irPath,reason:e.message});}}
  A.attachBusinessMetrics(company,bundle,dates);return{metrics,errors};
}
export async function runMonitor({client=serviceClient(),env=process.env,fetcher=fetch,now=new Date(),maxUsers=25,maxCompanies=300,budgetMs=220000}={}) {
  const owner=randomUUID(),start=Date.now(),leaseName='analyst:monitor';const lease=()=>client.rest('rpc/acquire_etl_lease',{method:'POST',body:{lock_name:leaseName,owner,lease_seconds:300}});
  if(!await lease())return {skipped:'Another monitoring run holds the lease'};
  let renewed=Date.now();const renew=async()=>{if(Date.now()-renewed>60000){if(!await lease())throw new Error('Monitoring lease lost');renewed=Date.now();}};
  const result={users:0,companies:0,alerts:0,errors:[],delivery:null},cache=new Map(),dataCache=new Map();
  const loadPublic=path=>{if(!dataCache.has(path)){if(Date.now()-start>budgetMs||dataCache.size>=maxCompanies*4)throw new Error('Supplemental data budget reached');dataCache.set(path,client.public(path));}return dataCache.get(path);};
  try{const [due,index]=await Promise.all([client.rest('rpc/due_research_settings',{method:'POST',body:{p_limit:Math.min(50,maxUsers)}}),client.public('index.json')]);const universe=index.companies||[];
    for(const item of due||[]){if(Date.now()-start>budgetMs)break;await renew();const raw=item.settings,userStart=new Date().toISOString();try{const settings={...defaults,...validateSettings(Object.fromEntries(Object.keys(defaults).filter(k=>raw[k]!==undefined).map(k=>[k,raw[k]])))};
        const broad=new Set();for(const r of settings.rules.filter(r=>r.enabled!==false)){if(r.scope==='all')universe.forEach(c=>broad.add(c.ticker));else if(r.scope==='banks')universe.filter(c=>c.sector==='Financials').forEach(c=>broad.add(c.ticker));else if(r.scope==='tickers')r.tickers.forEach(t=>broad.add(t));}
        const broadAll=[...broad].sort(),sliceCount=Math.max(1,Math.ceil(broadAll.length/100)),slice=Math.floor(now.getTime()/3600000)%sliceCount;
        const selected=[...new Set([...settings.watchlist,...settings.theses.map(t=>t.ticker),...broadAll.slice(slice*100,(slice+1)*100)])];const companies=[],failed=[],extras={},businessMetricGaps=[];
        for(let i=0;i<selected.length;i+=5){if(Date.now()-start>budgetMs)break;await renew();await Promise.all(selected.slice(i,i+5).map(async ticker=>{try{if(!cache.has(ticker)){if(cache.size>=maxCompanies)throw new Error('Run data budget reached');const pending=client.public('companies/'+ticker+'.json').then(c=>({...c,ticker}));cache.set(ticker,pending);}const company=await cache.get(ticker);companies.push(company);const business=await attachRequiredBusinessMetrics(company,settings,loadPublic);failed.push(...business.errors);for(const metric of business.metrics)if(!A.series(company,metric,{asOf:now.toISOString().slice(0,10)}).length)businessMetricGaps.push({ticker,metric,reason:'No exact business-metric series with a known eligible source publication date.'});if(settings.rules.some(r=>r.enabled!==false&&r.type==='insider_drawdown'&&A.inScope(r,company,settings.watchlist))){const [prices,insiders]=await Promise.all([loadPublic('prices/'+ticker+'.json').catch(()=>null),loadPublic('insiders/'+ticker+'.json').catch(()=>null)]);extras[ticker]={prices:prices?.d||[],insiders};}}catch(e){failed.push({ticker,reason:e.message});}}));}
        const allTargets=new Set([...settings.watchlist,...settings.theses.map(t=>t.ticker),...broadAll]);const coverage={loaded:companies.length,requested:selected.length,total:allTargets.size,remaining:allTargets.size-companies.length,partial:companies.length<allTargets.size,scopeSlice:slice+1,scopeSlices:sliceCount,failed,businessMetricGaps};
        const built=buildMonitoringEvents(settings,companies,{now,lastCheckedAt:item.last_checked_at,lastBriefDate:item.last_brief_date,coverage,extras});await enqueueEvents(client,raw.user_id,built.events,settings.email_enabled,async()=>{await renew();if(Date.now()-start>budgetMs)throw new Error('Monitoring time budget reached while persisting alerts; retry is deduplicated.');});result.alerts+=built.events.length;
        await client.rest('research_monitor_state?on_conflict=user_id',{method:'POST',headers:{Prefer:'resolution=merge-duplicates'},body:{user_id:raw.user_id,last_checked_at:userStart,last_brief_date:built.briefDate||item.last_brief_date,next_check_at:new Date(now.getTime()+3600000).toISOString(),last_error:coverage.partial?'Partial coverage: '+coverage.loaded+' of '+coverage.total:null,updated_at:new Date().toISOString()}});result.users++;
      }catch(e){result.errors.push({userId:raw.user_id,error:e.message});await client.rest('research_monitor_state?on_conflict=user_id',{method:'POST',headers:{Prefer:'resolution=merge-duplicates'},body:{user_id:raw.user_id,next_check_at:new Date(now.getTime()+3600000).toISOString(),last_error:String(e.message).slice(0,500),updated_at:new Date().toISOString()}}).catch(()=>{});}}
    result.companies=cache.size;await renew();result.delivery=await dispatchOutbox(client,{env,fetcher,now,limit:Math.min(20,Math.max(0,Math.floor((budgetMs-(Date.now()-start))/16000))),beforeEach:renew});return result;
  }finally{await client.rest('rpc/release_etl_lease',{method:'POST',body:{lock_name:leaseName,owner}}).catch(()=>{});}
}
