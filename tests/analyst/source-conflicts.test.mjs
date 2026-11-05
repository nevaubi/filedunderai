import test from 'node:test';
import assert from 'node:assert/strict';
await import('../../public/lib/analyst.js');
const A = globalThis.FU_ANALYST;
const reported = (v = 100, extra = {}) => ({v, start:'2026-04-01', end:'2026-06-30', filed:'2026-08-01', available_at:'2026-08-01', accn:'reported', c:'Revenues', ...extra});
const derived = (v = 110, extra = {}) => reported(v, {filed:'2026-11-01', available_at:'2026-11-01', accn:'comparative', derived:{from:{v:210,start:'2026-01-01',end:'2026-06-30',filed:'2026-11-01'},minus:{v:100,start:'2026-01-01',end:'2026-03-31',filed:'2026-05-01'}}, ...extra});
const company = records => ({ticker:'TEST',cik:1,statements:{income:[{label:'Revenue',unit:'USD',concept:'Revenues',quarterly:records,annual:[],filing_history:{quarterly:records}}]}});
const values = (records, asOf='2026-11-05') => A.series(company(records),'Revenue',{asOf}).map(p=>p.v);

test('a later conflicting derived quarter is withheld after its disclosure',()=>{
 const history=[reported(),derived()];
 assert.deepEqual(values(history,'2026-09-01'),[100]);
 assert.deepEqual(values(history),[]);
});
test('same-date reported and derived disagreement is withheld regardless of input order',()=>{
 const direct=reported(100), calculated=derived(110,{filed:direct.filed,available_at:direct.available_at});
 calculated.derived.from.filed=direct.filed;
 assert.deepEqual(values([direct,calculated]),[]);
 assert.deepEqual(values([calculated,direct]),[]);
});
test('equal later derivation keeps the latest exact reported source',()=>{
 const old=reported(90,{filed:'2026-07-31',available_at:'2026-07-31'}),current=reported(100);
 const output=A.series(company([old,current,derived(100)]),'Revenue',{asOf:'2026-11-05'});
 assert.equal(output[0].v,100);
 assert.equal(output[0].rec.accn,'reported');
 assert.equal(output[0].derived,undefined);
});
test('same-date direct-value or interval ambiguity is withheld',()=>{
 assert.deepEqual(values([reported(100),reported(101)]),[]);
 assert.deepEqual(values([reported(100),reported(100,{start:'2026-04-02'}),derived(100)]),[]);
});
test('same-scope later direct revision remains available at the correct date',()=>{
 const revised=reported(120,{filed:'2026-10-01',available_at:'2026-10-01',accn:'revision'});
 assert.deepEqual(values([reported(),revised],'2026-09-01'),[100]);
 assert.deepEqual(values([reported(),revised]),[120]);
});
test('different concepts are not assumed to prove equivalent reported amounts',()=>{
 assert.deepEqual(values([reported(100,{c:'SalesRevenueNet'}),derived(110)]),[110]);
});
test('explicit conflict metadata cannot contaminate an earlier as-of view',()=>{
 const point=reported(100,{selection_reason:'Later source conflict',selection_conflict:{reason:'later_derived_value_differs_from_reported',alternative:derived(110)}});
 const original=structuredClone(point);
 const earlier=A.series(company([point]),'Revenue',{asOf:'2026-09-01'});
 assert.equal(earlier[0].v,100);
 assert.equal(earlier[0].rec.selection_conflict,undefined);
 assert.deepEqual(values([point]),[]);
 assert.deepEqual(point,original);
});
test('statement observations ending after their filing are unavailable',()=>{
 assert.deepEqual(values([reported(100,{filed:'2026-05-01',available_at:'2026-05-01'})]),[]);
});
test('a derivation cannot use an input ending after that input was filed',()=>{
 const point=derived(); point.derived.from.filed='2026-05-01';
 assert.deepEqual(values([point]),[]);
});
test('an earlier view cannot use a derivation leg filed after its declared availability',()=>{
 const point=derived(110,{filed:'2026-08-01',available_at:'2026-08-01'});
 assert.deepEqual(values([point],'2026-09-01'),[]);
});
test('absent and null starts both describe the same instantaneous balance scope',()=>{
 const first=reported(100,{start:null}),second=reported(100); delete second.start;
 const c={ticker:'TEST',statements:{balance:[{label:'Total assets',concept:'Assets',unit:'USD',quarterly:[first,second],filing_history:{quarterly:[first,second]}}]}};
 assert.equal(A.series(c,'Total assets',{asOf:'2026-11-05'})[0].v,100);
});
const subtraction=(big,small,value=big-small)=>derived(value,{derived:{from:{v:big,start:'2026-01-01',end:'2026-06-30',filed:'2026-11-01'},minus:{v:small,start:'2026-01-01',end:'2026-03-31',filed:'2026-05-01'}}});
test('exact decimal agreement preserves reported value despite binary subtraction residue',()=>{
 const direct=reported(199.9),calculated=subtraction(300.1,100.2);
 assert.notEqual(calculated.v,direct.v);
 for(const records of [[direct,calculated],[calculated,direct]]){
  const result=A.series(company(records),'Revenue',{asOf:'2026-11-05'});
  assert.equal(result[0].v,199.9);
  assert.equal(result[0].rec.accn,'reported');
 }
});
test('exact decimal comparison supports exponent-form source amounts',()=>{
 const direct=reported(1.999e-8),calculated=subtraction(3.001e-8,1.002e-8);
 assert.deepEqual(values([direct,calculated]),[direct.v]);
});
test('decimal equivalence never forgives a real difference or tampered derivation',()=>{
 assert.deepEqual(values([reported(200.9),subtraction(300.1,100.2)]),[]);
 assert.deepEqual(values([reported(199.9),subtraction(300.1,100.2,199.90000000000006)]),[]);
});
test('decimal equivalence refuses unsafe integral source numbers',()=>{
 assert.deepEqual(values([reported(9007199254740990),subtraction(9007199254740992,1)]),[]);
});
