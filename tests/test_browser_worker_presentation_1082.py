"""#1082 lane A: the workers' page-presentation language and time zone, fixed at launch.

Evidence class: model-free unit tests of the worker protocol (fake Playwright/CDP for Chromium, argument
handling for WebKit).  The real engines are covered by ``WebKitIntegrationTests`` in test_browser_webkit.py.
"""
import io
import json
from unittest import mock

import pytest

from personal_agent import browser_worker as shared
from personal_agent import browser_worker_chromium as chromium

from test_browser_worker_chromium import FakePlaywright, command, packets  # noqa: F401 (fixtures below)


@pytest.fixture(autouse=True)
def no_native_display():
    with mock.patch.object(chromium, 'relinquish_keyboard_focus'):
        yield


def make_worker(locale=None, timezone=None, engine=None):
    engine = engine or FakePlaywright()
    messages = []
    result = chromium.Worker('fixture', messages.append, engine, '/pinned/chrome', ('fixture.test:80',),
                             locale=locale, timezone=timezone)
    result.messages, result.engine = messages, engine
    return result


def land(worker, url='https://public.test/'):
    worker.page.url = url
    worker.frame_navigated(worker.main_target, {'id': worker.main_target.main_frame, 'loaderId': 'loader', 'url': url})
    worker.loaded(worker.main_target, {'frameId': worker.main_target.main_frame, 'loaderId': 'loader'})
    return worker.messages[-1]


# -- arguments -----------------------------------------------------------------------------------------------

def test_chromium_arguments_accept_locale_and_timezone_and_default_to_none():
    args = chromium.parse_args(['--chromium', '/bin/chrome', '--locale', 'zh-Hans-CN', '--timezone', 'Asia/Shanghai'])
    assert (args.locale, args.timezone) == ('zh-Hans-CN', 'Asia/Shanghai')
    args = chromium.parse_args(['--chromium', '/bin/chrome'])
    assert (args.locale, args.timezone) == (None, None)


@pytest.mark.parametrize('flag,value', [
    ('--locale', ''), ('--locale', 'ja JP'), ('--locale', 'ja_JP'), ('--locale', '-ja'), ('--locale', 'ja\nJP'),
    ('--locale', 'x' * 200), ('--timezone', ''), ('--timezone', 'Asia/Tokyo; rm'), ('--timezone', '../etc'),
    ('--timezone', 'a' * 200),
])
@pytest.mark.parametrize('module,base', [
    (chromium, ['--chromium', '/pinned/chrome']),
    (shared, []),
])
def test_malformed_presentation_input_is_refused_before_any_engine_starts(module, base, flag, value):
    output = io.StringIO()
    factory = mock.Mock(side_effect=AssertionError('no engine may start'))
    with mock.patch.object(module.sys, 'stdout', output), \
         mock.patch.object(chromium, '_playwright_factory', factory):
        assert module.main(base + [flag, value]) == 2
    assert json.loads(output.getvalue()) == {'event': 'unavailable', 'error': 'bad_presentation'}
    factory.assert_not_called()


def test_webkit_arguments_accept_locale_and_timezone_and_still_reject_unknown_arguments():
    output = io.StringIO()
    with mock.patch.object(shared.sys, 'platform', 'linux'), mock.patch.object(shared.sys, 'stdout', output):
        assert shared.main(['--profile', 'p', '--locale', 'ja-JP', '--timezone', 'Asia/Tokyo']) == 2
    assert json.loads(output.getvalue()) == {'event': 'unavailable', 'error': 'unsupported_platform'}
    with pytest.raises(SystemExit):
        shared.main(['--language', 'ja-JP'])


# -- Chromium: Playwright context options ---------------------------------------------------------------------

def test_omitted_settings_keep_todays_context_options_exactly():
    worker = make_worker()
    options = worker.engine.browser.context_options
    assert 'locale' not in options and 'timezone_id' not in options
    assert worker.presentation() == {'locale': None, 'timezone': None, 'unsupported': []}
    for params in packets(worker, 'Emulation.setUserAgentOverride'):
        assert 'acceptLanguage' not in params


def test_requested_settings_go_to_the_playwright_context_not_hand_written_overrides():
    worker = make_worker('ja-JP', 'Asia/Tokyo')
    options = worker.engine.browser.context_options
    assert (options['locale'], options['timezone_id']) == ('ja-JP', 'Asia/Tokyo')
    assert not packets(worker, 'Emulation.setLocaleOverride')
    assert not packets(worker, 'Emulation.setTimezoneOverride')
    assert worker.presentation() == {'locale': 'ja-JP', 'timezone': 'Asia/Tokyo', 'unsupported': []}


def test_only_the_timezone_or_only_the_locale_sets_just_that_option():
    options = make_worker(timezone='America/New_York').engine.browser.context_options
    assert options['timezone_id'] == 'America/New_York' and 'locale' not in options
    options = make_worker(locale='en-US').engine.browser.context_options
    assert options['locale'] == 'en-US' and 'timezone_id' not in options


def test_every_user_agent_override_keeps_the_requested_accept_language():
    """``Emulation.setUserAgentOverride`` replaces the whole override, Playwright's acceptLanguage included."""
    worker = make_worker('ja-JP', 'Asia/Tokyo')
    command(worker, 'navigate', url='https://public.test/', layout='desktop')   # a layout change re-emulates
    land(worker)
    overrides = packets(worker, 'Emulation.setUserAgentOverride')
    assert overrides, 'the layout change re-applied the user agent'
    assert all(params.get('acceptLanguage') == 'ja-JP' for params in overrides), overrides


def test_a_zone_chromium_refuses_fails_the_launch_visibly():
    engine = FakePlaywright()
    engine.browser.new_context = mock.Mock(side_effect=RuntimeError('Invalid timezone ID: Mars/Base'))
    output = io.StringIO()
    factory = mock.MagicMock()
    factory.return_value.__enter__.return_value = engine
    with mock.patch.object(chromium.sys, 'platform', 'linux'), \
         mock.patch.object(chromium.sys, 'stdout', output), \
         mock.patch.dict(chromium.os.environ, {'DISPLAY': ':0'}, clear=True), \
         mock.patch.object(chromium, '_playwright_factory', return_value=factory):
        assert chromium.main(['--chromium', '/pinned/chrome', '--timezone', 'Mars/Base']) == 2
    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    assert lines == [{'event': 'unavailable', 'error': 'chromium_failed'}], 'no silent retry without the zone'
    assert engine.browser.new_context.call_count == 1


@pytest.mark.parametrize('locale,timezone,expected', [
    (None, None, {'locale': None, 'timezone': None, 'unsupported': []}),
    ('ko-KR', 'Asia/Seoul', {'locale': 'ko-KR', 'timezone': 'Asia/Seoul', 'unsupported': []}),
])
def test_chromium_ready_event_reports_what_the_context_was_given(locale, timezone, expected):
    engine = FakePlaywright()
    output = io.StringIO()
    factory = mock.MagicMock()
    factory.return_value.__enter__.return_value = engine
    argv = ['--chromium', '/pinned/chrome'] + (['--locale', locale] if locale else []) + (
        ['--timezone', timezone] if timezone else [])
    with mock.patch.object(chromium.sys, 'platform', 'linux'), \
         mock.patch.object(chromium.sys, 'stdout', output), \
         mock.patch.object(chromium.sys, 'stdin', io.StringIO('')), \
         mock.patch.dict(chromium.os.environ, {'DISPLAY': ':0'}, clear=True), \
         mock.patch.object(chromium, '_playwright_factory', return_value=factory), \
         mock.patch.object(chromium.Worker, 'close'):
        chromium.main(argv)
    ready = [json.loads(line) for line in output.getvalue().splitlines() if '"ready"' in line]
    assert len(ready) == 1, output.getvalue()
    assert ready[0]['presentation'] == expected


# -- navigate replies carry what the page observed ---------------------------------------------------------

OBSERVED = {'navigator_language': 'ja-JP', 'intl_locale': 'ja-JP', 'intl_timezone': 'Asia/Tokyo'}


def test_navigate_reply_carries_the_values_read_in_the_isolated_world():
    worker = make_worker('ja-JP', 'Asia/Tokyo')
    command(worker, 'navigate', url='https://public.test/')
    worker.engine.browser.script_results.append(dict(OBSERVED))
    reply = land(worker)
    assert reply['ok'] is True and reply['url'] == 'https://public.test/'
    assert reply['presentation_observed'] == OBSERVED
    calls = [p for p in packets(worker, 'Runtime.callFunctionOn') if 'navigator.language' in p['functionDeclaration']]
    assert calls and all(p['executionContextId'] == 101 for p in calls), 'read in Still’s isolated world'


def test_observed_values_are_what_the_page_reported_never_the_requested_ones():
    worker = make_worker('ja-JP', 'Asia/Tokyo')
    command(worker, 'navigate', url='https://public.test/')
    worker.engine.browser.script_results.append(
        {'navigator_language': 'en-US', 'intl_locale': 'en-US', 'intl_timezone': 'UTC'})
    assert land(worker)['presentation_observed'] == {
        'navigator_language': 'en-US', 'intl_locale': 'en-US', 'intl_timezone': 'UTC'}


@pytest.mark.parametrize('result', [{}, {'navigator_language': 7, 'intl_locale': None, 'intl_timezone': ['x']}])
def test_unreadable_observations_are_null(result):
    worker = make_worker('ja-JP')
    command(worker, 'navigate', url='https://public.test/')
    worker.engine.browser.script_results.append(result)
    assert land(worker)['presentation_observed'] == {
        'navigator_language': None, 'intl_locale': None, 'intl_timezone': None}


def test_a_failed_observation_script_still_answers_the_navigation_with_nulls(monkeypatch):
    worker = make_worker('ja-JP')
    command(worker, 'navigate', url='https://public.test/')
    original = worker.run

    def failing(body, arguments, done, **kwargs):
        if 'navigator.language' in body:
            return done(None, 'script_failed')
        return original(body, arguments, done, **kwargs)
    monkeypatch.setattr(worker, 'run', failing)
    reply = land(worker)
    assert reply['ok'] is True and reply['layout'] == 'mobile'
    assert reply['presentation_observed'] == {'navigator_language': None, 'intl_locale': None, 'intl_timezone': None}


def test_same_document_navigation_reply_also_carries_observations():
    worker = make_worker('ja-JP')
    worker.engine.browser.navigate_result = {'frameId': 'f'}   # no loaderId: only the fragment changed
    worker.engine.browser.script_results.append(dict(OBSERVED))
    reply = command(worker, 'navigate', url='https://public.test/#part')
    assert reply['ok'] is True and reply['url'] == 'https://public.test/#part'
    assert reply['presentation_observed'] == OBSERVED


def test_default_launch_still_reports_observations():
    worker = make_worker()
    command(worker, 'navigate', url='https://public.test/')
    worker.engine.browser.script_results.append(
        {'navigator_language': 'en-US', 'intl_locale': 'en-US', 'intl_timezone': 'Asia/Seoul'})
    assert land(worker)['presentation_observed']['intl_timezone'] == 'Asia/Seoul'


def test_observation_script_overrides_nothing_in_the_page():
    """Never fake a value: the read is pure (no assignment to navigator/Intl/Date)."""
    script = shared.PRESENTATION_SCRIPT
    for forbidden in ('defineProperty', 'Intl.DateTimeFormat =', 'Date =', 'navigator.language =', '__proto__'):
        assert forbidden not in script
    assert 'navigator.language' in script and 'resolvedOptions' in script
