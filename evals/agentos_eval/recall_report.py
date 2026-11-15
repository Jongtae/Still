"""Run R1 paired recall arms and report split-local gains and uncertainty.

Existing Solutions Review: Adopt stdlib tempfile/importlib/subprocess/statistics,
the existing recall_pairs scorer and judgments.compare output conventions;
Build only report glue. No dependency is added: stdlib APIs fit the offline
Python/AGPL path without new network authority or supply-chain exposure.
External release activity was not checked (offline review).
"""
import hashlib
import importlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from . import recall_pairs as pairs


DEFAULT_FIXTURE = Path(__file__).resolve().parents[1] / 'recall/recall-pairs-v1.json'
PHRASING_PROMPT = (
    "You write search words for a literal keyword search over a person's saved notes "
    "(it matches word prefixes). Given the person's request and the turn before it, "
    "output only space-separated search words likely to appear in the saved note: "
    "the key nouns, their synonyms, the same words in Korean and in English, and base "
    "forms without particles or endings. No sentences, no explanations, at most 20 words."
    "\n\nTurn before: {context}\nRequest: {query}\n"
)
CHANGES = ('fixed', 'regressed', 'unchanged-pass', 'unchanged-fail', 'uncertain')


@dataclass(frozen=True)
class Arm:
    """A factory and its evidence declaration; phraser is used only by phrasing."""

    factory: object
    evidence: str = 'fixture (model-free)'
    deterministic: bool = True
    phraser: object = None
    diagnostic: bool = False


def resolve_arm(spec, *, phraser=None):
    """Resolve a built-in or module:factory, without importing other arms eagerly."""
    if spec == 'phrasing':
        if phraser is None:
            raise ValueError('phrasing requires --phraser-cmd or --phrasings')
        return Arm(lambda store, data: phrasing(store, data, phraser),
                   getattr(phraser, 'evidence', 'local model observation'), False, phraser)
    if ':' not in spec:
        try:
            from .recall_arms import ARMS, DETERMINISTIC, DIAGNOSTIC, EVIDENCE
        except ImportError as exc:
            raise ValueError(f'Unknown recall arm {spec!r} (built-in arms unavailable)') from exc
        if spec not in ARMS:
            raise ValueError(f'Unknown recall arm {spec!r}; use a known name or module:callable')
        return Arm(ARMS[spec], EVIDENCE[spec], spec in DETERMINISTIC, diagnostic=spec in DIAGNOSTIC)
    module_name, attribute = spec.split(':', 1)
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f'Cannot load recall arm {spec!r}: {exc}') from exc
    if not callable(factory):
        raise ValueError(f'Recall arm {spec!r} must name a callable factory')
    evidence = getattr(module, 'EVIDENCE', 'fixture (model-free)')
    if isinstance(evidence, dict):
        evidence = evidence.get(attribute, evidence.get(spec, 'fixture (model-free)'))
    deterministic = getattr(module, 'DETERMINISTIC', True)
    if not isinstance(deterministic, bool):
        deterministic = attribute in deterministic or spec in deterministic
    diagnostics = getattr(module, 'DIAGNOSTIC', frozenset())
    return Arm(factory, getattr(factory, 'evidence', evidence),
               getattr(factory, 'deterministic', deterministic),
               diagnostic=getattr(factory, 'diagnostic', attribute in diagnostics or spec in diagnostics))


def command_phraser(argv, timeout=120):
    """Create an explicitly requested stdin/stdout worker, with no shell."""
    if not isinstance(argv, list) or not argv or any(not isinstance(word, str) for word in argv):
        raise ValueError('A phraser command must be a nonempty argv list')

    def phrase(query, context):
        try:
            done = subprocess.run(argv, input=PHRASING_PROMPT.format(query=query, context=context),
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            tail = exc.stderr or ''
            if isinstance(tail, bytes):
                tail = tail.decode('utf-8', errors='replace')
            raise RuntimeError(f'Phraser timed out after {timeout}s: {tail[-2000:]}') from exc
        if done.returncode:
            raise RuntimeError(f'Phraser exited {done.returncode}: {done.stderr[-2000:]}')
        return done.stdout.strip()

    phrase.evidence = 'local model observation'
    return phrase


def _fixture_digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _phrasing_cases(data):
    return {case['id']: dict(query=case['query'], context=case.get('context', '')) for case in data['cases']}


def _validate_phrasing_fixture(phraser, data):
    digest = getattr(phraser, 'phrasing_fixture', None)
    if digest is not None and digest != _fixture_digest(data):
        raise ValueError('Recorded phrasing fixture digest differs from the loaded fixture')
    cases = getattr(phraser, 'phrasing_cases', None)
    if cases is not None and cases != _phrasing_cases(data):
        raise ValueError('Recorded phrasing query/context differs from the loaded fixture')


def replay_phraser(recorded, *, data=None):
    """Replay a legacy run -> case -> words mapping or a report with provenance."""
    provenance = recorded['arms']['phrasing'] if 'arms' in recorded else {}
    if provenance:
        recorded = provenance['phrasings']
    recorded = {str(run): dict(words) for run, words in recorded.items()}

    def for_run(run, cases):
        identify = _case_lookup(cases)

        def phrase(query, context):
            ident = identify(query, context)
            try:
                words = recorded[str(run)][ident]
            except KeyError as exc:
                raise ValueError(f'Missing recorded phrasing for run {run}, case {ident}') from exc
            if not isinstance(words, str):
                raise ValueError('Recorded phrasing must be text')
            return words

        return phrase

    # Binding case ids to the actual ordered fixture avoids depending on JSON key order.
    def phrase(query, context):
        raise ValueError('Bind replay with phrasing(store, data, phraser) or run_arm')

    phrase.for_run = for_run
    phrase.evidence = 'fixture replay of a local model observation'
    phrase.phrasing_fixture = provenance.get('phrasing_fixture')
    phrase.phrasing_cases = provenance.get('phrasing_cases')
    if data is not None:
        _validate_phrasing_fixture(phrase, data)
    return phrase


def _case_lookup(cases):
    """Identify observed queries, preserving distinct ids for identical case inputs."""
    matches, offsets = {}, {}
    for case in cases:
        matches.setdefault((case['query'], case.get('context', '')), []).append(case['id'])

    def identify(query, context):
        key = query, context
        if key not in matches:
            raise ValueError('Phrasing query is absent from the fixture')
        index = offsets.get(key, 0)
        offsets[key] = index + 1
        return matches[key][index % len(matches[key])]

    return identify


def phrasing(store, data, phraser, *, run=1):
    """Search literal Memory with the request plus the worker's extra words."""
    from personal_agent.memory_service import MemoryService

    _validate_phrasing_fixture(phraser, data)
    service = MemoryService(store, private_read_sink=lambda marker: None)
    service._today = lambda: data['today']
    identify, recorded = _case_lookup(data['cases']), {}
    if hasattr(phraser, 'for_run'):
        phraser = phraser.for_run(run, data['cases'])

    def retrieve(query, context):
        ident = identify(query, context)
        words = phraser(query, context)
        if not isinstance(words, str):
            raise ValueError('Phraser must return text')
        recorded[ident] = words
        return [row['id'] for row in service.search_memories(data['owner'], f'{query} {words}')['memories']]

    retrieve.phrasings = recorded
    return retrieve


def run_arm(data, arm, runs=3):
    """Run an Arm (or an injected factory) on independently seeded temporary stores."""
    from personal_agent.quickstart_store import QuickStore

    if not isinstance(runs, int) or isinstance(runs, bool) or runs < 1:
        raise ValueError('runs must be a positive integer')
    if not isinstance(arm, Arm):
        arm = Arm(arm, getattr(arm, 'evidence', 'fixture (model-free)'),
                  getattr(arm, 'deterministic', True), diagnostic=getattr(arm, 'diagnostic', False))
    if arm.phraser is not None:
        _validate_phrasing_fixture(arm.phraser, data)
    results, phrasings = [], {}
    for index in range(1, (1 if arm.deterministic else runs) + 1):
        with tempfile.TemporaryDirectory(prefix='still-recall-') as tmp:
            store = QuickStore(Path(tmp) / 'state')
            refs = pairs.seed(store, data)
            retriever = (phrasing(store, data, arm.phraser, run=index) if arm.phraser is not None
                         else arm.factory(store, data))
            result = pairs.run(retriever, data['cases'], refs)
            if results and result['fixture'] != results[0]['fixture']:
                raise pairs.RecallPairsError('Fixture digest changed between runs')
            results.append(result)
            if hasattr(retriever, 'phrasings'):
                phrasings[str(index)] = dict(retriever.phrasings)
    out = dict(evidence=arm.evidence, deterministic=arm.deterministic, diagnostic=arm.diagnostic, runs=results)
    if phrasings:
        out.update(phrasings=phrasings, phrasing_fixture=results[0]['fixture'], phrasing_cases=_phrasing_cases(data))
    return out


def _passes(row):
    return int(row['needed_hit'] == row['needed_total'] and not row['trap_hits'] and not row['noise_hits'])


def _majority(results):
    """Select an observed representative row, rather than inventing average hits."""
    first = results[0]
    # compare validates the digest, case ids and case definitions on every run.
    for result in results[1:]:
        pairs.compare(first, result)
    indexed = [{row['id']: row for row in result['rows']} for result in results]
    rows, cases = [], []
    for first_row in first['rows']:
        observed = [run[first_row['id']] for run in indexed]
        passes = [_passes(row) for row in observed]
        majority = sum(passes) > len(passes) / 2
        representative = next(row for row, passed in zip(observed, passes) if passed == majority)
        rows.append(dict(representative))
        # Store counts, not random temporary-store memory ids: replay is comparable.
        case = {key: value for key, value in representative.items() if key != 'returned'}
        case.update(passes=sum(passes), majority=majority, spread=max(passes) - min(passes),
                    per_run=[{key: row[key] for key in ('needed_hit', 'trap_hits', 'noise_hits')}
                             for row in observed])
        cases.append(case)
    return dict(version=first['version'], fixture=first['fixture'], rows=rows), cases


def _verdict(comparison, score, scores, baseline_score, deterministic, baseline_rows, runs):
    """#827 conditions, each named independently for inspectable failures.

    For sampling arms, condition b fails when a baseline-passing lexical-control
    fails in ANY run, even when the majority passes. Case change labels stay as is.
    """
    failed = []
    if any(score[split]['total']['cases_with_trap'] for score in scores for split in pairs.SPLITS):
        failed.append('a-zero-traps')
    lexical_loss = not deterministic and any(
        row['kind'] == 'lexical-control' and _passes(baseline_rows[row['id']]) and not _passes(row)
        for run in runs for row in run['rows'])
    if lexical_loss or any(row['kind'] == 'lexical-control' and row['change'] == 'regressed'
                           for split in comparison.values() for row in split['changes']):
        failed.append('b-no-lexical-control-regressions')
    heldout = comparison['heldout']
    baseline_recall = baseline_score['heldout']['total']['needed_recall']
    recalls = [score['heldout']['total']['needed_recall'] for score in scores]
    observed = all(recall is not None for recall in recalls) and baseline_recall is not None
    gain = mean(recalls) - baseline_recall if observed else None
    recall = score['heldout']['total']['needed_recall']
    if recall is None or baseline_recall is None or recall < baseline_recall or not heldout['counts']['fixed']:
        failed.append('c-heldout-gain-and-fix')
    if heldout['counts']['regressed']:
        failed.append('d-no-heldout-regressions')
    if not deterministic and (not observed or max(recalls) - min(recalls) > gain):
        failed.append('e-heldout-spread-within-gain')
    return dict(preferred=not failed, failed=failed,
                note='R1 retrieval evidence only; dev and heldout are evaluated separately. '
                     'Any observed trap overrides majority; uncertain gains do not count as fixed.')


def report(data, results_by_arm, baseline='lexical'):
    """Build a JSON report from run_arm results; no model or retrieval is called here."""
    if baseline not in results_by_arm:
        raise ValueError(f'Missing baseline arm {baseline!r}')
    prepared = {}
    for name, result in results_by_arm.items():
        runs = result['runs']
        if not runs or (result['deterministic'] and len(runs) != 1):
            raise ValueError('An arm needs runs; deterministic arms need exactly one')
        envelope, cases = _majority(runs)
        prepared[name] = envelope, cases
    base = prepared[baseline][0]
    if base['version'] != data['version']:
        raise pairs.RecallPairsError('Report fixture version differs')
    # The report's named fixture must be the one measured, not merely the same version.
    digest = _fixture_digest(data)
    if base['fixture'] != digest:
        raise pairs.RecallPairsError('Report fixture contents differ')
    baseline_score = pairs.score(base)
    before = {row['id']: row for row in base['rows']}
    arms = {}
    for name, result in results_by_arm.items():
        envelope, cases = prepared[name]
        comparison = pairs.compare(base, envelope)
        sampling = not result['deterministic']
        diagnostic = result.get('diagnostic', False)
        scores = [pairs.score(run) for run in result['runs']]
        score = pairs.score(envelope)
        for case in cases:
            case['uncertain'] = (sampling and case['spread'] >
                                 abs(case['passes'] / len(scores) - _passes(before[case['id']])))
            split = comparison[case['split']]
            change = next(row for row in split['changes'] if row['id'] == case['id'])
            traps = [row['trap_hits'] for row in case['per_run']]
            if any(traps):
                change['change'] = 'regressed'
                # Replace the representative's trap entry with all observed authority hits.
                split['trap_regressions'] = [row for row in split['trap_regressions'] if row['id'] != case['id']]
                split['trap_regressions'].append(dict(change, trap_hits=max(traps), per_run=traps))
            elif case['uncertain'] and change['change'] == 'fixed':
                change['change'] = 'uncertain'
            if sampling:
                change.update(passes=case['passes'], runs=len(scores), spread=case['spread'],
                              uncertain=case['uncertain'])
        for split_name, split in comparison.items():
            split['counts'] = {label: sum(row['change'] == label for row in split['changes']) for label in CHANGES}
            if sampling:
                recalls = [score[split_name]['total']['needed_recall'] for score in scores]
                b = baseline_score[split_name]['total']['needed_recall']
                valid = b is not None and all(value is not None for value in recalls)
                split.update(needed_recalls=recalls, recall_min=min(recalls) if valid else None,
                             recall_max=max(recalls) if valid else None,
                             recall_spread=max(recalls) - min(recalls) if valid else None,
                             recall_difference=mean(recalls) - b if valid else None)
        arm = dict(evidence=result['evidence'], deterministic=result['deterministic'], diagnostic=diagnostic,
                   runs=len(scores),
                   score=score,
                   cases=cases if sampling else [{key: value for key, value in row.items() if key != 'returned'}
                                                for row in envelope['rows']],
                   compare=comparison, verdict=None if name == baseline or diagnostic else
                   _verdict(comparison, score, scores, baseline_score, result['deterministic'], before, result['runs']))
        if sampling:
            arm['run_scores'] = scores
        if 'phrasings' in result:
            for key in ('phrasings', 'phrasing_fixture', 'phrasing_cases'):
                arm[key] = result[key]
        arms[name] = arm
    return dict(version=base['version'], fixture=base['fixture'], today=data['today'], baseline=baseline, arms=arms)


def _cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def _recall(value):
    return 'n/a' if value is None else f'{value:.3f}'


def report_markdown(result):
    """Render split tables, changed cases, authority hits and the decision conditions."""
    lines = ['# R1 paired recall', '', f"Fixture: {result['version']} · `{result['fixture']}`",
             f"Today: {result['today']} · Baseline: {_cell(result['baseline'])}", '']
    for name, arm in result['arms'].items():
        diagnostic = '; diagnostic (no verdict)' if arm.get('diagnostic') else ''
        lines.append(f"- {_cell(name)}: {_cell(arm['evidence'])}; {arm['runs']} run(s){diagnostic}")
    lines += ['', 'Sampling recall, hit totals and noise use observed majority representatives; '
              'trap case counts include every run. Per-run recall and uncertainty appear below.']
    for split in pairs.SPLITS:
        lines += ['', f'## {split}', '',
                  '| Arm | Needed recall | Needed hit/total | Cases with trap | Noise | Fixed | Regressed | Uncertain | Unchanged-pass | Unchanged-fail |',
                  '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
        for name, arm in result['arms'].items():
            total = arm['score'][split]['total']
            counts = arm['compare'][split]['counts']
            traps = len(arm['compare'][split]['trap_regressions'])
            values = [name, _recall(total['needed_recall']), f"{total['needed_hit']}/{total['needed_total']}",
                      traps, total['noise_count'],
                      *[counts[label] for label in ('fixed', 'regressed', 'uncertain', 'unchanged-pass', 'unchanged-fail')]]
            lines.append('| ' + ' | '.join(_cell(value) for value in values) + ' |')
        for name, arm in result['arms'].items():
            comparison = arm['compare'][split]
            lines += ['', f'### {_cell(name)} ({split})', '']
            if not arm['deterministic']:
                lines.append('Per-run needed recall: ' + ', '.join(_recall(v) for v in comparison['needed_recalls']) +
                             f"; min/max {_recall(comparison['recall_min'])}/{_recall(comparison['recall_max'])}; "
                             f"spread {_recall(comparison['recall_spread'])}; "
                             f"mean difference from baseline {_recall(comparison['recall_difference'])}.")
            for row in comparison['changes']:
                if row['change'].startswith('unchanged-'):
                    continue
                extra = f"; {row['passes']}/{row['runs']}, spread {row['spread']}" if 'passes' in row else ''
                lines.append(f"- {_cell(row['id'])} ({row['kind']}): {row['change']}{extra}")
            lines += ['', 'Trap regressions:', '']
            lines += [f"- {_cell(row['id'])} ({row['kind']}): {row['trap_hits']} trap hit(s)"
                      for row in comparison['trap_regressions']] or ['None.']
    lines += ['', '## Verdicts', '']
    for name, arm in result['arms'].items():
        verdict = arm['verdict']
        if arm.get('diagnostic'):
            lines.append(f'- {_cell(name)}: diagnostic (no verdict).')
        elif verdict is None:
            lines.append(f'- {_cell(name)}: baseline.')
        else:
            lines.append(f"- {_cell(name)}: {'preferred' if verdict['preferred'] else 'not preferred'}; "
                         f"failed: {', '.join(verdict['failed']) or 'none'}. {verdict['note']}")
    return '\n'.join(lines) + '\n'
