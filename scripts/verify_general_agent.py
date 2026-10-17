"""Live multi-topic acceptance, isolated from personal conversation and files."""
import json,os,tempfile,time,sys
from pathlib import Path
if '--installed' in sys.argv:
 root=Path('/opt/homebrew/opt/agentos/libexec')
 sites=sorted(root.glob('lib/python*/site-packages'))
 sys.path.insert(0,str(sites[0] if sites else root))
else:sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'src'))
from personal_agent.quickstart_store import QuickStore
from personal_agent.quickstart_service import AgentService
source=QuickStore(Path.home()/'.local/share/agentos')
report_path=Path(os.environ.get('AGENTOS_ACCEPTANCE_REPORT',str(Path(tempfile.gettempdir())/'agentos-general-agent-acceptance.json')))
results=[]
with tempfile.TemporaryDirectory() as root:
 docs=Path(root)/'documents';docs.mkdir();(docs/'aurora-launch.txt').write_text('Project Aurora launch date: 2031-10-12. Owner: Mina. Budget: 4200 USD.')
 store=QuickStore(Path(root)/'data');svc=AgentService(store)
 svc.save_roots({'paths':[str(docs)]})
 config=dict(source.config('model'))
 checked_source=source.config('model_test',{})
 if os.environ.get('OPENAI_API_KEY'):
  config={'provider':'compatible','endpoint':'https://api.openai.com/v1','model':'gpt-4o-mini'}
 if (config.get('provider')=='compatible' and config.get('endpoint')=='https://openrouter.ai/api/v1'
     and config.get('model')=='openrouter/free' and checked_source.get('runtime_model')):
  # Re-run acceptance against the exact free model that passed the user's
  # native-tool probe. The pool alias may otherwise select a text-only model.
  config['model']=checked_source['runtime_model']
 if '--model' in sys.argv:config['model']=sys.argv[sys.argv.index('--model')+1]
 svc.save_model({**config,'api_key':os.environ.get('OPENAI_API_KEY',source.secret('model_key'))})
 if svc.document_boundary()['requires_approval']:
  svc.set_document_approval({'approved':True})
 checked=svc.test_model()
 if not checked['ok']:
  print('model-tool-validation FAIL',checked.get('error',''),flush=True)
  report_path.write_text(json.dumps({'installed':'--installed' in sys.argv,'tested_at':time.time(),'configured_model':config.get('model'),'model_validation':checked,'results':[]},ensure_ascii=False,indent=2))
  raise SystemExit(1)
 cases=[('search','Kubernetes 공식 문서를 웹에서 검색해서 링크를 알려줘',['web_search'],None),('files','이번에는 내 파일에서 Aurora 출시 날짜를 찾아줘. 파일 내용으로 확인해줘.',['find_files','read_file'],'2031'),('delegate','방금 읽은 Aurora 출시 내용을 검토 에이전트에게 전달해서 검토를 받아줘.',['delegate_agent'],None),('general','이제 다른 주제야. 도구를 쓰지 말고 안녕이라고만 답해줘.',[], '안녕'),('memory','Aurora 출시 검토가 필요하다는 내용을 내 메모에 저장해줘.',['save_note'],None)]
 for label,prompt,expected,substring in cases:
  # Free and small models can occasionally stop after a successful tool call.
  # Retry this isolated, read-only acceptance case once; never replay owner work.
  for attempt in (1,2):
   job=store.enqueue(prompt,f'acceptance-{label}-{attempt}');svc.run_one()
   row=store.jobs()[0]
   with store.db() as db:events=[dict(r) for r in db.execute('SELECT * FROM tool_events WHERE job_id=? ORDER BY id',(job,))]
   actual={e['tool'] for e in events if e['status']=='succeeded'}
   passed=row['status'] in ('succeeded','partial') and all(x in actual for x in expected) and (bool(substring in (row.get('response') or '')) if substring else True)
   if label=='general':passed=passed and not any(e['status']=='running' for e in events)
   if passed or attempt==2:break
  results.append({'case':label,'passed':passed,'attempts':attempt,'status':row['status'],'response':row.get('response'),'error':row.get('error'),'model':row['model'],'events':events})
  print(label,'PASS' if passed else 'FAIL',sorted(actual),row['model'],flush=True)
report_path.write_text(json.dumps({'installed':'--installed' in sys.argv,'tested_at':time.time(),'configured_model':config['model'],'results':results},ensure_ascii=False,indent=2))
if not all(r['passed'] for r in results):raise SystemExit(1)
