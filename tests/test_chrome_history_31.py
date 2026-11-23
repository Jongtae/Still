"""#31 model-free fixtures. Chrome and all connections use temporary owner state."""

import io
import json
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_agent import chrome_history as history
from personal_agent import manifests
from personal_agent.a2a_peers import READ_TOOLS
from personal_agent.agent_runtime import Capabilities, ToolError
from personal_agent.messages import msg
from personal_agent.providers import ModelAdapter
from personal_agent.quickstart import make_handler
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

ACTION = 'browser_history_search'
BASE = 1_700_000_000
MICROS = (BASE + history.UNIX_OFFSET) * 1_000_000
PRIVATE_TITLE = 'aurora private browsing title'
PRIVATE_URL = 'https://history.example.test/private-entry'
SETTINGS_URL = '/api/settings/chrome-history'


def add(db, identifier, title=PRIVATE_TITLE, url=PRIVATE_URL, visit=MICROS):
    db.execute('INSERT INTO urls VALUES (?, ?, ?, ?, ?)', (identifier, url, title, 1, visit))
    db.execute('INSERT INTO visits(url, visit_time) VALUES (?, ?)', (identifier, visit))


def create_profile(root, name, *, title=PRIVATE_TITLE):
    path = root / name
    path.mkdir()
    with closing(sqlite3.connect(path / 'History')) as db, db:
        db.executescript('''
            CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT, title TEXT, visit_count INTEGER, last_visit_time INTEGER);
            CREATE TABLE visits(id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER);
            CREATE INDEX visits_time ON visits(visit_time);
            CREATE TABLE downloads(path TEXT);
            CREATE TABLE keyword_search_terms(term TEXT);
            INSERT INTO downloads VALUES ('private download path');
            INSERT INTO keyword_search_terms VALUES ('private typed search');
        ''')
        add(db, 1, title)
    return path


@pytest.fixture
def install(tmp_path):
    root = tmp_path / 'chrome'
    root.mkdir()
    first = create_profile(root, 'Default')
    create_profile(root, 'Profile 2', title='aurora second profile')
    create_profile(root, 'Unlisted', title='aurora unlisted profile')
    (root / 'Local State').write_text(json.dumps({'profile': {'info_cache': {
        'Default': {'name': 'Personal', 'irrelevant': 'metadata'},
        'Profile 2': {'name': 'Work'},
        '..': {'name': 'invalid'},
    }}}))
    store = QuickStore(tmp_path / 'owner')
    return store, root, first, history.ChromeHistory(store, root)


def snapshot_root(store):
    return store.private / history.SNAPSHOTS


def test_listing_only_reads_local_state_and_each_grant_is_explicit(install, monkeypatch):
    store, root, _, reader = install
    opened = []
    open_regular = history._open_regular

    def track(parent, name):
        opened.append(name)
        return open_regular(parent, name)

    monkeypatch.setattr(history, '_open_regular', track)
    assert reader.settings() == {'profiles': [
        {'profile_dir': 'Default', 'display_name': 'Personal', 'granted': False},
        {'profile_dir': 'Profile 2', 'display_name': 'Work', 'granted': False},
    ]}
    assert opened == ['Local State']
    assert reader.search({'query': 'aurora'}) == {'rows': [], 'message': msg('chrome_history.no_grant')}
    with pytest.raises(history.HistoryError, match=msg('chrome_history.profile_required')):
        reader.grant('Unlisted')
    reader.grant('Default')
    [entry] = history.grants(store)
    assert set(entry) == {'profile_dir', 'display_name', 'device', 'inode', 'granted_at'}
    assert entry['inode'] == (root / 'Default').stat().st_ino
    assert set(opened) == {'Local State'}
    assert [row['title'] for row in reader.search({'query': 'aurora'})['rows']] == [PRIVATE_TITLE]
    reader.grant('Profile 2')
    assert {row['profile'] for row in reader.search({'query': 'aurora'})['rows']} == {'Personal', 'Work'}
    assert [row['title'] for row in reader.search({'query': 'aurora', 'profile': 'Work'})['rows']] == ['aurora second profile']
    assert [row['title'] for row in reader.search({'query': 'aurora', 'profile': 'Default'})['rows']] == [PRIVATE_TITLE]


def test_listing_validates_local_state_size_and_type(install, monkeypatch):
    _, root, _, _ = install
    monkeypatch.setattr(history, 'LOCAL_STATE_LIMIT', 16)
    assert history.list_profiles(root) == []
    (root / 'Local State').write_text('invalid json')
    assert history.list_profiles(root) == []


def test_local_settings_http_gate_and_no_conversation_grant(install):
    store, root, _, _ = install
    service = AgentService(store, chrome_root=root)
    handler = make_handler(service)
    session = store.local_session()

    def request(method, path, payload=None, *, headers=None, client='127.0.0.1', server='127.0.0.1'):
        body = json.dumps(payload).encode() if payload is not None else b''
        request = handler.__new__(handler)
        request.path = path
        request.rfile = io.BytesIO(body)
        request.client_address = (client, 1234)
        request.server = SimpleNamespace(server_address=(server, 8788), server_port=8788)
        request.headers = {'Host': 'localhost:8788', 'Cookie': 'agentos_session=' + session,
                           'Content-Type': 'application/json', 'Content-Length': str(len(body)), **(headers or {})}
        replies = []
        request.reply = lambda status, data: replies.append((status, data))
        getattr(request, 'do_' + method)()
        return replies[0]

    confirmed = {'profile_dir': 'Default', 'confirm': True}
    assert request('GET', SETTINGS_URL)[0] == 200
    assert request('GET', SETTINGS_URL, client='198.51.100.4')[0] == 403
    assert request('POST', SETTINGS_URL + '/grant', confirmed, headers={'X-Forwarded-For': '198.51.100.4'})[0] == 403
    assert request('POST', SETTINGS_URL + '/grant', confirmed, server='0.0.0.0')[0] == 403
    assert request('POST', SETTINGS_URL + '/grant', confirmed, headers={'Origin': 'https://elsewhere.test'})[0] == 403
    assert request('POST', SETTINGS_URL + '/grant', confirmed, headers={'Cookie': ''})[0] == 401
    assert request('POST', SETTINGS_URL + '/grant', {'profile_dir': 'Default'})[0] == 400
    for channel in ('web', 'telegram', 'slack', 'mattermost'):
        job_id = store.enqueue('Grant browsing history', channel, channel=channel)
        job = store.job(job_id)
        caps = Capabilities(store, None, {}, '', job_id, None, settings=service.settings_tools(job))
        with pytest.raises((ToolError, ValueError)):
            caps.execute('settings_change', {'category': 'privacy', 'setting': history.GRANTS_KEY,
                                             'value': 'Default', 'reason': 'owner asked'})
        assert history.grants(store) == []
    assert request('POST', SETTINGS_URL + '/grant', confirmed)[0] == 200
    assert history.grants(store)[0]['profile_dir'] == 'Default'
    assert request('POST', SETTINGS_URL + '/revoke', confirmed)[0] == 200
    assert history.grants(store) == []


@pytest.mark.parametrize('journal', ['delete', 'wal'])
def test_exclusive_writer_snapshot_replays_committed_rows_only(install, monkeypatch, journal):
    store, _, profile, reader = install
    reader.grant('Default')
    path = profile / 'History'
    connect = sqlite3.connect
    with closing(connect(path)) as writer:
        assert writer.execute('PRAGMA locking_mode=EXCLUSIVE').fetchone() == ('exclusive',)
        assert writer.execute(f'PRAGMA journal_mode={journal}').fetchone() == (journal,)
        add(writer, 2, title='aurora committed')
        writer.commit()
        writer.execute('PRAGMA cache_size=5')
        # Spill uncommitted pages so this exercises recovery and WAL commit boundaries.
        writer.execute('BEGIN')
        for i in range(3, 85):
            add(writer, i, title='aurora uncommitted ' + 'x' * 1800)
        suffix = '-wal' if journal == 'wal' else '-journal'
        assert (profile / ('History' + suffix)).stat().st_size > 512
        files = {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in profile.iterdir()}
        opened = []

        def snapshot_connection(database, *args, **kwargs):
            path = Path(database)
            assert path.parent.parent == snapshot_root(store)
            opened.append(path)
            return connect(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, 'connect', lambda database, *a, **kw:
                            connect(database, *a, **kw) if database == store.path else
                            snapshot_connection(database, *a, **kw))
        result = reader.search({'query': 'aurora'})
        assert {row['title'] for row in result['rows']} == {PRIVATE_TITLE, 'aurora committed'}
        assert len(opened) == 1
        assert files == {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in profile.iterdir()}
    assert list(snapshot_root(store).iterdir()) == []


def test_copy_detects_changes_and_stops_after_bounded_retries(install, monkeypatch):
    store, _, profile, reader = install
    reader.grant('Default')
    copy = history._copy_source
    attempts = []

    def mutate(source, directory, name, remaining):
        copied = copy(source, directory, name, remaining)
        attempts.append(name)
        path = profile / name
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
        return copied

    monkeypatch.setattr(history, '_copy_source', mutate)
    with pytest.raises(history.HistoryBusy, match=msg('chrome_history.busy')):
        reader.search({'query': 'aurora'})
    assert attempts == ['History'] * history.ATTEMPTS
    assert list(snapshot_root(store).iterdir()) == []


@pytest.mark.parametrize('case', ['ungranted', 'revoked', 'profile_symlink', 'profile_replaced',
                                  'history_symlink', 'journal_symlink', '..'])
def test_refused_before_creating_any_snapshot(install, case):
    store, root, profile, reader = install
    reader.grant('Default')
    selected = 'Default'
    if case == 'ungranted':
        selected = 'Profile 2'
    elif case == 'revoked':
        reader.revoke('Default')
    elif case in ('profile_symlink', 'profile_replaced'):
        original = profile.rename(root / 'original')
        if case == 'profile_symlink':
            profile.symlink_to(original, target_is_directory=True)
        else:
            create_profile(root, 'Default')
    elif case in ('history_symlink', 'journal_symlink'):
        target = profile / 'elsewhere'
        path = profile / ('History' if case == 'history_symlink' else 'History-journal')
        if path.exists():
            path.rename(target)
        path.symlink_to(target)
    else:
        selected = '..'
        with pytest.raises(history.HistoryError):
            reader.grant(selected)
    with pytest.raises(history.HistoryError):
        reader.search({'query': 'aurora', 'profile': selected})
    assert not snapshot_root(store).exists()


@pytest.mark.parametrize('fail', [False, True])
def test_snapshot_permissions_and_cleanup_even_if_query_raises(install, monkeypatch, fail):
    store, _, profile, reader = install
    reader.grant('Default')
    (profile / 'History-shm').write_bytes(b'never copied')
    execute = history._query_snapshot

    def observe(db, *filters):
        [path] = snapshot_root(store).iterdir()
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
        assert stat.S_IMODE((path / 'History').stat().st_mode) == 0o600
        assert not (path / 'History-shm').exists()
        if fail:
            raise RuntimeError('query interrupted')
        return execute(db, *filters)

    monkeypatch.setattr(history, '_query_snapshot', observe)
    if fail:
        with pytest.raises(RuntimeError, match='query interrupted'):
            reader.search({'query': 'aurora'})
    else:
        assert reader.search({'query': 'aurora'})['rows']
    assert list(snapshot_root(store).iterdir()) == []


def test_startup_sweeps_snapshot_directories_before_work_recovery(install, monkeypatch):
    store, root, _, _ = install
    service = AgentService(store, chrome_root=root)
    stale = snapshot_root(store) / ('1' * 32)
    stale.mkdir(parents=True)
    (stale / 'History').write_bytes(b'old snapshot')
    protected = stale.with_name('2' * 32)
    protected.symlink_to(root, target_is_directory=True)

    def stop_after_sweep():
        assert not stale.exists()
        assert protected.is_symlink()
        raise RuntimeError('stop before launching service')

    monkeypatch.setattr(service.a2a_peers, 'recover', stop_after_sweep)
    with pytest.raises(RuntimeError, match='stop before launching service'):
        service.start()


def test_query_bounds_chrome_visit_times_redaction_and_allowed_tables(install, monkeypatch):
    store, _, profile, reader = install
    reader.grant('Default')
    reader.grant('Profile 2')
    secret = 'privateStoredSecretFixture'
    token = 'ghp_fixturecredential123456'
    store.secret('model_key', secret)
    with closing(sqlite3.connect(profile / 'History')) as db, db:
        for i in range(2, 26):
            add(db, i, title=('%_ aurora ' + secret + ' x' * 150),
                url=f'https://private.test/path?token={token}&q=' + 'a' * 520,
                visit=MICROS + i * 1_000_000)
        db.execute('INSERT INTO visits(url, visit_time) VALUES (25, ?)', (MICROS,))
        db.execute('INSERT INTO visits(url, visit_time) VALUES (25, ?)', (MICROS + 1,))
    reads = []
    authorize = history._authorize_query

    def record(action, table, *args):
        if action == sqlite3.SQLITE_READ:
            reads.append(table)
        return authorize(action, table, *args)

    monkeypatch.setattr(history, '_authorize_query', record)
    for args in ({}, {'after': BASE}, {'after': BASE, 'before': BASE + history.WINDOW_SECONDS + 1}):
        with pytest.raises(history.HistoryError, match=msg('chrome_history.invalid_query')):
            reader.search(args)
    result = reader.search({'query': 'aurora %_', 'limit': 100})
    assert len(result['rows']) == 20
    assert sum(profile['row_count'] for profile in result['profiles']) == 20
    for row in result['rows']:
        assert len(row['url']) == 512 and len(row['title']) == 200
        assert token not in row['url'] and secret not in row['title']
        assert '[redacted]' in row['url'] and '[redacted]' in row['title']
    window = reader.search({'profile': 'Default', 'after': BASE, 'before': BASE + 1})
    assert [row['last_visit_time'] for row in window['rows']] == [
        '2023-11-14T22:13:45+00:00', '2023-11-14T22:13:20+00:00']
    assert {table for table in reads} == {'urls', 'visits'}


def test_owner_worker_retains_rows_privately_and_audits_the_actual_route(install):
    store, root, _, reader = install
    reader.grant('Default')
    received = []

    def transport(_url, body, *_args, **_kwargs):
        messages = body['messages']
        if any(message.get('role') == 'tool' for message in messages):
            received.extend(message['content'] for message in messages if message.get('role') == 'tool')
            return {'choices': [{'message': {'content': 'Found one earlier visit.'}}]}
        return {'choices': [{'message': {'content': None, 'tool_calls': [
            {'id': 'history-1', 'type': 'function', 'function': {
                'name': ACTION, 'arguments': json.dumps({'query': 'aurora', 'after': BASE, 'before': BASE + 60})}}
        ]}}]}

    service = AgentService(store, adapter=ModelAdapter(transport), chrome_root=root)
    config = {'provider': 'compatible', 'endpoint': 'https://model.example.test/v1', 'model': 'fixture-history'}
    store.secret('model_key', 'fixture-history-key')
    store.put('model', config)
    store.put('model_test', {'ok': True, 'tools_ok': True, 'time': 9_999_999_999,
                             'fingerprint': service.model_fingerprint(config)})
    job = store.enqueue('Find the earlier aurora visit', 'history-request', owner_typed=True)
    assert service.run_one()
    assert any(PRIVATE_TITLE in content and PRIVATE_URL in content for content in received)
    [event] = [row for row in store.task_events(job) if row['tool'] == ACTION and row['status'] == 'succeeded']
    assert event['trace']['evidence'] == {
        'profiles': [{'profile': 'Personal', 'row_count': 1}], 'query': 'aurora', 'after': BASE, 'before': BASE + 60}
    serialized = json.dumps(store.task_events(job), ensure_ascii=False)
    assert PRIVATE_URL not in serialized and PRIVATE_TITLE not in serialized
    audit = service.work_information_use(job)
    [entry] = [row for row in audit['used'] if row['category'] == 'history']
    assert 'Chrome 방문 기록 (Personal)' in entry['items'][0] and 'aurora' in entry['items'][0]
    assert any(worker['route'] == 'direct-api' and worker['model'] == 'fixture-history'
               for worker in audit['sent_to']['workers']), audit['sent_to']['workers']
    assert history.PROVENANCE in {source['category'] for source in store.outside_content_sources(job)}
    [row] = store.read_results(job)
    assert row['source_kind'] == 'owner-private'
    assert PRIVATE_URL in row['excerpt']
    assert store.find_read_pages('aurora') == []
    reader.revoke('Default')
    assert store.read_results(job) == [row]
    from personal_agent.owner_forget import OwnerForget
    with store.db() as db:
        message_id = db.execute("SELECT id FROM messages WHERE job_id=? AND role='assistant'", (job,)).fetchone()['id']
    OwnerForget(store).forget('local-owner', 'message:' + str(message_id))
    assert store.read_results(job) == []


def test_specialist_package_and_peer_cannot_use_history(install):
    store, _, _, reader = install
    reader.grant('Default')
    job = store.enqueue('fixture', 'scope')
    record = lambda *_: None
    child = Capabilities(store, None, {}, '', job, record, delegated=True, history_reader=reader.search)
    assert ACTION not in child.offered_tools()
    with pytest.raises(ToolError, match=msg('agent_runtime.tool_not_allowed')):
        child.execute(ACTION, {'query': 'aurora'})
    package = {'version': 1, 'id': 'external', 'tools': [
        {'id': 'aliased_history', 'host_action': ACTION, 'mode': 'read_only'}]}
    with pytest.raises(ValueError, match='reserved for owner Works'):
        manifests.validate(package)
    assert ACTION not in READ_TOOLS
    parent = Capabilities(store, None, {}, '', job, record, history_reader=reader.search)
    assert parent.execute(ACTION, {'query': 'aurora'})['rows']
    assert parent.evidence == []
    with pytest.raises(ToolError) as error:
        parent.execute('delegate_agent', {'agent_id': 'researcher', 'task': PRIVATE_TITLE})
    assert error.value.code == 'chrome_history_private'



def test_linux_uses_chromium_when_chrome_is_absent(install, tmp_path, monkeypatch):
    store, root, _, _ = install
    config = tmp_path / '.config'
    config.mkdir()
    root.rename(config / 'chromium')
    monkeypatch.setattr(history, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    reader = history.ChromeHistory(store)
    assert reader.roots() == (config / 'google-chrome', config / 'chromium')
    reader.grant('Default')
    assert reader.search({'query': 'aurora'})['rows'][0]['title'] == PRIVATE_TITLE


def test_trusted_local_bridge_forwards_history_to_the_service_only(install, monkeypatch):
    from personal_agent import cli_browser_relay
    from personal_agent.bounded_execution import AgentOSMcpTools, StrictIsolatedAgentOSMcpTools

    store, _, _, reader = install
    reader.grant('Default')
    job = store.enqueue('Read browser history', 'relay-fixture')
    # Use the real relay router and key check, with no socket or CLI process.
    server = SimpleNamespace(serve_forever=lambda **_: None, shutdown=lambda: None, server_close=lambda: None)
    monkeypatch.setattr(cli_browser_relay.socketserver, 'ThreadingUnixStreamServer', lambda *_: server)
    service = Capabilities(store, None, {}, '', job, None, history_reader=reader.search)
    relay = cli_browser_relay.BrowserRelay(AgentOSMcpTools(service))

    def forward(name, arguments):
        request = json.dumps({'key': relay._key, 'name': name, 'arguments': arguments})
        reply = relay._reply(request.encode())
        assert 'ok' in reply, reply
        return reply['ok']

    bridge = Capabilities(store, None, {}, '', job, None, history_reader=cli_browser_relay.RELAYED_HISTORY)
    facade = AgentOSMcpTools(bridge)
    facade.relay = SimpleNamespace(call=forward)
    try:
        assert ACTION in {tool['name'] for tool in facade.definitions()}
        assert ACTION not in {tool['name'] for tool in StrictIsolatedAgentOSMcpTools(bridge).definitions()}
        assert facade.call(ACTION, {'query': 'aurora'})['rows'][0]['title'] == PRIVATE_TITLE
        assert len(store.read_results(job)) == 1
    finally:
        relay.close()
