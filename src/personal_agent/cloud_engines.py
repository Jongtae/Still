"""Cloud provider profiles for the subscription CLI engines (#211).

At work the owner's AI comes through the company cloud, not a personal
subscription.  Claude Code and Codex already support those clouds
themselves, so Still writes no Bedrock or Azure client (C15/C16).  It only
removes the blocker: a Work runs each CLI with an empty per-turn HOME and a
minimal environment, so the CLIs' own cloud configuration never reached
them.  This module holds the owner's choice and hands each CLI **only the
variable names its vendor documents** for that cloud.

Still-owned boundary, nothing more:

* **Profile.** One provider per engine, set in Settings on this computer only;
  the conversational ``settings_change`` path cannot reach it.
* **Credential custody.** Secret values live in Still's secret file
  (``SECRET_PREFIX``), reach only the CLI process environment, and are
  withheld from the model's shell (``bounded_execution``).  Never argv, logs,
  prompts or Evidence.
* **Budget.** A cloud profile carries an owner-set daily call cap.  Every CLI
  model launch (Work turn or Judgment AI call) is admitted against it first;
  past the cap nothing is launched and nothing falls back to another route.

Documented variable names (pinned 2026-10-23, Claude Code 2.1.287 and
codex-cli 0.153.4 installed locally):

* Claude Code on Amazon Bedrock, https://code.claude.com/docs/en/amazon-bedrock
  and https://code.claude.com/docs/en/env-vars : ``CLAUDE_CODE_USE_BEDROCK``,
  ``AWS_REGION``, ``AWS_PROFILE``, ``AWS_CONFIG_FILE``,
  ``AWS_SHARED_CREDENTIALS_FILE``, ``ANTHROPIC_BEDROCK_BASE_URL``,
  ``ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL``; credentials
  ``AWS_BEARER_TOKEN_BEDROCK``, ``AWS_ACCESS_KEY_ID``,
  ``AWS_SECRET_ACCESS_KEY``, ``AWS_SESSION_TOKEN``.
* Claude Code on Microsoft Foundry,
  https://code.claude.com/docs/en/microsoft-foundry : ``CLAUDE_CODE_USE_FOUNDRY``,
  ``ANTHROPIC_FOUNDRY_RESOURCE``, ``ANTHROPIC_FOUNDRY_BASE_URL``,
  ``ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL``; credentials
  ``ANTHROPIC_FOUNDRY_API_KEY``, ``ANTHROPIC_FOUNDRY_AUTH_TOKEN``.
* Codex with Azure OpenAI,
  https://learn.chatgpt.com/docs/config-file/config-advanced ("Azure provider
  and per-provider tuning"): ``model_providers.<id>`` with ``name``,
  ``base_url``, ``env_key = "AZURE_OPENAI_API_KEY"``, ``query_params``
  (``api-version``) and ``wire_api = "responses"``.  A Work runs Codex with
  ``--ignore-user-config``, so the owner's ``config.toml`` is not read; the
  same documented keys are passed as ``-c`` overrides instead, and only the key
  travels in the environment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import secrets as _secrets
import time
from urllib.parse import urlsplit

from .messages import msg

#: Owner config: ``{engine_id: {provider, settings, daily_calls, revision}}``.
PROFILE_KEY = 'engine_cloud_profiles'
#: ``{"<engine>:<provider>": {"day": "YYYY-MM-DD", "calls": n}}``.
USAGE_KEY = 'engine_cloud_usage'
#: Secret slots: ``cloud_engine:<engine>:<provider>:<VARIABLE>``.
SECRET_PREFIX = 'cloud_engine:'
SUBSCRIPTION = 'subscription'
#: Failure class of a launch the daily cap refused (no model call, no fallback).
BUDGET_EXCEEDED = 'cloud-budget'
#: Failure class of a cloud profile missing a required value.
NOT_CONFIGURED = 'cloud-not-configured'
MAX_DAILY_CALLS = 10_000
MAX_SECRET_CHARS = 8192

_REGION = re.compile(r'^[a-z]{2}(?:-[a-z]+)+-\d{1,2}$')
_AWS_PROFILE = re.compile(r'^[A-Za-z0-9_.+=,@-]{1,64}$')
_RESOURCE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]{0,62}$')
#: A Bedrock inference profile ID or ARN, a Foundry deployment name; Claude Code's ``[1m]`` suffix allowed.
_MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}(?:\[1m\])?$')
_API_VERSION = re.compile(r'^[A-Za-z0-9.-]{1,40}$')
_SECRET_VALUE = re.compile(r'^\S{8,%d}$' % MAX_SECRET_CHARS)


def _https_url(value):
    if not isinstance(value, str) or len(value) > 300 or any(ch.isspace() for ch in value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme == 'https' and bool(parts.hostname) and not parts.username and not parts.password \
        and not parts.query and not parts.fragment


def _absolute_path(value):
    return isinstance(value, str) and value.startswith('/') and 1 < len(value) <= 400 \
        and not any(ch in value for ch in '\n\r\x00')


#: One validator per documented non-secret name.
VALIDATORS = {
    'AWS_REGION': lambda v: isinstance(v, str) and bool(_REGION.match(v)),
    'AWS_PROFILE': lambda v: isinstance(v, str) and bool(_AWS_PROFILE.match(v)),
    'AWS_CONFIG_FILE': _absolute_path,
    'AWS_SHARED_CREDENTIALS_FILE': _absolute_path,
    'ANTHROPIC_BEDROCK_BASE_URL': _https_url,
    'ANTHROPIC_FOUNDRY_RESOURCE': lambda v: isinstance(v, str) and bool(_RESOURCE.match(v)),
    'ANTHROPIC_FOUNDRY_BASE_URL': _https_url,
    'ANTHROPIC_DEFAULT_OPUS_MODEL': lambda v: isinstance(v, str) and bool(_MODEL.match(v)),
    'ANTHROPIC_DEFAULT_SONNET_MODEL': lambda v: isinstance(v, str) and bool(_MODEL.match(v)),
    'ANTHROPIC_DEFAULT_HAIKU_MODEL': lambda v: isinstance(v, str) and bool(_MODEL.match(v)),
    'base_url': _https_url,
    'api_version': lambda v: isinstance(v, str) and bool(_API_VERSION.match(v)),
}
_MODEL_PINS = ('ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL')


@dataclass(frozen=True)
class Provider:
    id: str
    engine: str
    name: str
    #: Environment Still itself sets for this provider (documented switch names).
    fixed: dict
    #: Documented non-secret names the owner may set.
    settings: tuple
    #: Documented credential names the owner may store.
    secrets: tuple
    docs: tuple
    #: Codex: the documented ``model_providers`` keys come as ``-c`` overrides, not environment.
    config_keys: tuple = field(default=())


PROVIDERS = {
    'bedrock': Provider(
        'bedrock', 'claude-code', 'Amazon Bedrock', {'CLAUDE_CODE_USE_BEDROCK': '1'},
        ('AWS_REGION', 'AWS_PROFILE', 'AWS_CONFIG_FILE', 'AWS_SHARED_CREDENTIALS_FILE', 'ANTHROPIC_BEDROCK_BASE_URL',
         *_MODEL_PINS),
        ('AWS_BEARER_TOKEN_BEDROCK', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN'),
        ('https://code.claude.com/docs/en/amazon-bedrock', 'https://code.claude.com/docs/en/env-vars')),
    'foundry': Provider(
        'foundry', 'claude-code', 'Microsoft Foundry', {'CLAUDE_CODE_USE_FOUNDRY': '1'},
        ('ANTHROPIC_FOUNDRY_RESOURCE', 'ANTHROPIC_FOUNDRY_BASE_URL', *_MODEL_PINS),
        ('ANTHROPIC_FOUNDRY_API_KEY', 'ANTHROPIC_FOUNDRY_AUTH_TOKEN'),
        ('https://code.claude.com/docs/en/microsoft-foundry', 'https://code.claude.com/docs/en/env-vars')),
    'azure-openai': Provider(
        'azure-openai', 'codex', 'Azure OpenAI', {}, (), ('AZURE_OPENAI_API_KEY',),
        ('https://learn.chatgpt.com/docs/config-file/config-advanced',),
        config_keys=('base_url', 'api_version')),
}
ENGINES = ('claude-code', 'codex')
#: Codex's ``model_providers`` id for the Azure profile (the vendor example's own id).
CODEX_AZURE_ID = 'azure'


def providers_for(engine_id):
    return [provider for provider in PROVIDERS.values() if provider.engine == engine_id]


def secret_slot(engine_id, provider_id, name):
    return f'{SECRET_PREFIX}{engine_id}:{provider_id}:{name}'


class CloudLaunchError(ValueError):
    """A cloud launch that must not start; ``failure_class`` says why."""

    def __init__(self, text, failure_class):
        super().__init__(text)
        self.failure_class = failure_class


@dataclass(frozen=True)
class CloudLaunch:
    """One engine's resolved cloud profile for one launch (values never logged)."""
    engine: str
    provider: str
    settings: dict
    secret_values: dict = field(repr=False)
    daily_calls: int
    revision: str

    @property
    def definition(self):
        return PROVIDERS[self.provider]

    def environment(self):
        """The CLI process environment this profile adds: documented names only."""
        provider = self.definition
        env = dict(provider.fixed)
        for name in provider.settings:
            if self.settings.get(name):
                env[name] = self.settings[name]
        for name in provider.secrets:
            if self.secret_values.get(name):
                env[name] = self.secret_values[name]
        return env

    def withheld(self):
        """Names whose values the model's shell must never inherit."""
        return sorted(name for name in self.definition.secrets if self.secret_values.get(name))

    def codex_arguments(self):
        """Codex ``-c`` overrides selecting the documented Azure ``model_providers`` entry."""
        if self.provider != 'azure-openai':
            return []
        prefix = f'model_providers.{CODEX_AZURE_ID}'
        args = ['-c', f'model_provider={json.dumps(CODEX_AZURE_ID)}',
                '-c', f'{prefix}.name={json.dumps("Azure OpenAI")}',
                '-c', f'{prefix}.base_url={json.dumps(self.settings["base_url"])}',
                '-c', f'{prefix}.env_key={json.dumps("AZURE_OPENAI_API_KEY")}',
                '-c', f'{prefix}.wire_api={json.dumps("responses")}']
        if self.settings.get('api_version'):
            args += ['-c', f'{prefix}.query_params={{{json.dumps("api-version")}={json.dumps(self.settings["api_version"])}}}']
        return args

    def destination(self):
        """Where requests go, for Settings and Evidence (no secret)."""
        settings, name = self.settings, self.definition.name
        if self.provider == 'bedrock':
            host = urlsplit(settings['ANTHROPIC_BEDROCK_BASE_URL']).hostname if settings.get('ANTHROPIC_BEDROCK_BASE_URL') else ''
            return f'{name} ({settings.get("AWS_REGION", "")}{", " + host if host else ""})'
        if self.provider == 'foundry':
            where = settings.get('ANTHROPIC_FOUNDRY_RESOURCE') or urlsplit(settings.get('ANTHROPIC_FOUNDRY_BASE_URL', '')).hostname or ''
            return f'{name} ({where})'
        return f'{name} ({urlsplit(settings.get("base_url", "")).hostname or ""})'

    def identity(self):
        """The profile snapshot this launch was resolved from (``<provider>:<revision>``)."""
        return f'{self.provider}:{self.revision}'

    def record(self):
        """What a Work's turn record keeps about this launch: names and destination, never values."""
        return {'cloud_provider': self.provider, 'cloud_destination': self.destination(),
                'cloud_profile_revision': self.revision, 'cloud_credentials': self.withheld()}


def missing(provider_id, settings, saved_secrets):
    """Required values a profile still lacks (names), so a launch refuses instead of guessing."""
    lacks = []
    has = lambda name: bool(settings.get(name)) or name in saved_secrets  # noqa: E731
    if provider_id == 'bedrock':
        if not settings.get('AWS_REGION'):
            lacks.append('AWS_REGION')
        # A Work runs the CLI with an empty per-turn HOME, so a named profile needs an
        # explicit file to be found in; without one it would launch only to fail.
        profile_source = settings.get('AWS_PROFILE') and (settings.get('AWS_CONFIG_FILE')
                                                         or settings.get('AWS_SHARED_CREDENTIALS_FILE'))
        if not (has('AWS_BEARER_TOKEN_BEDROCK') or (has('AWS_ACCESS_KEY_ID') and has('AWS_SECRET_ACCESS_KEY'))
                or profile_source):
            lacks.append('AWS_BEARER_TOKEN_BEDROCK | AWS_ACCESS_KEY_ID+AWS_SECRET_ACCESS_KEY'
                         ' | AWS_PROFILE+(AWS_CONFIG_FILE | AWS_SHARED_CREDENTIALS_FILE)')
    elif provider_id == 'foundry':
        if not (settings.get('ANTHROPIC_FOUNDRY_RESOURCE') or settings.get('ANTHROPIC_FOUNDRY_BASE_URL')):
            lacks.append('ANTHROPIC_FOUNDRY_RESOURCE | ANTHROPIC_FOUNDRY_BASE_URL')
        if not (has('ANTHROPIC_FOUNDRY_API_KEY') or has('ANTHROPIC_FOUNDRY_AUTH_TOKEN')):
            lacks.append('ANTHROPIC_FOUNDRY_API_KEY | ANTHROPIC_FOUNDRY_AUTH_TOKEN')
    elif provider_id == 'azure-openai':
        if not settings.get('base_url'):
            lacks.append('base_url')
        if not has('AZURE_OPENAI_API_KEY'):
            lacks.append('AZURE_OPENAI_API_KEY')
    return lacks


class CloudEngines:
    """The owner's cloud profiles over the Still store (config rows plus the secret file)."""

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    # -- read ----------------------------------------------------------------
    def _rows(self):
        rows = self.store.config(PROFILE_KEY, {}) or {}
        return rows if isinstance(rows, dict) else {}

    def _row(self, engine_id):
        row = self._rows().get(engine_id)
        return row if isinstance(row, dict) else {}

    def provider(self, engine_id):
        """The provider id selected for an engine, or ``SUBSCRIPTION``."""
        provider = self._row(engine_id).get('provider')
        return provider if provider in PROVIDERS and PROVIDERS[provider].engine == engine_id else SUBSCRIPTION

    def _saved(self, engine_id, provider_id):
        names = PROVIDERS[provider_id].secrets
        return {name: value for name in names
                for value in [self.store.secret(secret_slot(engine_id, provider_id, name))]
                if isinstance(value, str) and value}

    def launch(self, engine_id):
        """The resolved profile for one launch, or None on the subscription route.

        Raises ``CloudLaunchError`` when the selected profile lacks a required
        value: a half-configured cloud profile never runs, and never silently
        runs on the subscription instead.
        """
        # One consistent snapshot: the revision is read before and after the
        # values, and a save in between (which bumps the revision before it
        # writes a credential) makes this resolve again, so a launch never pairs
        # one revision with another revision's credential (#211 review).
        for _attempt in range(3):
            before = self._row(engine_id).get('revision')
            launch = self._resolve(engine_id)
            if self._row(engine_id).get('revision') == before:
                return launch
        raise CloudLaunchError(msg('cloud_engines.not_configured', provider=self.provider(engine_id), names='revision'),
                               NOT_CONFIGURED)

    def _resolve(self, engine_id):
        provider_id = self.provider(engine_id)
        if provider_id == SUBSCRIPTION:
            return None
        row = self._row(engine_id)
        provider = PROVIDERS[provider_id]
        stored = row.get('settings') if isinstance(row.get('settings'), dict) else {}
        settings = {name: stored[name] for name in (*provider.settings, *provider.config_keys)
                    if isinstance(stored.get(name), str) and stored[name] and VALIDATORS[name](stored[name])}
        values = self._saved(engine_id, provider_id)
        cap = row.get('daily_calls')
        lacks = missing(provider_id, settings, values)
        if not isinstance(cap, int) or isinstance(cap, bool) or not 1 <= cap <= MAX_DAILY_CALLS:
            lacks.append('daily_calls')
        if lacks:
            raise CloudLaunchError(msg('cloud_engines.not_configured', provider=provider.name, names=', '.join(lacks)),
                                   NOT_CONFIGURED)
        return CloudLaunch(engine_id, provider_id, settings, values, cap, str(row.get('revision') or ''))

    def _day(self):
        return time.strftime('%Y-%m-%d', time.localtime(self.clock()))

    def used_today(self, engine_id, provider_id):
        usage = self.store.config(USAGE_KEY, {}) or {}
        entry = usage.get(f'{engine_id}:{provider_id}') if isinstance(usage, dict) else None
        if not isinstance(entry, dict) or entry.get('day') != self._day():
            return 0
        calls = entry.get('calls')
        return calls if isinstance(calls, int) and not isinstance(calls, bool) else 0

    def admit(self, launch):
        """Count one model launch against the profile's daily cap, or refuse it before anything starts."""
        key, day = f'{launch.engine}:{launch.provider}', self._day()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM config WHERE key=?', (USAGE_KEY,)).fetchone()
            try:
                usage = json.loads(row['value']) if row else {}
            except (TypeError, ValueError):
                usage = {}
            usage = usage if isinstance(usage, dict) else {}
            entry = usage.get(key) if isinstance(usage.get(key), dict) else {}
            calls = entry.get('calls') if entry.get('day') == day and isinstance(entry.get('calls'), int) else 0
            if calls >= launch.daily_calls:
                raise CloudLaunchError(msg('cloud_engines.budget_exceeded', provider=launch.definition.name,
                                           cap=launch.daily_calls), BUDGET_EXCEEDED)
            usage[key] = {'day': day, 'calls': calls + 1}
            db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (USAGE_KEY, json.dumps(usage)))
        return calls + 1

    def status(self, engine_id):
        """What Settings shows for one engine: names, saved-or-not and counts; never a secret value."""
        selected = self.provider(engine_id)
        row = self._row(engine_id)
        stored = row.get('settings') if isinstance(row.get('settings'), dict) else {}
        options = []
        for provider in providers_for(engine_id):
            saved = self._saved(engine_id, provider.id)
            current = provider.id == selected
            settings = {name: stored[name] for name in (*provider.settings, *provider.config_keys)
                        if current and isinstance(stored.get(name), str)}
            options.append({'id': provider.id, 'name': provider.name, 'settings': list(provider.settings or provider.config_keys),
                            'secrets': list(provider.secrets), 'saved_secrets': sorted(saved), 'values': settings,
                            'docs': list(provider.docs),
                            'missing': missing(provider.id, settings, saved) if current else []})
        status = {'provider': selected, 'providers': options, 'daily_calls': row.get('daily_calls') if selected != SUBSCRIPTION else None,
                  'used_today': self.used_today(engine_id, selected) if selected != SUBSCRIPTION else 0, 'destination': ''}
        if selected != SUBSCRIPTION:
            try:
                status['destination'] = self.launch(engine_id).destination()
            except CloudLaunchError as exc:
                status['problem'] = str(exc)
        return status

    def secret_values(self):
        """Every stored cloud credential value, for host-side redaction only (never a model tool)."""
        items = self.store.secret_items(SECRET_PREFIX) if callable(getattr(self.store, 'secret_items', None)) else {}
        return [value for value in items.values() if isinstance(value, str) and len(value) >= 8]

    # -- owner actions (Settings on this computer only) --------------------------
    def save_profile(self, engine_id, provider_id, settings=None, daily_calls=None):
        """Select a provider for an engine (``subscription`` clears it), with its documented settings and cap."""
        if engine_id not in ENGINES:
            raise ValueError(msg('cloud_engines.unsupported_engine'))
        if provider_id == SUBSCRIPTION:
            row = {'provider': SUBSCRIPTION}
        else:
            provider = PROVIDERS.get(provider_id)
            if provider is None or provider.engine != engine_id:
                raise ValueError(msg('cloud_engines.unsupported_provider'))
            settings = settings if isinstance(settings, dict) else {}
            allowed = (*provider.settings, *provider.config_keys)
            undocumented = sorted(str(name)[:64] for name in settings if name not in allowed)
            if undocumented:
                raise ValueError(msg('cloud_engines.undocumented_name', names=', '.join(undocumented)))
            clean = {}
            for name, value in settings.items():
                if value in (None, ''):
                    continue
                value = value.strip() if isinstance(value, str) else value
                if not VALIDATORS[name](value):
                    raise ValueError(msg('cloud_engines.invalid_value', name=name))
                clean[name] = value
            if not isinstance(daily_calls, int) or isinstance(daily_calls, bool) or not 1 <= daily_calls <= MAX_DAILY_CALLS:
                raise ValueError(msg('cloud_engines.invalid_daily_calls', max=MAX_DAILY_CALLS))
            row = {'provider': provider_id, 'settings': clean, 'daily_calls': daily_calls}
        row['revision'] = _secrets.token_hex(8)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            found = db.execute('SELECT value FROM config WHERE key=?', (PROFILE_KEY,)).fetchone()
            try:
                rows = json.loads(found['value']) if found else {}
            except (TypeError, ValueError):
                rows = {}
            rows = dict(rows) if isinstance(rows, dict) else {}
            rows[engine_id] = row
            db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (PROFILE_KEY, json.dumps(rows)))
        return self.status(engine_id)

    def save_secret(self, engine_id, provider_id, name, value):
        """Store (or with ``''`` remove) one documented credential of one provider."""
        provider = PROVIDERS.get(provider_id)
        if engine_id not in ENGINES or provider is None or provider.engine != engine_id:
            raise ValueError(msg('cloud_engines.unsupported_provider'))
        if name not in provider.secrets:
            raise ValueError(msg('cloud_engines.undocumented_name', names=str(name)[:64]))
        if not isinstance(value, str):
            raise ValueError(msg('cloud_engines.invalid_value', name=name))
        value = value.strip()
        slot = secret_slot(engine_id, provider_id, name)
        if value and not _SECRET_VALUE.match(value):
            raise ValueError(msg('cloud_engines.invalid_value', name=name))
        # A changed credential may be another account: the revision moves first, so a
        # qualification bound to the old revision (the Judgment AI's, ``identity``)
        # never authorizes a launch that already carries the new value.
        self._bump_revision(engine_id, provider_id)
        if not value:
            self.store.remove_secret(slot)
        elif not _SECRET_VALUE.match(value):
            raise ValueError(msg('cloud_engines.invalid_value', name=name))
        else:
            self.store.secret(slot, value)
        return self.status(engine_id)

    def _bump_revision(self, engine_id, provider_id):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            found = db.execute('SELECT value FROM config WHERE key=?', (PROFILE_KEY,)).fetchone()
            try:
                rows = json.loads(found['value']) if found else {}
            except (TypeError, ValueError):
                return
            row = rows.get(engine_id) if isinstance(rows, dict) else None
            if not isinstance(row, dict) or row.get('provider') != provider_id:
                return
            row['revision'] = _secrets.token_hex(8)
            db.execute('UPDATE config SET value=? WHERE key=?', (json.dumps(rows), PROFILE_KEY))

    def identity(self, engine_id):
        """What a qualification of this engine is bound to: ``subscription`` or ``<provider>:<revision>``.

        The revision changes whenever the profile or one of its credentials is saved,
        so a Judgment AI qualified on the subscription or on an earlier cloud account
        is never reused for a different model destination (#211 review).
        """
        provider_id = self.provider(engine_id)
        if provider_id == SUBSCRIPTION:
            return SUBSCRIPTION
        return f"{provider_id}:{self._row(engine_id).get('revision') or ''}"
