"""#1328: an optional tool argument sent as JSON null counts as not given.

Replays showed ``code_run`` refused as "download unavailable" with no download in the Work, and
``entrusted_line`` refused for a ``rule_scope`` on a non-revise action: engines fill unset optional
fields with null and tools test presence.  The entry drops null once for every tool.
"""
from personal_agent.quickstart_store import QuickStore
from test_code_run import FakeProcess
from test_work_download_1016 import Opener, Response, active_work, network


def _caps(tmp_path):
    store = QuickStore(tmp_path)
    clock = lambda: 0
    caps = active_work(store, 'nulls', network(clock, Opener([Response(b'', status=200, headers=[])])), clock)
    process = FakeProcess()
    runner = caps.code_runner()
    runner.popen = lambda *args, **kwargs: process
    runner._node = '/fixture/node'
    runner.install = lambda *_: tmp_path
    return caps, process


def test_null_optional_fields_do_not_name_a_download(tmp_path):
    caps, process = _caps(tmp_path)
    result = caps.execute('code_run', {'code': 'print(1)', 'download_artifact': None, 'library': None,
                                       'rerun': None, 'secrets': None, 'hosts': None, 'packages': None})
    assert result['exit_status'] == 0
    assert 'STILL_DOWNLOAD_FILE' not in process.config['env']


def test_the_entry_hands_tools_no_null_values(tmp_path, monkeypatch):
    caps, _process = _caps(tmp_path)
    seen = []
    original = caps.code_runner

    def spy():
        runner = original()
        real = runner.run
        def run(args, **kwargs):
            seen.append(dict(args))
            return real(args, **kwargs)
        runner.run = run
        return runner
    monkeypatch.setattr(caps, 'code_runner', spy)
    caps.execute('code_run', {'code': 'print(1)', 'download_artifact': None})
    assert seen and all(value is not None for value in seen[-1].values())
    assert 'download_artifact' not in seen[-1]
