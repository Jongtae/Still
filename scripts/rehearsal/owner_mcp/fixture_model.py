"""Local fixture model for observing CLI tool launches without any model provider.

OpenAI Responses (Codex, POST /v1/responses, SSE) and Anthropic Messages
(Claude Code, POST /v1/messages, SSE or JSON).  The first turn calls the tool
whose name contains FIXTURE_TOOL (with FIXTURE_ARGS); a turn that carries a
tool result answers with text.  Every request's tool names are logged.
"""
import json
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOL = os.environ.get('FIXTURE_TOOL', 'read_ticket')
ARGS = json.loads(os.environ.get('FIXTURE_ARGS', '{"key": "FIX-1"}'))
LOG = os.environ['FIXTURE_MODEL_LOG']


def log(entry):
    with open(LOG, 'a') as handle:
        handle.write(json.dumps(entry) + '\n')


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def body(self):
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b''
        encoding = (self.headers.get('Content-Encoding') or '').lower()
        if encoding == 'zstd':
            import zstandard
            raw = zstandard.ZstdDecompressor().decompressobj().decompress(raw)
        elif encoding == 'gzip':
            import gzip
            raw = gzip.decompress(raw)
        try:
            return json.loads(raw or b'{}')
        except Exception as exc:
            log({'bad_body': repr(exc), 'headers': dict(self.headers), 'head': raw[:80].hex()})
            return {}

    def sse(self, events):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        for name, data in events:
            self.wfile.write(f'event: {name}\ndata: {json.dumps(data)}\n\n'.encode())
            self.wfile.flush()
        self.close_connection = True

    def json_reply(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        log({'get': self.path})
        if self.path.rstrip('/').endswith('/models'):
            return self.json_reply({'data': [], 'models': []})
        self.json_reply({})

    def do_POST(self):
        data = self.body()
        if self.path.endswith('/responses'):
            return self.on_responses(data)
        if self.path.split('?')[0].endswith('/messages'):
            return self.on_messages(data)
        log({'post': self.path})
        self.json_reply({})

    # -- OpenAI Responses (Codex) ------------------------------------------------
    def on_responses(self, data):
        tools = [tool.get('name') or (tool.get('function') or {}).get('name') for tool in data.get('tools') or []]
        items = data.get('input') or []
        done = any(isinstance(item, dict) and item.get('type') == 'function_call_output' for item in items)
        outputs = [item.get('output') for item in items if isinstance(item, dict) and item.get('type') == 'function_call_output']
        target, namespace = next((name for name in tools if name and TOOL in name), None), None
        for tool in data.get('tools') or []:
            if tool.get('type') == 'namespace':
                for inner in tool.get('tools') or []:
                    if TOOL in str(inner.get('name')):
                        target, namespace = inner['name'], tool['name']
        log({'api': 'responses', 'namespace': namespace, 'tools': tools, 'tool_outputs': outputs, 'target': target,
             })
        rid = 'resp_' + uuid.uuid4().hex
        if done or target is None:
            item = {'type': 'message', 'role': 'assistant', 'id': 'msg_' + uuid.uuid4().hex,
                    'content': [{'type': 'output_text', 'text': 'fixture done', 'annotations': []}]}
        else:
            item = {'type': 'function_call', 'id': 'fc_' + uuid.uuid4().hex, 'call_id': 'call_' + uuid.uuid4().hex,
                    'name': target, 'arguments': json.dumps(ARGS), **({'namespace': namespace} if namespace else {})}
        self.sse([('response.created', {'type': 'response.created', 'response': {'id': rid}}),
                  ('response.output_item.done', {'type': 'response.output_item.done', 'item': item}),
                  ('response.completed', {'type': 'response.completed', 'response': {
                      'id': rid, 'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2,
                                           'input_tokens_details': {'cached_tokens': 0},
                                           'output_tokens_details': {'reasoning_tokens': 0}}}})])

    # -- Anthropic Messages (Claude Code) ---------------------------------------
    def on_messages(self, data):
        tools = [tool.get('name') for tool in data.get('tools') or []]
        results = []
        for message in data.get('messages') or []:
            for part in message.get('content') if isinstance(message.get('content'), list) else []:
                if isinstance(part, dict) and part.get('type') == 'tool_result':
                    results.append(part.get('content'))
        target = next((name for name in tools if name and TOOL in name), None)
        log({'api': 'messages', 'model': data.get('model'), 'tools': tools, 'tool_results': results, 'target': target})
        mid = 'msg_' + uuid.uuid4().hex
        if results or target is None:
            content, stop = [{'type': 'text', 'text': 'fixture done'}], 'end_turn'
        else:
            content, stop = [{'type': 'tool_use', 'id': 'toolu_' + uuid.uuid4().hex, 'name': target, 'input': ARGS}], 'tool_use'
        if not data.get('stream'):
            return self.json_reply({'id': mid, 'type': 'message', 'role': 'assistant', 'model': data.get('model'),
                                    'content': content, 'stop_reason': stop, 'stop_sequence': None,
                                    'usage': {'input_tokens': 1, 'output_tokens': 1}})
        events = [('message_start', {'type': 'message_start', 'message': {
            'id': mid, 'type': 'message', 'role': 'assistant', 'model': data.get('model'), 'content': [],
            'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}}})]
        block = content[0]
        if block['type'] == 'text':
            events += [('content_block_start', {'type': 'content_block_start', 'index': 0,
                                                'content_block': {'type': 'text', 'text': ''}}),
                       ('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                                                'delta': {'type': 'text_delta', 'text': block['text']}})]
        else:
            events += [('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': {
                           'type': 'tool_use', 'id': block['id'], 'name': block['name'], 'input': {}}}),
                       ('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                                                'delta': {'type': 'input_json_delta', 'partial_json': json.dumps(ARGS)}})]
        events += [('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
                   ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': stop, 'stop_sequence': None},
                                      'usage': {'output_tokens': 1}}),
                   ('message_stop', {'type': 'message_stop'})]
        self.sse(events)


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1]) if len(sys.argv) > 1 else 0), Handler)
    print(server.server_port, flush=True)
    server.serve_forever()
