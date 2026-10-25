"""One-time connector identity upgrade; issuer approvals are never migrated.

SQLite owns the receipt and lifecycle rows. Tokens remain in the existing
ciphertext stores: stage before committing, publish before serving Work, and
retain the stage until the transaction commits so interruption is recoverable.
"""
import json
import logging

from .connector_contract import CONNECTOR_STATE_KEY, _owner_key
from .preparations import OWNER

MIGRATION_KEY = 'connector_owner_migration_v1'
CONFLICTS_KEY = 'connector_owner_migration_conflicts'


def migrate_connector_owner(service):
    store = service.store
    if service.connector_registry is None:
        return
    # These are issuer names only; credentials also survive a forgotten pairing.
    owners = service._connector_owner_candidates()
    with store.db() as db:
        jobs = db.execute('SELECT DISTINCT channel,chat_id FROM jobs WHERE chat_id IS NOT NULL').fetchall()
    for job in jobs:
        surface = service.surface_claiming(dict(job))
        if surface is not None:
            owner = surface.connector_owner(dict(job))
            if owner not in owners:
                owners.append(owner)
    names = {_owner_key(owner): owner for owner in owners}
    local = _owner_key(OWNER)

    def read(db, key, default):
        row = db.execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def put(db, key, value):
        db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (key, json.dumps(value)))

    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        receipt = read(db, MIGRATION_KEY, None)
        rows = read(db, CONNECTOR_STATE_KEY, {})
        if receipt is None and not any(key != local and value for key, value in rows.items()):
            return
        holders = [holder for holder in (service.gmail, service.calendar_oauth, service.drive_oauth)
                   if holder is not None and callable(getattr(holder, 'token_slots', None))]
        available = {connector for holder in holders for connector in holder.token_slots(local)}
        if receipt and not set(receipt['selected']).issubset(available):
            return  # Finish wiring before recovering a prepared credential publication.
        if receipt is None:
            connectors = {connector for by_owner in rows.values() for connector in by_owner}
            if not connectors.issubset(available):
                # Startup may still be wiring credential holders. Never migrate lifecycle
                # metadata without the matching encrypted credentials.
                return
            selected, conflicts = {}, []
            for connector in sorted(connectors):
                candidates = [(key, by_owner[connector]) for key, by_owner in rows.items() if connector in by_owner]
                # changed_at is the existing durable grant/lifecycle timestamp.
                # Local wins ties, then the stable source key: no pairing-order dependency.
                holder = next(holder for holder in holders if connector in holder.token_slots(local))
                credentials = []
                for key, state in candidates:
                    token = holder.store.secret(holder.token_slots(key)[connector])
                    if isinstance(token, dict) and token.get('owner') == key and token.get('access_token'):
                        credentials.append((key, state))
                winner, state = max(credentials or candidates,
                                    key=lambda item: (item[1]['changed_at'], item[0] == local, item[0]))
                selected[connector] = {'source': winner, 'state': state}
                for key, _state in candidates:
                    if key != winner:
                        conflicts.append({'connector': connector, 'source': names.get(key, key),
                                          'kept_source': names.get(winner, winner)})
            receipt = {'phase': 'prepared', 'selected': selected, 'sources': list(rows), 'conflicts': conflicts}
            # Stage ciphertext through the existing encryption boundary. No token in SQLite.
            for holder in holders:
                for connector, target in holder.token_slots(local).items():
                    item = selected.get(connector)
                    if item is None:
                        continue
                    source = holder.token_slots(item['source'])[connector]
                    token = holder.store.secret(source)
                    if token:
                        holder.store.secret('owner-migration:' + target, {**token, 'owner': local})
            put(db, MIGRATION_KEY, receipt)

    committed = False
    if receipt['phase'] != 'complete':
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Re-read under the transaction, so a second startup cannot duplicate the audit.
            receipt = read(db, MIGRATION_KEY, None)
            if receipt['phase'] != 'complete':
                for holder in holders:
                    for connector, target in holder.token_slots(local).items():
                        if connector in receipt['selected']:
                            token = holder.store.secret('owner-migration:' + target)
                            holder.store.secret(target, token or {})
                put(db, CONNECTOR_STATE_KEY, {local: {key: item['state'] for key, item in receipt['selected'].items()}})
                put(db, CONFLICTS_KEY, receipt['conflicts'])
                put(db, MIGRATION_KEY, {**receipt, 'phase': 'complete'})
                committed = True
    if committed:
        for conflict in receipt['conflicts']:
            logging.getLogger(__name__).warning(
                'connector owner migration connector=%s dropped_source=%s kept_source=%s',
                conflict['connector'], conflict['source'], conflict['kept_source'])
    # Idempotent cleanup after commit. A crash before here leaves only inaccessible
    # old credentials, and the next startup completes cleanup without another audit.
    for holder in holders:
        for key in receipt['sources']:
            if key == local:
                continue
            for slot in holder.token_slots(key).values():
                if holder.store.secret(slot):
                    holder.store.secret(slot, {})
        for slot in holder.token_slots(local).values():
            if holder.store.secret('owner-migration:' + slot):
                holder.store.secret('owner-migration:' + slot, {})
