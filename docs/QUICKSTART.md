# Start using Still

Install Still, connect one AI, and try a conversation. You do not need to configure every service first.

## Current use versus planned work

The [release manifest](release-manifest.json) records what the published build contains and what was checked. [Product status](product-status.en.md) separates release coverage, newer source changes and observed use. Updating this guide does not publish a new application build.

## Before you install

You need macOS, Linux, or Windows with [WSL2](#windows-wsl2), and one AI connection: a ChatGPT plan through Codex, a Claude plan through Claude Code, an API key, or an already running Ollama server. The installer supplies Python through uv; Homebrew, Python, Docker and git are not prerequisites. AI subscriptions, API usage and model downloads are separate from Still.

A local installation is not necessarily local-only processing. An external AI receives the conversation and context used for a request. Start with non-sensitive material and connect only the accounts and folders you intend to use.

## Install on macOS or Linux

Inspect the [installer](../scripts/install.sh), then run:

```sh
curl -LsSf https://raw.githubusercontent.com/Jongtae/Still/main/scripts/install.sh | sh
```

<!-- scripts/dev/release.py updates the following sentence; edit its consumer and tests together if changing the format. -->
This command and the Homebrew commands below install the newest published release, `v0.6.0` (2026-11-07). To run unreleased `main`, use a checkout instead ([source installation](#remote-host--source-installation)).

The installer checks the release archive's SHA-256 against the manifest before installing. It uses a managed Python and starts Still in the same terminal. Set `AGENTOS_NO_START=1` to install without starting. Open a new terminal if `~/.local/bin` has just been added to your `PATH`.

On an Apple silicon Mac with Homebrew, the equivalent installation is:

```sh
brew tap jongtae/still https://github.com/Jongtae/Still
brew install jongtae/still/agentos
agentos start
```

The formula refuses Intel Macs; use the one-line installer there. The retained command name is `agentos`; it is not a different product. See [name compatibility](still-name-compatibility.en.md).

## Connect an AI

First setup runs in the terminal. Choose an AI and follow the prompts. Completed setup steps survive an interruption; running `agentos start` again resumes unfinished setup. Starts without an interactive terminal do not prompt.

| Connection | What to prepare |
| --- | --- |
| ChatGPT through Codex | Install the [official Codex CLI](https://learn.chatgpt.com/docs/cli) and sign in with `codex login`. Still confirms the selected connection; never paste a Codex credential into a conversation. |
| Claude through Claude Code | Install [Claude Code](https://code.claude.com/docs/en/setup). Where Still asks for a token, obtain it with `claude setup-token` and enter it only in the hidden setup input or local credential form. |
| API or Ollama | Select the provider, endpoint and model. Test the exact settings before applying them. API billing is separate from a subscription; Ollama must already be running. |

The optional Still access password is a new password for this installation, not your computer password. Without it, access stays local. To inspect setup or connections, open `http://127.0.0.1:8787` on this computer. The interface follows the setup language, with English, Korean, Japanese and Simplified Chinese available; some legacy text may still be Korean.

Official authentication details: [Codex](https://learn.chatgpt.com/docs/auth) and [Claude Code](https://code.claude.com/docs/en/authentication). A provider's supported authentication options do not automatically imply that every option is supported by Still.

## First task

Once the AI is connected, talk to Still in the terminal. Paste a short, non-sensitive paragraph and ask for a summary; then ask it to make the answer shorter. Check that the answer addresses the paragraph and that the follow-up uses the same context. An answer is a first-use check, not proof that every connected service works.

Telegram is optional. Say “connect Telegram” and follow the dedicated token input and pairing link; do not type the bot token into an ordinary message. The full [messenger setup guide](guide/connections.en.md#telegram) also covers the local Settings path. A file-backed check is available in [the operating guide](guide/operations.en.md#recommended-owner-dogfood-task); it is not a prerequisite for ordinary conversation.

For the legacy Gmail source-checkout connection, create the owner-only credential file before opening the local consent route:

```sh
agentos gmail-config \
  --oauth-client-json ~/Downloads/client_secret_XXXX.json \
  --secret-file /Users/your-name/.agentos-secrets/gmail.json
```

Then open `http://127.0.0.1:8787/google-gmail` in the same local browser session. Configuration does not connect an account or grant mailbox access. The [Gmail connection guide](guide/connections.en.md#gmail-source-checkout) gives the required file boundary, scope and evidence limits.

## Keep Still running

Keep the `agentos start` terminal open while using Still. Ctrl-C stops the foreground process. The computer must remain awake for remote requests and scheduled work.

On macOS, first setup may offer a login service. You can also register it explicitly:

```sh
agentos service install
agentos service status
```

Installation alone does not register a service. See [background service](guide/operations.en.md#background-service-macos) for executable paths, upgrade and rollback behavior, and the limits of the available validation. Linux and WSL2 do not use this launchd service.

## Windows (WSL2)

In PowerShell run `wsl --install`, restart Windows, open Ubuntu, and complete its initial account setup. Run the one-line Still installer inside Ubuntu. Install and sign in to Codex or Claude Code inside Ubuntu as well, not only in Windows. Windows files are available under `/mnt/c/`.

Native Windows support is separate work ([#33](https://github.com/Jongtae/Still/issues/33)). Keep Ubuntu open. Browser support and desktop prerequisites depend on the installed release; WSLg operation has not been observed. Check the release-versus-source distinction in [platform notes](guide/platforms.en.md) before relying on browser actions.

## When something does not work

Run `agentos guide` for credential-free setup and recovery information. If the browser did not open, use the local setup link in `~/.local/share/agentos/private/setup-link.txt`; do not share it. For an occupied port, use `agentos start --port 8788`. Do not repeatedly resend an action whose external result is unknown.

For [restart, update and uninstall](guide/operations.en.md), [messengers and Google](guide/connections.en.md), or [company networks and MCP servers](guide/workplace.en.md), use the relevant guide rather than repeating setup from scratch.

## Further configuration

The detailed instructions have moved. These anchors keep existing README, site and issue links usable; they are not a second copy of the instructions.

<a id="company-cloud-ai-amazon-bedrock-microsoft-foundry-azure-openai"></a>
[Company cloud AI](guide/workplace.en.md#company-cloud-ai).

<a id="other-model-connections"></a>
[Other model connections](guide/connections.en.md#other-model-connections).

<a id="intranet-addresses"></a>
[Intranet addresses](guide/workplace.en.md#intranet-addresses).

<a id="company-tools-through-your-own-mcp-server"></a>
[Company tools through your own MCP server](guide/workplace.en.md#company-tools-through-your-own-mcp-server).

<a id="work-mode-on-a-company-network"></a>
[Work mode on a company network](guide/workplace.en.md#work-mode-on-a-company-network).

<a id="browser-on-a-linux-desktop"></a>
[Browser on a Linux desktop](guide/platforms.en.md#browser-on-a-linux-desktop).

<a id="recommended-owner-dogfood-task"></a>
[Recommended owner dogfood task](guide/operations.en.md#recommended-owner-dogfood-task).

<a id="telegram"></a>
[Telegram](guide/connections.en.md#telegram).

<a id="mattermost"></a>
[Mattermost](guide/connections.en.md#mattermost).

<a id="slack"></a>
[Slack](guide/connections.en.md#slack).

<a id="restart-and-update"></a>
[Restart and update](guide/operations.en.md#restart-and-update).

<a id="uninstall"></a>
[Uninstall](guide/operations.en.md#uninstall).

<a id="background-service-macos"></a>
[Background service (macOS)](guide/operations.en.md#background-service-macos).

<a id="google-drive-gmail-and-calendar"></a>
[Google Drive, Gmail and Calendar](guide/connections.en.md#google-drive-gmail-and-calendar).

<a id="google-calendar-source-checkout"></a>
[Google Calendar: per-service configuration](guide/connections.en.md#google-calendar-source-checkout).

<a id="gmail-source-checkout"></a>
[Gmail: per-service configuration](guide/connections.en.md#gmail-source-checkout).

<a id="remote-host--source-installation"></a>
[Remote host / source installation](guide/operations.en.md#remote-host--source-installation).

<a id="preview-scope"></a>
[Preview scope and evidence](product-status.en.md).

<a id="advanced-local-manifest-plugins"></a>
[Advanced: local manifest plugins](guide/operations.en.md#advanced-local-manifest-plugins).

<a id="historical-general-tool-runtime-020"></a>
The old general-runtime account is [preserved at the pre-edit revision](https://github.com/Jongtae/Still/blob/6edc3380b613ab0c2897ba7a913ab658150a68f4/docs/QUICKSTART.md#historical-general-tool-runtime-020). It is historical evidence, not current setup instructions.
