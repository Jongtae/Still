"""#767: an attempt's outcome judgment reads every result the worker relied on, not only the newest three.

Evidence class: model-free.  A temporary ``QuickStore``, a real ``Capabilities`` with a fake API client
in place of the network, and ``orchestrator.observations_with_pages`` unchanged.  No tool, site or task
is named in ``src``.
"""
import tempfile
import unittest
from pathlib import Path

from personal_agent.agent_runtime import ATTEMPT_RESULT_CHARS, Capabilities
from personal_agent.orchestrator import (OBSERVATION_CHARS, PAGE_TEXT_MIN_CHARS, fair_shares,
                                         observations_with_pages)
from personal_agent.quickstart_store import QuickStore


class FakeApi:
    def __init__(self, effect):
        self.effect = effect

    def call(self, args):
        return {'status': 200, 'body': {'guests': ['guest-marker-1', 'guest-marker-2']},
                'provenance': {'effect': self.effect}}


class AttemptResultTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'data')
        self.job = self.store.enqueue('x', 'k')
        self.caps = Capabilities(self.store, None, {}, '', self.job, lambda *args: None)

    def test_a_plain_api_read_joins_the_judgment_without_an_effect_receipt(self):
        self.caps._api = FakeApi('read')
        self.caps.execute('api_request', {'connection': 'fixture', 'method': 'GET', 'path': '/event'})
        pages = self.caps.take_browser_pages()
        self.assertEqual(len(pages), 1)
        self.assertIn('guest-marker-1', pages[0][2])
        self.assertIn('guest-marker-1', observations_with_pages(['- api_request: {"status": 200}'], pages))

    def test_a_state_changing_api_call_does_not_join(self):
        self.caps._api = FakeApi('mutate')
        self.caps.execute('api_request', {'connection': 'fixture', 'method': 'POST', 'path': '/event', 'body': '{}'})
        self.assertEqual(self.caps.take_browser_pages(), [])

    def test_the_attempt_keeps_results_by_characters_oldest_dropped_first(self):
        for n in range(8):
            self.caps._keep_browser_page({'url': f'r{n}', 'title': '', 'text': f'result-{n} ' + 'x' * 1000})
        self.assertEqual(len(self.caps.take_browser_pages()), 8, 'eight small results all stay')
        big = ATTEMPT_RESULT_CHARS // 2
        for n in range(3):
            self.caps._keep_browser_page({'url': f'big{n}', 'title': '', 'text': f'big-{n} ' + 'y' * big})
        kept = [url for url, _title, _text in self.caps.take_browser_pages()]
        self.assertEqual(kept, ['big2'], 'two halves no longer fit with a third; the newest stays')
        self.caps._keep_browser_page({'url': 'huge', 'title': '', 'text': 'z' * (ATTEMPT_RESULT_CHARS * 2)})
        (_url, _title, text), = self.caps.take_browser_pages()
        self.assertEqual(len(text), ATTEMPT_RESULT_CHARS, 'one result alone is cut to the bound, never dropped')


class FairSplitTests(unittest.TestCase):
    def test_fair_shares_give_small_items_what_they_need_and_split_the_rest(self):
        self.assertEqual(fair_shares([100, 5000, 5000], 3100), [100, 1500, 1500])
        self.assertEqual(fair_shares([10, 20], 1000), [10, 20])
        self.assertEqual(fair_shares([], 1000), [])

    def test_the_newest_result_no_longer_crowds_out_the_earlier_ones(self):
        # Newest first, as ``take_browser_pages`` returns them: one large result, then five earlier reads.
        pages = [('newest', '', 'NEWEST ' + 'n' * 40000)] + [
            (f'read{n}', '', f'EARLIER-{n} ' + 'e' * 3000) for n in range(5)]
        observed = observations_with_pages(['- code_run: {"exit_status": 0}'], pages)
        self.assertLessEqual(len(observed), OBSERVATION_CHARS)
        for n in range(5):
            self.assertIn(f'EARLIER-{n}', observed)
        self.assertIn('NEWEST', observed)
        self.assertNotIn('not shown here', observed)

    def test_results_left_out_for_room_are_counted(self):
        pages = [(f'r{n}', '', f'R{n} ' + 'q' * 1000) for n in range(120)]
        observed = observations_with_pages([], pages)
        self.assertLessEqual(len(observed), OBSERVATION_CHARS)
        self.assertIn('R0 ', observed, 'the newest is kept')
        shown = sum(f'- r{n}:' in observed for n in range(120))
        self.assertGreater(shown, 3)
        self.assertIn(f'({120 - shown} older results this attempt read are not shown here, for room.)', observed)
        self.assertTrue(all(len(line) >= PAGE_TEXT_MIN_CHARS for line in observed.split('\n')
                            if line.startswith('R')), 'every shown result keeps at least the minimum')


if __name__ == '__main__':
    unittest.main()
