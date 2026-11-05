# Platforms and browser support

[Quick start](../QUICKSTART.md) · [Operations](operations.en.md) · [Product status](../product-status.en.md)

The application service, subscription CLI, browser tool and background service have different platform requirements. A platform on the README badge does not mean every capability is available there.

| Component | macOS | Linux | Windows / WSL2 |
| --- | --- | --- | --- |
| Still service | Supported installation path | Supported installation path | Run inside WSL2; native Windows is separate work |
| Subscription CLI | Install and sign in locally | Install and sign in in Linux; route qualification is separate | Install and sign in inside the WSL2 distribution |
| Browser | System WebKit | Newer main source: Chromium on a glibc desktop; not in v0.5.0 | WSLg display use has not been observed |
| Managed background service | Explicit launchd registration | No equivalent managed service documented | No equivalent managed service documented |

## Browser on a Linux desktop

This section describes the source implementation merged under [#34](https://github.com/Jongtae/Still/issues/34), checked at `6edc3380b613ab0c2897ba7a913ab658150a68f4` on 2026-11-05. It is **not** a claim that v0.5.0 contains the Linux browser. See the [source installation](operations.en.md#remote-host--source-installation) instructions for the `linux-browser` extra.

On a glibc Linux desktop, x86_64 or arm64, the browser uses a pinned Google Chrome for Testing build driven through Playwright. The reviewed source pins 156.0.8078.4; the code, not this prose, owns future version changes. The first browser request downloads roughly 200 MB into `~/.local/share/agentos/tools/chromium`. The archive must match its pinned SHA-256 and the executable digest is checked again before each start. No system-wide browser is installed, and `playwright install` is not run. Alpine and other musl environments do not have this browser tool.

Chromium's renderer sandbox is mandatory. On an Ubuntu configuration that restricts the required user namespaces, local Settings shows the command for a narrowly scoped AppArmor profile at `/etc/apparmor.d/still-chromium`. Review the command before running it with administrator permission. A newer browser path requires renewed setup; there is no `--no-sandbox` workaround. When system libraries are missing, Settings may show Playwright's `install-deps chromium` command, which installs libraries through the system package manager rather than downloading the browser again.

The browser needs `DISPLAY`. A Wayland desktop can provide X11 through XWayland; a headless service is not sufficient. A window is displayed for owner sign-in. Sessions are encrypted in the private data area with a key in the desktop Secret Service, such as GNOME Keyring or KWallet. A locked keyring may prompt for unlock; without a keyring, sessions are not saved in plaintext. API connection and Google token keys use the corresponding Linux keyring path.

Sites see Chrome on an Android phone, rather than the macOS WebKit path's mobile Safari profile. Google sign-in is not supported inside the embedded browser. The Linux path does not read sign-in QR codes for phone handoff. During AI work, a new page loads in the same page; the owner's sign-in window handles sign-in popups separately. Form submissions into a new window and certain untouched cross-frame form-submit paths are refused before sending when the paused request cannot be inspected safely.

The documented evidence consists of real-engine browser tests against Chromium in Linux CI plus unit checks of downloads, keyring handling and refusals. It is not an observation of a complete owner session on an Ubuntu desktop, and it does not establish WSLg support in practice.

## macOS browser and service

The browser uses the system WebKit, with a dedicated sign-in window and encrypted session storage backed by Keychain. A mobile profile does not guarantee that every site will accept the browser. Payment mediation and secret filtering remain subject to their documented limitations; a logged-in browser is not a claim of universal purchasing support.

The launchd login service is installed explicitly. It is distinct from the browser process and from Homebrew installation. See [operations](operations.en.md#background-service-macos) for its commands and evidence limits.

## Windows (WSL2)

Use Ubuntu inside WSL2 for the service and the selected CLI. Windows folders appear under `/mnt/c/`, for example `/mnt/c/Users/your-name/Documents/Still`. The Ubuntu session must remain available. Native Windows packaging and a managed Windows service are not supplied by these instructions; see [#33](https://github.com/Jongtae/Still/issues/33).

The Linux browser's need for a desktop display, glibc, supported architecture and a working credential store applies independently. Do not equate successful service startup in WSL2 with verified browser operation there.

## Source and maintenance

The platform details were separated from [the former installation guide](https://github.com/Jongtae/Still/blob/6edc3380b613ab0c2897ba7a913ab658150a68f4/docs/QUICKSTART.md). Refresh this page when a platform requirement changes; keep release coverage in the manifest rather than inferring it from a merged source change.
