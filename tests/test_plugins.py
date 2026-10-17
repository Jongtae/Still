import json,tempfile,unittest
from pathlib import Path
from personal_agent.plugins import PluginRegistry
from personal_agent.quickstart import plugins_main
from personal_agent.agent_runtime import Capabilities
from personal_agent.quickstart_store import QuickStore
class PluginTests(unittest.TestCase):
 def test_lifecycle(self):
  with tempfile.TemporaryDirectory() as folder:
   manifest=Path(folder)/'demo.json';manifest.write_text(json.dumps({'version':1,'id':'demo','tools':[{'id':'notes','host_action':'list_notes','mode':'read_only'}],'roles':[]}))
   registry=PluginRegistry(Path(folder)/'data');self.assertEqual(registry.install(manifest),'demo');self.assertTrue(registry.list()[0]['enabled']);self.assertFalse(registry.set_enabled('demo',False)['enabled']);registry.remove('demo');self.assertEqual(registry.list(),[])
 def test_cli_lists_declaration_plugins(self):
  with tempfile.TemporaryDirectory() as folder:
   manifest=Path(folder)/'demo.json';manifest.write_text(json.dumps({'version':1,'id':'demo','tools':[],'roles':[]}))
   data=Path(folder)/'data';plugins_main(['--data',str(data),'install',str(manifest)])
   self.assertTrue(PluginRegistry(data).list()[0]['enabled'])
 def test_enabled_package_activates_only_declared_role_tools(self):
  with tempfile.TemporaryDirectory() as folder:
   data=Path(folder)/'data';manifest=Path(folder)/'review.json'
   manifest.write_text(json.dumps({'version':1,'id':'review','tools':[{'id':'review_notes','host_action':'list_notes','mode':'read_only'}],'roles':[{'id':'package_reviewer','name':'Package reviewer','instructions':'Review supplied material.','permissions':['read_only'],'tools':['review_notes']}]}))
   registry=PluginRegistry(data);registry.install(manifest);packages=registry.runtime_packages()
   caps=Capabilities(QuickStore(data),None,{},'','job',lambda *a:None,packages=packages)
   self.assertIn('review_notes',{item['function']['name'] for item in caps.definitions()})
   self.assertEqual(caps.execute('review_notes',{})['notes'],[])
   role=caps.roles['package_reviewer'];child=Capabilities(caps.store,None,{},'','job',lambda *a:None,readonly=True,packages=packages,allowed_tools=role['tools'])
   self.assertEqual([item['function']['name'] for item in child.definitions()],['review_notes'])
   registry.set_enabled('review',False);disabled=registry.runtime_packages()
   self.assertNotIn('package_reviewer',{role['id'] for package in disabled for role in package['roles']})
   self.assertFalse(registry.declared_packages()[-1]['enabled'])
   with self.assertRaises(ValueError):Capabilities(QuickStore(data),None,{},'','job',lambda *a:None,packages=disabled).execute('review_notes',{})
 def test_runtime_rejects_tampered_declaration(self):
  with tempfile.TemporaryDirectory() as folder:
   data=Path(folder)/'data';registry=PluginRegistry(data)
   (registry.root/'unsafe.json').write_text(json.dumps({'version':1,'id':'unsafe','enabled':True,'tools':[{'id':'write','host_action':'save_note','mode':'read_only'}],'roles':[]}))
   with self.assertRaises(ValueError):registry.runtime_packages()
 def test_role_with_no_declared_tools_receives_no_tools(self):
  with tempfile.TemporaryDirectory() as folder:
   data=Path(folder)/'data';manifest=Path(folder)/'silent.json'
   manifest.write_text(json.dumps({'version':1,'id':'silent','tools':[],'roles':[{'id':'silent_reviewer','name':'Silent reviewer','instructions':'Review supplied material.','permissions':['read_only'],'tools':[]}]}))
   registry=PluginRegistry(data);registry.install(manifest)
   caps=Capabilities(QuickStore(data),None,{},'','job',lambda *a:None,packages=registry.runtime_packages())
   role=caps.roles['silent_reviewer']
   child=Capabilities(caps.store,None,{},'','job',lambda *a:None,readonly=True,packages=caps.packages,allowed_tools=role['tools'])
   self.assertEqual(child.definitions(),[])
   with self.assertRaises(ValueError):child.execute('list_notes',{})
