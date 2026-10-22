"""Path-confinement tests (no real MindNode library needed).

Run: uv run python tests/test_paths_security.py
"""

import tempfile
from pathlib import Path

from mindnode_mcp import paths


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def main():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "docs"
        (base / "a.mindnode").mkdir(parents=True)
        outside = Path(td) / "evil.mindnode"
        outside.mkdir()

        assert paths.resolve_document("a", base) == (base / "a.mindnode").resolve()
        assert _raises(lambda: paths.resolve_document(str(outside), base))
        assert _raises(lambda: paths.resolve_document("../evil.mindnode", base))
        assert _raises(lambda: paths.resolve_subdir("../x", base))
        assert _raises(lambda: paths.resolve_subdir("/tmp", base))
        assert paths.resolve_subdir("sub/dir", base) == (base / "sub" / "dir").resolve()
    print("OK")


if __name__ == "__main__":
    main()
