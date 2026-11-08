"""#632 phase 2: stored facts only; model-free migration and worker-route evidence."""
import itertools
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from personal_agent import orchestrator as orch, work_status as ws
from personal_agent.bounded_execution import ExecutionResult
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.subscription_engines import SubscriptionEngines
import test_agency_loop as loop
import test_orchestrator as orchestration_tests
from test_unknown_outcome_routes_663 import UNKNOWN_CALL


class StoredFacts(unittest.TestCase):
    def test_migration_and_fresh_schema(self):
        for legacy in (True, False):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                if legacy:
                    (root/'private').mkdir()
                    with sqlite3.connect(root/'private'/'quickstart.db') as db:
                        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY, request_key TEXT UNIQUE, message TEXT, channel TEXT, chat_id INTEGER, status TEXT, response TEXT, error TEXT, delivery TEXT, provider TEXT, model TEXT, created REAL)')
                        db.execute("INSERT INTO jobs(id,status) VALUES ('old','succeeded')")
                store=QuickStore(root)
                with store.db() as db:
                    columns={row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
                self.assertTrue({'goal_outcome','effect_state'}<=columns)
                job='old' if legacy else store.enqueue('Example request', 'fresh')
                row=store.job(job)
                self.assertEqual((row['goal_outcome'],row['effect_state']),(None,None))
                self.assertFalse(ws.facts_recorded(row['goal_outcome'],row['effect_state']))

    def test_goal_mapping(self):
        class Judgment(str):pass
        for orchestrated,terminal,judgment in itertools.product(
                (False,True),(None,'reached','not_reached','owner_needed','unjudged','not_judged','worker_failed','other'),
                (None,Judgment('yes'),Judgment('no'),Judgment('unavailable'))):
            expected={'yes':'reached','no':'not_reached','unavailable':'not_judged'}.get(judgment,'none')
            if orchestrated and terminal is not None:
                if terminal=='owner_needed':expected='owner_needed'
                elif judgment is None:expected=terminal if terminal in ('reached','not_reached') else 'not_judged'
            with self.subTest(orchestrated=orchestrated,terminal=terminal,judgment=judgment):
                self.assertEqual(ws.goal_outcome_of(terminal,judgment,orchestrated),expected)
        self.assertEqual(ws.GOAL_OUTCOMES,{'answered_by_plan','reached','not_reached','owner_needed','not_judged','none'})
        self.assertEqual((ws.GOAL_REACHED,ws.GOAL_NOT_REACHED,ws.GOAL_OWNER_NEEDED,ws.GOAL_NOT_JUDGED),
                         (orch.REACHED,orch.NOT_REACHED,orch.OWNER_NEEDED,orch.NOT_JUDGED))
        self.assertEqual((orch.UNJUDGED,orch.WORKER_FAILED),('unjudged','worker_failed'))

    def test_effect_precedence_and_recording(self):
        for unknown,withheld,applied in itertools.product((False,True),repeat=3):
            expected='unknown' if unknown else 'withheld' if withheld else 'applied' if applied else 'none'
            self.assertEqual(ws.effect_state_of(unknown,withheld,applied),expected)
        self.assertEqual(ws.EFFECT_STATES,{'none','applied','withheld','unknown'})
        for goal,effect in itertools.product((None,'none'),repeat=2):
            self.assertEqual(ws.facts_recorded(goal,effect),goal is not None and effect is not None)

    def test_run_guard_and_cli_ignores_direct_judgment(self):
        self.assertEqual(AgentService.run_goal_outcome(SimpleNamespace(subscription=None)),'none')
        run=SimpleNamespace(subscription={'id':'cli'},result=SimpleNamespace(judgment='no'),
                            orchestration=SimpleNamespace(orchestrated=True,terminal='reached'))
        self.assertEqual(AgentService.run_goal_outcome(run),'reached')


class DirectFacts(unittest.TestCase):
    service=loop.OwnerEntryPointTests.service

    def run_work(self, calls=(), judgment=None, outcome='succeeded', raises=False):
        service,store=self.service(loop.Script(),loop.Network())
        job=store.enqueue('Example request', 'facts', channel='http')
        def worker(adapter, config, key, messages, sections, capabilities, record, **kwargs):
            for tool,status,detail in calls:record(tool,status,json.dumps(detail))
            if raises:raise ValueError('fixture worker failed')
            return SimpleNamespace(outcome=outcome,content='Example reply',provider=config['provider'],model=config['model'],
                                   incomplete=[],verified=[],report=None,alternatives=[],judgment=judgment)
        with mock.patch('personal_agent.work_executor.run_agent',worker):
            self.assertTrue(service.run_one())
        return store.job(job)

    def test_direct_effects_and_unchanged_status(self):
        cases=[('api_request','failed',UNKNOWN_CALL,'unknown','unknown'),
               ('api_request','succeeded',{'host_action':'api_request','declared_effect':'mutate'},'succeeded','applied'),
               ('settings_change','succeeded',{'host_action':'settings_change','evidence':{
                   'draft_id':'d','applied':False,'requires_owner_confirmation':True}},'partial','withheld'),
               ('list_notes','succeeded',{'host_action':'list_notes'},'succeeded','none')]
        for tool,status,detail,outcome,effect in cases:
            with self.subTest(effect=effect):
                row=self.run_work([(tool,status,detail)],outcome=outcome if outcome!='unknown' else 'succeeded')
                self.assertEqual((row['status'],row['effect_state'],row['goal_outcome']),(outcome,effect,'none'))

    def test_withheld_reads_the_run_that_sets_the_status(self):
        draft={'host_action':'settings_change','evidence':{'draft_id':'d','applied':False,'requires_owner_confirmation':True}}
        service,store=self.service(loop.Script(),loop.Network())
        job=store.enqueue('Example request','earlier-run',channel='http')
        with store.db() as db:
            # An earlier run's withheld draft (recorded before this run was claimed).
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job,'settings_change','succeeded',json.dumps(draft),1.0))
        self.assertEqual(service.work_effect_state(job),'withheld')
        self.assertEqual(service.work_effect_state(job,claimed_at=2.0),'none')
        self.assertEqual(service.work_effect_state(job,claimed_at=0.5),'withheld')

    def test_a_yes_over_an_invalid_call_is_not_reached(self):
        run=SimpleNamespace(subscription=None,orchestration=None,
                            result=SimpleNamespace(judgment='yes',goal_judgment='no'))
        self.assertEqual(AgentService.run_goal_outcome(run),'not_reached')

    def test_direct_judgment(self):
        # #632 phase 3: a run whose own judgment said no or could not judge is never stored succeeded.
        for judgment,expected,status in (('yes','reached','succeeded'),(None,'none','succeeded'),
                                         ('no','not_reached','partial'),('unavailable','not_judged','partial')):
            with self.subTest(judgment=judgment):
                row=self.run_work(judgment=judgment)
                self.assertEqual((row['status'],row['goal_outcome'],row['effect_state']),(status,expected,'none'))

    def test_worker_failure_records_facts(self):
        for unknown in (False,True):
            with self.subTest(unknown=unknown):
                calls=[('api_request','failed',UNKNOWN_CALL)] if unknown else []
                row=self.run_work(calls,raises=True)
                self.assertEqual((row['status'],row['goal_outcome'],row['effect_state']),
                                 ('unknown' if unknown else 'failed','none','unknown' if unknown else 'none'))


class CliFacts(unittest.TestCase):
    def test_cli_unknown_and_clean(self):
        for unknown in (True,False):
            with self.subTest(unknown=unknown), tempfile.TemporaryDirectory() as tmp:
                store=QuickStore(Path(tmp)/'data')
                class Engine:
                    def execute(self,engine,prompt,tools,**kwargs):
                        if unknown:tools.capabilities.record('api_request','failed',json.dumps(UNKNOWN_CALL))
                        return ExecutionResult('Example reply',engine,0)
                service=AgentService(store,subscription_engines=SubscriptionEngines(finder=lambda _: '/runtime/cli',clock=lambda:1),
                                     execution_adapter=Engine())
                service.connect_subscription_engine({'engine':'codex','officially_authenticated':True})
                job=store.enqueue('Example request','cli-facts',channel='http')
                self.assertTrue(service.run_one())
                row=store.job(job)
                self.assertEqual((row['status'],row['goal_outcome'],row['effect_state']),
                                 ('unknown' if unknown else 'succeeded','none','unknown' if unknown else 'none'))


class OrchestratedFacts(orchestration_tests.Harness):
    def test_reached_on_both_routes(self):
        for route in ('openai','codex'):
            with self.subTest(route=route):
                self.use_main(route)
                self.script([orchestration_tests.plan(route,'Answer the request.')],goals=[True])
                _job,row=self.run_work('Example request',key='facts-'+route)
                self.assertEqual((row['status'],row['goal_outcome'],row['effect_state']),('succeeded','reached','none'))


if __name__=='__main__':unittest.main()
