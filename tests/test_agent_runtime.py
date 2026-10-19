import json,tempfile,unittest
from pathlib import Path
from personal_agent.agent_runtime import Capabilities,run_agent
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.providers import ModelAdapter
from test_agency_loop import finish,judgments
CFG={'provider':'compatible','endpoint':'https://openrouter.ai/api/v1','model':'openrouter/free'}
class GeneralRuntimeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=QuickStore(Path(self.tmp.name)/'data');self.root=Path(self.tmp.name)/'docs';self.root.mkdir();(self.root/'launch.txt').write_text('Project Aurora launches October 12.')
  self.svc=AgentService(self.store);self.svc.save_roots({'paths':[str(self.root)]})
  self.caps=Capabilities(self.store,None,CFG,'','job',lambda *a:None)
 def tearDown(self):self.tmp.cleanup()
 def test_file_search_read_and_boundary(self):
  hits=self.caps.execute('find_files',{'query':'Aurora'})['files'];self.assertEqual(hits[0]['path'],'launch.txt')
  self.assertIn('October',self.caps.execute('read_file',{'root_id':hits[0]['root_id'],'path':hits[0]['path']})['content'])
  for path in ['../data/private/connections.json','/etc/passwd']:
   with self.assertRaises(ValueError):self.caps.read_file(hits[0]['root_id'],path)
  (self.root/'escape.txt').symlink_to(Path(self.tmp.name)/'data/private/quickstart.db')
  with self.assertRaises(ValueError):self.caps.read_file(hits[0]['root_id'],'escape.txt')

 def test_public_page_read_requires_owner_scope_and_passes_scope_to_reader(self):
  class Public:
   def __init__(self): self.calls=[]
   def execute(self,plan): self.calls.append(plan); return {'tool':'public_page_read','url':plan['url'],'content':'ok','sources':[plan['url']]}
  network=Public(); caps=Capabilities(self.store,None,CFG,'','job',lambda *a:None,network=network)
  with self.assertRaisesRegex(ValueError,'소유자가 승인한 공개 페이지가 아니어서'):
   caps.execute('public_page_read',{'url':'https://example.com/event'})
  self.assertEqual(network.calls,[])
  caps=Capabilities(self.store,None,CFG,'','job',lambda *a:None,network=network,public_page_scope=['https://example.com/event?a=1&b=2'])
  caps.execute('public_page_read',{'url':'https://example.com/event?b=2&a=1'})
  self.assertEqual(network.calls[-1]['approved_urls'],['https://example.com/event?a=1&b=2'])

 def search_network(self,results,redirect_refused=()):
  class Public:
   def __init__(self): self.calls=[]
   def execute(self,plan):
    self.calls.append(plan)
    if plan['tool']=='web_search':
     return {'tool':'web_search','query':plan['query'],'results':[{'title':url,'url':url,'snippet':''} for url in results],'sources':list(results)}
    if plan['url'] in redirect_refused:raise ValueError('승인한 공개 페이지 범위를 벗어난 주소로 이동했습니다.')
    return {'tool':'public_page_read','url':plan['url'],'content':'page text','sources':[plan['url']]}
  return Public()

 def test_public_page_read_reads_a_result_of_this_works_own_search(self):
  """#23: the retired category research tool's page reads, as a generic scope."""
  network=self.search_network(['https://shop.example/item?b=2&a=1','https://other.example/page'])
  caps=Capabilities(self.store,None,CFG,'','job',lambda *a:None,network=network)
  with self.assertRaises(ValueError):caps.execute('public_page_read',{'url':'https://shop.example/item?a=1&b=2'})
  caps.execute('web_search',{'query':'compare item prices'})
  page=caps.execute('public_page_read',{'url':'https://shop.example/item?a=1&b=2'})
  self.assertEqual(page['content'],'page text')
  # The reader gets the same set for its redirect checks: the search's results, nothing else.
  self.assertEqual(network.calls[-1]['approved_urls'],['https://other.example/page','https://shop.example/item?a=1&b=2'])
  reads=len(network.calls)
  for url in ('https://unlisted.example/','https://shop.example/item','http://localhost/admin'):
   with self.subTest(url=url),self.assertRaisesRegex(ValueError,'web_search 결과'):
    caps.execute('public_page_read',{'url':url})
  self.assertEqual(len(network.calls),reads,'a page outside the scope is never contacted')

 def test_search_result_scope_belongs_to_one_work(self):
  network=self.search_network(['https://shop.example/item'])
  Capabilities(self.store,None,CFG,'','job',lambda *a:None,network=network).execute('web_search',{'query':'item'})
  other=Capabilities(self.store,None,CFG,'','other-job',lambda *a:None,network=network)
  with self.assertRaisesRegex(ValueError,'web_search 결과'):
   other.execute('public_page_read',{'url':'https://shop.example/item'})

 def test_search_result_redirect_refusal_does_not_name_an_owner_approval(self):
  network=self.search_network(['https://shop.example/item'],redirect_refused={'https://shop.example/item'})
  caps=Capabilities(self.store,None,CFG,'','job',lambda *a:None,network=network)
  caps.execute('web_search',{'query':'item'})
  with self.assertRaisesRegex(ValueError,'소유자 승인 범위와는 무관'):
   caps.execute('public_page_read',{'url':'https://shop.example/item'})

 def test_web_search_query_passes_the_credential_tripwire_before_the_network(self):
  network=self.search_network([])
  caps=Capabilities(self.store,None,CFG,'','job',lambda *a:None,network=network)
  for query in ('api_key=sk-abcdefghijklmnop','compare https://example.com/?token=supersecret123456789'):
   with self.subTest(query=query),self.assertRaisesRegex(ValueError,'자격 증명'):
    caps.execute('web_search',{'query':query})
  self.assertEqual(network.calls,[])
  caps.execute('web_search',{'query':'api key security best practices'})
  self.assertEqual(network.calls[-1]['query'],'api key security best practices')
 def test_notes_idempotent(self):
  self.caps.execute('save_note',{'content':'hello'});self.caps.execute('save_note',{'content':'hello'})
  self.assertEqual(len(self.store.notes()),1)
 def test_tools_do_not_change_with_topic(self):
  payloads=[]
  def transport(u,b,h):payloads.append(b);return {'choices':[{'message':{'content':'일반 답변'}}]}
  for text in ['성남시 날씨','다른 것도 할 수 있어?','파일 찾아줘','에이전트에게 물어봐']:
   run_agent(ModelAdapter(transport),CFG,'',[{'role':'user','content':text}],'',self.caps,lambda *a:None)
  names=[{x['function']['name'] for x in p['tools']} for p in payloads]
  self.assertTrue(all(n==names[0] for n in names));self.assertIn('delegate_agent',names[0]);self.assertTrue(all(p['tool_choice']=='auto' for p in payloads))
 def test_delegation_is_separate_and_cannot_recurse(self):
  seen=[]
  def transport(u,b,h):seen.append(b);return {'model':'specialist-test','choices':[{'message':{'content':'검토 보고서'}}]}
  self.caps.adapter=ModelAdapter(transport)
  r=self.caps.execute('delegate_agent',{'agent_id':'reviewer','task':'이 문구를 검토: 안녕하세요'})
  self.assertEqual(r['model'],'specialist-test');self.assertIn('보고서',r['report'])
  self.assertNotIn('delegate_agent',{t['function']['name'] for t in seen[0]['tools']})
  self.assertNotIn('save_note',{t['function']['name'] for t in seen[0]['tools']})
 def test_chain_find_read_final(self):
  count=0
  def transport(u,b,h):
   nonlocal count
   count+=1
   if count==1:name,args='find_files',{'query':'Aurora'}
   elif count==2:
    hit=json.loads(b['messages'][-1]['content'])['files'][0];name,args='read_file',{'root_id':hit['root_id'],'path':hit['path']}
   else:
    self.assertIn('October 12',b['messages'][-1]['content']);return {'choices':[{'message':finish('f','2',summary='October 12')}]}
   return {'choices':[{'message':{'tool_calls':[{'id':str(count),'function':{'name':name,'arguments':json.dumps(args)}}]}}]}
  result=run_agent(ModelAdapter(transport),CFG,'',[{'role':'user','content':'Aurora 출시일을 파일에서 찾아줘'}],'',self.caps,lambda *a:None)
  self.assertIn('October 12',result.content)
  self.assertIn('파일: launch.txt · 줄 1',result.content)
 def test_weather_falls_back_to_tool_result_when_model_returns_no_final_text(self):
  calls=[0]
  def transport(u,b,h):
   calls[0]+=1
   if calls[0]==1:return {'choices':[{'message':{'tool_calls':[{'id':'weather','function':{'name':'weather','arguments':'{"city":"Seongnam-si","country":"KR"}'}}]}}]}
   return {'choices':[{'message':{'content':''}}]}
  class Weather:
   def execute(self,plan):return {'location':{'name':'Seongnam-si','admin1':'Gyeonggi-do','country':'South Korea'},'forecast':{'current':{'time':'2026-09-07T12:00','temperature_2m':24,'apparent_temperature':25,'precipitation':0,'wind_speed_10m':3},'current_units':{'temperature_2m':'°C','apparent_temperature':'°C','precipitation':'mm','wind_speed_10m':'km/h'},'timezone':'Asia/Seoul'},'sources':['https://open-meteo.example']}
  caps=Capabilities(self.store,ModelAdapter(transport),CFG,'','job',lambda *a:None,network=Weather())
  result=run_agent(caps.adapter,CFG,'',[{'role':'user','content':'성남시 날씨'}],'',caps,lambda *a:None)
  self.assertIn('Seongnam-si',result.content)
  self.assertIn('24 °C',result.content)
if __name__=='__main__':unittest.main()

class ProviderToolProtocolTests(unittest.TestCase):
 def test_ollama_roundtrip(self):
  seen=[]
  def transport(url,body,headers):
   seen.append(body)
   if len(seen)==1:return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'list_notes','arguments':{}}}]}}
   return {'message':{'role':'assistant','content':'done'}}
  adapter=ModelAdapter(transport);cfg={'provider':'ollama','endpoint':'http://localhost:11434','model':'test'}
  history=[{'role':'user','content':'notes'}]
  msg,_=adapter.tool_turn(cfg,'',history,[])
  adapter.tool_turn(cfg,'',history+[msg,{'role':'tool','tool_call_id':msg['tool_calls'][0]['id'],'content':'[]'}],[])
  self.assertEqual(seen[1]['messages'][-1]['tool_name'],'list_notes')
  self.assertEqual(seen[1]['messages'][-2]['tool_calls'][0]['function']['arguments'],{})
 def test_anthropic_parallel_results(self):
  seen=[]
  def transport(url,body,headers):
   seen.append(body)
   if len(seen)==1:return {'content':[{'type':'tool_use','id':i,'name':'list_notes','input':{}} for i in ['a','b']]}
   return {'content':[{'type':'text','text':'done'}]}
  adapter=ModelAdapter(transport);cfg={'provider':'anthropic','endpoint':'https://api.anthropic.com','model':'test'}
  history=[{'role':'system','content':'policy'},{'role':'user','content':'notes'}]
  msg,_=adapter.tool_turn(cfg,'test',history,[])
  adapter.tool_turn(cfg,'test',history+[msg]+[{'role':'tool','tool_call_id':i,'content':'[]'} for i in ['a','b']],[])
  self.assertEqual([b['tool_use_id'] for b in seen[1]['messages'][-1]['content']],['a','b'])
  self.assertEqual(seen[1]['messages'][-2]['content'][0]['type'],'tool_use')
 def test_free_router_pins_model_within_run(self):
  seen=[]
  def transport(url,body,headers):
   seen.append(body['model'])
   if len(seen)==1:return {'model':'selected/model:free','choices':[{'message':{'tool_calls':[{'id':'a','function':{'name':'list_agents','arguments':'{}'}}]}}]}
   return {'model':'selected/model:free','choices':[{'message':finish('f','a',summary='done')}]}
  with tempfile.TemporaryDirectory() as folder:
   store=QuickStore(Path(folder));adapter=ModelAdapter(transport)
   caps=Capabilities(store,adapter,CFG,'','job',lambda *a:None)
   events=[]
   run_agent(adapter,CFG,'',[{'role':'user','content':'agents'}],'',caps,lambda *a:events.append(a))
  self.assertEqual(seen,['openrouter/free','selected/model:free'])
  # #598: each responded event names the model that call was sent with,
  # apart from the one the provider reported.
  responded=[json.loads(e[2]) for e in events if e[0]=='model' and e[1]=='responded']
  self.assertEqual([(r['requested_model'],r['model']) for r in responded],
                   [('openrouter/free','selected/model:free'),('selected/model:free','selected/model:free')])
 def test_rate_limit_reroute_preserves_completed_tool(self):
  from personal_agent.providers import ProviderError
  seen=[]
  def transport(url,body,headers):
   seen.append(body)
   if len(seen)==1:return {'model':'selected/model:free','choices':[{'message':{'tool_calls':[{'id':'a','function':{'name':'save_note','arguments':'{"content":"once"}'}}]}}]}
   if len(seen)==2:raise ProviderError('rate limited',status=429)
   self.assertEqual(body['model'],'openrouter/free')
   self.assertEqual(body['messages'][-1]['role'],'tool')
   return {'model':'other/model:free','choices':[{'message':finish('f','a',summary='saved')}]}
  with tempfile.TemporaryDirectory() as folder:
   store=QuickStore(Path(folder));adapter=ModelAdapter(transport)
   caps=Capabilities(store,adapter,CFG,'','job',lambda *a:None)
   run_agent(adapter,CFG,'',[{'role':'user','content':'save once'}],'',caps,lambda *a:None)
   self.assertEqual(len(store.notes()),1)
 def test_corrected_arguments_keep_failure_log_but_complete(self):
  count=0;events=[]
  def transport(url,body,headers):
   nonlocal count
   count+=1
   if count<3:return {'choices':[{'message':{'tool_calls':[{'id':str(count),'function':{'name':'list_agents','arguments':'{"bad":"key"}' if count==1 else '{}'}}]}}]}
   return {'choices':[{'message':finish('f','2',summary='done')}]}
  with tempfile.TemporaryDirectory() as folder:
   store=QuickStore(Path(folder));adapter=ModelAdapter(transport)
   caps=Capabilities(store,adapter,CFG,'','job',lambda *a:None,judgments=judgments(True))
   result=run_agent(adapter,CFG,'',[{'role':'user','content':'list agents'}],'',caps,lambda *a:events.append(a))
   self.assertEqual(result.outcome,'succeeded')
   self.assertIn(('list_agents','failed'),[(e[0],e[1]) for e in events])
 def test_failed_capability_is_not_repeated_for_the_same_arguments(self):
  from personal_agent.providers import ProviderError
  calls=[];events=[];turn=[0]
  def transport(url,body,headers):
   turn[0]+=1
   if turn[0]<3:return {'choices':[{'message':{'tool_calls':[{'id':str(turn[0]),'function':{'name':'web_search','arguments':'{"query":"same query"}'}}]}}]}
   return {'choices':[{'message':{'content':'search failed'}}]}
  class BrokenNetwork:
   def execute(self,plan):calls.append(plan);raise ProviderError('offline')
  with tempfile.TemporaryDirectory() as folder:
   store=QuickStore(Path(folder));adapter=ModelAdapter(transport)
   caps=Capabilities(store,adapter,CFG,'','job',lambda *a:None,network=BrokenNetwork())
   result=run_agent(adapter,CFG,'',[{'role':'user','content':'search'}],'',caps,lambda *a:events.append(a))
  self.assertEqual(result.outcome,'failed')
  # #607: one bounded transient retry inside the first call; the identical
  # second call is still refused before the network (#657: as a repeated path).
  self.assertEqual(len(calls),2)
  failed=[json.loads(e[2]) for e in events if e[0]=='web_search' and e[1]=='failed' and 'attempt' in json.loads(e[2])]
  self.assertEqual([e['attempt'] for e in failed],[1,2])
  self.assertEqual(failed[-1]['code'],'repeat_path')
 def test_trace_evidence_keeps_file_content_out_of_event_store(self):
  events=[];count=[0]
  def transport(url,body,headers):
   count[0]+=1
   if count[0]==1:return {'choices':[{'message':{'tool_calls':[{'id':'read','function':{'name':'read_file','arguments':'{"root_id":"root","path":"launch.txt"}'}}]}}]}
   return {'choices':[{'message':{'content':'done'}}]}
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder).resolve()/'docs';root.mkdir();(root/'launch.txt').write_text('secret Aurora release detail')
   store=QuickStore(Path(folder)/'data');store.put('file_roots',[{'id':'root','path':str(root)}])
   caps=Capabilities(store,ModelAdapter(transport),CFG,'','job',lambda *a:None)
   run_agent(caps.adapter,CFG,'',[{'role':'user','content':'read'}],'',caps,lambda *a:events.append(a))
  trace=json.loads([e[2] for e in events if e[0]=='read_file' and e[1]=='succeeded'][0])
  self.assertEqual(trace['attempt'],1)
  self.assertEqual(trace['evidence']['path'],'launch.txt')
  self.assertNotIn('secret Aurora release detail',json.dumps(trace))


class TimeSensitiveRecheckTests(unittest.TestCase):
 """QUALITY-FRESH-01 (#24): time-sensitive claims after conflicting owner feedback.

 Evidence class: worker guidance text plus model-free scripted runs with an
 injected model transport and a fake public network.  The fixtures prove that
 the guidance reaches the model and that the evidence it needs (a source's own
 date, the retrieval time, the owner's dated report, a failed recheck) reaches
 it as distinct values; they do not prove how a live model answers.
 """
 LISTING='https://listing.example/place'
 NOTICE='https://notice.example/place'
 PAGES={LISTING:'Hours: 09:00-18:00 daily. Last updated 2024-03-02.',
        NOTICE:'Notice dated 2026-10-01: closed until further notice.'}
 RETRIEVED=1792400000.0
 HISTORY=[{'role':'user','content':'Is the place open today?'},
          {'role':'assistant','content':'Yes, 09:00-18:00 (listing.example).'},
          {'role':'user','content':'It closed last month.'}]

 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.store=QuickStore(Path(self.tmp.name)/'data');self.events=[]

 def network(self,offline=False):
  pages,retrieved=self.PAGES,self.RETRIEVED
  class Public:
   def __init__(self):self.calls=[]
   def execute(self,plan):
    from personal_agent.providers import ProviderError
    self.calls.append(plan)
    if offline:raise ProviderError('offline')
    if plan['tool']=='web_search':
     return {'tool':'web_search','query':plan['query'],'results':[{'title':url,'url':url,'snippet':''} for url in pages],
             'sources':list(pages),'retrieved_at':retrieved}
    return {'tool':'public_page_read','url':plan['url'],'retrieved_at':retrieved,'content':pages[plan['url']],'sources':[plan['url']]}
  return Public()

 def run_script(self,*messages,offline=False):
  from test_agency_loop import Script
  script=Script(*messages);network=self.network(offline)
  caps=Capabilities(self.store,ModelAdapter(script),CFG,'','job',lambda *a:self.events.append(a),network=network)
  run_agent(caps.adapter,CFG,'',list(self.HISTORY),'',caps,lambda *a:self.events.append(a))
  observed={}
  for message in script.bodies[-1]['messages']:
   if message['role']=='tool':
    observed[message.get('tool_call_id')]=json.loads(message['content'])
  return script,network,observed

 @staticmethod
 def step(ident,name,**args):
  from test_agency_loop import call
  return {'tool_calls':[call(ident,name,**args)]}

 def test_the_worker_guidance_keeps_the_three_times_apart_and_rechecks_once(self):
  from personal_agent.agent_runtime import CORE_INSTRUCTIONS
  for phrase in ('Keep three times apart: when the source says the fact took effect or was last updated, when you retrieved it, and when the owner reported something',
                 'A page fetched just now can still describe an old state',
                 'otherwise say it is unconfirmed and how old the support is',
                 'do not repeat it and do not simply take either side. Recheck it once with a lookup aimed at that conflict',
                 'keep the conflict visible','do not guess whether a change is temporary or permanent',
                 'If the recheck cannot be done, say the fact is unconfirmed rather than repeating it',
                 'is dated evidence, not a permanent truth, and neither is a listing: newer dated evidence can revise either',
                 "Saving the owner's correction does not by itself settle what is current"):
   self.assertIn(phrase,CORE_INSTRUCTIONS)
  script,_,_=self.run_script({'content':'ok'})
  self.assertIn('Keep three times apart',script.bodies[0]['messages'][0]['content'],'the guidance reaches the worker model')

 def test_stale_listing_dated_correction_and_newer_source_reach_the_model_as_dated_evidence(self):
  from test_agency_loop import finish_observed
  job=self.store.enqueue('The place closed last month.','k1',owner_typed=True)
  report=self.store.save_memory('place.status','closed',owner_id='local-owner',work_id=job)
  _,network,observed=self.run_script(
   self.step('m','search_memory',query='place'),self.step('s','web_search',query='place notice'),
   self.step('l','public_page_read',url=self.LISTING),self.step('n','public_page_read',url=self.NOTICE),
   finish_observed('f',summary='The notice dated 2026-10-01 says closed; the listing was last updated 2024-03-02.'))
  # The owner's dated report: their own words, when it was saved and that the owner typed it.
  row=observed['m']['memories'][0]
  self.assertEqual((row['id'],row['content'],row['source']['kind'],row['source_status']),(report['id'],'closed','owner_request','found'))
  self.assertTrue(row['saved_at'] and row['source']['at'])
  self.assertIn('closed last month',row['source']['text'])
  # The stale listing and the newer source: the page's own date and the retrieval time stay separate values.
  for ref,dated in (('l','Last updated 2024-03-02'),('n','Notice dated 2026-10-01')):
   self.assertIn(dated,observed[ref]['content'])
   self.assertEqual(observed[ref]['retrieved_at'],self.RETRIEVED)
  # One bounded recheck aimed at the conflict: one search and the two pages it returned.
  self.assertEqual([call['tool'] for call in network.calls],['web_search','public_page_read','public_page_read'])

 def test_a_stored_observation_is_revised_by_later_evidence_whoever_gave_it(self):
  job=self.store.enqueue('The place closed last month.','k1',owner_typed=True)
  report=self.store.save_memory('place.status','closed (owner report)',owner_id='local-owner',work_id=job)
  caps=Capabilities(self.store,None,CFG,'','job',lambda *a:None,allowed_tools=['save_memory','list_memory'])
  revised=caps.execute('save_memory',{'memory_key':'place.status','content':'reopened per notice dated 2026-10-15'})
  self.assertTrue(revised['saved']);self.assertEqual(revised['supersedes'],report['id'])
  current=[row for row in caps.execute('list_memory',{})['memories'] if row['memory_key']=='place.status']
  self.assertEqual([row['content'] for row in current],['reopened per notice dated 2026-10-15'])
  self.assertTrue(current[0]['saved_at'],'the revision keeps its own time')
  with self.store.db() as db:
   earlier=db.execute('SELECT content,state FROM memories WHERE id=?',(report['id'],)).fetchone()
  self.assertEqual((earlier['content'],earlier['state']),('closed (owner report)','superseded'),
                   'the earlier report is kept as superseded, not erased or marked never-true')

 def test_unavailable_verification_reaches_the_model_as_a_failure_not_a_check(self):
  _,network,observed=self.run_script(
   self.step('s','web_search',query='place notice'),{'content':'I could not recheck it, so whether it is open is unconfirmed.'},offline=True)
  self.assertTrue(network.calls,'the recheck was attempted')
  self.assertIn('error',observed['s']);self.assertEqual(observed['s']['effect'],'none')
  self.assertNotIn(('web_search','succeeded'),[event[:2] for event in self.events],'no lookup is recorded as checked')
