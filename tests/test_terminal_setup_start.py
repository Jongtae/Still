"""#438: what an interactive `agentos start` shows around the terminal first setup.

Evidence class: local deterministic checks (logging handlers, the browser URL,
and a node-driven run of the web app's language bootstrap).
"""
import io
import logging
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_agent import first_run
from personal_agent.quickstart import configure_logging, with_language
from personal_agent.quickstart_store import QuickStore

WEB = Path(__file__).resolve().parents[1] / 'src' / 'personal_agent' / 'web'


class TerminalLoggingTest(unittest.TestCase):
    def logged(self, tty):
        logger = logging.getLogger('personal_agent')
        saved = logger.handlers[:], logger.propagate, logger.level
        logger.handlers = []
        stream = io.StringIO()
        stream.isatty = lambda: tty
        try:
            with tempfile.TemporaryDirectory() as folder, patch('sys.stderr', stream):
                store = QuickStore(Path(folder) / 'data')
                configure_logging(store)
                child = logging.getLogger('personal_agent.service')
                child.info('working notes migration moved=0')
                child.warning('engine turn failed engine=codex')
                for handler in logger.handlers:
                    handler.flush()
                kept = (store.private / 'logs' / 'agentos.log').read_text()
                for handler in logger.handlers:
                    handler.close()
        finally:
            logger.handlers, logger.propagate, logger.level = saved
        return stream.getvalue(), kept

    def test_an_owner_terminal_shows_warnings_only_and_the_file_keeps_everything(self):
        shown, kept = self.logged(tty=True)
        self.assertNotIn('working notes migration', shown)
        self.assertIn('engine turn failed', shown)
        self.assertIn('working notes migration', kept)

    def test_a_redirected_stderr_keeps_info_lines(self):
        shown, _ = self.logged(tty=False)
        self.assertIn('working notes migration', shown)


class BrowserLanguageTest(unittest.TestCase):
    def test_the_terminal_language_rides_in_the_fragment(self):
        self.assertEqual(with_language('http://127.0.0.1:8787/', 'ko'), 'http://127.0.0.1:8787/#lang=ko')
        self.assertEqual(with_language('http://127.0.0.1:8787/#setup=abc', 'ja'),
                         'http://127.0.0.1:8787/#setup=abc&lang=ja')
        self.assertEqual(with_language('http://127.0.0.1:8787/', ''), 'http://127.0.0.1:8787/')

    def test_every_terminal_language_names_a_web_language(self):
        app = (WEB / 'app.js').read_text(encoding='utf-8')
        for terminal, web in first_run.WEB_LANGUAGES.items():
            with self.subTest(terminal=terminal):
                self.assertRegex(app, r"const LANGUAGES=\{[^}]*" + ("'zh-CN'" if web == 'zh-CN' else web) + ':')

    def test_the_web_app_keeps_a_handed_language_and_ignores_an_unknown_one(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is required for the web check')
        script = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8');
const source=app.slice(app.indexOf('const LANGUAGES='),app.indexOf('function normalizeEndpoint('));
function load(hash,stored){
 const store=new Map(stored?[['agentos-language',stored]]:[]);
 const ctx={document:{},location:{hash},window:{localStorage:{getItem:key=>store.get(key)??null,setItem:(key,value)=>store.set(key,value)}},Intl,Object,URLSearchParams};
 vm.createContext(ctx);vm.runInContext(source+';var seen=currentLanguage;',ctx);
 return [ctx.seen,store.get('agentos-language')??null];
}
assert.deepEqual(load('#lang=ko',null),['ko','ko']);
assert.deepEqual(load('#lang=zh-CN','en'),['zh-CN','zh-CN']);
assert.deepEqual(load('#lang=xx','ja'),['ja','ja']);
assert.deepEqual(load('',null),['en',null]);
assert.deepEqual(load('#tasks','ja'),['ja','ja']);
"""
        result = subprocess.run([node, '-e', script, str(WEB / 'app.js')], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
