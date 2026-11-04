"""Temporary #34 diagnostic: drive the Chromium worker directly with its stderr visible."""
import json, subprocess, sys, threading, time, http.server, socketserver
chrome = sys.argv[1]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<html><head><title>T</title></head><body><button onclick="this.textContent=\'pressed\'">Press</button><input aria-label="memo"></body></html>'
        self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
srv = socketserver.ThreadingTCPServer(('127.0.0.1', 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
origin = f'127.0.0.1:{srv.server_address[1]}'
p = subprocess.Popen([sys.executable, '-P', '-m', 'personal_agent.browser_worker_chromium', '--profile', 'p', '--chromium', chrome,
                      '--test-allow-origin', origin], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None, text=True, bufsize=1)
lines = []
threading.Thread(target=lambda: [lines.append(l) or print('<<', l.strip()[:600], flush=True) for l in p.stdout], daemon=True).start()
def wait(pred, t):
    end = time.time() + t
    while time.time() < end:
        for l in list(lines):
            try:
                m = json.loads(l)
            except ValueError:
                continue
            if pred(m): return m
        time.sleep(0.1)
    print('!! no answer within', t, flush=True); return None
print('ready:', wait(lambda m: m.get('event') in ('ready', 'unavailable'), 60), flush=True)
for i, cmd in enumerate([{'op': 'navigate', 'url': f'http://{origin}/', 'timeout': 20}, {'op': 'snapshot', 'timeout': 10},
                         {'op': 'state', 'timeout': 5}, {'op': 'cookies_export', 'timeout': 5}, {'op': 'quit', 'timeout': 5}], 1):
    cmd['id'] = i; print('>>', cmd, flush=True); p.stdin.write(json.dumps(cmd) + '\n'); p.stdin.flush()
    wait(lambda m, i=i: m.get('id') == i, cmd['timeout'] + 5)
p.kill()
