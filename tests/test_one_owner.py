"""Issue #319: temporary stores, encrypted fixtures, no model or network calls."""
import json
from itertools import permutations
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet

from personal_agent.browser_session import step_binding
from personal_agent.calendar_oauth import CalendarOAuth, EncryptedCalendarSecretStore
from personal_agent.connector_contract import ConnectorRegistry, ConnectorState, CONNECTOR_STATE_KEY, _owner_key
from personal_agent.conversation_handoff import ConversationHandoffError
from personal_agent.gmail import EncryptedGmailSecretStore, GmailConnector, GMAIL_CONNECTOR, GMAIL_CONNECTOR_ID, GMAIL_READONLY_SCOPE
from personal_agent.owner_identity import migrate_connector_owner, MIGRATION_KEY, CONFLICTS_KEY
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

LOCAL = 'local-owner'


@pytest.fixture
def setup(tmp_path):
    store = QuickStore(tmp_path / 'owner')
    encrypted = EncryptedGmailSecretStore(store, Fernet.generate_key())
    clock = [1000.0]
    registry = ConnectorRegistry(store, (GMAIL_CONNECTOR,), clock=lambda: clock[0])
    calls = []
    def transport(method, url, params, headers):
        calls.append(headers)
        return {'messages': []}
    gmail = GmailConnector(encrypted, 'fixture-client', 'https://example.test/callback',
                           registry=registry, now=lambda: clock[0], transport=transport)
    service = AgentService(store, connector_registry=registry, gmail=gmail)
    for name, user in [('telegram', 77), ('mattermost', 'mm-owner'), ('slack', 'sl-owner')]:
        store.put(name, {'enabled': True, 'generation': 'g1', 'user_id': user, 'owner_user_id': user,
                         'channel_id': 'dm', 'team_id': 'team', 'company_approved': True})
    return store, service, registry, gmail, clock, calls


def work(store, name, user, key='work'):
    ident = store.enqueue('fixture request', f'{name}-{key}', f'{name}:g1' if user is not None else name, user)
    return store.job(ident)


def connect(gmail, owner=LOCAL, token='fixture-token'):
    start = gmail.begin_oauth(owner)
    state = parse_qs(urlsplit(start['authorization_url']).query)['state'][0]
    return gmail.complete_oauth(owner, {'state': state, 'code': 'fixture-code'},
        lambda _: {'access_token': token, 'refresh_token': 'fixture-refresh',
                   'expires_in': 7200, 'scope': GMAIL_READONLY_SCOPE})


@pytest.mark.parametrize('origin', ['telegram', 'mattermost', 'slack', 'web'])
def test_credentials_are_visible_across_every_surface_and_web(setup, origin):
    store, service, registry, gmail, _, calls = setup
    users = {'telegram': 77, 'mattermost': 'mm-owner', 'slack': 'sl-owner', 'web': None}
    source = work(store, origin, users[origin], 'origin')
    connect(gmail, service.connector_owner_id(source))
    for name, user in users.items():
        job = work(store, name, user)
        assert service.connector_owner_id(job) == LOCAL
        assert service.mail_for(job).search('fixture')['messages'] == []
        assert service._park_for_connector(job, GMAIL_CONNECTOR_ID) is None
    assert len(calls) == 4
    assert registry.status(LOCAL, GMAIL_CONNECTOR_ID).state is ConnectorState.CONNECTED


def test_callback_owner_is_independent_of_pairing_order(setup):
    store, service, *_ = setup
    configs = {name: store.config(name) for name in ('telegram', 'mattermost', 'slack')}
    for order in permutations(configs):
        for name in configs:
            store.put(name, {})
        for name in order:
            store.put(name, configs[name])
            assert service.connector_callback_owner(GMAIL_CONNECTOR_ID) == LOCAL


@pytest.mark.parametrize('change', ['repaired', 'disconnected'])
def test_issued_slack_browser_approval_cannot_be_spent_after_pairing_changes(setup, change):
    store, service, *_ = setup
    job = work(store, 'slack', 'sl-owner')
    binding = step_binding(job['id'], 'browser_type', 'https://example.test/pay', 'field', 'fixture')
    with store.db() as db:
        db.execute("UPDATE jobs SET status='partial' WHERE id=?", (job['id'],))
    service._request_browser_step(job, binding, 'fixture payment')
    assert service._decide_browser_step(job['id'], True)['approved']
    if change == 'repaired':
        store.put('slack', {**store.config('slack'), 'generation': 'g2'})
    else:
        service.disconnect_slack()
    assert not service._consume_browser_step(job, binding)


def test_browser_completion_does_not_resume_disconnected_telegram_work(setup):
    store, service, _, gmail, _, _ = setup
    job = work(store, 'telegram', 77)
    service._park_for_connector(job, GMAIL_CONNECTOR_ID)
    with store.db() as db:
        db.execute("UPDATE jobs SET status='awaiting_connection' WHERE id=?", (job['id'],))
    start = service.begin_gmail_connection()
    state = parse_qs(urlsplit(start['authorization_url']).query)['state'][0]
    service.disconnect_telegram()
    service.gmail_token_exchange = lambda _: {'access_token': 'new-fixture-token', 'expires_in': 7200,
                                              'scope': GMAIL_READONLY_SCOPE}
    result = service.complete_gmail_connection({'state': state, 'code': 'fixture'})
    assert result['connected']
    assert not result.get('scheduled')
    assert result.get('resume_refused')
    assert store.job(job['id'])['status'] == 'awaiting_connection'
    assert service.resume_ready_native_work() == 0
    # Connection is owner state even though this particular issuer is gone.
    assert gmail.credential_current(LOCAL)


@pytest.mark.parametrize('surface', ['telegram', 'mattermost', 'slack'])
def test_disconnect_preserves_owner_connectors(setup, surface):
    _, service, registry, gmail, *_ = setup
    connect(gmail)
    getattr(service, 'disconnect_' + surface)()
    assert registry.status(LOCAL, GMAIL_CONNECTOR_ID).state is ConnectorState.CONNECTED
    assert gmail.credential_current(LOCAL)


def seed_observation(store, owner, generation='g1'):
    with store.db() as db:
        db.execute('INSERT INTO context_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('observation', owner, generation, 0, owner + ':message', 1, 1000, 1, 'place_reference',
                    None, 1000, 1000, None, 2000, json.dumps({'label': 'fixture', 'latitude': 1, 'longitude': 2}), 'current'))


@pytest.mark.parametrize('surface,user', [('mattermost', 'mm-owner'), ('slack', 'sl-owner')])
def test_observations_use_only_current_source_generations_without_telegram(setup, surface, user):
    store, service, *_ = setup
    store.put('telegram', {})
    store.put('current_context', {'enabled': True})
    observations = service.context_observations
    observations.clock = lambda: 1001
    observations.set_controls({'enabled': True})
    seed_observation(store, f'{surface}:{user}')
    assert len(observations.usable()) == 1
    assert observations.status()['observations'][0]['source'] == surface
    # Broker resolution also checks active source binding, not just the list.
    with store.db() as db:
        assert service.current_state._observation(db, (LOCAL, ''), 0, 'observation', 1001)
    store.put(surface, {**store.config(surface), 'generation': 'g2'})
    assert observations.usable() == []
    assert observations.status()['observations'] == []
    with store.db() as db:
        assert service.current_state._observation(db, (LOCAL, ''), 0, 'observation', 1001) is None


def test_migration_keeps_newest_records_conflict_and_is_idempotent(setup, caplog):
    store, service, registry, gmail, clock, _ = setup
    connect(gmail, 'telegram:77', 'older-google-account')
    clock[0] += 1
    connect(gmail, 'slack:sl-owner', 'newer-google-account')
    migrate_connector_owner(service)
    token = gmail.store.secret(gmail.token_slots(_owner_key(LOCAL))[GMAIL_CONNECTOR_ID])
    assert token['access_token'] == 'newer-google-account'
    assert token['owner'] == _owner_key(LOCAL)
    assert registry.status(LOCAL, GMAIL_CONNECTOR_ID).state is ConnectorState.CONNECTED
    assert registry.status('telegram:77', GMAIL_CONNECTOR_ID).state is ConnectorState.DISCONNECTED
    conflicts = store.config(CONFLICTS_KEY)
    assert conflicts == [{'connector': GMAIL_CONNECTOR_ID, 'source': 'telegram:77', 'kept_source': 'slack:sl-owner'}]
    before = store.config(CONNECTOR_STATE_KEY), store.config(MIGRATION_KEY), conflicts, store.secret_path.read_bytes()
    migrate_connector_owner(service)
    after = store.config(CONNECTOR_STATE_KEY), store.config(MIGRATION_KEY), store.config(CONFLICTS_KEY), store.secret_path.read_bytes()
    assert after == before
    assert 'older-google-account' not in json.dumps(store.config(CONFLICTS_KEY))
    assert service.connector_connections()[0]['migration_conflicts'] == conflicts
    assert caplog.text.count('dropped_source=telegram:77') == 1
    assert 'older-google-account' not in caplog.text
    assert 'newer-google-account' not in caplog.text


@pytest.mark.parametrize('phase', ['stage', 'publish', 'commit', 'cleanup'])
def test_migration_recovers_after_interruption(setup, phase):
    store, service, _, gmail, _, _ = setup
    connect(gmail, 'mattermost:mm-owner')
    target = gmail.token_slots(_owner_key(LOCAL))[GMAIL_CONNECTOR_ID]
    original_secret = gmail.store.secret
    if phase in ('stage', 'publish', 'cleanup'):
        slot = {'stage': 'owner-migration:' + target, 'publish': target,
                'cleanup': gmail.token_slots(_owner_key('mattermost:mm-owner'))[GMAIL_CONNECTOR_ID]}[phase]
        def interrupted(key, value=None):
            result = original_secret(key, value)
            if key == slot and value is not None:
                raise RuntimeError('fixture interruption')
            return result
        patch = mock.patch.object(gmail.store, 'secret', interrupted)
    else:
        # Interrupt after publishing tokens, before the SQLite commit.
        from personal_agent.quickstart_store import _StoreConnection
        original_execute = _StoreConnection.execute
        def interrupted(db, sql, parameters=()):
            if sql.startswith('INSERT INTO config') and parameters[0] == CONFLICTS_KEY:
                raise RuntimeError('fixture interruption')
            return original_execute(db, sql, parameters)
        patch = mock.patch.object(_StoreConnection, 'execute', interrupted)
    with patch, pytest.raises(RuntimeError, match='fixture interruption'):
        migrate_connector_owner(service)
    migrate_connector_owner(service)
    assert gmail.credential_current(LOCAL)
    assert store.config(MIGRATION_KEY)['phase'] == 'complete'
    assert not gmail.store.secret(gmail.token_slots(_owner_key('mattermost:mm-owner'))[GMAIL_CONNECTOR_ID])
    assert store.config(CONFLICTS_KEY) == []


def test_non_owner_is_refused_before_connector_use(setup):
    store, service, _, gmail, *_ = setup
    connect(gmail)
    with mock.patch.object(service, 'connector_owner_id', side_effect=AssertionError('connector used')):
        service.slack_surface.ingest_event({'team_id': 'team', 'event': {'type': 'message', 'channel': 'dm',
            'user': 'intruder', 'ts': '1000.001', 'text': 'read connected account'}}, 'g1')
        assert service.mattermost_surface._ingest_owner_message('fixture', 'read account', None, 'intruder', 'g1') is None
        service.ingest_update({'update_id': 1, 'message': {'message_id': 1, 'date': 1000,
            'chat': {'id': 99, 'type': 'private'}, 'from': {'id': 99}, 'text': 'read connected account'}}, 'g1')
    assert store.jobs() == []


def test_one_callback_resumes_current_issuers_but_not_a_repaired_surface(setup):
    store, service, *_ = setup
    jobs = [work(store, name, user) for name, user in
            [('telegram', 77), ('slack', 'sl-owner'), ('web', None)]]
    for job in jobs:
        service._park_for_connector(job, GMAIL_CONNECTOR_ID)
        with store.db() as db:
            db.execute("UPDATE jobs SET status='awaiting_connection' WHERE id=?", (job['id'],))
    state = parse_qs(urlsplit(service.begin_gmail_connection()['authorization_url']).query)['state'][0]
    store.put('slack', {**store.config('slack'), 'generation': 'g2'})
    service.gmail_token_exchange = lambda _: {'access_token': 'new-fixture-token', 'expires_in': 7200,
                                              'scope': GMAIL_READONLY_SCOPE}
    result = service.complete_gmail_connection({'state': state, 'code': 'fixture'})
    assert {row['work_id'] for row in result['resumed']} == {jobs[0]['id'], jobs[2]['id']}
    assert result['resume_refusals'] == [{'work_id': jobs[1]['id'], 'reason': 'generation_changed'}]
    assert [store.job(job['id'])['status'] for job in jobs] == ['queued', 'awaiting_connection', 'queued']


@pytest.mark.parametrize('newest', [LOCAL, 'slack:sl-owner'])
def test_migration_compares_existing_local_connection_with_surface_tokens(setup, newest):
    store, service, _, gmail, clock, _ = setup
    oldest = 'slack:sl-owner' if newest == LOCAL else LOCAL
    connect(gmail, oldest, 'older-account')
    clock[0] += 1
    connect(gmail, newest, 'newest-account')
    migrate_connector_owner(service)
    token = gmail.store.secret(gmail.token_slots(_owner_key(LOCAL))[GMAIL_CONNECTOR_ID])
    assert token['access_token'] == 'newest-account'
    assert store.config(CONFLICTS_KEY)[0]['source'] == oldest
    assert store.config(CONFLICTS_KEY)[0]['kept_source'] == newest


@pytest.mark.parametrize('grant', ['read', 'write', 'drive-read'])
def test_migration_preserves_each_declared_encrypted_grant(setup, grant):
    from personal_agent.calendar_oauth import DRIVE_GRANT, DRIVE_SECRET_NAMESPACE, _spec
    store, service, registry, _, clock, _ = setup
    grant = DRIVE_GRANT if grant == 'drive-read' else grant
    options = {'namespace': DRIVE_SECRET_NAMESPACE} if grant == DRIVE_GRANT else {}
    encrypted = EncryptedCalendarSecretStore(store, Fernet.generate_key(), **options)
    oauth = CalendarOAuth(encrypted, 'fixture-client', 'https://example.test/calendar/callback',
                          registry=registry, grants=(grant,), now=lambda: clock[0])
    if grant == DRIVE_GRANT:
        service.drive_oauth = oauth
    else:
        service.calendar_oauth = oauth
    start = oauth.begin_oauth('mattermost:mm-owner', grant=grant)
    state = parse_qs(urlsplit(start['authorization_url']).query)['state'][0]
    spec = _spec(grant)
    oauth.complete_oauth('mattermost:mm-owner', {'state': state, 'code': 'fixture'},
        lambda _: {'access_token': 'fixture-grant-token', 'refresh_token': 'fixture-refresh',
                   'expires_in': 7200, 'scope': ' '.join(spec.required_scopes)})
    migrate_connector_owner(service)
    assert oauth.credential_current(LOCAL, grant=grant)
    assert registry.status(LOCAL, spec.connector_id).state is ConnectorState.CONNECTED


def test_callback_denial_notifies_only_its_current_issuer(setup):
    from personal_agent.gmail import GmailError
    store, service, *_ = setup
    current = work(store, 'telegram', 77)
    stale = work(store, 'slack', 'sl-owner')
    for job in (current, stale):
        service._park_for_connector(job, GMAIL_CONNECTOR_ID)
        with store.db() as db:
            db.execute("UPDATE jobs SET status='awaiting_connection' WHERE id=?", (job['id'],))
    state = parse_qs(urlsplit(service.begin_gmail_connection()['authorization_url']).query)['state'][0]
    store.put('slack', {**store.config('slack'), 'generation': 'g2'})
    service.gmail_token_exchange = lambda _: pytest.fail('denial must not exchange tokens')
    with mock.patch.object(service, '_notify_owner') as notice, pytest.raises(GmailError):
        service.complete_gmail_connection({'state': state, 'error': 'access_denied'})
    assert [call.args[0] for call in notice.call_args_list] == ['telegram:77']
    assert store.job(current['id'])['status'] == 'failed'
    assert store.job(stale['id'])['status'] == 'awaiting_connection'


@pytest.mark.parametrize('surface,user', [('telegram', 77), ('mattermost', 'mm-owner'), ('slack', 'sl-owner')])
def test_observation_forget_preserves_owner_and_source_generation_checks(setup, surface, user):
    from personal_agent.owner_forget import OwnerForget, ForgetError
    store, service, *_ = setup
    observations = service.context_observations
    observations.clock = lambda: 1001
    observations.set_controls({'enabled': True})
    seed_observation(store, f'{surface}:{user}')
    forget = OwnerForget(store, clock=lambda: 1001)
    ref = 'obs:observation'
    with pytest.raises(ForgetError):
        forget.forget('unrelated-owner', ref)
    assert len(observations.usable()) == 1
    receipt = forget.forget(LOCAL, ref)
    assert observations.usable() == []
    forget.undo(LOCAL, receipt['receipt'])
    assert len(observations.usable()) == 1
    receipt = forget.forget(LOCAL, ref)
    store.put(surface, {**store.config(surface), 'generation': 'g2'})
    with pytest.raises(ForgetError):
        forget.undo(LOCAL, receipt['receipt'])
    assert observations.usable() == []
    with pytest.raises(ForgetError):
        forget.forget(LOCAL, ref)


def test_registered_surface_identity_reads_need_only_store_config():
    from types import SimpleNamespace
    from personal_agent.conversation_surface import registered_surfaces
    configs = {'telegram': {'enabled': True, 'user_id': 77, 'generation': 'tg'},
               'mattermost': {'enabled': True, 'owner_user_id': 'mm', 'generation': 'mg'},
               'slack': {'enabled': True, 'owner_user_id': 'sl', 'generation': 'sg'},
               # #438: an enabled terminal row (a crash leaves one) adds no paired source identity.
               'terminal': {'enabled': True, 'owner_user_id': 'owner', 'channel_id': 'tty', 'generation': '3'}}
    service = SimpleNamespace(store=SimpleNamespace(config=lambda key, default=None: configs.get(key, default)))
    assert [(surface.paired_connector_owner(), surface.owner_generation())
            for surface in registered_surfaces(service)] == [('telegram:77', 'tg'), ('mattermost:mm', 'mg'), ('slack:sl', 'sg'),
                                                                 (None, '3')]


@pytest.mark.parametrize('wiring', ['constructor', 'installer'])
def test_startup_migrates_only_after_credential_holders_are_wired(setup, wiring):
    from personal_agent.quickstart import install_google_connectors
    store, _, registry, gmail, *_ = setup
    connect(gmail, 'telegram:77')
    if wiring == 'constructor':
        service = AgentService(store, connector_registry=registry, gmail=gmail)
    else:
        service = AgentService(store, connector_registry=registry)
        assert store.config(MIGRATION_KEY) is None
        assert gmail.credential_current('telegram:77')
        install_google_connectors(service, {'registry': registry, 'gmail': gmail,
            'exchange': lambda _: pytest.fail('migration cannot exchange tokens'),
            'drive_oauth': None, 'drive_reader_factory': None, 'source': 'fixture', 'client_id': 'fixture'})
    assert gmail.credential_current(LOCAL)
    assert not gmail.credential_current('telegram:77')
    before = store.secret_path.read_bytes(), store.config(MIGRATION_KEY)
    migrate_connector_owner(service)
    assert (store.secret_path.read_bytes(), store.config(MIGRATION_KEY)) == before


@pytest.mark.parametrize('historical_state', ['disconnected', 'connected'])
def test_migration_progress_is_independent_of_unconfigured_holders(setup, caplog, historical_state):
    from personal_agent.calendar_oauth import _spec
    store, _, registry, gmail, clock, _ = setup
    encrypted = EncryptedCalendarSecretStore(store, Fernet.generate_key())
    calendar = CalendarOAuth(encrypted, 'fixture-client', 'https://example.test/callback',
                             registry=registry, grants=('read',), now=lambda: clock[0])
    connector = _spec('read').connector_id
    # Retained disconnected metadata must not block a separately usable credential.
    registry.transition('telegram:77', connector, ConnectorState.DISCONNECTED)
    if historical_state == 'connected':
        start = calendar.begin_oauth('telegram:77')
        state = parse_qs(urlsplit(start['authorization_url']).query)['state'][0]
        calendar.complete_oauth('telegram:77', {'state': state, 'code': 'fixture'},
            lambda _: {'access_token': 'retained-calendar-secret', 'expires_in': 7200,
                       'scope': ' '.join(_spec('read').required_scopes)})
    calendar_slot = 'encrypted:calendar:' + calendar.token_slots(_owner_key('telegram:77'))[connector]
    calendar_ciphertext = store.secret(calendar_slot)
    connect(gmail, 'telegram:77', 'older-gmail-secret')
    clock[0] += 1
    connect(gmail, 'slack:sl-owner', 'newer-gmail-secret')
    legacy = _owner_key('telegram:77')
    calendar_row = store.config(CONNECTOR_STATE_KEY)[legacy][connector]
    service = AgentService(store, connector_registry=registry, gmail=gmail)
    assert gmail.credential_current(LOCAL)
    assert store.config(CONNECTOR_STATE_KEY)[legacy] == {connector: calendar_row}
    assert store.secret(calendar_slot) == calendar_ciphertext
    first_receipt = store.config(MIGRATION_KEY)
    assert connector not in first_receipt['connectors']
    conflicts = store.config(CONFLICTS_KEY)
    assert len(conflicts) == 1
    # Wiring the deferred holder migrates either its retained credential or
    # the credential newly supplied after the disconnected historical row.
    if historical_state == 'disconnected':
        start = calendar.begin_oauth('telegram:77')
        state = parse_qs(urlsplit(start['authorization_url']).query)['state'][0]
        calendar.complete_oauth('telegram:77', {'state': state, 'code': 'fixture'},
            lambda _: {'access_token': 'calendar-secret', 'expires_in': 7200,
                       'scope': ' '.join(_spec('read').required_scopes)})
    service.calendar_oauth = calendar
    with mock.patch.object(gmail.store, 'secret', side_effect=AssertionError('completed connector revisited')):
        migrate_connector_owner(service)
    assert calendar.credential_current(LOCAL)
    assert store.config(CONFLICTS_KEY) == conflicts
    assert store.config(MIGRATION_KEY)['connectors'][GMAIL_CONNECTOR_ID] == first_receipt['connectors'][GMAIL_CONNECTOR_ID]
    before = store.config(MIGRATION_KEY), store.secret_path.read_bytes()
    migrate_connector_owner(service)
    assert (store.config(MIGRATION_KEY), store.secret_path.read_bytes()) == before
    assert caplog.text.count('dropped_source=telegram:77') == 1


@pytest.mark.parametrize('holder_kind', ['gmail', 'calendar', 'drive'])
@pytest.mark.parametrize('wiring', ['constructor', 'installer'])
@pytest.mark.parametrize('failure', ['locked', 'wrong-key', 'one-unreadable'])
def test_startup_defers_unreadable_migration_without_losing_conflict_candidates(
        setup, caplog, holder_kind, wiring, failure):
    from personal_agent.calendar_oauth import DRIVE_GRANT, DRIVE_SECRET_NAMESPACE, CalendarOAuthError, _spec
    from personal_agent.gmail import GmailError
    from personal_agent.quickstart import install_google_connectors
    store, _, registry, gmail, clock, _ = setup
    key = Fernet.generate_key()
    if holder_kind == 'gmail':
        holder = gmail
        holder.store = EncryptedGmailSecretStore(store, key)
        connector, error = GMAIL_CONNECTOR_ID, GmailError
        def connect_holder(owner, token):
            connect(holder, owner, token)
        def secret_store(provider):
            return EncryptedGmailSecretStore(store, provider)
    else:
        grant = DRIVE_GRANT if holder_kind == 'drive' else 'read'
        options = {'namespace': DRIVE_SECRET_NAMESPACE} if holder_kind == 'drive' else {}
        def secret_store(provider):
            return EncryptedCalendarSecretStore(store, provider, **options)
        holder = CalendarOAuth(secret_store(key), 'fixture-client', 'https://example.test/callback',
                               registry=registry, grants=(grant,), now=lambda: clock[0])
        connector, error = _spec(grant).connector_id, CalendarOAuthError
        def connect_holder(owner, token):
            start = holder.begin_oauth(owner, grant=grant)
            state = parse_qs(urlsplit(start['authorization_url']).query)['state'][0]
            holder.complete_oauth(owner, {'state': state, 'code': 'fixture'},
                lambda _: {'access_token': token, 'expires_in': 7200,
                           'scope': ' '.join(_spec(grant).required_scopes)})
    connect_holder('telegram:77', 'older-readable-secret')
    clock[0] += 1
    connect_holder('slack:sl-owner', 'newest-unreadable-secret')
    before = store.config(CONNECTOR_STATE_KEY), store.secret_path.read_bytes()
    def locked():
        raise RuntimeError('secret-keychain-detail')
    holder.store = secret_store(locked if failure == 'locked' else Fernet.generate_key() if failure == 'wrong-key' else key)
    original = holder.store.secret
    unreadable = holder.token_slots(_owner_key('slack:sl-owner'))[connector]
    def failing_slot(slot, value=None):
        if slot == unreadable:
            raise error('newest-unreadable-secret')
        return original(slot, value)
    if failure == 'one-unreadable':
        holder.store.secret = failing_slot
    kwargs = {'gmail': holder} if holder_kind == 'gmail' else {holder_kind + '_oauth': holder}
    if wiring == 'constructor':
        service = AgentService(store, connector_registry=registry, **kwargs)
    else:
        service = AgentService(store, connector_registry=registry)
        built = {'registry': registry, 'exchange': lambda _: pytest.fail('no exchange'),
                 'drive_oauth': None, 'drive_reader_factory': None, 'source': 'fixture', 'client_id': 'fixture'}
        built.update(kwargs)
        if holder_kind == 'calendar':
            built['calendar_factory'] = None
        install_google_connectors(service, built)
    assert (store.config(CONNECTOR_STATE_KEY), store.secret_path.read_bytes()) == before
    assert store.config(MIGRATION_KEY) is None
    assert store.config(CONFLICTS_KEY) is None
    assert 'migration deferred' in caplog.text
    assert 'secret-keychain-detail' not in caplog.text
    assert 'newest-unreadable-secret' not in caplog.text
    assert 'older-readable-secret' not in caplog.text
    holder.store = secret_store(key)
    migrate_connector_owner(service)
    token = holder.store.secret(holder.token_slots(_owner_key(LOCAL))[connector])
    assert token['access_token'] == 'newest-unreadable-secret'
    assert len(store.config(CONFLICTS_KEY)) == 1
    before = store.config(MIGRATION_KEY), store.secret_path.read_bytes()
    migrate_connector_owner(service)
    assert (store.config(MIGRATION_KEY), store.secret_path.read_bytes()) == before


@pytest.mark.parametrize('phase', ['read', 'stage', 'publish'])
def test_unreadable_connector_does_not_block_another_available_connector(setup, phase, caplog):
    from personal_agent.calendar_oauth import CalendarOAuthError, _spec
    store, service, registry, gmail, clock, _ = setup
    encrypted = EncryptedCalendarSecretStore(store, Fernet.generate_key())
    calendar = CalendarOAuth(encrypted, 'fixture-client', 'https://example.test/callback',
                             registry=registry, grants=('read',), now=lambda: clock[0])
    connector = _spec('read').connector_id
    start = calendar.begin_oauth('telegram:77')
    state = parse_qs(urlsplit(start['authorization_url']).query)['state'][0]
    calendar.complete_oauth('telegram:77', {'state': state, 'code': 'fixture'},
        lambda _: {'access_token': 'calendar-secret', 'expires_in': 7200,
                   'scope': ' '.join(_spec('read').required_scopes)})
    connect(gmail, 'telegram:77')
    service.calendar_oauth = calendar
    legacy = _owner_key('telegram:77')
    calendar_row = store.config(CONNECTOR_STATE_KEY)[legacy][connector]
    source = calendar.token_slots(legacy)[connector]
    ciphertext = store.secret('encrypted:calendar:' + source)
    target = calendar.token_slots(_owner_key(LOCAL))[connector]
    original = encrypted.secret
    def unavailable(slot, value=None):
        if ((phase == 'read' and slot == source) or
                (phase == 'stage' and slot == 'owner-migration:' + target and value is not None) or
                (phase == 'publish' and slot == target and value is not None)):
            raise CalendarOAuthError('calendar-secret')
        return original(slot, value)
    with mock.patch.object(encrypted, 'secret', unavailable):
        migrate_connector_owner(service)
    assert gmail.credential_current(LOCAL)
    assert store.config(CONNECTOR_STATE_KEY)[legacy] == {connector: calendar_row}
    assert store.secret('encrypted:calendar:' + source) == ciphertext
    assert 'calendar-secret' not in caplog.text
    migrate_connector_owner(service)
    assert calendar.credential_current(LOCAL)
    assert store.config(MIGRATION_KEY)['phase'] == 'complete'


@pytest.mark.parametrize('phase', ['prepared', 'complete'])
def test_migration_recovers_original_receipt_format(setup, phase, caplog):
    store, service, _, gmail, clock, _ = setup
    connect(gmail, 'telegram:77')
    clock[0] += 1
    connect(gmail, 'slack:sl-owner', 'newer-account')
    target = gmail.token_slots(_owner_key(LOCAL))[GMAIL_CONNECTOR_ID]
    original = gmail.store.secret
    def interrupted(slot, value=None):
        if slot == target and value is not None:
            raise RuntimeError('fixture interruption')
        return original(slot, value)
    if phase == 'prepared':
        with mock.patch.object(gmail.store, 'secret', interrupted), pytest.raises(RuntimeError):
            migrate_connector_owner(service)
    else:
        migrate_connector_owner(service)
    entry = store.config(MIGRATION_KEY)['connectors'][GMAIL_CONNECTOR_ID]
    # The original v1 receipt described all selected connectors in one batch.
    store.put(MIGRATION_KEY, {'phase': phase,
        'selected': {GMAIL_CONNECTOR_ID: {'source': entry['source'], 'state': entry['state']}},
        'sources': entry['sources'], 'conflicts': entry['conflicts']})
    migrate_connector_owner(service)
    assert gmail.credential_current(LOCAL)
    assert len(store.config(CONFLICTS_KEY)) == 1
    assert caplog.text.count('dropped_source=telegram:77') == 1
    before = store.config(MIGRATION_KEY), store.secret_path.read_bytes()
    migrate_connector_owner(service)
    assert (store.config(MIGRATION_KEY), store.secret_path.read_bytes()) == before
