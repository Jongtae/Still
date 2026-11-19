"""Model-free evidence for Work-local download access, transfer and handoff."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest

from personal_agent import api_requests, work_download, work_mode
from personal_agent.agent_runtime import Capabilities, ToolError, WorkBudget, WorkLedger, evidence_summary
from personal_agent.bounded_execution import ExecutionError
from personal_agent.browser_jar import MemoryKey
from personal_agent.code_run import CodeRunError, CodeRunner
from personal_agent.local_tools import LocalTools, PublicPageReader
from personal_agent.quickstart import make_handler
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

from test_code_run import FakeProcess
from test_code_run_secrets import FakeApprovals
from test_orchestrator import Harness, NO_TOOL_CALLS, plan
from test_public_download_1009 import Opener, Response


URL = 'https://example.org/data'
TAG = '"fixture-v1"'


def network(clock, opener):
    return LocalTools(page_reader=PublicPageReader(
        clock=clock, opener=opener, resolver=lambda host, port, **kwargs: [
            (None, None, None, None, ('93.184.216.34', port)),
        ],
    ))


def active_work(store, key, net, clock, *, seconds=120):
    work_id = store.enqueue('Read the public data.', key, owner_typed=True)
    with store.db() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (work_id,))
    budget = WorkBudget(clock=clock, seconds=seconds, ledger=WorkLedger(store, work_id, fresh=True))
    return Capabilities(store, None, {}, '', work_id, lambda *args: None, network=net, budget=budget)


def fetch(caps, offset=0):
    args = {'operation': 'fetch', 'offset': offset}
    if not offset:
        args['url'] = URL
    return caps.execute('public_download', args)


def get(service, path, cookie=''):
    """Exercise the real HTTP handler and authentication without binding a socket."""
    handler_type = make_handler(service)
    handler = handler_type.__new__(handler_type)
    handler.server = SimpleNamespace(server_address=('127.0.0.1', 7744), server_port=7744)
    handler.client_address = ('127.0.0.1', 12345)
    handler.request_version = 'HTTP/1.1'
    handler.headers = {'Host': 'localhost:7744', 'Cookie': cookie}
    handler.path, handler.requestline = path, f'GET {path} HTTP/1.1'
    handler.wfile = io.BytesIO()
    handler.log_message = lambda *_: None
    handler.do_GET()
    return handler.wfile.getvalue().split(b'\r\n\r\n', 1)


def test_completed_download_reaches_code_and_authenticated_owner_only(tmp_path):
    data = b'\xff\x00\x03\nexternal sample secret: orchid river silver cloud\n'
    secret = 'orchid river silver cloud'
    store = QuickStore(tmp_path)
    store.secret('model_key', secret)
    clock = lambda: 0
    caps = active_work(store, 'consumer', network(clock, Opener([Response(data, status=200, headers=[])])), clock)
    receipt = fetch(caps)
    staged = store.private / 'work-downloads' / receipt['artifact']
    with staged.open('ab') as stream:
        stream.write(b'uncommitted tail')

    class DataProcess(FakeProcess):
        def _lines(self):
            path = Path(self.config['env']['STILL_DOWNLOAD_FILE'])
            assert str(store.private.resolve()) in self.config['denyRead']
            assert self.config['allowRead'][-1] == str(path)
            assert self.config['denyWrite'] == [str(path.parent)]
            assert not path.is_relative_to(self.config['workdir'])
            assert path.stat().st_mode & 0o777 == 0o400
            done = subprocess.run(self.config['argv'], cwd=self.config['workdir'], env=self.config['env'],
                                  capture_output=True, text=True, check=True)
            self._result = {'stdout': done.stdout}
            yield from super()._lines()

    process = DataProcess()
    runner = caps.code_runner()
    runner.popen = lambda *args, **kwargs: process
    runner._node = '/fixture/node'
    runner.install = lambda *_: tmp_path
    args = {'code': 'from pathlib import Path\nimport os, hashlib\n'
                    'print(hashlib.sha256(Path(os.environ["STILL_DOWNLOAD_FILE"]).read_bytes()).hexdigest())',
            'download_artifact': receipt['artifact']}
    result = caps.execute('code_run', args)
    masked = data.replace(secret.encode(), b'*' * len(secret))
    assert result['stdout'].strip() == hashlib.sha256(masked).hexdigest()
    evidence = evidence_summary('code_run', result)
    assert evidence['download_artifact'] == receipt['artifact']
    assert 'STILL_DOWNLOAD_FILE' not in json.dumps(evidence)
    assert not Path(process.config['env']['STILL_DOWNLOAD_FILE']).exists()
    assert staged.read_bytes() == data + b'uncommitted tail'

    other = active_work(store, 'unrelated', caps.network, clock)
    with pytest.raises(ToolError, match='unavailable in this Work'):
        other.execute('code_run', args)
    with pytest.raises(ToolError, match='unavailable in this Work'):
        caps.execute('code_run', {**args, 'download_artifact': str(staged)})

    service = AgentService(store)
    task = next(item for item in service.task_progress(caps.job_id)['tasks'] if item['id'] == caps.job_id)
    artifact, = task['artifacts']
    assert artifact['id'] == receipt['artifact']
    headers, _ = get(service, artifact['download_url'])
    assert b' 401 ' in headers.splitlines()[0]
    cookie = f'agentos_session={store.local_session()}'
    headers, body = get(service, artifact['download_url'], cookie)
    assert b' 200 ' in headers.splitlines()[0]
    assert b'Content-Disposition: attachment;' in headers
    assert body == data
    headers, _ = get(service, artifact['download_url'].replace(caps.job_id, other.job_id), cookie)
    assert b' 404 ' in headers.splitlines()[0]
    staged.write_bytes(b'?' * len(data))
    headers, _ = get(service, artifact['download_url'], cookie)
    assert b' 404 ' in headers.splitlines()[0]


def test_approved_rerun_reuses_the_verified_download(tmp_path):
    data = b'fixture data'
    digest = hashlib.sha256(data).hexdigest()
    clock = lambda: 0
    store = QuickStore(tmp_path)
    caps = active_work(store, 'approval', network(clock, Opener([Response(data, status=200, headers=[])])), clock)
    artifact = fetch(caps)['artifact']
    host = 'api.example.org'
    store.api_slot_key = MemoryKey()
    api_requests.save_slot(store, 'fixture', [host], {'KEY': 'fixture-api-key'}, money=True, encrypted=True,
                           code={'fields': [{'name': 'KEY', 'label': 'Key', 'use': 'header_or_body'}], 'reads': []})
    approvals = FakeApprovals()

    class DataProcess(FakeProcess):
        def _lines(self):
            done = subprocess.run(self.config['argv'], cwd=self.config['workdir'], env=self.config['env'],
                                  capture_output=True, text=True)
            if done.returncode == 0:
                self._requests = [{'method': 'POST', 'host': host, 'path': '/fixture', 'bodyText': done.stdout.strip()}]
            self._result = {'stdout': done.stdout, 'stderr': done.stderr, 'exit': done.returncode}
            yield from super()._lines()

    def runner(process):
        return CodeRunner(store, caps.job_id, node='/fixture/node', popen=lambda *args, **kwargs: process,
                          install=lambda *_: tmp_path, approvals=approvals, download_budget=caps.budget)

    code = ('from pathlib import Path\nimport os, hashlib\n'
            'data = Path(os.environ["STILL_DOWNLOAD_FILE"]).read_bytes()\n'
            'print(hashlib.sha256(data).hexdigest())')
    first = DataProcess()
    result = runner(first).run({'code': code, 'download_artifact': artifact, 'secrets': 'fixture',
                                'approval_note': 'Submit the digest.'})
    assert result['stdout'].strip() == digest
    assert [row['allow'] for row in first.decisions] == [False]
    [(binding, _)] = approvals.requested
    approvals.granted.append(binding)

    resumed = DataProcess()
    result = runner(resumed).run({'rerun': 'approved'})
    assert result['stdout'].strip() == digest
    assert result['download_artifact'] == artifact
    assert [row['allow'] for row in resumed.decisions] == [True]

    (store.private / 'work-downloads' / artifact).write_bytes(b'?' * len(data))
    corrupt = DataProcess()
    with pytest.raises(CodeRunError) as refused:
        runner(corrupt).run({'rerun': 'approved'})
    assert refused.value.code == 'read_checkpoint_corrupt'
    assert corrupt.config is None


def test_one_call_commits_each_range_and_stops_at_work_byte_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(work_download, 'RANGE_BYTES', 4)
    monkeypatch.setattr(work_download, 'WORK_BYTES', 12)
    store = QuickStore(tmp_path)
    coverage = []

    class InspectingOpener(Opener):
        def open(self, request, timeout):
            checkpoint = store.read_checkpoint(caps.job_id)
            coverage.append((checkpoint['revision'], checkpoint['received']) if checkpoint else (0, 0))
            return super().open(request, timeout)

    opener = InspectingOpener([
        Response(body, headers=[('Content-Range', f'bytes {start}-{start + 3}/16'), ('ETag', TAG)])
        for start, body in zip((0, 4, 8), (b'aaaa', b'bbbb', b'cccc'))
    ])
    clock = lambda: 0
    caps = active_work(store, 'loop', network(clock, opener), clock)
    receipt = fetch(caps)
    assert (receipt['revision'], receipt['received'], receipt['total']) == (3, 12, 16)
    assert receipt['stop_code'] == 'read_checkpoint_limit'
    assert coverage == [(0, 0), (1, 4), (2, 8)]
    assert [headers.get('If-range') for _, _, headers in opener.calls] == [None, TAG, TAG]
    assert caps.budget.ledger.counts()['attempts'] == 1
    assert caps.budget.ledger.counts()['turns'] == 0


@pytest.mark.parametrize('tag', (None, 'W/"weak"'))
@pytest.mark.parametrize('range_reply', (False, True))
def test_bounded_plain_download_with_weak_or_missing_validator(tmp_path, monkeypatch, tag, range_reply):
    monkeypatch.setattr(work_download, 'RANGE_BYTES', 4)
    payload = b'abcdef'
    headers = [('ETag', tag)] if tag else []
    plain = Response(payload, status=200, headers=headers)
    first = Response(b'abcd', headers=[('Content-Range', 'bytes 0-3/6'), *headers])
    opener = Opener([first, plain] if range_reply else [plain])
    store = QuickStore(tmp_path)
    clock = lambda: 0
    caps = active_work(store, 'plain', network(clock, opener), clock)
    result = fetch(caps)
    checkpoint = store.read_checkpoint(caps.job_id)
    assert result['received'] == result['total'] == len(payload)
    assert 'etag' not in checkpoint
    assert checkpoint['revision'] == 1
    assert work_download.read_artifact(store, caps.job_id, result['artifact']) == payload
    assert [headers.get('Range') for _, _, headers in opener.calls] == (
        ['bytes=0-3', None] if range_reply else ['bytes=0-3']
    )
    assert all('If-range' not in headers for _, _, headers in opener.calls)


@pytest.mark.parametrize(('headers', 'body', 'code'), [
    ([], b'abcde', 'public_download_size_limit'),
    ([('Content-Length', '5')], b'abcde', 'public_download_size_limit'),
    ([('Content-Length', '4')], b'abc', 'public_download_interrupted'),
])
def test_oversized_or_interrupted_plain_download_publishes_no_checkpoint(tmp_path, monkeypatch, headers, body, code):
    monkeypatch.setattr(work_download, 'WORK_BYTES', 4)
    store = QuickStore(tmp_path)
    clock = lambda: 0
    opener = Opener([Response(body, status=200, headers=headers)])
    caps = active_work(store, 'incomplete-plain', network(clock, opener), clock)
    with pytest.raises(ToolError) as failed:
        fetch(caps)
    assert failed.value.code == code
    assert store.read_checkpoint(caps.job_id) is None


def test_work_deadline_bounds_body_even_when_range_timeout_is_longer(tmp_path, monkeypatch):
    monkeypatch.setattr(work_download, 'RANGE_BYTES', 4)
    now = [0]
    clock = lambda: now[0]

    class SlowResponse(Response):
        def read(self, size):
            now[0] += 4
            return super().read(size)

    opener = Opener([Response(headers=[('Content-Range', 'bytes 0-3/6'), ('ETag', TAG)]), SlowResponse(b'ef', headers=[
        ('Content-Range', 'bytes 4-5/6'), ('ETag', TAG)])])
    store = QuickStore(tmp_path)
    caps = active_work(store, 'deadline', network(clock, opener), clock, seconds=3)
    with pytest.raises(ToolError) as stopped:
        fetch(caps)
    assert stopped.value.code == 'deadline_exceeded'
    assert len(opener.calls) == 2
    assert store.read_checkpoint(caps.job_id)['received'] == 4


def test_work_mode_allows_checkpoint_claim_and_local_preview(tmp_path):
    opener = Opener([Response(b'abc\n', status=200, headers=[])])
    store = QuickStore(tmp_path)
    clock = lambda: 0
    caps = active_work(store, 'mode', network(clock, opener), clock)
    # A complete representation gives the new worker a useful local read.
    receipt = fetch(caps)
    service = AgentService(store)
    service.set_work_mode({'enabled': True})
    service.recover_interrupted_work()
    claim = service._claim_queued_work()
    assert claim['id'] == caps.job_id
    replacement = Capabilities(store, None, {}, '', claim['id'], lambda *args: None, network=caps.network,
                               budget=service.work_budget(claim['id']))
    assert replacement.execute('public_download', {'operation': 'preview', 'offset': 0})['artifact'] == receipt['artifact']
    with pytest.raises(work_mode.WorkModeRefused):
        fetch(replacement, receipt['received'])
    assert len(opener.calls) == 1


def test_old_relay_leaves_the_last_committed_range_for_its_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(work_download, 'RANGE_BYTES', 4)
    first = [('Content-Range', 'bytes 0-3/6'), ('ETag', TAG)]
    last = [('Content-Range', 'bytes 4-5/6'), ('ETag', TAG)]
    closed = False

    class ClosingResponse(Response):
        def read(self, size):
            nonlocal closed
            closed = True
            return super().read(size)

    opener = Opener([Response(headers=first), ClosingResponse(b'ef', headers=last), Response(b'ef', headers=last)])
    store = QuickStore(tmp_path)
    clock = lambda: 0
    caps = active_work(store, 'closed-relay', network(clock, opener), clock)
    caps.download_worker_closed = lambda: closed
    with pytest.raises(ToolError) as interrupted:
        fetch(caps)
    assert interrupted.value.code == 'public_download_interrupted'
    first_checkpoint = store.read_checkpoint(caps.job_id)
    assert first_checkpoint['received'] == 4

    replacement = Capabilities(store, None, {}, '', caps.job_id, lambda *args: None,
                               network=caps.network, budget=caps.budget)
    receipt = fetch(replacement, 4)
    assert receipt['artifact'] == first_checkpoint['artifact']
    assert receipt['received'] == receipt['total'] == 6
    assert [headers.get('If-range') for _, _, headers in opener.calls] == [None, TAG, TAG]


class TestLiveDownloadReplacement(Harness):
    def test_stopped_cli_is_replaced_on_another_route_using_the_same_staging(self):
        self.script([plan('codex', 'Begin the read.'), plan('openai', 'Continue the read.')], goals=[True])
        now = [0]

        class TemporarilyInterrupted(Opener):
            def open(self, request, timeout):
                if len(self.calls) == 1:
                    self.calls.append((request.full_url, request.get_method(), dict(request.header_items())))
                    raise OSError('fixture connection ended')
                return super().open(request, timeout)

        opener = TemporarilyInterrupted([Response(headers=[('Content-Range', 'bytes 0-3/6'), ('ETag', TAG)]),
                                         Response(b'ef', headers=[('Content-Range', 'bytes 4-5/6'), ('ETag', TAG)])])
        self.service.local_tools = network(lambda: now[0], opener)
        self.engine.fail = [ExecutionError('Worker stopped', failure_class='stopped',
                                           meta={**NO_TOOL_CALLS, 'stream_tail': ['turn.failed']})]
        first = []

        def start(tools):
            receipt = tools.call('public_download', {'operation': 'fetch', 'url': URL, 'offset': 0})
            assert receipt['stop_code'] == 'public_download_interrupted'
            first.append((tools.capabilities.job_id, receipt['artifact'], tools.capabilities.budget.ledger.epoch))

        self.engine.before = start
        request = 'Read the public data and report its size.'
        job = self.store.enqueue(request, '1016-replace', owner_typed=True, owner_words=True)
        worker_turns = []

        def replace(_config, _key, messages, _tools, **options):
            worker_turns.append(messages)
            if len(worker_turns) == 1:
                self.assertIn('4 of 6 bytes verified', messages[0]['content'])
                self.assertEqual(messages[-1]['content'], request)
                return {'role': 'assistant', 'content': '', 'tool_calls': [
                    {'id': 'resume', 'type': 'function', 'function': {'name': 'public_download',
                     'arguments': '{"operation":"fetch","offset":4}'}}]}, 'fixture-model'
            return {'role': 'assistant', 'content': 'The file contains six bytes.'}, 'fixture-model'

        # Keep the production range validator, using short ranges to fit the fixture.
        with mock.patch.object(work_download, 'RANGE_BYTES', 4), \
             mock.patch.object(self.service.adapter, 'tool_turn', side_effect=replace):
            self.assertTrue(self.service.run_one())
        self.assertTrue(worker_turns)
        checkpoint = self.store.read_checkpoint(job)
        self.assertEqual(first, [(job, checkpoint['artifact'], checkpoint['epoch'])])
        self.assertEqual(work_download.read_artifact(self.store, job, checkpoint['artifact']), b'abcdef')
        self.assertEqual([headers.get('Range') for _, _, headers in opener.calls],
                         ['bytes=0-3', 'bytes=4-7', 'bytes=4-7'])
        self.assertEqual([headers.get('If-range') for _, _, headers in opener.calls], [None, TAG, TAG])
        self.assertEqual(self.store.job(job)['status'], 'succeeded')
