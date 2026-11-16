"""R1 v3 dev-only selection and explicitly frozen locked measurement.

Reuse: Adopt stdlib argparse/git subprocess/JSON and the existing pinned ORT
encoder, numpy cosine and RRF. Adapt r1v2.make_retriever, instrument,
recall_at_k, dev_report and the socket guard; Build only the experiment's
selection, committed-freeze validation and three-baseline gate glue. No new
dependency, network authority, index store or runtime path. Offline review
reuses R1.md/R1v2.md provenance/licences; release activity is not refreshed.
"""
import argparse
from itertools import product
import json
from pathlib import Path
import subprocess
import tempfile

from agentos_eval import recall_pairs as pairs, recall_report as rr
from . import r1v2 as arms, r1v2_measure as prior
from .r1_arm import _CachedEmbedder, _offline_guard

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'evals/recall/recall-pairs-v3.json'
BASELINES = ('lexical', 'lexical-context', 'runtime')
NAMES = ('vector', 'hybrid')
GRID = {'top_k': [1, 2, 3], 'min_sim': [round(n / 100, 2) for n in range(70, 91, 2)]}
SELECTION = ('Reject dev traps; maximize dev exact passes, then fewer noise rows, '
             'then higher min_sim; remaining ties: smaller top_k, arm name. '
             'Request only, content passages, RRF k=60, empty-only semantic admission.')


def select_data(data, split):
    if split not in ('dev', 'all'):
        raise ValueError('split must be dev or all')
    return dict(data, cases=[c for c in data['cases'] if split == 'all' or c['split'] == 'dev'])


def parameters(name, top_k, floor):
    p = dict(top_k=top_k, min_sim=floor, use_context=False, passage_form='content')
    return p if name == 'vector' else dict(p, k=60, vector_only_when_empty=True)


def objective(row):
    return (-row['passes'], row['score']['noise_count'], -row['parameters']['min_sim'],
            row['parameters']['top_k'], row['arm'])


def choose(records):
    selected = {}
    for name in NAMES:
        clean = [r for r in records if r['arm'] == name and not r['score']['cases_with_trap']]
        if not clean:
            raise ValueError('No trap-free dev candidate: ' + name)
        selected[name] = min(clean, key=objective)
    return selected, min(selected.values(), key=objective)['arm']


def sweep(data, *, embedder=None):
    """All rows indexed, but only dev requests can reach retrieval/inference."""
    from personal_agent.quickstart_store import QuickStore
    full_digest = rr._fixture_digest(data)
    dev = select_data(data, 'dev')
    if not dev['cases']:
        raise ValueError('No dev cases')
    records = []
    with _offline_guard() as attempts, tempfile.TemporaryDirectory(prefix='r1v3-dev-') as tmp:
        encoder = _CachedEmbedder(arms.get_embedder()) if embedder is None else embedder
        store = QuickStore(Path(tmp) / 'state')
        refs = pairs.seed(store, dev)
        before = refs.digest()
        for name, top_k, floor in product(NAMES, GRID['top_k'], GRID['min_sim']):
            p = parameters(name, top_k, floor)
            retrieve = arms.make_retriever(store, dev, name, p, embedder=encoder)
            if refs.digest() != before:
                raise AssertionError('Factory changed Memory')
            envelope = pairs.run(retrieve, dev['cases'], refs)
            records.append(dict(arm=name, parameters=p, passes=sum(rr._passes(r) for r in envelope['rows']),
                                score=pairs.score(envelope)['dev']['total']))
        if refs.digest() != before or attempts:
            raise AssertionError('Memory changed or network attempted')
    selected, primary = choose(records)
    return dict(version='r1v3-frozen/1', fixture_digest=full_digest, split='dev',
                dev_cases=[c['id'] for c in dev['cases']], selection=SELECTION, grid=GRID,
                selected=selected, primary=primary, model=prior.provenance(),
                records=records, network_attempts=attempts)


def load_frozen(path, data, *, root=ROOT, runner=subprocess.run):
    """Refuse before model loading or any held-out runner construction."""
    path, root = Path(path).resolve(strict=True), Path(root).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError('Frozen JSON must be committed inside this repository')
    rel = path.relative_to(root).as_posix()
    for command in (['git', 'ls-files', '--error-unmatch', '--', rel],
                    ['git', 'diff', '--exit-code', 'HEAD', '--', rel]):
        result = runner(command, cwd=root, capture_output=True, text=True)
        if result.returncode:
            raise ValueError('Frozen JSON must be committed and clean against HEAD')
    frozen = json.loads(path.read_text(encoding='utf-8'))
    if frozen.get('version') != 'r1v3-frozen/1' or frozen.get('fixture_digest') != rr._fixture_digest(data):
        raise ValueError('Frozen fixture digest/version differs')
    if frozen.get('split') != 'dev' or frozen.get('selection') != SELECTION or frozen.get('grid') != GRID:
        raise ValueError('Frozen selection protocol differs')
    if frozen.get('dev_cases') != [c['id'] for c in data['cases'] if c['split'] == 'dev']:
        raise ValueError('Frozen dev cases differ')
    if frozen.get('model') != prior.provenance():
        raise ValueError('Frozen model provenance differs')
    identities = [(r['arm'], r['parameters']['top_k'], r['parameters']['min_sim'])
                  for r in frozen['records']]
    expected = set(product(NAMES, GRID['top_k'], GRID['min_sim']))
    if len(identities) != len(expected) or set(identities) != expected:
        raise ValueError('Frozen grid records are incomplete or duplicated')
    for row in frozen['records']:
        p = row['parameters']
        if p != parameters(row['arm'], p['top_k'], p['min_sim']):
            raise ValueError('Frozen parameters outside the declared grid')
    selected, primary = choose(frozen['records'])
    if selected != frozen.get('selected') or primary != frozen.get('primary'):
        raise ValueError('Frozen selection does not follow the declared ordering')
    for name in NAMES:
        p = selected[name]['parameters']
        if p.get('top_k') not in GRID['top_k'] or p.get('min_sim') not in GRID['min_sim'] or p != parameters(name, p['top_k'], p['min_sim']):
            raise ValueError('Frozen parameters outside the declared grid')
    return frozen


def combined_gate(reports, name):
    """Adapt the existing gate to three references; keep the default v2 gate."""
    return prior.combined_gate(reports, name, baselines=BASELINES)


def measure(data, frozen, output, split='dev', *, embedder=None):
    selected = select_data(data, split)
    results = {}
    with _offline_guard() as attempts:
        for name in BASELINES:
            arm = rr.resolve_arm(name)
            results[name] = rr.run_arm(selected, rr.Arm(prior.instrument(arm.factory), arm.evidence))
        for name in NAMES:
            p = frozen['selected'][name]['parameters']
            factory = lambda store, data, name=name, p=p: arms.make_retriever(store, data, name, p, embedder=embedder)
            results[name] = rr.run_arm(selected, rr.Arm(prior.instrument(factory), arms.EVIDENCE))
        if attempts:
            raise AssertionError('Network attempted')
    reports = {baseline: rr.report(selected, results, baseline) for baseline in BASELINES}
    if split == 'dev':
        reports = {b: prior.dev_report(r) for b, r in reports.items()}
    summary = dict(split=split, fixture_digest=rr._fixture_digest(data),
                   gate={name: combined_gate(reports, name) for name in NAMES} if split == 'all' else None,
                   gate_status='evaluated' if split == 'all' else 'deferred; dev only',
                   recall_at_k={name: prior.recall_at_k(selected, result) for name, result in results.items()},
                   network_attempts=attempts, primary=frozen['primary'])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    prior.write(output / 'r1v3-observations.json', results)
    for baseline, report in reports.items():
        prior.write(output / f'r1v3-vs-{baseline}.json', report)
        (output / f'r1v3-vs-{baseline}.md').write_text(rr.report_markdown(report), encoding='utf-8')
    prior.write(output / 'r1v3-measurement.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, default=FIXTURE)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--split', choices=('dev', 'all'), default='dev')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dev-sweep', action='store_true')
    mode.add_argument('--frozen', type=Path)
    args = parser.parse_args()
    data = pairs.load(args.fixture)
    if args.dev_sweep:
        if args.split != 'dev':
            parser.error('Sweeps are dev only')
        frozen = sweep(data)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        prior.write(args.out_dir / 'r1v3-frozen.json', frozen)
    else:
        frozen = load_frozen(args.frozen, data)
        measure(data, frozen, args.out_dir, args.split)


if __name__ == '__main__':
    main()
