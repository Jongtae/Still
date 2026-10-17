"""AgentOS's own owner-facing words, by id (#1356).

One catalog (``messages.json``) holds each fixed line AgentOS itself says,
in Korean (the source) and English, Japanese and Chinese.  Code takes the
Korean source by id with ``msg``; the Telegram seam (``telegram_locale``)
shows it in the owner's language.  Changing a line means changing its
catalog entry, so the translations change with it instead of drifting.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CATALOG = Path(__file__).with_name('messages.json')
LANGUAGES = ('ko', 'en', 'ja', 'zh')


@lru_cache(maxsize=1)
def catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def msg(message_id, lang='ko', **values):
    """The line ``message_id`` in ``lang`` (Korean by default), with ``values`` filled in."""
    entry = catalog()[message_id]
    text = entry.get(lang) or entry['ko']
    return text.format(**values) if values else text
