"""#662: model-free payment mode, real Settings/Work/Evidence and existing fake transports."""
import json
import unittest
from unittest import mock

from personal_agent import browser_session as bs, preparations as prep, trust_record
from personal_agent.agent_runtime import ToolError
from personal_agent.api_requests import ApiRequests, ApiError, save_slot
from personal_agent.conversation_handoff import JUDGMENT_YES, Judgment
from personal_agent.context_observations import CONTINUATION_KEY_PREFIX
from personal_agent.quickstart_service import AgentService
from personal_agent.settings_orchestrator import SettingsError, direct_request_setting
from test_owner_settings_conversation import _Case
import test_action_bound_approvals_619 as prior
from test_browser_session import FakeDriver, ORIGIN
import test_connect_effects as effects
from test_connect_effects import INPUT


class Mode:
    def switch(self, enabled):
        settings = self.service.settings_orchestrator
        draft = settings.propose('owner', 'http', 'payment_autonomy', 'enabled', 'on' if enabled else 'off')
        return settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])

    def scheduled_job(self, key='scheduled'):
        row = self.service.preparations.create(kind='prepare', goal='pay', due_at=1000, timezone='UTC',
                                              recurrence='daily', channel='web', created_from=None,
                                              state=prep.STATE_SCHEDULED)
        work = self.store.enqueue('pay', prep.request_key(row['id'], 1000))
        with self.store.db() as db:
            db.execute("UPDATE preparations SET state=?,last_run_job_id=? WHERE id=?", (prep.STATE_RUNNING, work, row['id']))
        return self.store.job(work)

    def receipts(self, work):
        return [event['trace']['evidence'] for event in self.store.task_events(work)
                if event['tool'] == 'payment_autonomy']

    def assert_receipt(self, work, action):
        rows = self.receipts(work)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['authorized_by'], rows[0]['revision'], rows[0]['action']),
                         ('payment-autonomy', 1, action))
        self.assertEqual(len(rows[0]['digest']), 64)
        with self.store.db() as db:
            messages = [dict(row) for row in db.execute("SELECT * FROM messages WHERE role='assistant'")]
        self.assertTrue(any(rows[0]['label'] in row['content'] and row['job_id'] == work for row in messages))
        return rows[0]


class Settings(Mode, _Case):
    def test_default_off_preview_confirm_revision_and_stale_draft(self):
        self.assertEqual(self.service.payment_autonomy_settings(), {'enabled': False, 'revision': 0})
        first = self.settings.propose('owner', 'http', 'payment_autonomy', 'enabled', 'on')
        stale = self.settings.propose('owner', 'http', 'payment_autonomy', 'enabled', 'on')
        self.assertIn('웹 페이지', first['response'])
        self.assertIn('도구 결과', first['response'])
        self.assertFalse(self.service.payment_autonomy_settings()['enabled'])
        with self.assertRaises(SettingsError):
            self.settings.confirm('owner', 'http', first['draft_id'], 'wrong-digest')
        self.settings.confirm('owner', 'http', first['draft_id'], first['digest'])
        self.assertEqual(self.service.payment_autonomy_settings(), {'enabled': True, 'revision': 1})
        with self.assertRaises(SettingsError):
            self.settings.confirm('owner', 'http', stale['draft_id'], stale['digest'])
        self.switch(False)
        self.assertEqual(self.service.payment_autonomy_settings(), {'enabled': False, 'revision': 2})
        self.switch(True)
        self.assertEqual(self.service.payment_autonomy_settings()['revision'], 3)

    def test_ai_draft_and_page_instruction_cannot_enable_but_direct_off_is_immediate(self):
        job = self.store.job(self.store.enqueue('자율 결제 모드 켜 줘', 'own', owner_typed=True))
        change = {'category': 'payment_autonomy', 'setting': 'enabled', 'value': 'on'}
        with mock.patch.object(self.service.decision_judge, 'explicit_settings_request', return_value=Judgment(JUDGMENT_YES)) as judge:
            draft = self.service.settings_tools(job)('settings_change', change)
            self.assertTrue(draft['requires_owner_confirmation'])
            judge.assert_not_called()
        self.assertFalse(self.service.payment_autonomy_settings()['enabled'])
        self.assertFalse(direct_request_setting('payment_autonomy', 'enabled', 'on'))
        self.assertTrue(direct_request_setting('payment_autonomy', 'enabled', 'off'))
        self.switch(True)
        change['value'] = 'off'
        with mock.patch.object(self.service.decision_judge, 'explicit_settings_request', return_value=Judgment(JUDGMENT_YES)):
            result = self.service.settings_tools(job)('settings_change', change)
        self.assertTrue(result['applied'])
        self.assertFalse(self.service.payment_autonomy_settings()['enabled'])

    def test_unattended_replayed_and_continued_turns_cannot_enable(self):
        keys = ('replayed', CONTINUATION_KEY_PREFIX + 'continued')
        for i, key in enumerate(keys):
            job = self.store.job(self.store.enqueue('자율 결제 모드 켜 줘', key, owner_typed=bool(i)))
            with self.assertRaises(SettingsError):
                self.service.settings_tools(job)('settings_change', {'category': 'payment_autonomy', 'setting': 'enabled', 'value': 'on'})
        scheduled = self.store.job(self.store.enqueue('켜 줘', prep.request_key('p', 1000)))
        result = self.service.settings_tools(scheduled)('settings_change', {'category': 'payment_autonomy', 'setting': 'enabled', 'value': 'on'})
        self.assertFalse(result['applied'])
        self.assertEqual(self.settings._drafts(), {})
        self.assertFalse(self.service.payment_autonomy_settings()['enabled'])

    def test_read_exposes_risk_and_revision(self):
        mode = self.settings.read('owner', 'payment_autonomy')['settings']['payment_autonomy']['enabled']
        self.assertEqual((mode['value'], mode['revision']), ('off', 0))
        self.assertIn('웹 페이지', mode['note'])

    def browser(self, job):
        driver = FakeDriver()
        browser = bs.BrowserSession(lambda: driver, work_id=job['id'], judgments=prior.NeverJudged(),
                                    approvals=self.service.browser_approvals_for(job))
        browser.open({'url': ORIGIN + '/one-press', 'effect': 'navigate'})
        return browser, driver

    def test_browser_payment_owner_and_unattended_with_receipt_notice_and_no_repeat(self):
        self.switch(True)
        for i, key in enumerate(('owner', prep.request_key('p', 1000))):
            with self.subTest(key=key):
                job = self.scheduled_job() if i else self.store.job(self.store.enqueue('pay', key, owner_typed=True))
                browser, driver = self.browser(job)
                browser.click({'target': 'Buy now', 'effect': 'payment'})
                self.assertEqual(driver.approved, [True])
                self.assertIsNone(self.service._browser_request(job['id']))
                self.assert_receipt(job['id'], 'browser_click')
                with self.assertRaises(ToolError):
                    browser.click({'target': 'Buy now', 'effect': 'payment'})

    def test_enabling_mode_during_an_unattended_browser_session_lets_the_next_payment_step_through(self):
        job = self.scheduled_job()
        browser, driver = self.browser(job)
        self.switch(True)
        browser.click({'target': 'Buy now', 'effect': 'payment'})
        self.assertEqual(driver.approved, [True])
        self.assert_receipt(job['id'], 'browser_click')

    def test_a_step_stopped_by_the_final_recheck_is_not_held_as_replayed(self):
        # Codex P2 on #668: the owner turns the mode off while its notice is being delivered.
        self.switch(True)
        job = self.store.job(self.store.enqueue('pay', 'recheck', owner_typed=True))
        browser, driver = self.browser(job)
        deliver = self.service._deliver_payment_autonomy_notice
        def off_during_notice(job_, authorization):
            deliver(job_, authorization)
            self.switch(False)
        with mock.patch.object(self.service, '_deliver_payment_autonomy_notice', off_during_notice):
            with self.assertRaises(ToolError) as stopped:
                browser.click({'target': 'Buy now', 'effect': 'payment'})
        self.assertEqual(stopped.exception.code, 'payment_autonomy_changed')
        self.assertEqual(driver.approved, [], 'nothing was dispatched')
        with self.store.db() as db:
            statuses = [row['status'] for row in db.execute(
                "SELECT status FROM tool_events WHERE job_id=? AND tool='payment_autonomy'", (job['id'],))]
        self.assertEqual(statuses, ['not_run'], 'the record stays, released')
        self.switch(True)
        browser.click({'target': 'Buy now', 'effect': 'payment'})
        self.assertEqual(driver.approved, [True], 'the same step runs once the mode is on again')
        with self.assertRaises(ToolError):
            browser.click({'target': 'Buy now', 'effect': 'payment'})
        self.assertEqual(driver.approved, [True], 'a dispatched step is still held as replayed')

    def test_browser_held_submit_and_secret_fields(self):
        self.switch(True)
        job = self.store.job(self.store.enqueue('pay', 'submit', owner_typed=True))
        browser, driver = self.browser(job)
        record = {'page': ORIGIN + '/checkout', 'action': ORIGIN + '/pay', 'method': 'POST', 'dom': 0, 'state': 'exact-form'}
        driver.release_submit = mock.Mock(return_value={'navigated': False})
        browser._cancelled_submit(record, 'Pay exact form')
        driver.release_submit.assert_called_once()
        self.assert_receipt(job['id'], 'browser_submit')
        browser.open({'url': ORIGIN + '/checkout', 'effect': 'navigate'})
        for field, value in (('카드번호', '4242424242424242'), ('CVC', '123')):
            with self.assertRaises(ToolError) as refused:
                browser.type({'target': field, 'text': value, 'effect': 'payment'})
            self.assertEqual(refused.exception.code, 'approval_required')
            # Codex P2 on #668: the field kept out of the mode waits on a real request the owner can approve,
            # and the owner's approval of it lets exactly that typing through, with the mode still on.
            pending = self.service._browser_request(job['id'])
            self.assertEqual((pending or {}).get('state'), 'requested')
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='partial' WHERE id=?", (job['id'],))
            self.assertTrue(self.service._decide_browser_step(job['id'], True, pending['offer_digest'])['approved'])
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
            browser.type({'target': field, 'text': value, 'effect': 'payment'})
            self.assertIsNone(self.service._browser_request(job['id']), 'the token is spent')
        self.assertEqual(len(self.receipts(job['id'])), 1)

    def test_api_money_owner_and_unattended_and_off_before_dispatch(self):
        self.switch(True)
        save_slot(self.store, 'money', ['pay.test'], 'secret-key-value-123456789', money=True)
        for i, key in enumerate(('api-owner', prep.request_key('p', 2000), 'switch-off')):
            with self.subTest(key=key):
                job = self.scheduled_job() if i == 1 else self.store.job(self.store.enqueue('pay', key, owner_typed=True))
                sent = []
                api = ApiRequests(self.store, job['id'], approvals=self.service.browser_approvals_for(job),
                                  transport=lambda *a, **k: sent.append(a) or (200, {}, b'{}'))
                args = {'slot': 'money', 'url': 'https://pay.test/pay', 'method': 'POST', 'body': '{"amount":"10"}', 'effect': 'payment'}
                if i == 2:
                    with self.assertRaises(ApiError):
                        api._call_slot(args, api.slots()['money'], before_send=lambda: self.switch(False))
                    self.assertEqual(sent, [])
                    self.assertEqual(self.receipts(job['id']), [])
                else:
                    api.call(args)
                    self.assertEqual(len(sent), 1)
                    receipt = self.assert_receipt(job['id'], 'api_request')
                    self.assertIn('amount', receipt['label'])
                    with self.assertRaises(ApiError):api.call(args)
                    self.assertEqual(len(sent), 1)

    def test_unattended_held_form_and_nonsecret_payment_typing(self):
        self.switch(True)
        job = self.scheduled_job()
        browser, driver = self.browser(job)
        record = {'page': ORIGIN + '/checkout', 'action': ORIGIN + '/pay', 'method': 'POST', 'dom': 0, 'state': 'exact-form'}
        driver.release_submit = mock.Mock(return_value={'navigated': False})
        browser._cancelled_submit(record, 'Pay exact form')
        driver.release_submit.assert_called_once()
        self.assert_receipt(job['id'], 'browser_submit')
        for unattended in (False, True):
            job = self.scheduled_job() if unattended else self.store.job(self.store.enqueue('type payee', 'payee', owner_typed=True))
            browser, driver = self.browser(job)
            browser.open({'url': ORIGIN + '/checkout', 'effect': 'navigate'})
            browser.type({'target': '받는 사람', 'text': 'Owner', 'effect': 'payment'})
            self.assertEqual([row[2] for row in driver.log if row[0] == 'type'], ['Owner'])
            self.assert_receipt(job['id'], 'browser_type')

    def test_off_during_notice_delivery_still_prevents_browser_send(self):
        self.switch(True)
        job = self.store.job(self.store.enqueue('pay', 'notice-revoke', owner_typed=True))
        browser, driver = self.browser(job)
        with mock.patch.object(self.service, '_deliver_payment_autonomy_notice', side_effect=lambda *a: self.switch(False)):
            with self.assertRaises(ToolError) as caught:
                browser.click({'target': 'Buy now', 'effect': 'payment'})
        self.assertEqual(caught.exception.code, 'payment_autonomy_changed')
        self.assertEqual(driver.approved, [])

    def test_browser_off_between_guard_and_dispatch_sends_nothing(self):
        self.switch(True)
        job = self.store.job(self.store.enqueue('pay', 'browser-off', owner_typed=True))
        browser, driver = self.browser(job)
        original = browser._spend_step
        def spend():
            original()
            self.switch(False)
        browser._spend_step = spend
        with self.assertRaises(ToolError) as caught:
            browser.click({'target': 'Buy now', 'effect': 'payment'})
        self.assertEqual(caught.exception.code, 'payment_autonomy_changed')
        self.assertEqual(driver.approved, [])
        self.assertEqual(self.receipts(job['id']), [])

    def test_unknown_api_outcome_holds_another_payment(self):
        self.switch(True)
        save_slot(self.store, 'money', ['pay.test'], 'secret-key-value-123456789', money=True)
        job = self.store.job(self.store.enqueue('pay', 'unknown', owner_typed=True))
        transport = mock.Mock(side_effect=TimeoutError())
        api = ApiRequests(self.store, job['id'], approvals=self.service.browser_approvals_for(job), transport=transport)
        args = {'slot': 'money', 'url': 'https://pay.test/pay', 'method': 'POST', 'body': '{"amount":10}', 'effect': 'payment'}
        for amount in (10, 20):
            with self.assertRaises(ApiError) as caught:
                api.call({**args, 'body': json.dumps({'amount': amount})})
            self.assertEqual(caught.exception.code, 'effect_unknown')
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(len(self.receipts(job['id'])), 1)

    def test_settings_switch_refuses_a_stale_revision_and_bad_input(self):
        self.assertEqual(self.service.set_payment_autonomy({'enabled': True, 'revision': 0}), {'enabled': True, 'revision': 1})
        for body in ({'enabled': False, 'revision': 0}, {'enabled': 'no', 'revision': 1}, {'enabled': False}, None):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.service.set_payment_autonomy(body)
        self.assertTrue(self.service.payment_autonomy_settings()['enabled'])
        self.assertEqual(self.service.set_payment_autonomy({'enabled': False, 'revision': 1}), {'enabled': False, 'revision': 2})

    def test_replayed_confirmation_cannot_apply_an_on_draft(self):
        draft = self.settings.propose('local-owner', 'http', 'payment_autonomy', 'enabled', 'on')
        with self.assertRaises(ValueError):
            self.service.conversation_settings_request({'operation': 'confirm', 'draft_id': draft['draft_id'],
                                                         'digest': draft['digest']}, owner_typed=False)
        self.assertFalse(self.service.payment_autonomy_settings()['enabled'])

    def test_delegated_capabilities_still_have_no_payment_surface(self):
        from personal_agent.agent_runtime import Capabilities, BROWSER_ACTIONS, API_ACTIONS
        self.switch(True)
        job = self.store.job(self.store.enqueue('pay', 'delegate', owner_typed=True))
        child = Capabilities(self.store, None, {}, '', job['id'], lambda *a: None, readonly=True, delegated=True)
        names = {definition['function']['name'] for definition in child.definitions()}
        self.assertFalse(names & (BROWSER_ACTIONS | API_ACTIONS))
        self.assertIsNone(child.browser_approvals)
        with self.assertRaises(ValueError):
            child.execute('api_request', {'slot': 'money', 'url': 'https://pay.test/pay', 'method': 'POST', 'effect': 'payment'})
        self.assertEqual(self.receipts(job['id']), [])



class CodePayments(Mode, unittest.TestCase):
    BODY = prior.CodeRunMoneyOnly.BODY
    setUp = prior.CodeRunMoneyOnly.setUp
    run_change = prior.CodeRunMoneyOnly.run_change

    def test_mode_allows_money_code_with_receipt_and_notice(self):
        self.service = AgentService(self.store)
        self.switch(True)
        self.save(money=True)
        for scheduled in (False, True):
            job = self.scheduled_job() if scheduled else self.store.job(self.work)
            self.work = job['id']
            process, result = self.run_change(self.service.browser_approvals_for(job))
            self.assertEqual([item['allow'] for item in process.decisions], [True])
            self.assertTrue(result['effects'])
            self.assert_receipt(self.work, 'code_run')

    def test_off_before_code_dispatch_refuses_without_a_receipt(self):
        self.service = AgentService(self.store)
        self.switch(True)
        self.save(money=True)
        approvals = self.service.browser_approvals_for(self.store.job(self.work))
        authorize = approvals.payment_autonomy
        def revoke(binding, description, **kwargs):
            result = authorize(binding, description, **kwargs)
            if result and not kwargs.get('dispatch'):
                self.switch(False)
            return result
        approvals.payment_autonomy = revoke
        process, _result = self.run_change(approvals)
        self.assertEqual([item['allow'] for item in process.decisions], [False])
        self.assertEqual(self.receipts(self.work), [])

    def test_off_during_notice_delivery_still_prevents_code_send(self):
        self.service = AgentService(self.store)
        self.switch(True)
        self.save(money=True)
        with mock.patch.object(self.service, '_deliver_payment_autonomy_notice', side_effect=lambda *a: self.switch(False)):
            process, _result = self.run_change(self.service.browser_approvals_for(self.store.job(self.work)))
        self.assertEqual([item['allow'] for item in process.decisions], [False])
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tool_events WHERE tool='api_dispatch'").fetchone()[0], 0)


class ConnectorPayments(Mode, unittest.TestCase):
    setUp = effects.EffectBoundary.setUp
    call = effects.EffectBoundary.call

    def test_mode_allows_payment_and_still_holds_unknown_and_replay(self):
        self.switch(True)
        trust_record.set_money(self.store, self.target, True)
        self.assertEqual(self.call()['behavior'], 'allow')
        self.assert_receipt(self.job['id'], 'connector_call')
        self.assertIsNone(self.service._browser_request(self.job['id']))
        self.assertEqual(self.call()['behavior'], 'deny')
        self.assertEqual(self.call({**INPUT, 'amount': '5.00'})['behavior'], 'deny', 'unsettled permission stays unknown')
        with self.assertRaises(ValueError):
            trust_record.set_rung(self.store, self.target, 'mandate')


    def test_unattended_connector_payment_has_receipt_and_notice(self):
        self.switch(True)
        trust_record.set_money(self.store, self.target, True)
        self.job = self.scheduled_job()
        self.assertEqual(self.call()['behavior'], 'allow')
        self.assert_receipt(self.job['id'], 'connector_call')

    def test_off_before_connector_dispatch_and_secret_input_still_refused(self):
        self.switch(True)
        trust_record.set_money(self.store, self.target, True)
        self.assertEqual(self.call({'password': 'not-a-real-secret'})['behavior'], 'deny')
        claim = self.service._connector_effect_claim
        def revoke(*args):
            result = claim(*args)
            self.switch(False)
            return result
        with mock.patch.object(self.service, '_connector_effect_claim', side_effect=revoke):
            self.assertEqual(self.call()['behavior'], 'deny')
        self.assertEqual(self.receipts(self.job['id']), [])

    def test_off_during_notice_delivery_still_prevents_connector_permission(self):
        self.switch(True)
        trust_record.set_money(self.store, self.target, True)
        with mock.patch.object(self.service, '_deliver_payment_autonomy_notice', side_effect=lambda *a: self.switch(False)):
            self.assertEqual(self.call()['behavior'], 'deny')
        permissions = [event for event in self.store.task_events(self.job['id']) if event['tool'] == 'connector_permission']
        self.assertEqual(permissions[-1]['status'], 'denied')


class NoticesAndWaits(Mode, _Case):
    def test_notice_uses_the_works_telegram_conversation(self):
        from test_owner_settings_conversation import CHAT, GENERATION
        self.switch(True)
        job = self.store.job(self.store.enqueue('pay', 'telegram-payment', channel='telegram:' + GENERATION,
                                                chat_id=CHAT, owner_typed=True))
        binding = bs.step_binding(job['id'], 'browser_click', 'https://pay.test/', 'pay', 'pay', 'exact')
        self.service.browser_approvals_for(job).payment_autonomy(binding, 'Pay exact amount', dispatch=True)
        receipt = self.assert_receipt(job['id'], 'browser_click')
        sends = [body for url, body in self.telegram if url == 'sendMessage']
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]['chat_id'], CHAT)
        self.assertIn(receipt['label'], sends[0]['text'])
        with self.store.db() as db:
            row = db.execute("SELECT channel FROM messages WHERE job_id=? AND role='assistant'", (job['id'],)).fetchone()
        self.assertEqual(row['channel'], job['channel'])

    def test_an_old_payment_prompt_approved_in_mode_issues_its_exact_token_once(self):
        job = self.store.job(self.store.enqueue('pay', 'old-prompt', owner_typed=True))
        binding = bs.step_binding(job['id'], 'browser_click', 'https://pay.test/', 'pay', 'pay', 'exact')
        self.service._request_browser_step(job, binding, 'Pay exact amount')
        pending = self.service._browser_request(job['id'])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='partial' WHERE id=?", (job['id'],))
        self.switch(True)
        result = self.service._decide_browser_step(job['id'], True, pending['offer_digest'])
        self.assertTrue(result['approved'] and result['resumed'])
        self.assertEqual(self.service._browser_request(job['id'])['state'], 'issued')
        self.switch(False)
        self.assertTrue(self.service._consume_browser_step(job, binding), "the owner's own approval stands")
        self.assertFalse(self.service._consume_browser_step(job, binding), 'spent once')
        self.assertEqual(self.receipts(job['id']), [], 'an owner-approved step is not a payment-autonomy receipt')


class WebSettings(unittest.TestCase):
    def test_on_waits_for_owner_confirm_off_applies_and_polling_preserves_confirm(self):
        import shutil
        import subprocess
        from pathlib import Path
        from personal_agent.messages import msg
        node = shutil.which('node')
        if not node:self.skipTest('node is unavailable')
        app = Path(__file__).parents[1] / 'src/personal_agent/web/app.js'
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8'),nodes=new Map(),calls=[];
const $=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',checked:false,disabled:false,hidden:true,focus(){}});return nodes.get(id);};
let mode=false,revision=0;
const context={$ ,t:s=>s,setFeedback:(id,text)=>$(id).textContent=text,setError:(_id,error)=>{throw error;},
 busy:async(button,fn)=>{button.disabled=true;try{return await fn();}finally{button.disabled=false;}},
 refresh:async()=>{},api:async(path,body)=>{calls.push({path,body});assert.equal(path,'/api/payment-autonomy');
 assert.equal(body.revision,revision);mode=body.enabled;revision+=1;return {enabled:mode,revision};}};
vm.createContext(context);vm.runInContext(source.slice(source.indexOf('let paymentAutonomyRevision='),source.indexOf('function renderCurrentContext(')),context);
(async()=>{
 context.renderPaymentAutonomy({enabled:false,revision:0});
 assert.equal($('payment-autonomy-enabled').checked,false);
 assert.equal($('payment-autonomy-risk').textContent,process.argv[2]);
 const toggle=$('payment-autonomy-enabled');toggle.checked=true;
 await toggle.onchange({currentTarget:toggle});
 assert.equal(calls.length,0,'turning on only shows the risk');assert.equal(mode,false);
 assert.equal($('payment-autonomy-preview').hidden,false);assert.equal(toggle.checked,false);
 context.renderPaymentAutonomy({enabled:false,revision:0});
 assert.equal($('payment-autonomy-preview').hidden,false,'polling keeps the confirmation open');
 await $('payment-autonomy-confirm').onclick({currentTarget:$('payment-autonomy-confirm')});
 assert.equal(mode,true);assert.equal(calls.length,1);assert.equal($('payment-autonomy-preview').hidden,true);
 context.renderPaymentAutonomy({enabled:true,revision:1});toggle.checked=false;
 await toggle.onchange({currentTarget:toggle});
 assert.equal(calls.length,2,'turning off applies at once');assert.equal(mode,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', script, str(app), msg('payment_autonomy.risk')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
