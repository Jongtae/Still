"""Temporary HTTPS links for phone sign-in and input, with tunnel recovery."""
import json
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path

LOG = logging.getLogger(__name__)


def _own_tunnel(command, port):
    """Whether ``command`` is a tunnel Still starts for ``port``: ``ngrok http 127.0.0.1:<port>``
    or a Cloudflare quick tunnel to ``http://127.0.0.1:<port>`` (#523)."""
    from . import quick_tunnel
    words = str(command or '').split()
    return bool(words) and (Path(words[0]).name == 'ngrok' and words[1:3] == ['http', f'127.0.0.1:{port}']
                            or quick_tunnel.is_own(words, port))


def reclaim_orphan_tunnels(port, *, run=subprocess.run, kill=None):
    """At server start: end tunnels to this server's ``port`` whose server died (#1445 part 5).

    A server killed without its shutdown path (a crash, a forced restart) leaves its phone input
    or remote login tunnel running, re-parented to launchd (parent pid 1); a provider that allows
    one tunnel at a time then refuses the next link (#1436).  Only a process whose command line
    is Still's own tunnel to this port and whose parent is gone is signalled.  Returns how many.
    """
    import os
    import signal
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return 0
    try:
        result = run(['ps', '-axo', 'pid=,ppid=,command='], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return 0
    ended = 0
    for line in (result.stdout or '').splitlines() if result.returncode == 0 else ():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or parts[1] != '1' or not _own_tunnel(parts[2], port):
            continue
        try:
            (kill or os.kill)(int(parts[0]), signal.SIGTERM)
            ended += 1
        except OSError:
            continue
    if ended:
        LOG.info('stopped %d tunnel(s) a stopped server left behind (port=%s)', ended, port)
    return ended


def tunnel_available(find=None):
    """Whether a link can be tried: ngrok or cloudflared installed, or a pinned cloudflared for this platform (#523)."""
    from . import quick_tunnel
    from .subscription_engines import find_cli
    find = find or find_cli
    return bool(find('ngrok') or find('cloudflared') or quick_tunnel.pin())


def _locator(store):
    """``name -> binary``: an installed tool first; cloudflared otherwise from Still's pinned download."""
    from . import quick_tunnel
    from .subscription_engines import find_cli

    def locate(name):
        found = find_cli(name)
        if found or name != 'cloudflared':
            return found
        return quick_tunnel.ensure(quick_tunnel.tool_dir(store))
    return locate


def start_tunnel(port, popen=subprocess.Popen, timeout=30, *, store, locate=None):
    """Start a public HTTPS tunnel to one loopback port; returns ``(process, https_url)``.

    #208: the one place a public tunnel is started.  ``store`` is the owner
    store whose work mode is read first, and again before a second attempt;
    while it is on this raises ``work_mode.WorkModeRefused`` and nothing is
    spawned.

    ngrok is tried first when it is installed (the owner set it up, #897);
    when it is missing or does not open (no account token, a busy free
    tunnel), a Cloudflare quick tunnel is started instead, which needs no
    account (#523).  ``locate(name)`` returns a binary or None; an injected
    ``popen`` without one finds ngrok only, as before.  Either way the output
    is drained in a daemon thread for the tunnel's life and the address is
    awaited with a real deadline (review P2-4).
    """
    from . import work_mode
    work_mode.refuse(store, 'tunnel')
    # #932: under launchd PATH lacks Homebrew; the real launcher runs the found binary.
    locate = locate or (_locator(store) if popen is subprocess.Popen else {'ngrok': 'ngrok'}.get)
    failure = None
    ngrok = locate('ngrok')
    if ngrok:
        try:
            return _start_ngrok(ngrok, port, popen, timeout)
        except (OSError, RuntimeError) as exc:
            LOG.info('ngrok did not open a link (%s); trying a Cloudflare quick tunnel', type(exc).__name__)
            failure = exc
    # Read again before the fallback is sought: finding it may download it (PR #529 review P1).
    work_mode.refuse(store, 'tunnel')
    try:
        cloudflared = locate('cloudflared')
    except (OSError, RuntimeError):
        if failure is not None:
            raise failure from None
        raise
    if not cloudflared:
        raise failure or FileNotFoundError(2, 'No such file or directory', 'cloudflared')
    # #554: a first download takes a while; a switch that landed meanwhile still refuses the spawn.
    work_mode.refuse(store, 'tunnel')
    return _start_cloudflared(cloudflared, port, popen, timeout, store)


def _start_ngrok(binary, port, popen, timeout):
    """``ngrok http``: ``--host-header=rewrite`` makes the instance see ``localhost:<port>``.

    ngrok still adds forwarding headers, so every tunneled request is
    *relayed* and never local (#594).  ``--inspect=false`` keeps no request
    log in ngrok's local inspector (review P3-2).
    """
    process = popen([binary, 'http', f'127.0.0.1:{int(port)}', '--host-header=rewrite', '--inspect=false',
                     '--log', 'stdout', '--log-format', 'json'],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def read(entry):
        url = entry.get('url')
        if isinstance(url, str) and url.startswith('https://'):
            return url, None
        if entry.get('lvl') in ('eror', 'error', 'crit') or entry.get('err'):
            return None, str(entry.get('err') or entry.get('msg') or '')
        return None, None
    return process, _await_address(process, timeout, read, 'ngrok')


def _start_cloudflared(binary, port, popen, timeout, store):
    """``cloudflared tunnel --url``: the address is used once the edge registered a connection
    and its new hostname had time to resolve (``quick_tunnel.SETTLE_SECONDS``)."""
    from . import quick_tunnel
    config = quick_tunnel.config_file(quick_tunnel.tool_dir(store))
    process = popen(quick_tunnel.command(binary, port, config), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, env=quick_tunnel.environment())
    seen = {}

    def read(entry):
        message = str(entry.get('message') or '')
        match = quick_tunnel.ADDRESS.search(message)
        if match:
            seen['url'] = match.group(0)
        if quick_tunnel.CONNECTED in message:
            seen['connected'] = True
        if seen.get('url') and seen.get('connected'):
            return seen['url'], None
        if entry.get('level') in ('error', 'fatal'):
            return None, message
        return None, None
    # At the deadline an address without a recognised connection line (another installed version) stands.
    url = _await_address(process, timeout, read, 'cloudflared', at_deadline=lambda: seen.get('url'))
    time.sleep(quick_tunnel.SETTLE_SECONDS)
    return process, url


def _await_address(process, timeout, read, name, at_deadline=lambda: None):
    """Drain ``process`` output for its life and return the address ``read`` finds before ``timeout``.

    ``at_deadline()`` may still name an address when the deadline passes; otherwise the process is ended.
    """
    lines = queue.Queue()
    found = threading.Event()

    def drain():
        # After the address is found the output is only drained, never kept (re-review P3-5).
        for line in process.stdout:
            if not found.is_set():
                lines.put(line)
        lines.put(None)
    threading.Thread(target=drain, daemon=True).start()
    deadline = time.monotonic() + timeout
    last_error = ''
    while True:
        try:
            line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            url = at_deadline()
            if url:
                found.set()
                return url
            break
        if line is None:
            break
        try:
            entry = json.loads(line)
        except ValueError:
            entry = {}
        if not isinstance(entry, dict):
            continue
        url, error = read(entry)
        if url:
            found.set()
            return url
        if error:
            last_error = error[:200]
    found.set()
    process.terminate()
    raise RuntimeError(f'{name} did not report a public HTTPS address.' + (f' ({last_error})' if last_error else ''))


