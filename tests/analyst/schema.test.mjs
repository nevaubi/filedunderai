import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';
const a='11111111-1111-4111-8111-111111111111',b='22222222-2222-4222-8222-222222222222';
test('monitoring migration enforces owner isolation, column permissions and service-only leases',async()=>{
 const db=new PGlite();
 try{
  await db.exec(`create role anon; create role authenticated; create role service_role bypassrls; create schema auth; create table auth.users(id uuid primary key); create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$; grant usage on schema auth,public to anon,authenticated,service_role; grant execute on function auth.uid() to authenticated; insert into auth.users values('${a}'),('${b}');`);
  await db.exec(await readFile(new URL('../../supabase/migrations/20261104200000_research_monitoring_and_leases.sql',import.meta.url),'utf8'));
  const privileges=await db.query(`select has_table_privilege('anon','public.research_settings','SELECT') anon_read, has_table_privilege('authenticated','public.research_outbox','SELECT') outbox_read, has_function_privilege('authenticated','public.acquire_etl_lease(text,text,integer)','EXECUTE') acquire, has_function_privilege('anon','public.due_research_settings(integer)','EXECUTE') due`);
  assert.deepEqual(privileges.rows[0],{anon_read:false,outbox_read:false,acquire:false,due:false});
  await db.exec(`insert into public.research_settings(user_id,watchlist) values('${b}','["MSFT"]');set role authenticated;select set_config('request.jwt.claim.sub','${a}',false);insert into public.research_settings(user_id,watchlist) values('${a}','["AAPL"]');`);
  assert.deepEqual((await db.query('select user_id from public.research_settings')).rows,[{user_id:a}]);
  await assert.rejects(db.exec(`insert into public.research_settings(user_id) values('${b}')`),/row-level security/);
  await db.exec(`update public.research_settings set morning_enabled=true where user_id='${a}'`);
  assert.equal((await db.query('select revision from public.research_settings')).rows[0].revision,2);
  await assert.rejects(db.exec('update public.research_settings set revision=999'),/permission denied/);
  assert.equal((await db.query(`update public.research_settings set morning_enabled=false where user_id='${b}' returning user_id`)).rows.length,0);
  await db.exec(`reset role; insert into public.research_alerts(user_id,event_key,kind,title) values('${a}','filing:x','filing','Filing'); set role authenticated;`);
  await db.exec('update public.research_alerts set read_at=now()');
  await assert.rejects(db.exec("update public.research_alerts set title='Altered'"),/permission denied/);
  await db.exec('reset role;set role service_role;');
  const acquire=async(owner)=> (await db.query("select public.acquire_etl_lease('test',$1,300) as ok",[owner])).rows[0].ok;
  assert.equal(await acquire('one'),true);assert.equal(await acquire('two'),false);assert.equal(await acquire('one'),true);
  assert.equal((await db.query("select public.release_etl_lease('test','two') as ok")).rows[0].ok,false);
  assert.equal((await db.query("select public.release_etl_lease('test','one') as ok")).rows[0].ok,true);
  assert.equal(await acquire('two'),true);
  assert.equal((await db.query('select * from public.due_research_settings(50)')).rows.length,2);
 }finally{await db.close();}
});
