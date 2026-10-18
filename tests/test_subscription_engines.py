import tempfile
import unittest

from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore
from personal_agent.subscription_engines import SubscriptionEngines


class SubscriptionEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=QuickStore(self.temp.name)
        engines=SubscriptionEngines(finder=lambda command: '/bin/codex' if command=='codex' else None,clock=lambda:1234)
        # The login check (#571) is covered in test_engine_auth; here it must
        # not depend on whether this machine has a Codex profile.
        class _UnknownLogin:
            def login_status(self,engine_id,binary=None):return {'state':'unknown'}
        self.service=AgentService(self.store,subscription_engines=engines,execution_adapter=_UnknownLogin())

    def tearDown(self):self.temp.cleanup()

    def test_discovery_never_reads_or_reports_credentials(self):
        engines=self.service.settings()['subscription_engines']['engines']
        self.assertEqual([(e['id'],e['installed']) for e in engines],[('codex',True),('claude-code',False)])
        self.assertNotIn('token',str(engines).lower())

    def test_connect_requires_installed_cli_and_owner_login_confirmation(self):
        with self.assertRaises(ValueError):self.service.connect_subscription_engine({'engine':'codex','officially_authenticated':False})
        status=self.service.connect_subscription_engine({'engine':'codex','officially_authenticated':True})
        self.assertEqual(status['selected'],'codex')
        selected=[e for e in status['engines'] if e['id']=='codex'][0]
        self.assertTrue(selected['connected']);self.assertEqual(selected['authentication'],'owner-confirmed-official-login')
        self.assertFalse(self.store.secret('model_key'))
        with self.assertRaises(ValueError):self.service.connect_subscription_engine({'engine':'claude-code','officially_authenticated':True})

    def test_onboarding_reports_only_safe_connection_and_recovery_state(self):
        self.store.secret('model_key','private-api-key')
        self.store.enqueue('sensitive task body','recovery-test')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running',delivery='sending'")
        self.store.recover()
        guide=self.service.onboarding()
        self.assertEqual(guide['recovery'],{'interrupted_jobs':1,'uncertain_deliveries':1,'uncertain_notifications':0})
        self.assertTrue(any(step['id']=='recovery' and step['state']=='attention' for step in guide['steps']))
        rendered=str(guide)
        self.assertNotIn('private-api-key',rendered)
        self.assertNotIn('sensitive task body',rendered)

    def recovery_step(self):
        return next(step for step in self.service.onboarding()['steps'] if step['id']=='recovery')

    def test_the_recovery_step_names_only_what_the_latest_restart_cut_off(self):
        """#90: an older interrupted Work stays in the history and its count, but no restart is blamed for it."""
        self.store.enqueue('earlier task','recovery-old')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running',delivery='sending'")
        self.store.recover()
        self.assertEqual(self.recovery_step()['state'],'attention')
        self.store.recover()   # the next start finds nothing running
        self.assertEqual(self.store.last_recovery(),{'interrupted_jobs':0,'uncertain_deliveries':0,'uncertain_notifications':0})
        self.assertEqual(self.recovery_step()['state'],'ready')
        self.assertEqual(self.service.onboarding()['recovery'],
                         {'interrupted_jobs':1,'uncertain_deliveries':1,'uncertain_notifications':0})

    def test_a_store_that_never_restarted_reports_no_recovery_attention(self):
        self.assertEqual(self.store.last_recovery(),{'interrupted_jobs':0,'uncertain_deliveries':0,'uncertain_notifications':0})
        self.assertEqual(self.recovery_step()['state'],'ready')

    def test_connected_codex_is_an_onboarding_ready_execution_choice(self):
        self.service.connect_subscription_engine({'engine':'codex','officially_authenticated':True})
        guide=self.service.onboarding()
        self.assertEqual(guide['subscription_engine'],'codex')
        self.assertTrue(any(step['id']=='engine' and step['state']=='ready' for step in guide['steps']))

    def test_a_working_api_route_does_not_ask_for_a_subscription_engine(self):
        self.service.model_ready=lambda model,test:True
        guide=self.service.onboarding()
        self.assertFalse(any(step['id'] in ('engine','model') and step['state']=='needed' for step in guide['steps']))
        self.assertTrue(any(step['id']=='model' and step['state']=='ready' for step in guide['steps']))
