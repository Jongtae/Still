"""SIDE-ANSWER-01: model-free real-store/service races and tool-less boundaries."""
import io
import json
import threading
from unittest import mock

from personal_agent.decision import DecisionConfidence, FixtureDecisionEngine, OUTCOME_DECIDED, StructuredDecision, fixture_confidence
from personal_agent.quickstart import make_handler
from test_steer_running_work import _SteerCase, steer_engine

QUESTION = 'Explain why a triangle has three sides.'
ANSWER = 'A triangle is defined as a polygon with three sides.'


def result(answered=True, reply=ANSWER):
    return StructuredDecision(OUTCOME_DECIDED, {'answered': answered, 'reply': reply}, fixture_confidence())


class SideAnswerTests(_SteerCase):
    STEERS = False

    def setUp(self):
        super().setUp()
        self.side_calls = []
        self.side_questions = []
        self.side_script = lambda: result()
        def answer(context, question, schema):
            self.side_calls.append(context)
            self.side_questions.append(question)
            return self.side_script()
        self.side_engine = FixtureDecisionEngine(structured=answer)
        self.service.side_answer_engine_factory = lambda: self.side_engine

    def pair(self):
        self.typed('Complete the other task.')
        running = self.service._claim_queued_work()
        self.typed(QUESTION)
        return running, self.jobs()[QUESTION]

    def replies(self, work):
        with self.store.db() as db:
            return [r[0] for r in db.execute("SELECT content FROM messages WHERE job_id=? AND role='assistant'", (work['id'],))]

    def test_side_reply_uses_shared_default_voice_without_expanding_its_context(self):
        from personal_agent.agent_runtime import MEMORY_OWNER
        from personal_agent.memory_service import MemoryService
        from personal_agent.prompt_files import prompt
        preference = '일할 땐 짧게, 힘든 얘기엔 다정하게 말해 줘'
        MemoryService(self.store, private_read_sink=MemoryService.NO_EGRESS_GUARD).remember_profile(
            MEMORY_OWNER, 'settings', 'profile.preference.communication', preference)
        running, side = self.pair()
        self.service._side_answer(running, side)
        [question] = self.side_questions
        self.assertEqual(question.count(prompt('voice')), 1)
        [context] = self.side_calls
        self.assertEqual(set(context.facts), {'owner_message', 'another_task_in_progress', 'conversation'})
        self.assertNotIn(preference, context.render(), 'the tool-less lane still excludes Memory')

    def test_delivers_side_reply_while_real_worker_is_blocked_then_worker_finishes(self):
        entered, release = threading.Event(), threading.Event()
        def block(body):
            entered.set()
            self.assertTrue(release.wait(10))
            return {'content': 'The other task is complete.'}
        self.script = [block, {'content': 'The other task is complete.'}]
        self.typed('Complete the other task.')
        worker = threading.Thread(target=self.service.run_one)
        worker.start()
        try:
            self.assertTrue(entered.wait(10))
            self.typed_from_poll_thread(QUESTION)
            side = self.jobs()[QUESTION]
            self.assertEqual(side['status'], 'succeeded')
            self.assertTrue(worker.is_alive())
            # Delivered by the side lane itself: the work loop is still inside run_one.
            self.assertEqual(self.jobs()[QUESTION]['delivery'], 'sent')
            self.assertEqual(sum(ANSWER in t for t in self.texts()), 1)
            self.assertEqual(self.replies(side), [ANSWER])
            self.assertEqual([e['tool'] for e in self.events(side['id'], 'side_answer')], ['side_answer'])
            with self.store.db() as db:
                self.assertEqual([r[0] for r in db.execute('SELECT DISTINCT tool FROM tool_events WHERE job_id=?', (side['id'],))], ['side_answer'])
        finally:
            release.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        first = self.jobs()['Complete the other task.']
        self.assertEqual(first['status'], 'succeeded')
        self.service.deliver_one()
        self.assertEqual(len(self.replies(first)), 1)
        self.assertEqual(sum(ANSWER in t for t in self.texts()), 1)
        self.assertFalse(self.service.run_one())

    def test_declined_unavailable_malformed_exception_and_timeout_preserve_order(self):
        for index, script in enumerate((lambda: result(False), lambda: None,
                                      lambda: StructuredDecision(OUTCOME_DECIDED, {'answered': 'yes', 'reply': ANSWER}),
                                      lambda: (_ for _ in ()).throw(RuntimeError()),
                                      lambda: (_ for _ in ()).throw(TimeoutError()))):
            with self.subTest(index=index):
                # A fresh pair for each attempt; queued reservations are inspected inside the call.
                running_id = self.store.enqueue('Other task '+str(index), 'running-'+str(index), owner_typed=True)
                with self.store.db() as db:
                    db.execute("UPDATE jobs SET status='running' WHERE id=?", (running_id,))
                side_id = self.store.enqueue(QUESTION, 'side-'+str(index), owner_typed=True)
                tail_id = self.store.enqueue('Later message', 'tail-'+str(index), owner_typed=True)
                def attempt():
                    self.assertFalse(self.service._claim_queued_work())
                    self.assertEqual(self.store.job(side_id)['status'], 'queued')
                    return script()
                self.side_script = attempt
                self.assertTrue(self.service.judge_steer(running_id, side_id, QUESTION))
                self.assertEqual(self.store.job(side_id)['status'], 'queued')
                self.assertEqual(self.replies({'id': side_id}), [])
                with self.store.db() as db:
                    db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (running_id,))
                self.assertTrue(self.service.run_one())
                self.assertEqual(self.store.job(side_id)['status'], 'succeeded')
                self.assertEqual(self.store.job(tail_id)['status'], 'queued')
                self.assertTrue(self.service.run_one())
                self.assertEqual(self.store.job(tail_id)['status'], 'succeeded')
                # Side attempt and eventual worker share cumulative host turns.
                self.assertGreater(self.store.config('work_budget:'+side_id)['turns'], 1)

    def delayed(self):
        callbacks = []
        self.service.steer_spawn = callbacks.append
        running_id = self.store.enqueue('Other task', 'other', owner_typed=True)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (running_id,))
        side_id = self.store.enqueue(QUESTION, 'side', owner_typed=True)
        self.service.judge_steer(running_id, side_id, QUESTION)
        self.assertEqual(len(callbacks), 1)
        self.assertFalse(self.service._claim_queued_work())
        return side_id, callbacks[0]

    def test_cancel_during_flight_has_no_reply(self):
        side_id, complete = self.delayed()
        # Use the stored cancellation boundary, independent of surface controls.
        def cancel():
            with self.store.db() as db:db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (side_id,))
            return result()
        self.side_script = cancel
        complete()
        self.assertEqual(self.store.job(side_id)['status'], 'cancelled')
        self.assertEqual(self.replies({'id': side_id}), [])

    def test_main_claim_first_discards_side_commit(self):
        side_id, complete = self.delayed()
        # Another service has no in-memory reservation (restart/racing host).
        restarted = self.make_service()
        def claim():
            self.assertEqual(restarted._claim_queued_work()['id'], side_id)
            return result()
        self.side_script = claim
        complete()
        self.assertEqual(self.store.job(side_id)['status'], 'running')
        self.assertEqual(self.replies({'id': side_id}), [])
        self.assertEqual([e['status'] for e in self.events(side_id, 'side_answer')], ['discarded'])

    def test_side_commit_first_is_never_claimed(self):
        side_id, complete = self.delayed()
        complete()
        self.assertEqual(self.replies({'id': side_id}), [ANSWER])
        self.assertFalse(self.service._claim_queued_work())
        self.assertEqual(self.store.turn_provenance(side_id)['context_messages'], 0)
        self.assertEqual(self.store.config('work_budget:'+side_id)['turns'], 1)
        self.assertFalse(self.store.config('work_budget:'+side_id)['active'])

    def test_restart_runs_queued_turn_once_and_late_side_reply_is_discarded(self):
        side_id, complete = self.delayed()
        restarted = self.make_service()
        def restart_during_call():
            self.store.recover()
            self.assertEqual(self.store.job(side_id)['status'], 'queued')
            self.assertTrue(restarted.run_one())
            return result()
        self.side_script = restart_during_call
        complete()
        self.assertEqual(self.store.job(side_id)['status'], 'succeeded')
        self.assertEqual(len(self.replies({'id': side_id})), 1)
        self.assertFalse(restarted.run_one())
        self.assertEqual([e['status'] for e in self.events(side_id, 'side_answer')], ['discarded'])

    def test_open_owner_item_prevents_side_call(self):
        with mock.patch.object(self.service.owner_acts, 'open_items', return_value=[{'kind': 'pending'}]):
            running, side = self.pair()
        self.assertEqual(self.side_calls, [])
        self.assertEqual(self.store.job(side['id'])['status'], 'queued')

    def test_unavailable_steer_does_not_call_side_engine(self):
        self.service.use_decision_engine(steer_engine(None))
        running, side = self.pair()
        self.assertEqual(self.side_calls, [])
        self.assertEqual(self.store.job(side['id'])['status'], 'queued')

    def test_yes_still_goes_to_running_work(self):
        self.service.use_decision_engine(steer_engine(True))
        running, side = self.pair()
        self.assertEqual(self.side_calls, [])
        self.assertEqual(self.store.job(side['id'])['related_job_id'], running['id'])

    def test_only_one_side_answer_in_flight(self):
        side_id, complete = self.delayed()
        other = self.store.enqueue('One more', 'another', owner_typed=True)
        running = self.jobs()['Other task']
        self.assertFalse(self.service.start_side_answer(running, other))
        complete()
        self.assertEqual(len(self.side_calls), 1)

    def test_context_redaction_history_and_document_boundary(self):
        hidden = self.store.enqueue('private material', 'hidden')
        with self.store.db() as db:
            db.execute("INSERT INTO messages(role,content,channel,created,job_id) VALUES ('assistant','private answer','web',1,?)", (hidden,))
            db.execute("INSERT INTO messages(role,content,channel,created) VALUES ('user','safe previous question','web',2)")
            db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (hidden,))
        self.store.put('file_workspace_document_jobs', [hidden])
        self.store.secret('model_key', 'fixture-private-secret')
        with self.store.db() as db:
            db.execute("INSERT INTO messages(role,content,channel,created) VALUES ('user','fixture-private-secret','web',3)")
        with mock.patch.object(self.service, 'document_boundary', return_value={'requires_approval': True}):
            self.pair()
        [context] = self.side_calls
        self.assertNotIn('private answer', context.render())
        self.assertNotIn('fixture-private-secret', context.render())
        self.assertIn('safe previous question', context.render())
        self.assertIn('another_task_in_progress', context.facts)

    def test_secret_with_json_escapes_is_redacted_before_serialization(self):
        secret = 'fixture"secret\\value'
        self.store.secret('model_key', secret)
        with self.store.db() as db:
            db.execute("INSERT INTO messages(role,content,channel,created) VALUES ('user',?,'web',3)", ('my key is ' + secret,))
        self.pair()
        [context] = self.side_calls
        conversation = json.loads(context.facts['conversation'])
        self.assertFalse(any(secret in str(message.get('content')) for message in conversation))
        self.assertNotIn(secret, context.render())

    def test_side_reply_recorded_while_running_work_routes_keeps_its_request(self):
        self.script = [{'content': 'The other task is complete.'}, {'content': 'The other task is complete.'}]
        self.typed('Complete the other task.')
        original = self.service._prepare_worker_turn
        def prepare(run):
            # The side answer commits after the running Work was claimed and routed.
            self.typed(QUESTION)
            self.assertEqual(self.jobs()[QUESTION]['status'], 'succeeded')
            return original(run)
        with mock.patch.object(self.service, '_prepare_worker_turn', side_effect=prepare):
            self.assertTrue(self.service.run_one())
        first = self.jobs()['Complete the other task.']
        self.assertEqual(first['status'], 'succeeded')
        self.assertEqual(self.replies(first), ['The other task is complete.'])
        self.assertEqual(self.store.history(through=first['id'])[-1]['job_id'], first['id'])

    def test_cloud_cli_side_answer_records_the_actual_recipient(self):
        class Cloud:
            provider = 'bedrock'
            def destination(self):
                return 'bedrock-runtime.us-east-1.amazonaws.com'
            def record(self):
                return {'cloud_provider': 'bedrock', 'cloud_destination': self.destination(),
                        'cloud_profile_revision': 3, 'cloud_credentials': ['AWS_ACCESS_KEY_ID']}
        self.store.put('subscription_engine', {'id': 'claude-code'})
        self.side_script = lambda: StructuredDecision(OUTCOME_DECIDED, {'answered': True, 'reply': ANSWER}, DecisionConfidence(
            1.0, provider='bedrock', destination='bedrock-runtime.us-east-1.amazonaws.com', cloud_profile='bedrock:3'))
        with mock.patch.object(self.service.execution_adapter, 'cloud_profile', create=True, return_value=Cloud()):
            running, side = self.pair()
        record = self.store.turn_provenance(side['id'])
        [worker] = record['workers']
        self.assertEqual((worker['provider'], worker['destination']), ('bedrock', 'bedrock-runtime.us-east-1.amazonaws.com'))
        self.assertEqual((record['provider'], record['cloud_provider'], record['cloud_profile_revision']), ('bedrock', 'bedrock', 3))
        self.assertEqual(self.replies(side), [ANSWER])

    def test_web_handler_calls_consider_steer_without_socket(self):
        handler = object.__new__(make_handler(self.service))
        handler.path = '/api/chat'
        payload = json.dumps({'message': QUESTION, 'request_key': 'web-direct'}).encode()
        handler.headers = {'Content-Length': str(len(payload)), 'Content-Type': 'application/json'}
        handler.rfile = io.BytesIO(payload)
        handler.valid_host = lambda: True
        handler.family_gate = handler.remote_login_gate = handler.phone_input_gate = lambda path: False
        handler.auth = lambda: True
        handler.csrf = lambda: True
        handler.reply = mock.Mock()
        with mock.patch.object(self.service, 'consider_steer') as consider:
            handler.do_POST()
        self.assertEqual(handler.reply.call_args.args[0], 202)
        task_id = handler.reply.call_args.args[1]['id']
        consider.assert_called_once_with(task_id, QUESTION)

    def test_factory_uses_main_api_model_and_key_with_no_tools(self):
        from personal_agent.providers import ModelAdapter
        captured = []
        def transport(url, body, headers=None, timeout=60):
            captured.append((url, body, headers))
            return {'choices': [{'message': {'tool_calls': [{'id': 'shape', 'type': 'function', 'function': {
                'name': 'decide', 'arguments': json.dumps({'answered': True, 'reply': ANSWER, 'confidence': 1.0})}}]}}]}
        self.service.adapter = ModelAdapter(transport)
        self.store.put('decision_route', {'transport': 'direct_api', 'provider': 'openai', 'requested_model': 'judgment-only'})
        self.service.side_answer_engine_factory = self.service.decision_routes.main_answer_engine
        self.pair()
        [(url, body, headers)] = captured
        self.assertEqual(body['model'], 'test-model')
        self.assertTrue(url.startswith('https://example.test/v1'))
        # The existing API schema adapter uses one inert output function.
        # It has no executor and offers no host capability.
        self.assertEqual([tool['function']['name'] for tool in body['tools']], ['decide'])
        self.assertEqual(self.jobs()[QUESTION]['response'], ANSWER)

    def test_factory_cli_uses_main_model_and_existing_toolless_argv(self):
        from personal_agent import decision_routes
        from personal_agent.decision_adapters import SubscriptionCliDecisionEngine
        from test_decision_routes import PLAN
        for engine_id in ('claude-code', 'codex'):
            with self.subTest(engine=engine_id):
                self.store.put('subscription_engine', {'id': engine_id})
                self.store.put('subscription_engine_models', {engine_id: 'main-model'})
                self.store.put('decision_route', {'transport': 'subscription_cli', 'engine': engine_id,
                    'model_policy': 'explicit', 'requested_model': 'judgment-model', 'fingerprint': 'qualified',
                    'codex_disabled_features': PLAN})
                routes = self.service.decision_routes
                with mock.patch.object(decision_routes, 'cli_fingerprint', return_value='qualified'), \
                     mock.patch.object(routes, '_cloud_identity', return_value='subscription'), \
                     mock.patch.object(routes, '_codex_instructions_qualified', return_value=True), \
                     mock.patch.object(routes, '_codex_strict_recorded', return_value=True), \
                     mock.patch.object(routes, '_codex_strict_stale', return_value=[]):
                    engine = routes.main_answer_engine()
                    self.assertIsInstance(engine, SubscriptionCliDecisionEngine)
                    argv = engine.argv('/fixture-cli', 'question', '/schema', {})
                self.assertEqual(engine.model, 'main-model')
                self.assertEqual(argv[argv.index('--model') + 1], 'main-model')
                if engine_id == 'claude-code':
                    self.assertEqual(argv[argv.index('--tools') + 1], '')
                    self.assertIn('--strict-mcp-config', argv)
                else:
                    disabled = [argv[i+1] for i, value in enumerate(argv) if value == '--disable']
                    self.assertEqual(set(disabled), set(PLAN))
                    self.assertIn('--ignore-user-config', argv)
        self.store.put('decision_route', {'transport': 'off'})
        self.assertIsNone(self.service.decision_routes.main_answer_engine())

    def test_eligibility_is_stored_state_and_explicit_commands_wait(self):
        running_id = self.store.enqueue('Other task', 'eligibility-running', owner_typed=True)
        with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?", (running_id,))
        running = self.store.job(running_id)
        for key, message, typed, setup in (
            ('not-typed', QUESTION, False, lambda ident: None),
            ('explicit', '/help', True, lambda ident: None),
            ('photo', QUESTION, True, lambda ident: self.store.attach_telegram_photo(ident, 'fixture-photo')),
            ('photo-marker', QUESTION, True, lambda ident: self.store.mark_telegram_photo_attached(ident)),
            ('retry', QUESTION, True, lambda ident: self._retry_parent(ident, running_id)),
        ):
            with self.subTest(key=key):
                ident = self.store.enqueue(message, key, owner_typed=typed)
                setup(ident)
                self.assertFalse(self.service.start_side_answer(running, ident))
                self.assertEqual(self.store.job(ident)['status'], 'queued')
        self.assertEqual(self.side_calls, [])

    def _retry_parent(self, ident, parent):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET relation_kind='retry',related_job_id=? WHERE id=?", (parent, ident))

    def test_audit_and_transcript_roll_back_together(self):
        side_id, complete = self.delayed()
        original = self.store.merge_turn_provenance
        calls = []
        def fail_commit(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:raise RuntimeError('fixture commit failure')
            return original(*args, **kwargs)
        with mock.patch.object(self.store, 'merge_turn_provenance', side_effect=fail_commit):
            complete()
        self.assertEqual(self.store.job(side_id)['status'], 'queued')
        self.assertEqual(self.replies({'id': side_id}), [])
        self.assertNotIn('answered', [e['status'] for e in self.events(side_id, 'side_answer')])


    def test_web_null_chat_running_work_reaches_side_lane(self):
        running_id = self.store.enqueue('Web task in progress', 'web-running', owner_typed=True)
        with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?", (running_id,))
        side_id = self.store.enqueue(QUESTION, 'web-side', owner_typed=True)
        self.assertTrue(self.service.consider_steer(side_id, QUESTION))
        self.assertEqual(self.store.job(side_id)['response'], ANSWER)
        self.assertEqual(self.store.job(running_id)['status'], 'running')

    def test_continuation_request_waits(self):
        from personal_agent.context_observations import continuation_key
        running_id = self.store.enqueue('Other task', 'continuation-other', owner_typed=True)
        with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?", (running_id,))
        ident = self.store.enqueue(QUESTION, continuation_key('fixture-request'), owner_typed=True)
        self.assertFalse(self.service.start_side_answer(self.store.job(running_id), ident))
        self.assertEqual(self.side_calls, [])

    def test_document_history_answer_keeps_boundary_for_later_turns(self):
        hidden = self.store.enqueue('Private material', 'document-origin')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (hidden,))
            db.execute("INSERT INTO messages(role,content,channel,created,job_id) VALUES ('assistant','private answer','web',1,?)", (hidden,))
        self.store.put('file_workspace_document_jobs', [hidden])
        with mock.patch.object(self.service, 'document_boundary', return_value={'requires_approval': False}):
            running, side = self.pair()
        self.assertIn('private answer', self.side_calls[0].render())
        self.assertIn(side['id'], self.store.config('file_workspace_document_jobs'))

    def test_new_owner_item_during_side_call_prevents_commit(self):
        side_id, complete = self.delayed()
        def pending():
            self.service.owner_acts.open_items = lambda job: [{'kind': 'pending'}]
            return result()
        self.side_script = pending
        complete()
        self.assertEqual(self.store.job(side_id)['status'], 'queued')
        self.assertEqual(self.replies({'id': side_id}), [])

    def test_web_enqueue_survives_steer_failure(self):
        handler = object.__new__(make_handler(self.service))
        handler.path = '/api/chat'
        payload = json.dumps({'message': QUESTION, 'request_key': 'web-failure'}).encode()
        handler.headers = {'Content-Length': str(len(payload)), 'Content-Type': 'application/json'}
        handler.rfile = io.BytesIO(payload)
        handler.valid_host = lambda: True
        handler.family_gate = handler.remote_login_gate = handler.phone_input_gate = lambda path: False
        handler.auth = lambda: True
        handler.reply = mock.Mock()
        with mock.patch.object(self.service, 'consider_steer', side_effect=RuntimeError()):
            handler.do_POST()
        self.assertEqual(handler.reply.call_args.args[0], 202)
        self.assertEqual(self.store.job(handler.reply.call_args.args[1]['id'])['status'], 'queued')


    def test_side_lane_cannot_enter_worker_or_settlement_paths(self):
        side_id, complete = self.delayed()
        with mock.patch.object(self.service, '_after_work', side_effect=AssertionError('worker phase')), \
             mock.patch.object(self.service, '_prepare_worker_turn', side_effect=AssertionError('worker phase')), \
             mock.patch.object(self.service.owner_acts, 'settle', side_effect=AssertionError('owner settlement')), \
             mock.patch.object(self.service.owner_model, 'enqueue', side_effect=AssertionError('owner upkeep')):
            # Holding the worker lock also proves the side lane never takes it.
            with self.service.worker_lock:
                complete()
        self.assertEqual(self.replies({'id': side_id}), [ANSWER])


    def test_unbuildable_engine_leaves_job_for_original_worker(self):
        side_id, complete = self.delayed()
        self.service.side_answer_engine_factory = lambda: None
        complete()
        self.assertEqual(self.store.job(side_id)['status'], 'queued')
        self.assertEqual(self.side_calls, [])
        self.assertEqual([e['status'] for e in self.events(side_id, 'side_answer')], ['unavailable'])
        self.assertTrue(self.service.run_one())
        self.assertEqual(len(self.replies({'id': side_id})), 1)
