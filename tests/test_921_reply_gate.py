"""#921 PR-5: real temporary stores, fixture decisions/workers, no model or CLI.

Build the Still-specific gate; adopt the existing orchestrator/executor harness,
Memory snapshot/search, recall_selector and delegation fallback. Python dictionaries,
tuples and positional slices supply the inventory; no SDK, protocol or retrieval
framework owns this call-specific Still routing invariant or needs a new dependency.
"""
from unittest import mock

from personal_agent import owner_model as om, preparations
from personal_agent.agent_runtime import MEMORY_OWNER, ToolError
from personal_agent.memory_service import MemoryService
from personal_agent.orchestrator import cut_profile_fact
from test_orchestrator import Harness, plan
import test_orchestrator as harness
from personal_agent.decision import audit_record


def reply(selector='', worker='codex', needs=''):
    return dict(plan(worker, ''), reply='DIRECT', recall_query=selector,
                brief={'notes': '', 'lens': '', 'needs': needs})


class ReplyGate(Harness):
    def script(self, *args, **kwargs):
        engine = super().script(*args, **kwargs)
        # Fixture calls use the same content-free audit shape as real adapters.
        for attr, kind in (('_structured', 'structured'), ('_judge', 'judge')):
            original = getattr(engine, attr)
            def audited(context, *args, _original=original, _kind=kind):
                result = _original(context, *args)
                if result is not None:
                    self.service.record_decision(audit_record(
                        context, _kind, result.outcome, {}, result.confidence, self.clock()))
                return result
            setattr(engine, attr, audited)
        return engine

    def save(self, key='profile.control', content='needle owner fact'):
        return self.store.save_memory(key, content, owner_id=MEMORY_OWNER)

    def raise_row(self, row):
        om.MemoryRaise(self.store).add([{'kind': om.RAISE_HELD, 'memory_id': row['id'], 'reason': 'check this'}])

    def open_ids(self):
        return set(om.MemoryRaise(self.store).open())

    def after_plan(self, action):
        prepare = self.service._prepare_worker_turn
        def prepared(run):
            result = prepare(run)
            action(run)
            return result
        return mock.patch.object(self.service, '_prepare_worker_turn', side_effect=prepared)

    def run_checked(self, request='request', *, key=None):
        """Observe the exact checked refresh and the commit boundary, not mocked inventory."""
        builds, dispatched, consumed = [], [], []
        original = self.service.profile_section
        def section(query, **kwargs):
            result = original(query, **kwargs)
            builds.append((query, kwargs, result))
            return result
        shown = om.MemoryRaise.shown
        def commit(raising, ids, work_id):
            consumed.append((list(ids), work_id, len(self.engine.turns), len(self.transport.bodies)))
            return shown(raising, ids, work_id)
        self.engine.before = lambda tools: dispatched.append(self.open_ids())
        with mock.patch.object(self.service, 'profile_section', side_effect=section), \
                mock.patch.object(om.MemoryRaise, 'shown', autospec=True, side_effect=commit):
            job, row = self.run_work(request, key=key)
        return job, row, builds, dispatched, consumed

    def withheld(self, job, new_count=1):
        receipts = [r for r in self.store.work_decisions(job) if r.get('route') == 'plan-reply-withheld']
        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        self.assertEqual(receipt['kind'], 'route')
        self.assertEqual(receipt['new_count'], new_count)
        self.assertGreaterEqual(receipt['matched_count'], new_count)
        self.assertFalse({'query', 'recall_query', 'items', 'profile', 'reply'} & receipt.keys())
        self.assertEqual(len(self.asked_plans), 1)
        self.assertEqual(len(self.asked_goals), 1)
        self.assertNotIn('plan_reading', self.asked_goals[0].facts)
        return receipt

    def test_empty_selector_uses_reply_without_refresh_worker_or_consumption(self):
        row = self.save(); self.raise_row(row)
        self.script([reply()])
        with mock.patch.object(self.service, 'worker_profile_snapshot', wraps=self.service.worker_profile_snapshot) as refresh:
            _, result, _, _, consumed = self.run_checked('needle')
        refresh.assert_not_called()
        self.assertEqual(result['response'], 'DIRECT')
        self.assertEqual(self.engine.turns, [])
        self.assertEqual(self.asked_goals, [])
        self.assertEqual(consumed, [])
        self.assertIn(row['id'], self.open_ids())

    def test_only_seen_hits_use_reply_with_no_added_call_or_consumption(self):
        row = self.save(); self.raise_row(row)
        self.script([reply('needle')])
        job, result, builds, _, consumed = self.run_checked('needle')
        self.assertEqual(result['response'], 'DIRECT')
        self.assertEqual(self.engine.turns, [])
        self.assertEqual(self.asked_goals, [])
        self.assertEqual(consumed, [])
        self.assertIn(row['id'], self.open_ids())
        self.assertEqual(len([b for b in builds if b[0] == 'needle needle']), 1)
        audit = self.service.work_information_use(job)
        self.assertEqual(audit['sent_to']['judgment_ai']['calls'], 1)
        self.assertEqual(audit['sent_to']['workers'], [])

    def test_new_profile_delegates_using_the_exact_checked_refresh(self):
        self.script([reply('needle')], goals=[True])
        def added(run):
            self.row = self.save(); self.raise_row(self.row)
        with self.after_plan(added):
            job, result, builds, dispatched, consumed = self.run_checked()
        self.withheld(job)
        refreshes = [b for b in builds if b[0] == 'needle request']
        self.assertEqual(len(refreshes), 1)
        self.assertIs(refreshes[0][1]['consume'], False)
        self.assertEqual(self.engine.turns[0]['context']['profile'], refreshes[0][2][0])
        self.assertEqual(result['response'], 'cli answer')
        self.assertEqual(consumed, [([self.row['id']], job, 0, 0)])
        self.assertNotIn(self.row['id'], dispatched[0])
        self.assertEqual(self.service.work_information_use(job)['sent_to']['judgment_ai']['calls'], 2)

    def test_new_older_record_withholds_and_sends_whole_record(self):
        record = self.save('diary.2000-01-01', 'needle\nThe substantive old record.')
        self.script([reply('needle')], goals=[True])
        job, result, builds, _, consumed = self.run_checked()
        self.withheld(job)
        self.assertEqual(result['response'], 'cli answer')
        refresh = [b[2] for b in builds if b[0] == 'needle request']
        self.assertEqual(len(refresh), 1)
        item = next(i for i in refresh[0][1] if i['id'] == record['id'])
        self.assertTrue(item['matched'])
        self.assertEqual(item['digest'], record['content_digest'])
        self.assertIn(item['line'], self.engine.turns[0]['context']['profile'])
        self.assertEqual(consumed, [])

    def test_new_annotation_on_seen_fill_row_withholds(self):
        row = self.save(); self.raise_row(row)
        self.script([reply('needle')], goals=[True])
        job, _, builds, _, consumed = self.run_checked()
        receipt = self.withheld(job)
        self.assertEqual(receipt['seen_count'], 1)
        plan_items = builds[0][2][1]
        self.assertEqual(plan_items[0]['id'], row['id'])
        self.assertFalse(plan_items[0]['annotated'])
        self.assertEqual(consumed, [([row['id']], job, 0, 0)])

    def test_changed_digest_of_a_seen_row_counts_as_new(self):
        row = self.save()
        self.script([reply('needle')], goals=[True])
        def changed(run):
            with self.store.db() as db:
                db.execute("UPDATE memories SET content=?,content_digest=? WHERE id=?",
                           ('needle revised owner fact', 'revised-content-digest', row['id']))
        with self.after_plan(changed):
            job, _, _, _, _ = self.run_checked('needle')
        self.withheld(job)
        self.assertIn('revised owner fact', self.engine.turns[0]['context']['profile'])

    def test_hit_cut_from_the_plan_fact_counts_as_new(self):
        old = self.save('profile.target', 'needle owner fact')
        self.save('profile.newer', 'unrelated background')
        self.script([reply('needle')], goals=[True])
        with mock.patch('personal_agent.orchestrator.PROFILE_FACT_CHARS', 85):
            job, _, builds, _, _ = self.run_checked()
        self.withheld(job)
        self.assertNotIn('profile.target', self.asked_plans[0][0].facts['owner_profile'])
        self.assertIn(old['id'], [i['id'] for i in builds[0][2][1]])
        self.assertIn('profile.target', self.engine.turns[0]['context']['profile'])

    def test_saturated_request_snapshot_misses_owners_row_until_planned_recall(self):
        self.save('profile.target', 'needle owner preference')
        for index in range(90):
            self.save(f'profile.fill.{index:03}', 'background ' + 'x' * 180)
        self.script([reply('needle')], goals=[True])
        job, _, builds, _, _ = self.run_checked()
        self.withheld(job)
        self.assertNotIn('profile.target:', builds[0][2][0])
        self.assertNotIn('profile.target:', self.asked_plans[0][0].facts['owner_profile'])
        self.assertIn('profile.target:', self.engine.turns[0]['context']['profile'])

    def test_differing_earlier_exchanges_are_not_items(self):
        # Actual prior Works/messages; unrelated exchanges push the hit outside
        # the 16-message recent window used by the existing recall reader.
        for index in range(11):
            words = 'needle from the old conversation' if index == 0 else f'unrelated filler {index}'
            work = self.store.enqueue(words, f'prior-{index}', owner_typed=True, owner_words=True)
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='succeeded',created=? WHERE id=?", (index+1, work))
                db.executemany('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                               [('user', words, 'web', index+1, work),
                                ('assistant', 'recorded earlier', 'web', index+1, work)])
        self.script([reply('needle')])
        _, result, builds, _, consumed = self.run_checked()
        self.assertEqual(result['response'], 'DIRECT')
        self.assertNotIn('needle from the old conversation', builds[0][2][0])
        self.assertTrue(any('needle from the old conversation' in b[2][0] for b in builds))
        self.assertTrue(all(not b[2][1] for b in builds))
        self.assertEqual(self.engine.turns, [])
        self.assertEqual(consumed, [])

    def test_equal_digest_early_reply_keeps_its_own_frozen_row_revision(self):
        old = self.save()
        self.script([reply('needle')], goals=[True])
        prepare = self.service._prepare_worker_turn
        def prepared(run):
            # Build the actual speculative call through the service's profile path.
            early = self.service._early_plan.result()
            self.assertIn(old['id'], [i['id'] for i in early['profile_items']])
            # Same rendered text/date but a new row revision. Equal text is insufficient.
            with self.store.db() as db:
                db.execute('UPDATE memories SET id=? WHERE id=?', ('replacement-row-id', old['id']))
            self.service._early_plan = harness.EarlyPlan.future(early)
            result = prepare(run)
            self.assertTrue(run.orchestration.early_used)
            self.assertEqual([i['id'] for i in run.orchestration.plan_inventory], [old['id']])
            return result
        with mock.patch.object(self.service, '_prepare_worker_turn', side_effect=prepared):
            job, _, _, _, _ = self.run_checked()
        self.withheld(job)
        self.assertEqual(len(self.engine.turns), 1)

    def test_steer_still_refuses_direct_reply(self):
        self.script([reply('needle')], goals=[True])
        with self.after_plan(lambda run: setattr(run.attempt, 'steer', True)):
            job, result, _, _, _ = self.run_checked()
        self.assertEqual(result['response'], 'cli answer')
        self.assertFalse(any(r.get('route') == 'plan-reply-withheld' for r in self.store.work_decisions(job)))

    def test_steer_arriving_during_refresh_also_refuses_reply(self):
        self.script([reply('needle')], goals=[True])
        original = self.service.worker_profile_snapshot
        steers = []
        def refresh(*args, **kwargs):
            result = original(*args, **kwargs)
            steers.append('owner correction')
            return result
        with mock.patch.object(self.service, 'worker_profile_snapshot', side_effect=refresh), \
                mock.patch.object(self.store, 'work_steer_texts', side_effect=lambda *_: list(steers)):
            _, result, _, _, _ = self.run_checked()
        self.assertEqual(result['response'], 'cli answer')
        self.assertEqual(len(self.engine.turns), 1)

    def test_preparation_disables_selector_and_keeps_reply(self):
        self.save('diary.2000-01-01', 'needle old record')
        self.script([reply('needle')])
        # Keep ordinary Work admission; set the accepted preparation identity for its
        # context/refresh paths so no scheduler, time or new preparation is involved.
        with self.after_plan(lambda run: run.job.update(request_key=preparations.request_key('fixture', 30))), \
                mock.patch.object(self.service, 'day_plan_run', return_value=False):
            _, result, builds, _, consumed = self.run_checked()
        self.assertEqual(result['response'], 'DIRECT')
        self.assertFalse(any(i['kind'] == 'record' for i in builds[-1][2][1]))
        self.assertEqual(self.engine.turns, [])
        self.assertEqual(consumed, [])

    def test_final_cli_cut_consumes_only_complete_sent_annotations(self):
        self.script([reply('needle')], goals=[True])
        def added(run):
            self.rows = [self.save(f'profile.fact.{i}', 'needle '+str(i)+'x'*175) for i in range(12)]
            for row in self.rows: self.raise_row(row)
        with self.after_plan(added), mock.patch('personal_agent.work_executor.MAX_PROMPT_BYTES', 1900):
            job, _, builds, _, consumed = self.run_checked()
        self.withheld(job, new_count=12)
        final = self.engine.turns[0]['context']
        self.assertIn('profile', final['cut_to_fit'])
        sent_lines = final.get('profile', '').splitlines()
        items = builds[-1][2][1]
        expected = {i['id'] for i in items if i['line'] in sent_lines}
        self.assertTrue(expected)
        self.assertLess(len(expected), len(self.rows))
        self.assertEqual(set(consumed[0][0]), expected)
        self.assertEqual(self.open_ids(), {r['id'] for r in self.rows} - expected)

    def test_bare_request_fallback_consumes_no_annotations(self):
        self.script([reply('needle')], goals=[True])
        def added(run):
            self.row = self.save(); self.raise_row(self.row)
        with self.after_plan(added):
            job, _, _, _, consumed = self.run_checked('𐀀' * 11_998, key='921-bare')
        self.withheld(job)
        self.assertIsNone(self.engine.turns[0]['context'])
        self.assertEqual(consumed, [])
        self.assertIn(self.row['id'], self.open_ids())

    def test_api_dispatch_consumes_the_same_refresh_and_needs_alone_reaches_judgment(self):
        self.use_main('openai')
        for needs in ('', 'Consider the available facts.'):
            with self.subTest(needs=needs):
                self.script([reply('needle', worker='openai', needs=needs)], goals=[True])
                def added(run):
                    self.row = self.save('profile.api', 'needle current fact'); self.raise_row(self.row)
                before = len(self.transport.bodies)
                with self.after_plan(added):
                    job, _, _, _, consumed = self.run_checked(key='api-'+needs)
                self.assertTrue(consumed)
                self.assertEqual(consumed[0][0], [self.row['id']])
                self.assertEqual(consumed[0][3], before)
                facts = self.asked_goals[0].facts
                self.assertEqual(facts.get('plan_reading'), needs or None)
                self.assertIn('profile.api:', facts['owner_context'])

    def test_api_budget_refusal_before_dispatch_consumes_nothing(self):
        self.use_main('openai')
        self.script([reply('needle', worker='openai')])
        def added(run):
            self.row = self.save(); self.raise_row(self.row)
        api = self.service._run_api_attempt
        def refused(run):
            with mock.patch.object(run.work_budget, 'spend_turn', side_effect=ToolError('exhausted', 'turn_limit')):
                return api(run)
        with self.after_plan(added), mock.patch.object(self.service, '_run_api_attempt', side_effect=refused):
            job, _, _, _, consumed = self.run_checked()
        self.assertEqual(self.transport.bodies, [])
        self.assertEqual(consumed, [])
        self.assertIn(self.row['id'], self.open_ids())
        self.assertTrue(any(r.get('route') == 'plan-reply-withheld' for r in self.store.work_decisions(job)))


class Inventory(Harness):
    def test_snapshot_metadata_and_compatible_text_apis(self):
        row = self.store.save_memory('profile.control', 'needle fact')
        memory = MemoryService(self.store, private_read_sink=MemoryService.NO_EGRESS_GUARD)
        hit = memory.profile_snapshot(MEMORY_OWNER, query='needle')['entries'][0]
        fill = memory.profile_snapshot(MEMORY_OWNER, query='unmatched')['entries'][0]
        self.assertTrue(hit['matched']); self.assertFalse(fill['matched'])
        self.assertEqual(hit['content_digest'], row['content_digest'])
        self.store.save_memory('diary.2000-01-01', 'needle\nrecord text')
        text, items = self.service.profile_section('needle', consume=False)
        self.assertEqual(text, self.service.owner_profile_snapshot('needle'))
        self.assertEqual([i['kind'] for i in items], ['profile', 'record'])
        for item in items:
            self.assertEqual(text[item['start']:item['start']+len(item['line'])], item['line'])
        records, _ = memory.records_block(MEMORY_OWNER, 'needle', with_items=True)
        self.assertEqual(records, memory.records_block(MEMORY_OWNER, 'needle'))

    def test_redaction_precedes_cut_and_records_are_never_partly_inventoried(self):
        self.store.secret('model_key', 'fixture-secret-value')
        row = self.store.save_memory('profile.control', 'fixture-secret-value needle')
        record = self.store.save_memory('diary.2000-01-01', 'needle\nsecond line\nlast line')
        text, items = self.service.profile_section('needle', consume=False)
        self.assertNotIn('fixture-secret-value', text)
        self.assertIn('[redacted]', text)
        record_item = next(i for i in items if i['id'] == record['id'])
        cut, kept = cut_profile_fact(text, items, limit_chars=record_item['start']+len('[record of 2000-01-01]\nneedle'))
        self.assertNotIn('[record of', cut)
        self.assertEqual([i['id'] for i in kept], [row['id']])
        self.assertEqual(kept[0]['line'], text.splitlines()[0])
        self.assertEqual(cut_profile_fact('no newline too long', limit_chars=2), ('', ()))


def test_direct_reply_keeps_recall_query_for_the_gate():
    """#921: a direct-reply plan still writes recall_query; the other text fields stay blank."""
    from personal_agent import prompt_files
    line = next(text for text in prompt_files.prompt('planner').split('\n') if text.startswith('Write the complete owner-facing reply'))
    assert 'every other text field except recall_query as ""' in line
    assert 'brief.notes, brief.lens, brief.needs and reason are ""' in line
    assert 'Write recall_query as usual' in line
    assert 'recall_query are ""' not in line
    assert 'so Still can check' not in line  # the plan need not know about the gate
