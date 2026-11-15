"""#887: fixture-only evidence, genuine schemas, injected time, no sockets/models."""
import copy
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import runner as r, scenarios, scoring as s
from agentos_eval.fake_telegram import FakeTelegram
from personal_agent.quickstart_store import QuickStore
from personal_agent.telegram_presence import TelegramTurnAddressing

BASE = 1700000000
SPEC = {'after_turn': 1, 'until': 'delivered', 'timeout_seconds': 50}
SOURCE = {'task_id': 'owner', 'sent_epoch': BASE}


class Clock:
    def __init__(self):
        self.now = 0
        self.jump = 0
    def mono(self): return self.now
    def wall(self): return BASE + self.now + self.jump
    def sleep(self, seconds): self.now += seconds


class Trace:
    """Write actual-shaped receipts at poll boundaries; read via the production eval query."""
    def __init__(self, root, *, quiet=False, notification=False, stop=None, mutate=None):
        self.store = QuickStore(root)
        TelegramTurnAddressing(self.store)
        self.root, self.clock = root, Clock()
        self.telegram = FakeTelegram(42, clock=self.clock.wall)
        self.quiet, self.notification, self.stop, self.mutate = quiet, notification, stop, mutate
        self.done, self.read_timeouts = set(), []
        self.sent = None
        self.task_events = []

    def update(self):
        t = self.clock.now
        with self.store.db() as db:
            if t >= 2 and 'row' not in self.done:
                db.execute("INSERT INTO preparations(id,owner,kind,goal_text,due_at,timezone,channel,state,created_from,created_at,updated_at) VALUES ('p','42','prepare','fixture',?,'UTC','telegram','scheduled','owner',?,?)", (BASE+4, BASE+1, BASE+1))
                self.done.add('row')
            if t >= 4 and self.stop != 'not_started' and 'start' not in self.done:
                db.execute("INSERT INTO jobs(id,request_key,channel,chat_id,status,delivery,created) VALUES ('due',?,'telegram',42,'running','none',?)", (f'preparation:p:{BASE+4}', BASE+4))
                db.execute("UPDATE preparations SET state='running',last_run_job_id='due',run_count=1")
                slot = datetime.fromtimestamp(BASE+4, timezone.utc).isoformat(timespec='minutes')
                for tool, facts in [('scheduled_work_budget', {'preparation_id':'p','slot':slot}), ('preparation', {'preparation_id':'p','slot':slot,'kind':'prepare'})]:
                    db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)', ('due',tool,'admitted' if tool=='scheduled_work_budget' else 'succeeded',json.dumps(facts),BASE+4))
                self.done.add('start')
            if t >= 8 and 'start' in self.done and self.stop != 'not_settled' and 'settled' not in self.done:
                db.execute("UPDATE jobs SET status='succeeded'")
                db.execute("UPDATE preparations SET state='delivered',prepared_at=?,last_decision=?", (BASE+8, 'quiet' if self.quiet else 'notify' if self.notification else None))
                facts = {'preparation_id':'p', 'outcome':'delivered','delivery':'none','notification_queued':self.notification}
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)', ('due','preparation','succeeded',json.dumps(facts),BASE+8))
                if self.notification:
                    db.execute("INSERT INTO telegram_notifications(id,job_id,chat_id,generation,kind,state,created) VALUES ('n','due',42,'test','preparation_notify','queued',?)", (BASE+8,))
                self.done.add('settled')
            if t >= 10 and 'settled' in self.done and not self.quiet and self.stop != 'not_delivered' and 'send' not in self.done:
                self.sent = self.telegram._call('sendMessage', {'chat_id':42,'text':'observed result'})
                mid = self.sent['message_id']
                if self.notification:
                    db.execute("UPDATE telegram_notifications SET state='sent',message_id=?,sent_at=?", (mid,BASE+10))
                else:
                    db.execute("UPDATE jobs SET delivery='sent'")
                    db.execute("INSERT INTO telegram_turns(job_id,chat_id,reply_message_id,created) VALUES ('due',42,?,?)", (mid,BASE+10))
                self.done.add('send')
            if self.mutate:
                self.mutate(db, self)

    def get(self, path, timeout=None):
        assert timeout is not None and 0 < timeout <= 50
        self.read_timeouts.append(timeout)
        self.update()
        if path == '/api/preparations':
            return {'preparations':[{'id':'p','last_result':'API excerpt alone', 'last_delivery':'none','line_account':False}]}
        assert path == '/api/tasks/due'
        # quickstart_service.task_progress: selected contains the detailed task,
        # including public events/failure_class, rather than the envelope.
        with self.store.db() as db:
            status = db.execute("SELECT status FROM jobs WHERE id='due'").fetchone()[0]
        selected = {'id': 'due', 'status': status, 'events': self.task_events}
        if self.stop == 'usage':
            selected['failure_class'] = 'usage-limit'
        return {'tasks': [selected], 'selected': selected, 'unknown_detail_message': 'fixture'}

    def wait(self, **kw):
        return r.wait_due(self, self.telegram, {**SPEC, **kw.pop('spec', {})}, SOURCE, {'owner'}, {}, self.root,
                          clock=self.clock.mono, wall=self.clock.wall, sleep=self.clock.sleep, **kw)


def test_schema_frozen_and_old_bytes():
    added = scenarios.load_file(ROOT/'evals/scenarios/continuity-come-due.json')
    assert [x['split'] for x in added].count('dev') == 2
    assert [x['split'] for x in added].count('heldout') == 1
    for case in added:
        assert len(case['turns']) == 3
        assert case['turns'][1] == {'wait_due':{'after_turn':1,'until':case['turns'][1]['wait_due']['until'],'timeout_seconds':420}}
        with pytest.raises(scenarios.ScenarioError): scenarios.owner_message(case['turns'][1])
    assert len(scenarios.load_file(ROOT/'evals/scenarios/continuity-experiments.json')) == 8
    # A fixed digest guards exact bytes without loading/tuning held-out words.
    assert hashlib.sha256((ROOT/'evals/scenarios/continuity-experiments.json').read_bytes()).hexdigest() == '765b823118d66aac040e1a00f62b4614f78c03320f1e0932534e59f3b3c128d5'


@pytest.mark.parametrize('change', [
    {'after_turn': True}, {'after_turn':0}, {'after_turn':2}, {'after_turn':3},
    {'timeout_seconds':True}, {'timeout_seconds':0}, {'timeout_seconds':601}, {'timeout_seconds':2.5},
    {'until':'sent'}, {'extra':1},
])
def test_schema_bad_wait(change):
    case = {'id':'structural', 'channel':'telegram', 'turns':[{'say':'one'},{'wait_due':{**SPEC,**change}}]}
    with pytest.raises(scenarios.ScenarioError): scenarios.validate(case)


def test_schema_mixed_wrong_channel_world_and_reference():
    for extra in ({'say':'x'},{'note':'x'},{'note_in_message':True},{'from_world':True}):
        with pytest.raises(scenarios.ScenarioError):
            scenarios.validate({'id':'s','channel':'telegram','turns':[{'say':'a'},{'wait_due':SPEC,**extra}]})
    for channel in ('web','other'):
        with pytest.raises(scenarios.ScenarioError): scenarios.validate({'id':'s','channel':channel,'turns':['a',{'wait_due':SPEC}]})
    with pytest.raises(scenarios.ScenarioError):
        scenarios.validate({'id':'s','channel':'telegram','turns':['a',{'wait_due':SPEC},{'wait_due':{**SPEC,'after_turn':2}}]})
    with pytest.raises(scenarios.ScenarioError):
        scenarios.validate({'id':'s','world':{'name':'x','task_id':'x','task_set':'dev','approvals':{}},'turns':[{'wait_due':SPEC}]})
    with pytest.raises(scenarios.ScenarioError):
        scenarios.validate({'id':'s','channel':'telegram','turns':['a',{'wait_due':{'after_turn':1}}]})


@pytest.mark.parametrize('notification', [False,True])
def test_real_db_delivery_joins_and_full_drain(tmp_path, notification):
    trace = Trace(tmp_path, notification=notification)
    receipt = trace.wait()
    assert receipt['completed'] and receipt['elapsed'] == 30
    assert receipt['due_slot'] == datetime.fromtimestamp(BASE+4, timezone.utc).isoformat(timespec='minutes')
    assert receipt['due_epoch'] == BASE+4  # event ISO is minute precision; key preserves seconds
    assert receipt['start']['created'] == BASE+4
    assert receipt['settlement']['created'] == BASE+8
    assert receipt['deliveries'][0]['at'] == BASE+10
    assert receipt['deliveries'][0]['message_id'] == trace.sent['message_id']
    assert receipt['api_excerpt']['line_account'] is False
    assert receipt['end_epoch'] == BASE+30
    if notification:
        with trace.store.db() as db:
            assert db.execute('SELECT delivery FROM jobs').fetchone()[0] == 'none'
    observed = s.proactive_observation({'turns':[SOURCE], 'due_observations':[receipt]})
    assert observed['observed'] and observed['outputs'][0]['text'] == 'observed result'


def test_quiet_is_completed_but_ungraded_and_keeps_noise(tmp_path):
    trace = Trace(tmp_path, quiet=True)
    trace.telegram._call('sendMessage',{'text':'unexpected ping','chat_id':42})
    receipt = trace.wait(spec={'until':'settled'})
    assert receipt['completed'] and receipt['reason'] == 'quiet' and receipt['elapsed'] == 28
    assert receipt['messages'][0]['text'] == 'unexpected ping'
    assert not s.proactive_observation({'due_observations':[receipt]})['observed']


@pytest.mark.parametrize('stage', ['no_preparation','not_started','not_settled','not_delivered','drain_incomplete'])
def test_every_timeout_stage(tmp_path, stage):
    trace = Trace(tmp_path, stop=stage)
    if stage == 'no_preparation':
        trace.update = lambda: None
    receipt = trace.wait(spec={'timeout_seconds':15 if stage == 'drain_incomplete' else 30})
    assert receipt['timed_out'] and receipt['stage'] == stage and not receipt['completed']
    assert receipt['elapsed'] == (15 if stage == 'drain_incomplete' else 30)
    if stage == 'drain_incomplete': assert receipt['deliveries']


@pytest.mark.parametrize('fault', ['old','wrong_source','wrong_work','owner_work','wrong_slot','wrong_message','no_admission','run_now','queued','unknown','empty','only_api','failed','pending_resume'])
def test_false_provenance_never_completes(tmp_path, fault):
    def mutate(db, trace):
        if fault == 'old': db.execute('UPDATE preparations SET created_at=?',(BASE-1,))
        if fault == 'wrong_source': db.execute("UPDATE preparations SET created_from='other'")
        if fault == 'wrong_work': db.execute("UPDATE tool_events SET job_id='wrong'")
        if fault == 'owner_work': db.execute("UPDATE preparations SET last_run_job_id='owner'")
        if fault == 'wrong_slot': db.execute("UPDATE jobs SET request_key='preparation:p:1700000010'")
        if fault in ('no_admission','run_now'): db.execute("DELETE FROM tool_events WHERE tool='scheduled_work_budget'")
        if fault == 'wrong_message': db.execute('UPDATE telegram_turns SET reply_message_id=999')
        if fault in ('queued','unknown'): db.execute('UPDATE telegram_notifications SET state=?',(fault,))
        if fault == 'only_api': db.execute('DELETE FROM telegram_turns')
        if fault == 'empty' and trace.telegram.calls: trace.telegram.calls[-1]['result']['text']=''
        if fault == 'failed': db.execute("UPDATE jobs SET status='failed'")
        if fault == 'pending_resume': db.execute("DELETE FROM tool_events WHERE detail LIKE '%outcome%'")
    trace = Trace(tmp_path, notification=fault in ('queued','unknown'), mutate=mutate)
    receipt = trace.wait(spec={'timeout_seconds':35})
    assert not receipt['completed']
    assert not s.proactive_observation({'turns':[SOURCE], 'due_observations':[receipt]})['observed']
    if fault == 'failed': assert receipt['reason'] == 'execution_failed'


def test_ambiguous_outside_horizon_and_budget_refusal(tmp_path):
    trace = Trace(tmp_path)
    trace.clock.now=2
    trace.update()
    with trace.store.db() as db:
        db.execute('UPDATE preparations SET due_at=?',(BASE+900,))
    trace.update=lambda: None
    receipt=trace.wait(spec={'timeout_seconds':10})
    assert receipt['reason']=='out_of_horizon'
    with trace.store.db() as db:
        db.execute('UPDATE preparations SET due_at=?',(BASE+4,))
        db.execute("INSERT INTO preparations(id,owner,kind,goal_text,due_at,timezone,channel,state,created_from,created_at,updated_at) SELECT 'p2',owner,kind,goal_text,due_at,timezone,channel,state,created_from,created_at,updated_at FROM preparations")
    assert trace.wait()['reason']=='ambiguous_preparation'
    with trace.store.db() as db:
        db.execute("DELETE FROM preparations WHERE id='p2'")
        db.execute("INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES ('slot','scheduled_work_budget','skipped',?,?)",(json.dumps({'preparation_id':'p'}),BASE+2))
    assert trace.wait()['reason']=='budget_refusal'


@pytest.mark.parametrize('jump', [-9000,9000])
def test_monotonic_cap_despite_wall_jump(tmp_path, jump):
    trace=Trace(tmp_path, stop='not_started')
    def reader(*a,**kw):
        trace.clock.jump=jump
        return r.due_evidence(*a,**kw)
    receipt=trace.wait(reader=reader,spec={'timeout_seconds':11})
    assert receipt['elapsed']==11 and receipt['timed_out']


@pytest.mark.parametrize('boundary', ['http','sqlite'])
@pytest.mark.parametrize('exhausted', [False, True])
def test_blocked_reader_remaining_budget_and_infra(tmp_path,boundary,exhausted):
    trace=Trace(tmp_path)
    def blocked(*a,timeout,**kw):
        trace.clock.sleep(timeout if exhausted else 1)
        error = sqlite3.OperationalError if boundary == 'sqlite' else TimeoutError
        raise error('blocked read')
    kw={'reader':blocked} if boundary=='sqlite' else {}
    if boundary=='http': trace.get=blocked
    receipt=trace.wait(spec={'timeout_seconds':9},**kw)
    assert receipt['elapsed'] == (9 if exhausted else 1)
    assert receipt['status'] == ('timed_out' if exhausted else 'observation_error')
    assert receipt['timed_out'] is exhausted
    if exhausted: assert receipt['stage'] == 'no_preparation'


def test_usage_limit_stops_and_is_visible(tmp_path):
    trace=Trace(tmp_path,stop='usage')
    receipt=trace.wait()
    assert receipt['reason']=='usage-limit' and not receipt['completed']
    assert r.usage_limited({'turns':[{'kind':'wait_due','failure_class':receipt['failure_class']}]})


def test_delivery_before_entry_and_between_polls(tmp_path):
    trace=Trace(tmp_path)
    trace.clock.now=12
    trace.update()
    receipt=trace.wait()
    assert receipt['completed'] and receipt['elapsed']==20
    # All reads are new transactions; delivery did not need to be seen in a particular poll.
    assert len(receipt['deliveries'])==1 and receipt['start_epoch']==BASE+12


def test_reader_really_read_only_and_no_payload_dump(tmp_path):
    trace=Trace(tmp_path)
    trace.clock.now=12
    trace.update()
    before=trace.store.path.read_bytes()
    evidence=r.due_evidence(tmp_path,'owner',BASE,timeout=1)
    assert trace.store.path.read_bytes()==before
    assert set(evidence['jobs'][0])=={'id','request_key','created','status','delivery','channel','provider','model'}
    assert all('detail' not in e for e in evidence['events'])


@pytest.mark.parametrize('fault', ['wrong_preparation','wrong_chat','old_work','no_settlement','sending','awaiting_owner'])
def test_other_join_boundaries(tmp_path,fault):
    def mutate(db,trace):
        if fault=='wrong_preparation':
            rows=db.execute('SELECT id,detail FROM tool_events').fetchall()
            for row in rows:
                facts=json.loads(row['detail']);facts['preparation_id']='other'
                db.execute('UPDATE tool_events SET detail=? WHERE id=?',(json.dumps(facts),row['id']))
        if fault=='wrong_chat':db.execute('UPDATE telegram_turns SET chat_id=99')
        if fault=='old_work':db.execute('UPDATE jobs SET created=?',(BASE-1,))
        if fault=='no_settlement':db.execute("DELETE FROM tool_events WHERE detail LIKE '%outcome%'")
        if fault=='sending':db.execute("UPDATE jobs SET delivery='sending'")
        if fault=='awaiting_owner':db.execute("UPDATE jobs SET status='awaiting_context'")
    trace=Trace(tmp_path,mutate=mutate)
    result=trace.wait(spec={'timeout_seconds':35})
    assert not result['completed']


def test_old_id_excluded_far_future_extra_ignored_and_advanced_slot_kept(tmp_path):
    trace=Trace(tmp_path)
    trace.clock.now=12
    trace.update()
    # A future incidental preparation is recorded but cannot make this ambiguous.
    with trace.store.db() as db:
        db.execute("INSERT INTO preparations(id,owner,kind,goal_text,due_at,timezone,channel,state,created_from,created_at,updated_at) SELECT 'future',owner,kind,goal_text,?,timezone,channel,'scheduled',created_from,created_at,updated_at FROM preparations WHERE id='p'",(BASE+9999,))
    trace.update=lambda:None
    assert trace.wait()['completed']
    old={'preparations':{'preparations':[{'id':'p'}]}}
    result=r.wait_due(trace,trace.telegram,SPEC,SOURCE,{'owner'},old,tmp_path,clock=trace.clock.mono,
                      wall=trace.clock.wall,sleep=trace.clock.sleep)
    assert not result['completed']
    # A recurring row can have advanced past its observed slot; the typed
    # settlement's next_due, start/admission and exact Work key still join.
    following=BASE+3604
    with trace.store.db() as db:
        db.execute("UPDATE preparations SET due_at=? WHERE id='p'",(following,))
        row=db.execute("SELECT id,detail FROM tool_events WHERE detail LIKE '%outcome%'").fetchone()
        facts=json.loads(row['detail']);facts['next_due']=datetime.fromtimestamp(following,timezone.utc).isoformat(timespec='minutes')
        db.execute('UPDATE tool_events SET detail=? WHERE id=?',(json.dumps(facts),row['id']))
    result=trace.wait()
    assert result['completed'] and result['due_epoch']==BASE+4 and result['row_due_epoch']==following


def test_multipart_unknown_text_not_promoted_and_edits_keep_observed_versions(tmp_path):
    trace=Trace(tmp_path)
    trace.clock.now=12
    trace.update()
    trace.telegram._call('sendMessage',{'chat_id':42,'text':'unlinked second body'})
    trace.telegram._call('editMessageText',{'chat_id':42,'message_id':trace.sent['message_id'],'text':'edited result'})
    trace.update=lambda:None
    result=trace.wait()
    assert [m['text'] for m in result['messages']]==['observed result','unlinked second body','edited result']
    assert [m['text'] for m in result['deliveries']]==['observed result','edited result']
    assert 'multipart' in result['coverage_limit']
    observed=s.proactive_observation({'due_observations':[result]})
    assert len(observed['outputs'])==2
    result['deliveries'][0]['text']='x'*(s.ANSWER_CHARS+1)
    assert s.proactive_observation({'due_observations':[result]})['outputs'][0]['truncated']


def test_drain_can_finish_exactly_at_cap(tmp_path):
    result=Trace(tmp_path).wait(spec={'timeout_seconds':30})
    assert result['completed'] and result['elapsed']==30


def test_wait_collector_retains_message_after_backward_clock_jump(tmp_path):
    trace=Trace(tmp_path,stop='not_started')
    def reader(*a,**kw):
        if not trace.telegram.calls:
            trace.clock.jump=-9000
            trace.telegram._call('sendMessage',{'chat_id':42,'text':'retained despite clock jump'})
        return r.due_evidence(*a,**kw)
    result=trace.wait(reader=reader,capture_sequence=0,spec={'timeout_seconds':11})
    assert result['timed_out'] and result['elapsed']==11
    assert result['messages'][0]['text']=='retained despite clock jump'


def test_selected_task_progress_retains_usage_limit_and_approval_events(tmp_path):
    trace = Trace(tmp_path)
    trace.task_events = [{'tool': 'local_authority', 'status': 'requested', 'label': 'fixture gate'}]
    receipt = trace.wait()
    assert receipt['work_events'] == trace.task_events
    record = {'turns': [SOURCE, {'kind': 'wait_due', 'events': receipt['work_events']}]}
    approvals = s.required_approvals_events({'expect': {}}, record)
    assert len(approvals) == 1 and approvals[0]['turn'] == 2
    limited = Trace(tmp_path / 'limited', stop='usage')
    receipt = limited.wait()
    assert receipt['failure_class'] == 'usage-limit' and receipt['elapsed'] == 4


@pytest.mark.parametrize('status', ['interrupted', 'unknown', 'blocked'])
def test_runtime_terminal_failure_settles_without_timeout(tmp_path, status):
    def mutate(db, trace):
        if 'settled' in trace.done:
            db.execute('UPDATE jobs SET status=?', (status,))
            db.execute("UPDATE preparations SET state='failed',last_outcome='failed'")
            event = db.execute("SELECT id,detail FROM tool_events WHERE detail LIKE '%outcome%'").fetchone()
            facts = json.loads(event['detail'])
            facts['outcome'] = 'failed'
            db.execute("UPDATE tool_events SET status='failed',detail=? WHERE id=?", (json.dumps(facts),event['id']))
    receipt = Trace(tmp_path, mutate=mutate).wait()
    assert receipt['status'] == 'failed' and receipt['reason'] == 'execution_failed'
    assert receipt['result_status'] == status and receipt['settlement']
    assert not receipt['timed_out'] and receipt['elapsed'] == 8


@pytest.mark.parametrize('until', ['settled', 'delivered'])
def test_one_off_keep_result_is_quiet_only_for_until_settled(tmp_path, until):
    def keep(db, trace):
        db.execute("UPDATE preparations SET channel='web',recurrence=NULL,last_decision=NULL")
        db.execute("UPDATE jobs SET channel='web',chat_id=NULL,delivery='none'")
    trace = Trace(tmp_path, quiet=True, mutate=keep)
    receipt = trace.wait(spec={'until': until})
    assert receipt['completed'] is (until == 'settled')
    if until == 'settled':
        assert receipt['reason'] == 'quiet' and receipt['elapsed'] == 28
        assert not s.proactive_observation({'due_observations': [receipt]})['observed']
    else:
        assert receipt['timed_out'] and receipt['stage'] == 'not_delivered'


def test_mid_task_read_exhaustion_keeps_last_unmet_stage(tmp_path):
    trace = Trace(tmp_path)
    original = trace.get
    def exhaust_task(path, timeout=None):
        if path.startswith('/api/tasks/'):
            trace.clock.sleep(timeout)
            raise TimeoutError('task detail reached deadline')
        return original(path, timeout=timeout)
    trace.get = exhaust_task
    receipt = trace.wait(spec={'timeout_seconds': 11})
    assert receipt['status'] == 'timed_out' and receipt['timed_out']
    assert receipt['stage'] == 'not_settled' and receipt['elapsed'] == 11
    assert receipt['start'] and not receipt['completed']


def test_terminal_status_mirror_matches_runtime():
    from personal_agent.work_status import TERMINAL
    assert r.TERMINAL == TERMINAL


def test_eval_entry_imports_without_still_package(tmp_path):
    """The eval-only venv must not need Still installed, even during imports."""
    import subprocess
    script = '''
import importlib.abc
import sys

sys.path.insert(0, sys.argv[1])
blocked = []
class BlockStill(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'personal_agent' or fullname.startswith('personal_agent.'):
            blocked.append(fullname)
            raise ModuleNotFoundError('Still is not installed in the eval venv', name=fullname)

sys.meta_path.insert(0, BlockStill())
import agentos_eval.runner
import agentos_eval.scoring
import agentos_eval.continuity
import agentos_eval.cli
assert blocked == [], blocked
assert not any(name == 'personal_agent' or name.startswith('personal_agent.') for name in sys.modules)
'''
    # -I ignores inherited PYTHONPATH/user site; -S excludes installed packages
    # and editable-install .pth files. Only the eval package is added back.
    result = subprocess.run([sys.executable, '-I', '-S', '-c', script, str(ROOT / 'evals')],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


class ReminderTrace:
    """Real Preparations.start/settle and reply linkage, with injected time/Telegram."""
    def __init__(self, root, *, fault=None, lead=4, recurrence=None, late=False):
        from personal_agent.preparations import Preparations
        self.root, self.clock, self.fault = root, Clock(), fault
        self.clock.now = 1
        self.store = QuickStore(root)
        self.addresses = TelegramTurnAddressing(self.store)
        self.preparations = Preparations(self.store, clock=self.clock.wall)
        self.row = self.preparations.create(kind='reminder', goal='a synthetic reminder body',
            due_at=BASE+lead, timezone='UTC', recurrence=recurrence, channel='telegram',
            created_from='owner', state='scheduled', accepted_by='owner-request')
        self.telegram = FakeTelegram(42, clock=self.clock.wall)
        # The smoke's delivered reminder had message id 6. These are stand-in
        # owner message ids, not fabricated delivered result texts.
        for _ in range(5): self.telegram.say('synthetic setup')
        self.work_id = None
        self.finished = False
        self.due = BASE+lead
        self.owner_ids = {'owner'}
        self.clock.now = lead+4 if late else 2

    def update(self):
        if self.work_id is None and self.clock.wall() >= (BASE+2 if self.fault == 'run_now_early' else self.due):
            if self.fault in ('run_now_early', 'run_now_at_due'):
                self.work_id = self.preparations.run_now(self.row['id'], channel='telegram:eval', chat_id=42,
                                                        now=self.clock.wall())
            else:
                self.work_id = self.preparations.start(self.preparations.get(self.row['id']),
                    channel='telegram:eval', chat_id=42, now=self.clock.wall())
        if self.work_id and not self.finished and self.clock.wall() >= self.due+2:
            result = self.telegram._call('sendMessage', {'chat_id':42, 'text':'the observed reminder body'})
            with self.store.db() as db:
                db.execute("UPDATE jobs SET delivery='sent' WHERE id=?", (self.work_id,))
                self.addresses.record_reply(self.work_id,42,result['message_id'],db=db)
            self.preparations.settle(self.preparations.get(self.row['id']),self.clock.wall())
            self.finished = True

    def get(self, path, timeout=None):
        assert 0 < timeout <= 420
        self.update()
        row = self.preparations.get(self.row['id'])
        if path == '/api/preparations':
            return {'preparations':[{'id':row['id'],'kind':row['kind'],'state':row['state'],
                    'last_result':'the observed reminder body','line_account':False,
                    'last_delivery':'sent' if self.finished else None,'last_work_id':self.work_id}]}
        assert path == '/api/tasks/' + self.work_id
        selected = {'id':self.work_id,'status':'succeeded','events':[]}
        return {'tasks':[selected],'selected':selected}

    def read(self, *args, **kwargs):
        with self.store.db() as db:
            if self.fault == 'old_row': db.execute('UPDATE preparations SET created_at=?',(BASE-1,))
            if self.fault == 'wrong_source': db.execute("UPDATE preparations SET created_from='other'")
            if self.fault == 'unsupported_kind': db.execute("UPDATE preparations SET kind='future-unrecognized-kind'")
            if self.work_id:
                if self.fault == 'owner_work': self.owner_ids.add(self.work_id)
                if self.fault == 'wrong_work': db.execute("UPDATE tool_events SET job_id='wrong'")
                if self.fault == 'wrong_reply_work': db.execute("UPDATE telegram_turns SET job_id='wrong'")
                if self.fault == 'wrong_message': db.execute('UPDATE telegram_turns SET reply_message_id=999')
                if self.fault == 'wrong_chat': db.execute('UPDATE telegram_turns SET chat_id=99')
                if self.fault == 'missing_reply': db.execute('DELETE FROM telegram_turns')
                if self.fault == 'wrong_provider': db.execute("UPDATE jobs SET provider='subscription-engine'")
                if self.fault == 'wrong_model': db.execute("UPDATE jobs SET model='other'")
                if self.fault == 'wrong_key': db.execute('UPDATE jobs SET request_key=?',(f'preparation:{self.row["id"]}:{int(self.due)+1}',))
                if self.fault in ('wrong_slot','wrong_preparation','missing_settlement','bad_settlement_delivery'):
                    for event in db.execute('SELECT id,detail FROM tool_events').fetchall():
                        facts = json.loads(event['detail'])
                        if self.fault == 'wrong_slot' and 'slot' in facts:
                            facts['slot'] = datetime.fromtimestamp(self.due+60,timezone.utc).isoformat(timespec='minutes')
                        if self.fault == 'wrong_preparation': facts['preparation_id'] = 'other'
                        if self.fault == 'missing_settlement' and 'outcome' in facts:
                            db.execute('DELETE FROM tool_events WHERE id=?',(event['id'],))
                            continue
                        if self.fault == 'bad_settlement_delivery' and 'outcome' in facts: facts['delivery'] = 'none'
                        db.execute('UPDATE tool_events SET detail=? WHERE id=?',(json.dumps(facts),event['id']))
                if self.fault == 'empty_text' and self.telegram.calls:
                    self.telegram.calls[-1]['result']['text'] = ''
        return r.due_evidence(*args, **kwargs)

    def wait(self, *, timeout=40, before=None):
        return r.wait_due(self,self.telegram,{**SPEC,'timeout_seconds':timeout},SOURCE,self.owner_ids,
                         before or {},self.root,clock=self.clock.mono,wall=self.clock.wall,
                         sleep=self.clock.sleep,reader=self.read)


def test_builtin_reminder_smoke_trace_is_observed_by_id(tmp_path):
    trace = ReminderTrace(tmp_path, lead=180)
    result = trace.wait(timeout=420)
    assert result['completed'] and result['origin'] == 'scheduled_reminder'
    assert result['admission'] is None
    assert result['preparation_kind'] == 'reminder'
    assert result['due_slot'] == result['start']['facts']['slot']
    assert result['settlement']['facts']['outcome'] == 'delivered'
    assert result['settlement']['facts']['delivery'] == 'sent'
    assert result['deliveries'][0]['message_id'] == 6
    assert result['deliveries'][0]['kind'] == 'work_reply'
    assert trace.clock.now == 202  # delivered at 182, followed by the full 20-second drain
    receipts = result['delivery_receipts']
    assert receipts['work']['provider'] == 'builtin' and receipts['work']['model'] == 'preparation'
    assert receipts['replies'][0]['job_id'] == result['work_id']
    assert receipts['replies'][0]['reply_message_id'] == 6
    assert receipts['notifications'] == []  # builtin uses deliver_one, not preparation_notify
    assert not any(e['tool']=='scheduled_work_budget' for e in result['events'])
    observed = s.proactive_observation({'turns':[SOURCE],'due_observations':[result]})
    assert observed['observed'] and observed['outputs'][0]['kind'] == 'scheduled_reminder'
    assert observed['outputs'][0]['text'] == 'the observed reminder body'
    assert observed['outputs'][0]['message_ref'] == result['deliveries'][0]['delivery_id']


@pytest.mark.parametrize('fault', ['run_now_early','run_now_at_due','old_row','wrong_source','wrong_slot',
    'wrong_key','wrong_work','owner_work','wrong_preparation','missing_reply','wrong_reply_work','wrong_message',
    'wrong_chat','missing_settlement','bad_settlement_delivery','wrong_provider','wrong_model','empty_text'])
def test_builtin_reminder_rejects_false_origin_or_delivery(tmp_path, fault):
    trace = ReminderTrace(tmp_path,fault=fault)
    result = trace.wait()
    assert not result['completed']
    assert not s.proactive_observation({'turns':[SOURCE],'due_observations':[result]})['observed']
    if fault.startswith('run_now'):
        assert trace.finished  # a real run-now delivered; it is still not scheduled-origin evidence
        assert trace.preparations.get(trace.row['id'])['state'] == 'scheduled'
    if fault in ('missing_reply','wrong_message','wrong_chat','wrong_reply_work'):
        assert result['stage'] == 'not_delivered'
        assert result['messages']  # preserve observed interruptions even with no linked result


def test_reminder_already_settled_between_polls_and_old_id_exclusion(tmp_path):
    trace = ReminderTrace(tmp_path,late=True)
    result = trace.wait()
    assert result['completed'] and result['elapsed'] == 20
    # A pre-existing preparation is never promoted by a new reply receipt.
    trace = ReminderTrace(tmp_path/'old',late=True)
    before = {'preparations':{'preparations':[{'id':trace.row['id']}]}}
    assert not trace.wait(before=before)['completed']


def test_unsupported_kind_ends_immediately_but_missing_admission_does_not(tmp_path):
    trace = ReminderTrace(tmp_path,fault='unsupported_kind')
    result = trace.wait()
    assert result['status'] == 'unsupported' and result['reason'] == 'unsupported_kind'
    assert result['elapsed'] == 0 and not result['timed_out']
    supported = Trace(tmp_path/'supported', mutate=lambda db,t: db.execute("DELETE FROM tool_events WHERE tool='scheduled_work_budget'"))
    result = supported.wait(spec={'timeout_seconds':11})
    assert result['timed_out'] and result['elapsed'] == 11


def test_reminder_recurring_slot_needs_observed_running_origin(tmp_path):
    trace = ReminderTrace(tmp_path,recurrence='daily')
    result = trace.wait()
    assert result['completed'] and result['reminder_origin']['preparation']['state'] == 'running'
    # Advanced state alone cannot distinguish a missed run-now from a scheduled run.
    trace = ReminderTrace(tmp_path/'unobserved',recurrence='daily',late=True)
    assert not trace.wait()['completed']


def test_run_now_reused_at_scheduled_slot_does_not_promote_its_prior_reply(tmp_path):
    trace = ReminderTrace(tmp_path,fault='run_now_at_due')
    trace.clock.now = 4
    trace.update()
    trace.clock.now = 6
    trace.update()  # immediate run settles back to scheduled
    assert trace.preparations.get(trace.row['id'])['state'] == 'scheduled'
    # The runtime may reuse the same idempotency key at a later scheduler tick.
    # Its repeated start is ambiguous; the earlier run-now send is not proof.
    work = trace.preparations.start(trace.preparations.get(trace.row['id']),
                                   channel='telegram:eval',chat_id=42,now=trace.clock.wall())
    assert work == trace.work_id
    trace.preparations.settle(trace.preparations.get(trace.row['id']),trace.clock.wall())
    assert trace.preparations.get(trace.row['id'])['state'] == 'delivered'
    result = trace.wait()
    assert not result['completed']
    assert not s.proactive_observation({'turns':[SOURCE],'due_observations':[result]})['observed']
