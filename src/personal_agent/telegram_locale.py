"""Telegram system lines in the owner's language (#1350).

AI answers already follow the owner's language.  AgentOS's own fixed lines
(progress, task cards, buttons, terminal and memory notices) are written in
Korean at their call sites.  This module translates them at the one place
they leave for Telegram, so no call site changes.

The lines come from the id-keyed catalog (``messages.json``, #1356).  Only
a whole line that exactly matches a catalog line or template is
translated.  An AI answer never matches and passes through unchanged, and a
line the table does not know stays Korean.  Korean owners see no change.
"""
from __future__ import annotations

import copy
import re
from functools import lru_cache

LANGUAGES = ('en', 'ja', 'zh')
#: Methods whose body text or buttons the owner reads.
LOCALIZED_METHODS = frozenset({'sendMessage', 'editMessageText', 'editMessageReplyMarkup', 'answerCallbackQuery',
                               'sendMessageDraft', 'sendRichMessageDraft'})
_PLACEHOLDER = re.compile(r'\{([a-z_]+)\}')
#: A presence draft ends with its dots frame (``telegram_presence.DOTS_FRAMES``).
_TRAILER = re.compile(r'(\s*(?:·\s*){2,}·?\s*|…\s*)$')


def language(code):
    """The table language for a Telegram ``language_code``, or None for Korean or unknown."""
    lang = str(code or '').lower().split('-')[0]
    return lang if lang in LANGUAGES else ('en' if lang and lang != 'ko' else None)


@lru_cache(maxsize=1)
def _table():
    # #1356: the id-keyed catalog (messages.json) is the only source.
    from .messages import catalog
    data = {entry['ko']: {lang: entry[lang] for lang in LANGUAGES} for entry in catalog().values()}
    exact, templates = {}, []
    for source, targets in data.items():
        if _PLACEHOLDER.search(source):
            names = _PLACEHOLDER.findall(source)
            parts = _PLACEHOLDER.split(source)
            pattern = ''.join(re.escape(part) if i % 2 == 0 else f'(?P<{part}>.+?)' for i, part in enumerate(parts))
            templates.append((re.compile(f'^{pattern}$', re.S), names, targets))
        else:
            exact[source] = targets
    # Longer templates first, so the most specific one wins.
    templates.sort(key=lambda row: -len(row[0].pattern))
    return exact, templates


def _line(line, lang):
    exact, templates = _table()
    if not line.strip():
        return line
    lead = line[:len(line) - len(line.lstrip())]
    body = line.strip()
    trailer = ''
    match = _TRAILER.search(body)
    if match and match.start() > 0:
        trailer, body = body[match.start():], body[:match.start()]
    hit = exact.get(body)
    if hit and hit.get(lang):
        return lead + hit[lang] + trailer
    for pattern, names, targets in templates:
        found = pattern.match(body)
        if found and targets.get(lang):
            values = {name: translate(found.group(name), lang) for name in names}
            return lead + targets[lang].format(**values) + trailer
    return line


def translate(text, lang):
    """``text`` with every known fixed line translated into ``lang``; unchanged for Korean or None."""
    if not lang or not isinstance(text, str) or not text:
        return text
    exact, _ = _table()
    whole = exact.get(text.strip())
    if whole and whole.get(lang):
        return whole[lang]
    return '\n'.join(_line(line, lang) for line in text.split('\n'))


def localize_body(method, body, lang):
    """A copy of one Bot API ``body`` with its owner-visible text translated."""
    if not lang or method not in LOCALIZED_METHODS or not isinstance(body, dict):
        return body
    out = copy.deepcopy(body)
    if isinstance(out.get('text'), str):
        out['text'] = translate(out['text'], lang)
    markup = out.get('reply_markup')
    if isinstance(markup, dict):
        for key in ('inline_keyboard', 'keyboard'):
            for row in markup.get(key) or []:
                for button in row if isinstance(row, list) else []:
                    if isinstance(button, dict) and isinstance(button.get('text'), str):
                        button['text'] = translate(button['text'], lang)
    rich = out.get('rich_message')
    if isinstance(rich, dict):
        for block in rich.get('blocks') or []:
            if isinstance(block, dict) and isinstance(block.get('text'), str):
                block['text'] = translate(block['text'], lang)
    return out
