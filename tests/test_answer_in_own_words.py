"""RULES-OUT-01 (#173, slice C): the AI's answer closes in the judgment's words; fixed lines are truth floors.

Before this slice a partial answer always closed with a hard-coded Korean
count ("한 단계는 끝내지 못했어요", Korean count words inside every
language's line), even when the outcome judgment had said, in its own words,
what was left.  Now the judgment's owner line (``note``, #1459, or its
``gap``) is the closing line; a fixed line is said only as the truth floor
when nothing else says it, and every fixed line comes from the catalog
(``messages.json``, #10), so the Telegram seam shows it in the owner's
language.

Evidence class: deterministic unit tests of the projection functions and the
catalog; the service-level cases (the calendar preview, the context sources)
live in ``tests/test_refused_write_outcome.py``,
``tests/test_truth_integrity_history.py`` and ``tests/test_judged_continuity.py``.
"""
import unittest

from personal_agent import telegram_locale
from personal_agent.agent_runtime import GOAL_NOT_SHOWN, GOAL_UNJUDGED
from personal_agent.conversation_projection import (TERMINAL_FAILED_HEADER, TERMINAL_FAILED_NOTE,
                                                    TERMINAL_PARTIAL_NOTE, TERMINAL_UNCONFIRMED_NOTE, answer_note,
                                                    terminal_text)
from personal_agent.messages import catalog, msg

ANSWER = '예약 페이지를 열어 빈 좌석을 찾아봤어요.'
STEPS = [('browser_click', '일치하는 요소가 없습니다.'), ('browser_open', '페이지를 열지 못했습니다.')]
NOTE = '예약 페이지가 열리지 않아 금요일 좌석은 확인하지 못했어요.'
#: Words of success a failed or partial closing line must never carry.
SUCCESS = ('완료했', '끝냈어요', '반영했', 'done', 'completed')


class ClosingLineTests(unittest.TestCase):
    def test_a_partial_work_with_a_judgment_note_closes_with_that_note_alone(self):
        note = answer_note(STEPS, 'partial', report={'note': NOTE})
        self.assertEqual(note, NOTE)
        text = terminal_text(ANSWER, None, 'partial', note=note)
        self.assertEqual(text, ANSWER + '\n\n' + NOTE)
        for fixed in (TERMINAL_PARTIAL_NOTE, TERMINAL_UNCONFIRMED_NOTE, TERMINAL_FAILED_NOTE, '단계는 끝내지 못했어요'):
            self.assertNotIn(fixed, text)

    def test_a_judgment_gap_without_a_note_also_replaces_the_fixed_line(self):
        gap = '금요일 좌석이 남았는지는 확인하지 못했어요.'
        self.assertEqual(answer_note(STEPS, 'partial', report={'gap': gap}), gap)

    def test_a_partial_work_without_a_note_gets_the_localized_truth_floor(self):
        self.assertEqual(answer_note(STEPS, 'partial'), TERMINAL_PARTIAL_NOTE)
        entry = catalog()['projection.some_steps_unfinished']
        self.assertEqual(TERMINAL_PARTIAL_NOTE, entry['ko'])
        self.assertEqual(entry['ko'], '일부 단계는 끝내지 못했어요.')
        self.assertEqual(entry['en'], "Some steps didn't finish.")
        self.assertEqual(telegram_locale.translate(TERMINAL_PARTIAL_NOTE, 'en'), entry['en'])
        # No step left a reason and the judgment said nothing: the unconfirmed floor, also from the catalog.
        text = terminal_text(ANSWER, None, 'partial')
        self.assertEqual(text, ANSWER + '\n\n' + msg('projection.answer_not_confirmed'))
        self.assertEqual(telegram_locale.translate(TERMINAL_UNCONFIRMED_NOTE, 'en'),
                         msg('projection.answer_not_confirmed', 'en'))
        self.assertFalse(any('{count}' in value for value in entry.values()), 'no Korean count word in any language')

    def test_a_failed_work_never_reads_as_success(self):
        self.assertEqual(answer_note([], 'failed'), TERMINAL_FAILED_NOTE)
        self.assertEqual(answer_note(STEPS, 'failed'), TERMINAL_FAILED_NOTE)
        with_answer = terminal_text(ANSWER, None, 'failed')
        self.assertTrue(with_answer.endswith('\n\n' + TERMINAL_FAILED_NOTE), with_answer)
        without_answer = terminal_text('', '페이지를 열지 못했습니다.', 'failed')
        self.assertTrue(without_answer.startswith(TERMINAL_FAILED_HEADER), without_answer)
        for text in (with_answer, without_answer, TERMINAL_FAILED_NOTE):
            for word in SUCCESS:
                self.assertNotIn(word, text.replace(ANSWER, ''))
        self.assertEqual(TERMINAL_FAILED_NOTE, msg('projection.i_couldn_t_finish_what_you'))

    def test_the_goal_truth_floors_come_from_the_catalog(self):
        for constant, message_id in ((GOAL_NOT_SHOWN, 'agent_runtime.goal_not_shown'),
                                     (GOAL_UNJUDGED, 'agent_runtime.goal_unjudged')):
            with self.subTest(message_id=message_id):
                entry = catalog()[message_id]
                self.assertEqual(constant, entry['ko'])
                self.assertTrue(entry['en'] and entry['en'] != entry['ko'])
                self.assertEqual(telegram_locale.translate(constant, 'en'), entry['en'])


class PendingLineTests(unittest.TestCase):
    """A draft's pending line is not repeated next to Still's own preview or confirmation (slice C).

    The calendar case runs end to end in ``tests/test_refused_write_outcome.py``; this pins the
    settings case and that every other line stays.
    """

    def setUp(self):
        import tempfile
        from types import SimpleNamespace
        from personal_agent.quickstart_service import AgentService
        from personal_agent.quickstart_store import QuickStore
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = QuickStore(temp.name)
        self.service = AgentService(self.store)
        self.store.put('telegram', {'enabled': True, 'user_id': 42, 'generation': 'g'})
        self.job = {'id': 'w1', 'channel': 'telegram:g', 'chat_id': 42}
        pending = '설정 변경은 소유자 확인을 기다립니다. 확인 전에는 아무것도 바뀌지 않았습니다.'
        self.run = SimpleNamespace(refusals=[('settings_change', pending), ('browser_open', '페이지를 열지 못했습니다.')],
                                   owner_steps=[pending])
        self.pending = pending

    def test_a_settings_draft_confirmed_on_telegram_is_not_repeated_after_the_answer(self):
        from unittest.mock import patch
        with patch.object(self.service.settings_orchestrator, 'pending_for_work', return_value=[{'id': 'd1'}]):
            steps, kept = self.service._unshown_steps(self.run, self.job, '')
        self.assertEqual(steps, [('browser_open', '페이지를 열지 못했습니다.')], 'every other line stays')
        self.assertEqual(kept, [])
        note = answer_note(steps, 'partial', kept=set(kept))
        self.assertEqual(note, TERMINAL_PARTIAL_NOTE)
        self.assertNotIn(self.pending, note)
        self.assertEqual(len(self.run.refusals), 2, 'the cause and the record keep the line')

    def test_without_a_confirmation_shown_the_pending_line_stays(self):
        steps, kept = self.service._unshown_steps(self.run, {**self.job, 'chat_id': None, 'channel': 'web'}, '')
        self.assertEqual((steps, kept), (self.run.refusals, self.run.owner_steps))


if __name__ == '__main__':
    unittest.main()
