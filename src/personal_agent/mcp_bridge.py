"""Minimal stdio MCP bridge; Still, never the engine, owns tool execution.

The host-only MCP SDK owns stdio framing and JSON-RPC response correlation.
Still retains handshake negotiation, the supported method/tool profiles,
serialized execution and Work/Grant/secret/effect/Event/Evidence callbacks.
The isolated engine bridge keeps its separate envelope contract.
"""
import argparse
import json
import sys
import time
import uuid

from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, LATEST_HANDSHAKE_VERSION

from .agent_runtime import (ENGINE_UNMEDIATED, OWNER_STATE_ACTIONS, TRANSIENT_FAILURE_TEXT,
                            Capabilities, ToolError, WorkBudget, WorkLedger, attempted_outbound, classify_failure, declared_effect,
                            claim_owner_steers, evidence_summary, lookup_sources, outbound_evidence, observed_field, progress_step, ran_field, worker_result,
                            recorded_private_sources, split_status, work_source_records,
                            work_stop_requested)
from .current_context import redact_known_secrets
from .providers import ProviderError
from .bounded_execution import (AgentOSMcpTools, BOUNDED_PROFILE, HOST_CLI_PROFILES, STRICT_PROFILE, ExecutionError,  # noqa: F401
                                profile_actions, redact_reason, turn_actions)
from .skills import SkillBinding, SkillLibrary
from .connection_slots import relayed as relayed_slots
from .cli_browser_relay import (RELAYED_INFORMATION_USE, RELAYED_LOCATION_REQUEST, RELAYED_PREPARATIONS, RELAYED_SETTINGS,
                                SECRET_REDACTION, RelayClient, unused_browser_factory)
from .context_observations import answerable_work
from .local_tools import LocalTools
from .search_providers import ProviderRegistry
from .quickstart_store import QuickStore


def negotiated_protocol_version(offered):
    """Return the handshake revision this bridge will speak with the peer.

    The registry comes from ``mcp_types`` so the supported set tracks the
    official SDK instead of a hand-maintained literal.  ``initialize`` is a
    handshake method, so the counter-offer is the newest *handshake* revision;
    ``LATEST_PROTOCOL_VERSION`` may name a stateless per-request revision this
    bridge does not implement.  An unknown or non-string offer is never echoed.
    """
    if isinstance(offered, str) and offered in HANDSHAKE_PROTOCOL_VERSIONS:
        return offered
    return LATEST_HANDSHAKE_VERSION


class _AllInlineMethods(frozenset):
    """Make every request but ``tools/call`` use the dispatcher's documented inline path.

    The public API accepts a frozenset and checks membership.  Still must
    serialize supported and rejected requests alike: otherwise the SDK cancels
    a final unknown-method task as soon as stdin reaches EOF, before its error
    is written.  The exact SDK pin and contract tests cover this small wildcard
    adaptation until upstream offers an explicit all-inline mode.  #386: a
    ``tools/call`` is its own task so independent reads can overlap; ``_CallOrder``
    keeps every other call in arrival order.
    """

    def __contains__(self, value):
        return value != 'tools/call'


class _CallOrder:
    """Arrival-ordered ``tools/call`` execution where only effect-free reads overlap (#386, #16).

    Every call passes one FIFO gate in arrival order.  A read then runs at once, at most
    ``CONCURRENT_READ_LIMIT`` together; any other call holds the gate until the reads that
    arrived before it have finished and it has run, so nothing after it starts first.  As in
    #16, a page read whose scope includes search results and a search keep their arrival order
    with each other, so a page read sees exactly the searches before it.
    Each call runs in a worker thread that a cancellation waits for, so a started call always
    writes its terminal record.
    """

    def __init__(self):
        import anyio
        from .agent_runtime import CONCURRENT_READ_LIMIT
        self.gate = anyio.Lock()
        self.limiter = anyio.CapacityLimiter(CONCURRENT_READ_LIMIT)
        self.settled = anyio.Condition()
        self.reads = 0
        # Searches and search-scoped page reads admitted and not finished.  Each waits only for the
        # other kind admitted before it, so a page read sees exactly the searches that preceded it.
        self.searches, self.pages = set(), set()
        # Ids of calls read off the wire whose answer is not yet written: EOF waits for them
        # (``_AnsweredBeforeEof``).  A call the peer cancelled is never answered, so it leaves too.
        self.pending = set()

    async def settle(self, request_id):
        async with self.settled:
            self.pending.discard(request_id)
            self.settled.notify_all()

    async def all_answered(self):
        async with self.settled:
            while self.pending:
                await self.settled.wait()

    async def run(self, call, read, search=False, after_searches=False):
        import anyio
        if read:
            token = object()
            async with self.gate:
                self.reads += 1
                earlier = (set(self.pages) if search else set(self.searches) if after_searches else set())
                (self.searches if search else self.pages if after_searches else set()).add(token)
            try:
                if earlier:
                    async with self.settled:
                        while earlier & (self.searches | self.pages):
                            await self.settled.wait()
                async with self.limiter:
                    return await anyio.to_thread.run_sync(call)
            finally:
                async with self.settled:
                    self.reads -= 1
                    self.searches.discard(token)
                    self.pages.discard(token)
                    self.settled.notify_all()
        async with self.gate:
            async with self.settled:
                while self.reads:
                    await self.settled.wait()
            return await anyio.to_thread.run_sync(call)


def _request_key(value):
    return json.dumps(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else None


class _AnsweredBeforeEof:
    """The read stream, with EOF held back until every ``tools/call`` read off it is answered (#386).

    Inline calls were answered before the next message was read, so EOF never cut one off.  A
    call that is its own task is noted when it is read and settled when its answer is written
    (``_NotingAnswers``) or the peer cancels it; the dispatcher sees EOF only then, which would
    otherwise cancel it and answer "Connection closed".
    """

    def __init__(self, stream, order):
        self._stream, self._order = stream, order

    def __getattr__(self, name):
        return getattr(self._stream, name)

    async def __aenter__(self):
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._stream.__aexit__(*exc)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            item = await self._stream.__anext__()
        except StopAsyncIteration:
            await self._order.all_answered()
            raise
        message = getattr(item, 'message', None)
        method = getattr(message, 'method', None)
        if method == 'tools/call' and _request_key(getattr(message, 'id', None)) is not None:
            self._order.pending.add(_request_key(message.id))
        elif method == 'notifications/cancelled':
            params = getattr(message, 'params', None)
            key = _request_key(params.get('requestId')) if isinstance(params, dict) else None
            if key is not None:
                await self._order.settle(key)
        return item


class _NotingAnswers:
    """The write stream, settling a noted call once its answer has been handed to the transport (#386)."""

    def __init__(self, stream, order):
        self._stream, self._order = stream, order

    def __getattr__(self, name):
        return getattr(self._stream, name)

    async def __aenter__(self):
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._stream.__aexit__(*exc)

    async def send(self, item):
        await self._stream.send(item)
        message = getattr(item, 'message', None)
        key = _request_key(getattr(message, 'id', None)) if getattr(message, 'method', None) is None else None
        if key is not None and key in self._order.pending:
            await self._order.settle(key)


def _serve_stdio(handle, concurrent=None):
    """Adapt the exact-pinned SDK's public dispatcher to Still callbacks.

    Imports stay here so importing the host facade does not require the SDK
    in the isolated engine's base package. No SDK Server/session registration
    is used: it would add methods and require a different initialize shape.
    """
    import anyio
    from mcp import MCPError
    from mcp.server.stdio import stdio_server
    from mcp.shared.jsonrpc_dispatcher import JSONRPCDispatcher

    order = []

    def answer(method, params):
        try:
            return handle(method, params)
        except _Rejected as exc:
            raise MCPError(**exc.rpc) from None
        except Exception:
            # The dispatcher otherwise logs the exception and sends its text,
            # which can contain owner payloads, destinations or credentials.
            raise MCPError(code=-32602, message='Still MCP request rejected.') from None

    async def on_request(context, method, params):
        if method == 'notifications/initialized':
            # This is notification-only; do not turn a malformed request
            # into a newly supported request method.
            raise MCPError(code=-32601, message='Method not found.')
        if method != 'tools/call':
            # Inline, so the read loop waits: a later request never overtakes a call before it.
            await order[0].all_answered()
            return answer(method, params)
        try:
            kind = concurrent(params) if callable(concurrent) else None
        except Exception:
            kind = None
        return await order[0].run(lambda: answer(method, params), bool(kind), search=kind == 'search',
                                  after_searches=kind == 'after-searches')

    def handle_notification(method, params):
        # The old bridge also executed supported methods without an id.
        # Keep notification work in the read loop so EOF cannot cancel a
        # normalized no-id call before its durable evidence is recorded.
        try:
            handle(method, params)
        except Exception:
            pass

    async def on_notify(context, method, params):
        # Fallback for a future dispatcher that bypasses the interceptor.
        handle_notification(method, params)

    def on_notify_intercept(method, params):
        handle_notification(method, params)
        return True

    async def malformed_input(exc):
        # Invalid wire input has no trusted request id. Drop it without the
        # SDK's default debug log of the parser exception/payload, and never
        # attribute it to a previously consumed request.
        pass

    async def run():
        # In-process callers may inject text streams (including StringIO).
        # Real process stdio uses the SDK's fd claim and UTF-8 wrappers, keeping
        # stray handler/child output off the protocol wire.
        stdin = None if hasattr(sys.stdin, 'buffer') else anyio.wrap_file(sys.stdin)
        stdout = None if hasattr(sys.stdout, 'buffer') else anyio.wrap_file(sys.stdout)
        order.append(_CallOrder())
        async with stdio_server(stdin=stdin, stdout=stdout) as (read_stream, write_stream):
            dispatcher = JSONRPCDispatcher(
                _AnsweredBeforeEof(read_stream, order[0]), _NotingAnswers(write_stream, order[0]),
                inline_methods=_AllInlineMethods(),
                peer_cancel_mode='signal', on_stream_exception=malformed_input,
            )
            # Inline callbacks have no awaits during tool execution.  #386: a tool
            # call is ordered by ``_CallOrder`` instead: only effect-free reads overlap,
            # each spends the shared Work budget under its lock and pairs its records by
            # call id; nothing else can overtake a predecessor. Owner Stop remains the Work gate.
            await dispatcher.run(on_request, on_notify, on_notify_intercept)

    anyio.run(run)


def _provenance(labels):
    """Private-source labels handed over by the Still process for this Work.

    The bridge runs as a separate process, so the Work's private-source
    provenance (same-turn sources and conversation history) must be passed
    in explicitly.  Any label -- known or not -- is kept.  #826: the labels
    are a record for the Work's information-use audit, not an egress gate.
    """
    return {str(label) for label in labels or () if str(label).strip()}


def _work_running(store, job_id):
    """The bridge acts for exactly one Work, and only while it is running.

    Discovery (``tools/list``) grants nothing; every call rechecks that the
    Work named on the command line exists in this owner store and is still
    ``running``.  A finished, interrupted or foreign Work id is refused, so a
    bridge that outlives its turn cannot keep acting for it.
    """
    # Time-of-check bound: the check runs before each call, so a Work that
    # ends *during* a call may still see that one in-flight call finish.  It
    # is bounded by that call's own deadlines (search 15 s, weather 10 s + 15 s,
    # each research page read 12 s) and no further call starts.
    job = store.job(job_id) if isinstance(job_id, str) and job_id else None
    return bool(job) and job.get('status') == 'running'


def _recorded_private_sources(store, job_id, tools=None):
    """Private-source labels this Work already carries in durable state.

    Taint used to live only in one bridge process: a second bridge started for
    the same running Work (a CLI restarting its MCP server) received only the
    argv ``--provenance`` labels and forgot a ``list_notes`` the first one had
    served.  The bridge rehydrates on start and before every call from the
    Work's successful ``tool_events`` (``recorded_private_sources``) and, since
    #605, from the Work's source record written before model use, which also
    carries the history-window sources the Work was shown.  A missing record
    adds nothing here: the host always passes the same labels on argv.
    """
    labels = recorded_private_sources(store, job_id, tools)
    record = work_source_records(store).get(job_id)
    if isinstance(record, list):
        # A CLI's unmediated-read label concerns its *reply* for later Works,
        # not this Work's own inputs (#605 F1).
        labels |= {str(label) for label in record if str(label).strip() and str(label) != ENGINE_UNMEDIATED}
    return labels


def _lookup_sources(store, job_id):
    """The same lookup resolver the host uses (#605): the running-Work binding and the Work's saved values."""
    return lambda: lookup_sources(store, job_id)


def approved_public_pages(store):
    """The owner's current approved public page addresses (#826).

    The exact normalized URLs the owner approved in Settings; read on every
    use, so a revoked approval refuses a later read.  On this route the
    approval is the owner's page grant itself; it is not bound to the
    direct-API model's fingerprint.
    """
    saved = store.config('public_page_sharing', {})
    if not isinstance(saved, dict) or saved.get('approved') is not True:
        return []
    return [url for url in saved.get('urls') or [] if isinstance(url, str)]


#: The Work this bridge serves has ended; a typed tool error, not a protocol one.
WORK_NOT_RUNNING = '이 작업은 더 이상 실행 중이 아니어서 도구를 실행하지 않았습니다.'
#: Returned for an untyped tool failure whose own text is not Still's.
TOOL_FAILED_TEXT = 'Still 도구가 이 요청을 처리하지 못했습니다. 다른 방법을 고르거나 소유자에게 필요한 정보를 물어보세요.'


class _Rejected(ExecutionError):
    """A JSON-RPC protocol error with its own code and fixed message (#607 AX-06)."""
    def __init__(self, code, message):
        super().__init__(message)
        self.rpc = {'code': code, 'message': message}


def tool_error_result(exc, action):
    """An MCP tool-result error carrying #607's typed recovery metadata.

    Normal tool failures are tool results (``isError``), not JSON-RPC protocol
    errors, so the CLI can tell a transient read failure from a denial, a
    setup need, the Work's Stop/deadline or an unknown effect.  The text is
    Still's own: a typed ``ToolError`` or #605 input request verbatim, a fixed
    text otherwise (an exception's own text may quote a URL or credential).
    """
    code, retry, effect = classify_failure(exc, action)
    if code == 'input_required':
        # #605 D2: Still's own fixed texts reach the CLI verbatim.
        text = str(exc)
    elif isinstance(exc, ToolError):
        text = str(exc)
    elif code == 'transient_failure':
        text = TRANSIENT_FAILURE_TEXT
    else:
        text = TOOL_FAILED_TEXT
    typed = {'code': code, 'retry': retry, 'effect': effect, **ran_field(exc), **observed_field(exc)}
    if getattr(exc, 'requires', None):
        typed['requires'] = exc.requires
    return {'content': [{'type': 'text', 'text': text}], 'structuredContent': typed, 'isError': True}, typed


def unexpected_error_result(exc, action):
    """The typed tool result of an exception no tool layer maps (#735).

    Its class name only, never its text (it may quote a URL or credential).
    An effect-free read failed with no effect; any other action may have
    acted before it failed, so its effect is unknown and it is never replayed.
    """
    from .agent_runtime import EFFECT_FREE_READS
    read = action in EFFECT_FREE_READS
    typed = {'code': 'tool_failed', 'retry': 'permanent' if read else 'never', 'effect': 'none' if read else 'unknown',
             'exception': type(exc).__name__}
    return {'content': [{'type': 'text', 'text': TOOL_FAILED_TEXT}], 'structuredContent': typed, 'isError': True}, typed


def _with_steer(result, store, job_id, record):
    """#999: an owner message for this running Work rides on the tool result the worker reads next."""
    steer = claim_owner_steers(store, job_id, record)
    if steer and isinstance(result, dict) and isinstance(result.get('content'), list):
        result['content'].append({'type':'text','text':steer})
    return result


#: #1197: Claude Code's permission prompt for the owner's AI connector tools.
#: Listed only with ``--ai-connections``; never pre-approved, so a model call
#: to it is itself a permission prompt, which this tool refuses.
CONNECTOR_PERMISSION = 'connector_permission'
def _permission_text(decision):
    """Exactly one text block, as Claude Code requires of a permission prompt tool (observed, 2.1.280)."""
    return {'content': [{'type': 'text', 'text': json.dumps(decision, ensure_ascii=False)}]}


def bridge_secret_redactor(store, relay):
    """The stored-secret pass for text this bridge records or returns (#718), done where the key is (#369).

    With the service's relay it is the service's own ``redact_known_secrets``: encrypted API slot values
    need the owner's Keychain key, which this process cannot read when the CLI started it.  A failed
    call raises, so the caller withholds the text as before.  Without a relay it runs here.
    """
    if relay is None:
        return lambda text: redact_known_secrets(store, text)
    return lambda text: relay.call(SECRET_REDACTION, {'text': str(text)})


def serve(data, job_id, provenance=(), native_search=False, profile=BOUNDED_PROFILE, browser_relay=None,
          search_off_reason='', relay_browser=True, skills=(), ai_connections=False, connection_slots=(),
          session_steers=False):
    """Serve one Work's Still tools over stdio for the route profile the host named (#701).

    ``profile`` is ``trusted-local`` or ``strict-isolated``; anything else
    serves the strict set.  ``browser_relay`` (trusted-local only) is the
    service's relay directory: the browser tools are then listed and every
    browser call is executed by the service (``cli_browser_relay``); without
    it no browser tool is offered.  ``skills`` (trusted-local only, #961) are the
    ``package/name@digest`` refs of the host Work's skill binding; without any
    no skill tool is offered.  ``session_steers`` (#308 S2): the host writes the
    owner's steers to the CLI session's stdin, so tool results carry none.
    """
    profile = profile if profile in HOST_CLI_PROFILES else STRICT_PROFILE
    relay = RelayClient(browser_relay) if browser_relay and profile == BOUNDED_PROFILE else None
    store = QuickStore(data)
    def record(tool, status, detail):
        with store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)', (job_id,tool,status,detail,time.time()))
    # #604: the Work's allowed actions are the bounded CLI profile; names and
    # schemas come from Capabilities.definitions(), never a bridge-local list.
    # #826: the connected-folder documents and approved public pages the profile offers are
    # read here, within the folder grants (``Capabilities.roots``/``resolve_file``) and
    # the owner's page approval (``approved_public_pages``) the tools check on every call.
    capabilities = Capabilities(store, None, {}, '', job_id, record, network=LocalTools(providers=ProviderRegistry.from_store(store)), document_access=True,
                                # #23: strict-isolated reads only this Work's search results, never the owner's page list.
                                public_page_scope=((lambda: approved_public_pages(store)) if profile == BOUNDED_PROFILE
                                                   else ()),
                                # #774: owner-state tools run only in the service; without its relay they are not offered.
                                allowed_tools=(set(turn_actions(profile, native_search and profile == BOUNDED_PROFILE))
                                               - (set() if relay is not None else set(OWNER_STATE_ACTIONS))),
                                # #701: a placeholder that lists the browser tools; their calls go to the service.
                                browser=unused_browser_factory if relay is not None and relay_browser else None,
                                # #774: placeholders that list schedule_preparation and ask_location; their calls go to the service.
                                preparations=RELAYED_PREPARATIONS if relay is not None else None,
                                # ask_location only for a Work from the paired Telegram chat, as on the direct route.
                                location_request=(RELAYED_LOCATION_REQUEST if relay is not None
                                                  and answerable_work(store.job(job_id)) else None),
                                # #814: a placeholder that lists the settings tools; their calls go to the service.
                                settings=RELAYED_SETTINGS if relay is not None else None,
                                # #826: a placeholder that lists information_use; its calls go to the service.
                                information_use=RELAYED_INFORMATION_USE if relay is not None else None,
                                # #1325: token-less placeholders that list api_request for the Work's connections;
                                # its calls go to the service, which holds the tokens.
                                connections=((lambda: relayed_slots(connection_slots))
                                             if relay is not None and connection_slots else None),
                                # #961: the exact skill revisions the host bound to this Work, read here from the store.
                                skills=(SkillBinding.from_refs(SkillLibrary(store), skills)
                                        if skills and profile == BOUNDED_PROFILE else None),
                                inherited_provenance=_provenance(provenance),
                                lookup_sources=_lookup_sources(store, job_id),
                                # #607 AX-10: the same durable attempt count and
                                # deadline as the host serving this Work.
                                budget=WorkBudget(stop=lambda: work_stop_requested(store, job_id),
                                                  ledger=WorkLedger(store, job_id)),
                                # #718: the same stored-secret pass as the host, for recorded step text.
                                secret_redactor=bridge_secret_redactor(store, relay))
    capabilities.private_provenance.update(_recorded_private_sources(store, job_id, capabilities.tools))
    tools = AgentOSMcpTools(capabilities, native_search=native_search and profile == BOUNDED_PROFILE)
    tools.PROFILE = profile
    tools.relay = relay
    tools.native_search_reason = str(search_off_reason or '')

    def handle(method, params):
        if method == 'initialize':
            offered = params.get('protocolVersion') if isinstance(params, dict) else None
            result = {'protocolVersion':negotiated_protocol_version(offered),'capabilities':{'tools':{}},'serverInfo':{'name':'agentos','version':'1'}}
        elif method == 'tools/list':
            from .agent_runtime import CONNECTOR_PERMISSION_DEFINITION
            from .bounded_execution import mcp_tool
            # Never read-only: a client must not auto-approve a direct call to it (review on #1197).
            extra = [mcp_tool(CONNECTOR_PERMISSION_DEFINITION, 'bounded_write', profile)] if ai_connections and relay else []
            result = {'tools': tools.definitions() + extra}
        elif method == 'tools/call' and ai_connections and relay and (params or {}).get('name') == CONNECTOR_PERMISSION:
            # #1197: the service decides (reads allowed, everything else refused for now)
            # and records the call; nothing here is shown to the model as a tool result.
            arguments = (params or {}).get('arguments') or {}
            try:
                decision = relay.call(CONNECTOR_PERMISSION, {'tool_name': arguments.get('tool_name'),
                                                             'input': arguments.get('input')})
            except Exception:
                decision = None
            if not isinstance(decision, dict) or decision.get('behavior') not in ('allow', 'deny'):
                decision = {'behavior': 'deny', 'message': 'Still could not decide this action, so it was not run.'}
            return _permission_text(decision)
        elif method == 'tools/call':
            params = params or {}; name = params.get('name')
            # Host-generated identity pairs durable start/terminal events across
            # process restarts without trusting or storing a peer's RPC id.
            call_id = uuid.uuid4().hex
            # A CLI-chosen name is stored only when it is an offered tool.
            listed = name if isinstance(name, str) and name in capabilities.allowed_tools else 'unlisted'
            action = (capabilities.tools.get(listed) or {}).get('host_action') if listed != 'unlisted' else None
            if listed == 'unlisted' or name not in tools._offered():
                record(listed, 'failed', json.dumps({'scope':'subscription-mcp-bridge','call_id':call_id,'code':'unknown_tool','retry':'permanent',
                                                     'effect':'none','ran':False,'error':'Unknown Still MCP tool.'}))
                raise _Rejected(-32602, 'Unknown Still MCP tool.')
            # #787: a browser call's declared effect, recorded on every event of the call.
            declared, arguments = {}, {}
            try:
                if not _work_running(store, job_id):
                    raise ToolError(WORK_NOT_RUNNING, 'stopped', ran=False)
                capabilities.private_provenance.update(_recorded_private_sources(store, job_id, capabilities.tools))
                arguments, status = split_status(params.get('arguments', {}))
                declared = declared_effect(action, arguments)
                if action:
                    # #607: a call is durably in flight before it runs, so a
                    # crash mid-call leaves an attempted (possibly effectful)
                    # action that retry/resume refuse to replay blindly.
                    # #718: with the call's bounded, redacted display step.
                    record(listed, 'running', json.dumps({'scope':'subscription-mcp-bridge','call_id':call_id,'host_action':action,
                                                          'step':progress_step(action, arguments, status, capabilities.judgment_text),
                                                          **declared}, ensure_ascii=False))
                value = tools.call(name, arguments)
            except ExecutionError as exc:
                # Invalid arguments stay a protocol error (MCP: invalid
                # params).  Redacted: the reason, never the arguments.
                record(listed, 'failed',
                       json.dumps({'scope':'subscription-mcp-bridge','call_id':call_id,'code':'invalid_arguments','retry':'permanent','effect':'none',
                                   **ran_field(exc), 'error':redact_reason(str(exc))}, ensure_ascii=False))
                raise _Rejected(-32602, 'Invalid Still MCP tool arguments.') from None
            except (ValueError, TypeError, OSError, ProviderError) as exc:
                # #607 AX-06: a normal tool failure is a typed tool result.
                result, typed = tool_error_result(exc, action)
                # #167: a call that failed after it was handed to a transport may still have carried a held value out.
                record(listed, 'failed',
                       json.dumps({'scope':'subscription-mcp-bridge','call_id':call_id, **({'host_action':action} if action else {}), **typed,
                                   'error':redact_reason(str(exc)), **declared,
                                   **attempted_outbound(capabilities, action, arguments, exc)}, ensure_ascii=False))
                if not session_steers:_with_steer(result, store, job_id, record)
                return result
            except Exception as exc:
                # #735: any other exception inside one call (an EOFError from a
                # helper process, an HTTP-library error type no layer maps) is a
                # typed failure of that call.  It used to end the bridge
                # process: the CLI saw its MCP connection close mid-call and
                # the Work recorded only a call that never completed.
                result, typed = unexpected_error_result(exc, action)
                record(listed, 'failed',
                       json.dumps({'scope':'subscription-mcp-bridge','call_id':call_id, **({'host_action':action} if action else {}), **typed,
                                   'error':redact_reason(TOOL_FAILED_TEXT), **declared,
                                   **attempted_outbound(capabilities, action, arguments, exc)}, ensure_ascii=False))
                if not session_steers:_with_steer(result, store, job_id, record)
                return result
            # The same redacted Evidence the direct route records
            # (sources, attempted/failed URLs, counts), never the payload.
            host_action = capabilities.tools[name]['host_action']
            record(name, 'succeeded', json.dumps({'scope':'subscription-mcp-bridge','call_id':call_id,'host_action':host_action,
                                                   # #167: held-value match labels (display only).
                                                   'evidence':{**evidence_summary(host_action, value),
                                                               **outbound_evidence(capabilities, host_action, arguments, value)},
                                                   **declared},
                                                  ensure_ascii=False))
            # #836: the worker reads a held memory write as the owner's one-tap ask, nothing more.
            result = {'content':[{'type':'text','text':json.dumps(worker_result(host_action, value), ensure_ascii=False)}]}
            if not session_steers:_with_steer(result, store, job_id, record)
        elif method == 'notifications/initialized': return {}
        else: raise _Rejected(-32601, 'Method not found.')
        return result

    def concurrent(params):
        """#386: how this call may overlap others: None (it runs alone, in order), ``'search'``,
        ``'after-searches'`` (#16: a page read waits for searches before it) or ``'read'``."""
        from .agent_runtime import SEARCH_BACKED_ACTIONS, concurrent_read, reads_search_results
        name = (params or {}).get('name')
        if not isinstance(name, str) or name not in capabilities.allowed_tools or name not in tools._offered():
            return None
        arguments = (params or {}).get('arguments')
        action = (capabilities.tools.get(name) or {}).get('host_action')
        if not concurrent_read(action, arguments if isinstance(arguments, dict) else {}):
            return None
        return ('search' if action in SEARCH_BACKED_ACTIONS else
                'after-searches' if reads_search_results(action) else 'read')

    _serve_stdio(handle, concurrent)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--data',required=True); parser.add_argument('--job',required=True)
    parser.add_argument('--provenance',action='append',default=[])
    parser.add_argument('--native-search',action='store_true')
    # #701: the host names the route profile (absent: the historical trusted-local set, as the
    # doctor's probe runs it; unknown: the strict set).  Only a service-created relay, handed to a
    # trusted-local bridge, ever lists a browser tool.
    parser.add_argument('--profile',default=BOUNDED_PROFILE)
    parser.add_argument('--search-off-reason',default='')
    parser.add_argument('--browser-relay',default=None)
    parser.add_argument('--relay-no-browser',action='store_true')
    parser.add_argument('--skill',action='append',default=[])
    parser.add_argument('--ai-connections',action='store_true')
    parser.add_argument('--connection-slot',action='append',default=[])
    # #308 S2: the host's Claude Code session takes the owner's steers on its own stdin.
    parser.add_argument('--session-steers',action='store_true')
    args=parser.parse_args()
    serve(args.data, args.job, args.provenance, native_search=args.native_search, profile=args.profile,
          browser_relay=args.browser_relay, search_off_reason=args.search_off_reason,
          relay_browser=not args.relay_no_browser, skills=args.skill, ai_connections=args.ai_connections,
          connection_slots=args.connection_slot, session_steers=args.session_steers)
