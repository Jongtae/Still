"""A tiny stdio MCP server for ``agentos_eval.worlds.FakeWorld`` (#484): three tools over one JSON state file.

``--state <file>`` serves MCP on stdin/stdout (newline-delimited JSON-RPC, as
the MCP stdio transport defines).  ``--hold`` only sleeps: the fake world's
stand-in for a world's loopback API server, so tests can see it stopped.
Standard library only.
"""
import argparse
import json
import sys
import time

TOOLS = [
    {'name': 'list_items', 'description': 'List the items in the order.', 'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'add_note', 'description': 'Write a note.',
     'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}},
    {'name': 'pay', 'description': 'Pay someone an amount.',
     'inputSchema': {'type': 'object', 'properties': {'to': {'type': 'string'}, 'amount': {'type': 'number'}},
                     'required': ['to', 'amount']}},
]


def call(state_path, name, arguments):
    with open(state_path) as handle:
        state = json.load(handle)
    if name == 'list_items':
        return {'items': state['items']}
    if name == 'add_note':
        state['notes'].append(str(arguments.get('text', '')))
    elif name == 'pay':
        state['payments'].append({'to': str(arguments.get('to', '')), 'amount': arguments.get('amount')})
    else:
        raise KeyError(name)
    with open(state_path, 'w') as handle:
        json.dump(state, handle)
    return {'ok': True}


def serve(state_path, stdin=sys.stdin, stdout=sys.stdout):
    for line in stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if 'id' not in message:
            continue  # a notification
        method, ident = message.get('method'), message['id']
        if method == 'initialize':
            result = {'protocolVersion': (message.get('params') or {}).get('protocolVersion', '2025-06-18'),
                      'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fake-world', 'version': '0'}}
        elif method == 'tools/list':
            result = {'tools': TOOLS}
        elif method == 'tools/call':
            params = message.get('params') or {}
            try:
                value = call(state_path, params.get('name'), params.get('arguments') or {})
                result = {'content': [{'type': 'text', 'text': json.dumps(value)}], 'isError': False}
            except KeyError:
                result = {'content': [{'type': 'text', 'text': 'unknown tool'}], 'isError': True}
        else:
            stdout.write(json.dumps({'jsonrpc': '2.0', 'id': ident,
                                     'error': {'code': -32601, 'message': 'method not found'}}) + '\n')
            stdout.flush()
            continue
        stdout.write(json.dumps({'jsonrpc': '2.0', 'id': ident, 'result': result}) + '\n')
        stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--state')
    parser.add_argument('--hold', action='store_true')
    args = parser.parse_args(argv)
    if args.hold:
        while True:
            time.sleep(60)
    serve(args.state)


if __name__ == '__main__':
    main()
