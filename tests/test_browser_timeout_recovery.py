"""Navigation failure stays explicit; a later read can observe a loaded document."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import tempfile
import unittest
from pathlib import Path

from personal_agent import browser_session as bs
from personal_agent.agent_runtime import ToolError
from test_browser_session import FakeDriver, ORIGIN, PASSWORD


class TimeoutDriver(FakeDriver):
    def goto(self, url, timeout):
        super().goto(url, timeout)
        raise TimeoutError('upstream detail must not leave driver')


class TimeoutRecoveryTests(unittest.TestCase):
    def test_explicit_read_observes_the_partial_page_without_replaying_navigation(self):
        driver = TimeoutDriver()
        session = bs.BrowserSession(lambda: driver, work_id='w', excluded=lambda: [PASSWORD])
        with self.assertRaises(ToolError) as caught:
            session.open({'url': ORIGIN + '/login', 'effect': 'read'})
        self.assertEqual(caught.exception.code, 'browser_timeout')
        page = session.read()
        self.assertEqual(page['state'], 'login_required')
        self.assertEqual(page['load_state'], 'navigation_timeout')
        self.assertNotIn(PASSWORD, json.dumps(page))
        self.assertEqual(len([x for x in driver.log if x[0] == 'goto']), 1)

    def test_refused_app_handoff_keeps_the_web_document_observable(self):
        class AppHandoffDriver(FakeDriver):
            def goto(self, url, timeout):
                super().goto(url, timeout)
                raise ToolError('unsupported app handoff', 'unsupported_navigation')
        driver = AppHandoffDriver()
        session = bs.BrowserSession(lambda: driver, work_id='w')
        with self.assertRaises(ToolError) as caught:
            session.open({'url': ORIGIN + '/product', 'effect': 'read'})
        self.assertEqual(caught.exception.code, 'unsupported_navigation')
        page = session.read()
        self.assertEqual(page['url'], ORIGIN + '/product')
        self.assertEqual(page['load_state'], 'navigation_interrupted')
        self.assertEqual(len([x for x in driver.log if x[0] == 'goto']), 1)

    def test_previous_elements_are_invalidated_until_a_fresh_read(self):
        driver = FakeDriver()
        session = bs.BrowserSession(lambda: driver, work_id='w')
        session.open({'url': ORIGIN + '/product', 'effect': 'read'})
        driver.goto = lambda *args: (_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(ToolError):
            session.open({'url': ORIGIN + '/cart', 'effect': 'read'})
        self.assertIsNone(session.last)
        with self.assertRaises(ToolError) as caught:
            session.click({'target': 1, 'effect': 'navigate'})
        self.assertEqual(caught.exception.code, 'no_page')
        page = session.read()
        self.assertEqual(page['url'], ORIGIN + '/product', 'observe the actual page, not the requested URL')

    def test_no_document_and_non_timeout_refusals_still_have_no_page(self):
        for error in (TimeoutError(), ToolError(bs.BLOCKED_TEXT, 'blocked_destination')):
            with self.subTest(error=type(error).__name__):
                driver = FakeDriver()
                driver.goto = lambda *args: (_ for _ in ()).throw(error)
                driver.snapshot = lambda: {'url': 'about:blank', 'text': '', 'elements': []}
                session = bs.BrowserSession(lambda: driver, work_id='w')
                with self.assertRaises(ToolError):
                    session.open({'url': ORIGIN + '/product', 'effect': 'read'})
                with self.assertRaises(ToolError) as caught:
                    session.read()
                self.assertEqual(caught.exception.code, 'no_page')
                self.assertIsNone(session.last)

    def test_worker_process_timeout_keeps_the_real_observable_document(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / 'commands'
            worker = Path(__file__).with_name('browser_fake_worker.py')
            driver = bs.WebKitWorkerDriver('p', command=[sys.executable, str(worker), str(log)])
            session = bs.BrowserSession(lambda: driver, work_id='w')
            try:
                with self.assertRaises(ToolError):
                    session.open({'url': 'https://shop.test/partial', 'effect': 'read'})
                self.assertEqual(session.read()['url'], 'https://shop.test/partial')
                commands = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(sum(c['op'] == 'navigate' for c in commands), 1)
            finally:
                session.close()


@unittest.skipUnless(os.environ.get('AGENTOS_REAL_BROWSER_TESTS') == '1' and sys.platform == 'darwin',
                     'opt-in real WebKit boundary')
class RealWebKitRecoveryTests(unittest.TestCase):
    def test_a_slow_subresource_does_not_prevent_reading_the_rendered_document(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == '/slow.png':
                    time.sleep(5)
                    try:
                        self.send_response(200)
                        self.end_headers()
                    except OSError:
                        pass
                    return
                script = b'<script>setTimeout(()=>location.href="fixtureapp://open",100)</script>' if self.path == '/handoff' else b''
                body = b'<html><body><h1>Visible document</h1><img src="/slow.png">' + script + b'</body></html>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = f'127.0.0.1:{server.server_port}'
        with tempfile.TemporaryDirectory() as folder:
            driver = bs.WebKitWorkerDriver('slow-fixture', cwd=folder, allowed_origins_for_tests=(origin,))
            session = bs.BrowserSession(lambda: driver, work_id='fixture', action_seconds=1,
                                        allowed_origins_for_tests=(origin,))
            try:
                for path, code, load_state in (('/', 'browser_timeout', 'navigation_timeout'),
                                               ('/handoff', 'unsupported_navigation', 'navigation_interrupted')):
                    with self.subTest(path=path):
                        with self.assertRaises(ToolError) as caught:
                            session.open({'url': 'http://' + origin + path, 'effect': 'read'})
                        self.assertEqual(caught.exception.code, code)
                        page = session.read()
                        self.assertEqual(page['load_state'], load_state)
                        self.assertIn('Visible document', page['text'])
            finally:
                session.close()

    def test_slow_address_check_is_typed_and_still_refused(self):
        source = ('import time; from personal_agent import browser_worker as w; '
                  'w.destination_refusal=lambda *a,**kw:(time.sleep(4),None)[1]; w.main()')
        with tempfile.TemporaryDirectory() as folder:
            driver = bs.WebKitWorkerDriver('dns-fixture', cwd=folder,
                                           command=[sys.executable, '-c', source, '--profile', 'dns-fixture'])
            try:
                with self.assertRaises(ToolError) as caught:
                    driver.goto('https://example.com', 8)
                self.assertEqual(caught.exception.code, 'destination_check_timeout')
            finally:
                driver.close()
