"""Suite-wide guards."""
import functools
import threading
import unittest

import pytest

#: #142: the service's own background seams, each starting one daemon thread per call, by thread name.
SERVICE_SPAWNS = {
    'owner_model_spawn': 'agentos-owner-model',
    'steer_spawn': 'agentos-steer-judgment',
    'progress_reaction_spawn': 'agentos-telegram-progress-reaction',
    'turn_reaction_spawn': 'agentos-telegram-turn-reaction',
    'early_judgment_spawn': 'agentos-early-judgment',
}
SERVICE_THREAD_JOIN_SECONDS = 30


@pytest.fixture(autouse=True)
def _no_automatic_phone_login(monkeypatch):
    """#953: a login prompt never starts a real ngrok tunnel in a test; a test that wants it opts in with a fake."""
    from personal_agent import quickstart_service
    monkeypatch.setattr(quickstart_service.AgentService, 'AUTO_PHONE_LOGIN', False)
    monkeypatch.setattr(quickstart_service.AgentService, 'SESSION_KEEPALIVE', False)


@pytest.fixture(autouse=True)
def _service_threads_end_before_the_test_cleans_up(request, monkeypatch):
    """#142: a service's background judgment ends before its test removes the data folder.

    The turn reaction (#969) and the other seams above run off the worker on
    purpose, so a Work can finish while one still reads or writes the store.
    Under a loaded parallel run such a thread was still writing when the
    test's temporary directory was removed ("Directory not empty").  Each
    seam keeps its real thread; the test joins those threads in a cleanup
    registered when the service is built, which runs before the cleanups
    registered earlier (the data folder's).  A test that sets its own seam
    keeps it.
    """
    case = request.instance
    if not isinstance(case, unittest.TestCase):
        return
    from personal_agent.quickstart_service import AgentService
    build = AgentService.__init__

    @functools.wraps(build)
    def tracked(service, *args, **kwargs):
        build(service, *args, **kwargs)
        started = []

        def seam(name):
            def spawn(target):
                thread = threading.Thread(target=target, name=name, daemon=True)
                started.append(thread)
                thread.start()
            return spawn

        for attribute, name in SERVICE_SPAWNS.items():
            setattr(service, attribute, seam(name))
        case.addCleanup(_join_service_threads, case, started)

    monkeypatch.setattr(AgentService, '__init__', tracked)


def _join_service_threads(case, started):
    for thread in started:
        thread.join(SERVICE_THREAD_JOIN_SECONDS)
    case.assertEqual([thread.name for thread in started if thread.is_alive()], [],
                     'a service background thread outlived its test')
