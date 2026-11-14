# Use Still on a company network

[Quick start](../QUICKSTART.md) · [Connections](connections.en.md) · [Operations](operations.en.md)

Use only services and destinations your organization permits. Still's work mode is a bounded application control, not a network firewall or a claim of compliance with company policy. The existing configuration and tests define what it controls; a company-network operating observation is separate evidence.

## Company cloud AI

In the selected CLI's account settings, choose the cloud provider supported by that route: Claude Code with Amazon Bedrock or Microsoft Foundry, or Codex with Azure OpenAI. Enter the documented configuration supplied by your organization and a daily call limit. Unsupported variable names are refused; do not assume a provider option in the underlying CLI is automatically available through Still.

Credentials are entered only in local Settings, kept in the private credential store and passed to the intended CLI process. They must not reach model prompts, logs, execution records or the model's shell commands. Both work and judgment calls use the selected provider and count toward the daily limit. At the limit, calls stop rather than silently changing AI. A configuration change affects the next request; an in-flight request keeps its recorded route.

For a Work, the CLI starts with an empty home directory. A Bedrock profile therefore needs explicit `AWS_PROFILE`, `AWS_CONFIG_FILE` and `AWS_SHARED_CREDENTIALS_FILE` paths, or supported API/access keys. An `aws sso login` cache is not covered by the recorded verification. For Foundry, the documented route uses an API key or Entra bearer token, not an inherited `az login` session.

Provider instructions: [Bedrock](https://code.claude.com/docs/en/amazon-bedrock), [Foundry](https://code.claude.com/docs/en/microsoft-foundry) and [Codex provider configuration](https://learn.chatgpt.com/docs/config-file/config-advanced). A company gateway that accepts OpenAI-compatible Chat Completions can use Still's compatible API connection. The gateway request shape has fixture evidence, not an observation of a real company gateway.

## Intranet addresses

Private-use destinations are refused unless the owner declares an exact host in local Settings → External connections → Intranet addresses. Conversation cannot add a host. Every change is recorded.

An entry is a hostname with an optional port, such as `jira.corp.example` or `gitlab.corp.example:8443`. IP literals, wildcards and `localhost` are not accepted. Without an explicit port, only 443 and 80 are allowed. A declared hostname may resolve to admitted private-use address ranges, but never loopback, link-local or cloud metadata addresses. Undeclared private addresses remain refused.

This declaration affects public page reads (which, since #820, take any public or declared address without an approval), browser access, MCP setup, API probes and code runs where their respective controls permit them. A code run naming intranet hosts may name only intranet hosts. Reachability does not create credentials, write permission or an action approval. Removing a host takes effect on the next request; dependent settings remain stored but cannot use the removed destination.

### Proxy and certificate boundaries

These paths do not all use the same proxy or certificate store:

| Path | Recorded behavior |
| --- | --- |
| macOS embedded browser | Uses system proxy settings and Keychain trust. |
| Page reads and API calls to a declared intranet host | Connect directly to the checked address, without a proxy; use Python's OpenSSL trust store. |
| Other API calls and signed-in-session refresh | Use Python's trust store and the Still process's `HTTPS_PROXY` / `NO_PROXY`, where applicable. |
| Code runs | Connect directly and use Node's bundled certificate authorities. |
| MCP setup through Claude Code | Uses the CLI's trust settings; proxy variables are not forwarded by this setup path. |

An untrusted certificate fails the handshake; there is no instruction here to disable TLS checks. This table records the existing path-specific behavior, not a universal guarantee for every platform or CLI version. Evidence is injected-resolver tests, not a company-network audit. See the [platform guide](platforms.en.md) for the newer Linux browser's separate scope.

## Company tools through your own MCP server

Still does not run executable scripts supplied inside a skill. Separate the executable service from the instructions:

1. Use a maintained server, or an MCP server you control, to obtain the company credential and call the API inside its own process.
2. Register that server in the CLI's supported user configuration: for example Claude Code's user-scope MCP setup or Codex's `mcp_servers` configuration.
3. Enable the use of the AI's connected services in Still. Ask to use the server and confirm the proposed server and read operations. A server present only in CLI configuration is not automatically started for a Work.
4. Keep query syntax, procedures and project conventions in a script-free skill.

In a Claude Code Work, the server receives the owner's `HOME` and `USER`, but a restricted `PATH` (`/usr/bin:/bin`); use an absolute command or an explicit server `env` path. In a Codex Work, the default home is the turn's empty home, with no `USER`. Required `HOME`, `PATH`, `USER`, `LOGNAME` or `TMPDIR` values can be specified in that server definition's `env`; they go to that server, not the worker's shell.

Other server environment values may be passed through the CLI by name. Do not put token values in tool arguments: a secret-shaped input is refused. The model's shell must not receive the server's secrets.

Still decides mediated calls under the confirmed read classification and current pilot policy. A payment requires exact approval. The server's own account rights, internal behavior and network traffic are not thereby controlled by Still, including in work mode. The recorded local qualification used a fixture server and fixture company CLI on Claude Code 2.1.287 and codex-cli 0.153.4; no company service or model provider was contacted. Those versioned observations are not a claim about every later binary.

## Work mode on a company network

Only the owner on the local Settings surface can switch work mode. Conversation or a phone link cannot do so, and changes are recorded.

While enabled, Still closes temporary inbound links and refuses new phone sign-in/input tunnels. Telegram pauses. Mattermost and Slack pause unless the owner has explicitly marked the destination company-approved; suppressed replies are not delivered later.

The mode refuses the AI's public web search, public page reads, weather, browser, API calls, code runs and MCP discovery before they run, subject to the specifically allowed intranet paths. The CLI's own web search is off, signed-in sites are not refreshed, and no skill is fetched from GitHub. Approved intranet page reads, API calls and code runs may still operate. The browser remains refused in work mode because a page can load resources from destinations it chooses.

**The selected AI connection still works.** Work mode does not comprehensively control a CLI's model traffic, telemetry, update checks or every network operation its sandbox permits. Services the owner connected separately, such as Google, AI-side connectors and owner MCP servers, also have their own boundaries. Do not describe work mode as an air gap or as proof that no company data can leave the computer.

Evidence is unit testing with fake tunnels and transports. Confirm the actual AI route, services, trust stores and organizational permission before using real company data.
