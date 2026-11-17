"""#744 owner-approved paraphrase judgment: only bounded owner words are evidence."""
import json
import pytest

from personal_agent import owner_model as om
from personal_agent.agent_runtime import Capabilities
from personal_agent.decision import BinaryDecision, FixtureDecisionEngine, OUTCOME_DECIDED, fixture_confidence
from personal_agent.quickstart_store import OWNER_FACT_TURNS
from test_744_effects import host
from test_owner_model_dream import fact

REQUEST = '내 회의 시간 선호를 기억해 줘: 오전이 좋아'
VALUE = '오전 회의를 선호합니다'
KEY = 'profile.preference.meeting'


def owner_work(host, *, marked=True):
    store, service, _ = host
    work = store.enqueue(REQUEST, 'owner-paraphrase', owner_typed=True, owner_words=True)
    if marked:
        store.mark_outside_content(work, {'operation': 'public_page_read', 'url': 'https://outside.test/'})
    return work


def judgment(service, outcome):
    seen = []
    def judge(context, proposition):
        seen.append(context)
        if outcome == 'exception':
            raise RuntimeError('judgment unavailable')
        if outcome == 'unavailable':
            return None
        return BinaryDecision(OUTCOME_DECIDED, outcome, fixture_confidence())
    service.use_decision_engine(FixtureDecisionEngine(judge=judge))
    return seen


def save(host, work, route, value=VALUE):
    store, service, _ = host
    if route == 'worker':
        caps = Capabilities(store, None, {}, '', work, lambda *args: None, judgments=service.decision_judge)
        return caps.execute('save_memory', {'memory_key': KEY, 'content': value})
    return service.owner_model._apply_facts([fact(KEY, value, kind='stated')], store.job(work), [],
                                          judgments=service.decision_judge)


@pytest.mark.parametrize('route', ['worker', 'upkeep'])
@pytest.mark.parametrize('outcome', [True, False, None, 'unavailable', 'exception'])
def test_marked_paraphrase_requires_owner_only_judgment_yes(host, route, outcome):
    store, service, _ = host
    work = owner_work(host)
    seen = judgment(service, outcome)
    save(host, work, route)
    assert len(seen) == 1
    assert seen[0].purpose == 'owner-stated-memory'
    assert json.loads(seen[0].facts['owner_words']) == [REQUEST]
    assert json.loads(seen[0].facts['proposed_memory']) == {'key': KEY, 'value': VALUE}
    assert {row['content'] for row in store.memories()} == ({VALUE} if outcome is True else set())
    candidates = store.memory_candidates()
    assert len(candidates) == (0 if outcome is True else 1)
    if candidates:
        assert candidates[0]['source'] == store.outside_content_sources(work)
    else:
        assert store.work_memories('local-owner', work)[0]['content'] == VALUE


@pytest.mark.parametrize('route', ['worker', 'upkeep'])
@pytest.mark.parametrize('marked,literal', [(False, False), (True, True)])
def test_unmarked_or_literal_owner_fact_needs_no_judgment(host, route, marked, literal):
    store, service, _ = host
    work = owner_work(host, marked=marked)
    seen = judgment(service, 'exception')
    value = '오전이 좋아' if literal else VALUE
    save(host, work, route, value)
    assert seen == []
    assert [row['content'] for row in store.memories()] == [value]
    assert store.memory_candidates() == []


def test_owner_words_withdrawn_during_judgment_cannot_authorize_save(host):
    store, service, _ = host
    work = owner_work(host)
    def judge(context, proposition):
        with store.db() as db:
            db.execute('UPDATE jobs SET owner_words=0 WHERE id=?', (work,))
        return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
    service.use_decision_engine(FixtureDecisionEngine(judge=judge))
    save(host, work, 'worker')
    assert store.memories() == []
    assert len(store.memory_candidates()) == 1


def test_judgment_excludes_assistant_tool_diagnostic_forgotten_future_and_old_words(host):
    store, service, _ = host
    store.enqueue('OLD_OUTSIDE_WINDOW', 'old', owner_typed=True, owner_words=True)
    for n in range(OWNER_FACT_TURNS):
        store.enqueue(f'Owner turn {n}', f'prior-{n}', owner_typed=True, owner_words=True)
    store.enqueue('DIAGNOSTIC', 'diagnostic', owner_typed=True)
    store.enqueue('SYSTEM', 'system')
    store.enqueue('OTHER_CONVERSATION', 'other', channel='telegram:x', chat_id=42, owner_typed=True)
    forgotten = store.enqueue('FORGOTTEN', 'forgotten', owner_typed=True, owner_words=True)
    with store.db() as db:
        db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (forgotten,))
        message = db.execute('INSERT INTO messages(job_id,role,content,channel,created) VALUES (?,?,?,?,?)',
                             (forgotten, 'user', 'FORGOTTEN', 'web', 1)).lastrowid
    from personal_agent.owner_forget import OwnerForget
    OwnerForget(store).forget('local-owner', 'message:' + str(message))
    work = owner_work(host)
    store.enqueue('FUTURE', 'future', owner_typed=True, owner_words=True)
    with store.db() as db:
        db.execute("UPDATE jobs SET response='ASSISTANT_POISON' WHERE id=?", (work,))
        db.execute('INSERT INTO messages(job_id,role,content,created) VALUES (?,?,?,?)',
                   (work, 'assistant', 'ASSISTANT_POISON', 1))
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (work, 'read_file', 'succeeded', json.dumps({'text': 'TOOL_POISON'}), 1))
    seen = judgment(service, True)
    save(host, work, 'worker')
    assert len(seen) == 1
    context = seen[0]
    assert set(context.facts) == {'owner_words', 'proposed_memory'}
    words = json.loads(context.facts['owner_words'])
    assert words == [REQUEST] + [f'Owner turn {n}' for n in range(OWNER_FACT_TURNS - 1, 0, -1)]
    assert not any(text in json.dumps(context.facts) for text in
                   ('ASSISTANT_POISON', 'TOOL_POISON', 'DIAGNOSTIC', 'SYSTEM', 'OTHER_CONVERSATION', 'FORGOTTEN', 'FUTURE', 'OLD_OUTSIDE_WINDOW'))


def test_first_use_worker_paths_without_http_transport():
    """Supplement the HTTP journey: same configured service, Telegram ingress and worker."""
    from urllib.parse import parse_qs, urlsplit
    from test_pa1_first_use_acceptance import FirstUseEndToEndAcceptance, BOT_TOKEN, OWNER_PASSWORD, PAGE_URL, MODEL_ANSWER
    from personal_agent.agent_runtime import WORKSPACE_REFERENCE_PREFIX
    case = FirstUseEndToEndAcceptance()
    case.serve = lambda service: 'http://unused.invalid'
    case.setUp()
    try:
        case.store.claim(case.store.bootstrap.read_text(), OWNER_PASSWORD)
        paired = case.service.connect_telegram({'token': BOT_TOKEN})
        code = parse_qs(urlsplit(paired['url']).query)['start'][0]
        case.says(10, '/start ' + code)
        case.service.mark_telegram_connected()
        case.drain()
        draft = {'provider': 'compatible', 'endpoint': 'http://127.0.0.1:11434/v1',
                 'model': 'test-model', 'api_key': 'local-model-key'}
        checked = case.service.test_model(draft)
        case.service.save_model({**draft, 'test_proof': checked['test_proof']}, strict=True)
        case.model_plan = [('web_search', '{"query":"fixture research"}'),
                           ('public_page_read', json.dumps({'url': PAGE_URL}))]
        research = case.says(14, '웹에서 찾아봐')
        case.drain()
        assert {s['operation'] for s in case.store.outside_content_sources(research)} >= {'web_search', 'public_page_read'}
        for update, request, value in (
            (15, REQUEST, VALUE),
            (16, '아니 오후가 좋아. 회의 시간 선호를 저장해 줘', '오후 회의를 선호합니다')):
            case.model_plan = [('save_memory', json.dumps({'memory_key': 'meeting-time', 'content': value}, ensure_ascii=False))]
            work = case.says(update, request)
            case.drain()
            assert case.store.job(work)['status'] == 'succeeded'
            assert case.store.outside_content_sources(work)
            assert any(s['operation'] == 'inherited-context' for s in case.store.outside_content_sources(work))
            assert [m['content'] for m in case.store.memories()] == [value]
            assert not case.store.memory_candidates()
            assert case.store.notification_of(work, case.store.MEMORY_SAVED_NOTICE_KIND) is None  # #955
        # The outside-derived value remains pending until the existing owner acceptance.
        case.model_plan = [('save_memory', json.dumps({'memory_key': 'inferred-preference', 'content': '모델이 추론한 값'}))]
        work = case.says(17, '이건 기억하지 마. 그냥 방금 이야기만 정리해 줘')
        case.drain()
        assert case.store.job(work)['status'] == 'succeeded'
        [candidate] = case.store.memory_candidates()
        assert candidate['source'] == case.store.outside_content_sources(work)
        assert all(m['memory_key'] != 'inferred-preference' for m in case.store.memories())
        [receipt] = [event['trace']['evidence'] for event in case.store.task_events(work)
                     if event['tool'] == 'save_memory' and event['trace'].get('evidence')]
        assert (receipt['state'], receipt['saved'], receipt['auto_saved']) == ('pending', False, False)
        binding = {key: candidate[key] for key in ('work_ref', 'id', 'content_digest')}
        inspected = case.service.memory_candidate_request({**binding, 'operation': 'inspect'})
        assert inspected['content'] == candidate['content']
        approval = case.service.memory_candidate_request({**binding, 'operation': 'request-approval'})
        case.service.memory_candidate_request({**binding, 'operation': 'accept', 'approval_token': approval['approval_token']})
        [saved] = case.store.work_memories('local-owner', work)
        assert saved['content'] == '모델이 추론한 값'
        assert case.store.retract_memory('local-owner', saved['id'], saved['content_digest'])['retracted']
        # J2 has no new gate. Reset the scripted reply exactly as its preceding subtest does.
        case.model_text = MODEL_ANSWER
        configured = case.service.configure_file_workspace({'references': [str(case.reference)], 'workspace': str(case.workspace)})
        root = WORKSPACE_REFERENCE_PREFIX + configured['references'][0]['id']
        case.model_plan = [('read_file', json.dumps({'root_id': root, 'path': 'trip.md'})),
                           ('save_result', json.dumps({'title': '9월 출장 정리', 'content': MODEL_ANSWER}))]
        summary = case.says(18, '“출장 계획” 자료를 요약해 “9월 출장 정리”로 저장해줘')
        case.drain()
        assert MODEL_ANSWER in case.store.job(summary)['response']
        assert MODEL_ANSWER in (case.workspace / '9월 출장 정리.md').read_text()
        assert case.original.read_bytes() == case.original_bytes
        assert case.service._browser_request(summary) is None
    finally:
        case.doCleanups()


from test_owner_model_upkeep import Upkeep, proposal
from personal_agent.decision import StructuredDecision


class TestParaphraseUpkeepBudget(Upkeep):
    def test_pause_during_judgment_prevents_memory_and_candidate_writes(self):
        work = self.finished(REQUEST)
        self.store.mark_outside_content(work, {'operation': 'public_page_read'})
        def judge(context, proposition):
            self.service.owner_model_request({'operation': 'set', 'enabled': False})
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
        self.service.use_decision_engine(FixtureDecisionEngine(judge=judge))
        applied, stopped = self.service.owner_model._apply_facts(
            [proposal(KEY, VALUE, category='preference')], self.store.job(work), [], judgments=self.service.decision_judge)
        assert applied == [] and stopped == om.STOPPED_PAUSED
        assert self.store.memories() == [] and self.store.memory_candidates() == []

    def test_paraphrase_calls_share_the_existing_upkeep_cap(self):
        work = self.finished(REQUEST, answer='ASSISTANT_POISON')
        self.store.mark_outside_content(work, {'operation': 'public_page_read'})
        self.service.owner_model_request({'operation': 'set', 'daily_calls': 2})
        seen = []
        def judge(context, proposition):
            seen.append(context)
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence())
        engine = FixtureDecisionEngine(judge=judge, structured=lambda context, question, schema:
            StructuredDecision(OUTCOME_DECIDED, {'proposals': [
                proposal(KEY, VALUE, category='preference'),
                proposal(KEY + '.second', 'Another paraphrase', category='preference')]}, fixture_confidence()))
        self.service.use_decision_engine(engine)
        assert self.service.run_owner_model_upkeep()
        assert len(seen) == 1
        assert 'ASSISTANT_POISON' not in json.dumps(seen[0].facts)
        assert self.upkeep_rows()[work]['calls'] == 2
        assert [row['content'] for row in self.store.memories()] == [VALUE]
        assert [row['content'] for row in self.store.memory_candidates()] == ['Another paraphrase']
