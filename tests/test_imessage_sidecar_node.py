"""#1142: runs the Node-side sidecar tests (tests/imessage_sidecar/*.test.mjs) from pytest.

They use a fake imessage-kit and need no node_modules, chat.db or osascript.  The
Node is the host's Node.js 20+ or the pinned one Still already downloaded; where
neither exists the test is skipped locally and fails in CI, so CI never passes
without having run them.
"""
import os
import subprocess
import unittest
from pathlib import Path

from personal_agent import code_run, node_runtime

TESTS = Path(__file__).with_name('imessage_sidecar')
ROOT = Path(__file__).resolve().parent.parent


def node():
    found = code_run.find_node()
    if found:
        done = subprocess.run([found, '--version'], capture_output=True, text=True, timeout=10)
        if int(done.stdout.strip().lstrip('v').split('.')[0]) >= 20:
            return found
    home = os.environ.get('STILL_TEST_NODE_DIR')
    return node_runtime.installed(home) if home else None


class SidecarNodeTest(unittest.TestCase):
    def test_node_side_protocol_tests_pass(self):
        binary = node()
        if binary is None:
            if os.environ.get('CI'):
                self.fail('CI has no Node.js 20+ to run the iMessage sidecar tests')
            self.skipTest('no Node.js 20+ on this host')
        files = sorted(str(path) for path in TESTS.glob('*.test.mjs'))
        self.assertTrue(files)
        done = subprocess.run([binary, '--test', *files], cwd=ROOT, capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stdout[-6000:] + done.stderr[-2000:])


if __name__ == '__main__':
    unittest.main()
