"""Owner-authoritative Memory and MemoryCandidate policy for PA1.

The service deliberately uses :class:`QuickStore` as its only persistence
authority.  Model proposals enter the existing ``memory_candidates`` table;
only an explicit owner operation may write canonical Memory.

Integration requirement for PA1-CONV-01 (#393) and PA1-INT-01 (#394)
--------------------------------------------------------------------
EGRESS-OPEN-01 (#826, owner decision 2026-09-28): the marker no longer closes
public egress.  It still labels the Work's provenance, which its
information-use audit and turn record read.  The names below (``egress
guard``) are kept for compatibility; read them as "provenance recorded".

Reading Memory or a MemoryCandidate brings private owner content into the
current Work turn.  ``agent_runtime.Capabilities`` refuses same-turn public
egress (``web_search``, ``public_page_read``) while ``Capabilities.evidence``
or ``document_context`` is non-empty, and its own ``list_memory``/
``save_memory`` actions arm that guard by appending to ``evidence``.

``MemoryService`` is a *second*, independent read surface over the same private
rows.  A conversation layer that calls :meth:`list_memories`,
:meth:`inspect_memory`, :meth:`list_candidates` or :meth:`inspect_candidate`
instead of the ``list_memory`` capability would otherwise leave that guard
unarmed and silently re-open same-Work public egress with private Memory in
context.

Two mechanisms make that impossible to forget:

1. The constructor has no default for ``private_read_sink``.  A runtime
   integration must pass the turn-scoped marker sink, normally
   ``MemoryService(store, private_read_sink=capabilities.evidence.append)``.
   A surface that provably cannot perform public egress in the same turn (an
   offline CLI, an export job, a unit test) must opt out *explicitly* with
   ``private_read_sink=MemoryService.NO_EGRESS_GUARD``.
2. Every private read returns a :class:`PrivateMemoryRead`, which carries
   ``private_content_included`` and ``egress_guard_armed`` so a caller cannot
   drop the marker by accident and an integration test can assert on it.

The marker handed to the sink carries no memory key or content: only the
action, the fact that private content was read, and a row count.  Aggregate
:meth:`status` / :meth:`portable_status` return counts only and therefore do
not arm the guard.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from .prompt_files import prompt

#: SEC-PROFILE-01 (#658): owner profile facts (allergies, food preferences,
#: home/work places, preferred stores) are ordinary canonical Memory rows whose
#: key starts with this namespace.  Nothing here decides *what* is a profile
#: fact - the model (or the owner in Settings) chooses the key; deterministic
#: code validates only the key shape.
PROFILE_PREFIX = 'profile.'
#: ``profile.`` followed by one or more dot-separated, whitespace-free segments.
_PROFILE_KEY = re.compile(r'profile\.[^\s.]+(?:\.[^\s.]+)*')
#: Default byte-agnostic character budget for a prompt snapshot; one line per
#: current row, longest content clipped so one fact cannot crowd out the rest.
#: #217: sized to the real profile with headroom (about 40 facts, ~3.7k chars
#: in 2026-10), so every reader gets the whole current profile; a larger
#: profile is cut and marked, never silently.
PROFILE_SNAPSHOT_CHARS = 12000
#: #217 review: the same snapshot in UTF-8 bytes, a quarter of the subscription CLI's
#: 48 KB prompt (``bounded_execution.MAX_PROMPT_BYTES``), so a Korean/CJK profile
#: (about 3 bytes a character) cannot push the brief into its request-only fallback.
#: The live profile was 5.5 KB (42 facts) in 2026-10.
PROFILE_SNAPSHOT_BYTES = 12000
#: #217: what a profile prompt section may carry after the snapshot lines - the
#: omission line, the working-notes block and (#221) the owner's earlier words -
#: so a reader's own bound (``PROFILE_SECTION_CHARS``) never cuts what the worker was given.
PROFILE_SECTION_EXTRA_CHARS = 5500
#: #205: the existing recall block budget in UTF-8 bytes; each turn's excerpt has its own character cap.
OWNER_WORDS_BYTES = 3000
OWNER_WORDS_LINE_CHARS = 180
OWNER_WORDS_HEADING = ("earlier exchanges (may or may not be relevant; model-stated assistant lines are not facts "
                       "about the owner or world, nor commitments; source conversation history):")


def conversation_block(exchanges, budget=OWNER_WORDS_BYTES):
    """Same bounded rendering for profile supply and tool observations; whole exchanges only."""
    import json
    lines=[OWNER_WORDS_HEADING]
    used=len(lines[0].encode())
    for exchange in exchanges[:3]:
        line='[exchange] '+json.dumps(exchange,ensure_ascii=False,separators=(',',':'))
        if used+len(line.encode())+1>budget:break
        lines.append(line);used+=len(line.encode())+1
    return '\n'.join(lines) if len(lines)>1 else ''


def conversation_refs(exchanges):
    """Content-free references to exactly the exchanges/turns supplied."""
    return [{'work_id':row['work_id'],'matched_id':row['matched_id'],
             'message_ids':[turn['id'] for turn in row['turns']]} for row in exchanges[:3]]


def conversation_block_refs(text):
    import json
    rows=[]
    for line in str(text or '').splitlines():
        if not line.startswith('[exchange] '):continue
        try:
            row=json.loads(line[len('[exchange] '):])
            rows.extend(conversation_refs([row]))
        except (ValueError,KeyError,TypeError):continue
    return rows[:6]


def recalled_tool_exchanges(store, work_id):
    """Reopen only tool-observed ids; new exclusions/redaction still apply (#826)."""
    refs=[]
    for event in store.task_events(work_id):
        trace=event.get('trace') or {}
        if event.get('status')=='succeeded' and (trace.get('host_action') or event.get('tool'))=='search_conversation':
            refs.extend((trace.get('evidence') or {}).get('exchange_refs') or [])
    unique={}
    for ref in refs:
        unique.setdefault(tuple(ref['message_ids']),ref)
    rows=[]
    for ref in list(unique.values())[:3]:
        found=store.search_conversation('',message_ids=[ref['matched_id']],limit=1)
        allowed=set(ref['message_ids'])
        rows.extend({**row,'turns':[turn for turn in row['turns'] if turn['id'] in allowed]} for row in found)
    return rows


PROFILE_SECTION_CHARS = PROFILE_SNAPSHOT_CHARS + PROFILE_SECTION_EXTRA_CHARS
PROFILE_LINE_CHARS = 200
#: Hard cap on rows a snapshot will ever read, whatever the char budget.
_PROFILE_ROWS_CAP = 500
#: Tool-description text the runtime may append to ``save_memory`` so the
#: model lands profile facts under this namespace (wired by the runtime owner).
PROFILE_KEY_GUIDANCE = prompt('memory_profile_key')

CONNECTION_PREFERENCE_GUIDANCE = prompt('memory_connection_preference')


class MemoryServiceError(ValueError):
    """A fail-closed Memory lifecycle error safe for an owner surface."""


class PrivateMemoryRead(dict):
    """A read result that carries private owner content into the caller's turn.

    Any surface that can also reach a public destination in the same Work turn
    must have armed its egress guard before using this value; see the module
    docstring.
    """

    __slots__ = ()

    def __init__(self, payload, *, egress_guard_armed):
        super().__init__(payload)
        self["private_content_included"] = True
        self["egress_guard_armed"] = bool(egress_guard_armed)

    @property
    def private_content_included(self):
        return True

    @property
    def egress_guard_armed(self):
        return bool(self.get("egress_guard_armed"))


class MemoryService:
    """Bounded owner-facing operations over the existing QuickStore schema."""

    #: Explicit opt-out for a surface that cannot perform same-turn public egress.
    NO_EGRESS_GUARD = "memory-service-no-egress-guard"

    def __init__(self, store, now=time.time, *, private_read_sink):
        if private_read_sink is not self.NO_EGRESS_GUARD and not callable(private_read_sink):
            raise MemoryServiceError(
                "MemoryService requires private_read_sink: pass the turn-scoped egress "
                "marker sink (for example capabilities.evidence.append), or "
                "MemoryService.NO_EGRESS_GUARD for a surface with no same-turn public egress"
            )
        self.store, self.now = store, now
        self.private_read_sink = private_read_sink

    @staticmethod
    def _identity(value, label):
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise MemoryServiceError(f"{label} identification is invalid")
        return value

    def _request(self, owner_id, work_id):
        return self._identity(owner_id, "owner"), self._identity(work_id, "work")

    def _private_read(self, action, payload, row_count):
        """Arm the caller's same-turn egress guard, then mark the result."""
        armed = False
        if self.private_read_sink is not self.NO_EGRESS_GUARD:
            self.private_read_sink({
                "tool": action,
                "result": {"private_content_included": True, "row_count": row_count},
            })
            armed = True
        return PrivateMemoryRead(payload, egress_guard_armed=armed)

    def remember(self, owner_id, work_id, memory_key, content, valid_until=None):
        """Persist one fact only through this explicit owner operation."""
        owner_id, work_id = self._request(owner_id, work_id)
        return self.store.save_memory(memory_key, content, owner_id=owner_id, work_id=work_id, valid_until=valid_until)

    # -- owner profile facts (SEC-PROFILE-01 #658) ---------------------------
    #
    # A profile fact is a canonical Memory row like any other; the only thing
    # that makes it "profile" is its key namespace.  Writes from conversation
    # keep going through ``Capabilities.execute('save_memory')`` and its
    # explicit-request / MemoryCandidate rules; the owner's Settings surface
    # uses ``remember_profile`` (an explicit owner operation) and the runtime
    # reads ``profile_snapshot`` for the turn prompt.

    @staticmethod
    def is_profile_key(memory_key):
        """Shape check only: ``profile.`` plus dot-separated non-blank segments."""
        return isinstance(memory_key, str) and _PROFILE_KEY.fullmatch(memory_key.strip()) is not None

    @classmethod
    def profile_key(cls, memory_key):
        """The validated profile key, or a fail-closed error for an owner surface."""
        if not cls.is_profile_key(memory_key):
            raise MemoryServiceError(
                'profile memory key must be "profile." followed by dot-separated names, '
                'for example profile.allergy.peanut or profile.place.home'
            )
        return memory_key.strip()

    def remember_profile(self, owner_id, work_id, memory_key, content):
        """Explicit owner write of one profile fact; the same key supersedes.

        #220 (Codex review): editing a fact's words keeps the last day the current row has.
        """
        # The store keeps the replaced row's last day while it still holds (Codex review: never an elapsed one).
        return self.remember(owner_id, work_id, self.profile_key(memory_key), content)

    def _profile_rows(self, owner_id, holding_on=None):
        """Every current ``profile.*`` row of this owner, ordered by key then value.

        #220 (Codex review): with ``holding_on`` (a date) rows past their last day are
        left out in the query, so ended history cannot use up the row cap.
        """
        rows, offset = [], 0
        while len(rows) < _PROFILE_ROWS_CAP:
            page = self.store.memories(owner_id, limit=101, offset=offset, key_prefix=PROFILE_PREFIX,
                                       holding_on=holding_on)
            rows.extend(row for row in page if self.is_profile_key(row.get('memory_key')))
            if len(page) < 101:
                break
            offset += 101
        rows.sort(key=lambda row: (row['memory_key'], row['content']))
        return rows[:_PROFILE_ROWS_CAP]

    def profile_memories(self, owner_id):
        """The owner's current profile rows, ordered by key; a private read."""
        self._identity(owner_id, 'owner')
        today = self._today()
        # #220: ``ended`` is decided here in the owner's zone, so Settings labels a fact as the models read it.
        rows = [{**row, 'ended': not self.holds(row, today)} for row in self._profile_rows(owner_id)]
        return self._private_read('memory_service.profile_memories', {
            'state': 'current', 'memories': rows, 'memory_count': len(rows),
        }, len(rows))

    @staticmethod
    def _saved_date(created):
        try:
            return datetime.fromtimestamp(float(created), timezone.utc).date().isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            return 'unknown-date'

    #: #220: how many leading characters of a Memory id name its row in an ``ids`` snapshot.
    SHORT_ID_CHARS = 8

    def _today(self):
        """The owner's date, for ``valid_until`` (the last day a fact holds): in the owner's calendar zone (Codex review)."""
        from .current_context import owner_today
        return owner_today(self.store, self.now())

    def holds(self, row, today=None):
        """Whether a current row still holds today (#220): no ``valid_until``, or not before today."""
        until = row.get('valid_until') if isinstance(row, dict) else None
        return not until or str(until) >= (today or self._today())

    def holding(self, rows):
        """``rows`` without the ones past their last day - what a model may read (#220, Codex review)."""
        today = self._today()
        return [row for row in rows if self.holds(row, today)]

    def profile_snapshot(self, owner_id, *, limit_chars=PROFILE_SNAPSHOT_CHARS, line_chars=PROFILE_LINE_CHARS, query=None,
                         limit_bytes=PROFILE_SNAPSHOT_BYTES, ids=False):
        """A bounded, attributable snapshot of ``profile.*`` rows for a turn prompt.

        ``text`` is one line per row - ``key: value (saved YYYY-MM-DD)`` - in
        key order, with current profile matches from an optional bounded literal
        query first, never longer than ``limit_chars`` characters nor
        ``limit_bytes`` UTF-8 bytes; a value longer than
        ``line_chars`` is clipped with a marker rather than dropped.  Rows
        that did not fit are counted in ``omitted_count`` so the caller can
        say the snapshot is partial instead of pretending it is complete.
        ``entries`` carries the same rows with their Memory ids and saved
        times, so every line in the prompt is attributable to one row.

        #220: a row whose ``valid_until`` is before today has ended and is left
        out (it stays in the store and the owner's view); a row that ends
        later says ``until YYYY-MM-DD``.  With ``ids`` each line starts with
        ``[<first 8 characters of its id>]`` so the memory upkeep can name the
        rows a new fact ends.

        This is a private read like ``list_memories``: it arms the caller's
        turn-scoped egress sink unless the service was built with
        ``NO_EGRESS_GUARD``.
        """
        self._identity(owner_id, 'owner')
        if isinstance(limit_chars, bool) or not isinstance(limit_chars, int) or limit_chars < 1:
            raise MemoryServiceError('profile snapshot size is invalid')
        if isinstance(line_chars, bool) or not isinstance(line_chars, int) or line_chars < 8:
            raise MemoryServiceError('profile snapshot line size is invalid')
        if isinstance(limit_bytes, bool) or not isinstance(limit_bytes, int) or limit_bytes < 1:
            raise MemoryServiceError('profile snapshot byte size is invalid')
        rows = self._profile_rows(owner_id, holding_on=self._today())
        if query is not None:
            matched = [row for row in self.search_memories(owner_id, query, limit=20, key_prefix=PROFILE_PREFIX)['memories']
                       if self.is_profile_key(row.get('memory_key'))]
            found = {row['id'] for row in matched}
            rows = matched + [row for row in rows if row['id'] not in found]
        lines, entries, used, used_bytes = [], [], 0, 0
        for row in rows:
            content = ' '.join(str(row.get('content') or '').split())
            if len(content) > line_chars:
                content = content[:line_chars - 6] + ' [...]'
            until = f", until {row['valid_until']}" if row.get('valid_until') else ''
            line = f"{row['memory_key']}: {content} (saved {self._saved_date(row.get('created'))}{until})"
            size = len(line) + (1 if lines else 0)
            size_bytes = len(line.encode('utf-8')) + (1 if lines else 0)
            if used + size > limit_chars or used_bytes + size_bytes > limit_bytes:
                break
            used += size
            used_bytes += size_bytes
            # Codex review on #226: the id is added after the bounds, so the upkeep selects exactly the worker's rows.
            lines.append(f"[{str(row['id'])[:self.SHORT_ID_CHARS]}] {line}" if ids else line)
            entries.append({'id': row['id'], 'memory_key': row['memory_key'], 'content': row['content'],
                            'created': row.get('created')})
        omitted = len(rows) - len(entries)
        return self._private_read('memory_service.profile_snapshot', {
            'state': 'current', 'text': '\n'.join(lines), 'entries': entries,
            'total_count': len(rows), 'omitted_count': omitted, 'truncated': omitted > 0,
            'limit_chars': limit_chars, 'limit_bytes': limit_bytes,
        }, len(entries))

    def write(self, owner_id, work_id, memory_key, content, *, origin, explicit_owner_request=False):
        """Route non-owner output to a candidate rather than canonical Memory."""
        owner_id, work_id = self._request(owner_id, work_id)
        if origin == "owner" and explicit_owner_request is True:
            return self.remember(owner_id, work_id, memory_key, content)
        if origin not in ("owner", "model") or explicit_owner_request not in (False, True):
            raise MemoryServiceError("memory origin is invalid")
        return self.propose(owner_id, work_id, memory_key, content)

    def propose(self, owner_id, work_id, memory_key, content):
        """Store model-originated text only as pending owner review."""
        owner_id, work_id = self._request(owner_id, work_id)
        return self.store.save_memory_candidate(
            None, memory_key, content, owner_id=owner_id, work_id=work_id
        )

    @staticmethod
    def _page(limit, offset):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise MemoryServiceError("memory page size is invalid")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise MemoryServiceError("memory page offset is invalid")
        return limit, offset

    def list_memories(self, owner_id, *, limit=50, offset=0):
        self._identity(owner_id, "owner")
        limit, offset = self._page(limit, offset)
        rows = self.store.memories(owner_id, limit=limit + 1, offset=offset)
        page = rows[:limit]
        return self._private_read("memory_service.list_memories", {
            "state": "current", "memories": page,
            "next_offset": offset + limit if len(rows) > limit else None,
        }, len(page))

    def search_memories(self, owner_id, query, *, limit=10, key_prefix=None):
        """Find a small, owner-scoped set of current Memory rows by literal terms."""
        self._identity(owner_id, "owner")
        if not isinstance(query, str) or len(query) > 500:
            raise MemoryServiceError("기억 검색어를 확인하세요.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise MemoryServiceError("기억 검색 범위를 확인하세요.")
        # SQLite FTS syntax is built from extracted literal words, never from
        # worker-provided operators. Short Korean terms are kept because
        # unicode61 indexes them as words; semantic alternatives are selected
        # by the worker and passed as additional words in the query.
        terms = list(dict.fromkeys(re.findall(r"[^\W_]+", query.casefold(), flags=re.UNICODE)))[:12]
        searched = self.store.search_memories(owner_id, terms, limit=limit + 1, include_mode=True, key_prefix=key_prefix,
                                              holding_on=self._today()) if terms else {
            'memories': [], 'search_mode': 'fts5' if getattr(self.store, 'memory_search_available', False) else 'bounded-like',
        }
        # #220: a model reads search results, so facts past their last day are left out (in the query, before the limit).
        rows = searched['memories']
        page = rows[:limit]
        return self._private_read("memory_service.search_memories", {
            "state": "current", "memories": page, "query_terms": terms,
            "search_mode": searched['search_mode'],
            "truncated": len(rows) > limit,
        }, len(page))

    def inspect_memory(self, owner_id, memory_id):
        self._identity(owner_id, "owner")
        row = self.store.memory(memory_id, owner_id)
        if not row:
            raise MemoryServiceError("memory not found")
        return self._private_read("memory_service.inspect_memory", row, 1)

    def list_candidates(self, owner_id, work_id=None, *, limit=50, offset=0):
        self._identity(owner_id, "owner")
        if work_id is not None:
            self._identity(work_id, "work")
        limit, offset = self._page(limit, offset)
        rows = self.store.memory_candidates(owner_id, work_id, limit=limit + 1, offset=offset)
        page = rows[:limit]
        return self._private_read("memory_service.list_candidates", {
            "state": "pending", "candidates": page,
            "next_offset": offset + limit if len(rows) > limit else None,
        }, len(page))

    def inspect_candidate(self, owner_id, work_id, candidate_id):
        owner_id, work_id = self._request(owner_id, work_id)
        row = self.store.memory_candidate(candidate_id, owner_id, work_id)
        if not row:
            raise MemoryServiceError("memory candidate not found")
        return self._private_read("memory_service.inspect_candidate", row, 1)

    def request_candidate_approval(self, owner_id, work_id, candidate_id, content_digest, ttl=600):
        """Issue a short-lived approval for the exact inspected candidate."""
        owner_id, work_id = self._request(owner_id, work_id)
        try:
            return self.store.issue_candidate_memory_approval(
                owner_id, work_id, candidate_id, content_digest, ttl=ttl, now=self.now()
            )
        except ValueError:
            raise MemoryServiceError("memory candidate changed or was already decided") from None

    def approve_candidate(self, owner_id, work_id, candidate_id, content_digest, approval_token):
        owner_id, work_id = self._request(owner_id, work_id)
        return self.store.accept_memory_candidate(
            owner_id, work_id, candidate_id, content_digest, approval_token, now=self.now()
        )

    def reject_candidate(self, owner_id, work_id, candidate_id, content_digest):
        owner_id, work_id = self._request(owner_id, work_id)
        return self.store.reject_memory_candidate(
            owner_id, work_id, candidate_id, content_digest, now=self.now()
        )

    def request_correction(self, owner_id, work_id, memory_id, memory_key,
                           current_content, replacement_content, ttl=600):
        """Bind correction approval to owner, Work, key, old value, and new value."""
        owner_id, work_id = self._request(owner_id, work_id)
        current_digest = self.store.memory_digest(memory_key, current_content)
        replacement_digest = self.store.memory_digest(memory_key, replacement_content)
        try:
            return self.store.issue_correction_memory_approval(
                owner_id, work_id, memory_id, memory_key, current_digest,
                replacement_digest, ttl=ttl, now=self.now()
            )
        except ValueError:
            raise MemoryServiceError("memory key or current value changed") from None

    def correct(self, owner_id, work_id, memory_id, memory_key, current_content,
                replacement_content, approval_token):
        owner_id, work_id = self._request(owner_id, work_id)
        current_digest = self.store.memory_digest(memory_key, current_content)
        return self.store.correct_memory(
            owner_id, work_id, memory_id, memory_key, current_digest,
            replacement_content, approval_token, now=self.now()
        )

    def delete(self, owner_id, memory_id):
        """Delete one canonical Memory chain.

        Fails closed rather than returning a silent ``deleted: False`` receipt:
        an owner surface must not be able to report a successful delete of a
        row that was never current (already deleted, superseded, another
        owner's, or unknown).
        """
        self._identity(owner_id, "owner")
        receipt = self.store.delete_memory(owner_id, memory_id)
        if not receipt.get("deleted"):
            raise MemoryServiceError("memory not found or is not the current value")
        return receipt

    def status(self, owner_id):
        """Return counts only; status never carries private Memory content."""
        self._identity(owner_id, "owner")
        counts = self.store.memory_status_counts(owner_id)
        return {
            "state": "ready",
            **counts,
            "private_content_included": False,
        }

    def portable_status(self, owner_id):
        """Redacted export/restore evidence; no token, owner, Work, key, or content."""
        return {
            **self.status(owner_id),
            "accepted_state": "portable",
            "pending_candidate_state": "pending",
            "approval_state": "requires_fresh_owner_review_after_restore",
        }
