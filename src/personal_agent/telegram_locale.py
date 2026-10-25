"""Telegram system lines in the owner's language (#1350).

AI answers already follow the owner's language.  Still's own fixed lines
(progress, task cards, buttons, terminal and memory notices) are written in
Korean at their call sites.  This module translates them at the one place
they leave for Telegram, so no call site changes.

The lines come from the id-keyed catalog (``messages.json``, #1356).  Only
a whole line that exactly matches a catalog line or template is
translated.  An AI answer never matches and passes through unchanged, and a
line the table does not know stays Korean.  Korean owners see no change.

A catalog entry may span several lines (#235).  It is matched as one block,
as the whole message or as consecutive lines inside a longer one, before the
remaining lines are matched one by one.
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
            names = list(dict.fromkeys(_PLACEHOLDER.findall(source)))
            parts = _PLACEHOLDER.split(source)
            # #242: a value named twice ("{label} ... {label}") must be the same text both times.
            pattern = ''.join(re.escape(part) if i % 2 == 0 else
                              f'(?P={part})' if part in parts[1:i:2] else f'(?P<{part}>.+?)'
                              for i, part in enumerate(parts))
            templates.append((re.compile(f'^{pattern}$', re.S), names, targets, source.count('\n') + 1))
        else:
            exact[source] = targets
    # Longer templates first, so the most specific one wins.
    templates.sort(key=lambda row: -len(row[0].pattern))
    return exact, templates


@lru_cache(maxsize=1)
def _blocks():
    """#235: the catalog entries that span several lines, as (line count, exact, templates); most lines first."""
    exact, templates = _table()
    sizes = {source.count('\n') + 1 for source in exact} | {row[3] for row in templates}
    rows = []
    for size in sorted((size for size in sizes if size > 1), reverse=True):
        rows.append((size, {source: targets for source, targets in exact.items() if source.count('\n') + 1 == size},
                     [row for row in templates if row[3] == size]))
    return rows


def _match(body, lang, exact, templates):
    """``body`` translated when it is exactly one catalog line or template; else None."""
    hit = exact.get(body)
    if hit and hit.get(lang):
        return hit[lang]
    for pattern, names, targets, _size in templates:
        found = pattern.match(body)
        if found and targets.get(lang):
            values = {name: translate(found.group(name), lang) for name in names}
            return targets[lang].format(**values)
    return None


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
    hit = _match(body, lang, exact, [row for row in templates if row[3] == 1])
    return line if hit is None else lead + hit + trailer


def translate(text, lang):
    """``text`` with every known fixed line translated into ``lang``; unchanged for Korean or None."""
    if not lang or not isinstance(text, str) or not text:
        return text
    exact, templates = _table()
    # The whole message as one entry: a single line, or a multi-line entry
    # whose placeholder value may itself span lines (#235).
    whole = _match(text.strip(), lang, exact, [row for row in templates if row[3] > 1])
    if whole is not None:
        return whole
    lines = text.split('\n')
    out, index = [], 0
    while index < len(lines):
        for size, block_exact, block_templates in _blocks():
            if index + size > len(lines) or not lines[index].strip():
                continue
            chunk = '\n'.join(lines[index:index + size])
            hit = _match(chunk.strip(), lang, block_exact, block_templates)
            if hit is not None:
                out.append(lines[index][:len(lines[index]) - len(lines[index].lstrip())] + hit)
                index += size
                break
        else:
            out.append(_line(lines[index], lang))
            index += 1
    return '\n'.join(out)


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
