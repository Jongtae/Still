# Still name compatibility map

## Status

Still was imported on 2026-10-17 from the private predecessor repository `Jongtae/agentos` (#46). The product name changed; several technical names did not. This page lists the names that were kept on purpose, so a reader can tell a deliberate compatibility name from a missed rename ([#4](https://github.com/Jongtae/Still/issues/4)).

Renaming any entry below changes an installed owner's commands, data location, background service or stored secrets. Such a change needs its own issue with a migration and rollback path. It is not a documentation cleanup.

## Retained names

| Kind | Retained name | Where it is defined | Why it is kept |
| --- | --- | --- | --- |
| Command | `agentos` (and the equivalent `still`) | `pyproject.toml` `[project.scripts]` | Both names run the same entry point. Documentation uses `agentos` because existing installs, the launchd job and the Homebrew formula call it. |
| Python distribution | `personal-agentos` | `pyproject.toml` `name` | `agentos uninstall` and the service version check look up this distribution name. |
| Python import package | `personal_agent` | `src/personal_agent/` | Internal module path; renaming it would touch every import and test without changing behavior. |
| Homebrew formula | `agentos` in the `jongtae/still` tap | `deploy/homebrew/agentos.rb.template` | Matches the installed command. The tap itself lives in this repository. |
| Data directory | `~/.local/share/agentos` (override: `AGENTOS_DATA`) | `src/personal_agent/quickstart.py` | Holds the owner's state. Moving it requires a data migration. |
| Extra instances | `~/.local/share/agentos-instances/<name>` | `src/personal_agent/quickstart.py` | Same reason as the data directory. |
| Environment variables | `AGENTOS_*` (for example `AGENTOS_DATA`, `AGENTOS_GOOGLE_CLIENT_FILE`) | `src/personal_agent/` | Existing owner configuration and service files set them. |
| macOS background service | launchd label `com.personal-agentos` | `src/personal_agent/service_control.py`, `deploy/com.personal-agentos.plist` | Installed jobs are found and removed by this label. |
| Linux service template | `personal-agentos.service` | `deploy/personal-agentos.service` | Example unit file; kept consistent with the launchd label. |
| macOS keychain item | `personal-agentos.browser-jar` | `src/personal_agent/browser_jar.py` | The browser sign-in store's encryption key. Renaming it would orphan existing keys. |
| Bundled skill package | `agentos-skills` | `src/personal_agent/bundled_skills/agentos-skills/` | Bundled skill identity. Its links now point to this repository's `skills/`. |

## Historical references

Links of the form `https://github.com/Jongtae/agentos/issues/N` and bare issue numbers in older contracts, roadmaps and research name records in the private predecessor repository. They are historical provenance (Development Constitution C12): they are not publicly readable, select nothing here, and are not rewritten. Current work is tracked in this repository's issues; the curated backlog is [#44](https://github.com/Jongtae/Still/issues/44). The predecessor plan and release manifest are archived under [`docs/history/`](history/).

Operational entry points (README in every locale, QUICKSTART, CONTRIBUTING, SECURITY, issue-template links, the site and the bundled skill index) point to `Jongtae/Still`. Where a predecessor issue was linked as a live reference, it now links to the Still backlog issue that carries the same work:

| Predecessor issue | Still issue |
| --- | --- |
| `Jongtae/agentos#758` browser payment-guard limitations | [#26](https://github.com/Jongtae/Still/issues/26) |
| `Jongtae/agentos#1168` native Windows | [#33](https://github.com/Jongtae/Still/issues/33) |
| `Jongtae/agentos#682` non-macOS browser backend | [#34](https://github.com/Jongtae/Still/issues/34) |
| `Jongtae/agentos#828` private-data disclosure at outbound boundaries | [#28](https://github.com/Jongtae/Still/issues/28) |

Evidence citations such as the synthetic first-user audit `Jongtae/agentos#472` in [product status](product-status.en.md) stay as predecessor records, because no Still issue holds that evidence.
