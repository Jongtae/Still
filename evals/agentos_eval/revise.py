"""Propose and record versioned-text revision candidates outside the repository.

Adapt: Harness² (arXiv 2610.14224) Propose/Compose and experience library onto `compare_versioned_text` (#1278) and the eval's judge transport (Inspect model or `cli_judge.CliJudge`); no new dependency.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from .arm_compare import _run_scores, partition_ids, record_refused, text_digest
from .paths import REPO, eval_home

PROPOSER_KEYS = ('target_path', 'lines', 'base_digest', 'text', 'cause', 'prior')
PROPOSER_SYSTEM = (
    'Revise the supplied versioned text to address the cause. Return only a JSON object with '
    'a candidates list. Each candidate has exactly scope, path, text, and summary. Use target_path '
    'as path and provide the full replacement for the supplied text. Without input candidates, '
    'offer at most one general revision and one scoped revision. With input candidates, '
    'combine their useful changes into at most one revision with scope composed.'
)
_PROPOSE_SCOPES = ('general', 'scoped')
_COMPOSE_SCOPES = ('composed',)
_CANDIDATE_KEYS = {'scope', 'path', 'text', 'summary'}
_BUNDLE_FIELDS = ('target_path', 'lines', 'base_digest', 'cause', 'proposer')


def forbidden_ids(dirs=None, include_local=True, environ=None):
    """Every scenario id the proposer must not name: rubric and every world's scenarios."""
    from . import scenarios
    rubric = scenarios.load(dirs, include_local=include_local, environ=environ)
    folders = [scenarios.BUNDLED_SCENARIOS] if dirs is None else [Path(item) for item in dirs]
    if include_local:
        folders.extend(scenarios.local_scenario_dirs(environ))
    worlds = {(item.get('world') or {}).get('name') for folder in folders if folder.is_dir()
              for path in sorted(folder.glob('*.json')) for item in scenarios.load_file(path)} - {None}
    ids = {item['id'] for item in rubric}
    for world in sorted(worlds):
        ids.update(item['id'] for item in scenarios.load(dirs, include_local=include_local, environ=environ,
                                                          world=world))
    return ids


def load_target(path, lines=None, *, repo=REPO):
    """Read an exact UTF-8 text or inclusive line range under src/."""
    root = Path(repo).resolve()
    path = (root / Path(path)).resolve()
    if not path.is_relative_to(root / 'src') or not path.is_file():
        raise ValueError('Target must be a regular file under src/ in the repository')
    with path.open(encoding='utf-8', newline='') as source:
        text = source.read()
    if lines is not None:
        first, last = lines
        rows = text.splitlines(keepends=True)
        if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(rows):
            raise ValueError('Target line range must be within the file (1-based, inclusive)')
        text = ''.join(rows[first - 1:last])
        lines = [first, last]
    return {'path': path.relative_to(root).as_posix(), 'lines': lines, 'text': text, 'digest': text_digest(text)}


def check_room(arm, target, *, meta, measure):
    """Require complete dev evidence with room to improve."""
    if (not isinstance(arm, dict) or arm.get('schema') != 'revision-arm/1' or arm.get('role') != 'current'
            or not isinstance(arm.get('records'), list)):
        raise ValueError('Expected a current revision-arm/1 baseline with records')
    if (arm.get('text_digest'), arm.get('target_path'), arm.get('lines')) != (
            target['digest'], target['path'], target['lines']):
        raise ValueError('The baseline does not match the current text')
    records = arm['records']
    ids = set()
    for row in records:
        ident = row.get('scenario') if isinstance(row, dict) else None
        if not isinstance(ident, str) or ident not in meta:
            raise ValueError('The baseline contains an unknown scenario')
        ids.add(ident)
    dev = set(partition_ids(ids, meta)['dev'])
    if not dev:
        raise ValueError('The baseline contains no dev records')
    scores = [_run_scores(row, (measure,), measure, record_refused(row))[measure]
              for row in records if row['scenario'] in dev]
    if None in scores:
        raise ValueError(f'The baseline has incomplete dev scores for {measure}')
    if all(score >= 1.0 for score in scores):
        raise ValueError(f'no_room_on_dev: {measure} is at its maximum on dev and no revision can be preferred')


def _recent(entries, target_path, limit):
    matching = (entry for entry in entries if entry['target_path'] == target_path)
    return sorted(matching, key=lambda entry: entry['recorded_at'], reverse=True)[:max(0, limit)]


def prior_for_proposer(entries, target, limit=8):
    """Project bounded same-target experience to the fields the proposer needs."""
    return [
        {'candidate_digest': candidate['candidate_digest'], 'scope': candidate['scope'],
         'summary': candidate['summary'], 'cause': entry['cause']['text'], 'verdict': candidate['verdict'],
         'adopted': candidate['candidate_digest'] == target['digest']}
        for entry in _recent(entries, target['path'], limit)
        for candidate in entry['candidates']
    ]


def _nonempty(text):
    return isinstance(text, str) and bool(text.strip())


def proposer_input(target, cause, experience, *, forbidden=(), limit=8):
    """Build the proposer input, omitting the cause's source id."""
    if cause.get('kind') not in ('blocker', 'regression') or not _nonempty(cause.get('text')):
        raise ValueError('Cause needs a blocker or regression kind and non-empty text')
    if any(term in cause['text'] for term in forbidden):
        raise ValueError('Cause text contains a scenario id')
    return {'target_path': target['path'], 'lines': target['lines'], 'base_digest': target['digest'],
            'text': target['text'], 'cause': cause['text'], 'prior': prior_for_proposer(experience, target, limit)}


def _candidate_refusal(candidate, target, forbidden, available):
    if not isinstance(candidate, dict) or candidate.keys() != _CANDIDATE_KEYS:
        return 'Candidate keys must be scope, path, text, and summary'
    if candidate['path'] != target['path']:
        return 'Candidate path must match the target path'
    if candidate['scope'] not in available:
        return 'Candidate scope is invalid or repeated'
    if not _nonempty(candidate['text']) or candidate['text'] == target['text']:
        return 'Candidate text must be non-empty and different from the target'
    if any(term in candidate['text'] for term in forbidden):
        return 'Candidate text contains a scenario id'
    if not _nonempty(candidate['summary']):
        return 'Candidate summary must be non-empty'
    if any(term in candidate['summary'] for term in forbidden):
        return 'Candidate summary contains a scenario id'
    return None


async def _generate_candidates(generate, prompt, target, forbidden, scopes, result, *, parents=()):
    response = await generate(PROPOSER_SYSTEM, json.dumps(prompt))
    usage = response.usage
    result['calls'].append({'model': response.model,
                            'input_tokens': usage.input_tokens if usage is not None else None,
                            'output_tokens': usage.output_tokens if usage is not None else None})
    try:
        reply = json.loads(response.completion)
    except (ValueError, RecursionError):
        result['refused'].append({'reason': 'Reply was not valid JSON'})
        return
    if not isinstance(reply, dict) or reply.keys() != {'candidates'} or not isinstance(reply['candidates'], list):
        result['refused'].append({'reason': 'Reply must have only a candidates list'})
        return

    available = list(scopes)
    for candidate in reply['candidates']:
        reason = _candidate_refusal(candidate, target, forbidden, available)
        if reason is None:
            try:
                digest = text_digest(candidate['text'])
            except UnicodeEncodeError:
                reason = 'Candidate text must be valid UTF-8'
        if reason is not None:
            refusal = {'reason': reason}
            if isinstance(candidate, dict):
                refusal.update({key: candidate[key] for key in ('scope', 'path')
                                if isinstance(candidate.get(key), str)})
            result['refused'].append(refusal)
            continue
        available.remove(candidate['scope'])
        accepted = dict(candidate, label=candidate['scope'], base_digest=target['digest'], candidate_digest=digest)
        if parents:
            accepted['parents'] = parents
        result['candidates'].append(accepted)


async def propose(target, cause, generate, *, experience=(), forbidden=(), compose=False, limit=8):
    """Generate candidates and optionally compose the two accepted proposals."""
    forbidden = tuple(forbidden)
    prompt = proposer_input(target, cause, experience, forbidden=forbidden, limit=limit)
    result = {'candidates': [], 'refused': [], 'calls': []}
    await _generate_candidates(generate, prompt, target, forbidden, _PROPOSE_SCOPES, result)
    if compose and len(result['candidates']) == 2:
        by_scope = {candidate['scope']: candidate for candidate in result['candidates']}
        ordered = [by_scope[scope] for scope in _PROPOSE_SCOPES]
        prompt['candidates'] = [{key: candidate[key] for key in ('scope', 'summary', 'text')} for candidate in ordered]
        parents = [candidate['candidate_digest'] for candidate in ordered]
        await _generate_candidates(generate, prompt, target, forbidden, _COMPOSE_SCOPES, result, parents=parents)
    return result


def _outside_repo(path):
    folder = Path(path).expanduser().resolve()
    if folder.is_relative_to(REPO.resolve()):
        raise ValueError('Revision files must be written outside the repository')
    return folder


def _slug(path):
    return re.sub(r'[^a-z0-9]+', '-', path.lower()).strip('-')


def _time_digest(moment, digest):
    suffix = digest.removeprefix('sha256:')[:8]
    if not re.fullmatch(r'[0-9a-f]{8}', suffix):
        raise ValueError('Invalid base digest')
    return f'{moment:%Y%m%dT%H%M%SZ}-{suffix}'


def candidate_folder(target, out=None):
    """Resolve an explicit output folder or derive its timestamped default."""
    if out is None:
        name = _time_digest(datetime.now(timezone.utc), target['digest'])
        out = eval_home() / 'revisions' / _slug(target['path']) / name
    return _outside_repo(out)


def _json(value):
    return json.dumps(value, indent=2) + '\n'


def _read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_candidates(result, target, cause, out_dir, *, settings):
    """Write candidate text, metadata and a manifest outside the repository."""
    folder = _outside_repo(out_dir)
    shared = {'target_path': target['path'], 'lines': target['lines'], 'base_digest': target['digest'],
              'cause': cause, 'proposer': settings}
    labels = []
    files = {}
    for candidate in result['candidates']:
        label = candidate['label']
        if label not in _PROPOSE_SCOPES + _COMPOSE_SCOPES or label in labels:
            raise ValueError('Invalid or repeated candidate label')
        labels.append(label)
        files[f'{label}.txt'] = candidate['text']
        meta = {key: candidate[key] for key in ('label', 'scope', 'candidate_digest', 'summary')}
        files[f'{label}.json'] = _json(dict(meta, **shared, schema='revision-candidate/1',
                                           parents=candidate.get('parents', [])))
    files['refused.json'] = _json(result['refused'])
    files['manifest.json'] = _json(dict(shared, schema='revision-candidates/1', candidates=labels,
                                      refused=result['refused'], calls=result['calls']))

    folder.mkdir(parents=True, exist_ok=True)
    # Stage first, then replace directory entries without following existing file links.
    with TemporaryDirectory(dir=folder) as staging:
        staging = Path(staging)
        for name, contents in files.items():
            (staging / name).write_text(contents, encoding='utf-8', newline='')
        for name in files:
            (staging / name).replace(folder / name)
    return folder


def experience_entry(candidates_dir, comparison, *, recorded_at):
    """Join the bundle to a comparison of the same baseline and candidate text."""
    folder = Path(candidates_dir).expanduser()
    manifest = _read_json(folder / 'manifest.json')
    if not isinstance(manifest, dict) or manifest.get('schema') != 'revision-candidates/1':
        raise ValueError('Expected a revision-candidates/1 manifest')
    if not isinstance(comparison, dict) or comparison.get('schema') != 'arm-compare/1':
        raise ValueError('Expected an arm-compare/1 comparison')
    baseline = comparison.get('baseline')
    if not isinstance(baseline, dict) or baseline.get('text_digest') != manifest['base_digest']:
        raise ValueError('Comparison baseline digest does not match the candidate bundle')
    metas = {}
    for label in manifest['candidates']:
        if label not in _PROPOSE_SCOPES + _COMPOSE_SCOPES:
            raise ValueError('Invalid candidate label in manifest')
        try:
            metas[label] = _read_json(folder / f'{label}.json')
        except FileNotFoundError as exc:
            raise ValueError(f'Candidate metadata is missing: {label}') from exc
    compared = comparison.get('candidates')
    if not isinstance(compared, dict):
        raise ValueError('Comparison needs candidate arms')
    for label, arm in compared.items():
        if label not in metas or not isinstance(arm, dict) or arm.get('text_digest') != metas[label]['candidate_digest']:
            raise ValueError(f'Comparison candidate digest or label does not match the bundle: {label}')

    candidates = []
    for label, meta in metas.items():
        row = {key: meta[key] for key in ('label', 'scope', 'candidate_digest', 'summary', 'parents')}
        arm = compared.get(label)
        if arm is None:
            row.update(verdict='not_compared', reasons=[])
        else:
            row.update(verdict=arm['verdict'], reasons=arm['reasons'])
        candidates.append(row)
    retries = comparison.get('equal_retries')
    cost = {'proposer': manifest['calls'], 'baseline': baseline['cost'],
            'equal_retries': retries['cost'] if retries is not None else None,
            **{label: arm['cost'] for label, arm in compared.items()}}
    return {**{key: manifest[key] for key in _BUNDLE_FIELDS},
            'schema': 'revision-experience/1', 'recorded_at': recorded_at, 'settings': comparison['settings'],
            'scenario_ids': comparison['scenario_ids'], 'measure': comparison['target'],
            'tolerances': comparison['tolerances'], 'candidates': candidates, 'cost': cost}


def record_experience(entry, folder):
    """Store a separate JSON file for each comparison."""
    root = _outside_repo(folder)
    folder = _outside_repo(root / _slug(entry['target_path']))
    moment = datetime.fromisoformat(entry['recorded_at']).astimezone(timezone.utc)
    prefix = _time_digest(moment, entry['base_digest']) + '-'
    contents = _json(entry)
    folder.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, prefix=prefix, suffix='.json',
                            delete=False) as record:
        record.write(contents)
    return Path(record.name)


def read_experience(folder, target_path, limit=8):
    """Read the newest usable experience records for this target."""
    entries = []
    for path in Path(folder).expanduser().rglob('*.json'):
        try:
            entry = _read_json(path)
            if entry['schema'] != 'revision-experience/1' or entry['target_path'] != target_path:
                continue
            datetime.fromisoformat(entry['recorded_at'])
            if not isinstance(entry['cause']['text'], str) or not isinstance(entry['candidates'], list):
                continue
            if not all(isinstance(candidate[key], str) for candidate in entry['candidates']
                       for key in ('candidate_digest', 'scope', 'summary', 'verdict')):
                continue
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            continue
        entries.append(entry)
    return _recent(entries, target_path, limit)


def make_generate(spec, *, seed=None, max_tokens=8000):
    """Wrap the selected proposer transport with its settings and cleanup."""
    if not _nonempty(spec) or spec.strip() == 'none':
        raise ValueError('A proposer model is required')
    from .cli_judge import CliJudge, is_cli_judge

    settings = {'proposer': spec, 'max_tokens': max_tokens}
    if is_cli_judge(spec):
        _outside_repo(eval_home() / 'sandboxes')
        # propose() makes at most two calls; this cap also covers the route's own requalification suite.
        judge = CliJudge(spec, seed)
        return judge.generate, settings, judge.close

    from inspect_ai.model import ChatMessageSystem, ChatMessageUser, GenerateConfig, get_model

    model = get_model(spec)

    async def generate(system, prompt):
        return await model.generate([ChatMessageSystem(content=system), ChatMessageUser(content=prompt)],
                                    config=GenerateConfig(max_tokens=max_tokens))

    return generate, settings, lambda: None
