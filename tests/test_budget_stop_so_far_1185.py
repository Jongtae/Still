"""#1185: the budget-stop "So far" is the AI's answer line, never the host's read record."""
import json
import time
import unittest

from personal_agent.messages import msg
from test_budget_continue import BudgetContinueTests as Base

READ='Station Seoul\nTemperature: 18 °C\nWind: 12 km/h\nForecast: Tue rain, Wed sun'


class BudgetStopSoFarTests(unittest.TestCase):
    def stop(self, response):
        helper=Base()
        service,store,job,run,_=helper.stopped(progress=False)
        with store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (job,'list_notes','succeeded',json.dumps({'evidence':{'count':3,'titles':[READ]}}),time.time()))
            db.execute('UPDATE jobs SET response=?,owner_verified=? WHERE id=?',(response,READ,job))
        self.assertTrue(service.offer_budget_continue(run))
        return service.budget_continue_offer(job)

    def test_answer_line_leads_not_read_text(self):
        offer=self.stop('Draft: sunny in the morning, rain later.\nMore detail.')
        self.assertIn('Draft: sunny in the morning, rain later.',offer['text'])
        for raw in ('Station Seoul','km/h','Forecast: Tue'):
            self.assertNotIn(raw,offer['text'])

    def test_no_answer_uses_step_count(self):
        offer=self.stop(None)
        self.assertIn(msg('budget_continue.done_steps',count=1),offer['text'])
        self.assertNotIn('Station Seoul',offer['text'])

    def test_long_answer_not_cut_mid_word(self):
        offer=self.stop(' '.join(['forecast']*60))
        line=[part for part in offer['text'].splitlines() if 'forecast' in part][0]
        self.assertIn('forecast…',line)
        words=line.split('지금까지:')[1].replace('…','').split()
        self.assertTrue(words and all(word=='forecast' for word in words))

    def test_resume_brief_keeps_read_facts(self):
        offer=self.stop('Draft ready.')
        self.assertIn('Station Seoul',offer['progress'])


if __name__=='__main__':
    unittest.main()
