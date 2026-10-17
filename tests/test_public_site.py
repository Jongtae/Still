from pathlib import Path


ROOT = Path(__file__).parents[1]
SITE = ROOT / "site"
PAGES = ("index.html", "install.html", "privacy.html", "terms.html")


def test_static_site_has_required_bilingual_pages_and_navigation():
    for page in PAGES:
        text = (SITE / page).read_text(encoding="utf-8")
        assert 'lang="en"' in text
        assert 'lang="ko"' in text
        assert 'viewport' in text
        # The homepage has its own stylesheet; the policy pages share site.css.
        assert 'assets/home.css' in text if page == "index.html" else 'assets/site.css' in text
    home = (SITE / "index.html").read_text(encoding="utf-8")
    for page in PAGES[1:]:
        assert f'href="{page}"' in home


def test_static_site_never_becomes_an_oauth_or_secret_relay():
    text = "\n".join((SITE / page).read_text(encoding="utf-8") for page in PAGES).lower()
    assert "<form" not in text
    assert "<script" not in text
    assert "oauth callback" in text
    assert "token broker" in text
    assert "drive proxy" in text
    assert "client_secret" not in text
    assert "pkce verifier" not in text


def test_pages_deployment_is_manual_until_operator_details_are_confirmed():
    workflow = (ROOT / ".github/workflows/deploy-pages.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "path: site" in workflow


def test_privacy_page_explains_separate_local_deletion_and_provider_revocation():
    privacy = (SITE / "privacy.html").read_text(encoding="utf-8")
    assert "Delete local data and revoke access" in privacy
    assert "operating system’s file-management tools" in privacy
    assert "provider revocation are separate actions" in privacy
    assert "로컬 삭제와 제공자 권한 철회는 별개의 작업" in privacy


def test_site_release_stamp_follows_readme_and_manifest(tmp_path):
    import shutil, sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import stamp_site_release as stamp

    commit, version = stamp.release_values(ROOT)
    copy = tmp_path / "site"
    shutil.copytree(SITE, copy)
    for page in copy.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        text = stamp.PIN.sub(lambda m: m.group(1) + "0" * 40 + m.group(3), text)
        text = stamp.VERSION.sub(lambda m: m.group(1) + "v0.0.0" + m.group(2), text)
        page.write_text(text, encoding="utf-8")
    stamp.stamp(copy, commit, version)
    for name in ("index.html", "ko.html", "ja.html", "zh-CN.html"):
        body = (copy / name).read_text(encoding="utf-8")
        assert f"Jongtae/agentos/{commit}/scripts/install.sh" in body
        assert f'<span data-release="version">v{version}</span>' in body
        assert "0" * 40 not in body
    workflow = (ROOT / ".github/workflows/deploy-pages.yml").read_text(encoding="utf-8")
    assert "scripts/stamp_site_release.py site" in workflow


def test_homepage_is_offered_in_the_readme_languages():
    homes = {"en": "index.html", "ko": "ko.html", "ja": "ja.html", "zh-CN": "zh-CN.html"}
    for lang, name in homes.items():
        body = (SITE / name).read_text(encoding="utf-8")
        assert f'<html lang="{lang}">' in body
        assert "<script" not in body.lower() and "<form" not in body.lower()
        for other, target in homes.items():
            assert f'hreflang="{other}"' in body
        assert 'aria-current="page"' in body


def test_reading_guide_is_a_static_docs_section_that_points_to_the_manuscripts():
    import re

    guide = SITE / "read" / "ko"
    pages = sorted(guide.glob("*.html"))
    assert {p.name for p in pages} >= {"index.html", "memory.html", "ai.html", "authority.html", "limits.html", "glossary.html"}
    navs = set()
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert '<html lang="ko">' in text
        assert "<script" not in text.lower() and "<form" not in text.lower()
        assert "../../assets/read.css" in text
        # The guide retells the manuscripts; it says so on every page and points back to them.
        assert "docs/whitepapers/whose-agent.ko.md" in text or "docs/whitepapers/personal-agentos-compendium.ko.md" in text
        assert "원문과 다르면 원문이 맞습니다" in text
        assert "독립 전문가나 사람의 검토가 아닙니다" in text
        assert "docs/product-status.en.md" in text
        nav = re.search(r'<nav class="side".*?</nav>', text, re.S).group(0)
        navs.add(nav.replace(' aria-current="page"', ""))
        # Every in-page link in the "on this page" list has a matching heading.
        for anchor in re.findall(r'<nav class="onpage".*?</nav>', text, re.S)[0].split('href="#')[1:]:
            assert f'id="{anchor.split(chr(34))[0]}"' in text
    assert len(navs) == 1, "every guide page shares the same contents list"
    assert 'href="read/ko/"' in (SITE / "ko.html").read_text(encoding="utf-8")
