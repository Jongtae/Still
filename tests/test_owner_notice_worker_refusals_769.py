"""#769: a refusal meant only for the worker never reaches the owner's notice as text.

Evidence class: model-free.  A temporary ``QuickStore`` and recorded tool events; ``AgentService.owner_refusals``,
``failed_attempt_report`` and ``owner_cause`` run unchanged.  No tool, request or site is special-cased in ``src``.
"""
import json
import tempfile
import unittest
from pathlib import Path

from personal_agent.conversation_projection import owner_cause, worker_directed_refusal
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

LENGTH = 'field is at most 1000 characters.'
FORMAT = 'packages are name==version, up to 16.'


def refused(tool, error, code='argument_invalid', **extra):
    """A call Still refused before running it, as written never makeable (``_error_observation``)."""
    return (tool, 'failed', json.dumps({'host_action': tool, 'code': code, 'retry': 'permanent', 'effect': 'none',
                                        'ran': False, 'error': error, **extra}))


def failed_run(tool, error):
    """A call that ran and failed: its reason is a fact about the outside world."""
    return (tool, 'failed', json.dumps({'host_action': tool, 'code': 'tool_failed', 'retry': 'permanent',
                                        'effect': 'none', 'error': error}))


def succeeded(tool):
    return (tool, 'succeeded', json.dumps({'host_action': tool, 'evidence': {}}))


class WorkerDirectedRefusalTests(unittest.TestCase):
    def test_only_an_unrun_permanent_refusal_with_no_owner_wait_is_the_workers(self):
        base = {'ran': False, 'retry': 'permanent', 'error': LENGTH}
        self.assertTrue(worker_directed_refusal(base))
        for label, detail in (('the call ran', {**base, 'ran': None}),
                              ('it waits for the owner', {**base, 'requires': 'api-step-approval'}),
                              ('a budget stop', {**base, 'retry': 'budget'}),
                              ('a needs-setup refusal', {**base, 'retry': 'needs_setup'}),
                              ('a transient failure', {**base, 'retry': 'transient'}),
                              ('no record', None)):
            with self.subTest(label):
                self.assertFalse(worker_directed_refusal(detail))


class OwnerNoticeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'data')
        self.service = AgentService(self.store)

    def work(self, rows):
        job = self.store.enqueue('x', f'k-{len(rows)}-{id(rows)}')
        with self.store.db() as db:
            for tool, status, detail in rows:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,1)',
                           (job, tool, status, detail))
        return job

    def test_the_owner_reads_the_step_never_the_workers_correction(self):
        sign_in = ('browser_open', 'sign-in needed')
        for label, rows, refusals, expected in (
                ('corrected by a later call of the same action',
                 [refused('entrusted_line', LENGTH), succeeded('entrusted_line')],
                 [('entrusted_line', LENGTH), sign_in], [sign_in]),
                ('never redone: the step stays named, without the text',
                 [refused('code_run', FORMAT), succeeded('web_search')],
                 [('code_run', FORMAT)], [('code_run', None)]),
                ('a failure that ran keeps its reason',
                 [failed_run('weather', 'city not found.')], [('weather', 'city not found.')],
                 [('weather', 'city not found.')]),
                ('the same text from a call that ran keeps it',
                 [refused('code_run', FORMAT), failed_run('code_run', FORMAT), succeeded('code_run')],
                 [('code_run', FORMAT), ('code_run', FORMAT)], [('code_run', FORMAT), ('code_run', FORMAT)]),
                ('an owner wait keeps its reason',
                 [refused('api_request', 'waits for your approval.', code='approval_required', requires='api-step-approval')],
                 [('api_request', 'waits for your approval.')], [('api_request', 'waits for your approval.')])):
            with self.subTest(label):
                job = self.work(rows)
                named = self.service.owner_refusals(job, None, refusals)
                self.assertEqual(named, expected)
                spoken = owner_cause(named) or ''
                self.assertNotIn(LENGTH, spoken)
                if label.startswith('never redone'):
                    self.assertTrue(spoken.endswith('— 코드 실행'), spoken)

    def test_a_failed_work_without_an_answer_says_the_step_not_the_correction(self):
        job = self.work([('subscription_engine', 'succeeded', '{}'),
                         refused('entrusted_line', LENGTH), succeeded('entrusted_line'),
                         refused('code_run', FORMAT)])
        report = self.service.failed_attempt_report(self.store.job(job), 'stopped') or ''
        self.assertNotIn(LENGTH, report)
        self.assertNotIn(FORMAT, report)
        self.assertIn('코드 실행', report)
        self.assertNotIn('맡은 일', report.split('\n')[0])


if __name__ == '__main__':
    unittest.main()
