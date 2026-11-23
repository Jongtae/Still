"""Entrusted lines (#798): one continuing responsibility the owner gave Still, with an identity of its own.

A **line** outlives any one preparation.  ``preparations.line_id`` links the rows that do
its work; a replacement (``replaces``) keeps the id, the owner's AI continues it when the
owner starts the same work again, and the line exists while nothing is scheduled.

Its **brief** is free text the owner's AI writes in the owner's words: the task sentence,
the standing rules (each with the owner turn it came from), the report promise, open
matters, the state and how often the line gives an account of itself.  It is stored as
versions in the existing ``procedure_versions`` store under the reserved slot prefix
``line:`` (``ProcedureLibrary.commit``), with an expected-version check.
Code knows the format only; it never reads what a
line is about.

A brief grants nothing.  It has no authority, rung, amount or subject field (an unknown
field is refused), and no authorizing path reads it: Grants, trust rungs, per-action
approval and payment autonomy are decided as before. A brief is written inside an
owner-typed Work or by the look back's stand/amend path, recorded as inferred (``preparations_service``). #968: a scheduled
run writes only its own line: as the owner's steer into it asks, or, in a run of a line the owner set up, its open matters (with an outside-content mark
on the receipt when applicable, #984).  Every other unattended write is refused.
"""
import json
import sqlite3
import time
import uuid
from contextlib import nullcontext

from .procedures import CURRENT, KEPT, LINE_PREFIX, ProcedureLibrary

SLOT_PREFIX = LINE_PREFIX
#: ``owner_forget`` reference of one whole line (every brief version).
REF_PREFIX = 'line:'


def line_id_of(ref):
    """#1168: the line id of what the worker passes, the ``line:<id>`` ref Still shows or the bare id.

    Any other value (including another kind of ref such as ``procedure:x``) is returned unchanged, so it
    names no line and is refused as ``line_not_found`` where it is used."""
    if isinstance(ref, str) and ref.startswith(REF_PREFIX):
        return ref[len(REF_PREFIX):]
    return ref
STATE_ACTIVE, STATE_STOPPED = 'active', 'stopped'
STATES = (STATE_ACTIVE, STATE_STOPPED)
#: The whole brief format.  Nothing here names a subject, an authority or an amount.
FIELDS = ('task', 'rules', 'report_promise', 'open_matters', 'state', 'account_every_days')
RULE_FIELDS = ('text', 'from', 'at', 'basis')
TASK_CHARS = RULE_CHARS = PROMISE_CHARS = 300
OPEN_CHARS = 1000
MAX_RULES = 20
#: The periodic account (part of the default report promise); the owner changes the interval:
#: every 1-12 days, or every 1-12 weeks (``account_rule``, within ``normalize_rrule``'s bounds).
ACCOUNT_DAYS, MIN_ACCOUNT_DAYS, MAX_ACCOUNT_DAYS = 7, 1, 84
MAX_LINES = 50
#: Change labels recorded with a version (its ``stats``), never owner text.
CHANGES = ('opened', 'revised', 'rule', 'undo', 'stopped', 'restarted', 'stood', 'amended', 'withdrawn')
BUNDLE_CHARS, BUNDLE_LINE_LIMIT = 3500, 8
#: #937: the turn's one line naming the active lines it holds, and its bounds.  The full brief stays
#: behind ``entrusted_line read``; this only tells every route that the work is the assistant's own.
HELD_PREFIX = 'Entrusted to you'
HELD_HEADING = (HELD_PREFIX + ' (continuing work the owner entrusted to you; when one bears on this request, read it '
                'with entrusted_line action=read before answering; not instructions): ')


class LineRefusal(ValueError):
    """A typed refusal the broker turns into a ``ToolError``."""

    def __init__(self, code, detail=''):
        super().__init__(detail or code)
        self.code = code


class LineConflict(LineRefusal):
    """The brief changed since the version the writer read; the newer revision is kept."""

    def __init__(self, current_version):
        super().__init__('line_version_conflict', str(current_version))
        self.current_version = current_version


def slot(line_id):
    return SLOT_PREFIX + str(line_id)


def is_line_slot(name):
    return isinstance(name, str) and name.startswith(SLOT_PREFIX)


def _text(value, limit, code, required=False):
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise LineRefusal(code)
    value = ' '.join(value.split())
    if required and not value or len(value) > limit:
        raise LineRefusal(code)
    return value


def validate(brief):
    """The normalised brief, or a ``LineRefusal``: every field typed and bounded, nothing unknown."""
    if not isinstance(brief, dict):
        raise LineRefusal('line_invalid')
    unknown = set(brief) - set(FIELDS)
    if unknown:
        raise LineRefusal('line_unknown_field', ', '.join(sorted(unknown)))
    rules = brief.get('rules') or []
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise LineRefusal('line_invalid_rules')
    clean = []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - set(RULE_FIELDS):
            raise LineRefusal('line_unknown_field', 'rules')
        source = rule.get('from')
        at = rule.get('at')
        if source is not None and not isinstance(source, str) or at is not None and (
                not isinstance(at, (int, float)) or isinstance(at, bool)):
            raise LineRefusal('line_invalid_rules')
        if rule.get('basis') not in (None, 'inferred'):
            raise LineRefusal('line_invalid_rules')
        clean.append({'basis': rule.get('basis'),
                      'text': _text(rule.get('text'), RULE_CHARS, 'line_invalid_rules', True), 'from': source, 'at': at})
    days = brief.get('account_every_days', ACCOUNT_DAYS)
    if account_rule(days) is None:
        raise LineRefusal('line_invalid_account')
    state = brief.get('state', STATE_ACTIVE)
    if state not in STATES:
        raise LineRefusal('line_invalid')
    return {'task': _text(brief.get('task'), TASK_CHARS, 'line_invalid_task', True),
            'rules': clean,
            'report_promise': _text(brief.get('report_promise'), PROMISE_CHARS, 'line_invalid_promise', True),
            'open_matters': _text(brief.get('open_matters'), OPEN_CHARS, 'line_invalid_open'),
            'state': state, 'account_every_days': days}


def account_rule(days):
    """The repeating rule of a line's periodic account every ``days`` days, or None for an unsupported interval."""
    if isinstance(days, bool) or not isinstance(days, int) or not MIN_ACCOUNT_DAYS <= days <= MAX_ACCOUNT_DAYS:
        return None
    if days <= 12:
        return f'RRULE:FREQ=DAILY;INTERVAL={days}'
    return f'RRULE:FREQ=WEEKLY;INTERVAL={days // 7}' if days % 7 == 0 else None


def encode(brief):
    return json.dumps(validate(brief), ensure_ascii=False, sort_keys=True)


def decode(row):
    """A stored version as ``{line_id, version, saved, change, **brief}``, or None when it cannot be read."""
    if row is None:
        return None
    try:
        brief = validate(json.loads(row['source']))
    except (LineRefusal, TypeError, ValueError):
        return None
    stats = row['stats'] if isinstance(row.get('stats'), dict) else {}
    return {'line_id': row['slot'][len(SLOT_PREFIX):], 'version': row['version'], 'saved': row['saved'],
            'change': stats.get('change'), 'written_in': row.get('work_id'), **brief}


def line_state(db, line_id):
    """``active``, ``stopped`` or None (forgotten, missing or unreadable), inside the caller's transaction."""
    try:
        row = db.execute('SELECT source FROM procedure_versions WHERE slot=? AND state=?', (slot(line_id), CURRENT)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    try:
        return validate(json.loads(row['source']))['state']
    except (LineRefusal, TypeError, ValueError):
        return None


class EntrustedLines:
    """The brief versions of every line in one owner store.  No model, no network."""

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.library = ProcedureLibrary(store, clock=lambda: self.clock())

    def current(self, line_id, db=None):
        """The current brief of ``line_id``, or None (forgotten or never opened); unreadable raises."""
        if db is None:
            with self.store.db() as db:
                return self.current(line_id, db)
        row = self.library.current(slot(line_id), db)
        if row is None:
            return None
        brief = decode(row)
        if brief is None:
            raise LineRefusal('line_unreadable')
        return brief

    def readable(self, line_id, db=None):
        """The current brief, or None for a forgotten, missing or unreadable line (never raises)."""
        try:
            return self.current(line_id, db)
        except LineRefusal:
            return None

    def version(self, line_id, version):
        """One stored version (current or kept) of ``line_id``, or None."""
        with self.store.db() as db:
            row = db.execute('SELECT * FROM procedure_versions WHERE slot=? AND version=? AND state IN (?,?)',
                             (slot(line_id), int(version), CURRENT, KEPT)).fetchone()
        return decode(self.library._row(row)) if row else None

    def lines(self):
        """Every line's current brief: active first, then stopped, newest first; unreadable ones left out."""
        with self.store.db() as db:
            rows = [self.library._row(row) for row in db.execute(
                'SELECT * FROM procedure_versions WHERE slot LIKE ? AND state=? ORDER BY saved DESC',
                (SLOT_PREFIX + '%', CURRENT))]
        briefs = [brief for brief in (decode(row) for row in rows) if brief is not None]
        return sorted(briefs, key=lambda brief: brief['state'] != STATE_ACTIVE)

    def first_saved(self, line_id, db=None):
        with (self.store.db() if db is None else nullcontext(db)) as db:
            row = db.execute('SELECT MIN(created) AS at FROM procedure_versions WHERE slot=?', (slot(line_id),)).fetchone()
        return row['at'] if row else None

    def open(self, work_id, *, task, report_promise, rules=(), open_matters='', account_every_days=ACCOUNT_DAYS):
        """A new line with its first brief version; returns the brief."""
        now = self.clock()
        brief = {'task': task, 'report_promise': report_promise, 'open_matters': open_matters,
                 'rules': [{'text': text, 'from': work_id, 'at': now} for text in rules or ()],
                 'state': STATE_ACTIVE, 'account_every_days': account_every_days}
        source = encode(brief)
        line_id = uuid.uuid4().hex[:16]
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            count = db.execute('SELECT count(DISTINCT slot) FROM procedure_versions WHERE slot LIKE ? AND state=?',
                               (SLOT_PREFIX + '%', CURRENT)).fetchone()[0]
            if count >= MAX_LINES:
                raise LineRefusal('line_too_many')
            row = self.library.commit(slot(line_id), source, work_id=work_id, stats={'change': 'opened'}, db=db)
        return decode(row)

    def provenance(self, line_id, db=None):
        """The immutable opening record, or {} for a missing line."""
        with (self.store.db() if db is None else nullcontext(db)) as db:
            row = db.execute('SELECT stats_json FROM procedure_versions WHERE slot=? AND version=1',
                             (slot(line_id),)).fetchone()
        return json.loads(row['stats_json'] or '{}') if row else {}

    def owner_written(self, line_id):
        """Whether an owner revision or explicit restart locks the brief against inference."""
        with self.store.db() as db:
            rows = db.execute('SELECT stats_json FROM procedure_versions WHERE slot=? AND version>1',
                              (slot(line_id),)).fetchall()
            if any(json.loads(row['stats_json'] or '{}').get('change') in
                   ('revised', 'rule', 'undo', 'restarted') for row in rows):
                return True
            # Restarting an active waiting line may leave the brief byte-identical. The owner act remains real.
            return db.execute("SELECT 1 FROM tool_events WHERE tool='line_restarted' AND status='succeeded' "
                              "AND json_extract(detail,'$.line_id')=? LIMIT 1", (line_id,)).fetchone() is not None

    def stand(self, work_id, brief, stats):
        """Commit an inferred opening once per stable look-back key, including across restart."""
        source = encode(brief)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            for row in db.execute('SELECT * FROM procedure_versions WHERE slot LIKE ? AND version=1 AND state IN (?,?)',
                                  (SLOT_PREFIX+'%', CURRENT, KEPT)):
                if json.loads(row['stats_json'] or '{}').get('stand_key') == stats['stand_key']:
                    return self.current(row['slot'][len(SLOT_PREFIX):], db)
            count = db.execute('SELECT count(*) FROM procedure_versions WHERE slot LIKE ? AND state=?',
                               (SLOT_PREFIX+'%', CURRENT)).fetchone()[0]
            if count >= MAX_LINES:
                raise LineRefusal('line_too_many')
            row = self.library.commit(slot(uuid.uuid4().hex[:16]), source, work_id=work_id, stats=stats, db=db)
        return decode(row)

    def write(self, line_id, work_id, expected_version, change, update, *, db=None):
        """Save ``update(brief)`` as the next version, only while ``expected_version`` is still current.

        Returns ``(before, after)``.  A stale write raises ``LineConflict`` and keeps the newer revision.
        """
        if change not in CHANGES:
            raise ValueError('unknown line change')
        owns_transaction = db is None
        with (self.store.db() if owns_transaction else nullcontext(db)) as db:
            if owns_transaction:db.execute('BEGIN IMMEDIATE')
            self.store.require_read_checkpoint_writer(db)
            before = self.current(line_id, db)
            if before is None:
                raise LineRefusal('line_not_found')
            if expected_version is not None and int(expected_version) != before['version']:
                raise LineConflict(before['version'])
            brief = {key: before[key] for key in FIELDS}
            brief['rules'] = [dict(rule) for rule in before['rules']]
            source = encode(update(brief))
            if json.loads(source) == {key: before[key] for key in FIELDS}:
                return before, before
            row = self.library.commit(slot(line_id), source, work_id=work_id, stats={'change': change}, db=db)
            return before, decode(row)

    def restart(self, line_id, work_id, *, db=None):
        """Record the owner's restart, leaving an already-active brief intact."""
        return self.write(line_id, work_id, None, 'restarted', lambda brief: {**brief, 'state': STATE_ACTIVE}, db=db)[1]

    def bundle_part_text(self, job):
        """The result, or the same empty-result failure body the direct watch sends."""
        from .conversation_projection import terminal_text
        from .current_context import redact_known_secrets
        text=(self.store.blocked_delivery_reply(job['id'])
              or self.store.projected_delivery_reply(job['id'],'system')
              or terminal_text(job['response'],job['owner_cause'] or job['error'],job['status'],
                               verified=job['owner_verified'],note=job['owner_note']))
        return redact_known_secrets(self.store,text).strip()

    def compose_line_bundle(self, parts, db=None):
        """Plain bounded composition of the runs' own words; no topic logic or model call."""
        from . import preparations as prep
        from .current_context import redact_known_secrets
        redact=lambda text:redact_known_secrets(self.store,text)
        groups={}
        for part in parts:groups.setdefault(part['brief']['line_id'],[]).append(part)
        groups=sorted(groups.values(),key=lambda group:(self.first_saved(group[0]['brief']['line_id'],db),
                                                        group[0]['brief']['line_id']))
        heading=msg('lines.bundle_heading',count=len(groups))
        footer=msg('lines.bundle_footer')
        shown=groups[:BUNDLE_LINE_LIMIT]
        more=(msg('lines.bundle_more',tasks=', '.join(excerpt(redact(g[0]['brief']['task']),40)
                                                     for g in groups[BUNDLE_LINE_LIMIT:])) if len(groups)>len(shown) else '')
        # Reserve space for labels/footer first; split the remaining report space fairly.
        more=excerpt(more,400) if more else ''
        labels=[excerpt(redact(g[0]['brief']['task']),100) for g in shown]
        share=max(200,(BUNDLE_CHARS-len(heading)-len(footer)-len(more)-sum(map(len,labels))-60)//len(shown))
        blocks=[]
        for label,group in zip(labels,shown):
            newest=group[-1]
            older=[prep.local_text(p['job']['created'],p['row']['timezone'])+' '+excerpt(' '.join(p['text'].split()),80)
                   for p in group[:-1]]
            # Older runs remain one-line excerpts; the newest retains at least 200 characters.
            old_text='\n'.join(older) if older else ''
            old_limit=max(0,share-201)
            if len(old_text)>old_limit:old_text=old_text[:old_limit-1]+'…' if old_limit else ''
            lead=excerpt(newest['text'],max(200,share-len(old_text)-1))
            blocks.append('\n'.join(x for x in (label,lead,old_text) if x))
        return redact('\n\n'.join(x for x in (heading,*blocks,more,footer) if x))

    def suppress_pending_bundles(self, line_id, db):
        """Remove stopped/forgotten parts in the owner's stop/forget transaction.

        Consumption receipts stay; each removed part gets separate suppression Evidence.
        Delivery that has already claimed a bundle stays untouched.
        """
        from . import preparations as prep
        from .context_observations import CONFIG_KEY, ContextObservations
        bundles=db.execute("SELECT id,created FROM jobs WHERE delivery='pending' AND provider='builtin' "
                           "AND model='line-bundle' AND EXISTS(SELECT 1 FROM tool_events e "
                           "WHERE e.tool='line_bundled' AND json_extract(e.detail,'$.bundle_id')=jobs.id "
                           "AND json_extract(e.detail,'$.line_id')=?)",(line_id,)).fetchall()
        for bundle in bundles:
            parts=[]
            jobs=db.execute("SELECT j.*,json_extract(e.detail,'$.line_id') AS bundle_line_id "
                            "FROM tool_events e JOIN jobs j ON j.id=e.job_id WHERE e.tool='line_bundled' "
                            "AND json_extract(e.detail,'$.bundle_id')=? "
                            "AND NOT EXISTS(SELECT 1 FROM tool_events s WHERE s.job_id=e.job_id "
                            "AND s.tool='line_bundle_suppressed' "
                            "AND json_extract(s.detail,'$.bundle_id')=json_extract(e.detail,'$.bundle_id') "
                            "AND json_extract(s.detail,'$.line_id')=json_extract(e.detail,'$.line_id')) "
                            "ORDER BY j.created,j.id",(bundle['id'],)).fetchall()
            if not any(job['bundle_line_id']==line_id for job in jobs):continue
            for job in jobs:
                try:brief=self.current(job['bundle_line_id'],db) if job['bundle_line_id']!=line_id else None
                except LineRefusal:brief=None
                # Also honour removals made before suppression Evidence was recorded.
                # A restart/forget undo cannot restore an old part to the pending bundle.
                if not brief or brief['state']!=STATE_ACTIVE or db.execute(
                              "SELECT 1 FROM procedure_versions WHERE slot=? AND saved>=? "
                              "AND json_extract(stats_json,'$.change')='stopped' "
                              "UNION ALL SELECT 1 FROM owner_state_receipts WHERE kind='line' "
                              "AND created>=? AND json_extract(items_json,'$.line')=? LIMIT 1",
                              (slot(brief['line_id']),bundle['created'],bundle['created'],brief['line_id'])).fetchone():
                    db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                               (job['id'],'line_bundle_suppressed','succeeded',
                                json.dumps({'bundle_id':bundle['id'],'line_id':job['bundle_line_id']}),self.clock()))
                    continue
                row=db.execute('SELECT timezone FROM preparations WHERE id=?',
                               (prep.preparation_of(job['request_key']),)).fetchone()
                if row is None:
                    row={'timezone':ContextObservations._normalize(self.store.config(CONFIG_KEY,db=db))['timezone'] or None}
                parts.append({'job':dict(job),'row':dict(row),'brief':brief,
                              'text':self.bundle_part_text(job)})
            if not parts:
                db.execute("UPDATE jobs SET delivery='cancelled' WHERE id=? AND delivery='pending'",(bundle['id'],))
            else:
                text=self.compose_line_bundle(parts,db)
                db.execute("UPDATE jobs SET response=? WHERE id=? AND delivery='pending'",(text,bundle['id']))
                db.execute("UPDATE messages SET content=? WHERE job_id=? AND role='assistant'",(text,bundle['id']))


# -- what runs, answers and notices read (#798) -------------------------------------------------

from .messages import msg  # noqa: E402 - the catalogue is only needed by the text helpers below

#: Notification kinds, each followed by ``<line_id>:<version>`` (one notice per Work and kind).
NOTICE_OPENED, NOTICE_RULE, NOTICE_STOPPED = 'line_opened:', 'line_rule:', 'line_stopped:'
NOTICE_PLAN = 'line_plan:'
NOTICE_WORK_STOPPED = 'line_work_stopped:'
NOTICE_KINDS = (NOTICE_OPENED, NOTICE_RULE, NOTICE_STOPPED, NOTICE_PLAN, NOTICE_WORK_STOPPED)
#: The Evidence row recording which brief version a Work read.
READ_EVENT = 'entrusted_line_read'
OUTCOME_DELIVERED, OUTCOME_KEPT, OUTCOME_FAILED, OUTCOME_OPEN = 'delivered', 'kept', 'failed', 'open'
OUTCOMES_SHOWN, READ_OUTCOMES = 6, 3
OUTCOME_CHARS, WORDS_CHARS = 600, 300
INDEX_ITEMS, INDEX_TASK_CHARS = 12, 120
#: #937 review: the index's bound holds all ``INDEX_ITEMS`` complete entries (number, a ref up to
#: ``LINE_REF_CHARS``, the schedule marker, a task excerpt with its ellipsis and the separator).
LINE_REF_CHARS = 64
NOTHING_SCHEDULED_MARKER = f"({msg('lines.blocked_unscheduled')}) "
HELD_CHARS = len(HELD_HEADING) + INDEX_ITEMS * (
    len('12) [] ; ') + len(REF_PREFIX) + LINE_REF_CHARS + INDEX_TASK_CHARS + 1 + len(NOTHING_SCHEDULED_MARKER))


def held_text(briefs, clean=lambda text: text):
    """#937: the one-line index of active lines with refs, task sentences and missing schedules.

    The secretary contract (#798 "Answers") gives a Work the owner started only each active line's
    id, task sentence and missing schedule marker. The brief and outcomes are read with ``entrusted_line read`` when the AI
    judges a line relevant, never injected.  One line, so every route reads it as its own fact.
    ``clean`` (stored-secret redaction) runs on each whole task before any cut.
    """
    items = []
    for brief in briefs:
        if brief.get('state') != STATE_ACTIVE or len(items) >= INDEX_ITEMS:
            continue
        task = excerpt(' '.join(str(clean(str(brief.get('task') or '')) or '').split()), INDEX_TASK_CHARS)
        if task:
            # The ref names the line for ``entrusted_line read`` and for the information-use audit.
            ref = f'[{REF_PREFIX}{brief["line_id"]}] ' if brief.get('line_id') else ''
            marker = NOTHING_SCHEDULED_MARKER if brief.get('nothing_scheduled') else ''
            items.append(f'{len(items) + 1}) {marker}{ref}{task}')
    if not items:
        return ''
    return (HELD_HEADING + '; '.join(items))[:HELD_CHARS]


def is_notice(kind):
    return isinstance(kind, str) and kind.startswith(NOTICE_KINDS)


def excerpt(text, limit=OUTCOME_CHARS):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def whole_days(value):
    """A positive whole number from a tool argument (``"7"`` or ``7``), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) or None
    return None


def split_rules(value):
    """Standing rules given at opening: one per line of a string, or a list of strings."""
    if value in (None, ''):
        return []
    items = value if isinstance(value, list) else str(value).splitlines() if isinstance(value, str) else None
    if items is None or not all(isinstance(item, str) for item in items):
        raise LineRefusal('line_invalid_rules')
    return [' '.join(item.split()) for item in items if item.strip()]


def outcome_label(status, delivered):
    """What came of one run of a line: delivered to the owner, kept in Still, failed, or still open."""
    from . import work_status as ws
    if status in ws.ACTIVE_PREPARATION or status in ws.WAITING_PREREQUISITE:
        return OUTCOME_OPEN
    if ws.has_delivered_result(status):
        return OUTCOME_DELIVERED if delivered else OUTCOME_KEPT
    return OUTCOME_FAILED if status else None


def public_brief(brief):
    """A brief as a tool result shows it: its id, version and fields (rule sources as Work ids)."""
    return {'line_id': brief['line_id'], 'version': brief['version'], 'state': brief['state'],
            'task': brief['task'], 'report_promise': brief['report_promise'], 'open_matters': brief['open_matters'],
            'rules': [rule['text'] for rule in brief['rules']], 'account_every_days': brief['account_every_days']}


def brief_text(brief, lang='ko'):
    rules = '\n'.join(f'{number}. {rule["text"]}' for number, rule in enumerate(brief['rules'], 1)) or msg('lines.no_rules', lang)
    return msg('lines.brief', lang, task=brief['task'], promise=brief['report_promise'], rules=rules,
               open=brief['open_matters'] or msg('label.none', lang), version=brief['version'],
               days=brief['account_every_days'])


def outcomes_text(outcomes, timezone_name=None, lang='ko'):
    from .preparations import local_text
    if not outcomes:
        return msg('lines.no_outcomes', lang)
    return msg('lines.outcomes_heading', lang) + '\n' + '\n'.join(
        msg('lines.outcome_' + item['outcome'], lang, at=local_text(item['at'], timezone_name), text=item['text'] or '-')
        for item in outcomes)


def entry_text(entry, lang='ko'):
    """One line of the list answer: task, report promise, last delivered report, next run or blocker."""
    checks = ''.join('\n' + msg('lines.list_check', lang, title=item['title'], seconds=item['every_seconds'],
                               result=msg('checks.result_' + (item['last_result'] or 'none'), lang),
                               runs=item['runs'], wakes=item['wakes'], cap=item['daily_wakes'])
                     for item in entry.get('checks', ()))
    if entry['state'] == 'waiting':
        return msg('lines.list_waiting', lang, task=entry['task']) + checks
    if entry['state'] != STATE_ACTIVE:
        return msg('lines.list_stopped', lang, task=entry['task']) + checks
    last = entry.get('last_report') or msg('lines.no_report_yet', lang)
    if entry.get('blocked') == msg('lines.blocked_unscheduled'):
        following = msg('lines.blocked_unscheduled', lang)
    elif entry.get('blocked'):
        following = msg('lines.list_blocked', lang, reason=entry['blocked'])
    elif entry.get('next_is_account'):
        following = msg('lines.list_next_account', lang, at=entry['next'])
    else:
        following = msg('lines.list_next', lang, at=entry['next'])
    if entry.get('nothing_scheduled') and entry.get('blocked') != msg('lines.blocked_unscheduled'):
        following = f"{msg('lines.blocked_unscheduled', lang)} / {following}"
    return msg('lines.list_entry', lang, task=entry['task'], promise=entry['report_promise'], last=last,
               following=following) + checks


def change_text(brief, *, rule='', drop=None, changes=None, days=None):
    """A plain description of one proposed brief change, for the owner-turn judgment."""
    parts = []
    if rule:
        parts.append('add rule: ' + rule)
    if drop:
        parts.append('remove rule ' + str(drop) + ': ' + brief['rules'][drop - 1]['text'])
    for key, value in (changes or {}).items():
        parts.append(f'{key}: {brief.get(key)!s} -> {value}')
    if days is not None:
        parts.append(f"account every {brief['account_every_days']} days -> {days} days")
    return '; '.join(parts)
