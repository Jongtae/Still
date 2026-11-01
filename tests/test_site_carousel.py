"""Localized assets and no-script carousel provenance/navigation checks."""
import importlib.util
import json
from pathlib import Path
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
        assert all(copy['replies'])
    # The redrawn excerpts retain both the source's unknown date and partial verification.
    assert 'cannot establish whether that was yesterday' in copies['en']['replies'][0]
    assert 'Some steps could not be checked' in copies['en']['replies'][1]
    assert 'decision AI is unavailable' in copies['en']['replies'][2]


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
            img = slide.xpath('./img')[0]
            assert img.get('src') == f'assets/ai-switch/{lang}-{index + 1}.svg'
            assert (ROOT / 'site' / img.get('src')).is_file()
            assert len(img.get('alt')) > 50
            for control in slide.xpath('.//a'):
                assert control.get('href').removeprefix('#') in ids
                assert control.get('aria-label')
        assert len(carousel.xpath('.//nav/a')) == 3
        assert 'carousel.md' in html.tostring(carousel).decode()
