"""Credential-free stdio tools, run only by the separate AppWorld venv."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit


def load_rules():
    spec = importlib.util.spec_from_file_location(
        '_still_appworld_world', Path(__file__).with_name('appworld.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rules = load_rules()


def unwrap(response):
    if isinstance(response, dict) and 'response' in response:
        return response['response']
    return response


def filter_tools(docs, apps):
    tools = {}
    for doc in docs:
        app, separator, api = doc['name'].partition('__')
        if separator and app in apps and rules.exposed(app, api):
            tools[doc['name']] = {**doc, 'input_schema': rules.strip_credentials(doc['input_schema'])}
    return tools


def fill_arguments(app, input_schema, arguments, token_getter):
    filled = {key: value for key, value in (arguments or {}).items()
              if key not in rules.WORLD_CREDENTIALS}
    for name in rules.WORLD_CREDENTIALS:
        if name in input_schema.get('properties', {}):
            token_app = 'file_system' if name == 'file_system_access_token' else app
            try:
                filled[name] = token_getter(token_app)
            except Exception:
                raise RuntimeError(f'world login failed for {token_app}') from None
    return filled


def call_tool(name, arguments, tools, apis, token_getter):
    app, separator, api = name.partition('__')
    if not separator or name not in tools or not rules.exposed(app, api):
        return {'response': {'message': f"Tool '{name}' not found.", 'is_error': True}}
    try:
        filled = fill_arguments(app, tools[name]['input_schema'], arguments, token_getter)
    except RuntimeError as error:
        return {'response': {'message': str(error), 'is_error': True}}
    try:
        return apis[app][api](**filled)
    except Exception:
        # API exceptions may echo credentials from the filled arguments.
        return {'response': {'message': 'world tool call failed', 'is_error': True}}


def make_token_getter(apis, secrets):
    from appworld.collections.apis import _access_token_from
    from munch import Munch

    tokens = {}
    user = None

    def get_token(app):
        nonlocal user
        try:
            if app not in tokens:
                if user is None:
                    profile = unwrap(apis.supervisor.show_profile())
                    passwords = unwrap(apis.supervisor.show_account_passwords())
                    account_passwords = {item['account_name']: item['password'] for item in passwords}
                    secrets.extend(account_passwords.values())
                    user = Munch(email=profile['email'], phone_number=profile['phone_number'],
                                 account_passwords=account_passwords)
                # The shortcut expects an unwrapped login response.
                def login(**arguments):
                    return unwrap(apis[app].login(**arguments))

                # Remote APIs reject the shortcut's virtual-time override.
                tokens[app] = _access_token_from(user, login, app, disable_expiry=False)
                if not isinstance(tokens[app], str) or not tokens[app]:
                    del tokens[app]
                    raise ValueError('missing token')
                secrets.append(tokens[app])
            return tokens[app]
        except Exception:
            raise RuntimeError(f'world login failed for {app}') from None

    return get_token


def response_text(response, secrets):
    text = json.dumps(response, indent=2)
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            text = text.replace(json.dumps(secret)[1:-1], '[REDACTED]')
    return text


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remote-apis-url', required=True)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--apps', required=True)
    args = parser.parse_args(argv)
    try:
        url = urlsplit(args.remote_apis_url)
        if (not args.remote_apis_url.startswith('http://127.0.0.1:') or
                url.hostname != '127.0.0.1' or not url.port or url.username or url.password or
                url.path not in ('', '/') or url.query or url.fragment):
            raise ValueError('invalid URL')
    except ValueError:
        parser.error('remote APIs URL must be an HTTP loopback URL with a port')
    apps = args.apps.split(',')
    if 'supervisor' not in apps:
        parser.error('supervisor must be present')
    try:
        args.apps = rules.validated_apps(apps, args.root / 'data/api_docs/standard')
    except ValueError:
        parser.error('invalid AppWorld apps')
    return args


def main():
    args = parse_args()
    from appworld import update_root

    update_root(str(args.root))
    from appworld.collections.api_docs import ApiDocCollection
    from appworld.collections.apis import ApiCollection
    from mcp.server import Server, ServerRequestContext
    from mcp.server.stdio import stdio_server
    from mcp.types import (CallToolRequestParams, CallToolResult, ListToolsResult,
                           PaginatedRequestParams, TextContent, Tool)

    docs = ApiDocCollection.build(load_apps=tuple(args.apps)).mcp()
    tools = filter_tools(docs, args.apps)
    originals = {doc['name']: doc for doc in docs if doc['name'] in tools}
    load_apps = set(args.apps)
    if any('file_system_access_token' in doc['input_schema'].get('properties', {})
           for doc in originals.values()):
        load_apps.add('file_system')
    apis, _ = ApiCollection.load(
        random_seed=100, load_apps=tuple(sorted(load_apps)), remote_apis_url=args.remote_apis_url,
        raise_on_failure=False, wrap_response=True, unwrap_response=False,
        max_num_requests=None, skip_setup=True)
    secrets = []
    token_getter = make_token_getter(apis, secrets)

    async def list_tools(ctx: ServerRequestContext, params: PaginatedRequestParams | None):
        return ListToolsResult(tools=[Tool(name=doc['name'], description=doc['description'],
                                          input_schema=doc['input_schema']) for doc in tools.values()])

    async def handle_call(ctx: ServerRequestContext, params: CallToolRequestParams):
        response = call_tool(params.name, params.arguments, originals, apis, token_getter)
        return CallToolResult(content=[TextContent(type='text', text=response_text(response, secrets))],
                              structured_content=None)

    server = Server('AppWorld (Still eval)', on_list_tools=list_tools, on_call_tool=handle_call)

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(run())


if __name__ == '__main__':
    main()
