"""Decision records and outcome-backed working lessons for entrusted lines (#1284).

The run reports the outcome and proposes the lesson. Still stores the reports
and checks their citations. Lesson content and undo remain in canonical Memory.
"""

import json
import math
import re
import uuid
from datetime import datetime, timezone

from . import owner_model as om
from .entrusted_lines import LineRefusal
from .messages import msg

PENDING, SETTLED, UNSETTLEABLE = 'pending', 'settled', 'unsettleable'
MATCHED = ('yes', 'no', 'unclear')
MAX_RUN_DECISIONS = 10
MAX_LINE_DECISIONS = 100
SETTLE_ATTEMPTS = 3
SUBJECT_CHARS, CLAIM_CHARS, EXPECTATION_CHARS = 120, 600, 300
CHECK_CHARS, OUTCOME_CHARS, REASON_CHARS = 600, 600, 300
ADD_STRENGTH = 2
RECALL_DUE, RECALL_SETTLED, RECALL_CROSS, RECALL_CHARS = 5, 5, 3, 3000
ORIGIN_LINE_OUTCOMES = 'line-outcomes'

DDL = '''
CREATE TABLE IF NOT EXISTS line_decisions(
  id TEXT PRIMARY KEY, line_id TEXT NOT NULL, work_id TEXT NOT NULL,
  made_at REAL NOT NULL, subject TEXT NOT NULL, claim TEXT NOT NULL, expectation TEXT NOT NULL,
  settle_after REAL NOT NULL, check_plan TEXT NOT NULL, lessons_applied TEXT NOT NULL,
  state TEXT NOT NULL, outcome TEXT, matched TEXT, resolved_at REAL, settled_at REAL,
  settle_work_id TEXT, reason TEXT, attempts INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS line_decisions_state ON line_decisions(line_id, state, made_at);
CREATE TABLE IF NOT EXISTS lesson_strength(
  lesson_key TEXT PRIMARY KEY, strength INTEGER NOT NULL, line_id TEXT NOT NULL,
  decisions TEXT NOT NULL, updated REAL NOT NULL);
'''


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _refusal(code, key, **values):
    """A refusal whose text names the field and the rule, so one retry can fix the call (#1309)."""
    return LineRefusal(code, message='lines.refused_' + key, values=values)


def _invalid(key, **values):
    return _refusal('line_decision_invalid', key, **values)


def _required(action, fields):
    missing = [name for name, value in fields if not value]
    if missing:
        raise _invalid('decision_required', action=action, fields=', '.join(missing))


def _iso(stamp, rounded=math.floor):
    try:
        return datetime.fromtimestamp(rounded(stamp), timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return str(stamp)


def _timestamp(value, field):
    try:
        stamp = float(_text(value))
    except ValueError:
        try:
            moment = datetime.fromisoformat(_text(value))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            stamp = moment.timestamp()
        except (ValueError, OverflowError, OSError):
            raise _invalid('decision_time', field=field) from None
    if not math.isfinite(stamp):
        raise _invalid('decision_time', field=field)
    return stamp


def _row(row):
    return {**row, 'lessons_applied': json.loads(row['lessons_applied'])} if row else None


class LineDecisions:
    def __init__(self, store, clock):
        self.store, self.clock = store, clock
        with store.db() as db:
            db.executescript(DDL)

    def get(self, decision_id):
        with self.store.db() as db:
            return _row(db.execute('SELECT * FROM line_decisions WHERE id=?', (decision_id,)).fetchone())

    def line_rows(self, line_id):
        with self.store.db() as db:
            return [_row(row) for row in db.execute(
                'SELECT * FROM line_decisions WHERE line_id=? ORDER BY made_at,rowid', (line_id,))]

    def strength(self, lesson_key):
        with self.store.db() as db:
            row = db.execute('SELECT strength FROM lesson_strength WHERE lesson_key=?', (lesson_key,)).fetchone()
            return row['strength'] if row else None

    def lesson_line(self, lesson_key):
        with self.store.db() as db:
            row = db.execute('SELECT line_id FROM lesson_strength WHERE lesson_key=?', (lesson_key,)).fetchone()
            return row['line_id'] if row else None

    @staticmethod
    def _record(db, work_id, tool, now, **evidence):
        db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                   (work_id, tool, 'succeeded', json.dumps({'evidence': evidence}), now))

    def decide(self, line_id, work_id, args, redact):
        subject, claim, expectation, check = (
            redact(_text(args.get(field)))[:limit] for field, limit in (
                ('about', SUBJECT_CHARS), ('claim', CLAIM_CHARS),
                ('expectation', EXPECTATION_CHARS), ('check', CHECK_CHARS)))
        _required('decide', (('about', subject), ('claim', claim), ('expectation', expectation),
                             ('settle_after', _text(args.get('settle_after')))))
        settle_after = _timestamp(args.get('settle_after'), 'settle_after')

        refs = re.findall(r'[^,\s]+', _text(args.get('lessons_applied')))
        lessons = om.WorkingLessons(self.store, self.clock) if refs else None
        decision = str(uuid.uuid4())
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            now = self.clock()
            if settle_after <= now:
                raise _invalid('decision_settle_after_past', now=_iso(now))
            # The Evidence survives row rotation, so the Work's budget does too.
            count = db.execute("SELECT COUNT(*) FROM tool_events WHERE job_id=? AND tool='line_decision'",
                               (work_id,)).fetchone()[0]
            if count >= MAX_RUN_DECISIONS:
                raise LineRefusal('line_decision_limit')
            by_ref = {lesson[key]: lesson['id'] for lesson in lessons.current(db)
                      for key in ('ref', 'key', 'id')} if lessons else {}
            applied = list(dict.fromkeys(by_ref[ref] for ref in refs if ref in by_ref))
            db.execute(
                'INSERT INTO line_decisions(id,line_id,work_id,made_at,subject,claim,expectation,settle_after,'
                'check_plan,lessons_applied,state) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (decision, line_id, work_id, now, subject, claim, expectation, settle_after,
                 check, json.dumps(applied), PENDING))
            excess = db.execute('SELECT COUNT(*) FROM line_decisions WHERE line_id=?', (line_id,)).fetchone()[0]
            if excess > MAX_LINE_DECISIONS:
                db.execute('DELETE FROM line_decisions WHERE id IN (SELECT id FROM line_decisions '
                           'WHERE line_id=? AND state!=? ORDER BY made_at,rowid LIMIT ?)',
                           (line_id, PENDING, excess - MAX_LINE_DECISIONS))
            self._record(db, work_id, 'line_decision', now, line_id=line_id,
                         decision=decision, lessons_applied=applied)
        return {'decision': decision, 'state': PENDING}

    def settle(self, line_id, work_id, args, redact):
        decision = _text(args.get('decision'))
        outcome = redact(_text(args.get('outcome')))[:OUTCOME_CHARS]
        reason = redact(_text(args.get('unsettleable')))[:REASON_CHARS]
        matched = args.get('matched')
        _required('settle', (('decision', decision),))
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            now = self.clock()
            row = db.execute('SELECT * FROM line_decisions WHERE id=? AND line_id=?',
                             (decision, line_id)).fetchone()
            if row is None:
                raise _refusal('line_decision_unknown', 'line_decision_unknown')
            if row['state'] != PENDING:
                raise _refusal('line_decision_settled', 'line_decision_settled')
            if now < row['settle_after']:
                raise _refusal('line_decision_not_due', 'line_decision_not_due',
                               due=_iso(row['settle_after'], math.ceil))
            if reason:
                extra = [name for name, value in (('outcome', outcome), ('matched', matched),
                                                  ('observed_at', _text(args.get('observed_at')))) if value]
                if extra:
                    raise _invalid('decision_unsettleable_alone', fields=', '.join(extra))
                attempts = row['attempts'] + 1
                state = UNSETTLEABLE if attempts >= SETTLE_ATTEMPTS else PENDING
                db.execute('UPDATE line_decisions SET state=?,reason=?,attempts=?,settled_at=?,settle_work_id=? '
                           'WHERE id=?', (state, reason, attempts, now if state == UNSETTLEABLE else None,
                                          work_id, decision))
            else:
                _required('settle', (('outcome', outcome), ('matched', matched)))
                if matched not in MATCHED:
                    raise _invalid('decision_matched')
                observed = args.get('observed_at')
                resolved_at = now if observed in (None, '') else _timestamp(observed, 'observed_at')
                if not row['made_at'] <= resolved_at <= now:
                    raise _invalid('decision_observed_window',
                                   made=_iso(row['made_at'], math.ceil), now=_iso(now))
                state = SETTLED
                db.execute('UPDATE line_decisions SET state=?,outcome=?,matched=?,resolved_at=?,settled_at=?,'
                           'settle_work_id=?,reason=NULL WHERE id=?',
                           (state, outcome, matched, resolved_at, now, work_id, decision))
            self._record(db, work_id, 'line_settlement', now, line_id=line_id, decision=decision, state=state)
        return {'decision': decision, 'state': state}

    @staticmethod
    def _lesson_ops(raw):
        def invalid(key, **values):
            return _refusal('line_lesson_invalid', 'lesson_' + key, **values)
        try:
            ops = json.loads(raw)
        except (TypeError, ValueError, RecursionError):
            raise invalid('json') from None
        if not isinstance(ops, list):
            raise invalid('json')
        if not 0 < len(ops) <= om.MAX_LESSON_OPS:
            raise invalid('count', max=om.MAX_LESSON_OPS)
        for index, item in enumerate(ops):
            if not isinstance(item, dict):
                raise invalid('object', index=index)
            op = item.get('op')
            if op not in ('add', 'edit', 'agree', 'downvote'):
                raise invalid('op', index=index)
            if op != 'add' and not _text(item.get('id')):
                raise invalid('id', index=index)
            if op in ('add', 'edit') and not 0 < len(_text(item.get('text'))) <= om.POINT_CHARS:
                raise invalid('text', index=index, max=om.POINT_CHARS)
            if 'why' in item and (not isinstance(item['why'], str) or len(item['why']) > om.CHANGE_CHARS):
                raise invalid('why', index=index, max=om.CHANGE_CHARS)
        return ops

    @staticmethod
    def _citations(db, line_id, ops):
        settled = dict(db.execute('SELECT id,settle_work_id FROM line_decisions WHERE line_id=? AND state=?',
                                  (line_id, SETTLED)))
        for index, item in enumerate(ops):
            cited = item.get('decisions')
            if (not isinstance(cited, list) or not cited
                    or any(not isinstance(ref, str) or ref not in settled for ref in cited)):
                raise _refusal('line_lesson_uncited', 'line_lesson_uncited', index=index)
        return settled

    def learn(self, line_id, work_id, args):
        ops = self._lesson_ops(args.get('lessons'))
        with self.store.db() as db:
            self._citations(db, line_id, ops)
        lessons = om.WorkingLessons(self.store, self.clock)
        if not lessons.migrated():
            lessons.migrate()

        applied, dropped, strengths = [], [], {}
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            settled = self._citations(db, line_id, ops)
            now = self.clock()
            for item in ops:
                change, refused, strength = self._apply_lesson(db, lessons, line_id, work_id, item, settled, now)
                if change:
                    applied.append(change)
                    strengths[change['id']] = strength
                else:
                    dropped.extend(refused)
            if not applied:
                reasons = ', '.join(dict.fromkeys(item['reason'] for item in dropped))
                raise LineRefusal('line_lesson_refused', message='lines.refused_line_lesson_refused',
                                  values={'detail': reasons})
            self._record(db, work_id, 'line_lesson', now, line_id=line_id,
                         decisions=list(dict.fromkeys(ref for item in ops for ref in item['decisions'])),
                         lessons=applied)
        return {'applied': applied, 'dropped': dropped, 'strength': strengths}

    @staticmethod
    def _apply_lesson(db, lessons, line_id, work_id, item, settled, now):
        op = item['op']
        ident = '' if op == 'add' else _text(item['id'])
        current = {lesson['id']: lesson for lesson in lessons.current(db)}
        target = current.get(ident)
        strength = ADD_STRENGTH
        if op != 'add':
            if target is None:
                return None, [om._lesson_drop(op, 'lesson-changed')], None
            prior = db.execute('SELECT strength FROM lesson_strength WHERE lesson_key=?',
                               (target['key'],)).fetchone()
            strength = prior['strength'] if prior else ADD_STRENGTH
            if op == 'downvote':
                strength -= 3 if len(current) >= om.LESSONS_MAX else 1
            else:
                strength += 1

        decisions = list(dict.fromkeys(item['decisions']))
        memory_op = {'add': 'add', 'edit': 'update'}.get(op)
        # Outcomes may rewrite or end only the lessons they wrote. A lesson saved another way
        # (owner feedback, self-review) only gathers its strength count here.
        outcome_lesson = target is None or target.get('origin') == ORIGIN_LINE_OUTCOMES
        if op == 'edit' and not outcome_lesson:
            return None, [om._lesson_drop(op, 'lesson-not-from-outcomes')], None
        if strength <= 0:
            strength = 0
            memory_op = 'remove' if outcome_lesson else None
        if memory_op:
            removing = memory_op == 'remove'
            operation = {'op': memory_op, 'id': ident,
                         'text': '' if removing else _text(item.get('text')),
                         'why': '' if removing else _text(item.get('why')),
                         'reason': msg('lines.lesson_outcomes_contradicted') if removing else ''}
            result = lessons.apply([operation], '', work_id, origin=ORIGIN_LINE_OUTCOMES,
                                   sources=[settled[ref] for ref in decisions], notify=False, db=db, saved_at=now)
            if not result['applied']:
                return None, result['dropped'], None
            [change] = result['applied']
        else:
            change = {'op': op, 'id': ident, 'memory_id': target['memory_id']}

        key = om.LESSONS_PREFIX + change['id']
        if memory_op == 'remove':
            db.execute('DELETE FROM lesson_strength WHERE lesson_key=?', (key,))
        else:
            db.execute('INSERT INTO lesson_strength(lesson_key,strength,line_id,decisions,updated) VALUES (?,?,?,?,?) '
                       'ON CONFLICT(lesson_key) DO UPDATE SET strength=excluded.strength,line_id=excluded.line_id,'
                       'decisions=excluded.decisions,updated=excluded.updated',
                       (key, strength, line_id, json.dumps(decisions), now))
        return change, [], max(0, strength)

    def recall_text(self, line_id, as_of):
        with self.store.db() as db:
            pending = db.execute(
                'SELECT * FROM line_decisions WHERE line_id=? AND made_at<=? AND '
                '(state=? OR (state=? AND resolved_at>?) OR (state=? AND settled_at>?)) ORDER BY made_at,rowid',
                (line_id, as_of, PENDING, SETTLED, as_of, UNSETTLEABLE, as_of)).fetchall()
            settled = db.execute(
                'SELECT * FROM line_decisions WHERE line_id=? AND state=? AND made_at<=? AND resolved_at<=? '
                'ORDER BY resolved_at DESC,settled_at DESC,rowid DESC LIMIT ?',
                (line_id, SETTLED, as_of, as_of, RECALL_SETTLED)).fetchall()
            has_records = pending or settled or db.execute(
                'SELECT 1 FROM line_decisions WHERE line_id=? AND made_at<=? LIMIT 1', (line_id, as_of)).fetchone()
            strengths = {row['lesson_key']: row for row in db.execute(
                'SELECT * FROM lesson_strength WHERE updated<=? ORDER BY strength DESC,updated DESC,lesson_key',
                (as_of,))}
        lessons = {lesson['key']: lesson for lesson in om.WorkingLessons(self.store, self.clock).current()
                   if lesson['saved'] <= as_of} if strengths else {}
        own, cross = [], []
        for key, row in strengths.items():
            if key in lessons:
                line_lessons = own if row['line_id'] == line_id else cross
                line_lessons.append(msg('lines.decisions_lesson', strength=row['strength'], **lessons[key]))

        due = [row for row in pending if row['settle_after'] <= as_of]
        waiting = len(pending) - len(due)
        how = msg('lines.decisions_how')
        if not (has_records or own or cross):
            return how
        lines = [msg('lines.decisions_heading')]
        waiting_text = msg('lines.decisions_waiting', count=waiting) if waiting else ''
        remaining = RECALL_CHARS - len(lines[0]) - len(how) - 1
        if waiting_text:
            remaining -= len(waiting_text) + 1
        groups = ([msg('lines.decisions_due', **row) for row in due[:RECALL_DUE]],
                  [msg('lines.decisions_settled', **row) for row in settled],
                  own + cross[:RECALL_CROSS])
        for index, items in enumerate(groups):
            for item in items:
                if len(item) + 1 <= remaining:
                    lines.append(item)
                    remaining -= len(item) + 1
            if index == 0 and waiting_text:
                lines.append(waiting_text)
        return '\n'.join([*lines, how])
