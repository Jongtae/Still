"""#1324: an empty optional ``download_artifact`` means no download, not a missing one.

Sandbox replays (2026-11-28) showed ``code_run`` refused with ``read_checkpoint_missing`` in Works
that never downloaded anything; tool callers commonly send optional string fields as "".
"""
import pytest

from personal_agent.agent_runtime import ToolError
from personal_agent.quickstart_store import QuickStore
from test_code_run import FakeProcess
from test_work_download_1016 import Opener, Response, active_work, network


def _runner(caps, tmp_path):
    process = FakeProcess()
    runner = caps.code_runner()
    runner.popen = lambda *args, **kwargs: process
    runner._node = '/fixture/node'
    runner.install = lambda *_: tmp_path
    return process


@pytest.mark.parametrize('blank', ['', '   '])
def test_a_blank_download_artifact_runs_the_code_without_a_download(tmp_path, blank):
    store = QuickStore(tmp_path)
    clock = lambda: 0
    caps = active_work(store, 'blank', network(clock, Opener([Response(b'', status=200, headers=[])])), clock)
    process = _runner(caps, tmp_path)
    result = caps.execute('code_run', {'code': 'print(1)', 'download_artifact': blank})
    assert result['exit_status'] == 0
    assert 'STILL_DOWNLOAD_FILE' not in process.config['env']


def test_a_named_artifact_that_is_not_this_works_download_is_still_refused(tmp_path):
    store = QuickStore(tmp_path)
    clock = lambda: 0
    caps = active_work(store, 'named', network(clock, Opener([Response(b'', status=200, headers=[])])), clock)
    _runner(caps, tmp_path)
    with pytest.raises(ToolError, match='unavailable in this Work'):
        caps.execute('code_run', {'code': 'print(1)', 'download_artifact': 'not-a-receipt'})
