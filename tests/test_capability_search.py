"""CONNECT-FIND-01 metadata path: injected registry, real owner relay/store."""
import json
import os
import subprocess
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from personal_agent.capability_search import CapabilitySearch, MAX_BYTES, REGISTRY, TTL
from personal_agent.agent_runtime import Capabilities, DEFINITIONS, ToolError
from personal_agent.bounded_execution import AgentOSMcpTools, StrictIsolatedAgentOSMcpTools
from personal_agent.cli_browser_relay import BrowserRelay, RelayClient
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore


def payload(**changes):
    server={'name':'org.example/novel','version':'1.2.3','description':'A previously unseen capability',
            'repository':{'url':'https://example.org/project'},
            'remotes':[{'type':'streamable-http','url':'https://example.org/mcp'}],
            'packages':[{'registryType':'npm','identifier':'@example/novel','version':'1.2.3'}]}
    server.update(changes)
    return json.dumps({'servers':[{'server':server,'_meta':{'io.modelcontextprotocol.registry/official':{'status':'active'}}}]}).encode()


class RegistryTests(unittest.TestCase):
    def test_fixed_anonymous_get_and_cache_expiry(self):
        now=[100.0];transport=Mock(return_value=(200,{},payload()))
        search=CapabilitySearch(transport,lambda:now[0])
        first=search.registry('org.example/novel')
        method,url,headers,body,timeout=transport.call_args.args
        self.assertEqual(method,'GET');self.assertTrue(url.startswith(REGISTRY+'?'))
        self.assertEqual(parse_qs(urlsplit(url).query),{'search':['org.example/novel'],'limit':['10'],'version':['latest']})
        self.assertIsNone(body);self.assertEqual(headers,{'Accept':'application/json'});self.assertEqual(timeout,10)
        row=first['results'][0]
        self.assertEqual((row['state'],row['authenticated'],row['authority']),('discovered','unknown','none'))
        self.assertEqual(row['licence'],'unknown')
        self.assertFalse(search.registry('org.example/novel')['contacted']);self.assertEqual(transport.call_count,1)
        now[0]+=TTL
        self.assertTrue(search.registry('org.example/novel')['contacted']);self.assertEqual(transport.call_count,2)

    def test_private_freeform_and_secret_queries_never_leave(self):
        transport=Mock();search=CapabilitySearch(transport)
        for query in ('my private request','a@private.example','https://example.org','x'*81,'token=abc','',None):
            with self.subTest(query=query),self.assertRaises(ValueError):search.registry(query)
        with self.assertRaises(ValueError):search.registry('private-value',lambda text:'[redacted]')
        transport.assert_not_called()

    def test_failures_and_hostile_metadata_are_not_readiness(self):
        for response in ((302,{'location':'http://localhost/'},b''),(200,{},b'x'*(MAX_BYTES+1)),
                         (200,{},b'{bad'),(200,{},b'{"servers":null}'),(503,{},b'private failure text')):
            with self.subTest(response=response[:2]):
                result=CapabilitySearch(Mock(return_value=response)).registry('novel')
                self.assertEqual(result['state'],'unavailable');self.assertEqual(result['results'],[])
                self.assertNotIn('private failure',json.dumps(result))
        data=json.loads(payload());data['metadata']={'nextCursor':'opaque'}
        data['servers'][0]['server']['remotes']=[{'type':'http','url':'https://example.org/mcp?token=hidden'}]
        result=CapabilitySearch(Mock(return_value=(200,{},json.dumps(data).encode()))).registry('novel')
        self.assertEqual(result['completeness'],'partial');self.assertEqual(result['results'][0]['remotes'],[])
        self.assertNotIn('hidden',json.dumps(result))
        data['servers'][0]['_meta']['io.modelcontextprotocol.registry/official']['status']='deleted'
        self.assertEqual(CapabilitySearch(Mock(return_value=(200,{},json.dumps(data).encode()))).registry('novel')['results'],[])

    def test_known_secret_response_is_omitted_and_timeout_is_bounded(self):
        result=CapabilitySearch(Mock(return_value=(200,{},payload(description='synthetic-secret')))).registry('novel',lambda text:text.replace('synthetic-secret','[redacted]'))
        self.assertEqual(result['results'],[]);self.assertEqual(result['completeness'],'partial')
        result=CapabilitySearch(Mock(side_effect=TimeoutError('private message'))).registry('novel')
        self.assertEqual(result['state'],'unavailable');self.assertNotIn('private message',json.dumps(result))

    def test_cache_rechecks_current_secrets_and_outage_retries_after_short_backoff(self):
        transport=Mock(return_value=(200,{},payload(description='newly-stored-value')))
        search=CapabilitySearch(transport)
        self.assertEqual(len(search.registry('novel')['results']),1)
        result=search.registry('novel',lambda text:text.replace('newly-stored-value','[redacted]'))
        self.assertEqual(result['results'],[]);self.assertEqual(transport.call_count,1)
        now=[100.0];transport=Mock(side_effect=[TimeoutError(),(200,{},payload())])
        search=CapabilitySearch(transport,lambda:now[0])
        self.assertEqual(search.registry('novel')['state'],'unavailable')
        self.assertFalse(search.registry('novel')['contacted'])
        now[0]+=30
        self.assertEqual(search.registry('novel')['state'],'observed')


class RelayTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=QuickStore(Path(temp.name)/'state');self.service=AgentService(self.store)
        self.job=self.store.job(self.store.enqueue('Find a usable method and complete the task','original'))
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?",(self.job['id'],))
        self.events=[]
        def record(tool,status,detail):
            self.events.append((tool,status,detail))
            with self.store.db() as db:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',(self.job['id'],tool,status,detail,time.time()))
        self.caps=Capabilities(self.store,None,{},'',self.job['id'],record,
                               settings=self.service.settings_tools(self.job),secret_redactor=self.service._redact_known_secrets)
        self.tools=AgentOSMcpTools(self.caps)
        self.transport=Mock(return_value=(200,{},payload()))
        self.service.capability_search=CapabilitySearch(self.transport)

    def test_config_names_do_not_probe_or_grant_and_registry_flows_through_real_relay(self):
        relay=BrowserRelay(self.tools)
        try:
            client=RelayClient(relay.address)
            with patch.object(self.service,'owner_mcp_available',return_value={'codex':['novel']}),patch('subprocess.run',side_effect=AssertionError('discovery must not launch')):
                local=client.call('capability_search',{'source':'configured'})
                self.assertFalse(local['connections'][0]['confirmed'])
                self.assertEqual(local['connections'][0]['tools'],'unknown');self.transport.assert_not_called()
            requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}}]
            requests += [{'jsonrpc':'2.0','id':number,'method':'tools/call','params':
                          {'name':'capability_search','arguments':{'source':'registry','query':'novel'}}} for number in (2,3)]
            done=subprocess.run([sys.executable,'-m','personal_agent.mcp_bridge','--data',str(self.store.root),
                                 '--job',self.job['id'],'--browser-relay',relay.address,'--relay-no-browser'],
                                input=''.join(json.dumps(row)+'\n' for row in requests),capture_output=True,text=True,
                                timeout=20,env={**os.environ,'PYTHONPATH':str(Path('src').resolve())})
            self.assertEqual(done.returncode,0,done.stderr)
            replies={row['id']:row for line in done.stdout.splitlines() if (row:=json.loads(line)).get('id')}
            self.assertNotIn('isError',replies[2]['result'],replies)
            self.assertIn('org.example/novel',json.dumps(replies[2]))
        finally:relay.close()
        audit=self.service.work_information_use(self.job['id'])
        lookups=audit['sent_to']['lookups']
        self.assertEqual(len(lookups),1,'cache/config reads are not external transmissions')
        self.assertEqual(lookups[0]['queries'],['novel'])
        self.assertEqual(lookups[0]['by'],'official-mcp-registry')
        self.assertFalse(self.service.ai_connections_enabled())
        self.assertEqual(self.service.settings_orchestrator._drafts(),{})
        self.assertEqual(self.store.job(self.job['id'])['message'],self.job['message'])

    def test_relayed_read_result_reaches_the_attempt_judgment_and_a_change_does_not(self):
        """#1466: the judgment saw only that settings_read ran, so "no reservation" read as unsupported."""
        from personal_agent.orchestrator import observations_with_pages
        relay=BrowserRelay(self.tools)
        try:
            client=RelayClient(relay.address)
            read=client.call('settings_read',{'category':'preparations'})
            try:client.call('settings_change',{'category':'preparations','change':'turn reminders off'})
            except Exception:pass
        finally:relay.close()
        pages=self.caps.take_browser_pages()
        self.assertEqual([title for _url,title,_text in pages],['settings_read result'],'only the effect-free read is kept')
        self.assertEqual(json.loads(pages[0][2]),json.loads(json.dumps(read,ensure_ascii=False,default=str)))
        observed=observations_with_pages(['- settings_read: {"category": "preparations"}'],pages)
        self.assertIn('settings_read result',observed)
        self.assertEqual(self.caps.take_browser_pages(),[],'judged once')

    def test_unwired_strict_and_delegated_routes_do_not_gain_discovery(self):
        self.assertIn('capability_search',{row['name'] for row in self.tools.definitions()})
        self.assertNotIn('capability_search',{row['name'] for row in StrictIsolatedAgentOSMcpTools(self.caps,qualification=None).definitions()})
        self.caps.delegated=True
        with self.assertRaises(ToolError):self.tools.call('capability_search',{'source':'registry','query':'novel'})
        self.transport.assert_not_called()
        self.caps.settings=None
        self.assertNotIn('capability_search',self.caps.offered_tools())

    def test_private_query_is_rejected_before_registry(self):
        self.caps.written_private=['private-value']
        with self.assertRaises(ToolError):self.tools.call('capability_search',{'source':'registry','query':'private-value'})
        self.transport.assert_not_called()

    def test_existing_connection_controls_are_representable_in_model_schema(self):
        definitions={row['function']['name']:row['function'] for row in DEFINITIONS}
        for name in ('settings_read','settings_change'):
            self.assertIn('ai_connections',definitions[name]['parameters']['properties']['category']['enum'])
        self.assertNotIn('only read operations run',definitions['settings_change']['description'])
        self.assertIn('per-action',definitions['settings_change']['description'])
