"""#651 Phase 0: the installed SDK crosses its real HTTP/protobuf boundary.

Fixture evidence only; both transports run the same SDK handler and executor.
The optional extra must not make a base installation fail test collection.
"""

import asyncio
import socket
import threading
import time

import pytest

pytest.importorskip('a2a')
httpx = pytest.importorskip('httpx')
uvicorn = pytest.importorskip('uvicorn')
pytest.importorskip('starlette')

from a2a.client import ClientConfig, ClientFactory
from a2a.helpers.proto_helpers import get_message_text, new_text_message
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import AgentCard, AgentInterface, AgentCapabilities, AgentSkill, Role, SendMessageRequest
from starlette.applications import Starlette


def card(url):
    return AgentCard(
        name='Fixture counterpart', description='Model-free compatibility check', version='1',
        supported_interfaces=[AgentInterface(url=url, protocol_binding='JSONRPC', protocol_version='1.0')],
        default_input_modes=['text/plain'], default_output_modes=['text/plain'],
        capabilities=AgentCapabilities(streaming=False),
        skills=[AgentSkill(id='echo', name='Echo', description='Model-free echo', tags=['fixture'])],
    )


def application(agent_card, rpc_url='/'):
    class Echo(AgentExecutor):
        async def execute(self, context, event_queue):
            await event_queue.enqueue_event(new_text_message('answer: ' + context.get_user_input()))

        async def cancel(self, context, event_queue):
            raise NotImplementedError

    handler = DefaultRequestHandler(Echo(), InMemoryTaskStore(), agent_card)
    return Starlette(routes=[*create_jsonrpc_routes(handler, rpc_url), *create_agent_card_routes(agent_card)])


async def exchange(agent_card, transport=None):
    # Still's existing HTTP stack must coexist with the SDK's separate httpx.
    import httpx2
    with httpx2.Client() as existing_client:
        assert existing_client is not None
        if transport is None:
            # On the real socket, exercise both installed HTTP transports,
            # not just simultaneous imports/client construction.
            url = agent_card.supported_interfaces[0].url.rstrip('/') + '/.well-known/agent-card.json'
            response = existing_client.get(url, timeout=5)
            assert response.status_code == 200
            assert response.json()['name'] == agent_card.name
        async with httpx.AsyncClient(transport=transport, timeout=5, trust_env=False) as http:
            async with ClientFactory(ClientConfig(httpx_client=http, streaming=False)).create(agent_card) as client:
                request = SendMessageRequest(message=new_text_message('hello', role=Role.ROLE_USER))
                responses = [event async for event in client.send_message(request)]
                assert len(responses) == 1
                assert get_message_text(responses[0].message) == 'answer: hello'


def test_sdk_asgi_compatibility():
    agent_card = card('http://127.0.0.1/')
    asyncio.run(exchange(agent_card, httpx.ASGITransport(app=application(agent_card))))


def test_sdk_real_loopback_compatibility():
    sock = socket.socket()
    try:
        sock.bind(('127.0.0.1', 0))
    except PermissionError:
        sock.close()
        pytest.skip('sandbox forbids loopback bind; run this test outside the sandbox')
    sock.listen()
    agent_card = card(f'http://127.0.0.1:{sock.getsockname()[1]}/')
    server = uvicorn.Server(uvicorn.Config(application(agent_card), log_level='critical', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, 'SDK fixture server did not start'
        asyncio.run(exchange(agent_card))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()
