"""#1036: the progress-update cases validate and the form comparison is model-free and keeps the owner-store check apart."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import progress_forms as pf, scenarios as sc  # noqa: E402

DOMAINS = {'weather-watch', 'delivery-tracking', 'availability-watch', 'research-progress', 'price-watch',
           'mail-followup', 'market-check'}
VARIANTS = {'nochange', 'change', 'failure', 'stalled'}


def meta():
    return pf.progress_scenarios(dirs=[ROOT / 'evals/scenarios'], include_local=False)


def test_cases_cover_every_domain_variant_and_both_reader_profiles():
    found = meta()
    assert Counter((m['domain'], m['variant'], m['reader']) for m in found.values()) == Counter(
        (d, v, r) for d in DOMAINS for v in VARIANTS for r in ('short', 'evidence'))
    assert {m['split'] for m in found.values()} == {'dev', 'heldout'}
    assert 'progress_update' in sc.OPT_IN_RUBRIC and 'progress_update' not in sc.DEFAULT_RUBRIC
    for scenario in sc.load(dirs=[ROOT / 'evals/scenarios'], include_local=False):
        if scenario['id'] in found:
            assert scenario['rubric'][0] == 'progress_update' and 'reader_form' in scenario['rubric']
            assert 'Judge only' in scenario['turns'][0]['note'] and 'reader' in scenario['good_secretary']


def test_the_962_durable_fact_scenario_judges_passing_mention():
    [scenario] = sc.load(dirs=[ROOT / 'evals/scenarios'], include_local=False, ids=['communication-durable-fact-in-passing'])
    assert {'reader_form', 'conversational'} <= set(scenario['rubric'])
    assert scenario['expect']['memory_any'] == [['갑각류']] and 'passing' in scenario['good_secretary']


def _records(found, score, extra=None):
    rows = [{'scenario': i, 'source': m['source'], 'split': m['split'],
             'value': {'rubric_progress_update': score(i), 'rubric_reader_form': 4.0, 'rubric_honest_unverified': 4.0}}
            for i, m in found.items()]
    for ident, source, value in extra or ():
        rows.append({'scenario': ident, 'source': source, 'split': 'heldout', 'value': {'rubric_progress_update': value}})
    return rows


def test_a_better_candidate_is_preferred_and_the_owner_store_check_is_reported_apart():
    found = meta()
    local = {'owner-watch': {'split': 'heldout', 'source': 'local', 'domain': 'price-watch', 'variant': 'change', 'reader': 'short'}}
    both = {**found, **local}
    base = _records(found, lambda i: 3.0, [('owner-watch', 'local', 5.0)])
    cand = _records(found, lambda i: 4.0, [('owner-watch', 'local', 1.0)])
    result = pf.compare_arms({'current': base, 'bluf': cand}, both, baseline='current')
    row = result['arms']['bluf']
    assert row['preferred'] is True
    assert row['paired_delta']['dev']['progress_update'] == 1.0 and row['paired_delta']['heldout']['progress_update'] == 1.0
    assert row['paired_delta']['owner_store_check']['progress_update'] == -4.0
    assert row['partitions']['owner_store_check']['cases'] == 1
    assert row['partitions']['dev']['cases'] + row['partitions']['heldout']['cases'] == len(found)
    assert 'preferred over current: True' in pf.render_markdown(result)


def test_a_form_that_helps_one_reader_but_hurts_the_other_or_the_honesty_floor_is_not_preferred():
    found = meta()
    base = _records(found, lambda i: 3.0)
    hurts_evidence = _records(found, lambda i: 4.0 if found[i]['reader'] == 'short' else 2.0)
    assert pf.compare_arms({'a': base, 'b': hurts_evidence}, found, baseline='a')['arms']['b']['preferred'] is False
    cand = _records(found, lambda i: 4.0)
    for row in cand:
        row['value']['rubric_honest_unverified'] = 3.0
    assert pf.compare_arms({'a': base, 'b': cand}, found, baseline='a')['arms']['b']['preferred'] is False


def test_missing_honesty_grades_leave_the_preference_undetermined():
    found = meta()
    base, cand = _records(found, lambda i: 3.0), _records(found, lambda i: 4.0)
    for row in base + cand:
        del row['value']['rubric_honest_unverified']
    assert pf.compare_arms({'a': base, 'b': cand}, found, baseline='a')['arms']['b']['preferred'] is False


def test_the_cli_reads_record_files_without_a_model(tmp_path, capsys):
    from agentos_eval import cli
    found = meta()
    for name, score in (('a', 3.0), ('b', 4.0)):
        (tmp_path / f'{name}.json').write_text(json.dumps(_records(found, lambda i, s=score: s)))
    old = pf.progress_scenarios
    pf.progress_scenarios = lambda **kw: found
    try:
        cli.main(['progress-forms', '--baseline', 'a', '--arm', f'a={tmp_path}/a.json', '--arm', f'b={tmp_path}/b.json'])
    finally:
        pf.progress_scenarios = old
    assert 'preferred over a: True' in capsys.readouterr().out
