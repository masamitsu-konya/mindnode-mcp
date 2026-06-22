"""Smoke test: read against real docs (safe), write against a temp copy.

Run: uv run python tests/smoke.py
"""

import os
import shutil
import tempfile
from pathlib import Path

from mindnode_mcp import document as doc
from mindnode_mcp import paths


def section(title):
    print(f"\n=== {title} ===")


def test_read():
    section("READ (real files, read-only)")
    docs = paths.list_documents()
    print(f"documents found: {len(docs)}")
    assert docs, "no .mindnode documents found"
    for p in docs[:3]:
        print(f"  - {p.stem}")

    # Read the most recent supported doc into a tree.
    for p in docs:
        try:
            data = doc.load_document(p)
        except (ValueError, FileNotFoundError):
            continue
        trees = [doc.to_tree(r) for r in doc.main_nodes(data)]
        node_count = sum(
            1 for r in doc.main_nodes(data) for _ in doc.iter_nodes(r)
        )
        root_text = trees[0]["text"] if trees else "(empty)"
        print(f"read '{p.stem}': {len(trees)} map(s), {node_count} nodes, root={root_text[:40]!r}")
        assert trees, "expected at least one map"
        return p
    raise AssertionError("no supported-version document to read")


def test_write(skeleton_real: Path):
    section("WRITE (temp copy — real files untouched)")
    tmp = Path(tempfile.mkdtemp(prefix="mindnode-mcp-test-"))
    try:
        # Build an isolated docs dir with one copied skeleton document.
        docs_dir = tmp / "Documents"
        docs_dir.mkdir()
        target = docs_dir / "Smoke Source.mindnode"
        shutil.copytree(skeleton_real, target)
        os.environ["MINDNODE_DOCS_DIR"] = str(docs_dir)

        # Reload server module so it picks up the env override.
        from mindnode_mcp import server

        # --- add_node ---
        before = doc.load_document(target)
        before_count = sum(1 for r in doc.main_nodes(before) for _ in doc.iter_nodes(r))
        res = server.add_node("Smoke Source", "★ added by smoke test", note="a note")
        after = doc.load_document(target)
        after_count = sum(1 for r in doc.main_nodes(after) for _ in doc.iter_nodes(r))
        print(f"add_node -> {res}")
        assert after_count == before_count + 1, "node count did not increase by 1"
        added = doc.find_node(after, res["id"])
        assert added is not None, "added node not found by id"
        assert doc.node_text(added) == "★ added by smoke test"
        assert doc.node_note(added) == "a note"
        bak = list(target.glob("contents.xml.bak-*"))
        assert bak, "expected a backup file"
        print(f"  verified: node present, note present, backup created ({bak[0].name})")

        # --- create_map ---
        out = server.create_map(
            "Smoke New Map",
            outline=[
                "Branch A",
                {"text": "Branch B", "children": ["B-1", "B-2"]},
            ],
        )
        print(f"create_map -> {out}")
        new_doc = docs_dir / out["path"]
        assert new_doc.exists(), "new document not created"
        nd = doc.load_document(new_doc)
        tree = doc.to_tree(doc.main_nodes(nd)[0])
        assert tree["text"] == "Smoke New Map"
        kids = [c["text"] for c in tree["children"]]
        assert kids == ["Branch A", "Branch B"], f"unexpected branches: {kids}"
        b = tree["children"][1]
        assert [c["text"] for c in b["children"]] == ["B-1", "B-2"]
        print(f"  verified: title + nested outline round-tripped ({kids} / {[c['text'] for c in b['children']]})")
    finally:
        os.environ.pop("MINDNODE_DOCS_DIR", None)
        shutil.rmtree(tmp, ignore_errors=True)


def test_html():
    section("HTML <-> text round-trip")
    cases = ["hello", "line1\nline2", "a & b < c", "<script>x</script>"]
    for c in cases:
        h = doc.text_to_html(c)
        back = doc.html_to_text(h)
        print(f"  {c!r} -> {h!r} -> {back!r}")
        assert back == c, f"round-trip failed for {c!r}: got {back!r}"


def _find_doc_with_connections() -> Path | None:
    for p in paths.list_documents():
        try:
            data = doc.load_document(p)
        except (ValueError, FileNotFoundError):
            continue
        if doc.cross_connections(data):
            return p
    return None


def test_connections_read():
    section("CONNECTIONS — read (real files)")
    p = _find_doc_with_connections()
    assert p is not None, "no document with connections found"
    data = doc.load_document(p)
    conns = [doc.connection_to_dict(c, data) for c in doc.cross_connections(data)]
    print(f"'{p.stem}': {len(conns)} connection(s)")
    c = conns[0]
    print(f"  e.g. {c['start_text']!r} --[{c['direction']}]--> {c['end_text']!r}")
    assert c["start_id"] and c["end_id"], "connection missing endpoint ids"
    return p


def test_connections_write(skeleton_with_conn: Path):
    section("CONNECTIONS — write + schema parity (temp copy)")
    import plistlib

    tmp = Path(tempfile.mkdtemp(prefix="mindnode-mcp-conn-"))
    try:
        docs_dir = tmp / "Documents"
        docs_dir.mkdir()
        target = docs_dir / "Conn Source.mindnode"
        shutil.copytree(skeleton_with_conn, target)
        os.environ["MINDNODE_DOCS_DIR"] = str(docs_dir)
        from mindnode_mcp import server

        # Grab two real node ids to connect.
        data = doc.load_document(target)
        ids = [
            n.get("nodeID")
            for r in doc.main_nodes(data)
            for n in doc.iter_nodes(r)
        ]
        assert len(ids) >= 2, "need at least two nodes to connect"

        before = len(doc.cross_connections(data))
        res = server.add_connection(
            "Conn Source", ids[0], ids[1], label="深掘り", direction="both"
        )
        print(f"add_connection -> {res}")

        after = doc.load_document(target)
        conns = doc.cross_connections(after)
        assert len(conns) == before + 1, "connection count did not increase by 1"
        new = next(c for c in conns if c["connectionID"] == res["id"])
        assert new["endPoints"]["startNodeID"] == ids[0]
        assert new["endPoints"]["endNodeID"] == ids[1]
        assert doc.connection_label(new) == "深掘り"
        assert doc.connection_direction(new) == "both"

        # Schema parity: new connection must match a real one key-for-key.
        real = doc.cross_connections(data)[0]
        nk, rk = set(new.keys()), set(real.keys())
        assert nk == rk, f"connection keys differ: extra={nk-rk} missing={rk-nk}"
        # And the file must still lint as a valid plist.
        with (target / "contents.xml").open("rb") as f:
            plistlib.load(f)
        print(f"  verified: keys match real ({sorted(nk)}), label+direction round-trip, plist valid")
    finally:
        os.environ.pop("MINDNODE_DOCS_DIR", None)
        shutil.rmtree(tmp, ignore_errors=True)


# 1x1 PNG (valid, so `sips` can read its dimensions)
_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f100000000049454e44ae426082"
)


def _find_doc_with(key_predicate) -> Path | None:
    for p in paths.list_documents():
        try:
            data = doc.load_document(p)
        except (ValueError, FileNotFoundError):
            continue
        for r in doc.main_nodes(data):
            for n in doc.iter_nodes(r):
                if key_predicate(n, data):
                    return p
    return None


def test_features_read():
    section("TAGS / TASK / ATTACHMENT — read (real files)")
    tag_doc = _find_doc_with(lambda n, d: bool(n.get("tags")))
    assert tag_doc, "no document with tagged nodes"
    data = doc.load_document(tag_doc)
    nm = doc.tag_name_map(data)
    tagged = next(
        n for r in doc.main_nodes(data) for n in doc.iter_nodes(r) if n.get("tags")
    )
    print(f"tags  : '{tag_doc.stem}' node tags -> {doc.node_tag_names(tagged, nm)}")

    task_doc = _find_doc_with(lambda n, d: isinstance(n.get("task"), dict))
    data = doc.load_document(task_doc)
    statuses = [
        doc.task_status_str(n)
        for r in doc.main_nodes(data)
        for n in doc.iter_nodes(r)
        if n.get("task")
    ]
    from collections import Counter

    print(f"task  : '{task_doc.stem}' -> {dict(Counter(statuses))}")

    att_doc = _find_doc_with(lambda n, d: isinstance(n.get("attachment"), dict))
    data = doc.load_document(att_doc)
    att = next(
        doc.node_attachment(n)
        for r in doc.main_nodes(data)
        for n in doc.iter_nodes(r)
        if doc.node_attachment(n)
    )
    print(f"attach: '{att_doc.stem}' -> {att}")
    return tag_doc, att_doc


def test_features_write(skeleton: Path, real_att_doc: Path):
    section("TAGS / TASK / ATTACHMENT — write + schema parity (temp copy)")
    import plistlib

    tmp = Path(tempfile.mkdtemp(prefix="mindnode-mcp-feat-"))
    try:
        docs_dir = tmp / "Documents"
        docs_dir.mkdir()
        target = docs_dir / "Feat Source.mindnode"
        shutil.copytree(skeleton, target)
        os.environ["MINDNODE_DOCS_DIR"] = str(docs_dir)
        from mindnode_mcp import server

        data = doc.load_document(target)
        first = doc.main_nodes(data)[0]
        a_id = first["nodeID"]
        b_id = (first.get("subnodes") or [first])[0]["nodeID"]

        # --- tags ---
        r1 = server.add_tag("Feat Source", a_id, "重要", color="{0.1, 0.6, 1.0, 1.0}")
        r2 = server.add_tag("Feat Source", a_id, "重要")  # idempotent
        assert r1["added"] is True and r2["added"] is False
        after = doc.load_document(target)
        ct = doc.canvas_tags(after)
        assert any(t.get("name") == "重要" for t in ct), "tag not defined in canvas.tags"
        assert set(ct[0].keys()) == {"tagID", "name", "color"}, f"tag keys: {ct[0].keys()}"
        node_a = doc.find_node(after, a_id)
        assert "重要" in doc.node_tag_names(node_a, doc.tag_name_map(after))
        print(f"  tags: defined in canvas + on node, idempotent ✓ ({[t['name'] for t in ct]})")

        # --- task ---
        server.set_task("Feat Source", b_id, done=True)
        after = doc.load_document(target)
        nb = doc.find_node(after, b_id)
        assert nb["task"] == {"state": 2, "uuids": {}}, f"task: {nb.get('task')}"
        server.set_task("Feat Source", b_id, done=False)
        after = doc.load_document(target)
        assert doc.find_node(after, b_id)["task"]["state"] == 1
        print("  task: done<->todo toggles state 2<->1 ✓")

        # --- attachment ---
        img = tmp / "pic.png"
        img.write_bytes(_PNG_1x1)
        res = server.attach_image("Feat Source", a_id, str(img))
        after = doc.load_document(target)
        att = doc.find_node(after, a_id)["attachment"]
        real_att = next(
            n["attachment"]
            for r in doc.main_nodes(doc.load_document(real_att_doc))
            for n in doc.iter_nodes(r)
            if isinstance(n.get("attachment"), dict)
        )
        assert set(att.keys()) == set(real_att.keys()), (
            f"attachment keys differ: {set(att.keys())} vs {set(real_att.keys())}"
        )
        assert att["type"] == 2
        assert (target / "resources" / att["fileName"]).is_file(), "image not copied to resources/"
        with (target / "contents.xml").open("rb") as f:
            plistlib.load(f)
        print(f"  attach: keys match real ({sorted(att.keys())}), file copied, plist valid ✓")
    finally:
        os.environ.pop("MINDNODE_DOCS_DIR", None)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    skeleton = test_read()
    test_html()
    test_write(skeleton)
    conn_doc = test_connections_read()
    test_connections_write(conn_doc)
    _tag_doc, att_doc = test_features_read()
    test_features_write(skeleton, att_doc)
    print("\nALL SMOKE TESTS PASSED ✅")
