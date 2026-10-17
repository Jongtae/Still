"""Live P2-02 acceptance for external-model document approval boundaries."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "src"))
from personal_agent.agent_runtime import Capabilities
from personal_agent.quickstart_store import QuickStore
from personal_agent.quickstart_service import AgentService


report_path=Path(os.environ.get('AGENTOS_DOCUMENT_BOUNDARY_REPORT',str(Path(tempfile.gettempdir())/'agentos-document-boundary.json')))
source=QuickStore(Path.home()/'.local/share/agentos')
with tempfile.TemporaryDirectory() as root:
 docs=Path(root)/'documents';docs.mkdir();(docs/'boundary.txt').write_text('Boundary Project owner: Mina.')
 store=QuickStore(Path(root)/'data');service=AgentService(store)
 config=dict(source.config('model'));prior=source.config('model_test',{})
 key=source.secret('model_key')
 if os.environ.get('OPENAI_API_KEY'):
  config={'provider':'compatible','endpoint':'https://api.openai.com/v1','model':'gpt-4o-mini'};key=os.environ['OPENAI_API_KEY']
 elif config.get('model')=='openrouter/free' and prior.get('runtime_model'):config['model']=prior['runtime_model']
 service.save_model({**config,'api_key':key});service.save_roots({'paths':[str(docs)]})
 validation=service.test_model();before=service.document_boundary()
 blocked=False
 try:Capabilities(store,None,{},'','boundary',lambda *args:None,document_access=not before['requires_approval']).find_files('Boundary')
 except ValueError:blocked=True
 approved=service.set_document_approval({'approved':True})
 job_id=store.enqueue('boundary.txt에서 소유자를 찾아줘. 파일과 줄 근거를 표시해줘.','boundary-live');service.run_one()
 job=next(row for row in store.jobs() if row['id']==job_id);response=job.get('response') or ''
 live=job['status'] in ('succeeded','partial') and 'boundary.txt' in response and '줄' in response
 service.save_roots({'paths':[str(docs)]});cleared_by_roots=service.document_boundary()['requires_approval']
 service.set_document_approval({'approved':True});service.save_model({**config,'api_key':key});cleared_by_model=service.document_boundary()['requires_approval']
 report={'tested_at':time.time(),'model_validation':validation,'blocked_before_approval':blocked,'approved':approved,'live_document_answer':live,'cleared_by_roots':cleared_by_roots,'cleared_by_model':cleared_by_model,'model':job.get('model')}
 report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
 if not (validation['ok'] and blocked and approved['approved'] and live and cleared_by_roots and cleared_by_model):raise SystemExit(1)
