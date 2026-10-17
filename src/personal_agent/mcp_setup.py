"""#1299: official CLI remote setup primitives; callers own confirmation.

No protocol client, model prompt, tool call or authority lives here. Only public
HTTP definitions are supported. CLI credentials stay with the CLI. Status is
intentionally weaker than authenticated account/scope/schema verification.
"""
from __future__ import annotations

import json
import ipaddress
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, unquote

from .bounded_execution import bounded_run, EngineInterrupted, ExecutionError, SECRET_PATTERN
from .context_inbox import SENSITIVE
from .providers import ProviderError
from .owner_mcp import valid_name, claude_definitions

TOOL_NAME = re.compile(r'[A-Za-z0-9_.-]{1,60}\Z')
SDK_VERSION = '0.2.166'
MAX_TOOLS = 256
STATES = frozenset({'connected', 'pending', 'failed', 'needs-auth', 'disabled', 'unknown', 'unsupported', 'cancelled'})



class SetupFailure(ValueError):
    """Whitelisted setup outcome; raw CLI output never becomes recovery context."""
    def __init__(self, phase, state):
        self.phase = phase
        self.observed_state = state if state in STATES else 'unknown'
        self.retryable = self.observed_state in {'failed', 'pending', 'needs-auth', 'connected'}
        self.settled_state = 'unknown' if self.observed_state == 'unknown' else 'failed'
        step = ('같은 연결 설정을 다시 확인하면 현재 상태부터 확인하고 원래 작업을 이어갈게요.' if self.retryable else
                '결과가 불확실해 자동으로 다시 등록하거나 로그인하지 않았어요. 현재 연결 상태 확인이 필요해요.'
                if self.observed_state == 'unknown' else
                '취소되었거나 지원되지 않는 연결은 다시 실행하지 않았어요. 다른 사용 가능한 연결을 검토할 수 있어요.')
        super().__init__(f"연결 설정의 {phase} 단계에서 준비를 확인하지 못했어요 ({self.observed_state}). " + step)

def remote_url(url, redact=lambda value: value):
    """Public definition only. Credential/header input needs a secret channel."""
    if not isinstance(url, str) or len(url) > 1000 or SENSITIVE.search(url):
        raise ValueError('공개 HTTPS 서버 주소를 확인하세요.')
    decoded = url
    while True:
        if SENSITIVE.search(decoded) or SECRET_PATTERN.search(decoded) or redact(decoded) != decoded:
            raise ValueError('인증 정보 없는 공개 HTTPS 서버 주소를 확인하세요.')
        current = unquote(decoded)
        if current == decoded:
            break
        decoded = current
    try:
        parts = urlsplit(url)
        valid = (url.isascii() and not any(c.isspace() or ord(c) < 32 for c in url)
                 and '\\' not in url and parts.scheme == 'https' and parts.hostname
                 and not parts.username and not parts.password and not parts.query and not parts.fragment
                 and '{' not in url and '}' not in url)
        _port = parts.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('인증 정보 없는 공개 HTTPS 서버 주소를 확인하세요.')
    # Reuse the existing public-destination syntax/literal policy. Resolution
    # and OAuth redirects remain CLI-owned, not an Still-pinned egress path.
    from .local_tools import normalize_public_url
    normalize_public_url(url)
    host = parts.hostname.rstrip('.')
    if '%' in host:
        raise ValueError('인코딩된 호스트 주소는 연결할 수 없어요.')
    try:
        numeric = str(ipaddress.IPv4Address(socket.inet_aton(host)))
    except OSError:
        numeric = None
    if numeric is not None and host != numeric:
        raise ValueError('축약하거나 다른 진법으로 적은 IP 주소는 연결할 수 없어요.')
    return url


def setup_value(value, redact=lambda value: value):
    """Canonical public owner-confirmation content, not executable instructions."""
    if not isinstance(value, str) or len(value) > 4000:
        raise ValueError('연결할 서버의 공개 정보를 확인하세요.')
    try:
        data = json.loads(value)
    except ValueError:
        raise ValueError('연결할 서버의 공개 정보를 JSON으로 주세요.') from None
    fields = {'engine', 'name', 'url', 'source', 'publisher', 'licence', 'required_tools', 'read_tools', 'money'}
    if not isinstance(data, dict) or set(data) != fields or redact(value) != value or SECRET_PATTERN.search(value) or SENSITIVE.search(value):
        raise ValueError('인증 정보 없이 검토된 연결 정보만 주세요.')
    if data['engine'] != 'claude-code':
        raise ValueError('이 AI의 새 서버 인증·준비 확인은 아직 지원하지 않아요. 현재 연결이나 일반 API·브라우저 경로를 검토하세요.')
    name = data['name']
    if not valid_name(name) or name.startswith('-'):
        raise ValueError('MCP 서버 이름을 확인하세요.')
    for key in ('url', 'source'):
        data[key] = remote_url(data[key], redact)
    for key in ('publisher', 'licence'):
        text = data[key]
        if not isinstance(text, str) or not text.strip() or len(text) > 120 or any(ord(c) < 32 for c in text):
            raise ValueError('게시자와 라이선스를 짧게 적고, 확인하지 못한 값은 unknown으로 주세요.')
    if not isinstance(data['money'], bool):
        raise ValueError('돈이 오갈 수 있는 연결인지 표시하세요.')
    for key in ('required_tools', 'read_tools'):
        names = data[key]
        if (not isinstance(names, list) or len(names) > 16
                or any(not isinstance(n, str) or not TOOL_NAME.fullmatch(n) for n in names)
                or len(set(names)) != len(names)):
            raise ValueError('필요한 동작 이름은 중복 없이 16개까지 주세요.')
        data[key] = sorted(names)
    if not set(data['read_tools']) <= set(data['required_tools']):
        raise ValueError('읽기로 검토할 동작은 필요한 동작 목록 안에서 골라 주세요.')
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def status_result(state='unknown', tools=()):
    return {'state': state, 'tools': list(tools), 'authentication': 'unknown',
            'account': 'unknown', 'granted_scopes': 'unknown', 'input_schemas': 'unknown',
            'authority': 'none'}


def observed_status(payload, name):
    """Whitelist the SDK response; never emit config, auth URLs or errors."""
    rows = payload.get('mcpServers') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get('name') != name:
        return status_result()
    row = rows[0]
    state = row.get('status')
    if state not in {'connected', 'pending', 'failed', 'needs-auth', 'disabled'}:
        return status_result()
    if state != 'connected':
        return status_result(state)
    tools = row.get('tools')
    if not isinstance(tools, list) or len(tools) > MAX_TOOLS:
        return status_result()
    names = []
    for tool in tools:
        value = tool.get('name') if isinstance(tool, dict) else None
        if not isinstance(value, str) or not TOOL_NAME.fullmatch(value) or SENSITIVE.search(value) or SECRET_PATTERN.search(value) or value in names:
            return status_result()
        names.append(value)
    return status_result('connected', sorted(names))


def environment(home):
    """Only the CLI's owner profile and executable search path, not host tokens."""
    env = {'HOME': str(home), 'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
           'LANG': 'en_US.UTF-8', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}
    # Same owner identity as BoundedExecutionAdapter.environment (#1247):
    # macOS CLI credentials use the account name to locate their Keychain item.
    # These identity names are not credential values; tokens remain excluded.
    for key in ('USER', 'LOGNAME'):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


class RemoteMcpSetup:
    """Bounded official CLI calls after the caller admits an exact owner draft.

    No caller may equate a zero registration/login exit code with readiness.
    Codex is explicitly unsupported until selected-server isolation is proven.
    """
    def __init__(self, binary, home, *, runner=subprocess.run, interrupted=None, redact=lambda value: value, resolver=None):
        if not isinstance(binary, (str, Path)) or not str(binary) or not Path(binary).is_absolute():
            raise ValueError('설치된 AI 실행 파일의 전체 경로를 확인하세요.')
        self.binary, self.home = str(binary), Path(home)
        self.runner, self.interrupted, self.redact = runner, interrupted, redact
        from .local_tools import PublicPageReader
        self.destinations = PublicPageReader(resolver=resolver)

    def _check_interrupted(self):
        if self.interrupted is not None:
            try:
                reason = self.interrupted()
            except Exception:
                reason = 'cancellation-state-unavailable'
            if reason:
                raise EngineInterrupted(reason)

    def _run(self, argv, timeout, *, destination, env=None, before_dispatch=None):
        with tempfile.TemporaryDirectory(prefix='agentos-mcp-setup-') as cwd:
            # Reuse the existing bounded resolver after owner admission. Reject
            # observed private answers before handing the endpoint to the CLI.
            # The CLI may resolve again; this is not DNS/redirect pinning.
            self._check_interrupted()
            self.destinations._validate_host(destination)
            self._check_interrupted()
            if before_dispatch is not None and not before_dispatch():
                raise ExecutionError('서버 설정이 바뀌어 명령을 실행하지 않았어요.')
            return bounded_run(self.runner, argv, cwd=cwd, env=env or environment(self.home),
                               timeout=timeout, interrupted=self.interrupted)

    def _registration_state(self, name):
        # Recheck after DNS as well: the CLI must not replace a definition that
        # appeared while resolution was blocked, including a malformed entry.
        try:
            config = json.loads((self.home / '.claude.json').read_text())
            servers = config.get('mcpServers', {})
            if not isinstance(servers, dict) or name in servers:
                return 'already-present'
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            return 'unknown'
        return 'absent'

    def register(self, engine, name, url):
        if engine != 'claude-code':
            return {'state': 'unsupported'}
        if not valid_name(name) or name.startswith('-') or SECRET_PATTERN.search(name) or self.redact(name) != name:
            raise ValueError('MCP 서버 이름을 확인하세요.')
        url = remote_url(url, self.redact)
        state = self._registration_state(name)
        if state != 'absent':
            return {'state': state}
        try:
            done = self._run([self.binary, 'mcp', 'add', '--transport', 'http', '--scope', 'user', name, url], 15, destination=url,
                             before_dispatch=lambda: self._registration_state(name) == 'absent')
            return {'state': 'registration-returned' if done.returncode == 0 else 'failed', 'configuration': 'unknown'}
        except EngineInterrupted:
            return {'state': 'cancelled', 'registration': 'unknown'}
        except (OSError, ValueError, ProviderError, subprocess.TimeoutExpired, ExecutionError):
            return {'state': 'unknown'}

    def login(self, engine, name, url):
        """The CLI opens its native browser flow; output never reaches a model."""
        if engine != 'claude-code':
            return {'state': 'unsupported'}
        if not valid_name(name) or name.startswith('-') or SECRET_PATTERN.search(name) or self.redact(name) != name:
            raise ValueError('MCP 서버 이름을 확인하세요.')
        expected = {'type': 'http', 'url': remote_url(url, self.redact)}
        if claude_definitions(self.home, [name]).get(name) != expected:
            return {'state': 'definition-changed', 'authentication': 'unknown'}
        try:
            done = self._run([self.binary, 'mcp', 'login', name], 180, destination=url,
                             before_dispatch=lambda: claude_definitions(self.home, [name]).get(name) == expected)
            return {'state': 'login-returned' if done.returncode == 0 else 'failed', 'authentication': 'unknown'}
        except EngineInterrupted:
            return {'state': 'cancelled', 'authentication': 'unknown'}
        except (OSError, ValueError, ProviderError, subprocess.TimeoutExpired, ExecutionError):
            return {'state': 'unknown', 'authentication': 'unknown'}

    def observe(self, engine, name, url):
        if engine != 'claude-code':
            return status_result('unsupported')
        if not valid_name(name) or name.startswith('-') or SECRET_PATTERN.search(name) or self.redact(name) != name:
            raise ValueError('MCP 서버 이름을 확인하세요.')
        url = remote_url(url, self.redact)
        # Use the SDK in a separate bounded process: suppress its untrusted logs
        # without changing the service's logging or inheriting host credentials.
        env = environment(self.home)
        env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
        try:
            done = self._run([sys.executable, '-m', 'personal_agent.mcp_setup',
                              self.binary, name, url], 25, destination=url, env=env)
            if done.returncode != 0 or len(done.stdout) > 24000:
                return status_result()
            result = json.loads(done.stdout)
            # Treat even helper output as untrusted, never forwarding extra fields.
            if (not isinstance(result, dict) or result.get('state') not in STATES
                    or self.redact(json.dumps(result)) != json.dumps(result)):
                return status_result()
            state = result['state']
            if state != 'connected':
                return status_result(state)
            if not isinstance(result.get('tools'), list) or len(result['tools']) > MAX_TOOLS:
                return status_result()
            return observed_status({'mcpServers': [{'name': name, 'status': state,
                                    'tools': [{'name': v} for v in result.get('tools', [])]}]}, name)
        except EngineInterrupted:
            return status_result('cancelled')
        except (OSError, ValueError, TypeError, ProviderError, subprocess.TimeoutExpired, ExecutionError):
            return status_result()


async def sdk_observe(binary, name, url, *, sdk=None):
    """Official SDK API only. No query() / user prompt is ever submitted."""
    import anyio
    if not binary or not Path(binary).is_absolute() or not valid_name(name) or name.startswith('-') or SECRET_PATTERN.search(name):
        return status_result('unsupported')
    if sdk is None:
        import claude_agent_sdk as sdk
        if sdk.__version__ != SDK_VERSION:
            return status_result('unsupported')
    async def deny(*args, **kwargs):
        return sdk.PermissionResultDeny(message='Readiness does not authorize tool calls.')
    with tempfile.TemporaryDirectory(prefix='agentos-mcp-status-') as cwd:
        options = sdk.ClaudeAgentOptions(cli_path=binary, cwd=cwd, setting_sources=[],
                    strict_mcp_config=True, mcp_servers={name: {'type': 'http', 'url': remote_url(url)}},
                    tools=[], can_use_tool=deny, permission_mode='default', stderr=lambda _line: None,
                    max_buffer_size=256 * 1024)
        with anyio.fail_after(20):
            async with sdk.ClaudeSDKClient(options) as client:
                for _ in range(10):
                    result = observed_status(await client.get_mcp_status(), name)
                    if result['state'] != 'pending':
                        return result
                    await anyio.sleep(0.5)
                return result


def main():
    # The process is dedicated to this operation. Suppress raw SDK logs and
    # exceptions before import; only the sanitized result goes to stdout.
    import logging
    logging.disable(logging.CRITICAL)
    result = status_result()
    try:
        if len(sys.argv) == 4 and valid_name(sys.argv[2]):
            import anyio
            result = anyio.run(sdk_observe, *sys.argv[1:])
    except ImportError:
        result = status_result('unsupported')
    except Exception:
        pass
    print(json.dumps(result))


if __name__ == '__main__':
    main()
