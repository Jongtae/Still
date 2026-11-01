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


def lines(text, lang):
    if lang == 'en':
        return textwrap.wrap(text, width=42)
    result, current, width = [], '', 0
    # Keep product names and quantities such as 200g intact in CJK copy.
    for char in re.findall(r'[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*|.', text):
        size = sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in char)
        if width + size > 42:
            result.append(current)
            current, width = '', 0
        current += char
        width += size
    if current:
        result.append(current)
    return result


def text_rows(rows, x, y, size=36, color='#293c35'):
    return ''.join(f'<text x="{x}" y="{y + i * 52}" font-size="{size}" fill="{color}">{escape(row)}</text>' for i, row in enumerate(rows))


def render(lang, copy, index):
    user = lines(copy['users'][index], lang)
    reply = lines(copy['replies'][index], lang)
    y = 265
    content = ''
    if user:
        height = len(user) * 52 + 70
        content += f'<rect x="90" y="{y}" width="840" height="{height}" rx="28" fill="#e6d8ce"/>'
        content += text_rows([copy['you']], 120, y + 35, 24)
        content += text_rows(user, 120, y + 85)
        y += height + 30
    height = len(reply) * 52 + 70
    if y + height > 915:
        raise ValueError(f'Dialogue overflows {lang} slide {index + 1}: {y + height}')
    content += f'<rect x="50" y="{y}" width="880" height="{height}" rx="28" fill="#fffdfa"/>'
    content += text_rows([copy['assistant']], 80, y + 35, 24)
    content += text_rows(reply, 80, y + 85)
    description = ' '.join([copy['notes'][index], copy['users'][index], copy['replies'][index], copy['label']])
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1040" viewBox="0 0 1000 1040" xml:lang="{lang}">
<title>{escape(copy['titles'][index])}</title><desc>{escape(description)}</desc>
<rect width="1000" height="1040" rx="32" fill="#f3eee9"/>
<g font-family="system-ui, -apple-system, sans-serif">
<text x="50" y="65" font-size="28" fill="#53655c">Still / {index + 1:02d}</text>
<text x="50" y="145" font-size="58" font-weight="650" fill="#293c35">{escape(copy['titles'][index])}</text>
<text x="50" y="210" font-size="32" fill="#53655c">{escape(copy['notes'][index])}</text>
{content}
<path d="M50 950H950" stroke="#bdc9bd"/>
<text x="50" y="990" font-size="27" fill="#53655c">{escape(copy['label'])}</text>
</g></svg>
'''


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for lang, copy in json.loads(COPY.read_text()).items():
        for index in range(3):
            (OUT / f'{lang}-{index + 1}.svg').write_text(render(lang, copy, index), encoding='utf-8')
    print('Rendered 12 localized SVG dialogue excerpts')


if __name__ == '__main__':
    main()
