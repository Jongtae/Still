"""INTRANET-DEST-01 (#209): owner-declared intranet hosts are reachable; every other private address is not.

Model-free and network-free: injected resolvers, a fake page opener, a fake
code-run process and a fake MCP CLI runner.  Each path is asserted both ways:
a declared host resolving to private-use space is reached, and an undeclared
private address, loopback, link-local or a metadata address is refused, even
for a declared name.  A refusal is asserted at the egress point (the fake was
never called), not only by its message.
"""
import ipaddress
import json
import pathlib
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from personal_agent import api_requests, intranet, work_mode
from personal_agent.api_requests import ApiError, ApiRequests
from personal_agent.agent_runtime import Capabilities
from personal_agent.browser_session import BrowserProfile, BrowserSession, http_refresh, local_destination
from personal_agent.browser_worker import Worker, destination_refusal
from personal_agent.code_run import CodeRunError, CodeRunner, _arguments
from personal_agent.local_tools import LocalTools, normalize_public_url
from personal_agent.mcp_setup import RemoteMcpSetup, remote_url
from personal_agent.quickstart import configured_service, make_handler
from personal_agent.quickstart_store import QuickStore
from personal_agent.search_providers import ProviderRegistry
from personal_agent.settings_orchestrator import SettingsError

CFG = {'provider': 'compatible', 'endpoint': 'https://openrouter.ai/api/v1', 'model': 'openrouter/free'}
HOST = 'wiki.corp.internal'


def resolving(*addresses):
    def resolver(host, port, **kwargs):
        return [(2, 1, 6, '', (address, port)) for address in addresses]
    return resolver


class Case(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        self.store = QuickStore(self.root / 'data')

    def declare(self, text=HOST):
        return intranet.add(self.store, text, surface='settings')

    def declared(self, host, port):
        return intranet.declared(self.store, host, port)


class Entries(Case):
    def test_only_an_exact_host_name_with_an_optional_port_is_an_entry(self):
        self.assertEqual(intranet.parse('Wiki.Corp.Internal.'), ('wiki.corp.internal', None))
        self.assertEqual(intranet.parse('gitlab.corp.example:8443'), ('gitlab.corp.example', 8443))
        for text in ('10.0.0.5', '[fd00::1]', '::1', '*.corp.example', 'corp.example*', 'localhost', 'app.localhost',
                     'metadata.google.internal', 'https://wiki.corp.internal', 'wiki.corp.internal/path',
                     'user@wiki.corp.internal', 'intranet', 'wiki.corp.internal:0', 'wiki.corp.internal:70000',
                     'wiki.corp.internal:', 'ä.corp.example', '', None, 'a' * 300):
            with self.subTest(text=text), self.assertRaises(ValueError):
                intranet.parse(text)

    def test_add_list_remove_with_an_audit(self):
        self.declare()
        self.declare('gitlab.corp.example:8443')
        self.declare(HOST.upper())   # the same entry again changes nothing
        labels = [row['label'] for row in intranet.status(self.store)['entries']]
        self.assertEqual(labels, [HOST, 'gitlab.corp.example:8443'])
        intranet.remove(self.store, HOST, surface='settings')
        self.assertEqual([row['host'] for row in intranet.entries(self.store)], ['gitlab.corp.example'])
        with self.assertRaises(ValueError):
            intranet.remove(self.store, HOST, surface='settings')
        audit = self.store.config(intranet.AUDIT_KEY)
        self.assertEqual([(row['action'], row['host'], row['surface']) for row in audit],
                         [('add', HOST, 'settings'), ('add', 'gitlab.corp.example', 'settings'),
                          ('remove', HOST, 'settings')])

    def test_the_list_is_bounded(self):
        for index in range(intranet.MAX_ENTRIES):
            self.declare(f'h{index}.corp.example')
        with self.assertRaises(ValueError):
            self.declare('one-more.corp.example')

    def test_a_port_entry_admits_that_port_and_no_port_the_default_ports_only(self):
        self.declare()
        self.declare('gitlab.corp.example:8443')
        self.assertTrue(self.declared(HOST, 443))
        self.assertTrue(self.declared(HOST, 80))
        self.assertTrue(self.declared('WIKI.corp.internal.', 443))
        self.assertFalse(self.declared(HOST, 8443), 'a declared host plus a non-declared port is refused')
        self.assertTrue(self.declared('gitlab.corp.example', 8443))
        self.assertFalse(self.declared('gitlab.corp.example', 443))
        self.assertFalse(self.declared('other.corp.internal', 443), 'an undeclared name under the same suffix')
        self.assertEqual(intranet.origin_keys(self.store),
                         {f'{HOST}:443', f'{HOST}:80', 'gitlab.corp.example:8443'})

    def test_a_malformed_row_declares_nothing(self):
        self.store.put(intranet.CONFIG_KEY, [{'host': '10.0.0.5'}, {'host': '*.corp'}, {'host': HOST, 'port': True},
                                             {'host': HOST, 'port': '443'}, 'wiki.corp.internal', {'host': 'ok.corp.example'}])
        self.assertEqual(intranet.entries(self.store), [{'host': 'ok.corp.example', 'port': None}])
        self.store.put(intranet.CONFIG_KEY, {'host': HOST})
        self.assertEqual(intranet.entries(self.store), [])
        self.assertEqual(intranet.entries(None), [])


class Addresses(unittest.TestCase):
    def test_a_declared_host_may_reach_private_use_and_global_space(self):
        for raw in ('10.1.2.3', '172.16.0.1', '192.168.1.10', '100.64.1.1', 'fd12:3456::1', '93.184.216.34'):
            with self.subTest(address=raw):
                self.assertTrue(intranet.address_admitted(ipaddress.ip_address(raw)))

    def test_never_this_mac_link_local_metadata_or_special_purpose_space(self):
        for raw in ('127.0.0.1', '127.8.8.8', '::1', '0.0.0.0', '::', '169.254.169.254', 'fe80::1', '224.0.0.1',
                    'ff02::1', '255.255.255.255', '100.100.100.200', '168.63.129.16', '192.0.0.192', 'fd00:ec2::254',
                    'fd20:ce::254', '192.0.2.1', '198.18.0.1', '240.0.0.1', '2001:db8::1',
                    '::ffff:127.0.0.1', '::ffff:169.254.169.254', '2002:7f00:1::1'):
            with self.subTest(address=raw):
                self.assertFalse(intranet.address_admitted(ipaddress.ip_address(raw)))


class PageRead(Case):
    def tools(self):
        return LocalTools(providers=ProviderRegistry.from_store(self.store))

    def test_a_declared_host_with_a_private_name_and_address_is_read(self):
        self.declare()
        tools = self.tools()
        tools.page_reader.resolver = resolving('10.20.30.40')
        url = f'https://{HOST}/page'
        self.assertEqual(normalize_public_url(url, tools.destination_declared), url)
        self.assertEqual(tools.page_reader._validate_host(url), {'10.20.30.40'})

    def test_undeclared_private_names_and_addresses_stay_refused(self):
        tools = self.tools()
        with self.assertRaises(ValueError):
            normalize_public_url(f'https://{HOST}/', tools.destination_declared)
        tools.page_reader.resolver = resolving('10.20.30.40')
        with self.assertRaises(ValueError):
            tools.page_reader._validate_host('https://public-name.example/')

    def test_a_declared_name_never_reaches_this_mac_or_a_metadata_address(self):
        self.declare()
        tools = self.tools()
        for address in ('127.0.0.1', '169.254.169.254', 'fd00:ec2::254', '10.0.0.1 127.0.0.1'):
            with self.subTest(address=address):
                tools.page_reader.resolver = resolving(*address.split())
                with self.assertRaises(ValueError):
                    tools.page_reader._validate_host(f'https://{HOST}/')

    def test_an_undeclared_port_is_refused(self):
        self.declare()
        tools = self.tools()
        with self.assertRaises(ValueError):
            normalize_public_url(f'https://{HOST}:8443/', tools.destination_declared)

    def test_a_redirect_to_an_undeclared_private_host_is_not_followed(self):
        self.declare()
        tools = self.tools()
        urls = []

        class Redirect:
            status = 302
            headers = {'Location': 'https://other.corp.internal/'}

            def __init__(self, url):
                self.url = url

            def read(self, *a):
                return b''

            def close(self):
                pass

        class Opener:
            def open(self, request, timeout=None):
                urls.append(request.full_url)
                return Redirect(request.full_url)
        tools.page_reader.opener = Opener()
        tools.page_reader.resolver = resolving('10.0.0.8')
        with self.assertRaises(ValueError):
            tools.page_reader.read(f'https://{HOST}/')
        self.assertEqual(urls, [f'https://{HOST}/'])

    def test_a_removed_host_is_refused_from_the_next_request(self):
        self.declare()
        tools = self.tools()
        tools.page_reader.resolver = resolving('10.0.0.8')
        tools.page_reader._validate_host(f'https://{HOST}/')
        intranet.remove(self.store, HOST, surface='settings')
        with self.assertRaises(ValueError):
            tools.page_reader.read(f'https://{HOST}/')

    def test_an_owner_approved_intranet_page_reaches_the_reader(self):
        self.declare('jira.corp.internal')
        url = 'https://jira.corp.internal/browse/X-1'
        plans = []

        class Network:
            destination_declared = staticmethod(self.declared)

            def execute(inner, plan):
                plans.append(plan)
                return {'tool': plan['tool'], 'url': plan['url'], 'content': 'ok', 'sources': [], 'results': []}
        caps = Capabilities(self.store, None, CFG, '', 'job', lambda *a: None, network=Network(), public_page_scope=[url])
        caps.execute('public_page_read', {'url': url})
        self.assertEqual([plan['url'] for plan in plans], [url])


class WorkMode(Case):
    def setUp(self):
        super().setUp()
        self.declare('jira.corp.example')
        work_mode.set_enabled(self.store, True, surface='settings')

    def test_only_calls_naming_declared_destinations_pass_the_tool_gate(self):
        passes = [('public_page_read', {'url': 'https://jira.corp.example/x'}),
                  ('api_request', {'url': 'https://jira.corp.example/rest/api/2/issue/X-1'}),
                  ('code_run', {'code': 'print(1)', 'hosts': 'jira.corp.example'})]
        refused = [('public_page_read', {'url': 'https://example.com/'}),
                   ('public_page_read', {'url': 'https://jira.corp.example:8443/'}),
                   ('weather', {'city': 'Seoul'}),
                   ('browser_open', {'url': 'https://jira.corp.example/'}),
                   ('api_request', {'url': 'https://api.example.com/'}),
                   ('code_run', {'code': 'print(1)', 'hosts': 'jira.corp.example', 'packages': 'requests==2.0'}),
                   ('code_run', {'code': 'print(1)', 'hosts': 'jira.corp.example, api.example.com'}),
                   ('code_run', {'code': 'print(1)'}),
                   ('web_search', {'query': 'jira.corp.example'})]
        for action, args in passes:
            with self.subTest(action=action, args=args):
                self.assertIsNone(work_mode.tool_refusal(self.store, action, args))
        for action, args in refused:
            with self.subTest(action=action, args=args):
                self.assertIsInstance(work_mode.tool_refusal(self.store, action, args), work_mode.WorkModeRefused)

    def test_each_request_is_checked_at_its_egress_point(self):
        from personal_agent.api_requests import ApiError, ApiRequests
        sent = []
        api = ApiRequests(self.store, 'job', transport=lambda *args: sent.append(args[1]) or (200, {}, b'{}'),
                          resolver=resolving('10.1.1.1'))
        api.transport('GET', 'https://jira.corp.example/rest', {}, None, 5)
        with self.assertRaises(ApiError):
            api.transport('GET', 'https://api.example.com/', {}, None, 5)
        self.assertEqual(sent, ['https://jira.corp.example/rest'])
        tools = LocalTools(providers=ProviderRegistry.from_store(self.store))
        self.assertIsNone(tools.egress_refusal('https://jira.corp.example/x'))
        self.assertIsNotNone(tools.egress_refusal('https://example.com/'))
        self.assertIsNotNone(tools.egress_refusal(), 'weather and lookups name no declared destination')

    def test_an_api_input_probe_to_a_declared_host_may_be_verified(self):
        from types import SimpleNamespace
        from personal_agent.api_input import refuse_probe_in_work_mode
        service = SimpleNamespace(store=self.store)
        refuse_probe_in_work_mode(service, {'probe': {'url': 'https://jira.corp.example/rest/myself'}})
        with self.assertRaises(ValueError):
            refuse_probe_in_work_mode(service, {'probe': {'url': 'https://api.example.com/me'}})

    def test_a_code_run_reaches_only_the_declared_host_in_work_mode(self):
        runner = CodeRunner(self.store, 'job')
        runner._intranet_hosts = frozenset({'jira.corp.example'})
        self.assertEqual(runner._decide({'method': 'GET', 'host': 'jira.corp.example', 'port': 443, 'path': '/'},
                                        {'jira.corp.example': set()}), (True, ''))
        allow, reason = runner._decide({'method': 'GET', 'host': 'pypi.org', 'port': 443, 'path': '/'},
                                       {'pypi.org': set()})
        self.assertFalse(allow)
        self.assertIn('회사 모드', reason)


class ApiCalls(Case):
    """#284 review: a declared host's DNS answers are checked, and the request goes to the checked address."""

    def client(self, *addresses, transport=None):
        self.sent = []
        return ApiRequests(self.store, 'job', resolver=resolving(*addresses),
                           transport=transport or (lambda *args: self.sent.append(args[1]) or (200, {}, b'{}')))

    def test_a_declared_host_resolving_to_private_use_space_is_sent(self):
        self.declare()
        self.client('10.2.2.2').transport('GET', f'https://{HOST}/rest', {}, None, 5)
        self.assertEqual(self.sent, [f'https://{HOST}/rest'])

    def test_a_declared_name_resolving_to_this_mac_or_metadata_is_refused_unsent(self):
        self.declare()
        for address in ('127.0.0.1', '169.254.169.254', 'fd00:ec2::254', '10.2.2.2 127.0.0.1'):
            for mode in (False, True):
                with self.subTest(address=address, work_mode=mode):
                    work_mode.set_enabled(self.store, mode, surface='settings')
                    api = self.client(*address.split())
                    with self.assertRaises(ApiError) as caught:
                        api.transport('GET', f'https://{HOST}/rest', {}, None, 5)
                    self.assertEqual((caught.exception.code, self.sent), ('api_host_not_allowed', []))

    def test_the_production_transport_connects_to_the_checked_address(self):
        self.declare()
        connected = []

        def pinned(method, url, headers, body, timeout, addresses):
            connected.append((url, addresses))
            return 200, {}, b'{}'
        with mock.patch.object(api_requests, 'pinned_http_request', pinned):
            ApiRequests(self.store, 'job', resolver=resolving('10.2.2.2')).transport('GET', f'https://{HOST}/r', {}, None, 5)
        self.assertEqual(connected, [(f'https://{HOST}/r', ['10.2.2.2'])])

    def test_the_pinned_request_never_resolves_the_name_again(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers['Host'])
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_port
        status, _headers, body = api_requests.pinned_http_request(
            'GET', f'http://{HOST}:{port}/x', {}, None, 5, ['127.0.0.1'])
        self.assertEqual((status, body, seen), (200, b'{}', [f'{HOST}:{port}']))


class CodeRun(Case):
    def test_a_declared_host_with_a_private_name_is_an_argument(self):
        args = {'code': 'print(1)', 'hosts': HOST}
        with self.assertRaises(CodeRunError):
            _arguments(args)
        self.declare()
        runner = CodeRunner(self.store, 'job')
        self.assertEqual(_arguments(args, runner._intranet_host)[2], [HOST])

    def test_a_run_never_mixes_declared_and_other_hosts(self):
        self.declare()
        runner = CodeRunner(self.store, 'job', node='/nonexistent/node', install=lambda store, node: str(self.root))
        with self.assertRaises(CodeRunError) as caught:
            runner.run({'code': 'print(1)', 'hosts': f'{HOST}, api.example.com'})
        self.assertIn('사내 주소', str(caught.exception))

    def test_each_request_needs_the_declared_port_and_a_removal_applies_to_the_next(self):
        self.declare()
        runner = CodeRunner(self.store, 'job')
        runner._intranet_hosts = frozenset({HOST})
        policy = {HOST: set()}
        self.assertEqual(runner._decide({'method': 'GET', 'host': HOST, 'port': 443, 'path': '/'}, policy), (True, ''))
        self.assertFalse(runner._decide({'method': 'GET', 'host': f'{HOST}:8443', 'port': 8443, 'path': '/'}, policy)[0])
        self.assertFalse(runner._decide({'method': 'GET', 'host': HOST, 'path': '/'}, policy)[0],
                         'a request that does not say its port reaches no declared port')
        intranet.remove(self.store, HOST, surface='settings')
        allow, reason = runner._decide({'method': 'GET', 'host': HOST, 'port': 443, 'path': '/'}, policy)
        self.assertFalse(allow)
        self.assertIn(HOST, reason)

    def test_only_the_declared_port_is_reached_when_the_default_port_is_omitted(self):
        """#284 review: ``url.host`` omits a default port, so the runner's effective port is what is checked."""
        self.declare(f'{HOST}:443')
        work_mode.set_enabled(self.store, True, surface='settings')
        runner = CodeRunner(self.store, 'job')
        runner._intranet_hosts = frozenset({HOST})
        policy = {HOST: set()}
        self.assertEqual(runner._decide({'method': 'GET', 'host': HOST, 'port': 443, 'path': '/'}, policy), (True, ''))
        self.assertFalse(runner._decide({'method': 'GET', 'host': HOST, 'port': 80, 'path': '/'}, policy)[0])

    @unittest.skipUnless(shutil.which('node'), 'needs Node.js')
    def test_the_runner_reports_the_effective_port_of_each_request(self):
        """#288 review: the real ``runner.mjs`` runs, with sandbox-runtime replaced by a stub that
        hands its ``filterRequest`` URLs whose default port is omitted; the requests it asks
        about carry the effective port."""
        urls = ['http://wiki.corp.internal/a', 'https://wiki.corp.internal/b', 'https://wiki.corp.internal:8443/c']
        package = self.root / 'runner' / 'node_modules' / '@anthropic-ai' / 'sandbox-runtime'
        package.mkdir(parents=True)
        (package / 'package.json').write_text(json.dumps({'name': '@anthropic-ai/sandbox-runtime', 'type': 'module',
                                                          'exports': './index.js'}))
        (package / 'index.js').write_text(
            'let filter\n'
            'export const SandboxManager = {\n'
            '  async initialize (config) { filter = config.network.filterRequest },\n'
            "  getSentinelRegistry () { return { register: () => 'sentinel' } },\n"
            '  async wrapWithSandbox (command) {\n'
            f'    for (const url of {json.dumps(urls)}) await filter(new Request(url))\n'
            "    return 'true'\n"
            '  },\n'
            '  annotateStderrWithSandboxFailures (command, stderr) { return stderr },\n'
            '  async reset () {}\n'
            '}\n')
        source = pathlib.Path(__file__).resolve().parents[1] / 'src/personal_agent/sandbox/runner.mjs'
        runner = self.root / 'runner' / 'runner.mjs'
        runner.write_text(source.read_text())
        process = subprocess.Popen([shutil.which('node'), str(runner)], cwd=str(runner.parent), text=True,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        process.stdin.write(json.dumps({'workdir': str(self.root), 'argv': ['true'], 'allowedDomains': [HOST],
                                        'deniedResolvedAddresses': [], 'denyRead': [], 'allowRead': [], 'env': {},
                                        'masked': [], 'timeoutMs': 10000, 'outputBytes': 1000}) + '\n')
        process.stdin.flush()
        asked, result = [], None
        for line in process.stdout:
            message = json.loads(line)
            if message.get('type') == 'request':
                asked.append((message['host'], message['port']))
                process.stdin.write(json.dumps({'id': message['id'], 'allow': True}) + '\n')
                process.stdin.flush()
            elif message.get('type') in ('result', 'error'):
                result = message
                break
        self.assertEqual(result and result.get('type'), 'result', (result, process.stderr.read() if result is None else ''))
        self.assertEqual(asked, [(HOST, 80), (HOST, 443), (f'{HOST}:8443', 8443)])

    def test_the_sandbox_refuses_private_use_space_unless_the_run_reaches_a_declared_host(self):
        from test_code_run import FakeProcess
        self.declare()
        for hosts, private_denied in (('api.example.com', True), (HOST, False)):
            with self.subTest(hosts=hosts):
                process = FakeProcess()
                runner = CodeRunner(self.store, 'job', popen=lambda *a, **k: process, node='/usr/bin/node',
                                    install=lambda store, node: str(self.root))
                runner.run({'code': 'print(1)', 'hosts': hosts})
                denied = process.config['deniedResolvedAddresses']
                self.assertEqual(set(intranet.PRIVATE_USE) <= set(denied), private_denied)
                self.assertLessEqual(set(intranet.SPECIAL_PURPOSE), set(denied))


class Browser(Case):
    KEYS = frozenset({f'{HOST}:443'})

    def test_a_declared_origin_resolving_to_private_use_space_loads(self):
        url = f'https://{HOST}/wiki'
        self.assertTrue(local_destination(url))
        self.assertFalse(local_destination(url, (), self.KEYS))
        self.assertIsNone(destination_refusal(url, resolver=resolving('10.4.4.4'), intranet=self.KEYS))

    def test_undeclared_or_unsafe_destinations_stay_blocked(self):
        cases = ((f'https://{HOST}/', '10.4.4.4', frozenset()),
                 (f'https://{HOST}:8443/', '10.4.4.4', self.KEYS),
                 (f'https://{HOST}/', '127.0.0.1', self.KEYS),
                 (f'https://{HOST}/', '169.254.169.254', self.KEYS),
                 ('https://public.example/', '10.4.4.4', self.KEYS))
        for url, address, keys in cases:
            with self.subTest(url=url, address=address):
                self.assertEqual(destination_refusal(url, resolver=resolving(address), intranet=keys),
                                 'blocked_destination')

    def test_the_profile_hands_the_current_keys_to_every_session_and_refresh(self):
        profile = BrowserProfile(self.root / 'profile', launcher=lambda *a: None, available=lambda: True)
        self.declare()
        profile.intranet = lambda: intranet.origin_keys(self.store)
        session = BrowserSession(profile.driver_factory('job'), work_id='job')
        self.assertIn(f'{HOST}:443', session._intranet())
        intranet.remove(self.store, HOST, surface='settings')
        self.assertEqual(session._intranet(), frozenset(), 'read again for every step')
        profile.intranet = lambda: (_ for _ in ()).throw(OSError('unreadable'))
        self.assertEqual(profile.intranet_keys(), frozenset(), 'an unreadable list declares nothing')
        checked = []
        http_refresh(f'https://{HOST}/', [], intranet=self.KEYS,
                     refusal=lambda url, allowed, intranet=(): checked.append(intranet) or 'blocked_destination')
        self.assertEqual(checked, [self.KEYS])

    def test_the_worker_forgets_judged_origins_when_the_keys_change(self):
        worker = Worker.__new__(Worker)
        worker.intranet, worker.resolved, worker.fail = frozenset(), {f'{HOST}:443': 'blocked_destination'}, lambda *a: None
        worker.handle({'id': 1, 'op': 'nothing', 'intranet': sorted(self.KEYS)})
        self.assertEqual((worker.intranet, worker.resolved), (self.KEYS, {}))
        worker.resolved[f'{HOST}:443'] = None
        worker.handle({'id': 2, 'op': 'nothing_stop'})
        self.assertEqual((worker.intranet, worker.resolved), (self.KEYS, {f'{HOST}:443': None}),
                         'a command without keys (a stop) changes nothing')
        worker.handle({'id': 3, 'op': 'nothing', 'intranet': []})
        self.assertEqual((worker.intranet, worker.resolved), (frozenset(), {}))


class McpSetup(Case):
    def setUp(self):
        super().setUp()
        self.calls = []

        def runner(argv, **kwargs):
            self.calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, '', '')
        self.runner = runner

    def setup(self, *addresses):
        return RemoteMcpSetup('/owner/bin/claude', self.root, runner=self.runner, resolver=resolving(*addresses),
                              declared=self.declared)

    def test_a_declared_intranet_server_is_registered(self):
        url = f'https://{HOST}/mcp'
        with self.assertRaises(ValueError):
            remote_url(url)
        self.declare()
        self.assertEqual(remote_url(url, declared=self.declared), url)
        self.assertEqual(self.setup('10.9.9.9').register('claude-code', 'wiki', url)['state'], 'registration-returned')
        self.assertEqual(self.calls[0][-1], url)

    def test_undeclared_or_unsafe_destinations_reach_no_cli(self):
        url = f'https://{HOST}/mcp'
        with self.assertRaises(ValueError):
            self.setup('10.9.9.9').register('claude-code', 'wiki', url)
        self.declare()
        self.assertEqual(self.setup('127.0.0.1').register('claude-code', 'wiki', url)['state'], 'unknown')
        self.assertEqual(self.setup('10.9.9.9').register('claude-code', 'wiki', 'https://public.example/mcp')['state'],
                         'unknown')
        self.assertEqual(self.calls, [])

    def test_the_status_helper_is_told_only_that_this_url_is_declared(self):
        self.declare()
        self.setup('10.9.9.9').observe('claude-code', 'wiki', f'https://{HOST}/mcp')
        self.assertEqual(self.calls[0][-1], '--intranet')
        self.calls.clear()
        self.setup('93.184.216.34').observe('claude-code', 'pub', 'https://public.example/mcp')
        self.assertNotIn('--intranet', self.calls[0])


class SettingsOnly(Case):
    def setUp(self):
        super().setUp()
        self.service = configured_service(self.store, {})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.service))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.session = self.store.local_session()

    def post(self, body, tunneled=False):
        headers = {'Content-Type': 'application/json', 'Cookie': f'agentos_session={self.session}', 'Origin': self.base}
        if tunneled:
            headers.update({'X-Forwarded-For': '203.0.113.9', 'X-Forwarded-Proto': 'https'})
        try:
            with urlopen(Request(self.base + '/api/intranet-destinations', data=json.dumps(body).encode(),
                                 method='POST', headers=headers), timeout=10) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')

    def test_settings_adds_lists_and_removes_hosts(self):
        status, body = self.post({'action': 'add', 'host': 'gitlab.corp.example:8443'})
        self.assertEqual(status, 200, body)
        self.assertEqual([row['label'] for row in body['entries']], ['gitlab.corp.example:8443'])
        self.assertEqual(self.post({'action': 'add', 'host': '*.corp.example'})[0], 400)
        self.assertEqual(self.post({'action': 'drop', 'host': 'gitlab.corp.example:8443'})[0], 400)
        status, body = self.post({'action': 'remove', 'host': 'gitlab.corp.example:8443'})
        self.assertEqual((status, body['entries']), (200, []))

    def test_a_change_through_a_tunnel_is_refused(self):
        status, _body = self.post({'action': 'add', 'host': HOST}, tunneled=True)
        self.assertEqual(status, 400)
        self.assertEqual(intranet.entries(self.store), [])

    def test_a_conversation_proposal_cannot_add_a_host(self):
        orchestrator = self.service.settings_orchestrator
        for category, setting in (('intranet', 'add'), ('intranet_destinations', 'hosts'), ('work_mode', 'intranet')):
            with self.subTest(category=category), self.assertRaises(SettingsError):
                orchestrator.propose('local-owner', 'web', category, setting, HOST, reason='회사 위키')
        self.assertEqual(intranet.entries(self.store), [])

    def test_only_the_intranet_module_writes_the_list(self):
        root = pathlib.Path(__file__).resolve().parents[1] / 'src'
        writers = [path.name for path in root.rglob('*.py')
                   if 'intranet_destinations' in path.read_text(encoding='utf-8') and path.name != 'intranet.py']
        self.assertEqual(writers, [], 'no Work tool or settings path names the stored list')


if __name__ == '__main__':
    unittest.main()
