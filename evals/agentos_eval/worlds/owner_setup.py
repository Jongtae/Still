"""The eval plays the owner's setup of the world's MCP server in one sandbox store (#484).

Run by path with the sandbox server's interpreter and ``PYTHONPATH=<repo>/src``
before the server starts, so it uses the exact ``personal_agent`` the sandbox
runs (never an editable install of another checkout)::

    python owner_setup.py --data <sandbox data folder> --spec '<json>'

It applies what Still's own confirmed MCP-server setup applies
(``QuickstartService`` settings apply, #1297) through the same owner-state
API, in one transaction:

* ``trust_record.introduce_connection``: the world's read tools as reads, and
  the money-capable marking the scenario asks for;
* ``owner_mcp.set_confirmed``: the ``world`` server confirmed for Codex only;
* the owner's AI-connections switch on (``ai_connections``).

Then each operation the scenario names as a money operation is classified
``payment`` with ``trust_record.set_operation``, as the owner's confirmed
settings draft would.  This file imports nothing from ``agentos_eval``.
"""
import argparse
import json
import secrets
import sys
import time

ENGINE = 'codex'
SERVER = 'world'
#: ``QuickstartService.AI_CONNECTIONS_KEY``: the owner's AI-connections switch.
AI_CONNECTIONS_KEY = 'ai_connections'


def apply(data, spec):
    """``spec``: ``{'reads': [op, ...], 'payments': [op, ...], 'money': bool}``; returns the hand's summary."""
    from personal_agent import owner_mcp, trust_record
    from personal_agent.quickstart_store import QuickStore
    reads, payments = list(spec.get('reads') or []), list(spec.get('payments') or [])
    money = spec.get('money') is True
    if set(reads) & set(payments):
        raise ValueError('an operation cannot be both a read and a money operation')
    if len(reads) + len(payments) > trust_record.MAX_OPERATIONS:
        raise ValueError(f'the trust record holds at most {trust_record.MAX_OPERATIONS} operations per connection; '
                         f'the world exposes {len(reads)} reads and {len(payments)} money operations')
    store = QuickStore(data)
    target = trust_record.connection(ENGINE, 'owner-mcp', SERVER)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        before = trust_record.hand(store, target)
        trust_record.introduce_connection(store, target, reads, money, expected_revision=before['revision'],
                                          transaction=db)
        owner_mcp.set_confirmed(store, ENGINE, [SERVER], transaction=db)
        db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (AI_CONNECTIONS_KEY, json.dumps({'enabled': True, 'changed_at': time.time(),
                                                    'revision': secrets.token_hex(16)})))
    for operation in payments:
        trust_record.set_operation(store, target, operation, 'payment')
    record = trust_record.hand(store, target)
    return {'confirmed': owner_mcp.confirmed(store, ENGINE), 'money': record['money'],
            'operations': len(record['operations'])}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--spec', required=True)
    args = parser.parse_args(argv)
    print(json.dumps(apply(args.data, json.loads(args.spec))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
