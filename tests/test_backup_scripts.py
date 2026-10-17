import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]
class BackupScripts(unittest.TestCase):
 def test_backup_restore(self):
  with tempfile.TemporaryDirectory() as folder:
   source=Path(folder)/'source'
   from personal_agent.quickstart_store import QuickStore
   store=QuickStore(source);store.claim(store.bootstrap.read_text(),'a-long-test-password')
   store.put('file_roots',[{'path':'/private'}]);store.secret('telegram_token','secret');store.enqueue('remember this','work')
   references=Path(folder)/'references';managed=Path(folder)/'managed';references.mkdir();managed.mkdir();(references/'source.txt').write_text('portable source')
   from personal_agent.file_workspace import FileWorkspace
   files=FileWorkspace(store);state=files.configure([str(references)],str(managed));files.save('portable-result','portable','portable result',[files.read(state['references'][0]['id'],'source.txt')])
   store.put('a2a_delegations',{'task':{'id':'task','owner':'owner','correlation_id':'private-correlation','state':'completed','peer':'compatibility-a2a-peer','skill':'bounded-research','artifacts':[{'id':'a','text':'private artifact'}]}})
   store.put('calendar_create',{'draft':{'id':'draft','payload':{'summary':'private'},'owner':'owner','approval':'secret','state':'created','hash':'safe-hash','result':{'id':'event-1','summary':'private'}}})
   store.put('drive_excerpt_approvals',{'excerpt':{'text':'private drive text','approval_id':'secret','owner':'owner'}})
   archive=Path(folder)/'backup.tar.gz';target=Path(folder)/'target'
   subprocess.run(['python3',str(ROOT/'scripts/agentos-backup.py'),str(source),str(archive)],check=True,capture_output=True,text=True)
   subprocess.run(['python3',str(ROOT/'scripts/agentos-restore.py'),str(archive),str(target)],check=True,capture_output=True,text=True)
   restored=QuickStore(target)
   self.assertEqual(restored.jobs()[0]['message'],'remember this')
   self.assertFalse(restored.claimed())
   self.assertIsNone(restored.config('file_roots'))
   self.assertIsNone(restored.config('file_workspace'))
   with restored.db() as db:self.assertEqual(db.execute("SELECT count(*) FROM file_workspace_results WHERE state='detached' AND path IS NULL AND workspace_id IS NULL").fetchone()[0],1)
   exported=restored.config('a2a_delegations')['task'];self.assertEqual(exported['state'],'completed');self.assertNotIn('owner',exported);self.assertNotIn('correlation_id',exported);self.assertNotIn('artifacts',exported)
   calendar=restored.config('calendar_create');self.assertEqual(calendar['draft']['state'],'created');self.assertEqual(calendar['draft']['result']['id'],'event-1')
   self.assertNotIn('private',str(calendar));self.assertNotIn('secret',str(calendar));self.assertNotIn('owner',str(calendar))
   self.assertIsNone(restored.config('drive_excerpt_approvals'))
   self.assertFalse((target/'private'/'connections.json').exists())
