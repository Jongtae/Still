"""Sandbox-only import guard: no PyObjC, so no WebKit window can reach the owner's screen.

The eval launcher puts this folder first on ``PYTHONPATH`` of every sandbox
Still process (and the browser worker inherits it).  Still already treats
missing PyObjC as "embedded browser unavailable"
(``browser_session.webkit_unavailable_reason``), so the sandbox runs its
normal code path without a window.  Not used by the owner's live server.
"""
import sys

BLOCKED = frozenset({'objc', 'AppKit', 'WebKit', 'Foundation', 'Cocoa', 'Quartz', 'PyObjCTools'})


class _NoPyObjC:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name.split('.', 1)[0] in BLOCKED:
            raise ImportError(f'{name} is disabled in Still eval sandboxes')
        return None


sys.meta_path.insert(0, _NoPyObjC())


# Eval-only: with AGENTOS_EVAL_TELEGRAM_ROOT set, the sandbox's Bot API calls go to the
# eval's loopback stand-in (``agentos_eval.fake_telegram``) instead of api.telegram.org.
# Only a loopback root is accepted; the sandbox's bot token is made up as well.
import os as _os

_TELEGRAM_ROOT = _os.environ.get('AGENTOS_EVAL_TELEGRAM_ROOT', '')
_TELEGRAM_MODULE = 'personal_agent.conversation_handoff'


def _loopback(root):
    from urllib.parse import urlsplit
    parts = urlsplit(root)
    return parts.scheme == 'http' and parts.hostname == '127.0.0.1' and not parts.path.strip('/')


class _TelegramRoot:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name != _TELEGRAM_MODULE:
            return None
        import importlib.machinery
        for finder in sys.meta_path:
            if finder is _TelegramRoot or isinstance(finder, (_NoPyObjC, _TelegramRoot)):
                continue
            spec = getattr(finder, 'find_spec', lambda *a: None)(name, path, target)
            if spec is not None and spec.loader is not None:
                original = spec.loader.exec_module

                def exec_module(module, original=original):
                    original(module)
                    module.TELEGRAM_API_ROOT = _TELEGRAM_ROOT
                    module.TELEGRAM_FILE_ROOT = _TELEGRAM_ROOT + '/file'
                spec.loader.exec_module = exec_module
                return spec
        return None


if _TELEGRAM_ROOT:
    if not _loopback(_TELEGRAM_ROOT):
        # ``site`` only warns about an exception raised here, so the sandbox stops itself instead.
        sys.stderr.write('AGENTOS_EVAL_TELEGRAM_ROOT must be an http://127.0.0.1:<port> root\n')
        _os._exit(2)
    sys.meta_path.insert(0, _TelegramRoot())
