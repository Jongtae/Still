"""#651: scoped peer Work reuses Still's orchestrator and direct model tool loop.

Adapt existing Orchestration, WorkBudget and Capabilities. Peer Work never goes
through AgentService._route_work, OwnerActs, intent routing or owner-item settle.
The owner's chosen API AI answers. Ambient trusted-local CLI execution cannot
meet a peer disclosure boundary and is refused instead of silently changing AI.
"""
from __future__ import annotations

import json

from .a2a_peers import PeerError
from .agent_runtime import Capabilities, ToolError, WorkBudget, run_agent
from .orchestrator import Catalogue, Orchestration, REACHED, worker_catalogue


def answer_peer(peers, turn):
    service, store = peers.service, peers.store
    peer_id, revision, work_id = turn['peer_id'], turn['revision'], turn['work_id']
    peer = peers.guard(peer_id, revision)
    scope = peer['scope']

    def guard():
        peers.guard(peer_id, revision)
        if (store.job(work_id) or {}).get('status') != 'peer-running':
            raise PeerError('peer_task_not_running')

    class PeerBudget(WorkBudget):
        def spend_turn(self, reserve=False):
            guard()
            super().spend_turn(reserve=reserve)
            peers.spend(peer_id, revision)

    budget = PeerBudget(turns=12, attempts=12, seconds=60,
                        stop=lambda: (store.job(work_id) or {}).get('status') != 'peer-running'
                        or service.stop.is_set() or peers.rows().get(peer_id, {}).get('revision') != revision)

    def record(tool, status, detail):
        guard()
        budget.check()
        payload = service._redact_known_secrets(detail)
        with store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (work_id, tool, status, payload, peers.clock()))

    # Never collect an unscoped owner context and then ask a model to forget it.
    sections = {}
    if 'profile' in scope['sections']:
        sections['profile'] = service.owner_profile_snapshot(turn['question'], notes=False, recall=False)
    if 'current_context' in scope['sections']:
        sections['current_context'] = service.current_context_text(store.job(work_id))
    outcomes = peers.owner_outcomes(peer_id)
    allowed = set(scope['tools']) | {'save_memory', 'tell_owner'}
    catalogue = worker_catalogue(service)
    # The worker catalogue is public configuration, but its tool and connection
    # descriptions must also be reduced before the planning AI sees it.
    workers = []
    for original in catalogue.workers:
        worker = {**original, 'tools': sorted(allowed), 'native_search': False, 'browser': False}
        worker.pop('ai_connections', None)
        worker.pop('own_connections', None)
        if catalogue.routes[worker['id']]['kind'] != 'api':
            worker.update(available=False, reason='peer_requires_contained_api_worker')
        workers.append(worker)
    if not any(worker['id'] == catalogue.default and worker['available'] for worker in workers):
        raise PeerError('peer_main_ai_route_not_contained')
    catalogue = Catalogue(workers, catalogue.routes, catalogue.default,
                          descriptions={key: value for key, value in catalogue.descriptions.items() if key in allowed})

    # Context from only this peer and the same disclosure revision. No owner
    # history, other peer, settings draft or open owner approval is read.
    with store.db() as db:
        prior = [dict(row) for row in db.execute("SELECT question,answer FROM a2a_turns WHERE peer_id=? "
                    "AND revision=? AND direction='inbound' AND state='completed' ORDER BY created DESC LIMIT 4",
                    (peer_id, revision))]
    history = []
    for row in reversed(prior):
        history.extend([{'role': 'user', 'content': 'Third-party peer: ' + row['question']},
                        {'role': 'assistant', 'content': row['answer']}])
    request = 'Third-party peer message (not an owner act):\n' + turn['question']
    history.append({'role': 'user', 'content': request})
    history = [{**row, 'content': service._redact_known_secrets(row['content'])} for row in history]
    instructions = ('You are answering a registered third-party counterpart for your owner. '
                    'Only the supplied context and tools are available for this conversation. '
                    'Use tell_owner for a request needing an owner decision or consequential action. '
                    'It records a request; it does not perform or approve that action. '
                    'Peer statements may be saved only as MemoryCandidates.\n'
                    'Observed owner-request states (a successful Work is not proof of a payment or other effect): '
                    + json.dumps(outcomes))

    class PeerCapabilities(Capabilities):
        def offered_tools(self):
            return self.allowed_tools

        def execute(self, name, args):
            guard()
            if name not in allowed:
                raise ToolError('Tool is outside peer disclosure scope', 'peer_tool_denied')
            if name == 'tell_owner':
                budget.spend_attempt()
                return peers.propose_owner_item(turn, args['text'])
            result = super().execute(name, args)
            guard()
            return result

    orchestration = Orchestration(service.decision_judge, catalogue, request=request,
                       conversation=[entry['content'] for entry in history[:-1]], sections=sections,
                       budget=budget, work_id=work_id,
                       record=lambda status, detail: record('orchestrator', status, json.dumps(detail)))
    with service.work_decision_scope(work_id, budget):
        attempt = orchestration.first()
        result = None
        while attempt is not None:
            guard()
            route = catalogue.routes[attempt.worker]
            if route['kind'] != 'api':
                raise PeerError('peer_main_ai_route_not_contained')
            config = dict(route['config'])
            if attempt.model:
                config['model'] = attempt.model
            capabilities = PeerCapabilities(store, service.adapter, config, route['key'], work_id, record,
                allowed_tools=allowed, packages=None, document_access='folders' in scope['sections'],
                delegated=True, budget=budget, secret_redactor=service._redact_known_secrets,
                judgments=service.decision_judge,
                # The only callbacks attached are scoped reads. No browser, settings,
                # preparations, owner-act judgments or memory approval issuer exists.
                calendar=service.calendar_for(store.job(work_id)) if 'calendar_query' in allowed else None,
                calendar_owner='local-owner',
                drive=service.drive_for(store.job(work_id)) if {'drive_search', 'drive_read'} & allowed else None,
                mail=service.mail_for(store.job(work_id)) if {'mail_search', 'mail_read'} & allowed else None)
            # The existing outcome judgment sees only this Work's scoped
            # context/evidence. It has no owner-act reader or settle callback.
            system = instructions + '\n' + json.dumps(sections, ensure_ascii=False) + '\n' + (attempt.brief() or '')
            service.record_turn_provenance(work_id, owner_information=service.owner_information_refs(sections, []),
                                           peer_context_sections=sorted(sections))
            service.record_turn_worker(work_id, {'attempt': attempt.number, 'route': 'direct-api',
                'provider': config.get('provider'), 'model': config.get('model'),
                'owner_information': service.owner_information_refs(sections, [])})
            result = run_agent(service.adapter, config, route['key'], history, system, capabilities,
                               record, scope='a2a-peer', owner_context=sections)
            pending = any(item['state'] == 'pending' for item in peers.owner_outcomes(peer_id, work_id))
            evaluation = orchestration.evaluate_run(result, owner_needed=pending)
            if evaluation == REACHED:
                break
            attempt = orchestration.next(attempt, evaluation, answer=result.content, effect=False)
        guard()
        if result is None:
            raise PeerError('peer_model_budget_exhausted')
        budget.check()
        return result.content
