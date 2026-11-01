"""Check public entry points, source reachability and nested deploy stamping."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
LOCALES = {"en": "index.html", "ko": "ko.html", "ja": "ja.html", "zh-CN": "zh-CN.html"}


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links = []
        self.ids = set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "a" and "href" in attrs:
            self.links.append(attrs["href"])


def test_engineering_locales_and_links():
    for lang, name in LOCALES.items():
        page = SITE / "engineering" / name
        body = page.read_text()
        assert f'<html lang="{lang}">' in body
        assert "<script" not in body and "<form" not in body
        assert f'href="engineering/{name}"' in (SITE / name).read_text()
        parsed = Links(body)
        assert {"philosophy", "ontology", "decision", "presence", "models", "authority", "research"} <= parsed.ids
        for other in LOCALES:
            assert f'hreflang="{other}"' in body
        for href in parsed.links:
            url = urlsplit(href)
            if url.netloc == "github.com" and url.path.startswith("/Jongtae/Still/blob/main/"):
                assert (ROOT / unquote(url.path.removeprefix("/Jongtae/Still/blob/main/"))).is_file(), href
            elif not url.scheme and not url.netloc:
                target = page.parent / unquote(url.path) if url.path else page
                assert target.is_file(), href
                if url.fragment:
                    assert unquote(url.fragment) in Links(target.read_text()).ids, href


def test_stamping_reaches_nested_engineering_page(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("stamp", ROOT / "scripts/stamp_site_release.py")
    stamp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stamp)
    nested = tmp_path / "engineering" / "ko.html"
    nested.parent.mkdir()
    nested.write_text((SITE / "engineering/ko.html").read_text())
    assert stamp.stamp(tmp_path, "a" * 40, "9.8.7") == [nested]
    assert '<span data-release="version">v9.8.7</span>' in nested.read_text()
    assert stamp.stamp(tmp_path, "a" * 40, "9.8.7") == []
