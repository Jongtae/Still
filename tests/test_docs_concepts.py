"""Static checks for the concept reading path, source IDs and retired links.

These check repository paths and explicit reference anchors, not external
availability, claim accuracy, policy semantics or product behavior. They require
no model, account or network.
"""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CONCEPTS = ROOT / "docs" / "concepts"
RETIRED = {
    "docs/design/utility-v1/prototype.html",
    "docs/design/utility-v1/utility.css",
    "docs/design/utility-v1/utility.js",
}


def _targets(text):
    # The changed documents use inline Markdown links; accept HTML links too.
    yield from re.findall(r"\[[^]\n]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", text)
    yield from re.findall(r"(?:href|src)=[\"']([^\"']+)[\"']", text)


def _local_path(source, target):
    parsed = urlsplit(target.strip("<>"))
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    path = unquote(parsed.path)
    return (ROOT / path.lstrip("/") if path.startswith("/") else source.parent / path).resolve()


def test_concept_chapters_are_reachable_and_local_links_exist():
    entry = CONCEPTS / "README.md"
    root_map = ROOT / "docs" / "README.md"
    assert entry in {_local_path(root_map, target) for target in _targets(root_map.read_text(encoding="utf-8"))}
    chapters = set(CONCEPTS.glob("*.ko.md"))
    assert chapters, "The concept index must lead to actual prose, not an empty directory"
    indexed = {_local_path(entry, target) for target in _targets(entry.read_text(encoding="utf-8"))}
    assert chapters <= indexed, f"Unlisted concept chapters: {chapters - indexed}"
    documents = [root_map, ROOT / "docs/TASKS.md", ROOT / "docs/roadmap.md",
                 ROOT / "docs/design/utility-v1/README.md", *sorted(CONCEPTS.glob("*.md"))]
    missing = []
    for source in documents:
        for target in _targets(source.read_text(encoding="utf-8")):
            path = _local_path(source, target)
            if path is not None and not path.exists():
                missing.append(f"{source.relative_to(ROOT)} -> {target}")
    assert not missing, "Missing local documentation paths:\n" + "\n".join(missing)


def test_reference_ids_are_unique_and_chapter_citations_resolve():
    registry = CONCEPTS / "references.md"
    text = registry.read_text(encoding="utf-8")
    anchors = re.findall(r"<a\s+id=[\"']([^\"']+)[\"']\s*></a>", text)
    assert anchors, "The shared source register needs stable reference anchors"
    assert len(anchors) == len(set(anchors)), "Duplicate reference IDs"
    cited = set()
    for source in sorted(CONCEPTS.glob("*.ko.md")):
        for target in _targets(source.read_text(encoding="utf-8")):
            if _local_path(source, target) != registry.resolve():
                continue
            fragment = unquote(urlsplit(target).fragment)
            assert fragment in anchors, f"Unresolved source citation: {source.name} -> {target}"
            cited.add(fragment)
    assert cited, "The register must be connected to claims in the chapters"
    assert set(anchors) <= cited, f"Uncited source entries: {set(anchors) - cited}"


def test_retired_prototype_has_no_relative_markdown_or_html_links():
    retired = {(ROOT / path).resolve() for path in RETIRED}
    assert all(not path.exists() for path in retired)
    broken = []
    # A pinned GitHub link to the original is valid historical provenance.
    # Scan tracked documentation surfaces, not owner data or build outputs.
    for folder in (ROOT / "docs", ROOT / "site"):
        for source in folder.rglob("*"):
            if not source.is_file() or source.suffix not in {".md", ".html"}:
                continue
            for target in _targets(source.read_text(encoding="utf-8")):
                if _local_path(source, target) in retired:
                    broken.append(f"{source.relative_to(ROOT)} -> {target}")
    assert not broken, "Links still target the retired prototype:\n" + "\n".join(broken)
