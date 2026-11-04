/* Deterministic research engine. Shared by browser and scheduled monitoring. */
(function (root) {
  'use strict';
  const finite = x => typeof x === 'number' && Number.isFinite(x);
  const date = x => String(x || '').slice(0, 10);
  const day = x => Date.parse(date(x) + 'T00:00:00Z') / 86400000;
  const diff = (a, b) => day(a) - day(b);
  const latest = a => a.slice().sort().at(-1) || null;
  const today = () => new Date().toISOString().slice(0, 10);
  const E = () => root.FU_EVIDENCE;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const metrics = ['Revenue', 'Operating income', 'Net income', 'EPS diluted', 'Operating cash flow', 'Capex', 'Free cash flow', 'Operating margin'];
  function sourceURL(company, rec) {
    if (rec.url && /^https?:\/\//.test(rec.url)) return rec.url;
    const f = (company.filings || []).find(f => (f.a || f.accn) === rec.accn);
    if (f?.url) return f.url;
    if (!rec.accn || !company.cik) return null;
    return `https://www.sec.gov/Archives/edgar/data/${Number(company.cik)}/${rec.accn.replace(/-/g, '')}/${f?.p || rec.accn + '-index.html'}`;
  }
  function attach(company, metric, unit, rec, stmt = 'income', kind = 'Q') {
    const cell = {tk:company.ticker, label:metric, v:rec.v, unit, concept:rec.c, kind, stmt, start:rec.start, end:rec.end,
      rec:{...rec, url:sourceURL(company, rec)}, derived:rec.derived};
    const evidence = E()?.fromCell(cell, {sourceUrl:cell.rec.url, period:rec.end});
    return {...cell, evidence, availableAt:rec.available_at || rec.filed || null};
  }
  function derived(company, metric, unit, value, inputs, formula) {
    if (!finite(value) || !inputs.length) return null;
    const cell={tk:company.ticker,label:metric,v:value,unit,stmt:'ratios',kind:inputs[0].kind || 'Q',start:inputs[0].start,end:inputs[0].end,calc:{formula,inputs}};
    return {...cell,evidence:E()?.fromCell(cell,{period:cell.end}),availableAt:latest(inputs.map(x=>x.availableAt).filter(Boolean))};
  }
  function series(company, metric, {asOf, kind='Q', requireHistory=false} = {}) {
    const key = kind === 'A' ? 'annual' : 'quarterly';
    const all = Object.entries(company.statements || {}).flatMap(([stmt, rows]) => rows.map(row => ({stmt,row})));
    const match = all.find(x=>x.row.label.toLowerCase()===String(metric).toLowerCase());
    if (!match) {
      if(metric==='Revenue growth YoY') {const raw=series(company,'Revenue',{asOf,kind,requireHistory});return raw.map(x=>growth(company,x,aligned(raw,x.end,365),'Revenue growth YoY')).filter(Boolean);}
      if (['Free cash flow','Operating margin','Net margin','Gross margin'].includes(metric)) {
        const names=metric==='Free cash flow'?['Operating cash flow','Capex']:metric==='Operating margin'?['Operating income','Revenue']:metric==='Net margin'?['Net income','Revenue']:['Gross profit','Revenue'];
        const a=series(company,names[0],{asOf,kind,requireHistory}), b=series(company,names[1],{asOf,kind,requireHistory});
        return a.map(x=>{const y=aligned(b,x.end,0,0);return y && x.start && y.start===x.start && (metric==='Free cash flow' || y.v>0) ? derived(company,metric,metric==='Free cash flow'?'USD':'pct',metric==='Free cash flow'?x.v-Math.abs(y.v):x.v/y.v,[x,y],metric==='Free cash flow'?'Operating cash flow − ABS(Capex)':`${names[0]} / Revenue`):null;}).filter(Boolean);
      }
      const extra = company.extraSeries?.[metric] || [];
      if(requireHistory)return []; // Current business-metric snapshots are not historical vintages.
      return extra.filter(x=>finite(x.v) && x.end && (!x.availableAt || date(x.availableAt)<=date(asOf || today())) && (!asOf || x.availableAt)).sort((a,b)=>a.end.localeCompare(b.end)).map(x=>({...x,evidence:x.evidence || E()?.fromCell(x,{sourceUrl:x.rec?.url,period:x.end})}));
    }
    const {row,stmt}=match;
    if (requireHistory && !Array.isArray(row.filing_history?.[key])) return [];
    const rows=row.filing_history?.[key] || row[key] || [];
    const byEnd = new Map();
    for (const r of rows) {
      if (!finite(r.v) || !r.end || ['invalid','rejected'].includes(r.validation_status)) continue;
      if (r.derived && (row.unit==='USD/shares' || /shares|^EPS /i.test(row.label))) continue;
      if (kind==='Q' && ['income','cashflow'].includes(stmt) && r.start && (diff(r.end,r.start)<69 || diff(r.end,r.start)>105)) continue;
      const available=r.available_at || r.filed;
      if (r.superseded_at && date(r.superseded_at)<=date(asOf || today())) continue;
      if ((asOf && !available) || (available && date(available)>date(asOf || today()))) continue;
      const old=byEnd.get(r.end);
      if (!old || String(available || '').localeCompare(old.available_at || old.filed || '')>=0) byEnd.set(r.end,r);
    }
    return [...byEnd.values()].sort((a,b)=>a.end.localeCompare(b.end)).map(r=>attach(company,row.label,row.unit,r,stmt,kind));
  }
  function attachBusinessMetrics(company,bundle,documentDates={}) {
    const extra={},items=(bundle?.groups || []).flatMap(g=>(g.items || []).map(r=>({...r,category:g.category}))),counts={};
    for(const r of items)counts[r.name]=(counts[r.name] || 0)+1;
    const cardCandidates=[];
    for(const r of items) {
      const name=counts[r.name]>1?`${r.name} [${r.id}]`:r.name;if(!name)continue;
      extra[name]=Object.entries(r.values?.Q || {}).filter(([,v])=>finite(v.v) && v.url && v.rejected!==true).map(([end,v])=>{
        const unit=r.unit==='%'?'pct':r.unit==='USD_per_share'?'USD/shares':r.unit,availableAt=v.filed || v.published_at || documentDates[v.doc] || null;
        return {tk:company.ticker,label:name,v:r.unit==='%'?v.v/100:v.v,unit,kind:'Q',stmt:'kpi',end,start:v.start || null,availableAt,
          rec:{v:r.unit==='%'?v.v/100:v.v,end,start:v.start || null,url:v.url,accn:v.accn,filed:availableAt,available_at:availableAt,quality:{businessMetricId:r.id,definition:r.description,category:r.category,sourceLocator:{page:v.page,sheet:v.sheet,cell:v.cell,quote:v.quote},availabilityKnown:!!availableAt}}};
      });
      if(r.unit==='USD' && /net charge.off/i.test(r.name) && /(?:credit card|Card Services)(?!.*Auto)/i.test(r.name) && !/\d{4}q|rate|allowance|provision/i.test(r.id+' '+r.name))cardCandidates.push(name);
    }
    if(cardCandidates.length===1)extra['Credit card losses']=extra[cardCandidates[0]];
    company.extraSeries={...(company.extraSeries || {}),...extra};return company;
  }
  function aligned(records,end,offsetDays,tolerance=20) {
    return records.map(x=>({x,gap:Math.abs(diff(end,x.end)-offsetDays)})).filter(o=>o.gap<=tolerance).sort((a,b)=>a.gap-b.gap)[0]?.x || null;
  }
  function growth(company,current,previous,label) {
    if (!current || !previous || previous.v<=0 || current.unit!==previous.unit) return null;
    if (current.start && previous.start && Math.abs(diff(current.end,current.start)-diff(previous.end,previous.start))>14) return null;
    return derived(company,label,'pct',current.v/previous.v-1,[current,previous],`${current.label} / prior comparable period − 1`);
  }
  function changes(company,{metrics:selected=metrics,asOf}={}) {
    const revenue=series(company,'Revenue',{asOf});
    const end=revenue.at(-1)?.end || latest(selected.flatMap(m=>series(company,m,{asOf}).map(x=>x.end)));
    const rows=selected.map(metric=>{
      const s=series(company,metric,{asOf}), current=aligned(s,end,0,12);
      const previous=aligned(s,end,91,25), yearAgo=aligned(s,end,365,20);
      if (!current) return {metric,status:'unavailable',current:null,qoq:null,yoy:null};
      const make=(prior,name)=> current.unit==='pct' ? prior && derived(company,`${metric} ${name} change`,'pct',current.v-prior.v,[current,prior],`${metric} − prior comparable ${metric} (percentage points)`) : growth(company,current,prior,`${metric} ${name}`);
      return {metric,status:'available',current,previous,yearAgo,qoq:make(previous,'QoQ'),yoy:make(yearAgo,'YoY'),changeType:current.unit==='pct'?'percentage_points':'growth'};
    });
    return {ticker:company.ticker,periodEnd:end,asOf:asOf || today(),availableAt:latest(rows.map(x=>x.current?.availableAt).filter(Boolean)),rows,
      limitations:rows.some(x=>!x.yoy)?['Missing or nonpositive comparison bases are left blank; percentage changes are not meaningful for those rows.']:[]};
  }
  function newEvents(company,{since,until=today()}={}) {
    const from=since || new Date(Date.now()-7*86400000).toISOString().slice(0,10);
    const events=(company.filings || []).filter(f=>['10-Q','10-K','8-K','10-Q/A','10-K/A'].includes(f.f || f.form)).map(f=>({id:`filing:${f.a || f.accn}`,ticker:company.ticker,type:'filing',title:f.f || f.form,date:f.d || f.filed,period:f.r || f.period,url:sourceURL(company,{accn:f.a || f.accn,url:f.url})}));
    if (company.release?.url) events.push({id:`release:${company.release.accn || company.release.url}`,ticker:company.ticker,type:'release',title:'Earnings release',date:company.release.filed,url:company.release.url});
    return events.filter(x=>x.date && date(x.date)>=date(from) && date(x.date)<=date(until)).sort((a,b)=>b.date.localeCompare(a.date));
  }
  function brief(companies,opts={}) {
    const items=companies.map(c=>({ticker:c.ticker,name:c.profile?.name || c.name,events:newEvents(c,opts),changes:changes(c,{asOf:opts.until})}));
    return {generatedAt:new Date().toISOString(),since:opts.since || null,until:opts.until || today(),items,eventCount:items.reduce((s,i)=>s+i.events.length,0),
      coverage:{loaded:companies.length,failed:opts.failed || []}};
  }
  const aliases=[['credit card losses',/credit.card.(?:loss|charge.off)/i],['Revenue',/\brevenue|\bsales/i],['Operating margin',/operating.margin/i],['Gross margin',/gross.margin/i],['Net income',/net.income|earnings/i],['Free cash flow',/free.cash.flow|\bfcf\b/i],['EPS diluted',/\beps\b/i]];
  function validateRule(r) {
    if (!r || !['trend','threshold','insider_drawdown'].includes(r.type)) return {ok:false,error:'Choose a supported rule type.'};
    if (!['watchlist','banks','tickers','all'].includes(r.scope || 'watchlist')) return {ok:false,error:'Unknown company scope.'};
    if (r.scope==='tickers' && (!Array.isArray(r.tickers) || !r.tickers.length || r.tickers.length>100 || r.tickers.some(t=>!/^[-.A-Z0-9]{1,12}$/.test(t)))) return {ok:false,error:'Choose one to one hundred valid tickers.'};
    if (r.type!=='insider_drawdown' && (typeof r.metric!=='string' || !r.metric.trim() || r.metric.length>120)) return {ok:false,error:'Choose a measurable metric.'};
    if (r.type==='trend' && (!['up','down'].includes(r.direction) || !Number.isInteger(r.consecutive) || r.consecutive<1 || r.consecutive>8)) return {ok:false,error:'Trend rules need a direction and one to eight consecutive quarters.'};
    if (r.type==='threshold' && (!['gt','gte','lt','lte'].includes(r.operator) || !finite(r.threshold))) return {ok:false,error:'A threshold needs an operator and finite value.'};
    if (r.type==='insider_drawdown' && (!finite(r.drawdown) || r.drawdown<=0 || r.drawdown>=1)) return {ok:false,error:'Drawdown must be between zero and one.'};
    return {ok:true};
  }
  function compileRule(text,{tickers=[]}={}) {
    text=String(text || '').slice(0,2000);
    const scope=/\bbank'?s?\b/i.test(text)?'banks':/\b(any|all) compan/i.test(text)?'all':tickers.length?'tickers':'watchlist';
    let rule;
    if (/insider.*buy|insider.*purchas/i.test(text) && /drop|drawdown|fall/i.test(text)) {
      const pct=text.match(/(\d+(?:\.\d+)?)\s*%/);
      if (pct) rule={type:'insider_drawdown',scope,tickers,drawdown:Number(pct[1])/100,lookback:252};
    } else {
      let metric=aliases.find(([,rx])=>rx.test(text))?.[0];
      if (metric==='credit card losses') metric='Credit card losses';
      const n=text.match(/(\d+)\s*(?:consecutive\s*)?quarters?/i);
      const direction=/rise|increas|grow|accelerat/i.test(text)?'up':/fall|declin|decreas|drop/i.test(text)?'down':null;
      if (metric && direction && n) rule={type:'trend',scope,tickers,metric,direction,consecutive:Number(n[1])};
      else {
        const threshold=text.match(/(above|below|greater than|less than|at least|at most)\s*\$?(-?\d+(?:\.\d+)?)\s*(%|billion|million|bn|[bm])?/i);
        if (metric && threshold) {const scale={'%':.01,billion:1e9,bn:1e9,b:1e9,million:1e6,m:1e6};rule={type:'threshold',scope,tickers,metric,operator:/above|greater/.test(threshold[1])?'gt':/below|less/.test(threshold[1])?'lt':threshold[1]==='at least'?'gte':'lte',threshold:Number(threshold[2])*(scale[(threshold[3] || '').toLowerCase()] || 1)};}
      }
    }
    return rule && validateRule(rule).ok ? {status:'ready',rule:{...rule,text,enabled:true},explanation:describeRule(rule)} : {status:'needs_review',rule:null,explanation:'Choose a metric, comparison and scope below, or ask the assistant to propose a measurable rule. No rule has been activated.'};
  }
  function describeRule(r) {
    const scope=r.scope==='banks'?'banks':r.scope==='all'?'all covered companies':r.scope==='tickers'?(r.tickers || []).join(', '):'your watchlist';
    const metric=r.metric==='Credit card losses'?'Credit-card net charge-offs in dollars (only where a single explicit card-portfolio series is mapped)':r.metric;
    return r.type==='trend'?`${metric} moves ${r.direction} for ${r.consecutive} consecutive quarter-to-quarter comparisons in ${scope}.`:r.type==='insider_drawdown'?`Open-market insider purchase after at least a ${(r.drawdown*100).toFixed(0)}% drawdown from the preceding 252-session high in ${scope}.`:`${metric} ${r.operator} ${r.threshold} in ${scope}.`;
  }
  function inScope(rule,company,watchlist=null) {
    if (rule.scope==='banks') return /bank|credit card/i.test([company.sicDescription,company.profile?.sicDescription,company.industry].filter(Boolean).join(' '));
    if (rule.scope==='tickers') return rule.tickers?.includes(company.ticker);
    return rule.scope!=='watchlist' || watchlist===null || watchlist.includes(company.ticker);
  }
  function evaluateRule(rule,company,{asOf,prices=[],insiders,watchlist=null}={}) {
    const valid=validateRule(rule);
    const result=(status,reason,observations=[],extra={})=>({status,reason,ticker:company.ticker,rule:describeRule(rule),observations,...extra});
    if (!valid.ok) return result('unknown',valid.error);
    if (!inScope(rule,company,watchlist)) return result('out_of_scope','Company is outside the selected scope.');
    if (rule.type==='insider_drawdown') {
      if (!insiders || !prices.length) return result('unknown','Insider filings and price history are both required.');
      const transactions=insiderContext(insiders,prices,{drawdown:rule.drawdown,asOf}).filter(x=>diff(asOf || today(),x.filed)<=30),hits=transactions.filter(x=>x.triggered),missing=transactions.some(x=>!x.coverage.complete);
      return result(hits.length?'triggered':missing?'unknown':'clear',hits.length?'A qualifying disclosed purchase was found.':missing?'The 252-session pre-purchase price history is incomplete.':'No qualifying disclosed purchase in the past 30 days.',[],{transactions});
    }
    const s=series(company,rule.metric,{asOf});
    if (rule.type==='threshold') {
      const x=s.at(-1);if (!x) return result('unknown','The selected metric is not available.');
      const yes={gt:x.v>rule.threshold,gte:x.v>=rule.threshold,lt:x.v<rule.threshold,lte:x.v<=rule.threshold}[rule.operator];
      return result(yes?'triggered':'clear',`${rule.metric} evaluated for ${x.end}.`,[x]);
    }
    const points=s.slice(-(rule.consecutive+1));
    if (points.length!==rule.consecutive+1 || points.some((x,i)=>i && (diff(x.end,points[i-1].end)<65 || diff(x.end,points[i-1].end)>115))) return result('unknown','A complete consecutive quarterly history is required.',points);
    const hit=points.every((x,i)=>!i || (rule.direction==='up'?x.v>points[i-1].v:x.v<points[i-1].v));
    return result(hit?'triggered':'clear',hit?'All consecutive comparisons meet the rule.':'The sequence does not meet the rule.',points);
  }
  function thesis(thesis,company) {
    const criteria=(thesis.criteria || []).map(c=>({...c,result:evaluateRule({type:c.type || 'threshold',scope:'tickers',tickers:[company.ticker],...c},company)}));
    const measured=criteria.filter(c=>['clear','triggered'].includes(c.result.status));
    const passing=measured.filter(c=>c.result.status==='triggered').length;
    const score=measured.length?passing/measured.length:null;
    return {criteria,measured:measured.length,total:criteria.length,score,status:!criteria.length || measured.length<criteria.length?'insufficient':score===1?'holding':score===0?'broken':'weakening'};
  }
  function signal(company,type,{asOf,requireHistory=false}={}) {
    const options={asOf,requireHistory};
    if (type==='revenue_acceleration') {
      const s=series(company,'Revenue',options), a=s.at(-1), b=a&&aligned(s,a.end,91,25);
      const ag=a&&growth(company,a,aligned(s,a.end,365),'Revenue growth YoY'), bg=b&&growth(company,b,aligned(s,b.end,365),'Prior quarter revenue growth YoY');
      if (!ag || !bg) return {status:'unknown',reason:'Two adjacent year-over-year comparisons are required.'};
      return {status:ag.v>bg.v && ag.v>0?'triggered':'clear',type,period:a.end,availableAt:latest([ag.availableAt,bg.availableAt]),observations:[ag,bg],reason:'Year-over-year revenue growth increased versus the preceding quarter.',invalidation:'Revenue growth decelerates or the underlying filings are revised.',horizon:'Next quarterly report'};
    }
    if (type==='margin_turn') {
      const s=series(company,'Operating margin',options), p=s.slice(-3);
      if (p.length!==3 || p.some((x,i)=>i&&Math.abs(diff(x.end,p[i-1].end)-91)>25)) return {status:'unknown',reason:'Three adjacent quarterly operating margins are required.'};
      return {status:p[2].v>p[1].v && p[1].v<=p[0].v?'triggered':'clear',type,period:p[2].end,availableAt:latest(p.map(x=>x.availableAt)),observations:p,reason:'Operating margin improved after a flat or declining quarter.',invalidation:'The improvement reverses in the next reported quarter.',horizon:'Next quarterly report'};
    }
    return {status:'unknown',reason:'Unknown signal.'};
  }
  function bars(prices) {
    return prices.map(p=>Array.isArray(p)?{date:new Date(p[0]*1000).toISOString().slice(0,10),o:p[1],c:p[4]}:{date:date(p.date || (p.t instanceof Date?p.t.toISOString():p.t)),o:p.o,c:p.c}).filter(p=>p.date && finite(p.c) && p.c>0).sort((a,b)=>a.date.localeCompare(b.date));
  }
  function eventStudy(events,prices,{horizon=20,costBps=10}={}) {
    horizon=Math.max(1,Math.min(252,Math.floor(horizon)));costBps=Math.max(0,Math.min(1000,Number(costBps)||0));
    const p=bars(prices), trades=[];let lastExit='';
    for (const e of events.slice().sort((a,b)=>a.availableAt.localeCompare(b.availableAt))) {
      const i=p.findIndex(b=>b.date>date(e.availableAt)), entry=p[i], exit=p[i+horizon-1];
      if (i<=0 || !entry || !exit || !finite(entry.o) || entry.o<=0 || entry.date<=lastExit || diff(entry.date,e.availableAt)>7 || diff(e.availableAt,p[i-1].date)>7) continue;
      if (p.slice(i,i+horizon).some((x,j,all)=>j && diff(x.date,all[j-1].date)>7)) continue;
      trades.push({eventId:e.id,publicationDate:date(e.availableAt),entryDate:entry.date,exitDate:exit.date,entry:entry.o,exit:exit.c,grossReturn:exit.c/entry.o-1,netReturn:exit.c/entry.o-1-2*costBps/10000,evidenceIds:(e.observations || []).map(x=>x.evidence?.id).filter(Boolean)});
      lastExit=exit.date;
    }
    return {trades,n:trades.length,averageReturn:trades.length?trades.reduce((s,t)=>s+t.netReturn,0)/trades.length:null,hitRate:trades.length?trades.filter(t=>t.netReturn>0).length/trades.length:null,
      methodology:`Entry at the next trading session's open after filing publication; hold ${horizon} trading sessions including entry day, then exit at the close; ${costBps} basis points per side; overlapping trades excluded. Price return, excluding dividends. Current coverage creates survivorship bias. Historical examples are not an out-of-sample forecast.`};
  }
  function backtest(company,prices,{type='revenue_acceleration',horizon=20,costBps=10}={}) {
    const needed=type==='margin_turn'?['Revenue','Operating income']:['Revenue'];
    const rows=Object.values(company.statements || {}).flat();
    if (needed.some(m=>!rows.find(r=>r.label===m)?.filing_history?.quarterly?.length)) return {status:'unavailable',reason:'Archived filing vintages are required. Latest-only data cannot support an honest point-in-time study.',trades:[],n:0};
    const dates=[...new Set(rows.filter(r=>needed.includes(r.label)).flatMap(r=>r.filing_history.quarterly.map(x=>x.available_at || x.filed)).filter(Boolean))].sort();
    const events=[],seen=new Set();
    for (const asOf of dates) {const s=signal(company,type,{asOf,requireHistory:true}); if (s.status==='triggered' && !seen.has(s.period)) {events.push({...s,id:type+':'+s.period});seen.add(s.period);}}
    return {status:'available',signal:type,events:events.length,...eventStudy(events,prices,{horizon,costBps})};
  }
  function earningsSetup(company,prices,{metric='Revenue',growthAssumption=null}={}) {
    const s=series(company,metric), last=s.at(-1);
    if (!last) return {status:'unavailable',reason:'No reported history for the selected metric.'};
    const previous=aligned(s,last.end,365), observed=last.v>0 && previous?.v>0?last.v/previous.v-1:null;
    const base=aligned(s,last.end,274,30);
    const assumption=finite(growthAssumption)?growthAssumption:observed;
    const estimate=base && assumption!==null?base.v*(1+assumption):null;
    const events=(company.earnings8k || []).map(f=>({id:f.accn,availableAt:f.filed})).filter(e=>e.availableAt);
    return {status:'available',metric,latest:last,estimate,unit:last.unit,assumption,assumptionType:finite(growthAssumption)?'User assumption':'Latest reported year-over-year growth',seasonalBase:base,consensus:null,
      forecastLabel:'Mechanical scenario, not analyst consensus',reaction:eventStudy(events,prices,{horizon:1,costBps:0}),
      limitations:['The scenario applies an editable growth assumption to the comparable quarter a year earlier.','No sourced consensus feed is configured; beat/miss against consensus and upcoming report dates are left unavailable.']};
  }
  function pair(a,b,{metrics:selected=['Revenue','Operating margin','Free cash flow']}={}) {
    return {a:a.ticker,b:b.ticker,sameSector:!!a.sector && a.sector===b.sector,rows:selected.map(metric=>{
      const sa=series(a,metric),sb=series(b,metric),xa=sa.at(-1),xb=xa&&aligned(sb,xa.end,0,35);
      const ga=xa && growth(a,xa,aligned(sa,xa.end,365),metric+' growth YoY'),gb=xb && growth(b,xb,aligned(sb,xb.end,365),metric+' growth YoY');
      return {metric,a:xa,b:xb,aligned:!!xb,growthA:ga,growthB:gb,gap:xa?.unit==='pct' && xb?xa.v-xb.v:ga&&gb?ga.v-gb.v:null};
    }),interpretation:'A difference is a research lead. Fiscal calendars, business mix and accounting definitions must be compared before forming a long/short view.',horizon:'Next two quarterly reports',invalidation:'The reported divergence closes or is explained by inconsistent definitions.'};
  }
  function insiderContext(insiders,prices,{drawdown=.2,asOf=today()}={}) {
    const p=bars(prices);
    return (insiders.rows || []).filter(r=>r.code==='P' && r.acq_disp!=='D' && !r.derivative && r.filed && date(r.filed)<=date(asOf) && finite(r.shares) && r.shares>0).map(r=>{
      const before=p.filter(x=>x.date<=date(r.tx_date)).slice(-252), last=before.at(-1), peak=before.length?Math.max(...before.map(x=>x.c)):null;
      const dd=last && peak?Number((last.c/peak-1).toFixed(12)):null;
      const complete=before.length===252 && diff(r.tx_date,last?.date)<=7 && !before.some((x,i)=>i && diff(x.date,before[i-1].date)>7);
      return {...r,value:finite(r.price)?r.shares*r.price:null,drawdown:dd,triggered:complete && dd!==null && dd<=-drawdown,coverage:{complete,sessions:before.length,required:252},priceDate:last?.date || null,sourceUrl:insiders.cik?sourceURL(insiders,{accn:r.accession}):null};
    }).sort((a,b)=>b.filed.localeCompare(a.filed));
  }
  function promises(bundle) {
    const items=[];
    for (const s of bundle?.series || []) for (const p of s.periods || []) {
      const points=(p.points || []).slice().sort((a,b)=>String(a.d).localeCompare(b.d));
      const f=points.at(-1);if (!f) continue;
      if(f.w || /withdrawn|withdrawal/i.test(f.a || '')) {items.push({metric:s.name || s.metric,basis:s.basis,unit:s.unit,period:p.label,periodEnd:p.end,issued:f.d,quote:f.q,sourceUrl:bundle.docs?.[f.doc]?.url || null,status:'withdrawn',result:null,met:null,reason:'The latest guidance was withdrawn; earlier forecasts are not scored as current promises.'});continue;}
      const actual=p.actual;
      // Legacy extraction labels are NOT proof of period/basis equivalence.
      const check=p.comparability || actual?.comparability;
      const comparable=check?.validated===true && check.period_match===true && check.basis_match===true && check.unit_match===true && finite(actual?.v) && finite(f.lo) && finite(f.hi) && f.lo<=f.hi;
      const result=comparable?(actual.v<f.lo?'below':actual.v>f.hi?'above':'in_range'):null;
      items.push({metric:s.name || s.metric,basis:s.basis,unit:s.unit,period:p.label,periodEnd:p.end,issued:f.d,low:f.lo,high:f.hi,actual:actual?.v ?? null,quote:f.q,sourceUrl:bundle.docs?.[f.doc]?.url || null,actualInputs:actual?.inputs || [],status:comparable?'comparable':'needs_review',result,
        met:comparable?(s.direction===-1?actual.v<=f.hi:actual.v>=f.lo):null,reason:comparable?'Period, basis and units have an explicit validated match.':'Period, accounting basis and units need verification before scoring.'});
    }
    const scored=items.filter(i=>i.status==='comparable');
    return {items:items.sort((a,b)=>String(b.issued).localeCompare(a.issued)),scored:scored.length,excluded:items.length-scored.length,hitRate:scored.length?scored.filter(x=>x.met).length/scored.length:null,lastIssued:latest(items.map(x=>x.issued).filter(Boolean))};
  }
  function disclosureChanges(previous,current) {
    if (!previous || !current) return {status:'unavailable',items:[],reason:'Two comparable, sourced disclosure snapshots are required.'};
    if (previous.documentType!==current.documentType || !previous.sourceUrl || !current.sourceUrl) return {status:'unavailable',items:[],reason:'Compare the same document type and retain both source links.'};
    const old=new Map((previous.metrics || []).map(x=>[x.name,x])), now=new Map((current.metrics || []).map(x=>[x.name,x]));
    const items=[];
    for (const [name,m] of old) {
      if (!now.has(name)) items.push({type:'not_observed',metric:name,reason:'Previously extracted metric is absent from this extraction. This does not prove the company stopped reporting it.',sources:[previous.sourceUrl,current.sourceUrl]});
      else if (m.definition && now.get(name).definition && m.definition!==now.get(name).definition) items.push({type:'definition_change',metric:name,before:m.definition,after:now.get(name).definition,sources:[previous.sourceUrl,current.sourceUrl]});
    }
    const phrases=['uncertain','challenging','headwind','softness','remain confident','strong demand'];
    for (const phrase of phrases) {const count=t=>(String(t || '').toLowerCase().match(new RegExp(phrase,'g')) || []).length,a=count(previous.text),b=count(current.text);if(a!==b)items.push({type:'language',phrase,before:a,after:b,sources:[previous.sourceUrl,current.sourceUrl],reason:'Literal phrase count; a research prompt, not a sentiment or evasion verdict.'});}
    return {status:'available',items,previous:previous.date,current:current.date};
  }
  function exposure(company,universe) {
    return {documented:(company.relationships || []).filter(r=>r.sourceUrl && r.counterparty),associations:universe.filter(x=>x.ticker!==company.ticker && x.sector===company.sector).slice(0,12).map(x=>({ticker:x.ticker,name:x.name,reason:'Same sector; exposure is not established.'})),limitation:'Sector membership is not evidence of a supplier, customer or causal relationship.'};
  }
  function format(value,unit) {
    if (!finite(value)) return 'Unavailable';
    if (unit==='pct') return (value*100).toFixed(2)+'%';
    if (unit==='USD/shares') return '$'+value.toFixed(2);
    return (unit==='USD'?'$':'')+value.toLocaleString('en-US',{maximumFractionDigits:2})+(unit && !['USD','USD/shares'].includes(unit)?' '+unit:'');
  }
  function report(company) {
    const c=changes(company), claims=[], evidence=[];
    const cite=x=>{if(!x)return 'Unavailable';if(x.evidence){evidence.push(x.evidence);claims.push({evidenceId:x.evidence.id,value:x.v,unit:x.unit,ticker:company.ticker,metric:x.label,period:x.evidence.period});}return `${format(x.v,x.unit)} ${x.evidence?E().citation(x.evidence.id):''}`;};
    const lines=[`# ${company.ticker} — evidence-backed research draft`,`Reporting period: ${c.periodEnd || 'unavailable'}. This is a factual first draft; investment conclusions require analyst review.`,`## Financial performance`];
    for(const r of c.rows)if(r.current)lines.push(`${r.metric} was ${cite(r.current)} for the period ending ${r.current.end}.${r.yoy?` The comparable year-over-year ${r.changeType==='percentage_points'?'margin change':'change'} was ${cite(r.yoy)}.`:''}`);
    lines.push('## Research questions','Which reported drivers explain the changes above? Does the next filing support the same thesis? Add interpretation only after checking the sources.','## Source register');
    for(const e of [...new Map(evidence.map(x=>[x.id,x])).values()])lines.push(`- ${e.id}: ${e.ticker} · ${e.metric} · ${e.periodEnd} · ${e.sourceUrl || 'Derived from evidence inputs: '+e.inputEvidenceIds.join(', ')}`);
    return {title:`${company.ticker} research — ${c.periodEnd || today()}`,markdown:lines.join('\n\n'),claims,evidence:[...new Map(evidence.map(x=>[x.id,x])).values()],sections:[]};
  }
  root.FU_ANALYST=Object.freeze({metrics,series,attachBusinessMetrics,aligned,changes,sourceURL,newEvents,brief,compileRule,validateRule,describeRule,inScope,evaluateRule,thesis,signal,backtest,eventStudy,earningsSetup,pair,insiderContext,promises,disclosureChanges,exposure,report,format,esc});
})(typeof window!=='undefined'?window:globalThis);
