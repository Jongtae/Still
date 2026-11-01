"""Localized assets and no-script carousel provenance/navigation checks."""
import importlib.util
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from lxml import html

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('carousel', ROOT / 'scripts/build_site_carousel.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_localized_images_match_copy_and_keep_uncertainty():
    copies = json.loads(builder.COPY.read_text())
    for lang, copy in copies.items():
        for index in range(3):
            source = (builder.OUT / f'{lang}-{index + 1}.svg').read_text()
            assert source == builder.render(lang, copy, index)
            svg = ET.fromstring(source)
            assert '<script' not in source and '<image' not in source
            assert copy['label'] in ''.join(svg.itertext())
            wide = (builder.OUT / f'{lang}-{index + 1}.wide.svg').read_text()
            assert wide == builder.render_wide(lang, copy, index)
            ET.fromstring(wide)
            # Context must be visibly rendered, not merely hidden in alt/description metadata.
            visible = ''.join(node.text or '' for node in svg.findall('.//{http://www.w3.org/2000/svg}text'))
            normalize = lambda value: re.sub(r'\s+', '', value)
            assert normalize(copy['contexts'][index]) in normalize(visible)
            for key in ('manifest', 'record'):
                assert (ROOT / copy['sources'][index][key]).is_file()
        assert all(copy['replies'])
        assert len(set(copy['contexts'])) == 1, 'the same context must persist across the AI switch'
        assert {row['date'] for row in copy['sources']} == {'2026-10-12'}


def test_each_homepage_links_its_own_translated_images_and_all_controls_resolve():
    for lang, name in {'en':'index.html', 'ko':'ko.html', 'ja':'ja.html', 'zh-CN':'zh-CN.html'}.items():
        doc = html.fromstring((ROOT / 'site' / name).read_text())
        assert not doc.xpath('//video|//script|//form')
        carousel = doc.xpath('//figure[@class="switch-carousel"]')[0]
        region = carousel.xpath('.//*[@class="switch-slides"]')[0]
        assert region.get('tabindex') == '0'
        slides = region.xpath('./figure')
        assert len(slides) == 3
        ids = {slide.get('id') for slide in slides}
        for index, slide in enumerate(slides):
            img = slide.xpath('.//picture//img')[0]
            source = slide.xpath('./picture/source')[0]
            assert source.get('srcset') == f'assets/ai-switch/{lang}-{index + 1}.wide.svg'
            assert (ROOT / 'site' / source.get('srcset')).is_file()
            assert img.get('src') == f'assets/ai-switch/{lang}-{index + 1}.svg'
            assert (ROOT / 'site' / img.get('src')).is_file()
            assert len(img.get('alt')) > 50
            for control in slide.xpath('.//a'):
                assert control.get('href').removeprefix('#') in ids
                assert control.get('aria-label')
        assert len(carousel.xpath('.//nav/a')) == 3
        assert 'carousel.md' in html.tostring(carousel).decode()


def test_start_engineering_and_contribution_paths_and_truthful_setup_preview():
    catalog = json.loads((ROOT / 'src/personal_agent/messages.json').read_text())
    for lang, name in {'en':'index.html', 'ko':'ko.html', 'ja':'ja.html', 'zh':'zh-CN.html'}.items():
        doc = html.fromstring((ROOT / 'site' / name).read_text())
        links = doc.xpath('//nav[@class="entry-points"]/a/@href')
        assert links == ['#install', f'engineering/{name}', '#contribute']
        assert doc.xpath('//*[@id="contribute"]//a[contains(@href,"CONTRIBUTING.md")]')
        preview = doc.xpath('//figure[@class="setup-preview"]//pre')[0].text_content()
        for key in ('intro', 'ai_codex', 'ai_claude', 'ai_api', 'ai_later'):
            assert catalog[f'first_run.{key}'][lang] in preview
