"""Condition checks: the AI's own code watches, and wakes the AI only on a hit (#972).

Design: ``docs/design/condition-checks-972.en.md``.  C16: the owner's AI writes the condition
as code and proves it runs; Still only runs that code on a short interval, bounds it and reports
it.  Nothing here knows what a check is for: no service, site or domain is named.

* A **check** is the exact arguments of one ``code_run`` the AI already ran in the same Work
  (code, hosts, secret sets, timeout), saved with a ``prepare`` window row together with the
  version of each set's library that run imported, an interval and a digest (``proven_check``).
* The **runner** (``ConditionChecks.run_due``, one thread, one check at a time across Still) runs
  a due check through the same ``CodeRunner`` sandbox and request decisions as ``code_run``,
  with ``approvals=None``: every request that is not a read is refused unsent, so a check never
  acts.  Nothing due costs one indexed query; a quiet run costs no model call.
* **Output protocol** (cron, Monitor): exit 0 with nothing on stdout is quiet; exit 0 with
  output is a hit; any other end is a failure.  The run reads and rewrites ``CHECK_STATE_FILE``
  (hold durations, edge triggering), kept encrypted between runs and bound to the check.
* A **hit wakes** the row's ordinary scheduled run (``Preparations.wake``), with the output as
  its handoff, charged to the shared daily budget, at most ``DAILY_WAKES`` a day.  Three failures
  in a row pause the check and wake the AI once with the failure; a set that is not ready or a
  forgotten library version pauses it at once and tells the owner.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from datetime import datetime, timedelta

from . import preparations as prep
from .code_run import MAX_SECONDS, CodeRunError, CodeRunner, _arguments, check_arguments_digest
from .messages import msg

LOG = logging.getLogger(__name__)

#: Owner decisions 2026-11-18 (#972): the shortest interval, the default, and wakes per check per owner-local day.
MIN_EVERY_SECONDS, DEFAULT_EVERY_SECONDS = 15, 60
MAX_EVERY_SECONDS = prep.MAX_EVERY_MINUTES * 60
DAILY_WAKES = 24
#: The Kubernetes probe default ``failureThreshold``.
FAILURES_BEFORE_PAUSE = 3
STATE_ENV = 'CHECK_STATE_FILE'
#: What a failure handoff and the owner's view keep of stderr: its tail.
DIAGNOSTIC_CHARS = 600
#: Per-day counts kept on a row for the owner's view and the line's periodic account.
KEPT_DAYS = 40
#: Why a check stopped running on its own (the owner's resume control restarts it).
PAUSED_FAILURES, PAUSED_NOT_READY, PAUSED_LIBRARY = 'failures', 'set-not-ready', 'library-forgotten'
#: The last run's result.
QUIET, HIT, FAILURE = 'quiet', 'hit', 'failure'
#: The notice that tells the owner a check paused itself (a preparation-blocked notice).
PAUSED_NOTICE_PREFIX = prep.BLOCKED_NOTICE_PREFIX + 'check:'
STATE_SWEEP_SECONDS = 300


def parse_check(value):
    """The ``check`` argument as a dict (a JSON object or its text), or a typed refusal."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if not isinstance(value, dict):
        raise prep.PreparationRefusal('invalid_check', '{"code", "hosts", "secrets", "timeout_seconds", "every_seconds"}')
    return value


def normalize(value, declared=lambda host: False):
    """``(code, hosts, secret sets, timeout, every_seconds)`` of one check, validated as ``code_run`` validates it."""
    args = parse_check(value)
    try:
        code, packages, hosts, names, timeout = _arguments(args, declared)
    except CodeRunError as exc:
        raise prep.PreparationRefusal('invalid_check', str(exc)) from None
    if packages:
        raise prep.PreparationRefusal('check_packages')
    every = args.get('every_seconds')
    every = DEFAULT_EVERY_SECONDS if every in (None, '') else prep._whole_number(every)
    if every is None or not MIN_EVERY_SECONDS <= every <= MAX_EVERY_SECONDS:
        raise prep.PreparationRefusal('invalid_check', f'every_seconds is a whole number from {MIN_EVERY_SECONDS} '
                                                       f'to {MAX_EVERY_SECONDS} (default {DEFAULT_EVERY_SECONDS}).')
    return code, hosts, names, timeout, every


def spec_digest(spec):
    """The check's identity in records and in the owner's view (C7): every field that decides what runs."""
    fields = {key: spec[key] for key in ('code', 'hosts', 'secrets', 'timeout_seconds', 'every_seconds', 'libraries')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def proof_of(store, work_id, digest):
    """The library versions a successful ``code_run`` of Work ``work_id`` with these arguments pinned, or None.

    Reuses the Work's own Evidence (``code_run.evidence`` keeps ``check_proof`` only for a run that
    ended on its own with exit 0, no packages, no state change and no approval asked).
    """
    with store.db() as db:
        rows = db.execute("SELECT detail FROM tool_events WHERE job_id=? AND status='succeeded'", (work_id,)).fetchall()
    for row in rows:
        try:
            detail = json.loads(row['detail'] or '{}')
        except (TypeError, ValueError):
            continue
        evidence = detail.get('evidence') if isinstance(detail, dict) else None
        proof = evidence.get('check_proof') if isinstance(evidence, dict) else None
        if (detail.get('host_action') == 'code_run' and isinstance(proof, dict) and proof.get('digest') == digest
                and isinstance(proof.get('libraries'), dict)):
            return dict(proof['libraries'])
    return None


def proven_check(store, work_id, value, declared=lambda host: False):
    """The check to save: ``value``'s arguments, proven by a ``code_run`` of this Work, with its pinned libraries.

    The run timeout is at most the interval and ``MAX_SECONDS`` (``_arguments`` already caps it), so
    a check never overlaps itself.
    """
    code, hosts, names, timeout, every = normalize(value, declared)
    digest = check_arguments_digest(code, hosts, names, timeout)
    libraries = proof_of(store, work_id, digest)
    if libraries is None:
        raise prep.PreparationRefusal('check_not_proven')
    spec = {'code': code, 'hosts': hosts, 'secrets': names, 'timeout_seconds': min(timeout, every, MAX_SECONDS),
            'every_seconds': every, 'libraries': libraries, 'proven_in': work_id}
    spec['digest'] = spec_digest(spec)
    return spec


def spec_of(row):
    try:
        spec = json.loads((row or {}).get('check_spec') or 'null')
    except (TypeError, ValueError):
        return None
    return spec if isinstance(spec, dict) else None


def status_of(row):
    try:
        status = json.loads((row or {}).get('check_status') or '{}')
    except (TypeError, ValueError):
        status = {}
    return status if isinstance(status, dict) else {}


def state_key(preparation_id):
    from .api_credentials import CHECK_STATE_PREFIX
    return CHECK_STATE_PREFIX + preparation_id


def handoff_text(detail, timezone_name):
    """What the woken run reads after its brief: the check's output (data, never instructions), or its failure."""
    at = prep.local_text(detail.get('at') or 0, timezone_name)
    if detail.get('kind') == HIT:
        return msg('checks.handoff_hit', at=at, check=str(detail.get('check') or '')[:16],
                   output=str(detail.get('output') or ''))
    return msg('checks.handoff_failure', at=at, check=str(detail.get('check') or '')[:16],
               failures=detail.get('failures') or FAILURES_BEFORE_PAUSE,
               exit_status='timeout' if detail.get('timed_out') else detail.get('exit_status'),
               diagnostic=str(detail.get('diagnostic') or ''))


class CheckRunner(CodeRunner):
    """One check run: ``code_run``'s sandbox and request decisions, with the check's own seams (#972).

    The differences from a Work's run: ``approvals`` is None (every request that is not a read is
    refused unsent), the libraries are the pinned versions, nothing is proposed, saved or counted on a
    library, no hint is given, and ``CHECK_STATE_FILE`` holds the check's own state.
    """

    HINTS = False

    def __init__(self, store, record_id, *, pinned, state_text='', **kwargs):
        kwargs.pop('approvals', None)
        super().__init__(store, record_id, approvals=None, **kwargs)
        self.pinned, self.state_text, self.state_out = pinned, state_text or '{}', None
        self._state_path = None

    def _libraries(self, names, proposed):
        from .procedures import module_name
        return {name: {'source': row['source'], 'row': row, 'proposed': False, 'module': module_name(name),
                       'report': f'.agentos-library-{module_name(name)}.json'}
                for name, row in self.pinned.items() if name in names}

    def _keep_libraries(self, libraries, workdir, outcome, session):
        return {}

    def _load_states(self, names, workdir, env):
        from pathlib import Path
        states = super()._load_states(names, workdir, env)
        self._state_path = Path(workdir, 'check-state.json')
        self._state_path.write_text(self.state_text, encoding='utf-8')
        env[STATE_ENV] = str(self._state_path)
        return states

    def _save_states(self, states):
        saved = super()._save_states(states)
        try:
            self.state_out = self._state_path.read_text(encoding='utf-8') if self._state_path and self._state_path.is_file() else ''
        except (OSError, UnicodeDecodeError):
            self.state_out = None
        return saved

    def _result(self, code, packages, hosts, outcome, requests, names=(), failed=None):
        from .api_credentials import STATE_PLAIN_KEY, STATE_REDACT_CHARS, _strings
        from .quickstart_store import QuickStore
        # The captured state is not saved until admission. Protect its new values now,
        # using the same state-value policy and literal redactor as stored credentials.
        try:
            state = json.loads(self.state_out or '{}')
        except ValueError:
            state = {}
        if isinstance(state, dict):
            state = {key: value for key, value in state.items() if key != STATE_PLAIN_KEY}
        values = _strings(state, STATE_REDACT_CHARS)
        values = sorted({*values, *(form for value in values for form in QuickStore.json_escaped_forms(value))},
                        key=len, reverse=True)
        outcome = {**outcome, **{key: QuickStore.redact_secret_spans(outcome.get(key), values, None)
                                for key in ('stdout', 'stderr')}}
        requests = [{**row, 'path': QuickStore.redact_secret_spans(row['path'], values, None)} for row in requests]
        return super()._result(code, packages, hosts, outcome, requests, names, failed)


class ConditionChecks:
    """The check runner of one service (#972): claim, run, then quiet, wake or count a failure."""

    def __init__(self, service, *, clock=None, runner=None):
        self.service = service
        self.clock = clock
        #: ``runner(store, record_id, pinned=..., state_text=..., interrupted=...)``: injectable for tests.
        self.runner = runner or (lambda store, record_id, **kwargs: CheckRunner(store, record_id, **kwargs))
        self.interrupted = None
        self._next_sweep = 0.0

    @property
    def store(self):
        return self.service.store

    @property
    def preparations(self):
        return self.service.preparations

    def now(self):
        return self.clock() if self.clock is not None else self.preparations.clock()

    # -- the owner-local day and per-row counts ------------------------------------------

    def _day(self, now, db=None):
        return self.preparations.budget_day(now, db=db)

    def _next_day_start(self, now, db=None):
        from .context_observations import ContextObservations, CONFIG_KEY
        tz = prep.zone(ContextObservations._normalize(self.store.config(CONFIG_KEY, db=db))['timezone'])
        local = datetime.fromtimestamp(now, tz)
        return datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=tz).timestamp()

    def _count(self, status, now, db, *, run=False, wake=False, hosts=()):
        day = self._day(now, db)
        days = status.setdefault('days', {})
        counts = days.setdefault(day, {'runs': 0, 'wakes': 0})
        if run:
            counts['runs'] += 1
        if wake:
            counts['wakes'] += 1
        if status.get('hosts_day') != day:
            status['hosts_day'], status['hosts_today'] = day, {}
        for host in hosts:
            status['hosts_today'][host] = status['hosts_today'].get(host, 0) + 1
        for old in sorted(days)[:-KEPT_DAYS]:
            days.pop(old)
        return counts

    def wakes_today(self, row, now, db=None):
        return status_of(row).get('days', {}).get(self._day(now, db), {}).get('wakes', 0)

    # -- the runner ------------------------------------------------------------------------

    def due(self, now):
        """The runner's one indexed query: the earliest due check of a scheduled row, or None."""
        with self.store.db() as db:
            row = db.execute("SELECT * FROM preparations WHERE state=? AND check_spec IS NOT NULL AND check_next_at<=? "
                             'ORDER BY check_next_at LIMIT 1', (prep.STATE_SCHEDULED, now)).fetchone()
        return dict(row) if row else None

    def run_due(self, now=None):
        """Run at most one due check; True when one was looked at.  Nothing due costs one query."""
        now = self.now() if now is None else now
        if now >= self._next_sweep:
            self._next_sweep = now + STATE_SWEEP_SECONDS
            self.sweep_states()
        row = self.due(now)
        if row is None:
            return False
        claimed = self._claim(row, now)
        if claimed is None:
            return True
        spec, pinned, problem = claimed
        if problem:
            self._pause(row, spec, problem, now)
            return True
        outcome = self._run(row, spec, pinned)
        self._commit(row, spec, outcome, self.now())
        return True

    def _claim(self, row, now):
        """Recheck the row, its line, its window and today's wakes before a run; ``(spec, pinned, problem)`` or None."""
        spec = spec_of(row)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM preparations WHERE id=?', (row['id'],)).fetchone()
            current = dict(current) if current else None
            if (not current or current['state'] != prep.STATE_SCHEDULED
                    or current['check_spec'] != row['check_spec'] or (current['check_next_at'] or 0) > now):
                return None
            if spec is None:
                # An unreadable saved check never runs; it is not retried every pass either.
                db.execute('UPDATE preparations SET check_next_at=? WHERE id=?', (now + DEFAULT_EVERY_SECONDS, current['id']))
                return None
            refusal = prep.line_refusal(db, current.get('line_id'))
            if refusal:
                # #798: a stopped line runs nothing, its check included; an unreadable brief stops the row.
                state = prep.STATE_CANCELLED if refusal == prep.LINE_STOPPED else prep.STATE_FAILED
                db.execute('UPDATE preparations SET state=?,last_outcome=?,updated_at=? WHERE id=? AND state=?',
                           (state, state, now, current['id'], prep.STATE_SCHEDULED))
                return None
            if now > current['until_at'] or int(current['run_count'] or 0) >= int(current['max_runs'] or 0):
                # The window ended (or its wakes are used): the check ends with its row.
                db.execute('UPDATE preparations SET state=?,updated_at=? WHERE id=? AND state=?',
                           (prep.STATE_EXPIRED, now, current['id'], prep.STATE_SCHEDULED))
                return None
            if self.wakes_today(current, now, db) >= DAILY_WAKES:
                # The day's wakes are used: no run until the next owner-local day.
                db.execute('UPDATE preparations SET check_next_at=? WHERE id=?', (self._next_day_start(now, db), current['id']))
                return None
            # A late check runs once, never once per missed slot; the next is due one interval after it ends.
            db.execute('UPDATE preparations SET check_next_at=? WHERE id=?', (now + spec['every_seconds'], current['id']))
        pinned, problem = self._pinned(spec)
        return spec, pinned, problem

    def _pinned(self, spec):
        """``({set: library row}, None)`` of the pinned versions, or ``({}, PAUSED_LIBRARY)`` when one is gone."""
        from .procedures import ProcedureLibrary
        ProcedureLibrary(self.store)
        pinned = {}
        with self.store.db() as db:
            for name, version in (spec.get('libraries') or {}).items():
                found = db.execute('SELECT * FROM procedure_versions WHERE id=? AND slot=?', (version, name)).fetchone()
                if not found or found['state'] == 'forgotten':
                    # Forgetting does what the owner asked: the check never switches to another version.
                    return {}, PAUSED_LIBRARY
                pinned[name] = ProcedureLibrary._row(found)
        return pinned, None

    def _run(self, row, spec, pinned):
        from .api_credentials import ApiCredentials
        state_text = ''
        try:
            state_text = ApiCredentials(self.store).read_bound_state(state_key(row['id']), spec['digest'])
        except Exception:
            state_text = ''
        runner = self.runner(self.store, 'check:' + row['id'], pinned=pinned, state_text=state_text,
                             interrupted=self.interrupted)
        args = {'code': spec['code'], 'hosts': spec['hosts'], 'secrets': spec['secrets'],
                'timeout_seconds': spec['timeout_seconds']}
        try:
            result = runner.run(args)
        except CodeRunError as exc:
            return {'error': exc.code, 'reason': str(exc)[:DIAGNOSTIC_CHARS], 'requires': exc.requires}
        except Exception as exc:  # noqa: BLE001 - an unexpected runner failure is a failed check run, never a stopped loop
            LOG.warning('condition check run failed kind=%s', type(exc).__name__)
            return {'error': 'code_run_failed', 'reason': ''}
        return {'result': result, 'state': getattr(runner, 'state_out', None)}

    def _commit(self, row, spec, outcome, now):
        """Settle one run: dropped when its row changed meanwhile, else quiet, a wake, or a counted failure."""
        result = outcome.get('result')
        if outcome.get('error') == 'needs_setup':
            self._pause(row, spec, PAUSED_NOT_READY, now)
            return
        if outcome.get('error') == 'code_run_interrupted':
            # Cut off by a stop: not counted, and its state was not written (D9).
            return
        hosts = [item['host'] for item in (result or {}).get('requests') or () if item.get('allowed') and item.get('phase') == 'run']
        ended = result is not None and not result.get('timed_out') and result.get('exit_status') is not None
        if result is not None and ended and result.get('exit_status') == 0 and not result.get('failed_phase'):
            kind = HIT if str(result.get('stdout') or '').strip() else QUIET
        else:
            kind = FAILURE
        destination = self._destination(row) if kind != QUIET else None
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM preparations WHERE id=?', (row['id'],)).fetchone()
            current = dict(current) if current else None
            if (not current or current['state'] != prep.STATE_SCHEDULED or current['check_spec'] != row['check_spec']
                    or current['updated_at'] != row['updated_at']):
                # D10: the row changed during the run (paused, cancelled, replaced, run now): its result is dropped.
                return
            status = status_of(current)
            if status.pop('paused', None):
                # The owner resumed a check that paused itself: it starts counting failures afresh.
                status['failures'] = 0
            self._count(status, now, db, run=True, hosts=hosts)
            status.update(last_run_at=now, last_result=kind)
            save_state = ended and outcome.get('state') is not None
            updates = {}
            if kind == QUIET:
                status['failures'] = 0
                status.pop('last_error', None)
                updates['check_next_at'] = now + spec['every_seconds']
            elif kind == HIT:
                status['failures'] = 0
                status.pop('last_error', None)
                # The woken Work's record names what the hit run reached (D7, information use).
                handoff = {'kind': HIT, 'at': now, 'check': spec['digest'][:16], 'output': result.get('stdout') or '',
                           'reached': sorted(set(hosts)), 'secrets': spec['secrets']}
                woken, reason = self._wake(db, current, status, now, handoff, destination, prep.STATE_SCHEDULED)
                # An edge-triggered check may have marked this hit reported in its state.
                # Keep its previous state on every non-admission so the next run retries it.
                save_state = save_state and bool(woken)
                updates['check_next_at'] = (now + spec['every_seconds'] if woken or reason == 'queue-full'
                                            else self._next_day_start(now, db))
                if reason == 'run-limit':
                    updates['state'] = prep.STATE_EXPIRED
            else:
                status['failures'] = int(status.get('failures') or 0) + 1
                diagnostic = (str((result or {}).get('stderr') or '') or str(outcome.get('reason') or ''))[-DIAGNOSTIC_CHARS:]
                status['last_error'] = {'exit_status': (result or {}).get('exit_status'),
                                        'timed_out': bool((result or {}).get('timed_out')),
                                        'code': outcome.get('error'), 'diagnostic': diagnostic}
                updates['check_next_at'] = now + spec['every_seconds']
                if status['failures'] >= FAILURES_BEFORE_PAUSE:
                    # D9: pause, and wake the AI once with the failure; it usually fixes its own code.
                    status['paused'] = PAUSED_FAILURES
                    handoff = {'kind': FAILURE, 'at': now, 'check': spec['digest'][:16], 'failures': status['failures'],
                               'exit_status': status['last_error']['exit_status'],
                               'timed_out': status['last_error']['timed_out'], 'diagnostic': diagnostic}
                    woken, _reason = self._wake(db, current, status, now, handoff, destination, prep.STATE_PAUSED)
                    if not woken:
                        updates['state'] = prep.STATE_PAUSED
                        self._queue_paused_notice(db, current, now)
            if save_state:
                self._write_state(current['id'], spec, outcome['state'])
            updates['check_status'] = json.dumps(status, sort_keys=True)
            if 'state' in updates:
                updates['updated_at'] = max(now, math.nextafter(current['updated_at'], math.inf))
            db.execute(f"UPDATE preparations SET {','.join(key + '=?' for key in updates)} WHERE id=?",
                       (*updates.values(), current['id']))

    def _wake(self, db, row, status, now, handoff, destination, return_state):
        """Wake the row's run unless today's wakes are used; counts the wake on ``status``."""
        if self.wakes_today({'check_status': json.dumps(status)}, now, db) >= DAILY_WAKES:
            status['wake_refused'] = {'at': now, 'reason': 'daily-wakes'}
            return None, 'daily-wakes'
        from .agent_runtime import result_outside_source
        mark = result_outside_source('code_run')
        if mark is not None:
            mark = {**mark, 'check': handoff['check']}
        channel, chat_id = destination or ('web', None)
        work_id, reason = self.preparations.wake(db, row, at=now, channel=channel, chat_id=chat_id, now=now,
                                                 handoff=handoff, mark=mark, return_state=return_state)
        if work_id:
            self._count(status, now, db, wake=True)
            status['last_wake_at'], status['last_wake_work'] = now, work_id
            status.pop('wake_refused', None)
            LOG.info('condition check woke id=%s work=%s kind=%s', row['id'], work_id, handoff['kind'])
        else:
            status['wake_refused'] = {'at': now, 'reason': reason}
        return work_id, reason

    def _destination(self, row):
        """Where the woken run's reply goes: as the tick's own start chooses it (a paired surface, else kept in Still)."""
        surface = next((item for item in self.service.surfaces if item.name == row['channel']), None)
        pairing = surface.pairing() if surface is not None else None
        if pairing is None or row.get('delivery_mode') == prep.DELIVERY_WHEN_NEEDED:
            return 'web', None
        return pairing.channel, pairing.owner

    def _pause(self, row, spec, reason, now):
        """Pause a check that cannot run as saved (a set not ready, a forgotten library) and tell the owner."""
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM preparations WHERE id=?', (row['id'],)).fetchone()
            current = dict(current) if current else None
            if not current or current['state'] != prep.STATE_SCHEDULED or current['check_spec'] != row['check_spec']:
                return
            status = status_of(current)
            status.update(paused=reason, last_run_at=now, last_result=FAILURE)
            db.execute('UPDATE preparations SET state=?,updated_at=?,check_status=? WHERE id=?',
                       (prep.STATE_PAUSED, max(now, math.nextafter(current['updated_at'], math.inf)),
                        json.dumps(status, sort_keys=True), current['id']))
            self._queue_paused_notice(db, current, now)

    def _queue_paused_notice(self, db, row, now):
        # The existing preparation-blocked notice family (``status_notice_text``); one per pause.
        self.preparations._queue_status_notice(db, row, f"check:{row['id']}:{int(now)}",
                                               PAUSED_NOTICE_PREFIX + str(int(now)), now)

    def _write_state(self, preparation_id, spec, text):
        from .api_credentials import ApiCredentials
        try:
            credentials = ApiCredentials(self.store)
            if text == credentials.read_bound_state(state_key(preparation_id), spec['digest']):
                return
            credentials.write_bound_state(state_key(preparation_id), spec['digest'], text)
        except Exception as exc:  # noqa: BLE001 - an unsaved state is the next run's empty state, never a stopped loop
            LOG.warning('condition check state not saved kind=%s', type(exc).__name__)

    def sweep_states(self):
        """Discard the saved state of a check whose row ended or whose check was replaced (D4)."""
        from .api_credentials import ApiCredentials, CHECK_STATE_PREFIX
        reader = getattr(self.store, 'secret_items', None)
        if not callable(reader):
            return 0
        try:
            keys = list(reader(CHECK_STATE_PREFIX))
        except Exception:
            return 0
        removed = 0
        for key in keys:
            preparation_id = key[len(CHECK_STATE_PREFIX):]
            row = self.preparations.get(preparation_id)
            if row and row['state'] in prep.ACTIVE_STATES and spec_of(row):
                continue
            try:
                ApiCredentials(self.store).discard_bound_state(key)
                removed += 1
            except Exception:
                continue
        return removed

    # -- the woken run's own replacement (D6) -----------------------------------------------

    def replace_own(self, job, value, declared=lambda host: False):
        """Replace the check of the preparation ``job`` is the current run of, within its sets and hosts (D6).

        The new check is proven in this run as at setup.  A replacement never adds a secret set or a
        host.  It takes effect when this run settles (the row resumes at once if a failure paused it).
        """
        preparation_id = prep.preparation_of((job or {}).get('request_key'))
        row = self.preparations.get(preparation_id) if preparation_id else None
        if not (row and self.preparations.owns_running(row, job) and spec_of(row)):
            raise prep.PreparationRefusal('check_not_own')
        before = spec_of(row)
        spec = proven_check(self.store, job['id'], value, declared)
        if set(spec['secrets']) - set(before['secrets']) or set(spec['hosts']) - set(before['hosts']):
            raise prep.PreparationRefusal('check_widened')
        now = self.now()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM preparations WHERE id=?', (row['id'],)).fetchone()
            if not (current and self.preparations.owns_running(dict(current), job)
                    and current['check_spec'] == row['check_spec']):
                raise prep.PreparationRefusal('check_not_own')
            status = status_of(dict(current))
            paused = status.pop('paused', None)
            status['failures'] = 0
            status.pop('last_error', None)
            status['replaced'] = {'at': now, 'work_id': job['id'], 'from': before['digest'][:16], 'to': spec['digest'][:16]}
            updates = {'check_spec': json.dumps(spec, sort_keys=True), 'check_next_at': now,
                       'check_status': json.dumps(status, sort_keys=True)}
            if paused == PAUSED_FAILURES and current['run_return_state'] == prep.STATE_PAUSED:
                # A fix of the check that paused itself: it runs again once this run settles.
                updates['run_return_state'] = prep.STATE_SCHEDULED
            db.execute(f"UPDATE preparations SET {','.join(key + '=?' for key in updates)} WHERE id=?",
                       (*updates.values(), current['id']))
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job['id'], prep.CHECK_EVENT_TOOL, 'replaced',
                        json.dumps({'preparation_id': row['id'], 'from': before['digest'], 'to': spec['digest'],
                                    'hosts': spec['hosts'], 'secrets': spec['secrets'],
                                    'every_seconds': spec['every_seconds']}), now))
        try:
            from .api_credentials import ApiCredentials
            ApiCredentials(self.store).discard_bound_state(state_key(row['id']))
        except Exception:
            pass
        return {'preparation_id': row['id'], 'check_replaced': True, 'check': spec['digest'][:16],
                'every_seconds': spec['every_seconds'], 'hosts': spec['hosts'], 'secrets': spec['secrets'],
                'next_step': msg('checks.replaced_next_step')}

    # -- what the owner and the woken run read ---------------------------------------------

    def handoff(self, job):
        """The woken run's handoff event detail for ``job`` (or the run it continues), or None."""
        key = prep.slot_key((job or {}).get('request_key'))
        if not key.startswith(prep.REQUEST_KEY_PREFIX):
            return None
        with self.store.db() as db:
            found = db.execute("SELECT e.detail FROM tool_events e JOIN jobs j ON j.id=e.job_id "
                               "WHERE j.request_key=? AND e.tool=? AND e.status='woken' ORDER BY e.id LIMIT 1",
                               (key, prep.CHECK_EVENT_TOOL)).fetchone()
        return json.loads(found['detail']) if found else None

    def summary(self, row, now=None, lang='ko'):
        """The owner's view of one row's check (D7), or None for a row without one."""
        spec = spec_of(row)
        if spec is None:
            return None
        now = self.now() if now is None else now
        status = status_of(row)
        day = self._day(now)
        counts = status.get('days', {}).get(day, {'runs': 0, 'wakes': 0})
        hosts = status.get('hosts_today', {}) if status.get('hosts_day') == day else {}
        last = status.get('last_result')
        parts = [msg('checks.summary', lang, every=spec['every_seconds'], until=prep.local_text(row['until_at'], row['timezone']),
                     runs=counts['runs'], wakes=counts['wakes'], maximum=DAILY_WAKES,
                     hosts=', '.join(f'{host} {count}' for host, count in sorted(hosts.items())) or '-',
                     secrets=', '.join(spec['secrets']) or '-')]
        if last:
            parts.append(msg('checks.last_' + last, lang, at=prep.local_text(status['last_run_at'], row['timezone'])))
        if status.get('paused'):
            parts.append(msg('checks.paused_' + status['paused'].replace('-', '_'), lang))
        return ' · '.join(parts)

    def view(self, row, now=None):
        """The Settings entry of one row's check: its text in each language, and its code, read-only."""
        from .messages import LANGUAGES
        spec = spec_of(row)
        if spec is None:
            return None
        return {'text': {lang: self.summary(row, now, lang) for lang in LANGUAGES}, 'code': spec['code'],
                'every_seconds': spec['every_seconds'], 'hosts': spec['hosts'], 'secrets': spec['secrets'],
                'digest': spec['digest'][:16]}

    def counts_since(self, row, since):
        """``(runs, wakes)`` of a row's check on the owner-local days from ``since`` (the line's account, D7)."""
        if spec_of(row) is None:
            return None
        start = self._day(since) if since else ''
        days = status_of(row).get('days', {})
        runs = sum(item.get('runs', 0) for day, item in days.items() if day >= start)
        wakes = sum(item.get('wakes', 0) for day, item in days.items() if day >= start)
        return runs, wakes

    def run_fact(self, row):
        """The woken run's own line about its check (``preparation_run_fact``)."""
        spec = spec_of(row)
        return msg('checks.run_fact', every=spec['every_seconds'], until=prep.local_text(row['until_at'], row['timezone']),
                   wakes=int(row['run_count'] or 0), maximum=row['max_runs'], daily=DAILY_WAKES, id=row['id'])


def loop(checks, stop, interval=1.0):
    """The check thread (one, store-wide): one due check at a time; a failed pass never ends the thread."""
    checks.interrupted = stop.is_set
    while not stop.is_set():
        try:
            busy = checks.run_due()
        except Exception as exc:  # noqa: BLE001 - one bad row never stops every other check
            LOG.warning('condition check pass failed kind=%s', type(exc).__name__)
            busy = False
        if not busy:
            stop.wait(interval)
