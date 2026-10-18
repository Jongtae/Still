"""PROC-MEM-01 (#1482, #1486): a verified function library per code-run secret set, model-free.

The store and its owner undo run on a real QuickStore; the code-run gate runs the runner
protocol with scripted processes (the real sandbox only with AGENTOS_REAL_SANDBOX_TESTS=1).
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from personal_agent import code_run
from personal_agent.code_run import CodeRunner, flow_problem
from personal_agent.owner_forget import ForgetError, OwnerForget
from personal_agent.procedures import ProcedureLibrary, module_name
from personal_agent.quickstart_store import QuickStore

OWNER = 'owner'
SOURCE_V1 = 'def balance():\n    """The balance."""\n    return 1\n'
SOURCE_V2 = 'def balance():\n    """The balance, v2."""\n    return 2\n'
FUNCTIONS = [{'name': 'balance', 'signature': '()', 'doc': 'The balance.', 'required': []}]
SOURCE_B = 'def history():\n    """The history."""\n    return []\n'
FUNCTIONS_B = [{'name': 'history', 'signature': '()', 'doc': 'The history.', 'required': []}]


class Store(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(self.temp.name)
        self.library = ProcedureLibrary(self.store)

    def test_a_work_that_did_not_reach_its_outcome_saves_nothing(self):
        self.library.propose('w1', 'set-a', SOURCE_V1, FUNCTIONS)
        self.assertEqual(self.library.settle('w1', False), [])
        self.assertIsNone(self.library.current('set-a'))
        # Its own continuation still imports the proposal; no other Work sees it.
        self.assertEqual(self.library.for_run('set-a', 'w1')['source'], SOURCE_V1)
        self.assertIsNone(self.library.for_run('set-a', 'w2'))

    def test_a_reached_work_saves_the_next_version_and_keeps_the_previous_one(self):
        self.library.propose('w1', 'set-a', SOURCE_V1, FUNCTIONS)
        saved = self.library.settle('w1', True)
        self.assertEqual([(row['slot'], row['version'], row['functions']) for row in saved], [('set-a', 1, ['balance'])])
        self.library.propose('w2', 'set-a', SOURCE_V2, FUNCTIONS)
        self.assertEqual(self.library.settle('w2', True)[0]['version'], 2)
        self.assertEqual(self.library.current('set-a')['source'], SOURCE_V2)
        with self.store.db() as db:
            states = dict(db.execute('SELECT version,state FROM procedure_versions').fetchall())
        self.assertEqual(states, {1: 'kept', 2: 'current'})
        self.assertEqual(self.library.for_run('set-a', 'w3')['version'], 2)

    def test_adding_a_function_with_the_whole_module_keeps_the_earlier_one(self):
        # #7: library replaces the module; a worker that follows the contract passes A with B and keeps both.
        self.library.propose('w1', 'set-a', SOURCE_V1, FUNCTIONS)
        self.library.settle('w1', True)
        self.library.propose('w2', 'set-a', SOURCE_V1 + SOURCE_B, FUNCTIONS + FUNCTIONS_B)
        saved = self.library.settle('w2', True)
        self.assertEqual([(row['version'], row['functions'], row['dropped']) for row in saved],
                         [(2, ['balance', 'history'], [])])
        self.assertEqual([item['name'] for item in self.library.current('set-a')['functions']], ['balance', 'history'])

    def test_a_module_that_leaves_out_a_saved_function_says_so_and_the_earlier_version_stays_recoverable(self):
        v1 = self.library.propose('w1', 'set-a', SOURCE_V1, FUNCTIONS)
        self.library.settle('w1', True)
        v2 = self.library.propose('w2', 'set-a', SOURCE_B, FUNCTIONS_B)
        saved = self.library.settle('w2', True)
        self.assertEqual((saved[0]['functions'], saved[0]['dropped']), (['history'], ['balance']))
        # Version history: v1 is kept, and forgetting v2 puts it back with its function.
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT state FROM procedure_versions WHERE id=?', (v1,)).fetchone()['state'], 'kept')
        OwnerForget(self.store).forget(OWNER, 'procedure:' + v2)
        current = self.library.current('set-a')
        self.assertEqual((current['id'], current['source']), (v1, SOURCE_V1))

    def test_the_first_save_drops_nothing(self):
        self.library.propose('w1', 'set-a', SOURCE_V1, FUNCTIONS)
        self.assertEqual(self.library.settle('w1', True)[0]['dropped'], [])

    def test_the_same_module_again_only_adds_its_counts(self):
        self.library.propose('w1', 'set-a', SOURCE_V1, FUNCTIONS)
        self.library.settle('w1', True)
        row_id = self.library.propose('w2', 'set-a', SOURCE_V1, FUNCTIONS)
        self.library.record_calls(row_id, {'balance': [2, 1]})
        self.assertEqual(self.library.settle('w2', True), [])
        current = self.library.current('set-a')
        self.assertEqual((current['version'], current['stats']), (1, {'balance': [2, 1]}))

    def test_counts_are_bounded_and_typed(self):
        row_id = self.library.propose('w1', 'set-a', SOURCE_V1, FUNCTIONS)
        self.library.record_calls(row_id, {'balance': [1, 0], 'bad name': [5, 5], 'other': ['x', 1], 'third': [True, 0]})
        self.assertEqual(self.library.for_run('set-a', 'w1')['stats'], {'balance': [1, 0], 'other': [0, 1, 1], 'third': [0, 0]})

    def test_the_listing_names_functions_counts_module_and_ref(self):
        self.assertEqual(self.library.listing('set-a'), '')
        row_id = self.library.propose('w1', 'set-a', SOURCE_V1,
                                      [{'name': 'reserve', 'signature': '(item, count)', 'doc': 'Reserve count of item.'}])
        self.library.record_calls(row_id, {'reserve': [3, 1]})
        self.library.settle('w1', True)
        text = self.library.listing('set-a')
        self.assertIn('saved functions v1 (import lib_set_a; ref procedure:' + row_id, text)
        self.assertIn('reserve(item, count) [3 ok, 1 failed] - Reserve count of item.', text)

    def test_a_function_failing_runs_in_a_row_is_flagged_until_it_works_again(self):
        row_id = self.library.propose('w1', 'set-a', SOURCE_V1, FUNCTIONS)
        self.library.record_calls(row_id, {'balance': [1, 0]})
        self.library.settle('w1', True)
        for work in ('w2', 'w3'):
            row_id = self.library.propose(work, 'set-a', SOURCE_V1, FUNCTIONS)
            self.library.record_calls(row_id, {'balance': [0, 1]})
            self.library.settle(work, True)
        self.assertIn('[failed in the last 2 runs in a row', self.library.listing('set-a'))
        row_id = self.library.propose('w4', 'set-a', SOURCE_V1, FUNCTIONS)
        self.library.record_calls(row_id, {'balance': [1, 0]})
        self.library.settle('w4', True)
        self.assertNotIn('in a row', self.library.listing('set-a'))
        self.assertEqual(self.library.current('set-a')['stats'], {'balance': [2, 2]})

    def test_removing_the_secret_set_removes_its_library(self):
        from personal_agent.api_requests import remove_slot
        self.library.propose('w1', 'set-a', SOURCE_V1, FUNCTIONS)
        self.library.settle('w1', True)
        remove_slot(self.store, 'set-a')
        self.assertIsNone(self.library.current('set-a'))

    def test_module_name_is_an_identifier(self):
        self.assertEqual(module_name('my-api-2'), 'lib_my_api_2')
        self.assertTrue(module_name('my-api-2').isidentifier())


class OwnerUndo(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(self.temp.name)
        self.library = ProcedureLibrary(self.store)
        self.forget = OwnerForget(self.store)
        self.v1 = self.library.propose('w1', 'set-a', SOURCE_V1, FUNCTIONS)
        self.library.settle('w1', True)
        self.v2 = self.library.propose('w2', 'set-a', SOURCE_V2, FUNCTIONS)
        self.library.settle('w2', True)

    def test_forgetting_the_current_version_puts_the_previous_one_back_and_undo_restores_it(self):
        receipt = self.forget.forget(OWNER, 'procedure:' + self.v2)
        self.assertEqual(self.library.current('set-a')['id'], self.v1)
        listed = self.forget.undoable(OWNER, kind='procedure')
        self.assertEqual(listed[0]['item'], {'set': 'set-a', 'version': 2})
        self.forget.undo(OWNER, receipt['receipt'])
        self.assertEqual(self.library.current('set-a')['id'], self.v2)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT state FROM procedure_versions WHERE id=?', (self.v1,)).fetchone()['state'], 'kept')

    def test_a_version_saved_after_the_forget_is_never_replaced_by_undo(self):
        receipt = self.forget.forget(OWNER, 'procedure:' + self.v2)
        self.library.propose('w3', 'set-a', SOURCE_V1 + '\n# fixed\n', FUNCTIONS)
        self.library.settle('w3', True)
        with self.assertRaises(ForgetError) as refused:
            self.forget.undo(OWNER, receipt['receipt'])
        self.assertEqual(refused.exception.code, 'later_value')
        self.assertEqual(self.library.current('set-a')['version'], 3)

    def test_a_candidate_or_unknown_ref_is_refused_and_the_purge_deletes_the_source(self):
        candidate = self.library.propose('w9', 'set-a', SOURCE_V1 + '#x\n', FUNCTIONS)
        for ref in ('procedure:' + candidate, 'procedure:missing'):
            with self.subTest(ref=ref), self.assertRaises(ForgetError):
                self.forget.forget(OWNER, ref)
        self.forget.forget(OWNER, 'procedure:' + self.v1)
        self.forget.purge_due(now=time.time() + 8 * 86400)
        with self.store.db() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM procedure_versions WHERE id=?', (self.v1,)).fetchone())


class OwnerFacing(unittest.TestCase):
    """The owner is told after a save (act-then-tell) and removes it through their AI."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(self.temp.name)
        self.library = ProcedureLibrary(self.store)

    def test_a_reached_work_says_what_it_saved_and_an_unreached_one_says_nothing(self):
        from personal_agent.quickstart_service import AgentService
        service = AgentService(self.store)
        self.library.propose('w1', 'set-a', SOURCE_V1, FUNCTIONS)
        self.assertEqual(service._settle_procedures('w1', '답변', False), '답변')
        self.assertIsNone(self.library.current('set-a'))
        settled = service._settle_procedures('w1', '답변', True)
        self.assertTrue(settled.startswith('답변\n\n'))
        self.assertIn('이번에 된 방법(1가지)을 저장해 뒀어요', settled)
        self.assertNotIn('set-a', settled, 'no internal connection name')
        self.assertEqual(service._settle_procedures('w1', '답변', True), '답변', 'told once')

    def test_the_owner_ai_forgets_and_restores_a_saved_version_with_the_tool(self):
        from personal_agent.agent_runtime import Capabilities
        row_id = self.library.propose('w1', 'set-a', SOURCE_V1, FUNCTIONS)
        self.library.settle('w1', True)
        job = self.store.enqueue('그 방법 지워줘', 'forget-procedure')
        caps = Capabilities(self.store, None, {}, '', job, lambda *event: None)
        result = caps.execute('forget_record', {'action': 'forget', 'ref': 'procedure:' + row_id})
        self.assertEqual(result['kind'], 'procedure')
        self.assertIn('이제 쓰지 않아요', result['message'])
        self.assertIsNone(self.library.current('set-a'))
        caps.execute('forget_record', {'action': 'undo', 'receipt': result['receipt']})
        self.assertEqual(self.library.current('set-a')['id'], row_id)


def _requests(*rows):
    return '\n'.join(f"{method} api.example.com{path}?{query}\n{body}" for method, path, query, body in rows)


class ParameterFlow(unittest.TestCase):
    A, B = {'item': 'A1001', 'count': 2}, {'item': 'B2002', 'count': 5}

    def test_a_parameterized_function_passes(self):
        texts = [_requests(('POST', '/orders', '', '{"item": "A1001", "count": 2}')),
                 _requests(('POST', '/orders', '', '{"item": "B2002", "count": 5}'))]
        self.assertIsNone(flow_problem('reserve', [self.A, self.B], texts))

    def test_a_function_that_sends_the_same_request_is_refused(self):
        same = _requests(('POST', '/orders', '', '{"item": "A1001", "count": 2}'))
        self.assertIn('same requests', flow_problem('reserve', [self.A, self.B], [same, same]))

    def test_a_value_written_into_the_function_is_refused(self):
        # The item is fixed to the first example's value; only the count follows the argument.
        texts = [_requests(('POST', '/orders', '', '{"item": "A1001", "count": 2}')),
                 _requests(('POST', '/orders', '', '{"item": "A1001", "count": 5}'))]
        self.assertIn("'item'", flow_problem('reserve', [self.A, self.B], texts))

    def test_an_argument_that_reaches_no_request_is_refused(self):
        texts = [_requests(('POST', '/orders', '', '{"item": "Z9", "count": 2}')),
                 _requests(('POST', '/orders', '', '{"item": "Z9", "count": 5}'))]
        self.assertIn("argument 'item' did not reach", flow_problem('reserve', [self.A, self.B], texts))

    def test_reads_and_queries_count_and_a_function_without_requests_is_not_checked(self):
        texts = [_requests(('GET', '/quote', 'item=A1001&n=2', '')), _requests(('GET', '/quote', 'item=B2002&n=5', ''))]
        self.assertIsNone(flow_problem('quote', [self.A, self.B], texts))
        self.assertIsNone(flow_problem('format_count', [self.A, self.B], ['', '']))

    def test_a_value_the_function_reformats_still_matches_and_a_fixed_one_still_fails(self):
        # #1489: dashes stripped and case changed by the function; the argument still reaches the request.
        examples = [{'invoice': '6100-2233-4455'}, {'invoice': '6100-9988-7766'}]
        texts = [_requests(('GET', '/api/v3/tracking', 'invoice=610022334455', '')),
                 _requests(('GET', '/api/v3/tracking', 'invoice=610099887766', ''))]
        self.assertIsNone(flow_problem('track', examples, texts))
        self.assertIsNone(flow_problem('find', [{'code': 'ab-12'}, {'code': 'cd-34'}],
                                       [_requests(('GET', '/find', 'code=AB12', '')), _requests(('GET', '/find', 'code=CD34', ''))]))
        fixed = [texts[0], _requests(('GET', '/api/v3/tracking', 'invoice=610022334455&page=2', ''))]
        self.assertIsNotNone(flow_problem('track', examples, fixed))

    def test_examples_must_differ_and_both_run(self):
        self.assertIn('two different', flow_problem('reserve', [self.A, dict(self.A)], ['x', 'x']))
        self.assertIn('only one', flow_problem('reserve', [self.A, self.B], [_requests(('GET', '/', '', '')), '']))


class ScriptedRunner:
    """The runner protocol for one main run and its check sessions, with no process.

    ``main_report`` is what the module's footer would write after the main run; ``build(function,
    arguments)`` is the requests one check call would make.
    """

    def __init__(self, main_report, build, exit_status=0, main_requests=()):
        self.main_report, self.build, self.exit_status, self.main_requests = main_report, build, exit_status, main_requests
        self.sessions = []

    def __call__(self, *args, **kwargs):
        runner = self

        class Process:
            def __init__(self):
                self.config, self.decisions, self.terminated = None, [], False
                self.stdin = self
                self.stdout = self

            def write(self, text):
                message = json.loads(text)
                if self.config is None:
                    self.config = message
                    runner.sessions.append(self)
                else:
                    self.decisions.append(message)

            def flush(self):
                pass

            def __iter__(self):
                while self.config is None:
                    time.sleep(0.001)
                workdir, argv = Path(self.config['workdir']), self.config['argv']
                requests = []
                if argv[-1].startswith('agentos_check_'):
                    script = (workdir / argv[-1]).read_text()
                    function = script.split('getattr(library, ')[1].split(')')[0].strip("'")
                    arguments = json.loads(eval(script.split('json.loads(')[1].split(')')[0]))
                    requests = runner.build(function, arguments)
                    exit_status = 0
                else:
                    self.module_source = {path.name: path.read_text() for path in workdir.glob('lib_*.py')}
                    requests, exit_status = list(runner.main_requests), runner.exit_status
                    if runner.main_report is not None:
                        report = next(workdir.glob('lib_*.py')).stem
                        (workdir / f'.agentos-library-{report}.json').write_text(json.dumps(runner.main_report))
                for index, request in enumerate(requests, 1):
                    yield json.dumps({'type': 'request', 'id': index, 'query': '', 'bodyText': '', **request}) + '\n'
                yield json.dumps({'type': 'result', 'exit': exit_status, 'timedOut': False, 'stdout': 'ok', 'stderr': ''}) + '\n'

            def poll(self):
                return 0 if self.terminated else None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.terminated = True
        return Process()


LIBRARY = ('def reserve(item, count):\n    """Reserve count of item."""\n    pass\n\n'
           'EXAMPLES = {"reserve": [{"item": "A1001", "count": 2}, {"item": "B2002", "count": 5}]}\n')
REPORT = {'functions': [{'name': 'reserve', 'signature': '(item, count)', 'doc': 'Reserve count of item.', 'required': ['item', 'count']}],
          'examples': {'reserve': [{'item': 'A1001', 'count': 2}, {'item': 'B2002', 'count': 5}]},
          'calls': {'reserve': [1, 0]}}


def parameterized(function, arguments):
    return [{'method': 'POST', 'host': 'api.example.com', 'path': '/orders',
             'bodyText': json.dumps(arguments)}]


def fixed(function, arguments):
    return [{'method': 'POST', 'host': 'api.example.com', 'path': '/orders', 'bodyText': '{"item": "A1001", "count": 2}'}]


class CodeRunGate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = QuickStore(self.temp.name)
        patches = [mock.patch.object(CodeRunner, 'secret_sets', lambda self, names, hosts: ([], {}, {'api.example.com': set()})),
                   mock.patch('personal_agent.current_context.redact_known_secrets', lambda store, text: text)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_code(self, scripted, runner=None, **args):
        runner = runner or CodeRunner(self.store, 'w1', popen=scripted, node='/usr/bin/node', install=lambda store, node: self.temp.name)
        return runner.run({'code': 'import lib_set_a\nlib_set_a.reserve("A1001", 2)', 'hosts': 'api.example.com',
                           'secrets': 'set-a', **args})

    def test_a_parameterized_library_is_kept_and_saved_when_the_work_reaches_its_outcome(self):
        scripted = ScriptedRunner(REPORT, parameterized)
        result = self.run_code(scripted, library=LIBRARY)
        self.assertEqual(result['library']['set-a']['state'], 'saved_if_fulfilled')
        # The main run plus one check session per example; every check request was refused, never sent.
        self.assertEqual(len(scripted.sessions), 3)
        self.assertEqual(scripted.sessions[0].config['argv'][-2:], ['-c', code_run.LAUNCHER])
        self.assertTrue(all(not decision['allow'] for session in scripted.sessions[1:] for decision in session.decisions))
        self.assertIn('def _agentos_library_report', scripted.sessions[0].module_source['lib_set_a.py'])
        library = ProcedureLibrary(self.store)
        self.assertIsNone(library.current('set-a'))
        self.assertEqual(library.settle('w1', True)[0]['functions'], ['reserve'])
        self.assertEqual(library.current('set-a')['stats'], {'reserve': [1, 0]})
        self.assertNotIn('source', json.dumps(code_run.evidence(result)))

    def test_an_argument_sent_in_a_header_reaches_the_request(self):
        # #1489 (rehearsal): a token passed as an argument travels in a header, not in path, query or body.
        report = {'functions': [{'name': 'accounts', 'signature': '(token)', 'doc': 'Accounts.', 'required': ['token']}],
                  'examples': {'accounts': [{'token': 'tok-AAAA1111'}, {'token': 'tok-BBBB2222'}]}, 'calls': {'accounts': [1, 0]}}

        def header(function, arguments):
            return [{'method': 'GET', 'host': 'api.example.com', 'path': '/v2/accounts',
                     'headers': [['x-token', arguments['token']], ['user-agent', 'Python-urllib/3.12']]}]
        result = self.run_code(ScriptedRunner(report, header), library='def accounts(token):\n    """Accounts."""\n')
        self.assertEqual(result['library']['set-a']['state'], 'saved_if_fulfilled', result['library'])

    def test_a_fixed_value_is_refused_and_nothing_is_kept(self):
        result = self.run_code(ScriptedRunner(REPORT, fixed), library=LIBRARY)
        self.assertEqual(result['library']['set-a']['state'], 'not_saved')
        self.assertIn('same requests', result['library']['set-a']['reason'])
        self.assertIsNone(ProcedureLibrary(self.store).for_run('set-a', 'w1'))

    def test_what_the_gate_refuses_before_any_check(self):
        cases = {
            'failed run': (dict(REPORT), 1, LIBRARY, 'did not finish successfully'),
            'not imported': (None, 0, LIBRARY, 'did not import lib_set_a'),
            'no call completed': ({**REPORT, 'calls': {'reserve': [0, 1]}}, 0, LIBRARY, 'no library function completed'),
            'no docstring': ({**REPORT, 'functions': [{**REPORT['functions'][0], 'doc': ''}]}, 0, LIBRARY, 'docstring'),
            'no examples': ({**REPORT, 'examples': {}}, 0, LIBRARY, "give EXAMPLES['reserve']"),
        }
        for name, (report, exit_status, source, reason) in cases.items():
            with self.subTest(name):
                scripted = ScriptedRunner(report, parameterized, exit_status=exit_status)
                result = self.run_code(scripted, library=source)
                self.assertEqual(result['library']['set-a']['state'], 'not_saved')
                self.assertIn(reason, result['library']['set-a']['reason'])
                self.assertEqual(len(scripted.sessions), 1, 'no check ran')

    def test_a_stored_secret_in_the_source_is_refused(self):
        with mock.patch('personal_agent.current_context.stored_secret_values', lambda store: ['A1001']):
            result = self.run_code(ScriptedRunner(REPORT, parameterized), library=LIBRARY)
        self.assertIn('stored secret value', result['library']['set-a']['reason'])

    def test_a_key_read_from_the_environment_into_a_header_is_not_a_secret_in_the_source(self):
        # #1489 (rehearsal): credential-shaped code is how a library reads a key; only stored values refuse it.
        source = ('import os\n\ndef reserve(item, count):\n    """Reserve count of item."""\n'
                  '    headers = {"Authorization": f"ApiKey {os.environ[\'SET_A_API_KEY\']}", "X-App-Secret": os.environ["S"]}\n'
                  '    token = os.environ.get("SET_A_TOKEN")\n    return headers, token\n\n'
                  'EXAMPLES = {"reserve": [{"item": "A1001", "count": 2}, {"item": "B2002", "count": 5}]}\n')
        with mock.patch('personal_agent.current_context.stored_secret_values', lambda store: ['real-key-value-123']):
            result = self.run_code(ScriptedRunner(REPORT, parameterized), library=source)
        self.assertEqual(result['library']['set-a']['state'], 'saved_if_fulfilled', result['library'])

    def test_a_saved_library_is_imported_by_the_next_work_and_its_calls_are_counted(self):
        library = ProcedureLibrary(self.store)
        library.propose('w0', 'set-a', LIBRARY, REPORT['functions'])
        library.settle('w0', True)
        scripted = ScriptedRunner({**REPORT, 'calls': {'reserve': [2, 1]}}, parameterized)
        result = self.run_code(scripted)
        self.assertEqual(result['library']['set-a'], {'module': 'lib_set_a', 'state': 'used', 'version': 1})
        self.assertTrue(scripted.sessions[0].module_source['lib_set_a.py'].startswith(LIBRARY))
        self.assertEqual(len(scripted.sessions), 1, 'a used library is not checked again')
        self.assertEqual(library.current('set-a')['stats'], {'reserve': [2, 1]})

    def test_adding_a_function_to_a_saved_library_keeps_both_and_a_left_out_one_is_named(self):
        # #7: save A (reserve), then a later Work adds B (lookup).  The whole module keeps both;
        # a module with only B names reserve as dropped, in the result and in its Evidence.
        library = ProcedureLibrary(self.store)
        library.propose('w0', 'set-a', LIBRARY, REPORT['functions'])
        library.settle('w0', True)
        lookup = {'name': 'lookup', 'signature': '()', 'doc': 'Look up the orders.', 'required': []}
        both = {**REPORT, 'functions': REPORT['functions'] + [lookup], 'calls': {'reserve': [1, 0], 'lookup': [1, 0]}}
        whole = LIBRARY + 'def lookup():\n    """Look up the orders."""\n    pass\n'
        result = self.run_code(ScriptedRunner(both, parameterized), library=whole)
        kept = result['library']['set-a']
        self.assertEqual((kept['state'], kept['functions']), ('saved_if_fulfilled', ['reserve', 'lookup']))
        self.assertNotIn('dropped', kept)
        self.assertEqual(library.settle('w1', True)[0]['dropped'], [])
        # The next Work imports both functions.
        scripted = ScriptedRunner({**both, 'calls': {'lookup': [1, 0]}}, parameterized)
        runner = CodeRunner(self.store, 'w2', popen=scripted, node='/usr/bin/node', install=lambda store, node: self.temp.name)
        self.assertEqual(self.run_code(scripted, runner=runner)['library']['set-a']['state'], 'used')
        self.assertTrue(scripted.sessions[0].module_source['lib_set_a.py'].startswith(whole))
        # A module with only lookup leaves reserve out: named, not silent.
        only = {**REPORT, 'functions': [lookup], 'examples': {}, 'calls': {'lookup': [1, 0]}}
        runner = CodeRunner(self.store, 'w3', popen=ScriptedRunner(only, parameterized), node='/usr/bin/node',
                            install=lambda store, node: self.temp.name)
        result = self.run_code(None, runner=runner, library='def lookup():\n    """Look up the orders."""\n    pass\n')
        kept = result['library']['set-a']
        self.assertEqual(kept['dropped'], ['reserve'])
        self.assertIn('saved library v2', kept['note'])
        self.assertIn('reserve', kept['note'])
        self.assertEqual(code_run.evidence(result)['library']['set-a']['dropped'], ['reserve'])
        self.assertEqual(library.settle('w3', True)[0]['dropped'], ['reserve'])

    def test_a_saved_function_carries_no_approval_and_no_connection_authority(self):
        # #7 acceptance: a saved procedure is a method, never a permission.  Its state change in a
        # later Work is refused (not sent) like any unapproved one, and saving changes no connection setting.
        from personal_agent.api_requests import SLOTS_KEY
        connections = self.store.config(SLOTS_KEY, None)
        library = ProcedureLibrary(self.store)
        library.propose('w0', 'set-a', LIBRARY, REPORT['functions'])
        library.settle('w0', True)
        self.assertEqual(self.store.config(SLOTS_KEY, None), connections)
        post = {'method': 'POST', 'host': 'api.example.com', 'path': '/orders', 'bodyText': '{"item": "A1001", "count": 2}'}
        scripted = ScriptedRunner(REPORT, parameterized, main_requests=[post])
        runner = CodeRunner(self.store, 'w2', popen=scripted, node='/usr/bin/node', install=lambda store, node: self.temp.name)
        result = self.run_code(scripted, runner=runner, effect='mutate')
        self.assertEqual(result['library']['set-a']['state'], 'used')
        self.assertEqual([decision['allow'] for decision in scripted.sessions[0].decisions], [False])
        self.assertEqual([(row['method'], row['allowed']) for row in result['requests']], [('POST', False)])

    def test_a_run_without_a_library_starts_main_py_as_before_and_says_how_to_keep_it(self):
        scripted = ScriptedRunner(None, parameterized)
        result = self.run_code(scripted)
        self.assertNotIn('library', result)
        self.assertEqual(scripted.sessions[0].config['argv'][-1], 'main.py')
        # #1489: a run that worked on a set with no saved functions says how to keep them; a failed one does not.
        self.assertIn('Secret set set-a has no saved functions yet', result['library_hint'])
        self.assertIn('imports lib_set_a', result['library_hint'])
        self.assertNotIn('library_hint', self.run_code(ScriptedRunner(None, parameterized, exit_status=1)))
        # Once per set and Work: the same runner's next run is not told again.
        scripted = ScriptedRunner(None, parameterized)
        runner = CodeRunner(self.store, 'w1', popen=scripted, node='/usr/bin/node', install=lambda store, node: self.temp.name)
        self.assertIn('library_hint', self.run_code(scripted, runner=runner))
        self.assertNotIn('library_hint', self.run_code(scripted, runner=runner))

    def test_no_hint_once_the_set_has_a_library(self):
        library = ProcedureLibrary(self.store)
        library.propose('w0', 'set-a', LIBRARY, REPORT['functions'])
        library.settle('w0', True)
        self.assertNotIn('library_hint', self.run_code(ScriptedRunner(REPORT, parameterized)))

    def test_library_needs_exactly_one_named_set(self):
        with self.assertRaises(code_run.CodeRunError):
            code_run._library_argument({'library': LIBRARY}, [])
        with self.assertRaises(code_run.CodeRunError):
            code_run._library_argument({'library': LIBRARY}, ['set-a', 'set-b'])
        self.assertIsNone(code_run._library_argument({'library': ''}, ['set-a']))


@unittest.skipUnless(os.environ.get('AGENTOS_REAL_SANDBOX_TESTS') == '1', 'starts a real sandbox and proxy')
class RealSandboxLibrary(unittest.TestCase):
    def test_the_module_imports_its_footer_reports_and_the_check_runs(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = QuickStore(temp.name)
        source = ('def double(value):\n    """Twice the value."""\n    return value * 2\n\n'
                  'EXAMPLES = {"double": [{"value": 2}, {"value": 3}]}\n')
        with mock.patch.object(CodeRunner, 'secret_sets', lambda self, names, hosts: ([], {}, {})), \
                mock.patch('personal_agent.current_context.redact_known_secrets', lambda store, text: text):
            result = CodeRunner(store, 'w1').run({'code': 'import lib_set_a\nprint(lib_set_a.double(21))',
                                                 'secrets': 'set-a', 'library': source})
        self.assertIn('42', result['stdout'])
        self.assertEqual(result['library']['set-a']['state'], 'saved_if_fulfilled')
        self.assertEqual(ProcedureLibrary(store).for_run('set-a', 'w1')['stats'], {'double': [1, 0]})

    def test_the_check_records_real_requests_unsent_and_refuses_a_fixed_value(self):
        submit = ('import json, urllib.request\n\n'
                  'def submit(item, count):\n    """Submit count of item."""\n'
                  '    body = json.dumps({"item": ITEM, "count": count}).encode()\n'
                  '    request = urllib.request.Request("https://example.com/orders", data=body, method="POST",\n'
                  '                                     headers={"Content-Type": "application/json"})\n'
                  '    try:\n        urllib.request.urlopen(request, timeout=20)\n    except Exception:\n        return False\n'
                  '    return True\n\n'
                  'EXAMPLES = {"submit": [{"item": "A1001", "count": 2}, {"item": "B2002", "count": 5}]}\n')
        # #1489: the item travels in a header here, which the check reads as the request sends it.
        submit = submit.replace('headers={"Content-Type": "application/json"}',
                                'headers={"Content-Type": "application/json", "X-Item": ITEM}')
        for name, item, state in (('parameterized', 'item', 'saved_if_fulfilled'), ('fixed', '"A1001"', 'not_saved')):
            with self.subTest(name):
                temp = tempfile.TemporaryDirectory()
                self.addCleanup(temp.cleanup)
                store = QuickStore(temp.name)
                with mock.patch.object(CodeRunner, 'secret_sets', lambda self, names, hosts: ([], {}, {'example.com': set()})), \
                        mock.patch('personal_agent.current_context.redact_known_secrets', lambda store, text: text):
                    result = CodeRunner(store, 'w1').run({'code': 'import lib_set_a\nprint(lib_set_a.submit("A1001", 2))',
                                                         'hosts': 'example.com', 'secrets': 'set-a',
                                                         'library': submit.replace('ITEM', item)})
                self.assertEqual(result['library']['set-a']['state'], state, result['library'])
                # The main run's POST was refused as an unapproved state change; nothing was sent.
                self.assertEqual([(row['method'], row['allowed']) for row in result['requests']], [('POST', False)])

    def test_an_argument_only_in_a_header_reaches_the_check(self):
        # #1489: the runner reports headers, so a value sent only in a header is seen by the check.
        source = ('import urllib.request\n\n'
                  'def accounts(token):\n    """Accounts for one token."""\n'
                  '    request = urllib.request.Request("https://example.com/accounts", headers={"X-Token": token})\n'
                  '    try:\n        urllib.request.urlopen(request, timeout=20)\n    except Exception:\n        return False\n'
                  '    return True\n\n'
                  'EXAMPLES = {"accounts": [{"token": "tok-AAAA1111"}, {"token": "tok-BBBB2222"}]}\n')
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = QuickStore(temp.name)
        # A GET is a read and goes through in the check, as in any run (example.com answers it).
        with mock.patch.object(CodeRunner, 'secret_sets', lambda self, names, hosts: ([], {}, {'example.com': set()})), \
                mock.patch('personal_agent.current_context.redact_known_secrets', lambda store, text: text):
            result = CodeRunner(store, 'w1').run({'code': 'import lib_set_a\nprint(lib_set_a.accounts("tok-CCCC3333"))',
                                                 'hosts': 'example.com', 'secrets': 'set-a', 'library': source})
        self.assertEqual(result['library']['set-a']['state'], 'saved_if_fulfilled', result['library'])


if __name__ == '__main__':
    unittest.main()
