import tempfile
import unittest
from personal_agent.context_inbox import ContextInbox
from personal_agent.quickstart_store import QuickStore


class ContextInboxTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.store=QuickStore(self.temp.name);self.inbox=ContextInbox(self.store)
 def tearDown(self):self.temp.cleanup()
 def test_opt_in_sensitive_url_dedup_and_delete(self):
  with self.assertRaises(ValueError):self.inbox.capture({'source_kind':'text','content':'ordinary text'})
  self.inbox.configure({'sources':{'text':True,'url':True}})
  with self.assertRaises(ValueError):self.inbox.capture({'source_kind':'text','content':'api_key=not-safe'})
  for url in ('file:///private/x','http://localhost:8787','http://192.168.1.9/a','https://user:pass@example.com'):
   with self.assertRaises(ValueError):self.inbox.capture({'source_kind':'url','content':url})
  item=self.inbox.capture({'source_kind':'text','content':'Project Aurora launch notes','retention_seconds':60})
  self.assertFalse(item['duplicate']);self.assertTrue(self.inbox.capture({'source_kind':'text','content':'Project Aurora launch notes'})['duplicate'])
  self.assertEqual(len(self.inbox.list()),1);self.assertEqual(self.inbox.delete(item['id'])['deleted'],1);self.assertEqual(self.inbox.list(),[])
 def test_sharing_requires_both_policy_and_each_request_approval(self):
  self.inbox.configure({'sources':{'text':True}});item=self.inbox.capture({'source_kind':'text','content':'local planning context'})
  with self.assertRaises(ValueError):self.inbox.share({'assistant_id':'external-model','event_ids':[item['id']],'approved':True})
  self.inbox.set_policy({'assistant_id':'external-model','approved':True})
  with self.assertRaises(ValueError):self.inbox.share({'assistant_id':'external-model','event_ids':[item['id']]})
  payload=self.inbox.share({'assistant_id':'external-model','event_ids':[item['id']],'approved':True})
  self.assertTrue(payload['untrusted']);self.assertEqual(payload['items'][0]['content'],'local planning context')
  self.inbox.delete();self.assertEqual(self.inbox.list(),[])

if __name__=='__main__':unittest.main()
