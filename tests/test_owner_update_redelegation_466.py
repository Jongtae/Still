"""#466: an interim message to the owner does not stop re-delegation; real changes still do.

Live Work 64b7d908 (2026-10-31) only read Gmail, yet its evaluation stopped with ``STOP_EFFECT``
because the worker's one ``tell_owner`` counted as ``changed``.  Injected rows and a temporary
store; no model or account calls.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from personal_agent.agent_runtime import (EFFECT_CHANGED, EFFECT_NONE, EFFECT_UNKNOWN, call_effects,
                                          redelegation_effects)
from personal_agent.orchestrator import EFFECT_UNKNOWN_STOP, MAX_CORRECTIONS, Attempt, Orchestration
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import UPDATE_PROJECTION, QuickStore


def _pair(action, terminal, status='succeeded', *, call_id):
    """A running and a terminal record of one bridge call, shaped like the live Work's rows."""
    base = {'scope': 'subscription-mcp-bridge', 'call_id': call_id, 'host_action': action}
    running = {**base, **{key: value for key, value in terminal.items() if key == 'declared_effect'}}
    return [(action, 'running', running), (action, status, {**base, **terminal})]


def gmail(method, path, kind, call_id, status='succeeded', **extra):
    declared = 'read' if kind == 'read' else 'mutate'
    return _pair('api_request', {'declared_effect': declared, **extra,
                                 'evidence': {'source': {'slot': 'gmail', 'host': 'gmail.googleapis.com', 'path': path,
                                                         'method': method}, 'status': 200, 'effect': kind}},
                 status, call_id=call_id)


READS = (gmail('GET', '/gmail/v1/users/me/labels', 'read', 'g1')
         + _pair('mail_search', {'evidence': {'message_ids': ['m1']}}, call_id='s1')
         + _pair('search_memory', {'evidence': {}}, call_id='s2'))
TOLD = _pair('tell_owner', {'evidence': {'sent': True, 'channel': 'telegram'}}, call_id='t1')
#: name -> rows that must still stop re-delegation, with the class that stops it.
STILL_STOPS = {
    'gmail batchModify': (gmail('POST', '/gmail/v1/users/me/messages/batchModify', 'mutate', 'p1'), EFFECT_CHANGED),
    'gmail send': (gmail('POST', '/gmail/v1/users/me/messages/send', 'mutate', 'p2'), EFFECT_CHANGED),
    'settings write applied': (_pair('settings_change', {'evidence': {'applied': True}}, call_id='c1'), EFFECT_CHANGED),
    'a saved note': (_pair('save_note', {'evidence': {'saved': True}}, call_id='n1'), EFFECT_CHANGED),
    'unknown package tool': (_pair('acme.place_order', {}, call_id='k1'), EFFECT_CHANGED),
    'post with an unknown outcome': (gmail('POST', '/gmail/v1/users/me/messages/batchModify', 'mutate', 'u1',
                                           status='failed', effect='unknown', code='effect_unknown'), EFFECT_UNKNOWN),
    'interim message with an unknown outcome': (_pair('tell_owner', {'effect': 'unknown', 'code': 'effect_unknown'},
                                                      'failed', call_id='t2'), EFFECT_UNKNOWN),
}


class TheClassifier(unittest.TestCase):
    def test_an_attempt_that_only_read_and_told_the_owner_has_no_blocking_effect(self):
        self.assertEqual(redelegation_effects(READS + TOLD), {EFFECT_NONE})
        self.assertEqual(redelegation_effects(READS + TOLD + TOLD[:1]), {EFFECT_NONE}, 'still running')

    def test_the_interim_message_still_counts_for_every_other_consumer(self):
        # Resumption, parking and steer continuation read ``call_effects`` unchanged.
        self.assertIn(('tell_owner', EFFECT_CHANGED, 'succeeded'), call_effects(TOLD))

    def test_real_changes_and_unknown_outcomes_still_stop(self):
        for name, (rows, expected) in STILL_STOPS.items():
            with self.subTest(name):
                self.assertIn(expected, redelegation_effects(READS + TOLD + rows))
                self.assertIn(expected, redelegation_effects(rows))


class TheEvaluation(unittest.TestCase):
    """``orchestration_step`` passes the effect ``Orchestration.next`` stops on."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(Path(temp.name) / 'state')
        self.service = AgentService(self.store)
        self.job = self.store.job(self.store.enqueue('label my newsletters in Gmail', 'k1', owner_typed=True))

    def step(self, rows, told=()):
        with self.store.db() as db:
            for tool, status, detail in rows:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                           (self.job['id'], tool, status, json.dumps(detail), 0.0))
            for text in told:
                db.execute('INSERT INTO messages(role,content,channel,created,workspace_id,job_id,delivery_projection) '
                           'VALUES (?,?,?,?,?,?,?)', ('assistant', text, self.job['channel'], 0.0,
                                                      self.job.get('workspace_id'), self.job['id'], UPDATE_PROJECTION))
        orchestration = SimpleNamespace(orchestrated=True, _redact=lambda text, **_: text, next=Mock(return_value=None),
                                        evaluate_answer=Mock(return_value='not_reached'), may_judge=lambda: True,
                                        budget_allows=lambda **_: True, budget=None, owner_told=Mock())
        self.service.orchestration_step(orchestration, object(), self.job['id'], 0, answer='42 mails found', outcome='partial')
        return orchestration

    def test_reads_and_an_interim_message_leave_the_next_attempt_open(self):
        orchestration = self.step(READS + TOLD, told=('Found 42 newsletters so far.',))
        self.assertIs(orchestration.next.call_args.kwargs['effect'], False)
        orchestration.owner_told.assert_called_once_with(['Found 42 newsletters so far.'])

    def test_a_real_change_still_stops_with_the_effect(self):
        for name in ('gmail batchModify', 'gmail send', 'settings write applied'):
            with self.subTest(name):
                self.setUp()
                orchestration = self.step(READS + TOLD + STILL_STOPS[name][0])
                self.assertEqual(orchestration.next.call_args.kwargs['effect'], 'changed')

    def test_an_unknown_outcome_still_stops_as_unknown(self):
        orchestration = self.step(READS + TOLD + STILL_STOPS['post with an unknown outcome'][0])
        self.assertEqual(orchestration.next.call_args.kwargs['effect'], EFFECT_UNKNOWN_STOP)


class TheNextBrief(unittest.TestCase):
    """A further attempt is told what the owner already heard, so it neither repeats nor silently contradicts it."""

    def orchestration(self):
        return Orchestration(SimpleNamespace(), SimpleNamespace(), request='label my newsletters')

    def test_a_later_attempt_names_what_the_owner_was_already_sent(self):
        orchestration = self.orchestration()
        orchestration.owner_told(['Found 42 newsletters so far.', '  ', None])
        attempt = Attempt(2, 'codex', notes='label them', planned=True)
        attempt.told = orchestration.told
        brief = attempt.brief(adjusted=True)
        self.assertIn('already sent the owner these messages', brief)
        self.assertIn('- Found 42 newsletters so far.', brief)

    def test_an_attempt_with_nothing_sent_before_says_nothing_about_it(self):
        self.assertNotIn('already sent the owner', Attempt(1, 'codex', notes='label them', planned=True).brief() or '')

    def test_the_kept_messages_are_bounded(self):
        orchestration = self.orchestration()
        orchestration.owner_told([f'message {n}' for n in range(MAX_CORRECTIONS + 2)])
        self.assertEqual(len(orchestration.told), MAX_CORRECTIONS)
        self.assertEqual(orchestration.told[-1], f'message {MAX_CORRECTIONS + 1}')


if __name__ == '__main__':
    unittest.main()
