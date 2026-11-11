"""#791: model-free progress/kill/relay boundary, using real subprocess pipes."""
import sys
from unittest import mock

import pytest

from personal_agent import agent_runtime as ar
from personal_agent import browser_session as bs
from personal_agent import browser_worker as bw
from personal_agent import browser_worker_chromium as chromium
from personal_agent import cli_browser_relay as relay
from test_browser_session import FakeDriver, ORIGIN, session


STREAM_WORKER = r'''
import json, sys, time
mode = sys.argv[1]
def emit(value):
    print(json.dumps(value), flush=True)
emit({'event': 'ready'})
for line in sys.stdin:
    command = json.loads(line)
    ident = command['id']
    if mode != 'missing':
        emit({'event': 'step', 'id': ident + 1, 'posted': True})
        emit({'event': 'step', 'id': ident, 'posted': False})
    if mode in ('posted', 'pending', 'monotonic'):
        emit({'event': 'step', 'id': ident, 'posted': True})
    if mode == 'monotonic':
        emit({'event': 'step', 'id': ident, 'posted': False})
    if mode == 'eof':
        sys.stdout.close()
        import os
        os.close(1)
    time.sleep(60)
'''


@pytest.mark.parametrize('mode,posted', [('quiet', False), ('posted', True), ('pending', True),
                                        ('missing', None), ('monotonic', True), ('eof', None)])
def test_killed_step_survives_session_record_and_relay(mode, posted):
    driver = bs.WebKitWorkerDriver(command=[sys.executable, '-u', '-c', STREAM_WORKER, mode], grace_seconds=0)
    browser, fake = session(FakeDriver())
    browser.open({'url': ORIGIN + '/product', 'effect': 'read'})
    fake.click = lambda *args, **kwargs: driver._request('click', 1)
    try:
        with pytest.raises(ar.ToolError) as caught:
            browser.click({'target': '내 계정', 'effect': 'navigate'})
        exc = caught.value
        # EOF is a worker failure, never evidence of an effect-free timeout.
        assert exc.code == ('browser_failed' if mode == 'eof' else 'browser_timeout')
        seen = None if posted is None else {'posted': posted, 'approved': False}
        assert getattr(exc, 'observed_step', None) == seen
        restored = relay.relay_exception(relay._error_payload(exc))
        assert getattr(restored, 'observed_step', None) == seen
        if mode != 'eof':
            detail = ar._error_observation(restored, True, 'browser_click')
            assert detail['code'] == 'browser_timeout'
        for declaration in ('read', 'navigate', 'mutate', 'payment'):
            detail = {**ar._error_observation(restored, True, 'browser_click'), 'declared_effect': declaration}
            classification = ar.record_effect('browser_click', detail, 'failed')
            assert (classification == ar.EFFECT_NONE) == (posted is False and declaration in ('read', 'navigate'))
        assert driver._observations['steps'] == {}
        assert driver._observations['active'] == set()
    finally:
        driver.close()


@pytest.mark.parametrize('worker_class', [bw.Worker, chromium.Worker])
def test_step_begin_precedes_page_script(worker_class):
    worker = object.__new__(worker_class)
    worker.posted_submits = worker.refused_submits = worker.deciding = 0
    events = []
    worker.emit = events.append
    worker.run = lambda *args, **kwargs: events.append('page script')
    command = {'index': 0, 'expect': {}, 'text': 'query'}
    worker._locate(42, command, lambda *args: None)
    assert events[0] == {'event': 'step', 'id': 42, 'posted': False}
    assert events[1:] == ['page script']


def test_cleanup_keeps_reporting_until_reply():
    worker = object.__new__(bw.Worker)
    worker._step_ident = 42
    worker.dialog_step = {'dialogs': []}
    worker.pending = {42: True}
    worker.posted_submits = worker.step_posted = worker.deciding = 0
    events = []
    worker.emit = events.append
    worker.run = lambda *args: None
    worker.end_step()
    worker.posted_submits += 1
    worker.report_step()
    assert events == [{'event': 'step', 'id': 42, 'posted': True}]
    worker.reply(42)
    worker.report_step()
    assert len(events) == 2


def test_progress_write_failure_is_not_swallowed():
    stream = mock.Mock()
    stream.flush.side_effect = OSError('pipe failed')
    emit = bw.make_emit(stream)
    with pytest.raises(OSError):
        emit({'event': 'step', 'id': 42, 'posted': True})
    emit({'event': 'hidden'})   # existing best-effort diagnostics stay best effort


def test_kill_drains_a_true_event_the_reader_has_not_processed(monkeypatch):
    import threading
    release = threading.Event()
    entered = threading.Event()
    loads = bs.json.loads

    def delayed(line):
        message = loads(line)
        if message.get('event') == 'step' and message.get('posted') is True and message.get('id') == 1:
            entered.set()
            assert release.wait(5)
        return message

    monkeypatch.setattr(bs.json, 'loads', delayed)
    driver = bs.WebKitWorkerDriver(command=[sys.executable, '-u', '-c', STREAM_WORKER, 'posted'], grace_seconds=0)
    kill = driver._kill

    def drain():
        assert entered.is_set()
        release.set()
        return kill()

    monkeypatch.setattr(driver, '_kill', drain)
    try:
        with pytest.raises(TimeoutError) as caught:
            driver._request('click', 1)
        assert caught.value.posted is True
    finally:
        release.set()
        driver.close()


@pytest.mark.parametrize('reader_alive,drained', [(False, True), (True, False)])
def test_kill_ignores_observation_when_reader_failed_or_drain_is_unconfirmed(reader_alive, drained):
    import io
    import itertools
    import threading
    from types import SimpleNamespace
    driver = object.__new__(bs.WebKitWorkerDriver)
    driver._ids = itertools.count(1)
    driver._proc = SimpleNamespace(stdin=io.StringIO())
    driver._queue = None
    driver.grace_seconds = 0
    driver._observation_lock = threading.Lock()
    driver._observations = {'active': set(), 'steps': {1: False}, 'alive': reader_alive, 'drained': drained}
    driver._kill = lambda: reader_alive
    with mock.patch.object(bs.time, 'monotonic', side_effect=[0, 2]):
        with pytest.raises(TimeoutError) as caught:
            driver._send('click', 1)
    assert not hasattr(caught.value, 'posted')
    assert not driver._observations['steps']

