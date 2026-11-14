"""Offline, model-free paired retrieval measurement for synthetic fixtures (#827).

Reuse review: Adopt stdlib JSON/date/path facilities and the existing scenario
validation and judgments.compare conventions; Adapt QuickStore.save_memory,
QuickStore.memory and OwnerForget.forget as thin fixture glue. No retrieval,
Memory policy, scheduler or runtime behavior is implemented here. External
packages (e.g. jsonschema or an evaluation framework) cannot supply Still's
store eligibility or this experiment's paired authority verdict; no dependency
is added. Offline review only: external release activity was not checked.
Stdlib/platform APIs add no network authority or supply-chain dependency and
are compatible with the repository's Python/AGPL distribution.

API: data = load(path); refs = seed(store, data); result = run(fn, data['cases'],
refs), where ``fn(query, context)`` returns memory ids. ``context`` is the
preceding owner turn ('' when the case has none); a baseline may ignore it. Results are envelopes {version, fixture, rows}; score/compare consume
these envelopes. The caller owns and disposes the temporary QuickStore.
"""
import hashlib
import json
from datetime import date
from pathlib import Path


VERSION = 'recall-pairs/1'
SPLITS = ('dev', 'heldout')
KINDS = {'lexical-control', 'paraphrase', 'cross-language', 'short-follow-up',
         'dated-contradiction', 'correction', 'currentness', 'forgetting', 'harm-control'}


class RecallPairsError(ValueError):
    """Malformed fixture or incompatible paired results."""


def _object(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise RecallPairsError(f'Expected fields {sorted(required)}; optional {sorted(optional)}')


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise RecallPairsError('Expected nonempty text')


def _date(value):
    _text(value)
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise RecallPairsError('Expected YYYY-MM-DD') from None


def validate(data):
    """Check the strict fixture schema and final-state labels; return data."""
    _object(data, ('version', 'owner', 'today', 'rows', 'cases'))
    if data['version'] != VERSION:
        raise RecallPairsError('Unsupported fixture version')
    _text(data['owner'])
    _date(data['today'])
    if any(not isinstance(data[key], list) or not data[key] for key in ('rows', 'cases')):
        raise RecallPairsError('rows and cases must be nonempty lists')
    rows, latest, withheld = {}, {}, set()
    for row in data['rows']:
        _object(row, ('ref', 'key', 'content'), ('valid_until', 'after', 'corrected', 'forgotten'))
        for key in ('ref', 'key', 'content'):
            _text(row[key])
        ref, key = row['ref'], row['key']
        if ref in rows or not key.startswith('profile.'):
            raise RecallPairsError('Duplicate ref or non-profile key')
        for flag in ('corrected', 'forgotten'):
            if flag in row and not isinstance(row[flag], bool):
                raise RecallPairsError('Flags must be booleans')
        if 'valid_until' in row:
            _date(row['valid_until'])
        if 'after' in row:
            _text(row['after'])
        if row.get('after') != latest.get(key):
            raise RecallPairsError('after must name the preceding row of the same key')
        if row.get('corrected') and 'after' not in row:
            raise RecallPairsError('A correction needs a predecessor')
        if (key, row['content']) in withheld:
            raise RecallPairsError('Cannot re-seed an explicitly forgotten value')
        rows[ref], latest[key] = row, ref
        if row.get('forgotten'):
            withheld.update((key, old['content']) for old in rows.values() if old['key'] == key)
    eligible = {ref for ref, row in rows.items() if latest[row['key']] == ref
                and (row['key'], row['content']) not in withheld
                and row.get('valid_until', data['today']) >= data['today']}
    ids = set()
    for case in data['cases']:
        _object(case, ('id', 'split', 'kind', 'query', 'needed', 'traps', 'note'), ('irrelevant', 'context'))
        for key in ('id', 'split', 'kind', 'query', 'note'):
            _text(case[key])
        if case['id'] in ids or case['split'] not in SPLITS or case['kind'] not in KINDS:
            raise RecallPairsError('Duplicate id, invalid split or invalid kind')
        ids.add(case['id'])
        if 'context' in case:
            _text(case['context'])
        if case['kind'] == 'short-follow-up' and 'context' not in case:
            raise RecallPairsError('A short follow-up needs the preceding owner turn as context')
        groups = []
        for key in ('needed', 'traps', 'irrelevant'):
            values = case.get(key, [])
            if not isinstance(values, list) or any(not isinstance(ref, str) for ref in values):
                raise RecallPairsError('Reference groups must be lists of strings')
            if len(set(values)) != len(values) or set(values) - rows.keys():
                raise RecallPairsError('Duplicate or unknown reference')
            groups.append(set(values))
        needed, traps, irrelevant = groups
        if needed - eligible or traps & eligible or irrelevant - eligible:
            raise RecallPairsError('Needed/noise must be eligible; traps must be withheld or expired')
        if needed & traps or needed & irrelevant or traps & irrelevant:
            raise RecallPairsError('Reference groups must be disjoint')
        if case['kind'] == 'harm-control' and (needed or traps or not irrelevant):
            raise RecallPairsError('Harm controls have only irrelevant references')
    return data


def load(path):
    try:
        return validate(json.loads(Path(path).read_text(encoding='utf-8')))
    except (OSError, ValueError) as exc:
        raise RecallPairsError(f'{path}: {exc}') from None


class _SeedRefs(dict):
    """Reference mapping retaining the authoritative store, never retriever metadata."""

    def __init__(self, store, data):
        super().__init__()
        self.store, self.owner, self.today = store, data['owner'], data['today']
        self.version = data['version']
        self.fixture = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def digest(self):
        """A digest of every Memory row's id, state, content and last day: any write changes it."""
        with self.store.db() as db:
            rows = db.execute('SELECT id, state, content, valid_until FROM memories ORDER BY id').fetchall()
        return hashlib.sha256(json.dumps([list(row) for row in rows], ensure_ascii=False).encode()).hexdigest()

    def eligible(self, ident):
        row = self.store.memory(ident, owner_id=self.owner, current_only=False)
        return bool(row and row['state'] == 'current'
                    and (not row.get('valid_until') or row['valid_until'] >= self.today))


def seed(store, data):
    """Seed an empty caller-owned QuickStore through its real write/forget APIs."""
    from personal_agent.owner_forget import OwnerForget

    validate(data)
    if store.memories(owner_id=data['owner'], limit=1):
        raise RecallPairsError('Seed requires an empty owner store')
    refs = _SeedRefs(store, data)
    forget = OwnerForget(store)
    for row in data['rows']:
        saved = store.save_memory(row['key'], row['content'], owner_id=data['owner'],
                                  work_id='recall-fixture:' + row['ref'], corrected=row.get('corrected', False),
                                  # '' explicitly clears a predecessor's last day;
                                  # None would inherit it based on the real clock.
                                  valid_until=row.get('valid_until', ''))
        refs[row['ref']] = saved['id']
        if row.get('forgotten'):
            forget.forget(data['owner'], 'memory:' + saved['id'])
    return refs


def run(retriever, cases, refs):
    """Measure unique returned ids; every seeded ineligible id is an authority hit.

    Unknown ids and eligible non-needed ids are noise. Ineligible needed ids
    never receive credit, including if store state changes after seeding.
    """
    rows = []
    for case in cases:
        before = refs.digest()
        returned = retriever(case['query'], case.get('context', ''))
        if refs.digest() != before:
            # A retriever that saves, restores or ends a row could re-create a forgotten value under a new id.
            raise RecallPairsError(f"Retriever wrote to the store on case {case['id']}")
        if not isinstance(returned, list) or any(not isinstance(ident, str) for ident in returned):
            raise RecallPairsError('Retriever must return a list of memory ids')
        returned = list(dict.fromkeys(returned))
        eligible = {ident for ident in refs.values() if refs.eligible(ident)}
        forbidden = set(refs.values()) - eligible
        needed = {refs[ref] for ref in case['needed']}
        got = set(returned)
        rows.append(dict(id=case['id'], split=case['split'], kind=case['kind'],
                         needed_hit=len(got & needed & eligible), needed_total=len(needed),
                         trap_hits=len(got & forbidden), noise_hits=len(got - forbidden - (needed & eligible)),
                         returned=returned))
    return dict(version=refs.version, fixture=refs.fixture, rows=rows)


def _metrics(rows):
    hits, total = sum(row['needed_hit'] for row in rows), sum(row['needed_total'] for row in rows)
    return dict(needed_hit=hits, needed_total=total, needed_recall=hits / total if total else None,
                cases_with_trap=sum(row['trap_hits'] > 0 for row in rows),
                noise_count=sum(row['noise_hits'] for row in rows), cases=len(rows))


def score(results):
    """Separate split totals and split-local kind totals; never pool heldout/dev."""
    out = {}
    for split in SPLITS:
        rows = [row for row in results['rows'] if row['split'] == split]
        out[split] = dict(total=_metrics(rows), by_kind={
            kind: _metrics([row for row in rows if row['kind'] == kind])
            for kind in sorted({row['kind'] for row in rows})})
    return out


def compare(baseline, candidate):
    """Paired all-needed verdicts; any candidate authority hit overrides gains."""
    if baseline['version'] != candidate['version']:
        raise RecallPairsError('Cannot compare different fixture versions')
    if baseline['fixture'] != candidate['fixture']:
        raise RecallPairsError('Cannot compare different fixture contents')
    def indexed(result):
        indexed_rows = {row['id']: row for row in result['rows']}
        if len(indexed_rows) != len(result['rows']):
            raise RecallPairsError('Duplicate result ids')
        return indexed_rows
    before, after = indexed(baseline), indexed(candidate)
    if before.keys() != after.keys():
        raise RecallPairsError('Paired case selections differ')
    out = {split: dict(changes=[], trap_regressions=[]) for split in SPLITS}
    for ident, b in after.items():
        a = before[ident]
        if any(a[key] != b[key] for key in ('split', 'kind', 'needed_total')):
            raise RecallPairsError('Paired case definitions differ')
        va = a['needed_hit'] == a['needed_total'] and not a['trap_hits'] and not a['noise_hits']
        vb = b['needed_hit'] == b['needed_total'] and not b['trap_hits'] and not b['noise_hits']
        change = ('regressed' if b['trap_hits'] else 'fixed' if vb and not va else
                  'regressed' if va and not vb else 'unchanged-pass' if vb else 'unchanged-fail')
        row = dict(id=ident, kind=b['kind'], change=change)
        out[b['split']]['changes'].append(row)
        if b['trap_hits']:
            out[b['split']]['trap_regressions'].append(dict(row, trap_hits=b['trap_hits']))
    return out
