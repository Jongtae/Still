"""#367: forget keeps a record and covers the conversation transcript; delete erases it."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from personal_agent.agent_runtime import MEMORY_OWNER, Capabilities, ToolError, evidence_summary
from personal_agent.owner_forget import UNDO_SECONDS, ForgetError, OwnerForget
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore, forgotten_turn
from personal_agent.subscription_engines import SubscriptionEngines  # noqa: F401 (store fixtures import order)


class ForgetTranscript(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = QuickStore(Path(tmp.name) / 'state')
        self.now = 1000.0
        self.forget = OwnerForget(self.store, clock=lambda: self.now)

    def exchange(self, owner, assistant, *, status='succeeded'):
        job = self.store.enqueue(owner, str(uuid.uuid4()), owner_typed=True, channel='web')
        with self.store.db() as db:
            db.execute('UPDATE jobs SET status=?,response=?,created=10 WHERE id=?', (status, assistant, job))
            ids = [db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                              (role, text, 'web', 10, job)).lastrowid
                   for role, text in (('user', owner), ('assistant', assistant))]
        return job, ids

    def age(self, count=40):
        with self.store.db() as db:
            db.executemany('INSERT INTO messages(role,content,channel,created) VALUES (?,?,?,?)',
                           [('user', f'unrelated {i}', 'web', 20 + i) for i in range(count)])

    def refs(self, ids):
        return 'message:' + ','.join(str(i) for i in ids)

    def test_find_lists_recent_and_old_messages_newest_first_without_forgotten_ones(self):
        _job, old = self.exchange('낮에 커피 마시면서 얘기해요', '좋아요 커피 마시면서 여기서 만나요')
        self.age()
        _job, recent = self.exchange('커피도 좋아', '커피 좋죠')
        found = self.store.find_messages('커피')
        self.assertEqual([row['ref'] for row in found], [f'message:{i}' for i in reversed(old + recent)])
        self.assertEqual({row['role'] for row in found}, {'owner', 'assistant'})
        self.forget.forget(MEMORY_OWNER, self.refs(recent))
        self.assertEqual([row['ref'] for row in self.store.find_messages('커피')], [f'message:{i}' for i in reversed(old)])
        with self.assertRaises(ValueError):
            self.store.find_messages('')

    def test_a_forgotten_message_reaches_no_ai_reader_and_the_owner_view_marks_it(self):
        job, (asked, answered) = self.exchange('우리 집 비밀번호 힌트는 고양이 이름이야', '고양이 이름으로 기억할게요')
        self.age()
        receipt = self.forget.forget(MEMORY_OWNER, self.refs([asked, answered]))
        self.assertEqual((receipt['operation'], receipt['kind'], receipt['items']), ('forget', 'message', 2))
        self.assertIsNone(receipt['undo_until'])
        self.assertIn('Telegram', receipt['outside_scope'])
        self.assertNotIn('고양이', json.dumps(receipt, ensure_ascii=False))
        # Recent window, recall and the Work-level readers.
        self.assertNotIn(asked, [row['id'] for row in self.store.history()])
        self.assertEqual(self.store.search_conversation('고양이'), [])
        self.assertEqual(self.store.owner_words('고양이'), [])
        self.assertIsNone(self.store.last_reply(job))
        self.assertTrue(self.store.work_request_forgotten(job))
        with self.store.db() as db:
            copy = db.execute(f"SELECT CASE WHEN {forgotten_turn('user')} THEN '' ELSE message END AS message,"
                              f"CASE WHEN {forgotten_turn('assistant')} THEN '' ELSE response END AS response "
                              'FROM jobs WHERE id=?', (job,)).fetchone()
        self.assertEqual((copy['message'], copy['response']), ('', ''), 'the Work copies are withheld too')
        service = AgentService(self.store)
        follow = service.followup_context(self.store.job(job), None)
        self.assertEqual(follow['previous_request'], '')
        self.assertNotIn('previous_reply', follow)
        later = self.store.enqueue('그거 다시 알려줘', 'later')
        with self.store.db() as db:
            db.execute("UPDATE jobs SET relation_kind='reference',related_job_id=? WHERE id=?", (job, later))
        self.assertEqual(service.continued_exchange(self.store.job(later)), '')
        # The owner's own view keeps it, marked.
        mine = {row['id']: row for row in self.store.history(include_forgotten=True)}
        self.assertTrue(mine[asked]['forgotten'] and mine[answered]['forgotten'])
        self.assertEqual(mine[asked]['content'], '우리 집 비밀번호 힌트는 고양이 이름이야')

    def test_a_forget_is_never_purged_and_undo_restores_every_reader(self):
        job, ids = self.exchange('커피 마시면서 만나요', '여기서 만나요')
        receipt = self.forget.forget(MEMORY_OWNER, self.refs(ids))
        self.now += UNDO_SECONDS + 1
        self.assertEqual(self.forget.purge_due(), 0)
        self.assertEqual([entry['item'] for entry in self.forget.undoable(MEMORY_OWNER)], [{'messages': 2}])
        self.assertEqual(self.forget.undo(MEMORY_OWNER, receipt['receipt'])['state'], 'undone')
        self.assertEqual([row['id'] for row in self.store.history()][-2:], ids)
        self.assertEqual(self.store.last_reply(job)['content'], '여기서 만나요')
        self.assertFalse(self.store.work_request_forgotten(job))

    def test_delete_blanks_the_message_its_index_and_the_work_copies_and_cannot_be_undone(self):
        job, (asked, answered) = self.exchange('계좌번호 메모해줘 마흔둘', '마흔둘로 메모했어요')
        receipt = self.forget.forget(MEMORY_OWNER, self.refs([asked, answered]), delete=True)
        self.assertEqual(receipt['state'], 'deleted')
        self.assertEqual(receipt['counts'], {'messages': 2, 'work_copies': 2})
        with self.store.db() as db:
            rows = [row['content'] for row in db.execute('SELECT content FROM messages WHERE id IN (?,?)', (asked, answered))]
            work = db.execute('SELECT message,response FROM jobs WHERE id=?', (job,)).fetchone()
            indexed = db.execute("SELECT count(*) FROM messages_search WHERE messages_search MATCH '\"마흔둘\"*'").fetchone()[0]
        self.assertEqual((rows, work['message'], work['response'], indexed), (['', ''], '', '', 0))
        with self.assertRaises(ForgetError) as refused:
            self.forget.undo(MEMORY_OWNER, receipt['receipt'])
        self.assertEqual(refused.exception.code, 'no_receipt')
        mine = {row['id']: row for row in self.store.history(include_forgotten=True)}
        self.assertTrue(mine[asked]['forgotten'], 'the owner still sees where a deleted message was')

    def test_refusals(self):
        _job, ids = self.exchange('커피', '커피')
        running, (live, _reply) = self.exchange('지금 처리 중', '…', status='running')
        for ref, code in ((f'message:{ids[0]},x', 'bad_ref'), ('message:' + ','.join(['1'] * 21), 'bad_ref'),
                          ('message:999999', 'stale'), (f'message:{live}', 'running')):
            with self.subTest(ref=ref):
                with self.assertRaises(ForgetError) as refused:
                    self.forget.forget(MEMORY_OWNER, ref)
                self.assertEqual(refused.exception.code, code)
        self.forget.forget(MEMORY_OWNER, self.refs(ids[:1]))
        with self.assertRaises(ForgetError) as refused:
            self.forget.forget(MEMORY_OWNER, self.refs(ids[:1]))
        self.assertEqual(refused.exception.code, 'stale', 'a message is forgotten once')

    def test_the_owner_ai_finds_and_forgets_messages_through_the_tool(self):
        _job, ids = self.exchange('낮에 커피 마시면서 얘기해요', '커피 마시면서 여기서 만나요')
        work = self.store.enqueue('커피 얘기 잊어줘', 'forget-coffee')
        caps = Capabilities(self.store, None, {}, '', work, lambda *e: None)
        found = caps.execute('forget_record', {'action': 'find', 'query': '커피'})
        self.assertEqual([row['ref'] for row in found['messages']][-2:], [f'message:{i}' for i in reversed(ids)])
        self.assertEqual(evidence_summary('forget_record', found), {'action': 'find', 'found': len(found['messages'])},
                         'Evidence keeps how many were shown, never their text')
        done = caps.execute('forget_record', {'action': 'forget', 'ref': self.refs(ids)})
        self.assertEqual((done['kind'], done['items'], done['message']), ('message', 2, '잊었어요. 언제든 되돌릴 수 있어요.'))
        with self.assertRaises(ToolError):
            caps.execute('forget_record', {'action': 'find'})


if __name__ == '__main__':
    unittest.main()
