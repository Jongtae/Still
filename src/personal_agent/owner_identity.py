"""One-time connector identity upgrade; issuer approvals are never migrated.

SQLite owns the receipt and lifecycle rows. Tokens remain in the existing
ciphertext stores: stage before committing, publish before serving Work, and
retain the stage until the transaction commits so interruption is recoverable.
"""
import json
import logging

from .calendar_oauth import CalendarOAuthError
from .connector_contract import CONNECTOR_STATE_KEY, _owner_key
from .gmail import GmailError
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

    def progress_receipt(db):
        receipt = read(db, MIGRATION_KEY, None)
        if receipt is None:
            return {'connectors': {}}
        if 'connectors' in receipt:
            return receipt
        # Recover receipts written by the all-connectors-at-once upgrade too.
        return {'phase': receipt['phase'], 'connectors': {
            connector: {**item, 'phase': receipt['phase'],
                        'sources': receipt['sources'],
                        'conflicts': [row for row in receipt['conflicts'] if row['connector'] == connector]}
            for connector, item in receipt['selected'].items()}}

    def save_receipt(db, receipt, rows):
        phases = {item['phase'] for item in receipt['connectors'].values()}
        receipt['phase'] = ('prepared' if 'prepared' in phases else
                            'partial' if any(key != local and value for key, value in rows.items()) else
                            'complete')
        put(db, MIGRATION_KEY, receipt)

    with store.db() as db:
        rows = read(db, CONNECTOR_STATE_KEY, {})
        receipt = progress_receipt(db)
        if not receipt['connectors'] and not any(key != local and value for key, value in rows.items()):
            return
        connectors = {connector for by_owner in rows.values() for connector in by_owner}
        connectors.update(receipt['connectors'])
    # Credential holders are inspected only when legacy state needs migrating.
    holders = {connector: holder
               for holder in (service.gmail, service.calendar_oauth, service.drive_oauth, getattr(service, 'gmail_modify_oauth', None))
               if holder is not None and callable(getattr(holder, 'token_slots', None))
               for connector in holder.token_slots(local)}

    def migrate(connector, holder):
        target = holder.token_slots(local)[connector]
        stage = 'owner-migration:' + target
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt = progress_receipt(db)
            rows = read(db, CONNECTOR_STATE_KEY, {})
            item = receipt['connectors'].get(connector)
            if item is None:
                candidates = [(key, by_owner[connector]) for key, by_owner in rows.items() if connector in by_owner]
                # Read every candidate before selecting or writing anything. An unreadable
                # credential is unknown, never evidence that another account should win.
                tokens = {key: holder.store.secret(holder.token_slots(key)[connector]) for key, _ in candidates}
                credentials = [(key, state) for key, state in candidates
                               if isinstance(tokens[key], dict) and tokens[key].get('owner') == key
                               and tokens[key].get('access_token')]
                winner, state = max(credentials or candidates,
                                    key=lambda row: (row[1]['changed_at'], row[0] == local, row[0]))
                conflicts = [{'connector': connector, 'source': names.get(key, key),
                              'kept_source': names.get(winner, winner)}
                             for key, _ in candidates if key != winner]
                item = {'phase': 'prepared', 'source': winner, 'state': state,
                        'sources': [key for key, _ in candidates], 'conflicts': conflicts}
                token = tokens[winner]
                # Even an absent token replaces an abandoned stage from a rolled-back
                # preparation. SQLite contains metadata only; staging remains encrypted.
                holder.store.secret(stage, {**token, 'owner': local} if token else {})
                receipt['connectors'][connector] = item
                save_receipt(db, receipt, rows)

        committed = False
        if item['phase'] != 'complete':
            with store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                receipt = progress_receipt(db)
                item = receipt['connectors'][connector]
                if item['phase'] != 'complete':
                    token = holder.store.secret(stage)
                    holder.store.secret(target, token or {})
                    rows = read(db, CONNECTOR_STATE_KEY, {})
                    # Remove this connector only. Unconfigured or unreadable connectors
                    # retain their exact legacy lifecycle records for a later startup.
                    for key in list(rows):
                        rows[key].pop(connector, None)
                        if not rows[key]:
                            del rows[key]
                    rows.setdefault(local, {})[connector] = item['state']
                    put(db, CONNECTOR_STATE_KEY, rows)
                    put(db, CONFLICTS_KEY, read(db, CONFLICTS_KEY, []) + item['conflicts'])
                    item['phase'] = 'complete'
                    save_receipt(db, receipt, rows)
                    committed = True
        if committed:
            for conflict in item['conflicts']:
                logging.getLogger(__name__).warning(
                    'connector owner migration connector=%s dropped_source=%s kept_source=%s',
                    connector, conflict['source'], conflict['kept_source'])
        # A separate cleanup marker avoids touching or re-auditing completed connectors
        # when another holder is configured later. Interrupted cleanup remains retryable.
        if not item.get('cleanup_complete'):
            with store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                receipt = progress_receipt(db)
                item = receipt['connectors'][connector]
                if not item.get('cleanup_complete'):
                    slots = [holder.token_slots(key)[connector] for key in item['sources'] if key != local]
                    slots.append(stage)
                    populated = [slot for slot in slots if holder.store.secret(slot)]
                    for slot in populated:
                        holder.store.secret(slot, {})
                    item['cleanup_complete'] = True
                    save_receipt(db, receipt, read(db, CONNECTOR_STATE_KEY, {}))

    for connector in sorted(connectors & holders.keys()):
        try:
            migrate(connector, holders[connector])
        except (GmailError, CalendarOAuthError):
            # Neither exception text/reason nor credential payloads belong in logs.
            # A local key/credential problem must not prevent Settings or other Work.
            logging.getLogger(__name__).warning(
                'connector owner migration deferred connector=%s credential unavailable', connector)
