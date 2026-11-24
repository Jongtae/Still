"""#1192: a press's form method reaches the press-effect judgment.

Live 2026-11-24: in a Work that read outside content, a nameless search button in an
exchange's GET stock-search form asked the owner for approval, because the judgment saw
only ``button`` and an empty name.  Still's own snapshot knows the form's method; HTTP
defines GET as safe (RFC 9110 §9.2.1), so the judgment now receives it as evidence.
Model-free: a real temporary store, the fixture ``FakeDriver`` and an injected judgment.
"""
from unittest import mock

import pytest

from personal_agent.agent_runtime import Capabilities, ToolError
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

from test_browser_session import FakeDriver
from test_press_effect_1063 import _judge

ORIGIN = 'https://exchange.test'
PAGES = {
    '/lookup': ('<title>Stock search</title><p>Search a stock by its code.</p>'
                '<form action="/lookup" method="get"><input aria-label="Stock code"><button type="button"></button></form>'
                '<form action="/comment" method="post"><input name="comment" aria-label="Comment"><button type="submit">Post</button></form>'),
}


@pytest.fixture
def work(tmp_path):
    store = QuickStore(tmp_path / 'state')
    service = AgentService(store)
    job = store.job(store.enqueue('Is 403870 traded on the alternative exchange?', 'owner-1192', owner_typed=True))
    driver = FakeDriver(pages=PAGES, origin=ORIGIN)
    caps = Capabilities(store, None, {}, '', job['id'], lambda *args: None, browser=lambda: driver,
                        browser_approvals=service.browser_approvals_for(job))
    page = caps.execute('browser_open', {'url': ORIGIN + '/lookup', 'effect': 'read'})
    assert store.outside_content_sources(job['id'])
    return service, job, driver, caps, page


def test_nameless_button_in_a_get_form_is_judged_with_the_method(work):
    service, job, driver, caps, page = work
    button = next(row['n'] for row in page['elements'] if row['role'] == 'button' and not row['name'])
    with mock.patch.object(service.decision_judge, 'connector_effect', return_value='read') as judge:
        caps.execute('browser_click', {'target': str(button), 'effect': 'read'})
    assert judge.call_args.kwargs['form_method'] == 'get'
    assert judge.call_args.kwargs['target'][:2] == ('button', '')
    assert [row for row in driver.log if row[0] == 'click'] and service._browser_request(job['id']) is None


def test_post_form_press_carries_post_and_still_waits_when_not_judged_read(work):
    service, job, driver, caps, page = work
    post = next(row['n'] for row in page['elements'] if row['name'] == 'Post')
    with mock.patch.object(service.decision_judge, 'connector_effect', return_value=None) as judge:
        with pytest.raises(ToolError) as refusal:
            caps.execute('browser_click', {'target': str(post), 'effect': 'read'})
    assert judge.call_args.kwargs['form_method'] == 'post'
    assert refusal.value.code == 'approval_required' and driver.posts == []


def test_press_question_carries_the_form_method(tmp_path):
    judge = _judge(tmp_path)
    judge.connector_effect('browser_click @ https://exchange.test', [], '', target=('button', ''), form_method='GET')
    (context, _, _), = judge.engine.asked
    assert context.facts['form_method'] == 'get' and context.facts['form_fields'] == 'none named'
    judge.engine.asked.clear()
    judge.connector_effect('browser_click @ https://exchange.test', [], '', target=('button', 'Menu'))
    (context, _, _), = judge.engine.asked
    assert context.facts['form_method'] == 'none (not in a form or unknown)'
    assert context.facts['form_fields'] == 'none (not in a form)'
