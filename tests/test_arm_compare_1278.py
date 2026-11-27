"""#1278: the versioned-text arm comparator reads recorded runs only; no model is called."""
import copy
import json
import math
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import arm_compare as ac, progress_forms as pf, runner  # noqa: E402

META = {'d1': {'split': 'dev', 'source': 'bundled'}, 'd2': {'split': 'dev', 'source': 'bundled'},
        'h1': {'split': 'heldout', 'source': 'bundled'}, 'h2': {'split': 'heldout', 'source': 'bundled'}}
SETTINGS = {'worker': 'codex', 'model': 'm-1', 'effort': 'medium', 'judge': 'j-1'}
OLD, NEW, OTHER = ac.text_digest('current text'), ac.text_digest('revised text'), ac.text_digest('scoped text')


def arm(passed, *, digest=OLD, epochs=3, honest=1.0, settings=SETTINGS, meta=META, cost={'worker_calls': 12}):
    """``passed`` and ``honest`` are numbers or ``f(scenario, epoch)``."""
    at = lambda value, ident, epoch: value(ident, epoch) if callable(value) else value
    records = [{'scenario': ident, 'epoch': epoch, 'worker': settings['worker'], 'refused_effects': [],
                'value': {'passed': at(passed, ident, epoch), 'check_honest': at(honest, ident, epoch)}}
               for ident in meta for epoch in range(1, epochs + 1)]
    return {'text_digest': digest, 'settings': dict(settings), 'scenario_ids': sorted(meta), 'records': records,
            'cost': dict(cost) if cost is not None else None}


def compare(base, cand, retries='default', **options):
    cands = {'new': cand} if 'text_digest' in cand else cand
    if retries == 'default':
        retries = arm(0.0, epochs=3 * len(cands))
    options = {'meta': META, 'target': 'passed', 'tolerances': {'check_honest': 0.05}, **options}
    return ac.compare_versioned_text(base, cands, equal_retries=retries, **options)


def verdict(*args, **kwargs):
    row = compare(*args, **kwargs)['candidates']['new']
    assert row['preferred'] is (row['verdict'] == 'preferred')
    return row['verdict']


def test_a_gain_on_dev_and_heldout_with_cost_is_preferred_and_every_arm_reports_it():
    result = compare(arm(0.0), arm(1.0, digest=NEW, cost={'worker_calls': 20, 'usd': 0.5}))
    row = result['candidates']['new']
    assert row['verdict'] == 'preferred' and row['preferred'] is True and row['reasons'] == []
    assert row['text_digest'] == NEW and row['cost'] == {'worker_calls': 20, 'usd': 0.5} and row['executions'] == 12
    assert result['baseline']['cost'] == {'worker_calls': 12} and result['equal_retries']['cost'] == {'worker_calls': 12}
    assert result['equal_retries']['executions'] == 12 and result['settings'] == SETTINGS
    for against in ('baseline', 'equal_retries'):
        assert row['against'][against]['dev']['passed'] == 1.0 and row['against'][against]['heldout']['passed'] == 1.0
    assert result['baseline']['partitions']['dev']['passed'] == 0.0
    json.dumps(result)


@pytest.mark.parametrize('flat', ['d', 'h'])
def test_a_gain_on_only_one_split_is_not_preferred(flat):
    assert verdict(arm(0.0), arm(lambda i, e: 0.0 if i.startswith(flat) else 1.0, digest=NEW)) == 'rejected'


@pytest.mark.parametrize('split', ['d', 'h'])
def test_a_safety_dimension_beyond_its_tolerance_on_either_split_is_rejected(split):
    worse = arm(1.0, digest=NEW, honest=lambda i, e: 0.9 if i.startswith(split) else 1.0)
    assert verdict(arm(0.0), worse) == 'rejected'
    assert verdict(arm(0.0), worse, tolerances={'check_honest': 0.2}) == 'preferred'


def test_each_dimension_has_its_own_tolerance():
    base, cand, retries = arm(0.0), arm(1.0, digest=NEW, honest=0.9), arm(0.0)
    for current in (base, cand, retries):
        for record in current['records']:
            record['value']['check_safe'] = 0.9 if current is cand else 1.0
    assert verdict(base, cand, retries, tolerances={'check_honest': 0.2, 'check_safe': 0.2}) == 'preferred'
    assert verdict(base, cand, retries, tolerances={'check_honest': 0.2, 'check_safe': 0.0}) == 'rejected'


def test_a_refused_run_never_counts_as_success():
    cand = arm(1.0, digest=NEW)
    for record in cand['records']:
        record['refused_effects'] = [{'method': 'POST', 'host': 'example.test'}]
    result = compare(arm(0.0), cand)
    row = result['candidates']['new']
    assert row['verdict'] == 'rejected' and row['refused_runs'] == 12 and row['partitions']['heldout']['passed'] == 0.0
    # The same marker as the sweep records it: a count on the Work's measurement.
    for record in cand['records']:
        del record['refused_effects']
        record['work'] = {'t': {'tools': [{'tool': 'code_run'}], 'refused_effects': 1}}
    assert compare(arm(0.0), cand)['candidates']['new']['refused_runs'] == 12


def test_a_success_whose_refusals_were_not_observed_is_missing_evidence():
    for work in (None, {}, {'t': {'tools': None}}, {'t': {'tools': [{'tool': 'code_run'}]}}):
        cand = arm(1.0, digest=NEW)
        del cand['records'][0]['refused_effects']
        if work is not None:
            cand['records'][0]['work'] = work
        assert verdict(arm(0.0), cand) == 'insufficient_evidence', work


def test_record_refused_reads_the_record_or_its_work_measurements():
    seen = {'tools': [{'tool': 'x'}], 'refused_effects': 0}
    assert ac.record_refused({'refused_effects': []}) is False
    assert ac.record_refused({'work': {'a': seen}}) is False
    assert ac.record_refused({'work': {'a': seen, 'b': {**seen, 'refused_effects': 2}}}) is True
    assert ac.record_refused({'work': {'a': {**seen, 'refused_effects': 2}, 'b': {'tools': None}}}) is True
    assert ac.record_refused({'work': {'a': seen, 'b': {'tools': None}}}) is None
    assert ac.record_refused({}) is None


def test_the_sweep_records_the_refusals_still_made(tmp_path):
    (tmp_path / 'private').mkdir()
    db = sqlite3.connect(tmp_path / 'private' / 'quickstart.db')
    db.execute('CREATE TABLE jobs (id, request_key, related_job_id, relation_kind, channel, chat_id)')
    db.execute('CREATE TABLE config (key, value)')
    db.execute('CREATE TABLE tool_events (id INTEGER PRIMARY KEY, job_id, tool, status, created, detail)')
    refused = {'method': 'POST', 'host': 'private.example.test', 'path': '/orders'}
    for job, detail in (('clean', {'evidence': {}}), ('refused', {'evidence': {'refused_effects': [refused]}}),
                        ('cut-off', {'refused_effects': [refused, refused]})):
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)', (job, job, None, None, 'web', '1'))
        db.execute('INSERT INTO tool_events (job_id,tool,status,created,detail) VALUES (?,?,?,?,?)',
                   (job, 'code_run', 'succeeded', 1.0, json.dumps(detail)))
    db.commit()
    db.close()
    work = runner.work_measurements(tmp_path, ['clean', 'refused', 'cut-off'])
    assert {task: row.get('refused_effects') for task, row in work.items()} == {'clean': 0, 'refused': 1, 'cut-off': 2}
    assert 'private.example.test' not in json.dumps(work)


def test_too_few_trials_is_insufficient_even_for_a_perfect_gain():
    assert verdict(arm(0.0, epochs=2), arm(1.0, digest=NEW, epochs=2)) == 'insufficient_evidence'
    assert verdict(arm(0.0, epochs=2), arm(1.0, digest=NEW, epochs=2), min_trials=2) == 'preferred'
    cand = arm(1.0, digest=NEW)
    cand['records'] = [r for r in cand['records'] if (r['scenario'], r['epoch']) != ('h2', 3)]
    assert verdict(arm(0.0), cand) == 'insufficient_evidence'
    cand['records'] = [r for r in arm(1.0, digest=NEW)['records'] if r['scenario'] != 'h2']
    assert verdict(arm(0.0), cand) == 'insufficient_evidence'


def test_unbalanced_epochs_or_an_empty_split_are_insufficient():
    cand = arm(1.0, digest=NEW)
    for record in cand['records']:
        if record['scenario'] == 'd2':
            record['epoch'] += 3
    assert verdict(arm(0.0), cand) == 'insufficient_evidence'
    dev = {k: v for k, v in META.items() if v['split'] == 'dev'}
    assert verdict(arm(0.0, meta=dev), arm(1.0, digest=NEW, meta=dev), arm(0.0, meta=dev)) == 'insufficient_evidence'


@pytest.mark.parametrize('dimension', ['passed', 'check_honest'])
def test_a_missing_grade_is_missing_evidence(dimension):
    cand = arm(1.0, digest=NEW)
    cand['records'][0]['value'][dimension] = math.nan
    assert verdict(arm(0.0), cand) == 'insufficient_evidence'


def test_a_dimension_that_does_not_apply_to_a_scenario_is_skipped_but_must_apply_somewhere():
    base, cand, retries = arm(0.0), arm(1.0, digest=NEW), arm(0.0)
    def ungraded(match):
        for current in (base, cand, retries):
            for record in current['records']:
                if match(record['scenario']):
                    record['value']['check_honest'] = math.nan
    ungraded(lambda ident: ident == 'd1')
    assert verdict(base, cand, retries) == 'preferred'
    ungraded(lambda ident: ident.startswith('d'))
    assert verdict(base, cand, retries) == 'insufficient_evidence'


def test_a_gain_within_the_epoch_spread_is_insufficient():
    # Baseline split means swing 0 -> 1 -> 0 across epochs; the candidate is higher on average only.
    base = arm(lambda i, e: 1.0 if e == 2 else 0.0)
    cand = arm(lambda i, e: 0.0 if e == 1 else 1.0, digest=NEW)
    row = compare(base, cand)['candidates']['new']
    assert row['against']['baseline']['dev']['passed'] > 0 and row['against']['baseline']['dev']['noise'] == 1.0
    assert row['verdict'] == 'insufficient_evidence'


def test_no_change_without_noise_is_rejected():
    assert verdict(arm(1.0), arm(1.0, digest=NEW), arm(1.0, epochs=3)) == 'rejected'


def test_the_candidate_must_also_beat_equal_retries():
    cand = arm(1.0, digest=NEW)
    assert verdict(arm(0.0), cand, arm(1.0)) == 'rejected'
    assert verdict(arm(0.0), cand, None) == 'insufficient_evidence'


def test_equal_retries_needs_the_whole_candidate_path_executions():
    cands = {'new': arm(1.0, digest=NEW), 'scoped': arm(0.0, digest=OTHER)}
    assert compare(arm(0.0), cands, arm(0.0, epochs=3))['candidates']['new']['verdict'] == 'insufficient_evidence'
    result = compare(arm(0.0), cands, arm(0.0, epochs=6))
    assert result['candidates']['new']['verdict'] == 'preferred'
    assert result['candidates']['scoped']['verdict'] == 'rejected'
    assert result['candidate_path_executions'] == 24 and result['equal_retries']['executions'] == 24


@pytest.mark.parametrize('cost', [None, {}, {'usd': math.nan}, {'usd': -1}, {'usd': 'unavailable'}])
def test_without_a_reported_cost_nothing_is_preferred(cost):
    cand = arm(1.0, digest=NEW)
    cand['cost'] = cost
    assert verdict(arm(0.0), cand) == 'insufficient_evidence'


def test_owner_store_cases_are_reported_and_never_decide():
    meta = {**META, 'own': {'split': 'heldout', 'source': 'local'}}
    result = compare(arm(0.0, meta=meta), arm(lambda i, e: 0.0 if i == 'own' else 1.0, digest=NEW, meta=meta),
                     arm(0.0, meta=meta), meta=meta)
    row = result['candidates']['new']
    assert row['verdict'] == 'preferred' and row['partitions']['owner_store_check']['cases'] == 1
    assert row['against']['baseline']['heldout']['cases'] == 2


def _refused(change, *, which='candidate'):
    base, cand, retries = arm(0.0), arm(1.0, digest=NEW), arm(0.0)
    change({'baseline': base, 'candidate': cand, 'retries': retries}[which])
    with pytest.raises(ValueError):
        compare(base, cand, retries)


@pytest.mark.parametrize('change', [
    lambda a: a['settings'].update(model='m-2'),
    lambda a: a['settings'].update(worker='claude-code'),
    lambda a: a['settings'].update(effort='high'),
    lambda a: a['settings'].pop('model'),
    lambda a: a['scenario_ids'].remove('h2'),
    lambda a: a['records'][0].update(scenario='elsewhere'),
    lambda a: a['records'][0].update(worker='claude-code'),
    lambda a: a['records'].append(copy.deepcopy(a['records'][0])),
    lambda a: a.update(text_digest='current'),
], ids=['model', 'worker', 'effort', 'no-model', 'scenarios', 'unknown-run', 'run-worker', 'duplicate', 'digest'])
@pytest.mark.parametrize('which', ['candidate', 'retries'])
def test_mismatched_or_malformed_arms_are_refused(change, which):
    _refused(change, which=which)


def test_arm_digests_must_say_what_ran():
    _refused(lambda a: a.update(text_digest=OLD))
    _refused(lambda a: a.update(text_digest=NEW), which='retries')


def test_different_judges_on_a_rubric_measure_are_refused():
    base, cand = arm(0.0), arm(1.0, digest=NEW)
    for current, resolved in ((base, 'judge-a'), (cand, 'judge-b')):
        for record in current['records']:
            record.update(judge_status='judged', judge_model_resolved=resolved)
            record['value']['rubric_honest'] = 4.0
    with pytest.raises(ValueError):
        compare(base, cand, tolerances={'rubric_honest': 0.05})


def judged(current, status='judged', resolved='judge-a'):
    for record in current['records']:
        record['judge_status'] = status
        if resolved:
            record['judge_model_resolved'] = resolved
    return current


@pytest.mark.parametrize('status,resolved', [('judged', 'judge-b'), ('skipped', None)])
@pytest.mark.parametrize('which', ['candidate', 'retries'])
def test_the_target_passed_is_judge_dependent_so_judges_must_match(status, resolved, which):
    arms = {'baseline': arm(0.0), 'candidate': arm(1.0, digest=NEW), 'retries': arm(0.0)}
    for name, current in arms.items():
        judged(current, *((status, resolved) if name == which else ()))
    with pytest.raises(ValueError):
        compare(arms['baseline'], arms['candidate'], arms['retries'])


@pytest.mark.parametrize('status', ['judge_error', 'budget_refused'])
def test_a_success_the_judge_never_graded_is_missing_evidence(status):
    base, cand, retries = judged(arm(0.0)), judged(arm(1.0, digest=NEW)), judged(arm(0.0))
    assert verdict(base, cand, retries) == 'preferred'
    cand['records'][0]['judge_status'] = status
    assert verdict(base, cand, retries) == 'insufficient_evidence'


@pytest.mark.parametrize('field', ['model', 'effort'])
def test_a_run_whose_recorded_engine_contradicts_the_settings_is_refused(field):
    cand = arm(1.0, digest=NEW)
    seen = {'tools': [{'tool': 'x'}], 'refused_effects': 0, 'engine': [{'model': 'm-1', 'effort': 'medium'}, {'model': None}]}
    cand['records'][0]['work'] = {'t': seen}
    assert verdict(arm(0.0), cand) == 'preferred'
    cand['records'][0]['work'] = {'t': {**seen, 'engine': [{field: 'something-else'}]}}
    with pytest.raises(ValueError):
        compare(arm(0.0), cand)


def test_a_scenario_without_a_split_and_a_gate_without_guards_are_refused():
    base, cand = arm(0.0), arm(1.0, digest=NEW)
    for options in ({'meta': {k: v for k, v in META.items() if k != 'h2'}}, {'tolerances': {}},
                    {'tolerances': {'check_honest': -0.1}}, {'tolerances': {'check_honest': math.inf}}, {'min_trials': 1}):
        with pytest.raises(ValueError):
            compare(base, cand, **options)
    with pytest.raises(ValueError):
        compare(base, {})


def test_progress_forms_output_is_unchanged():
    """Recorded from ``progress_forms.compare_arms`` before it moved onto the shared pieces."""
    golden = json.loads((ROOT / 'tests/fixtures/progress_forms_1278.json').read_text(encoding='utf-8'))
    result = pf.compare_arms(golden['arms'], golden['meta'], baseline='current')
    assert json.loads(json.dumps(result)) == golden['result']
    assert pf.render_markdown(result) == golden['markdown']
    assert [golden['result']['arms'][k]['preferred'] for k in ('better', 'dishonest', 'hurts', 'same')] == [
        True, False, False, False]
