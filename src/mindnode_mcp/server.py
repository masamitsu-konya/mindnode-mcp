"""MCP server exposing MindNode documents to Claude.

Read tools (safe): list_documents, read_document, search_nodes.
Write tools (mutate real files, guarded by backups + atomic writes):
add_node, create_map.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import document as doc
from . import paths

mcp = FastMCP("mindnode")


# --------------------------------------------------------------------------- #
# read tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_documents() -> list[dict[str, Any]]:
    """List all MindNode documents, most recently modified first.

    Returns one entry per .mindnode file with its display name, relative path
    (use this as the `document` argument elsewhere), and modified date.
    """
    base = paths.docs_dir()
    out = []
    for p in paths.list_documents(base):
        st = p.stat()
        out.append(
            {
                "name": p.stem,
                "path": str(p.relative_to(base)),
                "modified": _fmt_mtime(st.st_mtime),
            }
        )
    return out


@mcp.tool()
def read_document(document: str) -> dict[str, Any]:
    """Read a MindNode document and return its mind maps as clean node trees.

    `document` may be a document name, a path relative to the MindNode docs
    directory, or an absolute path. Each map is a recursive tree of
    {id, text, note?, children?}.
    """
    path = paths.resolve_document(document)
    data = doc.load_document(path)
    name_map = doc.tag_name_map(data)
    maps = [doc.to_tree(root, name_map) for root in doc.main_nodes(data)]
    result = {"name": path.stem, "maps": maps}
    connections = [doc.connection_to_dict(c, data) for c in doc.cross_connections(data)]
    if connections:
        result["connections"] = connections
    tags = [t.get("name") for t in doc.canvas_tags(data) if t.get("name")]
    if tags:
        result["tags"] = tags
    return result


@mcp.tool()
def search_nodes(query: str, document: str | None = None) -> list[dict[str, Any]]:
    """Search node text (and notes) across one or all MindNode documents.

    Case-insensitive substring match. If `document` is omitted, every document
    is searched. Returns matching nodes with their document, node id, and text.
    """
    base = paths.docs_dir()
    targets = (
        [paths.resolve_document(document)] if document else paths.list_documents(base)
    )
    needle = query.casefold()
    results: list[dict[str, Any]] = []
    for path in targets:
        try:
            data = doc.load_document(path)
        except (ValueError, FileNotFoundError):
            continue  # skip unsupported-version / malformed docs during a global search
        for root in doc.main_nodes(data):
            for node in doc.iter_nodes(root):
                text = doc.node_text(node)
                note = doc.node_note(node)
                if needle in text.casefold() or needle in note.casefold():
                    hit = {
                        "document": path.stem,
                        "id": node.get("nodeID"),
                        "text": text,
                    }
                    if note:
                        hit["note"] = note
                    results.append(hit)
    return results


# --------------------------------------------------------------------------- #
# write tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def add_node(
    document: str,
    text: str,
    parent_id: str | None = None,
    parent_text: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Add a node to an existing MindNode document.

    Specify the parent by `parent_id` (exact) or `parent_text` (first node whose
    text contains it, case-insensitive). If neither is given, the node is added
    under the root of the first mind map. The original file is backed up before
    writing. Returns the new node's id.
    """
    path = paths.resolve_document(document)
    data = doc.load_document(path)
    roots = doc.main_nodes(data)
    if not roots:
        raise ValueError(f"{path.stem} has no mind map to add to.")

    if parent_id:
        parent = doc.find_node(data, parent_id)
        if parent is None:
            raise ValueError(f"No node with id {parent_id!r} in {path.stem}.")
    elif parent_text:
        parent = _find_by_text(roots, parent_text)
        if parent is None:
            raise ValueError(f"No node containing {parent_text!r} in {path.stem}.")
    else:
        parent = roots[0]

    # Inherit style from an existing sibling, else from the parent itself.
    siblings = parent.get("subnodes") or []
    template = siblings[-1] if siblings else parent
    node = doc.make_node(text, template=template, note=note)
    parent.setdefault("subnodes", []).append(node)
    if parent.get("hasFoldedSubnodes"):
        parent["hasFoldedSubnodes"] = False

    doc.save_document(path, data)
    return {"id": node["nodeID"], "text": text, "parent_id": parent.get("nodeID")}


@mcp.tool()
def add_connection(
    document: str,
    start: str,
    end: str,
    label: str | None = None,
    direction: str = "forward",
) -> dict[str, Any]:
    """Add a cross-connection (a free link line) between two existing nodes.

    Unlike add_node, this links two nodes that already exist anywhere in the
    document, independent of the parent/child tree. `start` and `end` each accept
    a node id (exact) or node text (first case-insensitive substring match).
    `label` is optional text shown on the line. `direction` is one of:
    "forward" (arrow at end, default), "backward", "both", "none".
    The original file is backed up before writing. Returns the new connection id.
    """
    if direction not in doc.CONNECTION_DIRECTIONS:
        raise ValueError(
            f"direction must be one of {list(doc.CONNECTION_DIRECTIONS)}, got {direction!r}."
        )
    path = paths.resolve_document(document)
    data = doc.load_document(path)
    roots = doc.main_nodes(data)

    start_node = _resolve_node(data, roots, start)
    if start_node is None:
        raise ValueError(f"Start node {start!r} not found in {path.stem}.")
    end_node = _resolve_node(data, roots, end)
    if end_node is None:
        raise ValueError(f"End node {end!r} not found in {path.stem}.")
    if start_node.get("nodeID") == end_node.get("nodeID"):
        raise ValueError("Cannot connect a node to itself.")

    existing = doc.cross_connections(data)
    template = existing[0] if existing else None
    conn = doc.make_connection(
        start_node["nodeID"],
        end_node["nodeID"],
        label=label,
        direction=direction,
        template=template,
    )
    canvas = data.setdefault("canvas", {})
    canvas.setdefault("crossConnections", []).append(conn)

    doc.save_document(path, data)
    return {
        "id": conn["connectionID"],
        "start": doc.node_text(start_node),
        "end": doc.node_text(end_node),
        "direction": direction,
        "label": label,
    }


@mcp.tool()
def add_tag(
    document: str,
    node: str,
    tag: str,
    color: str | None = None,
) -> dict[str, Any]:
    """Tag a node. Creates the tag in the document if it doesn't exist yet.

    `node` is a node id or text substring. `tag` is the tag name. `color` is an
    optional "{r, g, b, a}" string (0..1 floats); a default is used otherwise.
    Tags are document-wide: tagging reuses an existing tag of the same name.
    """
    path = paths.resolve_document(document)
    data = doc.load_document(path)
    roots = doc.main_nodes(data)
    target = _resolve_node(data, roots, node)
    if target is None:
        raise ValueError(f"Node {node!r} not found in {path.stem}.")

    tag_id = doc.ensure_tag(data, tag, color=color)
    added = doc.add_tag_to_node(target, tag_id)
    doc.save_document(path, data)
    return {
        "node": doc.node_text(target),
        "tag": tag,
        "added": added,  # False if the node already had this tag
        "tags": doc.node_tag_names(target, doc.tag_name_map(data)),
    }


@mcp.tool()
def remove_tag(document: str, node: str, tag: str) -> dict[str, Any]:
    """Remove a tag from a node (the tag definition stays in the document)."""
    path = paths.resolve_document(document)
    data = doc.load_document(path)
    roots = doc.main_nodes(data)
    target = _resolve_node(data, roots, node)
    if target is None:
        raise ValueError(f"Node {node!r} not found in {path.stem}.")

    name_map = doc.tag_name_map(data)
    tag_id = next((tid for tid, nm in name_map.items() if nm == tag), None)
    removed = doc.remove_tag_from_node(target, tag_id) if tag_id else False
    if removed:
        doc.save_document(path, data)
    return {"node": doc.node_text(target), "tag": tag, "removed": removed}


@mcp.tool()
def set_task(document: str, node: str, done: bool = True) -> dict[str, Any]:
    """Make a node a checkbox task and set its state.

    `node` is a node id or text substring. `done=True` marks it complete,
    `done=False` marks it open/todo. (To remove the checkbox entirely, this MVP
    leaves the task in place; use done=False to uncheck.)
    """
    path = paths.resolve_document(document)
    data = doc.load_document(path)
    roots = doc.main_nodes(data)
    target = _resolve_node(data, roots, node)
    if target is None:
        raise ValueError(f"Node {node!r} not found in {path.stem}.")

    doc.set_node_task(target, done)
    doc.save_document(path, data)
    return {"node": doc.node_text(target), "task": "done" if done else "todo"}


@mcp.tool()
def attach_image(document: str, node: str, image_path: str) -> dict[str, Any]:
    """Attach a local image file to a node.

    Copies the image into the document's resources/ folder and links it on the
    node (`node` is a node id or text substring). Replaces any existing
    attachment on that node. Returns the stored file name and display size.
    """
    path = paths.resolve_document(document)
    data = doc.load_document(path)
    roots = doc.main_nodes(data)
    target = _resolve_node(data, roots, node)
    if target is None:
        raise ValueError(f"Node {node!r} not found in {path.stem}.")

    src = Path(image_path).expanduser()
    if not src.is_file():
        raise ValueError(f"Image not found: {src}")

    ext = src.suffix.lower() or ".png"
    file_name = f"{doc.new_node_id()}{ext}"
    res_dir = path / "resources"
    res_dir.mkdir(exist_ok=True)
    shutil.copy2(src, res_dir / file_name)

    width, height = _image_size(src)
    target["attachment"] = doc.make_image_attachment(file_name, width, height)
    doc.save_document(path, data)
    return {
        "node": doc.node_text(target),
        "fileName": file_name,
        "size": target["attachment"]["size"],
    }


@mcp.tool()
def create_map(
    title: str,
    outline: list[Any] | None = None,
    folder: str | None = None,
    open_after: bool = False,
) -> dict[str, Any]:
    """Create a new .mindnode document from a title and optional outline.

    `outline` is a list of items, each either a string (a top-level branch) or
    an object {"text": str, "children": [...]} for nesting (recursive).
    `folder` is an optional subdirectory under the MindNode docs dir.
    Set `open_after` to open the new document in MindNode. Returns its path.
    """
    base = paths.docs_dir()
    skeleton = _skeleton_document(base)
    target_dir = base / folder if folder else base
    target_dir.mkdir(parents=True, exist_ok=True)
    new_path = _unique_path(target_dir, title)

    # Clone a known-good package, then overwrite only the node tree.
    shutil.copytree(skeleton, new_path)
    data = doc.load_document(new_path)

    style_root = doc.main_nodes(data)[0] if doc.main_nodes(data) else None
    root = doc.make_node(title, template=style_root)
    root["location"] = "{0, 0}"
    for item in outline or []:
        root["subnodes"].append(_build_branch(item, style_root))

    maps = data["canvas"]["mindMaps"]
    maps[0]["mainNode"] = root
    del maps[1:]  # one map per new document

    doc.save_document(new_path, data, backup=False)

    if open_after:
        try:
            subprocess.run(["open", str(new_path)], check=False, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass

    return {"name": new_path.stem, "path": str(new_path.relative_to(base))}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _build_branch(item: Any, style_template: dict | None) -> dict:
    if isinstance(item, str):
        return doc.make_node(item, template=style_template)
    if isinstance(item, dict):
        node = doc.make_node(item.get("text", ""), template=style_template)
        for child in item.get("children") or []:
            node["subnodes"].append(_build_branch(child, style_template))
        return node
    return doc.make_node(str(item), template=style_template)


def _find_by_text(roots: list[dict], needle: str) -> dict | None:
    n = needle.casefold()
    for root in roots:
        for node in doc.iter_nodes(root):
            if n in doc.node_text(node).casefold():
                return node
    return None


def _resolve_node(data: dict, roots: list[dict], ref: str) -> dict | None:
    """Resolve a node reference that may be an exact id or substring of text."""
    by_id = doc.find_node(data, ref)
    if by_id is not None:
        return by_id
    return _find_by_text(roots, ref)


def _image_size(p: Path) -> tuple[float, float]:
    """Pixel dimensions of an image via macOS `sips`; (300, 200) on failure."""
    try:
        out = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(p)],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 300.0, 200.0
    w = h = 0.0
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("pixelWidth:"):
            w = float(line.split(":", 1)[1])
        elif line.startswith("pixelHeight:"):
            h = float(line.split(":", 1)[1])
    return (w or 300.0), (h or 200.0)


def _skeleton_document(base: Path) -> Path:
    docs = paths.list_documents(base)
    if not docs:
        raise FileNotFoundError(
            "create_map needs at least one existing .mindnode to use as a "
            "structural template. Create one in MindNode first."
        )
    # Most recent doc that parses as a supported version.
    for d in docs:
        try:
            doc.load_document(d)
            return d
        except (ValueError, FileNotFoundError):
            continue
    raise ValueError("No supported-version MindNode document found to clone.")


def _unique_path(folder: Path, title: str) -> Path:
    safe = title.replace("/", "-").strip() or "Untitled"
    candidate = folder / f"{safe}.mindnode"
    i = 2
    while candidate.exists():
        candidate = folder / f"{safe} {i}.mindnode"
        i += 1
    return candidate


def _fmt_mtime(ts: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
