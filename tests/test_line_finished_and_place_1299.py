"""#1299 follow-ups: a finished window is not offered to resume; a ranked place is not hidden."""
import unittest

from personal_agent import preparations as prep

import test_day_plan_resume_1027 as resume_1027
from test_saved_place_geocode_1186 import place, tools_with


class FinishedWindowNotOffered(*resume_1027.ResumeOffers.__bases__):
    line = resume_1027.ResumeOffers.line  # the helper only, not the #1027 tests again

    def window_row(self, brief, state, reason):
        row = self.service.preparations.create(kind='prepare', goal='변동 확인', due_at=self.now + 3600,
                                               timezone='Asia/Seoul', recurrence=None, channel='web',
                                               created_from=None, state=prep.STATE_SCHEDULED,
                                               accepted_by=prep.ACCEPTED_OWNER_REQUEST, line_id=brief['line_id'],
                                               window=(3600, self.now + 5 * 86400, 10))
        with self.store.db() as db:
            db.execute('UPDATE preparations SET state=?,last_decision_reason=? WHERE id=?', (state, reason, row['id']))
            db.commit()
        return row

    def resume_row(self, brief, state, accepted_by):
        row = self.service.preparations.create(kind='prepare', goal='오늘 변동 확인', due_at=self.now + 7200,
                                               timezone='Asia/Seoul', recurrence=None, channel='web',
                                               created_from=self.receive('계획'), state=prep.STATE_PROPOSED,
                                               offer='오늘 이어서 확인할까요?', origin=prep.ORIGIN_LINE_RESUME,
                                               line_id=brief['line_id'])
        with self.store.db() as db:
            db.execute('UPDATE preparations SET state=?,accepted_by=?,created_at=? WHERE id=?',
                       (state, accepted_by, self.now + 60, row['id']))
            db.commit()

    def test_a_window_that_closed_on_its_terms_leaves_nothing_to_resume(self):
        brief = self.line()
        self.window_row(brief, prep.STATE_DELIVERED, prep.REASON_WINDOW_CLOSED)
        self.assertIsNone(self.service.preparations.line_resume_target(brief['line_id']))

    def test_a_window_the_owner_cancelled_can_still_be_offered(self):
        brief = self.line()
        self.window_row(brief, prep.STATE_CANCELLED, None)
        self.assertIsNotNone(self.service.preparations.line_resume_target(brief['line_id']))

    def test_a_failed_window_can_still_be_offered(self):
        brief = self.line()
        self.window_row(brief, prep.STATE_FAILED, prep.REASON_RUN_FAILED)
        self.assertIsNotNone(self.service.preparations.line_resume_target(brief['line_id']))

    def test_a_stopped_line_stays_offerable_after_a_finished_window(self):
        brief = self.line()
        self.window_row(brief, prep.STATE_DELIVERED, prep.REASON_WINDOW_CLOSED)
        brief, _ = self.service.preparations_service.stop_line(brief['line_id'], self.receive('멈춰'), end_line=True)
        self.assertIsNotNone(self.service.preparations.line_resume_target(brief['line_id']))

    def test_accepted_resume_work_that_was_cancelled_counts_over_the_finished_window(self):
        brief = self.line()
        self.resume_row(brief, prep.STATE_CANCELLED, prep.ACCEPTED_OWNER_ANSWER)  # dated after the window below
        self.window_row(brief, prep.STATE_DELIVERED, prep.REASON_WINDOW_CLOSED)
        self.assertIsNotNone(self.service.preparations.line_resume_target(brief['line_id']))

    def test_an_unaccepted_resume_offer_does_not_hide_the_finished_window(self):
        brief = self.line()
        self.resume_row(brief, prep.STATE_EXPIRED, None)  # dated after the window below
        self.window_row(brief, prep.STATE_DELIVERED, prep.REASON_WINDOW_CLOSED)
        self.assertIsNone(self.service.preparations.line_resume_target(brief['line_id']))


class RankedPlaceNamed(unittest.TestCase):
    county = {**place('Vale County', 'Northland', 'Vale-gun', 5.0, 5.0), 'population': 55000}
    villages = [place('Vale', 'Eastland', 'A-gun', 1.0, 1.0), place('Vale', 'Westland', 'B-gun', 2.0, 2.0)]

    def test_ambiguity_names_the_ranked_place_the_exact_name_step_drops(self):
        tools, _ = tools_with({'Vale': [self.county, *self.villages]})
        with self.assertRaises(ValueError) as caught:
            tools.weather('Vale')
        self.assertIn('Vale County Vale-gun Northland', str(caught.exception))

    def test_a_hyphenated_name_is_shortened_and_its_suffix_picks_the_area(self):
        searches = []
        tools, forecasts = tools_with({'Vale': [self.county, *self.villages]}, searches=searches)
        result = tools.weather('Vale-gun')
        self.assertEqual(searches, ['Vale-gun', 'Vale'])
        self.assertEqual(forecasts, [(5.0, 5.0)])
        self.assertEqual(result['location']['searched_text'], 'Vale-gun')

    def test_the_region_named_after_the_place_resolves_it(self):
        tools, forecasts = tools_with({'Vale': [self.county, *self.villages]})
        tools.weather('Vale Northland')
        self.assertEqual(forecasts, [(5.0, 5.0)])


if __name__ == '__main__':
    unittest.main()
