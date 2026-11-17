"""#953 (owner choice 2026-11-17): an approval card always starts with the AI's plain words.

A state change that asks the owner is refused before any request is recorded when the call
carries no ``approval_note``; with one, the note leads the card and the exact request follows.
"""
from personal_agent.api_requests import save_slot
from personal_agent.code_run import APPROVAL_EXACT_LABEL
from personal_agent.quickstart_service import request_prompt_parts
from test_api_requests import tool_call
from test_744_effects import READ, WRITE, after_read, host, injected_api, worker  # noqa: F401 (fixture)


def marked_write(store, service, job, write):
    save_slot(store, 'notes', ['notes.test'], 'fixture-secret-notes-123456789', money=False)
    sent = []
    worker(store, service, job, [{'tool_calls': [tool_call('read', 'api_request', **READ)]},
                                 lambda body: {'tool_calls': [tool_call('write', 'api_request', **write)]}],
           wire=lambda *args: injected_api(*args, sent=sent))
    return sent


def test_a_marked_write_without_a_note_asks_nothing(host):
    store, service, job = host
    bare = {key: value for key, value in WRITE.items() if key != 'approval_note'}
    sent = marked_write(store, service, job, bare)
    assert [row[0] for row in sent] == ['GET']
    assert service._browser_request(job['id']) is None, 'no approval card without the AI words'
    with store.db() as db:
        details = [row['detail'] for row in db.execute(
            "SELECT detail FROM tool_events WHERE job_id=? AND tool='api_request' AND status='failed'", (job['id'],))]
    assert any('approval_note_required' in detail for detail in details)


def test_a_marked_write_with_a_note_leads_the_card_with_it(host):
    store, service, job = host
    sent = marked_write(store, service, job, WRITE)
    assert [row[0] for row in sent] == ['GET']
    label = service._browser_request(job['id'])['label']
    _title, summary, _rest, _check, exact = request_prompt_parts(label)
    assert summary == WRITE['approval_note']
    assert WRITE['url'] in exact and WRITE['body'] in exact
    assert label.index(WRITE['approval_note']) < label.index(APPROVAL_EXACT_LABEL)
