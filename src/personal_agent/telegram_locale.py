"""Telegram system lines in the owner's language (#1350).

Only Still-authored text is eligible for catalog matching. ``Text`` preserves
that provenance while composers join AI prose and fixed lines. Matching a
catalog template is never evidence that a sentence was written by Still.

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


class Text(str):
    """Outgoing text segments paired with whether Still may translate them.

    A str subclass keeps the existing send/store APIs; concatenation and preview
    slicing retain the boundary until localization, before wire serialization.
    """

    def __new__(cls, parts):
        parts = tuple(parts)
        obj = super().__new__(cls, ''.join(text for text, _fixed in parts))
        obj.parts = tuple(parts)
        return obj

    def __getnewargs__(self):
        return (self.parts,)

    def __add__(self, other):
        return Text(self.parts + (other.parts if isinstance(other, Text) else ((other, True),)))

    def __radd__(self, other):
        return Text(((other, True),) + self.parts)

    def __getitem__(self, key):
        if not isinstance(key, slice) or key.step not in (None, 1):
            return super().__getitem__(key)
        start, stop, _ = key.indices(len(self))
        parts, offset = [], 0
        for text, fixed in self.parts:
            left, right = max(0, start - offset), min(len(text), stop - offset)
            if left < right:
                parts.append((text[left:right], fixed))
            offset += len(text)
        return Text(parts)


def ai_text(text):
    """Mark AI prose at its composer; its words never enter catalog matching."""
    return Text(((str(text or ''), False),))


def language(code, text=None):
    """Writing script first, then Telegram language_code; None means Korean/unknown."""
    text = text or ''
    if re.search(r'[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7ff]', text):
        return None
    if re.search(r'[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]', text):
        return 'ja'
    if re.search(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]', text):
        return 'zh'
    lang = str(code or '').lower().split('-')[0]
    return lang if lang in LANGUAGES else ('en' if lang and lang != 'ko' else None)


def owner_language(store, work_id=None, *, default='ko'):
    """One language decision, from the latest owner words in this Work/chat.

    Read existing owner state; never overwrite Telegram's language_code.
    Minimal stores used by phone pages may only expose config().
    ``default`` supplies a language code when the surface has no saved Telegram
    language. Script detection still wins, including Korean (returned as None).
    """
    if store is None:
        return None
    cfg = store.config('telegram', {})
    text = None
    if hasattr(store, 'db'):
        scope = "j.id=?" if work_id else "j.chat_id=? AND j.channel LIKE 'telegram:%'"
        value = work_id or cfg.get('user_id')
        with store.db() as db:
            # Queued turns already exist in jobs before their transcript row is
            # written; later owner corrections may instead be newer message rows.
            row = db.execute(
                "SELECT content FROM ("
                "SELECT m.content,m.created FROM messages m JOIN jobs j ON j.id=m.job_id "
                "WHERE m.role='user' AND j.owner_typed=1 AND " + scope +
                " UNION ALL SELECT j.message AS content,j.created FROM jobs j "
                "WHERE j.owner_typed=1 AND " + scope +
                ") ORDER BY created DESC LIMIT 1", (value, value)).fetchone()
            if row:
                text = row['content']
    return language(cfg.get('owner_language') or default, text)


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
    if isinstance(text, Text):
        return ''.join(translate(part, lang) if fixed else part for part, fixed in text.parts)
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
