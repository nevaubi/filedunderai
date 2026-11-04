/* Proactive research, backed by the same records used by the assistant and workspace. */
(function () {
  'use strict';
  const A=window.FU_ANALYST, cache=new Map();
  const id=()=>crypto.randomUUID();
  const saved=(key,fallback)=>store.get('analyst:'+key,fallback);
  function save(key,value) {try{localStorage.setItem('fu:analyst:'+key,JSON.stringify(value));return true;}catch{toast('Storage is full. Export your workspace before adding more research.');return false;}}
  const button=(text,fn,primary=false)=>el('button',{class:'btn'+(primary?' pri':''),onclick:fn},text);
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
    if(window.FU_CLOUD)await FU_CLOUD.panel(account,{getLocal:()=>({watchlist:watchlist(),rules:saved('rules',[]),theses:readTheses()}),applyRemote:s=>{if(s.watchlist){store.set('watch',s.watchlist);renderRail();}save('rules',s.rules || []);save('theses',s.theses || []);draw();}});
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
    const title=el('textarea',{rows:2,placeholder:'AMD gains data-center share','aria-label':'Your investment thesis'}),tk=selectTicker(watchlist()[0] || 'AMD'),metric=metricsSelect('Revenue'),operator=el('select',{},el('option',{value:'gte'},'At least'),el('option',{value:'lte'},'At most')),threshold=el('input',{type:'number',step:'any',placeholder:'0.2 = 20%','aria-label':'Criterion threshold'}),criteria=el('div',{class:'an-criteria'});let pending=[];
    const add=()=>{const v=Number(threshold.value);if(!threshold.value || !Number.isFinite(v)){toast('Enter an exact threshold.');return;}pending.push({metric:metric.value,operator:operator.value,threshold:v});drawCriteria();};
    const drawCriteria=()=>{criteria.innerHTML='';pending.forEach((c,i)=>criteria.append(el('div',{},`${c.metric} ${c.operator} ${c.threshold}`,button('Remove',()=>{pending.splice(i,1);drawCriteria();}))));};
    const form=el('div',{class:'an-card-body'},field('Company',tk),field('Thesis',title),el('div',{class:'an-form-grid'},field('Measurable metric',metric),field('Condition',operator),field('Threshold in raw units',threshold)),button('Add criterion',add),criteria,note('Market share needs a sourced numerator and denominator. Revenue growth alone does not prove a market-share thesis.'),button('Track this thesis',()=>{if(!title.value.trim() || !pending.length){toast('Write a thesis and add at least one measurable criterion.');return;}save('theses',[{id:id(),ticker:tk.value,title:title.value.trim(),criteria:pending,created:new Date().toISOString()},...readTheses()]);route();},true));
    host.append(card('Create a thesis',form));
    for(const t of readTheses()) {
      const body=el('div',{class:'an-card-body'});host.append(card(`${t.ticker} · ${t.title}`,body));
      try {const c=await load(t.ticker,true),r=A.thesis(t,c);body.append(badge(statusNames[r.status],r.status==='holding'?'positive':r.status==='broken'?'attention':''),note(`${r.measured} of ${r.total} criteria measurable${r.score!==null?'; '+Math.round(r.score*100)+'% of measurable criteria currently hold':''}.`));
        for(const criterion of r.criteria)body.append(el('div',{class:'an-rule-row'},el('b',{},criterion.metric),` ${criterion.operator} ${criterion.threshold} · ${statusNames[criterion.result.status]}`,...criterion.result.observations.map(v=>value(v))));
        body.append(button('Edit thesis & criteria',()=>{title.value=t.title;tk.value=t.ticker;pending=t.criteria.map(c=>({...c}));drawCriteria();form.scrollIntoView({behavior:'smooth'});const saveEdit=button('Save changes to this thesis',()=>{if(!title.value.trim() || !pending.length)return;save('theses',readTheses().map(x=>x.id===t.id?{...x,title:title.value.trim(),ticker:tk.value,criteria:pending}:x));route();},true);form.querySelector('[data-edit-save]')?.remove();saveEdit.dataset.editSave='1';form.append(saveEdit);}),button('Remove thesis',()=>{save('theses',readTheses().filter(x=>x.id!==t.id));route();}));
      }catch(e){body.append(note(e.message));}
    }
  }
  async function pIdeas() {
    const host=el('div',{class:'analyst-page'});page.append(host);
    host.append(header('Evidence before conviction','Research ideas','Scan your watchlist, compare peers, and test an idea against the information available at the time.'));
    const selected=selectTicker(saved('ideaTicker',watchlist()[0] || 'AAPL')),peer=selectTicker(saved('peerTicker','MSFT'));
    const body=el('div',{class:'an-stack'});host.append(el('div',{class:'an-toolbar'},field('Company',selected),field('Compare with',peer),button('Analyze',()=>{save('ideaTicker',selected.value);save('peerTicker',peer.value);route();},true),button('Scan watchlist',async e=>{
      e.currentTarget.disabled=true;const results=await loadMany(watchlist()),scan=el('div',{class:'an-card-body'});
      for(const c of results.companies)for(const type of ['revenue_acceleration','margin_turn']){const s=A.signal(c,type);if(s.status==='triggered')scan.append(signalCard(c,s));}
      if(!scan.children.length)scan.append(note('No supported signal currently triggers on the loaded watchlist.'));body.prepend(card('Watchlist signals',scan));e.currentTarget.disabled=false;
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
  async function disclosurePanel(c) {
    const host=el('div',{class:'an-card-body'}),calls=(c.calls?.calls || []).filter(x=>x.url || x.source_url).sort((a,b)=>String(b.date || b.d).localeCompare(String(a.date || a.d)));
    if(calls.length<2){host.append(note('Two sourced, comparable call or release texts are needed. Upload or paste them below to compare exact language.'));}
    const prevText=el('textarea',{rows:6,placeholder:'Earlier release or call text','aria-label':'Earlier disclosure text'}),nextText=el('textarea',{rows:6,placeholder:'Current release or call text','aria-label':'Current disclosure text'}),prevURL=el('input',{type:'url',placeholder:'https://…','aria-label':'Earlier source URL'}),nextURL=el('input',{type:'url',placeholder:'https://…','aria-label':'Current source URL'}),results=el('div');
    if(c.release?.markdown){nextText.value=c.release.markdown;nextURL.value=c.release.url;}
    host.append(el('div',{class:'an-two-col'},el('div',{},field('Earlier source URL',prevURL),prevText),el('div',{},field('Current source URL',nextURL),nextText)),button('Compare sourced language',()=>{
      if(!/^https?:\/\//.test(prevURL.value) || !/^https?:\/\//.test(nextURL.value) || !prevText.value.trim() || !nextText.value.trim()){toast('Add both source URLs and both texts.');return;}
      const r=A.disclosureChanges({documentType:'user-selected comparable disclosure',sourceUrl:prevURL.value,text:prevText.value},{documentType:'user-selected comparable disclosure',sourceUrl:nextURL.value,text:nextText.value});
      results.innerHTML='';for(const item of r.items)results.append(el('div',{class:'an-rule-row'},el('b',{},'“'+item.phrase+'”'),` ${item.before} → ${item.after} mentions`,el('div',{class:'an-actions'},...item.sources.map((url,i)=>link(i?'Current source':'Earlier source',url))),note(item.reason)));
      if(!r.items.length)results.append(note('No change in the tracked literal phrases. This does not establish that management’s tone or disclosure quality is unchanged.'));
    }),results,note('Literal comparisons do not establish that management dodged a question or intentionally withheld a metric. Such conclusions require an analyst to inspect the complete source.'));
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
      tool('watchlist_brief','Filings, releases and evidence-backed changes in the current watchlist.',{since:{type:'string'}},[],async({since})=>{const r=await loadMany(watchlist());return A.brief(r.companies,{since,failed:r.failed});}),
      tool('propose_standing_order','Compile a plain-language monitoring instruction into a draft. Does not activate without user review.',{instruction:{type:'string'}},['instruction'],({instruction})=>A.compileRule(instruction)),
      tool('evaluate_research_rule','Evaluate an explicit rule against a company. Missing data returns unknown.',{ticker:{type:'string'},rule:{type:'object'}},['ticker','rule'],async({ticker,rule})=>{const c=await load(ticker.toUpperCase(),true);return A.evaluateRule(rule,c,{prices:PX[c.ticker]?.bars || [],insiders:c.insiders,watchlist:watchlist()});}),
      tool('research_signal','Deterministic signal and point-in-time historical study. Returns unavailable if filing vintages are missing.',{ticker:{type:'string'},signal:{type:'string',enum:['revenue_acceleration','margin_turn']}},['ticker','signal'],async({ticker,signal})=>{const c=await load(ticker.toUpperCase());return {signal:A.signal(c,signal),study:A.backtest(c,PX[c.ticker]?.bars || [],{type:signal})};}),
      tool('compare_peers','Aligned peer metrics; identifies coverage and definition limitations.',{ticker:{type:'string'},peer:{type:'string'}},['ticker','peer'],async({ticker,peer})=>A.pair(await load(ticker.toUpperCase()),await load(peer.toUpperCase()))),
      tool('draft_cited_report','Create a factual report with exact values and source records; no unsupported investment conclusions.',{ticker:{type:'string'}},['ticker'],async({ticker})=>A.report(await load(ticker.toUpperCase())))
    ];
  });
  window.FU_RESEARCH={load,readTheses,getRules:()=>saved('rules',[])};
})();
