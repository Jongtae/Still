"""Fresh, owner-data-free judgment seeds through shipped activation (#855).

Adapt: stdlib pathlib/shutil/JSON and the existing eval service factory and
runtime projection. No copied profile, route-row writer or qualification fork.
"""
import argparse
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

from . import cli, judgments
from .paths import LIVE_DATA, eval_home


def _system_temp_roots():
    return {Path(p).resolve() for p in ('/tmp', '/private/tmp', tempfile.gettempdir())}


def validate_location(path):
    """Temporary stores cannot satisfy the shipped protected-directory probe."""
    path = Path(path).expanduser().resolve()
    if any(path.is_relative_to(root) for root in _system_temp_roots()):
        raise ValueError('Synthetic seeds cannot live under the system temp directory: '
                         'the shipped protected-directory-denied isolation probe cannot protect /tmp. '
                         'Use --out NAME under eval_home()/seeds (outside system temp).')
    return path


def _output(out):
    path = Path(out).expanduser()
    if not path.is_absolute() and len(path.parts) == 1:
        path = eval_home() / 'seeds' / path
    path = path.resolve()
    if path == LIVE_DATA.resolve():
        raise ValueError('The live data folder is never used directly.')
    validate_location(path)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f'Synthetic seed output must be empty: {path}')
    return path


def _activation_body(engine, model, effort):
    judgments.still()  # Select the production checkout before further imports.
    routes = importlib.import_module('personal_agent.decision_routes')
    body = dict(transport=judgments.still().decision.ROUTE_SUBSCRIPTION_CLI,
                engine=engine,
                model_policy=routes.POLICY_EXPLICIT if model is not None else routes.POLICY_FASTEST_QUALIFIED)
    if model is not None:
        body['model'] = model
    if effort is not None:
        body['effort'] = effort
    return body


def build(out, *, engine='codex', model=None, effort=None, factory=None, activate=None) -> dict:
    """Build an empty seed, qualifying via the host CLI login (real calls).

    Test seams: ``factory(path)`` returns a service; ``activate(service, body)``
    replaces only the explicit activation. Shipped activation raises on failure.
    Any construction/activation/manifest failure removes the output directory.
    """
    path = _output(out)
    body = _activation_body(engine, model, effort)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        service = (factory or judgments.service_factory)(path)
        if activate is None:
            service.decision_routes.activate(body)
        else:
            activate(service, body)
        summary = dict(created_at=datetime.now(timezone.utc).isoformat(),
                       git_head=cli.git_head(), engine=engine,
                       requested_model=model, requested_effort=effort,
                       runtime=judgments.effective_runtime(service), owner_data='none')
        manifest = path / 'synthetic-seed.json'
        manifest.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        manifest.chmod(0o600)
        return summary
    except BaseException:
        shutil.rmtree(path)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, help='Bare NAME resolves to eval_home()/seeds/NAME; system temp is refused')
    parser.add_argument('--engine', default='codex')
    parser.add_argument('--model')
    parser.add_argument('--effort')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.dry_run:
            _output(args.out)
            body = _activation_body(args.engine, args.model, args.effort)
            suite = judgments.still().decision_qualification
            routes = importlib.import_module('personal_agent.decision_routes')
            explicit = args.model is not None
            trials = 1 if explicit else routes.FASTEST_TRIALS
            candidates = 1 if explicit else len(routes.RANKED_MODELS.get(args.engine, ()))
            summary = dict(dry_run=True, activate=body, suite_version=suite.SUITE_VERSION,
                           suite_calls=len(suite.CASES), max_trials_per_case=trials,
                           max_calls_per_candidate=len(suite.CASES) * trials,
                           max_candidates=candidates,
                           max_qualification_calls=len(suite.CASES) * trials * candidates,
                           note='Per candidate; failures may stop early. Ranked models may be filtered by the CLI. '
                                'Local isolation probes use a scripted model; CLI-internal calls are not counted.')
        else:
            summary = build(args.out, engine=args.engine, model=args.model, effort=args.effort)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
