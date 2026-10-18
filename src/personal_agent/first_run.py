"""Console-first first setup for an interactive `agentos start` (#1475).

The steps call the same service methods as the browser screens, so the browser
later shows exactly what was saved here. Secrets are read with hidden input and
are never printed or passed as arguments. Lines are catalog lines (#1356),
translated on the way to the terminal like the Telegram seam (#1350).
"""
import getpass
import os
import secrets
import subprocess
import sys
import webbrowser

from .messages import msg
from .decision_routes import MODE_FOLLOW
from .providers import ProviderError
from .subscription_engines import ENGINES
from .telegram_locale import language, translate

SETUP_KEY = 'console_setup'

# provider, label, endpoint, default model and key page; endpoints and models follow web/app.js.
API_PROVIDERS = {
    '1': ('openai', '1) OpenAI', 'https://api.openai.com/v1', 'gpt-4o-mini', 'https://platform.openai.com/api-keys'),
    '2': ('anthropic', '2) Anthropic', 'https://api.anthropic.com', '', 'https://console.anthropic.com/settings/keys'),
    '3': ('compatible', msg('first_run.api_compatible'), 'https://openrouter.ai/api/v1', 'openrouter/free',
          'https://openrouter.ai/keys'),
    '4': ('ollama', msg('first_run.api_ollama'), 'http://127.0.0.1:11434', '', ''),
}
STEP_MARKS = {'ready': '✓', 'attention': '!'}
BOTFATHER_URL = 'https://t.me/BotFather'


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


def finished(store):
    return bool(store.config(SETUP_KEY, {}).get('finished'))


def run(service, store, term):
    """Return True when the setup finished; False when the owner stopped early."""
    try:
        # #90: an install set up in the browser or by an earlier version has nothing to ask.
        if not (store.claimed() and _ai_ready(service) and store.config('telegram', {}).get('enabled')):
            term.say(msg('first_run.intro'))
        if not store.claimed():
            _access(store, term)
        if not _ai_ready(service):
            _ai(service, term)
        _judgment(service)
        if not store.config('telegram', {}).get('enabled'):
            _telegram(service, term)
    except (KeyboardInterrupt, EOFError):
        term.say('\n' + msg('first_run.stopped'))
        return False
    store.put(SETUP_KEY, {'finished': True})
    _summary(service, term)
    return True


def _access(store, term):
    term.say('\n' + msg('first_run.access_title'))
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


def _ai_ready(service):
    status = service.onboarding()
    return bool(status['subscription_engine'] or status['model_ready'])


def _ai(service, term):
    term.say('\n' + msg('first_run.ai_title'))
    for line in ('ai_help', 'ai_codex', 'ai_claude', 'ai_api', 'ai_later'):
        term.say('   ' + msg(f'first_run.{line}'))
    while True:
        choice = term.ask('   ' + msg('first_run.choice_prompt') + ' ')
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
    for _, label, _, _, _ in API_PROVIDERS.values():
        term.say(f'   {label}')
    choice = term.ask('   ' + msg('first_run.choice_prompt') + ' ')
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
    term.say('\n' + msg('first_run.telegram_title'))
    term.say('   ' + msg('first_run.telegram_help'))
    if term.ask('   ' + msg('first_run.telegram_prompt') + ' ').lower() not in ('y', 'yes'):
        return
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


def _summary(service, term):
    term.say('\n' + msg('first_run.finished'))
    for step in service.onboarding()['steps']:
        term.say(f'   {STEP_MARKS.get(step["state"], "·")} {step["message"]}')
    state = service.decision_routes.status()['effective']['state']
    mark = {'active': '✓', 'checking': '·'}.get(state, '!')
    term.say(f'   {mark} ' + msg(f'first_run.judgment_{state}'))
