"""GOV-ASK-LESS-01 slice (a) (#918): the owner's own AI saves a fact at once; the undo is exact.

Owner decision 2026-09-30: "I'll remember that. • Has a spouse — things like this should be handled without asking; asking at every turn is not a good user experience."

* The owner's worker's ``save_memory`` writes current Memory at once (no
  👍/👎 ask, no #597 judgment call).  #955 (C5 as amended): no notice of its
  own follows; the reply says in passing what it will keep in mind.  #962
  retired the #918 saved-notice machinery: a notice an earlier version queued
  is not sent.
* The undo (asking with ``forget_record revert``, or 되돌리기 in 내 기록)
  retracts exactly that Memory row (current id and content digest), restores
  the value it superseded, and is refused when the row changed since.
* Third-party writers (package tools, delegated specialists) still leave a
  pending MemoryCandidate with the #818/#836 ask (C5 unchanged for them).
* The web lists the fact as saved, not for review; Evidence and the
  information-use audit record the auto-saved write and the undo.

Evidence class: unit and model-free service tests with a scripted model and
an injected Telegram transport.  No live model, no live Telegram.
"""
import json
import tempfile
import unittest
from pathlib import Path

from personal_agent.agent_runtime import (CORE_INSTRUCTIONS, DEFINITIONS, MEMORY_OWNER, MEMORY_REFUSALS,
                                          SECRET_SHAPED_VALUE, THIRD_PARTY_MEMORY_WRITE, Capabilities, evidence_summary,
                                          work_saved_values, worker_result)
from personal_agent.decision import OUTCOME_DECIDED, BinaryDecision, FixtureDecisionEngine, fixture_confidence
from personal_agent.manifests import runtime_packages
from personal_agent.quickstart_service import MEMORY_CANDIDATES_KIND, MEMORY_PENDING_WEB_NOTE, MEMORY_UNDO_TOOL
MEMORY_SAVED_KIND, MEMORY_SAVED_UPKEEP_KIND = 'memory_saved', 'memory_saved_upkeep'
from personal_agent.quickstart_store import QuickStore

from test_memory_candidate_confirm import CHAT, GENERATION, KEY, OTHER, VALUE, TelegramHarness

SECOND_KEY, SECOND_VALUE = 'profile.routine.commute', '지하철'


class SaveDirectly(TelegramHarness):
    """The owner's own worker saves at once; no notice follows the reply (#955, #962); the undo is exact.

    The undo is the one ``memory_prompts.retract_saved`` that asking (``forget_record revert``) and the
    records screen's 되돌리기 (``memory_undo_request``) share.
    """

    def saved_turn(self, message='난 오늘 내 직장인 판교 카카오뱅크로 출근했어.', plan=None, channel=True):
        self.plan = plan if plan is not None else [('save_memory', {'memory_key': KEY, 'content': VALUE})]
        if not self.claim:
            self.claim_completion()
        job_id = self.enqueue(message, channel)
        self.service.run_one()
        return job_id

    def told(self, **kwargs):
        """Run and deliver the reply; nothing else is sent about the save.  Returns the Work."""
        job_id = self.saved_turn(**kwargs)
        self.service.deliver_one()
        self.assertEqual(self.sends()[-1]['text'], self.text, 'the answer as the secretary said it')
        self.assertFalse(self.service.deliver_notification(), '#955: no notice of its own follows')
        self.assertEqual(self.notification(job_id, MEMORY_SAVED_KIND), [])
        return self.store.job(job_id)

    def current(self, key=KEY):
        return self.store.current_memory(key, MEMORY_OWNER)

    def undo(self, job_id, key=KEY):
        from personal_agent.memory_prompts import retract_saved
        row = self.current(key)
        return retract_saved(self.store, job_id, {'id': row['id'], 'digest': row['content_digest'], 'key': key})

    def memory_states(self):
        with self.store.db() as db:
            return [(row['content'], row['state']) for row in db.execute('SELECT content,state FROM memories ORDER BY created,id')]

    def test_calibrated_voice_saves_and_its_undo_brings_back_the_previous_one(self):
        from personal_agent.memory_service import VOICE_PREFERENCE_KEY
        previous = '짧게 말해 줘'
        self.told(message='말투는 짧게 해 줘. 기억해 둬.',
                  plan=[('save_memory', {'memory_key': VOICE_PREFERENCE_KEY, 'content': previous})])
        preference = '짧게, 이모지 없이. 힘든 이야기는 다정하게'
        job = self.told(message='B가 좋아. 힘든 이야기에는 다정하게 해 줘.',
                        plan=[('save_memory', {'memory_key': VOICE_PREFERENCE_KEY, 'content': preference})])
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual([(m['memory_key'], m['content']) for m in self.store.memories()],
                         [(VOICE_PREFERENCE_KEY, preference)])
        self.assertTrue(self.service.memory_undo_request(self.current(VOICE_PREFERENCE_KEY)['id'])['undone'])
        self.assertEqual([(m['memory_key'], m['content']) for m in self.store.memories()],
                         [(VOICE_PREFERENCE_KEY, previous)])

    def test_voice_save_replace_and_owner_undo_reach_every_generator(self):
        from unittest import mock
        from personal_agent.memory_service import VOICE_PREFERENCE_KEY
        from personal_agent.prompt_files import prompt
        from personal_agent.decision import StructuredDecision
        from test_orchestrator import Harness, plan
        from test_self_review import SelfReview, review

        preference = '반말로 해줘'
        # The real worker tool and the exact owner undo; no direct Memory edits.
        job = self.told(message='앞으로는 반말로 해줘. 기억해 둬.',
            plan=[('save_memory', {'memory_key': VOICE_PREFERENCE_KEY, 'content': preference})])
        telegram = self.store.config('telegram')

        def readers(expected, stage):
            # Reuse the route and self-review harnesses against the same canonical store.
            workers = Harness()
            self.addCleanup(workers.doCleanups)
            with mock.patch('test_orchestrator.QuickStore', return_value=self.store):
                workers.setUp()
            workers.script([plan('codex', 'Answer.'), plan('openai', 'Answer.')], goals=[True, True])
            cli_job, _ = workers.run_work('안녕', key=stage + '-cli')
            cli = workers.engine.turns[-1]['prompt']
            instructions = self.store.turn_provenance(cli_job)['instructions']
            workers.use_main('openai')
            workers.run_work('고마워', key=stage + '-api')
            system = workers.transport.bodies[-1]['input'][0]['content']
            # #1164: the OpenAI route sends Still's static prefix as text blocks.
            system = system if isinstance(system, str) else ''.join(block['text'] for block in system)
            profiles = [cli, system]
            self.assertIn(prompt('voice'), instructions)
            self.assertIn(prompt('voice'), system)
            for context, question, _schema in workers.asked_plans:
                profiles.append(context.facts['owner_profile'])
                self.assertIn(prompt('voice'), question)
            self.assertEqual(len(workers.asked_goals), 2)
            profiles.extend(context.facts['owner_context'] for context in workers.asked_goals)

            reviewer = SelfReview()
            self.addCleanup(reviewer.doCleanups)
            with mock.patch('test_orchestrator.QuickStore', return_value=self.store):
                reviewer.setUp()
            reviewer.missed('검토해 줘')
            reviewer.answers = [review()]
            self.assertTrue(reviewer.service.run_owner_model_upkeep())
            context, question, _schema = reviewer.facts[-1]
            profiles.append(context.facts['owner_profile'])
            self.assertIn(prompt('voice'), question)

            side_calls = []
            def side_reply(context, question, schema):
                side_calls.append((context, question))
                return StructuredDecision(OUTCOME_DECIDED, {'answered': True, 'reply': '안녕'}, fixture_confidence())
            self.service.side_answer_engine_factory = lambda: FixtureDecisionEngine(structured=side_reply)
            running = self.store.enqueue('다른 일', stage + '-running')
            side = self.store.enqueue('삼각형이 뭐야?', stage + '-side')
            self.service._side_answer(self.store.job(running), self.store.job(side))
            [(context, question)] = side_calls
            self.assertIn(prompt('voice'), question)
            profiles.append(context.facts.get('owner_voice_preference', ''))
            keys = self.store.turn_provenance(side)['owner_information']['profile_keys']
            self.assertEqual(keys, [VOICE_PREFERENCE_KEY] if expected else [])
            for supplied in profiles:
                with self.subTest(stage=stage, supplied=supplied[:80]):
                    if expected:
                        self.assertIn(VOICE_PREFERENCE_KEY + ': ' + expected, supplied)
                    else:
                        self.assertNotIn(VOICE_PREFERENCE_KEY + ':', supplied)
                        self.assertNotIn(preference, supplied)
            # These were context-only inputs, not queued worker tasks.
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (running,))
            self.store.put('telegram', telegram)

        second_preference = '반말로 해줘. 이모지는 빼줘'
        self.told(message='이모지는 빼줘. 말투 설정도 바꿔 둬.',
            plan=[('save_memory', {'memory_key': VOICE_PREFERENCE_KEY, 'content': second_preference})])
        rows = [row for row in self.store.memories() if row['memory_key'] == VOICE_PREFERENCE_KEY]
        self.assertEqual([row['content'] for row in rows], [second_preference])
        readers(second_preference, 'replaced')
        self.assertTrue(self.service.memory_undo_request(self.current(VOICE_PREFERENCE_KEY)['id'])['undone'])
        self.assertEqual([row['content'] for row in self.store.memories() if row['memory_key'] == VOICE_PREFERENCE_KEY], [preference])
        readers(preference, 'restored')
        self.assertTrue(self.service.memory_undo_request(self.current(VOICE_PREFERENCE_KEY)['id'])['undone'])
        self.assertEqual([row for row in self.store.memories() if row['memory_key'] == VOICE_PREFERENCE_KEY], [])
        readers(None, 'undone')

    def test_the_owner_worker_save_is_current_at_once_and_nothing_else_is_sent(self):
        job = self.told()
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        self.assertEqual([(m['memory_key'], m['content'], m['state']) for m in self.store.memories()],
                         [(KEY, VALUE, 'current')])
        self.assertEqual(self.store.memory_candidates(include_decided=True), [], 'no candidate, pending or decided')
        self.assertEqual(self.notification(job['id'], MEMORY_CANDIDATES_KIND), [], 'no ask')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM telegram_notifications WHERE job_id=?', (job['id'],)).fetchone()[0], 0,
                             'the save holds no notice row')

    def test_the_worker_reads_the_save_as_done(self):
        self.saved_turn()
        [tool] = [message['content'] for message in self.bodies[-1]['messages'] if message.get('role') == 'tool']
        result = json.loads(tool)
        self.assertEqual((result['remembered'], result['content'], result['memory_key'], result['replaced_previous']),
                         (True, VALUE, KEY, False))
        self.assertIn('undo it', result['next'])
        self.assertIn('say in passing', result['next'], '#955: the reply tells the save')
        for word in ('Not remembered', 'one tap', 'Still', 'candidate', 'approval', 'refused_because', 'digest', 'stor'):
            self.assertNotIn(word, tool)

    def test_nothing_asks_anymore_for_an_owner_worker_save(self):
        purposes = []

        def judge(context, proposition):
            purposes.append(context.purpose)
            return BinaryDecision(OUTCOME_DECIDED, True, fixture_confidence()) if context.purpose == 'goal-reached' else None
        self.claim_completion(FixtureDecisionEngine(judge=judge))
        job = self.told()
        self.assertIn('goal-reached', purposes, 'the judgment ran, so its absence below is meaningful')
        self.assertNotIn('explicit-memory-request', purposes, 'no #597 judgment call on the owner-worker path')
        for body in self.sends():
            self.assertNotIn('기억해 둘까요', body['text'])
            for line in (body.get('reply_markup') or {}).get('inline_keyboard') or ():
                for button in line:
                    self.assertNotIn('👍', button['text'])
                    self.assertNotIn('👎', button['text'])
        self.assertEqual(self.notification(job['id'], MEMORY_CANDIDATES_KIND), [])

    def test_an_upkeep_save_holds_no_notice_either(self):
        job = self.told()
        def run(job, judgments, **_kwargs):
            memory = self.store.save_memory(SECOND_KEY, SECOND_VALUE, MEMORY_OWNER, work_id=job['id'])
            return 'done', 1, 'recorded', {'applied': [{'memory_key': SECOND_KEY, 'memory_id': memory['id'],
                                                         'outcome': 'memory', 'auto_saved': True}]}
        self.service.owner_model.run = run
        self.assertTrue(self.service.owner_model_flight.acquire(blocking=False))
        self.service._owner_model_run({'job_id': job['id'], 'expired': 0}, 0)
        self.assertEqual(self.current(SECOND_KEY)['content'], SECOND_VALUE)
        self.assertFalse(self.service.deliver_notification())
        self.assertEqual(self.notification(job['id'], MEMORY_SAVED_UPKEEP_KIND), [])

    def test_a_credential_shaped_value_leaves_no_memory(self):
        secret = 'sk-proj-abcdefghijklmnopqrstuvwxyz123456'
        job_id = self.saved_turn(plan=[('save_memory', {'memory_key': 'profile.api', 'content': secret})])
        self.assertEqual(self.store.memories(), [])
        self.assertEqual(self.store.memory_candidates(include_decided=True), [])
        self.service.deliver_one()
        self.assertFalse(self.service.deliver_notification())
        tool_messages = [body['content'] for body in self.bodies[-1]['messages'] if body.get('role') == 'tool']
        self.assertTrue(any(SECRET_SHAPED_VALUE in content for content in tool_messages), tool_messages)
        for content in tool_messages:
            self.assertNotIn(secret, content, 'the worker\'s refusal never echoes the value')
        [event] = [row for row in self.store.task_events(job_id) if row['tool'] == 'save_memory' and row['status'] != 'running']
        self.assertNotIn(secret, json.dumps(event, ensure_ascii=False))

    # --- undo -----------------------------------------------------------------------

    def test_undo_retracts_exactly_that_item(self):
        job = self.told()
        self.store.save_memory('profile.food_preference', '매운 음식')   # another fact, elsewhere: untouched
        self.assertEqual(self.undo(job['id'])['status'], 'retracted')
        self.assertEqual([(m['memory_key'], m['content']) for m in self.store.memories()],
                         [('profile.food_preference', '매운 음식')])
        self.assertEqual(self.memory_states(), [(VALUE, 'retracted'), ('매운 음식', 'current')])

    def test_undo_restores_the_superseded_value(self):
        kept = self.store.save_memory(KEY, '서울 역삼 오피스')
        job = self.told()
        self.assertEqual(self.memory_states(), [('서울 역삼 오피스', 'superseded'), (VALUE, 'current')])
        self.assertEqual(self.undo(job['id'])['restored'], kept['id'])
        self.assertEqual(self.memory_states(), [('서울 역삼 오피스', 'current'), (VALUE, 'retracted')])
        self.assertEqual(self.current()['content'], '서울 역삼 오피스')

    def test_a_memory_changed_since_refuses_the_undo(self):
        from personal_agent.memory_prompts import retract_saved
        job = self.told()
        saved = self.current()
        self.store.save_memory(KEY, '여의도 본사')      # the owner changed it elsewhere
        undone = retract_saved(self.store, job['id'], {'id': saved['id'], 'digest': saved['content_digest'], 'key': KEY})
        self.assertEqual(undone['status'], 'outdated')
        self.assertEqual([m['content'] for m in self.store.memories()], ['여의도 본사'], 'nothing retracted')

    def test_a_deleted_memory_refuses_the_undo(self):
        from personal_agent.memory_prompts import retract_saved
        job = self.told()
        saved = self.current()
        self.assertTrue(self.store.delete_memory(MEMORY_OWNER, saved['id'])['deleted'])
        undone = retract_saved(self.store, job['id'], {'id': saved['id'], 'digest': saved['content_digest'], 'key': KEY})
        self.assertEqual(undone['status'], 'outdated')
        self.assertEqual(self.store.memories(), [])

    def test_retract_is_bound_to_the_content_digest(self):
        self.saved_turn()
        [memory] = self.store.memories()
        with self.assertRaises(ValueError):
            self.store.retract_memory(MEMORY_OWNER, memory['id'], 'f' * 64)
        with self.assertRaises(ValueError):
            self.store.retract_memory('someone-else', memory['id'], memory['content_digest'])
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE])
        receipt = self.store.retract_memory(MEMORY_OWNER, memory['id'], memory['content_digest'])
        self.assertEqual((receipt['retracted'], receipt['id'], receipt['restored']), (True, memory['id'], None))
        with self.assertRaises(ValueError):
            self.store.retract_memory(MEMORY_OWNER, memory['id'], memory['content_digest'])

    def test_the_web_shows_the_fact_as_saved_not_for_review(self):
        job_id = self.saved_turn()
        self.assertEqual(self.service.memory_candidate_request({'operation': 'list'})['candidates'], [])
        saved = self.store.personal_records(record_filter='memory')['items']
        self.assertEqual([(row['memory_key'], row['content']) for row in saved], [(KEY, VALUE)])
        [memory] = self.store.memories()
        self.assertEqual(self.store.memory_sources([memory['id']])[memory['id']]['work_id'], job_id, 'its source as today')
        [served] = [item for item in self.service.owner_jobs(self.store.jobs()) if item['id'] == job_id]
        self.assertNotIn(MEMORY_PENDING_WEB_NOTE, served['response'], 'nothing to choose in 내 기록')
        [link] = self.service._retained_links([(job_id, self.store.task_events(job_id))])[job_id]
        self.assertEqual((link['kind'], link['id'], link['available']), ('memory', memory['id'], True))

    def test_the_records_name_the_auto_saved_write_and_the_undo(self):
        job = self.told()
        [event] = [row for row in self.store.task_events(job['id'])
                   if row['tool'] == 'save_memory' and row['status'] == 'succeeded']
        evidence = event['trace']['evidence']
        self.assertEqual((evidence['saved'], evidence['state'], evidence['auto_saved'], evidence['memory_key']),
                         (True, 'current', True, KEY))
        self.assertNotIn(VALUE, json.dumps(evidence, ensure_ascii=False), 'references, never the value')
        audit = self.service.work_information_use(job['id'])
        [memory] = [row for row in audit['used'] if row['category'] == 'memory']
        self.assertEqual(memory['items'], [f'{KEY} (바로 저장)'])
        self.undo(job['id'])
        [undo] = [row for row in self.store.task_events(job['id']) if row['tool'] == MEMORY_UNDO_TOOL]
        self.assertEqual(undo['status'], 'recorded', 'not a Work step')
        self.assertEqual(undo['trace']['evidence']['memory_key'], KEY)
        audit = self.service.work_information_use(job['id'])
        [memory] = [row for row in audit['used'] if row['category'] == 'memory']
        self.assertEqual(memory['items'], [f'{KEY} (바로 저장)', f'{KEY} (되돌림)'])
        self.assertEqual(self.store.job(job['id'])['status'], 'succeeded', 'the settled Work is unchanged')

    def test_evidence_summary_carries_the_auto_saved_state(self):
        summary = evidence_summary('save_memory', {'id': 'm1', 'memory_key': KEY, 'content': VALUE, 'state': 'current',
                                                   'saved': True, 'auto_saved': True})
        self.assertEqual((summary['saved'], summary['auto_saved'], summary['state']), (True, True, 'current'))
        self.assertNotIn('content', summary)
        held = evidence_summary('save_memory', {'id': 'c1', 'state': 'pending', 'refused_because': THIRD_PARTY_MEMORY_WRITE})
        self.assertEqual((held['saved'], held['auto_saved'], held['refused_because']), (False, False, THIRD_PARTY_MEMORY_WRITE))

    # --- review P2: the undo and its record are one transaction; the redaction set keeps every state ---

    def test_the_undo_and_its_record_are_one_transaction(self):
        job = self.told()
        memory = self.current()
        broken = {'job_id': job['id'], 'tool': MEMORY_UNDO_TOOL, 'status': 'recorded',
                  'detail': {'host_action': MEMORY_UNDO_TOOL, 'evidence': {'id': memory['id'], 'unserializable': object()}}}
        with self.assertRaises(TypeError):
            self.store.retract_memory(MEMORY_OWNER, memory['id'], memory['content_digest'], record=broken)
        self.assertEqual([(m['id'], m['state']) for m in self.store.memories()], [(memory['id'], 'current')],
                         'a record that cannot be written leaves the Memory current')
        self.assertEqual([row for row in self.store.task_events(job['id']) if row['tool'] == MEMORY_UNDO_TOOL], [])
        self.undo(job['id'])
        self.assertEqual(self.store.memories(), [])
        [undo] = [row for row in self.store.task_events(job['id']) if row['tool'] == MEMORY_UNDO_TOOL]
        self.assertEqual((undo['status'], undo['trace']['evidence']['id'], undo['trace']['evidence']['restored_id']),
                         ('recorded', memory['id'], None))

    def test_the_undo_record_names_the_restored_row(self):
        kept = self.store.save_memory(KEY, '서울 역삼 오피스')
        job = self.told()
        self.undo(job['id'])
        [undo] = [row for row in self.store.task_events(job['id']) if row['tool'] == MEMORY_UNDO_TOOL]
        self.assertEqual(undo['trace']['evidence']['restored_id'], kept['id'])

    def test_audit_values_keep_superseded_and_retracted_values_of_the_work(self):
        job = self.told(plan=[('save_memory', {'memory_key': 'passport', 'content': 'M1234567'}),
                              ('save_memory', {'memory_key': 'passport', 'content': 'M7654321'})])
        self.assertEqual(self.memory_states(), [('M1234567', 'superseded'), ('M7654321', 'current')])
        written = work_saved_values(self.store, job['id'])
        self.assertIn('M1234567', written, 'the superseded value still stands in the Work\'s tool conversation')
        self.assertIn('M7654321', written)
        self.undo(job['id'], 'passport')
        self.assertEqual(self.memory_states(), [('M1234567', 'current'), ('M7654321', 'retracted')])
        written = work_saved_values(self.store, job['id'])
        self.assertIn('M7654321', written, 'a retracted value stays in the audit input')
        self.assertIn('M1234567', written)


class NoNotice(TelegramHarness):
    """#955 (owner 2026-11-17, C5 as amended): no saved notice is sent; a save is undone exactly by asking."""

    def test_a_notice_queued_by_an_earlier_version_is_not_sent(self):
        job_id = self.enqueue('난 판교에서 일해')
        memory = self.store.save_memory(KEY, VALUE, MEMORY_OWNER, work_id=job_id)
        job = self.store.job(job_id)
        self.service.queue_notification(job, MEMORY_SAVED_KIND, fingerprint=json.dumps(
            {'items': [[memory['id'], memory['content_digest']]], 'more': 0}))
        before = len(self.sends())
        self.service.deliver_notification()
        self.assertEqual(len(self.sends()), before, 'an older queued saved notice is not sent')
        self.assertEqual([row['state'] for row in self.notification(job_id, MEMORY_SAVED_KIND)], ['cancelled'])

    def test_asking_undoes_a_save_and_brings_back_the_value_it_replaced(self):
        from personal_agent.agent_runtime import ToolError
        job_id = self.enqueue('직장 바뀌었어. 이제 분당이야')
        caps = Capabilities(self.store, None, {}, '', job_id, lambda *a, **k: None)
        first = caps.execute('save_memory', {'memory_key': KEY, 'content': VALUE})
        second = caps.execute('save_memory', {'memory_key': KEY, 'content': VALUE + ' 분당'})
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE + ' 분당'])
        result = caps.execute('forget_record', {'action': 'revert', 'ref': f"memory:{second['id']}"})
        self.assertEqual(result['restored'], f"memory:{first['id']}")
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE])
        with self.store.db() as db:
            undo = [json.loads(row['detail']) for row in db.execute(
                "SELECT detail FROM tool_events WHERE job_id=? AND tool='memory_undo'", (job_id,))]
        self.assertEqual(len(undo), 1, 'the undo has its Evidence event')
        for ref, code in ((f"memory:{second['id']}", 'memory_not_current'), ('state:x', 'invalid_arguments'),
                          (None, 'invalid_arguments')):
            with self.subTest(ref=ref), self.assertRaises(ToolError) as refused:
                caps.execute('forget_record', {'action': 'revert', **({'ref': ref} if ref else {})})
            self.assertEqual(refused.exception.code, code)
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE], 'a refused revert changes nothing')
        # #955 review: a Work that undid a save is the owner managing state; no lesson is drawn from it.
        from personal_agent.agent_runtime import evidence_summary
        with self.store.db() as db:
            db.execute("INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)",
                       (job_id, 'forget_record', 'succeeded',
                        json.dumps({'evidence': evidence_summary('forget_record', result)}), 1.0))
        self.assertTrue(self.store.managed_owner_state(job_id))

    def test_a_revert_does_not_claim_an_expired_earlier_value_is_back(self):
        """#955 review: like the notice undo (#220), a predecessor past its last day is not back in use."""
        job_id = self.enqueue('출장지는 이제 분당이야')
        caps = Capabilities(self.store, None, {}, '', job_id, lambda *a, **k: None)
        self.store.save_memory(KEY, VALUE, MEMORY_OWNER, work_id=job_id, valid_until='2000-01-01')
        second = caps.execute('save_memory', {'memory_key': KEY, 'content': VALUE + ' 분당'})
        result = caps.execute('forget_record', {'action': 'revert', 'ref': f"memory:{second['id']}"})
        self.assertIsNone(result['restored'])
        [expired] = result['earlier_expired']
        self.assertTrue(expired.startswith('memory:'))
        self.assertIn('기한이 지나', result['message'])

    def test_a_revert_reports_facts_under_other_keys_it_brings_back(self):
        """#955 review (P1): a save that ended facts under other keys (#220) brings them back on revert, and says so."""
        job_id = self.enqueue('회사 옮겼어. 이제 강남이야')
        caps = Capabilities(self.store, None, {}, '', job_id, lambda *a, **k: None)
        old = self.store.save_memory(SECOND_KEY, SECOND_VALUE, MEMORY_OWNER, work_id=job_id)
        new = self.store.save_memory(KEY, '강남', MEMORY_OWNER, work_id=job_id, invalidates=[(old['id'], 'moved')])
        self.assertEqual([m['content'] for m in self.store.memories()], ['강남'])
        result = caps.execute('forget_record', {'action': 'revert', 'ref': f"memory:{new['id']}"})
        self.assertEqual((result['restored'], result['restored_ended'], result['earlier_expired']),
                         (None, [f"memory:{old['id']}"], []))
        self.assertIn('이전 값을 다시', result['message'])
        self.assertEqual([m['content'] for m in self.store.memories()], [SECOND_VALUE])


class SecretsNeverEnterMemory(unittest.TestCase):
    """#918 review P1: a stored secret or a credential-shaped value is refused before any Memory write."""

    CREDENTIAL = 'sk-proj-abcdefghijklmnopqrstuvwxyz123456'
    STORED = 'LEAKYSECRET0918VALUE'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(Path(self.temp.name) / 'data')
        self.store.secret('telegram_token', self.STORED)
        self.job = self.store.enqueue('내 토큰 좀 기억해 둬', 'secret-save')

    def caps(self, **kwargs):
        return Capabilities(self.store, None, {}, '', self.job, lambda *a, **k: None, **kwargs)

    def assert_refused(self, result, value):
        self.assertEqual((result['saved'], result['state'], result['refused_because']), (False, 'refused', SECRET_SHAPED_VALUE))
        self.assertNotIn(value, json.dumps(result, ensure_ascii=False), 'the refusal never echoes the value')
        self.assertNotIn('content', result)
        self.assertNotIn(value, json.dumps(worker_result('save_memory', result), ensure_ascii=False))
        self.assertNotIn(value, MEMORY_REFUSALS[SECRET_SHAPED_VALUE])
        self.assertEqual(self.store.memories(), [])
        self.assertEqual(self.store.memory_candidates(include_decided=True), [])

    def test_a_credential_shaped_value_is_refused_by_the_owner_worker(self):
        self.assert_refused(self.caps().execute('save_memory', {'memory_key': 'profile.api', 'content': self.CREDENTIAL}),
                            self.CREDENTIAL)

    def test_a_stored_secret_value_is_refused_wherever_it_stands(self):
        for key, content in (('profile.note', 'my token is ' + self.STORED), ('profile.' + self.STORED, '값')):
            with self.subTest(key=key):
                self.assert_refused(self.caps().execute('save_memory', {'memory_key': key, 'content': content}), self.STORED)

    def test_a_bare_value_under_a_credential_named_key_is_refused(self):
        """Re-review P3: the joined ``key: content`` passes the gate too."""
        self.assert_refused(self.caps().execute('save_memory', {'memory_key': 'profile.account.password', 'content': 'hunter2'}),
                            'hunter2')
        self.assert_refused(self.caps().execute('save_memory', {'memory_key': 'profile.bank.api_key', 'content': 'abc123'}),
                            'abc123')

    def test_a_third_party_write_with_a_secret_makes_no_candidate(self):
        caps = self.caps(delegated=True, allowed_tools=['save_memory'])
        self.assert_refused(caps.execute('save_memory', {'memory_key': 'profile.api', 'content': self.CREDENTIAL}), self.CREDENTIAL)

    def test_an_ordinary_value_still_saves(self):
        result = self.caps().execute('save_memory', {'memory_key': KEY, 'content': VALUE})
        self.assertEqual((result['state'], result['auto_saved']), ('current', True))

    def test_list_memory_passes_the_secret_filter(self):
        # A row that entered the store another way (older data, a direct owner write) still never reaches the model raw.
        self.store.save_memory('profile.note.' + self.STORED, 'token ' + self.STORED + ' and ' + self.CREDENTIAL)
        from personal_agent.current_context import redact_known_secrets
        caps = self.caps(secret_redactor=lambda text: redact_known_secrets(self.store, text))
        result = caps.execute('list_memory', {})
        serialized = json.dumps(result, ensure_ascii=False) + json.dumps(caps.evidence, ensure_ascii=False)
        self.assertNotIn(self.STORED, serialized)
        self.assertNotIn(self.CREDENTIAL, serialized)
        [row] = result['memories']
        self.assertEqual((row['memory_key'], row['content']), ('profile.note.[redacted]', 'token [redacted] and [redacted]'))
        # The credential-shape filter applies even without a stored-secret redactor.
        [row] = self.caps().execute('list_memory', {})['memories']
        self.assertNotIn(self.CREDENTIAL, row['content'])


class ThirdPartyWrites(unittest.TestCase):
    """C5 unchanged: a package tool or a delegated specialist still proposes a MemoryCandidate."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(Path(self.temp.name) / 'data')
        self.job = self.store.enqueue('난 판교에서 일해', 'third-party')

    def caps(self, **kwargs):
        return Capabilities(self.store, None, {}, '', self.job, lambda *a, **k: None, **kwargs)

    def assert_pending(self, result):
        self.assertEqual((result['state'], result['saved'], result['requires_owner_approval'], result['refused_because']),
                         ('pending', False, True, THIRD_PARTY_MEMORY_WRITE))
        self.assertEqual(self.store.memories(), [], 'nothing entered canonical Memory')
        [candidate] = self.store.memory_candidates(MEMORY_OWNER, self.job)
        self.assertEqual((candidate['memory_key'], candidate['content'], candidate['state']), (KEY, VALUE, 'pending'))
        shown = json.dumps(worker_result('save_memory', result), ensure_ascii=False)
        self.assertEqual(json.loads(shown)['remembered'], False, 'the third party is told it is not remembered yet')

    def test_a_delegated_specialist_write_stays_a_pending_candidate(self):
        result = self.caps(delegated=True, allowed_tools=['save_memory']).execute('save_memory', {'memory_key': KEY, 'content': VALUE})
        self.assert_pending(result)

    def test_a_package_tool_write_stays_a_pending_candidate(self):
        package = {'version': 1, 'id': 'notebook', 'enabled': True,
                   'tools': [{'id': 'notebook_remember', 'host_action': 'save_memory', 'mode': 'bounded_write'}], 'roles': []}
        caps = self.caps(packages=runtime_packages([package]), allowed_tools=['notebook_remember', 'save_memory'])
        result = caps.execute('notebook_remember', {'memory_key': KEY, 'content': VALUE})
        self.assert_pending(result)
        self.assertTrue(caps.third_party_memory_write('notebook_remember'))
        self.assertFalse(caps.third_party_memory_write('save_memory'))

    def test_the_owner_worker_itself_saves_directly(self):
        result = self.caps().execute('save_memory', {'memory_key': KEY, 'content': VALUE})
        self.assertEqual((result['state'], result['saved'], result['auto_saved']), ('current', True, True))
        self.assertEqual([m['content'] for m in self.store.memories()], [VALUE])
        self.assertEqual(self.store.memory_candidates(include_decided=True), [])
        shown = worker_result('save_memory', result)
        self.assertEqual((shown['remembered'], shown['content'], shown['replaced_previous']), (True, VALUE, False))
        self.assertNotIn('id', shown)

    def test_every_work_s_reply_tells_the_save_and_no_notice_is_promised(self):
        """#955 (owner 2026-11-17, C5 as amended): the reply says what it keeps in mind; nothing else tells it."""
        web = worker_result('save_memory', self.caps().execute('save_memory', {'memory_key': KEY, 'content': VALUE}))
        telegram = self.store.enqueue('난 판교에서 일해', 'tg-1', 'telegram:g1', 4242)
        caps = Capabilities(self.store, None, {}, '', telegram, lambda *a, **k: None)
        shown = worker_result('save_memory', caps.execute('save_memory', {'memory_key': KEY, 'content': VALUE + ' 본사'}))
        for result in (web, shown):
            self.assertTrue(result['remembered'])
            self.assertIn('say in passing', result['next'])
            self.assertNotIn('separate message', result['next'])
        self.assertIsNone(self.store.notification_of(telegram, MEMORY_SAVED_KIND))
        delegated = Capabilities(self.store, None, {}, '', telegram, lambda *a, **k: None, delegated=True,
                                 allowed_tools=['save_memory'])
        held = delegated.execute('save_memory', {'memory_key': KEY, 'content': VALUE + ' 분당'})
        self.assertNotIn('say in passing', json.dumps(worker_result('save_memory', held), ensure_ascii=False),
                         'a third-party write is still a candidate, not told as saved')

    def test_the_request_sentence_is_still_never_a_value(self):
        """#846 stays: the owner's own request sentence is the task, not a fact."""
        result = self.caps().execute('save_memory', {'memory_key': KEY, 'content': '난 판교에서 일해'})
        self.assertEqual((result['state'], result['refused_because']), ('refused', 'value-is-the-request'))
        self.assertEqual(self.store.memories(), [])


class Guidance(unittest.TestCase):
    def test_the_worker_is_told_it_is_remembered_at_once(self):
        [save_memory] = [tool['function'] for tool in DEFINITIONS if tool['function']['name'] == 'save_memory']
        self.assertIn('remembered at once, and your reply says in passing what you will keep in mind', save_memory['description'])
        self.assertIn('there is no separate notice', save_memory['description'])
        self.assertNotIn('asked with one tap', save_memory['description'])
        self.assertNotIn('candidate', save_memory['description'])
        self.assertIn('may be saved the same way, as what you inferred', save_memory['description'])
        self.assertIn('Never save a credential', save_memory['description'])
        self.assertNotIn('Never save an inference as a fact', save_memory['description'])
        self.assertIn("Never narrate Still, tools, approvals, candidates or other internal states", CORE_INSTRUCTIONS)


if __name__ == '__main__':
    unittest.main()
