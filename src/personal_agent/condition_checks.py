"""Run proven preparation checks in the existing code sandbox, independently of Work lanes (#972)."""
from __future__ import annotations

from datetime import datetime, timedelta
import fcntl
import hashlib
import json
import logging
from pathlib import Path
import time

from . import preparations as prep
from .api_credentials import ApiCredentials, CredentialError, secret_state_values
from .code_run import CodeRunError, CodeRunner, _arguments, check_arguments_digest
from .current_context import redact_known_secrets
from .direct_message_surface import redact_provenance_values
from .messages import msg

LOG = logging.getLogger(__name__)

MIN_INTERVAL, DEFAULT_INTERVAL = 15, 60
DAILY_WAKES, FAILURE_LIMIT = 24, 3
#: How often states of checks that ended are discarded (D4: discarded when the check is replaced or ends).
SWEEP_SECONDS = 60
#: D4: the check's own state between its runs, for hold durations and edge triggering in the AI's code.
STATE_ENV = 'CHECK_STATE_FILE'
PAUSED_NOTICE = prep.BLOCKED_NOTICE_PREFIX + 'check:'


def _redact(store, value):
    return redact_provenance_values(value, lambda item: redact_known_secrets(store, item))


def parse(value, declared):
    try:
        args = json.loads(value) if isinstance(value, str) else value
        if not isinstance(args, dict) or set(args) - {'code', 'hosts', 'secrets', 'timeout_seconds', 'every_seconds'}:
            raise ValueError
        code, _, hosts, names, timeout = _arguments(args, declared)
        interval = args.get('every_seconds')
        interval = DEFAULT_INTERVAL if interval in (None, '') else prep._whole_number(interval)
        if interval is None or not MIN_INTERVAL <= interval <= prep.MAX_EVERY_MINUTES * 60:
            raise ValueError
    except (CodeRunError, ValueError, TypeError, OverflowError):
        raise prep.PreparationRefusal('invalid_check') from None
    return dict(code=code, hosts=hosts, secrets=names, timeout_seconds=timeout), interval


def prove(store, work_id, args, interval):
    """Bind the same Work's successful call and the libraries it actually loaded."""
    if _redact(store, args) != args:
        raise prep.PreparationRefusal('check_credential')
    expected = check_arguments_digest(args['code'], args['hosts'], args['secrets'], args['timeout_seconds'])
    for event in reversed(store.task_events(work_id)):
        detail = event['trace']
        if event['status'] != 'succeeded' or detail.get('host_action') != 'code_run':
            continue
        proof = (detail.get('evidence') or {}).get('check_proof') or {}
        if proof.get('arguments') == expected:
            spec = {'arguments': args, 'every_seconds': interval, 'libraries': proof['libraries']}
            spec['digest'] = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
            return spec
    raise prep.PreparationRefusal('check_not_proven')


def owner_view(store, row):
    spec = json.loads(row['check_spec'])
    status = json.loads(row.get('check_status') or '{}')
    status.pop('pending', None)
    return _redact(store, {
        'code': spec['arguments']['code'], 'hosts': spec['arguments']['hosts'],
        'secrets': spec['arguments']['secrets'], 'every_seconds': spec['every_seconds'],
        'digest': spec['digest'], 'status': status,
    })


def handoff_event(store, job):
    key = prep.slot_key((job or {}).get('request_key'))
    if not key.startswith(prep.REQUEST_KEY_PREFIX) or '~wake-' not in key:
        return None
    with store.db() as db:
        event = db.execute('SELECT e.* FROM tool_events e JOIN jobs j ON j.id=e.job_id '
                           "WHERE j.request_key=? AND e.tool=? AND e.status='woken'",
                           (key, prep.CHECK_EVENT_TOOL)).fetchone()
    return {**dict(event), 'trace': json.loads(event['detail'])} if event else None


def handoff_text(store, handoff, timezone):
    kind = handoff['kind']
    hosts = ', '.join(handoff.get('allowed_hosts') or ()) or '-'
    sets = ', '.join(handoff.get('allowed_secret_sets', handoff.get('secrets')) or ()) or '-'
    return msg('checks.handoff', kind=kind, at=prep.local_text(handoff['at'], timezone),
               digest=handoff['check'], hosts=redact_known_secrets(store, hosts),
               secrets=redact_known_secrets(store, sets), output=redact_known_secrets(store, handoff['output']))


class CheckRunner(CodeRunner):
    """Use CodeRunner's normal request policy with a previously loaded library revision."""

    library_hints = False
    persist_states = False  # A check's writes are scratch data.
    withhold_truncated_output = True

    def __init__(self, store, record_id, *, pinned, check_state=None, **kwargs):
        super().__init__(store, record_id, approvals=None, **kwargs)
        self.pinned = pinned
        # (check id, check digest): the state is bound to the exact check code (D4).
        self.check_state = check_state
        self.check_state_saved = None
        self._check_file = None

    def _load_states(self, names, workdir, env):
        states = super()._load_states(names, workdir, env)
        if self.check_state:
            original = ApiCredentials(self.store).read_check_state(*self.check_state) or '{}'
            path = Path(workdir, 'check-state.json')
            path.write_text(original, encoding='utf-8')
            env[STATE_ENV] = str(path)
            self._check_file = (path, original)
        return states

    def _save_states(self, states):
        saved, scratch_secrets = super()._save_states(states)
        if self._check_file:
            path, original = self._check_file
            self._check_file = None
            try:
                text = path.read_text(encoding='utf-8') if path.is_file() else ''
            except (OSError, UnicodeDecodeError):
                text = None
            try:
                # The values this run wrote are redacted from its own output before the store's pass sees them.
                scratch_secrets.update(secret_state_values(text))
            except ValueError:
                pass
            if text != original:
                self.check_state_saved = text is not None and ApiCredentials(self.store).write_check_state(
                    *self.check_state, text)
        return saved, scratch_secrets

    def _libraries(self, names, proposed):
        from .procedures import module_name
        return {name: {'source': row['source'], 'row': row, 'module': module_name(name),
                       'report': f'.agentos-library-{module_name(name)}.json'}
                for name, row in self.pinned.items()}

    def _keep_libraries(self, libraries, workdir, outcome, session):
        return {}


class ConditionChecks:
    def __init__(self, service, runner=CheckRunner):
        self.service = service
        self.store = service.store
        self.preparations = service.preparations
        self.runner = runner

    def loop(self):
        stop = self.service.stop
        swept = None
        while not stop.is_set():
            try:
                self.run_due()
                if swept is None or time.monotonic() - swept >= SWEEP_SECONDS:
                    swept = time.monotonic()
                    self.sweep_states()
            except Exception as exc:  # Keep the scheduler available without logging payloads.
                LOG.warning('condition check tick failed kind=%s', type(exc).__name__)
            stop.wait(1)

    def sweep_states(self):
        """Discard the state of every check whose row ended, was forgotten or no longer has a check (D4, D10).

        Runs outside any SQLite transaction: discarding reads the owner-held key to retire the values.
        A state that cannot be read now is kept and tried again on the next sweep, never dropped unretired.
        """
        credentials = ApiCredentials(self.store)
        ids = credentials.check_state_ids()
        if not ids:
            return 0
        with self.store.db() as db:
            live = {row['id'] for row in db.execute(
                f"SELECT id FROM preparations WHERE id IN ({','.join('?' * len(ids))}) AND check_spec IS NOT NULL "
                f"AND state IN ({','.join('?' * len(prep.ACTIVE_STATES))})", (*ids, *prep.ACTIVE_STATES))}
        dropped = 0
        for check_id in ids:
            if check_id in live:
                continue
            try:
                dropped += bool(credentials.discard_check_state(check_id))
            except Exception:
                continue
        return dropped

    def _due(self, db, now):
        row = db.execute("SELECT * FROM preparations WHERE state='scheduled' AND check_spec IS NOT NULL "
                         'AND check_next_at<=? ORDER BY check_next_at,id LIMIT 1', (now,)).fetchone()
        return dict(row) if row else None

    def _next_day(self, now, db):
        zone = self.preparations._owner_zone(db)
        day = datetime.fromtimestamp(now, zone).date() + timedelta(days=1)
        return datetime.combine(day, datetime.min.time(), tzinfo=zone).timestamp()

    def run_due(self):
        now = self.preparations.clock()
        with self.store.db() as db:
            if self._due(db, now) is None:
                return False
        # The file lock is store-wide and released on process exit. SQLite has no lock
        # held during execution, so owner actions and Work lanes continue independently.
        with (self.store.private / 'condition-check.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return self._run_due()

    def _run_due(self):
        now = self.preparations.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._due(db, now)
            if row is None:
                return False
            day = self.preparations.budget_day(now, db)
            status = json.loads(row['check_status'] or '{}')
            refusal = prep.line_refusal(db, row['line_id'])
            ended = now >= row['until_at'] or row['run_count'] >= row['max_runs']
            claimed_at = row['check_next_at']
            if not (refusal or ended):
                if status.get('day') == day and status['wakes'] >= DAILY_WAKES:
                    self._advance(db, row, self._next_day(now, db), status)
                    return True
                # Keep a durable floor even if the process stops or result settling fails.
                claimed_at = min(now + row['every_seconds'], row['until_at'])
                db.execute('UPDATE preparations SET check_next_at=? WHERE id=?', (claimed_at, row['id']))
        result = None
        if not (refusal or ended):
            try:
                result = self._run(row, json.loads(row['check_spec']), status.get('pending'))
            except CredentialError:
                # An unavailable key can prevent even the redactor from running.
                result = {'pause': 'setup_missing'}
        self._settle(row, result, claimed_at=claimed_at)
        return True

    def _run(self, row, spec, pending=None):
        from .agent_runtime import outside_result_source
        from .procedures import ProcedureLibrary, digest

        args = spec['arguments']
        if spec.get('scrubbed'):
            return {'pause': 'credential'}
        cleaned = _redact(self.store, args)
        if cleaned != args:
            scrubbed = {**spec, 'arguments': cleaned, 'libraries': {}, 'scrubbed': True}
            return {'pause': 'credential', 'scrubbed_spec': scrubbed}
        pinned = {}
        if spec['libraries']:
            with self.store.db() as db:
                for name, version in spec['libraries'].items():
                    found = db.execute('SELECT * FROM procedure_versions WHERE id=? AND slot=? AND state!=?',
                                       (version['id'], name, 'forgotten')).fetchone()
                    if not found or found['digest'] != version['digest'] or digest(found['source']) != version['digest']:
                        return {'pause': 'library'}
                    pinned[name] = ProcedureLibrary._row(found)
        if pending:
            return _redact(self.store, pending['result'])
        runner = self.runner(self.store, 'check:' + row['id'], pinned=pinned, interrupted=self.service.stop.is_set,
                             check_state=(row['id'], spec['digest']))
        failure_reason = None
        try:
            result = runner.run(args, seconds=spec['every_seconds'])
            outside = outside_result_source('code_run', 'code_run', args)
            if result.get('exit_status') == 0 and not result.get('timed_out') and not result.get('failed_phase'):
                output = result.get('stdout') or ''
                kind = 'hit' if output else 'quiet'
            else:
                kind = 'failure'
                failure_reason = 'timeout' if result.get('timed_out') else f"exit {result.get('exit_status')}"
                output = result.get('stderr') or ''
            if getattr(runner, 'check_state_saved', None) is False:
                # A state the next run would not see is the check's own defect, for its AI to fix (D4, D9):
                # a hit still wakes with the note, and a quiet run counts as a failure.
                note = msg('checks.state_refused')
                kind = 'hit' if kind == 'hit' else 'failure'
                output = f'{output}\n{note}' if output else note
        except CodeRunError as exc:
            if exc.code == 'needs_setup':
                return {'pause': 'setup_missing'}
            if exc.code == 'code_run_interrupted':
                return None
            outside = outside_result_source('code_run', 'code_run', args, dispatched_error=True) if runner.started else None
            kind, output = 'failure', str(exc)
        except CredentialError:
            raise
        except Exception as exc:
            LOG.warning('condition check run failed kind=%s', type(exc).__name__)
            outside = outside_result_source('code_run', 'code_run', args, dispatched_error=True) if runner.started else None
            kind, output = 'failure', msg('checks.failed')

        # The sandbox proxy records allow decisions before transport. Do not infer contact.
        handoff = _redact(self.store, {
            'kind': kind, 'check': spec['digest'], 'at': self.preparations.clock(),
            'output': output, 'allowed_hosts': sorted(runner.allowed_hosts),
            'allowed_secret_sets': args['secrets'], 'outside': outside,
        })
        if kind == 'failure':
            tail = handoff['output'][-600:]
            handoff['output'] = '\n'.join(part for part in (failure_reason, tail) if part)
        return handoff

    @staticmethod
    def _advance(db, row, next_at, status):
        db.execute('UPDATE preparations SET check_next_at=?, check_status=? WHERE id=?',
                   (min(next_at, row['until_at']), json.dumps(status), row['id']))

    def _settle(self, row, result=None, *, claimed_at=None):
        preparations = self.preparations
        # All secret-dependent rendering and pairing reads happen before the write transaction.
        try:
            title = redact_known_secrets(self.store, prep.owner_title(row))
        except CredentialError:
            title = row['id']
            if not (result and result.get('scrubbed_spec')):
                result = {'pause': 'setup_missing'}
        status_destination = self.service.preparations_service.notice_destination(preparations.notice_channel(row))
        surface = next((item for item in self.service.surfaces if item.name == row['channel']), None)
        can_wake = result and result.get('kind') in ('hit', 'failure')
        pairing = surface.pairing() if can_wake and surface and row['delivery_mode'] != prep.DELIVERY_WHEN_NEEDED else None
        budget_destinations = self.service.preparations_service.budget_notice_destinations() if can_wake else ()
        expected_due = row['check_next_at'] if claimed_at is None else claimed_at
        now = preparations.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM preparations WHERE id=?', (row['id'],)).fetchone()
            if (not current or current['state'] != prep.STATE_SCHEDULED or current['check_next_at'] != expected_due
                    or current['updated_at'] != row['updated_at'] or current['check_spec'] != row['check_spec']):
                return
            refusal = prep.line_refusal(db, row['line_id'])
            if refusal:
                state = prep.STATE_CANCELLED if refusal == prep.LINE_STOPPED else prep.STATE_FAILED
                db.execute('UPDATE preparations SET state=?,last_outcome=?,updated_at=? WHERE id=?',
                           (state, state, now, row['id']))
                return
            if now >= row['until_at'] or row['run_count'] >= row['max_runs']:
                status = json.loads(row['check_status'] or '{}')
                if status.pop('pending', None):
                    self._advance(db, row, row['until_at'], status)
                db.execute('UPDATE preparations SET state=?,updated_at=? WHERE id=?', (prep.STATE_EXPIRED, now, row['id']))
                preparations._queue_status_notice(db, row, prep.request_key(row['id'], row['until_at']),
                                                  prep.EXPIRED_NOTICE_KIND, now, destination=status_destination)
                return
            if result is None:
                return
            status = json.loads(row['check_status'] or '{}')
            pending = status.pop('pending', None)
            if status.pop('paused', None):
                status['failures'] = 0
            day = preparations.budget_day(now, db)
            if day != status.get('day'):
                status.update(day=day, runs=0, wakes=0)
            next_at = now + row['every_seconds']
            pause = result.get('pause')
            kind = result.get('kind', 'failure')
            if not pending or pause:
                status['runs'] += 1
                status.update(last_result=kind, last_run_at=now,
                              failures=status.get('failures', 0) + 1 if kind == 'failure' else 0)
            failed = status['failures'] >= FAILURE_LIMIT
            if not pause and (kind == 'hit' or failed):
                slot = pending['slot'] if pending else row['check_next_at']
                wake = {'handoff': {key: value for key, value in result.items() if key != 'outside'},
                        'outside': result['outside'], 'slot': slot, 'title': title,
                        'budget_destinations': budget_destinations, 'return_state': prep.STATE_PAUSED if failed else None}
                if status['wakes'] < DAILY_WAKES and preparations.start(
                        row, channel=pairing.channel if pairing else 'web', chat_id=pairing.owner if pairing else None,
                        now=now, db=db, wake=wake):
                    status['wakes'] += 1
                    if failed:
                        status['paused'] = 'failures'
                elif failed:
                    pause = 'failures'
                elif wake.get('queue_refused'):
                    status['pending'] = {'slot': slot, 'result': result}
                    next_at = now + 1
                elif wake.get('budget_refused') or status['wakes'] >= DAILY_WAKES:
                    if pending:
                        status['pending'] = {'slot': slot, 'result': result}
                    next_at = self._next_day(now, db)
            if pause:
                status['paused'] = pause
                scrubbed = result.get('scrubbed_spec')
                spec = json.dumps(scrubbed, sort_keys=True) if scrubbed else row['check_spec']
                db.execute('UPDATE preparations SET state=?,updated_at=?,check_spec=? WHERE id=?',
                           (prep.STATE_PAUSED, now, spec, row['id']))
                self._notice(db, row, title, pause, now, status_destination)
            self._advance(db, row, next_at, status)

    def _notice(self, db, row, title, pause, now, destination):
        notice = f"check:{row['id']}:{row['check_next_at']}"
        message = msg('checks.paused', title=title, reason=msg('checks.' + pause))
        db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                   ('assistant', message, 'web', now, notice))
        self.preparations._queue_status_notice(db, row, notice, PAUSED_NOTICE + str(row['check_next_at']),
                                               now, destination=destination)
