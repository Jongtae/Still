# Run, maintain and remove Still

[Quick start](../QUICKSTART.md) · [Connections](connections.en.md) · [Company networks](workplace.en.md) · [Platforms](platforms.en.md)

This guide covers the installed application's lifecycle. Development setup belongs in [CONTRIBUTING](../../.github/CONTRIBUTING.md#setting-up); release publication belongs in the [release procedure](../release.en.md). Check [product status](../product-status.en.md) before treating a source-only change as part of an installed release.

## Restart and update

```sh
agentos start
agentos guide
```

`agentos guide` reports setup and recovery counts and next steps, not task text or credentials. Ctrl-C stops a foreground process. After an unexpected stop, in-progress work is marked interrupted and an uncertain Telegram send remains unknown; neither is silently replayed. Inspect the record before deciding whether a new request is appropriate.

Stop Still before upgrading. For an installer-managed installation, rerun the installer from [Quick start](../QUICKSTART.md). For Homebrew:

```sh
brew update
brew upgrade jongtae/still/agentos
agentos start
```

With a registered macOS login service, use `agentos service upgrade` after replacing the executable instead of relying on a foreground start. Keep the computer awake for remote requests and scheduled work; a running service cannot execute while its host is unavailable.

### Migrating the predecessor Homebrew tap

An installation from `jongtae/agentos/agentos` does not move taps through `brew upgrade`. Stop Still, run `brew uninstall agentos` and `brew untap jongtae/agentos`, then install `jongtae/still/agentos` as described in Quick start. The data directory is retained. The formula's `version_scheme` makes Still's `0.x` releases newer than the predecessor's `1.x` packages for upgrade ordering. Those are different release series, not evidence that a current feature first shipped in Still 1.x.

## Data, backup and recovery

Owner data is normally in `~/.local/share/agentos`; `AGENTOS_DATA` selects a different directory. Old named installations may have data under `~/.local/share/agentos-instances`.

Back up a stopped installation and protect the backup as private material. The directory contains conversations and connection material. Encryption differs by store and platform; a backup is not anonymized, and copying files alone does not reproduce a platform keyring. For portable owner-state export and restore, use the repository's [backup](../../scripts/agentos-backup.py) and [restore](../../scripts/agentos-restore.py) tools and their `--help`. Portable owner state deliberately excludes credentials, sign-in sessions and folder grants; reconnect them rather than assuming authority transfers with an export.

If the browser did not open, read `private/setup-link.txt` locally. The setup link is not a support attachment. Once setup is complete, use `http://127.0.0.1:8787`; a password is required only when you configured one. For a port conflict, start with `agentos start --port 8788`.

Share redacted observations when reporting a problem, not private documents, tokens, raw model context or complete tool payloads. A failed connection check must not be described as a working connection, and a request whose external effect is unknown must not be replayed just to obtain a cleaner status.

## Background service (macOS)

Still uses a per-user launchd job, `com.personal-agentos`, not `brew services`. Neither Homebrew installation nor a source checkout registers it automatically. First setup can offer registration; these commands are also available later:

```sh
agentos service install    # register and start
agentos service status     # observed launchd state and loopback health
agentos service restart
agentos service stop       # stop and persistently disable
agentos service uninstall  # remove registration, keep owner data
agentos service upgrade    # replace a definition, with rollback on failure
```

After a Homebrew installation, `brew info jongtae/still/agentos` prints the stable executable path:

```sh
$(brew --prefix)/opt/agentos/bin/agentos service install --cli-path $(brew --prefix)/opt/agentos/bin/agentos
```

`install` and `upgrade` record the absolute executable path, so a different `agentos` earlier on `PATH` is not selected accidentally. When launched with `python3 -m personal_agent.quickstart service <action>`, the command instead resolves the console script from `PATH` and then the Homebrew prefix; pass `--cli-path /full/path/to/agentos` explicitly for an unusual source installation. `--data` overrides the data directory. Without it, status and other lifecycle actions use the directory in the installed definition.

The first-run offer is limited to the normal loopback address and compatible environment settings; a login service does not inherit an arbitrary terminal environment. A refused, unreadable or unhealthy service returns a failure and a next action. `background_available` requires both a running process and a successful loopback health check. The definition uses `RunAtLoad` and `KeepAlive`; stopping or uninstalling the service does not delete owner data.

This lifecycle is macOS-specific. Linux and WSL2 do not have an equivalent managed service here. Tests with an injected `launchctl` runner establish lifecycle handling, not that a real service survived a machine restart. Release package checks and foreground operation likewise do not establish unattended end-to-end use. Consult the exact observations in the [release manifest](../release-manifest.json).

## Uninstall

Stop any foreground `agentos start` process, then run:

```sh
agentos uninstall
```

Still lists the planned removal, asks for confirmation, removes its background services (including legacy named registrations), and removes the application through the installer that owns it, uv or Homebrew. Data stays unless you explicitly choose to delete it. Typing `delete` removes the data and associated keys as described by the command; that deletion cannot be undone.

For non-interactive use, `agentos uninstall --yes` retains data. `agentos uninstall --yes --delete-data` also deletes it; use that form only when deletion is intended.

Removing local files does not revoke access at other services. Delete an unused bot through `/deletebot` in Telegram's BotFather, and review Google access in [your account permissions](https://myaccount.google.com/permissions). Codex, Claude Code and uv are separate tools and remain installed.

## Remote host / source installation

Use Python 3.12 or newer. The macOS Command Line Tools Python 3.9 is not sufficient. From the checkout:

```sh
python3 --version
python3 -m venv ~/.venvs/agentos
~/.venvs/agentos/bin/pip install -e '.[mcp-host]'
~/.venvs/agentos/bin/agentos start --no-browser
```

For the newer browser implementation on a glibc Linux desktop, use `'.[mcp-host,linux-browser]'` instead; this is a source-only addition relative to v0.5.0. The `mcp-host` extra adds the MCP stdio client and Claude Agent SDK; the base installation omits them. Contributors should use the locked environment in CONTRIBUTING rather than this end-user source-install example.

Keep a remote service on loopback and use SSH forwarding:

```sh
ssh -L 8787:127.0.0.1:8787 your-host
```

Open the setup link through the forwarded port. This is not a public-hosting or multi-user deployment recipe. A remote server without a desktop display does not gain an interactive browser merely because the service starts.

## Recommended owner dogfood task

This retained, bounded file-workspace check uses a direct model connection; do not infer the capabilities of every subscription route from it. Create dedicated reference and result folders and put a small text or Markdown note containing `Launch review` in the reference folder. Configure those folders in Settings → Files and permissions. Test a direct OpenAI-compatible, OpenAI, Anthropic or Ollama connection. The configured model reads the documents in those folders without a separate sharing approval (#820); with an external model, the excerpts it reads go to that model.

Ask in the paired conversation:

> “Launch review” 자료를 요약해 “Launch notes”로 저장해줘.

Check that the Markdown result is in the intended result folder and the original is unchanged. Restart Still and ask:

> 저장된 작업공간에서 “Launch notes” 찾아줘.

Check the saved result rather than treating the assistant's words as proof. This is a local check with a real model only when you configure one. Fixture tests do not establish model quality; exact example wording is not a guarantee that every paraphrase succeeds. No purchase, booking, payment or new external account is part of the check.

## Advanced: local manifest plugins

The legacy declaration-only plugin path exposes bounded host actions already supported by Still; it does not execute arbitrary third-party code and is not the planned AgentPackage marketplace.

```sh
agentos plugins install /path/to/manifest.json
agentos plugins list
agentos plugins disable example-plugin
agentos plugins remove example-plugin
```

Review the manifest before installation. Installation is not permission to perform every declared action. For capability and authority semantics use the [architecture](../personal-agentos-architecture.en.md) and [connection and delegation contract](../connection-delegation-contract.en.md).
