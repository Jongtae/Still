#!/usr/bin/env python3
"""Rehearse the generic connection path end to end before the owner (HARDEN-GENERIC-01 #1445).

Runs the real Judgment AI and the owner's real workers on a scratch AgentOS
store against synthetic vendors (``vendor.py``). The driver plays the owner
only where an owner acts: confirming a proposed secret set and entering values
on the one-time page. Everything else is the product's own path:

    request -> route choice -> secret-set proposal -> confirmation -> secure input
    -> same-Work continuation -> sandboxed code runs -> answer

#1489 (PROC-MEM-01): each domain gets two requests on the same connection. The
first discovers the API and should save a function library; the second asks for a
different value and should import that library instead of re-reading the docs. The
report gives each request's seconds, tool calls and documentation fetches.

Usage: PYTHONPATH=src python3 scripts/rehearsal/run.py [--domains brokerage,parcel]
       [--owner-data ~/.local/share/agentos]

It reads the owner's AI route (``family_setup.share_ai_route``) and nothing else
from the owner's store, and uses the owner's subscription for a few model turns.
Exit status 0 only when every check passes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vendor import ACCOUNTS, APP_KEY, APP_SECRET, BALANCES, PARCEL_KEY, ParcelVendor, Vendor  # noqa: E402

FINISHED = ('succeeded', 'partial', 'failed', 'cancelled')
MAX_ROUNDS = 6


def digits(text):
    return re.sub(r'[^0-9]', '', text)


def brokerage_value(field):
    """What the owner types for one proposed field, read from its name and label as a person would."""
    text = f"{field['name']} {field.get('label', '')}".upper()
    if 'SECRET' in text or '시크릿' in text:
        return APP_SECRET
    if 'KEY' in text or '키' in text:
        return APP_KEY
    if 'PRODUCT' in text or '상품' in text:
        return ACCOUNTS[0]['product_code']
    if 'ACCOUNT' in text or '계좌' in text:
        return ACCOUNTS[0]['account_no']
    return None


def parcel_value(field):
    text = f"{field['name']} {field.get('label', '')}".upper()
    return PARCEL_KEY if 'KEY' in text or '키' in text else None


#: name -> (vendor class, owner input, [(request, what only a right answer carries)], secrets that must never leak)
DOMAINS = {
    'brokerage': (Vendor, brokerage_value, [
        ('리허설증권에 있는 내 계좌 목록과 각 계좌 잔고를 보여줘. 앱키와 시크릿은 갖고 있어. 개발자 문서: {base}/docs',
         lambda answer: all(str(value['cash']) in digits(answer) for value in BALANCES.values())),
        ('리허설증권 연금 계좌(50987654)의 현금 잔고만 다시 알려줘.',
         lambda answer: str(BALANCES[('50987654', '22')]['cash']) in digits(answer)),
    ], (APP_KEY, APP_SECRET)),
    'parcel': (ParcelVendor, parcel_value, [
        ('리허설택배 송장 6100-2233-4455 배송 상태 알려줘. API 키는 갖고 있어. 개발자 문서: {base}/docs',
         lambda answer: 'RH-7731' in answer),
        ('리허설택배 송장 6100-9988-7766 은 지금 어디쯤이야?', lambda answer: '0942' in answer),
    ], (PARCEL_KEY,)),
}


def qualify_judgment(service, store, deadline):
    """Earn the Judgment AI's qualification for this scratch store once (it is bound to the store path)."""
    routes = service.decision_routes
    main = (store.config('subscription_engine', {}) or {}).get('id')
    if not main:
        return 'no subscription engine'
    marker = Path(store.root).parent / 'judgment-qualified'
    if marker.exists() and marker.read_text().strip() == main:
        return 'already qualified'
    job = routes.queue_qualification(main, explicit=True)
    while time.time() < deadline:
        routes.run_due_qualification()
        row = routes._jobs().get(job['option']) or {}
        if row.get('state') not in ('queued', 'running', 'cancelling'):
            if row.get('state') == 'passed':
                marker.write_text(main)
            return row.get('state') or 'unknown'
        time.sleep(3)
    return 'timed out'


def run_until_settled(service, store, job_id, deadline):
    while time.time() < deadline:
        job = store.job(job_id)
        if job['status'] in FINISHED or job['status'] == 'awaiting_connection':
            return job
        service.run_one()
    return store.job(job_id)


def saved_libraries(store):
    """``{set: version}`` of the current function libraries in the scratch store (#1486)."""
    with store.db() as db:
        try:
            return {row['slot']: row['version'] for row in
                    db.execute("SELECT slot,version FROM procedure_versions WHERE state='current'")}
        except Exception:
            return {}


def run_request(service, store, vendor, text, value_of, deadline):
    """One owner request, played to its end; what it took and what it answered."""
    from personal_agent import api_input
    started, docs_before, tokens_before = time.time(), vendor.stats()['docs_fetches'], vendor.stats().get('tokens_issued', 0)
    job_id = store.enqueue(text.format(base=vendor.base), f'rehearsal-{int(time.time() * 1000)}', owner_typed=True)
    timeline, confirmed = [], set()
    for _round in range(MAX_ROUNDS):
        job = run_until_settled(service, store, job_id, deadline)
        drafts = [row for row in service.settings_orchestrator._drafts().values()
                  if isinstance(row, dict) and row.get('category') == 'api_connections'
                  and row.get('state') == 'awaiting-confirmation' and row['id'] not in confirmed]
        timeline.append({'status': job['status'], 'drafts': len(drafts)})
        if not drafts:
            break
        draft = drafts[-1]
        confirmed.add(draft['id'])
        owner_job = store.job(draft['work_id'])
        result = service.settings_orchestrator.confirm(service.settings_owner(owner_job), owner_job['channel'],
                                                       draft['id'], draft['digest'])
        timeline.append({'confirmed': result.get('state')})
        session = service.phone_input_session()
        data = json.loads(draft['after'])
        values = {field['name']: value_of(field) for field in data.get('fields') or []}
        if session is None or None in values.values():
            timeline.append({'input': 'not possible', 'fields': [field['name'] for field in data.get('fields') or []]})
            break
        timeline.append({'input': api_input.submit(service, session, values).get('ok')})
        service.resume_settings_work()
    job = store.job(job_id)
    events = store.task_events(job_id)
    tools = [(event['tool'], event['status'], (event.get('trace') or {}).get('code')) for event in events
             if event['status'] in ('succeeded', 'failed') and event['tool'] not in ('orchestrator', 'response_timing')]
    library = [((event.get('trace') or {}).get('evidence') or {}).get('library') for event in events
               if event['tool'] == 'code_run' and event['status'] == 'succeeded']
    return {'job': job_id, 'status': job['status'], 'seconds': round(time.time() - started),
            'tool_calls': len(tools), 'docs_fetches': vendor.stats()['docs_fetches'] - docs_before,
            'tokens_issued': vendor.stats().get('tokens_issued', 0) - tokens_before,
            'code_runs': sum(1 for tool, status, _code in tools if tool == 'code_run' and status == 'succeeded'),
            'library': [item for item in library if item], 'saved_after': saved_libraries(store),
            'answer': job.get('response') or '', 'timeline': timeline, 'tools': tools, 'events': events}


def rehearse_domain(name, service, store, deadline):
    from personal_agent.family_setup import start_tunnel
    vendor_class, value_of, requests, secrets = DOMAINS[name]
    vendor = vendor_class().start()
    # A public HTTPS address, as a real vendor has: the product needs no rehearsal exception.
    tunnel, public = start_tunnel(vendor.port)
    try:
        vendor.base, vendor.host = public.rstrip('/'), public.split('://', 1)[1].rstrip('/')
        runs = [run_request(service, store, vendor, text, value_of, deadline) for text, _right in requests]
        first, second = runs
        states = [state.get('state') for item in second['library'] for state in item.values() if isinstance(state, dict)]
        leaked = [secret for secret in secrets for run in runs if secret in json.dumps(run['events']) or secret in run['answer']]
        checks = {
            'first: work succeeded': first['status'] == 'succeeded',
            'first: right answer': requests[0][1](first['answer']),
            'first: code_run used': first['code_runs'] > 0,
            'first: a function library was saved': bool(first['saved_after']),
            'second: work succeeded': second['status'] == 'succeeded',
            'second: right answer for its own value': requests[1][1](second['answer']),
            'second: imported the saved library': any(state in ('used', 'saved_if_fulfilled') for state in states),
            'second: no documentation fetched': second['docs_fetches'] == 0,
            'second: fewer tool calls than the first': second['tool_calls'] < first['tool_calls'],
            'no key or secret in records or answers': not leaked,
            # A request to send a credential in chat, not a warning against it ("보내지 마세요").
            'no request for credentials in chat': not any(re.search(
                r'(키|시크릿|토큰|비밀번호)[^.\n]{0,20}(보내|붙여 ?넣어|입력해) ?주(세요|시겠어요|실래요)', run['answer']) for run in runs),
        }
        if name == 'brokerage':
            # A valid token (kept in the set's run state) is reused, not requested on every run: the first
            # request, which also writes and checks its functions, issues fewer tokens than it runs code,
            # and the second request issues none.
            checks['first: a token reused across runs'] = first['tokens_issued'] < max(first['code_runs'], 2)
            checks['second: no new token'] = second['tokens_issued'] == 0
        report = {'checks': checks, 'vendor': vendor.stats(),
                  'requests': [{key: run[key] for key in ('job', 'status', 'seconds', 'tool_calls', 'docs_fetches', 'tokens_issued', 'code_runs',
                                                          'library', 'saved_after', 'timeline', 'tools')}
                               | {'answer': run['answer'][:800]} for run in runs]}
        with store.db() as db:
            for item, run in zip(report['requests'], runs):
                item['judgment'] = sorted({json.loads(row['record']).get('outcome', '') for row in db.execute(
                    "SELECT record FROM work_decisions WHERE job_id=?", (run['job'],))})
        return report
    finally:
        tunnel.terminate()
        vendor.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--owner-data', default=str(Path.home() / '.local/share/agentos'))
    parser.add_argument('--domains', default=','.join(DOMAINS))
    parser.add_argument('--minutes', type=int, default=45)
    # A fixed scratch store: the Judgment AI's qualification is bound to the store path, so it is
    # earned once here and reused by later rehearsals.
    parser.add_argument('--scratch', default=str(Path.home() / '.local/share/agentos-rehearsal'))
    args = parser.parse_args()
    domains = [name for name in args.domains.split(',') if name]
    if not domains or set(domains) - set(DOMAINS):
        print(f'domains: {", ".join(DOMAINS)}')
        return 2

    from personal_agent.browser_jar import MemoryKey
    from personal_agent.family_setup import share_ai_route
    from personal_agent.quickstart import configured_service
    from personal_agent.quickstart_store import QuickStore

    scratch = args.scratch
    store = QuickStore(str(Path(scratch) / 'data'))
    store.api_slot_key = MemoryKey()
    from personal_agent.api_requests import remove_slot, slots
    for name in list(slots(store)):
        remove_slot(store, name)  # every rehearsal starts without a secret set (and so without a library)
    # The scratch store's key lives only in this process: any encrypted API record an earlier run left
    # (a reconnected set's previous credential, a run state) can no longer be read, and an unreadable
    # record withholds every Work's context.  None of it belongs to this run.
    from personal_agent.api_credentials import PREFIX, STATE_PREFIX
    for prefix in (PREFIX, STATE_PREFIX):
        for key in list(store.secret_items(prefix)):
            store.remove_secret(key)
    # Each rehearsal is a fresh conversation: an earlier run's unanswered requests in the scratch
    # history were read as the subject of this run's first request.
    with store.db() as db:
        db.execute('DELETE FROM messages')
    owner = QuickStore(args.owner_data)
    copied = share_ai_route(owner, store)
    if copied is None:
        print('no owner AI route to rehearse with')
        return 2
    # The Judgment AI's route also needs its recorded CLI checks (not part of the family share).
    for key in ('decision_cli_capabilities', 'main_ai_checks', 'codex_bundled_models', 'engine_login'):
        value = owner.config(key, None)
        if value:
            store.put(key, value)
    service = configured_service(store)
    # The one-time input page is answered in process (``api_input.submit``); only its link needs a port.
    service.local_server_port = 8999
    deadline = time.time() + args.minutes * 60
    qualification = qualify_judgment(service, store, deadline)
    report = {'qualification': qualification, 'scratch': scratch,
              'checks': {'judgment AI orchestrated': qualification in ('already qualified', 'passed')}}
    for name in domains:
        report[name] = rehearse_domain(name, service, store, deadline)
        report['checks'].update({f'{name}: {check}': passed for check, passed in report[name]['checks'].items()})
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if all(report['checks'].values()) else 1


if __name__ == '__main__':
    sys.exit(main())
