import test from 'node:test';
import assert from 'node:assert/strict';
await import('../../public/lib/evidence.js');
await import('../../public/lib/analyst.js');
const A = globalThis.FU_ANALYST;
const rec = (end, v, filed, extra={}) => ({start: end.slice(0,4)+'-'+String(Math.floor((Number(end.slice(5,7))-1)/3)*3+1).padStart(2,'0')+'-01', end, v, filed, available_at:filed, accn:'0000000001-26-000001', c:'Revenues', ...extra});
const company = (records, history=records) => ({ticker:'TEST',cik:1,statements:{income:[{label:'Revenue',unit:'USD',quarterly:records,annual:[],filing_history:{quarterly:history}}]},filings:[]});
test('as-of lookup cannot see later revisions and requires archived vintages',()=>{
 const old=rec('2025-03-31',100,'2025-05-01'), revised=rec('2025-03-31',120,'2026-05-01');
 assert.equal(A.series(company([revised],[old,revised]),'Revenue',{asOf:'2025-06-01'})[0].v,100);
 assert.equal(A.series(company([revised],[old,revised]),'Revenue')[0].v,120);
 const c=company([old]);delete c.statements.income[0].filing_history;
 assert.equal(A.series(c,'Revenue',{asOf:'2025-06-01',requireHistory:true}).length,0);
});
test('changes align a year and a quarter and reject nonpositive growth bases',()=>{
 const c=company([rec('2024-03-31',100,'2024-05-01'),rec('2024-12-31',-20,'2025-02-01'),rec('2025-03-31',120,'2025-05-01')]);
 const r=A.changes(c,{metrics:['Revenue']}).rows[0];
 assert.ok(Math.abs(r.yoy.v-.2)<1e-12);assert.equal(r.qoq,null);
 assert.equal(r.current.evidence.value,120);assert.ok(r.yoy.evidence.inputEvidenceIds.length===2);
});
test('standing order keeps scope, validates unsupported requests, and needs consecutive periods',()=>{
 const p=A.compileRule("Tell me if any bank's credit card losses rise 2 quarters in a row");
 assert.equal(p.rule.scope,'banks');assert.equal(p.rule.consecutive,2);assert.equal(p.rule.metric,'Credit card losses');
 assert.equal(A.compileRule('Tell me when the CEO sounds worried').status,'needs_review');
 assert.equal(A.evaluateRule({...p.rule,metric:'Revenue',scope:'watchlist'},company([rec('2024-03-31',100,'2024-05-01'),rec('2024-09-30',120,'2024-11-01'),rec('2024-12-31',130,'2025-02-01')])).status,'unknown');
 const c=company([rec('2024-03-31',100,'2024-05-01'),rec('2024-06-30',120,'2024-08-01'),rec('2024-09-30',130,'2024-11-01')]);
 assert.equal(A.evaluateRule({...p.rule,metric:'Revenue',scope:'watchlist'},c).status,'triggered');
});
test('unavailable thesis data is insufficient, never a failing score',()=>{
 const result=A.thesis({criteria:[{metric:'Data-center share',operator:'gte',threshold:.3}]},company([]));
 assert.equal(result.status,'insufficient');assert.equal(result.score,null);
});
test('backtest requires vintages, trades after publication, excludes overlapping observations',()=>{
 const c=company([rec('2025-03-31',100,'2025-05-01')]);delete c.statements.income[0].filing_history;
 assert.equal(A.backtest(c,[],{type:'revenue_acceleration'}).status,'unavailable');
 const result=A.eventStudy([{availableAt:'2025-05-01',id:'a'},{availableAt:'2025-05-02',id:'b'}],['2025-05-01','2025-05-02','2025-05-05','2025-05-06'].map((date,i)=>({date,o:100+i,c:100+i})),{horizon:2});
 assert.equal(result.trades.length,1);assert.equal(result.trades[0].entryDate,'2025-05-02');assert.equal(result.trades[0].exitDate,'2025-05-05');
});
test('legacy guidance and quarter/annual mismatches cannot inflate reliability',()=>{
 const legacy={series:[{name:'Revenue',unit:'USD',basis:'gaap',periods:[{type:'FY',end:'2025-12-31',points:[{lo:10,hi:20,mid:15,d:'2025-01-01'}],actual:{v:100,inputs:[{start:'2025-01-01',end:'2025-12-31'}]},outcome:{met:true}}]}]};
 const r=A.promises(legacy);assert.equal(r.scored,0);assert.equal(r.hitRate,null);assert.equal(r.items[0].status,'needs_review');
});
test('insider drawdown uses a purchase and prices available on transaction date',()=>{
 const prices=[{date:'2025-01-01',c:100},{date:'2025-01-02',c:78},{date:'2025-01-03',c:200}];
 const rows=[{code:'P',acq_disp:'A',derivative:false,tx_date:'2025-01-02',filed:'2025-01-03',price:78,shares:10,accession:'a'}];
 assert.equal(A.insiderContext({rows},prices,{drawdown:.2})[0].drawdown,-.22);
 assert.equal(A.insiderContext({rows:[{...rows[0],code:'A'}]},prices).length,0);
});
test('derived FCF requires matching intervals and normalizes cash capex outflow sign',()=>{
 const c={ticker:'TEST',cik:1,statements:{cashflow:[{label:'Operating cash flow',unit:'USD',quarterly:[rec('2025-09-30',100,'2025-11-01',{start:'2025-07-01'})]},{label:'Capex',unit:'USD',quarterly:[rec('2025-09-30',20,'2025-11-01',{start:'2025-01-01'})]}]}};
 assert.equal(A.series(c,'Free cash flow').length,0);
 c.statements.cashflow[1].quarterly[0]={...c.statements.cashflow[1].quarterly[0],start:'2025-07-01',v:-20};
 assert.equal(A.series(c,'Free cash flow')[0].v,80);
});
test('an event before price coverage or inside a price gap has no trade',()=>{
 const p=['2025-05-01','2025-05-02','2025-05-05'].map(date=>({date,o:100,c:101}));
 assert.equal(A.eventStudy([{availableAt:'2020-05-01'}],p,{horizon:1}).n,0);
 assert.equal(A.eventStudy([{availableAt:'2025-05-01'}],[p[0],{date:'2025-08-01',o:100,c:101},{date:'2025-08-02',o:100,c:101}],{horizon:1}).n,0);
});
test('short insider price histories return unknown and do not pretend to cover 252 sessions',()=>{
 const c=company([]),r={type:'insider_drawdown',scope:'watchlist',drawdown:.2};
 const result=A.evaluateRule(r,c,{asOf:'2025-05-03',prices:[{date:'2025-05-01',c:100},{date:'2025-05-02',c:70}],insiders:{rows:[{code:'P',tx_date:'2025-05-02',filed:'2025-05-03',shares:10,price:70}]}});
 assert.equal(result.status,'unknown');
});
test('legacy EPS subtraction and superseded derivations cannot leak into current or historical analysis',()=>{
 const c={ticker:'TEST',cik:1,statements:{income:[{label:'EPS diluted',unit:'USD/shares',quarterly:[rec('2025-03-31',1.3,'2025-05-01',{derived:{from:{v:2},minus:{v:.7}}})]}]}};
 assert.equal(A.series(c,'EPS diluted').length,0);
 const r=rec('2025-03-31',100,'2025-05-01',{superseded_at:'2025-08-01'}),v=company([r],[r]);
 assert.equal(A.series(v,'Revenue',{asOf:'2025-06-01'}).length,1);
 assert.equal(A.series(v,'Revenue',{asOf:'2025-09-01'}).length,0);
});
test('withdrawn guidance is excluded and an explicitly empty watchlist matches nobody',()=>{
 assert.equal(A.inScope({scope:'watchlist'},{ticker:'TEST'},[]),false);
 const b={series:[{name:'Revenue',unit:'USD',basis:'gaap',periods:[{type:'Q',end:'2025-12-31',points:[{d:'2025-01-01',lo:10,hi:20},{d:'2025-08-01',w:true}],actual:{v:30},comparability:{validated:true,period_match:true,basis_match:true,unit_match:true}}]}]};
 assert.equal(A.promises(b).scored,0);assert.equal(A.promises(b).items[0].status,'withdrawn');
});
test('default reports exclude future-filed numbers and fiscal transition growth',()=>{
 const c=company([rec('2025-03-31',100,'2099-05-01')]);assert.equal(A.series(c,'Revenue').length,0);
 const v=company([rec('2024-03-31',100,'2024-05-01',{start:'2024-03-01'}),rec('2025-03-31',150,'2025-05-01')]);
 assert.equal(A.changes(v,{metrics:['Revenue']}).rows[0].yoy,null);
});
test('business metrics preserve definitions, units, document locators and publication uncertainty',()=>{
 const c=company([]);A.attachBusinessMetrics(c,{groups:[{category:'Credit',items:[{id:'card_nco',name:'Net charge-offs: Card Services',unit:'USD',description:'Card portfolio only',values:{Q:{'2025-03-31':{v:10,url:'https://example.com/filing',doc:'d',page:3,quote:'Net charge-offs 10'}}}},{id:'ratio',name:'Capital ratio',unit:'%',values:{Q:{'2025-03-31':{v:12.3,url:'https://example.com/filing',doc:'d'}}}}]}]});
 assert.equal(A.series(c,'Credit card losses')[0].v,10);assert.equal(A.series(c,'Credit card losses')[0].evidence.quality.sourceLocator.page,3);
 assert.ok(Math.abs(A.series(c,'Capital ratio')[0].v-.123)<1e-12);
 assert.equal(A.series(c,'Capital ratio',{asOf:'2025-06-01'}).length,0);
 assert.equal(A.series(c,'Capital ratio',{requireHistory:true}).length,0);
});
test('reports retain complete derivation sources and use percentage points for margin changes',()=>{
 const c=company([rec('2024-03-31',100,'2024-05-01'),rec('2025-03-31',120,'2025-05-01')]);
 c.statements.income.push({label:'Operating income',unit:'USD',quarterly:[rec('2024-03-31',20,'2024-05-01'),rec('2025-03-31',30,'2025-05-01')]});
 const r=A.report(c),ids=new Set(r.evidence.map(e=>e.id));
 assert.match(r.markdown,/margin change was 5.00 percentage points/);
 for(const e of r.evidence)for(const id of e.inputEvidenceIds)assert.ok(ids.has(id),'missing exported derivation input '+id);
 assert.ok(r.evidence.some(e=>e.metric==='Revenue' && e.periodEnd==='2024-03-31'));
 const q=rec('2025-09-30',30,'2025-11-01',{accn:'new',derived:{from:rec('2025-09-30',100,'2025-11-01',{accn:'new',start:'2025-01-01'}),minus:rec('2025-06-30',70,'2025-08-01',{accn:'old',start:'2025-01-01'})}});
 const d=company([q]);d.filings=[{a:'new',p:'new.htm',d:'2025-11-01'},{a:'old',p:'old.htm',d:'2025-08-01'}];
 const ev=A.series(d,'Revenue')[0].evidence,inputs=ev.inputEvidenceIds.map(id=>globalThis.FU_EVIDENCE.get(id));
 assert.equal(ev.validation.status,'valid');assert.ok(inputs[0].sourceUrl.endsWith('/new/new.htm'));assert.ok(inputs[1].sourceUrl.endsWith('/old/old.htm'));
 assert.equal(inputs[1].filedDate,'2025-08-01');
});
