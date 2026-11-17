"""#932: a marked Work's approval names its outside sources readably, not as a raw JSON dump.

The #744 confirmation still names every source group; only its rendering changes.
"""
import json

from personal_agent.self_set_writes import SOURCE_URLS_SHOWN, marked_lines
from test_api_requests import tool_call
from test_744_effects import READ, WRITE, after_read, host, injected_api, worker  # noqa: F401 (fixture)

READS = 55


def test_many_reads_keep_the_folded_card_and_name_every_source_group(host):
    store, service, job = host
    from personal_agent.api_requests import save_slot
    save_slot(store, 'notes', ['notes.test'], 'fixture-secret-notes-123456789', money=False)
    # Earlier reads of this Work, recorded by the host like the live mail cleanup's 55.
    for n in range(READS - 1):
        store.mark_outside_content(job['id'], {'category': 'owner-api', 'operation': 'api_request',
                                               'slot': 'notes', 'url': f"{READ['url']}/{n}"})
    sent = []
    worker(store, service, job, [{'tool_calls': [tool_call('read', 'api_request', **READ)]}, after_read],
           wire=lambda *args: injected_api(*args, sent=sent))
    assert [r[0] for r in sent] == ['GET'], 'the marked write must stay held'
    label = service._browser_request(job['id'])['label']
    assert '{"sources"' not in label and '"category":' not in label
    assert WRITE['url'] in label and 'api_request' in label and 'notes' in label
    assert f'({READS})' in label
    assert f"{READ['url']}/0" in label and f"{READ['url']}/{SOURCE_URLS_SHOWN}" not in label
    folded = service.browser_step_prompt_html(job['id'])
    assert folded and len(folded) <= 3800, 'a long source list must not push the card out of its folded form'


def test_groups_count_and_bound_their_urls():
    sources = ([{'category': 'owner-api', 'operation': 'api_request', 'slot': 'mail', 'url': f'https://m.test/{n}'}
                for n in range(10)]
               + [{'category': 'owner-mail', 'operation': 'mail_search'}])
    text = marked_lines(sources, {}, '')
    assert 'owner-api / api_request / mail (10)' in text
    assert 'owner-mail / mail_search' in text and 'mail_search (' not in text
    assert 'https://m.test/0' in text and f'https://m.test/{SOURCE_URLS_SHOWN}' not in text
    lines = text.splitlines()
    more = lines[lines.index('- owner-mail / mail_search') - 1]
    assert str(10 - SOURCE_URLS_SHOWN) in more


def test_parameters_already_in_the_label_are_not_repeated():
    body = json.dumps({'ids': ['a', 'b']})
    label = f'slot:mail / POST / https://m.test/batch\n{body}'
    text = marked_lines([{'category': 'owner-api', 'operation': 'api_request'}],
                        {'url': 'https://m.test/batch', 'body': body, 'extra': {'k': 1}}, label)
    assert 'url:' not in text and 'body:' not in text
    assert 'extra: {"k": 1}' in text
