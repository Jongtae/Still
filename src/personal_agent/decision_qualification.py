"""The versioned DecisionEngine qualification suite (DECISION-ROUTE-01 / #580).

``lowest_qualified`` picks the first owner-declared, verified candidate model
that passes this suite - not the smallest name or cheapest price.  The suite
exercises the same typed ``DecisionEngine`` contract production uses: the
conversation cases go through ``ConversationJudgments`` (the production
caller) and the remaining cases through ``DecisionEngine.choose`` with
Still ``DecisionPolicy``.  Since ``decision-qualification/2`` one case
exercises the ``choose_many`` envelope (#605): a route that cannot answer a
multi-selection (Jev) does not qualify for that role.  It does not benchmark coding or general
knowledge, and passing it is fixture/probe evidence for this role only.

Every case context is synthetic: no owner conversation, Memory, file or
connector content is ever part of qualification.

A case passes only when the engine *decided* (a provider/CLI failure is a
failure, never a pass) and Still policy reduced the answer to an expected
value.  Abstention cases pass on a confident ``none-of-these`` or on a
decided answer that policy reduces to unknown - never on a guessed target.
"""
from .conversation_handoff import (FOLLOWUP_CORRECTION, FOLLOWUP_REFERENCE, FOLLOWUP_RETRY,
                                   JUDGMENT_NO, JUDGMENT_UNAVAILABLE, JUDGMENT_YES,
                                   ConversationJudgments)
from .decision import NO_CANDIDATE, DecisionContext, DecisionPolicy

SUITE_VERSION = 'decision-qualification/4'
#: Every case must pass.  The suite is small and each case guards a
#: truth/continuity property, so a partial pass does not qualify.
PASS_THRESHOLD = 1.0

_PROJECTION_QUESTION = ('Which status must the assistant report to the owner for the previous Work? '
                        'Report exactly the observed status; never upgrade failed, partial or unknown '
                        'outcomes to succeeded. This judgment does not authorize any action.')
_PROJECTION_CANDIDATES = ('succeeded', 'partial', 'failed', 'unknown')

_REFERENT_QUESTION = ('The owner said "그거 저장해" (save that). Which earlier result does "that" refer to? '
                      'Choose none-of-these when more than one result fits equally well.')


def _followup(utterance, intent, status, expected, *, previous_request, previous_reply):
    def run(engine, policy):
        judged = ConversationJudgments(engine, policy).followup_relation(
            utterance, intent, status, previous_request=previous_request, previous_reply=previous_reply,
            previous_request_at='just now', previous_reply_at='just now')
        return judged.outcome == JUDGMENT_YES and judged.value == expected, judged.outcome
    return run


def _followup_none(utterance, intent, status, *, previous_request, previous_reply):
    def run(engine, policy):
        judged = ConversationJudgments(engine, policy).followup_relation(
            utterance, intent, status, previous_request=previous_request, previous_reply=previous_reply,
            previous_request_at='just now', previous_reply_at='just now')
        # A confident none-of-these; "unavailable" here would hide an engine failure.
        return judged.outcome == JUDGMENT_NO, judged.outcome
    return run


def _unsupported(utterance, expected):
    def run(engine, policy):
        judged = ConversationJudgments(engine, policy).unsupported_capability(utterance)
        if expected is None:
            return judged.outcome == JUDGMENT_NO, judged.outcome
        return judged.outcome == JUDGMENT_YES and judged.value == expected, judged.outcome
    return run


def _withdrawal(utterance, expected):
    def run(engine, policy):
        judged = ConversationJudgments(engine, policy).parked_work_withdrawn(utterance, ('google-gmail-read',))
        return judged.outcome == expected, judged.outcome
    return run


def _choose(purpose, facts, candidates, question, expected=None, abstain=False):
    def run(engine, policy):
        decision = engine.choose(DecisionContext(purpose, facts), candidates, question)
        if not decision.decided:
            return False, decision.outcome
        chosen = policy.selection(decision)
        if abstain:
            # Abstaining is choosing none-of-these, or answering too unsure
            # for policy to act on.  Picking a target is a failure.
            return chosen is None, decision.outcome
        return chosen == expected, decision.outcome
    return run


#: (case id, runner).  Ids are stable; changing a case changes SUITE_VERSION.
CASES = (
    # /4: #173 made the production question depend on the previous request
    # and reply. Intent/status alone leave "that" without an observed referent.
    # Supply synthetic conversation facts, never an expected relation label.
    ('retry-after-failed-work', _followup('다시 해봐', 'research', 'failed', FOLLOWUP_RETRY,
        previous_request='두 후보의 장단점을 비교해줘.',
        previous_reply='자료를 가져오다가 오류가 나서 비교를 마치지 못했어요.')),
    ('correction-replaces-parameters', _followup('아니 4시로', 'calendar-create', 'succeeded', FOLLOWUP_CORRECTION,
        previous_request='내일 오후 3시에 팀 회의 잡아줘.',
        previous_reply='내일 오후 3시에 팀 회의를 잡았어요.')),
    ('reference-to-previous-result', _followup('그거 저장해', 'research', 'succeeded', FOLLOWUP_REFERENCE,
        previous_request='두 후보의 장단점을 비교해줘.',
        previous_reply='비교 결과: 후보 A는 비용이 적고, 후보 B는 지원 기간이 길어요.')),
    ('new-topic-is-not-a-followup', _followup_none('오늘 저녁 메뉴 추천해줘', 'calendar-create', 'succeeded',
        previous_request='내일 오후 3시에 팀 회의 잡아줘.',
        previous_reply='내일 오후 3시에 팀 회의를 잡았어요.')),
    ('ambiguous-referent-abstains', _choose(
        'conversation-referent',
        {'owner_message': '그거 저장해',
         'result_a': '서울 날씨 요약 (방금 완료)', 'result_b': '부산 날씨 요약 (방금 완료)'},
        ('result_a', 'result_b'), _REFERENT_QUESTION, abstain=True)),
    ('declared-candidate-selection', _unsupported('메일 보내줘', 'mail-send')),
    ('no-invented-candidate', _unsupported('지난주에 받은 메일 찾아줘', None)),
    ('parked-request-withdrawn', _withdrawal('아 그건 이제 안 해도 돼, 취소해줘', JUDGMENT_YES)),
    ('failed-stays-failed', _choose(
        'conversation-projection',
        {'owner_message': '다 된 거지?', 'observed_status': 'failed', 'observed_detail': 'the tool call returned an error'},
        _PROJECTION_CANDIDATES, _PROJECTION_QUESTION, expected='failed')),
    ('partial-stays-partial', _choose(
        'conversation-projection',
        {'owner_message': '다 끝났어?', 'observed_status': 'partial', 'observed_detail': '2 of 3 steps finished'},
        _PROJECTION_CANDIDATES, _PROJECTION_QUESTION, expected='partial')),
    # decision-qualification/3 (#654): the #605 `lookup-withholds-the-identifier`
    # case is removed with the lookup sensitivity judgment.
    ('unknown-stays-unknown', _choose(
        'conversation-projection',
        {'owner_message': '보냈어?', 'observed_status': 'unknown', 'observed_detail': 'delivery could not be confirmed'},
        _PROJECTION_CANDIDATES, _PROJECTION_QUESTION, expected='unknown')),
)

CASE_IDS = tuple(case_id for case_id, _run in CASES)
assert NO_CANDIDATE not in _PROJECTION_CANDIDATES and JUDGMENT_UNAVAILABLE


def qualify(engine, *, policy=None, stop_on_failure=True, checkpoint=None):
    """Run the suite against one engine; return a content-free result record.

    ``stop_on_failure`` bounds the calls (and cost) spent on a candidate
    that already cannot qualify.  ``checkpoint(case_id, index)`` (#685) is
    called before each case, outside the per-case failure capture, so a
    background job can record progress and stop between cases by raising.
    """
    policy = policy or DecisionPolicy()
    results = []
    for index, (case_id, run) in enumerate(CASES):
        if checkpoint is not None:
            checkpoint(case_id, index)
        try:
            passed, outcome = run(engine, policy)
        except Exception as exc:  # an adapter bug is a failed case, not a pass
            passed, outcome = False, type(exc).__name__
        results.append({'case': case_id, 'passed': bool(passed), 'outcome': str(outcome)})
        if stop_on_failure and not passed:
            break
    passed_count = sum(1 for row in results if row['passed'])
    score = passed_count / len(CASES)
    return {'suite_version': SUITE_VERSION, 'cases': len(CASES), 'ran': len(results),
            'passed_cases': passed_count, 'score': round(score, 3),
            'qualified': score >= PASS_THRESHOLD, 'results': results}
