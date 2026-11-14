"""#632 phase 3: model-free status truth table and durable worker closeout evidence."""
import itertools
import json
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from personal_agent import work_status as ws
from personal_agent.messages import msg
from personal_agent.quickstart_service import AgentService
from personal_agent.work_execution import WorkRun
import test_agency_loop as loop
import test_orchestrator as orch
from test_recovered_steps import failed, succeeded
from test_unknown_outcome_routes_663 import UNKNOWN_CALL


class Derivation(unittest.TestCase):
    def test_truth_table(self):
        # Columns: succeeded / partial / failed / unknown steps. Each row is a policy case.
        table={
            'reached_clean': ('succeeded','succeeded','succeeded','unknown'),
            'reached_held': ('partial','partial','failed','unknown'),
            'owner_needed': ('partial','partial','failed','unknown'),
            'none_clear': ('succeeded','partial','failed','unknown'),
            'none_held': ('partial','partial','failed','unknown'),
            'unknown': ('unknown','unknown','unknown','unknown'),
        }
        steps_values=('succeeded','partial','failed','unknown')
        for goal,effect,steps,gate,login,shortfall in itertools.product(
                (*sorted(ws.GOAL_OUTCOMES),None),sorted(ws.EFFECT_STATES),steps_values,
                (False,True),(False,True),('partial','failed')):
            cases={
                'reached': 'reached_clean' if effect in ('none','applied') and not gate else 'reached_held',
                'answered_by_plan': 'none_clear' if effect=='none' and not gate and not login else 'none_held',
                'owner_needed': 'owner_needed',
                'none': 'none_held' if effect=='withheld' or gate or login else 'none_clear',
                None: 'none_held' if effect=='withheld' or gate or login else 'none_clear',
            }
            row=(shortfall,'partial','failed','unknown') if goal in ('not_reached','not_judged') else table[cases[goal]]
            if effect=='unknown':row=table['unknown']
            with self.subTest(goal=goal,effect=effect,steps=steps,gate=gate,login=login,shortfall=shortfall):
                actual=ws.derive_status(goal,effect,steps,shortfall=shortfall,owner_gate=gate,login_pending=login)
                self.assertEqual(actual,row[steps_values.index(steps)])
                if actual=='succeeded':
                    self.assertIn(effect,('none','applied'))
                    self.assertNotEqual(steps,'unknown')
                    self.assertFalse(gate)

    def test_fact_predicates_and_legacy_request(self):
        for goal in (*ws.GOAL_OUTCOMES,None):
            self.assertEqual(ws.goal_reached(goal),goal=='reached')
            for status in ('succeeded','partial','failed','unknown',None):
                self.assertEqual(ws.request_met(status,goal),status=='succeeded' and goal in ('reached','answered_by_plan','none',None))
        for effect in (*ws.EFFECT_STATES,None):
            self.assertEqual(ws.effect_floor_clean(effect),effect in ('none','applied'))


class WorkerCloseout(unittest.TestCase):
    service=loop.OwnerEntryPointTests.service

    def close(self, route, *, goal='reached', steps='succeeded', rows=(), pending=None, gate=False, waits=False, old_login=False):
        service,store=self.service(loop.Script(),loop.Network())
        job=store.enqueue('Example request','derived',channel='http')
        run=WorkRun(store.job(job),time.time())
        run.prompt='Example request';run.response='Example reply'
        run.subscription={'id':'fixture'} if route=='cli' else {}
        run.turn_provenance=set();run.context_sources=[]
        run.outcome=steps;run.resolved_blocker=True
        run.context_approval_needed[0]=gate=='context'
        run.waits_for_sign_in=waits
        run.orchestration=SimpleNamespace(orchestrated=True,terminal=goal,terminal_gap='Missing detail',
                                          terminal_note=None,terminal_code=None,notice=None)
        run.result=SimpleNamespace(judgment={'reached':'yes','not_reached':'no','not_judged':'unavailable'}.get(goal))
        if goal=='none':run.orchestration=None
        with store.db() as db:
            if old_login:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job,'browser_sign_in','succeeded',json.dumps({'evidence':{'state':'login_required'}}),run.claimed_at-10))
            for tool,status,detail in rows:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (job,tool,status,detail,run.claimed_at+0.01))
        if pending=='approval':
            service.store.put('browser_step_requests',{job:{'state':'requested'}})
        elif pending=='login':service._put_browser_login(job,{'state':'requested','explicit':True})
        facts=(service.run_goal_outcome(run),service.work_effect_state(job,run.claimed_at))
        with mock.patch.object(service,'_settle_procedures',wraps=service._settle_procedures) as procedures:
            with mock.patch.object(ws,'derive_status',wraps=ws.derive_status) as derive:
                service._settle_worker_outcome(run)
            self.assertEqual(derive.call_args.args[:2],facts)
            self.assertEqual((run.goal_outcome,run.effect_state),facts)
            # Finishing must persist the inputs used above, even if a later read would differ.
            with mock.patch.object(service,'run_goal_outcome',side_effect=AssertionError('facts recomputed')), \
                    mock.patch.object(service,'work_effect_state',side_effect=AssertionError('facts recomputed')):
                service._finish_work(run)
        row=store.job(job)
        self.assertEqual((row['goal_outcome'],row['effect_state']),facts)
        self.assertEqual(procedures.call_args.args[2],row['status']=='succeeded' and row['goal_outcome']=='reached')
        return service,store,run,row

    def test_reached_never_overrules_own_effect_records(self):
        cases={
            'draft':([succeeded('settings_change',{'draft_id':'d','applied':False,'requires_owner_confirmation':True})],None,'withheld'),
            'failed write':([failed('browser_type',declared='mutate',ran=True)],None,'withheld'),
            'approval':([], 'approval','withheld'),
            'sign-in':([succeeded('browser_sign_in',{'state':'login_required'})],None,'withheld'),
            'unknown':([('api_request','failed',json.dumps(UNKNOWN_CALL))],None,'unknown'),
        }
        for route,(kind,(rows,pending,effect)) in itertools.product(('cli','direct'),cases.items()):
            with self.subTest(route=route,kind=kind):
                _service,_store,run,row=self.close(route,rows=rows,pending=pending)
                self.assertEqual(row['effect_state'],effect)
                self.assertEqual(row['status'],'unknown' if effect=='unknown' else 'partial')
                self.assertFalse(run.resolved_blocker)

    def test_pending_login_and_owner_gate_floor(self):
        for route,goal in itertools.product(('cli','direct'),('none','reached')):
            with self.subTest(route=route,goal=goal):
                *_,row=self.close(route,goal=goal,pending='login')
                self.assertEqual((row['status'],row['effect_state']),('partial','withheld'))
                for gate in ('context',):
                    *_,row=self.close(route,goal=goal,gate=gate)
                    self.assertEqual(row['status'],'partial')
        *_,run,row=self.close('direct',goal='none',waits=True)
        self.assertEqual(row['status'],'partial')
        self.assertIn(('browser_open',msg('agent_runtime.this_page_needs_a_sign_in')),run.refusals)
        self.assertIn(msg('agent_runtime.this_page_needs_a_sign_in'),run.owner_steps)

    def test_earlier_resolved_sign_in_does_not_hold_this_run(self):
        for route in ('cli','direct'):
            with self.subTest(route=route):
                *_,row=self.close(route,old_login=True)
                self.assertEqual((row['status'],row['goal_outcome'],row['effect_state']),('succeeded','reached','none'))

    def test_procedure_settlement_for_each_goal(self):
        for goal,steps in itertools.product(ws.GOAL_OUTCOMES,('succeeded','partial','failed')):
            with self.subTest(goal=goal,steps=steps):self.close('cli',goal=goal,steps=steps)

    def test_rule_handled_finish_keeps_the_sign_in_floor_and_computes_facts(self):
        service,store=self.service(loop.Script(),loop.Network())
        job=store.enqueue('Example request','rule-handled',channel='http')
        run=WorkRun(store.job(job),time.time())
        service._put_browser_login(job,{'state':'requested','explicit':True})
        service._finish_work(run)
        row=store.job(job)
        self.assertEqual((row['status'],row['goal_outcome'],row['effect_state']),('partial','none','withheld'))
        self.assertIn(('browser_open',msg('agent_runtime.this_page_needs_a_sign_in')),run.refusals)


class RouteParity(orch.Harness):
    def test_cli_upgrade_and_downgrade(self):
        for observed,reached,expected in ((True,True,'succeeded'),(False,False,'failed'),(True,False,'partial')):
            with self.subTest(observed=observed,reached=reached):
                def record(tools):
                    rows=([succeeded('list_notes')] if observed else [])+([failed('browser_read','no_page')] if reached else [])
                    for row in rows:
                        tools.capabilities.record(*row)
                self.engine.before=record
                self.script([orch.plan('codex','Answer the request.')],goals=[reached])
                settle=self.service._settle_worker_outcome
                def capture(run):
                    self.assertEqual(run.outcome,'partial' if reached else 'succeeded')
                    settle(run)
                with mock.patch.object(self.service,'_settle_worker_outcome',side_effect=capture):
                    _job,row=self.run_work('Example request',key=f'cli-{observed}-{reached}')
                self.assertEqual(row['status'],expected)
                self.assertEqual(row['goal_outcome'],'reached' if reached else 'not_reached')
                self.assertEqual(row['effect_state'],'none')

    def test_direct_partial_steps_with_a_reached_goal_succeed(self):
        self.use_main('openai')
        self.script([orch.plan('openai','Answer the request.')],goals=[True])
        def worker(adapter,config,key,messages,sections,capabilities,record,**kwargs):
            return SimpleNamespace(outcome='partial',content='Example question to the owner',provider=config['provider'],model=config['model'],
                                   incomplete=[],verified=[],report=None,alternatives=[],judgment='yes')
        settle=self.service._settle_worker_outcome
        def capture(run):
            self.assertEqual(run.outcome,'partial')
            run.refusals=[('browser_read','Earlier failed read')];run.owner_steps=['Earlier step']
            settle(run)
            self.assertTrue(run.resolved_blocker)
            self.assertEqual((run.refusals,run.owner_steps),([],[]))
        with mock.patch('personal_agent.work_executor.run_agent',worker), \
                mock.patch.object(self.service,'_settle_worker_outcome',side_effect=capture):
            _job,row=self.run_work('Example request')
        self.assertEqual((row['status'],row['goal_outcome'],row['effect_state']),('succeeded','reached','none'))


if __name__=='__main__':unittest.main()
