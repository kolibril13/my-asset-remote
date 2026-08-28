#!/usr/bin/env python3
"""Sanity-check a generated remote asset library before it is published.

Blender's `asset_listing generate` command is happy to emit a listing that is
structurally valid but useless (no assets found, a thumbnail that never made it
to disk, a hash that no longer matches). Publishing that to GitHub Pages means
users get a library that silently shows nothing, so check it here instead.

Usage: python3 verify_listing.py <asset-library-dir>
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import unquote

META_FILENAME = "_asset-library-meta.json"
LIBRARY_DIRNAME = "assets"
PLACEHOLDERS = {"Your Asset Library", "Your Name", "https://example.org/", "example@example.org"}

errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "SHA256:" + digest.hexdigest()


def check_referenced_file(root: Path, url: str, expected_hash: str | None, label: str) -> Path | None:
    """Resolve a URL from the listing to a file on disk and verify its hash."""
    path = root / unquote(url)
    if not path.is_file():
        fail(f"{label}: referenced file is missing: {url}")
        return None
    if expected_hash is not None:
        actual = sha256(path)
        if actual != expected_hash:
            fail(f"{label}: hash mismatch for {url}\n    listed: {expected_hash}\n    actual: {actual}")
    return path


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    meta_path = root / META_FILENAME
    if not meta_path.is_file():
        print(f"error: {META_FILENAME} was not generated in {root}", file=sys.stderr)
        return 1
    meta = load_json(meta_path)

    # The library name and contact details are what users see in Preferences. In CI
    # these are filled in from the repository, so placeholders here only mean the
    # generator was run without that step — which is exactly what a local test run
    # does. Worth pointing out, not worth failing over.
    placeholder_fields = [
        field
        for field, value in [
            ("name", meta.get("name")),
            *((f"contact.{k}", (meta.get("contact") or {}).get(k)) for k in ("name", "url", "email")),
        ]
        if value in PLACEHOLDERS
    ]
    if placeholder_fields:
        print(
            f"note: {META_FILENAME} still has generator placeholders for "
            f"{', '.join(placeholder_fields)} — the workflow fills these in from the "
            f"repository; set them in {LIBRARY_DIRNAME}/{META_FILENAME} to override"
        )

    api_versions = meta.get("api_versions") or {}
    if "v1" not in api_versions:
        fail(f"{META_FILENAME}: no 'v1' entry in 'api_versions'")
        return report()

    index_ref = api_versions["v1"]
    index_path = check_referenced_file(root, index_ref["url"], index_ref.get("hash"), "asset index")
    if index_path is None:
        return report()
    index = load_json(index_path)

    asset_count = index.get("asset_count", 0)
    if asset_count < 1:
        fail("asset index: library contains no assets — check that the .blend files are marked as assets")
    if not index.get("catalogs"):
        fail("asset index: no catalogs — check that blender_assets.cats.txt is present in the library root")

    # Walk every page and confirm each blend file and thumbnail is really there,
    # unchanged, and reachable at the URL the listing advertises.
    seen_assets = 0
    for page_ref in index.get("pages", []):
        page_path = check_referenced_file(root, page_ref["url"], page_ref.get("hash"), "index page")
        if page_path is None:
            continue
        page = load_json(page_path)

        for file_info in page.get("files", []):
            path = check_referenced_file(root, file_info["path"], file_info.get("hash"), "asset file")
            if path is not None and path.stat().st_size != file_info.get("size_in_bytes"):
                fail(f"asset file: size mismatch for {file_info['path']}")

        for asset in page.get("assets", []):
            seen_assets += 1
            label = f"asset {asset.get('name', '?')!r}"
            if not asset.get("files"):
                fail(f"{label}: not associated with any .blend file")
            thumbnail = asset.get("thumbnail")
            if thumbnail:
                check_referenced_file(root, thumbnail["url"], thumbnail.get("hash"), f"{label} thumbnail")
            else:
                print(f"note: {label} has no preview image")

    if seen_assets != asset_count:
        fail(f"asset index: claims {asset_count} assets but the pages list {seen_assets}")

    if not errors:
        print(f"OK: {meta['name']} — {asset_count} asset(s) across {index.get('file_count', 0)} file(s), "
              f"{index.get('asset_size_bytes', 0) / 1024:.1f} KiB")
    return report()


def report() -> int:
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
