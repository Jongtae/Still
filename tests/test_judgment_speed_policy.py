"""#472: choose the Judgment model by quality and measured speed, not price.

Evidence class: deterministic unit tests with a fake clock, the fixture
OpenAI-compatible transport and the fake CLI runner from
``test_decision_routes``.  No live provider, CLI or account is used; the
latencies are scripted, so nothing here claims how fast a real model is.
"""
import os

from personal_agent.decision import DEFAULT_DECISION_PROVIDER, NO_CANDIDATE, ModelDecisionEngine, UnavailableDecisionEngine
from personal_agent.decision_qualification import CASE_IDS, latency, qualify
from personal_agent.decision_routes import pick_fastest
from personal_agent.providers import ModelAdapter
from test_decision_routes import OPENAI_KEY, CliRunner, OpenAITransport, ServiceFixture, oracle, parse_prompt


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TimedTransport(OpenAITransport):
    """Each judgment takes ``seconds(purpose, facts)`` on the fake clock."""

    def __init__(self, clock, seconds, judge=oracle):
        super().__init__(judge)
        self.clock, self.seconds = clock, seconds

    def __call__(self, url, body, headers=None, timeout=60):
        self.clock.now += self.seconds(*parse_prompt(body['messages'][-1]['content']))
        return super().__call__(url, body, headers, timeout)


def engine(transport):
    return ModelDecisionEngine(ModelAdapter(transport), lambda: (dict(DEFAULT_DECISION_PROVIDER), OPENAI_KEY))


def flaky_reference(answers):
    """The oracle, except "그거 저장해" after a result answers ``answers`` in turn (#470)."""
    answers = list(answers)

    def judge(purpose, facts, kind, options):
        if facts.get('owner_message') == '그거 저장해' and facts.get('previous_reply') and answers:
            return answers.pop(0), 0.9
        return oracle(purpose, facts, kind, options)
    return judge


REFERENCE = CASE_IDS.index('reference-to-previous-result')


def test_qualify_records_each_case_latency_on_the_injected_clock():
    clock = Clock()
    slow = 'failed-stays-failed'
    seconds = lambda purpose, facts: 6.0 if facts.get('observed_status') == 'failed' else 2.0
    result = qualify(engine(TimedTransport(clock, seconds)), timer=clock)
    assert result['qualified']
    assert [row['seconds'] for row in result['results']] == [[6.0] if row['case'] == slow else [2.0]
                                                             for row in result['results']]
    assert result['latency'] == {'p50': 2.0, 'p95': 6.0, 'samples': len(CASE_IDS)}
    assert result['pass_rate'] == 1.0


def test_latency_is_nearest_rank_and_empty_without_samples():
    assert latency([]) is None
    assert latency([3.0, 1.0, 2.0]) == {'p50': 2.0, 'p95': 3.0, 'samples': 3}
    assert latency([1.0] * 19 + [9.0]) == {'p50': 1.0, 'p95': 1.0, 'samples': 20}


def test_one_unlucky_answer_on_a_borderline_case_does_not_decide_the_model():
    clock = Clock()
    timed = lambda answers: TimedTransport(clock, lambda purpose, facts: 1.0, flaky_reference(answers))
    # The #470 shape: wrong once, then right twice - a majority of three runs passed.
    result = qualify(engine(timed([NO_CANDIDATE, 'reference', 'reference'])), trials=3, timer=clock)
    assert result['qualified']
    assert {key: result['results'][REFERENCE][key] for key in ('passed', 'trials', 'passes', 'seconds')} == \
        {'passed': True, 'trials': 3, 'passes': 2, 'seconds': [1.0, 1.0, 1.0]}
    assert result['pass_rate'] == round((len(CASE_IDS) + 1) / (len(CASE_IDS) + 2), 3)
    assert result['latency']['samples'] == len(CASE_IDS) + 2
    # One run (lowest_qualified, explicit): the same first answer excludes it.
    one_shot = qualify(engine(timed([NO_CANDIDATE, 'reference', 'reference'])), timer=clock)
    assert not one_shot['qualified'] and one_shot['ran'] == REFERENCE + 1
    assert 'trials' not in one_shot['results'][REFERENCE]


def test_a_case_that_fails_most_runs_still_fails_and_stops_once_decided():
    clock = Clock()
    timed = lambda answers: TimedTransport(clock, lambda purpose, facts: 1.0, flaky_reference(answers))
    mostly_wrong = qualify(engine(timed([NO_CANDIDATE, 'reference', NO_CANDIDATE])), trials=3, timer=clock)
    assert not mostly_wrong['qualified']
    assert (mostly_wrong['results'][REFERENCE]['trials'], mostly_wrong['results'][REFERENCE]['passes']) == (3, 1)
    twice_wrong = qualify(engine(timed([NO_CANDIDATE, NO_CANDIDATE, 'reference'])), trials=3, timer=clock)
    assert (twice_wrong['results'][REFERENCE]['trials'], twice_wrong['results'][REFERENCE]['passes']) == (2, 0), \
        'two failures out of three runs decide it; the third run is not spent'
    # A case that passes first time is never repeated.
    assert all('trials' not in row for row in twice_wrong['results'][:REFERENCE])


def test_an_engine_failure_is_not_repeated_as_variance():
    unavailable = UnavailableDecisionEngine()
    unavailable.last_failure = 'timeout'
    result = qualify(unavailable, trials=3, timer=Clock())
    assert not result['qualified'] and result['ran'] == 1
    assert 'trials' not in result['results'][0]


def row(p50, p95=None, pass_rate=1.0, score=1.0):
    return {'p50_seconds': p50, 'p95_seconds': p95 if p95 is not None else p50, 'pass_rate': pass_rate, 'score': score}


def test_pick_fastest_prefers_the_lower_median():
    assert pick_fastest([row(11.0), row(3.4), row(4.1)]) == 1


def test_pick_fastest_with_equal_latency_takes_the_higher_score_then_the_ranked_order():
    assert pick_fastest([row(3.0, score=0.9), row(3.0, score=1.0)]) == 1
    assert pick_fastest([row(3.0, pass_rate=0.923), row(3.1)]) == 1, 'within the tie ratio the steadier one wins'
    assert pick_fastest([row(3.0), row(3.0)]) == 0, 'all equal: the ranked order'
    assert pick_fastest([row(3.0, pass_rate=0.923), row(4.0)]) == 0, 'a clearly faster model is not traded away'


def test_pick_fastest_guards_against_a_slow_tail():
    assert pick_fastest([row(3.0, p95=20.0), row(4.0, p95=5.0)]) == 1
    assert pick_fastest([row(3.0, p95=6.0), row(4.0, p95=5.0)]) == 0, 'a tail within the guard keeps the faster median'
    assert pick_fastest([row(3.0), {'pass_rate': 1.0, 'score': 1.0}]) == 0, 'an unmeasured row ranks last'


class TimedCli(CliRunner):
    """The fixture CLIs, where each judgment of ``model`` takes ``seconds[model]`` on ``now``."""

    def __init__(self, seconds, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seconds, self.now = seconds, 0.0

    def __call__(self, argv, *args, **kwargs):
        if '--output-schema' in argv or '--json-schema' in argv:
            self.now += self.seconds.get(argv[argv.index('--model') + 1] if '--model' in argv else None, 1.0)
        return super().__call__(argv, *args, **kwargs)


class FastestQualifiedServiceTests(ServiceFixture):
    def timed(self, seconds, *args, **kwargs):
        runner = TimedCli(seconds, *args, **kwargs)
        service = self.service(runner=runner)
        service.decision_routes.timer = lambda: runner.now
        return service

    def judged_models(self):
        return [call['argv'][call['argv'].index('--model') + 1] for call in self.runner.calls
                if os.path.basename(call['argv'][0]) == 'claude' and '--json-schema' in call['argv']]

    def test_the_fastest_passing_candidate_is_used_and_the_record_says_why(self):
        service = self.timed({'haiku': 7.5, 'sonnet': 3.0})
        status = service.activate_decision_route({'transport': 'subscription_cli', 'engine': 'claude-code'})
        active = status['active']
        self.assertEqual((active['model_policy'], active['requested_model']), ('fastest_qualified', 'sonnet'))
        record = active['qualification']
        self.assertEqual((record['model'], record['p50_seconds'], record['p95_seconds'], record['pass_rate']),
                         ('sonnet', 3.0, 3.0, 1.0))
        self.assertEqual(record['compared'], [
            {'model': 'haiku', 'qualified': True, 'p50_seconds': 7.5, 'p95_seconds': 7.5},
            {'model': 'sonnet', 'qualified': True, 'p50_seconds': 3.0, 'p95_seconds': 3.0}])
        check = next(e for e in status['subscription_cli'] if e['id'] == 'claude-code')['check']
        self.assertEqual([(row['model'], row.get('selected', False)) for row in check['candidates']],
                         [('haiku', False), ('sonnet', True)])
        self.judge(service)
        self.assertEqual(self.judged_models()[-1], 'sonnet')

    def test_a_faster_model_that_fails_the_bar_is_never_chosen(self):
        from test_decision_routes import careless
        service = self.timed({'haiku': 1.0, 'sonnet': 9.0}, {None: oracle, 'haiku': careless, 'sonnet': oracle})
        active = service.activate_decision_route({'transport': 'subscription_cli', 'engine': 'claude-code'})['active']
        self.assertEqual(active['requested_model'], 'sonnet')
        self.assertEqual([(row['model'], row['qualified']) for row in active['qualification']['compared']],
                         [('haiku', False), ('sonnet', True)])
        self.assertEqual(active['qualification']['compared'][0]['failed_case'], CASE_IDS[1])

    def test_lowest_qualified_keeps_its_first_pass_order_and_one_shot_runs(self):
        service = self.timed({'haiku': 7.5, 'sonnet': 3.0})
        active = service.activate_decision_route({'transport': 'subscription_cli', 'engine': 'claude-code',
                                                  'model_policy': 'lowest_qualified'})['active']
        self.assertEqual((active['model_policy'], active['requested_model']), ('lowest_qualified', 'haiku'))
        self.assertEqual(set(self.judged_models()), {'haiku'}, 'a later candidate is not run once one passed')
        self.assertNotIn('compared', active['qualification'])

    def test_the_follow_default_measures_every_candidate(self):
        service = self.timed({'haiku': 7.5, 'sonnet': 3.0})
        self.store.put('subscription_engine', {'id': 'claude-code', 'connected_at': 1,
                                               'authentication': 'owner-confirmed-official-login'})
        active = self.follow(service)['active']
        self.assertEqual((active['source'], active['model_policy'], active['requested_model']),
                         ('follow', 'fastest_qualified', 'sonnet'))
        self.assertEqual(len(self.judged_models()), 2 * len(CASE_IDS))

    def test_a_cli_update_requalifies_a_follow_row_under_the_current_follow_policy(self):
        from pathlib import Path
        service = self.timed({'haiku': 7.5, 'sonnet': 3.0})
        self.store.put('subscription_engine', {'id': 'claude-code', 'connected_at': 1,
                                               'authentication': 'owner-confirmed-official-login'})
        self.follow(service)
        # A follow row committed by #679 cheapest-first.
        self.store.put('decision_route', {**self.store.config('decision_route'), 'model_policy': 'lowest_qualified',
                                          'requested_model': 'haiku', 'effort': None})
        Path(self.finder('claude')).write_bytes(b'#!/bin/sh\n# an updated release\n')
        self.assertEqual(self.judge(service).outcome, 'unavailable')
        self.assertTrue(service.run_due_qualification())
        self.assertEqual(service.decision_routes._jobs()['update:claude-code']['state'], 'passed')
        active = service.decision_routes.active()
        self.assertEqual((active['mode'], active['model_policy'], active['requested_model']),
                         ('follow_main', 'fastest_qualified', 'sonnet'))

    def test_an_owner_choice_of_lowest_qualified_survives_a_cli_update(self):
        from pathlib import Path
        service = self.timed({'haiku': 7.5, 'sonnet': 3.0})
        service.activate_decision_route({'transport': 'subscription_cli', 'engine': 'claude-code',
                                         'model_policy': 'lowest_qualified'})
        Path(self.finder('claude')).write_bytes(b'#!/bin/sh\n# an updated release\n')
        self.assertEqual(self.judge(service).outcome, 'unavailable')
        self.assertTrue(service.run_due_qualification())
        active = service.decision_routes.active()
        self.assertEqual((active['model_policy'], active['requested_model']), ('lowest_qualified', 'haiku'))
