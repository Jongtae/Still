"""#921 PR-3: model-free real-store queue, writers, CAS and attribution evidence."""
import json
from datetime import datetime, timezone

import pytest

from personal_agent import information_use, owner_model as om
from personal_agent.conversation_handoff import ConversationJudgments
from personal_agent.decision import (FixtureDecisionEngine, StructuredDecision, OUTCOME_DECIDED,
    OUTCOME_UNAVAILABLE, DecisionConfidence, UnavailableDecisionEngine, audit_record)
from personal_agent.memory_terms import parse_meta
from personal_agent.owner_forget import OwnerForget
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def env(tmp_path, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr('personal_agent.quickstart_store.time.time', lambda: clock[0])
    store = QuickStore(tmp_path / 'state')
    service = AgentService(store)
    service.owner_model.clock = lambda: clock[0]
    service.owner_model_spawn = lambda call: call()
    service.owner_model.set_controls({'dream': False})
    asked = []

    def answer(context, question, schema):
        asked.append(context)
        data = {'notes': [{'n': n, 'terms': '검색 retrieval'}
                         for n in range(1, len(context.facts['notes'].splitlines())+1)]}
        confidence = DecisionConfidence(1, route='chosen-route', model='active-model', observed_model='observed', sent=True)
        service.record_decision(audit_record(context, 'structured', OUTCOME_DECIDED, data, confidence, clock[0]))
        return StructuredDecision(OUTCOME_DECIDED, data, confidence)

    service.use_decision_engine(FixtureDecisionEngine(structured=answer))
    return store, service, clock, asked


def save(store, n=0, **kw):
    return store.save_memory(f'profile.fact{n}', f'canonical fact {n}', **kw)


def rows(store):
    with store.db() as db:
        return [dict(row) for row in db.execute('SELECT * FROM owner_model_upkeep WHERE kind=? ORDER BY created,job_id', (om.KIND_TERMS,))]


def raw(store, row):
    with store.db() as db:
        value = db.execute('SELECT * FROM memories WHERE id=?', (row['id'],)).fetchone()
        return dict(value) if value else None


def claim(env):
    store, service, clock, _ = env
    run = service.owner_model.arm_terms(now=clock[0], available=True)
    row = service.owner_model.claim_due(clock[0], redact=service._owner_model_judgments(None).redact)
    assert row['job_id'] == run
    return row


@pytest.mark.parametrize('with_work', [False, True])
def test_idle_many_batches_and_attribution(env, with_work):
    store, service, clock, asked = env
    work = store.enqueue('source', 'one', owner_words=True) if with_work else None
    if work:
        with store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (work,))
    saved = [save(store, n, work_id=work) for n in range(13)]
    for _ in range(12):
        service.run_owner_model_upkeep()
        clock[0] += om.WINDOW_SECONDS + 1
    assert len(asked) == 2
    assert len(rows(store)) == 2
    assert [json.loads(row['observation'])['cap'] for row in rows(store)] == [8, 16]
    assert not service.current_work_id
    for item in saved:
        meta = parse_meta(raw(store, item)['retrieval_terms_meta'])
        assert (meta['status'], meta['route'], meta['model'], meta['observed_model'], meta['policy']) == (
            'keyed', 'chosen-route', 'active-model', 'observed', 'terms-pass')
        assert meta['run'].startswith('memory-terms:')
    for row in rows(store):
        manifest = json.loads(row['observation'])
        assert manifest['result'] == 'ok'
        assert all(set(item) == {'id', 'digest', 'outcome'} for item in manifest['entries'])
        assert all(item['outcome'] == 'keyed' for item in manifest['entries'])
        assert 'canonical' not in row['observation'] and 'retrieval' not in row['observation']
    with store.db() as db:
        assert not db.execute("SELECT 1 FROM tool_events WHERE job_id LIKE 'memory-terms:%'").fetchone()
    audit = store.config('decision_audit')
    assert len(audit) == 2 and all(not item.get('work_id') for item in audit)
    if work:
        assert len(store.work_decisions(work)) == 2


@pytest.mark.parametrize('paused,unavailable', [(True, False), (False, True)])
def test_no_arm_when_paused_or_unavailable(env, paused, unavailable):
    store, service, clock, asked = env
    save(store)
    if paused: service.owner_model.set_controls({'enabled': False})
    if unavailable: service.use_decision_engine(UnavailableDecisionEngine())
    assert not service.run_owner_model_upkeep()
    assert rows(store) == [] and asked == []


def test_claim_priority_share_expiry_crash(env):
    store, service, clock, _ = env
    save(store)
    up = service.owner_model
    run = up.arm_terms(available=True)
    assert not up.arm_terms(available=True)
    for n in range(2): up.enqueue(None, f'work{n}', now=clock[0]+n)
    for n in (1, 0):
        row = up.claim_due(clock[0])
        assert row['job_id'] == f'work{n}'
        up.finish(row['job_id'], om.STATE_DONE, 0, 'done', {}, clock[0])
    clock[0] += om.MAX_PENDING_SECONDS+1
    row = up.claim_due(clock[0])
    assert row['job_id'] == run and not row['expired']
    assert not up.arm_terms(available=True)
    manifest = rows(store)[0]['observation']
    clock[0] += om.CLAIM_STALE_SECONDS+1
    assert up.expire_interrupted(clock[0]) == 1
    assert rows(store)[0]['observation'] == manifest
    new = up.arm_terms(available=True)
    assert new == 'memory-terms:2'
    up.set_controls({'daily_calls': 4})
    assert up.claim_due(clock[0]) is None  # interrupted call spent the share
    clock[0] += om.WINDOW_SECONDS+1
    assert up.claim_due(clock[0])['job_id'] == new


@pytest.mark.parametrize('bad', [[], [{'n': 1, 'terms': 'x'}, {'n': 1, 'terms': 'y'}],
    [{'n': 1, 'terms': 'x'}, {'n': 3, 'terms': 'y'}], [{'n': 1, 'terms': 7}, {'n': 2, 'terms': 'y'}],
    [{'n': True, 'terms': 'x'}, {'n': 2, 'terms': 'y'}], [{'n': 1, 'terms': 'x'*401}]])
def test_invalid_whole_answer_retries_stop_at_three(env, bad):
    store, service, clock, _ = env
    item = save(store)
    service.use_decision_engine(FixtureDecisionEngine(structured=lambda *args:
        StructuredDecision(OUTCOME_DECIDED, {'notes': bad}, DecisionConfidence(1, sent=True))))
    for n in range(1, om.memory_terms.FAIL_LIMIT + 1):
        assert service.run_owner_model_upkeep()
        meta = parse_meta(raw(store, item)['retrieval_terms_meta'])
        assert meta['status'] == 'failed' and meta['n'] == n
        manifest = json.loads(rows(store)[-1]['observation'])
        assert manifest['result'] == 'rejected' and manifest['entries'][0]['outcome'] == 'failed'
        clock[0] += om.WINDOW_SECONDS+1
    assert not service.run_owner_model_upkeep()
    assert parse_meta(raw(store, item)['retrieval_terms_meta'])['n'] == om.memory_terms.FAIL_LIMIT


@pytest.mark.parametrize('truncated', [False, True])
def test_rejected_batches_halve_without_changing_meta(env, truncated):
    store, service, clock, _ = env
    saved = [save(store, n) for n in range(8)]
    # Rejections must preserve both absent metadata and existing generation/failure counts.
    with store.db() as db:
        for item, status in zip(saved, ('cleared', 'failed')):
            meta = om.memory_terms.build_meta(status, previous={'g': 2}, at=clock[0], n=1)
            db.execute('UPDATE memories SET retrieval_terms_meta=? WHERE id=?', (meta, item['id']))
    before = [raw(store, item)['retrieval_terms_meta'] for item in saved]
    service.use_decision_engine(FixtureDecisionEngine(structured=lambda *args:
        StructuredDecision(OUTCOME_UNAVAILABLE if truncated else OUTCOME_DECIDED,
                           None if truncated else {'notes': []}, DecisionConfidence(1, sent=True))))
    for size in (8, 4, 2):
        assert service.run_owner_model_upkeep()
        manifest = json.loads(rows(store)[-1]['observation'])
        assert (manifest['cap'], manifest['result'], manifest['rows']) == (size, 'rejected', size)
        assert len(manifest['entries']) == size
        assert all(item['outcome'] == 'rejected-batch' for item in manifest['entries'])
        assert [raw(store, item)['retrieval_terms_meta'] for item in saved] == before
        clock[0] += om.WINDOW_SECONDS+1
    # Once a singleton is rejected, its allowance is used, and the cap stays at one.
    for _ in range(2):
        assert service.run_owner_model_upkeep()
        manifest = json.loads(rows(store)[-1]['observation'])
        assert manifest['cap'] == 1 and len(manifest['entries']) == 1
        assert manifest['entries'][0]['outcome'] == 'failed'
        clock[0] += om.WINDOW_SECONDS+1


def test_success_doubles_up_to_hard_ceiling(env):
    store, service, clock, asked = env
    for n in range(80): save(store, n)
    for size in (8, 16, 25, 25):
        assert service.run_owner_model_upkeep()
        manifest = json.loads(rows(store)[-1]['observation'])
        assert (manifest['cap'], manifest['result'], manifest['rows']) == (size, 'ok', size)
        assert len(manifest['entries']) == size
        assert len(asked[-1].facts['notes'].splitlines()) == size
        clock[0] += om.WINDOW_SECONDS+1


@pytest.mark.parametrize('result,expected_cap', [('ok', 16), ('rejected', 1)])
@pytest.mark.parametrize('packed', [False, True])
def test_cap_and_sent_rows_drive_different_adjustments(env, result, expected_cap, packed):
    store, service, clock, _ = env
    # Either a small backlog or character packing sends only three of the allowed eight.
    for n in range(8 if packed else 3):
        store.save_memory(f'profile.fact{n}', 'x'*1500 if packed else f'canonical fact {n}')
    if result == 'rejected':
        service.use_decision_engine(FixtureDecisionEngine(structured=lambda *args:
            StructuredDecision(OUTCOME_DECIDED, {'notes': []}, DecisionConfidence(1, sent=True))))
    assert service.run_owner_model_upkeep()
    manifest = json.loads(rows(store)[-1]['observation'])
    assert (manifest['cap'], manifest['rows'], manifest['result']) == (8, 3, result)
    assert len(manifest['entries']) == 3
    clock[0] += om.WINDOW_SECONDS+1
    save(store, 99)
    assert json.loads(claim(env)['observation'])['cap'] == expected_cap


@pytest.mark.parametrize('state,result', [
    (om.STATE_DONE, 'unsent'), (om.STATE_DONE, 'oversize'),
    (om.STATE_EXPIRED, None), (om.STATE_EXPIRED, 'ok'), (om.STATE_DONE, None)])
def test_finished_without_answer_keeps_size(env, state, result):
    store, service, clock, _ = env
    save(store)
    up = service.owner_model
    row = claim(env)
    # Seed a previous run's cap and finish state, including an interrupted claim.
    up.finish(row['job_id'], state, 0, om.EVENT_RECORDED,
              {'manifest': om.terms_manifest(row['batch'], cap=16, result=result)}, clock[0], kind=om.KIND_TERMS)
    assert json.loads(claim(env)['observation'])['cap'] == 16


def test_only_latest_finished_run_controls_size(env):
    store, service, clock, _ = env
    for n in range(8): save(store, n)
    up = service.owner_model
    for result in ('ok', 'rejected'):
        row = claim(env)
        up.finish(row['job_id'], om.STATE_DONE, 0, om.EVENT_RECORDED,
                  {'manifest': om.terms_manifest(row['batch'], cap=8, result=result)}, clock[0], kind=om.KIND_TERMS)
    pending = up.arm_terms(available=True)
    with store.db() as db:
        db.execute('UPDATE owner_model_upkeep SET observation=? WHERE job_id=?',
                   (om.terms_manifest([], cap=25, result='ok'), pending))
        assert up.terms_cap(db) == 4
        db.execute('UPDATE owner_model_upkeep SET state=? WHERE job_id=?', (om.STATE_CLAIMED, pending))
        assert up.terms_cap(db) == 4


def test_legacy_manifest_starts_at_eight(env):
    store, service, clock, _ = env
    save(store)
    row = claim(env)
    service.owner_model.finish(row['job_id'], om.STATE_DONE, 0, om.EVENT_RECORDED,
                               {'manifest': '{"rows":[]}'}, clock[0], kind=om.KIND_TERMS)
    assert json.loads(claim(env)['observation'])['cap'] == 8


def test_empty_claim_is_unsent_and_keeps_size(env):
    store, service, clock, asked = env
    item = save(store)
    up = service.owner_model
    run = up.arm_terms(available=True)
    OwnerForget(store).forget('local-owner', 'memory:'+item['id'])
    row = up.claim_due(clock[0])
    assert row['batch'] == []
    state, calls, status, detail = up.run_terms(row, service._owner_model_judgments(None))
    up.finish(run, state, calls, status, detail, clock[0], kind=om.KIND_TERMS)
    assert json.loads(rows(store)[-1]['observation']) == {'cap': 8, 'rows': 0, 'result': 'unsent', 'entries': []}
    assert calls == 0 and not asked
    save(store, 1)
    assert json.loads(claim(env)['observation'])['cap'] == 8


def test_whole_facts_redaction_singleton_and_oversize(env):
    store, service, clock, asked = env
    store.secret('telegram_token', 'secret-to-remove')
    item = store.save_memory('profile.large', '𐀀'*4000)
    assert service.run_owner_model_upkeep()
    assert asked[-1].facts['notes'] == '1. profile.large: ' + '𐀀'*4000
    clock[0] += om.WINDOW_SECONDS+1
    item = save(store, 2)
    # Simulate legacy/expanded rows above today's store bound.
    with store.db() as db:
        db.execute('UPDATE memories SET content=? WHERE id=?', ('z'*6000, item['id']))
    assert service.run_owner_model_upkeep()
    assert parse_meta(raw(store, item)['retrieval_terms_meta'])['status'] == 'oversize'
    manifest = json.loads(rows(store)[-1]['observation'])
    assert (manifest['cap'], manifest['result']) == (16, 'oversize')
    assert manifest['rows'] == 0
    assert len(asked) == 1
    clock[0] += om.WINDOW_SECONDS+1
    assert not service.run_owner_model_upkeep()
    item = save(store, 3)
    with store.db() as db:
        db.execute('UPDATE memories SET content=? WHERE id=?', ('secret-to-remove'*1000, item['id']))
    # The real upkeep redactor shrinks a value longer than any pack allowance.
    service._redact_known_secrets = lambda text: text.replace('secret-to-remove'*1000, '[redacted]')
    assert service.run_owner_model_upkeep()
    assert asked[-1].facts['notes'] == '1. profile.fact3: [redacted]'
    assert json.loads(rows(store)[-1]['observation'])['cap'] == 16


@pytest.mark.parametrize('action', ['forget', 'undo', 'supersede', 'retract', 'delete', 'edit', 'date'])
def test_stale_interleavings(env, action):
    store, service, clock, _ = env
    item = save(store, valid_until='2026-11-17')
    row = claim(env)
    if action in ('forget', 'undo'):
        forgotten = OwnerForget(store).forget('local-owner', 'memory:'+item['id'])
        if action == 'undo': OwnerForget(store).undo('local-owner', forgotten['receipt'])
    elif action == 'supersede': store.save_memory(item['memory_key'], 'replacement')
    elif action == 'retract': store.retract_memory('local-owner', item['id'], item['content_digest'])
    elif action == 'delete':
        with store.db() as db: db.execute('DELETE FROM memories WHERE id=?', (item['id'],))
    elif action == 'edit':
        with store.db() as db: db.execute('UPDATE memories SET content=?,content_digest=? WHERE id=?', ('edited', 'new-digest', item['id']))
    elif action == 'date': clock[0] += 86400
    state, calls, status, detail = service.owner_model.run_terms(row, service._owner_model_judgments(None))
    assert json.loads(detail['manifest'])['entries'][0]['outcome'] == 'stale'
    assert not raw(store, item) or not raw(store, item)['retrieval_terms']


def test_unsent_parks_with_no_charge(env):
    store, service, clock, _ = env
    for n in range(8): save(store, n)
    assert service.run_owner_model_upkeep()
    clock[0] += om.WINDOW_SECONDS+1
    item = save(store, 8)
    service.use_decision_engine(FixtureDecisionEngine(structured=lambda *args:
        StructuredDecision(OUTCOME_UNAVAILABLE, confidence=DecisionConfidence(sent=False))))
    assert service.run_owner_model_upkeep()
    row = rows(store)[-1]
    assert row['state'] == om.STATE_PENDING and row['calls'] == 0
    assert raw(store, item)['retrieval_terms_meta'] is None
    manifest = json.loads(row['observation'])
    assert (manifest['cap'], manifest['result']) == (16, 'unsent')
    assert manifest['rows'] == 0
    for _ in range(3): assert not service.run_owner_model_upkeep()
    assert len(rows(store)) == 2
    clock[0] += om.WINDOW_SECONDS+1
    assert service.run_owner_model_upkeep()
    retry = rows(store)[-1]
    assert retry['job_id'] == row['job_id'] and retry['calls'] == 0
    assert json.loads(retry['observation']) == manifest


def test_complete_prompt_bounds(env):
    from personal_agent.decision import DECISION_SYSTEM
    from personal_agent.bounded_execution import MAX_PROMPT_BYTES
    store, service, clock, asked = env
    store.save_memory('profile.large', '𐀀'*4000)
    service.run_owner_model_upkeep()
    context = asked[0]
    complete = (DECISION_SYSTEM + '\n\n' + f'Purpose: {context.purpose}\n{context.render()}\n\n'
                + om.TERMS_QUESTION + '\n\nReply with only one JSON object that matches the required schema.')
    assert len(complete.encode()) < MAX_PROMPT_BYTES


@pytest.mark.parametrize('kind', ['upkeep', 'gather', 'dream'])
@pytest.mark.parametrize('terms', ['검색 retrieval', '', 'the-stored-secret-value'])
def test_writer_insert_terms_and_identity(env, monkeypatch, kind, terms):
    from test_owner_model_upkeep import proposal
    from test_owner_model_dream import fact, dream
    store, service, clock, _ = env
    store.secret('telegram_token', 'the-stored-secret-value')
    work = store.enqueue('I live beside the river', 'writer', owner_words=True)
    with store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded',provider='subscription' WHERE id=?", (work,))
    item = (proposal('profile.home', 'beside the river', evidence='beside the river') if kind == 'upkeep'
            else fact('profile.home', 'beside the river', 't1'))
    item['terms'] = terms
    def answer(context, question, schema):
        used = context.purpose == {'upkeep': om.PURPOSE, 'gather': om.DREAM_GATHER_PURPOSE,
                                   'dream': om.DREAM_PURPOSE}[kind]
        data = dream([item] if used else []) if context.purpose == om.DREAM_PURPOSE else {'proposals': [item] if used else []}
        confidence = DecisionConfidence(1, route=context.purpose, model='active', observed_model='reported', sent=True)
        return StructuredDecision(OUTCOME_DECIDED, data, confidence)
    judgments = ConversationJudgments(FixtureDecisionEngine(structured=answer))
    inserted = []
    original = store.save_memory
    def save_with_receipt(*args, **kwargs):
        assert 'retrieval_terms' in kwargs and 'retrieval_terms_meta' in kwargs
        saved = original(*args, **kwargs)
        inserted.append(raw(store, saved))
        return saved
    monkeypatch.setattr(store, 'save_memory', save_with_receipt)
    job = store.job(work)
    if kind == 'upkeep':
        service.owner_model.run(job, judgments, answer='', profile='', clock='')
    else:
        om.WorkingNotes(store).migrate()
        service.owner_model.run_dream(job, {}, judgments, turns=[('t1', job['message'], '')], profile='',
            clock='', diary_date='2026-11-17', ends_after=clock[0], source_works={'t1': work})
    assert len(inserted) == 1
    row = inserted[0]
    if terms == '검색 retrieval':
        meta = parse_meta(row['retrieval_terms_meta'])
        assert row['retrieval_terms'] == terms
        assert meta['run'] == work and meta['policy'] == ('upkeep' if kind == 'upkeep' else 'dream')
        assert meta['route'] == {'upkeep': om.PURPOSE, 'gather': om.DREAM_GATHER_PURPOSE, 'dream': om.DREAM_PURPOSE}[kind]
        assert meta['model'] == 'active' and meta['observed_model'] == 'reported'
    else:
        assert not row['retrieval_terms'] and row['retrieval_terms_meta'] is None


@pytest.mark.parametrize('kind', ['upkeep', 'gather', 'dream', 'terms'])
@pytest.mark.parametrize('secret', ['StoredSecret921TermsValue', 'sk-proj-abcdefghijklmnopqrstuvwxyz123456'])
def test_writer_terms_secrets_never_reach_durable_records(env, kind, secret):
    """#957 regression class: inspect persisted records without display-time redaction."""
    from test_owner_model_upkeep import proposal
    from test_owner_model_dream import fact, dream

    store, service, clock, _ = env
    # The credential shape is deliberately not stored, so shape filtering stands alone.
    if secret == 'StoredSecret921TermsValue':
        store.secret('telegram_token', secret)
    work = store.enqueue('I live beside the river and drink jasmine tea.', 'terms-secret',
                         owner_typed=True, owner_words=True)
    facts = [('profile.home', 'beside the river', 'before ' + secret + ' after'),
             ('profile.drink', 'jasmine tea', '차 beverage')]
    run_kind = om.KIND_DREAM if kind in ('gather', 'dream') else kind
    with store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded',provider='subscription',response='Understood.' WHERE id=?",
                   (work,))
    if kind == 'terms':
        for key, content, _terms in facts:
            store.save_memory(key, content, work_id=work)
    else:
        if run_kind == om.KIND_DREAM:
            om.WorkingNotes(store).migrate()
        assert service.owner_model.enqueue(None, work, kind=run_kind)

    purpose = {'upkeep': om.PURPOSE, 'gather': om.DREAM_GATHER_PURPOSE,
               'dream': om.DREAM_PURPOSE, 'terms': 'memory-terms'}[kind]
    answered = []

    def answer(context, question, schema):
        items = []
        if context.purpose == purpose:
            answered.append(context.purpose)
            if kind == 'terms':
                # Use the actual packed order, rather than assuming row-id ordering.
                items = [{'n': n, 'terms': next(terms for key, _content, terms in facts
                                               if key in line)}
                         for n, line in enumerate(context.facts['notes'].splitlines(), 1)]
            else:
                for key, content, terms in facts:
                    item = (proposal(key, content, evidence=content) if kind == 'upkeep'
                            else fact(key, content, 't1'))
                    items.append({**item, 'terms': terms})
        data = ({'notes': items} if kind == 'terms' else dream(items)
                if context.purpose == om.DREAM_PURPOSE else {'proposals': items})
        confidence = DecisionConfidence(1, route='fixture-route', model='fixture-model', sent=True)
        # FixtureDecisionEngine has no audit sink; use the same content-free record as production.
        service.record_decision(audit_record(context, 'structured', OUTCOME_DECIDED, data, confidence, clock[0]))
        return StructuredDecision(OUTCOME_DECIDED, data, confidence)

    service.use_decision_engine(FixtureDecisionEngine(structured=answer))
    assert service.run_owner_model_upkeep()
    assert answered == [purpose]
    saved = {row['memory_key']: raw(store, row) for row in store.memories()}
    assert set(saved) == {key for key, _content, _terms in facts}
    for key, content, _terms in facts:
        assert (saved[key]['content'], saved[key]['state']) == (content, 'current')
    assert saved['profile.home']['retrieval_terms'] is None
    assert saved['profile.drink']['retrieval_terms'] == '차 beverage'
    if kind != 'terms':
        assert saved['profile.home']['retrieval_terms_meta'] is None

    with store.db() as db:
        upkeep = [dict(row) for row in db.execute('SELECT * FROM owner_model_upkeep')]
        events = [dict(row) for row in db.execute('SELECT * FROM tool_events')]
    [finished] = [row for row in upkeep if row['kind'] == run_kind]
    assert finished['state'] == om.STATE_DONE and finished['calls'] > 0
    if kind == 'terms':
        manifest = json.loads(finished['observation'])
        assert (manifest['rows'], manifest['result']) == (2, 'ok')
        assert {entry['outcome'] for entry in manifest['entries']} == {'empty', 'keyed'}
        assert not events
    else:
        assert any(event['tool'] == (om.EVENT_TOOL if kind == 'upkeep' else om.DREAM_EVENT_TOOL)
                   for event in events)
    decisions = store.work_decisions(work)
    global_audit = store.config('decision_audit')
    assert decisions and global_audit
    assert any(row['purpose'] == purpose for row in decisions)
    assert any(row['purpose'] == purpose for row in global_audit)
    audit = information_use.work_information_use(store, work)
    assert audit is not None
    assert audit['sent_to']['judgment_ai']['calls'] == len(decisions)
    surfaces = {'Memory terms': [row['retrieval_terms'] for row in saved.values()],
                'upkeep rows including observation': upkeep,
                'tool event details': [event['detail'] for event in events],
                'Work decisions': decisions, 'global decision audit': global_audit,
                'information use': audit}
    for name, value in surfaces.items():
        assert secret not in json.dumps(value, ensure_ascii=False), name


def test_dream_admission_and_reader_ignore_terms(env):
    store, service, clock, _ = env
    up = service.owner_model
    up.set_controls({'dream': True})
    save(store)
    run = up.arm_terms(available=True)
    assert up.enqueue_dream_if_due(clock[0], 3) is None
    for n in range(om.DREAM_MIN_WORKS):
        work = store.enqueue('owner words', str(n), owner_words=True)
        with store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded',provider='subscription' WHERE id=?", (work,))
            up.enqueue(db, work, now=clock[0]+n)
            db.execute("UPDATE owner_model_upkeep SET state='done' WHERE job_id=?", (work,))
    clock[0] += om.DREAM_CHECK_SECONDS+1
    assert up.enqueue_dream_if_due(clock[0], 3) == work
    assert up.claim_due(clock[0])['kind'] == om.KIND_DREAM
    assert rows(store)[0]['job_id'] == run and rows(store)[0]['state'] == om.STATE_PENDING


def test_fanout_only_retained_sources(env):
    store, service, clock, _ = env
    works = [store.enqueue('source', str(n), owner_words=True) for n in range(3)]
    with store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded'")
    item = save(store, source_works=works)
    with store.db() as db:
        db.execute('DELETE FROM jobs WHERE id=?', (works[-1],))
    assert service.run_owner_model_upkeep()
    for work in works[:2]:
        assert len(store.work_decisions(work)) == 1
    assert not store.work_decisions(works[-1])


def test_pause_and_cap_rechecked_before_call(env):
    store, service, clock, asked = env
    save(store)
    row = claim(env)
    service.owner_model.set_controls({'daily_calls': 0})
    state, calls, _, _ = service.owner_model.run_terms(row, service._owner_model_judgments(None))
    assert (state, calls) == (om.STATE_PENDING, 0)
    assert asked == []


def test_terms_schema_and_host_bounds():
    from test_owner_model_upkeep import proposal
    p = proposal('profile.home', 'some fact')
    assert 'terms' in om.SCHEMA['properties']['proposals']['items']['required']
    assert 'terms' in om._DREAM_ITEM['required']
    for bad in [None, 7, 'x'*401]:
        assert not om.validate([{**p, 'terms': bad}], set(), set())[0]
    assert om.validate([p], set(), set())[0]
    notes_schema = om.TERMS_SCHEMA['properties']['notes']
    assert notes_schema['maxItems'] == 25
    assert notes_schema['items']['properties']['terms']['maxLength'] == 400
    answer = {'notes': [{'n': n, 'terms': '𐀀'*400} for n in range(1, 26)]}
    assert om.terms_answer(answer, 25)
    answer['notes'][0]['terms'] += 'x'
    assert not om.terms_answer(answer, 25)


# Reuse the model-free Work executor harness to verify the real settle transaction.
from test_owner_model_upkeep import Upkeep


class TestSettle(Upkeep):
    def test_settle_arms_terms_without_calling_it(self):
        save(self.store)
        job, result = self.run_work('A request', owner_words=True)
        assert result['status'] == 'succeeded'
        assert len(rows(self.store)) == 1
        assert rows(self.store)[0]['state'] == om.STATE_PENDING
        assert not any(asked[1].purpose == 'memory-terms' for asked in self.fixture.asked)


@pytest.mark.parametrize('char,length,count', [('x', 2000, 2), ('x', 2490, 1), ('x', 3000, 1),
                                             ('𐀀', 1900, 1), ('𐀀', 1200, 3)])
def test_packing_whole_rendered_bounds(env, char, length, count):
    store, service, clock, asked = env
    for n in range(8): store.save_memory(f'profile.size{n}', char*length)
    row = claim(env)
    assert json.loads(row['observation'])['cap'] == 8
    assert len(row['batch']) == count
    text = '\n'.join(item['rendered'] for item in row['batch'])
    assert len(text) <= 5000 and len(text.encode()) <= 15000
    assert all(item['rendered'].endswith(char*length) for item in row['batch'])


def test_atomic_arming_and_owner_holding_scope(env):
    from concurrent.futures import ThreadPoolExecutor
    store, service, clock, _ = env
    save(store, 1, valid_until='2026-11-16')
    save(store, 2, owner_id='another-owner')
    assert not service.owner_model.arm_terms(available=True)
    save(store, 3)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.owner_model.arm_terms(available=True), range(8)))
    assert len([item for item in results if item]) == 1
    assert len(rows(store)) == 1


def test_unconfigured_active_api_preflight(env):
    from personal_agent.decision import ModelDecisionEngine, RoutedDecisionEngine
    store, service, clock, asked = env
    save(store)
    service.use_decision_engine(RoutedDecisionEngine(lambda: ModelDecisionEngine(None, lambda: None)))
    assert not service.run_owner_model_upkeep()
    assert not rows(store)


def test_complete_api_request_and_active_model(env):
    from personal_agent.decision import ModelDecisionEngine
    store, service, clock, asked = env
    sent = []
    class Adapter:
        def tool_turn(self, config, key, messages, tools, **kwargs):
            sent.append((config, messages, kwargs))
            return ({'tool_calls': [{'function': {'name': 'decide', 'arguments': json.dumps(
                {'notes': [{'n': 1, 'terms': '검색'}], 'confidence': 1})}}]}, 'observed')
    engine = ModelDecisionEngine(Adapter(), lambda: ({'provider': 'openai', 'model': 'active',
        'endpoint': 'https://api.openai.com/v1'}, 'fixture-key'))
    judgments = ConversationJudgments(engine)
    data, decision = judgments.memory_terms('1. profile.large: '+'𐀀'*4000)
    assert sent[0][0]['model'] == 'active'
    assert len(json.dumps(sent[0], ensure_ascii=False).encode()) < 48000
    assert data == {'notes': [{'n': 1, 'terms': '검색'}]}


def test_replay_is_counted_and_guarded_generation_survives(env):
    store, service, clock, asked = env
    item = save(store)
    receipt = OwnerForget(store).forget('local-owner', 'memory:'+item['id'])
    OwnerForget(store).undo('local-owner', receipt['receipt'])
    row = claim(env)
    judgments = service._owner_model_judgments(None)
    # First dispatch returned but the process stopped before committing or finishing.
    judgments.memory_terms('\n'.join(item['rendered'] for item in row['batch']))
    clock[0] += om.CLAIM_STALE_SECONDS+1
    service.owner_model.expire_interrupted(clock[0])
    resumed = claim(env)
    result = service.owner_model.run_terms(resumed, judgments)
    assert json.loads(result[3]['manifest'])['entries'][0]['outcome'] == 'keyed'
    assert parse_meta(raw(store, item)['retrieval_terms_meta'])['g'] == 1
    assert service.owner_model.calls_used(clock[0]) == 2 and len(asked) == 2
    assert row['job_id'] != resumed['job_id']


def test_one_call_cap_and_pause_during_call(env):
    store, service, clock, asked = env
    item = save(store)
    service.owner_model.set_controls({'daily_calls': 1})
    assert service.run_owner_model_upkeep()
    save(store, 1)
    assert not service.run_owner_model_upkeep()
    clock[0] += om.WINDOW_SECONDS+1
    def answer(*args):
        service.owner_model.set_controls({'enabled': False})
        return StructuredDecision(OUTCOME_DECIDED, {'notes': [{'n': 1, 'terms': 'query'}]},
                                  DecisionConfidence(1, sent=True))
    service.use_decision_engine(FixtureDecisionEngine(structured=answer))
    assert service.run_owner_model_upkeep()
    assert json.loads(rows(store)[-1]['observation'])['entries'][0]['outcome'] == 'paused'
