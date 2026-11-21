"""Portable Memory interchange: export the owner's Memory as a ``.mem`` bundle, import one as candidates (#38).

A thin adapter over the Portable Memory reference SDK (MacPaw ``portable-memory`` 0.3.0, MIT, format
1.1.0).  The SDK owns the bundle: its directory layout, canonical JSONL, ``CHECKSUMS``, manifest,
tombstone-first import and the no-resurrection guard.  Still subclasses ``PortableMemoryStore`` only
to map its own rows; Still's ``memories`` table stays canonical and nothing here replaces it.

Portable scope (see ``docs/memory-interchange.en.md`` for the full compatibility matrix):

* Export: this owner's Memory rows that are current, superseded or corrected, as ``fact`` records
  (``predicate`` = memory key, ``object`` = value, validity and supersession from the rows).  Rows the
  owner forgot, deleted or moved (``owner_state_receipts``) and saves that were undone (``retracted``)
  leave as ``tombstone`` records, never as facts.  Stored secret values are redacted from every value.
* Never exported: memory candidates and approvals, Grants, sessions, credentials, folder grants, the
  derived profile snapshot and search terms, Work references and evidence quotes.
* Import: only facts current in the bundle become pending ``MemoryCandidate`` rows (C5), reviewed in
  Personal Space like any other candidate.  Nothing imported is current Memory, an instruction, an
  approval or a Grant.  A bundle tombstone withdraws a still-pending candidate imported from that fact;
  it never changes current Memory (the owner forgets there if they want to).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

from .current_context import redact_known_secrets

#: The candidate ``source.kind`` of an imported fact.
SOURCE_KIND = 'portable_import'
#: Memory states exported as facts (history included); every other state leaves as a tombstone or not at all.
EXPORTED_STATES = ('current', 'superseded', 'corrected')
#: Still tracks no confidence or importance; the spec requires both, so a constant with no meaning is written.
NEUTRAL = 0.5
FACT_PREFIX = 'fact_still_'
#: Receipts whose memory items have left use (``undone`` ones are back in use).
TOMBSTONE_RECEIPTS = ('forgotten', 'deleted')
#: Bounded per bundle, like every other owner-state read.
MAX_FACTS = 10000


class MemoryInterchangeError(ValueError):
    """A bundle that cannot be exported or imported; nothing was written."""


def _sdk():
    try:
        import portable_memory
        from portable_memory.exporter import BundleExporter
        from portable_memory.importer import BundleImporter
    except ImportError as exc:  # pragma: no cover - a base dependency
        raise MemoryInterchangeError('The portable-memory package is not installed.') from exc
    return portable_memory, BundleExporter, BundleImporter


def _at(seconds):
    return datetime.fromtimestamp(float(seconds), tz=timezone.utc)


def _generator():
    try:
        from importlib.metadata import version
        return 'still/' + version('personal-agentos')
    except Exception:
        return 'still/unknown'


def fact_id(memory_id):
    return FACT_PREFIX + str(memory_id)


def _store_class():
    pm, _exporter, _importer = _sdk()

    class StillMemoryStore(pm.PortableMemoryStore):
        """The owner's Memory rows as the SDK's store seam; export readers and import writers only."""

        def __init__(self, store, owner_id, *, now=time.time, bundle=None):
            self.store, self.owner_id, self.now = store, owner_id, now
            self.owner_key = store._memory_binding(owner_id)
            self.bundle = bundle or {}
            self.report = {'candidates': 0, 'already_known': 0, 'forgotten_here': 0, 'history_skipped': 0,
                           'invalid': 0, 'secret_refused': 0, 'withdrawn': 0, 'tombstones_not_applied': 0}
            self.tombstoned = set()

        def store_info(self):
            return pm.StoreInfo(generator=_generator())

        # -- export ---------------------------------------------------------
        def _rows(self):
            with self.store.db() as db:
                return [dict(row) for row in db.execute(
                    f"SELECT id,memory_key,content,created,supersedes,state,invalidated_by,invalidated_at,valid_until "
                    f"FROM memories WHERE owner_key=? AND state IN ({','.join('?' * len(EXPORTED_STATES))}) "
                    f"ORDER BY created,id LIMIT ?", (self.owner_key, *EXPORTED_STATES, MAX_FACTS + 1))]

        def export_facts(self):
            rows = self._rows()
            if len(rows) > MAX_FACTS:
                raise MemoryInterchangeError(f'More than {MAX_FACTS} Memory rows; export refused rather than cut.')
            exported = {row['id'] for row in rows}
            with self.store.db() as db:
                # Every successor, in any state: a row whose successor was forgotten still ended then.
                successor = {row['supersedes']: dict(row) for row in db.execute(
                    'SELECT id,supersedes,created FROM memories WHERE owner_key=? AND supersedes IS NOT NULL '
                    'ORDER BY created,id', (self.owner_key,))}
            facts = []
            for row in rows:
                value = redact_known_secrets(self.store, row['content'])
                key = redact_known_secrets(self.store, row['memory_key'])
                after = successor.get(row['id'])
                ended = next_id = None
                if row['state'] != 'current':
                    # A row no longer current always carries an end, so a reader never takes it as current.
                    ended = (after or {}).get('created') or row['invalidated_at'] or row['created']
                    next_id = (after or {}).get('id') or row['invalidated_by']
                facts.append(pm.PortableFact(
                    id=fact_id(row['id']), episode_id='', subject='owner', predicate=key, obj=value,
                    text=f'{key}: {value}', t_valid_from=_at(row['created']), confidence=NEUTRAL,
                    importance=NEUTRAL, created_at=_at(row['created']),
                    t_valid_to=_at(ended) if ended is not None else None,
                    superseded_by=fact_id(next_id) if next_id in exported else None))
            return facts

        def export_tombstones(self, since):
            from .owner_forget import OwnerForget
            OwnerForget(self.store)  # its receipt tables, created on first use
            tombstones = []
            with self.store.db() as db:
                receipts = db.execute(
                    f"SELECT id,operation,state,items_json,created FROM owner_state_receipts "
                    f"WHERE owner_key=? AND kind='memory' AND state IN ({','.join('?' * len(TOMBSTONE_RECEIPTS))}) "
                    f"ORDER BY created,id", (self.owner_key, *TOMBSTONE_RECEIPTS)).fetchall()
                retracted = db.execute("SELECT id,created FROM memories WHERE owner_key=? AND state='retracted' "
                                       "ORDER BY created,id", (self.owner_key,)).fetchall()
            for receipt in receipts:
                for item in json.loads(receipt['items_json']).get('rows') or ():
                    if item.get('table') != 'memories':
                        continue
                    deleted = receipt['state'] == 'deleted'
                    tombstones.append(pm.Tombstone(
                        id=f"tomb_still_{receipt['id']}_{item['id']}",
                        op=pm.TombstoneOp.REDACT if deleted else pm.TombstoneOp.DELETE,
                        target_kind='fact', target_id=fact_id(item['id']), deleted_at=_at(receipt['created']),
                        actor='owner', reason='deleted' if deleted else receipt['operation']))
            for row in retracted:
                # A save that was undone: never the owner's Memory, though an earlier export may have carried it.
                tombstones.append(pm.Tombstone(id=f"tomb_still_retracted_{row['id']}", op=pm.TombstoneOp.DELETE,
                                               target_kind='fact', target_id=fact_id(row['id']),
                                               deleted_at=_at(row['created']), actor='owner', reason='retracted'))
            if since is not None:
                tombstones = [item for item in tombstones if item.deleted_at >= since]
            return tombstones

        # -- import ---------------------------------------------------------
        def tombstoned_target_ids(self):
            return set(self.tombstoned)

        def apply_tombstone(self, tombstone):
            """Withdraw a still-pending candidate imported from that fact; current Memory is never touched."""
            self.tombstoned.add(tombstone.target_id)
            withdrawn = 0
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                for row in db.execute("SELECT id,source FROM memory_candidates WHERE owner_key=? AND state='pending' "
                                      "AND source IS NOT NULL", (self.owner_key,)).fetchall():
                    source = _source(row['source'])
                    if source.get('kind') == SOURCE_KIND and source.get('fact_id') == tombstone.target_id:
                        withdrawn += db.execute("UPDATE memory_candidates SET state='retracted',decided=? "
                                                "WHERE id=? AND state='pending'", (self.now(), row['id'])).rowcount
            self.report['withdrawn'] += withdrawn
            if not withdrawn:
                self.report['tombstones_not_applied'] += 1

        def import_fact(self, fact):
            from .owner_forget import OwnerForget
            if fact.t_valid_to is not None or fact.superseded_by or fact.pruned_at is not None:
                self.report['history_skipped'] += 1
                return
            key = fact.predicate
            value = fact.obj or fact.text
            try:
                digest = self.store.memory_digest(key, value)
            except ValueError:
                self.report['invalid'] += 1
                return
            if redact_known_secrets(self.store, key) != key or redact_known_secrets(self.store, value) != value:
                self.report['secret_refused'] += 1
                return
            if OwnerForget(self.store).is_forgotten(self.owner_id, key, value):
                # The owner forgot this exact value here; an import never brings it back as a proposal.
                self.report['forgotten_here'] += 1
                return
            with self.store.db() as db:
                known = db.execute("SELECT 1 FROM memories WHERE owner_key=? AND content_digest=? AND state='current'",
                                   (self.owner_key, digest)).fetchone() or any(
                    row['state'] == 'pending' or _source(row['source']).get('kind') == SOURCE_KIND
                    for row in db.execute('SELECT state,source FROM memory_candidates WHERE owner_key=? AND content_digest=?',
                                          (self.owner_key, digest)))
            if known:
                self.report['already_known'] += 1
                return
            source = {'kind': SOURCE_KIND, 'fact_id': fact.id, 'format': self.bundle.get('format'),
                      'generator': redact_known_secrets(self.store, str(self.bundle.get('generator') or ''))[:120],
                      'bundle_digest': self.bundle.get('bundle_digest'),
                      'valid_from': pm.format_timestamp(fact.t_valid_from)}
            self.store.save_memory_candidate(None, key, value, self.owner_id, import_work_id(self.bundle), source=source)
            self.report['candidates'] += 1

    return StillMemoryStore


def _source(text):
    try:
        value = json.loads(text) if text else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def import_work_id(bundle):
    """The review binding of one bundle's candidates: one stable group per bundle digest."""
    digest = str(bundle.get('bundle_digest') or '') or hashlib.sha256(json.dumps(bundle, sort_keys=True).encode()).hexdigest()
    return 'portable-import:' + digest[:32]


def export_memory(store, owner_id, directory):
    """Write this owner's portable Memory to ``directory`` as a Portable Memory bundle; its counts."""
    pm, BundleExporter, _importer = _sdk()
    manifest = BundleExporter().export(_store_class()(store, owner_id), directory, level=pm.ConformanceLevel.L0)
    return {'format': manifest.format, 'generator': manifest.generator, 'counts': dict(manifest.counts),
            'bundle_digest': manifest.bundle_digest, 'directory': str(directory)}


def import_memory(store, owner_id, directory, *, now=time.time):
    """Read a Portable Memory bundle into pending MemoryCandidates for the owner's review; a report.

    The SDK verifies the manifest and every checksum before any write; a corrupt or unlisted file
    refuses the whole bundle.  Kinds Still does not take (episodes, core blocks, entities, procedures,
    passthrough streams ...) are counted under ``not_imported`` and never stored.
    """
    pm, _exporter, BundleImporter = _sdk()
    from pathlib import Path
    manifest_path = Path(directory) / 'manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise MemoryInterchangeError('The bundle has no readable manifest.') from exc
    if not isinstance(manifest, dict):
        raise MemoryInterchangeError('The bundle has no readable manifest.')
    bundle = {'format': manifest.get('format'), 'generator': manifest.get('generator'),
              'bundle_digest': manifest.get('bundleDigest')}
    target = _store_class()(store, owner_id, now=now, bundle=bundle)
    try:
        result = BundleImporter().import_bundle(target, directory, reembed=False)
    except Exception as exc:
        if type(exc).__name__ == 'MemImportError' or isinstance(exc, (OSError, ValueError, KeyError, TypeError)):
            raise MemoryInterchangeError(f'The bundle was refused: {type(exc).__name__}: {exc}') from None
        raise
    counts = manifest.get('counts') if isinstance(manifest.get('counts'), dict) else {}
    not_imported = {kind: count for kind, count in sorted(counts.items())
                    if kind not in ('fact', 'tombstone', 'audit') and isinstance(count, int) and count}
    return {**target.report, 'facts_read': result.applied.get('fact', 0),
            'tombstones': result.tombstones_applied, 'skipped_tombstoned': result.skipped_tombstoned,
            'not_imported': not_imported, 'review_ref': 'workref:' + store._work_binding(import_work_id(bundle)),
            'current_memory_changed': False}
