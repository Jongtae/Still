"""A subscription-CLI rubric judge for sweeps (#1036); no API key, no USD.

Adapt: Still's own shipped judgment route, ``decision_routes`` and
``SubscriptionCliDecisionEngine.structured`` (the engine the #855 matrix runs drive), on a
sandbox copy of a sanitized seed, through the judgments harness helpers (``service_factory``,
``pin_model``, ``requalify_route``, ``CallCap``). The rubric judge's JSON reply travels as one
text field of a structured judgment, so the shipped isolation (tool-less, read-only, no user
config) and qualification apply unchanged. Nothing here calls a provider API.

Select it with ``--judge subscription-cli:codex[:<model>[:<effort>]]``. ``--max-judge-calls`` caps the calls.
"""
import asyncio
import threading
from types import SimpleNamespace

from . import judgments as j
from .paths import SRC, eval_home

PREFIX = 'subscription-cli:'
SCHEMA = {'type': 'object', 'properties': {'reply': {'type': 'string'}}, 'required': ['reply'],
          'additionalProperties': False}


def is_cli_judge(judge):
    return isinstance(judge, str) and judge.startswith(PREFIX)


def parse_spec(judge):
    engine, _, rest = judge[len(PREFIX):].partition(':')
    if engine != 'codex':
        raise ValueError('The subscription-CLI judge supports the codex route only')
    model, _, effort = rest.partition(':')
    return engine, model or None, effort or None


class CallBudget:
    """The ``JudgeBudget`` seam in calls: reserve one, settle at no USD."""

    def __init__(self, max_calls):
        self.max_calls, self.used = max_calls, 0
        self._lock = threading.Lock()

    def reserve(self, input_tokens, output_tokens):
        with self._lock:
            if self.used >= self.max_calls:
                return None
            self.used += 1
            return 0.0

    def settle(self, reserved, input_tokens, output_tokens):
        return 0.0


class CliJudge:
    """``generate(system, prompt)`` over one sandboxed Still judgment engine, one call at a time."""

    def __init__(self, judge, seed, *, max_calls=400, src=SRC, service_factory=None):
        self.engine_id, self.model, self.effort = parse_spec(judge)
        self.seed, self.src, self.factory = seed, src, service_factory
        self.cap = j.CallCap(max_calls)
        self._lock, self._service, self._sandbox = threading.Lock(), None, None

    def _start(self):
        from .cli import resolve_seed
        from .sandbox import Sandbox
        j.load_still(self.src)
        self._sandbox = Sandbox(eval_home() / 'sandboxes', seed=resolve_seed(self.seed), src=self.src)
        service = (self.factory or j.service_factory)(self._sandbox.prepare())
        # The copied seed's CLI capability record can predate the installed CLI (its model and effort
        # listing); the shipped local, model-free check refreshes it (no model request, no calls).
        service.decision_routes.check_cli_capabilities(self.engine_id)
        route = service.decision_routes.active() or {}
        if route.get('transport') != j.still().decision.ROUTE_SUBSCRIPTION_CLI or route.get('engine') != self.engine_id:
            # pin_model changes the model only: a seed on another engine or a direct API would take the calls.
            raise RuntimeError('the seed Judgment AI route is not the subscription CLI ' + self.engine_id)
        if self.model:
            j.pin_model(service, self.model, self.effort)
        # Qualify and wait: the shipped update job runs to its end here (#855), so no judged case
        # starts against a route still qualifying (that returned provider_unavailable before).
        ready = j.effective_runtime(service)['active']
        report = j.requalify_route(service, self.cap, force=bool(service.decision_routes.qualifying())
                                   or not ready.get('available'))
        after = j.effective_runtime(service)['active']
        self.qualification = dict(report, available=after.get('available'),
                                  needed=after.get('requalification_needed'))
        if not after.get('available') or service.decision_routes.qualifying():
            raise RuntimeError('subscription-CLI judge route is not qualified: ' + repr(self.qualification))
        return service

    def _ask(self, system, prompt):
        with self._lock:
            if self._service is None:
                self._service = self._start()
            service = self._service
            context = service.decision_judge._context('eval-judge', {'transcript': prompt},
                                                      uncut='transcript')
            with service.work_decision_scope(None, self.cap):
                decision = service.decision_engine.structured(context, system, SCHEMA)
        data = getattr(decision, 'data', None)
        if not data or not isinstance(data.get('reply'), str):
            raise RuntimeError('subscription-CLI judge gave no reply: ' + str(getattr(decision, 'outcome', '?')))
        return data['reply'], decision

    async def generate(self, system, prompt):
        reply, decision = await asyncio.to_thread(self._ask, system, prompt)
        confidence = getattr(decision, 'confidence', None)
        return SimpleNamespace(completion=reply, usage=None, model=getattr(confidence, 'observed_model', None) or
                               self.model or self.engine_id)

    def close(self):
        if self._sandbox is not None:
            self._sandbox.stop()
