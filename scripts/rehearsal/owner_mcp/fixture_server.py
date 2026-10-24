"""Fixture owner MCP server (#210): its token comes from a company CLI on PATH that reads a file under HOME.

It records what its own process saw (where it runs, whether the token and its
secret environment value arrived), never the values themselves.
"""
import json
import os
import subprocess

from mcp.server.mcpserver import MCPServer

mcp = MCPServer('fixture')


def record(entry):
    with open(os.environ['FIXTURE_LOG'], 'a') as log:
        log.write(json.dumps(entry) + '\n')


@mcp.tool()
def read_ticket(key: str) -> str:
    """Read one ticket (read-only)."""
    try:
        token = subprocess.run(['company-auth', 'token'], capture_output=True, text=True, timeout=5).stdout.strip()
        via = 'PATH'
    except FileNotFoundError:
        token, via = '', 'not-on-PATH'
    record({'key': key, 'env': {name: os.environ.get(name) for name in ('HOME', 'USER', 'LOGNAME', 'PATH', 'TMPDIR')},
            'secret_env_present': bool(os.environ.get('COMPANY_TOKEN_NAME')), 'token_found': bool(token), 'via': via})
    return f'ticket {key}: token {"ok" if token else "missing"}'


@mcp.tool()
def comment_ticket(key: str, text: str) -> str:
    """Add a comment to a ticket (a write)."""
    record({'write': key})
    return 'commented'


if __name__ == '__main__':
    mcp.run()
