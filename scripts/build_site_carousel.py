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


def scene_messages(copy, index):
    return copy['scenes'][index]['messages']


def description(copy, index):
    scene = copy['scenes'][index]
    return ' '.join([scene['title'], *[
        (row.get('command', '') + ' ' + row['text']).strip()
        for row in scene_messages(copy, index)], copy['label'], scene['date']])


def dialogue_body(lang, copy, index, wide, font):
    frame_width = 1440 if wide else 1000
    title_rows = lines(copy['scenes'][index]['title'], lang, 62 if wide else 40)
    start = y = 155 + len(title_rows) * 58
    left, common_width = (120, 1200) if wide else (50, 900)
    content, engine = '', copy['scenes'][index].get('engine')
    for row in scene_messages(copy, index):
        role, message = row['role'], row['text']
        if role == 'switch':
            content += f'<path d="M{left} {y + 44}H{frame_width - left}" stroke="#c7cbd0"/>'
            content += f'<rect x="{frame_width / 2 - 225}" y="{y}" width="450" height="86" rx="22" fill="#e1e4e8"/>'
            content += f'<text x="{frame_width / 2}" y="{y + 35}" text-anchor="middle" font-size="28" fill="#171717">{escape(message)}</text>'
            content += f'<text x="{frame_width / 2}" y="{y + 66}" text-anchor="middle" font-size="22" fill="#626262">{escape(row["command"])}</text>'
            engine = row['engine']
            y += 108
            continue
        if role in ('marker', 'notice'):
            size = font - 4
            limit = int((common_width - 25) / size * 1.9)
            rows = lines(message, lang, limit)
            leading = int(size * 1.45)
            content += text_rows(rows, left + 15, y + size, size, '#626262', leading)
            y += len(rows) * leading + 20
            continue
        user = role == 'user'
        x, width = ((420, 900) if wide else (180, 770)) if user else (left, common_width)
        leading = int(font * 1.45)
        rows = lines(message, lang, int((width - 64) / font * 1.9))
        height = len(rows) * leading + 60
        background = '#e5e7e9' if user else '#ffffff'
        speaker = copy['you'] if user else copy['assistant'] + (f' · {engine}' if engine else '')
        content += f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="28" fill="{background}"/>'
        content += text_rows([speaker], x + 32, y + 30, 22 if wide else 24, '#626262')
        content += text_rows(rows, x + 32, y + 65, font, leading=leading)
        y += height + 16
    return content, y, start


def render_scene(lang, copy, index, wide):
    width, height = (1440, 960) if wide else (1000, 1400)
    maximum, minimum = (42, 32) if wide else (48, 40)
    scene = copy['scenes'][index]
    for font in range(maximum, minimum - 1, -1):
        content, end, start = dialogue_body(lang, copy, index, wide, font)
        if end <= height - 70:
            break
    else:
        raise ValueError(f'Dialogue overflows {lang} slide {index + 1} wide={wide}')
    shift = max(0, (height - 100 - end) / 2)
    title_rows = lines(scene['title'], lang, 62 if wide else 40)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" xml:lang="{lang}">
<title>{escape(scene['title'])}</title><desc>{escape(description(copy, index))}</desc>
<rect width="{width}" height="{height}" rx="32" fill="#f0f1f3"/>
<g font-family="system-ui, -apple-system, sans-serif">
<text x="60" y="65" font-size="28" font-weight="550" fill="#171717">Still</text>
<text x="{width - 60}" y="65" text-anchor="end" font-size="24" fill="#626262">{scene['date']} · Telegram</text>
<path d="M60 100H{width - 60}" stroke="#d5d5d5"/>
{text_rows(title_rows, 60, 160, 42, leading=58)}
<g transform="translate(0 {shift})">{content}</g>
<text x="60" y="{height - 35}" font-size="30" fill="#626262">{escape(copy['label'])}</text>
</g></svg>
'''


def render(lang, copy, index):
    return render_scene(lang, copy, index, False)


def render_wide(lang, copy, index):
    return render_scene(lang, copy, index, True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for lang, copy in json.loads(COPY.read_text()).items():
        for index in range(3):
            (OUT / f'{lang}-{index + 1}.svg').write_text(render(lang, copy, index), encoding='utf-8')
            (OUT / f'{lang}-{index + 1}.wide.svg').write_text(render_wide(lang, copy, index), encoding='utf-8')
    print('Rendered 24 localized SVG dialogue excerpts (mobile + desktop)')


if __name__ == '__main__':
    main()
