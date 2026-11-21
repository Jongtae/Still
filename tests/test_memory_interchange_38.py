"""#38: Portable Memory export -> validate -> import -> re-export, through the reference SDK.

Evidence class: model-free unit tests over real temporary ``QuickStore`` instances, the real
``OwnerForget`` receipts and the pinned ``portable-memory`` 0.3.0 SDK (exporter, importer and
L0 validator).  No network, no live model, no owner data.
"""
import json
import tempfile
import unittest
from pathlib import Path

from portable_memory.validator import BundleValidator

from personal_agent.memory_interchange import (MemoryInterchangeError, SOURCE_KIND, export_memory, fact_id,
                                               import_memory)
from personal_agent.owner_forget import OwnerForget
from personal_agent.quickstart_store import QuickStore

OWNER = 'local-owner'


def rewrite(path, relative, text):
    """Replace one bundle file and keep its manifest and CHECKSUMS consistent (a well-formed foreign bundle)."""
    import hashlib
    data = text.encode()
    (path / relative).write_bytes(data)
    manifest = json.loads((path / 'manifest.json').read_text())
    entries = [entry for entry in manifest['files'] if entry['path'] != relative]
    entries.append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
    manifest['files'] = sorted(entries, key=lambda entry: entry['path'])
    (path / 'manifest.json').write_text(json.dumps(manifest))
    if (path / 'CHECKSUMS').exists():
        (path / 'CHECKSUMS').write_text(''.join(f"{entry['sha256']}  {entry['path']}\n" for entry in manifest['files']))


class Interchange(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = QuickStore(self.root / 'a')
        self.target = QuickStore(self.root / 'b')

    def bundle(self, store=None, name='bundle'):
        path = self.root / name
        export_memory(store or self.source, OWNER, path)
        return path

    def facts(self, path):
        text = (path / 'items' / 'fact.jsonl').read_text() if (path / 'items' / 'fact.jsonl').exists() else ''
        return [json.loads(line) for line in text.splitlines() if line]

    def tombstones(self, path):
        file = path / 'audit' / 'tombstones.jsonl'
        return [json.loads(line) for line in file.read_text().splitlines() if line] if file.exists() else []

    def pending(self, store):
        return store.memory_candidates(OWNER, limit=101)

    def current(self, store):
        return {(row['memory_key'], row['content']) for row in store.memories(OWNER, limit=101)}

    def accept_all(self, store):
        for row in self.pending(store):
            approval = store.issue_candidate_memory_approval(OWNER, row['work_ref'], row['id'], row['content_digest'])
            store.accept_memory_candidate(OWNER, row['work_ref'], row['id'], row['content_digest'],
                                          approval['approval_token'])

    def test_export_validates_and_is_byte_stable(self):
        self.source.save_memory('profile.home_city', 'Seoul')
        self.source.save_memory('preference.coffee', 'oat latte')
        first, second = self.bundle(name='one'), self.bundle(name='two')
        self.assertEqual(BundleValidator().validate(first).issues, [])
        for part in ('items/fact.jsonl', 'CHECKSUMS'):
            self.assertEqual((first / part).read_bytes(), (second / part).read_bytes(), part)
        one, two = (json.loads((path / 'manifest.json').read_text()) for path in (first, second))
        one.pop('createdAt'), two.pop('createdAt')
        self.assertEqual(one, two, 'the manifest differs only in its creation time')
        self.assertEqual({(fact['predicate'], fact['object']) for fact in self.facts(first)},
                         {('profile.home_city', 'Seoul'), ('preference.coffee', 'oat latte')})

    def test_supersession_maps_to_valid_to_and_superseded_by(self):
        old = self.source.save_memory('profile.home_city', 'Busan')
        new = self.source.save_memory('profile.home_city', 'Seoul')
        facts = {fact['id']: fact for fact in self.facts(self.bundle())}
        self.assertEqual(facts[fact_id(old['id'])]['supersededBy'], fact_id(new['id']))
        self.assertIsNotNone(facts[fact_id(old['id'])].get('tValidTo'))
        self.assertIsNone(facts[fact_id(new['id'])].get('tValidTo'))

    def test_a_row_whose_successor_was_forgotten_is_still_history(self):
        old = self.source.save_memory('profile.home_city', 'Busan')
        new = self.source.save_memory('profile.home_city', 'Seoul')
        OwnerForget(self.source).forget(OWNER, 'memory:' + new['id'])
        facts = {fact['id']: fact for fact in self.facts(self.bundle())}
        self.assertEqual(set(facts), set(), 'forgetting a value forgets its whole chain')
        self.assertEqual(import_memory(self.target, OWNER, self.bundle(name='b2'))['candidates'], 0)
        self.assertTrue(old)

    def test_a_last_day_is_exported_and_a_time_bounded_fact_is_not_revived(self):
        self.source.put('calendar_timezone', 'Asia/Seoul')
        past = self.source.save_memory('plan.trip', 'Jeju', valid_until='2020-01-31')
        later = self.source.save_memory('plan.desk', 'window seat', valid_until='2999-12-31')
        facts = {fact['id']: fact for fact in self.facts(self.bundle())}
        self.assertEqual(facts[fact_id(past['id'])]['tValidTo'], '2020-01-31T15:00:00Z', 'next midnight in Seoul')
        self.assertEqual(facts[fact_id(later['id'])]['tValidTo'], '2999-12-31T15:00:00Z')
        report = import_memory(self.target, OWNER, self.bundle(name='b2'))
        self.assertEqual((report['candidates'], report['history_skipped'], report['time_bounded_skipped']), (0, 1, 1))
        self.assertEqual(self.pending(self.target), [])

    def test_a_fact_that_has_not_started_is_not_imported(self):
        self.source.save_memory('profile.home_city', 'Seoul')
        path = self.bundle()
        fact = self.facts(path)[0]
        line = json.dumps({**fact, 'tValidFrom': '2999-01-01T00:00:00Z'}, sort_keys=True, separators=(',', ':')) + '\n'
        rewrite(path, 'items/fact.jsonl', line)
        report = import_memory(self.target, OWNER, path)
        self.assertEqual((report['candidates'], report['time_bounded_skipped']), (0, 1))

    def test_a_tombstone_is_kept_for_later_bundles(self):
        self.source.save_memory('preference.coffee', 'oat latte')
        path = self.bundle()
        fact = self.facts(path)[0]
        tomb = {'id': 'tomb_1', 'op': 'delete', 'targetKind': 'fact', 'targetID': fact['id'],
                'deletedAt': '2026-11-01T00:00:00Z', 'actor': 'owner', 'derived': {}}
        only_tomb = self.root / 'only_tomb'
        export_memory(QuickStore(self.root / 'empty'), OWNER, only_tomb)
        rewrite(only_tomb, 'audit/tombstones.jsonl', json.dumps(tomb) + '\n')
        first = import_memory(self.target, OWNER, only_tomb)
        self.assertEqual((first['tombstones'], first['candidates']), (1, 0))
        later = import_memory(self.target, OWNER, path)
        self.assertEqual((later['candidates'], later['skipped_tombstoned']), (0, 1),
                         'a fact deleted at its source earlier never comes back from a later bundle')

    def test_a_fact_about_someone_else_is_refused(self):
        self.source.save_memory('profile.home_city', 'Seoul')
        path = self.bundle()
        fact = self.facts(path)[0]
        line = json.dumps({**fact, 'id': 'fact_alice', 'subject': 'Alice', 'object': 'Berlin', 'text': 'Alice lives in Berlin'},
                          sort_keys=True, separators=(',', ':')) + '\n'
        rewrite(path, 'items/fact.jsonl', (path / 'items' / 'fact.jsonl').read_text() + line)
        report = import_memory(self.target, OWNER, path)
        self.assertEqual((report['candidates'], report['other_subject']), (1, 1))
        self.assertEqual([row['content'] for row in self.pending(self.target)], ['Seoul'])

    def test_import_makes_only_pending_candidates_and_round_trips_after_review(self):
        self.source.save_memory('profile.home_city', 'Busan')
        self.source.save_memory('profile.home_city', 'Seoul')
        self.source.save_memory('preference.coffee', 'oat latte')
        report = import_memory(self.target, OWNER, self.bundle())
        self.assertEqual((report['candidates'], report['history_skipped'], report['current_memory_changed']), (2, 1, False))
        self.assertEqual(self.current(self.target), set(), 'nothing imported is current Memory before review')
        rows = self.pending(self.target)
        self.assertTrue(all(row['source']['kind'] == SOURCE_KIND for row in rows))
        self.assertEqual({row['work_ref'] for row in rows}, {report['review_ref']})
        self.accept_all(self.target)
        self.assertEqual(self.current(self.target), self.current(self.source))
        again = self.bundle(self.target, 'again')
        self.assertEqual({(f['predicate'], f['object']) for f in self.facts(again) if not f.get('tValidTo')},
                         self.current(self.source))

    def test_import_is_idempotent_and_does_not_reask_a_declined_value(self):
        self.source.save_memory('preference.coffee', 'oat latte')
        path = self.bundle()
        self.assertEqual(import_memory(self.target, OWNER, path)['candidates'], 1)
        self.assertEqual(import_memory(self.target, OWNER, path)['candidates'], 0)
        row = self.pending(self.target)[0]
        self.target.reject_memory_candidate(OWNER, row['work_ref'], row['id'], row['content_digest'])
        report = import_memory(self.target, OWNER, path)
        self.assertEqual((report['candidates'], report['already_known']), (0, 1))
        self.assertEqual(self.pending(self.target), [])

    def test_a_corrupt_bundle_is_refused_before_any_write(self):
        self.source.save_memory('preference.coffee', 'oat latte')
        path = self.bundle()
        with (path / 'items' / 'fact.jsonl').open('a') as file:
            file.write('{"id":"fact_injected"}\n')
        with self.assertRaises(MemoryInterchangeError):
            import_memory(self.target, OWNER, path)
        (path / 'manifest.json').unlink()
        with self.assertRaises(MemoryInterchangeError):
            import_memory(self.target, OWNER, path)
        self.assertEqual(self.pending(self.target), [])

    def test_forget_delete_and_undo_receipts_shape_the_export(self):
        forgotten = self.source.save_memory('preference.tea', 'green tea')
        deleted = self.source.save_memory('preference.snack', 'almonds')
        restored = self.source.save_memory('preference.coffee', 'oat latte')
        forget = OwnerForget(self.source)
        forget.forget(OWNER, 'memory:' + forgotten['id'])
        forget.forget(OWNER, 'memory:' + deleted['id'], delete=True)
        undo = forget.forget(OWNER, 'memory:' + restored['id'])
        forget.undo(OWNER, undo['receipt'])
        path = self.bundle()
        self.assertEqual({fact['id'] for fact in self.facts(path)}, {fact_id(restored['id'])})
        marks = {tomb['targetID']: tomb['op'] for tomb in self.tombstones(path)}
        self.assertEqual(marks, {fact_id(forgotten['id']): 'delete', fact_id(deleted['id']): 'redact'})
        self.assertNotIn('green tea', (path / 'audit' / 'tombstones.jsonl').read_text(), 'receipts carry no content')

    def test_a_tombstone_withdraws_a_pending_import_but_never_current_memory(self):
        kept = self.source.save_memory('preference.coffee', 'oat latte')
        dropped = self.source.save_memory('preference.tea', 'green tea')
        import_memory(self.target, OWNER, self.bundle(name='first'))
        own = self.target.save_memory('preference.coffee', 'oat latte')
        self.assertTrue(own)
        forget = OwnerForget(self.source)
        forget.forget(OWNER, 'memory:' + kept['id'])
        forget.forget(OWNER, 'memory:' + dropped['id'])
        report = import_memory(self.target, OWNER, self.bundle(name='second'))
        self.assertEqual(report['withdrawn'], 2, 'both still-pending imported candidates are withdrawn')
        self.assertIn(('preference.coffee', 'oat latte'), self.current(self.target),
                      'an imported tombstone never changes current Memory')
        self.assertEqual(self.pending(self.target), [])

    def test_a_value_forgotten_here_is_not_proposed_again(self):
        mine = self.target.save_memory('preference.tea', 'green tea')
        OwnerForget(self.target).forget(OWNER, 'memory:' + mine['id'])
        self.source.save_memory('preference.tea', 'green tea')
        report = import_memory(self.target, OWNER, self.bundle())
        self.assertEqual((report['candidates'], report['forgotten_here']), (0, 1))

    def test_secrets_never_leave_and_secret_values_are_refused_on_import(self):
        self.source.secret('telegram_token', 'bot-token-value-1234-abcdef')
        self.source.save_memory('note.bot', 'my token is bot-token-value-1234-abcdef')
        path = self.bundle()
        self.assertNotIn('bot-token-value-1234-abcdef', (path / 'items' / 'fact.jsonl').read_text())
        self.target.secret('telegram_token', 'bot-token-value-9999-zzzzzz')
        foreign = self.root / 'foreign'
        other = QuickStore(self.root / 'c')
        other.save_memory('note.bot', 'my token is bot-token-value-9999-zzzzzz')
        export_memory(other, OWNER, foreign)
        report = import_memory(self.target, OWNER, foreign)
        self.assertEqual((report['candidates'], report['secret_refused']), (0, 1))

    def test_export_carries_no_candidates_approvals_or_authority(self):
        self.source.save_memory('preference.coffee', 'oat latte')
        self.source.save_memory_candidate(None, 'preference.tea', 'pending tea', OWNER, 'work-1')
        path = self.bundle()
        manifest = json.loads((path / 'manifest.json').read_text())
        self.assertEqual({kind for kind, count in manifest['counts'].items() if count}, {'fact'})
        names = {file.relative_to(path).as_posix() for file in path.rglob('*') if file.is_file()}
        self.assertEqual(names, {'manifest.json', 'CHECKSUMS', 'items/fact.jsonl'})
        self.assertNotIn('pending tea', (path / 'items' / 'fact.jsonl').read_text())

    def test_unsupported_kinds_are_disclosed_not_stored(self):
        self.source.save_memory('preference.coffee', 'oat latte')
        path = self.bundle()
        manifest = json.loads((path / 'manifest.json').read_text())
        line = '{"id":"proc_1","name":"x","triggerPattern":"y","steps":["do it"],"successCount":0,"failureCount":0,"enabled":true}\n'
        (path / 'items' / 'procedure.jsonl').write_text(line)
        import hashlib
        data = line.encode()
        manifest['files'].append({'path': 'items/procedure.jsonl', 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
        manifest['counts']['procedure'] = 1
        (path / 'manifest.json').write_text(json.dumps(manifest))
        report = import_memory(self.target, OWNER, path)
        self.assertEqual(report['not_imported'], {'procedure': 1})
        self.assertEqual(report['candidates'], 1)


if __name__ == '__main__':
    unittest.main()
