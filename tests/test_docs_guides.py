"""Offline links for the split guides and consolidated contract entry points.

Checks changed reading surfaces and links into them from docs/site/root/.github
Markdown and HTML. Pinned historical revisions are not treated as current files.
This is not an external-URL, scientific-claim or runtime-policy validator.
"""
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
GUIDES = (
    "docs/guide/operations.en.md",
    "docs/guide/connections.en.md",
    "docs/guide/workplace.en.md",
    "docs/guide/platforms.en.md",
)
MEMORY = "docs/engineering/contracts/memory.en.md"
DOCUMENTS = (
    "docs/README.md", "docs/QUICKSTART.md",
    "docs/product-status.en.md", "docs/product-status.ko.md",
    "docs/secretary-agency-contract.en.md",
    "docs/web-management-contract.en.md", "docs/web-management-contract.ko.md",
    "docs/decision-layer.en.md", "docs/presence-experience-contract.en.md",
    MEMORY, *GUIDES,
)
QUICKSTART_ALIASES = (
    "current-use-versus-planned-work", "before-you-install",
    "install-on-macos-or-linux", "windows-wsl2", "first-task",
    "company-cloud-ai-amazon-bedrock-microsoft-foundry-azure-openai",
    "other-model-connections", "intranet-addresses",
    "company-tools-through-your-own-mcp-server", "work-mode-on-a-company-network",
    "browser-on-a-linux-desktop", "recommended-owner-dogfood-task",
    "telegram", "mattermost", "slack", "restart-and-update", "uninstall",
    "background-service-macos", "google-drive-gmail-and-calendar",
    "google-calendar-source-checkout", "gmail-source-checkout",
    "remote-host--source-installation", "preview-scope",
    "advanced-local-manifest-plugins", "historical-general-tool-runtime-020",
)
MEMORY_ALIASES = (
    "amendment--422-2026-10-30-memory-is-shown-in-use-consolidation-is-quiet-and-reads-only-the-owners-words",
    "amendment--402-2026-10-30-the-once-a-day-look-back-dream",
    "amendment--220-2026-10-23-append--invalidate",
    "amendment--19-steady-notes-01-2026-10-20-one-bounded-revisable-working-notes-document",
    "amendment--998-self-review-01-2026-10-04-the-secretary-reviews-its-own-misses",
    "amendment--918-slice-a-2026-10-02-memory-saves-without-an-ask",
    "amendment--805-phase-1-2026-09-28",
)


def prose(text):
    """Exclude fenced examples; preserve actual HTML anchor declarations."""
    fence = None
    for line in text.splitlines():
        mark = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if mark:
            token = mark.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None:
            yield line


def targets(text):
    body = "\n".join(prose(text))
    yield from re.findall(r"\[[^]\n]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", body)
    yield from re.findall(r"(?:href|src)=[\"']([^\"']+)[\"']", body)
    # Resolve reference definitions too; footnote bodies are not URL definitions.
    yield from re.findall(r"^ {0,3}\[(?!\^)[^]\n]+\]:\s*<?([^\s>]+)>?", body, re.M)


def anchors(text):
    """Explicit IDs and the simple ATX headings used on these changed pages."""
    lines = list(prose(text))
    result = set(re.findall(r'\bid=[\"\']([^\"\']+)[\"\']', "\n".join(lines)))
    counts = {}
    for line in lines:
        match = re.match(r"^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?$", line)
        if not match:
            continue
        title = re.sub(r"<[^>]*>", "", match.group(1))
        title = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", title)
        slug = re.sub(r"[^\w\s-]", "", title.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        result.add(slug if count == 0 else f"{slug}-{count}")
        counts[slug] = count + 1
    return result


def local_target(source, target):
    parsed = urlsplit(target.strip("<>"))
    path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc:
        # Current GitHub links are inbound consumers too. Do not reinterpret a
        # commit-pinned source as the current checkout or follow arbitrary URLs.
        prefix = "/jongtae/still/blob/main/"
        raw_prefix = "/jongtae/still/main/"
        if parsed.netloc.lower() == "github.com" and path.lower().startswith(prefix):
            path = "/" + path[len(prefix):]
        elif parsed.netloc.lower() == "raw.githubusercontent.com" and path.lower().startswith(raw_prefix):
            path = "/" + path[len(raw_prefix):]
        else:
            return None
    destination = (source if not path else
                   ROOT / path.lstrip("/") if path.startswith("/") else
                   source.parent / path)
    return destination.resolve(), unquote(parsed.fragment)


def test_split_guides_and_current_memory_home_are_reachable():
    quickstart = ROOT / "docs/QUICKSTART.md"
    linked = {local_target(quickstart, target)[0]
              for target in targets(quickstart.read_text(encoding="utf-8"))
              if local_target(quickstart, target) is not None}
    assert {(ROOT / path).resolve() for path in GUIDES} <= linked
    agency = ROOT / "docs/secretary-agency-contract.en.md"
    assert (ROOT / MEMORY).resolve() in {
        value[0] for target in targets(agency.read_text(encoding="utf-8"))
        if (value := local_target(agency, target)) is not None
    }


def test_legacy_sections_have_compatibility_anchors():
    for name, expected in (("docs/QUICKSTART.md", QUICKSTART_ALIASES),
                           ("docs/secretary-agency-contract.en.md", MEMORY_ALIASES)):
        present = anchors((ROOT / name).read_text(encoding="utf-8"))
        assert set(expected) <= present, (name, sorted(set(expected) - present))


def link_errors(sources, anchor_targets=None):
    """Check paths, and anchors on the declared current destinations."""
    destinations = sources if anchor_targets is None else anchor_targets
    anchor_sets = {path: anchors(path.read_text(encoding="utf-8")) for path in destinations}
    errors = []
    for source in sources:
        for target in targets(source.read_text(encoding="utf-8")):
            value = local_target(source, target)
            if value is None:
                continue
            destination, fragment = value
            # Inbound-only auditing must not turn an unrelated old link into
            # this cleanup's claim or bypass checking a changed destination.
            if anchor_targets is not None and destination not in anchor_sets:
                continue
            if not destination.exists():
                errors.append(f"{source} -> {target}: missing path")
            elif fragment and destination in anchor_sets and fragment not in anchor_sets[destination]:
                errors.append(f"{source} -> {target}: missing anchor")
    return errors


def test_changed_reading_surfaces_have_local_paths_and_internal_anchors():
    errors = link_errors([(ROOT / path).resolve() for path in DOCUMENTS])
    assert not errors, "\n".join(errors)


def test_inbound_links_to_changed_contracts_and_guides_keep_their_anchors():
    sources = {path.resolve() for path in ROOT.iterdir()
               if path.is_file() and path.suffix in {".md", ".html"}}
    for folder in (ROOT / "docs", ROOT / "site", ROOT / ".github"):
        sources.update(path.resolve() for path in folder.rglob("*")
                       if path.is_file() and path.suffix in {".md", ".html"})
    changed = [(ROOT / path).resolve() for path in DOCUMENTS]
    errors = link_errors(sorted(sources), anchor_targets=changed)
    assert not errors, "\n".join(errors)


def test_link_and_anchor_helpers_reject_missing_targets_and_ignore_examples():
    text = '# Setup\n\n<a id="kept"></a>\n\n[Guide](guide.md#start)\n\n```md\n# Fake\n[Absent](absent.md)\n```\n'
    assert anchors(text) == {"setup", "kept"}
    assert list(targets(text)) == ["guide.md#start"]
    assert list(targets('[guide]: guide.md#start "Guide"\n')) == ['guide.md#start']
    assert "missing" not in anchors(text)
    assert local_target(ROOT / 'docs/README.md', 'https://example.test/a#b') is None
    assert local_target(ROOT / 'docs/README.md', 'guide.md#start') == (
        (ROOT / 'docs/guide.md').resolve(), 'start')
    assert local_target(ROOT / 'README.md', 'https://github.com/Jongtae/Still/blob/main/docs/QUICKSTART.md#first-task') == (
        (ROOT / 'docs/QUICKSTART.md').resolve(), 'first-task')
    assert local_target(ROOT / 'README.md', 'https://github.com/Jongtae/Still/blob/' + 'a' * 40 + '/docs/QUICKSTART.md#old') is None


def test_link_check_detects_missing_file_and_fragment():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source, target = root / "index.md", root / "guide.md"
        target.write_text("# Start\n", encoding="utf-8")
        source.write_text("[Guide](guide.md#start)\n", encoding="utf-8")
        assert link_errors([source, target]) == []
        assert link_errors([source], anchor_targets=[target]) == []
        source.write_text("[Guide](guide.md#missing)\n", encoding="utf-8")
        assert len(link_errors([source, target])) == 1
        assert "missing anchor" in link_errors([source, target])[0]
        assert "missing anchor" in link_errors([source], anchor_targets=[target])[0]
        source.write_text("[Guide](missing.md)\n", encoding="utf-8")
        assert len(link_errors([source, target])) == 1
        assert "missing path" in link_errors([source, target])[0]
