"""#867: real slot admission/storage/Settings/delivery with fixture AI transports, no network."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from personal_agent import preparations as prep
from personal_agent.quickstart_store import QuickStore
from personal_agent.messages import msg
from personal_agent.settings_orchestrator import SettingsError
from personal_agent.subscription_engines import SubscriptionEngines
from test_preparations import _WatchCase, watch_engine, SEOUL
from test_cloud_engines import _Engine, BEDROCK_TOKEN


@pytest.fixture
def case():
    case = _WatchCase('runTest')
    case.setUp()
    case.now = datetime(2026, 11, 16, 23, 0, tzinfo=SEOUL).timestamp()
    case.judge = watch_engine([False] * 30)
    case.service.use_decision_engine(case.judge)
    case.watched = case.watch(span=86400)
    case.service.preparations.set_budget(2)
    yield case
    case.doCleanups()


def configure(case, route):
    if route == 'direct_api':
        return lambda: len(case.model_calls)
    engine = _Engine()
    case.service.execution_adapter = engine
    case.service.subscription_engines = SubscriptionEngines(finder=lambda name: '/fixture/' + name, clock=lambda: case.now)
    case.service.connect_subscription_engine({'engine': 'claude-code', 'officially_authenticated': True})
    if route == 'cloud_cli':
        case.service.cloud_engines.clock = lambda: case.now
        case.service.save_cloud_profile({'engine': 'claude-code', 'provider': 'bedrock',
                                        'settings': {'AWS_REGION': 'us-east-1'}, 'daily_calls': 100})
        case.service.save_cloud_secret({'engine': 'claude-code', 'provider': 'bedrock',
                                       'name': 'AWS_BEARER_TOKEN_BEDROCK', 'value': BEDROCK_TOKEN})
    case.route_engine = engine
    return lambda: len(engine.launches)


def usage(case):
    day = case.service.preparations.budget_day(case.now)
    return case.store.config(prep.SCHEDULED_USAGE_PREFIX + day, 0)


def skipped(case):
    with case.store.db() as db:
        return [json.loads(row[0]) for row in db.execute('SELECT detail FROM tool_events WHERE tool=? AND status=?',
                                                        (prep.SCHEDULED_BUDGET_KEY, 'skipped'))]


def skip_slot(case):
    row = case.service.preparations.get(case.watched['id'])
    case.now = row['due_at'] + 1
    assert case.tick()
    assert not case.service.run_one()
    case.service.deliver_notification()
    return row


@pytest.mark.parametrize('route', ['cloud_cli', 'subscription_cli', 'direct_api'])
def test_all_routes_stop_notify_once_and_owner_requests_run(case, route):
    launches = configure(case, route)
    case.run_slot('checked')
    case.run_slot('checked')
    before = launches()
    assert before > 0
    runs = case.runs(case.watched['id'])
    assert len(runs) == 2 and all(work['status'] == 'succeeded' for work in runs)
    if route == 'direct_api':
        # The fixture judgment does not attest model identity in the Work row;
        # the actual direct model transport must receive the configured model.
        assert case.model_calls and all(body['model'] == 'test-model' for body in case.model_calls)
    else:
        assert len(case.route_engine.launches) == 2
        assert all(engine == 'claude-code' for engine, _, _ in case.route_engine.launches)
        profiles = [profile for _, profile, _ in case.route_engine.launches]
        if route == 'cloud_cli':
            assert all(profile.provider == 'bedrock' and profile.daily_calls > 2 for profile in profiles)
            assert case.service.cloud_engines.used_today('claude-code', 'bedrock') == 2
        else:
            assert profiles == [None, None]
            assert case.service.cloud_engines.provider('claude-code') == 'subscription'
    first = skip_slot(case)
    skip_slot(case)
    assert launches() == before
    assert len(case.runs(case.watched['id'])) == 2, 'refusal happens before Work creation or routing'
    if route == 'cloud_cli':
        assert case.service.cloud_engines.used_today('claude-code', 'bedrock') == 2
    records = skipped(case)
    assert len(records) == 2
    assert records[0]['slot'] == prep.iso(first['due_at'])
    assert all(r['reason'] == prep.SCHEDULED_BUDGET_REASON and r['preparation_id'] == first['id'] and r['ran'] is False for r in records)
    assert usage(case) == 2
    assert case.service.preparations.get(first['id'])['state'] == prep.STATE_SCHEDULED
    with case.store.db() as db:
        notices = list(db.execute('SELECT state FROM telegram_notifications WHERE kind=?', (prep.SCHEDULED_BUDGET_NOTICE,)))
        texts = [r[0] for r in db.execute('SELECT content FROM messages WHERE job_id=?',
                                        (prep.SCHEDULED_USAGE_PREFIX + records[0]['day'],))]
    assert len(notices) == len(texts) == 1
    assert notices[0]['state'] == 'sent'
    assert sum(texts[0] in body.get('text', '') for method, body in case.telegram if method == 'sendMessage') == 1
    # Real owner ingress over budget must reach the engine, without a budget charge.
    work_id = case.receive('Please answer this now')
    assert case.store.job(work_id)['status'] == 'succeeded'
    assert launches() > before
    assert usage(case) == 2
    case.service.preparation_request({'operation': 'run_now', 'id': first['id']})
    before = launches()
    assert case.service.run_one()
    assert launches() > before
    assert usage(case) == 2


@pytest.mark.parametrize('cap', [3, 0])
def test_settings_raise_or_disable_at_next_slot(case, cap):
    case.run_slot('checked')
    case.run_slot('checked')
    skip_slot(case)
    # Both conversation Settings and the Settings page use the same stored control.
    settings = case.service.settings_orchestrator
    draft = settings.draft('owner', 'http', {'category': 'scheduled_work', 'setting': 'daily_calls', 'value': cap})
    settings.confirm('owner', 'http', draft['draft_id'], draft['digest'])
    assert case.service.preparations_status()['budget']['daily_calls'] == cap
    case.run_slot('checked after change')
    assert usage(case) == 3
    assert len(skipped(case)) == 1


def test_restart_readmission_and_next_owner_local_day(case):
    row = case.watched
    case.now = row['due_at']
    first = case.service.preparations.start(row, channel='web', chat_id=None, now=case.now)
    assert first and usage(case) == 1
    case.store = QuickStore(case.root)
    case.service = case.make_service()
    assert case.service.preparations.start(row, channel='web', chat_id=None, now=case.now) is None
    assert usage(case) == 1
    # Even if a recoverer presents a scheduled row again, the existing request key is charged once.
    with case.store.db() as db:
        db.execute('UPDATE preparations SET state=? WHERE id=?', (prep.STATE_SCHEDULED, row['id']))
    assert case.service.preparations.start(row, channel='web', chat_id=None, now=case.now) == first
    assert usage(case) == 1
    assert case.service.run_one()
    case.tick()
    case.service.preparations.set_budget(1)
    skip_slot(case)
    old_day = case.service.preparations.budget_day(case.now)
    # Midnight Seoul is 15:00 UTC: the budget must reset even on the same UTC date.
    case.now = datetime(2026, 11, 17, 0, 1, tzinfo=SEOUL).timestamp()
    assert case.service.preparations.budget_day(case.now) != old_day
    assert usage(case) == 0
    assert case.tick()
    assert case.service.run_one()
    assert usage(case) == 1
    case.tick()
    skip_slot(case)
    with case.store.db() as db:
        assert db.execute('SELECT count(*) FROM telegram_notifications WHERE kind=?', (prep.SCHEDULED_BUDGET_NOTICE,)).fetchone()[0] == 2


def test_shared_counter_serializes_rows_and_queue_failure_does_not_charge(case):
    case.service.preparations.set_budget(1)
    other = case.service.preparations.create(kind='prepare', goal='another', due_at=case.watched['due_at'],
        timezone='UTC', recurrence=None, channel='web', created_from='settings', state=prep.STATE_SCHEDULED,
        accepted_by=prep.ACCEPTED_OWNER_SETTINGS)
    case.now = case.watched['due_at']
    with patch.object(case.store, 'enqueue', side_effect=ValueError('queue full')):
        assert case.service.preparations.start(other, channel='web', chat_id=None, now=case.now) is None
    assert usage(case) == 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda row: case.service.preparations.start(row, channel='web', chat_id=None, now=case.now),
                                (other, case.watched)))
    assert sum(result is not None for result in results) == 1
    assert usage(case) == len(skipped(case)) == 1


def test_default_validation_and_owner_settings_exposure(case):
    with case.store.db() as db:
        db.execute('DELETE FROM config WHERE key=?', (prep.SCHEDULED_BUDGET_KEY,))
    assert case.service.preparations.budget_settings() == {'daily_calls': prep.DEFAULT_SCHEDULED_DAILY_CALLS}
    settings = case.service.settings_orchestrator
    assert settings.read('owner', 'scheduled_work')['settings']['scheduled_work']['daily_calls']['value'] == str(prep.DEFAULT_SCHEDULED_DAILY_CALLS)
    for value in (True, -1, 1.5, 'off', prep.MAX_SCHEDULED_DAILY_CALLS + 1, None):
        with pytest.raises(ValueError):
            case.service.preparation_request({'operation': 'set_budget', 'daily_calls': value})
        with pytest.raises(SettingsError):
            settings.draft('owner', 'http', {'category': 'scheduled_work', 'setting': 'daily_calls', 'value': value})
    result = case.service.preparation_request({'operation': 'set_budget', 'daily_calls': 0})
    assert result['budget']['daily_calls'] == 0


@pytest.mark.parametrize('first_run', ['watch', 'line', 'preparation'])
def test_line_and_preparation_share_budget_but_reminder_does_not(case, first_run):
    service = case.service
    service.preparations.set_budget(1)
    line = service.lines.open('settings', task='Keep this responsibility', report_promise='Report checks', rules=[])
    due = case.now + 60
    def create(kind, goal, **extra):
        return service.preparations.create(kind=kind, goal=goal, due_at=due, timezone='Asia/Seoul',
            recurrence='daily', channel='telegram', created_from='settings', state=prep.STATE_SCHEDULED,
            accepted_by=prep.ACCEPTED_OWNER_SETTINGS, **extra)
    reminder = create('reminder', 'Reminder')
    row = service.preparations.create(kind='prepare', goal='One-shot preparation', due_at=due,
        timezone='Asia/Seoul', recurrence=None, channel='web', created_from='settings',
        state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS)
    entrusted = create('prepare', 'Line run', line_id=line['line_id'])
    case.now = due
    assert service.preparations.start(reminder, channel='web', chat_id=None, now=due)
    assert usage(case) == 0
    rows = {'watch': case.watched, 'line': entrusted, 'preparation': row}
    assert service.preparations.start(rows[first_run], channel='web', chat_id=None, now=due)
    refused = [item for name, item in rows.items() if name != first_run]
    for item in refused:
        assert not service.preparations.start(item, channel='web', chat_id=None, now=due)
    assert usage(case) == 1
    assert {item['preparation_id'] for item in skipped(case)} == {item['id'] for item in refused}
    for item in refused:
        current = service.preparations.get(item['id'])
        if item['recurrence'] is not None:
            assert current['state'] == prep.STATE_SCHEDULED and current['due_at'] > due
        else:
            assert current['state'] == prep.STATE_EXPIRED
    # A restart cannot turn a consumed refusal into a new slot or a second notice.
    case.store = QuickStore(case.root)
    case.service = case.make_service()
    assert not case.service.preparations.start(refused[0], channel='web', chat_id=None, now=due)
    assert len(skipped(case)) == 2
    assert usage(case) == 1


def test_worker_settings_schema_and_unattended_cannot_raise_own_budget(case):
    from personal_agent.agent_runtime import DEFINITIONS
    definitions = {item['function']['name']: item['function'] for item in DEFINITIONS}
    for name in ('settings_read', 'settings_change'):
        assert 'scheduled_work' in definitions[name]['parameters']['properties']['category']['enum']
    case.now = case.watched['due_at']
    job = case.service.preparations.start(case.watched, channel='web', chat_id=None, now=case.now)
    result = case.service.settings_tools(case.store.job(job))('settings_change',
        {'category': 'scheduled_work', 'setting': 'daily_calls', 'value': '0'})
    assert not result.get('applied')
    assert case.service.preparations.budget_settings()['daily_calls'] == 2


def test_local_settings_keeps_the_daily_notice_visible(case):
    case.service.preparations.set_budget(1)
    case.now = case.watched['due_at']
    assert case.service.preparations.start(case.watched, channel='web', chat_id=None, now=case.now)
    local = case.service.preparations.create(kind='prepare', goal='Local check', due_at=case.now,
        timezone='Asia/Seoul', recurrence=None, channel='web', created_from='settings',
        state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS)
    assert case.service.preparations.start(local, channel='web', chat_id=None, now=case.now) is None
    assert skipped(case)[0]['slot_at'] == local['due_at']
    notice = case.service.preparations_status()['budget']['notice']
    assert set(notice) == {'ko', 'en', 'ja', 'zh'}
    assert 'Local check' in notice['en'] and 'skipped' in notice['en']
    assert 'Local check' in notice['ko'] and notice['en'] != notice['ko']


def test_budget_settings_form_preserves_edits_and_submits_numeric_limit():
    import shutil
    import subprocess
    from test_settings_ui import DOM_CHECKS, ROOT
    if not shutil.which('node'):
        pytest.skip('Node is unavailable')
    # Reuse the Settings UI's DOM fixture, running the actual budget form function.
    program = DOM_CHECKS.split('const part=')[0] + r'''
const host=new Element('div');host.id='scheduled-budget';host._root=true;
const calls=[],labels={budget_label:'Daily budget',budget_help:'0 disables',save:'Save',saved:'Saved'};
const ctx={document,$,scheduledBudget:{daily_calls:6,max:14400,notice:{en:'Skipped today'}},currentLanguage:'en',
 preparationLabel:key=>labels[key],element:(tag,text,css)=>{const e=new Element(tag);if(text)e.textContent=text;if(css)e.className=css;return e;},
 busy:async(button,fn)=>fn(),setFeedback:()=>{},setError:(_id,error)=>{throw error;},
 api:async(path,body)=>{calls.push({path,body});return {budget:{daily_calls:body.daily_calls,max:14400}};}};
vm.createContext(ctx);
vm.runInContext(app.slice(app.indexOf('function renderScheduledBudget()'),app.indexOf('function preparationDescription(')),ctx);
(async()=>{
 ctx.renderScheduledBudget();const input=$('scheduled-budget-input'),form=host.querySelector('form');
 assert.equal(input.value,'6');assert.equal(input.max,'14400');assert.equal($('scheduled-budget-notice').textContent,'Skipped today');
 input.value='7';input.oninput();ctx.scheduledBudget.daily_calls=30;ctx.renderScheduledBudget();
 assert.equal(input.value,'7');assert.equal(host.querySelector('form'),form);
 await form.onsubmit({preventDefault(){}});
 assert.equal(calls[0].path,'/api/preparations/request');assert.equal(calls[0].body.operation,'set_budget');assert.equal(calls[0].body.daily_calls,7);
 input.value='0';input.oninput();await form.onsubmit({preventDefault(){}});assert.equal(calls[1].body.daily_calls,0);
 ctx.currentLanguage='zh-CN';ctx.scheduledBudget.notice={zh:'已跳过'};ctx.renderScheduledBudget();assert.equal($('scheduled-budget-notice').textContent,'已跳过');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(['node', '-e', program, str(ROOT / 'app.js')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def unchecked_fact(text):
    marker = 'unchecked_scheduled_slots: '
    if marker not in text:
        return None
    return json.JSONDecoder().raw_decode(text.split(marker, 1)[1])[0]


@pytest.mark.parametrize('report', ['promised', 'closing', 'ordinary'])
def test_next_report_carries_typed_unchecked_slots_once_after_restart(case, report):
    if report == 'closing':
        case.service.preparations.edit(case.watched['id'], max_runs=5)
    elif report == 'promised':
        with case.store.db() as db:
            terms = prep.stored_window_terms(case.watched)
            terms['report_seconds'] = 1800
            db.execute('UPDATE preparations SET window_terms=? WHERE id=?',
                       (json.dumps(terms), case.watched['id']))
    case.run_slot('Initial check')
    case.run_slot('Initial check')
    first, last = skip_slot(case), skip_slot(case)
    # Facts survive process restart and a different row cannot consume them.
    case.store = QuickStore(case.root)
    case.service = case.make_service()
    assert case.service.preparations.budget_skipped_fact({'id': 'owner', 'request_key': 'typed'}) is None
    case.judge = watch_engine([True, True])
    case.service.use_decision_engine(case.judge)
    case.service.preparations.set_budget(0)
    expected = {'count': 2, 'first_slot_at': first['due_at'], 'last_slot_at': last['due_at'],
                'first_slot': prep.local_text(first['due_at'], first['timezone']),
                'last_slot': prep.local_text(last['due_at'], last['timezone']),
                'reason': prep.SCHEDULED_BUDGET_REASON}
    observed = []

    def respond(body):
        # Observe the actual model transport input, rather than calling the fact builder in the fixture.
        text = '\n'.join(str(item.get('content', '')) for item in body['messages'])
        observed.append(unchecked_fact(text))
        assert observed[-1] == expected
        return {'content': 'Fixture AI report about the unchecked interval.'}

    row = case.service.preparations.get(case.watched['id'])
    case.now = row['due_at'] + 1
    assert case.tick()
    case.script = [respond, respond]
    assert case.service.run_one()
    work_id = case.service.preparations.get(row['id'])['last_run_job_id']
    # Repeated fact reads must stay stable, including on the judgment path.
    assert unchecked_fact(case.service.preparation_run_fact(case.store.job(work_id))) == expected
    assert case.tick()
    case.service.deliver_notification()
    assert observed
    settled = case.service.preparations.get(row['id'])
    reason = {'promised': prep.REASON_PROMISED_REPORT, 'closing': prep.REASON_WINDOW_CLOSED,
              'ordinary': prep.REASON_JUDGED_NEEDED}[report]
    assert settled['last_decision_reason'] == reason
    assert any('Fixture AI report about the unchecked interval.' in text for text in case.texts())
    assert case.store.config(prep.SCHEDULED_SKIPPED_PREFIX + row['id']) is None
    # The old run retains its evidence; a later run must not repeat the skipped slots.
    assert unchecked_fact(case.service.preparation_run_fact(case.store.job(work_id))) == expected
    if report == 'closing':
        assert settled['state'] == prep.STATE_DELIVERED
    else:
        case.model_calls.clear()
        case.run_slot('Following report')
        assert all(unchecked_fact(str(item.get('content', ''))) is None
                   for body in case.model_calls for item in body['messages']
                   if item.get('role') == 'user')
        assert unchecked_fact(case.service.preparation_run_fact(
            case.store.job(case.service.preparations.get(row['id'])['last_run_job_id']))) is None


@pytest.mark.parametrize('read_fact', [False, True])
def test_failed_run_keeps_unchecked_facts_for_next_successful_run(case, read_fact):
    case.service.preparations.set_budget(1)
    case.run_slot('Initial check')
    refused = skip_slot(case)
    case.service.preparations.set_budget(0)
    row = case.service.preparations.get(refused['id'])
    case.now = row['due_at']
    work_id = case.service.preparations.start(row, channel='web', chat_id=None, now=case.now)
    assert work_id
    if read_fact:
        assert unchecked_fact(case.service.preparation_run_fact(case.store.job(work_id)))['count'] == 1
    # A terminal failure may happen either before or after the worker read its brief.
    with case.store.db() as db:
        db.execute("UPDATE jobs SET status='failed',error='fixture failure' WHERE id=?", (work_id,))
    assert case.tick()
    assert case.store.config(prep.SCHEDULED_SKIPPED_PREFIX + row['id'])['count'] == 1
    case.service.deliver_notification()  # A failed-run notice is not the requested report.
    assert case.store.config(prep.SCHEDULED_SKIPPED_PREFIX + row['id'])['count'] == 1
    case.service.use_decision_engine(watch_engine([True]))
    case.run_slot('Recovered check')
    current = case.service.preparations.get(row['id'])
    fact = unchecked_fact(case.service.preparation_run_fact(case.store.job(current['last_run_job_id'])))
    assert fact['count'] == 1 and fact['first_slot_at'] == refused['due_at']
    assert case.store.config(prep.SCHEDULED_SKIPPED_PREFIX + row['id']) is None


def test_refused_closing_slot_closes_window_and_daily_notice_covers_unchecked_tail(case):
    case.service.preparations.edit(case.watched['id'], max_runs=4)
    case.run_slot('Initial check')
    case.run_slot('Initial check')
    first = skip_slot(case)
    closing = skip_slot(case)
    final = case.service.preparations.get(case.watched['id'])
    assert final['state'] == prep.STATE_EXPIRED
    assert final['run_count'] == 4 and final['last_outcome'] == 'skipped'
    assert case.store.config(prep.SCHEDULED_SKIPPED_PREFIX + final['id']) is None
    assert len(case.runs(final['id'])) == 2, 'no closing Work or invented report'
    assert skipped(case)[-1]['slot_at'] == closing['due_at']
    assert usage(case) == 2
    case.now += 3600
    assert not case.tick(), 'a refused final slot cannot keep the window alive'
    with case.store.db() as db:
        notices = list(db.execute('SELECT job_id,state,fingerprint FROM telegram_notifications WHERE kind=?',
                                 (prep.SCHEDULED_BUDGET_NOTICE,)))
        assert len(notices) == 1 and notices[0]['state'] == 'sent'
        text = db.execute('SELECT content FROM messages WHERE job_id=?', (notices[0]['fingerprint'],)).fetchone()[0]
        assert db.execute('SELECT count(*) FROM telegram_notifications WHERE kind=?',
                          (prep.NOTIFY_KIND,)).fetchone()[0] == 0
    # The single daily status notice warns about the unchecked tail and the absent closing report.
    from personal_agent.telegram_locale import translate
    english = translate(text, 'en')
    assert 'closing checks' in english and 'without a closing report' in english
    assert prep.local_text(first['due_at'], first['timezone']) in text
    assert sum(text in body.get('text', '') for method, body in case.telegram if method == 'sendMessage') == 1


def test_skipped_slot_fact_message_in_all_locales():
    facts = json.dumps({'count': 2, 'reason': prep.SCHEDULED_BUDGET_REASON})
    for language in ('ko', 'en', 'ja', 'zh'):
        text = msg('preparations.run_budget_skipped', language, facts=facts)
        assert unchecked_fact(text) == json.loads(facts)
        assert text != 'preparations.run_budget_skipped'


def test_quiet_check_retains_unchecked_slots_until_a_report_carries_them(case):
    case.service.preparations.set_budget(1)
    case.run_slot('Initial check')
    refused = skip_slot(case)
    case.service.preparations.set_budget(0)
    quiet = case.run_slot('Quiet recovery')
    assert quiet['last_decision'] == prep.DECISION_QUIET
    assert case.store.config(prep.SCHEDULED_SKIPPED_PREFIX + quiet['id'])['count'] == 1
    case.service.use_decision_engine(watch_engine([True]))
    reported = case.run_slot('Report with unchecked interval')
    assert reported['last_decision'] == prep.DECISION_NOTIFY
    fact = unchecked_fact(case.service.preparation_run_fact(case.store.job(reported['last_run_job_id'])))
    assert fact['count'] == 1 and fact['first_slot_at'] == refused['due_at']
    assert case.store.config(prep.SCHEDULED_SKIPPED_PREFIX + quiet['id']) is None



def test_web_first_skip_notifies_every_paired_surface_once_and_keeps_local_notice(case):
    from contextlib import ExitStack
    service = case.service
    terminal_texts = []
    service.terminal_surface.attach(terminal_texts.append)
    posted = {'slack': [], 'mattermost': []}
    for name in posted:
        case.store.put(name, {'enabled': True, 'owner_user_id': name + '-owner',
                             'channel_id': 'dm', 'team_id': 'fixture-team', 'generation': name + '-generation', 'company_approved': True})
    service.preparations.set_budget(1)
    case.now = case.watched['due_at']
    assert service.preparations.start(case.watched, channel='web', chat_id=None, now=case.now)
    with ExitStack() as stack:
        for name, texts in posted.items():
            surface = getattr(service, name + '_surface')
            def post(_cfg, text, texts=texts):
                texts.append(text)
                return 'fixture-post'
            stack.enter_context(patch.object(surface, '_post', side_effect=post))
        # A local one-shot consumes the first refused slot; a different Telegram row follows.
        for channel in ('web', 'telegram', 'telegram'):
            row = service.preparations.create(kind='prepare', goal=channel + ' skipped check', due_at=case.now,
                timezone='Asia/Seoul', recurrence=None, channel=channel, created_from='settings',
                state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS)
            assert not service.preparations.start(row, channel='web', chat_id=None, now=case.now)
            assert service.deliver_notification() == (channel == 'web')
            for name in ('slack', 'mattermost', 'terminal'):
                surface = getattr(service, name + '_surface')
                assert not surface.deliver_notification(), 'the service already delivered each surface once'
    with case.store.db() as db:
        notices = list(db.execute('SELECT surface,state FROM telegram_notifications WHERE kind=?',
                                 (prep.SCHEDULED_BUDGET_NOTICE,)))
        messages = list(db.execute('SELECT content FROM messages WHERE job_id=?',
                                   (prep.SCHEDULED_USAGE_PREFIX + service.preparations.budget_day(case.now),)))
    assert {row['surface'] for row in notices} == {'telegram', 'slack', 'mattermost', 'terminal'}
    assert len(notices) == 4 and all(row['state'] == 'sent' for row in notices)
    assert len(messages) == 1
    assert sum(messages[0]['content'] in body.get('text', '') for method, body in case.telegram
               if method == 'sendMessage') == 1
    assert all(len(texts) == 1 for texts in posted.values()) and len(terminal_texts) == 1
    assert 'web skipped check' in service.preparations_status()['budget']['notice']['en']


@pytest.mark.parametrize('finish', ['one_shot', 'window_elapsed', 'cancelled', 'deleted'])
def test_no_orphaned_pending_skip_facts_after_a_row_can_no_longer_run(case, finish):
    service = case.service
    service.preparations.set_budget(1)
    case.now = case.watched['due_at']
    assert service.preparations.start(case.watched, channel='web', chat_id=None, now=case.now)
    if finish == 'one_shot':
        row = service.preparations.create(kind='prepare', goal='One-shot check', due_at=case.now,
            timezone='Asia/Seoul', recurrence=None, channel='web', created_from='settings',
            state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS)
    else:
        row = case.watch(span=1800)
        case.now = row['due_at']
    assert not service.preparations.start(row, channel='web', chat_id=None, now=case.now)
    pending_key = prep.SCHEDULED_SKIPPED_PREFIX + row['id']
    if finish != 'one_shot':
        assert case.store.config(pending_key)['count'] == 1
        if finish == 'window_elapsed':
            case.now = row['window_end'] + 1
            current = service.preparations.get(row['id'])
            assert not service.preparations.start(current, channel='web', chat_id=None, now=case.now)
        elif finish == 'cancelled':
            service.preparations.cancel(row['id'])
        else:
            service.preparations.delete(row['id'])
    assert case.store.config(pending_key) is None
    # Generic daily wording covers every remaining slot; detailed skip evidence is retained.
    notice = service.preparations_status()['budget']['notice']['en']
    assert 'Remaining scheduled checks today' in notice and 'closing checks' in notice
    assert any(item['preparation_id'] == row['id'] for item in skipped(case))


def test_new_local_day_prunes_only_old_usage_when_admission_commits(case):
    service = case.service
    day = datetime.fromisoformat(service.preparations.budget_day(case.now))
    prior = {}
    for age in range(1, 9):
        key = prep.SCHEDULED_USAGE_PREFIX + (day - timedelta(days=age)).date().isoformat()
        prior[key] = age
        case.store.put(key, age)
    unrelated = 'scheduled_work_usage_other'
    case.store.put(unrelated, {'retain': True})
    case.store.put(prep.SCHEDULED_BUDGET_KEY, {'daily_calls': 2})
    case.now = case.watched['due_at']
    with patch.object(case.store, 'enqueue', side_effect=ValueError('queue full')):
        assert not service.preparations.start(case.watched, channel='web', chat_id=None, now=case.now)
    assert all(case.store.config(key) == value for key, value in prior.items())
    assert service.preparations.start(case.watched, channel='web', chat_id=None, now=case.now)
    for key, age in prior.items():
        assert case.store.config(key) == (age if age < prep.SCHEDULED_USAGE_RETENTION_DAYS else None)
    assert usage(case) == 1
    assert case.store.config(unrelated) == {'retain': True}
    assert service.preparations.budget_settings() == {'daily_calls': 2}


@pytest.mark.parametrize('stored', [{'daily_calls': 'bad'}, {'daily_calls': -1},
                                   {'daily_calls': True}, ['wrong shape'], None, '{invalid JSON'])
def test_malformed_stored_budget_uses_default_logs_once_and_still_admits(case, caplog, stored):
    import logging
    if stored == '{invalid JSON':
        with case.store.db() as db:
            db.execute('UPDATE config SET value=? WHERE key=?', (stored, prep.SCHEDULED_BUDGET_KEY))
    else:
        case.store.put(prep.SCHEDULED_BUDGET_KEY, stored)
    case.now = case.watched['due_at']
    for number in range(2):
        case.service.preparations.create(kind='prepare', goal='Another check ' + str(number), due_at=case.now,
            timezone='Asia/Seoul', recurrence=None, channel='web', created_from='settings',
            state=prep.STATE_SCHEDULED, accepted_by=prep.ACCEPTED_OWNER_SETTINGS)
    with caplog.at_level(logging.WARNING, logger=prep.__name__):
        assert case.tick()
        assert case.service.preparations.budget_settings() == {'daily_calls': prep.DEFAULT_SCHEDULED_DAILY_CALLS}
        assert case.service.preparations.budget_settings() == {'daily_calls': prep.DEFAULT_SCHEDULED_DAILY_CALLS}
    with case.store.db() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0] == 3
    assert usage(case) == 3 and not skipped(case)
    warnings = [record for record in caplog.records if record.name == prep.__name__]
    assert len(warnings) == 1
    assert 'using default' in warnings[0].message


def settle_budget_report_without_notification_delivery(case, answer='Report with the unchecked interval'):
    row = case.service.preparations.get(case.watched['id'])
    case.now = row['due_at'] + 1
    assert case.tick()
    case.script = [{'content': answer}] * 2
    assert case.service.run_one()
    running = case.service.preparations.get(row['id'])
    job = case.store.job(running['last_run_job_id'])
    # Observe what this Work actually carried, without asking the fact builder to bind it.
    with case.store.db() as db:
        event = db.execute('SELECT detail FROM tool_events WHERE job_id=? AND tool=? AND status=?',
                           (job['id'], prep.SCHEDULED_BUDGET_KEY, 'unchecked-slots')).fetchone()
    assert event
    snapshot = json.loads(event['detail'])
    if job['delivery'] == 'pending':
        case.service.deliver_one()
        assert case.store.job(job['id'])['delivery'] == 'sent'
    assert case.tick()
    with case.store.db() as db:
        notice = db.execute('SELECT * FROM telegram_notifications WHERE job_id=? AND kind=?',
                            (job['id'], prep.NOTIFY_KIND)).fetchone()
    return job, snapshot, dict(notice) if notice else None


def start_unconfirmed_budget_report(case, *, terminal=False):
    case.service.preparations.set_budget(1)
    if terminal:
        case.service.preparations.edit(case.watched['id'], max_runs=3)
    case.run_slot('Initial check')
    skip_slot(case)
    pending_key = prep.SCHEDULED_SKIPPED_PREFIX + case.watched['id']
    pending = case.store.config(pending_key)
    assert pending['count'] == 1
    case.service.preparations.set_budget(0)
    case.service.use_decision_engine(watch_engine([True] * 6))
    job, snapshot, notice = settle_budget_report_without_notification_delivery(case)
    assert job['delivery'] == 'none'
    assert notice and notice['state'] == 'queued'
    assert snapshot == pending
    return pending_key, job, snapshot, notice


def confirm_budget_report(case, job, notice):
    # The existing surfaces store the confirmed send before invoking record_notified.
    case.store.update_notification(notice['id'], 'sent', delivered=True)
    assert case.service.preparations.record_notified(
        job['id'], notice['fingerprint'], job['response'], case.now)


@pytest.mark.parametrize('terminal', [False, True])
def test_unchecked_facts_wait_for_record_notified_even_on_the_final_run(case, terminal):
    key, job, snapshot, notice = start_unconfirmed_budget_report(case, terminal=terminal)
    assert case.store.config(key) == snapshot, 'settling and queuing a report is not delivery'
    assert case.service.preparations.get(case.watched['id'])['state'] == (
        prep.STATE_DELIVERED if terminal else prep.STATE_SCHEDULED)
    # An idle tick's orphan cleanup cannot drop a terminal row's pending report.
    case.tick()
    assert case.store.config(key) == snapshot
    confirm_budget_report(case, job, notice)
    assert case.store.config(key) is None


@pytest.mark.parametrize('state', ['queued', 'cancelled', 'unknown'])
def test_unconfirmed_notification_repeats_unchecked_interval_on_next_report(case, state):
    key, old_job, snapshot, old_notice = start_unconfirmed_budget_report(case)
    assert case.store.config(key) == snapshot
    case.store.update_notification(old_notice['id'], state)
    # Pending input and the delivery receipt survive a restart independently.
    case.store = QuickStore(case.root)
    case.service = case.make_service()
    case.service.use_decision_engine(watch_engine([True] * 6))
    job, carried_again, notice = settle_budget_report_without_notification_delivery(case, 'A later report')
    assert job['id'] != old_job['id']
    assert carried_again == snapshot
    assert case.store.config(key) == snapshot
    confirm_budget_report(case, job, notice)
    assert case.store.config(key) is None


def test_newer_skips_survive_confirmation_of_an_earlier_report(case):
    key, old_job, snapshot, old_notice = start_unconfirmed_budget_report(case)
    assert case.store.config(key) == snapshot
    case.service.preparations.set_budget(1)
    newer_slot = case.service.preparations.get(case.watched['id'])
    case.now = newer_slot['due_at'] + 1
    assert case.tick()
    assert not case.service.run_one()
    newer = case.store.config(key)
    assert newer == {**snapshot, 'count': 2, 'last_slot_at': newer_slot['due_at']}
    confirm_budget_report(case, old_job, old_notice)
    assert case.store.config(key) == newer
    case.service.preparations.set_budget(0)
    job, carried_again, notice = settle_budget_report_without_notification_delivery(case, 'Report both missed checks')
    assert carried_again == newer
    confirm_budget_report(case, job, notice)
    assert case.store.config(key) is None


@pytest.mark.parametrize('channel', ['telegram', 'web'])
def test_directly_delivered_or_explicitly_kept_result_still_clears_at_settle(case, channel):
    key, _job, snapshot, notice = start_unconfirmed_budget_report(case)
    assert case.store.config(key) == snapshot
    case.store.update_notification(notice['id'], 'cancelled')
    with case.store.db() as db:
        db.execute('UPDATE preparations SET delivery_mode=?,channel=? WHERE id=?',
                   (prep.DELIVERY_ALWAYS, channel, case.watched['id']))
    job, carried_again, notice = settle_budget_report_without_notification_delivery(case, 'Direct or kept report')
    assert carried_again == snapshot and notice is None
    assert case.store.job(job['id'])['delivery'] == ('sent' if channel == 'telegram' else 'none')
    assert case.store.config(key) is None


@pytest.mark.parametrize('state', ['sending', 'unknown'])
def test_terminal_row_keeps_facts_while_final_notification_is_unconfirmed(case, state):
    key, job, snapshot, notice = start_unconfirmed_budget_report(case, terminal=True)
    assert case.store.config(key) == snapshot
    case.store.update_notification(notice['id'], state)
    case.tick()
    assert case.store.config(key) == snapshot
    confirm_budget_report(case, job, notice)
    assert case.store.config(key) is None



def test_cancelled_terminal_report_still_uses_orphan_cleanup_and_the_daily_notice(case):
    key, _job, snapshot, notice = start_unconfirmed_budget_report(case, terminal=True)
    assert case.store.config(key) == snapshot
    case.store.update_notification(notice['id'], 'cancelled')
    case.tick()
    assert case.store.config(key) is None
    daily_notice = case.service.preparations_status()['budget']['notice']['en']
    assert 'Remaining scheduled checks today' in daily_notice and 'without a closing report' in daily_notice
