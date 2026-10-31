"""Console-first first setup for an interactive `agentos start` (#1475).

The steps call the same service methods as the browser screens, so the browser
later shows exactly what was saved here. Secrets are read with hidden input and
are never printed or passed as arguments. Lines are catalog lines (#1356),
translated on the way to the terminal like the Telegram seam (#1350).

On a real terminal Rich draws the banner, steps and summary and prompt_toolkit
reads lines and the arrow-key choice (#438); elsewhere the same steps print
plain lines. Either way the libraries only draw and read lines: a secret goes
from the hidden prompt straight to the service.
"""
import getpass
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser

from .messages import msg
from .decision_routes import MODE_FOLLOW
from .providers import ProviderError
from .subscription_engines import ENGINES
from .telegram_locale import language, translate

SETUP_KEY = 'console_setup'
#: The owner's answer to the start-at-login offer (#105), kept in the setup state so it is asked once.
LOGIN_SERVICE = 'login_service'

# provider, label, endpoint, default model and key page; endpoints and models follow web/app.js.
API_PROVIDERS = {
    '1': ('openai', '1) OpenAI', 'https://api.openai.com/v1', 'gpt-4o-mini', 'https://platform.openai.com/api-keys'),
    '2': ('anthropic', '2) Anthropic', 'https://api.anthropic.com', '', 'https://console.anthropic.com/settings/keys'),
    '3': ('compatible', msg('first_run.api_compatible'), 'https://openrouter.ai/api/v1', 'openrouter/free',
          'https://openrouter.ai/keys'),
    '4': ('ollama', msg('first_run.api_ollama'), 'http://127.0.0.1:11434', '', ''),
}
STEP_MARKS = {'ready': '✓', 'attention': '!'}
#: #512: the working line's spinner, one frame per ``SPIN_SECONDS``, so a long Work never looks frozen.
SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
SPIN_SECONDS = 0.125
BOTFATHER_URL = 'https://t.me/BotFather'
#: The README's wordmark, drawn above the first setup on a real terminal.
LOGO = r"""   ________________   __
  / __/_  __/  _/ /  / /
 _\ \  / / _/ // /__/ /__
/___/ /_/ /___/____/____/"""
#: The numbering a catalog option line carries for the plain list; the arrow-key menu numbers its own rows.
_OPTION_NUMBER = re.compile(r'^\d+\)\s*')
#: The web screens' language codes (web/app.js LANGUAGES) for a terminal catalog language.
WEB_LANGUAGES = {None: 'ko', 'en': 'en', 'ja': 'ja', 'zh': 'zh-CN'}


def terminal_language(env=None, platform=None, runner=subprocess.run):
    """The catalog language for this terminal: None for Korean, English when unknown."""
    env = os.environ if env is None else env
    for name in ('LC_ALL', 'LC_MESSAGES', 'LANG'):
        code = env.get(name, '').split('.')[0]
        if code and code not in ('C', 'POSIX'):
            return language(code.replace('_', '-'))
    if (platform or sys.platform) == 'darwin':
        try:
            code = runner(['defaults', 'read', '-g', 'AppleLocale'], capture_output=True, text=True,
                          timeout=2, check=False).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            code = ''
        if code:
            return language(code.replace('_', '-'))
    return 'en'


class Terminal:
    """Plain lines and ``input()``: what every setup step talks to."""

    def __init__(self, lang='en'):
        self.lang = lang

    def local(self, text):
        if not self.lang:
            return text
        lines = []
        for line in text.split('\n'):
            core = line.strip()
            lead, tail = line[:len(line) - len(line.lstrip())], line[len(line.rstrip()):]
            if core[:2] in {f'{mark} ' for mark in (*STEP_MARKS.values(), '·')}:
                lead, core = lead + core[:2], core[2:]
            lines.append(lead + translate(core, self.lang) + tail if core else line)
        return '\n'.join(lines)

    def say(self, text=''):
        print(self.local(text), flush=True)

    def ask(self, prompt):
        return input(self.local(prompt)).strip()

    def secret(self, prompt):
        return getpass.getpass(self.local(prompt)).strip()

    def run(self, argv):
        return subprocess.run(argv, check=False).returncode

    def open(self, url):
        webbrowser.open(url)

    def banner(self, version=''):
        self.say(f'Still {version}'.strip())

    def title(self, text):
        self.say('\n' + text)

    def mark(self, mark, text):
        self.say(f'   {mark} {text}')

    def choose(self, prompt, options, default=None, preview=None):
        """The key of one of ``options`` (key, catalog label); a typed number here, '' only without a default.

        ``preview`` is what the choice settles, shown with it (not a catalog line).
        """
        if preview:
            print(f'\n{preview}\n', flush=True)
        for _, label in options:
            self.say('   ' + label)
        return self.ask('   ' + prompt + ' ') or default or ''

    # -- the terminal conversation (#438) --------------------------------------------------
    def conversation(self):
        """A context in which answers may print while the owner types."""
        import contextlib
        return contextlib.nullcontext()

    def message(self, status):
        """One line the owner types; ``status`` names what Still is doing, or ''."""
        return input('> ')

    def interrupt(self):
        """End the line being typed ('' is returned); plain ``input()`` cannot be, so a request waits for Enter."""

    def answer(self, text):
        """Still's answer: the model's own text, printed as it is (not a catalog line)."""
        print(f'\nStill: {text}\n', flush=True)


class RichTerminal(Terminal):
    """The same steps drawn by Rich, with lines and choices read by prompt_toolkit (#438)."""

    STYLES = {'✓': 'green', '!': 'yellow'}

    def __init__(self, lang='en', console=None, reader=None, chooser=None):
        super().__init__(lang)
        if console is None:
            from rich.console import Console
            console = Console(highlight=False)
        if reader is None or chooser is None:
            from prompt_toolkit import prompt
            from prompt_toolkit.shortcuts import choice
            reader, chooser = reader or prompt, chooser or choice
        self.console, self.reader, self.chooser = console, reader, chooser
        self._wake_lock, self._woken = threading.Lock(), False

    def say(self, text=''):
        from rich.padding import Padding
        from rich.text import Text
        for line in self.local(text).split('\n'):
            body = line.lstrip(' ')
            # A wrapped help line stays under its own indent instead of falling back to column 0.
            self.console.print(Padding(Text(body), (0, 0, 0, len(line) - len(body))))

    def ask(self, prompt):
        return self.reader(self.local(prompt)).strip()

    def secret(self, prompt):
        # A fresh prompt per call: the typed value is masked and kept in no history.
        return self.reader(self.local(prompt), is_password=True).strip()

    def banner(self, version=''):
        from rich.text import Text
        logo = Text(LOGO, style='bold cyan')
        if version:
            logo.append(f'  v{version}', style='cyan')
        self.console.print()
        self.console.print(logo)
        self.console.print(Text(self.local(msg('first_run.tagline')), style='dim'))

    def title(self, text):
        from rich.rule import Rule
        from rich.text import Text
        self.console.print()
        self.console.print(Rule(Text(self.local(text), style='bold'), align='left', style='cyan'))

    def mark(self, mark, text):
        from rich.text import Text
        line = Text('   ')
        line.append(mark, style='bold ' + self.STYLES.get(mark, 'dim'))
        line.append(' ' + self.local(text))
        self.console.print(line)

    def conversation(self):
        from prompt_toolkit.patch_stdout import patch_stdout
        # Answers printed by the delivery thread appear above the line being typed, colours kept.
        return patch_stdout(raw=True)

    def message(self, status):
        from prompt_toolkit.application.current import get_app
        from prompt_toolkit.formatted_text import ANSI
        def started():
            with self._wake_lock:
                self._line, woken, self._woken = get_app(), self._woken, False
            if woken:
                # A request arrived between two lines: this one ends at once so it is served now.
                self._line.exit(result='')
        def toolbar():
            line = status()
            # #512: the frame follows the clock, so the line keeps turning while Still works.
            return f'{SPINNER[int(time.monotonic() / SPIN_SECONDS) % len(SPINNER)]} {self.local(line)}' if line else None
        return self.reader(ANSI('\x1b[1;36m› \x1b[0m'), bottom_toolbar=toolbar, refresh_interval=SPIN_SECONDS,
                           pre_run=started)

    def interrupt(self):
        # Called from the worker thread: the running line ends on its own event loop; with no line
        # running (the owner's message is being taken), the next line ends as soon as it starts.
        with self._wake_lock:
            app = getattr(self, '_line', None)
            if app is None or not app.is_running:
                self._woken = True
                return
        app.loop.call_soon_threadsafe(lambda: app.exit(result='') if app.is_running else None)

    def answer(self, text):
        from rich.markdown import Markdown
        from rich.text import Text
        self.console.print()
        self.console.print(Text('Still', style='bold cyan'))
        self.console.print(Markdown(text))
        self.console.print()

    def choose(self, prompt, options, default=None, preview=None):
        keys = [key for key, _ in options]
        return self.chooser(self._rendered(preview) if preview else '',
                            options=[(key, _OPTION_NUMBER.sub('', self.local(label))) for key, label in options],
                            default=default if default in keys else None)

    def _rendered(self, text):
        """``text`` as Markdown in this terminal's width, for a prompt_toolkit message (drawn with the menu)."""
        import io
        from prompt_toolkit.formatted_text import ANSI
        from rich.console import Console
        from rich.markdown import Markdown
        out = io.StringIO()
        # A little narrower than the terminal and without Rich's trailing padding, so the menu does not re-wrap it.
        Console(file=out, force_terminal=True, width=max(40, self.console.width - 4), highlight=False).print(Markdown(text))
        return ANSI('\n'.join(line.rstrip() for line in out.getvalue().rstrip().split('\n')) + '\n')


def open_terminal(lang, stdin=None, stdout=None, environ=None):
    """Rich drawing on a real terminal; plain lines when either side is redirected or TERM is dumb."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    environ = os.environ if environ is None else environ
    if stdin.isatty() and stdout.isatty() and environ.get('TERM') != 'dumb':
        try:
            return RichTerminal(lang)
        except ImportError:
            pass
    return Terminal(lang)


def version():
    try:
        from importlib.metadata import PackageNotFoundError, version as installed
        return installed('personal-agentos')
    except PackageNotFoundError:
        return ''


def finished(store):
    return bool(store.config(SETUP_KEY, {}).get('finished'))


def wants_login_service(store):
    """True only after the owner explicitly answered yes to starting Still at login."""
    return store.config(SETUP_KEY, {}).get(LOGIN_SERVICE) == 'yes'


def _record(store, **values):
    store.put(SETUP_KEY, {**store.config(SETUP_KEY, {}), **values})


def run(service, store, term, login_service=None, conversation=False):
    """Return True when the setup finished; False when the owner stopped early.

    ``login_service`` reads the macOS login service's status when this start
    could offer to register it (#105); None skips the offer. ``conversation``:
    this terminal becomes a conversation once an AI is connected (#438), so
    Telegram is offered there instead, unless Still moves to the login service.
    """
    try:
        # #90: an install set up in the browser or by an earlier version has nothing to ask.
        if not (store.claimed() and ai_ready(service) and store.config('telegram', {}).get('enabled')):
            term.banner(version())
            term.say(msg('first_run.intro'))
        if not store.claimed():
            _access(store, term)
        if not ai_ready(service):
            _ai(service, term)
        _judgment(service)
        in_conversation = conversation and ai_ready(service)
        if not store.config('telegram', {}).get('enabled') and not in_conversation:
            _telegram(service, term)
        if login_service:
            _login_service(store, term, login_service)
        if in_conversation and wants_login_service(store) and not store.config('telegram', {}).get('enabled'):
            # The login service takes Still out of this terminal, so there is no conversation to offer it in.
            _telegram(service, term)
    except (KeyboardInterrupt, EOFError):
        term.say('\n' + msg('first_run.stopped'))
        return False
    _record(store, finished=True)
    _summary(service, term)
    return True


def converse(service, term, clock=time.time):
    """Talk to Still in this terminal until the owner ends it (Ctrl-D or Ctrl-C) (#438)."""
    surface = service.terminal_surface
    term.say('\n' + msg('terminal.ready'))
    if not service.store.config('telegram', {}).get('enabled'):
        term.say(msg('terminal.telegram_hint'))
    def working():
        # #512: measured time since the request was received; never an estimate of what is left.
        since = surface.waiting_since()
        return '' if since is None else msg('terminal.working_for', seconds=max(0, int(clock() - since)))
    with term.conversation():
        surface.attach(term.answer, term.interrupt)
        try:
            while True:
                line = term.message(working)
                while (kind := surface.take_request()) is not None:
                    _requested_input(service, term, kind)
                surface.say(line)
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            surface.detach()
    term.say(msg('terminal.bye'))


def _requested_input(service, term, kind):
    """An input a confirmed settings draft asked for (``TerminalSurface.INPUTS``), read here and never sent as a message."""
    try:
        if kind == 'telegram':
            _telegram_connect(service, term)
        elif isinstance(kind, dict):
            # A notice's button pair: the owner's choice is settled exactly, like a pressed button, and
            # the notice it settles is drawn with the menu itself.
            options = [(str(index), f'{index}) ' + term.local(msg(label))) for index, label in enumerate(kind['labels'], 1)]
            answer = term.choose(msg('first_run.choice_prompt'), options, '1', preview=kind.get('text'))
            if answer in ('1', '2'):
                service.terminal_surface.answer_choice(kind, answer == '1')
    except (KeyboardInterrupt, EOFError):
        term.say('   ' + msg('terminal.input_stopped'))


def _access(store, term):
    term.title(msg('first_run.access_title'))
    term.say('   ' + msg('first_run.access_help'))
    password = ''
    if term.ask('   ' + msg('first_run.access_prompt') + ' ').lower() in ('y', 'yes'):
        password = _new_password(term)
    store.claim(store.bootstrap.read_text(), password or secrets.token_urlsafe(32), local_access=not password)


def _new_password(term):
    while True:
        password = term.secret('   ' + msg('first_run.new_password_prompt') + ' ')
        if not password:
            return ''
        if not 12 <= len(password) <= 256:
            term.say('   ' + msg('first_run.password_too_short'))
        elif term.secret('   ' + msg('first_run.password_again') + ' ') != password:
            term.say('   ' + msg('first_run.password_mismatch'))
        else:
            return password


def ai_ready(service):
    status = service.onboarding()
    return bool(status['subscription_engine'] or status['model_ready'])


def _ai(service, term):
    term.title(msg('first_run.ai_title'))
    term.say('   ' + msg('first_run.ai_help'))
    default = None
    for key, engine_id in (('1', 'codex'), ('2', 'claude-code')):
        if service.subscription_engines.finder(ENGINES[engine_id].command) and _signed_in(service, engine_id):
            default = key
            term.say('   ' + msg('first_run.ai_detected', name=ENGINES[engine_id].name))
            break
    options = [(str(index), msg(f'first_run.{line}'))
               for index, line in enumerate(('ai_codex', 'ai_claude', 'ai_api', 'ai_later'), 1)]
    while True:
        choice = term.choose(msg('first_run.choice_prompt'), options, default)
        if choice == '4':
            return
        if choice == '1':
            done = _codex(service, term)
        elif choice == '2':
            done = _claude(service, term)
        elif choice == '3':
            done = _api(service, term)
        else:
            term.say('   ' + msg('first_run.choose_one_to_four'))
            continue
        if done:
            return


def _signed_in(service, engine_id):
    try:
        return service.check_engine_login(engine_id).get('state') == 'signed-in'
    except ValueError:
        return False


def _installed(service, term, engine_id):
    """The CLI's path, after opening its official install page when it is missing."""
    engine = ENGINES[engine_id]
    binary = service.subscription_engines.finder(engine.command)
    if binary:
        return binary
    term.say('   ' + msg('first_run.cli_missing', name=engine.name))
    term.say('   ' + engine.install_url)
    term.say('   ' + msg('first_run.cli_install_command'))
    term.say('     ' + engine.install_command)
    term.open(engine.install_url)
    while True:
        if term.ask('   ' + msg('first_run.cli_install_wait') + ' ').lower() == 'b':
            return None
        binary = service.subscription_engines.finder(engine.command)
        if binary:
            return binary
        term.say('   ' + msg('first_run.cli_still_missing', name=engine.name))


def _codex(service, term):
    binary = _installed(service, term, 'codex')
    if not binary:
        return False
    if _signed_in(service, 'codex'):
        term.say('   ' + msg('first_run.codex_signed_in'))
    elif term.ask('   ' + msg('first_run.codex_login_prompt') + ' ').lower() in ('', 'y', 'yes'):
        term.run([binary, 'login'])
    return _connect_engine(service, term, 'codex', 'Codex')


def _claude(service, term):
    binary = _installed(service, term, 'claude-code')
    if not binary:
        return False
    if _signed_in(service, 'claude-code'):
        term.say('   ' + msg('first_run.claude_signed_in'))
        return _connect_engine(service, term, 'claude-code', 'Claude Code')
    term.say('   ' + msg('first_run.claude_setup_token'))
    term.run([binary, 'setup-token'])
    token = term.secret('   ' + msg('first_run.claude_token_prompt') + ' ')
    if not token:
        term.say('   ' + msg('first_run.token_empty'))
        return False
    try:
        service.save_engine_credential({'engine': 'claude-code', 'token': token})
    except ValueError as exc:
        term.say(f'   {exc}')
        return False
    return _connect_engine(service, term, 'claude-code', 'Claude Code')


def _connect_engine(service, term, engine_id, name):
    try:
        service.connect_subscription_engine({'engine': engine_id, 'officially_authenticated': True},
                                            require_signed_in=True)
    except ValueError as exc:
        term.say(f'   {exc}')
        return False
    term.say('   ' + msg('first_run.engine_connected', name=name))
    return True


def _api(service, term):
    term.say('   ' + msg('first_run.api_help'))
    choice = term.choose(msg('first_run.choice_prompt'),
                         [(key, label) for key, (_, label, _, _, _) in API_PROVIDERS.items()])
    if choice not in API_PROVIDERS:
        term.say('   ' + msg('first_run.choose_one_to_four'))
        return False
    provider, _, default_endpoint, default_model, key_page = API_PROVIDERS[choice]
    endpoint = default_endpoint
    if provider in ('compatible', 'ollama'):
        endpoint = term.ask('   ' + msg('first_run.server_prompt', endpoint=default_endpoint) + ' ') or default_endpoint
    if default_model:
        model = term.ask('   ' + msg('first_run.model_prompt_default', model=default_model) + ' ') or default_model
    else:
        model = term.ask('   ' + msg('first_run.model_prompt') + ' ')
    key = ''
    if provider != 'ollama':
        if key_page and endpoint == default_endpoint and \
                term.ask('   ' + msg('first_run.open_key_page') + ' ').lower() in ('', 'y', 'yes'):
            term.say('   ' + key_page)
            term.open(key_page)
        key = term.secret('   ' + msg('first_run.api_key_prompt') + ' ')
    try:
        service.save_model({'provider': provider, 'endpoint': endpoint, 'model': model, 'api_key': key})
        result = service.test_model()
        if not result['ok']:
            error = (result['error'] or msg('first_run.no_model_reply')).rstrip('.')
            term.say('   ' + msg('first_run.api_test_failed', error=error))
            return False
        service.select_ai_route({'route': 'direct-api'})
    except (ValueError, ProviderError) as exc:
        term.say(f'   {exc}')
        return False
    term.say('   ' + msg('first_run.api_connected'))
    return True


def _judgment(service):
    """Reuse the Main AI switch's qualification, including resumed console setup.

    Only initialize the default follow mode. Existing owner choices, passed
    routes and pending/failed checks remain intact; no model runs here.
    """
    routes = service.decision_routes
    if routes.mode() != MODE_FOLLOW:
        return
    main = service.main_ai.current()
    if not main:
        return
    status = routes.status()
    if status['effective']['state'] == 'active' or status.get('qualification'):
        return
    routes.follow_main_switched(main)


def _telegram(service, term):
    term.title(msg('first_run.telegram_title'))
    term.say('   ' + msg('first_run.telegram_help'))
    if term.ask('   ' + msg('first_run.telegram_prompt') + ' ').lower() not in ('y', 'yes'):
        return
    _telegram_connect(service, term)


def _telegram_connect(service, term):
    """BotFather's steps, the bot token from a hidden prompt, then the pairing link; the token goes only to the service."""
    term.say('   ' + msg('first_run.telegram_steps_intro'))
    term.say('   ' + BOTFATHER_URL)
    term.say('   ' + msg('first_run.telegram_no_app'))
    term.open(BOTFATHER_URL)
    for line in ('telegram_step_newbot', 'telegram_step_names', 'telegram_step_token'):
        term.say('     ' + msg(f'first_run.{line}'))
    token = term.secret('   ' + msg('first_run.bot_token_prompt') + ' ')
    if not token:
        return
    try:
        result = service.connect_telegram({'token': token})
    except (ValueError, ProviderError) as exc:
        term.say(f'   {exc}')
        return
    term.say('   ' + msg('first_run.telegram_pairing') + f'\n   {result["url"]}')
    term.open(result['url'])


def _login_service(store, term, status):
    """Ask once whether Still starts at login; only an explicit yes registers it (#105)."""
    if LOGIN_SERVICE in store.config(SETUP_KEY, {}) or status().get('status') != 'not_installed':
        return
    term.title(msg('first_run.login_title'))
    term.say('   ' + msg('first_run.login_help'))
    answer = term.ask('   ' + msg('first_run.login_prompt') + ' ').lower() in ('y', 'yes')
    _record(store, **{LOGIN_SERVICE: 'yes' if answer else 'no'})


def _summary(service, term):
    ready = ai_ready(service)
    term.title(msg('first_run.finished' if ready else 'first_run.finished_without_ai'))
    for step in service.onboarding()['steps']:
        term.mark(STEP_MARKS.get(step['state'], '·'), step['message'])
    if not ready:
        # Judgment follows the AI the owner connects later; without one there is nothing to warn about.
        return
    state = service.decision_routes.status()['effective']['state']
    term.mark({'active': '✓', 'checking': '·'}.get(state, '!'), msg(f'first_run.judgment_{state}'))
