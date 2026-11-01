#!/usr/bin/env python3
"""Render localized vector dialogue excerpts; no model or image service needed."""
from html import escape
import json
from pathlib import Path
import re
import textwrap
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
COPY = ROOT / 'docs/assets/readme/ai-switch/carousel-copy.json'
OUT = ROOT / 'site/assets/ai-switch'


def lines(text, lang, limit=42):
    if lang == 'en':
        return textwrap.wrap(text, width=limit)
    if lang == 'ko':
        result, current = [], ''
        for word in text.split():
            candidate = (current + ' ' + word).strip()
            width = sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in candidate)
            if width > limit and current:
                result.append(current)
                current = word
            else:
                current = candidate
        if current:
            result.append(current)
        return result
    result, current, width = [], '', 0
    # Keep product names and quantities such as 200g intact in CJK copy.
    for char in re.findall(r'[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*|.', text):
        size = sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in char)
        if width + size > limit:
            result.append(current)
            current, width = '', 0
        current += char
        width += size
    if current:
        result.append(current)
    return result


def text_rows(rows, x, y, size=36, color='#171717', leading=52):
    return ''.join(f'<text x="{x}" y="{y + i * leading}" font-size="{size}" fill="{color}">{escape(row)}</text>' for i, row in enumerate(rows))


def description(copy, index):
    return ' '.join([copy['context_labels'][index], copy['contexts'][index],
                     copy['notes'][index], copy['users'][index], copy['replies'][index],
                     copy['label'], copy['sources'][index]['date']])


def render(lang, copy, index):
    prior = lines(copy['contexts'][index], lang)
    prior_height = len(prior) * 46 + 85
    content = f'<rect x="50" y="230" width="900" height="{prior_height}" rx="24" fill="#ffffff" stroke="#d5d5d5"/>'
    content += text_rows([copy['context_labels'][index]], 80, 270, 24, '#626262')
    content += text_rows(prior, 80, 320, 32, leading=46)
    y = 230 + prior_height + 42
    content += text_rows([copy['later_label']], 80, y, 24, '#626262')
    y += 24
    for speaker, message, x, width, background in (
        (copy['you'], copy['users'][index], 90, 840, '#e5e7e9'),
        (copy['assistant'], copy['replies'][index], 50, 880, '#ffffff'),
    ):
        rows = lines(message, lang)
        height = len(rows) * 52 + 70
        content += f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="26" fill="{background}"/>'
        content += text_rows([speaker], x + 30, y + 35, 24, '#626262')
        content += text_rows(rows, x + 30, y + 85)
        y += height + 26
    if y > 1220:
        raise ValueError(f'Dialogue overflows {lang} slide {index + 1}: {y}')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1320" viewBox="0 0 1000 1320" xml:lang="{lang}">
<title>{escape(copy['titles'][index])}</title><desc>{escape(description(copy, index))}</desc>
<rect width="1000" height="1320" rx="32" fill="#f0f1f3"/>
<g font-family="system-ui, -apple-system, sans-serif">
<text x="50" y="65" font-size="28" fill="#626262">Still / {index + 1:02d} · {copy['sources'][index]['date']}</text>
<text x="50" y="145" font-size="54" font-weight="550" fill="#171717">{escape(copy['titles'][index])}</text>
{content}
<path d="M50 1250H950" stroke="#d5d5d5"/>
<text x="50" y="1290" font-size="27" fill="#626262">{escape(copy['label'])}</text>
</g></svg>
'''


def render_wide(lang, copy, index):
    """Earlier context is visible beside the later request and observed reply."""
    prior = lines(copy['contexts'][index], lang, 26)
    prior_height = len(prior) * 36 + 85
    content = f'<rect x="70" y="370" width="400" height="{prior_height}" rx="24" fill="#ffffff" stroke="#d5d5d5"/>'
    content += text_rows([copy['context_labels'][index]], 94, 410, 20, '#626262')
    content += text_rows(prior, 94, 455, 26, leading=36)
    y = 150
    content += text_rows(lines(copy['notes'][index], lang), 520, 76, 24, '#626262', leading=34)
    for speaker, message, x, width, background in (
        (copy['you'], copy['users'][index], 570, 800, '#e5e7e9'),
        (copy['assistant'], copy['replies'][index], 520, 850, '#ffffff'),
    ):
        rows = lines(message, lang)
        height = len(rows) * 46 + 70
        content += f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="26" fill="{background}"/>'
        content += text_rows([speaker], x + 30, y + 34, 23, '#626262')
        content += text_rows(rows, x + 30, y + 82, 32, leading=46)
        y += height + 26
    if y > 825 or 370 + prior_height > 735:
        raise ValueError(f'Wide dialogue overflows {lang} slide {index + 1}')
    title = lines(copy['titles'][index], lang, 16)
    label = lines(copy['label'], lang, 30)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" viewBox="0 0 1440 900" xml:lang="{lang}">
<title>{escape(copy['titles'][index])}</title><desc>{escape(description(copy, index))}</desc>
<rect width="1440" height="900" rx="40" fill="#f0f1f3"/>
<g font-family="system-ui, -apple-system, sans-serif">
<text x="70" y="85" font-size="24" fill="#626262">Still / {index + 1:02d} · {copy['sources'][index]['date']}</text>
{text_rows(title, 70, 200, 48, leading=64)}
{text_rows(label, 70, 780, 23, '#626262', leading=34)}
{content}
</g></svg>
'''


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for lang, copy in json.loads(COPY.read_text()).items():
        for index in range(3):
            (OUT / f'{lang}-{index + 1}.svg').write_text(render(lang, copy, index), encoding='utf-8')
            (OUT / f'{lang}-{index + 1}.wide.svg').write_text(render_wide(lang, copy, index), encoding='utf-8')
    print('Rendered 24 localized SVG dialogue excerpts (mobile + desktop)')


if __name__ == '__main__':
    main()
