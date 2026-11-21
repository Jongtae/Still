"""#963: owner-facing schedule lines show times in the owner's home zone, not a foreign IANA name or UTC."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from personal_agent import preparations as prep


def row(zone, due_local, **extra):
    due = datetime.fromisoformat(due_local).replace(tzinfo=ZoneInfo(zone)).timestamp()
    return {'kind': prep.KIND_PREPARE, 'due_at': due, 'timezone': zone, 'recurrence': None,
            'delivery_mode': None, 'channel': prep.CHANNEL_WEB, **extra}


class HomeZoneScheduleTests(unittest.TestCase):
    def test_a_one_off_in_another_zone_is_shown_in_the_home_zone(self):
        text = prep.schedule_text(row('America/New_York', '2026-12-01T09:00'), 'Asia/Seoul')
        self.assertIn('23:00', text)
        self.assertNotIn('America/New_York', text)
        self.assertNotIn('09:00', text)

    def test_the_stored_zone_is_named_when_the_home_zone_is_unknown(self):
        self.assertIn('America/New_York', prep.schedule_text(row('America/New_York', '2026-12-01T09:00'), None))

    def test_a_repeating_rule_keeps_its_own_zone_so_its_days_stay_true(self):
        text = prep.schedule_text(row('America/New_York', '2026-12-07T21:00', recurrence='RRULE:FREQ=WEEKLY;BYDAY=MO'),
                                  'Asia/Seoul')
        self.assertIn('America/New_York', text)
        self.assertIn('21:00', text)

    def test_an_rrule_end_in_utc_is_never_printed_as_utc(self):
        rule = 'RRULE:FREQ=DAILY;UNTIL=20261231T140000Z'
        self.assertNotIn('UTC', prep.rule_text(rule, timezone_name='America/New_York', home_zone='America/New_York'))
        self.assertIn('23:00', prep.rule_text(rule, timezone_name='Asia/Seoul', home_zone='Asia/Seoul'))
        self.assertIn('09:00', prep.rule_text(rule, timezone_name='America/New_York', home_zone='America/New_York'))

    def test_local_text_converts_to_the_home_zone(self):
        ts = row('America/New_York', '2026-12-01T09:00')['due_at']
        shown = prep.local_text(ts, 'America/New_York', 'Asia/Seoul')
        self.assertIn('23:00', shown)
        self.assertNotIn('America', shown)
