import unittest
import subprocess
from personal_agent import https_tunnel

class OrphanTunnels(unittest.TestCase):
    """#1445 part 5: a killed server's tunnel is reclaimed when the next server starts."""

    TABLE = ('  101     1 /opt/homebrew/bin/ngrok http 127.0.0.1:8787 --host-header=rewrite --inspect=false\n'
             '  102   555 /opt/homebrew/bin/ngrok http 127.0.0.1:8787 --host-header=rewrite\n'
             '  103     1 /opt/homebrew/bin/ngrok http 127.0.0.1:8797 --host-header=rewrite\n'
             '  104     1 /usr/bin/python3 ngrok http 127.0.0.1:8787\n'
             '  105     1 ngrok http 127.0.0.1:87870\n')

    def reclaim(self, port, table=TABLE, code=0):
        import signal
        killed = []
        run = lambda argv, **_kw: subprocess.CompletedProcess(argv, code, table, '')
        ended = https_tunnel.reclaim_orphan_tunnels(port, run=run, kill=lambda pid, sig: killed.append((pid, sig)))
        self.assertEqual(ended, len(killed))
        self.assertTrue(all(sig == signal.SIGTERM for _pid, sig in killed))
        return [pid for pid, _sig in killed]

    def test_only_an_orphaned_tunnel_to_this_port_is_ended(self):
        # 102 still has its server; 103 serves another instance; 104 and 105 are not this tunnel.
        self.assertEqual(self.reclaim(8787), [101])
        self.assertEqual(self.reclaim(8797), [103])

    def test_an_unreadable_table_or_port_ends_nothing(self):
        self.assertEqual(self.reclaim(8787, code=1), [])
        self.assertEqual(self.reclaim(None), [])
        self.assertEqual(https_tunnel.reclaim_orphan_tunnels(8787, run=lambda *a, **k: (_ for _ in ()).throw(OSError())), 0)


