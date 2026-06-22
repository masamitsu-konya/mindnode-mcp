"""Locate the MindNode documents directory and enumerate .mindnode packages.

MindNode stores documents in its iCloud container by default:
    ~/Library/Mobile Documents/<TEAM>~com~mindnode~MindNode/Documents/

The team prefix differs per install, so we glob rather than hardcode it.
Override with the MINDNODE_DOCS_DIR environment variable (e.g. for a local,
non-iCloud library or for tests against a fixture directory).
"""

from __future__ import annotations

import os
from pathlib import Path


def _icloud_candidates() -> list[Path]:
    base = Path.home() / "Library" / "Mobile Documents"
    if not base.is_dir():
        return []
    # Glob is scoped to the MindNode container only — never a home-wide walk.
    return sorted(base.glob("*com~mindnode*/Documents"))


def docs_dir() -> Path:
    """Return the MindNode Documents directory.

    Resolution order:
      1. MINDNODE_DOCS_DIR env var (explicit override)
      2. iCloud container (auto-detected)
    Raises FileNotFoundError with an actionable message when nothing is found.
    """
    override = os.environ.get("MINDNODE_DOCS_DIR")
    if override:
        p = Path(override).expanduser()
        if not p.is_dir():
            raise FileNotFoundError(
                f"MINDNODE_DOCS_DIR points to a non-directory: {p}"
            )
        return p

    candidates = _icloud_candidates()
    if not candidates:
        raise FileNotFoundError(
            "Could not find a MindNode documents directory. "
            "Set MINDNODE_DOCS_DIR to your MindNode library path."
        )
    return candidates[0]


def list_documents(root: Path | None = None) -> list[Path]:
    """Return every .mindnode package under the documents directory (recursive).

    The walk is rooted at the MindNode container, so it never touches unrelated
    folders. Sorted by most-recently-modified first.
    """
    base = root or docs_dir()
    docs = [p for p in base.rglob("*.mindnode") if p.is_dir()]
    docs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return docs


def resolve_document(name_or_path: str, root: Path | None = None) -> Path:
    """Resolve a user-supplied document reference to an absolute .mindnode path.

    Accepts an absolute path, a path relative to the documents dir, or a bare
    document name (with or without the .mindnode suffix), matched case-folded.
    """
    base = root or docs_dir()

    p = Path(name_or_path).expanduser()
    if p.is_absolute() and p.exists():
        return p

    rel = base / name_or_path
    if rel.exists():
        return rel
    if (rel_ext := base / f"{name_or_path}.mindnode").exists():
        return rel_ext

    needle = name_or_path.removesuffix(".mindnode").casefold()
    matches = [d for d in list_documents(base) if d.stem.casefold() == needle]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        joined = "\n".join(f"  - {m.relative_to(base)}" for m in matches)
        raise FileNotFoundError(
            f"Ambiguous document name {name_or_path!r}; candidates:\n{joined}"
        )
    raise FileNotFoundError(f"No MindNode document matching {name_or_path!r}")
