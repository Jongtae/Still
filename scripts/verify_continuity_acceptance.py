"""Exercise restart and delivery safety with an isolated AgentOS store.

This is deterministic transport coverage. The separate live Telegram acceptance
is deliberately left to a paired bot because a fake transport cannot prove a
real account can receive messages.
"""
import json
import tempfile
import time
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "src"))

from personal_agent.providers import ModelAdapter
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore


def transport(url, body, headers=None, timeout=60):
    if url.endswith('/getMe'): return {'ok': True, 'result': {'username': 'acceptance_bot'}}
    if url.endswith('/getWebhookInfo'): return {'ok': True, 'result': {'url': ''}}
    if url.endswith('/sendMessage'): return {'ok': True, 'result': {'message_id': 1}}
    if url.endswith('/api/chat'):
        if body.get('tools'): return {'message': {'content': '', 'tool_calls': [{'function': {'name': 'agentos_connection_probe', 'arguments': '{}'}}]}}
        return {'message': {'content': 'continuity response'}}
    raise AssertionError(url)


with tempfile.TemporaryDirectory() as root:
    store=QuickStore(Path(root)/'data')
    service=AgentService(store, ModelAdapter(transport), transport)
    service.save_model({'provider':'ollama','endpoint':'http://127.0.0.1:11434','model':'acceptance'})
    service.test_model()
    link=service.connect_telegram({'token':'123456:ACCEPTANCE_TOKEN'})['url']
    code=parse_qs(urlsplit(link).query)['start'][0]
    generation=store.config('telegram')['generation']
    service.ingest_update({'update_id':1,'message':{'from':{'id':42},'chat':{'id':42,'type':'private'},'text':'/start '+code}},generation)
    service.run_one(); service.deliver_one()
    service.ingest_update({'update_id':2,'message':{'from':{'id':42},'chat':{'id':42,'type':'private'},'text':'/note shared conversation'}},generation)
    service.run_one(); service.deliver_one()
    same_history=any('shared conversation' in row['content'] for row in store.history())
    job=store.enqueue('interrupted work','restart-proof')
    with store.db() as db: db.execute("UPDATE jobs SET status='running', delivery='sending' WHERE id=?",(job,))
    restarted=QuickStore(Path(root)/'data')
    restarted.recover()
    recovered=restarted.jobs()[0]
    report={'tested_at':time.time(),'same_history':same_history,'restart_status':recovered['status'],'restart_delivery':recovered['delivery'],'telegram_user_id':restarted.config('telegram')['user_id']}
    print(json.dumps(report,ensure_ascii=False))
    if not (same_history and recovered['status']=='interrupted' and recovered['delivery']=='unknown' and report['telegram_user_id']==42): raise SystemExit(1)
