"""Eval-only: hold a sandbox's Main AI worker to the model the sweep pinned (#1296).

The sweep writes the pin as Still's own Main AI route setting
(``sandbox.pin_main_ai``).  That setting is what a Work runs when the plan
names no model, but the orchestrator's plan may still name another model of the
same CLI, so recorded arms could mix models.  For a pinned sandbox this wraps
``orchestrator.worker_catalogue`` so the plan is offered the pinned worker with
that one model, and that one effort (or no effort: the CLI's own default).

A model the CLI refused stays the only one offered: the run fails with the
CLI's reason instead of moving to another model or another vendor.

Loaded only by the sandbox import guard (``sitecustomize.py``), which the
owner's live server never loads.  Importing this module changes nothing;
``apply`` does.
"""
import json

ENV = 'AGENTOS_EVAL_WORKER_PIN'
MODULE = 'personal_agent.orchestrator'
FIELDS = ('worker', 'model', 'effort')


def parse(value):
    """``{'worker', 'model', 'effort'}`` from the environment value; ``ValueError`` for anything else."""
    pin = json.loads(value)
    if (not isinstance(pin, dict) or set(pin) != set(FIELDS) or
            not all(isinstance(pin[key], str) for key in FIELDS) or not pin['worker'] or not pin['model']):
        raise ValueError(f'{ENV} must be a JSON object of {", ".join(FIELDS)}')
    return pin


def hold(catalogue, pin):
    """Narrow a ``Catalogue`` in place: only the pinned worker, only the pinned model and effort."""
    model, effort = pin['model'], pin['effort']
    for row in catalogue.workers:
        if row['id'] != pin['worker']:
            row.update(available=False, reason='eval_worker_pin')
            continue
        tiers = row.get('model_tiers') or {}
        row.update(models=[model], default_model=model, default_refused='',
                   model_tiers={model: tiers[model]} if model in tiers else {},
                   model_efforts={model: [effort]} if effort else {})
        # A remembered refusal of this model must not move the Work elsewhere.
        if row.get('reason') == 'default_model_refused':
            row.update(available=True, reason='')
    return catalogue


def apply(module, pin, setattr=setattr):
    """Wrap ``module.worker_catalogue``; ``setattr`` lets a test undo it."""
    original = module.worker_catalogue

    def pinned_worker_catalogue(service):
        return hold(original(service), pin)

    setattr(module, 'worker_catalogue', pinned_worker_catalogue)
