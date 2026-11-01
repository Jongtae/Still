"""Localized assets and no-script carousel provenance/navigation checks."""
import importlib.util
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from urllib.parse import urlsplit

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
            # Each example includes its own dialogue in reading order.
            visible = ''.join(node.text or '' for node in svg.findall('.//{http://www.w3.org/2000/svg}text'))
            normalize = lambda value: re.sub(r'\s+', '', value)
            cursor = 0
            for row in builder.scene_messages(copy, index):
                found = normalize(visible).find(normalize(row['text']), cursor)
                assert found >= cursor
                cursor = found + len(normalize(row['text']))
        assert 'contexts' not in copy, 'do not replace dialogue with sidebar summaries'
        assert len(copy['scenes']) == 3
        for scene in copy['scenes']:
            for key in ('manifest', 'record'):
                assert (ROOT / scene['source'][key]).is_file()
            manifest = json.loads((ROOT / scene['source']['manifest']).read_text())
            record = ROOT / scene['source']['record']
            assert manifest.get('created') == scene['date'] or scene['date'] in record.read_text()
            # The October 11 recordings do not identify the model. Only the
            # separately sourced switch session may name one in dialogue.
            if manifest.get('model_identity') == 'not shown':
                assert not scene.get('engine')
                assert not any(row.get('engine') for row in scene['messages'])


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
            wide_url = urlsplit(source.get('srcset'))
            mobile_url = urlsplit(img.get('src'))
            assert wide_url.path == f'assets/ai-switch/{lang}-{index + 1}.wide.svg'
            assert (ROOT / 'site' / wide_url.path).is_file()
            assert mobile_url.path == f'assets/ai-switch/{lang}-{index + 1}.svg'
            assert (ROOT / 'site' / mobile_url.path).is_file()
            assert wide_url.query and mobile_url.query, 'invalidate cached dialogue images'
            assert len(img.get('alt')) > 50
            for control in slide.xpath('.//a'):
                assert control.get('href').removeprefix('#') in ids
                assert control.get('aria-label')
        assert len(carousel.xpath('.//nav/a')) == 3
        assert not doc.xpath('//*[@class="swap" or @class="section-intro"]')
        assert 'carousel.md' in html.tostring(carousel).decode()


def test_start_engineering_and_contribution_paths_and_truthful_setup_preview():
    catalog = json.loads((ROOT / 'src/personal_agent/messages.json').read_text())
    for lang, name in {'en':'index.html', 'ko':'ko.html', 'ja':'ja.html', 'zh':'zh-CN.html'}.items():
        doc = html.fromstring((ROOT / 'site' / name).read_text())
        links = doc.xpath('//nav[@class="entry-points"]/a/@href')
        assert links == ['#install', f'engineering/{name}', '#contribute']
        assert doc.xpath('//*[@id="contribute"]//a[contains(@href,"CONTRIBUTING.md")]')
        preview = doc.xpath('//figure[@class="setup-preview"]//pre')[0].text_content()
        assert doc.xpath('//div[@class="setup-grid"]/ol[@class="steps"]')
        assert doc.xpath('//div[@class="setup-grid"]/figure[@class="setup-preview"]')
        for key in ('intro', 'ai_codex', 'ai_claude', 'ai_api', 'ai_later'):
            assert catalog[f'first_run.{key}'][lang] in preview
