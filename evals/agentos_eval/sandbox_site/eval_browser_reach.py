"""Eval-only: let a sandbox's embedded browser load exactly a browser world's loopback origins (#1122).

Still refuses this computer and its private network as a browser destination
(``browser_session.local_destination``, the worker's ``destination_refusal``).
That rule is not changed.  Still already has a **test-only exact-origin
allowance**: ``BrowserProfile.allow_origins_for_tests`` (passed to the worker as
``--test-allow-origin``) and ``BrowserSession(allowed_origins_for_tests=...)``,
which Still's own config and environment never set.  The fixture tests of the
real worker use it (``tests/test_browser_webkit.py``).

A sandbox run against a browser world gives those two the world's origins,
and only them, from the sandbox import guard (``sitecustomize.py``), which the
owner's live server never loads.  Every other loopback, private or link-local
destination stays refused, and an origin that is not ``127.0.0.1:<port>``
stops the sandbox.  The profile's cookie jar uses an in-memory key, so a
sandbox never writes a key into the eval user's login Keychain; the profile
folder is the fresh slot's own (the seed snapshot drops the owner's profile).
The browser worker the sandbox starts gets the same origins in its
environment, so its import guard lets it load WebKit (PyObjC stays blocked in
every sandbox that runs no browser world).

Importing this module changes nothing; ``apply`` does.
"""
import re

ENV = 'AGENTOS_EVAL_BROWSER_ORIGINS'
MODULE = 'personal_agent.browser_session'
_ORIGIN = re.compile(r'^127\.0\.0\.1:([1-9][0-9]{0,4})$')


def parse(value):
    """The comma-separated origins, validated; raises ``ValueError`` for anything but ``127.0.0.1:<port>``."""
    origins = tuple(item.strip() for item in str(value or '').split(',') if item.strip())
    if not origins or len(origins) > 4:
        raise ValueError(f'{ENV} must list 1-4 origins')
    for origin in origins:
        found = _ORIGIN.match(origin)
        if not found or int(found.group(1)) > 65535:
            raise ValueError(f'{ENV} accepts only 127.0.0.1:<port> origins')
    return origins


def apply(module, origins, setattr=setattr):
    """Give ``module``'s ``BrowserProfile`` and ``BrowserSession`` the world's exact origins.

    ``setattr`` lets a test undo it (``monkeypatch.setattr``).  An explicit
    allowance a caller passes is kept as it is.
    """
    origins = parse(','.join(origins))
    from personal_agent.browser_jar import JAR_NAME, CookieJar, MemoryKey
    from pathlib import Path

    profile_init = module.BrowserProfile.__init__
    session_init = module.BrowserSession.__init__
    worker_environment = module.worker_environment

    def init_profile(self, profile_dir, *args, jar=None, **kwargs):
        if jar is None:
            jar = CookieJar(Path(profile_dir) / JAR_NAME, MemoryKey())
        profile_init(self, profile_dir, *args, jar=jar, **kwargs)
        self.allow_origins_for_tests(*origins)

    def init_session(self, *args, allowed_origins_for_tests=(), **kwargs):
        session_init(self, *args, allowed_origins_for_tests=tuple(allowed_origins_for_tests) or origins, **kwargs)

    def worker_env(base=None):
        # The worker loads the same import guard; it needs the origins to know it serves a browser world.
        env = worker_environment(base)
        env[ENV] = ','.join(origins)
        return env

    setattr(module.BrowserProfile, '__init__', init_profile)
    setattr(module, 'worker_environment', worker_env)
    setattr(module.BrowserSession, '__init__', init_session)
    return origins
