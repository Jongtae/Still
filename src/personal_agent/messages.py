"""Still's own owner-facing words, by id (#1356).

One catalog (``messages.json``) holds each fixed line Still itself says,
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
#: #242: placeholders that carry Korean grammar only (``{obj}`` is the object
#: particle 을/를 after a name).  The other languages may leave them out.
KOREAN_ONLY_PLACEHOLDERS = frozenset({'obj'})


@lru_cache(maxsize=1)
def catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def msg(message_id, lang='ko', **values):
    """The line ``message_id`` in ``lang`` (Korean by default), with ``values`` filled in."""
    entry = catalog()[message_id]
    text = entry.get(lang) or entry['ko']
    return text.format(**values) if values else text


#: #1052: the web screen's language codes for the catalog's languages.
WEB_LANGUAGES = {'en': 'en', 'ja': 'ja', 'zh': 'zh-CN'}


@lru_cache(maxsize=1)
def web_catalog_script():
    """``/messages.js``: the catalog as Korean source → translation per web language.

    The web screen shows Still's own lines in Korean as the server sends them and
    translates them by their Korean text; this keeps that table in step with the
    catalog (#1052).
    """
    tables = {web: {} for web in WEB_LANGUAGES.values()}
    for entry in catalog().values():
        for lang, web in WEB_LANGUAGES.items():
            if entry.get(lang) and entry[lang] != entry['ko']:
                tables[web].setdefault(entry['ko'], entry[lang])
    return ('globalThis.STILL_MESSAGES=' + json.dumps(tables, ensure_ascii=False, separators=(',', ':')) + ';\n').encode('utf-8')
