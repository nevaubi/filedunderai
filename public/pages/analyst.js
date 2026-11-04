/* Proactive research, backed by the same records used by the assistant and workspace. */
(function () {
  'use strict';
  const A=window.FU_ANALYST, cache=new Map();
  const id=()=>crypto.randomUUID();
  const saved=(key,fallback)=>store.get('analyst:'+key,fallback);
  function save(key,value) {try{localStorage.setItem('fu:analyst:'+key,JSON.stringify(value));return true;}catch{toast('Storage is full. Export your workspace before adding more research.');return false;}}
  const button=(text,fn,primary=false)=>{const b=el('button',{class:'btn'+(primary?' pri':'')},text);b.addEventListener('click',async e=>{b.disabled=true;try{await fn(e);}catch(error){toast(error.message || 'This action could not finish. Please retry.');}finally{b.disabled=false;}});return b;};
  const badge=(text,status='')=>el('span',{class:'an-badge '+status},text);
  const note=text=>el('div',{class:'an-note'},text);
  const field=(label,control)=>el('label',{class:'an-field'},el('span',{},label),control);
  const link=(text,url)=>/^https?:\/\//.test(url || '')?el('a',{href:url,target:'_blank',rel:'noopener noreferrer'},text+' ↗'):el('span',{class:'muted'},text+' · source unavailable');
  const statusNames={triggered:'Triggered',clear:'No trigger',unknown:'Needs data',out_of_scope:'Outside scope',holding:'Holding up',weakening:'Weakening',broken:'Broken',insufficient:'Insufficient evidence'};
  const pct=v=>v==null?'—':(v>=0?'+':'')+(v*100).toFixed(1)+'%';
  function value(c,{change=false,pp=false}={}) {
    if(!c)return el('span',{class:'an-missing',title:'No comparable, available value'},'—');
    const label=pp?(c.v*100).toFixed(2)+' pp':change?pct(c.v):c.unit==='pct'?(c.v*100).toFixed(1)+'%':fmtMoney(c.v,c.unit);
    return el('button',{class:'an-value'+(change?' '+cls(c.v):''),title:`${c.label} · ${c.end} · inspect exact value and source`,onclick:()=>openSource(FU_EVIDENCE.cell(c.evidence?.id)||c)},label,el('span',{'aria-hidden':'true'},' ↗'));
  }
  function header(kicker,title,description,actions=[]) {
    return el('header',{class:'an-header'},el('div',{},el('div',{class:'eyebrow'},kicker),el('h1',{},title),el('p',{},description)),el('div',{class:'an-actions'},...actions));
  }
  function card(title,content,action=null) {return el('section',{class:'an-card'},el('div',{class:'an-card-head'},el('h2',{},title),action),content);}
  function selectTicker(selected) {const s=el('select',{'aria-label':'Company'},...TICKERS.map(t=>el('option',{value:t,selected:t===selected},`${t} · ${SHORT[t] || t}`)));return s;}
  let metricsList=null;
  function metricsSelect(selected='Revenue') {if(!metricsList){metricsList=el('datalist',{id:'analyst-metric-options'},...[...new Set([...A.metrics,'Gross margin','Revenue growth YoY','Credit card losses'])].map(m=>el('option',{value:m})));document.body.append(metricsList);}return el('input',{'aria-label':'Metric',list:'analyst-metric-options',value:selected,placeholder:'Reported metric name'});}
  async function load(ticker,extra=false) {
    if(!IDX[ticker])throw new Error(`${ticker} is outside coverage.`);
    if(!cache.has(ticker)) {
      const task=Promise.all([getJSON(`companies/${ticker}.json`),ensureCompany(ticker)]).then(([b])=>({...b,ticker,industry:IDX[ticker].industry,sector:b.sector || IDX[ticker].sector}));
      cache.set(ticker,task);task.catch(()=>cache.delete(ticker));
    }
    const c=await cache.get(ticker);
    if(extra && !c._extras) {
      const paths=[`insiders/${ticker}.json`,`guidance/${ticker}.json`,`calls/${ticker}.json`,`kpis/${ticker}.json`];
      const rs=await Promise.allSettled(paths.map(p=>getJSON(p)));
      [c.insiders,c.guidance,c.calls,c.kpis]=rs.map(x=>x.status==='fulfilled'?x.value:null);
      c.coverageGaps=rs.map((r,i)=>r.status==='rejected'?paths[i]:null).filter(Boolean);
      if(c.insiders)c.insiders.cik=c.cik;
      c._extras=true;
      A.attachBusinessMetrics(c,c.kpis);
      if(metricsList){const existing=new Set([...metricsList.options].map(x=>x.value));for(const name of Object.keys(c.extraSeries)){if(existing.size>=2000)break;if(!existing.has(name)){metricsList.append(el('option',{value:name}));existing.add(name);}}}
    }
    return c;
  }
  async function loadMany(tickers,{extra=false,onProgress}={}) {
    const companies=[],failed=[];let next=0,done=0;
    await Promise.all(Array.from({length:Math.min(5,tickers.length)},async()=>{while(next<tickers.length){const t=tickers[next++];try{companies.push(await load(t,extra));}catch(e){failed.push({ticker:t,reason:e.message});}onProgress?.(++done,tickers.length);}}));
    return {companies:companies.sort((a,b)=>tickers.indexOf(a.ticker)-tickers.indexOf(b.ticker)),failed};
  }
  function changesTable(c,compact=false) {
    const report=A.changes(c),table=el('table',{class:'an-table'},el('thead',{},el('tr',{},...['Metric','Reported','YoY','QoQ'].map(x=>el('th',{},x))))),body=el('tbody');
    for(const r of (compact?report.rows.filter(r=>['Revenue','Operating margin','Free cash flow'].includes(r.metric)):report.rows))body.append(el('tr',{},el('td',{},r.metric),el('td',{},value(r.current)),el('td',{},value(r.yoy,{change:true,pp:r.changeType==='percentage_points'})),el('td',{},value(r.qoq,{change:true,pp:r.changeType==='percentage_points'}))));
    table.append(body);return el('div',{class:'an-table-wrap'},table);
  }
  function companyActions(c) {return el('div',{class:'an-actions'},button('Open company',()=>location.hash='company-'+c.ticker.toLowerCase()),button('Build model',()=>FU_WORKSPACE.openModel(c.ticker)),button('Draft report',()=>FU_WORKSPACE.openReport(c.ticker,A.report(c)),true));}
  function renderEvents(events) {
    if(!events.length)return note('No new filings or earnings releases in this window.');
    return el('div',{class:'an-events'},...events.slice(0,10).map(e=>el('div',{class:'an-event'},badge(e.type==='release'?'Release':e.title),link(e.type==='release'?'Earnings release':`${e.title} filing`,e.url),el('time',{datetime:e.date},e.date))));
  }
  async function pAnalyst() {
    const host=el('div',{class:'analyst-page'});page.append(host);
    const since=saved('briefSince',new Date(Date.now()-7*864e5).toISOString().slice(0,10));
    const dateInput=el('input',{type:'date',value:since,max:new Date().toISOString().slice(0,10),'aria-label':'Show events since'});
    host.append(header('Your research desk','The morning brief','What arrived, what changed, and which questions deserve your attention.',[button('Manage watchlist',()=>location.hash='watchlists'),button('Research ideas',()=>location.hash='ideas',true)]));
    const toolbar=el('div',{class:'an-toolbar'},field('Since',dateInput),button('Refresh brief',()=>{save('briefSince',dateInput.value);route();}),badge('Filed figures · source linked'));
    host.append(toolbar);
    const progress=note('Loading your watchlist…');host.append(progress);
    const {companies,failed}=await loadMany(watchlist(),{onProgress:(n,total)=>progress.textContent=`Reading ${n} of ${total} companies…`});
    if(!host.isConnected)return;progress.remove();
    const brief=A.brief(companies,{since,failed});
    host.append(el('div',{class:'an-summary-grid'},...[[String(companies.length),'Companies followed'],[String(brief.eventCount),'New filings & releases'],[String(saved('rules',[]).filter(r=>r.enabled).length),'Standing orders'],[new Date().toLocaleDateString('en-US',{month:'short',day:'numeric'}),'Brief prepared']].map(([v,l])=>el('div',{class:'an-summary'},el('strong',{},v),el('span',{},l)))));
    if(failed.length)host.append(note('Could not load: '+failed.map(x=>x.ticker).join(', ')+'. The brief does not cover these companies.'));
    if(!companies.length)host.append(card('Start with a watchlist',el('div',{class:'an-card-body'},el('p',{},'Follow the companies you research. Their filings and quarterly changes will appear here.'),button('Choose companies',()=>location.hash='watchlists',true))));
    const list=el('div',{class:'an-company-grid'});
    for(const item of brief.items) {
      const c=companies.find(c=>c.ticker===item.ticker),rev=item.changes.rows.find(r=>r.metric==='Revenue'),margin=item.changes.rows.find(r=>r.metric==='Operating margin');
      const body=el('div',{class:'an-card-body'},el('div',{class:'an-meta'},`${item.changes.periodEnd || 'No reporting period'} · available ${item.changes.availableAt || 'date unavailable'}`),renderEvents(item.events),changesTable(c,true));
      if(rev?.yoy || margin?.yoy)body.append(el('div',{class:'an-question'},el('b',{},'Question to investigate'),el('p',{},rev?.yoy?.v>0 && margin?.yoy?.v<0?'Revenue grew while operating margin fell. Check pricing, mix and operating costs in the filing.':rev?.yoy?.v<0?'Revenue declined from the comparable prior-year period. Check the reported demand and mix explanations.':'Check whether the reported changes support your investment thesis and whether the same pattern holds in segment results.')));
      body.append(companyActions(c));list.append(card(`${item.ticker} · ${SHORT[item.ticker] || ''}`,body,item.events.length?badge('New activity','positive'):badge('Quarterly view')));
    }
    host.append(list);
    const ruleHost=el('div',{class:'an-card-body'});host.append(card('Standing order checks',ruleHost));
    await renderRuleResults(ruleHost,companies);
  }
  async function renderRuleResults(host,companies) {
    const rules=saved('rules',[]).filter(r=>r.enabled);
    if(!rules.length){host.append(note('No active standing orders.'),button('Create a standing order',()=>location.hash='watchlists'));return;}
    for(const r of rules) {
      const relevant=companies.filter(c=>A.inScope(r,c,watchlist()));
      const results=[];
      for(const c of relevant) {await load(c.ticker,true);results.push(A.evaluateRule(r,c,{prices:PX[c.ticker]?.bars || [],insiders:c.insiders,watchlist:watchlist()}));}
      const hits=results.filter(x=>x.status==='triggered'),unknown=results.filter(x=>x.status==='unknown');
      host.append(el('div',{class:'an-rule-row'},el('div',{},el('b',{},r.text || A.describeRule(r)),el('p',{class:'muted'},A.describeRule(r))),badge(`${hits.length} triggered`,hits.length?'attention':''),unknown.length?badge(`${unknown.length} need data`):null,
        ...hits.map(x=>el('div',{class:'an-observations'},el('b',{},x.ticker),...x.observations.map(v=>value(v))))));
      if(r.scope==='all' || r.scope==='banks')host.append(note('This brief checks loaded watchlist companies. Use “Run full scope” in Watchlists for wider coverage. Scheduled checks use the saved rule scope.'));
    }
  }
  function ruleForm(host,onSaved,initial=null) {
    const text=el('textarea',{rows:3,placeholder:'Tell me if any bank’s credit card losses rise 2 quarters in a row','aria-label':'Standing order in plain English'},initial?.text || '');
    const preview=el('div',{class:'an-rule-preview','aria-live':'polite'});let proposed=null;
    const metric=metricsSelect(initial?.metric),type=el('select',{},el('option',{value:'trend'},'Consecutive change'),el('option',{value:'threshold'},'Threshold'),el('option',{value:'insider_drawdown'},'Insider purchase after drawdown'));
    const direction=el('select',{},el('option',{value:'up'},'Rises'),el('option',{value:'down'},'Falls'));
    const count=el('input',{type:'number',min:1,max:8,value:2,'aria-label':'Consecutive quarters'});
    const threshold=el('input',{type:'number',step:'any',value:0,'aria-label':'Threshold in raw units'});
    const op=el('select',{},el('option',{value:'gte'},'At least'),el('option',{value:'lte'},'At most'));
    const scope=el('select',{},el('option',{value:'watchlist'},'My watchlist'),el('option',{value:'banks'},'Banks in coverage'),el('option',{value:'all'},'All covered companies'));
    const manual=el('details',{class:'an-details'},el('summary',{},'Set the exact rule'),el('div',{class:'an-form-grid'},field('Rule type',type),field('Scope',scope),field('Metric',metric),field('Direction',direction),field('Consecutive quarters',count),field('Threshold operator',op),field('Threshold (raw units; 0.2 = 20%)',threshold)),button('Preview exact rule',()=>{
      proposed={id:initial?.id || id(),text:text.value,type:type.value,scope:scope.value,metric:metric.value,direction:direction.value,consecutive:Number(count.value),operator:op.value,threshold:Number(threshold.value),drawdown:Number(threshold.value),enabled:true};
      drawPreview();
    }));
    function drawPreview() {
      preview.innerHTML='';const check=A.validateRule(proposed);
      if(!check.ok){preview.append(note(check.error));return;}
      preview.append(badge('Ready to review'),el('p',{},A.describeRule(proposed)),note('Missing metrics produce “Needs data.” A rule only checks observable values; it does not infer them.'),button('Activate standing order',()=>{const rules=saved('rules',[]).filter(r=>r.id!==proposed.id);if(save('rules',[...rules,proposed]))onSaved();},true));
    }
    host.append(text,button('Interpret rule',()=>{const p=A.compileRule(text.value);preview.innerHTML='';if(p.rule){proposed={...p.rule,id:initial?.id || id()};drawPreview();}else preview.append(note(p.explanation));}),preview,manual);
  }
  async function pWatchlists() {
    const host=el('div',{class:'analyst-page'});page.append(host);
    host.append(header('Research that continues','Watchlists & standing orders','Follow companies, define what matters, and control how your analyst checks in.',[button('Read morning brief',()=>location.hash='analyst',true)]));
    const picker=selectTicker(watchlist()[0] || TICKERS[0]),list=el('div',{class:'an-watch-list'});
    const draw=()=>{list.innerHTML='';for(const t of watchlist())list.append(el('div',{class:'an-watch-row'},el('a',{href:'#company-'+t.toLowerCase()},el('b',{},t),el('span',{},SHORT[t])),el('span',{class:'muted'},IDX[t].sector),button('Remove',()=>{toggleWatch(t);draw();})));};draw();
    host.append(card('Companies you follow',el('div',{class:'an-card-body'},el('div',{class:'an-toolbar'},picker,button('Follow company',()=>{if(!watchlist().includes(picker.value))toggleWatch(picker.value);draw();},true)),list)));
    const grid=el('div',{class:'an-two-col'}),form=el('div',{class:'an-card-body'}),rulesHost=el('div',{class:'an-card-body'});
    ruleForm(form,()=>route());
    for(const r of saved('rules',[]))rulesHost.append(el('div',{class:'an-rule-row'},el('b',{},r.text || A.describeRule(r)),el('p',{},A.describeRule(r)),el('div',{class:'an-actions'},badge(r.enabled?'Active':'Paused',r.enabled?'positive':''),button(r.enabled?'Pause':'Resume',()=>{save('rules',saved('rules',[]).map(x=>x.id===r.id?{...x,enabled:!x.enabled}:x));route();}),button('Delete',()=>{save('rules',saved('rules',[]).filter(x=>x.id!==r.id));route();}),button('Run full scope',async e=>{
      const b=e.currentTarget;b.disabled=true;b.textContent='Checking…';
      const targets=r.scope==='watchlist'?watchlist():r.scope==='tickers'?r.tickers:r.scope==='banks'?TICKERS.filter(t=>IDX[t].sector==='Financials'):TICKERS;
      const result=await loadMany(targets,{extra:true,onProgress:(n,total)=>b.textContent=`${n}/${total}`});
      const rows=result.companies.map(c=>A.evaluateRule(r,c,{prices:PX[c.ticker]?.bars || [],insiders:c.insiders,watchlist:watchlist()})).filter(x=>x.status!=='out_of_scope');
      const out=el('div',{class:'an-rule-result'},note(`${rows.filter(x=>x.status==='triggered').length} triggered; ${rows.filter(x=>x.status==='unknown').length} need data; ${result.failed.length} could not load.`));
      for(const row of rows.filter(x=>x.status!=='clear'))out.append(el('p',{},el('b',{},row.ticker+' · '+statusNames[row.status]),' — '+row.reason,...row.observations.map(v=>value(v))));
      b.parentElement.parentElement.querySelector('.an-rule-result')?.remove();b.parentElement.parentElement.append(out);b.disabled=false;b.textContent='Run full scope';
    }))));
    if(!saved('rules',[]).length)rulesHost.append(note('Your activated rules will appear here.'));
    grid.append(card('Give your analyst a standing order',form),card('Active research instructions',rulesHost));host.append(grid);
    const account=el('div',{class:'an-card-body'});host.append(card('Morning brief & email alerts',account));
    if(window.FU_CLOUD)await FU_CLOUD.panel(account,{getLocal:()=>({watchlist:watchlist(),rules:saved('rules',[]),theses:readTheses()}),applyRemote:s=>{if(s.watchlist){store.set('watch',s.watchlist);renderRail();}save('rules',s.rules || []);save('theses',s.theses || []);route();}});
    else account.append(note('Cloud monitoring is not configured. Watchlists and manual checks are saved in this browser.'));
  }
  function readTheses() {
    let t=saved('theses',null);if(t)return t;
    const old=store.get('theses',[]);t=old.map(x=>({...x,criteria:(x.checks || []).map(c=>({metric:c.metric,operator:{'>=':'gte','>':'gt','<=':'lte','<':'lt'}[c.op] || 'gte',threshold:c.value}))}));
    if(t.length)save('theses',t);return t;
  }
  async function pGuardian() {
    const host=el('div',{class:'analyst-page'});page.append(host);
    host.append(header('Thesis guardian','Turn a thesis into a test','Define measurable conditions. Each score comes from reported evidence, with missing coverage kept visible.'));
    const title=el('textarea',{rows:2,maxlength:2000,placeholder:'AMD gains data-center share','aria-label':'Your investment thesis'}),tk=selectTicker(watchlist()[0] || 'AMD'),metric=metricsSelect('Revenue');
    const ruleType=el('select',{'aria-label':'Criterion type'},el('option',{value:'threshold'},'Threshold'),el('option',{value:'trend'},'Consecutive quarterly change'));
    const operator=el('select',{'aria-label':'Criterion operator'},el('option',{value:'gte'},'At least'),el('option',{value:'gt'},'Greater than'),el('option',{value:'lte'},'At most'),el('option',{value:'lt'},'Less than'));
    const threshold=el('input',{type:'number',step:'any',placeholder:'0.2 = 20%','aria-label':'Criterion threshold'}),direction=el('select',{'aria-label':'Criterion direction'},el('option',{value:'up'},'Rises'),el('option',{value:'down'},'Falls')),count=el('input',{type:'number',min:1,max:8,value:2,'aria-label':'Criterion consecutive quarters'}),criteria=el('div',{class:'an-criteria'});
    let pending=[],editIndex=null,activeRead=null,staged=null,assistantMounted=false;
    const assistantStatus=el('div',{class:'an-note',role:'status'}),proposalHost=el('div',{'aria-live':'polite'}),assistantChat=el('div',{class:'panel ai',style:'min-height:420px;max-height:660px;overflow:hidden'});
    const fingerprint=()=>JSON.stringify({ticker:tk.value,title:title.value,pending});
    const criterionLabel=c=>c.type==='trend'?`${c.metric} ${c.direction==='up'?'rises':'falls'} for ${c.consecutive} consecutive quarterly comparisons`:`${c.metric} ${c.operator} ${c.threshold}${c.unit?' '+c.unit:''}`;
    function draftChanged(){activeRead=null;if(staged?.status==='pending'){staged.status='stale';drawProposal();}assistantStatus.textContent='The draft changed. Ask the assistant to read the current company, thesis and criteria before proposing edits.';}
    title.addEventListener('input',draftChanged);title.addEventListener('change',draftChanged);tk.addEventListener('change',draftChanged);
    const addButton=button('Add criterion',()=>{
      const candidate=ruleType.value==='trend'?{type:'trend',metric:metric.value,direction:direction.value,consecutive:Number(count.value)}:{type:'threshold',metric:metric.value,operator:operator.value,threshold:Number(threshold.value),thresholdBasis:'user_review_estimate'};
      if(candidate.type==='threshold' && !threshold.value.trim()){toast('Enter an exact threshold.');return;}
      const valid=A.validateRule({...candidate,scope:'tickers',tickers:[tk.value]});if(!valid.ok){toast(valid.error);return;}
      if(editIndex===null && pending.length>=20){toast('A thesis can contain at most twenty criteria.');return;}
      if(editIndex!==null)pending[editIndex]=candidate;else pending.push(candidate);
      editIndex=null;addButton.textContent='Add criterion';draftChanged();drawCriteria();
    });
    function drawCriteria(){
      criteria.replaceChildren();pending.forEach((c,i)=>criteria.append(el('div',{},el('span',{},criterionLabel(c),c.type!=='trend'?' · user-review estimate':''),el('div',{class:'an-actions'},button('Edit criterion',()=>{editIndex=i;metric.value=c.metric;ruleType.value=c.type || 'threshold';operator.value=c.operator || 'gte';threshold.value=c.threshold ?? '';direction.value=c.direction || 'up';count.value=c.consecutive || 2;addButton.textContent='Save criterion';metric.focus();}),button('Remove',()=>{pending.splice(i,1);editIndex=null;addButton.textContent='Add criterion';draftChanged();drawCriteria();})))));
    }
    const form=el('div',{class:'an-card-body'},field('Company',tk),field('Thesis',title),el('div',{class:'an-form-grid'},field('Measurable metric',metric),field('Criterion type',ruleType),field('Threshold condition',operator),field('Threshold in raw units',threshold),field('Trend direction',direction),field('Consecutive quarterly comparisons',count)),addButton,criteria,note('Thresholds are user-review estimates, not reported forecasts. Percent values use fractions: 0.2 = 20%. Market share needs a sourced numerator and denominator. Revenue growth alone does not prove a market-share thesis.'),button('Track this thesis',()=>{if(!title.value.trim() || !pending.length){toast('Write a thesis and add at least one measurable criterion.');return;}if(save('theses',[{id:id(),ticker:tk.value,title:title.value.trim(),criteria:pending,created:new Date().toISOString()},...readTheses()]))route();},true));
    host.append(card('Create a thesis',form));
    function metricNames(company){return [...new Set([...Object.values(company.statements || {}).flat().map(r=>r.label),...A.metrics,'Revenue growth YoY','Net margin','Gross margin',...Object.keys(company.extraSeries || {})])].filter(n=>typeof n==='string' && n).sort();}
    function metricDescription(company,name,full=false){
      const points=A.series(company,name),selected=points.slice(full?-9:-1);
      if(!selected.length || !selected.at(-1).unit)return {metric:name,available:false,reason:'No usable quarterly observations in this company’s sourced coverage.',observations:[]};
      return {metric:name,unit:selected.at(-1).unit,available:true,totalObservations:points.length,observations:selected.map(p=>({value:p.v,unit:p.unit,period:p.end,availableAt:p.availableAt,evidenceId:p.evidence?.id,citation:p.evidence?FU_EVIDENCE.citation(p.evidence.id):null,sourceUrl:p.evidence?.sourceUrl || null,validation:p.evidence?.validation?.status || 'unverified'}))};
    }
    async function readDraft(input={}){
      const before=fingerprint(),ticker=tk.value,company=await load(ticker,true);
      if(before!==fingerprint() || !host.isConnected)return {status:'stale',reason:'The company, thesis or pending criteria changed while metrics loaded. Read the draft again.'};
      const names=metricNames(company),start=Math.max(0,Math.floor(Number(input.offset)||0)),limit=Math.min(12,Math.max(1,Math.floor(Number(input.limit)||8)));
      const selected=input.metric?names.filter(n=>n===input.metric):names.slice(start,start+limit);
      activeRead={token:id(),fingerprint:before,ticker,company,names:new Set(names)};
      const result={status:'ready',draftToken:activeRead.token,ticker,thesis:title.value,pendingCriteria:pending.map(c=>({...c})),periodPolicy:'Quarterly only. Exact raw units; pct values are fractions. Threshold targets are user-review estimates, not filed facts.',metrics:selected.map(n=>metricDescription(company,n,!!input.metric)),totalMetrics:names.length,offset:start,nextOffset:!input.metric && start+limit<names.length?start+limit:null,truncated:!input.metric && start+limit<names.length,gaps:input.metric && !selected.length?[`Exact metric “${String(input.metric).slice(0,200)}” is not mapped for ${ticker}. No proxy was substituted.`]:[]};
      assistantStatus.textContent=`Read ${ticker} draft and sourced metric coverage. A proposal will require your review before anything is added.`;
      return result;
    }
    function shareClaim(){return /market[\s-]*share|data[\s-]*cent(?:er|re).{0,25}\bshare\b/i.test(title.value);}
    function validateCriterion(raw){
      if(!raw || !['threshold','trend'].includes(raw.type))throw new Error('Thesis proposals support threshold and trend criteria only. Other rule types need a separate standing order.');
      if(typeof raw.metric!=='string' || !activeRead.names.has(raw.metric))throw new Error('The exact proposed metric is unavailable for this company. Read its metric coverage; no proxy will be substituted.');
      const observation=A.series(activeRead.company,raw.metric).at(-1);
      if(!observation)throw new Error(`Missing quarterly coverage for ${raw.metric}. Explain the gap instead of substituting another metric.`);
      if(typeof raw.unit!=='string' || raw.unit!==observation.unit)throw new Error(`Metric ${raw.metric} requires exact raw unit ${observation.unit}; received ${raw.unit}.`);
      if(shareClaim() && (!/market[\s-]*share|data[\s-]*cent(?:er|re).{0,25}share|share.{0,25}data[\s-]*cent(?:er|re)/i.test(raw.metric) || raw.unit!=='pct'))throw new Error('This market-share thesis needs a sourced market-share metric or comparable numerator and market denominator. Revenue, margins and share counts do not measure market share; no proxy was substituted.');
      const criterion={type:raw.type,metric:raw.metric,unit:raw.unit,rationale:typeof raw.rationale==='string'?raw.rationale.slice(0,1000):''};
      if(raw.type==='threshold'){
        if(raw.thresholdBasis!=='user_review_estimate')throw new Error('A proposed threshold must be labeled user_review_estimate, never a reported fact or forecast.');
        if(typeof raw.threshold!=='number' || !Number.isFinite(raw.threshold))throw new Error('Threshold must be a finite number in the metric’s raw units.');
        Object.assign(criterion,{operator:raw.operator,threshold:raw.threshold,thresholdBasis:'user_review_estimate'});
      }else Object.assign(criterion,{direction:raw.direction,consecutive:raw.consecutive});
      const check=A.validateRule({...criterion,scope:'tickers',tickers:[tk.value]});if(!check.ok)throw new Error(check.error);
      if(!Array.isArray(raw.evidenceIds) || !raw.evidenceIds.length || raw.evidenceIds.length>9 || raw.evidenceIds.some(e=>typeof e!=='string'))throw new Error('Supply one to nine exact evidence IDs for this metric.');
      criterion.evidenceIds=[...new Set(raw.evidenceIds)];
      for(const evidenceId of criterion.evidenceIds){const record=FU_EVIDENCE.get(evidenceId);if(!record || record.ticker!==tk.value || record.metric!==criterion.metric || record.unit!==criterion.unit || !Number.isFinite(record.value))throw new Error('Referenced evidence must resolve to this exact company, metric and unit.');if(record.validation.status!=='valid')throw new Error('Referenced evidence has incomplete provenance. Explain the coverage gap instead of presenting it as a verified criterion.');}
      const evaluation=A.evaluateRule({...criterion,scope:'tickers',tickers:[tk.value]},activeRead.company);
      if(!['clear','triggered'].includes(evaluation.status))throw new Error(evaluation.reason || 'The required consecutive quarterly history is unavailable.');
      if(evaluation.observations.some(p=>!p.evidence?.id || !criterion.evidenceIds.includes(p.evidence.id)))throw new Error('Reference the current observed evidence for this test, including every quarter in a trend sequence. Read the exact metric for its recent evidence IDs.');
      return {criterion,observations:evaluation.observations};
    }
    function proposeCriteria(input){
      if(!activeRead || input.draftToken!==activeRead.token || activeRead.fingerprint!==fingerprint() || !host.isConnected)throw new Error('The draft changed or its read token is stale. Read the current thesis draft before proposing criteria.');
      if(!title.value.trim())throw new Error('Write the investment thesis before proposing measurable criteria.');
      if(!Array.isArray(input.criteria) || input.criteria.length>10)throw new Error('Propose up to ten structured criteria at a time.');
      if(pending.length+input.criteria.length>20)throw new Error('Accepting this proposal would exceed twenty thesis criteria.');
      if(input.gaps!==undefined && (!Array.isArray(input.gaps) || input.gaps.length>10 || input.gaps.some(x=>typeof x!=='string')))throw new Error('Coverage gaps must be up to ten plain-text explanations.');
      const gaps=(input.gaps || []).map(x=>x.slice(0,1500)),items=input.criteria.map(validateCriterion);
      if(!items.length && !gaps.length)throw new Error('Propose measurable criteria or explain a specific coverage gap.');
      staged={status:items.length?'pending':'gaps',fingerprint:fingerprint(),ticker:tk.value,thesis:title.value,summary:typeof input.summary==='string'?input.summary.slice(0,1500):'Proposed thesis criteria',items,gaps};drawProposal();
      return {status:items.length?'pending_review':'coverage_gaps',criteriaCount:items.length,gaps,saved:false,instruction:'Nothing has been tracked. The user may accept criteria into the pending draft, edit them, and explicitly click Track this thesis.'};
    }
    function drawProposal(){
      proposalHost.replaceChildren();if(!staged)return;
      proposalHost.append(el('div',{class:'an-rule-preview'},el('b',{},staged.summary),badge(staged.status==='pending'?'Review proposed criteria':staged.status==='stale'?'Stale proposal':staged.status==='accepted'?'Added to draft':staged.status==='rejected'?'Rejected':'Coverage gaps'),el('p',{},`${staged.ticker} · ${staged.thesis}`)));
      const preview=proposalHost.firstChild;
      for(const item of staged.items){preview.append(el('div',{class:'an-rule-row'},el('b',{},criterionLabel(item.criterion)),item.criterion.type==='threshold'?note('Target is a user-review estimate, not a reported fact, consensus estimate or company guidance.'):note('The selected count describes consecutive quarter-to-quarter comparisons.'),item.criterion.rationale?note('Assistant rationale (unverified interpretation): '+item.criterion.rationale):null,el('div',{class:'an-observations'},...item.observations.map(point=>el('div',{},el('small',{},`Observed ${point.label} · ${point.end}`),value(point))))));}
      for(const gap of staged.gaps)preview.append(note('Coverage gap: '+gap));
      if(staged.status==='pending')preview.append(el('div',{class:'an-actions'},button('Add proposed criteria to draft',()=>{
        if(staged.fingerprint!==fingerprint() || staged.status!=='pending'){staged.status='stale';drawProposal();throw new Error('The draft changed. Review a new proposal for the current thesis.');}
        pending.push(...staged.items.map(x=>JSON.parse(JSON.stringify(x.criterion))));staged.status='accepted';activeRead=null;drawCriteria();drawProposal();assistantStatus.textContent='Added proposed criteria to the pending draft only. Review or edit them, then click Track this thesis to save.';
      },true),button('Reject proposed criteria',()=>{staged.status='rejected';drawProposal();})));
      if(staged.status==='stale')preview.append(note('Your newer company, thesis and criterion edits are preserved. Ask the assistant to read the draft again.'));
    }
    function openAssistant(){
      if(assistantMounted)return;
      if(typeof chatPanel!=='function'){assistantStatus.textContent='The assistant is unavailable. You can still add and track criteria manually.';return;}
      assistantMounted=true;assistantBody.append(assistantChat);assistantStatus.textContent='The assistant can read exact metric coverage and stage criteria. Targets remain estimates for your review; tracking always requires your explicit action.';
      chatPanel(assistantChat,{id:'thesis-draft',intro:'Describe the thesis and ask me to decompose it. I will inspect the chosen company’s exact metric names, units and evidence before proposing conditions. Missing market-share coverage remains a gap.',placeholder:'Help turn this thesis into measurable checks…',suggestions:['Read my thesis and explain which parts can be measured with sourced data','Propose criteria for review, and explain any missing evidence'],rules:'You are a careful thesis research editor. Always read_thesis_draft before proposing edits; bind every proposal to its returned draftToken. Do not save or track theses. Use only exact quarterly metrics and units returned by the draft reader; request metric-specific reads for trend evidence. All threshold targets must use thresholdBasis user_review_estimate and be described as assumptions for user review, never reported facts, consensus or guidance. Reference every current observation evidence ID used by a threshold or trend. Do not substitute revenue, margins or stock share counts for market share. A market-share claim needs a sourced share series or a comparable numerator and market denominator; explain missing coverage in gaps and propose no proxy. If a metric or complete trend is unavailable, explain the gap instead of silently changing the metric. The user accepts, edits or rejects proposals and explicitly tracks the thesis. Narrative rationale is interpretation for review, not verified evidence.',context:()=>JSON.stringify({ticker:tk.value,thesis:title.value.slice(0,2000),pendingCriteria:pending,proposalStatus:staged?.status || null}),tools:trace=>{
        const tool=(name,description,properties,required,execute)=>({name,description,inputSchema:{type:'object',properties,required,additionalProperties:false},execute:async input=>{const step=trace?.(name,input);try{const result=await execute(input || {});step?.done(result);return result;}catch(error){assistantStatus.textContent=error.message;step?.fail(error);throw error;}}});
        return [tool('read_thesis_draft','Read the current unsaved thesis and paginated exact metric coverage. A metric-specific read returns up to nine recent quarterly evidence records. Never changes the draft.',{metric:{type:'string'},offset:{type:'integer',minimum:0},limit:{type:'integer',minimum:1,maximum:12}},[],readDraft),tool('propose_thesis_criteria','Stage measurable threshold/trend conditions or explain coverage gaps. Does not edit pending criteria or saved theses; user review is mandatory.',{draftToken:{type:'string'},summary:{type:'string'},criteria:{type:'array',maxItems:10,items:{type:'object',additionalProperties:false,properties:{type:{type:'string',enum:['threshold','trend']},metric:{type:'string'},unit:{type:'string'},operator:{type:'string',enum:['gt','gte','lt','lte']},threshold:{type:'number'},thresholdBasis:{type:'string',enum:['user_review_estimate']},direction:{type:'string',enum:['up','down']},consecutive:{type:'integer',minimum:1,maximum:8},evidenceIds:{type:'array',minItems:1,maxItems:9,items:{type:'string'}},rationale:{type:'string'}},required:['type','metric','unit','evidenceIds']}},gaps:{type:'array',maxItems:10,items:{type:'string'}}},['draftToken','criteria'],proposeCriteria)];
      }});
    }
    const assistantBody=el('div',{class:'an-card-body'},note('Optional: ask the assistant to separate measurable conditions from evidence gaps. Proposed criteria are reviewed before they enter your draft.'),button('Open thesis assistant',openAssistant),assistantStatus,proposalHost);
    host.append(card('Decompose your thesis with evidence',assistantBody));
    for(const t of readTheses()) {
      const body=el('div',{class:'an-card-body'});host.append(card(`${t.ticker} · ${t.title}`,body));
      try {const c=await load(t.ticker,true),r=A.thesis(t,c);body.append(badge(statusNames[r.status],r.status==='holding'?'positive':r.status==='broken'?'attention':''),note(`${r.measured} of ${r.total} criteria measurable${r.score!==null?'; '+Math.round(r.score*100)+'% of measurable criteria currently hold':''}.`));
        for(const criterion of r.criteria)body.append(el('div',{class:'an-rule-row'},el('b',{},criterion.metric),criterionLabel(criterion)+` · ${statusNames[criterion.result.status]}`,...criterion.result.observations.map(v=>value(v))));
        body.append(button('Edit thesis & criteria',()=>{title.value=t.title;tk.value=t.ticker;pending=t.criteria.map(c=>({...c}));editIndex=null;addButton.textContent='Add criterion';draftChanged();drawCriteria();form.scrollIntoView({behavior:'smooth'});const saveEdit=button('Save changes to this thesis',()=>{if(!title.value.trim() || !pending.length)return;if(save('theses',readTheses().map(x=>x.id===t.id?{...x,title:title.value.trim(),ticker:tk.value,criteria:pending}:x)))route();},true);form.querySelector('[data-edit-save]')?.remove();saveEdit.dataset.editSave='1';form.append(saveEdit);}),button('Remove thesis',()=>{save('theses',readTheses().filter(x=>x.id!==t.id));route();}));
      }catch(e){body.append(note(e.message));}
    }
  }
  async function pIdeas() {
    const host=el('div',{class:'analyst-page'});page.append(host);
    host.append(header('Evidence before conviction','Research ideas','Scan your watchlist, compare peers, and test an idea against the information available at the time.'));
    const selected=selectTicker(saved('ideaTicker',watchlist()[0] || 'AAPL')),peer=selectTicker(saved('peerTicker','MSFT'));
    const body=el('div',{class:'an-stack'});host.append(el('div',{class:'an-toolbar'},field('Company',selected),field('Compare with',peer),button('Analyze',()=>{save('ideaTicker',selected.value);save('peerTicker',peer.value);route();},true),button('Scan watchlist',async e=>{
      const scanButton=e.currentTarget;scanButton.disabled=true;const results=await loadMany(watchlist()),scan=el('div',{class:'an-card-body'});
      for(const c of results.companies)for(const type of ['revenue_acceleration','margin_turn']){const s=A.signal(c,type);if(s.status==='triggered')scan.append(signalCard(c,s));}
      if(!scan.children.length)scan.append(note('No supported signal currently triggers on the loaded watchlist.'));body.prepend(card('Watchlist signals',scan));scanButton.disabled=false;
    })),body);
    body.append(note('Reading reported history…'));
    try {
      const [c,p]=await Promise.all([load(selected.value,true),load(peer.value,true)]);if(!host.isConnected)return;body.innerHTML='';
      const signalGrid=el('div',{class:'an-two-col'});
      for(const type of ['revenue_acceleration','margin_turn'])signalGrid.append(signalCard(c,A.signal(c,type)));body.append(signalGrid);
      const setup=A.earningsSetup(c,PX[c.ticker]?.bars || []),assumption=el('input',{type:'number',step:.1,value:setup.assumption===null?'':(setup.assumption*100).toFixed(1),'aria-label':'Forecast growth assumption percent'}),forecast=el('div',{class:'an-forecast'});
      const drawForecast=()=>{const r=A.earningsSetup(c,PX[c.ticker]?.bars || [],{growthAssumption:assumption.value===''?null:Number(assumption.value)/100});forecast.innerHTML='';forecast.append(el('div',{class:'an-big'},r.estimate===null?'Insufficient comparable history':fmtMoney(r.estimate,r.unit)),badge('Scenario estimate'),el('p',{},r.forecastLabel),r.seasonalBase?el('p',{},'Prior-year comparable quarter: ',value(r.seasonalBase)):null,note('Consensus is unavailable. This scenario does not claim that market expectations are too high or too low.'));};drawForecast();
      const earnings=el('div',{class:'an-card-body'},field('Editable growth assumption (%)',assumption),button('Recalculate scenario',drawForecast),forecast,note(`Observed post-filing reaction sample: ${setup.reaction?.n || 0}. ${setup.reaction?.methodology || ''}`));
      const compare=A.pair(c,p),pt=el('table',{class:'an-table'},el('thead',{},el('tr',{},...['Metric',c.ticker,p.ticker,'Growth / margin gap'].map(x=>el('th',{},x))))),pb=el('tbody');
      for(const r of compare.rows)pb.append(el('tr',{},el('td',{},r.metric),el('td',{},value(r.a)),el('td',{},value(r.b)),el('td',{},r.gap==null?'Not comparable':(r.gap*100).toFixed(2)+' pp')));pt.append(pb);
      const pairBody=el('div',{class:'an-card-body'},badge(compare.sameSector?'Same sector':'Different sectors'),el('div',{class:'an-table-wrap'},pt),note(compare.interpretation),el('p',{},el('b',{},'Time frame: '),compare.horizon),el('p',{},el('b',{},'What would invalidate it: '),compare.invalidation));
      body.append(el('div',{class:'an-two-col'},card('Earnings setup',earnings),card('Peer divergence',pairBody)));
      const promises=A.promises(c.guidance),promiseBody=el('div',{class:'an-card-body'},note(`${promises.scored} comparable forecasts; ${promises.excluded} excluded pending period/basis verification. ${promises.lastIssued?'Last extracted forecast: '+promises.lastIssued+'.':''}`));
      if(promises.hitRate!==null)promiseBody.append(el('strong',{},(promises.hitRate*100).toFixed(1)+'% met the validated range or direction'));
      for(const r of promises.items.slice(0,6))promiseBody.append(el('div',{class:'an-rule-row'},el('b',{},`${r.metric} · ${r.period}`),badge(r.status==='comparable'?r.result:'Needs verification'),el('blockquote',{},r.quote || 'No exact quotation available.'),link('Management source',r.sourceUrl),note(r.reason)));
      if(!promises.items.length)promiseBody.append(note('No extracted management forecasts are available for this company.'));
      const insider=A.insiderContext(c.insiders || {},PX[c.ticker]?.bars || []),insiderBody=el('div',{class:'an-card-body'});
      for(const t of insider.slice(0,6))insiderBody.append(el('div',{class:'an-rule-row'},el('b',{},t.owner),el('p',{},`${t.tx_date} · open-market purchase · ${t.value===null?'value unavailable':fmtMoney(t.value)} · ${t.drawdown===null?'price history unavailable':pct(t.drawdown)+' from prior high'}`),link('Form 4',t.sourceUrl)));
      if(!insider.length)insiderBody.append(note('No reported open-market purchases in the available transaction history. Grants, option exercises and derivative transactions are excluded.'));
      body.append(el('div',{class:'an-two-col'},card('Promises vs. results',promiseBody),card('Insider buying in context',insiderBody)));
      body.append(card('Disclosure changes',await disclosurePanel(c)));
      const exposure=A.exposure(c,INDEX.companies),ex=el('div',{class:'an-card-body'},note(exposure.limitation));
      for(const r of exposure.documented)ex.append(link(`${r.counterparty} · ${r.relationship}`,r.sourceUrl));
      ex.append(el('div',{class:'an-actions'},...exposure.associations.map(x=>el('a',{class:'chip',href:'#company-'+x.ticker.toLowerCase()},x.ticker))),note('These are sector research leads. No supplier or customer exposure has been inferred.'));
      body.append(card('Potential cross-company research',ex),companyActions(c));
    }catch(e){body.innerHTML='';body.append(note(e.message),button('Retry',()=>route()));}
  }
  function signalCard(c,s) {
    const title=s.type==='margin_turn'?'Operating margin turn':'Revenue acceleration';
    const b=el('div',{class:'an-card-body'},badge(statusNames[s.status],s.status==='triggered'?'positive':''),el('p',{},s.reason),el('div',{class:'an-observations'},...(s.observations || []).map(x=>el('div',{},el('small',{},x.label+' · '+x.end),value(x)))));
    if(s.status!=='unknown') {
      b.append(el('p',{},el('b',{},'Why now: '),s.status==='triggered'?'The latest filed comparison meets the stated condition.':'This condition does not currently trigger.'),el('p',{},el('b',{},'Invalidation: '),s.invalidation),el('p',{},el('b',{},'Time frame: '),s.horizon));
      const study=A.backtest(c,PX[c.ticker]?.bars || [],{type:s.type});
      if(study.status==='available')b.append(el('div',{class:'an-study'},el('strong',{},`${study.n} completed historical observations`),el('p',{},`Average net price return: ${pct(study.averageReturn)} · Positive observations: ${study.hitRate===null?'—':(study.hitRate*100).toFixed(0)+'%'}`),note(study.methodology),el('details',{},el('summary',{},'Inspect historical entries'),...study.trades.map(t=>el('p',{},`${t.publicationDate} published → ${t.entryDate} entry → ${t.exitDate} exit · ${pct(t.netReturn)}`)))));
      else b.append(note(study.reason));
    }
    return card(`${c.ticker} · ${title}`,b);
  }
  function sourcedDisclosures(bundle) {
    const docs=bundle?.docs || {},seen=new Set();
    return (bundle?.calls || []).flatMap(call=>{
      const doc=docs[call.doc] || {},urls=[],urlSeen=new Set();
      const add=(url,title)=>{if(/^https?:\/\//i.test(url || '') && !urlSeen.has(url)){urlSeen.add(url);urls.push({url,title});}};
      add(call.url || call.source_url || doc.url,doc.title || call.title || 'Original source');
      for(const key of call.alt_docs || [])add(docs[key]?.url,docs[key]?.title || 'Additional source document');
      if(!call.call_id || !urls.length || seen.has(call.call_id))return [];
      seen.add(call.call_id);
      const kind=String(call.kind || doc.doc_type || 'disclosure').toLowerCase(),event=String(call.event_type || 'unspecified'),date=String(call.date || call.d || doc.published || '').slice(0,10);
      const typeLabel=({transcript:'Transcript',remarks:'Prepared remarks',release:'Earnings release',presentation:'Presentation'})[kind] || 'Disclosure';
      const period=call.label || doc.fiscal_label || call.period_end || 'Period not specified';
      return [{...call,kind,event,date,period,typeLabel,urls,comparisonType:event+':'+kind,labelText:`${period} · ${typeLabel} · ${date || 'Date unavailable'}`}];
    }).sort((a,b)=>b.date.localeCompare(a.date));
  }
  function disclosureTurnText(payload,call) {
    if(payload?.call_id!==call.call_id)throw new Error('The loaded disclosure ID does not match the selected source.');
    const turns=(Array.isArray(payload.turns)?payload.turns:[]).filter(t=>typeof t.x==='string' && t.x.trim());
    if(!turns.length)throw new Error('Parsed turn text is unavailable for this disclosure. Open its original source or use the manual comparison.');
    return {meta:call,text:turns.map(t=>t.x).join('\n\n'),turns:turns.length,sections:[...new Set(turns.map(t=>t.sec).filter(Boolean))]};
  }
  async function disclosurePanel(c) {
    const host=el('div',{class:'an-card-body'}),calls=sourcedDisclosures(c.calls),lookup=new Map(calls.map(call=>[call.call_id,call]));
    const results=el('div',{'aria-live':'polite'}),sourceStatus=el('div',{class:'an-note',role:'status'}),loaded={prior:null,current:null},textCache=new Map();
    let generation=0,busy=false;
    const makeSelector=(label)=>el('select',{'aria-label':label},el('option',{value:''},'Choose a sourced disclosure'),...calls.map(call=>el('option',{value:call.call_id},call.labelText)));
    const previous=makeSelector('Earlier sourced disclosure'),current=makeSelector('Current sourced disclosure');
    const priorPreview=el('textarea',{rows:7,readonly:true,'aria-label':'Loaded earlier disclosure text'}),currentPreview=el('textarea',{rows:7,readonly:true,'aria-label':'Loaded current disclosure text'}),priorSource=el('div'),currentSource=el('div');
    const compareLoaded=button('Compare loaded disclosures',()=>{
      const reason=comparability();if(reason){toast(reason);return;}
      const before=loaded.prior,after=loaded.current;
      const result=A.disclosureChanges({documentType:before.meta.comparisonType,date:before.meta.date,sourceUrl:before.meta.urls[0].url,text:before.text},{documentType:after.meta.comparisonType,date:after.meta.date,sourceUrl:after.meta.urls[0].url,text:after.text});
      renderComparison(result,`${before.meta.labelText} → ${after.meta.labelText}`,before.meta.urls,after.meta.urls);
    },true);
    compareLoaded.disabled=true;
    function comparability(){
      if(busy)return 'Wait for the selected source text to finish loading.';
      if(!loaded.prior || !loaded.current)return calls.length===1?'Only one sourced disclosure is available. Supply an earlier comparable source in the manual comparison.':'Load two sourced disclosures to compare their exact turn text.';
      const a=loaded.prior.meta,b=loaded.current.meta;
      if(a.call_id===b.call_id)return 'Choose two different disclosures.';
      if(a.comparisonType!==b.comparisonType)return 'Choose disclosures of the same source type and event type; a transcript and prepared remarks are not equivalent.';
      if(!a.date || !b.date || a.date>=b.date)return 'Choose an earlier dated disclosure and a later current disclosure.';
      return null;
    }
    function updateComparisonState(message){const reason=comparability();compareLoaded.disabled=!!reason;sourceStatus.textContent=message || reason || 'Both source texts are loaded. Compare literal phrases, then inspect the original documents.';}
    function sourceSummary(side){
      const row=side==='prior'?priorSource:currentSource,preview=side==='prior'?priorPreview:currentPreview,x=loaded[side];row.replaceChildren();preview.value=x?.text || '';
      if(!x)return;
      const sections=x.sections.map(sec=>({prepared:'prepared remarks',qa:'Q&A',question:'questions',answer:'answers'})[sec] || sec).join(', ');
      row.append(el('p',{class:'an-meta'},`${x.meta.labelText} · period ended ${x.meta.period_end || 'not specified'} · ${x.turns} parsed turns${sections?' · sections: '+sections:''}`),el('div',{class:'an-actions'},...x.meta.urls.map((source,i)=>link(i?'Additional original document':'Original '+x.meta.typeLabel.toLowerCase(),source.url))));
      if(x.meta.urls.length>1)row.append(note('The parsed call corpus can combine the linked original documents. Both links are retained for checking prepared remarks and Q&A.'));
    }
    function selectionChanged(){generation++;busy=false;loaded.prior=null;loaded.current=null;sourceSummary('prior');sourceSummary('current');results.replaceChildren();updateComparisonState('Selection changed. Load the selected original source texts before comparing.');}
    previous.addEventListener('change',selectionChanged);current.addEventListener('change',selectionChanged);
    async function loadSelected(){
      const request=++generation;busy=true;compareLoaded.disabled=true;sourceStatus.textContent='Loading parsed text from the selected source documents…';
      const selected={prior:lookup.get(previous.value),current:lookup.get(current.value)},errors=[];
      await Promise.all(Object.entries(selected).map(async([side,call])=>{
        if(!call){if(request===generation)loaded[side]=null;return;}
        try{
          if(!textCache.has(call.call_id)){const task=getJSON(`calls/${encodeURIComponent(c.ticker)}/${encodeURIComponent(call.call_id)}.json`).then(payload=>disclosureTurnText(payload,call));textCache.set(call.call_id,task);task.catch(()=>textCache.delete(call.call_id));}
          const text=await textCache.get(call.call_id);if(request===generation)loaded[side]=text;
        }catch(error){errors.push(`${call.labelText}: ${error.message}`);if(request===generation)loaded[side]=null;}
      }));
      if(request!==generation)return;
      busy=false;sourceSummary('prior');sourceSummary('current');updateComparisonState(errors.length?errors.join(' '):undefined);
    }
    function renderComparison(result,label,beforeSources=[],afterSources=[]){
      results.replaceChildren(el('p',{class:'an-meta'},label));
      if(result.status!=='available'){results.append(note(result.reason || 'These sources cannot be compared.'));return;}
      results.append(el('div',{class:'an-actions'},...beforeSources.map((s,i)=>link('Earlier source'+(i?' '+(i+1):''),s.url)),...afterSources.map((s,i)=>link('Current source'+(i?' '+(i+1):''),s.url))));
      for(const item of result.items)results.append(el('div',{class:'an-rule-row'},el('b',{},'“'+item.phrase+'”'),` ${item.before} → ${item.after} mentions`,note(item.reason)));
      if(!result.items.length)results.append(note('No change in the tracked literal phrases. This does not establish that management’s tone or disclosure quality is unchanged.'));
      results.append(note('Counts use the full loaded turn text, including any analyst questions and operator remarks. Extraction or section coverage can differ between sources; inspect the originals before interpreting a change.'));
    }
    if(calls.length){
      current.value=calls[0].call_id;
      const older=calls.find(call=>call.call_id!==calls[0].call_id && call.comparisonType===calls[0].comparisonType && call.date && call.date<calls[0].date);if(older)previous.value=older.call_id;
      host.append(el('p',{},'Compare sourced call transcripts or prepared remarks from the available company documents.'),el('div',{class:'an-two-col'},field('Earlier period and source type',previous),field('Current period and source type',current)),el('div',{class:'an-actions'},button('Load selected sources',loadSelected),compareLoaded),sourceStatus,el('div',{class:'an-two-col'},el('div',{},priorSource,el('details',{},el('summary',{},'Inspect earlier loaded text'),priorPreview)),el('div',{},currentSource,el('details',{},el('summary',{},'Inspect current loaded text'),currentPreview))));
      if(calls.length===1)host.append(note('Only one sourced disclosure is available. Its current text loads below; an earlier period is not invented. Use the manual comparison for another sourced document.'));
      else if(!older)host.append(note('No earlier disclosure of the same source type is available by default. Select a comparable pair or use the manual comparison.'));
    }else host.append(note('No sourced call transcript or prepared remarks are available for this company. Add two original URLs and their text in the manual comparison.'));
    const prevText=el('textarea',{rows:6,placeholder:'Earlier source text','aria-label':'Earlier disclosure text'}),nextText=el('textarea',{rows:6,placeholder:'Current source text','aria-label':'Current disclosure text'}),prevURL=el('input',{type:'url',placeholder:'https://…','aria-label':'Earlier source URL'}),nextURL=el('input',{type:'url',placeholder:'https://…','aria-label':'Current source URL'});
    const manualType=el('select',{'aria-label':'Manual comparison source type'},el('option',{value:'transcript'},'Call transcripts'),el('option',{value:'remarks'},'Prepared remarks'),el('option',{value:'release'},'Earnings releases'),el('option',{value:'other'},'Other matching disclosure type'));
    if(c.release?.markdown && /^https?:\/\//i.test(c.release.url || '')){nextText.value=c.release.markdown;nextURL.value=c.release.url;manualType.value='release';}
    const manual=el('details',{class:'an-details',...(calls.length?{}:{open:true})},el('summary',{},'Manual source comparison'),field('Same document type for both sources',manualType),el('div',{class:'an-two-col'},el('div',{},field('Earlier original source URL',prevURL),prevText),el('div',{},field('Current original source URL',nextURL),nextText)),button('Compare sourced language',()=>{
      if(!/^https?:\/\//i.test(prevURL.value) || !/^https?:\/\//i.test(nextURL.value) || !prevText.value.trim() || !nextText.value.trim()){toast('Add both source URLs and both texts.');return;}
      const result=A.disclosureChanges({documentType:'manual:'+manualType.value,sourceUrl:prevURL.value,text:prevText.value},{documentType:'manual:'+manualType.value,sourceUrl:nextURL.value,text:nextText.value});
      renderComparison(result,'Manual text comparison · document equivalence is selected by you',[{url:prevURL.value}],[{url:nextURL.value}]);
    }),note('Provide the same document type and comparable periods. Pasted text is user-supplied; it is not fetched or independently verified against the URLs.'));
    host.append(manual,results,note('Literal comparisons do not establish that management dodged a question, deliberately omitted a metric or withheld information. Such conclusions require an analyst to inspect the complete sources.'));
    if(calls.length)loadSelected().catch(error=>{busy=false;updateComparisonState(error.message);});
    return host;
  }
  async function companyChanges(host,tk) {
    host.append(note('Loading comparable periods…'));
    try{const c=await load(tk);host.innerHTML='';host.append(card('What changed this quarter',el('div',{class:'an-card-body'},note(`Period ended ${A.changes(c).periodEnd}. Each value opens its immutable evidence record.`),changesTable(c),companyActions(c))));}catch(e){host.innerHTML='';host.append(note(e.message));}
  }
  window.FU_PAGES=Object.assign(window.FU_PAGES || {},{analyst:pAnalyst,watchlists:pWatchlists,ideas:pIdeas,theses:pGuardian});
  (window.FU_COTABS=window.FU_COTABS || []).push({id:'changes',name:'What changed',after:'overview',render:companyChanges});
  PAGES.splice(0,0,{id:'analyst',name:'Morning brief',icon:'i-news',key:'b'});
  PAGES.splice(2,0,{id:'watchlists',name:'Watchlists',icon:'i-feed',key:'l'},{id:'ideas',name:'Research ideas',icon:'i-spark',key:'i'});
  const thesisNav=PAGES.find(p=>p.id==='theses');if(thesisNav)thesisNav.name='Thesis guardian';
  function pack(x) { if(Array.isArray(x))return x.map(pack); if(!x || typeof x!=='object')return x; if(typeof x.v==='number' && x.unit && x.label && x.end)return {value:x.v,unit:x.unit,ticker:x.tk,metric:x.label,period:x.end,availableAt:x.availableAt,evidenceId:x.evidence?.id,citation:x.evidence?FU_EVIDENCE.citation(x.evidence.id):null,sourceUrl:x.evidence?.sourceUrl};return Object.fromEntries(Object.entries(x).map(([k,v])=>[k,pack(v)])); }
  (window.FU_TOOL_PROVIDERS=window.FU_TOOL_PROVIDERS || []).push(trace=>{
    const tool=(name,description,properties,required,fn)=>({name,description,inputSchema:{type:'object',properties,required,additionalProperties:false},execute:async input=>{const step=trace?.(name,input);try{const result=pack(await fn(input));step?.done(result);return result;}catch(e){step?.fail(e);throw e;}}});
    return [
      tool('earnings_changes','Exact sourced quarterly changes with immutable evidence IDs.',{ticker:{type:'string'}},['ticker'],async({ticker})=>A.changes(await load(ticker.toUpperCase()))),
      tool('watchlist_brief','Paginated filings, releases and evidence-backed changes in the current watchlist.',{since:{type:'string'},offset:{type:'integer'},limit:{type:'integer'}},[],async({since,offset=0,limit=2})=>{const all=watchlist(),start=Math.max(0,Math.floor(offset)||0),n=Math.min(3,Math.max(1,Math.floor(limit)||2)),r=await loadMany(all.slice(start,start+n));return {...A.brief(r.companies,{since,failed:r.failed}),total:all.length,offset:start,nextOffset:start+n<all.length?start+n:null,truncated:start+n<all.length};}),
      tool('propose_standing_order','Compile a plain-language monitoring instruction into a draft. Does not activate without user review.',{instruction:{type:'string'}},['instruction'],({instruction})=>A.compileRule(instruction)),
      tool('evaluate_research_rule','Evaluate an explicit rule against a company. Missing data returns unknown.',{ticker:{type:'string'},rule:{type:'object'}},['ticker','rule'],async({ticker,rule})=>{const c=await load(ticker.toUpperCase(),true);return A.evaluateRule(rule,c,{prices:PX[c.ticker]?.bars || [],insiders:c.insiders,watchlist:watchlist()});}),
      tool('research_signal','Deterministic signal and point-in-time historical study. Returns unavailable if filing vintages are missing.',{ticker:{type:'string'},signal:{type:'string',enum:['revenue_acceleration','margin_turn']}},['ticker','signal'],async({ticker,signal})=>{const c=await load(ticker.toUpperCase());return {signal:A.signal(c,signal),study:A.backtest(c,PX[c.ticker]?.bars || [],{type:signal})};}),
      tool('compare_peers','Aligned peer metrics; identifies coverage and definition limitations.',{ticker:{type:'string'},peer:{type:'string'}},['ticker','peer'],async({ticker,peer})=>A.pair(await load(ticker.toUpperCase()),await load(peer.toUpperCase()))),
      tool('draft_cited_report','Create a factual report with exact values and source records; no unsupported investment conclusions.',{ticker:{type:'string'}},['ticker'],async({ticker})=>A.report(await load(ticker.toUpperCase())))
    ];
  });
  window.FU_RESEARCH={load,readTheses,getRules:()=>saved('rules',[])};
})();
