"""#1052: Settings › AI shows Still's own lines translated and its routes by name.

The server sends Still's fixed lines in Korean from ``messages.json``; the web
screen translates them by their Korean text.  ``/messages.js`` hands the
screen the catalog's own translations, so a reworded catalog line can no longer
fall out of the screen's table and show up in Korean in the English UI.
"""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from personal_agent.messages import catalog, web_catalog_script
from personal_agent.quickstart import make_handler
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

WEB = Path(__file__).parents[1] / 'src' / 'personal_agent' / 'web'


def node_run(script, *args):
    node = shutil.which('node')
    if node is None:
        raise unittest.SkipTest('Node is needed for JavaScript behaviour checks')
    return subprocess.run([node, '-e', script, str(WEB / 'app.js'), *args], check=True,
                          capture_output=True, text=True, timeout=30).stdout


class MessageCatalogOnTheScreen(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.script = Path(tmp.name) / 'messages.js'
        self.script.write_bytes(web_catalog_script())

    def test_every_catalog_line_reaches_the_screen_in_each_language(self):
        out = node_run(r"""
const fs=require('node:fs');new Function(fs.readFileSync(process.argv[2],'utf8'))();
const ui=require(process.argv[1]);process.stdout.write(JSON.stringify(ui.I18N));
""", str(self.script))
        tables = json.loads(out)
        for entry in catalog().values():
            for lang, web in (('en', 'en'), ('ja', 'ja'), ('zh', 'zh-CN')):
                if entry.get(lang) and entry[lang] != entry['ko']:
                    self.assertTrue(tables[web].get(entry['ko']), (web, entry['ko']))

    def test_the_reported_lines_are_english_and_the_screen_table_wins(self):
        out = node_run(r"""
const fs=require('node:fs');new Function(fs.readFileSync(process.argv[2],'utf8'))();
const ui=require(process.argv[1]);ui.setLanguage('en');
const before=ui.t('직접 선택');ui.mergeMessageCatalog({en:{'직접 선택':'catalog wording'}});
process.stdout.write(JSON.stringify({
  web:ui.t('개인 자료가 포함된 작업에서는 그 작업에 한해 꺼져요'),
  codex:ui.t(globalThis.STILL_MESSAGES.en ? Object.keys(globalThis.STILL_MESSAGES.en).find(k=>k.startsWith('Codex는 로그인 프로필')) : ''),
  same:ui.t('직접 선택')===before,
}));
""", str(self.script))
        result = json.loads(out)
        self.assertEqual(result['web'], 'turned off only for a task that includes private material')
        self.assertTrue(result['codex'].startswith('Codex includes AGENTS.override.md'))
        self.assertTrue(result['same'], 'a line the screen already translates keeps its own wording')

    def test_a_running_candidate_check_reads_in_the_screen_language(self):
        """#1067: the multi-candidate progress is translated before its values are filled in."""
        template = catalog()['decision_routes.check_progress_candidate']['ko']
        effective = {'template': catalog()['decision_routes.checking_keep_route']['ko'],
                     'params': {'progress': template.format(candidate=2, candidates=3, model='claude-haiku-4-5', index=2, cases=11)},
                     'progress_template': template,
                     'progress_params': {'candidate': 2, 'candidates': 3, 'model': 'claude-haiku-4-5', 'index': 2, 'cases': 11}}
        out = node_run(r"""
const fs=require('node:fs');new Function(fs.readFileSync(process.argv[2],'utf8'))();
const ui=require(process.argv[1]);ui.mergeMessageCatalog(globalThis.STILL_MESSAGES);ui.setLanguage('en');
// The renderer lives in the browser-only part of app.js; take that one function and run it with the page's t().
const app=fs.readFileSync(process.argv[1],'utf8'),start=app.indexOf('function effectiveJudgmentText(');
const effectiveJudgmentText=new Function('t','return '+app.slice(start,app.indexOf('\nfunction ',start)))(ui.t);
process.stdout.write(effectiveJudgmentText(JSON.parse(process.argv[3])));
""", str(self.script), json.dumps(effective, ensure_ascii=False))
        self.assertIn('candidate 2 of 3, claude-haiku-4-5: case 2 of 11', out)
        self.assertNotIn('후보', out)

    def test_the_check_started_notice_clears_once_the_check_has_run(self):
        """#1069: the notice belongs to a running check and goes once the result is in."""
        out = node_run(r"""
const fs=require('node:fs'),app=fs.readFileSync(process.argv[1],'utf8'),start=app.indexOf('function judgmentCheckNoticeDone(');
const make=new Function('let judgmentCheckNotice=null;'+app.slice(start,app.indexOf('\nfunction ',start))+
 ';return {start:at=>{judgmentCheckNotice={started:at,seen:false};},done:route=>judgmentCheckNoticeDone(route)};');
const old={active:{qualification:{at:100}},follow_check:{checked_at:100}},checking={...old,effective:{state:'checking'}};
const runThrough=make();runThrough.start(200);
const queuedGap=runThrough.done(old),whileChecking=runThrough.done(checking),afterCheck=runThrough.done(old);
const fast=make();fast.start(200);
const fastFinished=fast.done({active:{qualification:{at:205}},effective:{state:'active'}});
const none=make().done(old);
process.stdout.write(JSON.stringify({queuedGap,whileChecking,afterCheck,fastFinished,none}));
""")
        self.assertEqual(json.loads(out), {
            'queuedGap': False,      # an earlier result does not end a check that has not shown up yet
            'whileChecking': False,
            'afterCheck': True,      # seen running, now finished
            'fastFinished': True,    # never seen running, but a result newer than the start
            'none': False,           # no notice showing, nothing to clear
        })

    def test_without_the_catalog_the_screen_still_loads(self):
        out = node_run("const ui=require(process.argv[1]);ui.mergeMessageCatalog(undefined);process.stdout.write(ui.t('AI'))")
        self.assertEqual(out, 'AI')

    def test_the_page_loads_the_catalog_before_the_app_and_the_server_serves_it(self):
        html = (WEB / 'index.html').read_text(encoding='utf-8')
        self.assertLess(html.index('<script src="/messages.js" defer></script>'), html.index('<script src="/app.js" defer></script>'))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(AgentService(QuickStore(Path(tmp.name) / 'state'))))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with urlopen(f'http://127.0.0.1:{server.server_port}/messages.js', timeout=5) as response:
                self.assertEqual(response.headers.get_content_type(), 'text/javascript')
                self.assertEqual(response.read(), web_catalog_script())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
