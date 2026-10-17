#!/usr/bin/env python3
"""File edits the release workflow makes, kept testable outside Actions (#1472).

`.github/workflows/release.yml` owns the order, the owner approval and every
GitHub call (`gh release`, the tap PR, the record and pin PRs). This script
only reads and rewrites repository files, so each step can be checked on a
temporary copy:

* ``check``    print the in-tree version once it is ready to publish;
* ``notes``    print the GitHub release notes from its manifest entry;
* ``formula``  point the Homebrew tap formula at the published tag archive;
* ``record``   move the entry to ``published``, re-pin ``scripts/install.sh``,
               update the release-status prose and set the next placeholder;
* ``pin``      pin the README installer to the commit that installs it.

Nothing here measures a checksum or claims an installed observation: the
workflow passes in the served archive digest, and ``observed`` stays pending
until the owner records their own upgrade (docs/release.en.md).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from stamp_site_release import PIN  # noqa: E402  the README pin the site stamp reads

MANIFEST = "docs/release-manifest.json"
INSTALLER = "scripts/install.sh"
PYPROJECT = "pyproject.toml"
TEMPLATE = ROOT / "deploy" / "homebrew" / "agentos.rb.template"
READMES = ("README.md", "docs/i18n/README.ko.md", "docs/i18n/README.ja.md", "docs/i18n/README.zh-CN.md")
#: Prose that names the newest published release. Each file must name it, so
#: a reworded sentence fails here instead of silently keeping an old version.
RELEASE_PROSE = READMES + (
    "docs/product-status.en.md",
    "docs/product-status.ko.md",
    ".github/ISSUE_TEMPLATE/feedback.yml",
)
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class ReleaseError(RuntimeError):
    pass


def version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def project_version(root: Path) -> str:
    with open(root / PYPROJECT, "rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def load_manifest(root: Path) -> dict:
    return json.loads((root / MANIFEST).read_text(encoding="utf-8"))


def save_manifest(root: Path, data: dict) -> None:
    (root / MANIFEST).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def newest_published(data: dict) -> dict:
    return max(data["published"], key=lambda row: version_key(row["version"]))


def unpublished_entry(data: dict, version: str) -> dict:
    for row in data["unpublished"]:
        if row["version"] == version:
            return row
    raise ReleaseError(f"{MANIFEST} has no unpublished entry for {version}")


def check(root: Path) -> str:
    """Return the in-tree version if the preparation PR made it publishable."""
    version = project_version(root)
    if not SEMVER.match(version):
        raise ReleaseError(f"{version} is not a stable X.Y.Z version")
    data = load_manifest(root)
    newest = newest_published(data)["version"]
    if version_key(version) <= version_key(newest):
        raise ReleaseError(f"{version} is not newer than the published v{newest}")
    entry = unpublished_entry(data, version)
    # The post-release placeholder carries no boundary; a prepared release
    # must say what it does not claim before it can be published.
    for field in ("carries", "not_claimed"):
        if not str(entry.get(field) or "").strip():
            raise ReleaseError(f"the {version} entry needs a non-empty '{field}'")
    return version


def notes(root: Path, version: str, milestone_url: str) -> str:
    entry = unpublished_entry(load_manifest(root), version)
    lines = [entry["carries"], "", f"**Not claimed:** {entry['not_claimed']}"]
    if milestone_url:
        lines += ["", f"Scope: {milestone_url}"]
    return "\n".join(lines) + "\n"


def _replace_once(text: str, pattern: str, value: str, what: str) -> str:
    new, count = re.subn(pattern, value, text, count=1, flags=re.M)
    if count != 1:
        raise ReleaseError(f"could not find {what}")
    return new


def _formula_body(text: str) -> str:
    start = text.find("class Agentos < Formula")
    if start < 0:
        raise ReleaseError("no `class Agentos < Formula` in the formula")
    return text[start:].strip()


def formula(path: Path, repository: str, version: str, sha256: str, template: Path = TEMPLATE) -> None:
    """Rewrite url and sha256, and refuse a tap formula that drifted from the template.

    `brew test` runs the formula's own test block, so a weakened install body
    and test would still pass on their own; the reviewed template is the
    reference for every other line (docs/release.en.md step 7).
    """
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ReleaseError("sha256 must be 64 lowercase hex characters")
    text = path.read_text(encoding="utf-8")
    text = _replace_once(text, r'^(\s*url ")[^"]+/archive/refs/tags/v[^"]+\.tar\.gz"',
                         rf'\g<1>https://github.com/{repository}/archive/refs/tags/v{version}.tar.gz"',
                         "the formula url")
    text = _replace_once(text, r'^(\s*sha256 ")[0-9a-f]{64}"', rf'\g<1>{sha256}"', "the formula sha256")
    expected = _formula_body(template.read_text(encoding="utf-8")
                             .replace("__VERSION__", version).replace("__SHA256__", sha256))
    if _formula_body(text) != expected:
        raise ReleaseError(f"the tap formula differs from {template.name} beyond url and sha256; "
                           "re-sync them by hand (docs/release.en.md step 7)")
    path.write_text(text, encoding="utf-8")


def _set_installer_pin(text: str, name: str, value: str) -> str:
    return _replace_once(text, rf'^{name}="[^"]*"$', f'{name}="{value}"', f"{name} in {INSTALLER}")


def record(root: Path, version: str, commit: str, sha256: str, tag_date: str, tap_pr: str) -> str:
    """Record a published release and return the next placeholder version."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ReleaseError("commit must be a full 40-character SHA")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ReleaseError("sha256 must be 64 lowercase hex characters")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tag_date):
        raise ReleaseError("tag date must be YYYY-MM-DD")
    data = load_manifest(root)
    previous = newest_published(data)
    entry = unpublished_entry(data, version)
    data["unpublished"].remove(entry)
    data["published"].append({
        "version": version,
        "tag": f"v{version}",
        "commit": commit,
        "archive_sha256": sha256,
        "state": "published",
        "carries": entry["carries"],
        "tag_date": tag_date,
        "observed": (f"Published by the release workflow after owner approval. The served tag archive "
                     f"SHA-256 was measured twice and matched; Homebrew tap PR {tap_pr} merged after its "
                     "macOS formula install and brew test passed. The owner's own installed upgrade is "
                     "not yet recorded."),
        "not_claimed": entry["not_claimed"],
    })
    major, minor, patch = version_key(version)
    following = f"{major}.{minor}.{patch + 1}"
    data["unpublished"].append({
        "version": following,
        "tag": None,
        "commit": None,
        "why_no_commit": "The future tagged main merge commit does not exist; no earlier source SHA can stand in for it.",
        "archive_sha256": None,
        "state": "unpublished",
        "why_no_checksum": f"No v{following} GitHub tag archive exists; its served SHA-256 must be measured only after publication.",
        "carries": (f"Next development-source version after v{version}. This placeholder does not choose the "
                    "next release's version or claim a new product capability; the release-preparation PR does."),
    })
    save_manifest(root, data)

    installer = root / INSTALLER
    text = _set_installer_pin(installer.read_text(encoding="utf-8"), "AGENTOS_VERSION", version)
    installer.write_text(_set_installer_pin(text, "AGENTOS_ARCHIVE_SHA256", sha256), encoding="utf-8")

    pyproject = root / PYPROJECT
    pyproject.write_text(_replace_once(pyproject.read_text(encoding="utf-8"), r'^version = "[^"]+"$',
                                       f'version = "{following}"', f"the version in {PYPROJECT}"),
                         encoding="utf-8")

    old_name, new_name = f"v{previous['version']}", f"v{version}"
    for name in RELEASE_PROSE:
        path = root / name
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        hits = 0
        for index, line in enumerate(lines):
            if re.search(rf"{re.escape(old_name)}(?![\d.])", line):
                hits += 1
                line = re.sub(rf"{re.escape(old_name)}(?![\d.])", new_name, line)
                lines[index] = line.replace(previous.get("tag_date") or "\0", tag_date)
        if not hits:
            raise ReleaseError(f"{name} no longer names {old_name}; update its release-status sentence by hand")
        path.write_text("".join(lines), encoding="utf-8")
    return following


def pin(root: Path, commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ReleaseError("commit must be a full 40-character SHA")
    for name in READMES:
        path = root / name
        text = path.read_text(encoding="utf-8")
        new, count = PIN.subn(lambda m: m.group(1) + commit + m.group(3), text)
        if not count:
            raise ReleaseError(f"{name} has no pinned installer URL")
        path.write_text(new, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    p = sub.add_parser("notes")
    p.add_argument("--version", required=True)
    p.add_argument("--milestone-url", default="")
    p = sub.add_parser("formula")
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--repository", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--sha256", required=True)
    p = sub.add_parser("record")
    p.add_argument("--version", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--sha256", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--tap-pr", required=True)
    p = sub.add_parser("pin")
    p.add_argument("--commit", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            print(check(args.root))
        elif args.command == "notes":
            sys.stdout.write(notes(args.root, args.version, args.milestone_url))
        elif args.command == "formula":
            formula(args.file, args.repository, args.version, args.sha256)
        elif args.command == "record":
            print(record(args.root, args.version, args.commit, args.sha256, args.date, args.tap_pr))
        else:
            pin(args.root, args.commit)
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
