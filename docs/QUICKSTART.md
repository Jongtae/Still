# Still — install, configure, talk

Still is a self-hosted personal agent preview. One local process serves browser setup and task records; you talk to it in the terminal where you ran `agentos start` (once an AI is connected) and in your paired Telegram chat. The browser screens open in the language the terminal setup used (English otherwise), with English, Korean, Simplified Chinese and Japanese selectable; connection errors and Still's catalogued system lines (including the connection and folder-permission handoffs, family sharing receipts and the phone sign-in link) follow your language in Telegram, the terminal and the browser. Still in Korean: parts of conversational Settings, many error replies in the browser Settings screens, calendar draft field labels, the phone sign-in page, the phone input link and page, the family assistant setup page, and, in a failure reply, the reasons given for some failed steps. Docker and Kubernetes are not required. Homebrew installs Python automatically; model runtimes and model weights are separate.

## Current use versus planned work

The main path is the one below: install, connect the AI subscription you already have, pair Telegram, and ask for what you need ([First task](#first-task)). The smaller [file-workspace check](#recommended-owner-dogfood-task) remains available for a direct model connection. [Product status](product-status.en.md) separates what has been observed in real use from what is tested only with simulated services, and from what is still planned. Check the installed revision when comparing a released Homebrew build with current source; a merged documentation PR is not a new application release.

[Owner control requirements](owner-control-contract.en.md) describe implemented versus planned boundaries. A local install can use an external model; approve only the data/destinations you intend. No ticket/cart/booking/payment, account creation or new external action is part of the recommended test. Do not publish private documents, credentials or full tool payloads as repository evidence.

## Before you install

- **A computer and a connection.** macOS, Linux, or Windows with WSL2, online. The installer brings its own Python through uv; you do not need Homebrew, Python, Docker or git.
- **One AI to do the work:**
  - ChatGPT plan: the official [Codex CLI](https://github.com/openai/codex) (`curl -fsSL https://chatgpt.com/codex/install.sh | sh`), signed in with `codex login`.
  - Claude plan: [Claude Code](https://code.claude.com/docs/en/setup) (`curl -fsSL https://claude.ai/install.sh | bash`) and the token from `claude setup-token`.
  - Or an API key from OpenAI, Anthropic or an OpenAI-compatible server such as OpenRouter (billed per use, separately from a subscription), or [Ollama](https://ollama.com) running on this computer.

  If the CLI you choose is not installed yet, first setup opens its install page and continues once it is installed.
- **Optional: Telegram.** The [Telegram](https://telegram.org) app on your phone or this computer, to create a bot with @BotFather (`/newbot`) and chat with Still from your phone. First setup walks you through it.

## Install on macOS or Linux

One command installs and starts Still. You do not need Homebrew, Python, Docker or git:

```sh
curl -LsSf https://raw.githubusercontent.com/Jongtae/Still/main/scripts/install.sh | sh
```

This command and the Homebrew commands below install the newest published release, `v0.3.0` (2026-10-31). To run unreleased `main`, use a checkout instead ([source installation](#remote-host--source-installation)).

The [installer](../scripts/install.sh) first downloads the published release archive and checks its SHA-256 against the [release manifest](release-manifest.json); on a mismatch it installs nothing. If you do not already have a current [uv](https://docs.astral.sh/uv/), it installs a pinned version through Astral's official installer, which adds `~/.local/bin` to your shell `PATH`. It then installs Still with a uv-managed Python, ignoring your own uv settings, and starts it; the first setup described below then runs in the same terminal. Set `AGENTOS_NO_START=1` to install without starting. Later, open a new terminal and run `agentos start`. Running the same command again after a release updates Still. To remove it, see [Uninstall](#uninstall).

If you use Homebrew on an Apple silicon Mac, this installs the same release; on an Intel Mac the formula refuses to install, so use the command above. This repository is its own tap, so add it once by URL:

```sh
brew tap jongtae/still https://github.com/Jongtae/Still
brew install jongtae/still/agentos
agentos start
```

The first start is set up in the same terminal, like a first run of Claude Code. The terminal follows your system language (Korean, Japanese, Simplified Chinese, otherwise English). Each step can be skipped with Enter, and the steps you finish are kept if you stop with Ctrl-C:

1. **Access password.** Off unless you answer `y`. It is a new password only for Still (12+ characters, typed twice), not your Mac login password. Without one, only this computer can open Still.
2. **AI connection.** Choose ChatGPT (Codex), Claude (Claude Code), or an API key. If the chosen CLI is not installed, its official install page opens in the browser and setup continues when you press Enter. Codex then runs its own `codex login`; Claude Code asks for the token from `claude setup-token`. For an API key, Still explains the per-use billing, offers to open the provider's key page and proposes a default model where one exists; keys are typed hidden and stored in the private credential file. Choose *later* to connect in the browser.
3. **Telegram (optional).** Answer `y` to chat with Still from your phone; Telegram must already be installed on your phone or this computer ([telegram.org](https://telegram.org)). Still opens @BotFather in Telegram and lists the steps (`/newbot`, a display name, a username ending in `bot`); paste the token BotFather sends, and Still opens the pairing link for you to press Start.
4. **Start at login (macOS, optional).** Off unless you answer `y`. Asked once, only when no Still [login service](#background-service-macos) is registered yet and Still runs on its usual address (`127.0.0.1:8787`, no `--port`, `--host` or tunnel option) with no `AGENTOS_*` settings other than `AGENTOS_DATA`, because the login service starts Still without this terminal's environment. On `y`, this terminal hands Still over to the login service (`agentos service install`): it registers the service, waits until it answers, then opens the browser and exits, so you can close the terminal and Still starts again after a restart. If the service does not come up, setup prints why and Still keeps running in this terminal; turn it off later with `agentos service stop`.

The browser then opens at `http://127.0.0.1:8787` as the final check. Run `agentos start` again at any time; a finished setup is not repeated, and a stopped one resumes at the first unfinished step. Starts without a terminal (for example the background login service) do not prompt.

Connect the AI subscription you already have:

- **ChatGPT plan → Codex.** Install the official Codex CLI and log in (`codex login`), then select **Codex 로그인 완료 · 선택** in Still (**전환** when switching from another subscription engine). Still records only your confirmation; it does not request, read, or store the Codex login.
- **Claude plan → Claude Code.** Install Claude Code, run `claude setup-token`, and paste the token into the Claude Code token field once. Still keeps it in this computer's private credential store and passes it only to Claude Code.

A connected subscription engine receives only the bounded Still tools, not your local files, credentials, or arbitrary shell access. Requests send conversation content to OpenAI or Anthropic. No paid subscription is included with Still.

### Company cloud AI (Amazon Bedrock, Microsoft Foundry, Azure OpenAI)

When your AI comes through your company's cloud rather than a personal plan, open the AI chooser's account settings for the CLI and pick a **클라우드 제공자** (#211): Claude Code → Amazon Bedrock or Microsoft Foundry; Codex → Azure OpenAI. Enter the values your company gave you under the variable names the vendor documents ([Claude Code on Amazon Bedrock](https://code.claude.com/docs/en/amazon-bedrock), [Claude Code on Microsoft Foundry](https://code.claude.com/docs/en/microsoft-foundry), [Codex `model_providers`](https://learn.chatgpt.com/docs/config-file/config-advanced)) and a daily call limit. Only those names reach the CLI; any other name is refused.

- Credentials are entered in Settings on this computer only, stored in the private credential file, passed to the CLI process alone and never to the AI's shell commands, prompts, logs or Work records.
- Work and the Judgment AI both run through the selected provider, and both count against the daily call limit. At the limit nothing runs and nothing switches to another AI.
- A change applies from the next request; a request already running keeps the provider it started with.
- A Work turn runs the CLI with an empty home folder, so for Bedrock either save a Bedrock API key or access keys, or point `AWS_PROFILE` at your profile together with `AWS_CONFIG_FILE` / `AWS_SHARED_CREDENTIALS_FILE`. An SSO login cache (`aws sso login`) is looked up under the home folder and has not been verified here. For Foundry, an API key or an Entra bearer token; `az login` is not used.

### Other model connections

Still can also use a direct model connection: Ollama (an already running local model server), OpenAI-compatible Chat Completions endpoints, or OpenAI and Anthropic API keys. Choose a provider, endpoint and model name, test the settings, and apply the successful configuration. API keys stay in a private local file. Results depend heavily on how well the chosen model uses tools, so start with a subscription if you can. A company gateway that speaks OpenAI-compatible Chat Completions (for example LiteLLM in front of Bedrock or Azure) uses the OpenAI-compatible connection; its request shape is covered by a local fixture test (#211), not by an observation of a real gateway.

### Intranet addresses

Company hosts resolve to private addresses, which Still otherwise refuses so that nothing reaches this Mac, your home network or a cloud metadata service by accident. Add the exact hosts Still may reach in Settings > External connections > **사내 주소 (intranet addresses)**, on this Mac (#209), for example `jira.corp.example` or `gitlab.corp.example:8443`. A conversation cannot add one, and every change is recorded.

- An entry is one host name with an optional port: no IP address, no wildcard, never `localhost`. Without a port only 443 and 80 are allowed.
- A declared host may resolve to private-use space (10/8, 172.16/12, 192.168/16, 100.64/10, IPv6 unique-local), never to this Mac (loopback), link-local or a cloud metadata address. Undeclared private addresses stay refused, on every path.
- It reaches page reads (of pages you approved), the browser, MCP server setup, API connection probes and code runs. A code run that names an intranet host may name only intranet hosts; every other run is refused private-use addresses by its sandbox.
- Declaring grants reachability only: no credential, no write and no approval. A removal applies from the next request; settings that used the host are kept and refused until you add it again.

Company proxy and internal CA, as they are today: the browser uses macOS's proxy settings and the Keychain, so a company CA installed there is trusted. Page reads, and API calls to a declared host, connect directly to the checked address (no proxy) and trust Python's OpenSSL certificate store (not the Keychain); other API calls and signed-in session refreshes also trust that store and follow an `HTTPS_PROXY`/`NO_PROXY` set for the Still process. Code runs connect directly (no proxy) and their TLS is verified by Node's bundled CA list. MCP server setup runs the Claude Code CLI with its own trust settings and no proxy variables. A host whose certificate the path does not trust fails its TLS handshake; nothing is bypassed. Evidence is unit tests with injected resolvers, not an observation on a company network.

### Company tools through your own MCP server

Still never runs a script that comes inside a skill. If your skill for a company tool carries scripts (one that gets a token from the company's own tooling and calls the tool's API, say), split it in two (#210):

1. **The executable part becomes a small local MCP server you own.** Write it with the official MCP Python SDK (`MCPServer`, formerly FastMCP), or use a maintained server for that tool when its auth fits your company. The server gets its token itself, inside its own process: from an internal CLI, SSO or a token file.
2. **Register it in the CLI**, as you would for yourself: Claude Code's user scope (`claude mcp add --scope user …`, kept in `~/.claude.json`) or Codex's `~/.codex/config.toml` (`[mcp_servers.<name>]`).
3. **Let Still start it for a Work.** Keep **AI의 연결 서비스 사용** on in Settings > External connections. Ask in conversation to use the server; Still proposes which servers this AI may start for Works, and which of their operations are reads, and you confirm. A server only present in your CLI configuration is never started by a Work.
4. **Keep the know-how as a skill without scripts**: query syntax, project conventions and workflows.

The environment the server starts with in a Work:

- **Claude Code**: your `HOME` and `USER`, but `PATH` is only `/usr/bin:/bin`. Use an absolute `command` path, or set `PATH` in the definition's `env`.
- **Codex**: the turn's own empty `HOME`, no `USER`, and Codex's `PATH`. Set `HOME`, `PATH`, `USER`, `LOGNAME` or `TMPDIR` in the definition's `env` (`[mcp_servers.<name>.env]`). Still passes these to that server only, never to the CLI or the model's shell. Before #210 a definition that set them was skipped whole.
- Any other `env` value, a token for example, reaches the server through the CLI's own environment by name. The model's shell commands never see it, and it is never an argument, a prompt, a log line or Evidence. Keep token values out of tool arguments too: a call whose input carries a secret is refused.

Still decides every call to the server: an operation you confirmed as a read runs; another change runs on the pilot authority and is reported; anything that moves money needs your approval of that exact call. Claude Code turns name the `default` permission mode, so the CLI's own auto-mode classifier never decides in Still's place (#210: with no settings, Claude Code 2.1.287 starts a `-p` turn in auto mode). Not controlled: what the server process does with your account's rights, and its own network traffic, in work mode too.

Evidence is a local observation of a whole Work on Claude Code 2.1.287 and codex-cli 0.153.4: the Still service, the CLI, Still's decision path and a fixture server whose token comes from a fixture company CLI, against a local fixture model. No company system and no model provider were contacted.

### Work mode on a company network

On a company network, switch on **회사 모드 (work mode)** in Settings > External connections, on this Mac (#208). It cannot be switched from a conversation or through a phone link, and every change is recorded. While it is on:

- no temporary link into this Mac is opened (phone sign-in, phone input and family setup say to do it at the Mac instead), and a link that is open closes when you switch it on;
- Telegram is not used: no polling, no replies or notifications (a reply that could not be sent is not sent later), and a bot cannot be connected or paired. Mattermost and Slack pause the same way, unless you marked them company-approved (below);
- the AI's web search, public page reads, weather, browser, API calls, code runs and MCP server lookup are refused before they run, the CLI's own web search is off, signed-in sites are not refreshed, and no skill is fetched from GitHub.

The AI route you set up keeps working. Not controlled by work mode: traffic a CLI makes inside its own turn besides that web search (its model provider, its own telemetry and update checks, and what its sandbox lets a command reach; Codex runs with `--sandbox read-only`), services you connected yourself (Google connections, the AI's claude.ai connectors, your MCP servers) and family assistants on this Mac. Page reads, API calls and code runs may still reach the intranet addresses you declared (below); the browser stays refused, because a page loads resources from wherever it names. Evidence so far is unit tests with fake tunnels and transports, not an observation on a company network.

## Windows (WSL2)

Still does not run natively on Windows yet ([#33](https://github.com/Jongtae/Still/issues/33)). It runs in WSL2, the Linux environment built into Windows 10 and 11:

1. In PowerShell, run `wsl --install`, then restart Windows.
2. Open **Ubuntu** from the Start menu and create your Linux user name and password.
3. In Ubuntu, run the install command from [Install on macOS or Linux](#install-on-macos-or-linux).
4. If no browser window opens, open `http://127.0.0.1:8787` in your Windows browser. WSL2 forwards that local address to Windows.

Things that differ from macOS:

- A Codex or Claude Code subscription must be installed and logged in inside Ubuntu, not in Windows.
- Windows folders appear under `/mnt/c/`, for example `/mnt/c/Users/your-name/Documents/Still`.
- Keep the Ubuntu window open while you talk. The background login service and the embedded browser tool are macOS only for now ([#33](https://github.com/Jongtae/Still/issues/33), [#34](https://github.com/Jongtae/Still/issues/34)).

## First task

After connecting and testing your model, [pair your Telegram bot](#telegram), then ask in that Telegram chat for something you actually need: “My cat has been off her food lately. What should I try?” While answering, it notes on its own that you have a cat and tells you so, with an undo. Days later, just ask: “Find me a robot vacuum under $500.” The answer should account for cat hair without your mentioning the cat again. The browser is where you configure connections and inspect task records. For a model-free check after pairing, `/note Review the launch on Friday` followed by `/notes` saves and lists a note.

## Recommended owner dogfood task

Create two dedicated local folders (for example, `/Users/your-name/Still-dogfood/reference`
and `/Users/your-name/Still-dogfood/workspace`) and place one small Markdown or text note in the
reference folder. Put the words `Launch review` in that note. In **설정 → 파일 · 권한**
(Settings → Files · storage), enter the reference folder and workspace folder, then choose a
direct model-provider connection (OpenAI-compatible, OpenAI, Anthropic, or
Ollama) and complete its connection test; subscription engines do not run this
file-workspace path. If the selected provider is external, approve document
sharing before submitting the task. Ask in your paired Telegram chat:
`“Launch review” 자료를 요약해 “Launch notes”로 저장해줘`. Confirm that a
Markdown result appears only in the managed workspace and that the original note
is unchanged. Stop Still with Ctrl-C, run `agentos start` again, and ask
`저장된 작업공간에서 “Launch notes” 찾아줘` to reuse the saved result.

This local test uses a real folder and a real provider only when you supply the
provider credentials. Repository tests use a simulated provider and do not prove
live external-provider operation. After this bounded smoke test, record useful-result quality,
missing facts, awkward clarifications and failures; the exact sample phrase is not a claim that
all natural-language requests work. Share redacted observations, not private documents.

## Telegram

Create your own bot using Telegram’s BotFather. In `v1.1.0` and later, open **설정 → 외부 연결** (Settings → External connections) in the browser, open **Telegram 연결 설정**, and paste the bot token. **Telegram 연결** saves the token and produces a pairing link. Open **Telegram 열기** in your own Telegram account and press **Start** to pair it. Only that paired private account can submit Telegram work. Send requests in that chat; use the browser to configure connections and inspect the resulting task records. Still uses outbound polling, so no public inbound port is needed for Telegram. Use a dedicated bot without an existing webhook.

When you connect your own bot, Still gives it the default assistant's profile once: the name **Still**, its picture, and a short introduction in English, Korean, Japanese and Chinese. If you rename the bot or change its picture in BotFather afterwards, Still leaves your choice alone. Family instances keep their own names. The chat background is a Telegram app setting that a bot cannot change.

The computer must remain running and awake for remote requests to be processed. Installing Still does not start it at login: on macOS, register the background login service once with `agentos service install` so Still starts again after a restart without a terminal; see [Background service (macOS)](#background-service-macos) for exactly what that does and does not cover. Without that service, keep the terminal open; Ctrl-C stops Still.

## Mattermost

You can talk to Still in a direct message with a bot on your Mattermost server, typically a company one (#213). Still connects out to the server's API and checks the direct message every few seconds. Nothing on this Mac is opened to the outside, and no callback is received.

1. In Mattermost, an admin creates a **bot account** (Integrations > Bot Accounts) and gives you its access token.
2. In Settings > External connections > **Mattermost** on this Mac, enter the server address (`https://…`), the bot token and your own Mattermost username. The token is kept in Still's secret store and never shown again. If the server is on a private address, first add its host under **사내 주소 (intranet addresses)**.
3. The bot says hello in your direct message. Write there as you would in Telegram.

- Only your posts in that direct message are read. Anyone else who messages the bot, or a channel the bot is added to, is never read or answered.
- An edited post is never a new request.
- Still marks your message with 👀 while it works and shows typing. There are no live drafts or buttons.
- You approve or decline what Still proposes (a settings change, a calendar change, remembering something) by replying, or with a reaction such as 👍 on the message that showed it. Still reads that answer the same way as on Telegram.
- **Payments and other exact action approvals are never given in Mattermost** (your decision on #213). The prompt tells you to approve or decline in Still on this Mac.
- A reply that never reached the server is sent once it can be. A reply that may have arrived is never sent twice.
- In work mode, Mattermost is used only when you switched on **회사 모드에서도 사용 (use in work mode too)**. Do that only for a company-approved server. Otherwise it pauses with Telegram, and nothing queued while it was paused is sent later.

Evidence is unit tests with a fixture server, plus a local observation against a test Mattermost (`mattermost/mattermost-preview`, Mattermost 11, on this Mac). It covered connecting, a direct message becoming Work and its reply, the 👀 reaction, and an edit and another user's message ignored. It also covered a settings proposal applied by the owner's 👍 reaction, and an action approval that stayed unanswered there. There is no observation against a company server yet.

## Slack

You can talk to Still in a direct message with your own Slack app's bot (#214). Still connects out to Slack over **Socket Mode**, an outbound WebSocket. There is no public request URL, no tunnel, and nothing on this Mac is opened to the outside. Still uses the official `slack_sdk`.

1. At [api.slack.com/apps](https://api.slack.com/apps) choose **Create New App → From a manifest**, pick your workspace and paste the manifest that Settings > External connections > **Slack** shows (**Slack 앱 만들기**). It enables Agent Experience (`agent_view`) and asks for `assistant:write`, `chat:write`, `im:history`, `im:write`, `reactions:read` and `reactions:write`. It subscribes to `message.im` and `reaction_added`, and turns on interactivity and Socket Mode.
2. Under **OAuth & Permissions** on the left, choose **Install to Workspace** and allow it; the **Bot User OAuth Token** (`xoxb-…`) then appears there. Under **Basic Information → App-Level Tokens**, create an **app token** (`xapp-…`) with the `connections:write` scope.
3. In Settings on this Mac, enter both tokens and your own **member ID** (Slack profile → ⋮ → Copy member ID). Both tokens are kept in Still's secret store and never shown again. The bot says hello in your direct message.

For an existing app, re-paste the updated manifest in Slack, then **reinstall the app** under OAuth & Permissions. If the Bot User OAuth Token shown afterwards differs from the one in Still, enter the new one in Settings. Switching on Agent Experience alone does not grant the new scope.

- Only your messages in that direct message, in that workspace, are read. Anyone else who messages the bot, a channel, or another workspace is never read or answered.
- An edited or deleted message is never a new request. Slack may deliver an event twice; Still takes it once.
- Still marks your message with 👀 while it works. For a message written in a thread, the reply stays in that thread and Agent Experience can show the observed, redacted wait status until the answer. Plain DMs keep their existing behavior; replies are not edited as live drafts.
- A proposal to approve (a settings change, a schedule, remembering something, or an action approval) comes with **approve / decline buttons**. Only your press settles it, at most once, and the buttons are then removed. You can also answer by replying, or with a reaction such as 👍, read the same way as on Telegram.
- **Payments and other exact action approvals can be given in Slack** (your decision on #214), by their button or a reply. Document-sharing and context approvals and browser logins are still answered in Still on this Mac.
- A reply that never reached Slack is sent once it can be. A reply that may have arrived is never sent twice.
- In work mode, Slack is used only when you switched on **회사 모드에서도 사용 (use in work mode too)**. Do that only for a company-approved workspace. Otherwise its connection closes, and nothing queued while it was paused is sent later.

Evidence is unit tests with payloads shaped like Slack's Events API and Block Kit button presses, and the SDK's own request path against a local HTTP server. A live observation in the owner's test workspace (2026-10-25) covered connecting, a direct message answered, a settings change applied and an action approval answered by their buttons, a 👍 on an answer, the connection closing in work mode and reconnecting, and an edit that made no new request. There is no observation in a company workspace.

## Restart and update

```sh
agentos start
# Stop the process before upgrading:
brew update
brew upgrade jongtae/still/agentos
agentos start
# View only credential-free setup and recovery state:
agentos guide
```

If Still came from the predecessor's Homebrew tap (`brew install jongtae/agentos/agentos`, versions `v1.0.4`–`v1.4.1`), `brew upgrade` does not move it to this repository's tap. Switch once, after the first Still release: stop Still, run `brew uninstall agentos` and `brew untap jongtae/agentos`, then follow the Homebrew install above. Your data in `~/.local/share/agentos` is kept. The formula's `version_scheme` makes Still's `0.x` versions count as newer than those `1.x` builds.

If you registered the background login service, run `agentos service upgrade` after upgrading or replacing the executable rather than relying on the foreground command.

Data persists in `~/.local/share/agentos`; uninstalling keeps it unless you ask to delete it ([Uninstall](#uninstall)). Back up the entire data directory while Still is stopped. This directory contains your private conversations and credentials; credentials have filesystem permissions, not application-level encryption.

If the browser does not open, use the link in `~/.local/share/agentos/private/setup-link.txt` locally. Do not share that file before setup. After setup, open `http://127.0.0.1:8787`; log in only if you chose a password. An occupied port can be changed using `agentos start --port 8788`. After an unexpected stop, Still marks in-progress work as interrupted and an in-flight Telegram send as unknown; it never silently repeats either. Review the web record and submit a new request if needed. `agentos guide` shows counts and next steps only, never task text or credentials.

## Uninstall

```sh
agentos uninstall
```

It lists what it will remove, asks once, then removes every Still background service (yours and family instances) and the program, using uv or Homebrew, whichever installed it. Your data stays in `~/.local/share/agentos` (and `~/.local/share/agentos-instances`) unless you type `delete` when asked; deleting it also deletes the macOS Keychain keys that encrypt its credentials, and cannot be undone. Stop any `agentos start` still running in a terminal first. Without a terminal, use `agentos uninstall --yes` (keeps data) or `--yes --delete-data`.

Deleting local files does not reach other services. Delete the Telegram bot with `/deletebot` in @BotFather, and remove Still access to Google at [myaccount.google.com/permissions](https://myaccount.google.com/permissions). Codex, Claude Code and uv are separate tools and stay installed.

## Background service (macOS)

`agentos start` runs in the foreground, and installing Still (Homebrew, the installer or a
checkout) registers nothing to start at login. On macOS the same executable can instead be
registered as a per-user launchd login service, so Telegram and web requests are handled
without an interactive terminal session and Still starts again after a restart. First setup
offers this once (step 4 above); the commands below do the same at any time:

```sh
agentos service install    # register and start the login service
agentos service status     # report the observed launchd state and health
agentos service restart
agentos service stop       # stop and persistently disable it
agentos service uninstall  # remove the registration; owner data is retained
agentos service upgrade    # replace an existing definition, rolling back on failure
```

After a Homebrew install, `brew info jongtae/still/agentos` prints the exact command:
`$(brew --prefix)/opt/agentos/bin/agentos service install --cli-path $(brew --prefix)/opt/agentos/bin/agentos`.
The `opt` path stays valid across `brew upgrade`. Still uses its own launchd job
(`com.personal-agentos`), not `brew services`, so there is one registration with health-checked
receipts, rollback on `upgrade` and removal by `agentos uninstall`.

From a source checkout the same actions are available as
`python3 -m personal_agent.quickstart service <action>`. `install` and `upgrade` must record
an absolute path to the executable launchd will run. They record the `agentos` program you
ran, so another `agentos` earlier on `PATH` (for example a pyenv shim) is not picked up instead;
`v0.1.0` resolved `PATH` first, which is why the Homebrew command above passes `--cli-path`.
When started as `python3 -m`, they resolve `agentos` from `PATH` and then from the Homebrew
prefix, so from a source checkout either install the console script
(`pip install -e '.[mcp-host]'`) or pass `--cli-path /full/path/to/agentos` explicitly. Use `--data` only
to override the data directory: with no `--data`, the other actions report the directory
recorded in the installed service definition.

Each action prints the receipt it actually observed and exits non-zero when the operation
did not succeed. A refused, unhealthy or unreadable service is reported as a failure with a
`next_action`, never as a success: `background_available` is reported only when a running
process also passed the loopback health check. `uninstall` removes only the service
registration and never deletes `~/.local/share/agentos`. The service definition is bound to
loopback and sets `RunAtLoad`/`KeepAlive`, so it is designed to start again at login.

What this does **not** yet cover, stated exactly:

- **macOS only.** The lifecycle is launchd-specific. There is no systemd equivalent here.
- **Released in Still `v0.1.0`.** `agentos service` first shipped in the predecessor's
  `v1.1.0` (2026-09-23, its private repository and separate tap). Still's Homebrew formula
  lives in this repository as `Formula/agentos.rb`; see [the release procedure](release.en.md).
  Homebrew does not start services at install time, so the registration above stays one
  explicit command.
- **Not covered by automated tests of real launchd.** Repository CI runs on Linux and cannot
  execute launchd. The automated evidence for these commands is injected-runner tests that
  substitute `launchctl`. The v1.3.0 Homebrew upgrade and foreground smoke were observed
  on the owner's Mac ([release manifest](release-manifest.json)); a real login service
  surviving a machine restart and an end-to-end Telegram result with no terminal open
  remain owner operating validation.

## An agent for a family member (macOS)

Each family member gets their own Still instance on your Mac. An instance has its own memory, folders, approvals and work history, and its own Telegram bot paired to that person. Nothing is shared with your own instance.

**The simplest way is to ask your assistant**, for example "아내 비서 만들어줘". It shows what it will create; tap 👍 (or 적용), and the setup link arrives in the same chat for you to forward. It needs your own Still Telegram bot to be connected, and its **Bot Management Mode** switched on once in the BotFather Mini App. The same setup is also one command:

```sh
agentos family add spouse --display-name "아내 비서"
```

Either way:
- creates and starts the instance;
- opens a temporary HTTPS link through your installed `ngrok` (about 30 minutes);
- sends the link to you on Telegram so you can forward it.

The family member opens the link on their phone:
1. They install Telegram.
2. They tap **텔레그램에서 봇 만들기**. Telegram shows a pre-filled "create bot" screen (Managed Bots); they confirm, and they own the new bot.
3. Your bot fetches the new bot's token, limits the bot to its owner, and hands the token to the instance on this Mac.
4. They tap **비서와 대화 시작**.

The link then closes by itself. Through the link, only the setup page and its status answer, and only with the link's one-time code. Once an instance was set up this way, no tunnel reaches anything else on it: a family instance is not meant to be served through `--public-tunnel-host`. The page tells the family member that the agent runs on your Mac with your AI subscription.

The instance runs on **your** AI route. `family add` copies your main AI and judgment AI settings, with only the keys or tokens those routes use. It copies nothing else: not your Telegram bot, folders, memory or other connections. An instance that already has a route keeps it. If you later change or rotate your AI key, the family instance keeps the old one until you set its route again.

The manual commands below remain available.

```sh
agentos service install --instance spouse --port 8797   # data: ~/.local/share/agentos-instances/spouse
agentos service status    --instance spouse
agentos service stop      --instance spouse             # stop and persistently disable
agentos service start     --instance spouse             # re-enable and start a stopped instance
agentos service uninstall --instance spouse             # remove the registration; data is retained
```

To remove an instance completely, run `stop` before `uninstall`. Still has no command that deletes an instance's data directory; after stopping it, delete `~/.local/share/agentos-instances/spouse` yourself, which cannot be undone.

The service definition sets `KeepAlive`, so launchd restarts an instance whose process exits. Killing the process is therefore not a way to stop it; use `service stop`. Instances started by `family add` are registered the same way and can be managed with these commands.

- **Name and port.** The instance name uses lowercase letters, digits and hyphens. The port must be free and must not be 8787. The next port (8798 here) is used for its local handoff.
- **Data directory.** An instance never uses your own data directory or `AGENTOS_DATA`.
- **Setup.** Open `http://127.0.0.1:8797/` and use the setup link in that instance's `private/setup-link.txt`.
  - Codex uses this Mac's Codex login. Claude Code needs its setup token entered once in that instance.
  - Pair the family member's own Telegram bot the same way as in [Telegram](#telegram).
- **Google Calendar and Gmail.** An instance's callbacks use its own port, for example `http://localhost:8797/oauth/calendar/callback` and `.../oauth/gmail/callback`. Add those as authorised redirect URIs in your Google OAuth client.
- **Not yet covered.** The same "not covered" notes as [Background service](#background-service-macos) apply: tests substitute `launchctl`, and a real login service is owner operating validation.

## Google Drive, Gmail and Calendar

**Default: your AI's own Google connection.** Most AIs you can connect already offer a
Google connection of their own. Claude Code has claude.ai connectors, and Codex has ChatGPT
connector plugins. Letting Still work use that connection is planned separately; Still
keeps what was read and why, not the connection itself.

**Optional: your own Google client.** If you want Still itself to hold the connection (for
example because your AI has no Google connection), add one Google OAuth client of your own.
One client covers all three services:

The same steps, with direct links, are in **설정 → 외부 연결 → 자체 Google client → client 만드는 방법**:

1. [Create a project](https://console.cloud.google.com/projectcreate) in Google Cloud.
2. [Enable the Drive, Gmail and Calendar APIs](https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com,gmail.googleapis.com,calendar-json.googleapis.com) in that project.
3. [Get started with Google Auth Platform](https://console.cloud.google.com/auth/overview): enter an app name and your email, and choose *External* as the audience. If *OAuth Overview* already shows, this step is done.
4. In [Branding](https://console.cloud.google.com/auth/branding), enter a home page and a privacy policy URL **that you manage** under *App domain*, add their domain under *Authorized domains*, and save. Without these, *Publish app* stays disabled (Google reports "Missing domain"). For an app only you use, Google does not review these values; to offer the app to others you need a domain you own and a real, published policy page. Do not upload a logo; a logo requires verification.
5. On [Audience](https://console.cloud.google.com/auth/audience), press *Publish app*, then *Confirm*. In *Testing*, Google expires the connection every 7 days.
6. [Create a client](https://console.cloud.google.com/auth/clients/create) of type **Desktop app**. Tick *Use this client for an AI-powered agent*, then download its JSON right away; Google shows the secret only at creation.
7. In Still, open **설정 → 외부 연결 → 자체 Google client → client 넣기**, choose the downloaded file (or paste its contents) and save. Alternatively, point `AGENTOS_GOOGLE_CLIENT_FILE` at the file; an environment file wins over the saved one.

Then, on this computer, press **연결** on each service in the same section and approve Google's consent screen. Google consent is done on the desktop; a phone cannot reach this computer's sign-in callback.

| Service | Address | Access |
|---|---|---|
| Google Drive | `http://127.0.0.1:8787/google-drive-connect` | read the whole Drive (`drive.readonly`) |
| Gmail | `http://127.0.0.1:8787/google-gmail` | search and read mail (`gmail.readonly`) |
| Google Calendar | `http://127.0.0.1:8787/google-calendar?grant=read` | read events from every calendar you show in Google Calendar (`calendar.events.readonly`, `calendar.calendarlist.readonly`) |
| Calendar changes | `http://127.0.0.1:8787/google-calendar?grant=write` | create, change, move or delete events; no attendee is emailed (`calendar.events`) |

Google shows "Google hasn't verified this app" because the client is your own unverified
app. Choose *Advanced → continue*. You are its only user, so Google's verification and its
100-user limit for unverified apps do not apply to you.

After connecting Drive, your AI can search your Drive and read a file. Google Docs, Sheets
and Slides come back as text; PDF, DOCX, XLSX, TXT and MD are extracted on this computer.
The connections keep working after the one-hour Google access token expires, because
Still renews it with the stored refresh token, a couple of minutes before it expires.
If Google cannot be reached during a renewal (no network, a timeout, a Google-side error),
the stored connection is kept and the next request renews it; you are asked to connect
again only when Google refuses the grant itself (for example after you removed Still's
access in your Google account).

What this does and does not do:

- **Read-only Drive and Gmail.** Still cannot change Drive files or send, delete or label
  mail. With the calendar write connection, your AI makes a calendar change itself, and its
  answer and the Work's information-use record say what it changed. Nobody you add to an
  event is emailed.
- **Everything stays on this computer.** The client is kept in the Still secret store.
  Tokens are stored encrypted, with the key in the macOS Keychain. No Still server sits
  between you and Google, and no client ships with Still. On Linux, set
  `AGENTOS_GOOGLE_OAUTH_KEY` to a Fernet key you keep outside the data folder.
- **Replacing the client** applies after Still restarts, and each service must then be
  connected again.
- **Removing the client**: once every service that used it is disconnected, **client 지우기**
  in the same section deletes the saved client and stops offering those connections. A client
  set by `AGENTOS_GOOGLE_CLIENT_FILE` is removed by removing that setting.
- **A per-service client still wins.** If you configured Gmail or Calendar with a separate
  client (sections below), that configuration is used for that service.

## Google Calendar (source checkout)

Still can read your calendar and draft changes to it, so `내일 일정 뭐 있어?` is
answered and `내일 3시에 회의 잡아줘` produces an exact preview for you to approve. It is
off unless you configure it, and configuring it connects nothing on its own.

**Read and write are two separate connections.** A read grant never becomes a write
grant: they are separate Google authorizations, separate credentials and separate
connector records, and you authorize each one deliberately.

Create a Google Cloud OAuth **web** client, add
`http://localhost:8787/oauth/calendar/callback` as an authorised redirect URI, download
the client JSON, then write the owner-only credential file:

```sh
agentos calendar-config \
  --oauth-client-json ~/Downloads/client_secret_XXXX.json \
  --secret-file /Users/your-name/.agentos-secrets/calendar.json
AGENTOS_CALENDAR_LOCAL_ONLY=1 \
AGENTOS_CALENDAR_SECRET_FILE=/Users/your-name/.agentos-secrets/calendar.json \
  agentos start
```

`calendar-config` behaves exactly like `gmail-config`: the encryption key is generated
locally, the file is `0600` and owned by you, a relative `--secret-file` is refused, and
an existing file is never overwritten. The client secret never becomes a command-line
argument, an environment value or log output.

Then open Still on its local address and connect each grant from the settings page —
`/google-calendar?grant=read` and `/google-calendar?grant=write`. Both routes require
your owner session and refuse a tunnel host.

**What the model can and cannot do (#432).** The model reads your calendar with
`calendar_query` or the Calendar API. With the write connection it creates, changes, moves
and deletes events itself, through the Google Calendar API (`api_request`, slot
`google-calendar`). It can use any field of Google's event format, including all-day
events, recurrence and attendees, so a case Still never planned for is not a dead end.
When Google refuses a change, the model gets Google's own reason back, and a refused change
is recorded as changing nothing, so the model can correct the request and send it again.
Still keeps the token, which a change spends: only the write grant can change anything.
Still also keeps the paths: event insert, quickAdd, patch, update, delete and move. It
never shares a calendar, creates or deletes calendars, imports events, or registers a
watch. Every change is sent with `sendUpdates=none`, so no invitation or update email
reaches anyone. A change applies without a separate approval, as for the other connected
services; only payments need your approval for each action.

Reading your calendar puts event titles in the model's context. A later public lookup in
the same turn (web search, public page read, weather) is not refused for that reason: as
for any lookup, stored secrets are removed from the query before it is sent (#826), and
the task's information-use record shows that the calendar was read.

What this does **not** yet cover, stated exactly:

- **This per-service path has not been observed live.** Every test uses an injected
  transport and a fixture token endpoint. A real OAuth consent, a real event created in
  a real calendar, and the behaviour of a revoked grant through this path remain owner
  validation. The owner's first real setup of the [own Google client](#google-drive-gmail-and-calendar)
  (2026-10-09, #1209) is a pilot observation of that other path, not release validation.
- **Released from `v1.1.0` (2026-09-23).** `v1.0.4` and earlier Homebrew builds do not
  include it; see [the release procedure](release.en.md).
- **The natural-language create path still asks a question.** `내일 3시에 회의 잡아줘`
  parks for a connection and then asks for the missing detail rather than producing a
  draft on its own; ask for the calendar explicitly, or read first and draft from what
  you see.

## Gmail (source checkout)

Still can read and search your Gmail so a request such as
`메일에서 예산 관련 내용 찾아줘` is answered instead of refused. It is off unless you
configure it, and configuring it connects nothing on its own.

Create a Google Cloud OAuth **web** client, add
`http://localhost:8787/oauth/gmail/callback` as an authorised redirect URI, download the
client JSON, then write the owner-only credential file:

```sh
agentos gmail-config \
  --oauth-client-json ~/Downloads/client_secret_XXXX.json \
  --secret-file /Users/your-name/.agentos-secrets/gmail.json
AGENTOS_GMAIL_LOCAL_ONLY=1 \
AGENTOS_GMAIL_SECRET_FILE=/Users/your-name/.agentos-secrets/gmail.json \
  agentos start
```

`gmail-config` is the Gmail counterpart of `drive-config`: it generates the token-store
encryption key locally, writes a `0600` file owned by you, refuses a relative
`--secret-file`, and refuses to overwrite an existing one. The client secret and the
encryption key are never command-line arguments, environment values or log output. The
file must stay outside `~/.local/share/agentos`; otherwise startup fails closed. If you
prefer not to keep a file, the equivalent `AGENTOS_GMAIL_CLIENT_ID`,
`AGENTOS_GMAIL_CLIENT_SECRET` and `AGENTOS_GMAIL_ENCRYPTION_KEY` environment values are
still read, but the file is the supported boundary.

Then connect, from this computer's browser: **설정 → 외부 연결 → Google 연결 → 연결하기**,
or open `http://127.0.0.1:8787/google-gmail` in the same browser where you already
use Still. Google asks you to approve read-only Gmail
access; Still records the scope Google actually granted. If you asked for something over
Telegram that needs Gmail, the reply names the connection you need and carries this same
address, and the original request is resumed exactly once after you connect.

What this does **not** claim, stated exactly:

- **This per-service path has not been observed live.** The automated evidence is a
  fixture token endpoint and a fixture Gmail transport inside the repository suite. A real
  Google consent screen, a real token exchange and a real message list through this path
  remain owner operating validation. The own Google client path above was first set up for
  real by the owner on 2026-10-09 (#1209); that is a pilot observation, not release
  validation.
- **Read-only.** The only scope requested is `gmail.readonly`, and the connection is
  recorded only if Google grants exactly that. Still cannot send, reply to, delete,
  label or archive mail, and nothing here authorises a Calendar, Drive or send scope.
- **Released from `v1.1.0` (2026-09-23).** `brew install` / `brew upgrade
  jongtae/agentos/agentos` at `v1.1.0` or later provides `agentos gmail-config` and the
  Gmail route; `v1.0.4` and earlier do not.
- **Connecting is a separate decision from configuring.** Writing the credential file only
  lets this installation *offer* Gmail. It issues no grant, and the connector stays
  disconnected until you complete the authorisation yourself. The start route requires
  your local Still session and refuses a tunnel host, so Gmail cannot be connected from
  a phone over the mobile pairing link.
- **Local install is not local-only processing.** Gmail metadata read this way is handled
  locally, but if you have connected an external model provider, answering a mail question
  can send that content to the provider you chose.

## Remote host / source installation

Python 3.12+ on macOS or Linux is required for source execution. Check with `python3 --version`. The Python that ships with macOS Command Line Tools is 3.9 and cannot run Still: `pip install -e` refuses it, and running the source fails with `ModuleNotFoundError: No module named 'cryptography'`. Install Python 3.12 or newer (Homebrew `python@3.12`, or the python.org installer), then install from a virtual environment:

```sh
python3 -m venv ~/.venvs/agentos
~/.venvs/agentos/bin/pip install -e '.[mcp-host]'   # run inside the checkout
~/.venvs/agentos/bin/agentos start --no-browser
```

The `mcp-host` extra adds the MCP stdio client (`mcp`) and the Claude Agent SDK (`claude-agent-sdk`). They let Still use MCP servers and the Claude Code CLI that you installed yourself. The base install omits them. Developers working in a checkout should follow the locked setup in [CONTRIBUTING](../.github/CONTRIBUTING.md#setting-up) instead.

On a remote host, keep the default loopback binding and connect through SSH forwarding (`ssh -L 8787:127.0.0.1:8787 your-host`). Open the setup link through that tunnel. Public managed hosting, TLS termination, mobile apps and Kubernetes packaging are future deployment work.

## Preview scope

Implemented paths include single owner, password login, model adapters, persistent chat/notes, queued requests, private Telegram pairing/deduplication, connected TXT/Markdown/PDF/DOCX/XLSX folders with sources, explicit external-model document approval, Docker Compose and backup/restore scripts. Each path's actual operating evidence remains scoped to its recorded revision/configuration. Interrupted model work and uncertain Telegram delivery are shown without automatic replay.

Arbitrary shell, native desktop/mobile apps, managed unattended installation, multi-user hosting and the complete AgentPackage ecosystem are not this preview. Calendar/Drive-related design or mock-validated code elsewhere in the repository is not proof of a currently configured live integration. See the [roadmap](roadmap.md) for exact historical and planned states.

## Advanced: local manifest plugins

Declaration-only plugins can add only the bounded host actions Still already
supports; they never execute third-party code. They are the legacy declaration path,
not proof of the planned v0.1 AgentPackage installer or arbitrary binary support.
Install a reviewed manifest and manage it from the same local data directory:

```sh
agentos plugins install /path/to/manifest.json
agentos plugins list
agentos plugins disable example-plugin
agentos plugins remove example-plugin
```

Tests use simulated provider and Telegram responses, plus a real local HTTP server for setup/chat/authentication. Live provider and Telegram testing requires your credentials and explicit operating scope.

Source and Homebrew formula: [Jongtae/Still](https://github.com/Jongtae/Still) (the formula is `Formula/agentos.rb`, written at each release from `deploy/homebrew/agentos.rb.template`).

## Historical general tool runtime (0.2.0)

The following describes that release's recorded behavior and limits, not a new live
acceptance or a promise that its limits match every later source revision.

Normal messages use a shared native tool-call loop. The model selects from web search,
weather, connected-folder search/read, personal notes, and built-in researcher/reviewer
agents. No keyword rule forces weather or search. OpenRouter's free router chooses a
model for each request; that model is kept for the remainder of the tool loop.
Free-provider availability and quotas can still interrupt a request; failures are shown.

Under **연결 설정 → 내 파일 연결**, register specific folders on the Still host.
Connected UTF-8 TXT/Markdown, PDF, DOCX, and XLSX files up to 1 MB are supported
(16,000 characters per read). For cloud models, document search and excerpts are
blocked until the owner approves the current model-and-folder scope; the approval
is cleared when either changes. Mobile clients access the host's connected folders.

Try these in the same conversation:
- “Kubernetes 공식 문서를 검색해 줘.”
- “내 파일에서 Aurora 출시 계획을 찾아서 읽어 줘.”
- “그 내용을 검토 에이전트에게 전달해 줘.”
- “이제 도구 없이 안녕이라고만 답해 줘.”
- “출시 검토가 필요하다고 메모해 줘.”

Specialists run separate conversations with the configured model provider and read-only
tools. They are not external Codex/Claude Code processes. Recursive delegation and
shell commands are unavailable. **최근 도구 실행 기록** shows actual tool execution.

`python3 -m unittest discover -s tests -q` checks local contracts and errors.
`scripts/verify_general_agent.py` runs live multi-topic acceptance with the configured
provider in a temporary store; `--installed` verifies the Homebrew installation.
It creates a synthetic local document and never changes personal chat or notes.
Run a live verifier only after explicitly choosing provider/data scope and budget;
its historical result is not the new USE-01 24-case quality gate.
