"""#1077: a plan's direct reply in another writing script than the owner's words goes to the worker.

Real temporary stores and the orchestrator harness; fixture plans and workers, no model or CLI.
The script rule is ``telegram_locale.language``, the one Still already uses for the owner's language.
"""
from test_orchestrator import Harness
from test_orchestrator import plan


def reply(text):
    return dict(plan('codex', ''), reply=text, recall_query='', brief={'notes': '', 'lens': '', 'needs': ''})


class ReplyLanguage(Harness):
    def receipts(self, job):
        return [r for r in self.store.work_decisions(job) if r.get('route') == 'plan-reply-withheld']

    def test_korean_reply_to_a_chinese_request_goes_to_the_worker(self):
        self.script([reply('장 볼 목록이에요.')], goals=[True])
        job, result = self.run_work('帮我整理一份购物清单。')
        self.assertEqual(result['response'], 'cli answer')
        self.assertEqual(len(self.engine.turns), 1)
        [receipt] = self.receipts(job)
        self.assertEqual(receipt['reason'], 'reply-language')
        self.assertFalse({'reply', 'request', 'query'} & receipt.keys())

    def test_chinese_reply_to_a_korean_request_goes_to_the_worker(self):
        self.script([reply('这是购物清单。')], goals=[True])
        job, result = self.run_work('장 볼 목록 정리해줘')
        self.assertEqual(result['response'], 'cli answer')
        self.assertEqual([r['reason'] for r in self.receipts(job)], ['reply-language'])

    def test_japanese_reply_to_a_japanese_request_is_used(self):
        self.script([reply('買い物リストです。')])
        job, result = self.run_work('買い物リストを作って')
        self.assertEqual(result['response'], '買い物リストです。')
        self.assertEqual(self.engine.turns, [])
        self.assertEqual(self.receipts(job), [])

    def test_same_script_reply_is_used(self):
        self.script([reply('밀푀유나베 재료예요.')])
        job, result = self.run_work('밀푀유나베')
        self.assertEqual(result['response'], '밀푀유나베 재료예요.')
        self.assertEqual(self.engine.turns, [])
        self.assertEqual(self.receipts(job), [])

    def test_latin_script_reply_to_a_korean_request_is_used(self):
        """The script rule cannot tell Korean from Latin-script text apart from Hangul; it never withholds there."""
        self.script([reply('Here is the list.')])
        job, result = self.run_work('영어로 답해줘')
        self.assertEqual(result['response'], 'Here is the list.')
        self.assertEqual(self.receipts(job), [])
