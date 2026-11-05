"""#651 SDK transport adapter, loaded only when the optional peer channel is used.

Adapt a2a-sdk 1.3.0's protobuf, ClientFactory, routes, task store and executor.
Still supplies authenticated peer isolation and durable policy receipts. Each
peer has a distinct SDK handler/active-task registry, including while tasks run.
No custom A2A wire protocol or public-address mechanism lives here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
import weakref

import httpx
from google.protobuf.json_format import MessageToDict, ParseDict
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers.proto_helpers import get_message_text, new_text_message
from a2a.server.agent_execution import AgentExecutor
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import ServerCallContextBuilder, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskStore, TaskUpdater
from a2a.utils.proto_utils import validate_proto_required_fields
from a2a.types.a2a_pb2 import (AgentCard, AgentInterface, Role, SendMessageRequest,
                               Task, TaskStatus, TaskState, AgentCapabilities, AgentSkill, SecurityScheme, HTTPAuthSecurityScheme, SecurityRequirement)

from .a2a_peers import PeerError, endpoint
from . import work_mode

MAX_BYTES = 65536
_services = weakref.WeakSet()


def redacted_json(content, redact):
    """Decode ordinary JSON escapes before the SDK can log/trace a value.

    This is secret mediation, not protocol parsing: SDK protobuf validation
    still owns every A2A field, method and response below this byte boundary.
    """
    def clean(value):
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {redact(key): clean(item) for key, item in value.items()}
        return value
    try:
        return json.dumps(clean(json.loads(content)), ensure_ascii=False).encode()
    except Exception:
        raise PeerError('invalid_peer_json') from None


class SecretFilter(logging.Filter):
    """The SDK has no logger-injection seam; reuse Still's secret redactor.

    SDK debug events contain protobuf payloads and exception tracebacks may
    contain peer-supplied error text. Never retain those unredacted, even when
    an operator enables debug logging. This grants no authority to SDK text.
    """
    def filter(self, record):
        text = record.getMessage()
        for service in list(_services):
            try:
                text = service._redact_known_secrets(text)
            except Exception:
                text = 'A2A SDK event (payload withheld: secret redaction unavailable)'
                break
        record.msg, record.args = text, ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


_secret_filter = SecretFilter()


def protect_sdk_logs(service):
    _services.add(service)
    protect_transport_logs()


def protect_transport_logs():
    # All SDK modules used by the adapter have been imported above. Filters
    # belong on emitting loggers: parent-logger filters do not see child events.
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if ((name == 'a2a' or name.startswith('a2a.') or name == 'httpx' or name.startswith('httpcore.'))
                and isinstance(logger, logging.Logger) and _secret_filter not in logger.filters):
            logger.addFilter(_secret_filter)


def parse_card(value):
    try:
        if isinstance(value, str):
            if len(value.encode()) > MAX_BYTES:
                raise ValueError
            value = json.loads(value)
        if not isinstance(value, dict) or len(json.dumps(value).encode()) > MAX_BYTES:
            raise ValueError
        card = ParseDict(value, AgentCard())
        validate_proto_required_fields(card)
        if not card.name or not card.version or len(card.supported_interfaces) != 1:
            raise ValueError
        interface = card.supported_interfaces[0]
        endpoint(interface.url)
        if interface.protocol_binding != 'JSONRPC' or interface.protocol_version != '1.0' or interface.tenant:
            raise ValueError
        for scheme in card.security_schemes.values():
            if (not scheme.HasField('http_auth_security_scheme')
                    or scheme.http_auth_security_scheme.scheme.lower() != 'bearer'):
                raise ValueError
        for requirement in card.security_requirements:
            if any(name not in card.security_schemes for name in requirement.schemes):
                raise ValueError
        return MessageToDict(card)
    except Exception:
        raise PeerError('invalid_agent_card') from None


async def fetch_card(url, *, store, credential='', http=None):
    work_mode.refuse(store, 'a2a')
    endpoint(url)
    own = http is None
    http = http or httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)
    protect_transport_logs()
    try:
        async with http.stream('GET', url, follow_redirects=False,
                               headers={'Authorization': 'Bearer ' + credential} if credential else {}) as response:
            if response.status_code != 200:
                raise PeerError('peer_card_unavailable')
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise PeerError('peer_card_too_large')
            return parse_card(json.loads(content))
    except PeerError:
        raise
    except Exception:
        raise PeerError('peer_card_unavailable') from None
    finally:
        if own:
            await http.aclose()


async def send_message(card, text, message_id, credential='', *, store, http=None, guard=lambda: None,
                       redact=lambda text: text):
    work_mode.refuse(store, 'a2a')
    card = ParseDict(parse_card(card), AgentCard())
    # The SDK only receives a client restricted to the single confirmed endpoint.
    # No automatic redirects, proxy environment, retries or alternate interfaces.
    own = http is None
    http = http or httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False)
    protect_transport_logs()
    url = card.supported_interfaces[0].url

    class BoundTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            work_mode.refuse(store, 'a2a')
            guard()
            if request.url != httpx.URL(url) or request.method != 'POST':
                raise PeerError('unregistered_peer_destination')
            # Do not mutate a caller's shared client's default headers.
            if credential:
                request.headers['Authorization'] = 'Bearer ' + credential
            response = await http.send(request, stream=True, follow_redirects=False)
            try:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        raise PeerError('peer_response_too_large')
                # Redact before the SDK can parse, log or trace an error body.
                clean = redacted_json(content, redact)
                return httpx.Response(response.status_code, content=clean, request=request,
                    headers={key: value for key, value in response.headers.items()
                             if key not in ('content-length', 'content-encoding')})
            finally:
                await response.aclose()

    try:
        guard()
        async with httpx.AsyncClient(transport=BoundTransport(), timeout=30, trust_env=False) as bound:
            async with ClientFactory(ClientConfig(httpx_client=bound, streaming=False)).create(card) as client:
                message = new_text_message(text, role=Role.ROLE_USER)
                message.message_id = message_id
                request = SendMessageRequest(message=message)
                answers = []
                async for event in client.send_message(request):
                    guard()
                    if event.HasField('message'):
                        answers.append(get_message_text(event.message))
                    elif event.HasField('task'):
                        if event.task.status.state != TaskState.TASK_STATE_COMPLETED:
                            raise PeerError('peer_task_not_completed')
                        answers.append(get_message_text(event.task.status.message))
                        answers.extend(part.text for artifact in event.task.artifacts for part in artifact.parts if part.text)
                    if sum(len(answer) for answer in answers) > 24000:
                        raise PeerError('peer_response_too_large')
                if not answers:
                    raise PeerError('peer_response_empty')
                return '\n'.join(answers)
    finally:
        if own:
            await http.aclose()


class PersistedTasks(TaskStore):
    """SDK pagination/filtering over protobuf snapshots in Still's existing DB.

    The SQL extra would add SQLAlchemy solely for this boundary. QuickStore
    already owns transactions; the SDK's public TaskStore is the narrow seam.
    Every handler uses its own peer-bound store, never a caller-supplied owner.
    """
    def __init__(self, peers, peer_id):
        self.peers, self.peer_id = peers, peer_id
        self.key = 'a2a_tasks:' + peer_id

    async def snapshot(self, context):
        memory = InMemoryTaskStore()
        for value in (self.peers.store.config(self.key, {}) or {}).values():
            task = ParseDict(value, Task())
            with self.peers.store.db() as db:
                turn = db.execute('SELECT work_id,revision FROM a2a_turns WHERE peer_id=? AND task_id=?',
                                  (self.peer_id, task.id)).fetchone()
            if turn:
                peer = self.peers.rows().get(self.peer_id, {})
                if turn['revision'] != peer.get('revision'):
                    # A later scope cannot redisclose a previous wider response.
                    task.ClearField('history')
                    task.ClearField('artifacts')
                    task.ClearField('metadata')
                    task.status.ClearField('message')
                    task.status.state = TaskState.TASK_STATE_CANCELED
                else:
                    task.metadata.update({'ownerItems': self.peers.owner_outcomes(self.peer_id, turn['work_id'])})
            # A new process has no executor for an old open task.
            if task.status.state not in (TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED,
                                         TaskState.TASK_STATE_CANCELED, TaskState.TASK_STATE_REJECTED):
                # Live saves/readbacks remain live; startup recovery marks the receipts.
                with self.peers.store.db() as db:
                    turn = db.execute('SELECT state FROM a2a_turns WHERE peer_id=? AND task_id=?',
                                      (self.peer_id, task.id)).fetchone()
                if turn and turn['state'] in ('failed', 'cancelled'):
                    task.status.state = (TaskState.TASK_STATE_CANCELED if turn['state'] == 'cancelled'
                                         else TaskState.TASK_STATE_FAILED)
            await memory.save(task, context)
        return memory

    async def save(self, task, context):
        payload = self.peers.service._redact_known_secrets(json.dumps(MessageToDict(task)))
        with self.peers.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = self.peers.store.config(self.key, {}, db=db) or {}
            rows[task.id] = json.loads(payload)
            self.peers.store.put(self.key, rows, db=db)

    async def get(self, task_id, context):
        return await (await self.snapshot(context)).get(task_id, context)

    async def list(self, params, context):
        return await (await self.snapshot(context)).list(params, context)

    async def delete(self, task_id, context):
        with self.peers.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = self.peers.store.config(self.key, {}, db=db) or {}
            rows.pop(task_id, None)
            self.peers.store.put(self.key, rows, db=db)


class PeerExecutor(AgentExecutor):
    def __init__(self, peers, peer_id):
        self.peers, self.peer_id = peers, peer_id

    async def execute(self, context, event_queue):
        if not context.current_task:
            await event_queue.enqueue_event(Task(id=context.task_id, context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING), history=[context.message]))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        try:
            self.peers.guard(self.peer_id)
            # IDs are protocol correlation, never authority. The peer identity is
            # captured by the authenticated handler, not message role or metadata.
            result = await asyncio.to_thread(self.peers.receive, self.peer_id,
                                             context.message.message_id, context.task_id,
                                             context.get_user_input())
            state = result['state']
            message = new_text_message(result.get('answer') or ('peer_task_' + state))
            if state == 'completed':
                self.peers.guard(self.peer_id, result['revision'])
                await updater.complete(message)
            elif state == 'cancelled':
                await updater.update_status(TaskState.TASK_STATE_CANCELED, message)
            elif state == 'working':
                await updater.reject(new_text_message('peer_request_already_running'))
            else:
                await updater.failed(message)
        except PeerError as exc:
            await updater.reject(new_text_message(str(exc)))

    async def cancel(self, context, event_queue):
        with self.peers.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE jobs SET status='cancelled',error='peer_cancelled' WHERE id IN "
                       "(SELECT work_id FROM a2a_turns WHERE peer_id=? AND task_id=? AND state='working')",
                       (self.peer_id, context.task_id))
            db.execute("UPDATE a2a_turns SET state='cancelled' WHERE peer_id=? AND task_id=? AND state='working'",
                       (self.peer_id, context.task_id))
        await TaskUpdater(event_queue, context.task_id, context.context_id).update_status(TaskState.TASK_STATE_CANCELED)


class PeerHandler(DefaultRequestHandler):
    """Preflight SDK requests before allocating a task for a replay or refusal.

    Atomic admission still belongs to the Work transaction. This public handler
    seam prevents repeated message IDs from growing an unrelated SDK task cache.
    """
    def __init__(self, peers, peer_id, card):
        self.peers, self.peer_id = peers, peer_id
        self.peer_tasks = PersistedTasks(peers, peer_id)
        super().__init__(PeerExecutor(peers, peer_id), self.peer_tasks, card)

    async def preflight(self, params, context):
        row = self.peers.peer(self.peer_id)
        with self.peers.store.db() as db:
            old = db.execute('SELECT * FROM a2a_turns WHERE peer_id=? AND direction=\'inbound\' '
                'AND (message_id=? OR task_id=?)',
                (self.peer_id, params.message.message_id, params.message.task_id or None)).fetchone()
            refusal = self.peers.admission_refusal(self.peer_id, row, db)
        if old:
            if old['revision'] != row['revision']:
                refusal = 'peer_revision_changed'
            elif old['question'] != get_message_text(params.message):
                refusal = 'duplicate_peer_request_conflict'
            elif old['state'] == 'working':
                refusal = 'peer_request_already_running'
            else:
                saved = await self.peer_tasks.get(old['task_id'], context)
                if saved:
                    return saved
                # A crash may leave a durable admission without an SDK snapshot.
                refusal = 'peer_interrupted'
        if not params.message.message_id:
            refusal = 'invalid_peer_message'
        if refusal:
            return Task(id=params.message.task_id or str(uuid.uuid4()),
                context_id=params.message.context_id or str(uuid.uuid4()),
                status=TaskStatus(state=TaskState.TASK_STATE_REJECTED, message=new_text_message(refusal)))
        return None

    async def on_message_send(self, params, context):
        return await self.preflight(params, context) or await super().on_message_send(params, context)

    async def on_message_send_stream(self, params, context):
        result = await self.preflight(params, context)
        if result:
            yield result
        else:
            async for event in super().on_message_send_stream(params, context):
                yield event


def application(peers, url='http://127.0.0.1/'):
    protect_sdk_logs(peers.service)
    peers.initialize()
    card = AgentCard(name='Still', version='1', description='Owner-controlled peer conversation',
        supported_interfaces=[AgentInterface(url=url, protocol_binding='JSONRPC', protocol_version='1.0')],
        default_input_modes=['text/plain'], default_output_modes=['text/plain'],
        capabilities=AgentCapabilities(streaming=False),
        skills=[AgentSkill(id='conversation', name='Conversation',
                           description='Answer a registered counterpart within its disclosure scope', tags=['conversation'])],
        security_schemes={'peer': SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme='bearer'))},
        security_requirements=[SecurityRequirement(schemes={'peer': {}})])
    apps = {}

    class Context(ServerCallContextBuilder):
        def build(self, request):
            # Deliberately no headers, tokens, owner pairing or owner-act objects.
            return ServerCallContext(state={'headers': {'a2a-version': request.headers.get('a2a-version', '')}})

    class AuthenticatedPeers:
        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http':
                return
            try:
                headers = dict(scope.get('headers', []))
                peer_id = peers.authenticate(headers.get(b'authorization', b'').decode('ascii'))
            except Exception:
                return await JSONResponse({'error': 'unauthenticated_peer'}, status_code=401)(scope, receive, send)
            body = bytearray()
            while True:
                event = await receive()
                if event['type'] == 'http.disconnect':
                    return
                body.extend(event.get('body', b''))
                if len(body) > MAX_BYTES:
                    return await JSONResponse({'error': 'peer_request_too_large'}, status_code=413)(scope, receive, send)
                if not event.get('more_body'):
                    break
            try:
                cleaned = redacted_json(body, peers.service._redact_known_secrets) if body else b''
            except Exception:
                return await JSONResponse({'error': 'invalid_peer_message'}, status_code=400)(scope, receive, send)
            sent = False
            async def redacted_receive():
                nonlocal sent
                if sent:
                    return await receive()
                sent = True
                return {'type': 'http.request', 'body': cleaned}
            safe_scope = {**scope, 'headers': [(key, value) for key, value in scope.get('headers', [])
                                              if key not in (b'authorization', b'cookie', b'content-length')]}
            if peer_id not in apps:
                handler = PeerHandler(peers, peer_id, card)
                apps[peer_id] = Starlette(routes=[*create_jsonrpc_routes(handler, '/', Context()),
                                                  *create_agent_card_routes(card)])
            await apps[peer_id](safe_scope, redacted_receive, send)

    return AuthenticatedPeers()
