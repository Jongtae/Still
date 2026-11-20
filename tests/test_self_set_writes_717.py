"""Model-free #717 authority and dispatch boundary tests."""
import json
from unittest import mock

from personal_agent import preparations as prep
from personal_agent.api_requests import ApiRequests, ApiError, save_slot
from personal_agent.browser_session import step_binding
from personal_agent.agent_runtime import ToolError
from personal_agent.settings_orchestrator import SettingsError, direct_request_setting
from personal_agent.self_set_writes import allowance, set_allowance
from personal_agent.context_observations import CONTINUATION_KEY_PREFIX
from test_scheduled_runs_ask_821 import _Runs
from test_owner_settings_conversation import _Case


class Settings(_Case):
    def setUp(self):
        super().setUp()
        save_slot(self.store,'notes',['notes.test'],'secret-notes-123456789',money=False)

    def draft(self, op='allow'):
        return self.settings.propose('owner','http','write_allowances',op,'slot:notes')

    def confirm(self, draft):
        return self.settings.confirm('owner','http',draft['draft_id'],draft['digest'])

    def test_exact_owner_confirmation_revision_and_revoke(self):
        self.assertFalse(allowance(self.service,'slot:notes')['enabled'])
        first, stale = self.draft(), self.draft()
        with self.assertRaises(SettingsError):
            self.settings.confirm('owner','http',first['draft_id'],'wrong')
        self.confirm(first)
        with self.assertRaises(SettingsError):self.confirm(stale)
        with self.assertRaises(SettingsError):self.confirm(first)
        self.confirm(self.draft('revoke'))
        self.assertFalse(allowance(self.service,'slot:notes')['enabled'])
        history=self.store.config('write_allowances_history')
        self.assertEqual([r['revision'] for r in history],[1,2])
        self.assertEqual(history[0]['authorized_by'],'owner')
        self.assertEqual(history[0]['connection'],'slot:notes')

    def test_stale_connection_does_not_block_conversation_revocation(self):
        self.confirm(self.draft())
        revoke = self.draft('revoke')
        save_slot(self.store,'notes',['notes.test'],'replacement-secret-123456789',money=False)
        self.confirm(revoke)
        self.assertFalse(allowance(self.service,'slot:notes')['enabled'])

    def test_ai_cannot_apply_enable_and_continuation_replay_cannot_draft(self):
        self.assertFalse(direct_request_setting('write_allowances','allow','slot:notes'))
        args={'category':'write_allowances','setting':'allow','value':'slot:notes'}
        job=self.store.job(self.store.enqueue('allow changes','typed',owner_typed=True))
        result=self.service.settings_tools(job)('settings_change',args)
        self.assertTrue(result['requires_owner_confirmation'])
        self.assertFalse(allowance(self.service,'slot:notes')['enabled'])
        for key,typed in [('replay',False),(CONTINUATION_KEY_PREFIX+'continued',True)]:
            job=self.store.job(self.store.enqueue('allow changes',key,owner_typed=typed))
            with self.assertRaises(SettingsError):self.service.settings_tools(job)('settings_change',args)
        job=self.store.job(self.store.enqueue('allow changes',prep.request_key('x',1000)))
        self.assertFalse(self.service.settings_tools(job)('settings_change',args)['applied'])


class Writes(_Runs):
    def setUp(self):
        super().setUp()
        save_slot(self.store,'notes',['notes.test'],'secret-notes-123456789',money=False)
        self.sent=[]

    def api(self,job):
        return ApiRequests(self.store,job['id'],approvals=self.service.browser_approvals_for(job),
            transport=lambda *a,**k:self.sent.append(a) or (200,{},b'{}'))

    def args(self,body='{}'):
        # #953: an approval request carries the AI's words.
        return {'slot':'notes','url':'https://notes.test/items','method':'POST','body':body,'effect':'mutate',
                'approval_note':'노트에 한 줄을 남겨요'}

    def started_self(self):
        return self.started(accepted_by=prep.ACCEPTED_DREAM)

    @staticmethod
    def encoded_bodies(value):
        # Encode every character: quote() leaves letters and digits unchanged.
        return ('{"note":"' + ''.join('\\u%04x' % ord(c) for c in value) + '"}',
                '{"note":"' + ''.join('%%%02X' % ord(c) for c in value) + '"}')

    def assert_no_write_approval(self, job):
        self.assertIsNone(self.service._browser_request(job['id']))
        self.assertEqual(self.service.browser_step_requests(), [])
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tool_events WHERE tool='self_set_write'").fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_approvals WHERE work_key=?',
                                        (job['id'],)).fetchone()[0], 0)

    def test_encoded_api_body_never_enters_approval_or_continuation(self):
        _, job = self.started_self()
        bodies = self.encoded_bodies('secret-notes-123456789')
        with mock.patch.object(self.service.decision_judge, 'connector_effect', return_value='irreversible'):
            for allowed in (False, True):
                if allowed:
                    set_allowance(self.service, 'slot:notes', True, 0, 'owner')
                for body in bodies:
                    with self.subTest(allowed=allowed, encoding=bodies.index(body)):
                        with self.assertRaises(ApiError) as raised:
                            self.api(job).call(self.args(body))
                        self.assertEqual(raised.exception.code, 'api_approval_unrepresentable')
                        self.assertIs(getattr(raised.exception, 'ran', None), False)
                        self.assert_no_write_approval(job)
                        self.assertEqual(self.sent, [])
            set_allowance(self.service, 'slot:notes', False, 1, 'owner')
            clean = '{"note":"reviewable value"}'
            with self.assertRaises(ApiError) as raised:
                self.api(job).call(self.args(clean))
            self.assertEqual(raised.exception.code, 'approval_required')
        offered = self.service._browser_request(job['id'])
        self.assertIn(clean, offered['label'])
        self.ended_waiting(job)
        self.assertTrue(self.service._decide_browser_step(job['id'], True, offered['offer_digest'])['resumed'])
        prompt = self.service.action_continuation_note(job)
        self.assertIn(clean, prompt)
        approvals = json.dumps(self.service.browser_step_requests())
        for value in ('secret-notes-123456789', *bodies):
            self.assertNotIn(value, prompt)
            self.assertNotIn(value, approvals)

    def test_encoded_in_code_body_never_enters_approval_or_continuation(self):
        from personal_agent.api_requests import slots
        from personal_agent.browser_jar import MemoryKey
        from personal_agent.code_run import CodeRunner
        self.store.api_slot_key = MemoryKey()
        value = 'fixture-account-123456789'
        save_slot(self.store, 'code-notes', ['notes.test'], {'ACCOUNT': value}, money=False,
                  encrypted=True, code={'fields': [{'name': 'ACCOUNT', 'use': 'in_code'}], 'reads': []})
        _, job = self.started_self()
        runner = CodeRunner(self.store, job['id'], approvals=self.service.browser_approvals_for(job))
        runner._owners = {'notes.test': [('code-notes', slots(self.store)['code-notes'])]}
        runner._fields = []
        runner._note = '노트에 한 줄을 남겨요'  # #953: an approval request carries the AI's words
        request = {'method': 'POST', 'host': 'notes.test', 'path': '/items', 'query': ''}
        bodies = self.encoded_bodies(value)
        with mock.patch.object(self.service.decision_judge, 'connector_effect', return_value='irreversible'):
            for allowed in (False, True):
                if allowed:
                    set_allowance(self.service, 'slot:code-notes', True, 0, 'owner')
                for body in bodies:
                    with self.subTest(allowed=allowed, encoding=bodies.index(body)):
                        permitted, reason = runner._decide({**request, 'bodyText': body}, {'notes.test': []})
                        self.assertFalse(permitted)
                        self.assertIn('비밀값', reason)
                        self.assertEqual(runner._asked, [])
                        self.assertEqual(runner._claims, [])
                        self.assert_no_write_approval(job)
            set_allowance(self.service, 'slot:code-notes', False, 1, 'owner')
            clean = '{"note":"reviewable code value"}'
            self.assertFalse(runner._decide({**request, 'bodyText': clean}, {'notes.test': []})[0])
        offered = self.service._browser_request(job['id'])
        self.assertIn(clean, offered['label'])
        self.ended_waiting(job)
        self.assertTrue(self.service._decide_browser_step(job['id'], True, offered['offer_digest'])['resumed'])
        prompt = self.service.action_continuation_note(job)
        self.assertIn(clean, prompt)
        approvals = json.dumps(self.service.browser_step_requests())
        for text in (value, *bodies):
            self.assertNotIn(text, prompt)
            self.assertNotIn(text, approvals)

    def test_decoded_metadata_and_raw_values_are_checked_before_judgment(self):
        from personal_agent import self_set_writes
        _, job = self.started_self()
        binding = step_binding(job['id'], 'api_request', 'https://notes.test/items', 'target')
        body, percent_body = self.encoded_bodies('secret-notes-123456789')
        encoded = json.loads(percent_body)['note']
        inputs = {'connection': 'slot:notes', 'operation': 'POST', 'target': '/items'}
        known = self_set_writes.connections(self.service)
        cases = [({'description': body}, known),
                 ({'values': {'note': body}}, known),
                 ({'connection': 'slot:' + encoded}, known),
                 ({'target': '/items?note=' + encoded}, known),
                 ({'operation': encoded}, known),
                 ({'names': [percent_body]}, known),
                 ({}, {**known, 'slot:notes': {**known['slot:notes'], 'label': body}})]
        with mock.patch.object(self.service.decision_judge, 'connector_effect') as judge:
            for index, (changes, connections) in enumerate(cases):
                with self.subTest(field=index), mock.patch.object(self_set_writes, 'connections', return_value=connections):
                    with self.assertRaises(ToolError) as raised:
                        self.service.self_set_write(job, binding, **{**inputs, **changes})
                    self.assertEqual(raised.exception.code, 'api_approval_unrepresentable')
                    self.assertIs(raised.exception.ran, False)
                    self.assert_no_write_approval(job)
            judge.assert_not_called()

    def test_notice_uses_checked_connection_label_even_when_empty(self):
        from personal_agent import self_set_writes
        _, job = self.started_self()
        known = self_set_writes.connections(self.service)
        known['slot:notes']['label'] = ''
        binding = step_binding(job['id'], 'api_request', 'https://notes.test/items', 'target')
        with mock.patch.object(self_set_writes, 'connections', return_value=known):
            set_allowance(self.service, 'slot:notes', True, 0, 'owner')
            ticket = self.service.self_set_write(job, binding, 'slot:notes', 'POST', '/items', effect='irreversible')
            known['slot:notes']['label'] = self.encoded_bodies('secret-notes-123456789')[1]
            with mock.patch.object(self.service, 'record_conversation_notice', wraps=self.service.record_conversation_notice) as notice:
                self.service.self_set_write_finished(job, ticket)
        self.assertEqual(ticket['target'], '/items')
        self.assertEqual(ticket['connection_label'], '')
        notice.assert_called_once()
        self.assertNotIn(known['slot:notes']['label'], notice.call_args.args[0])
        self.assertNotIn('secret-notes-123456789', notice.call_args.args[0])

    def test_browser_checks_raw_text_and_submit_url_before_display_minimization(self):
        from personal_agent import browser_session as bs
        from test_browser_session import FakeDriver, ORIGIN
        _, job = self.started_self()
        driver = FakeDriver()
        browser = bs.BrowserSession(lambda: driver, work_id=job['id'], approvals=self.service.browser_approvals_for(job))
        self.addCleanup(browser.close)
        browser.open({'url': ORIGIN + '/one-press', 'effect': 'navigate'})
        raw = {'url': ORIGIN + '/form', 'elements': [
            {'index': 0, 'role': 'textbox', 'name': 'Note', 'fieldName': 'note', 'form': 0, 'value': ''}]}
        with mock.patch.object(browser, '_snapshot', return_value=bs.mediate_snapshot(raw)), \
                mock.patch.object(self.service.decision_judge, 'connector_effect') as judge:
            for body in self.encoded_bodies('secret-notes-123456789'):
                with self.assertRaises(ToolError) as raised:
                    browser.type({'target': 'Note', 'text': body, 'effect': 'mutate'})
                self.assertEqual(raised.exception.code, 'api_approval_unrepresentable')
                self.assertIs(raised.exception.ran, False)
                self.assert_no_write_approval(job)
            encoded = json.loads(self.encoded_bodies('secret-notes-123456789')[1])['note']
            record = {'page': ORIGIN + '/form', 'action': ORIGIN + '/submit?note=' + encoded,
                      'method': 'POST', 'dom': 0, 'state': 'digest', 'payment': False, 'fieldNames': ['note']}
            driver.release_submit = mock.Mock()
            with self.assertRaises(ToolError) as raised:
                browser._cancelled_submit(record, 'submit')
            self.assertEqual(raised.exception.code, 'api_approval_unrepresentable')
            self.assert_no_write_approval(job)
            driver.release_submit.assert_not_called()
            judge.assert_not_called()
        self.assertEqual(driver.approved, [])

    def gmail_slot_revisions(self, grants):
        from types import SimpleNamespace
        from personal_agent import connection_slots
        from personal_agent.connector_contract import ConnectorState
        registry = mock.Mock()
        registry.status.side_effect = lambda owner, name: SimpleNamespace(
            state=ConnectorState.CONNECTED if name in grants else ConnectorState.DISCONNECTED,
            connection_revision=grants.get(name, ''))
        source = SimpleNamespace(connector_registry=registry, gmail_modify_oauth=object())
        return mock.patch.object(self.service, 'connection_slots',
                                 side_effect=lambda job: connection_slots.build(source, 'local-owner'))

    def test_subordinate_grant_changes_invalidate_settings_and_hold_new_writes(self):
        from personal_agent import self_set_writes
        _, job = self.started_self()
        for before in (None, 'filters-1'):
            with self.subTest(before=before):
                grants = {'gmail-modify': 'modify-1'}
                if before is not None:
                    grants['gmail-filters'] = before
                with self.gmail_slot_revisions(grants):
                    revision = allowance(self.service, 'slot:gmail')['revision']
                    set_allowance(self.service, 'slot:gmail', True, revision, 'owner')
                    grants['gmail-filters'] = 'filters-2'
                    row = next(row for row in self_set_writes.settings(self.service)['connections']
                               if row['connection'] == 'slot:gmail')
                    self.assertFalse(row['enabled'])
                    self.assertTrue(row['reason'])
                    binding = step_binding(job['id'], 'api_request', 'https://gmail.googleapis.com',
                                           'settings/filters', argument=str(before))
                    with self.assertRaises(ToolError) as raised:
                        self.service.self_set_write(job, binding, 'slot:gmail', 'POST', 'settings/filters',
                                                    effect='irreversible', request=False)
                    self.assertEqual(raised.exception.code, 'approval_required')

    def test_subordinate_grant_changes_refuse_previously_authorized_ticket(self):
        _, job = self.started_self()
        for before in (None, 'filters-1'):
            with self.subTest(before=before):
                grants = {'gmail-modify': 'modify-1'}
                if before is not None:
                    grants['gmail-filters'] = before
                with self.gmail_slot_revisions(grants):
                    revision = allowance(self.service, 'slot:gmail')['revision']
                    set_allowance(self.service, 'slot:gmail', True, revision, 'owner')
                    binding = step_binding(job['id'], 'api_request', 'https://gmail.googleapis.com',
                                           'settings/filters', argument=str(before))
                    ticket = self.service.self_set_write(job, binding, 'slot:gmail', 'POST', 'settings/filters',
                                                         effect='irreversible')
                    self.assertTrue(ticket['allowance'])
                    self.service.self_set_write_check(job, ticket)
                    grants['gmail-filters'] = 'filters-2'
                    with self.assertRaises(ToolError) as raised:
                        self.service.self_set_write_check(job, ticket)
                    self.assertEqual(raised.exception.code, 'allowance_changed')
        with self.store.db() as db:
            statuses = [row['status'] for row in db.execute(
                "SELECT status FROM tool_events WHERE job_id=? AND tool='self_set_write' ORDER BY id", (job['id'],))]
        self.assertEqual(statuses, ['not_run', 'not_run'])

    def test_slot_allowance_identity_preserves_no_grants_and_sorts_grants(self):
        from personal_agent import self_set_writes
        grants = {'gmail-modify': 'modify-1'}
        with self.gmail_slot_revisions(grants):
            slots = self.service.connection_slots({})
        with mock.patch.object(self.service, 'connection_slots', return_value=slots):
            slot = slots['gmail']
            set_allowance(self.service, 'slot:gmail', True, 0, 'owner')
            self.assertEqual(allowance(self.service, 'slot:gmail')['connection_revision'], 'modify-1')
            slot['write_grant_revisions'] = {}
            row = lambda: next(row for row in self_set_writes.settings(self.service)['connections']
                               if row['connection'] == 'slot:gmail')
            self.assertTrue(row()['enabled'])
            slot['write_grant_revisions'] = {'grant-b': 'b-1', 'grant-a': 'a-1'}
            set_allowance(self.service, 'slot:gmail', True, 1, 'owner')
            slot['write_grant_revisions'] = {'grant-a': 'a-1', 'grant-b': 'b-1'}
            slot.update(note='Updated display text', max_age_seconds=60, token=lambda: 'renewed-token')
            self.assertTrue(row()['enabled'])

    def test_unknown_waits_exact_approval_continues_once(self):
        row,job=self.started_self()
        args=self.args('{"note":"owner can review this value"}')
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value=None) as judge:
            with self.assertRaises(ApiError):self.api(job).call(args)
            self.assertEqual(self.sent,[])
            request=self.service._browser_request(job['id'])
            self.assertEqual(request['state'],'requested')
            self.assertEqual(judge.call_args.args[1],['body'])
            self.assertNotIn('owner can review this value',str(judge.call_args))
            self.assertIn(args['body'],request['label'])
            self.ended_waiting(job)
            self.assertFalse(self.service._decide_browser_step(job['id'],True,'other')['approved'])
            approved=self.service._decide_browser_step(job['id'],True,request['offer_digest'])
            self.assertTrue(approved['approved'])
            self.assertTrue(approved['resumed'])
            self.assertFalse(self.service._decide_browser_step(job['id'],True,request['offer_digest'])['approved'])
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            self.api(job).call(args)
            self.assertEqual(len(self.sent),1)
            with self.assertRaises(ApiError):self.api(job).call(args)
            self.assertEqual(len(self.sent),1)

    def test_reversible_proceeds_unknown_and_irreversible_wait(self):
        _,job=self.started_self()
        for effect in ('reversible','irreversible',None):
            with mock.patch.object(self.service.decision_judge,'connector_effect',return_value=effect):
                if effect=='reversible':self.api(job).call(self.args('{"n":1}'))
                else:
                    with self.assertRaises(ApiError):self.api(job).call(self.args('{"n":2}'))
        self.assertEqual(len(self.sent),1)

    def test_api_connection_changed_during_judgment_dispatches_nothing(self):
        _,job=self.started_self()
        def changed(*args):
            save_slot(self.store,'notes',['notes.test'],'replacement-secret-123456789',money=False)
            return 'reversible'
        with mock.patch.object(self.service.decision_judge,'connector_effect',side_effect=changed):
            with self.assertRaises(ApiError):self.api(job).call(self.args())
        self.assertEqual(self.sent,[])

    def test_only_owner_button_on_the_offered_message_settles(self):
        from test_preparations import CHAT, GENERATION
        _,job=self.started_self()
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            with self.assertRaises(ApiError):self.api(job).call(self.args())
        self.ended_waiting(job)
        note=self.store.notification_of(job['id'],'browser_approval_needed')
        self.store.update_notification(note['id'],'sent',message_id=77,delivered=True)
        data=f"p7w:{note['id']}:approve"
        self.service.ingest_callback({'id':'foreign','from':{'id':999},'data':data,
            'message':{'message_id':77,'chat':{'id':CHAT,'type':'private'}}},GENERATION)
        self.tap(data,78)
        self.assertEqual(self.service._browser_request(job['id'])['state'],'requested')
        self.assertEqual(self.sent,[])
        self.tap(data,77)
        self.assertEqual(self.service._browser_request(job['id'])['state'],'issued')
        self.assertEqual(self.store.job(job['id'])['status'],'queued')
        self.tap(data,77)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tool_events WHERE job_id=? AND tool='action_continuation'",(job['id'],)).fetchone()[0],1)

    def test_allowed_once_notice_then_revocation_and_payment_floor(self):
        _,job=self.started_self()
        set_allowance(self.service,'slot:notes',True,0,'owner')
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            self.api(job).call(self.args())
            with self.assertRaises(ApiError):self.api(job).call(self.args())
            set_allowance(self.service,'slot:notes',False,1,'owner')
            with self.assertRaises(ApiError):self.api(job).call(self.args('{"n":2}'))
        self.assertEqual(len(self.sent),1)
        with self.store.db() as db:
            rows=[json.loads(row['detail']) for row in db.execute("SELECT detail FROM tool_events WHERE tool='self_set_write' AND status='observed'")]
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['revision'],1)
        set_allowance(self.service,'slot:notes',True,2,'owner')
        args={**self.args('{"n":3}'),'effect':'payment'}
        with self.assertRaises((ApiError,ToolError)):self.api(job).call(args)
        self.assertEqual(len(self.sent),1)

    def test_owner_accepted_and_typed_skip_new_judgment(self):
        _,scheduled=self.started()
        typed=self.store.job(self.store.enqueue('write','typed',owner_typed=True))
        for job in (scheduled,typed):
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            with mock.patch.object(self.service.decision_judge,'connector_effect',side_effect=AssertionError('must not judge')):
                self.api(job).call(self.args())
        self.assertEqual(len(self.sent),2)

    def test_expired_offer_or_changed_preparation_does_not_resume(self):
        _,job=self.started_self()
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            with self.assertRaises(ApiError):self.api(job).call(self.args())
        self.ended_waiting(job)
        row=self.service._browser_request(job['id'])
        with mock.patch('personal_agent.browser_signin.time.time',return_value=row['requested_at']+100000):
            self.assertFalse(self.service._decide_browser_step(job['id'],True,row['offer_digest'])['approved'])
        self.assertEqual(self.sent,[])

    def test_connector_owner_mutate_still_needs_reversibility_and_exact_input(self):
        from personal_agent import owner_mcp, trust_record
        from test_connect_effects import ACTION, INPUT
        _,job=self.started_self()
        self.service.set_ai_connections({'enabled':True})
        owner_mcp.set_confirmed(self.store,'codex',['synthetic'])
        target=trust_record.connection('codex','owner-mcp','synthetic')
        trust_record.set_operation(self.store,target,'apply_change','mutate')
        args={'engine':'codex','tool_name':ACTION,'input':INPUT}
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible') as judge:
            self.assertEqual(self.service.connector_permission(job,args)['behavior'],'deny')
            self.assertEqual(judge.call_args.args[1],sorted(INPUT))
            request=self.service._browser_request(job['id'])
            self.ended_waiting(job)
            self.assertTrue(self.service._decide_browser_step(job['id'],True,request['offer_digest'])['resumed'])
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            self.assertEqual(self.service.connector_permission(job,args)['behavior'],'allow')
            self.assertEqual(self.service.connector_permission(job,args)['behavior'],'deny')

    def test_browser_press_and_nonpayment_submit_unknown_wait_before_driver(self):
        from personal_agent import browser_session as bs
        from test_browser_session import FakeDriver, ORIGIN
        _,job=self.started_self()
        driver=FakeDriver()
        browser=bs.BrowserSession(lambda:driver,work_id=job['id'],approvals=self.service.browser_approvals_for(job))
        browser.open({'url':ORIGIN+'/one-press','effect':'navigate'})
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value=None):
            with self.assertRaises(ToolError):browser.click({'target':'메모 저장','effect':'mutate'})
        self.assertEqual(driver.approved,[])
        record={'page':ORIGIN+'/form','action':ORIGIN+'/submit','method':'POST','dom':0,
                'state':'digest-of-exact-values','payment':False,'fieldNames':['note']}
        driver.release_submit=mock.Mock(return_value={})
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible') as judge:
            with self.assertRaises(ToolError):browser._cancelled_submit(record,'submit')
            self.assertEqual(judge.call_args.args[1],['note'])
            self.assertNotIn(record['state'],str(judge.call_args))
        driver.release_submit.assert_not_called()
        self.assertEqual(self.service._browser_request(job['id'])['action'],'self_set_write')

    def test_approved_browser_submit_releases_once_without_payment_authority(self):
        from personal_agent import browser_session as bs
        from test_browser_session import FakeDriver, ORIGIN
        _,job=self.started_self()
        driver=FakeDriver()
        driver.release_submit=mock.Mock(return_value={})
        browser=bs.BrowserSession(lambda:driver,work_id=job['id'],approvals=self.service.browser_approvals_for(job))
        record={'page':ORIGIN+'/form','action':ORIGIN+'/submit','method':'POST','dom':0,
                'state':'exact-state','payment':False,'fieldNames':['note']}
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            with self.assertRaises(ToolError):browser._cancelled_submit(record,'submit')
            driver.release_submit.assert_not_called()
            self.ended_waiting(job)
            offered=self.service._browser_request(job['id'])
            self.assertTrue(self.service._decide_browser_step(job['id'],True,offered['offer_digest'])['resumed'])
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            with mock.patch.object(browser.approvals,'payment_autonomy',side_effect=AssertionError('not payment')):
                browser._cancelled_submit(record,'submit')
            with self.assertRaises(ToolError):browser._cancelled_submit(record,'submit')
        driver.release_submit.assert_called_once()
        with self.store.db() as db:
            self.assertEqual([row['status'] for row in db.execute("SELECT status FROM tool_events WHERE tool='self_set_write'")],['observed'])

    def test_browser_input_changed_during_judgment_does_not_use_old_approval(self):
        import copy
        from personal_agent import browser_session as bs
        from test_browser_session import FakeDriver, ORIGIN
        _,job=self.started_self()
        driver=FakeDriver()
        browser=bs.BrowserSession(lambda:driver,work_id=job['id'],approvals=self.service.browser_approvals_for(job))
        browser.open({'url':ORIGIN+'/one-press','effect':'navigate'})
        args={'target':'메모 저장','effect':'mutate'}
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            with self.assertRaises(ToolError):browser.click(args)
            self.ended_waiting(job)
            offered=self.service._browser_request(job['id'])
            self.assertTrue(self.service._decide_browser_step(job['id'],True,offered['offer_digest'])['resumed'])
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            original=browser._snapshot()
            changed=copy.deepcopy(original)
            changed['_states']={key:'changed during judgment' for key in original.get('_states',{})}
            changed['_states'][None]='changed during judgment'
            with mock.patch.object(browser,'_snapshot',side_effect=[original,changed]):
                with self.assertRaises(ToolError):browser.click(args)
            browser.click(args)
        self.assertEqual(len(driver.approved),1)

    def test_code_run_non_money_boundary_and_allowance(self):
        from personal_agent.code_run import CodeRunner
        from personal_agent.api_requests import slots
        _,job=self.started_self()
        runner=CodeRunner(self.store,job['id'],approvals=self.service.browser_approvals_for(job))
        runner._owners={'notes.test':[('notes',slots(self.store)['notes'])]}
        runner._fields=[]
        runner._note='노트에 한 줄을 남겨요'  # #953
        request={'method':'POST','host':'notes.test','path':'/items','query':'','bodyText':'{"title":"private value"}'}
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            self.assertFalse(runner._decide(request,{'notes.test':[]})[0])
            self.assertEqual(runner._asked,[{'method':'POST','host':'notes.test','path':'/items'}])
            set_allowance(self.service,'slot:notes',True,0,'owner')
            self.assertTrue(runner._decide(request,{'notes.test':[]})[0])
            self.assertFalse(runner._decide(request,{'notes.test':[]})[0])
        self.assertEqual(len(runner._claims),1)

    def test_allowance_notice_is_once_and_not_an_undo_claim(self):
        _,job=self.started_self()
        set_allowance(self.service,'slot:notes',True,0,'owner')
        binding=step_binding(job['id'],'api_request','https://notes.test/items','target',argument='body')
        ticket=self.service.self_set_write(job,binding,'slot:notes','POST','/items',effect='irreversible')
        with self.store.db() as db:
            before=db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0]
        self.service.self_set_write_finished(job,ticket)
        self.service.self_set_write_finished(job,ticket)
        with self.store.db() as db:
            after=db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0]
        self.assertEqual(after-before,1)

    def test_peer_send_waits_and_allowance_dispatches_only_once(self):
        import importlib.util
        if importlib.util.find_spec('a2a') is None:self.skipTest('optional A2A extra unavailable')
        import asyncio
        import httpx
        from cryptography.fernet import Fernet
        from google.protobuf.json_format import MessageToDict
        from a2a.server.routes import create_agent_card_routes
        from personal_agent.a2a_peers import A2APeers
        from test_a2a_compatibility import card, application
        _,job=self.started_self()
        self.service.record_turn_provenance(job['id'],owner_information={},context_messages=0)
        peers=A2APeers(self.service,secret_key=Fernet.generate_key())
        self.service.__dict__['a2a_peers']=peers
        remote_card=card('http://127.0.0.1/')
        peer=peers.register({'card':MessageToDict(remote_card)})['peer']['id']
        app=application(remote_card)
        app.router.routes.extend(create_agent_card_routes(remote_card))
        async def send(text="hello", message_id="exact-message"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                return await peers.send(peer,job['id'],text,message_id,http=http)
        with self.assertRaises(ToolError):asyncio.run(send())
        self.assertEqual(peers.conversations()['turns'],[])
        self.assertEqual(self.service._browser_request(job['id'])['state'],'requested')
        set_allowance(self.service,'peer:'+peer,True,0,'owner')
        self.assertEqual(asyncio.run(send())['answer'],'answer: hello')
        self.assertEqual(asyncio.run(send())['answer'],'answer: hello')
        self.assertEqual(len(peers.conversations()['turns']),1)

    def test_worker_preserves_only_nonpayment_form_names_across_wait(self):
        from test_browser_webkit import WorkerGuardLogicTests
        harness=WorkerGuardLogicTests()
        _bw,worker=harness.worker()
        worker.self_set_writes=True
        raw={'id':'one','dom':0,'method':'post','action':'https://site.test/send',
             'page':'https://site.test','state':'note=private-value', 'payment':False,'fieldNames':['note']}
        first=worker.take_cancelled(raw)
        self.assertNotIn('private-value',json.dumps(first))
        self.assertEqual(first['fieldNames'],['note'])
        self.assertIs(first['payment'],False)
        self.assertIsNone(worker.take_cancelled())
        self.assertIsNotNone(worker.held)
        with mock.patch('personal_agent.browser_worker.time.monotonic', return_value=worker._held_until+1):
            self.assertIsNone(worker.take_cancelled())
        self.assertIsNone(worker.held)
        worker.held=None
        self.assertIsNone(worker.take_cancelled())

    def test_unavailable_judge_changed_input_and_other_work_do_not_spend_approval(self):
        _,job=self.started_self()
        first=step_binding(job['id'],'api_request','https://notes.test/items','target',argument='first')
        second=step_binding(job['id'],'api_request','https://notes.test/items','target',argument='second')
        with mock.patch.object(self.service.decision_judge,'connector_effect',side_effect=RuntimeError('unavailable')):
            with self.assertRaises(ToolError):self.service.self_set_write(job,first,'slot:notes','POST','/items')
        self.ended_waiting(job)
        offered=self.service._browser_request(job['id'])
        other=self.store.enqueue('another work','other',owner_typed=True)
        self.assertFalse(self.service._decide_browser_step(other,True,offered['offer_digest'])['approved'])
        self.assertTrue(self.service._decide_browser_step(job['id'],True,offered['offer_digest'])['approved'])
        with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
        with self.assertRaises(ToolError):
            self.service.self_set_write(job,second,'slot:notes','POST','/items',effect='irreversible')
        self.assertFalse(self.service._decide_browser_step(job['id'],True,offered['offer_digest'])['approved'])
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tool_events WHERE tool='self_set_write'").fetchone()[0],0)

    def test_native_submit_guard_intercepts_only_self_set_nonpayment_posts(self):
        import shutil
        import subprocess
        from test_browser_webkit import WorkerGuardLogicTests
        node=shutil.which('node')
        if not node:self.skipTest('Node unavailable for worker JavaScript boundary')
        bw,_worker=WorkerGuardLogicTests().worker()
        fragment=bw.PRELUDE[bw.PRELUDE.index('const armSubmitGuard ='):bw.PRELUDE.index('const takeCancelled =')]
        harness=r'''
let current, listener, held;
class HTMLFormElement {}
const state=()=>current, holdsPayment=form=>form.payment;
const effective=form=>({method:form.method}), vet=()=>{}, allows=()=>null;
const hold=()=>{held++};
const window={addEventListener:(type,fn)=>{if(type==='submit')listener=fn}};
''' + fragment + r'''
const run=(selfSetWrites,method,payment)=>{
 current={selfSetWrites};held=0;armSubmitGuard();
 const form=Object.assign(new HTMLFormElement(),{method,payment});let prevented=false;
 listener({type:'submit',target:form,preventDefault(){prevented=true},stopImmediatePropagation(){}});
 return {prevented,held};
};
console.log(JSON.stringify([run(false,'post',false),run(true,'get',false),run(true,'post',false),
                           run(false,'post',true),run(true,'post',true)]));
'''
        result=subprocess.run([node,'-e',harness],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[
            {'prevented':False,'held':0},{'prevented':False,'held':0},
            {'prevented':True,'held':1},{'prevented':True,'held':1},{'prevented':True,'held':1}])

    def test_reconnected_mcp_server_does_not_inherit_allowance(self):
        from personal_agent import owner_mcp
        from test_connect_effects import ACTION, INPUT
        _,job=self.started_self()
        self.service.set_ai_connections({'enabled':True})
        owner_mcp.set_confirmed(self.store,'codex',['synthetic'])
        connection='connector:codex|owner-mcp|synthetic'
        set_allowance(self.service,connection,True,0,'owner')
        owner_mcp.set_confirmed(self.store,'codex',[])
        owner_mcp.set_confirmed(self.store,'codex',['synthetic'])
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            result=self.service.connector_permission(job,{'engine':'codex','tool_name':ACTION,'input':INPUT})
        self.assertEqual(result['behavior'],'deny')
        self.assertEqual(self.service._browser_request(job['id'])['state'],'requested')


class VerifierRegressions(_Runs):
    def setUp(self):
        super().setUp()
        save_slot(self.store,'notes',['notes.test'],'secret-notes-123456789',money=False)
        self.sent=[]
    api = Writes.api
    args = Writes.args
    started_self = Writes.started_self
    def events(self):
        with self.store.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM tool_events WHERE tool='self_set_write'")]

    def peer_fixture(self):
        import importlib.util
        if importlib.util.find_spec('a2a') is None:self.skipTest('optional A2A extra unavailable')
        import asyncio
        import httpx
        from cryptography.fernet import Fernet
        from google.protobuf.json_format import MessageToDict
        from a2a.server.routes import create_agent_card_routes
        from personal_agent.a2a_peers import A2APeers
        from test_a2a_compatibility import card, application
        _,job=self.started_self()
        self.service.record_turn_provenance(job['id'],owner_information={},context_messages=0)
        peers=A2APeers(self.service,secret_key=Fernet.generate_key())
        self.service.__dict__['a2a_peers']=peers
        remote_card=card('http://127.0.0.1/')
        peer=peers.register({'card':MessageToDict(remote_card)})['peer']['id']
        app=application(remote_card)
        app.router.routes.extend(create_agent_card_routes(remote_card))
        async def send(text="hello", message_id="exact-message"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                return await peers.send(peer,job['id'],text,message_id,http=http)
        set_allowance(self.service,'peer:'+peer,True,0,'owner')
        return job, peers, send

    def test_peer_work_mode_refusal_releases_ticket(self):
        import asyncio
        from personal_agent.work_mode import WorkModeRefused
        job,peers,send=self.peer_fixture()
        async def refused(*args,**kwargs):raise WorkModeRefused('a2a')
        with mock.patch.object(peers.transport(),'send_message',side_effect=refused):
            with self.assertRaises(WorkModeRefused):asyncio.run(send())
        self.assertEqual([row['status'] for row in self.events()],['not_run'])

    def test_peer_not_fresh_race_releases_ticket(self):
        import asyncio
        job,peers,send=self.peer_fixture()
        with mock.patch.object(peers,'admit',return_value=({'state':'completed','answer':'cached'},False)):
            self.assertEqual(asyncio.run(send())['answer'],'cached')
        self.assertEqual([row['status'] for row in self.events()],['not_run'])

    def test_all_press_labels_are_judged_without_values(self):
        from personal_agent import browser_session as bs
        from test_browser_session import FakeDriver, ORIGIN
        _,job=self.started_self()
        for label in ('read','navigate','mutate'):
            driver=FakeDriver()
            browser=bs.BrowserSession(lambda:driver,work_id=job['id'],approvals=self.service.browser_approvals_for(job))
            browser.open({'url':ORIGIN+'/one-press','effect':'navigate'})
            with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible') as judge:
                with self.assertRaises(ToolError):browser.click({'target':'메모 저장','effect':label})
                self.assertEqual(judge.call_count,1)
                self.assertEqual(driver.approved,[])
        raw={'url':ORIGIN+'/private-path?private=query','text':'private page text',
             'elements':[{'index':0,'role':'textbox','name':'Note','fieldName':'note','form':0,'value':'private typed value'}]}
        snapshot=bs.mediate_snapshot(raw)
        self.assertEqual(browser._write_metadata(snapshot,snapshot['_elements'][0]),('textbox','Note','note'))

    def test_judged_read_press_goes_ahead_without_write_receipt(self):
        from personal_agent import browser_session as bs
        from test_browser_session import FakeDriver, ORIGIN
        _,job=self.started_self()
        for effect in ('read','navigate'):
            driver=FakeDriver()
            browser=bs.BrowserSession(lambda:driver,work_id=job['id'],approvals=self.service.browser_approvals_for(job))
            browser.open({'url':ORIGIN+'/one-press','effect':'navigate'})
            with mock.patch.object(self.service.decision_judge,'connector_effect',return_value=effect) as judge:
                browser.click({'target':'메모 저장','effect':'mutate'})
            self.assertEqual(judge.call_count,1)
            self.assertEqual(len(driver.approved),1)
        self.assertEqual(self.events(),[])

    def test_type_gate_and_local_typing(self):
        from personal_agent import browser_session as bs
        from test_browser_session import FakeDriver, ORIGIN
        _,job=self.started_self()
        driver=FakeDriver()
        browser=bs.BrowserSession(lambda:driver,work_id=job['id'],approvals=self.service.browser_approvals_for(job))
        browser.open({'url':ORIGIN+'/one-press','effect':'navigate'})
        raw={'url':ORIGIN+'/form','elements':[{'index':0,'role':'textbox','name':'Note','fieldName':'note','form':0,'value':'old secret value'}]}
        with mock.patch.object(browser,'_snapshot',return_value=bs.mediate_snapshot(raw)):
            for args in ({'text':'private text','effect':'mutate'},
                         {'text':'private text','effect':'navigate','submit':True}, {'text':'private text','effect':'read','key':'Enter'}):
                with mock.patch.object(self.service.decision_judge,'connector_effect',return_value=None) as judge:
                    with self.assertRaises(ToolError):browser.type({'target':'Note',**args})
                    self.assertNotIn('private text',str(judge.call_args))
                    self.assertNotIn('old secret value',str(judge.call_args))
                    self.assertEqual(judge.call_args.args[1],['Note','note','textbox'])
            with mock.patch.object(self.service.decision_judge,'connector_effect',side_effect=AssertionError('local')):
                browser.type({'target':'Note','text':'private text\nsecond line','effect':'read'})
        self.assertEqual(len(driver.approved),1)

    def test_api_preflight_refusal_restores_exact_approval(self):
        _,job=self.started_self()
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
            with self.assertRaises(ApiError):self.api(job).call(self.args())
            self.ended_waiting(job)
            offered=self.service._browser_request(job['id'])
            self.service._decide_browser_step(job['id'],True,offered['offer_digest'])
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            for seam in ('_effect_guard','work_mode','intranet'):
                api=self.api(job)
                patch=(mock.patch.object(api,'_effect_guard',side_effect=ApiError('refused','test')) if seam=='_effect_guard' else
                       mock.patch('personal_agent.api_requests.'+('refuse_in_work_mode' if seam=='work_mode' else 'intranet_addresses'),side_effect=ApiError('refused','test')))
                with patch:
                    with self.assertRaises(ApiError):api.call(self.args())
                self.assertEqual(self.sent,[])
                self.assertTrue(all(row['status']=='not_run' for row in self.events()))
                self.assertEqual(self.service._browser_request(job['id'])['state'],'issued')
            self.api(job).call(self.args())
        self.assertEqual(len(self.sent),1)

    def test_connector_claim_refusal_can_retry(self):
        from personal_agent import owner_mcp, trust_record
        from test_connect_effects import ACTION, INPUT
        _,job=self.started_self()
        self.service.set_ai_connections({'enabled':True})
        owner_mcp.set_confirmed(self.store,'codex',['synthetic'])
        trust_record.set_operation(self.store,trust_record.connection('codex','owner-mcp','synthetic'),'apply_change','mutate')
        args={'engine':'codex','tool_name':ACTION,'input':INPUT}
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='reversible'):
            with mock.patch.object(self.service,'_connector_effect_claim',return_value=None):
                self.assertEqual(self.service.connector_permission(job,args)['behavior'],'deny')
            self.assertEqual(self.events()[0]['status'],'not_run')
            self.assertEqual(self.service.connector_permission(job,args)['behavior'],'allow')

    def test_rejected_http_has_label_and_known_outcome(self):
        _,job=self.started_self()
        set_allowance(self.service,'slot:notes',True,0,'owner')
        for status in (422,404):
            api=self.api(job)
            api.transport=lambda *a,**k:(status,{},b'{}')
            with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'), mock.patch.object(self.service,'record_conversation_notice') as notice:
                with self.assertRaises(ApiError) as raised:api.call(self.args(json.dumps({'status':status})))
            self.assertNotEqual(getattr(raised.exception,'effect',None),'unknown')
            self.assertEqual(self.events()[-1]['status'],'rejected')
            self.assertNotIn('slot:notes',notice.call_args.args[0])
            self.assertIn('notes',notice.call_args.args[0])

    def test_rejected_http_allows_unchanged_retry_with_allowance(self):
        set_allowance(self.service,'slot:notes',True,0,'owner')
        for status in (401,429):
            with self.subTest(status=status):
                _,job=self.started_self()
                api=self.api(job)
                api.transport=mock.Mock(side_effect=[(status,{},b'{}'),(200,{},b'{}')])
                with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
                    with self.assertRaises(ApiError) as raised:api.call(self.args())
                    self.assertFalse(raised.exception.executed)
                    self.assertEqual(self.events()[-1]['status'],'rejected')
                    with self.store.db() as db:
                        dispatch=db.execute("SELECT status FROM tool_events WHERE job_id=? AND tool='api_dispatch'",
                                            (job['id'],)).fetchone()
                    self.assertEqual(dispatch['status'],'rejected')
                    api.call(self.args())
                    tickets=[row for row in self.events() if row['job_id']==job['id']]
                    self.assertEqual([row['status'] for row in tickets],['rejected','observed'])
                    self.assertEqual(json.loads(tickets[0]['detail'])['digest'],json.loads(tickets[1]['detail'])['digest'])
                    with self.assertRaises(ApiError):api.call(self.args())
                self.assertEqual(api.transport.call_count,2)

    def test_possible_or_observed_write_still_blocks_replay(self):
        set_allowance(self.service,'slot:notes',True,0,'owner')
        _,job=self.started_self()
        for status in ('dispatching','unknown','observed','held'):
            with self.subTest(status=status):
                binding=step_binding(job['id'],'api_request','https://notes.test/items',status)
                ticket=self.service.self_set_write(job,binding,'slot:notes','POST','/items',effect='irreversible')
                if status!='dispatching':
                    self.service.self_set_write_finished(job,ticket,observed=status=='observed',held=status=='held')
                self.assertEqual(self.events()[-1]['status'],status)
                with self.assertRaises(ToolError) as raised:
                    self.service.self_set_write(job,binding,'slot:notes','POST','/items',effect='irreversible')
                self.assertEqual(raised.exception.code,'effect_replayed')
        self.assertEqual(len(self.events()),4)

    def test_rejected_owner_step_retry_needs_fresh_approval(self):
        for status in (401,429):
            with self.subTest(status=status):
                _,job=self.started_self()
                api=self.api(job)
                api.transport=mock.Mock(side_effect=[(status,{},b'{}'),(200,{},b'{}')])
                with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'):
                    with self.assertRaises(ApiError):api.call(self.args())
                    self.ended_waiting(job)
                    offered=self.service._browser_request(job['id'])
                    self.assertTrue(self.service._decide_browser_step(job['id'],True,offered['offer_digest'])['resumed'])
                    issued=self.service._browser_request(job['id'])
                    with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
                    with self.assertRaises(ApiError):api.call(self.args())
                    self.assertEqual(api.transport.call_count,1)
                    self.assertEqual(self.events()[-1]['status'],'rejected')
                    self.assertIsNone(self.service._browser_request(job['id']))
                    with self.store.db() as db:
                        approval=db.execute('SELECT state FROM memory_approvals WHERE token_hash=?',
                                            (self.store._exact_memory_token_lookup(issued['token']),)).fetchone()
                    self.assertEqual(approval['state'],'consumed')
                    with self.assertRaises(ApiError) as raised:api.call(self.args())
                    self.assertEqual(raised.exception.code,'approval_required')
                    self.assertEqual(api.transport.call_count,1)
                    fresh=self.service._browser_request(job['id'])
                    self.assertEqual(fresh['state'],'requested')
                    self.assertEqual(fresh['digest'],offered['digest'])
                    self.assertNotEqual(fresh['offer_digest'],offered['offer_digest'])
                    self.ended_waiting(job)
                    self.assertFalse(self.service._decide_browser_step(job['id'],True,offered['offer_digest'])['approved'])
                    self.assertTrue(self.service._decide_browser_step(job['id'],True,fresh['offer_digest'])['resumed'])
                    with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
                    api.call(self.args())
                self.assertEqual(api.transport.call_count,2)
                self.assertEqual(self.events()[-1]['status'],'observed')

    def test_mcp_revision_lapse_is_visible_and_off_is_unconditional(self):
        from personal_agent import owner_mcp, trust_record, self_set_writes
        _,job=self.started_self()
        owner_mcp.set_confirmed(self.store,'codex',['synthetic'])
        key='connector:codex|owner-mcp|synthetic'
        old=self_set_writes.connections(self.service)[key]['connection_revision']
        set_allowance(self.service,key,True,0,'owner')
        trust_record.set_operation(self.store,trust_record.connection('codex','owner-mcp','synthetic'),'apply_change','mutate')
        row=next(row for row in self_set_writes.settings(self.service)['connections'] if row['connection']==key)
        self.assertFalse(row['enabled'])
        self.assertTrue(row['reason'])
        set_allowance(self.service,key,False,0,'owner',connection_revision=old)
        self.assertFalse(allowance(self.service,key)['enabled'])

    def test_connection_display_label_cannot_leak_a_stored_secret(self):
        from personal_agent import self_set_writes
        _,job=self.started_self()
        set_allowance(self.service,'slot:notes',True,0,'owner')
        rows=self_set_writes.connections(self.service)
        secret='secret-notes-123456789'
        rows['slot:notes']['label']='Notes '+secret
        binding=step_binding(job['id'],'api_request','https://notes.test/items','target',argument='body')
        with mock.patch.object(self_set_writes,'connections',return_value=rows):
            with self.assertRaises(ToolError) as raised:
                self.service.self_set_write(job,binding,'slot:notes','POST','/items',effect='irreversible')
        self.assertEqual(raised.exception.code,'api_approval_unrepresentable')
        self.assertIs(raised.exception.ran,False)
        self.assertIsNone(self.service._browser_request(job['id']))
        self.assertEqual(self.events(),[])
        self.assertNotIn(secret,json.dumps(self.events()))

    def test_allowance_category_is_available_to_conversation(self):
        from personal_agent.agent_runtime import SETTINGS_CATEGORIES
        self.assertIn('write_allowances',SETTINGS_CATEGORIES)
        self.assertTrue(direct_request_setting('write_allowances','revoke','slot:notes'))
        self.assertFalse(direct_request_setting('write_allowances','allow','slot:notes'))

    def test_code_run_claim_refusal_releases_ticket(self):
        from personal_agent.code_run import CodeRunner
        from personal_agent.api_requests import slots
        _,job=self.started_self()
        runner=CodeRunner(self.store,job['id'],approvals=self.service.browser_approvals_for(job))
        runner._owners={'notes.test':[('notes',slots(self.store)['notes'])]}
        runner._fields=[]
        request={'method':'POST','host':'notes.test','path':'/items','query':'','bodyText':'{}'}
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='reversible'):
            with mock.patch.object(runner,'_claim_state_change',return_value=(False,'refused')):
                self.assertFalse(runner._decide(request,{'notes.test':[]})[0])
            self.assertEqual(self.events()[0]['status'],'not_run')
            self.assertTrue(runner._decide(request,{'notes.test':[]})[0])

    def test_review875_typing_uses_target_refreshed_after_judgment(self):
        from personal_agent import browser_session as bs
        from test_browser_session import FakeDriver, ORIGIN
        _,job=self.started_self()
        intended='<input aria-label="Intended field" name="intended">'
        other='<input aria-label="Other field" name="other">'
        def page(fields):return '<form method="post" action="/save">'+fields+'</form>'
        fixture=FakeDriver(pages={'/edit':page(intended+other)})
        # Keep the real snapshot/_expect/type boundary; only the worker transport
        # is fake. It validates the refreshed descriptor just as the native guard does.
        with mock.patch.object(bs.WebKitWorkerDriver,'_start'):
            driver=bs.WebKitWorkerDriver()
        browser=bs.BrowserSession(lambda:driver,work_id=job['id'],approvals=self.service.browser_approvals_for(job))
        self.addCleanup(browser.close)
        dispatched=[]
        def worker(op,timeout,**args):
            if op=='navigate':fixture.goto(args['url'],timeout)
            elif op=='snapshot':return {'page':fixture.snapshot()}
            elif op=='type':
                live=next(e for e in fixture.snapshot()['elements'] if e['index']==args['index'])
                self.assertEqual(args['expect']['name'],live['name'])
                dispatched.append(args)
                fixture.type(args['index'],args['text'],timeout)
            else:self.assertIn(op,('self_set_writes','settle'))
            return {}
        def reorder(*args,**kwargs):
            fixture.pages['/edit']=page(other+intended)
            return 'reversible'
        with mock.patch.object(driver,'_request',side_effect=worker):
            browser.open({'url':ORIGIN+'/edit','effect':'navigate'})
            before=browser._snapshot()
            before_element=bs.resolve_target(before,'Intended field')
            with mock.patch.object(self.service.decision_judge,'connector_effect',side_effect=reorder):
                browser.type({'target':'Intended field','text':'approved text','effect':'mutate'})
            after=bs.mediate_snapshot(fixture.snapshot())
            after_element=bs.resolve_target(after,'Intended field')
            self.assertNotEqual(before_element['index'],after_element['index'])
            self.assertEqual(len(dispatched),1)
            self.assertEqual(dispatched[0]['expect']['name'],'Intended field')
            self.assertEqual(dispatched[0]['index'],after_element['index'])
            self.assertEqual({e['name']:e['value'] for e in fixture.snapshot()['elements']},
                             {'Intended field':'approved text','Other field':''})

    def test_review875_api_revoked_during_final_dns_check(self):
        _,job=self.started_self()
        set_allowance(self.service,'slot:notes',True,0,'owner')
        def revoke(*args):set_allowance(self.service,'slot:notes',False,1,'owner')
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'), mock.patch('personal_agent.api_requests.intranet_addresses',side_effect=revoke):
            with self.assertRaises((ApiError,ToolError)):self.api(job).call(self.args())
        self.assertEqual(self.sent,[])
        self.assertEqual([r['status'] for r in self.events()],['not_run'])

    def test_review875_code_run_revoked_during_claim(self):
        from personal_agent.code_run import CodeRunner
        from personal_agent.api_requests import slots
        _,job=self.started_self()
        set_allowance(self.service,'slot:notes',True,0,'owner')
        runner=CodeRunner(self.store,job['id'],approvals=self.service.browser_approvals_for(job))
        runner._owners={'notes.test':[('notes',slots(self.store)['notes'])]}
        runner._fields=[]
        guard=ApiRequests._effect_guard
        def revoke(api,*args,**kwargs):
            result=guard(api,*args,**kwargs)
            if kwargs.get('method'):
                set_allowance(self.service,'slot:notes',False,1,'owner')
            return result
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'), mock.patch.object(ApiRequests,'_effect_guard',revoke):
            allowed,_=runner._decide({'method':'POST','host':'notes.test','path':'/items','bodyText':'{}'}, {'notes.test':[]})
        self.assertFalse(allowed)
        self.assertEqual(runner._claims,[])
        self.assertEqual([r['status'] for r in self.events()],['not_run'])

    def test_review875_connector_revoked_during_claim(self):
        from personal_agent import owner_mcp,trust_record
        from test_connect_effects import ACTION,INPUT
        _,job=self.started_self()
        self.service.set_ai_connections({'enabled':True})
        owner_mcp.set_confirmed(self.store,'codex',['synthetic'])
        trust_record.set_operation(self.store,trust_record.connection('codex','owner-mcp','synthetic'),'apply_change','mutate')
        key='connector:codex|owner-mcp|synthetic'
        set_allowance(self.service,key,True,0,'owner')
        claim=self.service._connector_effect_claim
        def revoke(*a,**kw):
            result=claim(*a,**kw)
            set_allowance(self.service,key,False,1,'owner')
            return result
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='irreversible'), mock.patch.object(self.service,'_connector_effect_claim',side_effect=revoke):
            answer=self.service.connector_permission(job,{'engine':'codex','tool_name':ACTION,'input':INPUT})
        self.assertEqual(answer['behavior'],'deny')
        self.assertEqual([r['status'] for r in self.events()],['not_run'])

    def test_review875_a2a_revoked_before_send(self):
        import asyncio
        job,peers,send=self.peer_fixture()
        spend=peers.spend
        def revoke(peer,revision):
            spend(peer,revision)
            set_allowance(self.service,'peer:'+peer,False,1,'owner')
        with mock.patch.object(peers,'spend',side_effect=revoke), mock.patch.object(peers.transport(),'send_message',new_callable=mock.AsyncMock) as transport:
            with self.assertRaises(ValueError):asyncio.run(send())
        transport.assert_not_called()
        self.assertEqual([r['status'] for r in self.events()],['not_run'])

    def test_review875_browser_checks_ticket_at_input_boundary(self):
        from personal_agent import browser_session as bs
        from test_browser_session import FakeDriver,ORIGIN
        _,job=self.started_self()
        driver=FakeDriver()
        browser=bs.BrowserSession(lambda:driver,work_id=job['id'],approvals=self.service.browser_approvals_for(job))
        browser.open({'url':ORIGIN+'/one-press','effect':'navigate'})
        def stop():
            with self.store.db() as db:db.execute("UPDATE jobs SET status='cancelled' WHERE id=?",(job['id'],))
        with mock.patch.object(self.service.decision_judge,'connector_effect',return_value='reversible'), mock.patch.object(browser,'_spend_step',side_effect=stop):
            with self.assertRaises(ToolError):browser.click({'target':'메모 저장','effect':'mutate'})
        self.assertEqual(driver.approved,[])
        self.assertEqual([r['status'] for r in self.events()],['not_run'])

    def test_review875_a2a_rechecks_inside_sdk_transport(self):
        import asyncio,httpx
        job,peers,send=self.peer_fixture()
        module=peers.transport()
        refuse=module.work_mode.refuse
        revoked=[];posts=[]
        def revoke(store,egress,*args,**kwargs):
            if self.events() and not revoked:
                key=next(iter(self.store.config('write_allowances')))
                set_allowance(self.service,key,False,1,'owner');revoked.append(True)
            return refuse(store,egress,*args,**kwargs)
        original=httpx.ASGITransport.handle_async_request
        async def observed(transport,request):
            if request.method=='POST':posts.append(request)
            return await original(transport,request)
        with mock.patch.object(module.work_mode,'refuse',side_effect=revoke), mock.patch.object(httpx.ASGITransport,'handle_async_request',observed):
            with self.assertRaises(ValueError):asyncio.run(send())
        self.assertEqual(posts,[])
        self.assertEqual([r['status'] for r in self.events()],['not_run'])

    def held_submit_continuation(self,kind,changed=False):
        from personal_agent.agent_runtime import Capabilities,WorkBudget
        from test_browser_session import FakeDriver,ORIGIN
        _,job=self.started_self()
        driver=FakeDriver()
        def runtime():
            return Capabilities(self.store,None,{},'',job['id'],lambda *args:None,
                                browser=lambda:driver,budget=WorkBudget(),
                                browser_approvals=self.service.browser_approvals_for(job))
        runtime1=runtime()
        browser=Capabilities.browser_session(runtime1)
        browser.open({'url':ORIGIN+'/one-press','effect':'navigate'})
        form={'page':ORIGIN+'/one-press','action':ORIGIN+'/send','method':'POST','dom':0,
              'state':'exact-form-digest','payment':False,'fieldNames':['note']}
        def held(*args,**kwargs):
            driver.held=dict(form)
            exc=ToolError('held','approval_required');exc.cancelled_form=dict(form);raise exc
        sent_forms=[]
        def release(record,timeout):
            if record!=driver.held:raise ToolError('form changed','target_unavailable')
            sent_forms.append(dict(record))
            driver.held=None
            return {'posted':True}
        args={'target':'메모 저장','effect':'mutate',**({'text':'value'} if kind=='type' else {})}
        with mock.patch.object(driver,kind,side_effect=held) as input_call, mock.patch.object(driver,'release_submit',side_effect=release) as release_call, mock.patch.object(self.service.decision_judge,'connector_effect',side_effect=lambda operation,*a,**k:'irreversible' if operation.startswith('browser_submit') else 'reversible'):
            with self.assertRaises(ToolError):getattr(browser,kind)(args)
            self.ended_waiting(job)
            Capabilities.close_browser(runtime1)
            offered=self.service._browser_request(job['id'])
            waiting_runtime=runtime();waiting=Capabilities.browser_session(waiting_runtime)
            waiting.read()
            self.assertEqual(input_call.call_count,1)
            release_call.assert_not_called()
            self.assertEqual(self.service._browser_request(job['id'])['offer_digest'],offered['offer_digest'])
            Capabilities.close_browser(waiting_runtime)
            self.assertEqual(offered['source_action'],'browser_submit')
            self.assertTrue(self.service._decide_browser_step(job['id'],True,offered['offer_digest'])['resumed'])
            with self.store.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
            runtime2=runtime();continued=Capabilities.browser_session(runtime2)
            try:
                if changed:
                    driver.held['state']='different-form-digest'
                    with self.assertRaises(ToolError):getattr(continued,kind)(args)
                    self.assertEqual(input_call.call_count,1)
                    self.assertEqual(sent_forms,[])
                    return
                getattr(continued,kind)(args)
                self.assertEqual(input_call.call_count,1)
                release_call.assert_called_once()
                self.assertIsNone(self.service._browser_request(job['id']))
                with self.assertRaises(ToolError):getattr(continued,kind)(args)
                self.assertEqual(input_call.call_count,1)
                release_call.assert_called_once()
            finally:Capabilities.close_browser(runtime2)

    def test_review875_click_continues_native_submit_without_second_click(self):
        self.held_submit_continuation('click')

    def test_review875_changed_native_form_is_not_submitted(self):
        self.held_submit_continuation('click',changed=True)

    def test_review875_type_continues_native_submit_without_second_type(self):
        self.held_submit_continuation('type')


def test_review875_allowance_confirmation_survives_polling():
    import shutil,subprocess
    from pathlib import Path
    from test_settings_ui import DOM_CHECKS
    from personal_agent import self_set_writes
    import pytest
    node=shutil.which('node')
    if not node:pytest.skip('Node unavailable')
    # Reuse the repository's executable DOM harness, not a source-text assertion.
    script=DOM_CHECKS.split('const source=')[0]+r'''
Element.prototype.remove=function(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(n=>n!==this);this.parentNode=null;};
const root=new Element('section');root.id='write-allowances';root._root=true;
const calls=[],ctx={document,$,api:async(path,body)=>calls.push(body),refresh:async()=>{},busy:async(button,fn)=>fn()};
vm.createContext(ctx);vm.runInContext(part('function renderWriteAllowances(', 'function renderPaymentAutonomy('),ctx);
const data={label:'Writes',note:'Risk',enable:'Allow',disable:'Off',confirm:'Confirm',connections:[
 {connection:'slot:notes',label:'Notes',revision:1,connection_revision:'a',enabled:false},
 {connection:'slot:other',label:'Other',revision:1,connection_revision:'b',enabled:false}]};
(async()=>{
 ctx.renderWriteAllowances(data);
 root.querySelectorAll('button')[0].onclick();
 const confirm=root.querySelectorAll('button').find(n=>n.textContent==='Confirm · Notes');confirm.focus();
 ctx.renderWriteAllowances(JSON.parse(JSON.stringify(data)));
 assert(confirm.isConnected,'poll must retain the confirmation');assert.equal(document.activeElement,confirm);
 data.connections[1].revision++;ctx.renderWriteAllowances(data);
 assert(confirm.isConnected,'another connection changing must not discard the draft');
 assert.equal(calls.length,0);await confirm.onclick();assert.equal(calls.length,1);
 assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),{connection:'slot:notes',enabled:true,revision:1,connection_revision:'a'});
 data.connections[0].connection_revision='changed';ctx.renderWriteAllowances(data);
 assert(!confirm.isConnected,'a changed connection invalidates the old confirmation');
 assert(!root.querySelectorAll('button').some(n=>n.textContent==='Confirm · Notes'));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result=subprocess.run([node,'-e',script,str(Path(self_set_writes.__file__).parent/'web/app.js')],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
